# DDS Load Monitoring System

**Status**: READY
**Date**: 2026-07-22
**Task**: T32/T33 - DDS/Mart update with progress monitoring

---

## 1. Описание

Система мониторинга загрузки DDS и Mart таблиц с отслеживанием прогресса в реальном времени, расчётом ETA и историей производительности.

### Проблема

При запуске SQL-скриптов через `cur.execute(sql)` невозможно отслеживать:
- Какая таблица загружается
- Сколько строк обработано
- Сколько времени осталось
- Завис ли процесс

### Решение

Разделение загрузки на независимые этапы с мониторингом каждого из них.

---

## 2. Структура файлов

```
D:\py_pro\jira_focus_exporter\bz\Anomaly Detection\
├── sql\postgres\dds_load\
│   ├── 00_prepare_monitoring.sql    # Таблицы мониторинга
│   ├── 01_serial_mark.sql           # Этап 2: dds.serial_mark
│   ├── 02_picking_route.sql         # Этап 3: dds.picking_route
│   ├── 03_pack_task.sql             # Этап 4: dds.pack_task
│   ├── 04_order_trans.sql           # Этап 5: dds.order_trans
│   ├── 05_sales_order.sql           # Этап 6: dds.sales_order
│   ├── 06_purchase_order.sql        # Этап 7: dds.purchase_order
│   └── 99_validate_dds.sql          # Этап 9: Валидация
├── scripts\
│   └── run_dds_load.py              # Управляющий Python-скрипт
├── run_dds_load.ps1                 # PowerShell запуск
└── watch_dds_progress.ps1           # Мониторинг в реальном времени
```

---

## 3. Таблицы мониторинга

### etl.pipeline_run (Паспорт запуска)

| Поле | Тип | Описание |
|------|-----|----------|
| run_id | BIGSERIAL | ID запуска |
| pipeline_name | TEXT | Имя пайплайна |
| status | TEXT | PENDING/RUNNING/DONE/FAILED |
| started_at | TIMESTAMP | Время начала |
| completed_at | TIMESTAMP | Время завершения |
| current_stage_no | INTEGER | Текущий этап |
| total_stages | INTEGER | Всего этапов |
| completed_stages | INTEGER | Завершено этапов |
| total_source_rows | BIGINT | Всего строк источника |
| total_processed_rows | BIGINT | Обработано строк |
| total_progress_pct | NUMERIC | Общий прогресс % |
| estimated_finish_at | TIMESTAMP | Прогноз завершения |

### etl.stage_progress (Прогресс этапов)

| Поле | Тип | Описание |
|------|-----|----------|
| progress_id | BIGSERIAL | ID записи |
| run_id | BIGINT | ID запуска |
| stage_no | INTEGER | Номер этапа |
| stage_name | TEXT | Имя этапа |
| source_table | TEXT | Таблица-источник |
| target_table | TEXT | Целевая таблица |
| status | TEXT | PENDING/RUNNING/DONE/FAILED |
| source_rows | BIGINT | Строк в источнике |
| processed_rows | BIGINT | Обработано строк |
| remaining_rows | BIGINT | Осталось строк |
| progress_pct | NUMERIC | Прогресс % |
| rows_per_second | NUMERIC | Скорость загрузки |
| eta_seconds | NUMERIC | Оставшееся время (сек) |
| error_message | TEXT | Текст ошибки |

### etl.stage_history (История)

Для прогнозирования ETA будущих запусков на основе истории.

---

## 4. Этапы загрузки

| № | Этап | Источник | Назначение |
|---|------|----------|------------|
| 1 | Очистка DDS | — | dds.* таблицы |
| 2 | Загрузка serial_mark | raw_ax.alk_markserial | dds.serial_mark |
| 3 | Загрузка picking_route | raw_ax.wmspickingroute | dds.picking_route |
| 4 | Загрузка pack_task | raw_ax.lfl_scspacktask | dds.pack_task |
| 5 | Загрузка order_trans | raw_ax.wmsordertrans | dds.order_trans |
| 6 | Загрузка sales_order | raw_ax.salestable | dds.sales_order |
| 7 | Загрузка purchase_order | raw_ax.purchtable | dds.purchase_order |
| 8 | Контроль количества строк | DDS | DDS |
| 9 | Финальная валидация | DDS | DDS |

---

## 5. Команды запуска

### PowerShell

```powershell
# Preflight проверка
.\run_dds_load.ps1 -Mode preflight

# Полная загрузка (очищает все таблицы)
.\run_dds_load.ps1 -Mode full

# Продолжение после ошибки
.\run_dds_load.ps1 -Mode resume

# Повторный запуск конкретного этапа
.\run_dds_load.ps1 -Mode restart_stage -Stage serial_mark

# Валидация (только проверка)
.\run_dds_load.ps1 -Mode validate_only

# Мониторинг в реальном времени (в отдельном окне)
.\watch_dds_progress.ps1
```

### Python напрямую

```bash
python scripts/run_dds_load.py --mode full
python scripts/run_dds_load.py --mode resume
python scripts/run_dds_load.py --mode restart_stage --stage order_trans
python scripts/run_dds_load.py --mode validate_only
python scripts/run_dds_load.py --mode preflight
```

---

## 6. Режимы работы

| Режим | Описание |
|-------|----------|
| `full` | Очищает все DDS таблицы, загружает заново |
| `resume` | Продолжает с последнего незавершённого этапа |
| `restart_stage` | Очищает и перезапускает конкретный этап |
| `validate_only` | Только проверяет количество строк |
| `preflight` | Предварительная проверка перед запуском |

---

## 7. Пример вывода

```
======================================================================
DDS LOAD | run_id=27
Этап 5/9: dds.order_trans
Источник: raw_ax.wmsordertrans
Начало: 22.07.2026 11:40:15 MSK
======================================================================

Обработано:       12 500 000 / 45 750 473
Прогресс:         27.32%
Скорость:         8 420 строк/с
Средняя скорость: 7 980 строк/с
Прошло:           00:26:06
Осталось:         01:05:49
Завершение:       22.07.2026 13:12:10 MSK
Последний batch:  500 000 строк за 58.7 сек
Статус:           RUNNING
```

---

## 8. Формулы расчёта

### Процент этапа
```
progress_pct = processed_rows / source_rows × 100
```

### Средняя скорость
```
rows_per_second = processed_rows / elapsed_seconds
```

### Оставшееся время
```
eta_seconds = remaining_rows / rows_per_second
```

### Прогноз завершения
```
estimated_finish_at = текущее время + eta_seconds
```

---

## 9. Требования к транзакциям

- Каждый batch фиксируется отдельно (`conn.commit()`)
- При ошибке: `conn.rollback()`
- Ошибка одного этапа не скрывает статус предыдущих
- Каждый этап имеет свой статус: PENDING → RUNNING → DONE/FAILED

---

## 10. Требования к повторному запуску

- **full**: очищает все целевые таблицы
- **resume**: продолжает с последнего batch
- **restart_stage**: очищает только выбранную таблицу
- **validate_only**: ничего не загружает, только проверяет

---

## 11. Мониторинг в реальном времени

Запуск в отдельном окне PowerShell:

```powershell
.\watch_dds_progress.ps1
```

Обновляет экран каждые 10 секунд.

---

## 12. Валидация

После завершения проверяет:

| Таблица | Источник | DDS | Разница | Статус |
|---------|----------|-----|---------|--------|
| serial_mark | 151 817 640 | 151 817 640 | 0 | OK |
| picking_route | 1 200 000 | 1 200 000 | 0 | OK |
| pack_task | 7 622 335 | 7 622 335 | 0 | OK |
| order_trans | 45 750 473 | 45 750 473 | 0 | OK |
| sales_order | 3 600 000 | 3 600 000 | 0 | OK |
| purchase_order | 258 000 | 258 000 | 0 | OK |
