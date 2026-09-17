from __future__ import annotations

from types import SimpleNamespace

from structured_pdf_text.assemble.content import assemble_page_content
from structured_pdf_text.config import (
    ExtractorConfig,
    OcrQualityPolicy,
    best_extraction_config,
    effective_ocr_quality_policy,
)
from structured_pdf_text.document import (
    Baseline,
    EvidenceRef,
    LayoutRegion,
    NativeCharacter,
    RegionDecision,
    RegionKind,
    RegionQuality,
    SourceKind,
    StructuredPage,
    TableFragment,
    TextLine,
    TextToken,
    WritingDirection,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.layout.decorative import DecorativeRole, cluster_decorative_lines
from structured_pdf_text.layout.engine import _semantic_predictions
from structured_pdf_text.layout.heading import assign_heading_levels
from structured_pdf_text.renderers.markdown import render_markdown
from structured_pdf_text.text.line_detector import reconstruct_native_lines, spacing_diagnostics
from structured_pdf_text.text.reading_order import order_lines_in_region


def _text_token(text: str, bbox: BBox, *, size: float = 10.0, weight: int | None = None, fill=None) -> TextToken:
    return TextToken(
        text=text,
        bbox=bbox,
        sources=[EvidenceRef(SourceKind.NATIVE_PDF, 0, f"text:{text}:{bbox.x0}:{bbox.y0}")],
        confidence=1.0,
        normalized_text=text,
        font_name="Body",
        font_size=size,
        font_weight=weight,
        fill_color=fill,
    )


def _line(text: str, x: float, y: float, *, size: float = 10.0, weight: int | None = None, fill=None) -> TextLine:
    bbox = BBox(x, y, x + max(20.0, len(text) * 7.0), y + 10.0)
    token = _text_token(text, bbox, size=size, weight=weight, fill=fill)
    return TextLine([token], bbox, Baseline(y=bbox.y1), WritingDirection.LEFT_TO_RIGHT, None, None)


def _region(kind: RegionKind, region_id: str, bbox: BBox, lines: list[TextLine]) -> LayoutRegion:
    return LayoutRegion(
        region_id=region_id,
        kind=kind,
        bbox=bbox,
        layout_confidence=1.0,
        native_lines=lines,
        ocr_tokens=[],
        quality=RegionQuality(RegionDecision.KEEP_NATIVE),
    )


def _page(regions: list[LayoutRegion]) -> StructuredPage:
    return StructuredPage(
        page_index=0,
        bbox=BBox(0, 0, 600, 800),
        regions=regions,
        tables=[],
        raw_text="",
        reading_text="",
        diagnostics=SimpleNamespace(facts={}),
    )


def test_weak_alphanumeric_title_is_demoted_before_assembly() -> None:
    title = _region(RegionKind.TITLE, "title", BBox(100, 150, 180, 160), [_line("Resumo", 100, 150)])
    body = _region(RegionKind.TEXT, "body", BBox(20, 200, 500, 220), [_line("Texto normal", 20, 200)])
    page = assign_heading_levels([_page([title, body])])[0]

    assert page.regions[0].kind == RegionKind.TEXT
    assert page.regions[0].heading_level is None


def test_strong_title_remains_heading_and_weak_title_never_renders_h1() -> None:
    strong = _region(
        RegionKind.TITLE,
        "strong",
        BBox(100, 150, 250, 170),
        [_line("Título forte", 100, 150, size=24, weight=700)],
    )
    weak = _region(RegionKind.TITLE, "weak", BBox(100, 250, 160, 260), [_line("Código", 100, 250)])
    page = assign_heading_levels([_page([strong, weak])])[0]
    result = assemble_page_content(page)
    rendered = render_markdown(
        SimpleNamespace(
            pages=[
                page,
            ],
            diagnostics=SimpleNamespace(facts={"preserve_headers_footers": True}),
        )
    )

    assert page.regions[0].kind == RegionKind.TITLE
    assert page.regions[0].heading_level is not None
    assert page.regions[1].kind == RegionKind.TEXT
    assert all(block.kind.value != "title" or block.heading_level is not None for block in result.blocks)
    assert "# Código" not in rendered


def test_semantic_status_requires_visual_signals_and_is_materialized_once() -> None:
    status_lines = [
        _line("REVISAD", 230, 350, size=18, fill=(20, 20, 20, 255)),
        _line("O", 285, 350, size=18, fill=(20, 20, 20, 255)),
    ]
    normal = _line("curto", 20, 200)
    predictions = _semantic_predictions(
        lines=[*status_lines, normal],
        page_bbox=BBox(0, 0, 600, 800),
        header_lines=[],
        footer_lines=[],
        excluded=[],
    )
    status_predictions = [prediction for prediction in predictions if prediction.semantic_role == "semantic_status"]
    clusters = cluster_decorative_lines([*status_lines, normal], BBox(0, 0, 600, 800))

    assert len(status_predictions) == 1
    assert sum(cluster.role == DecorativeRole.SEMANTIC_STATUS for cluster in clusters) == 1
    assert all(prediction.semantic_role != "semantic_status" for prediction in predictions if prediction.bbox == normal.bbox)


def test_figure_ocr_lines_are_used_by_figure_ordering() -> None:
    figure = _region(RegionKind.FIGURE, "figure", BBox(100, 100, 300, 300), [])
    figure.ocr_lines = [_line("Texto da figura", 120, 140)]

    ordered, _ = order_lines_in_region(figure)

    assert [line.text for line in ordered] == ["Texto da figura"]


def test_visual_table_baseline_refinement_is_disabled() -> None:
    from structured_pdf_text.api import _refine_visual_table_ocr
    from structured_pdf_text.document import StructuredTable, TableCell, TableMethod

    class FailEngine:
        def recognize_page(self, *args, **kwargs):
            raise AssertionError("baseline must not run visual table OCR")

    table = StructuredTable(
        table_id="visual",
        page_fragments=[TableFragment(0, BBox(10, 10, 100, 100), 0, 1)],
        cells=[TableCell(0, 0, 1, 1, BBox(10, 10, 100, 100), "", [], 0.5)],
        column_count=1,
        row_count=1,
        confidence=0.8,
        method=TableMethod.VISUAL_MODEL,
    )
    page = SimpleNamespace(bbox=BBox(0, 0, 200, 200))

    assert _refine_visual_table_ocr(FailEngine(), object(), page, 0, table, [], quality_policy="baseline") is None


def test_effective_policy_matches_best_config_and_legacy_baseline_switch() -> None:
    assert effective_ocr_quality_policy(best_extraction_config()) == OcrQualityPolicy.ADAPTIVE
    assert effective_ocr_quality_policy(
        ExtractorConfig(ocr_quality_variants=False, ocr_quality_policy="exhaustive")
    ) == OcrQualityPolicy.BASELINE


def test_spacing_diagnostics_report_actual_gap_and_order_modes() -> None:
    chars = tuple(
        NativeCharacter(
            page_index=0,
            char_index=index,
            text=char,
            unicode_codepoint=ord(char),
            bbox=BBox(x, 0, x + 5, 10),
            font_size=10,
        )
        for index, (char, x) in enumerate((
            ("A", 0),
            ("B", 6),
            ("C", 20),
            ("D", 21),
            ("E", 35),
        ))
    )
    lines = reconstruct_native_lines(chars)
    diagnostics = spacing_diagnostics(lines)

    assert diagnostics["gap_bimodal_lines"] == 1
    assert diagnostics["gap_fallback_lines"] == 0
    assert diagnostics["native_order_used_lines"] == 1
    assert lines[0].gap_mode == "bimodal"
    assert lines[0].order_mode == "native"


def test_spacing_diagnostics_marks_explicit_whitespace_separately() -> None:
    chars = tuple(
        NativeCharacter(
            page_index=0,
            char_index=index,
            text=char,
            unicode_codepoint=ord(char),
            bbox=BBox(x, 0, x + 5, 10),
            font_size=10,
        )
        for index, (char, x) in enumerate((("A", 0), (" ", 6), ("B", 12)))
    )
    lines = reconstruct_native_lines(chars)
    diagnostics = spacing_diagnostics(lines)

    assert diagnostics["gap_explicit_lines"] == 1
    assert lines[0].gap_mode == "explicit"
