# ЕДИНЫЙ СТАТУС ПРОЕКТА

**Проект:** Anomaly Detection / Unified ETL для AX 2012 и WMS  
**Обновлено:** 2026-07-31  
**Основание:** скан ветки `main`, последних коммитов, PR, конфигурации `config/raw_to_dds.yaml` и ETL-кода.

> Важно: документ отражает состояние кода в GitHub. Фактическое состояние данных PostgreSQL необходимо подтверждать командами `preflight`, `status` и запросами к `etl.load_run` / `etl.load_chunk`.

---

## 1. Фактическое состояние

Основной контур проекта:

```text
SQL Server AX 2012/WMS → raw_ax → stage_ax (при необходимости) → dds → mart/mart_ax → аналитика/ML
```

Unified ETL реализует:

- `PipelineRunner`;
- `RunManager`;
- `ChunkManager`;
- `RetryPolicy`;
- `PostgresRuntime`;
- `RawToDdsAdapter`;
- advisory lock, heartbeat, resume и обработку Ctrl+C;
- режимы CLI `preflight`, `full`, `resume`, `restart-stage`, `validate-only`, `status`.

RAW → DDS выполняется внутри PostgreSQL через `INSERT ... SELECT`. Передача больших таблиц через pandas не используется.

---

## 2. Последние изменения

### 2026-07-31 — PR #9

Исправлен preflight для stages со стратегией `full_table`:

- больше не требуется B-tree chunk-индекс источника;
- `EXPLAIN (FORMAT JSON)` выполняется без диапазонного `WHERE`;
- логика `numeric_text_range` зависит от `chunk_strategy`, а не только от типа ключа;
- добавлены регрессионные тесты;
- исправление предназначено прежде всего для `purchase_order`.

Оставшийся блокер `purchase_order`: необходимо сверить колонки `vendaccount` и `orderdate` с фактической структурой `raw_ax.purchtable`.

### 2026-07-31 — PR #10

Исправлена передача `chunk_strategy` из конфигурации stage в `PipelineSpec` и далее в исполняемый pipeline. Это критично для корректного разделения стратегий:

- `numeric_range`;
- `numeric_text_range`;
- `lexical_range`;
- `full_table`.

Без этого исправления stage мог выполняться или проверяться по стратегии по умолчанию.

### 2026-07-30–31

Добавлены и доработаны диагностики `sales_order` и `purchase_order`, конфигурация RAW → DDS и обработка полного чтения небольшой таблицы `purchtable`.

---

## 3. Реестр stages RAW → DDS

| Stage | Источник | Цель | Стратегия chunks | Состояние кода |
|---|---|---|---|---|
| `serial_mark_normalization` | `raw_ax.alk_markserial` | `stage_ax.alk_markserial_normalized` | `lexical_range` | Реализован, требует runtime-проверки |
| `serial_mark` | `raw_ax.alk_markserial` | `dds.serial_mark` | `numeric_range` | Реализован, загрузка большой таблицы требует завершения и валидации |
| `picking_route` | `raw_ax.wmspickingroute` | `dds.picking_route` | `numeric_range` | Реализован, требуется preflight/status |
| `pack_task` | `raw_ax.lfl_scspacktask` | `dds.pack_task` | `numeric_text_range` | Реализован, требуется подтверждение Index Cond |
| `order_trans` | `raw_ax.wmsordertrans` | `dds.order_trans` | `numeric_range` | Реализован, требуется runtime-проверка ключа и индекса |
| `sales_order` | `raw_ax.salestable` | `dds.sales_order` | `numeric_range`, batch 250k | Реализован, диагностика добавлена |
| `purchase_order` | `raw_ax.purchtable` | `dds.purchase_order` | `full_table` | Preflight исправлен; колонки источника требуют сверки |

---

## 4. Критические риски

### `raw_ax.alk_markserial`

Таблица содержит около 152 млн строк. Запрещено без отдельного плана выполнять массовый `UPDATE` всей RAW-таблицы для преобразования `recid`.

Основные риски:

- большой объём WAL;
- новые версии всех строк и dead tuples;
- длительный rollback;
- рост `pg_wal` и риск заполнения диска;
- последующий тяжёлый VACUUM;
- блокировки и деградация I/O.

Предпочтительный путь: нормализованная staging-таблица или числовой ключ, сформированный при SQL Server → RAW.

### Конфигурация ключей

Для каждого stage необходимо подтвердить полное совпадение:

- выражения фильтрации;
- типа параметров границ;
- выражения/колонки индекса;
- `Index Cond` в плане;
- conflict key и уникального ограничения DDS.

### `full_table`

Допустим только для реально небольшой таблицы. Стратегия не поддерживает полноценный resume внутри одного полного чтения и должна применяться после оценки размера таблицы и WAL.

---

## 5. Текущий статус готовности

| Компонент | Статус |
|---|---|
| SQL Server → RAW | Работает; не переписывать без веской причины |
| Архитектура Unified ETL | Реализована |
| Read-only preflight | Реализован; исправлена поддержка `full_table` |
| Передача `chunk_strategy` | Исправлена в PR #10 |
| Resume/heartbeat/chunks | Реализованы |
| WAL monitoring | Реализован отдельным collector-скриптом |
| Анализ WAL CSV | Выполняется notebook без подключения к PostgreSQL |
| Полная RAW → DDS интеграция | Не подтверждена для всех stages |
| MART/ML на актуальном полном DDS | Зависит от завершения и валидации RAW → DDS |

Оценивать общий процент готовности без runtime-данных некорректно. Кодовая база близка к рабочему контуру, но промышленная готовность определяется успешным preflight, полной загрузкой, resume-тестами и сверкой данных.

---

## 6. Безопасная последовательность следующих действий

### Диагностика — read-only

```powershell
cd "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2"

python -m ax_to_postgres_etl.pipelines.dds_cli --mode preflight
python -m ax_to_postgres_etl.pipelines.dds_cli --mode status
python -m pytest tests -q --import-mode=importlib --ignore=tests/test_parallel_inventtable.py
```

Отдельно проверить stages:

```powershell
python -m ax_to_postgres_etl.pipelines.dds_cli --mode preflight --stage purchase_order --batch-size 100000
python -m ax_to_postgres_etl.pipelines.dds_cli --mode preflight --stage sales_order --batch-size 250000
python -m ax_to_postgres_etl.pipelines.dds_cli --mode preflight --stage pack_task --batch-size 100000
python -m ax_to_postgres_etl.pipelines.dds_cli --mode preflight --stage serial_mark --batch-size 500000
```

### Перед изменяющим запуском

Проверить:

1. свободное место на диске PostgreSQL;
2. `pg_stat_activity`;
3. `pg_stat_progress_create_index`;
4. `pg_stat_progress_vacuum`;
5. `pg_stat_user_tables`;
6. `pg_stat_wal` и `pg_stat_checkpointer`;
7. существующие индексы и уникальные ограничения;
8. отсутствие активного run для того же stage.

### Изменяющие операции

Запускать по одному stage только после успешного preflight. Для большой таблицы использовать измеренный batch size и WAL monitor:

```powershell
python monitoring\postgres_wal_monitor.py --interval 60 --duration 8h
python -m ax_to_postgres_etl.pipelines.dds_cli --mode full --stage serial_mark --batch-size 500000
```

При прерывании продолжать через:

```powershell
python -m ax_to_postgres_etl.pipelines.dds_cli --mode resume --stage serial_mark
```

---

## 7. Критерии завершения stage

Stage считается завершённым только когда:

- `etl.load_run.status = COMPLETED`;
- отсутствуют `FAILED` и незавершённые chunks;
- heartbeat и finish time корректны;
- целевая таблица не пустая;
- отсутствуют дубликаты по conflict key;
- отсутствуют недопустимые `NULL` в обязательных ключах;
- проверены min/max ключа и контрольные диапазоны;
- повторный запуск не создаёт дубликаты;
- выполнена сверка RAW и DDS;
- после загрузки нет критичного роста WAL, dead tuples и нехватки диска.
