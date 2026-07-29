"""Integration tests for P9-P12 features."""

import pytest
from unittest.mock import MagicMock, patch, call
from dataclasses import dataclass
import time


# ============================================================
# P9: Profiler tests
# ============================================================

class TestETLProfiler:
    def test_phase_timing(self):
        from ax_to_postgres_etl.core.profiler import ETLProfiler
        p = ETLProfiler()
        p.start_phase("fetch")
        time.sleep(0.01)
        p.end_phase("fetch", rows_processed=1000)
        p.finish()

        phase = p.get_phase("fetch")
        assert phase.rows_processed == 1000
        assert phase.elapsed > 0
        assert phase.speed > 0

    def test_summary_format(self):
        from ax_to_postgres_etl.core.profiler import ETLProfiler
        p = ETLProfiler()
        p.start_phase("test")
        p.end_phase("test", rows=100)
        p.finish()
        summary = p.summary()
        assert "PERFORMANCE SUMMARY" in summary
        assert "TOTAL" in summary

    def test_to_dict(self):
        from ax_to_postgres_etl.core.profiler import ETLProfiler
        p = ETLProfiler()
        p.start_phase("load")
        p.end_phase("load", rows=50000)
        p.finish()
        d = p.to_dict()
        assert "total_elapsed_seconds" in d
        assert "phases" in d


# ============================================================
# P9: DataQuality tests
# ============================================================

class TestDataQualityChecker:
    def test_check_not_empty(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = [1000]
        mock_conn.cursor.return_value = mock_cursor

        from ax_to_postgres_etl.core.data_quality import DataQualityChecker
        checker = DataQualityChecker(mock_conn)
        result = checker.check_not_empty("test_table", min_rows=1)
        assert result.passed is True

    def test_check_not_empty_fail(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = [0]
        mock_conn.cursor.return_value = mock_cursor

        from ax_to_postgres_etl.core.data_quality import DataQualityChecker
        checker = DataQualityChecker(mock_conn)
        result = checker.check_not_empty("test_table", min_rows=1)
        assert result.passed is False

    def test_check_no_duplicates(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = [0]
        mock_conn.cursor.return_value = mock_cursor

        from ax_to_postgres_etl.core.data_quality import DataQualityChecker
        checker = DataQualityChecker(mock_conn)
        result = checker.check_no_duplicates("test_table")
        assert result.passed is True

    def test_summary(self):
        mock_conn = MagicMock()
        from ax_to_postgres_etl.core.data_quality import DataQualityChecker
        checker = DataQualityChecker(mock_conn)
        checks = [MagicMock(passed=True), MagicMock(passed=False)]
        summary = checker.summary(checks)
        assert "1/2 passed" in summary


# ============================================================
# P10: MetricsExporter tests
# ============================================================

class TestMetricsExporter:
    def test_prometheus_format(self):
        from ax_to_postgres_etl.core.metrics_exporter import ETLMetrics
        m = ETLMetrics("test_table")
        m.rows_inserted = 100000
        m.chunks_completed = 10
        m.finish()
        prom = m.to_prometheus()
        assert "etl_rows_inserted" in prom
        assert "etl_chunks_completed" in prom

    def test_to_dict(self):
        from ax_to_postgres_etl.core.metrics_exporter import ETLMetrics
        m = ETLMetrics("test_table")
        m.record_batch(0.1, 1000)
        m.finish()
        d = m.to_dict()
        assert d["table"] == "test_table"
        assert d["status"] == "completed"
        assert "performance" in d


# ============================================================
# P10: ValidationReport tests
# ============================================================

class TestValidationReport:
    def test_all_passed(self):
        from ax_to_postgres_etl.core.validation_report import ValidationReport
        r = ValidationReport("test_table")
        r.check_row_count(100, 100)
        r.check_no_duplicates("recid", 0)
        assert r.all_passed is True
        assert r.passed_count == 2
        assert r.failed_count == 0

    def test_some_failed(self):
        from ax_to_postgres_etl.core.validation_report import ValidationReport
        r = ValidationReport("test_table")
        r.check_row_count(100, 90)
        r.check_no_duplicates("recid", 0)
        assert r.all_passed is False
        assert r.failed_count == 1

    def test_summary(self):
        from ax_to_postgres_etl.core.validation_report import ValidationReport
        r = ValidationReport("test_table")
        r.check_row_count(100, 100)
        summary = r.summary()
        assert "VALIDATION REPORT" in summary
        assert "test_table" in summary

    def test_to_dict(self):
        from ax_to_postgres_etl.core.validation_report import ValidationReport
        r = ValidationReport("test_table")
        r.check_row_count(100, 100)
        d = r.to_dict()
        assert d["table"] == "test_table"
        assert d["all_passed"] is True


# ============================================================
# P10: AutoTuner tests
# ============================================================

class TestAutoTuner:
    def test_reduce_workers_when_idle(self):
        from ax_to_postgres_etl.core.auto_tune import AutoTuner
        config = {"etl": {"parallel": {"workers": 4, "fetch_size": 5000, "commit_size": 50000}}}
        tuner = AutoTuner(config)
        results = tuner.analyze(
            speed_rows_per_sec=50000,
            avg_batch_time_ms=50,
            memory_usage_mb=100,
            workers_active_pct=20,  # Low activity
            queue_full_pct=10,
        )
        # Should suggest reducing workers
        worker_changes = [r for r in results if "workers" in r.parameter]
        assert len(worker_changes) > 0
        assert worker_changes[0].new_value < 4

    def test_increase_workers_when_busy(self):
        from ax_to_postgres_etl.core.auto_tune import AutoTuner
        config = {"etl": {"parallel": {"workers": 4, "fetch_size": 5000, "commit_size": 50000}}}
        tuner = AutoTuner(config)
        results = tuner.analyze(
            speed_rows_per_sec=50000,
            avg_batch_time_ms=50,
            memory_usage_mb=100,
            workers_active_pct=90,  # High activity
            queue_full_pct=60,  # Queue often full
        )
        worker_changes = [r for r in results if "workers" in r.parameter]
        assert len(worker_changes) > 0
        assert worker_changes[0].new_value > 4

    def test_summary(self):
        from ax_to_postgres_etl.core.auto_tune import AutoTuner
        config = {"etl": {"parallel": {"workers": 4}}}
        tuner = AutoTuner(config)
        tuner.analyze(5000, 100, 100, 20, 60)
        summary = tuner.summary()
        assert "AUTO-TUNE RESULTS" in summary


# ============================================================
# P11: Webhook tests
# ============================================================

class TestWebhookNotifier:
    def test_notify_start(self):
        from ax_to_postgres_etl.core.webhook import WebhookNotifier, WebhookConfig
        config = WebhookConfig(url="http://test.com", enabled=True)
        notifier = WebhookNotifier(config)
        # Should not raise
        notifier.notify_start("test_table", "resume", 100, 4)

    def test_disabled_webhook(self):
        from ax_to_postgres_etl.core.webhook import WebhookNotifier, WebhookConfig
        config = WebhookConfig(url="http://test.com", enabled=False)
        notifier = WebhookNotifier(config)
        # Should not raise
        notifier.notify_start("test_table", "resume", 100, 4)


# ============================================================
# P11: ComparisonReport tests
# ============================================================

class TestRunComparison:
    def test_summary(self):
        from ax_to_postgres_etl.core.comparison_report import RunComparison
        r = RunComparison(
            table_name="test",
            current_run_id=2,
            previous_run_id=1,
            current_inserted=100000,
            current_elapsed=100.0,
            current_chunks=10,
            current_failed=0,
            current_speed=1000.0,
            previous_inserted=90000,
            previous_elapsed=110.0,
            previous_chunks=10,
            previous_failed=0,
            previous_speed=818.0,
        )
        summary = r.summary()
        assert "LOAD COMPARISON" in summary
        assert "test" in summary

    def test_diffs(self):
        from ax_to_postgres_etl.core.comparison_report import RunComparison
        r = RunComparison(
            table_name="test",
            current_run_id=2,
            previous_run_id=1,
            current_inserted=100000,
            previous_inserted=90000,
        )
        assert r.inserted_diff == 10000
        assert r.inserted_diff_pct == pytest.approx(11.11, rel=0.01)


# ============================================================
# P12: CLI tests
# ============================================================

class TestCLI:
    def test_parser_creation(self):
        from ax_to_postgres_etl.core.cli import create_parser
        parser = create_parser()
        assert parser is not None

    def test_load_command_parse(self):
        from ax_to_postgres_etl.core.cli import create_parser
        parser = create_parser()
        args = parser.parse_args(["load", "--table", "ALK_MARKSERIAL", "--mode", "resume"])
        assert args.command == "load"
        assert args.table == "ALK_MARKSERIAL"
        assert args.mode == "resume"

    def test_status_command_parse(self):
        from ax_to_postgres_etl.core.cli import create_parser
        parser = create_parser()
        args = parser.parse_args(["status", "--table", "test"])
        assert args.command == "status"
        assert args.table == "test"

    def test_validate_command_parse(self):
        from ax_to_postgres_etl.core.cli import create_parser
        parser = create_parser()
        args = parser.parse_args(["validate", "--table", "test", "--check", "duplicates"])
        assert args.command == "validate"
        assert args.check == "duplicates"


# ============================================================
# P12: QueryCache tests
# ============================================================

class TestQueryCache:
    def test_set_get(self):
        from ax_to_postgres_etl.core.query_cache import QueryCache
        cache = QueryCache()
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_expired_entry(self):
        from ax_to_postgres_etl.core.query_cache import QueryCache
        cache = QueryCache(default_ttl=0)
        cache.set("key1", "value1")
        time.sleep(0.01)
        assert cache.get("key1") is None

    def test_stats(self):
        from ax_to_postgres_etl.core.query_cache import QueryCache
        cache = QueryCache()
        cache.set("key1", "value1")
        cache.get("key1")
        cache.get("key2")
        stats = cache.stats
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 50.0

    def test_decorator(self):
        from ax_to_postgres_etl.core.query_cache import QueryCache
        cache = QueryCache()

        @cache.cached("test", ttl=60)
        def expensive_func(x):
            return x * 2

        result1 = expensive_func(5)
        result2 = expensive_func(5)
        assert result1 == 10
        assert result2 == 10
        assert cache.stats["hits"] == 1


# ============================================================
# P12: BatchProfiler tests
# ============================================================

class TestBatchProfiler:
    def test_profile_batch(self):
        from ax_to_postgres_etl.core.batch_profiler import BatchProfiler
        profiler = BatchProfiler()
        batch_num = profiler.start_batch(chunk_id=1, chunk_no=0)
        time.sleep(0.01)
        profiler.end_batch(rows_fetched=1000, rows_written=1000, fetch_time=0.005, write_time=0.005)

        assert profiler.total_batches == 1
        assert profiler.total_rows == 1000
        assert profiler.avg_speed > 0

    def test_summary(self):
        from ax_to_postgres_etl.core.batch_profiler import BatchProfiler
        profiler = BatchProfiler()
        profiler.start_batch(1, 0)
        profiler.end_batch(rows_written=1000)
        summary = profiler.summary()
        assert "BATCH PROFILING SUMMARY" in summary
        assert "Total batches:    1" in summary


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
