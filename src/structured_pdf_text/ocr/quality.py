"""Generic, language-independent OCR quality assessment."""
from __future__ import annotations

import math
import statistics
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable

from structured_pdf_text.config import OcrQualityThresholds
from structured_pdf_text.document import OcrToken, TokenFlag


@dataclass(frozen=True, slots=True)
class PaddleRawMetrics:
    detection_count: int | None = None
    recognition_count: int | None = None
    recognition_yield: float | None = None
    mean_detection_score: float | None = None
    recognition_score_threshold: float | None = None
    document_orientation: int | None = None
    textline_orientations: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class OcrQualityAssessment:
    score: float
    sufficient: bool
    recovery_recommended: bool
    character_count: int
    token_count: int
    char_weighted_confidence: float
    median_confidence: float
    lower_quartile_confidence: float
    low_confidence_character_ratio: float
    horizontal_token_ratio: float
    printable_character_ratio: float
    alphanumeric_character_ratio: float
    suspicious_token_ratio: float
    detection_count: int | None
    recognition_count: int | None
    recognition_yield: float | None
    orientation_incoherent: bool
    reasons: tuple[str, ...] = ()


@dataclass(slots=True)
class OcrCandidate:
    name: str
    tokens: list[OcrToken]
    raw_metrics: PaddleRawMetrics
    quality: OcrQualityAssessment
    rotation: int = 0
    enhancement: str | None = None
    family: str = "unknown"
    line_cluster_count: int = 0
    text_area_coverage: float = 0.0
    character_count: int = 0
    token_count: int = 0
    duplicate_ratio: float = 0.0
    spatial_coverage_score: float = 0.0
    fusion_replacements_attempted: int = 0
    fusion_replacements_accepted: int = 0
    fusion_replacements_rolled_back: int = 0
    fusion_lost_clusters: int = 0
    fusion_duplicate_clusters: int = 0


@dataclass(frozen=True, slots=True)
class OcrCoverageMetrics:
    line_cluster_count: int
    text_area_coverage: float
    character_count: int
    token_count: int
    duplicate_ratio: float
    spatial_coverage_score: float


def assess_ocr_coverage(
    tokens: Iterable[OcrToken],
    page_bbox: Any | None = None,
) -> OcrCoverageMetrics:
    """Measure spatial completeness independently from recognition quality."""
    values = [token for token in tokens if token.text.strip() and token.bbox.area > 0]
    if not values:
        return OcrCoverageMetrics(0, 0.0, 0, 0, 0.0, 0.0)
    try:
        from structured_pdf_text.ocr.reconstruct import reconstruct_ocr_lines

        line_count = len(reconstruct_ocr_lines(values, 0, page_bbox))
    except (ImportError, TypeError, ValueError):
        line_count = 0
    boxes = [token.bbox for token in values]
    covered_area = _union_area(boxes)
    denominator = page_bbox.area if page_bbox is not None and page_bbox.area > 0 else _union_area(boxes)
    duplicate_count = 0
    for index, first in enumerate(values):
        for second in values[index + 1 :]:
            if first.bbox.iou(second.bbox) < 0.70:
                continue
            if " ".join(first.text.split()).casefold() == " ".join(second.text.split()).casefold():
                duplicate_count += 1
                break
    character_count = sum(len(token.text) for token in values)
    duplicate_ratio = duplicate_count / max(len(values), 1)
    area_coverage = covered_area / max(denominator, 1.0)
    # The score is intentionally monotonic in independent spatial evidence;
    # selection compares it only alongside quality and line clusters.
    spatial_score = min(
        1.0,
        0.55 * min(1.0, line_count / max(1.0, min(len(values), 12)))
        + 0.45 * min(1.0, area_coverage * 20.0),
    )
    return OcrCoverageMetrics(
        line_cluster_count=line_count,
        text_area_coverage=round(area_coverage, 8),
        character_count=character_count,
        token_count=len(values),
        duplicate_ratio=round(duplicate_ratio, 8),
        spatial_coverage_score=round(spatial_score, 8),
    )


def _union_area(boxes: list[Any]) -> float:
    """Compute rectangle union area with a small deterministic sweep."""
    if not boxes:
        return 0.0
    x_edges = sorted({value for box in boxes for value in (box.x0, box.x1)})
    area = 0.0
    for left, right in zip(x_edges, x_edges[1:]):
        if right <= left:
            continue
        spans = sorted(
            (box.y0, box.y1)
            for box in boxes
            if box.x0 < right and box.x1 > left and box.y1 > box.y0
        )
        covered_y = 0.0
        current_start = current_end = None
        for start, end in spans:
            if current_start is None:
                current_start, current_end = start, end
            elif start > current_end:
                covered_y += current_end - current_start
                current_start, current_end = start, end
            else:
                current_end = max(current_end, end)
        if current_start is not None:
            covered_y += current_end - current_start
        area += (right - left) * covered_y
    return area


def _text_chars(text: str) -> list[str]:
    return list(text or "")


def _confidence(token: OcrToken) -> float:
    return max(0.0, min(1.0, float(token.confidence))) if token.confidence is not None else 0.0


def is_suspicious_token(token: OcrToken) -> bool:
    chars = _text_chars(token.text)
    if not chars:
        return True
    printable = sum(char.isprintable() for char in chars) / len(chars)
    alnum = sum(char.isalnum() for char in chars)
    punctuation = sum(unicodedata.category(char).startswith("P") for char in chars)
    repeated = max((chars.count(char) for char in set(chars)), default=0) / len(chars)
    control = sum(unicodedata.category(char).startswith("C") for char in chars)
    # Technical identifiers, URLs and currency values remain valid because no
    # lexical dictionary or domain-specific regular expression is used here.
    return (
        printable < 0.90
        or control / max(len(chars), 1) > 0.10
        or (alnum == 0 and punctuation / len(chars) > 0.80 and len(chars) >= 4)
        or (repeated > 0.82 and len(chars) >= 5 and alnum == 0)
        or (len(chars) == 1 and token.bbox.area > 2500 and _confidence(token) < 0.70)
    )


def assess_ocr_quality(
    tokens: Iterable[OcrToken],
    *,
    raw_metrics: PaddleRawMetrics | None = None,
    thresholds: OcrQualityThresholds | None = None,
    orientation_incoherent: bool = False,
) -> OcrQualityAssessment:
    values = list(tokens)
    # Reconstructed separators are useful in the output stream but are not
    # recognition evidence. Counting them here makes an otherwise good line
    # look less confident and can trigger needless targeted recovery.
    metric_values = [
        token
        for token in values
        if not (
            TokenFlag.WHITESPACE_INFERRED in getattr(token, "flags", set())
            and not token.text.strip()
        )
    ]
    thresholds = thresholds or OcrQualityThresholds()
    token_count = len(metric_values)
    chars = [_text_chars(token.text) for token in metric_values]
    character_count = sum(len(item) for item in chars)
    weights = [max(1, sum(char.isalnum() for char in item)) for item in chars]
    confidences = [_confidence(token) for token in metric_values]
    weighted_total = sum(confidence * weight for confidence, weight in zip(confidences, weights))
    char_weighted = weighted_total / sum(weights) if weights else 0.0
    median_conf = statistics.median(confidences) if confidences else 0.0
    lower_quartile = _percentile(confidences, 0.25)
    low_chars = sum(weight for weight, confidence in zip(weights, confidences) if confidence < thresholds.low_confidence_threshold)
    low_ratio = low_chars / sum(weights) if weights else 0.0
    total_chars = max(1, character_count)
    printable = sum(char.isprintable() for item in chars for char in item) / total_chars
    alphanumeric = sum(char.isalnum() for item in chars for char in item) / total_chars
    control_ratio = sum(
        unicodedata.category(char).startswith("C")
        for item in chars
        for char in item
    ) / total_chars
    suspicious = sum(is_suspicious_token(token) for token in metric_values) / max(1, token_count)
    horizontal = sum(token.bbox.width >= token.bbox.height * 1.15 for token in metric_values) / max(1, token_count)
    metrics = raw_metrics or PaddleRawMetrics()
    reasons: list[str] = []
    if not metric_values:
        reasons.append("no_tokens")
    if char_weighted < thresholds.severe_mean_confidence:
        reasons.append("low_weighted_confidence")
    if lower_quartile < thresholds.strong_lower_quartile:
        reasons.append("weak_lower_quartile")
    if low_ratio > thresholds.max_low_confidence_char_ratio:
        reasons.append("low_confidence_characters")
    if low_ratio >= thresholds.severe_low_confidence_char_ratio:
        reasons.append("severely_low_confidence_characters")
    if printable < thresholds.minimum_printable_ratio:
        reasons.append("low_printable_ratio")
    if suspicious > 0.20:
        reasons.append("suspicious_tokens")
    if control_ratio > 0.10:
        reasons.append("control_characters")
    if metric_values and horizontal < thresholds.minimum_orientation_ratio:
        reasons.append("orientation_ratio_below_threshold")
    if metrics.recognition_yield is not None and metrics.recognition_yield < 0.65:
        reasons.append("low_recognition_yield")
    if orientation_incoherent:
        reasons.append("orientation_incoherent")
    sufficient = bool(metric_values) and not reasons and char_weighted >= thresholds.strong_mean_confidence
    if sufficient:
        score = min(1.0, char_weighted + 0.04 * printable + 0.02 * horizontal)
    else:
        score = max(0.0, char_weighted - 0.12 * suspicious - 0.08 * low_ratio)
    return OcrQualityAssessment(
        score=score,
        sufficient=sufficient,
        recovery_recommended=not sufficient,
        character_count=character_count,
        token_count=token_count,
        char_weighted_confidence=char_weighted,
        median_confidence=median_conf,
        lower_quartile_confidence=lower_quartile,
        low_confidence_character_ratio=low_ratio,
        horizontal_token_ratio=horizontal,
        printable_character_ratio=printable,
        alphanumeric_character_ratio=alphanumeric,
        suspicious_token_ratio=suspicious,
        detection_count=metrics.detection_count,
        recognition_count=metrics.recognition_count,
        recognition_yield=metrics.recognition_yield,
        orientation_incoherent=orientation_incoherent,
        reasons=tuple(reasons),
    )


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def raw_result_metrics(raw: Any) -> PaddleRawMetrics:
    """Extract optional PaddleOCR 3.x or legacy metrics without raising."""
    records = _result_dicts(raw)
    detection = _first_value(records, "dt_polys", "dt_boxes")
    recognition = _first_value(records, "rec_texts", "rec_boxes", "rec_polys")
    det_scores = _first_value(records, "dt_scores")
    rec_scores = _first_value(records, "rec_scores")
    orientations = _first_value(records, "textline_orientation_angles")
    doc_result = _first_value(records, "doc_preprocessor_res")
    detection_count = _length(detection)
    recognition_count = _length(recognition)
    if recognition_count is None:
        recognition_count = _length(rec_scores)
    if detection_count is None or recognition_count is None:
        legacy_count = _legacy_count(raw)
        detection_count = detection_count if detection_count is not None else legacy_count
        recognition_count = recognition_count if recognition_count is not None else legacy_count
    return PaddleRawMetrics(
        detection_count=detection_count,
        recognition_count=recognition_count,
        recognition_yield=(recognition_count / max(detection_count, 1)) if detection_count is not None and recognition_count is not None else None,
        mean_detection_score=_mean_numbers(det_scores),
        recognition_score_threshold=_number(_first_value(records, "text_rec_score_thresh")),
        document_orientation=_orientation(doc_result),
        textline_orientations=tuple(_orientation(item) for item in (orientations or ()) if _orientation(item) is not None),
    )


def _legacy_count(raw: Any) -> int | None:
    """Count legacy ``[[poly, [text, score]], ...]`` records safely."""
    if not isinstance(raw, (list, tuple)):
        return None
    count = 0
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            payload = item[1]
            if isinstance(payload, (list, tuple)) and payload and str(payload[0]).strip():
                count += 1
            elif isinstance(item[0], (list, tuple)):
                count += 1
    return count or None


def _result_dicts(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, (list, tuple)):
        output: list[dict[str, Any]] = []
        for item in raw:
            if isinstance(item, dict):
                output.append(item)
            elif hasattr(item, "json") and isinstance(item.json, dict):
                output.append(item.json)
        return output
    if hasattr(raw, "json") and isinstance(raw.json, dict):
        return [raw.json]
    return []


def _first_value(records: list[dict[str, Any]], *names: str) -> Any:
    for record in records:
        for name in names:
            if name in record and record[name] is not None:
                return record[name]
    return None


def _length(value: Any) -> int | None:
    try:
        return len(value) if value is not None else None
    except TypeError:
        return None


def _mean_numbers(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numbers = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    return sum(numbers) / len(numbers) if numbers else None


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _orientation(value: Any) -> int | None:
    if isinstance(value, dict):
        value = value.get("angle", value.get("orientation"))
    try:
        value = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return value % 360
