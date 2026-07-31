-- =============================================================================
-- RAW -> DDS preflight:
--     raw_ax.salestable -> dds.sales_order
--
-- READ-ONLY ONLY:
--   * no INSERT / UPDATE / DELETE
--   * no CREATE
--   * no ANALYZE
--   * EXPLAIN without ANALYZE
--   * no creation of etl.load_run / etl.load_chunk
--
-- This file is a reference/query catalogue.
-- The Python runner executes equivalent queries and writes CSV output.
-- =============================================================================

BEGIN TRANSACTION READ ONLY;

SET LOCAL application_name = 'diagnose_sales_order_preflight';
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '60s';
SET LOCAL idle_in_transaction_session_timeout = '5min';

-- 01. Tables existence
SELECT
    to_regclass('raw_ax.salestable') AS raw_table,
    to_regclass('dds.sales_order') AS dds_table,
    to_regclass('etl.load_run') AS load_run_table,
    to_regclass('etl.load_chunk') AS load_chunk_table;

-- 02. Table sizes and estimated rows
SELECT
    n.nspname AS schema_name,
    c.relname AS table_name,
    c.reltuples::bigint AS estimated_rows,
    pg_relation_size(c.oid) AS heap_bytes,
    pg_indexes_size(c.oid) AS indexes_bytes,
    pg_total_relation_size(c.oid) AS total_bytes,
    pg_size_pretty(pg_relation_size(c.oid)) AS heap_size,
    pg_size_pretty(pg_indexes_size(c.oid)) AS indexes_size,
    pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size
FROM pg_class c
JOIN pg_namespace n
    ON n.oid = c.relnamespace
WHERE (n.nspname, c.relname) IN (
    ('raw_ax', 'salestable'),
    ('dds', 'sales_order')
)
ORDER BY n.nspname, c.relname;

-- 03. RAW and DDS columns
SELECT
    table_schema,
    table_name,
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
WHERE (table_schema, table_name) IN (
    ('raw_ax', 'salestable'),
    ('dds', 'sales_order')
)
ORDER BY table_schema, table_name, ordinal_position;

-- 04. Potential keys
SELECT
    table_schema,
    table_name,
    column_name,
    data_type,
    udt_name,
    is_nullable
FROM information_schema.columns
WHERE (table_schema, table_name) IN (
    ('raw_ax', 'salestable'),
    ('dds', 'sales_order')
)
AND lower(column_name) IN (
    'recid',
    'recid_bigint',
    'salesid',
    'dataareaid',
    'partition',
    'sales_order_id'
)
ORDER BY table_schema, table_name, column_name;

-- 05. Index definitions
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

-- 06. Detailed RAW index columns
SELECT
    idx.relname AS index_name,
    am.amname AS access_method,
    i.indisvalid AS is_valid,
    i.indisready AS is_ready,
    i.indisunique AS is_unique,
    i.indisprimary AS is_primary,
    ord.ordinality AS column_position,
    COALESCE(
        att.attname,
        pg_get_indexdef(i.indexrelid, ord.ordinality, true)
    ) AS indexed_column_or_expression
FROM pg_index i
JOIN pg_class tbl
    ON tbl.oid = i.indrelid
JOIN pg_namespace ns
    ON ns.oid = tbl.relnamespace
JOIN pg_class idx
    ON idx.oid = i.indexrelid
JOIN pg_am am
    ON am.oid = idx.relam
CROSS JOIN LATERAL
    unnest(i.indkey) WITH ORDINALITY AS ord(attnum, ordinality)
LEFT JOIN pg_attribute att
    ON att.attrelid = tbl.oid
   AND att.attnum = ord.attnum
WHERE ns.nspname = 'raw_ax'
  AND tbl.relname = 'salestable'
ORDER BY idx.relname, ord.ordinality;

-- 07. DDS constraints for idempotency / ON CONFLICT
SELECT
    con.conname AS constraint_name,
    CASE con.contype
        WHEN 'p' THEN 'PRIMARY KEY'
        WHEN 'u' THEN 'UNIQUE'
        WHEN 'f' THEN 'FOREIGN KEY'
        WHEN 'c' THEN 'CHECK'
        WHEN 'x' THEN 'EXCLUSION'
        ELSE con.contype::text
    END AS constraint_type,
    pg_get_constraintdef(con.oid) AS definition
FROM pg_constraint con
JOIN pg_class cls
    ON cls.oid = con.conrelid
JOIN pg_namespace ns
    ON ns.oid = cls.relnamespace
WHERE ns.nspname = 'dds'
  AND cls.relname = 'sales_order'
ORDER BY con.contype, con.conname;

-- 08. Table statistics (estimates, not exact counts)
SELECT
    schemaname,
    relname,
    n_live_tup,
    n_dead_tup,
    last_vacuum,
    last_autovacuum,
    last_analyze,
    last_autoanalyze
FROM pg_stat_user_tables
WHERE (schemaname, relname) IN (
    ('raw_ax', 'salestable'),
    ('dds', 'sales_order')
)
ORDER BY schemaname, relname;

-- 09. Active operations
SELECT
    pid,
    usename,
    application_name,
    state,
    wait_event_type,
    wait_event,
    clock_timestamp() - query_start AS query_duration,
    left(query, 1500) AS query
FROM pg_stat_activity
WHERE pid <> pg_backend_pid()
  AND (
      query ILIKE '%raw_ax.salestable%'
      OR query ILIKE '%dds.sales_order%'
      OR application_name ILIKE '%dds%'
  )
ORDER BY query_start;

-- 10. History lookup, schema-independent JSON search
-- Execute only when etl.load_run exists.
SELECT
    to_jsonb(lr) AS run_data
FROM etl.load_run lr
WHERE to_jsonb(lr)::text ILIKE '%raw_ax%'
  AND (
      to_jsonb(lr)::text ILIKE '%sales_order%'
      OR to_jsonb(lr)::text ILIKE '%salestable%'
  )
ORDER BY lr.run_id DESC
LIMIT 50;

-- 11. RECID sample: safe preliminary quality check.
-- TABLESAMPLE is not a complete validation.
SELECT recid
FROM raw_ax.salestable TABLESAMPLE SYSTEM (0.1)
WHERE recid IS NOT NULL
LIMIT 100;

-- 12. Non-numeric RECID sample, only when RECID is text-like.
SELECT recid
FROM raw_ax.salestable TABLESAMPLE SYSTEM (0.1)
WHERE recid IS NOT NULL
  AND btrim(recid) !~ '^[0-9]+$'
LIMIT 100;

-- 13A. Plan example when RECID is bigint.
-- Replace bounds with values from a sample if needed.
EXPLAIN (COSTS, VERBOSE, SETTINGS, FORMAT TEXT)
SELECT *
FROM raw_ax.salestable
WHERE recid >= 5665000000
  AND recid < 5665500000
ORDER BY recid;

-- 13B. Plan example when RECID is text.
EXPLAIN (COSTS, VERBOSE, SETTINGS, FORMAT TEXT)
SELECT *
FROM raw_ax.salestable
WHERE btrim(recid)::bigint >= 5665000000
  AND btrim(recid)::bigint < 5665500000;

ROLLBACK;
