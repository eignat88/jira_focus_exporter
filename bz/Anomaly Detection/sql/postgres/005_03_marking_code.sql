-- ============================================================
-- 005_03: Load dds.marking_code from raw_ax.wms_pickdiffactline
-- ============================================================

INSERT INTO dds.marking_code (mark_code, item_id, description)
SELECT DISTINCT
    markcode,
    itemid,
    NULL
FROM raw_ax.wms_pickdiffactline
WHERE markcode IS NOT NULL
ON CONFLICT DO NOTHING;

-- Verify
SELECT 'dds.marking_code loaded: ' || COUNT(*) AS result FROM dds.marking_code;
