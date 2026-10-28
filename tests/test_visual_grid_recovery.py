"""Recover financial cell ownership from actual composed pixels, without OCR."""
from types import SimpleNamespace
from collections import Counter

import numpy as np
import pytest
from PIL import Image, ImageDraw

from structured_pdf_text.document import TextToken
from structured_pdf_text.geometry import BBox
from structured_pdf_text.tables.visual import detect_visual_table, detect_visual_grid, _build_visual_cells
from structured_pdf_text.tables.visual_alignment import point, quarter_turn_to_source, align_ruling_lines
from structured_pdf_text.tables.text_join import join_table_tokens


def _box_transform(box, matrix):
    corners=[point(matrix,x,y)for x,y in ((box.x0,box.y0),(box.x1,box.y0),(box.x1,box.y1),(box.x0,box.y1))]
    return BBox(min(p[0]for p in corners),min(p[1]for p in corners),max(p[0]for p in corners),max(p[1]for p in corners))


def _financial_grid():
    image=Image.new('RGB',(820,270),'white');draw=ImageDraw.Draw(image)
    xs=[20,180,340,500,660,800];ys=[20,60,100,145,190,240]
    for i,x in enumerate(xs):
        draw.line((x,60 if i in (2,4)else 20,x,240),fill='black',width=3)
    for i,y in enumerate(ys):draw.line((180 if i==1 else 20,y,800,y),fill='black',width=3)
    content={(0,0):(1,2,'Unidade'),(0,1):(2,1,'Semestre I'),(0,3):(2,1,'Semestre II')}
    for c,text in enumerate(('Quantidade','Valor','Quantidade','Valor'),1):content[(1,c)]=(1,1,text)
    for r,labels in enumerate((('Norte','6','R$ 48.000,00','8','R$ 61.000,00'),('Sul','4','R$ 30.000,00','5','R$ 40.000,00'),('Total','10','R$ 78.000,00','13','R$ 101.000,00')),2):
        for c,text in enumerate(labels):content[(r,c)]=(1,1,text)
    tokens=[]
    for (r,c),(cs,rs,text) in content.items():
        x,y=xs[c]+9,ys[r]+12;draw.text((x,y),text,fill='black')
        box=BBox(x,y,x+min(len(text)*6, xs[c+cs]-x-6),y+12)
        token=TextToken(text,box,[],.99,text,evidence_id=f'r{r}c{c}');tokens.append(token)
    return image,tokens


def _page(image):
    width,height=image.size if hasattr(image,'size')and isinstance(image.size,tuple)else(image.shape[1],image.shape[0])
    bbox=BBox(0,0,width,height)
    return SimpleNamespace(page_index=0,bbox=bbox,extracted_text='',objects=SimpleNamespace(rotation=0,images=[SimpleNamespace(bbox=bbox)]))


@pytest.mark.parametrize('shear', [False, True])
def test_raster_grid_preserves_merged_headers_and_six_amounts(shear):
    cv2=pytest.importorskip('cv2');image,tokens=_financial_grid()
    if shear:
        matrix=(1.,.018,5.,-.028,1.,35.)
        image=cv2.warpAffine(np.asarray(image),np.asarray(matrix).reshape(2,3),(850,310),borderValue=(255,255,255))
        for token in tokens:token.bbox=_box_transform(token.bbox,matrix)
    table=detect_visual_table(_page(image),image,tokens=tokens)
    assert table is not None
    assert (table.row_count,table.column_count)==(5,5)
    bypos={(c.row,c.col):c for c in table.cells}
    assert (bypos[(0,0)].rowspan,bypos[(0,0)].colspan)==(2,1)
    assert bypos[(0,1)].colspan==bypos[(0,3)].colspan==2
    assert [bypos[(r,c)].text for r in (2,3,4)for c in (2,4)]==['R$ 48.000,00','R$ 61.000,00','R$ 30.000,00','R$ 40.000,00','R$ 78.000,00','R$ 101.000,00']
    assert Counter(id(t)for cell in table.cells for t in cell.tokens)==Counter(map(id,tokens))
    covered=[(r,c)for cell in table.cells for r in range(cell.row,cell.row+cell.rowspan)for c in range(cell.col,cell.col+cell.colspan)]
    assert len(covered)==len(set(covered))==25


@pytest.mark.parametrize('turns',[1,2,3])
def test_recognized_orientation_orders_grid_without_moving_source_tokens(turns):
    pytest.importorskip('cv2');upright,tokens=_financial_grid();image=np.ascontiguousarray(np.rot90(np.asarray(upright),-turns));h,w=image.shape[:2]
    matrix=quarter_turn_to_source(turns,w,h)
    for token in tokens:token.bbox=_box_transform(token.bbox,matrix);token.rotation=turns*90
    original={id(t):t.bbox for t in tokens};table=detect_visual_table(_page(image),image,tokens=tokens)
    assert table is not None
    cells={(c.row,c.col):c for c in table.cells}
    assert cells[(2,0)].text=='Norte'
    assert cells[(4,4)].text=='R$ 101.000,00'
    assert all(t.bbox==original[id(t)]for t in tokens)


def test_shared_boundary_observation_has_one_owner_and_empty_is_not_zero():
    image=Image.new('RGB',(300,150),'white');draw=ImageDraw.Draw(image)
    for x in(0,100,200,299):draw.line((x,0,x,149),fill='black',width=2)
    for y in(0,50,100,149):draw.line((0,y,299,y),fill='black',width=2)
    tokens=[TextToken('R$ 0,00',BBox(90,60,110,80),[],.99,'R$ 0,00')]
    page=_page(image);cells=_build_visual_cells(page,image,(0,100,200,299),(0,50,100,149),tokens)
    assert sum(len(c.tokens)for c in cells)==1
    assert next(c for c in cells if(c.row,c.col)==(1,0)).text==''
    assert next(c for c in cells if(c.row,c.col)==(1,1)).text=='R$ 0,00'


def test_word_boxes_in_multiline_cell_keep_lines_and_decimal_suffix():
    tokens=[TextToken(text,BBox(x,y,x+width,y+10),[],.99,text)for text,x,y,width in [('Segunda',5,30,45),('linha',55,30,25),('R$ 1.234,56',10,10,65),('líquido',85,10,40)]]
    assert join_table_tokens(tokens)=='R$ 1.234,56 líquido Segunda linha'


def test_plot_axes_and_geometric_drawing_do_not_become_grids():
    image=Image.new('RGB',(400,300),'white');draw=ImageDraw.Draw(image)
    draw.line((30,30,30,260,370,260),fill='black',width=3)
    for xy in[(60,80,150,170),(220,90,310,180)]:draw.ellipse(xy,outline='black',width=3)
    assert detect_visual_grid(image) is None


def test_compact_light_gray_table_is_found_inside_a_large_scan():
    pytest.importorskip('cv2')
    source, tokens = _financial_grid()
    small = np.asarray(source.resize((410, 135))).copy()
    small[small[..., 0] < 240] = 220
    image = Image.new('RGB', (1200, 1600), 'white')
    image.paste(Image.fromarray(small), (100, 300))
    for token in tokens:
        b = token.bbox
        token.bbox = BBox(100+b.x0*.5, 300+b.y0*.5, 100+b.x1*.5, 300+b.y1*.5)
    table = detect_visual_table(_page(image), image, tokens=tokens)
    assert table is not None
    assert (table.row_count, table.column_count) == (5, 5)
    assert next(c for c in table.cells if (c.row, c.col) == (4, 4)).text == 'R$ 101.000,00'


def test_skewed_neighbor_cells_validate_but_crossing_polygons_do_not():
    from dataclasses import replace
    from structured_pdf_text.document import TableCell
    from structured_pdf_text.geometry import Point
    from structured_pdf_text.tables.cell_geometry import contains_point, overlaps, valid_polygon

    left = TableCell(0, 0, 1, 1, BBox(0, 0, 12, 10), '', [], .9,
                     polygon=(Point(0, 0), Point(10, 0), Point(12, 10), Point(2, 10)))
    right = TableCell(0, 1, 1, 1, BBox(10, 0, 22, 10), '', [], .9,
                      polygon=(Point(10, 0), Point(20, 0), Point(22, 10), Point(12, 10)))
    assert left.bbox.intersection(right.bbox) is not None
    assert valid_polygon(left) and valid_polygon(right)
    assert not overlaps(left, right)
    assert contains_point(left, 11, 9) and not contains_point(right, 11, 9)
    assert overlaps(left, replace(right, bbox=left.bbox, polygon=left.polygon))
    crossed = replace(left, polygon=(Point(0, 0), Point(12, 10), Point(10, 0), Point(2, 10)))
    assert not valid_polygon(crossed)
    assert not valid_polygon(replace(left, bbox=BBox(0, 0, 10, 10)))


def test_cell_snapshot_detects_a_changed_polygon():
    from dataclasses import replace
    from structured_pdf_text.document import TableCell
    from structured_pdf_text.geometry import Point
    from structured_pdf_text.fusion.integrator import cell_evidence_snapshot

    cell = TableCell(0, 0, 1, 1, BBox(0, 0, 12, 10), '', [], .9,
                     polygon=(Point(0, 0), Point(10, 0), Point(12, 10), Point(2, 10)))
    assert cell_evidence_snapshot(cell) != cell_evidence_snapshot(replace(cell, polygon=None))


@pytest.mark.parametrize('turns', [1, 2, 3])
def test_raster_warp_respects_pixel_centres_on_quarter_turns(turns):
    pytest.importorskip('cv2')
    from structured_pdf_text.tables.visual_alignment import warp
    source = np.arange(35, dtype=np.uint8).reshape(5, 7)
    expected = np.rot90(source, turns)
    actual = warp(source, quarter_turn_to_source(turns, 7, 5),
                  (expected.shape[1], expected.shape[0]))
    np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize('turns', [1, 2, 3])
def test_rotated_raster_table_passes_geometry_checks_with_source_lines(turns):
    pytest.importorskip('cv2')
    from structured_pdf_text.document import Baseline, TextLine, WritingDirection
    from structured_pdf_text.tables.validation import validate_table_geometry
    upright, tokens = _financial_grid()
    image = np.ascontiguousarray(np.rot90(np.asarray(upright), -turns))
    h, w = image.shape[:2]
    matrix = quarter_turn_to_source(turns, w, h)
    for token in tokens:
        token.bbox = _box_transform(token.bbox, matrix)
        token.rotation = turns*90
    table = detect_visual_table(_page(image), image, tokens=tokens)
    assert table is not None
    lines = []
    for row in range(table.row_count):
        selected = [token for cell in table.cells if cell.row == row for token in cell.tokens]
        box = BBox.union_all([token.bbox for token in selected])
        lines.append(TextLine(selected, box, Baseline(box.y1, turns*np.pi/2),
                              WritingDirection.UNKNOWN, None, None))
    result = validate_table_geometry(table, source_lines=lines)
    assert result.valid, result.reasons
    # Keeping the observed axes must still reject an actual index inversion.
    first, last = table.cells[0], table.cells[-1]
    first.row, last.row = last.row, first.row
    assert not validate_table_geometry(table, source_lines=lines).valid


@pytest.mark.parametrize('turns', [1, 2, 3])
def test_rotated_multiline_cell_words_preserve_reading_order_and_amount(turns):
    tokens = [TextToken(text, BBox(x, y, x+w, y+10), [], .99, text)
              for text, x, y, w in [('Segunda', 5, 30, 45), ('linha', 55, 30, 25),
                                   ('R$ 1.234,56', 10, 10, 65), ('líquido', 85, 10, 40)]]
    matrix = quarter_turn_to_source(turns, 200, 200)
    for token in tokens:
        token.bbox = _box_transform(token.bbox, matrix)
        token.rotation = turns*90
    assert join_table_tokens(tokens) == 'R$ 1.234,56 líquido Segunda linha'


def test_outer_form_frame_and_signature_rules_do_not_expand_the_table():
    pytest.importorskip('cv2')
    grid, tokens = _financial_grid()
    image = Image.new('RGB', (1000, 1100), 'white')
    image.paste(grid, (80, 300))
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 980, 1080), outline='black', width=3)
    draw.line((90, 800, 500, 800), fill='black', width=3)
    draw.line((90, 950, 800, 950), fill='black', width=3)
    for token in tokens:
        b = token.bbox
        token.bbox = BBox(80+b.x0, 300+b.y0, 80+b.x1, 300+b.y1)
    table = detect_visual_table(_page(image), image, tokens=tokens)
    assert table is not None
    assert (table.row_count, table.column_count) == (5, 5)
    assert table.page_fragments[0].bbox.y1 < 600
    assert sum(len(c.tokens) for c in table.cells) == len(tokens)


def test_rows_fully_covered_by_rowspan_remain_inside_one_html_block():
    from structured_pdf_text.document import StructuredTable, TableCell, TableMethod
    from structured_pdf_text.renderers.markdown import _render_spanned_table_html
    from structured_pdf_text.evaluation.markdown_parser import parse_page
    from structured_pdf_text.evaluation.models import NodeKind

    cell = TableCell(0, 0, 3, 2, BBox(0, 0, 100, 60), 'R$ -1,00', [], .9)
    table = StructuredTable('spanned', [], [cell], 2, 3, .9, TableMethod.VISUAL_MODEL)
    text = _render_spanned_table_html(table)
    assert not any(not line.strip() for line in text.splitlines())
    nodes = parse_page(text, 1, 'hypothesis')
    assert any(node.kind is NodeKind.TABLE for node in nodes)


def test_faint_separators_are_preserved_without_treating_shading_as_rules():
    pytest.importorskip('cv2')
    image, tokens = _financial_grid()
    pixels = np.asarray(image).copy()
    pixels[pixels[..., 0] < 235] = 234
    table = detect_visual_table(_page(pixels), pixels, tokens=tokens)
    assert table is not None
    assert (table.row_count, table.column_count) == (5, 5)
    assert next(c for c in table.cells if (c.row, c.col) == (0, 1)).colspan == 2
    assert next(c for c in table.cells if (c.row, c.col) == (4, 4)).text == 'R$ 101.000,00'


def test_disconnected_caption_and_stamp_rules_are_not_a_table_network():
    pytest.importorskip('cv2')
    image = Image.new('RGB', (400, 400), 'white')
    draw = ImageDraw.Draw(image)
    for y in (30, 130, 230):
        draw.line((20, y, 350, y), fill='black', width=2)
    for x, top, bottom in [(30, 300, 390), (150, 250, 350), (270, 280, 380)]:
        draw.line((x, top, x, bottom), fill='black', width=2)
    assert detect_visual_grid(image) is None


def test_rectified_separator_tolerance_uses_page_units_at_high_resolution():
    from structured_pdf_text.tables.visual import VisualGrid
    from structured_pdf_text.tables.visual_alignment import IDENTITY
    pytest.importorskip('cv2')
    image = Image.new('RGB', (600, 600), 'white')
    draw = ImageDraw.Draw(image)
    edges = (60, 210, 360, 510)
    for value in edges:
        draw.line((value, 60, value, 510), fill='black', width=1)
        draw.line((60, value, 510, value), fill='black', width=1)
    page = _page(image)
    page.bbox = BBox(0, 0, 200, 200)
    page.objects.images[0].bbox = page.bbox
    tokens = [TextToken(f'{r}:{c}', BBox(30+c*50, 30+r*50, 45+c*50, 40+r*50), [], .99, f'{r}:{c}')
              for r in range(3) for c in range(3)]
    class Engine:
        def recognize(self, image):
            shifted = tuple(value+3 for value in edges)
            return VisualGrid(shifted, shifted, .99, source_transform=IDENTITY, raster_size=(600, 600))
    table = detect_visual_table(page, image, tokens=tokens, structure_engine=Engine())
    assert table is not None
    assert (table.row_count, table.column_count) == (3, 3)
    assert all(c.rowspan == c.colspan == 1 for c in table.cells)
    assert [c.text for c in table.cells] == [f'{r}:{c}' for r in range(3) for c in range(3)]


def test_spanned_table_html_uses_compact_format():
    """Regression: _render_spanned_table_html must produce compact inline rows.

    Pretty-printed format (  <tr>\\n    <cells>\\n  </tr>) adds ~130 extra
    whitespace characters vs the reference format (<tr><cells></tr>), which
    the CER metric counts as 137 insertions per page for V6 pages 28/50/81.
    Fix-F14: each row must be on a single line with no indentation.
    """
    from structured_pdf_text.document import StructuredTable, TableCell, TableMethod
    from structured_pdf_text.renderers.markdown import _render_spanned_table_html

    cells = [
        TableCell(0, 0, 2, 1, BBox(0, 0, 50, 40), 'Natureza', [], 0.9),
        TableCell(0, 1, 1, 2, BBox(50, 0, 150, 20), 'Previsto', [], 0.9),
        TableCell(1, 1, 1, 1, BBox(50, 20, 100, 40), 'Quantidade', [], 0.9),
        TableCell(1, 2, 1, 1, BBox(100, 20, 150, 40), 'Valor', [], 0.9),
        TableCell(2, 0, 1, 1, BBox(0, 40, 50, 60), 'Drenagem', [], 0.9),
        TableCell(2, 1, 1, 1, BBox(50, 40, 100, 60), '8', [], 0.9),
        TableCell(2, 2, 1, 1, BBox(100, 40, 150, 60), 'R$ 34.000,00', [], 0.9),
    ]
    table = StructuredTable('test', [], cells, 3, 3, 0.9, TableMethod.VISUAL_MODEL)
    html = _render_spanned_table_html(table)

    # Every content line must be a <tr>...</tr> on a single line (compact format)
    for line in html.splitlines():
        stripped = line.strip()
        if stripped in ('<table>', '</table>', ''):
            continue
        assert stripped.startswith('<tr>') and stripped.endswith('</tr>'), (
            f"Expected compact <tr>...</tr> on one line, got: {stripped!r}"
        )
    # No indentation or standalone cell tags allowed
    assert '  <tr>' not in html, "Pretty-printed indented <tr> found — compact format required"
    assert '\n    <' not in html, "Per-cell indentation found — compact format required"


def test_align_ruling_lines_works_with_opencv5_houghlinesP_shape():
    """Regression: OpenCV 5 changed HoughLinesP return shape from (N,1,4) to (N,4).

    The old code `for x0,y0,x1,y1 in lines[:,0]` raised TypeError in OpenCV 5
    because lines[:,0] returns scalars, not 4-tuples. The exception was silently
    caught by api.py causing all rotated-table grid detection to be discarded.
    The fix selects indexing based on lines.ndim, compatible with both versions.

    A slight shear is required so align_ruling_lines has reason to return a
    non-None transform (perfectly axis-aligned grids return None by design).
    """
    cv2 = pytest.importorskip('cv2')
    import numpy as np

    upright, _ = _financial_grid()
    shear_matrix = np.array([[1., .018, 5.], [-.028, 1., 35.]], dtype=np.float32)
    skewed = cv2.warpAffine(np.asarray(upright), shear_matrix, (850, 310),
                            borderValue=(255, 255, 255))

    result = align_ruling_lines(Image.fromarray(skewed))
    assert result is not None, (
        "align_ruling_lines returned None for skewed grid — "
        "OpenCV HoughLinesP shape compat bug may have recurred"
    )
    rectified, transform = result
    assert transform is not None
