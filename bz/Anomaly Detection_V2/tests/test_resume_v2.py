#!/usr/bin/env python3
"""
Integration test for ETL resume mechanism v2.

Tests:
- Run creation and management
- Chunk creation and claiming
- Chunk completion tracking
- Resume after failure
"""

import sys
sys.path.insert(0, 'ax_to_postgres_etl')

import psycopg2
from core.run_manager import RunManager
from core.chunk_manager import ChunkManager
from core.models import RunStatus, ChunkStatus
from core.strategies import NumericRangeStrategy, get_strategy
from core.retry import RetryPolicy


def test_run_manager():
    """Test RunManager operations."""
    print("Testing RunManager...")
    
    conn = psycopg2.connect(
        host='localhost', port=5432, dbname='wms_analysis',
        user='postgres', password='123', connect_timeout=5
    )
    
    rm = RunManager(conn)
    
    # Create run
    run = rm.create_run(
        pipeline_name='test',
        source_system='SQL Server',
        source_table='TEST_RESUME',
        target_schema='raw_ax',
        target_table='test_resume',
        load_mode='full',
        chunk_strategy='numeric_range',
        chunk_column='RECID',
        config={'test': True}
    )
    print(f"  Created run: {run.run_id}")
    assert run.run_id > 0
    
    # Update status
    rm.update_run_status(run.run_id, RunStatus.RUNNING)
    print(f"  Updated status to RUNNING")
    
    # Find resumable
    found = rm.find_resumable_run('TEST_RESUME', 'test_resume')
    print(f"  Found resumable: {found is not None}")
    assert found is not None
    assert found.run_id == run.run_id
    
    # Cleanup
    cursor = conn.cursor()
    cursor.execute("DELETE FROM etl.load_chunk WHERE run_id = %s", (run.run_id,))
    cursor.execute("DELETE FROM etl.load_run WHERE run_id = %s", (run.run_id,))
    conn.commit()
    conn.close()
    
    print("  RunManager tests passed!")
    return True


def test_chunk_manager():
    """Test ChunkManager operations."""
    print("\nTesting ChunkManager...")
    
    conn = psycopg2.connect(
        host='localhost', port=5432, dbname='wms_analysis',
        user='postgres', password='123', connect_timeout=5
    )
    
    rm = RunManager(conn)
    cm = ChunkManager(conn)
    
    # Create run
    run = rm.create_run(
        pipeline_name='test',
        source_system='SQL Server',
        source_table='TEST_CHUNKS',
        target_schema='raw_ax',
        target_table='test_chunks',
        load_mode='full',
        chunk_strategy='numeric_range',
        chunk_column='RECID',
    )
    
    # Create chunks
    chunks = cm.create_chunks(
        run_id=run.run_id,
        chunk_strategy='numeric_range',
        chunk_column='RECID',
        ranges=[(0, 100000), (100000, 200000), (200000, 300000)]
    )
    print(f"  Created {len(chunks)} chunks")
    assert len(chunks) == 3
    
    # Get stats
    stats = cm.get_chunk_stats(run.run_id)
    print(f"  Stats: {stats}")
    assert stats['pending'] == 3
    
    # Claim chunk
    chunk = cm.claim_chunk(run.run_id, 'test_worker')
    print(f"  Claimed chunk: {chunk.chunk_no if chunk else None}")
    assert chunk is not None
    assert chunk.status == ChunkStatus.RUNNING
    
    # Complete chunk
    cm.complete_chunk(chunk.chunk_id, rows_read=50000, rows_inserted=50000)
    print(f"  Completed chunk {chunk.chunk_no}")
    
    # Verify stats
    stats = cm.get_chunk_stats(run.run_id)
    print(f"  Stats after complete: {stats}")
    assert stats['completed'] == 1
    assert stats['pending'] == 2
    
    # Cleanup
    cursor = conn.cursor()
    cursor.execute("DELETE FROM etl.load_chunk WHERE run_id = %s", (run.run_id,))
    cursor.execute("DELETE FROM etl.load_run WHERE run_id = %s", (run.run_id,))
    conn.commit()
    conn.close()
    
    print("  ChunkManager tests passed!")
    return True


def test_strategies():
    """Test chunking strategies."""
    print("\nTesting strategies...")
    
    strategy = get_strategy('numeric_range')
    ranges = strategy.build_ranges((0, 1000000), 10)
    print(f"  NumericRange: {len(ranges)} ranges")
    assert len(ranges) == 10
    assert ranges[0] == (0, 100000)
    # Last range may exceed max_val due to integer division
    assert ranges[-1][0] == 900000
    
    strategy = get_strategy('full_table')
    ranges = strategy.build_ranges((None, None), 1)
    print(f"  FullTable: {len(ranges)} ranges")
    assert len(ranges) == 1
    
    print("  Strategy tests passed!")
    return True


def test_retry_policy():
    """Test retry policy."""
    print("\nTesting retry policy...")
    
    policy = RetryPolicy()
    
    # Test retriable error
    error = ConnectionError("connection timeout")
    assert policy.should_retry(error, 1) == True
    assert policy.is_retriable(error) == True
    
    # Test non-retriable error
    error = ValueError("column does not exist")
    assert policy.should_retry(error, 1) == False
    
    # Test attempt limit
    error = ConnectionError("connection timeout")
    assert policy.should_retry(error, 5) == False
    
    # Test delay calculation
    delay = policy.get_delay(1)
    print(f"  Delay for attempt 1: {delay}s")
    assert delay == 5.0
    
    delay = policy.get_delay(3)
    print(f"  Delay for attempt 3: {delay}s")
    assert delay == 20.0
    
    print("  Retry policy tests passed!")
    return True


def main():
    """Run all tests."""
    print("=" * 60)
    print("ETL Resume Mechanism V2 - Integration Tests")
    print("=" * 60)
    
    tests = [
        test_run_manager,
        test_chunk_manager,
        test_strategies,
        test_retry_policy,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            if test():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  FAILED: {e}")
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
