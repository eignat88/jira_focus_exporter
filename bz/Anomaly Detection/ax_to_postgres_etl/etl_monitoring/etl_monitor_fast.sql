-- ============================================================
-- ETL MONITOR: БЫСТРЫЙ МОНИТОРИНГ
-- Запуск каждые 30-60 секунд
-- Работает ТОЛЬКО с etl.load_run + etl.load_chunk (быстро)
-- ============================================================

-- ============================================================
-- 00. ПАРАМЕТРЫ
-- ============================================================
-- Измените run_id на нужный или используйте авто-определение
WITH params AS (
    SELECT
        COALESCE(
            NULLIF(19::bigint, 0),  -- ручной run_id (измените здесь)
            (SELECT run_id FROM etl.load_run
             WHERE source_table = 'ALK_MARKSERIAL'
               AND status IN ('running', 'completed_with_errors')
             ORDER BY started_at DESC LIMIT 1)
        ) AS run_id
),
target AS (
    SELECT 'raw_ax'::text AS schema_name, 'alk_markserial'::text AS table_name
),
hb AS (
    SELECT 30::int AS interval_sec, 600::int AS timeout_sec
),

-- ============================================================
-- 01. ПАСПОРТ ЗАПУСКА
-- ============================================================
run_info AS (
    SELECT
        r.run_id,
        r.source_table,
        r.target_schema || '.' || r.target_table AS target_table,
        r.status AS run_status,
        r.load_mode,
        r.started_at,
        r.finished_at,
        r.created_at,
        r.chunk_strategy,
        r.chunk_column,
        r.total_chunks,
        r.completed_chunks,
        r.rows_read AS run_rows_read,
        r.rows_inserted AS run_rows_inserted,
        r.error_message
    FROM etl.load_run r, params p
    WHERE r.run_id = p.run_id
),

-- ============================================================
-- 02. СВОДКА ПО СТАТУСАМ
-- ============================================================
status_summary AS (
    SELECT
        c.status,
        COUNT(*) AS chunk_count,
        COALESCE(SUM(c.rows_read), 0) AS rows_read,
        COALESCE(SUM(c.rows_staged), 0) AS rows_staged,
        COALESCE(SUM(c.rows_inserted), 0) AS rows_inserted,
        COALESCE(SUM(c.rows_conflicted), 0) AS rows_conflicted,
        ROUND(COUNT(*) * 100.0 / NULLIF((SELECT total_chunks FROM run_info), 0), 1) AS pct
    FROM etl.load_chunk c, params p
    WHERE c.run_id = p.run_id
    GROUP BY c.status
),
ordered_status AS (
    SELECT * FROM status_summary WHERE status = 'failed' UNION ALL
    SELECT * FROM status_summary WHERE status = 'writing' UNION ALL
    SELECT * FROM status_summary WHERE status = 'ready_to_commit' UNION ALL
    SELECT * FROM status_summary WHERE status = 'running' UNION ALL
    SELECT * FROM status_summary WHERE status = 'retry' UNION ALL
    SELECT * FROM status_summary WHERE status = 'pending' UNION ALL
    SELECT * FROM status_summary WHERE status = 'completed' UNION ALL
    SELECT * FROM status_summary WHERE status = 'cancelled'
),

-- ============================================================
-- 03. ГЛАВНАЯ ПАНЕЛЬ ПРОГРЕССА
-- ============================================================
progress AS (
    SELECT
        ri.total_chunks,
        COALESCE(ss_completed.chunk_count, 0) AS completed_chunks,
        COALESCE(ss_active.chunk_count, 0) AS active_chunks,
        COALESCE(ss_retry.chunk_count, 0) AS retry_chunks,
        COALESCE(ss_pending.chunk_count, 0) AS pending_chunks,
        COALESCE(ss_failed.chunk_count, 0) AS failed_chunks,
        COALESCE(ss_completed.rows_inserted, 0) AS rows_completed,
        ri.run_rows_read AS estimated_total_rows,
        ROUND(COALESCE(ss_completed.chunk_count, 0) * 100.0 / NULLIF(ri.total_chunks, 0), 2) AS progress_percent,
        CASE
            WHEN EXTRACT(EPOCH FROM (COALESCE(ri.finished_at, NOW()) - ri.started_at)) > 0
            THEN ROUND(COALESCE(ss_completed.rows_inserted, 0) / EXTRACT(EPOCH FROM (COALESCE(ri.finished_at, NOW()) - ri.started_at)))
            ELSE 0
        END AS rows_per_second,
        CASE
            WHEN COALESCE(ss_completed.rows_inserted, 0) > 0 AND EXTRACT(EPOCH FROM (NOW() - ri.started_at)) > 0
            THEN TO_CHAR(
                (EXTRACT(EPOCH FROM (ri.run_rows_read - ss_completed.rows_inserted))
                 / (ss_completed.rows_inserted / EXTRACT(EPOCH FROM (NOW() - ri.started_at))))
                 * INTERVAL '1 second',
                'HH24:MI:SS')
            ELSE 'N/A'
        END AS estimated_remaining
    FROM run_info ri
    LEFT JOIN status_summary ss_completed ON ss_completed.status = 'completed'
    LEFT JOIN (
        SELECT 'running' AS status, COUNT(*) AS chunk_count FROM etl.load_chunk c, params p
        WHERE c.run_id = p.run_id AND c.status IN ('running','ready_to_commit','writing')
    ) ss_active ON ss_active.status = 'running'
    LEFT JOIN status_summary ss_retry ON ss_retry.status = 'retry'
    LEFT JOIN status_summary ss_pending ON ss_pending.status = 'pending'
    LEFT JOIN status_summary ss_failed ON ss_failed.status = 'failed'
),

-- ============================================================
-- 04. АКТИВНЫЕ WORKER
-- ============================================================
active_workers AS (
    SELECT
        c.chunk_no,
        c.status,
        c.worker_id,
        c.attempt_count,
        c.range_start_bigint,
        c.range_end_bigint,
        c.rows_read,
        c.last_processed_key,
        c.started_at,
        c.heartbeat_at,
        c.error_type,
        c.error_message,
        TO_CHAR(COALESCE(NOW() - c.started_at, INTERVAL '0'), 'HH24:MI:SS') AS chunk_elapsed,
        TO_CHAR(COALESCE(NOW() - c.heartbeat_at, INTERVAL '0'), 'HH24:MI:SS') AS heartbeat_age,
        CASE
            WHEN c.rows_read > 0 AND EXTRACT(EPOCH FROM (NOW() - c.started_at)) > 0
            THEN ROUND(c.rows_read / EXTRACT(EPOCH FROM (NOW() - c.started_at)))
            ELSE 0
        END AS rows_per_sec,
        CASE
            WHEN c.rows_read > 0 AND c.range_end_bigint > c.range_start_bigint
            THEN ROUND(c.rows_read * 100.0 / (c.range_end_bigint - c.range_start_bigint), 1)
            ELSE 0
        END AS progress_pct
    FROM etl.load_chunk c, params p
    WHERE c.run_id = p.run_id
      AND c.status IN ('running', 'ready_to_commit', 'writing')
    ORDER BY c.chunk_no
),

-- ============================================================
-- 05. КОНТРОЛЬ HEARTBEAT
-- ============================================================
heartbeat_check AS (
    SELECT
        c.chunk_no,
        c.status,
        c.worker_id,
        c.started_at,
        c.heartbeat_at,
        TO_CHAR(COALESCE(NOW() - c.heartbeat_at, INTERVAL '0'), 'HH24:MI:SS') AS heartbeat_age,
        CASE
            WHEN c.heartbeat_at IS NULL THEN 'NO_HEARTBEAT'
            WHEN NOW() - c.heartbeat_at > (hb.timeout_sec || ' seconds')::interval THEN 'STALE'
            WHEN NOW() - c.heartbeat_at > (hb.interval_sec * 2 || ' seconds')::interval THEN 'WARNING'
            ELSE 'OK'
        END AS health_status
    FROM etl.load_chunk c, params p, hb
    WHERE c.run_id = p.run_id
      AND c.status IN ('running', 'ready_to_commit', 'writing')
    ORDER BY
        CASE
            WHEN c.heartbeat_at IS NULL THEN 0
            WHEN NOW() - c.heartbeat_at > (hb.timeout_sec || ' seconds')::interval THEN 1
            WHEN NOW() - c.heartbeat_at > (hb.interval_sec * 2 || ' seconds')::interval THEN 2
            ELSE 3
        END,
        c.chunk_no
),

-- ============================================================
-- 06. ОЧЕРЕДЬ RETRY/PENDING
-- ============================================================
queue_summary AS (
    SELECT
        c.chunk_no,
        c.status,
        c.attempt_count,
        c.worker_id AS prev_worker,
        c.rows_read AS prev_rows_read,
        c.last_processed_key,
        c.error_type,
        c.error_message,
        c.updated_at,
        TO_CHAR(COALESCE(NOW() - c.updated_at, INTERVAL '0'), 'HH24:MI:SS') AS queue_wait
    FROM etl.load_chunk c, params p
    WHERE c.run_id = p.run_id
      AND c.status IN ('retry', 'pending')
    ORDER BY
        CASE c.status WHEN 'retry' THEN 0 ELSE 1 END,
        c.chunk_no
),

-- ============================================================
-- 07. ОШИБКИ
-- ============================================================
errors AS (
    SELECT
        CASE
            WHEN c.status = 'failed' THEN 'FAILED'
            WHEN c.status = 'running' AND c.heartbeat_at < NOW() - (hb.timeout_sec || ' seconds')::interval THEN 'STALE_RUNNING'
            WHEN c.status = 'ready_to_commit' AND c.updated_at < NOW() - INTERVAL '10 minutes' THEN 'READY_STUCK'
            WHEN c.status = 'writing' AND c.updated_at < NOW() - INTERVAL '10 minutes' THEN 'WRITING_STUCK'
            WHEN c.error_type IS NOT NULL THEN 'HAS_ERROR'
            WHEN c.attempt_count >= 5 THEN 'MAX_ATTEMPTS'
            ELSE NULL
        END AS severity,
        c.chunk_no,
        c.status,
        c.attempt_count,
        c.worker_id,
        TO_CHAR(COALESCE(NOW() - c.heartbeat_at, INTERVAL '0'), 'HH24:MI:SS') AS heartbeat_age,
        TO_CHAR(COALESCE(NOW() - c.started_at, INTERVAL '0'), 'HH24:MI:SS') AS state_age,
        c.error_type,
        CASE
            WHEN c.error_type = 'heartbeat_timeout' THEN 'Перевести stale running в retry'
            WHEN c.status = 'failed' THEN 'Проверить ошибку и сбросить после анализа'
            WHEN c.status = 'ready_to_commit' AND c.updated_at < NOW() - INTERVAL '10 minutes' THEN 'Проверить writer'
            WHEN c.status = 'writing' AND c.updated_at < NOW() - INTERVAL '10 minutes' THEN 'Проверить staging'
            WHEN c.attempt_count >= 5 THEN 'Сбросить attempt_count после анализа причины'
            ELSE 'Проверить соединение с SQL Server'
        END AS recommended_action
    FROM etl.load_chunk c, params p, hb
    WHERE c.run_id = p.run_id
      AND (
          c.status = 'failed'
          OR (c.status = 'running' AND c.heartbeat_at < NOW() - (hb.timeout_sec || ' seconds')::interval)
          OR (c.status = 'ready_to_commit' AND c.updated_at < NOW() - INTERVAL '10 minutes')
          OR (c.status = 'writing' AND c.updated_at < NOW() - INTERVAL '10 minutes')
          OR c.error_type IS NOT NULL
          OR c.attempt_count >= 5
      )
    ORDER BY
        CASE
            WHEN c.status = 'failed' THEN 1
            WHEN c.status = 'running' AND c.heartbeat_at < NOW() - (hb.timeout_sec || ' seconds')::interval THEN 2
            ELSE 3
        END,
        c.chunk_no
),

-- ============================================================
-- 08. WRITER BACKLOG
-- ============================================================
writer_backlog AS (
    SELECT
        (SELECT COUNT(*) FROM etl.load_chunk c, params p WHERE c.run_id = p.run_id AND c.status = 'running') AS running_count,
        (SELECT COUNT(*) FROM etl.load_chunk c, params p WHERE c.run_id = p.run_id AND c.status = 'ready_to_commit') AS ready_to_commit_count,
        (SELECT COUNT(*) FROM etl.load_chunk c, params p WHERE c.run_id = p.run_id AND c.status = 'writing') AS writing_count,
        (SELECT TO_CHAR(COALESCE(MIN(NOW() - c.updated_at), INTERVAL '0'), 'HH24:MI:SS')
         FROM etl.load_chunk c, params p WHERE c.run_id = p.run_id AND c.status = 'ready_to_commit') AS oldest_ready_age,
        (SELECT TO_CHAR(COALESCE(MIN(NOW() - c.updated_at), INTERVAL '0'), 'HH24:MI:SS')
         FROM etl.load_chunk c, params p WHERE c.run_id = p.run_id AND c.status = 'writing') AS oldest_writing_age
)

-- ============================================================
-- ВЫВОД РЕЗУЛЬТАТОВ
-- ============================================================

-- 01. Паспорт запуска
SELECT '=== 01. ПАСПОРТ ЗАПУСКА ===' AS block;
SELECT * FROM run_info;

-- 02. Статусы
SELECT '=== 02. СТАТУСЫ ЧАНКОВ ===' AS block;
SELECT * FROM ordered_status;

-- 03. Прогресс
SELECT '=== 03. ПРОГРЕСС ===' AS block;
SELECT * FROM progress;

-- 04. Активные worker
SELECT '=== 04. АКТИВНЫЕ WORKER ===' AS block;
SELECT * FROM active_workers;

-- 05. Heartbeat
SELECT '=== 05. HEARTBEAT ===' AS block;
SELECT * FROM heartbeat_check;

-- 06. Очередь
SELECT '=== 06. ОЧЕРЕДЬ RETRY/PENDING ===' AS block;
SELECT * FROM queue_summary;

-- 07. Ошибки
SELECT '=== 07. ОШИБКИ ===' AS block;
SELECT * FROM errors;

-- 08. Writer backlog
SELECT '=== 08. WRITER BACKLOG ===' AS block;
SELECT * FROM writer_backlog;
