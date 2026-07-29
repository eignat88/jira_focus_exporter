# План доработки: Стабилизация Parallel ETL V2

## Источники требований

1. `Завершать каждый чанк сразу после его полной загрузки.docx`
2. `стабилизация Parallel ETL V2 и механизма Resume.docx`

## Текущее состояние (что уже есть)

| Компонент | Файл | Статус |
|-----------|------|--------|
| ParallelLoaderV2 | `loader/parallel_loader_v2.py` | Базовый, есть критичные баги |
| ChunkManager | `core/chunk_manager.py` | Базовый, нужна доработка |
| RunManager | `core/run_manager.py` | Базовый |
| escape_copy_text | `parallel_loader_v2.py:46` | Есть, использует FORMAT TEXT |
| RetryPolicy | `core/retry.py` | Есть |

---

## ЭТАП 1: Протокол сообщений worker → writer (P0)

**Проблема:** Сейчас worker передаёт dict с `is_last_batch`, writer помечает ВСЕ чанки completed после SENTINEL.

**Решение:** Ввести типизированные сообщения.

### Задачи:

1.1. Создать `core/messages.py` с dataclasses:
```python
@dataclass(frozen=True)
class DataBatch:
    chunk_id: int
    chunk_no: int
    rows: Sequence[Sequence[Any]]
    last_processed_key: int

@dataclass(frozen=True)
class ChunkFinished:
    chunk_id: int
    chunk_no: int
    rows_read: int
    last_processed_key: int | None

@dataclass(frozen=True)
class ChunkFailed:
    chunk_id: int
    chunk_no: int
    error_type: str
    error_message: str
    rows_read: int
    last_processed_key: int | None
```

1.2. Изменить `_fetch_worker_v2`:
- Отправлять `DataBatch` вместо dict
- После завершения диапазона отправлять `ChunkFinished`
- При ошибке отправлять `ChunkFailed`
- **НЕ отправлять** `ChunkFinished` при `stop_event` или ошибке

1.3. Изменить `_write_worker_v2`:
- Принимать типизированные сообщения
- Обрабатывать `ChunkFinished` → атомарная фиксация чанка
- Обрабатывать `ChunkFailed` → пометить чанк как failed/retry

**Файлы:** `core/messages.py`, `loader/parallel_loader_v2.py`

---

## ЭТАП 2: Staging таблица с _etl_chunk_id (P0)

**Проблема:** Невозможно фиксировать данные конкретного чанка атомарно.

**Решение:** Добавить техническую колонку `_etl_chunk_id`.

### Задачи:

2.1. Изменить создание staging таблицы:
```sql
CREATE UNLOGGED TABLE raw_ax._staging_alk_markserial (
    _etl_chunk_id bigint NOT NULL,
    col1 text,
    col2 text,
    ...
)
```

2.2. При COPY добавлять `_etl_chunk_id` в каждую строку:
```python
# В _build_copy_buffer добавить chunk_id
def _build_copy_buffer(rows, col_count, chunk_id, ...):
    for row in rows:
        output.write(f"{chunk_id}\t")
        output.write("\t".join(escape_copy_text(v) for v in row))
        output.write("\n")
```

2.3. Изменить SQL в `_commit_buffer`:
```sql
COPY {staging_table} (_etl_chunk_id, {col_names})
FROM STDIN WITH (FORMAT text, ...)
```

**Файлы:** `loader/parallel_loader_v2.py`

---

## ЭТАП 3: Атомарная фиксация чанка (P0)

**Проблема:** Данные и статус чанка фиксируются раздельно.

**Решение:** Одна транзакция для INSERT + UPDATE status + DELETE staging.

### Задачи:

3.1. Создать метод `finalize_chunk`:
```python
def finalize_chunk(pg_conn, schema, table_name, staging_table,
                   chunk_id, chunk_stats, target_columns):
    cursor = pg_conn.cursor()
    try:
        # 1. INSERT из staging в target
        cursor.execute(f"""
            INSERT INTO {schema}.{table_name} ({target_columns})
            SELECT {target_columns}
            FROM {staging_table}
            WHERE _etl_chunk_id = %s
            ON CONFLICT (recid) DO NOTHING
        """, (chunk_id,))
        inserted = cursor.rowcount

        # 2. UPDATE статус чанка
        cursor.execute("""
            UPDATE etl.load_chunk
            SET status = 'completed',
                completed_at = CURRENT_TIMESTAMP,
                rows_read = %s,
                rows_staged = %s,
                rows_inserted = %s,
                rows_conflicted = %s,
                last_processed_key = %s,
                worker_id = NULL,
                error_type = NULL,
                error_message = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE chunk_id = %s AND status = 'running'
        """, (...))
        if cursor.rowcount != 1:
            raise RuntimeError(f"Chunk {chunk_id} not transitioned")

        # 3. Очистка staging
        cursor.execute(f"""
            DELETE FROM {staging_table}
            WHERE _etl_chunk_id = %s
        """, (chunk_id,))

        pg_conn.commit()
    except Exception:
        pg_conn.rollback()
        raise
```

3.2. Вызывать `finalize_chunk` при получении `ChunkFinished`

**Файлы:** `loader/parallel_loader_v2.py`

---

## ЭТАП 4: Пустые чанки (P0)

**Проблема:** Пустой чанк остаётся в running навсегда.

**Решение:** Worker отправляет `ChunkFinished(rows_read=0)`.

### Задачи:

4.1. В `_fetch_worker_v2` после цикла чтения:
```python
if chunk_completed and not self.stop_event.is_set():
    self.write_queue.put(ChunkFinished(
        chunk_id=chunk.chunk_id,
        chunk_no=chunk.chunk_no,
        rows_read=chunk_rows_fetched,
        last_processed_key=last_recid if chunk_rows_fetched > 0 else None,
    ))
```

4.2. В writer обработать `rows_read=0`:
```python
if isinstance(item, ChunkFinished) and item.rows_read == 0:
    finalize_chunk(pg_conn, ..., chunk_id=item.chunk_id,
                   chunk_stats={"rows_read": 0, ...})
    log(f"Writer: chunk {item.chunk_no} completed, no rows")
```

**Файлы:** `loader/parallel_loader_v2.py`

---

## ЭТАП 5: Heartbeat в отдельном потоке (P0)

**Проблема:** Heartbeat не обновляется во время длительного COPY/INSERT, чанки массово уходят в retry.

**Решение:** Отдельный heartbeat loop с собственным PG-соединением.

### Задачи:

5.1. Создать метод `heartbeat_loop`:
```python
def heartbeat_loop(stop_event, pg_conn_params, chunk_id, interval):
    conn = psycopg2.connect(**pg_conn_params)
    while not stop_event.wait(interval):
        try:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE etl.load_chunk
                SET heartbeat_at = CURRENT_TIMESTAMP
                WHERE chunk_id = %s
            """, (chunk_id,))
            conn.commit()
        except Exception:
            pass
    conn.close()
```

5.2. Запускать heartbeat thread при захвате чанка в worker

5.3. Останавливать heartbeat thread при завершении чанка

**Файлы:** `loader/parallel_loader_v2.py`

---

## ЭТАП 6: Recovery stale-чанков отдельно (P1)

**Проблема:** `claim_chunk` выполняет recovery при каждом вызове → гонки.

**Решение:** Вынести recovery в отдельный метод.

### Задачи:

6.1. Создать `recover_stale_chunks(run_id, timeout_minutes)` в ChunkManager:
```python
def recover_stale_chunks(self, run_id, timeout_minutes):
    cutoff = datetime.now() - timedelta(minutes=timeout_minutes)
    cursor = self.conn.cursor()
    cursor.execute("""
        UPDATE etl.load_chunk
        SET status = 'retry',
            worker_id = NULL,
            error_type = 'heartbeat_timeout',
            error_message = 'Heartbeat expired',
            updated_at = CURRENT_TIMESTAMP
        WHERE run_id = %s
          AND status = 'running'
          AND heartbeat_at < %s
    """, (run_id, cutoff))
    self.conn.commit()
```

6.2. **НЕ увеличивать** `attempt_count` при recovery

6.3. Увеличивать `attempt_count` только при `claim_chunk`

6.4. Вызывать recovery:
- Один раз при старте resume
- Отдельным supervisor не чаще 1 раза в минуту

**Файлы:** `core/chunk_manager.py`, `loader/parallel_loader_v2.py`

---

## ЭТАП 7: RECID-диапазоны [start, end) (P1)

**Проблема:** Строка с MIN(RECID) может быть пропущена.

**Решение:** Полуоткрытые диапазоны.

### Задачи:

7.1. Изменить SQL в `_fetch_worker_v2`:
```sql
-- Первый запрос чанка:
WHERE {column} >= %s AND {column} < %s

-- Следующие batch:
WHERE {column} > %s AND {column} < %s
```

7.2. Добавить тест на загрузку строки с MIN(RECID)

**Файлы:** `loader/parallel_loader_v2.py`

---

## ЭТАП 8: Отдельные PG-соединения для workers (P1)

**Проблема:** Один ChunkManager с одним соединением для всех workers.

**Решение:** Каждый worker получает собственный ChunkManager.

### Задачи:

8.1. В `_fetch_worker_v2` создавать отдельное PG-соединение:
```python
pg_conn = psycopg2.connect(
    host=self.pg.conn.info.host,
    port=self.pg.conn.info.port,
    dbname=self.pg.conn.info.dbname,
    user=self.pg.conn.info.user,
    password=self.pg.conn.info.password,
)
worker_chunk_manager = ChunkManager(pg_conn)
```

8.2. Writer также получает собственное соединение (уже есть)

**Файлы:** `loader/parallel_loader_v2.py`

---

## ЭТАП 9: Критерий успешного завершения (P0)

**Проблема:** `LoadStatus.SUCCESS` возвращается при неполной загрузке.

**Решение:** Строгая проверка.

### Задачи:

9.1. Изменить определение success:
```python
success = (
    self.writer_error is None
    and self.worker_error is None
    and completed_chunks == total_chunks
    and failed_chunks == 0
    and running_chunks == 0
    and retry_chunks == 0
    and pending_chunks == 0
)
```

9.2. Возвращать `LoadStatus.FAILED` если:
- `completed_chunks < total_chunks`
- Есть `running`, `retry`, `failed`
- `writer_error is not None`

9.3. Проверять `writer_error` после `writer.join()`:
```python
if self.writer_error is not None:
    raise RuntimeError(f"Writer failed: {self.writer_error}")
```

**Файлы:** `loader/parallel_loader_v2.py`, `application.py`

---

## ЭТАП 10: chunk_count из конфигурации (P1)

**Проблема:** `application.py` хардкодит `chunk_count: 500`.

**Решение:** Использовать значение из `config.yaml`.

### Задачи:

10.1. Убрать хардкод в `application.py`

10.2. Логировать итоговую конфигурацию перед стартом:
```python
log(f"  Config: chunk_count={chunk_count}, workers={workers}")
```

**Файлы:** `application.py`, `loader/parallel_loader_v2.py`

---

## ЭТАП 11: Семантика режимов (P1)

**Проблема:** `full` и `reload` оба делают TRUNCATE.

**Решение:** Явное определение.

### Задачи:

11.1. `reload`:
- TRUNCATE target
- Новый run
- Загрузка с нула

11.2. `full`:
- Полное чтение источника
- Поведение target определить явно (не TRUNCATE по умолчанию)

11.3. `resume`:
- Продолжение совместимого run
- Completed не перечитываются

11.4. `incremental`:
- Только новые/изменённые записи

**Файлы:** `loader/parallel_loader_v2.py`, `application.py`

---

## ЭТАП 12: Preflight перед TRUNCATE (P2)

**Проблема:** Target очищается до проверки работоспособности writer.

**Решение:** Preflight проверки.

### Задачи:

12.1. До TRUNCATE выполнять:
- Тест соединения
- Проверка колонок
- Тестовый SELECT
- Тестовый COPY
- Проверка уникального индекса
- Проверка прав

12.2. При ошибке preflight — не выполнять TRUNCATE

**Файлы:** `loader/parallel_loader_v2.py`

---

## ЭТАП 13: Логирование (P1)

**Проблема:** Недостаточно детальные логи.

**Решение:** Стандартизированный формат.

### Задачи:

13.1. После каждого чанка:
```
Writer: chunk 18 committed
  rows_read=302,145
  inserted=302,145
  conflicts=0
  last_recid=5,643,604,128
  status=completed
  progress=19/100
```

13.2. При retry:
```
Chunk 18 → retry
  reason=heartbeat_timeout
  attempt=2/5
  rows_read=150,000
  last_recid=5,643,500,000
```

13.3. При ошибке — **всегда** указывать `error_type` и `error_message`

**Файлы:** `loader/parallel_loader_v2.py`

---

## ЭТАП 14: Тесты (P0)

### Обязательные тесты:

| Тест | Описание |
|------|----------|
| `test_chunk_completed_immediately_after_commit` | Чанк получает completed сразу после COMMIT |
| `test_partial_chunk_not_completed` | Частично прочитанный чанк НЕ становится completed |
| `test_empty_chunk_completed` | Пустой чанк корректно получает completed |
| `test_writer_failure_does_not_complete_chunk` | Ошибка writer не завершает чанк |
| `test_completed_chunk_not_reprocessed_on_resume` | Завершённые чанки не перечитываются |
| `test_chunk_data_and_status_are_atomic` | Данные и статус фиксируются одной транзакцией |
| `test_one_chunk_failure_does_not_revert_completed` | Ошибка одного чанка не откатывает другие |
| `test_final_success_requires_all_chunks_completed` | SUCCESS только при 100% completed |
| `test_heartbeat_during_long_fetch` | Heartbeat работает во время длительного COPY |
| `test_stale_recovery_does_not_increment_attempt` | Recovery не увеличивает attempt_count |
| `test_max_attempts_transitions_to_failed` | Исчерпание попыток → failed |
| `test_network_retry_reconnects_sql_server` | Retry переподключается к SQL Server |
| `test_min_recid_is_loaded` | Строка с MIN(RECID) загружается |
| `test_copy_text_special_chars` | Кавычки, табы, \n, None, кириллица |

**Файлы:** `tests/test_parallel_loader_v2.py`

---

## Приоритеты и сроки

| Этап | Приоритет | Описание | Срок |
|------|-----------|----------|------|
| 1 | P0 | Протокол сообщений | 1 день |
| 2 | P0 | Staging с _etl_chunk_id | 0.5 дня |
| 3 | P0 | Атомарная фиксация | 1 день |
| 4 | P0 | Пустые чанки | 0.5 дня |
| 5 | P0 | Heartbeat в отдельном потоке | 1 день |
| 9 | P0 | Критерий успешного завершения | 0.5 дня |
| 14 | P0 | Тесты | 2 дня |
| 6 | P1 | Recovery отдельно | 0.5 дня |
| 7 | P1 | RECID-диапазоны | 0.5 дня |
| 8 | P1 | Отдельные PG-соединения | 0.5 дня |
| 10 | P1 | chunk_count из конфига | 0.5 дня |
| 11 | P1 | Семантика режимов | 0.5 дня |
| 13 | P1 | Логирование | 0.5 дня |
| 12 | P2 | Preflight перед TRUNCATE | 1 день |

**Итого:** ~10 рабочих дней

---

## Критерии приёмки (из обоих документов)

- [ ] Чанк получает `completed` сразу после успешного COMMIT именно его данных
- [ ] Завершение одного чанка не зависит от окончания остальных workers
- [ ] Частично прочитанный чанк НИКОГДА не получает `completed`
- [ ] Пустой чанк корректно получает `completed`
- [ ] Ошибка writer не оставляет успешно зафиксированные предыдущие чанки в running
- [ ] После перезапуска resume берёт только `pending`, `retry` и допустимые `failed`
- [ ] Уже завершённые чанки не перечитываются
- [ ] `rows_read`, `rows_inserted`, `rows_conflicted` записываются по каждому чанку
- [ ] Target-данные и статус чанка фиксируются одной транзакцией
- [ ] При падении до commit статус остаётся `running`/`retry`, но не `completed`
- [ ] Во время работы количество `completed` постепенно увеличивается
- [ ] В конце успешного запуска `completed = total_chunks`
- [ ] Нельзя вернуть `SUCCESS` при 0/500, 499/500 или наличии `running`
- [ ] Heartbeat обновляется каждые 30 секунд независимо от SQL-запроса
- [ ] Активный чанк не переводится ошибочно в retry через 600 секунд
- [ ] Recovery не увеличивает `attempt_count`
- [ ] Строка с минимальным RECID загружается
- [ ] `chunk_count` берётся из конфигурации
- [ ] Writer корректно обрабатывает кавычки, табуляции, переносы строк и NULL
