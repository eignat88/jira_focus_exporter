# T34: NORMALIZED_STAGING for alk_markserial

**Date**: 2026-07-24
**Status**: READY
**Task**: b156.txt - Реализация NORMALIZED_STAGING

---

## 1. Описание

Реализация стратегии **RAW → normalized staging → DDS** для таблицы `raw_ax.alk_markserial`.

### Проблема

Таблица содержит ~152 млн строк (79 GB) с ключом `recid` типа `text`. Существующий индекс использует лексикографический порядок, что делает невозможным числовое чанкирование:

```sql
WHERE trim(recid)::bigint > :last_recid  -- Seq Scan!
```

### Решение

```
SQL Server
    ↓
raw_ax.alk_markserial (recid text)
    ↓ lexical chunks (Index Only Scan)
stage_ax.alk_markserial_normalized (recid_text, recid_bigint)
    ↓ numeric chunks (Index Scan)
dds.marking_code (recid bigint)
```

---

## 2. Структура файлов

```
sql/postgres/staging/
├── 001_create_stage_schema.sql    # Создание schema и таблиц
├── 002_load_chunk.sql             # Загрузка одного чанка
├── 003_create_numeric_index.sql   # Создание числового индекса
└── 004_validate_staging.sql       # Валидация

config/
└── raw_to_dds.yaml                # Конфигурация этапов

run_staging_normalization.ps1      # PowerShell launcher
```

---

## 3. Этапы выполнения

### Этап 1: Предварительная проверка

```powershell
python -m ax_to_postgres_etl.diagnostics.raw_strategy_cli --mode scan --table alk_markserial
```

### Этап 2: Создание staging schema

```sql
CREATE SCHEMA IF NOT EXISTS stage_ax;

CREATE TABLE IF NOT EXISTS stage_ax.alk_markserial_normalized (
    recid_text      text   NOT NULL,
    recid_bigint    bigint NOT NULL,
    gtin            text,
    serialnumber    text,
    itemid          text,
    markcode        text,
    createddatetime timestamptz,
    modifieddatetime timestamptz,
    createdby       text,
    modifiedby      text,
    source_loaded_at timestamptz,
    normalized_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT alk_markserial_normalized_recid_text_uq UNIQUE (recid_text)
);
```

### Этап 3: Загрузка staging (пакетами)

```sql
WITH source_chunk AS MATERIALIZED (
    SELECT recid, gtin, serialid, itemid, markcode, ...
    FROM raw_ax.alk_markserial
    WHERE recid > %(last_text_recid)s
    ORDER BY recid
    LIMIT %(batch_size)s
)
INSERT INTO stage_ax.alk_markserial_normalized (...)
SELECT recid, btrim(recid)::bigint, ...
FROM source_chunk
WHERE recid IS NOT NULL AND btrim(recid) ~ '^[0-9]+$'
ON CONFLICT (recid_text) DO NOTHING;
```

### Этап 4: Создание числового индекса

```sql
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS
    idx_alk_markserial_normalized_recid_bigint
ON stage_ax.alk_markserial_normalized (recid_bigint);
```

### Этап 5: Валидация

```sql
EXPLAIN SELECT recid_bigint
FROM stage_ax.alk_markserial_normalized
WHERE recid_bigint > 1000000000
ORDER BY recid_bigint
LIMIT 100000;
-- Ожидается: Index Scan
```

### Этап 6: Загрузка в DDS

```sql
INSERT INTO dds.marking_code (recid, ...)
SELECT recid_bigint, ...
FROM stage_ax.alk_markserial_normalized
WHERE recid_bigint > %(last_recid_bigint)s
ORDER BY recid_bigint
LIMIT %(batch_size)s
ON CONFLICT (recid) DO NOTHING;
```

---

## 4. Команды запуска

### PowerShell

```powershell
# Preflight
.\run_staging_normalization.ps1 -Mode preflight

# Полная загрузка
.\run_staging_normalization.ps1 -Mode full -BatchSize 250000

# Resume
.\run_staging_normalization.ps1 -Mode resume

# Валидация
.\run_staging_normalization.ps1 -Mode validate_only
```

### Python

```powershell
# Diagnostics
python -m ax_to_postgres_etl.diagnostics.raw_strategy_cli --mode scan --table alk_markserial

# Normalization
python -m ax_to_postgres_etl.pipelines.dds_cli --mode full --stage serial_mark_normalization --batch-size 250000

# Resume
python -m ax_to_postgres_etl.pipelines.dds_cli --mode resume --stage serial_mark_normalization
```

---

## 5. YAML конфигурация

```yaml
stages:
  - no: 0
    name: serial_mark_normalization
    source:
      schema: raw_ax
      table: alk_markserial
      key_column: recid
      key_type: text
      chunk_order: lexical
    target:
      schema: stage_ax
      table: alk_markserial_normalized
    normalization:
      source_key: recid
      normalized_key: recid_bigint
      normalized_type: bigint
    execution:
      strategy: normalized_staging
      chunk_strategy: lexical_range
      batch_size: 250000
```

---

## 6. Идемпотентность

```sql
ON CONFLICT (recid_text) DO NOTHING;
```

Повторное выполнение чанка не создаст дубли.

---

## 7. Обработка ошибок

Невалидные `recid` записываются в:

```sql
etl.normalization_error
```

Пример:
```sql
INSERT INTO etl.normalization_error (stage_name, source_key, error_code, error_message)
SELECT 'serial_mark_normalization', recid, 'INVALID_BIGINT_RECID', 'Cannot convert'
FROM raw_ax.alk_markserial
WHERE recid IS NULL OR btrim(recid) !~ '^[0-9]+$';
```

---

## 8. Resume

| Этап | Watermark |
|------|-----------|
| RAW → staging | `last_text_recid` |
| staging → DDS | `last_recid_bigint` |

```sql
-- Resume staging
WHERE recid > %(last_text_recid)s

-- Resume DDS
WHERE recid_bigint > %(last_recid_bigint)s
```

---

## 9. Размер чанка

Рекомендуемый стартовый размер:

| Размер | Когда использовать |
|--------|-------------------|
| 100,000 | Начальное тестирование |
| 250,000 | Стандартный режим |
| 500,000 | После подтверждения производительности |
| 1,000,000 | Только после замеров |

---

## 10. Требования к диску

Для таблицы 79 GB:

| Компонент | Оценка |
|-----------|--------|
| Staging data | ~80 GB |
| Staging indexes | ~10 GB |
| Temp files | ~20 GB |
| WAL | ~20 GB |
| **Итого** | **~130 GB** |

Рекомендуется не менее **150 GB** свободного места.

---

## 11. Risks

| Риск | Митигация |
|------|-----------|
| Блокировки | Чанковые INSERT, row-level locks |
| WAL | Ограничение размером чанка |
| Dead tuples | Нет массового UPDATE |
| Disk space | Проверка перед запуском |
| Rollback | Только текущий чанк |

---

## 12. Мониторинг

```sql
-- Прогресс загрузки
SELECT chunk_no, status, rows_inserted, start_recid, end_recid
FROM etl.stage_batch WHERE run_id = :run_id ORDER BY chunk_no;

-- Ошибки нормализации
SELECT COUNT(*) FROM etl.normalization_error
WHERE stage_name = 'serial_mark_normalization';

-- Статус индекса
SELECT indexname, indisvalid, indisready
FROM pg_indexes WHERE tablename = 'alk_markserial_normalized';
```
