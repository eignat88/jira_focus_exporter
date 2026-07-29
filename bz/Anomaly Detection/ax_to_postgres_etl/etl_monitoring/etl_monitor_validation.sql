-- ============================================================
-- ETL MONITOR: ВАЛИДАЦИЯ ДАННЫХ
-- Тяжёлые запросы — запускать только при необходимости
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
target AS (
    SELECT 'raw_ax'::text AS schema_name, 'alk_markserial'::text AS table_name
),

-- ============================================================
-- 12. ВАЛИДАЦИЯ COMPLETED-ЧАНКОВ
-- ТЯЖЁЛЫЙ ЗАПРОС. Не запускать каждую минуту.
-- ============================================================
completed_validation AS (
    SELECT
        c.chunk_no,
        c.rows_read,
        c.rows_staged,
        c.rows_inserted,
        c.rows_conflicted,
        (c.range_end_bigint - c.range_start_bigint) AS expected_rows,
        -- Подсчёт строк в target для этого чанка
        (SELECT COUNT(*)
         FROM raw_ax.alk_markserial t
         WHERE t.recid::bigint >= c.range_start_bigint
           AND t.recid::bigint < c.range_end_bigint) AS rows_in_target,
        CASE
            WHEN c.rows_read = 0 THEN 'EMPTY_COMPLETED_CHUNK'
            WHEN c.rows_read != (c.range_end_bigint - c.range_start_bigint) THEN 'READ_COUNT_MISMATCH'
            WHEN c.rows_inserted + c.rows_conflicted != c.rows_read THEN 'METRICS_MISMATCH'
            WHEN (SELECT COUNT(*) FROM raw_ax.alk_markserial t
                  WHERE t.recid::bigint >= c.range_start_bigint
                    AND t.recid::bigint < c.range_end_bigint) != (c.range_end_bigint - c.range_start_bigint) THEN 'TARGET_COUNT_MISMATCH'
            ELSE 'OK'
        END AS validation_status
    FROM etl.load_chunk c, params p
    WHERE c.run_id = p.run_id
      AND c.status = 'completed'
    ORDER BY c.chunk_no
),

-- ============================================================
-- 13. ПРОВЕРКА ДИАПАЗОНОВ
-- ============================================================
range_check AS (
    SELECT
        c.chunk_no,
        c.range_start_bigint,
        c.range_end_bigint,
        LAG(c.range_end_bigint) OVER (ORDER BY c.chunk_no) AS prev_range_end,
        CASE
            WHEN c.range_start_bigint >= c.range_end_bigint THEN 'INVALID_RANGE'
            WHEN LAG(c.range_end_bigint) OVER (ORDER BY c.chunk_no) > c.range_start_bigint THEN 'OVERLAP'
            WHEN LAG(c.range_end_bigint) OVER (ORDER BY c.chunk_no) IS NOT NULL
                 AND LAG(c.range_end_bigint) OVER (ORDER BY c.chunk_no) != c.range_start_bigint THEN 'GAP'
            ELSE 'OK'
        END AS range_status
    FROM etl.load_chunk c, params p
    WHERE c.run_id = p.run_id
    ORDER BY c.chunk_no
),

-- ============================================================
-- 14. ПРОВЕРКА STAGING
-- ============================================================
staging_check AS (
    SELECT
        (SELECT COUNT(*) FROM raw_ax."_staging_alk_markserial") AS total_staging_rows,
        (SELECT COUNT(DISTINCT _etl_chunk_id) FROM raw_ax."_staging_alk_markserial") AS distinct_chunks,
        -- Строки для completed-чанков
        (SELECT COUNT(*) FROM raw_ax."_staging_alk_markserial" s
         WHERE s._etl_chunk_id IN (
             SELECT chunk_id FROM etl.load_chunk
             WHERE run_id = (SELECT run_id FROM params) AND status = 'completed'
         )) AS rows_for_completed,
        -- Строки для failed-чанков
        (SELECT COUNT(*) FROM raw_ax."_staging_alk_markserial" s
         WHERE s._etl_chunk_id IN (
             SELECT chunk_id FROM etl.load_chunk
             WHERE run_id = (SELECT run_id FROM params) AND status = 'failed'
         )) AS rows_for_failed,
        -- Orphan-строки (нет в load_chunk)
        (SELECT COUNT(*) FROM raw_ax."_staging_alk_markserial" s
         WHERE s._etl_chunk_id NOT IN (
             SELECT chunk_id FROM etl.load_chunk
             WHERE run_id = (SELECT run_id FROM params)
         )) AS orphan_rows
)

SELECT '=== 12. ВАЛИДАЦИЯ COMPLETED ===' AS block;
SELECT * FROM completed_validation;

SELECT '=== 13. ДИАПАЗОНЫ ===' AS block;
SELECT * FROM range_check;

SELECT '=== 14. STAGING ===' AS block;
SELECT * FROM staging_check;
