"""Atomic hierarchical accounting; work is cumulative, live bytes are released."""
from threading import RLock

from structured_pdf_text.config import SecurityLimits
from structured_pdf_text.errors import ExtractionCancelled
from .contracts import ResourceAllocation, integer


class BudgetBlocked(RuntimeError):
    pass


class WorkCancelled(ExtractionCancelled):
    pass


class RasterReservation:
    def __init__(self, pool, byte_count):
        self._pool = pool
        self._bytes = byte_count
        self._released = False

    def retain_rgb(self, width: int, height: int):
        """Release temporary copies after publishing the final owned RGB image."""
        integer(width, minimum=1)
        integer(height, minimum=1)
        byte_count = width * height * 3
        with self._pool._lock:
            if self._released or byte_count > self._bytes:
                raise ValueError("Invalid reservation reduction")
            for pool in self._pool._chain():
                pool._live_bytes -= self._bytes - byte_count
            self._bytes = byte_count

    def release(self):
        with self._pool._lock:
            if not self._released:
                for pool in self._pool._chain():
                    pool._live_bytes -= self._bytes
                self._released = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.release()


class ResourcePool:
    def __init__(self, allocation: ResourceAllocation, limits: SecurityLimits, *, parent=None):
        if not isinstance(allocation, ResourceAllocation) or not isinstance(limits, SecurityLimits):
            raise ValueError("Expected immutable resource allocation and security limits")
        self.allocation = allocation
        self.limits = limits
        self._parent = parent
        self._lock = parent._lock if parent is not None else RLock()
        self._live_bytes = self._peak_bytes = self._work_pixels = self._rasters = self._attempts = 0

    def _chain(self):
        current = self
        while current is not None:
            yield current
            current = current._parent

    def child(self, allocation: ResourceAllocation):
        return ResourcePool(allocation, self.limits, parent=self)

    def reserve_raster(self, width: int, height: int, *, copies: int = 1) -> RasterReservation:
        for value in (width, height, copies):
            integer(value, minimum=1)
        pixels = width * height
        byte_count = pixels * 3 * copies
        with self._lock:
            for pool in self._chain():
                a, limits = pool.allocation, pool.limits
                if (pixels > min(a.max_raster_pixels, limits.max_render_pixels)
                        or pixels * 3 > limits.max_render_bytes
                        or pool._live_bytes + byte_count > min(a.max_live_raster_bytes, limits.max_render_bytes)
                        or pool._work_pixels + pixels > a.max_work_pixels
                        or pool._rasters + 1 > a.max_rasters):
                    raise BudgetBlocked("Raster memory, pixels or cumulative work budget exhausted")
            for pool in self._chain():
                pool._live_bytes += byte_count
                pool._peak_bytes = max(pool._peak_bytes, pool._live_bytes)
                pool._work_pixels += pixels
                pool._rasters += 1
        return RasterReservation(self, byte_count)

    def consume_attempt(self) -> int:
        with self._lock:
            if any(pool._attempts >= pool.allocation.max_attempts for pool in self._chain()):
                raise BudgetBlocked("Inference attempt budget exhausted")
            for pool in self._chain():
                pool._attempts += 1
            return self._attempts

    def observations(self):
        with self._lock:
            return {"live_raster_bytes": self._live_bytes, "peak_reserved_raster_bytes": self._peak_bytes,
                    "work_pixels": self._work_pixels, "rasters": self._rasters, "attempts": self._attempts}
