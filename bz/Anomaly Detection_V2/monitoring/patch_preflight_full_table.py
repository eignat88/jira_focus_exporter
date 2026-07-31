from __future__ import annotations

import argparse
import difflib
import shutil
from datetime import datetime
from pathlib import Path


DEFAULT_ROOT = Path(r"D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2")
UNSAFE_CONDITION = (
    'chunk_strategy == "numeric_text_range" '
    'or self._key_type == "numeric_text"'
)
SAFE_CONDITION = 'chunk_strategy == "numeric_text_range"'


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    target = (
        args.project_root
        / "ax_to_postgres_etl"
        / "pipelines"
        / "preflight.py"
    )
    if not target.exists():
        raise SystemExit(f"File not found: {target}")

    old = target.read_text(encoding="utf-8-sig")
    matches = old.count(UNSAFE_CONDITION)
    if matches == 0:
        if SAFE_CONDITION in old:
            print("Known unsafe condition is already absent.")
            return 0
        raise SystemExit("Expected preflight condition was not found; no changes made.")

    new = old.replace(UNSAFE_CONDITION, SAFE_CONDITION)
    diff = "\n".join(
        difflib.unified_diff(
            old.splitlines(),
            new.splitlines(),
            fromfile=str(target),
            tofile=str(target) + " (patched)",
            lineterm="",
        )
    )
    print(diff)

    if not args.apply:
        print("\nDRY RUN: no files changed. Re-run with --apply.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = (
        args.project_root
        / "logs"
        / "3"
        / f"preflight_backup_{stamp}"
    )
    backup_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(target, backup_dir / target.name)
    target.write_text(new, encoding="utf-8", newline="\n")

    print(f"Patched {matches} condition(s).")
    print(f"Backup: {backup_dir / target.name}")
    print("Next: run compileall, generated tests, and purchase_order preflight.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
