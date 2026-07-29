-- ============================================================
-- 04: Load dds.order_trans from raw_ax.wmsordertrans
-- Stage: 5/9
-- ============================================================

INSERT INTO etl.load_log (table_name, operation, message)
VALUES ('dds.order_trans', 'STAGE_START', 'Loading dds.order_trans');

INSERT INTO dds.order_trans (
    order_id, order_trans_id, item_id, invent_dim_id,
    qty, picked_qty, waste_qty,
    modified_datetime, created_datetime, data_area_id
)
SELECT
    orderid, ordertransid, itemid, inventdimid,
    CASE WHEN qty IS NOT NULL THEN qty::numeric END,
    CASE WHEN pickedqty IS NOT NULL THEN pickedqty::numeric END,
    CASE WHEN wastedqty IS NOT NULL THEN wastedqty::numeric END,
    CASE WHEN modifieddatetime IS NOT NULL THEN modifieddatetime::timestamp END,
    CASE WHEN createddatetime IS NOT NULL THEN createddatetime::timestamp END,
    dataareaid
FROM raw_ax.wmsordertrans WHERE recid IS NOT NULL;

INSERT INTO etl.load_log (table_name, operation, rows_loaded, message)
VALUES ('dds.order_trans', 'STAGE_DONE', (SELECT COUNT(*) FROM dds.order_trans), 'Loaded');
