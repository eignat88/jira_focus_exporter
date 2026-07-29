"""Refactored ParallelLoaderV2 with reliable heartbeat and per-chunk commit."""
from __future__ import annotations

import hashlib
import io
import json
import queue
import re
import threading
import time
import uuid
from dataclasses import dataclass
from queue import Queue
from typing import Any, Callable, List, Optional, Sequence

import psycopg2
import psycopg2.extras

try:
    from ax_to_postgres_etl.domain import LoadResult, LoadStatus
    from ax_to_postgres_etl.core.run_manager import RunManager
    from ax_to_postgres_etl.core.strategies import get_strategy
except ImportError:
    from domain import LoadResult, LoadStatus
    from core.run_manager import RunManager
    from core.strategies import get_strategy

SENTINEL = object()
_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def ident(value: str) -> str:
    if not _SAFE_IDENT.fullmatch(value):
        raise ValueError(f"Unsafe SQL identifier: {value!r}")
    return f'"{value}"'


def copy_text(value: Any) -> str:
    if value is None:
        return r"\N"
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, bool):
        value = "t" if value else "f"
    return (str(value).replace("\\", "\\\\")
                      .replace("\t", "\\t")
                      .replace("\r", "\\r")
                      .replace("\n", "\\n"))


@dataclass(frozen=True)
class ChunkStarted:
    chunk_id: int
    chunk_no: int


@dataclass(frozen=True)
class DataBatch:
    chunk_id: int
    chunk_no: int
    rows: Sequence[Sequence[Any]]
    last_key: int


@dataclass(frozen=True)
class ChunkFinished:
    chunk_id: int
    chunk_no: int
    rows_read: int
    last_key: Optional[int]


@dataclass(frozen=True)
class ChunkFailed:
    chunk_id: int
    chunk_no: int


class ParallelLoaderV2:
    def __init__(self, ss_conn_str: str, pg, config: dict,
                 log_func: Optional[Callable[[str], None]] = None,
                 use_new_resume: bool = True):
        self.ss_conn_str = ss_conn_str
        self.pg = pg
        self.config = config
        self.log_func = log_func
        self.use_new_resume = use_new_resume

        parallel = config.get("etl", {}).get("parallel", {})
        retry = config.get("retry", {})
        heartbeat = config.get("heartbeat", {})
        self.workers = int(parallel.get("workers", 4))
        self.fetch_size = int(parallel.get("fetch_size", 5000))
        self.queue_size = int(parallel.get("queue_size", 32))
        self.max_attempts = int(retry.get("max_attempts", 5))
        self.retry_initial = float(retry.get("initial_delay_seconds", 5))
        self.retry_max = float(retry.get("max_delay_seconds", 300))
        self.retry_multiplier = float(retry.get("backoff_multiplier", 2))
        self.heartbeat_interval = int(heartbeat.get("interval_seconds", 30))
        self.heartbeat_timeout = int(heartbeat.get("timeout_seconds", 600))
        self.supervisor_interval = int(heartbeat.get("supervisor_interval_seconds", 60))

        self.pipeline_name = "ax_to_postgres_etl"
        self.session_id = uuid.uuid4().hex[:12]
        self.stop_event = threading.Event()
        self.write_queue: Queue = Queue(maxsize=self.queue_size)
        self.lock = threading.Lock()
        self.total_fetched = 0
        self.writer_error = None
        self.worker_error = None

    def log(self, message: str) -> None:
        if self.log_func:
            self.log_func(message)

    def pg_connect(self):
        conn = psycopg2.connect(
            host=self.pg.conn.info.host,
            port=self.pg.conn.info.port,
            dbname=self.pg.conn.info.dbname,
            user=self.pg.conn.info.user,
            password=self.pg.conn.info.password,
        )
        conn.autocommit = False
        return conn

    def ss_connect(self):
        import pyodbc
        return pyodbc.connect(self.ss_conn_str, timeout=30)

    def load_table(self, table_name: str, columns: Optional[List[str]] = None,
                   load_mode: str = "full") -> LoadResult:
        started = time.monotonic()
        self.stop_event.clear()
        self.write_queue = Queue(maxsize=self.queue_size)
        self.writer_error = None
        self.worker_error = None
        self.total_fetched = 0

        table_cfg = self.config.get("tables", {}).get(table_name, {})
        strategy_name = table_cfg.get("chunk_strategy", "numeric_range")
        chunk_column = table_cfg.get("chunk_column", "RECID")
        chunk_count = int(table_cfg.get("chunk_count", 100))
        source_schema = table_cfg.get("source_schema", "dbo")

        meta = self.pg_connect()
        run_manager = RunManager(meta)
        if not run_manager.acquire_advisory_lock(table_name):
            meta.close()
            raise RuntimeError(f"ETL load for {table_name} is already running")

        try:
            run = None
            if load_mode == "resume":
                run = run_manager.find_resumable_run(table_name, table_name, table_cfg)
                if run:
                    self.log(f"  Found resumable run: {run.run_id}")

            if run is None:
                run = run_manager.create_run(
                    pipeline_name=self.pipeline_name,
                    source_system="SQL Server",
                    source_database=self.config.get("source", {}).get("database"),
                    source_schema=source_schema,
                    source_table=table_name,
                    target_schema=self.pg.schema,
                    target_table=table_name,
                    load_mode=load_mode,
                    chunk_strategy=strategy_name,
                    chunk_column=chunk_column,
                    config=table_cfg,
                )
                self.log(f"  Created new run: {run.run_id}")

            run_manager.update_run_status(run.run_id, "running")
            strategy = get_strategy(strategy_name)
            ss = self.ss_connect()
            try:
                boundaries = strategy.get_boundaries(
                    ss, source_schema, table_name, chunk_column
                )
            finally:
                ss.close()

            ss_columns = self.resolve_columns(table_name, columns, chunk_column)
            pg_columns = [c.lower() for c in ss_columns]
            chunk_index = next(i for i, c in enumerate(ss_columns)
                               if c.upper() == chunk_column.upper())
            total_chunks = self.ensure_chunks(
                meta, run_manager, run.run_id, strategy, boundaries,
                chunk_count, strategy_name, chunk_column
            )

            self.start_threads(table_name, source_schema, ss_columns, pg_columns,
                               chunk_column, chunk_index, run.run_id, total_chunks)

            run_manager.update_run_stats(run.run_id)
            stats = self.total_stats(meta, run.run_id)
            ok = (self.writer_error is None and self.worker_error is None
                  and stats["completed"] == stats["total_chunks"]
                  and sum(stats[k] for k in ("pending", "running", "ready_to_commit",
                                               "writing", "retry", "failed")) == 0)
            run_manager.update_run_status(
                run.run_id,
                "completed" if ok else "completed_with_errors",
                None if ok else self.error_summary(),
            )
            return LoadResult(
                table_name=table_name,
                status=LoadStatus.SUCCESS if ok else LoadStatus.FAILED,
                error_message=None if ok else self.error_summary(),
                rows_fetched=self.total_fetched,
                rows_inserted=stats["rows_inserted"],
                rows_conflicted=stats["rows_conflicted"],
                chunks_total=stats["total_chunks"],
                chunks_completed=stats["completed"],
                elapsed_seconds=time.monotonic() - started,
            )
        finally:
            try:
                run_manager.release_advisory_lock(table_name)
            finally:
                meta.close()

    def start_threads(self, table_name, source_schema, ss_columns, pg_columns,
                      chunk_column, chunk_index, run_id, total_chunks):
        writer = threading.Thread(
            target=self.writer,
            args=(table_name, pg_columns, run_id, total_chunks),
            daemon=True,
        )
        supervisor = threading.Thread(
            target=self.supervisor, args=(run_id,), daemon=True
        )
        writer.start()
        supervisor.start()

        workers = []
        for idx in range(self.workers):
            thread = threading.Thread(
                target=self.fetch_worker,
                args=(idx, table_name, source_schema, ss_columns,
                      chunk_column, chunk_index, run_id),
                daemon=True,
            )
            thread.start()
            workers.append(thread)

        for thread in workers:
            thread.join()
        self.queue_put(SENTINEL)
        writer.join()
        self.stop_event.set()
        supervisor.join(timeout=self.supervisor_interval + 5)

        if self.writer_error:
            raise RuntimeError(f"Writer failed: {self.writer_error}") from self.writer_error
        if self.worker_error:
            raise RuntimeError(f"Worker failed: {self.worker_error}") from self.worker_error

    def fetch_worker(self, index, table_name, source_schema, columns,
                     chunk_column, chunk_index, run_id):
        worker_id = f"{self.session_id}:worker_{index}"
        meta = self.pg_connect()
        ss = None
        try:
            ss = self.ss_connect()
            cursor = ss.cursor()
            cursor.arraysize = self.fetch_size
            while not self.stop_event.is_set():
                chunk = self.claim_chunk(meta, run_id, worker_id)
                if not chunk:
                    return
                chunk_id = int(chunk["chunk_id"])
                chunk_no = int(chunk["chunk_no"])
                start = int(chunk["range_start_bigint"])
                end = int(chunk["range_end_bigint"])
                self.queue_put(ChunkStarted(chunk_id, chunk_no))

                hb_stop = threading.Event()
                hb = threading.Thread(
                    target=self.heartbeat_loop,
                    args=(chunk_id, worker_id, hb_stop),
                    daemon=True,
                )
                hb.start()
                rows_read = 0
                last_key = None
                try:
                    next_after = start - 1
                    failures = 0
                    while next_after < end - 1 and not self.stop_event.is_set():
                        sql = (
                            f"SELECT TOP ({self.fetch_size}) "
                            + ", ".join(ident(c) for c in columns)
                            + f" FROM {ident(source_schema)}.{ident(table_name)} "
                            + f"WHERE {ident(chunk_column)} > ? "
                            + f"AND {ident(chunk_column)} < ? "
                            + f"ORDER BY {ident(chunk_column)}"
                        )
                        try:
                            cursor.execute(sql, next_after, end)
                            batch = cursor.fetchmany(self.fetch_size)
                            failures = 0
                        except Exception as exc:
                            failures += 1
                            if failures >= self.max_attempts or not self.retriable(exc):
                                raise
                            delay = min(self.retry_initial * self.retry_multiplier ** (failures - 1),
                                        self.retry_max)
                            try:
                                ss.close()
                            except Exception:
                                pass
                            if self.stop_event.wait(delay):
                                raise RuntimeError("ETL stop requested")
                            ss = self.ss_connect()
                            cursor = ss.cursor()
                            cursor.arraysize = self.fetch_size
                            continue
                        if not batch:
                            break
                        last_key = int(batch[-1][chunk_index])
                        next_after = last_key
                        rows_read += len(batch)
                        with self.lock:
                            self.total_fetched += len(batch)
                        self.update_progress(meta, chunk_id, worker_id,
                                             rows_read, last_key)
                        self.queue_put(DataBatch(chunk_id, chunk_no, batch, last_key))

                    self.mark_ready(meta, chunk_id, worker_id, rows_read, last_key)
                    self.queue_put(ChunkFinished(chunk_id, chunk_no, rows_read, last_key))
                except Exception as exc:
                    self.mark_retry(meta, chunk_id, worker_id, exc, rows_read, last_key)
                    self.queue_put(ChunkFailed(chunk_id, chunk_no))
                    if not self.retriable(exc):
                        raise
                finally:
                    hb_stop.set()
                    hb.join(timeout=5)
        except Exception as exc:
            self.worker_error = exc
            self.stop_event.set()
            self.log(f"  Worker {worker_id} ERROR: {type(exc).__name__}: {exc}")
        finally:
            try:
                if ss:
                    ss.close()
            except Exception:
                pass
            meta.close()

    def writer(self, table_name, pg_columns, run_id, total_chunks):
        conn = self.pg_connect()
        staging = f"_staging_{table_name.lower()}"
        try:
            self.prepare_staging(conn, staging, pg_columns)
            while True:
                item = self.write_queue.get()
                try:
                    if item is SENTINEL:
                        return
                    if isinstance(item, ChunkStarted):
                        self.clear_staging(conn, staging, item.chunk_id)
                    elif isinstance(item, DataBatch):
                        self.stage_batch(conn, staging, pg_columns, item)
                    elif isinstance(item, ChunkFinished):
                        inserted, conflicts, completed = self.finalize_chunk(
                            conn, staging, table_name, pg_columns, item, run_id
                        )
                        self.log(
                            f"  Writer: chunk {item.chunk_no} committed\n"
                            f"    rows_read={item.rows_read:,}\n"
                            f"    inserted={inserted:,}\n"
                            f"    conflicts={conflicts:,}\n"
                            f"    progress={completed}/{total_chunks}"
                        )
                    elif isinstance(item, ChunkFailed):
                        self.clear_staging(conn, staging, item.chunk_id)
                finally:
                    self.write_queue.task_done()
        except Exception as exc:
            conn.rollback()
            self.writer_error = exc
            self.stop_event.set()
            self.log(f"  Writer ERROR: {type(exc).__name__}: {exc}")
        finally:
            conn.close()

    def prepare_staging(self, conn, staging, columns):
        defs = ", ".join(f"{ident(c)} text" for c in columns)
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE UNLOGGED TABLE IF NOT EXISTS "
                f"{ident(self.pg.schema)}.{ident(staging)} "
                f"({ident('_etl_chunk_id')} bigint NOT NULL, {defs})"
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS {ident(staging + '_chunk_idx')} "
                f"ON {ident(self.pg.schema)}.{ident(staging)} "
                f"({ident('_etl_chunk_id')})"
            )
        conn.commit()

    def clear_staging(self, conn, staging, chunk_id):
        with conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM {ident(self.pg.schema)}.{ident(staging)} "
                f"WHERE {ident('_etl_chunk_id')}=%s", (chunk_id,)
            )
        conn.commit()

    def stage_batch(self, conn, staging, columns, item):
        lines = []
        for row in item.rows:
            if len(row) != len(columns):
                raise RuntimeError(f"Chunk {item.chunk_id}: invalid column count")
            lines.append("\t".join([str(item.chunk_id)] + [copy_text(v) for v in row]))
        if not lines:
            return
        content = "\n".join(lines) + "\n"
        copy_cols = [ident("_etl_chunk_id")] + [ident(c) for c in columns]
        sql = (
            f"COPY {ident(self.pg.schema)}.{ident(staging)} "
            f"({', '.join(copy_cols)}) FROM STDIN "
            "WITH (FORMAT text, DELIMITER E'\\t', NULL E'\\\\N')"
        )
        with conn.cursor() as cur:
            cur.copy_expert(sql, io.StringIO(content))
        conn.commit()

    def finalize_chunk(self, conn, staging, table_name, columns, item, run_id):
        cols = ", ".join(ident(c) for c in columns)
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE etl.load_chunk SET status='writing', worker_id=%s, "
                "heartbeat_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP "
                "WHERE chunk_id=%s AND status='ready_to_commit'",
                (f"{self.session_id}:writer", item.chunk_id),
            )
            if cur.rowcount != 1:
                raise RuntimeError(
                    f"Chunk {item.chunk_id} cannot transition ready_to_commit -> writing"
                )
            cur.execute(
                f"INSERT INTO {ident(self.pg.schema)}.{ident(table_name)} ({cols}) "
                f"SELECT {cols} FROM {ident(self.pg.schema)}.{ident(staging)} "
                f"WHERE {ident('_etl_chunk_id')}=%s "
                f"ON CONFLICT ({ident('recid')}) DO NOTHING",
                (item.chunk_id,),
            )
            inserted = max(int(cur.rowcount), 0)
            conflicts = max(item.rows_read - inserted, 0)
            cur.execute(
                "UPDATE etl.load_chunk SET status='completed', "
                "completed_at=CURRENT_TIMESTAMP, rows_read=%s, rows_staged=%s, "
                "rows_inserted=%s, rows_conflicted=%s, last_processed_key=%s, "
                "worker_id=NULL, error_type=NULL, error_message=NULL, "
                "heartbeat_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP "
                "WHERE chunk_id=%s AND status='writing'",
                (item.rows_read, item.rows_read, inserted, conflicts,
                 None if item.last_key is None else str(item.last_key), item.chunk_id),
            )
            if cur.rowcount != 1:
                raise RuntimeError(
                    f"Chunk {item.chunk_id} cannot transition writing -> completed"
                )
            cur.execute(
                f"DELETE FROM {ident(self.pg.schema)}.{ident(staging)} "
                f"WHERE {ident('_etl_chunk_id')}=%s", (item.chunk_id,)
            )
            cur.execute(
                "SELECT COUNT(*) FROM etl.load_chunk "
                "WHERE run_id=%s AND status='completed'", (run_id,)
            )
            completed = int(cur.fetchone()[0])
        conn.commit()
        return inserted, conflicts, completed

    def claim_chunk(self, conn, run_id, worker_id):
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "WITH x AS (SELECT chunk_id FROM etl.load_chunk "
                "WHERE run_id=%s AND status IN ('retry','pending') "
                "AND attempt_count < %s "
                "ORDER BY CASE status WHEN 'retry' THEN 0 ELSE 1 END, chunk_no "
                "FOR UPDATE SKIP LOCKED LIMIT 1) "
                "UPDATE etl.load_chunk c SET status='running', worker_id=%s, "
                "attempt_count=c.attempt_count+1, started_at=CURRENT_TIMESTAMP, "
                "heartbeat_at=CURRENT_TIMESTAMP, rows_read=0, "
                "last_processed_key=NULL, error_type=NULL, error_message=NULL, "
                "updated_at=CURRENT_TIMESTAMP FROM x WHERE c.chunk_id=x.chunk_id "
                "RETURNING c.*",
                (run_id, self.max_attempts, worker_id),
            )
            row = cur.fetchone()
        conn.commit()
        return row

    def heartbeat_loop(self, chunk_id, worker_id, local_stop):
        conn = self.pg_connect()
        try:
            while not local_stop.wait(self.heartbeat_interval):
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE etl.load_chunk SET heartbeat_at=CURRENT_TIMESTAMP, "
                        "updated_at=CURRENT_TIMESTAMP WHERE chunk_id=%s "
                        "AND status='running' AND worker_id=%s",
                        (chunk_id, worker_id),
                    )
                conn.commit()
        finally:
            conn.close()

    def update_progress(self, conn, chunk_id, worker_id, rows_read, last_key):
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE etl.load_chunk SET rows_read=%s, last_processed_key=%s, "
                "heartbeat_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP "
                "WHERE chunk_id=%s AND status='running' AND worker_id=%s",
                (rows_read, str(last_key), chunk_id, worker_id),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"Chunk {chunk_id} lease lost")
        conn.commit()

    def mark_ready(self, conn, chunk_id, worker_id, rows_read, last_key):
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE etl.load_chunk SET status='ready_to_commit', "
                "rows_read=%s, last_processed_key=%s, worker_id=NULL, "
                "heartbeat_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP "
                "WHERE chunk_id=%s AND status='running' AND worker_id=%s",
                (rows_read, None if last_key is None else str(last_key),
                 chunk_id, worker_id),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"Chunk {chunk_id} cannot become ready_to_commit")
        conn.commit()

    def mark_retry(self, conn, chunk_id, worker_id, exc, rows_read, last_key):
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE etl.load_chunk SET status=CASE WHEN attempt_count >= %s "
                "THEN 'failed' ELSE 'retry' END, worker_id=NULL, rows_read=%s, "
                "last_processed_key=%s, error_type=%s, error_message=%s, "
                "updated_at=CURRENT_TIMESTAMP WHERE chunk_id=%s "
                "AND status='running' AND worker_id=%s",
                (self.max_attempts, rows_read,
                 None if last_key is None else str(last_key),
                 type(exc).__name__, str(exc)[:2000], chunk_id, worker_id),
            )
        conn.commit()

    def supervisor(self, run_id):
        conn = self.pg_connect()
        try:
            while not self.stop_event.wait(self.supervisor_interval):
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE etl.load_chunk SET status=CASE WHEN attempt_count >= %s "
                        "THEN 'failed' ELSE 'retry' END, worker_id=NULL, "
                        "error_type='heartbeat_timeout', error_message='Heartbeat expired', "
                        "updated_at=CURRENT_TIMESTAMP WHERE run_id=%s "
                        "AND status='running' AND heartbeat_at < "
                        "CURRENT_TIMESTAMP - (%s * INTERVAL '1 second')",
                        (self.max_attempts, run_id, self.heartbeat_timeout),
                    )
                    recovered = cur.rowcount
                conn.commit()
                if recovered:
                    self.log(f"  SUPERVISOR: recovered {recovered} stale chunks")
        finally:
            conn.close()

    def ensure_chunks(self, conn, run_manager, run_id, strategy, boundaries,
                      chunk_count, strategy_name, chunk_column):
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM etl.load_chunk WHERE run_id=%s", (run_id,))
            existing = int(cur.fetchone()[0])
        if existing:
            return existing
        ranges = strategy.build_ranges(boundaries, chunk_count)
        with conn.cursor() as cur:
            for no, (start, end) in enumerate(ranges):
                cur.execute(
                    "INSERT INTO etl.load_chunk "
                    "(run_id,chunk_no,chunk_strategy,chunk_column,"
                    "range_start_bigint,range_end_bigint,status) "
                    "VALUES (%s,%s,%s,%s,%s,%s,'pending')",
                    (run_id, no, strategy_name, chunk_column, int(start), int(end)),
                )
        conn.commit()
        run_manager.update_run_counts(run_id, len(ranges))
        return len(ranges)

    def resolve_columns(self, table_name, columns, chunk_column):
        if columns:
            result = list(columns)
            if not any(c.upper() == chunk_column.upper() for c in result):
                result.append(chunk_column)
            return result
        conn = self.ss_connect()
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT TOP 1 * FROM {ident(table_name)}")
            return [d[0] for d in cur.description]
        finally:
            conn.close()

    def total_stats(self, conn, run_id):
        stats = {k: 0 for k in (
            "pending", "running", "ready_to_commit", "writing",
            "completed", "retry", "failed", "cancelled"
        )}
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status,COUNT(*) FROM etl.load_chunk "
                "WHERE run_id=%s GROUP BY status", (run_id,)
            )
            for status, count in cur.fetchall():
                stats[str(status)] = int(count)
            cur.execute(
                "SELECT COUNT(*),COALESCE(SUM(rows_inserted),0),"
                "COALESCE(SUM(rows_conflicted),0) FROM etl.load_chunk WHERE run_id=%s",
                (run_id,),
            )
            total, inserted, conflicts = cur.fetchone()
        stats["total_chunks"] = int(total)
        stats["rows_inserted"] = int(inserted)
        stats["rows_conflicted"] = int(conflicts)
        return stats

    def queue_put(self, item):
        while not self.stop_event.is_set():
            try:
                self.write_queue.put(item, timeout=1)
                return
            except queue.Full:
                continue
        if item is not SENTINEL:
            raise RuntimeError("Writer stopped while queueing data")

    @staticmethod
    def retriable(exc):
        text = str(exc).lower()
        return any(x in text for x in (
            "10054", "10053", "10060", "08s01", "connectionread",
            "communication link failure", "dbnetlib", "timeout",
            "timed out", "общая ошибка сети"
        ))

    def error_summary(self):
        values = [str(e) for e in (self.writer_error, self.worker_error) if e]
        return "; ".join(values) or "Not all chunks completed"

    @staticmethod
    def compute_config_hash(config):
        raw = json.dumps(config, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
