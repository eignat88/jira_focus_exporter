\set ON_ERROR_STOP on
\pset pager off
\timing on

BEGIN TRANSACTION READ ONLY;
SET LOCAL application_name = 'diagnose_sales_order_mapping';
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '5min';

\echo '=== 1. Required objects ==='
SELECT
    to_regclass('raw_ax.salestable') AS raw_table,
    to_regclass('dds.sales_order') AS dds_table;

\echo '=== 2. Required RAW columns and types ==='
SELECT
    ordinal_position,
    column_name,
    data_type,
    udt_name,
    is_nullable
FROM information_schema.columns
WHERE table_schema = 'raw_ax'
  AND table_name = 'salestable'
  AND lower(column_name) IN (
      'recid',
      'salesid',
      'custaccount',
      'invoiceaccount',
      'deliverydate',
      'currencycode',
      'salesstatus',
      'modifieddatetime',
      'createddatetime',
      'dataareaid'
  )
ORDER BY ordinal_position;

\echo '=== 3. Invoice/date candidate columns in SALESTABLE ==='
SELECT
    ordinal_position,
    column_name,
    data_type
FROM information_schema.columns
WHERE table_schema = 'raw_ax'
  AND table_name = 'salestable'
  AND (
      lower(column_name) LIKE '%invoice%'
      OR lower(column_name) LIKE '%date%'
  )
ORDER BY ordinal_position;

\echo '=== 4. DDS columns ==='
SELECT
    ordinal_position,
    column_name,
    data_type,
    udt_name,
    is_nullable,
    column_default
FROM information_schema.columns
WHERE table_schema = 'dds'
  AND table_name = 'sales_order'
ORDER BY ordinal_position;

\echo '=== 5. Preliminary mapping sample ==='
SELECT
    btrim(recid) AS source_recid_text,
    CASE
        WHEN recid IS NOT NULL AND btrim(recid) ~ '^[0-9]+$'
        THEN btrim(recid)::bigint
    END AS source_recid,
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
FROM raw_ax.salestable TABLESAMPLE SYSTEM (0.01)
LIMIT 100;

\echo '=== 6. Sample of unparseable date values ==='
SELECT
    recid,
    deliverydate,
    modifieddatetime,
    createddatetime
FROM raw_ax.salestable TABLESAMPLE SYSTEM (0.1)
WHERE
    (
        NULLIF(btrim(deliverydate), '') IS NOT NULL
        AND btrim(deliverydate) NOT IN ('1900-01-01', '1900-01-01 00:00:00')
        AND btrim(deliverydate) !~ '^\d{4}-\d{2}-\d{2}'
    )
    OR (
        NULLIF(btrim(modifieddatetime), '') IS NOT NULL
        AND btrim(modifieddatetime) !~ '^\d{4}-\d{2}-\d{2}'
    )
    OR (
        NULLIF(btrim(createddatetime), '') IS NOT NULL
        AND btrim(createddatetime) !~ '^\d{4}-\d{2}-\d{2}'
    )
LIMIT 100;

\echo '=== 7. Current indexes and constraints ==='
SELECT schemaname, tablename, indexname, indexdef
FROM pg_indexes
WHERE (schemaname, tablename) IN (
    ('raw_ax', 'salestable'),
    ('dds', 'sales_order')
)
ORDER BY schemaname, tablename, indexname;

SELECT
    conname,
    contype,
    pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid = 'dds.sales_order'::regclass
ORDER BY conname;

ROLLBACK;
