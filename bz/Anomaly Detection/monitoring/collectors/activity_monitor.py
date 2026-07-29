"""PostgreSQL activity monitoring collector."""

import psycopg2
from datetime import datetime
from typing import Optional
import pandas as pd


class ActivityMonitor:
    """Collects PostgreSQL activity statistics."""
    
    def __init__(self, conn_params: dict):
        self.conn_params = conn_params
        self.history = []
        
    def get_connection(self):
        return psycopg2.connect(**self.conn_params)
    
    def collect(self) -> list:
        """Collect current active queries."""
        conn = self.get_connection()
        cur = conn.cursor()
        
        cur.execute("""
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
            ORDER BY query_start
        """)
        
        activities = []
        now = datetime.now()
        
        for row in cur.fetchall():
            activity = {
                'timestamp': now,
                'pid': row[0],
                'application_name': row[1],
                'state': row[2],
                'wait_event_type': row[3],
                'wait_event': row[4],
                'runtime': row[5],
                'runtime_seconds': row[5].total_seconds() if row[5] else 0,
                'query': row[6]
            }
            activities.append(activity)
        
        self.history.append({
            'timestamp': now,
            'active_count': len(activities),
            'activities': activities
        })
        
        conn.close()
        return activities
    
    def get_long_running_queries(self, threshold_seconds: int = 300) -> list:
        """Get queries running longer than threshold."""
        activities = self.collect()
        return [a for a in activities if a['runtime_seconds'] > threshold_seconds]
    
    def get_history_df(self) -> pd.DataFrame:
        """Return history as DataFrame."""
        return pd.DataFrame(self.history)
    
    def filter_by_query(self, pattern: str) -> list:
        """Filter activities by query pattern."""
        activities = self.collect()
        return [a for a in activities if pattern.lower() in (a['query'] or '').lower()]
    
    def filter_by_pid(self, pid: int) -> Optional[dict]:
        """Get activity for specific PID."""
        activities = self.collect()
        for a in activities:
            if a['pid'] == pid:
                return a
        return None
