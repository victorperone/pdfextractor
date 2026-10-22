"""Paddle result parsing kept with the Paddle pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from structured_pdf_text.ocr.quality import RawOCRMetrics


@dataclass(frozen=True, slots=True)
class PaddleRawMetrics(RawOCRMetrics):
    """Raw diagnostic fields exposed by supported PaddleOCR result versions.

    Missing fields stay ``None`` so callers can distinguish an unavailable
    metric from a measured zero. Shared quality scoring consumes only the
    engine-neutral count fields inherited from :class:`RawOCRMetrics`.
    """

    mean_detection_score: float | None = None
    recognition_score_threshold: float | None = None
    document_orientation: int | None = None
    textline_orientations: tuple[int, ...] = ()


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
        recognition_yield=(
            recognition_count / max(detection_count, 1)
            if detection_count is not None and recognition_count is not None else None
        ),
        mean_detection_score=_mean_numbers(det_scores),
        recognition_score_threshold=_number(_first_value(records, "text_rec_score_thresh")),
        document_orientation=_orientation(doc_result),
        textline_orientations=tuple(
            angle for item in (orientations or ())
            if (angle := _orientation(item)) is not None
        ),
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
