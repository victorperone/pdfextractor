"""Run 009 review: overlapping variant readings and stroke-only disagreements."""
import pytest

from structured_pdf_text.document import OcrProvenance, OcrToken, SourceKind
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.candidate_fusion import OcrCandidateResult, OcrEvidence
from structured_pdf_text.ocr.pipelines.easyocr.segmentation import (
    prefer_stroke_majority, trim_overlapping_readings)


def token(text, bounds, candidate, confidence=.8, rotation=0):
    return OcrToken(text, BBox(*bounds), confidence, 'pt-BR', SourceKind.OCR_PAGE, rotation=rotation,
                    ocr_provenance=OcrProvenance('easyocr', candidate))


def evidence(*tokens):
    return OcrEvidence(tuple(tokens), 2, 0, 0)


def _rotate(bounds, angle, size=1000):
    x0, y0, x1, y1 = bounds  # upright reading frame -> page box of text rotated by ``angle``
    return {0: (x0, y0, x1, y1), 90: (-y1 + size, x0, -y0 + size, x1),
            180: (size - x1, size - y1, size - x0, size - y0), 270: (y0, size - x1, y1, size - x0)}[angle]


@pytest.mark.parametrize('angle', [0, 90, 180, 270])
def test_shared_stretch_of_two_partial_readings_is_printed_once(angle):
    # Run 009, V4 p89: otsu and adaptive_threshold split one printed line differently.
    left = token('"aspas" e caminho C:IQAIlote_089.pdf. A frase final confirma',
                 _rotate((100, 300, 500, 320), angle), 'otsu', rotation=angle)
    right = token('A frase final confirma controle e análise. Nenhuma palavra',
                  _rotate((330, 300, 700, 320), angle), 'adaptive_threshold', rotation=angle)
    fused, trims = trim_overlapping_readings(evidence(left, right))
    assert [t.text for t in fused.tokens] == ['"aspas" e caminho C:IQAIlote_089.pdf.', right.text]
    assert dict(trims) == {'otsu': 1}
    assert fused.tokens[0].ocr_provenance.refinement_kind == 'variant_overlap_trimmed'
    assert fused.tokens[1] is right


def test_accents_spacing_and_punctuation_do_not_hide_the_shared_stretch():
    left = token('a taxa é +07,25% e a data é 13/07/2026', (0, 0, 300, 20), 'otsu')
    right = token('a data e 13/07/2026. Esta amostra combina', (210, 0, 520, 20), 'default')
    fused, _ = trim_overlapping_readings(evidence(left, right))
    assert fused.tokens[0].text == 'a taxa é +07,25% e'


@pytest.mark.parametrize('right_bounds,candidate', [
    ((390, 0, 700, 20), 'default'),    # 10 px cannot hold "A frase final confirma"
    ((330, 0, 700, 20), 'otsu'),       # one variant's own adjacent boxes
    ((330, 40, 700, 60), 'default'),   # the next line
])
def test_legitimate_neighbours_are_not_trimmed(right_bounds, candidate):
    left = token('e caminho C:IQAIlote_089.pdf. A frase final confirma', (0, 0, 400, 20), 'otsu')
    right = token('A frase final confirma controle e análise.', right_bounds, candidate)
    fused, trims = trim_overlapping_readings(evidence(left, right))
    assert not trims and fused.tokens == (left, right)


def test_unrelated_overlap_and_entire_fragments_are_left_to_fusion():
    left = token('Parágrafo 136.02: Ordem e conferência', (0, 0, 300, 20), 'otsu')
    other = token('devem preservar extração e validação', (200, 0, 520, 20), 'default')
    fragment = token('conferência', (230, 0, 300, 20), 'sharpen')
    covering = token('conferência devem preservar', (230, 0, 500, 20), 'grayscale')
    fused, trims = trim_overlapping_readings(evidence(left, other, fragment, covering))
    assert [t.text for t in fused.tokens] == ['Parágrafo 136.02: Ordem e', other.text, fragment.text, covering.text]
    assert dict(trims) == {'otsu': 1}


def _variants(*readings, bounds=(10, 10, 120, 30)):
    return [OcrCandidateResult(f'v{n}', 'craft', (token(text, bounds, f'v{n}', confidence),))
            for n, (text, confidence) in enumerate(readings)]


def test_single_variant_stroke_reading_yields_to_the_majority():
    # Run 009, V6 p21: one ``Semestre Il`` (0.70) against seven ``Semestre II``.
    candidates = _variants(('Semestre Il', .70), *[('Semestre II', c) for c in (.68, .82, .67, .67, .65, .77, .57)])
    fused, adopted = prefer_stroke_majority(evidence(candidates[0].tokens[0]), candidates)
    assert [t.text for t in fused.tokens] == ['Semestre II']
    assert fused.tokens[0].confidence == .82 and dict(adopted) == {'v2': 1}
    assert fused.tokens[0].ocr_provenance.refinement_kind == 'stroke_majority'


@pytest.mark.parametrize('readings', [
    (('00I1', .9), ('O0I1', .6), ('O0I1', .6), ('O0I1', .6)),            # O/0 is not a stroke confusion
    (('Semestre Il', .7), ('Semestre II', .6), ('Semestre II', .6)),      # two votes are not enough
    (('Semestre Il', .7), ('Semestre Il', .6), ('Semestre II', .6), ('Semestre II', .6), ('Semestre II', .6)),
    (('-R$ 1,00', .7), ('R$ 1,00', .9), ('R$ 1,00', .9), ('R$ 1,00', .9)),  # signs keep their own rule
    (('Semestre Il', .7), ('Semestre II', .6), ('Semestre II', .6), ('Semestre I', .6), ('Semestre /', .6),
     ('Semestre', .6), ('Semestre |', .6)),                                # no majority
])
def test_without_a_stroke_only_majority_the_fused_reading_stays(readings):
    candidates = _variants(*readings)
    fused, adopted = prefer_stroke_majority(evidence(candidates[0].tokens[0]), candidates)
    assert not adopted and fused.tokens[0] is candidates[0].tokens[0]
