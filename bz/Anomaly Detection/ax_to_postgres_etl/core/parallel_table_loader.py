"""Parallel loader for multiple tables."""

import time
import threading
from typing import List, Optional, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from ax_to_postgres_etl.domain import LoadResult, LoadStatus


class ParallelTableLoader:
    """
    Load multiple tables in parallel using separate thread pools.

    Each table gets its own ParallelLoaderV2 instance with isolated state.
    """

    def __init__(
        self,
        max_tables: int = 3,
        log_func: Optional[Callable] = None,
    ):
        """
        Args:
            max_tables: Maximum concurrent table loads
            log_func: Logging function
        """
        self.max_tables = max_tables
        self.log_func = log_func

    def load_tables(
        self,
        load_fn,
        table_configs: list,
    ) -> List[LoadResult]:
        """
        Load multiple tables in parallel.

        Args:
            load_fn: Callable(table_config) -> LoadResult
            table_configs: List of table config objects

        Returns:
            List of LoadResult for each table
        """
        results = []
        start_time = time.time()

        if self.log_func:
            self.log_func(
                f"PARALLEL TABLE LOAD: {len(table_configs)} tables, "
                f"max_concurrent={self.max_tables}"
            )

        with ThreadPoolExecutor(max_workers=self.max_tables) as executor:
            future_to_table = {
                executor.submit(load_fn, config): config
                for config in table_configs
            }

            for future in as_completed(future_to_table):
                config = future_to_table[future]
                table_name = config.name if hasattr(config, "name") else str(config)
                try:
                    result = future.result()
                    results.append(result)
                    if self.log_func:
                        status = result.status if hasattr(result, "status") else "unknown"
                        self.log_func(
                            f"  {table_name}: {status}"
                        )
                except Exception as e:
                    if self.log_func:
                        self.log_func(f"  {table_name}: FAILED — {e}")
                    results.append(LoadResult(
                        table_name=table_name,
                        status=LoadStatus.FAILED,
                        error_message=str(e),
                    ))

        elapsed = time.time() - start_time
        success_count = sum(
            1 for r in results
            if hasattr(r, "status") and r.status == LoadStatus.SUCCESS
        )

        if self.log_func:
            self.log_func(
                f"PARALLEL TABLE LOAD DONE: {success_count}/{len(table_configs)} "
                f"succeeded in {elapsed:.1f}s"
            )

        return results
