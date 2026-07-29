"""
PostgreSQL batch writer for COPY and UPSERT operations.

Handles data loading with staging tables and conflict resolution.
"""

import io
import csv
from typing import List, Optional, Tuple

from ax_to_postgres_etl.repositories.base import BaseRepository


class PostgresBatchWriter(BaseRepository):
    """Repository for batch write operations."""
    
    def __init__(self, conn_str: str, schema: str = "raw_ax"):
        super().__init__(conn_str)
        self.schema = schema
    
    def copy_to_staging(
        self, 
        staging_table: str, 
        columns: List[str], 
        rows: List[List]
    ) -> int:
        """
        Copy rows to staging table using COPY.
        
        Returns number of rows copied.
        """
        if not rows:
            return 0
        
        # Build tab-delimited COPY buffer
        output = io.StringIO(newline="")
        writer = csv.writer(
            output,
            delimiter="\t",
            quotechar='"',
            quoting=csv.QUOTE_MINIMAL,
            lineterminator="\n",
        )
        
        for row in rows:
            writer.writerow(row)
        
        output.seek(0)
        
        # Execute COPY
        col_list = ", ".join(columns)
        copy_sql = f"""
            COPY {self.schema}.{staging_table} ({col_list})
            FROM STDIN
            WITH (
                FORMAT CSV,
                DELIMITER E'\\t',
                QUOTE E'"',
                ESCAPE E'"',
                NULL E'\\N'
            )
        """
        
        cursor = self.conn.cursor()
        cursor.copy_expert(copy_sql, output)
        return len(rows)
    
    def upsert_from_staging(
        self, 
        staging_table: str, 
        target_table: str, 
        columns: List[str],
        conflict_columns: List[str] = None,
        conflict_strategy: str = "DO NOTHING"
    ) -> Tuple[int, int]:
        """
        Upsert data from staging to target table.
        
        Returns (inserted_count, conflicted_count).
        """
        if conflict_columns is None:
            conflict_columns = ["recid"]
        
        col_list = ", ".join(columns)
        conflict_cols = ", ".join(conflict_columns)
        
        # Build INSERT statement
        if conflict_strategy == "DO NOTHING":
            on_conflict = f"ON CONFLICT ({conflict_cols}) DO NOTHING"
        elif conflict_strategy == "DO UPDATE":
            # Build UPDATE clause for all non-conflict columns
            update_cols = [c for c in columns if c not in conflict_columns]
            update_set = ", ".join([f"{c} = EXCLUDED.{c}" for c in update_cols])
            on_conflict = f"ON CONFLICT ({conflict_cols}) DO UPDATE SET {update_set}"
        else:
            on_conflict = ""
        
        insert_sql = f"""
            INSERT INTO {self.schema}.{target_table} ({col_list})
            SELECT {col_list}
            FROM {self.schema}.{staging_table}
            {on_conflict}
        """
        
        cursor = self.conn.cursor()
        cursor.execute(insert_sql)
        
        inserted = cursor.rowcount
        # For DO NOTHING, rowcount may not accurately reflect conflicts
        # We'd need additional logic to count conflicts
        conflicted = 0  # Placeholder
        
        return inserted, conflicted
    
    def create_staging_table(self, staging_table: str, target_table: str):
        """Create staging table matching target table structure."""
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                CREATE UNLOGGED TABLE {self.schema}.{staging_table}
                (LIKE {self.schema}.{target_table} INCLUDING DEFAULTS)
            """)
    
    def drop_staging_table(self, staging_table: str):
        """Drop staging table."""
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(f"DROP TABLE IF EXISTS {self.schema}.{staging_table}")
    
    def truncate_staging(self, staging_table: str):
        """Truncate staging table."""
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(f"TRUNCATE {self.schema}.{staging_table}")
    
    def load_batch(
        self,
        target_table: str,
        columns: List[str],
        rows: List[List],
        conflict_columns: List[str] = None,
        conflict_strategy: str = "DO NOTHING"
    ) -> Tuple[int, int]:
        """
        Load a batch of rows to target table.
        
        Uses staging table for idempotent loads.
        Returns (inserted_count, conflicted_count).
        """
        if not rows:
            return 0, 0
        
        staging_table = f"_staging_{target_table.lower()}"
        
        # Create staging table if not exists
        if not self.table_exists(staging_table):
            self.create_staging_table(staging_table, target_table)
        
        try:
            # Truncate staging
            self.truncate_staging(staging_table)
            
            # Copy to staging
            self.copy_to_staging(staging_table, columns, rows)
            
            # Upsert from staging to target
            inserted, conflicted = self.upsert_from_staging(
                staging_table, target_table, columns, 
                conflict_columns, conflict_strategy
            )
            
            return inserted, conflicted
            
        finally:
            # Clean up staging table
            self.drop_staging_table(staging_table)
