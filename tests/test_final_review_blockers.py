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


def test_baseline_grouping_keeps_sidebar_word_together() -> None:
    from structured_pdf_text.document import NativeCharacter

    def char(index: int, text: str, x: float, y0: float, y1: float) -> NativeCharacter:
        return NativeCharacter(
            page_index=0,
            char_index=index,
            text=text,
            unicode_codepoint=ord(text),
            bbox=BBox(x, y0, x + 5.0, y1),
            font_size=10.0,
        )

    characters = (
        char(0, "m", 0, 0, 10),
        char(1, "a", 6, 0, 10),
        char(2, "i", 12, 0, 10),
        char(3, "n", 18, 0, 10),
        char(4, "p", 100, 2, 13),
        char(5, "a", 106, 2, 12),
        char(6, "r", 112, 2, 12),
        char(7, "a", 118, 2, 12),
    )

    lines = reconstruct_native_lines(characters)

    assert [line.text for line in lines] == ["main", "para"]


def test_inline_descenders_and_superscripts_join_their_line() -> None:
    from structured_pdf_text.document import NativeCharacter

    def char(index: int, text: str, x: float, y0: float, y1: float) -> NativeCharacter:
        return NativeCharacter(
            page_index=0,
            char_index=index,
            text=text,
            unicode_codepoint=ord(text),
            bbox=BBox(x, y0, x + 4.0, y1),
            font_size=10.0,
        )

    characters = (
        char(0, "r", 0.0, 0.0, 10.0),
        char(1, "e", 5.0, 0.0, 10.0),
        char(2, "p", 10.0, 2.0, 13.0),
        char(3, "e", 15.0, 0.0, 10.0),
        char(4, "t", 20.0, 0.0, 10.0),
        char(5, "i", 25.0, 0.0, 10.0),
        char(6, "ç", 30.0, 2.0, 13.0),
        char(7, "ã", 35.0, 0.0, 10.0),
        char(8, "o", 40.0, 0.0, 10.0),
        char(9, "²", 45.0, -1.0, 4.0),
        char(10, "x", 50.0, 0.0, 10.0),
    )

    lines = reconstruct_native_lines(characters)

    assert [line.text for line in lines] == ["repetição²x"]


def test_compact_symbol_component_inside_line_is_rejoined() -> None:
    characters = tuple(
        [
            NativeCharacter(
                page_index=0,
                char_index=index,
                text=character,
                unicode_codepoint=ord(character),
                bbox=BBox(index * 8.0, 0.0, index * 8.0 + 6.0, 10.0),
                font_size=10.0,
            )
            for index, character in enumerate("abcdefghijklmno")
        ]
        + [
            NativeCharacter(
                page_index=0,
                char_index=15 + index,
                text=character,
                unicode_codepoint=ord(character),
                bbox=BBox(80.0 + index * 7.0, 2.0, 85.0 + index * 7.0, 4.0),
                font_size=10.0,
            )
            for index, character in enumerate("=-_`\"")
        ]
    )

    lines = reconstruct_native_lines(characters)

    assert [line.text for line in lines] == ['abcdefghijklmno=-_`"']


def test_ordinal_symbol_component_inside_line_is_rejoined() -> None:
    characters = tuple(
        [
            NativeCharacter(
                page_index=0,
                char_index=index,
                text=character,
                unicode_codepoint=ord(character),
                bbox=BBox(index * 8.0, 0.0, index * 8.0 + 6.0, 10.0),
                font_size=10.0,
            )
            for index, character in enumerate("valor")
        ]
        + [
            NativeCharacter(
                page_index=0,
                char_index=5 + index,
                text=character,
                unicode_codepoint=ord(character),
                bbox=BBox(40.0 + index * 7.0, 4.5, 45.0 + index * 7.0, 6.5),
                font_size=10.0,
            )
            for index, character in enumerate("¹²³ºª°")
        ]
        + [
            NativeCharacter(
                page_index=0,
                char_index=11 + index,
                text=character,
                unicode_codepoint=ord(character),
                bbox=BBox(85.0 + index * 8.0, 0.0, 91.0 + index * 8.0, 10.0),
                font_size=10.0,
            )
            for index, character in enumerate("texto")
        ]
    )

    lines = reconstruct_native_lines(characters)

    assert [line.text for line in lines] == ["valor¹²³ºª° texto"]


def test_zero_height_leading_space_stays_with_adjacent_native_line() -> None:
    characters = (
        NativeCharacter(
            page_index=0,
            char_index=0,
            text=" ",
            unicode_codepoint=ord(" "),
            bbox=BBox(0.0, 9.4, 4.4, 9.4),
            font_size=8.0,
        ),
        *(
            NativeCharacter(
                page_index=0,
                char_index=index + 1,
                text=character,
                unicode_codepoint=ord(character),
                bbox=BBox(18.0 + index * 5.0, 0.0, 22.0 + index * 5.0, 7.0),
                font_size=8.0,
            )
            for index, character in enumerate("return value")
        ),
    )

    lines = reconstruct_native_lines(characters)

    assert [line.text for line in lines] == [" return value"]


def test_column_gap_split_uses_relative_geometry() -> None:
    from structured_pdf_text.document import NativeCharacter

    def char(index: int, text: str, x: float) -> NativeCharacter:
        return NativeCharacter(
            page_index=0,
            char_index=index,
            text=text,
            unicode_codepoint=ord(text),
            bbox=BBox(x, 0.0, x + 3.0, 10.0),
            font_size=10.0,
        )

    characters = tuple(
        [char(index, text, index * 4.0) for index, text in enumerate("left")]
        + [char(index + 10, text, 36.0 + index * 4.0) for index, text in enumerate("right")]
    )

    lines = reconstruct_native_lines(characters)

    assert [line.text for line in lines] == ["left", "right"]
