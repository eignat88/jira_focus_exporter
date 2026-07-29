"""ETL configuration validator."""

from dataclasses import dataclass
from typing import List, Optional, Dict, Any


@dataclass
class ValidationIssue:
    """A single validation issue."""
    severity: str  # error, warning, info
    path: str
    message: str
    suggestion: Optional[str] = None


class ConfigValidator:
    """
    Validate ETL configuration before loading.

    Checks:
    - Required fields present
    - Value ranges correct
    - Table configurations valid
    - Dependency consistency
    """

    REQUIRED_FIELDS = [
        "source.server",
        "source.database",
        "target.host",
        "target.port",
        "target.database",
        "target.schema",
    ]

    TABLE_REQUIRED_FIELDS = [
        "source_schema",
        "chunk_strategy",
        "chunk_column",
    ]

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._issues: List[ValidationIssue] = []

    def validate(self) -> List[ValidationIssue]:
        """Run all validation checks."""
        self._issues = []

        self._check_required_fields()
        self._check_value_ranges()
        self._check_table_configs()
        self._check_parallel_config()
        self._check_retry_config()
        self._check_heartbeat_config()

        return self._issues

    def _get_nested(self, path: str) -> Any:
        """Get nested config value by dot path."""
        parts = path.split(".")
        value = self.config
        for part in parts:
            if isinstance(value, dict):
                value = value.get(part)
            else:
                return None
        return value

    def _add_issue(self, severity: str, path: str, message: str, suggestion: str = None):
        self._issues.append(ValidationIssue(severity, path, message, suggestion))

    def _check_required_fields(self):
        """Check all required fields are present."""
        for field in self.REQUIRED_FIELDS:
            value = self._get_nested(field)
            if value is None:
                self._add_issue("error", field, f"Required field missing: {field}")

    def _check_value_ranges(self):
        """Check numeric values are in valid ranges."""
        # Workers
        workers = self._get_nested("etl.parallel.workers")
        if workers is not None:
            if workers < 1:
                self._add_issue("error", "etl.parallel.workers", "Workers must be >= 1")
            elif workers > 32:
                self._add_issue("warning", "etl.parallel.workers",
                              "Workers > 32 may cause contention")

        # Fetch size
        fetch_size = self._get_nested("etl.parallel.fetch_size")
        if fetch_size is not None:
            if fetch_size < 100:
                self._add_issue("error", "etl.parallel.fetch_size",
                              "Fetch size must be >= 100")
            elif fetch_size > 50000:
                self._add_issue("warning", "etl.parallel.fetch_size",
                              "Large fetch size may use excessive memory")

        # Commit size
        commit_size = self._get_nested("etl.parallel.commit_size")
        if commit_size is not None:
            if commit_size < 1000:
                self._add_issue("warning", "etl.parallel.commit_size",
                              "Small commit size may slow down loading")

        # Port
        port = self._get_nested("target.port")
        if port is not None:
            if not (1 <= port <= 65535):
                self._add_issue("error", "target.port", "Port must be 1-65535")

    def _check_table_configs(self):
        """Check table configurations."""
        tables = self.config.get("tables", {})
        if not tables:
            self._add_issue("warning", "tables", "No tables configured")

        for table_name, table_config in tables.items():
            if not isinstance(table_config, dict):
                self._add_issue("error", f"tables.{table_name}", "Table config must be dict")
                continue

            # Check chunk strategy
            strategy = table_config.get("chunk_strategy")
            if strategy and strategy not in ("numeric_range", "text_range", "timestamp_range", "full_table"):
                self._add_issue("warning", f"tables.{table_name}.chunk_strategy",
                              f"Unknown strategy: {strategy}")

            # Check chunk_count
            chunk_count = table_config.get("chunk_count")
            if chunk_count is not None:
                if chunk_count < 1:
                    self._add_issue("error", f"tables.{table_name}.chunk_count",
                                  "chunk_count must be >= 1")
                elif chunk_count > 10000:
                    self._add_issue("warning", f"tables.{table_name}.chunk_count",
                                  "Very large chunk_count may be slow")

    def _check_parallel_config(self):
        """Check parallel configuration."""
        parallel = self.config.get("etl", {}).get("parallel", {})
        if not parallel:
            self._add_issue("info", "etl.parallel", "No parallel config, using defaults")

    def _check_retry_config(self):
        """Check retry configuration."""
        retry = self.config.get("retry", {})
        if retry:
            max_attempts = retry.get("max_attempts", 5)
            if max_attempts < 1:
                self._add_issue("error", "retry.max_attempts", "Must be >= 1")
            elif max_attempts > 20:
                self._add_issue("warning", "retry.max_attempts",
                              "Very high retry count may cause long waits")

    def _check_heartbeat_config(self):
        """Check heartbeat configuration."""
        heartbeat = self.config.get("heartbeat", {})
        if heartbeat:
            interval = heartbeat.get("interval_seconds", 30)
            timeout = heartbeat.get("timeout_seconds", 600)

            if interval < 5:
                self._add_issue("warning", "heartbeat.interval_seconds",
                              "Very frequent heartbeat may load DB")
            if timeout < interval * 2:
                self._add_issue("warning", "heartbeat.timeout_seconds",
                              "Timeout should be at least 2x interval")

    def summary(self) -> str:
        """Generate validation summary."""
        errors = sum(1 for i in self._issues if i.severity == "error")
        warnings = sum(1 for i in self._issues if i.severity == "warning")
        infos = sum(1 for i in self._issues if i.severity == "info")

        lines = []
        lines.append(f"{'='*60}")
        lines.append(f"CONFIG VALIDATION: {errors} errors, {warnings} warnings, {infos} info")
        lines.append(f"{'='*60}")

        for issue in self._issues:
            icon = {"error": "✗", "warning": "⚠", "info": "ℹ"}.get(issue.severity, "?")
            lines.append(f"  [{icon}] {issue.path}: {issue.message}")
            if issue.suggestion:
                lines.append(f"       Suggestion: {issue.suggestion}")

        if not self._issues:
            lines.append("  ✓ Configuration is valid")

        lines.append(f"{'='*60}")
        return "\n".join(lines)

    @property
    def is_valid(self) -> bool:
        """Check if config has no errors."""
        return not any(i.severity == "error" for i in self._issues)
