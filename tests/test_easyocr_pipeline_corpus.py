from __future__ import annotations

import json
from pathlib import Path

from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from PIL import Image

from structured_pdf_text import PdfTextExtractor
from structured_pdf_text.config import ExtractorConfig
from structured_pdf_text.document import ContentKind, OcrToken, RegionKind, SourceKind
from structured_pdf_text.geometry import BBox


ROOT = Path(__file__).parent / "corpus" / "easyocr_pipeline"


def test_easyocr_pipeline_corpus_manifest_has_ground_truth_for_each_document() -> None:
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["schema"] == "structured-pdf-text.easyocr-pipeline.v2"
    assert manifest["documents"]
    features = set()
    for document in manifest["documents"]:
        reference = ROOT / document["ground_truth"]
        assert reference.is_file(), document["id"]
        assert reference.read_text(encoding="utf-8").strip(), document["id"]
        assert document["features"]
        features.update(document["features"])
    assert len(manifest["documents"]) >= 25
    assert {
        "clean_scan", "low_dpi", "tiny_text", "low_contrast", "blur", "noise", "skew",
        "rotated_90", "rotated_180", "rotated_270", "two_columns", "three_columns",
        "headings", "lists", "footnotes", "forms", "bordered_table", "borderless_table",
        "financial_table", "cpf_cnpj", "process_numbers", "hybrid_native_scan",
        "bad_hidden_ocr", "screenshots", "accented_portuguese", "hyphenation",
        "dark_background", "jpeg_artifacts", "stamps",
    } <= features


def test_scan_extraction_reconstructs_heading_body_and_list_regions(tmp_path: Path) -> None:
    class Backend:
        def recognize_page(self, image, page_index, page_bbox=None, *, quality_policy=None):
            samples = [
                ("Introdução", BBox(35, 35, 220, 75)),
                ("Este parágrafo continua", BBox(35, 105, 245, 117)),
                ("na linha seguinte.", BBox(35, 121, 190, 133)),
                ("• Primeiro item", BBox(45, 185, 180, 197)),
            ]
            return [
                OcrToken(
                    text=text,
                    bbox=bbox,
                    confidence=0.95,
                    language="pt-BR",
                    source=SourceKind.OCR_PAGE,
                )
                for text, bbox in samples
            ]

    image_path = tmp_path / "scan.png"
    Image.new("RGB", (300, 400), "white").save(image_path)
    pdf_path = tmp_path / "scan.pdf"
    pdf = canvas.Canvas(str(pdf_path), pagesize=(300, 400))
    pdf.drawImage(ImageReader(str(image_path)), 0, 0, width=300, height=400)
    pdf.save()

    result = PdfTextExtractor(
        ExtractorConfig(mode="ocr", language="pt-BR", ocr_tiling=False),
        ocr_engine=Backend(),
    ).extract(pdf_path)

    page = result.pages[0]
    kinds = {block.kind for block in page.content_blocks}
    assert ContentKind.TITLE in kinds
    assert ContentKind.TEXT in kinds
    assert ContentKind.LIST in kinds
    assert "Este parágrafo continua na linha seguinte." in result.reading_text
    assert "Primeiro item" in result.reading_text
