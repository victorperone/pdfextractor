"""Run 009: conservative form marks and decimal evidence across crop rereads."""
from types import SimpleNamespace

import numpy as np
import pytest

from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.pipeline.policies import TokenRefinementPolicy
from structured_pdf_text.ocr.pipelines.rapidocr.policy import SELECTION_MARK_POLICY, TOKEN_REFINEMENT_POLICY
from test_rapidocr_improvement_008 import _canvas, _ink_word, _row


def _inner_option(*, marked=False):
    image = _canvas()
    _ink_word(image, 100, 300)
    image[40:62, 340] = image[40:62, 361] = 0
    image[40, 340:362] = image[61, 340:362] = 0
    if marked:
        for i in range(5, 17):
            image[40 + i, 340 + i] = 0
            image[41 + i, 340 + i] = 0
    _ink_word(image, 400, 560)
    return image


def test_marked_glyph_in_unseeded_prose_is_not_a_checkbox():
    rows = [_row(100, 35, 560, 75, 'valor associado ao serviço')]
    assert SELECTION_MARK_POLICY.apply(_inner_option(marked=True), rows) == rows


def test_internal_marked_option_is_recovered_on_an_established_form_row():
    rows = [_row(100, 35, 560, 75, '☐ DOCUMENTO CONFERIDO COMPLEMENTO')]
    recovered = SELECTION_MARK_POLICY.apply(_inner_option(marked=True), rows)
    assert [row[1] for row in recovered] == ['☐ DOCUMENTO CONFERIDO', 'COMPLEMENTO', '☑']


def test_long_prose_does_not_schedule_an_internal_square_search(monkeypatch):
    calls = []
    cls = type(SELECTION_MARK_POLICY)
    original = cls._box_candidates
    def observe(self, *args, **kwargs):
        calls.append(kwargs.get('inner', False))
        return original(self, *args, **kwargs)
    monkeypatch.setattr(cls, '_box_candidates', observe)
    rows = [_row(100, 35, 560, 75, 'Esta amostra combina palavras e valores associados ao documento completo')]
    assert SELECTION_MARK_POLICY.apply(_inner_option(), rows) == rows
    assert calls and not any(calls)


def test_too_small_square_search_returns_an_empty_collection():
    dark = np.zeros((6, 6), dtype=bool)
    integral = np.pad(dark.astype(np.int32).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    assert SELECTION_MARK_POLICY._box_candidates(dark, integral, 0, 5, 0, 5, 20, inner=True) == []


def _table(text, *, confidence=.99, extra=False):
    box = BBox(0, 0, 50, 20)
    token = SimpleNamespace(text=text, confidence=confidence)
    cell = SimpleNamespace(text=text, tokens=[token], bbox=box, row=0, col=0, rowspan=1, colspan=1)
    cells = [cell]
    if extra:
        cells.append(SimpleNamespace(text='recuperado', tokens=[], bbox=BBox(50, 0, 100, 20), row=0, col=1, rowspan=1, colspan=1))
    return SimpleNamespace(cells=cells)


@pytest.mark.parametrize('old,new', [('39,25', '39.25'), ('0,0025', '0.0025'), ('-0,01', '0,01'),
                                    ('1.200,50', '1,200.50'), ('01,25', '1,25')])
def test_confident_decimal_surface_cannot_be_overwritten_by_a_crop(old, new):
    assert not TOKEN_REFINEMENT_POLICY.accept_visual_table(_table(old), _table(new))
    assert TokenRefinementPolicy().accept_visual_table(_table(old), _table(new))  # Other engines keep their policy.


def test_missing_and_weak_cells_remain_eligible_for_recovery():
    assert TOKEN_REFINEMENT_POLICY.accept_visual_table(_table(''), _table('39,25'))
    assert TOKEN_REFINEMENT_POLICY.accept_visual_table(_table('39.25', confidence=.70), _table('39,25'))
    assert TOKEN_REFINEMENT_POLICY.accept_visual_table(_table('39,25'), _table('39,25', extra=True))
    assert TOKEN_REFINEMENT_POLICY.accept_visual_table(_table('39.25'), _table('39,25'))


def test_conflicting_decimal_is_kept_without_losing_other_cell_improvements():
    original, candidate = _table('55,25'), _table('55.25')
    original.cells[0].confidence = .99
    # This second cell reproduces V4 p178: freezing the entire table would
    # discard a correct regional comma in favour of the original dot.
    old_second, new_second = _table('39.25').cells[0], _table('39,25').cells[0]
    old_second.col = new_second.col = 1
    original.cells.append(old_second)
    candidate.cells.append(new_second)
    draft = TOKEN_REFINEMENT_POLICY.prepare_visual_table(original, candidate)
    assert [cell.text for cell in draft.cells] == ['55,25', '39,25']
    assert [cell.text for cell in original.cells] == ['55,25', '39.25']
    assert [cell.text for cell in candidate.cells] == ['55.25', '39,25']
    assert TOKEN_REFINEMENT_POLICY.accept_visual_table(original, draft)


@pytest.mark.parametrize('rapidocr', [False, True])
def test_table_crop_decimal_rejection_is_atomic_and_engine_local(monkeypatch, rapidocr):
    from copy import deepcopy
    from PIL import Image
    from structured_pdf_text import api, SecurityLimits
    from structured_pdf_text.document import OcrToken, SourceKind, StructuredTable, TableCell, TableFragment, TableMethod
    from structured_pdf_text.ocr.pipeline.adapters import LegacyInjectedPipeline
    from structured_pdf_text.ocr.pipeline.coordinator import OCRPageFacade, PageCoordinator
    from structured_pdf_text.ocr.pipeline.table_integration import table_snapshot
    from test_ocr_pipeline_services import SpySource

    box = BBox(20, 20, 120, 60)
    old = OcrToken('39,25', BBox(30, 30, 60, 40), .99, 'pt-BR', SourceKind.OCR_REGION)
    other = OcrToken('55.25', BBox(80, 30, 110, 40), .99, 'pt-BR', SourceKind.OCR_REGION)
    table = StructuredTable('decimal-table', [TableFragment(0, box, 0, 1)], [
        TableCell(0, 0, 1, 1, BBox(20, 20, 70, 60), old.text, [old], .99),
        TableCell(0, 1, 1, 1, BBox(70, 20, 120, 60), other.text, [other], .99),
    ], 2, 1, .99, TableMethod.VISUAL_MODEL)
    class Engine:
        def recognize_page(self, *args, **kwargs):
            from dataclasses import replace
            return [replace(old, text='39.25'), replace(other, text='55,25')]
    def builder(*args, **kwargs):
        candidate = deepcopy(table)
        for cell, token in zip(candidate.cells, kwargs['tokens']):
            cell.tokens = [token]
            cell.text = token.text
        return candidate
    monkeypatch.setattr(api, 'detect_visual_table_in_region', builder)
    pipeline = LegacyInjectedPipeline(Engine())
    if rapidocr:
        pipeline.token_refinement_policy = TOKEN_REFINEMENT_POLICY
    coordinator = PageCoordinator(pipeline, SpySource(), extraction_id='decimal', document_id='doc',
                                  selection=pipeline.descriptor.selection, page_index=0,
                                  page_bbox=BBox(0, 0, 200, 100), rotation=0,
                                  quality_policy='adaptive', security_limits=SecurityLimits())
    try:
        result = api._refine_visual_table_ocr(OCRPageFacade(coordinator), Image.new('RGB', (200, 100)),
                                             SimpleNamespace(bbox=coordinator.page_bbox), 0, table, [], 'adaptive')
        assert result
        assert not coordinator._deferred_tokens
        assert table.cells[1].text == '55,25'
        if rapidocr:
            assert table.cells[0].text == '39,25'
            assert table.cells[0].tokens[0].text == old.text
        else:
            assert table.cells[0].text == '39.25'
    finally:
        coordinator.close()
