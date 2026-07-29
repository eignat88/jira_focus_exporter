"""Thread-safe connection pool with health checks."""

import threading
import time
from queue import Queue, Empty
from typing import Optional, Callable
from contextlib import contextmanager

import psycopg2


class PooledConnection:
    """Wrapper around a connection with metadata."""

    def __init__(self, conn, created_at: float):
        self.conn = conn
        self.created_at = created_at
        self.last_used = created_at
        self.in_use = False
        self.use_count = 0

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at

    @property
    def idle_seconds(self) -> float:
        return time.time() - self.last_used


class HealthCheckPool:
    """
    Thread-safe PostgreSQL connection pool with health checks.

    Features:
    - Min/max connection limits
    - Connection recycling (max age)
    - Health checks before returning connections
    - Idle connection cleanup
    - Thread-safe get/return
    """

    def __init__(
        self,
        host: str,
        port: int,
        dbname: str,
        user: str,
        password: str,
        min_connections: int = 2,
        max_connections: int = 16,
        max_age_seconds: int = 3600,
        health_check_interval: int = 60,
    ):
        self._host = host
        self._port = port
        self._dbname = dbname
        self._user = user
        self._password = password
        self._min = min_connections
        self._max = max_connections
        self._max_age = max_age_seconds
        self._health_interval = health_check_interval

        self._pool: Queue = Queue(maxsize=max_connections)
        self._lock = threading.Lock()
        self._total_created = 0
        self._total_reused = 0
        self._total_discarded = 0
        self._last_health_check = time.time()

        # Pre-create minimum connections
        for _ in range(min_connections):
            conn = self._create_connection()
            if conn:
                self._pool.put(conn)

    def _create_connection(self) -> Optional[PooledConnection]:
        """Create a new PostgreSQL connection."""
        try:
            conn = psycopg2.connect(
                host=self._host,
                port=self._port,
                dbname=self._dbname,
                user=self._user,
                password=self._password,
            )
            conn.autocommit = False
            self._total_created += 1
            return PooledConnection(conn, time.time())
        except Exception:
            return None

    def _is_healthy(self, pooled: PooledConnection) -> bool:
        """Check if connection is healthy."""
        try:
            cursor = pooled.conn.cursor()
            cursor.execute("SELECT 1")
            cursor.close()
            return True
        except Exception:
            return False

    def _is_expired(self, pooled: PooledConnection) -> bool:
        """Check if connection is too old."""
        return pooled.age_seconds > self._max_age

    def get(self, timeout: float = 10.0) -> Optional[psycopg2.extensions.connection]:
        """
        Get a connection from the pool.

        Args:
            timeout: Maximum seconds to wait for a connection

        Returns:
            PostgreSQL connection or None
        """
        # Periodic health check
        if time.time() - self._last_health_check > self._health_interval:
            self._run_health_check()

        # Try to get from pool
        try:
            pooled = self._pool.get_nowait()

            # Check health
            if self._is_expired(pooled) or not self._is_healthy(pooled):
                try:
                    pooled.conn.close()
                except Exception:
                    pass
                self._total_discarded += 1
                # Create replacement
                pooled = self._create_connection()
                if not pooled:
                    return None
            else:
                self._total_reused += 1

            pooled.in_use = True
            pooled.last_used = time.time()
            pooled.use_count += 1
            return pooled.conn

        except Empty:
            # Pool empty — create new if under limit
            with self._lock:
                if self._total_created - self._total_discarded < self._max:
                    pooled = self._create_connection()
                    if pooled:
                        pooled.in_use = True
                        pooled.use_count += 1
                        return pooled.conn

            # Wait for one to become available
            try:
                pooled = self._pool.get(timeout=timeout)
                if pooled and self._is_healthy(pooled):
                    pooled.in_use = True
                    pooled.last_used = time.time()
                    pooled.use_count += 1
                    return pooled.conn
            except Empty:
                pass

            return None

    def return_connection(self, conn):
        """Return a connection to the pool."""
        if conn is None:
            return

        # Rollback any uncommitted transaction
        try:
            if not conn.autocommit:
                conn.rollback()
        except Exception:
            pass

        # Try to return to pool
        try:
            pooled = PooledConnection(conn, time.time())
            pooled.in_use = False
            self._pool.put_nowait(pooled)
        except Exception:
            # Pool full or error — close
            try:
                conn.close()
            except Exception:
                pass

    @contextmanager
    def connection(self):
        """Context manager for getting/returning a connection."""
        conn = self.get()
        try:
            yield conn
        finally:
            self.return_connection(conn)

    def _run_health_check(self):
        """Run health check on idle connections."""
        self._last_health_check = time.time()
        healthy = Queue()

        while not self._pool.empty():
            try:
                pooled = self._pool.get_nowait()
                if self._is_healthy(pooled) and not self._is_expired(pooled):
                    healthy.put(pooled)
                else:
                    try:
                        pooled.conn.close()
                    except Exception:
                        pass
                    self._total_discarded += 1
            except Empty:
                break

        # Replace with healthy ones
        while not healthy.empty():
            try:
                self._pool.put_nowait(healthy.get_nowait())
            except Empty:
                break

    @property
    def stats(self) -> dict:
        """Get pool statistics."""
        return {
            "pool_size": self._pool.qsize(),
            "min_connections": self._min,
            "max_connections": self._max,
            "total_created": self._total_created,
            "total_reused": self._total_reused,
            "total_discarded": self._total_discarded,
        }

    def close_all(self):
        """Close all connections in pool."""
        while not self._pool.empty():
            try:
                pooled = self._pool.get_nowait()
                pooled.conn.close()
            except Exception:
                pass
