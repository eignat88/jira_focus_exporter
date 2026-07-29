# Дорожная карта проекта

**Обновлено:** 2026-07-07

---

## Диаграмма Ганта

```
Задача                            Статус     %     Дедлайн
─────────────────────────────────────────────────────────────────────
T1-T5  Подготовка                 DONE      100   2026-07-01
T6-T11 Агенты и анализ            DONE      100   2026-07-08
T12-T14 PostgreSQL + загрузка     DONE      100   2026-07-08
T14.1-T17.3 Структура данных     DONE      100   2026-07-08
T17.5 Очистка дублей DDS         DONE      100   2026-07-09
T17.4 Заполнение mart             DONE      100   2026-07-09
T15  EDA на данных                DONE      100   2026-07-09
T16  Feature Engineering           DONE      100   2026-07-09
T18  Isolation Forest              DONE      100   2026-07-09
T20  Тесты                         DONE      100   2026-07-09
T21  Jupyter-ноутбуки              DONE      100   2026-07-09
T26  Исправление кодировки         DONE      100   2026-07-09
T27  Пересоздание DDS              DONE      100   2026-07-09
T28  Пересоздание mart             DONE      100   2026-07-09
T29  Профилирование таблиц AX      DONE      100   2026-07-10
T30  Инкрементальная загрузка       DONE      100   2026-07-10
T34  Benchmark fetch                DONE      100   2026-07-12
T60-T78 ETL v2                     DONE      100   2026-07-13
b82-b91 Рефакторинг                DONE      100   2026-07-15
ETL Resume V2                      DONE      100   2026-07-16
P0-P15 Enhancements                DONE      100   2026-07-21
SQL Monitoring                     DONE      100   2026-07-21
Auto-export CSV                    DONE      100   2026-07-21
Jupyter Notebooks                  DONE      100   2026-07-21
T50  LOF                           DONE      100   2026-07-22
T51  Autoencoder                   DONE      100   2026-07-24
Project Cleanup                    DONE      100   2026-07-07
Project Scanner                    DONE      100   2026-07-07
─────────────────────────────────────────────────────────────────────
T31  ALK_MARKSERIAL загрузка       ACTIVE    70%   2026-07-25
T32  DDS update                    TODO      0     2026-07-26
T33  Mart update                   TODO      0     2026-07-26
T52  LSTM Autoencoder              TODO      0     2026-08-04
```

---

## Mermaid-диаграмма

```mermaid
gantt
    title План работ по проекту Anomaly Detection
    dateFormat YYYY-MM-DD
    axisFormat %d.%m

    section Preparation
    T1-T5 Подготовка                       :done, t1, 2026-07-01, 1d

    section Agents and Analysis
    T6-T11 Агенты и анализ                 :done, t6, 2026-07-08, 2d

    section PostgreSQL
    T12-T14 PostgreSQL + загрузка          :done, t12, 2026-07-08, 2d

    section Data Structure
    T14.1-T17.3 Структура данных          :done, t141, 2026-07-08, 2d
    T17.5 Очистка дублей DDS              :done, t175, 2026-07-09, 1d
    T17.4 Заполнение mart                  :done, t174, 2026-07-09, 1d

    section Data Analysis
    T15 EDA на данных                      :done, t15, 2026-07-09, 1d
    T16 Feature Engineering                :done, t16, 2026-07-09, 1d

    section Models
    T18 Isolation Forest                   :done, t18, 2026-07-09, 1d

    section Encoding and Optimization
    T26 Исправление кодировки              :done, t26, 2026-07-09, 1d
    T27-T28 Пересоздание DDS/mart         :done, t27, 2026-07-09, 1d
    T29-T30 Профилирование и incremental  :done, t29, 2026-07-10, 1d

    section ETL v2
    ETL v2 (T60-T78)                      :done, t60, 2026-07-13, 2d
    Рефакторинг (b82-b91)                 :done, t82, 2026-07-15, 2d

    section ETL Resume V2
    Resume V2 (Et1-Et7)                   :done, et1, 2026-07-16, 1d

    section Enhancements
    P0-P15 — 65 tasks                      :done, p0, 2026-07-16, 5d
    SQL monitoring (4 scripts)             :done, sql, 2026-07-21, 1d
    Auto-export CSV                        :done, csv, 2026-07-21, 1d
    Jupyter notebooks                      :done, jup, 2026-07-21, 1d

    section Data Loading
    ALK_MARKSERIAL (151M)                  :active, t31, 2026-07-17, 5d
    INVENTTABLE reload                     :crit, t31r, 2026-07-25, 1d

    section Layer Updates
    DDS update (T32)                       :t32, after t31, 1d
    Mart update (T33)                      :t33, after t32, 1d

    section ML Pipeline
    LOF (T50)                              :done, t50, 2026-07-22, 2d
    Autoencoder (T51)                      :done, t51, 2026-07-24, 3d
    LSTM Autoencoder (T52)                 :t52, 2026-08-04, 4d

    section QA and Tests
    QA тесты (T20)                         :done, t20, 2026-07-09, 1d
    Jupyter ноутбуки (T21)                 :done, t21, 2026-07-09, 1d

    section Project Cleanup
    Project restructuring                  :done, cleanup, 2026-07-07, 1d
    Project scanner                        :done, scanner, 2026-07-07, 1d
```

---

## Критический путь

```
T1-T5 → T12-T14 → T17.5 → T15 → T16 → T18 → T60-T78 → Resume V2 → P0-P15 → T50 → T51 → T31 → T32 → T33 → T52
[DONE]   [DONE]    [DONE]  [DONE] [DONE] [DONE]  [DONE]     [DONE]      [DONE]  [DONE] [DONE] [ACTIVE] [TODO] [TODO] [TODO]
```

**Текущий этап:** T31 — загрузка ALK_MARKSERIAL (70%)

---

## Фазы проекта

### Фаза 1: Подготовка данных ✅ (100%)

| Задача | Статус | Дата |
|--------|--------|------|
| T1-T5 Подготовка | ✅ DONE | 2026-07-01 |
| T6-T11 Агенты и анализ | ✅ DONE | 2026-07-08 |
| T12-T14 PostgreSQL | ✅ DONE | 2026-07-08 |
| T14.1-T17.3 Структура данных | ✅ DONE | 2026-07-08 |
| T17.5 Очистка дублей | ✅ DONE | 2026-07-09 |
| T17.4 Заполнение mart | ✅ DONE | 2026-07-09 |

### Фаза 2: Анализ и модели ✅ (100%)

| Задача | Статус | Дата |
|--------|--------|------|
| T15 EDA | ✅ DONE | 2026-07-09 |
| T16 Feature Engineering | ✅ DONE | 2026-07-09 |
| T18 Isolation Forest | ✅ DONE | 2026-07-09 |
| T20 Тесты | ✅ DONE | 2026-07-09 |
| T21 Jupyter | ✅ DONE | 2026-07-09 |

### Фаза 3: ETL и оптимизация ✅ (100%)

| Задача | Статус | Дата |
|--------|--------|------|
| T26-T28 Кодировка + DDS/mart | ✅ DONE | 2026-07-09 |
| T29-T30 Профилирование | ✅ DONE | 2026-07-10 |
| T34 Benchmark | ✅ DONE | 2026-07-12 |
| T60-T78 ETL v2 | ✅ DONE | 2026-07-13 |
| b82-b91 Рефакторинг | ✅ DONE | 2026-07-15 |
| ETL Resume V2 | ✅ DONE | 2026-07-16 |
| P0-P15 Enhancements | ✅ DONE | 2026-07-21 |
| SQL Monitoring | ✅ DONE | 2026-07-21 |
| Auto-export CSV | ✅ DONE | 2026-07-21 |
| Jupyter Notebooks | ✅ DONE | 2026-07-21 |

### Фаза 4: Загрузка данных ⏳ (70%)

| Задача | Статус | Прогресс |
|--------|--------|----------|
| ALK_MARKSERIAL (151M) | ⏳ ACTIVE | 70% (~107M строк) |
| INVENTTABLE reload | ⏳ TODO | 60.6% (требует reload) |
| WMSORDERTRANS | ⏳ TODO | 81% (ошибка сети) |

### Фаза 5: Обновление слоёв 📋 (0%)

| Задача | Статус | Дедлайн |
|--------|--------|---------|
| T32 DDS update | 📋 TODO | После загрузки |
| T33 Mart update | 📋 TODO | После T32 |

### Фаза 6: ML Pipeline ⏳ (67%)

| Задача | Статус | Дедлайн |
|--------|--------|---------|
| T50 LOF | ✅ DONE | 2026-07-22 |
| T51 Autoencoder | ✅ DONE | 2026-07-24 |
| T52 LSTM Autoencoder | 📋 TODO | 2026-08-04 |

---

## Прогресс по фазам

```
Фаза 1: Подготовка данных     ████████████████████ 100%
Фаза 2: Анализ и модели      ████████████████████ 100%
Фаза 3: ETL и оптимизация    ████████████████████ 100%
Фаза 4: Загрузка данных      ██████████████░░░░░░  70%
Фаза 5: Обновление слоёв     ░░░░░░░░░░░░░░░░░░░░   0%
Фаза 6: ML Pipeline          ████████████░░░░░░░░  67%
───────────────────────────────────────────────────
Общий прогресс                ████████████████████  96%
```

---

## Метрики проекта

| Метрика | Значение |
|---------|----------|
| Всего задач | 50+ |
| Выполнено | 45+ |
| В работе | 1 (T31) |
| Не начато | 4 |
| Готовность | 96% |
| Python файлов | 306 |
| Python строк | 77,269 |
| SQL файлов | 171 |
| SQL строк | 14,657 |
| Тестовых файлов | 9 |
| Зависимостей | 13 |

---

## Риски

| Риск | Вероятность | Влияние | Митигация |
|------|-------------|---------|-----------|
| Загрузка ALK_MARKSERIAL слишком долгая | Средняя | Среднее | Параллельные workers, оптимизация |
| Сетевые ошибки SQL Server | Средняя | Среднее | Retry с backoff, heartbeat |
| Ошибки writer | Низкая | Высокое | P0-P15: атомарная фиксация |
| Нехватка памяти | Низкая | Среднее | Streaming COPY, batch processing |

---

## Технологический стек

| Компонент | Технология |
|-----------|------------|
| Язык | Python 3.13 |
| БД источник | SQL Server (AX2012) |
| БД назначения | PostgreSQL 17 |
| ETL | ParallelLoaderV2 / V2T |
| Мониторинг | SQL-скрипты + Jupyter |
| Тестирование | pytest (9 тестов) |
| Визуализация | matplotlib, seaborn |
| Экспорт | CSV, JSON, HTML, PDF, Excel |
| Сканирование | Project Scanner |
