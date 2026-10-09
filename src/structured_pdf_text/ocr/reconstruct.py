"""Reconstruct structured text lines from flat OCR token lists."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import replace
from statistics import median

from structured_pdf_text.document import (
    Baseline,
    EvidenceRef,
    OcrToken,
    SourceKind,
    TextLine,
    TextToken,
    TokenFlag,
    WritingDirection,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.text.normalize import normalize_text

# Characters that must not be preceded by a space in Portuguese text.
# Includes standard punctuation, closing brackets, and the percent sign.
_NO_SPACE_BEFORE: frozenset[str] = frozenset(",.:;!?%)]}")
# Characters that must not be followed by a space.
_NO_SPACE_AFTER: frozenset[str] = frozenset("([{")
# Currency prefix that must stay attached to the following digits (R$ 1.234 → R$1.234 would
# be wrong; only the *internal* gap between "R$" and the digits is closed when geometrically
# they appear separated by OCR box boundaries).
_CURRENCY_PREFIX_RE = re.compile(r"^R\$$", re.IGNORECASE)


def reconstruct_ocr_lines(
    tokens: list[OcrToken],
    page_index: int,
    page_bbox: BBox | None = None,
    *,
    dehyphenate: bool = True,
    page_rotation: int = 0,
) -> list[TextLine]:
    """Convert OCR tokens into lines using the OCR reading coordinate system.

    Rotated-page OCR tokens remain in page coordinates for evidence and table
    assignment, but are grouped and ordered in the temporary upright system
    used by the OCR pass.

    Args:
        tokens: Flat list of OCR tokens from the backend.
        page_index: Zero-based page number used for evidence references.
        page_bbox: Page bounding box in document coordinates; when ``None``
            the bounding box is inferred from the token extents.
        dehyphenate: When ``True`` (default), apply §30 conservative
            dehyphenation after line grouping.  OCR often splits hyphenated
            words across lines (e.g. ``docu-`` / ``mento``); this step
            joins them back.  Pass ``False`` to skip (e.g. in quality
            scoring where raw line count matters).
    """
    visible = [token for token in tokens if token.text and token.bbox.width >= 0 and token.bbox.height >= 0]
    if not visible:
        return []
    page_width, page_height = _page_size(visible, page_bbox)
    virtual_boxes = {id(token): _virtual_bbox(token, page_bbox, page_width, page_height, page_rotation) for token in visible}
    heights = [box.height for box in virtual_boxes.values() if box.height > 0]
    tolerance = max(2.0, (median(heights) if heights else 10.0) * 0.65)
    groups: list[list[OcrToken]] = []
    centers: list[float] = []
    for token in sorted(visible, key=lambda item: (virtual_boxes[id(item)].cy, virtual_boxes[id(item)].x0)):
        virtual_box = virtual_boxes[id(token)]
        best_index = min(
            range(len(centers)),
            key=lambda index: abs(centers[index] - virtual_box.cy),
            default=None,
        )
        if best_index is None or abs(centers[best_index] - virtual_box.cy) > tolerance:
            groups.append([token])
            centers.append(virtual_box.cy)
        else:
            groups[best_index].append(token)
            centers[best_index] = median(virtual_boxes[id(item)].cy for item in groups[best_index])

    lines: list[TextLine] = []
    for line_index, group in enumerate(groups):
        ordered = sorted(group, key=lambda item: virtual_boxes[id(item)].x0)
        text_tokens: list[TextToken] = []
        previous = None
        for token_index, token in enumerate(ordered):
            current_virtual = virtual_boxes[id(token)]
            previous_virtual = virtual_boxes[id(previous)] if previous is not None else None
            if previous is not None and _should_insert_space(
                previous,
                token,
                previous_virtual,
                current_virtual,
            ):
                gap_bbox = _gap_bbox(
                    previous_virtual,
                    current_virtual,
                    token.rotation,
                    page_rotation,
                    page_bbox,
                    page_width,
                    page_height,
                )
                text_tokens.append(
                    TextToken(
                        text=" ",
                        bbox=gap_bbox,
                        sources=[EvidenceRef(SourceKind.OCR_PAGE, page_index, f"ocr-gap:{line_index}:{token_index}")],
                        confidence=0.60,
                        normalized_text=" ",
                        flags={TokenFlag.WHITESPACE_INFERRED},
                    )
                )
            text_tokens.append(
                TextToken(
                    text=token.text,
                    bbox=token.bbox,
                    sources=[EvidenceRef(token.source or SourceKind.OCR_PAGE, page_index, f"ocr:{line_index}:{token_index}")],
                    confidence=max(0.0, min(1.0, token.confidence if token.confidence is not None else 0.0)),
                    normalized_text=normalize_text(token.text),
                    provenance=token.provenance or _default_ocr_provenance(token),
                    rotation=token.rotation,
                    ocr_provenance=token.ocr_provenance,
                )
            )
            previous = token
        bbox = BBox.union_all([token.bbox for token in ordered])
        lines.append(
            TextLine(
                tokens=text_tokens,
                bbox=bbox,
                baseline=Baseline(y=bbox.y1, angle=_line_angle(ordered)),
                direction=WritingDirection.LEFT_TO_RIGHT if _line_angle(ordered) == 0.0 else WritingDirection.UNKNOWN,
                native_order_min=None,
                native_order_max=None,
                line_id=_ocr_line_id(page_index, bbox, ordered),
            )
        )
    # ``groups`` were created and appended in the upright OCR coordinate
    # system above.  Do not sort them again by their source-page bboxes when
    # EasyOCR corrected a physically rotated scan: those bboxes deliberately
    # remain in source coordinates for evidence and table assignment, where a
    # 180° scan would otherwise reverse the already-correct reading order.
    has_rotated_ocr_tokens = any(token.rotation % 360 for token in visible)
    if (
        page_rotation % 360
        or has_rotated_ocr_tokens
        or any(line.baseline is not None and abs(line.baseline.angle) > 0.01 for line in lines)
    ):
        result = lines
    else:
        result = sorted(lines, key=lambda line: (line.bbox.y0, line.bbox.x0))
    if dehyphenate:
        result = dehyphenate_ocr_lines(result)
    result = merge_ocr_bullet_markers(result, page_index=page_index)
    return result


def _default_ocr_provenance(token: OcrToken) -> str:
    if token.source == SourceKind.OCR_REGION:
        return "targeted_region_recovery"
    if token.source == SourceKind.TABLE_MODEL:
        return "visual_table_refinement"
    return "baseline"


def _ocr_line_id(page_index: int, bbox: BBox, tokens: list[OcrToken]) -> str:
    """Build a deterministic identity for one OCR line geometry."""
    text_key = " ".join(" ".join(token.text.split()) for token in tokens).strip()
    return (
        f"ocr:{page_index}:{bbox.x0:.3f}:{bbox.y0:.3f}:"
        f"{bbox.x1:.3f}:{bbox.y1:.3f}:{text_key}"
    )


def _should_insert_space(
    previous: OcrToken,
    current: OcrToken,
    previous_bbox: BBox,
    current_bbox: BBox,
) -> bool:
    """Decide whether an inter-token space should be inserted.

    Two rules govern the decision:

    1. **Geometric gap**: when the current box starts to the right of the
       previous box, a gap exists and a space is normally warranted.
    2. **Overlap fallback**: detectors sometimes return adjacent word boxes
       with a small overlap after rotation mapping.  A space is still
       inserted when the overlap is small and the boundary looks like a
       word boundary.

    **pt-BR punctuation rules (§31)** — applied to the *geometric gap* path
    only (overlap path already requires alphanumeric boundaries):

    - No space before: ``,.:;!?%)]}``.  Prevents ``palavra ,`` and
      ``12 %``.
    - No space after: ``([{``.  Prevents ``( texto``.
    - Currency prefix ``R$``: no space between ``R$`` and the following
      digits.  Prevents ``R$ 1.234``.

    These rules apply to all OCR backends — they operate on the assembled
    token text, not on any engine-specific data.
    """
    previous_text = previous.text.rstrip()
    current_text = current.text.lstrip()

    if current_bbox.x0 >= previous_bbox.x1:
        # Geometric gap exists — apply pt-BR punctuation suppression rules.
        if not previous_text or not current_text:
            return True
        if current_text[0] in _NO_SPACE_BEFORE:
            return False
        if previous_text[-1] in _NO_SPACE_AFTER:
            return False
        if _CURRENCY_PREFIX_RE.match(previous_text) and current_text[:1].isdigit():
            return False
        return True

    # Overlap path — only insert a space at alphanumeric word boundaries.
    if not previous_text or not current_text:
        return False
    if previous_text[-1].isspace() or current_text[0].isspace():
        return False
    if not (previous_text[-1].isalnum() and current_text[0].isalnum()):
        return False
    overlap = previous_bbox.x1 - current_bbox.x0
    line_height = max(previous_bbox.height, current_bbox.height, 1.0)
    allowed_overlap = 0.75 if (" " in previous_text or " " in current_text) else 0.45
    if overlap > line_height * allowed_overlap:
        return False
    return (
        " " in previous_text
        or " " in current_text
        or len(previous_text) <= 2
        or len(current_text) <= 2
    )


def _page_size(tokens: list[OcrToken], page_bbox: BBox | None) -> tuple[float, float]:
    if page_bbox is not None:
        return page_bbox.width, page_bbox.height
    return max(token.bbox.x1 for token in tokens), max(token.bbox.y1 for token in tokens)


def _virtual_bbox(token: OcrToken, page_bbox: BBox | None, page_width: float, page_height: float, page_rotation: int = 0) -> BBox:
    """Map a token's bounding box into the upright (0°) coordinate frame.

    Tokens from rotated-page OCR passes carry their original page coordinates
    and a ``rotation`` attribute.  This function undoes the rotation so that
    line grouping and reading-order sorting can operate on a consistently
    upright coordinate system.

    Args:
        token: The OCR token whose bbox will be transformed.
        page_bbox: Bounding box of the page in document coordinates, used
            to translate token coordinates to a page-local origin.  When
            ``None``, the origin is assumed to be (0, 0).
        page_width: Width of the page in the local coordinate system,
            required for 90° and 180° rotations.
        page_height: Height of the page in the local coordinate system,
            required for 270° and 180° rotations.

    Returns:
        A :class:`~structured_pdf_text.geometry.BBox` in the upright
        coordinate frame.  For ``rotation=0`` (or any value not in
        {90, 180, 270}) the box is returned with only the page-origin
        offset applied.
    """
    origin_x = page_bbox.x0 if page_bbox is not None else 0.0
    origin_y = page_bbox.y0 if page_bbox is not None else 0.0
    x0 = token.bbox.x0 - origin_x
    y0 = token.bbox.y0 - origin_y
    x1 = token.bbox.x1 - origin_x
    y1 = token.bbox.y1 - origin_y
    canonical = BBox(x0, y0, x1, y1)
    page_visual = canonical.rotate_to_visual(page_rotation, page_width, page_height)
    visual_width = page_height if page_rotation % 360 in (90, 270) else page_width
    visual_height = page_width if page_rotation % 360 in (90, 270) else page_height
    # ``token.rotation`` records an OCR orientation correction already mapped
    # back into canonical page coordinates. Undo that correction in the visual
    # frame after applying the PDF page presentation rotation.
    return _inverse_visual_bbox(page_visual, token.rotation, visual_width, visual_height)


def _gap_bbox(
    previous: BBox,
    current: BBox,
    token_rotation: int,
    page_rotation: int,
    page_bbox: BBox | None,
    page_width: float,
    page_height: float,
) -> BBox:
    """Compute the bounding box for an inferred inter-word space token.

    Constructs a virtual box spanning the horizontal gap between two adjacent
    token bounding boxes in the upright coordinate frame, then maps it back
    to the original (possibly rotated) page coordinate space so that the
    inferred whitespace token has a geometrically meaningful position for
    downstream consumers such as table models.

    When the two boxes overlap (i.e. ``current.x0 < previous.x1``), the gap
    is collapsed to a point at their midpoint rather than producing an
    inverted rectangle.

    Args:
        previous: Bounding box of the left-hand token in the upright frame.
        current: Bounding box of the right-hand token in the upright frame.
        rotation: Page rotation in degrees (0, 90, 180, or 270) used to
            reverse the coordinate transform when mapping back to page
            space.
        page_bbox: Page bounding box in document coordinates, used to
            restore the page-level origin offset.
        page_width: Page width in the local coordinate system.
        page_height: Page height in the local coordinate system.

    Returns:
        A :class:`~structured_pdf_text.geometry.BBox` in original page
        coordinates suitable for assignment to an inferred whitespace
        :class:`~structured_pdf_text.document.TextToken`.
    """
    left = previous.x1
    right = current.x0
    if right < left:
        midpoint = (left + right) / 2.0
        left = midpoint
        right = midpoint
    virtual = BBox(left, min(previous.y0, current.y0), right, max(previous.y1, current.y1))
    visual_width = page_height if page_rotation % 360 in (90, 270) else page_width
    visual_height = page_width if page_rotation % 360 in (90, 270) else page_height
    oriented = virtual.rotate_to_visual(token_rotation, visual_width, visual_height)
    local = _inverse_visual_bbox(oriented, page_rotation, page_width, page_height)
    origin_x = page_bbox.x0 if page_bbox is not None else 0.0
    origin_y = page_bbox.y0 if page_bbox is not None else 0.0
    return BBox(local.x0 + origin_x, local.y0 + origin_y, local.x1 + origin_x, local.y1 + origin_y)


def _inverse_visual_bbox(box: BBox, rotation: int, page_width: float, page_height: float) -> BBox:
    """Invert :meth:`BBox.rotate_to_visual` for a visual-frame box."""
    angle = rotation % 360
    if angle == 90:
        return BBox(box.y0, page_height - box.x1, box.y1, page_height - box.x0)
    if angle == 180:
        return BBox(page_width - box.x1, page_height - box.y1,
                    page_width - box.x0, page_height - box.y0)
    if angle == 270:
        return BBox(page_width - box.y1, box.x0, page_width - box.y0, box.x1)
    return box


def _line_angle(tokens: list[OcrToken]) -> float:
    if not tokens:
        return 0.0
    rotation = tokens[0].rotation % 360
    return {90: 1.5707963267948966, 180: 3.141592653589793, 270: 4.71238898038469}.get(rotation, 0.0)


# Regex: a hyphen at the very end of a line of OCR text.
# Only plain ASCII hyphen-minus; en-dash/em-dash are not OCR line-break markers.
_TRAILING_HYPHEN_RE = re.compile(r"-$")

# Identifiers, URLs, codes: if the word ending in hyphen has digits or is ALL-CAPS,
# the join is likely wrong (chemical formula, code, compound noun).  Conservative
# heuristic to avoid silently destroying structured data.
_ID_LIKE_RE = re.compile(r"[0-9A-Z]{2,}")


def _is_hyphen_break(prev_text: str, next_text: str) -> bool:
    """Return True when the previous line ends with a line-break hyphen.

    Conservative rules (§30):
      1. Previous line must end with ASCII hyphen-minus (-).
      2. Next line must start with a Unicode letter (catches accented pt-BR).
      3. The character before the hyphen must be a letter (not digit/code).
      4. The token before the hyphen must not look like an identifier/code.
      5. The continuation word must start with a lowercase letter (compound
         nouns like segunda-feira are already on one OCR line; hyphens at
         line-end before an uppercase word are likely proper nouns or headings
         where joining is risky).
    """
    prev = prev_text.rstrip()
    nxt = next_text.lstrip()
    if not _TRAILING_HYPHEN_RE.search(prev):
        return False
    if not nxt or not unicodedata.category(nxt[0]).startswith("L"):
        return False
    # Character before the hyphen must be a letter
    stem = prev[:-1]
    if not stem or not unicodedata.category(stem[-1]).startswith("L"):
        return False
    # If the stem has a capital run or digits, it is likely a code/ID
    if _ID_LIKE_RE.search(stem):
        return False
    # Only join when next word starts lowercase — uppercase may be a proper noun
    if nxt[0] != nxt[0].lower():
        return False
    return True


def dehyphenate_ocr_lines(lines: list[TextLine]) -> list[TextLine]:
    """Join OCR lines where the previous line ends with a line-break hyphen.

    This is a post-processing step that runs after ``reconstruct_ocr_lines``
    and operates only on the OCR path (§30).  The native text path is
    unaffected.

    The algorithm is deliberately conservative:
    - Only ASCII hyphen-minus triggers a join (not en-dash or em-dash).
    - The continuation must start with a lowercase Unicode letter.
    - Identifiers and codes (digits, all-caps) are never joined.
    - The gap between the two lines must be ≤ 2× the median line height
      (avoids joining across paragraphs or columns).

    When a join occurs:
    - The hyphen-ending token is stripped of its trailing hyphen.
    - The first token of the next line is appended directly (no space).
    - Remaining tokens of the next line follow with their usual spacing.
    - The merged line's bbox is the union of both original bboxes.
    - The consumed ``next`` line is removed from the output.

    Empty lines or lines with no tokens are passed through unchanged.
    """
    if len(lines) < 2:
        return lines

    # Median line height used for gap threshold
    heights = [line.bbox.height for line in lines if line.bbox.height > 0]
    median_height = median(heights) if heights else 10.0
    max_gap = 2.0 * median_height

    output: list[TextLine] = []
    skip_next = False
    for i, line in enumerate(lines):
        if skip_next:
            skip_next = False
            continue
        if i + 1 >= len(lines):
            output.append(line)
            continue

        next_line = lines[i + 1]
        prev_text = line.text
        next_text = next_line.text

        # Check vertical gap between lines
        vertical_gap = next_line.bbox.y0 - line.bbox.y1
        gap_ok = 0 <= vertical_gap <= max_gap
        horizontal_continuation = (
            line.bbox.x0 <= next_line.bbox.x1
            and next_line.bbox.x0 <= line.bbox.x1 + max(12.0, median_height * 1.5)
        )

        if gap_ok and horizontal_continuation and _is_hyphen_break(prev_text, next_text):
            # Build merged token list: strip trailing hyphen from last token of
            # current line, then append all tokens from next line (no extra space).
            merged_tokens = list(line.tokens)

            # Strip the trailing hyphen from the last non-space token
            for rev_idx in range(len(merged_tokens) - 1, -1, -1):
                tok = merged_tokens[rev_idx]
                if tok.text.rstrip():
                    stripped = tok.text.rstrip("-")
                    if stripped != tok.text:
                        merged_tokens[rev_idx] = replace(
                            tok, text=stripped, normalized_text=normalize_text(stripped),
                            flags=set(tok.flags) | {TokenFlag.WHITESPACE_INFERRED},
                        )
                    break

            # Append next line tokens directly (no leading space — the join
            # is exactly where the hyphen was)
            merged_tokens.extend(next_line.tokens)

            merged_bbox = BBox.union_all([line.bbox, next_line.bbox])
            merged_line = TextLine(
                tokens=merged_tokens,
                bbox=merged_bbox,
                baseline=line.baseline,
                direction=line.direction,
                native_order_min=line.native_order_min,
                native_order_max=next_line.native_order_max,
                gap_mode=line.gap_mode,
                order_mode=line.order_mode,
                line_id=line.line_id,
                text_override=None,
                join_next_without_space=line.join_next_without_space,
                ghost_punctuation_candidate=line.ghost_punctuation_candidate,
                merged_source_line_ids=(
                    line.merged_source_line_ids
                    + next_line.merged_source_line_ids
                    + (next_line.line_id,)
                ) if next_line.line_id else line.merged_source_line_ids,
            )
            output.append(merged_line)
            skip_next = True
        else:
            output.append(line)

    return output


# ---------------------------------------------------------------------------
# §29 — Paragraph segmentation post-OCR
# ---------------------------------------------------------------------------

def segment_ocr_paragraphs(lines: list[TextLine]) -> list[list[TextLine]]:
    """Group OCR ``TextLine`` objects into logical paragraphs (§29).

    OCR produces one ``TextLine`` per physical text band.  This function joins
    consecutive lines that belong to the same paragraph and splits them at
    paragraph boundaries, returning a list of groups (each group is one
    paragraph).

    A **paragraph break** is detected when any of the following holds between
    consecutive lines ``prev`` and ``curr``:

    1. **Large vertical gap** — ``gap > 1.4 × median_gap`` (significantly
       larger than the typical inter-line spacing indicates a paragraph break).
    2. **First-line indent** — ``curr.bbox.x0`` is at least ``indent_ths``
       further right than the paragraph's running left edge (common in
       indented first-line paragraphs).
    3. **Ragged-right transition** — the previous line is substantially
       shorter than the page width (< 60 % of the median line width) and the
       current line starts at the same left edge, suggesting the previous line
       ended the paragraph.
    4. **Column break** — ``curr.bbox.x0`` is substantially to the left of
       ``prev.bbox.x0`` (new column start, not indentation).

    Lines that are empty or very short (≤ 2 characters) are kept as separate
    single-line paragraphs (they are likely bullet markers or standalone labels).

    Args:
        lines: Ordered list of ``TextLine`` objects, typically the output of
            :func:`reconstruct_ocr_lines` or :func:`dehyphenate_ocr_lines`.

    Returns:
        A list of paragraph groups.  Each group is a non-empty ``list[TextLine]``.
        The order of lines within each group and the order of groups preserve
        the original reading order.
    """
    if not lines:
        return []
    if len(lines) == 1:
        return [lines]

    # Compute inter-line gaps (vertical distance between consecutive lines).
    gaps: list[float] = []
    for i in range(1, len(lines)):
        gap = lines[i].bbox.y0 - lines[i - 1].bbox.y1
        if gap > 0:
            gaps.append(gap)
    median_gap = median(gaps) if gaps else 0.0
    gap_threshold = max(median_gap * 1.4, 1.0)

    # Compute line widths for ragged-right detection.
    widths = [line.bbox.width for line in lines if line.bbox.width > 0]
    median_width = median(widths) if widths else 1.0
    # Indent threshold: a step of >= 1 standard character width to the right.
    heights = [line.bbox.height for line in lines if line.bbox.height > 0]
    char_width_estimate = (median(heights) if heights else 10.0) * 0.6
    indent_ths = max(char_width_estimate, 4.0)

    paragraphs: list[list[TextLine]] = []
    current_group: list[TextLine] = [lines[0]]
    current_left = lines[0].bbox.x0

    for i in range(1, len(lines)):
        prev = lines[i - 1]
        curr = lines[i]

        # Rule 1: large vertical gap.
        gap = curr.bbox.y0 - prev.bbox.y1
        if gap > gap_threshold:
            paragraphs.append(current_group)
            current_group = [curr]
            current_left = curr.bbox.x0
            continue

        # Rule 2: first-line indent relative to running left edge.
        if curr.bbox.x0 > current_left + indent_ths:
            paragraphs.append(current_group)
            current_group = [curr]
            current_left = curr.bbox.x0
            continue

        # Rule 3: previous line is ragged-right (much shorter than median).
        prev_text = prev.text.rstrip()
        if prev.bbox.width < median_width * 0.60 and len(prev_text) > 2:
            # Only break if the current line is not also short (avoids breaking
            # two consecutive short lines that are both part of the same block).
            if curr.bbox.width >= median_width * 0.55:
                paragraphs.append(current_group)
                current_group = [curr]
                current_left = curr.bbox.x0
                continue

        # Rule 4: column break — current line starts far to the left of previous.
        if curr.bbox.x0 < prev.bbox.x0 - indent_ths * 2:
            paragraphs.append(current_group)
            current_group = [curr]
            current_left = curr.bbox.x0
            continue

        # Continuation: same paragraph.
        current_group.append(curr)
        # Update running left edge (use minimum seen in this paragraph).
        current_left = min(current_left, curr.bbox.x0)

    if current_group:
        paragraphs.append(current_group)
    return paragraphs


# ---------------------------------------------------------------------------
# §32 — OCR-aware list detection: merge isolated bullet marker boxes
# ---------------------------------------------------------------------------

# Bullet glyphs that EasyOCR may return as a tiny separate box.
_BULLET_GLYPHS: frozenset[str] = frozenset(
    "•◦▪‣·●○■–—-*"
)

# Ordered list markers: single digit/letter followed by . or )
_ORDERED_MARKER_RE = re.compile(r"^(?:\d+|[A-Za-z])[.)]$")


def _is_ocr_bullet_marker(text: str) -> bool:
    """Return True if ``text`` looks like a standalone list marker from OCR.

    Detects single bullet glyphs AND ordered-list labels (``1.``, ``a)``, etc.)
    that EasyOCR returned as a box separate from the item text.
    """
    stripped = text.strip()
    if not stripped:
        return False
    if len(stripped) == 1 and stripped in _BULLET_GLYPHS:
        return True
    return bool(_ORDERED_MARKER_RE.match(stripped))


def merge_ocr_bullet_markers(
    lines: list[TextLine], *, page_index: int = 0
) -> list[TextLine]:
    """Join isolated OCR bullet-marker boxes with the text line to their right.

    EasyOCR sometimes returns a list marker (``•``, ``-``, ``1.``, …) as a tiny
    separate detection box on the same baseline as the item text.  When the
    marker line and the text line share the same vertical band (similar y-centre)
    and the text starts to the right of the marker, they are merged into a single
    ``TextLine`` whose text begins with the marker and a space (§32).

    Merge conditions (all must hold):
    - The candidate marker line has only 1–3 characters after stripping.
    - The text is recognised as a bullet/ordered-list marker.
    - The next line's y-centre is within ½ the median line height of the
      marker's y-centre (same row).
    - The next line starts to the right of the marker's right edge.
    - The gap between marker right edge and text left edge is ≤ 2× the
      median line height (not a cross-column join).

    The function is conservative: it only merges when ALL conditions hold so
    that legitimate single-character words are never accidentally consumed.

    Args:
        lines: OCR ``TextLine`` list, typically from :func:`reconstruct_ocr_lines`.

    Returns:
        A new list with merged lines where bullet markers were detected.
        Lines not involved in a merge are returned unchanged.
    """
    if len(lines) < 2:
        return list(lines)

    heights = [line.bbox.height for line in lines if line.bbox.height > 0]
    median_height = median(heights) if heights else 10.0
    row_tolerance = median_height * 0.5
    max_gap = median_height * 2.0

    result: list[TextLine] = []
    consumed: set[int] = set()

    for i, line in enumerate(lines):
        if i in consumed:
            continue
        stripped = line.text.strip()
        if len(stripped) <= 3 and _is_ocr_bullet_marker(stripped) and i + 1 < len(lines):
            next_line = lines[i + 1]
            # Check same-row condition
            marker_cy = line.bbox.cy
            text_cy = next_line.bbox.cy
            if abs(marker_cy - text_cy) <= row_tolerance:
                # Check spatial ordering: text to the right
                gap = next_line.bbox.x0 - line.bbox.x1
                if 0 <= gap <= max_gap:
                    # Merge: build a new token list = marker tokens + space + text tokens
                    merged_tokens = list(line.tokens)
                    # Insert a space token between marker and text
                    space_bbox = BBox(
                        line.bbox.x1,
                        min(line.bbox.y0, next_line.bbox.y0),
                        next_line.bbox.x0,
                        max(line.bbox.y1, next_line.bbox.y1),
                    )
                    from structured_pdf_text.document import EvidenceRef, TokenFlag
                    space_token = TextToken(
                        text=" ",
                        bbox=space_bbox,
                        sources=[EvidenceRef(SourceKind.OCR_PAGE, page_index, f"bullet-gap:{i}")],
                        confidence=0.60,
                        normalized_text=" ",
                        flags={TokenFlag.WHITESPACE_INFERRED},
                    )
                    merged_tokens.append(space_token)
                    merged_tokens.extend(next_line.tokens)
                    merged_bbox = BBox.union_all([line.bbox, next_line.bbox])
                    merged_line = TextLine(
                        tokens=merged_tokens,
                        bbox=merged_bbox,
                        baseline=line.baseline if line.baseline is not None else next_line.baseline,
                        direction=next_line.direction,
                        native_order_min=line.native_order_min,
                        native_order_max=next_line.native_order_max,
                        gap_mode=line.gap_mode,
                        order_mode=line.order_mode,
                        line_id=line.line_id,
                    )
                    result.append(merged_line)
                    consumed.add(i + 1)
                    continue
        result.append(line)

    return result
