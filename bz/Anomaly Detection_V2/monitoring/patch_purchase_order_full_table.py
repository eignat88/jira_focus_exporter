#!/usr/bin/env python
"""
patch_purchase_order_full_table.py

Guarded hotfix for Unified ETL.

What it changes:
1. raw_to_dds.py:
   get_boundaries() returns one synthetic range for chunk_strategy=full_table,
   so it does not execute MIN/MAX on recid_bigint.

2. runner.py:
   the direct get_boundaries() call is wrapped with a full_table branch.

The script:
- creates timestamped backups;
- supports --dry-run;
- refuses to modify a file when the expected pattern is not found;
- never touches PostgreSQL.

Important:
This hotfix addresses the confirmed recid_bigint boundary failure.
It does not guess the correct replacements for missing mapping columns
vendaccount and orderdate.
"""

from __future__ import annotations

import argparse
import difflib
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path


PROJECT_DEFAULT = Path(r"D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def patch_get_boundaries(text: str) -> tuple[str, bool]:
    # Insert immediately after def get_boundaries(...):
    pattern = re.compile(
        r"(?P<indent>^[ \t]*)def[ \t]+get_boundaries"
        r"\((?P<args>[^)]*)\)[ \t]*(?P<ret>->[^\n:]+)?[ \t]*:\s*\n",
        re.MULTILINE,
    )
    match = pattern.search(text)
    if not match:
        return text, False

    # Avoid duplicate patch.
    tail = text[match.end(): match.end() + 500]
    if 'chunk_strategy' in tail and 'full_table' in tail and 'return 0, 1' in tail:
        return text, True

    indent = match.group("indent") + "    "
    guard = (
        f'{indent}strategy = getattr(spec, "chunk_strategy", None)\n'
        f'{indent}if strategy == "full_table":\n'
        f'{indent}    # One logical chunk; do not query MIN/MAX on a chunk key.\n'
        f'{indent}    return 0, 1\n\n'
    )
    return text[:match.end()] + guard + text[match.end():], True


def patch_runner_call(text: str) -> tuple[str, bool]:
    # Exact confirmed line from traceback context.
    pattern = re.compile(
        r"(?P<indent>^[ \t]*)lower,[ \t]*upper[ \t]*="
        r"[ \t]*adapter\.get_boundaries\(rt\.data,[ \t]*spec\)[ \t]*$",
        re.MULTILINE,
    )
    match = pattern.search(text)
    if not match:
        # Already patched is accepted.
        if (
            'spec.chunk_strategy == "full_table"' in text
            and "adapter.get_boundaries(rt.data, spec)" in text
        ):
            return text, True
        return text, False

    indent = match.group("indent")
    replacement = (
        f'{indent}if spec.chunk_strategy == "full_table":\n'
        f'{indent}    # Full-table stages do not require source key boundaries.\n'
        f'{indent}    lower, upper = 0, 1\n'
        f'{indent}else:\n'
        f'{indent}    lower, upper = adapter.get_boundaries(rt.data, spec)'
    )
    return text[:match.start()] + replacement + text[match.end():], True


def show_diff(path: Path, old: str, new: str) -> None:
    diff = difflib.unified_diff(
        old.splitlines(),
        new.splitlines(),
        fromfile=str(path),
        tofile=str(path) + " (patched)",
        lineterm="",
    )
    print("\n".join(diff))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=PROJECT_DEFAULT)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.apply and args.dry_run:
        parser.error("Use either --apply or --dry-run.")

    dry_run = not args.apply

    raw_to_dds = args.project_root / "ax_to_postgres_etl" / "pipelines" / "raw_to_dds.py"
    runner = args.project_root / "ax_to_postgres_etl" / "pipelines" / "runner.py"

    missing = [p for p in (raw_to_dds, runner) if not p.exists()]
    if missing:
        print("ERROR: required files not found:", file=sys.stderr)
        for path in missing:
            print(f"  {path}", file=sys.stderr)
        return 2

    raw_old = read_text(raw_to_dds)
    runner_old = read_text(runner)

    raw_new, raw_ok = patch_get_boundaries(raw_old)
    runner_new, runner_ok = patch_runner_call(runner_old)

    if not raw_ok:
        print("ERROR: get_boundaries() pattern not found in raw_to_dds.py.", file=sys.stderr)
        return 3
    if not runner_ok:
        print(
            "ERROR: adapter.get_boundaries(rt.data, spec) pattern not found in runner.py.",
            file=sys.stderr,
        )
        return 4

    print("=" * 78)
    print("raw_to_dds.py diff")
    print("=" * 78)
    show_diff(raw_to_dds, raw_old, raw_new)

    print("\n" + "=" * 78)
    print("runner.py diff")
    print("=" * 78)
    show_diff(runner, runner_old, runner_new)

    if dry_run:
        print("\nDRY RUN: no files modified.")
        print("Run again with --apply after reviewing the diff.")
        return 0

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = args.project_root / "logs" / "3" / f"purchase_order_code_backup_{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)

    shutil.copy2(raw_to_dds, backup_dir / "raw_to_dds.py")
    shutil.copy2(runner, backup_dir / "runner.py")

    write_text(raw_to_dds, raw_new)
    write_text(runner, runner_new)

    print(f"\nPATCH APPLIED.")
    print(f"Backups: {backup_dir}")
    print("Next:")
    print("  python -m compileall ax_to_postgres_etl")
    print("  python -m pytest")
    print("  python -m ax_to_postgres_etl.pipelines.dds_cli --mode preflight --stage purchase_order")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
