# ДОРОЖНАЯ КАРТА UNIFIED ETL

**Обновлено:** 2026-08-03 15:23  
**Период детального плана:** 2026-08-03 — 2026-08-09.

---

## 1. Текущая точка

Подтверждено runtime:

```text
purchase_order:
- full completed, run 66
- validate-only completed, run 67
- repeat full completed, run 68

sales_order:
- run 45 diagnosed
- data impact: none
- resume run 45: not required
- preflight batch 100k: READY_WITH_WARNINGS
```

Текущий readiness:

```text
RUNTIME COMPLETED:
- purchase_order

READY FOR NEW FULL RUN:
- sales_order

READY FOR VALIDATION:
- picking_route
- pack_task

BLOCKED:
- order_trans
- serial_mark_normalization
- serial_mark
```

Главная цель периода — завершить reconciliation `purchase_order`, выполнить новый безопасный `sales_order full`, затем закрыть `picking_route` и `pack_task`.

---

## 2. Приоритеты

| Приоритет | Задача | Ожидаемый результат |
|---|---|---|
| P0 | Reconciliation `purchase_order` | Runs 66–68 проверены, дубли и NULL отсутствуют, counts объяснены |
| P0 | Новый `sales_order full` | Run COMPLETED с batch 100k |
| P0 | Validate `sales_order` | validate-only и RAW/DDS reconciliation выполнены |
| P1 | Валидировать `picking_route` | Расхождение RAW/DDS объяснено |
| P1 | Валидировать `pack_task` | Конфликтное поведение и counts подтверждены |
| P1 | Разблокировать `order_trans` | Mapping исправлен, выбран индексируемый chunk key |
| P1 | Исправить normalization preflight | Stage проходит полноценную read-only проверку |
| P1 | Утвердить архитектуру `serial_mark` | Staging/CTAS/source-key без массового UPDATE RAW |

---

## 3. План работ

### 3 августа — завершить малые и средние stages

#### `purchase_order`

Уже выполнено:

- preflight READY;
- full run 66 completed;
- validate-only run 67 completed;
- repeat full run 68 completed.

Осталось:

1. Проверить `etl.load_run` и `etl.load_chunk` для runs 66–68.
2. Сверить точные counts RAW/DDS.
3. Проверить отсутствие дублей по `(purchase_id, data_area_id)`.
4. Проверить NULL и пустые business keys.
5. Проверить фактические `rows_inserted`, `rows_updated`, `rows_conflicted`.

Критерий закрытия: stage переведён из `RUNTIME COMPLETED` в `CLOSED / RECONCILED`.

#### `sales_order`

Причина run 45 установлена:

```text
'PipelineSpec' object has no attribute 'chunk_strategy'
```

Run 45 упал до создания chunks и не изменил данные. Resume не требуется.

Планы batch 100k и 250k используют:

```text
Bitmap Heap Scan
→ Bitmap Index Scan on idx_salestable_recid_bigint
```

Рекомендуемый запуск:

```powershell
python -m ax_to_postgres_etl.pipelines.dds_cli `
    --mode full `
    --stage sales_order `
    --batch-size 100000
```

После завершения:

```powershell
python -m ax_to_postgres_etl.pipelines.dds_cli `
    --mode validate-only `
    --stage sales_order
```

Критерии:

- run COMPLETED;
- failed chunks = 0;
- функциональный индекс используется;
- WAL и длительность сохранены;
- RAW/DDS reconciliation выполнена.

---

### 4 августа — `picking_route` и `pack_task`

#### `picking_route`

Факты:

- RAW ~6.99 млн строк, 14.0 GB;
- key `recid_bigint int8`;
- Index Only Scan;
- WAL risk MEDIUM.

Работы:

1. Выполнить `validate-only`.
2. Проверить min/max key.
3. Проверить NULL и дубли conflict key.
4. Объяснить оценочную разницу RAW/DDS.
5. При необходимости выполнить безопасный full/resume.

#### `pack_task`

Факты:

- RAW ~7.62 млн строк, 3.1 GB;
- `numeric_text_range` использует индекс `recid`;
- range находится в Index Cond;
- WAL risk LOW.

Работы:

1. Выполнить `validate-only`.
2. Проверить оценочную разницу RAW/DDS.
3. Проверить дубли RAW и `ON CONFLICT`.
4. Зафиксировать итоговый run status.

---

### 5–6 августа — `order_trans`

Preflight BLOCKED шестью ошибками.

Необходимо:

1. Получить реальные RAW-колонки.
2. Исправить mappings `ordertransid`, `pickedqty`, `wastedqty`.
3. Выбрать индексируемую chunk strategy.
4. Не выполнять массовый UPDATE 44+ млн строк.
5. Получить `EXPLAIN` без `ANALYZE` для batch 100k и 250k.
6. Подтвердить отсутствие Seq Scan.
7. Оценить WAL, disk, rollback и resume.

Критерий: preflight READY либо утверждён технический план с DDL и оценкой рисков.

---

### 7 августа — `serial_mark_normalization` и `serial_mark`

#### Normalization

Текущий блокер:

```text
No column mapping defined
```

Нужно добавить mapping либо отдельный preflight-контракт для normalization stage.

#### `serial_mark`

Факты:

- RAW ~153.2 млн строк, 78.8 GB;
- `recid text` несовместим с `numeric_range`;
- `recid_bigint` index отсутствует;
- блокирующий Seq Scan;
- WAL risk HIGH.

Сравнить:

1. Числовой RECID при SQL Server → RAW.
2. Отдельную normalized staging table.
3. Одноразовый CTAS + индекс.

Для каждого варианта оценить полное чтение RAW, размер новой таблицы и индекса, WAL, блокировки, rollback, resume и повторное использование.

---

### 8–9 августа — недельный gate

1. Полный pytest.
2. Полный read-only preflight.
3. `dds_cli --mode status`.
4. Reconciliation завершённых stages.
5. Анализ WAL history.
6. Обновление STATUS/ROADMAP/gantt по фактическим run IDs.
7. Переход к MART только после подтверждения DDS.

---

## 4. Порядок выполнения

```text
purchase_order reconciliation
→ sales_order full 100k
→ sales_order validation
→ picking_route validation
→ pack_task validation
→ order_trans indexed design
→ serial_mark normalization design
→ serial_mark architecture
→ DDS reconciliation
→ MART
→ ML
```

---

## 5. Ограничения безопасности

- Не запускать blocked stage в `full` или `resume`.
- Не выполнять массовый UPDATE `raw_ax.alk_markserial` или `raw_ax.wmsordertrans`.
- Не считать `n_live_tup` точным count или прогрессом.
- Не выполнять `CREATE INDEX CONCURRENTLY` внутри транзакции.
- Не применять `VACUUM FULL` без окна полной блокировки и места для переписывания.
- Не выполнять тяжёлый `EXPLAIN ANALYZE` без ограниченного диапазона.
- Один тяжёлый stage за раз.
- Любая загрузка должна быть идемпотентной, возобновляемой и отменяемой.
