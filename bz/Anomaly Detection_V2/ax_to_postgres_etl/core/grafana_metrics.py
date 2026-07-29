"""Grafana-compatible metrics for ETL monitoring."""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime


@dataclass
class MetricPoint:
    """A single metric data point."""
    name: str
    value: float
    labels: Dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class GrafanaMetrics:
    """
    Collect metrics in Prometheus format for Grafana dashboards.

    Exports:
    - etl_load_duration_seconds
    - etl_rows_inserted_total
    - etl_chunks_completed_total
    - etl_chunks_failed_total
    - etl_speed_rows_per_second
    - etl_memory_usage_bytes
    - etl_active_workers
    """

    def __init__(self):
        self._metrics: List[MetricPoint] = []
        self._counters: Dict[str, float] = {}
        self._gauges: Dict[str, float] = {}
        self._histograms: Dict[str, List[float]] = {}

    def counter(self, name: str, value: float, labels: Optional[Dict] = None):
        """Increment a counter metric."""
        key = f"{name}:{labels}"
        self._counters[key] = self._counters.get(key, 0) + value
        self._metrics.append(MetricPoint(name, value, labels or {}))

    def gauge(self, name: str, value: float, labels: Optional[Dict] = None):
        """Set a gauge metric."""
        key = f"{name}:{labels}"
        self._gauges[key] = value
        self._metrics.append(MetricPoint(name, value, labels or {}))

    def histogram(self, name: str, value: float, labels: Optional[Dict] = None):
        """Record a histogram value."""
        key = f"{name}:{labels}"
        if key not in self._histograms:
            self._histograms[key] = []
        self._histograms[key].append(value)
        self._metrics.append(MetricPoint(name, value, labels or {}))

    def record_load_start(self, table: str, mode: str, workers: int):
        """Record load start."""
        labels = {"table": table, "mode": mode}
        self.gauge("etl_load_active", 1, labels)
        self.gauge("etl_workers_active", workers, labels)

    def record_load_complete(
        self,
        table: str,
        status: str,
        duration: float,
        rows: int,
        chunks: int,
        failed: int,
    ):
        """Record load completion."""
        labels = {"table": table, "status": status}
        self.counter("etl_load_duration_seconds", duration, labels)
        self.counter("etl_rows_inserted_total", rows, labels)
        self.counter("etl_chunks_completed_total", chunks, labels)
        self.counter("etl_chunks_failed_total", failed, labels)
        self.gauge("etl_load_active", 0, {"table": table})

        if duration > 0:
            speed = rows / duration
            self.gauge("etl_speed_rows_per_second", speed, labels)

    def record_batch(self, table: str, rows: int, duration: float):
        """Record batch metrics."""
        labels = {"table": table}
        self.histogram("etl_batch_duration_seconds", duration, labels)
        self.histogram("etl_batch_rows", rows, labels)

    def record_memory(self, table: str, memory_mb: float):
        """Record memory usage."""
        labels = {"table": table}
        self.gauge("etl_memory_usage_bytes", memory_mb * 1024 * 1024, labels)

    def to_prometheus(self) -> str:
        """Export metrics in Prometheus format."""
        lines = []
        lines.append("# ETL Pipeline Metrics")
        lines.append("")

        # Counters
        for key, value in self._counters.items():
            name, labels_str = key.split(":", 1)
            labels = eval(labels_str) if labels_str != "{}" else {}
            label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
            lines.append(f"{name}{{{label_str}}} {value}")

        # Gauges
        for key, value in self._gauges.items():
            name, labels_str = key.split(":", 1)
            labels = eval(labels_str) if labels_str != "{}" else {}
            label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
            lines.append(f"{name}{{{label_str}}} {value}")

        # Histograms
        for key, values in self._histograms.items():
            name, labels_str = key.split(":", 1)
            labels = eval(labels_str) if labels_str != "{}" else {}
            label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
            if values:
                lines.append(f"{name}_sum{{{label_str}}} {sum(values)}")
                lines.append(f"{name}_count{{{label_str}}} {len(values)}")
                lines.append(f"{name}_bucket{{le=\"+Inf\",{label_str}}} {len(values)}")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Export metrics as dictionary."""
        return {
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "histograms": {
                k: {
                    "count": len(v),
                    "sum": sum(v),
                    "avg": sum(v) / len(v) if v else 0,
                    "min": min(v) if v else 0,
                    "max": max(v) if v else 0,
                }
                for k, v in self._histograms.items()
            },
        }

    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = []
        lines.append(f"{'='*60}")
        lines.append(f"GRAFANA METRICS SUMMARY")
        lines.append(f"{'='*60}")

        for key, value in self._gauges.items():
            name, labels_str = key.split(":", 1)
            lines.append(f"  {name}: {value:.1f}")

        for key, value in self._counters.items():
            name, labels_str = key.split(":", 1)
            lines.append(f"  {name}: {value:,.0f}")

        lines.append(f"{'='*60}")
        return "\n".join(lines)
