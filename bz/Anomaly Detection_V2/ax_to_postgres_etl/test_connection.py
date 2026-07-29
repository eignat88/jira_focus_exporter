"""Тест подключения к SQL Server."""

import pyodbc

conn_str = "DRIVER={SQL Server};SERVER=SWS-DB-T1;DATABASE=AX63_WMS_TEST;Trusted_Connection=yes;"

print("Подключение к SQL Server...")
print(f"Connection string: {conn_str}")
print()

try:
    conn = pyodbc.connect(conn_str)
    print("Connected!")
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM INVENTTABLE")
    count = cursor.fetchone()[0]
    print(f"INVENTTABLE rows: {count:,}")

    cursor.execute("SELECT SERVERPROPERTY('ServerName')")
    server = cursor.fetchone()[0]
    print(f"Server: {server}")

    cursor.execute("SELECT SYSTEM_USER")
    user = cursor.fetchone()[0]
    print(f"User: {user}")

    conn.close()
    print()
    print("Подключение успешно!")

except Exception as e:
    print(f"Error: {e}")
    print()
    print("Убедитесь, что скрипт запущен из cmd:")
    print("  runas /netonly /user:ALKOR\\ignatchenko-adm cmd")
    print("  cd D:\\py_pro\\jira_focus_exporter\\bz\\Anomaly Detection\\ax_to_postgres_etl")
    print("  python test_connection.py")
