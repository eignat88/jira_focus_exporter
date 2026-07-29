"""Data models for ETL runs and chunks."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from datetime import datetime


class RunStatus(str, Enum):
    """ETL run status."""
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"


class ChunkStatus(str, Enum):
    """Chunk processing status."""
    PENDING = "pending"
    CLAIMED = "claimed"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRY = "retry"
    CANCELLED = "cancelled"


@dataclass
class Run:
    """ETL run information."""
    run_id: int
    pipeline_name: str
    source_system: str
    source_database: Optional[str]
    source_schema: Optional[str]
    source_table: str
    target_schema: str
    target_table: str
    load_mode: str
    chunk_strategy: str
    chunk_column: Optional[str]
    status: RunStatus
    started_at: datetime
    finished_at: Optional[datetime] = None
    heartbeat_at: Optional[datetime] = None
    source_row_count: Optional[int] = None
    target_row_count: Optional[int] = None
    total_chunks: int = 0
    completed_chunks: int = 0
    failed_chunks: int = 0
    rows_read: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_conflicted: int = 0
    config_hash: Optional[str] = None
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def is_resumable(self) -> bool:
        """Check if run can be resumed."""
        return self.status in (
            RunStatus.RUNNING,
            RunStatus.FAILED,
            RunStatus.COMPLETED_WITH_ERRORS,
            RunStatus.ABANDONED,
        )


@dataclass
class Chunk:
    """ETL chunk information."""
    chunk_id: int
    run_id: int
    chunk_no: int
    chunk_strategy: str
    chunk_column: Optional[str]
    range_start_text: Optional[str] = None
    range_end_text: Optional[str] = None
    range_start_bigint: Optional[int] = None
    range_end_bigint: Optional[int] = None
    range_start_ts: Optional[datetime] = None
    range_end_ts: Optional[datetime] = None
    status: ChunkStatus = ChunkStatus.PENDING
    attempt_count: int = 0
    worker_id: Optional[str] = None
    started_at: Optional[datetime] = None
    heartbeat_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    rows_read: int = 0
    rows_staged: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_conflicted: int = 0
    last_processed_key: Optional[str] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def is_pending(self) -> bool:
        """Check if chunk needs processing."""
        return self.status in (
            ChunkStatus.PENDING,
            ChunkStatus.RETRY,
            ChunkStatus.FAILED,
        )

    @property
    def range_start(self) -> Optional[int]:
        """Get range start as bigint."""
        return self.range_start_bigint

    @property
    def range_end(self) -> Optional[int]:
        """Get range end as bigint."""
        return self.range_end_bigint
