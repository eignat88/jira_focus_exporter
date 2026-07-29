"""
Parallel pipeline for ETL loading.

Implements typed messages, writer confirmation, and coordinated shutdown.
"""

import time
import threading
from queue import Queue, Empty
from dataclasses import dataclass
from typing import List, Any, Optional
from enum import Enum


class MessageType(str, Enum):
    """Queue message types."""
    WRITE_BATCH = "WRITE_BATCH"
    CHUNK_READ_COMPLETED = "CHUNK_READ_COMPLETED"
    PRODUCER_COMPLETED = "PRODUCER_COMPLETED"
    SENTINEL = "SENTINEL"


@dataclass(frozen=True)
class WriteBatch:
    """Message containing a batch of rows to write."""
    load_group_id: str
    chunk_id: int
    chunk_run_id: int
    range_from: int
    range_to: int
    last_recid: int
    rows: List[List[Any]]
    is_last_batch: bool = False


@dataclass(frozen=True)
class ChunkReadCompleted:
    """Message indicating chunk read is complete."""
    load_group_id: str
    chunk_id: int
    chunk_run_id: int
    last_recid: int
    rows_fetched: int


@dataclass(frozen=True)
class ProducerCompleted:
    """Message indicating a producer worker is done."""
    worker_id: int


@dataclass
class ChunkWriteState:
    """Tracks write state for a chunk."""
    chunk_id: int
    chunk_run_id: int
    load_group_id: str
    rows_received: int = 0
    rows_committed: int = 0
    read_completed: bool = False
    last_committed_recid: Optional[int] = None
    
    @property
    def is_complete(self) -> bool:
        """Check if chunk is fully written."""
        return self.read_completed and self.rows_received == self.rows_committed


# Sentinel object for writer shutdown
SENTINEL = object()


class ParallelPipeline:
    """
    Parallel pipeline with typed messages and writer confirmation.
    
    Architecture:
    - N producer workers → Queue → 1 writer thread
    - Typed messages for coordination
    - Writer confirms chunk completion
    """
    
    def __init__(
        self,
        queue_size: int = 16,
        log_func=None
    ):
        self.write_queue: Queue = Queue(maxsize=queue_size)
        self.stop_event = threading.Event()
        self.log_func = log_func or print
        
        # Chunk write states
        self.chunk_states: dict[int, ChunkWriteState] = {}
        
        # Error tracking
        self.writer_error: Optional[Exception] = None
        self.running = True
    
    def _log(self, message: str):
        """Log message if log_func is set."""
        if self.log_func:
            self.log_func(message)
    
    def put_batch(self, batch: WriteBatch):
        """
        Put a batch into the queue with backpressure handling.
        
        Retries on queue.Full until stop_event is set.
        """
        while not self.stop_event.is_set():
            try:
                self.write_queue.put(batch, timeout=1.0)
                return
            except Exception:  # queue.Full
                time.sleep(0.5)
        
        # If we get here, stop_event was set
        raise RuntimeError("Stop event set during queue put")
    
    def get_batch(self, timeout: float = 1.0) -> Optional[Any]:
        """
        Get a batch from the queue with timeout.
        
        Returns None on timeout or stop_event.
        """
        try:
            return self.write_queue.get(timeout=timeout)
        except Empty:
            return None
    
    def task_done(self):
        """Mark a task as done in the queue."""
        self.write_queue.task_done()
    
    def update_chunk_state(self, message: Any):
        """
        Update chunk write state based on message type.
        
        This is called by the writer after processing a message.
        """
        if isinstance(message, WriteBatch):
            chunk_id = message.chunk_id
            
            # Initialize chunk state if needed
            if chunk_id not in self.chunk_states:
                self.chunk_states[chunk_id] = ChunkWriteState(
                    chunk_id=chunk_id,
                    chunk_run_id=message.chunk_run_id,
                    load_group_id=message.load_group_id
                )
            
            state = self.chunk_states[chunk_id]
            state.rows_received += len(message.rows)
            state.last_committed_recid = message.last_recid
            
            if message.is_last_batch:
                state.read_completed = True
        
        elif isinstance(message, ChunkReadCompleted):
            chunk_id = message.chunk_id
            
            if chunk_id not in self.chunk_states:
                self.chunk_states[chunk_id] = ChunkWriteState(
                    chunk_id=chunk_id,
                    chunk_run_id=message.chunk_run_id,
                    load_group_id=message.load_group_id
                )
            
            state = self.chunk_states[chunk_id]
            state.read_completed = True
    
    def can_confirm_chunk(self, chunk_id: int) -> bool:
        """
        Check if a chunk can be confirmed as DONE.
        
        Chunk is complete when:
        - read_completed = True
        - all received rows are committed
        """
        if chunk_id not in self.chunk_states:
            return False
        
        state = self.chunk_states[chunk_id]
        return state.is_complete
    
    def confirm_chunk_committed(self, chunk_id: int, rows_committed: int):
        """
        Update committed row count for a chunk.
        
        Called by writer after successful commit.
        """
        if chunk_id in self.chunk_states:
            state = self.chunk_states[chunk_id]
            state.rows_committed += rows_committed
    
    def shutdown(self):
        """Initiate graceful shutdown."""
        self.stop_event.set()
        self.running = False
        self._log("Pipeline shutdown initiated")
    
    def is_shutdown_requested(self) -> bool:
        """Check if shutdown was requested."""
        return self.stop_event.is_set()
    
    def wait_for_workers(self, workers: List[threading.Thread], timeout: float = 60.0):
        """Wait for all workers to complete."""
        for worker in workers:
            worker.join(timeout=timeout)
            if worker.is_alive():
                self._log(f"WARNING: Worker {worker.name} did not stop within {timeout}s")
