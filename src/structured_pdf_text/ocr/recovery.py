"""OCR region-level recovery: scale-variant planning, geometry transforms,
token mapping and RGB budget enforcement.

The central component is :class:`OcrRegionRefiner`, which takes an arbitrary
page crop in PDF coordinates, applies the scale factors and rotations specified
by the caller, runs each variant through the OCR engine, and maps every
recognised token back to the original page coordinate system.

**RGB budget gate** — to prevent ``STATUS_ACCESS_VIOLATION`` in Paddle's C++
inference runtime when upscaled variants produce very large images,
:func:`plan_ocr_scales` partitions the requested scale factors into *allowed*
and *blocked* groups before any image is created.  The limit is controlled by
the ``PDFEXTRACTOR_OCR_RGB_BUDGET_MIB`` environment variable (default 8 MiB).
See ``docs/ocr-rgb-budget-crash-fix.md`` for the full incident analysis.

**Debug log** — when ``PDFEXTRACTOR_OCR_DEBUG_LOG`` is set to a writable path,
this module writes ``REGION_SELECTED``, ``OCR_SCALE_PLAN``,
``OCR_SCALE_BLOCKED`` and ``OCR_SCALE_ALL_BLOCKED`` entries to the same file
used by ``ocr/paddle.py``, providing a single unified trace of the recovery
decision chain.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Any

from structured_pdf_text.document import OcrToken, SourceKind
from structured_pdf_text.errors import (
    FatalExtractionError,
    raise_if_resource_exhausted,
)
from structured_pdf_text.geometry import BBox

# ---------------------------------------------------------------------------
# OCR RGB budget — configurable upper bound on the estimated uncompressed size
# of images sent to Paddle.  This is an operational limit that protects
# against STATUS_ACCESS_VIOLATION in Paddle's native runtime on large crop
# variants; it is NOT a documented Paddle limit.
# ---------------------------------------------------------------------------

_MIB = 1024 * 1024

# Default: 8 MiB.  Override with PDFEXTRACTOR_OCR_RGB_BUDGET_MIB.
_OCR_RGB_BUDGET_MIB: float = float(
    os.environ.get("PDFEXTRACTOR_OCR_RGB_BUDGET_MIB", "8.0")
)


@dataclass(frozen=True, slots=True)
class ScalePlan:
    """Pre-computed geometry for one OCR scale variant.

    Instances are produced by :func:`plan_ocr_scales` before any image is
    created.  A plan is *allowed* when its ``estimated_rgb_bytes`` fits within
    the configured budget; it is *blocked* otherwise.

    Attributes:
        scale: The scale factor relative to the base crop (≥ 1.0).
        width: Target image width in pixels after applying *scale*.
        height: Target image height in pixels after applying *scale*.
        estimated_rgb_bytes: Estimated uncompressed RGB memory footprint
            (``width × height × 3`` bytes for uint8 RGB).  Matches the actual
            allocation produced by :func:`resize_image` because both use the
            same rounding rule.
    """

    scale: float
    width: int
    height: int
    estimated_rgb_bytes: int

    @property
    def estimated_rgb_mib(self) -> float:
        """Estimated RGB memory footprint in mebibytes (convenience accessor)."""
        return self.estimated_rgb_bytes / _MIB


def plan_ocr_scales(
    width: int,
    height: int,
    scale_factors: tuple[float, ...],
    max_rgb_mib: float = _OCR_RGB_BUDGET_MIB,
) -> tuple[list[ScalePlan], list[ScalePlan]]:
    """Partition scale factors into allowed and budget-blocked lists.

    Evaluates each scale factor against the RGB memory budget and returns two
    ordered lists: variants whose estimated footprint fits within *max_rgb_mib*
    (allowed) and variants that exceed it (blocked).

    The pixel-count estimate uses **exactly the same rounding rule** as
    :func:`resize_image` (``max(dim + 1, round(dim * scale))`` for scale > 1,
    identity for scale ≤ 1) so that the pre-flight check and the actual image
    allocation are always consistent.

    Args:
        width: Base crop width in pixels.  Must be positive.
        height: Base crop height in pixels.  Must be positive.
        scale_factors: Ordered sequence of scale multipliers to evaluate.
            Duplicate values produce duplicate plans.
        max_rgb_mib: Upper bound on uncompressed RGB size in mebibytes.
            Defaults to :data:`_OCR_RGB_BUDGET_MIB` (env-var controlled).

    Returns:
        A ``(allowed, blocked)`` tuple preserving the original order within
        each list.  Plans in *allowed* all have
        ``estimated_rgb_bytes ≤ max_rgb_mib × 1024²``; plans in *blocked*
        exceed the limit.

    Raises:
        ValueError: If *width* or *height* ≤ 0, *max_rgb_mib* is not a
            positive finite number, or any scale factor is not positive finite.

    Example::

        allowed, blocked = plan_ocr_scales(868, 1334, (1.0, 1.5, 2.0))
        # allowed → [ScalePlan(1.0, 868, 1334, ...), ScalePlan(1.5, 1302, 2001, ...)]
        # blocked → [ScalePlan(2.0, 1736, 2668, ...)]  # 13.3 MiB > 8.0 MiB default
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"Image dimensions must be positive, got {width}×{height}.")
    if not isfinite(max_rgb_mib) or max_rgb_mib <= 0:
        raise ValueError(f"max_rgb_mib must be a positive finite number, got {max_rgb_mib}.")

    limit_bytes = int(max_rgb_mib * _MIB)
    allowed: list[ScalePlan] = []
    blocked: list[ScalePlan] = []

    for scale in scale_factors:
        if not isfinite(scale) or scale <= 0:
            raise ValueError(f"Invalid OCR scale factor: {scale}")
        # Match resize_image() rounding exactly.
        if scale <= 1.0:
            target_width = width
            target_height = height
        else:
            target_width = max(width + 1, round(width * scale))
            target_height = max(height + 1, round(height * scale))

        est_bytes = target_width * target_height * 3  # RGB uint8

        plan = ScalePlan(
            scale=scale,
            width=target_width,
            height=target_height,
            estimated_rgb_bytes=est_bytes,
        )
        if est_bytes <= limit_bytes:
            allowed.append(plan)
        else:
            blocked.append(plan)

    return allowed, blocked


# ---------------------------------------------------------------------------
# Debug log — same env var as paddle.py so all entries land in one file.
# ---------------------------------------------------------------------------

def _recovery_debug(msg: str) -> None:
    """Append one line to the OCR debug log with immediate flush."""
    path = os.environ.get("PDFEXTRACTOR_OCR_DEBUG_LOG") or None
    if not path:
        return
    try:
        import datetime
        ts = datetime.datetime.now().isoformat(timespec="milliseconds")
        with open(path, "a", encoding="utf-8") as _f:
            _f.write(f"[{ts}] {msg}\n")
            _f.flush()
    except Exception:
        pass


class RegionRefinementGoal(str, Enum):
    TEXT = "text"
    TEXTUAL = "textual"
    NUMERIC = "numeric"


@dataclass(frozen=True, slots=True)
class RegionRefinementRequest:
    """Parameterize OCR recovery for one region in canonical PDF coordinates.

    All geometry is expressed in the same top-left coordinate system as the
    page's ``BBox``.  The refiner converts to raster coordinates internally.

    Attributes:
        bbox: Region to recover, in canonical PDF user-space points.
        scale_factors: Scale multipliers applied to the crop before OCR.
            Duplicates are deduplicated; values below 1.0 are clamped to 1.0.
            The RGB budget gate may further reduce this list; see
            :func:`plan_ocr_scales`.
        rotations: Rotation angles in degrees (counter-clockwise).  Each
            combination of (scale, rotation) is one OCR variant.
        quality_variants: Whether to ask the OCR engine for its own internal
            quality variants (e.g. orientation recovery) for each call.
        goal: Governs token selection and candidate ranking.  ``TEXT`` keeps
            all non-blank tokens; ``NUMERIC`` keeps only tokens that contain
            at least one digit or ``%``; ``TEXTUAL`` keeps only tokens with
            at least two characters and at least one alpha character.
        min_confidence: Lower bound on token confidence scores.  Tokens
            whose ``confidence`` is ``None`` always pass.
        page_rotation: PDF ``/Rotate`` value for the source page (0, 90, 180
            or 270).  Required to correctly transform ``bbox`` into the visual
            orientation used by the rendered image.
        quality_policy: OCR quality policy string forwarded verbatim to the
            engine's ``recognize_page``; ``None`` lets the engine use its
            default.
        quality_reasons: Textual reasons from upstream quality assessment
            forwarded to the debug log (e.g. ``"small"``, ``"sparse"``).
            Does not affect behaviour.
    """

    bbox: BBox
    scale_factors: tuple[float, ...] = (1.0,)
    rotations: tuple[float, ...] = (0.0,)
    quality_variants: bool = False
    goal: RegionRefinementGoal = RegionRefinementGoal.TEXT
    min_confidence: float = 0.0
    page_rotation: int = 0
    quality_policy: str | None = None
    quality_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RegionRefinementAttempt:
    """Diagnostic record for one (scale, rotation) OCR variant.

    Attributes:
        scale_factor: Scale applied to the base crop for this attempt.
        rotation: Counter-clockwise rotation in degrees.
        token_count: Number of tokens that survived :func:`_filter_tokens`.
        score: Composite quality score from :func:`_candidate_score`.  Higher
            is better; ``-inf`` means no tokens or a hard error.
        average_confidence: Mean confidence of surviving tokens, or ``0.0``
            when all confidences are ``None`` or there are no tokens.
        error: Exception class and message if this variant failed; ``None`` on
            success (even if ``token_count`` is zero).
    """

    scale_factor: float
    rotation: float
    token_count: int
    score: float
    average_confidence: float
    error: str | None = None


@dataclass(frozen=True, slots=True)
class RegionRefinementResult:
    """Output of :meth:`OcrRegionRefiner.refine` for one recovery request.

    Attributes:
        bbox: Effective region actually cropped (may differ from the requested
            ``bbox`` after intersection with the page boundary).
        tokens: Final selected tokens in page coordinate space, mapped back
            from whichever (scale, rotation) variant won.  Empty when no
            candidate produced any token, or when the RGB budget blocked all
            scale variants.
        attempts: One record per (scale, rotation) combination tried, in the
            order they were attempted.  Useful for diagnostics.
        selected_scale_factor: Scale of the winning variant, or ``None`` when
            no candidate produced tokens.
        selected_rotation: Rotation of the winning variant, or ``None`` when
            no candidate produced tokens.
        ocr_passes: Total number of individual OCR inference calls across all
            variants (tracks engine-internal quality passes).
        ocr_batches: Total number of batch calls across all variants.
    """

    bbox: BBox
    tokens: tuple[OcrToken, ...]
    attempts: tuple[RegionRefinementAttempt, ...]
    selected_scale_factor: float | None
    selected_rotation: float | None
    ocr_passes: int
    ocr_batches: int


class OcrRegionRefiner:
    """Run OCR variants on arbitrary page regions and preserve page geometry.

    The component is independent from page numbers and semantic region types.
    Callers decide which box represents a line, table, figure, axis or another
    recovery target. Scaling and rotation happen in raster coordinates; every
    selected token is mapped back to the original PDF coordinate system.
    """

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def refine(
        self,
        page_image: Any,
        page_index: int,
        page_bbox: BBox,
        request: RegionRefinementRequest,
    ) -> RegionRefinementResult:
        """Run OCR scale-and-rotation variants on one page region.

        **RGB budget gate** — before creating any image, :func:`plan_ocr_scales`
        evaluates every requested scale factor against
        ``PDFEXTRACTOR_OCR_RGB_BUDGET_MIB`` (default 8 MiB).  Scale factors
        whose estimated uncompressed RGB size exceeds the budget are silently
        skipped (a ``OCR_SCALE_BLOCKED`` entry is written to the debug log).
        If **all** scale factors are blocked (including 1×), recovery is
        abandoned and an empty result is returned with an
        ``OCR_SCALE_ALL_BLOCKED`` log entry.

        This guard prevents ``STATUS_ACCESS_VIOLATION`` in Paddle's C++
        inference runtime caused by consecutive very large image allocations.
        See ``docs/ocr-rgb-budget-crash-fix.md`` for the full analysis.

        **Variant selection** — for each allowed (scale, rotation) pair the
        crop is OCR-processed and tokens are mapped back to page coordinates
        via an inverse affine transform.  The winning variant is the one with
        the highest composite quality score (:func:`_candidate_score`).  For
        ``NUMERIC`` goals with multiple candidates, tokens are merged across
        variants to maximise coverage (:func:`_merge_numeric_candidates`).

        Args:
            page_image: Rendered page image (PIL ``Image`` or NumPy array).
                Must cover the full page at a consistent scale.
            page_index: 0-based page index; included in diagnostic log entries.
            page_bbox: Page bounding box in canonical PDF coordinates (top-left
                origin, points).  Used to compute crop-to-page scale factors.
            request: Full specification of region geometry, scale/rotation
                variants, quality policy and filtering goal.

        Returns:
            :class:`RegionRefinementResult` with selected tokens in page
            coordinate space and per-variant attempt diagnostics.
        """
        region_bbox = request.bbox.intersection(page_bbox)
        if region_bbox is None or region_bbox.area <= 0:
            return RegionRefinementResult(
                bbox=request.bbox,
                tokens=(),
                attempts=(),
                selected_scale_factor=None,
                selected_rotation=None,
                ocr_passes=0,
                ocr_batches=0,
            )

        try:
            crop = crop_page_region(page_image, page_bbox, region_bbox, request.page_rotation)
        except FatalExtractionError:
            raise
        except Exception as exc:
            raise_if_resource_exhausted(
                exc,
                page_index=page_index,
                stage="ocr_region_refinement",
            )
            raise
        attempts: list[RegionRefinementAttempt] = []
        candidates: list[tuple[float, float, float, list[OcrToken]]] = []
        total_passes = 0
        total_batches = 0

        scales_raw = tuple(
            dict.fromkeys(max(1.0, float(value)) for value in request.scale_factors)
        ) or (1.0,)
        rotations = tuple(
            dict.fromkeys(float(value) for value in request.rotations)
        ) or (0.0,)

        # ---- RGB budget gate -----------------------------------------------
        crop_w, crop_h = image_size(crop)
        reasons_str = ",".join(request.quality_reasons) if request.quality_reasons else "none"
        _recovery_debug(
            f"REGION_SELECTED page={page_index}"
            f" bbox={region_bbox.x0:.1f},{region_bbox.y0:.1f}"
            f",{region_bbox.x1:.1f},{region_bbox.y1:.1f}"
            f" base_width={crop_w} base_height={crop_h}"
            f" requested_scales={','.join(str(s) for s in scales_raw)}"
            f" quality_reasons=[{reasons_str}]"
        )

        allowed_plans, blocked_plans = plan_ocr_scales(
            crop_w, crop_h, scales_raw, _OCR_RGB_BUDGET_MIB
        )
        _recovery_debug(
            f"OCR_SCALE_PLAN page={page_index}"
            f" limit_rgb_mib={_OCR_RGB_BUDGET_MIB:.1f}"
            f" allowed_scales={','.join(str(p.scale) for p in allowed_plans) or 'none'}"
            f" blocked_scales={','.join(str(p.scale) for p in blocked_plans) or 'none'}"
        )
        for bp in blocked_plans:
            _recovery_debug(
                f"OCR_SCALE_BLOCKED page={page_index}"
                f" scale={bp.scale}"
                f" width={bp.width} height={bp.height}"
                f" estimated_rgb_mib={bp.estimated_rgb_mib:.3f}"
                f" reason=rgb_budget_exceeded"
            )

        if not allowed_plans:
            # Even 1× exceeds the budget.  Do not silently drop the region —
            # log an explicit failure so it can be investigated separately.
            _recovery_debug(
                f"OCR_SCALE_ALL_BLOCKED page={page_index}"
                f" base_width={crop_w} base_height={crop_h}"
                f" estimated_1x_mib={crop_w * crop_h * 3 / _MIB:.3f}"
                f" limit_rgb_mib={_OCR_RGB_BUDGET_MIB:.1f}"
                f" action=recovery_skipped"
            )
            return RegionRefinementResult(
                bbox=region_bbox,
                tokens=(),
                attempts=tuple(attempts),
                selected_scale_factor=None,
                selected_rotation=None,
                ocr_passes=0,
                ocr_batches=0,
            )

        allowed_scale_set = frozenset(p.scale for p in allowed_plans)
        scales = tuple(s for s in scales_raw if s in allowed_scale_set)
        # ---- end budget gate ------------------------------------------------

        for scale_factor in scales:
            try:
                scaled = resize_image(crop, scale_factor)
                scaled_size = image_size(scaled)
            except FatalExtractionError:
                raise
            except Exception as exc:
                raise_if_resource_exhausted(
                    exc,
                    page_index=page_index,
                    stage="ocr_region_refinement",
                )
                raise
            for rotation in rotations:
                try:
                    transformed, inverse = rotate_image_expanded(scaled, rotation)
                    width, height = image_size(transformed)
                    virtual_bbox = BBox(0.0, 0.0, float(width), float(height))
                    tokens = _recognize(
                        self.engine,
                        transformed,
                        page_index,
                        virtual_bbox,
                        quality_variants=request.quality_variants,
                        quality_policy=request.quality_policy,
                    )
                    total_passes += getattr(self.engine, "last_pass_count", 0) or 0
                    total_batches += getattr(self.engine, "last_batch_count", 0) or 0
                    mapped = [
                        _map_token_to_page(
                            token,
                            inverse,
                            scaled_size,
                            region_bbox,
                        )
                        for token in tokens
                    ]
                    mapped = _filter_tokens(mapped, request)
                    score, confidence = _candidate_score(mapped, request.goal)
                    attempts.append(
                        RegionRefinementAttempt(
                            scale_factor=scale_factor,
                            rotation=rotation,
                            token_count=len(mapped),
                            score=score,
                            average_confidence=confidence,
                        )
                    )
                    if mapped:
                        candidates.append((score, scale_factor, rotation, mapped))
                except FatalExtractionError:
                    raise
                except (AttributeError, ImportError, TypeError, ValueError, RuntimeError) as exc:
                    raise_if_resource_exhausted(
                        exc,
                        page_index=page_index,
                        stage="ocr_region_refinement",
                    )
                    attempts.append(
                        RegionRefinementAttempt(
                            scale_factor=scale_factor,
                            rotation=rotation,
                            token_count=0,
                            score=-math.inf,
                            average_confidence=0.0,
                            error=f"{type(exc).__name__}: {exc}",
                        )
                    )
                except Exception as exc:
                    raise_if_resource_exhausted(
                        exc,
                        page_index=page_index,
                        stage="ocr_region_refinement",
                    )
                    raise

        if not candidates:
            return RegionRefinementResult(
                bbox=region_bbox,
                tokens=(),
                attempts=tuple(attempts),
                selected_scale_factor=None,
                selected_rotation=None,
                ocr_passes=total_passes,
                ocr_batches=total_batches,
            )
        _, scale_factor, rotation, tokens = max(candidates, key=lambda item: item[0])
        if request.goal == RegionRefinementGoal.NUMERIC and len(candidates) > 1:
            tokens = _merge_numeric_candidates(candidates)
        return RegionRefinementResult(
            bbox=region_bbox,
            tokens=tuple(tokens),
            attempts=tuple(attempts),
            selected_scale_factor=scale_factor,
            selected_rotation=rotation,
            ocr_passes=total_passes,
            ocr_batches=total_batches,
        )

    def refine_many(
        self,
        page_image: Any,
        page_index: int,
        page_bbox: BBox,
        requests: list[RegionRefinementRequest] | tuple[RegionRefinementRequest, ...],
    ) -> list[RegionRefinementResult]:
        """Apply :meth:`refine` to every request in *requests* sequentially.

        Returns results in the same order as *requests*.  Each request is
        processed independently; budget gate and variant selection are applied
        per-request.
        """
        return [
            self.refine(page_image, page_index, page_bbox, request)
            for request in requests
        ]


def recover_ocr_tokens(
    engine: Any,
    page_image: Any,
    page_index: int,
    page_bbox: BBox,
    request: RegionRefinementRequest,
) -> list[OcrToken]:
    """Compatibility function for callers that only need selected tokens."""
    result = OcrRegionRefiner(engine).refine(
        page_image,
        page_index,
        page_bbox,
        request,
    )
    return list(result.tokens)


def crop_page_region(
    image: Any,
    page_bbox: BBox,
    region_bbox: BBox,
    page_rotation: int = 0,
) -> Any:
    """Crop a rendered page using canonical PDF-point coordinates.

    When ``page_rotation`` is non-zero the rendered image is in visual
    orientation (PDFium applies /Rotate during rendering) while
    ``region_bbox`` and ``page_bbox`` are in canonical PDF user space.
    This function applies the same rotation so the crop is correct.
    """
    width, height = image_size(image)
    r = page_rotation % 360
    if r in (90, 270):
        # Visual dims are swapped relative to canonical PDF dims.
        visual_page_width = page_bbox.height
        visual_page_height = page_bbox.width
    else:
        visual_page_width = page_bbox.width
        visual_page_height = page_bbox.height
    # Transform region_bbox to visual/raster space.
    visual_region = region_bbox.rotate_to_visual(r, page_bbox.width, page_bbox.height)
    scale_x = width / max(visual_page_width, 1.0)
    scale_y = height / max(visual_page_height, 1.0)
    left = max(0, int(visual_region.x0 * scale_x))
    top = max(0, int(visual_region.y0 * scale_y))
    right = min(width, max(left + 1, int(math.ceil(visual_region.x1 * scale_x))))
    bottom = min(height, max(top + 1, int(math.ceil(visual_region.y1 * scale_y))))
    if hasattr(image, "crop"):
        return image.crop((left, top, right, bottom))
    return image[top:bottom, left:right]


def resize_image(image: Any, factor: float) -> Any:
    """Upscale *image* by *factor* using the best available backend.

    When *factor* ≤ 1.0 the original image is returned unchanged.  Otherwise
    the target dimensions are ``(max(w+1, round(w*factor)), max(h+1, round(h*factor)))``
    — the ``+1`` floor ensures at least one pixel of growth even for sub-pixel
    scale requests.  This rounding is mirrored exactly by :func:`plan_ocr_scales`.

    Prefers PIL/Pillow (LANCZOS resampling) and falls back to OpenCV (cubic).
    Returns the input unchanged if neither library is available.
    """
    if factor <= 1.0:
        return image
    width, height = image_size(image)
    size = (
        max(width + 1, round(width * factor)),
        max(height + 1, round(height * factor)),
    )
    if hasattr(image, "resize"):
        try:
            from PIL import Image

            return image.resize(size, Image.Resampling.LANCZOS)
        except (ImportError, AttributeError, TypeError, ValueError):
            return image.resize(size)
    try:
        import cv2

        return cv2.resize(image, size, interpolation=cv2.INTER_CUBIC)
    except (ImportError, AttributeError, TypeError, ValueError):
        return image


def rotate_image_expanded(
    image: Any,
    angle: float,
) -> tuple[Any, tuple[float, float, float, float, float, float]]:
    """Rotate a raster and return the affine transform back to its source."""
    if abs(angle) < 0.001:
        return image, (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    import cv2
    import numpy as np

    array = np.asarray(image)
    height, width = array.shape[:2]
    center = (width / 2.0, height / 2.0)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    cosine = abs(matrix[0, 0])
    sine = abs(matrix[0, 1])
    target_width = max(1, int(math.ceil(height * sine + width * cosine)))
    target_height = max(1, int(math.ceil(height * cosine + width * sine)))
    matrix[0, 2] += target_width / 2.0 - center[0]
    matrix[1, 2] += target_height / 2.0 - center[1]
    border_value: int | tuple[int, int, int]
    border_value = 255 if array.ndim == 2 else (255, 255, 255)
    rotated = cv2.warpAffine(
        array,
        matrix,
        (target_width, target_height),
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=border_value,
    )
    inverse = cv2.invertAffineTransform(matrix)
    inverse_tuple = tuple(float(value) for row in inverse for value in row)
    try:
        from PIL import Image

        if hasattr(image, "mode"):
            rotated = Image.fromarray(rotated)
    except ImportError:
        pass
    return rotated, inverse_tuple  # type: ignore[return-value]


def image_size(image: Any) -> tuple[int, int]:
    """Return ``(width, height)`` from a PIL Image or NumPy array."""
    if hasattr(image, "size") and isinstance(image.size, tuple):
        return int(image.size[0]), int(image.size[1])
    shape = getattr(image, "shape", None)
    if shape is None or len(shape) < 2:
        raise TypeError("OCR region refinement requires a raster image")
    return int(shape[1]), int(shape[0])


def _recognize(
    engine: Any,
    image: Any,
    page_index: int,
    bbox: BBox,
    *,
    quality_variants: bool,
    quality_policy: str | None = None,
) -> list[OcrToken]:
    """Call the OCR engine, tolerating engines that accept fewer keyword args."""
    try:
        return engine.recognize_page(
            image,
            page_index,
            bbox,
            quality_variants=quality_variants,
            quality_policy=quality_policy,
        )
    except TypeError:
        try:
            return engine.recognize_page(image, page_index, bbox)
        except TypeError:
            return engine.recognize_page(image, page_index)


def _map_token_to_page(
    token: OcrToken,
    inverse: tuple[float, float, float, float, float, float],
    source_size: tuple[int, int],
    region_bbox: BBox,
) -> OcrToken:
    """Map an OCR token from the rotated/scaled variant back to page coordinates.

    Applies the inverse affine transform (6-element row-major matrix from
    :func:`rotate_image_expanded`) to all four corners of the token bounding
    box, clamps them to the source image bounds, and then linearly interpolates
    into the ``region_bbox`` in canonical PDF page space.

    The resulting token carries ``source=OCR_REGION`` and
    ``provenance="targeted_region_recovery"``.
    """
    a, b, c, d, e, f = inverse
    points = (
        (token.bbox.x0, token.bbox.y0),
        (token.bbox.x1, token.bbox.y0),
        (token.bbox.x1, token.bbox.y1),
        (token.bbox.x0, token.bbox.y1),
    )
    mapped = [(a * x + b * y + c, d * x + e * y + f) for x, y in points]
    width, height = source_size
    xs = [min(max(x, 0.0), float(width)) for x, _ in mapped]
    ys = [min(max(y, 0.0), float(height)) for _, y in mapped]
    local = BBox(min(xs), min(ys), max(xs), max(ys))
    bbox = BBox(
        region_bbox.x0 + local.x0 * region_bbox.width / max(width, 1),
        region_bbox.y0 + local.y0 * region_bbox.height / max(height, 1),
        region_bbox.x0 + local.x1 * region_bbox.width / max(width, 1),
        region_bbox.y0 + local.y1 * region_bbox.height / max(height, 1),
    )
    return OcrToken(
        text=token.text,
        bbox=bbox,
        confidence=token.confidence,
        language=token.language,
        source=SourceKind.OCR_REGION,
        rotation=0,
        provenance="targeted_region_recovery",
    )


def _filter_tokens(
    tokens: list[OcrToken],
    request: RegionRefinementRequest,
) -> list[OcrToken]:
    """Apply goal-specific and confidence filtering to a list of OCR tokens."""
    filtered = [
        token
        for token in tokens
        if token.text.strip()
        and (token.confidence is None or token.confidence >= request.min_confidence)
    ]
    if request.goal == RegionRefinementGoal.NUMERIC:
        return [token for token in filtered if _contains_numeric_content(token.text)]
    if request.goal == RegionRefinementGoal.TEXTUAL:
        return [
            token
            for token in filtered
            if len(token.text.strip()) >= 2
            and any(character.isalpha() for character in token.text)
        ]
    return filtered


def _contains_numeric_content(value: str) -> bool:
    text = " ".join(value.split())
    return any(character.isdigit() for character in text) or "%" in text


def _candidate_score(
    tokens: list[OcrToken],
    goal: RegionRefinementGoal,
) -> tuple[float, float]:
    """Score a list of filtered tokens for variant selection.

    Returns ``(score, average_confidence)``.  Score is ``-inf`` for empty
    lists.  Otherwise it combines mean confidence, a log-capped character
    count bonus, a horizontal-orientation bonus, and a small token-count
    bonus for NUMERIC goals.
    """
    if not tokens:
        return -math.inf, 0.0
    confidences = [token.confidence for token in tokens if token.confidence is not None]
    average = sum(confidences) / len(confidences) if confidences else 0.0
    characters = sum(len("".join(token.text.split())) for token in tokens)
    horizontal = sum(token.bbox.width >= token.bbox.height for token in tokens) / len(tokens)
    score = average + min(0.08, math.log1p(characters) * 0.012) + horizontal * 0.02
    if goal == RegionRefinementGoal.NUMERIC:
        score += min(0.05, len(tokens) * 0.005)
    return score, average


def _merge_numeric_candidates(
    candidates: list[tuple[float, float, float, list[OcrToken]]],
) -> list[OcrToken]:
    """Union numeric hypotheses by position while resolving local conflicts."""
    merged: list[OcrToken] = []
    for _, _, _, tokens in sorted(candidates, key=lambda item: item[0], reverse=True):
        for token in tokens:
            overlaps = [
                index
                for index, existing in enumerate(merged)
                if _same_token_position(existing, token)
            ]
            if not overlaps:
                merged.append(token)
                continue
            best_index = max(overlaps, key=lambda index: _numeric_token_score(merged[index]))
            if _numeric_token_score(token) > _numeric_token_score(merged[best_index]):
                merged[best_index] = token
    return sorted(merged, key=lambda token: (token.bbox.y0, token.bbox.x0))


def _same_token_position(first: OcrToken, second: OcrToken) -> bool:
    """Return True when two tokens overlap enough to be considered the same position.

    Uses IoU ≥ 0.25 or one-sided intersection coverage ≥ 0.50 as criteria.
    """
    if first.bbox.iou(second.bbox) >= 0.25:
        return True
    intersection = first.bbox.intersection(second.bbox)
    if intersection is None:
        return False
    return (
        intersection.area / max(first.bbox.area, 1.0) >= 0.50
        or intersection.area / max(second.bbox.area, 1.0) >= 0.50
    )


def _numeric_token_score(token: OcrToken) -> float:
    """Score a single token for numeric-merge conflict resolution.

    Higher score wins.  Combines OCR confidence with a syntax ratio (fraction
    of characters that are digits or common numeric punctuation) and a small
    bonus for each digit up to six.
    """
    text = "".join(token.text.split())
    digits = sum(character.isdigit() for character in text)
    accepted = sum(character.isdigit() or character in ".,%+-/R$" for character in text)
    syntax_ratio = accepted / max(len(text), 1)
    return (token.confidence or 0.0) + syntax_ratio * 0.03 + min(digits, 6) * 0.002
