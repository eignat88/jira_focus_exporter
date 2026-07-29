# План выполнения задач T29-T33

> Дата создания: 2026-07-10
> Автор: Project Admin Agent
> Источник: AX_TableProfile анализ

---

## Краткое описание

Данный план описывает выполнение задач T29-T33 по оптимизации ETL-пайплайна и загрузке недостающих таблиц AX2012 в PostgreSQL. Основная цель — перейти на инкрементальную загрузку и обеспечить полноту данных в raw_ax.

---

## Критический путь

```
T29 (Profile tables) → T30 (Incremental loading) → T31 (Load missing tables) → T32 (DDS update) → T33 (Mart update)
     [READY]              [NEXT]                     [FUTURE]                  [FUTURE]             [FUTURE]
     ~2 hrs               ~4 hrs                     ~8-12 hrs                 ~2 hrs               ~1 hr
```

**Общая оценка времени:** 15-20 часов

---

## Детали задач

### T29: Profile AX tables — идентификация отсутствующих/неполных таблиц

**Статус:** READY TO EXECUTE
**Приоритет:** Высокий
**Зависимости:** Нет
**Время:** ~2 часа

**Описание:**
Провести профилирование всех таблиц AX2012 и сравнить с текущим состоянием raw_ax.

**Действия:**
1. Прочитать `01_AX_TABLES.csv` (метаданные таблиц)
2. Сравнить с текущими таблицами в `raw_ax` схеме
3. Идентифицировать отсутствующие таблицы (15 таблиц)
4. Определить приоритеты загрузки на основе размера и важности
5. Создать отчёт в `docs/data/AX_TableProfile/TABLE_PROFILING_REPORT.md`

**Входные данные:**
- `docs/data/AX_TableProfile/01_AX_TABLES.csv`
- `docs/data/AX_TableProfile/06_INCREMENT_CANDIDATES.csv`
- `docs/data/AX_TableProfile/sql_profile/01_table_size.csv`

**Выходные данные:**
- `docs/data/AX_TableProfile/TABLE_PROFILING_REPORT.md`
- Список таблиц для загрузки с приоритетами

**Критерии приёмки:**
- Все таблицы AX2012 идентифицированы
- Отсутствующие таблицы помечены
- Приоритеты определены

---

### T30: Implement incremental loading для raw_ax

**Статус:** Не начато
**Приоритет:** Высокий
**Зависимости:** T29
**Время:** ~4 часа

**Описание:**
Добавить поддержку инкрементальной загрузки по `modifiedDateTime` в ETL-пайплайн.

**Действия:**
1. Обновить `ax_to_postgres_etl/loader/batch_loader.py`:
   - Добавить пагинацию по `modifiedDateTime`
   - Поддержка `WHERE modifiedDateTime > @last_modified`
   - Сохранение последнего `modifiedDateTime` в `etl_status`
2. Обновить `ax_to_postgres_etl/config.yaml`:
   - Добавить поле `incremental_field` для каждой таблицы
   - Настроить стратегию загрузки (full/incremental)
3. Обновить `ax_to_postgres_etl/connectors/postgres.py`:
   - Метод `get_last_modified()` для получения последнего modifiedDateTime
   - Метод `update_last_modified()` для обновления
4. Протестировать на малой таблице (MPProductGroup, 24 строки)

**Входные данные:**
- `ax_to_postgres_etl/loader/batch_loader.py`
- `ax_to_postgres_etl/config.yaml`
- `ax_to_postgres_etl/connectors/postgres.py`

**Выходные данные:**
- Обновлённый `batch_loader.py` с поддержкой modifiedDateTime
- Обновлённый `config.yaml` с incremental strategies
- Тесты на малой таблице

**Критерии приёмки:**
- Инкрементальная загрузка работает для тестовой таблицы
- Last modifiedDateTime сохраняется в etl_status
- Resume работает корректно

---

### T31: Load missing AX tables в raw layer

**Статус:** Не начато
**Приоритет:** Средний
**Зависимости:** T29, T30
**Время:** ~8-12 часов

**Описание:**
Загрузить 15 отсутствующих таблиц из AX2012 в raw_ax схему.

**Таблицы для загрузки (по приоритету):**

| # | Таблица | Строк | Размер | Приоритет | Стратегия |
|---|---------|-------|--------|-----------|-----------|
| 1 | ALK_MARKSERIAL | 151M | 13.8 GB | HIGH | Incremental (modifiedDateTime) |
| 2 | LFL_MARKINGCODETABLE | 78M | 9.2 GB | HIGH | Incremental (modifiedDateTime) |
| 3 | LFL_REQUESTTABLE | 11.7M | 1.9 GB | HIGH | Full reload (null bytes issue) |
| 4 | LFL_REQUESTTABLEES | 3.4M | 1.0 GB | HIGH | Incremental (modifiedDateTime) |
| 5 | ALK_MARKSERIAL_ZPL | ? | ? | MEDIUM | Full reload |
| 6-15 | Остальные 10 таблиц | <1M | <100MB | LOW | Full reload |

**Действия:**
1. Создать SQL-скрипты для каждой таблицы
2. Добавить таблицы в `config.yaml`
3. Запустить загрузку для каждой таблицы
4. Проверить количество строк после загрузки
5. Создать DDS-таблицы для новых данных

**Входные данные:**
- `docs/data/AX_TableProfile/TABLE_PROFILING_REPORT.md`
- `ax_to_postgres_etl/config.yaml`

**Выходные данные:**
- Загруженные таблицы в raw_ax
- Обновлённый `config.yaml`
- Отчёт о загрузке

**Критерии приёмки:**
- Все 15 таблиц загружены
- Количество строк соответствует ожиданиям
- Инкрементальная загрузка работает для больших таблиц

---

### T32: Load missing tables в DDS layer

**Статус:** Не начато
**Приоритет:** Средний
**Зависимости:** T31
**Время:** ~2 часа

**Описание:**
Создать DDS-таблицы для новых данных и заполнить их из raw_ax.

**Действия:**
1. Создать SQL-скрипты для DDS-таблиц:
   - `dds.marking_serial` (из ALK_MARKSERIAL)
   - `dds.request_table` (из LFL_REQUESTTABLE)
   - `dds.request_table_es` (из LFL_REQUESTTABLEES)
2. Добавить индексы на JOIN-поля
3. Заполнить DDS-таблицы из raw_ax
4. Проверить целостность данных

**Входные данные:**
- Загруженные таблицы в raw_ax
- `sql/postgres/005_*.sql` (существующие скрипты)

**Выходные данные:**
- SQL-скрипты для новых DDS-таблиц
- Заполненные DDS-таблицы
- Индексы

**Критерии приёмки:**
- DDS-таблицы созданы и заполнены
- Индексы созданы
- Данные соответствуют источникам

---

### T33: Update mart layer с новыми данными

**Статус:** Не начато
**Приоритет:** Средний
**Зависимости:** T32
**Время:** ~1 час

**Описание:**
Обновить mart-таблицы с учётом новых данных.

**Действия:**
1. Обновить `mart.daily_operations` с данными из новых DDS-таблиц
2. Обновить `mart.picking_efficiency` если есть новые данные
3. Обновить `mart.marking_statistics` с данными из ALK_MARKSERIAL
4. Пересчитать Feature Engineering (T16.1)
5. Проверить метрики

**Входные данные:**
- Заполненные DDS-таблицы
- `sql/postgres/006_populate_mart.sql`

**Выходные данные:**
- Обновлённые mart-таблицы
- Пересчитанные feature-таблицы
- Обновлённые метрики

**Критерии приёмки:**
- Mart-таблицы обновлены
- Feature-таблицы пересчитаны
- Метрики соответствуют ожиданиям

---

## Зависимости между задачами

```
T29 ──→ T30 ──→ T31 ──→ T32 ──→ T33
 │              │
 │              └──→ T19 (LOF) — параллельно
 │
 └──→ T16.1 (Feature Engineering) — параллельно
```

---

## Ресурсы

### Агенты
| Агент | Задачи | Описание |
|-------|--------|----------|
| Data Engineer | T29, T30, T31 | Профилирование, ETL, загрузка |
| Data Architect | T32 | DDS-структура |
| Data Analyst | T33 | Mart, feature engineering |
| Status Agent | Все | Мониторинг прогресса |

### Инструменты
- PostgreSQL (psql)
- Python ETL (ax_to_postgres_etl/)
- SQL-скрипты (sql/postgres/)

---

## Риски

| Риск | Вероятность | Влияние | Митигация |
|------|-------------|---------|-----------|
| ALK_MARKSERIAL слишком большая (13.8GB) | Средняя | Высокое | Инкрементальная загрузка, батчинг |
| LFL_REQUESTTABLE null bytes | Высокая | Среднее | Агрессивная очистка данных |
| Инкрементальная загрузка сложна в реализации | Средняя | Среднее | Тестирование на малых таблицах |
| DDS-таблицы не справляются с объёмом | Низкая | Высокое | Оптимизация запросов, индексы |

---

## Мониторинг

### Ежедневно
- Проверка статуса загрузки
- Мониторинг размеров таблиц
- Проверка ошибок

### Еженедельно
- Отчёт о прогрессе
- Сравнение с планом
- Обновление оценок времени

---

## Контакты

| Роль | Ответственный |
|------|---------------|
| Project Admin | Status Agent |
| Data Engineer | Data Engineer Agent |
| Data Architect | Data Architect Agent |
| Data Analyst | Data Analyst Agent |

---

**Последнее обновление:** 2026-07-10
**Версия:** 1.0
