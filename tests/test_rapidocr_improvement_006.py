"""Remaining table regressions from the 004 audit, after the inherited 005."""
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from structured_pdf_text.api import _table_structure_tokens
from structured_pdf_text.document import (
    LayoutRegion, OcrToken, RegionDecision, RegionKind, RegionQuality, SourceKind,
    StructuredTable, TableCell, TableFragment, TableMethod,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.tables.ocr_tracks import detect_ocr_track_tables
from structured_pdf_text.tables.visual import detect_visual_table
from test_visual_grid_recovery import _financial_grid, _page, _box_transform


def _region(tokens, box, region_id="accepted-figure"):
    return LayoutRegion(region_id, RegionKind.FIGURE, box, .99, [], tokens,
                        RegionQuality(RegionDecision.OCR_REGION))


def _ocr(text, x, y, identity):
    return OcrToken(text, BBox(x, y, x+max(10, len(text)*6), y+10),
                    .99, "pt-BR", SourceKind.OCR_REGION, evidence_id=identity)


def test_regional_ocr_establishes_table_structure_on_a_native_heavy_page():
    pytest.importorskip("cv2")
    image, original = _financial_grid()
    page = _page(image)
    page.extracted_text = "Native prose remains authoritative. " * 15
    tokens = [OcrToken(t.text, t.bbox, t.confidence, "pt-BR", SourceKind.OCR_REGION, evidence_id=t.evidence_id)
              for t in original]
    region = _region(tokens, page.bbox)
    observations = _table_structure_tokens([], [region], 0, page.bbox)
    table = detect_visual_table(page, image, tokens=observations)
    assert table is not None
    assert next(c.text for c in table.cells if (c.row, c.col) == (4, 4)) == "R$ 101.000,00"
    # A second regional view of the same observations must not duplicate them.
    twice = _table_structure_tokens([], [region, region], 0, page.bbox)
    assert [t.evidence_id for t in twice if t.text.strip()] == [t.evidence_id for t in observations if t.text.strip()]


def test_table_structure_uses_final_regional_tokens_instead_of_stale_lines():
    from structured_pdf_text.ocr.reconstruct import reconstruct_ocr_lines

    page, image, region, _ = _track_fixture()
    region.ocr_lines = reconstruct_ocr_lines(region.ocr_tokens, 0, page.bbox)
    region.ocr_tokens = [replace(t, text="FINAL", evidence_id="final:"+t.evidence_id) for t in region.ocr_tokens]
    tokens = _table_structure_tokens([], [region], 0, page.bbox)
    assert all(t.text == "FINAL" for t in tokens if t.text.strip())
    region.ocr_tokens = []
    assert _table_structure_tokens([], [region], 0, page.bbox) == []


def _track_fixture():
    image = Image.new("RGB", (500, 300), "white")
    page = _page(image)
    rows = [("Record", "Category", "Quantity", "Status"),
            ("DOC-001", "Service", "02", "Approved"),
            ("DOC-002", "Material", "04", "Pending"),
            ("DOC-003", "Service", "01", "Review")]
    tokens = [_ocr(text, 20+column*120, 60+row*25, f"r{row}c{column}")
              for row, cells in enumerate(rows) for column, text in enumerate(cells)]
    return page, image, _region(tokens, page.bbox), rows


def test_borderless_ocr_uses_alignment_without_new_recognition():
    page, image, region, rows = _track_fixture()
    original = [(t.text, t.bbox, t.evidence_id) for t in region.ocr_tokens]
    tables = detect_ocr_track_tables(page, [region], image, [])
    assert len(tables) == 1
    assert (tables[0].row_count, tables[0].column_count) == (4, 4)
    assert [c.text for c in tables[0].cells] == [text for row in rows for text in row]
    assert [(t.text, t.bbox, t.evidence_id) for t in region.ocr_tokens] == original
    assert detect_ocr_track_tables(page, [region], image, tables) == []


def test_borderless_candidate_containing_an_existing_grid_cannot_duplicate_it():
    page, image, region, _ = _track_fixture()
    # A large candidate including a distant footer contains a small real grid.
    # Candidate-relative overlap alone would miss this containment direction.
    claimed = StructuredTable("small-grid", [TableFragment(0, BBox(15, 55, 220, 95), 0, 1)],
                              [], 2, 2, .99, TableMethod.VISUAL_MODEL)
    assert detect_ocr_track_tables(page, [region], image, [claimed]) == []


def test_hybrid_grid_without_a_table_region_claims_ocr_before_conservation():
    from structured_pdf_text.assemble.content import assemble_page_content
    from structured_pdf_text.assemble.conservation import record_content_conservation
    from structured_pdf_text.document import ContentKind, StructuredPage
    from structured_pdf_text.ocr.reconstruct import reconstruct_ocr_lines

    native, image, region, _ = _track_fixture()
    region.ocr_lines = reconstruct_ocr_lines(region.ocr_tokens, 0, native.bbox)
    tables = detect_ocr_track_tables(native, [region], image, [])
    tables[0].method = TableMethod.STRICT_GRID  # Vector rules, wholly raster text.
    page = StructuredPage(0, native.bbox, [region], tables, "", "", None)
    result = assemble_page_content(page)
    blocks, accounting, _ = record_content_conservation(page, list(result.blocks), result.canonical_line_order)
    assert [b.kind for b in blocks] == [ContentKind.TABLE]
    assert accounting.fallback_lines == 0
    assert accounting.table_owned_lines == len(region.ocr_lines)


def test_detached_native_row_labels_rejoin_only_their_empty_aligned_cells():
    from structured_pdf_text.document import TextLine, TextToken, WritingDirection
    from structured_pdf_text.tables.text_tracks import complete_borderless_cells

    def token(text, x, y):
        return TextToken(text, BBox(x, y, x+len(text)*4, y+6), [], .99, text)

    cells = []
    for row in range(3):
        for col in range(3):
            tokens = [] if row == 1 and col == 0 else [token(f"Row{row}" if col == 0 else "12", 20+100*col, 60+row*25)]
            cells.append(TableCell(row, col, 1, 1, BBox(20+100*col, 55+row*25, 120+100*col, 80+row*25),
                                   " ".join(t.text for t in tokens), tokens, .99))
    table = StructuredTable("tracks", [TableFragment(0, BBox(20, 55, 320, 130), 0, 2)],
                            cells, 3, 3, .99, TableMethod.TEXT_TRACKS)
    observation = token("Detached label", 20, 85)
    line = TextLine([observation], observation.bbox, None, WritingDirection.LEFT_TO_RIGHT, None, None)
    region = replace(_region([], line.bbox), kind=RegionKind.TEXT, native_lines=[line])
    complete_borderless_cells([table], [region])
    assert cells[3].text == "Detached label"
    assert cells[3].tokens == [observation]
    assert region.native_lines == [line]
    # Neither a different baseline nor an occupied cell may be overwritten.
    cells[3].text, cells[3].tokens = "", []
    observation.bbox = BBox(20, 94, 76, 100)
    line.bbox = observation.bbox
    complete_borderless_cells([table], [region])
    assert cells[3].text == ""


def test_native_italic_evidence_reaches_prose_markdown_without_native_dump(tmp_path):
    from test_rapidocr_improvement_005 import _pdf, _extract

    def draw(pdf):
        pdf.setFont("Helvetica-Oblique", 11)
        pdf.drawString(48, 660, "An italic paragraph with its original emphasis.")
        pdf.setFont("Helvetica", 11)
        pdf.drawString(48, 610, "A regular paragraph with ordinary typography.")

    document, markdown = _extract(_pdf(tmp_path, "italic-prose", draw))
    assert "*An italic paragraph with its original emphasis.*" in markdown
    assert "*A regular paragraph" not in markdown


@pytest.mark.parametrize("glyph", ["O", "1"])
def test_recurrent_graphic_glyphs_do_not_become_a_borderless_table(glyph):
    page, image, region, _ = _track_fixture()
    region.ocr_tokens = [replace(t, text=glyph) for t in region.ocr_tokens]
    assert detect_ocr_track_tables(page, [region], image, []) == []


def test_ocr_tracks_cannot_establish_structure_outside_an_image_scope():
    page, image, region, _ = _track_fixture()
    page.objects.images = [SimpleNamespace(bbox=BBox(10, 10, 100, 40))]
    assert detect_ocr_track_tables(page, [region], image, []) == []


@pytest.mark.parametrize("kind", [RegionKind.FIGURE, RegionKind.TABLE])
def test_native_rules_accept_ocr_for_all_empty_raster_cells(kind):
    from structured_pdf_text.fusion.integrator import merge_ocr_region_tokens_into_table_cells
    from structured_pdf_text.tables.text_join import join_table_tokens

    cells = [TableCell(row, col, 1, 1, BBox(10+100*col, 10+30*row, 110+100*col, 40+30*row),
                       "", [], .5) for row in range(3) for col in range(3)]
    table = StructuredTable("vector-rules", [TableFragment(0, BBox(10, 10, 310, 100), 0, 2)],
                            cells, 3, 3, .9, TableMethod.STRICT_GRID)
    tokens = [_ocr(f"V{row}{col}", 15+100*col, 15+30*row, f"r{row}c{col}")
              for row in range(3) for col in range(3)]
    region = _region(tokens, BBox(10, 10, 310, 100))
    region.kind = kind
    consumed = merge_ocr_region_tokens_into_table_cells(tables=[table], regions=[region], page_index=0,
                                                       join_tokens=join_table_tokens)
    assert consumed == {id(t) for t in tokens}
    assert [c.text for c in cells] == [t.text for t in tokens]
    assert [t.evidence_id for c in cells for t in c.tokens] == [t.evidence_id for t in tokens]


def test_a_row_wide_ocr_box_is_not_inventively_split_between_native_cells():
    from structured_pdf_text.fusion.integrator import merge_ocr_region_tokens_into_table_cells
    from structured_pdf_text.tables.text_join import join_table_tokens

    cells = [TableCell(0, col, 1, 1, BBox(col*100, 0, (col+1)*100, 30), "", [], .5)
             for col in range(2)]
    table = StructuredTable("rules", [TableFragment(0, BBox(0, 0, 200, 30), 0, 0)],
                            cells, 2, 1, .9, TableMethod.STRICT_GRID)
    observation = replace(_ocr("Left and right", 5, 5, "wide"), bbox=BBox(5, 5, 195, 20))
    consumed = merge_ocr_region_tokens_into_table_cells(
        tables=[table], regions=[_region([observation], BBox(0, 0, 200, 30))], page_index=0,
        join_tokens=join_table_tokens,
    )
    assert not consumed and not any(c.tokens or c.text for c in cells)


def test_noisy_header_background_does_not_invent_missing_separators():
    pytest.importorskip("cv2")
    image, tokens = _financial_grid()
    array = np.asarray(image).copy()
    rng = np.random.default_rng(713)
    header = array[20:100, 20:800]
    background = np.clip(238+rng.normal(0, 4, header.shape), 0, 255).astype(np.uint8)
    header[:] = np.where(header > 245, background, header)
    table = detect_visual_table(_page(array), array, tokens=tokens)
    assert table is not None
    cells = {(c.row, c.col): c for c in table.cells}
    assert cells[0, 0].rowspan == 2
    assert cells[0, 1].colspan == cells[0, 3].colspan == 2
    assert len(table.cells) == 22
    assert table.header_rows == (0, 1)


def test_unshaded_table_does_not_acquire_an_invented_header():
    pytest.importorskip("cv2")
    image, tokens = _financial_grid()
    assert detect_visual_table(_page(image), image, tokens=tokens).header_rows == ()


def test_shading_transition_is_not_noise_that_erases_a_real_header_rule():
    from PIL import ImageDraw

    pytest.importorskip("cv2")
    image = Image.new("RGB", (400, 200), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 380, 90), fill=(235, 235, 235))
    for x in (20, 200, 380):
        draw.line((x, 20, x, 180), fill=(210, 210, 210), width=2)
    for y in (20, 90, 180):
        draw.line((20, y, 380, y), fill=(210, 210, 210), width=2)
    tokens = [_ocr(text, x, y, text) for text, x, y in (
        ("Record", 40, 45), ("Value", 220, 45), ("AB-001", 40, 120), ("123.00", 220, 120))]
    table = detect_visual_table(_page(image), image, tokens=tokens)
    assert table is not None
    assert len(table.cells) == 4
    assert all(c.rowspan == c.colspan == 1 for c in table.cells)
    assert [c.text for c in table.cells] == [t.text for t in tokens]


def test_faint_sloping_rules_preserve_every_financial_row():
    cv2 = pytest.importorskip("cv2")
    image, tokens = _financial_grid()
    array = np.asarray(image).copy()
    array[array[..., 0] < 240] = 220
    matrix = (1., .03, 5., -.029, 1., 32.)
    array = cv2.warpAffine(array, np.asarray(matrix).reshape(2, 3), (850, 320),
                           borderValue=(255, 255, 255))
    for token in tokens:
        token.bbox = _box_transform(token.bbox, matrix)
    table = detect_visual_table(_page(array), array, tokens=tokens)
    assert table is not None
    assert (table.row_count, table.column_count) == (5, 5)
    assert next(c.text for c in table.cells if (c.row, c.col) == (4, 4)) == "R$ 101.000,00"


@pytest.mark.parametrize("page_scale", [1., .5])
def test_detected_rules_survive_low_local_snr_without_filling_real_header_gaps(page_scale):
    from structured_pdf_text.tables.visual import VisualGrid

    pytest.importorskip("cv2")
    image, tokens = _financial_grid()
    array = np.asarray(image).astype(float)
    array = np.where(array < 128, 200., 240.)
    array = np.clip(array+np.random.default_rng(309).normal(0, 20, array.shape), 0, 255).astype(np.uint8)
    xs, ys = (20, 180, 340, 500, 660, 800), (20, 60, 100, 145, 190, 240)
    # A structure engine has detected these physical strokes. Their missing
    # header segments are deliberately absent, even though body rules recur.
    horizontal = tuple((180 if i == 1 else 20, y, 620 if i == 1 else 780, 3) for i, y in enumerate(ys))
    vertical = tuple((x, 60 if i in (2, 4) else 20, 3, 180 if i in (2, 4) else 220)
                     for i, x in enumerate(xs))

    class ObservedGrid:
        def recognize(self, raster):
            return VisualGrid(xs, ys, .95, horizontal, vertical)

    page = _page(array)
    page.bbox = BBox(0, 0, 820*page_scale, 270*page_scale)
    page.objects.images[0].bbox = page.bbox
    for token in tokens:
        token.bbox = _box_transform(token.bbox, (page_scale, 0, 0, 0, page_scale, 0))
    table = detect_visual_table(page, array, tokens=tokens, structure_engine=ObservedGrid())
    assert table is not None
    assert (table.row_count, table.column_count, len(table.cells)) == (5, 5, 22)
    cells = {(c.row, c.col): c for c in table.cells}
    assert cells[0, 0].rowspan == 2
    assert cells[0, 1].colspan == cells[0, 3].colspan == 2
    assert [cells[r, 4].text for r in (2, 3, 4)] == ["R$ 61.000,00", "R$ 40.000,00", "R$ 101.000,00"]


@pytest.mark.parametrize("text,is_table", [("CELL-ONLY-042", True),
                                           ("This is a boxed paragraph, not tabular content.", False),
                                           ("PROTOCOLO=ABC_042", False)])
def test_single_cell_identifier_frame_does_not_promote_callouts(tmp_path, text, is_table):
    from test_rapidocr_improvement_005 import _pdf, _extract

    def draw(pdf):
        for y in (680, 705):
            pdf.line(44, y, 551, y)
        for x in (44, 551):
            pdf.line(x, 680, x, 705)
        pdf.drawString(50, 688, text)

    document, markdown = _extract(_pdf(tmp_path, "scalar-frame", draw))
    tables = document.pages[0].tables
    assert bool(tables) is is_table
    if is_table:
        assert [(t.row_count, t.column_count) for t in tables] == [(1, 1)]
        assert tables[0].cells[0].text == text
    assert text.replace("_", "\\_") in markdown or text in markdown
