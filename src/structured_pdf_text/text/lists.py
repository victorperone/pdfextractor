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
    for line in all_lines:
        parsed = parse_list_marker(line.text)
        if parsed:
            candidates.append((line, parsed[0], parsed[1]))
    if len(candidates) < 2:
        return []
    indents = sorted({round(line.bbox.x0, 2) for line, _, _ in candidates})
    def level(x: float) -> int:
        return min(range(len(indents)), key=lambda index: abs(indents[index] - x))
    items: list[StructuredListItem] = []
    for index, (line, marker, text) in enumerate(candidates):
        continuation: list[str] = []
        start = all_lines.index(line) + 1
        stop = all_lines.index(candidates[index + 1][0]) if index + 1 < len(candidates) else len(all_lines)
        for extra in all_lines[start:stop]:
            if extra.bbox.x0 >= line.bbox.x0 and extra.text.strip():
                continuation.append(extra.text.strip())
        if continuation:
            text = " ".join([text, *continuation])
        items.append(StructuredListItem(marker, text, level(line.bbox.x0), line.bbox, index, _line_confidence(line)))
    return items


def _line_confidence(line: TextLine) -> float | None:
    values = [token.confidence for token in line.tokens if token.confidence is not None]
    return sum(values) / len(values) if values else None
