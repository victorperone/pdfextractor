from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any

from structured_pdf_text.document import OcrToken, SourceKind
from structured_pdf_text.geometry import BBox


class RegionRefinementGoal(str, Enum):
    TEXT = "text"
    TEXTUAL = "textual"
    NUMERIC = "numeric"


@dataclass(frozen=True, slots=True)
class RegionRefinementRequest:
    """Describe OCR recovery for one region in canonical PDF coordinates."""

    bbox: BBox
    scale_factors: tuple[float, ...] = (1.0,)
    rotations: tuple[float, ...] = (0.0,)
    quality_variants: bool = False
    goal: RegionRefinementGoal = RegionRefinementGoal.TEXT
    min_confidence: float = 0.0


@dataclass(frozen=True, slots=True)
class RegionRefinementAttempt:
    scale_factor: float
    rotation: float
    token_count: int
    score: float
    average_confidence: float
    error: str | None = None


@dataclass(frozen=True, slots=True)
class RegionRefinementResult:
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

        crop = crop_page_region(page_image, page_bbox, region_bbox)
        attempts: list[RegionRefinementAttempt] = []
        candidates: list[tuple[float, float, float, list[OcrToken]]] = []
        total_passes = 0
        total_batches = 0

        scales = tuple(
            dict.fromkeys(max(1.0, float(value)) for value in request.scale_factors)
        ) or (1.0,)
        rotations = tuple(
            dict.fromkeys(float(value) for value in request.rotations)
        ) or (0.0,)
        for scale_factor in scales:
            scaled = resize_image(crop, scale_factor)
            scaled_size = image_size(scaled)
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
                except (AttributeError, ImportError, TypeError, ValueError, RuntimeError) as exc:
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


def crop_page_region(image: Any, page_bbox: BBox, region_bbox: BBox) -> Any:
    """Crop a rendered page using canonical PDF-point coordinates."""
    width, height = image_size(image)
    scale_x = width / max(page_bbox.width, 1.0)
    scale_y = height / max(page_bbox.height, 1.0)
    left = max(0, int((region_bbox.x0 - page_bbox.x0) * scale_x))
    top = max(0, int((region_bbox.y0 - page_bbox.y0) * scale_y))
    right = min(
        width,
        max(left + 1, int(math.ceil((region_bbox.x1 - page_bbox.x0) * scale_x))),
    )
    bottom = min(
        height,
        max(top + 1, int(math.ceil((region_bbox.y1 - page_bbox.y0) * scale_y))),
    )
    if hasattr(image, "crop"):
        return image.crop((left, top, right, bottom))
    return image[top:bottom, left:right]


def resize_image(image: Any, factor: float) -> Any:
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
) -> list[OcrToken]:
    try:
        return engine.recognize_page(
            image,
            page_index,
            bbox,
            quality_variants=quality_variants,
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
    )


def _filter_tokens(
    tokens: list[OcrToken],
    request: RegionRefinementRequest,
) -> list[OcrToken]:
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
    text = "".join(token.text.split())
    digits = sum(character.isdigit() for character in text)
    accepted = sum(character.isdigit() or character in ".,%+-/R$" for character in text)
    syntax_ratio = accepted / max(len(text), 1)
    return (token.confidence or 0.0) + syntax_ratio * 0.03 + min(digits, 6) * 0.002
