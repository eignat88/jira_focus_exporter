# Рефакторинг ETL V2: почему зависает

## Корневая причина

1. `heartbeat` обновляется только после успешного `fetchmany()`. Пока SQL Server выполняет длинный запрос, heartbeat стареет.
2. stale-recovery выполняется внутри `claim_chunk()` каждым worker. Один worker может перевести чужой активный чанк `running -> retry`.
3. После полного чтения чанк продолжает числиться `running`, пока ждёт writer. Supervisor считает его зависшим.
4. Writer смешивает данные нескольких чанков в одном буфере и завершает чанки только после общего `SENTINEL`.
5. Один `ChunkManager` и одно PostgreSQL-соединение разделяются между потоками.
6. Старые `error_type/error_message` не очищаются при повторном захвате.

## Новая схема

`pending/retry -> running -> ready_to_commit -> writing -> completed`

- отдельное PostgreSQL-соединение на worker, heartbeat, writer и supervisor;
- heartbeat работает отдельным потоком;
- retry имеет приоритет над pending;
- supervisor обрабатывает только stale `running`;
- данные staging разделены по `_etl_chunk_id`;
- target INSERT и `writing -> completed` выполняются одной транзакцией;
- при retry staging чанка очищается и диапазон читается сначала, чтобы не пропустить строки;
- COPY использует `FORMAT text` с явным экранированием.

## Установка

```cmd
copy ax_to_postgres_etl\loader\parallel_loader_v2.py ax_to_postgres_etl\loader\parallel_loader_v2.py.bak
copy /Y parallel_loader_v2_refactored.py ax_to_postgres_etl\loader\parallel_loader_v2.py
psql -h localhost -U postgres -d wms_analysis -f 001_refactor_chunk_runtime.sql
```

Запуск:

```cmd
python -m ax_to_postgres_etl.main --use-v2 --table ALK_MARKSERIAL --mode resume
```

Первый запуск выполните на тестовой копии или небольшом диапазоне: синтаксис проверен, но подключение к вашим БД в этой среде недоступно.
