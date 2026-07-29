"""
Full load of raw_ax.alk_markserial with batch_size=100000
"""

import psycopg2
import time
import sys

# Config
BATCH_SIZE = 100000
START_RECID = "5637144576"
TARGET_TABLE = "benchmark.alk_markserial_test"

def main():
    print("=" * 70)
    print("FULL LOAD: raw_ax.alk_markserial -> benchmark.alk_markserial_test")
    print(f"Batch size: {BATCH_SIZE:,}")
    print("=" * 70)
    
    conn = psycopg2.connect(
        host='localhost', port=5432, database='wms_analysis',
        user='postgres', password='123'
    )
    conn.autocommit = False
    cur = conn.cursor()
    
    # Get total rows
    cur.execute("SELECT COUNT(*) FROM raw_ax.alk_markserial WHERE recid IS NOT NULL")
    total_rows = cur.fetchone()[0]
    print(f"Total rows: {total_rows:,}")
    
    # Truncate target
    cur.execute(f"TRUNCATE {TARGET_TABLE}")
    conn.commit()
    print(f"Truncated {TARGET_TABLE}")
    
    # Get max recid
    cur.execute("SELECT MAX(recid) FROM raw_ax.alk_markserial WHERE recid IS NOT NULL")
    max_recid = cur.fetchone()[0]
    print(f"Max recid: {max_recid}")
    
    # Calculate batches
    start_recid_int = int(START_RECID)
    max_recid_int = int(max_recid)
    total_batches = ((max_recid_int - start_recid_int) // BATCH_SIZE) + 1
    print(f"Total batches: {total_batches}")
    
    # Start loading
    start_time = time.time()
    loaded_rows = 0
    current_recid = START_RECID
    
    print("\nStarting load...")
    
    batch_num = 0
    while True:
        batch_num += 1
        batch_start_recid = str(start_recid_int + (batch_num - 1) * BATCH_SIZE)
        batch_end_recid = str(start_recid_int + batch_num * BATCH_SIZE)
        
        if int(batch_start_recid) > max_recid_int:
            break
        
        batch_start_time = time.time()
        
        try:
            cur.execute(f"""
                INSERT INTO {TARGET_TABLE}
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
            """, (batch_start_recid, batch_end_recid))
            
            inserted = cur.rowcount
            conn.commit()
            
            loaded_rows += inserted
            batch_time = time.time() - batch_start_time
            progress = (loaded_rows / total_rows) * 100 if total_rows > 0 else 0
            
            elapsed = time.time() - start_time
            speed = loaded_rows / elapsed if elapsed > 0 else 0
            
            print(f"  Batch {batch_num}/{total_batches}: +{inserted:,} rows ({progress:.1f}%) {batch_time:.1f}s | Speed: {speed:,.0f} rows/s")
            
        except Exception as e:
            conn.rollback()
            print(f"  Batch {batch_num} FAILED: {e}")
            break
    
    # Final stats
    elapsed = time.time() - start_time
    speed = loaded_rows / elapsed if elapsed > 0 else 0
    
    print("\n" + "=" * 70)
    print("LOAD COMPLETED")
    print(f"Total rows loaded: {loaded_rows:,}")
    print(f"Total time: {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"Average speed: {speed:,.0f} rows/s")
    print("=" * 70)
    
    conn.close()

if __name__ == '__main__':
    main()
