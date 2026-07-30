\set ON_ERROR_STOP on
\pset pager off
\timing on

BEGIN READ ONLY;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30min';
SET LOCAL default_transaction_read_only = on;

\echo '=== 1. Objects ==='
SELECT
    to_regclass('raw_ax.wmsordertrans') AS source_table,
    to_regclass('dds.order_trans') AS target_table,
    to_regclass('stage_ax.wmsordertrans_normalized') AS staging_table;

\echo '=== 2. DDS columns ==='
SELECT ordinal_position, column_name, data_type, udt_name, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'dds' AND table_name = 'order_trans'
ORDER BY ordinal_position;

\echo '=== 3. DDS constraints ==='
SELECT c.conname,
       c.contype,
       pg_get_constraintdef(c.oid) AS definition,
       c.convalidated,
       c.condeferrable,
       c.condeferred
FROM pg_constraint c
WHERE c.conrelid = 'dds.order_trans'::regclass
ORDER BY c.conname;

\echo '=== 4. DDS indexes ==='
SELECT i.relname AS index_name,
       ix.indisprimary,
       ix.indisunique,
       ix.indisvalid,
       ix.indisready,
       pg_size_pretty(pg_relation_size(i.oid)) AS index_size,
       pg_get_indexdef(i.oid) AS index_definition
FROM pg_class t
JOIN pg_namespace n ON n.oid = t.relnamespace
JOIN pg_index ix ON ix.indrelid = t.oid
JOIN pg_class i ON i.oid = ix.indexrelid
WHERE n.nspname = 'dds' AND t.relname = 'order_trans'
ORDER BY i.relname;

\echo '=== 5. RAW recid type ==='
SELECT column_name, data_type, udt_name, is_nullable
FROM information_schema.columns
WHERE table_schema = 'raw_ax'
  AND table_name = 'wmsordertrans'
  AND column_name = 'recid';

\echo '=== 6. RAW relevant columns ==='
SELECT ordinal_position, column_name, data_type, udt_name, is_nullable
FROM information_schema.columns
WHERE table_schema = 'raw_ax'
  AND table_name = 'wmsordertrans'
  AND column_name IN (
      'recid','orderid','inventtransid','itemid','inventdimid','qty',
      'wms_givenqty','wms_defectqty','routeid','palletidpicked',
      'modifieddatetime','createddatetime','dataareaid'
  )
ORDER BY ordinal_position;

\echo '=== 7. Sample quality check ==='
WITH sample AS (
    SELECT recid
    FROM raw_ax.wmsordertrans TABLESAMPLE SYSTEM (0.1)
)
SELECT count(*) AS sample_rows,
       count(*) FILTER (WHERE recid IS NULL) AS null_recid,
       count(*) FILTER (WHERE recid IS NOT NULL AND btrim(recid) = '') AS empty_recid,
       count(*) FILTER (
           WHERE recid IS NOT NULL
             AND btrim(recid) <> ''
             AND btrim(recid) !~ '^[0-9]+$'
       ) AS non_numeric_recid,
       min(length(btrim(recid))) FILTER (WHERE recid IS NOT NULL) AS min_length_sample,
       max(length(btrim(recid))) FILTER (WHERE recid IS NOT NULL) AS max_length_sample
FROM sample;

\echo '=== 8. Exact quality check plan only ==='
EXPLAIN (COSTS, VERBOSE, FORMAT TEXT)
SELECT count(*) FILTER (WHERE recid IS NULL),
       count(*) FILTER (WHERE recid IS NOT NULL AND btrim(recid) = ''),
       count(*) FILTER (
           WHERE recid IS NOT NULL
             AND btrim(recid) <> ''
             AND btrim(recid) !~ '^[0-9]+$'
       ),
       min(length(btrim(recid))),
       max(length(btrim(recid)))
FROM raw_ax.wmsordertrans;

\echo '=== 9. Current numeric chunk plan (expected to be bad) ==='
EXPLAIN (COSTS, VERBOSE, FORMAT TEXT)
SELECT recid
FROM raw_ax.wmsordertrans
WHERE btrim(recid)::bigint > 1000000000
ORDER BY btrim(recid)::bigint
LIMIT 100000;

\echo '=== 10. Text-key chunk plan (must use idx_wmsordertrans_recid) ==='
EXPLAIN (COSTS, VERBOSE, FORMAT TEXT)
SELECT recid
FROM raw_ax.wmsordertrans
WHERE recid > '0000000000'
ORDER BY recid
LIMIT 100000;

\echo '=== 11. Sizes ==='
SELECT pg_size_pretty(pg_relation_size('raw_ax.wmsordertrans')) AS raw_heap,
       pg_size_pretty(pg_indexes_size('raw_ax.wmsordertrans')) AS raw_indexes,
       pg_size_pretty(pg_total_relation_size('raw_ax.wmsordertrans')) AS raw_total,
       pg_size_pretty(pg_relation_size('dds.order_trans')) AS dds_heap,
       pg_size_pretty(pg_indexes_size('dds.order_trans')) AS dds_indexes,
       pg_size_pretty(pg_total_relation_size('dds.order_trans')) AS dds_total;

\echo '=== 12. Active operations ==='
SELECT pid, application_name, state, wait_event_type, wait_event,
       now() - query_start AS query_runtime,
       now() - xact_start AS transaction_runtime,
       left(regexp_replace(query, E'[\n\r\t]+', ' ', 'g'), 500) AS query
FROM pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid()
ORDER BY query_start NULLS LAST;

SELECT * FROM pg_stat_progress_create_index;
SELECT * FROM pg_stat_progress_vacuum;

ROLLBACK;
