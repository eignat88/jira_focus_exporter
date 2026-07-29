"""
ETL Application Service.

Orchestrates the ETL pipeline:
- Connects to databases
- Manages table loading
- Handles errors and status tracking
"""

import time
from dataclasses import dataclass, field
from typing import List, Optional

from ax_to_postgres_etl.configs.settings import Settings, TableConfig
from ax_to_postgres_etl.utils.logger import log_message, log_error
from ax_to_postgres_etl.utils.retry import retry_on_error
from ax_to_postgres_etl.connectors.sqlserver import SQLServerConnector
from ax_to_postgres_etl.connectors.postgres import PostgresConnector
from ax_to_postgres_etl.metadata.schema_reader import read_table_schema, sync_target_schema
from ax_to_postgres_etl.metadata.column_analyzer import analyze_columns, suggest_columns
from ax_to_postgres_etl.loader.batch_loader import load_table
from ax_to_postgres_etl.loader.parallel_loader import ParallelLoader, LoadStatus
from ax_to_postgres_etl.loader.parallel_loader_v2 import ParallelLoaderV2
from ax_to_postgres_etl.domain import LoadResult


class ETLApplication:
    """Main ETL application service."""

    def __init__(self, settings: Settings, log_file: str, use_v2: bool = False, use_v2t: bool = False):
        self.settings = settings
        self.log_file = log_file
        self.use_v2 = use_v2
        self.use_v2t = use_v2t
        self.ss: Optional[SQLServerConnector] = None
        self.pg: Optional[PostgresConnector] = None
        self.run_id: Optional[int] = None
    
    def run(
        self,
        table_filter: Optional[str] = None,
        mode_override: Optional[str] = None,
        dry_run: bool = False,
    ) -> LoadResult:
        """
        Execute ETL pipeline.
        
        Args:
            table_filter: Only load this specific table
            mode_override: Override load mode from config
            dry_run: Show what would be done without executing
            
        Returns:
            LoadResult with status and statistics
        """
        start_time = time.time()
        failed_tables = []
        total_inserted = 0
        final_status = "FAILED"
        final_error = None
        
        try:
            # Connect to databases
            self._connect_databases()
            
            # Start ETL run
            self.run_id = self.pg.start_run(
                source_server=self.settings.source.server,
                source_database=self.settings.source.database,
                target_database=self.settings.db.database,
            )
            log_message(f"Run ID: {self.run_id}", self.log_file)
            
            # Get tables to process
            tables = self._get_tables(table_filter)
            
            if not tables:
                log_message("No tables configured for loading.", self.log_file)
                final_status = "ALREADY_COMPLETE"
                return LoadResult(
                    status=LoadStatus.ALREADY_COMPLETE,
                    elapsed_seconds=time.time() - start_time,
                )
            
            log_message(f"Tables to process: {[t.name for t in tables]}", self.log_file)
            
            # Process each table
            for table_config in tables:
                table_result = self._process_table(
                    table_config=table_config,
                    mode_override=mode_override,
                    dry_run=dry_run,
                )
                
                if table_result.status == LoadStatus.FAILED:
                    failed_tables.append(table_config.name)
                elif table_result.status == LoadStatus.ALREADY_COMPLETE:
                    # Count as success, not failure
                    pass
                else:
                    total_inserted += table_result.rows_inserted
            
            # Determine final status
            if failed_tables:
                final_status = "DONE_WITH_ERRORS"
            else:
                final_status = "DONE"
            
            # Return result
            elapsed = time.time() - start_time
            if failed_tables:
                return LoadResult(
                    status=LoadStatus.PARTIAL,
                    rows_inserted=total_inserted,
                    elapsed_seconds=elapsed,
                    failed_tables=tuple(failed_tables),
                )
            else:
                return LoadResult(
                    status=LoadStatus.SUCCESS,
                    rows_inserted=total_inserted,
                    elapsed_seconds=elapsed,
                )
        
        except Exception as e:
            log_error(f"CRITICAL ERROR: {e}", self.log_file)
            final_status = "FAILED"
            final_error = str(e)[:500]
            elapsed = time.time() - start_time
            return LoadResult(
                status=LoadStatus.FAILED,
                error_message=final_error,
                elapsed_seconds=elapsed,
            )
        
        finally:
            # Finish run ONCE before disconnecting
            try:
                if self.run_id is not None and self.pg is not None:
                    self.pg.finish_run(
                        self.run_id,
                        status=final_status,
                        error_message=final_error,
                    )
            except Exception:
                pass
            
            self._disconnect_databases()
    
    def _connect_databases(self):
        """Connect to SQL Server and PostgreSQL."""
        log_message("Connecting to SQL Server...", self.log_file)
        
        @retry_on_error(
            max_retries=self.settings.etl.max_retries, 
            delay=self.settings.etl.retry_delay
        )
        def connect_ss():
            self.ss = SQLServerConnector(
                server=self.settings.source.server,
                database=self.settings.source.database,
                driver=self.settings.source.driver,
            )
            self.ss.connect()
        
        connect_ss()
        log_message("  OK", self.log_file)
        
        log_message("Connecting to PostgreSQL...", self.log_file)
        
        @retry_on_error(
            max_retries=self.settings.etl.max_retries, 
            delay=self.settings.etl.retry_delay
        )
        def connect_pg():
            self.pg = PostgresConnector(
                host=self.settings.db.host,
                port=self.settings.db.port,
                database=self.settings.db.database,
                user=self.settings.db.user,
                password=self.settings.db.password,
                schema=self.settings.db.schema,
            )
            self.pg.connect()
            self.pg.create_schema()
            self.pg.create_etl_status_table()
            self.pg.create_etl_validation_table()
            self.pg.create_etl_status_v2()
        
        connect_pg()
        log_message("  OK", self.log_file)
    
    def _disconnect_databases(self):
        """Disconnect from databases."""
        if self.ss:
            self.ss.disconnect()
        if self.pg:
            self.pg.disconnect()
        log_message("Connections closed", self.log_file)
    
    def _get_tables(self, table_filter: Optional[str]) -> List[TableConfig]:
        """Get list of tables to process."""
        tables = self.settings.etl.tables
        
        if table_filter:
            requested = table_filter.upper()
            tables = [t for t in tables if t.name.upper() == requested]
            
            if not tables:
                all_names = [t.name for t in self.settings.etl.tables]
                log_message(
                    f"ERROR: Unknown table '{table_filter}'. Available: {', '.join(all_names)}", 
                    self.log_file
                )
                return []
        
        return tables
    
    def _process_table(
        self, 
        table_config: TableConfig, 
        mode_override: Optional[str],
        dry_run: bool,
    ) -> LoadResult:
        """Process a single table."""
        table_name = table_config.name
        
        log_message(f"--- {table_name} ---", self.log_file)
        
        if table_config.date_filter:
            log_message(f"  Filter: {table_config.date_filter}", self.log_file)
        if table_config.columns:
            log_message(f"  Columns: {', '.join(table_config.columns)}", self.log_file)
        if table_config.incremental_field:
            log_message(f"  Incremental field: {table_config.incremental_field}", self.log_file)
        
        # Determine load mode
        load_mode = mode_override or table_config.load_mode or self.settings.etl.load_mode
        log_message(f"  Load mode: {load_mode}", self.log_file)
        
        if dry_run:
            log_message("  [DRY RUN] Would load table", self.log_file)
            return LoadResult(status="SUCCESS")
        
        # Start table run
        table_run_id = self.pg.start_table_run(self.run_id, table_name, load_mode)
        
        try:
            # Read schema
            log_message("  [1/5] Reading schema...", self.log_file)
            pg_columns = read_table_schema(self.ss, table_name, columns=table_config.columns)
            log_message(f"  [1/5] Schema: {len(pg_columns)} columns", self.log_file)
            
            # Analyze columns
            columns = table_config.columns
            if not columns:
                log_message("  [2/5] Analyzing columns...", self.log_file)
                analysis = analyze_columns(
                    self.ss, table_name, 
                    log_func=lambda msg: log_message(msg, self.log_file)
                )
                auto_exclude = self.settings.etl.auto_exclude_columns
                suggested = suggest_columns(analysis, auto_exclude=auto_exclude)
                
                if len(suggested) < len(analysis["columns"]):
                    if auto_exclude:
                        log_message(
                            f"  [2/5] Loading {len(suggested)} of "
                            f"{len(analysis['columns'])} columns"
                            f" (skipping {len(analysis['empty'])} empty)",
                            self.log_file,
                        )
                    else:
                        log_message(
                            f"  [2/5] Analysis: {len(suggested)} columns have data, "
                            f"{len(analysis['empty'])} empty"
                            f" (auto_exclude=false, loading ALL {len(analysis['columns'])} columns)",
                            self.log_file,
                        )
                else:
                    log_message("  [2/5] All columns contain data", self.log_file)
                
                columns = suggested
                pg_columns = read_table_schema(self.ss, table_name, columns=columns)
            else:
                log_message("  [2/5] Columns specified in config, analysis skipped", self.log_file)
            
            # Sync schema
            log_message("  [3/5] Syncing schema...", self.log_file)
            sync_target_schema(
                self.pg, table_name, pg_columns, 
                log_func=lambda msg: log_message(msg, self.log_file)
            )
            log_message("  [3/5] Schema synced", self.log_file)
            
            # Setup UPSERT support
            log_message("  [4/5] Setting up UPSERT support...", self.log_file)
            self.pg.ensure_recid_index(table_name)
            
            # Load data
            log_message("  [4/5] Loading data...", self.log_file)
            
            use_parallel = (
                self.settings.etl.parallel and 
                self.settings.etl.parallel.enabled and 
                not table_config.incremental_field
            )
            
            if use_parallel:
                workers = self.settings.etl.parallel.workers

                if self.use_v2:
                    # Use new resume mechanism with etl.load_chunk
                    if self.use_v2t:
                        log_message(f"  [4/5] PARALLEL V2T mode: {workers} workers (P0-P15)", self.log_file)
                        from ax_to_postgres_etl.loader_v2t.parallel_loader_v2t import ParallelLoaderV2T as ParallelLoaderV2
                    else:
                        log_message(f"  [4/5] PARALLEL V2 mode: {workers} workers", self.log_file)
                        from ax_to_postgres_etl.loader.parallel_loader_v2 import ParallelLoaderV2

                    # Build config for ParallelLoaderV2
                    v2_config = {
                        'etl': {
                            'batch_size': self.settings.etl.batch_size,
                            'parallel': {
                                'enabled': True,
                                'workers': workers,
                                'fetch_size': self.settings.etl.parallel.fetch_size,
                                'commit_size': self.settings.etl.parallel.commit_size,
                            },
                        },
                        'tables': {
                            table_name: {
                                'source_schema': table_config.source_schema if hasattr(table_config, 'source_schema') else 'dbo',
                                'target_schema': self.pg.schema,
                                'chunk_strategy': 'numeric_range',
                                'chunk_column': 'RECID',
                                'chunk_count': getattr(table_config, 'chunk_count', 100),
                            },
                        },
                        'retry': self.settings.etl.retry if hasattr(self.settings.etl, 'retry') else {
                            'max_attempts': 5,
                            'initial_delay_seconds': 5,
                            'max_delay_seconds': 300,
                            'backoff_multiplier': 2,
                        },
                        'heartbeat': self.settings.etl.heartbeat if hasattr(self.settings.etl, 'heartbeat') else {
                            'interval_seconds': 30,
                            'timeout_seconds': 600,
                        },
                    }

                    parallel_loader = ParallelLoaderV2(
                        ss_conn_str=self.ss.conn_str,
                        pg=self.pg,
                        config=v2_config,
                        log_func=lambda msg: log_message(msg, self.log_file),
                        use_new_resume=True,
                    )
                    result = parallel_loader.load_table(
                        table_name=table_name,
                        columns=columns,
                        load_mode=load_mode,
                    )
                else:
                    # Use original parallel loader
                    log_message(f"  [4/5] PARALLEL mode: {workers} workers", self.log_file)

                    parallel_loader = ParallelLoader(
                        ss_conn_str=self.ss.conn_str,
                        pg_connector=self.pg,
                        workers=workers,
                        fetch_size=self.settings.etl.parallel.fetch_size,
                        commit_size=self.settings.etl.parallel.commit_size,
                        log_func=lambda msg: log_message(msg, self.log_file),
                        run_id=self.run_id,
                    )
                    result = parallel_loader.load_table(
                        table_name=table_name,
                        columns=columns,
                        load_mode=load_mode,
                    )
                
                # Handle ALREADY_COMPLETE
                if result.status == LoadStatus.ALREADY_COMPLETE:
                    log_message(f"  [4/5] Table already complete; data loading skipped", self.log_file)
                    self.pg.finish_table_run(table_run_id, status='DONE')
                    return LoadResult(
                        status=LoadStatus.ALREADY_COMPLETE,
                        table_name=table_name,
                        target_count=result.target_count,
                    )

                # Handle FAILED - do NOT run post-load operations
                if result.status == LoadStatus.FAILED:
                    error_msg = result.error_message or f"Parallel loader failed for {table_name}"
                    log_message(f"  [4/5] Load FAILED: {error_msg}", self.log_file)
                    self.pg.finish_table_run(
                        table_run_id,
                        status='FAILED',
                        error_message=error_msg[:500] if error_msg else None,
                    )
                    return LoadResult(
                        status=LoadStatus.FAILED,
                        table_name=table_name,
                        error_message=error_msg,
                        rows_fetched=result.rows_fetched,
                        rows_inserted=result.rows_inserted,
                        chunks_total=result.chunks_total,
                        chunks_completed=result.chunks_completed,
                    )

                # Normal load - run post-load operations
                log_message("  [4/5] Data loaded", self.log_file)
                log_message("  [5/5] Running post-load operations...", self.log_file)
                
                # Create indexes
                self.pg.create_indexes_after_load(
                    table_name, 
                    log_func=lambda msg: log_message(msg, self.log_file)
                )
                
                # Finish table run with statistics
                self.pg.finish_table_run(
                    table_run_id, 
                    status='DONE',
                    target_count=result.target_count or 0,
                    inserted=result.rows_inserted,
                    updated=result.rows_updated,
                    rejected=result.rows_rejected,
                )
                
                return LoadResult(
                    status=LoadStatus.SUCCESS,
                    table_name=table_name,
                    rows_fetched=result.rows_fetched,
                    rows_inserted=result.rows_inserted,
                    rows_updated=result.rows_updated,
                    rows_conflicted=result.rows_conflicted,
                    rows_rejected=result.rows_rejected,
                    chunks_total=result.chunks_total,
                    chunks_completed=result.chunks_completed,
                    target_count=result.target_count,
                    elapsed_seconds=result.elapsed_seconds,
                )
            else:
                load_table(
                    ss_connector=self.ss,
                    pg_connector=self.pg,
                    table_name=table_name,
                    batch_size=self.settings.etl.batch_size,
                    log_func=lambda msg: log_message(msg, self.log_file),
                    date_filter=table_config.date_filter,
                    columns=columns,
                    paginate_by=table_config.paginate_by,
                    incremental_field=table_config.incremental_field,
                    load_mode=load_mode,
                )
            
            log_message("  [4/5] Data loaded", self.log_file)
            log_message("  [5/5] Validation complete", self.log_file)
            
            # Create indexes
            self.pg.create_indexes_after_load(
                table_name, 
                log_func=lambda msg: log_message(msg, self.log_file)
            )
            
            # Finish table run
            self.pg.finish_table_run(table_run_id, status='DONE')
            
            return LoadResult(
                status=LoadStatus.SUCCESS,
                table_name=table_name,
            )
            
        except Exception as e:
            log_error(f"Error processing {table_name}", self.log_file)
            log_message(f"  Skipping table {table_name}, continuing...", self.log_file)
            
            try:
                self.pg.finish_table_run(
                    table_run_id, 
                    status='FAILED', 
                    error_message=str(e)[:500]
                )
            except Exception:
                log_message("  WARNING: Could not update table_run status", self.log_file)
            
            return LoadResult(status="FAILED", error_message=str(e))
