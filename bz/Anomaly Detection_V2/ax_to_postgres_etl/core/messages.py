"""Typed message protocol for worker → writer communication."""

from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True)
class DataBatch:
    """Batch of rows from a specific chunk."""
    chunk_id: int
    chunk_no: int
    rows: Sequence[Sequence[Any]]
    last_processed_key: int


@dataclass(frozen=True)
class ChunkFinished:
    """Signal that worker fully read a chunk's range."""
    chunk_id: int
    chunk_no: int
    rows_read: int
    last_processed_key: int | None


@dataclass(frozen=True)
class ChunkFailed:
    """Signal that a chunk failed during processing."""
    chunk_id: int
    chunk_no: int
    error_type: str
    error_message: str
    rows_read: int
    last_processed_key: int | None
