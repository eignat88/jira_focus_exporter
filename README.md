# Jira Focus Exporter

Сервис-интегратор по расписанию для выгрузки задач Jira, назначенных на текущего пользователя и требующих внимания.

## Что выгружается

Скрипт обращается к Jira REST API и экспортирует задачи, которые:

- назначены на текущего пользователя Jira;
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
JIRA_EXPORT_DIR=exports
JIRA_LOG_DIR=logs
```

Токен Jira храните только в `.env`.

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
assignee = currentUser()
AND statusCategory != Done
AND (
    priority in (Highest, High, Critical, Blocker)
    OR due <= 7d
    OR due < now()
    OR labels in (focus, urgent, critical)
    OR updated <= -3d
)
ORDER BY priority DESC, due ASC, updated ASC
```

Если в вашей Jira нет приоритетов `Critical` или `Blocker`, измените список приоритетов в функции `build_focus_jql()` в `main.py`.
