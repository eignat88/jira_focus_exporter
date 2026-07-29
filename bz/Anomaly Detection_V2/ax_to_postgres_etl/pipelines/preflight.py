"""Preflight checks for RAW -> DDS pipeline.

Read-only diagnostic module. Does NOT create records in etl.load_run,
does NOT acquire advisory locks, does NOT modify any data.

Usage:
    from .preflight import PreflightRunner
    runner = PreflightRunner(conn, stage_config, pipeline_config)
    report = runner.run(batch_size=500000, count_mode="estimate")
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any


class CheckStatus(str, Enum):
    OK = "OK"
    WARN = "WARN"
    ERROR = "ERROR"


class PreflightResult(str, Enum):
    READY = "READY"
    READY_WITH_WARNINGS = "READY_WITH_WARNINGS"
    BLOCKED = "BLOCKED"


@dataclass
class Check:
    name: str
    status: CheckStatus
    message: str
    details: str = ""

    @property
    def passed(self) -> bool:
        return self.status != CheckStatus.ERROR


@dataclass
class PreflightReport:
    timestamp: str
    stage: str
    source: str
    target: str
    batch_size: int
    count_mode: str
    checks: list[Check] = field(default_factory=list)
    result: PreflightResult = PreflightResult.READY
    warnings: int = 0
    errors: int = 0
    plan_summary: str = ""
    source_rows_estimate: int | None = None
    source_total_size: str = ""
    target_current_size: str = ""
    free_disk: str = ""
    wal_risk: str = ""

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "stage": self.stage,
            "source": self.source,
            "target": self.target,
            "batch_size": self.batch_size,
            "count_mode": self.count_mode,
            "result": self.result.value,
            "warnings": self.warnings,
            "errors": self.errors,
            "checks": [asdict(c) for c in self.checks],
            "plan_summary": self.plan_summary,
            "source_rows_estimate": self.source_rows_estimate,
            "source_total_size": self.source_total_size,
            "target_current_size": self.target_current_size,
            "free_disk": self.free_disk,
            "wal_risk": self.wal_risk,
        }


def _check_exists(cur, schema: str, table: str) -> bool:
    """Check if a table exists via pg_catalog (no information_schema)."""
    cur.execute(
        "SELECT EXISTS (SELECT 1 FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = %s AND c.relname = %s AND c.relkind = 'r')",
        (schema, table),
    )
    return cur.fetchone()[0]


def _check_column_exists(cur, schema: str, table: str, column: str) -> bool:
    """Check if column exists via pg_attribute."""
    cur.execute(
        "SELECT EXISTS ("
        "SELECT 1 FROM pg_attribute a "
        "JOIN pg_class c ON c.oid = a.attrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = %s AND c.relname = %s "
        "AND a.attname = %s AND a.attnum > 0 AND NOT a.attisdropped"
        ")",
        (schema, table, column),
    )
    return cur.fetchone()[0]


def _get_column_type(cur, schema: str, table: str, column: str) -> str | None:
    """Get column type name via pg_catalog."""
    cur.execute(
        "SELECT t.typname FROM pg_attribute a "
        "JOIN pg_class c ON c.oid = a.attrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "JOIN pg_type t ON t.oid = a.atttypid "
        "WHERE n.nspname = %s AND c.relname = %s AND a.attname = %s "
        "AND a.attnum > 0 AND NOT a.attisdropped",
        (schema, table, column),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _check_column_nullable(cur, schema: str, table: str, column: str) -> bool:
    """Check if column is nullable."""
    cur.execute(
        "SELECT a.attnotnull FROM pg_attribute a "
        "JOIN pg_class c ON c.oid = a.attrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = %s AND c.relname = %s AND a.attname = %s "
        "AND a.attnum > 0",
        (schema, table, column),
    )
    row = cur.fetchone()
    return not row[0] if row else True


def _first_index_attnum(indkey: Any) -> int:
    """Return the first table attribute number from pg_index.indkey safely."""
    if indkey is None:
        return 0

    if isinstance(indkey, str):
        parts = indkey.strip().split()
        return int(parts[0]) if parts else 0

    if isinstance(indkey, (list, tuple)):
        return int(indkey[0]) if indkey else 0

    try:
        values = list(indkey)
    except TypeError:
        parts = str(indkey).strip().split()
        return int(parts[0]) if parts else 0

    return int(values[0]) if values else 0


def _find_btree_index(cur, schema: str, table: str, column: str) -> dict | None:
    """Find a valid B-tree index where column is the first table key."""
    cur.execute(
        """
        SELECT
            i.indrelid,
            i.indexrelid,
            i.indkey,
            i.indisvalid,
            i.indisready,
            c2.relname AS index_name,
            pg_get_indexdef(i.indexrelid) AS index_def
        FROM pg_index i
        JOIN pg_class c ON c.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_class c2 ON c2.oid = i.indexrelid
        JOIN pg_am am ON am.oid = c2.relam
        WHERE n.nspname = %s
          AND c.relname = %s
          AND am.amname = 'btree'
        ORDER BY i.indisprimary DESC, i.indisunique DESC
        """,
        (schema, table),
    )

    for row in cur.fetchall():
        table_oid, index_oid, indkey, isvalid, isready, idx_name, idx_def = row

        first_key = _first_index_attnum(indkey)

        # Expression indexes have attnum=0 and are not a direct column match.
        if not first_key:
            continue

        cur.execute(
            """
            SELECT attname
            FROM pg_attribute
            WHERE attrelid = %s
              AND attnum = %s
              AND attnum > 0
              AND NOT attisdropped
            """,
            (table_oid, first_key),
        )
        col_row = cur.fetchone()
        first_col = col_row[0] if col_row else ""

        if first_col == column:
            return {
                "name": idx_name,
                "definition": idx_def,
                "is_valid": isvalid,
                "is_ready": isready,
                "usable_for_chunking": bool(isvalid and isready),
            }

    return None


def _find_unique_constraint(cur, schema: str, table: str, column: str) -> dict | None:
    """Find unique constraint or unique index on column.

    Accepts: primary key, unique constraint, or unique index
    where the column is the first (and ideally only) key.
    """
    # 1) Check pg_constraint (primary key / unique constraint)
    cur.execute(
        "SELECT c.conname, pg_get_constraintdef(c.oid) AS constraint_def, "
        "c.convalidated, c.conkey "
        "FROM pg_constraint c "
        "JOIN pg_class cl ON cl.oid = c.conrelid "
        "JOIN pg_namespace n ON n.oid = cl.relnamespace "
        "WHERE n.nspname = %s AND cl.relname = %s "
        "AND c.contype IN ('u', 'p') "
        "ORDER BY c.contype",
        (schema, table),
    )
    for row in cur.fetchall():
        conname, condef, convalidated, conkey = row
        # Resolve column names from conkey
        cur.execute(
            "SELECT attname FROM pg_attribute "
            "WHERE attrelid = (SELECT oid FROM pg_class WHERE relname = %s "
            "AND relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = %s)) "
            "AND attnum = ANY(%s)",
            (table, schema, conkey)
        )
        cols = [r[0] for r in cur.fetchall()]
        if column in cols:
            return {
                "name": conname,
                "definition": condef,
                "is_valid": bool(convalidated),
            }

    # 2) Check unique indexes (pg_index + pg_class)
    cur.execute(
        "SELECT c2.relname AS index_name, "
        "pg_get_indexdef(i.indexrelid) AS index_def, "
        "i.indisvalid, i.indisready, i.indisunique, i.indkey "
        "FROM pg_index i "
        "JOIN pg_class c ON c.oid = i.indrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "JOIN pg_class c2 ON c2.oid = i.indexrelid "
        "WHERE n.nspname = %s AND c.relname = %s "
        "AND i.indisunique = true "
        "ORDER BY i.indisprimary DESC",
        (schema, table),
    )
    for row in cur.fetchall():
        idx_name, idx_def, isvalid, isready, _, indkey = row
        # Resolve first column of the index
        first_key = _first_index_attnum(indkey)

        cur.execute(
            "SELECT attname FROM pg_attribute "
            "WHERE attrelid = (SELECT oid FROM pg_class WHERE relname = %s "
            "AND relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = %s)) "
            "AND attnum = %s",
            (table, schema, first_key)
        )
        col_row = cur.fetchone()
        first_col = col_row[0] if col_row else ""

        if first_col == column and isvalid and isready:
            return {
                "name": idx_name,
                "definition": idx_def,
                "is_valid": True,
            }

    return None


def _get_table_size(cur, schema: str, table: str) -> dict:
    """Get relation sizes safely through regclass without scanning table data."""
    relation_name = f"{schema}.{table}"

    cur.execute(
        """
        SELECT
            pg_total_relation_size(%s::regclass),
            pg_relation_size(%s::regclass),
            pg_indexes_size(%s::regclass)
        """,
        (relation_name, relation_name, relation_name),
    )

    total, heap, indexes = cur.fetchone()

    return {
        "total": int(total or 0),
        "heap": int(heap or 0),
        "indexes": int(indexes or 0),
    }


def _get_estimated_rows(cur, schema: str, table: str) -> int | None:
    """Get estimated row count with fallback chain.

    1. pg_stat_user_tables.n_live_tup (most accurate if ANALYZE ran)
    2. pg_class.reltuples ( planner estimate, may be -1 )
    3. If table is large by size but rows==0, return None (unreliable stats)
    """
    # Try n_live_tup first
    cur.execute(
        "SELECT n_live_tup FROM pg_stat_user_tables "
        "WHERE schemaname = %s AND relname = %s",
        (schema, table),
    )
    row = cur.fetchone()
    if row and row[0] and row[0] > 0:
        return int(row[0])

    # Fallback to pg_class.reltuples
    cur.execute(
        "SELECT c.reltuples::bigint FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = %s AND c.relname = %s",
        (schema, table),
    )
    row = cur.fetchone()
    if row and row[0] and row[0] > 0:
        return int(row[0])

    # If reltuples is 0 or -1 but table is large by size, stats are unreliable
    # Return None to signal "unknown" — caller should use size as fallback
    return None


def _is_large_table(cur, schema: str, table: str) -> bool:
    """Check if table is large (>10GB) regardless of row statistics."""
    size = _get_table_size(cur, schema, table)
    return size["total"] > 10 * 1024 * 1024 * 1024  # 10 GB


def _get_last_analyze(cur, schema: str, table: str) -> str | None:
    """Get last analyze/vacuum time."""
    cur.execute(
        "SELECT GREATEST(last_analyze, last_autoanalyze) "
        "FROM pg_stat_user_tables "
        "WHERE schemaname = %s AND relname = %s",
        (schema, table),
    )
    row = cur.fetchone()
    return str(row[0]) if row and row[0] else None


def _format_bytes(size_bytes: int) -> str:
    """Human-readable file size."""
    if size_bytes == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    size = float(size_bytes)
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.1f} {units[i]}"


def _estimate_wal_risk(rows: int | None, avg_row_width: int, total_size: int = 0) -> str:
    """Estimate WAL risk category.

    Considers row count AND total table size. A 78GB table with n_live_tup=0
    (stale stats) is still HIGH risk.
    """
    # HIGH risk if table size > 50 GB regardless of row count
    if total_size > 50 * 1024 * 1024 * 1024:
        return "HIGH"

    # HIGH risk if rows > 50M
    if rows is not None and rows > 50_000_000:
        return "HIGH"

    # MEDIUM risk if table size > 10 GB
    if total_size > 10 * 1024 * 1024 * 1024:
        return "MEDIUM"

    # MEDIUM risk if rows > 10M
    if rows is not None and rows > 10_000_000:
        return "MEDIUM"

    if rows is None and total_size > 1024 * 1024 * 1024:
        return "MEDIUM"

    return "LOW"


class PreflightRunner:
    """Execute preflight checks for a single stage."""

    LARGE_TABLE_THRESHOLD = 10_000_000  # rows
    DISK_SAFETY_MULTIPLIER = 1.5

    def __init__(self, conn, stage_config: dict, pipeline_config: dict):
        self.conn = conn
        self.stage = stage_config
        self.pipeline = pipeline_config
        self.checks: list[Check] = []
        self._stage_name = stage_config.get("name", "unknown")
        self._source_schema = stage_config.get("source", {}).get("schema", "")
        self._source_table = stage_config.get("source", {}).get("table", "")
        self._target_schema = stage_config.get("target", {}).get("schema", "")
        self._target_table = stage_config.get("target", {}).get("table", "")
        self._key_column = stage_config.get("source", {}).get("key_column", "")
        self._key_type = stage_config.get("source", {}).get("key_type", "bigint")
        self._conflict_key = stage_config.get("target", {}).get("conflict_key")
        self._columns = stage_config.get("columns", [])

    def _add(self, name: str, status: CheckStatus, message: str, details: str = ""):
        self.checks.append(Check(name=name, status=status, message=message, details=details))

    def _ok(self, name: str, message: str):
        self._add(name, CheckStatus.OK, message)

    def _warn(self, name: str, message: str):
        self._add(name, CheckStatus.WARN, message)

    def _error(self, name: str, message: str):
        self._add(name, CheckStatus.ERROR, message)

    # ── Configuration checks ────────────────────────────────────────

    def check_config(self):
        """Validate stage configuration completeness."""
        s = self.stage
        if not s.get("source"):
            self._error("config_source", "No source defined")
            return
        if not s.get("target"):
            self._error("config_target", "No target defined")
            return
        if not s.get("source", {}).get("key_column"):
            self._error("config_key", "No chunk key defined")
            return
        if not s.get("columns"):
            self._error("config_columns", "No column mapping defined")
            return
        self._ok("config_loaded", "Stage configuration loaded")

    # ── Schema / table existence ─────────────────────────────────────

    def check_source_exists(self):
        cur = self.conn.cursor()
        exists = _check_exists(cur, self._source_schema, self._source_table)
        if exists:
            self._ok("source_table_exists", f"{self._source_schema}.{self._source_table}")
        else:
            self._error("source_table_exists",
                        f"Source table {self._source_schema}.{self._source_table} not found")

    def check_target_exists(self):
        cur = self.conn.cursor()
        exists = _check_exists(cur, self._target_schema, self._target_table)
        if exists:
            self._ok("target_table_exists", f"{self._target_schema}.{self._target_table}")
        else:
            self._error("target_table_exists",
                        f"Target table {self._target_schema}.{self._target_table} not found")

    # ── Column checks ───────────────────────────────────────────────

    def check_columns(self):
        cur = self.conn.cursor()

        # Check chunk key column
        key_type = _get_column_type(cur, self._source_schema, self._source_table, self._key_column)
        if key_type is None:
            self._error("source_key_exists",
                        f"Chunk key column '{self._key_column}' not found in source")
        else:
            self._ok("source_key_exists",
                     f"Source key {self._key_column}: {key_type}")

        # The target business/conflict key is independent from the source
        # chunk key. Do not require them to have the same name or type.
        target_cfg = self.stage.get("target", {})
        target_key = target_cfg.get("conflict_key") or target_cfg.get("key_column")

        if target_key:
            target_key_type = _get_column_type(
                cur,
                self._target_schema,
                self._target_table,
                target_key,
            )
            if target_key_type is None:
                self._error(
                    "target_key_exists",
                    f"Target key column '{target_key}' not found",
                )
            else:
                self._ok(
                    "target_key_exists",
                    f"Target key {target_key}: {target_key_type}",
                )

        # Validate source chunk-key type against the chunk strategy.
        chunk_strategy = (
            self.stage.get("execution", {}).get("chunk_strategy")
            or self.stage.get("chunk_strategy")
            or "numeric_range"
        )
        if key_type:
            compatible, message = self._check_chunk_key_compatibility(
                key_type,
                chunk_strategy,
            )
            if compatible:
                self._ok("key_type_compatible", message)
            else:
                self._error("key_type_compatible", message)

        # Check all source columns from mapping
        for col in self._columns:
            # Parse expression to find source columns (simple heuristic)
            src_cols = self._extract_source_columns(col.get("expression", ""))
            for sc in src_cols:
                if not _check_column_exists(cur, self._source_schema, self._source_table, sc):
                    self._warn("source_column",
                               f"Source column '{sc}' referenced in mapping not found")

        # Check target columns
        for col in self._columns:
            target_col = col.get("target", "")
            if target_col and not _check_column_exists(cur, self._target_schema,
                                                       self._target_table, target_col):
                self._error("target_column",
                            f"Target column '{target_col}' not found")

        # Check conflict key exists on target
        if self._conflict_key:
            if _check_column_exists(cur, self._target_schema, self._target_table, self._conflict_key):
                self._ok("conflict_key_exists",
                         f"Conflict key {self._conflict_key} exists on target")
            else:
                self._error("conflict_key_exists",
                            f"Conflict key {self._conflict_key} not found on target")

    def _check_chunk_key_compatibility(
        self,
        source_type: str,
        chunk_strategy: str,
    ) -> tuple[bool, str]:
        """Validate source chunk-key type for the configured strategy."""
        numeric_types = {
            "int2", "int4", "int8",
            "smallint", "integer", "bigint",
            "numeric", "decimal",
        }
        timestamp_types = {
            "date", "timestamp", "timestamptz",
            "timestamp without time zone",
            "timestamp with time zone",
        }

        if chunk_strategy == "numeric_range":
            if source_type in numeric_types:
                return (
                    True,
                    f"Source chunk key type {source_type} is compatible "
                    f"with {chunk_strategy}",
                )
            return (
                False,
                f"Chunk strategy {chunk_strategy} requires a numeric source "
                f"key, got {source_type}",
            )

        if chunk_strategy in {"timestamp_range", "time_range"}:
            if source_type in timestamp_types:
                return (
                    True,
                    f"Source chunk key type {source_type} is compatible "
                    f"with {chunk_strategy}",
                )
            return (
                False,
                f"Chunk strategy {chunk_strategy} requires a timestamp/date "
                f"source key, got {source_type}",
            )

        return (
            True,
            f"Source chunk key type {source_type}; strategy "
            f"{chunk_strategy} accepted for adapter validation",
        )

    def _check_type_compatibility(self, source_type: str, target_type: str) -> bool:
        """Check if source type is compatible with target type."""
        numeric = {"int2", "int4", "int8", "bigint", "integer", "smallint"}
        text = {"text", "varchar", "char", "character varying"}
        ts = {"timestamp", "timestamp without time zone", "timestamptz",
              "timestamp with time zone", "date"}

        if source_type in numeric and target_type in numeric:
            return True
        if source_type in text and target_type in text:
            return True
        if source_type in ts and target_type in ts:
            return True
        # Special: bigint_text uses pre-computed column
        if self._key_type == "bigint_text" and target_type in numeric:
            return True
        return source_type == target_type

    def _extract_source_columns(self, expression: str) -> list[str]:
        """Simple heuristic to extract source column names from expression."""
        import re
        # Match src.column or column patterns
        matches = re.findall(r'src\.(\w+)', expression)
        return matches

    # ── Index checks ────────────────────────────────────────────────

    def check_indexes(self):
        cur = self.conn.cursor()

        # Check B-tree index on source for chunk key
        key_col = self._key_column
        if self._key_type == "bigint_text":
            key_col = "recid_bigint"

        idx = _find_btree_index(cur, self._source_schema, self._source_table, key_col)
        if idx:
            status_msg = (f"Index: {idx['name']}\n"
                         f"Definition: {idx['definition']}\n"
                         f"Valid: {'yes' if idx['is_valid'] else 'NO'}\n"
                         f"Ready: {'yes' if idx['is_ready'] else 'NO'}\n"
                         f"Usable for chunking: {'yes' if idx['usable_for_chunking'] else 'NO'}")
            if idx["usable_for_chunking"]:
                self._ok("source_btree_index", status_msg)
            else:
                self._error("source_btree_index",
                            f"Index {idx['name']} is not valid/ready")
        else:
            # Check table size — Seq Scan only matters for large tables
            size_info = _get_table_size(cur, self._source_schema, self._source_table)
            est_rows = _get_estimated_rows(cur, self._source_schema, self._source_table)
            is_large = (est_rows and est_rows > self.LARGE_TABLE_THRESHOLD) or \
                       size_info["total"] > 10 * 1024 * 1024 * 1024  # 10 GB fallback
            if is_large:
                row_desc = f"~{_fmt_num(est_rows)} rows" if est_rows else "unknown rows"
                self._error("source_btree_index",
                            f"No B-tree index on '{key_col}' for large table "
                            f"({row_desc}, {_format_bytes(size_info['total'])})")
            else:
                self._warn("source_btree_index",
                           f"No B-tree index found on '{key_col}' (small table)")

        # Check unique constraint on target for conflict key
        if self._conflict_key:
            uq = _find_unique_constraint(cur, self._target_schema,
                                         self._target_table, self._conflict_key)
            if uq:
                self._ok("target_unique_constraint",
                         f"Unique constraint: {uq['name']}")
            else:
                self._error("target_unique_constraint",
                            f"No unique constraint on {self._conflict_key}")

    # ── Query plan (EXPLAIN without ANALYZE) ────────────────────────

    def check_query_plan(self, batch_size: int):
        """Run EXPLAIN without ANALYZE for one representative chunk.

        For numeric_text_range the source key remains text in PostgreSQL.
        Boundaries are calculated numerically in Python, then passed back as
        strings so the predicate can use the ordinary B-tree index directly.
        """
        cur = self.conn.cursor()
        key_col = self._key_column
        if self._key_type == "bigint_text":
            key_col = "recid_bigint"

        chunk_strategy = (
            self.stage.get("execution", {}).get("chunk_strategy")
            or self.stage.get("chunk_strategy")
            or "numeric_range"
        )

        try:
            if chunk_strategy == "numeric_text_range" or self._key_type == "numeric_text":
                # Read the smallest text key through the existing B-tree index.
                # ORDER BY ... LIMIT 1 avoids a full aggregate scan.
                cur.execute(
                    f"SELECT {key_col} "
                    f"FROM {self._source_schema}.{self._source_table} "
                    f"WHERE {key_col} IS NOT NULL "
                    f"ORDER BY {key_col} "
                    f"LIMIT 1"
                )
                row = cur.fetchone()

                if not row or row[0] is None:
                    self._error(
                        "query_plan",
                        f"Cannot determine minimum key for {key_col}",
                    )
                    return

                try:
                    min_key = int(str(row[0]).strip())
                except (TypeError, ValueError):
                    self._error(
                        "query_plan",
                        f"Minimum key value {row[0]!r} is not numeric text",
                    )
                    return

                start_key = min_key - 1
                end_key = start_key + batch_size

                sql = (
                    f"EXPLAIN (FORMAT JSON) "
                    f"SELECT 1 FROM {self._source_schema}.{self._source_table} "
                    f"WHERE {key_col} > %s::text "
                    f"AND {key_col} <= %s::text "
                    f"ORDER BY {key_col} LIMIT %s"
                )
                params = (str(start_key), str(end_key), batch_size)
            else:
                sql = (
                    f"EXPLAIN (FORMAT JSON) "
                    f"SELECT 1 FROM {self._source_schema}.{self._source_table} "
                    f"WHERE {key_col} > %s "
                    f"AND {key_col} <= %s "
                    f"ORDER BY {key_col} LIMIT %s"
                )
                params = (0, batch_size, batch_size)

            cur.execute(sql, params)
            plan_json = cur.fetchone()[0]
            plan_text = json.dumps(plan_json, indent=2, ensure_ascii=False)
            node_type = self._extract_plan_node_type(plan_json)

            # For numeric-text chunking, an index scan alone is not enough.
            # The range predicate must appear in Index Cond, otherwise the
            # planner may scan most or all of the index and filter afterwards.
            if chunk_strategy == "numeric_text_range" or self._key_type == "numeric_text":
                plan_compact = json.dumps(plan_json, ensure_ascii=False)
                has_index_scan = node_type in (
                    "Index Scan",
                    "Index Only Scan",
                    "Bitmap Index Scan",
                )
                has_index_cond = (
                    '"Index Cond"' in plan_compact
                    and key_col in plan_compact
                    and ">" in plan_compact
                    and "<=" in plan_compact
                )

                if has_index_scan and has_index_cond:
                    self._ok(
                        "query_plan",
                        f"Plan uses {node_type}; range predicate is in Index Cond "
                        f"for [{start_key}, {end_key}]",
                    )
                elif node_type in ("Seq Scan", "Parallel Seq Scan"):
                    self._error(
                        "query_plan",
                        f"BLOCKING: {node_type} for numeric_text_range. "
                        f"Expected indexed range on {key_col}",
                    )
                else:
                    self._error(
                        "query_plan",
                        f"BLOCKING: plan uses {node_type}, but indexed range "
                        f"condition on {key_col} was not confirmed",
                    )
                return

            if node_type in ("Index Scan", "Index Only Scan", "Bitmap Index Scan"):
                self._ok("query_plan", f"Plan uses {node_type}")
            elif node_type in ("Bitmap Heap Scan",):
                self._warn("query_plan", f"Plan uses {node_type} (depends on selectivity)")
            elif node_type in ("Seq Scan", "Parallel Seq Scan"):
                # Check table size — use both row count and total size
                est_rows = _get_estimated_rows(cur, self._source_schema, self._source_table)
                size_info = _get_table_size(cur, self._source_schema, self._source_table)
                is_large = (est_rows and est_rows > self.LARGE_TABLE_THRESHOLD) or \
                           size_info["total"] > 10 * 1024 * 1024 * 1024
                if is_large:
                    row_desc = f"~{_fmt_num(est_rows)} rows" if est_rows else "unknown rows"
                    self._error(
                        "query_plan",
                        f"BLOCKING: {node_type} on large table "
                        f"({row_desc}, {_format_bytes(size_info['total'])})",
                    )
                else:
                    self._warn("query_plan", f"Plan uses {node_type} (small table)")
            else:
                self._warn("query_plan", f"Plan node type: {node_type}")
        except Exception as e:
            self._error("query_plan", f"EXPLAIN failed: {e}")

    def _extract_plan_node_type(self, plan_json: Any) -> str:
        """Recursively extract the deepest scan node type from EXPLAIN JSON.

        For plans like: Limit -> Sort -> Index Scan
        Returns: Index Scan (the actual data access method).
        """
        SCAN_TYPES = {
            "Seq Scan", "Parallel Seq Scan",
            "Index Scan", "Index Only Scan", "Bitmap Index Scan",
            "Bitmap Heap Scan", "Tid Scan", "Subquery Scan",
            "Function Scan", "CTE Scan", "WorkTable Scan",
        }

        def _find_scan(node: dict) -> str | None:
            node_type = node.get("Node Type", "")
            if node_type in SCAN_TYPES:
                return node_type
            for child in node.get("Plans", []):
                result = _find_scan(child)
                if result:
                    return result
            return None

        if isinstance(plan_json, tuple) and len(plan_json) == 1:
            plan_json = plan_json[0]

        if isinstance(plan_json, list) and len(plan_json) > 0:
            plan = plan_json[0].get("Plan", {})
            # Try to find the actual scan node first
            scan = _find_scan(plan)
            if scan:
                return scan
            # Fallback to top-level node type
            return plan.get("Node Type", "Unknown")
        return "Unknown"

    # ── Data volume estimation ───────────────────────────────────────

    def check_data_volume(self, count_mode: str):
        cur = self.conn.cursor()

        # Source estimates
        est_rows = _get_estimated_rows(cur, self._source_schema, self._source_table)
        src_size = _get_table_size(cur, self._source_schema, self._source_table)
        last_analyze = _get_last_analyze(cur, self._source_schema, self._source_table)

        if est_rows is not None:
            self._ok("source_rows_estimate", f"{_fmt_num(est_rows)} rows")
        else:
            self._warn("source_rows_estimate", "No estimate available (no ANALYZE?)")

        self._ok("source_size",
                 f"Total: {_format_bytes(src_size['total'])}, "
                 f"Heap: {_format_bytes(src_size['heap'])}, "
                 f"Indexes: {_format_bytes(src_size['indexes'])}")

        if last_analyze:
            self._ok("source_last_analyze", f"{last_analyze}")
        else:
            self._warn("source_last_analyze", "No ANALYZE recorded")

        # Target estimates
        tgt_size = _get_table_size(cur, self._target_schema, self._target_table)
        tgt_rows = _get_estimated_rows(cur, self._target_schema, self._target_table)

        self._ok("target_size",
                 f"Total: {_format_bytes(tgt_size['total'])}, "
                 f"Rows: {_fmt_num(tgt_rows) if tgt_rows else 'N/A'}")

        # WAL risk — consider both row count and table size
        avg_width = src_size["heap"] // max(est_rows or 1, 1) if est_rows else 100
        wal_risk = _estimate_wal_risk(est_rows, avg_width, src_size["total"])
        self._ok("wal_risk", f"WAL risk: {wal_risk}")

        # Count mode warning
        if count_mode == "exact":
            self._warn("count_mode",
                       "WARNING: exact count may scan the whole table")

    # ── PostgreSQL runtime checks ────────────────────────────────────

    def check_pg_runtime(self):
        cur = self.conn.cursor()

        # Active ETL runs on same stage (advisory lock check)
        lock_key = self.pipeline.get("advisory_lock_key", 1734500127)
        cur.execute("SELECT pg_try_advisory_lock(%s)", (lock_key,))
        can_lock = cur.fetchone()[0]
        if can_lock:
            self._ok("advisory_lock", "No conflicting ETL run")
            # Release immediately — we're read-only
            cur.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))
        else:
            self._error("advisory_lock", "Another ETL run is active")

        # Check for locks on target table
        cur.execute(
            "SELECT l.mode, a.pid, a.state, a.query "
            "FROM pg_locks l "
            "JOIN pg_class c ON c.oid = l.relation "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "JOIN pg_stat_activity a ON a.pid = l.pid "
            "WHERE n.nspname = %s AND c.relname = %s "
            "AND l.granted = false "
            "AND l.mode IN ('AccessExclusiveLock', 'ShareLock', 'ShareUpdateExclusiveLock') "
            "LIMIT 10",
            (self._target_schema, self._target_table),
        )
        lock_rows = cur.fetchall()
        if lock_rows:
            for mode, pid, state, query in lock_rows:
                self._warn("target_locks",
                           f"Lock {mode} held by PID {pid} ({state})")
        else:
            self._ok("target_locks", "No conflicting locks on target")

        # Check for active autovacuum on target
        cur.execute(
            "SELECT pid, relid::regclass "
            "FROM pg_stat_progress_vacuum "
            "WHERE relid = (SELECT oid FROM pg_class "
            "WHERE relname = %s AND relnamespace = "
            "(SELECT oid FROM pg_namespace WHERE nspname = %s))",
            (self._target_table, self._target_schema),
        )
        vac_rows = cur.fetchall()
        if vac_rows:
            for pid, rel in vac_rows:
                self._warn("autovacuum_active",
                           f"Autovacuum active on {rel}, PID {pid}")
        else:
            self._ok("autovacuum_active", "No active autovacuum on target")

        # Check for active CREATE INDEX
        cur.execute(
            "SELECT pid, phase, blocks_total, blocks_done "
            "FROM pg_stat_progress_create_index "
            "WHERE relid = (SELECT oid FROM pg_class "
            "WHERE relname = %s AND relnamespace = "
            "(SELECT oid FROM pg_namespace WHERE nspname = %s))",
            (self._target_table, self._target_schema),
        )
        idx_rows = cur.fetchall()
        if idx_rows:
            for pid, phase, total, done in idx_rows:
                self._warn("index_building",
                           f"CREATE INDEX in progress: PID {pid}, phase {phase}")
        else:
            self._ok("index_building", "No active CREATE INDEX on target")

        # Check long transactions (> 5 min)
        cur.execute(
            "SELECT pid, now() - xact_start AS duration, state, query "
            "FROM pg_stat_activity "
            "WHERE state != 'idle' "
            "AND xact_start IS NOT NULL "
            "AND now() - xact_start > interval '5 minutes' "
            "LIMIT 10"
        )
        long_tx = cur.fetchall()
        if long_tx:
            for pid, dur, state, query in long_tx:
                self._warn("long_transactions",
                           f"Long transaction: PID {pid}, duration {dur}, {state}")
        else:
            self._ok("long_transactions", "No long transactions")

    # ── Disk space check ─────────────────────────────────────────────

    def check_disk_space(self, expected_growth_bytes: int = 0):
        cur = self.conn.cursor()

        try:
            cur.execute("SHOW data_directory")
            data_dir = cur.fetchone()[0]
        except Exception:
            self._warn("disk_space", "Cannot determine PostgreSQL data directory")
            return

        # Get disk free space
        try:
            usage = shutil.disk_usage(data_dir)
            free_bytes = usage.free
            free_str = _format_bytes(free_bytes)
        except Exception as e:
            self._warn("disk_space", f"Cannot check disk space: {e}")
            return

        # Estimate required space
        if expected_growth_bytes > 0:
            required = int(expected_growth_bytes * self.DISK_SAFETY_MULTIPLIER)
            required_str = _format_bytes(required)
            if free_bytes >= required:
                self._ok("disk_space",
                         f"Free: {free_str}, Required: ~{required_str} — OK")
            else:
                self._error("disk_space",
                            f"INSUFFICIENT: Free {free_str} < Required ~{required_str}")
        else:
            self._ok("disk_space", f"Free space: {free_str}")

    # ── Execution plan summary ───────────────────────────────────────

    def print_execution_plan(self, batch_size: int, count_mode: str, truncate: bool):
        """Print execution plan before modification modes (full, resume)."""
        source = f"{self._source_schema}.{self._source_table}"
        target = f"{self._target_schema}.{self._target_table}"
        print(f"\nExecution plan:")
        print(f"  Stage: {self._stage_name}")
        print(f"  Source: {source}")
        print(f"  Target: {target}")
        print(f"  Batch size: {batch_size}")
        print(f"  Strategy: keyset pagination")
        print(f"  Conflict mode: {'ON CONFLICT DO NOTHING' if self._conflict_key else 'none'}")
        print(f"  Target truncate: {'YES' if truncate else 'NO'}")
        print(f"  Resume supported: YES")

    # ── Main runner ──────────────────────────────────────────────────

    def run(self, batch_size: int = 250_000, count_mode: str = "estimate") -> PreflightReport:
        """Execute all preflight checks and return report."""
        self.checks = []
        ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

        source = f"{self._source_schema}.{self._source_table}"
        target = f"{self._target_schema}.{self._target_table}"

        report = PreflightReport(
            timestamp=ts,
            stage=self._stage_name,
            source=source,
            target=target,
            batch_size=batch_size,
            count_mode=count_mode,
        )

        # Execute checks in order
        self.check_config()

        # Only proceed with DB checks if config is valid
        if any(c.status == CheckStatus.ERROR for c in self.checks):
            report.checks = self.checks
            report.result = PreflightResult.BLOCKED
            report.errors = sum(1 for c in self.checks if c.status == CheckStatus.ERROR)
            return report

        self.check_source_exists()

        # Only continue if source exists
        if any(c.status == CheckStatus.ERROR and c.name == "source_table_exists"
               for c in self.checks):
            report.checks = self.checks
            report.result = PreflightResult.BLOCKED
            report.errors = sum(1 for c in self.checks if c.status == CheckStatus.ERROR)
            return report

        self.check_target_exists()
        self.check_columns()
        self.check_indexes()
        self.check_query_plan(batch_size)
        self.check_data_volume(count_mode)
        self.check_pg_runtime()
        self.check_disk_space()

        # Finalize report
        report.checks = self.checks
        report.warnings = sum(1 for c in self.checks if c.status == CheckStatus.WARN)
        report.errors = sum(1 for c in self.checks if c.status == CheckStatus.ERROR)

        if report.errors > 0:
            report.result = PreflightResult.BLOCKED
        elif report.warnings > 0:
            report.result = PreflightResult.READY_WITH_WARNINGS
        else:
            report.result = PreflightResult.READY

        return report


def _fmt_num(n: int | None) -> str:
    """Format number with thousands separator."""
    if n is None:
        return "N/A"
    return f"{n:,}"


def print_report(report: PreflightReport):
    """Print preflight report to console."""
    print(f"\n{'=' * 70}")
    print("RAW -> DDS PREFLIGHT")
    print(f"{'=' * 70}\n")
    print(f"Stage: {report.stage}")
    print(f"Source: {report.source}")
    print(f"Target: {report.target}")
    print(f"Batch size: {report.batch_size:,}")
    print(f"Count mode: {report.count_mode}")
    print()

    for check in report.checks:
        prefix = f"[{check.status.value}]"
        # Truncate multi-line messages for console
        msg_lines = check.message.split("\n")
        first_line = msg_lines[0]
        if len(msg_lines) > 1:
            first_line += f" (+{len(msg_lines) - 1} details)"
        print(f"{prefix:<8} {check.name}: {first_line}")

    print(f"\n{'-' * 70}")
    print(f"Result: {report.result.value}")
    print(f"Checks: {len(report.checks)}")
    print(f"Warnings: {report.warnings}")
    print(f"Errors: {report.errors}")
    print(f"No records created in etl.load_run")
    print(f"No data modified")
    print(f"{'-' * 70}")


def save_json_report(report: PreflightReport, logs_dir: str = "logs"):
    """Save preflight report as JSON."""
    os.makedirs(logs_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"preflight_{report.stage}_{ts}.json"
    filepath = os.path.join(logs_dir, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, indent=2, ensure_ascii=False)

    return filepath
