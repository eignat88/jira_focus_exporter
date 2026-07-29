# ETL: SQL Server AX2012 → PostgreSQL

## Установка

```bash
pip install -r requirements.txt
```

## Запуск

```bash
python main.py
```

## Конфигурация

Файл `config.yaml`:

```yaml
source:
  server: SWS-DB-T1
  database: AX63_WMS_TEST

target:
  host: localhost
  port: 5432
  database: wms_analysis
  schema: raw_ax
  user: postgres
  password: "123"

etl:
  batch_size: 100000
```

## Структура

```
ax_to_postgres_etl/
├── main.py              # Точка входа
├── config.yaml          # Конфигурация
├── requirements.txt     # Зависимости
├── connectors/
│   ├── sqlserver.py     # SQL Server (pyodbc)
│   └── postgres.py      # PostgreSQL (psycopg2)
├── metadata/
│   └── schema_reader.py # Чтение структуры
├── loader/
│   └── batch_loader.py  # Загрузка батчами
└── logs/                # Логи выполнения
```

## Особенности

- Постраничное чтение по RECID (без SELECT *)
- Batch загрузка через COPY
- Resume после ошибки
- Контроль загрузки (etl_status, etl_validation)
- Логирование в файл
