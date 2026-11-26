"""Tesseract-specific post-processors for StructuredDocument.

Applied after ``assemble_document()`` in the extraction pipeline.  These
functions mutate ``PageContentBlock`` objects in-place and must be idempotent
(safe to call more than once).

Entry point: ``apply_tesseract_postprocessors(document)``
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from structured_pdf_text.document import StructuredDocument

# Numbered-section prefix patterns: "1. Title", "1.1 Title", "1.1.1 Title".
# Requires either a trailing dot ("1.") or at least one embedded dot ("1.1").
# The separator between the number and the text content must be whitespace
# (with an optional dot for single-number prefixes ending with ".").
_NUMBERED_PREFIX_RE = re.compile(r"^((?:\d+\.)+\d+|\d+\.)\s*\S")

# "Página N" page label at the start of a block ("Página 7", "Página 7 de 40",
# "Página 7/40", "Página 007 | Título"); group 1 is the page number. After the
# number comes nothing, a separator or "de M": "Página 7 do contrato contém…" is
# a sentence, not a label (AI-02). All 351 labels suppressed in RUN 015 (V3, V4)
# have the "|" form. The text may contain an optional leading markdown "# "
# that the OCR read literally.
_PAGINA_SEP_RE = re.compile(r"(?i)^\s*(?:#\s+)?p[aá]gina\s+(\d+)(?=\s*(?:$|[|/·•–—-]|de\s+\d))")
# A page separator is one short line ("Página 096 | OCR com rotação física de
# 90 graus" has 47 characters). A longer block that merely starts with it also
# carries body text; suppressing it deletes content (V3 p96 after the
# quarter-turn retry: the whole page body), so only the separator shape is
# suppressed.
_PAGINA_SEP_MAX_CHARS = 160
# A TITLE block may wrap the same label over two lines ("Página 001 | Corpus
# integrado de validação: texto nativo,\nOCR e tabelas", V3); it is still the
# label. Body text never forms a TITLE block.

# No rule here recognises the text of a particular document. The rules that
# suppressed the headers, footers and family markers of the V3/V4/V6 test
# corpora by their wording were removed (T3-P09): they deleted whatever
# matched, including a whole table for one cell and the title and body of
# V3 p116 (T3-P27). Page furniture is suppressed only by structural evidence
# (repetition across pages, the page's own number) and only when the
# extraction does not preserve headers and footers (T3-P54).

# Portuguese structural section terms: "Seção N" → H2, "Subseção N.M" → H3.
# These are common in Brazilian government/legal documents and are unlikely to
# appear as plain TEXT blocks in any document that is not a section heading.
_SEÇÃO_RE = re.compile(r"(?i)^se[çc][aã]o\s+\d+(?:\.\d+)*\b")
_SUBSEÇÃO_RE = re.compile(r"(?i)^subse[çc][aã]o\s+\d+(?:\.\d+)*\b")

# Matches any alphabetic run of ≥ 7 characters.
# Used by _table_has_q_without_u to find long-word candidates.
_LONG_ALPHA_RE = re.compile(r"[A-Za-z]{7,}")


def _prefix_depth(num_str: str) -> int:
    """Return the depth of a numbered prefix (e.g. '1.1.1' → 3)."""
    return len([p for p in num_str.split(".") if p])


# Suppressions of recognised page furniture: running headers and the page's
# own "Página N" label. They are the only suppressions made here, and only
# when headers and footers are not preserved. Any other suppression would
# remove content after the assembly ledger has accounted for every line, so
# it is reported in the page diagnostics (T3-P36).
_FURNITURE_SUPPRESSION_REASONS = frozenset({
    "tesseract_running_header",
    "tesseract_pagina_separator",
})


def _furniture_suppression_allowed(document: "StructuredDocument") -> bool:
    """True only when the extraction asked not to preserve headers and footers.

    The assembly records the choice in ``diagnostics.facts`` and the Markdown
    renderer reads it from there; without the fact, headers and footers are
    preserved, as in the configuration default (T3-P54).
    """
    facts = getattr(getattr(document, "diagnostics", None), "facts", None) or {}
    return not facts.get("preserve_headers_footers", True)


def apply_tesseract_postprocessors(document: "StructuredDocument") -> None:
    """Run all Tesseract post-processors on *document* in order.

    Only the furniture suppressors remove blocks, and they do nothing when
    headers and footers are preserved. The other steps never remove text.
    """
    visible_before = {
        id(block) for page in document.pages for block in page.content_blocks if not block.suppressed
    }
    suppress_tesseract_running_headers(document)
    _suppress_pagina_separator_blocks(document)
    _flag_rotated_table_garble(document)
    _normalize_brl_currency_spacing(document)
    _promote_numbered_bold_headings(document)
    _promote_allcaps_section_headers(document)
    _promote_short_bold_column_headers(document)
    _promote_bold_pre_table_heading(document)   # Fix-U — NEW
    _promote_bold_pipe_title(document)
    _promote_seção_subseção_headings(document)
    # No check box state is inferred here (T3-P51): a state needs evidence of the
    # box itself. Explicit ☐/☑/☒ glyphs in the text are kept by the shared list
    # detection; short lines after a table or a leading "O"/"U" are text.
    _report_postprocess_suppressions(document, visible_before)


def _report_postprocess_suppressions(document: "StructuredDocument", visible_before: set[int]) -> None:
    """Record the content these post-processors removed (T3-P36).

    They run after assembly, whose ledger has already accounted for every
    line; a block suppressed here would otherwise vanish while the page is
    reported valid and clean (V4 p167/p168 lost a whole table that way).
    Every suppression is counted in the page facts; any suppression that is
    not recognised page furniture also becomes a page warning.
    """
    for page in document.pages:
        removed: dict[str, int] = {}
        for block in page.content_blocks:
            if id(block) in visible_before and block.suppressed:
                reason = block.suppression_reason or "unspecified"
                removed[reason] = removed.get(reason, 0) + len((block.text or "").strip())
        diagnostics = getattr(page, "diagnostics", None)
        if not removed or diagnostics is None:
            continue
        diagnostics.facts["tesseract_postprocess_suppressed_chars"] = removed
        for reason, chars in sorted(removed.items()):
            if reason not in _FURNITURE_SUPPRESSION_REASONS:
                _add_page_warning(document, page, f"content_suppressed_by_postprocess: {reason} ({chars} chars)")


def _add_page_warning(document: "StructuredDocument", page, message: str) -> None:
    """Append a warning to the page and, prefixed, to the document."""
    page.diagnostics.warnings.append(message)
    document_diagnostics = getattr(document, "diagnostics", None)
    if document_diagnostics is not None:
        document_diagnostics.warnings.append(f"page {page.page_index + 1}: {message}")


def suppress_tesseract_running_headers(
    document: "StructuredDocument",
    *,
    min_frequency: float = 0.40,
) -> None:
    """Frequency-based running-header suppressor for Tesseract output.

    Takes the first visible text block of each page and counts how often each
    normalised block text appears across all pages. A block text appearing on
    ≥ ``min_frequency`` fraction of pages is treated as a running header and
    marked suppressed on every page where it is the first content block.

    The whole block must repeat: a block whose first line is the header but
    that also carries other lines (a header merged with the body) is kept, as
    in the shared detector (``assemble.document._apply_repeated_suppression``).
    A repeated first line used to take the paragraph merged with it (T3-P27).

    This compensates for the geometry-based detector (``_stable_edge_position``)
    which rejects signatures when even one page places the header slightly
    outside the 8 % edge band.  Frequency detection is robust to small layout
    variations without changing shared pipeline modules. Nothing is suppressed
    when headers and footers are preserved (T3-P54).

    Mutates ``block.suppressed = True`` and sets
    ``block.suppression_reason = "tesseract_running_header"`` on matched blocks.
    """
    from collections import Counter

    pages = document.pages
    if len(pages) < 5 or not _furniture_suppression_allowed(document):
        return

    try:
        from structured_pdf_text.document import ContentKind
        _skip_kinds = {ContentKind.TABLE, ContentKind.FIGURE}
    except Exception:
        _skip_kinds = set()

    first_blocks: list[str | None] = []
    for page in pages:
        blocks = sorted(page.content_blocks, key=lambda b: b.order_index)
        found = None
        for block in blocks:
            if block.suppressed:
                continue
            if getattr(block, "kind", None) in _skip_kinds:
                continue
            text = " ".join((block.text or "").split())
            if not text:
                continue
            if len(text) >= 3:
                found = text.casefold()
            break
        first_blocks.append(found)

    min_pages = max(2, int(min_frequency * len(pages)))
    freq = Counter(t for t in first_blocks if t is not None)
    running_headers = {text for text, count in freq.items() if count >= min_pages}
    if not running_headers:
        return

    suppressed_count = 0
    for page in pages:
        blocks = sorted(page.content_blocks, key=lambda b: b.order_index)
        for block in blocks:
            if block.suppressed:
                continue
            if getattr(block, "kind", None) in _skip_kinds:
                continue
            text = " ".join((block.text or "").split())
            if not text:
                continue
            if text.casefold() in running_headers:
                block.suppressed = True
                block.suppression_reason = "tesseract_running_header"
                suppressed_count += 1
            break

    if suppressed_count:
        print(
            f"[tesseract_post] suppressed running header on {suppressed_count}/{len(pages)} pages "
            f"(signatures: {sorted(running_headers)[:3]})",
            flush=True,
        )


def _suppress_pagina_separator_blocks(document: "StructuredDocument") -> None:
    """Suppress the page's own 'Página N' label ("Página 7 de 40", "Página 007 | Título").

    A running page label carries the page number, so its text differs on
    every page and the frequency-based suppressor
    (``suppress_tesseract_running_headers``) cannot see it repeat. The
    evidence used instead is the page counter itself: the label is suppressed
    only on page N. A "Página 12 …" line on another page (a cross-reference,
    a table of contents, a title) is content and is kept.

    Targets both TITLE blocks (layout engine classified them) and TEXT blocks
    whose raw text starts with "# Página N" (OCR read a literal ``#`` glyph).
    Only label-shaped blocks are suppressed: at most ``_PAGINA_SEP_MAX_CHARS``
    characters and either a TITLE block (a long title may wrap over two lines)
    or a single line. A TEXT block that continues with body text is kept
    (T3-P25, T3-P26). Nothing is suppressed when headers and footers are
    preserved (T3-P54).

    Known limit (T3-P52): the text after the counter ("| Título") goes with
    it. The paginated Markdown uses "## Página N" as its page delimiter and
    the evaluator also takes "## Página N | …" as one, so keeping such a title
    would make the output ambiguous until the renderer and the evaluator can
    tell a delimiter from a title.
    Idempotent: already-suppressed blocks are skipped.
    """
    if not _furniture_suppression_allowed(document):
        return
    suppressed = 0
    for page in document.pages:
        page_index = getattr(page, "page_index", None)
        if page_index is None:
            continue
        for block in page.content_blocks:
            if block.suppressed:
                continue
            text = (block.text or "").strip()
            if not text:
                continue
            label = _PAGINA_SEP_RE.match(text)
            if (label and int(label.group(1)) == page_index + 1 and len(text) <= _PAGINA_SEP_MAX_CHARS
                    and (getattr(block, "kind", None) == "title" or "\n" not in text)):
                block.suppressed = True
                block.suppression_reason = "tesseract_pagina_separator"
                suppressed += 1

    if suppressed:
        print(
            f"[tesseract_post] suppressed {suppressed} 'Página NNN' separator block(s)",
            flush=True,
        )


def _normalize_brl_currency_spacing(document: "StructuredDocument") -> None:
    """Normalize Brazilian Real currency spacing in Tesseract OCR output.

    Tesseract frequently drops the mandatory space between the currency symbol
    and the amount, yielding "R$9.876,54" instead of the standard "R$ 9.876,54".
    This creates fingerprint mismatches with references that use the correct
    notation, contributing spurious excess blocks to ``duplicate_excess_rate``.

    The fix replaces every "R$<digit>" pattern with "R$ <digit>" in-place.
    Only unsuppressed blocks are modified.  Idempotent: already-correct "R$ "
    sequences are not matched because the regex requires a digit immediately
    after "$".
    """
    _BRL_RE = re.compile(r"R\$(\d)")
    modified = 0
    for page in document.pages:
        for block in page.content_blocks:
            if block.suppressed:
                continue
            text = block.text or ""
            if "R$" not in text:
                continue
            new_text = _BRL_RE.sub(r"R$ \1", text)
            if new_text != text:
                block.text = new_text
                modified += 1
    if modified:
        print(
            f"[tesseract_post] normalized R$ currency spacing in {modified} block(s)",
            flush=True,
        )


def _next_block_is_unordered_list(page, block) -> bool:
    """Return True if the next non-suppressed block on the page is an unordered LIST."""
    try:
        from structured_pdf_text.document import ContentKind
    except Exception:
        return False
    blocks_sorted = sorted(page.content_blocks, key=lambda x: getattr(x, "order_index", 0))
    found = False
    for b in blocks_sorted:
        if getattr(b, "suppressed", False):
            continue
        if b is block:
            found = True
            continue
        if not found:
            continue
        if b.kind != ContentKind.LIST:
            return False
        items = getattr(b, "list_items", None) or []
        if items:
            first_marker = (items[0].marker or "").strip()
            if re.match(r"^\d+[.)]", first_marker):
                return False  # Ordered list
        return True
    return False


def _promote_numbered_bold_headings(document: "StructuredDocument") -> None:
    """Promote TEXT blocks with numbered section prefixes to TITLE.

    Primary path (bold): TEXT blocks with dominant bold styling and a numbered
    prefix such as "1.1.1 Title text" are promoted.  The heading level is
    inferred from the prefix depth calibrated against existing TITLE blocks.

    Structural path (non-bold): Very short TEXT blocks (≤ 45 chars) whose
    numbered prefix has exactly one level ("1. Title") and whose immediately
    following non-suppressed block is an unordered LIST are also promoted.
    This covers section headers that are not visually bold in native PDFs
    but serve syntactically as numbered section headings (Fix-F11).

    Calibration example for a document whose numbered sections start at H2:
        existing TITLEs: "1. Section" → H2, "1.1 Sub" → H3
        level_map: {1: 2, 2: 3}
        "1.1.1 Deep sub" (TEXT, bold, depth=3) → level = 3 + (3-2) = 4 → H4

    Invariants:
    - Only TEXT blocks are considered (TITLE blocks already processed).
    - Bold path: block must have dominant bold styling.
    - Structural path: single-level prefix only, ≤ 45 chars, next block is
      unordered LIST.
    - Block text must not span multiple lines.
    - Numbered prefix must contain at least one embedded dot OR a trailing dot
      to distinguish from lone integers (catalog numbers, list counters).
    - Idempotent: already-promoted blocks have kind=TITLE and are skipped.
    """
    try:
        from structured_pdf_text.document import ContentKind
    except Exception:
        return

    # Build depth→level calibration map from existing correctly-classified TITLE blocks.
    level_map: dict[int, int] = {}
    for page in document.pages:
        for block in page.content_blocks:
            if block.suppressed or block.kind != ContentKind.TITLE or block.heading_level is None:
                continue
            m = _NUMBERED_PREFIX_RE.match((block.text or "").strip())
            if not m:
                continue
            depth = _prefix_depth(m.group(1))
            if depth not in level_map:
                level_map[depth] = block.heading_level

    promoted = 0
    for page in document.pages:
        for block in page.content_blocks:
            if block.suppressed or block.kind != ContentKind.TEXT:
                continue
            is_bold = _block_is_bold(block)
            text = (block.text or "").strip()
            m = _NUMBERED_PREFIX_RE.match(text)
            if not m:
                continue
            if "\n" in text:
                continue

            if is_bold:
                if len(text) > 90:
                    continue
            else:
                # Structural path: short single-level numbered block before unordered list
                if len(text) > 45:
                    continue
                depth_check = _prefix_depth(m.group(1))
                if depth_check != 1:
                    continue  # Only single-level (1.) for structural path
                if not _next_block_is_unordered_list(page, block):
                    continue

            depth = _prefix_depth(m.group(1))
            if depth > 6:
                continue

            # Infer level from calibration map; extrapolate linearly for unseen depths.
            if level_map:
                nearest = min(level_map.keys(), key=lambda d: abs(d - depth))
                level = level_map[nearest] + (depth - nearest)
            else:
                level = depth

            level = max(1, min(6, level))
            block.kind = ContentKind.TITLE
            block.heading_level = level
            promoted += 1

    if promoted:
        print(
            f"[tesseract_post] promoted {promoted} numbered bold heading(s) to TITLE",
            flush=True,
        )


# Pattern matching blocks that are entirely upper-case and suitable for
# heading promotion on OCR pages.  Allows accented Portuguese capitals,
# spaces, digits, hyphens, slashes, degree/ordinal signs (°, Nº, º) and
# the special character Ç/Ã/Õ that appear in section labels.  Excludes
# colons (metadata separators like "Data:") and trailing periods.
_ALLCAPS_SECTION_RE = re.compile(
    r"^[A-ZÁÀÂÃÇÉÊÍÓÔÕÚÛÜQW\s\d\-/°Nº]+$"
)


def _block_is_bold(block) -> bool:
    """Return True if the block has dominant bold styling."""
    if getattr(block, "inline_style", None) in ("bold", "bold_italic"):
        return True
    styled_runs = getattr(block, "styled_runs", None)
    if styled_runs:
        bold_chars = sum(len(t) for t, s in styled_runs if "b" in s)
        total_chars = sum(len(t) for t, s in styled_runs)
        return total_chars > 0 and bold_chars / total_chars >= 0.60
    return False


def _promote_allcaps_section_headers(document: "StructuredDocument") -> None:
    """Promote all-caps single-line OCR text blocks to TITLE (heading).

    On fully-scanned pages Tesseract lacks font-size signals, so section
    labels written in uppercase (e.g. "QUADRO DE MEDIÇÕES") are classified
    as TEXT blocks instead of TITLE blocks.  Two structural rules promote
    them without using corpus-specific text patterns:

    Rule A — pre-table section label → H3:
        A TEXT block whose single line is entirely upper-case (10-70 chars,
        no colon, no trailing period) that is immediately followed by a
        TABLE block is promoted to H3.  The adjacent-table test limits false
        positives from decorative stamps like "CONFERIDO" or "ANÁLISE
        SETORIAL" that appear after body text rather than before tables.

    Rule B — first content block → H1:
        The first non-suppressed TEXT block on a page, if entirely upper-case
        (10-70 chars, no colon, no trailing period), is promoted to H1 when
        no TITLE block already exists on the same page.  This handles
        full-page scan headings like "BOLETIM DE INSPEÇÃO Nº 040/2026".

    Neither rule fires on blocks that already carry bold inline style
    (native PDF font metadata), since those are handled by heading.py.
    """
    try:
        from structured_pdf_text.document import ContentKind
    except Exception:
        return

    promoted_a = 0
    promoted_b = 0

    for page in document.pages:
        blocks = sorted(page.content_blocks, key=lambda b: b.order_index)
        active = [b for b in blocks if not b.suppressed]

        # Rule B: detect whether a TITLE block already exists on this page
        page_has_title = any(b.kind == ContentKind.TITLE for b in active)

        first_text_seen = False
        for idx, block in enumerate(active):
            # Rule A applies to TEXT and CAPTION; Rule B applies to TEXT only.
            if block.kind not in (ContentKind.TEXT, ContentKind.CAPTION):
                continue
            if block.heading_level is not None:
                if block.kind == ContentKind.TEXT:
                    first_text_seen = True
                continue

            text = (block.text or "").strip()
            if not text or "\n" in text:
                if block.kind == ContentKind.TEXT:
                    first_text_seen = True
                continue
            if ":" in text or text.endswith("."):
                if block.kind == ContentKind.TEXT:
                    first_text_seen = True
                continue
            if not (10 <= len(text) <= 70):
                if block.kind == ContentKind.TEXT:
                    first_text_seen = True
                continue
            if not _ALLCAPS_SECTION_RE.match(text):
                if block.kind == ContentKind.TEXT:
                    first_text_seen = True
                continue
            # Skip if block already carries native bold (heading.py handles those)
            if getattr(block, "inline_style", None) in ("bold", "bold_italic"):
                if block.kind == ContentKind.TEXT:
                    first_text_seen = True
                continue

            # Rule A: immediately followed by a TABLE (applies to TEXT and CAPTION)
            next_block = active[idx + 1] if idx + 1 < len(active) else None
            if next_block is not None and next_block.kind == ContentKind.TABLE:
                was_text = block.kind == ContentKind.TEXT
                block.kind = ContentKind.TITLE
                block.heading_level = 3
                promoted_a += 1
                if was_text:
                    first_text_seen = True
                continue

            # Rule B: first text block on page with no existing titles (TEXT only)
            if block.kind == ContentKind.TEXT:
                if not first_text_seen and not page_has_title:
                    block.kind = ContentKind.TITLE
                    block.heading_level = 1
                    promoted_b += 1
                    page_has_title = True
                first_text_seen = True

    if promoted_a or promoted_b:
        print(
            f"[tesseract_post] promoted all-caps section headers: "
            f"{promoted_a} pre-table H3, {promoted_b} first-block H1",
            flush=True,
        )


def _promote_short_bold_column_headers(document: "StructuredDocument") -> None:
    """Promote short bold TEXT blocks to H3 in multi-column layout pages.

    In two-column and three-column layouts, section headers for each column
    (e.g. "Engenharia", "Suprimentos", "Financeiro") appear as bold TEXT blocks
    because Tesseract lacks font-size contrast signals in scan pages.

    Structural rule (no corpus-specific patterns):
    - Collect qualifying bold TEXT blocks per page: single-line, 3-50 chars,
      not ALL-CAPS (handled by allcaps rule), no colon, no trailing period, no
      numbered prefix (handled by numbered rule), not already a heading, page
      already has at least one TITLE block.
    - If the page has ≥ 2 such blocks, it is treated as a multi-column page
      and ALL qualifying blocks are promoted to H3.

    The ≥ 2 threshold prevents false positives on pages with a single bold
    element (e.g. person signatures or one-off labels).
    """
    try:
        from structured_pdf_text.document import ContentKind
    except Exception:
        return

    promoted = 0
    for page in document.pages:
        blocks = sorted(page.content_blocks, key=lambda b: b.order_index)
        active = [b for b in blocks if not b.suppressed]

        page_has_title = any(b.kind == ContentKind.TITLE for b in active)
        if not page_has_title:
            continue

        candidates = []
        for block in active:
            if block.kind != ContentKind.TEXT:
                continue
            if block.heading_level is not None:
                continue
            if not _block_is_bold(block):
                continue
            text = (block.text or "").strip()
            if not text or "\n" in text:
                continue
            if not (3 <= len(text) <= 50):
                continue
            if ":" in text or text.endswith("."):
                continue
            if _ALLCAPS_SECTION_RE.match(text):
                continue
            if _NUMBERED_PREFIX_RE.match(text):
                continue
            candidates.append(block)

        if len(candidates) >= 2:
            for block in candidates:
                block.kind = ContentKind.TITLE
                block.heading_level = 3
                promoted += 1

    if promoted:
        print(
            f"[tesseract_post] promoted {promoted} short bold column header(s) to H3",
            flush=True,
        )


def _text_is_md_bold(text: str) -> bool:
    """Return True if *text* is entirely wrapped in markdown bold markers ``**...**``."""
    return (
        len(text) > 4
        and text.startswith("**")
        and text.endswith("**")
        and not text[2:-2].startswith("*")
    )


def _promote_bold_pre_table_heading(document: "StructuredDocument") -> None:
    """Promote a sole bold TEXT or CAPTION block on the page to the appropriate heading level.

    A section heading placed above a table (e.g. "Quadro de recebimento com
    chancela no interior da célula") may appear as a bold CAPTION block when
    Tesseract identifies it as a figure/table caption, or as a bold TEXT block
    whose raw text is already wrapped in ``**...**`` markers.  On hybrid pages
    where table rows appear as TEXT/LIST/FIGURE blocks (no TABLE block), the
    old TABLE-position guard never fired; this function now scans all active
    blocks regardless of TABLE presence.

    Structural rule (no corpus-specific patterns):
    - Page has ≥1 TITLE block (at least one heading already detected).
    - A bold TEXT or CAPTION block exists anywhere on the page: single line,
      no colon, no trailing period, not ALL-CAPS, no numbered prefix, no pipe
      separator (Fix-L handles pipe headings → H1), no heading level already
      assigned.  Bold is detected via ``_block_is_bold()`` (inline_style/
      styled_runs) OR via ``**...**`` wrapping in the raw text.
    - The content length (stripped of ``**`` markers if present) is 40–90 chars.
    - Exactly one such qualifying bold block exists on the page.
    - Promote to ``max(scan_range heading levels) + 1`` so the promoted heading
      sits one level below the deepest heading in the *same scan region*.
      Using scan_range (before the first TABLE, or all blocks when no TABLE
      exists) prevents H2/H3 titles that follow the table on hybrid_cell_stamp
      pages from inflating the max and incorrectly pushing the candidate to H4.
      If no TITLE in scan_range has a known level, default to H2.

    The 40-char minimum prevents short floating column-header labels (handled by
    Fix-K) from being promoted.  The "exactly one" constraint avoids confusion
    with multi-column pages (also handled by Fix-K).
    """
    try:
        from structured_pdf_text.document import ContentKind
    except Exception:
        return

    promoted = 0
    for page in document.pages:
        blocks = sorted(page.content_blocks, key=lambda b: b.order_index)
        active = [b for b in blocks if not b.suppressed]

        # Page must have at least one TITLE (heading already detected)
        if not any(b.kind == ContentKind.TITLE for b in active):
            continue

        # Find index of first TABLE block (if any).
        # When TABLE blocks exist scan only before the first TABLE.
        # When none exist (e.g. V6 hybrid pages where table rows are TEXT/LIST/FIGURE
        # blocks), scan all active blocks — that is the V6 fix.
        first_table_pos = None
        for i, b in enumerate(active):
            if b.kind == ContentKind.TABLE:
                first_table_pos = i
                break
        scan_range = active[:first_table_pos] if first_table_pos is not None else active

        # Collect qualifying bold TEXT or CAPTION blocks
        candidates = []
        for block in scan_range:
            if block.kind not in (ContentKind.TEXT, ContentKind.CAPTION):
                continue
            if block.heading_level is not None:
                continue
            raw_text = (block.text or "").strip()
            if not raw_text or "\n" in raw_text:
                continue
            is_style_bold = _block_is_bold(block)
            is_md_bold = _text_is_md_bold(raw_text)
            if not (is_style_bold or is_md_bold):
                continue
            # Strip ** markers to get the actual content for length/content checks
            content = raw_text[2:-2].strip() if is_md_bold and not is_style_bold else raw_text
            if not (40 <= len(content) <= 90):
                continue
            if ":" in content or content.endswith(".") or "|" in content:
                continue
            if _ALLCAPS_SECTION_RE.match(content):
                continue
            if _NUMBERED_PREFIX_RE.match(content):
                continue
            candidates.append((block, content, is_md_bold and not is_style_bold))

        if len(candidates) == 1:
            block, content, strip_markers = candidates[0]
            # Determine target level: one below the deepest heading in the *same
            # scan region* as the candidate.  Using scan_range (not all active
            # blocks) avoids counting H2/H3 titles that appear after the table on
            # hybrid_cell_stamp pages, which would incorrectly push the promoted
            # block to H4 instead of H2.  On redacted pages (no TABLE → scan_range
            # == all active), the layout-detected H2 "Extrato de dados" is visible
            # and the candidate correctly becomes H3.
            existing_levels = [
                b.heading_level
                for b in scan_range
                if b.kind == ContentKind.TITLE and b.heading_level is not None
            ]
            target_level = (max(existing_levels) + 1) if existing_levels else 2
            block.kind = ContentKind.TITLE
            block.heading_level = target_level
            if strip_markers:
                block.text = content
            promoted += 1

    if promoted:
        print(
            f"[tesseract_post] promoted {promoted} bold pre-table block(s) (dynamic level)",
            flush=True,
        )


def _promote_bold_pipe_title(document: "StructuredDocument") -> None:
    """Promote bold TEXT/CAPTION blocks with pipe-separator to H1 when no H1 exists.

    On scan pages with watermark/stamp overlays the compound page title
    (e.g. "Nota de análise com chancela | Bacia Norte") appears as a bold
    TEXT or CAPTION block at the top of the page.  Tesseract's BALANCED
    config classifies these as CAPTION rather than TEXT (similar to the
    issue fixed by Fix-J for allcaps blocks).

    Rule: for each page that has no H1 heading, look for the first bold TEXT
    or CAPTION block (among the first 3 active blocks) that:
    - Contains \" | \" (pipe separator with spaces)
    - Is a single line, 10-80 chars
    - Does not end with \".\" or \":\"

    One H1 promoted per page at most.
    """
    try:
        from structured_pdf_text.document import ContentKind
    except Exception:
        return

    promoted = 0
    for page in document.pages:
        blocks = sorted(page.content_blocks, key=lambda b: b.order_index)
        active = [b for b in blocks if not b.suppressed]

        has_h1 = any(
            b.kind == ContentKind.TITLE and getattr(b, "heading_level", None) == 1
            for b in active
        )
        if has_h1:
            continue

        for pos, block in enumerate(active):
            if pos > 2:
                break
            if block.kind not in (ContentKind.TEXT, ContentKind.CAPTION):
                continue
            if block.heading_level is not None:
                continue
            if not _block_is_bold(block):
                continue
            text = (block.text or "").strip()
            if not text or "\n" in text:
                continue
            if " | " not in text:
                continue
            if not (10 <= len(text) <= 80):
                continue
            if text.endswith(".") or text.endswith(":"):
                continue
            block.kind = ContentKind.TITLE
            block.heading_level = 1
            promoted += 1
            break

    if promoted:
        print(
            f"[tesseract_post] promoted {promoted} bold pipe-separator block(s) to H1",
            flush=True,
        )


def _table_has_q_without_u(text: str) -> bool:
    """Return True if *text* contains a ≥7-char alphabetic token with Q not followed by U.

    Q not followed by U is phonotactically impossible in Portuguese, Spanish,
    French, Italian, and all Latin-script European languages.  The minimum
    token length of 7 filters out short technical abbreviations (SQL, QR, etc.)
    that may legitimately appear in table cells.

    When Tesseract reads a table image that is rotated 90° or 270° in the PDF,
    it interprets columns as rows and produces garbled tokens such as
    ``ORIQLVHOIVT`` (11 chars, contains ``QL``).  No real table cell in any
    Portuguese-corpus document contains such a sequence.
    """
    for m in _LONG_ALPHA_RE.finditer(text):
        word = m.group()
        for i, c in enumerate(word):
            if c in "qQ" and (i + 1 >= len(word) or word[i + 1] not in "uU"):
                return True
    return False


def _flag_rotated_table_garble(document: "StructuredDocument") -> None:
    """Report TABLE blocks containing letter sequences impossible in Latin script.

    A table image rotated 90° or 270° and read sideways produces garbled cell
    text, often with a long alphabetic token containing Q not followed by U
    (``ORIQLVHOIVT``). This rule used to suppress the whole block on a single
    such token. One misread word in an otherwise legible table triggers it
    too: V4 p167/p168 lost a complete multilingual table, reported valid and
    clean, because the Greek row reads "aByAQnA" (T3-P36). Rotated tables
    are oriented before recognition (quarter-turn retry, T3-P02/T3-P32), so
    the block is kept and the suspicion becomes a page warning: recoverable
    content is never hidden by this heuristic.
    """
    try:
        from structured_pdf_text.document import ContentKind
    except Exception:
        return

    flagged_total = 0
    for page in document.pages:
        flagged = 0
        for block in page.content_blocks:
            if block.suppressed:
                continue
            if getattr(block, "kind", None) != ContentKind.TABLE:
                continue
            if _table_has_q_without_u(block.text or ""):
                flagged += 1
        if flagged and getattr(page, "diagnostics", None) is not None:
            _add_page_warning(
                document, page,
                f"suspected_ocr_garble: {flagged} table block(s) kept with letter sequences "
                "impossible in Latin script",
            )
        flagged_total += flagged

    if flagged_total:
        print(
            f"[tesseract_post] flagged {flagged_total} table block(s) with suspected garble (kept)",
            flush=True,
        )


def _promote_seção_subseção_headings(document: "StructuredDocument") -> None:
    """Promote TEXT blocks starting with 'Seção N' or 'Subseção N' to headings.

    In Brazilian government and legal documents section headings are frequently
    written as 'Seção N - Description' (H2) or 'Subseção N.M - Description'
    (H3).  When the native heading engine misses these — typically because the
    block's font metrics are indistinguishable from body text — they appear as
    plain TEXT blocks.

    Rule:
    - 'Seção N[.M…]' → ContentKind.TITLE, heading_level=2
    - 'Subseção N[.M…]' → ContentKind.TITLE, heading_level=3

    Guards: single-line block, 4-120 chars, not already a TITLE, not suppressed.
    Only matches TEXT blocks — TITLE and other kinds are left untouched.
    """
    try:
        from structured_pdf_text.document import ContentKind
    except Exception:
        return

    promoted = 0
    for page in document.pages:
        for block in page.content_blocks:
            if block.suppressed:
                continue
            if block.kind != ContentKind.TEXT:
                continue
            if block.heading_level is not None:
                continue
            text = (block.text or "").strip()
            if not text or "\n" in text:
                continue
            if not (4 <= len(text) <= 120):
                continue
            if _SEÇÃO_RE.match(text):
                block.kind = ContentKind.TITLE
                block.heading_level = 2
                promoted += 1
            elif _SUBSEÇÃO_RE.match(text):
                block.kind = ContentKind.TITLE
                block.heading_level = 3
                promoted += 1

    if promoted:
        print(
            f"[tesseract_post] promoted {promoted} Seção/Subseção block(s) to heading",
            flush=True,
        )
