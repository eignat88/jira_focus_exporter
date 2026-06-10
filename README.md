# Jira Focus Exporter

Сервис-интегратор по расписанию для выгрузки задач Jira, назначенных на текущего пользователя и требующих внимания.

## Что выгружается

Скрипт обращается к Jira REST API и экспортирует задачи, которые:

- назначены на пользователя из `JIRA_ASSIGNEE` или, если переменная пустая, на текущего пользователя Jira;
- не находятся в statusCategory `Done`;
- имеют высокий приоритет, близкий или просроченный срок, label `focus` / `urgent` / `critical` либо давно не обновлялись.

Результат сохраняется в CSV и Excel. В выгрузку добавляется колонка `focus_reason`, чтобы сразу видеть причину попадания задачи в фокус-лист.

## Структура

```text
jira_focus_exporter/
├─ main.py
├─ .env.example
├─ requirements.txt
├─ run_export.ps1
├─ exports/
└─ logs/
```

Файлы `.env`, `exports/`, `logs/` и виртуальное окружение исключены из Git.

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
JIRA_TOKEN=replace_with_your_jira_token
JIRA_ASSIGNEE=ignatchenko
JIRA_FOCUS_PRIORITIES=High,Highest,Critical,Blocker
JIRA_EXPORT_DIR=exports
JIRA_LOG_DIR=logs
```

Токен Jira храните только в `.env`. Не добавляйте реальный токен в Git, README или `.env.example`. Если токен был отправлен в чат или сохранён в открытом виде не там, где нужно, лучше отозвать его и выпустить новый в Jira Personal Access Tokens.

`JIRA_ASSIGNEE` можно оставить пустым — тогда скрипт использует `assignee = currentUser()`. Для вашего пользователя можно указать `ignatchenko`, чтобы фильтр был явным.

`JIRA_FOCUS_PRIORITIES` — список приоритетов через запятую. Скрипт перед поиском получает реальные приоритеты из Jira и автоматически исключает отсутствующие значения, поэтому ошибка вида `Значение 'Highest' отсутствует для поля 'priority'` больше не должна останавливать выгрузку. Если фильтр по приоритетам не нужен, оставьте переменную пустой.

## Ручной запуск

```powershell
.\.venv\Scripts\activate
python main.py
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
  -Description "Выгрузка задач Jira, назначенных на меня и требующих фокуса"
```

Проверочный запуск:

```powershell
Start-ScheduledTask -TaskName "Jira Focus Tasks Exporter"
```

## JQL-фильтр

По умолчанию используется фильтр:

```sql
assignee = "ignatchenko"
AND statusCategory != Done
AND (
    priority in ("High")
    OR due <= 7d
    OR due < now()
    OR labels in (focus, urgent, critical)
    OR updated <= -3d
)
ORDER BY priority DESC, due ASC, updated ASC
```

Если `JIRA_ASSIGNEE` пустой, первая строка будет `assignee = currentUser()`. Если в вашей Jira нет `Highest`, `Critical` или `Blocker`, скрипт исключит эти значения из JQL автоматически. Настроить желаемые приоритеты можно через `JIRA_FOCUS_PRIORITIES`.

## Безопасность токена

- Реальный PAT должен лежать только в локальном `.env`, который уже исключён из Git.
- Не коммитьте токен и не вставляйте его в документацию.
- При утечке токена отзовите его в Jira и создайте новый.
