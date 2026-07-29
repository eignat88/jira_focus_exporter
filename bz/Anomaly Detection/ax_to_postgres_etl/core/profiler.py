"""Performance profiler for ETL operations."""

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PhaseMetrics:
    """Metrics for a single phase."""
    name: str
    start_time: float = 0.0
    end_time: float = 0.0
    rows_processed: int = 0
    details: dict = field(default_factory=dict)

    @property
    def elapsed(self) -> float:
        return self.end_time - self.start_time if self.end_time > 0 else 0

    @property
    def speed(self) -> float:
        return self.rows_processed / self.elapsed if self.elapsed > 0 else 0


class ETLProfiler:
    """
    Track timing and metrics for ETL phases.

    Usage:
        profiler = ETLProfiler()
        profiler.start_phase("fetch")
        # ... do work ...
        profiler.end_phase("fetch", rows_processed=100000)
        profiler.start_phase("write")
        # ... do work ...
        profiler.end_phase("write", rows_processed=100000)
        print(profiler.summary())
    """

    def __init__(self):
        self._phases: dict[str, PhaseMetrics] = {}
        self._global_start = time.time()
        self._global_end: Optional[float] = None

    def start_phase(self, name: str):
        """Start timing a phase."""
        self._phases[name] = PhaseMetrics(
            name=name,
            start_time=time.time(),
        )

    def end_phase(self, name: str, rows_processed: int = 0, **details):
        """End timing a phase."""
        if name in self._phases:
            phase = self._phases[name]
            phase.end_time = time.time()
            phase.rows_processed = rows_processed
            phase.details.update(details)

    def finish(self):
        """Mark global end time."""
        self._global_end = time.time()

    @property
    def total_elapsed(self) -> float:
        end = self._global_end or time.time()
        return end - self._global_start

    def get_phase(self, name: str) -> Optional[PhaseMetrics]:
        """Get metrics for a specific phase."""
        return self._phases.get(name)

    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = []
        lines.append(f"{'='*60}")
        lines.append(f"PERFORMANCE SUMMARY")
        lines.append(f"{'='*60}")

        for name, phase in self._phases.items():
            speed = phase.speed
            lines.append(
                f"  {name:20s} | {phase.elapsed:8.1f}s | "
                f"{phase.rows_processed:>12,} rows | "
                f"{speed:>10,.0f} rows/s"
            )

        lines.append(f"{'='*60}")
        lines.append(f"  {'TOTAL':20s} | {self.total_elapsed:8.1f}s")

        if self.total_elapsed > 0:
            total_rows = sum(p.rows_processed for p in self._phases.values())
            avg_speed = total_rows / self.total_elapsed
            lines.append(f"  {'AVG SPEED':20s} | {avg_speed:>10,.0f} rows/s")

        lines.append(f"{'='*60}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Export metrics as dict."""
        return {
            "total_elapsed_seconds": round(self.total_elapsed, 1),
            "phases": {
                name: {
                    "elapsed_seconds": round(phase.elapsed, 1),
                    "rows_processed": phase.rows_processed,
                    "speed_rows_per_sec": round(phase.speed, 0),
                    "details": phase.details,
                }
                for name, phase in self._phases.items()
            },
        }
