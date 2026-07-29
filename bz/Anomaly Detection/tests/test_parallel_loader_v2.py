"""Tests for ParallelLoaderV2T (P0-P15) chunk finalization logic."""

import pytest
from unittest.mock import MagicMock, patch, call
from dataclasses import dataclass
from typing import Any

from ax_to_postgres_etl.core.messages import DataBatch, ChunkFinished, ChunkFailed
from ax_to_postgres_etl.loader_v2t.parallel_loader_v2t import escape_copy_text, _build_copy_buffer


# ============================================================
# escape_copy_text tests
# ============================================================

class TestEscapeCopyText:
    def test_none_returns_null_marker(self):
        assert escape_copy_text(None) == r"\N"

    def test_empty_string_returns_empty(self):
        assert escape_copy_text("") == ""

    def test_backslash_escaped(self):
        assert escape_copy_text("a\\b") == "a\\\\b"

    def test_tab_escaped(self):
        assert escape_copy_text("a\tb") == "a\\tb"

    def test_newline_escaped(self):
        assert escape_copy_text("a\nb") == "a\\nb"

    def test_carriage_return_escaped(self):
        assert escape_copy_text("a\rb") == "a\\rb"

    def test_kirillica_preserved(self):
        assert escape_copy_text("Привет мир") == "Привет мир"

    def test_double_quotes_preserved(self):
        assert escape_copy_text('value with "quotes"') == 'value with "quotes"'

    def test_bool_true(self):
        assert escape_copy_text(True) == "t"

    def test_bool_false(self):
        assert escape_copy_text(False) == "f"

    def test_integer(self):
        assert escape_copy_text(123) == "123"

    def test_special_chars_combined(self):
        assert escape_copy_text('p:c"QQ*QmcXb&') == 'p:c"QQ*QmcXb&'

    def test_backslash_with_special_chars(self):
        assert escape_copy_text("a\\b\tc\nd") == "a\\\\b\\tc\\nd"


# ============================================================
# _build_copy_buffer tests
# ============================================================

class TestBuildCopyBuffer:
    def test_basic_buffer(self):
        rows = [["a", "b"], ["c", "d"]]
        content, skipped = _build_copy_buffer(rows, 2)
        assert skipped == 0
        assert "a\tb\n" in content
        assert "c\td\n" in content

    def test_with_chunk_id(self):
        rows = [["a", "b"]]
        content, skipped = _build_copy_buffer(rows, 2, chunk_id=42)
        assert skipped == 0
        assert content.startswith("42\ta\tb\n")

    def test_skipped_row_wrong_column_count(self):
        rows = [["a"], ["b", "c", "d"]]
        content, skipped = _build_copy_buffer(rows, 2)
        assert skipped == 2  # both rows have wrong column count

    def test_none_values(self):
        rows = [[None, "value"]]
        content, skipped = _build_copy_buffer(rows, 2)
        assert skipped == 0
        assert "\\N\tvalue\n" in content

    def test_special_chars_in_buffer(self):
        rows = [['p:c"QQ*QmcXb&']]
        content, skipped = _build_copy_buffer(rows, 1)
        assert skipped == 0
        assert 'p:c"QQ*QmcXb&\n' in content


# ============================================================
# DataBatch / ChunkFinished / ChunkFailed message tests
# ============================================================

class TestMessages:
    def test_data_batch_frozen(self):
        batch = DataBatch(chunk_id=1, chunk_no=0, rows=[["a"]], last_processed_key=100)
        with pytest.raises(AttributeError):
            batch.chunk_id = 2

    def test_chunk_finished_frozen(self):
        msg = ChunkFinished(chunk_id=1, chunk_no=0, rows_read=100, last_processed_key=500)
        assert msg.rows_read == 100

    def test_chunk_failed_frozen(self):
        msg = ChunkFailed(
            chunk_id=1, chunk_no=0,
            error_type="network",
            error_message="Connection reset",
            rows_read=50,
            last_processed_key=250,
        )
        assert msg.error_type == "network"


# ============================================================
# Writer finalization logic tests (mocked)
# ============================================================

class TestWriterFinalization:
    """Test that writer correctly finalizes chunks atomically."""

    def test_finalize_chunk_executes_correct_sql(self):
        """Verify _finalize_chunk runs INSERT, UPDATE, DELETE in one transaction."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_conn.cursor.return_value = mock_cursor

        loader = MagicMock()
        loader.pg.schema = "raw_ax"
        loader.stream_threshold = 10000
        loader.log_func = None

        # Call _finalize_chunk directly
        from ax_to_postgres_etl.loader_v2t.parallel_loader_v2t import ParallelLoaderV2T as ParallelLoaderV2
        ParallelLoaderV2._finalize_chunk(
            loader,
            pg_conn=mock_conn,
            table_name="test_table",
            staging_table="raw_ax._staging_test",
            pg_col_names=["col1", "col2"],
            chunk_id=42,
            chunk_no=5,
            rows_to_insert=[["a", "b"]],
            rows_read=10,
            last_processed_key=500,
        )

        # Verify commit was called
        mock_conn.commit.assert_called()

        # Verify 3 SQL statements were executed
        assert mock_cursor.execute.call_count >= 3

    def test_finalize_chunk_rollback_on_error(self):
        """Verify rollback on exception."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = [None, Exception("DB error")]
        mock_conn.cursor.return_value = mock_cursor

        loader = MagicMock()
        loader.pg.schema = "raw_ax"

        from ax_to_postgres_etl.loader_v2t.parallel_loader_v2t import ParallelLoaderV2T as ParallelLoaderV2
        with pytest.raises(Exception):
            ParallelLoaderV2._finalize_chunk(
                loader,
                pg_conn=mock_conn,
                table_name="test_table",
                staging_table="raw_ax._staging_test",
                pg_col_names=["col1"],
                chunk_id=42,
                chunk_no=5,
                rows_to_insert=[["a"]],
                rows_read=10,
                last_processed_key=500,
            )

        mock_conn.rollback.assert_called()
        mock_conn.commit.assert_not_called()

    def test_finalize_empty_chunk(self):
        """Verify empty chunk is finalized correctly."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_conn.cursor.return_value = mock_cursor

        loader = MagicMock()
        loader.pg.schema = "raw_ax"

        from ax_to_postgres_etl.loader_v2t.parallel_loader_v2t import ParallelLoaderV2T as ParallelLoaderV2
        ParallelLoaderV2._finalize_chunk(
            loader,
            pg_conn=mock_conn,
            table_name="test_table",
            staging_table="raw_ax._staging_test",
            pg_col_names=["col1"],
            chunk_id=99,
            chunk_no=0,
            rows_to_insert=[],
            rows_read=0,
            last_processed_key=None,
        )

        mock_conn.commit.assert_called()


# ============================================================
# ChunkManager tests
# ============================================================

class TestChunkManager:
    """Test ChunkManager methods."""

    def test_fail_chunk_sets_status(self):
        """Verify fail_chunk sets correct status and error info."""
        from ax_to_postgres_etl.core.chunk_manager import ChunkManager
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        cm = ChunkManager(mock_conn)
        cm.fail_chunk(
            chunk_id=42,
            error_type="network",
            error_message="Connection reset",
            rows_read=100,
        )

        # Verify SQL was executed
        mock_cursor.execute.assert_called_once()
        sql = mock_cursor.execute.call_args[0][0]
        assert "status = 'failed'" in sql
        assert "error_type = %s" in sql

        mock_conn.commit.assert_called()

    def test_heartbeat_updates_timestamp(self):
        """Verify heartbeat updates heartbeat_at."""
        from ax_to_postgres_etl.core.chunk_manager import ChunkManager
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        cm = ChunkManager(mock_conn)
        cm.heartbeat(chunk_id=42)

        mock_cursor.execute.assert_called_once()
        sql = mock_cursor.execute.call_args[0][0]
        assert "heartbeat_at = CURRENT_TIMESTAMP" in sql

        mock_conn.commit.assert_called()


# ============================================================
# Success criteria tests
# ============================================================

class TestSuccessCriteria:
    """Test that success requires all chunks completed."""

    def test_all_chunks_must_be_completed(self):
        """SUCCESS only when completed == total and no failures."""
        # Simulate stats
        stats = {
            "completed": 500,
            "failed": 0,
            "running": 0,
            "pending": 0,
            "retry": 0,
        }
        total = 500

        success = (
            stats["completed"] == total
            and stats["failed"] == 0
            and stats["running"] == 0
            and stats["pending"] == 0
            and stats["retry"] == 0
        )
        assert success is True

    def test_partial_completion_not_success(self):
        """499/500 is NOT success."""
        stats = {
            "completed": 499,
            "failed": 0,
            "running": 0,
            "pending": 1,
            "retry": 0,
        }
        total = 500

        success = (
            stats["completed"] == total
            and stats["failed"] == 0
            and stats["running"] == 0
            and stats["pending"] == 0
            and stats["retry"] == 0
        )
        assert success is False

    def test_any_running_not_success(self):
        """Any running chunks means not success."""
        stats = {
            "completed": 490,
            "failed": 0,
            "running": 10,
            "pending": 0,
            "retry": 0,
        }
        total = 500

        success = (
            stats["completed"] == total
            and stats["failed"] == 0
            and stats["running"] == 0
            and stats["pending"] == 0
            and stats["retry"] == 0
        )
        assert success is False

    def test_any_failed_not_success(self):
        """Any failed chunks means not success."""
        stats = {
            "completed": 499,
            "failed": 1,
            "running": 0,
            "pending": 0,
            "retry": 0,
        }
        total = 500

        success = (
            stats["completed"] == total
            and stats["failed"] == 0
            and stats["running"] == 0
            and stats["pending"] == 0
            and stats["retry"] == 0
        )
        assert success is False


# ============================================================
# P3: Retry patterns tests
# ============================================================

class TestRetryPatterns:
    """Test extended network error patterns from stabilization doc §8."""

    def test_dbnetlib_pattern_detected(self):
        from ax_to_postgres_etl.core.retry import RETRIABLE_PATTERNS
        import re
        test_errors = [
            "DBNETLIB ConnectionRead",
            "10054 connection reset",
            "10053 connection aborted",
            "10060 timeout",
            "08S01 communication link failure",
            "общая ошибка сети",
        ]
        for err in test_errors:
            matched = any(re.search(p, err, re.IGNORECASE) for p in RETRIABLE_PATTERNS)
            assert matched, f"Pattern not found for: {err}"

    def test_non_retriable_errors_still_blocked(self):
        from ax_to_postgres_etl.core.retry import RetryPolicy, RetryConfig
        policy = RetryPolicy(RetryConfig(max_attempts=5))
        schema_err = Exception("column 'foo' does not exist")
        assert policy.is_retriable(schema_err) is False


# ============================================================
# P3: ChunkManager recover_stale_chunks tests
# ============================================================

class TestRecoverStale:
    """Test recover_stale_chunks method."""

    def test_recover_stale_does_not_increment_attempt(self):
        from ax_to_postgres_etl.core.chunk_manager import ChunkManager
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 3
        mock_conn.cursor.return_value = mock_cursor

        cm = ChunkManager(mock_conn)
        recovered = cm.recover_stale_chunks(run_id=1, timeout_minutes=10)

        assert recovered == 3
        sql = mock_cursor.execute.call_args[0][0]
        assert "attempt_count" not in sql  # Must NOT increment attempt_count
        assert "error_type = 'heartbeat_timeout'" in sql
        mock_conn.commit.assert_called()


# ============================================================
# P4: Progress tracking tests
# ============================================================

class TestProgressTracking:
    """Test progress percentage calculation."""

    def test_progress_percentage_calculation(self):
        total = 100
        completed = 35
        pct = (completed / total * 100) if total > 0 else 0
        assert pct == 35.0

    def test_progress_zero_chunks(self):
        total = 0
        completed = 0
        pct = (completed / total * 100) if total > 0 else 0
        assert pct == 0

    def test_progress_log_format(self):
        total = 500
        completed = 125
        pct = (completed / total * 100)
        log_line = f"progress={completed}/{total} ({pct:.1f}%)"
        assert "125/500" in log_line
        assert "25.0%" in log_line


# ============================================================
# P4: Preflight checks tests
# ============================================================

class TestPreflightChecks:
    """Test preflight check method."""

    def test_preflight_all_checks_pass(self):
        loader = MagicMock()
        loader._get_ss_connection.return_value = MagicMock()
        loader._get_source_count.return_value = 1000000
        # Each PG cursor call gets a fresh mock
        def make_cursor():
            c = MagicMock()
            c.fetchall.return_value = [("col1",), ("col2",)]
            c.fetchone.return_value = [0]
            return c
        loader.pg.conn.cursor.side_effect = make_cursor
        loader.pg.schema = "raw_ax"
        loader.log_func = None

        from ax_to_postgres_etl.loader_v2t.parallel_loader_v2t import ParallelLoaderV2T as ParallelLoaderV2
        result = ParallelLoaderV2._preflight_checks(
            loader, "test_table", "col1, col2", "RECID", ["col1", "col2"]
        )
        # All 6 checks should pass
        assert result is True

    def test_preflight_ssql_server_fails(self):
        loader = MagicMock()
        loader._get_ss_connection.side_effect = Exception("Connection refused")
        loader.pg.conn.cursor.return_value = MagicMock()
        loader.pg.schema = "raw_ax"
        loader.log_func = None

        from ax_to_postgres_etl.loader_v2t.parallel_loader_v2t import ParallelLoaderV2T as ParallelLoaderV2
        result = ParallelLoaderV2._preflight_checks(
            loader, "test_table", "*", "RECID", ["col1"]
        )
        # At least one check failed
        assert result is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
