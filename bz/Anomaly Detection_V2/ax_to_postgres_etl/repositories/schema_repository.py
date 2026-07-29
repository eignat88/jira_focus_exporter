"""
Schema repository for PostgreSQL schema operations.

Handles schema creation, table creation, and schema synchronization.
"""

from typing import List, Dict, Optional, Tuple
from psycopg2 import sql

from ax_to_postgres_etl.repositories.base import BaseRepository


class SchemaRepository(BaseRepository):
    """Repository for schema operations."""
    
    def __init__(self, conn_str: str, schema: str = "raw_ax"):
        super().__init__(conn_str)
        self.schema = schema
    
    def create_schema(self):
        """Create schema if not exists."""
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                    sql.Identifier(self.schema)
                )
            )
    
    def table_exists(self, table_name: str) -> bool:
        """Check if table exists in schema."""
        result = self.fetchone(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = %s AND table_name = %s
            )
            """,
            (self.schema, table_name.lower())
        )
        return result[0]
    
    def get_table_columns(self, table_name: str) -> List[Tuple[str, str]]:
        """Get column names and types for a table."""
        return self.fetchall(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (self.schema, table_name.lower())
        )
    
    def create_table(self, table_name: str, columns: List[Dict]):
        """Create table with specified columns."""
        col_defs = []
        for col in columns:
            name = col["name"]
            pg_type = col.get("pg_type", "text")
            nullable = "NULL" if col.get("nullable", True) else "NOT NULL"
            col_defs.append(f"{sql.Identifier(name)} {pg_type} {nullable}")
        
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                sql.SQL("CREATE TABLE IF NOT EXISTS {}.{} ({})").format(
                    sql.Identifier(self.schema),
                    sql.Identifier(table_name.lower()),
                    sql.SQL(", ").join(map(sql.SQL, col_defs))
                )
            )
    
    def add_column(self, table_name: str, column_name: str, pg_type: str = "text"):
        """Add column to existing table."""
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                sql.SQL("ALTER TABLE {}.{} ADD COLUMN IF NOT EXISTS {} {}").format(
                    sql.Identifier(self.schema),
                    sql.Identifier(table_name.lower()),
                    sql.Identifier(column_name),
                    sql.SQL(pg_type)
                )
            )
    
    def drop_table(self, table_name: str):
        """Drop table if exists."""
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                sql.SQL("DROP TABLE IF EXISTS {}.{}").format(
                    sql.Identifier(self.schema),
                    sql.Identifier(table_name.lower())
                )
            )
    
    def truncate_table(self, table_name: str):
        """Truncate table."""
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                sql.SQL("TRUNCATE TABLE {}.{}").format(
                    sql.Identifier(self.schema),
                    sql.Identifier(table_name.lower())
                )
            )
    
    def create_unique_index(self, table_name: str, column_name: str, index_name: Optional[str] = None):
        """Create unique index on column."""
        if index_name is None:
            index_name = f"idx_{table_name.lower()}_{column_name.lower()}"
        
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                sql.SQL("CREATE UNIQUE INDEX IF NOT EXISTS {} ON {}.{} ({})").format(
                    sql.Identifier(index_name),
                    sql.Identifier(self.schema),
                    sql.Identifier(table_name.lower()),
                    sql.Identifier(column_name)
                )
            )
    
    def analyze_table(self, table_name: str):
        """Analyze table for query optimizer."""
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                sql.SQL("ANALYZE {}.{}").format(
                    sql.Identifier(self.schema),
                    sql.Identifier(table_name.lower())
                )
            )
