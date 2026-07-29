"""
Comparative testing of fetchall() vs fetchmany() for SQL Server data fetching.

Usage: python test_fetch_performance.py
"""

import time
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from configs.settings import get_settings
from connectors.sqlserver import SQLServerConnector


def test_fetchall(cursor, sql, batch_label):
    """Test fetchall() performance."""
    start = time.perf_counter()
    rows = cursor.fetchall()
    elapsed = time.perf_counter() - start
    
    memory_bytes = sys.getsizeof(rows)
    for row in rows:
        memory_bytes += sys.getsizeof(row)
    
    return {
        'method': 'fetchall',
        'batch': batch_label,
        'rows': len(rows),
        'time': elapsed,
        'rows_per_sec': len(rows) / elapsed if elapsed > 0 else 0,
        'memory_mb': memory_bytes / 1024 / 1024,
    }


def test_fetchmany(cursor, sql, batch_size, batch_label):
    """Test fetchmany() performance with specified batch size."""
    start = time.perf_counter()
    total_rows = 0
    total_memory = 0
    
    cursor.arraysize = batch_size
    
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break
        total_rows += len(rows)
        total_memory += sys.getsizeof(rows)
    
    elapsed = time.perf_counter() - start
    
    return {
        'method': f'fetchmany({batch_size})',
        'batch': batch_label,
        'rows': total_rows,
        'time': elapsed,
        'rows_per_sec': total_rows / elapsed if elapsed > 0 else 0,
        'memory_mb': total_memory / 1024 / 1024,
    }


def run_tests():
    """Run comparative tests."""
    settings = get_settings()
    
    print("=" * 70)
    print("Fetch Performance Comparative Testing")
    print("=" * 70)
    
    # Connect to SQL Server
    print("\nConnecting to SQL Server...")
    ss = SQLServerConnector(
        server=settings.source.server,
        database=settings.source.database,
        driver=settings.source.driver,
    )
    ss.connect()
    print("Connected!")
    
    # Test table
    test_table = "ALK_MARKSERIAL"
    test_sql = f"SELECT TOP (100000) * FROM {test_table} ORDER BY RECID"
    
    results = []
    
    # Test 1: fetchall()
    print("\n--- Test 1: fetchall() ---")
    cursor = ss.conn.cursor()
    cursor.execute(test_sql)
    result = test_fetchall(cursor, test_sql, "100K rows")
    results.append(result)
    cursor.close()
    print(f"  Time: {result['time']:.2f}s, Rows: {result['rows']:,}, Speed: {result['rows_per_sec']:,.0f} rows/sec")
    
    # Test 2-5: fetchmany() with different batch sizes
    batch_sizes = [1000, 5000, 10000, 20000]
    
    for batch_size in batch_sizes:
        print(f"\n--- Test: fetchmany({batch_size}) ---")
        cursor = ss.conn.cursor()
        cursor.execute(test_sql)
        result = test_fetchmany(cursor, test_sql, batch_size, f"batch={batch_size}")
        results.append(result)
        cursor.close()
        print(f"  Time: {result['time']:.2f}s, Rows: {result['rows']:,}, Speed: {result['rows_per_sec']:,.0f} rows/sec")
    
    # Print comparison table
    print("\n" + "=" * 70)
    print("COMPARISON RESULTS")
    print("=" * 70)
    print(f"{'Method':<20} {'Rows':<12} {'Time (s)':<12} {'Rows/sec':<12} {'Memory (MB)':<12}")
    print("-" * 70)
    
    for r in results:
        print(f"{r['method']:<20} {r['rows']:<12,} {r['time']:<12.2f} {r['rows_per_sec']:<12,.0f} {r['memory_mb']:<12.2f}")
    
    # Calculate improvement
    if results:
        baseline_time = results[0]['time']  # fetchall
        print(f"\n{'='*70}")
        print("IMPROVEMENT ANALYSIS")
        print(f"{'='*70}")
        for r in results[1:]:
            improvement = baseline_time / r['time'] if r['time'] > 0 else 0
            print(f"{r['method']}: {improvement:.1f}x faster than fetchall()")
    
    # Disconnect
    ss.disconnect()
    
    return results


if __name__ == "__main__":
    run_tests()
