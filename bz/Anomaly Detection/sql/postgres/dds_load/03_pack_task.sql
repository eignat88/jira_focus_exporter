-- ============================================================
-- 03: Load dds.pack_task from raw_ax.lfl_scspacktask
-- Stage: 4/9
-- ============================================================

INSERT INTO etl.load_log (table_name, operation, message)
VALUES ('dds.pack_task', 'STAGE_START', 'Loading dds.pack_task');

INSERT INTO dds.pack_task (
    pack_task_id, picking_route_id, item_id, invent_dim_id,
    qty, picked_qty, waste_qty, pallet_id,
    modified_datetime, created_datetime, data_area_id
)
SELECT
    packtaskid, pickingrouteid, itemid, inventdimid,
    CASE WHEN qty IS NOT NULL THEN qty::numeric END,
    CASE WHEN pickedqty IS NOT NULL THEN pickedqty::numeric END,
    CASE WHEN wasteqty IS NOT NULL THEN wasteqty::numeric END,
    wmspalletid,
    CASE WHEN modifieddatetime IS NOT NULL THEN modifieddatetime::timestamp END,
    CASE WHEN createddatetime IS NOT NULL THEN createddatetime::timestamp END,
    dataareaid
FROM raw_ax.lfl_scspacktask WHERE recid IS NOT NULL;

INSERT INTO etl.load_log (table_name, operation, rows_loaded, message)
VALUES ('dds.pack_task', 'STAGE_DONE', (SELECT COUNT(*) FROM dds.pack_task), 'Loaded');
