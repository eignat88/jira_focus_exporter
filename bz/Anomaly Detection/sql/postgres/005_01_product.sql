-- ============================================================
-- 005_01: Load dds.product from raw_ax.inventtable
-- ============================================================

INSERT INTO dds.product (item_id, item_name, item_group, item_type, data_area_id, rec_id)
SELECT
    itemid,
    namealias,
    prodgroupid,
    itemtype,
    dataareaid,
    recid::bigint
FROM raw_ax.inventtable
ON CONFLICT DO NOTHING;

-- Verify
SELECT 'dds.product loaded: ' || COUNT(*) AS result FROM dds.product;
