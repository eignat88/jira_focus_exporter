"""Incremental load support with change tracking."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Tuple

import psycopg2


@dataclass
class ChangeTracking:
    """Configuration for change tracking."""
    timestamp_column: str = "ModifiedDate"  # Column to track changes
    last_value_column: str = "_etl_last_value"  # Column in target storing last processed value
    tracking_table: str = "etl.change_tracking"  # Table storing tracking state


class IncrementalManager:
    """
    Manage incremental loads by tracking changes.

    Supports:
    - Timestamp-based change detection
    - Watermark tracking per table
    - Delta calculation
    """

    def __init__(
        self,
        conn: psycopg2.extensions.connection,
        schema: str = "etl",
    ):
        self.conn = conn
        self.schema = schema
        self._ensure_tracking_table()

    def _ensure_tracking_table(self):
        """Create change tracking table if not exists."""
        cursor = self.conn.cursor()
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.schema}.change_tracking (
                tracking_id bigserial PRIMARY KEY,
                table_name text NOT NULL,
                last_value text,
                last_load_time timestamp,
                rows_loaded bigint DEFAULT 0,
                created_at timestamp DEFAULT CURRENT_TIMESTAMP,
                updated_at timestamp DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(table_name)
            )
        """)
        self.conn.commit()

    def get_last_value(self, table_name: str) -> Optional[str]:
        """Get last processed value for a table."""
        cursor = self.conn.cursor()
        cursor.execute(f"""
            SELECT last_value FROM {self.schema}.change_tracking
            WHERE table_name = %s
        """, (table_name,))
        row = cursor.fetchone()
        return row[0] if row else None

    def update_tracking(
        self,
        table_name: str,
        last_value: str,
        rows_loaded: int,
    ):
        """Update tracking state after successful load."""
        cursor = self.conn.cursor()
        cursor.execute(f"""
            INSERT INTO {self.schema}.change_tracking
            (table_name, last_value, last_load_time, rows_loaded, updated_at)
            VALUES (%s, %s, CURRENT_TIMESTAMP, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (table_name) DO UPDATE SET
                last_value = EXCLUDED.last_value,
                last_load_time = CURRENT_TIMESTAMP,
                rows_loaded = {self.schema}.change_tracking.rows_loaded + EXCLUDED.rows_loaded,
                updated_at = CURRENT_TIMESTAMP
        """, (table_name, last_value, rows_loaded))
        self.conn.commit()

    def build_incremental_query(
        self,
        table_name: str,
        timestamp_column: str = "ModifiedDate",
        columns: str = "*",
        source_schema: str = "dbo",
    ) -> Tuple[str, Optional[str]]:
        """
        Build SQL query for incremental load.

        Returns:
            (sql_query, last_value)
        """
        last_value = self.get_last_value(table_name)

        if last_value:
            sql = f"""
                SELECT {columns} FROM {source_schema}.{table_name}
                WHERE {timestamp_column} > '{last_value}'
                ORDER BY {timestamp_column}
            """
        else:
            # First load — get everything
            sql = f"""
                SELECT {columns} FROM {source_schema}.{table_name}
                ORDER BY {timestamp_column}
            """

        return sql, last_value

    def get_max_value(
        self,
        table_name: str,
        value_column: str = "ModifiedDate",
        source_schema: str = "dbo",
    ) -> Optional[str]:
        """Get maximum value from source table."""
        import pyodbc
        # This would need SS connection — placeholder
        return None

    def get_pending_tables(self) -> List[str]:
        """Get tables that need incremental update."""
        cursor = self.conn.cursor()
        cursor.execute(f"""
            SELECT table_name FROM {self.schema}.change_tracking
            WHERE last_load_time IS NULL
               OR last_load_time < CURRENT_TIMESTAMP - INTERVAL '1 day'
            ORDER BY last_load_time NULLS FIRST
        """)
        return [row[0] for row in cursor.fetchall()]

    def summary(self) -> str:
        """Generate tracking summary."""
        cursor = self.conn.cursor()
        cursor.execute(f"""
            SELECT table_name, last_value, last_load_time, rows_loaded
            FROM {self.schema}.change_tracking
            ORDER BY table_name
        """)
        rows = cursor.fetchall()

        lines = []
        lines.append(f"{'='*70}")
        lines.append(f"INCREMENTAL TRACKING STATUS")
        lines.append(f"{'='*70}")

        if not rows:
            lines.append("  No tracking data found.")
        else:
            for table_name, last_value, last_load, rows_loaded in rows:
                last_str = last_load.strftime("%Y-%m-%d %H:%M") if last_load else "Never"
                lines.append(
                    f"  {table_name:25s} | last: {last_str} | "
                    f"rows: {rows_loaded or 0:>12,} | value: {(last_value or 'N/A')[:20]}"
                )

        lines.append(f"{'='*70}")
        return "\n".join(lines)
