"""Composed raster crops retain their frame; no OCR runtime is required."""
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from structured_pdf_text.geometry import BBox
from structured_pdf_text.tables.visual import (
    VisualGrid, _image_size, detect_visual_table, detect_visual_table_in_region,
)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("origin", [(0, 0), (17, 23)])
@pytest.mark.parametrize("fractional", [False, True])
def test_region_crop_maps_grid_and_separator_pixels_with_actual_frame(rotation, origin, fractional):
    ox, oy = origin
    delta = .25 if fractional else 0
    region = BBox(ox + 25 + delta, oy + 380 + delta, ox + 370 - delta, oy + 500 - delta)
    page = SimpleNamespace(
        page_index=0, bbox=BBox(ox, oy, ox + 400, oy + 600), extracted_text="",
        objects=SimpleNamespace(rotation=rotation, images=[SimpleNamespace(bbox=region)]),
    )
    canonical = Image.new("RGB", (400, 600), "red")
    draw = ImageDraw.Draw(canonical)
    draw.rectangle((25, 380, 369, 499), fill="white")
    for x in (25, 140, 255, 369):
        draw.line((x, 380, x, 499), fill="black", width=2)
    for y in (380, 420, 460, 499):
        draw.line((25, y, 369, y), fill="black", width=2)
    operation = {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180,
                 270: Image.Transpose.ROTATE_90}
    image = canonical.transpose(operation[rotation]) if rotation else canonical

    class Grid:
        def recognize(self, crop):
            assert _image_size(crop) == (345, 120)
            # A red background would expose a crop in the wrong presentation
            # coordinates. Pixel rounding must not be silently rescaled away.
            pixels = crop if hasattr(crop, "getpixel") else Image.fromarray(crop)
            try:
                assert pixels.getpixel((60, 20)) == (255, 255, 255)
                assert pixels.getpixel((115, 20)) == (0, 0, 0)
            finally:
                if pixels is not crop:
                    pixels.close()
            return VisualGrid((0., 115., 230., 344.), (0., 40., 80., 119.), .95)

    try:
        table = detect_visual_table_in_region(page, image, region, structure_engine=Grid())
        assert table.page_fragments[0].bbox == BBox(ox + 25, oy + 380, ox + 369, oy + 499)
        assert (table.column_count, table.row_count) == (3, 3)
        assert len(table.cells) == 9
        assert all(cell.rowspan == cell.colspan == 1 for cell in table.cells)
        assert table.cells[4].bbox == BBox(ox + 140, oy + 420, ox + 255, oy + 460)
        image.getpixel((0, 0))  # The caller still owns the page raster.
    finally:
        if image is not canonical:
            image.close()
        canonical.close()


@pytest.mark.parametrize("raise_in_engine", [False, True])
def test_owned_pillow_crop_is_closed_on_success_and_detection_failure(raise_in_engine):
    page = SimpleNamespace(page_index=0, bbox=BBox(0, 0, 400, 600), extracted_text="",
                           objects=SimpleNamespace(rotation=0, images=[]))
    seen = []

    class Grid:
        def recognize(self, crop):
            seen.append(crop)
            if raise_in_engine:
                raise RuntimeError("grid failure")
            return None

    with Image.new("RGB", (400, 600), "white") as image:
        if raise_in_engine:
            with pytest.raises(RuntimeError, match="grid failure"):
                detect_visual_table_in_region(page, image, BBox(25, 380, 370, 500), structure_engine=Grid())
        else:
            assert detect_visual_table_in_region(page, image, BBox(25, 380, 370, 500), structure_engine=Grid()) is None
        with pytest.raises(ValueError, match="closed"):
            seen[0].getpixel((0, 0))
        assert image.getpixel((0, 0)) == (255, 255, 255)


@pytest.mark.parametrize("region", [BBox(0, 0, 0, 1), BBox(-1, 0, 1, 1),
                                   BBox(0, 0, float("inf"), 1), BBox(float("nan"), 0, 1, 1)])
def test_invalid_frame_rejected_before_grid_discovery(region):
    page = SimpleNamespace(bbox=BBox(0, 0, 400, 600))
    class Forbidden:
        def recognize(self, _image):
            pytest.fail("invalid geometry must fail before detection")
    with pytest.raises(ValueError, match="finite"):
        detect_visual_table_in_region(page, object(), region, structure_engine=Forbidden())
    with pytest.raises(ValueError, match="finite"):
        detect_visual_table(page, object(), structure_engine=Forbidden(), image_bbox=region)


def test_table_line_rebuild_preserves_transitive_source_ids():
    from structured_pdf_text.document import TextToken, TextLine, WritingDirection
    from structured_pdf_text.tables.ocr_lines import _rebuild_table_ocr_lines
    from structured_pdf_text.document import StructuredTable, TableCell, TableFragment, TableMethod

    bbox = BBox(10, 10, 90, 30)
    token = TextToken("Valor -42,90", bbox, [], .99, "Valor -42,90", evidence_id="authorized:token")
    line = TextLine([token], bbox, None, WritingDirection.LEFT_TO_RIGHT, None, None,
                    line_id="ocr-original", merged_source_line_ids=("ocr-parent",))
    table = StructuredTable("raster", [TableFragment(0, bbox, 0, 0)],
                            [TableCell(0, 0, 1, 1, bbox, token.text, [token], .99)],
                            1, 1, .99, TableMethod.VISUAL_MODEL)
    first = _rebuild_table_ocr_lines(table, [line], 0)
    second = _rebuild_table_ocr_lines(table, first, 0)
    assert second[0].line_id == first[0].line_id
    assert second[0].merged_source_line_ids == ("ocr-parent", "ocr-original")
    assert second[0].tokens[0].evidence_id == "authorized:token"
