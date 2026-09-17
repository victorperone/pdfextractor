"""Generic, language-independent OCR quality assessment."""
from __future__ import annotations

import math
import statistics
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable

from structured_pdf_text.config import OcrQualityThresholds
from structured_pdf_text.document import OcrToken


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
        or control > 0.10
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
    thresholds = thresholds or OcrQualityThresholds()
    token_count = len(values)
    chars = [_text_chars(token.text) for token in values]
    character_count = sum(len(item) for item in chars)
    weights = [max(1, sum(char.isalnum() for char in item)) for item in chars]
    confidences = [_confidence(token) for token in values]
    weighted_total = sum(confidence * weight for confidence, weight in zip(confidences, weights))
    char_weighted = weighted_total / sum(weights) if weights else 0.0
    median_conf = statistics.median(confidences) if confidences else 0.0
    lower_quartile = _percentile(confidences, 0.25)
    low_chars = sum(weight for weight, confidence in zip(weights, confidences) if confidence < thresholds.low_confidence_threshold)
    low_ratio = low_chars / sum(weights) if weights else 0.0
    total_chars = max(1, character_count)
    printable = sum(char.isprintable() for item in chars for char in item) / total_chars
    alphanumeric = sum(char.isalnum() for item in chars for char in item) / total_chars
    suspicious = sum(is_suspicious_token(token) for token in values) / max(1, token_count)
    horizontal = sum(token.bbox.width >= token.bbox.height * 1.15 for token in values) / max(1, token_count)
    metrics = raw_metrics or PaddleRawMetrics()
    reasons: list[str] = []
    if not values:
        reasons.append("no_tokens")
    if char_weighted < thresholds.severe_mean_confidence:
        reasons.append("low_weighted_confidence")
    if lower_quartile < thresholds.strong_lower_quartile:
        reasons.append("weak_lower_quartile")
    if low_ratio > thresholds.max_low_confidence_char_ratio:
        reasons.append("low_confidence_characters")
    if printable < thresholds.minimum_printable_ratio:
        reasons.append("low_printable_ratio")
    if suspicious > 0.20:
        reasons.append("suspicious_tokens")
    if metrics.recognition_yield is not None and metrics.recognition_yield < 0.65:
        reasons.append("low_recognition_yield")
    if orientation_incoherent:
        reasons.append("orientation_incoherent")
    sufficient = bool(values) and not reasons and char_weighted >= thresholds.strong_mean_confidence
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
