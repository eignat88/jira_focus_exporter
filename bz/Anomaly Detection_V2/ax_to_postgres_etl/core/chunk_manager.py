"""Chunk manager for ETL load operations."""

from datetime import datetime, timedelta
from typing import Optional, List, Tuple

import psycopg2
import psycopg2.extras

from .models import Chunk, ChunkStatus


class ChunkManager:
    """Manages ETL load chunks in PostgreSQL."""

    def __init__(self, conn: psycopg2.extensions.connection, schema: str = "etl"):
        """
        Initialize ChunkManager.
        
        Args:
            conn: PostgreSQL connection
            schema: Schema name for ETL tables (default: etl)
        """
        self.conn = conn
        self.schema = schema

    def create_chunks(
        self,
        run_id: int,
        chunk_strategy: str,
        chunk_column: Optional[str],
        ranges: List[Tuple],
    ) -> List[Chunk]:
        """
        Create chunks for a run.
        
        Args:
            run_id: Run ID
            chunk_strategy: Chunking strategy
            chunk_column: Column used for chunking
            ranges: List of (start, end) tuples
            
        Returns:
            List of created Chunk objects
        """
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        chunks = []
        
        for i, (range_start, range_end) in enumerate(ranges):
            cursor.execute(
                f"""
                INSERT INTO {self.schema}.load_chunk (
                    run_id, chunk_no, chunk_strategy, chunk_column,
                    range_start_bigint, range_end_bigint, status
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s
                )
                RETURNING *
                """,
                (
                    run_id, i, chunk_strategy, chunk_column,
                    range_start, range_end, ChunkStatus.PENDING.value,
                ),
            )
            row = cursor.fetchone()
            chunks.append(self._row_to_chunk(row))
        
        self.conn.commit()
        return chunks

    def create_text_chunks(
        self,
        run_id: int,
        chunk_strategy: str,
        chunk_column: Optional[str],
        ranges: List[Tuple[str, str]],
    ) -> List[Chunk]:
        """
        Create text-range chunks for a run.
        
        Args:
            run_id: Run ID
            chunk_strategy: Chunking strategy
            chunk_column: Column used for chunking
            ranges: List of (start_text, end_text) tuples
            
        Returns:
            List of created Chunk objects
        """
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        chunks = []
        
        for i, (range_start, range_end) in enumerate(ranges):
            cursor.execute(
                f"""
                INSERT INTO {self.schema}.load_chunk (
                    run_id, chunk_no, chunk_strategy, chunk_column,
                    range_start_text, range_end_text, status
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s
                )
                RETURNING *
                """,
                (
                    run_id, i, chunk_strategy, chunk_column,
                    range_start, range_end, ChunkStatus.PENDING.value,
                ),
            )
            row = cursor.fetchone()
            chunks.append(self._row_to_chunk(row))
        
        self.conn.commit()
        return chunks

    def create_timestamp_chunks(
        self,
        run_id: int,
        chunk_strategy: str,
        chunk_column: Optional[str],
        ranges: List[Tuple[datetime, datetime]],
    ) -> List[Chunk]:
        """
        Create timestamp-range chunks for a run.
        
        Args:
            run_id: Run ID
            chunk_strategy: Chunking strategy
            chunk_column: Column used for chunking
            ranges: List of (start_ts, end_ts) tuples
            
        Returns:
            List of created Chunk objects
        """
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        chunks = []
        
        for i, (range_start, range_end) in enumerate(ranges):
            cursor.execute(
                f"""
                INSERT INTO {self.schema}.load_chunk (
                    run_id, chunk_no, chunk_strategy, chunk_column,
                    range_start_ts, range_end_ts, status
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s
                )
                RETURNING *
                """,
                (
                    run_id, i, chunk_strategy, chunk_column,
                    range_start, range_end, ChunkStatus.PENDING.value,
                ),
            )
            row = cursor.fetchone()
            chunks.append(self._row_to_chunk(row))
        
        self.conn.commit()
        return chunks

    def create_single_chunk(
        self,
        run_id: int,
        chunk_strategy: str,
    ) -> Chunk:
        """
        Create a single chunk for full_table strategy.
        
        Args:
            run_id: Run ID
            chunk_strategy: Chunking strategy
            
        Returns:
            Created Chunk object
        """
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(
            f"""
            INSERT INTO {self.schema}.load_chunk (
                run_id, chunk_no, chunk_strategy, status
            ) VALUES (
                %s, 0, %s, %s
            )
            RETURNING *
            """,
            (run_id, chunk_strategy, ChunkStatus.PENDING.value),
        )
        row = cursor.fetchone()
        self.conn.commit()
        return self._row_to_chunk(row)

    def recover_stale_chunks(
        self,
        run_id: int,
        timeout_minutes: int = 10,
    ) -> int:
        """
        Recover stale RUNNING chunks without incrementing attempt_count.

        Should be called separately, not inside claim_chunk.
        Once at resume start, then by supervisor no more than once per minute.

        Args:
            run_id: Run ID
            timeout_minutes: Timeout for stale RUNNING chunks

        Returns:
            Number of recovered chunks
        """
        cursor = self.conn.cursor()
        stale_cutoff = datetime.now() - timedelta(minutes=timeout_minutes)
        cursor.execute(
            f"""
            UPDATE {self.schema}.load_chunk
            SET status = 'retry',
                worker_id = NULL,
                error_type = 'heartbeat_timeout',
                error_message = 'Heartbeat expired',
                updated_at = CURRENT_TIMESTAMP
            WHERE run_id = %s
              AND status = 'running'
              AND heartbeat_at < %s
            """,
            (run_id, stale_cutoff),
        )
        recovered = cursor.rowcount
        self.conn.commit()
        return recovered

    def claim_chunk(
        self,
        run_id: int,
        worker_id: str,
        max_attempts: int = 5,
    ) -> Optional[Chunk]:
        """
        Atomically claim a chunk for processing.

        Uses FOR UPDATE SKIP LOCKED to prevent double-processing.
        Does NOT perform stale recovery — call recover_stale_chunks separately.

        Args:
            run_id: Run ID
            worker_id: Worker identifier
            max_attempts: Maximum retry attempts (configurable from YAML)

        Returns:
            Chunk object if claimed, None otherwise
        """
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Claim next pending chunk (attempt_count incremented only on new claim)
        cursor.execute(
            f"""
            WITH next_chunk AS (
                SELECT chunk_id
                FROM {self.schema}.load_chunk
                WHERE run_id = %s
                  AND status IN ('pending', 'retry', 'failed')
                  AND attempt_count < %s
                ORDER BY chunk_no
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE {self.schema}.load_chunk c
            SET
                status = 'running',
                worker_id = %s,
                started_at = CURRENT_TIMESTAMP,
                heartbeat_at = CURRENT_TIMESTAMP,
                attempt_count = attempt_count + 1,
                updated_at = CURRENT_TIMESTAMP
            FROM next_chunk n
            WHERE c.chunk_id = n.chunk_id
            RETURNING c.*
            """,
            (run_id, max_attempts, worker_id),
        )

        row = cursor.fetchone()
        self.conn.commit()

        return self._row_to_chunk(row) if row else None

    def complete_chunk(
        self,
        chunk_id: int,
        rows_read: int = 0,
        rows_staged: int = 0,
        rows_inserted: int = 0,
        rows_updated: int = 0,
        rows_conflicted: int = 0,
        last_processed_key: Optional[str] = None,
    ):
        """
        Mark chunk as completed.
        
        Args:
            chunk_id: Chunk ID
            rows_read: Number of rows read
            rows_staged: Number of rows staged
            rows_inserted: Number of rows inserted
            rows_updated: Number of rows updated
            rows_conflicted: Number of rows conflicted
            last_processed_key: Last processed key value
        """
        cursor = self.conn.cursor()
        cursor.execute(
            f"""
            UPDATE {self.schema}.load_chunk
            SET
                status = 'completed',
                completed_at = CURRENT_TIMESTAMP,
                rows_read = %s,
                rows_staged = %s,
                rows_inserted = %s,
                rows_updated = %s,
                rows_conflicted = %s,
                last_processed_key = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE chunk_id = %s
            """,
            (
                rows_read, rows_staged, rows_inserted, rows_updated,
                rows_conflicted, last_processed_key, chunk_id,
            ),
        )
        self.conn.commit()

    def fail_chunk(
        self,
        chunk_id: int,
        error_type: str,
        error_message: str,
        rows_read: int = 0,
    ):
        """
        Mark chunk as failed.
        
        Args:
            chunk_id: Chunk ID
            error_type: Type of error
            error_message: Error message
            rows_read: Number of rows read before failure
        """
        cursor = self.conn.cursor()
        cursor.execute(
            f"""
            UPDATE {self.schema}.load_chunk
            SET
                status = 'failed',
                error_type = %s,
                error_message = %s,
                rows_read = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE chunk_id = %s
            """,
            (error_type, error_message, rows_read, chunk_id),
        )
        self.conn.commit()

    def heartbeat(self, chunk_id: int):
        """
        Update chunk heartbeat.

        Args:
            chunk_id: Chunk ID
        """
        cursor = self.conn.cursor()
        cursor.execute(
            f"""
            UPDATE {self.schema}.load_chunk
            SET heartbeat_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE chunk_id = %s
            """,
            (chunk_id,),
        )
        self.conn.commit()

    def update_chunk_progress(
        self,
        chunk_id: int,
        last_processed_key: int,
        rows_read: int,
    ):
        """
        Update chunk progress without changing status.

        Enables resume from last successful key within a chunk.

        Args:
            chunk_id: Chunk ID
            last_processed_key: Last successfully processed key
            rows_read: Number of rows read so far
        """
        cursor = self.conn.cursor()
        cursor.execute(
            f"""
            UPDATE {self.schema}.load_chunk
            SET
                last_processed_key = %s,
                rows_read = %s,
                heartbeat_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE chunk_id = %s
              AND status = 'running'
            """,
            (str(last_processed_key), rows_read, chunk_id),
        )
        self.conn.commit()

    def get_pending_chunks(self, run_id: int) -> List[Chunk]:
        """
        Get all pending chunks for a run.
        
        Args:
            run_id: Run ID
            
        Returns:
            List of pending Chunk objects
        """
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(
            f"""
            SELECT * FROM {self.schema}.load_chunk
            WHERE run_id = %s
              AND status IN ('pending', 'retry', 'failed')
              AND attempt_count < 5
            ORDER BY chunk_no
            """,
            (run_id,),
        )
        return [self._row_to_chunk(row) for row in cursor.fetchall()]

    def get_chunk_stats(self, run_id: int) -> dict:
        """
        Get chunk statistics for a run.
        
        Args:
            run_id: Run ID
            
        Returns:
            Dictionary with chunk statistics
        """
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(
            f"""
            SELECT
                status,
                COUNT(*) as count
            FROM {self.schema}.load_chunk
            WHERE run_id = %s
            GROUP BY status
            """,
            (run_id,),
        )
        
        stats = {
            "pending": 0,
            "running": 0,
            "completed": 0,
            "failed": 0,
            "retry": 0,
            "cancelled": 0,
        }
        
        for row in cursor.fetchall():
            stats[row["status"]] = row["count"]
        
        return stats

    def get_total_stats(self, run_id: int) -> dict:
        """
        Get aggregate statistics for a run.
        
        Args:
            run_id: Run ID
            
        Returns:
            Dictionary with aggregate statistics
        """
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(
            f"""
            SELECT
                COUNT(*) as total_chunks,
                COUNT(*) FILTER (WHERE status = 'completed') as completed,
                COUNT(*) FILTER (WHERE status = 'failed') as failed,
                COALESCE(SUM(rows_read), 0) as rows_read,
                COALESCE(SUM(rows_inserted), 0) as rows_inserted,
                COALESCE(SUM(rows_updated), 0) as rows_updated,
                COALESCE(SUM(rows_conflicted), 0) as rows_conflicted
            FROM {self.schema}.load_chunk
            WHERE run_id = %s
            """,
            (run_id,),
        )
        
        row = cursor.fetchone()
        return dict(row) if row else {}

    def _row_to_chunk(self, row: dict) -> Chunk:
        """Convert database row to Chunk object."""
        return Chunk(
            chunk_id=row["chunk_id"],
            run_id=row["run_id"],
            chunk_no=row["chunk_no"],
            chunk_strategy=row["chunk_strategy"],
            chunk_column=row.get("chunk_column"),
            range_start_text=row.get("range_start_text"),
            range_end_text=row.get("range_end_text"),
            range_start_bigint=row.get("range_start_bigint"),
            range_end_bigint=row.get("range_end_bigint"),
            range_start_ts=row.get("range_start_ts"),
            range_end_ts=row.get("range_end_ts"),
            status=ChunkStatus(row["status"]),
            attempt_count=row.get("attempt_count", 0),
            worker_id=row.get("worker_id"),
            started_at=row.get("started_at"),
            heartbeat_at=row.get("heartbeat_at"),
            completed_at=row.get("completed_at"),
            rows_read=row.get("rows_read", 0),
            rows_staged=row.get("rows_staged", 0),
            rows_inserted=row.get("rows_inserted", 0),
            rows_updated=row.get("rows_updated", 0),
            rows_conflicted=row.get("rows_conflicted", 0),
            last_processed_key=row.get("last_processed_key"),
            error_type=row.get("error_type"),
            error_message=row.get("error_message"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )
