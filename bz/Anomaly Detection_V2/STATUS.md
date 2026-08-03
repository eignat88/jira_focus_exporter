# ЕДИНЫЙ СТАТУС ПРОЕКТА

**Проект:** Anomaly Detection / Unified ETL для AX 2012 и WMS  
**Обновлено:** 2026-08-03 15:23  
**Основание:** ветка `main`, полный read-only preflight от 2026-08-03, runtime-запуски `purchase_order` и read-only диагностика `sales_order` run 45.

---

## 1. Фактическое состояние

Основной контур:

```text
SQL Server AX 2012/WMS → raw_ax → stage_ax → dds → mart/mart_ax → аналитика/ML
```

SQL Server → RAW работает. RAW → DDS выполняется внутри PostgreSQL через `INSERT INTO ... SELECT FROM ...`; pandas на этом этапе не используется.

На момент последнего preflight:

- конфликтующих ETL runs и locks нет;
- autovacuum и CREATE INDEX на target не выполняются;
- долгих транзакций нет;
- свободно около 1.4 TB.

---

## 2. Подтверждённый runtime-статус

### `purchase_order` — RUNTIME COMPLETED

Источник и target:

```text
raw_ax.purchtable → dds.purchase_order
```

Подтверждено:

- preflight = `READY`;
- `full_table` корректно работает без `recid_bigint`;
- составной conflict key `(purchase_id, data_area_id)` подтверждён;
- unique index `ux_purchase_order_business_key` совпадает с conflict key;
- первый полный запуск завершён: `run_id=66`;
- `validate-only` завершён: `run_id=67`;
- повторный полный запуск завершён: `run_id=68`;
- повторный запуск не завершился ошибкой и подтверждает операционную идемпотентность;
- WAL risk LOW;
- Seq Scan допустим, поскольку `full_table` читает сравнительно небольшую таблицу около 293 MB.

Осталось выполнить финальную reconciliation:

- точные counts RAW/DDS;
- отсутствие дублей по `(purchase_id, data_area_id)`;
- отсутствие NULL/пустых business keys;
- проверка метрик `rows_inserted`, `rows_updated`, `rows_conflicted` для runs 66–68.

### `sales_order` — READY FOR NEW FULL RUN

Источник и target:

```text
raw_ax.salestable → dds.sales_order
```

Read-only диагностика run 45 установила точную причину:

```text
'PipelineSpec' object has no attribute 'chunk_strategy'
```

Факты по run 45:

- run завершился ошибкой до создания chunks;
- `total_chunks=0`;
- `rows_read=0`;
- `rows_inserted=0`;
- данные не изменялись;
- `resume` run 45 не требуется.

Новый preflight с `batch_size=100000`:

```text
READY_WITH_WARNINGS
errors: 0
warnings: 1
```

Подтверждено:

- functional index `idx_salestable_recid_bigint` valid и ready;
- выражение `(btrim(recid))::bigint` совпадает с выражением индекса;
- unique key `uq_sales_order_source_recid` существует;
- Seq Scan отсутствует;
- планы 100k и 250k используют `Bitmap Heap Scan → Bitmap Index Scan`;
- range predicate находится в `Index Cond`;
- WAL risk LOW.

Рекомендуемый следующий запуск:

```powershell
python -m ax_to_postgres_etl.pipelines.dds_cli `
    --mode full `
    --stage sales_order `
    --batch-size 100000
```

---

## 3. Сводка RAW → DDS stages

| Stage | Текущий статус | Следующее действие |
|---|---|---|
| `purchase_order` | RUNTIME COMPLETED, reconciliation required | Проверить runs 66–68 и точную сверку RAW/DDS |
| `sales_order` | READY_WITH_WARNINGS, run 45 diagnosed | Новый `full` с batch 100k, затем validate-only |
| `picking_route` | READY | Validate-only и reconciliation |
| `pack_task` | READY | Validate-only и reconciliation |
| `order_trans` | BLOCKED | Исправить mapping и выбрать индексируемый chunk key |
| `serial_mark_normalization` | BLOCKED | Исправить контракт preflight или добавить columns mapping |
| `serial_mark` | BLOCKED | Утвердить staging/CTAS/source-key архитектуру без массового UPDATE RAW |

---

## 4. Полный preflight baseline

```text
READY:               purchase_order, picking_route, pack_task
READY_WITH_WARNINGS: sales_order
BLOCKED:             order_trans, serial_mark_normalization, serial_mark
```

### `picking_route`

- RAW ~6.99 млн строк, 14.0 GB;
- source key `recid_bigint int8`;
- Index Only Scan;
- WAL risk MEDIUM;
- требуется reconciliation оценочной разницы RAW/DDS.

### `pack_task`

- RAW ~7.62 млн строк, 3.1 GB;
- `numeric_text_range` использует индекс `recid`;
- range находится в `Index Cond`;
- Index Only Scan;
- WAL risk LOW;
- требуется reconciliation оценочной разницы RAW/DDS.

### `order_trans` — BLOCKED

Блокеры:

1. `recid text` несовместим с `numeric_range`.
2. Отсутствует RAW-колонка `ordertransid`.
3. Отсутствует RAW-колонка `pickedqty`.
4. Отсутствует RAW-колонка `wastedqty`.
5. Нет B-tree index по ожидаемому `recid_bigint`.
6. `EXPLAIN` обращается к несуществующему `recid_bigint`.

Таблица около 44.4 млн строк и 23.7 GB. Массовый UPDATE RAW запрещён без отдельной оценки WAL, disk, rollback и dead tuples.

### `serial_mark_normalization` — BLOCKED

Текущий блокер:

```text
No column mapping defined
```

Необходимо добавить mapping либо отдельный preflight-контракт для normalization stage.

### `serial_mark` — BLOCKED

- RAW ~153.2 млн строк, 78.8 GB;
- `recid` имеет тип text;
- `numeric_range` ожидает числовой key;
- B-tree index по `recid_bigint` отсутствует;
- план использует блокирующий Seq Scan;
- WAL risk HIGH.

Массовый UPDATE `raw_ax.alk_markserial` не выполнять.

---

## 5. Безопасная последовательность действий

1. Выполнить reconciliation `purchase_order` для runs 66–68.
2. Запустить новый `sales_order full` с batch 100k.
3. После завершения выполнить `sales_order validate-only` и reconciliation.
4. Валидировать `picking_route` и `pack_task`.
5. Разблокировать `order_trans` через исправление mapping и индексируемую chunk strategy.
6. Исправить preflight `serial_mark_normalization`.
7. Утвердить staging/CTAS/source-key решение для `serial_mark`.

---

## 6. Правила безопасности

Перед изменяющим запуском проверять:

```text
pg_stat_activity
pg_stat_progress_create_index
pg_stat_progress_vacuum
pg_stat_user_tables
pg_stat_wal
pg_stat_checkpointer
размеры таблиц и индексов
свободное место на диске
```

Дополнительно:

- blocked stage не запускать в `full` или `resume`;
- не считать `n_live_tup` точным count или прогрессом;
- не выполнять `CREATE INDEX CONCURRENTLY` внутри транзакции;
- не применять `VACUUM FULL` без окна полной блокировки и места для переписывания;
- один тяжёлый stage за раз;
- любой запуск должен быть идемпотентным, отменяемым и возобновляемым там, где chunk strategy это позволяет.
