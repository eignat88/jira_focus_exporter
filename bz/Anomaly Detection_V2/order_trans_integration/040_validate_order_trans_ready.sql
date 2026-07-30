\set ON_ERROR_STOP on
\pset pager off
\timing on

BEGIN READ ONLY;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '10min';
SET LOCAL default_transaction_read_only = on;

\echo '=== 1. Required objects and columns ==='
SELECT
    to_regclass('stage_ax.wmsordertrans_normalized') AS staging_table,
    EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='dds' AND table_name='order_trans'
          AND column_name='rec_id' AND udt_name='int8'
    ) AS dds_rec_id_bigint,
    EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='stage_ax' AND table_name='wmsordertrans_normalized'
          AND column_name='recid_bigint' AND udt_name='int8'
    ) AS stage_recid_bigint;

\echo '=== 2. Staging indexes ==='
SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname='stage_ax' AND tablename='wmsordertrans_normalized'
ORDER BY indexname;

\echo '=== 3. Required chunk plan ==='
EXPLAIN (COSTS, VERBOSE, FORMAT TEXT)
SELECT *
FROM stage_ax.wmsordertrans_normalized
WHERE recid_bigint >= 6000000000
  AND recid_bigint < 6000500000
ORDER BY recid_bigint;

\echo '=== 4. Statistics ==='
SELECT schemaname, relname, n_live_tup, n_dead_tup,
       seq_scan, idx_scan, last_analyze, last_autoanalyze
FROM pg_stat_user_tables
WHERE (schemaname, relname) IN (
    ('raw_ax','wmsordertrans'),
    ('stage_ax','wmsordertrans_normalized'),
    ('dds','order_trans')
)
ORDER BY schemaname, relname;

\echo '=== 5. Sample integrity ==='
SELECT count(*) AS sample_rows,
       count(*) FILTER (WHERE recid_bigint IS NULL) AS null_recid_bigint,
       count(*) FILTER (WHERE source_recid IS NULL OR source_recid='') AS empty_source_recid
FROM stage_ax.wmsordertrans_normalized TABLESAMPLE SYSTEM (0.1);

ROLLBACK;
