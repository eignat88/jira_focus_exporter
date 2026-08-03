# Диагностика `sales_order`, run 45

## Куда положить

Скопируйте файлы:

```text
sales_order_run45_diagnostic.py
sales_order_run45_diagnostic_queries.sql
run_sales_order_run45_diagnostic.ps1
```

в каталог:

```text
D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2\monitoring
```

Результаты создаются в:

```text
D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2\logs\4
```

## Зависимость

```powershell
python -m pip install "psycopg[binary]"
```

При использовании `.env` дополнительно:

```powershell
python -m pip install python-dotenv
```

Поддерживаются стандартные переменные PostgreSQL:

```text
PGHOST
PGPORT
PGDATABASE
PGUSER
PGPASSWORD
```

## Безопасный запуск по умолчанию

`EXPLAIN ANALYZE` не выполняется.

```powershell
Set-Location "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2\monitoring"

.\run_sales_order_run45_diagnostic.ps1
```

Либо напрямую:

```powershell
python .\sales_order_run45_diagnostic.py
```

## Запуск ограниченного EXPLAIN ANALYZE для batch 100k

```powershell
.\run_sales_order_run45_diagnostic.ps1 -Analyze -AnalyzeBatches "100000"
```

## Запуск ANALYZE для 100k и 250k

Сначала оцените результат 100k. Затем при приемлемом времени и чтении buffers:

```powershell
.\run_sales_order_run45_diagnostic.ps1 `
    -Analyze `
    -AnalyzeBatches "100000,250000"
```

## Явная нижняя граница

```powershell
.\run_sales_order_run45_diagnostic.ps1 -RangeStart 5630000000
```

Без `-RangeStart` скрипт:

1. пытается взять нижнюю границу failed chunk из run 45;
2. если не получается — берёт минимальный числовой `recid` из `raw_ax.salestable`.

## Результаты

Главный файл:

```text
sales_order_run45_summary_YYYYMMDD_HHMMSS.csv
```

Дополнительно создаются:

- сравнение runs 35, 36, 37, 45;
- chunks run 45;
- структура `etl.load_run`;
- структура `etl.load_chunk`;
- `pg_stat_activity`;
- прогресс VACUUM;
- прогресс CREATE INDEX;
- планы 100k и 250k;
- JSON с полными планами;
- log;
- manifest.

## Ограничения безопасности

- транзакция начинается как `READ ONLY`;
- изменение данных не выполняется;
- установлен `lock_timeout = 5s`;
- установлен `statement_timeout`;
- `EXPLAIN ANALYZE` включается только флагом `--analyze`;
- отмена через `Ctrl+C` приводит к rollback read-only транзакции.
