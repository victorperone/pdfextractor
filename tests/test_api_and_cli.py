from pathlib import Path

from reportlab.pdfgen import canvas

import structured_pdf_text.api as api_module
from structured_pdf_text import PdfTextExtractor
from structured_pdf_text.cli import main
from structured_pdf_text.config import ExtractorConfig, best_extraction_config


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


def test_experimental_occlusion_redaction_is_disabled_by_default():
    config = ExtractorConfig()

    assert config.enable_experimental_occlusion_redaction is False


def test_best_profile_does_not_enable_experimental_occlusion_redaction():
    config = best_extraction_config()

    assert config.enable_experimental_occlusion_redaction is False


def test_default_extraction_does_not_run_occlusion_redaction(
    tmp_path,
    monkeypatch,
):
    pdf = _two_line_pdf(tmp_path / "visibility-default-off.pdf")

    def fail_if_called(*args, **kwargs):
        raise AssertionError(
            "Experimental occlusion detection must not run by default"
        )

    monkeypatch.setattr(
        api_module,
        "detect_opaque_occlusion_boxes",
        fail_if_called,
    )

    result = PdfTextExtractor().extract(pdf)

    facts = result.pages[0].diagnostics.facts

    assert facts["experimental_occlusion_redaction_enabled"] is False
    assert facts["textpage_reconciliation_disabled_for_redaction"] is False
    assert facts["opaque_occlusion_boxes"] == []
    assert facts["redacted_native_characters"] == 0


def test_redaction_disables_unfiltered_textpage_reconciliation(
    tmp_path,
    monkeypatch,
):
    pdf = _two_line_pdf(tmp_path / "visibility-redaction-enabled.pdf")

    original_reconstruct = api_module.reconstruct_native_lines
    captured = {}

    def fake_detect_opaque_occlusion_boxes(page, rendered_page):
        return []

    def fake_characters_occluded(characters, boxes):
        characters = list(characters)
        assert characters

        # Simulate one native character being rejected as visually occluded.
        return characters[:-1], 1

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

    assert captured["extracted_text"] is None
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
