BEGIN TRANSACTION READ ONLY;

SET LOCAL statement_timeout = '60s';
SET LOCAL lock_timeout = '3s';
SET LOCAL idle_in_transaction_session_timeout = '5min';

-- ============================================================
-- 01. Существование RAW и DDS
-- ============================================================

SELECT
    '01_table_exists' AS section,
    to_regclass('raw_ax.salestable') AS raw_table,
    to_regclass('dds.sales_order') AS dds_table,
    CASE
        WHEN to_regclass('raw_ax.salestable') IS NULL
            THEN 'BLOCKED: raw_ax.salestable отсутствует'
        WHEN to_regclass('dds.sales_order') IS NULL
            THEN 'BLOCKED: dds.sales_order отсутствует'
        ELSE 'OK: обе таблицы существуют'
    END AS status;


-- ============================================================
-- 02. Размеры таблиц
-- Не сканирует содержимое таблиц
-- ============================================================

SELECT
    '02_table_sizes' AS section,
    n.nspname AS schema_name,
    c.relname AS table_name,
    pg_size_pretty(pg_relation_size(c.oid)) AS heap_size,
    pg_size_pretty(pg_indexes_size(c.oid)) AS indexes_size,
    pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size,
    pg_relation_size(c.oid) AS heap_bytes,
    pg_indexes_size(c.oid) AS indexes_bytes,
    pg_total_relation_size(c.oid) AS total_bytes
FROM pg_class c
JOIN pg_namespace n
    ON n.oid = c.relnamespace
WHERE (n.nspname, c.relname) IN (
    ('raw_ax', 'salestable'),
    ('dds', 'sales_order')
)
ORDER BY n.nspname, c.relname;


-- ============================================================
-- 03. Оценочная статистика
-- n_live_tup не является точным COUNT(*)
-- ============================================================

SELECT
    '03_table_statistics' AS section,
    schemaname,
    relname,
    n_live_tup,
    n_dead_tup,
    last_analyze,
    last_autoanalyze,
    last_vacuum,
    last_autovacuum,
    analyze_count,
    autoanalyze_count,
    vacuum_count,
    autovacuum_count
FROM pg_stat_user_tables
WHERE (schemaname, relname) IN (
    ('raw_ax', 'salestable'),
    ('dds', 'sales_order')
)
ORDER BY schemaname, relname;


-- ============================================================
-- 04. Колонки RAW
-- ============================================================

SELECT
    '04_raw_columns' AS section,
    ordinal_position,
    column_name,
    data_type,
    udt_name,
    character_maximum_length,
    numeric_precision,
    numeric_scale,
    is_nullable,
    column_default
FROM information_schema.columns
WHERE table_schema = 'raw_ax'
  AND table_name = 'salestable'
ORDER BY ordinal_position;


-- ============================================================
-- 05. Колонки DDS
-- ============================================================

SELECT
    '05_dds_columns' AS section,
    ordinal_position,
    column_name,
    data_type,
    udt_name,
    character_maximum_length,
    numeric_precision,
    numeric_scale,
    is_nullable,
    column_default
FROM information_schema.columns
WHERE table_schema = 'dds'
  AND table_name = 'sales_order'
ORDER BY ordinal_position;


-- ============================================================
-- 06. Индексы RAW и DDS
-- ============================================================

SELECT
    '06_indexes' AS section,
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


-- ============================================================
-- 07. Ограничения DDS
-- Проверяем PK и UNIQUE, необходимые для ON CONFLICT
-- ============================================================

SELECT
    '07_dds_constraints' AS section,
    con.conname AS constraint_name,
    con.contype AS constraint_type,
    CASE con.contype
        WHEN 'p' THEN 'PRIMARY KEY'
        WHEN 'u' THEN 'UNIQUE'
        WHEN 'f' THEN 'FOREIGN KEY'
        WHEN 'c' THEN 'CHECK'
        WHEN 'x' THEN 'EXCLUSION'
        ELSE con.contype::text
    END AS constraint_type_name,
    pg_get_constraintdef(con.oid) AS constraint_definition
FROM pg_constraint con
JOIN pg_class cls
    ON cls.oid = con.conrelid
JOIN pg_namespace ns
    ON ns.oid = cls.relnamespace
WHERE ns.nspname = 'dds'
  AND cls.relname = 'sales_order'
ORDER BY con.contype, con.conname;


-- ============================================================
-- 08. Индексные колонки RAW
-- Показывает порядок колонок в индексах
-- ============================================================

SELECT
    '08_raw_index_columns' AS section,
    idx.relname AS index_name,
    i.indisunique AS is_unique,
    i.indisprimary AS is_primary,
    ord.ordinality AS index_column_position,
    COALESCE(att.attname, pg_get_indexdef(i.indexrelid, ord.ordinality, true))
        AS indexed_column_or_expression
FROM pg_index i
JOIN pg_class tbl
    ON tbl.oid = i.indrelid
JOIN pg_namespace ns
    ON ns.oid = tbl.relnamespace
JOIN pg_class idx
    ON idx.oid = i.indexrelid
CROSS JOIN LATERAL
    unnest(i.indkey) WITH ORDINALITY AS ord(attnum, ordinality)
LEFT JOIN pg_attribute att
    ON att.attrelid = tbl.oid
   AND att.attnum = ord.attnum
WHERE ns.nspname = 'raw_ax'
  AND tbl.relname = 'salestable'
ORDER BY idx.relname, ord.ordinality;


-- ============================================================
-- 09. Активные процессы по этим таблицам
-- ============================================================

SELECT
    '09_pg_stat_activity' AS section,
    pid,
    usename,
    application_name,
    state,
    wait_event_type,
    wait_event,
    backend_xid,
    backend_xmin,
    clock_timestamp() - query_start AS query_duration,
    left(query, 1000) AS query_text
FROM pg_stat_activity
WHERE pid <> pg_backend_pid()
  AND (
        query ILIKE '%raw_ax.salestable%'
        OR query ILIKE '%dds.sales_order%'
        OR query ILIKE '%sales_order%'
        OR query ILIKE '%salestable%'
      )
ORDER BY query_start;


-- ============================================================
-- 10. История ETL
-- Не предполагает фиксированный набор колонок etl.load_run
-- ============================================================

SELECT
    '10_etl_load_run_structure' AS section,
    ordinal_position,
    column_name,
    data_type,
    is_nullable
FROM information_schema.columns
WHERE table_schema = 'etl'
  AND table_name = 'load_run'
ORDER BY ordinal_position;


-- Поиск запусков через JSONB позволяет не зависеть от точных названий
-- полей source_table / target_table / stage_name.

SELECT
    '11_etl_load_run_history' AS section,
    to_jsonb(lr) AS load_run
FROM etl.load_run lr
WHERE to_jsonb(lr)::text ILIKE '%salestable%'
   OR to_jsonb(lr)::text ILIKE '%sales_order%'
ORDER BY to_jsonb(lr)::text DESC
LIMIT 50;


-- ============================================================
-- 12. История chunks
-- Выполняется только если таблица etl.load_chunk существует
-- ============================================================

SELECT
    '12_etl_load_chunk_structure' AS section,
    ordinal_position,
    column_name,
    data_type,
    is_nullable
FROM information_schema.columns
WHERE table_schema = 'etl'
  AND table_name = 'load_chunk'
ORDER BY ordinal_position;


SELECT
    '13_etl_load_chunk_history' AS section,
    to_jsonb(lc) AS load_chunk
FROM etl.load_chunk lc
WHERE to_jsonb(lc)::text ILIKE '%salestable%'
   OR to_jsonb(lc)::text ILIKE '%sales_order%'
ORDER BY to_jsonb(lc)::text DESC
LIMIT 100;


-- ============================================================
-- 14. Безопасный EXPLAIN RAW
-- EXPLAIN без ANALYZE не читает всю таблицу
-- ============================================================

EXPLAIN (
    COSTS,
    VERBOSE,
    SETTINGS,
    FORMAT TEXT
)
SELECT *
FROM raw_ax.salestable;


-- ============================================================
-- 15. Безопасный EXPLAIN DDS
-- ============================================================

EXPLAIN (
    COSTS,
    VERBOSE,
    SETTINGS,
    FORMAT TEXT
)
SELECT *
FROM dds.sales_order;


ROLLBACK;