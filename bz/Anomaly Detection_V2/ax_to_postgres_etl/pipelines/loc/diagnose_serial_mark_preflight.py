from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import traceback
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2 import sql
from dotenv import load_dotenv


PROJECT_ROOT_DEFAULT = Path(r"D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2")
OUTPUT_DIR_DEFAULT = PROJECT_ROOT_DEFAULT / "ax_to_postgres_etl" / "pipelines" / "loc"

TABLES = [
    ("raw_ax", "alk_markserial"),
    ("benchmark", "alk_markserial_test"),
    ("dds", "serial_mark"),
]

KNOWN_CHECKPOINT = 5_757_444_576
DEFAULT_BATCH_SIZE = 500_000


@dataclass
class Finding:
    severity: str
    code: str
    message: str
    evidence: Any = None
    recommendation: str = ""


def json_default(value: Any):
    if isinstance(value, (datetime, Path)):
        return str(value)
    return str(value)


def rows_to_dicts(cur) -> list[dict[str, Any]]:
    columns = [d.name if hasattr(d, "name") else d[0] for d in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def execute_fetch(conn, query: str, params: tuple = ()) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(query, params)
        return rows_to_dicts(cur)


def relation_exists(conn, schema: str, table: str) -> bool:
    rows = execute_fetch(
        conn,
        """
        SELECT EXISTS (
            SELECT 1
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s
              AND c.relname = %s
              AND c.relkind IN ('r', 'p')
        ) AS exists
        """,
        (schema, table),
    )
    return bool(rows[0]["exists"])


def get_relation_overview(conn, schema: str, table: str) -> dict[str, Any]:
    if not relation_exists(conn, schema, table):
        return {"exists": False, "schema": schema, "table": table}

    rows = execute_fetch(
        conn,
        """
        SELECT
            n.nspname AS schemaname,
            c.relname,
            c.reltuples::bigint AS pg_class_estimated_rows,
            c.relpages,
            COALESCE(s.n_live_tup, 0)::bigint AS n_live_tup,
            COALESCE(s.n_dead_tup, 0)::bigint AS n_dead_tup,
            s.last_analyze,
            s.last_autoanalyze,
            s.last_vacuum,
            s.last_autovacuum,
            pg_relation_size(c.oid) AS heap_bytes,
            pg_indexes_size(c.oid) AS indexes_bytes,
            pg_total_relation_size(c.oid) AS total_bytes,
            pg_size_pretty(pg_relation_size(c.oid)) AS heap_size,
            pg_size_pretty(pg_indexes_size(c.oid)) AS indexes_size,
            pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_stat_user_tables s
          ON s.relid = c.oid
        WHERE n.nspname = %s
          AND c.relname = %s
        """,
        (schema, table),
    )
    return {"exists": True, **rows[0]}


def get_columns(conn, schema: str, table: str) -> list[dict[str, Any]]:
    if not relation_exists(conn, schema, table):
        return []
    return execute_fetch(
        conn,
        """
        SELECT
            a.attnum AS ordinal_position,
            a.attname AS column_name,
            pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
            a.attnotnull AS not_null,
            pg_get_expr(ad.adbin, ad.adrelid) AS default_expression
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_attrdef ad
          ON ad.adrelid = a.attrelid
         AND ad.adnum = a.attnum
        WHERE n.nspname = %s
          AND c.relname = %s
          AND a.attnum > 0
          AND NOT a.attisdropped
        ORDER BY a.attnum
        """,
        (schema, table),
    )


def get_indexes(conn, schema: str, table: str) -> list[dict[str, Any]]:
    if not relation_exists(conn, schema, table):
        return []
    return execute_fetch(
        conn,
        """
        SELECT
            idx.relname AS index_name,
            am.amname AS access_method,
            i.indisprimary,
            i.indisunique,
            i.indisvalid,
            i.indisready,
            i.indislive,
            pg_get_indexdef(i.indexrelid) AS index_definition,
            pg_get_expr(i.indpred, i.indrelid) AS predicate,
            ARRAY(
                SELECT pg_get_indexdef(i.indexrelid, k, true)
                FROM generate_series(1, i.indnkeyatts) AS k
                ORDER BY k
            ) AS key_expressions
        FROM pg_index i
        JOIN pg_class tbl ON tbl.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = tbl.relnamespace
        JOIN pg_class idx ON idx.oid = i.indexrelid
        JOIN pg_am am ON am.oid = idx.relam
        WHERE n.nspname = %s
          AND tbl.relname = %s
        ORDER BY i.indisprimary DESC, i.indisunique DESC, idx.relname
        """,
        (schema, table),
    )


def get_constraints(conn, schema: str, table: str) -> list[dict[str, Any]]:
    if not relation_exists(conn, schema, table):
        return []
    return execute_fetch(
        conn,
        """
        SELECT
            con.conname AS constraint_name,
            con.contype AS constraint_type,
            con.convalidated,
            pg_get_constraintdef(con.oid, true) AS definition,
            ARRAY(
                SELECT a.attname
                FROM unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
                JOIN pg_attribute a
                  ON a.attrelid = con.conrelid
                 AND a.attnum = k.attnum
                ORDER BY k.ord
            ) AS columns
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s
          AND c.relname = %s
        ORDER BY con.contype, con.conname
        """,
        (schema, table),
    )


def explain_chunk(conn, schema: str, table: str, key_column: str,
                  checkpoint: int, batch_size: int) -> dict[str, Any]:
    if not relation_exists(conn, schema, table):
        return {"status": "SKIPPED", "reason": "relation does not exist"}

    columns = {row["column_name"] for row in get_columns(conn, schema, table)}
    if key_column not in columns:
        return {"status": "SKIPPED", "reason": f"column {key_column} does not exist"}

    query = sql.SQL(
        """
        EXPLAIN (FORMAT JSON, COSTS TRUE, VERBOSE TRUE)
        SELECT {key}
        FROM {relation}
        WHERE {key} > %s
        ORDER BY {key}
        LIMIT %s
        """
    ).format(
        key=sql.Identifier(key_column),
        relation=sql.Identifier(schema, table),
    )

    with conn.cursor() as cur:
        cur.execute(query, (checkpoint, batch_size))
        plan_json = cur.fetchone()[0]

    node_types: list[str] = []

    def walk(node: Any):
        if isinstance(node, dict):
            if "Node Type" in node:
                node_types.append(node["Node Type"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(plan_json)

    return {
        "status": "OK",
        "checkpoint": checkpoint,
        "batch_size": batch_size,
        "node_types": node_types,
        "has_index_scan": any(x in node_types for x in ("Index Scan", "Index Only Scan", "Bitmap Index Scan")),
        "has_seq_scan": any(x in node_types for x in ("Seq Scan", "Parallel Seq Scan")),
        "plan": plan_json,
    }


def get_runtime(conn) -> dict[str, Any]:
    return {
        "activity": execute_fetch(
            conn,
            """
            SELECT
                pid,
                usename,
                application_name,
                client_addr,
                state,
                wait_event_type,
                wait_event,
                xact_start,
                now() - xact_start AS transaction_age,
                query_start,
                now() - query_start AS query_age,
                LEFT(query, 2000) AS query
            FROM pg_stat_activity
            WHERE datname = current_database()
              AND pid <> pg_backend_pid()
            ORDER BY xact_start NULLS LAST, query_start
            """
        ),
        "long_transactions": execute_fetch(
            conn,
            """
            SELECT
                pid,
                usename,
                application_name,
                state,
                wait_event_type,
                wait_event,
                xact_start,
                now() - xact_start AS transaction_age,
                LEFT(query, 2000) AS query
            FROM pg_stat_activity
            WHERE datname = current_database()
              AND pid <> pg_backend_pid()
              AND xact_start IS NOT NULL
              AND now() - xact_start > interval '5 minutes'
            ORDER BY xact_start
            """
        ),
        "vacuum_progress": execute_fetch(
            conn,
            """
            SELECT
                pid,
                datname,
                relid::regclass::text AS relation,
                phase,
                heap_blks_total,
                heap_blks_scanned,
                heap_blks_vacuumed,
                index_vacuum_count,
                num_dead_item_ids
            FROM pg_stat_progress_vacuum
            ORDER BY pid
            """
        ),
        "index_progress": execute_fetch(
            conn,
            """
            SELECT
                pid,
                datname,
                relid::regclass::text AS relation,
                index_relid::regclass::text AS index_relation,
                command,
                phase,
                blocks_total,
                blocks_done,
                tuples_total,
                tuples_done
            FROM pg_stat_progress_create_index
            ORDER BY pid
            """
        ),
        "relation_locks": execute_fetch(
            conn,
            """
            SELECT
                l.pid,
                n.nspname AS schemaname,
                c.relname,
                l.mode,
                l.granted,
                a.state,
                a.wait_event_type,
                a.wait_event,
                LEFT(a.query, 1000) AS query
            FROM pg_locks l
            JOIN pg_class c ON c.oid = l.relation
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_stat_activity a ON a.pid = l.pid
            WHERE (n.nspname, c.relname) IN (
                ('raw_ax', 'alk_markserial'),
                ('benchmark', 'alk_markserial_test'),
                ('dds', 'serial_mark')
            )
            ORDER BY n.nspname, c.relname, l.granted, l.pid
            """
        ),
    }


def get_postgres_state(conn) -> dict[str, Any]:
    data_directory = execute_fetch(conn, "SHOW data_directory")[0]["data_directory"]
    disk = shutil.disk_usage(data_directory)

    result = {
        "data_directory": data_directory,
        "disk": {
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
            "free_gb": round(disk.free / 1024**3, 2),
        },
        "wal": execute_fetch(
            conn,
            """
            SELECT
                wal_records,
                wal_fpi,
                wal_bytes,
                wal_buffers_full,
                stats_reset
            FROM pg_stat_wal
            """
        ),
    }

    try:
        result["checkpointer"] = execute_fetch(
            conn,
            """
            SELECT *
            FROM pg_stat_checkpointer
            """
        )
    except Exception as exc:
        conn.rollback()
        result["checkpointer_error"] = str(exc)

    return result


def analyze(report: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []

    tables = report["tables"]
    raw = tables["raw_ax.alk_markserial"]
    benchmark = tables["benchmark.alk_markserial_test"]
    dds = tables["dds.serial_mark"]

    # Source architecture
    findings.append(
        Finding(
            severity="WARN",
            code="SOURCE_IS_RAW",
            message="Stage serial_mark currently points to raw_ax.alk_markserial, not normalized staging.",
            evidence={"configured_source_seen_in_preflight": "raw_ax.alk_markserial"},
            recommendation=(
                "Confirm YAML stage source. For NORMALIZED_STAGING strategy, use "
                "benchmark.alk_markserial_test with bigint recid as source for DDS."
            ),
        )
    )

    # Row estimate quality
    raw_live = raw.get("overview", {}).get("n_live_tup", 0) or 0
    raw_reltuples = raw.get("overview", {}).get("pg_class_estimated_rows", 0) or 0
    raw_size = raw.get("overview", {}).get("total_bytes", 0) or 0

    if raw_live == 0 and (raw_reltuples > 0 or raw_size > 10 * 1024**3):
        findings.append(
            Finding(
                severity="ERROR",
                code="BAD_ROW_ESTIMATE_FALLBACK",
                message=(
                    "pg_stat_user_tables.n_live_tup is zero, but the relation is large. "
                    "Preflight must not classify it as a small table."
                ),
                evidence={
                    "n_live_tup": raw_live,
                    "pg_class_reltuples": raw_reltuples,
                    "total_size": raw.get("overview", {}).get("total_size"),
                },
                recommendation=(
                    "Use pg_class.reltuples as fallback when n_live_tup is NULL or 0. "
                    "Also treat a relation larger than a configurable size threshold as large."
                ),
            )
        )

    # Source index
    raw_indexes = raw.get("indexes", [])
    bigint_indexes = [
        i for i in raw_indexes
        if i.get("access_method") == "btree"
        and i.get("key_expressions")
        and i["key_expressions"][0] == "recid_bigint"
        and i.get("indisvalid")
        and i.get("indisready")
    ]
    if bigint_indexes:
        findings.append(
            Finding(
                severity="OK",
                code="RAW_BIGINT_INDEX_PRESENT",
                message="Usable B-tree index on raw_ax.alk_markserial(recid_bigint) exists.",
                evidence=bigint_indexes,
            )
        )
    else:
        findings.append(
            Finding(
                severity="ERROR",
                code="RAW_BIGINT_INDEX_MISSING",
                message="No usable B-tree index found on raw_ax.alk_markserial(recid_bigint).",
                evidence=raw_indexes,
                recommendation=(
                    "Do not run RAW chunk loading by recid_bigint until the source strategy is clarified. "
                    "Prefer normalized staging or a verified existing index. Do not mass UPDATE RAW."
                ),
            )
        )

    # DDS conflict target
    dds_indexes = dds.get("indexes", [])
    unique_rec_id_indexes = [
        i for i in dds_indexes
        if i.get("indisunique")
        and i.get("indisvalid")
        and i.get("indisready")
        and i.get("key_expressions") == ["rec_id"]
    ]
    dds_constraints = dds.get("constraints", [])
    unique_rec_id_constraints = [
        c for c in dds_constraints
        if c.get("constraint_type") in ("p", "u")
        and c.get("columns") == ["rec_id"]
        and c.get("convalidated")
    ]

    if unique_rec_id_indexes or unique_rec_id_constraints:
        findings.append(
            Finding(
                severity="OK",
                code="DDS_CONFLICT_TARGET_VALID",
                message="dds.serial_mark(rec_id) has a valid uniqueness guarantee for ON CONFLICT.",
                evidence={
                    "unique_indexes": unique_rec_id_indexes,
                    "constraints": unique_rec_id_constraints,
                },
                recommendation=(
                    "Preflight should accept either a valid unique/primary constraint or "
                    "a valid unique index matching the conflict target."
                ),
            )
        )
    else:
        findings.append(
            Finding(
                severity="ERROR",
                code="DDS_CONFLICT_TARGET_MISSING",
                message="No valid unique constraint or unique index found for dds.serial_mark(rec_id).",
                evidence={"indexes": dds_indexes, "constraints": dds_constraints},
                recommendation=(
                    "Do not use ON CONFLICT (rec_id) until a valid uniqueness object exists. "
                    "Before creating one, check duplicates and disk/WAL impact."
                ),
            )
        )

    # Explain plans
    for name, plan in report.get("plans", {}).items():
        if plan.get("status") != "OK":
            findings.append(
                Finding(
                    severity="WARN",
                    code=f"PLAN_SKIPPED_{name.upper()}",
                    message=f"EXPLAIN for {name} was skipped.",
                    evidence=plan,
                )
            )
            continue
        if plan.get("has_seq_scan"):
            findings.append(
                Finding(
                    severity="ERROR",
                    code=f"SEQ_SCAN_{name.upper()}",
                    message=f"EXPLAIN for {name} contains Seq Scan/Parallel Seq Scan.",
                    evidence={"node_types": plan.get("node_types")},
                    recommendation="Do not start the large-table stage until the indexed plan is restored.",
                )
            )
        elif plan.get("has_index_scan"):
            findings.append(
                Finding(
                    severity="OK",
                    code=f"INDEX_PLAN_{name.upper()}",
                    message=f"EXPLAIN for {name} uses an index access path.",
                    evidence={"node_types": plan.get("node_types")},
                )
            )
        else:
            findings.append(
                Finding(
                    severity="WARN",
                    code=f"PLAN_UNCLEAR_{name.upper()}",
                    message=f"EXPLAIN for {name} has no recognized index or sequential scan node.",
                    evidence={"node_types": plan.get("node_types")},
                )
            )

    # Long transactions
    long_tx = report.get("runtime", {}).get("long_transactions", [])
    if long_tx:
        findings.append(
            Finding(
                severity="WARN",
                code="LONG_TRANSACTIONS",
                message=f"Found {len(long_tx)} transactions older than 5 minutes.",
                evidence=long_tx,
                recommendation="Identify owners and purpose before starting full/resume. Do not terminate automatically.",
            )
        )
    else:
        findings.append(
            Finding(
                severity="OK",
                code="NO_LONG_TRANSACTIONS",
                message="No transactions older than 5 minutes were found.",
            )
        )

    # Benchmark suitability
    if benchmark.get("overview", {}).get("exists"):
        benchmark_indexes = benchmark.get("indexes", [])
        benchmark_recid = [
            i for i in benchmark_indexes
            if i.get("access_method") == "btree"
            and i.get("key_expressions")
            and i["key_expressions"][0] == "recid"
            and i.get("indisvalid")
            and i.get("indisready")
        ]
        if benchmark_recid:
            findings.append(
                Finding(
                    severity="OK",
                    code="BENCHMARK_SOURCE_READY",
                    message="benchmark.alk_markserial_test has a usable B-tree index on recid.",
                    evidence=benchmark_recid,
                    recommendation="This is the preferred source for benchmark -> DDS keyset loading.",
                )
            )
        else:
            findings.append(
                Finding(
                    severity="ERROR",
                    code="BENCHMARK_RECID_INDEX_MISSING",
                    message="benchmark.alk_markserial_test has no usable B-tree index on recid.",
                    evidence=benchmark_indexes,
                )
            )

    return findings


def write_text_report(path: Path, report: dict[str, Any], findings: list[Finding]) -> None:
    lines: list[str] = []
    lines.append("=" * 80)
    lines.append("SERIAL_MARK PREFLIGHT LOCALIZATION REPORT")
    lines.append("=" * 80)
    lines.append(f"Generated: {report['generated_at']}")
    lines.append(f"Database: {report['connection']['database']}")
    lines.append("Read-only diagnostics; no COUNT(*), no EXPLAIN ANALYZE, no data changes.")
    lines.append("")

    lines.append("FINDINGS")
    lines.append("-" * 80)
    for finding in findings:
        lines.append(f"[{finding.severity}] {finding.code}: {finding.message}")
        if finding.recommendation:
            lines.append(f"  Recommendation: {finding.recommendation}")
        if finding.evidence is not None:
            evidence = json.dumps(finding.evidence, ensure_ascii=False, indent=2, default=json_default)
            for line in evidence.splitlines():
                lines.append(f"  {line}")
        lines.append("")

    lines.append("TABLE OVERVIEW")
    lines.append("-" * 80)
    for table_name, data in report["tables"].items():
        lines.append(f"{table_name}")
        lines.append(json.dumps(data["overview"], ensure_ascii=False, indent=2, default=json_default))
        lines.append("")

    lines.append("EXPLAIN PLAN NODE TYPES")
    lines.append("-" * 80)
    for name, plan in report.get("plans", {}).items():
        lines.append(f"{name}: {plan.get('node_types', plan)}")
    lines.append("")

    lines.append("LONG TRANSACTIONS")
    lines.append("-" * 80)
    lines.append(json.dumps(
        report.get("runtime", {}).get("long_transactions", []),
        ensure_ascii=False,
        indent=2,
        default=json_default,
    ))

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only localization of serial_mark preflight errors."
    )
    parser.add_argument("--host", default=os.getenv("PGHOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PGPORT", "5432")))
    parser.add_argument("--database", default=os.getenv("PGDATABASE", "wms_analysis"))
    parser.add_argument("--user", default=os.getenv("PGUSER", "postgres"))
    parser.add_argument("--password-env", default="DB_PASSWORD")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--checkpoint", type=int, default=KNOWN_CHECKPOINT)
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR_DEFAULT))
    parser.add_argument("--statement-timeout-ms", type=int, default=30_000)
    args = parser.parse_args()

    project_root = PROJECT_ROOT_DEFAULT
    load_dotenv(project_root / ".env")
    load_dotenv()

    password = os.getenv(args.password_env)
    if not password:
        print(
            f"ERROR: environment variable {args.password_env} is not set. "
            f"Add it to {project_root / '.env'} or the current environment.",
            file=sys.stderr,
        )
        return 2

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"serial_mark_preflight_localization_{timestamp}.json"
    txt_path = output_dir / f"serial_mark_preflight_localization_{timestamp}.txt"

    report: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(),
        "connection": {
            "host": args.host,
            "port": args.port,
            "database": args.database,
            "user": args.user,
        },
        "parameters": {
            "batch_size": args.batch_size,
            "checkpoint": args.checkpoint,
            "statement_timeout_ms": args.statement_timeout_ms,
        },
        "tables": {},
        "plans": {},
    }

    conn = None
    try:
        conn = psycopg2.connect(
            host=args.host,
            port=args.port,
            database=args.database,
            user=args.user,
            password=password,
            application_name="serial_mark_preflight_localization",
        )
        conn.autocommit = False

        with conn.cursor() as cur:
            cur.execute("BEGIN TRANSACTION READ ONLY")
            cur.execute("SET LOCAL statement_timeout = %s", (args.statement_timeout_ms,))
            cur.execute("SET LOCAL lock_timeout = '3s'")
            cur.execute("SET LOCAL idle_in_transaction_session_timeout = '60s'")

        for schema, table in TABLES:
            key = f"{schema}.{table}"
            report["tables"][key] = {
                "overview": get_relation_overview(conn, schema, table),
                "columns": get_columns(conn, schema, table),
                "indexes": get_indexes(conn, schema, table),
                "constraints": get_constraints(conn, schema, table),
            }

        report["plans"]["raw_recid_bigint"] = explain_chunk(
            conn,
            "raw_ax",
            "alk_markserial",
            "recid_bigint",
            args.checkpoint,
            args.batch_size,
        )
        report["plans"]["benchmark_recid"] = explain_chunk(
            conn,
            "benchmark",
            "alk_markserial_test",
            "recid",
            args.checkpoint,
            args.batch_size,
        )

        report["runtime"] = get_runtime(conn)
        report["postgres"] = get_postgres_state(conn)

        findings = analyze(report)
        report["findings"] = [asdict(f) for f in findings]

        conn.rollback()

        json_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=json_default),
            encoding="utf-8",
        )
        write_text_report(txt_path, report, findings)

        errors = sum(1 for f in findings if f.severity == "ERROR")
        warnings = sum(1 for f in findings if f.severity == "WARN")
        oks = sum(1 for f in findings if f.severity == "OK")

        print("=" * 80)
        print("SERIAL_MARK PREFLIGHT LOCALIZATION")
        print("=" * 80)
        print(f"OK:       {oks}")
        print(f"WARN:     {warnings}")
        print(f"ERROR:    {errors}")
        print(f"JSON:     {json_path}")
        print(f"TEXT:     {txt_path}")
        print("=" * 80)

        for finding in findings:
            print(f"[{finding.severity}] {finding.code}: {finding.message}")

        return 1 if errors else 0

    except KeyboardInterrupt:
        if conn is not None:
            conn.rollback()
        print("\nCancelled by user. Transaction rolled back.", file=sys.stderr)
        return 130
    except Exception as exc:
        if conn is not None:
            conn.rollback()

        error_path = output_dir / f"serial_mark_preflight_localization_ERROR_{timestamp}.txt"
        error_text = (
            f"Timestamp: {datetime.now().isoformat()}\n"
            f"Error: {exc}\n\n"
            f"{traceback.format_exc()}"
        )
        error_path.write_text(error_text, encoding="utf-8")
        print(f"ERROR: {exc}", file=sys.stderr)
        print(f"Diagnostic error saved to: {error_path}", file=sys.stderr)
        return 2
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
