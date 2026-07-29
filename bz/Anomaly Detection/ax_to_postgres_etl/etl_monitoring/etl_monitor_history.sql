-- ============================================================
-- ETL MONITOR: ИСТОРИЯ И СНИМКИ
-- ============================================================

-- ============================================================
-- 15. ТАБЛИЦА СНИМКОВ (создание при первом запуске)
-- ============================================================
CREATE TABLE IF NOT EXISTS etl.load_monitor_snapshot (
    snapshot_id bigserial PRIMARY KEY,
    snapshot_at timestamp DEFAULT CURRENT_TIMESTAMP,
    run_id bigint NOT NULL,
    status text,
    chunk_count integer,
    rows_read bigint,
    rows_inserted bigint,
    rows_conflicted bigint,
    completed_chunks integer,
    running_chunks integer,
    retry_chunks integer,
    failed_chunks integer,
    pending_chunks integer,
    ready_to_commit_chunks integer,
    writing_chunks integer
);

CREATE INDEX IF NOT EXISTS ix_snapshot_run_time
    ON etl.load_monitor_snapshot (run_id, snapshot_at);

-- ============================================================
-- 15b. СОЗДАНИЕ СНИМКА
-- ============================================================
-- Выполнять при необходимости (раз в 1-5 минут)
WITH params AS (
    SELECT
        COALESCE(
            NULLIF(19::bigint, 0),
            (SELECT run_id FROM etl.load_run
             WHERE source_table = 'ALK_MARKSERIAL'
               AND status IN ('running', 'completed_with_errors')
             ORDER BY started_at DESC LIMIT 1)
        ) AS run_id
),
snapshot_data AS (
    SELECT
        (SELECT run_id FROM params) AS run_id,
        (SELECT status FROM etl.load_run WHERE run_id = (SELECT run_id FROM params)) AS status,
        (SELECT total_chunks FROM etl.load_run WHERE run_id = (SELECT run_id FROM params)) AS chunk_count,
        (SELECT SUM(rows_read) FROM etl.load_chunk WHERE run_id = (SELECT run_id FROM params)) AS rows_read,
        (SELECT SUM(rows_inserted) FROM etl.load_chunk WHERE run_id = (SELECT run_id FROM params)) AS rows_inserted,
        (SELECT SUM(rows_conflicted) FROM etl.load_chunk WHERE run_id = (SELECT run_id FROM params)) AS rows_conflicted,
        (SELECT COUNT(*) FROM etl.load_chunk WHERE run_id = (SELECT run_id FROM params) AND status = 'completed') AS completed_chunks,
        (SELECT COUNT(*) FROM etl.load_chunk WHERE run_id = (SELECT run_id FROM params) AND status = 'running') AS running_chunks,
        (SELECT COUNT(*) FROM etl.load_chunk WHERE run_id = (SELECT run_id FROM params) AND status = 'retry') AS retry_chunks,
        (SELECT COUNT(*) FROM etl.load_chunk WHERE run_id = (SELECT run_id FROM params) AND status = 'failed') AS failed_chunks,
        (SELECT COUNT(*) FROM etl.load_chunk WHERE run_id = (SELECT run_id FROM params) AND status = 'pending') AS pending_chunks,
        (SELECT COUNT(*) FROM etl.load_chunk WHERE run_id = (SELECT run_id FROM params) AND status = 'ready_to_commit') AS ready_to_commit_chunks,
        (SELECT COUNT(*) FROM etl.load_chunk WHERE run_id = (SELECT run_id FROM params) AND status = 'writing') AS writing_chunks
)
INSERT INTO etl.load_monitor_snapshot (
    run_id, status, chunk_count, rows_read, rows_inserted, rows_conflicted,
    completed_chunks, running_chunks, retry_chunks, failed_chunks,
    pending_chunks, ready_to_commit_chunks, writing_chunks
)
SELECT * FROM snapshot_data;

-- ============================================================
-- 15c. ДИНАМИКА МЕЖДУ ПРОВЕРКАМИ
-- ============================================================
WITH params AS (
    SELECT
        COALESCE(
            NULLIF(19::bigint, 0),
            (SELECT run_id FROM etl.load_run
             WHERE source_table = 'ALK_MARKSERIAL'
               AND status IN ('running', 'completed_with_errors')
             ORDER BY started_at DESC LIMIT 1)
        ) AS run_id
),
recent_snapshots AS (
    SELECT
        s.snapshot_at,
        s.completed_chunks,
        s.rows_inserted,
        s.running_chunks,
        s.retry_chunks,
        LAG(s.rows_inserted) OVER (ORDER BY s.snapshot_at) AS prev_rows_inserted,
        LAG(s.completed_chunks) OVER (ORDER BY s.snapshot_at) AS prev_completed,
        LAG(s.snapshot_at) OVER (ORDER BY s.snapshot_at) AS prev_snapshot_at
    FROM etl.load_monitor_snapshot s, params p
    WHERE s.run_id = p.run_id
      AND s.snapshot_at > NOW() - INTERVAL '1 hour'
    ORDER BY s.snapshot_at
)
SELECT
    snapshot_at,
    completed_chunks,
    rows_inserted,
    running_chunks,
    retry_chunks,
    COALESCE(rows_inserted - prev_rows_inserted, 0) AS rows_delta,
    COALESCE(completed_chunks - prev_completed, 0) AS chunks_delta,
    CASE
        WHEN prev_snapshot_at IS NOT NULL AND snapshot_at - prev_snapshot_at > INTERVAL '0'
        THEN ROUND(COALESCE(rows_inserted - prev_rows_inserted, 0) /
                    EXTRACT(EPOCH FROM (snapshot_at - prev_snapshot_at)))
        ELSE 0
    END AS rows_per_sec
FROM recent_snapshots
ORDER BY snapshot_at;

-- ============================================================
-- 16. ИТОГОВОЕ ЗАКЛЮЧЕНИЕ
-- ============================================================
WITH params AS (
    SELECT
        COALESCE(
            NULLIF(19::bigint, 0),
            (SELECT run_id FROM etl.load_run
             WHERE source_table = 'ALK_MARKSERIAL'
               AND status IN ('running', 'completed_with_errors')
             ORDER BY started_at DESC LIMIT 1)
        ) AS run_id
),
run_status AS (
    SELECT
        r.status,
        r.total_chunks,
        r.completed_chunks,
        (SELECT COUNT(*) FROM etl.load_chunk WHERE run_id = (SELECT run_id FROM params) AND status = 'failed') AS failed,
        (SELECT COUNT(*) FROM etl.load_chunk WHERE run_id = (SELECT run_id FROM params) AND status = 'running') AS running,
        (SELECT COUNT(*) FROM etl.load_chunk WHERE run_id = (SELECT run_id FROM params)
         AND status = 'running' AND heartbeat_at < NOW() - INTERVAL '10 minutes') AS stale_running,
        (SELECT COUNT(*) FROM etl.load_chunk WHERE run_id = (SELECT run_id FROM params) AND status = 'ready_to_commit') AS ready_stuck,
        (SELECT COUNT(*) FROM etl.load_chunk WHERE run_id = (SELECT run_id FROM params) AND status = 'writing') AS writing
    FROM etl.load_run r, params p
    WHERE r.run_id = p.run_id
)
SELECT
    CASE
        WHEN completed_chunks = total_chunks THEN 'COMPLETED'
        WHEN failed > 0 OR stale_running > 0 THEN 'CRITICAL'
        WHEN ready_stuck > 0 OR writing > 0 OR (SELECT COUNT(*) FROM etl.load_chunk
             WHERE run_id = (SELECT run_id FROM params) AND status = 'retry') > 5 THEN 'WARNING'
        ELSE 'HEALTHY'
    END AS overall_status,
    CASE
        WHEN completed_chunks = total_chunks THEN 'Все чанки завершены.'
        WHEN failed > 0 THEN 'Есть ' || failed || ' failed чанков. Проверьте ошибки.'
        WHEN stale_running > 0 THEN 'Есть ' || stale_running || ' stale running. Heartbeat истёк.'
        WHEN ready_stuck > 0 THEN 'Writer не обрабатывает ready_to_commit.'
        WHEN writing > 0 THEN 'Writer выполняет запись.'
        ELSE 'Загрузка выполняется, heartbeat актуален.'
    END AS diagnosis,
    CASE
        WHEN completed_chunks = total_chunks THEN 'Нет действий.'
        WHEN failed > 0 THEN 'Проверить ошибки в блоке 07.'
        WHEN stale_running > 0 THEN 'Выполнить recovery или проверить SQL Server.'
        WHEN ready_stuck > 0 THEN 'Проверить writer и staging.'
        ELSE 'Продолжить мониторинг.'
    END AS recommended_action
FROM run_status;
