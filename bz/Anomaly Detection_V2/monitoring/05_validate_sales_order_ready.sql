\set ON_ERROR_STOP on
\pset pager off
\timing on

BEGIN TRANSACTION READ ONLY;
SET LOCAL application_name = 'validate_sales_order_ready';
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '5min';

\echo '=== 1. DDS source_recid and UNIQUE ==='
SELECT
    EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'dds'
          AND table_name = 'sales_order'
          AND column_name = 'source_recid'
          AND udt_name = 'int8'
          AND is_nullable = 'NO'
    ) AS source_recid_bigint_not_null,
    EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'dds.sales_order'::regclass
          AND conname = 'uq_sales_order_source_recid'
          AND contype = 'u'
    ) AS source_recid_unique;

\echo '=== 2. Functional index status ==='
SELECT
    idx.relname AS index_name,
    i.indisvalid,
    i.indisready,
    pg_get_indexdef(idx.oid) AS index_definition
FROM pg_index i
JOIN pg_class idx
    ON idx.oid = i.indexrelid
WHERE i.indrelid = 'raw_ax.salestable'::regclass
  AND idx.relname = 'idx_salestable_recid_bigint';

\echo '=== 3. Required chunk plan: must not be Seq Scan ==='
EXPLAIN (COSTS, VERBOSE, SETTINGS, FORMAT TEXT)
SELECT *
FROM raw_ax.salestable
WHERE btrim(recid)::bigint >= 5665000000
  AND btrim(recid)::bigint < 5665500000
ORDER BY btrim(recid)::bigint;

\echo '=== 4. Mapping sample with invoice_date deliberately NULL ==='
SELECT
    btrim(recid)::bigint AS source_recid,
    NULLIF(btrim(salesid), '') AS sales_id,
    NULLIF(btrim(custaccount), '') AS customer_account,
    NULL::timestamp without time zone AS invoice_date,
    CASE
        WHEN NULLIF(btrim(deliverydate), '') IS NULL THEN NULL
        WHEN btrim(deliverydate) IN ('1900-01-01', '1900-01-01 00:00:00') THEN NULL
        WHEN btrim(deliverydate) ~ '^\d{4}-\d{2}-\d{2}'
        THEN btrim(deliverydate)::timestamp
        ELSE NULL
    END AS delivery_date,
    NULLIF(btrim(currencycode), '') AS currency_code,
    NULLIF(btrim(salesstatus), '') AS sales_status,
    CASE
        WHEN NULLIF(btrim(modifieddatetime), '') IS NULL THEN NULL
        WHEN btrim(modifieddatetime) ~ '^\d{4}-\d{2}-\d{2}'
        THEN btrim(modifieddatetime)::timestamp
        ELSE NULL
    END AS modified_datetime,
    CASE
        WHEN NULLIF(btrim(createddatetime), '') IS NULL THEN NULL
        WHEN btrim(createddatetime) ~ '^\d{4}-\d{2}-\d{2}'
        THEN btrim(createddatetime)::timestamp
        ELSE NULL
    END AS created_datetime,
    NULLIF(btrim(dataareaid), '') AS data_area_id
FROM raw_ax.salestable
WHERE btrim(recid)::bigint >= 5665000000
  AND btrim(recid)::bigint < 5665500000
ORDER BY btrim(recid)::bigint
LIMIT 100;

ROLLBACK;
