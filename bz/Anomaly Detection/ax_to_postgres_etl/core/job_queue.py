"""ETL job queue with priority scheduling."""

import time
import threading
from dataclasses import dataclass, field
from typing import List, Optional, Callable, Any
from enum import Enum
from queue import PriorityQueue
from datetime import datetime


class JobStatus(Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(order=True)
class ETLJob:
    """A prioritized ETL job."""
    priority: int
    job_id: str = field(compare=False)
    table_name: str = field(compare=False)
    load_mode: str = field(compare=False, default="resume")
    status: JobStatus = field(compare=False, default=JobStatus.PENDING)
    created_at: float = field(compare=False, default_factory=time.time)
    started_at: Optional[float] = field(compare=False, default=None)
    completed_at: Optional[float] = field(compare=False, default=None)
    error: Optional[str] = field(compare=False, default=None)
    result: Optional[dict] = field(compare=False, default=None)
    retries: int = field(compare=False, default=0)
    max_retries: int = field(compare=False, default=3)


class JobQueue:
    """
    Thread-safe job queue for ETL operations.

    Features:
    - Priority-based scheduling
    - Retry on failure
    - Job status tracking
    - Concurrent workers
    """

    def __init__(
        self,
        max_workers: int = 2,
        log_func: Optional[Callable] = None,
    ):
        self.max_workers = max_workers
        self.log_func = log_func
        self._queue = PriorityQueue()
        self._jobs: dict[str, ETLJob] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._workers: List[threading.Thread] = []
        self._running = False

    def submit(self, job: ETLJob):
        """Submit a job to the queue."""
        with self._lock:
            self._jobs[job.job_id] = job
            job.status = JobStatus.QUEUED
            self._queue.put(job)

        if self.log_func:
            self.log_func(f"JOB QUEUE: Submitted {job.job_id} ({job.table_name})")

    def submit_batch(self, jobs: List[ETLJob]):
        """Submit multiple jobs."""
        for job in jobs:
            self.submit(job)

    def start(self, process_fn: Callable):
        """Start processing jobs."""
        self._running = True
        self._stop_event.clear()

        for i in range(self.max_workers):
            t = threading.Thread(
                target=self._worker_loop,
                args=(process_fn, i),
                daemon=True,
            )
            t.start()
            self._workers.append(t)

        if self.log_func:
            self.log_func(f"JOB QUEUE: Started {self.max_workers} workers")

    def _worker_loop(self, process_fn: Callable, worker_id: int):
        """Worker loop for processing jobs."""
        while not self._stop_event.is_set():
            try:
                job = self._queue.get(timeout=0.5)
            except Exception:
                continue

            job.status = JobStatus.RUNNING
            job.started_at = time.time()

            if self.log_func:
                self.log_func(
                    f"JOB QUEUE: Worker {worker_id} processing {job.job_id} "
                    f"({job.table_name}, mode={job.load_mode})"
                )

            try:
                result = process_fn(job.table_name, job.load_mode)
                job.result = result
                job.status = JobStatus.COMPLETED
                job.completed_at = time.time()

                if self.log_func:
                    elapsed = job.completed_at - job.started_at
                    self.log_func(
                        f"JOB QUEUE: {job.job_id} completed in {elapsed:.1f}s"
                    )

            except Exception as e:
                job.error = str(e)
                job.retries += 1

                if job.retries < job.max_retries:
                    job.status = JobStatus.QUEUED
                    self._queue.put(job)
                    if self.log_func:
                        self.log_func(
                            f"JOB QUEUE: {job.job_id} failed, retrying "
                            f"({job.retries}/{job.max_retries}): {e}"
                        )
                else:
                    job.status = JobStatus.FAILED
                    job.completed_at = time.time()
                    if self.log_func:
                        self.log_func(
                            f"JOB QUEUE: {job.job_id} FAILED after "
                            f"{job.max_retries} retries: {e}"
                        )

    def stop(self):
        """Stop all workers."""
        self._stop_event.set()
        self._running = False
        for t in self._workers:
            t.join(timeout=5)
        self._workers.clear()

    def get_job(self, job_id: str) -> Optional[ETLJob]:
        """Get job by ID."""
        return self._jobs.get(job_id)

    def get_status(self) -> dict:
        """Get queue status."""
        counts = {s.value: 0 for s in JobStatus}
        for job in self._jobs.values():
            counts[job.status.value] += 1
        return counts

    def summary(self) -> str:
        """Generate queue summary."""
        status = self.get_status()

        lines = []
        lines.append(f"{'='*60}")
        lines.append(f"JOB QUEUE STATUS")
        lines.append(f"{'='*60}")
        lines.append(f"  Running:  {status['running']}")
        lines.append(f"  Queued:   {status['queued']}")
        lines.append(f"  Completed: {status['completed']}")
        lines.append(f"  Failed:   {status['failed']}")
        lines.append(f"{'='*60}")

        for job_id, job in sorted(self._jobs.items()):
            if job.status == JobStatus.RUNNING:
                elapsed = time.time() - (job.started_at or time.time())
                lines.append(f"  [{job.status.value.upper():9s}] {job_id}: {job.table_name} ({elapsed:.1f}s)")
            elif job.status != JobStatus.COMPLETED:
                lines.append(f"  [{job.status.value.upper():9s}] {job_id}: {job.table_name}")

        lines.append(f"{'='*60}")
        return "\n".join(lines)
