# ДОРОЖНАЯ КАРТА UNIFIED ETL

**Обновлено:** 2026-08-03 14:25  
**Период детального плана:** 2026-08-03 — 2026-08-09.

---

## 1. Текущая точка

Полный read-only preflight от 2026-08-03 14:24 подтвердил:

```text
READY:               purchase_order, picking_route, pack_task
READY_WITH_WARNINGS: sales_order
BLOCKED:             order_trans, serial_mark_normalization, serial_mark
```

Общие условия среды на момент проверки благоприятные:

- конфликтующих ETL runs и locks нет;
- autovacuum и CREATE INDEX на target не выполняются;
- долгих транзакций нет;
- свободно около 1.4 TB.

Главная цель периода — выполнить runtime-валидацию READY stages и оформить технические решения для трёх blocked stages без массовых UPDATE больших RAW-таблиц.

---

## 2. Приоритеты

| Приоритет | Задача | Ожидаемый результат |
|---|---|---|
| P0 | Запустить `purchase_order` | run COMPLETED, validate-only успешен, repeat-run идемпотентен |
| P0 | Диагностировать `sales_order` run 45 | причина установлена, выбран resume/restart-stage |
| P1 | Валидировать `picking_route` | reconciliation объясняет оценочную разницу RAW/DDS |
| P1 | Валидировать `pack_task` | reconciliation и конфликтное поведение подтверждены |
| P1 | Разблокировать `order_trans` | mapping исправлен, выбран индексируемый chunk key |
| P1 | Исправить normalization preflight | stage проходит полноценную read-only проверку |
| P1 | Утвердить архитектуру `serial_mark` | staging/CTAS/source-key без массового UPDATE RAW |
| P2 | Обновить runtime baseline | pytest, status, run IDs, WAL и длительность зафиксированы |

---

## 3. План работ

### 3 августа — `purchase_order` и `sales_order`

#### `purchase_order`

Preflight уже READY:

- RAW: ~258,518 строк, 293.3 MB;
- DDS: ~257,817 строк, 68.8 MB;
- `full_table` корректен;
- unique business key `(purchase_id, data_area_id)` подтверждён;
- WAL risk LOW;
- Seq Scan допустим, поскольку выполняется полное чтение небольшой таблицы.

Перед запуском:

```powershell
python monitoring\postgres_wal_monitor.py --interval 60 --duration 2h
python -m ax_to_postgres_etl.pipelines.dds_cli --mode status
```

Последовательность:

```powershell
python -m ax_to_postgres_etl.pipelines.dds_cli `
    --mode full `
    --stage purchase_order `
    --batch-size 250000

python -m ax_to_postgres_etl.pipelines.dds_cli `
    --mode validate-only `
    --stage purchase_order

python -m ax_to_postgres_etl.pipelines.dds_cli `
    --mode full `
    --stage purchase_order `
    --batch-size 250000
```

Критерии:

- новый run = COMPLETED;
- target не содержит дублей по `(purchase_id, data_area_id)`;
- повторный запуск не увеличивает логическое количество строк;
- `DO UPDATE` работает предсказуемо;
- WAL и длительность сохранены.

#### `sales_order`

Preflight = READY_WITH_WARNINGS. Functional index соответствует выражению, но план использует Bitmap Heap Scan.

Сначала выполнить только read-only диагностику:

```powershell
.\monitoring\run_sales_order_run45_diagnostic.ps1
```

После анализа batch 100k/250k выбрать `resume` или `restart-stage`. До фиксации причины run 45 изменяющий запуск не выполнять.

---

### 4 августа — `picking_route` и `pack_task`

#### `picking_route`

Факты preflight:

- RAW ~6.99 млн строк, 14.0 GB;
- DDS ~6.53 млн строк, 1.5 GB;
- key `recid_bigint int8`;
- Index Only Scan;
- WAL risk MEDIUM.

Работы:

1. Выполнить `validate-only`.
2. Проверить min/max chunk key.
3. Проверить NULL и дубли conflict key.
4. Объяснить оценочную разницу около 456 тыс. строк.
5. Только после reconciliation решать вопрос о full/resume.

#### `pack_task`

Факты preflight:

- RAW ~7.62 млн строк, 3.1 GB;
- DDS ~7.62 млн строк, 871.1 MB;
- `numeric_text_range` использует unique index `recid`;
- predicate находится в Index Cond;
- WAL risk LOW.

Работы:

1. Выполнить `validate-only`.
2. Проверить оценочную разницу около 527 строк.
3. Проверить RAW duplicates и `ON CONFLICT`.
4. Зафиксировать итоговый статус run.

---

### 5–6 августа — `order_trans`

Preflight BLOCKED: 6 ошибок.

Необходимо:

1. Получить фактические RAW-колонки.
2. Исправить mappings `ordertransid`, `pickedqty`, `wastedqty`.
3. Убрать ожидание несуществующего `recid_bigint` либо создать его безопасным архитектурным способом.
4. Выбрать один подход:
   - числовой ключ при SQL Server → RAW;
   - отдельная normalized/staging table;
   - functional index при полном совпадении выражения.
5. Не выполнять массовый UPDATE 44+ млн строк.
6. Получить `EXPLAIN` без `ANALYZE` для batch 100k и 250k.
7. Подтвердить отсутствие Seq Scan и возможность resume.
8. Оценить WAL, disk и rollback.

Критерий готовности: preflight READY либо утверждён отдельный технический план с DDL, индексом, оценкой места и сценарием отмены.

---

### 7 августа — `serial_mark_normalization` и `serial_mark`

#### Normalization stage

Текущий блокер:

```text
No column mapping defined
```

Необходимо решить, должен ли stage:

- использовать обычный `columns` mapping;
- либо иметь отдельный normalization-контракт preflight.

После исправления preflight обязан read-only проверить source/target, выражение преобразования, ключ, индексируемость, EXPLAIN, размеры, WAL risk и disk.

#### `serial_mark`

Факты:

- RAW ~153.2 млн строк, 78.8 GB;
- DDS ~151.7 млн строк, 36.3 GB;
- source `recid text` несовместим с `numeric_range`;
- `recid_bigint` и индекс отсутствуют;
- план использует блокирующий Seq Scan;
- WAL risk HIGH.

Сравнить варианты:

1. Числовой RECID во время SQL Server → RAW.
2. Отдельная normalized staging table.
3. Одноразовый CTAS + индекс.

Для каждого варианта оценить:

- полное чтение 78.8 GB RAW;
- место под новую таблицу и индекс;
- WAL;
- время;
- блокировки;
- rollback;
- resume;
- повторное использование.

Массовый UPDATE `raw_ax.alk_markserial` запрещён без отдельного утверждённого плана.

---

### 8–9 августа — контрольный прогон

1. Полный pytest.
2. Полный read-only preflight.
3. `dds_cli --mode status`.
4. Reconciliation завершённых stages.
5. Сводка WAL history.
6. Обновление STATUS/ROADMAP/gantt по фактическим run IDs.
7. Переход к MART только после подтверждения DDS.

---

## 4. Порядок выполнения

```text
purchase_order load and validation
→ sales_order diagnosis and recovery
→ picking_route validation
→ pack_task validation
→ order_trans indexed design
→ serial_mark normalization design
→ serial_mark
→ DDS reconciliation
→ MART
→ ML
```

Для каждого изменяющего stage:

```text
read-only diagnostics
→ preflight
→ disk/WAL/activity/index checks
→ WAL monitor
→ full/resume/restart-stage
→ validate-only
→ status
→ RAW/DDS reconciliation
→ documentation
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
