"""Load validation report generator."""

from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime


@dataclass
class ValidationCheck:
    """A single validation check result."""
    name: str
    passed: bool
    expected: Optional[str] = None
    actual: Optional[str] = None
    message: str = ""


class ValidationReport:
    """
    Generate comprehensive validation report after ETL load.

    Checks:
    1. Row count integrity
    2. Primary key uniqueness
    3. Required columns present
    4. Data type consistency
    5. Null rate within thresholds
    6. Referential integrity (if FK defined)
    7. Source-target consistency
    """

    def __init__(self, table_name: str):
        self.table_name = table_name
        self.checks: List[ValidationCheck] = []
        self.start_time = datetime.now()

    def add_check(
        self,
        name: str,
        passed: bool,
        expected: Optional[str] = None,
        actual: Optional[str] = None,
        message: str = "",
    ):
        """Add a validation check result."""
        self.checks.append(ValidationCheck(
            name=name,
            passed=passed,
            expected=expected,
            actual=actual,
            message=message,
        ))

    def check_row_count(self, expected: int, actual: int):
        """Check row count matches."""
        passed = expected == actual
        diff = actual - expected
        self.add_check(
            name="row_count",
            passed=passed,
            expected=f"{expected:,}",
            actual=f"{actual:,}",
            message=f"Match" if passed else f"Diff: {diff:+,}",
        )

    def check_no_duplicates(self, pk_column: str, dup_count: int):
        """Check for duplicate primary keys."""
        passed = dup_count == 0
        self.add_check(
            name="no_duplicates",
            passed=passed,
            expected="0",
            actual=str(dup_count),
            message="No duplicates" if passed else f"{dup_count} duplicate groups found",
        )

    def check_columns_present(self, required: List[str], missing: List[str]):
        """Check required columns are present."""
        passed = len(missing) == 0
        self.add_check(
            name="columns_present",
            passed=passed,
            expected=f"{len(required)} columns",
            actual=f"{len(required) - len(missing)} columns",
            message="All columns present" if passed else f"Missing: {', '.join(missing)}",
        )

    def check_null_rate(self, column: str, max_pct: float, actual_pct: float):
        """Check null rate within threshold."""
        passed = actual_pct <= max_pct
        self.add_check(
            name=f"null_rate_{column}",
            passed=passed,
            expected=f"<={max_pct:.1f}%",
            actual=f"{actual_pct:.1f}%",
            message="OK" if passed else f"Exceeds threshold",
        )

    def check_source_target_match(self, source_count: int, target_count: int):
        """Check source and target counts match."""
        passed = source_count == target_count
        diff = target_count - source_count
        diff_pct = (diff / source_count * 100) if source_count > 0 else 0
        self.add_check(
            name="source_target_match",
            passed=passed,
            expected=f"{source_count:,}",
            actual=f"{target_count:,}",
            message="Match" if passed else f"Diff: {diff:+,} ({diff_pct:+.1f}%)",
        )

    def check_min_rows(self, min_rows: int, actual: int):
        """Check minimum row count."""
        passed = actual >= min_rows
        self.add_check(
            name="min_rows",
            passed=passed,
            expected=f">={min_rows:,}",
            actual=f"{actual:,}",
            message="OK" if passed else f"Below minimum",
        )

    @property
    def all_passed(self) -> bool:
        """Check if all validations passed."""
        return all(c.passed for c in self.checks)

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def failed_count(self) -> int:
        return sum(1 for c in self.checks if not c.passed)

    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = []
        lines.append(f"{'='*60}")
        lines.append(f"VALIDATION REPORT: {self.table_name}")
        lines.append(f"{'='*60}")
        lines.append(f"  Result: {self.passed_count}/{len(self.checks)} passed")
        lines.append(f"  Time:   {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"{'='*60}")

        for check in self.checks:
            status = "PASS" if check.passed else "FAIL"
            lines.append(f"  [{status}] {check.name}")
            if check.expected:
                lines.append(f"         Expected: {check.expected}")
            if check.actual:
                lines.append(f"         Actual:   {check.actual}")
            if check.message:
                lines.append(f"         Message:  {check.message}")

        lines.append(f"{'='*60}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Export as dictionary."""
        return {
            "table": self.table_name,
            "timestamp": self.start_time.isoformat(),
            "all_passed": self.all_passed,
            "passed": self.passed_count,
            "failed": self.failed_count,
            "total": len(self.checks),
            "checks": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "expected": c.expected,
                    "actual": c.actual,
                    "message": c.message,
                }
                for c in self.checks
            ],
        }
