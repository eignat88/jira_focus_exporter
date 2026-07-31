from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .contracts import BatchResult, PipelineSpec

_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def ident(value: str) -> str:
    """Validate and quote SQL identifier."""
    if not _SAFE_IDENT.fullmatch(value):
        raise ValueError(f"Unsafe SQL identifier: {value!r}")
    return f'"{value}"'


@dataclass(frozen=True)
class ColumnMap:
    target: str
    expression: str


# Key type handling
KEY_TYPE_HANDLERS = {
    "bigint": {
        "min_expr": "MIN({key})",
        "max_expr": "MAX({key})",
        "filter_expr": "src.{key} > %(start_key)s AND src.{key} <= %(end_key)s",
        "param_type": "bigint",
    },
    "bigint_text": {
        # Uses pre-computed recid_bigint column for index scan
        "min_expr": "MIN(recid_bigint)",
        "max_expr": "MAX(recid_bigint)",
        "filter_expr": "src.recid_bigint > %(start_key)s AND src.recid_bigint <= %(end_key)s",
        "param_type": "bigint",
        "key_column_override": "recid_bigint",
    },
    "bigint_text_expression": {
        # AX RECID is stored as text; use the exact functional-index expression.
        "value_expr": "(btrim({key}))::bigint",
        "min_expr": "MIN((btrim({key}))::bigint)",
        "max_expr": "MAX((btrim({key}))::bigint)",
        "filter_expr": "(btrim(src.{key}))::bigint > %(start_key)s AND (btrim(src.{key}))::bigint <= %(end_key)s",
        "param_type": "bigint",
    },
    "numeric_text": {
        # Numeric values are stored as text with equal width.
        # Keep the source column uncast in WHERE so PostgreSQL can use
        # the regular B-tree index on the text column.
        "min_expr": "MIN({key})",
        "max_expr": "MAX({key})",
        "filter_expr": "src.{key} > %(start_key)s AND src.{key} <= %(end_key)s",
        "param_type": "text",
    },
    "timestamp": {
        "min_expr": "MIN({key})",
        "max_expr": "MAX({key})",
        "filter_expr": "src.{key} > %(start_key)s AND src.{key} <= %(end_key)s",
        "param_type": "timestamp",
    },
    "text": {
        "min_expr": "MIN({key})",
        "max_expr": "MAX({key})",
        "filter_expr": "src.{key} > %(start_key)s AND src.{key} <= %(end_key)s",
        "param_type": "text",
    },
}


class RawToDdsAdapter:
    """Executes PostgreSQL-internal INSERT..SELECT without moving rows via Python."""

    def __init__(self, columns: list[ColumnMap], conflict_column: str | None = None,
                 key_type: str = "bigint"):
        self.columns = columns
        self.conflict_column = conflict_column
        self.key_type = key_type
        self._key_handler = KEY_TYPE_HANDLERS.get(key_type, KEY_TYPE_HANDLERS["bigint"])

    def _get_key_column(self, spec: PipelineSpec) -> str:
        """Get the actual key column to use in queries."""
        override = self._key_handler.get("key_column_override")
        if override:
            return override
        return spec.key_column

    def get_boundaries(self, data_conn, spec: PipelineSpec) -> tuple:
        strategy = getattr(spec, "chunk_strategy", None)
        if strategy == "full_table":
            # One logical chunk; do not query MIN/MAX on a chunk key.
            return 0, 1

        """Get MIN and MAX values of key column."""
        key_column = self._get_key_column(spec)
        key_expr = ident(key_column)
        value_expr = self._key_handler.get("value_expr", "{key}").format(key=key_expr)
        min_sql = self._key_handler["min_expr"].format(key=key_expr)
        max_sql = self._key_handler["max_expr"].format(key=key_expr)

        sql = f"""
            SELECT {min_sql}, {max_sql}
            FROM {ident(spec.source_schema)}.{ident(spec.source_table)}
            WHERE {value_expr} IS NOT NULL
        """

        with data_conn.cursor() as cur:
            cur.execute(sql)
            row = cur.fetchone()

        if not row or row[0] is None or row[1] is None:
            return 0, -1

        # Convert to appropriate type
        if self.key_type in ("bigint", "bigint_text", "bigint_text_expression", "numeric_text"):
            return int(row[0]), int(row[1])
        return row[0], row[1]

    def build_ranges(self, start, end, batch_size: int) -> list[tuple]:
        """Build ranges for chunk creation."""
        if end < start:
            return []

        ranges = []
        if self.key_type in ("bigint", "bigint_text", "bigint_text_expression", "numeric_text"):
            lower = int(start) - 1
            numeric_end = int(end)
            while lower < numeric_end:
                upper = min(lower + batch_size, numeric_end)
                ranges.append((lower, upper))
                lower = upper
        else:
            # For non-numeric types, single range
            ranges.append((start, end))

        return ranges

    def execute_batch(self, data_conn, spec: PipelineSpec, start, end) -> BatchResult:
        """Execute INSERT ... SELECT for a batch."""
        targets = ", ".join(ident(c.target) for c in self.columns)
        expressions = ",\n                ".join(c.expression for c in self.columns)

        # Build WHERE clause based on the effective key column.
        # For numeric_text the source column must remain uncast in WHERE;
        # chunk boundaries are converted to strings before binding.
        key_column = self._get_key_column(spec)
        where_template = self._key_handler.get(
            "filter_expr",
            "src.{key} > %(start_key)s AND src.{key} <= %(end_key)s",
        )
        where_clause = where_template.format(key=ident(key_column))
        where_clause = (
            where_clause
            .replace("%(start_key)s", "%s")
            .replace("%(end_key)s", "%s")
        )

        conflict = ""
        if self.conflict_column:
            conflict = f" ON CONFLICT ({ident(self.conflict_column)}) DO NOTHING"

        sql = f"""
            INSERT INTO {ident(spec.target_schema)}.{ident(spec.target_table)} ({targets})
            SELECT
                {expressions}
            FROM {ident(spec.source_schema)}.{ident(spec.source_table)} src
            WHERE {where_clause}
            {conflict}
        """

        query_params = (
            (str(start), str(end))
            if self.key_type == "numeric_text"
            else (start, end)
        )

        with data_conn.cursor() as cur:
            cur.execute(sql, query_params)
            inserted = max(cur.rowcount, 0)

        data_conn.commit()

        return BatchResult(
            rows_read=inserted,
            rows_inserted=inserted,
            last_processed_key=str(end),
        )

    def validate(self, data_conn, spec: PipelineSpec) -> dict[str, Any]:
        """Validate loaded data."""
        results = {}

        with data_conn.cursor() as cur:
            # Target count
            cur.execute(
                f"SELECT COUNT(*) FROM {ident(spec.target_schema)}.{ident(spec.target_table)}"
            )
            results["target_count"] = int(cur.fetchone()[0])

            # Check for duplicates if conflict column defined
            if self.conflict_column:
                cur.execute(f"""
                    SELECT COUNT(*) FROM (
                        SELECT {ident(self.conflict_column)}, COUNT(*)
                        FROM {ident(spec.target_schema)}.{ident(spec.target_table)}
                        GROUP BY {ident(self.conflict_column)}
                        HAVING COUNT(*) > 1
                    ) t
                """)
                results["duplicate_count"] = int(cur.fetchone()[0])

        return results

    def analyze(self, data_conn, spec: PipelineSpec) -> None:
        """Run ANALYZE on target table."""
        with data_conn.cursor() as cur:
            cur.execute(f"ANALYZE {ident(spec.target_schema)}.{ident(spec.target_table)}")
        data_conn.commit()

    def preflight(self, data_conn, spec: PipelineSpec) -> dict[str, Any]:
        """Run preflight checks without modifying data."""
        results = {"checks": [], "passed": True}

        with data_conn.cursor() as cur:
            # Check source schema exists
            cur.execute("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = %s)",
                       (spec.source_schema,))
            exists = cur.fetchone()[0]
            results["checks"].append({
                "name": f"Schema {spec.source_schema}",
                "passed": exists,
                "message": "OK" if exists else f"Schema {spec.source_schema} not found"
            })
            if not exists:
                results["passed"] = False

            # Check target schema exists
            cur.execute("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = %s)",
                       (spec.target_schema,))
            exists = cur.fetchone()[0]
            results["checks"].append({
                "name": f"Schema {spec.target_schema}",
                "passed": exists,
                "message": "OK" if exists else f"Schema {spec.target_schema} not found"
            })
            if not exists:
                results["passed"] = False

            # Check source table exists
            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM pg_tables
                    WHERE schemaname = %s AND tablename = %s
                )
            """, (spec.source_schema, spec.source_table))
            exists = cur.fetchone()[0]
            results["checks"].append({
                "name": f"Source {spec.source_schema}.{spec.source_table}",
                "passed": exists,
                "message": "OK" if exists else f"Source table not found"
            })
            if not exists:
                results["passed"] = False

            # Check target table exists
            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM pg_tables
                    WHERE schemaname = %s AND tablename = %s
                )
            """, (spec.target_schema, spec.target_table))
            exists = cur.fetchone()[0]
            results["checks"].append({
                "name": f"Target {spec.target_schema}.{spec.target_table}",
                "passed": exists,
                "message": "OK" if exists else f"Target table not found"
            })
            if not exists:
                results["passed"] = False

            # Check key column exists and type
            key_column = self._get_key_column(spec)
            cur.execute("""
                SELECT data_type FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s AND column_name = %s
            """, (spec.source_schema, spec.source_table, key_column))
            row = cur.fetchone()
            if row:
                col_type = row[0]
                is_numeric = col_type in ('bigint', 'integer', 'smallint')
                is_text = col_type == 'text'

                if self.key_type == "bigint_text":
                    # Check if recid_bigint column exists
                    cur.execute("""
                        SELECT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_schema = %s AND table_name = %s AND column_name = 'recid_bigint'
                        )
                    """, (spec.source_schema, spec.source_table))
                    has_bigint_col = cur.fetchone()[0]

                    # Check if index exists
                    cur.execute("""
                        SELECT EXISTS (
                            SELECT 1 FROM pg_indexes
                            WHERE tablename = %s AND schemaname = %s
                            AND indexname = 'idx_alk_markserial_recid_bigint'
                        )
                    """, (spec.source_table, spec.source_schema))
                    has_index = cur.fetchone()[0]

                    results["checks"].append({
                        "name": f"Key column {key_column}",
                        "passed": True,
                        "message": f"OK (column type: {col_type})"
                    })

                    results["checks"].append({
                        "name": "recid_bigint column",
                        "passed": has_bigint_col,
                        "message": "OK" if has_bigint_col else "Missing - run migration 001"
                    })
                    if not has_bigint_col:
                        results["passed"] = False

                    results["checks"].append({
                        "name": "idx_alk_markserial_recid_bigint",
                        "passed": has_index,
                        "message": "OK" if has_index else "Missing - run migration 002"
                    })
                    if not has_index:
                        results["passed"] = False

                elif self.key_type == "numeric_text":
                    type_ok = is_text
                    results["checks"].append({
                        "name": f"Key type {key_column}",
                        "passed": type_ok,
                        "message": (
                            f"OK (numeric_text stored as {col_type})"
                            if type_ok
                            else f"key_type=numeric_text requires text, got {col_type}"
                        )
                    })
                    if not type_ok:
                        results["passed"] = False

                    cur.execute("""
                        SELECT EXISTS (
                            SELECT 1
                            FROM pg_index i
                            JOIN pg_class idx ON idx.oid = i.indexrelid
                            JOIN pg_class tbl ON tbl.oid = i.indrelid
                            JOIN pg_namespace ns ON ns.oid = tbl.relnamespace
                            JOIN pg_attribute a
                              ON a.attrelid = tbl.oid
                             AND a.attnum = ANY(i.indkey)
                            WHERE ns.nspname = %s
                              AND tbl.relname = %s
                              AND a.attname = %s
                              AND i.indisvalid
                              AND i.indisready
                              AND idx.relam = (
                                  SELECT oid FROM pg_am WHERE amname = 'btree'
                              )
                        )
                    """, (spec.source_schema, spec.source_table, key_column))
                    has_key_index = cur.fetchone()[0]
                    results["checks"].append({
                        "name": f"B-tree index on {key_column}",
                        "passed": has_key_index,
                        "message": "OK" if has_key_index else f"No valid B-tree index on {key_column}"
                    })
                    if not has_key_index:
                        results["passed"] = False

                elif self.key_type == "bigint" and not is_numeric:
                    results["checks"].append({
                        "name": f"Key type {key_column}",
                        "passed": False,
                        "message": f"key_type=bigint but column type is {col_type}, not numeric"
                    })
                    results["passed"] = False
                else:
                    results["checks"].append({
                        "name": f"Key type {key_column}",
                        "passed": True,
                        "message": f"OK (column type: {col_type})"
                    })
            else:
                results["checks"].append({
                    "name": f"Key column {key_column}",
                    "passed": False,
                    "message": f"Key column {key_column} not found"
                })
                results["passed"] = False

            # Check conflict key has unique index
            if self.conflict_column:
                cur.execute("""
                    SELECT EXISTS (
                        SELECT 1 FROM pg_indexes
                        WHERE schemaname = %s AND tablename = %s
                        AND indexdef LIKE '%%UNIQUE%%' || %s || '%%'
                    )
                """, (spec.target_schema, spec.target_table, self.conflict_column))
                has_unique = cur.fetchone()[0]
                results["checks"].append({
                    "name": f"Unique index on {self.conflict_column}",
                    "passed": has_unique,
                    "message": "OK" if has_unique else f"No unique index on {self.conflict_column}"
                })
            else:
                results["checks"].append({
                    "name": "Conflict column",
                    "passed": True,
                    "message": "No conflict column configured"
                })

        return results

