"""Shared validation for quadrilateral OCR parser outputs."""
from __future__ import annotations

import math
from typing import Any


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


def finite_confidence(value: Any) -> float | None:
    """Return a finite float confidence, preserving absent or invalid scores."""
    if value is None:
        return None
    try:
        confidence = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return confidence if math.isfinite(confidence) else None
