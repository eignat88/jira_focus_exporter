# DDS Load v2 - Исправление схемы мониторинга

**Date**: 2026-07-22
**Issue**: Столбец "heartbeat_at" в таблице "stage_progress" не существует

---

## 1. Проблема

При запуске `restart_stage` скрипт v2 падал с ошибкой:

```
столбец "heartbeat_at" в таблице "stage_progress" не существует
```

Причина: Python-скрипт v2 ожидает новую структуру таблицы `etl.stage_progress`, но миграция `00_prepare_monitoring_v2.sql` не была применена.

---

## 2. Решение

### 2.1. Добавление недостающих колонок

```sql
ALTER TABLE etl.stage_progress
    ADD COLUMN IF NOT EXISTS heartbeat_at timestamptz,
    ADD COLUMN IF NOT EXISTS last_completed_batch integer,
    ADD COLUMN IF NOT EXISTS last_completed_recid bigint,
    ADD COLUMN IF NOT EXISTS total_batches integer,
    ADD COLUMN IF NOT EXISTS completed_batches integer DEFAULT 0,
    ADD COLUMN IF NOT EXISTS failed_batches integer DEFAULT 0,
    ADD COLUMN IF NOT EXISTS batch_size integer;
```

### 2.2. Создание таблицы stage_batch

```sql
CREATE TABLE IF NOT EXISTS etl.stage_batch (
    batch_id             bigserial PRIMARY KEY,
    run_id               bigint NOT NULL,
    stage_no             integer NOT NULL,
    stage_name           text NOT NULL,
    source_table         text NOT NULL,
    target_table         text NOT NULL,
    batch_no             integer NOT NULL,
    start_recid          bigint,
    end_recid            bigint,
    status               text NOT NULL DEFAULT 'PENDING',
    attempt_no           integer NOT NULL DEFAULT 1,
    rows_selected        bigint DEFAULT 0,
    rows_inserted        bigint DEFAULT 0,
    rows_conflicted      bigint DEFAULT 0,
    started_at           timestamptz,
    updated_at           timestamptz,
    completed_at         timestamptz,
    duration_seconds     numeric,
    rows_per_second      numeric,
    error_message        text,
    CONSTRAINT uq_stage_batch UNIQUE (run_id, stage_no, batch_no)
);
```

### 2.3. Исправление зависшего pipeline

```sql
UPDATE etl.pipeline_run
SET status = 'FAILED',
    completed_at = CURRENT_TIMESTAMP,
    updated_at = CURRENT_TIMESTAMP,
    error_message = 'Schema migration applied - restarting'
WHERE status = 'RUNNING';
```

---

## 3. Проверка

### Колонки stage_progress

```sql
SELECT column_name 
FROM information_schema.columns 
WHERE table_schema = 'etl' 
  AND table_name = 'stage_progress'
ORDER BY ordinal_position;
```

Ожидаемые колонки:
- `heartbeat_at`
- `last_completed_batch`
- `last_completed_recid`
- `total_batches`
- `completed_batches`
- `failed_batches`
- `batch_size`

### Статус pipeline

```sql
SELECT run_id, status, error_message 
FROM etl.pipeline_run 
ORDER BY run_id DESC;
```

---

## 4. Следующие шаги

```powershell
# Preflight
.\run_dds_load_v2.ps1 -Mode preflight

# Тестовый запуск
.\run_dds_load_v2.ps1 -Mode restart_stage -Stage serial_mark -BatchSize 100000

# Мониторинг
.\watch_dds_progress_v2.ps1
```
