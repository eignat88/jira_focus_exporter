\set ON_ERROR_STOP on
\pset pager off
\timing on

-- Изменяющая операция.
-- Предназначена для пустой dds.sales_order.
-- Блокировка: ACCESS EXCLUSIVE на dds.sales_order, обычно кратковременно.
-- WAL/диск: минимально для пустой таблицы.

BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '2min';

DO $$
DECLARE
    v_live_estimate bigint;
BEGIN
    IF to_regclass('dds.sales_order') IS NULL THEN
        RAISE EXCEPTION 'dds.sales_order does not exist';
    END IF;

    SELECT COALESCE(n_live_tup, 0)
      INTO v_live_estimate
    FROM pg_stat_user_tables
    WHERE schemaname = 'dds'
      AND relname = 'sales_order';

    IF COALESCE(v_live_estimate, 0) > 0 THEN
        RAISE EXCEPTION
            'dds.sales_order is not empty by estimate (n_live_tup=%). Review before ALTER.',
            v_live_estimate;
    END IF;
END
$$;

ALTER TABLE dds.sales_order
    ADD COLUMN IF NOT EXISTS source_recid bigint;

ALTER TABLE dds.sales_order
    ALTER COLUMN source_recid SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'dds.sales_order'::regclass
          AND conname = 'uq_sales_order_source_recid'
    ) THEN
        ALTER TABLE dds.sales_order
            ADD CONSTRAINT uq_sales_order_source_recid
            UNIQUE (source_recid);
    END IF;
END
$$;

COMMIT;

SELECT
    ordinal_position,
    column_name,
    data_type,
    is_nullable,
    column_default
FROM information_schema.columns
WHERE table_schema = 'dds'
  AND table_name = 'sales_order'
ORDER BY ordinal_position;

SELECT
    conname,
    pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid = 'dds.sales_order'::regclass
ORDER BY conname;
