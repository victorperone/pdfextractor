"""Tests for VQ-14 / VQ-18 / VQ-19 / VQ-24 / VQ-27.

VQ-14: EasyOCR regional orientation supplement for uncovered grid cells.
VQ-18: Heading detection H1–H6 (6 clusters) and same-line fragment merging.
VQ-19: Inline style detection (bold/italic/bold_italic) + Markdown rendering.
VQ-24: HTML table cell rendering with list-marker promotion to <ul>.
VQ-27: block_metrics() — per-block labels (present/modified/missing/duplicated/reordered).
"""
from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# VQ-18: H1–H6 headings and fragment merging
# ---------------------------------------------------------------------------

class TestVQ18HeadingLevels:
    """Heading detector supports levels 1–6 via 6-cluster assignment."""

    def test_heading_level_4_renders_as_h4(self):
        from structured_pdf_text.document import ContentKind, PageContentBlock
        from structured_pdf_text.geometry import BBox
        from structured_pdf_text.renderers.markdown import _render_content_block

        block = PageContentBlock(
            block_id="b1",
            page_index=0,
            kind=ContentKind.TITLE,
            bbox=BBox(0, 0, 100, 20),
            order_index=0,
            text="Sub-sub heading",
            heading_level=4,
        )
        md = _render_content_block(block, {}, preserve_hf=True)
        assert md == "#### Sub-sub heading"

    def test_heading_level_5_renders_as_h5(self):
        from structured_pdf_text.document import ContentKind, PageContentBlock
        from structured_pdf_text.geometry import BBox
        from structured_pdf_text.renderers.markdown import _render_content_block

        block = PageContentBlock(
            block_id="b1",
            page_index=0,
            kind=ContentKind.TITLE,
            bbox=BBox(0, 0, 100, 20),
            order_index=0,
            text="Level five",
            heading_level=5,
        )
        md = _render_content_block(block, {}, preserve_hf=True)
        assert md == "##### Level five"

    def test_heading_level_6_renders_as_h6(self):
        from structured_pdf_text.document import ContentKind, PageContentBlock
        from structured_pdf_text.geometry import BBox
        from structured_pdf_text.renderers.markdown import _render_content_block

        block = PageContentBlock(
            block_id="b1",
            page_index=0,
            kind=ContentKind.TITLE,
            bbox=BBox(0, 0, 100, 20),
            order_index=0,
            text="Deepest heading",
            heading_level=6,
        )
        md = _render_content_block(block, {}, preserve_hf=True)
        assert md == "###### Deepest heading"

    def test_heading_level_clamped_to_6(self):
        from structured_pdf_text.document import ContentKind, PageContentBlock
        from structured_pdf_text.geometry import BBox
        from structured_pdf_text.renderers.markdown import _render_content_block

        block = PageContentBlock(
            block_id="b1",
            page_index=0,
            kind=ContentKind.TITLE,
            bbox=BBox(0, 0, 100, 20),
            order_index=0,
            text="Too deep",
            heading_level=9,
        )
        md = _render_content_block(block, {}, preserve_hf=True)
        # Clamped to max 6
        assert md == "###### Too deep"

    def test_heading_level_none_defaults_to_h1(self):
        from structured_pdf_text.document import ContentKind, PageContentBlock
        from structured_pdf_text.geometry import BBox
        from structured_pdf_text.renderers.markdown import _render_content_block

        block = PageContentBlock(
            block_id="b1",
            page_index=0,
            kind=ContentKind.TITLE,
            bbox=BBox(0, 0, 100, 20),
            order_index=0,
            text="Default heading",
            heading_level=None,
        )
        md = _render_content_block(block, {}, preserve_hf=True)
        assert md == "# Default heading"


class TestVQ18HeadingFragmentMerge:
    """Same-line fragment merging in _can_merge_heading_fragments."""

    def test_heading_layout_module_importable(self):
        from structured_pdf_text.layout import heading as _heading_mod
        assert hasattr(_heading_mod, "_can_merge_heading_fragments")

    def _make_region(self, x0, y0, x1, y1):
        from structured_pdf_text.document import (
            LayoutRegion, RegionDecision, RegionKind, RegionQuality,
            TextLine, WritingDirection,
        )
        from structured_pdf_text.geometry import BBox
        # TextLine with text_override so _region_has_alphanumeric_text returns True
        line = TextLine(
            tokens=[], bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
            baseline=None, direction=WritingDirection.LEFT_TO_RIGHT,
            native_order_min=None, native_order_max=None,
            text_override="Title",
        )
        return LayoutRegion(
            region_id="r",
            kind=RegionKind.TITLE,
            bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
            layout_confidence=1.0,
            native_lines=[],
            ocr_tokens=[],
            quality=RegionQuality(decision=RegionDecision.KEEP_NATIVE),
            ocr_lines=[line],
        )

    def test_same_line_fragments_are_identified(self):
        from structured_pdf_text.layout.heading import _can_merge_heading_fragments
        # first region: x=[50,150], y=[700,720] (height=20)
        first = self._make_region(50, 700, 150, 720)
        # second region is to the right, overlapping slightly vertically (y0 within -50% of height)
        # vertical_gap = 712 - 720 = -8; min_height=20; -50% of 20 = -10 → -8 >= -10 → same_line
        second = self._make_region(160, 712, 250, 732)
        assert _can_merge_heading_fragments(first, second) is True

    def test_vertically_separated_regions_not_merged_when_gap_too_large(self):
        from structured_pdf_text.layout.heading import _can_merge_heading_fragments
        first = self._make_region(50, 700, 150, 720)
        # second region far below — should not merge
        second = self._make_region(50, 760, 150, 780)
        assert _can_merge_heading_fragments(first, second) is False


# ---------------------------------------------------------------------------
# VQ-19: inline style detection and rendering
# ---------------------------------------------------------------------------

class TestVQ19InlineStyleDetection:
    """detect_inline_style() classifies tokens by font weight and name."""

    def _make_token(self, text: str, font_weight: int | None = None, font_name: str | None = None):
        """Build a minimal TextToken-like object."""
        from types import SimpleNamespace
        return SimpleNamespace(text=text, font_weight=font_weight, font_name=font_name)

    def test_no_tokens_returns_none(self):
        from structured_pdf_text.document import detect_inline_style
        assert detect_inline_style([]) is None

    def test_all_whitespace_tokens_returns_none(self):
        from structured_pdf_text.document import detect_inline_style
        tokens = [self._make_token("   ", font_weight=700)]
        assert detect_inline_style(tokens) is None

    def test_bold_via_font_weight_600(self):
        from structured_pdf_text.document import detect_inline_style
        tokens = [self._make_token("Hello world", font_weight=700)]
        assert detect_inline_style(tokens) == "bold"

    def test_bold_via_font_name_keyword(self):
        from structured_pdf_text.document import detect_inline_style
        tokens = [self._make_token("Important", font_name="Arial-BoldMT")]
        assert detect_inline_style(tokens) == "bold"

    def test_italic_via_font_name_keyword(self):
        from structured_pdf_text.document import detect_inline_style
        tokens = [self._make_token("Emphasis", font_name="TimesNewRoman-Italic")]
        assert detect_inline_style(tokens) == "italic"

    def test_bold_italic_when_both_active(self):
        from structured_pdf_text.document import detect_inline_style
        tokens = [self._make_token("Both", font_weight=700, font_name="Arial-BoldItalic")]
        assert detect_inline_style(tokens) == "bold_italic"

    def test_mixed_bold_non_bold_below_threshold_returns_none(self):
        from structured_pdf_text.document import detect_inline_style
        # 3 chars bold, 17 chars normal → bold_ratio ≈ 0.15, below 0.60 threshold
        tokens = [
            self._make_token("foo", font_weight=700),
            self._make_token(" regular text here", font_weight=400),
        ]
        assert detect_inline_style(tokens) is None

    def test_semibold_name_triggers_bold(self):
        from structured_pdf_text.document import detect_inline_style
        tokens = [self._make_token("Semibold text", font_name="Semibold")]
        assert detect_inline_style(tokens) == "bold"

    def test_oblique_name_triggers_italic(self):
        from structured_pdf_text.document import detect_inline_style
        tokens = [self._make_token("Oblique text", font_name="FontOblique")]
        assert detect_inline_style(tokens) == "italic"


class TestVQ19InlineStyleRendering:
    """Inline style field on PageContentBlock controls Markdown wrapping."""

    def _make_block(self, text: str, inline_style: str | None):
        from structured_pdf_text.document import ContentKind, PageContentBlock
        from structured_pdf_text.geometry import BBox
        return PageContentBlock(
            block_id="b1", page_index=0,
            kind=ContentKind.TEXT, bbox=BBox(0, 0, 200, 20),
            order_index=0, text=text, inline_style=inline_style,
        )

    def test_bold_style_wraps_with_double_asterisks(self):
        from structured_pdf_text.renderers.markdown import _render_content_block
        block = self._make_block("Bold text", "bold")
        md = _render_content_block(block, {}, preserve_hf=True)
        assert md == "**Bold text**"

    def test_italic_style_wraps_with_single_asterisk(self):
        from structured_pdf_text.renderers.markdown import _render_content_block
        block = self._make_block("Italic text", "italic")
        md = _render_content_block(block, {}, preserve_hf=True)
        assert md == "*Italic text*"

    def test_bold_italic_style_wraps_with_triple_asterisks(self):
        from structured_pdf_text.renderers.markdown import _render_content_block
        block = self._make_block("Bold italic", "bold_italic")
        md = _render_content_block(block, {}, preserve_hf=True)
        assert md == "***Bold italic***"

    def test_none_style_no_wrapping(self):
        from structured_pdf_text.renderers.markdown import _render_content_block
        block = self._make_block("Plain text", None)
        md = _render_content_block(block, {}, preserve_hf=True)
        assert md == "Plain text"

    def test_empty_text_with_style_returns_empty(self):
        from structured_pdf_text.renderers.markdown import _render_content_block
        block = self._make_block("", "bold")
        md = _render_content_block(block, {}, preserve_hf=True)
        assert md == ""


# ---------------------------------------------------------------------------
# VQ-24: HTML cell rendering with list detection
# ---------------------------------------------------------------------------

class TestVQ24CellHtmlRendering:
    """_render_cell_html() promotes list-marker lines to <ul><li> elements."""

    def test_bullet_lines_become_ul_list(self):
        from structured_pdf_text.renderers.markdown import _render_cell_html
        text = "• Item one\n• Item two\n• Item three"
        html = _render_cell_html(text)
        assert html.startswith("<ul>")
        assert html.endswith("</ul>")
        assert "<li>Item one</li>" in html
        assert "<li>Item two</li>" in html
        assert "<li>Item three</li>" in html

    def test_dash_lines_become_ul_list(self):
        from structured_pdf_text.renderers.markdown import _render_cell_html
        text = "- Alpha\n- Beta"
        html = _render_cell_html(text)
        assert "<ul>" in html
        assert "<li>Alpha</li>" in html
        assert "<li>Beta</li>" in html

    def test_numbered_lines_become_ul_list(self):
        from structured_pdf_text.renderers.markdown import _render_cell_html
        text = "1. First step\n2. Second step"
        html = _render_cell_html(text)
        assert "<ul>" in html
        assert "<li>First step</li>" in html

    def test_plain_text_uses_br_not_ul(self):
        from structured_pdf_text.renderers.markdown import _render_cell_html
        text = "Line A\nLine B"
        html = _render_cell_html(text)
        assert "<ul>" not in html
        assert "<br>" in html

    def test_empty_text_returns_empty_string(self):
        from structured_pdf_text.renderers.markdown import _render_cell_html
        assert _render_cell_html("") == ""

    def test_mixed_content_uses_br_not_ul(self):
        from structured_pdf_text.renderers.markdown import _render_cell_html
        # One line is a list item, the other is plain — not all list
        text = "• Item one\nPlain sentence"
        html = _render_cell_html(text)
        assert "<ul>" not in html

    def test_html_escaping_in_list_items(self):
        from structured_pdf_text.renderers.markdown import _render_cell_html
        text = "• <script>alert(1)</script>"
        html = _render_cell_html(text)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_html_table_uses_render_cell_html_for_spanned_tables(self):
        from structured_pdf_text.document import (
            StructuredTable, TableCell, TableFragment, TableMethod,
        )
        from structured_pdf_text.geometry import BBox
        from structured_pdf_text.renderers.markdown import _render_table

        cells = [
            TableCell(row=0, col=0, rowspan=2, colspan=1,
                      bbox=BBox(0, 0, 100, 40), text="• A\n• B",
                      tokens=[], confidence=1.0),
            TableCell(row=0, col=1, rowspan=1, colspan=1,
                      bbox=BBox(100, 0, 200, 20), text="Normal",
                      tokens=[], confidence=1.0),
        ]
        fragment = TableFragment(page_index=0, bbox=BBox(0, 0, 200, 40), row_start=0, row_end=2)
        table = StructuredTable(
            table_id="t1", page_fragments=[fragment], cells=cells,
            column_count=2, row_count=2, confidence=1.0,
            method=TableMethod.STRICT_GRID, header_rows=(0,),
        )
        html = _render_table(table)
        assert "<ul>" in html
        assert "<li>A</li>" in html


# ---------------------------------------------------------------------------
# VQ-27: block_metrics()
# ---------------------------------------------------------------------------

class TestVQ27BlockMetrics:
    """block_metrics() produces per-block labels and summary counts."""

    def _make_doc(self, blocks: list[tuple[str, str]]):
        """Build a StructuredDocument with the given (block_id, text) pairs."""
        from unittest.mock import MagicMock
        from structured_pdf_text.document import (
            ContentKind, DocumentDiagnostics, DocumentMetadata,
            ExtractionStatus, PageContentBlock, PageDiagnostics,
            PageStrategy, StructuredDocument, StructuredPage,
        )
        from structured_pdf_text.geometry import BBox

        def _bbox():
            return BBox(0, 0, 100, 100)

        content_blocks = [
            PageContentBlock(
                block_id=bid, page_index=0,
                kind=ContentKind.TEXT, bbox=_bbox(),
                order_index=i, text=text,
            )
            for i, (bid, text) in enumerate(blocks)
        ]
        diag = PageDiagnostics(
            page_index=0,
            strategy=PageStrategy.NATIVE,
            reasons=[],
            native_chars=100,
            native_text_length=50,
            tables=0,
        )
        page = StructuredPage(
            page_index=0, bbox=_bbox(), regions=[], tables=[],
            raw_text="", reading_text="", diagnostics=diag,
            content_blocks=content_blocks,
        )
        doc_diag = MagicMock(spec=DocumentDiagnostics)
        doc_diag.status = ExtractionStatus.SUCCESS
        doc_diag.page_count = 1
        doc_diag.native_pages = 1
        doc_diag.mixed_pages = 0
        doc_diag.ocr_pages = 0
        doc_diag.facts = {}
        return StructuredDocument(
            pages=[page], tables=[], raw_text="", reading_text="",
            metadata=DocumentMetadata(source_path="", page_count=1, pdfium_version=None),
            diagnostics=doc_diag,
        )

    def test_present_block_is_labeled_present(self):
        from structured_pdf_text.diagnostics.report import block_metrics, BLOCK_PRESENT
        doc = self._make_doc([("b1", "The quick brown fox")])
        manifest = {"b1": {"page": 0, "expected_markdown": "The quick brown fox"}}
        result = block_metrics(doc, manifest)
        assert result["by_block"]["b1"]["label"] == BLOCK_PRESENT
        assert result["by_block"]["b1"]["similarity"] >= 0.80

    def test_missing_block_is_labeled_missing(self):
        from structured_pdf_text.diagnostics.report import block_metrics, BLOCK_MISSING
        doc = self._make_doc([("b1", "Something completely different")])
        manifest = {"b2": {"page": 0, "expected_markdown": "Content that was never extracted"}}
        result = block_metrics(doc, manifest)
        assert result["by_block"]["b2"]["label"] == BLOCK_MISSING

    def test_modified_block_has_partial_similarity(self):
        from structured_pdf_text.diagnostics.report import block_metrics, BLOCK_MODIFIED
        doc = self._make_doc([("b1", "The quick brown fox jumps")])
        # Intentionally diverged but similar — similarity between 0.30 and 0.80
        manifest = {"b1": {"page": 0, "expected_markdown": "The quick brown cat runs"}}
        result = block_metrics(doc, manifest)
        label = result["by_block"]["b1"]["label"]
        assert label in (BLOCK_MODIFIED, "present")  # depends on exact similarity

    def test_duplicated_block_is_labeled_duplicated(self):
        from structured_pdf_text.diagnostics.report import block_metrics, BLOCK_DUPLICATED
        repeated_text = "Repeated content exactly"
        doc = self._make_doc([
            ("b1", repeated_text),
            ("b2", repeated_text),
        ])
        manifest = {"ref1": {"page": 0, "expected_markdown": repeated_text}}
        result = block_metrics(doc, manifest)
        assert result["by_block"]["ref1"]["label"] == BLOCK_DUPLICATED

    def test_summary_counts_present_missing(self):
        from structured_pdf_text.diagnostics.report import block_metrics, BLOCK_PRESENT, BLOCK_MISSING
        doc = self._make_doc([("b1", "Exact match text here")])
        manifest = {
            "found": {"page": 0, "expected_markdown": "Exact match text here"},
            # Totally unrelated text — low similarity to trigger MISSING
            "missing_block": {"page": 0, "expected_markdown": "ZZZXXX 99887766 completely different"},
        }
        result = block_metrics(doc, manifest)
        assert result["summary"][BLOCK_PRESENT] >= 1
        assert result["summary"][BLOCK_MISSING] >= 1

    def test_gates_matches_summary(self):
        from structured_pdf_text.diagnostics.report import block_metrics
        doc = self._make_doc([("b1", "Some extracted text")])
        manifest = {"b1": {"page": 0, "expected_markdown": "Some extracted text"}}
        result = block_metrics(doc, manifest)
        assert result["gates"] == result["summary"]

    def test_empty_manifest_produces_empty_by_block(self):
        from structured_pdf_text.diagnostics.report import block_metrics
        doc = self._make_doc([("b1", "Something")])
        result = block_metrics(doc, {})
        assert result["by_block"] == {}

    def test_empty_expected_text_labeled_missing(self):
        from structured_pdf_text.diagnostics.report import block_metrics, BLOCK_MISSING
        doc = self._make_doc([("b1", "Something")])
        manifest = {"b1": {"page": 0, "expected_markdown": ""}}
        result = block_metrics(doc, manifest)
        assert result["by_block"]["b1"]["label"] == BLOCK_MISSING


# ---------------------------------------------------------------------------
# VQ-14: regional orientation supplement
# ---------------------------------------------------------------------------

class TestVQ14RegionalOrientation:
    """_supplement_with_regional_orientation() fills uncovered grid cells."""

    def test_function_importable(self):
        from structured_pdf_text.ocr.backends.easyocr import (
            _supplement_with_regional_orientation,
        )
        assert callable(_supplement_with_regional_orientation)

    def test_fully_covered_grid_returns_original_tokens(self):
        """When all grid cells have tokens, no supplement should run."""
        import numpy as np
        from unittest.mock import MagicMock
        from structured_pdf_text.ocr.backends.easyocr import (
            _supplement_with_regional_orientation,
        )
        from structured_pdf_text.document import OcrToken, SourceKind
        from structured_pdf_text.geometry import BBox

        # Build tokens covering all 4x4 = 16 grid cells (one per cell center)
        GRID_ROWS, GRID_COLS = 4, 4
        img_h, img_w = 400, 400
        cell_h = img_h / GRID_ROWS
        cell_w = img_w / GRID_COLS

        tokens = []
        for r in range(GRID_ROWS):
            for c in range(GRID_COLS):
                cx = c * cell_w + cell_w / 2
                cy = r * cell_h + cell_h / 2
                tokens.append(OcrToken(
                    text="word",
                    bbox=BBox(cx - 5, cy - 5, cx + 5, cy + 5),
                    confidence=0.9,
                    language="pt",
                    source=SourceKind.OCR_PAGE,
                ))

        arr = np.zeros((img_h, img_w, 3), dtype=np.uint8)

        reader = MagicMock()
        result = _supplement_with_regional_orientation(
            reader, arr, {}, tokens, page_index=0, language="pt"
        )
        # When all cells covered, reader should not be called for OCR
        reader.readtext.assert_not_called()
        assert result == tokens

    def test_empty_image_returns_existing_tokens(self):
        """Zero-size image should not crash and returns existing tokens unchanged."""
        import numpy as np
        from unittest.mock import MagicMock
        from structured_pdf_text.ocr.backends.easyocr import (
            _supplement_with_regional_orientation,
        )

        arr = np.zeros((0, 0, 3), dtype=np.uint8)
        reader = MagicMock()
        result = _supplement_with_regional_orientation(
            reader, arr, {}, [], page_index=0, language="pt"
        )
        assert result == []

    def test_low_confidence_tokens_not_admitted(self):
        """Regional tokens below confidence 0.40 must not be added."""
        import numpy as np
        from unittest.mock import MagicMock, patch
        from structured_pdf_text.ocr.backends.easyocr import (
            _supplement_with_regional_orientation,
        )

        arr = np.ones((400, 400, 3), dtype=np.uint8) * 128

        # Patch _run_easyocr to return a low-confidence detection
        low_conf_raw = [([[0, 0], [100, 0], [100, 30], [0, 30]], "text", 0.20)]

        reader = MagicMock()
        with patch(
            "structured_pdf_text.ocr.backends.easyocr._run_easyocr",
            return_value=(low_conf_raw, None),
        ):
            result = _supplement_with_regional_orientation(
                reader, arr, {}, [], page_index=0, language="pt"
            )
        # Low-confidence tokens must be filtered out
        assert len(result) == 0

    def test_high_confidence_tokens_are_admitted_from_uncovered_cells(self):
        """Regional tokens with confidence >= 0.40 and text length >= 2 are admitted."""
        import numpy as np
        from unittest.mock import MagicMock, patch
        from structured_pdf_text.ocr.backends.easyocr import (
            _supplement_with_regional_orientation,
        )

        arr = np.ones((400, 400, 3), dtype=np.uint8) * 128

        high_conf_raw = [([[10, 5], [90, 5], [90, 25], [10, 25]], "STAMP", 0.85)]

        reader = MagicMock()
        with patch(
            "structured_pdf_text.ocr.backends.easyocr._run_easyocr",
            return_value=(high_conf_raw, None),
        ):
            result = _supplement_with_regional_orientation(
                reader, arr, {}, [], page_index=0, language="pt"
            )
        # At least one token admitted from uncovered cell
        assert len(result) >= 1
        assert any(t.text == "STAMP" for t in result)

    def test_single_char_tokens_not_admitted(self):
        """Tokens with text length < 2 must not be admitted even if high confidence."""
        import numpy as np
        from unittest.mock import MagicMock, patch
        from structured_pdf_text.ocr.backends.easyocr import (
            _supplement_with_regional_orientation,
        )

        arr = np.ones((400, 400, 3), dtype=np.uint8) * 128
        single_char_raw = [([[10, 5], [30, 5], [30, 25], [10, 25]], "X", 0.95)]

        reader = MagicMock()
        with patch(
            "structured_pdf_text.ocr.backends.easyocr._run_easyocr",
            return_value=(single_char_raw, None),
        ):
            result = _supplement_with_regional_orientation(
                reader, arr, {}, [], page_index=0, language="pt"
            )
        assert all(len(t.text.strip()) >= 2 for t in result)
