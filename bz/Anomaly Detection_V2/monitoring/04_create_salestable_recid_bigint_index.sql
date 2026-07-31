\set ON_ERROR_STOP on
\pset pager off
\timing on

-- IMPORTANT:
-- CREATE INDEX CONCURRENTLY нельзя запускать внутри BEGIN/COMMIT.
-- Запускать только после успешного 03_validate_salestable_recid.sql.
--
-- Операция читает всю raw_ax.salestable, создает WAL и индекс на диске.
-- При отмене проверьте indisvalid; INVALID index удалять CONCURRENTLY.

\echo '=== Existing matching index ==='
SELECT
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = 'raw_ax'
  AND tablename = 'salestable'
ORDER BY indexname;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_salestable_recid_bigint
    ON raw_ax.salestable ((btrim(recid)::bigint));

\echo '=== Index status ==='
SELECT
    idx.relname AS index_name,
    i.indisvalid,
    i.indisready,
    i.indislive,
    pg_size_pretty(pg_relation_size(idx.oid)) AS index_size,
    pg_get_indexdef(idx.oid) AS index_definition
FROM pg_index i
JOIN pg_class idx
    ON idx.oid = i.indexrelid
WHERE i.indrelid = 'raw_ax.salestable'::regclass
  AND idx.relname = 'idx_salestable_recid_bigint';
