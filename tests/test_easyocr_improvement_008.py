"""Run 008 review: shadow-recovered headers, corroborated minus and R$ markers."""
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageDraw

from structured_pdf_text.document import (EvidenceRef, LayoutRegion, RegionDecision, RegionKind,
                                          RegionQuality, SourceKind, StructuredTable, TableCell,
                                          TableFragment, TableMethod, TextLine, TextToken,
                                          WritingDirection)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.pipelines.easyocr.pipeline import EasyOCRPipeline
from structured_pdf_text.ocr.pipelines.easyocr.structure import EasyOCRTableStructureEngine
from structured_pdf_text.tables.text_join import join_table_tokens
from structured_pdf_text.tables.value_surfaces import harmonize_currency_markers, harmonize_value_surfaces
from structured_pdf_text.tables.visual import detect_visual_table


class _ShadowOnlyEngine(EasyOCRTableStructureEngine):
    """The observed raster yields no grid, as under V4's shadow condition."""

    def __init__(self):
        self.calls = 0

    def recognize(self, image):
        self.calls += 1
        return None if self.calls == 1 else super().recognize(image)


def _shaded_header_grid():
    image = Image.new('RGB', (640, 200), 'white')
    draw = ImageDraw.Draw(image)
    xs, ys = [20, 220, 420, 620], [20, 70, 120, 170]
    draw.rectangle((xs[0], ys[0], xs[-1], ys[1]), fill=(190, 190, 190))
    for x in xs:
        draw.line((x, ys[0], x, ys[-1]), fill='black', width=3)
    for y in ys:
        draw.line((xs[0], y, xs[-1], y), fill='black', width=3)
    rows = (('Região', 'Jan', 'Fev'), ('T20B-NORTE', '100', '101'), ('T20B-SUL', '200', '201'))
    tokens = []
    for r, labels in enumerate(rows):
        for c, text in enumerate(labels):
            x, y = xs[c] + 10, ys[r] + 18
            draw.text((x, y), text, fill='black')
            tokens.append(TextToken(text, BBox(x, y, x + 6 * len(text), y + 12), [], .99, text))
    page = SimpleNamespace(page_index=0, bbox=BBox(0, 0, 640, 200), extracted_text='',
                           objects=SimpleNamespace(rotation=0, images=[SimpleNamespace(bbox=BBox(0, 0, 640, 200))]))
    return page, image, tokens


def test_header_band_is_measured_on_observed_pixels_after_shadow_recovery():
    # Run 008, V4 p184: the grid was found only on the illumination-normalized
    # raster, which also flattened the header band; the table lost its header
    # and was rendered as HTML.
    pytest.importorskip('cv2')
    page, image, tokens = _shaded_header_grid()
    engine = _ShadowOnlyEngine()
    table = detect_visual_table(page, image, tokens=tokens, structure_engine=engine)
    assert engine.calls == 2
    assert table is not None and (table.row_count, table.column_count) == (3, 3)
    assert table.header_rows == (0,)


def _cell(row, col, text, *, native=False):
    box = BBox(100 * col, 20 * row, 100 * col + 90, 20 * row + 15)
    kind = SourceKind.NATIVE_PDF if native else SourceKind.OCR_REGION
    tokens = [TextToken(text, box, [EvidenceRef(kind, 0, text)], .9, text)]
    return TableCell(row, col, 1, 1, box, join_table_tokens(tokens), tokens, .9)


def _table(texts):
    cells = [_cell(0, col, text) for col, text in enumerate(texts)]
    return StructuredTable('t', [TableFragment(0, BBox.union_all([c.bbox for c in cells]), 0, 0)],
                           cells, len(cells), 1, .9, TableMethod.VISUAL_MODEL)


def test_misread_currency_markers_follow_the_table_r_sign():
    table = _table(['R$ 12.500,00', 'RS 7.250,00', 'Rs 6.900,00', '-Rs 350,00', 'Rs +150,00',
                    'Rs 350', 'Rs 1.500', 'Rs abc'])
    assert harmonize_value_surfaces(table, currency_markers=True)
    assert [c.text for c in table.cells] == ['R$ 12.500,00', 'R$ 7.250,00', 'R$ 6.900,00', '-R$ 350,00',
                                             'R$ +150,00', 'Rs 350', 'Rs 1.500', 'Rs abc']
    assert table.cells[3].tokens[0].text == '-R$ 350,00'
    assert table.cells[3].tokens[0].transformation_type == 'table_value_surface'


@pytest.mark.parametrize('texts,opt_in', [
    (['Rs 7.250,00', 'Rs 6.900,00'], True),        # no observed R$: may be rupees
    (['R$ 12.500,00', 'Rs 6.900,00'], False),      # other engines keep their behavior
])
def test_currency_markers_need_table_evidence_and_engine_opt_in(texts, opt_in):
    table = _table(texts)
    harmonize_value_surfaces(table, currency_markers=opt_in)
    assert table.cells[1].text == 'Rs 6.900,00'


def _line(*parts, native=False):
    kind = SourceKind.NATIVE_PDF if native else SourceKind.OCR_PAGE
    tokens = [TextToken(text, BBox(10 * i, 0, 10 * i + 9, 10), [EvidenceRef(kind, 0, text)], .9, text)
              for i, text in enumerate(parts)]
    return TextLine(tokens, BBox.union_all([t.bbox for t in tokens]), None, WritingDirection.LEFT_TO_RIGHT, None, None)


def _region(ocr_lines=(), native_lines=()):
    return LayoutRegion('r', RegionKind.TEXT, BBox(0, 0, 500, 100), None, list(native_lines), [],
                        RegionQuality(RegionDecision.OCR_REGION), ocr_lines=list(ocr_lines))


def test_prose_currency_markers_follow_the_page_r_sign():
    split = _line('valor associado é', ' ', 'RS', ' ', '12.055,00', ', taxa')
    joined = _line('0 valor associado é Rs 12.227,00 a taxa')
    untouched = _line('Rs 350 itens; Rs 12,5; os Rs.')
    evidence = _line('o saldo é R$ 1.040,00')
    regions = [_region([split, joined, untouched, evidence])]
    assert harmonize_currency_markers(regions) == 2
    assert split.text == 'valor associado é R$ 12.055,00, taxa'
    assert joined.text == '0 valor associado é R$ 12.227,00 a taxa'
    assert untouched.text == 'Rs 350 itens; Rs 12,5; os Rs.'
    assert joined.tokens[0].transformation_type == 'page_currency_marker'


def test_prose_currency_markers_need_page_evidence_and_never_touch_native_or_cells():
    lone = _line('o valor é Rs 12.227,00')
    assert harmonize_currency_markers([_region([lone])]) == 0
    assert lone.text == 'o valor é Rs 12.227,00'
    native = _line('Rs 12.227,00', native=True)
    owned = _line('Rs 6.900,00')
    table = _table(['R$ 1,00'])
    table.cells[0].tokens.append(owned.tokens[0])
    assert harmonize_currency_markers([_region([native, owned], [_line('R$ 2,00', native=True)])], [table]) == 0
    assert (native.text, owned.text) == ('Rs 12.227,00', 'Rs 6.900,00')


@pytest.mark.parametrize('language,enabled', [('pt-BR', True), ('pt', True), ('en', False), (None, False)])
def test_currency_marker_repair_is_limited_to_portuguese(language, enabled):
    pipeline = SimpleNamespace(settings=SimpleNamespace(language=language))
    assert EasyOCRPipeline.currency_marker_repair.fget(pipeline) is enabled
