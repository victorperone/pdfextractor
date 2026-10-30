"""Order rotated EasyOCR prose using a temporary upright geometry view."""
from dataclasses import replace


def prepare_layout_lines(lines, page_bbox):
    """Infer spacing at proven segmentation boundaries before column ordering.

    CRAFT boxes may overlap slightly across a printed comma. A replacement
    with the observed coherent tail must keep a word boundary there. This is
    layout whitespace, explicitly flagged as inferred; OCR text, source boxes
    and confidence stay intact. All unmarked boundaries keep legacy behavior.
    """
    from structured_pdf_text.document import TextToken, TokenFlag
    from structured_pdf_text.geometry import BBox

    def reading_box(token):
        box = token.bbox
        if token.rotation % 360 == 90:
            return BBox(box.y0,-box.x1,box.y1,-box.x0)
        if token.rotation % 360 == 180:
            return BBox(-box.x1,-box.y1,-box.x0,-box.y0)
        if token.rotation % 360 == 270:
            return BBox(-box.y1,box.x0,-box.y0,box.x1)
        return box

    output = []
    for line in lines:
        if line.text_override is not None:
            output.append(line)
            continue
        tokens = []
        for current in line.tokens:
            previous = tokens[-1] if tokens else None
            origin = current.ocr_provenance
            prior = previous.ocr_provenance if previous is not None else None
            if (previous is not None and origin is not None and prior is not None
                    and origin.engine == prior.engine == 'easyocr'
                    and origin.refinement_kind == 'coherent_boundary_tail'
                    and origin.candidate_id == prior.candidate_id
                    and previous.rotation % 360 == current.rotation % 360
                    and previous.text.endswith((',', ';', ':'))
                    and current.text[:1].isalpha()
                    and 0 < reading_box(previous).x1-reading_box(current).x0 <= .25 * min(reading_box(previous).height,reading_box(current).height)):
                box = current.bbox
                gap = (BBox(box.x0,box.y0,box.x1,box.y0) if current.rotation % 180 else
                       BBox(box.x0,box.y0,box.x0,box.y1))
                tokens.append(TextToken(' ',gap,[],.60,' ',rotation=current.rotation,
                                        flags={TokenFlag.WHITESPACE_INFERRED},
                                        provenance='easyocr_coherent_boundary_gap',
                                        transformation_type='inferred_whitespace',
                                        decision_reason='observed coherent prose tail after punctuation'))
            tokens.append(current)
        output.append(replace(line,tokens=tokens) if len(tokens)!=len(line.tokens) else line)
    return order_rotated_columns(output,page_bbox)


def order_rotated_columns(lines, page_bbox):
    """Keep all source lines/coordinates; reorder only two proven prose lanes."""
    from structured_pdf_text.geometry import BBox
    from structured_pdf_text.layout.ocr_aware import _order_ocr_lines_by_columns
    visible = [token for line in lines for token in line.tokens if token.text.strip()]
    rotations = {token.rotation % 360 for token in visible}
    if len(rotations) != 1 or next(iter(rotations)) not in (90, 180, 270):
        return _order_ocr_lines_by_columns(lines, page_bbox)
    angle = (-next(iter(rotations))) % 360
    width, height = page_bbox.width, page_bbox.height

    def upright(box):
        local = BBox(box.x0-page_bbox.x0, box.y0-page_bbox.y0,
                     box.x1-page_bbox.x0, box.y1-page_bbox.y0)
        return local.rotate_to_visual(angle, width, height)

    view = [replace(line, bbox=upright(line.bbox), tokens=[
        replace(token, bbox=upright(token.bbox), rotation=0) for token in line.tokens]) for line in lines]
    prose = [line for line in view if len(line.text.split()) >= 3
             and sum(char.isalpha() for char in line.text) >= 24]
    if len(prose) < 6:
        return _order_ocr_lines_by_columns(lines, page_bbox)
    extent = BBox.union_all([line.bbox for line in prose])
    anchored = [line for line in prose if line.bbox.width <= .65 * extent.width]
    starts = sorted(line.bbox.x0 for line in anchored)
    gaps = [(right-left, (right+left)/2) for left,right in zip(starts,starts[1:])]
    if not gaps or max(gaps)[0] < .20 * extent.width:
        return _order_ocr_lines_by_columns(lines, page_bbox)
    split = max(gaps)[1]
    left = [line for line in anchored if line.bbox.x0 < split]
    right = [line for line in anchored if line.bbox.x0 >= split]
    if min(len(left),len(right)) < 3:
        return _order_ocr_lines_by_columns(lines, page_bbox)
    left_box, right_box = (BBox.union_all([line.bbox for line in group]) for group in (left,right))
    overlap = min(left_box.y1,right_box.y1)-max(left_box.y0,right_box.y0)
    if overlap < .5 * min(left_box.height,right_box.height):
        return _order_ocr_lines_by_columns(lines, page_bbox)
    original = {id(copy): line for copy,line in zip(view,lines)}
    return [original[id(line)] for line in _order_ocr_lines_by_columns(view, extent)]
