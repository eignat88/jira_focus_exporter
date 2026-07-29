-- ============================================================
-- 00: Create monitoring tables for DDS/Mart pipeline
-- Date: 2026-07-22
-- ============================================================

-- Pipeline run passport
CREATE TABLE IF NOT EXISTS etl.pipeline_run (
    run_id               BIGSERIAL PRIMARY KEY,
    pipeline_name        TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'PENDING',
    started_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at         TIMESTAMP,
    current_stage_no     INTEGER,
    current_stage_name   TEXT,
    total_stages         INTEGER,
    completed_stages     INTEGER DEFAULT 0,
    total_source_rows    BIGINT,
    total_processed_rows BIGINT DEFAULT 0,
    total_progress_pct   NUMERIC(7,3),
    estimated_finish_at  TIMESTAMP,
    error_message        TEXT
);

-- Stage progress (real-time)
CREATE TABLE IF NOT EXISTS etl.stage_progress (
    progress_id         BIGSERIAL PRIMARY KEY,
    run_id              BIGINT NOT NULL REFERENCES etl.pipeline_run(run_id),
    pipeline_name       TEXT NOT NULL,
    stage_no            INTEGER NOT NULL,
    stage_name          TEXT NOT NULL,
    source_table        TEXT,
    target_table        TEXT,
    status              TEXT NOT NULL DEFAULT 'PENDING',
    source_rows         BIGINT,
    processed_rows      BIGINT DEFAULT 0,
    remaining_rows      BIGINT,
    progress_pct        NUMERIC(7,3),
    started_at          TIMESTAMP,
    updated_at          TIMESTAMP,
    completed_at        TIMESTAMP,
    elapsed_seconds     NUMERIC,
    rows_per_second     NUMERIC,
    eta_seconds         NUMERIC,
    estimated_finish_at TIMESTAMP,
    error_message       TEXT,
    UNIQUE(run_id, stage_no)
);

-- Stage history (for future ETA predictions)
CREATE TABLE IF NOT EXISTS etl.stage_history (
    history_id          BIGSERIAL PRIMARY KEY,
    run_id              BIGINT NOT NULL,
    pipeline_name       TEXT NOT NULL,
    stage_name          TEXT NOT NULL,
    source_table        TEXT,
    target_table        TEXT,
    source_rows         BIGINT,
    processed_rows      BIGINT,
    duration_seconds    NUMERIC,
    avg_rows_per_second NUMERIC,
    started_at          TIMESTAMP,
    completed_at        TIMESTAMP,
    status              TEXT,
    error_message       TEXT
);

CREATE INDEX IF NOT EXISTS idx_stage_progress_run ON etl.stage_progress(run_id, stage_no);
CREATE INDEX IF NOT EXISTS idx_stage_history_name ON etl.stage_history(pipeline_name, stage_name);
