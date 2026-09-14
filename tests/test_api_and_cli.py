from pathlib import Path

from reportlab.pdfgen import canvas

from structured_pdf_text import PdfTextExtractor
from structured_pdf_text.cli import main


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
