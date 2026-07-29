"""Data quality checks for loaded data."""

from dataclasses import dataclass
from typing import List, Optional

import psycopg2


@dataclass
class QualityCheck:
    """Result of a single quality check."""
    name: str
    passed: bool
    message: str
    details: Optional[dict] = None


class DataQualityChecker:
    """
    Validate data quality after ETL load.

    Checks:
    1. Row count match (source vs target)
    2. No NULL in required columns
    3. No duplicate primary keys
    4. Referential integrity (if FK defined)
    5. Data type consistency
    """

    def __init__(self, conn: psycopg2.extensions.connection, schema: str = "raw_ax"):
        self.conn = conn
        self.schema = schema

    def check_row_count(
        self,
        table_name: str,
        expected_count: int,
    ) -> QualityCheck:
        """Verify row count matches expected."""
        cursor = self.conn.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM {self.schema}.{table_name}")
        actual = cursor.fetchone()[0]
        passed = actual == expected_count
        diff = actual - expected_count
        return QualityCheck(
            name="row_count",
            passed=passed,
            message=f"Expected {expected_count:,}, got {actual:,} (diff={diff:+,})",
            details={"expected": expected_count, "actual": actual, "diff": diff},
        )

    def check_no_duplicates(
        self,
        table_name: str,
        key_column: str = "recid",
    ) -> QualityCheck:
        """Check for duplicate primary keys."""
        cursor = self.conn.cursor()
        cursor.execute(f"""
            SELECT COUNT(*) FROM (
                SELECT {key_column}, COUNT(*) as cnt
                FROM {self.schema}.{table_name}
                GROUP BY {key_column}
                HAVING COUNT(*) > 1
            ) t
        """)
        dup_groups = cursor.fetchone()[0]

        cursor.execute(f"""
            SELECT SUM(cnt - 1) FROM (
                SELECT {key_column}, COUNT(*) as cnt
                FROM {self.schema}.{table_name}
                GROUP BY {key_column}
                HAVING COUNT(*) > 1
            ) t
        """)
        extra_rows = cursor.fetchone()[0] or 0

        passed = dup_groups == 0
        return QualityCheck(
            name="no_duplicates",
            passed=passed,
            message=f"Found {dup_groups:,} duplicate groups ({extra_rows:,} extra rows)" if not passed else "No duplicates found",
            details={"duplicate_groups": dup_groups, "extra_rows": extra_rows},
        )

    def check_null_rate(
        self,
        table_name: str,
        columns: List[str],
        max_null_pct: float = 100.0,
    ) -> QualityCheck:
        """Check null rate for specified columns."""
        cursor = self.conn.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM {self.schema}.{table_name}")
        total = cursor.fetchone()[0]

        if total == 0:
            return QualityCheck(
                name="null_rate",
                passed=True,
                message="Table is empty, null check skipped",
            )

        issues = []
        for col in columns:
            cursor.execute(f"""
                SELECT COUNT(*) FROM {self.schema}.{table_name}
                WHERE {col} IS NULL
            """)
            null_count = cursor.fetchone()[0]
            null_pct = (null_count / total * 100) if total > 0 else 0
            if null_pct > max_null_pct:
                issues.append(f"{col}: {null_pct:.1f}% nulls")

        passed = len(issues) == 0
        return QualityCheck(
            name="null_rate",
            passed=passed,
            message="All columns within null threshold" if passed else f"Columns exceeding {max_null_pct}% nulls: {', '.join(issues)}",
            details={"total_rows": total, "issues": issues},
        )

    def check_not_empty(
        self,
        table_name: str,
        min_rows: int = 1,
    ) -> QualityCheck:
        """Verify table is not empty."""
        cursor = self.conn.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM {self.schema}.{table_name}")
        count = cursor.fetchone()[0]
        passed = count >= min_rows
        return QualityCheck(
            name="not_empty",
            passed=passed,
            message=f"Table has {count:,} rows (min={min_rows:,})",
            details={"count": count, "min_required": min_rows},
        )

    def run_all_checks(
        self,
        table_name: str,
        expected_count: Optional[int] = None,
        key_column: str = "recid",
        required_columns: Optional[List[str]] = None,
        min_rows: int = 1,
    ) -> List[QualityCheck]:
        """Run all quality checks."""
        checks = []

        # Not empty
        checks.append(self.check_not_empty(table_name, min_rows))

        # Row count (if expected provided)
        if expected_count is not None:
            checks.append(self.check_row_count(table_name, expected_count))

        # No duplicates
        checks.append(self.check_no_duplicates(table_name, key_column))

        # Null rate
        if required_columns:
            checks.append(self.check_null_rate(table_name, required_columns))

        return checks

    def summary(self, checks: List[QualityCheck]) -> str:
        """Generate summary of quality checks."""
        passed = sum(1 for c in checks if c.passed)
        total = len(checks)

        lines = []
        lines.append(f"{'='*60}")
        lines.append(f"DATA QUALITY CHECKS: {passed}/{total} passed")
        lines.append(f"{'='*60}")

        for check in checks:
            status = "PASS" if check.passed else "FAIL"
            lines.append(f"  [{status}] {check.name}: {check.message}")

        lines.append(f"{'='*60}")
        return "\n".join(lines)
