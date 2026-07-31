# ДОРОЖНАЯ КАРТА UNIFIED ETL

**Обновлено:** 2026-07-31 19:24  
**Период детального плана:** 2026-08-01 — 2026-08-07.

---

## 1. Текущая точка

Runtime-проверка дала объективную базовую линию:

- pytest: `111 passed`, `3 failed`, `5 warnings`;
- два failures связаны с недоступным SQL Server/SSPI;
- один failure связан с некорректным ожиданием фиксированной задержки при включённом jitter;
- preflight: 2 READY, 1 READY_WITH_WARNINGS, 4 BLOCKED;
- последние runs: `sales_order` run 45 failed, `purchase_order` run 38 failed;
- свободное место PostgreSQL: около 1.4 TB;
- конфликтующих ETL runs, locks, autovacuum, CREATE INDEX и долгих транзакций на момент preflight не было.

Основная цель недели — перевести проект из состояния «каркас реализован» в состояние «малые и средние RAW → DDS stages воспроизводимо готовы, а для крупных таблиц утверждена безопасная архитектура».

---

## 2. Приоритеты недели

| Приоритет | Задача | Ожидаемый результат |
|---|---|---|
| P0 | Стабилизировать pytest | Unit-набор зелёный; integration-тесты отделены и управляемо skip |
| P0 | Исправить `purchase_order` full_table preflight | Preflight READY без обращения к `recid_bigint` |
| P0 | Исправить mappings `purchase_order` и `order_trans` | Все expressions ссылаются на существующие RAW-колонки |
| P0 | Утвердить chunk strategy для `order_trans` | Нет Seq Scan и нет ожидания несуществующего `recid_bigint` |
| P0 | Принять решение по `serial_mark` | Выбран staging/CTAS/source-key подход без массового UPDATE RAW |
| P1 | Валидировать READY stages | `picking_route`, `pack_task`, `sales_order` подтверждены через runs и сверки |
| P1 | Разобрать failed runs 38 и 45 | Зафиксированы причины, исправления и критерии повторного запуска |
| P2 | Привести документацию и конфиг к единому состоянию | STATUS/ROADMAP/gantt и YAML соответствуют runtime |

---

## 3. План по дням

### Суббота, 1 августа — тесты и диагностика failed runs

**Цель:** отделить дефекты кода от проблем окружения и получить причины runs 38/45.

Работы:

1. Исправить `test_retry_policy`:
   - проверять диапазон jitter;
   - либо подменять random/seed;
   - либо создавать `RetryPolicy(jitter=0)` для детерминированного теста.
2. Пометить `test_integration_inventtable.py` как integration.
3. Добавить skip с понятной причиной при невозможности SQL Server connect.
4. Заменить `return bool` на `assert` в пяти тестах.
5. Получить детали runs 38 и 45 из `etl.load_run` и `etl.load_chunk`.
6. Проверить текущую SSPI-конфигурацию отдельно от unit-тестов.

Read-only SQL:

```sql
SELECT *
FROM etl.load_run
WHERE run_id IN (38, 45)
ORDER BY run_id;

SELECT *
FROM etl.load_chunk
WHERE run_id IN (38, 45)
ORDER BY run_id, chunk_no;
```

**Критерий готовности:** unit-набор проходит без failures; integration-набор имеет явный статус PASS/SKIP/ENVIRONMENT BLOCKED.

---

### Воскресенье, 2 августа — `purchase_order`

**Цель:** довести preflight малой таблицы до READY.

Работы:

1. Проверить фактические колонки `raw_ax.purchtable` через `information_schema.columns`.
2. Найти реальные аналоги `vendaccount` и `orderdate`.
3. Исправить YAML mappings.
4. Исправить остаточное обращение preflight к `recid_bigint` при `full_table`.
5. Добавить регрессионный тест, который проверяет отсутствие range key/index logic для `full_table`.
6. Повторить preflight.
7. Только при READY выполнить безопасный full load маленькой таблицы.
8. Выполнить `validate-only`, проверить conflict key и counts.

Риски:

- WAL LOW;
- таблица RAW ~293 MB;
- resume внутри одного full-table чтения ограничен;
- rollback одной транзакции допустим, но запускать только после проверки свободного диска и locks.

**Критерий готовности:** `purchase_order` preflight READY, run COMPLETED, target заполнен, повторный запуск не создаёт дублей.

---

### Понедельник, 3 августа — `sales_order`

**Цель:** подтвердить стабильный идемпотентный stage после failed run 45.

Работы:

1. Разобрать error_message и failed chunks run 45.
2. Проверить, почему предыдущие runs 35–37 completed, а run 45 failed.
3. Выполнить `EXPLAIN` для batch 100k и 250k без `ANALYZE`.
4. При необходимости выполнить безопасный `EXPLAIN (ANALYZE, BUFFERS)` только на ограниченном диапазоне.
5. Сравнить Bitmap Heap Scan по selectivity.
6. Выполнить resume/restart-stage согласно состоянию run.
7. Проверить counts, min/max `source_recid`, duplicates и NULL.

**Критерий готовности:** последний run COMPLETED, failed chunks отсутствуют, повторный запуск идемпотентен.

---

### Вторник, 4 августа — `picking_route` и `pack_task`

**Цель:** закрыть два READY stages.

`picking_route`:

- подтвердить Index Only Scan;
- выяснить разницу RAW ~6.99 млн и DDS ~6.53 млн;
- проверить conflict key и фильтрацию/преобразования;
- выполнить resume или полную валидацию.

`pack_task`:

- подтвердить `Index Cond` на реальном диапазоне;
- проверить разницу RAW estimate 7,622,862 и DDS 7,622,335;
- проверить дубли RAW и поведение `ON CONFLICT`;
- выполнить validate-only.

**Критерий готовности:** оба stage имеют COMPLETED/validated status и документированную причину расхождений counts.

---

### Среда, 5 августа — `order_trans`

**Цель:** убрать архитектурные и mapping-блокеры большой таблицы 44+ млн строк.

Работы:

1. Получить фактический список колонок RAW.
2. Исправить `ordertransid`, `pickedqty`, `wastedqty` на реальные имена либо удалить неподдерживаемые mappings.
3. Выбрать chunking:
   - предпочтительно числовой ключ при SQL Server → RAW;
   - либо отдельная normalized/staging-таблица;
   - либо functional index при полном совпадении выражения;
   - не выполнять массовый UPDATE RAW.
4. Получить `EXPLAIN` без `ANALYZE`.
5. Подтвердить отсутствие Seq Scan.
6. Оценить WAL, disk и batch 100k/250k.

**Критерий готовности:** preflight READY или утверждён отдельный технический план с DDL, оценкой диска/WAL и resume.

---

### Четверг, 6 августа — `serial_mark_normalization` и архитектура `serial_mark`

**Цель:** принять безопасное решение для 153 млн строк.

Работы:

1. Исправить контракт preflight для normalization stage: он не должен требовать обычный DDS columns mapping, если stage описан через `normalization`.
2. Сравнить три подхода:
   - числовой RECID во время SQL Server → RAW;
   - отдельная normalized staging-таблица;
   - одноразовый CTAS с последующим индексом.
3. Для каждого подхода оценить:
   - полное чтение 78.8 GB RAW;
   - размер новой таблицы и индекса;
   - WAL;
   - длительность;
   - блокировки;
   - rollback;
   - возможность resume;
   - повторное использование.
4. Не использовать массовый UPDATE RAW.
5. Сформировать benchmark-план batch 100k, 250k, 500k, 1M.

**Критерий готовности:** утверждён один вариант и подготовлены команды dry-run/preflight/создания без запуска тяжёлой операции.

---

### Пятница, 7 августа — контрольный прогон и фиксация результатов

**Цель:** закрыть недельный цикл воспроизводимой проверкой.

Работы:

1. Полный unit pytest.
2. Отдельный integration pytest с явным состоянием SQL Server.
3. Полный preflight всех stages.
4. `dds_cli --mode status`.
5. Сверка завершённых stages.
6. Обновление STATUS, ROADMAP и Gantt по факту.
7. Сформировать backlog следующей недели для `order_trans`/`serial_mark`.

**Критерий готовности недели:** минимум 4 stages READY/validated, unit tests green, блокеры крупных таблиц оформлены техническими решениями, а не обходными UPDATE.

---

## 4. Порядок запуска stages

```text
purchase_order
→ sales_order
→ picking_route
→ pack_task
→ order_trans
→ serial_mark_normalization
→ serial_mark
→ DDS reconciliation
→ MART
→ ML
```

Для каждого stage:

```text
read-only diagnostics
→ preflight
→ проверка WAL/disk/activity/indexes
→ full/resume/restart-stage
→ validate-only
→ status
→ RAW/DDS reconciliation
→ документация
```

---

## 5. Команды контроля

```powershell
cd "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2"

python -m pytest tests -q `
    --import-mode=importlib `
    --ignore=tests/test_parallel_inventtable.py

python -m ax_to_postgres_etl.pipelines.dds_cli --mode preflight
python -m ax_to_postgres_etl.pipelines.dds_cli --mode status
```

WAL monitor перед изменяющими загрузками:

```powershell
python monitoring\postgres_wal_monitor.py --interval 60 --duration 8h
```

---

## 6. Ограничения безопасности

- Не выполнять массовый `UPDATE raw_ax.alk_markserial` или `raw_ax.wmsordertrans`.
- Не запускать blocked stage в режиме `full`.
- Не считать `n_live_tup` точным count или прогрессом.
- Не выполнять `CREATE INDEX CONCURRENTLY` внутри транзакции.
- Не применять `VACUUM FULL` без полного окна блокировки и места для переписывания таблицы.
- Один тяжёлый stage за раз.
- Любой запуск должен быть идемпотентным, возобновляемым и отменяемым.
