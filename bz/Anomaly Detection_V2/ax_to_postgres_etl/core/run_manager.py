"""Run manager for ETL load operations."""

import hashlib
import json
from datetime import datetime
from typing import Optional, Dict, Any

import psycopg2
import psycopg2.extras

from .models import Run, RunStatus


class RunManager:
    """Manages ETL load runs in PostgreSQL."""

    def __init__(self, conn: psycopg2.extensions.connection, schema: str = "etl"):
        """
        Initialize RunManager.
        
        Args:
            conn: PostgreSQL connection
            schema: Schema name for ETL tables (default: etl)
        """
        self.conn = conn
        self.schema = schema

    def create_run(
        self,
        pipeline_name: str,
        source_system: str,
        source_table: str,
        target_schema: str,
        target_table: str,
        load_mode: str,
        chunk_strategy: str,
        chunk_column: Optional[str] = None,
        source_database: Optional[str] = None,
        source_schema: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> Run:
        """
        Create a new ETL run.
        
        Args:
            pipeline_name: Name of the pipeline
            source_system: Source system identifier
            source_table: Source table name
            target_schema: Target schema name
            target_table: Target table name
            load_mode: Load mode (full, resume, restart, incremental)
            chunk_strategy: Chunking strategy
            chunk_column: Column used for chunking
            source_database: Source database name
            source_schema: Source schema name
            config: Configuration dict for hash calculation
            
        Returns:
            Created Run object
        """
        config_hash = self._compute_config_hash(config) if config else None
        
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(
            f"""
            INSERT INTO {self.schema}.load_run (
                pipeline_name, source_system, source_database, source_schema,
                source_table, target_schema, target_table, load_mode,
                chunk_strategy, chunk_column, status, config_hash
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            RETURNING *
            """,
            (
                pipeline_name, source_system, source_database, source_schema,
                source_table, target_schema, target_table, load_mode,
                chunk_strategy, chunk_column, RunStatus.CREATED.value, config_hash,
            ),
        )
        row = cursor.fetchone()
        self.conn.commit()
        
        return self._row_to_run(row)

    def find_resumable_run(
        self,
        source_table: str,
        target_table: str,
        config: Optional[Dict[str, Any]] = None,
    ) -> Optional[Run]:
        """
        Find the last resumable run for a table.
        
        Args:
            source_table: Source table name
            target_table: Target table name
            config: Current configuration for hash comparison
            
        Returns:
            Run object if found, None otherwise
        """
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(
            f"""
            SELECT * FROM {self.schema}.load_run
            WHERE source_table = %s
              AND target_table = %s
              AND status IN ('running', 'failed', 'completed_with_errors', 'abandoned')
            ORDER BY run_id DESC
            LIMIT 1
            """,
            (source_table, target_table),
        )
        row = cursor.fetchone()
        
        if row is None:
            return None
        
        run = self._row_to_run(row)
        
        # Check config compatibility
        if config and run.config_hash:
            current_hash = self._compute_config_hash(config)
            if current_hash != run.config_hash:
                return None
        
        return run

    def update_run_status(
        self,
        run_id: int,
        status,  # RunStatus enum or string
        error_message: Optional[str] = None,
    ):
        """
        Update run status.
        
        Args:
            run_id: Run ID
            status: New status (RunStatus enum or string)
            error_message: Error message if failed
        """
        # Handle both enum and string
        status_value = status.value if hasattr(status, 'value') else str(status)
        
        cursor = self.conn.cursor()
        cursor.execute(
            f"""
            UPDATE {self.schema}.load_run
            SET status = %s,
                error_message = %s,
                finished_at = CASE 
                    WHEN %s IN ('completed', 'completed_with_errors', 'failed', 'cancelled') 
                    THEN CURRENT_TIMESTAMP 
                    ELSE finished_at 
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE run_id = %s
            """,
            (status_value, error_message, status_value, run_id),
        )
        self.conn.commit()

    def update_run_stats(self, run_id: int):
        """
        Update run statistics from chunk data.
        
        Args:
            run_id: Run ID
        """
        cursor = self.conn.cursor()
        cursor.execute(
            f"""
            UPDATE {self.schema}.load_run r
            SET
                completed_chunks = s.completed_chunks,
                failed_chunks = s.failed_chunks,
                rows_read = s.rows_read,
                rows_inserted = s.rows_inserted,
                rows_updated = s.rows_updated,
                rows_conflicted = s.rows_conflicted,
                heartbeat_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            FROM
            (
                SELECT
                    run_id,
                    COUNT(*) FILTER (WHERE status = 'completed') AS completed_chunks,
                    COUNT(*) FILTER (WHERE status = 'failed') AS failed_chunks,
                    COALESCE(SUM(rows_read), 0) AS rows_read,
                    COALESCE(SUM(rows_inserted), 0) AS rows_inserted,
                    COALESCE(SUM(rows_updated), 0) AS rows_updated,
                    COALESCE(SUM(rows_conflicted), 0) AS rows_conflicted
                FROM {self.schema}.load_chunk
                WHERE run_id = %s
                GROUP BY run_id
            ) s
            WHERE r.run_id = s.run_id
            """,
            (run_id,),
        )
        self.conn.commit()

    def update_run_counts(self, run_id: int, total_chunks: int):
        """
        Update run total chunk count.
        
        Args:
            run_id: Run ID
            total_chunks: Total number of chunks
        """
        cursor = self.conn.cursor()
        cursor.execute(
            f"""
            UPDATE {self.schema}.load_run
            SET total_chunks = %s, updated_at = CURRENT_TIMESTAMP
            WHERE run_id = %s
            """,
            (total_chunks, run_id),
        )
        self.conn.commit()

    def heartbeat(self, run_id: int):
        """
        Update run heartbeat.
        
        Args:
            run_id: Run ID
        """
        cursor = self.conn.cursor()
        cursor.execute(
            f"""
            UPDATE {self.schema}.load_run
            SET heartbeat_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE run_id = %s
            """,
            (run_id,),
        )
        self.conn.commit()

    def acquire_advisory_lock(self, table_name: str) -> bool:
        """
        Try to acquire advisory lock for a table.

        Args:
            table_name: Table name to lock

        Returns:
            True if lock acquired, False otherwise
        """
        # Use stable hash (zlib.crc32) instead of Python's randomized hash()
        import zlib
        lock_key = zlib.crc32(f"etl:{table_name.lower()}".encode("utf-8"))
        cursor = self.conn.cursor()
        cursor.execute("SELECT pg_try_advisory_lock(%s)", (lock_key,))
        result = cursor.fetchone()
        self.conn.commit()
        return result[0] if result else False

    def release_advisory_lock(self, table_name: str):
        """
        Release advisory lock for a table.

        Args:
            table_name: Table name to unlock
        """
        import zlib
        lock_key = zlib.crc32(f"etl:{table_name.lower()}".encode("utf-8"))
        cursor = self.conn.cursor()
        cursor.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))
        self.conn.commit()

    def get_run(self, run_id: int) -> Optional[Run]:
        """
        Get run by ID.
        
        Args:
            run_id: Run ID
            
        Returns:
            Run object if found, None otherwise
        """
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(
            f"SELECT * FROM {self.schema}.load_run WHERE run_id = %s",
            (run_id,),
        )
        row = cursor.fetchone()
        return self._row_to_run(row) if row else None

    def _compute_config_hash(self, config: Dict[str, Any]) -> str:
        """
        Compute hash of configuration for compatibility check.
        
        Args:
            config: Configuration dictionary
            
        Returns:
            SHA256 hash string
        """
        # Sort keys for deterministic hashing
        config_str = json.dumps(config, sort_keys=True, default=str)
        return hashlib.sha256(config_str.encode()).hexdigest()[:16]

    def _row_to_run(self, row: Dict[str, Any]) -> Run:
        """Convert database row to Run object."""
        return Run(
            run_id=row["run_id"],
            pipeline_name=row["pipeline_name"],
            source_system=row["source_system"],
            source_database=row.get("source_database"),
            source_schema=row.get("source_schema"),
            source_table=row["source_table"],
            target_schema=row["target_schema"],
            target_table=row["target_table"],
            load_mode=row["load_mode"],
            chunk_strategy=row["chunk_strategy"],
            chunk_column=row.get("chunk_column"),
            status=RunStatus(row["status"]),
            started_at=row["started_at"],
            finished_at=row.get("finished_at"),
            heartbeat_at=row.get("heartbeat_at"),
            source_row_count=row.get("source_row_count"),
            target_row_count=row.get("target_row_count"),
            total_chunks=row.get("total_chunks", 0),
            completed_chunks=row.get("completed_chunks", 0),
            failed_chunks=row.get("failed_chunks", 0),
            rows_read=row.get("rows_read", 0),
            rows_inserted=row.get("rows_inserted", 0),
            rows_updated=row.get("rows_updated", 0),
            rows_conflicted=row.get("rows_conflicted", 0),
            config_hash=row.get("config_hash"),
            error_message=row.get("error_message"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    def get_previous_run_stats(self, source_table: str) -> Optional[Dict[str, Any]]:
        """
        Get stats from the most recent completed run for comparison.

        Args:
            source_table: Source table name

        Returns:
            Dict with previous run stats or None
        """
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(
            f"""
            SELECT
                run_id, status, started_at, finished_at,
                rows_inserted, rows_conflicted, completed_chunks,
                total_chunks, source_row_count, target_row_count
            FROM {self.schema}.load_run
            WHERE source_table = %s
              AND status IN ('completed', 'completed_with_errors')
            ORDER BY finished_at DESC NULLS LAST
            LIMIT 1
            """,
            (source_table,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None
