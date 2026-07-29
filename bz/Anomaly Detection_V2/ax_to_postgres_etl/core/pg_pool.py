"""Simple PostgreSQL connection pool for ETL workers."""

import threading
from typing import Optional

import psycopg2


class PgConnectionPool:
    """
    Thread-safe connection pool for PostgreSQL.

    Each worker gets its own connection to avoid sharing connections
    across threads (required for FOR UPDATE SKIP LOCKED correctness).
    """

    def __init__(
        self,
        host: str,
        port: int,
        dbname: str,
        user: str,
        password: str,
        max_connections: int = 16,
    ):
        self._host = host
        self._port = port
        self._dbname = dbname
        self._user = user
        self._password = password
        self._max = max_connections

        self._pool: list[psycopg2.extensions.connection] = []
        self._lock = threading.Lock()

    def get_connection(self) -> psycopg2.extensions.connection:
        """Get a connection from pool or create a new one."""
        with self._lock:
            if self._pool:
                conn = self._pool.pop()
                if not conn.closed:
                    return conn

        # Create new connection outside lock
        return psycopg2.connect(
            host=self._host,
            port=self._port,
            dbname=self._dbname,
            user=self._user,
            password=self._password,
        )

    def return_connection(self, conn: psycopg2.extensions.connection):
        """Return a connection to the pool."""
        if conn.closed:
            return

        with self._lock:
            if len(self._pool) < self._max:
                self._pool.append(conn)
            else:
                conn.close()

    def close_all(self):
        """Close all connections in pool."""
        with self._lock:
            for conn in self._pool:
                try:
                    if not conn.closed:
                        conn.close()
                except Exception:
                    pass
            self._pool.clear()

    @property
    def size(self) -> int:
        """Current pool size."""
        with self._lock:
            return len(self._pool)
