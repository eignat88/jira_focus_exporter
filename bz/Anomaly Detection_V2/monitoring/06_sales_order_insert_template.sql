-- TEMPLATE ONLY. Do not run directly as production ETL without review.
--
-- Confirmed:
--   recid -> source_recid
--   salesid -> sales_id
--   custaccount -> customer_account
--   deliverydate -> delivery_date
--   currencycode -> currency_code
--   salesstatus -> sales_status (raw enum/text; business decoding still optional)
--   modifieddatetime -> modified_datetime
--   createddatetime -> created_datetime
--   dataareaid -> data_area_id
--
-- Not confirmed:
--   invoice_date: no standalone invoice-date column was found in SALESTABLE.
--   It is intentionally NULL until a verified source is joined.

INSERT INTO dds.sales_order (
    source_recid,
    sales_id,
    customer_account,
    invoice_date,
    delivery_date,
    currency_code,
    sales_status,
    modified_datetime,
    created_datetime,
    data_area_id
)
SELECT
    btrim(s.recid)::bigint AS source_recid,
    NULLIF(btrim(s.salesid), '') AS sales_id,
    NULLIF(btrim(s.custaccount), '') AS customer_account,
    NULL::timestamp without time zone AS invoice_date,
    CASE
        WHEN NULLIF(btrim(s.deliverydate), '') IS NULL THEN NULL
        WHEN btrim(s.deliverydate) IN ('1900-01-01', '1900-01-01 00:00:00') THEN NULL
        WHEN btrim(s.deliverydate) ~ '^\d{4}-\d{2}-\d{2}'
        THEN btrim(s.deliverydate)::timestamp
        ELSE NULL
    END AS delivery_date,
    NULLIF(btrim(s.currencycode), '') AS currency_code,
    NULLIF(btrim(s.salesstatus), '') AS sales_status,
    CASE
        WHEN NULLIF(btrim(s.modifieddatetime), '') IS NULL THEN NULL
        WHEN btrim(s.modifieddatetime) ~ '^\d{4}-\d{2}-\d{2}'
        THEN btrim(s.modifieddatetime)::timestamp
        ELSE NULL
    END AS modified_datetime,
    CASE
        WHEN NULLIF(btrim(s.createddatetime), '') IS NULL THEN NULL
        WHEN btrim(s.createddatetime) ~ '^\d{4}-\d{2}-\d{2}'
        THEN btrim(s.createddatetime)::timestamp
        ELSE NULL
    END AS created_datetime,
    NULLIF(btrim(s.dataareaid), '') AS data_area_id
FROM raw_ax.salestable s
WHERE btrim(s.recid)::bigint >= :range_start
  AND btrim(s.recid)::bigint <  :range_end
ORDER BY btrim(s.recid)::bigint
ON CONFLICT (source_recid) DO UPDATE
SET
    sales_id = EXCLUDED.sales_id,
    customer_account = EXCLUDED.customer_account,
    invoice_date = EXCLUDED.invoice_date,
    delivery_date = EXCLUDED.delivery_date,
    currency_code = EXCLUDED.currency_code,
    sales_status = EXCLUDED.sales_status,
    modified_datetime = EXCLUDED.modified_datetime,
    created_datetime = EXCLUDED.created_datetime,
    data_area_id = EXCLUDED.data_area_id;
