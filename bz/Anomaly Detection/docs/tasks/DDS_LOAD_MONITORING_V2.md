# DDS Load Monitoring System v2

**Status**: READY
**Date**: 2026-07-22
**Task**: T32/T33 - DDS/Mart update with batch processing and resume

---

## 1. Описание

Система пакетной загрузки DDS таблиц с отслеживанием прогресса, поддержкой resume и обработкой Ctrl+C.

### Проблема v1

- Загрузка выполняется одним большим `INSERT ... SELECT`
- При остановке транзакция откатывается
- Невозможно видеть промежуточный прогресс
- Невозможно продолжить после ошибки

### Решение v2

- Пакетная загрузка порциями по `recid`
- Фиксация каждого пакета отдельно
- Продолжение с последнего завершённого пакета
- Корректная обработка Ctrl+C

---

## 2. Структура файлов

```
D:\py_pro\jira_focus_exporter\bz\Anomaly Detection\
├── sql\postgres\dds_load\
│   ├── 00_prepare_monitoring_v2.sql   # Таблицы мониторинга v2
│   ├── 01_serial_mark.sql             # Этап 2: dds.serial_mark
│   ├── 02_picking_route.sql           # Этап 3: dds.picking_route
│   ├── 03_pack_task.sql               # Этап 4: dds.pack_task
│   ├── 04_order_trans.sql             # Этап 5: dds.order_trans
│   ├── 05_sales_order.sql             # Этап 6: dds.sales_order
│   ├── 06_purchase_order.sql          # Этап 7: dds.purchase_order
│   └── 99_validate_dds.sql            # Этап 9: Валидация
├── scripts\
│   ├── run_dds_load.py                # v1 (без пакетов)
│   └── run_dds_load_v2.py             # v2 (с пакетами)
├── run_dds_load_v2.ps1                # PowerShell запуск v2
└── watch_dds_progress_v2.ps1          # Компактный мониторинг v2
```

---

## 3. Таблицы мониторинга v2

### etl.pipeline_run (Паспорт запуска)

| Поле | Тип | Описание |
|------|-----|----------|
| run_id | BIGSERIAL | ID запуска |
| pipeline_name | TEXT | Имя пайплайна |
| status | TEXT | PENDING/RUNNING/DONE/FAILED/CANCELLED |
| started_at | TIMESTAMPTZ | Время начала |
| completed_at | TIMESTAMPTZ | Время завершения |
| current_stage_no | INTEGER | Текущий этап |
| total_stages | INTEGER | Всего этапов |
| completed_stages | INTEGER | Завершено этапов |
| total_source_rows | BIGINT | Всего строк источника |
| total_processed_rows | BIGINT | Обработано строк |
| total_progress_pct | NUMERIC | Общий прогресс % |
| estimated_finish_at | TIMESTAMPTZ | Прогноз завершения |

### etl.stage_progress (Прогресс этапов)

| Поле | Тип | Описание |
|------|-----|----------|
| stage_no | INTEGER | Номер этапа |
| status | TEXT | PENDING/PREPARING/RUNNING/DONE/FAILED |
| source_rows | BIGINT | Строк в источнике |
| processed_rows | BIGINT | Обработано строк |
| progress_pct | NUMERIC | Прогресс % |
| rows_per_second | NUMERIC | Скорость загрузки |
| eta_seconds | NUMERIC | Оставшееся время (сек) |
| last_completed_batch | INTEGER | Последний пакет |
| last_completed_recid | BIGINT | Последний RECID |
| total_batches | INTEGER | Всего пакетов |
| completed_batches | INTEGER | Завершено пакетов |
| batch_size | INTEGER | Размер пакета |
| heartbeat_at | TIMESTAMPTZ | Время heartbeat |

### etl.stage_batch (Состояние пакетов)

| Поле | Тип | Описание |
|------|-----|----------|
| batch_no | INTEGER | Номер пакета |
| start_recid | BIGINT | Начальный RECID |
| end_recid | BIGINT | Конечный RECID |
| status | TEXT | PENDING/RUNNING/DONE/FAILED/CANCELLED |
| attempt_no | INTEGER | Номер попытки |
| rows_inserted | BIGINT | Вставлено строк |
| rows_conflicted | BIGINT | Конфликты |
| duration_seconds | NUMERIC | Длительность |
| rows_per_second | NUMERIC | Скорость |
| error_message | TEXT | Текст ошибки |

### etl.stage_history (История)

Для прогнозирования ETA будущих запусков.

---

## 4. Этапы загрузки

| № | Этап | Источник | Назначение | Пакетная |
|---|------|----------|------------|----------|
| 1 | Очистка DDS | — | dds.* | Нет |
| 2 | Загрузка serial_mark | raw_ax.alk_markserial | dds.serial_mark | **Да** |
| 3 | Загрузка picking_route | raw_ax.wmspickingroute | dds.picking_route | Нет |
| 4 | Загрузка pack_task | raw_ax.lfl_scspacktask | dds.pack_task | Нет |
| 5 | Загрузка order_trans | raw_ax.wmsordertrans | dds.order_trans | Нет |
| 6 | Загрузка sales_order | raw_ax.salestable | dds.sales_order | Нет |
| 7 | Загрузка purchase_order | raw_ax.purchtable | dds.purchase_order | Нет |
| 8 | Контроль количества | DDS | DDS | Нет |
| 9 | Финальная валидация | DDS | DDS | Нет |

---

## 5. Команды запуска

### Preflight проверка

```powershell
.\run_dds_load_v2.ps1 -Mode preflight
```

### Полная загрузка

```powershell
.\run_dds_load_v2.ps1 -Mode full -BatchSize 500000
```

### Resume (продолжение)

```powershell
.\run_dds_load_v2.ps1 -Mode resume
```

### Restart конкретного этапа

```powershell
.\run_dds_load_v2.ps1 -Mode restart_stage -Stage serial_mark -BatchSize 100000
```

### Валидация

```powershell
.\run_dds_load_v2.ps1 -Mode validate_only
```

### Мониторинг (в отдельном окне)

```powershell
.\watch_dds_progress_v2.ps1
```

---

## 6. Режимы работы

| Режим | Описание |
|-------|----------|
| `full` | Очищает все DDS таблицы, загружает заново |
| `resume` | Продолжает с последнего завершённого пакета |
| `restart_stage` | Очищает и перезапускает конкретный этап |
| `validate_only` | Только проверяет количество строк |
| `preflight` | Предварительная проверка перед запуском |

---

## 7. Параметры командной строки

| Параметр | По умолчанию | Описание |
|----------|--------------|----------|
| `--mode` | `full` | Режим работы |
| `--stage` | — | Имя этапа для restart_stage |
| `--batch-size` | `500000` | Размер пакета |
| `--timezone` | `Europe/Moscow` | Часовой пояс |
| `--max-attempts` | `3` | Макс. попыток на пакет |
| `--progress-width` | `30` | Ширина прогресс-бара |
| `--ascii-progress` | `False` | ASCII прогресс-бар |
| `--count-mode` | `exact` | Режим подсчёта строк |

---

## 8. Пример вывода

```
======================================================================
DDS LOAD PIPELINE v2 | Mode: full
Start: 22.07.2026 18:40:12 MSK
Log: logs/dds_load_7_20260722_184012.log
======================================================================

Pipeline: DDS_POPULATE
Run ID: 7

[Этап 2/9] Загрузка serial_mark...
  Источник: raw_ax.alk_markserial (151,817,640 строк)
  Пакет 1/304: +498,714 строк (0.3%) 00:01:12
  Пакет 2/304: +500,000 строк (0.7%) 00:01:08
  ...

======================================================================
Pipeline: DDS_POPULATE
Run ID: 7
Статус: DONE
Этапы: 9/9
Завершение: 22.07.2026 22:34:02 MSK
======================================================================
```

---

## 9. Формулы расчёта

### Процент этапа

```
progress_pct = processed_rows / source_rows × 100
```

### Скорость (скользящая)

```
eta_speed = median(last 5 batch speeds)
```

### Оставшееся время

```
eta_seconds = remaining_rows / eta_speed
```

---

## 10. Поведение при Ctrl+C

1. Текущая транзакция пакета откатывается
2. Пакет получает статус `CANCELLED`
3. Этап получает статус `CANCELLED`
4. Pipeline получает статус `CANCELLED`
5. Все предыдущие пакеты `DONE` остаются в БД
6. Выводится сообщение:

```
Загрузка остановлена пользователем.
Завершённые пакеты сохранены.
Для продолжения используйте: --mode resume
```

---

## 11. Resume логика

1. Найти последний запуск со статусом `FAILED`/`CANCELLED`
2. Найти последний этап без статуса `DONE`
3. Найти последний пакет этапа со статусом `DONE`
4. Получить `last_completed_recid`
5. Продолжить со следующего диапазона
6. Не очищать целевую таблицу
7. Не повторять завершённые пакеты

---

## 12. Retry логика

| Параметр | Значение |
|----------|----------|
| Макс. попыток | 3 |
| Backoff | 5с, 15с, 30с |

Ретраи для:
- Разрыва соединения
- Timeout
- Serialization failure
- Deadlock
- Временной сетевой ошибки

Не повторять:
- Нарушение схемы
- Отсутствующую колонку
- Ошибку типов
- Синтаксическую ошибку SQL

---

## 13. Heartbeat

Во время длительного выполнения обновлять `heartbeat_at` не реже 10 секунд.

Поля:
- `etl.pipeline_run.updated_at`
- `etl.stage_progress.heartbeat_at`
- `etl.stage_batch.updated_at`

---

## 14. Логирование

Файлы: `logs/dds_load_<run_id>_<timestamp>.log`

Записывается:
- Запуск pipeline
- Параметры
- Начало этапа
- Начало/завершение пакета
- Число строк
- Скорость
- Retry
- Ошибки
- Ctrl+C
- Итоговая валидация

Пароли в лог не записываются.

---

## 15. Тест Ctrl+C

```powershell
# Запустить с маленьким пакетом
.\run_dds_load_v2.ps1 -Mode restart_stage -Stage serial_mark -BatchSize 100000

# После 5 пакетов нажать Ctrl+C

# Проверить состояние
python -c "import psycopg2; conn=psycopg2.connect(host='localhost',port=5432,db='wms_analysis',user='postgres',password='123'); cur=conn.cursor(); cur.execute('SELECT batch_no, status, rows_inserted FROM etl.stage_batch ORDER BY batch_no'); [print(r) for r in cur.fetchall()]; conn.close()"

# Ожидаемый вывод:
# (1, 'DONE', 100000)
# (2, 'DONE', 100000)
# (3, 'DONE', 100000)
# (4, 'DONE', 100000)
# (5, 'DONE', 100000)
# (6, 'CANCELLED', 0)

# Resume
.\run_dds_load_v2.ps1 -Mode resume

# Должен начать с пакета 6
```

---

## 16. Критерии приёмки

1. `serial_mark` загружается пакетами
2. После каждого пакета видны новые строки в другом соединении
3. После `Ctrl+C` завершённые пакеты остаются в таблице
4. Повторный `resume` продолжает со следующего пакета
5. Повторно завершённые диапазоны не загружаются
6. Этап появляется в мониторинге до выполнения `COUNT(*)`
7. Pipeline не остаётся в `RUNNING` после ошибки
8. Показан прогресс текущего этапа
9. Показан общий прогресс pipeline
10. Показаны скорость, ETA и heartbeat
11. Мониторинг не выполняет тяжёлые `COUNT(*)`
12. Прогресс-бар корректно работает в Windows PowerShell
13. `restart_stage` очищает только выбранную таблицу
14. `validate_only` ничего не изменяет
15. Все действия записываются в лог
