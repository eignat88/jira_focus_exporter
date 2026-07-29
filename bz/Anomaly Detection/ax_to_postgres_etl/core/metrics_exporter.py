"""Metrics export for ETL operations."""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any


@dataclass
class ETLMetrics:
    """Collect and export ETL metrics."""
    table_name: str
    run_id: Optional[int] = None
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None

    # Counters
    rows_fetched: int = 0
    rows_inserted: int = 0
    rows_conflicted: int = 0
    chunks_total: int = 0
    chunks_completed: int = 0
    chunks_failed: int = 0
    chunks_retry: int = 0

    # Performance
    workers: int = 0
    fetch_size: int = 0
    commit_size: int = 0
    stream_threshold: int = 0

    # State
    status: str = "running"
    error_message: Optional[str] = None
    peak_memory_mb: float = 0.0

    # Batch timing
    batch_times: list = field(default_factory=list)
    fetch_times: list = field(default_factory=list)
    write_times: list = field(default_factory=list)

    @property
    def elapsed(self) -> float:
        end = self.end_time or time.time()
        return end - self.start_time

    @property
    def speed(self) -> float:
        return self.rows_inserted / self.elapsed if self.elapsed > 0 else 0

    @property
    def avg_batch_time(self) -> float:
        return sum(self.batch_times) / len(self.batch_times) if self.batch_times else 0

    @property
    def p95_batch_time(self) -> float:
        if not self.batch_times:
            return 0
        sorted_times = sorted(self.batch_times)
        idx = int(len(sorted_times) * 0.95)
        return sorted_times[min(idx, len(sorted_times) - 1)]

    def record_batch(self, elapsed: float, rows: int):
        """Record a batch write."""
        self.batch_times.append(elapsed)

    def record_fetch(self, elapsed: float, rows: int):
        """Record a fetch operation."""
        self.fetch_times.append(elapsed)

    def record_write(self, elapsed: float, rows: int):
        """Record a write operation."""
        self.write_times.append(elapsed)

    def finish(self, status: str = "completed", error: Optional[str] = None):
        """Mark metrics as finished."""
        self.end_time = time.time()
        self.status = status
        self.error_message = error

    def to_dict(self) -> Dict[str, Any]:
        """Export as dictionary."""
        return {
            "timestamp": datetime.now().isoformat(),
            "table": self.table_name,
            "run_id": self.run_id,
            "status": self.status,
            "elapsed_seconds": round(self.elapsed, 1),
            "rows": {
                "fetched": self.rows_fetched,
                "inserted": self.rows_inserted,
                "conflicted": self.rows_conflicted,
            },
            "chunks": {
                "total": self.chunks_total,
                "completed": self.chunks_completed,
                "failed": self.chunks_failed,
                "retry": self.chunks_retry,
            },
            "performance": {
                "speed_rows_per_sec": round(self.speed, 0),
                "avg_batch_time_ms": round(self.avg_batch_time * 1000, 1),
                "p95_batch_time_ms": round(self.p95_batch_time * 1000, 1),
                "total_batches": len(self.batch_times),
            },
            "config": {
                "workers": self.workers,
                "fetch_size": self.fetch_size,
                "commit_size": self.commit_size,
                "stream_threshold": self.stream_threshold,
            },
            "memory": {
                "peak_mb": round(self.peak_memory_mb, 1),
            },
        }

    def to_prometheus(self) -> str:
        """Export as Prometheus metrics format."""
        lines = []
        labels = f'table="{self.table_name}",status="{self.status}"'

        lines.append(f'etl_rows_inserted{{{labels}}} {self.rows_inserted}')
        lines.append(f'etl_rows_fetched{{{labels}}} {self.rows_fetched}')
        lines.append(f'etl_rows_conflicted{{{labels}}} {self.rows_conflicted}')
        lines.append(f'etl_chunks_completed{{{labels}}} {self.chunks_completed}')
        lines.append(f'etl_chunks_failed{{{labels}}} {self.chunks_failed}')
        lines.append(f'etl_elapsed_seconds{{{labels}}} {self.elapsed:.1f}')
        lines.append(f'etl_speed_rows_per_sec{{{labels}}} {self.speed:.0f}')
        lines.append(f'etl_peak_memory_mb{{{labels}}} {self.peak_memory_mb:.1f}')
        lines.append(f'etl_avg_batch_time_ms{{{labels}}} {self.avg_batch_time * 1000:.1f}')
        lines.append(f'etl_p95_batch_time_ms{{{labels}}} {self.p95_batch_time * 1000:.1f}')

        return "\n".join(lines)


class MetricsExporter:
    """Export metrics to various formats."""

    def __init__(self, output_dir: str = "metrics"):
        self.output_dir = output_dir

    def export_json(self, metrics: ETLMetrics, filename: Optional[str] = None) -> str:
        """Export metrics to JSON file."""
        import os
        os.makedirs(self.output_dir, exist_ok=True)

        if not filename:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"metrics_{metrics.table_name}_{ts}.json"

        filepath = os.path.join(self.output_dir, filename)
        with open(filepath, "w") as f:
            json.dump(metrics.to_dict(), f, indent=2)

        return filepath

    def export_prometheus(self, metrics: ETLMetrics, filename: Optional[str] = None) -> str:
        """Export metrics in Prometheus format."""
        import os
        os.makedirs(self.output_dir, exist_ok=True)

        if not filename:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"metrics_{metrics.table_name}_{ts}.prom"

        filepath = os.path.join(self.output_dir, filename)
        with open(filepath, "w") as f:
            f.write(metrics.to_prometheus())

        return filepath

    def export_csv_header(self) -> str:
        """CSV header for batch timing."""
        return "table,run_id,timestamp,batch_num,elapsed_ms,rows"

    def export_csv_row(self, metrics: ETLMetrics, batch_num: int, elapsed: float, rows: int) -> str:
        """CSV row for batch timing."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f"{metrics.table_name},{metrics.run_id or ''},{ts},{batch_num},{elapsed*1000:.1f},{rows}"
