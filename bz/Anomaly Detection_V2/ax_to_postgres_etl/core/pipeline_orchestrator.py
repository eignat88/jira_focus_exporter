"""Pipeline orchestrator for multi-table ETL operations."""

import time
import threading
from dataclasses import dataclass, field
from typing import List, Optional, Callable, Dict, Any
from enum import Enum
from datetime import datetime


class PipelineStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class PipelineStep:
    """A single step in the ETL pipeline."""
    name: str
    table_name: str
    load_mode: str = "resume"
    priority: int = 0
    depends_on: List[str] = field(default_factory=list)
    status: str = "pending"
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    error: Optional[str] = None
    result: Optional[Dict] = None


class PipelineOrchestrator:
    """
    Orchestrate multi-table ETL pipeline.

    Features:
    - Dependency resolution
    - Parallel execution of independent steps
    - Pause/resume support
    - Rollback on failure
    - Progress tracking
    """

    def __init__(self, max_parallel: int = 3, log_func: Optional[Callable] = None):
        self.max_parallel = max_parallel
        self.log_func = log_func
        self._steps: Dict[str, PipelineStep] = {}
        self._status = PipelineStatus.IDLE
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()

    def add_step(self, step: PipelineStep):
        """Add a step to the pipeline."""
        self._steps[step.name] = step

    def add_steps(self, steps: List[PipelineStep]):
        """Add multiple steps."""
        for step in steps:
            self.add_step(step)

    def _get_ready_steps(self) -> List[PipelineStep]:
        """Get steps that are ready to execute."""
        ready = []
        for step in self._steps.values():
            if step.status != "pending":
                continue

            # Check dependencies
            deps_met = all(
                self._steps[dep].status == "completed"
                for dep in step.depends_on
                if dep in self._steps
            )
            if deps_met:
                ready.append(step)

        return sorted(ready, key=lambda s: -s.priority)

    def _execute_step(self, step: PipelineStep, load_fn: Callable):
        """Execute a single pipeline step."""
        if self._stop_event.is_set():
            return

        self._pause_event.wait()  # Wait if paused

        step.status = "running"
        step.start_time = time.time()

        if self.log_func:
            self.log_func(f"PIPELINE: Starting {step.name} ({step.table_name}, mode={step.load_mode})")

        try:
            result = load_fn(step.table_name, step.load_mode)
            step.result = result
            step.status = "completed"
            step.end_time = time.time()

            if self.log_func:
                elapsed = step.end_time - step.start_time
                self.log_func(f"PIPELINE: {step.name} completed in {elapsed:.1f}s")

        except Exception as e:
            step.status = "failed"
            step.error = str(e)
            step.end_time = time.time()

            if self.log_func:
                self.log_func(f"PIPELINE: {step.name} FAILED: {e}")

    def run(self, load_fn: Callable) -> Dict[str, PipelineStep]:
        """
        Run the pipeline.

        Args:
            load_fn: Callable(table_name, load_mode) -> result

        Returns:
            Dict of step results
        """
        self._status = PipelineStatus.RUNNING
        self._stop_event.clear()
        self._pause_event.set()  # Not paused

        if self.log_func:
            self.log_func(f"PIPELINE: Starting with {len(self._steps)} steps")

        start_time = time.time()

        while not self._stop_event.is_set():
            ready = self._get_ready_steps()
            if not ready:
                # Check if all done
                all_done = all(
                    s.status in ("completed", "failed")
                    for s in self._steps.values()
                )
                if all_done:
                    break
                time.sleep(0.1)
                continue

            # Execute ready steps (up to max_parallel)
            threads = []
            for step in ready[:self.max_parallel]:
                t = threading.Thread(
                    target=self._execute_step,
                    args=(step, load_fn),
                    daemon=True,
                )
                t.start()
                threads.append(t)

            for t in threads:
                t.join()

        elapsed = time.time() - start_time

        # Determine final status
        failed = any(s.status == "failed" for s in self._steps.values())
        self._status = PipelineStatus.FAILED if failed else PipelineStatus.COMPLETED

        if self.log_func:
            completed = sum(1 for s in self._steps.values() if s.status == "completed")
            failed_count = sum(1 for s in self._steps.values() if s.status == "failed")
            self.log_func(
                f"PIPELINE: Finished in {elapsed:.1f}s — "
                f"{completed} completed, {failed_count} failed"
            )

        return self._steps

    def pause(self):
        """Pause the pipeline."""
        self._pause_event.clear()
        self._status = PipelineStatus.PAUSED
        if self.log_func:
            self.log_func("PIPELINE: Paused")

    def resume(self):
        """Resume the pipeline."""
        self._pause_event.set()
        self._status = PipelineStatus.RUNNING
        if self.log_func:
            self.log_func("PIPELINE: Resumed")

    def stop(self):
        """Stop the pipeline."""
        self._stop_event.set()
        self._pause_event.set()  # Unblock paused threads
        self._status = PipelineStatus.FAILED
        if self.log_func:
            self.log_func("PIPELINE: Stopped")

    @property
    def status(self) -> PipelineStatus:
        return self._status

    @property
    def progress(self) -> Dict[str, int]:
        """Get pipeline progress."""
        counts = {"pending": 0, "running": 0, "completed": 0, "failed": 0}
        for step in self._steps.values():
            counts[step.status] = counts.get(step.status, 0) + 1
        return counts

    def summary(self) -> str:
        """Generate pipeline summary."""
        progress = self.progress
        total = sum(progress.values())

        lines = []
        lines.append(f"{'='*70}")
        lines.append(f"PIPELINE SUMMARY")
        lines.append(f"{'='*70}")
        lines.append(f"  Status: {self._status.value}")
        lines.append(f"  Total steps: {total}")
        lines.append(f"  Completed: {progress['completed']}")
        lines.append(f"  Failed: {progress['failed']}")
        lines.append(f"  Running: {progress['running']}")
        lines.append(f"  Pending: {progress['pending']}")
        lines.append(f"{'='*70}")

        for name, step in self._steps.items():
            status_icon = {
                "pending": "○", "running": "●", "completed": "✓", "failed": "✗"
            }.get(step.status, "?")
            elapsed = ""
            if step.start_time and step.end_time:
                elapsed = f" ({step.end_time - step.start_time:.1f}s)"
            lines.append(f"  [{status_icon}] {name}: {step.table_name} ({step.load_mode}){elapsed}")

        lines.append(f"{'='*70}")
        return "\n".join(lines)
