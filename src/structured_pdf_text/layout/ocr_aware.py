"""OCR-aware layout re-classification pass (§27).

After EasyOCR produces reconstructed text lines for a page, this module
performs a second layout pass using OCR evidence to improve region
classification.  It does not replace the primary layout engine; it refines
the output of the primary pass using signals that are only available after
OCR runs.

Entry point: :func:`reclassify_ocr_regions`.

Features analysed per region
-----------------------------
- TITLE detection: OCR line height ratio vs document body median.
- LIST detection: bullet/numbered prefix patterns (§32 complement).
- CAPTION detection: short text below/above a TABLE or FIGURE region.
- HEADER/FOOTER detection: position near page top/bottom + repetition
  across pages (complements existing header/footer logic).
- Unknown → TEXT downgrade: regions that are still UNKNOWN but have
  OCR lines are promoted to TEXT so they participate in reading order.

Design constraints
------------------
- Never alters TABLE or FIGURE regions (those are anchor regions from the
  primary pass and have stable geometry).
- Never removes or reorders regions — only changes ``kind``.
- Operates on copies produced via dataclasses.replace(); the input list
  is not mutated.
- All heuristics are threshold-based with no external model dependencies.
"""
from __future__ import annotations

import dataclasses
import re
import statistics
from typing import TYPE_CHECKING

from structured_pdf_text.document import LayoutRegion, RegionKind, TextLine
from structured_pdf_text.geometry import BBox

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Bullet / list prefix patterns (complement to §32 merge_ocr_bullet_markers)
# ---------------------------------------------------------------------------

_BULLET_RE = re.compile(
    r"^(?:"
    r"[•◦▪‣·●○■–—*-]\s+"          # symbol bullets and dashes
    r"|(?:\d+|[a-zA-Z])[.)]\s+"   # 1. 1) a. a)
    r"|(?:i{1,3}|iv|vi{0,3}|ix)[.)]\s+"  # roman numerals i. iv) etc.
    r")"
)


def _is_list_line(line: TextLine) -> bool:
    text = line.text.strip()
    return bool(_BULLET_RE.match(text))


def _list_ratio(lines: list[TextLine]) -> float:
    if not lines:
        return 0.0
    count = sum(1 for l in lines if _is_list_line(l))
    return count / len(lines)


# ---------------------------------------------------------------------------
# Caption detection
# ---------------------------------------------------------------------------

_CAPTION_RE = re.compile(
    r"^(?:figura|figure|fig\.?|tabela|table|tab\.?|quadro|gráfico|graph|chart)\b",
    re.IGNORECASE,
)
_CAPTION_MAX_LINES = 3
_CAPTION_MAX_CHARS = 200


def _is_caption(region: LayoutRegion) -> bool:
    lines = region.ocr_lines or region.native_lines
    if not lines or len(lines) > _CAPTION_MAX_LINES:
        return False
    text = " ".join(l.text for l in lines).strip()
    if len(text) > _CAPTION_MAX_CHARS:
        return False
    return bool(_CAPTION_RE.match(text))


# ---------------------------------------------------------------------------
# OCR line height helpers
# ---------------------------------------------------------------------------

def _ocr_line_heights(region: LayoutRegion) -> list[float]:
    return [l.bbox.height for l in region.ocr_lines if l.bbox.height > 0]


def _body_ocr_height_median(regions: list[LayoutRegion]) -> float | None:
    _prose_kinds = {RegionKind.TEXT, RegionKind.LIST, RegionKind.CAPTION, RegionKind.UNKNOWN}
    heights: list[float] = []
    for r in regions:
        if r.kind in _prose_kinds:
            heights.extend(_ocr_line_heights(r))
    return statistics.median(heights) if heights else None


def _region_height_ratio(region: LayoutRegion, body_median: float) -> float:
    heights = _ocr_line_heights(region)
    if not heights or body_median <= 0:
        return 1.0
    return statistics.median(heights) / body_median


# ---------------------------------------------------------------------------
# Per-region reclassification
# ---------------------------------------------------------------------------

_TITLE_HEIGHT_RATIO_THRESHOLD = 1.3   # OCR line height >= 1.3× body → title candidate
_LIST_RATIO_THRESHOLD = 0.5           # ≥50% of lines start with a bullet → list region
_HEADER_FOOTER_BAND = 0.10            # top/bottom 10% of page height


def _reclassify_one(
    region: LayoutRegion,
    body_median: float | None,
    page_height: float,
) -> LayoutRegion:
    """Return a reclassified copy of ``region``, or the original if unchanged."""
    kind = region.kind

    # Never touch TABLE or FIGURE — those are anchor regions.
    if kind in (RegionKind.TABLE, RegionKind.FIGURE):
        return region

    lines = region.ocr_lines
    if not lines:
        return region

    # UNKNOWN → TEXT: promote if OCR lines are present so the region
    # participates in reading order and paragraph segmentation.
    if kind == RegionKind.UNKNOWN:
        kind = RegionKind.TEXT

    # CAPTION detection: short intro text near a table/figure.
    # Only upgrade TEXT/UNKNOWN to CAPTION (never downgrade TITLE).
    if kind == RegionKind.TEXT and _is_caption(region):
        kind = RegionKind.CAPTION

    # LIST detection: most lines start with a bullet/number.
    if kind == RegionKind.TEXT and _list_ratio(lines) >= _LIST_RATIO_THRESHOLD:
        kind = RegionKind.LIST

    # TITLE detection via OCR line height ratio (§28 complement).
    # Only upgrade TEXT to TITLE; do not re-classify LIST/CAPTION.
    if kind == RegionKind.TEXT and body_median is not None:
        ratio = _region_height_ratio(region, body_median)
        if ratio >= _TITLE_HEIGHT_RATIO_THRESHOLD:
            kind = RegionKind.TITLE

    # HEADER/FOOTER detection via position.
    if kind == RegionKind.TEXT and page_height > 0:
        top_frac = region.bbox.y0 / page_height
        bot_frac = region.bbox.y1 / page_height
        if top_frac < _HEADER_FOOTER_BAND:
            kind = RegionKind.HEADER
        elif bot_frac > (1.0 - _HEADER_FOOTER_BAND):
            kind = RegionKind.FOOTER

    if kind == region.kind:
        return region
    return dataclasses.replace(region, kind=kind)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def reclassify_ocr_regions(
    regions: list[LayoutRegion],
    *,
    page_height: float = 0.0,
) -> list[LayoutRegion]:
    """Apply OCR-aware reclassification to a list of layout regions.

    Computes document-level OCR line height statistics, then iterates over each
    region and re-classifies it based on OCR evidence.  Returns a new list;
    the input is not mutated.

    Args:
        regions:     List of layout regions after primary layout pass and OCR.
        page_height: Height of the page in PDF units (for header/footer detection).

    Returns:
        New list of LayoutRegion instances; unchanged regions are the same
        objects as in the input list (no copy overhead).
    """
    if not regions:
        return regions

    body_median = _body_ocr_height_median(regions)
    return [_reclassify_one(r, body_median, page_height) for r in regions]


def reconstruct_ocr_layout(
    regions: list[LayoutRegion], page_bbox: BBox
) -> list[LayoutRegion]:
    """Split OCR-only text areas into semantic regions before page assembly.

    Layout detectors based on native PDF text cannot classify a scanned page.
    This pass uses normalized OCR line geometry to recover headings, lists,
    footnotes, and edge furniture while leaving table and figure anchors intact.
    Each input OCR line is assigned to exactly one output region.
    """
    if not regions:
        return regions
    output: list[LayoutRegion] = []
    for region in regions:
        if region.kind in {RegionKind.TABLE, RegionKind.FIGURE} or not region.ocr_lines:
            output.append(region)
            continue
        if region.native_lines:
            output.append(_reclassify_one(region, _body_ocr_height_median(regions), page_bbox.height))
            continue

        lines = _order_ocr_lines_by_columns(region.ocr_lines, page_bbox)
        heights = [line.bbox.height for line in lines if line.bbox.height > 0]
        body_height = statistics.median(heights) if heights else 1.0
        assigned: list[tuple[TextLine, RegionKind]] = []
        for index, line in enumerate(lines):
            text = line.text.strip()
            previous = lines[index - 1] if index else None
            same_flow = bool(
                previous
                and abs(previous.bbox.x0 - line.bbox.x0) <= page_bbox.width * 0.12
            )
            gap_above = (
                line.bbox.y0 - previous.bbox.y1
                if same_flow and previous
                else (line.bbox.y0 if previous is None else 0.0)
            )
            ratio = line.bbox.height / body_height if body_height else 1.0
            top = line.bbox.y0 <= page_bbox.height * 0.08
            bottom = line.bbox.y1 >= page_bbox.height * 0.84
            marker = _BULLET_RE.match(text)
            uppercase = sum(char.isupper() for char in text if char.isalpha())
            letters = sum(char.isalpha() for char in text)
            uppercase_ratio = uppercase / max(letters, 1)
            short_heading = 2 <= len(text) <= 120 and not text.endswith(('.', ',', ';'))
            centered = abs(line.bbox.cx - page_bbox.width / 2) <= page_bbox.width * 0.18
            numbered = bool(re.match(r"^(?:\d+(?:\.\d+)*\.?|cap[ií]tulo|se[cç][aã]o|anexo|ap[eê]ndice)\b", text, re.I))
            if marker:
                kind = RegionKind.LIST
            elif _CAPTION_RE.match(text) and len(text) <= _CAPTION_MAX_CHARS:
                kind = RegionKind.CAPTION
            elif bottom and ratio < 0.88:
                kind = RegionKind.FOOTNOTE
            elif short_heading and (ratio >= 1.28 or (gap_above > body_height * 2.5 and (centered or numbered or uppercase_ratio >= 0.65))):
                kind = RegionKind.TITLE
            elif top and (centered or uppercase_ratio >= 0.75):
                kind = RegionKind.HEADER
            elif bottom and (len(text) < 100 or ratio < 1.0):
                kind = RegionKind.FOOTER
            else:
                kind = RegionKind.TEXT
            assigned.append((line, kind))

        groups: list[tuple[RegionKind, list[TextLine]]] = []
        for line, kind in assigned:
            if groups and groups[-1][0] == kind:
                groups[-1][1].append(line)
            else:
                groups.append((kind, [line]))
        for group_index, (kind, group_lines) in enumerate(groups, start=1):
            bbox = BBox.union_all([line.bbox for line in group_lines])
            tokens = [
                token for token in region.ocr_tokens
                if token.bbox.cx >= bbox.x0 and token.bbox.cx <= bbox.x1
                and token.bbox.cy >= bbox.y0 and token.bbox.cy <= bbox.y1
            ]
            output.append(dataclasses.replace(
                region,
                region_id=f"{region.region_id}:ocr-{group_index}",
                kind=kind,
                bbox=bbox,
                native_lines=[],
                ocr_lines=list(group_lines),
                ocr_tokens=tokens,
                heading_level=None,
            ))
    return reclassify_ocr_regions(output, page_height=page_bbox.height)


def _order_ocr_lines_by_columns(lines: list[TextLine], page_bbox: BBox) -> list[TextLine]:
    """Detect stable OCR text columns and order each column top-to-bottom.

    The split uses persistent left-edge gaps between ordinary-width lines.
    Short headings, captions and full-width lines stay outside the column
    anchors and are placed before or after the column flow according to their
    vertical position.  When the geometry does not show a clear gutter, the
    original top-to-bottom order is retained.
    """
    visual_order = sorted(lines, key=lambda line: (line.bbox.y0, line.bbox.x0))
    page_width = page_bbox.width
    if page_width <= 0 or len(visual_order) < 4:
        return visual_order

    anchored = [line for line in visual_order if line.bbox.width <= page_width * 0.76]
    x_values = sorted(line.bbox.x0 for line in anchored)
    if len(x_values) < 4:
        return visual_order
    gaps = [x_values[index + 1] - x_values[index] for index in range(len(x_values) - 1)]
    # Choose a clear gutter; this avoids mistaking normal paragraph indents for
    # columns while still supporting two and three-column scans.
    split_indices = [index for index, gap in enumerate(gaps) if gap >= page_width * 0.16]
    if not split_indices:
        return visual_order
    groups: list[list[float]] = []
    start = 0
    for index in split_indices:
        groups.append(x_values[start : index + 1])
        start = index + 1
    groups.append(x_values[start:])
    groups = [group for group in groups if group]
    if len(groups) < 2 or any(
        sum(1 for line in anchored if group[0] <= line.bbox.x0 <= group[-1]) < 2
        for group in groups
    ):
        return visual_order

    anchors = [statistics.median(group) for group in groups]
    columns: list[list[TextLine]] = [[] for _ in anchors]
    spanning: list[TextLine] = []
    for line in visual_order:
        if line.bbox.width > page_width * 0.76:
            spanning.append(line)
            continue
        column = min(range(len(anchors)), key=lambda index: abs(line.bbox.x0 - anchors[index]))
        columns[column].append(line)
    nonempty = [column for column in columns if column]
    if len(nonempty) < 2:
        return visual_order
    first_column_y = min(line.bbox.y0 for column in nonempty for line in column)
    last_column_y = max(line.bbox.y1 for column in nonempty for line in column)
    median_height = statistics.median(line.bbox.height for column in nonempty for line in column)
    leading = sorted(
        (line for line in spanning if line.bbox.cy <= first_column_y + median_height * 2),
        key=lambda line: (line.bbox.y0, line.bbox.x0),
    )
    trailing = sorted(
        (line for line in spanning if line.bbox.cy >= last_column_y - median_height * 2),
        key=lambda line: (line.bbox.y0, line.bbox.x0),
    )
    middle = [
        line for line in spanning
        if line not in leading and line not in trailing
    ]
    # A full-width line in the middle usually introduces a subsection; put it
    # immediately before the next column flow rather than losing its position.
    result = list(leading)
    for column in nonempty:
        result.extend(sorted(column, key=lambda line: (line.bbox.y0, line.bbox.x0)))
        between = [line for line in middle if line.bbox.y0 <= max(item.bbox.y1 for item in column)]
        result.extend(line for line in sorted(between, key=lambda item: (item.bbox.y0, item.bbox.x0)) if line not in result)
    result.extend(line for line in middle if line not in result)
    result.extend(trailing)
    return result
