# Jira Focus Exporter

Сервис-интегратор для выгрузки задач Jira в CSV и Excel. Скрипт поддерживает несколько режимов: Focus List, полная выгрузка незавершённых назначенных задач, дневная активность WMS-группы и диагностика одной задачи.

## Режимы работы

```powershell
python main.py --mode focus
python main.py --mode assigned
python main.py --mode wms-activity
python main.py --mode explain --issue DAX-11253
```

Если `--mode` не указан, используется `JIRA_DEFAULT_MODE` из `.env`.

### `focus`

Выгружает задачи, требующие внимания. Задача попадает в Focus List, если она назначена на выбранного пользователя, не завершена и соответствует хотя бы одному focus-условию:

- приоритет входит в `JIRA_FOCUS_PRIORITIES`;
- срок исполнения наступает в ближайшие `JIRA_DUE_SOON_DAYS` дней;
- срок исполнения просрочен;
- есть label из `JIRA_FOCUS_LABELS`;
- задача не обновлялась `JIRA_STALE_DAYS` дней;
- при включённом `JIRA_INCLUDE_EMPTY_DUE_IN_FOCUS=true` срок исполнения пустой.

Эти же настройки используются и для JQL, и для колонки `focus_reason`, поэтому причина попадания в фокус-лист соответствует фактическому фильтру.

### `assigned`

Выгружает все незавершённые задачи пользователя без дополнительных focus-условий. Этот режим нужен, чтобы не терять задачи со статусами в работе и обычным приоритетом, например `Medium`, без срока и без focus-меток.

Базовый JQL:

```sql
assignee = currentUser()
AND statusCategory not in ("Done")
ORDER BY updated DESC
```

### `wms-activity`

Формирует отдельную выгрузку активности пользователей группы WMS за текущий день. Скрипт ищет задачи, обновлённые с начала дня, получает `changelog` и `comments`, а в итоговый файл добавляет только активности, автор которых входит в группу `JIRA_WMS_GROUP_NAME` и время которых попадает в интервал `JIRA_WMS_ACTIVITY_FROM` — `JIRA_WMS_ACTIVITY_TO`.

### `explain`

Показывает диагностику одной задачи:

```powershell
python main.py --mode explain --issue DAX-11253
```

В выводе отображаются исполнитель, статус, категория статуса, приоритет, срок, labels, дата обновления, попадание в `assigned` и `focus`, а также причины решения.

## Структура

```text
jira_focus_exporter/
├─ main.py
├─ config.py
├─ jira_client.py
├─ filters.py
├─ exporters.py
├─ focus_reason.py
├─ .env.example
├─ requirements.txt
├─ run_export.ps1
├─ exports/
└─ logs/
```

## Установка на Windows

```powershell
cd D:\py_pro\jira_focus_exporter
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## Настройка

Скопируйте `.env.example` в `.env` и заполните значения:

```env
JIRA_URL=https://jira.letoile.tech
JIRA_TOKEN=your_token_here

# Исполнитель. Если пусто — использовать currentUser()
JIRA_ASSIGNEE=

# Основные директории
JIRA_EXPORT_DIR=exports
JIRA_LOG_DIR=logs

# Режим по умолчанию: focus, assigned или wms-activity
JIRA_DEFAULT_MODE=focus

# Категории статусов, которые считаются завершёнными
JIRA_EXCLUDED_STATUS_CATEGORIES=Done

# Конкретные статусы, которые нужно исключать дополнительно
JIRA_EXCLUDED_STATUSES=

# Статусы, которые нужно включать явно. Если пусто — не ограничивать.
JIRA_INCLUDED_STATUSES=

# Приоритеты и метки для Focus List
JIRA_FOCUS_PRIORITIES=High,Highest,Critical,Blocker
JIRA_FOCUS_LABELS=focus,urgent,critical

# Сроки и зависшие задачи
JIRA_DUE_SOON_DAYS=7
JIRA_STALE_DAYS=3
JIRA_INCLUDE_MEDIUM_IN_FOCUS=false
JIRA_INCLUDE_EMPTY_DUE_IN_FOCUS=false

# Ограничения выборки
JIRA_PROJECTS=
JIRA_ISSUE_TYPES=

# Дополнительные JQL-условия
JIRA_ASSIGNED_EXTRA_JQL=
JIRA_FOCUS_EXTRA_JQL=

# WMS
JIRA_WMS_GROUP_NAME=wms
JIRA_WMS_ACTIVITY_FROM=09:00
JIRA_WMS_ACTIVITY_TO=17:50
JIRA_WMS_ACTIVITY_EXTRA_JQL=
```

`JIRA_ASSIGNEE` можно оставить пустым — тогда скрипт использует `assignee = currentUser()`. Если нужно явно указать исполнителя, задайте отображаемое имя или логин, например:

```env
JIRA_ASSIGNEE=Evgeniy Ignatchenko
```

`JIRA_FOCUS_PRIORITIES` — единый список приоритетов для focus-фильтра. Он используется при построении JQL, расчёте `focus_reason` и логировании. Перед поиском скрипт получает реальные приоритеты из Jira и исключает отсутствующие значения из JQL, чтобы не получать ошибку по неизвестному приоритету.

Если нужно включать `Medium` в Focus List, можно либо добавить его в список приоритетов:

```env
JIRA_FOCUS_PRIORITIES=High,Highest,Critical,Blocker,Medium
```

либо включить флаг:

```env
JIRA_INCLUDE_MEDIUM_IN_FOCUS=true
```

## Примеры JQL

При настройках по умолчанию `focus` строит JQL вида:

```sql
assignee = currentUser()
AND statusCategory not in ("Done")
AND (
    priority in ("High", "Highest", "Critical", "Blocker")
    OR due <= 7d
    OR due < now()
    OR labels in (focus, urgent, critical)
    OR updated <= -3d
)
ORDER BY priority DESC, due ASC, updated ASC
```

Если указать:

```env
JIRA_PROJECTS=DAX,DEVAX12
JIRA_DUE_SOON_DAYS=14
JIRA_STALE_DAYS=5
JIRA_FOCUS_PRIORITIES=High,Highest,Critical,Blocker,Medium
```

то focus-JQL будет включать проекты, `due <= 14d`, `updated <= -5d` и `Medium` в списке приоритетов.

Для `assigned` при `JIRA_PROJECTS=DAX,DEVAX12` JQL будет вида:

```sql
project in ("DAX", "DEVAX12")
AND assignee = currentUser()
AND statusCategory not in ("Done")
ORDER BY updated DESC
```

## Логирование

При запуске в лог выводятся режим, итоговые настройки фильтрации и финальный JQL. Jira-токен в лог не выводится.

## Ручной запуск

```powershell
.\.venv\Scripts\activate
python main.py --mode focus
python main.py --mode assigned
```

Или через PowerShell-обёртку:

```powershell
powershell.exe -ExecutionPolicy Bypass -File "D:\py_pro\jira_focus_exporter\run_export.ps1"
```

## Запуск по расписанию

Пример регистрации задачи Windows Task Scheduler на каждый рабочий день в 09:00:

```powershell
$Action = New-ScheduledTaskAction `
  -Execute "powershell.exe" `
  -Argument "-ExecutionPolicy Bypass -File `"D:\py_pro\jira_focus_exporter\run_export.ps1`""

$Trigger = New-ScheduledTaskTrigger `
  -Weekly `
  -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
  -At 09:00

Register-ScheduledTask `
  -TaskName "Jira Focus Tasks Exporter" `
  -Action $Action `
  -Trigger $Trigger `
  -Description "Выгрузка задач Jira"
```

## Безопасность токена

- Реальный PAT должен лежать только в локальном `.env`, который исключён из Git.
- Не коммитьте токен и не вставляйте его в документацию.
- При утечке токена отзовите его в Jira и создайте новый.
