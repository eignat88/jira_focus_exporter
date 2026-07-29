#!/usr/bin/env python3
"""
Test script for ALK_MARKSERIAL resume using ParallelLoaderV2.

This script tests the new resume mechanism on a real table.
It processes only a few chunks to verify the mechanism works.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ax_to_postgres_etl'))

import psycopg2
from loader.parallel_loader_v2 import ParallelLoaderV2
from connectors.sqlserver import SQLServerConnector
from connectors.postgres import PostgresConnector


def get_config():
    """Load ETL configuration."""
    return {
        'source': {
            'server': 'SWS-DB-T1',
            'database': 'AX63_WMS_TEST',
            'driver': 'SQL Server',
        },
        'target': {
            'host': 'localhost',
            'port': 5432,
            'database': 'wms_analysis',
            'schema': 'raw_ax',
            'user': 'postgres',
        },
        'etl': {
            'batch_size': 100000,
            'parallel': {
                'enabled': True,
                'workers': 2,  # Reduced for testing
                'fetch_size': 5000,
                'commit_size': 50000,
            },
        },
        'tables': {
            'ALK_MARKSERIAL': {
                'source_schema': 'dbo',
                'target_schema': 'raw_ax',
                'chunk_strategy': 'numeric_range',
                'chunk_column': 'RECID',
                'chunk_count': 216,  # Match existing chunks
            },
        },
        'retry': {
            'max_attempts': 3,
            'initial_delay_seconds': 2,
            'max_delay_seconds': 30,
            'backoff_multiplier': 2,
        },
        'heartbeat': {
            'interval_seconds': 30,
            'timeout_seconds': 300,
        },
    }


def check_status():
    """Check current ALK_MARKSERIAL status."""
    conn = psycopg2.connect(
        host='localhost', port=5432, dbname='wms_analysis',
        user='postgres', password='123', connect_timeout=5
    )
    cur = conn.cursor()
    
    # Row count
    cur.execute("SELECT COUNT(*) FROM raw_ax.alk_markserial")
    count = cur.fetchone()[0]
    print(f"\nCurrent rows: {count:,}")
    
    # Chunk status
    cur.execute("""
        SELECT status, COUNT(*) 
        FROM raw_ax.etl_chunk_run 
        WHERE table_name = 'ALK_MARKSERIAL' 
        GROUP BY status
    """)
    print("Chunk status:")
    for row in cur.fetchall():
        print(f"  {row[0]}: {row[1]}")
    
    conn.close()
    return count


def main():
    """Run ALK_MARKSERIAL resume test."""
    print("=" * 60)
    print("ALK_MARKSERIAL Resume Test (ParallelLoaderV2)")
    print("=" * 60)
    
    # Check initial status
    print("\n[1/4] Checking initial status...")
    initial_count = check_status()
    
    # Get configuration
    print("\n[2/4] Loading configuration...")
    config = get_config()
    print(f"  Workers: {config['etl']['parallel']['workers']}")
    print(f"  Chunk count: {config['tables']['ALK_MARKSERIAL']['chunk_count']}")
    
    # Create connectors
    print("\n[3/4] Creating connectors...")
    ss_config = config['source']
    ss_conn_str = (
        f"DRIVER={{SQL Server}};"
        f"SERVER={ss_config['server']};"
        f"DATABASE={ss_config['database']};"
        f"Trusted_Connection=yes;"
    )
    
    pg_config = config['target']
    pg_conn = psycopg2.connect(
        host=pg_config['host'],
        port=pg_config['port'],
        dbname=pg_config['database'],
        user=pg_config['user'],
        password='123',
    )
    
    # Create mock pg connector
    class MockPG:
        def __init__(self, conn, schema):
            self.conn = conn
            self.schema = schema
    
    pg_connector = MockPG(pg_conn, pg_config['schema'])
    
    # Create loader
    loader = ParallelLoaderV2(
        ss_conn_str=ss_conn_str,
        pg=pg_connector,
        config=config,
        log_func=print,
        use_new_resume=True,
    )
    
    # Run resume (limited test)
    print("\n[4/4] Starting resume test...")
    print("  Note: This will process chunks from the existing run")
    print("  Press Ctrl+C to stop early (safe - chunks will be saved)")
    
    try:
        result = loader.load_table(
            table_name='ALK_MARKSERIAL',
            load_mode='resume',
        )
        
        print(f"\n{'=' * 60}")
        print(f"Result: {result.status}")
        print(f"  Rows fetched: {result.rows_fetched:,}")
        print(f"  Rows inserted: {result.rows_inserted:,}")
        print(f"  Rows conflicted: {result.rows_conflicted:,}")
        print(f"  Chunks completed: {result.chunks_completed}/{result.chunks_total}")
        print(f"  Duration: {result.elapsed_seconds:.1f}s")
        
    except KeyboardInterrupt:
        print("\n\nInterrupted by user (safe - progress saved)")
    except Exception as e:
        print(f"\n\nError: {e}")
        import traceback
        traceback.print_exc()
    
    # Check final status
    print("\n" + "=" * 60)
    print("Final status:")
    final_count = check_status()
    
    print(f"\nRows loaded: {final_count - initial_count:,}")
    print(f"Total rows: {final_count:,}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
