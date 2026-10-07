from __future__ import annotations

from structured_pdf_text.config import ExtractorConfig, max_quality_extraction_config
from structured_pdf_text.document import (
    LayoutRegion,
    OcrToken,
    PageDiagnostics,
    PageStrategy,
    RegionDecision,
    RegionKind,
    RegionQuality,
    SourceKind,
    StructuredPage,
    TextLine,
    TextToken,
    WritingDirection,
)
from structured_pdf_text.geometry import BBox


def _line(text: str, x0: float, y0: float, width: float, height: float) -> TextLine:
    bbox = BBox(x0, y0, x0 + width, y0 + height)
    token = TextToken(
        text=text,
        bbox=bbox,
        sources=[],
        confidence=0.9,
        normalized_text=text,
        provenance="test",
    )
    return TextLine(
        tokens=[token], bbox=bbox, baseline=None,
        direction=WritingDirection.LEFT_TO_RIGHT,
        native_order_min=None, native_order_max=None,
    )


def _ocr(text: str, bbox: BBox, confidence: float = 0.9) -> OcrToken:
    return OcrToken(
        text=text, bbox=bbox, confidence=confidence, language="pt-BR",
        source=SourceKind.OCR_PAGE,
    )


def test_ocr_bullet_boxes_are_joined_during_line_reconstruction() -> None:
    from structured_pdf_text.ocr.reconstruct import reconstruct_ocr_lines

    tokens = [
        _ocr("•", BBox(10, 10, 16, 20)),
        _ocr("Primeiro item", BBox(22, 10, 115, 20)),
    ]
    lines = reconstruct_ocr_lines(tokens, 4, BBox(0, 0, 200, 100))
    assert len(lines) == 1
    assert lines[0].text == "• Primeiro item"


def test_scanned_page_relayout_splits_heading_list_and_body_regions() -> None:
    from structured_pdf_text.layout.ocr_aware import reconstruct_ocr_layout

    region = LayoutRegion(
        region_id="scan", kind=RegionKind.TEXT, bbox=BBox(0, 0, 300, 500),
        layout_confidence=None, native_lines=[], ocr_tokens=[],
        quality=RegionQuality(RegionDecision.MERGE_OCR),
        ocr_lines=[
            _line("1 Introdução", 30, 30, 170, 20),
            _line("O documento descreve a primeira parte.", 30, 90, 250, 10),
            _line("- Primeiro item", 30, 112, 170, 10),
            _line("Texto final do corpo.", 30, 150, 180, 10),
        ],
    )
    result = reconstruct_ocr_layout([region], BBox(0, 0, 300, 500))
    kinds = [item.kind for item in result]
    assert RegionKind.TITLE in kinds
    assert RegionKind.LIST in kinds
    assert RegionKind.TEXT in kinds
    assert sum(len(item.ocr_lines) for item in result) == 4


def test_scanned_two_column_lines_are_ordered_column_by_column() -> None:
    from structured_pdf_text.layout.ocr_aware import reconstruct_ocr_layout

    region = LayoutRegion(
        region_id="columns", kind=RegionKind.TEXT, bbox=BBox(0, 0, 600, 500),
        layout_confidence=None, native_lines=[], ocr_tokens=[],
        quality=RegionQuality(RegionDecision.MERGE_OCR),
        ocr_lines=[
            _line("Coluna esquerda, linha 1", 25, 40, 230, 10),
            _line("Coluna direita, linha 1", 345, 40, 230, 10),
            _line("Coluna esquerda, linha 2", 25, 100, 230, 10),
            _line("Coluna direita, linha 2", 345, 100, 230, 10),
        ],
    )

    result = reconstruct_ocr_layout([region], BBox(0, 0, 600, 500))
    lines = [line.text for item in result for line in item.ocr_lines]
    assert lines == [
        "Coluna esquerda, linha 1",
        "Coluna esquerda, linha 2",
        "Coluna direita, linha 1",
        "Coluna direita, linha 2",
    ]


def test_ocr_paragraph_assembly_joins_wrapped_lines_and_splits_paragraphs() -> None:
    from structured_pdf_text.assemble.content import _emit_prose_blocks

    lines = [
        _line("Este é um parágrafo cuja", 10, 10, 170, 10),
        _line("segunda linha continua a ideia.", 10, 22, 190, 10),
        _line("Este é outro parágrafo.", 10, 62, 160, 10),
    ]
    region = LayoutRegion(
        region_id="ocr-text", kind=RegionKind.TEXT, bbox=BBox(0, 0, 250, 100),
        layout_confidence=None, native_lines=[], ocr_tokens=[],
        quality=RegionQuality(RegionDecision.MERGE_OCR), ocr_lines=lines,
    )
    blocks = _emit_prose_blocks(lines, region, __import__("structured_pdf_text.document", fromlist=["ContentKind"]).ContentKind.TEXT, 0)
    assert [block.text for block in blocks] == [
        "Este é um parágrafo cuja segunda linha continua a ideia.",
        "Este é outro parágrafo.",
    ]


def test_repeated_ocr_headers_are_detected_across_scanned_pages() -> None:
    from structured_pdf_text.assemble.repeated_regions import detect_repeated_headers_footers

    pages = []
    for page_index in range(2):
        line = _line("Relatório confidencial", 20, 12, 145, 10)
        region = LayoutRegion(
            region_id=f"header-{page_index}", kind=RegionKind.HEADER,
            bbox=line.bbox, layout_confidence=None, native_lines=[], ocr_tokens=[],
            quality=RegionQuality(RegionDecision.MERGE_OCR), ocr_lines=[line],
        )
        pages.append(StructuredPage(
            page_index=page_index, bbox=BBox(0, 0, 200, 300), regions=[region],
            tables=[], raw_text="", reading_text="",
            diagnostics=PageDiagnostics(
                page_index=page_index, strategy=PageStrategy.OCR_CANDIDATE, reasons=[],
                native_chars=0, native_text_length=0,
            ),
        ))

    repeated = detect_repeated_headers_footers(pages)
    assert repeated == {"header:relatório confidencial": [0, 1]}


def test_candidate_fusion_keeps_unique_note_and_selects_conflict_locally() -> None:
    from structured_pdf_text.ocr.candidate_fusion import OcrCandidateFusionEngine, OcrCandidateResult

    body_a = _ocr("Texto do corpo", BBox(0, 0, 120, 15), 0.92)
    body_b = _ocr("Texto do corpc", BBox(0, 0, 120, 15), 0.60)
    footnote = _ocr("Nota pequena", BBox(15, 300, 90, 309), 0.68)
    evidence = OcrCandidateFusionEngine().fuse([
        OcrCandidateResult("craft", "craft_greedy", (body_a,), 0.9, "easyocr"),
        OcrCandidateResult("dbnet", "dbnet", (body_b, footnote), 0.8, "easyocr"),
    ])
    assert {token.text for token in evidence.tokens} == {"Texto do corpo", "Nota pequena"}
    assert evidence.conflict_count == 1


def test_overlapping_tile_views_cover_page_and_keep_pdf_coordinate_mapping() -> None:
    import numpy as np
    from structured_pdf_text.ocr.image_views import tile_image_views

    views = tile_image_views(np.zeros((200, 400, 3), dtype=np.uint8), BBox(10, 20, 210, 120))
    assert len(views) == 4
    assert min(view.source_bbox.x0 for view in views) == 10
    assert max(view.source_bbox.x1 for view in views) == 210
    assert views[0].source_bbox.iou(views[1].source_bbox) > 0
    assert all(view.tile_id for view in views)


def test_max_quality_profile_enables_pipeline_features_and_easyocr_remains_default() -> None:
    config = max_quality_extraction_config()
    assert config.ocr_engine == "easyocr"
    assert config.ocr_tiling
    assert config.enable_critical_data_refinement
    assert config.enable_table_cell_ocr
    assert config.ocr_quality_policy.value == "exhaustive"
    assert ExtractorConfig().ocr_engine == "easyocr"


def test_ocr_capability_extensions_keep_legacy_constructor_compatible() -> None:
    from structured_pdf_text.ocr.contracts import OCRCapabilities

    legacy = OCRCapabilities(True, True, False, False, False, True)
    assert legacy.direct_recognition is False
    assert legacy.polygons is False


def test_critical_data_context_covers_ptbr_identifiers_time_and_currency() -> None:
    from structured_pdf_text.ocr.critical_data import CriticalDataRefiner, DataType

    refiner = CriticalDataRefiner()
    assert refiner.detect_type_from_context(["Inscrição"]) == DataType.CNPJ
    assert refiner.detect_type_from_context(["Acréscimo"]) == DataType.CURRENCY
    assert refiner.detect_type_from_context(["Horário"]) == DataType.TIME
    assert refiner.detect_type_from_context(["CEP"]) == DataType.CEP
    assert refiner.detect_type_from_context(["Processo nº"]) == DataType.PROCESS_NUMBER
    assert refiner.detect_type_from_context(["Nota fiscal"]) == DataType.INVOICE_NUMBER
    assert refiner.score_token(_ocr("23:59", BBox(0, 0, 40, 12)), DataType.TIME) == 1.0
    assert refiner.score_token(_ocr("01001-000", BBox(0, 0, 60, 12)), DataType.CEP) == 1.0
    assert refiner.score_token(_ocr("1.234,56", BBox(0, 0, 70, 12)), DataType.CURRENCY) == 0.85


def test_max_quality_rerenders_small_bottom_notes_directly() -> None:
    from structured_pdf_text.api import _refine_small_footnote_tokens

    class Backend:
        calls = 0

        def recognize_page(self, image, page_index, page_bbox=None, *, quality_policy=None):
            self.calls += 1
            return [_ocr("Nota de rodapé recuperada", BBox(10, 272, 170, 282), 0.95)]

    tokens = [
        _ocr("Corpo um", BBox(10, 50, 90, 65), 0.9),
        _ocr("Corpo dois", BBox(10, 100, 90, 115), 0.9),
        _ocr("Corpo três", BBox(10, 150, 90, 165), 0.9),
        _ocr("Nota", BBox(10, 272, 40, 280), 0.45),
    ]
    backend = Backend()

    refined, count = _refine_small_footnote_tokens(
        engine=backend, page_index=0, page_bbox=BBox(0, 0, 200, 300), tokens=tokens,
        quality_policy="exhaustive", region_renderer=lambda bbox, scale: object(), base_scale=3.0,
    )

    assert count == 1
    assert backend.calls == 1
    assert any(token.text == "Nota de rodapé recuperada" for token in refined)


def test_pdfium_region_render_returns_only_requested_source_area(tmp_path) -> None:
    from reportlab.pdfgen import canvas
    from structured_pdf_text.native.pdfium_source import PdfiumNativeEvidenceSource

    path = tmp_path / "render-region.pdf"
    pdf = canvas.Canvas(str(path), pagesize=(200, 300))
    pdf.drawString(20, 250, "left region")
    pdf.drawString(130, 20, "bottom right")
    pdf.save()
    source = PdfiumNativeEvidenceSource(path)
    with source:
        page = source.extract_page(0)
        image = source.render_region(0, BBox(0, 0, 100, 100), 3.0, page.bbox)
        assert image.width < 200 * 3
        assert image.height < 300 * 3
        assert image.width > 0 and image.height > 0
