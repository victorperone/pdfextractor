from types import SimpleNamespace

from structured_pdf_text.api import _append_hybrid_ocr_regions
from structured_pdf_text.document import RegionKind
from structured_pdf_text.geometry import BBox


def test_unmatched_ocr_inside_existing_figure_does_not_become_body_supplement():
    figure_bbox = BBox(100, 100, 200, 200)
    token = SimpleNamespace(bbox=BBox(140, 140, 155, 155))
    line = SimpleNamespace(bbox=token.bbox)
    figure = SimpleNamespace(
        region_id="page-1:figure",
        kind=RegionKind.FIGURE,
        bbox=figure_bbox,
        ocr_lines=[line],
        ocr_tokens=[token],
    )
    regions = [figure]

    _append_hybrid_ocr_regions(
        regions=regions,
        tables=[],
        unmatched_lines=[line],
        unmatched_tokens=[token],
        page_index=0,
        page_bbox=BBox(0, 0, 300, 300),
        complexity=None,
    )

    assert regions == [figure]
    assert figure.ocr_lines == [line]
    assert figure.ocr_tokens == [token]
