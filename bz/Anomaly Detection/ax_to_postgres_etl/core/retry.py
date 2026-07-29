"""Retry policy for ETL operations."""

import random
from dataclasses import dataclass
from typing import Optional
import re


@dataclass
class RetryConfig:
    """Retry configuration."""
    max_attempts: int = 5
    initial_delay_seconds: float = 5.0
    max_delay_seconds: float = 300.0
    backoff_multiplier: float = 2.0
    jitter: bool = True  # Add randomness to prevent thundering herd


# Error patterns that should be retried
RETRIABLE_PATTERNS = [
    r"connection.*timeout",
    r"connection.*reset",
    r"connection.*refused",
    r"connection.*lost",
    r"network.*error",
    r"temporary.*failure",
    r"deadlock",
    r"lock.*timeout",
    r"server.*closed.*connection",
    r"could not.*connect",
    r"ODBC.*error.*10054",
    r"ODBC.*error.*10053",
    r"ODBC.*error.*10060",
    # Extended patterns from stabilization doc §8
    r"10054",
    r"10053",
    r"10060",
    r"08s01",
    r"connectionread",
    r"communication link failure",
    r"dbnetlib",
    r"общая ошибка сети",
]

# Error types that should NOT be retried
NON_RETRIABLE_ERRORS = [
    "column.*does not exist",
    "relation.*does not exist",
    "invalid.*input.*syntax",
    "duplicate.*key.*value",
    "violates.*foreign key",
    "violates.*not-null",
    "violates.*unique constraint",
    "schema.*mismatch",
    "type.*mismatch",
    "missing.*table",
    "missing.*column",
]


class RetryPolicy:
    """Retry policy with exponential backoff."""

    def __init__(self, config: Optional[RetryConfig] = None):
        """
        Initialize RetryPolicy.
        
        Args:
            config: Retry configuration (uses defaults if None)
        """
        self.config = config or RetryConfig()

    def should_retry(self, error: Exception, attempt_count: int) -> bool:
        """
        Determine if an error should be retried.
        
        Args:
            error: The exception that occurred
            attempt_count: Current attempt number (1-based)
            
        Returns:
            True if should retry, False otherwise
        """
        # Check attempt limit
        if attempt_count >= self.config.max_attempts:
            return False
        
        # Check if error is non-retriable
        error_str = str(error).lower()
        for pattern in NON_RETRIABLE_ERRORS:
            if re.search(pattern, error_str, re.IGNORECASE):
                return False
        
        # Check if error is retriable
        for pattern in RETRIABLE_PATTERNS:
            if re.search(pattern, error_str, re.IGNORECASE):
                return True
        
        # Default: don't retry unknown errors
        return False

    def get_delay(self, attempt_count: int) -> float:
        """
        Get delay in seconds before next retry.

        Args:
            attempt_count: Current attempt number (1-based)

        Returns:
            Delay in seconds
        """
        delay = self.config.initial_delay_seconds * (
            self.config.backoff_multiplier ** (attempt_count - 1)
        )
        delay = min(delay, self.config.max_delay_seconds)

        # Add jitter to prevent thundering herd (±25%)
        if self.config.jitter:
            jitter_range = delay * 0.25
            delay = delay + random.uniform(-jitter_range, jitter_range)
            delay = max(0.1, delay)  # Never negative

        return delay

    def get_error_type(self, error: Exception) -> str:
        """
        Classify error type.
        
        Args:
            error: The exception that occurred
            
        Returns:
            Error type string
        """
        error_str = str(error).lower()
        
        if "timeout" in error_str or "timed out" in error_str:
            return "timeout"
        elif "connection" in error_str:
            return "connection"
        elif "network" in error_str:
            return "network"
        elif "deadlock" in error_str:
            return "deadlock"
        elif "column" in error_str and "does not exist" in error_str:
            return "schema"
        elif "relation" in error_str and "does not exist" in error_str:
            return "schema"
        elif "duplicate" in error_str or "unique" in error_str:
            return "constraint"
        elif "encoding" in error_str or "character" in error_str:
            return "encoding"
        elif "permission" in error_str or "access denied" in error_str:
            return "permission"
        else:
            return "unknown"

    def is_retriable(self, error: Exception) -> bool:
        """
        Check if error is retriable (regardless of attempt count).
        
        Args:
            error: The exception that occurred
            
        Returns:
            True if error is retriable
        """
        error_str = str(error).lower()
        
        # Check non-retriable first
        for pattern in NON_RETRIABLE_ERRORS:
            if re.search(pattern, error_str, re.IGNORECASE):
                return False
        
        # Check retriable patterns
        for pattern in RETRIABLE_PATTERNS:
            if re.search(pattern, error_str, re.IGNORECASE):
                return True
        
        return False
