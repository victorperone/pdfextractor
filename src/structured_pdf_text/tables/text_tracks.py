from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from math import ceil
from statistics import median

from structured_pdf_text.document import (
    LayoutRegion,
    NativePageEvidence,
    StructuredTable,
    TableCell,
    TableFragment,
    TableMethod,
    TextLine,
    TextToken,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.tables.text_join import join_table_tokens


@dataclass(frozen=True, slots=True)
class TextTrackAssessment:
    accepted: bool
    confidence: float
    reasons: tuple[str, ...]
    row_count: int
    column_count: int
    track_recurrence: float
    row_consistency: float
    numeric_cell_ratio: float
    prose_score: float
    code_score: float = 0.0
    prefix_candidate_count: int = 0
    prefix_accepted_count: int = 0
    prefix_rejected_count: int = 0
    prefix_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _CellGroup:
    tokens: tuple[TextToken, ...]
    bbox: BBox
    text: str


@dataclass(frozen=True, slots=True)
class _Candidate:
    lines: tuple[TextLine, ...]
    rows: tuple[tuple[_CellGroup, ...], ...]
    anchors: tuple[float, ...]
    assessment: TextTrackAssessment


class ProseVsTableClassifier:
    """Reject prose before aligned text is promoted to a borderless table."""

    def assess(
        self,
        rows: tuple[tuple[_CellGroup, ...], ...],
        anchors: tuple[float, ...],
    ) -> TextTrackAssessment:
        row_count = len(rows)
        column_count = len(anchors)
        assignments = [_assign_groups(row, anchors) for row in rows]
        populated_per_row = [len({column for column, _ in row}) for row in assignments]
        recurrence_by_column = [
            sum(any(column == index for column, _ in row) for row in assignments)
            / max(row_count, 1)
            for index in range(column_count)
        ]
        track_recurrence = (
            sum(recurrence_by_column) / max(column_count, 1)
            if recurrence_by_column
            else 0.0
        )
        row_consistency = 1.0 - sum(
            abs(count - column_count) / max(column_count, 1)
            for count in populated_per_row
        ) / max(row_count, 1)
        non_label_cells = [
            group
            for row in assignments
            for column, group in row
            if column > 0
        ]
        numeric_cell_ratio = sum(
            any(character.isdigit() for character in group.text)
            for group in non_label_cells
        ) / max(len(non_label_cells), 1)
        sentence_ratio = sum(
            " ".join(group.text for group in row).rstrip().endswith((".", "!", "?", ";"))
            for row in rows
        ) / max(row_count, 1)
        long_cell_ratio = sum(
            len(group.text.split()) >= 5 or len(group.text) >= 48
            for row in rows
            for group in row
        ) / max(sum(len(row) for row in rows), 1)
        prose_score = min(1.0, sentence_ratio * 0.60 + long_cell_ratio * 0.80)
        code_score = sum(
            _looks_like_code_row(" ".join(group.text for group in row))
            for row in rows
        ) / max(row_count, 1)

        reasons: list[str] = []
        if row_count < 3:
            reasons.append("fewer_than_three_rows")
        if column_count < 2:
            reasons.append("fewer_than_two_tracks")
        if track_recurrence < 0.58:
            reasons.append("x_tracks_not_recurrent")
        if row_consistency < 0.58:
            reasons.append("row_shape_inconsistent")
        if prose_score >= 0.40:
            reasons.append("continuous_prose_signal")
        if code_score >= 0.60:
            reasons.append("code_layout_signal")
        accepted = not reasons
        confidence = max(
            0.0,
            min(
                1.0,
                0.34 * track_recurrence
                + 0.30 * row_consistency
                + 0.18 * min(1.0, row_count / 5.0)
                + 0.10 * min(1.0, column_count / 4.0)
                + 0.08 * numeric_cell_ratio
                - 0.25 * prose_score
                - 0.30 * code_score,
            ),
        )
        if accepted:
            reasons.extend(("recurrent_x_tracks", "consistent_row_shape", "prose_rejected"))
        return TextTrackAssessment(
            accepted=accepted,
            confidence=round(confidence, 6),
            reasons=tuple(reasons),
            row_count=row_count,
            column_count=column_count,
            track_recurrence=round(track_recurrence, 6),
            row_consistency=round(row_consistency, 6),
            numeric_cell_ratio=round(numeric_cell_ratio, 6),
            prose_score=round(prose_score, 6),
            code_score=round(code_score, 6),
        )


def _looks_like_code_row(text: str) -> bool:
    normalized = " ".join(text.strip().split()).casefold()
    if normalized.startswith(
        ("def ", "for ", "while ", "if ", "elif ", "else:", "return ", "//")
    ):
        return True
    if normalized.startswith("# "):
        return True
    programming_punctuation = sum(character in "()[]{}_=/*" for character in normalized)
    return " = " in normalized and programming_punctuation >= 3


def assess_borderless_region(region: LayoutRegion) -> list[TextTrackAssessment]:
    """Return auditable assessments for every table-like run in a region."""
    return [candidate.assessment for candidate in _candidates(region)]


def detect_borderless_table(
    page: NativePageEvidence,
    region: LayoutRegion,
    table_id: str | None = None,
) -> StructuredTable | None:
    """Detect a borderless table from recurrent text alignment tracks."""
    accepted = [candidate for candidate in _candidates(region) if candidate.assessment.accepted]
    if not accepted:
        return None
    candidate = max(
        accepted,
        key=lambda item: (item.assessment.confidence, len(item.lines)),
    )
    return _build_table(
        page,
        region,
        candidate,
        table_id or f"{region.region_id}:text-tracks",
    )


def _candidates(region: LayoutRegion) -> list[_Candidate]:
    lines = sorted(region.native_lines, key=lambda line: (line.bbox.y0, line.bbox.x0))
    if len(lines) < 3:
        return []
    heights = [line.bbox.height for line in lines if line.bbox.height > 0]
    typical_height = median(heights) if heights else 10.0
    gap_threshold = max(7.0, typical_height * 0.85, region.bbox.width * 0.012)
    runs: list[list[tuple[TextLine, tuple[_CellGroup, ...]]]] = []
    current: list[tuple[TextLine, tuple[_CellGroup, ...]]] = []
    previous: TextLine | None = None
    for line in lines:
        groups = _cell_groups(line, gap_threshold)
        vertical_break = previous is not None and (
            line.bbox.y0 - previous.bbox.y1 > max(18.0, typical_height * 2.4)
        )
        if len(groups) < 2 or vertical_break:
            if len(current) >= 3:
                runs.append(current)
            current = []
        if len(groups) >= 2:
            current.append((line, groups))
        previous = line
    if len(current) >= 3:
        runs.append(current)

    classifier = ProseVsTableClassifier()
    output: list[_Candidate] = []
    positions = {id(line): index for index, line in enumerate(lines)}
    for run in runs:
        first_index = positions[id(run[0][0])]
        table_body_bbox = BBox.union_all([item[0].bbox for item in run])
        body_rows = tuple(item[1] for item in run)
        body_anchors = _infer_anchors(body_rows, region.bbox.width)
        prefix: list[tuple[TextLine, tuple[_CellGroup, ...]]] = []
        prefix_candidate_count = 0
        prefix_accepted_count = 0
        prefix_rejected_count = 0
        prefix_reasons: list[str] = []
        next_line = run[0][0]
        for line in reversed(lines[max(0, first_index - 2) : first_index]):
            text = line.text.strip()
            groups = _cell_groups(line, gap_threshold)
            prefix_candidate_count += 1
            close = next_line.bbox.y0 - line.bbox.y1 <= max(18.0, typical_height * 2.4)
            horizontally_related = not (
                line.bbox.x1 < table_body_bbox.x0 or line.bbox.x0 > table_body_bbox.x1
            )
            if not text or text.endswith((".", "!", "?", ";")) or not close or not horizontally_related:
                prefix_rejected_count += 1
                prefix_reasons.append("proximity_or_punctuation")
                break
            structural_reason = _prefix_structural_reason(
                line,
                groups,
                table_body_bbox,
                body_anchors,
                region.bbox.width,
            )
            if structural_reason is not None:
                prefix_rejected_count += 1
                prefix_reasons.append(structural_reason)
                break
            prefix.append((line, groups))
            prefix_accepted_count += 1
            next_line = line
        enriched = list(reversed(prefix)) + run
        run_lines = tuple(item[0] for item in enriched)
        rows = tuple(item[1] for item in enriched)
        anchors = _infer_anchors(rows, region.bbox.width)
        assessment = classifier.assess(rows, anchors)
        assessment = replace(
            assessment,
            prefix_candidate_count=prefix_candidate_count,
            prefix_accepted_count=prefix_accepted_count,
            prefix_rejected_count=prefix_rejected_count,
            prefix_reasons=tuple(prefix_reasons),
        )
        output.append(_Candidate(run_lines, rows, anchors, assessment))
    return output


def _prefix_structural_reason(
    line: TextLine,
    groups: tuple[_CellGroup, ...],
    table_body_bbox: BBox,
    body_anchors: tuple[float, ...],
    region_width: float,
) -> str | None:
    tolerance = max(4.0, region_width * 0.02)
    aligned = bool(
        body_anchors
        and any(
            abs(group.bbox.x0 - anchor) <= tolerance
            for group in groups
            for anchor in body_anchors
        )
    )
    spanning = (
        line.bbox.x0 <= table_body_bbox.x0 + tolerance
        and line.bbox.x1 >= table_body_bbox.x1 - tolerance
        and line.bbox.width >= table_body_bbox.width * 0.75
    )
    long_prose = len(line.text.split()) >= 8 or len(line.text) >= 60
    if long_prose and not spanning and not aligned:
        return "prose_prefix_without_track_alignment"
    if not aligned and not spanning:
        return "prefix_without_anchor_or_spanning_geometry"
    return None


def _cell_groups(line: TextLine, gap_threshold: float) -> tuple[_CellGroup, ...]:
    visible = [token for token in line.tokens if token.text and not token.text.isspace()]
    if not visible:
        return ()
    groups: list[list[TextToken]] = []
    current: list[TextToken] = []
    previous: TextToken | None = None
    for token in sorted(visible, key=lambda item: item.bbox.x0):
        if previous is not None and token.bbox.x0 - previous.bbox.x1 > gap_threshold:
            groups.append(current)
            current = []
        current.append(token)
        previous = token
    if current:
        groups.append(current)
    output: list[_CellGroup] = []
    for group in groups:
        bbox = BBox.union_all([token.bbox for token in group])
        text = _join_original_tokens(line.tokens, bbox)
        output.append(_CellGroup(tuple(group), bbox, text))
    return tuple(output)


def _join_original_tokens(tokens: list[TextToken], bbox: BBox) -> str:
    selected = [
        token
        for token in tokens
        if bbox.x0 - 1.0 <= token.bbox.cx <= bbox.x1 + 1.0
    ]
    return join_table_tokens(selected)


def _infer_anchors(
    rows: tuple[tuple[_CellGroup, ...], ...],
    region_width: float,
) -> tuple[float, ...]:
    tolerance = max(4.0, region_width * 0.015)
    clusters: list[list[float]] = []
    for value in sorted(group.bbox.x0 for row in rows for group in row):
        if not clusters or abs(value - median(clusters[-1])) > tolerance:
            clusters.append([value])
        else:
            clusters[-1].append(value)
    required = max(2, ceil(len(rows) * 0.45))
    anchors = [float(median(cluster)) for cluster in clusters if len(cluster) >= required]
    if len(anchors) >= 2:
        return tuple(anchors)

    modal_count, frequency = Counter(len(row) for row in rows).most_common(1)[0]
    if modal_count < 2 or frequency < max(2, ceil(len(rows) * 0.60)):
        return ()
    modal_rows = [row for row in rows if len(row) == modal_count]
    return tuple(
        float(median(row[index].bbox.x0 for row in modal_rows))
        for index in range(modal_count)
    )


def _assign_groups(
    row: tuple[_CellGroup, ...],
    anchors: tuple[float, ...],
) -> list[tuple[int, _CellGroup]]:
    if not anchors:
        return []
    return [
        (min(range(len(anchors)), key=lambda index: abs(anchors[index] - group.bbox.x0)), group)
        for group in row
    ]


def _build_table(
    page: NativePageEvidence,
    region: LayoutRegion,
    candidate: _Candidate,
    table_id: str,
) -> StructuredTable:
    lines = candidate.lines
    anchors = candidate.anchors
    table_bbox = BBox.union_all([line.bbox for line in lines])
    x_edges = [table_bbox.x0]
    x_edges.extend((left + right) / 2.0 for left, right in zip(anchors, anchors[1:]))
    x_edges.append(table_bbox.x1)
    y_centers = [line.bbox.cy for line in lines]
    y_edges = [table_bbox.y0]
    y_edges.extend((top + bottom) / 2.0 for top, bottom in zip(y_centers, y_centers[1:]))
    y_edges.append(table_bbox.y1)
    cells: list[TableCell] = []
    for row_index, groups in enumerate(candidate.rows):
        assigned = _assign_groups(groups, anchors)
        by_column: dict[int, list[_CellGroup]] = {}
        for column, group in assigned:
            by_column.setdefault(column, []).append(group)
        for column in range(len(anchors)):
            cell_groups = sorted(by_column.get(column, []), key=lambda group: group.bbox.x0)
            tokens = [token for group in cell_groups for token in group.tokens]
            text = join_table_tokens(
                [token for group in cell_groups for token in group.tokens]
            )
            cells.append(
                TableCell(
                    row=row_index,
                    col=column,
                    rowspan=1,
                    colspan=1,
                    bbox=BBox(
                        x_edges[column],
                        y_edges[row_index],
                        x_edges[column + 1],
                        y_edges[row_index + 1],
                    ),
                    text=text,
                    tokens=tokens,
                    confidence=candidate.assessment.confidence if text else 0.45,
                )
            )
    return StructuredTable(
        table_id=table_id,
        page_fragments=[
            TableFragment(
                page_index=page.page_index,
                bbox=table_bbox,
                row_start=0,
                row_end=len(lines) - 1,
            )
        ],
        cells=cells,
        column_count=len(anchors),
        row_count=len(lines),
        confidence=candidate.assessment.confidence,
        method=TableMethod.TEXT_TRACKS,
    )
