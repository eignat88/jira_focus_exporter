-- ============================================================
-- 007: Feature Engineering tables in mart schema
-- ============================================================

-- Признаки сотрудников
CREATE TABLE IF NOT EXISTS mart.feature_employee (
    employee_id TEXT PRIMARY KEY,
    employee_name TEXT,
    total_operations BIGINT,
    total_duration NUMERIC,
    avg_duration NUMERIC,
    days_active INT,
    first_operation TIMESTAMP,
    last_operation TIMESTAMP,
    operation_count INT,
    scan_interval NUMERIC,          -- Признак 1: средний интервал между операциями (сек)
    total_hours NUMERIC,
    scans_per_hour NUMERIC,         -- Признак 2: операций в час
    time_since_last_scan INT        -- Признак 3: дней с последней операции
);

-- Признаки маршрутов сборки
CREATE TABLE IF NOT EXISTS mart.feature_route (
    picking_route_id TEXT PRIMARY KEY,
    total_picked NUMERIC,
    total_waste NUMERIC,
    operation_count BIGINT,
    item_count INT,
    avg_waste_rate NUMERIC,
    error_rate NUMERIC,             -- Признак 4: доля потерянных единиц
    picked_per_operation NUMERIC
);

-- Признаки товаров
CREATE TABLE IF NOT EXISTS mart.feature_item (
    item_id TEXT PRIMARY KEY,
    total_picked NUMERIC,
    total_waste NUMERIC,
    route_count INT,
    avg_waste_rate NUMERIC,
    error_rate NUMERIC,
    mark_usage_count NUMERIC,
    mark_total_picked NUMERIC,
    unique_marks INT,
    competitor_ratio NUMERIC        -- Признак 5: доля конкурентных кодов
);

-- Метки аномалий (для supervised обучения)
CREATE TABLE IF NOT EXISTS mart.label_employee (
    employee_id TEXT,
    is_anomaly INT,
    scan_interval NUMERIC,
    scans_per_hour NUMERIC,
    time_since_last_scan INT
);

CREATE TABLE IF NOT EXISTS mart.label_route (
    picking_route_id TEXT,
    is_anomaly INT,
    error_rate NUMERIC
);

CREATE TABLE IF NOT EXISTS mart.label_item (
    item_id TEXT,
    is_anomaly INT,
    error_rate NUMERIC,
    competitor_ratio NUMERIC
);

-- Индексы
CREATE INDEX IF NOT EXISTS idx_feature_emp_anomaly ON mart.label_employee (is_anomaly);
CREATE INDEX IF NOT EXISTS idx_feature_route_anomaly ON mart.label_route (is_anomaly);
CREATE INDEX IF NOT EXISTS idx_feature_item_anomaly ON mart.label_item (is_anomaly);
