from __future__ import annotations

from structured_pdf_text.config import (
    ExtractorConfig,
    OcrQualityPolicy,
    effective_ocr_quality_policy,
)
from structured_pdf_text.document import (
    NativeCharacter,
    OcrToken,
    SourceKind,
    TextLine,
    WritingDirection,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.quality import assess_ocr_quality, raw_result_metrics
from structured_pdf_text.text.line_detector import reconstruct_native_lines
from structured_pdf_text.text.lists import extract_list_items
from structured_pdf_text.text.normalize import normalize_reading_text, normalize_text


def _token(text: str, confidence: float | None, x: float = 0.0) -> OcrToken:
    return OcrToken(
        text=text,
        bbox=BBox(x, 0, x + max(1, len(text) * 8), 10),
        confidence=confidence,
        language="pt",
        source=SourceKind.OCR_PAGE,
    )


def test_quality_confidence_is_weighted_by_alphanumeric_characters() -> None:
    assessment = assess_ocr_quality([_token("long readable text", 1.0), _token("x", 0.0, 200)])
    assert assessment.char_weighted_confidence < 1.0
    assert assessment.char_weighted_confidence > 0.85
    assert assessment.low_confidence_character_ratio < 0.10


def test_quality_recommends_recovery_for_suspicious_low_confidence_text() -> None:
    assessment = assess_ocr_quality([_token("!!!!!!", 0.5)])
    assert assessment.recovery_recommended
    assert "suspicious_tokens" in assessment.reasons


def test_raw_metrics_supports_paddle_fields_and_yield() -> None:
    metrics = raw_result_metrics({
        "dt_polys": [1, 2, 3],
        "dt_scores": [0.8, 0.9, 1.0],
        "rec_texts": ["a", "b"],
        "rec_scores": [0.8, 0.9],
        "doc_preprocessor_res": {"angle": 180},
        "textline_orientation_angles": [0, 90],
    })
    assert metrics.detection_count == 3
    assert metrics.recognition_count == 2
    assert metrics.recognition_yield == 2 / 3
    assert metrics.document_orientation == 180
    assert metrics.textline_orientations == (0, 90)


def test_raw_metrics_supports_legacy_records_without_failing() -> None:
    metrics = raw_result_metrics([[[[0, 0], [10, 10]], ["texto", 0.9]]])
    assert metrics.detection_count == 1
    assert metrics.recognition_count == 1


def test_legacy_boolean_maps_to_baseline_and_explicit_policy_survives() -> None:
    assert effective_ocr_quality_policy(ExtractorConfig(ocr_quality_variants=False)) == OcrQualityPolicy.BASELINE
    assert effective_ocr_quality_policy(ExtractorConfig(ocr_quality_policy="exhaustive")) == OcrQualityPolicy.EXHAUSTIVE


def test_native_style_is_propagated_to_text_token() -> None:
    chars = tuple(
        NativeCharacter(
            page_index=0,
            char_index=index,
            text=char,
            unicode_codepoint=ord(char),
            bbox=BBox(index * 10, 0, index * 10 + 8, 10),
            font_name="TestFont",
            font_size=18.0,
            font_weight=700,
            fill_color=(1, 2, 3, 255),
            stroke_color=(4, 5, 6, 255),
            text_render_mode=0,
        )
        for index, char in enumerate("Title")
    )
    line = reconstruct_native_lines(chars)[0]
    token = next(token for token in line.tokens if token.text == "T")
    assert token.font_name == "TestFont"
    assert token.font_size == 18.0
    assert token.font_weight == 700
    assert token.fill_color == (1, 2, 3, 255)


def test_ligatures_expand_only_in_reading_view() -> None:
    value = "o\ufb01ce \u00b2"
    assert "\ufb01" in normalize_text(value)
    assert normalize_reading_text(value) == "ofice \u00b2"


def test_nested_list_items_use_indent_clusters() -> None:
    lines = []
    for index, (text, x) in enumerate((("• primeiro", 10), ("◦ filho", 30), ("• segundo", 10))):
        lines.append(
            TextLine(
                tokens=[],
                bbox=BBox(x, index * 12, x + 80, index * 12 + 10),
                baseline=None,
                direction=WritingDirection.LEFT_TO_RIGHT,
                native_order_min=index,
                native_order_max=index,
            )
        )
        lines[-1].tokens = []
        # TextLine.text derives from tokens; use a minimal token-like object.
        from structured_pdf_text.document import EvidenceRef, TextToken
        lines[-1].tokens = [TextToken(text=text, bbox=lines[-1].bbox, sources=[EvidenceRef(SourceKind.NATIVE_PDF, 0, str(index))], confidence=1.0, normalized_text=text)]
    items = extract_list_items(lines)
    assert [item.level for item in items] == [0, 1, 0]
    assert items[1].text == "filho"
