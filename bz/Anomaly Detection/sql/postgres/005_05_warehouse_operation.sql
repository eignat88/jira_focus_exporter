-- ============================================================
-- 005_05: Load dds.warehouse_operation (CREATE TABLE AS, 41M rows)
-- ============================================================

-- Drop existing table
DROP TABLE IF EXISTS dds.warehouse_operation;

-- Create and populate in one step (optimized for 41M rows)
CREATE TABLE dds.warehouse_operation AS
SELECT
    ROW_NUMBER() OVER () AS operation_id,
    emplid AS employee_id,
    namealias AS employee_name,
    operationtype AS operation_type,
    CAST(startdate AS TIMESTAMP) AS start_time,
    CAST(enddate AS TIMESTAMP) AS end_time,
    CAST(durationoperation AS NUMERIC) AS duration_seconds
FROM raw_ax.wms_journalwarehouseoperationtable;

-- Add primary key
ALTER TABLE dds.warehouse_operation ADD PRIMARY KEY (operation_id);

-- Verify
SELECT 'dds.warehouse_operation loaded: ' || COUNT(*) AS result FROM dds.warehouse_operation;
