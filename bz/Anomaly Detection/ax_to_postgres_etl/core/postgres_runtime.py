from __future__ import annotations

import logging
import threading
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Callable, Optional

import psycopg2


@dataclass(frozen=True)
class ConnectionNames:
    data: str
    metadata: str
    heartbeat: str
    lock: str


class PostgresRuntime(AbstractContextManager):
    """Owns isolated PostgreSQL connections for one pipeline run."""

    def __init__(self, dsn: str, pipeline: str, run_ref: str):
        self.dsn = dsn
        self.names = ConnectionNames(
            data=f"{pipeline}_data_{run_ref}",
            metadata=f"{pipeline}_metadata_{run_ref}",
            heartbeat=f"{pipeline}_heartbeat_{run_ref}",
            lock=f"{pipeline}_lock_{run_ref}",
        )
        self.data = None
        self.metadata = None
        self.lock = None

    def _connect(self, application_name: str, autocommit: bool = False):
        conn = psycopg2.connect(self.dsn, application_name=application_name)
        conn.autocommit = autocommit
        return conn

    def __enter__(self):
        self.data = self._connect(self.names.data)
        self.metadata = self._connect(self.names.metadata)
        self.lock = self._connect(self.names.lock, autocommit=True)
        return self

    def heartbeat_connection(self):
        return self._connect(self.names.heartbeat, autocommit=True)

    def __exit__(self, exc_type, exc, tb):
        for conn in (self.data, self.metadata, self.lock):
            try:
                if conn and not conn.closed:
                    conn.close()
            except Exception:
                logging.exception("Failed to close PostgreSQL connection")
        return False


class HeartbeatThread(threading.Thread):
    """Updates run heartbeat on its own connection and transaction."""

    def __init__(
        self,
        connection_factory: Callable,
        update_sql: str,
        params: tuple,
        interval_seconds: int = 15,
    ):
        super().__init__(daemon=True, name="etl-heartbeat")
        self._factory = connection_factory
        self._sql = update_sql
        self._params = params
        self._interval = interval_seconds
        self._stop_event = threading.Event()
        self.last_error: Optional[BaseException] = None

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        conn = None
        try:
            conn = self._factory()
            while not self._stop_event.is_set():
                try:
                    with conn.cursor() as cur:
                        cur.execute(self._sql, self._params)
                    self.last_error = None
                except Exception as exc:
                    self.last_error = exc
                    logging.exception("Heartbeat update failed; reconnecting")
                    try:
                        conn.close()
                    except Exception:
                        pass
                    time.sleep(1)
                    conn = self._factory()
                self._stop_event.wait(self._interval)
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    logging.exception("Failed to close heartbeat connection")
