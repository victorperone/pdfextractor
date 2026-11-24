"""Repeated header and footer detection across a multi-page document.

A line is classified as repeated furniture only when its normalised text
template recurs on multiple pages **and** its vertical position and typography
are stable. Pure position matching is deliberately excluded because unique body
content often begins or ends in the same page band as genuine headers/footers.
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from statistics import median

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
        # When native and OCR observations coexist, OCR positions may be
        # noisier than native PDF coordinates.  Prefer native-only observations
        # for stability checks so that variable OCR vertical positions do not
        # incorrectly discard confirmed furniture keys.
        native_selected = [
            (k, l, p) for k, l, p in selected
            if l.line_id is not None and l.line_id.startswith("native:")
        ]
        check_selected = native_selected if len(native_selected) >= 2 else selected
        if not _stable_edge_position(check_selected) or not _stable_style(check_selected):
            continue
        confirmed[key] = sorted(by_page)
    _confirm_ocr_variants(observations, confirmed)
    _confirm_page_counters(observations, confirmed)
    return confirmed


# T3-P43: running furniture often carries the page number without a
# "Página/Page" label ("Folha 12", "Coleção · 012", "Relatório | 03/84").
# The number makes every page's signature unique, so the exact repetition test
# never confirms the line. A number is accepted as a page counter only when
# several independent pieces of evidence agree; a value equal to the page
# index is never enough on its own:
#   * the line is an edge candidate (same edge-band test as any furniture) and
#     is not vetoed as a semantic identifier (``_SEMANTIC_CONTROL_RE``);
#   * apart from the numbers, every occurrence has the same text form;
#   * each varying number follows the pagination with one constant offset on
#     every occurrence (a counter that restarts, skips or repeats is rejected
#     as a whole, so its lines are preserved);
#   * the occurrences include a run of consecutive pages, as running furniture
#     does: a line repeated only on scattered pages (a table continuation
#     label, an annex sheet) is content placed where the content needs it;
#   * position on the page and typography are stable across occurrences;
#   * the line is not more prominent than the body text: furniture is set at or
#     below body size, while numbered section titles are larger (checked when
#     native font sizes exist; OCR lines carry none).
# Only the exact signatures of the accepted lines are added to ``confirmed``,
# so suppression and block splitting work unchanged on per-line keys.
_COUNTER_NUMBER_RE = re.compile(r"(?<![\w.,%-])\d{1,4}(?![\w%-]|[.,]\d)")
_COUNTER_MIN_CONSECUTIVE_PAGES = 3
_COUNTER_MAX_SIZE_TO_BODY = 1.1


def _counter_form(key: str) -> tuple[tuple[str, str, tuple[str, ...], int], tuple[int, ...]] | None:
    """(group form, numbers) of a signature whose stand-alone numbers may be counters.

    Stand-alone numbers are digit runs not glued to letters, hyphens, decimal
    separators or a percent sign: "Folha 12", "· 012" and "03/84" qualify;
    "V11-R1", "TOKEN-035", "1.245,90" and "12%" do not. The form keeps the
    role, the letters and every other digit, so lines that differ only by
    punctuation (OCR reads "·" as "-" or ":") share a form.
    """
    role, _, text = key.partition(":")
    numbers = tuple(int(match) for match in _COUNTER_NUMBER_RE.findall(text))
    if not numbers:
        return None
    rest = _COUNTER_NUMBER_RE.sub(" ", text)
    letters = "".join(c for c in unicodedata.normalize("NFKD", rest).casefold() if c.isalpha())
    return (role, letters, tuple(re.findall(r"\d+", rest)), len(numbers)), numbers


def _counter_slots(numbers_by_page: dict[int, tuple[int, ...]]) -> bool:
    """True when every number slot is constant or a pagination counter, with at least one counter."""
    has_counter = False
    for slot in zip(*numbers_by_page.values()):
        if len(set(slot)) == 1:
            continue
        offsets = {value - page_index for page_index, value in zip(numbers_by_page, slot)}
        if len(offsets) != 1:
            return False
        has_counter = True
    return has_counter


def _has_consecutive_run(page_indices: list[int], length: int) -> bool:
    run = 1
    for previous, current in zip(page_indices, page_indices[1:]):
        run = run + 1 if current == previous + 1 else 1
        if run >= length:
            return True
    return length <= 1 and bool(page_indices)


def _confirm_page_counters(
    observations: dict[str, list[tuple[int, str, TextLine, StructuredPage]]],
    confirmed: dict[str, list[int]],
) -> None:
    """Confirm running furniture whose only variation is a page counter (T3-P43)."""
    groups: dict[tuple, list[tuple[str, int, str, TextLine, StructuredPage, tuple[int, ...]]]] = defaultdict(list)
    for key, values in sorted(observations.items()):
        parsed = _counter_form(key)
        if parsed is None:
            continue
        form, numbers = parsed
        for page_index, kind, line, page in values:
            if page_index in confirmed.get(key, ()):
                continue
            groups[form].append((key, page_index, kind, line, page, numbers))

    for members in groups.values():
        by_page: dict[int, list[tuple[str, int, str, TextLine, StructuredPage, tuple[int, ...]]]] = defaultdict(list)
        for member in members:
            by_page[member[1]].append(member)
        # Two same-form lines on one page leave the counter ambiguous: preserve the group.
        if any(len(items) > 1 for items in by_page.values()):
            continue
        # Evidence is required of the occurrences that share one vertical
        # position; an occurrence displaced from it (e.g. the inflated box of
        # a tilted scan line) is not confirmed and is preserved.
        members = _stable_position_core(members)
        page_indices = sorted(page_index for _, page_index, *_ in members)
        if not _has_consecutive_run(page_indices, _COUNTER_MIN_CONSECUTIVE_PAGES):
            continue
        if not _counter_slots({page_index: by_page[page_index][0][5] for page_index in page_indices}):
            continue
        selected = [(kind, line, page) for _, _, kind, line, page, _ in members]
        native_selected = [
            (k, l, p) for k, l, p in selected
            if l.line_id is not None and l.line_id.startswith("native:")
        ]
        check_selected = native_selected if len(native_selected) >= 2 else selected
        if not _stable_edge_position(check_selected) or not _stable_style(check_selected):
            continue
        if not _not_more_prominent_than_body(selected, observations):
            continue
        for key, page_index, *_ in members:
            confirmed[key] = sorted(set(confirmed.get(key, ())) | {page_index})


_POSITION_SPREAD = 0.035  # same tolerance as ``_stable_edge_position``


def _stable_position_core(members: list) -> list:
    """Members whose visual vertical centre lies within half the tolerance of the median."""
    centres = []
    for member in members:
        box, page_height = _visual_box(member[3], member[4])
        centres.append(box.cy / page_height)
    middle = median(centres)
    return [member for member, centre in zip(members, centres)
            if abs(centre - middle) <= _POSITION_SPREAD / 2]


def _token_sizes(lines) -> list[float]:
    return [token.font_size for line in lines for token in line.tokens
            if token.text.strip() and token.font_size and token.font_size > 0]


def _not_more_prominent_than_body(
    selected: list[tuple[str, TextLine, StructuredPage]],
    observations: dict[str, list[tuple[int, str, TextLine, StructuredPage]]],
) -> bool:
    """Furniture is not set larger than the body text of its pages.

    The body reference is the median native font size of the lines that are
    not edge candidates on the same pages. Without font sizes (OCR) the check
    gives no evidence either way and passes.
    """
    sizes = _token_sizes(line for _, line, _ in selected)
    if not sizes:
        return True
    candidate_ids = {id(line) for values in observations.values() for _, _, line, _ in values}
    pages = {id(page): page for _, _, page in selected}.values()
    body = _token_sizes(
        line for page in pages for region in page.regions for line in region.native_lines
        if id(line) not in candidate_ids
    )
    if not body:
        return True
    return median(sizes) <= _COUNTER_MAX_SIZE_TO_BODY * median(body)


# OCR furniture recurs with recognition noise ("GABARITO" read "CABAR O",
# "·" read "•") and a page counter that may be read apart from the label.
# Exact signatures then recur on one page each and nothing is confirmed.
_OCR_VARIANT_SIMILARITY = 0.85
_OCR_VARIANT_MINIMUM_LETTERS = 12
_PAGE_COUNTER_REMNANT_RE = re.compile(r"<page-number>(?:\s+\S{1,3}\s+\d{1,4})?")


def _ocr_variant_form(key: str) -> tuple[str, str, tuple[str, ...]]:
    """(role, letters, digits) of a signature without its page counter.

    Letters are compared with tolerance; any other number is semantic and
    must match exactly, so "repetição 1" never joins "repetição 2".
    """
    role, _, text = key.partition(":")
    text = _PAGE_COUNTER_REMNANT_RE.sub(" ", text)
    letters = "".join(c for c in unicodedata.normalize("NFKD", text).casefold() if c.isalpha())
    return role, letters, tuple(re.findall(r"\d+", text))


def _confirm_ocr_variants(
    observations: dict[str, list[tuple[int, str, TextLine, StructuredPage]]],
    confirmed: dict[str, list[int]],
) -> None:
    """Confirm OCR furniture whose signatures differ only by recognition noise.

    Only OCR observations take part: native text is exact evidence. Signatures
    with identical letters once the page counter is removed form one group;
    a group seen on two pages is an anchor. A one-page variant joins the most
    similar anchor of its role and numbers, so a page title (unique per page)
    never anchors anything. Each group must still pass the position and style
    checks. A bare page counter read apart from its label is furniture on the
    pages where a same-role line was confirmed.
    """
    from difflib import SequenceMatcher

    groups: dict[tuple[str, str, tuple[str, ...]], list[str]] = {}
    counters: list[str] = []
    for key, values in sorted(observations.items()):
        if any((line.line_id or "").startswith("native:") for _, _, line, _ in values):
            continue
        form = _ocr_variant_form(key)
        if not form[1]:
            counters.append(key)
        elif len(form[1]) >= _OCR_VARIANT_MINIMUM_LETTERS:
            groups.setdefault(form, []).append(key)

    def pages_of(keys):
        return {page for key in keys for page, *_ in observations[key]}

    anchors = {form: list(keys) for form, keys in groups.items() if len(pages_of(keys)) >= 2}
    for form, keys in groups.items():
        if form in anchors:
            continue
        scored = [(SequenceMatcher(None, form[1], anchor[1]).ratio(), anchor) for anchor in anchors
                  if anchor[0] == form[0] and anchor[2] == form[2]]
        best = max(scored, default=None)
        if best is not None and best[0] >= _OCR_VARIANT_SIMILARITY:
            anchors[best[1]].extend(keys)
    for members in anchors.values():
        by_page: dict[int, tuple[str, TextLine, StructuredPage]] = {}
        for key in members:
            for page_index, kind, line, page in observations[key]:
                by_page.setdefault(page_index, (kind, line, page))
        selected = list(by_page.values())
        if not _stable_edge_position(selected) or not _stable_style(selected):
            continue
        for key in members:
            confirmed[key] = sorted(set(confirmed.get(key, ())) | set(by_page))
    for key in counters:
        role = key.partition(":")[0]
        companion = {page for other, pages in confirmed.items()
                     if other.startswith(role + ":") and _ocr_variant_form(other)[1] for page in pages}
        pages = {page for page, *_ in observations[key]} & companion
        if pages:
            confirmed[key] = sorted(set(confirmed.get(key, ())) | pages)


def repeated_line_keys(page: StructuredPage) -> dict[str, str]:
    """Return furniture signature keys for candidate edge lines on one page.

    Keyed by the stable line identity (line_id or runtime object id) rather
    than by normalized text.  Text-keyed dicts collide when the same string
    appears at both the top and bottom of the same page (e.g. "CONFIDENTIAL"
    as both a header and a footer), causing one mapping to silently overwrite
    the other.  Identity-keyed dicts preserve both entries independently.
    """
    result: dict[str, str] = {}
    for kind, line in _candidate_lines(page):
        text = unicodedata.normalize("NFC", line.text.strip())
        key = _signature_key(kind, text)
        if key is not None:
            lid = line.line_id or f"line:{id(line)}"
            result[lid] = key
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
        # Scanned pages have no native line stream.  Their OCR line evidence
        # must participate in the same cross-page furniture check.
        lines = region.native_lines or region.ocr_lines
        all_lines.extend(lines)
        if region.kind == RegionKind.HEADER or region.edge_role == "top_candidate":
            role = "header"
        elif region.kind == RegionKind.FOOTER or region.edge_role == "bottom_candidate":
            role = "footer"
        else:
            continue
        for line in lines:
            explicit_ids.add(id(line))
            # A region label is not evidence for every line it contains: each
            # suppressible line must itself sit at the page edge, in the same
            # visual frame (/Rotate) used to confirm its repetition.
            if _at_edge(line, page, role):
                candidates.append((role, line))

    # Edge bands of the other regions use the same visual (/Rotate) test, so
    # an occurrence that is not at the visual edge is never a candidate: it
    # can neither veto a repeated signature nor be suppressed.
    for line in all_lines:
        if id(line) in explicit_ids:
            continue
        for role in ("header", "footer"):
            near, far = _edge_extent(line, page, role)
            # Band membership by the line center, as for the edge band itself.
            if (near + far) / 2 <= _EDGE_FRACTION and _at_edge(line, page, role):
                candidates.append((role, line))
                break

    return candidates


_SEMANTIC_CONTROL_RE = re.compile(
    # VQ-25: Lines that carry unique semantic identifiers must never be
    # treated as repeatable furniture, even if they appear in an edge band.
    # Patterns: protocol numbers, page-control stamps (V5-P###, PROC-####),
    # timestamps with unique values, and similar identifiers.
    r"controle\s+de\s+p[áa]gina"   # explicit page-control label
    r"|v\d+-p\d+"                   # corpus-style identifier (V5-P009)
    r"|\bproc(?:esso)?\s*[nº:]\s*\d"  # process number
    r"|\b\d{2}/\d{2}/\d{4}\b"      # date with year (unique per document)
    r"|\b\d{4}-\d{2}-\d{2}\b",     # ISO date
    re.IGNORECASE,
)


def _signature_key(kind: str, text: str) -> str | None:
    # VQ-25: Reject lines that carry semantic unique identifiers — they must
    # not be suppressed as repeating furniture even if their position is stable.
    if _SEMANTIC_CONTROL_RE.search(text):
        return None
    signature = _normalize_signature(text)
    if len(signature) < 3:
        return None
    return f"{kind}:{signature}"


_PAGE_NUMBER_PATTERNS = (
    # Explicit "Página 2 de 10" / "Page 2 of 10".
    r"\b(?:página|pagina|page)\s+\d+\s+(?:de|of)\s+\d+\b",
    # Explicit "Página 2/10" / "Page 2 / 10".
    r"\b(?:página|pagina|page)\s+\d+\s*/\s*\d+\b",
    # Explicit standalone "Página 2" / "Page 2".
    r"\b(?:página|pagina|page)\s+\d+\b",
)


def _normalize_signature(text: str) -> str:
    """Normalize only explicit dynamic pagination in repeated furniture.

    Arbitrary numbers are semantic content. Identifiers, account numbers,
    dates, invoice numbers and similar values must therefore remain part of
    the signature instead of being generalized merely because they occur in
    a stable edge position.

    Repeated page numbering is the intentionally narrow exception because its
    changing numeric component is explicitly identified by a page label.
    """
    value = text.casefold().strip()

    for pattern in _PAGE_NUMBER_PATTERNS:
        value = re.sub(
            pattern,
            "<page-number>",
            value,
        )
    # Unlabelled "018 / 095" pagination: a line that is only a page counter
    # over a fixed total. Other numbers stay part of the signature.
    value = re.sub(r"^\d{1,4}(\s*/\s*\d{1,4})$", r"<page-number>\1", value)

    value = re.sub(r"\s+", " ", value)
    return value.strip(" -|·")


_EDGE_FRACTION = 0.08
# Furniture lines are compact: the side facing the body must stay close to the
# edge as well. A line that starts at the edge but spans into the page (e.g. a
# horizontal line on a /Rotate 90 page, vertical in the visual frame) is body.
_EDGE_EXTENT_FRACTION = 0.12


def _visual_box(line: TextLine, page: StructuredPage):
    """Line box and page height in the upright (visual) frame of the page."""
    box = line.bbox
    if page.page_rotation % 360:
        box = box.rotate_to_visual(page.page_rotation, page.bbox.width, page.bbox.height)
        page_height = page.bbox.width if page.page_rotation % 180 else page.bbox.height
    else:
        page_height = page.bbox.height
    return box, max(page_height, 1.0)


def _edge_extent(line: TextLine, page: StructuredPage, role: str) -> tuple[float, float]:
    """(near, far) visual distances of a line from its role's edge, as page fractions."""
    box, page_height = _visual_box(line, page)
    if role == "header":
        near, far = box.y0, box.y1
    else:
        near, far = page_height - box.y1, page_height - box.y0
    return max(near, 0.0) / page_height, max(far, 0.0) / page_height


def _at_edge(line: TextLine, page: StructuredPage, role: str) -> bool:
    near, far = _edge_extent(line, page, role)
    return near <= _EDGE_FRACTION and far <= _EDGE_EXTENT_FRACTION


def _stable_edge_position(
    values: list[tuple[str, TextLine, StructuredPage]],
) -> bool:
    normalized = []
    for kind, line, page in values:
        if not _at_edge(line, page, kind):
            return False
        box, page_height = _visual_box(line, page)
        normalized.append(box.cy / page_height)
    if not normalized:
        return False
    return max(normalized) - min(normalized) <= 0.035


def _stable_style(
    values: list[tuple[str, TextLine, StructuredPage]],
) -> bool:
    """Return True when typography is consistent enough across occurrences to confirm furniture.

    Checks font size spread (≤ 35 %), unique font family count (≤ 1), and font
    weight spread (≤ 200). Lines with no typographic metadata pass by default.
    """
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
