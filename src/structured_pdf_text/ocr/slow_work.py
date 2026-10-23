"""Warn about long-running OCR work without ever interrupting it.

No OCR engine, page or document has a deadline. While a unit of work runs, a
warning is logged every ``interval_s`` seconds so an operator can see that it
is slow; the work itself is never cancelled, killed or abandoned.
"""
from __future__ import annotations

from contextlib import contextmanager
import logging
import threading
import time
from typing import Iterator

logger = logging.getLogger(__name__)
DEFAULT_WARNING_INTERVAL_S = 120.0


@contextmanager
def warn_if_slow(label: str, interval_s: float = DEFAULT_WARNING_INTERVAL_S) -> Iterator[None]:
    if not interval_s > 0:
        raise ValueError("Warning interval must be positive")
    stop = threading.Event()
    started = time.monotonic()

    def watch() -> None:
        while not stop.wait(interval_s):
            logger.warning("%s still running after %.0f s; no timeout is applied", label, time.monotonic() - started)

    watcher = threading.Thread(target=watch, name="pdfextractor-slow-work-watch", daemon=True)
    watcher.start()
    try:
        yield
    finally:
        stop.set()
        watcher.join()
