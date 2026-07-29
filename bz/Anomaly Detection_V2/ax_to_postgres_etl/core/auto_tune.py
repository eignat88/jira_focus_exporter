"""Auto-tune ETL parameters based on performance metrics."""

from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class TuneResult:
    """Result of auto-tuning."""
    parameter: str
    old_value: Any
    new_value: Any
    reason: str


class AutoTuner:
    """
    Auto-tune ETL parameters based on observed performance.

    Heuristics:
    - If write speed is low → increase commit_size
    - If memory usage is high → decrease commit_size
    - If worker idle time is high → decrease workers
    - If queue is always full → increase workers
    - If fetch speed is low → increase fetch_size
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._results: list[TuneResult] = []

    def analyze(
        self,
        speed_rows_per_sec: float,
        avg_batch_time_ms: float,
        memory_usage_mb: float,
        workers_active_pct: float,
        queue_full_pct: float,
    ) -> list[TuneResult]:
        """
        Analyze metrics and suggest parameter changes.

        Args:
            speed_rows_per_sec: Overall insert speed
            avg_batch_time_ms: Average batch write time
            memory_usage_mb: Peak memory usage
            workers_active_pct: % of time workers were active (vs idle)
            queue_full_pct: % of time write queue was full

        Returns:
            List of suggested changes
        """
        self._results = []

        parallel = self.config.get("etl", {}).get("parallel", {})
        current_workers = parallel.get("workers", 4)
        current_fetch_size = parallel.get("fetch_size", 5000)
        current_commit_size = parallel.get("commit_size", 50000)

        # 1. Worker count optimization
        if workers_active_pct < 30:
            # Workers mostly idle → reduce count
            new_workers = max(1, current_workers - 1)
            if new_workers != current_workers:
                self._results.append(TuneResult(
                    parameter="parallel.workers",
                    old_value=current_workers,
                    new_value=new_workers,
                    reason=f"Workers idle {100-workers_active_pct:.0f}% of time (threshold: 70% active)",
                ))
        elif queue_full_pct > 50 and workers_active_pct > 80:
            # Queue often full and workers busy → increase count
            new_workers = min(16, current_workers + 2)
            if new_workers != current_workers:
                self._results.append(TuneResult(
                    parameter="parallel.workers",
                    old_value=current_workers,
                    new_value=new_workers,
                    reason=f"Queue full {queue_full_pct:.0f}% of time, workers {workers_active_pct:.0f}% active",
                ))

        # 2. Commit size optimization
        if memory_usage_mb > 500:
            # High memory → reduce commit size
            new_commit = max(10000, current_commit_size // 2)
            if new_commit != current_commit_size:
                self._results.append(TuneResult(
                    parameter="parallel.commit_size",
                    old_value=current_commit_size,
                    new_value=new_commit,
                    reason=f"Memory usage {memory_usage_mb:.0f}MB (threshold: 500MB)",
                ))
        elif avg_batch_time_ms > 1000 and memory_usage_mb < 200:
            # Slow writes but low memory → increase commit size
            new_commit = min(200000, current_commit_size * 2)
            if new_commit != current_commit_size:
                self._results.append(TuneResult(
                    parameter="parallel.commit_size",
                    old_value=current_commit_size,
                    new_value=new_commit,
                    reason=f"Batch time {avg_batch_time_ms:.0f}ms, memory {memory_usage_mb:.0f}MB",
                ))

        # 3. Fetch size optimization
        if speed_rows_per_sec < 10000:
            # Slow speed → try larger fetch
            new_fetch = min(20000, current_fetch_size * 2)
            if new_fetch != current_fetch_size:
                self._results.append(TuneResult(
                    parameter="parallel.fetch_size",
                    old_value=current_fetch_size,
                    new_value=new_fetch,
                    reason=f"Speed {speed_rows_per_sec:,.0f} rows/s (threshold: 10,000)",
                ))

        return self._results

    def apply(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Apply tuning results to config."""
        for result in self._results:
            parts = result.parameter.split(".")
            section = config
            for part in parts[:-1]:
                section = section.setdefault(part, {})
            section[parts[-1]] = result.new_value
        return config

    def summary(self) -> str:
        """Generate tuning summary."""
        if not self._results:
            return "No tuning changes recommended."

        lines = []
        lines.append(f"{'='*60}")
        lines.append(f"AUTO-TUNE RESULTS")
        lines.append(f"{'='*60}")

        for r in self._results:
            lines.append(f"  {r.parameter}:")
            lines.append(f"    Old: {r.old_value}")
            lines.append(f"    New: {r.new_value}")
            lines.append(f"    Reason: {r.reason}")

        lines.append(f"{'='*60}")
        return "\n".join(lines)
