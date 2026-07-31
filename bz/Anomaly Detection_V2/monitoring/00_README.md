# Подготовка raw_ax.salestable -> dds.sales_order

Порядок запуска:

1. `01_diagnose_sales_order_mapping.sql` — только чтение.
2. `02_add_source_recid_to_dds.sql` — изменение пустой DDS-таблицы.
3. `03_validate_salestable_recid.sql` — полная read-only проверка `recid`.
4. `04_create_salestable_recid_bigint_index.sql` — создание функционального индекса CONCURRENTLY.
5. `05_validate_sales_order_ready.sql` — итоговая read-only проверка.
6. `06_sales_order_insert_template.sql` — шаблон mapping/INSERT, не запускать до интеграции в ETL.

## Важное решение по invoice_date

В `raw_ax.salestable` отдельная колонка даты счета не обнаружена.
Колонка `invoiceaccount` — это счет/контрагент, а не дата.

Поэтому:
- `dds.sales_order.invoice_date` в шаблоне заполняется `NULL`;
- реальную дату счета следует получать из подтвержденного источника,
  вероятно отдельной таблицы проводок/счетов, но источник должен быть
  установлен проектным mapping, а не предположением.

## Риски

`03_validate_salestable_recid.sql` выполняет один полный scan RAW.
Это осознанная одноразовая проверка перед функциональным индексом.

`04_create_salestable_recid_bigint_index.sql`:
- нельзя запускать внутри транзакции;
- выполняет чтение всей `raw_ax.salestable`;
- создает WAL и временную нагрузку на диск;
- `CONCURRENTLY` не блокирует обычные SELECT/INSERT/UPDATE на длительное время,
  но требует больше времени и двух проходов;
- при отмене может оставить INVALID index, который нужно удалить CONCURRENTLY.

Перед шагом 04 проверьте:
- свободное место на диске;
- `pg_stat_activity`;
- `pg_stat_progress_create_index`;
- существующие индексы;
- отсутствие тяжелого ETL.
