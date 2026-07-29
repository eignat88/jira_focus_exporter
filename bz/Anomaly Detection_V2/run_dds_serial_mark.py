"""
Load dds.serial_mark from benchmark.alk_markserial_test (normalized staging).

Strategy: NORMALIZED_STAGING
  raw_ax.alk_markserial (text recid, 153M)
    → benchmark.alk_markserial_test (bigint recid)
    → dds.serial_mark

Usage:
    python run_dds_serial_mark.py                         # Full pipeline
    python run_dds_serial_mark.py --resume-remaining      # Load remaining rows to benchmark
    python run_dds_serial_mark.py --dds-only              # Load benchmark → dds only
    python run_dds_serial_mark.py --resume-from 5757444576  # Resume from specific recid
    python run_dds_serial_mark.py --batch-size 500000
"""

import os
import sys
import time
import argparse
from datetime import datetime

import psycopg2
from dotenv import load_dotenv

load_dotenv()

BATCH_SIZE_DEFAULT = 250000


def get_conn():
    return psycopg2.connect(
        host="localhost",
        port=5432,
        database="wms_analysis",
        user="postgres",
        password=os.getenv("DB_PASSWORD"),
    )


def get_estimate(conn, schema, table):
    """Get row count estimate from pg_stat."""
    cur = conn.cursor()
    cur.execute(
        "SELECT n_live_tup FROM pg_stat_user_tables "
        "WHERE schemaname = %s AND relname = %s",
        (schema, table),
    )
    row = cur.fetchone()
    return row[0] if row else 0


def get_max_recid(conn, schema, table, column="recid"):
    """Get max recid from table."""
    cur = conn.cursor()
    cur.execute(f"SELECT COALESCE(MAX({column}), 0) FROM {schema}.{table}")
    return cur.fetchone()[0]


def load_remaining_to_benchmark(conn, batch_size, resume_from=None):
    """Load remaining rows from raw_ax to benchmark.alk_markserial_test.

    Uses keyset pagination with recid_bigint for efficient index scan.
    Handles text→timestamptz conversion with NULLIF(BTRIM(...), '').
    """
    print("\n" + "=" * 70)
    print("STEP 1: Load remaining rows to benchmark.alk_markserial_test")
    print("=" * 70)

    bench_count = get_estimate(conn, "benchmark", "alk_markserial_test")
    print(f"  benchmark: {bench_count:,} rows")

    # Determine starting point
    if resume_from is not None:
        last_recid = resume_from
        print(f"  Resume from: {last_recid}")
    else:
        last_recid = get_max_recid(conn, "benchmark", "alk_markserial_test", "recid")
        print(f"  Max recid in benchmark: {last_recid}")

    cur = conn.cursor()
    start_time = time.time()
    total_loaded = 0

    print(f"\n  Loading in batches of {batch_size:,}...")
    print(f"  Using keyset pagination: recid_bigint > {last_recid}")

    while True:
        batch_start = time.time()

        # Keyset pagination with index scan on recid_bigint
        # Uses NULLIF(BTRIM(...), '')::timestamptz for safe type conversion
        cur.execute("""
            INSERT INTO benchmark.alk_markserial_test (
                recid, gtin, serialnumber, itemid, markcode,
                createddatetime, modifieddatetime, createdby, modifiedby, loaded_at
            )
            SELECT
                r.recid_bigint,
                r.gtin,
                r.serialid,
                r.itemid,
                r.markcode,
                NULLIF(BTRIM(r.createddatetime), '')::timestamptz,
                NULLIF(BTRIM(r.modifieddatetime), '')::timestamptz,
                r.createdby,
                r.modifiedby,
                now()
            FROM raw_ax.alk_markserial r
            WHERE r.recid_bigint > %s
              AND r.recid_bigint IS NOT NULL
            ORDER BY r.recid_bigint
            LIMIT %s
            ON CONFLICT (recid) DO NOTHING
        """, (last_recid, batch_size))

        loaded = cur.rowcount
        conn.commit()

        if loaded == 0:
            print(f"\n  All rows loaded. Total: {total_loaded:,}")
            break

        total_loaded += loaded

        # Update last_recid for next batch
        cur.execute("SELECT MAX(recid) FROM benchmark.alk_markserial_test")
        last_recid = cur.fetchone()[0]

        elapsed = time.time() - batch_start
        speed = loaded / elapsed if elapsed > 0 else 0

        print(f"  +{loaded:>10,} rows ({speed:,.0f}/s) | total: {total_loaded:,} | last_recid: {last_recid}")

    total_elapsed = time.time() - start_time
    final_count = get_estimate(conn, "benchmark", "alk_markserial_test")
    print(f"\n  Completed in {total_elapsed:.0f}s")
    print(f"  Final benchmark count: {final_count:,}")


def load_benchmark_to_dds(conn, batch_size, truncate=False):
    """Load dds.serial_mark from benchmark.alk_markserial_test.

    Uses keyset pagination with recid for efficient index scan.
    ON CONFLICT DO NOTHING for idempotency.
    """
    print("\n" + "=" * 70)
    print("STEP 2: Load benchmark → dds.serial_mark")
    print("=" * 70)

    bench_count = get_estimate(conn, "benchmark", "alk_markserial_test")
    print(f"  Source: benchmark.alk_markserial_test = {bench_count:,} rows")

    cur = conn.cursor()

    if truncate:
        print("  Truncating dds.serial_mark...")
        cur.execute("TRUNCATE TABLE dds.serial_mark RESTART IDENTITY")
        conn.commit()

    # Get starting point
    last_recid = get_max_recid(conn, "dds", "serial_mark", "rec_id")
    print(f"  Max rec_id in dds: {last_recid}")

    start_time = time.time()
    total_loaded = 0

    print(f"\n  Loading in batches of {batch_size:,}...")

    while True:
        batch_start = time.time()

        cur.execute("""
            INSERT INTO dds.serial_mark (
                rec_id, gtin, serial_number, item_id,
                mark_code,
                modified_datetime, modified_by,
                created_datetime, created_by
            )
            SELECT
                b.recid,
                b.gtin,
                b.serialnumber,
                b.itemid,
                b.markcode,
                b.modifieddatetime,
                b.modifiedby,
                b.createddatetime,
                b.createdby
            FROM benchmark.alk_markserial_test b
            WHERE b.recid > %s
            ORDER BY b.recid
            LIMIT %s
            ON CONFLICT (rec_id) DO NOTHING
        """, (last_recid, batch_size))

        loaded = cur.rowcount
        conn.commit()

        if loaded == 0:
            print(f"\n  All rows loaded. Total: {total_loaded:,}")
            break

        total_loaded += loaded

        # Update last_recid for next batch
        cur.execute("SELECT MAX(rec_id) FROM dds.serial_mark")
        last_recid = cur.fetchone()[0]

        elapsed = time.time() - batch_start
        speed = loaded / elapsed if elapsed > 0 else 0

        print(f"  +{loaded:>10,} rows ({speed:,.0f}/s) | total: {total_loaded:,} | last_recid: {last_recid}")

    total_elapsed = time.time() - start_time
    final_count = get_estimate(conn, "dds", "serial_mark")
    print(f"\n  Completed in {total_elapsed:.0f}s")
    print(f"  Final dds.serial_mark count: {final_count:,}")

    # Log to etl.load_log
    cur.execute("""
        INSERT INTO etl.load_log (table_name, operation, rows_loaded, message)
        VALUES (%s, %s, %s, %s)
    """, ("dds.serial_mark", "STAGE_DONE", final_count, "Loaded from benchmark.alk_markserial_test"))
    conn.commit()


def validate(conn):
    """Validate DDS load."""
    print("\n" + "=" * 70)
    print("STEP 3: Validation")
    print("=" * 70)

    cur = conn.cursor()

    # Source count
    bench_count = get_estimate(conn, "benchmark", "alk_markserial_test")

    # Target count
    cur.execute("SELECT COUNT(*) FROM dds.serial_mark")
    dds_count = cur.fetchone()[0]

    # Null check
    cur.execute("SELECT COUNT(*) FROM dds.serial_mark WHERE rec_id IS NULL")
    null_count = cur.fetchone()[0]

    # Duplicate check
    cur.execute("""
        SELECT COUNT(*) FROM (
            SELECT rec_id, COUNT(*) c
            FROM dds.serial_mark
            GROUP BY rec_id
            HAVING COUNT(*) > 1
        ) t
    """)
    dup_count = cur.fetchone()[0]

    print(f"  Source (benchmark): {bench_count:,}")
    print(f"  Target (dds):      {dds_count:,}")
    print(f"  NULL rec_id:       {null_count:,}")
    print(f"  Duplicates:        {dup_count:,}")

    match = bench_count == dds_count
    print(f"\n  Match: {'YES' if match else 'NO'}")

    if not match:
        diff = bench_count - dds_count
        print(f"  Difference: {diff:,} rows")


def test_conversion(conn):
    """Test text→timestamptz conversion on sample data."""
    print("\n" + "=" * 70)
    print("TEST: Conversion check")
    print("=" * 70)

    cur = conn.cursor()
    cur.execute("""
        SELECT
            recid,
            createddatetime AS raw_created,
            NULLIF(BTRIM(createddatetime), '')::timestamptz AS converted_created,
            modifieddatetime AS raw_modified,
            NULLIF(BTRIM(modifieddatetime), '')::timestamptz AS converted_modified
        FROM raw_ax.alk_markserial
        WHERE recid_bigint IS NOT NULL
        LIMIT 10
    """)
    rows = cur.fetchall()

    print(f"  {'recid':<15} {'raw_created':<25} {'converted':<25} {'raw_modified':<25} {'converted':<25}")
    print(f"  {'-'*15} {'-'*25} {'-'*25} {'-'*25} {'-'*25}")
    for r in rows:
        print(f"  {str(r[0]):<15} {str(r[1])[:24]:<25} {str(r[2])[:24]:<25} {str(r[3])[:24]:<25} {str(r[4])[:24]:<25}")

    # Check for problematic values
    cur.execute("""
        SELECT COUNT(*) FROM raw_ax.alk_markserial
        WHERE recid_bigint IS NOT NULL
          AND (createddatetime IS NOT NULL AND createddatetime != '')
          AND NULLIF(BTRIM(createddatetime), '')::timestamptz IS NULL
    """)
    bad_count = cur.fetchone()[0]
    print(f"\n  Rows with unconvertible createddatetime: {bad_count:,}")


def test_chunk(conn, start_recid, end_recid):
    """Test loading a single chunk."""
    print("\n" + "=" * 70)
    print(f"TEST: Load chunk [{start_recid}, {end_recid})")
    print("=" * 70)

    cur = conn.cursor()

    # Count rows in range
    cur.execute("""
        SELECT COUNT(*) FROM raw_ax.alk_markserial
        WHERE recid_bigint > %s AND recid_bigint <= %s
    """, (start_recid, end_recid))
    range_count = cur.fetchone()[0]
    print(f"  Rows in range: {range_count:,}")

    # Load chunk
    start_time = time.time()
    cur.execute("""
        INSERT INTO benchmark.alk_markserial_test (
            recid, gtin, serialnumber, itemid, markcode,
            createddatetime, modifieddatetime, createdby, modifiedby, loaded_at
        )
        SELECT
            r.recid_bigint,
            r.gtin,
            r.serialid,
            r.itemid,
            r.markcode,
            NULLIF(BTRIM(r.createddatetime), '')::timestamptz,
            NULLIF(BTRIM(r.modifieddatetime), '')::timestamptz,
            r.createdby,
            r.modifiedby,
            now()
        FROM raw_ax.alk_markserial r
        WHERE r.recid_bigint > %s
          AND r.recid_bigint <= %s
          AND r.recid_bigint IS NOT NULL
        ON CONFLICT (recid) DO NOTHING
    """, (start_recid, end_recid))

    loaded = cur.rowcount
    conn.commit()
    elapsed = time.time() - start_time

    print(f"  Loaded: {loaded:,} rows in {elapsed:.1f}s")
    print(f"  Speed: {loaded/elapsed:,.0f} rows/s" if elapsed > 0 else "  Speed: instant")

    # Verify no duplicates
    cur.execute("""
        SELECT COUNT(*) FROM benchmark.alk_markserial_test
        WHERE recid > %s AND recid <= %s
    """, (start_recid, end_recid))
    verify_count = cur.fetchone()[0]
    print(f"  Verified in benchmark: {verify_count:,} rows")


def main():
    parser = argparse.ArgumentParser(description="Load dds.serial_mark from benchmark")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE_DEFAULT)
    parser.add_argument("--resume-remaining", action="store_true",
                       help="Only load remaining rows to benchmark")
    parser.add_argument("--resume-from", type=int, default=None,
                       help="Resume from specific recid_bigint")
    parser.add_argument("--dds-only", action="store_true",
                       help="Only load benchmark → dds (skip benchmark loading)")
    parser.add_argument("--truncate", action="store_true",
                       help="Truncate dds.serial_mark before loading")
    parser.add_argument("--validate-only", action="store_true",
                       help="Only validate, don't load")
    parser.add_argument("--test-conversion", action="store_true",
                       help="Test text→timestamptz conversion")
    parser.add_argument("--test-chunk", nargs=2, type=int, metavar=("START", "END"),
                       help="Test loading a single chunk [START, END)")
    args = parser.parse_args()

    print("=" * 70)
    print("DDS SERIAL_MARK LOADER")
    print(f"Strategy: NORMALIZED_STAGING")
    print(f"Batch size: {args.batch_size:,}")
    print(f"Time: {datetime.now().isoformat()}")
    print("=" * 70)

    conn = get_conn()

    try:
        if args.test_conversion:
            test_conversion(conn)
            return

        if args.test_chunk:
            test_chunk(conn, args.test_chunk[0], args.test_chunk[1])
            return

        if args.validate_only:
            validate(conn)
            return

        if not args.dds_only:
            load_remaining_to_benchmark(conn, args.batch_size, resume_from=args.resume_from)

        if not args.resume_remaining:
            load_benchmark_to_dds(conn, args.batch_size, truncate=args.truncate)
            validate(conn)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
