"""Native PDF line reconstruction from PDFium character streams.

Converts raw ``NativeCharacter`` sequences produced by the evidence layer into
``TextLine`` objects by grouping glyphs that share the same baseline and
writing direction. The key decisions made here are:

- orientation bucketing (horizontal, vertical, rotated, other)
- baseline grouping with tolerance for ascenders/descenders
- column-gap splitting to prevent multi-column merging
- word-gap inference (bimodal / unimodal / fallback strategies)
- superscript/subscript merging
- reconciliation against PDFium's own text-page extraction
"""
from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass, replace
from statistics import median
from difflib import SequenceMatcher

from structured_pdf_text.document import (
    Baseline,
    EvidenceRef,
    NativeCharacter,
    SourceKind,
    TextLine,
    TextToken,
    TokenFlag,
    WritingDirection,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.text.normalize import normalize_text


@dataclass(frozen=True, slots=True)
class GapObservation:
    gap: float
    normalized_gap: float
    previous_char: NativeCharacter
    current_char: NativeCharacter
    same_font: bool
    font_size_ratio: float | None


def reconstruct_native_lines(
    characters: tuple[NativeCharacter, ...],
    extracted_text: str | None = None,
) -> list[TextLine]:
    """Reconstruct horizontal text lines from native PDF characters.

    This is intentionally conservative. It is not the final reading order engine;
    it is the native reconstruction slice used before layout and OCR exist.
    """
    visible_chars = [char for char in characters if _is_visible_text_char(char)]
    visible_chars = _remove_near_duplicates(visible_chars)
    if not visible_chars:
        return []

    orientation_groups: dict[str, list[NativeCharacter]] = {
        "horizontal": [],
        "horizontal_reverse": [],
        "vertical": [],
        "vertical_reverse": [],
        "other": [],
    }
    for character in visible_chars:
        orientation_groups[_orientation_bucket(character)].append(character)

    lines: list[TextLine] = []
    horizontal_groups = _group_by_baseline(orientation_groups["horizontal"])
    lines.extend(
        _line_from_chars(group, WritingDirection.LEFT_TO_RIGHT)
        for group in horizontal_groups
        if group
    )
    reverse_horizontal_groups = _group_by_baseline(orientation_groups["horizontal_reverse"])
    lines.extend(
        _line_from_chars(group, WritingDirection.RIGHT_TO_LEFT, reverse_axis=True)
        for group in reverse_horizontal_groups
        if group
    )
    vertical_groups = _group_by_vertical_axis(orientation_groups["vertical"])
    lines.extend(
        _line_from_chars(group, WritingDirection.TOP_TO_BOTTOM)
        for group in vertical_groups
        if group
    )
    reverse_vertical_groups = _group_by_vertical_axis(orientation_groups["vertical_reverse"])
    lines.extend(
        _line_from_chars(group, WritingDirection.TOP_TO_BOTTOM, reverse_axis=True)
        for group in reverse_vertical_groups
        if group
    )
    other_groups = _group_by_baseline(orientation_groups["other"])
    lines.extend(_line_from_chars(group, WritingDirection.UNKNOWN) for group in other_groups if group)
    lines.sort(key=lambda line: (line.bbox.y0, line.bbox.x0))
    if extracted_text:
        lines = _reconcile_with_textpage(lines, extracted_text)
    lines = _mark_ghost_punctuation_candidates(lines)
    lines = _merge_script_lines(lines)
    return lines


def lines_to_text(lines: list[TextLine]) -> str:
    """Join ordered text lines into a single newline-separated string.

    Applies NFC normalisation to each line so that combining codepoints stored
    separately (e.g. ``'e'`` + combining accent) are composed into their
    canonical precomposed forms (e.g. ``'é'``) before joining. Adjacent lines
    whose ``join_next_without_space`` flag is set are concatenated without an
    intervening newline (used for hyphen/soft-hyphen continuation).
    """
    # F12: apply NFC to each assembled line so that combining characters that
    # were stored as separate codepoints (e.g. 'e' + combining accent) compose
    # into their canonical forms (e.g. 'é') in the final output.
    from unicodedata import normalize as _nfc
    output: list[str] = []
    previous_join = False
    for line in lines:
        text = _nfc("NFC", line.text).rstrip()
        if not text:
            continue
        if output and previous_join:
            output[-1] += text.lstrip()
        else:
            output.append(text)
        previous_join = line.join_next_without_space
    return "\n".join(output).strip()


_RECONCILE_WINDOW = 8  # max candidates ahead of cursor to consider per line


def _reconcile_with_textpage(lines: list[TextLine], extracted_text: str) -> list[TextLine]:
    """Recover spacing/ligature mappings when PDFium exposes better line text.

    PDFium's character stream may contain tracked glyphs or a lossy font
    mapping while its text-page extraction still has the semantic string. A
    high-similarity match lets us retain native geometry and evidence without
    hard-coded lexical corrections.

    Matching is monotonic: the search cursor never moves backwards.  A local
    window of _RECONCILE_WINDOW candidates is searched from the cursor forward.
    This prevents similar lines in different regions (e.g. repeated IDs, near-
    identical labels) from swapping with each other due to a better global score.
    """
    candidates = [line.strip() for line in extracted_text.replace("\r", "").split("\n") if line.strip()]
    available = set(range(len(candidates)))
    cursor = 0  # monotonic lower bound — never decreases
    output: list[TextLine | None] = [None] * len(lines)
    processing_order = sorted(
        enumerate(lines),
        key=lambda item: (
            item[1].native_order_min is None,
            item[1].native_order_min
            if item[1].native_order_min is not None
            else item[0],
            item[0],
        ),
    )
    for line_index, line in processing_order:
        compact = _compact(line.text)
        if not compact:
            output[line_index] = line
            continue
        best_index: int | None = None
        best_score = 0.0
        search_end = min(cursor + _RECONCILE_WINDOW, len(candidates))
        for index in range(cursor, search_end):
            if index not in available:
                continue
            candidate = candidates[index]
            score = SequenceMatcher(None, compact, _compact(candidate)).ratio()
            if _compact(candidate) == compact:
                score = 1.0
            if score > best_score:
                best_index, best_score = index, score
        if best_index is not None and best_score >= 0.92:
            candidate = candidates[best_index]
            should_replace = (
                best_score < 1.0
                or _needs_textpage_spacing_recovery(line.text, candidate)
            )
            if should_replace:
                output[line_index] = TextLine(
                    tokens=line.tokens,
                    bbox=line.bbox,
                    baseline=line.baseline,
                    direction=line.direction,
                    native_order_min=line.native_order_min,
                    native_order_max=line.native_order_max,
                    gap_mode=line.gap_mode,
                    order_mode=line.order_mode,
                    line_id=line.line_id,
                    text_override=candidate,
                    join_next_without_space=line.join_next_without_space,
                )
            else:
                output[line_index] = line
            available.remove(best_index)
            cursor = best_index + 1
        else:
            output[line_index] = line
    return [line for line in output if line is not None]


def _is_ghost_punctuation_line(line: TextLine) -> bool:
    text = line.text.strip()
    if text not in {",", ".", "’", "’", "`", ":", ";"}:
        return False
    return line.bbox.width <= max(10.0, line.bbox.height * 1.8)


def _mark_ghost_punctuation_candidates(lines: list[TextLine]) -> list[TextLine]:
    """Mark ghost-punctuation candidates without filtering them.

    A line that passes the ghost heuristic is flagged so that a later,
    auditable step can decide suppression.  Removing content silently before
    the Content Conservation Ledger is accounted violates the preservation
    invariant; the flag makes the candidate visible to downstream consumers.
    """
    result: list[TextLine] = []
    for line in lines:
        if _is_ghost_punctuation_line(line) and not line.ghost_punctuation_candidate:
            result.append(replace(line, ghost_punctuation_candidate=True))
        else:
            result.append(line)
    return result


_SUPERSCRIPTS = str.maketrans("0123456789+-=()", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾")
_SUBSCRIPTS = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")


def _merge_script_lines(lines: list[TextLine]) -> list[TextLine]:
    """Attach isolated single-digit/sign glyphs as Unicode superscripts or subscripts.

    PDFium can emit a superscript or subscript character as a standalone line
    because its bounding box sits outside the baseline band of the host line.
    If the candidate is a single character that can be translated into a
    Unicode combining form, and it overlaps the host line horizontally while
    extending above or below its vertical bounds, the character is converted
    and appended to the host token list.
    """
    output = list(lines)
    for candidate in list(lines):
        text = candidate.text.strip()
        if len(text) != 1 or text not in "0123456789+-=()":
            continue
        targets = [
            line for line in output
            if line is not candidate
            and line.bbox.height >= candidate.bbox.height * 1.20
            # A normal baseline punctuation glyph (for example the hyphen in
            # ``2 - controles``) can be emitted as a tiny separate line. It
            # must remain independent; script merging is only valid when the
            # candidate actually extends above or below the target line box.
            and (
                candidate.bbox.y0 < line.bbox.y0
                or candidate.bbox.y1 > line.bbox.y1
            )
            and line.bbox.x0 - candidate.bbox.width <= candidate.bbox.cx <= line.bbox.x1 + candidate.bbox.width
            and candidate.bbox.overlap_ratio(line.bbox) >= 0.18
        ]
        if not targets:
            continue
        target = max(targets, key=lambda line: candidate.bbox.overlap_ratio(line.bbox))
        subscript = candidate.bbox.cy > target.bbox.cy
        script = text.translate(_SUBSCRIPTS if subscript else _SUPERSCRIPTS)
        script_token = replace(candidate.tokens[0], text=script, normalized_text=script)
        tokens = [
            token for token in target.tokens
            if not (
                token.text.isspace()
                and abs(token.bbox.cx - candidate.bbox.cx) <= max(5.0, candidate.bbox.width * 1.5)
            )
        ]
        tokens.append(script_token)
        tokens.sort(key=lambda token: (token.bbox.x0, token.bbox.y0))
        candidate_id = candidate.line_id or f"line:{id(candidate)}"
        merged = replace(
            target,
            tokens=tokens,
            bbox=BBox.union_all([target.bbox, candidate.bbox]),
            text_override=None,
            merged_source_line_ids=(*target.merged_source_line_ids, candidate_id),
        )
        output[output.index(target)] = merged
        output.remove(candidate)
    return sorted(output, key=lambda line: (line.bbox.y0, line.bbox.x0))


def _compact(text: str) -> str:
    return "".join(character.casefold() for character in text if character.isalnum())


def _needs_textpage_spacing_recovery(text: str, candidate: str | None = None) -> bool:
    compact = text.strip()
    if not compact:
        return False
    spaces = sum(character.isspace() for character in compact)
    if spaces / max(len(compact), 1) >= 0.18:
        return True
    if candidate is not None:
        candidate_spaces = sum(character.isspace() for character in candidate.strip())
        if candidate_spaces > spaces:
            return True
    return any(
        previous.islower() and current.isupper()
        for previous, current in zip(compact, compact[1:])
    )


def _is_visible_text_char(char: NativeCharacter) -> bool:
    if not char.text:
        return False
    if char.text in {"\r", "\n"}:
        return False
    if char.bbox.width < 0 or char.bbox.height < 0:
        return False
    # PDF text render mode 3 means "invisible" (clip only, no fill/stroke).
    # These chars exist for searchability but must not appear in text output.
    if char.text_render_mode == 3:
        return False
    return True


def _remove_near_duplicates(characters: list[NativeCharacter]) -> list[NativeCharacter]:
    kept: list[NativeCharacter] = []
    for char in sorted(characters, key=lambda c: (c.page_index, c.char_index)):
        duplicate = False
        for previous in kept[-12:]:
            if char.text != previous.text:
                continue
            if char.bbox.iou(previous.bbox) >= 0.90:
                duplicate = True
                break
            if abs(char.bbox.cx - previous.bbox.cx) <= 0.25 and abs(char.bbox.cy - previous.bbox.cy) <= 0.25:
                duplicate = True
                break
        if not duplicate:
            kept.append(char)
    return kept


def _group_by_baseline(characters: list[NativeCharacter]) -> list[list[NativeCharacter]]:
    """Cluster horizontal characters into lines by shared baseline position.

    Uses the lower edge of each glyph box (adjusted by the median height) as a
    stable baseline signal rather than the center, which is sensitive to glyph
    height variation. After the initial tolerance-based scan the result is
    refined by merging adjacent fragmented components, attaching inline marks,
    and splitting at column gutters.
    """
    # PDFium represents explicit spaces as almost-zero-height glyph boxes.
    # They are useful evidence for text/indentation, but must not dominate the
    # scale used to compare ordinary glyph baselines.
    heights = [char.bbox.height for char in characters if char.bbox.height > 0.25]
    median_height = median(heights) if heights else 8.0
    tolerance = max(1.5, median_height * 0.55)

    groups: list[list[NativeCharacter]] = []
    group_centers: list[float] = []
    for char in sorted(characters, key=lambda c: (_line_center(c, median_height), c.bbox.x0, c.char_index)):
        char_center = _line_center(char, median_height)
        best_index: int | None = None
        best_delta = float("inf")
        for idx, center_y in enumerate(group_centers):
            delta = abs(char_center - center_y)
            if delta <= tolerance and not _baseline_group_is_horizontally_connected(
                groups[idx], char, median_height
            ):
                continue
            if delta < best_delta:
                best_delta = delta
                best_index = idx
        if best_index is None or best_delta > tolerance:
            groups.append([char])
            group_centers.append(char_center)
        else:
            groups[best_index].append(char)
            group_centers[best_index] = median([_line_center(item, median_height) for item in groups[best_index]])
    groups = _merge_baseline_components(groups, tolerance, median_height)
    groups = _merge_inline_attached_components(groups, median_height)
    groups = _merge_inline_attached_groups(groups, median_height)
    groups = _split_groups_by_column_gap(groups)
    # A zero-height explicit space can be split from its host by the column
    # gap pass. Reconcile it once more after that pass, using native adjacency
    # and local geometry so it cannot bridge an unrelated column.
    return _merge_inline_attached_groups(groups, median_height)


def _baseline_group_is_horizontally_connected(
    group: list[NativeCharacter],
    character: NativeCharacter,
    median_height: float,
) -> bool:
    """Prevent nearby baselines in separate columns from becoming one line.

    Baseline proximity alone is insufficient when a sidebar is vertically
    offset by a few points. Characters from one inline run remain connected
    through normal word/glyph gaps; a distant column does not. Large gaps are
    still split later by ``_split_groups_by_column_gap``.
    """
    if not group:
        return False
    group_x0 = min(item.bbox.x0 for item in group)
    group_x1 = max(item.bbox.x1 for item in group)
    if character.bbox.x1 < group_x0:
        horizontal_gap = group_x0 - character.bbox.x1
    elif character.bbox.x0 > group_x1:
        horizontal_gap = character.bbox.x0 - group_x1
    else:
        horizontal_gap = 0.0
    return horizontal_gap <= max(20.0, median_height * 4.0)


def _merge_baseline_components(
    groups: list[list[NativeCharacter]],
    tolerance: float,
    median_height: float,
) -> list[list[NativeCharacter]]:
    """Rejoin adjacent components that belong to the same baseline.

    The incremental baseline assignment above intentionally keeps distant
    columns apart.  It can nevertheless create two components for one line
    when glyph metrics move the running center slightly.  Reconcile those
    components using both baseline proximity and their actual horizontal
    distance; this is order-independent and cannot bridge a normal column
    gutter.
    """
    result = [list(group) for group in groups]
    join_distance = max(20.0, median_height * 4.0)
    changed = True
    while changed:
        changed = False
        for left_index, left in enumerate(result):
            left_center = median(
                [_line_center(item, median_height) for item in left]
            )
            left_bbox = BBox.union_all([item.bbox for item in left])
            for right_index in range(left_index + 1, len(result)):
                right = result[right_index]
                right_center = median(
                    [_line_center(item, median_height) for item in right]
                )
                if (
                    abs(left_center - right_center) > tolerance
                    and not _inline_symbol_component_can_join(
                        left,
                        right,
                        median_height,
                    )
                ):
                    continue
                right_bbox = BBox.union_all([item.bbox for item in right])
                if left_bbox.x1 < right_bbox.x0:
                    horizontal_gap = right_bbox.x0 - left_bbox.x1
                elif right_bbox.x1 < left_bbox.x0:
                    horizontal_gap = left_bbox.x0 - right_bbox.x1
                else:
                    horizontal_gap = 0.0
                if horizontal_gap > join_distance:
                    continue
                left.extend(right)
                del result[right_index]
                changed = True
                break
            if changed:
                break
    return result


def _inline_symbol_component_can_join(
    first: list[NativeCharacter],
    second: list[NativeCharacter],
    median_height: float,
) -> bool:
    """Join a compact inline run contained in a larger visual line.

    PDFium can expose punctuation, superscripts, or small ordinal glyphs with
    a slightly different baseline from the surrounding text. If that run is
    contained in the larger component and overlaps it vertically, its
    geometry is stronger evidence of one line than the baseline delta alone.
    Ordinary letters remain excluded; the small-letter exception is limited
    to glyphs whose height confirms the same compact inline role.
    """
    smaller, larger = (
        (first, second) if len(first) <= len(second) else (second, first)
    )
    visible = [character for character in smaller if not character.text.isspace()]
    if not visible or len(smaller) > 8 or len(smaller) > max(8, len(larger) // 3):
        return False
    if not all(
        (
            unicodedata.category(character.text[0]).startswith(("N", "P", "S"))
            or (
                unicodedata.category(character.text[0]).startswith("L")
                and character.bbox.height < median_height * 0.75
            )
        )
        for character in visible
        if character.text
    ):
        return False

    smaller_bbox = BBox.union_all([character.bbox for character in smaller])
    larger_bbox = BBox.union_all([character.bbox for character in larger])
    vertical_overlap = min(smaller_bbox.y1, larger_bbox.y1) - max(
        smaller_bbox.y0,
        larger_bbox.y0,
    )
    if vertical_overlap <= 0.0:
        return False
    if vertical_overlap / max(min(smaller_bbox.height, larger_bbox.height), 0.01) < 0.35:
        return False
    containment_tolerance = max(2.0, median_height * 0.35)
    if smaller_bbox.x0 < larger_bbox.x0 - containment_tolerance:
        return False
    if smaller_bbox.x1 > larger_bbox.x1 + containment_tolerance:
        return False
    center_delta = abs(
        median(_line_center(character, median_height) for character in first)
        - median(_line_center(character, median_height) for character in second)
    )
    return center_delta <= max(4.0, median_height * 0.75)


def _merge_inline_attached_groups(
    groups: list[list[NativeCharacter]],
    median_height: float,
) -> list[list[NativeCharacter]]:
    """Attach inline punctuation/marks split by their smaller glyph boxes."""
    result = [list(group) for group in groups]
    for candidate in list(result):
        if len(candidate) != 1 or not _is_inline_mark(candidate[0]):
            continue
        character = candidate[0]
        targets = [group for group in result if group is not candidate]
        compatible = [
            group
            for group in targets
            if _inline_mark_is_attached(character, group, median_height)
        ]
        if not compatible:
            continue
        target = min(
            compatible,
            key=lambda group: min(
                abs(character.bbox.cx - item.bbox.cx) for item in group
            ),
        )
        target.append(character)
        result.remove(candidate)
    return result


def _merge_inline_attached_components(
    groups: list[list[NativeCharacter]],
    median_height: float,
) -> list[list[NativeCharacter]]:
    """Attach small glyph components that overlap an established text line.

    Descenders and superscripts can have a different box height and therefore
    a displaced baseline center.  When such a component is horizontally
    adjacent to, or lies inside, a larger line component and its boxes overlap
    vertically, the geometry is stronger evidence than the center alone.
    The small-component guard prevents two ordinary lines from being merged.
    """
    result = [list(group) for group in groups]
    max_center_delta = max(4.0, median_height * 0.75)
    changed = True
    while changed:
        changed = False
        for left_index, left in enumerate(result):
            for right_index in range(left_index + 1, len(result)):
                right = result[right_index]
                if min(len(left), len(right)) > 2:
                    continue
                left_bbox = BBox.union_all([item.bbox for item in left])
                right_bbox = BBox.union_all([item.bbox for item in right])
                vertical_overlap = min(left_bbox.y1, right_bbox.y1) - max(
                    left_bbox.y0, right_bbox.y0
                )
                minimum_height = min(left_bbox.height, right_bbox.height)
                if vertical_overlap <= 0.0 or vertical_overlap / max(minimum_height, 0.01) < 0.35:
                    continue
                left_center = median(
                    [_line_center(item, median_height) for item in left]
                )
                right_center = median(
                    [_line_center(item, median_height) for item in right]
                )
                if abs(left_center - right_center) > max_center_delta:
                    continue
                if left_bbox.x1 < right_bbox.x0:
                    horizontal_gap = right_bbox.x0 - left_bbox.x1
                elif right_bbox.x1 < left_bbox.x0:
                    horizontal_gap = left_bbox.x0 - right_bbox.x1
                else:
                    horizontal_gap = 0.0
                if horizontal_gap > max(4.0, median_height * 0.85):
                    continue
                left.extend(right)
                del result[right_index]
                changed = True
                break
            if changed:
                break
    return result


def _is_inline_mark(character: NativeCharacter) -> bool:
    text = character.text
    return bool(text) and (
        text.isspace()
        or unicodedata.category(text[0]).startswith(("M", "P"))
    )


def _inline_mark_is_attached(
    character: NativeCharacter,
    group: list[NativeCharacter],
    median_height: float,
) -> bool:
    """Return True when a mark or space character belongs to an established group.

    Zero-height explicit spaces are treated with stricter native-adjacency
    guards: they must be immediately before the host line in the PDFium
    character stream and lie within the host's vertical band, so that column
    gutters are never interpreted as leading spaces. Ordinary marks (category M
    or P) require only vertical overlap and a small horizontal gap.
    """
    group_bbox = BBox.union_all([item.bbox for item in group])
    horizontal_gap = 0.0
    if character.bbox.x1 < group_bbox.x0:
        horizontal_gap = group_bbox.x0 - character.bbox.x1
    elif character.bbox.x0 > group_bbox.x1:
        horizontal_gap = character.bbox.x0 - group_bbox.x1
    # PDFium may expose an explicit space as a zero-height glyph. Preserve it
    # when it is immediately before a host line in native order and lies on
    # that line's vertical band. This is textual evidence, not invented
    # indentation, and the native adjacency guard prevents column gutters
    # from being interpreted as leading spaces.
    if character.text.isspace() and character.bbox.height <= 0.25:
        host_start = min(item.char_index for item in group)
        native_delta = host_start - character.char_index
        vertical_tolerance = max(1.5, median_height * 0.35)
        on_host_band = (
            group_bbox.y0 - vertical_tolerance
            <= character.bbox.y0
            <= group_bbox.y1 + vertical_tolerance
        )
        return (
            character.bbox.x1 <= group_bbox.x0
            and 0 < native_delta <= 2
            and on_host_band
            and horizontal_gap <= max(14.0, median_height * 2.0)
        )

    vertical_overlap = min(character.bbox.y1, group_bbox.y1) - max(
        character.bbox.y0, group_bbox.y0
    )
    return vertical_overlap > 0.0 and horizontal_gap <= max(6.0, median_height * 1.5)


def _split_groups_by_column_gap(
    groups: list[list[NativeCharacter]],
) -> list[list[NativeCharacter]]:
    """Split baseline groups that span multiple columns.

    Characters sharing the same baseline may belong to different columns.
    A large horizontal gap between consecutive characters (sorted by x0)
    indicates a column boundary and must produce separate line segments.
    """
    result: list[list[NativeCharacter]] = []
    for group in groups:
        if len(group) < 2:
            result.append(group)
            continue
        sorted_by_x = sorted(group, key=lambda c: (c.bbox.x0, c.char_index))
        widths = [c.bbox.width for c in sorted_by_x if c.bbox.width > 0]
        if not widths:
            result.append(group)
            continue
        median_width = median(widths)
        # Column gaps are typically several times a character width. A more
        # conservative boundary protects tracked headings whose inter-glyph
        # spacing is intentionally large; ordinary word separation is handled
        # later by _infer_gap_threshold.
        positive_gaps = [
            curr.bbox.x0 - prev.bbox.x1
            for prev, curr in zip(sorted_by_x, sorted_by_x[1:])
            if curr.bbox.x0 - prev.bbox.x1 > 0.0
        ]
        typical_gap = median(positive_gaps) if positive_gaps else 0.0
        gap_threshold = max(
            8.0,
            median_width * 3.5,
            typical_gap * 3.0,
        )
        current: list[NativeCharacter] = [sorted_by_x[0]]
        for prev, curr in zip(sorted_by_x, sorted_by_x[1:]):
            horizontal_gap = curr.bbox.x0 - prev.bbox.x1
            if horizontal_gap > gap_threshold:
                result.append(current)
                current = [curr]
            else:
                current.append(curr)
        result.append(current)
    return result


def _line_center(char: NativeCharacter, median_height: float) -> float:
    # Glyphs from the same visual line do not share the same vertical center:
    # ascenders, accents and descenders change the top/bottom of each box.
    # The lower edge is the stable baseline signal for the horizontal line
    # detector. Using the center split one line into several groups when, for
    # example, ``i`` and accented characters were taller than lowercase glyphs.
    return char.bbox.y1 - median_height / 2.0


def _line_from_chars(
    characters: list[NativeCharacter],
    direction: WritingDirection,
    reverse_axis: bool = False,
) -> TextLine:
    """Build a ``TextLine`` from an already-grouped character list.

    Sorts characters geometrically along their writing axis (or by native
    PDFium index when the sequence is plausible), infers word-gap tokens via
    ``_chars_to_text_tokens_with_diagnostics``, computes the union bounding box
    and baseline, and propagates hyphen/soft-hyphen flags for line joining.
    """
    order_mode = "geometry"
    if direction == WritingDirection.TOP_TO_BOTTOM:
        ordered = sorted(
            characters,
            key=lambda c: ((-c.bbox.y0) if reverse_axis else c.bbox.y0, c.char_index),
        )
        baseline = Baseline(
            y=BBox.union_all([char.bbox for char in ordered]).x0,
            angle=3 * math.pi / 2 if reverse_axis else math.pi / 2,
        )
    else:
        ordered = sorted(
            characters,
            key=lambda c: ((-c.bbox.x0) if reverse_axis else c.bbox.x0, c.char_index),
        )
        if not reverse_axis:
            ordered = _restore_inline_whitespace_order(ordered)
            if _native_sequence_is_plausible(ordered):
                ordered = sorted(ordered, key=lambda char: char.char_index)
                order_mode = "native"
        bbox_for_baseline = BBox.union_all([char.bbox for char in ordered])
        baseline = Baseline(y=bbox_for_baseline.y1, angle=math.pi if reverse_axis else 0.0)
    boxes = [char.bbox for char in ordered]
    bbox = BBox.union_all(boxes)
    token_chars, gap_mode = _chars_to_text_tokens_with_diagnostics(
        ordered,
        direction,
        reverse_axis=reverse_axis,
    )
    native_indices = [char.char_index for char in ordered]
    return TextLine(
        tokens=token_chars,
        bbox=bbox,
        baseline=baseline,
        direction=direction,
        native_order_min=min(native_indices) if native_indices else None,
        native_order_max=max(native_indices) if native_indices else None,
        gap_mode=gap_mode,
        order_mode=order_mode,
        line_id=(
            f"native:{characters[0].page_index}:"
            f"{min(native_indices)}:{max(native_indices)}"
            if native_indices
            else None
        ),
        join_next_without_space=any(
            bool(char.hyphen) or "\ufffe" in char.text
            for char in characters
        ),
    )


def _chars_to_text_tokens(
    characters: list[NativeCharacter],
    direction: WritingDirection = WritingDirection.LEFT_TO_RIGHT,
    reverse_axis: bool = False,
) -> list[TextToken]:
    tokens, _ = _chars_to_text_tokens_with_diagnostics(
        characters,
        direction,
        reverse_axis=reverse_axis,
    )
    return tokens


def _chars_to_text_tokens_with_diagnostics(
    characters: list[NativeCharacter],
    direction: WritingDirection = WritingDirection.LEFT_TO_RIGHT,
    reverse_axis: bool = False,
) -> tuple[list[TextToken], str]:
    """Convert a sorted character sequence into ``TextToken`` objects with gap diagnostics.

    Infers whitespace tokens between characters whose geometric gap exceeds the
    threshold computed by ``_infer_gap_threshold_with_diagnostics``. Returns
    the token list together with a mode string describing which gap strategy was
    used (``'bimodal'``, ``'unimodal_conservative'``, ``'explicit'``, or
    ``'fallback'``).

    Typographically attached pairs (e.g. ``word,``) and compact compact
    identifier patterns (URLs, dates, UUIDs) are protected from erroneous gap
    insertion.
    """
    if not characters:
        return [], "fallback"

    advances = [
        (
            char.bbox.height
            if direction == WritingDirection.TOP_TO_BOTTOM
            else char.bbox.width
        )
        for char in characters
        if (
            char.bbox.height
            if direction == WritingDirection.TOP_TO_BOTTOM
            else char.bbox.width
        )
        > 0
        and not char.text.isspace()
    ]

    median_advance = median(advances) if advances else 5.0
    inferred_gap_threshold, gap_mode = _infer_gap_threshold_with_diagnostics(
        characters,
        direction,
        median_advance,
    )

    tokens: list[TextToken] = []
    previous: NativeCharacter | None = None

    for char in characters:
        if previous is not None:
            gap = _character_axis_gap(
                previous,
                char,
                direction,
                reverse_axis=reverse_axis,
            )

            if (
                gap > inferred_gap_threshold
                and not previous.text.isspace()
                and not char.text.isspace()
                and not _typographically_attached(
                    previous.text,
                    char.text,
                )
                and not _compact_sequence_gap(characters, previous, char)
            ):
                gap_box = _inferred_gap_bbox(
                    previous,
                    char,
                    direction,
                    reverse_axis=reverse_axis,
                )

                tokens.append(
                    TextToken(
                        text=" ",
                        bbox=gap_box,
                        sources=[
                            EvidenceRef(
                                SourceKind.NATIVE_GENERATED,
                                char.page_index,
                                (
                                    f"gap:"
                                    f"{previous.char_index}:"
                                    f"{char.char_index}"
                                ),
                            )
                        ],
                        confidence=0.55,
                        normalized_text=" ",
                        flags={
                            TokenFlag.WHITESPACE_INFERRED
                        },
                    )
                )

        flags: set[TokenFlag] = set()

        if char.generated:
            flags.add(TokenFlag.GENERATED)

        if char.unicode_mapping_failed:
            flags.add(
                TokenFlag.UNICODE_MAPPING_FAILED
            )

        tokens.append(
            TextToken(
                text=char.text,
                bbox=char.bbox,
                sources=[char.evidence_ref],
                confidence=(
                    0.95
                    if not flags
                    else 0.75
                ),
                normalized_text=normalize_text(
                    char.text
                ),
                flags=flags,
                font_name=char.font_name,
                font_size=char.font_size,
                font_weight=char.font_weight,
                fill_color=char.fill_color,
                stroke_color=char.stroke_color,
                text_render_mode=char.text_render_mode,
            )
        )

        previous = char

    has_inferred_space = any(
        token.text.isspace() and TokenFlag.WHITESPACE_INFERRED in token.flags
        for token in tokens
    )
    has_explicit_space = any(
        token.text.isspace() and TokenFlag.WHITESPACE_INFERRED not in token.flags
        for token in tokens
    )
    if has_explicit_space and not has_inferred_space:
        gap_mode = "explicit"
    return tokens, gap_mode


def _native_sequence_is_plausible(characters: list[NativeCharacter]) -> bool:
    """Prefer PDFium order only inside an already separated line segment."""
    if len(characters) < 3:
        return False
    native = sorted(characters, key=lambda char: char.char_index)
    positions = [char.bbox.x0 for char in native if not char.text.isspace()]
    if len(positions) < 3:
        return False
    monotonic = sum(current >= previous - 0.75 for previous, current in zip(positions, positions[1:]))
    consistency = monotonic / max(1, len(positions) - 1)
    indices = [char.char_index for char in native]
    compactness = sum((current - previous) <= 4 for previous, current in zip(indices, indices[1:])) / max(1, len(indices) - 1)
    return consistency >= 0.85 and compactness >= 0.70


def _infer_gap_threshold(
    characters: list[NativeCharacter],
    direction: WritingDirection,
    median_advance: float,
) -> float:
    return _infer_gap_threshold_with_diagnostics(
        characters,
        direction,
        median_advance,
    )[0]


def _infer_gap_threshold_with_diagnostics(
    characters: list[NativeCharacter],
    direction: WritingDirection,
    median_advance: float,
) -> tuple[float, str]:
    """Estimate the inter-character gap above which a space token should be inserted.

    Collects normalised inter-character gap observations, then looks for a
    bimodal split (tight kerning vs. word spacing). When a clear split exists
    the midpoint is used; otherwise a conservative median-MAD estimate prevents
    uniform-tracking lines from being over-segmented. Returns ``(threshold,
    mode)`` where mode is one of ``'bimodal'``, ``'unimodal_conservative'``, or
    ``'fallback'``.
    """
    observations = _gap_observations(characters, direction, median_advance)
    positive = sorted(observation.normalized_gap for observation in observations if observation.gap > 0)
    if not positive:
        return max(2.5, median_advance * 0.85), "fallback"
    if len(positive) == 1:
        return max(2.5, median_advance * 0.85), "fallback"

    # Look for a relative separation between two groups. Uniform tracking
    # produces one group and must not become a space between every glyph.
    threshold_normalized: float | None = None
    if len(positive) >= 3:
        gaps = [
            (positive[index + 1] - positive[index]) / max(positive[index], 0.01)
            for index in range(len(positive) - 1)
        ]
        split = max(range(len(gaps)), key=gaps.__getitem__)
        left = positive[: split + 1]
        right = positive[split + 1 :]
        if left and right and gaps[split] >= 0.35 and len(left) >= 1 and len(right) >= 1:
            threshold_normalized = (positive[split] + positive[split + 1]) / 2.0
            mode = "bimodal"
        else:
            mode = "unimodal_conservative"
    else:
        mode = "unimodal_conservative"
    if threshold_normalized is None:
        middle = median(positive)
        mad = median([abs(gap - middle) for gap in positive])
        threshold_normalized = middle + max(0.20, 2.5 * mad / max(median(positive), 0.01))
    return max(2.5, min(median_advance * 2.0, threshold_normalized * median_advance)), mode


def _gap_observations(
    characters: list[NativeCharacter],
    direction: WritingDirection,
    median_advance: float,
) -> list[GapObservation]:
    font_sizes = [
        char.font_size for char in characters
        if char.font_size is not None and char.font_size > 0
    ]
    median_font_size = median(font_sizes) if font_sizes else None
    observations: list[GapObservation] = []
    for previous, current in zip(characters, characters[1:]):
        if previous.text.isspace() or current.text.isspace():
            continue
        gap = _character_axis_gap(previous, current, direction, reverse_axis=False)
        if gap <= 0:
            continue
        advance_basis = median_advance
        if previous.font_size and current.font_size:
            advance_basis = max(0.01, (previous.font_size + current.font_size) / 2.0)
        normalized = gap / max(advance_basis, 0.01)
        size_ratio = None
        if previous.font_size and current.font_size:
            size_ratio = min(previous.font_size, current.font_size) / max(previous.font_size, current.font_size)
        observations.append(
            GapObservation(
                gap=gap,
                normalized_gap=normalized,
                previous_char=previous,
                current_char=current,
                same_font=bool(previous.font_name and current.font_name and previous.font_name == current.font_name),
                font_size_ratio=size_ratio if median_font_size is not None else None,
            )
        )
    return observations


def _character_axis_gap(
    previous: NativeCharacter,
    current: NativeCharacter,
    direction: WritingDirection,
    *,
    reverse_axis: bool,
) -> float:
    """
    Return the geometric distance between consecutive characters
    along their logical writing axis.

    Positive values represent empty space between glyphs.
    Zero or negative values represent touching or overlapping glyphs.
    """

    if direction == WritingDirection.TOP_TO_BOTTOM:
        if reverse_axis:
            return (
                previous.bbox.y0
                - current.bbox.y1
            )

        return (
            current.bbox.y0
            - previous.bbox.y1
        )

    if reverse_axis:
        return (
            previous.bbox.x0
            - current.bbox.x1
        )

    return (
        current.bbox.x0
        - previous.bbox.x1
    )


def _inferred_gap_bbox(
    previous: NativeCharacter,
    current: NativeCharacter,
    direction: WritingDirection,
    *,
    reverse_axis: bool,
) -> BBox:
    """
    Build the bounding box corresponding only to an inferred
    whitespace interval between two characters.

    This function is called only when _character_axis_gap()
    returned a positive gap above the inference threshold.

    BBox coordinates are always returned in canonical order:
    x0 <= x1 and y0 <= y1.
    """

    if direction == WritingDirection.TOP_TO_BOTTOM:
        x0 = min(
            previous.bbox.x0,
            current.bbox.x0,
        )
        x1 = max(
            previous.bbox.x1,
            current.bbox.x1,
        )

        if reverse_axis:
            y0 = current.bbox.y1
            y1 = previous.bbox.y0
        else:
            y0 = previous.bbox.y1
            y1 = current.bbox.y0

        return BBox(
            x0,
            y0,
            x1,
            y1,
        )

    y0 = min(
        previous.bbox.y0,
        current.bbox.y0,
    )
    y1 = max(
        previous.bbox.y1,
        current.bbox.y1,
    )

    if reverse_axis:
        x0 = current.bbox.x1
        x1 = previous.bbox.x0
    else:
        x0 = previous.bbox.x1
        x1 = current.bbox.x0

    return BBox(
        x0,
        y0,
        x1,
        y1,
    )

def _restore_inline_whitespace_order(
    characters: list[NativeCharacter],
) -> list[NativeCharacter]:
    """Put zero-area spaces back between their native sequence neighbours.

    PDFium reports a point rather than a glyph box for many spaces. In italic
    or tightly kerned text that point can fall just after the next glyph's
    left edge, so a purely geometric sort moves the space into the word.
    """
    ordered = list(characters)
    by_index = {character.char_index: character for character in characters}
    spaces = sorted(
        (character for character in characters if character.text.isspace()),
        key=lambda character: character.char_index,
    )
    for space in spaces:
        previous = by_index.get(space.char_index - 1)
        following = by_index.get(space.char_index + 1)
        if previous is None or following is None:
            continue
        previous_position = ordered.index(previous)
        following_position = ordered.index(following)
        if previous_position >= following_position:
            continue
        ordered.remove(space)
        previous_position = ordered.index(previous)
        ordered.insert(previous_position + 1, space)
    return ordered


def _typographically_attached(previous: str, current: str) -> bool:
    previous = previous.rstrip()
    current = current.lstrip()
    if not previous or not current:
        return False
    return current[0] in ",.;:!?%)]}\"”’/$€£" or previous[-1] in "([{\"“‘/$€£-"


def _compact_sequence_gap(
    characters: list[NativeCharacter],
    previous: NativeCharacter,
    current: NativeCharacter,
) -> bool:
    """Protect generic compact identifiers from geometry-only word breaks."""
    if previous.text.isdigit() and current.text.isdigit():
        return True
    compact_punctuation = set("/@:_-")
    line_text = "".join(char.text for char in characters if not char.text.isspace())
    if any(marker in line_text for marker in compact_punctuation) and (
        previous.text.isalnum() or current.text.isalnum()
    ):
        # Explicit whitespace characters have already taken precedence and
        # are represented as tokens, so this only protects geometry-inferred
        # gaps inside a compact URL, e-mail, date, UUID, or code.
        return True
    return False


def spacing_diagnostics(lines: list[TextLine]) -> dict[str, int]:
    """Summarize explicit/inferred spacing and ordering decisions."""
    explicit = 0
    inferred = 0
    native = 0
    geometry = 0
    gap_modes = {
        "explicit": 0,
        "bimodal": 0,
        "unimodal_conservative": 0,
        "fallback": 0,
    }
    order_modes = {
        "native": 0,
        "geometry": 0,
    }
    for line in lines:
        for token in line.tokens:
            if token.text.isspace():
                if TokenFlag.WHITESPACE_INFERRED in token.flags:
                    inferred += 1
                else:
                    explicit += 1
        mode = getattr(line, "gap_mode", "fallback")
        gap_modes[mode if mode in gap_modes else "fallback"] += 1
        order_mode = getattr(line, "order_mode", "geometry")
        order_modes[order_mode if order_mode in order_modes else "geometry"] += 1
    native = order_modes["native"]
    geometry = order_modes["geometry"]
    # The line reconstruction path is intentionally conservative; expose the
    # counters even when a line has no positive gaps for audit consumers.
    return {
        "explicit_whitespace_count": explicit,
        "inferred_whitespace_count": inferred,
        "gap_explicit_lines": gap_modes["explicit"],
        "gap_bimodal_lines": gap_modes["bimodal"],
        "gap_unimodal_lines": gap_modes["unimodal_conservative"],
        "gap_fallback_lines": gap_modes["fallback"],
        "native_order_used_lines": native,
        "geometry_order_used_lines": geometry,
    }


def _orientation_bucket(character: NativeCharacter) -> str:
    """Map a character's rotation angle to one of five orientation categories.

    Returns one of ``'horizontal'``, ``'horizontal_reverse'``, ``'vertical'``,
    ``'vertical_reverse'``, or ``'other'``. Characters with no angle information
    default to ``'horizontal'``. A ±8° tolerance is applied so slightly rotated
    glyphs in a nominally axis-aligned font are not separated into their own
    group.
    """
    if character.angle is None:
        return "horizontal"
    angle = character.angle % (2.0 * math.pi)
    quarter_turn = min(
        abs(angle),
        abs(angle - math.pi / 2),
        abs(angle - math.pi),
        abs(angle - 3 * math.pi / 2),
        abs(angle - 2 * math.pi),
    )
    if quarter_turn <= math.radians(8):
        nearest = min(
            (0.0, math.pi / 2, math.pi, 3 * math.pi / 2, 2 * math.pi),
            key=lambda value: abs(angle - value),
        )
        if abs(nearest - math.pi / 2) <= math.radians(8):
            return "vertical"
        if abs(nearest - 3 * math.pi / 2) <= math.radians(8):
            return "vertical_reverse"
        if abs(nearest - math.pi) <= math.radians(8):
            return "horizontal_reverse"
        return "horizontal"
    return "other"


def _group_by_vertical_axis(characters: list[NativeCharacter]) -> list[list[NativeCharacter]]:
    """Cluster vertically-oriented characters by their horizontal centre position.

    Used for top-to-bottom text runs (e.g. CJK vertical typesetting or rotated
    labels). Characters are grouped by proximity of their ``bbox.cx`` value
    using a tolerance derived from the median glyph width.
    """
    widths = [char.bbox.width for char in characters if char.bbox.width > 0]
    tolerance = max(1.5, (median(widths) if widths else 8.0) * 0.75)
    groups: list[list[NativeCharacter]] = []
    centers: list[float] = []
    for character in sorted(characters, key=lambda item: (item.bbox.cx, item.bbox.y0, item.char_index)):
        best_index = None
        best_delta = float("inf")
        for index, center in enumerate(centers):
            delta = abs(character.bbox.cx - center)
            if delta < best_delta:
                best_delta = delta
                best_index = index
        if best_index is None or best_delta > tolerance:
            groups.append([character])
            centers.append(character.bbox.cx)
        else:
            groups[best_index].append(character)
            centers[best_index] = median([item.bbox.cx for item in groups[best_index]])
    return groups
