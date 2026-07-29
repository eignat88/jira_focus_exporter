"""WAL (Write-Ahead Log) monitoring collector."""

import psycopg2
from datetime import datetime
from typing import Optional
import pandas as pd


class WALMonitor:
    """Collects WAL statistics from PostgreSQL."""
    
    def __init__(self, conn_params: dict):
        self.conn_params = conn_params
        self.history = []
        self.initial_wal = None
        self.start_time = None
        
    def get_connection(self):
        return psycopg2.connect(**self.conn_params)
    
    def collect(self) -> dict:
        """Collect current WAL statistics."""
        conn = self.get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT 
                wal_records,
                wal_bytes,
                wal_buffers_full,
                stats_reset
            FROM pg_stat_wal
        """)
        
        row = cur.fetchone()
        # Convert to int to avoid Decimal/float division issues
        wal_bytes = int(row[1])
        wal_records = int(row[0])
        wal_buffers_full = int(row[2])
        
        wal_gb = wal_bytes / (1024**3)
        
        now = datetime.now()
        
        # Calculate delta
        if self.initial_wal is None:
            self.initial_wal = wal_bytes
            self.start_time = now
            delta_mb = 0
        else:
            delta_mb = (wal_bytes - self.initial_wal) / (1024**2)
        
        # Calculate speed
        if self.start_time and self.history:
            elapsed_minutes = (now - self.start_time).total_seconds() / 60
            if elapsed_minutes > 0:
                speed_mb_min = float(delta_mb) / float(elapsed_minutes)
            else:
                speed_mb_min = 0
        else:
            speed_mb_min = 0
        
        stats = {
            'timestamp': now,
            'wal_records': wal_records,
            'wal_bytes': wal_bytes,
            'wal_gb': wal_gb,
            'wal_delta_mb': delta_mb,
            'wal_speed_mb_min': speed_mb_min,
            'wal_buffers_full': wal_buffers_full
        }
        
        self.history.append(stats)
        conn.close()
        
        return stats
    
    def get_history_df(self) -> pd.DataFrame:
        """Return history as DataFrame."""
        return pd.DataFrame(self.history)
    
    def estimate_completion(self, target_wal_gb: float) -> Optional[str]:
        """Estimate time to reach target WAL size."""
        if not self.history or len(self.history) < 2:
            return "Insufficient data"
        
        current = self.history[-1]
        speed = current['wal_speed_mb_min']
        
        if speed <= 0:
            return "Cannot estimate (speed is 0)"
        
        remaining_gb = target_wal_gb - current['wal_gb']
        remaining_minutes = (remaining_gb * 1024) / speed
        
        hours = int(remaining_minutes // 60)
        minutes = int(remaining_minutes % 60)
        
        return f"{hours}h {minutes}m"
