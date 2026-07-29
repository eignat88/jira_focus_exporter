# Обнаружение аномалий в сканировании марок

Система выявления подозрительных паттернов сканирования для предотвращения мошенничества и контроля качества работы сотрудников.

**Обновлено:** 2026-07-07

---

## Статус проекта

| Метрика | Значение |
|---------|----------|
| Готовность | 96% |
| ETL модулей | 80 |
| Строк кода (ETL) | 17,048 |
| Тестов | 68 |
| Текущий этап | Загрузка ALK_MARKSERIAL (70%) |

**Подробнее:** [STATUS.md](STATUS.md)

---

## Типы аномалий

| # | Аномалия | Описание | Опасность |
|---|----------|----------|-----------|
| 1 | Слишком быстрое сканирование | Интервал < 2 сек | Мошенничество |
| 2 | Много ошибок подряд | > 5 ошибок подряд | Нарушение процесса |
| 3 | Увеличение конкурентных КМ | Рост > 30% за период | Проблемы с поставщиком |
| 4 | Отсутствие сканирований | Нет активности в рабочее время | Простой |
| 5 | Несоответствие SSCC | SSCC не найден в базе | Подделка |

---

## Быстрый старт

### Загрузка данных

```cmd
# V2 (оригинал с рефакторингом)
python -m ax_to_postgres_etl.main --use-v2 --table ALK_MARKSERIAL --mode resume

# V2T (P0-P15 enhancements)
python -m ax_to_postgres_etl.main --use-v2t --table ALK_MARKSERIAL --mode resume

# Полная загрузка
python -m ax_to_postgres_etl.main --use-v2 --table INVENTTABLE --mode reload
```

### Мониторинг

```cmd
# SQL-мониторинг
psql -h localhost -U postgres -d wms_analysis -f ax_to_postgres_etl/etl_monitoring/etl_monitor_fast.sql

# Авто-экспорт CSV (ежечасно)
powershell -ExecutionPolicy Bypass -File export_completed_chunks.ps1
```

### Jupyter ноутбуки

```cmd
cd notebooks
jupyter notebook 01_eda.ipynb
```

---

## Структура проекта

```
Anomaly Detection/
├── STATUS.md                          # Единый статус проекта
├── ROADMAP.md                         # Дорожная карта (Mermaid Gantt)
├── gantt.md                           # Диаграмма Ганта
├── README.md                          # Этот файл
│
├── ax_to_postgres_etl/               # ETL-пайплайн
│   ├── core/                          # 41 модуль (7,106 строк)
│   ├── loader/                        # V2 (663 строки)
│   ├── loader_v2t/                    # V2T + P0-P15 (1,649 строк)
│   ├── etl_monitoring/                # SQL-мониторинг
│   ├── diagnostics/                   # Диагностика RAW → DDS
│   ├── main.py                        # Точка входа
│   ├── application.py                 # Оркестратор
│   └── config.yaml                    # Конфигурация
│
├── sql/                               # SQL-скрипты
│   ├── postgres/                      # PostgreSQL DDL + DML
│   └── migrations/                    # Миграции данных
│
├── config/                            # Конфигурации ETL
├── tests/                             # Тесты (68 шт.)
├── notebooks/                         # Jupyter ноутбуки
├── monitoring/                        # Мониторинг WAL и ETL
├── diagrams/                          # Диаграммы (Mermaid)
├── generated_sql/                     # SQL-генераторы
├── data/                              # Данные (raw, processed)
├── logs/                              # Логи ETL
├── docs/                              # Документация
│   └── tasks/                         # Документация задач
│
├── archive/                           # Архив (старые файлы)
│   ├── old_root_files/                # Шумовые файлы
│   ├── old_scripts/                   # Старые скрипты
│   ├── old_reports/                   # Старые отчёты
│   ├── old_docs/                      # Старая документация
│   ├── old_analysis/                  # Старые аналитические работы
│   ├── old_generated_sql/             # Старые SQL-скрипты
│   └── old_logs/                      # Старые логи
│
├── export_completed_chunks.ps1        # Авто-экспорт CSV
├── run_dds_load_v2.ps1               # Запуск V2
├── run_dds_load_v3.ps1               # Запуск V3
├── run_full_load.py                   # Полная загрузка
├── run_raw_to_dds.ps1                # Запуск RAW → DDS
├── run_staging_normalization.ps1      # Нормализация staging
└── watch_dds_progress_v2.ps1         # Мониторинг прогресса
```

---

## ETL Pipeline

### Компоненты

| Компонент | Описание | Статус |
|-----------|----------|--------|
| V2 Loader | Оригинал с рефакторингом | ✅ |
| V2T Loader | P0-P15 enhancements | ✅ |
| ChunkManager | Управление чанками | ✅ |
| RunManager | Управление запусками | ✅ |
| RetryPolicy | Retry с jitter | ✅ |
| SQL Monitoring | 4 скрипта мониторинга | ✅ |
| Auto-export CSV | PowerShell + Планировщик | ✅ |
| Jupyter Notebooks | 5 ноутбуков аналитики | ✅ |

### Текущее состояние ETL

| Таблица | Источник (SS) | Цель (PG) | % | Статус |
|---------|---------------|-----------|---|--------|
| INVENTTABLE | 1,047,184 | 634,248 | 60.6% | ⚠️ Требует reload |
| ALK_MARKSERIAL | 151,817,640 | ~107M | ~70% | ⏳ Загружается |
| LFL_SCSPACKTASK | 7,622,335 | 7,622,335 | 100% | ✅ Загружена |
| WMSORDERTRANS | 45,750,473 | ~37M | ~81% | ⏳ Частично |
| SALESTABLE | 3,600,000 | 3,653,125 | 101% | ✅ Загружена |
| PURCHTABLE | 258,000 | 257,816 | 100% | ✅ Загружена |

---

## Модели ML

| Модель | Назначение | Статус |
|--------|------------|--------|
| Isolation Forest | Точечные аномалии | ✅ Обучена |
| LOF | Локальные кластеры | ✅ Обучена |
| Autoencoder | Сложные паттерны | ✅ Обучена |
| LSTM Autoencoder | Временные зависимости | 📋 Ожидает |

---

## Установка

```cmd
pip install -r requirements.txt
```

---

## Связанные файлы

| Файл | Описание |
|------|----------|
| [STATUS.md](STATUS.md) | Единый статус проекта |
| [ROADMAP.md](ROADMAP.md) | Дорожная карта |
| [gantt.md](gantt.md) | Диаграмма Ганта |
| [AUTO_EXPORT_README.md](AUTO_EXPORT_README.md) | Инструкция авто-экспорта |
| [PLAN_PARALLEL_V2_STABILIZATION.md](PLAN_PARALLEL_V2_STABILIZATION.md) | План стабилизации |
