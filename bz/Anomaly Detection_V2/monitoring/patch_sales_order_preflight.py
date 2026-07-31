from pathlib import Path

path = Path("diagnose_sales_order_preflight.py")
text = path.read_text(encoding="utf-8")

# 1. бираем SyntaxWarning от Windows-пути в module docstring.
# айл начинается с shebang и coding, поэтому меняем первое вхождение docstring.
marker = '"""\nRead-only diagnostic preflight'
replacement = 'r"""\nRead-only diagnostic preflight'

if marker in text:
    text = text.replace(marker, replacement, 1)

# 2. справляем pg_get_indexdef: ordinality bigint -> integer.
text = text.replace(
    "pg_get_indexdef(i.indexrelid, ord.ordinality, true)",
    "pg_get_indexdef(i.indexrelid, ord.ordinality::int, true)"
)

# 3. RealDictCursor возвращает dict, а не tuple.
old_block = """                    cur.execute(explain_query, (low, high))
                    lines = [row[0] for row in cur.fetchall()]
                    plan_rows = [{"line_no": i + 1, "plan_line": line} for i, line in enumerate(lines)]
"""

new_block = """                    cur.execute(explain_query, (low, high))
                    explain_rows = cur.fetchall()
                    lines = [
                        str(next(iter(row.values())))
                        for row in explain_rows
                    ]
                    plan_rows = [
                        {"line_no": i + 1, "plan_line": line}
                        for i, line in enumerate(lines)
                    ]
"""

if old_block in text:
    text = text.replace(old_block, new_block, 1)

path.write_text(text, encoding="utf-8")

print(f"справлен файл: {path.resolve()}")
print(f"сталось row[0]: {'row[0]' in text}")
print(f"сть ordinality::int: {'ordinality::int' in text}")
print(f"сть raw docstring: {'r\"\"\"' in text[:500]}")
