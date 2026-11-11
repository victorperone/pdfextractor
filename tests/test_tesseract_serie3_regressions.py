"""Regressões da Série 3 (rodada output/tesseract/001).

T3-P01: OCR de página descartado por figuras (página raster inteira e figuras
        marcadas como portadoras de texto).
T3-P03: região menor que um glifo, sem texto, não degrada a página.
"""
from __future__ import annotations

from types import SimpleNamespace

from PIL import Image

from structured_pdf_text.api import _append_hybrid_ocr_regions, _recover_selected_regions
from structured_pdf_text.document import LayoutRegion, RegionDecision, RegionKind, RegionQuality
from structured_pdf_text.geometry import BBox

PAGE = BBox(0, 0, 595, 842)


def _line(x0, y0, x1, y1):
    box = BBox(x0, y0, x1, y1)
    return SimpleNamespace(bbox=box), SimpleNamespace(bbox=box)


def _figure(bbox, decision=None, ocr_lines=None):
    return SimpleNamespace(
        region_id="page-1:region-1",
        kind=RegionKind.FIGURE,
        bbox=bbox,
        ocr_lines=list(ocr_lines or []),
        ocr_tokens=[],
        quality=RegionQuality(decision=decision) if decision else None,
    )


def _supplement(regions):
    return [r for r in regions if r.region_id.endswith(":ocr-supplement")]


# --- T3-P01: figura do tamanho da página em página raster-primary ----------

def test_page_sized_figure_on_raster_primary_page_releases_page_ocr(monkeypatch):
    monkeypatch.setattr("structured_pdf_text.api.full_page_text_region",
                        lambda page_index, bbox, lines, complexity: SimpleNamespace(
                            region_id="", kind=RegionKind.TEXT, bbox=bbox, ocr_lines=[], ocr_tokens=[]))
    page_figure = _figure(BBox(0, 0, 595, 842), RegionDecision.ESCALATE_PAGE_OCR)
    line, token = _line(60, 100, 500, 115)
    regions = [page_figure]

    _append_hybrid_ocr_regions(
        regions=regions, tables=[], unmatched_lines=[line], unmatched_tokens=[token],
        page_index=0, page_bbox=PAGE, complexity=None, page_raster_primary=True,
    )

    (supplement,) = _supplement(regions)
    assert supplement.ocr_lines == [line]
    assert supplement.ocr_tokens == [token]


def test_page_sized_figure_keeps_ownership_when_page_is_not_raster_primary():
    page_figure = _figure(BBox(0, 0, 595, 842), RegionDecision.ESCALATE_PAGE_OCR)
    line, token = _line(60, 100, 500, 115)
    regions = [page_figure]

    _append_hybrid_ocr_regions(
        regions=regions, tables=[], unmatched_lines=[line], unmatched_tokens=[token],
        page_index=0, page_bbox=PAGE, complexity=None, page_raster_primary=False,
    )

    assert regions == [page_figure]
    assert page_figure.ocr_lines == []


def test_small_figure_on_raster_primary_page_is_not_released(monkeypatch):
    monkeypatch.setattr("structured_pdf_text.api.full_page_text_region",
                        lambda page_index, bbox, lines, complexity: SimpleNamespace(
                            region_id="", kind=RegionKind.TEXT, bbox=bbox, ocr_lines=[], ocr_tokens=[]))
    logo = _figure(BBox(50, 50, 150, 120))  # sem decisão de texto: logo/decorativo
    inside, inside_tok = _line(60, 60, 140, 70)
    regions = [logo]

    _append_hybrid_ocr_regions(
        regions=regions, tables=[], unmatched_lines=[inside], unmatched_tokens=[inside_tok],
        page_index=0, page_bbox=PAGE, complexity=None, page_raster_primary=True,
    )

    assert regions == [logo]
    assert logo.ocr_lines == []


# --- T3-P01: figura marcada como portadora de texto recebe o OCR de página ---

def test_text_bearing_figure_receives_page_ocr_inside_it(monkeypatch):
    monkeypatch.setattr("structured_pdf_text.api.full_page_text_region",
                        lambda page_index, bbox, lines, complexity: SimpleNamespace(
                            region_id="", kind=RegionKind.TEXT, bbox=bbox, ocr_lines=[], ocr_tokens=[]))
    stamp = _figure(BBox(120, 180, 310, 250), RegionDecision.OCR_REGION)
    inside, inside_tok = _line(130, 190, 300, 205)
    outside, outside_tok = _line(60, 400, 500, 415)
    regions = [stamp]

    _append_hybrid_ocr_regions(
        regions=regions, tables=[], unmatched_lines=[inside, outside],
        unmatched_tokens=[inside_tok, outside_tok],
        page_index=0, page_bbox=PAGE, complexity=None,
    )

    assert stamp.ocr_lines == [inside]
    assert stamp.ocr_tokens == [inside_tok]
    (supplement,) = _supplement(regions)
    assert supplement.ocr_lines == [outside]  # sem duplicação do texto do carimbo


def test_figure_without_text_decision_does_not_receive_page_ocr():
    logo = _figure(BBox(120, 180, 310, 250), RegionDecision.KEEP_NATIVE)
    inside, inside_tok = _line(130, 190, 300, 205)
    regions = [logo]

    _append_hybrid_ocr_regions(
        regions=regions, tables=[], unmatched_lines=[inside], unmatched_tokens=[inside_tok],
        page_index=0, page_bbox=PAGE, complexity=None,
    )

    assert regions == [logo]
    assert logo.ocr_lines == []


def test_figure_with_own_ocr_binding_is_not_overwritten():
    own, _ = _line(130, 190, 300, 205)
    stamp = _figure(BBox(120, 180, 310, 250), RegionDecision.OCR_REGION, ocr_lines=[own])
    page_line, page_tok = _line(131, 191, 299, 204)
    regions = [stamp]

    _append_hybrid_ocr_regions(
        regions=regions, tables=[], unmatched_lines=[page_line], unmatched_tokens=[page_tok],
        page_index=0, page_bbox=PAGE, complexity=None,
    )

    assert stamp.ocr_lines == [own]
    assert regions == [stamp]


# --- T3-P03: região sub-glifo sem texto ---------------------------------------

class _EmptyEngine:
    class _Identity:
        engine = "tesseract"

    identity = _Identity()
    last_pass_count = 0
    last_batch_count = 0

    def recognize_page(self, image, page_index, page_bbox, **kwargs):
        return []


def _unknown_region(bbox):
    return LayoutRegion(
        region_id="page-22:region-9", kind=RegionKind.UNKNOWN, bbox=bbox,
        layout_confidence=1.0, native_lines=[], ocr_tokens=[],
        quality=RegionQuality(decision=RegionDecision.OCR_REGION,
                              reasons=["native_text_missing", "visible_ink_present"]),
    )


def _stats(region):
    _, _, _, stats = _recover_selected_regions(
        _EmptyEngine(), Image.new("RGB", (842, 595), "white"), page_index=21,
        page_bbox=BBox(0, 0, 842, 595), regions=[region], quality_variants=False,
    )
    return stats[region.region_id]


def test_sub_glyph_region_without_text_is_optional(monkeypatch):
    monkeypatch.setenv("PDFEXTRACTOR_OCR_RGB_BUDGET_MIB_TESSERACT", "100.0")
    stat = _stats(_unknown_region(BBox(219.4, 556.3, 225.4, 562.3)))  # 6x6 pt (V8 p22)
    assert stat["ocr_empty"] is True
    assert stat["ocr_required"] is False
    assert stat["ocr_failed"] is False


def test_text_sized_region_without_text_is_still_a_failure(monkeypatch):
    monkeypatch.setenv("PDFEXTRACTOR_OCR_RGB_BUDGET_MIB_TESSERACT", "100.0")
    stat = _stats(_unknown_region(BBox(100, 100, 300, 114)))  # uma linha de texto
    assert stat["ocr_required"] is True
    assert stat["ocr_failed"] is True
