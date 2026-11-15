"""Application logging and crash handling.

Logs go to ``~/.klinescan/logs/klinescan.log`` (rotated, 5 x 1 MB) so field
failures can be diagnosed; uncaught exceptions on any thread are logged with a
traceback and shown to the user instead of silently killing the app.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import threading
from typing import Callable, Optional

LOG_DIR = os.path.join(os.path.expanduser("~"), ".klinescan", "logs")
LOG_FILE = os.path.join(LOG_DIR, "klinescan.log")


def setup_logging(level: int = logging.INFO, log_file: str = LOG_FILE) -> str:
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(threadName)s %(name)s: %(message)s")
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    console.setFormatter(fmt)
    root.handlers[:] = [file_handler, console]
    logging.getLogger(__name__).info("KLineScan logging to %s", log_file)
    return log_file


def install_crash_handler(notify: Optional[Callable[[str], None]] = None) -> None:
    """Log uncaught exceptions (main and worker threads) and optionally notify the user."""
    log = logging.getLogger("klinescan.crash")

    def handle(exc_type, exc, tb, where="main thread"):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        log.critical("Uncaught exception in %s", where, exc_info=(exc_type, exc, tb))
        if notify is not None:
            try:
                notify(f"{exc_type.__name__}: {exc}\n\nDetails were written to {LOG_FILE}")
            except Exception:
                pass

    sys.excepthook = handle
    threading.excepthook = lambda args: handle(
        args.exc_type, args.exc_value, args.exc_traceback, getattr(args.thread, "name", "thread"))
