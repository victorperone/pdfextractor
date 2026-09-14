from __future__ import annotations

import math
from statistics import median

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


def reconstruct_native_lines(characters: tuple[NativeCharacter, ...]) -> list[TextLine]:
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
    return lines


def lines_to_text(lines: list[TextLine]) -> str:
    # F12: apply NFC to each assembled line so that combining characters that
    # were stored as separate codepoints (e.g. 'e' + combining accent) compose
    # into their canonical forms (e.g. 'é') in the final output.
    from unicodedata import normalize as _nfc
    return "\n".join(_nfc("NFC", line.text).rstrip() for line in lines).strip()


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
    heights = [char.bbox.height for char in characters if char.bbox.height > 0]
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
            if delta < best_delta:
                best_delta = delta
                best_index = idx
        if best_index is None or best_delta > tolerance:
            groups.append([char])
            group_centers.append(char_center)
        else:
            groups[best_index].append(char)
            group_centers[best_index] = median([_line_center(item, median_height) for item in groups[best_index]])
    return _split_groups_by_column_gap(groups)


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
        # Column gaps are typically several times a character width. A
        # threshold of 2.5× median width catches standard column gutters
        # while keeping normal word spacing (≈0.5–1× width) unsplit.
        gap_threshold = max(20.0, median_width * 2.5)
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
    if (
        char.bbox.height < median_height * 0.25
        or (
            char.text in "_,.;"
            and char.bbox.height < median_height * 0.70
        )
    ):
        # Baseline glyphs such as underscores, periods and hyphens may have a
        # very short box whose geometric center falls below the surrounding
        # letters. Anchor them using the shared baseline instead.
        return char.bbox.y1 - median_height / 2.0
    return char.bbox.cy


def _line_from_chars(
    characters: list[NativeCharacter],
    direction: WritingDirection,
    reverse_axis: bool = False,
) -> TextLine:
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
        bbox_for_baseline = BBox.union_all([char.bbox for char in ordered])
        baseline = Baseline(y=bbox_for_baseline.y1, angle=math.pi if reverse_axis else 0.0)
    boxes = [char.bbox for char in ordered]
    bbox = BBox.union_all(boxes)
    token_chars = _chars_to_text_tokens(ordered, direction, reverse_axis=reverse_axis)
    native_indices = [char.char_index for char in ordered]
    return TextLine(
        tokens=token_chars,
        bbox=bbox,
        baseline=baseline,
        direction=direction,
        native_order_min=min(native_indices) if native_indices else None,
        native_order_max=max(native_indices) if native_indices else None,
    )


def _chars_to_text_tokens(
    characters: list[NativeCharacter],
    direction: WritingDirection = WritingDirection.LEFT_TO_RIGHT,
    reverse_axis: bool = False,
) -> list[TextToken]:
    if not characters:
        return []
    advances = [
        (char.bbox.height if direction == WritingDirection.TOP_TO_BOTTOM else char.bbox.width)
        for char in characters
        if (char.bbox.height if direction == WritingDirection.TOP_TO_BOTTOM else char.bbox.width) > 0
        and not char.text.isspace()
    ]
    median_advance = median(advances) if advances else 5.0
    inferred_gap_threshold = max(2.5, median_advance * 0.85)
    tokens: list[TextToken] = []
    previous: NativeCharacter | None = None
    for char in characters:
        if previous is not None:
            if direction == WritingDirection.TOP_TO_BOTTOM:
                gap = (
                    previous.bbox.y0 - char.bbox.y1
                    if reverse_axis
                    else char.bbox.y0 - previous.bbox.y1
                )
            else:
                gap = (
                    previous.bbox.x0 - char.bbox.x1
                    if reverse_axis
                    else char.bbox.x0 - previous.bbox.x1
                )
            if (
                gap > inferred_gap_threshold
                and not previous.text.isspace()
                and not char.text.isspace()
                and not _typographically_attached(previous.text, char.text)
            ):
                if direction == WritingDirection.TOP_TO_BOTTOM:
                    gap_box = BBox(
                        previous.bbox.x0,
                        min(previous.bbox.y0, char.bbox.y1),
                        previous.bbox.x1,
                        max(previous.bbox.y0, char.bbox.y1),
                    )
                else:
                    gap_box = BBox(
                        min(previous.bbox.x0, char.bbox.x1),
                        previous.bbox.y0,
                        max(previous.bbox.x0, char.bbox.x1),
                        previous.bbox.y1,
                    )
                tokens.append(
                    TextToken(
                        text=" ",
                        bbox=gap_box,
                        sources=[EvidenceRef(SourceKind.NATIVE_GENERATED, char.page_index, f"gap:{previous.char_index}:{char.char_index}")],
                        confidence=0.55,
                        normalized_text=" ",
                        flags={TokenFlag.WHITESPACE_INFERRED},
                    )
                )
        flags: set[TokenFlag] = set()
        if char.generated:
            flags.add(TokenFlag.GENERATED)
        if char.unicode_mapping_failed:
            flags.add(TokenFlag.UNICODE_MAPPING_FAILED)
        tokens.append(
            TextToken(
                text=char.text,
                bbox=char.bbox,
                sources=[char.evidence_ref],
                confidence=0.95 if not flags else 0.75,
                normalized_text=normalize_text(char.text),
                flags=flags,
            )
        )
        previous = char
    return tokens


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
    return current[0] in ",.;:!?%)]}" or previous[-1] in "([{"


def _orientation_bucket(character: NativeCharacter) -> str:
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
