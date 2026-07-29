"""Schema migration tracker for ETL tables."""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Dict, Any

import psycopg2


@dataclass
class SchemaVersion:
    """Schema version for a table."""
    table_name: str
    version: int
    columns: List[str]
    column_types: Dict[str, str]
    created_at: datetime
    notes: Optional[str] = None


class SchemaTracker:
    """
    Track schema changes over time for ETL tables.

    Features:
    - Record schema versions
    - Detect schema drift
    - Compare source vs target schemas
    """

    def __init__(self, conn: psycopg2.extensions.connection, schema: str = "etl"):
        self.conn = conn
        self.schema = schema
        self._ensure_table()

    def _ensure_table(self):
        """Create schema tracking table if not exists."""
        cursor = self.conn.cursor()
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.schema}.schema_versions (
                version_id bigserial PRIMARY KEY,
                table_name text NOT NULL,
                version int NOT NULL,
                columns jsonb NOT NULL,
                column_types jsonb NOT NULL,
                created_at timestamp DEFAULT CURRENT_TIMESTAMP,
                notes text,
                UNIQUE(table_name, version)
            )
        """)
        self.conn.commit()

    def record_version(
        self,
        table_name: str,
        columns: List[str],
        column_types: Dict[str, str],
        notes: Optional[str] = None,
    ) -> int:
        """
        Record a new schema version.

        Returns:
            Version number
        """
        import json

        # Get next version
        cursor = self.conn.cursor()
        cursor.execute(f"""
            SELECT COALESCE(MAX(version), 0) + 1
            FROM {self.schema}.schema_versions
            WHERE table_name = %s
        """, (table_name,))
        next_version = cursor.fetchone()[0]

        # Record
        cursor.execute(f"""
            INSERT INTO {self.schema}.schema_versions
            (table_name, version, columns, column_types, notes)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            table_name,
            next_version,
            json.dumps(columns),
            json.dumps(column_types),
            notes,
        ))
        self.conn.commit()

        return next_version

    def get_latest_version(self, table_name: str) -> Optional[SchemaVersion]:
        """Get latest schema version for a table."""
        import json

        cursor = self.conn.cursor()
        cursor.execute(f"""
            SELECT * FROM {self.schema}.schema_versions
            WHERE table_name = %s
            ORDER BY version DESC
            LIMIT 1
        """, (table_name,))
        row = cursor.fetchone()

        if not row:
            return None

        return SchemaVersion(
            table_name=row[1],
            version=row[2],
            columns=json.loads(row[3]),
            column_types=json.loads(row[4]),
            created_at=row[5],
            notes=row[6],
        )

    def detect_drift(
        self,
        table_name: str,
        current_columns: List[str],
        current_types: Dict[str, str],
    ) -> Optional[Dict[str, Any]]:
        """
        Detect schema drift from latest recorded version.

        Returns:
            Dict with drift details or None if no drift
        """
        import json

        latest = self.get_latest_version(table_name)
        if not latest:
            return {
                "drift_type": "new_table",
                "message": f"No schema recorded for {table_name}",
            }

        drift = {
            "drift_type": "none",
            "added_columns": [],
            "removed_columns": [],
            "type_changes": [],
        }

        # Check added columns
        old_cols = set(latest.columns)
        new_cols = set(current_columns)
        added = new_cols - old_cols
        removed = old_cols - new_cols

        if added:
            drift["drift_type"] = "columns_added"
            drift["added_columns"] = list(added)

        if removed:
            drift["drift_type"] = "columns_removed"
            drift["removed_columns"] = list(removed)

        # Check type changes
        for col in old_cols & new_cols:
            old_type = latest.column_types.get(col, "")
            new_type = current_types.get(col, "")
            if old_type != new_type:
                drift["type_changes"].append({
                    "column": col,
                    "old_type": old_type,
                    "new_type": new_type,
                })

        if drift["type_changes"]:
            drift["drift_type"] = "type_changes"

        return drift if drift["drift_type"] != "none" else None

    def get_history(self, table_name: Optional[str] = None) -> List[SchemaVersion]:
        """Get schema version history."""
        import json

        cursor = self.conn.cursor()
        if table_name:
            cursor.execute(f"""
                SELECT * FROM {self.schema}.schema_versions
                WHERE table_name = %s
                ORDER BY version DESC
            """, (table_name,))
        else:
            cursor.execute(f"""
                SELECT * FROM {self.schema}.schema_versions
                ORDER BY table_name, version DESC
            """)

        results = []
        for row in cursor.fetchall():
            results.append(SchemaVersion(
                table_name=row[1],
                version=row[2],
                columns=json.loads(row[3]),
                column_types=json.loads(row[4]),
                created_at=row[5],
                notes=row[6],
            ))

        return results

    def summary(self, table_name: Optional[str] = None) -> str:
        """Generate tracking summary."""
        history = self.get_history(table_name)

        lines = []
        lines.append(f"{'='*70}")
        lines.append(f"SCHEMA TRACKING")
        lines.append(f"{'='*70}")

        if not history:
            lines.append("  No schema versions recorded.")
        else:
            current_table = None
            for v in history:
                if v.table_name != current_table:
                    current_table = v.table_name
                    lines.append(f"\n  Table: {v.table_name}")
                    lines.append(f"  {'Version':<10s} | {'Columns':<10s} | {'Date':<20s} | Notes")
                    lines.append(f"  {'-'*10}-+-{'-'*10}-+-{'-'*20}-+-{'-'*20}")

                lines.append(
                    f"  {v.version:<10d} | {len(v.columns):<10d} | "
                    f"{v.created_at.strftime('%Y-%m-%d %H:%M'):<20s} | {v.notes or ''}"
                )

        lines.append(f"\n{'='*70}")
        return "\n".join(lines)
