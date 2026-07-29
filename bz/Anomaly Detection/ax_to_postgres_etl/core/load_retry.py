"""Load retry with exponential backoff."""

import time
import random
from dataclasses import dataclass
from typing import Optional, Callable, Any


@dataclass
class RetryConfig:
    """Configuration for load retry."""
    max_retries: int = 3
    initial_delay: float = 10.0  # seconds
    max_delay: float = 300.0  # seconds
    backoff_multiplier: float = 2.0
    jitter: bool = True
    retry_on: tuple = (Exception,)  # Exception types to retry on


class LoadRetry:
    """
    Retry failed ETL loads with exponential backoff.

    Usage:
        retry = LoadRetry(RetryConfig(max_retries=3))
        result = retry.execute(lambda: loader.load_table("ALK_MARKSERIAL"))
    """

    def __init__(self, config: Optional[RetryConfig] = None):
        self.config = config or RetryConfig()

    def _get_delay(self, attempt: int) -> float:
        """Calculate delay with exponential backoff and jitter."""
        delay = self.config.initial_delay * (
            self.config.backoff_multiplier ** (attempt - 1)
        )
        delay = min(delay, self.config.max_delay)

        if self.config.jitter:
            jitter_range = delay * 0.25
            delay = delay + random.uniform(-jitter_range, jitter_range)
            delay = max(0.1, delay)

        return delay

    def execute(
        self,
        func: Callable,
        *args,
        log_func: Optional[Callable] = None,
        **kwargs,
    ) -> Any:
        """
        Execute function with retry logic.

        Args:
            func: Function to execute
            log_func: Optional logging function

        Returns:
            Result of the function

        Raises:
            Last exception if all retries fail
        """
        last_error = None

        for attempt in range(1, self.config.max_retries + 1):
            try:
                result = func(*args, **kwargs)
                if log_func:
                    log_func(f"  RETRY: Attempt {attempt} succeeded")
                return result

            except self.config.retry_on as e:
                last_error = e

                if attempt < self.config.max_retries:
                    delay = self._get_delay(attempt)
                    if log_func:
                        log_func(
                            f"  RETRY: Attempt {attempt} failed: {e}. "
                            f"Retrying in {delay:.1f}s..."
                        )
                    time.sleep(delay)
                else:
                    if log_func:
                        log_func(
                            f"  RETRY: All {self.config.max_retries} attempts failed. "
                            f"Last error: {e}"
                        )

        raise last_error


class LoadWithFallback:
    """
    Try primary load strategy, fallback to alternative on failure.

    Usage:
        strategy = LoadWithFallback(
            primary=lambda: v2_loader.load_table("T", mode="resume"),
            fallback=lambda: v1_loader.load_table("T"),
        )
        result = strategy.execute()
    """

    def __init__(
        self,
        primary: Callable,
        fallback: Callable,
        log_func: Optional[Callable] = None,
    ):
        self.primary = primary
        self.fallback = fallback
        self.log_func = log_func

    def execute(self) -> Any:
        """Try primary, fallback on failure."""
        try:
            if self.log_func:
                self.log_func("  FALLBACK: Trying primary strategy...")
            return self.primary()
        except Exception as e:
            if self.log_func:
                self.log_func(f"  FALLBACK: Primary failed: {e}")
                self.log_func("  FALLBACK: Trying fallback strategy...")
            return self.fallback()
