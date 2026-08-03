from ax_to_postgres_etl.pipelines.contracts import PipelineSpec
from ax_to_postgres_etl.pipelines.raw_to_dds import ColumnMap, RawToDdsAdapter


class RecordingCursor:
    def __init__(self):
        self.executions = []
        self.rowcount = 2

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=()):
        self.executions.append((sql, params))


class RecordingConnection:
    def __init__(self):
        self.cursor_instance = RecordingCursor()
        self.commits = 0

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1


def _spec(chunk_strategy="full_table"):
    return PipelineSpec(
        name="raw_to_dds",
        source_system="PostgreSQL RAW",
        source_schema="raw_ax",
        source_table="purchtable",
        target_schema="dds",
        target_table="purchase_order",
        key_column="recid",
        chunk_strategy=chunk_strategy,
    )


def _columns():
    return [
        ColumnMap("purchase_id", "NULLIF(btrim(src.purchid), '')"),
        ColumnMap("data_area_id", "NULLIF(btrim(src.dataareaid), '')"),
        ColumnMap("purchase_status", "NULLIF(btrim(src.purchstatus), '')"),
        ColumnMap(
            "modified_datetime",
            "NULLIF(btrim(src.modifieddatetime), '')::timestamp",
        ),
    ]


def test_full_table_omits_synthetic_chunk_predicate():
    connection = RecordingConnection()
    adapter = RawToDdsAdapter(
        columns=_columns(),
        conflict_column=["purchase_id", "data_area_id"],
        conflict_action="update",
        key_type="bigint_text",
    )

    result = adapter.execute_batch(connection, _spec(), 0, 1)

    sql, params = connection.cursor_instance.executions[0]
    assert "recid_bigint" not in sql
    assert "WHERE src." not in sql
    assert params == ()
    assert result.rows_inserted == 2
    assert connection.commits == 1


def test_composite_conflict_key_updates_only_non_key_attributes():
    adapter = RawToDdsAdapter(
        columns=_columns(),
        conflict_column=["purchase_id", "data_area_id"],
        conflict_action="update",
    )

    clause = adapter._build_conflict_clause()

    assert 'ON CONFLICT ("purchase_id", "data_area_id") DO UPDATE SET' in clause
    assert '"purchase_status" = EXCLUDED."purchase_status"' in clause
    assert '"modified_datetime" = EXCLUDED."modified_datetime"' in clause
    assert '"purchase_id" = EXCLUDED."purchase_id"' not in clause
    assert '"data_area_id" = EXCLUDED."data_area_id"' not in clause
    assert "IS DISTINCT FROM" in clause


def test_scalar_conflict_key_remains_backward_compatible():
    adapter = RawToDdsAdapter(
        columns=[ColumnMap("rec_id", "src.recid")],
        conflict_column="rec_id",
    )

    assert adapter.conflict_columns == ["rec_id"]
    assert adapter._build_conflict_clause() == (
        ' ON CONFLICT ("rec_id") DO NOTHING'
    )
