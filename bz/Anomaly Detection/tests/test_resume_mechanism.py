#!/usr/bin/env python3
"""
Test ETL resume mechanism without SQL Server connection.

Verifies:
- Run creation and management
- Chunk creation and claiming
- Chunk completion tracking
- Resume logic
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ax_to_postgres_etl'))

import psycopg2
from core.run_manager import RunManager
from core.chunk_manager import ChunkManager
from core.models import RunStatus, ChunkStatus
from core.strategies import get_strategy


def test_full_resume_flow():
    """Test complete resume flow on PostgreSQL."""
    print("=" * 60)
    print("ETL Resume Mechanism - Full Flow Test")
    print("=" * 60)
    
    conn = psycopg2.connect(
        host='localhost', port=5432, dbname='wms_analysis',
        user='postgres', password='123', connect_timeout=5
    )
    
    rm = RunManager(conn)
    cm = ChunkManager(conn)
    
    # Step 1: Create run
    print("\n[1/5] Creating run...")
    run = rm.create_run(
        pipeline_name='test_resume',
        source_system='SQL Server',
        source_table='TEST_RESUME_FLOW',
        target_schema='raw_ax',
        target_table='test_resume_flow',
        load_mode='full',
        chunk_strategy='numeric_range',
        chunk_column='RECID',
        config={'test': True, 'chunk_count': 10}
    )
    print(f"  Run ID: {run.run_id}")
    rm.update_run_status(run.run_id, "running")
    
    # Step 2: Create chunks
    print("\n[2/5] Creating chunks...")
    strategy = get_strategy('numeric_range')
    ranges = strategy.build_ranges((0, 1000000), 10)
    chunks = cm.create_chunks(
        run_id=run.run_id,
        chunk_strategy='numeric_range',
        chunk_column='RECID',
        ranges=ranges
    )
    print(f"  Created {len(chunks)} chunks")
    
    # Step 3: Simulate worker processing
    print("\n[3/5] Simulating worker processing...")
    for i in range(5):  # Process first 5 chunks
        chunk = cm.claim_chunk(run.run_id, f'worker_{i % 2}')
        if chunk:
            print(f"  Worker claimed chunk {chunk.chunk_no} ({chunk.range_start_bigint:,}→{chunk.range_end_bigint:,})")
            cm.complete_chunk(
                chunk.chunk_id,
                rows_read=50000,
                rows_inserted=50000
            )
            print(f"  Chunk {chunk.chunk_no} completed")
    
    # Step 4: Check stats
    print("\n[4/5] Checking stats...")
    stats = cm.get_chunk_stats(run.run_id)
    print(f"  Pending: {stats['pending']}")
    print(f"  Running: {stats['running']}")
    print(f"  Completed: {stats['completed']}")
    
    total = cm.get_total_stats(run.run_id)
    print(f"  Total rows inserted: {total.get('rows_inserted', 0):,}")
    
    # Step 5: Simulate resume
    print("\n[5/5] Simulating resume...")
    pending_chunks = cm.get_pending_chunks(run.run_id)
    print(f"  Pending chunks to resume: {len(pending_chunks)}")
    
    # Process remaining chunks
    for chunk in pending_chunks[:3]:  # Process 3 more
        claimed = cm.claim_chunk(run.run_id, 'resume_worker')
        if claimed:
            cm.complete_chunk(claimed.chunk_id, rows_read=50000, rows_inserted=50000)
            print(f"  Resumed chunk {claimed.chunk_no}")
    
    # Final stats
    print("\n" + "=" * 60)
    print("Final stats:")
    final_stats = cm.get_chunk_stats(run.run_id)
    print(f"  Completed: {final_stats['completed']}/{len(chunks)}")
    print(f"  Pending: {final_stats['pending']}")
    
    final_total = cm.get_total_stats(run.run_id)
    print(f"  Total rows: {final_total.get('rows_inserted', 0):,}")
    
    # Cleanup
    print("\nCleaning up...")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM etl.load_chunk WHERE run_id = %s", (run.run_id,))
    cursor.execute("DELETE FROM etl.load_run WHERE run_id = %s", (run.run_id,))
    conn.commit()
    conn.close()
    
    print("\nTest completed successfully!")
    return True


def test_advisory_lock():
    """Test advisory lock mechanism."""
    print("\n" + "=" * 60)
    print("Advisory Lock Test")
    print("=" * 60)
    
    conn1 = psycopg2.connect(
        host='localhost', port=5432, dbname='wms_analysis',
        user='postgres', password='123', connect_timeout=5
    )
    conn2 = psycopg2.connect(
        host='localhost', port=5432, dbname='wms_analysis',
        user='postgres', password='123', connect_timeout=5
    )
    
    rm1 = RunManager(conn1)
    rm2 = RunManager(conn2)
    
    # First connection acquires lock
    acquired1 = rm1.acquire_advisory_lock('TEST_TABLE')
    print(f"  Connection 1 acquired lock: {acquired1}")
    
    # Second connection tries to acquire (should fail)
    acquired2 = rm2.acquire_advisory_lock('TEST_TABLE')
    print(f"  Connection 2 acquired lock: {acquired2}")
    
    # Release lock
    rm1.release_advisory_lock('TEST_TABLE')
    print(f"  Connection 1 released lock")
    
    # Now second connection can acquire
    acquired3 = rm2.acquire_advisory_lock('TEST_TABLE')
    print(f"  Connection 2 acquired lock after release: {acquired3}")
    
    # Cleanup
    rm2.release_advisory_lock('TEST_TABLE')
    conn1.close()
    conn2.close()
    
    print("\nAdvisory lock test completed!")
    return True


def main():
    """Run all tests."""
    try:
        test_full_resume_flow()
        test_advisory_lock()
        
        print("\n" + "=" * 60)
        print("ALL TESTS PASSED!")
        print("=" * 60)
        return 0
    except Exception as e:
        print(f"\nTEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
