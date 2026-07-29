/*
Purpose:
  Read-only diagnostics for RAW -> DDS stage picking_route.

Objects:
  source: raw_ax.wmspickingroute
  target: dds.picking_route

Safety:
  - no INSERT / UPDATE / DELETE / TRUNCATE
  - no CREATE / ALTER / DROP
  - no VACUUM / ANALYZE
  - no COUNT(*)
  - EXPLAIN only, without ANALYZE
*/

BEGIN TRANSACTION READ ONLY;

SET LOCAL statement_timeout = '30s';
SET LOCAL lock_timeout = '3s';
SET LOCAL idle_in_transaction_session_timeout = '60s';

-- ============================================================================
-- 1. PostgreSQL and current database
-- ============================================================================
SELECT
    now() AS collected_at,
    version() AS postgres_version,
    current_database() AS database_name,
    current_user AS database_user,
    current_setting('data_directory') AS data_directory;

-- ============================================================================
-- 2. Existence and physical size of source/target
-- ============================================================================
SELECT
    n.nspname AS schema_name,
    c.relname AS table_name,
    c.relkind,
    c.reltuples::bigint AS pg_class_estimated_rows,
    COALESCE(s.n_live_tup, 0)::bigint AS n_live_tup,
    COALESCE(s.n_dead_tup, 0)::bigint AS n_dead_tup,
    s.last_analyze,
    s.last_autoanalyze,
    s.last_vacuum,
    s.last_autovacuum,
    pg_relation_size(c.oid) AS heap_bytes,
    pg_indexes_size(c.oid) AS indexes_bytes,
    pg_total_relation_size(c.oid) AS total_bytes,
    pg_size_pretty(pg_relation_size(c.oid)) AS heap_size,
    pg_size_pretty(pg_indexes_size(c.oid)) AS indexes_size,
    pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size
FROM pg_class c
JOIN pg_namespace n
  ON n.oid = c.relnamespace
LEFT JOIN pg_stat_user_tables s
  ON s.relid = c.oid
WHERE (n.nspname, c.relname) IN (
    ('raw_ax', 'wmspickingroute'),
    ('dds', 'picking_route')
)
ORDER BY n.nspname, c.relname;

-- ============================================================================
-- 3. Source and target columns
-- ============================================================================
SELECT
    n.nspname AS schema_name,
    c.relname AS table_name,
    a.attnum AS ordinal_position,
    a.attname AS column_name,
    format_type(a.atttypid, a.atttypmod) AS data_type,
    a.attnotnull AS not_null,
    pg_get_expr(ad.adbin, ad.adrelid) AS default_expression
FROM pg_attribute a
JOIN pg_class c
  ON c.oid = a.attrelid
JOIN pg_namespace n
  ON n.oid = c.relnamespace
LEFT JOIN pg_attrdef ad
  ON ad.adrelid = a.attrelid
 AND ad.adnum = a.attnum
WHERE (n.nspname, c.relname) IN (
    ('raw_ax', 'wmspickingroute'),
    ('dds', 'picking_route')
)
  AND a.attnum > 0
  AND NOT a.attisdropped
ORDER BY n.nspname, c.relname, a.attnum;

-- ============================================================================
-- 4. Key columns required by YAML/preflight
-- ============================================================================
SELECT
    requested_object,
    requested_column,
    actual_type,
    column_exists
FROM (
    SELECT
        'raw_ax.wmspickingroute'::text AS requested_object,
        x.column_name AS requested_column,
        format_type(a.atttypid, a.atttypmod) AS actual_type,
        a.attname IS NOT NULL AS column_exists
    FROM (VALUES ('recid'), ('recid_bigint')) AS x(column_name)
    LEFT JOIN pg_class c
      ON c.oid = to_regclass('raw_ax.wmspickingroute')
    LEFT JOIN pg_attribute a
      ON a.attrelid = c.oid
     AND a.attname = x.column_name
     AND a.attnum > 0
     AND NOT a.attisdropped

    UNION ALL

    SELECT
        'dds.picking_route',
        x.column_name,
        format_type(a.atttypid, a.atttypmod),
        a.attname IS NOT NULL
    FROM (VALUES
        ('rec_id'),
        ('route_id'),
        ('picking_route_id'),
        ('route_code')
    ) AS x(column_name)
    LEFT JOIN pg_class c
      ON c.oid = to_regclass('dds.picking_route')
    LEFT JOIN pg_attribute a
      ON a.attrelid = c.oid
     AND a.attname = x.column_name
     AND a.attnum > 0
     AND NOT a.attisdropped
) q
ORDER BY requested_object, requested_column;

-- ============================================================================
-- 5. All indexes and ordered index key expressions
-- ============================================================================
SELECT
    n.nspname AS schema_name,
    tbl.relname AS table_name,
    idx.relname AS index_name,
    am.amname AS access_method,
    i.indisprimary,
    i.indisunique,
    i.indisvalid,
    i.indisready,
    i.indislive,
    ARRAY(
        SELECT pg_get_indexdef(i.indexrelid, k, true)
        FROM generate_series(1, i.indnkeyatts) AS k
        ORDER BY k
    ) AS key_expressions,
    pg_get_expr(i.indpred, i.indrelid) AS predicate,
    pg_get_indexdef(i.indexrelid) AS index_definition
FROM pg_index i
JOIN pg_class tbl
  ON tbl.oid = i.indrelid
JOIN pg_namespace n
  ON n.oid = tbl.relnamespace
JOIN pg_class idx
  ON idx.oid = i.indexrelid
JOIN pg_am am
  ON am.oid = idx.relam
WHERE (n.nspname, tbl.relname) IN (
    ('raw_ax', 'wmspickingroute'),
    ('dds', 'picking_route')
)
ORDER BY n.nspname, tbl.relname, i.indisprimary DESC, i.indisunique DESC, idx.relname;

-- ============================================================================
-- 6. Constraints and columns covered by each constraint
-- ============================================================================
SELECT
    n.nspname AS schema_name,
    c.relname AS table_name,
    con.conname AS constraint_name,
    con.contype AS constraint_type,
    con.convalidated,
    ARRAY(
        SELECT a.attname
        FROM unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
        JOIN pg_attribute a
          ON a.attrelid = con.conrelid
         AND a.attnum = k.attnum
        ORDER BY k.ord
    ) AS constraint_columns,
    pg_get_constraintdef(con.oid, true) AS definition
FROM pg_constraint con
JOIN pg_class c
  ON c.oid = con.conrelid
JOIN pg_namespace n
  ON n.oid = c.relnamespace
WHERE (n.nspname, c.relname) IN (
    ('raw_ax', 'wmspickingroute'),
    ('dds', 'picking_route')
)
ORDER BY n.nspname, c.relname, con.contype, con.conname;

-- ============================================================================
-- 7. Is target conflict key route_id valid for ON CONFLICT?
--    A valid unique index or PK/UNIQUE constraint is sufficient.
-- ============================================================================
SELECT
    idx.relname AS index_name,
    i.indisprimary,
    i.indisunique,
    i.indisvalid,
    i.indisready,
    ARRAY(
        SELECT pg_get_indexdef(i.indexrelid, k, true)
        FROM generate_series(1, i.indnkeyatts) AS k
        ORDER BY k
    ) AS key_expressions,
    pg_get_indexdef(i.indexrelid) AS index_definition
FROM pg_index i
JOIN pg_class tbl
  ON tbl.oid = i.indrelid
JOIN pg_class idx
  ON idx.oid = i.indexrelid
WHERE i.indrelid = to_regclass('dds.picking_route')
  AND i.indisunique
ORDER BY i.indisprimary DESC, idx.relname;

-- ============================================================================
-- 8. Plan using current physical source key recid (text)
--    This is EXPLAIN only. No rows are read.
-- ============================================================================
EXPLAIN (FORMAT TEXT, COSTS TRUE, VERBOSE TRUE)
SELECT recid
FROM raw_ax.wmspickingroute
WHERE recid > ''
ORDER BY recid
LIMIT 100000;

-- ============================================================================
-- 9. Plan for the current bigint_text expression
--    This checks whether trim(recid)::bigint can use an index.
--    EXPLAIN only; no execution.
-- ============================================================================
EXPLAIN (FORMAT TEXT, COSTS TRUE, VERBOSE TRUE)
SELECT recid
FROM raw_ax.wmspickingroute
WHERE NULLIF(BTRIM(recid), '')::bigint > 0
ORDER BY NULLIF(BTRIM(recid), '')::bigint
LIMIT 100000;

-- ============================================================================
-- 10. Active sessions and long transactions
-- ============================================================================
SELECT
    pid,
    usename,
    application_name,
    client_addr,
    state,
    wait_event_type,
    wait_event,
    backend_xid,
    backend_xmin,
    xact_start,
    now() - xact_start AS transaction_age,
    query_start,
    now() - query_start AS query_age,
    LEFT(query, 2000) AS query
FROM pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid()
ORDER BY xact_start NULLS LAST, query_start;

SELECT
    pid,
    usename,
    application_name,
    state,
    wait_event_type,
    wait_event,
    xact_start,
    now() - xact_start AS transaction_age,
    LEFT(query, 2000) AS query
FROM pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid()
  AND xact_start IS NOT NULL
  AND now() - xact_start > interval '5 minutes'
ORDER BY xact_start;

-- ============================================================================
-- 11. Locks on source and target
-- ============================================================================
SELECT
    l.pid,
    n.nspname AS schema_name,
    c.relname AS table_name,
    l.mode,
    l.granted,
    a.state,
    a.wait_event_type,
    a.wait_event,
    LEFT(a.query, 1500) AS query
FROM pg_locks l
JOIN pg_class c
  ON c.oid = l.relation
JOIN pg_namespace n
  ON n.oid = c.relnamespace
LEFT JOIN pg_stat_activity a
  ON a.pid = l.pid
WHERE (n.nspname, c.relname) IN (
    ('raw_ax', 'wmspickingroute'),
    ('dds', 'picking_route')
)
ORDER BY n.nspname, c.relname, l.granted, l.pid;

-- ============================================================================
-- 12. Vacuum and CREATE INDEX progress
-- ============================================================================
SELECT
    pid,
    datname,
    relid::regclass AS relation,
    phase,
    heap_blks_total,
    heap_blks_scanned,
    heap_blks_vacuumed,
    index_vacuum_count,
    num_dead_item_ids
FROM pg_stat_progress_vacuum
ORDER BY pid;

SELECT
    pid,
    datname,
    relid::regclass AS relation,
    index_relid::regclass AS index_relation,
    command,
    phase,
    blocks_total,
    blocks_done,
    tuples_total,
    tuples_done
FROM pg_stat_progress_create_index
ORDER BY pid;

-- ============================================================================
-- 13. WAL and checkpointer statistics
--    wal_bytes is cumulative since stats_reset, not current pg_wal directory size.
-- ============================================================================
SELECT
    wal_records,
    wal_fpi,
    wal_bytes,
    wal_buffers_full,
    stats_reset
FROM pg_stat_wal;

SELECT *
FROM pg_stat_checkpointer;

-- ============================================================================
-- 14. Compact interpretation matrix
-- ============================================================================
SELECT *
FROM (
    VALUES
        (
            'source_recid_bigint_exists',
            EXISTS (
                SELECT 1
                FROM pg_attribute
                WHERE attrelid = to_regclass('raw_ax.wmspickingroute')
                  AND attname = 'recid_bigint'
                  AND attnum > 0
                  AND NOT attisdropped
            ),
            'Required only if YAML/preflight uses recid_bigint'
        ),
        (
            'target_route_id_exists',
            EXISTS (
                SELECT 1
                FROM pg_attribute
                WHERE attrelid = to_regclass('dds.picking_route')
                  AND attname = 'route_id'
                  AND attnum > 0
                  AND NOT attisdropped
            ),
            'Expected target conflict/key column'
        ),
        (
            'target_rec_id_exists',
            EXISTS (
                SELECT 1
                FROM pg_attribute
                WHERE attrelid = to_regclass('dds.picking_route')
                  AND attname = 'rec_id'
                  AND attnum > 0
                  AND NOT attisdropped
            ),
            'Should not be required for picking_route unless explicitly mapped'
        ),
        (
            'source_has_btree_on_recid',
            EXISTS (
                SELECT 1
                FROM pg_index i
                JOIN pg_class idx ON idx.oid = i.indexrelid
                JOIN pg_am am ON am.oid = idx.relam
                WHERE i.indrelid = to_regclass('raw_ax.wmspickingroute')
                  AND am.amname = 'btree'
                  AND i.indisvalid
                  AND i.indisready
                  AND pg_get_indexdef(i.indexrelid, 1, true) = 'recid'
            ),
            'Usable for text-key keyset pagination'
        ),
        (
            'source_has_btree_on_recid_bigint',
            EXISTS (
                SELECT 1
                FROM pg_index i
                JOIN pg_class idx ON idx.oid = i.indexrelid
                JOIN pg_am am ON am.oid = idx.relam
                WHERE i.indrelid = to_regclass('raw_ax.wmspickingroute')
                  AND am.amname = 'btree'
                  AND i.indisvalid
                  AND i.indisready
                  AND pg_get_indexdef(i.indexrelid, 1, true) = 'recid_bigint'
            ),
            'Required for numeric_range on recid_bigint'
        ),
        (
            'target_has_unique_route_id',
            EXISTS (
                SELECT 1
                FROM pg_index i
                WHERE i.indrelid = to_regclass('dds.picking_route')
                  AND i.indisunique
                  AND i.indisvalid
                  AND i.indisready
                  AND i.indnkeyatts = 1
                  AND pg_get_indexdef(i.indexrelid, 1, true) = 'route_id'
            ),
            'Required for ON CONFLICT (route_id)'
        )
) AS checks(check_name, passed, interpretation)
ORDER BY check_name;

ROLLBACK;
