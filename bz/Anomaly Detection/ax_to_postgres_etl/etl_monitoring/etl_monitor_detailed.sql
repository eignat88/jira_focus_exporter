-- ============================================================
-- ETL MONITOR: ДЕТАЛЬНЫЙ АНАЛИЗ
-- Запуск периодически (раз в 5-15 минут)
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

-- ============================================================
-- 09. ПРОИЗВОДИТЕЛЬНОСТЬ ЧАНКОВ
-- ============================================================
chunk_performance AS (
    SELECT
        c.chunk_no,
        c.rows_read,
        c.rows_inserted,
        c.rows_conflicted,
        c.started_at,
        c.completed_at,
        c.attempt_count,
        EXTRACT(EPOCH FROM (c.completed_at - c.started_at)) AS duration_sec,
        CASE
            WHEN EXTRACT(EPOCH FROM (c.completed_at - c.started_at)) > 0
            THEN ROUND(c.rows_read / EXTRACT(EPOCH FROM (c.completed_at - c.started_at)))
            ELSE 0
        END AS rows_per_sec,
        CASE
            WHEN EXTRACT(EPOCH FROM (c.completed_at - c.started_at)) > 0
            THEN ROUND(c.rows_read * 60.0 / EXTRACT(EPOCH FROM (c.completed_at - c.started_at)))
            ELSE 0
        END AS rows_per_min
    FROM etl.load_chunk c, params p
    WHERE c.run_id = p.run_id
      AND c.status = 'completed'
    ORDER BY c.chunk_no
),
chunk_stats AS (
    SELECT
        MIN(rows_per_sec) AS min_speed,
        MAX(rows_per_sec) AS max_speed,
        AVG(rows_per_sec)::int AS avg_speed,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY rows_per_sec)::int AS median_speed,
        AVG(duration_sec)::int AS avg_duration,
        COUNT(*) FILTER (WHERE rows_per_sec < (SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY rows_per_sec) * 0.7 FROM chunk_performance)) AS slow_chunks
    FROM chunk_performance
),

-- ============================================================
-- 10. ПРОИЗВОДИТЕЛЬНОСТЬ WORKER
-- ============================================================
worker_performance AS (
    SELECT
        c.worker_id,
        COUNT(*) AS chunks_processed,
        SUM(c.rows_read) AS total_rows,
        AVG(EXTRACT(EPOCH FROM (c.completed_at - c.started_at)))::int AS avg_duration,
        AVG(CASE
            WHEN EXTRACT(EPOCH FROM (c.completed_at - c.started_at)) > 0
            THEN c.rows_read / EXTRACT(EPOCH FROM (c.completed_at - c.started_at))
            ELSE 0
        END)::int AS avg_speed,
        COUNT(*) FILTER (WHERE c.error_type IS NOT NULL) AS error_count,
        COUNT(*) FILTER (WHERE c.attempt_count > 1) AS retry_count,
        MAX(c.heartbeat_at) AS last_heartbeat
    FROM etl.load_chunk c, params p
    WHERE c.run_id = p.run_id
      AND c.worker_id IS NOT NULL
    GROUP BY c.worker_id
),

-- ============================================================
-- 11. ПРОГНОЗ ЗАВЕРШЕНИЯ
-- ============================================================
remaining AS (
    SELECT
        ri.total_chunks - COALESCE(ss_completed.chunk_count, 0) AS remaining_chunks,
        ri.run_rows_read - COALESCE(ss_completed.rows_inserted, 0) AS remaining_rows
    FROM (SELECT * FROM etl.load_run WHERE run_id = (SELECT run_id FROM params)) ri
    LEFT JOIN (
        SELECT COUNT(*) AS chunk_count, SUM(rows_inserted) AS rows_inserted
        FROM etl.load_chunk WHERE run_id = (SELECT run_id FROM params) AND status = 'completed'
    ) ss_completed ON true
),
forecast AS (
    SELECT
        r.remaining_rows,
        r.remaining_chunks,
        cs.avg_speed,
        cs.avg_duration,
        -- Метод 1: по средней скорости
        CASE WHEN cs.avg_speed > 0
            THEN TO_CHAR((r.remaining_rows / cs.avg_speed) * INTERVAL '1 second', 'HH24:MI:SS')
            ELSE 'N/A'
        END AS est_by_rows,
        -- Метод 2: по средней длительности
        CASE WHEN cs.avg_duration > 0
            THEN TO_CHAR((r.remaining_chunks * cs.avg_duration / 4.0) * INTERVAL '1 second', 'HH24:MI:SS')
            ELSE 'N/A'
        END AS est_by_chunks,
        -- Пессимистичный (x1.3)
        CASE WHEN cs.avg_speed > 0
            THEN TO_CHAR((r.remaining_rows / cs.avg_speed * 1.3) * INTERVAL '1 second', 'HH24:MI:SS')
            ELSE 'N/A'
        END AS pessimistic,
        -- Оптимистичный (x0.8)
        CASE WHEN cs.avg_speed > 0
            THEN TO_CHAR((r.remaining_rows / cs.avg_speed * 0.8) * INTERVAL '1 second', 'HH24:MI:SS')
            ELSE 'N/A'
        END AS optimistic
    FROM remaining r, chunk_stats cs
)

SELECT '=== 09. ПРОИЗВОДИТЕЛЬНОСТЬ ЧАНКОВ ===' AS block;
SELECT * FROM chunk_performance ORDER BY chunk_no;

SELECT '=== 09b. АГРЕГАТЫ ЧАНКОВ ===' AS block;
SELECT * FROM chunk_stats;

SELECT '=== 10. ПРОИЗВОДИТЕЛЬНОСТЬ WORKER ===' AS block;
SELECT * FROM worker_performance ORDER BY chunks_processed DESC;

SELECT '=== 11. ПРОГНОЗ ЗАВЕРШЕНИЯ ===' AS block;
SELECT * FROM forecast;
