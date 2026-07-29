"""Data lineage tracking for ETL operations."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional


@dataclass
class LineageRecord:
    """A single lineage record."""
    source_table: str
    target_table: str
    operation: str  # load, transform, merge
    run_id: Optional[int] = None
    timestamp: datetime = field(default_factory=datetime.now)
    rows_affected: int = 0
    columns_used: List[str] = field(default_factory=list)
    transformation: Optional[str] = None
    notes: Optional[str] = None


class DataLineage:
    """
    Track data lineage through ETL pipeline.

    Records:
    - Source → Target mappings
    - Transformations applied
    - Column usage
    - Execution history
    """

    def __init__(self):
        self._records: List[LineageRecord] = []
        self._mappings: Dict[str, List[str]] = {}  # source -> [targets]

    def record(
        self,
        source_table: str,
        target_table: str,
        operation: str,
        run_id: Optional[int] = None,
        rows_affected: int = 0,
        columns_used: Optional[List[str]] = None,
        transformation: Optional[str] = None,
        notes: Optional[str] = None,
    ):
        """Record a lineage event."""
        record = LineageRecord(
            source_table=source_table,
            target_table=target_table,
            operation=operation,
            run_id=run_id,
            rows_affected=rows_affected,
            columns_used=columns_used or [],
            transformation=transformation,
            notes=notes,
        )
        self._records.append(record)

        # Update mappings
        if source_table not in self._mappings:
            self._mappings[source_table] = []
        if target_table not in self._mappings[source_table]:
            self._mappings[source_table].append(target_table)

    def get_upstream(self, table: str) -> List[str]:
        """Get all upstream tables for a table."""
        upstream = set()
        for record in self._records:
            if record.target_table == table:
                upstream.add(record.source_table)
                upstream.update(self.get_upstream(record.source_table))
        return list(upstream)

    def get_downstream(self, table: str) -> List[str]:
        """Get all downstream tables for a table."""
        downstream = set()
        for record in self._records:
            if record.source_table == table:
                downstream.add(record.target_table)
                downstream.update(self.get_downstream(record.target_table))
        return list(downstream)

    def get_table_history(self, table: str) -> List[LineageRecord]:
        """Get lineage history for a table."""
        return [
            r for r in self._records
            if r.source_table == table or r.target_table == table
        ]

    def to_dict(self) -> dict:
        """Export as dictionary."""
        return {
            "total_records": len(self._records),
            "tables": list(set(
                [r.source_table for r in self._records] +
                [r.target_table for r in self._records]
            )),
            "mappings": self._mappings,
            "records": [
                {
                    "source": r.source_table,
                    "target": r.target_table,
                    "operation": r.operation,
                    "timestamp": r.timestamp.isoformat(),
                    "rows": r.rows_affected,
                }
                for r in self._records
            ],
        }

    def summary(self) -> str:
        """Generate lineage summary."""
        tables = set()
        for r in self._records:
            tables.add(r.source_table)
            tables.add(r.target_table)

        lines = []
        lines.append(f"{'='*60}")
        lines.append(f"DATA LINEAGE")
        lines.append(f"{'='*60}")
        lines.append(f"  Total records: {len(self._records)}")
        lines.append(f"  Tables tracked: {len(tables)}")
        lines.append(f"")

        for table in sorted(tables):
            upstream = self.get_upstream(table)
            downstream = self.get_downstream(table)
            lines.append(f"  {table}:")
            if upstream:
                lines.append(f"    Upstream:   {', '.join(upstream)}")
            if downstream:
                lines.append(f"    Downstream: {', '.join(downstream)}")

        lines.append(f"{'='*60}")
        return "\n".join(lines)
