"""Logging configuration."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from agentfactor import constants
from agentfactor.utils.pathing import ensure_runtime_directories


_POLLING_404_SUFFIXES = (
    "/context-pack/latest",
    "/compaction/latest",
    "/blueprint-file",
    "/work-graph/blueprint/",
)


class _SuppressPolling404(logging.Filter):
    """Drop uvicorn access-log lines for polling endpoints that return 404."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if '" 404' in msg:
            return not any(suffix in msg for suffix in _POLLING_404_SUFFIXES)
        return True


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root logging with console + file handlers."""
    ensure_runtime_directories()
    log_dir = constants.LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "agentfactor.log"

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=5)
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(console_handler)
    root.addHandler(file_handler)

    # Suppress log noise from polling endpoints that legitimately return 404
    logging.getLogger("uvicorn.access").addFilter(_SuppressPolling404())
