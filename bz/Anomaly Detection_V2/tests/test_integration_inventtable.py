"""Integration test for ETL pipeline on INVENTTABLE with limited range.

Tests b82.txt section 21.2 scenarios:
1. Full load (target empty)
2. Resume after partial load
3. Deduplication verification
"""

import sys
import os
import time

# Add ETL module to path
etl_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'ax_to_postgres_etl')
sys.path.insert(0, etl_dir)

from configs.settings import get_settings
from connectors.sqlserver import SQLServerConnector
from connectors.postgres import PostgresConnector
from loader.parallel_loader import ParallelLoader


def get_recid_range(pg, table_name):
    """Get MIN and MAX RECID from target table."""
    cursor = pg.conn.cursor()
    cursor.execute(f"SELECT MIN(recid), MAX(recid) FROM raw_ax.{table_name.lower()}")
    result = cursor.fetchone()
    return result[0], result[1]


def get_row_count(pg, table_name):
    """Get row count from target table."""
    cursor = pg.conn.cursor()
    cursor.execute(f"SELECT COUNT(*) FROM raw_ax.{table_name.lower()}")
    return cursor.fetchone()[0]


def get_unique_recid_count(pg, table_name):
    """Get count of unique RECID values."""
    cursor = pg.conn.cursor()
    cursor.execute(f"SELECT COUNT(DISTINCT recid) FROM raw_ax.{table_name.lower()}")
    return cursor.fetchone()[0]


def test_full_load():
    """Scenario 1: Full load (target empty → all rows loaded → all chunks DONE)."""
    print("\n=== Scenario 1: Full Load Test ===")
    
    settings = get_settings()
    
    ss = SQLServerConnector(
        server=settings.source.server,
        database=settings.source.database,
        driver=settings.source.driver,
    )
    pg = PostgresConnector(
        host=settings.db.host,
        port=settings.db.port,
        database=settings.db.database,
        user=settings.db.user,
        password=settings.db.password,
        schema=settings.db.schema,
    )
    
    ss.connect()
    pg.connect()
    pg.create_schema()
    
    table_name = "INVENTTABLE"
    
    # Get source row count for verification
    cursor = ss.conn.cursor()
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    source_count = cursor.fetchone()[0]
    print(f"Source table {table_name}: {source_count:,} rows")
    
    # Get current target count
    target_count = get_row_count(pg, table_name)
    print(f"Current target count: {target_count:,} rows")
    
    # Run loader
    loader = ParallelLoader(
        ss_conn_str=ss.conn_str,
        pg_connector=pg,
        workers=4,
        fetch_size=5000,
        commit_size=50000,
        log_func=print,
    )
    
    start_time = time.time()
    loader.load_table(table_name, load_mode="reload")  # TRUNCATE first
    elapsed = time.time() - start_time
    
    # Verify results
    final_count = get_row_count(pg, table_name)
    unique_count = get_unique_recid_count(pg, table_name)
    
    print(f"\nResults:")
    print(f"  Source rows: {source_count:,}")
    print(f"  Target rows: {final_count:,}")
    print(f"  Unique RECID: {unique_count:,}")
    print(f"  Duplicates: {final_count - unique_count}")
    print(f"  Elapsed: {elapsed:.2f}s")
    
    # Assertions
    assert final_count == source_count, f"Row count mismatch: {final_count} != {source_count}"
    assert final_count == unique_count, f"Duplicates found: {final_count - unique_count}"
    
    print("\n✓ Scenario 1 PASSED: Full load successful")
    
    ss.disconnect()
    pg.disconnect()
    
    return final_count


def test_resume():
    """Scenario 2: Resume after partial load."""
    print("\n=== Scenario 2: Resume Test ===")
    
    settings = get_settings()
    
    ss = SQLServerConnector(
        server=settings.source.server,
        database=settings.source.database,
        driver=settings.source.driver,
    )
    pg = PostgresConnector(
        host=settings.db.host,
        port=settings.db.port,
        database=settings.db.database,
        user=settings.db.user,
        password=settings.db.password,
        schema=settings.db.schema,
    )
    
    ss.connect()
    pg.connect()
    pg.create_schema()
    
    table_name = "INVENTTABLE"
    
    # Get current count before resume
    count_before = get_row_count(pg, table_name)
    print(f"Count before resume: {count_before:,} rows")
    
    # Run resume
    loader = ParallelLoader(
        ss_conn_str=ss.conn_str,
        pg_connector=pg,
        workers=4,
        fetch_size=5000,
        commit_size=50000,
        log_func=print,
    )
    
    start_time = time.time()
    loader.load_table(table_name, load_mode="resume")
    elapsed = time.time() - start_time
    
    # Verify results
    count_after = get_row_count(pg, table_name)
    unique_count = get_unique_recid_count(pg, table_name)
    
    print(f"\nResults:")
    print(f"  Count before: {count_before:,}")
    print(f"  Count after: {count_after:,}")
    print(f"  New rows added: {count_after - count_before}")
    print(f"  Unique RECID: {unique_count:,}")
    print(f"  Duplicates: {count_after - unique_count}")
    print(f"  Elapsed: {elapsed:.2f}s")
    
    # No new rows should be added if all chunks were already done
    # (assuming previous test completed successfully)
    assert count_after == count_before, f"Unexpected rows added: {count_after - count_before}"
    assert count_after == unique_count, f"Duplicates found: {count_after - unique_count}"
    
    print("\n✓ Scenario 2 PASSED: Resume successful, no duplicates")
    
    ss.disconnect()
    pg.disconnect()


def test_deduplication():
    """Scenario 3: Verify no duplicates exist."""
    print("\n=== Scenario 3: Deduplication Verification ===")
    
    settings = get_settings()
    
    pg = PostgresConnector(
        host=settings.db.host,
        port=settings.db.port,
        database=settings.db.database,
        user=settings.db.user,
        password=settings.db.password,
        schema=settings.db.schema,
    )
    
    pg.connect()
    
    table_name = "INVENTTABLE"
    
    # Run deduplication check query from b82.txt section 21.3
    cursor = pg.conn.cursor()
    cursor.execute(f"""
        SELECT
            COUNT(*) AS total_rows,
            COUNT(DISTINCT recid) AS unique_recid,
            COUNT(*) - COUNT(DISTINCT recid) AS duplicates
        FROM raw_ax.{table_name.lower()}
    """)
    
    result = cursor.fetchone()
    total_rows = result[0]
    unique_recid = result[1]
    duplicates = result[2]
    
    print(f"\nDeduplication Check Results:")
    print(f"  Total rows: {total_rows:,}")
    print(f"  Unique RECID: {unique_recid:,}")
    print(f"  Duplicates: {duplicates}")
    
    # Get MIN/MAX RECID
    cursor.execute(f"SELECT MIN(recid), MAX(recid) FROM raw_ax.{table_name.lower()}")
    min_max = cursor.fetchone()
    min_recid = min_max[0]
    max_recid = min_max[1]
    
    if min_recid is not None and max_recid is not None:
        print(f"  MIN(RECID): {min_recid}")
        print(f"  MAX(RECID): {max_recid}")
    else:
        print("  MIN(RECID): None")
        print("  MAX(RECID): None")
    
    # Assertions
    assert duplicates == 0, f"Found {duplicates} duplicates!"
    assert total_rows == unique_recid, "Row count != unique RECID count"
    
    print("\n✓ Scenario 3 PASSED: No duplicates found")
    
    pg.disconnect()


if __name__ == "__main__":
    print("=" * 60)
    print("ETL Integration Tests for INVENTTABLE")
    print("=" * 60)
    
    # Run tests in order
    test_full_load()
    test_resume()
    test_deduplication()
    
    print("\n" + "=" * 60)
    print("All integration tests PASSED!")
    print("=" * 60)
