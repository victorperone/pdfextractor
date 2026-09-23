"""Page complexity analysis for native PDF evidence.

Heuristics that classify each page by the quality and reliability of its
native text layer.  No OCR or learned model is invoked here; the analysis
combines Unicode statistics, geometry, and — optionally — a rendered image
to produce a :class:`PageComplexity` verdict with a recommended
:class:`~structured_pdf_text.document.PageStrategy`.
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass, field
from statistics import median
from typing import Any

from structured_pdf_text.document import ComplexityReason, NativeCharacter, NativePageEvidence, PageStrategy
from structured_pdf_text.geometry import BBox


@dataclass(frozen=True, slots=True)
class PageComplexity:
    """Immutable verdict produced by :class:`ComplexityAnalyzer` for one page.

    Attributes:
        reasons: Set of :class:`~structured_pdf_text.document.ComplexityReason`
            flags that explain why the page may need recovery.
        native_text_score: Float in [0, 1] estimating how trustworthy the
            native PDF text layer is (1 = fully reliable, 0 = completely
            untrustworthy).
        visual_recovery_needed: True when at least one reason indicates that
            the native text layer is incomplete or corrupt and visual recovery
            (OCR, region merging) should be attempted.
        layout_needed: True when structural complexity (tables, multi-column
            layout, rotated text, embedded images) warrants layout analysis.
        full_page_ocr_candidate: True when the page should be sent through
            full-page OCR rather than region-level recovery.
        recommended_strategy: Recommended
            :class:`~structured_pdf_text.document.PageStrategy` derived from
            the above flags.
        facts: Diagnostic key-value bag with the raw measurements that
            produced this verdict.
    """

    reasons: set[ComplexityReason]
    native_text_score: float
    visual_recovery_needed: bool
    layout_needed: bool
    full_page_ocr_candidate: bool
    recommended_strategy: PageStrategy
    facts: dict[str, float | int | bool | str | None] = field(default_factory=dict)


class ComplexityAnalyzer:
    """Deterministic quality gate for native evidence.

    The analyzer intentionally does not call OCR or a learned model. Rendering
    is used only to compare native claims with visible ink and to classify a
    page as a recovery candidate.
    """

    def __init__(self, render_scale: float = 0.5) -> None:
        self.render_scale = max(0.05, float(render_scale))

    def analyze(self, page: NativePageEvidence, rendered_page: Any | None = None) -> PageComplexity:
        """Classify a single page and return its complexity verdict.

        Runs a multi-signal heuristic pipeline over the native PDF evidence:

        1. **Character filtering** — whitespace and zero-area characters are
           removed; the useful-text string drives length/coverage checks.
        2. **Coverage metrics** — union area of text, image and path bounding
           boxes, normalised to page area.
        3. **Grid detection** — thin horizontal and vertical path segments are
           counted to detect table grids.
        4. **Unicode quality** — replacement-character ratio (U+FFFD),
           private-use-area ratio, control-character ratio and explicit
           mapping-failure flags are each thresholded independently.
        5. **Duplicate-layer detection** — characters whose centre and text
           match an earlier character inside a 2-pixel bucket are counted.
        6. **Invisible-text detection** — when *rendered_page* is provided,
           each text line is checked against visible ink in the rendered image;
           lines with an ink ratio below 1.5 % are counted as invisible.
        7. **Reason accumulation** — each threshold gate adds a
           :class:`~structured_pdf_text.document.ComplexityReason` flag.
        8. **Score and strategy** — a continuous ``native_text_score`` and a
           discrete :class:`~structured_pdf_text.document.PageStrategy` are
           derived from the collected flags.

        Args:
            page: Native evidence bundle extracted from the PDF page.
            rendered_page: Optional PIL ``Image`` of the rendered page used
                for visible-ink checks.  When *None*, ink-based signals are
                skipped.

        Returns:
            A :class:`PageComplexity` instance with the verdict, score and
            a ``facts`` dict containing all raw measurements for diagnostics.
        """
        characters = [
            character
            for character in page.characters
            if character.text and not character.text.isspace() and character.bbox.area > 0
        ]
        text = "".join(character.text for character in page.characters)
        useful_text = "".join(character.text for character in characters)
        page_area = max(page.bbox.area, 1.0)
        text_boxes = [character.bbox for character in characters]
        text_coverage = _coverage(text_boxes, page_area)

        image_boxes = [image.bbox for image in page.objects.images if image.bbox and image.bbox.area > 0]
        image_coverage = _coverage(image_boxes, page_area)
        largest_image_coverage = max((box.area / page_area for box in image_boxes), default=0.0)

        path_boxes = [
            path.bbox
            for path in page.objects.paths
            if path.bbox and path.bbox.area > 0 and not _is_page_background(path.bbox, page.bbox)
        ]
        path_coverage = _coverage(path_boxes, page_area)
        grid_line_count = _grid_line_count(path_boxes, page.bbox)

        duplicate_char_count = _duplicate_char_count(characters)
        duplicate_char_ratio = duplicate_char_count / max(len(characters), 1)
        replacement_ratio = _replacement_ratio(text)
        private_use_ratio = _private_use_ratio(text)
        control_ratio = _control_ratio(text)
        mapping_failure_ratio = sum(
            1 for character in page.characters if character.unicode_mapping_failed is True
        ) / max(len(page.characters), 1)

        visible_ink_ratio: float | None = None
        invisible_line_count = 0
        line_count = 0
        if rendered_page is not None:
            visible_ink_ratio = _ink_ratio(rendered_page)
            lines = _line_boxes(characters)
            line_count = len(lines)
            invisible_line_count = sum(
                1
                for line in lines
                if _ink_ratio_in_bbox(rendered_page, line, page.bbox) < 0.015
            )

        reasons: set[ComplexityReason] = set()
        if not useful_text:
            reasons.add(ComplexityReason.NO_TEXT)
        if _is_scanned_candidate(
            useful_text_length=len(useful_text),
            image_coverage=image_coverage,
            largest_image_coverage=largest_image_coverage,
            visible_ink_ratio=visible_ink_ratio,
        ):
            reasons.add(ComplexityReason.SCANNED)
        if len(useful_text) < 80 and image_coverage >= 0.05:
            reasons.add(ComplexityReason.SPARSE_TEXT)
        if (
            replacement_ratio > 0.01
            or private_use_ratio > 0.10
            or control_ratio > 0.02
            or mapping_failure_ratio > 0.05
        ):
            reasons.add(ComplexityReason.GARBLED_UNICODE)
        if duplicate_char_ratio > 0.10:
            reasons.add(ComplexityReason.DUPLICATE_TEXT_LAYER)
        if invisible_line_count >= 2 and invisible_line_count / max(line_count, 1) >= 0.35:
            reasons.add(ComplexityReason.INVISIBLE_TEXT)
        if _vector_text_signal(path_coverage, path_boxes, text_coverage, len(useful_text)):
            reasons.add(ComplexityReason.VECTOR_TEXT)
        if page.objects.annotations and len(useful_text) < 80:
            reasons.add(ComplexityReason.ANNOTATION_TEXT)
        if _has_rotated_text(page):
            reasons.add(ComplexityReason.ROTATED_TEXT)
        multi_column = _multi_column_signal(page, characters, grid_line_count)
        if multi_column:
            reasons.add(ComplexityReason.MULTI_COLUMN_LIKELY)
        table_likely = grid_line_count >= 4 and len(useful_text) >= 20
        if table_likely:
            reasons.add(ComplexityReason.TABLE_LIKELY)

        native_text_score = _native_text_score(
            reasons,
            useful_text_length=len(useful_text),
            text_coverage=text_coverage,
            duplicate_char_ratio=duplicate_char_ratio,
            mapping_failure_ratio=mapping_failure_ratio,
        )
        full_page_ocr_candidate = bool(
            {ComplexityReason.NO_TEXT, ComplexityReason.SCANNED} & reasons
        ) and not bool(
            ComplexityReason.NO_TEXT in reasons
            and visible_ink_ratio is not None
            and visible_ink_ratio < 0.002
        )
        visual_recovery_needed = bool(
            {
                ComplexityReason.NO_TEXT,
                ComplexityReason.SCANNED,
                ComplexityReason.SPARSE_TEXT,
                ComplexityReason.GARBLED_UNICODE,
                ComplexityReason.INVISIBLE_TEXT,
                ComplexityReason.VECTOR_TEXT,
                ComplexityReason.ANNOTATION_TEXT,
            }
            & reasons
        )
        layout_needed = bool(
            {
                ComplexityReason.TABLE_LIKELY,
                ComplexityReason.MULTI_COLUMN_LIKELY,
                ComplexityReason.ROTATED_TEXT,
                ComplexityReason.EMBEDDED_IMAGES,
            }
            & reasons
        )
        if image_coverage >= 0.05 and image_coverage < 0.75:
            reasons.add(ComplexityReason.EMBEDDED_IMAGES)
            visual_recovery_needed = True
            layout_needed = True

        if full_page_ocr_candidate:
            strategy = PageStrategy.OCR_CANDIDATE
        elif visual_recovery_needed or layout_needed:
            strategy = PageStrategy.HYBRID_CANDIDATE
        else:
            strategy = PageStrategy.NATIVE

        facts: dict[str, float | int | bool | str | None] = {
            "native_char_count": len(page.characters),
            "useful_text_length": len(useful_text),
            "text_coverage": round(text_coverage, 6),
            "image_count": len(image_boxes),
            "image_coverage": round(image_coverage, 6),
            "largest_image_coverage": round(largest_image_coverage, 6),
            "path_count": len(path_boxes),
            "path_coverage": round(path_coverage, 6),
            "grid_line_count": grid_line_count,
            "duplicate_char_ratio": round(duplicate_char_ratio, 6),
            "replacement_ratio": round(replacement_ratio, 6),
            "private_use_ratio": round(private_use_ratio, 6),
            "control_ratio": round(control_ratio, 6),
            "unicode_mapping_failure_ratio": round(mapping_failure_ratio, 6),
            "rendered_for_diagnostics": rendered_page is not None,
            "visible_ink_ratio": None if visible_ink_ratio is None else round(visible_ink_ratio, 6),
            "text_lines_checked": line_count,
            "invisible_text_lines": invisible_line_count,
            "annotation_count": len(page.objects.annotations),
            "multi_column_signal": multi_column,
            "table_signal": table_likely,
        }
        return PageComplexity(
            reasons=reasons,
            native_text_score=native_text_score,
            visual_recovery_needed=visual_recovery_needed,
            layout_needed=layout_needed,
            full_page_ocr_candidate=full_page_ocr_candidate,
            recommended_strategy=strategy,
            facts=facts,
        )


def _coverage(boxes: list[BBox], page_area: float) -> float:
    return min(1.0, _union_area(boxes) / page_area) if boxes else 0.0


def _union_area(boxes: list[BBox]) -> float:
    """Compute the exact union area of a list of axis-aligned bounding boxes.

    Uses a sweep-line algorithm: x-edges partition the plane into vertical
    slabs; within each slab the covered y-intervals are merged and summed.
    Returns 0.0 for an empty list.
    """
    if not boxes:
        return 0.0
    x_edges = sorted({box.x0 for box in boxes} | {box.x1 for box in boxes})
    area = 0.0
    for left, right in zip(x_edges, x_edges[1:]):
        if right <= left:
            continue
        intervals = [
            (box.y0, box.y1)
            for box in boxes
            if box.x0 < right and box.x1 > left and box.y1 > box.y0
        ]
        if not intervals:
            continue
        intervals.sort()
        covered_y = 0.0
        start, end = intervals[0]
        for next_start, next_end in intervals[1:]:
            if next_start > end:
                covered_y += max(0.0, end - start)
                start, end = next_start, next_end
            else:
                end = max(end, next_end)
        covered_y += max(0.0, end - start)
        area += (right - left) * covered_y
    return area


def _is_page_background(box: BBox, page_bbox: BBox) -> bool:
    return box.overlap_ratio(page_bbox) >= 0.98 and box.area / max(page_bbox.area, 1.0) >= 0.90


def _grid_line_count(boxes: list[BBox], page_bbox: BBox) -> int:
    """Count thin path segments that form a table grid.

    A path box qualifies as a horizontal rule when its height is at most 4 pt
    and its width spans at least 20 % of the page; it qualifies as a vertical
    rule when its width is at most 4 pt and its height spans at least 12 % of
    the page.  Returns the total segment count only when both directions have
    at least two qualifying segments (i.e., a real grid), otherwise returns 0.
    """
    horizontal = 0
    vertical = 0
    for box in boxes:
        if box.height <= 4.0 and box.width >= page_bbox.width * 0.20:
            horizontal += 1
        if box.width <= 4.0 and box.height >= page_bbox.height * 0.12:
            vertical += 1
    return horizontal + vertical if horizontal >= 2 and vertical >= 2 else 0


def _vector_text_signal(
    path_coverage: float,
    path_boxes: list[BBox],
    text_coverage: float,
    useful_text_length: int,
) -> bool:
    if not path_boxes:
        return False
    # A path layer is interesting when it occupies visible area not explained
    # by text, but ordinary table rules should not become a vector-text alarm.
    return path_coverage >= 0.20 and useful_text_length < 120 and text_coverage < 0.02


def _is_scanned_candidate(
    useful_text_length: int,
    image_coverage: float,
    largest_image_coverage: float,
    visible_ink_ratio: float | None,
) -> bool:
    """Return True when the page looks like a scanned image with no reliable native text.

    Three independent signals trigger a positive result:

    - Very little native text (≤ 30 chars) combined with a large image that
      covers at least 75 % of the page.
    - No native text at all and images covering at least 40 % of the page.
    - No native text at all but visible ink in a rendered image (ink ratio ≥ 2 %),
      suggesting content not reflected in the native layer.
    """
    if useful_text_length <= 30 and largest_image_coverage >= 0.75:
        return True
    if useful_text_length == 0 and image_coverage >= 0.40:
        return True
    return useful_text_length == 0 and visible_ink_ratio is not None and visible_ink_ratio >= 0.02


def _replacement_ratio(text: str) -> float:
    return text.count("\ufffd") / max(len(text), 1)


def _private_use_ratio(text: str) -> float:
    return sum(1 for char in text if 0xE000 <= ord(char) <= 0xF8FF) / max(len(text), 1)


def _control_ratio(text: str) -> float:
    """Return the fraction of non-whitespace characters classified as control characters.

    Unicode general category "C*" characters (other than U+FFFD) are treated as
    control characters.  A high ratio indicates an unreliable or corrupt text layer.
    """
    meaningful = [char for char in text if not char.isspace()]
    controls = sum(
        1
        for char in meaningful
        if unicodedata.category(char).startswith("C") and char != "\ufffd"
    )
    return controls / max(len(meaningful), 1)


def _duplicate_char_count(characters: list[NativeCharacter]) -> int:
    """Count characters that are spatial and textual duplicates of an earlier character.

    Characters are bucketed by their centre coordinates (rounded to 2 pt cells).
    A character is a duplicate when another character with identical text has an
    IoU of at least 0.85 with it.  The count drives the ``duplicate_char_ratio``
    used by the DUPLICATE_TEXT_LAYER signal.
    """
    if len(characters) < 2:
        return 0
    buckets: dict[tuple[int, int], list[NativeCharacter]] = {}
    duplicate_count = 0
    for character in characters:
        key = (round(character.bbox.cx / 2.0), round(character.bbox.cy / 2.0))
        candidates = []
        for x in range(key[0] - 1, key[0] + 2):
            for y in range(key[1] - 1, key[1] + 2):
                candidates.extend(buckets.get((x, y), []))
        if any(
            character.text == other.text and character.bbox.iou(other.bbox) >= 0.85
            for other in candidates
        ):
            duplicate_count += 1
        buckets.setdefault(key, []).append(character)
    return duplicate_count


def _line_boxes(characters: list[NativeCharacter]) -> list[BBox]:
    """Group characters into text lines and return each line's bounding box.

    Characters are sorted by vertical centre, then grouped using a greedy
    nearest-centre strategy.  The vertical tolerance is 60 % of the median
    character height (minimum 1.5 pt).  Each group's union bounding box is
    returned as a :class:`~structured_pdf_text.geometry.BBox`.
    """
    if not characters:
        return []
    heights = [character.bbox.height for character in characters if character.bbox.height > 0]
    tolerance = max(1.5, (median(heights) if heights else 8.0) * 0.60)
    groups: list[list[NativeCharacter]] = []
    centers: list[float] = []
    for character in sorted(characters, key=lambda item: (item.bbox.cy, item.bbox.x0, item.char_index)):
        best_index = None
        best_delta = math.inf
        for index, center in enumerate(centers):
            delta = abs(character.bbox.cy - center)
            if delta < best_delta:
                best_delta = delta
                best_index = index
        if best_index is None or best_delta > tolerance:
            groups.append([character])
            centers.append(character.bbox.cy)
        else:
            groups[best_index].append(character)
            centers[best_index] = median([item.bbox.cy for item in groups[best_index]])
    return [BBox.union_all([character.bbox for character in group]) for group in groups]


def _ink_ratio(image: Any) -> float:
    pixels = list(image.convert("RGB").getdata())
    if not pixels:
        return 0.0
    ink = sum(1 for red, green, blue in pixels if min(red, green, blue) < 245)
    return ink / len(pixels)


def _ink_ratio_in_bbox(image: Any, bbox: BBox, page_bbox: BBox) -> float:
    """Return the fraction of pixels inside a bounding box that contain visible ink.

    Coordinates are re-projected from PDF space (relative to *page_bbox*) to
    pixel space before cropping.  Delegates to :func:`_ink_ratio` for the
    per-pixel darkness threshold.  Returns 0.0 when the projected region has
    zero size.
    """
    if page_bbox.width <= 0 or page_bbox.height <= 0:
        return 0.0
    width, height = image.size
    left = max(0, min(width, math.floor((bbox.x0 - page_bbox.x0) / page_bbox.width * width)))
    top = max(0, min(height, math.floor((bbox.y0 - page_bbox.y0) / page_bbox.height * height)))
    right = max(left + 1, min(width, math.ceil((bbox.x1 - page_bbox.x0) / page_bbox.width * width)))
    bottom = max(top + 1, min(height, math.ceil((bbox.y1 - page_bbox.y0) / page_bbox.height * height)))
    if right <= left or bottom <= top:
        return 0.0
    return _ink_ratio(image.crop((left, top, right, bottom)))


def _has_rotated_text(page: NativePageEvidence) -> bool:
    angles = [character.angle for character in page.characters if character.angle is not None and character.text.strip()]
    return any(abs(angle) > 1.0 for angle in angles)


def _multi_column_signal(
    page: NativePageEvidence,
    characters: list[NativeCharacter],
    grid_line_count: int,
) -> bool:
    """Return True when the page text is arranged in two or more columns.

    The test is intentionally conservative: it requires at least 120 useful
    characters, both left and right halves must each contain at least 20 % of
    all characters, and a horizontal gutter of at least 6 % of the page width
    must separate the two groups.  Pages with a detected table grid are excluded
    because table rules can look similar to prose columns.
    """
    if page.bbox.width <= 0 or len(characters) < 120 or grid_line_count >= 4:
        return False
    # This is intentionally conservative. It only reports two substantial
    # horizontal bands with a meaningful gutter; table grids are handled by
    # TABLE_LIKELY instead of being mislabeled as prose columns.
    midpoint = page.bbox.x0 + page.bbox.width / 2.0
    left = [character for character in characters if character.bbox.cx < midpoint]
    right = [character for character in characters if character.bbox.cx >= midpoint]
    if len(left) < len(characters) * 0.20 or len(right) < len(characters) * 0.20:
        return False
    left_right = max(character.bbox.x1 for character in left)
    right_left = min(character.bbox.x0 for character in right)
    return right_left - left_right > page.bbox.width * 0.06


def _native_text_score(
    reasons: set[ComplexityReason],
    useful_text_length: int,
    text_coverage: float,
    duplicate_char_ratio: float,
    mapping_failure_ratio: float,
) -> float:
    """Compute a [0, 1] score representing how trustworthy the native text layer is.

    Starts at 1.0 and subtracts fixed penalties for each active
    :class:`~structured_pdf_text.document.ComplexityReason`, plus continuous
    penalties proportional to the duplicate-character and Unicode-mapping-failure
    ratios.  A score of 1.0 means the layer is fully reliable; 0.0 means it
    cannot be trusted.
    """
    score = 1.0
    if useful_text_length == 0:
        score -= 0.80
    elif text_coverage < 0.002:
        score -= 0.10
    penalties = {
        ComplexityReason.GARBLED_UNICODE: 0.45,
        ComplexityReason.DUPLICATE_TEXT_LAYER: 0.25,
        ComplexityReason.SPARSE_TEXT: 0.20,
        ComplexityReason.NO_TEXT: 0.70,
        ComplexityReason.SCANNED: 0.80,
        ComplexityReason.INVISIBLE_TEXT: 0.35,
        ComplexityReason.VECTOR_TEXT: 0.20,
        ComplexityReason.ANNOTATION_TEXT: 0.15,
    }
    for reason, penalty in penalties.items():
        if reason in reasons:
            score -= penalty
    score -= min(0.20, duplicate_char_ratio * 0.50)
    score -= min(0.20, mapping_failure_ratio * 0.50)
    return max(0.0, min(1.0, score))
