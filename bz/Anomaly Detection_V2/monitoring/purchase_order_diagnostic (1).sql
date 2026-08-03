/*
Read-only diagnostics for RAW -> DDS stage purchase_order.

Source: raw_ax.purchtable
Target: dds.purchase_order

The result is one normalized data set suitable for CSV export.
No INSERT / UPDATE / DELETE / ANALYZE / DDL is executed.
*/

SET default_transaction_read_only = on;
SET statement_timeout = '60s';
SET lock_timeout = '5s';

WITH
params AS (
    SELECT
        clock_timestamp() AS diagnostic_time,
        to_regclass('raw_ax.purchtable') AS source_oid,
        to_regclass('dds.purchase_order') AS target_oid
),
required_columns(column_name) AS (
    VALUES ('vendaccount'), ('orderdate')
),
required_check AS (
    SELECT
        r.column_name,
        c.column_name AS actual_column,
        c.data_type,
        c.ordinal_position
    FROM required_columns r
    LEFT JOIN information_schema.columns c
      ON c.table_schema = 'raw_ax'
     AND c.table_name = 'purchtable'
     AND lower(c.column_name) = lower(r.column_name)
),
summary AS (
    SELECT
        CASE
            WHEN p.source_oid IS NULL THEN 'BLOCKED'
            WHEN p.target_oid IS NULL THEN 'BLOCKED'
            WHEN EXISTS (SELECT 1 FROM required_check WHERE actual_column IS NULL) THEN 'BLOCKED'
            WHEN NOT EXISTS (
                SELECT 1
                FROM pg_constraint con
                WHERE con.conrelid = p.target_oid
                  AND con.contype IN ('p', 'u')
            ) THEN 'BLOCKED'
            ELSE 'READY_FOR_MAPPING_REVIEW'
        END AS overall_status
    FROM params p
),
diagnostics AS (
    SELECT 1 AS section_order, 1 AS item_order, 'SUMMARY'::text AS section,
           'purchase_order'::text AS object_name, 'overall_status'::text AS metric,
           s.overall_status::text AS status, s.overall_status::text AS value,
           'READY_FOR_MAPPING_REVIEW means catalog checks passed; preflight must still validate the complete mapping SQL.'::text AS details
    FROM summary s

    UNION ALL
    SELECT 1, 2, 'SUMMARY', 'connection', 'postgres_version', 'INFO', version(),
           jsonb_build_object(
               'database', current_database(),
               'user', current_user,
               'server_address', inet_server_addr(),
               'server_port', inet_server_port()
           )::text
    FROM params

    UNION ALL
    SELECT 1, 3, 'SUMMARY', 'connection', 'data_directory', 'INFO',
           current_setting('data_directory'),
           jsonb_build_object(
               'default_tablespace', current_setting('default_tablespace'),
               'database_tablespace_oid', d.dattablespace,
               'database_tablespace_size_bytes', pg_tablespace_size(d.dattablespace),
               'database_tablespace_size_pretty', pg_size_pretty(pg_tablespace_size(d.dattablespace))
           )::text
    FROM pg_database d
    WHERE d.datname = current_database()

    UNION ALL
    SELECT 2, 1, 'TABLE_EXISTENCE', 'raw_ax.purchtable', 'table_exists',
           CASE WHEN p.source_oid IS NULL THEN 'MISSING' ELSE 'FOUND' END,
           COALESCE(p.source_oid::text, ''), ''
    FROM params p

    UNION ALL
    SELECT 2, 2, 'TABLE_EXISTENCE', 'dds.purchase_order', 'table_exists',
           CASE WHEN p.target_oid IS NULL THEN 'MISSING' ELSE 'FOUND' END,
           COALESCE(p.target_oid::text, ''), ''
    FROM params p

    UNION ALL
    SELECT 3, c.ordinal_position, 'SOURCE_COLUMNS', 'raw_ax.purchtable', c.column_name,
           'INFO', c.data_type,
           jsonb_build_object(
               'ordinal_position', c.ordinal_position,
               'udt_name', c.udt_name,
               'is_nullable', c.is_nullable,
               'column_default', c.column_default
           )::text
    FROM information_schema.columns c
    WHERE c.table_schema = 'raw_ax'
      AND c.table_name = 'purchtable'

    UNION ALL
    SELECT 4, c.ordinal_position, 'SOURCE_CANDIDATE_COLUMNS', 'raw_ax.purchtable', c.column_name,
           'CANDIDATE', c.data_type,
           jsonb_build_object(
               'ordinal_position', c.ordinal_position,
               'is_nullable', c.is_nullable
           )::text
    FROM information_schema.columns c
    WHERE c.table_schema = 'raw_ax'
      AND c.table_name = 'purchtable'
      AND (
           c.column_name ILIKE '%vend%'
        OR c.column_name ILIKE '%account%'
        OR c.column_name ILIKE '%order%'
        OR c.column_name ILIKE '%date%'
        OR c.column_name ILIKE '%created%'
        OR c.column_name ILIKE '%delivery%'
      )

    UNION ALL
    SELECT 5, row_number() OVER (ORDER BY rc.column_name)::integer,
           'REQUIRED_SOURCE_COLUMNS', 'raw_ax.purchtable', rc.column_name,
           CASE WHEN rc.actual_column IS NULL THEN 'MISSING' ELSE 'FOUND' END,
           COALESCE(rc.data_type, ''),
           jsonb_build_object('ordinal_position', rc.ordinal_position)::text
    FROM required_check rc

    UNION ALL
    SELECT 6, c.ordinal_position, 'TARGET_COLUMNS', 'dds.purchase_order', c.column_name,
           'INFO', c.data_type,
           jsonb_build_object(
               'ordinal_position', c.ordinal_position,
               'udt_name', c.udt_name,
               'is_nullable', c.is_nullable,
               'column_default', c.column_default
           )::text
    FROM information_schema.columns c
    WHERE c.table_schema = 'dds'
      AND c.table_name = 'purchase_order'

    UNION ALL
    SELECT 7, row_number() OVER (ORDER BY con.contype, con.conname)::integer,
           'TARGET_CONSTRAINTS', 'dds.purchase_order', con.conname,
           CASE con.contype WHEN 'p' THEN 'PRIMARY_KEY' WHEN 'u' THEN 'UNIQUE' ELSE con.contype::text END,
           pg_get_constraintdef(con.oid), ''
    FROM pg_constraint con
    WHERE con.conrelid = to_regclass('dds.purchase_order')
      AND con.contype IN ('p', 'u')

    UNION ALL
    SELECT 8, row_number() OVER (ORDER BY i.schemaname, i.tablename, i.indexname)::integer,
           'INDEXES', i.schemaname || '.' || i.tablename, i.indexname,
           'INFO', i.indexdef, ''
    FROM pg_indexes i
    WHERE (i.schemaname = 'raw_ax' AND i.tablename = 'purchtable')
       OR (i.schemaname = 'dds' AND i.tablename = 'purchase_order')

    UNION ALL
    SELECT 9, row_number() OVER (ORDER BY st.schemaname, st.relname)::integer,
           'TABLE_STATISTICS', st.schemaname || '.' || st.relname, 'estimated_rows',
           CASE WHEN st.last_analyze IS NULL AND st.last_autoanalyze IS NULL THEN 'WARNING_NO_ANALYZE' ELSE 'INFO' END,
           st.n_live_tup::text,
           jsonb_build_object(
               'n_live_tup_is_estimate', true,
               'n_dead_tup_estimate', st.n_dead_tup,
               'last_analyze', st.last_analyze,
               'last_autoanalyze', st.last_autoanalyze,
               'last_vacuum', st.last_vacuum,
               'last_autovacuum', st.last_autovacuum
           )::text
    FROM pg_stat_user_tables st
    WHERE (st.schemaname = 'raw_ax' AND st.relname = 'purchtable')
       OR (st.schemaname = 'dds' AND st.relname = 'purchase_order')

    UNION ALL
    SELECT 10, row_number() OVER (ORDER BY v.table_name)::integer,
           'TABLE_SIZES', v.table_name, 'total_size', 'INFO',
           pg_size_pretty(pg_total_relation_size(v.table_oid)),
           jsonb_build_object(
               'heap_bytes', pg_relation_size(v.table_oid),
               'heap_pretty', pg_size_pretty(pg_relation_size(v.table_oid)),
               'indexes_bytes', pg_indexes_size(v.table_oid),
               'indexes_pretty', pg_size_pretty(pg_indexes_size(v.table_oid)),
               'total_bytes', pg_total_relation_size(v.table_oid)
           )::text
    FROM (
        VALUES
            ('raw_ax.purchtable'::text, to_regclass('raw_ax.purchtable')),
            ('dds.purchase_order'::text, to_regclass('dds.purchase_order'))
    ) AS v(table_name, table_oid)
    WHERE v.table_oid IS NOT NULL

    UNION ALL
    SELECT 11, row_number() OVER (ORDER BY a.query_start, a.pid)::integer,
           'ACTIVE_OPERATIONS', COALESCE(a.application_name, ''), a.pid::text,
           CASE WHEN a.wait_event_type IS NULL THEN 'ACTIVE' ELSE 'WAITING' END,
           a.state,
           jsonb_build_object(
               'user', a.usename,
               'wait_event_type', a.wait_event_type,
               'wait_event', a.wait_event,
               'query_duration', clock_timestamp() - a.query_start,
               'query', left(a.query, 500)
           )::text
    FROM pg_stat_activity a
    WHERE a.pid <> pg_backend_pid()
      AND a.state <> 'idle'
      AND (a.query ILIKE '%purchtable%' OR a.query ILIKE '%purchase_order%')

    UNION ALL
    SELECT 12, row_number() OVER (ORDER BY l.granted, a.query_start, a.pid)::integer,
           'TABLE_LOCKS', COALESCE(a.application_name, ''), a.pid::text,
           CASE WHEN l.granted THEN 'GRANTED' ELSE 'WAITING' END,
           l.mode,
           jsonb_build_object(
               'user', a.usename,
               'state', a.state,
               'relation', l.relation::regclass::text,
               'query_duration', clock_timestamp() - a.query_start,
               'query', left(a.query, 500)
           )::text
    FROM pg_locks l
    JOIN pg_stat_activity a ON a.pid = l.pid
    WHERE l.relation = ANY (ARRAY[
        to_regclass('raw_ax.purchtable'),
        to_regclass('dds.purchase_order')
    ]::oid[])

    UNION ALL
    SELECT 13, 1, 'WAL_STATISTICS', 'pg_stat_wal', 'accumulated_wal', 'INFO',
           pg_size_pretty(w.wal_bytes),
           jsonb_build_object(
               'wal_records', w.wal_records,
               'wal_bytes', w.wal_bytes,
               'wal_buffers_full', w.wal_buffers_full,
               'stats_reset', w.stats_reset,
               'note', 'Accumulated counter since stats_reset; not the current pg_wal directory size.'
           )::text
    FROM pg_stat_wal w
)
SELECT
    to_char(p.diagnostic_time, 'YYYY-MM-DD HH24:MI:SS.MS TZH:TZM') AS diagnostic_time,
    d.section_order,
    d.section,
    d.object_name,
    d.metric,
    d.status,
    d.value,
    d.details
FROM diagnostics d
CROSS JOIN params p
ORDER BY d.section_order, d.item_order, d.object_name, d.metric;

