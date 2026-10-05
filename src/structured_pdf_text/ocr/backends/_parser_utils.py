"""Shared validation for quadrilateral OCR parser outputs."""
from __future__ import annotations

import hashlib
import math
import threading
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np


_HASH_CACHE: dict[tuple[str, int, int], str] = {}
_HASH_LOCK = threading.Lock()


def sha256_file(path: "str | Path") -> str | None:
    """Return the SHA-256 hex digest of a file, or None if it does not exist.

    Reads in 1 MB chunks to avoid loading large model files into memory at once.
    Returns None silently when the path is missing or unreadable so callers can
    build artifact_hashes dicts without conditional guards.
    """
    try:
        p = Path(path)
        if not p.is_file():
            return None
        stat = p.stat()
        key = (str(p.resolve()), stat.st_size, stat.st_mtime_ns)
        with _HASH_LOCK:
            cached = _HASH_CACHE.get(key)
        if cached is not None:
            return cached
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        digest = h.hexdigest()
        with _HASH_LOCK:
            _HASH_CACHE[key] = digest
        return digest
    except OSError:
        return None


def quadrilateral_geometry(value: Any, offset_x: float = 0.0, offset_y: float = 0.0):
    """Return a finite polygon and its positive-area bounds, or ``None``."""
    if hasattr(value, "tolist"):
        value = value.tolist()
    try:
        if not isinstance(value, (list, tuple)) or len(value) < 4:
            return None
        points = []
        for point in value:
            if hasattr(point, "tolist"):
                point = point.tolist()
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                return None
            x, y = float(point[0]) + offset_x, float(point[1]) + offset_y
            if not math.isfinite(x) or not math.isfinite(y):
                return None
            points.append((x, y))
    except (TypeError, ValueError, OverflowError):
        return None
    xs, ys = [point[0] for point in points], [point[1] for point in points]
    bounds = (min(xs), min(ys), max(xs), max(ys))
    if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        return None
    return tuple(points), bounds


def safe_crop_array(
    arr: "np.ndarray",
    x0: float,
    y0: float,
    x1: float,
    y1: float,
) -> "tuple[np.ndarray, tuple[int, int, int, int]]":
    """Crop a numpy image array with coordinates clamped to valid bounds.

    Converts float coordinates to int, clamps each edge to [0, dimension],
    and ensures x0 < x1, y0 < y1.  Returns (crop, (cx0, cy0, cx1, cy1))
    where the second element is the effective integer crop box after clamping —
    use it as the offset when projecting token coordinates back to page space.

    Callers should treat an empty crop (zero width or height) as a signal to
    skip OCR for that region rather than passing an empty array downstream.
    """
    h, w = arr.shape[:2]
    cx0 = max(0, min(int(x0), w))
    cy0 = max(0, min(int(y0), h))
    cx1 = max(0, min(int(x1), w))
    cy1 = max(0, min(int(y1), h))
    if cx0 > cx1:
        cx0, cx1 = cx1, cx0
    if cy0 > cy1:
        cy0, cy1 = cy1, cy0
    return arr[cy0:cy1, cx0:cx1], (cx0, cy0, cx1, cy1)


def crop_region_in_raster(page_image: Any, region_bbox: Any, page_bbox: Any | None = None):
    """Crop a page image from a PDF-point region or legacy raster-pixel region.

    With ``page_bbox`` the input region uses canonical page points and is
    transformed to the actual raster dimensions before cropping. Without it,
    ``region_bbox`` retains the legacy raster-pixel interpretation.
    """
    import numpy as np
    from structured_pdf_text.geometry import BBox
    from structured_pdf_text.ocr.coordinates import PageTransform, RasterGeometry

    arr = np.asarray(page_image)
    height, width = arr.shape[:2]
    raster_box = (
        PageTransform(page_bbox, RasterGeometry(width, height)).page_bbox_to_raster(region_bbox)
        if page_bbox is not None else region_bbox
    )
    crop, (cx0, cy0, cx1, cy1) = safe_crop_array(
        arr, raster_box.x0, raster_box.y0, raster_box.x1, raster_box.y1
    )
    return crop, (cx0, cy0, cx1, cy1), (width, height)


def finite_confidence(value: Any) -> float | None:
    """Return a finite float confidence, preserving absent or invalid scores."""
    if value is None:
        return None
    try:
        confidence = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return confidence if math.isfinite(confidence) else None
