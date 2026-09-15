from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from statistics import median

from structured_pdf_text.document import RegionKind, StructuredPage, TextLine


def detect_repeated_headers_footers(pages: list[StructuredPage]) -> dict[str, list[int]]:
    """Find stable header/footer signatures without altering page content.

    Two detection strategies are combined:
    1. Text-based: same normalized text appears in edge positions on 2+ pages.
    2. Positional: lines at the same y-band (top/bottom 15%) on 60%+ of pages,
       regardless of text content. Catches varying headers like "GS2 P19 CONTROLE".

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
    text_based = {
        key: sorted(page_indexes)
        for key, page_indexes in sorted(occurrences.items())
        if len(page_indexes) >= 2
    }
    positional = _detect_positional_headers(pages)
    combined = dict(text_based)
    combined.update(positional)
    return combined


def _detect_positional_headers(pages: list[StructuredPage]) -> dict[str, list[int]]:
    """Find y-position bands in header/footer zones repeated on 60%+ of pages.

    Returns keys of the form ``pos:<kind>:<bucket>`` that match the keys emitted
    by ``repeated_line_keys`` for lines in those bands.
    """
    if len(pages) < 3:
        return {}
    threshold = max(2, int(len(pages) * 0.60))
    position_pages: dict[str, set[int]] = defaultdict(set)
    for page in pages:
        if page.bbox.height <= 0:
            continue
        for kind, line in _candidate_lines(page):
            bucket = _position_bucket(kind, line, page)
            if bucket is not None:
                position_pages[bucket].add(page.page_index)
    return {
        key: sorted(page_set)
        for key, page_set in position_pages.items()
        if len(page_set) >= threshold
    }


def repeated_line_keys(page: StructuredPage) -> dict[str, str]:
    """Return normalized keys for candidate edge lines on one page.

    Each line's text maps to both its text-based key and positional key so that
    ``_reading_page_text`` can filter lines that match either strategy.
    """
    result: dict[str, str] = {}
    for kind, line in _candidate_lines(page):
        text = unicodedata.normalize("NFC", line.text.strip())
        key = _signature_key(kind, text)
        if key is not None:
            result[text] = key
        pos_bucket = _position_bucket(kind, line, page)
        if pos_bucket is not None:
            result.setdefault(text, pos_bucket)
            result[f"__pos__{text}"] = pos_bucket
    return result


def _position_bucket(kind: str, line: TextLine, page: StructuredPage) -> str | None:
    if page.bbox.height <= 0:
        return None
    # Use absolute y-position rounded to 5pt so headers at the same distance
    # from the top/bottom are clustered regardless of varying page heights.
    abs_y = round(line.bbox.cy / 5) * 5
    return f"pos:{kind}:{abs_y}"


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
        if region.kind == RegionKind.HEADER:
            for line in lines:
                candidates.append(("header", line))
                explicit_ids.add(id(line))
        elif region.kind == RegionKind.FOOTER:
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
    value = re.sub(r"\s+", " ", value)
    return value.strip(" -|·")
