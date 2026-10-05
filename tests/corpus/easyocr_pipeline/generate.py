"""Generate the deterministic synthetic EasyOCR regression corpus.

The output PDF is intentionally generated locally instead of being committed.
Each page's Markdown reference and image degradation are described by CASES.
"""
from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parent
PAGE_SIZE = (1240, 1754)
PDF_SIZE = (612, 864)


def _case(case_id: str, features: list[str], markdown: str, **kwargs: object) -> dict[str, object]:
    return {"id": case_id, "features": features, "markdown": markdown, **kwargs}


CASES: tuple[dict[str, object], ...] = (
    _case("clean_scan", ["clean_scan", "paragraph"], "# Relatório\n\nTexto rasterizado limpo para validação do pipeline OCR.", lines=["RELATÓRIO", "Texto rasterizado limpo para validação do pipeline OCR."]),
    _case("low_dpi", ["low_dpi"], "Baixa resolução — ação, órgão e revisão.", lines=["Baixa resolução — ação, órgão e revisão."], effect="low_dpi"),
    _case("tiny_text", ["tiny_text"], "Texto pequeno recuperável: Código 7319-AZ.", lines=["Texto pequeno recuperável: Código 7319-AZ."], font_size=18),
    _case("low_contrast", ["low_contrast"], "Contraste reduzido — informação preservada.", lines=["Contraste reduzido — informação preservada."], effect="low_contrast"),
    _case("blur", ["blur"], "Imagem desfocada com conteúdo legível.", lines=["Imagem desfocada com conteúdo legível."], effect="blur"),
    _case("noise", ["noise"], "Ruído controlado — protocolo 004218.", lines=["Ruído controlado — protocolo 004218."], effect="noise"),
    _case("skew", ["skew"], "Digitalização levemente inclinada.", lines=["Digitalização levemente inclinada."], effect="skew"),
    _case("rotated_90", ["rotated_90"], "Página girada em noventa graus.", lines=["Página girada em noventa graus."], effect="rot90"),
    _case("rotated_180", ["rotated_180"], "Página girada em cento e oitenta graus.", lines=["Página girada em cento e oitenta graus."], effect="rot180"),
    _case("rotated_270", ["rotated_270"], "Página girada em duzentos e setenta graus.", lines=["Página girada em duzentos e setenta graus."], effect="rot270"),
    _case("two_columns", ["two_columns", "reading_order"], "Coluna esquerda: primeiro fluxo.\n\nColuna direita: segundo fluxo.", columns=[["Coluna esquerda:", "primeiro fluxo."], ["Coluna direita:", "segundo fluxo."]]),
    _case("three_columns", ["three_columns", "reading_order"], "Coluna um: fluxo A.\n\nColuna dois: fluxo B.\n\nColuna três: fluxo C.", columns=[["Coluna um:", "fluxo A."], ["Coluna dois:", "fluxo B."], ["Coluna três:", "fluxo C."]]),
    _case("headings", ["headings", "hierarchy"], "# Seção principal\n\n## Subtítulo\n\nTexto do corpo.", lines=["1. SEÇÃO PRINCIPAL", "1.1 Subtítulo", "Texto do corpo."]),
    _case("lists", ["lists", "bullets"], "- Primeiro item\n- Segundo item\n- Terceiro item", lines=["• Primeiro item", "◦ Segundo item", "▪ Terceiro item"]),
    _case("footnotes", ["footnotes", "tiny_text"], "Texto do corpo.\n\n¹ Nota de rodapé em fonte pequena.", lines=["Texto do corpo.", "¹ Nota de rodapé em fonte pequena."], small_last=True),
    _case("forms", ["forms", "critical_data"], "Nome: Ana Silva\nCPF: 529.982.247-25\nData: 03/10/2026", lines=["Nome: Ana Silva", "CPF: 529.982.247-25", "Data: 03/10/2026"]),
    _case("bordered_table", ["bordered_table"], "| Campo | Valor |\n|---|---:|\n| Quantidade | 12 |\n| Total | 1.234,56 |", table=[["Campo", "Valor"], ["Quantidade", "12"], ["Total", "1.234,56"]], bordered=True),
    _case("borderless_table", ["borderless_table"], "| Código | Estado |\n|---|---|\n| AB-17 | Ativo |\n| CD-29 | Pendente |", table=[["Código", "Estado"], ["AB-17", "Ativo"], ["CD-29", "Pendente"]], bordered=False),
    _case("financial_table", ["financial_table", "currency"], "| Item | Valor |\n|---|---:|\n| Serviço | R$ 1.234,56 |\n| Imposto | 12,5% |", table=[["Item", "Valor"], ["Serviço", "R$ 1.234,56"], ["Imposto", "12,5%"]], bordered=True),
    _case("cpf_cnpj_cep", ["cpf_cnpj", "cep", "identifiers"], "CPF: 529.982.247-25\nCNPJ: 04.252.011/0001-10\nCEP: 01001-000", lines=["CPF: 529.982.247-25", "CNPJ: 04.252.011/0001-10", "CEP: 01001-000"]),
    _case("process_numbers", ["process_numbers", "invoice_number"], "Processo: 0000832-12.2019.5.02.0001\nNota fiscal: 847201", lines=["Processo: 0000832-12.2019.5.02.0001", "Nota fiscal: 847201"]),
    _case("hybrid_native_scan", ["hybrid_native_scan", "hybrid"], "Texto rasterizado: etapa visual.\nTexto nativo: etapa digital.", lines=["Texto rasterizado: etapa visual."], native_text="Texto nativo: etapa digital."),
    _case("bad_hidden_ocr", ["bad_hidden_ocr", "hybrid"], "Camada visível correta: protocolo 48271.", lines=["Camada visível correta: protocolo 48271."], hidden_text="Camada invisível errada: protocolo 48217."),
    _case("screenshot", ["screenshots"], "PAINEL\nStatus: concluído\nUsuário: operador-7", lines=["PAINEL", "Status: concluído", "Usuário: operador-7"], background=(242, 246, 252)),
    _case("accented_portuguese", ["accented_portuguese", "unicode"], "Ação, órgão, informação, revisão e São Cristóvão.", lines=["Ação, órgão, informação, revisão", "e São Cristóvão."]),
    _case("hyphenation", ["hyphenation", "paragraph"], "A documentação extraordinária continua na linha seguinte.", lines=["A documentação extraordi-", "nária continua na linha seguinte."]),
    _case("dark_background", ["dark_background", "inverted_text"], "TEXTO CLARO EM FUNDO ESCURO\nCódigo: DX-740.", lines=["TEXTO CLARO EM FUNDO ESCURO", "Código: DX-740."], background=(22, 35, 58), foreground=(250, 250, 250)),
    _case("jpeg_artifacts", ["jpeg_artifacts", "compression"], "Artefatos JPEG — número 928374.", lines=["Artefatos JPEG — número 928374."], effect="jpeg"),
    _case("stamps", ["stamps", "figure_text"], "Documento recebido\nCARIMBO: PROTOCOLADO\nAssinatura: M. Costa", lines=["Documento recebido", "CARIMBO: PROTOCOLADO", "Assinatura: M. Costa"], stamp=True),
)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _draw_page(case: dict[str, object]) -> Image.Image:
    background = tuple(case.get("background", (255, 255, 255)))
    foreground = tuple(case.get("foreground", (24, 24, 24)))
    image = Image.new("RGB", PAGE_SIZE, background)
    draw = ImageDraw.Draw(image)
    normal_font = _font(int(case.get("font_size", 38)))
    small_font = _font(21)
    y = 100

    columns = case.get("columns")
    if columns:
        count = len(columns)
        col_width = 1080 // count
        for index, column_lines in enumerate(columns):
            x = 70 + index * col_width
            for text in column_lines:
                draw.text((x, y), str(text), fill=foreground, font=normal_font)
                y += 75
            y = 100
        return image

    table = case.get("table")
    if table:
        top, row_h, left, col_w = 170, 90, 70, 540
        for row_index, row in enumerate(table):
            if case.get("bordered"):
                draw.rectangle((left, top + row_index * row_h, left + col_w, top + (row_index + 1) * row_h), outline=foreground, width=2)
                draw.line((left + col_w // 2, top + row_index * row_h, left + col_w // 2, top + (row_index + 1) * row_h), fill=foreground, width=2)
            draw.text((left + 15, top + row_index * row_h + 22), str(row[0]), fill=foreground, font=normal_font)
            draw.text((left + col_w // 2 + 15, top + row_index * row_h + 22), str(row[1]), fill=foreground, font=normal_font)
        return image

    lines = [str(line) for line in case.get("lines", [])]
    for index, text in enumerate(lines):
        use_font = small_font if case.get("small_last") and index == len(lines) - 1 else normal_font
        draw.text((75, y), text, fill=foreground, font=use_font)
        y += 75 if use_font == normal_font else 42
    if case.get("stamp"):
        draw.rounded_rectangle((690, 790, 1120, 1020), radius=20, outline=(175, 30, 30), width=12)
        draw.text((735, 855), "PROTOCOLADO", fill=(175, 30, 30), font=_font(45))

    effect = case.get("effect")
    if effect == "low_dpi":
        image = image.resize((220, 311), Image.Resampling.BILINEAR).resize(PAGE_SIZE, Image.Resampling.NEAREST)
    elif effect == "low_contrast":
        # Redraw in a narrow luminance range to preserve recoverable structure.
        image = Image.new("RGB", PAGE_SIZE, (214, 214, 214))
        d = ImageDraw.Draw(image)
        for index, text in enumerate(lines):
            d.text((75, 100 + index * 75), text, fill=(164, 164, 164), font=normal_font)
    elif effect == "blur":
        image = image.filter(ImageFilter.GaussianBlur(radius=1.5))
    elif effect == "noise":
        import numpy as np
        array = np.asarray(image).astype("int16")
        rng = np.random.default_rng(773)
        noise = rng.normal(0, 8, array.shape).astype("int16")
        image = Image.fromarray(np.clip(array + noise, 0, 255).astype("uint8"))
    elif effect == "skew":
        image = image.rotate(1.8, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=background)
    elif effect == "rot90":
        image = image.rotate(90, expand=True)
    elif effect == "rot180":
        image = image.rotate(180, expand=False)
    elif effect == "rot270":
        image = image.rotate(270, expand=True)
    elif effect == "jpeg":
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=18, optimize=False)
        buffer.seek(0)
        image = Image.open(buffer).convert("RGB")
    return image


def generate(output_dir: Path | None = None) -> Path:
    output_dir = output_dir or ROOT / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = output_dir / "EasyOCR_Pipeline_Corpus_V1.pdf"
    pdf = canvas.Canvas(str(pdf_path), pagesize=PDF_SIZE)
    for index, case in enumerate(CASES, start=1):
        image = _draw_page(case)
        image_buffer = BytesIO()
        image.save(image_buffer, format="PNG")
        image_buffer.seek(0)
        width, height = PDF_SIZE
        if image.width > image.height:
            width, height = PDF_SIZE[1], PDF_SIZE[0]
            pdf.setPageSize((width, height))
        else:
            pdf.setPageSize(PDF_SIZE)
        pdf.drawImage(ImageReader(image_buffer), 0, 0, width=width, height=height)
        native_text = case.get("native_text")
        if native_text:
            pdf.setFillColorRGB(0, 0, 0)
            pdf.setFont("Helvetica", 10)
            pdf.drawString(28, 22, str(native_text))
        hidden_text = case.get("hidden_text")
        if hidden_text:
            pdf.saveState()
            text = pdf.beginText(28, 22)
            text.setTextRenderMode(3)
            text.setFont("Helvetica", 10)
            text.textLine(str(hidden_text))
            pdf.drawText(text)
            pdf.restoreState()
        pdf.showPage()
        ref_name = f"{case['id']}.md"
        (ROOT / "ground_truth" / ref_name).write_text(str(case["markdown"]) + "\n", encoding="utf-8")
    pdf.save()

    manifest = {
        "schema": "structured-pdf-text.easyocr-pipeline.v2",
        "generator": "tests/corpus/easyocr_pipeline/generate.py",
        "pdf": "outputs/EasyOCR_Pipeline_Corpus_V1.pdf",
        "documents": [
            {
                "id": case["id"], "page": index, "ground_truth": f"ground_truth/{case['id']}.md",
                "features": case["features"],
            }
            for index, case in enumerate(CASES, start=1)
        ],
    }
    (ROOT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return pdf_path


if __name__ == "__main__":
    print(generate())
