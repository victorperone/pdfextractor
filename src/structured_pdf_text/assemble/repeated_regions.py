from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

from structured_pdf_text.document import RegionKind, StructuredPage, TextLine


def detect_repeated_headers_footers(pages: list[StructuredPage]) -> dict[str, list[int]]:
    """Confirm repeated furniture using text, position and style evidence.

    Edge position is only a candidate signal.  A line is suppressible only
    when its normalized template recurs on multiple pages and its geometry and
    typography remain compatible.  Position-only matches are intentionally not
    returned because unique body content often starts or ends in the same band.
    """
    observations: dict[str, list[tuple[int, str, TextLine, StructuredPage]]] = defaultdict(list)
    for page in pages:
        for kind, line in _candidate_lines(page):
            key = _signature_key(kind, line.text)
            if key is not None:
                observations[key].append((page.page_index, kind, line, page))

    confirmed: dict[str, list[int]] = {}
    for key, values in sorted(observations.items()):
        by_page: dict[int, tuple[str, TextLine, StructuredPage]] = {}
        for page_index, kind, line, page in values:
            by_page.setdefault(page_index, (kind, line, page))
        if len(by_page) < 2:
            continue
        selected = list(by_page.values())
        if not _stable_edge_position(selected) or not _stable_style(selected):
            continue
        confirmed[key] = sorted(by_page)
    return confirmed


def repeated_line_keys(page: StructuredPage) -> dict[str, str]:
    """Return normalized keys for candidate edge lines on one page.

    Each line maps only to its confirmed text/template signature.  Position is
    evidence for confirmation, never an independent suppression key.
    """
    result: dict[str, str] = {}
    for kind, line in _candidate_lines(page):
        text = unicodedata.normalize("NFC", line.text.strip())
        key = _signature_key(kind, text)
        if key is not None:
            result[text] = key
    return result


def _candidate_lines(page: StructuredPage) -> list[tuple[str, TextLine]]:
    """Return all lines that are candidates for header/footer detection.

    Collects from explicit HEADER/FOOTER layout regions AND from edge bands of
    all regions (to catch pages where top lines live in a TEXT region).
    Lines already found in explicit regions are not double-counted.
    """
    candidates: list[tuple[str, TextLine]] = []
    all_lines: list[TextLine] = []
    explicit_ids: set[int] = set()

    for region in page.regions:
        lines = region.native_lines
        all_lines.extend(lines)
        if region.kind == RegionKind.HEADER or region.edge_role == "top_candidate":
            for line in lines:
                candidates.append(("header", line))
                explicit_ids.add(id(line))
        elif region.kind == RegionKind.FOOTER or region.edge_role == "bottom_candidate":
            for line in lines:
                candidates.append(("footer", line))
                explicit_ids.add(id(line))

    if not all_lines:
        return candidates

    page_height = max(page.bbox.height, 1.0)
    edge_band = max(48.0, page_height * 0.05)
    top = page.bbox.y0 + edge_band
    bottom = page.bbox.y1 - edge_band

    for line in all_lines:
        if id(line) in explicit_ids:
            continue
        if line.bbox.cy <= top:
            candidates.append(("header", line))
        elif line.bbox.cy >= bottom:
            candidates.append(("footer", line))

    return candidates


def _signature_key(kind: str, text: str) -> str | None:
    signature = _normalize_signature(text)
    if len(signature) < 3:
        return None
    return f"{kind}:{signature}"


def _normalize_signature(text: str) -> str:
    value = text.casefold().strip()
    value = re.sub(r"\b(?:página|pagina|page)\s+\d+\s+(?:de|of)\s+\d+\b", "", value)
    value = re.sub(r"\b\d+\s*/\s*\d+\b", "", value)
    value = re.sub(r"\d+", "<num>", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" -|·")


def _stable_edge_position(
    values: list[tuple[str, TextLine, StructuredPage]],
) -> bool:
    normalized = [
        line.bbox.cy / max(page.bbox.height, 1.0)
        for _, line, page in values
    ]
    if not normalized:
        return False
    return max(normalized) - min(normalized) <= 0.035


def _stable_style(
    values: list[tuple[str, TextLine, StructuredPage]],
) -> bool:
    sizes: list[float] = []
    fonts: set[str] = set()
    weights: list[int] = []
    for _, line, _ in values:
        for token in line.tokens:
            if token.text.strip() and token.font_size and token.font_size > 0:
                sizes.append(token.font_size)
            if token.text.strip() and token.font_name:
                fonts.add(token.font_name.casefold())
            if token.text.strip() and token.font_weight is not None:
                weights.append(token.font_weight)
    if sizes and max(sizes) / max(min(sizes), 0.01) > 1.35:
        return False
    if len(fonts) > 1:
        return False
    if weights and max(weights) - min(weights) > 200:
        return False
    return True
