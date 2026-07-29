"""Schema and data validation for ETL batch processing."""

from typing import List, Tuple, Any
from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    """Result of a batch validation pass."""
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def __str__(self) -> str:
        parts = [f"valid={self.is_valid}"]
        if self.errors:
            parts.append(f"errors={len(self.errors)}")
        if self.warnings:
            parts.append(f"warnings={len(self.warnings)}")
        return f"ValidationResult({', '.join(parts)})"


def validate_batch(
    rows: List[Tuple[Any, ...]],
    expected_columns: int,
    table_name: str,
) -> ValidationResult:
    """
    Validate a batch of rows before loading into PostgreSQL.

    Checks performed:
      - Column count consistency (each row must have exactly expected_columns values)
      - Null byte detection in string values (triggers warnings, not errors)

    Args:
        rows: List of row tuples fetched from the source.
        expected_columns: Number of columns the target table expects.
        table_name: Table name for context in error messages.

    Returns:
        ValidationResult with is_valid=True if no hard errors found.
    """
    errors: List[str] = []
    warnings: List[str] = []

    for i, row in enumerate(rows):
        if len(row) != expected_columns:
            errors.append(
                f"Row {i}: expected {expected_columns} columns, got {len(row)}"
            )

        for j, val in enumerate(row):
            if isinstance(val, str) and "\x00" in val:
                warnings.append(
                    f"Row {i}, col {j}: contains null bytes (will be stripped)"
                )

    return ValidationResult(
        is_valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )
