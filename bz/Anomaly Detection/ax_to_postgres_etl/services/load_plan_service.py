"""
Load plan service for ETL pipeline.

Manages permanent load plans, chunk lifecycle, and resume logic.
"""

import uuid
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from datetime import datetime
from enum import Enum

from ax_to_postgres_etl.repositories.base import BaseRepository


class LoadMode(str, Enum):
    """Load mode enumeration."""
    FULL = "full"
    RELOAD = "reload"
    RESUME = "resume"
    INCREMENTAL = "incremental"


class ChunkStatus(str, Enum):
    """Chunk status enumeration."""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMMITTING = "COMMITTING"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class LoadPlan:
    """Represents a load plan for a table."""
    load_group_id: str
    table_name: str
    load_mode: LoadMode
    range_min: int
    range_max: int
    chunk_count: int
    status: str = "RUNNING"
    created_at: datetime = field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None
    config_hash: Optional[str] = None
    schema_hash: Optional[str] = None


@dataclass
class ChunkPlan:
    """Represents a single chunk in a load plan."""
    chunk_id: int
    range_from: int
    range_to: int
    status: ChunkStatus = ChunkStatus.PENDING
    attempt: int = 0
    last_fetched_recid: Optional[int] = None
    last_committed_recid: Optional[int] = None
    rows_fetched: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_conflicted: int = 0
    rows_rejected: int = 0
    started_at: Optional[datetime] = None
    heartbeat_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error_message: Optional[str] = None


class LoadPlanService(BaseRepository):
    """Service for managing load plans."""
    
    def __init__(self, conn_str: str, schema: str = "raw_ax"):
        super().__init__(conn_str)
        self.schema = schema
    
    def create_load_group_tables(self):
        """Create load_group and extended chunk_run tables."""
        with self.transaction() as conn:
            cursor = conn.cursor()
            
            # Load group table
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.schema}.etl_load_group (
                    load_group_id UUID PRIMARY KEY,
                    table_name TEXT NOT NULL,
                    load_mode TEXT NOT NULL,
                    range_min BIGINT,
                    range_max BIGINT,
                    chunk_count INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'RUNNING',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    finished_at TIMESTAMP,
                    config_hash TEXT,
                    schema_hash TEXT
                )
            """)
            
            # Extended chunk_run with load_group_id
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.schema}.etl_chunk_run_v2 (
                    chunk_run_id SERIAL PRIMARY KEY,
                    load_group_id UUID REFERENCES {self.schema}.etl_load_group(load_group_id),
                    run_id BIGINT,
                    table_name TEXT NOT NULL,
                    chunk_id INTEGER NOT NULL,
                    range_from BIGINT NOT NULL,
                    range_to BIGINT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    attempt INTEGER NOT NULL DEFAULT 0,
                    last_fetched_recid BIGINT,
                    last_committed_recid BIGINT,
                    rows_fetched BIGINT NOT NULL DEFAULT 0,
                    rows_inserted BIGINT NOT NULL DEFAULT 0,
                    rows_updated BIGINT NOT NULL DEFAULT 0,
                    rows_conflicted BIGINT NOT NULL DEFAULT 0,
                    rows_rejected BIGINT NOT NULL DEFAULT 0,
                    started_at TIMESTAMP,
                    heartbeat_at TIMESTAMP,
                    finished_at TIMESTAMP,
                    error_message TEXT,
                    UNIQUE(load_group_id, chunk_id)
                )
            """)
    
    def create_load_plan(
        self,
        table_name: str,
        load_mode: LoadMode,
        range_min: int,
        range_max: int,
        chunk_size: int = 100000
    ) -> LoadPlan:
        """Create a new load plan with chunks."""
        load_group_id = str(uuid.uuid4())
        
        # Calculate chunk ranges
        chunks = []
        start = range_min
        chunk_id = 0
        
        while start <= range_max:
            end = min(start + chunk_size - 1, range_max)
            chunks.append(ChunkPlan(
                chunk_id=chunk_id,
                range_from=start,
                range_to=end
            ))
            start = end + 1
            chunk_id += 1
        
        chunk_count = len(chunks)
        
        # Insert load group
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                INSERT INTO {self.schema}.etl_load_group 
                (load_group_id, table_name, load_mode, range_min, range_max, chunk_count)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (load_group_id, table_name, load_mode.value, range_min, range_max, chunk_count))
            
            # Insert chunks
            for chunk in chunks:
                cursor.execute(f"""
                    INSERT INTO {self.schema}.etl_chunk_run_v2 
                    (load_group_id, table_name, chunk_id, range_from, range_to, status)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (
                    load_group_id, table_name, chunk.chunk_id,
                    chunk.range_from, chunk.range_to, ChunkStatus.PENDING.value
                ))
        
        return LoadPlan(
            load_group_id=load_group_id,
            table_name=table_name,
            load_mode=load_mode,
            range_min=range_min,
            range_max=range_max,
            chunk_count=chunk_count
        )
    
    def get_or_create_load_plan(
        self,
        table_name: str,
        load_mode: LoadMode,
        source_min_recid: int,
        source_max_recid: int,
        chunk_size: int = 100000
    ) -> tuple[LoadPlan, bool]:
        """
        Get existing load plan or create new one.
        
        Returns:
            Tuple of (load_plan, is_new)
        """
        # Check for existing running plan
        result = self.fetchone(f"""
            SELECT load_group_id, table_name, load_mode, range_min, range_max, chunk_count
            FROM {self.schema}.etl_load_group
            WHERE table_name = %s AND status = 'RUNNING'
            ORDER BY created_at DESC
            LIMIT 1
        """, (table_name,))
        
        if result:
            # Resume existing plan
            load_plan = LoadPlan(
                load_group_id=result[0],
                table_name=result[1],
                load_mode=LoadMode(result[2]),
                range_min=result[3],
                range_max=result[4],
                chunk_count=result[5]
            )
            return load_plan, False
        
        # Create new plan
        load_plan = self.create_load_plan(
            table_name=table_name,
            load_mode=load_mode,
            range_min=source_min_recid,
            range_max=source_max_recid,
            chunk_size=chunk_size
        )
        return load_plan, True
    
    def get_pending_chunks(self, load_group_id: str) -> List[ChunkPlan]:
        """Get pending chunks for a load group."""
        results = self.fetchall(f"""
            SELECT chunk_id, range_from, range_to, status, attempt,
                   last_fetched_recid, last_committed_recid
            FROM {self.schema}.etl_chunk_run_v2
            WHERE load_group_id = %s AND status IN ('PENDING', 'FAILED', 'RUNNING')
            ORDER BY chunk_id
        """, (load_group_id,))
        
        return [
            ChunkPlan(
                chunk_id=r[0],
                range_from=r[1],
                range_to=r[2],
                status=ChunkStatus(r[3]),
                attempt=r[4],
                last_fetched_recid=r[5],
                last_committed_recid=r[6]
            )
            for r in results
        ]
    
    def get_completed_chunks(self, load_group_id: str) -> set:
        """Get completed chunk IDs for a load group."""
        results = self.fetchall(f"""
            SELECT chunk_id
            FROM {self.schema}.etl_chunk_run_v2
            WHERE load_group_id = %s AND status = 'DONE'
        """, (load_group_id,))
        return {r[0] for r in results}
    
    def start_chunk(self, load_group_id: str, chunk_id: int, run_id: Optional[int] = None):
        """Mark chunk as running."""
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE {self.schema}.etl_chunk_run_v2 
                SET status = %s, started_at = NOW(), run_id = %s, attempt = attempt + 1
                WHERE load_group_id = %s AND chunk_id = %s
            """, (ChunkStatus.RUNNING.value, run_id, load_group_id, chunk_id))
    
    def finish_chunk(
        self,
        load_group_id: str,
        chunk_id: int,
        status: ChunkStatus,
        rows_fetched: int = 0,
        rows_inserted: int = 0,
        rows_updated: int = 0,
        rows_conflicted: int = 0,
        rows_rejected: int = 0,
        last_fetched_recid: Optional[int] = None,
        last_committed_recid: Optional[int] = None,
        error_message: Optional[str] = None
    ):
        """Mark chunk as finished."""
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE {self.schema}.etl_chunk_run_v2 
                SET status = %s, finished_at = NOW(), error_message = %s,
                    rows_fetched = %s, rows_inserted = %s, rows_updated = %s,
                    rows_conflicted = %s, rows_rejected = %s,
                    last_fetched_recid = %s, last_committed_recid = %s
                WHERE load_group_id = %s AND chunk_id = %s
            """, (
                status.value, error_message, rows_fetched, rows_inserted,
                rows_updated, rows_conflicted, rows_rejected,
                last_fetched_recid, last_committed_recid,
                load_group_id, chunk_id
            ))
    
    def finish_load_group(self, load_group_id: str, status: str = "DONE"):
        """Mark load group as finished."""
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE {self.schema}.etl_load_group 
                SET status = %s, finished_at = NOW()
                WHERE load_group_id = %s
            """, (status, load_group_id))
    
    def is_load_complete(self, load_group_id: str) -> bool:
        """Check if all chunks in load group are done."""
        result = self.fetchone(f"""
            SELECT COUNT(*) 
            FROM {self.schema}.etl_chunk_run_v2
            WHERE load_group_id = %s AND status != 'DONE'
        """, (load_group_id,))
        return result[0] == 0
    
    def get_load_progress(self, load_group_id: str) -> Dict:
        """Get load progress statistics."""
        results = self.fetchall(f"""
            SELECT 
                COUNT(*) as total,
                COUNT(CASE WHEN status = 'DONE' THEN 1 END) as completed,
                COUNT(CASE WHEN status = 'RUNNING' THEN 1 END) as running,
                COUNT(CASE WHEN status = 'PENDING' THEN 1 END) as pending,
                COUNT(CASE WHEN status = 'FAILED' THEN 1 END) as failed,
                SUM(rows_fetched) as total_fetched,
                SUM(rows_inserted) as total_inserted,
                SUM(rows_conflicted) as total_conflicted
            FROM {self.schema}.etl_chunk_run_v2
            WHERE load_group_id = %s
        """, (load_group_id,))
        
        row = results[0]
        return {
            "total_chunks": row[0],
            "completed_chunks": row[1],
            "running_chunks": row[2],
            "pending_chunks": row[3],
            "failed_chunks": row[4],
            "total_rows_fetched": row[5] or 0,
            "total_rows_inserted": row[6] or 0,
            "total_rows_conflicted": row[7] or 0,
        }
