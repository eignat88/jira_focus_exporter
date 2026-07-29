"""Chunking strategies for ETL load operations."""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import List, Tuple, Optional

import psycopg2


class ChunkStrategy(ABC):
    """Base class for chunking strategies."""

    @abstractmethod
    def get_boundaries(
        self,
        conn: psycopg2.extensions.connection,
        source_schema: str,
        table_name: str,
        chunk_column: Optional[str] = None,
    ) -> Tuple:
        """
        Get boundaries from source table.
        
        Args:
            conn: Database connection
            source_schema: Source schema name
            table_name: Table name
            chunk_column: Column used for chunking
            
        Returns:
            Tuple of (min_value, max_value)
        """
        pass

    @abstractmethod
    def build_ranges(
        self,
        boundaries: Tuple,
        chunk_count: int,
    ) -> List[Tuple]:
        """
        Build chunk ranges from boundaries.
        
        Args:
            boundaries: Tuple of (min_value, max_value)
            chunk_count: Number of chunks to create
            
        Returns:
            List of (start, end) tuples
        """
        pass

    @abstractmethod
    def build_query(
        self,
        chunk_column: str,
        range_start,
        range_end,
        columns: str = "*",
        is_first: bool = False,
    ) -> str:
        """
        Build SQL query for a chunk.
        
        Args:
            chunk_column: Column used for chunking
            range_start: Range start value
            range_end: Range end value
            columns: Columns to select
            is_first: Whether this is the first chunk (use >= instead of >)
            
        Returns:
            SQL query string
        """
        pass


class NumericRangeStrategy(ChunkStrategy):
    """Strategy for numeric range chunking (RECID, ID, etc.)."""

    def get_boundaries(
        self,
        conn: psycopg2.extensions.connection,
        source_schema: str,
        table_name: str,
        chunk_column: Optional[str] = None,
    ) -> Tuple[int, int]:
        """Get MIN and MAX of numeric column."""
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT MIN({chunk_column}), MAX({chunk_column}) "
            f"FROM {source_schema}.{table_name}"
        )
        row = cursor.fetchone()
        return (row[0], row[1])

    def build_ranges(
        self,
        boundaries: Tuple[int, int],
        chunk_count: int,
    ) -> List[Tuple[int, int]]:
        """Build numeric ranges."""
        min_val, max_val = boundaries
        range_size = (max_val - min_val) // chunk_count
        
        ranges = []
        for i in range(chunk_count):
            start = min_val + (i * range_size)
            end = min_val + ((i + 1) * range_size) if i < chunk_count - 1 else max_val + 1
            ranges.append((start, end))
        
        return ranges

    def build_query(
        self,
        chunk_column: str,
        range_start: int,
        range_end: int,
        columns: str = "*",
        is_first: bool = False,
    ) -> str:
        """Build numeric range query."""
        op = ">=" if is_first else ">"
        return (
            f"SELECT {columns} FROM source_table "
            f"WHERE {chunk_column} {op} {range_start} "
            f"AND {chunk_column} <= {range_end} "
            f"ORDER BY {chunk_column}"
        )


class DateTimeRangeStrategy(ChunkStrategy):
    """Strategy for datetime range chunking."""

    def get_boundaries(
        self,
        conn: psycopg2.extensions.connection,
        source_schema: str,
        table_name: str,
        chunk_column: Optional[str] = None,
    ) -> Tuple[datetime, datetime]:
        """Get MIN and MAX of datetime column."""
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT MIN({chunk_column}), MAX({chunk_column}) "
            f"FROM {source_schema}.{table_name}"
        )
        row = cursor.fetchone()
        return (row[0], row[1])

    def build_ranges(
        self,
        boundaries: Tuple[datetime, datetime],
        chunk_count: int,
    ) -> List[Tuple[datetime, datetime]]:
        """Build datetime ranges."""
        min_val, max_val = boundaries
        total_seconds = (max_val - min_val).total_seconds()
        interval_seconds = total_seconds // chunk_count
        
        ranges = []
        for i in range(chunk_count):
            start = min_val + timedelta(seconds=i * interval_seconds)
            end = min_val + timedelta(seconds=(i + 1) * interval_seconds) if i < chunk_count - 1 else max_val
            ranges.append((start, end))
        
        return ranges

    def build_query(
        self,
        chunk_column: str,
        range_start: datetime,
        range_end: datetime,
        columns: str = "*",
        is_first: bool = False,
    ) -> str:
        """Build datetime range query."""
        op = ">=" if is_first else ">"
        return (
            f"SELECT {columns} FROM source_table "
            f"WHERE {chunk_column} {op} '{range_start.isoformat()}' "
            f"AND {chunk_column} < '{range_end.isoformat()}' "
            f"ORDER BY {chunk_column}"
        )


class FullTableStrategy(ChunkStrategy):
    """Strategy for full table load (single chunk)."""

    def get_boundaries(
        self,
        conn: psycopg2.extensions.connection,
        source_schema: str,
        table_name: str,
        chunk_column: Optional[str] = None,
    ) -> Tuple[None, None]:
        """No boundaries needed for full table."""
        return (None, None)

    def build_ranges(
        self,
        boundaries: Tuple[None, None],
        chunk_count: int,
    ) -> List[Tuple[None, None]]:
        """Single chunk for full table."""
        return [(None, None)]

    def build_query(
        self,
        chunk_column: str,
        range_start: None,
        range_end: None,
        columns: str = "*",
        is_first: bool = False,
    ) -> str:
        """Build full table query."""
        return f"SELECT {columns} FROM source_table"


class CompositeKeyStrategy(ChunkStrategy):
    """Strategy for composite key chunking (DATAAREAID + RECID)."""

    def get_boundaries(
        self,
        conn: psycopg2.extensions.connection,
        source_schema: str,
        table_name: str,
        chunk_column: Optional[str] = None,
    ) -> Tuple:
        """Get boundaries for composite key."""
        # Implementation depends on specific composite key structure
        raise NotImplementedError("Composite key strategy not yet implemented")

    def build_ranges(
        self,
        boundaries: Tuple,
        chunk_count: int,
    ) -> List[Tuple]:
        """Build ranges for composite key."""
        raise NotImplementedError("Composite key strategy not yet implemented")

    def build_query(
        self,
        chunk_column: str,
        range_start,
        range_end,
        columns: str = "*",
        is_first: bool = False,
    ) -> str:
        """Build query for composite key."""
        raise NotImplementedError("Composite key strategy not yet implemented")


def get_strategy(strategy_name: str) -> ChunkStrategy:
    """
    Get chunking strategy by name.
    
    Args:
        strategy_name: Strategy name
        
    Returns:
        ChunkStrategy instance
        
    Raises:
        ValueError: If strategy not found
    """
    strategies = {
        "numeric_range": NumericRangeStrategy,
        "datetime_range": DateTimeRangeStrategy,
        "full_table": FullTableStrategy,
        "composite_key": CompositeKeyStrategy,
    }
    
    if strategy_name not in strategies:
        raise ValueError(f"Unknown chunk strategy: {strategy_name}")
    
    return strategies[strategy_name]()
