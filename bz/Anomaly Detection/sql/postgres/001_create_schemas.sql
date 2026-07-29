-- ============================================================
-- Создание схем PostgreSQL
-- ============================================================

-- Сырые данные из AX
CREATE SCHEMA IF NOT EXISTS raw_ax;

-- Витрины и агрегаты
CREATE SCHEMA IF NOT EXISTS mart_ax;

-- Технические таблицы ETL
CREATE SCHEMA IF NOT EXISTS etl;

-- Результаты анализа
CREATE SCHEMA IF NOT EXISTS analysis;
