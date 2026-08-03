# ЕДИНЫЙ СТАТУС ПРОЕКТА

**Проект:** Anomaly Detection / Unified ETL для AX 2012 и WMS  
**Обновлено:** 2026-08-03 14:15  
**Основание:** ветка `main`, изменения PR #11, история коммитов и добавленный диагностический комплект для `sales_order` run 45.

---

## 1. Фактическое состояние

Основной контур:

```text
SQL Server AX 2012/WMS
        ↓
raw_ax
        ↓
stage_ax (при необходимости)
        ↓
dds
        ↓
mart / mart_ax
        ↓
аналитика и ML
```

SQL Server → RAW работает и не должен переписываться без отдельного обоснования.

RAW → DDS выполняется внутри PostgreSQL через `INSERT INTO ... SELECT FROM ...`. Передача данных через pandas на этом этапе не используется.

Unified ETL реализует:

- `PipelineRunner`;
- `RunManager`;
- `ChunkManager`;
- `RetryPolicy`;
- `PostgresRuntime`;
- `RawToDdsAdapter`;
- YAML-конфигурацию stages;
- advisory lock;
- heartbeat;
- resume;
- отмену через Ctrl+C;
- CLI-режимы `preflight`, `full`, `resume`, `restart-stage`, `validate-only`, `status`.

---

## 2. Что изменилось после статуса от 2026-07-31

В `main` объединён PR #11 `Fix purchase order RAW to DDS upsert`.

Реализовано:

1. Настоящая стратегия `full_table` без обязательного range key.
2. Поддержка составного `conflict_key`.
3. Поддержка `conflict_action=update` и `ON CONFLICT ... DO UPDATE`.
4. Проверка точного уникального ограничения или индекса по полному упорядоченному составному ключу.
5. Отсутствующие RAW-колонки в mapping теперь блокируют preflight, а не выдаются как предупреждение.
6. Режимы `full` и `resume` сначала выполняют обязательный read-only preflight.
7. При провале preflight ETL run не создаётся и `INSERT/UPDATE` не запускаются.
8. Для `purchase_order` исправлены mapping и business key.
9. Добавлена миграция составного уникального ключа DDS.
10. Добавлены регрессионные тесты для full-table и upsert.
11. JSON-результат preflight дополнен сводными полями.

Также добавлен read-only диагностический комплект для `sales_order` run 45:

- `monitoring/sales_order_run45_diagnostic.py`;
- `monitoring/sales_order_run45_diagnostic_queries.sql`;
- `monitoring/run_sales_order_run45_diagnostic.ps1`;
- `monitoring/README.md`;
- диагностический ZIP.

Диагностика по умолчанию не выполняет `EXPLAIN ANALYZE`, открывает read-only транзакцию и устанавливает timeout.

---

## 3. Состояние RAW → DDS stages

| Stage | RAW → DDS | Состояние кода | Runtime-статус | Следующее действие |
|---|---|---|---|---|
| `purchase_order` | `raw_ax.purchtable` → `dds.purchase_order` | Исправлен в `main` | Требуется повторный preflight и промышленная валидация | Применить миграцию ключа, выполнить preflight, full, validate-only |
| `sales_order` | `raw_ax.salestable` → `dds.sales_order` | Диагностика run 45 добавлена | Последний известный run 45 — failed | Запустить read-only диагностику, установить причину, затем решить resume/restart |
| `picking_route` | `raw_ax.wmspickingroute` → `dds.picking_route` | Конфигурация готова | Последний preflight — READY | Выполнить validate-only и reconciliation |
| `pack_task` | `raw_ax.lfl_scspacktask` → `dds.pack_task` | Конфигурация готова | Последний preflight — READY | Выполнить validate-only и reconciliation |
| `order_trans` | `raw_ax.wmsordertrans` → `dds.order_trans` | Mapping и chunk strategy требуют доработки | BLOCKED | Выбрать индексируемый ключ и исправить mapping |
| `serial_mark_normalization` | `raw_ax.alk_markserial` → `stage_ax...` | Архитектура не утверждена | BLOCKED | Выбрать staging/CTAS/source-key подход |
| `serial_mark` | `raw_ax.alk_markserial` → `dds.serial_mark` | Пакетная схема испытана отдельно | Pipeline BLOCKED для безопасного повторного запуска | Утвердить числовой chunk key без массового UPDATE RAW |

Важно: исправление кода `purchase_order` не означает, что production run уже выполнен. До получения нового `preflight = READY`, `run = COMPLETED` и результатов `validate-only` stage считается **готовым к проверке**, а не полностью закрытым.

---

## 4. Последний подтверждённый runtime baseline

Последний зафиксированный полный прогон от 2026-07-31:

```text
pytest: 111 passed, 3 failed, 5 warnings
preflight: 2 READY, 1 READY_WITH_WARNINGS, 4 BLOCKED
```

Классификация известных pytest failures:

| Проверка | Причина | Классификация |
|---|---|---|
| `test_integration_inventtable.py::test_full_load` | SQL Server SSPI context | ENVIRONMENT BLOCKED |
| `test_integration_inventtable.py::test_resume` | SQL Server SSPI context | ENVIRONMENT BLOCKED |
| `test_resume_v2.py::test_retry_policy` | Тест ожидал фиксированную задержку при jitter | TEST DEFECT |

После PR #11 добавлены новые регрессионные тесты, но в репозитории нет нового сохранённого полного runtime-отчёта, подтверждающего итоговое количество passed/failed на машине пользователя.

Поэтому документация не объявляет весь test suite зелёным до повторного запуска команды:

```powershell
python -m pytest tests -q `
    --import-mode=importlib `
    --ignore=tests/test_parallel_inventtable.py
```

---

## 5. Последние известные ETL runs

| Run ID | Stage | Статус | Комментарий |
|---:|---|---|---|
| 45 | `sales_order` | failed | Добавлен безопасный диагностический комплект; причина ещё не зафиксирована в STATUS |
| 38 | `purchase_order` | failed | Код stage после этого существенно исправлен; требуется новый запуск |
| 37 | `sales_order` | completed | Предыдущий успешный запуск |
| 36 | `sales_order` | completed | Предыдущий успешный запуск |
| 35 | `sales_order` | completed | Предыдущий успешный запуск |

Новый статус из PostgreSQL после изменений PR #11 пока не зафиксирован в репозитории.

---

## 6. Текущий readiness

```text
READY FOR RUNTIME CHECK:
- purchase_order

READY / VALIDATION REQUIRED:
- picking_route
- pack_task

READY WITH INVESTIGATION REQUIRED:
- sales_order

BLOCKED:
- order_trans
- serial_mark_normalization
- serial_mark
```

---

## 7. Что осталось сделать

### P0 — `purchase_order`

1. Проверить наличие и применимость миграции составного unique key.
2. Выполнить read-only preflight.
3. Подтвердить, что preflight не требует `recid_bigint` для `full_table`.
4. Выполнить безопасный full load.
5. Выполнить `validate-only`.
6. Повторить запуск и подтвердить идемпотентный `DO UPDATE`.

### P0 — `sales_order`

1. Запустить `monitoring/run_sales_order_run45_diagnostic.ps1`.
2. Зафиксировать ошибку run 45 и failed chunks.
3. Сравнить планы batch 100k и 250k.
4. Решить: `resume` или `restart-stage`.
5. Подтвердить counts, min/max key, duplicates и NULL.

### P1 — READY stages

- валидировать `picking_route`;
- валидировать `pack_task`;
- объяснить расхождения RAW/DDS;
- зафиксировать статусы runs.

### P1 — крупные таблицы

- для `order_trans` выбрать индексируемую chunk strategy;
- для `serial_mark` утвердить staging/CTAS/source-key решение;
- не выполнять массовый UPDATE RAW.

---

## 8. Правила безопасности

Для `raw_ax.alk_markserial` запрещён массовый UPDATE 150+ млн строк без отдельной оценки:

- WAL;
- свободного места;
- времени выполнения;
- rollback;
- MVCC/dead tuples;
- последующего VACUUM;
- возможности отмены и resume.

Перед тяжёлой операцией обязательны:

```text
pg_stat_activity
pg_stat_progress_create_index
pg_stat_progress_vacuum
pg_stat_user_tables
pg_stat_wal
pg_stat_checkpointer
размеры таблиц и индексов
свободное место на диске
EXPLAIN без ANALYZE
```

`VACUUM FULL` не используется без отдельного окна полной блокировки и оценки места для переписывания таблицы.

---

## 9. Ближайшая контрольная точка

Следующий достоверный статус должен быть сформирован после выполнения:

```powershell
python -m pytest tests -q `
    --import-mode=importlib `
    --ignore=tests/test_parallel_inventtable.py

python -m ax_to_postgres_etl.pipelines.dds_cli --mode preflight
python -m ax_to_postgres_etl.pipelines.dds_cli --mode status
```

Дополнительно:

```powershell
.\monitoring\run_sales_order_run45_diagnostic.ps1
```

После этого необходимо обновить фактические pytest counts, readiness всех stages и новые ETL run IDs.
