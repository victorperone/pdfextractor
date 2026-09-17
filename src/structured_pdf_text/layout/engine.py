from __future__ import annotations

from dataclasses import dataclass
import math
import re
from statistics import median
from typing import Any, Protocol

from structured_pdf_text.document import NativePageEvidence, RegionKind
from structured_pdf_text.geometry import BBox
from structured_pdf_text.layout.decorative import (
    DecorativeRole,
    cluster_decorative_lines,
)


@dataclass(frozen=True, slots=True)
class LayoutRegionPrediction:
    kind: RegionKind
    bbox: BBox
    confidence: float | None = None
    label: str | None = None
    semantic_role: str | None = None


class PageImage(Protocol):
    width: int
    height: int


class LayoutEngine(Protocol):
    def detect(self, image: PageImage) -> list[LayoutRegionPrediction]:
        """Detect structural regions on a page image."""


class NativeHeuristicLayoutEngine:
    """Small deterministic layout adapter used before a visual model is fixed.

    The engine consumes native evidence for high-value facts such as ruled
    tables and placed images. It never creates text and it keeps the public
    prediction contract independent from any vendor model.
    """

    def detect(self, image: PageImage) -> list[LayoutRegionPrediction]:
        return [
            LayoutRegionPrediction(
                kind=RegionKind.TEXT,
                bbox=BBox(0.0, 0.0, float(image.width), float(image.height)),
                confidence=0.20,
                label="heuristic_full_page",
            )
        ]

    def detect_page(
        self,
        page: NativePageEvidence,
        lines: list[Any],
        image: PageImage | None = None,
    ) -> list[LayoutRegionPrediction]:
        predictions: list[LayoutRegionPrediction] = []
        page_bbox = page.bbox
        header_lines, footer_lines = _edge_band_lines(lines)

        if header_lines:
            predictions.append(
                LayoutRegionPrediction(
                    RegionKind.HEADER,
                    _union_lines(header_lines, page_bbox),
                    confidence=0.82,
                    label="native_top_band",
                )
            )
        if footer_lines:
            predictions.append(
                LayoutRegionPrediction(
                    RegionKind.FOOTER,
                    _union_lines(footer_lines, page_bbox),
                    confidence=0.82,
                    label="native_bottom_band",
                )
            )

        table_boxes = _table_boxes(page)
        for index, bbox in enumerate(table_boxes, start=1):
            predictions.append(
                LayoutRegionPrediction(
                    RegionKind.TABLE,
                    bbox,
                    confidence=0.88,
                    label=f"native_grid_{index}",
                )
            )

        for index, image_evidence in enumerate(page.objects.images, start=1):
            if image_evidence.bbox is None:
                continue
            predictions.append(
                LayoutRegionPrediction(
                    RegionKind.FIGURE,
                    image_evidence.bbox,
                    confidence=0.90,
                    label=f"native_image_{index}",
                )
            )

        semantic = _semantic_predictions(
            lines=lines,
            page_bbox=page_bbox,
            header_lines=header_lines,
            footer_lines=footer_lines,
            excluded=[prediction.bbox for prediction in predictions],
        )
        predictions.extend(semantic)

        excluded = [prediction.bbox for prediction in predictions]
        body_lines = [
            line
            for line in lines
            if not any(line.bbox.overlap_ratio(box) >= 0.50 for box in excluded)
        ]
        if body_lines:
            predictions.append(
                LayoutRegionPrediction(
                    RegionKind.TEXT,
                    _union_lines(body_lines, page_bbox),
                    confidence=0.72,
                    label="native_remaining_text",
                )
            )
        if not predictions:
            predictions.append(
                LayoutRegionPrediction(
                    RegionKind.TEXT,
                    page_bbox,
                    confidence=0.20,
                    label="heuristic_full_page",
                )
            )
        return _normalize_predictions(predictions, page_bbox)


def _semantic_predictions(
    lines: list[Any],
    page_bbox: BBox,
    header_lines: list[Any],
    footer_lines: list[Any],
    excluded: list[BBox],
) -> list[LayoutRegionPrediction]:
    """Classify high-signal semantic bands without creating or removing text.

    This is deliberately conservative. A line becomes a semantic region only
    when its geometry or lexical marker is stronger than the generic body
    classification. The original line remains available in the assigned
    region, so a wrong label cannot reduce recall.
    """
    edge_ids = {id(line) for line in header_lines + footer_lines}
    candidates = [
        line
        for line in lines
        if id(line) not in edge_ids
        and not any(line.bbox.overlap_ratio(box) >= 0.50 for box in excluded)
        and line.text.strip()
    ]
    if not candidates:
        return []

    heights = [line.bbox.height for line in candidates if line.bbox.height > 0]
    typical_height = median(heights) if heights else 10.0
    predictions: list[LayoutRegionPrediction] = []
    decorative_clusters = cluster_decorative_lines(candidates, page_bbox)
    decorative_line_ids = {
        id(line)
        for cluster in decorative_clusters
        if cluster.role == DecorativeRole.DECORATIVE_WATERMARK
        for line in cluster.lines
    }
    semantic_status_clusters = tuple(
        cluster
        for cluster in decorative_clusters
        if cluster.role == DecorativeRole.SEMANTIC_STATUS
    )
    semantic_status_line_ids = {
        id(line)
        for cluster in semantic_status_clusters
        for line in cluster.lines
    }
    for index, cluster in enumerate(
        cluster for cluster in decorative_clusters
        if cluster.role == DecorativeRole.DECORATIVE_WATERMARK
    ):
        predictions.append(
            LayoutRegionPrediction(
                kind=RegionKind.DECORATIVE,
                bbox=_clamp_bbox(cluster.bbox.expand(3.0), page_bbox),
                confidence=cluster.confidence,
                label="decorative_cluster:" + ",".join(cluster.reasons),
            )
        )
    for cluster in semantic_status_clusters:
        predictions.append(
            LayoutRegionPrediction(
                kind=RegionKind.TEXT,
                bbox=_clamp_bbox(cluster.bbox.expand(3.0), page_bbox),
                confidence=cluster.confidence,
                label="semantic_status",
                semantic_role="semantic_status",
            )
        )
    for line in candidates:
        if id(line) in decorative_line_ids or id(line) in semantic_status_line_ids:
            continue
        text = " ".join(line.text.split())
        lower = text.casefold()
        kind: RegionKind | None = None
        confidence = 0.70
        label = None

        if _is_footnote(line, text, lower, page_bbox, typical_height):
            kind = RegionKind.FOOTNOTE
            confidence = 0.76
            label = "native_bottom_note"
        elif _is_decorative(line, page_bbox, typical_height):
            kind = RegionKind.DECORATIVE
            confidence = 0.84
            label = "native_decorative_text:" + ",".join(
                _decorative_reasons(line, page_bbox, typical_height)
            )
        elif _is_caption(line, text, lower, page_bbox):
            kind = RegionKind.CAPTION
            confidence = 0.78
            label = "native_caption_marker"
        elif _is_title(line, text, page_bbox, typical_height):
            kind = RegionKind.TITLE
            confidence = 0.74
            label = "native_heading_geometry"
        elif _is_marginalia(line, page_bbox):
            kind = RegionKind.MARGINALIA
            confidence = 0.62
            label = "native_edge_annotation"

        if kind is not None:
            predictions.append(
                LayoutRegionPrediction(
                    kind=kind,
                    bbox=_clamp_bbox(line.bbox.expand(3.0), page_bbox),
                    confidence=confidence,
                    label=label,
                )
            )
    return predictions


def _is_decorative(line: Any, page_bbox: BBox, typical_height: float) -> bool:
    """Require independent style and geometry signals before hiding text."""
    return len(_decorative_reasons(line, page_bbox, typical_height)) >= 2


def _decorative_reasons(line: Any, page_bbox: BBox, typical_height: float) -> tuple[str, ...]:
    tokens = [token for token in line.tokens if token.text.strip()]
    if not tokens:
        return ()
    reasons: list[str] = []
    sizes = [token.font_size for token in tokens if token.font_size and token.font_size > 0]
    large_font = bool(sizes and median(sizes) >= max(24.0, typical_height * 2.5))
    rotated = _line_is_rotated(line)
    broad_span = line.bbox.width >= page_bbox.width * 0.45 and line.bbox.height >= typical_height * 1.8
    colors = [token.fill_color for token in tokens if token.fill_color is not None]
    opaque = [color[3] / 255.0 >= 0.82 for color in colors]
    luminance = [
        (0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]) / 255.0
        for color in colors
    ]
    light = bool(luminance and sum(value >= 0.78 for value in luminance) / len(luminance) >= 0.70)
    translucent = bool(opaque and sum(not value for value in opaque) / len(opaque) >= 0.70)

    # A narrow glyph is not rotation evidence.  A rotated stamp or watermark
    # must be backed by span/contrast evidence; a large ordinary title alone
    # never becomes decorative.
    if rotated:
        reasons.append("rotated")
    if light:
        reasons.append("light_luminance")
    if translucent:
        reasons.append("low_opacity")
    if large_font:
        reasons.append("large_font")
    if broad_span:
        reasons.append("broad_span")
    if line.bbox.y0 <= page_bbox.y0 + page_bbox.height * 0.12:
        return ()
    if not (
        (rotated and (broad_span or light or translucent))
        or (light and broad_span and (large_font or translucent))
        or (large_font and light)
    ):
        return ()
    return tuple(reasons)


def _line_is_rotated(line: Any) -> bool:
    baseline = getattr(line, "baseline", None)
    angle = getattr(baseline, "angle", None)
    if angle is None:
        return False
    normalized = float(angle) % math.pi
    distance = min(normalized, math.pi - normalized)
    return distance > 0.20


def _is_title(line: Any, text: str, page_bbox: BBox, typical_height: float) -> bool:
    # Punctuation-only fragments are often rules, OCR residue, or decorative
    # marks. They are never semantic headings regardless of their size.
    if not text or not any(character.isalnum() for character in text) or len(text) > 140:
        return False
    near_top = line.bbox.y0 <= page_bbox.y0 + page_bbox.height * 0.18
    centered = abs(line.bbox.cx - page_bbox.cx) <= page_bbox.width * 0.16
    style_score = _heading_style_score(line, typical_height)
    if not near_top and not centered and style_score < 2:
        return False
    if line.bbox.height < max(12.0, typical_height * (1.35 if near_top else 1.55)) and style_score < 2:
        return False
    if text.endswith((".", ";", ":")) and len(text) > 45:
        return False
    return True


def _heading_style_score(line: Any, typical_height: float) -> int:
    tokens = [token for token in line.tokens if token.text.strip()]
    sizes = [token.font_size for token in tokens if token.font_size and token.font_size > 0]
    size_ratio = median(sizes) / max(typical_height, 1.0) if sizes else 0.0
    bold = any(
        (token.font_weight is not None and token.font_weight >= 600)
        or (token.font_name and "bold" in token.font_name.casefold())
        for token in tokens
    )
    score = int(size_ratio >= 1.35) + int(bold)
    if size_ratio >= 1.70:
        score += 1
    return score


def _is_caption(line: Any, text: str, lower: str, page_bbox: BBox) -> bool:
    marker = re.match(r"^(figura|fig\.?|gráfico|grafico|imagem|quadro|fonte|nota)\b", lower)
    if marker is None:
        return False
    # A note in the middle of ordinary prose is not a footnote/caption.
    if line.bbox.y0 <= page_bbox.y0 + page_bbox.height * 0.12:
        return False
    return len(text) <= 220


def _is_footnote(
    line: Any,
    text: str,
    lower: str,
    page_bbox: BBox,
    typical_height: float,
) -> bool:
    if line.bbox.y0 < page_bbox.y0 + page_bbox.height * 0.68:
        return False
    if line.bbox.height > typical_height * 1.30:
        return False
    return bool(
        re.match(r"^(nota|fonte|\*|†|‡|\d+[.)])\b", lower)
        or lower.startswith("observação")
        or lower.startswith("observacao")
    )


def _is_marginalia(line: Any, page_bbox: BBox) -> bool:
    if line.bbox.width >= page_bbox.width * 0.28:
        return False
    near_edge = (
        line.bbox.x0 <= page_bbox.x0 + page_bbox.width * 0.06
        or line.bbox.x1 >= page_bbox.x1 - page_bbox.width * 0.06
    )
    tall_glyph_run = line.bbox.height > max(24.0, line.bbox.width * 1.35)
    return near_edge and tall_glyph_run and len(line.text.strip()) <= 80


def _table_boxes(page: NativePageEvidence) -> list[BBox]:
    from structured_pdf_text.tables.geometry import detect_path_table_candidates
    return [
        _clamp_bbox(candidate.bbox.expand(8.0), page.bbox)
        for candidate in detect_path_table_candidates(page)
    ]


def _edge_band_lines(lines: list[Any]) -> tuple[list[Any], list[Any]]:
    """Select compact top/bottom bands without swallowing nearby content.

    A fixed percentage of page height classifies large titles and short
    paragraphs as headers on ordinary pages. The distance is therefore tied
    to the observed line height and to the first/last line instead.
    """
    if not lines:
        return [], []
    heights = [line.bbox.height for line in lines if line.bbox.height > 0]
    line_height = median(heights) if heights else 10.0
    band_gap = max(14.0, line_height * 2.0)
    ordered = sorted(lines, key=lambda line: (line.bbox.y0, line.bbox.x0))
    first_y = ordered[0].bbox.y0
    last_y = ordered[-1].bbox.y0
    header_lines = [line for line in ordered if line.bbox.y0 <= first_y + band_gap]
    footer_lines = [line for line in ordered if line.bbox.y0 >= last_y - band_gap]
    header_ids = {id(line) for line in header_lines}
    footer_lines = [line for line in footer_lines if id(line) not in header_ids]
    return header_lines, footer_lines


def _union_lines(lines: list[Any], page_bbox: BBox) -> BBox:
    return _clamp_bbox(BBox.union_all([line.bbox for line in lines]).expand(3.0), page_bbox)


def _clamp_bbox(box: BBox, page_bbox: BBox) -> BBox:
    def clamp(value: float, lower: float, upper: float) -> float:
        return min(upper, max(lower, value))

    return BBox(
        clamp(box.x0, page_bbox.x0, page_bbox.x1),
        clamp(box.y0, page_bbox.y0, page_bbox.y1),
        clamp(box.x1, page_bbox.x0, page_bbox.x1),
        clamp(box.y1, page_bbox.y0, page_bbox.y1),
    )


def _is_page_background(box: BBox, page_bbox: BBox) -> bool:
    return box.overlap_ratio(page_bbox) >= 0.98 and box.area / max(page_bbox.area, 1.0) >= 0.90


def _normalize_predictions(
    predictions: list[LayoutRegionPrediction],
    page_bbox: BBox,
) -> list[LayoutRegionPrediction]:
    normalized: list[LayoutRegionPrediction] = []
    for prediction in predictions:
        bbox = _clamp_bbox(prediction.bbox, page_bbox)
        if bbox.area <= 0:
            continue
        normalized.append(
            LayoutRegionPrediction(
                kind=prediction.kind,
                bbox=bbox,
                confidence=prediction.confidence,
                label=prediction.label,
                semantic_role=prediction.semantic_role,
            )
        )
    return normalized
