"""Memory-efficient streaming for large table loads."""

import io
import time
from typing import Iterator, List, Callable, Optional
from dataclasses import dataclass


@dataclass
class StreamChunk:
    """A chunk of data for streaming."""
    chunk_id: int
    rows: List[List]
    start_key: int
    end_key: int
    row_count: int


class StreamingBuffer:
    """
    Memory-efficient buffer for streaming data to PostgreSQL.

    Features:
    - Configurable memory limit
    - Automatic flushing when limit reached
    - Generator-based iteration
    """

    def __init__(
        self,
        max_rows: int = 10000,
        max_memory_mb: float = 100.0,
        flush_callback: Optional[Callable] = None,
    ):
        self.max_rows = max_rows
        self.max_memory_bytes = max_memory_mb * 1024 * 1024
        self.flush_callback = flush_callback
        self._buffer: List[List] = []
        self._current_size = 0
        self._total_flushed = 0
        self._flush_count = 0

    def add_row(self, row: List) -> bool:
        """
        Add a row to the buffer.

        Returns True if buffer was flushed.
        """
        row_size = self._estimate_row_size(row)
        self._buffer.append(row)
        self._current_size += row_size

        # Check if flush needed
        if len(self._buffer) >= self.max_rows or self._current_size >= self.max_memory_bytes:
            self.flush()
            return True

        return False

    def add_rows(self, rows: List[List]) -> int:
        """
        Add multiple rows to the buffer.

        Returns number of flushes performed.
        """
        flushes = 0
        for row in rows:
            if self.add_row(row):
                flushes += 1
        return flushes

    def flush(self):
        """Flush buffer contents."""
        if not self._buffer:
            return

        if self.flush_callback:
            self.flush_callback(self._buffer)

        self._total_flushed += len(self._buffer)
        self._flush_count += 1
        self._buffer = []
        self._current_size = 0

    def _estimate_row_size(self, row: List) -> int:
        """Estimate memory size of a row."""
        size = 0
        for val in row:
            if val is None:
                size += 8
            elif isinstance(val, str):
                size += len(val.encode('utf-8')) + 40
            elif isinstance(val, (int, float)):
                size += 28
            elif isinstance(val, bytes):
                size += len(val) + 40
            else:
                size += 80  # Estimate for other types
        return size

    @property
    def stats(self) -> dict:
        """Get buffer statistics."""
        return {
            "buffered": len(self._buffer),
            "buffer_size_bytes": self._current_size,
            "total_flushed": self._total_flushed,
            "flush_count": self._flush_count,
            "max_rows": self.max_rows,
            "max_memory_mb": self.max_memory_bytes / (1024 * 1024),
        }


class StreamingReader:
    """
    Stream data from SQL Server in chunks.

    Features:
    - Iterator-based interface
    - Configurable chunk size
    - Memory-efficient processing
    """

    def __init__(
        self,
        conn,
        query: str,
        chunk_size: int = 5000,
        key_column: str = "RECID",
    ):
        self.conn = conn
        self.query = query
        self.chunk_size = chunk_size
        self.key_column = key_column
        self._last_key = 0
        self._total_rows = 0

    def read_chunks(self) -> Iterator[StreamChunk]:
        """Read data in chunks."""
        chunk_id = 0

        while True:
            chunk = self._read_chunk(chunk_id)
            if chunk is None or chunk.row_count == 0:
                break

            self._last_key = chunk.end_key
            self._total_rows += chunk.row_count
            yield chunk
            chunk_id += 1

    def _read_chunk(self, chunk_id: int) -> Optional[StreamChunk]:
        """Read a single chunk."""
        cursor = self.conn.cursor()

        if self._last_key == 0:
            sql = f"""
                SELECT TOP {self.chunk_size} *
                FROM ({self.query}) sub
                ORDER BY {self.key_column}
            """
        else:
            sql = f"""
                SELECT TOP {self.chunk_size} *
                FROM ({self.query}) sub
                WHERE {self.key_column} > {self._last_key}
                ORDER BY {self.key_column}
            """

        try:
            cursor.execute(sql)
            rows = cursor.fetchall()

            if not rows:
                return None

            start_key = int(rows[0][0]) if rows else 0
            end_key = int(rows[-1][0]) if rows else 0

            return StreamChunk(
                chunk_id=chunk_id,
                rows=rows,
                start_key=start_key,
                end_key=end_key,
                row_count=len(rows),
            )
        except Exception:
            return None

    @property
    def stats(self) -> dict:
        """Get reader statistics."""
        return {
            "total_rows": self._total_rows,
            "last_key": self._last_key,
            "chunk_size": self.chunk_size,
        }


class StreamingWriter:
    """
    Stream data to PostgreSQL using COPY.

    Features:
    - Batch writing
    - Progress tracking
    - Error handling
    """

    def __init__(
        self,
        conn,
        table_name: str,
        columns: List[str],
        batch_size: int = 10000,
    ):
        self.conn = conn
        self.table_name = table_name
        self.columns = columns
        self.batch_size = batch_size
        self._total_written = 0
        self._batch_count = 0

    def write_rows(self, rows: List[List]) -> int:
        """
        Write rows to PostgreSQL.

        Returns number of rows written.
        """
        if not rows:
            return 0

        cursor = self.conn.cursor()
        col_names = ", ".join(self.columns)

        # Build COPY buffer
        buffer = io.StringIO()
        for row in rows:
            line = "\t".join(
                str(v) if v is not None else "\\N"
                for v in row
            )
            buffer.write(line + "\n")

        # Execute COPY
        copy_sql = f"""
            COPY {self.table_name} ({col_names})
            FROM STDIN WITH (FORMAT text, DELIMITER E'\\t', NULL E'\\\\N')
        """
        buffer.seek(0)
        cursor.copy_expert(copy_sql, buffer)

        written = len(rows)
        self._total_written += written
        self._batch_count += 1

        return written

    @property
    def stats(self) -> dict:
        """Get writer statistics."""
        return {
            "total_written": self._total_written,
            "batch_count": self._batch_count,
            "batch_size": self.batch_size,
        }


class StreamingPipeline:
    """
    End-to-end streaming pipeline: Read → Transform → Write.

    Memory-efficient for tables with billions of rows.
    """

    def __init__(
        self,
        reader: StreamingReader,
        writer: StreamingWriter,
        transform_fn: Optional[Callable] = None,
        log_func: Optional[Callable] = None,
    ):
        self.reader = reader
        self.writer = writer
        self.transform_fn = transform_fn
        self.log_func = log_func
        self._start_time = None

    def run(self) -> dict:
        """Execute the streaming pipeline."""
        self._start_time = time.time()
        total_rows = 0

        for chunk in self.reader.read_chunks():
            rows = chunk.rows

            # Apply transformation
            if self.transform_fn:
                rows = [self.transform_fn(row) for row in rows]

            # Write
            written = self.writer.write_rows(rows)
            total_rows += written

            if self.log_func and chunk.chunk_id % 10 == 0:
                elapsed = time.time() - self._start_time
                speed = total_rows / elapsed if elapsed > 0 else 0
                self.log_func(
                    f"  Streaming: chunk {chunk.chunk_id}, "
                    f"{total_rows:,} rows, {speed:,.0f} rows/s"
                )

        elapsed = time.time() - self._start_time
        speed = total_rows / elapsed if elapsed > 0 else 0

        return {
            "total_rows": total_rows,
            "elapsed_seconds": round(elapsed, 1),
            "speed_rows_per_sec": round(speed, 0),
        }
