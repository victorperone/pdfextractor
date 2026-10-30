"""Signed monetary conservation and observed prefix repairs for run 005."""
from types import SimpleNamespace

import pytest

from structured_pdf_text.fusion.conservation import (
    _critical_surface_preserved, _extract_signed_numeric_critical_values,
    _refinement_conserves_content,
)
from structured_pdf_text.ocr.pipelines.easyocr.policy import TOKEN_REFINEMENT_POLICY


@pytest.mark.parametrize('original', ['-R$ 350,00', '−R$ 350,00', 'R$ −350,00', '- R$ 350,00'])
def test_negative_currency_before_or_after_marker_cannot_become_positive(original):
    assert _extract_signed_numeric_critical_values(original) == {'money:-:350.00': 1}
    assert not _refinement_conserves_content([SimpleNamespace(text=original)], [SimpleNamespace(text='R$ 350,00')])


@pytest.mark.parametrize('new', ['R$ -350,00', '-R$ 350,00', '−R$ 350,00'])
def test_equivalent_negative_placement_conserves_value(new):
    assert _refinement_conserves_content([SimpleNamespace(text='-R$ 350,00')], [SimpleNamespace(text=new)])


def test_unicode_percentage_sign_and_surface_are_protected():
    assert _extract_signed_numeric_critical_values('−0,01%') == {'percentage:-:0.01': 1}
    assert not _refinement_conserves_content([SimpleNamespace(text='−0,01%')], [SimpleNamespace(text='0,01%')])
    assert _critical_surface_preserved('−0,01%', '-0,01%')
    assert not _critical_surface_preserved('−0,01%', '0,01%')


def test_negative_duplicates_and_conflicting_signs_are_not_silently_discarded():
    assert _extract_signed_numeric_critical_values('-R$ 350,00 e R$ -350,00') == {'money:-:350.00': 2}
    assert not _refinement_conserves_content([SimpleNamespace(text='-R$ 350,00 e R$ -350,00')],
                                            [SimpleNamespace(text='R$ -350,00')])
    assert _extract_signed_numeric_critical_values('-R$ +350,00') != _extract_signed_numeric_critical_values('R$ 350,00')


def cell(text, confidence):
    return SimpleNamespace(text=text, tokens=[SimpleNamespace(text=text, confidence=confidence)])


@pytest.mark.parametrize('old,new', [('-Rs 350,00', '-R$ 350,00'), ('−Rs 350,00', 'R$ -350,00')])
def test_signed_currency_repair_uses_an_observed_value_without_changing_sign_or_digits(old, new):
    assert not TOKEN_REFINEMENT_POLICY._keeps_prior(cell(old, .96), cell(new, .81))


@pytest.mark.parametrize('old,new', [('-R$ 350,00', 'R$ 350,00'), ('-Rs 350,00', 'R$ 350,00'),
                                    ('-R$ 350,00', '-Rs 350,00'), ('-Rs 350,00', '-R$ 350,00')])
def test_signed_repair_keeps_value_or_rejects_weak_reading(old, new):
    assert TOKEN_REFINEMENT_POLICY._keeps_prior(cell(old, .96), cell(new, .10))


def test_candidate_history_and_request_snapshot_do_not_lose_prior_calls():
    from structured_pdf_text.ocr.backends.easyocr import EasyOCRBackend
    from structured_pdf_text.ocr.pipeline.diagnostics import LegacyDiagnosticsProjector

    engine = object.__new__(EasyOCRBackend)
    engine._device = 'cpu'
    engine.reset_page_diagnostics()
    first = [{'candidate_id': 'default', 'score': .9}]
    engine._last_orientation_decision = {'selected_angle': 90}
    engine._record_candidates(first)
    first[0]['score'] = 0  # history owns its data
    engine._last_orientation_decision = {'selected_angle': 0}
    engine._record_candidates([{'candidate_id': 'sharpen', 'score': .8}])
    snapshot = LegacyDiagnosticsProjector.snapshot(engine, engine_name='easyocr')
    assert snapshot['candidate_metrics'] == {'sharpen': {'candidate_id': 'sharpen', 'score': .8}}
    assert dict(snapshot['candidate_metrics'])['sharpen']['score'] == .8
    assert snapshot['candidate_count'] == 1
    result = engine.consume_page_diagnostics()
    assert [entry['selected_angle'] for entry in result['candidate_decisions']] == [90, 0]
    assert result['candidate_decisions'][0]['candidates'][0]['score'] == .9
    assert engine.consume_page_diagnostics()['candidate_decisions'] == []
    assert engine.last_candidate_metrics == {}
    assert engine.last_candidate_count == 0
