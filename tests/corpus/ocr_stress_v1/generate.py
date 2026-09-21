#!/usr/bin/env python3
"""Generate the deterministic synthetic OCR stress corpus.

This module is test infrastructure only. It deliberately does not import the
PDFExtractor, OCR engines, or any production parser code.

Examples::

    python generate.py
    python generate.py --pages 15-16
    python generate.py --pages 36-37 --output-dir /tmp/ocr-stress

The default output is an ignored ``outputs/`` directory below this module.
The full manifest is written beside this file; subset manifests are written
next to their generated PDF unless ``--manifest`` is supplied.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import random
from typing import Iterable

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import code128
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import Color, HexColor, black, white
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
import pypdfium2 as pdfium


SEED = 20260921
PAGE_COUNT = 60
PDF_NAME = "Document_OCR_Stress_V1.pdf"
MANIFEST_NAME = "manifest.json"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"
DEFAULT_MANIFEST = Path(__file__).resolve().parent / MANIFEST_NAME
PAGE_SIZE_PORTRAIT = (612.0, 792.0)
PAGE_SIZE_LANDSCAPE = (792.0, 612.0)


SCENARIOS = (
    "texto digital de controle",
    "raster simples legível",
    "raster em resolução moderada",
    "raster de baixa resolução",
    "raster com fonte pequena",
    "raster com acentuação e Unicode",
    "raster com valores e datas",
    "raster com contraste reduzido",
    "raster com ruído leve e compressão",
    "raster com inclinação pequena",
    "texto sobre textura e carimbo",
    "raster com múltiplos blocos",
    "texto digital e comprovante raster",
    "texto digital acima e abaixo de raster",
    "figura raster contínua — parte 1",
    "figura raster contínua — parte 2",
    "duas imagens raster afastadas",
    "raster sobre elemento decorativo",
    "imagem decorativa sem texto",
    "comprovante raster e texto semelhante",
    "nota marginal raster",
    "carimbo e assinatura raster",
    "duas colunas e imagem próxima",
    "raster textual e gráfico sem texto",
    "tabela digital com grade",
    "tabela digital sem bordas",
    "tabela raster financeira",
    "tabela raster sem bordas",
    "tabela raster com fonte pequena",
    "tabela raster com células multilinha",
    "tabela digital com células mescladas",
    "tabela raster com células mescladas",
    "tabela digital e imagem em célula",
    "tabela digital seguida de raster",
    "tabela raster com muitas colunas",
    "tabela digital continuada — parte 1",
    "tabela digital continuada — parte 2",
    "tabela raster continuada — parte 1",
    "tabela raster continuada — parte 2",
    "tabela raster em paisagem",
    "texto digital em paisagem",
    "raster horizontal em paisagem",
    "objeto de texto girado em 90 graus",
    "imagem textual girada dentro da página",
    "três colunas e nota lateral",
    "QR válido e rótulo separado",
    "código de barras válido e rótulo",
    "várias imagens, poucas com texto",
    "imagem textual parcialmente fora da página",
    "figura inteiramente fora da área visível",
    "texto, comprovante raster e tabela",
    "texto, tabela raster e figura decorativa",
    "página só digital entre páginas OCR",
    "várias regiões raster e decoração",
    "documento paisagem misto",
    "tabela digital continuada com OCR",
    "continuação com cabeçalho e célula raster",
    "QR, barras, texto e imagem",
    "figuras múltiplas e raster parcial",
    "integração densa",
)

BLOCKS = (
    (1, 12, "A", "OCR básico"),
    (13, 24, "B", "páginas mistas"),
    (25, 40, "C", "tabelas"),
    (41, 50, "D", "orientação, layout e figuras"),
    (51, 60, "E", "integração"),
)


def _block_for_page(page: int) -> tuple[str, str]:
    for first, last, code, label in BLOCKS:
        if first <= page <= last:
            return code, label
    raise ValueError(page)


def _page_size(page: int) -> tuple[float, float]:
    if page in {15, 16, 40, 41, 42, 55}:
        return PAGE_SIZE_LANDSCAPE
    return PAGE_SIZE_PORTRAIT


def _orientation(size: tuple[float, float]) -> str:
    return "landscape" if size[0] > size[1] else "portrait"


def _marker(page: int) -> str:
    return f"OCRS-P{page:02d}-CONTROL"


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _wrap(text: str, font: str, size: float, width: float) -> list[str]:
    from reportlab.pdfbase import pdfmetrics

    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}" if current else word
        if not current or pdfmetrics.stringWidth(candidate, font, size) <= width:
            current = candidate
            continue
        lines.append(current)
        current = word
    if current:
        lines.append(current)
    return lines or [""]


def _draw_text(
    pdf: canvas.Canvas,
    text: str,
    x: float,
    top: float,
    *,
    page_height: float,
    size: float = 10.0,
    font: str = "Helvetica",
    color=black,
    angle: float = 0.0,
) -> None:
    pdf.saveState()
    pdf.setFillColor(color)
    pdf.setFont(font, size)
    if angle:
        pdf.translate(x, page_height - top - size)
        pdf.rotate(angle)
        pdf.drawString(0, 0, text)
    else:
        pdf.drawString(x, page_height - top - size, text)
    pdf.restoreState()


def _draw_wrapped(
    pdf: canvas.Canvas,
    text: str,
    x: float,
    top: float,
    width: float,
    *,
    page_height: float,
    size: float = 10.0,
    font: str = "Helvetica",
    leading: float | None = None,
) -> float:
    leading = leading or size * 1.35
    for line in _wrap(text, font, size, width):
        _draw_text(pdf, line, x, top, page_height=page_height, size=size, font=font)
        top += leading
    return top


def _raster_image(
    lines: Iterable[str],
    width: float,
    height: float,
    *,
    font_size: float = 12.0,
    mode: str = "clean",
    table: list[list[str]] | None = None,
    seed: int = 0,
) -> Image.Image:
    """Render a temporary text page and return only its rasterized pixels."""
    source = BytesIO()
    local = canvas.Canvas(source, pagesize=(width, height), invariant=1)
    local.setFillColor(white)
    local.rect(0, 0, width, height, stroke=0, fill=1)
    top = 28.0
    for line in lines:
        _draw_text(local, line, 28, top, page_height=height, size=font_size)
        top += font_size * 1.45
    if table is not None:
        x, y, table_width = 28.0, max(32.0, top + 12.0), width - 56.0
        row_height = max(24.0, font_size * 2.25)
        columns = max(len(row) for row in table)
        col_width = table_width / columns
        local.setStrokeColor(HexColor("#30465f"))
        local.setFillColor(HexColor("#eef3f7"))
        local.rect(x, height - y - row_height, table_width, row_height, stroke=1, fill=1)
        for row_index, row in enumerate(table):
            row_top = y + row_index * row_height
            local.setFillColor(white if row_index else HexColor("#dbe7f2"))
            local.rect(x, height - row_top - row_height, table_width, row_height, stroke=1, fill=1)
            for column_index, value in enumerate(row):
                local.setStrokeColor(HexColor("#30465f"))
                local.rect(
                    x + column_index * col_width,
                    height - row_top - row_height,
                    col_width,
                    row_height,
                    stroke=1,
                    fill=0,
                )
                _draw_text(
                    local,
                    value,
                    x + column_index * col_width + 5,
                    row_top + 7,
                    page_height=height,
                    size=font_size,
                    font="Helvetica-Bold" if row_index == 0 else "Helvetica",
                )
    local.save()
    document = pdfium.PdfDocument(source.getvalue())
    bitmap = document[0].render(scale=1.5)
    image = bitmap.to_pil().convert("RGB")
    if mode == "low_resolution":
        image = image.resize((max(1, image.width // 2), max(1, image.height // 2)))
    elif mode == "contrast":
        image = ImageEnhance.Contrast(image).enhance(0.55)
    elif mode == "noise":
        rng = random.Random(SEED + seed)
        noise = Image.effect_noise(image.size, 24).convert("RGB")
        image = Image.blend(image, noise, 0.055)
        image = image.filter(ImageFilter.GaussianBlur(radius=0.25))
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=72, optimize=False)
        image = Image.open(BytesIO(buffer.getvalue())).convert("RGB")
    elif mode == "skew":
        image = image.rotate(2.2, resample=Image.Resampling.BICUBIC, expand=False, fillcolor="white")
    elif mode == "texture":
        draw = ImageDraw.Draw(image, "RGB")
        for y in range(0, image.height, 26):
            draw.line((0, y, image.width, y + 8), fill=(224, 228, 224), width=2)
        image = ImageEnhance.Contrast(image).enhance(0.8)
    elif mode == "stamp":
        draw = ImageDraw.Draw(image, "RGBA")
        draw.rectangle((image.width - 250, 60, image.width - 35, 145), outline=(150, 25, 35, 180), width=5)
        draw.text((image.width - 220, 90), "CONFERIDO", fill=(150, 25, 35, 180))
        draw.line((image.width - 220, 165, image.width - 80, 205), fill=(25, 40, 110, 180), width=4)
    return image


def _draw_image(
    pdf: canvas.Canvas,
    image: Image.Image,
    x: float,
    top: float,
    width: float,
    height: float,
    *,
    page_height: float,
) -> None:
    pdf.drawImage(
        ImageReader(image),
        x,
        page_height - top - height,
        width=width,
        height=height,
        preserveAspectRatio=False,
        mask="auto",
    )


def _add_raster_region(
    pdf: canvas.Canvas,
    meta: dict,
    image: Image.Image,
    x: float,
    top: float,
    width: float,
    height: float,
    *,
    page_height: float,
    region_id: str,
    kind: str = "text",
    text_in_image: bool = True,
    quality: str = "clean",
) -> None:
    _draw_image(pdf, image, x, top, width, height, page_height=page_height)
    meta["raster_regions"].append(
        {
            "region_id": region_id,
            "kind": kind,
            "bbox_pt": [round(x, 2), round(top, 2), round(x + width, 2), round(top + height, 2)],
            "text_in_image": text_in_image,
            "quality": quality,
        }
    )


def _header(pdf: canvas.Canvas, meta: dict, title: str, *, page_height: float) -> None:
    _draw_text(pdf, "DOCUMENT OCR STRESS V1 — CORPUS SINTÉTICO", 28, 22, page_height=page_height, size=13, font="Helvetica-Bold")
    _draw_text(pdf, _marker(meta["source_page"]), 28, 43, page_height=page_height, size=9, font="Courier")
    _draw_text(pdf, title, 28, 67, page_height=page_height, size=12, font="Helvetica-Bold")


def _footer(pdf: canvas.Canvas, meta: dict, *, page_width: float, page_height: float) -> None:
    _draw_text(
        pdf,
        f"OCRS | página {meta['source_page']} de {PAGE_COUNT} | sintético e fictício",
        28,
        page_height - 30,
        page_height=page_height,
        size=7,
        font="Helvetica",
    )


def _native_body(pdf: canvas.Canvas, page: int, *, width: float, height: float, title: str) -> None:
    _header(pdf, {"source_page": page}, title, page_height=height)
    body = [
        f"Controle {page:02d}: texto digital preservado sem necessidade de OCR.",
        "Amostra fictícia: ação, revisão, conformidade, R$ 123,45 e 98,7%.",
        f"Identificador de inspeção: OCRS-DIGITAL-{page:02d}-A.",
    ]
    top = 108.0
    for line in body:
        _draw_text(pdf, line, 32, top, page_height=height, size=10)
        top += 20


def _table(pdf: canvas.Canvas, meta: dict, x: float, top: float, width: float, *, raster: bool = False, merged: bool = False, rows: int = 4, columns: int = 4) -> None:
    table_id = f"OCRS-P{meta['source_page']:02d}-T01"
    meta["has_table"] = True
    meta["tables"].append({"table_id": table_id, "raster": raster, "merged_cells": merged, "rows": rows, "columns": columns})
    cell_w = width / columns
    row_h = 30.0
    pdf.setStrokeColor(HexColor("#30465f"))
    for row in range(rows):
        for col in range(columns):
            if merged and row == 0 and col == 0:
                continue
            fill = HexColor("#dbe7f2") if row == 0 else white
            pdf.setFillColor(fill)
            pdf.rect(x + col * cell_w, meta["page_height"] - top - (row + 1) * row_h, cell_w, row_h, stroke=1, fill=1)
            value = "Campo" if row == 0 else f"{row}-{col + 1}"
            if row == 0:
                value = ["Campo", "Valor", "Status", "Nota"][col % 4]
            _draw_text(pdf, value, x + col * cell_w + 6, top + row * row_h + 9, page_height=meta["page_height"], size=7.5, font="Helvetica-Bold" if row == 0 else "Helvetica")
    if merged:
        pdf.setFillColor(HexColor("#dbe7f2"))
        pdf.rect(x, meta["page_height"] - top - row_h, cell_w * 2, row_h, stroke=1, fill=1)
        _draw_text(pdf, "Grupo mesclado", x + 6, top + 9, page_height=meta["page_height"], size=7.5, font="Helvetica-Bold")


def _draw_qr(pdf: canvas.Canvas, x: float, top: float, size: float, *, page_height: float, value: str) -> bool:
    try:
        widget = QrCodeWidget(value)
        widget.barWidth = size
        widget.barHeight = size
        drawing = Drawing(size, size)
        drawing.add(widget)
        pdf.saveState()
        pdf.translate(x, page_height - top - size)
        renderPDF.draw(drawing, pdf, 0, 0)
        pdf.restoreState()
        return True
    except Exception:
        pdf.setFillColor(black)
        pdf.rect(x, page_height - top - size, size, size, stroke=0, fill=1)
        return False


def _draw_barcode(pdf: canvas.Canvas, x: float, top: float, *, page_height: float, value: str) -> bool:
    try:
        barcode = code128.Code128(value, barHeight=48, humanReadable=False)
        barcode.drawOn(pdf, x, page_height - top - 48)
        return True
    except Exception:
        for offset in range(0, 150, 5):
            pdf.rect(x + offset, page_height - top - 48, 2, 48, stroke=0, fill=1)
        return False


def _base_meta(source_page: int, pdf_page: int) -> dict:
    width, height = _page_size(source_page)
    block, block_label = _block_for_page(source_page)
    return {
        "page": pdf_page,
        "source_page": source_page,
        "scenario": SCENARIOS[source_page - 1],
        "block": block,
        "block_label": block_label,
        "page_size_pt": [width, height],
        "orientation": _orientation((width, height)),
        "has_native_text_layer": False,
        "raster_regions": [],
        "has_table": False,
        "has_figure": False,
        "figures": [],
        "tables": [],
        "continuation": None,
        "notes": [],
        "expected_indicators": [_marker(source_page)],
        "source_page_marker": _marker(source_page),
        "page_height": height,
    }


def _draw_page(pdf: canvas.Canvas, meta: dict) -> None:
    page = meta["source_page"]
    width, height = meta["page_size_pt"]
    title = meta["scenario"].title()
    if page == 1:
        meta["has_native_text_layer"] = True
        _native_body(pdf, page, width=width, height=height, title=title)
        _draw_text(pdf, "Símbolos: € £ ¥ § ¶ © ® ™ ° º ª × ÷ ≤ ≥ ≠ ±.", 32, 188, page_height=height, size=10)
    elif 2 <= page <= 12:
        mode = {4: "low_resolution", 8: "contrast", 9: "noise", 10: "skew", 11: "texture", 12: "stamp"}.get(page, "clean")
        lines = [
            f"{_marker(page)} — OCR básico",
            "Texto raster fictício para avaliação de reconhecimento visual.",
            "Ação, órgão, revisão, R$ 123,45, 12/09/2026 e 98,7%.",
            f"Código OCRS-SCAN-{page:02d}-B; não há camada de texto nesta página.",
        ]
        image = _raster_image(lines, width, height, font_size=11 if page != 5 else 8, mode=mode, seed=page)
        _add_raster_region(pdf, meta, image, 0, 0, width, height, page_height=height, region_id=f"P{page:02d}-R01", quality=mode)
        meta["notes"].append("página integralmente raster, sem texto selecionável")
    elif page == 13:
        meta["has_native_text_layer"] = True
        _native_body(pdf, page, width=width, height=height, title=title)
        image = _raster_image(["COMPROVANTE OCRS-013", "Total R$ 213,40", "APROVADO"], 210, 130, font_size=10, seed=page)
        _add_raster_region(pdf, meta, image, 350, 160, 210, 130, page_height=height, region_id="P13-R02", kind="receipt")
    elif page == 14:
        meta["has_native_text_layer"] = True
        _native_body(pdf, page, width=width, height=height, title=title)
        image = _raster_image(["BLOCO RASTER 014", "Texto entre parágrafos"], 500, 100, font_size=11, seed=page)
        _add_raster_region(pdf, meta, image, 56, 230, 500, 100, page_height=height, region_id="P14-R02")
        _draw_text(pdf, "A ordem deve conservar o texto digital abaixo da imagem.", 32, 365, page_height=height, size=10)
    elif page in {15, 16}:
        lines = [f"{_marker(page)} — FIGURA CONTÍNUA {page - 14}/2", "Trecho raster horizontal contínuo", "OCRS-CONTINUA-15-16", "Dados fictícios 15,16%"]
        image = _raster_image(lines, width, height, font_size=15, mode="clean", seed=page)
        _add_raster_region(pdf, meta, image, 0, 0, width, height, page_height=height, region_id=f"P{page:02d}-R01", kind="continuous_figure")
        meta["continuation"] = {"group_id": "figure-15-16", "role": "first" if page == 15 else "second", "paired_page": 16 if page == 15 else 15}
    elif page == 17:
        meta["has_native_text_layer"] = True
        _native_body(pdf, page, width=width, height=height, title=title)
        for index, x in enumerate((40, 330), 1):
            image = _raster_image([f"IMAGEM {index}", f"OCRS-017-{index}"], 240, 150, font_size=11, seed=page + index)
            _add_raster_region(pdf, meta, image, x, 230, 240, 150, page_height=height, region_id=f"P17-R{index + 1:02d}")
    elif page == 18:
        meta["has_native_text_layer"] = True
        _native_body(pdf, page, width=width, height=height, title=title)
        pdf.setFillColor(HexColor("#d9e5ef"))
        pdf.circle(470, height - 440, 95, stroke=0, fill=1)
        image = _raster_image(["RASTER SOBRE DECORAÇÃO", "OCRS-018"], 250, 130, font_size=11, seed=page)
        _add_raster_region(pdf, meta, image, 245, 360, 250, 130, page_height=height, region_id="P18-R02")
    elif page == 19:
        meta["has_native_text_layer"] = True
        _native_body(pdf, page, width=width, height=height, title=title)
        meta["has_figure"] = True
        pdf.setFillColor(HexColor("#b9c8d4"))
        pdf.circle(450, height - 380, 90, stroke=0, fill=1)
        meta["figures"].append({"figure_id": "P19-F01", "text": False, "decorative": True})
    elif page == 20:
        meta["has_native_text_layer"] = True
        _native_body(pdf, page, width=width, height=height, title=title)
        image = _raster_image(["RECIBO 020", "Valor R$ 123,45", "OCRS-020-RECEIPT"], 260, 160, font_size=11, seed=page)
        _add_raster_region(pdf, meta, image, 300, 220, 260, 160, page_height=height, region_id="P20-R02", kind="receipt")
    elif page == 21:
        meta["has_native_text_layer"] = True
        _native_body(pdf, page, width=width, height=height, title=title)
        image = _raster_image(["Nota marginal OCRS-021", "fonte pequena"], 180, 90, font_size=9, seed=page)
        _add_raster_region(pdf, meta, image, 408, 220, 180, 90, page_height=height, region_id="P21-R02", kind="marginal_note")
    elif page == 22:
        meta["has_native_text_layer"] = True
        _native_body(pdf, page, width=width, height=height, title=title)
        image = _raster_image(["CARIMBO", "ASSINATURA", "OCRS-022"], 240, 130, font_size=11, mode="stamp", seed=page)
        _add_raster_region(pdf, meta, image, 300, 225, 240, 130, page_height=height, region_id="P22-R02", kind="stamp_signature")
    elif page == 23:
        meta["has_native_text_layer"] = True
        _header(pdf, meta, title, page_height=height)
        for col, x in enumerate((32, 315), 1):
            _draw_text(pdf, f"Coluna {col}: texto digital de controle.", x, 115, page_height=height, size=9)
            _draw_wrapped(pdf, "A leitura deve permanecer dentro do fluxo da coluna, sem mistura com a figura raster.", x, 145, 235, page_height=height, size=9)
        image = _raster_image(["IMAGEM TEXTUAL", "OCRS-023"], 150, 105, font_size=10, seed=page)
        _add_raster_region(pdf, meta, image, 32, 305, 150, 105, page_height=height, region_id="P23-R03")
    elif page == 24:
        meta["has_native_text_layer"] = True
        _native_body(pdf, page, width=width, height=height, title=title)
        image = _raster_image(["REGIÃO TEXTUAL", "OCRS-024"], 180, 100, font_size=10, seed=page)
        _add_raster_region(pdf, meta, image, 45, 240, 180, 100, page_height=height, region_id="P24-R02")
        meta["has_figure"] = True
        pdf.setFillColor(HexColor("#b8c4cf"))
        pdf.rect(340, height - 340, 160, 100, stroke=0, fill=1)
        meta["figures"].append({"figure_id": "P24-F01", "text": False, "decorative": True})
    elif 25 <= page <= 40:
        raster_pages = {27, 28, 29, 30, 32, 35, 38, 39, 40}
        if page in raster_pages:
            rows = [["Campo", "Valor", "Status", "Nota"], ["A", "123,45", "OK", "Fictício"], ["B", "67,89", "OK", "Controle"], ["C", "90,12", "REVISAR", "V1"]]
            mode = "clean" if page not in {29, 35} else ("low_resolution" if page == 29 else "contrast")
            image = _raster_image([f"{_marker(page)} — TABELA RASTER"], width, height, font_size=11, mode=mode, table=rows, seed=page)
            _add_raster_region(pdf, meta, image, 0, 0, width, height, page_height=height, region_id=f"P{page:02d}-R01", kind="table", quality=mode)
        else:
            meta["has_native_text_layer"] = True
            _header(pdf, meta, title, page_height=height)
            _table(pdf, meta, 42, 142, width - 84, merged=page in {31}, rows=5 if page in {31, 33, 34, 36, 37} else 4, columns=5 if page == 35 else 4)
            if page == 33:
                image = _raster_image(["CÉLULA", "OCRS-033"], 110, 55, font_size=8, seed=page)
                _add_raster_region(pdf, meta, image, 300, 318, 110, 55, page_height=height, region_id="P33-R02", kind="table_cell")
            if page == 34:
                image = _raster_image(["TABELA RASTER 034", "R$ 1.234,56"], 390, 125, font_size=10, seed=page)
                _add_raster_region(pdf, meta, image, 105, 335, 390, 125, page_height=height, region_id="P34-R02", kind="table")
        if page in {36, 37}:
            meta["continuation"] = {"group_id": "table-36-37", "role": "first" if page == 36 else "second", "paired_page": 37 if page == 36 else 36}
        if page in {38, 39}:
            meta["continuation"] = {"group_id": "table-38-39", "role": "first" if page == 38 else "second", "paired_page": 39 if page == 38 else 38}
    elif page == 41:
        meta["has_native_text_layer"] = True
        _native_body(pdf, page, width=width, height=height, title=title)
        _draw_text(pdf, "Paisagem com texto horizontal e nenhum OCR necessário.", 40, 225, page_height=height, size=12)
    elif page == 42:
        image = _raster_image([_marker(page), "TEXTO HORIZONTAL EM PAISAGEM", "OCRS-042"], width, height, font_size=15, seed=page)
        _add_raster_region(pdf, meta, image, 0, 0, width, height, page_height=height, region_id="P42-R01")
    elif page == 43:
        meta["has_native_text_layer"] = True
        _header(pdf, meta, title, page_height=height)
        _draw_text(pdf, "Texto horizontal permanece independente.", 32, 130, page_height=height, size=10)
        _draw_text(pdf, "OBJETO GIRADO 90° — OCRS-043", 420, 480, page_height=height, size=11, angle=90)
    elif page == 44:
        meta["has_native_text_layer"] = True
        _native_body(pdf, page, width=width, height=height, title=title)
        image = _raster_image(["IMAGEM GIRADA", "OCRS-044"], 170, 90, font_size=11, seed=page).rotate(90, expand=True, fillcolor="white")
        _add_raster_region(pdf, meta, image, 260, 260, 90, 170, page_height=height, region_id="P44-R02")
    elif page == 45:
        meta["has_native_text_layer"] = True
        _header(pdf, meta, title, page_height=height)
        for col, x in enumerate((28, 212, 396), 1):
            _draw_text(pdf, f"COLUNA {col} — OCRS-045", x, 120, page_height=height, size=8, font="Helvetica-Bold")
            _draw_wrapped(pdf, "Texto em três colunas com continuidade interna e fluxo separado.", x, 145, 155, page_height=height, size=8)
        _draw_text(pdf, "NOTA LATERAL", 500, 500, page_height=height, size=8, font="Helvetica-Bold")
    elif page in {46, 47}:
        meta["has_native_text_layer"] = True
        _native_body(pdf, page, width=width, height=height, title=title)
        meta["has_figure"] = True
        if page == 46:
            valid = _draw_qr(pdf, 60, 220, 150, page_height=height, value="OCRS-QR-046-FICTITIOUS")
            meta["figures"].append({"figure_id": "P46-F01", "kind": "qr", "valid_generated": valid, "decoded_value": "OCRS-QR-046-FICTITIOUS"})
            _draw_text(pdf, "Rótulo separado: OCRS-QR-046-FICTITIOUS", 250, 280, page_height=height, size=11)
        else:
            valid = _draw_barcode(pdf, 60, 230, page_height=height, value="OCRS-BC-047")
            meta["figures"].append({"figure_id": "P47-F01", "kind": "code128", "valid_generated": valid, "decoded_value": "OCRS-BC-047"})
            _draw_text(pdf, "Identificador legível ao lado: OCRS-BC-047", 60, 300, page_height=height, size=11)
    elif page == 48:
        meta["has_native_text_layer"] = True
        _native_body(pdf, page, width=width, height=height, title=title)
        image = _raster_image(["TEXTO REAL", "OCRS-048"], 190, 105, font_size=10, seed=page)
        _add_raster_region(pdf, meta, image, 45, 230, 190, 105, page_height=height, region_id="P48-R02")
        meta["has_figure"] = True
        pdf.setFillColor(HexColor("#c2ccd5"))
        pdf.rect(350, height - 335, 170, 105, stroke=0, fill=1)
        meta["figures"].append({"figure_id": "P48-F01", "text": False, "decorative": True})
    elif page == 49:
        meta["has_native_text_layer"] = True
        _native_body(pdf, page, width=width, height=height, title=title)
        image = _raster_image(["TRECHO VISÍVEL", "OCRS-049"], 260, 140, font_size=12, seed=page)
        _add_raster_region(pdf, meta, image, -45, 260, 260, 140, page_height=height, region_id="P49-R02")
        meta["notes"].append("imagem parcialmente fora da área visível; trecho interno é válido")
    elif page == 50:
        meta["has_native_text_layer"] = True
        _native_body(pdf, page, width=width, height=height, title=title)
        image = _raster_image(["FORA DA PÁGINA", "OCRS-050"], 180, 100, font_size=10, seed=page)
        _add_raster_region(pdf, meta, image, width + 20, 260, 180, 100, page_height=height, region_id="P50-R02", kind="off_page")
        meta["notes"].append("objeto de figura fora da área visível; não exigir texto raster na saída")
    elif page == 51:
        meta["has_native_text_layer"] = True
        _native_body(pdf, page, width=width, height=height, title=title)
        image = _raster_image(["COMPROVANTE 051", "R$ 551,00", "OCRS-051"], 220, 120, font_size=10, seed=page)
        _add_raster_region(pdf, meta, image, 350, 200, 220, 120, page_height=height, region_id="P51-R02", kind="receipt")
        _table(pdf, meta, 45, 370, 510, rows=3, columns=3)
    elif page == 52:
        meta["has_native_text_layer"] = True
        _native_body(pdf, page, width=width, height=height, title=title)
        image = _raster_image(["TABELA RASTER 052", "A", "B"], 390, 125, font_size=10, seed=page, table=[["Campo", "Valor"], ["A", "52,00"], ["B", "53,00"]])
        _add_raster_region(pdf, meta, image, 50, 225, 390, 125, page_height=height, region_id="P52-R02", kind="table")
        meta["has_figure"] = True
        pdf.setFillColor(HexColor("#c2ccd5"))
        pdf.circle(500, height - 285, 45, stroke=0, fill=1)
        meta["figures"].append({"figure_id": "P52-F01", "text": False, "decorative": True})
    elif page == 53:
        meta["has_native_text_layer"] = True
        _native_body(pdf, page, width=width, height=height, title=title)
        meta["notes"].append("página exclusivamente digital entre páginas OCR; OCR não é necessário")
    elif page == 54:
        meta["has_native_text_layer"] = True
        _native_body(pdf, page, width=width, height=height, title=title)
        for index, top in enumerate((220, 365), 1):
            image = _raster_image([f"REGIÃO OCR {index}", f"OCRS-054-{index}"], 220, 100, font_size=10, seed=page + index)
            _add_raster_region(pdf, meta, image, 42, top, 220, 100, page_height=height, region_id=f"P54-R{index + 1:02d}")
        meta["has_figure"] = True
        pdf.setFillColor(HexColor("#c2ccd5"))
        pdf.rect(370, height - 465, 150, 90, stroke=0, fill=1)
        meta["figures"].append({"figure_id": "P54-F01", "text": False, "decorative": True})
    elif page == 55:
        meta["has_native_text_layer"] = True
        _native_body(pdf, page, width=width, height=height, title=title)
        image = _raster_image(["TABELA RASTER 055", "R$ 55,00"], 340, 130, font_size=11, seed=page, table=[["Campo", "Valor", "Status"], ["A", "55,00", "OK"], ["B", "56,00", "OK"]])
        _add_raster_region(pdf, meta, image, 390, 170, 340, 130, page_height=height, region_id="P55-R02", kind="table")
    elif page in {56, 57}:
        meta["has_native_text_layer"] = True
        _header(pdf, meta, title, page_height=height)
        _table(pdf, meta, 42, 150, width - 84, rows=5, columns=4)
        meta["continuation"] = {"group_id": "table-56-57", "role": "first" if page == 56 else "second", "paired_page": 57 if page == 56 else 56}
        if page == 57:
            image = _raster_image(["CÉLULA OCR", "OCRS-057"], 120, 60, font_size=8, seed=page)
            _add_raster_region(pdf, meta, image, 320, 340, 120, 60, page_height=height, region_id="P57-R02", kind="table_cell")
    elif page == 58:
        meta["has_native_text_layer"] = True
        _native_body(pdf, page, width=width, height=height, title=title)
        meta["has_figure"] = True
        valid_qr = _draw_qr(pdf, 40, 220, 110, page_height=height, value="OCRS-QR-058")
        valid_bar = _draw_barcode(pdf, 240, 235, page_height=height, value="OCRS-BC-058")
        meta["figures"].extend([
            {"figure_id": "P58-F01", "kind": "qr", "valid_generated": valid_qr, "decoded_value": "OCRS-QR-058"},
            {"figure_id": "P58-F02", "kind": "code128", "valid_generated": valid_bar, "decoded_value": "OCRS-BC-058"},
        ])
        image = _raster_image(["IMAGEM TEXTUAL", "OCRS-058"], 190, 105, font_size=10, seed=page)
        _add_raster_region(pdf, meta, image, 360, 235, 190, 105, page_height=height, region_id="P58-R02")
    elif page == 59:
        meta["has_native_text_layer"] = True
        _native_body(pdf, page, width=width, height=height, title=title)
        image = _raster_image(["RASTER PARCIAL", "OCRS-059"], 250, 125, font_size=10, seed=page)
        _add_raster_region(pdf, meta, image, -20, 240, 250, 125, page_height=height, region_id="P59-R02")
        meta["has_figure"] = True
        pdf.setFillColor(HexColor("#c2ccd5"))
        pdf.rect(360, height - 370, 170, 120, stroke=0, fill=1)
        meta["figures"].append({"figure_id": "P59-F01", "text": False, "decorative": True})
    elif page == 60:
        meta["has_native_text_layer"] = True
        _header(pdf, meta, title, page_height=height)
        _draw_wrapped(pdf, "Página densa com texto nativo, OCR regional, figura, tabela, notas e rodapé. Todos os dados são fictícios.", 32, 110, 540, page_height=height, size=10)
        image = _raster_image(["PAINEL OCR 060", "ação — R$ 60,00", "OCRS-060"], 245, 115, font_size=10, seed=page)
        _add_raster_region(pdf, meta, image, 32, 205, 245, 115, page_height=height, region_id="P60-R02")
        _table(pdf, meta, 315, 205, 245, rows=3, columns=3)
        meta["has_figure"] = True
        valid = _draw_qr(pdf, 32, 390, 95, page_height=height, value="OCRS-QR-060")
        meta["figures"].append({"figure_id": "P60-F01", "kind": "qr", "valid_generated": valid, "decoded_value": "OCRS-QR-060"})
    else:
        raise AssertionError(f"unhandled corpus page {page}")
    if meta["has_native_text_layer"]:
        meta["notes"].append("camada PDF nativa intencional")
    if meta["has_native_text_layer"]:
        _footer(pdf, meta, page_width=width, page_height=height)
    meta.pop("page_height", None)


def _parse_pages(values: list[str] | None) -> list[int]:
    if not values:
        return list(range(1, PAGE_COUNT + 1))
    selected: set[int] = set()
    for value in values:
        for part in value.split(","):
            if "-" in part:
                first_text, last_text = part.split("-", 1)
                first, last = int(first_text), int(last_text)
                if first > last:
                    raise ValueError(f"invalid page range: {part}")
                selected.update(range(first, last + 1))
            else:
                selected.add(int(part))
    if not selected or min(selected) < 1 or max(selected) > PAGE_COUNT:
        raise ValueError(f"pages must be within 1..{PAGE_COUNT}")
    return sorted(selected)


def build_manifest(pages: list[dict], pdf_path: Path) -> dict:
    return {
        "schema": "structured-pdf-text.ocr-stress.v1",
        "corpus": "Document_OCR_Stress_V1",
        "version": 1,
        "seed": SEED,
        "page_count": len(pages),
        "source_page_count": PAGE_COUNT,
        "pdf_file": pdf_path.name,
        "pdf_sha256": _sha256(pdf_path),
        "generator": "tests/corpus/ocr_stress_v1/generate.py",
        "dependencies": ["reportlab", "Pillow", "pypdfium2"],
        "ocr_models_loaded": False,
        "production_parser_imported": False,
        "pages": pages,
    }


def write_scenarios(path: Path) -> None:
    lines = [
        "# Document_OCR_Stress_V1 — cenários",
        "",
        "Corpus sintético determinístico de 60 páginas. Todos os nomes, códigos, valores e imagens são fictícios.",
        "O manifesto é a referência executável; esta tabela é somente um mapa para inspeção humana.",
        "",
        "| Página | Bloco | Cenário | Foco de inspeção |",
        "|---:|:---:|---|---|",
    ]
    for page, scenario in enumerate(SCENARIOS, 1):
        block, label = _block_for_page(page)
        focus = "texto nativo" if page == 1 or page in {19, 25, 26, 31, 33, 36, 37, 41, 43, 45, 53} else "OCR/raster/misto"
        lines.append(f"| {page} | {block} | {scenario} | {focus} — {label} |")
    lines.extend(
        [
            "",
            "Continuações obrigatórias: 15–16, 36–37, 38–39 e 56–57.",
            "Páginas 46, 47 e 58 usam QR/Code128 do ReportLab quando disponíveis; o manifesto registra `valid_generated`.",
            "Páginas raster não recebem texto selecionável no PDF final; o texto existe somente nos pixels da imagem.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def generate(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    pages: list[int] | None = None,
    manifest_path: Path | None = None,
) -> tuple[Path, Path]:
    selected = pages or list(range(1, PAGE_COUNT + 1))
    output_dir.mkdir(parents=True, exist_ok=True)
    if selected == list(range(1, PAGE_COUNT + 1)):
        pdf_path = output_dir / PDF_NAME
        target_manifest = manifest_path or DEFAULT_MANIFEST
    else:
        label = f"{selected[0]}-{selected[-1]}" if len(selected) > 1 else str(selected[0])
        pdf_path = output_dir / f"Document_OCR_Stress_V1_pages_{label}.pdf"
        target_manifest = manifest_path or output_dir / f"Document_OCR_Stress_V1_pages_{label}_manifest.json"

    pdf = canvas.Canvas(str(pdf_path), pagesize=PAGE_SIZE_PORTRAIT, invariant=1, pageCompression=1)
    page_records: list[dict] = []
    for output_page, source_page in enumerate(selected, 1):
        width, height = _page_size(source_page)
        pdf.setPageSize((width, height))
        meta = _base_meta(source_page, output_page)
        _draw_page(pdf, meta)
        page_records.append(meta)
        pdf.showPage()
    pdf.setTitle("Document_OCR_Stress_V1")
    pdf.setAuthor("structured-pdf-text synthetic corpus")
    pdf.setSubject("Synthetic OCR stress corpus; all content fictitious")
    pdf.save()

    manifest = build_manifest(page_records, pdf_path)
    target_manifest.parent.mkdir(parents=True, exist_ok=True)
    target_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if selected == list(range(1, PAGE_COUNT + 1)) and manifest_path is None:
        write_scenarios(DEFAULT_MANIFEST.parent / "scenarios.md")
    return pdf_path, target_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--pages", nargs="+", help="pages or ranges, e.g. 15-16 36-37")
    args = parser.parse_args()
    pages = _parse_pages(args.pages)
    pdf_path, manifest_path = generate(args.output_dir, pages=pages, manifest_path=args.manifest)
    print(json.dumps({"pdf": str(pdf_path), "manifest": str(manifest_path), "pages": pages}, ensure_ascii=False))


if __name__ == "__main__":
    main()
