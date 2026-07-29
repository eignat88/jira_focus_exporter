from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict

import psycopg2

from ax_to_postgres_etl.core.chunk_manager import ChunkManager
from ax_to_postgres_etl.core.run_manager import RunManager
from ax_to_postgres_etl.core.retry import RetryPolicy
from ax_to_postgres_etl.core.postgres_runtime import HeartbeatThread, PostgresRuntime

from .contracts import LoadAdapter, PipelineSpec


class PipelineRunner:
    """Shared orchestration for SQL Server->RAW and PostgreSQL RAW->DDS."""

    def __init__(self, dsn: str, retry_policy: RetryPolicy | None = None):
        self.dsn = dsn
        self.retry = retry_policy or RetryPolicy()

    def recover_stale_runs(self, pipeline_name: str, stale_minutes: int = 5):
        """Close runs with stale heartbeat."""
        try:
            conn = psycopg2.connect(self.dsn)
            cur = conn.cursor()
            cur.execute("""
                UPDATE etl.load_run
                SET status = 'failed',
                    finished_at = CURRENT_TIMESTAMP,
                    error_message = CONCAT_WS(E'\n', error_message,
                        'Automatically closed: stale heartbeat')
                WHERE pipeline_name = %s
                  AND status = 'running'
                  AND heartbeat_at < CURRENT_TIMESTAMP - make_interval(mins => %s)
            """, (pipeline_name, stale_minutes))
            if cur.rowcount > 0:
                logging.warning("Recovered %d stale run(s)", cur.rowcount)
            conn.commit()
            conn.close()
        except Exception:
            logging.exception("Failed to recover stale runs")

    def run(self, spec: PipelineSpec, adapter: LoadAdapter) -> int:
        run_ref = uuid.uuid4().hex[:10]
        run_id = None

        with PostgresRuntime(self.dsn, spec.name, run_ref) as rt:
            run_manager = RunManager(rt.metadata)
            chunk_manager = ChunkManager(rt.metadata)
            lock_name = f"{spec.target_schema}.{spec.target_table}"

            if not run_manager.acquire_advisory_lock(lock_name):
                raise RuntimeError(f"Pipeline already running for {lock_name}")

            heartbeat = None
            try:
                if spec.load_mode == "resume":
                    run = run_manager.find_resumable_run(
                        spec.source_table,
                        spec.target_table,
                        asdict(spec),
                    )
                    if run is None:
                        raise RuntimeError(
                            f"No resumable run found for "
                            f"{spec.source_schema}.{spec.source_table} -> "
                            f"{spec.target_schema}.{spec.target_table}"
                        )
                else:
                    run = run_manager.create_run(
                        pipeline_name=spec.name,
                        source_system=spec.source_system,
                        source_database=None,
                        source_schema=spec.source_schema,
                        source_table=spec.source_table,
                        target_schema=spec.target_schema,
                        target_table=spec.target_table,
                        load_mode=spec.load_mode,
                        chunk_strategy="numeric_range",
                        chunk_column=spec.key_column,
                        config=asdict(spec),
                    )
                run_id = run.run_id

                run_manager.update_run_status(run.run_id, "running")
                heartbeat = HeartbeatThread(
                    rt.heartbeat_connection,
                    "UPDATE etl.load_run SET heartbeat_at=CURRENT_TIMESTAMP, "
                    "updated_at=CURRENT_TIMESTAMP WHERE run_id=%s",
                    (run.run_id,),
                    interval_seconds=15,
                )
                heartbeat.start()

                existing = chunk_manager.get_chunk_stats(run.run_id)
                if not existing or existing.get("total_chunks", 0) == 0:
                    lower, upper = adapter.get_boundaries(rt.data, spec)
                    ranges = adapter.build_ranges(lower, upper, spec.batch_size)
                    chunk_manager.create_chunks(
                        run.run_id, "numeric_range", spec.key_column, ranges
                    )
                    run_manager.update_run_counts(run.run_id, len(ranges))

                worker_id = f"raw-dds-{uuid.uuid4().hex[:8]}"
                while True:
                    chunk = chunk_manager.claim_chunk(run.run_id, worker_id)
                    if chunk is None:
                        break
                    self._run_chunk(rt.data, chunk_manager, adapter, spec, chunk)
                    run_manager.update_run_stats(run.run_id)

                stats = chunk_manager.get_total_stats(run.run_id)
                failed = int(stats.get("failed", 0))
                status = "completed" if failed == 0 else "completed_with_errors"
                run_manager.update_run_stats(run.run_id)
                run_manager.update_run_status(run.run_id, status)
                validation = adapter.validate(rt.data, spec)
                logging.info("Pipeline validation: %s", validation)
                return run.run_id

            except (KeyboardInterrupt, psycopg2.errors.QueryCanceled) as exc:
                try:
                    rt.data.rollback()
                except Exception:
                    logging.exception("Failed to rollback data connection after cancellation")

                try:
                    rt.metadata.rollback()
                except Exception:
                    logging.exception("Failed to rollback metadata connection after cancellation")

                if run_id is not None:
                    try:
                        run_manager.update_run_status(run_id, "cancelled", str(exc))
                    except Exception:
                        try:
                            rt.metadata.rollback()
                        except Exception:
                            pass
                        logging.exception("Failed to persist cancelled run status")
                raise

            except Exception as exc:
                try:
                    rt.data.rollback()
                except Exception:
                    logging.exception("Failed to rollback data connection")

                # RunManager and ChunkManager use rt.metadata. Any SQL error on
                # that connection aborts the transaction until explicit rollback.
                try:
                    rt.metadata.rollback()
                except Exception:
                    logging.exception("Failed to rollback metadata connection")

                if run_id is not None:
                    try:
                        run_manager.update_run_status(run_id, "failed", str(exc))
                    except Exception:
                        try:
                            rt.metadata.rollback()
                        except Exception:
                            pass
                        logging.exception("Failed to persist failed run status")
                raise

            finally:
                if heartbeat:
                    heartbeat.stop()
                    heartbeat.join(timeout=30)

                try:
                    rt.metadata.rollback()
                except Exception:
                    logging.exception("Failed to prepare metadata connection for advisory unlock")

                try:
                    run_manager.release_advisory_lock(lock_name)
                except Exception:
                    try:
                        rt.metadata.rollback()
                    except Exception:
                        pass
                    logging.exception("Failed to release advisory lock")

    def _run_chunk(self, data_conn, chunks, adapter, spec, chunk):
        start = int(chunk.range_start_bigint)
        end = int(chunk.range_end_bigint)
        attempt = 0
        while True:
            attempt += 1
            started = time.monotonic()
            try:
                result = adapter.execute_batch(data_conn, spec, start, end)
                chunks.complete_chunk(
                    chunk.chunk_id,
                    rows_read=result.rows_read,
                    rows_inserted=result.rows_inserted,
                    rows_updated=result.rows_updated,
                    rows_conflicted=result.rows_conflicted,
                    last_processed_key=result.last_processed_key,
                )
                logging.info(
                    "Chunk %s completed: %s rows in %.1fs",
                    chunk.chunk_no,
                    result.rows_inserted,
                    time.monotonic() - started,
                )
                return
            except Exception as exc:
                data_conn.rollback()
                if not self.retry.should_retry(exc, attempt):
                    chunks.fail_chunk(
                        chunk.chunk_id,
                        self.retry.get_error_type(exc),
                        str(exc),
                    )
                    raise
                delay = self.retry.get_delay(attempt)
                logging.warning("Chunk retry in %.1fs after error: %s", delay, exc)
                time.sleep(delay)
