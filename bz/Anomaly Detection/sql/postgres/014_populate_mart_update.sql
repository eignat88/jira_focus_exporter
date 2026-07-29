-- ============================================================
-- 014: Populate Mart tables from DDS (T33 Mart update)
-- Date: 2026-07-22
-- ============================================================

INSERT INTO etl.load_log (table_name, operation, message)
VALUES ('mart_populate', 'START', 'T33: Mart population started');

-- 1. Populate mart.serial_mark_stats
INSERT INTO mart.serial_mark_stats (
    item_id, mark_code, total_serials,
    accepted_count, blocked_count, defect_count,
    first_mark_date, last_mark_date
)
SELECT
    item_id, mark_code, COUNT(*),
    SUM(CASE WHEN accepted = TRUE THEN 1 ELSE 0 END),
    SUM(CASE WHEN blocked = TRUE THEN 1 ELSE 0 END),
    SUM(CASE WHEN is_defect = TRUE THEN 1 ELSE 0 END),
    MIN(mark_date), MAX(mark_date)
FROM dds.serial_mark
WHERE item_id IS NOT NULL
GROUP BY item_id, mark_code;

-- 2. Populate mart.picking_route_summary
INSERT INTO mart.picking_route_summary (
    picking_route_id, employee_id, employee_name,
    total_tasks, total_picked, total_waste,
    route_duration, start_date, end_date
)
SELECT
    pr.picking_route_id, pr.employee_id, pr.employee_name,
    COUNT(pt.task_id), COALESCE(SUM(pt.picked_qty), 0),
    COALESCE(SUM(pt.waste_qty), 0),
    EXTRACT(EPOCH FROM (pr.end_date - pr.start_date)),
    pr.start_date, pr.end_date
FROM dds.picking_route pr
LEFT JOIN dds.pack_task pt ON pr.picking_route_id = pt.picking_route_id
WHERE pr.picking_route_id IS NOT NULL
GROUP BY pr.picking_route_id, pr.employee_id, pr.employee_name,
         pr.start_date, pr.end_date;

-- 3. Populate mart.order_trans_stats
INSERT INTO mart.order_trans_stats (
    order_id, total_items, total_qty,
    total_picked, total_waste,
    first_trans_date, last_trans_date
)
SELECT
    order_id, COUNT(*), SUM(qty),
    SUM(picked_qty), SUM(waste_qty),
    MIN(created_datetime), MAX(created_datetime)
FROM dds.order_trans
WHERE order_id IS NOT NULL
GROUP BY order_id;

-- 4. Populate mart.sales_summary
INSERT INTO mart.sales_summary (
    customer_account, total_orders, total_sales,
    first_order_date, last_order_date, currencies_used
)
SELECT
    customer_account, COUNT(DISTINCT sales_id), COUNT(*),
    MIN(invoice_date), MAX(invoice_date),
    STRING_AGG(DISTINCT currency_code, ', ')
FROM dds.sales_order
WHERE customer_account IS NOT NULL
GROUP BY customer_account;

-- 5. Populate mart.purchase_summary
INSERT INTO mart.purchase_summary (
    vendor_account, total_orders, total_purchases,
    first_order_date, last_order_date, currencies_used
)
SELECT
    vendor_account, COUNT(DISTINCT purchase_id), COUNT(*),
    MIN(order_date), MAX(order_date),
    STRING_AGG(DISTINCT currency_code, ', ')
FROM dds.purchase_order
WHERE vendor_account IS NOT NULL
GROUP BY vendor_account;

-- 6. Populate mart.pack_task_stats
INSERT INTO mart.pack_task_stats (
    picking_route_id, total_tasks, total_qty,
    total_picked, total_waste, avg_qty_per_task
)
SELECT
    picking_route_id, COUNT(*), SUM(qty),
    SUM(picked_qty), SUM(waste_qty), AVG(qty)
FROM dds.pack_task
WHERE picking_route_id IS NOT NULL
GROUP BY picking_route_id;

INSERT INTO etl.load_log (table_name, operation, message)
VALUES ('mart_populate', 'DONE', 'T33: Mart population completed');

-- Summary
SELECT 'mart.serial_mark_stats' as tbl, COUNT(*) as rows FROM mart.serial_mark_stats
UNION ALL SELECT 'mart.picking_route_summary', COUNT(*) FROM mart.picking_route_summary
UNION ALL SELECT 'mart.order_trans_stats', COUNT(*) FROM mart.order_trans_stats
UNION ALL SELECT 'mart.sales_summary', COUNT(*) FROM mart.sales_summary
UNION ALL SELECT 'mart.purchase_summary', COUNT(*) FROM mart.purchase_summary
UNION ALL SELECT 'mart.pack_task_stats', COUNT(*) FROM mart.pack_task_stats;
