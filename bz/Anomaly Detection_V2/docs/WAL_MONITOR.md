# PostgreSQL WAL Monitor

**Date**: 2026-07-23
**Task**: b136.txt - Мониторинг скорости ETL через PostgreSQL WAL

---

## 1. Описание

Система мониторинга производительности загрузок RAW →.dds через PostgreSQL WAL (Write-Ahead Log).

Система состоит из двух компонентов:
1. **Python Collector** - сбор данных каждую минуту
2. **Jupyter Notebook** - анализ и визуализация

---

## 2. Структура файлов

```
monitoring/
├── postgres_wal_monitor.py        # Python collector
├── wal_monitor_config.yaml        # Конфигурация
├── postgres_monitor.ipynb         # Основной Notebook
├── notebooks/
│   └── WAL_ETL_Analytics.ipynb    # Аналитический Notebook
├── collectors/
│   ├── wal_monitor.py             # WAL мониторинг
│   ├── activity_monitor.py        # Мониторинг процессов
│   └── table_monitor.py           # Мониторинг таблиц
├── data/                          # CSV файлы
│   └── wal_history_*.csv
└── output/                        # Отчёты
    └── WAL_Report_*.html
```

---

## 3. Конфигурация

```yaml
postgres:
  host: localhost
  port: 5432
  database: wms_analysis
  user: postgres
  password: "123"

monitor:
  interval_seconds: 60

output:
  file: data/wal_history.csv
```

---

## 4. Запуск мониторинга

### Базовый запуск

```powershell
cd "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection"
python monitoring\postgres_wal_monitor.py
```

### С параметрами

```powershell
# Интервал 30 секунд, длительность 8 часов
python monitoring\postgres_wal_monitor.py --interval 30 --duration 8h

# Мониторинг конкретного PID
python monitoring\postgres_wal_monitor.py --pid 21904

# Фильтр по запросу
python monitoring\postgres_wal_monitor.py --filter alk_markserial
```

---

## 5. Формат CSV

### wal_history.csv

| Поле | Описание |
|------|----------|
| timestamp | Время замера |
| wal_records | Количество WAL записей |
| wal_bytes | Размер WAL в байтах |
| wal_mb | Размер WAL в MB |
| wal_gb | Размер WAL в GB |
| wal_delta_mb | Прирост WAL |
| wal_speed_mb_min | Скорость записи |
| wal_buffers_full | Переполнение буферов |

### activity_history.csv

| Поле | Описание |
|------|----------|
| timestamp | Время замера |
| pid | ID процесса |
| application_name | Имя приложения |
| state | Состояние |
| wait_event_type | Тип ожидания |
| wait_event | Событие ожидания |
| runtime | Время выполнения |
| query | SQL запрос |

---

## 6. Аналитика в Jupyter Notebook

### Загрузка данных

```python
import pandas as pd

wal_df = pd.read_csv('data/wal_history_*.csv', parse_dates=['timestamp'])
```

### Анализ WAL

```python
print(f"WAL: {wal_df['wal_gb'].max():.2f} GB")
print(f"Speed: {wal_df['wal_speed_mb_min'].mean():.0f} MB/min")
print(f"Max speed: {wal_df['wal_speed_mb_min'].max():.0f} MB/min")
```

### Прогноз завершения

```python
from notebooks.WAL_ETL_Analytics import estimate_completion

estimate_completion(wal_df, target_wal_gb=200)

# Вывод:
# ============================================================
# ETL OPERATION ANALYSIS
# ============================================================
# Started: 2026-07-23 15:53:00
# Runtime: 190 minutes (3.2 hours)
# Current WAL: 176.50 GB
# WAL Generated: 12.50 GB
# Average Speed: 850 MB/min
#
# Target: 200 GB
# Remaining: 23.50 GB
# ETA: 2026-07-23 16:21:00
# ============================================================
```

### Анализ узких мест

```python
from notebooks.WAL_ETL_Analytics import analyze_bottlenecks

analyze_bottlenecks(activity_df)

# Вывод:
# ============================================================
# BOTTLENECK ANALYSIS
# ============================================================
# Wait Events Distribution:
#   WALWriteLock: 150
#   Lock: 45
#   IO: 12
#
# Detected Bottleneck: WALWriteLock
# Recommendation: Reduce transaction size or use batch update
# ============================================================
```

---

## 7. Визуализация

### График 1: Рост WAL

```
GB
|
|             *
|          *
|       *
|    *
|________________
      time
```

### График 2: Скорость WAL

```
MB/min

800 |
600 |       *
400 |    *
200 | *
```

### График 3: Активные процессы

```
count
|
|    *
|  *   *
| *     *
|*       *
|___________
    time
```

---

## 8. Экспорт отчёта

```python
from notebooks.WAL_ETL_Analytics import export_html_report

report_file = export_html_report(wal_df, activity_df)

# Сохраняет: output/WAL_Report_20260723.html
```

---

## 9. RAW → DDS Сравнение

```python
from notebooks.WAL_ETL_Analytics import compare_raw_dds

compare_raw_dds(wal_df, raw_rows=151_817_640)

# Вывод:
# ============================================================
# RAW → DDS COMPARISON
# ============================================================
# WAL Generated: 12.50 GB
# Runtime: 190 minutes
#
# RAW Rows: 151,817,640
# WAL per million rows: 82.35 GB
# Rows per minute: 799,040
# ============================================================
```

---

## 10. Ответы на вопросы

| Вопрос | Как узнать |
|--------|------------|
| Сколько времени осталось? | `estimate_completion(wal_df, target_wal_gb)` |
| Скорость WAL? | `wal_df['wal_speed_mb_min'].mean()` |
| Когда пик нагрузки? | `wal_df.loc[wal_df['wal_speed_mb_min'].idxmax()]` |
| Bottleneck? | `analyze_bottlenecks(activity_df)` |
| Можно ли запускать загрузку? | Анализ WAL speed + активных процессов |
| Оптимальный batch size? | Зависит от WAL speed и IO |

---

## 11. Рекомендации по batch size

| WAL Speed | Рекомендация |
|-----------|--------------|
| < 100 MB/min | batch_size: 100,000 |
| 100-500 MB/min | batch_size: 250,000 |
| 500-1000 MB/min | batch_size: 500,000 |
| > 1000 MB/min | batch_size: 1,000,000 |

---

## 12. Зависимости

```
psycopg2
pandas
matplotlib
pyyaml
```

---

## 13. Файлы

| Файл | Назначение |
|------|------------|
| `monitoring/postgres_wal_monitor.py` | Python collector |
| `monitoring/wal_monitor_config.yaml` | Конфигурация |
| `monitoring/notebooks/WAL_ETL_Analytics.ipynb` | Аналитика |
| `monitoring/postgres_monitor.ipynb` | Основной Notebook |
