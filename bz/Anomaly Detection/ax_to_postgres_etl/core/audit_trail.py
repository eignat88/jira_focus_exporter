"""Audit trail for ETL operations."""

import json
from datetime import datetime
from typing import Optional, Any

import psycopg2


class AuditTrail:
    """
    Records detailed audit log for ETL operations.

    Creates table: etl.audit_log
    """

    def __init__(self, conn: psycopg2.extensions.connection, schema: str = "etl"):
        self.conn = conn
        self.schema = schema
        self._ensure_table()

    def _ensure_table(self):
        """Create audit log table if not exists."""
        cursor = self.conn.cursor()
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.schema}.audit_log (
                audit_id bigserial PRIMARY KEY,
                run_id bigint,
                table_name text,
                action text,
                details jsonb,
                actor text DEFAULT 'etl_pipeline',
                created_at timestamp DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.commit()

    def log(
        self,
        action: str,
        table_name: Optional[str] = None,
        run_id: Optional[int] = None,
        details: Optional[dict] = None,
        actor: str = "etl_pipeline",
    ):
        """
        Record an audit event.

        Args:
            action: Action type (LOAD_START, LOAD_COMPLETE, CHUNK_COMPLETED, etc.)
            table_name: Table being processed
            run_id: ETL run ID
            details: Additional details as dict
            actor: Who performed the action
        """
        cursor = self.conn.cursor()
        cursor.execute(f"""
            INSERT INTO {self.schema}.audit_log
            (run_id, table_name, action, details, actor)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            run_id,
            table_name,
            action,
            json.dumps(details, default=str) if details else None,
            actor,
        ))
        self.conn.commit()

    def log_load_start(
        self,
        table_name: str,
        run_id: int,
        load_mode: str,
        source_schema: str,
        chunk_count: int,
    ):
        """Log load start event."""
        self.log(
            action="LOAD_START",
            table_name=table_name,
            run_id=run_id,
            details={
                "load_mode": load_mode,
                "source_schema": source_schema,
                "chunk_count": chunk_count,
                "started_at": datetime.now().isoformat(),
            },
        )

    def log_load_complete(
        self,
        table_name: str,
        run_id: int,
        status: str,
        rows_inserted: int,
        elapsed: float,
        failed_chunks: int,
    ):
        """Log load complete event."""
        self.log(
            action="LOAD_COMPLETE",
            table_name=table_name,
            run_id=run_id,
            details={
                "status": status,
                "rows_inserted": rows_inserted,
                "elapsed_seconds": round(elapsed, 1),
                "failed_chunks": failed_chunks,
                "finished_at": datetime.now().isoformat(),
            },
        )

    def log_error(
        self,
        table_name: str,
        run_id: Optional[int],
        error_type: str,
        error_message: str,
    ):
        """Log error event."""
        self.log(
            action="ERROR",
            table_name=table_name,
            run_id=run_id,
            details={
                "error_type": error_type,
                "error_message": error_message[:500],
            },
        )

    def get_recent(
        self,
        table_name: Optional[str] = None,
        limit: int = 50,
    ) -> list:
        """Get recent audit entries."""
        cursor = self.conn.cursor()
        if table_name:
            cursor.execute(
                f"SELECT * FROM {self.schema}.audit_log "
                f"WHERE table_name = %s ORDER BY created_at DESC LIMIT %s",
                (table_name, limit),
            )
        else:
            cursor.execute(
                f"SELECT * FROM {self.schema}.audit_log "
                f"ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
        return cursor.fetchall()
