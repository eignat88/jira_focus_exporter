"""
ETL metadata repository for status tracking.

Handles ETL run, table run, and chunk run status management.
"""

from typing import Optional, List, Dict
from datetime import datetime

from ax_to_postgres_etl.repositories.base import BaseRepository


class EtlMetadataRepository(BaseRepository):
    """Repository for ETL metadata operations."""
    
    def __init__(self, conn_str: str, schema: str = "raw_ax"):
        super().__init__(conn_str)
        self.schema = schema
    
    def create_etl_tables(self):
        """Create ETL status tables if not exist."""
        with self.transaction() as conn:
            cursor = conn.cursor()
            
            # ETL run table
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.schema}.etl_run (
                    run_id SERIAL PRIMARY KEY,
                    source_server TEXT,
                    source_database TEXT,
                    target_database TEXT,
                    status TEXT DEFAULT 'RUNNING',
                    started_at TIMESTAMP DEFAULT NOW(),
                    finished_at TIMESTAMP,
                    error_message TEXT
                )
            """)
            
            # ETL table run table
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.schema}.etl_table_run (
                    table_run_id SERIAL PRIMARY KEY,
                    run_id INTEGER REFERENCES {self.schema}.etl_run(run_id),
                    table_name TEXT NOT NULL,
                    load_mode TEXT,
                    status TEXT DEFAULT 'RUNNING',
                    started_at TIMESTAMP DEFAULT NOW(),
                    finished_at TIMESTAMP,
                    error_message TEXT,
                    rows_loaded BIGINT DEFAULT 0
                )
            """)
            
            # ETL chunk run table
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.schema}.etl_chunk_run (
                    chunk_run_id SERIAL PRIMARY KEY,
                    table_run_id INTEGER REFERENCES {self.schema}.etl_table_run(table_run_id),
                    table_name TEXT NOT NULL,
                    chunk_id INTEGER NOT NULL,
                    range_from BIGINT,
                    range_to BIGINT,
                    status TEXT DEFAULT 'PENDING',
                    attempt INTEGER DEFAULT 0,
                    last_fetched_recid BIGINT,
                    last_committed_recid BIGINT,
                    rows_fetched BIGINT DEFAULT 0,
                    rows_inserted BIGINT DEFAULT 0,
                    rows_updated BIGINT DEFAULT 0,
                    rows_conflicted BIGINT DEFAULT 0,
                    rows_rejected BIGINT DEFAULT 0,
                    started_at TIMESTAMP,
                    heartbeat_at TIMESTAMP,
                    finished_at TIMESTAMP,
                    error_message TEXT,
                    UNIQUE(table_run_id, chunk_id)
                )
            """)
            
            # ETL errors table
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.schema}.etl_errors (
                    error_id SERIAL PRIMARY KEY,
                    chunk_run_id INTEGER REFERENCES {self.schema}.etl_chunk_run(chunk_run_id),
                    table_name TEXT NOT NULL,
                    batch_number INTEGER,
                    row_number BIGINT,
                    error_type TEXT,
                    error_message TEXT,
                    row_data TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
    
    def start_run(
        self, 
        source_server: str, 
        source_database: str, 
        target_database: str
    ) -> int:
        """Start a new ETL run and return run_id."""
        result = self.fetchone(
            f"""
            INSERT INTO {self.schema}.etl_run 
            (source_server, source_database, target_database, status)
            VALUES (%s, %s, %s, 'RUNNING')
            RETURNING run_id
            """,
            (source_server, source_database, target_database)
        )
        return result[0]
    
    def finish_run(
        self, 
        run_id: int, 
        status: str = 'DONE', 
        error_message: Optional[str] = None
    ):
        """Mark ETL run as finished."""
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                UPDATE {self.schema}.etl_run 
                SET status = %s, finished_at = NOW(), error_message = %s
                WHERE run_id = %s
                """,
                (status, error_message, run_id)
            )
    
    def start_table_run(
        self, 
        run_id: int, 
        table_name: str, 
        load_mode: str
    ) -> int:
        """Start a new table run and return table_run_id."""
        result = self.fetchone(
            f"""
            INSERT INTO {self.schema}.etl_table_run 
            (run_id, table_name, load_mode, status)
            VALUES (%s, %s, %s, 'RUNNING')
            RETURNING table_run_id
            """,
            (run_id, table_name, load_mode)
        )
        return result[0]
    
    def finish_table_run(
        self, 
        table_run_id: int, 
        status: str = 'DONE', 
        error_message: Optional[str] = None,
        rows_loaded: int = 0
    ):
        """Mark table run as finished."""
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                UPDATE {self.schema}.etl_table_run 
                SET status = %s, finished_at = NOW(), error_message = %s, rows_loaded = %s
                WHERE table_run_id = %s
                """,
                (status, error_message, rows_loaded, table_run_id)
            )
    
    def start_chunk_run(
        self, 
        table_run_id: int, 
        table_name: str, 
        chunk_id: int, 
        range_from: int, 
        range_to: int
    ) -> int:
        """Start a new chunk run and return chunk_run_id."""
        result = self.fetchone(
            f"""
            INSERT INTO {self.schema}.etl_chunk_run 
            (table_run_id, table_name, chunk_id, range_from, range_to, status, attempt)
            VALUES (%s, %s, %s, %s, %s, 'RUNNING', 1)
            RETURNING chunk_run_id
            """,
            (table_run_id, table_name, chunk_id, range_from, range_to)
        )
        return result[0]
    
    def finish_chunk_run(
        self, 
        chunk_run_id: int, 
        status: str = 'DONE', 
        error_message: Optional[str] = None,
        rows_fetched: int = 0,
        rows_inserted: int = 0,
        rows_updated: int = 0,
        rows_conflicted: int = 0,
        rows_rejected: int = 0,
        last_fetched_recid: Optional[int] = None,
        last_committed_recid: Optional[int] = None
    ):
        """Mark chunk run as finished."""
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                UPDATE {self.schema}.etl_chunk_run 
                SET status = %s, finished_at = NOW(), error_message = %s,
                    rows_fetched = %s, rows_inserted = %s, rows_updated = %s,
                    rows_conflicted = %s, rows_rejected = %s,
                    last_fetched_recid = %s, last_committed_recid = %s
                WHERE chunk_run_id = %s
                """,
                (
                    status, error_message, rows_fetched, rows_inserted, 
                    rows_updated, rows_conflicted, rows_rejected,
                    last_fetched_recid, last_committed_recid, chunk_run_id
                )
            )
    
    def get_pending_chunks(self, table_run_id: int) -> List[Dict]:
        """Get pending chunks for a table run."""
        results = self.fetchall(
            f"""
            SELECT chunk_run_id, chunk_id, range_from, range_to, status, attempt
            FROM {self.schema}.etl_chunk_run
            WHERE table_run_id = %s AND status IN ('PENDING', 'FAILED', 'RUNNING')
            ORDER BY chunk_id
            """,
            (table_run_id,)
        )
        return [
            {
                "chunk_run_id": r[0],
                "chunk_id": r[1],
                "range_from": r[2],
                "range_to": r[3],
                "status": r[4],
                "attempt": r[5],
            }
            for r in results
        ]
    
    def get_completed_chunks(self, table_run_id: int) -> set:
        """Get completed chunk IDs for a table run."""
        results = self.fetchall(
            f"""
            SELECT chunk_id
            FROM {self.schema}.etl_chunk_run
            WHERE table_run_id = %s AND status = 'DONE'
            """,
            (table_run_id,)
        )
        return {r[0] for r in results}
    
    def log_error(
        self, 
        chunk_run_id: int, 
        table_name: str, 
        batch_number: int,
        row_number: int,
        error_type: str, 
        error_message: str, 
        row_data: Optional[str] = None
    ):
        """Log an error to etl_errors table."""
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                INSERT INTO {self.schema}.etl_errors 
                (chunk_run_id, table_name, batch_number, row_number, error_type, error_message, row_data)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (chunk_run_id, table_name, batch_number, row_number, error_type, error_message, row_data)
            )
