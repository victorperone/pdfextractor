"""Deterministic PDFs and OCR observations for domain integration, without models.

These fixtures exercise PDFium, the authorized pipeline and document assembly.
They deliberately do not claim recognition quality on the V5 corpus.
"""
from dataclasses import replace

from PIL import Image, ImageDraw
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from structured_pdf_text.document import OcrToken, SourceKind
from structured_pdf_text.geometry import BBox


def hybrid_table_pdf(path):
    writer = canvas.Canvas(str(path), pagesize=(400, 600), invariant=1)
    writer.setFont("Helvetica", 11)
    writer.drawString(25, 575, "NÃO AUTORIZADO")
    writer.drawString(25, 555, "CPF 123.456.789-09 CNPJ 12.345.678/0001-90")
    writer.drawString(25, 535, "Saldo R$ -42,90 ID AB-009")
    for x in (25, 140, 255, 370):
        writer.line(x, 410, x, 490)
    for y in (410, 450, 490):
        writer.line(25, y, 370, y)
    for x, label in ((35, "Item"), (150, "Valor"), (265, "Código")):
        writer.drawString(x, 465, label)
    writer.drawString(35, 425, "Ajuste")
    with Image.new("RGB", (690, 240), "white") as image:
        draw = ImageDraw.Draw(image)
        for x in (0, 230, 460, 689):
            draw.line((x, 0, x, 239), fill="black", width=3)
        for y in (0, 80, 160, 239):
            draw.line((0, y, 689, y), fill="black", width=3)
        for row, labels in enumerate((("Produto", "Valor", "Codigo"),
                                     ("Papel", "-42,90", "AB-009"),
                                     ("Caneta", "12,00", "AB-010"))):
            for col, text in enumerate(labels):
                draw.text((col * 230 + 20, row * 80 + 20), text, fill="black")
        writer.drawImage(ImageReader(image), 25, 100, width=345, height=120)
    writer.save()
    return path


class TableFixtureOCR:
    """Known raster evidence, filtered to the actual authorized render rectangle."""

    def __init__(self, extra_tokens=()):
        self.calls = []
        self.closed = False
        self.tokens = [
            OcrToken(text, BBox(35 + col * 115, 390 + row * 40,
                               100 + col * 115, 402 + row * 40),
                     .98, "pt-BR", SourceKind.OCR_REGION)
            for row, labels in enumerate((("Produto", "Valor", "Código"),
                                          ("Papel", "-42,90", "AB-009"),
                                          ("Caneta", "12,00", "AB-010")))
            for col, text in enumerate(labels)
        ] + list(extra_tokens)

    def recognize_page(self, image, page_index, page_bbox=None, **kwargs):
        assert not self.closed
        assert page_index == 0
        self.calls.append(page_bbox)
        return [replace(token) for token in self.tokens
                if page_bbox.x0 <= token.bbox.x0 < token.bbox.x1 <= page_bbox.x1
                and page_bbox.y0 <= token.bbox.y0 < token.bbox.y1 <= page_bbox.y1]

    def close(self):
        self.closed = True


def install_table_structure(monkeypatch, *, duplicate_native=False):
    """Inject only grid discovery; keep real mapping, cells and integration.

    The synthetic full page contains two grids. As in the real detector, the
    initial whole-page attempt cannot isolate the raster grid. The region
    attempt must preserve its own coordinate frame to succeed.
    """
    from structured_pdf_text.tables.visual import VisualGrid, _image_size
    from structured_pdf_text.tables.visual_engine import OpenCvTableStructureEngine

    calls = []

    def recognize(self, image):
        width, height = _image_size(image)
        calls.append((width, height))
        if height > width:
            if not duplicate_native:
                return None
            return VisualGrid(tuple(x * width / 400 for x in (25, 140, 255, 370)),
                              tuple(y * height / 600 for y in (110, 150, 190)), .95)
        if duplicate_native:
            return None
        return VisualGrid(tuple(i * width / 3 for i in range(4)),
                          tuple(i * height / 3 for i in range(4)), .95)

    monkeypatch.setattr(OpenCvTableStructureEngine, "recognize", recognize)
    return calls


def extract_hybrid(path, *, extra_tokens=()):
    from structured_pdf_text import ExtractorConfig, PdfTextExtractor

    engine = TableFixtureOCR(extra_tokens)
    config = ExtractorConfig(mode="balanced", enable_tables=True,
                             enable_table_cell_ocr=True, complexity_render_scale=1.)
    with PdfTextExtractor(config, ocr_engine=engine) as extractor:
        document = extractor.extract(path)
    assert engine.closed
    assert engine.calls, "The fixture must exercise OCR, not only native extraction"
    return document, engine
