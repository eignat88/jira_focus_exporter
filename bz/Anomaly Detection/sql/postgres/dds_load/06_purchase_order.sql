-- ============================================================
-- 06: Load dds.purchase_order from raw_ax.purchtable
-- Stage: 7/9
-- ============================================================

INSERT INTO etl.load_log (table_name, operation, message)
VALUES ('dds.purchase_order', 'STAGE_START', 'Loading dds.purchase_order');

INSERT INTO dds.purchase_order (
    purchase_id, vendor_account, order_date, delivery_date,
    currency_code, purchase_status,
    modified_datetime, created_datetime, data_area_id
)
SELECT
    purchid, vendaccount,
    CASE WHEN orderdate IS NOT NULL THEN orderdate::timestamp END,
    CASE WHEN deliverydate IS NOT NULL THEN deliverydate::timestamp END,
    currencycode, purchstatus,
    CASE WHEN modifieddatetime IS NOT NULL THEN modifieddatetime::timestamp END,
    CASE WHEN createddatetime IS NOT NULL THEN createddatetime::timestamp END,
    dataareaid
FROM raw_ax.purchtable WHERE recid IS NOT NULL;

INSERT INTO etl.load_log (table_name, operation, rows_loaded, message)
VALUES ('dds.purchase_order', 'STAGE_DONE', (SELECT COUNT(*) FROM dds.purchase_order), 'Loaded');
