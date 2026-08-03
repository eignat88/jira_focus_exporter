# Постановка: исправление RAW → DDS для purchase_order

## Цель

Сделать этап `purchase_order` корректным, идемпотентным, read-only на preflight и безопасным для повторного запуска.

## Подтверждённый маппинг

| DDS | RAW |
|---|---|
| `purchase_id` | `purchid` |
| `vendor_account` | `orderaccount` |
| `order_date` | `createddatetime` |
| `delivery_date` | `deliverydate` |
| `currency_code` | `currencycode` |
| `purchase_status` | `purchstatus` |
| `modified_datetime` | `modifieddatetime` |
| `created_datetime` | `createddatetime` |
| `data_area_id` | `dataareaid` |

Пустые строки нормализуются через `NULLIF(btrim(...), '')`. Технические даты AX `1900-01-01` для `delivery_date` преобразуются в `NULL`.

## Идемпотентность

Бизнес-ключ: `(purchase_id, data_area_id)`.

Повторная загрузка использует `ON CONFLICT ... DO UPDATE`. Обновляются все атрибуты кроме `purchase_order_id`, `purchase_id` и `data_area_id`. Неизменившиеся строки не переписываются: используется `IS DISTINCT FROM`, чтобы сократить WAL и dead tuples.

## Preflight

Preflight обязан:

- работать через read-only connection;
- не создавать `etl.load_run` и не выполнять DML;
- считать отсутствующую колонку RAW блокирующей ошибкой;
- дедуплицировать сообщения об одной отсутствующей колонке;
- проверять все колонки составного conflict key;
- требовать точный уникальный индекс по всей комбинации;
- выполнять `EXPLAIN` без `ANALYZE` для полного набора выражений;
- возвращать `BLOCKED` при любой ошибке.

Режимы `full` и `resume` автоматически выполняют preflight до создания ETL run и прекращают работу при `BLOCKED`.

## Full-table

`raw_ax.purchtable` загружается одним логическим resumable chunk. Для `full_table` INSERT не должен содержать синтетический диапазон `recid_bigint > 0 AND recid_bigint <= 1`.

## Подготовка БД

Перед загрузкой выполнить `monitoring/002_create_purchase_order_business_key.sql`. Скрипт проверяет пустые и повторяющиеся бизнес-ключи и создаёт индекс `ux_purchase_order_business_key` через `CREATE UNIQUE INDEX CONCURRENTLY`.

## Приёмка

```powershell
python -m pytest tests/test_purchase_order_upsert.py `
    tests/test_preflight_full_table_regression.py -q `
    --import-mode=importlib

python -m ax_to_postgres_etl.pipelines.dds_cli `
    --mode preflight `
    --stage purchase_order

python -m ax_to_postgres_etl.pipelines.dds_cli `
    --mode full `
    --stage purchase_order
```

Критерии:

- unit-тесты проходят;
- preflight возвращает `READY` или `READY_WITH_WARNINGS`, не `BLOCKED`;
- повторный запуск не создаёт дубликатов;
- изменённые атрибуты обновляются;
- неизменившиеся строки не переписываются;
- `full_table` охватывает всю исходную таблицу.
