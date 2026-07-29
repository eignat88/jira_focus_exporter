"""Batch-level retry for failed COPY operations."""

import time
import io
from dataclasses import dataclass
from typing import List, Optional, Callable

import psycopg2


@dataclass
class BatchRetryConfig:
    """Configuration for batch retry."""
    max_retries: int = 3
    initial_delay: float = 1.0
    backoff_multiplier: float = 2.0
    retry_on_copy_error: bool = True
    retry_on_insert_error: bool = True


class BatchRetryHandler:
    """
    Handle retry logic at the batch level.

    When a COPY or INSERT fails for a batch:
    1. Retry the entire batch up to N times
    2. If still fails, split batch and retry smaller chunks
    3. If individual rows fail, log to dead letter queue
    """

    def __init__(self, config: Optional[BatchRetryConfig] = None):
        self.config = config or BatchRetryConfig()
        self._retry_stats = {}

    def _get_delay(self, attempt: int) -> float:
        """Calculate retry delay."""
        delay = self.config.initial_delay * (
            self.config.backoff_multiplier ** (attempt - 1)
        )
        return min(delay, 30.0)

    def execute_batch_copy(
        self,
        cursor,
        copy_sql: str,
        content: str,
        batch_num: int = 0,
        log_func: Optional[Callable] = None,
    ) -> bool:
        """
        Execute COPY with retry logic.

        Returns True if successful, False if all retries exhausted.
        """
        for attempt in range(1, self.config.max_retries + 1):
            try:
                buffer = io.StringIO(content)
                cursor.copy_expert(copy_sql, buffer)
                return True
            except Exception as e:
                if not self.config.retry_on_copy_error:
                    raise

                if attempt < self.config.max_retries:
                    delay = self._get_delay(attempt)
                    if log_func:
                        log_func(
                            f"  BATCH RETRY: Batch {batch_num}, attempt {attempt} "
                            f"failed: {e}. Retrying in {delay:.1f}s..."
                        )
                    time.sleep(delay)
                else:
                    if log_func:
                        log_func(
                            f"  BATCH RETRY: Batch {batch_num} failed after "
                            f"{self.config.max_retries} attempts: {e}"
                        )
                    return False

        return False

    def execute_batch_insert(
        self,
        cursor,
        insert_sql: str,
        params: tuple,
        batch_num: int = 0,
        log_func: Optional[Callable] = None,
    ) -> bool:
        """
        Execute INSERT with retry logic.

        Returns True if successful, False if all retries exhausted.
        """
        for attempt in range(1, self.config.max_retries + 1):
            try:
                cursor.execute(insert_sql, params)
                return True
            except Exception as e:
                if not self.config.retry_on_insert_error:
                    raise

                if attempt < self.config.max_retries:
                    delay = self._get_delay(attempt)
                    if log_func:
                        log_func(
                            f"  BATCH RETRY: Insert batch {batch_num}, attempt {attempt} "
                            f"failed: {e}. Retrying in {delay:.1f}s..."
                        )
                    time.sleep(delay)
                else:
                    if log_func:
                        log_func(
                            f"  BATCH RETRY: Insert batch {batch_num} failed after "
                            f"{self.config.max_retries} attempts: {e}"
                        )
                    return False

        return False

    def split_and_retry(
        self,
        cursor,
        copy_sql: str,
        content: str,
        batch_num: int = 0,
        min_chunk_size: int = 100,
        log_func: Optional[Callable] = None,
    ) -> tuple:
        """
        Split failed batch into smaller chunks and retry.

        Returns:
            (success_count, fail_count)
        """
        lines = content.strip().split("\n")

        if len(lines) <= min_chunk_size:
            # Can't split further
            success = self.execute_batch_copy(
                cursor, copy_sql, content, batch_num, log_func
            )
            return (1 if success else 0, 0 if success else 1)

        # Split in half
        mid = len(lines) // 2
        part1 = "\n".join(lines[:mid]) + "\n"
        part2 = "\n".join(lines[mid:]) + "\n"

        s1, f1 = self.split_and_retry(
            cursor, copy_sql, part1, batch_num, min_chunk_size, log_func
        )
        s2, f2 = self.split_and_retry(
            cursor, copy_sql, part2, batch_num, min_chunk_size, log_func
        )

        return (s1 + s2, f1 + f2)

    @property
    def stats(self) -> dict:
        """Get retry statistics."""
        return self._retry_stats
