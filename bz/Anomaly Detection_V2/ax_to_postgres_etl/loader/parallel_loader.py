"""
Parallel loader for SQL Server → PostgreSQL ETL.

Each fetch thread creates its OWN SQL Server connection and reads
a distinct RECID range. A single writer thread writes to PostgreSQL
via COPY.

Bottleneck: SQL Server fetch (95-130s per 100K rows)
Goal: N workers → N concurrent fetches → 1 writer → PostgreSQL COPY

Usage:
    from loader.parallel_loader import ParallelLoader
    loader = ParallelLoader(ss_conn_str, pg_connector, config)
    loader.load_table(table_name, columns=None)
"""

import time
import io
import csv
import queue
import threading
from queue import Queue
from typing import Any, Callable, Optional, List, Tuple
from dataclasses import dataclass

from ax_to_postgres_etl.domain import LoadStatus, LoadResult


# Sentinel object for writer shutdown signaling
SENTINEL = object()


def sanitize_copy_value(value):
    """Normalize a value for PostgreSQL COPY text format.

    Guarantees:
    - None → None (PostgreSQL NULL)
    - str → valid UTF-8 str
    - bytes/bytearray → decoded to str
    - int/float/bool → str representation
    """
    if value is None:
        return None

    if isinstance(value, str):
        # Verify round-trip is safe
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            value = value.encode("utf-8", errors="replace").decode("utf-8")
        return value

    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")

    if isinstance(value, bytearray):
        return bytes(value).decode("utf-8", errors="replace")

    if isinstance(value, memoryview):
        return value.tobytes().decode("utf-8", errors="replace")

    if isinstance(value, bool):
        return "t" if value else "f"

    return str(value)


def _validate_row_encoding(row, columns=None):
    """Validate that all values in a row can be safely encoded to UTF-8.

    Returns (is_valid, error_message).
    """
    for idx, val in enumerate(row):
        if val is None:
            continue
        try:
            if isinstance(val, bytes):
                val.decode("utf-8")
            else:
                str(val).encode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError) as e:
            col_name = columns[idx] if columns and idx < len(columns) else f"col_{idx}"
            return False, (
                f"column={col_name}, "
                f"value={repr(val)[:100]}, "
                f"type={type(val).__name__}, "
                f"error={e}"
            )
    return True, None


def _build_copy_buffer(rows: list, col_count: int, log_func=None, columns=None) -> Tuple[str, int]:
    """Build tab-delimited COPY buffer from rows using csv.writer.

    Returns (content_string, skipped_row_count).
    """
    output = io.StringIO(newline="")
    writer = csv.writer(
        output,
        delimiter="\t",
        quotechar='"',
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\n",
    )

    skipped = 0
    for i, row in enumerate(rows):
        try:
            # Validate column count
            if len(row) != col_count:
                raise ValueError(f"Column mismatch: expected={col_count}, actual={len(row)}")
            # Validate encoding before writing
            is_valid, err_msg = _validate_row_encoding(row, columns)
            if not is_valid:
                skipped += 1
                if log_func:
                    log_func(f"  WARNING: Row {i} encoding error: {err_msg}")
                continue
            # Sanitize and write
            clean_row = [sanitize_copy_value(v) for v in row]
            writer.writerow(clean_row)
        except Exception as e:
            skipped += 1
            if log_func:
                log_func(f"  WARNING: Row {i} skipped: {e}")

    content = output.getvalue()
    output.close()
    return content, skipped


class ParallelLoader:
    """Multi-threaded loader: N fetch workers → 1 writer → PostgreSQL COPY."""

    def __init__(
        self,
        ss_conn_str: str,
        pg_connector: Any,
        workers: int = 4,
        fetch_size: int = 5000,
        commit_size: int = 50000,
        log_func: Optional[Callable] = None,
        run_id: int = None,
    ):
        self.ss_conn_str = ss_conn_str
        self.pg = pg_connector
        self.pg_conn_str = pg_connector.conn_str
        self.pg_schema = pg_connector.schema
        self.workers = workers
        self.fetch_size = fetch_size
        self.commit_size = commit_size
        self.log_func = log_func
        self._run_id = run_id

        self.write_queue = Queue(maxsize=workers * 4)
        self.total_fetched = 0
        self.total_inserted = 0
        self.total_conflicted = 0
        self.total_rejected = 0
        self.total_committed = 0
        self.total_errors = 0
        self._counter_lock = threading.Lock()
        self.running = True
        self.worker_error = None
        self.writer_error = None
        self.stop_event = threading.Event()

    def _get_recid_range(self, table_name: str) -> Tuple[int, int]:
        """Get MIN and MAX RECID from SQL Server."""
        import pyodbc
        conn = pyodbc.connect(self.ss_conn_str)
        cursor = conn.cursor()
        cursor.execute(f"SELECT ISNULL(MIN(RECID),0), ISNULL(MAX(RECID),0) FROM {table_name}")
        row = cursor.fetchone()
        min_recid = int(row[0]) if row[0] is not None else 0
        max_recid = int(row[1]) if row[1] is not None else 0
        cursor.close()
        conn.close()
        return min_recid, max_recid

    def _get_columns(self, table_name: str) -> Tuple[List[str], List[str]]:
        """Get column list from SQL Server schema."""
        import pyodbc
        conn = pyodbc.connect(self.ss_conn_str)
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME = '{table_name}' ORDER BY ORDINAL_POSITION
        """)
        all_cols = [row[0] for row in cursor.fetchall()]
        cursor.close()
        conn.close()

        # PG columns = all except RECID
        pg_cols = [c for c in all_cols if c.upper() != "RECID"]
        # SS query columns = pg_cols + RECID (RECID must be last for pagination)
        ss_cols = pg_cols + ["RECID"]
        return ss_cols, pg_cols

    def _check_table_empty(self, table_name: str) -> bool:
        """Check if PG table has existing data."""
        try:
            cursor = self.pg.conn.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM {self.pg.schema}.{table_name}")
            count = cursor.fetchone()[0]
            return count == 0
        except Exception:
            return True  # Table may not exist yet

    def _get_source_count(self, table_name: str) -> int:
        """Get row count from SQL Server source."""
        import pyodbc
        try:
            conn = pyodbc.connect(self.ss_conn_str)
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT_BIG(*) FROM {table_name}")
            count = cursor.fetchone()[0]
            cursor.close()
            conn.close()
            return count
        except Exception:
            return 0

    def _get_target_count(self, table_name: str) -> int:
        """Get row count from PostgreSQL target."""
        try:
            cursor = self.pg.conn.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM {self.pg.schema}.{table_name}")
            count = cursor.fetchone()[0]
            cursor.close()
            return count
        except Exception:
            return 0

    def _fetch_worker(
        self,
        worker_id: int,
        table_name: str,
        col_list: str,
        range_start: int,
        range_end: int,
    ):
        """Fetch a RECID range using its own SQL Server connection."""
        import pyodbc

        conn = None
        try:
            conn = pyodbc.connect(self.ss_conn_str)
            cursor = conn.cursor()
            cursor.arraysize = 5000

            last_recid = range_start
            rows_fetched = 0

            while self.running and last_recid < range_end:
                batch_end = min(last_recid + self.fetch_size * 20, range_end)
                sql = f"""
                    SELECT {col_list} FROM {table_name}
                    WHERE RECID > {last_recid} AND RECID <= {batch_end}
                    ORDER BY RECID
                """
                cursor.execute(sql)
                batch = cursor.fetchmany(self.fetch_size)

                if not batch:
                    # Try a wider range if no data found
                    if batch_end < range_end:
                        last_recid = batch_end
                        continue
                    break

                # CRITICAL: cast RECID to int (pyodbc may return string)
                last_recid = int(batch[-1][-1])
                rows_fetched += len(batch)

                with self._counter_lock:
                    self.total_fetched += len(batch)

                self.write_queue.put(batch)

            cursor.close()

            if self.log_func:
                self.log_func(f"  Worker {worker_id}: done, fetched {rows_fetched:,} rows")

        except Exception as e:
            if self.log_func:
                self.log_func(f"  Worker {worker_id} ERROR: {e}")
            self.total_errors += 1
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def _fetch_worker_from_queue(
        self,
        worker_id: int,
        table_name: str,
        col_list: str,
        chunk_queue: Queue,
        recid_index: int,
        completed_chunks: set,
    ):
        """Fetch chunks from shared queue. Each worker takes next available chunk."""
        import pyodbc

        if self.log_func:
            self.log_func(f"  Worker {worker_id}: STARTED (RECID index={recid_index})")

        conn = None
        rows_fetched = 0
        try:
            if self.log_func:
                self.log_func(f"  Worker {worker_id}: Opening SQL Server connection...")
            conn = pyodbc.connect(self.ss_conn_str, timeout=30)
            if self.log_func:
                self.log_func(f"  Worker {worker_id}: SQL Server connected")
            cursor = conn.cursor()
            cursor.arraysize = 5000

            while self.running and not self.stop_event.is_set():
                try:
                    chunk_id, range_start, range_end = chunk_queue.get(timeout=2)
                except Exception:
                    if chunk_queue.empty():
                        if self.log_func:
                            self.log_func(f"  Worker {worker_id}: queue empty, finishing")
                        break
                    continue

                if self.log_func:
                    self.log_func(f"  Worker {worker_id}: chunk {chunk_id} RECID {range_start:,}→{range_end:,}")

                # Skip completed chunks (resume mode)
                if (range_start, range_end) in completed_chunks:
                    if self.log_func:
                        self.log_func(f"  Worker {worker_id}: chunk {chunk_id} already DONE, skipping")
                    chunk_queue.task_done()
                    continue

                # Log chunk start to etl_chunk_run
                try:
                    chunk_db_id = self.pg.start_chunk(self._run_id, table_name, chunk_id, range_start, range_end)
                except Exception:
                    chunk_db_id = None

                last_recid = range_start
                chunk_rows_fetched = 0  # Per-chunk counter
                while self.running and not self.stop_event.is_set() and last_recid < range_end:
                    # Check stop_event before each SELECT
                    if self.stop_event.is_set():
                        if self.log_func:
                            self.log_func(f"  Worker {worker_id}: stop requested, exiting")
                        break

                    sql = f"""
                        SELECT TOP ({self.fetch_size}) {col_list} FROM {table_name}
                        WHERE RECID > {last_recid} AND RECID <= {range_end}
                        ORDER BY RECID
                    """
                    if self.log_func:
                        self.log_func(f"  Worker {worker_id}: SELECT columns={len(col_list.split(','))}, RECID range {last_recid:,}→{range_end:,}")
                    cursor.execute(sql)

                    # Check stop_event after execute
                    if self.stop_event.is_set():
                        if self.log_func:
                            self.log_func(f"  Worker {worker_id}: stop requested after execute")
                        break

                    batch = cursor.fetchmany(self.fetch_size)

                    if not batch:
                        break

                    # Use real RECID index, not batch[-1][-1]
                    new_last_recid = int(batch[-1][recid_index])

                    # Validate pagination progress
                    if new_last_recid <= last_recid:
                        if self.log_func:
                            self.log_func(f"  Worker {worker_id}: WARNING pagination not advancing: old={last_recid}, new={new_last_recid}")
                        break

                    last_recid = new_last_recid
                    chunk_rows_fetched += len(batch)
                    rows_fetched += len(batch)

                    with self._counter_lock:
                        self.total_fetched += len(batch)

                    if self.log_func:
                        self.log_func(f"  Worker {worker_id}: fetched {len(batch)} rows, last_recid={last_recid:,}")

                    # Put batch with chunk metadata for writer to track
                    batch_with_meta = {
                        'rows': batch,
                        'chunk_id': chunk_id,
                        'chunk_db_id': chunk_db_id,
                        'range_start': range_start,
                        'range_end': range_end,
                        'last_recid': last_recid,
                        'is_last_batch': False,
                    }

                    # Put with backpressure — queue.Full is normal, not an error
                    queued = False
                    while not queued and not self.stop_event.is_set():
                        try:
                            self.write_queue.put(batch_with_meta, timeout=1)
                            queued = True
                        except queue.Full:
                            time.sleep(0.5)  # backpressure: wait for writer

                    # Check stop_event after put
                    if self.stop_event.is_set():
                        if self.log_func:
                            self.log_func(f"  Worker {worker_id}: stop requested after put")
                        break

                # Send completion signal for this chunk (DO NOT mark as DONE yet)
                # Writer will mark as DONE after successful commit
                chunk_queue.task_done()

            cursor.close()

        except Exception as e:
            import traceback
            if self.log_func:
                self.log_func(f"  Worker {worker_id} ERROR: {type(e).__name__}: {e}")
                self.log_func(f"  Worker {worker_id} TRACEBACK:\n{traceback.format_exc()}")
            self.worker_error = e
            self.running = False
            self.total_errors += 1
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
            if self.log_func:
                self.log_func(f"  Worker {worker_id}: done, fetched {rows_fetched:,} rows")

    def _write_worker(self, table_name: str, pg_col_names: List[str]):
        """Single writer thread: reads batches from queue, writes to PostgreSQL via COPY.

        Creates its own PostgreSQL connection for thread safety.
        Uses SENTINEL for clean shutdown.
        """
        import psycopg2

        if self.log_func:
            self.log_func(f"  Writer: STARTED (columns={len(pg_col_names)})")

        # Create own PostgreSQL connection for thread safety
        pg_conn = None
        try:
            pg_conn = psycopg2.connect(self.pg_conn_str)
            pg_conn.autocommit = False
            if self.log_func:
                self.log_func(f"  Writer: PostgreSQL connection opened")
        except Exception as e:
            if self.log_func:
                self.log_func(f"  Writer: FAILED to connect to PostgreSQL: {e}")
            self.writer_error = e
            self.stop_event.set()
            return

        batch_count = 0
        buffer_content = ""
        rows_in_buffer = 0
        write_times = []
        staging_table = f"{self.pg_schema}._staging_{table_name}"

        # Track chunks for DONE confirmation
        chunk_rows_received = {}  # chunk_db_id -> rows received
        chunk_read_complete = {}  # chunk_db_id -> True when worker finished reading

        try:
            # Create staging table
            cursor = pg_conn.cursor()
            cursor.execute(f"DROP TABLE IF EXISTS {staging_table}")
            col_defs = ", ".join([f"{c} text" for c in pg_col_names])
            cursor.execute(f"CREATE UNLOGGED TABLE {staging_table} ({col_defs})")
            pg_conn.commit()
            if self.log_func:
                self.log_func(f"  Writer: staging table {staging_table} created")

            while True:
                item = self.write_queue.get()

                # Sentinel = shutdown signal from main thread
                if item is SENTINEL:
                    if self.log_func:
                        self.log_func(f"  Writer: received sentinel, finishing...")
                    break

                # Process batch - handle both old format (list) and new format (dict)
                if isinstance(item, dict):
                    rows = item['rows']
                    chunk_db_id = item.get('chunk_db_id')
                    # Track chunk reception
                    if chunk_db_id:
                        chunk_rows_received[chunk_db_id] = chunk_rows_received.get(chunk_db_id, 0) + len(rows)
                else:
                    rows = item
                    chunk_db_id = None

                content, skipped = _build_copy_buffer(rows, len(pg_col_names), log_func=self.log_func, columns=pg_col_names)
                buffer_content += content
                rows_in_buffer += len(rows) - skipped

                # Commit when commit_size reached
                if rows_in_buffer >= self.commit_size:
                    t_start = time.time()
                    try:
                        col_names = ", ".join(pg_col_names)
                        cursor = pg_conn.cursor()

                        # Step 1: TRUNCATE staging
                        cursor.execute(f"TRUNCATE {staging_table}")

                        # Step 2: COPY to staging
                        copy_sql = (
                            f"COPY {staging_table} ({col_names}) "
                            f"FROM STDIN WITH ("
                            f"FORMAT CSV, DELIMITER E'\\t', "
                            f"QUOTE E'\"', ESCAPE E'\"', "
                            f"NULL E''"
                            f")"
                        )
                        buffer = io.StringIO(buffer_content)
                        cursor.copy_expert(copy_sql, buffer)

                        # Step 3: UPSERT from staging to target
                        upsert_sql = (
                            f"INSERT INTO {self.pg_schema}.{table_name} ({col_names}) "
                            f"SELECT {col_names} FROM {staging_table} "
                            f"ON CONFLICT (recid) DO NOTHING"
                        )
                        cursor.execute(upsert_sql)
                        inserted = cursor.rowcount
                        conflicts = rows_in_buffer - inserted

                        pg_conn.commit()
                        write_times.append(time.time() - t_start)

                        # Update counters
                        with self._counter_lock:
                            self.total_inserted += inserted
                            self.total_conflicted += conflicts
                            self.total_committed += rows_in_buffer

                        if self.log_func:
                            self.log_func(f"  Writer: batch {batch_count + 1}, committed {rows_in_buffer:,} rows (inserted={inserted:,}, conflicts={conflicts:,})")
                        batch_count += 1
                        buffer_content = ""
                        rows_in_buffer = 0
                    except Exception as e:
                        pg_conn.rollback()
                        self.writer_error = e
                        self.stop_event.set()
                        if self.log_func:
                            self.log_func(f"  Writer: COPY FAILED: {type(e).__name__}: {e}")
                        raise

                self.write_queue.task_done()

            # Flush remaining buffer after sentinel
            if buffer_content:
                try:
                    col_names = ", ".join(pg_col_names)
                    cursor = pg_conn.cursor()
                    cursor.execute(f"TRUNCATE {staging_table}")
                    copy_sql = (
                        f"COPY {staging_table} ({col_names}) "
                        f"FROM STDIN WITH ("
                        f"FORMAT CSV, DELIMITER E'\\t', "
                        f"QUOTE E'\"', ESCAPE E'\"', "
                        f"NULL E''"
                        f")"
                    )
                    buffer = io.StringIO(buffer_content)
                    cursor.copy_expert(copy_sql, buffer)
                    upsert_sql = (
                        f"INSERT INTO {self.pg_schema}.{table_name} ({col_names}) "
                        f"SELECT {col_names} FROM {staging_table} "
                        f"ON CONFLICT (recid) DO NOTHING"
                    )
                    cursor.execute(upsert_sql)
                    inserted = cursor.rowcount
                    conflicts = rows_in_buffer - inserted
                    pg_conn.commit()

                    # Update counters
                    with self._counter_lock:
                        self.total_inserted += inserted
                        self.total_conflicted += conflicts
                        self.total_committed += rows_in_buffer

                    if self.log_func:
                        self.log_func(f"  Writer: flushed {rows_in_buffer:,} remaining rows (inserted={inserted:,}, conflicts={conflicts:,})")
                except Exception as e:
                    pg_conn.rollback()
                    self.writer_error = e
                    if self.log_func:
                        self.log_func(f"  Writer: FLUSH FAILED: {type(e).__name__}: {e}")

        finally:
            # Cleanup staging table
            try:
                pg_conn.cursor().execute(f"DROP TABLE IF EXISTS {staging_table}")
                pg_conn.commit()
            except Exception:
                pass
            if pg_conn and not pg_conn.closed:
                pg_conn.close()
            if self.log_func:
                self.log_func(f"  Writer: PostgreSQL connection closed")

        # Log writer performance
        if write_times and self.log_func:
            avg_write = sum(write_times) / len(write_times)
            max_write = max(write_times)
            self.log_func(f"  WRITER STATS: batches={len(write_times)} avg={avg_write:.2f}s max={max_write:.2f}s")

    def _write_to_pg(self, table_name: str, pg_col_names: List[str], content: str, batch_num: int):
        """Write buffer to PostgreSQL via direct COPY."""
        try:
            # UTF-8 encoding (preserve Cyrillic and all Unicode)
            content = content.encode("utf-8").decode("utf-8")

            # Diagnostic: check column count vs first row field count
            if batch_num == 0:
                first_line = content.split("\n")[0] if content else ""
                field_count = len(first_line.split("\t")) if first_line else 0
                if self.log_func:
                    self.log_func(f"  DIAG: COPY columns={len(pg_col_names)}, first_row_fields={field_count}")
                    if field_count != len(pg_col_names):
                        self.log_func(f"  WARNING: Column count mismatch! COPY={len(pg_col_names)} vs data={field_count}")

            # Direct COPY to target
            self.pg.copy_to_staging(table_name, pg_col_names, io.StringIO(content))
            self.pg.conn.commit()

            lines = content.count("\n")
            with self._counter_lock:
                self.total_written += lines

            if self.log_func:
                self.log_func(f"  Writer: batch {batch_num + 1}, COPY {lines:,} rows, total={self.total_written:,}")

        except Exception as e:
            self.pg.conn.rollback()
            self.worker_error = e
            self.running = False
            if self.log_func:
                self.log_func(f"  Writer batch {batch_num + 1} FAILED: {type(e).__name__}: {e}")
            raise

    def load_table(self, table_name: str, columns: Optional[List[str]] = None, load_mode: str = "full"):
        """Load table using parallel RECID-range fetching.

        Load modes:
          - full:       загрузка с начала (TRUNCATE если есть данные)
          - resume:     продолжение незавершённых чанков
          - reload:     очистка и полная перезагрузка
        """
        if self.log_func:
            self.log_func(
                f"START PARALLEL LOAD: {table_name} (mode={load_mode}, "
                f"workers={self.workers}, fetch_size={self.fetch_size}, commit_size={self.commit_size})"
            )

        start_time = time.time()
        self.total_fetched = 0
        self.total_written = 0
        self.total_errors = 0
        self.running = True

        # Handle load_mode
        if load_mode == "reload":
            try:
                self.pg.execute(f"TRUNCATE TABLE {self.pg.schema}.{table_name}")
                self.pg.conn.commit()
                if self.log_func:
                    self.log_func(f"  RELOAD: Table truncated")
            except Exception as e:
                if self.log_func:
                    self.log_func(f"  RELOAD: Truncate failed ({e}), continuing...")

        if load_mode == "full" and not self._check_table_empty(table_name):
            # Full mode with existing data — truncate first
            try:
                self.pg.execute(f"TRUNCATE TABLE {self.pg.schema}.{table_name}")
                self.pg.conn.commit()
                if self.log_func:
                    self.log_func(f"  FULL: Table had data, truncated for fresh load")
            except Exception as e:
                if self.log_func:
                    self.log_func(f"  FULL: Truncate failed ({e}), continuing with append...")

        # Get RECID range
        min_recid, max_recid = self._get_recid_range(table_name)
        if self.log_func:
            self.log_func(f"  RECID range: {min_recid:,} → {max_recid:,}")

        # Get columns — always include RECID for pagination
        if columns:
            selected = list(columns)
            if not any(c.upper() == "RECID" for c in selected):
                selected.append("RECID")
            ss_col_names = selected
            pg_col_names = [c.lower() for c in selected]
            col_list = ", ".join(ss_col_names)
        else:
            ss_col_names, pg_col_names = self._get_columns(table_name)
            col_list = ", ".join(ss_col_names)

        # Find RECID index in column list
        recid_index = None
        for i, col in enumerate(ss_col_names):
            if col.upper() == "RECID":
                recid_index = i
                break
        if recid_index is None:
            raise RuntimeError(f"{table_name}: RECID not found in column list")

        if self.log_func:
            self.log_func(f"  Columns: {len(ss_col_names)}, RECID index: {recid_index}")

        # Dynamic chunk partitioning: 100-500 small chunks
        total_range = max_recid - min_recid
        num_chunks = min(500, max(100, total_range // 100000))  # 100-500 chunks
        chunk_size = total_range // num_chunks + 1

        ranges = []
        for i in range(num_chunks):
            r_start = min_recid + i * chunk_size
            r_end = min(min_recid + (i + 1) * chunk_size, max_recid + 1)
            if r_start < max_recid:
                ranges.append((r_start, r_end))

        # Fix first chunk to include min_recid (WHERE RECID > r_start needs r_start = min_recid - 1)
        if ranges:
            ranges[0] = (min_recid - 1, ranges[0][1])

        if self.log_func:
            self.log_func(f"  Dynamic chunks: {len(ranges)} chunks, ~{chunk_size:,} RECID range each")
            # Show first 3 and last chunk
            for i, (rs, re) in enumerate(ranges[:3]):
                self.log_func(f"  Chunk {i}: RECID {rs:,} → {re:,}")
            if len(ranges) > 3:
                self.log_func(f"  ... ({len(ranges) - 3} more chunks)")
                rs, re = ranges[-1]
                self.log_func(f"  Chunk {len(ranges)-1}: RECID {rs:,} → {re:,}")

        # Resume: check which chunks are already completed via etl_chunk_run
        completed_chunks = set()
        if load_mode == "resume":
            try:
                cursor = self.pg.conn.cursor()
                cursor.execute(
                    f"SELECT chunk_id, range_from, range_to FROM {self.pg.schema}.etl_chunk_run "
                    f"WHERE table_name = %s AND status = 'DONE'",
                    (table_name,)
                )
                for row in cursor.fetchall():
                    completed_chunks.add((row[1], row[2]))  # (range_from, range_to)
                cursor.close()
            except Exception:
                pass

        # Log resume status
        total_chunks = len(ranges)
        completed_count = len(completed_chunks)
        pending_count = total_chunks - completed_count

        if self.log_func:
            self.log_func(f"  Resume plan:")
            self.log_func(f"    Total chunks: {total_chunks}")
            self.log_func(f"    Completed: {completed_count}")
            self.log_func(f"    Pending: {pending_count}")

        # Get source and target counts for verification
        source_count = self._get_source_count(table_name)
        target_count = self._get_target_count(table_name)

        if self.log_func:
            self.log_func(f"  Source rows: {source_count:,}")
            self.log_func(f"  Target rows: {target_count:,}")

        # Check for inconsistency: all chunks marked DONE but counts don't match
        all_chunks_complete = total_chunks > 0 and completed_count == total_chunks
        counts_match = source_count == target_count

        if all_chunks_complete and not counts_match:
            missing = source_count - target_count
            if self.log_func:
                self.log_func(f"  RESUME INCONSISTENCY: all chunks marked DONE, "
                             f"but source_count={source_count:,}, "
                             f"target_count={target_count:,}, "
                             f"missing={missing:,}")
            self.log_func(f"  Continuing with full load to fix data...")

        # EARLY EXIT: Only if all chunks done AND counts match
        if load_mode == "resume" and all_chunks_complete and counts_match:
            elapsed = time.time() - start_time
            if self.log_func:
                self.log_func(f"  ALREADY_COMPLETE {table_name}: all {total_chunks} chunks done, "
                             f"source={source_count:,}, target={target_count:,}")
                self.log_func(f"  No workers, writer or staging table started")

            return LoadResult(
                table_name=table_name,
                status=LoadStatus.ALREADY_COMPLETE,
                rows_fetched=0,
                rows_inserted=0,
                rows_updated=0,
                rows_conflicted=0,
                rows_rejected=0,
                chunks_total=total_chunks,
                chunks_completed=completed_count,
                target_count=target_count,
                elapsed_seconds=elapsed,
            )

        # Create chunk queue with ONLY pending chunks
        chunk_queue = Queue()
        queued_count = 0
        for i, (r_start, r_end) in enumerate(ranges):
            if (r_start, r_end) not in completed_chunks:
                chunk_queue.put((i, r_start, r_end))
                queued_count += 1

        if self.log_func:
            self.log_func(f"  Queued pending chunks: {queued_count}")

        if self.log_func:
            self.log_func(f"  Starting {self.workers} fetch workers + 1 writer")

        # Start writer thread
        writer = threading.Thread(
            target=self._write_worker,
            args=(table_name, pg_col_names),
            daemon=True,
        )
        writer.start()

        # Start N fetch workers (each takes chunks from queue)
        fetch_threads = []
        for worker_id in range(self.workers):
            t = threading.Thread(
                target=self._fetch_worker_from_queue,
                args=(worker_id, table_name, col_list, chunk_queue, recid_index, completed_chunks),
                daemon=True,
            )
            t.start()
            fetch_threads.append(t)

        # Wait for all fetch workers
        if self.log_func:
            self.log_func(f"  Waiting for fetch workers to finish...")
        for t in fetch_threads:
            t.join()
        if self.log_func:
            self.log_func(f"  All fetch workers finished. Signaling writer...")

        # Signal writer to finish via sentinel
        self.write_queue.put(SENTINEL)
        writer.join()
        if self.log_func:
            if self.writer_error is not None:
                self.log_func(f"  Writer terminated with error.")
            else:
                self.log_func(f"  Writer completed successfully.")

        # Propagate writer error to main thread
        if self.writer_error is not None:
            raise RuntimeError(f"Parallel writer failed: {self.writer_error}") from self.writer_error

        # Propagate errors to main thread
        if self.worker_error is not None:
            raise RuntimeError(f"Parallel load failed: {self.worker_error}") from self.worker_error

        if self.total_errors > 0:
            raise RuntimeError(f"Parallel load failed: {self.total_errors} worker errors")

        # Check if load is complete (all chunks DONE) - this is success, not error
        all_chunks_done = len(completed_chunks) >= len(ranges) if ranges else False

        if self.total_inserted == 0 and max_recid > min_recid and not all_chunks_done:
            raise RuntimeError(f"Parallel load failed: 0 rows inserted but source has data")

        elapsed = time.time() - start_time
        speed = self.total_inserted / elapsed if elapsed > 0 else 0

        if self.log_func:
            if all_chunks_done:
                self.log_func(
                    f"DONE {table_name}: all {len(ranges)} chunks complete, "
                    f"0 new rows needed"
                )
            else:
                self.log_func(
                    f"DONE {table_name}: inserted={self.total_inserted:,}, "
                    f"conflicts={self.total_conflicted:,}, "
                    f"{elapsed:.1f}s, {speed:,.0f} rows/sec, errors={self.total_errors}"
                )

        # Get final target count
        target_count = 0
        try:
            cursor = self.pg.conn.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM {self.pg.schema}.{table_name}")
            target_count = cursor.fetchone()[0]
            cursor.close()
        except Exception:
            pass

        return LoadResult(
            table_name=table_name,
            status=LoadStatus.SUCCESS if self.total_errors == 0 else LoadStatus.FAILED,
            rows_fetched=self.total_fetched,
            rows_inserted=self.total_inserted,
            rows_updated=0,
            rows_conflicted=self.total_conflicted,
            rows_rejected=self.total_rejected,
            chunks_total=len(ranges),
            chunks_completed=len(completed_chunks) + (queued_count if 'queued_count' in locals() else 0),
            target_count=target_count,
            elapsed_seconds=elapsed,
        )
