from __future__ import annotations

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


def reconstruct_ocr_lines(
    tokens: list[OcrToken],
    page_index: int,
    page_bbox: BBox | None = None,
) -> list[TextLine]:
    """Convert OCR tokens into lines using the OCR reading coordinate system.

    Rotated-page OCR tokens remain in page coordinates for evidence and table
    assignment, but are grouped and ordered in the temporary upright system
    used by the OCR pass.
    """
    visible = [token for token in tokens if token.text and token.bbox.width >= 0 and token.bbox.height >= 0]
    if not visible:
        return []
    page_width, page_height = _page_size(visible, page_bbox)
    virtual_boxes = {id(token): _virtual_bbox(token, page_bbox, page_width, page_height) for token in visible}
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
            )
        )
    if any(line.baseline is not None and abs(line.baseline.angle) > 0.01 for line in lines):
        return lines
    return sorted(lines, key=lambda line: (line.bbox.y0, line.bbox.x0))


def _should_insert_space(
    previous: OcrToken,
    current: OcrToken,
    previous_bbox: BBox,
    current_bbox: BBox,
) -> bool:
    """Recover separators when OCR word boxes overlap by a few pixels.

    Detectors sometimes return adjacent word boxes with a small overlap after
    rotation mapping. Only apply the overlap fallback when the token boundary
    looks like a word boundary; ordinary gaps continue to use the geometric
    rule above.
    """
    if current_bbox.x0 >= previous_bbox.x1:
        return True
    previous_text = previous.text.rstrip()
    current_text = current.text.lstrip()
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


def _virtual_bbox(token: OcrToken, page_bbox: BBox | None, page_width: float, page_height: float) -> BBox:
    origin_x = page_bbox.x0 if page_bbox is not None else 0.0
    origin_y = page_bbox.y0 if page_bbox is not None else 0.0
    x0 = token.bbox.x0 - origin_x
    y0 = token.bbox.y0 - origin_y
    x1 = token.bbox.x1 - origin_x
    y1 = token.bbox.y1 - origin_y
    if token.rotation % 360 == 90:
        return BBox(y0, page_width - x1, y1, page_width - x0)
    if token.rotation % 360 == 270:
        return BBox(page_height - y1, x0, page_height - y0, x1)
    if token.rotation % 360 == 180:
        return BBox(page_width - x1, page_height - y1, page_width - x0, page_height - y0)
    return BBox(x0, y0, x1, y1)


def _gap_bbox(
    previous: BBox,
    current: BBox,
    rotation: int,
    page_bbox: BBox | None,
    page_width: float,
    page_height: float,
) -> BBox:
    left = previous.x1
    right = current.x0
    if right < left:
        midpoint = (left + right) / 2.0
        left = midpoint
        right = midpoint
    virtual = BBox(left, min(previous.y0, current.y0), right, max(previous.y1, current.y1))
    if rotation % 360 == 90:
        local = BBox(page_width - virtual.y1, virtual.x0, page_width - virtual.y0, virtual.x1)
    elif rotation % 360 == 270:
        local = BBox(virtual.y0, page_height - virtual.x1, virtual.y1, page_height - virtual.x0)
    elif rotation % 360 == 180:
        local = BBox(page_width - virtual.x1, page_height - virtual.y1, page_width - virtual.x0, page_height - virtual.y0)
    else:
        local = virtual
    origin_x = page_bbox.x0 if page_bbox is not None else 0.0
    origin_y = page_bbox.y0 if page_bbox is not None else 0.0
    return BBox(local.x0 + origin_x, local.y0 + origin_y, local.x1 + origin_x, local.y1 + origin_y)


def _line_angle(tokens: list[OcrToken]) -> float:
    if not tokens:
        return 0.0
    rotation = tokens[0].rotation % 360
    return {90: 1.5707963267948966, 180: 3.141592653589793, 270: 4.71238898038469}.get(rotation, 0.0)
