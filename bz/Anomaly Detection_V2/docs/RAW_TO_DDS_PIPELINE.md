# RAW → DDS Pipeline (Unified ETL)

**Date**: 2026-07-23
**Status**: READY
**Task**: b128.txt - Unified ETL Architecture

---

## 1. Архитектура

```
dds_cli.py
    ↓
PipelineRunner (shared)
    ↓
RunManager + ChunkManager + RetryPolicy (existing)
    ↓
RawToDdsAdapter (new)
    ↓
INSERT INTO dds ... SELECT FROM raw_ax
```

### Ключевой принцип

Данные **не передаются через Python**. Преобразование выполняется внутри PostgreSQL:

```sql
INSERT INTO dds.<target> (...)
SELECT ...
FROM raw_ax.<source>
WHERE recid > %(start_key)s AND recid <= %(end_key)s;
```

---

## 2. Структура файлов

```
ax_to_postgres_etl/
├── core/
│   ├── run_manager.py          # Управление запусками
│   ├── chunk_manager.py        # Управление пакетами
│   ├── retry.py                # Политика повторов
│   └── postgres_runtime.py     # Соединения, heartbeat
│
├── pipelines/
│   ├── __init__.py
│   ├── contracts.py            # PipelineSpec, LoadAdapter
│   ├── runner.py               # PipelineRunner (общий)
│   ├── raw_to_dds.py           # RawToDdsAdapter
│   └── dds_cli.py              # CLI точка входа
│
└── configs/
    └── raw_to_dds.yaml         # Конфигурация этапов
```

---

## 3. Компоненты

### 3.1. PipelineRunner

Общая оркестрация для SQL Server → RAW и RAW → DDS.

**Обязанности:**
- Advisory lock
- Создание/восстановление запуска
- Управление chunks
- Retry с exponential backoff
- Heartbeat
- Обработка отмены (Ctrl+C, QueryCanceled)
- Exit codes: 0=OK, 1=FAILED, 2=BLOCKED, 130=CANCELLED

**Не знает:**
- Названия таблиц RAW/DDS
- SQL преобразований
- Состав колонок

### 3.2. RawToDdsAdapter

Адаптер для PostgreSQL → PostgreSQL преобразований.

**Обязанности:**
- Определение границ (MIN/MAX recid)
- Формирование ranges
- Выполнение INSERT ... SELECT
- Валидация после загрузки
- ANALYZE после этапа

**Стратегии:**
- `numeric_range` - для больших таблиц с числовым ключом
- `full_table` - для небольших таблиц

### 3.3. PostgresRuntime

Управление изолированными соединениями:

| Соединение | Назначение |
|------------|------------|
| data_conn | INSERT ... SELECT, COUNT,边界 |
| metadata_conn | Статусы, chunks, checkpoint |
| heartbeat_conn | Обновление heartbeat_at |
| lock_conn | Advisory lock |

### 3.4. HeartbeatThread

Отдельный поток для обновления heartbeat:
- Использует отдельное соединение
- autocommit = True
- Интервал: 15 секунд
- Автопереподключение при ошибке

---

## 4. Конфигурация (YAML)

```yaml
pipeline:
  name: raw_to_dds
  advisory_lock_key: 1734500127
  default_batch_size: 250000
  heartbeat_interval_seconds: 15
  max_attempts: 3

stages:
  - name: serial_mark
    source:
      schema: raw_ax
      table: alk_markserial
      key_column: recid
    target:
      schema: dds
      table: serial_mark
      conflict_key: rec_id
    execution:
      strategy: postgres_insert_select
      chunk_strategy: numeric_range
      batch_size: 250000
      count_mode: estimate
    columns:
      - target: rec_id
        expression: "src.recid::bigint"
      - target: gtin
        expression: "src.gtin"
      # ... другие колонки
```

---

## 5. Режимы запуска

| Режим | Команда | Описание |
|-------|---------|----------|
| preflight | `--mode preflight` | Проверка без загрузки |
| full | `--mode full` | Полная загрузка |
| resume | `--mode resume` | Продолжение с последнего chunk |
| restart-stage | `--mode restart-stage --stage X` | Перезапуск этапа |
| validate-only | `--mode validate-only` | Только проверки |
| status | `--mode status` | Текущее состояние |

---

## 6. Команды запуска

### Python

```bash
# Preflight
python -m ax_to_postgres_etl.pipelines.dds_cli --mode preflight

# Resume конкретного этапа
python -m ax_to_postgres_etl.pipelines.dds_cli \
    --mode resume \
    --stage serial_mark \
    --batch-size 250000 \
    --count-mode estimate

# Полная загрузка
python -m ax_to_postgres_etl.pipelines.dds_cli --mode full

# Статус
python -m ax_to_postgres_etl.pipelines.dds_cli --mode status
```

### PowerShell

```powershell
# Preflight
.\run_raw_to_dds.ps1 -Mode preflight

# Resume
.\run_raw_to_dds.ps1 -Mode resume -Stage serial_mark -BatchSize 250000

# Full
.\run_raw_to_dds.ps1 -Mode full

# Status
.\run_raw_to_dds.ps1 -Mode status
```

---

## 7. Стратегии загрузки

### 7.1. numeric_range

Для больших таблиц с числовым ключом:

```sql
WHERE recid > %(start_key)s AND recid <= %(end_key)s
```

Таблицы: `alk_markserial`, `wmspickingroute`, `lfl_scspacktask`, `wmsordertrans`

### 7.2. full_table

Для небольших таблиц - один chunk:

```sql
INSERT INTO dds.<target> SELECT ... FROM raw_ax.<source>
```

Таблицы: `salestable`, `purchtable`

---

## 8. Идемпотентность

```sql
ON CONFLICT (rec_id) DO NOTHING;
```

Повторный chunk не создаёт дубли.

---

## 9. Мониторинг

### Таблицы

| Таблица | Назначение |
|---------|------------|
| `etl.load_run` | Информация о запусках |
| `etl.load_chunk` | Состояние пакетов |

### Проверка статуса

```sql
SELECT run_id, status, source_table, target_table, 
       completed_chunks, total_chunks
FROM etl.load_run
WHERE pipeline_name = 'raw_to_dds'
ORDER BY run_id DESC;
```

---

## 10. Переход с DDS v3

### Этап 1: Подготовка
- Новая архитектура реализована
- `run_dds_load_v3.py` оставлен как rollback

### Этап 2: Тестирование
- Тест на малой таблице
- Сравнение результатов

### Этап 3: Переключение
```powershell
# Было
.\run_dds_load_v3.ps1 -Mode resume

# Стало
.\run_raw_to_dds.ps1 -Mode resume
```

### Этап 4: Legacy cleanup
- Архивация старого кода
- Удаление после подтверждения

---

## 11. Сравнение с DDS v3

| Аспект | DDS v3 | Unified ETL |
|--------|--------|-------------|
| Инфраструктура | Дублированная | Общая |
| Мониторинг | etl.pipeline_run | etl.load_run |
| Пакеты | etl.stage_batch | etl.load_chunk |
| Heartbeat | Свой | PostgresRuntime |
| Advisory lock | Свой | RunManager |
| Конфигурация | Python dict | YAML |
| SQL | Встроен в Python | Отдельные файлы |

---

## 12. Преимущества

1. **Единая инфраструктура** - один код для обоих pipeline
2. **Данные не передаются через Python** - INSERT ... SELECT
3. **YAML конфигурация** -легко добавлять этапы
4. **Переиспользование** - RunManager, ChunkManager, RetryPolicy
5. **Безопасность** - валидация SQL идентификаторов
6. **Мониторинг** - единая модель ETL

---

## 13. Known Issues

- Требуется `pip install pyyaml` для чтения конфигурации
- Advisory lock key захардкожен в YAML (нужно сделать стабильным)
- Heartbeat не имеет max_failures лимита

---

## 14. Next Steps

1. Добавить unit tests
2. Добавить integration tests
3. Реализовать `validate-only` режим полностью
4. Добавить `ANALYZE` после каждого этапа
5. Реализовать миграцию истории с DDS v3
