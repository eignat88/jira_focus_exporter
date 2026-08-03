from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = (
    PROJECT_ROOT
    / "ax_to_postgres_etl"
    / "pipelines"
    / "preflight.py"
)


def _source() -> str:
    return PREFLIGHT.read_text(encoding="utf-8-sig")


def test_full_table_does_not_fall_back_to_numeric_text_strategy():
    source = _source()
    unsafe = (
        'chunk_strategy == "numeric_text_range" '
        'or self._key_type == "numeric_text"'
    )
    assert unsafe not in source


def test_preflight_has_explicit_full_table_index_handling():
    source = _source()
    assert 'chunk_strategy == "full_table"' in source
    assert "B-tree chunk index is not required" in source


def test_preflight_explains_all_full_table_mapping_expressions():
    source = _source()
    assert "Full mapped SELECT passed EXPLAIN without ANALYZE" in source
    assert 'column.get("expression", "")' in source
    assert "SELECT {mapped_expressions}" in source


def test_missing_mapped_source_columns_are_blocking_and_deduplicated():
    source = _source()
    assert "checked_source_columns: set[str] = set()" in source
    assert 'self._error(' in source
    assert 'f"source_column:{source_column}"' in source
