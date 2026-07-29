"""
Загрузка UTF-8 CSV в PostgreSQL.
Заменяет повреждённые данные в raw_ax.wms_journalwarehouseoperationtable.

Запуск: python load_utf8_csv.py
"""

import os
import csv
import time
import io
import psycopg2
from datetime import datetime


# Конфигурация
PG_HOST = "localhost"
PG_PORT = 5432
PG_DATABASE = "wms_analysis"
PG_USER = "postgres"
PG_PASSWORD = "123"
PG_SCHEMA = "raw_ax"

TABLE_NAME = "wms_journalwarehouseoperationtable"
CSV_DIR = r"D:\py_pro\jira_focus_exporter\bz\Anomaly Detection\generated_sql\UTF8_EXPORT"
CSV_FILE = os.path.join(CSV_DIR, f"{TABLE_NAME.upper()}_utf8.csv")
BATCH_SIZE = 10000


def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}")


def load_csv_to_postgresql():
    """Загрузка CSV в PostgreSQL через COPY"""
    
    log(f"Загрузка {CSV_FILE} в {PG_SCHEMA}.{TABLE_NAME}")
    
    # Проверка файла
    if not os.path.exists(CSV_FILE):
        log(f"ОШИБКА: Файл не найден: {CSV_FILE}")
        log("Сначала запустите export_utf8.py для экспорта данных")
        return
    
    file_size = os.path.getsize(CSV_FILE) / 1024 / 1024
    log(f"Размер файла: {file_size:.1f} MB")
    
    # Подключение к PostgreSQL
    log("Подключение к PostgreSQL...")
    conn = psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DATABASE,
        user=PG_USER,
        password=PG_PASSWORD
    )
    conn.autocommit = False
    
    # КРИТИЧЕСКИ ВАЖНО: установить client_encoding = UTF8
    cursor = conn.cursor()
    cursor.execute("SET client_encoding = 'UTF8'")
    cursor.execute("SHOW client_encoding")
    actual_enc = cursor.fetchone()[0]
    cursor.close()
    
    if actual_enc.upper() != 'UTF8':
        log(f"  WARNING: client_encoding is {actual_enc}, not UTF8!")
    else:
        log(f"  Client encoding: UTF8")
    log("OK")
    
    # Очистка старых данных
    log("Очистка старых данных...")
    cursor = conn.cursor()
    cursor.execute(f"DELETE FROM {PG_SCHEMA}.{TABLE_NAME}")
    conn.commit()
    log("Старые данные удалены")
    
    # Загрузка через COPY (самый быстрый способ)
    log("Загрузка через COPY...")
    start_time = time.time()
    
    cursor = conn.cursor()
    
    # SQL для COPY
    columns = "EMPLID, NAMEALIAS, STARTDATE, ENDDATE, OPERATIONTYPE, DURATIONOPERATION"
    copy_sql = f"COPY {PG_SCHEMA}.{TABLE_NAME} ({columns}) FROM STDIN WITH (FORMAT text, NULL '')"
    
    # Чтение CSV и загрузка
    with open(CSV_FILE, 'r', encoding='utf-8') as f:
        # Пропускаем заголовок
        next(f)
        
        # Создаём буфер для COPY
        buffer = []
        total_loaded = 0
        
        for line in f:
            buffer.append(line)
            
            if len(buffer) >= BATCH_SIZE:
                # Загружаем батч
                buffer_content = ''.join(buffer)
                cursor.copy_expert(copy_sql, io.StringIO(buffer_content))
                conn.commit()
                
                total_loaded += len(buffer)
                elapsed = time.time() - start_time
                speed = total_loaded / elapsed if elapsed > 0 else 0
                log(f"  Загружено: {total_loaded:,} строк ({speed:,.0f} rows/sec)")
                
                buffer = []
        
        # Загружаем остаток
        if buffer:
            buffer_content = ''.join(buffer)
            cursor.copy_expert(copy_sql, io.StringIO(buffer_content))
            conn.commit()
            total_loaded += len(buffer)
    
    elapsed = time.time() - start_time
    log(f"Загрузка завершена: {total_loaded:,} строк за {elapsed:.1f} сек")
    
    # Проверка результата
    log("Проверка результата...")
    cursor = conn.cursor()
    cursor.execute(f"SELECT COUNT(*) FROM {PG_SCHEMA}.{TABLE_NAME}")
    final_count = cursor.fetchone()[0]
    log(f"Итого строк в таблице: {final_count:,}")
    
    # Проверка кодировки имён
    cursor.execute(f"""
        SELECT COUNT(*) 
        FROM {PG_SCHEMA}.{TABLE_NAME} 
        WHERE NAMEALIAS LIKE '%?%'
    """)
    corrupted = cursor.fetchone()[0]
    
    if corrupted == 0:
        log("✓ Все имена员工 сохранены корректно!")
    else:
        log(f"⚠ {corrupted} записей с повреждёнными именами")
    
    # Показать пример данных
    log("Пример данных (первые 5 строк):")
    cursor.execute(f"""
        SELECT EMPLID, NAMEALIAS, STARTDATE 
        FROM {PG_SCHEMA}.{TABLE_NAME} 
        LIMIT 5
    """)
    for row in cursor.fetchall():
        log(f"  {row[0]} | {row[1]} | {row[2]}")
    
    cursor.close()
    conn.close()
    
    log("Готово!")


if __name__ == "__main__":
    print("=" * 60)
    print("Загрузка UTF-8 CSV в PostgreSQL")
    print("=" * 60)
    print()
    
    load_csv_to_postgresql()
    
    print()
    print("=" * 60)
    print("Для обновления DDS и mart:")
    print("  python -c \"from features.feature_engineering import run_feature_engineering; run_feature_engineering()\"")
    print("=" * 60)
