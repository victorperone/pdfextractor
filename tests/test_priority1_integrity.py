from __future__ import annotations

from structured_pdf_text.document import (
    EvidenceRef,
    SourceKind,
    TextLine,
    TextToken,
    TokenFlag,
    WritingDirection,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.quality import assess_ocr_quality
from structured_pdf_text.text.lists import parse_list_marker, segment_list_lines


def _line(text: str, x: float, y: float) -> TextLine:
    bbox = BBox(x, y, x + max(30.0, len(text) * 6.0), y + 10.0)
    return TextLine(
        tokens=[
            TextToken(
                text=text,
                bbox=bbox,
                sources=[EvidenceRef(SourceKind.NATIVE_PDF, 0, f"line:{y}")],
                confidence=1.0,
                normalized_text=text,
            )
        ],
        bbox=bbox,
        baseline=None,
        direction=WritingDirection.LEFT_TO_RIGHT,
        native_order_min=None,
        native_order_max=None,
    )


def test_list_segmentation_preserves_every_line_and_mixed_prose() -> None:
    lines = [_line("antes", 0, 0), _line("• um", 0, 15), _line("• dois", 0, 30), _line("depois", 0, 60)]
    result = segment_list_lines(lines)
    assert [id(line) for segment in result.segments for line in segment.lines] == [id(line) for line in lines]
    assert result.unassigned_line_count == 0
    assert [segment.is_list for segment in result.segments] == [False, True, False]


def test_ambiguous_single_marker_and_undelimited_number_stay_text() -> None:
    assert parse_list_marker("12 resultado") is None
    result = segment_list_lines([_line("• isolado", 0, 0)])
    assert not result.segments[0].is_list


def test_inferred_nested_item_is_observed_text_with_structural_marker() -> None:
    result = segment_list_lines(
        [_line("• pai", 0, 0), _line("filho", 30, 12), _line("• outro", 0, 25)]
    )
    items = [item for segment in result.segments for item in segment.items]
    assert [item.text for item in items] == ["pai", "filho", "outro"]
    assert items[1].marker_source == "inferred"


def test_synthetic_whitespace_is_not_quality_evidence() -> None:
    from structured_pdf_text.document import TextToken

    metric_tokens = [
        TextToken("texto", BBox(0, 0, 40, 10), [], 0.98, "texto"),
        TextToken(" ", BBox(40, 0, 45, 10), [], 0.0, " ", flags={TokenFlag.WHITESPACE_INFERRED}),
    ]
    # The weak-line path evaluates reconstructed TextTokens, not only raw
    # OcrTokens; inferred separators must not lower that assessment.
    assert assess_ocr_quality(metric_tokens).sufficient
