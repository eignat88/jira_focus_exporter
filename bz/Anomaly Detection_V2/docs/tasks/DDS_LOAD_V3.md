# DDS Load Pipeline v3

**Date**: 2026-07-22
**Status**: READY
**Fixes**: b122.txt (19 issues)

---

## 1. Исправления

| # | Проблема | Решение |
|---|----------|---------|
| 1 | Параллельные запуски | Advisory lock `pg_try_advisory_lock` |
| 2 | Pipeline остается RUNNING | Обработка `QueryCanceled` + `KeyboardInterrupt` |
| 3 | Множественные COUNT(*) | Один COUNT на этап, кэширование |
| 4 | Старый монолитный процесс | Проверка `pg_stat_activity` |
| 5 | Кодировка PowerShell | `chcp 65001` + UTF-8 |
| 6 | Heartbeat во время COUNT | Отдельный поток `HeartbeatWorker` |
| 7 | Идемпотентность | `ON CONFLICT DO NOTHING` |
| 8 | Отдельные соединения | `data_conn`, `monitor_conn`, `lock_conn` |
| 9 | preflight блокировка | RuntimeError при активных запусках |
| 10 | Потерянные запуски | Авто-INTERRUPTED по heartbeat |
| 11 | Статус COUNTING_SOURCE | Новый статус для подсчета |
| 12 | Resume логика | Продолжение с последнего batch |
| 13 | Exit codes | 0=OK, 1=FAILED, 2=BLOCKED, 130=CANCELLED |

---

## 2. Файлы

| Файл | Описание |
|------|----------|
| `scripts/run_dds_load_v3.py` | Основной скрипт |
| `run_dds_load_v3.ps1` | PowerShell запуск |
| `watch_dds_progress_v2.ps1` | Мониторинг |

---

## 3. Команды

```powershell
# Preflight
.\run_dds_load_v3.ps1 -Mode preflight

# Full load
.\run_dds_load_v3.ps1 -Mode full -BatchSize 500000

# Resume
.\run_dds_load_v3.ps1 -Mode resume

# Restart stage
.\run_dds_load_v3.ps1 -Mode restart_stage -Stage serial_mark -BatchSize 100000

# With cached count
.\run_dds_load_v3.ps1 -Mode restart_stage -Stage serial_mark -CountMode cached

# ASCII progress
.\run_dds_load_v3.ps1 -Mode resume -AsciiProgress
```

---

## 4. Advisory Lock

```sql
-- Lock key
SELECT hashtext('DDS_POPULATE_V2')

-- Acquire
SELECT pg_try_advisory_lock(<key>)

-- Release
SELECT pg_advisory_unlock(<key>)
```

---

## 5. Статусы

```
PENDING -> PREPARING -> COUNTING_SOURCE -> RUNNING -> COMMITTING -> DONE
                                                                   -> FAILED
                                                                   -> CANCELLED
                                                                   -> INTERRUPTED
```

---

## 6. Heartbeat

Отдельный поток обновляет каждые 10 секунд:
- `etl.stage_progress.heartbeat_at`
- `etl.pipeline_run.updated_at`

Потерянным считается запуск с heartbeat старше 2 минут.

---

## 7. Тесты

| Тест | Действие | Ожидание |
|------|----------|----------|
| Параллельный | 2 запуска одновременно | Второй: exit code 2 |
| Ctrl+C во время COUNT | Остановка в COUNTING_SOURCE | CANCELLED |
| Ctrl+C во время batch | Остановка после 5 batch | 1-5 DONE, 6 CANCELLED |
| Resume | После отмены | Продолжение с batch 6 |
| Старый процесс | Запустить 011_populate, затем v3 | Блокировка v3 |

---

## 8. Exit Codes

| Code | Описание |
|------|----------|
| 0 | SUCCESS |
| 1 | FAILED |
| 2 | PREFLIGHT BLOCKED |
| 130 | CANCELLED |
