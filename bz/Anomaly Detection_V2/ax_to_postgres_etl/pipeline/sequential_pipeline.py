"""
Sequential pipeline for ETL loading.

Uses common writer and serializer with atomic checkpoint updates.
"""

import time
from typing import List, Any, Optional, Callable
from dataclasses import dataclass

from ax_to_postgres_etl.pipeline.serializer import serialize_rows, SerializationResult
from ax_to_postgres_etl.repositories.writer_repository import PostgresBatchWriter
from ax_to_postgres_etl.services.load_plan_service import (
    LoadPlanService, LoadMode, ChunkStatus, LoadPlan, ChunkPlan
)


@dataclass
class SequentialPipelineConfig:
    """Configuration for sequential pipeline."""
    batch_size: int = 100000
    encoding_error_policy: str = "fail"  # fail | reject_row | replace
    conflict_strategy: str = "DO NOTHING"


class SequentialPipeline:
    """
    Sequential pipeline with atomic checkpoint updates.
    
    Flow:
    - Read rows from source
    - Serialize to COPY format
    - Write to target with staging
    - Update checkpoint atomically
    """
    
    def __init__(
        self,
        writer: PostgresBatchWriter,
        plan_service: LoadPlanService,
        config: SequentialPipelineConfig,
        log_func: Optional[Callable] = None
    ):
        self.writer = writer
        self.plan_service = plan_service
        self.config = config
        self.log_func = log_func or print
        self.stop_event = None
    
    def _log(self, message: str):
        """Log message if log_func is set."""
        if self.log_func:
            self.log_func(message)
    
    def load_table(
        self,
        table_name: str,
        columns: List[str],
        rows_generator: Any,  # Iterator yielding rows
        load_mode: LoadMode,
        source_min_recid: int,
        source_max_recid: int,
        conflict_columns: Optional[List[str]] = None,
        stop_event: Optional[Any] = None
    ):
        """
        Load table using sequential pipeline.
        
        Args:
            table_name: Target table name
            columns: Column names
            rows_generator: Iterator yielding rows
            load_mode: Load mode
            source_min_recid: Source MIN(RECID)
            source_max_recid: Source MAX(RECID)
            conflict_columns: Columns for conflict detection
            stop_event: Event to signal shutdown
        """
        self.stop_event = stop_event
        
        # Get or create load plan
        load_plan, is_new = self.plan_service.get_or_create_load_plan(
            table_name=table_name,
            load_mode=load_mode,
            source_min_recid=source_min_recid,
            source_max_recid=source_max_recid,
            chunk_size=self.config.batch_size
        )
        
        self._log(f"Load plan: {load_plan.load_group_id} ({'new' if is_new else 'resumed'})")
        
        # Check if already complete
        if load_mode == LoadMode.RESUME and self.plan_service.is_load_complete(load_plan.load_group_id):
            self._log("ALREADY_COMPLETE: All chunks done")
            return {
                "status": "ALREADY_COMPLETE",
                "load_group_id": load_plan.load_group_id,
                "chunks_total": load_plan.chunk_count,
                "chunks_completed": load_plan.chunk_count
            }
        
        # Get pending chunks
        pending_chunks = self.plan_service.get_pending_chunks(load_plan.load_group_id)
        self._log(f"Pending chunks: {len(pending_chunks)}/{load_plan.chunk_count}")
        
        # Process each chunk
        stats = {
            "rows_fetched": 0,
            "rows_inserted": 0,
            "rows_conflicted": 0,
            "rows_rejected": 0,
            "chunks_completed": 0
        }
        
        for chunk_plan in pending_chunks:
            if stop_event and stop_event.is_set():
                self._log("Stop event detected, aborting")
                break
            
            chunk_stats = self._process_chunk(
                load_plan=load_plan,
                chunk_plan=chunk_plan,
                columns=columns,
                rows_generator=rows_generator,
                conflict_columns=conflict_columns
            )
            
            # Update stats
            stats["rows_fetched"] += chunk_stats["rows_fetched"]
            stats["rows_inserted"] += chunk_stats["rows_inserted"]
            stats["rows_conflicted"] += chunk_stats["rows_conflicted"]
            stats["rows_rejected"] += chunk_stats["rows_rejected"]
            stats["chunks_completed"] += 1
            
            # Log progress
            self._log(
                f"Chunk {chunk_plan.chunk_id}: "
                f"fetched={chunk_stats['rows_fetched']}, "
                f"inserted={chunk_stats['rows_inserted']}, "
                f"conflicts={chunk_stats['rows_conflicted']}"
            )
        
        # Finish load group
        if not stop_event or not stop_event.is_set():
            self.plan_service.finish_load_group(load_plan.load_group_id, "DONE")
        
        return {
            "status": "SUCCESS" if not stop_event or not stop_event.is_set() else "CANCELLED",
            "load_group_id": load_plan.load_group_id,
            "chunks_total": load_plan.chunk_count,
            "chunks_completed": stats["chunks_completed"],
            **stats
        }
    
    def _process_chunk(
        self,
        load_plan: LoadPlan,
        chunk_plan: ChunkPlan,
        columns: List[str],
        rows_generator: Any,
        conflict_columns: Optional[List[str]]
    ) -> dict:
        """Process a single chunk."""
        # Mark chunk as running
        self.plan_service.start_chunk(
            load_plan.load_group_id, 
            chunk_plan.chunk_id
        )
        
        chunk_stats = {
            "rows_fetched": 0,
            "rows_inserted": 0,
            "rows_conflicted": 0,
            "rows_rejected": 0
        }
        
        try:
            # Read rows for this chunk
            batch_rows = []
            last_recid = chunk_plan.range_from - 1
            
            for row in rows_generator:
                if self.stop_event and self.stop_event.is_set():
                    break
                
                # Extract RECID (assuming last column)
                recid = row[-1] if row else None
                
                # Skip rows outside chunk range
                if recid and recid < chunk_plan.range_from:
                    continue
                if recid and recid > chunk_plan.range_to:
                    break
                
                batch_rows.append(row)
                chunk_stats["rows_fetched"] += 1
                last_recid = recid
                
                # Process batch when full
                if len(batch_rows) >= self.config.batch_size:
                    batch_stats = self._write_batch(
                        load_plan, chunk_plan, columns, 
                        batch_rows, conflict_columns
                    )
                    chunk_stats["rows_inserted"] += batch_stats["inserted"]
                    chunk_stats["rows_conflicted"] += batch_stats["conflicted"]
                    batch_rows = []
            
            # Process remaining rows
            if batch_rows:
                batch_stats = self._write_batch(
                    load_plan, chunk_plan, columns,
                    batch_rows, conflict_columns
                )
                chunk_stats["rows_inserted"] += batch_stats["inserted"]
                chunk_stats["rows_conflicted"] += batch_stats["conflicted"]
            
            # Mark chunk as done
            self.plan_service.finish_chunk(
                load_plan.load_group_id,
                chunk_plan.chunk_id,
                ChunkStatus.DONE,
                rows_fetched=chunk_stats["rows_fetched"],
                rows_inserted=chunk_stats["rows_inserted"],
                rows_conflicted=chunk_stats["rows_conflicted"],
                last_committed_recid=last_recid
            )
            
        except Exception as e:
            # Mark chunk as failed
            self.plan_service.finish_chunk(
                load_plan.load_group_id,
                chunk_plan.chunk_id,
                ChunkStatus.FAILED,
                error_message=str(e)[:500]
            )
            raise
        
        return chunk_stats
    
    def _write_batch(
        self,
        load_plan: LoadPlan,
        chunk_plan: ChunkPlan,
        columns: List[str],
        rows: List[List],
        conflict_columns: Optional[List[str]]
    ) -> dict:
        """Write a batch of rows to target."""
        # Serialize rows
        serialization = serialize_rows(
            rows, columns, self.config.encoding_error_policy
        )
        
        if serialization.rows_skipped > 0:
            self._log(f"WARNING: {serialization.rows_skipped} rows skipped due to encoding errors")
        
        # Write to target
        inserted, conflicted = self.writer.load_batch(
            target_table=load_plan.table_name,
            columns=columns,
            rows=rows,
            conflict_columns=conflict_columns,
            conflict_strategy=self.config.conflict_strategy
        )
        
        return {
            "inserted": inserted,
            "conflicted": conflicted,
            "rejected": serialization.rows_skipped
        }
