"""
Запрос данных из SQL Server AX63_WMS_TEST.
Запуск: python -B query_table.py <таблица> [строк]

Примеры:
  python -B query_table.py INVENTTABLE 10
  python -B query_table.py LFL_MARKINGCODETABLE 5
  python -B query_table.py WMS_PICKDIFFACTLINE 20
"""

import sys
import pyodbc


def main():
    if len(sys.argv) < 2:
        print("Использование: python -B query_table.py <таблица> [строк]")
        print("Пример: python -B query_table.py INVENTTABLE 10")
        sys.exit(1)

    table_name = sys.argv[1].upper()
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10

    conn_str = "DRIVER={SQL Server};SERVER=SWS-DB-T1;DATABASE=AX63_WMS_TEST;Trusted_Connection=yes;"

    print(f"Подключение к SQL Server...")
    try:
        conn = pyodbc.connect(conn_str)
        print("OK\n")
    except Exception as e:
        print(f"Ошибка: {e}")
        sys.exit(1)

    cursor = conn.cursor()

    # Структура таблицы
    print(f"=== Структура {table_name} ===")
    cursor.execute(f"""
        SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = '{table_name}'
        ORDER BY ORDINAL_POSITION
    """)
    columns = cursor.fetchall()
    print(f"Колонок: {len(columns)}\n")
    print(f"{'Колонка':<35} {'Тип':<15} {'Длина':<8} {'NULL'}")
    print("-" * 70)
    for col in columns:
        name = col[0]
        dtype = col[1]
        length = col[2] or "-"
        nullable = col[3]
        print(f"{name:<35} {dtype:<15} {str(length):<8} {nullable}")

    # Количество строк
    print(f"\n=== Количество строк ===")
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    count = cursor.fetchone()[0]
    print(f"Всего строк: {count:,}\n")

    # Первые N строк
    print(f"=== Первые {limit} строк ===\n")
    cursor.execute(f"SELECT TOP {limit} * FROM {table_name}")
    rows = cursor.fetchall()
    col_names = [desc[0] for desc in cursor.description]

    # Вывод в виде таблицы
    if rows:
        # Определяем ширину колонок
        widths = [len(name) for name in col_names]
        for row in rows:
            for i, val in enumerate(row):
                if val is not None:
                    widths[i] = max(widths[i], min(len(str(val)), 30))

        # Заголовок
        header = " | ".join([name[:widths[i]].ljust(widths[i]) for i, name in enumerate(col_names)])
        print(header)
        print("-" * len(header))

        # Строки
        for row in rows:
            line = " | ".join([
                str(val)[:30].ljust(widths[i]) if val is not None else "NULL".ljust(widths[i])
                for i, val in enumerate(row)
            ])
            print(line)

    conn.close()
    print(f"\nГотово. Всего строк в таблице: {count:,}")


if __name__ == "__main__":
    main()
