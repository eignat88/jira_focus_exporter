"""
Base repository for PostgreSQL operations.

Handles connection management and provides transaction context.
"""

import psycopg2
from contextlib import contextmanager
from typing import Optional


class BaseRepository:
    """Base repository with connection management."""
    
    def __init__(self, conn_str: str):
        self.conn_str = conn_str
        self.conn: Optional[psycopg2.extensions.connection] = None
    
    def connect(self):
        """Establish database connection."""
        self.conn = psycopg2.connect(self.conn_str)
        self.conn.autocommit = False
        self.conn.set_client_encoding('UTF8')
        
        # Verify encoding
        cursor = self.conn.cursor()
        cursor.execute("SHOW client_encoding")
        actual_enc = cursor.fetchone()[0]
        cursor.close()
        
        if actual_enc.upper() != 'UTF8':
            import sys
            print(
                f"WARNING: set_client_encoding('UTF8') did not take effect! "
                f"Actual client encoding: {actual_enc}", 
                file=sys.stderr
            )
    
    def disconnect(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None
    
    @contextmanager
    def transaction(self):
        """
        Transaction context manager.
        
        Usage:
            with repo.transaction() as tx:
                tx.execute("INSERT INTO ...")
                # Auto-commit on success, rollback on exception
        """
        if not self.conn:
            raise RuntimeError("Not connected to database")
        
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
    
    def execute(self, sql: str, params: Optional[tuple] = None):
        """Execute SQL within current transaction."""
        cursor = self.conn.cursor()
        cursor.execute(sql, params)
        return cursor
    
    def executemany(self, sql: str, params_list):
        """Execute SQL for multiple parameter sets."""
        cursor = self.conn.cursor()
        cursor.executemany(sql, params_list)
        return cursor
    
    def fetchone(self, sql: str, params: Optional[tuple] = None):
        """Execute query and return first row."""
        cursor = self.execute(sql, params)
        return cursor.fetchone()
    
    def fetchall(self, sql: str, params: Optional[tuple] = None):
        """Execute query and return all rows."""
        cursor = self.execute(sql, params)
        return cursor.fetchall()
