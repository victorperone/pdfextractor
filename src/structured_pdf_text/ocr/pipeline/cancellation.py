"""Thread-safe, monotonic cancellation signal for one extraction operation."""
from __future__ import annotations

from threading import Event, Lock
from collections.abc import Callable

from .budgets import WorkCancelled


class CancellationToken:
    def __init__(self):
        self._event = Event()
        self._lock = Lock()
        self._callbacks: list[Callable[[], None]] = []

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> bool:
        with self._lock:
            if self._event.is_set():
                return False
            self._event.set()
            callbacks = tuple(self._callbacks)
            self._callbacks.clear()
        for callback in callbacks:
            try:
                callback()
            except Exception:
                pass
        return True

    def add_callback(self, callback: Callable[[], None]) -> None:
        with self._lock:
            if not self._event.is_set():
                self._callbacks.append(callback)
                return
        callback()

    def check(self) -> None:
        if self._event.is_set():
            raise WorkCancelled("Extraction was cancelled by its caller")
