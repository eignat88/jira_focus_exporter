\set ON_ERROR_STOP on
\pset pager off
\pset format csv
\pset footer off

/*
Read-only diagnostics:
    raw_ax.purchtable -> dds.purchase_order

The script does not execute:
    INSERT / UPDATE / DELETE
    ANALYZE / VACUUM
    CREATE INDEX
    EXPLAIN ANALYZE

The exact RECID check performs a read-only scan of raw_ax.purchtable.
*/

\echo [1/17] Object existence
\o :output_dir/object_existence.csv
SELECT
    to_regclass('raw_ax.purchtable')::text AS source_table,
    to_regclass('dds.purchase_order')::text AS target_table,
    to_regclass('etl.load_run')::text AS load_run_table,
    to_regclass('etl.load_chunk')::text AS load_chunk_table;
\o

\echo [2/17] Source columns
\o :output_dir/source_columns.csv
SELECT ordinal_position, column_name, data_type, udt_name, is_nullable
FROM information_schema.columns
WHERE table_schema = 'raw_ax'
  AND table_name = 'purchtable'
ORDER BY ordinal_position;
\o

\echo [3/17] Candidate mapping columns
\o :output_dir/source_candidate_columns.csv
SELECT ordinal_position, column_name, data_type, udt_name
FROM information_schema.columns
WHERE table_schema = 'raw_ax'
  AND table_name = 'purchtable'
  AND (
         column_name ILIKE '%purch%'
      OR column_name ILIKE '%vend%'
      OR column_name ILIKE '%account%'
      OR column_name ILIKE '%order%'
      OR column_name ILIKE '%date%'
      OR column_name ILIKE '%delivery%'
      OR column_name ILIKE '%currency%'
      OR column_name ILIKE '%status%'
      OR column_name ILIKE '%created%'
      OR column_name ILIKE '%modified%'
      OR column_name ILIKE '%dataarea%'
  )
ORDER BY ordinal_position;
\o

\echo [4/17] Target columns
\o :output_dir/target_columns.csv
SELECT ordinal_position, column_name, data_type, udt_name, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'dds'
  AND table_name = 'purchase_order'
ORDER BY ordinal_position;
\o

\echo [5/17] Relation sizes and estimated rows
\o :output_dir/relation_sizes.csv
SELECT
    n.nspname AS schema_name,
    c.relname AS table_name,
    c.oid::regclass::text AS qualified_name,
    pg_relation_size(c.oid) AS heap_bytes,
    pg_indexes_size(c.oid) AS indexes_bytes,
    pg_total_relation_size(c.oid) AS total_bytes,
    pg_size_pretty(pg_relation_size(c.oid)) AS heap_size,
    pg_size_pretty(pg_indexes_size(c.oid)) AS indexes_size,
    pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size,
    c.reltuples::bigint AS estimated_rows
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.oid IN (
    'raw_ax.purchtable'::regclass,
    'dds.purchase_order'::regclass
)
ORDER BY n.nspname, c.relname;
\o

\echo [6/17] Exact target row count
\o :output_dir/target_row_count.csv
SELECT count(*) AS target_rows_exact
FROM dds.purchase_order;
\o

\echo [7/17] Table statistics
\o :output_dir/table_statistics.csv
SELECT
    schemaname,
    relname,
    n_live_tup,
    n_dead_tup,
    seq_scan,
    seq_tup_read,
    idx_scan,
    idx_tup_fetch,
    last_analyze,
    last_autoanalyze,
    last_vacuum,
    last_autovacuum
FROM pg_stat_user_tables
WHERE schemaname IN ('raw_ax', 'dds')
  AND relname IN ('purchtable', 'purchase_order')
ORDER BY schemaname, relname;
\o

\echo [8/17] Indexes
\o :output_dir/indexes.csv
SELECT schemaname, tablename, indexname, indexdef
FROM pg_indexes
WHERE (schemaname = 'raw_ax' AND tablename = 'purchtable')
   OR (schemaname = 'dds' AND tablename = 'purchase_order')
ORDER BY schemaname, tablename, indexname;
\o

\echo [9/17] Target constraints
\o :output_dir/target_constraints.csv
SELECT
    con.conname,
    con.contype,
    pg_get_constraintdef(con.oid) AS definition
FROM pg_constraint con
WHERE con.conrelid = 'dds.purchase_order'::regclass
ORDER BY con.contype, con.conname;
\o

\echo [10/17] RECID quality and numeric range
\o :output_dir/recid_quality.csv
WITH quality AS (
    SELECT
        count(*) AS total_rows,
        count(*) FILTER (WHERE recid IS NULL) AS null_recid,
        count(*) FILTER (
            WHERE recid IS NOT NULL AND btrim(recid) = ''
        ) AS empty_recid,
        count(*) FILTER (
            WHERE recid IS NOT NULL
              AND btrim(recid) <> ''
              AND btrim(recid) !~ '^[0-9]+$'
        ) AS non_numeric_recid
    FROM raw_ax.purchtable
),
numeric_range AS (
    SELECT
        min(btrim(recid)::numeric) AS min_recid,
        max(btrim(recid)::numeric) AS max_recid,
        count(DISTINCT recid) AS distinct_text_recid
    FROM raw_ax.purchtable
    WHERE recid IS NOT NULL
      AND btrim(recid) ~ '^[0-9]+$'
)
SELECT
    q.total_rows,
    q.null_recid,
    q.empty_recid,
    q.non_numeric_recid,
    r.min_recid,
    r.max_recid,
    r.distinct_text_recid,
    2147483647::bigint AS int4_max,
    CASE
        WHEN r.max_recid IS NULL THEN NULL
        ELSE r.max_recid > 2147483647
    END AS exceeds_int4
FROM quality q
CROSS JOIN numeric_range r;
\o

\echo [11/17] Mapping columns check
\o :output_dir/mapping_columns_check.csv
WITH required(column_name) AS (
    VALUES
        ('recid'),
        ('purchid'),
        ('vendaccount'),
        ('orderdate'),
        ('deliverydate'),
        ('currencycode'),
        ('purchstatus'),
        ('modifieddatetime'),
        ('createddatetime'),
        ('dataareaid'),
        ('recid_bigint')
)
SELECT
    r.column_name,
    c.column_name IS NOT NULL AS exists_in_source,
    c.data_type,
    c.udt_name
FROM required r
LEFT JOIN information_schema.columns c
  ON c.table_schema = 'raw_ax'
 AND c.table_name = 'purchtable'
 AND c.column_name = r.column_name
ORDER BY r.column_name;
\o

\echo [12/17] Safe full-table EXPLAIN
\pset format aligned
\o :output_dir/explain_full_table.txt
EXPLAIN (FORMAT JSON)
SELECT 1
FROM raw_ax.purchtable;
\o
\pset format csv

\echo [13/17] Sessions touching source or target
\o :output_dir/relevant_activity.csv
SELECT
    pid,
    usename,
    application_name,
    state,
    wait_event_type,
    wait_event,
    now() - query_start AS query_duration,
    now() - xact_start AS transaction_duration,
    left(query, 1000) AS query
FROM pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid()
  AND (
         query ILIKE '%raw_ax.purchtable%'
      OR query ILIKE '%dds.purchase_order%'
      OR query ILIKE '%purchase_order%'
  )
ORDER BY query_start NULLS LAST;
\o

\echo [14/17] Long transactions
\o :output_dir/long_transactions.csv
SELECT
    pid,
    usename,
    application_name,
    state,
    now() - xact_start AS transaction_duration,
    wait_event_type,
    wait_event,
    left(query, 1000) AS query
FROM pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid()
  AND xact_start IS NOT NULL
  AND now() - xact_start >= interval '15 minutes'
ORDER BY xact_start;
\o

\echo [15/17] VACUUM and CREATE INDEX progress
\o :output_dir/pg_stat_progress_create_index.csv
SELECT *
FROM pg_stat_progress_create_index;
\o

\o :output_dir/pg_stat_progress_vacuum.csv
SELECT *
FROM pg_stat_progress_vacuum;
\o

\echo [16/17] WAL and checkpointer
\o :output_dir/pg_stat_wal.csv
SELECT wal_records, wal_fpi, wal_bytes, wal_buffers_full, stats_reset
FROM pg_stat_wal;
\o

\o :output_dir/pg_stat_checkpointer.csv
SELECT *
FROM pg_stat_checkpointer;
\o

\echo [17/17] ETL history
\o :output_dir/etl_load_run.csv
SELECT *
FROM etl.load_run
WHERE target_schema = 'dds'
  AND target_table = 'purchase_order'
ORDER BY started_at DESC
LIMIT 20;
\o

\o :output_dir/etl_load_chunk.csv
SELECT lc.*
FROM etl.load_chunk lc
WHERE lc.run_id IN (
    SELECT lr.run_id
    FROM etl.load_run lr
    WHERE lr.target_schema = 'dds'
      AND lr.target_table = 'purchase_order'
)
LIMIT 500;
\o

\echo Diagnostics completed.
