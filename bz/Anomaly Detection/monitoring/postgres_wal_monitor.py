"""
PostgreSQL WAL Monitor
Collects WAL statistics every N seconds and saves to CSV.

Usage:
    python postgres_wal_monitor.py --interval 60 --duration 8h
    python postgres_wal_monitor.py --pid 21904
"""

import argparse
import csv
import os
import time
import signal
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg2
import yaml


# Global flag for graceful shutdown
RUNNING = True


def signal_handler(signum, frame):
    global RUNNING
    print("\nStopping monitor...")
    RUNNING = False


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def load_config(config_path='wal_monitor_config.yaml'):
    """Load configuration from YAML."""
    default_config = {
        'postgres': {
            'host': 'localhost',
            'port': 5432,
            'database': 'wms_analysis',
            'user': 'postgres',
            'password': '123'
        },
        'monitor': {
            'interval_seconds': 60
        },
        'output': {
            'file': 'data/wal_history.csv'
        }
    }
    
    if os.path.exists(config_path):
        with open(config_path) as f:
            config = yaml.safe_load(f)
            # Merge with defaults
            for key in default_config:
                if key not in config:
                    config[key] = default_config[key]
                else:
                    for subkey in default_config[key]:
                        if subkey not in config[key]:
                            config[key][subkey] = default_config[key][subkey]
        return config
    return default_config


def get_connection(config):
    """Create PostgreSQL connection."""
    return psycopg2.connect(
        host=config['postgres']['host'],
        port=config['postgres']['port'],
        database=config['postgres']['database'],
        user=config['postgres']['user'],
        password=config['postgres'].get('password', '')
    )


def collect_wal_stats(conn):
    """Collect WAL statistics from pg_stat_wal."""
    cur = conn.cursor()
    cur.execute("""
        SELECT 
            wal_records,
            wal_bytes,
            wal_buffers_full
        FROM pg_stat_wal
    """)
    row = cur.fetchone()
    return {
        'wal_records': row[0],
        'wal_bytes': row[1],
        'wal_buffers_full': row[2]
    }


def collect_activity(conn, pid=None, query_filter=None):
    """Collect active queries from pg_stat_activity."""
    cur = conn.cursor()
    
    query = """
        SELECT 
            pid,
            application_name,
            state,
            wait_event_type,
            wait_event,
            clock_timestamp() - query_start AS runtime,
            LEFT(query, 500) AS query
        FROM pg_stat_activity
        WHERE state <> 'idle'
          AND pid <> pg_backend_pid()
    """
    
    if pid:
        query += f" AND pid = {pid}"
    if query_filter:
        query += f" AND query ILIKE '%{query_filter}%'"
    
    query += " ORDER BY query_start"
    
    cur.execute(query)
    activities = []
    for row in cur.fetchall():
        activities.append({
            'pid': row[0],
            'application_name': row[1],
            'state': row[2],
            'wait_event_type': row[3],
            'wait_event': row[4],
            'runtime': str(row[5]),
            'runtime_seconds': row[5].total_seconds() if row[5] else 0,
            'query': row[6]
        })
    return activities


def save_to_csv(filename, data, fieldnames):
    """Append data to CSV file."""
    file_exists = os.path.exists(filename)
    
    # Ensure directory exists
    os.makedirs(os.path.dirname(filename) if os.path.dirname(filename) else '.', exist_ok=True)
    
    with open(filename, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow(data)


def parse_duration(duration_str):
    """Parse duration string like '8h', '30m', '2h30m'."""
    total_seconds = 0
    current_num = ''
    
    for char in duration_str:
        if char.isdigit():
            current_num += char
        elif char == 'h':
            total_seconds += int(current_num) * 3600
            current_num = ''
        elif char == 'm':
            total_seconds += int(current_num) * 60
            current_num = ''
        elif char == 's':
            total_seconds += int(current_num)
            current_num = ''
    
    return total_seconds


def main():
    parser = argparse.ArgumentParser(description='PostgreSQL WAL Monitor')
    parser.add_argument('--interval', type=int, default=60, help='Collection interval in seconds')
    parser.add_argument('--duration', type=str, default=None, help='Duration (e.g., 8h, 30m)')
    parser.add_argument('--pid', type=int, default=None, help='Specific PID to monitor')
    parser.add_argument('--filter', type=str, default=None, help='Query filter pattern')
    parser.add_argument('--config', type=str, default='wal_monitor_config.yaml', help='Config file')
    args = parser.parse_args()
    
    # Load config
    config = load_config(args.config)
    config['monitor']['interval_seconds'] = args.interval
    
    # Determine end time
    if args.duration:
        duration_seconds = parse_duration(args.duration)
        end_time = datetime.now() + timedelta(seconds=duration_seconds)
    else:
        end_time = None
    
    # Setup output files
    date_str = datetime.now().strftime('%Y%m%d_%H%M%S')
    wal_file = f"data/wal_history_{date_str}.csv"
    activity_file = f"data/activity_history_{date_str}.csv"
    
    wal_fields = ['timestamp', 'wal_records', 'wal_bytes', 'wal_mb', 'wal_gb', 
                  'wal_delta_mb', 'wal_speed_mb_min', 'wal_buffers_full']
    activity_fields = ['timestamp', 'pid', 'application_name', 'state', 
                       'wait_event_type', 'wait_event', 'runtime', 'query']
    
    # Print header
    print("=" * 60)
    print("PostgreSQL WAL Monitor")
    print("=" * 60)
    print(f"Interval: {args.interval} seconds")
    if args.duration:
        print(f"Duration: {args.duration}")
    if args.pid:
        print(f"Target PID: {args.pid}")
    if args.filter:
        print(f"Query filter: {args.filter}")
    print(f"Output: {wal_file}")
    print("=" * 60)
    print()
    
    # Connect
    conn = get_connection(config)
    conn.autocommit = True
    
    prev_wal_bytes = None
    start_time = datetime.now(REPORT_TZ)
    iteration = 0
    
    try:
        while RUNNING:
            if end_time and datetime.now() >= end_time:
                print("\nDuration reached. Stopping...")
                break
            
            iteration += 1
            now = datetime.now(REPORT_TZ)
            
            # Collect WAL stats
            wal = collect_wal_stats(conn)
            # Convert to float to avoid Decimal/float division issues
            wal_bytes = float(wal['wal_bytes'])
            wal_records = int(wal['wal_records'])
            wal_buffers_full = int(wal['wal_buffers_full'])
            
            wal_gb = wal_bytes / (1024**3)
            wal_mb = wal_bytes / (1024**2)
            
            # Calculate delta and speed
            if prev_wal_bytes is not None:
                delta_bytes = wal_bytes - prev_wal_bytes
                delta_mb = delta_bytes / (1024**2)
                elapsed_minutes = float(args.interval) / 60
                speed_mb_min = float(delta_mb) / elapsed_minutes if elapsed_minutes > 0 else 0
            else:
                delta_mb = 0
                speed_mb_min = 0
            
            prev_wal_bytes = wal_bytes
            
            # Save WAL stats
            wal_data = {
                'timestamp': now.strftime('%Y-%m-%d %H:%M:%S'),
                'wal_records': wal_records,
                'wal_bytes': wal_bytes,
                'wal_mb': round(wal_mb, 2),
                'wal_gb': round(wal_gb, 2),
                'wal_delta_mb': round(delta_mb, 2),
                'wal_speed_mb_min': round(speed_mb_min, 2),
                'wal_buffers_full': wal_buffers_full
            }
            save_to_csv(wal_file, wal_data, wal_fields)
            
            # Collect activity
            activities = collect_activity(conn, args.pid, args.filter)
            
            # Save activity
            for act in activities:
                act_data = {
                    'timestamp': now.strftime('%Y-%m-%d %H:%M:%S'),
                    'pid': act['pid'],
                    'application_name': act['application_name'],
                    'state': act['state'],
                    'wait_event_type': act['wait_event_type'],
                    'wait_event': act['wait_event'],
                    'runtime': act['runtime'],
                    'query': act['query']
                }
                save_to_csv(activity_file, act_data, activity_fields)
            
            # Print status
            elapsed = (now - start_time).total_seconds() / 60
            print(f"\r[{iteration}] {now.strftime('%H:%M:%S')} | "
                  f"WAL: {wal_gb:.1f} GB | "
                  f"Delta: {delta_mb:.0f} MB | "
                  f"Speed: {speed_mb_min:.0f} MB/min | "
                  f"Active: {len(activities)}", end='')
            
            # Sleep
            time.sleep(args.interval)
    
    finally:
        conn.close()
        print(f"\n\nMonitor stopped. Data saved to:")
        print(f"  {wal_file}")
        print(f"  {activity_file}")


REPORT_TZ = ZoneInfo("Europe/Moscow")


if __name__ == '__main__':
    main()
