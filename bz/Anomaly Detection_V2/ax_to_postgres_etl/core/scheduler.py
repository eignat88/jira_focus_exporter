"""Load scheduler for ETL operations."""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Callable


@dataclass
class ScheduledLoad:
    """A scheduled ETL load task."""
    table_name: str
    load_mode: str  # full, resume, reload, incremental
    enabled: bool = True
    cron: Optional[str] = None  # e.g. "0 2 * * *" for daily at 2am
    priority: int = 0  # Higher = runs first
    max_retries: int = 3
    timeout_minutes: int = 120
    depends_on: List[str] = field(default_factory=list)  # Other table names
    last_run: Optional[str] = None
    last_status: Optional[str] = None
    run_count: int = 0


class LoadScheduler:
    """
    Schedule and manage ETL load tasks.

    Supports:
    - Priority-based execution order
    - Dependency resolution
    - Retry on failure
    - Persistent state (JSON file)
    """

    def __init__(self, state_file: str = "scheduler_state.json"):
        self.state_file = state_file
        self._schedules: dict[str, ScheduledLoad] = {}
        self._load_state()

    def _load_state(self):
        """Load scheduler state from file."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    data = json.load(f)
                for name, info in data.items():
                    self._schedules[name] = ScheduledLoad(**info)
            except Exception:
                pass

    def _save_state(self):
        """Save scheduler state to file."""
        data = {}
        for name, sched in self._schedules.items():
            data[name] = {
                "table_name": sched.table_name,
                "load_mode": sched.load_mode,
                "enabled": sched.enabled,
                "cron": sched.cron,
                "priority": sched.priority,
                "max_retries": sched.max_retries,
                "timeout_minutes": sched.timeout_minutes,
                "depends_on": sched.depends_on,
                "last_run": sched.last_run,
                "last_status": sched.last_status,
                "run_count": sched.run_count,
            }
        with open(self.state_file, "w") as f:
            json.dump(data, f, indent=2)

    def add_schedule(self, schedule: ScheduledLoad):
        """Add or update a schedule."""
        self._schedules[schedule.table_name] = schedule
        self._save_state()

    def remove_schedule(self, table_name: str):
        """Remove a schedule."""
        self._schedules.pop(table_name, None)
        self._save_state()

    def get_schedule(self, table_name: str) -> Optional[ScheduledLoad]:
        """Get schedule for a table."""
        return self._schedules.get(table_name)

    def get_pending(self) -> List[ScheduledLoad]:
        """Get schedules that need to run (by priority)."""
        pending = [
            s for s in self._schedules.values()
            if s.enabled
        ]
        return sorted(pending, key=lambda s: -s.priority)

    def get_execution_order(self) -> List[ScheduledLoad]:
        """
        Get execution order respecting dependencies.

        Returns schedules topologically sorted by dependencies.
        """
        pending = self.get_pending()
        if not pending:
            return []

        # Build dependency graph
        order = []
        remaining = {s.table_name: s for s in pending}
        completed = set()

        max_iterations = len(remaining) + 1
        for _ in range(max_iterations):
            if not remaining:
                break

            progress = False
            for name, sched in list(remaining.items()):
                deps_met = all(d in completed for d in sched.depends_on)
                if deps_met:
                    order.append(sched)
                    completed.add(name)
                    del remaining[name]
                    progress = True

            if not progress:
                # Circular dependency — add remaining in priority order
                for sched in sorted(remaining.values(), key=lambda s: -s.priority):
                    order.append(sched)
                break

        return order

    def mark_completed(self, table_name: str, status: str = "completed"):
        """Mark a schedule as completed."""
        if table_name in self._schedules:
            sched = self._schedules[table_name]
            sched.last_run = datetime.now().isoformat()
            sched.last_status = status
            sched.run_count += 1
            self._save_state()

    def mark_failed(self, table_name: str, error: str):
        """Mark a schedule as failed."""
        if table_name in self._schedules:
            sched = self._schedules[table_name]
            sched.last_run = datetime.now().isoformat()
            sched.last_status = f"failed: {error}"
            self._save_state()

    def summary(self) -> str:
        """Generate summary of all schedules."""
        lines = []
        lines.append(f"{'='*60}")
        lines.append(f"LOAD SCHEDULES")
        lines.append(f"{'='*60}")

        order = self.get_execution_order()
        for i, sched in enumerate(order, 1):
            status_icon = "✓" if sched.last_status == "completed" else "✗" if sched.last_status and "failed" in sched.last_status else "○"
            lines.append(
                f"  {i}. [{status_icon}] {sched.table_name:20s} | "
                f"mode={sched.load_mode:12s} | priority={sched.priority} | "
                f"runs={sched.run_count}"
            )
            if sched.depends_on:
                lines.append(f"     depends on: {', '.join(sched.depends_on)}")

        lines.append(f"{'='*60}")
        return "\n".join(lines)
