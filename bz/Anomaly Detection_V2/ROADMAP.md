# ДОРОЖНАЯ КАРТА UNIFIED ETL

**Обновлено:** 2026-07-31  
**Контур:** SQL Server AX 2012/WMS → `raw_ax` → `stage_ax` → `dds` → `mart` / `mart_ax` → аналитика и ML.

---

## 1. Текущая точка

Архитектурный каркас Unified ETL реализован. Последние изменения устранили две ошибки исполнения и preflight:

1. `chunk_strategy` теперь передаётся из конфигурации stage в `PipelineSpec`;
2. preflight корректно обрабатывает `full_table` и не требует диапазонный индекс.

Основная задача проекта сейчас — не добавление новых абстракций, а последовательная промышленная валидация и загрузка каждого RAW → DDS stage.

---

## 2. Приоритеты

| Приоритет | Работа | Результат |
|---|---|---|
| P0 | Полный прогон unit-тестов после PR #9 и #10 | Нет регрессий в runner, preflight и стратегиях chunks |
| P0 | Preflight всех enabled stages | Все проверки read-only проходят либо формируют конкретные блокеры |
| P0 | Исправить mapping `purchase_order` | Колонки YAML соответствуют `raw_ax.purchtable` |
| P0 | Завершить и валидировать `serial_mark` | Полная возобновляемая загрузка 152 млн строк без массового UPDATE RAW |
| P1 | Валидировать `pack_task`, `order_trans`, `sales_order`, `picking_route` | Подтверждены индексы, Index Cond, conflict keys и resume |
| P1 | Зафиксировать runtime-статус в документации | Статусы основаны на `etl.load_run` и `etl.load_chunk` |
| P1 | Интеграционная проверка MART | MART строится только после подтверждённого DDS |
| P2 | Нагрузочные замеры batch size | Сравнение 100k, 250k, 500k и 1M по времени, WAL и I/O |
| P2 | Cleanup конфигурации и дублей каталогов | Единственный canonical config и комплект документов |

---

## 3. Этапы выполнения

### Этап A. Стабилизация кода

- [x] Реализовать Unified ETL runner;
- [x] Реализовать advisory lock, heartbeat, chunks и resume;
- [x] Реализовать read-only preflight;
- [x] Исправить preflight для `full_table`;
- [x] Передавать `chunk_strategy` через `PipelineSpec`;
- [ ] Выполнить полный тестовый прогон в чистом окружении;
- [ ] Разделить unit- и integration-тесты, чтобы отсутствие SQL Server не считалось unit-регрессией.

### Этап B. Проверка конфигурации RAW → DDS

- [ ] Проверить фактические колонки всех RAW-таблиц;
- [ ] Проверить типы chunk keys;
- [ ] Проверить B-tree/functional indexes;
- [ ] Проверить уникальные ограничения DDS;
- [ ] Проверить `EXPLAIN` без `ANALYZE`;
- [ ] Устранить блокеры `purchase_order` по `vendaccount` и `orderdate`;
- [ ] Подтвердить безопасное выражение ключа для `sales_order`;
- [ ] Подтвердить `Index Cond` для `pack_task`.

### Этап C. Загрузка DDS

Рекомендуемый порядок:

1. `purchase_order` — небольшая таблица, проверка `full_table`;
2. `sales_order` — проверка numeric range и batch 250k;
3. `picking_route`;
4. `pack_task`;
5. `order_trans`;
6. `serial_mark_normalization` или утверждённая альтернатива;
7. `serial_mark` — самая тяжёлая загрузка.

Для каждого stage:

```text
preflight → full/resume → validate-only → status → сверка RAW/DDS → фиксация результата
```

### Этап D. MART и ML

- [ ] Обновлять витрины только после подтверждения полноты DDS;
- [ ] Проверить идемпотентность MART;
- [ ] Пересчитать признаки на актуальных данных;
- [ ] Повторно оценить Isolation Forest, LOF и Autoencoder;
- [ ] LSTM Autoencoder выполнять после стабилизации данных, а не параллельно с незавершённой загрузкой.

---

## 4. Критический путь

```text
Тесты
  ↓
Preflight всех stages
  ↓
Исправление mapping/index blockers
  ↓
Загрузка и валидация малых DDS stages
  ↓
Нормализация ключа ALK_MARKSERIAL
  ↓
Полная загрузка dds.serial_mark с resume и WAL monitoring
  ↓
Контрольная сверка DDS
  ↓
Пересчёт MART
  ↓
Повторная оценка ML
```

---

## 5. Команды ближайшего цикла

### Read-only

```powershell
cd "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection_V2"

python -m pytest tests -q --import-mode=importlib --ignore=tests/test_parallel_inventtable.py
python -m ax_to_postgres_etl.pipelines.dds_cli --mode preflight
python -m ax_to_postgres_etl.pipelines.dds_cli --mode status
```

### По stages

```powershell
python -m ax_to_postgres_etl.pipelines.dds_cli --mode preflight --stage purchase_order --batch-size 100000
python -m ax_to_postgres_etl.pipelines.dds_cli --mode preflight --stage sales_order --batch-size 250000
python -m ax_to_postgres_etl.pipelines.dds_cli --mode preflight --stage pack_task --batch-size 100000
python -m ax_to_postgres_etl.pipelines.dds_cli --mode preflight --stage order_trans --batch-size 100000
python -m ax_to_postgres_etl.pipelines.dds_cli --mode preflight --stage picking_route --batch-size 100000
python -m ax_to_postgres_etl.pipelines.dds_cli --mode preflight --stage serial_mark --batch-size 500000
```

---

## 6. Правила безопасности

- Не выполнять массовый `UPDATE raw_ax.alk_markserial` на 150+ млн строк.
- Не выполнять `CREATE INDEX CONCURRENTLY` внутри транзакции.
- Не выполнять `VACUUM FULL` без окна полной блокировки и места для переписывания таблицы.
- Перед тяжёлой операцией проверять диск, активные процессы, WAL, vacuum и индексы.
- `n_live_tup` и `n_dead_tup` использовать только как оценки, не как точный прогресс текущей транзакции.
- Не путать текущий размер каталога `pg_wal` и накопительный `wal_bytes` из `pg_stat_wal`.
- Любой production-like запуск должен быть идемпотентным, отменяемым и возобновляемым.
