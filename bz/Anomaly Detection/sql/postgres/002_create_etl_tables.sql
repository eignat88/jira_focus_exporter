-- ============================================================
-- Создание технических таблиц ETL
-- ============================================================

-- Статус загрузки таблиц
CREATE TABLE IF NOT EXISTS etl.load_status (
    table_name text PRIMARY KEY,
    strategy text,           -- full, incremental, aggregate
    last_recid bigint DEFAULT 0,
    last_datetime timestamp,
    loaded_rows bigint DEFAULT 0,
    total_rows bigint DEFAULT 0,
    status text DEFAULT 'PENDING',  -- PENDING, RUNNING, DONE, ERROR
    started_at timestamp,
    finished_at timestamp,
    error_text text
);

-- Лог загрузки
CREATE TABLE IF NOT EXISTS etl.load_log (
    id serial PRIMARY KEY,
    table_name text NOT NULL,
    operation text NOT NULL,  -- START, BATCH, DONE, ERROR
    rows_loaded bigint DEFAULT 0,
    batch_size int,
    duration_sec numeric,
    speed_rows_per_sec numeric,
    message text,
    created_at timestamp DEFAULT NOW()
);

-- Результаты валидации
CREATE TABLE IF NOT EXISTS etl.validation_result (
    id serial PRIMARY KEY,
    table_name text NOT NULL,
    source_count bigint,
    target_count bigint,
    difference bigint,
    check_date timestamp DEFAULT NOW()
);

-- Стратегия загрузки таблиц
CREATE TABLE IF NOT EXISTS etl.table_strategy (
    table_name text PRIMARY KEY,
    category text,           -- A, B, C
    strategy text,           -- full, incremental, aggregate, skip
    date_column text,        -- CREATEDDATETIME, STARTDATE, etc.
    filter_days int,         -- сколько дней загружать
    priority int,            -- 1=быстро, 2=средние, 3=большие
    notes text
);
