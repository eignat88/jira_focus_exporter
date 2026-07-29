-- ============================================================
-- 005_04: Load dds.picking_operation (CREATE TABLE AS)
-- ============================================================

-- Drop existing table
DROP TABLE IF EXISTS dds.picking_operation;

-- Create and populate in one step
CREATE TABLE dds.picking_operation AS
SELECT
    ROW_NUMBER() OVER () AS operation_id,
    d.pickingrouteid AS picking_route_id,
    d.itemid AS item_id,
    d.markcode AS mark_code,
    d.acttype AS act_type,
    CAST(d.diffqtyforpick AS NUMERIC) AS picked_qty,
    CAST(d.diffqtyforcontrol AS NUMERIC) AS waste_qty,
    CAST(d.diffqtyforcontrol AS NUMERIC) - CAST(d.diffqtyforpick AS NUMERIC) AS diff_qty,
    b.wmspalletid AS pallet_id,
    b.sscc AS sscc,
    b.gtin AS gtin,
    CAST(d.createddatetime AS TIMESTAMP) AS operation_date,
    d.dataareaid AS data_area_id
FROM raw_ax.wms_pickdiffactline d
LEFT JOIN raw_ax.lfl_pickinglinebuffermarking b
    ON d.pickingrouteid = b.routeid
   AND d.itemid = b.itemid;

-- Add primary key
ALTER TABLE dds.picking_operation ADD PRIMARY KEY (operation_id);

-- Verify
SELECT 'dds.picking_operation loaded: ' || COUNT(*) AS result FROM dds.picking_operation;
