"""Domain models for ETL pipeline."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class LoadStatus(str, Enum):
    """Load status enumeration."""
    SUCCESS = "SUCCESS"
    ALREADY_COMPLETE = "ALREADY_COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class LoadResult:
    """Unified result of a load operation."""
    status: LoadStatus
    table_name: Optional[str] = None
    rows_fetched: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_conflicted: int = 0
    rows_rejected: int = 0
    chunks_total: int = 0
    chunks_completed: int = 0
    target_count: Optional[int] = None
    elapsed_seconds: float = 0
    failed_tables: tuple = ()
    error_message: Optional[str] = None


# Legacy aliases for backward compatibility
LoadStatusLegacy = LoadStatus
LoadResultLegacy = LoadResult
