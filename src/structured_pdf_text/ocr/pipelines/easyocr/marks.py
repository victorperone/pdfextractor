"""Pixel evidence for selection boxes, independent of the text model alphabet."""
from dataclasses import dataclass, replace

from structured_pdf_text.document import OcrProvenance, OcrToken, SourceKind
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.selection_marks import RasterSelectionMarkPolicy


@dataclass(frozen=True, slots=True)
class EasyOCRSelectionMarkPolicy(RasterSelectionMarkPolicy):
    policy_id: str = 'easyocr-selection-marks-v1'


SELECTION_MARK_POLICY = EasyOCRSelectionMarkPolicy()


def _supported_marks(rows):
    """A form pattern, including an empty square, corroborates raster boxes.

    Blurred letters may resemble one marked square near a fragment's start.
    Accept a square only with a separate aligned neighbour, and require an
    empty square in that pair. Entirely filled or isolated boxes abstain.
    """
    boxes = [(index, row[1], BBox(min(p[0] for p in row[0]), min(p[1] for p in row[0]),
                                max(p[0] for p in row[0]), max(p[1] for p in row[0])))
             for index, row in enumerate(rows) if len(row) == 3]
    supported = set()
    for i, state, box in boxes:
        for j, other_state, other in boxes:
            height = max(box.height, other.height)
            if i == j or '☐' not in (state, other_state) or min(box.height, other.height) < .65 * height:
                continue
            column = (abs(box.x0-other.x0) <= .5*height
                      and 1.2*height <= abs(box.cy-other.cy) <= 6*height)
            inline = (abs(box.cy-other.cy) <= .5*height
                      and abs(box.cx-other.cx) >= 1.5*height)
            if column or inline:
                supported.update((i, j))
    return supported


def recover_selection_marks(image, tokens, *, language, allowlist=None, blocklist=None):
    """Add only geometrically proven boxes in the already authorized raster.

    Preserve source tokens in the row's extra field through any option split.
    Native PDF content never enters this backend hook. Rotated observations
    remain untouched: a sideways label has no safe left-of-label search.
    """
    eligible = [t for t in tokens if t.rotation % 360 == 0]
    permitted = set('☐☑☒').difference(blocklist or '')
    if allowlist is not None:
        permitted.intersection_update(allowlist)
    if not eligible or not permitted:
        return list(tokens)
    rows = [([[t.bbox.x0, t.bbox.y0], [t.bbox.x1, t.bbox.y0],
              [t.bbox.x1, t.bbox.y1], [t.bbox.x0, t.bbox.y1]], t.text, t.confidence, t) for t in eligible]
    recovered = SELECTION_MARK_POLICY.apply(image, rows)
    supported = _supported_marks(recovered)
    # Applying the mechanism with no new mark is an exact no-op, including
    # ordering and polygons. Only changed label rows get new geometry.
    if not any(index in supported and row[1] in permitted for index, row in enumerate(recovered)):
        return list(tokens)
    result = []
    restored = set()
    accepted_boxes = [row[0] for index, row in enumerate(recovered) if index in supported]
    for index, row in enumerate(recovered):
        points, text, confidence = row[:3]
        if len(row) == 3 and (index not in supported or text not in permitted):
            continue
        box = BBox(min(p[0] for p in points), min(p[1] for p in points),
                   max(p[0] for p in points), max(p[1] for p in points))
        if len(row) > 3:
            source = row[3]
            # If no supported mark belongs to this source row, restore its
            # exact original geometry/text, once even after an inner split.
            if not any(abs(sum(p[1] for p in mark)/4-source.bbox.cy) <= source.bbox.height
                       and source.bbox.x0-2.5*source.bbox.height <= mark[0][0] <= source.bbox.x1
                       for mark in accepted_boxes):
                if id(source) not in restored:
                    result.append(source)
                    restored.add(id(source))
                continue
            result.append(source if source.text == text and source.bbox == box else
                          replace(source, text=text, bbox=box, polygon=None))
        else:
            result.append(OcrToken(text, box, confidence, language, SourceKind.OCR_PAGE,
                provenance='raster_selection_mark', ocr_provenance=OcrProvenance(
                    'easyocr', candidate_id='selection_marks', recognizer='raster_geometry',
                    refinement_kind='raster_selection_mark')))
    result.extend(t for t in tokens if t.rotation % 360 != 0)
    return result
