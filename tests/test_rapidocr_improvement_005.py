"""Run 005 corrections from corpus/RapidOCRImprovement.md (section 11).

Each test pins one root cause found in run 004: cell text built from the
cell's own glyphs, links and quotes carried without the native dump, layout
bands split at obstacles, native paragraphs and verbatim runs, list markers,
OCR overlay de-duplication, furniture on /Rotate pages, raster check boxes,
watermarks and shape glyphs in figures.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from reportlab.pdfgen import canvas

from structured_pdf_text import PdfTextExtractor
from structured_pdf_text.assemble.content import _native_paragraphs, _native_text_units
from structured_pdf_text.config import best_extraction_config
from structured_pdf_text.document import (
    Baseline,
    ContentKind,
    EvidenceRef,
    NativeCharacter,
    PageContentBlock,
    SourceKind,
    StructuredListItem,
    TextLine,
    TextToken,
    WritingDirection,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.layout.engine import _body_bands, _is_bold_subtitle, _is_caption
from structured_pdf_text.ocr.pipeline.contracts import WorkGoal
from structured_pdf_text.ocr.pipelines.rapidocr.policy import (
    ADMISSION_POLICY,
    SELECTION_MARK_POLICY,
    WATERMARK_POLICY,
)
from structured_pdf_text.ocr.reconstruct import segment_ocr_paragraphs
from structured_pdf_text.renderers.markdown import _markdown_list_marker, render_markdown
from structured_pdf_text.tables.detector import _cell_text_preserving_vertical_lines
from structured_pdf_text.tables.text_join import recover_cell_text
from structured_pdf_text.text.lists import segment_list_lines
from structured_pdf_text.visibility import characters_without_ink

from ocr_pipeline_helpers import make_work
from test_ocr_pipeline_contracts import evidence


def _token(text: str, box: BBox, *, font: str = "DejaVuSans", size: float = 10.0, weight: int = 400) -> TextToken:
    return TextToken(text=text, bbox=box, sources=[EvidenceRef(SourceKind.NATIVE_PDF, 0, f"t:{text}")],
                     confidence=1.0, normalized_text=text, font_name=font, font_size=size, font_weight=weight)


def _line(text: str, box: BBox, *, line_id: str | None = None, **style) -> TextLine:
    return TextLine(tokens=[_token(text, box, **style)], bbox=box, baseline=Baseline(y=box.y1),
                    direction=WritingDirection.LEFT_TO_RIGHT, native_order_min=None, native_order_max=None,
                    line_id=line_id or f"native:0:{text}")


def _extract(path: Path, pages: tuple[int, ...] | None = None):
    config = replace(best_extraction_config(language="pt-BR", preserve_headers=False), enable_ocr=False,
                     page_indices=pages)
    document = PdfTextExtractor(config).extract(path)
    return document, render_markdown(document)


# -- Table cells: text comes from the cell's own glyphs ---------------------

def test_cell_restoration_keeps_the_case_of_the_cells_own_glyphs():
    page_text = "O total de tokens da página.\nTokens 4.406 3.730 tokens"
    assert recover_cell_text("tokens", page_text) == "tokens"
    assert recover_cell_text("R$-350,00", "Valor R$ -350,00") == "R$ -350,00"


def test_line_text_spanning_another_column_is_not_used_for_a_cell():
    # Textpage reconciliation merged "ML-001" (previous column) into the line.
    tokens = [_token(c, BBox(100 + 6 * i, 0, 105 + 6 * i, 10)) for i, c in enumerate("Notebook")]
    line = TextLine(tokens=tokens, bbox=BBox(100, 0, 148, 10), baseline=Baseline(y=10),
                    direction=WritingDirection.LEFT_TO_RIGHT, native_order_min=None, native_order_max=None,
                    text_override="ML-001 Notebook")
    assert _cell_text_preserving_vertical_lines([line], tokens) == "Notebook"


def test_vertical_cell_letters_are_line_breaks_not_literal_br():
    tokens = [_token(c, BBox(10, 12 * i, 16, 12 * i + 9)) for i, c in enumerate("NORTE")]
    lines = [_line(t.text, t.bbox) for t in tokens]
    text = _cell_text_preserving_vertical_lines(lines, [line.tokens[0] for line in lines])
    assert text == "N\nO\nR\nT\nE" and "<br>" not in text


# -- Native PDFs: links, single-row tables, table of contents ---------------

def _pdf(tmp_path: Path, name: str, draw) -> Path:
    path = tmp_path / f"{name}.pdf"
    pdf = canvas.Canvas(str(path), pagesize=(595, 842))
    pdf.setFont("Helvetica", 10)
    draw(pdf)
    pdf.save()
    return path


def test_link_annotation_reaches_markdown_without_native_dump(tmp_path):
    def draw(pdf):
        pdf.drawString(60, 700, "O termo foi aprovado e vinculado ao codigo V7-01.")
        pdf.drawString(60, 686, "Consultar autenticidade do termo")
        pdf.linkURL("https://exemplo.invalid/verificar", (58, 683, 230, 696), relative=0)
        pdf.drawString(60, 672, "A URL impressa faz parte da evidencia digital.")

    document, markdown = _extract(_pdf(tmp_path, "link", draw))
    assert document.pages[0].native_evidence is None
    assert "[Consultar autenticidade do termo](https://exemplo.invalid/verificar)" in markdown


def test_single_row_ruled_table_is_a_table(tmp_path):
    def draw(pdf):
        pdf.drawString(60, 760, "O quadro a seguir nao contem registros de dados.")
        pdf.setLineWidth(0.8)
        for y in (720, 740):
            pdf.line(44, y, 552, y)
        for x in (44, 213, 394, 552):
            pdf.line(x, 720, x, 740)
        for x, label in ((50, "Pedido"), (220, "Produto"), (400, "Valor")):
            pdf.drawString(x, 727, label)

    document, _ = _extract(_pdf(tmp_path, "header-only", draw))
    assert [[cell.text for cell in table.cells] for table in document.pages[0].tables] == [["Pedido", "Produto", "Valor"]]


def test_dot_leader_index_is_not_promoted_to_a_table(tmp_path):
    entries = ["1. Apresentacao", "2. Contextualizacao e escopo", "3. Execucao financeira",
               "3.1. Demonstrativos", "4. Aceite e conclusao"]

    def draw(pdf):
        for index, entry in enumerate(entries):
            y = 700 - 16 * index
            pdf.drawString(60, y, entry + " " + "." * 40)
            pdf.drawRightString(520, y, str(3 + 4 * index))

    document, markdown = _extract(_pdf(tmp_path, "toc", draw))
    assert not document.pages[0].tables
    assert "<table>" not in markdown and "| --- |" not in markdown


# -- Layout: bands, subtitles, captions --------------------------------------

def test_body_is_split_at_an_obstacle_between_paragraphs():
    above = [_line("Paragrafo antes", BBox(50, 100, 500, 110))]
    below = [_line("Paragrafo depois", BBox(50, 400, 500, 410))]
    figure = BBox(48, 150, 540, 380)
    assert _body_bands(above + below, [figure]) == [above, below]


def test_body_is_not_split_where_a_parallel_column_continues():
    left = [_line(f"esquerda {i}", BBox(50, 100 + 12 * i, 280, 109 + 12 * i)) for i in range(10)]
    right = [_line(f"direita {i}", BBox(310, 100 + 12 * i, 540, 109 + 12 * i)) for i in range(10)]
    subtitle = BBox(50, 160, 150, 170)  # sits in the left column only
    bands = _body_bands([line for pair in zip(left, right) for line in pair], [subtitle])
    assert len(bands) == 1


def test_bold_subtitle_by_font_metadata():
    subtitle = _line("Regras de normalizacao", BBox(45, 100, 160, 111), size=11.2, weight=800)
    assert _is_bold_subtitle(subtitle, subtitle.text, 9.4)
    amount = _line("R$ 5.555,55 | APROVADO | VISIVEL-V7-001", BBox(45, 100, 300, 111), size=11.2, weight=800)
    assert not _is_bold_subtitle(amount, amount.text, 9.4)
    sentence = _line("Texto em negrito que termina.", BBox(45, 100, 200, 111), size=11.2, weight=800)
    assert not _is_bold_subtitle(sentence, sentence.text, 9.4)


def test_lowercase_wrapped_word_is_not_a_caption_marker():
    page = BBox(0, 0, 595, 842)
    line = _line("quadro de referencia de precos.", BBox(46, 221, 198, 236))
    assert not _is_caption(line, line.text, line.text.casefold(), page)
    label = _line("Quadro 2 - Precos de referencia", BBox(46, 221, 198, 236))
    assert _is_caption(label, label.text, label.text.casefold(), page)


# -- Native paragraphs, verbatim runs, lists ---------------------------------

def test_native_paragraphs_split_at_paragraph_gaps_only():
    lines, y = [], 100.0
    for paragraph in range(3):
        for _ in range(2):
            lines.append(_line(f"linha {len(lines)}", BBox(50, y, 500, y + 9)))
            y += 13.4  # 4.4 pt between wrapped lines
        y += 9.0       # 13.4 pt between paragraphs
    assert [len(paragraph) for paragraph in _native_paragraphs(lines)] == [2, 2, 2]


def test_monospaced_runs_are_verbatim_units():
    prose = _line("Esta remissao conserva o texto original.", BBox(50, 100, 400, 109))
    code = [_line(text, BBox(60, 130 + 14 * i, 170, 138 + 14 * i), font="DejaVuSansMono")
            for i, text in enumerate(("PROTOCOLO=AIO-V7-001", "HASH=6fa4-92bd-ptBR", "STATUS=VALIDO"))]
    units = _native_text_units([prose, *code])
    assert [(len(lines), verbatim) for lines, verbatim in units] == [(1, False), (3, True)]


def test_list_markers_render_as_markdown_lists():
    assert _markdown_list_marker("•") == "-"
    assert _markdown_list_marker("☒") == "- ☒"
    assert _markdown_list_marker("1.") == "1."
    assert _markdown_list_marker("a)") == "a)"


def test_continuation_flush_with_the_bullet_joins_a_wrapped_item_only():
    item = _line("• Texto nativo: portugues, multiplos idiomas, numeros e multiplas", BBox(53, 148, 540, 158))
    wrapped = _line("colunas.", BBox(52.5, 164, 90, 171))
    short = _line("• Vistoria complementar pendente", BBox(53, 180, 215, 190))
    following = _line("O codigo foi verificado.", BBox(52.5, 196, 220, 205))
    segments = segment_list_lines([item, wrapped, short, following]).segments
    items = [entry.text for segment in segments if segment.is_list for entry in segment.items]
    assert items == ["Texto nativo: portugues, multiplos idiomas, numeros e multiplas colunas.",
                     "Vistoria complementar pendente"]


def test_uniformly_spaced_ocr_form_fields_are_separate_paragraphs():
    lines = [_line(text, BBox(70, 270 + 22 * i, 220, 281 + 22 * i), line_id=f"ocr:{i}")
             for i, text in enumerate(("Processo: IRINF-2026-0112", "Responsavel: Marina Teixeira",
                                       "Valor retido: R$ 2.150,00"))]
    assert [len(group) for group in segment_ocr_paragraphs(lines)] == [1, 1, 1]


def test_quoted_and_verbatim_blocks_render():
    from structured_pdf_text.renderers.markdown import _render_content_block
    quote = PageContentBlock("b1", 0, ContentKind.TEXT, BBox(0, 0, 1, 1), 0,
                             text="Manifestacao consolidada:\nprosseguir.", blockquote=True)
    code = PageContentBlock("b2", 0, ContentKind.TEXT, BBox(0, 0, 1, 1), 1,
                            text="HASH=6fa4_92bd\nSTATUS=VALIDO", preformatted=True)
    checklist = PageContentBlock("b3", 0, ContentKind.LIST, BBox(0, 0, 1, 1), 2, text="",
                                 list_items=[StructuredListItem("☑", "Conferencia", 0, BBox(0, 0, 1, 1), 0)])
    assert _render_content_block(quote, {}, preserve_hf=False) == "> Manifestacao consolidada:\n> prosseguir."
    assert _render_content_block(code, {}, preserve_hf=False) == "```text\nHASH=6fa4_92bd\nSTATUS=VALIDO\n```"
    assert _render_content_block(checklist, {}, preserve_hf=False) == "- ☑ Conferencia"


def test_quote_bar_marks_its_paragraph_but_table_rules_do_not(tmp_path):
    def draw(pdf):
        pdf.drawString(60, 760, "Paragrafo comum antes da nota.")
        pdf.setFillColorRGB(0.1, 0.4, 0.4)
        pdf.rect(50, 700, 5, 30, fill=1, stroke=0)
        pdf.setFillColorRGB(0, 0, 0)
        pdf.drawString(66, 715, "Observacao complementar com barra lateral.")
        pdf.setLineWidth(0.8)
        for y in (560, 580, 600):
            pdf.line(44, y, 552, y)
        for x in (44, 300, 552):
            pdf.line(x, 560, x, 600)
        for x, y, text in ((50, 587, "Item"), (306, 587, "Valor"), (50, 567, "A"), (306, 567, "10")):
            pdf.drawString(x, y, text)

    _, markdown = _extract(_pdf(tmp_path, "quote", draw))
    assert "> Observacao complementar com barra lateral." in markdown
    assert "> Paragrafo" not in markdown and "> Item" not in markdown


# -- Visibility and furniture -------------------------------------------------

def _char(index: int, text: str, x: float, *, generated: bool = False) -> NativeCharacter:
    box = BBox(x, 0, x if generated else x + 5, 0 if generated else 8)
    return NativeCharacter(page_index=0, char_index=index, text=text, unicode_codepoint=ord(text), bbox=box,
                           generated=generated)


def test_separators_left_by_hidden_text_are_dropped():
    chars = [_char(0, "]", 0), _char(1, " ", 6, generated=True), _char(2, "A", 140), _char(3, " ", 146),
             _char(4, "X", 152), _char(5, " ", 158), _char(6, "Y", 164)]
    visible, _ = characters_without_ink(chars, {0, 6})
    # A real space between visible and hidden text stays; generated ones go.
    assert [c.char_index for c in visible] == [2, 3, 4, 5]


def test_unlabelled_page_counter_and_rotated_occurrence():
    from structured_pdf_text.assemble.repeated_regions import _normalize_signature
    assert _normalize_signature("018 / 095") == _normalize_signature("019 / 095") == "<page-number> / 095"
    assert _normalize_signature("Volume 2026 / 01 de pagamento") == "volume 2026 / 01 de pagamento"


# -- RapidOCR local policies --------------------------------------------------

def _canvas(width=900, height=120):
    return np.full((height, width, 3), 255, dtype=np.uint8)


def _row(x0, y0, x1, y1, text):
    return ([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], text, 0.99)


def test_selection_marks_are_read_from_drawn_boxes():
    image = _canvas()
    image[40:70, 100:102] = image[40:70, 128:130] = 0   # box sides
    image[40:42, 100:130] = image[68:70, 100:130] = 0
    for k in range(6, 24):                              # X mark
        image[40 + k, 100 + k] = image[40 + k, 129 - k] = 0
    image[40:70, 160:164] = 0                           # first glyph of the label
    rows = SELECTION_MARK_POLICY.apply(image, [_row(90, 30, 400, 80, " Aprovado")])
    assert [row[1] for row in rows] == [" Aprovado", "☒"]
    assert min(x for x, _ in rows[0][0]) >= 130         # the label starts after the box


def test_round_letters_and_plain_rows_are_not_check_boxes():
    image = _canvas()
    image[40:70, 100:103] = 0                           # stem
    image[40:43, 100:120] = image[67:70, 100:120] = 0   # partial bars, open right side
    image[52:55, 100:115] = 0
    assert SELECTION_MARK_POLICY.apply(image, [_row(98, 30, 400, 80, "Em revisao")]) == [_row(98, 30, 400, 80, "Em revisao")]


def test_diagonal_faint_rows_are_watermarks_and_tilted_stamps_are_not():
    image = _canvas(600, 400)
    image[100:200, 100:400] = 210                       # faint ink
    image[250:300, 100:400] = 80                        # dark stamp ink
    diagonal = ([[100, 200], [400, 100], [400, 130], [100, 230]], "USO INTERNO", 0.99)
    stamp = ([[100, 270], [400, 250], [400, 280], [100, 300]], "FISCALIZADO", 0.99)
    upright = ([[100, 100], [400, 100], [400, 130], [100, 130]], "legenda clara", 0.99)
    kept, dropped = WATERMARK_POLICY.filter(image, [diagonal, stamp, upright])
    assert dropped == 1 and [row[1] for row in kept] == ["FISCALIZADO", "legenda clara"]


@pytest.mark.parametrize("text,confidence,box,rejected", [
    ("O", .97, BBox(0, 0, 16, 17), True),     # chart circle read as a letter
    ("口", .76, BBox(0, 0, 34, 35), True),
    ("4", .63, BBox(0, 0, 37, 28), True),     # lone glyph with low confidence (QR)
    ("8", 1.0, BBox(0, 0, 9, 11.5), False),   # axis tick
    ("A", 1.0, BBox(0, 0, 10.8, 12.9), False),
    ("NUT", .89, BBox(0, 0, 53, 24), False),
])
def test_lone_shape_glyphs_in_figures_are_not_text(text, confidence, box, rejected):
    _, request, auth = make_work(goal=WorkGoal.FIGURE)
    token = replace(evidence(auth, confidence_normalized=confidence, normalization="domain_0..1"), text=text, bbox=box)
    assert bool(ADMISSION_POLICY.rejection_reason(token, request)) is rejected


def test_antialiased_one_pixel_box_is_read_at_ocr_scale():
    image = _canvas()
    gray = 160                                           # antialiased 1 px rule
    image[40:58, 100] = image[40:58, 117] = gray
    image[40, 100:118] = image[57, 100:118] = gray
    rows = SELECTION_MARK_POLICY.apply(image, [_row(90, 30, 400, 66, " Vistoria complementar pendente")])
    assert [row[1] for row in rows][-1] == "☐"
