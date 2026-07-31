from pathlib import Path

path = Path("diagnose_sales_order_preflight.py")
text = path.read_text(encoding="utf-8")

old_fetch_all = '''def fetch_all(cur: RealDictCursor, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cur.execute(query, params)
    return [dict(row) for row in cur.fetchall()]
'''

new_fetch_all = '''def fetch_all(cur: RealDictCursor, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    if params:
        cur.execute(query, params)
    else:
        cur.execute(query)
    return [dict(row) for row in cur.fetchall()]
'''

old_fetch_one = '''def fetch_one(cur: RealDictCursor, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    cur.execute(query, params)
    row = cur.fetchone()
    return dict(row) if row else None
'''

new_fetch_one = '''def fetch_one(cur: RealDictCursor, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    if params:
        cur.execute(query, params)
    else:
        cur.execute(query)
    row = cur.fetchone()
    return dict(row) if row else None
'''

if old_fetch_all not in text:
    raise RuntimeError("лок fetch_all не найден")

if old_fetch_one not in text:
    raise RuntimeError("лок fetch_one не найден")

text = text.replace(old_fetch_all, new_fetch_all, 1)
text = text.replace(old_fetch_one, new_fetch_one, 1)

path.write_text(text, encoding="utf-8")

print("справлен:", path.resolve())
print("fetch_all patched:", "if params:" in text)
