-- 001_fix_purchase_order_id_bigint.sql
-- Target: dds.purchase_order
-- Safe when the target table is empty.
-- Run in DBeaver or psql as a separate controlled operation.

\set ON_ERROR_STOP on

-- 1. Read-only checks
SELECT
    count(*) AS target_rows,
    pg_size_pretty(pg_total_relation_size('dds.purchase_order'::regclass)) AS total_size
FROM dds.purchase_order;

SELECT
    pid,
    state,
    wait_event_type,
    wait_event,
    now() - query_start AS duration,
    left(query, 500) AS query
FROM pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid()
  AND query ILIKE '%dds.purchase_order%';

SELECT
    column_name,
    data_type,
    udt_name
FROM information_schema.columns
WHERE table_schema = 'dds'
  AND table_name = 'purchase_order'
  AND column_name = 'purchase_order_id';

-- 2. Guard: stop if target is not empty
DO $$
DECLARE
    v_rows bigint;
BEGIN
    SELECT count(*) INTO v_rows
    FROM dds.purchase_order;

    IF v_rows <> 0 THEN
        RAISE EXCEPTION
            'dds.purchase_order is not empty (% rows). Type change aborted.',
            v_rows;
    END IF;
END
$$;

-- 3. Change int4 -> bigint
BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '2min';

ALTER TABLE dds.purchase_order
    ALTER COLUMN purchase_order_id TYPE bigint;

COMMIT;

-- 4. Verification
SELECT
    column_name,
    data_type,
    udt_name
FROM information_schema.columns
WHERE table_schema = 'dds'
  AND table_name = 'purchase_order'
  AND column_name = 'purchase_order_id';

SELECT
    conname,
    contype,
    pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid = 'dds.purchase_order'::regclass
ORDER BY contype, conname;
