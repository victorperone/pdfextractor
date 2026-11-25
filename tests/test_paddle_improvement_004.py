"""Regressões da run Paddle 004 (corpus/PaddleOCRImprovement.md, §9 e §10).

004-P0-01: em páginas mistas cujas colunas são imagens separadas (V9 p8, p31,
           p39, p41...), a reconstrução de linhas juntava as linhas das
           colunas na mesma altura numa linha só, que ficava inteira com a
           primeira figura. ``TextColumnPolicy.confine_to_boxes`` (só Paddle)
           reparte a linha por figura antes da atribuição.
003-P1-01: uma página cujo OCR só viu formas (o motor rejeitou todas as
           observações, V10 selos/gráficos) é ``no_text``, não ``degraded``.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from reportlab.pdfgen import canvas

import structured_pdf_text.api as api
from structured_pdf_text.api import PdfTextExtractor, _append_hybrid_ocr_regions
from structured_pdf_text.config import ExtractorConfig
from structured_pdf_text.document import (
    LayoutRegion, OcrProvenance, OcrToken, RegionDecision, RegionKind, RegionQuality, SourceKind,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.pipeline.text_columns import LEGACY_TEXT_COLUMN_POLICY, TextColumnPolicy
from structured_pdf_text.ocr.pipelines.paddle.pipeline import PaddlePipeline
from structured_pdf_text.ocr.reconstruct import reconstruct_ocr_lines

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from evaluate_e2e import _classify_page_status  # noqa: E402

PAGE = BBox(0, 0, 595, 842)
POLICY = TextColumnPolicy("test-columns", enabled=True)
# V9 p39: três imagens de 149 pt, uma por coluna, separadas por ≈ 19 pt.
COLUMNS = (BBox(55, 213, 204, 499), BBox(223, 213, 372, 499), BBox(391, 213, 540, 499))


def _token(text, x0, y0, x1, y1):
    return OcrToken(text, BBox(x0, y0, x1, y1), 0.98, None, SourceKind.OCR_REGION,
                    ocr_provenance=OcrProvenance(engine="paddle"))


def _column_tokens(rows=8, pitch=11.0):
    """One line-level observation per column line, each inside its own image."""
    return [
        _token(f"c{column}l{row} texto da coluna", box.x0 + 2, 220 + row * pitch, box.x1 - 4, 228 + row * pitch)
        for column, box in enumerate(COLUMNS)
        for row in range(rows)
    ]


def _figure(index, box):
    return LayoutRegion(f"page-1:figure-{index}", RegionKind.FIGURE, box, 0.85, [], [],
                        RegionQuality(decision=RegionDecision.OCR_REGION))


def _first_words(lines):
    return [line.text.split()[0] for line in lines]


# --- 004-P0-01 -----------------------------------------------------------------


def test_row_joined_across_column_images_is_split_per_image():
    tokens = _column_tokens()
    lines = reconstruct_ocr_lines(tokens, 0, PAGE)
    assert any("c0l0" in line.text and "c2l0" in line.text for line in lines)  # o defeito

    split = POLICY.confine_to_boxes(lines, tokens, list(COLUMNS), 0, PAGE)

    assert len(split) == 3 * len(lines)
    for line in split:
        owners = {index for index, box in enumerate(COLUMNS)
                  for token in line.tokens if token.text.strip() and box.x0 <= token.bbox.cx <= box.x1}
        assert len(owners) == 1
    assert _first_words(split[:3]) == ["c0l0", "c1l0", "c2l0"]


def test_lines_inside_one_box_and_disabled_policy_are_untouched():
    tokens = _column_tokens()
    lines = reconstruct_ocr_lines(tokens, 0, PAGE)
    assert LEGACY_TEXT_COLUMN_POLICY.confine_to_boxes(lines, tokens, list(COLUMNS), 0, PAGE) is lines
    single = [token for token in tokens if token.bbox.x1 <= COLUMNS[0].x1]
    single_lines = reconstruct_ocr_lines(single, 0, PAGE)
    kept = POLICY.confine_to_boxes(single_lines, single, list(COLUMNS), 0, PAGE)
    assert all(a is b for a, b in zip(kept, single_lines)) and len(kept) == len(single_lines)
    # Uma só caixa nunca reparte.
    assert POLICY.confine_to_boxes(lines, tokens, [COLUMNS[0]], 0, PAGE) is lines


def test_each_column_image_owns_its_own_lines():
    tokens = _column_tokens()
    lines = reconstruct_ocr_lines(tokens, 0, PAGE)

    def assign(text_columns):
        regions = [_figure(index, box) for index, box in enumerate(COLUMNS, start=1)]
        _append_hybrid_ocr_regions(regions=regions, tables=[], unmatched_lines=list(lines), unmatched_tokens=tokens,
                                   page_index=0, page_bbox=PAGE, complexity=None, text_columns=text_columns)
        return regions

    legacy = assign(LEGACY_TEXT_COLUMN_POLICY)
    assert [len(region.ocr_lines) for region in legacy] == [8, 0, 0]  # comportamento dos demais motores

    regions = assign(PaddlePipeline.text_column_policy)
    assert [len(region.ocr_lines) for region in regions] == [8, 8, 8]
    for column, region in enumerate(regions):
        assert _first_words(region.ocr_lines) == [f"c{column}l{row}" for row in range(8)]
        assert {token.text.split()[0][:2] for token in region.ocr_tokens} == {f"c{column}"}


# --- 003-P1-01 -----------------------------------------------------------------


def _pdf(path: Path) -> Path:
    pdf = canvas.Canvas(str(path), pagesize=(240, 240))
    pdf.drawString(20, 200, "Texto de teste")
    pdf.save()
    return path


class _EmptyBackend:
    def recognize_page(self, image, page_index, page_bbox=None, *, quality_variants=None, quality_policy=None):
        return []

    def recognize_region(self, image, page_index, region_bbox):
        return []


def _engine(observed, admitted, failures=()):
    counters = {"observed_tokens": observed, "admitted_tokens": admitted}
    return SimpleNamespace(last_result=SimpleNamespace(counters=counters, failures=failures))


def _page_status(page):
    facts = page.diagnostics.facts
    return _classify_page_status(str(facts["ocr_outcome"]), facts, bool(page.reading_text.strip()),
                                 int(page.diagnostics.ocr_tokens_added))


def _extract(tmp_path, name):
    config = ExtractorConfig(mode="ocr", language="pt", ocr_quality_variants=False)
    return PdfTextExtractor(config, ocr_engine=_EmptyBackend()).extract(_pdf(tmp_path / name)).pages[0]


def test_abstention_requires_observed_and_no_admitted_evidence():
    assert api._engine_rejected_all_evidence(_engine(3, 0))
    assert not api._engine_rejected_all_evidence(_engine(0, 0))  # nada visto
    assert not api._engine_rejected_all_evidence(_engine(3, 1))  # algo admitido
    assert not api._engine_rejected_all_evidence(_engine(3, 0, failures=("timeout",)))  # falha
    assert not api._engine_rejected_all_evidence(SimpleNamespace())  # motor sem contadores


def test_page_whose_shapes_were_all_rejected_is_no_text_not_degraded(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_engine_rejected_all_evidence", lambda engine: True)
    page = _extract(tmp_path, "a.pdf")

    assert page.diagnostics.facts["ocr_outcome"] == "no_text"
    assert page.diagnostics.facts["ocr_degraded"] is False
    assert "ocr_no_text" not in page.diagnostics.facts["partial_reasons"]
    assert any("rejected every page observation" in warning for warning in page.diagnostics.warnings)
    assert _page_status(page)[1] != "degraded"


def test_page_where_engine_observed_nothing_stays_degraded(tmp_path):
    page = _extract(tmp_path, "b.pdf")

    assert page.diagnostics.facts["ocr_outcome"] == "degraded"
    assert "ocr_no_text" in page.diagnostics.facts["partial_reasons"]
    assert _page_status(page)[1] == "degraded"
