"""Load balancing for multi-table ETL operations."""

import time
from dataclasses import dataclass
from typing import List, Dict, Optional
from enum import Enum


class TablePriority(Enum):
    CRITICAL = 1
    HIGH = 2
    MEDIUM = 3
    LOW = 4


@dataclass
class TableLoadInfo:
    """Information about a table's load state."""
    table_name: str
    source_rows: int = 0
    target_rows: int = 0
    chunk_count: int = 0
    last_load_time: Optional[float] = None
    priority: TablePriority = TablePriority.MEDIUM
    estimated_duration: float = 0.0
    workers_assigned: int = 0

    @property
    def completion_pct(self) -> float:
        if self.source_rows == 0:
            return 0
        return (self.target_rows / self.source_rows) * 100

    @property
    def rows_remaining(self) -> int:
        return max(0, self.source_rows - self.target_rows)

    @property
    def staleness_hours(self) -> float:
        if self.last_load_time is None:
            return float('inf')
        return (time.time() - self.last_load_time) / 3600


class LoadBalancer:
    """
    Balance load across multiple tables.

    Strategies:
    - Priority-based: Higher priority tables get more workers
    - Staleness-based: Staler tables get priority
    - Size-based: Larger tables get more workers
    - Round-robin: Equal distribution
    """

    def __init__(
        self,
        total_workers: int = 8,
        strategy: str = "priority",
        min_workers_per_table: int = 1,
    ):
        self.total_workers = total_workers
        self.strategy = strategy
        self.min_workers = min_workers_per_table
        self._tables: Dict[str, TableLoadInfo] = {}

    def register_table(self, info: TableLoadInfo):
        """Register a table for balancing."""
        self._tables[info.table_name] = info

    def register_tables(self, infos: List[TableLoadInfo]):
        """Register multiple tables."""
        for info in infos:
            self.register_table(info)

    def calculate_allocation(self) -> Dict[str, int]:
        """
        Calculate worker allocation for each table.

        Returns:
            Dict mapping table_name -> worker_count
        """
        if not self._tables:
            return {}

        tables = list(self._tables.values())

        if self.strategy == "priority":
            return self._allocate_by_priority(tables)
        elif self.strategy == "staleness":
            return self._allocate_by_staleness(tables)
        elif self.strategy == "size":
            return self._allocate_by_size(tables)
        elif self.strategy == "round_robin":
            return self._allocate_round_robin(tables)
        else:
            return self._allocate_equal(tables)

    def _allocate_by_priority(self, tables: List[TableLoadInfo]) -> Dict[str, int]:
        """Allocate by priority (higher priority = more workers)."""
        allocation = {}
        remaining = self.total_workers

        # Sort by priority
        sorted_tables = sorted(tables, key=lambda t: t.priority.value)

        # First pass: assign minimum
        for table in sorted_tables:
            allocation[table.table_name] = self.min_workers
            remaining -= self.min_workers

        # Second pass: distribute remaining by priority weight
        total_weight = sum(5 - t.priority.value for t in sorted_tables)
        if total_weight > 0:
            for table in sorted_tables:
                weight = (5 - table.priority.value) / total_weight
                extra = int(remaining * weight)
                allocation[table.table_name] += extra

        return allocation

    def _allocate_by_staleness(self, tables: List[TableLoadInfo]) -> Dict[str, int]:
        """Allocate by staleness (staler = more workers)."""
        allocation = {}
        remaining = self.total_workers

        # Sort by staleness
        sorted_tables = sorted(tables, key=lambda t: -t.staleness_hours)

        # First pass: assign minimum
        for table in sorted_tables:
            allocation[table.table_name] = self.min_workers
            remaining -= self.min_workers

        # Second pass: distribute by staleness weight
        total_staleness = sum(t.staleness_hours for t in sorted_tables if t.staleness_hours < float('inf'))
        if total_staleness > 0:
            for table in sorted_tables:
                if table.staleness_hours < float('inf'):
                    weight = table.staleness_hours / total_staleness
                    extra = int(remaining * weight)
                    allocation[table.table_name] += extra

        return allocation

    def _allocate_by_size(self, tables: List[TableLoadInfo]) -> Dict[str, int]:
        """Allocate by table size (larger = more workers)."""
        allocation = {}
        remaining = self.total_workers

        # Sort by rows remaining
        sorted_tables = sorted(tables, key=lambda t: -t.rows_remaining)

        # First pass: assign minimum
        for table in sorted_tables:
            allocation[table.table_name] = self.min_workers
            remaining -= self.min_workers

        # Second pass: distribute by size weight
        total_rows = sum(t.rows_remaining for t in sorted_tables)
        if total_rows > 0:
            for table in sorted_tables:
                weight = table.rows_remaining / total_rows
                extra = int(remaining * weight)
                allocation[table.table_name] += extra

        return allocation

    def _allocate_round_robin(self, tables: List[TableLoadInfo]) -> Dict[str, int]:
        """Equal distribution across tables."""
        allocation = {}
        per_table = self.total_workers // len(tables)
        remainder = self.total_workers % len(tables)

        for i, table in enumerate(tables):
            allocation[table.table_name] = per_table + (1 if i < remainder else 0)

        return allocation

    def _allocate_equal(self, tables: List[TableLoadInfo]) -> Dict[str, int]:
        """Equal distribution."""
        return self._allocate_round_robin(tables)

    def summary(self) -> str:
        """Generate allocation summary."""
        allocation = self.calculate_allocation()

        lines = []
        lines.append(f"{'='*70}")
        lines.append(f"LOAD BALANCING ({self.strategy})")
        lines.append(f"{'='*70}")
        lines.append(f"  Total workers: {self.total_workers}")
        lines.append(f"  Tables: {len(self._tables)}")
        lines.append(f"")

        for table_name, workers in sorted(allocation.items(), key=lambda x: -x[1]):
            info = self._tables[table_name]
            lines.append(
                f"  {table_name:25s} | {workers:2d} workers | "
                f"{info.completion_pct:5.1f}% done | "
                f"{info.rows_remaining:>12,} remaining | "
                f"priority={info.priority.name}"
            )

        lines.append(f"{'='*70}")
        return "\n".join(lines)
