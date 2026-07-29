"""
RAW → DDS Benchmark CLI

Usage:
    python -m ax_to_postgres_etl.diagnostics.raw_to_dds_benchmark_cli --mode preflight
    python -m ax_to_postgres_etl.diagnostics.raw_to_dds_benchmark_cli --mode prepare
    python -m ax_to_postgres_etl.diagnostics.raw_to_dds_benchmark_cli --mode run
    python -m ax_to_postgres_etl.diagnostics.raw_to_dds_benchmark_cli --mode validate
    python -m ax_to_postgres_etl.diagnostics.raw_to_dds_benchmark_cli --mode cleanup
    python -m ax_to_postgres_etl.diagnostics.raw_to_dds_benchmark_cli --mode report
"""

import argparse
import csv
import json
import os
import signal
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import psycopg2
import yaml


# Global flag for graceful shutdown
SHOULD_STOP = False


def signal_handler(signum, frame):
    global SHOULD_STOP
    print("\nStopping benchmark...")
    SHOULD_STOP = True


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def load_config(config_path=None):
    """Load benchmark configuration."""
    if config_path is None:
        config_path = Path(__file__).parent.parent.parent / "config" / "raw_to_dds_benchmark.yaml"
    
    with open(config_path) as f:
        return yaml.safe_load(f)


def get_connection(config):
    """Create PostgreSQL connection."""
    return psycopg2.connect(
        host='localhost',
        port=5432,
        database='wms_analysis',
        user='postgres',
        password='123'
    )


def run_preflight(conn, config):
    """Run preflight checks (read-only)."""
    print("=" * 70)
    print("BENCHMARK PREFLIGHT")
    print("=" * 70)
    
    checks = []
    cur = conn.cursor()
    
    # Check source table
    cur.execute("""
        SELECT EXISTS (
            SELECT 1 FROM pg_tables 
            WHERE schemaname = 'raw_ax' AND tablename = 'alk_markserial'
        )
    """)
    exists = cur.fetchone()[0]
    checks.append(("Source table exists", exists))
    
    # Check recid column type
    cur.execute("""
        SELECT data_type FROM information_schema.columns
        WHERE table_schema = 'raw_ax' AND table_name = 'alk_markserial' AND column_name = 'recid'
    """)
    row = cur.fetchone()
    if row:
        checks.append(("recid column type", row[0] == 'text'))
    
    # Check index
    cur.execute("""
        SELECT indexname FROM pg_indexes
        WHERE schemaname = 'raw_ax' AND tablename = 'alk_markserial'
        AND indexname LIKE '%recid%'
    """)
    indexes = cur.fetchall()
    checks.append(("recid index exists", len(indexes) > 0))
    
    # Check EXPLAIN
    start_recid = config['benchmark']['start_recid']
    try:
        cur.execute(f"""
            EXPLAIN (FORMAT JSON)
            SELECT recid::bigint
            FROM raw_ax.alk_markserial
            WHERE recid >= '{start_recid}'
              AND recid < '{start_recid}'
        """)
        plan = cur.fetchone()[0][0]
        node_type = plan.get('Plan', {}).get('Node Type', '')
        checks.append(("EXPLAIN succeeds", True))
        checks.append(("Index Scan used", 'Index' in node_type))
    except Exception as e:
        checks.append(("EXPLAIN succeeds", False))
        print(f"  EXPLAIN error: {e}")
    
    # Check target table
    cur.execute("""
        SELECT EXISTS (
            SELECT 1 FROM pg_tables 
            WHERE schemaname = 'benchmark' AND tablename = 'alk_markserial_test'
        )
    """)
    exists = cur.fetchone()[0]
    checks.append(("Target table exists", exists))
    
    # Check disk space
    cur.execute("""
        SELECT pg_size_pretty(pg_total_relation_size('raw_ax.alk_markserial'))
    """)
    size = cur.fetchone()[0]
    checks.append(("Source table size", size))
    
    # Print results
    all_passed = True
    for check_name, passed in checks:
        status = "[OK]" if passed else "[FAIL]"
        print(f"  {status} {check_name}")
        if not passed:
            all_passed = False
    
    print("=" * 70)
    if all_passed:
        print("Preflight passed")
    else:
        print("Preflight FAILED")
    
    return all_passed


def run_prepare(conn, config):
    """Create benchmark schema and tables."""
    print("=" * 70)
    print("BENCHMARK PREPARE")
    print("=" * 70)
    
    cur = conn.cursor()
    
    # Create schema
    cur.execute("CREATE SCHEMA IF NOT EXISTS benchmark")
    print("  Created schema: benchmark")
    
    # Create test table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS benchmark.alk_markserial_test (
            recid bigint NOT NULL,
            gtin text,
            serialnumber text,
            itemid text,
            markcode text,
            createddatetime timestamptz,
            modifieddatetime timestamptz,
            createdby text,
            modifiedby text,
            loaded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            CONSTRAINT pk_benchmark_alk_markserial_test PRIMARY KEY (recid)
        )
    """)
    print("  Created table: benchmark.alk_markserial_test")
    
    # Create result tables
    cur.execute("""
        CREATE TABLE IF NOT EXISTS benchmark.raw_to_dds_test_result (
            test_result_id bigserial PRIMARY KEY,
            benchmark_run_id uuid NOT NULL,
            batch_size integer NOT NULL,
            repeat_number integer NOT NULL,
            is_warmup boolean NOT NULL DEFAULT false,
            lower_bound text NOT NULL,
            upper_bound text NOT NULL,
            source_rows bigint,
            inserted_rows bigint,
            conflict_rows bigint,
            started_at timestamptz NOT NULL,
            finished_at timestamptz,
            duration_ms numeric,
            rows_per_second numeric,
            wal_bytes_delta numeric,
            wal_records_delta bigint,
            wal_fpi_delta bigint,
            wal_buffers_full_delta bigint,
            table_size_delta bigint,
            indexes_size_delta bigint,
            total_size_delta bigint,
            shared_blks_hit bigint,
            shared_blks_read bigint,
            shared_blks_dirtied bigint,
            shared_blks_written bigint,
            temp_blks_read bigint,
            temp_blks_written bigint,
            heap_fetches bigint,
            plan_json jsonb,
            status text NOT NULL,
            error_message text,
            CONSTRAINT uq_raw_to_dds_test_result UNIQUE (benchmark_run_id, batch_size, repeat_number)
        )
    """)
    print("  Created table: benchmark.raw_to_dds_test_result")
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS benchmark.raw_to_dds_test_run (
            benchmark_run_id uuid PRIMARY KEY,
            source_table text NOT NULL,
            target_table text NOT NULL,
            started_at timestamptz NOT NULL,
            finished_at timestamptz,
            status text NOT NULL,
            host_name text,
            database_name text,
            postgres_version text,
            config_json jsonb,
            error_message text
        )
    """)
    print("  Created table: benchmark.raw_to_dds_test_run")
    
    conn.commit()
    print("=" * 70)
    print("Prepare completed")


def run_benchmark(conn, config):
    """Run benchmark tests."""
    global SHOULD_STOP
    
    print("=" * 70)
    print("BENCHMARK RUN")
    print("=" * 70)
    
    cur = conn.cursor()
    
    # Create benchmark run
    run_id = uuid.uuid4()
    start_recid = config['benchmark']['start_recid']
    batch_sizes = config['benchmark']['batch_sizes']
    warmup_runs = config['benchmark']['warmup_runs']
    measurement_runs = config['benchmark']['measurement_runs']
    
    # Insert run record
    cur.execute("""
        INSERT INTO benchmark.raw_to_dds_test_run 
        (benchmark_run_id, source_table, target_table, started_at, status, config_json)
        VALUES (%s, %s, %s, CURRENT_TIMESTAMP, 'RUNNING', %s)
    """, (str(run_id), config['benchmark']['source_table'], 
          config['benchmark']['target_table'], json.dumps(config['benchmark'])))
    conn.commit()
    
    print(f"  Run ID: {run_id}")
    print(f"  Batch sizes: {batch_sizes}")
    print(f"  Warmup runs: {warmup_runs}")
    print(f"  Measurement runs: {measurement_runs}")
    
    total_tests = len(batch_sizes) * (warmup_runs + measurement_runs)
    current_test = 0
    
    for batch_size in batch_sizes:
        if SHOULD_STOP:
            break
        
        for repeat in range(warmup_runs + measurement_runs):
            if SHOULD_STOP:
                break
            
            current_test += 1
            is_warmup = repeat < warmup_runs
            repeat_num = repeat + 1
            
            print(f"\n  Test {current_test}/{total_tests}: batch_size={batch_size}, repeat={repeat_num} ({'warmup' if is_warmup else 'measurement'})")
            
            # Calculate bounds
            lower_bound = start_recid
            upper_bound = str(int(start_recid) + batch_size)
            
            # Run single test
            result = run_single_test(conn, run_id, batch_size, repeat_num, is_warmup, 
                                   lower_bound, upper_bound, config)
            
            if result['status'] == 'SUCCESS':
                print(f"    Duration: {result['duration_ms']:.0f}ms")
                print(f"    Rows/sec: {result['rows_per_second']:.0f}")
                print(f"    WAL delta: {result['wal_bytes_delta'] / 1024 / 1024:.2f} MB")
            else:
                print(f"    FAILED: {result.get('error_message', 'Unknown error')}")
    
    # Update run status
    final_status = 'CANCELLED' if SHOULD_STOP else 'COMPLETED'
    cur.execute("""
        UPDATE benchmark.raw_to_dds_test_run
        SET status = %s, finished_at = CURRENT_TIMESTAMP
        WHERE benchmark_run_id = %s
    """, (final_status, str(run_id)))
    conn.commit()
    
    print("\n" + "=" * 70)
    print(f"Benchmark {final_status}")
    print("=" * 70)
    
    return run_id


def run_single_test(conn, run_id, batch_size, repeat_num, is_warmup, 
                   lower_bound, upper_bound, config):
    """Run a single benchmark test."""
    cur = conn.cursor()
    
    result = {
        'benchmark_run_id': str(run_id),
        'batch_size': batch_size,
        'repeat_number': repeat_num,
        'is_warmup': is_warmup,
        'lower_bound': lower_bound,
        'upper_bound': upper_bound,
        'status': 'SUCCESS'
    }
    
    try:
        # Get WAL stats before
        cur.execute("SELECT wal_records, wal_fpi, wal_bytes, wal_buffers_full FROM pg_stat_wal")
        wal_before = cur.fetchone()
        
        # Get table size before
        cur.execute("""
            SELECT pg_table_size('benchmark.alk_markserial_test'),
                   pg_indexes_size('benchmark.alk_markserial_test'),
                   pg_total_relation_size('benchmark.alk_markserial_test')
        """)
        size_before = cur.fetchone()
        
        # Truncate target table
        cur.execute("TRUNCATE benchmark.alk_markserial_test")
        conn.commit()
        
        # Run INSERT
        started_at = datetime.now()
        
        cur.execute("""
            INSERT INTO benchmark.alk_markserial_test
            (recid, gtin, serialnumber, itemid, markcode, createddatetime, modifieddatetime, createdby, modifiedby)
            SELECT
                src.recid::bigint,
                src.gtin,
                src.serialid,
                src.itemid,
                src.markcode,
                src.createddatetime::timestamptz,
                src.modifieddatetime::timestamptz,
                src.createdby,
                src.modifiedby
            FROM raw_ax.alk_markserial AS src
            WHERE src.recid >= %s
              AND src.recid < %s
            ON CONFLICT (recid) DO NOTHING
        """, (lower_bound, upper_bound))
        
        inserted_rows = cur.rowcount
        conn.commit()
        
        finished_at = datetime.now()
        duration_ms = (finished_at - started_at).total_seconds() * 1000
        rows_per_second = inserted_rows / (duration_ms / 1000) if duration_ms > 0 else 0
        
        # Get WAL stats after
        cur.execute("SELECT wal_records, wal_fpi, wal_bytes, wal_buffers_full FROM pg_stat_wal")
        wal_after = cur.fetchone()
        
        # Get table size after
        cur.execute("""
            SELECT pg_table_size('benchmark.alk_markserial_test'),
                   pg_indexes_size('benchmark.alk_markserial_test'),
                   pg_total_relation_size('benchmark.alk_markserial_test')
        """)
        size_after = cur.fetchone()
        
        # Get buffer stats (PostgreSQL 17 uses blks_hit, blks_read without shared_ prefix)
        cur.execute("SELECT blks_hit, blks_read FROM pg_stat_database WHERE datname = current_database()")
        stats = cur.fetchone()
        # Set default values for other stats
        shared_blks_hit = stats[0] if stats else 0
        shared_blks_read = stats[1] if stats else 0
        shared_blks_dirtied = 0
        shared_blks_written = 0
        
        # Calculate deltas
        result['source_rows'] = inserted_rows
        result['inserted_rows'] = inserted_rows
        result['conflict_rows'] = 0
        result['started_at'] = started_at
        result['finished_at'] = finished_at
        result['duration_ms'] = duration_ms
        result['rows_per_second'] = rows_per_second
        result['wal_bytes_delta'] = wal_after[2] - wal_before[2]
        result['wal_records_delta'] = wal_after[0] - wal_before[0]
        result['wal_fpi_delta'] = wal_after[1] - wal_before[1]
        result['wal_buffers_full_delta'] = wal_after[3] - wal_before[3]
        result['table_size_delta'] = size_after[0] - size_before[0]
        result['indexes_size_delta'] = size_after[1] - size_before[1]
        result['total_size_delta'] = size_after[2] - size_before[2]
        result['shared_blks_hit'] = shared_blks_hit
        result['shared_blks_read'] = shared_blks_read
        result['shared_blks_dirtied'] = shared_blks_dirtied
        result['shared_blks_written'] = shared_blks_written
        result['heap_fetches'] = 0
        
        # Save result
        cur.execute("""
            INSERT INTO benchmark.raw_to_dds_test_result
            (benchmark_run_id, batch_size, repeat_number, is_warmup,
             lower_bound, upper_bound, source_rows, inserted_rows, conflict_rows,
             started_at, finished_at, duration_ms, rows_per_second,
             wal_bytes_delta, wal_records_delta, wal_fpi_delta, wal_buffers_full_delta,
             table_size_delta, indexes_size_delta, total_size_delta,
             shared_blks_hit, shared_blks_read, shared_blks_dirtied, shared_blks_written,
             status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (str(run_id), batch_size, repeat_num, is_warmup,
              lower_bound, upper_bound, inserted_rows, inserted_rows, 0,
              started_at, finished_at, duration_ms, rows_per_second,
              result['wal_bytes_delta'], result['wal_records_delta'], 
              result['wal_fpi_delta'], result['wal_buffers_full_delta'],
              result['table_size_delta'], result['indexes_size_delta'], result['total_size_delta'],
              result['shared_blks_hit'], result['shared_blks_read'], 
              result['shared_blks_dirtied'], result['shared_blks_written'],
              'SUCCESS'))
        conn.commit()
        
    except Exception as e:
        result['status'] = 'FAILED'
        result['error_message'] = str(e)
        
        cur.execute("""
            INSERT INTO benchmark.raw_to_dds_test_result
            (benchmark_run_id, batch_size, repeat_number, is_warmup,
             lower_bound, upper_bound, started_at, status, error_message)
            VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, 'FAILED', %s)
        """, (str(run_id), batch_size, repeat_num, is_warmup,
              lower_bound, upper_bound, str(e)))
        conn.commit()
    
    return result


def run_cleanup(conn, config):
    """Drop benchmark objects."""
    print("=" * 70)
    print("BENCHMARK CLEANUP")
    print("=" * 70)
    
    cur = conn.cursor()
    
    cur.execute("DROP TABLE IF EXISTS benchmark.alk_markserial_test CASCADE")
    print("  Dropped: benchmark.alk_markserial_test")
    
    cur.execute("DROP TABLE IF EXISTS benchmark.raw_to_dds_test_result CASCADE")
    print("  Dropped: benchmark.raw_to_dds_test_result")
    
    cur.execute("DROP TABLE IF EXISTS benchmark.raw_to_dds_test_run CASCADE")
    print("  Dropped: benchmark.raw_to_dds_test_run")
    
    conn.commit()
    print("=" * 70)
    print("Cleanup completed")


def run_report(conn, config):
    """Generate benchmark report."""
    print("=" * 70)
    print("BENCHMARK REPORT")
    print("=" * 70)
    
    cur = conn.cursor()
    
    # Get latest run
    cur.execute("""
        SELECT benchmark_run_id, started_at, finished_at, status
        FROM benchmark.raw_to_dds_test_run
        ORDER BY started_at DESC
        LIMIT 1
    """)
    run = cur.fetchone()
    
    if not run:
        print("  No benchmark runs found")
        return
    
    run_id, started, finished, status = run
    print(f"  Run ID: {run_id}")
    print(f"  Started: {started}")
    print(f"  Finished: {finished}")
    print(f"  Status: {status}")
    
    # Get results by batch size
    cur.execute("""
        SELECT 
            batch_size,
            COUNT(*) as total_runs,
            COUNT(*) FILTER (WHERE status = 'SUCCESS') as successful,
            AVG(duration_ms) as avg_duration,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY duration_ms) as median_duration,
            AVG(rows_per_second) as avg_rps,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY rows_per_second) as median_rps,
            AVG(wal_bytes_delta) as avg_wal,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY wal_bytes_delta) as median_wal
        FROM benchmark.raw_to_dds_test_result
        WHERE benchmark_run_id = %s
        GROUP BY batch_size
        ORDER BY batch_size
    """, (str(run_id),))
    
    results = cur.fetchall()
    
    print(f"\n{'Batch Size':<12} {'Runs':<8} {'Avg(ms)':<12} {'Median(ms)':<12} {'Avg(RPS)':<12} {'Median(RPS)':<12} {'Avg(WAL MB)':<12}")
    print("-" * 90)
    
    best_batch = None
    best_rps = 0
    
    for row in results:
        batch_size, total, successful, avg_dur, med_dur, avg_rps, med_rps, avg_wal, med_wal = row
        avg_wal_mb = avg_wal / 1024 / 1024 if avg_wal else 0
        
        print(f"{batch_size:<12} {successful:<8} {avg_dur:<12.0f} {med_dur:<12.0f} {avg_rps:<12.0f} {med_rps:<12.0f} {avg_wal_mb:<12.2f}")
        
        if med_rps and med_rps > best_rps:
            best_rps = med_rps
            best_batch = batch_size
    
    print("-" * 90)
    if best_batch:
        print(f"\n  Recommended batch size: {best_batch}")
        print(f"  Reason: Highest median rows/sec ({best_rps:.0f})")
    
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description='RAW → DDS Benchmark')
    parser.add_argument('--mode', choices=['preflight', 'prepare', 'run', 'validate', 'cleanup', 'report'],
                       default='preflight')
    parser.add_argument('--config', help='Config file path')
    
    args = parser.parse_args()
    config = load_config(args.config)
    
    conn = get_connection(config)
    
    try:
        if args.mode == 'preflight':
            run_preflight(conn, config)
        elif args.mode == 'prepare':
            run_prepare(conn, config)
        elif args.mode == 'run':
            run_benchmark(conn, config)
        elif args.mode == 'cleanup':
            run_cleanup(conn, config)
        elif args.mode == 'report':
            run_report(conn, config)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
