"""Regression: confidence alone must not erase corroborated signed values."""
from dataclasses import replace

import pytest

from structured_pdf_text.document import OcrToken, SourceKind
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.backends.easyocr import _best_candidate
from structured_pdf_text.ocr.candidate_fusion import OcrCandidateResult
from structured_pdf_text.ocr.pipelines.easyocr.policy import FUSION_POLICY


def token(text, confidence=.8, bounds=(10,10,150,40), rotation=0):
    return OcrToken(text,BBox(*bounds),confidence,'pt-BR',SourceKind.OCR_PAGE,rotation=rotation)


@pytest.mark.parametrize('negative,unsigned',[('-R$ 350,00','R$ 350,00'),('−R$ 350,00','R$ 350,00'),
    ('R$ -350,00','R$ 350,00'),('-0,01%','0,01%'),('-2,75‰','2,75‰')])
def test_two_aligned_signed_readings_survive_a_more_confident_unsigned_read(negative,unsigned):
    tokens,diag=_best_candidate([('default',[token(unsigned,.99)]),
        ('high_recall',[token(negative,.78)]),('otsu',[token(negative,.70)])])
    assert [t.text for t in tokens]==[negative]
    assert any(d['sign_protected_alternatives'] for d in diag)
    assert tokens[0].confidence in [.78,.70]


def test_marker_repair_preserves_an_observed_signed_currency_read():
    tokens,_=_best_candidate([('default',[token('R$ 350,00',.99)]),
        ('high_recall',[token('-Rs 350,00',.78)]),('otsu',[token('-R$ 350,00',.70)])])
    assert [t.text for t in tokens]==['-R$ 350,00']


@pytest.mark.parametrize('second',[
    token('-R$ 350,00',.59),token('-R$ 351,00'),token('-R$ 350,00',bounds=(10,60,150,90)),
    token('-R$ 350,00',rotation=90),token('-R$ 350,00',bounds=(100,10,240,40))])
def test_weak_or_unrelated_negative_does_not_form_a_quorum(second):
    candidates=[OcrCandidateResult('default','craft',(token('R$ 350,00',.99),)),
        OcrCandidateResult('high_recall','craft',(token('-R$ 350,00'),)),
        OcrCandidateResult('otsu','craft',(second,))]
    kept,exclusions=FUSION_POLICY.with_observed_signs(candidates)
    assert exclusions=={}
    assert kept is FUSION_POLICY


def test_repeated_candidate_id_is_not_two_votes_and_explicit_positive_is_not_removed():
    a=OcrCandidateResult('same','craft',(token('-R$ 350,00'),))
    unsigned=OcrCandidateResult('default','craft',(token('R$ 350,00'),))
    assert FUSION_POLICY.with_observed_signs([a,a,unsigned])[1]=={}
    other=replace(a,candidate_id='otsu')
    explicit=replace(unsigned,tokens=(token('+R$ 350,00'),token('R$ +350,00')))
    assert FUSION_POLICY.with_observed_signs([a,other,explicit])[1]=={}


def test_other_amounts_and_prose_remain_verbatim():
    unrelated=token('R$ 351,00',.99)
    prose=token('O saldo é R$ 350,00',.99,bounds=(10,100,200,130))
    a=OcrCandidateResult('default','craft',(unrelated,prose,token('-R$ 350,00')))
    b=OcrCandidateResult('otsu','craft',(token('-R$ 350,00'),))
    kept,excluded=FUSION_POLICY.with_observed_signs([a,b])
    assert excluded=={}
    assert kept is FUSION_POLICY


@pytest.mark.parametrize('negatives',[(.78,.70),(.90,.70),(.85,.59,.59)])
def test_weak_malformed_negative_markers_do_not_override_well_formed_currency(negatives):
    # Without a well-formed R$ anchor, two moderate reads are not enough.
    candidates=[OcrCandidateResult('default','craft',(token('R$ 350,00',.99),))]+[
        OcrCandidateResult(f'variant{n}','craft',(token('-Rs 350,00',c),)) for n,c in enumerate(negatives)]
    policy,adjustments=FUSION_POLICY.with_observed_signs(candidates)
    assert policy is FUSION_POLICY and adjustments=={}


@pytest.mark.parametrize('negatives',[(.90,.85),(.74,.83,.86)])
def test_strongly_corroborated_minus_survives_a_misread_marker(negatives):
    # Run 008, V6 p11: the minus was read by several variants that all read
    # ``$`` as ``s``; a well-formed but unsigned R$ read erased it.
    candidates=[('default',[token('R$ 350,00',.96)])]+[
        (f'variant{n}',[token('-Rs 350,00',c)]) for n,c in enumerate(negatives)]
    tokens,diag=_best_candidate(candidates)
    assert [t.text for t in tokens]==['-Rs 350,00']
    assert next(d for d in diag if d['candidate_id']=='default')['sign_protected_alternatives']==1
