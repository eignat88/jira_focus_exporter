"""
Экспорт таблицы из SQL Server в CSV с кодировкой UTF-8.
Для обхода проблемы повреждения русских символов.

Запуск: python export_utf8.py
Требуется: runas /netonly /user:ALKOR\ignatchenko-adm cmd
"""

import os
import csv
import io
import time
import pyodbc
from datetime import datetime


# Конфигурация
SQL_SERVER = "SWS-DB-T1"
SQL_DATABASE = "AX63_WMS_TEST"
TABLE_NAME = "WMS_JOURNALWAREHOUSEOPERATIONTABLE"
CSV_DIR = r"D:\py_pro\jira_focus_exporter\bz\Anomaly Detection\generated_sql\UTF8_EXPORT"
BATCH_SIZE = 100000

# Колонки для экспорта (как в config.yaml)
COLUMNS = [
    "EMPLID",
    "NAMEALIAS",
    "STARTDATE",
    "ENDDATE",
    "OPERATIONTYPE",
    "DURATIONOPERATION"
]


def get_sql_connection():
    """Подключение к SQL Server через Windows Auth"""
    conn_str = (
        f"DRIVER={{SQL Server}};"
        f"SERVER={SQL_SERVER};"
        f"DATABASE={SQL_DATABASE};"
        f"Trusted_Connection=yes;"
    )
    return pyodbc.connect(conn_str)


def export_table_to_utf8_csv():
    """Экспорт таблицы в CSV с кодировкой UTF-8"""
    os.makedirs(CSV_DIR, exist_ok=True)
    
    csv_file = os.path.join(CSV_DIR, f"{TABLE_NAME}_utf8.csv")
    log_file = os.path.join(CSV_DIR, f"export_{TABLE_NAME}.log")
    
    def log(msg):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {msg}"
        print(line)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    
    log(f"Экспорт {TABLE_NAME} в UTF-8 CSV")
    log(f"Колонки: {', '.join(COLUMNS)}")
    
    # Подключение к SQL Server
    log("Подключение к SQL Server...")
    conn = get_sql_connection()
    cursor = conn.cursor()
    log("OK")
    
    # Получение общего количества строк
    cursor.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}")
    total_count = cursor.fetchone()[0]
    log(f"Всего строк: {total_count:,}")
    
    # Формирование запроса
    col_list = ", ".join(COLUMNS)
    query = f"""
        SELECT {col_list}
        FROM {TABLE_NAME}
        ORDER BY STARTDATE, RECID
    """
    
    # Экспорт в CSV
    log(f"Начало экспорта в {csv_file}...")
    start_time = time.time()
    exported = 0
    
    # Открытие CSV файла с кодировкой UTF-8
    with open(csv_file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter='\t')
        
        # Запись заголовка
        writer.writerow(COLUMNS)
        
        # Построчная выгрузка с конвертацией кодировки
        cursor.execute(query)
        
        while True:
            rows = cursor.fetchmany(BATCH_SIZE)
            if not rows:
                break
            
            for row in rows:
                # Конвертация байтов в строки с правильной кодировкой
                clean_row = []
                for val in row:
                    if val is None:
                        clean_row.append("")
                    elif isinstance(val, bytes):
                        # Попытка декодирования из CP1251 (Windows Russian)
                        try:
                            clean_row.append(val.decode('cp1251'))
                        except:
                            try:
                                clean_row.append(val.decode('cp1252'))
                            except:
                                clean_row.append(val.decode('utf-8', errors='replace'))
                    else:
                        # Строка - конвертируем в UTF-8
                        s = str(val)
                        # Удаляем null bytes
                        s = s.replace('\x00', '')
                        clean_row.append(s)
                
                writer.writerow(clean_row)
                exported += 1
            
            elapsed = time.time() - start_time
            speed = exported / elapsed if elapsed > 0 else 0
            pct = exported / total_count * 100 if total_count > 0 else 0
            log(f"  Экспортировано: {exported:,} / {total_count:,} ({pct:.1f}%) - {speed:,.0f} rows/sec")
    
    elapsed = time.time() - start_time
    log(f"Экспорт завершён: {exported:,} строк за {elapsed:.1f} сек")
    log(f"Файл: {csv_file}")
    log(f"Размер: {os.path.getsize(csv_file) / 1024 / 1024:.1f} MB")
    
    # Проверка кодировки - читаем первые строки
    log("Проверка кодировки...")
    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.reader(f, delimiter='\t')
        header = next(reader)
        first_row = next(reader)
        
        # Проверяем есть ли русские символы
        has_russian = False
        for val in first_row:
            if any('\u0400' <= c <= '\u04FF' for c in str(val)):
                has_russian = True
                break
        
        if has_russian:
            log("✓ Русские символы сохранены корректно!")
        else:
            log("⚠ Русские символы не обнаружены в первой строке")
    
    cursor.close()
    conn.close()
    
    return csv_file


if __name__ == "__main__":
    print("=" * 60)
    print("Экспорт SQL Server → UTF-8 CSV")
    print("=" * 60)
    print()
    print("ВАЖНО: Запускать из консоли с Windows Auth:")
    print("  runas /netonly /user:ALKOR\\ignatchenko-adm cmd")
    print()
    
    csv_file = export_table_to_utf8_csv()
    
    print()
    print("=" * 60)
    print("Экспорт завершён!")
    print(f"CSV файл: {csv_file}")
    print()
    print("Для загрузки в PostgreSQL:")
    print("  python load_utf8_csv.py")
    print("=" * 60)
