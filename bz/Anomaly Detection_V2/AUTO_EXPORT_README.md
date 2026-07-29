# Автоматическая выгрузка завершённых чанков

## Быстрый старт

### 1. Ручной запуск

```powershell
powershell.exe -ExecutionPolicy Bypass -File "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection\export_completed_chunks.ps1"
```

### 2. Автоматический запуск (каждый час)

1. Откройте `Win + R` → `taskschd.msc`
2. Создайте задачу:
   - **Имя:** `ETL completed chunks export`
   - **Триггеры:** каждые 1 час
   - **Действия:**
     - Программа: `powershell.exe`
     - Аргументы: `-ExecutionPolicy Bypass -File "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection\export_completed_chunks.ps1"`
     - Рабочая папка: `D:\py_pro\jira_focus_exporter\bz\Anomaly Detection`

### 3. Настройка пароля PostgreSQL

Создайте файл `C:\Users\Jacks\AppData\Roaming\postgresql\pgpass.conf`:

```
localhost:5432:wms_analysis:postgres:ВАШ_ПАРОЛЬ
```

## Структура файлов

```
sql/
└── completed_chunks_export.sql    # SQL-запрос

export_completed_chunks.ps1        # PowerShell-скрипт
AUTO_EXPORT_README.md              # Эта инструкция
logs/
└── completed_19_20260721_130000.csv  # Результаты
```

## Авто-определение run_id

Скрипт автоматически находит последний запуск для таблицы `ALK_MARKSERIAL`. Ручная подстановка `run_id` не требуется.

## Формат CSV

| Поле | Описание |
|------|----------|
| `chunk_no` | Номер чанка |
| `attempt_count` | Количество попыток |
| `rows_read` | Прочитано строк |
| `rows_inserted` | Вставлено строк |
| `rows_conflicted` | Конфликтов |
| `started_at` | Время начала |
| `completed_at` | Время завершения |
| `duration` | Длительность |
| `rows_per_second` | Скорость |
