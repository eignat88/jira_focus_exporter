-- sales_order_run45_diagnostic_queries.sql
-- Только диагностические запросы. INSERT/UPDATE/DELETE отсутствуют.
-- Для EXPLAIN замените :range_start на реальное числовое значение.

-- ============================================================
-- 1. Структура ETL-таблиц
-- ============================================================

SELECT
    ordinal_position,
    column_name,
    data_type,
    udt_name,
    is_nullable
FROM information_schema.columns
WHERE table_schema = 'etl'
  AND table_name = 'load_run'
ORDER BY ordinal_position;

SELECT
    ordinal_position,
    column_name,
    data_type,
    udt_name,
    is_nullable
FROM information_schema.columns
WHERE table_schema = 'etl'
  AND table_name = 'load_chunk'
ORDER BY ordinal_position;

-- ============================================================
-- 2. Runs 35, 36, 37, 45
-- ============================================================

SELECT *
FROM etl.load_run
WHERE run_id IN (35, 36, 37, 45)
ORDER BY run_id;

-- Этот вариант выполняйте только если перечисленные колонки существуют.
SELECT
    run_id,
    status,
    started_at,
    finished_at,
    heartbeat_at,
    total_chunks,
    completed_chunks,
    failed_chunks,
    rows_read,
    rows_inserted,
    rows_updated,
    rows_conflicted,
    error_message
FROM etl.load_run
WHERE run_id IN (35, 36, 37, 45)
ORDER BY run_id;

-- ============================================================
-- 3. Chunks run 45
-- ============================================================

SELECT *
FROM etl.load_chunk
WHERE run_id = 45
ORDER BY chunk_id;

-- Этот вариант выполняйте только если перечисленные колонки существуют.
SELECT
    run_id,
    chunk_id,
    status,
    range_start,
    range_end,
    rows_read,
    rows_inserted,
    rows_updated,
    rows_conflicted,
    started_at,
    finished_at,
    error_message
FROM etl.load_chunk
WHERE run_id = 45
ORDER BY chunk_id;

-- ============================================================
-- 4. Текущая активность PostgreSQL
-- ============================================================

SELECT
    pid,
    usename,
    application_name,
    state,
    wait_event_type,
    wait_event,
    now() - xact_start AS transaction_age,
    now() - query_start AS query_age,
    left(query, 500) AS query
FROM pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid()
ORDER BY query_start;

SELECT *
FROM pg_stat_progress_vacuum
WHERE relid IN (
    'raw_ax.salestable'::regclass,
    'dds.sales_order'::regclass
);

SELECT *
FROM pg_stat_progress_create_index
WHERE relid IN (
    'raw_ax.salestable'::regclass,
    'dds.sales_order'::regclass
);

-- ============================================================
-- 5. Реальная нижняя граница RECID
-- ============================================================

SELECT btrim(recid)::bigint AS min_recid
FROM raw_ax.salestable
WHERE recid IS NOT NULL
  AND btrim(recid) ~ '^[0-9]+$'
ORDER BY btrim(recid)::bigint
LIMIT 1;

-- ============================================================
-- 6. EXPLAIN без ANALYZE
-- Выражение должно точно совпадать с функциональным индексом:
-- btrim(recid)::bigint
-- ============================================================

EXPLAIN (COSTS, VERBOSE, SETTINGS)
SELECT
    btrim(recid)::bigint AS source_recid
FROM raw_ax.salestable
WHERE btrim(recid)::bigint >= :range_start
  AND btrim(recid)::bigint <  :range_start + 100000;

EXPLAIN (COSTS, VERBOSE, SETTINGS)
SELECT
    btrim(recid)::bigint AS source_recid
FROM raw_ax.salestable
WHERE btrim(recid)::bigint >= :range_start
  AND btrim(recid)::bigint <  :range_start + 250000;

-- ============================================================
-- 7. Ограниченный EXPLAIN ANALYZE
-- Выполнять только после проверки активности и только вручную.
-- ============================================================

EXPLAIN (ANALYZE, BUFFERS, WAL, TIMING, SUMMARY)
SELECT
    btrim(recid)::bigint AS source_recid
FROM raw_ax.salestable
WHERE btrim(recid)::bigint >= :range_start
  AND btrim(recid)::bigint <  :range_start + 100000;

EXPLAIN (ANALYZE, BUFFERS, WAL, TIMING, SUMMARY)
SELECT
    btrim(recid)::bigint AS source_recid
FROM raw_ax.salestable
WHERE btrim(recid)::bigint >= :range_start
  AND btrim(recid)::bigint <  :range_start + 250000;
