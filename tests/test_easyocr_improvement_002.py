"""Run 002: EasyOCR variant fusion, figure admission, table rereads and cost."""
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from structured_pdf_text.document import OcrToken, SourceKind
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.backends import easyocr as backend
from structured_pdf_text.ocr.candidate_fusion import OcrCandidateFusionEngine, OcrCandidateResult
from structured_pdf_text.ocr.pipeline.contracts import WorkGoal
from structured_pdf_text.ocr.pipelines.easyocr.policy import ADMISSION_POLICY, FUSION_POLICY, TOKEN_REFINEMENT_POLICY


def tok(x0, y0, x1, y1, text, confidence=.95):
    return OcrToken(text, BBox(x0, y0, x1, y1), confidence, "pt-BR", SourceKind.OCR_PAGE)


def fuse(*candidates):
    results = [OcrCandidateResult(label, "craft_greedy", tuple(tokens), score, "easyocr")
               for label, score, tokens in candidates]
    return [token.text for token in OcrCandidateFusionEngine(FUSION_POLICY).fuse(results).tokens]


# -- variant fusion -------------------------------------------------------------

def test_variant_readings_of_the_same_box_are_one_evidence():
    # V3 p90: "OCR-Ooo1OCR-0001" came from two variants of one box.
    assert fuse(("default", .9, [tok(10, 10, 80, 22, "OCR-0001")]),
                ("sharpen", .85, [tok(11, 10, 81, 22, "OCR-Ooo1", .80)])) == ["OCR-0001"]


def test_partial_rereads_inside_a_line_follow_the_more_confident_side():
    # V6 p26: tall variant boxes crossing the watermark line read parts of it.
    line = tok(100, 422, 495, 436, "A chancela de fiscalização integra o conteúdo", .80)
    parts = [tok(288, 414, 365, 453, "scrição diagonal =", .895), tok(319, 420, 364, 445, "diagonãl", .61),
             tok(246, 428, 336, 457, "DE€o", .44)]
    assert fuse(("sharpen", .9, [line]), ("high_recall", .85, parts)) == [line.text]


def test_confident_fragment_does_not_erase_the_rest_of_its_line():
    # V3 p72 (run 002): a confident reread of the line tail suppressed the
    # whole line, losing "O lote OCR-2026-0007 apresenta ação,".
    line = tok(100, 200, 500, 214, "O lote OCR-2026-0007 apresenta ação, informação; coração e saúde.", .70)
    tail = tok(330, 199, 500, 215, "informação; coração e saúde", .95)
    assert fuse(("default", .9, [line]), ("sharpen", .85, [tail])) == [line.text]


def test_next_line_of_skewed_text_is_not_a_fragment():
    # V4 p105 (run 002): skewed boxes are taller than the line pitch, so the
    # next line overlapped a garbled line box and was dropped as its reread.
    line = tok(94, 341, 1628, 407, "sotr2se ve-P105.803509112026-Zo5*amcostea cominaru", .30)
    below = [tok(423, 380, 836, 420, "caminho C:IQAllote_1O5larquivo_01.", .84),
             tok(878, 378, 1018, 403, "A frase final", .89)]
    assert sorted(fuse(("default", .9, [line, *below]))) == sorted([line.text, *(t.text for t in below)])


def test_offset_reread_of_the_same_line_is_still_a_fragment():
    # V4 p113: a variant reread the line start in a box shifted below its middle.
    line = tok(100, 300, 900, 350, "Parágrafo 113.01: Extração e integridade cevem preservar", .80)
    reread = tok(100, 327, 400, 362, "Pardgrafo 113.01: Extraçoo", .60)
    assert fuse(("default", .9, [line]), ("sharpen", .85, [reread])) == [line.text]


def test_unpaired_edge_bracket_does_not_win_on_length():
    # V8 p22/p25: a cell border read as "[" beat the plain header by one character.
    assert fuse(("default", .95, [tok(10, 10, 80, 22, "Situação", .749)]),
                ("high_recall", .9, [tok(9, 9, 81, 23, "[Situação", .997)]),
                ("grayscale", .85, [tok(10, 10, 80, 22, "Situação", 1.0)])) == ["Situação"]
    paired, unpaired = tok(10, 10, 80, 22, "[AB]"), tok(10, 10, 80, 22, "AB]")
    assert FUSION_POLICY.score_token(paired) > FUSION_POLICY.score_token(unpaired)


def test_distinct_words_keep_their_boxes():
    words = [tok(10, 10, 40, 22, "Valor"), tok(45, 10, 80, 22, "total")]
    assert fuse(("default", .9, words), ("sharpen", .85, [tok(10, 10, 80, 22, "Valortotal", .6)])) == ["Valor", "total"]


def test_malformed_real_does_not_replace_a_well_formed_amount():
    # V3 p99: "R$ 12.500,00" became "RS 12.500,00" in the 001.
    assert fuse(("default", .9, [tok(10, 10, 80, 22, "R$ 12.500,00", .85)]),
                ("autocontrast", .85, [tok(10, 10, 80, 22, "RS 12.500,00", .95)])) == ["R$ 12.500,00"]


# -- figure admission -----------------------------------------------------------

def _reject(text, box, confidence=.99, goal=WorkGoal.FIGURE):
    token = SimpleNamespace(text=text, bbox=BBox(*box), confidence_normalized=confidence)
    return ADMISSION_POLICY.rejection_reason(token, SimpleNamespace(goal=goal))


@pytest.mark.parametrize("text,box,confidence", [
    ("0", (0, 0, 13.3, 15.8), .998),   # V7 p75: diagram node
    ("0", (0, 0, 41.1, 14.9), .982),   # V7 p75: connector
    ("9", (0, 0, 7.7, 2.7), .973),     # V5 p17: speck
    (")", (0, 0, 2.0, 5.3), .992),     # V5 p17: lone mark
    ("1", (0, 0, 29.3, 41.3), .043),   # V5 p21: uncertain glyph
])
def test_figure_shapes_are_not_admitted_as_glyphs(text, box, confidence):
    assert _reject(text, box, confidence)


@pytest.mark.parametrize("text,box", [("0", (0, 0, 7, 11)), ("C", (0, 0, 9, 12)), ("10", (0, 0, 14, 11)),
                                       ("Eixo", (0, 0, 30, 11))])
def test_figure_labels_stay_eligible(text, box):
    assert _reject(text, box) is None


def test_admission_only_applies_to_figures():
    assert _reject("0", (0, 0, 41.1, 14.9), goal=WorkGoal.REGION) is None


# -- table reread conservation --------------------------------------------------

def _cell(text, confidences):
    words = text.split() or [""]
    tokens = [SimpleNamespace(text=w, confidence=c) for w, c in zip(words, confidences)]
    return SimpleNamespace(text=text, tokens=tokens, bbox=BBox(0, 0, 100, 40), row=0, col=0, rowspan=1, colspan=1)


def _keeps(old, new):
    return not TOKEN_REFINEMENT_POLICY.accept_visual_table(SimpleNamespace(cells=[old]), SimpleNamespace(cells=[new]))


@pytest.mark.parametrize("old,new", [
    (_cell("TI", [.95]), _cell("Ti", [.95])),                        # V3 p99
    (_cell("Situação", [1.0]), _cell("[Situação 22", [.992, .446])),  # V8 p39
    (_cell("55,25", [.96]), _cell("55.25", [.92])),
])
def test_reread_without_better_evidence_keeps_the_cell(old, new):
    assert _keeps(old, new)


@pytest.mark.parametrize("old,new", [
    (_cell("πI", [.71]), _cell("TI", [.98])),
    (_cell("", []), _cell("39,25", [.5])),
    (_cell("Semestre", [.90]), _cell("Semestre II", [.88, .86])),  # V6 p42
])
def test_reread_with_better_or_added_evidence_replaces_the_cell(old, new):
    assert not _keeps(old, new)


# -- cost: one-line strips are not probed for rotation ----------------------------

def _raw(text, confidence=.95):
    return [([[10, 5], [190, 5], [190, 25], [10, 25]], text, confidence)]


def test_single_line_strip_skips_rotation_probes(monkeypatch):
    run = Mock(return_value=(_raw("R$ 12.500,00"), None))
    monkeypatch.setattr(backend, "_run_easyocr", run)
    monkeypatch.setattr(backend, "_image_preprocessing_candidates", lambda img, adaptive: [])
    kwargs = {"text_threshold": .7, "low_text": .4, "link_threshold": .4, "min_size": 10}
    _, diag = backend._adaptive_with_orientation_selection(None, np.full((30, 200, 3), 255, np.uint8), kwargs, 0, "pt-BR")
    assert diag["selected_angle"] == 0 and len(diag["attempts"]) == 1
    assert diag["attempts"][0]["single_line_strip"] is True
    assert run.call_count == 2  # default + high recall, no 90/180/270 probes


def test_page_crop_with_few_tokens_still_probes_rotations(monkeypatch):
    run = Mock(return_value=(_raw("TEXTO"), None))
    monkeypatch.setattr(backend, "_run_easyocr", run)
    monkeypatch.setattr(backend, "_image_preprocessing_candidates", lambda img, adaptive: [])
    kwargs = {"text_threshold": .7, "low_text": .4, "link_threshold": .4, "min_size": 10}
    _, diag = backend._adaptive_with_orientation_selection(None, np.full((400, 300, 3), 255, np.uint8), kwargs, 0, "pt-BR")
    assert [a["angle"] for a in diag["attempts"]] == [0, 90, 180, 270]
