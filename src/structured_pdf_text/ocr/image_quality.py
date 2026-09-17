"""Cheap visual signals used to choose OCR recovery families."""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class OcrImageProfile:
    contrast_span: float
    grayscale_stddev: float
    sharpness_score: float
    noise_score: float
    median_token_height_px: float | None
    low_contrast: bool
    likely_blurred_or_small: bool
    likely_noisy: bool
    available: bool = True


def profile_image(image: Any, token_heights: Iterable[float] = ()) -> OcrImageProfile:
    values = list(token_heights)
    available = False
    span = stddev = sharpness = noise = 0.0
    try:
        import numpy as np

        array = np.asarray(image.convert("L") if hasattr(image, "convert") else image)
        array = array.astype("float32")
        if array.size == 0:
            raise ValueError("empty image")
        available = True
        p05, p95 = np.percentile(array, [5, 95])
        span = float(p95 - p05)
        stddev = float(array.std())
        try:
            from cv2 import Laplacian, CV_32F
            sharpness = float(Laplacian(array, CV_32F).var())
        except Exception:
            sharpness = float(array.var())
        try:
            from cv2 import medianBlur
            residual = array - medianBlur(array, 3)
            noise = float(np.mean(np.abs(residual)) / max(stddev, 1.0))
        except Exception:
            noise = 0.0
    except Exception:
        available = False
    median_height = median(values) if values else None
    return OcrImageProfile(
        available=available,
        contrast_span=span,
        grayscale_stddev=stddev,
        sharpness_score=sharpness,
        noise_score=noise,
        median_token_height_px=median_height,
        low_contrast=available and span < 70.0,
        likely_blurred_or_small=available and (
            (median_height is not None and median_height < 14.0) or sharpness < 20.0
        ),
        likely_noisy=available and noise > 0.18,
    )
