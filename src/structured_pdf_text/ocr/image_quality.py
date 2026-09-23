"""Cheap visual signals used to choose OCR recovery families."""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class OcrImageProfile:
    """Visual characteristics of a page image used to select recovery strategies.

    All numeric fields are set to ``0.0`` and boolean flags to ``False`` when
    ``available`` is ``False`` (e.g. numpy or OpenCV are absent at runtime).

    Attributes:
        contrast_span: Difference between the 95th and 5th percentile pixel
            intensities in the grayscale image.  Values below 70 indicate
            low contrast.
        grayscale_stddev: Standard deviation of the grayscale pixel
            distribution.  Low values suggest a washed-out or near-blank
            page.
        sharpness_score: Variance of the Laplacian of the grayscale array.
            Lower values indicate blur; values below 20 trigger
            ``likely_blurred_or_small``.
        noise_score: Mean absolute deviation of the image from a median-
            filtered version, normalised by ``grayscale_stddev``.  Values
            above 0.18 set ``likely_noisy``.
        median_token_height_px: Median height of OCR token bounding boxes
            in pixels, or ``None`` if no token heights were supplied.
            Values below 14 px contribute to ``likely_blurred_or_small``.
        low_contrast: ``True`` when ``contrast_span`` < 70 and the image
            was successfully analysed.
        likely_blurred_or_small: ``True`` when either
            ``median_token_height_px`` < 14 px or ``sharpness_score`` < 20.
        likely_noisy: ``True`` when ``noise_score`` > 0.18.
        available: ``False`` when the analysis could not be completed,
            typically because numpy is not installed.  All other fields
            are meaningless in that case.
    """

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
    """Measure cheap visual quality signals from a rendered page image.

    Converts the image to 8-bit grayscale, then derives a small set of
    summary statistics that describe contrast, sharpness, and noise level.
    When OpenCV is available, sharpness is measured via the Laplacian
    variance and noise via median-filter residuals; otherwise, pixel
    variance is used as a fallback for sharpness and noise is set to 0.0.
    The function never raises: any analysis failure sets ``available=False``
    on the returned profile.

    Args:
        image: A PIL ``Image`` or any array-like object that either has a
            ``convert`` method or can be passed directly to
            ``numpy.asarray``.
        token_heights: Optional sequence of OCR token bounding-box heights
            in pixels, already available from a prior recognition pass.
            Used solely to populate ``median_token_height_px``.

    Returns:
        An :class:`OcrImageProfile` with all fields populated when analysis
        succeeds, or with ``available=False`` and zeroed numeric fields
        when numpy is unavailable or the image is empty.
    """
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
