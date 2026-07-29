"""
Structured logging for WMS Anomaly Detection.

Usage:
    from src.utils.logger import get_logger
    logger = get_logger(__name__)
    logger.info("Processing started", extra={"table": "INVENTTABLE", "batch": 1})

Output:
    Console: 2024-01-15 10:30:00 | INFO | Processing started | table=INVENTTABLE batch=1
    File (JSON): {"timestamp": "2024-01-15T10:30:00", "level": "INFO", "message": "Processing started", "table": "INVENTTABLE", "batch": 1}
"""

import json
import logging
import os
import sys
import datetime
from typing import Optional


class JSONFormatter(logging.Formatter):
    """JSON formatter for machine-readable logs."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "level": record.levelname,
            "module": record.module,
            "function": record.funcName,
            "message": record.getMessage(),
        }

        # Add extra fields
        if hasattr(record, "extra_data"):
            log_entry.update(record.extra_data)

        # Add exception info if present
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": self.formatException(record.exc_info),
            }

        return json.dumps(log_entry, ensure_ascii=False, default=str)


class ConsoleFormatter(logging.Formatter):
    """Human-readable console formatter."""

    COLORS = {
        "DEBUG": "\033[36m",     # Cyan
        "INFO": "\033[32m",      # Green
        "WARNING": "\033[33m",   # Yellow
        "ERROR": "\033[31m",     # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "")
        reset = self.RESET if color else ""

        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        level = f"{color}{record.levelname:8s}{reset}"
        module = f"{record.module}.{record.funcName}"

        msg = record.getMessage()

        # Add extra fields as key=value
        extras = ""
        if hasattr(record, "extra_data"):
            extras = " | " + " ".join(
                f"{k}={v}" for k, v in record.extra_data.items()
            )

        return f"{timestamp} | {level} | {module} | {msg}{extras}"


class ETLLogger:
    """
    Structured logger for ETL operations.
    
    Provides logging with context (table name, batch number, etc.)
    and outputs to both console and file.
    """

    def __init__(
        self,
        name: str,
        log_dir: str = "logs",
        level: str = "INFO",
        json_format: bool = True,
        console_output: bool = True,
        file_output: bool = True,
    ):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        self.logger.handlers.clear()

        if console_output:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(ConsoleFormatter())
            self.logger.addHandler(console_handler)

        if file_output:
            os.makedirs(log_dir, exist_ok=True)
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            log_file = os.path.join(log_dir, f"{name}_{timestamp}.log")

            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            if json_format:
                file_handler.setFormatter(JSONFormatter())
            else:
                file_handler.setFormatter(ConsoleFormatter())
            self.logger.addHandler(file_handler)

        self._log_file = log_file if file_output else None

    def _log(self, level: str, msg: str, **extra):
        """Internal log method with extra context."""
        record = self.logger.makeRecord(
            name=self.logger.name,
            level=getattr(logging, level.upper()),
            fn="",
            lno=0,
            msg=msg,
            args=(),
            exc_info=None,
        )
        record.extra_data = extra
        self.logger.handle(record)

    def info(self, msg: str, **extra):
        self._log("INFO", msg, **extra)

    def warning(self, msg: str, **extra):
        self._log("WARNING", msg, **extra)

    def error(self, msg: str, exc_info: bool = False, **extra):
        self._log("ERROR", msg, exc_info=exc_info, **extra)

    def debug(self, msg: str, **extra):
        self._log("DEBUG", msg, **extra)

    def critical(self, msg: str, **extra):
        self._log("CRITICAL", msg, **extra)

    def table_start(self, table_name: str, **extra):
        """Log ETL table processing start."""
        self.info(f"Starting ETL for {table_name}", table=table_name, event="start", **extra)

    def table_done(self, table_name: str, rows: int, elapsed: float, **extra):
        """Log ETL table processing completion."""
        self.info(
            f"Completed ETL for {table_name}: {rows:,} rows in {elapsed:.1f}s",
            table=table_name,
            event="done",
            rows=rows,
            elapsed=round(elapsed, 1),
            **extra,
        )

    def table_error(self, table_name: str, error: str, **extra):
        """Log ETL table processing error."""
        self.error(
            f"ETL failed for {table_name}: {error}",
            table=table_name,
            event="error",
            error=error,
            **extra,
        )

    def batch_log(self, table_name: str, batch_num: int, rows: int, speed: float, **extra):
        """Log batch processing progress."""
        self.debug(
            f"Batch {batch_num}: {rows:,} rows @ {speed:,.0f} rows/sec",
            table=table_name,
            event="batch",
            batch=batch_num,
            rows=rows,
            speed=round(speed, 0),
            **extra,
        )


def get_logger(
    name: str = "wms",
    log_dir: Optional[str] = None,
    level: Optional[str] = None,
    json_format: Optional[bool] = None,
    console_output: Optional[bool] = None,
    file_output: Optional[bool] = None,
) -> ETLLogger:
    """
    Get a configured logger instance.
    
    Uses settings from configs.settings if available, otherwise defaults.
    """
    try:
        from configs.settings import get_settings
        settings = get_settings()
        log_dir = log_dir or settings.logging.log_dir
        level = level or settings.logging.level
        json_format = json_format if json_format is not None else settings.logging.json_format
        console_output = console_output if console_output is not None else settings.logging.console_output
        file_output = file_output if file_output is not None else settings.logging.file_output
    except ImportError:
        log_dir = log_dir or "logs"
        level = level or "INFO"
        json_format = json_format if json_format is not None else True
        console_output = console_output if console_output is not None else True
        file_output = file_output if file_output is not None else True

    return ETLLogger(
        name=name,
        log_dir=log_dir,
        level=level,
        json_format=json_format,
        console_output=console_output,
        file_output=file_output,
    )
