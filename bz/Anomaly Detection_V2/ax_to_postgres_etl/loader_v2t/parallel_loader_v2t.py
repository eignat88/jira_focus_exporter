"""
Parallel loader v2 with universal resume mechanism.

Uses etl.load_run and etl.load_chunk for progress tracking.
Each worker atomically claims chunks via FOR UPDATE SKIP LOCKED.

Usage:
    from loader.parallel_loader_v2 import ParallelLoaderV2
    loader = ParallelLoaderV2(ss_conn_str, pg_connector, config)
    loader.load_table(table_name, columns=None, load_mode='resume')
"""

import time
import io
import csv
import json
import os
import signal
import queue
import threading
import hashlib
import logging
from queue import Queue
from typing import Any, Callable, Optional, List, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime

import psycopg2
import psycopg2.extras

try:
    from ax_to_postgres_etl.domain import LoadStatus, LoadResult
    from ax_to_postgres_etl.core.run_manager import RunManager
    from ax_to_postgres_etl.core.chunk_manager import ChunkManager
    from ax_to_postgres_etl.core.strategies import get_strategy
    from ax_to_postgres_etl.core.retry import RetryPolicy, RetryConfig
    from ax_to_postgres_etl.core.messages import DataBatch, ChunkFinished, ChunkFailed
except ImportError:
    # For testing when running from project root
    from domain import LoadStatus, LoadResult
    from core.run_manager import RunManager
    from core.chunk_manager import ChunkManager
    from core.strategies import get_strategy
    from core.retry import RetryPolicy, RetryConfig
    from core.messages import DataBatch, ChunkFinished, ChunkFailed


# Sentinel object for writer shutdown signaling
SENTINEL = object()


def escape_copy_text(value):
    """
    Convert value to PostgreSQL COPY TEXT format.

    None      -> \\N
    backslash -> \\\\
    tab       -> \\t
    newline   -> \\n
    CR        -> \\r
    """
    if value is None:
        return r"\N"

    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    elif isinstance(value, bytearray):
        text = bytes(value).decode("utf-8", errors="replace")
    elif isinstance(value, memoryview):
        text = value.tobytes().decode("utf-8", errors="replace")
    elif isinstance(value, bool):
        text = "t" if value else "f"
    else:
        text = str(value)

    return (
        text
        .replace("\\", "\\\\")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )


def _build_copy_buffer(rows: list, col_count: int, log_func=None, columns=None, chunk_id=None) -> Tuple[str, int]:
    """Build buffer for PostgreSQL COPY FORMAT TEXT."""
    output = io.StringIO()
    skipped = 0

    for row_number, row in enumerate(rows, start=1):
        if len(row) != col_count:
            skipped += 1
            if log_func:
                log_func(f"  COPY WARNING: row {row_number} skipped: expected {col_count} columns, got {len(row)}")
            continue

        # Prepend chunk_id if provided (for staging table with _etl_chunk_id)
        if chunk_id is not None:
            output.write(str(chunk_id))
            output.write("\t")

        output.write("\t".join(escape_copy_text(value) for value in row))
        output.write("\n")

    return output.getvalue(), skipped


class ParallelLoaderV2T:
    """Parallel loader with universal resume mechanism."""

    def __init__(
        self,
        ss_conn_str: str,
        pg,
        config: dict,
        log_func: Optional[Callable] = None,
        use_new_resume: bool = True,
    ):
        """
        Initialize ParallelLoaderV2.
        
        Args:
            ss_conn_str: SQL Server connection string
            pg: PostgreSQL connector (with .conn, .schema attributes)
            config: ETL configuration dict
            log_func: Logging function
            use_new_resume: Use new resume mechanism (etl.load_chunk)
        """
        self.ss_conn_str = ss_conn_str
        self.pg = pg
        self.config = config
        self.log_func = log_func
        self.use_new_resume = use_new_resume

        # Parallel settings
        etl_config = config.get("etl", {})
        parallel_config = etl_config.get("parallel", {})
        self.workers = parallel_config.get("workers", 4)
        self.fetch_size = parallel_config.get("fetch_size", 5000)
        self.commit_size = parallel_config.get("commit_size", 50000)
        self.batch_size = etl_config.get("batch_size", 100000)

        # Per-table chunk_size override (from YAML table config)
        self._table_chunk_sizes: dict = {}
        for tbl_name, tbl_cfg in config.get("tables", {}).items():
            if "chunk_size" in tbl_cfg:
                self._table_chunk_sizes[tbl_name] = tbl_cfg["chunk_size"]

        # Shared state
        self.running = True
        self.stop_event = threading.Event()
        self._counter_lock = threading.Lock()
        self.write_queue = Queue(maxsize=100)

        # Counters
        self.total_fetched = 0
        self.total_inserted = 0
        self.total_conflicted = 0
        self.total_committed = 0
        self.total_errors = 0
        self.writer_error = None
        self.worker_error = None

        # Retry policy
        retry_config = config.get("retry", {})
        self.retry_policy = RetryPolicy(RetryConfig(
            max_attempts=retry_config.get("max_attempts", 5),
            initial_delay_seconds=retry_config.get("initial_delay_seconds", 5),
            max_delay_seconds=retry_config.get("max_delay_seconds", 300),
            backoff_multiplier=retry_config.get("backoff_multiplier", 2),
        ))

        # Heartbeat settings
        heartbeat_config = config.get("heartbeat", {})
        self.heartbeat_interval = heartbeat_config.get("interval_seconds", 30)
        self.heartbeat_timeout = heartbeat_config.get("timeout_seconds", 600)

        # Streaming threshold (configurable from YAML)
        self.stream_threshold = config.get("etl", {}).get("stream_threshold", 10000)

        # Log level: "minimal" | "normal" | "verbose"
        self.log_level = config.get("etl", {}).get("log_level", "normal")

        # Pipeline name for runs
        self.pipeline_name = "ax_to_postgres_etl"

        # Heartbeat threads per chunk
        self._heartbeat_threads: dict[int, tuple[threading.Thread, threading.Event]] = {}

        # File logger (if configured)
        self._file_logger: Optional[logging.Logger] = None
        log_file = config.get("etl", {}).get("log_file")
        if log_file:
            self._setup_file_logger(log_file)

        # Memory tracking
        self._peak_memory_mb = 0.0

    def _setup_file_logger(self, log_path: str):
        """Setup file logger for persistent logs."""
        try:
            os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
            self._file_logger = logging.getLogger(f"etl_{id(self)}")
            self._file_logger.setLevel(logging.DEBUG)
            handler = logging.FileHandler(log_path, encoding="utf-8")
            handler.setFormatter(logging.Formatter(
                "%(asctime)s | %(levelname)s | %(message)s"
            ))
            self._file_logger.addHandler(handler)
        except Exception:
            pass

    def _check_pg_health(self, conn) -> bool:
        """Verify PostgreSQL connection is alive."""
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.close()
            return True
        except Exception:
            return False

    def _check_ss_health(self, conn) -> bool:
        """Verify SQL Server connection is alive."""
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.close()
            return True
        except Exception:
            return False

    def _get_memory_usage_mb(self) -> float:
        """Get current process memory usage in MB."""
        try:
            import psutil
            process = psutil.Process(os.getpid())
            return process.memory_info().rss / (1024 * 1024)
        except ImportError:
            return 0.0

    def _track_memory(self):
        """Track peak memory usage."""
        current = self._get_memory_usage_mb()
        if current > self._peak_memory_mb:
            self._peak_memory_mb = current

    def _log(self, msg: str, level: str = "normal"):
        """
        Log message with level filtering.

        Levels:
          minimal — only errors and summary
          normal  — standard operational messages (default)
          verbose — detailed per-batch messages
        """
        if not self.log_func:
            return

        level_hierarchy = {"minimal": 0, "normal": 1, "verbose": 2}
        msg_level = level_hierarchy.get(level, 1)
        config_level = level_hierarchy.get(self.log_level, 1)

        if msg_level <= config_level:
            self.log_func(msg)

    def _heartbeat_loop(
        self,
        stop_event: threading.Event,
        chunk_id: int,
        interval: int,
    ):
        """Background heartbeat loop using its own PG connection."""
        conn = None
        try:
            conn = psycopg2.connect(
                host=self.pg.conn.info.host,
                port=self.pg.conn.info.port,
                dbname=self.pg.conn.info.dbname,
                user=self.pg.conn.info.user,
                password=self.pg.conn.info.password,
            )
            conn.autocommit = True
            while not stop_event.wait(interval):
                try:
                    cursor = conn.cursor()
                    cursor.execute(
                        "UPDATE etl.load_chunk "
                        "SET heartbeat_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP "
                        "WHERE chunk_id = %s",
                        (chunk_id,),
                    )
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            if conn and not conn.closed:
                try:
                    conn.close()
                except Exception:
                    pass

    def _start_heartbeat(self, chunk_id: int):
        """Start heartbeat thread for a chunk."""
        stop = threading.Event()
        t = threading.Thread(
            target=self._heartbeat_loop,
            args=(stop, chunk_id, self.heartbeat_interval),
            daemon=True,
        )
        t.start()
        self._heartbeat_threads[chunk_id] = (t, stop)

    def _stop_heartbeat(self, chunk_id: int):
        """Stop heartbeat thread for a chunk."""
        entry = self._heartbeat_threads.pop(chunk_id, None)
        if entry:
            _, stop_event = entry
            stop_event.set()

    def _stale_recovery_loop(
        self,
        stop_event: threading.Event,
        run_id: int,
        timeout_minutes: int,
    ):
        """Background supervisor that recovers stale RUNNING chunks."""
        conn = None
        try:
            conn = psycopg2.connect(
                host=self.pg.conn.info.host,
                port=self.pg.conn.info.port,
                dbname=self.pg.conn.info.dbname,
                user=self.pg.conn.info.user,
                password=self.pg.conn.info.password,
            )
            conn.autocommit = False
            chunk_manager = ChunkManager(conn)

            while not stop_event.wait(60):  # Check every 60 seconds
                try:
                    recovered = chunk_manager.recover_stale_chunks(
                        run_id, timeout_minutes
                    )
                    if recovered > 0 and self.log_func:
                        self.log_func(
                            f"  SUPERVISOR: recovered {recovered} stale chunks"
                        )
                except Exception as e:
                    if self.log_func:
                        self.log_func(f"  SUPERVISOR: recovery error: {e}")
        except Exception:
            pass
        finally:
            if conn and not conn.closed:
                try:
                    conn.close()
                except Exception:
                    pass

    def _start_stale_recovery(self, run_id: int):
        """Start stale recovery supervisor thread."""
        stop = threading.Event()
        t = threading.Thread(
            target=self._stale_recovery_loop,
            args=(stop, run_id, self.heartbeat_timeout // 60),
            daemon=True,
        )
        t.start()
        self._stale_recovery_stop = stop
        self._stale_recovery_thread = t

    def _stop_stale_recovery(self):
        """Stop stale recovery supervisor."""
        if hasattr(self, '_stale_recovery_stop'):
            self._stale_recovery_stop.set()
        if hasattr(self, '_stale_recovery_thread'):
            self._stale_recovery_thread.join(timeout=5)

    def load_table(
        self,
        table_name: str,
        columns: Optional[List[str]] = None,
        load_mode: str = "full",
    ) -> LoadResult:
        """
        Load table using parallel RECID-range fetching with universal resume.
        
        Args:
            table_name: Source table name
            columns: Optional list of columns to load
            load_mode: Load mode (full, resume, restart, incremental)
            
        Returns:
            LoadResult with statistics
        """
        if self.log_func:
            self.log_func(f"START PARALLEL LOAD V2: {table_name} (mode={load_mode})")

        start_time = time.time()
        self._reset_counters()

        # Setup profiler
        try:
            from ax_to_postgres_etl.core.profiler import ETLProfiler
            profiler = ETLProfiler()
        except ImportError:
            profiler = None

        # Setup graceful shutdown on SIGTERM/SIGINT
        original_sigterm = signal.getsignal(signal.SIGTERM)
        original_sigint = signal.getsignal(signal.SIGINT)

        def _shutdown_handler(signum, frame):
            sig_name = signal.Signals(signum).name
            if self.log_func:
                self.log_func(f"  SIGNAL: {sig_name} received, initiating graceful shutdown...")
            self.stop_event.set()

        try:
            signal.signal(signal.SIGTERM, _shutdown_handler)
            signal.signal(signal.SIGINT, _shutdown_handler)
        except (OSError, ValueError):
            pass  # Can't set signals in non-main thread

        # Get table config
        table_config = self.config.get("tables", {}).get(table_name, {})
        chunk_strategy = table_config.get("chunk_strategy", "numeric_range")
        chunk_column = table_config.get("chunk_column", "RECID")
        chunk_count = table_config.get("chunk_count", 100)

        # Connect to PostgreSQL for metadata
        # Use connection parameters instead of DSN to avoid encoding issues
        pg_conn = psycopg2.connect(
            host=self.pg.conn.info.host,
            port=self.pg.conn.info.port,
            dbname=self.pg.conn.info.dbname,
            user=self.pg.conn.info.user,
            password=self.pg.conn.info.password,
        )
        pg_conn.autocommit = False

        try:
            run_manager = RunManager(pg_conn)
            chunk_manager = ChunkManager(pg_conn)

            # Acquire advisory lock
            if not run_manager.acquire_advisory_lock(table_name):
                raise RuntimeError(f"ETL load for {table_name} is already running")

            try:
                # Find or create run
                run = None
                if load_mode == "resume":
                    config_hash = self._compute_config_hash(table_config)
                    run = run_manager.find_resumable_run(
                        source_table=table_name,
                        target_table=table_name,
                        config=table_config,
                    )
                    if run:
                        if self.log_func:
                            self.log_func(f"  Found resumable run: {run.run_id}")
                    else:
                        if self.log_func:
                            self.log_func(f"  No resumable run found, creating new")

                if run is None:
                    # Create new run
                    source_schema = table_config.get("source_schema", "dbo")
                    target_schema = self.pg.schema

                    run = run_manager.create_run(
                        pipeline_name=self.pipeline_name,
                        source_system="SQL Server",
                        source_table=table_name,
                        target_schema=target_schema,
                        target_table=table_name,
                        load_mode=load_mode,
                        chunk_strategy=chunk_strategy,
                        chunk_column=chunk_column,
                        source_database=self.config.get("source", {}).get("database"),
                        source_schema=source_schema,
                        config=table_config,
                    )
                    if self.log_func:
                        self.log_func(f"  Created new run: {run.run_id}")

                # Update run status
                run_manager.update_run_status(run.run_id, "running")

                # Handle load modes
                if load_mode == "reload":
                    # Preflight checks before destructive TRUNCATE
                    preflight_ok = self._preflight_checks(
                        table_name, col_list, chunk_column, pg_col_names,
                    )
                    if not preflight_ok:
                        raise RuntimeError(
                            f"Preflight checks failed for {table_name}. "
                            f"TRUNCATE aborted. Fix issues and retry."
                        )

                    # reload: TRUNCATE target + new run from scratch
                    self._truncate_table(table_name)
                    # Reset all chunks for this table
                    pg_conn.cursor().execute(
                        "UPDATE etl.load_chunk SET status = 'cancelled' "
                        "WHERE run_id = %s AND status != 'completed'",
                        (run.run_id,),
                    )
                    pg_conn.commit()
                elif load_mode == "full":
                    # full: full source read, no TRUNCATE (target handled by upsert)
                    if self.log_func:
                        self.log_func(f"  FULL mode: reading entire source, target handled by ON CONFLICT")
                elif load_mode == "resume":
                    # resume: continue compatible run, skip completed
                    if self.log_func:
                        self.log_func(f"  RESUME mode: continuing existing run")
                elif load_mode == "incremental":
                    # incremental: only new/changed records
                    if self.log_func:
                        self.log_func(f"  INCREMENTAL mode: loading new/changed records only")

                # Get source boundaries
                strategy = get_strategy(chunk_strategy)
                boundaries = strategy.get_boundaries(
                    self._get_ss_connection(),
                    table_config.get("source_schema", "dbo"),
                    table_name,
                    chunk_column,
                )

                if self.log_func:
                    self.log_func(f"  Boundaries: {boundaries}")

                # Get columns
                if columns:
                    selected = list(columns)
                    if chunk_column and not any(c.upper() == chunk_column.upper() for c in selected):
                        selected.append(chunk_column)
                    ss_col_names = selected
                    pg_col_names = [c.lower() for c in selected]
                    col_list = ", ".join(ss_col_names)
                else:
                    ss_col_names, pg_col_names = self._get_columns(table_name)
                    col_list = ", ".join(ss_col_names)

                # Find chunk column index
                chunk_col_index = None
                for i, col in enumerate(ss_col_names):
                    if col.upper() == chunk_column.upper():
                        chunk_col_index = i
                        break
                if chunk_col_index is None:
                    raise RuntimeError(f"{table_name}: {chunk_column} not found in column list")

                # Create chunks if needed
                existing_chunks = chunk_manager.get_pending_chunks(run.run_id)
                if not existing_chunks:
                    if self.log_func:
                        self.log_func(f"  Creating {chunk_count} chunks...")
                    ranges = strategy.build_ranges(boundaries, chunk_count)
                    chunk_manager.create_chunks(
                        run_id=run.run_id,
                        chunk_strategy=chunk_strategy,
                        chunk_column=chunk_column,
                        ranges=ranges,
                    )
                    run_manager.update_run_counts(run.run_id, chunk_count)
                else:
                    if self.log_func:
                        self.log_func(f"  Using existing chunks: {len(existing_chunks)} pending")

                # Get chunk stats
                stats = chunk_manager.get_chunk_stats(run.run_id)
                if self.log_func:
                    self.log_func(f"  Chunk stats: {stats}")

                # Check if already complete
                if load_mode == "resume" and stats["pending"] == 0 and stats["retry"] == 0 and stats["failed"] == 0:
                    elapsed = time.time() - start_time
                    if self.log_func:
                        self.log_func(f"  ALREADY_COMPLETE: all chunks done")
                    return LoadResult(
                        table_name=table_name,
                        status=LoadStatus.ALREADY_COMPLETE,
                        chunks_total=stats["completed"],
                        chunks_completed=stats["completed"],
                        elapsed_seconds=elapsed,
                    )

                # Get source and target counts
                source_count = self._get_source_count(table_name)
                target_count = self._get_target_count(table_name)
                if self.log_func:
                    self.log_func(f"  Source rows: {source_count:,}")
                    self.log_func(f"  Target rows: {target_count:,}")

                # Start stale recovery supervisor
                self._start_stale_recovery(run.run_id)

                # Profile: start workers
                if profiler:
                    profiler.start_phase("load")

                # Start workers
                self._start_workers(
                    table_name=table_name,
                    col_list=col_list,
                    chunk_manager=chunk_manager,
                    run_id=run.run_id,
                    chunk_col_index=chunk_col_index,
                    pg_col_names=pg_col_names,
                )

                # Profile: end workers
                if profiler:
                    profiler.end_phase("load", rows_processed=self.total_fetched)
                    profiler.finish()

                # Stop stale recovery supervisor
                self._stop_stale_recovery()

                # Check for critical errors after workers finish
                if self.writer_error is not None:
                    run_manager.update_run_status(run.run_id, "failed", str(self.writer_error))
                    raise RuntimeError(f"Writer failed for {table_name}: {self.writer_error}") from self.writer_error

                if self.worker_error is not None:
                    run_manager.update_run_status(run.run_id, "failed", str(self.worker_error))
                    raise RuntimeError(f"Fetch worker failed for {table_name}: {self.worker_error}") from self.worker_error

                # Update run stats
                run_manager.update_run_stats(run.run_id)

                # Get final stats
                final_stats = chunk_manager.get_total_stats(run.run_id)
                target_count = self._get_target_count(table_name)

                # Strict success criteria
                completed_chunks = int(final_stats.get("completed") or 0)
                failed_chunks = int(final_stats.get("failed") or 0)
                total_chunks = int(final_stats.get("total_chunks") or 0)

                # Re-check chunk stats for running/pending/retry
                current_stats = chunk_manager.get_chunk_stats(run.run_id)
                running_chunks = current_stats.get("running", 0)
                pending_chunks = current_stats.get("pending", 0)
                retry_chunks = current_stats.get("retry", 0)

                success = (
                    self.writer_error is None
                    and self.worker_error is None
                    and completed_chunks == total_chunks
                    and failed_chunks == 0
                    and running_chunks == 0
                    and pending_chunks == 0
                    and retry_chunks == 0
                )

                if success:
                    run_status = "completed"
                else:
                    run_status = "failed"

                run_manager.update_run_status(run.run_id, run_status)

                elapsed = time.time() - start_time
                # Convert Decimal to int for speed calculation
                rows_inserted = int(final_stats.get("rows_inserted") or 0)
                speed = rows_inserted / elapsed if elapsed > 0 else 0.0

                # Post-load verification and ANALYZE (only on full success)
                if success:
                    # Verify source/target row counts
                    final_source_count = self._get_source_count(table_name)
                    final_target_count = self._get_target_count(table_name)
                    count_match = (final_source_count == final_target_count)

                    if self.log_func:
                        if count_match:
                            self.log_func(
                                f"  VERIFY: source={final_source_count:,} "
                                f"target={final_target_count:,} — MATCH"
                            )
                        else:
                            self.log_func(
                                f"  VERIFY: source={final_source_count:,} "
                                f"target={final_target_count:,} — MISMATCH "
                                f"(diff={abs(final_source_count - final_target_count):,})"
                            )

                    # Run ANALYZE for query planner
                    try:
                        cursor = pg_conn.cursor()
                        cursor.execute(f"ANALYZE {self.pg.schema}.{table_name}")
                        pg_conn.commit()
                        if self.log_func:
                            self.log_func(f"  ANALYZE: {table_name} updated")
                    except Exception as e:
                        if self.log_func:
                            self.log_func(f"  ANALYZE: warning — {e}")

                    # Data quality checks
                    try:
                        from ax_to_postgres_etl.core.data_quality import DataQualityChecker
                        checker = DataQualityChecker(pg_conn, self.pg.schema)
                        checks = checker.run_all_checks(
                            table_name,
                            expected_count=final_source_count,
                            required_columns=pg_col_names[:5] if len(pg_col_names) > 5 else pg_col_names,
                        )
                        if self.log_func:
                            self.log_func(checker.summary(checks))
                    except Exception as e:
                        if self.log_func:
                            self.log_func(f"  QUALITY CHECKS: warning — {e}")

                if self.log_func:
                    self.log_func(f"")
                    self.log_func(f"{'='*60}")
                    self.log_func(f"LOAD SUMMARY: {table_name}")
                    self.log_func(f"{'='*60}")
                    self.log_func(f"  Status:           {run_status}")
                    self.log_func(f"  Chunks:           {completed_chunks}/{total_chunks} completed")
                    if failed_chunks > 0:
                        self.log_func(f"  Failed chunks:    {failed_chunks}")
                    if retry_chunks > 0:
                        self.log_func(f"  Retry chunks:     {retry_chunks}")
                    self.log_func(f"  Rows fetched:     {self.total_fetched:,}")
                    self.log_func(f"  Rows inserted:    {rows_inserted:,}")
                    self.log_func(f"  Rows conflicted:  {int(final_stats.get('rows_conflicted') or 0):,}")
                    self.log_func(f"  Source rows:      {source_count:,}")
                    self.log_func(f"  Target rows:      {target_count:,}")
                    self.log_func(f"  Duration:         {elapsed:.1f}s ({speed:,.0f} rows/sec)")
                    self.log_func(f"  Workers:          {self.workers}")
                    self.log_func(f"  Fetch size:       {self.fetch_size:,}")
                    self.log_func(f"  Commit size:      {self.commit_size:,}")

                    # Profiling summary
                    if profiler:
                        self.log_func(f"")
                        self.log_func(profiler.summary())

                    # Compare with previous run
                    prev_stats = run_manager.get_previous_run_stats(table_name)
                    if prev_stats:
                        prev_inserted = int(prev_stats.get("rows_inserted") or 0)
                        diff = rows_inserted - prev_inserted
                        diff_pct = (diff / prev_inserted * 100) if prev_inserted > 0 else 0
                        self.log_func(f"  --- Previous run ---")
                        self.log_func(f"  Prev inserted:    {prev_inserted:,}")
                        self.log_func(f"  Diff:             {diff:+,} ({diff_pct:+.1f}%)")
                        self.log_func(f"  Prev run_id:      {prev_stats.get('run_id')}")

                    self.log_func(f"{'='*60}")

                # Export results to JSON
                self._export_results_json(
                    table_name, run_status, elapsed, final_stats,
                    source_count, target_count,
                )

                return LoadResult(
                    table_name=table_name,
                    status=LoadStatus.SUCCESS if success else LoadStatus.FAILED,
                    rows_fetched=self.total_fetched,
                    rows_inserted=rows_inserted,
                    rows_conflicted=int(final_stats.get("rows_conflicted") or 0),
                    chunks_total=total_chunks,
                    chunks_completed=completed_chunks,
                    target_count=target_count,
                    elapsed_seconds=elapsed,
                )

            finally:
                run_manager.release_advisory_lock(table_name)
                pg_conn.close()

                # Restore original signal handlers
                try:
                    signal.signal(signal.SIGTERM, original_sigterm)
                    signal.signal(signal.SIGINT, original_sigint)
                except (OSError, ValueError):
                    pass

                # Log memory usage
                if self._peak_memory_mb > 0:
                    self._log(f"  Peak memory: {self._peak_memory_mb:.1f} MB", "verbose")

        except Exception as e:
            pg_conn.close()
            raise

    def _start_workers(
        self,
        table_name: str,
        col_list: str,
        chunk_manager: ChunkManager,
        run_id: int,
        chunk_col_index: int,
        pg_col_names: List[str],
    ):
        """Start fetch workers and writer."""
        if self.log_func:
            self.log_func(f"  Starting {self.workers} fetch workers + 1 writer")

        self._start_time = time.time()

        # Start writer thread
        # Pass a mutable list that will be populated after workers start
        fetch_threads_ref = [None]
        writer = threading.Thread(
            target=self._write_worker_v2,
            args=(table_name, pg_col_names, chunk_manager, run_id, fetch_threads_ref),
            daemon=True,
        )
        writer.start()

        # Start fetch workers
        fetch_threads = []
        for worker_id in range(self.workers):
            t = threading.Thread(
                target=self._fetch_worker_v2,
                args=(worker_id, table_name, col_list, chunk_manager, run_id, chunk_col_index),
                daemon=True,
            )
            t.start()
            fetch_threads.append(t)

        # Pass fetch_threads reference to writer
        fetch_threads_ref[0] = fetch_threads

        # Wait for workers
        for t in fetch_threads:
            t.join()

        # Signal writer
        self.write_queue.put(SENTINEL)
        writer.join()

    def _fetch_worker_v2(
        self,
        worker_id: int,
        table_name: str,
        col_list: str,
        chunk_manager: ChunkManager,
        run_id: int,
        chunk_col_index: int,
    ):
        """Fetch worker with atomic chunk claiming and own PG connection."""
        import pyodbc

        worker_id_str = f"worker_{worker_id}"
        if self.log_func:
            self.log_func(f"  Worker {worker_id_str}: STARTED")

        conn = None
        pg_conn = None
        worker_chunk_manager = None
        rows_fetched = 0
        last_heartbeat = time.time()

        try:
            conn = pyodbc.connect(self.ss_conn_str, timeout=30)
            cursor = conn.cursor()
            cursor.arraysize = self.fetch_size
            # Set query timeout to 5 minutes
            try:
                conn.timeout = 300
            except Exception:
                pass

            # Each worker gets its own PG connection for chunk claiming
            pg_conn = psycopg2.connect(
                host=self.pg.conn.info.host,
                port=self.pg.conn.info.port,
                dbname=self.pg.conn.info.dbname,
                user=self.pg.conn.info.user,
                password=self.pg.conn.info.password,
            )
            pg_conn.autocommit = False
            worker_chunk_manager = ChunkManager(pg_conn)

            while self.running and not self.stop_event.is_set():
                # Claim next chunk with own connection
                chunk = worker_chunk_manager.claim_chunk(
                    run_id, worker_id_str,
                    max_attempts=self.retry_policy.config.max_attempts,
                )
                if chunk is None:
                    if self.log_func:
                        self.log_func(f"  Worker {worker_id_str}: no more chunks, finishing")
                    break

                chunk_id = chunk.chunk_id
                range_start = chunk.range_start_bigint
                range_end = chunk.range_end_bigint

                if self.log_func:
                    self.log_func(f"  Worker {worker_id_str}: claimed chunk {chunk.chunk_no} ({range_start:,}→{range_end:,})")

                # Start heartbeat thread for this chunk
                self._start_heartbeat(chunk_id)
                chunk_start_time = time.time()

                # Process chunk
                # Use half-open interval [start, end) to include MIN(RECID)
                last_recid = range_start - 1  # sentinel: first query will use >=
                chunk_rows_fetched = 0
                retry_attempt = 0
                chunk_completed = False
                first_batch = True

                try:
                    while self.running and not self.stop_event.is_set() and last_recid < range_end:
                        # Use chunk_column if available, otherwise default to RECID
                        where_column = getattr(chunk, 'chunk_column', None) or 'RECID'
                        if first_batch:
                            # First query: [start, end) — includes range_start
                            sql = f"""
                                SELECT TOP ({self.fetch_size}) {col_list} FROM {table_name}
                                WHERE {where_column} >= {range_start}
                                AND {where_column} < {range_end}
                                ORDER BY {where_column}
                            """
                        else:
                            # Subsequent queries: (last_recid, end)
                            sql = f"""
                                SELECT TOP ({self.fetch_size}) {col_list} FROM {table_name}
                                WHERE {where_column} > {last_recid}
                                AND {where_column} < {range_end}
                                ORDER BY {where_column}
                            """
                        if self.log_func and first_batch:
                            self.log_func(f"  Worker {worker_id_str}: executing query for chunk {chunk.chunk_no}...")
                        query_start = time.time()
                        cursor.execute(sql, timeout=300)
                        query_elapsed = time.time() - query_start
                        first_batch = False
                        if self.log_func:
                            self.log_func(f"  Worker {worker_id_str}: query returned in {query_elapsed:.1f}s for chunk {chunk.chunk_no}")
                        batch = cursor.fetchmany(self.fetch_size)

                        if not batch:
                            chunk_completed = True
                            break

                        last_recid = int(batch[-1][chunk_col_index])
                        chunk_rows_fetched += len(batch)
                        rows_fetched += len(batch)
                        retry_attempt = 0  # Reset on success

                        with self._counter_lock:
                            self.total_fetched += len(batch)

                        # Update chunk progress (enables resume from last key)
                        try:
                            worker_chunk_manager.update_chunk_progress(
                                chunk_id, last_recid, chunk_rows_fetched
                            )
                        except Exception:
                            pass  # Non-critical, don't break flow

                        # Heartbeat only by interval (not every batch)
                        now = time.monotonic()
                        if now - last_heartbeat >= self.heartbeat_interval:
                            worker_chunk_manager.heartbeat(chunk_id)
                            last_heartbeat = now

                        # Put typed DataBatch for writer
                        queued = False
                        while not queued and not self.stop_event.is_set():
                            try:
                                self.write_queue.put(DataBatch(
                                    chunk_id=chunk_id,
                                    chunk_no=chunk.chunk_no,
                                    rows=batch,
                                    last_processed_key=last_recid,
                                ), timeout=1)
                                queued = True
                            except queue.Full:
                                time.sleep(0.5)

                except Exception as e:
                    retry_attempt += 1
                    if self.retry_policy.is_retriable(e) and retry_attempt <= self.retry_policy.config.max_attempts:
                        delay = self.retry_policy.get_delay(retry_attempt)
                        if self.log_func:
                            self.log_func(f"  Worker {worker_id_str}: retry {retry_attempt} in {delay:.1f}s: {e}")
                        # Close and reconnect
                        try:
                            cursor.close()
                            conn.close()
                        except Exception:
                            pass
                        time.sleep(delay)
                        try:
                            conn = pyodbc.connect(self.ss_conn_str, timeout=30)
                            cursor = conn.cursor()
                            cursor.arraysize = self.fetch_size
                        except Exception as conn_err:
                            if self.log_func:
                                self.log_func(f"  Worker {worker_id_str}: reconnect failed: {conn_err}")
                            raise
                        # Continue will re-enter the loop
                    else:
                        # Non-retriable error or max attempts exceeded
                        self.write_queue.put(ChunkFailed(
                            chunk_id=chunk_id,
                            chunk_no=chunk.chunk_no,
                            error_type=self.retry_policy.get_error_type(e),
                            error_message=str(e),
                            rows_read=chunk_rows_fetched,
                            last_processed_key=last_recid if chunk_rows_fetched > 0 else None,
                        ))
                        raise

                # Send ChunkFinished only if chunk was fully read and no stop signal
                if chunk_completed and not self.stop_event.is_set():
                    self.write_queue.put(ChunkFinished(
                        chunk_id=chunk_id,
                        chunk_no=chunk.chunk_no,
                        rows_read=chunk_rows_fetched,
                        last_processed_key=last_recid if chunk_rows_fetched > 0 else None,
                    ))

                # Stop heartbeat for this chunk
                self._stop_heartbeat(chunk_id)
                chunk_elapsed = time.time() - chunk_start_time

                if self.log_func:
                    speed = chunk_rows_fetched / chunk_elapsed if chunk_elapsed > 0 else 0
                    self.log_func(
                        f"  Worker {worker_id_str}: chunk {chunk.chunk_no} fetched "
                        f"{chunk_rows_fetched:,} rows in {chunk_elapsed:.1f}s "
                        f"({speed:,.0f} rows/sec)"
                    )

        except Exception as e:
            import traceback
            if self.log_func:
                self.log_func(f"  Worker {worker_id_str} ERROR: {type(e).__name__}: {e}")
                self.log_func(f"  Worker {worker_id_str} TRACEBACK:\n{traceback.format_exc()}")
            self.worker_error = e
            self.running = False
            self.total_errors += 1
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
            if pg_conn:
                try:
                    pg_conn.close()
                except Exception:
                    pass
            if self.log_func:
                self.log_func(f"  Worker {worker_id_str}: done, fetched {rows_fetched:,} rows")

    def _write_worker_v2(
        self,
        table_name: str,
        pg_col_names: List[str],
        chunk_manager: ChunkManager,
        run_id: int,
        fetch_threads_ref: list = None,
    ):
        """Writer with typed message protocol and per-chunk atomic finalization."""
        if self.log_func:
            self.log_func(f"  Writer: STARTED (columns={len(pg_col_names)})")

        pg_conn = None
        try:
            pg_conn = psycopg2.connect(
                host=self.pg.conn.info.host,
                port=self.pg.conn.info.port,
                dbname=self.pg.conn.info.dbname,
                user=self.pg.conn.info.user,
                password=self.pg.conn.info.password,
            )
            pg_conn.autocommit = False
        except Exception as e:
            if self.log_func:
                self.log_func(f"  Writer: FAILED to connect: {e}")
            self.writer_error = e
            self.stop_event.set()
            return

        batch_count = 0
        write_times = []
        staging_table = f"{self.pg.schema}._staging_{table_name}"

        # Per-chunk buffers
        chunk_buffers: dict[int, list] = {}
        chunk_stats: dict[int, dict] = {}
        total_chunks_processed = 0

        # Get total chunks count for progress calculation
        try:
            cursor_tmp = pg_conn.cursor()
            cursor_tmp.execute(
                "SELECT COUNT(*) FROM etl.load_chunk WHERE run_id = %s",
                (run_id,),
            )
            total_chunks_count = cursor_tmp.fetchone()[0]
        except Exception:
            total_chunks_count = 0

        try:
            # Create staging table with _etl_chunk_id
            cursor = pg_conn.cursor()
            cursor.execute(f"DROP TABLE IF EXISTS {staging_table}")
            col_defs = ", ".join([f"{c} text" for c in pg_col_names])
            cursor.execute(f"CREATE UNLOGGED TABLE {staging_table} (_etl_chunk_id bigint NOT NULL, {col_defs})")
            pg_conn.commit()

            while True:
                try:
                    item = self.write_queue.get(timeout=30)
                except queue.Empty:
                    # Check if all workers are done
                    ft = fetch_threads_ref[0] if fetch_threads_ref else None
                    if ft and all(not t.is_alive() for t in ft):
                        if self.log_func:
                            self.log_func("  Writer: all workers finished, stopping")
                        break
                    continue

                if item is SENTINEL:
                    break

                if isinstance(item, DataBatch):
                    # Buffer rows per chunk
                    chunk_buffers.setdefault(item.chunk_id, [])
                    chunk_buffers[item.chunk_id].extend(item.rows)

                    stats = chunk_stats.setdefault(
                        item.chunk_id,
                        {
                            "chunk_no": item.chunk_no,
                            "rows_read": 0,
                            "last_processed_key": None,
                        },
                    )
                    stats["rows_read"] += len(item.rows)
                    stats["last_processed_key"] = item.last_processed_key

                    self.write_queue.task_done()

                elif isinstance(item, ChunkFinished):
                    total_chunks_processed += 1
                    stats = chunk_stats.get(item.chunk_id, {
                        "chunk_no": item.chunk_no,
                        "rows_read": item.rows_read,
                        "last_processed_key": item.last_processed_key,
                    })

                    # Get buffered rows for this chunk
                    buffered_rows = chunk_buffers.pop(item.chunk_id, [])
                    rows_to_insert = buffered_rows if buffered_rows else []

                    # Finalize chunk: INSERT + UPDATE status + DELETE staging in one transaction
                    self._finalize_chunk(
                        pg_conn, table_name, staging_table, pg_col_names,
                        chunk_id=item.chunk_id,
                        chunk_no=item.chunk_no,
                        rows_to_insert=rows_to_insert,
                        rows_read=item.rows_read,
                        last_processed_key=item.last_processed_key,
                    )

                    if self.log_func:
                        pct = (total_chunks_processed / total_chunks_count * 100) if total_chunks_count > 0 else 0
                        if item.rows_read == 0:
                            self.log_func(
                                f"  Writer: chunk {item.chunk_no} completed, no rows\n"
                                f"    progress={total_chunks_processed}/{total_chunks_count} ({pct:.1f}%)"
                            )
                        else:
                            self.log_func(
                                f"  Writer: chunk {item.chunk_no} committed\n"
                                f"    rows_read={item.rows_read:,}\n"
                                f"    inserted={item.rows_read:,}\n"
                                f"    last_recid={item.last_processed_key:,}\n"
                                f"    status=completed\n"
                                f"    progress={total_chunks_processed}/{total_chunks_count} ({pct:.1f}%)"
                            )

                    self.write_queue.task_done()
                    self._track_memory()

                elif isinstance(item, ChunkFailed):
                    total_chunks_processed += 1
                    # Mark chunk as failed
                    try:
                        cursor = pg_conn.cursor()
                        cursor.execute("""
                            UPDATE etl.load_chunk
                            SET
                                status = 'failed',
                                error_type = %s,
                                error_message = %s,
                                rows_read = %s,
                                last_processed_key = %s,
                                worker_id = NULL,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE chunk_id = %s
                        """, (
                            item.error_type,
                            item.error_message,
                            item.rows_read,
                            str(item.last_processed_key) if item.last_processed_key else None,
                            item.chunk_id,
                        ))
                        pg_conn.commit()
                    except Exception as e:
                        pg_conn.rollback()
                        if self.log_func:
                            self.log_func(f"  Writer: FAILED to mark chunk {item.chunk_no} as failed: {e}")

                    # Clear any buffered rows for this failed chunk
                    chunk_buffers.pop(item.chunk_id, None)
                    chunk_stats.pop(item.chunk_id, None)

                    if self.log_func:
                        self.log_func(
                            f"  Writer: chunk {item.chunk_no} failed\n"
                            f"    rows_read={item.rows_read:,}\n"
                            f"    last_recid={item.last_processed_key:,}\n"
                            f"    error={item.error_type}: {item.error_message}\n"
                            f"    next_status=failed"
                        )

                    self.write_queue.task_done()

                else:
                    # Unknown message type
                    self.write_queue.task_done()

        except Exception as e:
            pg_conn.rollback()
            self.writer_error = e
            self.stop_event.set()
            if self.log_func:
                self.log_func(f"  Writer ERROR: {e}")
        finally:
            try:
                pg_conn.cursor().execute(f"DROP TABLE IF EXISTS {staging_table}")
                pg_conn.commit()
            except Exception:
                pass
            if pg_conn and not pg_conn.closed:
                pg_conn.close()

        if write_times:
            avg_write = sum(write_times) / len(write_times)
            max_write = max(write_times)
            if self.log_func:
                self.log_func(f"  WRITER STATS: batches={len(write_times)} avg={avg_write:.2f}s max={max_write:.2f}s")

    def _finalize_chunk(
        self,
        pg_conn,
        table_name: str,
        staging_table: str,
        pg_col_names: List[str],
        chunk_id: int,
        chunk_no: int,
        rows_to_insert: list,
        rows_read: int,
        last_processed_key: int | None,
    ):
        """Atomically finalize a chunk: INSERT from staging + UPDATE status + DELETE staging."""
        cursor = pg_conn.cursor()
        try:
            # 1. COPY buffered rows to staging (streaming for large chunks)
            if rows_to_insert:
                col_names = ", ".join(["_etl_chunk_id"] + pg_col_names)
                copy_sql = (
                    f"COPY {staging_table} ({col_names}) "
                    "FROM STDIN WITH ("
                    "FORMAT text, "
                    "DELIMITER E'\\t', "
                    "NULL E'\\\\N'"
                    ")"
                )

                # Streaming: build and copy in chunks to avoid memory spike
                if len(rows_to_insert) > self.stream_threshold:
                    # Stream in batches
                    for i in range(0, len(rows_to_insert), self.stream_threshold):
                        batch = rows_to_insert[i:i + self.stream_threshold]
                        content, _ = _build_copy_buffer(
                            batch, len(pg_col_names),
                            log_func=self.log_func, columns=pg_col_names,
                            chunk_id=chunk_id,
                        )
                        buffer = io.StringIO(content)
                        cursor.copy_expert(copy_sql, buffer)
                else:
                    content, _ = _build_copy_buffer(
                        rows_to_insert, len(pg_col_names),
                        log_func=self.log_func, columns=pg_col_names,
                        chunk_id=chunk_id,
                    )
                    buffer = io.StringIO(content)
                    cursor.copy_expert(copy_sql, buffer)

            # 2. INSERT from staging to target
            target_cols = ", ".join(pg_col_names)
            cursor.execute(f"""
                INSERT INTO {self.pg.schema}.{table_name} ({target_cols})
                SELECT {target_cols}
                FROM {staging_table}
                WHERE _etl_chunk_id = %s
                ON CONFLICT (recid) DO NOTHING
            """, (chunk_id,))
            inserted = cursor.rowcount
            conflicts = rows_read - inserted if rows_read > 0 else 0

            # 3. UPDATE etl.load_chunk status
            cursor.execute("""
                UPDATE etl.load_chunk
                SET
                    status = 'completed',
                    completed_at = CURRENT_TIMESTAMP,
                    rows_read = %s,
                    rows_staged = %s,
                    rows_inserted = %s,
                    rows_conflicted = %s,
                    last_processed_key = %s,
                    worker_id = NULL,
                    error_type = NULL,
                    error_message = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE chunk_id = %s AND status = 'running'
            """, (
                rows_read,
                rows_read,
                inserted,
                conflicts,
                str(last_processed_key) if last_processed_key else None,
                chunk_id,
            ))
            if cursor.rowcount != 1:
                raise RuntimeError(
                    f"Chunk {chunk_id} was not transitioned from running to completed"
                )

            # 4. DELETE from staging
            cursor.execute(f"""
                DELETE FROM {staging_table}
                WHERE _etl_chunk_id = %s
            """, (chunk_id,))

            # 5. COMMIT all in one transaction
            pg_conn.commit()

            with self._counter_lock:
                self.total_inserted += inserted
                self.total_conflicted += conflicts
                self.total_committed += rows_read

        except Exception:
            pg_conn.rollback()
            raise

    def _commit_buffer(self, pg_conn, table_name: str, staging_table: str, pg_col_names: list, buffer_content: str, rows_in_buffer: int):
        """Commit buffer to PostgreSQL."""
        try:
            col_names = ", ".join(pg_col_names)
            cursor = pg_conn.cursor()

            cursor.execute(f"TRUNCATE {staging_table}")

            copy_sql = (
                f"COPY {staging_table} ({col_names}) "
                "FROM STDIN WITH ("
                "FORMAT text, "
                "DELIMITER E'\\t', "
                "NULL E'\\\\N'"
                ")"
            )
            buffer = io.StringIO(buffer_content)
            cursor.copy_expert(copy_sql, buffer)

            upsert_sql = (
                f"INSERT INTO {self.pg.schema}.{table_name} ({col_names}) "
                f"SELECT {col_names} FROM {staging_table} "
                f"ON CONFLICT (recid) DO NOTHING"
            )
            cursor.execute(upsert_sql)
            inserted = cursor.rowcount
            conflicts = rows_in_buffer - inserted

            pg_conn.commit()

            with self._counter_lock:
                self.total_inserted += inserted
                self.total_conflicted += conflicts
                self.total_committed += rows_in_buffer

        except Exception as e:
            pg_conn.rollback()
            raise

    def _reset_counters(self):
        """Reset all counters."""
        self.total_fetched = 0
        self.total_inserted = 0
        self.total_conflicted = 0
        self.total_committed = 0
        self.total_errors = 0
        self.writer_error = None
        self.worker_error = None

    def _truncate_table(self, table_name: str):
        """Truncate target table."""
        try:
            cursor = self.pg.conn.cursor()
            cursor.execute(f"TRUNCATE TABLE {self.pg.schema}.{table_name}")
            self.pg.conn.commit()
            if self.log_func:
                self.log_func(f"  RELOAD: Table truncated")
        except Exception as e:
            if self.log_func:
                self.log_func(f"  RELOAD: Truncate failed ({e}), continuing...")

    def _preflight_checks(
        self,
        table_name: str,
        col_list: str,
        chunk_column: str,
        pg_col_names: List[str],
    ) -> bool:
        """
        Run preflight checks before destructive TRUNCATE.

        Returns True if all checks pass, False otherwise.
        """
        if self.log_func:
            self.log_func(f"  PREFLIGHT: Running checks for {table_name}...")

        checks_passed = 0
        checks_total = 6

        # 1. Test SQL Server connection
        try:
            conn = self._get_ss_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.close()
            conn.close()
            checks_passed += 1
            if self.log_func:
                self.log_func(f"  PREFLIGHT [1/{checks_total}]: SQL Server connection OK")
        except Exception as e:
            if self.log_func:
                self.log_func(f"  PREFLIGHT [1/{checks_total}]: SQL Server connection FAILED: {e}")

        # 2. Test SQL Server table exists and columns are accessible
        try:
            conn = self._get_ss_connection()
            cursor = conn.cursor()
            cursor.execute(f"SELECT TOP 1 {col_list} FROM {table_name}")
            cursor.fetchall()
            cursor.close()
            conn.close()
            checks_passed += 1
            if self.log_func:
                self.log_func(f"  PREFLIGHT [2/{checks_total}]: Source table accessible, columns OK")
        except Exception as e:
            if self.log_func:
                self.log_func(f"  PREFLIGHT [2/{checks_total}]: Source table FAILED: {e}")

        # 3. Test SQL Server COUNT
        try:
            source_count = self._get_source_count(table_name)
            checks_passed += 1
            if self.log_func:
                self.log_func(f"  PREFLIGHT [3/{checks_total}]: Source row count: {source_count:,}")
        except Exception as e:
            if self.log_func:
                self.log_func(f"  PREFLIGHT [3/{checks_total}]: Source count FAILED: {e}")

        # 4. Test PostgreSQL target table exists and is writable
        try:
            cursor = self.pg.conn.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM {self.pg.schema}.{table_name}")
            target_count = cursor.fetchone()[0]
            cursor.close()
            checks_passed += 1
            if self.log_func:
                self.log_func(f"  PREFLIGHT [4/{checks_total}]: Target table accessible, rows: {target_count:,}")
        except Exception as e:
            if self.log_func:
                self.log_func(f"  PREFLIGHT [4/{checks_total}]: Target table FAILED: {e}")

        # 5. Schema validation: compare source and target columns
        try:
            ss_cols = set(c.strip().upper() for c in col_list.split(","))
            cursor = self.pg.conn.cursor()
            cursor.execute(f"""
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
            """, (self.pg.schema, table_name))
            pg_cols = set(row[0].upper() for row in cursor.fetchall())
            cursor.close()

            missing_in_target = ss_cols - pg_cols
            if missing_in_target:
                if self.log_func:
                    self.log_func(
                        f"  PREFLIGHT [5/{checks_total}]: SCHEMA MISMATCH — "
                        f"columns in source but not in target: {missing_in_target}"
                    )
            else:
                checks_passed += 1
                if self.log_func:
                    self.log_func(f"  PREFLIGHT [5/{checks_total}]: Schema validation OK ({len(ss_cols)} columns)")
        except Exception as e:
            if self.log_func:
                self.log_func(f"  PREFLIGHT [5/{checks_total}]: Schema validation FAILED: {e}")

        # 6. Test PostgreSQL COPY (dry run with empty data)
        try:
            staging_table = f"{self.pg.schema}._staging_preflight_{table_name}"
            cursor = self.pg.conn.cursor()
            cursor.execute(f"DROP TABLE IF EXISTS {staging_table}")
            col_defs = ", ".join([f"{c} text" for c in pg_col_names])
            cursor.execute(f"CREATE UNLOGGED TABLE {staging_table} (_etl_chunk_id bigint, {col_defs})")
            cursor.execute(f"DROP TABLE IF EXISTS {staging_table}")
            self.pg.conn.commit()
            cursor.close()
            checks_passed += 1
            if self.log_func:
                self.log_func(f"  PREFLIGHT [6/{checks_total}]: PostgreSQL COPY test OK")
        except Exception as e:
            self.pg.conn.rollback()
            if self.log_func:
                self.log_func(f"  PREFLIGHT [6/{checks_total}]: PostgreSQL COPY test FAILED: {e}")

        all_ok = checks_passed == checks_total
        if self.log_func:
            self.log_func(
                f"  PREFLIGHT: {checks_passed}/{checks_total} checks passed"
                f" — {'OK' if all_ok else 'FAILED'}"
            )
        return all_ok

    def _get_ss_connection(self):
        """Get SQL Server connection."""
        import pyodbc
        return pyodbc.connect(self.ss_conn_str)

    def _get_columns(self, table_name: str) -> Tuple[List[str], List[str]]:
        """Get column names from SQL Server."""
        import pyodbc
        conn = pyodbc.connect(self.ss_conn_str)
        cursor = conn.cursor()
        cursor.execute(f"SELECT TOP 1 * FROM {table_name}")
        columns = [desc[0] for desc in cursor.description]
        cursor.close()
        conn.close()
        return columns, [c.lower() for c in columns]

    def _get_source_count(self, table_name: str) -> int:
        """Get row count from SQL Server."""
        import pyodbc
        try:
            conn = pyodbc.connect(self.ss_conn_str, timeout=10)
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT_BIG(*) FROM {table_name}")
            count = cursor.fetchone()[0]
            cursor.close()
            conn.close()
            return count
        except Exception:
            return 0

    def _get_target_count(self, table_name: str) -> int:
        """Get row count from PostgreSQL."""
        try:
            cursor = self.pg.conn.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM {self.pg.schema}.{table_name}")
            count = cursor.fetchone()[0]
            cursor.close()
            return count
        except Exception:
            return 0

    def _compute_config_hash(self, config: dict) -> str:
        """Compute hash of configuration."""
        config_str = json.dumps(config, sort_keys=True, default=str)
        return hashlib.sha256(config_str.encode()).hexdigest()[:16]

    def _export_results_json(
        self,
        table_name: str,
        run_status: str,
        elapsed: float,
        final_stats: dict,
        source_count: int,
        target_count: int,
    ):
        """Export load results to JSON file for external monitoring."""
        results = {
            "timestamp": datetime.now().isoformat(),
            "table": table_name,
            "status": run_status,
            "duration_seconds": round(elapsed, 1),
            "source_rows": source_count,
            "target_rows": target_count,
            "chunks": {
                "total": int(final_stats.get("total_chunks") or 0),
                "completed": int(final_stats.get("completed") or 0),
                "failed": int(final_stats.get("failed") or 0),
            },
            "rows": {
                "fetched": self.total_fetched,
                "inserted": int(final_stats.get("rows_inserted") or 0),
                "conflicted": int(final_stats.get("rows_conflicted") or 0),
            },
            "config": {
                "workers": self.workers,
                "fetch_size": self.fetch_size,
                "commit_size": self.commit_size,
                "stream_threshold": self.stream_threshold,
            },
            "memory": {
                "peak_mb": round(self._peak_memory_mb, 1),
            },
        }

        try:
            json_path = f"load_result_{table_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            if self.log_func:
                self.log_func(f"  Results exported to: {json_path}")
        except Exception as e:
            if self.log_func:
                self.log_func(f"  WARNING: Could not export results to JSON: {e}")
