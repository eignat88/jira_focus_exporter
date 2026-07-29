# Алгоритм ETL v2: SQL Server → PostgreSQL

## Дерево вызовов main.py

```
main.py
├── configs.settings.get_settings()          # Настройки из env/.env/defaults
├── utils.logger.setup_etl_logging()         # Логирование в logs/
├── utils.retry.retry_on_error()             # Декоратор повтора (5→15→30→60s)
│
├── connectors.sqlserver.SQLServerConnector  # Подключение к SQL Server
│   ├── .connect()                           # pyodbc, Trusted_Connection
│   ├── .execute(sql)                        # cursor.execute()
│   ├── .get_table_columns(name)             # INFORMATION_SCHEMA.COLUMNS
│   └── .disconnect()
│
├── connectors.postgres.PostgresConnector    # Подключение к PostgreSQL
│   ├── .connect()                           # psycopg2, SET client_encoding=UTF8
│   ├── .create_schema()                     # CREATE SCHEMA IF NOT EXISTS raw_ax
│   ├── .create_etl_status_table()           # etl_status (legacy)
│   ├── .create_etl_status_v2()              # etl_run, etl_table_run, etl_chunk_run, etl_errors
│   ├── .create_etl_validation_table()       # etl_validation
│   ├── .start_run() / .finish_run()         # Метаданные запуска
│   ├── .start_table_run() / .finish_table_run()  # Статус загрузки таблицы
│   ├── .start_chunk() / .finish_chunk()     # Статус чанка
│   ├── .log_error()                         # Запись ошибок строк
│   ├── .copy_to_staging()                   # COPY во staging таблицу
│   ├── .merge_staging_to_target()           # UPSERT (DO NOTHING / DO UPDATE)
│   ├── .create_staging_table()              # Создание staging
│   ├── .ensure_recid_index()                # UNIQUE INDEX на RECID
│   ├── .create_indexes_after_load()         # INDEXES + ANALYZE после загрузки
│   ├── .extended_validation()               # COUNT, DISTINCT RECID, MIN/MAX
│   ├── .validate_by_ranges()                # Валидация по диапазонам RECID
│   └── .disconnect()
│
└── ДЛЯ КАЖДОЙ ТАБЛИЦЫ ИЗ config.yaml:
    │
    ├── [1/5] metadata.schema_reader.read_table_schema()
    │   └── ss.get_table_columns() → TYPE_MAP → pg_columns[]
    │
    ├── [2/5] metadata.column_analyzer.analyze_columns()
    │   ├── SELECT TOP 1000 * FROM table
    │   ├── Анализ NULL% и уникальных значений
    │   └── suggest_columns(auto_exclude=False) → рекомендация (без авто-исключения)
    │
    ├── [3/5] metadata.schema_reader.sync_target_schema()
    │   ├── CREATE TABLE IF NOT EXISTS (если новая)
    │   ├── Сравнить SS vs PG колонки
    │   └── ALTER TABLE ADD COLUMN (без DROP!)
    │
    └── [4/5] ЗАГРУЗКА ДАННЫХ:
        │
        ├── ЕСЛИ parallel.enabled=true:
        │   └── loader.parallel_loader.ParallelLoader
        │       ├── _get_recid_range()         # MIN/MAX RECID из SS
        │       ├── Динамические чанки          # 100-500 мелких чанков
        │       ├── _get_columns()             # SS + PG колонки
        │       ├── N × _fetch_worker_from_queue()  # Каждый: свой pyodbc conn
        │       │   └── Берёт чанк из chunk_queue
        │       │   └── SELECT WHERE RECID > start AND RECID <= end
        │       └── 1 × _write_worker()        # Queue → staging → UPSERT
        │
        └── ЕСЛИ parallel.enabled=false:
            └── loader.batch_loader.load_table()
                ├── get_last_recid() / get_last_modified()  # Resume point
                ├── async COUNT(*) × 2         # Фоновые потоки для валидации
                ├── ЦИКЛ:
                │   ├── _build_batch_sql()     # SELECT TOP N WHERE RECID > last
                │   ├── ss.execute(sql)         # Fetch из SQL Server
                │   ├── cursor.fetchmany(5000)  # Пакетная выборка
                │   ├── _build_buffer()         # Tab-delimited StringIO
                │   ├── BEGIN TRANSACTION
                │   │   ├── copy_to_staging()   # COPY во staging
                │   │   ├── merge_staging()     # UPSERT (ON CONFLICT)
                │   │   └── update_etl_status() # CHECKPOINT
                │   └── COMMIT
                ├── extended_validation()       # COUNT, DISTINCT RECID, MIN/MAX
                └── create_indexes_after_load() # INDEXES + ANALYZE
```

## Режимы загрузки (load_mode)

| Режим | Описание | Начальная точка | Очистка | Повторный запуск |
|-------|----------|-----------------|---------|-------------------|
| `full` | Полная первоначальная загрузка | 0 | CREATE TABLE IF NOT EXISTS | Пропуск если есть данные |
| `resume` | Продолжение прерванной загрузки | last_recid из etl_status | Нет | Продолжает с checkpoint |
| `incremental` | Загрузка новых/изменённых записей | watermark (modifiedDateTime) | Нет | UPSERT по ключу |
| `reload` | Очистка и повторная загрузка | 0 | TRUNCATE | Полная перезагрузка |

## UPSERT (идемпотентность)

```
Для каждой батчи:
1. COPY → staging таблица (_staging_{table_name})
2. INSERT INTO target SELECT FROM staging ON CONFLICT (recid) DO NOTHING
3. DROP staging

Для incremental:
4. ON CONFLICT (recid) DO UPDATE SET col1 = EXCLUDED.col1, ...
```

## Транзакционные границы

```
BEGIN
    COPY batch → staging
    UPSERT staging → target
    UPDATE etl_status SET last_recid = ...
COMBIT
```

## Безопасное время (incremental)

```sql
WHERE modifiedDateTime > ?
   OR (modifiedDateTime = ? AND RECID > ?)
ORDER BY modifiedDateTime, RECID
```

## Retry-политика

- **Повторять:** обрыв соединения, timeout, deadlock, временная недоступность PostgreSQL
- **НЕ повторять:** SQL-синтаксис, несовместимый тип, отсутствующая колонка, уникальность
- **Задержки:** 5s → 15s → 30s → 60s
- **Максимум:** 3 попытки

## Расширенная валидация

- `COUNT(*)` — общее количество строк
- `COUNT(DISTINCT RECID)` — уникальность ключа
- `MIN(RECID)`, `MAX(RECID)` — диапазон
- `NULL count in recid` — проверка на null
- Валидация по диапазонам RECID (10M строк на диапазон)

## Модель статусов (ETL v2)

### etl_run
- run_id, started_at, finished_at, status, source_server, source_database, target_database

### etl_table_run
- run_id, table_name, load_mode, status, started_at, finished_at
- source_count, target_count, inserted_count, updated_count, rejected_count
- last_recid, last_modified_datetime

### etl_chunk_run
- run_id, table_name, chunk_id, range_from, range_to, status
- rows_read, rows_written, error_message

### etl_errors
- run_id, table_name, recid, batch_start, batch_end, error_type, error_message

### Статусы
`PENDING → RUNNING → DONE / DONE_WITH_ERRORS / FAILED / SKIPPED`

## Динамические чанки (parallel loader)

- 100-500 мелких чанков (вместо N фиксированных диапазонов)
- chunk_queue: воркер берёт следующий доступный чанк
- Балансировка нагрузки между воркерами

## TYPE_MAP (расширенный)

| SQL Server | PostgreSQL | Примечание |
|------------|------------|------------|
| `datetime` | `text` | Храним как text для AX-дат (0001-01-01) |
| `datetime2` | `text` | Аналогично |
| `datetimeoffset` | `text` | |
| `uniqueidentifier` | `text` | Храним как text |
| `money` | `numeric(19,4)` | |
| `float` | `double precision` | NaN/Infinity → empty |

## Edge cases

- **NaN/Infinity** → пустая строка (PostgreSQL COPY отвергает)
- **AX-даты** (0001-01-01, 1900-01-01) → пустая строка
- **\\x00 null bytes** → удалены
- **Control characters** (tab, newline) → заменены

## Безопасность

- Пароли в `.env` (не в config.yaml)
- `.env.example` — шаблон
- `settings.py` загружает `.env` автоматически

## Запуск

```bash
cd D:\py_pro\jira_focus_exporter\bz\Anomaly Detection
python -B ax_to_postgres_etl/main.py
```

Флаг `-B` отключает кэширование .pyc.

### Переменные окружения

```bash
DB_PASSWORD=your_password
ETL_LOAD_MODE=full
ETL_AUTO_EXCLUDE_COLUMNS=false
ETL_PARALLEL_ENABLED=true
ETL_PARALLEL_WORKERS=4
```
