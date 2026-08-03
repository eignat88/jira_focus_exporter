#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
Read-only diagnostics for RAW -> DDS stage sales_order.

Runs:
- run comparison for run_id 35, 36, 37, 45;
- failed chunk inspection for run_id 45;
- PostgreSQL activity/progress diagnostics;
- safe determination of a real RECID range;
- EXPLAIN for batches 100k and 250k;
- optional EXPLAIN (ANALYZE, BUFFERS, WAL) when explicitly enabled;
- CSV summary and detailed CSV files.

Default output directory:
D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2\logs\4

The script starts a READ ONLY transaction and does not execute INSERT/UPDATE/DELETE.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    import psycopg
    from psycopg.rows import dict_row
    PSYCOPG_VERSION = 3
except ImportError:
    try:
        import psycopg2
        import psycopg2.extras
        PSYCOPG_VERSION = 2
    except ImportError as exc:
        raise SystemExit(
            "Не установлен PostgreSQL-драйвер.\n"
            "Установите один из вариантов:\n"
            "  python -m pip install \"psycopg[binary]\"\n"
            "или\n"
            "  python -m pip install psycopg2-binary"
        ) from exc

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


DEFAULT_OUTPUT = Path(
    r"D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2\logs\4"
)

RUN_IDS = (35, 36, 37, 45)
FAILED_RUN_ID = 45
BATCHES = (100_000, 250_000)

SOURCE_TABLE = "raw_ax.salestable"
TARGET_TABLE = "dds.sales_order"

RUN_COLUMNS_WANTED = [
    "run_id",
    "stage_name",
    "source_schema",
    "source_table",
    "target_schema",
    "target_table",
    "mode",
    "status",
    "batch_size",
    "checkpoint",
    "started_at",
    "finished_at",
    "heartbeat_at",
    "total_chunks",
    "completed_chunks",
    "failed_chunks",
    "rows_read",
    "rows_inserted",
    "rows_updated",
    "rows_conflicted",
    "error_message",
]

CHUNK_COLUMNS_WANTED = [
    "run_id",
    "chunk_id",
    "status",
    "range_start",
    "range_end",
    "chunk_start",
    "chunk_end",
    "start_key",
    "end_key",
    "min_key",
    "max_key",
    "rows_read",
    "rows_inserted",
    "rows_updated",
    "rows_conflicted",
    "attempt",
    "retry_count",
    "started_at",
    "finished_at",
    "error_message",
]


@dataclass
class QueryResult:
    name: str
    rows: list[dict[str, Any]]
    error: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only diagnostics for sales_order run 45."
    )
    parser.add_argument("--host", default=os.getenv("PGHOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PGPORT", "5432")))
    parser.add_argument("--database", default=os.getenv("PGDATABASE", "wms_analysis"))
    parser.add_argument("--user", default=os.getenv("PGUSER", "postgres"))
    parser.add_argument("--password", default=os.getenv("PGPASSWORD"))
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT),
        help="Каталог CSV и логов.",
    )
    parser.add_argument(
        "--statement-timeout-ms",
        type=int,
        default=120_000,
        help="statement_timeout для обычных диагностических запросов.",
    )
    parser.add_argument(
        "--explain-timeout-ms",
        type=int,
        default=120_000,
        help="statement_timeout для EXPLAIN без ANALYZE.",
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="Явно разрешить EXPLAIN ANALYZE для ограниченных диапазонов.",
    )
    parser.add_argument(
        "--analyze-batches",
        default="100000",
        help="Batch sizes для ANALYZE через запятую. Например: 100000 или 100000,250000.",
    )
    parser.add_argument(
        "--analyze-timeout-ms",
        type=int,
        default=300_000,
        help="statement_timeout для EXPLAIN ANALYZE.",
    )
    parser.add_argument(
        "--range-start",
        type=int,
        default=None,
        help="Явно задать нижнюю границу RECID. Иначе берётся failed chunk run 45 или MIN(recid).",
    )
    return parser.parse_args()


def setup_logging(output_dir: Path, stamp: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / f"sales_order_run45_diagnostic_{stamp}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return log_path


@contextmanager
def connect_db(args: argparse.Namespace):
    if PSYCOPG_VERSION == 3:
        conn = psycopg.connect(
            host=args.host,
            port=args.port,
            dbname=args.database,
            user=args.user,
            password=args.password,
            row_factory=dict_row,
            application_name="sales_order_run45_diagnostic",
            autocommit=False,
        )
    else:
        conn = psycopg2.connect(
            host=args.host,
            port=args.port,
            dbname=args.database,
            user=args.user,
            password=args.password,
            application_name="sales_order_run45_diagnostic",
        )
        conn.autocommit = False

    try:
        with conn.cursor() as cur:
            cur.execute("BEGIN READ ONLY")
            cur.execute("SET LOCAL lock_timeout = '5s'")
            cur.execute("SET LOCAL idle_in_transaction_session_timeout = '10min'")
        yield conn
        conn.rollback()
    finally:
        conn.close()


def execute(
    conn,
    sql: str,
    params: Sequence[Any] | None = None,
    timeout_ms: int | None = None,
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        if timeout_ms is not None:
            cur.execute("SET LOCAL statement_timeout = %s", (timeout_ms,))
        cur.execute(sql, params or ())
        if cur.description is None:
            return []
        columns = [desc.name if hasattr(desc, "name") else desc[0] for desc in cur.description]
        raw_rows = cur.fetchall()
        rows: list[dict[str, Any]] = []
        for row in raw_rows:
            if isinstance(row, dict):
                rows.append(dict(row))
            else:
                rows.append(dict(zip(columns, row)))
        return rows


def get_columns(conn, schema: str, table: str, timeout_ms: int) -> list[dict[str, Any]]:
    return execute(
        conn,
        """
        SELECT
            ordinal_position,
            column_name,
            data_type,
            udt_name,
            is_nullable
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = %s
        ORDER BY ordinal_position
        """,
        (schema, table),
        timeout_ms,
    )


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def select_existing_columns(
    conn,
    schema: str,
    table: str,
    wanted: list[str],
    where_sql: str,
    params: Sequence[Any],
    order_sql: str,
    timeout_ms: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    cols_meta = get_columns(conn, schema, table, timeout_ms)
    existing = {row["column_name"] for row in cols_meta}
    selected = [c for c in wanted if c in existing]
    if not selected:
        return [], []
    sql = (
        "SELECT "
        + ", ".join(quote_ident(c) for c in selected)
        + f" FROM {quote_ident(schema)}.{quote_ident(table)} "
        + where_sql
        + " "
        + order_sql
    )
    return execute(conn, sql, params, timeout_ms), selected


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            f.write("")
        return
    fieldnames: list[str] = []
    seen = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)

    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            normalized = {}
            for k, v in row.items():
                if isinstance(v, (dict, list)):
                    normalized[k] = json.dumps(v, ensure_ascii=False, default=str)
                else:
                    normalized[k] = v
            writer.writerow(normalized)


def first_existing(columns: Iterable[str], candidates: Sequence[str]) -> str | None:
    colset = set(columns)
    for c in candidates:
        if c in colset:
            return c
    return None


def integer_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        match = re.search(r"-?\d+", str(value))
        return int(match.group(0)) if match else None


def determine_range_start(
    conn,
    args: argparse.Namespace,
    chunk_rows: list[dict[str, Any]],
    chunk_columns: list[str],
) -> tuple[int, str]:
    if args.range_start is not None:
        return args.range_start, "CLI --range-start"

    start_col = first_existing(
        chunk_columns,
        ("range_start", "chunk_start", "start_key", "min_key"),
    )

    failed_rows = [
        row for row in chunk_rows
        if str(row.get("status", "")).upper() in {"FAILED", "ERROR"}
    ]
    if start_col:
        for row in failed_rows:
            value = integer_or_none(row.get(start_col))
            if value is not None:
                return value, f"failed chunk run {FAILED_RUN_ID}, column {start_col}"

    rows = execute(
        conn,
        """
        SELECT btrim(recid)::bigint AS min_recid
        FROM raw_ax.salestable
        WHERE recid IS NOT NULL
          AND btrim(recid) ~ '^[0-9]+$'
        ORDER BY btrim(recid)::bigint
        LIMIT 1
        """,
        timeout_ms=args.statement_timeout_ms,
    )
    if not rows or rows[0].get("min_recid") is None:
        raise RuntimeError("Не удалось определить минимальный числовой RECID.")
    return int(rows[0]["min_recid"]), "MIN numeric raw_ax.salestable.recid"


def explain_query(
    conn,
    range_start: int,
    batch_size: int,
    analyze: bool,
    timeout_ms: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    options = (
        "ANALYZE, BUFFERS, WAL, TIMING, SUMMARY, FORMAT JSON"
        if analyze
        else "COSTS, VERBOSE, SETTINGS, FORMAT JSON"
    )
    sql = f"""
        EXPLAIN ({options})
        SELECT
            btrim(recid)::bigint AS source_recid
        FROM raw_ax.salestable
        WHERE btrim(recid)::bigint >= %s
          AND btrim(recid)::bigint < %s
    """
    rows = execute(
        conn,
        sql,
        (range_start, range_start + batch_size),
        timeout_ms,
    )
    if not rows:
        return rows, {}

    first_value = next(iter(rows[0].values()))
    payload = first_value
    if isinstance(payload, str):
        payload = json.loads(payload)
    if isinstance(payload, list) and payload:
        doc = payload[0]
    elif isinstance(payload, dict):
        doc = payload
    else:
        doc = {}
    return rows, doc


def flatten_plan_nodes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    def walk(node: dict[str, Any], depth: int = 0) -> None:
        item = {
            "depth": depth,
            "node_type": node.get("Node Type"),
            "relation_name": node.get("Relation Name"),
            "index_name": node.get("Index Name"),
            "startup_cost": node.get("Startup Cost"),
            "total_cost": node.get("Total Cost"),
            "plan_rows": node.get("Plan Rows"),
            "actual_rows": node.get("Actual Rows"),
            "actual_loops": node.get("Actual Loops"),
            "actual_startup_time_ms": node.get("Actual Startup Time"),
            "actual_total_time_ms": node.get("Actual Total Time"),
            "shared_hit_blocks": node.get("Shared Hit Blocks"),
            "shared_read_blocks": node.get("Shared Read Blocks"),
            "exact_heap_blocks": node.get("Exact Heap Blocks"),
            "lossy_heap_blocks": node.get("Lossy Heap Blocks"),
            "rows_removed_by_index_recheck": node.get("Rows Removed by Index Recheck"),
            "index_cond": node.get("Index Cond"),
            "filter": node.get("Filter"),
        }
        result.append(item)
        for child in node.get("Plans", []) or []:
            walk(child, depth + 1)

    root = plan.get("Plan")
    if isinstance(root, dict):
        walk(root)
    return result


def summarize_plan(
    plan_doc: dict[str, Any],
    batch_size: int,
    range_start: int,
    analyze: bool,
) -> dict[str, Any]:
    nodes = flatten_plan_nodes(plan_doc)
    types = [str(n.get("node_type")) for n in nodes if n.get("node_type")]
    bitmap_heap = next((n for n in nodes if n.get("node_type") == "Bitmap Heap Scan"), {})
    bitmap_index = next((n for n in nodes if n.get("node_type") == "Bitmap Index Scan"), {})
    seq_scan = any(t in {"Seq Scan", "Parallel Seq Scan"} for t in types)

    summary = {
        "section": "EXPLAIN_ANALYZE" if analyze else "EXPLAIN",
        "metric": f"batch_{batch_size}",
        "value": " -> ".join(types),
        "status": "WARN" if seq_scan else "OK",
        "details": json.dumps(
            {
                "range_start": range_start,
                "range_end": range_start + batch_size,
                "execution_time_ms": plan_doc.get("Execution Time"),
                "planning_time_ms": plan_doc.get("Planning Time"),
                "bitmap_index": bitmap_index.get("index_name"),
                "plan_rows": (plan_doc.get("Plan") or {}).get("Plan Rows"),
                "actual_rows": (plan_doc.get("Plan") or {}).get("Actual Rows"),
                "shared_hit_blocks": bitmap_heap.get("shared_hit_blocks"),
                "shared_read_blocks": bitmap_heap.get("shared_read_blocks"),
                "exact_heap_blocks": bitmap_heap.get("exact_heap_blocks"),
                "lossy_heap_blocks": bitmap_heap.get("lossy_heap_blocks"),
                "rows_removed_by_index_recheck": bitmap_heap.get("rows_removed_by_index_recheck"),
            },
            ensure_ascii=False,
            default=str,
        ),
    }
    return summary


def add_summary(
    summary: list[dict[str, Any]],
    section: str,
    metric: str,
    value: Any,
    status: str = "INFO",
    details: Any = "",
) -> None:
    summary.append(
        {
            "section": section,
            "metric": metric,
            "value": value,
            "status": status,
            "details": (
                json.dumps(details, ensure_ascii=False, default=str)
                if isinstance(details, (dict, list))
                else details
            ),
        }
    )


def main() -> int:
    if load_dotenv:
        load_dotenv()

    args = parse_args()
    output_dir = Path(args.output_dir)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = setup_logging(output_dir, stamp)

    logging.info("Начало read-only диагностики sales_order.")
    logging.info("Выходной каталог: %s", output_dir)
    logging.info("EXPLAIN ANALYZE разрешён: %s", args.analyze)

    summary: list[dict[str, Any]] = []
    details_files: list[Path] = []

    try:
        with connect_db(args) as conn:
            server_info = execute(
                conn,
                """
                SELECT
                    current_database() AS database,
                    current_user AS current_user,
                    inet_server_addr() AS server_addr,
                    inet_server_port() AS server_port,
                    version() AS postgres_version,
                    now() AS collected_at,
                    current_setting('transaction_read_only') AS transaction_read_only
                """,
                timeout_ms=args.statement_timeout_ms,
            )
            path = output_dir / f"sales_order_server_info_{stamp}.csv"
            write_csv(path, server_info)
            details_files.append(path)
            for row in server_info:
                add_summary(summary, "CONNECTION", "transaction_read_only",
                            row.get("transaction_read_only"),
                            "OK" if row.get("transaction_read_only") == "on" else "ERROR")

            run_columns = get_columns(conn, "etl", "load_run", args.statement_timeout_ms)
            path = output_dir / f"etl_load_run_columns_{stamp}.csv"
            write_csv(path, run_columns)
            details_files.append(path)

            chunk_columns_meta = get_columns(conn, "etl", "load_chunk", args.statement_timeout_ms)
            path = output_dir / f"etl_load_chunk_columns_{stamp}.csv"
            write_csv(path, chunk_columns_meta)
            details_files.append(path)

            run_rows, selected_run_cols = select_existing_columns(
                conn,
                "etl",
                "load_run",
                RUN_COLUMNS_WANTED,
                "WHERE run_id = ANY(%s)",
                (list(RUN_IDS),),
                "ORDER BY run_id",
                args.statement_timeout_ms,
            )
            path = output_dir / f"sales_order_runs_35_36_37_45_{stamp}.csv"
            write_csv(path, run_rows)
            details_files.append(path)

            chunk_rows, selected_chunk_cols = select_existing_columns(
                conn,
                "etl",
                "load_chunk",
                CHUNK_COLUMNS_WANTED,
                "WHERE run_id = %s",
                (FAILED_RUN_ID,),
                "ORDER BY chunk_id",
                args.statement_timeout_ms,
            )
            path = output_dir / f"sales_order_run45_chunks_{stamp}.csv"
            write_csv(path, chunk_rows)
            details_files.append(path)

            failed_rows = [
                r for r in chunk_rows
                if str(r.get("status", "")).upper() in {"FAILED", "ERROR"}
            ]
            add_summary(summary, "RUN_45", "failed_chunk_count", len(failed_rows),
                        "ERROR" if failed_rows else "OK")
            add_summary(summary, "RUN_45", "chunk_rows_found", len(chunk_rows), "INFO")
            add_summary(summary, "RUNS", "runs_found", len(run_rows),
                        "OK" if len(run_rows) == len(RUN_IDS) else "WARN",
                        {"expected": list(RUN_IDS), "found": [r.get("run_id") for r in run_rows]})

            for row in run_rows:
                run_id = row.get("run_id")
                add_summary(
                    summary,
                    "RUN_STATUS",
                    f"run_{run_id}",
                    row.get("status"),
                    "OK" if str(row.get("status", "")).upper() == "COMPLETED"
                    else ("ERROR" if str(row.get("status", "")).upper() in {"FAILED", "ERROR"} else "WARN"),
                    {
                        "started_at": row.get("started_at"),
                        "finished_at": row.get("finished_at"),
                        "batch_size": row.get("batch_size"),
                        "failed_chunks": row.get("failed_chunks"),
                        "error_message": row.get("error_message"),
                    },
                )

            activity = execute(
                conn,
                """
                SELECT
                    pid,
                    usename,
                    application_name,
                    state,
                    wait_event_type,
                    wait_event,
                    now() - xact_start AS transaction_age,
                    now() - query_start AS query_age,
                    left(query, 500) AS query
                FROM pg_stat_activity
                WHERE datname = current_database()
                  AND pid <> pg_backend_pid()
                ORDER BY query_start
                """,
                timeout_ms=args.statement_timeout_ms,
            )
            path = output_dir / f"postgres_activity_{stamp}.csv"
            write_csv(path, activity)
            details_files.append(path)
            active_count = sum(1 for r in activity if r.get("state") == "active")
            add_summary(summary, "POSTGRES", "other_active_sessions", active_count,
                        "WARN" if active_count else "OK")

            vacuum = execute(
                conn,
                """
                SELECT *
                FROM pg_stat_progress_vacuum
                WHERE relid IN (
                    'raw_ax.salestable'::regclass,
                    'dds.sales_order'::regclass
                )
                """,
                timeout_ms=args.statement_timeout_ms,
            )
            path = output_dir / f"postgres_vacuum_progress_{stamp}.csv"
            write_csv(path, vacuum)
            details_files.append(path)
            add_summary(summary, "POSTGRES", "vacuum_progress_rows", len(vacuum),
                        "WARN" if vacuum else "OK")

            create_index = execute(
                conn,
                """
                SELECT *
                FROM pg_stat_progress_create_index
                WHERE relid IN (
                    'raw_ax.salestable'::regclass,
                    'dds.sales_order'::regclass
                )
                """,
                timeout_ms=args.statement_timeout_ms,
            )
            path = output_dir / f"postgres_create_index_progress_{stamp}.csv"
            write_csv(path, create_index)
            details_files.append(path)
            add_summary(summary, "POSTGRES", "create_index_progress_rows", len(create_index),
                        "WARN" if create_index else "OK")

            range_start, range_source = determine_range_start(
                conn, args, chunk_rows, selected_chunk_cols
            )
            add_summary(summary, "RANGE", "range_start", range_start, "OK", range_source)

            explain_docs: dict[str, Any] = {}
            for batch in BATCHES:
                _, doc = explain_query(
                    conn,
                    range_start=range_start,
                    batch_size=batch,
                    analyze=False,
                    timeout_ms=args.explain_timeout_ms,
                )
                explain_docs[f"explain_{batch}"] = doc
                nodes = flatten_plan_nodes(doc)
                path = output_dir / f"sales_order_explain_{batch}_{stamp}.csv"
                write_csv(path, nodes)
                details_files.append(path)
                summary.append(summarize_plan(doc, batch, range_start, False))

            explain_json_path = output_dir / f"sales_order_explain_plans_{stamp}.json"
            explain_json_path.write_text(
                json.dumps(explain_docs, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            details_files.append(explain_json_path)

            if args.analyze:
                requested_batches = []
                for token in args.analyze_batches.split(","):
                    token = token.strip()
                    if token:
                        requested_batches.append(int(token))

                analyze_docs: dict[str, Any] = {}
                for batch in requested_batches:
                    if batch not in BATCHES:
                        raise ValueError(
                            f"ANALYZE разрешён только для batches {BATCHES}; получено {batch}"
                        )
                    logging.warning(
                        "Запуск EXPLAIN ANALYZE для диапазона [%s, %s).",
                        range_start,
                        range_start + batch,
                    )
                    _, doc = explain_query(
                        conn,
                        range_start=range_start,
                        batch_size=batch,
                        analyze=True,
                        timeout_ms=args.analyze_timeout_ms,
                    )
                    analyze_docs[f"analyze_{batch}"] = doc
                    nodes = flatten_plan_nodes(doc)
                    path = output_dir / f"sales_order_explain_analyze_{batch}_{stamp}.csv"
                    write_csv(path, nodes)
                    details_files.append(path)
                    summary.append(summarize_plan(doc, batch, range_start, True))

                analyze_json_path = output_dir / f"sales_order_explain_analyze_plans_{stamp}.json"
                analyze_json_path.write_text(
                    json.dumps(analyze_docs, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )
                details_files.append(analyze_json_path)
            else:
                add_summary(
                    summary,
                    "EXPLAIN_ANALYZE",
                    "status",
                    "SKIPPED",
                    "OK",
                    "Для запуска используйте --analyze. По умолчанию тяжёлая операция отключена.",
                )

            summary_path = output_dir / f"sales_order_run45_summary_{stamp}.csv"
            write_csv(summary_path, summary)

            manifest = {
                "timestamp": stamp,
                "summary_csv": str(summary_path),
                "log_file": str(log_path),
                "details_files": [str(p) for p in details_files],
                "read_only": True,
                "analyze_enabled": args.analyze,
                "range_start": range_start,
                "range_source": range_source,
            }
            manifest_path = output_dir / f"sales_order_run45_manifest_{stamp}.json"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            logging.info("Диагностика завершена.")
            logging.info("Summary CSV: %s", summary_path)
            logging.info("Manifest: %s", manifest_path)
            return 0

    except KeyboardInterrupt:
        logging.warning("Остановлено пользователем. Транзакция будет отменена.")
        return 130
    except Exception:
        logging.exception("Диагностика завершилась с ошибкой.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
