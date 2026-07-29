"""Webhook notifications for ETL progress."""

import json
import urllib.request
import urllib.error
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Callable


@dataclass
class WebhookConfig:
    """Webhook configuration."""
    url: str
    enabled: bool = True
    on_start: bool = True
    on_progress: bool = True  # Every N chunks
    on_complete: bool = True
    on_error: bool = True
    progress_interval: int = 10  # Notify every N chunks
    timeout: int = 10  # seconds
    headers: Optional[dict] = None


class WebhookNotifier:
    """
    Send webhook notifications for ETL events.

    Supports:
    - Load start
    - Progress updates (every N chunks)
    - Load complete
    - Error notifications
    """

    def __init__(self, config: Optional[WebhookConfig] = None):
        self.config = config
        self._last_progress_chunk = 0

    def _send(self, payload: dict):
        """Send webhook payload."""
        if not self.config or not self.config.enabled:
            return

        try:
            data = json.dumps(payload, default=str).encode("utf-8")
            req = urllib.request.Request(
                self.config.url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    **(self.config.headers or {}),
                },
                method="POST",
            )
            urllib.request.urlopen(req, timeout=self.config.timeout)
        except Exception:
            pass  # Non-critical, don't break ETL

    def notify_start(
        self,
        table_name: str,
        load_mode: str,
        chunk_count: int,
        workers: int,
    ):
        """Notify load started."""
        if not self.config or not self.config.on_start:
            return

        self._send({
            "event": "etl_start",
            "timestamp": datetime.now().isoformat(),
            "table": table_name,
            "mode": load_mode,
            "chunks": chunk_count,
            "workers": workers,
        })

    def notify_progress(
        self,
        table_name: str,
        chunk_id: int,
        chunks_completed: int,
        chunks_total: int,
        rows_inserted: int,
    ):
        """Notify progress update."""
        if not self.config or not self.config.on_progress:
            return

        # Only notify at intervals
        if chunks_completed - self._last_progress_chunk < self.config.progress_interval:
            return

        self._last_progress_chunk = chunks_completed
        pct = (chunks_completed / chunks_total * 100) if chunks_total > 0 else 0

        self._send({
            "event": "etl_progress",
            "timestamp": datetime.now().isoformat(),
            "table": table_name,
            "chunk_id": chunk_id,
            "chunks_completed": chunks_completed,
            "chunks_total": chunks_total,
            "progress_pct": round(pct, 1),
            "rows_inserted": rows_inserted,
        })

    def notify_complete(
        self,
        table_name: str,
        status: str,
        rows_inserted: int,
        elapsed: float,
        chunks_completed: int,
        chunks_total: int,
    ):
        """Notify load completed."""
        if not self.config or not self.config.on_complete:
            return

        speed = rows_inserted / elapsed if elapsed > 0 else 0

        self._send({
            "event": "etl_complete",
            "timestamp": datetime.now().isoformat(),
            "table": table_name,
            "status": status,
            "rows_inserted": rows_inserted,
            "elapsed_seconds": round(elapsed, 1),
            "speed_rows_per_sec": round(speed, 0),
            "chunks_completed": chunks_completed,
            "chunks_total": chunks_total,
        })

    def notify_error(
        self,
        table_name: str,
        error_type: str,
        error_message: str,
    ):
        """Notify error occurred."""
        if not self.config or not self.config.on_error:
            return

        self._send({
            "event": "etl_error",
            "timestamp": datetime.now().isoformat(),
            "table": table_name,
            "error_type": error_type,
            "error_message": error_message[:500],
        })
