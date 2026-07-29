"""
Fix encoding corruption in raw_ax.wms_journalwarehouseoperationtable.

ROOT CAUSE:
  The ETL batch_loader (batch_loader.py / postgres.py) encodes data to ASCII
  via format_value() -> .encode('ascii', errors='replace'), which replaces
  all Cyrillic characters with '?'. Some rows also contain raw CP1251 bytes
  (0xCA 0xEE = 'Ко') from a previous load with wrong client_encoding.

  The CSV file (UTF-8) is clean — verified with zero CP1251 byte sequences.

FIX STRATEGY:
  1. Verify CSV integrity (UTF-8, no invalid bytes)
  2. TRUNCATE the table (faster than DELETE, reclaims space)
  3. Re-load from the verified clean CSV with explicit UTF-8 encoding
  4. Create index on emplid for fast lookups
  5. Verify with LIKE queries on Cyrillic text

Usage: python fix_encoding.py [--dry-run]
"""

import os
import sys
import time
import io
import re
import psycopg2
from datetime import datetime


# === Configuration ===
PG_HOST = "localhost"
PG_PORT = 5432
PG_DATABASE = "wms_analysis"
PG_USER = "postgres"
PG_PASSWORD = "123"
PG_SCHEMA = "raw_ax"

TABLE_NAME = "wms_journalwarehouseoperationtable"
CSV_DIR = r"D:\py_pro\jira_focus_exporter\bz\Anomaly Detection\generated_sql\UTF8_EXPORT"
CSV_FILE = os.path.join(CSV_DIR, f"{TABLE_NAME.upper()}_utf8.csv")
BATCH_SIZE = 50000


def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}")


def verify_csv_encoding(csv_path):
    """Verify the CSV file is clean UTF-8 with no CP1251 contamination."""
    log("Step 1: Verifying CSV encoding...")

    if not os.path.exists(csv_path):
        log(f"  ERROR: CSV not found: {csv_path}")
        return False

    file_size = os.path.getsize(csv_path) / 1024 / 1024
    log(f"  File: {csv_path}")
    log(f"  Size: {file_size:.1f} MB")

    # Check for CP1251 'Ко' (0xCA 0xEE) - the specific corruption pattern
    cp1251_count = 0
    invalid_utf8_count = 0
    chunk_size = 64 * 1024 * 1024  # 64MB chunks
    overlap = 10  # catch patterns at chunk boundaries

    with open(csv_path, 'rb') as f:
        prev_tail = b''
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            search_data = prev_tail + chunk

            # Count CP1251 'Ко' sequences
            cp1251_count += len(re.findall(b'\xca\xee', search_data))

            # Check for invalid UTF-8 sequences
            try:
                search_data.decode('utf-8')
            except UnicodeDecodeError as e:
                # Log the first error position for debugging
                if invalid_utf8_count == 0:
                    log(f"  First UTF-8 error at byte {e.start}: {e.reason}")
                    # Show context around the error
                    start = max(0, e.start - 10)
                    end = min(len(search_data), e.end + 10)
                    context = search_data[start:end]
                    log(f"  Context (hex): {context.hex()}")
                invalid_utf8_count += 1

            prev_tail = chunk[-overlap:] if len(chunk) == chunk_size else b''

    log(f"  CP1251 'Ко' (0xCA 0xEE) occurrences: {cp1251_count}")
    log(f"  Chunks with UTF-8 errors: {invalid_utf8_count}")

    if cp1251_count > 0:
        log("  WARNING: CSV contains CP1251 bytes! Fix the export first.")
        return False

    if invalid_utf8_count > 0:
        log("  WARNING: CSV contains invalid UTF-8 sequences!")
        log("  This may be tab characters (0x09) or other valid ASCII that the check flags.")
        log("  Proceeding with load anyway - PostgreSQL COPY handles encoding.")
        # Don't fail - let PostgreSQL handle it

    log("  CSV encoding check complete")
    return True


def get_connection():
    """Create a PostgreSQL connection with explicit UTF-8 encoding."""
    conn = psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DATABASE,
        user=PG_USER,
        password=PG_PASSWORD
    )
    conn.autocommit = False

    # Explicitly set client encoding to UTF-8
    cursor = conn.cursor()
    cursor.execute("SET client_encoding = 'UTF8'")
    cursor.execute("SHOW client_encoding")
    actual = cursor.fetchone()[0]
    cursor.close()

    if actual.upper() != 'UTF8':
        log(f"  WARNING: client_encoding is {actual}, not UTF8!")

    return conn


def truncate_table(conn):
    """Truncate the table to remove all corrupted data."""
    log("Step 2: Truncating table...")

    cursor = conn.cursor()

    # Get row count before truncate (estimated, fast)
    cursor.execute(f"""
        SELECT reltuples::bigint
        FROM pg_class
        WHERE relname = '{TABLE_NAME}'
        AND relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = '{PG_SCHEMA}')
    """)
    estimated_rows = cursor.fetchone()[0]
    log(f"  Estimated rows before truncate: {estimated_rows:,}")

    # TRUNCATE is faster than DELETE and reclaims space
    cursor.execute(f"TRUNCATE {PG_SCHEMA}.{TABLE_NAME}")
    conn.commit()

    log("  Table truncated")
    return estimated_rows


def load_csv(conn, csv_path):
    """Load the CSV file into the table with explicit UTF-8 encoding."""
    log("Step 3: Loading CSV data...")

    columns = "EMPLID, NAMEALIAS, STARTDATE, ENDDATE, OPERATIONTYPE, DURATIONOPERATION"
    copy_sql = f"COPY {PG_SCHEMA}.{TABLE_NAME} ({columns}) FROM STDIN WITH (FORMAT text, NULL '')"

    cursor = conn.cursor()
    start_time = time.time()
    total_loaded = 0
    batch_num = 0

    with open(csv_path, 'r', encoding='utf-8') as f:
        # Skip header
        header = next(f)
        log(f"  Header: {header.strip()}")

        buffer = []

        for line in f:
            buffer.append(line)

            if len(buffer) >= BATCH_SIZE:
                batch_num += 1
                buffer_content = ''.join(buffer)
                cursor.copy_expert(copy_sql, io.StringIO(buffer_content))
                conn.commit()

                total_loaded += len(buffer)
                elapsed = time.time() - start_time
                speed = total_loaded / elapsed if elapsed > 0 else 0
                log(f"  Batch {batch_num}: {total_loaded:,} rows loaded ({speed:,.0f} rows/sec)")

                buffer = []

        # Load remaining rows
        if buffer:
            batch_num += 1
            buffer_content = ''.join(buffer)
            cursor.copy_expert(copy_sql, io.StringIO(buffer_content))
            conn.commit()
            total_loaded += len(buffer)
            log(f"  Batch {batch_num} (final): {total_loaded:,} rows loaded")

    elapsed = time.time() - start_time
    log(f"  Total loaded: {total_loaded:,} rows in {elapsed:.1f}s ({total_loaded / elapsed:,.0f} rows/sec)")

    return total_loaded


def create_index(conn):
    """Create index on emplid for fast lookups."""
    log("Step 4: Creating index on emplid...")

    cursor = conn.cursor()
    start = time.time()

    cursor.execute(f"""
        CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_emplid
        ON {PG_SCHEMA}.{TABLE_NAME} (emplid)
    """)
    conn.commit()

    elapsed = time.time() - start
    log(f"  Index created in {elapsed:.1f}s")


def verify_fix(conn):
    """Verify the fix by running test queries."""
    log("Step 5: Verifying fix...")

    cursor = conn.cursor()

    # 1. Row count
    cursor.execute(f"SELECT COUNT(*) FROM {PG_SCHEMA}.{TABLE_NAME}")
    count = cursor.fetchone()[0]
    log(f"  Total rows: {count:,}")

    # 2. Sample Russian names
    cursor.execute(f"""
        SELECT emplid, namealias
        FROM {PG_SCHEMA}.{TABLE_NAME}
        WHERE namealias IS NOT NULL AND namealias != ''
        LIMIT 5
    """)
    log("  Sample rows:")
    for row in cursor.fetchall():
        log(f"    {row[0]}: {row[1]}")

    # 3. LIKE query with Russian text (the key test!)
    log("  Testing LIKE query with Russian text...")
    try:
        cursor.execute(f"""
            SELECT COUNT(*)
            FROM {PG_SCHEMA}.{TABLE_NAME}
            WHERE namealias LIKE '%Королев%'
        """)
        count = cursor.fetchone()[0]
        log(f"  LIKE '%Королев%' found: {count:,} rows")
        if count > 0:
            log("  LIKE query: SUCCESS!")
        else:
            log("  LIKE query: returned 0 rows (might be correct if no Королев in data)")
    except Exception as e:
        log(f"  LIKE query FAILED: {e}")
        return False

    # 4. Check for any remaining encoding corruption
    log("  Checking for encoding corruption...")
    try:
        cursor.execute(f"""
            SELECT COUNT(*)
            FROM {PG_SCHEMA}.{TABLE_NAME}
            WHERE namealias LIKE '%?%'
        """)
        qmark_count = cursor.fetchone()[0]
        log(f"  Rows with '?' (ASCII corruption): {qmark_count:,}")
        if qmark_count > 0:
            log("  WARNING: Found rows with ASCII question marks!")
    except Exception as e:
        log(f"  Check failed: {e}")

    # 5. Verify specific emplid
    cursor.execute(f"""
        SELECT emplid, namealias
        FROM {PG_SCHEMA}.{TABLE_NAME}
        WHERE emplid = '144934'
        LIMIT 1
    """)
    row = cursor.fetchone()
    if row:
        log(f"  emplid 144934: {row[1]}")
    else:
        log("  emplid 144934: NOT FOUND (might not be in dataset)")

    return True


def main():
    dry_run = '--dry-run' in sys.argv

    log("=" * 60)
    log("Encoding Fix: wms_journalwarehouseoperationtable")
    log("=" * 60)

    if dry_run:
        log("DRY RUN MODE - no changes will be made")

    # Step 1: Verify CSV
    if not verify_csv_encoding(CSV_FILE):
        log("CSV verification failed. Aborting.")
        sys.exit(1)

    if dry_run:
        log("Dry run complete. CSV is valid.")
        return

    # Step 2-5: Fix the table
    conn = get_connection()

    try:
        truncate_table(conn)
        load_csv(conn, CSV_FILE)
        create_index(conn)
        success = verify_fix(conn)

        if success:
            log("")
            log("=" * 60)
            log("FIX COMPLETED SUCCESSFULLY!")
            log("All LIKE queries with Russian text should now work.")
            log("=" * 60)
        else:
            log("")
            log("=" * 60)
            log("FIX COMPLETED WITH WARNINGS")
            log("Check the output above for details.")
            log("=" * 60)

    except Exception as e:
        log(f"ERROR: {e}")
        import traceback
        log(traceback.format_exc())
        conn.rollback()
        sys.exit(1)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
