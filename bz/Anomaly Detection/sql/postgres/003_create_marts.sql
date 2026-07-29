-- ============================================================
-- Создание витрин (mart_ax)
-- ============================================================

-- Витрина: конкурентные КМ по товарам
CREATE MATERIALIZED VIEW IF NOT EXISTS mart_ax.marking_codes_by_item AS
SELECT
    ITEMID,
    COUNT(*) AS total_km,
    SUM(CASE WHEN COMPETITOR = 1 THEN 1 ELSE 0 END) AS competitor_km,
    SUM(CASE WHEN BLOCKED = 1 THEN 1 ELSE 0 END) AS blocked_km,
    MIN(CREATEDDATETIME) AS first_km_date,
    MAX(CREATEDDATETIME) AS last_km_date
FROM raw_ax.lfl_markingcodetable
GROUP BY ITEMID;

-- Витрина: КМ по дням
CREATE MATERIALIZED VIEW IF NOT EXISTS mart_ax.marking_codes_by_day AS
SELECT
    CAST(CREATEDDATETIME AS DATE) AS dt,
    COUNT(*) AS total_km,
    SUM(CASE WHEN COMPETITOR = 1 THEN 1 ELSE 0 END) AS competitor_km,
    SUM(CASE WHEN BLOCKED = 1 THEN 1 ELSE 0 END) AS blocked_km
FROM raw_ax.lfl_markingcodetable
GROUP BY CAST(CREATEDDATETIME AS DATE);

-- Витрина: ошибки по типам
CREATE MATERIALIZED VIEW IF NOT EXISTS mart_ax.diff_act_by_type AS
SELECT
    ACTTYPE,
    COUNT(*) AS total_errors,
    MIN(CREATEDDATETIME) AS first_error,
    MAX(CREATEDDATETIME) AS last_error
FROM raw_ax.wms_pickdiffactline
GROUP BY ACTTYPE;

-- Витрина: активность сотрудников по дням
CREATE MATERIALIZED VIEW IF NOT EXISTS mart_ax.journal_by_day AS
SELECT
    EMPLID,
    NAMEALIAS,
    CAST(STARTDATE AS DATE) AS dt,
    COUNT(*) AS operations_count,
    SUM(QTY) AS total_qty,
    AVG(DURATIONOPERATION) AS avg_duration
FROM raw_ax.wms_journalwarehouseoperationtable
GROUP BY EMPLID, NAMEALIAS, CAST(STARTDATE AS DATE);

-- Витрина: маршруты по статусам
CREATE MATERIALIZED VIEW IF NOT EXISTS mart_ax.pickingroute_by_status AS
SELECT
    EXPEDITIONSTATUS,
    COUNT(*) AS route_count,
    MIN(STARTDATETIME) AS first_route,
    MAX(STARTDATETIME) AS last_route
FROM raw_ax.wmspickingroute
GROUP BY EXPEDITIONSTATUS;
