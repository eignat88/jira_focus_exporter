-- ============================================================
-- 02: Load dds.picking_route from raw_ax.wmspickingroute
-- Stage: 3/9
-- ============================================================

INSERT INTO etl.load_log (table_name, operation, message)
VALUES ('dds.picking_route', 'STAGE_START', 'Loading dds.picking_route');

INSERT INTO dds.picking_route (
    picking_route_id, route_code, status, start_date, end_date,
    employee_id, employee_name, location_id, pallet_id,
    modified_datetime, created_datetime, data_area_id
)
SELECT
    pickingrouteid, routeid, status,
    CASE WHEN startdate IS NOT NULL THEN startdate::timestamp END,
    CASE WHEN enddate IS NOT NULL THEN enddate::timestamp END,
    emplid, namealias, inventlocationid, wmspalletid,
    CASE WHEN modifieddatetime IS NOT NULL THEN modifieddatetime::timestamp END,
    CASE WHEN createddatetime IS NOT NULL THEN createddatetime::timestamp END,
    dataareaid
FROM raw_ax.wmspickingroute WHERE recid IS NOT NULL;

INSERT INTO etl.load_log (table_name, operation, rows_loaded, message)
VALUES ('dds.picking_route', 'STAGE_DONE', (SELECT COUNT(*) FROM dds.picking_route), 'Loaded');
