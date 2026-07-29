# Экспорт данных с правильной кодировкой (UTF-8)

## Проблема

Имена员工 в `raw_ax.wms_journalwarehouseoperationtable` повреждены:
- `????????-??????? 10` вместо `Иванов-Петров 10`
- Причина: ETL заменял русские символы на `?`

## Решение

### Шаг 1: Экспорт из SQL Server в UTF-8 CSV

```bash
# Запустить из консоли с Windows Auth:
runas /netonly /user:ALKOR\ignatchenko-adm cmd

# Затем:
cd /d D:\py_pro\jira_focus_exporter\bz\Anomaly Detection\ax_to_postgres_etl
python export_utf8.py
```

Результат: `generated_sql/UTF8_EXPORT/WMS_JOURNALWAREHOUSEOPERATIONTABLE_utf8.csv`

### Шаг 2: Загрузка в PostgreSQL

```bash
python load_utf8_csv.py
```

Результат: Таблица `raw_ax.wms_journalwarehouseoperationtable` обновлена с правильной кодировкой.

### Шаг 3: Обновление DDS и mart

```bash
# Очистка дублей
psql -h localhost -U postgres -d wms_analysis -c "
DELETE FROM dds.warehouse_operation WHERE operation_id NOT IN (
    SELECT MIN(operation_id) FROM dds.warehouse_operation 
    GROUP BY employee_id, start_time, operation_type
);
"

# Пересоздание mart
psql -h localhost -U postgres -d wms_analysis -f sql/postgres/005_populate_dds.sql
psql -h localhost -U postgres -d wms_analysis -f sql/postgres/006_populate_mart.sql
```

## Файлы

| Файл | Описание |
|------|----------|
| `export_utf8.py` | Экспорт из SQL Server в UTF-8 CSV |
| `load_utf8_csv.py` | Загрузка CSV в PostgreSQL |
| `generated_sql/UTF8_EXPORT/` | Директория с CSV файлами |

## Проверка

После загрузки проверьте имена员工:

```sql
SELECT DISTINCT employee_name 
FROM mart.daily_operations 
WHERE employee_name NOT LIKE '%?%' 
LIMIT 10;
```
