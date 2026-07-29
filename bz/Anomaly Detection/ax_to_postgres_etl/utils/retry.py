"""
Retry utilities for transient error handling.

Retry policy:
  - Повторять: обрыв соединения, timeout, deadlock, временная недоступность PostgreSQL, network reset
  - НЕ повторять: SQL-синтаксис, несовместимый тип, отсутствующая колонка, нарушение уникальности, неверная конфигурация
  - Задержки: 5s → 15s → 30s → 60s (exponential backoff)
  - Максимум попыток: 3
"""

import time
import logging
from functools import wraps
from typing import Type, Tuple, Optional, Callable, TypeVar

T = TypeVar('T')

logger = logging.getLogger("etl.retry")

# --- Delay schedule ---
RETRY_DELAYS = [5, 15, 30, 60]  # seconds
MAX_RETRIES = 3


def is_transient_error(exc: Exception) -> bool:
    """
    Определить, является ли ошибка временной и стоит ли повторять.

    Повторять:
      - Обрыв соединения
      - Timeout
      - Deadlock
      - Временная недоступность PostgreSQL
      - Network reset

    НЕ повторять:
      - SQL-синтаксис
      - Несовместимый тип
      - Отсутствующая колонка
      - Нарушение уникального ограничения
      - Неверная конфигурация
    """
    # Python exception type check
    transient_types = (
        ConnectionError,
        ConnectionRefusedError,
        ConnectionResetError,
        TimeoutError,
        OSError,
    )
    if isinstance(exc, transient_types):
        return True

    # Check for common DB transient error patterns
    exc_str = str(exc).lower()
    transient_patterns = [
        "could not connect",
        "connection refused",
        "connection timed out",
        "timeout expired",
        "deadlock detected",
        "server closed the connection",
        "connection reset",
        "temporary failure",
        "too many connections",
        "network is unreachable",
        "no route to host",
        "connection slot reserved",
        "still starting up",
        "the database system is starting up",
        "the database system is shutting down",
        "could not receive data from server",
    ]

    # Check for non-transient patterns (should NOT retry)
    non_transient_patterns = [
        "syntax error",
        "does not exist",
        "column",
        "permission denied",
        "violates",
        "duplicate key",
        "unique constraint",
        "invalid input syntax",
        "type mismatch",
        "relation",
    ]

    # If it matches a non-transient pattern, don't retry
    for pattern in non_transient_patterns:
        if pattern in exc_str:
            return False

    return any(pattern in exc_str for pattern in transient_patterns)


def get_retry_delay(attempt: int) -> float:
    """Получить задержку для попытки (exponential backoff)."""
    if attempt < len(RETRY_DELAYS):
        return RETRY_DELAYS[attempt]
    return RETRY_DELAYS[-1]


def retry_on_error(
    max_retries: int = MAX_RETRIES,
    delay: float = None,
    backoff_factor: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    on_retry: Optional[Callable] = None,
):
    """
    Декоратор повтора при временных ошибках.

    Использует is_transient_error() для определения типа ошибки.
    Непередающие ошибки (syntax error, permission denied) выбрасываются сразу.

    Args:
        max_retries: Максимальное количество повторов (по умолчанию 3)
        delay: Начальная задержка (если None, используется RETRY_DELAYS)
        backoff_factor: Множитель задержки (по умолчанию 2.0)
        exceptions: Кортеж типов исключений для перехвата
        on_retry: Callback(attempt_number, exception) при каждом повторе

    Example:
        @retry_on_error(max_retries=3, exceptions=(ConnectionError,))
        def connect_to_db():
            ...
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e

                    # Don't retry non-transient errors
                    if not is_transient_error(e):
                        raise

                    if attempt < max_retries:
                        retry_delay = get_retry_delay(attempt)
                        logger.warning(
                            f"Attempt {attempt + 1}/{max_retries + 1} failed: {type(e).__name__}: {e}. "
                            f"Retrying in {retry_delay:.1f}s..."
                        )

                        if on_retry:
                            on_retry(attempt + 1, e)

                        time.sleep(retry_delay)
                    else:
                        logger.error(
                            f"All {max_retries + 1} attempts failed: {type(e).__name__}: {e}"
                        )

            raise last_exception

        return wrapper

    return decorator
