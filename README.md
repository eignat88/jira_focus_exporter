# Jira Focus Exporter

Сервис-интегратор для выгрузки задач Jira в CSV и Excel. Скрипт поддерживает несколько режимов: Focus List, полная выгрузка незавершённых назначенных задач, дневная активность WMS-группы и диагностика одной задачи.

## Режимы работы

```powershell
python main.py --mode focus
python main.py --mode assigned
python main.py --mode wms-activity
python main.py --mode project-users
python main.py --mode project-users --project DEVAX12
python main.py --mode devax12-actual --days 1 --stale-days 7
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

По умолчанию режим читает состав группы через Jira REST API (`/rest/api/2/group/member`). В некоторых Jira этот метод доступен только администраторам. Если при запуске появляется `HTTP status: 403` и сообщение о правах администратора, заполните `JIRA_WMS_MEMBER_IDENTITIES` — тогда скрипт не будет обращаться к API группы, а отфильтрует активность по указанным идентификаторам авторов.


### `project-users`

Выгружает пользователей, включённых в проект Jira через проектные роли. По умолчанию используется проект `DEVAX12`; его можно изменить переменной `JIRA_PROJECT_USERS_PROJECT_KEY` или параметром запуска `--project`.

Скрипт сначала пытается прочитать роли проекта через `/rest/api/2/project/{projectKey}/role`, получает участников каждой роли и раскрывает группы через `/rest/api/2/group/member`. В итоговых CSV/XLSX есть идентификаторы пользователя, признак активности, роли проекта и группы-источники. Если Jira не даёт прочитать участников группы, выгрузка останавливается с пояснением, потому что без раскрытия групп список пользователей проекта будет неполным.

Если Jira возвращает `401`/`403` на чтение ролей проекта с сообщением о невозможности редактировать конфигурацию проекта, режим `project-users` автоматически переходит в упрощённую выгрузку: берёт назначаемых пользователей проекта через `/rest/api/2/user/assignable/search` и дополнительно собирает исполнителей, репортёров и создателей из задач проекта. В такой выгрузке роли будут помечены как `Assignable user`, `Issue assignee`, `Issue reporter` или `Issue creator`, а реальные проектные роли и группы будут недоступны.

Пример запуска для проекта DEVAX12:

```powershell
python main.py --mode project-users
```

### `devax12-actual`

Формирует Excel-отчёт актуальных задач группы DEVAX12 на основе Excel-файла пользователей, открытых задач на участниках группы, недавно обновлённых задач и зависших задач. Режим сопоставляет пользователей по `account_id`, `name`, `key`, `display_name` и `email`, анализирует исполнителя, автора, комментарии и changelog, рассчитывает `actual_score` и категории `active_now`, `changed_recently`, `needs_attention`, `stale`, `overdue`, `backlog_actual`.

Пример запуска:

```powershell
python main.py --mode devax12-actual --days 1 --stale-days 7
```

Отчёт сохраняется в `JIRA_REPORT_OUTPUT_DIR` с именем вида `devax12_actual_tasks_YYYY-MM-DD_HHMM.xlsx` и содержит листы `Summary`, `Actual Tasks`, `Active Now`, `Changed Recently`, `Needs Attention`, `Stale`, `Overdue`, `Events`, `Raw Issues`.

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

# Режим по умолчанию: focus, assigned, wms-activity или project-users
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
# Если нет прав администратора Jira на чтение группы, заполните список вручную.
# Подходят логины, displayName, email, key или accountId авторов changelog/comment.
JIRA_WMS_MEMBER_IDENTITIES=
JIRA_WMS_ACTIVITY_FROM=09:00
JIRA_WMS_ACTIVITY_TO=17:50
JIRA_WMS_ACTIVITY_EXTRA_JQL=

# Проект для выгрузки пользователей в режиме project-users
JIRA_PROJECT_USERS_PROJECT_KEY=DEVAX12

# Актуальные задачи DEVAX12
JIRA_DEV_GROUP_NAME=DEVAX12
JIRA_DEV_GROUP_USERS_FILE=data/jira_project_devax12_users_2026-06-11_135952.xlsx
JIRA_ACTUAL_TASKS_DAYS=1
JIRA_STALE_DAYS=7
JIRA_REPORT_OUTPUT_DIR=reports
JIRA_ACTUAL_TASKS_REPORT_FORMAT=xlsx
JIRA_DEV_GROUP_EXCLUDE_INACTIVE=true
JIRA_DEV_GROUP_REQUIRE_ASSIGNABLE=false
JIRA_DEV_GROUP_EXCLUDE_PATTERNS=$DUPLICATE,1C-,4000-,JIRAUSER
JIRA_MAX_ISSUES_PER_QUERY=1000
```

Для `wms-activity` можно вручную задать участников WMS, если ваш Jira-токен не имеет прав на чтение состава группы:

```env
JIRA_WMS_MEMBER_IDENTITIES=ignatchenko,Evgeniy Ignatchenko,ivanov@example.com
```

Значения сравниваются без учёта регистра с полями автора из Jira: `accountId`, `name`, `key`, `emailAddress`, `displayName`. Если `JIRA_WMS_MEMBER_IDENTITIES` заполнен, `JIRA_WMS_GROUP_NAME` остаётся только справочным значением в логах, а запрос `/rest/api/2/group/member` не выполняется.

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
python main.py --mode project-users
python main.py --mode devax12-actual --days 1 --stale-days 7
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
