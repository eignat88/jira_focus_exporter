from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class PipelineSpec:
    name: str
    source_system: str
    source_schema: str
    source_table: str
    target_schema: str
    target_table: str
    key_column: str
    batch_size: int = 250_000
    count_mode: str = "estimate"
    load_mode: str = "resume"
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BatchResult:
    rows_read: int
    rows_inserted: int
    rows_updated: int = 0
    rows_conflicted: int = 0
    last_processed_key: str | None = None


class LoadAdapter(Protocol):
    """Source/target-specific data movement; orchestration stays generic."""

    def get_boundaries(self, data_conn, spec: PipelineSpec) -> tuple[int, int]: ...

    def build_ranges(
        self, start: int, end: int, batch_size: int
    ) -> list[tuple[int, int]]: ...

    def execute_batch(
        self, data_conn, spec: PipelineSpec, start: int, end: int
    ) -> BatchResult: ...

    def validate(self, data_conn, spec: PipelineSpec) -> dict[str, Any]: ...
