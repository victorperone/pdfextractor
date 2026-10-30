"""Conservation, rotated evidence, fatal errors and numeric cell authorization."""
from dataclasses import replace
from unittest.mock import PropertyMock, patch

import numpy as np
import pytest
from PIL import Image

from structured_pdf_text.config import SecurityLimits
from structured_pdf_text.document import OcrProvenance, OcrToken, SourceKind, TableCell
from structured_pdf_text.errors import ResourceExhaustedExtractionError
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.backends import easyocr as backend
from structured_pdf_text.ocr.candidate_fusion import OcrCandidateFusionEngine, OcrCandidateResult
from structured_pdf_text.ocr.contracts import OCRRequest
from structured_pdf_text.ocr.pipeline.adapters import AuthorizedBackendPipeline
from structured_pdf_text.ocr.pipeline.contracts import EngineSelection, WorkGoal
from structured_pdf_text.ocr.pipeline.coordinator import OCRPageFacade, PageCoordinator
from structured_pdf_text.ocr.pipeline.policies import CellRecoveryPolicy
from structured_pdf_text.ocr.pipelines.easyocr.policy import ADMISSION_POLICY, CELL_POLICY, FUSION_POLICY


def tok(bounds, text, confidence=.95):
    return OcrToken(text, BBox(*bounds), confidence, 'pt-BR', SourceKind.OCR_PAGE)


def rotated(token, angle):
    return replace(token, bbox=token.bbox.rotate_to_visual(angle, 1000, 1000), rotation=angle)


def fuse(first, second=()):
    return OcrCandidateFusionEngine(FUSION_POLICY).fuse([
        OcrCandidateResult('default', 'craft_greedy', tuple(first), .9, 'easyocr'),
        OcrCandidateResult('sharpen', 'craft_greedy', tuple(second), .85, 'easyocr')]).tokens


@pytest.mark.parametrize('angle', [0, 90, 180, 270])
@pytest.mark.parametrize('start', [270, 330, 350])
def test_confident_partial_span_never_replaces_the_whole_line(angle, start):
    line = rotated(tok((100, 200, 500, 214), 'O lote apresenta ação, informação e saúde.', .65), angle)
    tail = rotated(tok((start, 200, 500, 214), 'informaçã0 e saúde', .99), angle)
    assert [token.text for token in fuse([line], [tail])] == [line.text]
    assert line.rotation == angle


@pytest.mark.parametrize('angle', [0, 90, 180, 270])
def test_a_single_pass_keeps_separate_lines_with_overlapping_envelopes(angle):
    first = rotated(tok((100, 100, 500, 130), 'Primeira linha completa', .90), angle)
    second = rotated(tok((200, 112, 400, 140), 'Segunda linha distinta', .95), angle)
    assert {token.text for token in fuse([first, second])} == {first.text, second.text}


def test_perpendicular_text_is_not_explained_away_by_horizontal_body():
    body = tok((100, 100, 500, 140), 'corpo extenso do documento')
    stamp = replace(tok((110, 105, 130, 135), 'corpo'), rotation=90)
    assert len(fuse([body], [stamp])) == 2


def test_nested_tile_readings_keep_distinct_outer_origins():
    line = tok((100, 200, 500, 214), 'O lote apresenta ação, informação e saúde.', .70)
    tail = tok((350, 199, 500, 215), 'informaçã0 e saúde', .95)
    origin = OcrProvenance('easyocr', candidate_id='default')
    line, tail = replace(line, ocr_provenance=origin), replace(tail, ocr_provenance=origin)
    result = OcrCandidateFusionEngine(FUSION_POLICY).fuse([
        OcrCandidateResult('full-page', 'full-page', (line,), .9, 'easyocr'),
        OcrCandidateResult('tile:2', 'tile', (tail,), .85, 'easyocr')])
    assert [token.text for token in result.tokens] == [line.text]
    assert line.ocr_provenance.tile_id is None


@pytest.mark.parametrize('angle', [90, 180, 270])
def test_rotated_polygon_edges_stay_inside_source_raster(angle):
    source_w, source_h = 100, 60
    w, h = (source_h, source_w) if angle in (90, 270) else (source_w, source_h)
    raw = [([[0, 0], [w, 0], [w, h], [0, h]], 'boundary', .99)]
    points = backend._remap_raw_for_rotation(raw, angle, h, w)[0][0]
    assert min(x for x, y in points) == min(y for x, y in points) == 0
    assert max(x for x, y in points) == source_w
    assert max(y for x, y in points) == source_h


def test_raw_api_propagates_resource_failure_without_gpu_retry():
    engine = object.__new__(backend.EasyOCRBackend)
    engine._closed, engine._device, engine._reader = False, 'cpu', None
    engine._run_kwargs = lambda: {}
    request = OCRRequest(np.full((20, 40, 3), 255, np.uint8), 'test', 'test', 0, 'page', 'pt-BR')
    with patch.object(backend, '_run_easyocr', side_effect=MemoryError('allocation failed')):
        with pytest.raises(ResourceExhaustedExtractionError):
            engine.recognize(request)


@pytest.mark.parametrize('letter', ['O', 'o', 'C'])
def test_confident_letter_figure_labels_are_not_rejected_as_rings(letter):
    from types import SimpleNamespace
    value = SimpleNamespace(text=letter, bbox=BBox(0, 0, 10, 12), confidence_normalized=.999)
    assert ADMISSION_POLICY.rejection_reason(value, SimpleNamespace(goal=WorkGoal.FIGURE)) is None


class Source:
    def render_region(self, page_index, bbox, scale, *, page_bbox):
        return Image.new('RGB', (round(bbox.width*scale), round(bbox.height*scale)), 'white')


class DigitBackend:
    def recognize_page(self, image, page_index, page_bbox=None, **kwargs):
        return [tok((2, 2, 12, 12), '6', .999)]


@pytest.mark.parametrize('policy,header,expected', [
    (CELL_POLICY, 'Quantidade', '6'),
    (CELL_POLICY, 'Qtd.', '6'),
    (CELL_POLICY, 'Descrição', ''),
    (CellRecoveryPolicy(), 'Quantidade', ''),
])
def test_single_digit_requires_numeric_context_and_local_opt_in(policy, header, expected):
    pipeline = AuthorizedBackendPipeline(DigitBackend(), selection=EngineSelection('easyocr'))
    pipeline.cell_policy = policy
    coordinator = PageCoordinator(pipeline, Source(), extraction_id='test', document_id='test',
        selection=EngineSelection('easyocr'), page_index=0, page_bbox=BBox(0, 0, 100, 20),
        rotation=0, quality_policy='adaptive', security_limits=SecurityLimits())
    facade = OCRPageFacade(coordinator)
    cell = TableCell(row=0, col=0, rowspan=1, colspan=1, bbox=BBox(0, 0, 100, 20), text='', tokens=[], confidence=0.)
    try:
        facade.refine_cell(table_id='table', page_index=0, cell=cell, quality_policy='adaptive',
                           base_scale=1., header_text=header)
        assert cell.text == expected
    finally:
        facade.close()


@pytest.mark.parametrize('text,confidence', [('6', .94), ('O', .999), ('-', .999)])
def test_numeric_context_does_not_admit_weak_or_nondigit_noise(text, confidence):
    observation = tok((1, 1, 10, 12), text, confidence)
    assert not CELL_POLICY.select([[observation]], current_text='', cell_bbox=BBox(0, 0, 20, 20), header_text='Quantidade')


def test_missing_numeric_context_does_not_enable_single_digit_insertion():
    assert CELL_POLICY.allows_single_digit(None) is False


def test_same_baseline_redundant_fragments_are_still_suppressed():
    line = tok((100, 100, 500, 120), 'Texto principal com informação', .90)
    noise = tok((440, 100, 460, 120), '_[', .95)
    assert [token.text for token in fuse([line, noise])] == [line.text]


def test_confident_parts_do_not_erase_punctuation_of_a_reliable_complete_line():
    line = tok((0, 100, 400, 120), 'O lote apresenta ação, informação; coração e saúde.', .768)
    parts = [tok((0, 100, 180, 120), 'O lote apresenta ação', .999),
             tok((180, 100, 400, 120), 'informação; coração e saúde_', .822)]
    assert [token.text for token in fuse(parts, [line])] == [line.text]


def test_reread_fragments_do_not_displace_the_complete_caption():
    caption = tok((100, 450, 266, 463), 'sucinto de encaminhamento', .876)
    fragments = [tok((100, 450, 159, 463), 'ucinto de', .99),
                 tok((159, 437, 287, 474), 'encaminhamento €', .931)]
    assert [token.text for token in fuse(fragments, [caption])] == [caption.text]
