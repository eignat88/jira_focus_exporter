\set ON_ERROR_STOP on
\pset pager off
\pset null '[NULL]'

\echo 'VERSION: diagnose_sales_order_status_v3_20260730'
\echo 'READ ONLY: только SELECT и EXPLAIN'

SET default_transaction_read_only = on;
SET statement_timeout = '60s';
SET lock_timeout = '3s';

\echo ''
\echo '01. Таблицы'
SELECT
    to_regclass('raw_ax.salestable') AS raw_table,
    to_regclass('dds.sales_order') AS dds_table,
    CASE
        WHEN to_regclass('raw_ax.salestable') IS NULL THEN 'BLOCKED_RAW_MISSING'
        WHEN to_regclass('dds.sales_order') IS NULL THEN 'BLOCKED_DDS_MISSING'
        ELSE 'OK'
    END AS status;

\echo ''
\echo '02. Размеры'
SELECT
    n.nspname AS schema_name,
    c.relname AS table_name,
    pg_size_pretty(pg_relation_size(c.oid)) AS heap_size,
    pg_size_pretty(pg_indexes_size(c.oid)) AS indexes_size,
    pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size,
    pg_relation_size(c.oid) AS heap_bytes,
    pg_indexes_size(c.oid) AS indexes_bytes,
    pg_total_relation_size(c.oid) AS total_bytes
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE (n.nspname, c.relname) IN (
    ('raw_ax', 'salestable'),
    ('dds', 'sales_order')
)
ORDER BY n.nspname, c.relname;

\echo ''
\echo '03. Статистика'
SELECT
    schemaname,
    relname,
    n_live_tup,
    n_dead_tup,
    last_analyze,
    last_autoanalyze,
    last_vacuum,
    last_autovacuum
FROM pg_stat_user_tables
WHERE (schemaname, relname) IN (
    ('raw_ax', 'salestable'),
    ('dds', 'sales_order')
)
ORDER BY schemaname, relname;

\echo ''
\echo '04. Оценка строк'
SELECT
    n.nspname AS schema_name,
    c.relname AS table_name,
    c.reltuples::bigint AS estimated_rows,
    c.relpages AS estimated_pages
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE (n.nspname, c.relname) IN (
    ('raw_ax', 'salestable'),
    ('dds', 'sales_order')
)
ORDER BY n.nspname, c.relname;

\echo ''
\echo '05. Ключевые колонки RAW'
SELECT
    ordinal_position,
    column_name,
    data_type,
    is_nullable
FROM information_schema.columns
WHERE table_schema = 'raw_ax'
  AND table_name = 'salestable'
  AND column_name IN (
      'recid','salesid','dataareaid','partition','custaccount',
      'deliverydate','currencycode','salesstatus',
      'modifieddatetime','createddatetime'
  )
ORDER BY ordinal_position;

\echo ''
\echo '06. Колонки DDS'
SELECT
    ordinal_position,
    column_name,
    data_type,
    is_nullable,
    column_default
FROM information_schema.columns
WHERE table_schema = 'dds'
  AND table_name = 'sales_order'
ORDER BY ordinal_position;

\echo ''
\echo '07. Индексы'
SELECT
    schemaname,
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE (schemaname, tablename) IN (
    ('raw_ax', 'salestable'),
    ('dds', 'sales_order')
)
ORDER BY schemaname, tablename, indexname;

\echo ''
\echo '08. Ограничения DDS'
SELECT
    con.conname AS constraint_name,
    con.contype AS constraint_type,
    pg_get_constraintdef(con.oid) AS constraint_definition
FROM pg_constraint con
JOIN pg_class cls ON cls.oid = con.conrelid
JOIN pg_namespace ns ON ns.oid = cls.relnamespace
WHERE ns.nspname = 'dds'
  AND cls.relname = 'sales_order'
ORDER BY con.contype, con.conname;

\echo ''
\echo '09. Индексные колонки RAW'
SELECT
    idx.relname AS index_name,
    i.indisvalid AS is_valid,
    i.indisready AS is_ready,
    i.indisunique AS is_unique,
    i.indisprimary AS is_primary,
    ord.ordinality AS index_column_position,
    COALESCE(
        att.attname,
        pg_get_indexdef(i.indexrelid, ord.ordinality::integer, true)
    ) AS indexed_column_or_expression
FROM pg_index i
JOIN pg_class tbl ON tbl.oid = i.indrelid
JOIN pg_namespace ns ON ns.oid = tbl.relnamespace
JOIN pg_class idx ON idx.oid = i.indexrelid
CROSS JOIN LATERAL
    unnest(i.indkey) WITH ORDINALITY AS ord(attnum, ordinality)
LEFT JOIN pg_attribute att
    ON att.attrelid = tbl.oid
   AND att.attnum = ord.attnum
WHERE ns.nspname = 'raw_ax'
  AND tbl.relname = 'salestable'
ORDER BY idx.relname, ord.ordinality;

\echo ''
\echo '10. Активные процессы'
SELECT
    pid,
    usename,
    application_name,
    state,
    wait_event_type,
    wait_event,
    clock_timestamp() - query_start AS query_duration,
    left(query, 1200) AS query_text
FROM pg_stat_activity
WHERE pid <> pg_backend_pid()
  AND (
        query ILIKE '%raw_ax.salestable%'
        OR query ILIKE '%dds.sales_order%'
        OR query ILIKE '%sales_order%'
        OR query ILIKE '%salestable%'
      )
ORDER BY query_start;

\echo ''
\echo '11. Структура ETL'
SELECT
    table_name,
    ordinal_position,
    column_name,
    data_type
FROM information_schema.columns
WHERE table_schema = 'etl'
  AND table_name IN ('load_run', 'load_chunk')
ORDER BY table_name, ordinal_position;

\echo ''
\echo '12. Наличие ETL-таблиц'
SELECT
    CASE WHEN to_regclass('etl.load_run') IS NOT NULL THEN 1 ELSE 0 END AS has_load_run,
    CASE WHEN to_regclass('etl.load_chunk') IS NOT NULL THEN 1 ELSE 0 END AS has_load_chunk
\gset

\if :has_load_run
    \echo '12.1 История etl.load_run'
    SELECT to_jsonb(lr) AS load_run
    FROM etl.load_run lr
    WHERE to_jsonb(lr)::text ILIKE '%salestable%'
       OR to_jsonb(lr)::text ILIKE '%sales_order%'
    ORDER BY to_jsonb(lr)::text DESC
    LIMIT 50;
\else
    \echo 'SKIPPED: etl.load_run отсутствует'
\endif

\if :has_load_chunk
    \echo '12.2 История etl.load_chunk'
    SELECT to_jsonb(lc) AS load_chunk
    FROM etl.load_chunk lc
    WHERE to_jsonb(lc)::text ILIKE '%salestable%'
       OR to_jsonb(lc)::text ILIKE '%sales_order%'
    ORDER BY to_jsonb(lc)::text DESC
    LIMIT 100;
\else
    \echo 'SKIPPED: etl.load_chunk отсутствует'
\endif

\echo ''
\echo '13. Небольшая выборка RAW'
SELECT recid, salesid, dataareaid, partition
FROM raw_ax.salestable
ORDER BY recid
LIMIT 20;

\echo ''
\echo '14. EXPLAIN текстового chunking'
EXPLAIN (COSTS, VERBOSE, SETTINGS)
SELECT recid, salesid, dataareaid, partition
FROM raw_ax.salestable
WHERE recid > '0'
ORDER BY recid
LIMIT 1000;

\echo ''
\echo '15. EXPLAIN числового chunking'
EXPLAIN (COSTS, VERBOSE, SETTINGS)
SELECT recid, salesid, dataareaid, partition
FROM raw_ax.salestable
WHERE trim(recid)::bigint > 0
ORDER BY trim(recid)::bigint
LIMIT 1000;

\echo ''
\echo '16. EXPLAIN максимального recid'
EXPLAIN (COSTS, VERBOSE, SETTINGS)
SELECT recid
FROM raw_ax.salestable
ORDER BY recid DESC
LIMIT 1;

\echo ''
\echo '17. Итог'
SELECT
    CASE
        WHEN to_regclass('raw_ax.salestable') IS NULL THEN 'BLOCKED_RAW_MISSING'
        WHEN to_regclass('dds.sales_order') IS NULL THEN 'BLOCKED_DDS_MISSING'
        WHEN pg_relation_size('dds.sales_order'::regclass) = 0
            THEN 'TARGET_CREATED_DATA_NOT_LOADED'
        ELSE 'DATA_PRESENT_REQUIRES_ETL_VALIDATION'
    END AS integration_status,
    pg_size_pretty(pg_relation_size('raw_ax.salestable'::regclass)) AS raw_heap_size,
    pg_size_pretty(pg_relation_size('dds.sales_order'::regclass)) AS dds_heap_size,
    (
        SELECT n_live_tup
        FROM pg_stat_user_tables
        WHERE schemaname = 'dds'
          AND relname = 'sales_order'
    ) AS dds_estimated_rows;

\echo ''
\echo 'Диагностика завершена.'
