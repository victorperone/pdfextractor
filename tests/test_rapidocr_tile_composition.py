"""Select complete tile observations without changing text or other engines."""
from dataclasses import replace

import pytest

from structured_pdf_text.document import OcrToken, SourceKind
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.pipeline.policies import RegionalCandidatePolicy
from structured_pdf_text.ocr.pipelines.rapidocr.policy import CANDIDATE_POLICY


def _token(text, box, confidence=.99):
    return OcrToken(text, BBox(*box), confidence, "pt-BR", SourceKind.OCR_REGION)


def _groups(complete, fragment, *others):
    return ((BBox(0, 0, 310, 200), (complete,)),
            (BBox(280, 0, 500, 200), (fragment, *others)))


@pytest.mark.parametrize("amount,fragment", [("R$ 48.000,00", "00,00"),
                                             ("R$ 30.000,00", "00,00"),
                                             ("R$ 78.000,00", "000,00")])
def test_partial_currency_at_overlapping_tile_edge_is_not_appended(amount, fragment):
    full = _token(amount, (249, 50, 304, 65), .997)
    clipped = _token(fragment, (281, 49, 305, 66), .999)
    groups = _groups(full, clipped)
    assert CANDIDATE_POLICY.compose_tiles(groups) == [full]
    assert groups[1][1] == (clipped,)  # raw observation is still available
    assert len(RegionalCandidatePolicy().compose_tiles(groups)) == 2


@pytest.mark.parametrize("change", ["different_value", "low_confidence", "unknown_confidence",
                                    "interior_label", "other_cell", "same_tile"])
def test_uncertain_conflicting_and_independent_observations_are_preserved(change):
    full = _token("R$ 48.000,00", (249, 50, 304, 65))
    clipped = _token("00,00", (281, 49, 305, 66))
    if change == "different_value":
        clipped = replace(clipped, text="01,00")
    elif change == "low_confidence":
        full = replace(full, confidence=.89)
    elif change == "unknown_confidence":
        full = replace(full, confidence=None)
    elif change == "interior_label":
        clipped = replace(clipped, bbox=BBox(289, 50, 303, 65))
        full = replace(full, bbox=BBox(281, 50, 305, 65))
    elif change == "other_cell":
        clipped = replace(clipped, bbox=BBox(281, 90, 305, 106))
    groups = _groups(full, clipped)
    if change == "same_tile":
        groups = ((BBox(0, 0, 500, 200), (full, clipped)),)
    result = CANDIDATE_POLICY.compose_tiles(groups)
    assert len(result) == 2
    assert {id(token) for token in result} == {id(full), id(clipped)}


def test_repeated_zero_in_neighboring_cell_and_complete_negative_value_survive():
    full = _token("R$ -48.000,00", (249, 50, 304, 65))
    clipped = _token("00,00", (281, 49, 305, 66))
    zero = _token("00,00", (350, 49, 380, 66))
    assert {id(token) for token in CANDIDATE_POLICY.compose_tiles(_groups(full, clipped, zero))} == {id(full), id(zero)}
