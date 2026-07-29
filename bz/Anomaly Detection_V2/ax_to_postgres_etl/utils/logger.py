"""
ETL-specific logging utilities.
Provides structured logging with file + console output.
"""

import os
import datetime
import logging
import sys
from typing import Optional, Callable


def setup_etl_logging(log_dir: str = "logs", level: str = "INFO") -> logging.Logger:
    """
    Setup ETL logging with file + console output.
    
    Returns:
        Configured logger instance
    """
    os.makedirs(log_dir, exist_ok=True)
    
    logger = logging.getLogger("etl")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    
    # Prevent duplicate handlers
    if logger.handlers:
        return logger
    
    # Console handler (human-readable)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_format = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)
    
    # File handler (structured)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_dir, f"etl_{timestamp}.log")
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(module)s.%(funcName)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(file_format)
    logger.addHandler(file_handler)
    
    return logger


def log_message(msg: str, logger: Optional[logging.Logger] = None, level: str = "INFO"):
    """
    Legacy compatibility: log a message.
    New code should use logger.info() directly.
    """
    if logger:
        logger.log(getattr(logging, level.upper(), logging.INFO), msg)
    else:
        print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def log_error(msg: str, logger: Optional[logging.Logger] = None, exc_info: bool = True):
    """
    Legacy compatibility: log an error.
    New code should use logger.error() directly.
    """
    if logger:
        logger.error(msg, exc_info=exc_info)
    else:
        import traceback
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] ERROR: {msg}"
        if exc_info:
            line += f"\n{traceback.format_exc()}"
        print(line)
