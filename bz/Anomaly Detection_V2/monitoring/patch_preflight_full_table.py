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

INDEX_ANCHOR = """    def check_indexes(self):
        cur = self.conn.cursor()

        # Check B-tree index on source for chunk key
"""
INDEX_REPLACEMENT = """    def check_indexes(self):
        cur = self.conn.cursor()
        chunk_strategy = (
            self.stage.get("execution", {}).get("chunk_strategy")
            or self.stage.get("chunk_strategy")
            or "numeric_range"
        )

        if chunk_strategy == "full_table":
            self._ok(
                "source_btree_index",
                "B-tree chunk index is not required for full_table strategy",
            )
        else:
            self._check_source_chunk_index(cur)

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

    def _check_source_chunk_index(self, cur):
        # Check B-tree index on source for chunk key
"""

QUERY_ANCHOR = """        try:
            if chunk_strategy == "numeric_text_range" or self._key_type == "numeric_text":
"""
QUERY_REPLACEMENT = """        try:
            if chunk_strategy == "full_table":
                sql = (
                    f"EXPLAIN (FORMAT JSON) "
                    f"SELECT 1 FROM {self._source_schema}.{self._source_table}"
                )
                cur.execute(sql)
                plan_json = cur.fetchone()[0]
                node_type = self._extract_plan_node_type(plan_json)
                self._ok(
                    "query_plan",
                    f"Full-table EXPLAIN completed; plan uses {node_type}",
                )
                return

            if chunk_strategy == "numeric_text_range":
"""

SECOND_UNSAFE = """            if chunk_strategy == "numeric_text_range" or self._key_type == "numeric_text":
"""
SECOND_SAFE = """            if chunk_strategy == "numeric_text_range":
"""


def apply_patch(text: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    new = text

    if INDEX_ANCHOR in new:
        new = new.replace(INDEX_ANCHOR, INDEX_REPLACEMENT, 1)
        # Remove the old target unique-constraint block, now moved above.
        old_target_block = """        # Check unique constraint on target for conflict key
        if self._conflict_key:
            uq = _find_unique_constraint(cur, self._target_schema,
                                         self._target_table, self._conflict_key)
            if uq:
                self._ok("target_unique_constraint",
                         f"Unique constraint: {uq['name']}")
            else:
                self._error("target_unique_constraint",
                            f"No unique constraint on {self._conflict_key}")

"""
        # Remove the second occurrence only.
        first = new.find(old_target_block)
        second = new.find(old_target_block, first + len(old_target_block))
        if second >= 0:
            new = new[:second] + new[second + len(old_target_block):]
        changes.append("full_table skips source chunk-index validation")
    elif "def _check_source_chunk_index" not in new:
        raise RuntimeError("check_indexes anchor not found")

    if QUERY_ANCHOR in new:
        new = new.replace(QUERY_ANCHOR, QUERY_REPLACEMENT, 1)
        changes.append("full_table uses EXPLAIN without range predicate")
    elif "Full-table EXPLAIN completed" not in new:
        raise RuntimeError("check_query_plan anchor not found")

    if SECOND_UNSAFE in new:
        new = new.replace(SECOND_UNSAFE, SECOND_SAFE, 1)
        changes.append("numeric_text post-plan validation depends on strategy only")

    if UNSAFE_CONDITION in new:
        new = new.replace(UNSAFE_CONDITION, SAFE_CONDITION)
        changes.append("removed remaining key-type strategy override")

    return new, changes


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
    try:
        new, changes = apply_patch(old)
    except RuntimeError as exc:
        raise SystemExit(f"Patch aborted: {exc}") from exc

    if new == old:
        print("Preflight full_table patch is already applied.")
        return 0

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
    backup_dir = args.project_root / "logs" / "3" / f"preflight_backup_{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / target.name
    shutil.copy2(target, backup)
    target.write_text(new, encoding="utf-8", newline="\n")

    print("Applied changes:")
    for change in changes:
        print(f"  - {change}")
    print(f"Backup: {backup}")
    print("Next commands:")
    print("  python -m compileall ax_to_postgres_etl")
    print("  python -m pytest tests/test_preflight_full_table_regression.py --import-mode=importlib")
    print("  python -m ax_to_postgres_etl.pipelines.dds_cli --mode preflight --stage purchase_order --batch-size 100000")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
