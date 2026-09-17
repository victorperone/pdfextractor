"""Cluster-level classification for decorative text without lexical rules."""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from statistics import median

from structured_pdf_text.document import TextLine
from structured_pdf_text.geometry import BBox


class DecorativeRole(str, Enum):
    DECORATIVE_WATERMARK = "decorative_watermark"
    SEMANTIC_STATUS = "semantic_status"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class DecorativeCluster:
    lines: tuple[TextLine, ...]
    bbox: BBox
    angle: float
    luminance: float | None
    opacity: float | None
    role: DecorativeRole
    confidence: float
    reasons: tuple[str, ...] = ()


def cluster_decorative_lines(
    lines: list[TextLine],
    page_bbox: BBox,
    body_font_median: float | None = None,
) -> tuple[DecorativeCluster, ...]:
    """Group nearby/collinear fragments before assigning a visual role."""
    candidates = [line for line in lines if line.text.strip()]
    if not candidates:
        return ()
    heights = [line.bbox.height for line in candidates if line.bbox.height > 0]
    typical_height = median(heights) if heights else 10.0
    groups: list[list[TextLine]] = []
    for line in sorted(candidates, key=lambda item: (item.bbox.y0, item.bbox.x0)):
        angle = _line_angle(line)
        target = None
        for index, group in enumerate(groups):
            anchor = group[-1]
            if abs(angle - _line_angle(anchor)) > 0.18:
                continue
            distance = _line_distance(anchor, line)
            size_ratio = _font_size(line) / max(_font_size(anchor), 0.01)
            if distance <= max(18.0, typical_height * 2.5) and 0.55 <= size_ratio <= 1.8:
                target = index
                break
        if target is None:
            groups.append([line])
        else:
            groups[target].append(line)

    clusters: list[DecorativeCluster] = []
    for group in groups:
        bbox = BBox.union_all([line.bbox for line in group])
        angles = [_line_angle(line) for line in group]
        colors = [
            token.fill_color
            for line in group
            for token in line.tokens
            if token.fill_color is not None
        ]
        luminance = (
            sum((0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]) / 255.0 for color in colors)
            / len(colors)
            if colors else None
        )
        opacity = (
            sum(color[3] / 255.0 for color in colors) / len(colors)
            if colors else None
        )
        sizes = [_font_size(line) for line in group if _font_size(line) > 0]
        large = bool(sizes and median(sizes) >= max(24.0, (body_font_median or typical_height) * 2.2))
        angled = any(min(abs(angle % math.pi), abs((angle % math.pi) - math.pi)) > 0.20 for angle in angles)
        broad = bbox.width >= page_bbox.width * 0.42 or bbox.height >= page_bbox.height * 0.42
        light = luminance is not None and luminance >= 0.78
        translucent = opacity is not None and opacity < 0.82
        reasons = tuple(
            reason for reason, enabled in (
                ("unusual_angle", angled),
                ("light_luminance", light),
                ("low_opacity", translucent),
                ("large_font", large),
                ("broad_bbox", broad),
                ("fragmented_collinear", len(group) > 1),
            ) if enabled
        )
        strong_count = sum((angled, light, translucent, broad, len(group) > 1))
        if strong_count >= 2 and not _near_page_edge(bbox, page_bbox):
            role = DecorativeRole.DECORATIVE_WATERMARK
            confidence = min(0.99, 0.55 + strong_count * 0.09)
        elif _looks_like_semantic_status(
            group=group,
            bbox=bbox,
            page_bbox=page_bbox,
            body_font_median=body_font_median,
            typical_height=typical_height,
            luminance=luminance,
            opacity=opacity,
            angled=angled,
            broad=broad,
            light=light,
            translucent=translucent,
        ):
            role = DecorativeRole.SEMANTIC_STATUS
            confidence = 0.72
        else:
            role = DecorativeRole.UNKNOWN
            confidence = 0.25
        if role != DecorativeRole.UNKNOWN:
            clusters.append(
                DecorativeCluster(
                    lines=tuple(group),
                    bbox=bbox,
                    angle=median(angles),
                    luminance=luminance,
                    opacity=opacity,
                    role=role,
                    confidence=confidence,
                    reasons=reasons,
                )
            )
    return tuple(clusters)


def _line_angle(line: TextLine) -> float:
    return float(line.baseline.angle) if line.baseline is not None else 0.0


def _font_size(line: TextLine) -> float:
    values = [token.font_size for token in line.tokens if token.font_size and token.font_size > 0]
    return median(values) if values else line.bbox.height


def _line_distance(first: TextLine, second: TextLine) -> float:
    dx = max(first.bbox.x0 - second.bbox.x1, second.bbox.x0 - first.bbox.x1, 0.0)
    dy = max(first.bbox.y0 - second.bbox.y1, second.bbox.y0 - first.bbox.y1, 0.0)
    return math.hypot(dx, dy)


def _near_page_edge(box: BBox, page: BBox) -> bool:
    return box.y0 <= page.y0 + page.height * 0.12 or box.y1 >= page.y1 - page.height * 0.12


def _looks_like_semantic_status(
    *,
    group: list[TextLine],
    bbox: BBox,
    page_bbox: BBox,
    body_font_median: float | None,
    typical_height: float,
    luminance: float | None,
    opacity: float | None,
    angled: bool,
    broad: bool,
    light: bool,
    translucent: bool,
) -> bool:
    """Require several visual signals before materializing a status cluster."""
    compact = bbox.area <= page_bbox.area * 0.10
    localized = bbox.width <= page_bbox.width * 0.45 and bbox.height <= page_bbox.height * 0.20
    near_title_band = bbox.y0 <= page_bbox.y0 + page_bbox.height * 0.20
    sizes = [_font_size(line) for line in group if _font_size(line) > 0]
    median_size = median(sizes) if sizes else typical_height
    font_above_body = median_size >= max(12.0, (body_font_median or typical_height) * 1.25)
    fragmented = len(group) > 1
    style_coherent = _style_is_coherent(group)
    dark_contrast = luminance is not None and luminance <= 0.55
    opaque_contrast = opacity is not None and opacity >= 0.82 and not light
    contrast = dark_contrast or opaque_contrast
    if not compact or not localized or near_title_band or angled or broad or light or translucent:
        return False
    if not style_coherent:
        return False
    return (contrast and (font_above_body or fragmented)) or (font_above_body and fragmented)


def _style_is_coherent(lines: list[TextLine]) -> bool:
    sizes = [_font_size(line) for line in lines if _font_size(line) > 0]
    if sizes and max(sizes) / max(min(sizes), 0.01) > 1.35:
        return False
    fonts = {
        token.font_name
        for line in lines
        for token in line.tokens
        if token.font_name
    }
    return len(fonts) <= 1
