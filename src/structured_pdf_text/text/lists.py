"""Conservative list marker and nested-list reconstruction."""
from __future__ import annotations

import re
from dataclasses import dataclass

from structured_pdf_text.document import StructuredListItem, TextLine


_MARKER = re.compile(r"^\s*(?P<marker>[•◦▪‣*]|[-–—]|(?:\d+|[A-Za-z]|[IVXivx]+)[.)])\s+(?P<text>.+?)\s*$")


def parse_list_marker(text: str) -> tuple[str, str] | None:
    match = _MARKER.match(text)
    if match is None:
        return None
    return match.group("marker"), match.group("text")


def extract_list_items(lines: list[TextLine]) -> list[StructuredListItem]:
    candidates: list[tuple[TextLine, str, str]] = []
    all_lines = list(lines)
    candidate_positions: list[int] = []
    for position, line in enumerate(all_lines):
        parsed = parse_list_marker(line.text)
        if parsed:
            candidates.append((line, parsed[0], parsed[1]))
            candidate_positions.append(position)
    if len(candidates) < 2:
        return []

    line_heights = [line.bbox.height for line in all_lines if line.bbox.height > 0]
    indent_tolerance = max(3.0, (sum(line_heights) / len(line_heights) if line_heights else 10.0) * 0.25)
    indent_centers: list[float] = []
    for x0 in sorted(line.bbox.x0 for line, _, _ in candidates):
        if not indent_centers or x0 - indent_centers[-1] > indent_tolerance:
            indent_centers.append(x0)
        else:
            indent_centers[-1] = (indent_centers[-1] + x0) / 2.0

    def level(x: float) -> int:
        return min(range(len(indent_centers)), key=lambda index: abs(indent_centers[index] - x))

    items: list[StructuredListItem] = []
    for index, (line, marker, text) in enumerate(candidates):
        continuation: list[str] = []
        start = candidate_positions[index] + 1
        stop = candidate_positions[index + 1] if index + 1 < len(candidates) else len(all_lines)
        text_start_x = _text_start_x(line, marker)
        line_height = max(line.bbox.height, 8.0)
        for extra in all_lines[start:stop]:
            gap = extra.bbox.y0 - line.bbox.y1
            same_indent = abs(extra.bbox.x0 - text_start_x) <= max(3.0, line_height * 0.55)
            is_new_block = (
                extra.text.strip()
                and (
                    extra.bbox.y0 < line.bbox.y1 - line_height * 0.25
                    or gap > line_height * 1.8
                    or extra.bbox.x0 < line.bbox.x0 - indent_tolerance
                )
            )
            if extra.text.strip() and same_indent and not is_new_block:
                continuation.append(extra.text.strip())
        if continuation:
            text = " ".join([text, *continuation])
        items.append(StructuredListItem(marker, text, level(line.bbox.x0), line.bbox, index, _line_confidence(line)))
    return items


def _text_start_x(line: TextLine, marker: str) -> float:
    """Approximate the text column after a marker without using exact glyph widths."""
    marker_width = max(len(marker), 1) * max(line.bbox.height * 0.65, 4.0)
    return line.bbox.x0 + min(max(line.bbox.height, marker_width), line.bbox.width * 0.40)


def _line_confidence(line: TextLine) -> float | None:
    values = [token.confidence for token in line.tokens if token.confidence is not None]
    return sum(values) / len(values) if values else None
