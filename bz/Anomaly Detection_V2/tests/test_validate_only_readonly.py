from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DDS_CLI = PROJECT_ROOT / "ax_to_postgres_etl" / "pipelines" / "dds_cli.py"
RUNNER = PROJECT_ROOT / "ax_to_postgres_etl" / "pipelines" / "runner.py"


def test_validate_only_has_dedicated_readonly_path():
    source = DDS_CLI.read_text(encoding="utf-8-sig")

    assert "def run_validate_only" in source
    assert "conn.set_session(readonly=True, autocommit=True)" in source
    assert 'if args.mode == "validate-only"' in source
    assert "return run_validate_only(stages, pipeline_config, dsn)" in source


def test_runner_persists_configured_chunk_strategy():
    source = RUNNER.read_text(encoding="utf-8-sig")

    assert "chunk_strategy=spec.chunk_strategy" in source
    assert "run.run_id, spec.chunk_strategy, spec.key_column, ranges" in source
