"""Conservation and scope checks for the run-011 review fixes."""
from types import SimpleNamespace as NS
from dataclasses import replace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from structured_pdf_text.document import NativeCharacter, OcrToken, SourceKind, TableCell
from structured_pdf_text.geometry import BBox, Point
from structured_pdf_text.ocr.backends.easyocr import _best_candidate
from structured_pdf_text.ocr.pipelines.easyocr.policy import CELL_POLICY
from structured_pdf_text.ocr.pipelines.easyocr.recognition import (
    direct_content_box, recover_numeric_readings, _same_percent_amount,
)
from structured_pdf_text.ocr.pipelines.easyocr.structure import EasyOCRTableStructureEngine
from structured_pdf_text.tables.headers import infer_visual_header_rows
from structured_pdf_text.text.line_detector import reconstruct_native_lines
from structured_pdf_text.ocr.pipelines.easyocr.marks import recover_selection_marks
from structured_pdf_text.ocr.pipelines.easyocr.decorations import WATERMARK_POLICY
from structured_pdf_text.ocr.pipelines.easyocr.policy import ADMISSION_POLICY
from structured_pdf_text.ocr.pipeline.contracts import WorkGoal
from structured_pdf_text.document import OcrProvenance
from structured_pdf_text.ocr.pipelines.easyocr.segmentation import trim_overlapping_readings
from structured_pdf_text.ocr.candidate_fusion import OcrEvidence


def token(text, confidence=.95, box=(5, 5, 145, 35), rotation=0):
    return OcrToken(text, BBox(*box), confidence, 'pt-BR', SourceKind.OCR_PAGE, rotation=rotation)


def selection_image(marked=True):
    image = np.full((100, 450), 255, np.uint8)
    cv2.rectangle(image, (100, 40), (129, 69), 0, 2)
    if marked:
        cv2.line(image, (106, 46), (123, 63), 0, 1)
        cv2.line(image, (106, 63), (123, 46), 0, 1)
    image[40:70, 160:164] = 0
    return image


@pytest.mark.parametrize('marked,symbol', [(False, '☐'), (True, '☒')])
def test_selection_box_preserves_label_and_provenance(marked, symbol):
    original = token('Aprovado', box=(90, 30, 400, 80))
    second = token('Reabrir', box=(90, 130, 400, 180))
    image = np.vstack((selection_image(marked), selection_image(False)))
    recovered = recover_selection_marks(image, [original, second], language='pt-BR')
    assert [t.text for t in recovered] == ['Aprovado', 'Reabrir', symbol, '☐']
    assert recovered[0].bbox.x0 >= 129
    assert recovered[2].ocr_provenance.refinement_kind == 'raster_selection_mark'
    assert recovered[2].bbox.x0 >= original.bbox.x0
    assert all(0 <= t.bbox.x0 < t.bbox.x1 <= 450 for t in recovered)


def test_isolated_blurred_box_shape_does_not_manufacture_a_form_state():
    original = token('trecho de prosa', box=(90,30,400,80))
    assert recover_selection_marks(selection_image(), [original], language='pt-BR') == [original]


@pytest.mark.parametrize('options', [{'allowlist':'ABC'}, {'blocklist':'☐☑☒'}])
def test_selection_boxes_respect_explicit_character_configuration(options):
    original = token('Aprovado', box=(90, 30, 400, 80))
    assert recover_selection_marks(selection_image(), [original], language='pt-BR', **options) == [original]


def test_selection_marks_leave_rotated_and_unmarked_rows_exactly_unchanged():
    upright = token('Aprovado', box=(90, 30, 400, 80))
    sideways = token('Rotacionado', box=(90, 30, 400, 80), rotation=90)
    assert recover_selection_marks(selection_image(), [sideways], language='pt-BR') == [sideways]
    assert recover_selection_marks(np.full((100,450),255,np.uint8), [sideways,upright], language='pt-BR') == [sideways,upright]


@pytest.mark.parametrize('level,polygon,tagged', [
    (211, (Point(10,40),Point(90,10),Point(90,30),Point(10,60)), True),
    (60, (Point(10,40),Point(90,10),Point(90,30),Point(10,60)), False),
    (211, (Point(10,10),Point(90,10),Point(90,40),Point(10,40)), False),
    (211, None, False),
])
def test_watermark_requires_original_tilt_and_faint_pixels(level, polygon, tagged):
    original = replace(token('VIA DE CONFERÊNCIA', box=(10,10,90,60)), polygon=polygon)
    annotated = WATERMARK_POLICY.annotate(np.full((80,100),level,np.uint8), [original])
    assert len(annotated) == 1 and annotated[0].text == original.text
    assert annotated[0].bbox == original.bbox and annotated[0].polygon == original.polygon
    provenance = annotated[0].ocr_provenance
    assert bool(provenance and provenance.refinement_kind == 'decorative_watermark') is tagged


@pytest.mark.parametrize('goal,rejected', [(WorkGoal.FIGURE, True), (WorkGoal.REGION, False)])
def test_watermark_admission_only_rejects_tagged_figure_observations(goal, rejected):
    observation = NS(text='VIA', confidence_normalized=.99, bbox=BBox(0,0,100,40),
        metadata={'domain': {'ocr_provenance': {'refinement_kind':'decorative_watermark'}}})
    reason = ADMISSION_POLICY.rejection_reason(observation, NS(goal=goal))
    assert (reason == 'easyocr_decorative_watermark') is rejected


@pytest.mark.parametrize('text', ['☐','☑','☒'])
def test_geometric_checkbox_states_are_admitted_in_figures_with_their_provenance(text):
    observation = NS(text=text, confidence_normalized=.9, bbox=BBox(0,0,20,20),
                     metadata={'domain':{'ocr_provenance':{'refinement_kind':'raster_selection_mark'}}})
    assert ADMISSION_POLICY.rejection_reason(observation, NS(goal=WorkGoal.FIGURE)) is None
    observation.metadata = {}
    assert ADMISSION_POLICY.rejection_reason(observation, NS(goal=WorkGoal.FIGURE)) == 'easyocr_lone_figure_mark'


def test_overlap_of_confusable_url_readings_keeps_one_observed_surface_and_quote():
    first = replace(token('Documento “https://portal/01.pdf', box=(0,0,300,30)),
                    ocr_provenance=OcrProvenance('easyocr',candidate_id='first'))
    second = replace(token('“https://portaI/O1.pdf” final', box=(100,0,390,30)),
                     ocr_provenance=OcrProvenance('easyocr',candidate_id='second'))
    evidence = OcrEvidence((first,second), 2, 0, 0)
    repaired, counts = trim_overlapping_readings(evidence)
    assert [t.text for t in repaired.tokens] == ['Documento', second.text]
    assert sum(counts.values()) == 1


def test_same_url_printed_in_distinct_positions_remains_independent():
    first = token('Documento https://portal/01.pdf', box=(0,0,300,30))
    second = token('https://portal/01.pdf final', box=(0,45,290,75))
    evidence = OcrEvidence((first,second), 2, 0, 0)
    repaired, counts = trim_overlapping_readings(evidence)
    assert repaired == evidence and not counts


def test_confusable_glyph_lists_need_substantial_shared_prose_before_trimming():
    first = replace(token('Campos 0, 0, I, 1, rn e m', box=(0,0,300,30)),
                    ocr_provenance=OcrProvenance('easyocr',candidate_id='first'))
    second = replace(token('O, O, 1, |, rn e m restante', box=(150,0,450,30)),
                     ocr_provenance=OcrProvenance('easyocr',candidate_id='second'))
    evidence = OcrEvidence((first,second),2,0,0)
    repaired, counts = trim_overlapping_readings(evidence)
    assert repaired == evidence and not counts


def test_direct_content_ignores_fragmented_edge_rules_and_preserves_sign_and_cents():
    image = np.full((60, 400), 255, np.uint8)
    cv2.line(image, (0, 1), (230, 1), 0, 1)
    cv2.line(image, (150, 58), (399, 58), 0, 1)
    cv2.putText(image, '-0,01', (8, 37), cv2.FONT_HERSHEY_SIMPLEX, .7, 0, 2)
    before = image.copy()
    x0, y0, x1, y1 = direct_content_box(image)
    assert 0 <= x0 < 10 < x1 < 130 and 1 < y0 < 30 < y1 < 58
    assert np.array_equal(image, before)
    assert np.array_equal(image[y0:y1, x0:x1], before[y0:y1, x0:x1])


@pytest.mark.parametrize('shape', [(0, 0), (30, 100), (20, 20)])
def test_blank_direct_content_is_bounded(shape):
    assert direct_content_box(np.full(shape, 255, np.uint8)) == (0, 0, shape[1], shape[0])


@pytest.mark.parametrize('old,new,expected', [
    ('+0,749', '+0,74%', True), ('0,0196', '0,01%', True),
    ('+21,2586', '+21,25%', True), ('0,0196', '0,019%', False),
    ('-0,749', '+0,74%', False), ('0,749', '0,75%', False),
    ('01,2596', '1,25%', False), ('0,749', '0,74‰', False),
    ('45,81%', '+5,81%', False), ('+0,749', '-0,74%', False),
    ('+0,749', '0,74%', False),
])
def test_percent_verification_preserves_literal_amount_and_explicit_sign(old, new, expected):
    assert _same_percent_amount(old, new) is expected


def numeric_candidates():
    return [('default', [token('+0,749', .99), token('%', .99, (160, 5, 175, 35))]),
            ('otsu', [token('+0,749', .98)])]


def test_two_complete_percent_reads_override_the_confident_misread_suffix():
    reader = NS(recognize=Mock(return_value=[([], '+0,74%', .90)]))
    calls = Mock()
    candidates = numeric_candidates()
    recovered = recover_numeric_readings(reader, np.full((60, 200), 255, np.uint8), candidates,
        page_index=0, language='pt-BR', record_call=calls)
    assert reader.recognize.call_count == calls.call_count == 2
    assert all(call.kwargs['allowlist'] is None for call in reader.recognize.call_args_list)
    selected, diagnostics = _best_candidate(recovered)
    assert selected[0].text == '+0,74%'
    assert selected[0].confidence == .9
    assert selected[0].bbox == token('').bbox
    assert sum(d['numeric_verification_adoptions'] for d in diagnostics) == 1
    assert candidates[0][1][0].text == '+0,749'


@pytest.mark.parametrize('reads', [
    [('+0,75%', .99), ('+0,75%', .99)],
    [('+0,74%', .99), ('+0,749', .99)],
    [('+0,74%', .99), ('+0,74%', .79)],
    [('+0,74%', float('nan')), ('+0,74%', .99)],
    [('-0,74%', .99), ('-0,74%', .99)],
])
def test_numeric_verification_abstains_on_changed_digits_disagreement_weakness_and_sign(reads):
    reader = NS(recognize=Mock(side_effect=[[([], text, confidence)] for text, confidence in reads]))
    candidates = numeric_candidates()
    recovered = recover_numeric_readings(reader, np.zeros((60, 200), np.uint8), candidates,
        page_index=0, language='pt-BR')
    assert recovered == candidates


def test_verified_currency_repairs_separators_but_never_changes_cents():
    candidates = [('default', [token('Rs 120 000,00', .99)])]
    for text, accepted in [('R$ 120.000,00', True), ('R$ 12.000,00', False),
                           ('R$ 120.000,01', False), ('R$ 0120.000,00', False)]:
        reader = NS(recognize=Mock(return_value=[([], text, .91)]))
        result = recover_numeric_readings(reader, np.zeros((60, 200), np.uint8), candidates,
            page_index=0, language='pt-BR')
        assert (len(result) > len(candidates)) is accepted
        if accepted:
            assert _best_candidate(result)[0][0].text == text


@pytest.mark.parametrize('context,language,expected', [
    ('R$ 1,00','pt-BR',True), ('1,00','pt-BR',False), ('R$ 1,00','en',False),
])
def test_ambiguous_as_currency_requires_observed_real_and_portuguese(context, language, expected):
    anchor = token('As 293.500,00', .99)
    candidates = [('default',[anchor,token(context,box=(5,40,145,70))])]
    reader = NS(recognize=Mock(return_value=[([], 'R$ 293.500,00', .95)]))
    result = recover_numeric_readings(reader,np.zeros((90,200),np.uint8),candidates,
                                     page_index=0,language=language)
    assert bool(reader.recognize.call_count) is expected
    if expected:
        assert _best_candidate(result)[0][0].text == 'R$ 293.500,00'


def test_direct_currency_must_not_override_explicit_ambiguous_marker_sign():
    candidates = [('default',[token('-As 12,00'),token('R$ 1,00',box=(5,40,145,70))])]
    reader = NS(recognize=Mock(return_value=[([], 'R$ +12,00', .99)]))
    assert recover_numeric_readings(reader,np.zeros((90,200),np.uint8),candidates,
                                    page_index=0,language='pt-BR') == candidates


def test_numeric_verification_preserves_an_explicit_positive_currency_sign():
    candidates = [('default',[token('Rs +12,00')])]
    reader = NS(recognize=Mock(return_value=[([], 'R$ 12,00', .99)]))
    assert recover_numeric_readings(reader,np.zeros((60,200),np.uint8),candidates,
                                    page_index=0,language='pt-BR') == candidates
    assert not CELL_POLICY.select([[token('R$ 12,00')]]*2, current_text='Rs +12,00',
                                  cell_bbox=BBox(0,0,200,60),header_text='Valor R$')


def test_unsigned_percent_variant_cannot_hide_a_signed_aligned_observation():
    candidates = numeric_candidates()
    candidates[1][1][0] = token('0,749')
    reader = NS(recognize=Mock(return_value=[([], '0,74%', .99)]))
    assert recover_numeric_readings(reader,np.zeros((60,200),np.uint8),candidates,
                                    page_index=0,language='pt-BR') == candidates


def test_numeric_inference_is_bounded_and_resource_errors_propagate():
    candidates = [('default', [token('Rs 1,00', box=(5, i * 40, 145, i * 40 + 30)) for i in range(12)])]
    reader = NS(recognize=Mock(return_value=[]))
    recover_numeric_readings(reader, np.zeros((500, 200), np.uint8), candidates,
        page_index=0, language='pt-BR', maximum_fields=3)
    assert reader.recognize.call_count == 6
    reader.recognize.side_effect = RuntimeError('CUDA out of memory')
    def propagate(error):
        raise error
    with pytest.raises(RuntimeError, match='out of memory'):
        recover_numeric_readings(reader, np.zeros((500, 200), np.uint8), candidates,
            page_index=0, language='pt-BR', propagate_error=propagate)


def test_numeric_cell_selection_keeps_only_two_matching_complete_reads():
    cell = BBox(0, 0, 200, 60)
    good = [token('+0,74%', .92)]
    assert CELL_POLICY.select([good, good], current_text='+0,749', cell_bbox=cell, header_text='%')
    assert not CELL_POLICY.select([good], current_text='+0,749', cell_bbox=cell, header_text='%')
    assert not CELL_POLICY.select([good, [token('+0,74%', .3)]], current_text='+0,749', cell_bbox=cell, header_text='%')
    assert not CELL_POLICY.select([good, good], current_text='-0,749', cell_bbox=cell, header_text='%')


def test_shaded_alphanumeric_labels_are_easyocr_headers_but_numbers_are_not():
    image = np.full((100, 200), 255, np.uint8)
    image[:20] = 230
    cells = [TableCell(r, c, 1, 1, BBox(c*100, r*20, (c+1)*100, (r+1)*20),
                       text, [], .9) for r, row in enumerate([['C01', 'C02'], ['T10A-01', 'T10A-02']])
             for c, text in enumerate(row)]
    assert infer_visual_header_rows(cells, image, BBox(0, 0, 200, 100)) == ()
    assert EasyOCRTableStructureEngine().infer_header_rows(cells, image, BBox(0, 0, 200, 100)) == (0,)
    for cell in cells[:2]:
        cell.text = '123'
    assert EasyOCRTableStructureEngine().infer_header_rows(cells, image, BBox(0, 0, 200, 100)) == ()


def test_case_majority_keeps_the_observed_surface_and_cannot_change_ids_or_signs():
    candidates = [('default', [token('T17a-02', .99)])]
    candidates += [(name, [token('T17A-02', .9)]) for name in ('gray', 'otsu', 'sharp')]
    selected, diagnostics = _best_candidate(candidates)
    assert selected[0].text == 'T17A-02'
    assert sum(d['case_majority_adoptions'] for d in diagnostics) == 1
    wrong = [(name, [token('T18A-02', .9)]) for name in ('gray', 'otsu', 'sharp')]
    assert _best_candidate(candidates[:1] + wrong)[0][0].text == 'T17a-02'


def native_columns(gap, rows):
    chars = []
    for row in range(rows):
        for col in range(2):
            for i, text in enumerate('abcdefghijkl'):
                x = col * (12 * 5 - 1 + gap) + i * 5
                chars.append(NativeCharacter(0, len(chars), text, ord(text),
                    BBox(x, 10 + row*12, x+4, 16+row*12), angle=0.))
    return tuple(chars)


def test_recurring_narrow_native_gutters_split_without_losing_a_glyph():
    characters = native_columns(12.6, 5)
    lines = reconstruct_native_lines(characters)
    assert len(lines) == 10
    assert all(line.text == 'abcdefghijkl' for line in lines)
    assert sum(len(t.text) for line in lines for t in line.tokens if t.text.strip()) == len(characters)


def test_an_isolated_gap_and_ordinary_word_spaces_do_not_create_columns():
    assert len(reconstruct_native_lines(native_columns(12.6, 1))) == 1
    assert len(reconstruct_native_lines(native_columns(7., 5))) == 5
