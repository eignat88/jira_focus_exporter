-- ============================================================
-- 05: Load dds.sales_order from raw_ax.salestable
-- Stage: 6/9
-- ============================================================

INSERT INTO etl.load_log (table_name, operation, message)
VALUES ('dds.sales_order', 'STAGE_START', 'Loading dds.sales_order');

INSERT INTO dds.sales_order (
    sales_id, customer_account, invoice_date, delivery_date,
    currency_code, sales_status,
    modified_datetime, created_datetime, data_area_id
)
SELECT
    salesid, custaccount,
    CASE WHEN invoicedate IS NOT NULL THEN invoicedate::timestamp END,
    CASE WHEN deliverydate IS NOT NULL THEN deliverydate::timestamp END,
    currencycode, salesstatus,
    CASE WHEN modifieddatetime IS NOT NULL THEN modifieddatetime::timestamp END,
    CASE WHEN createddatetime IS NOT NULL THEN createddatetime::timestamp END,
    dataareaid
FROM raw_ax.salestable WHERE recid IS NOT NULL;

INSERT INTO etl.load_log (table_name, operation, rows_loaded, message)
VALUES ('dds.sales_order', 'STAGE_DONE', (SELECT COUNT(*) FROM dds.sales_order), 'Loaded');
