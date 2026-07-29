# Команды параллельной загрузки

> Дата: 2026-07-14
> Источник: parallel_loader.py + config.yaml

---

## 1. Включение параллельной загрузки

Отредактируйте `ax_to_postgres_etl/config.yaml`:

```yaml
etl:
  parallel:
    enabled: true  # Включить параллельную загрузку
    workers: 4      # Количество потоков
    fetch_size: 5000  # Строк на fetch
    commit_size: 50000  # Строк на commit
```

---

## 2. Запуск ETL с параллельной загрузкой

```powershell
cd /d "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection"
python ax_to_postgres_etl/main.py
```

---

## 3. Запуск параллельного загрузчика напрямую

```powershell
cd /d "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection"

python -c "
from connectors.sqlserver import SQLServerConnector
from connectors.postgres import PostgresConnector
from loader.parallel_loader import ParallelLoader
from configs.settings import get_settings

settings = get_settings()
ss = SQLServerConnector(server=settings.source.server, database=settings.source.database, driver=settings.source.driver)
ss.connect()
pg = PostgresConnector(host=settings.db.host, port=settings.db.port, database=settings.db.database, user=settings.db.user, password=settings.db.password, schema=settings.db.schema)
pg.connect()

loader = ParallelLoader(ss, pg, workers=4, fetch_size=5000, commit_size=50000)
loader.load_table('ALK_MARKSERIAL', 'SELECT * FROM ALK_MARKSERIAL ORDER BY RECID', 0)

ss.disconnect()
pg.disconnect()
"
```

---

## 4. Тест производительности (разные worker-ы)

```powershell
cd /d "D:\py_pro\jira_focus_exporter\bz\Anomaly Detection"

python -c "
from connectors.sqlserver import SQLServerConnector
from connectors.postgres import PostgresConnector
from loader.parallel_loader import ParallelLoader
from configs.settings import get_settings
import time

settings = get_settings()
ss = SQLServerConnector(server=settings.source.server, database=settings.source.database, driver=settings.source.driver)
ss.connect()
pg = PostgresConnector(host=settings.db.host, port=settings.db.port, database=settings.db.database, user=settings.db.user, password=settings.db.password, schema=settings.db.schema)
pg.connect()

# Test with different worker counts
for workers in [1, 2, 4]:
    loader = ParallelLoader(ss, pg, workers=workers, fetch_size=5000, commit_size=50000)
    start = time.time()
    loader.load_table('LFL_SCSPACKTASK', 'SELECT * FROM LFL_SCSPACKTASK ORDER BY RECID', 0)
    elapsed = time.time() - start
    print(f'Workers={workers}: {elapsed:.1f}s')

ss.disconnect()
pg.disconnect()
"
```

---

## 5. Конфигурация

```yaml
etl:
  parallel:
    enabled: true      # Включить параллельную загрузку
    workers: 4         # Количество потоков fetch
    fetch_size: 5000   # Строк на fetch (оптимально по бенчмарку)
    commit_size: 50000 # Строк на commit в PostgreSQL
```

---

## 6. Архитектура

```
Main Thread
    │
    ├── Thread 1: Fetch + Buffer → Queue
    ├── Thread 2: Fetch + Buffer → Queue
    ├── Thread 3: Fetch + Buffer → Queue
    └── Thread 4: Fetch + Buffer → Queue
            │
            ▼
    Write Thread (single PG connection)
            │
            ▼
        PostgreSQL COPY
```

---

## 7. Ожидаемый прирост

| Метрика | Однопоточная | Параллельная (4 worker) |
|---------|-------------|-------------------------|
| fetch время | 50 sec | ~15 sec |
| Общее время | 55 sec | ~20 sec |
| Скорость | 1,800 r/s | 5,000+ r/s |

---

## 8. Рекомендации

| Таблица | Рекомендуемые workers | fetch_size | commit_size |
|---------|----------------------|------------|-------------|
| ALK_MARKSERIAL (151M) | 4 | 5000 | 50000 |
| WMSORDERTRANS (45.7M) | 4 | 5000 | 50000 |
| LFL_SCSPACKTASK (7.6M) | 2 | 5000 | 20000 |
| SALESTABLE (3.6M) | 2 | 5000 | 20000 |
| PURCHTABLE (258K) | 1 | 1000 | 10000 |
