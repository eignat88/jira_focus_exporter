-- ============================================================
-- 01: Load dds.serial_mark from raw_ax.alk_markserial
-- Stage: 2/9
-- ============================================================

-- Log stage start
INSERT INTO etl.load_log (table_name, operation, message)
VALUES ('dds.serial_mark', 'STAGE_START', 'Loading dds.serial_mark');

-- Truncate target (for full mode)
TRUNCATE TABLE dds.serial_mark RESTART IDENTITY;

-- Insert data
INSERT INTO dds.serial_mark (
    rec_id, gtin, serial_number, item_id,
    mark_code,
    modified_datetime, modified_by,
    created_datetime, created_by
)
SELECT
    recid::bigint, gtin, serialid, itemid,
    markcode,
    CASE WHEN modifieddatetime IS NOT NULL THEN modifieddatetime::timestamp END,
    modifiedby,
    CASE WHEN createddatetime IS NOT NULL THEN createddatetime::timestamp END,
    createdby
FROM raw_ax.alk_markserial
WHERE recid IS NOT NULL;

-- Log completion
INSERT INTO etl.load_log (table_name, operation, rows_loaded, message)
VALUES ('dds.serial_mark', 'STAGE_DONE', (SELECT COUNT(*) FROM dds.serial_mark), 'Loaded');
