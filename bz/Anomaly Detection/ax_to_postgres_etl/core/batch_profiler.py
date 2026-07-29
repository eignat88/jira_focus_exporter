"""Detailed batch-level profiling for ETL operations."""

import time
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class BatchProfile:
    """Profile data for a single batch."""
    batch_num: int
    chunk_id: int
    chunk_no: int
    start_time: float
    end_time: float = 0.0
    rows_fetched: int = 0
    rows_written: int = 0
    fetch_time: float = 0.0
    write_time: float = 0.0
    copy_time: float = 0.0
    error: Optional[str] = None

    @property
    def total_time(self) -> float:
        return self.end_time - self.start_time if self.end_time > 0 else 0

    @property
    def speed(self) -> float:
        return self.rows_written / self.total_time if self.total_time > 0 else 0


class BatchProfiler:
    """
    Track detailed profiling data per batch.

    Provides insights into:
    - Fetch vs write time breakdown
    - Batch-level speed variations
    - Identifying slow batches
    """

    def __init__(self):
        self._profiles: List[BatchProfile] = []
        self._current: Optional[BatchProfile] = None
        self._batch_counter = 0

    def start_batch(self, chunk_id: int, chunk_no: int) -> int:
        """Start profiling a new batch."""
        self._batch_counter += 1
        self._current = BatchProfile(
            batch_num=self._batch_counter,
            chunk_id=chunk_id,
            chunk_no=chunk_no,
            start_time=time.time(),
        )
        return self._batch_counter

    def end_batch(
        self,
        rows_fetched: int = 0,
        rows_written: int = 0,
        fetch_time: float = 0.0,
        write_time: float = 0.0,
        copy_time: float = 0.0,
        error: Optional[str] = None,
    ):
        """End profiling current batch."""
        if self._current:
            self._current.end_time = time.time()
            self._current.rows_fetched = rows_fetched
            self._current.rows_written = rows_written
            self._current.fetch_time = fetch_time
            self._current.write_time = write_time
            self._current.copy_time = copy_time
            self._current.error = error
            self._profiles.append(self._current)
            self._current = None

    @property
    def profiles(self) -> List[BatchProfile]:
        return self._profiles

    @property
    def total_batches(self) -> int:
        return len(self._profiles)

    @property
    def total_rows(self) -> int:
        return sum(p.rows_written for p in self._profiles)

    @property
    def total_time(self) -> float:
        return sum(p.total_time for p in self._profiles)

    @property
    def avg_speed(self) -> float:
        return self.total_rows / self.total_time if self.total_time > 0 else 0

    @property
    def avg_fetch_time(self) -> float:
        fetch_times = [p.fetch_time for p in self._profiles if p.fetch_time > 0]
        return sum(fetch_times) / len(fetch_times) if fetch_times else 0

    @property
    def avg_write_time(self) -> float:
        write_times = [p.write_time for p in self._profiles if p.write_time > 0]
        return sum(write_times) / len(write_times) if write_times else 0

    @property
    def p95_batch_time(self) -> float:
        times = sorted(p.total_time for p in self._profiles)
        if not times:
            return 0
        idx = int(len(times) * 0.95)
        return times[min(idx, len(times) - 1)]

    @property
    def slowest_batch(self) -> Optional[BatchProfile]:
        if not self._profiles:
            return None
        return max(self._profiles, key=lambda p: p.total_time)

    @property
    def fastest_batch(self) -> Optional[BatchProfile]:
        if not self._profiles:
            return None
        return min(self._profiles, key=lambda p: p.total_time if p.total_time > 0 else float('inf'))

    @property
    def failed_batches(self) -> List[BatchProfile]:
        return [p for p in self._profiles if p.error]

    def summary(self) -> str:
        """Generate profiling summary."""
        lines = []
        lines.append(f"{'='*70}")
        lines.append(f"BATCH PROFILING SUMMARY")
        lines.append(f"{'='*70}")

        lines.append(f"  Total batches:    {self.total_batches}")
        lines.append(f"  Total rows:       {self.total_rows:,}")
        lines.append(f"  Total time:       {self.total_time:.1f}s")
        lines.append(f"  Avg speed:        {self.avg_speed:,.0f} rows/s")
        lines.append(f"  Avg fetch time:   {self.avg_fetch_time*1000:.1f}ms")
        lines.append(f"  Avg write time:   {self.avg_write_time*1000:.1f}ms")
        lines.append(f"  P95 batch time:   {self.p95_batch_time*1000:.1f}ms")

        if self.slowest_batch:
            lines.append(f"  Slowest batch:    #{self.slowest_batch.batch_num} "
                        f"({self.slowest_batch.total_time*1000:.1f}ms, "
                        f"{self.slowest_batch.rows_written:,} rows)")

        if self.fastest_batch and self.fastest_batch.total_time > 0:
            lines.append(f"  Fastest batch:    #{self.fastest_batch.batch_num} "
                        f"({self.fastest_batch.total_time*1000:.1f}ms, "
                        f"{self.fastest_batch.rows_written:,} rows)")

        if self.failed_batches:
            lines.append(f"  Failed batches:   {len(self.failed_batches)}")
            for fb in self.failed_batches[:5]:
                lines.append(f"    - Batch #{fb.batch_num}: {fb.error}")

        lines.append(f"{'='*70}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Export as dictionary."""
        return {
            "total_batches": self.total_batches,
            "total_rows": self.total_rows,
            "total_time_seconds": round(self.total_time, 1),
            "avg_speed_rows_per_sec": round(self.avg_speed, 0),
            "avg_fetch_time_ms": round(self.avg_fetch_time * 1000, 1),
            "avg_write_time_ms": round(self.avg_write_time * 1000, 1),
            "p95_batch_time_ms": round(self.p95_batch_time * 1000, 1),
            "failed_batches": len(self.failed_batches),
            "batches": [
                {
                    "num": p.batch_num,
                    "chunk_id": p.chunk_id,
                    "chunk_no": p.chunk_no,
                    "total_time_ms": round(p.total_time * 1000, 1),
                    "fetch_time_ms": round(p.fetch_time * 1000, 1),
                    "write_time_ms": round(p.write_time * 1000, 1),
                    "rows_written": p.rows_written,
                    "speed": round(p.speed, 0) if p.speed > 0 else 0,
                    "error": p.error,
                }
                for p in self._profiles
            ],
        }
