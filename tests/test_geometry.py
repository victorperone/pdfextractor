from structured_pdf_text.geometry import BBox


def test_pdfium_rect_conversion_to_top_left_coordinates():
    bbox = BBox.from_pdfium_rect(20, 150, 40, 160, page_height=200)
    assert bbox == BBox(20, 40, 40, 50)
    assert bbox.to_pdfium_rect(page_height=200) == (20, 150, 40, 160)


def test_overlap_and_iou():
    a = BBox(0, 0, 10, 10)
    b = BBox(5, 5, 15, 15)
    assert a.overlap_ratio(b) == 0.25
    assert round(a.iou(b), 4) == 0.1429
