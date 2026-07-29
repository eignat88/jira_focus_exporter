"""PostgreSQL table statistics monitoring collector."""

import psycopg2
from datetime import datetime
from typing import Optional
import pandas as pd


class TableMonitor:
    """Collects table statistics from PostgreSQL."""
    
    def __init__(self, conn_params: dict, tables: list = None):
        self.conn_params = conn_params
        self.tables = tables or []
        self.history = []
        
    def get_connection(self):
        return psycopg2.connect(**self.conn_params)
    
    def collect_table_stats(self, schema: str, table: str) -> dict:
        """Collect statistics for a specific table."""
        conn = self.get_connection()
        cur = conn.cursor()
        
        # Get row counts
        cur.execute(f"""
            SELECT 
                n_live_tup,
                n_dead_tup,
                last_analyze,
                last_autoanalyze
            FROM pg_stat_user_tables
            WHERE schemaname = %s AND tablename = %s
        """, (schema, table))
        
        row = cur.fetchone()
        if not row:
            conn.close()
            return {}
        
        # Get table size
        cur.execute(f"""
            SELECT pg_size_pretty(pg_total_relation_size(%s))
        """, (f"{schema}.{table}",))
        
        size_row = cur.fetchone()
        size_pretty = size_row[0] if size_row else '0 bytes'
        
        # Get size in bytes
        cur.execute(f"""
            SELECT pg_total_relation_size(%s)
        """, (f"{schema}.{table}",))
        
        size_bytes_row = cur.fetchone()
        size_bytes = size_bytes_row[0] if size_bytes_row else 0
        
        stats = {
            'timestamp': datetime.now(),
            'schema': schema,
            'table': table,
            'n_live_tup': row[0],
            'n_dead_tup': row[1],
            'last_analyze': row[2],
            'last_autoanalyze': row[3],
            'size_pretty': size_pretty,
            'size_bytes': size_bytes,
            'dead_tuple_percent': (row[1] / row[0] * 100) if row[0] > 0 else 0
        }
        
        self.history.append(stats)
        conn.close()
        return stats
    
    def collect_all_tables(self) -> list:
        """Collect statistics for all configured tables."""
        results = []
        for table in self.tables:
            parts = table.split('.')
            schema = parts[0] if len(parts) > 1 else 'public'
            table_name = parts[-1]
            stats = self.collect_table_stats(schema, table_name)
            if stats:
                results.append(stats)
        return results
    
    def get_history_df(self) -> pd.DataFrame:
        """Return history as DataFrame."""
        return pd.DataFrame(self.history)
    
    def get_table_history(self, schema: str, table: str) -> pd.DataFrame:
        """Get history for specific table."""
        df = self.get_history_df()
        if df.empty:
            return df
        return df[(df['schema'] == schema) & (df['table'] == table)]
