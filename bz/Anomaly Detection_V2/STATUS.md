# ЕДИНЫЙ СТАТУС ПРОЕКТА

**Проект:** Anomaly Detection / Unified ETL для AX 2012 и WMS  
**Обновлено:** 2026-07-31 19:24  
**Основание:** ветка `main`, pytest, полный RAW → DDS preflight и `dds_cli --mode status`.

---

## 1. Фактическое состояние

Основной контур:

```text
SQL Server AX 2012/WMS → raw_ax → stage_ax (при необходимости) → dds → mart/mart_ax → аналитика/ML
```

Unified ETL реализует `PipelineRunner`, `RunManager`, `ChunkManager`, `RetryPolicy`, `PostgresRuntime`, `RawToDdsAdapter`, advisory lock, heartbeat, resume, отмену Ctrl+C и CLI-режимы `preflight`, `full`, `resume`, `restart-stage`, `validate-only`, `status`.

RAW → DDS выполняется внутри PostgreSQL через `INSERT ... SELECT`. Массовое преобразование данных через pandas не используется.

---

## 2. Результат тестов 2026-07-31

Команда:

```powershell
python -m pytest tests -q `
    --import-mode=importlib `
    --ignore=tests/test_parallel_inventtable.py
```

Результат:

```text
111 passed
3 failed
5 warnings
Время: 2.33 s
```

### Классификация ошибок

| Тест | Тип проблемы | Фактическая причина | Статус |
|---|---|---|---|
| `test_integration_inventtable.py::test_full_load` | Окружение / интеграция | SQL Server не создаёт SSPI-контекст | BLOCKED BY ENVIRONMENT |
| `test_integration_inventtable.py::test_resume` | Окружение / интеграция | SQL Server не создаёт SSPI-контекст | BLOCKED BY ENVIRONMENT |
| `test_resume_v2.py::test_retry_policy` | Ошибка ожидания теста | В `RetryPolicy` используется jitter; тест ожидает строго `5.0`, получено `4.142799...` | TEST DEFECT |

Два integration-теста не подтверждают дефект ETL-кода: соединение падает до выполнения сценария загрузки. Их необходимо отделить маркером `integration` и запускать только при доступном SQL Server и валидной Windows/Kerberos-аутентификации.

Тест `test_retry_policy` должен проверять допустимый диапазон jitter либо использовать отключённый/детерминированный jitter.

### Предупреждения pytest

Пять тестов возвращают `bool` вместо использования `assert` и возврата `None`. Это не блокирует прогон, но тесты необходимо привести к стандартному контракту pytest.

---

## 3. Результат полного preflight

Preflight был read-only: записи в `etl.load_run` не создавались, `INSERT` и `UPDATE` не выполнялись.

| Stage | Результат | Ключевые факты | Блокеры / предупреждения |
|---|---|---|---|
| `serial_mark_normalization` | BLOCKED | источник `raw_ax.alk_markserial` | отсутствует `columns` mapping для preflight |
| `serial_mark` | BLOCKED | RAW ~153.2 млн строк, 78.8 GB; DDS ~151.7 млн строк, 36.3 GB; свободно 1.4 TB | `recid` text при `numeric_range`; нет `recid_bigint` B-tree; Seq Scan; нет ANALYZE |
| `picking_route` | READY | `recid_bigint int8`; индекс `idx_wmspickingroute_recid_bigint`; Index Only Scan; RAW ~6.99 млн, DDS ~6.53 млн | нет |
| `pack_task` | READY | `numeric_text_range`; индекс `idx_lfl_scspacktask_recid`; диапазон находится в `Index Cond`; RAW ~7.62 млн, DDS ~7.62 млн | нет |
| `order_trans` | BLOCKED | RAW ~44.36 млн, 23.7 GB; DDS ~45.75 млн, 9.8 GB | `recid` text при `numeric_range`; нет `recid_bigint`; отсутствуют mapping-колонки `ordertransid`, `pickedqty`, `wastedqty`; нет ANALYZE |
| `sales_order` | READY_WITH_WARNINGS | functional index `idx_salestable_recid_bigint`; RAW ~3.65 млн, DDS ~3.65 млн | Bitmap Heap Scan; требуется проверить селективность batch 250k |
| `purchase_order` | BLOCKED | RAW ~257.8 тыс., 292.8 MB; стратегия `full_table`; target практически пуст | отсутствуют `vendaccount`, `orderdate`; preflight всё ещё ошибочно обращается к `recid_bigint`; нет ANALYZE |

### Сводка readiness

```text
READY:               2 stages
READY_WITH_WARNINGS: 1 stage
BLOCKED:             4 stages
```

К изменяющему запуску сейчас допускаются только:

1. `picking_route`;
2. `pack_task`;
3. `sales_order` — после отдельной оценки Bitmap Heap Scan и batch size.

---

## 4. Состояние ETL runs

`dds_cli --mode status` показывает:

| Run ID | Stage / source | Target | Статус | Начало |
|---:|---|---|---|---|
| 45 | `salestable` | `sales_order` | failed | 2026-07-31 17:31:59 |
| 38 | `purchtable` | `purchase_order` | failed | 2026-07-31 16:03:16 |
| 37 | `salestable` | `sales_order` | completed | 2026-07-31 15:04:11 |
| 36 | `salestable` | `sales_order` | completed | 2026-07-31 14:25:49 |
| 35 | `salestable` | `sales_order` | completed | 2026-07-31 14:14:31 |

`purchase_order` не загружен. Для `sales_order` есть успешные runs, но последний run завершился ошибкой. До нового запуска необходимо разобрать ошибку run 45 и подтвердить идемпотентность повторного выполнения.

---

## 5. Статус компонентов

| Компонент | Статус |
|---|---|
| SQL Server → RAW | Работает; не переписывать без веской причины |
| Unified ETL core | Реализован |
| Read-only preflight | Работает, но остаётся дефект `full_table` для `purchase_order` |
| Unit/regression tests | 111 прошли; 1 тест требует исправления ожидания jitter |
| Integration tests SQL Server | Не выполнены из-за SSPI |
| `picking_route` | READY |
| `pack_task` | READY |
| `sales_order` | READY_WITH_WARNINGS; последний run failed |
| `purchase_order` | BLOCKED |
| `order_trans` | BLOCKED |
| `serial_mark_normalization` | BLOCKED |
| `serial_mark` | BLOCKED; существующая DDS содержит около 151.7 млн строк, но pipeline не готов к безопасному повторному запуску |
| MART/ML на актуальном DDS | Не подтверждены |

---

## 6. Основные причины текущих блокировок

1. Конфигурация стратегий не соответствует фактическому типу chunk key для `serial_mark` и `order_trans`.
2. Для крупных RAW-таблиц отсутствует индексируемый числовой ключ, ожидаемый текущим `numeric_range`.
3. YAML mappings содержат имена колонок, которых нет в RAW.
4. Ветка `full_table` исправлена не полностью: `purchase_order` всё ещё формирует проверку по `recid_bigint`.
5. Integration-тесты смешаны с unit-набором и зависят от внешнего SQL Server.
6. Тест retry policy не учитывает jitter.

---

## 7. Правила безопасности

Для `raw_ax.alk_markserial` запрещён массовый `UPDATE` 153 млн строк без отдельной оценки WAL, диска, rollback и времени. Предпочтительные варианты:

- числовой ключ при SQL Server → RAW;
- отдельная staging/normalized-таблица;
- `CREATE TABLE AS SELECT` для одноразовой реорганизации;
- functional index только при полном совпадении выражения запроса и индекса.

Перед тяжёлой операцией обязательны проверки `pg_stat_activity`, `pg_stat_progress_create_index`, `pg_stat_progress_vacuum`, `pg_stat_user_tables`, `pg_stat_wal`, `pg_stat_checkpointer`, размеров таблиц/индексов и свободного диска.

---

## 8. Ближайшая контрольная точка

К концу недели должны быть достигнуты следующие результаты:

- pytest unit-набор проходит без failures и предупреждений о возврате `bool`;
- integration-тесты вынесены отдельно и корректно skip при недоступном SQL Server;
- `purchase_order` preflight = READY;
- `order_trans` имеет утверждённую индексируемую стратегию chunking;
- `picking_route` и `pack_task` валидированы через `validate-only` либо безопасный идемпотентный прогон;
- `sales_order` run 45 разобран, повторный запуск подтверждён;
- по `serial_mark` принято архитектурное решение без массового UPDATE RAW.
