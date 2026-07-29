-- ============================================================
-- 005_02: Load dds.warehouse from raw_ax.inventlocation
-- ============================================================

INSERT INTO dds.warehouse (location_id, location_name, location_type, is_active)
SELECT
    inventlocationid,
    name,
    inventlocationtype,
    TRUE
FROM raw_ax.inventlocation
ON CONFLICT DO NOTHING;

-- Verify
SELECT 'dds.warehouse loaded: ' || COUNT(*) AS result FROM dds.warehouse;
