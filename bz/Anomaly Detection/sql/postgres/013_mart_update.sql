-- ============================================================
-- 013: Mart Update - Create mart tables for new DDS tables
-- Task: T33 - Mart update
-- Date: 2026-07-22
-- ============================================================

INSERT INTO etl.load_log (table_name, operation, message)
VALUES ('mart_update', 'START', 'T33: Mart update started');

-- 1. mart.serial_mark_stats
CREATE TABLE IF NOT EXISTS mart.serial_mark_stats (
    stat_id         SERIAL PRIMARY KEY,
    item_id         TEXT,
    mark_code       TEXT,
    total_serials   INT,
    accepted_count  INT,
    blocked_count   INT,
    defect_count    INT,
    first_mark_date TIMESTAMP,
    last_mark_date  TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_serial_stats_item ON mart.serial_mark_stats (item_id);

-- 2. mart.picking_route_summary
CREATE TABLE IF NOT EXISTS mart.picking_route_summary (
    summary_id      SERIAL PRIMARY KEY,
    picking_route_id TEXT,
    employee_id     TEXT,
    employee_name   TEXT,
    total_tasks     INT,
    total_picked    NUMERIC,
    total_waste     NUMERIC,
    route_duration  NUMERIC,
    start_date      TIMESTAMP,
    end_date        TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_route_summary_route ON mart.picking_route_summary (picking_route_id);

-- 3. mart.order_trans_stats
CREATE TABLE IF NOT EXISTS mart.order_trans_stats (
    stat_id         SERIAL PRIMARY KEY,
    order_id        TEXT,
    total_items     INT,
    total_qty       NUMERIC,
    total_picked    NUMERIC,
    total_waste     NUMERIC,
    first_trans_date TIMESTAMP,
    last_trans_date TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_order_stats_order ON mart.order_trans_stats (order_id);

-- 4. mart.sales_summary
CREATE TABLE IF NOT EXISTS mart.sales_summary (
    summary_id      SERIAL PRIMARY KEY,
    customer_account TEXT,
    total_orders    INT,
    total_sales     INT,
    first_order_date TIMESTAMP,
    last_order_date TIMESTAMP,
    currencies_used TEXT
);

CREATE INDEX IF NOT EXISTS idx_sales_summary_cust ON mart.sales_summary (customer_account);

-- 5. mart.purchase_summary
CREATE TABLE IF NOT EXISTS mart.purchase_summary (
    summary_id      SERIAL PRIMARY KEY,
    vendor_account  TEXT,
    total_orders    INT,
    total_purchases INT,
    first_order_date TIMESTAMP,
    last_order_date TIMESTAMP,
    currencies_used TEXT
);

CREATE INDEX IF NOT EXISTS idx_purchase_summary_vendor ON mart.purchase_summary (vendor_account);

-- 6. mart.pack_task_stats
CREATE TABLE IF NOT EXISTS mart.pack_task_stats (
    stat_id         SERIAL PRIMARY KEY,
    picking_route_id TEXT,
    total_tasks     INT,
    total_qty       NUMERIC,
    total_picked    NUMERIC,
    total_waste     NUMERIC,
    avg_qty_per_task NUMERIC
);

CREATE INDEX IF NOT EXISTS idx_pack_stats_route ON mart.pack_task_stats (picking_route_id);

INSERT INTO etl.load_log (table_name, operation, message)
VALUES ('mart_update', 'TABLES_CREATED', 'Created 6 mart tables');
