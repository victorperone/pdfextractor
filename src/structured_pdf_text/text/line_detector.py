from __future__ import annotations

import math
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class GapObservation:
    gap: float
    normalized_gap: float
    previous_char: NativeCharacter
    current_char: NativeCharacter
    same_font: bool
    font_size_ratio: float | None


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
        # Column gaps are typically several times a character width. A more
        # conservative boundary protects tracked headings whose inter-glyph
        # spacing is intentionally large; ordinary word separation is handled
        # later by _infer_gap_threshold.
        gap_threshold = max(20.0, median_width * 4.0)
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
