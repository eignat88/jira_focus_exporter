from __future__ import annotations

from datetime import datetime
from pathlib import Path
import py_compile
import re
import shutil

ROOT = Path(r"D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2")
TS = datetime.now().strftime("%Y%m%d_%H%M%S")
BACKUP_DIR = ROOT / "logs" / "3" / f"sales_order_patch_backup_{TS}"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

YAML_MAIN = ROOT / "config" / "raw_to_dds.yaml"
YAML_ALT = ROOT / "config" / "config" / "raw_to_dds.yaml"
PREFLIGHT = ROOT / "ax_to_postgres_etl" / "pipelines" / "preflight.py"
ADAPTER = ROOT / "ax_to_postgres_etl" / "pipelines" / "raw_to_dds.py"

for path in (YAML_MAIN, YAML_ALT, PREFLIGHT, ADAPTER):
    if not path.exists():
        raise FileNotFoundError(path)
    shutil.copy2(path, BACKUP_DIR / path.name)

MAIN_STAGE = """- false: 5
  name: sales_order
  enabled: true
  source:
    schema: raw_ax
    table: salestable
    key_column: recid
    key_type: bigint_text_expression
  target:
    schema: dds
    table: sales_order
    conflict_key: source_recid
  execution:
    strategy: postgres_insert_select
    chunk_strategy: numeric_range
    batch_size: 250000
    count_mode: estimate
  columns:
  - target: source_recid
    expression: btrim(src.recid)::bigint
  - target: sales_id
    expression: NULLIF(btrim(src.salesid), '')
  - target: customer_account
    expression: NULLIF(btrim(src.custaccount), '')
  - target: invoice_date
    expression: NULL::timestamp without time zone
  - target: delivery_date
    expression: "CASE WHEN NULLIF(btrim(src.deliverydate), '') IS NULL OR btrim(src.deliverydate) IN ('1900-01-01', '1900-01-01 00:00:00') THEN NULL ELSE btrim(src.deliverydate)::timestamp END"
  - target: currency_code
    expression: NULLIF(btrim(src.currencycode), '')
  - target: sales_status
    expression: NULLIF(btrim(src.salesstatus), '')
  - target: modified_datetime
    expression: "CASE WHEN NULLIF(btrim(src.modifieddatetime), '') IS NULL THEN NULL ELSE btrim(src.modifieddatetime)::timestamp END"
  - target: created_datetime
    expression: "CASE WHEN NULLIF(btrim(src.createddatetime), '') IS NULL THEN NULL ELSE btrim(src.createddatetime)::timestamp END"
  - target: data_area_id
    expression: NULLIF(btrim(src.dataareaid), '')
  validation:
    target_not_empty: true
    preflight_required: true
    require_index_condition: true
    expected_source_index: idx_salestable_recid_bigint
  post_actions:
    analyze: true
"""

ALT_STAGE = """  - no: 5
    name: sales_order
    enabled: true

    source:
      schema: raw_ax
      table: salestable
      key_column: recid
      key_type: bigint_text_expression

    target:
      schema: dds
      table: sales_order
      conflict_key: source_recid

    execution:
      strategy: postgres_insert_select
      chunk_strategy: numeric_range
      batch_size: 250000
      count_mode: estimate

    columns:
      - target: source_recid
        expression: "btrim(src.recid)::bigint"
      - target: sales_id
        expression: "NULLIF(btrim(src.salesid), '')"
      - target: customer_account
        expression: "NULLIF(btrim(src.custaccount), '')"
      - target: invoice_date
        expression: "NULL::timestamp without time zone"
      - target: delivery_date
        expression: "CASE WHEN NULLIF(btrim(src.deliverydate), '') IS NULL OR btrim(src.deliverydate) IN ('1900-01-01', '1900-01-01 00:00:00') THEN NULL ELSE btrim(src.deliverydate)::timestamp END"
      - target: currency_code
        expression: "NULLIF(btrim(src.currencycode), '')"
      - target: sales_status
        expression: "NULLIF(btrim(src.salesstatus), '')"
      - target: modified_datetime
        expression: "CASE WHEN NULLIF(btrim(src.modifieddatetime), '') IS NULL THEN NULL ELSE btrim(src.modifieddatetime)::timestamp END"
      - target: created_datetime
        expression: "CASE WHEN NULLIF(btrim(src.createddatetime), '') IS NULL THEN NULL ELSE btrim(src.createddatetime)::timestamp END"
      - target: data_area_id
        expression: "NULLIF(btrim(src.dataareaid), '')"

    validation:
      target_not_empty: true
      preflight_required: true
      require_index_condition: true
      expected_source_index: idx_salestable_recid_bigint

    post_actions:
      analyze: true

"""

def replace_once(text: str, pattern: str, replacement: str, label: str) -> str:
    result, count = re.subn(pattern, replacement, text, count=1, flags=re.M | re.S)
    if count != 1:
        raise RuntimeError(f"{label}: replacements={count}")
    return result

text = YAML_MAIN.read_text(encoding="utf-8-sig")
text = replace_once(
    text,
    r"^- false: 5\r?\n  name: sales_order\r?\n.*?(?=^- false: 6\r?\n  name: purchase_order\r?\n)",
    MAIN_STAGE,
    "main YAML",
)
YAML_MAIN.write_text(text, encoding="utf-8")

text = YAML_ALT.read_text(encoding="utf-8-sig")
text = replace_once(
    text,
    r"^  - no: 5\r?\n    name: sales_order\r?\n.*?(?=^  - no: 6\r?\n    name: purchase_order\r?\n)",
    ALT_STAGE,
    "alternate YAML",
)
YAML_ALT.write_text(text, encoding="utf-8")

text = ADAPTER.read_text(encoding="utf-8")

anchor = """    "numeric_text": {
        # Numeric values are stored as text with equal width.
"""
handler = """    "bigint_text_expression": {
        # AX RECID is stored as text; use the exact functional-index expression.
        "value_expr": "(btrim({key}))::bigint",
        "min_expr": "MIN((btrim({key}))::bigint)",
        "max_expr": "MAX((btrim({key}))::bigint)",
        "filter_expr": "(btrim(src.{key}))::bigint > %(start_key)s AND (btrim(src.{key}))::bigint <= %(end_key)s",
        "param_type": "bigint",
    },
"""
if handler not in text:
    if anchor not in text:
        raise RuntimeError("KEY_TYPE_HANDLERS anchor not found")
    text = text.replace(anchor, handler + anchor, 1)

old = """        key_column = self._get_key_column(spec)
        key_expr = ident(key_column)
        min_sql = self._key_handler["min_expr"].format(key=key_expr)
        max_sql = self._key_handler["max_expr"].format(key=key_expr)

        sql = f\"\"\"
            SELECT {min_sql}, {max_sql}
            FROM {ident(spec.source_schema)}.{ident(spec.source_table)}
            WHERE {key_expr} IS NOT NULL
        \"\"\"
"""
new = """        key_column = self._get_key_column(spec)
        key_expr = ident(key_column)
        value_expr = self._key_handler.get("value_expr", "{key}").format(key=key_expr)
        min_sql = self._key_handler["min_expr"].format(key=key_expr)
        max_sql = self._key_handler["max_expr"].format(key=key_expr)

        sql = f\"\"\"
            SELECT {min_sql}, {max_sql}
            FROM {ident(spec.source_schema)}.{ident(spec.source_table)}
            WHERE {value_expr} IS NOT NULL
        \"\"\"
"""
if old not in text:
    raise RuntimeError("get_boundaries block not found")
text = text.replace(old, new, 1)

old = """        key_column = self._get_key_column(spec)
        filter_key = f"src.{ident(key_column)}"
        where_clause = f"{filter_key} > %s AND {filter_key} <= %s"
"""
new = """        key_column = self._get_key_column(spec)
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
"""
if old not in text:
    raise RuntimeError("execute_batch filter block not found")
text = text.replace(old, new, 1)
ADAPTER.write_text(text, encoding="utf-8")

text = PREFLIGHT.read_text(encoding="utf-8")

old = """        if key_type:
            compatible, message = self._check_chunk_key_compatibility(
                key_type,
                chunk_strategy,
            )
"""
new = """        if key_type:
            if (
                self._key_type == "bigint_text_expression"
                and chunk_strategy == "numeric_range"
                and key_type in {"text", "varchar", "character varying"}
            ):
                compatible, message = (
                    True,
                    "Source RECID is text; numeric_range uses "
                    "btrim(recid)::bigint",
                )
            else:
                compatible, message = self._check_chunk_key_compatibility(
                    key_type,
                    chunk_strategy,
                )
"""
if old not in text:
    raise RuntimeError("preflight compatibility block not found")
text = text.replace(old, new, 1)

old = """        # Check B-tree index on source for chunk key
        key_col = self._key_column
        if self._key_type == "bigint_text":
            key_col = "recid_bigint"

        idx = _find_btree_index(cur, self._source_schema, self._source_table, key_col)
"""
new = """        # Check B-tree index on source for chunk key
        key_col = self._key_column
        if self._key_type == "bigint_text":
            key_col = "recid_bigint"

        if self._key_type == "bigint_text_expression":
            cur.execute(
                \"\"\"
                SELECT
                    idx.relname,
                    pg_get_indexdef(idx.oid),
                    i.indisvalid,
                    i.indisready
                FROM pg_index i
                JOIN pg_class tbl ON tbl.oid = i.indrelid
                JOIN pg_namespace ns ON ns.oid = tbl.relnamespace
                JOIN pg_class idx ON idx.oid = i.indexrelid
                JOIN pg_am am ON am.oid = idx.relam
                WHERE ns.nspname = %s
                  AND tbl.relname = %s
                  AND am.amname = 'btree'
                  AND lower(pg_get_expr(i.indexprs, i.indrelid))
                      LIKE '%%btrim(recid)%%bigint%%'
                ORDER BY i.indisvalid DESC, i.indisready DESC
                LIMIT 1
                \"\"\",
                (self._source_schema, self._source_table),
            )
            row = cur.fetchone()
            idx = (
                {
                    "name": row[0],
                    "definition": row[1],
                    "is_valid": row[2],
                    "is_ready": row[3],
                    "usable_for_chunking": bool(row[2] and row[3]),
                }
                if row
                else None
            )
            key_col = "btrim(recid)::bigint"
        else:
            idx = _find_btree_index(
                cur,
                self._source_schema,
                self._source_table,
                key_col,
            )
"""
if old not in text:
    raise RuntimeError("preflight index block not found")
text = text.replace(old, new, 1)

old = """        cur = self.conn.cursor()
        key_col = self._key_column
        if self._key_type == "bigint_text":
            key_col = "recid_bigint"

        chunk_strategy = (
"""
new = """        cur = self.conn.cursor()
        key_col = self._key_column
        if self._key_type == "bigint_text":
            key_col = "recid_bigint"
        elif self._key_type == "bigint_text_expression":
            key_col = f"(btrim({self._key_column}))::bigint"

        chunk_strategy = (
"""
if old not in text:
    raise RuntimeError("preflight plan key block not found")
text = text.replace(old, new, 1)

PREFLIGHT.write_text(text, encoding="utf-8")

py_compile.compile(str(PREFLIGHT), doraise=True)
py_compile.compile(str(ADAPTER), doraise=True)

print("PATCH COMPLETE")
print(f"Backups: {BACKUP_DIR}")
print("Next:")
print(
    r"python -m ax_to_postgres_etl.pipelines.dds_cli "
    r"--mode preflight --stage sales_order --batch-size 250000"
)
