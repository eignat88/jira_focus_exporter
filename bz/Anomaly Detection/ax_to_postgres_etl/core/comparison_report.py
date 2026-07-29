"""Load comparison report generator."""

from dataclasses import dataclass
from typing import Optional, Dict, Any
from datetime import datetime


@dataclass
class RunComparison:
    """Comparison between two ETL runs."""
    table_name: str
    current_run_id: int
    previous_run_id: Optional[int]

    # Current run metrics
    current_inserted: int = 0
    current_elapsed: float = 0
    current_chunks: int = 0
    current_failed: int = 0
    current_speed: float = 0

    # Previous run metrics
    previous_inserted: int = 0
    previous_elapsed: float = 0
    previous_chunks: int = 0
    previous_failed: int = 0
    previous_speed: float = 0

    @property
    def inserted_diff(self) -> int:
        return self.current_inserted - self.previous_inserted

    @property
    def inserted_diff_pct(self) -> float:
        if self.previous_inserted == 0:
            return 0
        return (self.inserted_diff / self.previous_inserted) * 100

    @property
    def speed_diff_pct(self) -> float:
        if self.previous_speed == 0:
            return 0
        return ((self.current_speed - self.previous_speed) / self.previous_speed) * 100

    @property
    def elapsed_diff_pct(self) -> float:
        if self.previous_elapsed == 0:
            return 0
        return ((self.current_elapsed - self.previous_elapsed) / self.previous_elapsed) * 100

    def summary(self) -> str:
        """Generate comparison summary."""
        lines = []
        lines.append(f"{'='*70}")
        lines.append(f"LOAD COMPARISON: {self.table_name}")
        lines.append(f"{'='*70}")

        lines.append(f"  {'Metric':<25s} | {'Current':>15s} | {'Previous':>15s} | {'Diff':>12s}")
        lines.append(f"  {'-'*25}-+-{'-'*15}-+-{'-'*15}-+-{'-'*12}")

        # Rows inserted
        diff_str = f"{self.inserted_diff:+,}"
        pct_str = f"({self.inserted_diff_pct:+.1f}%)"
        lines.append(
            f"  {'Rows inserted':<25s} | {self.current_inserted:>15,} | "
            f"{self.previous_inserted:>15,} | {diff_str} {pct_str}"
        )

        # Duration
        diff_str = f"{self.current_elapsed - self.previous_elapsed:+.1f}s"
        pct_str = f"({self.elapsed_diff_pct:+.1f}%)"
        lines.append(
            f"  {'Duration':<25s} | {self.current_elapsed:>14.1f}s | "
            f"{self.previous_elapsed:>14.1f}s | {diff_str} {pct_str}"
        )

        # Speed
        diff_str = f"{self.current_speed - self.previous_speed:+,.0f}"
        pct_str = f"({self.speed_diff_pct:+.1f}%)"
        lines.append(
            f"  {'Speed (rows/s)':<25s} | {self.current_speed:>15,.0f} | "
            f"{self.previous_speed:>15,.0f} | {diff_str} {pct_str}"
        )

        # Chunks
        lines.append(
            f"  {'Chunks completed':<25s} | {self.current_chunks:>15} | "
            f"{self.previous_chunks:>15} | {self.current_chunks - self.previous_chunks:+d}"
        )

        # Failed
        lines.append(
            f"  {'Failed chunks':<25s} | {self.current_failed:>15} | "
            f"{self.previous_failed:>15} | {self.current_failed - self.previous_failed:+d}"
        )

        lines.append(f"  {'-'*25}-+-{'-'*15}-+-{'-'*15}-+-{'-'*12}")

        # Assessment
        if self.inserted_diff > 0:
            lines.append(f"  ✓ Data grew by {self.inserted_diff:,} rows")
        elif self.inserted_diff < 0:
            lines.append(f"  ⚠ Data decreased by {abs(self.inserted_diff):,} rows")

        if self.speed_diff_pct > 10:
            lines.append(f"  ✓ Speed improved by {self.speed_diff_pct:.1f}%")
        elif self.speed_diff_pct < -10:
            lines.append(f"  ⚠ Speed decreased by {abs(self.speed_diff_pct):.1f}%")

        if self.current_failed > self.previous_failed:
            lines.append(f"  ✗ More failed chunks than previous run")

        lines.append(f"{'='*70}")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Export as dictionary."""
        return {
            "table": self.table_name,
            "current_run_id": self.current_run_id,
            "previous_run_id": self.previous_run_id,
            "current": {
                "inserted": self.current_inserted,
                "elapsed": round(self.current_elapsed, 1),
                "speed": round(self.current_speed, 0),
                "chunks": self.current_chunks,
                "failed": self.current_failed,
            },
            "previous": {
                "inserted": self.previous_inserted,
                "elapsed": round(self.previous_elapsed, 1),
                "speed": round(self.previous_speed, 0),
                "chunks": self.previous_chunks,
                "failed": self.previous_failed,
            },
            "diff": {
                "inserted": self.inserted_diff,
                "inserted_pct": round(self.inserted_diff_pct, 1),
                "speed_pct": round(self.speed_diff_pct, 1),
                "elapsed_pct": round(self.elapsed_diff_pct, 1),
            },
        }
