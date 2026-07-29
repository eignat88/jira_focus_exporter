"""CLI interface for ETL management."""

import argparse
import sys
import json
from typing import List, Optional


def create_parser() -> argparse.ArgumentParser:
    """Create ETL CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="etl",
        description="ETL Pipeline Manager — Parallel Loader V2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Load table with resume
  etl load --table ALK_MARKSERIAL --mode resume

  # Load with custom workers
  etl load --table INVENTTABLE --mode reload --workers 8

  # Check status
  etl status --table ALK_MARKSERIAL

  # Run validation
  etl validate --table ALK_MARKSERIAL

  # View history
  etl history --table ALK_MARKSERIAL --limit 10

  # Auto-tune
  etl tune --table ALK_MARKSERIAL
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Load command
    load_parser = subparsers.add_parser("load", help="Load a table")
    load_parser.add_argument("--table", "-t", required=True, help="Table name")
    load_parser.add_argument("--mode", "-m", default="resume",
                           choices=["full", "resume", "reload", "incremental"],
                           help="Load mode")
    load_parser.add_argument("--workers", "-w", type=int, help="Number of workers")
    load_parser.add_argument("--fetch-size", type=int, help="Fetch size")
    load_parser.add_argument("--commit-size", type=int, help="Commit size")
    load_parser.add_argument("--config", "-c", default="config.yaml", help="Config file")

    # Status command
    status_parser = subparsers.add_parser("status", help="Check load status")
    status_parser.add_argument("--table", "-t", help="Table name (all if omitted)")
    status_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # Validate command
    validate_parser = subparsers.add_parser("validate", help="Run validation checks")
    validate_parser.add_argument("--table", "-t", required=True, help="Table name")
    validate_parser.add_argument("--check", choices=["row_count", "duplicates", "nulls", "all"],
                               default="all", help="Check type")

    # History command
    history_parser = subparsers.add_parser("history", help="View load history")
    history_parser.add_argument("--table", "-t", help="Table name")
    history_parser.add_argument("--limit", "-l", type=int, default=10, help="Number of entries")
    history_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # Tune command
    tune_parser = subparsers.add_parser("tune", help="Auto-tune parameters")
    tune_parser.add_argument("--table", "-t", help="Table name")
    tune_parser.add_argument("--apply", action="store_true", help="Apply changes")

    # Compare command
    compare_parser = subparsers.add_parser("compare", help="Compare with previous run")
    compare_parser.add_argument("--table", "-t", required=True, help="Table name")
    compare_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # Metrics command
    metrics_parser = subparsers.add_parser("metrics", help="Export metrics")
    metrics_parser.add_argument("--table", "-t", help="Table name")
    metrics_parser.add_argument("--format", choices=["json", "prometheus", "csv"],
                              default="json", help="Export format")
    metrics_parser.add_argument("--output", "-o", help="Output directory")

    # Retry command
    retry_parser = subparsers.add_parser("retry", help="Retry failed load")
    retry_parser.add_argument("--table", "-t", required=True, help="Table name")
    retry_parser.add_argument("--max-retries", type=int, default=3, help="Max retries")

    return parser


def format_table(data: list, headers: list) -> str:
    """Format data as ASCII table."""
    if not data:
        return "No data"

    # Calculate column widths
    widths = [len(h) for h in headers]
    for row in data:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))

    # Build table
    lines = []
    header_line = " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    lines.append(header_line)
    lines.append("-+-".join("-" * w for w in widths))

    for row in data:
        row_line = " | ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row))
        lines.append(row_line)

    return "\n".join(lines)


def cmd_load(args):
    """Execute load command."""
    from ax_to_postgres_etl.loader.parallel_loader_v2 import ParallelLoaderV2
    from ax_to_postgres_etl.configs.settings import load_settings

    settings = load_settings(args.config)

    # Override config with CLI args
    if args.workers:
        settings.etl.parallel.workers = args.workers
    if args.fetch_size:
        settings.etl.parallel.fetch_size = args.fetch_size
    if args.commit_size:
        settings.etl.parallel.commit_size = args.commit_size

    print(f"Loading {args.table} in {args.mode} mode...")
    # Would connect and load here
    print("Load complete.")


def cmd_status(args):
    """Execute status command."""
    print(f"Status for table: {args.table or 'ALL'}")
    # Would query etl.load_run here


def cmd_validate(args):
    """Execute validate command."""
    print(f"Validating {args.table}...")
    # Would run data quality checks here


def cmd_history(args):
    """Execute history command."""
    print(f"Load history for: {args.table or 'ALL'}")
    # Would query etl.load_run here


def cmd_tune(args):
    """Execute tune command."""
    print(f"Auto-tuning for: {args.table or 'ALL'}")
    # Would analyze metrics and suggest changes


def cmd_compare(args):
    """Execute compare command."""
    print(f"Comparing runs for: {args.table}")
    # Would compare current vs previous run


def cmd_metrics(args):
    """Execute metrics command."""
    print(f"Exporting metrics in {args.format} format...")
    # Would export metrics


def cmd_retry(args):
    """Execute retry command."""
    print(f"Retrying load for: {args.table} (max {args.max_retries} retries)")
    # Would retry failed load


def main(argv: Optional[List[str]] = None):
    """Main CLI entry point."""
    parser = create_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return

    commands = {
        "load": cmd_load,
        "status": cmd_status,
        "validate": cmd_validate,
        "history": cmd_history,
        "tune": cmd_tune,
        "compare": cmd_compare,
        "metrics": cmd_metrics,
        "retry": cmd_retry,
    }

    cmd_func = commands.get(args.command)
    if cmd_func:
        cmd_func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
