-- ============================================================
-- ETL MONITOR: БЫСТРЫЙ МОНИТОРИНГ
-- PostgreSQL 17
-- Запуск каждые 30–60 секунд
-- Использует только etl.load_run и etl.load_chunk
-- Скрипт не изменяет данные ETL.
-- ============================================================

-- ВАЖНО:
-- 1. Выполнять в подключении PostgreSQL к базе wms_analysis.
-- 2. Для ручного выбора запуска укажите run_id в блоке 00.
-- 3. Если manual_run_id = 0, будет выбран последний подходящий запуск.
-- ============================================================


-- ============================================================
-- 00. ПАРАМЕТРЫ
-- ============================================================

DROP TABLE IF EXISTS pg_temp.etl_monitor_params;

CREATE TEMP TABLE etl_monitor_params AS
SELECT
    COALESCE(
        NULLIF(19::bigint, 0),  -- ручной run_id; 0 = автоопределение
        (
            SELECT r.run_id
            FROM etl.load_run r
            WHERE UPPER(r.source_table) = 'ALK_MARKSERIAL'
              AND r.status IN (
                  'created',
                  'running',
                  'completed_with_errors',
                  'failed'
              )
            ORDER BY
                r.started_at DESC NULLS LAST,
                r.created_at DESC NULLS LAST,
                r.run_id DESC
            LIMIT 1
        )
    ) AS run_id,
    'raw_ax'::text AS target_schema,
    'alk_markserial'::text AS target_table,
    30::integer AS heartbeat_interval_sec,
    600::integer AS heartbeat_timeout_sec,
    10::integer AS writer_warning_minutes,
    5::integer AS max_attempts;


-- Проверка выбранных параметров
SELECT
    '00. ПАРАМЕТРЫ' AS block_name,
    p.*
FROM etl_monitor_params p;


-- ============================================================
-- 01. ПАСПОРТ ЗАПУСКА
-- ============================================================

SELECT
    '01. ПАСПОРТ ЗАПУСКА' AS block_name,
    r.run_id,
    r.pipeline_name,
    r.source_system,
    r.source_database,
    r.source_schema,
    r.source_table,
    r.target_schema,
    r.target_table,
    r.status AS run_status,
    r.load_mode,
    r.chunk_strategy,
    r.chunk_column,
    r.created_at,
    r.started_at,
    r.finished_at,
    CASE
        WHEN r.started_at IS NULL THEN NULL
        ELSE COALESCE(r.finished_at, CURRENT_TIMESTAMP) - r.started_at
    END AS elapsed,
    r.total_chunks,
    r.completed_chunks,
    CASE
        WHEN COALESCE(r.total_chunks, 0) > 0
        THEN ROUND(
            COALESCE(r.completed_chunks, 0) * 100.0
            / r.total_chunks,
            2
        )
        ELSE 0
    END AS completed_percent,
    r.rows_read,
    r.rows_inserted,
    r.rows_updated,
    r.rows_conflicted,
    r.error_message
FROM etl.load_run r
CROSS JOIN etl_monitor_params p
WHERE r.run_id = p.run_id;


-- ============================================================
-- 02. СВОДКА ПО СТАТУСАМ
-- ============================================================

WITH status_order(status, sort_no) AS (
    VALUES
        ('failed', 1),
        ('writing', 2),
        ('ready_to_commit', 3),
        ('running', 4),
        ('retry', 5),
        ('pending', 6),
        ('completed', 7),
        ('cancelled', 8)
),
run_total AS (
    SELECT COUNT(*)::bigint AS total_chunks
    FROM etl.load_chunk c
    CROSS JOIN etl_monitor_params p
    WHERE c.run_id = p.run_id
),
summary AS (
    SELECT
        c.status,
        COUNT(*)::bigint AS chunk_count,
        COALESCE(SUM(c.rows_read), 0)::bigint AS rows_read,
        COALESCE(SUM(c.rows_staged), 0)::bigint AS rows_staged,
        COALESCE(SUM(c.rows_inserted), 0)::bigint AS rows_inserted,
        COALESCE(SUM(c.rows_updated), 0)::bigint AS rows_updated,
        COALESCE(SUM(c.rows_conflicted), 0)::bigint AS rows_conflicted
    FROM etl.load_chunk c
    CROSS JOIN etl_monitor_params p
    WHERE c.run_id = p.run_id
    GROUP BY c.status
)
SELECT
    '02. СВОДКА ПО СТАТУСАМ' AS block_name,
    so.status,
    COALESCE(s.chunk_count, 0) AS chunk_count,
    COALESCE(s.rows_read, 0) AS rows_read,
    COALESCE(s.rows_staged, 0) AS rows_staged,
    COALESCE(s.rows_inserted, 0) AS rows_inserted,
    COALESCE(s.rows_updated, 0) AS rows_updated,
    COALESCE(s.rows_conflicted, 0) AS rows_conflicted,
    CASE
        WHEN rt.total_chunks > 0
        THEN ROUND(
            COALESCE(s.chunk_count, 0) * 100.0
            / rt.total_chunks,
            2
        )
        ELSE 0
    END AS chunk_percent
FROM status_order so
CROSS JOIN run_total rt
LEFT JOIN summary s
    ON s.status = so.status
ORDER BY so.sort_no;


-- ============================================================
-- 03. ГЛАВНАЯ ПАНЕЛЬ ПРОГРЕССА
-- ============================================================

WITH chunk_totals AS (
    SELECT
        COUNT(*)::bigint AS total_chunks,
        COUNT(*) FILTER (
            WHERE c.status = 'completed'
        )::bigint AS completed_chunks,
        COUNT(*) FILTER (
            WHERE c.status IN (
                'running',
                'ready_to_commit',
                'writing'
            )
        )::bigint AS active_chunks,
        COUNT(*) FILTER (
            WHERE c.status = 'retry'
        )::bigint AS retry_chunks,
        COUNT(*) FILTER (
            WHERE c.status = 'pending'
        )::bigint AS pending_chunks,
        COUNT(*) FILTER (
            WHERE c.status = 'failed'
        )::bigint AS failed_chunks,
        COALESCE(SUM(c.rows_read), 0)::bigint AS all_rows_read,
        COALESCE(SUM(c.rows_read) FILTER (
            WHERE c.status = 'completed'
        ), 0)::bigint AS completed_rows_read,
        COALESCE(SUM(c.rows_inserted), 0)::bigint AS rows_inserted,
        COALESCE(SUM(c.rows_conflicted), 0)::bigint AS rows_conflicted
    FROM etl.load_chunk c
    CROSS JOIN etl_monitor_params p
    WHERE c.run_id = p.run_id
),
run_info AS (
    SELECT
        r.started_at,
        r.finished_at,
        r.rows_read AS declared_total_rows
    FROM etl.load_run r
    CROSS JOIN etl_monitor_params p
    WHERE r.run_id = p.run_id
),
calc AS (
    SELECT
        ct.*,
        ri.started_at,
        ri.finished_at,
        ri.declared_total_rows,
        CASE
            WHEN ri.started_at IS NULL THEN 0::numeric
            ELSE EXTRACT(
                EPOCH FROM (
                    COALESCE(ri.finished_at, CURRENT_TIMESTAMP)
                    - ri.started_at
                )
            )::numeric
        END AS elapsed_seconds
    FROM chunk_totals ct
    CROSS JOIN run_info ri
)
SELECT
    '03. ГЛАВНАЯ ПАНЕЛЬ ПРОГРЕССА' AS block_name,
    c.total_chunks,
    c.completed_chunks,
    c.active_chunks,
    c.retry_chunks,
    c.pending_chunks,
    c.failed_chunks,
    c.all_rows_read,
    c.completed_rows_read,
    c.rows_inserted,
    c.rows_conflicted,
    c.declared_total_rows,
    CASE
        WHEN c.total_chunks > 0
        THEN ROUND(
            c.completed_chunks * 100.0
            / c.total_chunks,
            2
        )
        ELSE 0
    END AS completed_chunk_percent,
    CASE
        WHEN COALESCE(c.declared_total_rows, 0) > 0
        THEN ROUND(
            c.completed_rows_read * 100.0
            / c.declared_total_rows,
            2
        )
        ELSE NULL
    END AS completed_rows_percent,
    CASE
        WHEN c.elapsed_seconds > 0
        THEN ROUND(
            c.completed_rows_read
            / c.elapsed_seconds,
            0
        )
        ELSE 0
    END AS average_rows_per_second,
    CASE
        WHEN c.elapsed_seconds > 0
         AND c.completed_rows_read > 0
         AND COALESCE(c.declared_total_rows, 0) > c.completed_rows_read
        THEN (
            (
                c.declared_total_rows
                - c.completed_rows_read
            )
            /
            NULLIF(
                c.completed_rows_read
                / c.elapsed_seconds,
                0
            )
        ) * INTERVAL '1 second'
        ELSE NULL
    END AS estimated_remaining,
    CASE
        WHEN c.elapsed_seconds > 0
         AND c.completed_rows_read > 0
         AND COALESCE(c.declared_total_rows, 0) > c.completed_rows_read
        THEN CURRENT_TIMESTAMP + (
            (
                c.declared_total_rows
                - c.completed_rows_read
            )
            /
            NULLIF(
                c.completed_rows_read
                / c.elapsed_seconds,
                0
            )
        ) * INTERVAL '1 second'
        ELSE NULL
    END AS estimated_finish_at
FROM calc c;


-- ============================================================
-- 04. АКТИВНЫЕ WORKER
-- ============================================================

SELECT
    '04. АКТИВНЫЕ WORKER' AS block_name,
    c.chunk_no,
    c.status,
    c.worker_id,
    c.attempt_count,
    c.range_start_bigint,
    c.range_end_bigint,
    c.rows_read,
    c.rows_staged,
    c.last_processed_key,
    c.started_at,
    c.heartbeat_at,
    CASE
        WHEN c.started_at IS NULL THEN NULL
        ELSE CURRENT_TIMESTAMP - c.started_at
    END AS chunk_elapsed,
    CASE
        WHEN c.heartbeat_at IS NULL THEN NULL
        ELSE CURRENT_TIMESTAMP - c.heartbeat_at
    END AS heartbeat_age,
    CASE
        WHEN c.started_at IS NOT NULL
         AND CURRENT_TIMESTAMP > c.started_at
        THEN ROUND(
            c.rows_read
            / NULLIF(
                EXTRACT(
                    EPOCH FROM (
                        CURRENT_TIMESTAMP - c.started_at
                    )
                ),
                0
            ),
            0
        )
        ELSE 0
    END AS rows_per_second,
    CASE
        WHEN c.range_start_bigint IS NOT NULL
         AND c.range_end_bigint IS NOT NULL
         AND c.range_end_bigint > c.range_start_bigint
        THEN ROUND(
            (
                c.last_processed_key::bigint
                - c.range_start_bigint
            ) * 100.0
            / NULLIF(
                c.range_end_bigint
                - c.range_start_bigint,
                0
            ),
            2
        )
        ELSE NULL
    END AS key_range_progress_percent,
    c.error_type,
    c.error_message
FROM etl.load_chunk c
CROSS JOIN etl_monitor_params p
WHERE c.run_id = p.run_id
  AND c.status IN (
      'running',
      'ready_to_commit',
      'writing'
  )
ORDER BY c.chunk_no;


-- ============================================================
-- 05. КОНТРОЛЬ HEARTBEAT
-- ============================================================

SELECT
    '05. КОНТРОЛЬ HEARTBEAT' AS block_name,
    c.chunk_no,
    c.status,
    c.worker_id,
    c.started_at,
    c.heartbeat_at,
    CASE
        WHEN c.heartbeat_at IS NULL THEN NULL
        ELSE CURRENT_TIMESTAMP - c.heartbeat_at
    END AS heartbeat_age,
    CASE
        WHEN c.heartbeat_at IS NULL
            THEN 'NO_HEARTBEAT'
        WHEN CURRENT_TIMESTAMP - c.heartbeat_at
             > p.heartbeat_timeout_sec * INTERVAL '1 second'
            THEN 'STALE'
        WHEN CURRENT_TIMESTAMP - c.heartbeat_at
             > (p.heartbeat_interval_sec * 2) * INTERVAL '1 second'
            THEN 'WARNING'
        ELSE 'OK'
    END AS health_status
FROM etl.load_chunk c
CROSS JOIN etl_monitor_params p
WHERE c.run_id = p.run_id
  AND c.status IN (
      'running',
      'ready_to_commit',
      'writing'
  )
ORDER BY
    CASE
        WHEN c.heartbeat_at IS NULL THEN 1
        WHEN CURRENT_TIMESTAMP - c.heartbeat_at
             > p.heartbeat_timeout_sec * INTERVAL '1 second'
            THEN 2
        WHEN CURRENT_TIMESTAMP - c.heartbeat_at
             > (p.heartbeat_interval_sec * 2) * INTERVAL '1 second'
            THEN 3
        ELSE 4
    END,
    c.chunk_no;


-- ============================================================
-- 06. ОЧЕРЕДЬ RETRY / PENDING
-- ============================================================

SELECT
    '06. ОЧЕРЕДЬ RETRY / PENDING' AS block_name,
    c.chunk_no,
    c.status,
    c.attempt_count,
    c.worker_id AS previous_worker_id,
    c.rows_read AS previous_rows_read,
    c.rows_staged AS previous_rows_staged,
    c.last_processed_key,
    c.error_type,
    c.error_message,
    c.updated_at,
    CASE
        WHEN c.updated_at IS NULL THEN NULL
        ELSE CURRENT_TIMESTAMP - c.updated_at
    END AS queue_age
FROM etl.load_chunk c
CROSS JOIN etl_monitor_params p
WHERE c.run_id = p.run_id
  AND c.status IN ('retry', 'pending')
ORDER BY
    CASE c.status
        WHEN 'retry' THEN 1
        ELSE 2
    END,
    c.chunk_no;


-- ============================================================
-- 07. ОШИБКИ И ПРОБЛЕМНЫЕ СОСТОЯНИЯ
-- ============================================================

SELECT
    '07. ОШИБКИ И ПРОБЛЕМНЫЕ СОСТОЯНИЯ' AS block_name,
    CASE
        WHEN c.status = 'failed'
            THEN 'CRITICAL'
        WHEN c.status = 'running'
         AND (
             c.heartbeat_at IS NULL
             OR CURRENT_TIMESTAMP - c.heartbeat_at
                > p.heartbeat_timeout_sec * INTERVAL '1 second'
         )
            THEN 'CRITICAL'
        WHEN c.status = 'ready_to_commit'
         AND CURRENT_TIMESTAMP - c.updated_at
             > p.writer_warning_minutes * INTERVAL '1 minute'
            THEN 'WARNING'
        WHEN c.status = 'writing'
         AND CURRENT_TIMESTAMP - c.updated_at
             > p.writer_warning_minutes * INTERVAL '1 minute'
            THEN 'CRITICAL'
        WHEN c.attempt_count >= p.max_attempts
            THEN 'WARNING'
        WHEN c.error_type IS NOT NULL
            THEN 'WARNING'
        ELSE 'INFO'
    END AS severity,
    c.chunk_no,
    c.status,
    c.attempt_count,
    c.worker_id,
    c.started_at,
    c.heartbeat_at,
    c.updated_at,
    CASE
        WHEN c.heartbeat_at IS NULL THEN NULL
        ELSE CURRENT_TIMESTAMP - c.heartbeat_at
    END AS heartbeat_age,
    CASE
        WHEN c.updated_at IS NULL THEN NULL
        ELSE CURRENT_TIMESTAMP - c.updated_at
    END AS state_age,
    c.error_type,
    c.error_message,
    CASE
        WHEN c.status = 'failed'
            THEN 'Проверить error_type/error_message и причину отказа'
        WHEN c.status = 'running'
         AND c.heartbeat_at IS NULL
            THEN 'Проверить worker: heartbeat отсутствует'
        WHEN c.status = 'running'
         AND CURRENT_TIMESTAMP - c.heartbeat_at
             > p.heartbeat_timeout_sec * INTERVAL '1 second'
            THEN 'Проверить worker и при остановленном процессе перевести чанк в retry'
        WHEN c.status = 'ready_to_commit'
         AND CURRENT_TIMESTAMP - c.updated_at
             > p.writer_warning_minutes * INTERVAL '1 minute'
            THEN 'Проверить writer и наличие строк чанка в staging'
        WHEN c.status = 'writing'
         AND CURRENT_TIMESTAMP - c.updated_at
             > p.writer_warning_minutes * INTERVAL '1 minute'
            THEN 'Проверить PostgreSQL-транзакцию и блокировки'
        WHEN c.attempt_count >= p.max_attempts
            THEN 'Проверить причину повторных попыток'
        WHEN c.error_type IS NOT NULL
            THEN 'Проверить последнее сообщение об ошибке'
        ELSE 'Дополнительных действий не требуется'
    END AS recommended_action
FROM etl.load_chunk c
CROSS JOIN etl_monitor_params p
WHERE c.run_id = p.run_id
  AND (
      c.status = 'failed'
      OR (
          c.status = 'running'
          AND (
              c.heartbeat_at IS NULL
              OR CURRENT_TIMESTAMP - c.heartbeat_at
                 > p.heartbeat_timeout_sec * INTERVAL '1 second'
          )
      )
      OR (
          c.status = 'ready_to_commit'
          AND CURRENT_TIMESTAMP - c.updated_at
              > p.writer_warning_minutes * INTERVAL '1 minute'
      )
      OR (
          c.status = 'writing'
          AND CURRENT_TIMESTAMP - c.updated_at
              > p.writer_warning_minutes * INTERVAL '1 minute'
      )
      OR c.error_type IS NOT NULL
      OR c.attempt_count >= p.max_attempts
  )
ORDER BY
    CASE
        WHEN c.status = 'failed' THEN 1
        WHEN c.status = 'writing' THEN 2
        WHEN c.status = 'running' THEN 3
        WHEN c.status = 'ready_to_commit' THEN 4
        ELSE 5
    END,
    c.chunk_no;


-- ============================================================
-- 08. WRITER BACKLOG
-- ============================================================

SELECT
    '08. WRITER BACKLOG' AS block_name,
    COUNT(*) FILTER (
        WHERE c.status = 'running'
    ) AS running_count,
    COUNT(*) FILTER (
        WHERE c.status = 'ready_to_commit'
    ) AS ready_to_commit_count,
    COUNT(*) FILTER (
        WHERE c.status = 'writing'
    ) AS writing_count,
    MAX(
        CURRENT_TIMESTAMP - c.updated_at
    ) FILTER (
        WHERE c.status = 'ready_to_commit'
    ) AS oldest_ready_to_commit_age,
    MAX(
        CURRENT_TIMESTAMP - c.updated_at
    ) FILTER (
        WHERE c.status = 'writing'
    ) AS oldest_writing_age,
    CASE
        WHEN COUNT(*) FILTER (
            WHERE c.status = 'writing'
              AND CURRENT_TIMESTAMP - c.updated_at
                  > p.writer_warning_minutes * INTERVAL '1 minute'
        ) > 0
            THEN 'CRITICAL: writing длится слишком долго'
        WHEN COUNT(*) FILTER (
            WHERE c.status = 'ready_to_commit'
              AND CURRENT_TIMESTAMP - c.updated_at
                  > p.writer_warning_minutes * INTERVAL '1 minute'
        ) > 0
            THEN 'WARNING: есть очередь перед writer'
        WHEN COUNT(*) FILTER (
            WHERE c.status = 'ready_to_commit'
        ) > 0
            THEN 'INFO: writer обрабатывает очередь'
        ELSE 'OK'
    END AS writer_status
FROM etl.load_chunk c
CROSS JOIN etl_monitor_params p
WHERE c.run_id = p.run_id
GROUP BY p.writer_warning_minutes;


-- ============================================================
-- 09. ПРОИЗВОДИТЕЛЬНОСТЬ ЗАВЕРШЁННЫХ ЧАНКОВ
-- ============================================================

WITH completed AS (
    SELECT
        c.chunk_no,
        c.attempt_count,
        c.rows_read,
        c.rows_staged,
        c.rows_inserted,
        c.rows_conflicted,
        c.started_at,
        c.completed_at,
        CASE
            WHEN c.started_at IS NOT NULL
             AND c.completed_at IS NOT NULL
             AND c.completed_at > c.started_at
            THEN EXTRACT(
                EPOCH FROM (
                    c.completed_at - c.started_at
                )
            )::numeric
            ELSE NULL
        END AS duration_seconds
    FROM etl.load_chunk c
    CROSS JOIN etl_monitor_params p
    WHERE c.run_id = p.run_id
      AND c.status = 'completed'
)
SELECT
    '09. ПРОИЗВОДИТЕЛЬНОСТЬ ЗАВЕРШЁННЫХ ЧАНКОВ' AS block_name,
    c.chunk_no,
    c.attempt_count,
    c.rows_read,
    c.rows_staged,
    c.rows_inserted,
    c.rows_conflicted,
    c.started_at,
    c.completed_at,
    c.duration_seconds * INTERVAL '1 second' AS duration,
    CASE
        WHEN c.duration_seconds > 0
        THEN ROUND(
            c.rows_read / c.duration_seconds,
            0
        )
        ELSE NULL
    END AS rows_per_second
FROM completed c
ORDER BY c.chunk_no;


-- ============================================================
-- 10. ИТОГОВОЕ ЗАКЛЮЧЕНИЕ
-- ============================================================

WITH state AS (
    SELECT
        COUNT(*)::bigint AS total_chunks,
        COUNT(*) FILTER (
            WHERE c.status = 'completed'
        )::bigint AS completed_chunks,
        COUNT(*) FILTER (
            WHERE c.status = 'failed'
        )::bigint AS failed_chunks,
        COUNT(*) FILTER (
            WHERE c.status = 'retry'
        )::bigint AS retry_chunks,
        COUNT(*) FILTER (
            WHERE c.status = 'running'
        )::bigint AS running_chunks,
        COUNT(*) FILTER (
            WHERE c.status = 'ready_to_commit'
        )::bigint AS ready_chunks,
        COUNT(*) FILTER (
            WHERE c.status = 'writing'
        )::bigint AS writing_chunks,
        COUNT(*) FILTER (
            WHERE c.status = 'running'
              AND (
                  c.heartbeat_at IS NULL
                  OR CURRENT_TIMESTAMP - c.heartbeat_at
                     > p.heartbeat_timeout_sec * INTERVAL '1 second'
              )
        )::bigint AS stale_running_chunks,
        COUNT(*) FILTER (
            WHERE c.status = 'ready_to_commit'
              AND CURRENT_TIMESTAMP - c.updated_at
                  > p.writer_warning_minutes * INTERVAL '1 minute'
        )::bigint AS stuck_ready_chunks,
        COUNT(*) FILTER (
            WHERE c.status = 'writing'
              AND CURRENT_TIMESTAMP - c.updated_at
                  > p.writer_warning_minutes * INTERVAL '1 minute'
        )::bigint AS stuck_writing_chunks
    FROM etl.load_chunk c
    CROSS JOIN etl_monitor_params p
    WHERE c.run_id = p.run_id
    GROUP BY
        p.heartbeat_timeout_sec,
        p.writer_warning_minutes
)
SELECT
    '10. ИТОГОВОЕ ЗАКЛЮЧЕНИЕ' AS block_name,
    CASE
        WHEN s.total_chunks > 0
         AND s.completed_chunks = s.total_chunks
            THEN 'COMPLETED'
        WHEN s.failed_chunks > 0
          OR s.stale_running_chunks > 0
          OR s.stuck_writing_chunks > 0
            THEN 'CRITICAL'
        WHEN s.retry_chunks > 0
          OR s.stuck_ready_chunks > 0
            THEN 'WARNING'
        ELSE 'HEALTHY'
    END AS overall_status,
    CASE
        WHEN s.total_chunks > 0
         AND s.completed_chunks = s.total_chunks
            THEN 'Все чанки завершены'
        WHEN s.failed_chunks > 0
            THEN 'Есть чанки в статусе failed'
        WHEN s.stale_running_chunks > 0
            THEN 'Есть running-чанки с просроченным heartbeat'
        WHEN s.stuck_writing_chunks > 0
            THEN 'Есть чанки, зависшие в writing'
        WHEN s.stuck_ready_chunks > 0
            THEN 'Есть чанки, долго ожидающие writer'
        WHEN s.retry_chunks > 0
            THEN 'Загрузка работает, но присутствует очередь retry'
        WHEN s.running_chunks > 0
          OR s.ready_chunks > 0
          OR s.writing_chunks > 0
            THEN 'Загрузка выполняется штатно'
        ELSE 'Активная загрузка отсутствует'
    END AS diagnosis,
    CASE
        WHEN s.total_chunks > 0
         AND s.completed_chunks = s.total_chunks
            THEN 'Дополнительных действий не требуется'
        WHEN s.failed_chunks > 0
            THEN 'Проверить ошибки failed-чанков'
        WHEN s.stale_running_chunks > 0
            THEN 'Проверить процессы worker и heartbeat'
        WHEN s.stuck_writing_chunks > 0
            THEN 'Проверить PostgreSQL, writer и блокировки'
        WHEN s.stuck_ready_chunks > 0
            THEN 'Проверить writer и staging'
        WHEN s.retry_chunks > 0
            THEN 'Наблюдать за уменьшением retry и ростом completed'
        ELSE 'Продолжать мониторинг'
    END AS recommended_action,
    s.*
FROM state s;
