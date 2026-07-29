-- ============================================================
-- 004: Create DDS (Data Domain Store) and Mart schemas/tables
-- ============================================================

-- DDS schema: normalized data with clear relationships
CREATE SCHEMA IF NOT EXISTS dds;

-- Mart schema: ready-to-use reports
CREATE SCHEMA IF NOT EXISTS mart;

-- ============================================================
-- DDS Tables
-- ============================================================

-- Products (from raw_ax.inventtable)
CREATE TABLE IF NOT EXISTS dds.product (
    product_id  SERIAL PRIMARY KEY,
    item_id     TEXT NOT NULL,
    item_name   TEXT,
    item_group  TEXT,
    item_type   TEXT,
    data_area_id TEXT,
    rec_id      BIGINT
);

CREATE INDEX IF NOT EXISTS idx_product_item_id ON dds.product (item_id);
CREATE INDEX IF NOT EXISTS idx_product_item_group ON dds.product (item_group);

-- Warehouses (from raw_ax.inventlocation)
CREATE TABLE IF NOT EXISTS dds.warehouse (
    warehouse_id   SERIAL PRIMARY KEY,
    location_id    TEXT NOT NULL,
    location_name  TEXT,
    location_type  TEXT,
    is_active      BOOLEAN DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_warehouse_location_id ON dds.warehouse (location_id);

-- Marking codes (from raw_ax.lfl_markingcodetable)
CREATE TABLE IF NOT EXISTS dds.marking_code (
    marking_code_id SERIAL PRIMARY KEY,
    mark_code       TEXT NOT NULL,
    item_id         TEXT,
    description     TEXT
);

CREATE INDEX IF NOT EXISTS idx_marking_code_mark ON dds.marking_code (mark_code);
CREATE INDEX IF NOT EXISTS idx_marking_code_item ON dds.marking_code (item_id);

-- Picking operations (from raw_ax.wms_pickdiffactline + lfl_pickinglinebuffermarking)
CREATE TABLE IF NOT EXISTS dds.picking_operation (
    operation_id    SERIAL PRIMARY KEY,
    picking_route_id TEXT,
    item_id         TEXT,
    mark_code       TEXT,
    act_type        TEXT,
    picked_qty      NUMERIC,
    waste_qty       NUMERIC,
    diff_qty        NUMERIC,
    pallet_id       TEXT,
    sscc            TEXT,
    gtin            TEXT,
    operation_date  TIMESTAMP,
    data_area_id    TEXT
);

CREATE INDEX IF NOT EXISTS idx_picking_op_route ON dds.picking_operation (picking_route_id);
CREATE INDEX IF NOT EXISTS idx_picking_op_item ON dds.picking_operation (item_id);
CREATE INDEX IF NOT EXISTS idx_picking_op_date ON dds.picking_operation (operation_date);

-- Warehouse operations (from raw_ax.wms_journalwarehouseoperationtable)
CREATE TABLE IF NOT EXISTS dds.warehouse_operation (
    operation_id     SERIAL PRIMARY KEY,
    employee_id      TEXT,
    employee_name    TEXT,
    operation_type   TEXT,
    start_time       TIMESTAMP,
    end_time         TIMESTAMP,
    duration_seconds NUMERIC
);

CREATE INDEX IF NOT EXISTS idx_wh_op_employee ON dds.warehouse_operation (employee_id);
CREATE INDEX IF NOT EXISTS idx_wh_op_start ON dds.warehouse_operation (start_time);

-- ============================================================
-- Mart Tables
-- ============================================================

-- Daily operations aggregated by day and employee
CREATE TABLE IF NOT EXISTS mart.daily_operations (
    operation_date  DATE,
    employee_id     TEXT,
    employee_name   TEXT,
    operation_type  TEXT,
    total_operations INT,
    total_duration   NUMERIC,
    avg_duration     NUMERIC
);

CREATE INDEX IF NOT EXISTS idx_daily_ops_date ON mart.daily_operations (operation_date);
CREATE INDEX IF NOT EXISTS idx_daily_ops_emp ON mart.daily_operations (employee_id);

-- Picking efficiency by route and item
CREATE TABLE IF NOT EXISTS mart.picking_efficiency (
    picking_route_id TEXT,
    item_id          TEXT,
    total_picked     NUMERIC,
    total_waste      NUMERIC,
    waste_rate       NUMERIC,
    operation_count  INT
);

CREATE INDEX IF NOT EXISTS idx_pick_eff_route ON mart.picking_efficiency (picking_route_id);

-- Marking code usage statistics
CREATE TABLE IF NOT EXISTS mart.marking_statistics (
    mark_code     TEXT,
    item_id       TEXT,
    usage_count   INT,
    total_picked  NUMERIC
);

CREATE INDEX IF NOT EXISTS idx_mark_stat_code ON mart.marking_statistics (mark_code);
