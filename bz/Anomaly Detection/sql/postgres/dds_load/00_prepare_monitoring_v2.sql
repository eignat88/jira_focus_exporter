-- ============================================================
-- 00v2: Create monitoring tables for DDS/Mart pipeline (Batch version)
-- Date: 2026-07-22
-- ============================================================

-- Pipeline run passport
CREATE TABLE IF NOT EXISTS etl.pipeline_run (
    run_id               BIGSERIAL PRIMARY KEY,
    pipeline_name        TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'PENDING',
    started_at           TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at         TIMESTAMPTZ,
    current_stage_no     INTEGER,
    current_stage_name   TEXT,
    total_stages         INTEGER,
    completed_stages     INTEGER DEFAULT 0,
    total_source_rows    BIGINT,
    total_processed_rows BIGINT DEFAULT 0,
    total_progress_pct   NUMERIC(7,3),
    estimated_finish_at  TIMESTAMPTZ,
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
    started_at          TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    elapsed_seconds     NUMERIC,
    rows_per_second     NUMERIC,
    eta_seconds         NUMERIC,
    estimated_finish_at TIMESTAMPTZ,
    error_message       TEXT,
    last_completed_batch INTEGER,
    last_completed_recid BIGINT,
    total_batches       INTEGER,
    completed_batches   INTEGER DEFAULT 0,
    failed_batches      INTEGER DEFAULT 0,
    batch_size          INTEGER,
    heartbeat_at        TIMESTAMPTZ,
    UNIQUE(run_id, stage_no)
);

-- Stage batch tracking
CREATE TABLE IF NOT EXISTS etl.stage_batch (
    batch_id             BIGSERIAL PRIMARY KEY,
    run_id               BIGINT NOT NULL,
    stage_no             INTEGER NOT NULL,
    stage_name           TEXT NOT NULL,
    source_table         TEXT NOT NULL,
    target_table         TEXT NOT NULL,
    batch_no             INTEGER NOT NULL,
    start_recid          BIGINT,
    end_recid            BIGINT,
    status               TEXT NOT NULL DEFAULT 'PENDING',
    attempt_no           INTEGER NOT NULL DEFAULT 1,
    rows_selected        BIGINT DEFAULT 0,
    rows_inserted        BIGINT DEFAULT 0,
    rows_conflicted      BIGINT DEFAULT 0,
    started_at           TIMESTAMPTZ,
    updated_at           TIMESTAMPTZ,
    completed_at         TIMESTAMPTZ,
    duration_seconds     NUMERIC,
    rows_per_second      NUMERIC,
    error_message        TEXT,
    CONSTRAINT uq_stage_batch UNIQUE (run_id, stage_no, batch_no)
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
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    status              TEXT,
    error_message       TEXT
);

CREATE INDEX IF NOT EXISTS idx_stage_progress_run ON etl.stage_progress(run_id, stage_no);
CREATE INDEX IF NOT EXISTS idx_stage_history_name ON etl.stage_history(pipeline_name, stage_name);
CREATE INDEX IF NOT EXISTS idx_stage_batch_run ON etl.stage_batch(run_id, stage_no, batch_no);
CREATE INDEX IF NOT EXISTS idx_stage_batch_status ON etl.stage_batch(status);
