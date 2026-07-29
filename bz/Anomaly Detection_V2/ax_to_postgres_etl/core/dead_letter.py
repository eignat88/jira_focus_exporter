"""Dead letter queue for failed rows during ETL load."""

import json
from datetime import datetime
from typing import List, Optional

import psycopg2


class DeadLetterQueue:
    """
    Stores rows that failed during COPY/INSERT for later inspection.

    Creates a table: etl.dead_letter_{table_name}
    """

    def __init__(self, conn: psycopg2.extensions.connection, schema: str = "etl"):
        self.conn = conn
        self.schema = schema

    def ensure_table(self, table_name: str):
        """Create dead letter table if not exists."""
        dl_table = f"{self.schema}.dead_letter_{table_name.lower()}"
        cursor = self.conn.cursor()
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {dl_table} (
                dl_id bigserial PRIMARY KEY,
                run_id bigint,
                chunk_id bigint,
                chunk_no int,
                error_type text,
                error_message text,
                row_data jsonb,
                created_at timestamp DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.commit()

    def insert_failed_rows(
        self,
        table_name: str,
        run_id: Optional[int],
        chunk_id: int,
        chunk_no: int,
        error_type: str,
        error_message: str,
        rows: list,
    ):
        """
        Insert failed rows into dead letter queue.

        Args:
            table_name: Source table name
            run_id: ETL run ID
            chunk_id: Chunk ID
            chunk_no: Chunk number
            error_type: Type of error
            error_message: Error description
            rows: List of failed rows (as lists)
        """
        if not rows:
            return

        dl_table = f"{self.schema}.dead_letter_{table_name.lower()}"
        self.ensure_table(table_name)

        cursor = self.conn.cursor()
        for row in rows:
            cursor.execute(f"""
                INSERT INTO {dl_table}
                (run_id, chunk_id, chunk_no, error_type, error_message, row_data)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                run_id,
                chunk_id,
                chunk_no,
                error_type,
                error_message,
                json.dumps(row, default=str),
            ))
        self.conn.commit()

    def get_failed_count(self, table_name: str, run_id: Optional[int] = None) -> int:
        """Get count of failed rows."""
        dl_table = f"{self.schema}.dead_letter_{table_name.lower()}"
        cursor = self.conn.cursor()
        if run_id:
            cursor.execute(f"SELECT COUNT(*) FROM {dl_table} WHERE run_id = %s", (run_id,))
        else:
            cursor.execute(f"SELECT COUNT(*) FROM {dl_table}")
        return cursor.fetchone()[0]

    def get_failed_rows(
        self,
        table_name: str,
        run_id: Optional[int] = None,
        limit: int = 100,
    ) -> list:
        """Get failed rows for inspection."""
        dl_table = f"{self.schema}.dead_letter_{table_name.lower()}"
        cursor = self.conn.cursor()
        if run_id:
            cursor.execute(
                f"SELECT * FROM {dl_table} WHERE run_id = %s ORDER BY dl_id LIMIT %s",
                (run_id, limit),
            )
        else:
            cursor.execute(
                f"SELECT * FROM {dl_table} ORDER BY dl_id LIMIT %s",
                (limit,),
            )
        return cursor.fetchall()
