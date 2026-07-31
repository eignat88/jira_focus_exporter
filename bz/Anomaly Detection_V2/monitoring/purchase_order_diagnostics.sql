/*
purchase_order_diagnostics.sql
Read-only diagnostics for:
    raw_ax.purchtable -> dds.purchase_order

Expected variables supplied by psql:
    :output_dir

The script does not run:
    ANALYZE
    INSERT
    UPDATE
    DELETE
    CREATE INDEX
    VACUUM
    EXPLAIN ANALYZE
*/

\set ON_ERROR_STOP on
\pset pager off

\echo [1/14] Table existence
\copy (
    SELECT
        to_regclass('raw_ax.purchtable')::text AS raw_table,
        to_regclass('dds.purchase_order')::text AS dds_table
) TO :'output_dir/table_existence.csv' WITH (FORMAT CSV, HEADER, ENCODING 'UTF8');

\echo [2/14] Source columns
\copy (
    SELECT
        ordinal_position,
        column_name,
        data_type,
        udt_name,
        is_nullable
    FROM information_schema.columns
    WHERE table_schema = 'raw_ax'
      AND table_name = 'purchtable'
    ORDER BY ordinal_position
) TO :'output_dir/source_columns.csv' WITH (FORMAT CSV, HEADER, ENCODING 'UTF8');

\echo [3/14] Candidate source columns
\copy (
    SELECT
        ordinal_position,
        column_name,
        data_type,
        udt_name
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
    ORDER BY ordinal_position
) TO :'output_dir/source_candidate_columns.csv' WITH (FORMAT CSV, HEADER, ENCODING 'UTF8');

\echo [4/14] Target columns
\copy (
    SELECT
        ordinal_position,
        column_name,
        data_type,
        udt_name,
        is_nullable,
        column_default
    FROM information_schema.columns
    WHERE table_schema = 'dds'
      AND table_name = 'purchase_order'
    ORDER BY ordinal_position
) TO :'output_dir/target_columns.csv' WITH (FORMAT CSV, HEADER, ENCODING 'UTF8');

\echo [5/14] Relation sizes
\copy (
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
    ORDER BY n.nspname, c.relname
) TO :'output_dir/relation_sizes.csv' WITH (FORMAT CSV, HEADER, ENCODING 'UTF8');

\echo [6/14] Table statistics
\copy (
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
    ORDER BY schemaname, relname
) TO :'output_dir/table_statistics.csv' WITH (FORMAT CSV, HEADER, ENCODING 'UTF8');

\echo [7/14] Source and target indexes
\copy (
    SELECT
        schemaname,
        tablename,
        indexname,
        indexdef
    FROM pg_indexes
    WHERE (schemaname = 'raw_ax' AND tablename = 'purchtable')
       OR (schemaname = 'dds' AND tablename = 'purchase_order')
    ORDER BY schemaname, tablename, indexname
) TO :'output_dir/indexes.csv' WITH (FORMAT CSV, HEADER, ENCODING 'UTF8');

\echo [8/14] Target constraints
\copy (
    SELECT
        con.conname,
        con.contype,
        pg_get_constraintdef(con.oid) AS definition
    FROM pg_constraint con
    WHERE con.conrelid = 'dds.purchase_order'::regclass
    ORDER BY con.contype, con.conname
) TO :'output_dir/target_constraints.csv' WITH (FORMAT CSV, HEADER, ENCODING 'UTF8');

\echo [9/14] RECID quality and range
\copy (
    WITH quality AS (
        SELECT
            count(*) AS total_rows,
            count(*) FILTER (WHERE recid IS NULL) AS null_recid,
            count(*) FILTER (WHERE recid IS NOT NULL AND btrim(recid) = '') AS empty_recid,
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
        CASE
            WHEN r.max_recid IS NULL THEN NULL
            ELSE r.max_recid > 2147483647
        END AS exceeds_int4
    FROM quality q
    CROSS JOIN numeric_range r
) TO :'output_dir/recid_quality.csv' WITH (FORMAT CSV, HEADER, ENCODING 'UTF8');

\echo [10/14] Required mapping columns
\copy (
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
        (c.column_name IS NOT NULL) AS exists_in_source,
        c.data_type,
        c.udt_name
    FROM required r
    LEFT JOIN information_schema.columns c
      ON c.table_schema = 'raw_ax'
     AND c.table_name = 'purchtable'
     AND c.column_name = r.column_name
    ORDER BY r.column_name
) TO :'output_dir/mapping_columns_check.csv' WITH (FORMAT CSV, HEADER, ENCODING 'UTF8');

\echo [11/14] Safe EXPLAIN without ANALYZE
\o :output_dir/explain_full_table.txt
EXPLAIN (FORMAT JSON)
SELECT 1
FROM raw_ax.purchtable;
\o

\echo [12/14] Activity and progress
\copy (
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
    ORDER BY query_start NULLS LAST
) TO :'output_dir/pg_stat_activity.csv' WITH (FORMAT CSV, HEADER, ENCODING 'UTF8');

\copy (
    SELECT *
    FROM pg_stat_progress_create_index
) TO :'output_dir/pg_stat_progress_create_index.csv' WITH (FORMAT CSV, HEADER, ENCODING 'UTF8');

\copy (
    SELECT *
    FROM pg_stat_progress_vacuum
) TO :'output_dir/pg_stat_progress_vacuum.csv' WITH (FORMAT CSV, HEADER, ENCODING 'UTF8');

\echo [13/14] WAL and checkpointer
\copy (
    SELECT
        wal_records,
        wal_fpi,
        wal_bytes,
        wal_buffers_full,
        stats_reset
    FROM pg_stat_wal
) TO :'output_dir/pg_stat_wal.csv' WITH (FORMAT CSV, HEADER, ENCODING 'UTF8');

\copy (
    SELECT *
    FROM pg_stat_checkpointer
) TO :'output_dir/pg_stat_checkpointer.csv' WITH (FORMAT CSV, HEADER, ENCODING 'UTF8');

\echo [14/14] ETL history
\copy (
    SELECT *
    FROM etl.load_run
    WHERE target_schema = 'dds'
      AND target_table = 'purchase_order'
    ORDER BY started_at DESC
    LIMIT 20
) TO :'output_dir/etl_load_run.csv' WITH (FORMAT CSV, HEADER, ENCODING 'UTF8');

\copy (
    SELECT lc.*
    FROM etl.load_chunk lc
    WHERE lc.run_id IN (
        SELECT lr.run_id
        FROM etl.load_run lr
        WHERE lr.target_schema = 'dds'
          AND lr.target_table = 'purchase_order'
    )
    LIMIT 500
) TO :'output_dir/etl_load_chunk.csv' WITH (FORMAT CSV, HEADER, ENCODING 'UTF8');

\echo Diagnostics completed.
