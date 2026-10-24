from pathlib import Path

from reportlab.pdfgen import canvas

import structured_pdf_text.api as api_module
from structured_pdf_text import PdfTextExtractor
from structured_pdf_text.cli import main
from structured_pdf_text.config import ExtractionMode, ExtractorConfig, best_extraction_config
from structured_pdf_text.native.pdfium_source import PdfiumNativeEvidenceSource


RENDERING = ExtractorConfig(mode=ExtractionMode.BALANCED, enable_ocr=False,
                            enable_experimental_occlusion_redaction=True)


def _two_line_pdf(path: Path) -> Path:
    c = canvas.Canvas(str(path), pagesize=(240, 240))
    c.drawString(20, 200, "Primeira linha")
    c.drawString(20, 180, "São Cristóvão")
    c.save()
    return path


def test_extractor_returns_raw_and_reading_text(tmp_path):
    pdf = _two_line_pdf(tmp_path / "two-lines.pdf")
    result = PdfTextExtractor().extract(pdf)
    assert "Primeira linha" in result.raw_text
    assert "São Cristóvão" in result.raw_text
    assert "Primeira linha" in result.reading_text
    assert "São Cristóvão" in result.reading_text
    assert result.pages[0].diagnostics.native_chars > 0


def test_cli_extract_json(tmp_path, capsys):
    pdf = _two_line_pdf(tmp_path / "cli.pdf")
    exit_code = main(["extract", str(pdf), "--output", "json"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Primeira linha" in captured.out
    assert '"pages"' in captured.out


def test_cli_overlay(tmp_path, capsys):
    pdf = _two_line_pdf(tmp_path / "overlay.pdf")
    out = tmp_path / "overlay.png"
    exit_code = main(["overlay", str(pdf), "--page", "1", "--out", str(out)])
    assert exit_code == 0
    assert out.exists()


def test_experimental_occlusion_redaction_is_enabled_by_default():
    config = ExtractorConfig()

    assert config.enable_experimental_occlusion_redaction is True


def test_best_profile_enables_experimental_occlusion_redaction():
    config = best_extraction_config()

    assert config.enable_experimental_occlusion_redaction is True


def test_default_extraction_runs_occlusion_redaction(
    tmp_path,
    monkeypatch,
):
    pdf = _two_line_pdf(tmp_path / "visibility-default-on.pdf")

    calls = []
    original = PdfiumNativeEvidenceSource.hidden_glyphs

    def spy_hidden_glyphs(self, *args, **kwargs):
        calls.append(args[0])
        return original(self, *args, **kwargs)

    def unexpected_heuristic(*args, **kwargs):
        raise AssertionError("glyph ink analysis supersedes the pixel heuristic")

    monkeypatch.setattr(PdfiumNativeEvidenceSource, "hidden_glyphs", spy_hidden_glyphs)
    monkeypatch.setattr(api_module, "detect_opaque_occlusion_boxes", unexpected_heuristic)

    # Glyph visibility needs the page raster, i.e. a rendering mode.
    result = PdfTextExtractor(RENDERING).extract(pdf)

    facts = result.pages[0].diagnostics.facts

    assert calls == [0]
    assert facts["experimental_occlusion_redaction_enabled"] is True
    assert facts["opaque_occlusion_boxes"] == []
    assert facts["redacted_native_characters"] == 0
    assert facts["inkless_native_characters"] == 0


def test_pixel_heuristic_is_the_fallback_without_glyph_attribution(tmp_path, monkeypatch):
    pdf = _two_line_pdf(tmp_path / "visibility-fallback.pdf")
    calls = []

    def stub_detect_opaque_occlusion_boxes(*args, **kwargs):
        calls.append(args)
        return []

    monkeypatch.setattr(PdfiumNativeEvidenceSource, "hidden_glyphs", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(api_module, "detect_opaque_occlusion_boxes", stub_detect_opaque_occlusion_boxes)

    facts = PdfTextExtractor(RENDERING).extract(pdf).pages[0].diagnostics.facts

    assert calls
    assert facts["redacted_native_characters"] == 0


def test_redaction_reconciles_only_against_the_filtered_textpage(
    tmp_path,
    monkeypatch,
):
    pdf = _two_line_pdf(tmp_path / "visibility-redaction-enabled.pdf")

    original_reconstruct = api_module.reconstruct_native_lines
    original_without_ink = api_module.characters_without_ink
    captured = {}

    def fake_characters_without_ink(characters, hidden):
        characters = list(characters)
        rejected = next(c for c in reversed(characters) if c.text.strip())
        captured["rejected"] = rejected.char_index
        # Simulate one native glyph proven to leave no visible ink.
        return original_without_ink(characters, {rejected.char_index})

    def capture_reconstruct_native_lines(characters, extracted_text=None):
        captured["extracted_text"] = extracted_text
        captured["characters"] = characters
        return original_reconstruct(characters, extracted_text)

    monkeypatch.setattr(api_module, "characters_without_ink", fake_characters_without_ink)
    monkeypatch.setattr(api_module, "reconstruct_native_lines", capture_reconstruct_native_lines)

    result = PdfTextExtractor(RENDERING).extract(pdf)

    facts = result.pages[0].diagnostics.facts
    with PdfiumNativeEvidenceSource(pdf) as source:
        original_text = source.extract_page(0).extracted_text

    assert captured["extracted_text"] is not None
    assert len(captured["extracted_text"]) == len(original_text) - 1
    assert all(c.char_index != captured["rejected"] for c in captured["characters"])
    assert facts["experimental_occlusion_redaction_enabled"] is True
    assert facts["textpage_reconciliation_disabled_for_redaction"] is True
    assert facts["redacted_native_characters"] == 1


def test_experimental_visibility_keeps_textpage_reconciliation_without_redaction(
    tmp_path,
    monkeypatch,
):
    pdf = _two_line_pdf(tmp_path / "visibility-no-redaction.pdf")

    original_reconstruct = api_module.reconstruct_native_lines
    captured = {}

    def fake_detect_opaque_occlusion_boxes(page, rendered_page):
        return []

    def fake_characters_occluded(characters, boxes):
        return list(characters), 0

    def capture_reconstruct_native_lines(characters, extracted_text=None):
        captured["extracted_text"] = extracted_text
        return original_reconstruct(characters, extracted_text)

    monkeypatch.setattr(
        api_module,
        "detect_opaque_occlusion_boxes",
        fake_detect_opaque_occlusion_boxes,
    )
    monkeypatch.setattr(
        api_module,
        "characters_occluded",
        fake_characters_occluded,
    )
    monkeypatch.setattr(
        api_module,
        "reconstruct_native_lines",
        capture_reconstruct_native_lines,
    )

    config = ExtractorConfig(
        enable_experimental_occlusion_redaction=True,
    )

    result = PdfTextExtractor(config).extract(pdf)

    facts = result.pages[0].diagnostics.facts

    assert captured["extracted_text"] is not None
    assert facts["experimental_occlusion_redaction_enabled"] is True
    assert facts["textpage_reconciliation_disabled_for_redaction"] is False
    assert facts["redacted_native_characters"] == 0
