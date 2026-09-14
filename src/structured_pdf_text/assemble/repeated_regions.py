from __future__ import annotations

import re
from collections import defaultdict
from statistics import median

from structured_pdf_text.document import LayoutRegion, RegionKind, StructuredPage, TextLine


def detect_repeated_headers_footers(pages: list[StructuredPage]) -> dict[str, list[int]]:
    """Find stable header/footer signatures without altering page content.

    Page counters are removed from signatures, but all other text is retained.
    A region is considered repeated only after appearing on at least two pages,
    which keeps one-off titles and body text out of the document policy.
    Page indexes in the result follow ``StructuredPage.page_index`` (zero-based).
    """
    occurrences: dict[str, set[int]] = defaultdict(set)
    for page in pages:
        seen_on_page: set[str] = set()
        for kind, line in _candidate_lines(page):
            key = _signature_key(kind, line.text)
            if key is not None and key not in seen_on_page:
                occurrences[key].add(page.page_index)
                seen_on_page.add(key)
    return {
        key: sorted(page_indexes)
        for key, page_indexes in sorted(occurrences.items())
        if len(page_indexes) >= 2
    }


def repeated_line_keys(page: StructuredPage) -> dict[str, str]:
    """Return normalized keys for candidate edge lines on one page."""
    result: dict[str, str] = {}
    for kind, line in _candidate_lines(page):
        key = _signature_key(kind, line.text)
        if key is not None:
            result[line.text.strip()] = key
    return result


def _candidate_lines(page: StructuredPage) -> list[tuple[str, TextLine]]:
    candidates: list[tuple[str, TextLine]] = []
    all_lines: list[TextLine] = []
    for region in page.regions:
        lines = region.native_lines
        all_lines.extend(lines)
        if region.kind == RegionKind.HEADER:
            candidates.extend(("header", line) for line in lines)
        elif region.kind == RegionKind.FOOTER:
            candidates.extend(("footer", line) for line in lines)
    if candidates:
        return candidates
    if not all_lines:
        return []
    heights = [line.bbox.height for line in all_lines if line.bbox.height > 0]
    edge_band = max(24.0, (median(heights) if heights else 10.0) * 2.5)
    top = page.bbox.y0 + edge_band
    bottom = page.bbox.y1 - edge_band
    for line in all_lines:
        if line.bbox.y0 <= top:
            candidates.append(("header", line))
        elif line.bbox.y1 >= bottom:
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
    value = re.sub(r"\s+", " ", value)
    return value.strip(" -|·")
