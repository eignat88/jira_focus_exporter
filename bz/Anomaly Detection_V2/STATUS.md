# ЕДИНЫЙ СТАТУС ПРОЕКТА

**Проект:** Anomaly Detection / Unified ETL для AX 2012 и WMS  
**Обновлено:** 2026-08-03 14:25  
**Основание:** ветка `main` и полный read-only preflight всех RAW → DDS stages от 2026-08-03 14:24 с `batch_size=250000` и `count_mode=estimate`.

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

SQL Server → RAW работает. RAW → DDS выполняется внутри PostgreSQL через `INSERT INTO ... SELECT FROM ...`; pandas на этом этапе не используется.

Полный preflight был read-only: он проверил конфигурацию, таблицы, колонки, типы ключей, индексы, уникальные ограничения, `EXPLAIN` без `ANALYZE`, блокировки, autovacuum, создание индексов, долгие транзакции и свободное место.

На момент проверки:

- конфликтующих ETL runs нет;
- конфликтующих locks на target нет;
- autovacuum на target не активен;
- `CREATE INDEX` не выполняется;
- долгих транзакций нет;
- свободно около 1.4 TB.

---

## 2. Сводка полного preflight

```text
READY:               3 stages
READY_WITH_WARNINGS: 1 stage
BLOCKED:             3 stages
```

| Stage | Результат | RAW | DDS / stage target | План и ключевые факты |
|---|---|---:|---:|---|
| `purchase_order` | READY | ~258,518 строк, 293.3 MB | ~257,817 строк, 68.8 MB | настоящий `full_table`; mapped SELECT проходит EXPLAIN; Seq Scan допустим для таблицы такого размера; WAL LOW |
| `sales_order` | READY_WITH_WARNINGS | ~3,653,448 строк, 3.7 GB | ~3,652,685 строк, 821.0 MB | functional index используется; план Bitmap Heap Scan; WAL LOW |
| `picking_route` | READY | ~6,986,292 строк, 14.0 GB | ~6,529,987 строк, 1.5 GB | `recid_bigint int8`; B-tree; Index Only Scan; WAL MEDIUM |
| `pack_task` | READY | ~7,622,862 строк, 3.1 GB | ~7,622,335 строк, 871.1 MB | `numeric_text_range`; range находится в Index Cond; Index Only Scan; WAL LOW |
| `order_trans` | BLOCKED | ~44,359,952 строк, 23.7 GB | ~45,750,463 строк, 9.8 GB | 6 ошибок: text key при `numeric_range`, 3 отсутствующие RAW-колонки, нет `recid_bigint` index, EXPLAIN не выполняется |
| `serial_mark_normalization` | BLOCKED | не оценено | не оценено | отсутствует columns mapping для текущего контракта preflight |
| `serial_mark` | BLOCKED | ~153,227,536 строк, 78.8 GB | ~151,698,873 строк, 36.3 GB | text key при `numeric_range`; нет `recid_bigint` B-tree; блокирующий Seq Scan; WAL HIGH |

---

## 3. Подробный статус stages

### 3.1. `purchase_order` — READY

Источник и target:

```text
raw_ax.purchtable → dds.purchase_order
```

Подтверждено:

- стратегия `full_table` корректно допускает source key `recid text`;
- B-tree chunk index не требуется;
- составной business key `(purchase_id, data_area_id)` существует;
- уникальный индекс `ux_purchase_order_business_key` полностью совпадает с conflict key;
- mapped SELECT проходит `EXPLAIN` без `ANALYZE`;
- источник около 293.3 MB и 258.5 тыс. строк;
- WAL risk LOW;
- свободно 1.4 TB.

Seq Scan здесь ожидаем и допустим, поскольку `full_table` должен прочитать всю сравнительно небольшую таблицу. Stage готов к изменяющему runtime-запуску после контрольной проверки activity/WAL/disk.

Следующий шаг:

```text
full → validate-only → повторный запуск → проверка DO UPDATE и отсутствия дублей
```

### 3.2. `sales_order` — READY_WITH_WARNINGS

Источник и target:

```text
raw_ax.salestable → dds.sales_order
```

Подтверждено:

- выражение chunking `btrim(recid)::bigint` соответствует functional index;
- индекс `idx_salestable_recid_bigint` valid, ready и пригоден для chunking;
- target имеет unique key по `source_recid`;
- ошибок preflight нет;
- WAL risk LOW.

Предупреждение:

```text
Plan uses Bitmap Heap Scan (depends on selectivity)
```

Stage структурно готов, но перед resume/restart необходимо завершить диагностику run 45 и сравнить планы batch 100k и 250k. Новый изменяющий запуск без разбора причины run 45 не выполнять.

### 3.3. `picking_route` — READY

Подтверждено:

- source key `recid_bigint int8` совместим с `numeric_range`;
- индекс `idx_wmspickingroute_recid_bigint` valid и ready;
- план использует Index Only Scan;
- target имеет unique index по `picking_route_id`;
- WAL risk MEDIUM.

Оценочная разница RAW/DDS составляет около 456 тыс. строк. Она не доказывает потерю данных: необходимо выполнить reconciliation с учётом преобразований, конфликтов и оценочного характера статистики.

### 3.4. `pack_task` — READY

Подтверждено:

- `numeric_text_range` совместим с `recid text`;
- unique index `idx_lfl_scspacktask_recid` используется для chunking;
- план использует Index Only Scan;
- range predicate находится в Index Cond;
- target имеет primary key по `task_id`;
- WAL risk LOW.

Оценочная разница RAW/DDS — около 527 строк. Требуется reconciliation и проверка конфликтов/дублей.

### 3.5. `order_trans` — BLOCKED

Блокирующие ошибки:

1. `numeric_range` требует числовой source key, но `recid` имеет тип text.
2. В mapping отсутствуют RAW-колонки `ordertransid`.
3. В mapping отсутствует RAW-колонка `pickedqty`.
4. В mapping отсутствует RAW-колонка `wastedqty`.
5. Нет B-tree index по ожидаемому `recid_bigint`.
6. `EXPLAIN` падает, потому что `recid_bigint` не существует.

Для таблицы около 44.4 млн строк и 23.7 GB запрещено устранять проблему массовым UPDATE RAW. Требуется исправить mapping и выбрать индексируемую chunk strategy.

### 3.6. `serial_mark_normalization` — BLOCKED

Preflight остановлен на проверке конфигурации:

```text
No column mapping defined
```

Необходимо либо добавить явный mapping, либо выделить отдельный контракт preflight для normalization stage. Изменяющие операции не запускать.

### 3.7. `serial_mark` — BLOCKED

Блокеры:

- source key `recid` имеет тип text, а стратегия задана как `numeric_range`;
- отсутствует B-tree index по `recid_bigint`;
- план использует блокирующий Seq Scan по таблице около 153.2 млн строк и 78.8 GB;
- для source не зафиксирован ANALYZE;
- WAL risk HIGH.

Существующая `dds.serial_mark` содержит оценочно около 151.7 млн строк, но это не делает pipeline безопасным для повторного запуска.

Запрещён массовый UPDATE `raw_ax.alk_markserial`. Допустимые направления:

- формирование числового RECID при SQL Server → RAW;
- отдельная normalized/staging table;
- одноразовый CTAS с последующим индексом;
- functional index только при полном совпадении выражения запроса и индекса.

---

## 4. Текущий readiness

```text
READY FOR RUNTIME LOAD / VALIDATION:
- purchase_order
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

## 5. Безопасная последовательность действий

1. Выполнить `purchase_order full`, затем `validate-only` и повторный идемпотентный запуск.
2. Запустить read-only диагностику `sales_order` run 45; только после неё выбрать `resume` или `restart-stage`.
3. Выполнить `validate-only` и reconciliation для `picking_route` и `pack_task`.
4. Для `order_trans` исправить mapping и утвердить индексируемую стратегию без массового UPDATE.
5. Для `serial_mark_normalization` исправить контракт preflight.
6. Для `serial_mark` утвердить staging/CTAS/source-key архитектуру и benchmark batch 100k, 250k, 500k и 1M.

---

## 6. Правила безопасности

Перед каждым изменяющим запуском проверить:

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

- blocked stage запрещено запускать в `full` или `resume`;
- не считать `n_live_tup` точным count или прогрессом;
- не выполнять `CREATE INDEX CONCURRENTLY` внутри транзакции;
- не применять `VACUUM FULL` без окна полной блокировки и места для полного переписывания;
- `EXPLAIN ANALYZE` на больших таблицах допускается только для безопасного ограниченного диапазона;
- один тяжёлый stage за раз.

---

## 7. Следующая контрольная точка

После runtime-запусков сохранить:

- новый `dds_cli --mode status`;
- run IDs и статусы;
- результаты `validate-only`;
- reconciliation RAW/DDS;
- фактические WAL delta и длительность;
- актуальный pytest baseline.
