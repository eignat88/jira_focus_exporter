# ДОРОЖНАЯ КАРТА UNIFIED ETL

**Обновлено:** 2026-08-03 14:15  
**Период детального плана:** 2026-08-03 — 2026-08-09.

---

## 1. Текущая точка

После обновления `main` фактическое состояние изменилось:

- PR #11 с исправлением `purchase_order` объединён;
- реализована настоящая стратегия `full_table`;
- добавлены составные conflict keys и `ON CONFLICT DO UPDATE`;
- `full/resume` теперь блокируются обязательным read-only preflight;
- отсутствующие RAW mapping-колонки стали блокирующей ошибкой;
- добавлены regression tests для `purchase_order`;
- добавлен безопасный диагностический комплект для `sales_order` run 45;
- новый полный runtime baseline после этих изменений ещё не зафиксирован.

Главная цель периода — подтвердить изменения кодом не только на уровне unit/regression tests, но и фактическими preflight, ETL runs и reconciliation в PostgreSQL.

---

## 2. Приоритеты

| Приоритет | Задача | Ожидаемый результат |
|---|---|---|
| P0 | Повторный полный pytest | Актуальные passed/failed и отделённые environment failures |
| P0 | Проверить `purchase_order` после PR #11 | Preflight READY, run COMPLETED, повторный запуск идемпотентен |
| P0 | Диагностировать `sales_order` run 45 | Установлена причина и выбран resume/restart-stage |
| P1 | Закрыть `picking_route` и `pack_task` | validate-only и RAW/DDS reconciliation выполнены |
| P1 | Утвердить chunk strategy `order_trans` | Индексируемый ключ, отсутствие Seq Scan |
| P1 | Утвердить архитектуру `serial_mark` | Staging/CTAS/source-key без массового UPDATE RAW |
| P2 | Обновить документы по runtime | STATUS/ROADMAP/gantt совпадают с фактическими runs |

---

## 3. План работ

### 3 августа — актуальный baseline и `sales_order`

1. Запустить полный unit/regression pytest.
2. Отдельно классифицировать SQL Server integration failures.
3. Запустить read-only диагностику run 45:

```powershell
Set-Location "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2"

.\monitoring\run_sales_order_run45_diagnostic.ps1
```

4. Проверить:
   - `etl.load_run` и `etl.load_chunk`;
   - активные процессы;
   - vacuum/index progress;
   - планы batch 100k и 250k;
   - точную ошибку failed chunk.
5. Не выполнять resume до фиксации причины.

**Критерий готовности:** причина run 45 документирована, выбран безопасный сценарий продолжения.

---

### 4 августа — `purchase_order`

1. Проверить наличие миграции составного unique key.
2. Выполнить preflight:

```powershell
python -m ax_to_postgres_etl.pipelines.dds_cli `
    --mode preflight `
    --stage purchase_order
```

3. Подтвердить:
   - стратегия `full_table` не требует `recid_bigint`;
   - все mapping-колонки существуют;
   - составной conflict key полностью совпадает с unique index/constraint;
   - mapped SELECT проходит `EXPLAIN` без `ANALYZE`.
4. Перед изменяющим запуском проверить disk, WAL, activity и locks.
5. Выполнить full load и `validate-only`.
6. Повторить запуск для проверки `DO UPDATE` и отсутствия дублей.

**Критерий готовности:** новый run COMPLETED, target заполнен, повторный запуск идемпотентен.

---

### 5 августа — `picking_route` и `pack_task`

Для `picking_route`:

- подтвердить Index Only Scan;
- проверить разницу RAW/DDS;
- проверить conflict key;
- выполнить validate-only.

Для `pack_task`:

- подтвердить Index Cond;
- проверить дубли RAW;
- проверить поведение `ON CONFLICT`;
- выполнить validate-only.

**Критерий готовности:** оба stages имеют подтверждённый runtime-статус и документированную reconciliation.

---

### 6 августа — `order_trans`

1. Получить фактические RAW-колонки.
2. Исправить mapping на реально существующие поля.
3. Выбрать безопасную chunk strategy:
   - числовой ключ во время SQL Server → RAW;
   - normalized/staging table;
   - functional index только при точном совпадении выражения.
4. Не выполнять массовый UPDATE RAW.
5. Выполнить `EXPLAIN` без `ANALYZE` для batch 100k и 250k.
6. Подтвердить отсутствие Seq Scan.
7. Оценить WAL, disk, rollback и resume.

**Критерий готовности:** preflight READY либо утверждён отдельный DDL/ETL-план.

---

### 7 августа — `serial_mark`

1. Исправить контракт normalization stage, если он всё ещё требует обычный columns mapping.
2. Сравнить:
   - числовой RECID при SQL Server → RAW;
   - отдельную normalized staging table;
   - одноразовый CTAS + индекс.
3. Для каждого варианта оценить:
   - полное чтение RAW;
   - место для новой таблицы и индекса;
   - WAL;
   - блокировки;
   - rollback;
   - resume;
   - повторное использование.
4. Использовать benchmark batch 100k, 250k, 500k и 1M.

**Критерий готовности:** выбран и документирован один безопасный вариант без массового UPDATE `raw_ax.alk_markserial`.

---

### 8–9 августа — контрольный прогон

1. Полный pytest.
2. Полный read-only preflight.
3. `dds_cli --mode status`.
4. Reconciliation завершённых stages.
5. Обновление STATUS/ROADMAP/gantt по фактическим run IDs.
6. Формирование backlog для MART и ML только после подтверждения DDS.

**Критерий готовности:** минимум четыре stages подтверждены runtime-проверками, а блокеры больших таблиц оформлены как технические решения.

---

## 4. Текущий порядок stages

```text
purchase_order runtime validation
→ sales_order recovery
→ picking_route validation
→ pack_task validation
→ order_trans indexed design
→ serial_mark normalization design
→ serial_mark
→ DDS reconciliation
→ MART
→ ML
```

Для каждого stage:

```text
read-only diagnostics
→ preflight
→ disk/WAL/activity/index checks
→ full/resume/restart-stage
→ validate-only
→ status
→ RAW/DDS reconciliation
→ documentation
```

---

## 5. Контрольные команды

```powershell
cd "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2"

python -m pytest tests -q `
    --import-mode=importlib `
    --ignore=tests/test_parallel_inventtable.py

python -m ax_to_postgres_etl.pipelines.dds_cli --mode preflight
python -m ax_to_postgres_etl.pipelines.dds_cli --mode status
```

WAL monitor:

```powershell
python monitoring\postgres_wal_monitor.py --interval 60 --duration 8h
```

Диагностика `sales_order` run 45:

```powershell
.\monitoring\run_sales_order_run45_diagnostic.ps1
```

---

## 6. Ограничения безопасности

- Не выполнять массовый UPDATE `raw_ax.alk_markserial` или `raw_ax.wmsordertrans`.
- Не запускать blocked stage в `full`.
- Не считать `n_live_tup` точным count или прогрессом.
- Не выполнять `CREATE INDEX CONCURRENTLY` внутри транзакции.
- Не применять `VACUUM FULL` без окна полной блокировки и места для переписывания.
- Не выполнять `EXPLAIN ANALYZE` на большой таблице без ограниченного диапазона и оценки риска.
- Один тяжёлый stage за раз.
- Любая загрузка должна быть идемпотентной, возобновляемой и отменяемой.
