from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml

from .contracts import PipelineSpec
from .raw_to_dds import ColumnMap, RawToDdsAdapter
from .runner import PipelineRunner


def load_config(config_path: str = None) -> dict:
    """Load pipeline configuration from YAML."""
    if config_path is None:
        project_root = Path(__file__).parent.parent.parent
        for candidate in [
            project_root / "config" / "raw_to_dds.yaml",
            Path("config") / "raw_to_dds.yaml",
        ]:
            if candidate.exists():
                config_path = str(candidate)
                break

    if config_path and Path(config_path).exists():
        with open(config_path) as f:
            return yaml.safe_load(f)
    return {}


def build_stage_spec(stage_config: dict, pipeline_config: dict) -> tuple[PipelineSpec, RawToDdsAdapter]:
    """Build PipelineSpec and adapter from YAML stage config."""
    source = stage_config["source"]
    target = stage_config["target"]
    execution = stage_config.get("execution", {})

    key_type = source.get("key_type", "bigint")

    spec = PipelineSpec(
        name=pipeline_config.get("name", "raw_to_dds"),
        source_system="PostgreSQL RAW",
        source_schema=source["schema"],
        source_table=source["table"],
        target_schema=target["schema"],
        target_table=target["table"],
        key_column=source["key_column"],
        batch_size=execution.get("batch_size", pipeline_config.get("default_batch_size", 250000)),
        count_mode=execution.get("count_mode", "estimate"),
        load_mode="resume",
    )

    columns = [ColumnMap(c["target"], c["expression"]) for c in stage_config.get("columns", [])]
    conflict_column = target.get("conflict_key")

    adapter = RawToDdsAdapter(
        columns=columns,
        conflict_column=conflict_column,
        key_type=key_type,
    )
    return spec, adapter


def parse_args():
    parser = argparse.ArgumentParser(description="PostgreSQL RAW -> DDS pipeline")
    parser.add_argument("--mode", default="resume",
                       choices=["preflight", "full", "resume", "restart-stage", "validate-only", "status"])
    parser.add_argument("--stage", default=None, help="Stage name to run")
    parser.add_argument("--batch-size", type=int, default=250000)
    parser.add_argument("--count-mode", default="estimate",
                       choices=["none", "estimate", "exact", "cached"])
    parser.add_argument("--config", default=None, help="Path to YAML config")
    parser.add_argument("--truncate-target", action="store_true", help="Truncate target tables before load")
    parser.add_argument("--ascii-progress", action="store_true", help="Use ASCII progress bar")
    return parser.parse_args()


def show_status(config: dict):
    """Show current pipeline status."""
    print("\n" + "=" * 70)
    print("RAW -> DDS PIPELINE STATUS")
    print("=" * 70)

    import psycopg2
    pipeline_config = config.get("pipeline", {})
    dsn = os.environ.get(
        "PG_DSN",
        f"host={pipeline_config.get('host', 'localhost')} "
        f"port={pipeline_config.get('port', 5432)} "
        f"dbname={pipeline_config.get('database', 'wms_analysis')} "
        f"user={pipeline_config.get('user', 'postgres')} "
        f"password={pipeline_config.get('password', '123')}",
    )

    try:
        conn = psycopg2.connect(dsn)
        cur = conn.cursor()

        cur.execute("""
            SELECT run_id, pipeline_name, status, source_table, target_table,
                   started_at, finished_at, total_chunks, completed_chunks
            FROM etl.load_run
            WHERE pipeline_name = 'raw_to_dds'
            ORDER BY run_id DESC
            LIMIT 5
        """)
        runs = cur.fetchall()

        if runs:
            print(f"\n{'Run ID':<10} {'Status':<15} {'Source':<25} {'Target':<25} {'Started':<20}")
            print("-" * 95)
            for run in runs:
                print(f"{run[0]:<10} {run[2]:<15} {run[3]:<25} {run[4]:<25} {str(run[5])[:19]:<20}")
        else:
            print("\nNo runs found.")

        conn.close()
    except Exception as e:
        print(f"\nError: {e}")

    print("=" * 70)


def run_preflight(stages: list, pipeline_config: dict, dsn: str):
    """
    Run preflight checks ONLY.
    CRITICAL: This MUST NOT create any records in etl.load_run.
    """
    print("\n" + "=" * 70)
    print("RAW -> DDS PREFLIGHT")
    print("=" * 70)
    print("NOTE: Preflight checks configuration only. No data will be modified.")
    print("=" * 70)

    import psycopg2

    all_passed = True

    for stage_config in stages:
        if not stage_config.get("enabled", True):
            print(f"\n[{stage_config['name']}] SKIPPED (disabled)")
            continue

        print(f"\n[{stage_config['name']}]")

        spec, adapter = build_stage_spec(stage_config, pipeline_config)

        try:
            # Create a READ-ONLY connection for preflight
            conn = psycopg2.connect(dsn)
            conn.set_session(readonly=True, autocommit=True)

            results = adapter.preflight(conn, spec)
            conn.close()

            for check in results["checks"]:
                status = "[OK]" if check["passed"] else "[FAIL]"
                print(f"  {status} {check['name']}: {check['message']}")
                if not check["passed"]:
                    all_passed = False

        except Exception as e:
            print(f"  [ERROR] Connection failed: {e}")
            all_passed = False

    print("\n" + "=" * 70)
    if all_passed:
        print("Preflight completed successfully")
        print("No records created in etl.load_run")
    else:
        print("Preflight FAILED")
    print("=" * 70)

    return 0 if all_passed else 3


def main() -> int:
    args = parse_args()
    config = load_config(args.config)

    pipeline_config = config.get("pipeline", {})
    stages = config.get("stages", [])

    dsn = os.environ.get(
        "PG_DSN",
        f"host={pipeline_config.get('host', 'localhost')} "
        f"port={pipeline_config.get('port', 5432)} "
        f"dbname={pipeline_config.get('database', 'wms_analysis')} "
        f"user={pipeline_config.get('user', 'postgres')} "
        f"password={pipeline_config.get('password', '123')}",
    )

    # Status mode
    if args.mode == "status":
        show_status(config)
        return 0

    # Preflight mode - NO data modification, NO run creation
    if args.mode == "preflight":
        if args.stage:
            stages = [s for s in stages if s["name"] == args.stage]
        return run_preflight(stages, pipeline_config, dsn)

    # Filter stages if --stage specified
    if args.stage:
        stages = [s for s in stages if s["name"] == args.stage]
        if not stages:
            print(f"ERROR: Stage '{args.stage}' not found")
            return 1

    if not stages:
        print("ERROR: No stages defined")
        return 1

    # Recover stale runs before starting
    runner = PipelineRunner(dsn)
    runner.recover_stale_runs(pipeline_config.get("name", "raw_to_dds"))

    # Run each enabled stage
    for stage_config in stages:
        if not stage_config.get("enabled", True):
            print(f"Skipping disabled stage: {stage_config['name']}")
            continue

        print(f"\n{'=' * 70}")
        print(f"Running stage: {stage_config['name']}")
        print(f"{'=' * 70}")

        spec, adapter = build_stage_spec(stage_config, pipeline_config)
        spec = PipelineSpec(**{**spec.__dict__, "batch_size": args.batch_size, "count_mode": args.count_mode})

        try:
            run_id = runner.run(spec, adapter)
            print(f"Stage {stage_config['name']} completed (run_id={run_id})")
        except RuntimeError as e:
            if "already running" in str(e).lower():
                print(f"Stage {stage_config['name']} blocked: {e}")
                return 2
            raise
        except Exception as e:
            print(f"Stage {stage_config['name']} failed: {e}")
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
