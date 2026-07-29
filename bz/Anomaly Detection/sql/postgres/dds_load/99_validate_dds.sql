-- ============================================================
-- 99: Validate DDS tables (Stage 9/9)
-- ============================================================

SELECT 'dds.serial_mark' as table_name,
    (SELECT COUNT(*) FROM raw_ax.alk_markserial WHERE recid IS NOT NULL) as source_rows,
    (SELECT COUNT(*) FROM dds.serial_mark) as target_rows
UNION ALL
SELECT 'dds.picking_route',
    (SELECT COUNT(*) FROM raw_ax.wmspickingroute WHERE recid IS NOT NULL),
    (SELECT COUNT(*) FROM dds.picking_route)
UNION ALL
SELECT 'dds.pack_task',
    (SELECT COUNT(*) FROM raw_ax.lfl_scspacktask WHERE recid IS NOT NULL),
    (SELECT COUNT(*) FROM dds.pack_task)
UNION ALL
SELECT 'dds.order_trans',
    (SELECT COUNT(*) FROM raw_ax.wmsordertrans WHERE recid IS NOT NULL),
    (SELECT COUNT(*) FROM dds.order_trans)
UNION ALL
SELECT 'dds.sales_order',
    (SELECT COUNT(*) FROM raw_ax.salestable WHERE recid IS NOT NULL),
    (SELECT COUNT(*) FROM dds.sales_order)
UNION ALL
SELECT 'dds.purchase_order',
    (SELECT COUNT(*) FROM raw_ax.purchtable WHERE recid IS NOT NULL),
    (SELECT COUNT(*) FROM dds.purchase_order);
