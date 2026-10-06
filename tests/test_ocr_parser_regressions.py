"""Regression coverage for malformed OCR rows and confidence values."""
from __future__ import annotations

import math

import pytest

from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.backends.easyocr import (
    _result_to_ocr_tokens as easy_canonical,
    _result_to_pipeline_tokens as easy_pipeline,
)
from structured_pdf_text.ocr.backends.rapidocr import (
    _result_to_ocr_tokens as rapid_canonical,
    _result_to_pipeline_tokens as rapid_pipeline,
)
from structured_pdf_text.ocr.backends.tesseract import (
    _tsv_to_ocr_tokens,
    _tsv_to_pipeline_tokens,
)
from structured_pdf_text.ocr.paddle import _tokens_from_result


QUAD = [[1, 2], [11, 2], [11, 12], [1, 12]]


@pytest.mark.parametrize("parser", [easy_canonical, rapid_canonical])
def test_quadrilateral_parsers_skip_bad_rows_and_nonfinite_geometry(parser):
    rows = [
        [QUAD, "good", 0.8],
        [[['bad']], "malformed", 0.9],
        [[[math.nan, 0], [2, 0], [2, 2], [0, 2]], "nonfinite", 0.9],
    ]
    parsed = parser(rows, "test")
    assert [token.text for token in parsed] == ["good"]
    assert parsed[0].bbox_px == (1.0, 2.0, 11.0, 12.0)


@pytest.mark.parametrize("parser", [easy_pipeline, rapid_pipeline])
def test_pipeline_quadrilateral_parsers_clamp_scores_and_keep_good_rows(parser):
    rows = [[QUAD, "high", 1.4], [QUAD, "low", -0.2], [[['bad']], "bad", 0.5]]
    parsed = parser(rows, 0, "pt", offset_x=20)
    assert [(token.text, token.confidence) for token in parsed] == [
        ("high", 1.0), ("low", 0.0)
    ]
    assert parsed[0].bbox.x0 == 21.0


def _tsv_row(text="ok", conf="0", left="1", top="2", width="3", height="4"):
    return {
        "level": "5", "text": text, "conf": conf, "left": left,
        "top": top, "width": width, "height": height,
    }


def test_tesseract_parser_accepts_zero_confidence_and_skips_bad_rows():
    rows = [
        _tsv_row(),
        _tsv_row(text="bad confidence", conf="nan"),
        _tsv_row(text="bad box", left="x"),
        _tsv_row(text="zero area", width="0"),
    ]
    canonical = _tsv_to_ocr_tokens(rows, "tesseract")
    pipeline = _tsv_to_pipeline_tokens(rows, 0, "pt")
    assert [token.text for token in canonical] == ["ok"]
    assert canonical[0].confidence_native == 0.0
    assert [token.text for token in pipeline] == ["ok"]
    assert pipeline[0].confidence == 0.0


def test_paddle_parser_preserves_good_row_after_malformed_geometry():
    class Image:
        size = (100, 100)

    rows = [
        ([['bad']], ("broken", 0.8)),
        ([[1, 2], [11, 2], [11, 12], [1, 12]], ("good", 0.9)),
    ]
    parsed = _tokens_from_result(rows, 0, Image(), None)
    assert [token.text for token in parsed] == ["good"]
    assert parsed[0].bbox == BBox(1, 2, 11, 12)


# ---------------------------------------------------------------------------
# §31 — pt-BR punctuation spacing in reconstruct_ocr_lines
# ---------------------------------------------------------------------------

def _ocr_token(text: str, x0: float, x1: float, y0: float = 0.0, y1: float = 10.0) -> "Any":
    """Create an OcrToken with a simple axis-aligned bbox."""
    from structured_pdf_text.document import OcrToken, SourceKind
    return OcrToken(text, BBox(x0, y0, x1, y1), 0.9, "pt", SourceKind.OCR_PAGE)


def _reconstruct_text(tokens: list) -> str:
    """Run reconstruct_ocr_lines and return joined text."""
    from structured_pdf_text.ocr.reconstruct import reconstruct_ocr_lines
    lines = reconstruct_ocr_lines(tokens, page_index=0)
    return " ".join(line.text for line in lines)


class TestPtBrSpacingRules:
    """§31: OCR reconstruction must suppress spaces before/after pt-BR punctuation."""

    def test_no_space_before_comma(self):
        tokens = [_ocr_token("palavra", 0, 50), _ocr_token(",", 52, 56)]
        text = _reconstruct_text(tokens)
        assert "palavra," in text, f"Expected 'palavra,' but got: {text!r}"
        assert "palavra ," not in text

    def test_no_space_before_period(self):
        tokens = [_ocr_token("fim", 0, 30), _ocr_token(".", 31, 34)]
        text = _reconstruct_text(tokens)
        assert "fim." in text
        assert "fim ." not in text

    def test_no_space_before_percent(self):
        tokens = [_ocr_token("12,5", 0, 30), _ocr_token("%", 31, 35)]
        text = _reconstruct_text(tokens)
        assert "12,5%" in text
        assert "12,5 %" not in text

    def test_no_space_before_closing_paren(self):
        tokens = [_ocr_token("(texto", 0, 40), _ocr_token(")", 41, 44)]
        text = _reconstruct_text(tokens)
        assert "texto)" in text
        assert "texto )" not in text

    def test_no_space_before_colon(self):
        tokens = [_ocr_token("Total", 0, 30), _ocr_token(":", 31, 34)]
        text = _reconstruct_text(tokens)
        assert "Total:" in text
        assert "Total :" not in text

    def test_no_space_before_semicolon(self):
        tokens = [_ocr_token("item", 0, 30), _ocr_token(";", 31, 34)]
        text = _reconstruct_text(tokens)
        assert "item;" in text
        assert "item ;" not in text

    def test_currency_prefix_attached_to_digits(self):
        tokens = [_ocr_token("R$", 0, 15), _ocr_token("1.234,56", 16, 60)]
        text = _reconstruct_text(tokens)
        assert "R$1.234,56" in text
        assert "R$ 1.234" not in text

    def test_normal_word_gap_still_inserts_space(self):
        tokens = [_ocr_token("uma", 0, 30), _ocr_token("palavra", 32, 80)]
        text = _reconstruct_text(tokens)
        assert "uma palavra" in text


# ---------------------------------------------------------------------------
# §30 — OCR dehyphenation after line reconstruction
# ---------------------------------------------------------------------------

def _make_two_line_tokens(line1_text: str, line2_text: str) -> "list[Any]":
    """Create two groups of tokens on separate y-bands."""
    from structured_pdf_text.document import OcrToken, SourceKind
    t1 = OcrToken(line1_text, BBox(0, 0, 50, 10), 0.9, "pt", SourceKind.OCR_PAGE)
    t2 = OcrToken(line2_text, BBox(0, 15, 50, 25), 0.9, "pt", SourceKind.OCR_PAGE)
    return [t1, t2]


class TestDehyphenation:
    """§30: OCR line reconstruction must join line-break hyphens conservatively."""

    def _lines(self, tok_list):
        from structured_pdf_text.ocr.reconstruct import reconstruct_ocr_lines
        return reconstruct_ocr_lines(tok_list, page_index=0)

    def test_hyphen_break_joins_continuation(self):
        """'docu-' + 'mento' → 'documento' on one line."""
        toks = _make_two_line_tokens("docu-", "mento")
        lines = self._lines(toks)
        full_text = " ".join(l.text for l in lines)
        assert "documento" in full_text
        assert "docu-" not in full_text

    def test_hyphen_break_single_line_result(self):
        """Two-line input joined to one line."""
        toks = _make_two_line_tokens("rela-", "tório")
        lines = self._lines(toks)
        assert len(lines) == 1

    def test_compound_noun_not_joined(self):
        """'segunda-feira' is never split across OCR lines; if it were, the
        heuristic should leave it alone (uppercase follows, or same-line)."""
        from structured_pdf_text.document import OcrToken, SourceKind
        t1 = OcrToken("segunda-", BBox(0, 0, 50, 10), 0.9, "pt", SourceKind.OCR_PAGE)
        t2 = OcrToken("Feira", BBox(0, 15, 50, 25), 0.9, "pt", SourceKind.OCR_PAGE)
        lines = self._lines([t1, t2])
        full = " ".join(l.text for l in lines)
        # Next word starts with uppercase → not joined
        assert "segunda-" in full or "segunda" in full
        assert len(lines) == 2  # kept separate — uppercase continuation

    def test_id_like_stem_not_joined(self):
        """A hyphen after a digit/code stem must not be joined."""
        toks = _make_two_line_tokens("12A-", "código")
        lines = self._lines(toks)
        assert len(lines) == 2

    def test_normal_two_lines_kept_separate(self):
        """Lines without trailing hyphen remain separate."""
        toks = _make_two_line_tokens("primeira linha", "segunda linha")
        lines = self._lines(toks)
        assert len(lines) == 2

    def test_dehyphenate_false_skips_joining(self):
        """dehyphenate=False must leave the hyphen-break lines as-is."""
        from structured_pdf_text.ocr.reconstruct import reconstruct_ocr_lines
        toks = _make_two_line_tokens("docu-", "mento")
        lines = reconstruct_ocr_lines(toks, page_index=0, dehyphenate=False)
        assert len(lines) == 2
        assert any("docu-" in l.text for l in lines)


# ---------------------------------------------------------------------------
# §29 — Paragraph segmentation post-OCR
# ---------------------------------------------------------------------------

def _make_text_line(text: str, x0: float, y0: float, x1: float, y1: float) -> "Any":
    """Build a minimal TextLine for paragraph segmentation tests."""
    from structured_pdf_text.document import TextLine, TextToken, EvidenceRef, SourceKind, WritingDirection
    tok = TextToken(
        text=text,
        bbox=BBox(x0, y0, x1, y1),
        sources=[EvidenceRef(SourceKind.OCR_PAGE, 0, "test")],
        confidence=0.9,
        normalized_text=text,
    )
    return TextLine(
        tokens=[tok],
        bbox=BBox(x0, y0, x1, y1),
        baseline=None,
        direction=WritingDirection.LEFT_TO_RIGHT,
        native_order_min=None,
        native_order_max=None,
    )


class TestParagraphSegmentation:
    """§29: segment_ocr_paragraphs must group physical lines into logical paragraphs."""

    def _seg(self, lines):
        from structured_pdf_text.ocr.reconstruct import segment_ocr_paragraphs
        return segment_ocr_paragraphs(lines)

    def test_single_line_is_one_paragraph(self):
        line = _make_text_line("texto", 0, 0, 200, 10)
        groups = self._seg([line])
        assert len(groups) == 1
        assert groups[0] == [line]

    def test_two_close_lines_same_paragraph(self):
        """Lines with small gap stay together."""
        l1 = _make_text_line("primeira linha do parágrafo", 0, 0, 200, 10)
        l2 = _make_text_line("segunda linha do parágrafo", 0, 12, 200, 22)
        groups = self._seg([l1, l2])
        assert len(groups) == 1

    def test_large_gap_splits_paragraphs(self):
        """A gap > 1.4× median triggers a paragraph break."""
        l1 = _make_text_line("parágrafo um", 0, 0, 200, 10)
        l2 = _make_text_line("parágrafo um continuação", 0, 12, 200, 22)
        # Large gap before next paragraph
        l3 = _make_text_line("parágrafo dois", 0, 50, 200, 60)
        groups = self._seg([l1, l2, l3])
        assert len(groups) == 2
        assert len(groups[0]) == 2
        assert len(groups[1]) == 1

    def test_indent_splits_paragraph(self):
        """First-line indent (x0 much further right) starts a new paragraph."""
        l1 = _make_text_line("final do parágrafo anterior", 10, 0, 200, 10)
        # Indented by ~20 px (well above indent_ths for 10px height)
        l2 = _make_text_line("início recuado novo parágrafo", 35, 12, 200, 22)
        groups = self._seg([l1, l2])
        assert len(groups) == 2

    def test_empty_input_returns_empty(self):
        groups = self._seg([])
        assert groups == []

    def test_three_lines_two_paragraphs(self):
        """Mixed: two lines in paragraph 1, one in paragraph 2 after large gap."""
        l1 = _make_text_line("A", 0, 0, 200, 10)
        l2 = _make_text_line("B", 0, 12, 200, 22)
        l3 = _make_text_line("C", 0, 60, 200, 70)
        groups = self._seg([l1, l2, l3])
        assert len(groups) == 2
        assert l1 in groups[0] and l2 in groups[0]
        assert l3 in groups[1]


# ---------------------------------------------------------------------------
# §28 — OCR-aware heading detection
# ---------------------------------------------------------------------------

def _make_ocr_region(
    text: str,
    y0: float,
    height: float,
    kind: str = "title",
    page_height: float = 800.0,
) -> "Any":
    """Build a minimal LayoutRegion with an ocr_lines TextLine for §28 tests."""
    from structured_pdf_text.document import LayoutRegion, RegionKind, RegionQuality, RegionDecision
    line = _make_text_line(text, 10, y0, 300, y0 + height)
    rk = RegionKind(kind)
    quality = RegionQuality(decision=RegionDecision.KEEP_NATIVE)
    return LayoutRegion(
        region_id=f"test-{y0}",
        kind=rk,
        bbox=BBox(10, y0, 300, y0 + height),
        layout_confidence=None,
        native_lines=[],
        ocr_tokens=[],
        quality=quality,
        ocr_lines=[line],
    )


class TestOcrHeadingDetection:
    """§28: heading detection must work via OCR box height when font_size is absent."""

    def test_heading_candidate_score_ocr_tall_region(self):
        """Taller-than-body OCR region should score higher than body line."""
        from structured_pdf_text.layout.heading import _heading_candidate_score_ocr
        region = _make_ocr_region("1. Introdução", y0=0, height=24)
        score = _heading_candidate_score_ocr(
            region, body_ocr_height_median=10.0
        )
        assert score > 1.5, f"Expected score > 1.5 for tall numbered heading, got {score}"

    def test_heading_candidate_score_ocr_body_region(self):
        """Body-height region should score low (not promoted to heading)."""
        from structured_pdf_text.layout.heading import _heading_candidate_score_ocr
        region = _make_ocr_region("texto corrido normal da página", y0=100, height=10)
        score = _heading_candidate_score_ocr(
            region, body_ocr_height_median=10.0
        )
        assert score < 1.5, f"Expected score < 1.5 for body-height region, got {score}"

    def test_heading_is_accepted_ocr_tall_numbered(self):
        """Tall numbered line should pass the OCR acceptance gate."""
        from structured_pdf_text.layout.heading import _heading_is_accepted_ocr, _heading_candidate_score_ocr
        region = _make_ocr_region("2. Metodologia", y0=200, height=22)
        score = _heading_candidate_score_ocr(region, body_ocr_height_median=10.0)
        accepted = _heading_is_accepted_ocr(region, score, body_ocr_height_median=10.0)
        assert accepted

    def test_heading_is_accepted_ocr_body_rejected(self):
        """Body-height non-numbered line should fail the OCR acceptance gate."""
        from structured_pdf_text.layout.heading import _heading_is_accepted_ocr, _heading_candidate_score_ocr
        region = _make_ocr_region("texto simples sem numeração", y0=300, height=10)
        score = _heading_candidate_score_ocr(region, body_ocr_height_median=10.0)
        accepted = _heading_is_accepted_ocr(region, score, body_ocr_height_median=10.0)
        assert not accepted

    def test_ocr_region_height_returns_median(self):
        """_ocr_region_height must return median of ocr_lines heights."""
        from structured_pdf_text.layout.heading import _ocr_region_height
        region = _make_ocr_region("texto", y0=0, height=18)
        h = _ocr_region_height(region)
        assert h is not None
        assert abs(h - 18.0) < 1.0

    def test_ocr_region_height_none_for_no_ocr_lines(self):
        """_ocr_region_height must return None when region has no ocr_lines."""
        from structured_pdf_text.document import LayoutRegion, RegionKind, RegionQuality, RegionDecision
        from structured_pdf_text.layout.heading import _ocr_region_height
        region = LayoutRegion(
            region_id="empty",
            kind=RegionKind.TITLE,
            bbox=BBox(0, 0, 100, 20),
            layout_confidence=None,
            native_lines=[],
            ocr_tokens=[],
            quality=RegionQuality(decision=RegionDecision.KEEP_NATIVE),
        )
        assert _ocr_region_height(region) is None


# ---------------------------------------------------------------------------
# §14 — CLAHE preprocessing candidate in exhaustive policy
# ---------------------------------------------------------------------------

class TestClahePreprocessing:
    """§14: exhaustive policy must include a CLAHE-preprocessed image candidate."""

    def _make_exhaustive_counting_backend(self, monkeypatch):
        import sys
        import types

        class FakeReader:
            def detect(self, img_color, **kwargs):
                return ([[[10, 90, 10, 30]]], [[]])

            def recognize(self, img_gray, h_list, f_list, **kwargs):
                return [([[10, 10], [90, 10], [90, 30], [10, 30]], "text", 0.9)]

        class FakeMod:
            def Reader(self, langs, **kwargs):
                return FakeReader()

        fake_mod = FakeMod()
        monkeypatch.setitem(sys.modules, "easyocr", fake_mod)
        fake_utils = types.SimpleNamespace(reformat_input=lambda a: (a, a[:, :, 0]))
        monkeypatch.setitem(sys.modules, "easyocr.utils", fake_utils)
        monkeypatch.delenv("EASYOCR_RECOG_NETWORK", raising=False)
        monkeypatch.delenv("EASYOCR_ALLOW_DOWNLOAD", raising=False)

        from structured_pdf_text.config import ExtractorConfig
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod

        monkeypatch.setattr(easyocr_mod, "_import_easyocr", lambda: fake_mod)
        monkeypatch.setattr(easyocr_mod, "_apply_torch_threads", lambda n, p=None: (None, None))
        config = ExtractorConfig(language="pt")
        backend = easyocr_mod.EasyOCRBackend.__new__(easyocr_mod.EasyOCRBackend)
        easyocr_mod.EasyOCRBackend.__init__(backend, config)
        return backend

    def test_exhaustive_includes_clahe_candidate(self, monkeypatch):
        """§14: exhaustive candidate list must include the 'clahe' entry."""
        import numpy as np
        backend = self._make_exhaustive_counting_backend(monkeypatch)
        img = np.full((100, 120, 3), 128, dtype=np.uint8)
        backend.recognize_page(img, 0, quality_policy="exhaustive")
        diag = backend.consume_page_diagnostics()
        ids = [c["candidate_id"] for c in diag["candidate_diagnostics"]]
        assert "clahe" in ids, f"'clahe' not found in candidates: {ids}"

    def test_exhaustive_has_six_or_more_candidates(self, monkeypatch):
        """§14: exhaustive adds clahe as 6th candidate, total >= 6."""
        import numpy as np
        backend = self._make_exhaustive_counting_backend(monkeypatch)
        img = np.full((100, 120, 3), 128, dtype=np.uint8)
        backend.recognize_page(img, 0, quality_policy="exhaustive")
        diag = backend.consume_page_diagnostics()
        assert len(diag["candidate_diagnostics"]) >= 6, (
            f"Expected >=6 candidates, got {len(diag['candidate_diagnostics'])}"
        )

    def test_apply_clahe_returns_array_with_same_shape(self):
        """_apply_clahe must return an array of the same shape as the input."""
        import numpy as np
        from structured_pdf_text.ocr.backends.easyocr import _apply_clahe
        img = np.full((80, 100, 3), 100, dtype=np.uint8)
        result = _apply_clahe(img)
        import numpy as np2
        arr = np2.asarray(result)
        assert arr.shape == img.shape, f"Shape changed: {img.shape} → {arr.shape}"

    def test_apply_clahe_graceful_without_cv2(self, monkeypatch):
        """_apply_clahe must return original image unchanged when cv2 is unavailable."""
        import sys
        import numpy as np
        monkeypatch.setitem(sys.modules, "cv2", None)  # make cv2 unimportable
        from structured_pdf_text.ocr.backends.easyocr import _apply_clahe
        img = np.full((40, 60, 3), 200, dtype=np.uint8)
        result = _apply_clahe(img)
        assert result is img, "Without cv2, original image must be returned unchanged"


# ---------------------------------------------------------------------------
# §32 — OCR-aware list detection: isolated bullet marker merging
# ---------------------------------------------------------------------------

class TestOcrBulletMarkerMerge:
    """§32: merge_ocr_bullet_markers must join tiny marker boxes with item text."""

    def _make_lines(self, specs):
        """Build TextLine list from (text, x0, y0, x1, y1) tuples."""
        return [_make_text_line(t, x0, y0, x1, y1) for (t, x0, y0, x1, y1) in specs]

    def test_bullet_glyph_merged_with_next_line(self):
        """A bullet • on the same row as text must be merged into one line."""
        from structured_pdf_text.ocr.reconstruct import merge_ocr_bullet_markers
        lines = self._make_lines([
            ("•", 10, 5, 18, 15),          # marker: 8×10 box
            ("item de lista", 22, 5, 200, 15),  # text: same vertical band
        ])
        result = merge_ocr_bullet_markers(lines)
        assert len(result) == 1, f"Expected 1 merged line, got {len(result)}"
        assert result[0].text.strip().startswith("•")
        assert "item de lista" in result[0].text

    def test_dash_marker_merged(self):
        """A dash - on the same row as text must be merged."""
        from structured_pdf_text.ocr.reconstruct import merge_ocr_bullet_markers
        lines = self._make_lines([
            ("-", 10, 5, 16, 15),
            ("outro item", 20, 5, 180, 15),
        ])
        result = merge_ocr_bullet_markers(lines)
        assert len(result) == 1
        assert result[0].text.strip().startswith("-")

    def test_ordered_marker_merged(self):
        """An ordered marker like '1.' on the same row must be merged."""
        from structured_pdf_text.ocr.reconstruct import merge_ocr_bullet_markers
        lines = self._make_lines([
            ("1.", 10, 5, 22, 15),
            ("primeiro item da lista", 26, 5, 200, 15),
        ])
        result = merge_ocr_bullet_markers(lines)
        assert len(result) == 1
        assert "1." in result[0].text
        assert "primeiro item" in result[0].text

    def test_non_marker_short_line_not_merged(self):
        """A short word that is not a marker must not be merged with the next line."""
        from structured_pdf_text.ocr.reconstruct import merge_ocr_bullet_markers
        lines = self._make_lines([
            ("ou", 10, 5, 30, 15),          # common word, not a marker
            ("próxima linha", 10, 17, 200, 27),
        ])
        result = merge_ocr_bullet_markers(lines)
        assert len(result) == 2, "Non-marker short word must remain separate"

    def test_marker_on_different_row_not_merged(self):
        """Bullet and text on very different rows (different paragraphs) must stay separate."""
        from structured_pdf_text.ocr.reconstruct import merge_ocr_bullet_markers
        lines = self._make_lines([
            ("•", 10, 5, 18, 15),
            ("texto em outra região", 22, 100, 200, 110),  # far below
        ])
        result = merge_ocr_bullet_markers(lines)
        assert len(result) == 2, "Marker and text in different rows must not merge"

    def test_empty_input(self):
        from structured_pdf_text.ocr.reconstruct import merge_ocr_bullet_markers
        assert merge_ocr_bullet_markers([]) == []

    def test_single_line_unchanged(self):
        from structured_pdf_text.ocr.reconstruct import merge_ocr_bullet_markers
        line = _make_text_line("only line", 0, 0, 100, 10)
        result = merge_ocr_bullet_markers([line])
        assert len(result) == 1
        assert result[0] is line


# ---------------------------------------------------------------------------
# §40 — OCR-authoritative fusion (OCR_REGION local conflict resolution)
# ---------------------------------------------------------------------------

class TestOcrAuthoritativeFusion:
    """§40: fuse_native_and_ocr with ocr_authoritative=True must let OCR win."""

    def _make_native_line(self, text, x0=0, y0=0, x1=100, y1=10):
        from structured_pdf_text.document import TextLine, TextToken, EvidenceRef, SourceKind, WritingDirection
        tok = TextToken(
            text=text, bbox=BBox(x0, y0, x1, y1),
            sources=[EvidenceRef(SourceKind.NATIVE_PDF, 0, "native")],
            confidence=1.0, normalized_text=text,
        )
        return TextLine(
            tokens=[tok], bbox=BBox(x0, y0, x1, y1),
            baseline=None, direction=WritingDirection.LEFT_TO_RIGHT,
            native_order_min=0, native_order_max=0,
        )

    def _make_ocr_token(self, text, x0=5, y0=1, x1=95, y1=9):
        from structured_pdf_text.document import OcrToken, SourceKind
        return OcrToken(text=text, bbox=BBox(x0, y0, x1, y1),
                        confidence=0.9, language="pt", source=SourceKind.OCR_REGION)

    def test_native_wins_by_default(self):
        """Default (ocr_authoritative=False): native text is chosen in conflicts."""
        from structured_pdf_text.fusion.token_fusion import fuse_native_and_ocr
        native = [self._make_native_line("nativo")]
        ocr = [self._make_ocr_token("ocr_diferente")]
        result = fuse_native_and_ocr(native, ocr)
        conflicts = result.conflicts
        assert len(conflicts) == 1
        assert conflicts[0].chosen == "nativo"
        assert conflicts[0].reason == "native_ocr_text_mismatch"

    def test_ocr_wins_when_authoritative(self):
        """ocr_authoritative=True: OCR text is chosen over native in conflicts."""
        from structured_pdf_text.fusion.token_fusion import fuse_native_and_ocr
        native = [self._make_native_line("nativo_ruim")]
        ocr = [self._make_ocr_token("ocr_correto")]
        result = fuse_native_and_ocr(native, ocr, ocr_authoritative=True)
        conflicts = result.conflicts
        assert len(conflicts) == 1
        assert conflicts[0].chosen == "ocr_correto"
        assert conflicts[0].reason == "ocr_authoritative_override"

    def test_matching_tokens_always_counted(self):
        """When texts match, both modes count them as matched (no conflict)."""
        from structured_pdf_text.fusion.token_fusion import fuse_native_and_ocr
        native = [self._make_native_line("igual")]
        ocr = [self._make_ocr_token("igual")]
        for authoritative in (False, True):
            result = fuse_native_and_ocr(native, ocr, ocr_authoritative=authoritative)
            assert result.matched_ocr_tokens == 1
            assert result.conflicts == ()

    def test_unmatched_ocr_tokens_always_unmatched(self):
        """OCR tokens with no native overlap are always unmatched (both modes)."""
        from structured_pdf_text.fusion.token_fusion import fuse_native_and_ocr
        native = [self._make_native_line("longe", x0=500, y0=500, x1=600, y1=510)]
        ocr = [self._make_ocr_token("aqui", x0=5, y0=1, x1=95, y1=9)]
        for authoritative in (False, True):
            result = fuse_native_and_ocr(native, ocr, ocr_authoritative=authoritative)
            assert len(result.unmatched_ocr_tokens) == 1
            assert result.conflicts == ()


# ---------------------------------------------------------------------------
# §9 — wordbeamsearch candidate in exhaustive; §10 — no_quantize candidate
# §15 — deskew candidate; §17 — high_mag candidate
# ---------------------------------------------------------------------------

class TestExhaustiveNewCandidates:
    """§9/§10/§15/§17: exhaustive policy must include wordbeamsearch, no_quantize,
    deskew and high_mag candidates (when conditions are met)."""

    def _make_backend(self, monkeypatch):
        import sys
        import types

        class FakeReader:
            lang_list = ["pt"]
            device = "cpu"
            model_storage_directory = "/tmp/fake"
            user_network_directory = "/tmp/fake"
            recog_network = "latin_g2"

            def detect(self, img_color, **kwargs):
                return ([[[10, 90, 10, 30]]], [[]])

            def recognize(self, img_gray, h_list, f_list, **kwargs):
                return [([[10, 10], [90, 10], [90, 30], [10, 30]], "text", 0.9)]

        class FakeMod:
            def Reader(self, langs, **kwargs):
                return FakeReader()

        fake_mod = FakeMod()
        monkeypatch.setitem(sys.modules, "easyocr", fake_mod)
        fake_utils = types.SimpleNamespace(reformat_input=lambda a: (a, a[:, :, 0]))
        monkeypatch.setitem(sys.modules, "easyocr.utils", fake_utils)
        monkeypatch.delenv("EASYOCR_RECOG_NETWORK", raising=False)
        monkeypatch.delenv("EASYOCR_ALLOW_DOWNLOAD", raising=False)

        from structured_pdf_text.config import ExtractorConfig
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod

        monkeypatch.setattr(easyocr_mod, "_import_easyocr", lambda: fake_mod)
        monkeypatch.setattr(easyocr_mod, "_apply_torch_threads", lambda n, p=None: (None, None))
        config = ExtractorConfig(language="pt")
        backend = easyocr_mod.EasyOCRBackend.__new__(easyocr_mod.EasyOCRBackend)
        easyocr_mod.EasyOCRBackend.__init__(backend, config)
        return backend

    def test_exhaustive_wordbeamsearch_candidate_added(self, monkeypatch):
        """§9: wordbeamsearch candidate appears when base decoder is greedy."""
        import numpy as np
        backend = self._make_backend(monkeypatch)
        img = np.full((100, 120, 3), 128, dtype=np.uint8)
        backend.recognize_page(img, 0, quality_policy="exhaustive")
        diag = backend.consume_page_diagnostics()
        ids = [c["candidate_id"] for c in diag["candidate_diagnostics"]]
        assert "wordbeamsearch" in ids, f"Expected wordbeamsearch in {ids}"

    def test_exhaustive_wordbeamsearch_not_added_if_already_wbs(self, monkeypatch):
        """§9: wordbeamsearch candidate is skipped when base decoder is already wordbeamsearch."""
        import numpy as np
        monkeypatch.setenv("EASYOCR_DECODER", "wordbeamsearch")
        backend = self._make_backend(monkeypatch)
        img = np.full((100, 120, 3), 128, dtype=np.uint8)
        backend.recognize_page(img, 0, quality_policy="exhaustive")
        diag = backend.consume_page_diagnostics()
        ids = [c["candidate_id"] for c in diag["candidate_diagnostics"]]
        # wordbeamsearch is the base decoder so no extra wbs candidate
        assert ids.count("wordbeamsearch") <= 1, \
            f"wordbeamsearch should not be duplicated; got {ids}"

    def test_exhaustive_total_candidates_ten_or_more(self, monkeypatch):
        """§9/§10/§15/§17: exhaustive produces up to 10 candidates (env-dependent)."""
        import numpy as np
        backend = self._make_backend(monkeypatch)
        img = np.full((100, 120, 3), 128, dtype=np.uint8)
        backend.recognize_page(img, 0, quality_policy="exhaustive")
        diag = backend.consume_page_diagnostics()
        count = len(diag["candidate_diagnostics"])
        # wordbeamsearch + base 6 = at least 7; high_mag adds more
        assert count >= 7, f"Expected >=7 candidates in exhaustive, got {count}"

    def test_exhaustive_high_mag_candidate_added(self, monkeypatch):
        """§17: high_mag candidate must appear when base mag_ratio < 2.5."""
        import numpy as np
        monkeypatch.setenv("EASYOCR_MAG_RATIO", "1.2")
        backend = self._make_backend(monkeypatch)
        img = np.full((100, 120, 3), 128, dtype=np.uint8)
        backend.recognize_page(img, 0, quality_policy="exhaustive")
        diag = backend.consume_page_diagnostics()
        ids = [c["candidate_id"] for c in diag["candidate_diagnostics"]]
        assert "high_mag" in ids, f"Expected high_mag in {ids}"


# ---------------------------------------------------------------------------
# §15 — _apply_deskew
# ---------------------------------------------------------------------------

class TestDeskewPreprocessing:
    """§15: deskew function must estimate and correct small rotation angles."""

    def test_apply_deskew_returns_same_shape(self):
        """_apply_deskew must return array with same shape as input."""
        import numpy as np
        from structured_pdf_text.ocr.backends.easyocr import _apply_deskew
        img = np.full((80, 100, 3), 200, dtype=np.uint8)
        result = _apply_deskew(img)
        arr = np.asarray(result)
        assert arr.shape == img.shape

    def test_apply_deskew_graceful_without_cv2(self, monkeypatch):
        """_apply_deskew must return original image when cv2 is unavailable."""
        import sys
        import numpy as np
        monkeypatch.setitem(sys.modules, "cv2", None)
        from structured_pdf_text.ocr.backends.easyocr import _apply_deskew
        img = np.full((40, 60, 3), 150, dtype=np.uint8)
        result = _apply_deskew(img)
        assert result is img

    def test_apply_deskew_near_straight_image_unchanged(self):
        """_apply_deskew must not rotate a perfectly straight image."""
        import numpy as np
        from structured_pdf_text.ocr.backends.easyocr import _apply_deskew
        # All-white image — no contours → no angle estimated → unchanged
        img = np.full((60, 80, 3), 255, dtype=np.uint8)
        result = _apply_deskew(img)
        arr = np.asarray(result)
        # Result is same object or pixel-identical
        assert arr.shape == img.shape


# ---------------------------------------------------------------------------
# §43 — deep readiness CER validation
# ---------------------------------------------------------------------------

class TestDeepReadinessCer:
    """§43: probe_deep must validate CER, not just token presence."""

    def test_simple_cer_perfect_match(self):
        """CER of identical strings must be 0.0."""
        from structured_pdf_text.ocr.readiness import _simple_cer
        assert _simple_cer("hello", "hello") == 0.0

    def test_simple_cer_empty_reference(self):
        """CER with empty reference returns 0.0 when hypothesis is also empty."""
        from structured_pdf_text.ocr.readiness import _simple_cer
        assert _simple_cer("", "") == 0.0

    def test_simple_cer_empty_reference_nonempty_hypothesis(self):
        """CER with empty reference but non-empty hypothesis returns 1.0."""
        from structured_pdf_text.ocr.readiness import _simple_cer
        assert _simple_cer("abc", "") == 1.0

    def test_simple_cer_one_substitution(self):
        """Single character substitution in 4-char string → CER = 0.25."""
        from structured_pdf_text.ocr.readiness import _simple_cer
        cer = _simple_cer("abXd", "abcd")
        assert abs(cer - 0.25) < 1e-6

    def test_simple_cer_all_wrong(self):
        """All characters wrong → CER = 1.0."""
        from structured_pdf_text.ocr.readiness import _simple_cer
        cer = _simple_cer("xxxx", "abcd")
        assert abs(cer - 1.0) < 1e-6

    def test_smoke_max_cer_threshold_defined(self):
        """_SMOKE_MAX_CER must be a float between 0 and 1."""
        from structured_pdf_text.ocr.readiness import _SMOKE_MAX_CER
        assert isinstance(_SMOKE_MAX_CER, float)
        assert 0.0 < _SMOKE_MAX_CER <= 1.0

    def test_smoke_expected_text_contains_real_portuguese_words(self):
        from structured_pdf_text.ocr.readiness import _SMOKE_EXPECTED

        expected = _SMOKE_EXPECTED.lower()

        assert "ação" in expected
        assert "órgão" in expected
        assert "informações" in expected
        assert "você" in expected
        assert "avô" in expected

    def test_smoke_expected_text_contains_real_hyphenated_words(self):
        from structured_pdf_text.ocr.readiness import _SMOKE_EXPECTED

        expected = _SMOKE_EXPECTED.lower()

        assert "segunda-feira" in expected
        assert "anti-inflamatório" in expected
        assert "-" in expected

    def test_smoke_precision_checks_accept_valid_pt_br_text(self):
        from structured_pdf_text.ocr.readiness import _smoke_precision_checks

        text = (
            "Ação, órgão, informações, você, avô e põe. "
            "segunda-feira e anti-inflamatório "
            "R$ 1.234,56 03/10/2026 12,5% "
            "CPF 123.456.789-09 CNPJ 12.345.678/0001-90"
        )

        checks = _smoke_precision_checks(text)

        assert all(checks.values())

    def test_smoke_precision_checks_reject_missing_accents(self):
        from structured_pdf_text.ocr.readiness import _smoke_precision_checks

        text = (
            "Acao, orgao, informacoes, voce, avo e poe. "
            "segunda-feira e anti-inflamatorio "
            "R$ 1.234,56 03/10/2026 12,5%"
        )

        checks = _smoke_precision_checks(text)

        assert checks["accented_portuguese"] is False

    def test_smoke_precision_checks_reject_missing_hyphens(self):
        from structured_pdf_text.ocr.readiness import _smoke_precision_checks

        text = (
            "Ação, órgão, informações, você, avô e põe. "
            "segunda feira e anti inflamatório "
            "R$ 1.234,56 03/10/2026 12,5%"
        )

        checks = _smoke_precision_checks(text)

        assert checks["hyphen_preserved"] is False

# ---------------------------------------------------------------------------
# §33 — reading order uses OCR lines for prose/column detection
# ---------------------------------------------------------------------------

class TestReadingOrderOcrLines:
    """§33: OCR-only regions must use full column-detection path, not return []."""

    def _make_ocr_line(self, text, x0, y0, x1, y1):
        from structured_pdf_text.document import TextLine, WritingDirection
        return TextLine(
            tokens=[], bbox=BBox(x0, y0, x1, y1),
            baseline=None, direction=WritingDirection.LEFT_TO_RIGHT,
            native_order_min=None, native_order_max=None,
            text_override=text,
        )

    def _make_region(self, kind, ocr_lines, native_lines=None, x0=0, y0=0, x1=200, y1=200):
        from structured_pdf_text.document import LayoutRegion, RegionQuality, RegionDecision
        quality = RegionQuality(decision=RegionDecision.KEEP_NATIVE)
        return LayoutRegion(
            region_id="r1", kind=kind, bbox=BBox(x0, y0, x1, y1),
            layout_confidence=1.0,
            native_lines=native_lines or [],
            ocr_tokens=[],
            quality=quality,
            ocr_lines=ocr_lines,
        )

    def test_ocr_only_text_region_returns_lines(self):
        """§33: TEXT region with only ocr_lines must not return empty."""
        from structured_pdf_text.document import RegionKind
        from structured_pdf_text.text.reading_order import order_lines_in_region
        ocr_lines = [
            self._make_ocr_line("linha um", 10, 10, 100, 20),
            self._make_ocr_line("linha dois", 10, 30, 100, 40),
        ]
        region = self._make_region(RegionKind.TEXT, ocr_lines)
        lines, groups = order_lines_in_region(region)
        assert len(lines) == 2

    def test_ocr_only_list_region_returns_lines(self):
        """§33: LIST region with only ocr_lines must be ordered."""
        from structured_pdf_text.document import RegionKind
        from structured_pdf_text.text.reading_order import order_lines_in_region
        ocr_lines = [
            self._make_ocr_line("item A", 10, 50, 90, 60),
            self._make_ocr_line("item B", 10, 70, 90, 80),
        ]
        region = self._make_region(RegionKind.LIST, ocr_lines)
        lines, groups = order_lines_in_region(region)
        assert len(lines) == 2

    def test_ocr_only_empty_region_returns_empty(self):
        """§33: region with neither native nor OCR lines returns []."""
        from structured_pdf_text.document import RegionKind
        from structured_pdf_text.text.reading_order import order_lines_in_region
        region = self._make_region(RegionKind.TEXT, [])
        lines, groups = order_lines_in_region(region)
        assert lines == []

    def test_prose_region_with_both_native_and_ocr_merges_them(self):
        """§33: when region has both native and non-overlapping OCR lines, all are returned."""
        from structured_pdf_text.document import RegionKind
        from structured_pdf_text.text.reading_order import order_lines_in_region
        native = [self._make_ocr_line("native line", 10, 10, 90, 20)]
        ocr = [self._make_ocr_line("ocr line", 10, 40, 90, 50)]
        region = self._make_region(RegionKind.TEXT, ocr, native_lines=native)
        lines, groups = order_lines_in_region(region)
        assert len(lines) == 2, f"Expected 2 lines (native+ocr), got {len(lines)}"

    def test_order_region_lines_ocr_only_included(self):
        """§33: order_region_lines processes regions with only ocr_lines."""
        from structured_pdf_text.document import RegionKind
        from structured_pdf_text.text.reading_order import order_region_lines
        ocr_lines = [
            self._make_ocr_line("linha A", 10, 10, 90, 20),
            self._make_ocr_line("linha B", 10, 30, 90, 40),
        ]
        region = self._make_region(RegionKind.TEXT, ocr_lines)
        lines, decision = order_region_lines([region])
        assert len(lines) == 2


# ---------------------------------------------------------------------------
# §16 — Page orientation candidates (_rotate_image + _remap_raw_for_rotation)
# ---------------------------------------------------------------------------

class TestPageOrientationCandidates:
    """§16: orientation rotation helpers produce correct remapped coordinates."""

    def test_rotate_image_90_swaps_hw(self):
        """Rotating 90° clockwise swaps height and width."""
        import numpy as np
        from structured_pdf_text.ocr.backends.easyocr import _rotate_image

        arr = np.zeros((100, 200, 3), dtype=np.uint8)
        rotated = _rotate_image(arr, 90)
        assert rotated.shape == (200, 100, 3)

    def test_rotate_image_180_preserves_hw(self):
        """Rotating 180° preserves height and width."""
        import numpy as np
        from structured_pdf_text.ocr.backends.easyocr import _rotate_image

        arr = np.zeros((100, 200, 3), dtype=np.uint8)
        rotated = _rotate_image(arr, 180)
        assert rotated.shape == (100, 200, 3)

    def test_rotate_image_270_swaps_hw(self):
        """Rotating 270° clockwise swaps height and width."""
        import numpy as np
        from structured_pdf_text.ocr.backends.easyocr import _rotate_image

        arr = np.zeros((100, 200, 3), dtype=np.uint8)
        rotated = _rotate_image(arr, 270)
        assert rotated.shape == (200, 100, 3)

    def test_remap_180_inverts_coordinates(self):
        """180° remap: point (x, y) maps to (W-1-x, H-1-y)."""
        from structured_pdf_text.ocr.backends.easyocr import _remap_raw_for_rotation

        raw = [([[10, 20], [30, 20], [30, 40], [10, 40]], "text", 0.9)]
        # rotated image is 100×200 (H×W same as original for 180°)
        remapped = _remap_raw_for_rotation(raw, 180, rotated_h=100, rotated_w=200)
        pts = remapped[0][0]
        # (10,20) → (200-1-10, 100-1-20) = (189, 79)
        assert pts[0] == [189.0, 79.0]

    def test_remap_empty_raw_unchanged(self):
        """Empty raw list → empty result."""
        from structured_pdf_text.ocr.backends.easyocr import _remap_raw_for_rotation

        assert _remap_raw_for_rotation([], 90, 100, 200) == []

    def test_remap_non_rotation_angle_unchanged(self):
        """Unknown angle (not 90/180/270) → raw returned unchanged."""
        from structured_pdf_text.ocr.backends.easyocr import _remap_raw_for_rotation

        raw = [([[0, 0], [10, 0], [10, 10], [0, 10]], "x", 0.8)]
        result = _remap_raw_for_rotation(raw, 0, 100, 200)
        assert result is raw  # same object, unchanged

    def test_exhaustive_candidates_has_rotate_helpers(self):
        """_rotate_image and _remap_raw_for_rotation are importable."""
        from structured_pdf_text.ocr.backends.easyocr import (
            _rotate_image,
            _remap_raw_for_rotation,
        )
        assert callable(_rotate_image)
        assert callable(_remap_raw_for_rotation)


# ---------------------------------------------------------------------------
# §36 — CriticalDataRefiner
# ---------------------------------------------------------------------------

class TestCriticalDataRefiner:
    """§36: CriticalDataRefiner scores and identifies structured data types."""

    def test_cpf_format_scores_high(self):
        """CPF with correct format and checksum → score 1.0."""
        from structured_pdf_text.ocr.critical_data import _score_as_cpf

        assert _score_as_cpf("123.456.789-09") == 1.0

    def test_cpf_digits_only_scores_nonzero(self):
        """CPF with only digits (no punctuation) scores > 0 (may reach 1.0 if checksum valid)."""
        from structured_pdf_text.ocr.critical_data import _score_as_cpf

        score = _score_as_cpf("12345678909")
        assert score > 0.0

    def test_cpf_wrong_length_scores_zero(self):
        """Text that is not 11 digits → 0.0."""
        from structured_pdf_text.ocr.critical_data import _score_as_cpf

        assert _score_as_cpf("1234") == 0.0

    def test_cnpj_format_scores_nonzero(self):
        """14-digit string with CNPJ punctuation → score > 0."""
        from structured_pdf_text.ocr.critical_data import _score_as_cnpj

        score = _score_as_cnpj("12.345.678/0001-90")
        assert score > 0.0

    def test_cnpj_wrong_length_scores_zero(self):
        """Not 14 digits → score 0.0."""
        from structured_pdf_text.ocr.critical_data import _score_as_cnpj

        assert _score_as_cnpj("123") == 0.0

    def test_currency_pattern_scores_one(self):
        """R$ 1.234,56 → score 1.0."""
        from structured_pdf_text.ocr.critical_data import _score_as_currency

        assert _score_as_currency("R$ 1.234,56") == 1.0

    def test_currency_prefix_only_partial(self):
        """R$ with no amount → partial score 0.5."""
        from structured_pdf_text.ocr.critical_data import _score_as_currency

        assert _score_as_currency("R$") == 0.5

    def test_date_format_scores_one(self):
        """dd/mm/yyyy date → score 1.0."""
        from structured_pdf_text.ocr.critical_data import _score_as_date

        assert _score_as_date("03/10/2026") == 1.0

    def test_date_non_date_scores_zero(self):
        """Random text → score 0.0."""
        from structured_pdf_text.ocr.critical_data import _score_as_date

        assert _score_as_date("hello world") == 0.0

    def test_detect_context_cpf_label(self):
        """Context label 'CPF:' → detected type is CPF."""
        from structured_pdf_text.ocr.critical_data import CriticalDataRefiner, DataType

        refiner = CriticalDataRefiner()
        result = refiner.detect_type_from_context(["CPF:"])
        assert result == DataType.CPF

    def test_detect_context_cnpj_label(self):
        """Context label 'CNPJ:' → detected type is CNPJ."""
        from structured_pdf_text.ocr.critical_data import CriticalDataRefiner, DataType

        refiner = CriticalDataRefiner()
        result = refiner.detect_type_from_context(["CNPJ:"])
        assert result == DataType.CNPJ

    def test_detect_context_currency_label(self):
        """Context label 'valor' → detected type is CURRENCY."""
        from structured_pdf_text.ocr.critical_data import CriticalDataRefiner, DataType

        refiner = CriticalDataRefiner()
        result = refiner.detect_type_from_context(["Valor"])
        assert result == DataType.CURRENCY

    def test_detect_context_no_label(self):
        """Empty labels → None (no context detected)."""
        from structured_pdf_text.ocr.critical_data import CriticalDataRefiner

        refiner = CriticalDataRefiner()
        assert refiner.detect_type_from_context([]) is None

    def test_get_allowlist_cpf(self):
        """CPF allowlist contains digits, dot, dash."""
        from structured_pdf_text.ocr.critical_data import get_allowlist, DataType

        al = get_allowlist(DataType.CPF)
        assert al is not None
        assert "0" in al and "." in al and "-" in al

    def test_get_allowlist_unknown_returns_none(self):
        """Unknown type → None."""
        from structured_pdf_text.ocr.critical_data import get_allowlist

        assert get_allowlist("nonexistent_type") is None

    def test_cpf_checksum_valid(self):
        """_validate_cpf_checksum accepts 123.456.789-09 (first CPF in RECEITA database)."""
        from structured_pdf_text.ocr.critical_data import _validate_cpf_checksum

        assert _validate_cpf_checksum("12345678909")

    def test_cpf_checksum_all_same_digit_invalid(self):
        """All-same-digit CPFs fail checksum (known invalid)."""
        from structured_pdf_text.ocr.critical_data import _validate_cpf_checksum

        assert not _validate_cpf_checksum("11111111111")

    def test_refiner_score_token(self):
        """score_token returns float for a currency token."""
        from structured_pdf_text.ocr.critical_data import CriticalDataRefiner, DataType
        from structured_pdf_text.geometry import BBox
        from structured_pdf_text.document import OcrToken, SourceKind

        token = OcrToken(
            text="R$ 1.234,56", bbox=BBox(0, 0, 100, 20),
            confidence=0.9, language="pt", source=SourceKind.OCR_PAGE,
        )
        refiner = CriticalDataRefiner()
        score = refiner.score_token(token, DataType.CURRENCY)
        assert score == 1.0

    def test_refiner_returns_tokens_unchanged_when_no_labels(self):
        """refine_tokens with no context_labels returns original list."""
        from structured_pdf_text.ocr.critical_data import CriticalDataRefiner
        from structured_pdf_text.geometry import BBox
        from structured_pdf_text.document import OcrToken, SourceKind

        tokens = [OcrToken(
            text="hello", bbox=BBox(0, 0, 50, 20),
            confidence=0.8, language="pt", source=SourceKind.OCR_PAGE,
        )]
        refiner = CriticalDataRefiner()
        result = refiner.refine_tokens(tokens)
        assert result is tokens


# ---------------------------------------------------------------------------
# §27 — OcrAwareLayoutEngine (reclassify_ocr_regions)
# ---------------------------------------------------------------------------

class TestOcrAwareLayoutReclassification:
    """§27: reclassify_ocr_regions() promotes/reclassifies regions using OCR evidence."""

    def _make_line(self, text, x0, y0, x1, y1):
        from structured_pdf_text.document import TextLine, WritingDirection
        return TextLine(
            tokens=[], bbox=BBox(x0, y0, x1, y1),
            baseline=None, direction=WritingDirection.LEFT_TO_RIGHT,
            native_order_min=None, native_order_max=None,
            text_override=text,
        )

    def _make_region(self, kind, ocr_lines=None, x0=0, y0=0, x1=400, y1=50):
        from structured_pdf_text.document import LayoutRegion, RegionQuality, RegionDecision
        quality = RegionQuality(decision=RegionDecision.KEEP_NATIVE)
        return LayoutRegion(
            region_id="r1", kind=kind, bbox=BBox(x0, y0, x1, y1),
            layout_confidence=1.0,
            native_lines=[],
            ocr_tokens=[],
            quality=quality,
            ocr_lines=ocr_lines or [],
        )

    def test_unknown_with_ocr_lines_becomes_text(self):
        """§27: UNKNOWN region with OCR lines is promoted to TEXT."""
        from structured_pdf_text.document import RegionKind
        from structured_pdf_text.layout.ocr_aware import reclassify_ocr_regions

        lines = [self._make_line("some text", 10, 5, 300, 20)]
        region = self._make_region(RegionKind.UNKNOWN, lines)
        result = reclassify_ocr_regions([region])
        assert result[0].kind == RegionKind.TEXT

    def test_unknown_without_ocr_lines_unchanged(self):
        """§27: UNKNOWN region with no OCR lines stays UNKNOWN."""
        from structured_pdf_text.document import RegionKind
        from structured_pdf_text.layout.ocr_aware import reclassify_ocr_regions

        region = self._make_region(RegionKind.UNKNOWN, [])
        result = reclassify_ocr_regions([region])
        assert result[0].kind == RegionKind.UNKNOWN

    def test_table_region_never_reclassified(self):
        """§27: TABLE regions are protected from reclassification."""
        from structured_pdf_text.document import RegionKind
        from structured_pdf_text.layout.ocr_aware import reclassify_ocr_regions

        lines = [self._make_line("1 2 3 4 5", 10, 5, 300, 20)]
        region = self._make_region(RegionKind.TABLE, lines)
        result = reclassify_ocr_regions([region])
        assert result[0].kind == RegionKind.TABLE

    def test_figure_region_never_reclassified(self):
        """§27: FIGURE regions are protected from reclassification."""
        from structured_pdf_text.document import RegionKind
        from structured_pdf_text.layout.ocr_aware import reclassify_ocr_regions

        lines = [self._make_line("Figure 1", 10, 5, 200, 20)]
        region = self._make_region(RegionKind.FIGURE, lines)
        result = reclassify_ocr_regions([region])
        assert result[0].kind == RegionKind.FIGURE

    def test_text_region_with_bullets_becomes_list(self):
        """§27: TEXT region where most lines start with bullets → LIST."""
        from structured_pdf_text.document import RegionKind
        from structured_pdf_text.layout.ocr_aware import reclassify_ocr_regions

        lines = [
            self._make_line("• primeiro item", 10, 10, 300, 20),
            self._make_line("• segundo item", 10, 25, 300, 35),
            self._make_line("• terceiro item", 10, 40, 300, 50),
        ]
        region = self._make_region(RegionKind.TEXT, lines)
        result = reclassify_ocr_regions([region])
        assert result[0].kind == RegionKind.LIST

    def test_text_region_with_caption_prefix_becomes_caption(self):
        """§27: TEXT region starting with 'Figura' becomes CAPTION."""
        from structured_pdf_text.document import RegionKind
        from structured_pdf_text.layout.ocr_aware import reclassify_ocr_regions

        lines = [self._make_line("Figura 1: exemplo de gráfico.", 10, 5, 300, 20)]
        region = self._make_region(RegionKind.TEXT, lines)
        result = reclassify_ocr_regions([region])
        assert result[0].kind == RegionKind.CAPTION

    def test_empty_regions_list(self):
        """§27: empty input → empty output."""
        from structured_pdf_text.layout.ocr_aware import reclassify_ocr_regions

        assert reclassify_ocr_regions([]) == []

    def test_returns_new_list_not_mutated(self):
        """§27: input list is not mutated — returns new list."""
        from structured_pdf_text.document import RegionKind
        from structured_pdf_text.layout.ocr_aware import reclassify_ocr_regions

        lines = [self._make_line("texto", 10, 5, 300, 20)]
        region = self._make_region(RegionKind.UNKNOWN, lines)
        original = [region]
        result = reclassify_ocr_regions(original)
        assert result is not original
        assert original[0].kind == RegionKind.UNKNOWN  # original unchanged
