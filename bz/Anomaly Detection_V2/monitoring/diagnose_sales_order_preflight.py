#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
Read-only diagnostic preflight for:
    raw_ax.salestable -> dds.sales_order

Outputs are written to:
    D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2\logs\3

Main output:
    sales_order_preflight_summary_<timestamp>.csv

Additional detailed CSV files are also written for columns, indexes,
constraints, ETL history, active sessions and EXPLAIN output.

Environment variables:
    PGHOST       default: localhost
    PGPORT       default: 5432
    PGDATABASE   default: wms_analysis
    PGUSER       default: postgres
    PGPASSWORD   required unless pgpass.conf is configured

Safety:
    * transaction is READ ONLY
    * statement_timeout = 60s
    * lock_timeout = 3s
    * no INSERT / UPDATE / DELETE / CREATE
    * EXPLAIN without ANALYZE
    * no exact COUNT(*) over source/target tables
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extras import RealDictCursor
except ImportError as exc:
    raise SystemExit(
        "Не установлен psycopg2. Выполните:\n"
        "  python -m pip install psycopg2-binary"
    ) from exc


PROJECT_DIR = Path(r"D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2")
LOG_DIR = PROJECT_DIR / "logs" / "3"

RAW_SCHEMA = "raw_ax"
RAW_TABLE = "salestable"
DDS_SCHEMA = "dds"
DDS_TABLE = "sales_order"

TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")


@dataclass
class SummaryRow:
    section: str
    check_name: str
    status: str
    value: str = ""
    details: str = ""
    recommendation: str = ""


def stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return

    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)

    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: stringify(row.get(key)) for key in fieldnames})


def fetch_all(cur: RealDictCursor, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    if params:
        cur.execute(query, params)
    else:
        cur.execute(query)
    return [dict(row) for row in cur.fetchall()]


def fetch_one(cur: RealDictCursor, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    if params:
        cur.execute(query, params)
    else:
        cur.execute(query)
    row = cur.fetchone()
    return dict(row) if row else None


def relation_exists(cur: RealDictCursor, qualified_name: str) -> bool:
    row = fetch_one(cur, "SELECT to_regclass(%s) AS relation_name", (qualified_name,))
    return bool(row and row["relation_name"])


def add_summary(
    summary: list[SummaryRow],
    section: str,
    check_name: str,
    status: str,
    value: Any = "",
    details: Any = "",
    recommendation: str = "",
) -> None:
    summary.append(
        SummaryRow(
            section=section,
            check_name=check_name,
            status=status,
            value=stringify(value),
            details=stringify(details),
            recommendation=recommendation,
        )
    )


def identify_plan_status(plan_lines: Iterable[str]) -> tuple[str, str]:
    plan = "\n".join(plan_lines)
    if re.search(r"\b(Index Scan|Index Only Scan|Bitmap Index Scan)\b", plan, re.I):
        return "OK", "План использует индексный доступ."
    if re.search(r"\b(Parallel Seq Scan|Seq Scan)\b", plan, re.I):
        return "BLOCKED", "Обнаружен полный последовательный scan."
    return "REVIEW", "Тип доступа не удалось однозначно классифицировать."


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    summary_path = LOG_DIR / f"sales_order_preflight_summary_{TIMESTAMP}.csv"
    log_path = LOG_DIR / f"sales_order_preflight_{TIMESTAMP}.log"

    details: dict[str, list[dict[str, Any]]] = {}
    summary: list[SummaryRow] = []

    conn_params = {
        "host": os.getenv("PGHOST", "localhost"),
        "port": int(os.getenv("PGPORT", "5432")),
        "dbname": os.getenv("PGDATABASE", "wms_analysis"),
        "user": os.getenv("PGUSER", "postgres"),
        "connect_timeout": 10,
        "application_name": "diagnose_sales_order_preflight",
    }
    if os.getenv("PGPASSWORD"):
        conn_params["password"] = os.environ["PGPASSWORD"]

    with log_path.open("w", encoding="utf-8") as log:
        def log_line(message: str) -> None:
            print(message)
            log.write(message + "\n")
            log.flush()

        log_line(f"Начало диагностики: {datetime.now():%Y-%m-%d %H:%M:%S}")
        log_line(f"PostgreSQL: {conn_params['host']}:{conn_params['port']}/{conn_params['dbname']}")
        log_line(f"Маршрут: {RAW_SCHEMA}.{RAW_TABLE} -> {DDS_SCHEMA}.{DDS_TABLE}")
        log_line("Режим: READ ONLY")

        try:
            conn = psycopg2.connect(**conn_params)
        except Exception as exc:
            add_summary(
                summary, "connection", "postgres_connection", "ERROR",
                details=f"{type(exc).__name__}: {exc}",
                recommendation="Проверьте службу PostgreSQL, параметры подключения и PGPASSWORD/pgpass.conf."
            )
            write_csv(summary_path, [row.__dict__ for row in summary])
            log_line(f"Ошибка подключения: {exc}")
            return 2

        try:
            conn.autocommit = False
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("BEGIN TRANSACTION READ ONLY")
                cur.execute("SET LOCAL lock_timeout = '3s'")
                cur.execute("SET LOCAL statement_timeout = '60s'")
                cur.execute("SET LOCAL idle_in_transaction_session_timeout = '5min'")

                # 01. Existence
                existence = fetch_one(
                    cur,
                    """
                    SELECT
                        to_regclass('raw_ax.salestable') AS raw_table,
                        to_regclass('dds.sales_order') AS dds_table,
                        to_regclass('etl.load_run') AS load_run_table,
                        to_regclass('etl.load_chunk') AS load_chunk_table
                    """
                ) or {}
                details["01_existence"] = [existence]

                raw_exists = bool(existence.get("raw_table"))
                dds_exists = bool(existence.get("dds_table"))
                load_run_exists = bool(existence.get("load_run_table"))
                load_chunk_exists = bool(existence.get("load_chunk_table"))

                add_summary(
                    summary, "01_existence", "raw_table_exists",
                    "OK" if raw_exists else "BLOCKED",
                    existence.get("raw_table"),
                    recommendation="" if raw_exists else "Завершите SQL Server -> RAW для SALESTABLE."
                )
                add_summary(
                    summary, "01_existence", "dds_table_exists",
                    "OK" if dds_exists else "BLOCKED",
                    existence.get("dds_table"),
                    recommendation="" if dds_exists else "Подготовьте DDL dds.sales_order до запуска full."
                )

                # 02. Sizes
                sizes = fetch_all(
                    cur,
                    """
                    SELECT
                        n.nspname AS schema_name,
                        c.relname AS table_name,
                        c.reltuples::bigint AS estimated_rows,
                        pg_relation_size(c.oid) AS heap_bytes,
                        pg_indexes_size(c.oid) AS indexes_bytes,
                        pg_total_relation_size(c.oid) AS total_bytes,
                        pg_size_pretty(pg_relation_size(c.oid)) AS heap_size,
                        pg_size_pretty(pg_indexes_size(c.oid)) AS indexes_size,
                        pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE (n.nspname, c.relname) IN (
                        ('raw_ax', 'salestable'),
                        ('dds', 'sales_order')
                    )
                    ORDER BY n.nspname, c.relname
                    """
                )
                details["02_sizes"] = sizes
                for row in sizes:
                    add_summary(
                        summary,
                        "02_sizes",
                        f"{row['schema_name']}.{row['table_name']}",
                        "INFO",
                        row.get("estimated_rows"),
                        f"heap={row.get('heap_size')}; indexes={row.get('indexes_size')}; total={row.get('total_size')}",
                        "estimated_rows является оценкой, не точным COUNT(*)."
                    )

                # 03. Columns
                columns = fetch_all(
                    cur,
                    """
                    SELECT
                        table_schema,
                        table_name,
                        ordinal_position,
                        column_name,
                        data_type,
                        udt_name,
                        character_maximum_length,
                        numeric_precision,
                        numeric_scale,
                        is_nullable,
                        column_default
                    FROM information_schema.columns
                    WHERE (table_schema, table_name) IN (
                        ('raw_ax', 'salestable'),
                        ('dds', 'sales_order')
                    )
                    ORDER BY table_schema, table_name, ordinal_position
                    """
                )
                details["03_columns"] = columns

                raw_columns = [r for r in columns if r["table_schema"] == RAW_SCHEMA]
                dds_columns = [r for r in columns if r["table_schema"] == DDS_SCHEMA]
                add_summary(summary, "03_columns", "raw_column_count", "OK" if raw_columns else "BLOCKED", len(raw_columns))
                add_summary(summary, "03_columns", "dds_column_count", "OK" if dds_columns else "BLOCKED", len(dds_columns))

                recid_col = next((r for r in raw_columns if r["column_name"].lower() == "recid"), None)
                recid_bigint_col = next((r for r in raw_columns if r["column_name"].lower() == "recid_bigint"), None)
                salesid_col = next((r for r in raw_columns if r["column_name"].lower() == "salesid"), None)

                if recid_bigint_col:
                    chunk_col = recid_bigint_col
                    chunk_expr = sql.Identifier(recid_bigint_col["column_name"]).as_string(cur)
                    chunk_kind = "bigint"
                elif recid_col:
                    chunk_col = recid_col
                    chunk_expr = sql.Identifier(recid_col["column_name"]).as_string(cur)
                    chunk_kind = (
                        "bigint"
                        if recid_col["udt_name"] in ("int8", "int4", "int2", "numeric")
                        else "text"
                    )
                else:
                    chunk_col = None
                    chunk_expr = ""
                    chunk_kind = "missing"

                add_summary(
                    summary,
                    "03_columns",
                    "chunk_key_candidate",
                    "OK" if chunk_col else "BLOCKED",
                    chunk_col["column_name"] if chunk_col else "",
                    details=(
                        f"data_type={chunk_col['data_type']}; udt_name={chunk_col['udt_name']}; kind={chunk_kind}"
                        if chunk_col else "RECID/RECID_BIGINT не найден"
                    ),
                    recommendation=(
                        "" if chunk_col
                        else "Нужен индексируемый числовой ключ для chunk-загрузки."
                    )
                )
                add_summary(
                    summary,
                    "03_columns",
                    "salesid_present",
                    "OK" if salesid_col else "REVIEW",
                    salesid_col["data_type"] if salesid_col else "",
                    recommendation="" if salesid_col else "Проверьте mapping бизнес-ключа заказа."
                )

                # 04. Indexes
                indexes = fetch_all(
                    cur,
                    """
                    SELECT
                        schemaname,
                        tablename,
                        indexname,
                        indexdef
                    FROM pg_indexes
                    WHERE (schemaname, tablename) IN (
                        ('raw_ax', 'salestable'),
                        ('dds', 'sales_order')
                    )
                    ORDER BY schemaname, tablename, indexname
                    """
                )
                details["04_indexes"] = indexes

                raw_index_columns = fetch_all(
                    cur,
                    """
                    SELECT
                        idx.relname AS index_name,
                        am.amname AS access_method,
                        i.indisvalid AS is_valid,
                        i.indisready AS is_ready,
                        i.indisunique AS is_unique,
                        i.indisprimary AS is_primary,
                        ord.ordinality AS column_position,
                        COALESCE(
                            att.attname,
                            pg_get_indexdef(i.indexrelid, ord.ordinality::int, true)
                        ) AS indexed_column_or_expression
                    FROM pg_index i
                    JOIN pg_class tbl ON tbl.oid = i.indrelid
                    JOIN pg_namespace ns ON ns.oid = tbl.relnamespace
                    JOIN pg_class idx ON idx.oid = i.indexrelid
                    JOIN pg_am am ON am.oid = idx.relam
                    CROSS JOIN LATERAL
                        unnest(i.indkey) WITH ORDINALITY AS ord(attnum, ordinality)
                    LEFT JOIN pg_attribute att
                        ON att.attrelid = tbl.oid
                       AND att.attnum = ord.attnum
                    WHERE ns.nspname = 'raw_ax'
                      AND tbl.relname = 'salestable'
                    ORDER BY idx.relname, ord.ordinality
                    """
                ) if raw_exists else []
                details["05_raw_index_columns"] = raw_index_columns

                candidate_name = chunk_col["column_name"].lower() if chunk_col else ""
                matching_first_indexes = [
                    r for r in raw_index_columns
                    if r["column_position"] == 1
                    and str(r["indexed_column_or_expression"]).lower() == candidate_name
                    and r["access_method"] == "btree"
                    and r["is_valid"]
                    and r["is_ready"]
                ]
                add_summary(
                    summary,
                    "04_indexes",
                    "chunk_key_btree_index",
                    "OK" if matching_first_indexes else "BLOCKED",
                    ", ".join(r["index_name"] for r in matching_first_indexes),
                    details=f"candidate={candidate_name or 'missing'}",
                    recommendation=(
                        "" if matching_first_indexes
                        else "До full-запуска нужен валидный B-tree индекс, начинающийся с chunk key."
                    )
                )

                # 05. DDS constraints
                constraints = fetch_all(
                    cur,
                    """
                    SELECT
                        con.conname AS constraint_name,
                        CASE con.contype
                            WHEN 'p' THEN 'PRIMARY KEY'
                            WHEN 'u' THEN 'UNIQUE'
                            WHEN 'f' THEN 'FOREIGN KEY'
                            WHEN 'c' THEN 'CHECK'
                            WHEN 'x' THEN 'EXCLUSION'
                            ELSE con.contype::text
                        END AS constraint_type,
                        pg_get_constraintdef(con.oid) AS definition
                    FROM pg_constraint con
                    JOIN pg_class cls ON cls.oid = con.conrelid
                    JOIN pg_namespace ns ON ns.oid = cls.relnamespace
                    WHERE ns.nspname = 'dds'
                      AND cls.relname = 'sales_order'
                    ORDER BY con.contype, con.conname
                    """
                ) if dds_exists else []
                details["06_dds_constraints"] = constraints
                conflict_constraints = [
                    r for r in constraints
                    if r["constraint_type"] in ("PRIMARY KEY", "UNIQUE")
                ]
                add_summary(
                    summary,
                    "05_constraints",
                    "dds_conflict_constraint",
                    "OK" if conflict_constraints else "BLOCKED",
                    ", ".join(r["constraint_name"] for r in conflict_constraints),
                    details=" | ".join(r["definition"] for r in conflict_constraints),
                    recommendation=(
                        "" if conflict_constraints
                        else "Для ON CONFLICT требуется PRIMARY KEY или UNIQUE по утверждённому ключу."
                    )
                )

                # 06. Stats
                stats = fetch_all(
                    cur,
                    """
                    SELECT
                        schemaname,
                        relname,
                        n_live_tup,
                        n_dead_tup,
                        last_vacuum,
                        last_autovacuum,
                        last_analyze,
                        last_autoanalyze
                    FROM pg_stat_user_tables
                    WHERE (schemaname, relname) IN (
                        ('raw_ax', 'salestable'),
                        ('dds', 'sales_order')
                    )
                    ORDER BY schemaname, relname
                    """
                )
                details["07_table_stats"] = stats

                # 07. Active sessions
                active = fetch_all(
                    cur,
                    """
                    SELECT
                        pid,
                        usename,
                        application_name,
                        state,
                        wait_event_type,
                        wait_event,
                        clock_timestamp() - query_start AS query_duration,
                        left(query, 1500) AS query
                    FROM pg_stat_activity
                    WHERE pid <> pg_backend_pid()
                      AND (
                          query ILIKE '%raw_ax.salestable%'
                          OR query ILIKE '%dds.sales_order%'
                          OR application_name ILIKE '%dds%'
                      )
                    ORDER BY query_start
                    """
                )
                details["08_active_sessions"] = active
                add_summary(
                    summary,
                    "07_activity",
                    "conflicting_active_sessions",
                    "OK" if not active else "REVIEW",
                    len(active),
                    recommendation=(
                        "" if not active
                        else "Перед full-запуском разберите активные запросы и блокировки."
                    )
                )

                # 08. ETL history
                history: list[dict[str, Any]] = []
                if load_run_exists:
                    history = fetch_all(
                        cur,
                        """
                        SELECT to_jsonb(lr) AS run_data
                        FROM etl.load_run lr
                        WHERE to_jsonb(lr)::text ILIKE '%raw_ax%'
                          AND (
                              to_jsonb(lr)::text ILIKE '%sales_order%'
                              OR to_jsonb(lr)::text ILIKE '%salestable%'
                          )
                        ORDER BY lr.run_id DESC
                        LIMIT 50
                        """
                    )
                details["09_etl_history"] = history
                add_summary(
                    summary,
                    "08_history",
                    "raw_to_dds_history_found",
                    "INFO",
                    len(history),
                    recommendation=(
                        "Проверьте, что найденные записи относятся именно к raw_ax.salestable -> dds.sales_order."
                        if history else "Предыдущая RAW -> DDS загрузка не обнаружена."
                    )
                )

                # 09. Sample RECID
                samples: list[dict[str, Any]] = []
                bad_samples: list[dict[str, Any]] = []
                numeric_values: list[int] = []

                if raw_exists and chunk_col:
                    sample_query = sql.SQL(
                        """
                        SELECT {column} AS recid_value
                        FROM {schema}.{table} TABLESAMPLE SYSTEM (0.1)
                        WHERE {column} IS NOT NULL
                        LIMIT 100
                        """
                    ).format(
                        column=sql.Identifier(chunk_col["column_name"]),
                        schema=sql.Identifier(RAW_SCHEMA),
                        table=sql.Identifier(RAW_TABLE),
                    )
                    cur.execute(sample_query)
                    samples = [dict(row) for row in cur.fetchall()]
                    for row in samples:
                        value = stringify(row.get("recid_value")).strip()
                        if re.fullmatch(r"\d+", value):
                            numeric_values.append(int(value))

                    if chunk_kind == "text":
                        bad_query = sql.SQL(
                            """
                            SELECT {column} AS recid_value
                            FROM {schema}.{table} TABLESAMPLE SYSTEM (0.1)
                            WHERE {column} IS NOT NULL
                              AND btrim({column}) !~ '^[0-9]+$'
                            LIMIT 100
                            """
                        ).format(
                            column=sql.Identifier(chunk_col["column_name"]),
                            schema=sql.Identifier(RAW_SCHEMA),
                            table=sql.Identifier(RAW_TABLE),
                        )
                        cur.execute(bad_query)
                        bad_samples = [dict(row) for row in cur.fetchall()]

                details["10_recid_sample"] = samples
                details["11_non_numeric_recid_sample"] = bad_samples
                add_summary(
                    summary,
                    "09_recid_quality",
                    "recid_sample_numeric",
                    (
                        "OK" if samples and len(numeric_values) == len(samples)
                        else "REVIEW" if samples
                        else "REVIEW"
                    ),
                    f"{len(numeric_values)}/{len(samples)}",
                    details=f"non_numeric_sample_count={len(bad_samples)}",
                    recommendation=(
                        "TABLESAMPLE — предварительная проверка, не полная валидация."
                    )
                )

                # 10. EXPLAIN without ANALYZE
                plan_rows: list[dict[str, Any]] = []
                if raw_exists and chunk_col and numeric_values:
                    low = min(numeric_values)
                    # Use a bounded range. Constants only affect estimates; EXPLAIN does not execute.
                    high = low + 500_000

                    if chunk_kind == "bigint":
                        explain_query = sql.SQL(
                            """
                            EXPLAIN (COSTS, VERBOSE, SETTINGS, FORMAT TEXT)
                            SELECT *
                            FROM {schema}.{table}
                            WHERE {column} >= %s
                              AND {column} < %s
                            ORDER BY {column}
                            """
                        ).format(
                            schema=sql.Identifier(RAW_SCHEMA),
                            table=sql.Identifier(RAW_TABLE),
                            column=sql.Identifier(chunk_col["column_name"]),
                        )
                    else:
                        explain_query = sql.SQL(
                            """
                            EXPLAIN (COSTS, VERBOSE, SETTINGS, FORMAT TEXT)
                            SELECT *
                            FROM {schema}.{table}
                            WHERE btrim({column})::bigint >= %s
                              AND btrim({column})::bigint < %s
                            """
                        ).format(
                            schema=sql.Identifier(RAW_SCHEMA),
                            table=sql.Identifier(RAW_TABLE),
                            column=sql.Identifier(chunk_col["column_name"]),
                        )

                    cur.execute(explain_query, (low, high))
                    explain_rows = cur.fetchall()
                    lines = [
                        str(next(iter(row.values())))
                        for row in explain_rows
                    ]
                    plan_rows = [
                        {"line_no": i + 1, "plan_line": line}
                        for i, line in enumerate(lines)
                    ]
                    plan_status, plan_details = identify_plan_status(lines)
                    add_summary(
                        summary,
                        "10_explain",
                        "chunk_query_plan",
                        plan_status,
                        f"range=[{low}, {high})",
                        details=plan_details,
                        recommendation=(
                            "" if plan_status == "OK"
                            else "Не запускайте full до устранения Seq Scan/неясного плана."
                        )
                    )
                else:
                    add_summary(
                        summary,
                        "10_explain",
                        "chunk_query_plan",
                        "REVIEW",
                        details="EXPLAIN пропущен: нет числового sample или chunk key.",
                        recommendation="Уточните тип/качество RECID и индекс."
                    )
                details["12_explain_plan"] = plan_rows

                # Final readiness
                blocking = [row for row in summary if row.status in ("BLOCKED", "ERROR")]
                reviews = [row for row in summary if row.status == "REVIEW"]
                if blocking:
                    final_status = "NOT_READY"
                    final_details = f"blocking_checks={len(blocking)}; review_checks={len(reviews)}"
                    final_recommendation = "Не запускать RAW -> DDS full. Сначала устранить BLOCKED."
                elif reviews:
                    final_status = "READY_WITH_REVIEW"
                    final_details = f"review_checks={len(reviews)}"
                    final_recommendation = "Проверить пункты REVIEW и mapping перед full."
                else:
                    final_status = "READY_FOR_PREFLIGHT"
                    final_details = "Критические блокеры не обнаружены."
                    final_recommendation = "Можно запускать штатный dds_cli --mode preflight --stage sales_order."

                add_summary(
                    summary,
                    "99_final",
                    "raw_to_dds_readiness",
                    final_status,
                    details=final_details,
                    recommendation=final_recommendation
                )

                conn.rollback()

            # Save output
            write_csv(summary_path, [row.__dict__ for row in summary])
            for name, rows in details.items():
                write_csv(LOG_DIR / f"sales_order_preflight_{name}_{TIMESTAMP}.csv", rows)

            log_line(f"Итоговый CSV: {summary_path}")
            log_line(f"Финальный статус: {summary[-1].status}")
            log_line("Диагностика завершена без изменения данных.")
            return 0

        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass

            add_summary(
                summary,
                "runtime",
                "diagnostic_execution",
                "ERROR",
                details=f"{type(exc).__name__}: {exc}",
                recommendation="Проверьте log-файл и проблемный диагностический запрос."
            )
            write_csv(summary_path, [row.__dict__ for row in summary])
            log_line(f"Ошибка: {type(exc).__name__}: {exc}")
            log.write(traceback.format_exc())
            return 1
        finally:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
