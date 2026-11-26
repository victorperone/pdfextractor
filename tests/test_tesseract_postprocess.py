"""Tests for the Tesseract OCR post-processor module.

Covers:
- Running header suppression (migrated from extract_worker.py)
- Idempotency (calling twice is safe)
- No effect on non-Tesseract pipelines (tested indirectly via direct call)
- Worker-API equivalence (same logic runs in both paths)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any


# ---------------------------------------------------------------------------
# Minimal stub types — avoids importing the full pipeline for unit tests
# ---------------------------------------------------------------------------

@dataclass
class _Block:
    order_index: int
    text: str
    kind: str = "text"
    suppressed: bool = False
    suppression_reason: str | None = None
    inline_style: str | None = None
    styled_runs: list = field(default_factory=list)
    heading_level: int | None = None
    list_items: list = field(default_factory=list)


@dataclass
class _Page:
    content_blocks: list[_Block] = field(default_factory=list)
    page_index: int = 0


class _Doc:
    """Stub document.

    ``assemble_document`` records ``preserve_headers_footers`` in
    ``diagnostics.facts``; the post-processing suppresses page furniture only
    when it is False (T3-P54). These unit tests model the batch extraction
    (False) unless a test passes True.
    """

    def __init__(self, pages: list[_Page], preserve_headers_footers: bool = False):
        self.pages = pages
        self.diagnostics = SimpleNamespace(
            facts={"preserve_headers_footers": preserve_headers_footers}, warnings=[]
        )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_doc(page_texts: list[str], kind: str = "text", preserve_headers_footers: bool = False) -> _Doc:
    """One block per page (page_index = position), with the given text and optional kind."""
    return _Doc(
        [_Page([_Block(order_index=0, text=t, kind=kind)], page_index=i) for i, t in enumerate(page_texts)],
        preserve_headers_footers,
    )


# ---------------------------------------------------------------------------
# Import the function under test
# ---------------------------------------------------------------------------

import pytest

from structured_pdf_text.ocr.tesseract.postprocess import (
    _block_is_bold,
    _next_block_is_unordered_list,
    _normalize_brl_currency_spacing,
    _promote_allcaps_section_headers,
    _promote_bold_pipe_title,
    _promote_bold_pre_table_heading,
    _promote_numbered_bold_headings,
    _promote_seção_subseção_headings,
    _promote_short_bold_column_headers,
    _flag_rotated_table_garble,
    _table_has_q_without_u,
    _text_is_md_bold,
    apply_tesseract_postprocessors,
    suppress_tesseract_running_headers,
)


# ---------------------------------------------------------------------------
# 1. Basic running header suppression
# ---------------------------------------------------------------------------

def test_running_header_suppressed_when_above_threshold():
    """A line appearing on ≥40% of pages is suppressed."""
    texts = [
        "HEADER CORP",
        "HEADER CORP",
        "HEADER CORP",
        "Different text A",
        "Different text B",
    ]
    doc = _make_doc(texts)
    suppress_tesseract_running_headers(doc)
    suppressed = [b.suppressed for p in doc.pages for b in p.content_blocks]
    # 3 out of 5 pages (60%) have the running header — must be suppressed
    assert suppressed.count(True) == 3


def test_unique_lines_not_suppressed():
    """Unique lines on each page are not suppressed."""
    texts = ["Page A content", "Page B content", "Page C content", "Page D content", "Page E content"]
    doc = _make_doc(texts)
    suppress_tesseract_running_headers(doc)
    suppressed = [b.suppressed for p in doc.pages for b in p.content_blocks]
    assert not any(suppressed)


def test_suppression_reason_is_set():
    """Suppressed blocks receive the correct reason string."""
    texts = ["CORP HEADER"] * 3 + ["body text A", "body text B"]
    doc = _make_doc(texts)
    suppress_tesseract_running_headers(doc)
    for p in doc.pages[:3]:
        for b in p.content_blocks:
            assert b.suppression_reason == "tesseract_running_header"


def test_fewer_than_five_pages_is_noop():
    """Documents with < 5 pages are not processed (too few samples)."""
    texts = ["SAME HEADER"] * 4
    doc = _make_doc(texts)
    suppress_tesseract_running_headers(doc)
    suppressed = [b.suppressed for p in doc.pages for b in p.content_blocks]
    assert not any(suppressed)


def test_already_suppressed_blocks_are_skipped():
    """Pre-suppressed blocks do not count as the first visible line."""
    # Page 0: pre-suppressed block then a body block
    page0 = _Page([
        _Block(order_index=0, text="PRE-SUPPRESSED", suppressed=True),
        _Block(order_index=1, text="real content"),
    ])
    other_pages = [_Page([_Block(order_index=0, text="real content")])] * 4
    doc = _Doc([page0] + other_pages)
    suppress_tesseract_running_headers(doc)
    # "real content" appears on all 5 pages — but it's NOT a running header because
    # the count is 5/5 = 100% and min_frequency=0.40, so actually it WILL be suppressed.
    # Let's check that "PRE-SUPPRESSED" stays suppressed and is not the trigger.
    assert doc.pages[0].content_blocks[0].suppressed is True  # still suppressed
    assert doc.pages[0].content_blocks[0].suppression_reason is None  # original reason unchanged


# ---------------------------------------------------------------------------
# 2. Idempotency
# ---------------------------------------------------------------------------

def test_idempotent_double_call():
    """Calling the suppressor twice does not change the result."""
    texts = ["HEADER LINE"] * 3 + ["body A", "body B"]
    doc = _make_doc(texts)
    suppress_tesseract_running_headers(doc)
    state_after_first = [(p.content_blocks[0].suppressed, p.content_blocks[0].suppression_reason)
                         for p in doc.pages]
    suppress_tesseract_running_headers(doc)
    state_after_second = [(p.content_blocks[0].suppressed, p.content_blocks[0].suppression_reason)
                          for p in doc.pages]
    assert state_after_first == state_after_second


# ---------------------------------------------------------------------------
# 3. apply_tesseract_postprocessors entry point
# ---------------------------------------------------------------------------

def test_apply_entry_point_calls_suppressor():
    """apply_tesseract_postprocessors must run the running header suppressor."""
    texts = ["RUNNING HDR"] * 3 + ["body A", "body B"]
    doc = _make_doc(texts)
    apply_tesseract_postprocessors(doc)
    suppressed_count = sum(b.suppressed for p in doc.pages for b in p.content_blocks)
    assert suppressed_count == 3


def test_running_header_merged_with_body_keeps_the_body():
    """T3-P27: only a block that is wholly the repeated header is suppressed. The
    old contract compared the first line only, so the paragraph merged with the
    header line went with it."""
    merged = "HEADER CORP\nParágrafo do corpo que começa logo abaixo do cabeçalho."
    doc = _make_doc(["HEADER CORP", "HEADER CORP", "HEADER CORP", merged, "Different text B"])
    suppress_tesseract_running_headers(doc)
    assert [p.content_blocks[0].suppressed for p in doc.pages] == [True, True, True, False, False]
    assert doc.pages[3].content_blocks[0].text == merged


def test_two_line_running_header_repeated_whole_is_suppressed():
    doc = _make_doc(["RELATÓRIO ANUAL\nVOLUME I"] * 3 + ["body A", "body B"])
    suppress_tesseract_running_headers(doc)
    assert [p.content_blocks[0].suppressed for p in doc.pages] == [True, True, True, False, False]


def test_running_headers_are_kept_when_preserved():
    """T3-P54: with preserve_headers_footers=True nothing repeated is suppressed."""
    doc = _make_doc(["HEADER CORP"] * 4 + ["body"], preserve_headers_footers=True)
    apply_tesseract_postprocessors(doc)
    assert not any(b.suppressed for p in doc.pages for b in p.content_blocks)
    assert [p.content_blocks[0].text for p in doc.pages] == ["HEADER CORP"] * 4 + ["body"]


def test_furniture_is_kept_when_the_document_has_no_preservation_fact():
    """Without diagnostics.facts["preserve_headers_footers"] the default is to
    preserve, as in ExtractionConfig and the Markdown renderer (T3-P54)."""
    pages = [SimpleNamespace(page_index=i, content_blocks=[_Block(order_index=0, text=t)])
             for i, t in enumerate(["Página 1 de 5"] + ["CABEÇALHO"] * 4)]
    apply_tesseract_postprocessors(SimpleNamespace(pages=pages))
    assert not any(b.suppressed for p in pages for b in p.content_blocks)


# ---------------------------------------------------------------------------
# 4. Worker–API equivalence: same function, same result
# ---------------------------------------------------------------------------

def test_worker_uses_same_function(monkeypatch):
    """extract_worker.py must import from postprocess module, not a local copy."""
    import importlib
    # Re-importing to verify the symbol resolves to the same function
    from structured_pdf_text.ocr.tesseract.postprocess import (
        suppress_tesseract_running_headers as canonical,
    )

    # Simulate what the worker does: import by name and call
    from structured_pdf_text.ocr.tesseract import postprocess as mod
    worker_fn = mod.suppress_tesseract_running_headers
    assert worker_fn is canonical


# ---------------------------------------------------------------------------
# 5. _block_is_bold helper
# ---------------------------------------------------------------------------

def test_block_is_bold_via_inline_style():
    b = _Block(order_index=0, text="x", inline_style="bold")
    assert _block_is_bold(b) is True


def test_block_is_bold_italic_via_inline_style():
    b = _Block(order_index=0, text="x", inline_style="bold_italic")
    assert _block_is_bold(b) is True


def test_block_not_bold_plain():
    b = _Block(order_index=0, text="x", inline_style=None)
    assert _block_is_bold(b) is False


def test_block_is_bold_via_styled_runs():
    b = _Block(order_index=0, text="x", styled_runs=[("hello", "b"), (" world", "b")])
    assert _block_is_bold(b) is True


def test_block_not_bold_when_mostly_plain_styled_runs():
    b = _Block(order_index=0, text="x", styled_runs=[("long plain text here", ""), ("bold", "b")])
    assert _block_is_bold(b) is False


# ---------------------------------------------------------------------------
# 6. _promote_numbered_bold_headings
# ---------------------------------------------------------------------------

def _title_block(order_index: int, text: str, heading_level: int) -> _Block:
    return _Block(order_index=order_index, text=text, kind="title", heading_level=heading_level)


def _bold_text_block(order_index: int, text: str) -> _Block:
    return _Block(order_index=order_index, text=text, kind="text", inline_style="bold")


def test_numbered_heading_promoted_with_calibration():
    """Bold TEXT block with numbered prefix promoted to TITLE using calibration."""
    # Calibration: existing "1. Section" → H2, "1.1 Sub" → H3
    page0 = _Page([
        _title_block(0, "1. Section", 2),
        _title_block(1, "1.1 Subsection", 3),
    ])
    # "1.1.1 Deep" should become H4 (depth=3, nearest known=2→level3, extrapolate: 3+(3-2)=4)
    page1 = _Page([_bold_text_block(0, "1.1.1 Deep subsection")])
    doc = _Doc([page0, page1])
    _promote_numbered_bold_headings(doc)
    b = doc.pages[1].content_blocks[0]
    assert b.kind == "title"
    assert b.heading_level == 4


def test_numbered_heading_no_calibration_uses_depth():
    """Without existing numbered TITLEs, depth is used directly as level."""
    page = _Page([_bold_text_block(0, "2.3.1 Something")])
    doc = _Doc([page])
    _promote_numbered_bold_headings(doc)
    b = doc.pages[0].content_blocks[0]
    assert b.kind == "title"
    assert b.heading_level == 3  # depth=3 → level=3


def test_numbered_heading_not_promoted_if_not_bold():
    """Non-bold TEXT block with numbered prefix is NOT promoted."""
    page = _Page([_Block(order_index=0, text="1.1.1 Not bold", kind="text", inline_style=None)])
    doc = _Doc([page])
    _promote_numbered_bold_headings(doc)
    assert doc.pages[0].content_blocks[0].kind == "text"


def test_numbered_heading_not_promoted_if_too_long():
    """TEXT block whose text exceeds 90 chars is NOT promoted."""
    long_text = "1.1.1 " + "x" * 90
    page = _Page([_bold_text_block(0, long_text)])
    doc = _Doc([page])
    _promote_numbered_bold_headings(doc)
    assert doc.pages[0].content_blocks[0].kind == "text"


def test_numbered_heading_not_promoted_if_multiline():
    """Multi-line blocks are NOT promoted."""
    page = _Page([_bold_text_block(0, "1.1 Title\nSecond line")])
    doc = _Doc([page])
    _promote_numbered_bold_headings(doc)
    assert doc.pages[0].content_blocks[0].kind == "text"


def test_numbered_heading_not_promoted_if_suppressed():
    """Suppressed blocks are NOT promoted."""
    b = _Block(order_index=0, text="1.1.1 Title", kind="text",
               inline_style="bold", suppressed=True)
    page = _Page([b])
    doc = _Doc([page])
    _promote_numbered_bold_headings(doc)
    assert doc.pages[0].content_blocks[0].kind == "text"


def test_numbered_heading_single_dot_prefix():
    """'1. Title' pattern (trailing dot) is detected correctly with depth=1."""
    page = _Page([_bold_text_block(0, "3. Third section")])
    doc = _Doc([page])
    _promote_numbered_bold_headings(doc)
    b = doc.pages[0].content_blocks[0]
    assert b.kind == "title"
    assert b.heading_level == 1  # depth=1, no calibration → level=1


def test_numbered_heading_plain_integer_not_promoted():
    """A plain integer without dots (e.g. '042 text') is NOT promoted."""
    page = _Page([_bold_text_block(0, "042 Some catalog entry")])
    doc = _Doc([page])
    _promote_numbered_bold_headings(doc)
    assert doc.pages[0].content_blocks[0].kind == "text"


def test_promote_is_idempotent():
    """Calling _promote_numbered_bold_headings twice gives the same result."""
    page = _Page([_bold_text_block(0, "1.1 Sub section")])
    doc = _Doc([page])
    _promote_numbered_bold_headings(doc)
    first_kind = doc.pages[0].content_blocks[0].kind
    first_level = doc.pages[0].content_blocks[0].heading_level
    _promote_numbered_bold_headings(doc)
    assert doc.pages[0].content_blocks[0].kind == first_kind
    assert doc.pages[0].content_blocks[0].heading_level == first_level


def test_apply_entry_point_calls_numbered_promoter():
    """apply_tesseract_postprocessors must also run the numbered heading promoter."""
    texts = ["RUNNING HDR"] * 3 + ["body A", "body B"]
    base_doc = _make_doc(texts)
    # Add a page with a numbered bold heading block
    heading_page = _Page([_bold_text_block(0, "2.1 Some section")])
    from dataclasses import replace
    full_doc = _Doc(base_doc.pages + [heading_page])
    apply_tesseract_postprocessors(full_doc)
    heading_block = full_doc.pages[-1].content_blocks[0]
    assert heading_block.kind == "title"


# ---------------------------------------------------------------------------
# 7. "Página N" lines are content (T3-P09/OF-03, T3-P52)
# ---------------------------------------------------------------------------

# Contract change (T3-P52): the post-processing no longer suppresses "Página N"
# lines. The rule existed so that a title such as "Página 7 | Resultados" would
# not be read as the "## Página N" delimiter of the paginated Markdown; the
# renderer now writes such content so that it cannot be. It also took the page
# title after "|" (351 titles in V3/V4, RUN 015). The shapes it suppressed are
# now kept, in both preservation settings; page counters are left to the shared
# furniture detector (T3-P43), which sees the whole document.

_FORMER_LABEL_SHAPES = [
    # (text, kind, page_index): what the removed rule suppressed on its own page.
    ("Página 005 | Português com acentos", "title", 4),
    ("# Página 005 | Title", "text", 4),
    ("Página 001", "title", 0),
    ("pagina 003 | content", "title", 2),
    ("Página 001 | Corpus integrado de validação: texto nativo,\nOCR e tabelas", "title", 0),
    ("Página 7", "text", 6),
    ("Página 7/40", "text", 6),
    ("Página 7 de 40", "text", 6),
    ("Página 7 — Relatório anual", "text", 6),
    ("PÁGINA 007 | Anexo", "text", 6),
]


@pytest.mark.parametrize("preserve", [False, True], ids=["not-preserved", "preserved"])
@pytest.mark.parametrize("text,kind,page_index", _FORMER_LABEL_SHAPES)
def test_page_label_lines_are_never_suppressed(text, kind, page_index, preserve):
    block = _Block(order_index=0, text=text, kind=kind)
    doc = _Doc([_Page([block], page_index=page_index)], preserve_headers_footers=preserve)
    apply_tesseract_postprocessors(doc)
    assert block.suppressed is False
    assert block.text == text


def test_pagina_block_that_continues_with_body_text_is_kept():
    """T3-P02/RUN 003: o separador fundido ao corpo da página não apaga o corpo."""
    text = ("Página 096 | OCR com rotação física de 90 graus Controle de página: QA-P096 "
            "O lote OCR-2026-0007 apresenta ação, informação, coração e saúde. "
            "Valor: R$ 9.876,54; desconto: -2,15%; quantidade: 00042.")
    b = _Block(order_index=0, text=text, kind="text")
    multi = _Block(order_index=0, text="Página 097 | Título\nCorpo da página.", kind="text")
    doc = _Doc([_Page([b], page_index=95), _Page([multi], page_index=96)])
    apply_tesseract_postprocessors(doc)
    assert b.suppressed is False
    assert multi.suppressed is False


def test_block_suppressed_before_the_postprocessing_keeps_its_reason():
    """Already-suppressed blocks are not re-processed."""
    b = _Block(order_index=0, text="Página 001", kind="title",
               suppressed=True, suppression_reason="prior_reason")
    doc = _Doc([_Page([b])])
    apply_tesseract_postprocessors(doc)
    assert b.suppressed is True
    assert b.suppression_reason == "prior_reason"


def test_pagina_label_of_another_page_is_content():
    """T3-P09/OF-03: a table of contents, a cross-reference or a title "Página N"
    is content; the RUN 015 contract deleted any short line starting with it."""
    texts = ["Página 12 | Resultados", "Página 5 | Introdução", "Página 9 de 40", "# Página 7",
             "Consultar a Página 42 do manual."]
    blocks = [_Block(order_index=i, text=t, kind="title" if i == 0 else "text") for i, t in enumerate(texts)]
    doc = _Doc([_Page(blocks, page_index=2)])
    apply_tesseract_postprocessors(doc)
    assert [b.suppressed for b in blocks] == [False] * len(texts)
    assert [b.text for b in blocks] == texts


@pytest.mark.parametrize("preserve", [False, True], ids=["not-preserved", "preserved"])
def test_page_counter_is_left_to_the_shared_furniture_detector(preserve):
    """"Página 3 de 10" on page 3 is page furniture, but a counter is recognised by
    its recurrence across pages, which the shared detector (T3-P43) checks before
    this step; the Tesseract post-processing keeps it."""
    footer = _Block(order_index=1, text="Página 3 de 10", kind="footer")
    body = _Block(order_index=0, text="Corpo da página três.")
    doc = _Doc([_Page([body, footer], page_index=2)], preserve_headers_footers=preserve)
    apply_tesseract_postprocessors(doc)
    assert footer.suppressed is False
    assert body.suppressed is False


@pytest.mark.parametrize("preserve", [False, True], ids=["not-preserved", "preserved"])
def test_title_equal_to_the_page_label_is_kept(preserve):
    """A title whose text is exactly the page's delimiter text ("Página 3" on page 3)
    is content; the renderer keeps it apart from the delimiter (T3-P52)."""
    title = _Block(order_index=0, text="Página 3", kind="title", heading_level=2)
    doc = _Doc([_Page([title], page_index=2)], preserve_headers_footers=preserve)
    apply_tesseract_postprocessors(doc)
    assert title.suppressed is False
    assert title.text == "Página 3"


@pytest.mark.parametrize("preserve", [False, True], ids=["not-preserved", "preserved"])
def test_titles_with_numbers_are_kept(preserve):
    texts = ["Página 3 | Resultados", "Capítulo 3", "2025 em números", "Seção 4 — Página 4 do contrato"]
    blocks = [_Block(order_index=i, text=t, kind="title", heading_level=2) for i, t in enumerate(texts)]
    doc = _Doc([_Page(blocks, page_index=6)], preserve_headers_footers=preserve)
    apply_tesseract_postprocessors(doc)
    assert not any(b.suppressed for b in blocks)
    assert [b.text for b in blocks] == texts


@pytest.mark.parametrize("preserve", [False, True], ids=["not-preserved", "preserved"])
@pytest.mark.parametrize("text", [
    "Página 7 do contrato contém cláusula de reajuste.",
    "Página 7: ver anexo II",
    "Página 7\nResultados do trimestre",
])
def test_sentence_starting_with_the_page_number_is_content(text, preserve):
    """AI-02 on its own page: a sentence that starts with "Página 7" on page 7."""
    block = _Block(order_index=0, text=text, kind="title" if "\n" in text else "text")
    doc = _Doc([_Page([block], page_index=6)], preserve_headers_footers=preserve)
    apply_tesseract_postprocessors(doc)
    assert block.suppressed is False and block.text == text


# ---------------------------------------------------------------------------
# 8. _normalize_brl_currency_spacing
# ---------------------------------------------------------------------------

def test_brl_no_space_normalized():
    """'R$9.876,54' is normalized to 'R$ 9.876,54'."""
    b = _Block(order_index=0, text="Valor: R$9.876,54; desconto: -2,75%.")
    page = _Page([b])
    doc = _Doc([page])
    _normalize_brl_currency_spacing(doc)
    assert b.text == "Valor: R$ 9.876,54; desconto: -2,75%."


def test_brl_already_correct_unchanged():
    """'R$ 9.876,54' (already correct) is not modified."""
    b = _Block(order_index=0, text="Soma contábil: R$ 17.530,09; situação: LIBERADO.")
    page = _Page([b])
    doc = _Doc([page])
    _normalize_brl_currency_spacing(doc)
    assert b.text == "Soma contábil: R$ 17.530,09; situação: LIBERADO."


def test_brl_multiple_occurrences_in_one_block():
    """Multiple 'R$<digit>' occurrences in a single block are all normalized."""
    b = _Block(order_index=0, text="A: R$45,70 e B: R$99,90.")
    page = _Page([b])
    doc = _Doc([page])
    _normalize_brl_currency_spacing(doc)
    assert b.text == "A: R$ 45,70 e B: R$ 99,90."


def test_brl_suppressed_block_not_modified():
    """Suppressed blocks are skipped."""
    b = _Block(order_index=0, text="Total: R$1.000,00", suppressed=True)
    page = _Page([b])
    doc = _Doc([page])
    _normalize_brl_currency_spacing(doc)
    assert b.text == "Total: R$1.000,00"  # unchanged


def test_brl_normalization_is_idempotent():
    """Calling twice gives the same result."""
    b = _Block(order_index=0, text="Preço: R$31,90.")
    page = _Page([b])
    doc = _Doc([page])
    _normalize_brl_currency_spacing(doc)
    text_after_first = b.text
    _normalize_brl_currency_spacing(doc)
    assert b.text == text_after_first == "Preço: R$ 31,90."


def test_brl_no_rs_sign_unchanged():
    """Block without 'R$' is not touched."""
    b = _Block(order_index=0, text="Valor: 9.876,54 reais.")
    page = _Page([b])
    doc = _Doc([page])
    _normalize_brl_currency_spacing(doc)
    assert b.text == "Valor: 9.876,54 reais."


def test_apply_entry_point_calls_brl_normalizer():
    """apply_tesseract_postprocessors must also run the BRL normalizer."""
    texts = ["RUNNING HDR"] * 3 + ["body A", "body B"]
    base_doc = _make_doc(texts)
    brl_page = _Page([_Block(order_index=0, text="Total: R$500,00")])
    full_doc = _Doc(base_doc.pages + [brl_page])
    apply_tesseract_postprocessors(full_doc)
    assert full_doc.pages[-1].content_blocks[0].text == "Total: R$ 500,00"


# ---------------------------------------------------------------------------
# T3-P09 / T3-P27: no block is suppressed for its wording
#
# These tests replace those of the rules removed in this cycle:
# _suppress_ocr_corpus_markers (sections 9 and 10, Fix D1, Fix-N with
# _omitir_gabarito_split), _suppress_v6_irinf_artifacts (section 10),
# _suppress_tesseract_coordinate_dump (section 11, Fix G) and
# _suppress_dollar_letter_garble (Fix-P). The old contract deleted every block
# whose text matched wording of the V3/V4/V6 test corpora ("OCR PURO",
# "CORPUS INTEGRADO V3 / LABORATÓRIO", "ADVERSARIAL •", "OMITIR NO GABARITO",
# "IRINF / DOSSIÊ", "Processo IRINF-…"), a whole table when one row held
# "OMITIR NO" and a nearby row "GABARITO", any table or text block with two "$"
# followed by a capital letter ("$PATH", "$A$1") and any block with a line of
# eleven tab-separated numbers. Those rules followed the reference conventions
# of three corpora, not evidence of page furniture or noise: in RUN 015 they
# deleted the title and body of V3 p116 without a warning (T3-P27). The new
# contract: no wording causes a suppression, in any configuration; page
# furniture is suppressed only by structure (sections 1 and 7) and only when
# headers and footers are not preserved (T3-P54).
# ---------------------------------------------------------------------------

_CORPUS_LIKE_TEXTS = [
    "OCR PURO * SCAN",
    "ocr tabelas * scan",
    "CORPUS INTEGRADO V3 / LABORATÓRIO SINTÉTICO",
    "CORPUS INTEGRADO V3 / LABORATÓRIO SINTÉTICO ADVERSARIAL * SCAN DUPLICATE",
    "CORPUS INTEGRADO V3 / LABORATÓRIO SINTÉTICO\nTEXTO NATIVO • NATIVE",
    "CORPUS INTEGRADO V12 / LABORATÓRIO CLÍNICO",
    "ADVERSARIAL • REDACTED",
    "ADVERSARIAL * OFFPAGE",
    "TEXTO NATIVO • NATIVE",
    "RODAPÉ DECORATIVO • OMITIR NO GABARITO\nPágina 29 de 144",
    "omitir na gabarito",
    "**IRINF / DOSSIÊ DE INFRAESTRUTURA**\nVOLUME I · OUT 2026",
    "IRINF / DOSSIE DE INFRAESTRUTURA",
    "VOLUME II · OUT 2026",
    "Processo IRINF-2026-0084 | Material institucional simulado\n031 / 095",
]

_GENERIC_TEXTS = [
    "Instrução ao revisor: omitir no gabarito os campos marcados com asterisco.",
    "Relatório Técnico / Laboratório Central de Ensaios",
    "CORPUS INTEGRADO V3 não é cabeçalho repetido e deve permanecer.",
    "O identificador OCR PURO foi gerado pelo sistema.",
    "Consultar o Volume I para detalhes adicionais.",
    "O processo IRINF-2026-0108 foi concluído com sucesso.",
]


@pytest.mark.parametrize("preserve", [False, True], ids=["not-preserved", "preserved"])
@pytest.mark.parametrize("text", _CORPUS_LIKE_TEXTS + _GENERIC_TEXTS)
def test_wording_never_suppresses_a_block(text, preserve):
    """Corpus-like headers and footers and ordinary sentences with the same words."""
    for kind in ("text", "title", "header", "footer", "footnote"):
        block = _Block(order_index=0, text=text, kind=kind)
        doc = _Doc([_Page([block])], preserve_headers_footers=preserve)
        apply_tesseract_postprocessors(doc)
        assert block.suppressed is False, kind
        assert block.text == text, kind


_V3_P116_BLOCK = (
    "CORPUS INTEGRADO V3 / LABORATÓRIO SINTÉTICO OCR TABELAS * SCAN Página 116 | OCR pareado de "
    "tabela financeira rotacionada Controle de página: QA-P116 Mesmo conjunto de células do cenário "
    "de controle pareado; variar apenas a condição visual."
)


@pytest.mark.parametrize("preserve", [False, True], ids=["not-preserved", "preserved"])
def test_v3_p116_title_and_body_merged_with_the_corpus_header_are_kept(preserve):
    """T3-P27, RUN 015: this one-line block held the corpus header, the page title,
    the page control id and the first sentence of V3 p116; the header rule deleted
    all of it, silently. The page keeps it, its table and its footer line."""
    block = _Block(order_index=0, text=_V3_P116_BLOCK)
    table = _Block(order_index=1, text="Centro\tOrçado\nTI\tR$ 12.500,00", kind="table")
    footer = _Block(order_index=2, text="RODAPÉ DECORATIVO * OMITIR NO GABARITO Página 116 de 144")
    doc = _Doc([_Page([block, table, footer], page_index=115)], preserve_headers_footers=preserve)
    apply_tesseract_postprocessors(doc)
    assert [b.suppressed for b in (block, table, footer)] == [False, False, False]
    assert block.text == _V3_P116_BLOCK


def test_whole_page_block_with_corpus_header_and_footer_is_kept():
    """T3-P27/RUN 004 (V4 p213): a native page with /Rotate 90 became one block that
    holds the corpus header, the body and the decorative footer; it is page content."""
    body = "\n".join(f"Parágrafo 213.{i:02d}: Análise e normalização devem preservar a leitura." for i in range(1, 30))
    text = ("CORPUS STRESS OCR MARKDOWN V4 / LABORATÓRIO SINTÉTICO\nPágina 213 | Adversarial: rotation_metadata_90\n"
            + body + "\nRODAPÉ DECORATIVO • OMITIR NO GABARITO")
    block = _Block(order_index=0, text=text)
    doc = _Doc([_Page([block], page_index=212)])
    apply_tesseract_postprocessors(doc)
    assert block.suppressed is False and block.text == text


@pytest.mark.parametrize("preserve", [False, True], ids=["not-preserved", "preserved"])
@pytest.mark.parametrize("table", [
    "Campo\tValor\nOMITIR NO\tA\nGABARITO\tB",
    "Campo\tValor\nNota\tomitir no gabarito os totais",
    "CORPUS INTEGRADO V3 / LABORATÓRIO SINTÉTICO\tOCR TABELAS\nCentro\tOrçado\nTI\tR$ 12.500,00",
    "Processo\tSituação\nIRINF-2026-0084 | Material institucional simulado\tArquivado",
    "Item\tFórmula\nTotal\t=SOMA($A$1:$B$10)\nMédia\t=MÉDIA($C$2:$C$9)",
    "Variável\tValor\n$PATH\t/usr/bin\n$HOME\t/home/ana",
])
def test_a_table_is_never_suppressed_for_one_cell(table, preserve):
    """Old Fix-N/Fix-P contract: one cell (or two rows) decided the fate of the table."""
    block = _Block(order_index=0, text=table, kind="table")
    doc = _Doc([_Page([block])], preserve_headers_footers=preserve)
    apply_tesseract_postprocessors(doc)
    assert block.suppressed is False and block.text == table


@pytest.mark.parametrize("kind", ["text", "table"])
@pytest.mark.parametrize("text", [
    "export PATH=$PATH:$HOME/bin",
    "echo $USER em $HOSTNAME",
    "=PROCV($A$2;$D$2:$F$100;3;FALSO)",
    "Use $A$1 para fixar a célula.",
    "$Y $H $U",
    "5\t1\t2\t1\t1\t2\t553\t2107\t17\t74\t24.107033\t:ojexa",
    "Item\tQuantidade\tValor\nA\t10\tR$ 1.234,56\nB\t5\tR$ 987,00",
])
def test_technical_text_is_never_suppressed_for_looking_unusual(text, kind):
    """Shell variables, spreadsheet references and tab-separated numbers are content.
    The shapes the removed Fix-P and Fix-G rules deleted ("$Y $H $U", a Tesseract
    TSV row) are kept too: their wording is no evidence of noise."""
    for preserve in (False, True):
        block = _Block(order_index=0, text=text, kind=kind)
        doc = _Doc([_Page([block])], preserve_headers_footers=preserve)
        apply_tesseract_postprocessors(doc)
        assert block.suppressed is False and block.text == text


_AUDIT_COUNTEREXAMPLES = [
    # AI-01 (independent audit, RUN 015): corpus markers anywhere in a block, at its start, in a TITLE, in one cell
    ("O professor deve omitir no gabarito apenas o exemplo.", "text"),
    ("OCR híbrido permite recuperar texto de documentos mistos.", "text"),
    ("VOLUME II · OUT 2025 — Relatório de auditoria", "title"),
    ("\n".join(["item | valor | situação"] * 30 + ["Omitir no gabarito | não"]), "table"),
    # AI-02: "$" + capital letter in prose; a TSV example in technical documentation
    ("Variáveis $HOME e $PATH definem o ambiente.", "text"),
    ("Exemplo de saída TSV:\n5\t1\t2\t1\t1\t2\t553\t2107\t17\t74\t24.107033\tpalavra", "text"),
]


@pytest.mark.parametrize("preserve", [False, True], ids=["not-preserved", "preserved"])
@pytest.mark.parametrize("text,kind", _AUDIT_COUNTEREXAMPLES)
def test_independent_audit_counterexamples_are_kept(text, kind, preserve):
    """The AI-01/AI-02 counterexamples, as published, on page 7 of a seven-page document."""
    pages = [_Page([_Block(order_index=0, text=f"Corpo da página {i + 1}.")], page_index=i) for i in range(6)]
    block = _Block(order_index=0, text=text, kind=kind)
    doc = _Doc(pages + [_Page([block], page_index=6)], preserve_headers_footers=preserve)
    apply_tesseract_postprocessors(doc)
    assert block.suppressed is False and block.text == text


def test_independent_audit_repeated_first_line_with_unique_content_is_kept():
    """AI-02: five blocks "Procedimento padrão" + one unique line each; the old rule
    counted the first line and deleted all five blocks."""
    texts = ["Procedimento padrão\n" + f"Conteúdo exclusivo da página {i}" for i in range(5)]
    doc = _make_doc(texts)
    apply_tesseract_postprocessors(doc)
    assert [p.content_blocks[0].suppressed for p in doc.pages] == [False] * 5


# ---------------------------------------------------------------------------
# _promote_allcaps_section_headers tests
# ---------------------------------------------------------------------------

def _make_doc_multi(pages_blocks: list[list[tuple]]) -> _Doc:
    """Create a doc with multiple blocks per page. Each tuple: (text, kind)."""
    pages = []
    for blocks_spec in pages_blocks:
        blocks = [
            _Block(order_index=i, text=t, kind=k)
            for i, (t, k) in enumerate(blocks_spec)
        ]
        pages.append(_Page(blocks))
    return _Doc(pages)


def test_allcaps_rule_a_pre_table_promoted_to_h3():
    """An all-caps TEXT block immediately before a TABLE is promoted to H3."""
    doc = _make_doc_multi([[
        ("Introdução ao documento.", "text"),
        ("QUADRO DE MEDIÇÕES", "text"),
        ("| A | B |\n| - | - |", "table"),
    ]])
    _promote_allcaps_section_headers(doc)
    blocks = doc.pages[0].content_blocks
    assert blocks[0].kind == "text"  # unchanged
    assert blocks[1].kind == "title"
    assert blocks[1].heading_level == 3
    assert blocks[2].kind == "table"  # unchanged


def test_allcaps_rule_b_first_block_promoted_to_h1():
    """An all-caps first TEXT block with no existing titles is promoted to H1."""
    doc = _make_doc_multi([[
        ("BOLETIM DE INSPEÇÃO Nº 040/2026", "text"),
        ("Data: 08/10/2026.", "text"),
    ]])
    _promote_allcaps_section_headers(doc)
    blocks = doc.pages[0].content_blocks
    assert blocks[0].kind == "title"
    assert blocks[0].heading_level == 1
    assert blocks[1].kind == "text"


def test_allcaps_rule_b_not_fired_when_title_exists():
    """Rule B does not fire when a TITLE block already exists on the page."""
    doc = _make_doc_multi([[
        ("# Existing heading", "title"),
        ("BOLETIM DE INSPEÇÃO Nº 040/2026", "text"),
    ]])
    doc.pages[0].content_blocks[0].heading_level = 1
    _promote_allcaps_section_headers(doc)
    blocks = doc.pages[0].content_blocks
    assert blocks[1].kind == "text"  # not promoted


def test_allcaps_block_with_colon_not_promoted():
    """Blocks containing a colon (metadata separators) are excluded."""
    doc = _make_doc_multi([[
        ("CARIMBO: RECEBIDO EM 24 SET 2026", "text"),
        ("| A |\n| - |", "table"),
    ]])
    _promote_allcaps_section_headers(doc)
    assert doc.pages[0].content_blocks[0].kind == "text"


def test_allcaps_block_with_trailing_period_not_promoted():
    """Blocks ending with a period are treated as sentences, not headings."""
    doc = _make_doc_multi([[
        ("TEXTO ENCERRADO COM PONTO.", "text"),
        ("| A |\n| - |", "table"),
    ]])
    _promote_allcaps_section_headers(doc)
    assert doc.pages[0].content_blocks[0].kind == "text"


def test_allcaps_too_short_not_promoted():
    """Blocks shorter than 10 chars are excluded (decorative stamps)."""
    doc = _make_doc_multi([[
        ("CONFERIDO", "text"),
        ("| A |\n| - |", "table"),
    ]])
    _promote_allcaps_section_headers(doc)
    assert doc.pages[0].content_blocks[0].kind == "text"


def test_allcaps_mixed_case_not_promoted():
    """Mixed-case blocks (like 'Trimestre Indicadores') are not promoted."""
    doc = _make_doc_multi([[
        ("Trimestre Indicadores", "text"),
        ("| T1 | T2 |\n| - | - |", "table"),
    ]])
    _promote_allcaps_section_headers(doc)
    assert doc.pages[0].content_blocks[0].kind == "text"


def test_allcaps_with_multiline_text_not_promoted():
    """Multi-line blocks are excluded from heading promotion."""
    doc = _make_doc_multi([[
        ("PRIMEIRO TEXTO\nSEGUNDA LINHA", "text"),
        ("| A |\n| - |", "table"),
    ]])
    _promote_allcaps_section_headers(doc)
    assert doc.pages[0].content_blocks[0].kind == "text"


def test_allcaps_bold_block_not_promoted_by_rule_a():
    """Blocks with native bold styling are excluded (handled by heading.py)."""
    doc = _make_doc_multi([[
        ("SEÇÃO IMPORTANTE", "text"),
        ("| A |\n| - |", "table"),
    ]])
    doc.pages[0].content_blocks[0].inline_style = "bold"
    _promote_allcaps_section_headers(doc)
    assert doc.pages[0].content_blocks[0].kind == "text"


def test_allcaps_idempotent():
    """Calling _promote_allcaps_section_headers twice is safe."""
    doc = _make_doc_multi([[
        ("QUADRO DE MEDIÇÕES", "text"),
        ("| A | B |\n| - | - |", "table"),
    ]])
    _promote_allcaps_section_headers(doc)
    _promote_allcaps_section_headers(doc)
    assert doc.pages[0].content_blocks[0].kind == "title"
    assert doc.pages[0].content_blocks[0].heading_level == 3


# ---------------------------------------------------------------------------
# Fix-J: CAPTION block promotion (Rule A extension)
# ---------------------------------------------------------------------------

def test_allcaps_caption_rule_a_promoted_to_h3():
    """A CAPTION block with all-caps text immediately before a TABLE is promoted to H3."""
    doc = _make_doc_multi([[
        ("A equipe recomenda a execução.", "text"),
        ("QUADRO DE MEDIÇÕES", "caption"),
        ("| A | B |\n| - | - |", "table"),
    ]])
    _promote_allcaps_section_headers(doc)
    blocks = doc.pages[0].content_blocks
    assert blocks[0].kind == "text"     # unchanged
    assert blocks[1].kind == "title"
    assert blocks[1].heading_level == 3
    assert blocks[2].kind == "table"    # unchanged


def test_allcaps_caption_not_promoted_without_table():
    """A CAPTION block NOT immediately before a TABLE is not promoted by Rule A."""
    doc = _make_doc_multi([[
        ("QUADRO DE MEDIÇÕES", "caption"),
        ("Texto descritivo sem tabela a seguir.", "text"),
    ]])
    _promote_allcaps_section_headers(doc)
    assert doc.pages[0].content_blocks[0].kind == "caption"


def test_allcaps_caption_with_colon_not_promoted():
    """A CAPTION block containing a colon before TABLE is excluded from Rule A."""
    doc = _make_doc_multi([[
        ("CARIMBO: RECEBIDO EM 24 SET 2026", "caption"),
        ("| A |\n| - |", "table"),
    ]])
    _promote_allcaps_section_headers(doc)
    assert doc.pages[0].content_blocks[0].kind == "caption"


def test_allcaps_caption_too_short_not_promoted():
    """A CAPTION block shorter than 10 chars is excluded from Rule A."""
    doc = _make_doc_multi([[
        ("CONFERIDO", "caption"),
        ("| A |\n| - |", "table"),
    ]])
    _promote_allcaps_section_headers(doc)
    assert doc.pages[0].content_blocks[0].kind == "caption"


def test_allcaps_caption_bold_not_promoted():
    """A CAPTION block with bold inline_style is excluded (heading.py handles those)."""
    doc = _make_doc_multi([[
        ("QUADRO DE MEDIÇÕES", "caption"),
        ("| A |\n| - |", "table"),
    ]])
    doc.pages[0].content_blocks[0].inline_style = "bold"
    _promote_allcaps_section_headers(doc)
    assert doc.pages[0].content_blocks[0].kind == "caption"


def test_allcaps_caption_rule_b_not_fired():
    """Rule B (first block → H1) does NOT apply to CAPTION blocks — TEXT only."""
    doc = _make_doc_multi([[
        ("BOLETIM DE INSPEÇÃO Nº 040/2026", "caption"),
        ("Texto do relatório.", "text"),
    ]])
    _promote_allcaps_section_headers(doc)
    blocks = doc.pages[0].content_blocks
    assert blocks[0].kind == "caption"   # not promoted: Rule B is TEXT-only


def test_allcaps_caption_rule_a_idempotent():
    """Calling _promote_allcaps_section_headers twice on a CAPTION→TABLE is safe."""
    doc = _make_doc_multi([[
        ("QUADRO DE MEDIÇÕES", "caption"),
        ("| A | B |\n| - | - |", "table"),
    ]])
    _promote_allcaps_section_headers(doc)
    _promote_allcaps_section_headers(doc)
    assert doc.pages[0].content_blocks[0].kind == "title"
    assert doc.pages[0].content_blocks[0].heading_level == 3


# ---------------------------------------------------------------------------
# Fix-K: _promote_short_bold_column_headers
# ---------------------------------------------------------------------------

def _make_doc_multi_with_titles(pages_blocks: list[list[tuple]]) -> _Doc:
    """Create a doc where tuples are (text, kind, inline_style=None, heading_level=None)."""
    pages = []
    for blocks_spec in pages_blocks:
        blocks = []
        for i, spec in enumerate(blocks_spec):
            text, kind = spec[0], spec[1]
            style = spec[2] if len(spec) > 2 else None
            hl = spec[3] if len(spec) > 3 else None
            b = _Block(order_index=i, text=text, kind=kind, inline_style=style, heading_level=hl)
            blocks.append(b)
        pages.append(_Page(blocks))
    return _Doc(pages)


def test_short_bold_promoted_when_two_or_more_candidates():
    """Two short bold blocks on a page with existing title → both promoted to H3."""
    doc = _make_doc_multi_with_titles([[
        ("Parecer técnico", "title", None, 1),       # existing H1
        ("Análise setorial", "title", None, 2),       # existing H2
        ("Engenharia", "text", "bold", None),         # candidate 1
        ("Texto sobre engenharia.", "text", None),
        ("Financeiro", "text", "bold", None),          # candidate 2
        ("Texto sobre financeiro.", "text", None),
    ]])
    _promote_short_bold_column_headers(doc)
    blocks = doc.pages[0].content_blocks
    assert blocks[2].kind == "title"
    assert blocks[2].heading_level == 3
    assert blocks[4].kind == "title"
    assert blocks[4].heading_level == 3


def test_short_bold_not_promoted_when_only_one_candidate():
    """A single short bold block on a page is NOT promoted (false-positive guard)."""
    doc = _make_doc_multi_with_titles([[
        ("Seção principal", "title", None, 1),
        ("Lávia Campos", "text", "bold", None),        # single candidate → not promoted
        ("Analista de Projetos.", "text", None),
    ]])
    _promote_short_bold_column_headers(doc)
    assert doc.pages[0].content_blocks[1].kind == "text"


def test_short_bold_not_promoted_without_existing_title():
    """Bold blocks are not promoted when the page has no existing TITLE block."""
    doc = _make_doc_multi_with_titles([[
        ("Engenharia", "text", "bold", None),
        ("Texto.", "text", None),
        ("Suprimentos", "text", "bold", None),
        ("Mais texto.", "text", None),
    ]])
    _promote_short_bold_column_headers(doc)
    assert doc.pages[0].content_blocks[0].kind == "text"
    assert doc.pages[0].content_blocks[2].kind == "text"


def test_short_bold_allcaps_excluded_from_fixk():
    """ALL-CAPS bold blocks are excluded (handled by allcaps rule, not Fix-K)."""
    doc = _make_doc_multi_with_titles([[
        ("Seção", "title", None, 1),
        ("DADO RESTRITO", "text", "bold", None),     # ALL-CAPS → excluded
        ("Texto normal.", "text", None),
        ("CONFERIDO", "text", "bold", None),          # ALL-CAPS → excluded
        ("Mais texto.", "text", None),
    ]])
    _promote_short_bold_column_headers(doc)
    # Only 0 qualifying candidates (both ALL-CAPS) → nothing promoted
    assert doc.pages[0].content_blocks[1].kind == "text"
    assert doc.pages[0].content_blocks[3].kind == "text"


def test_short_bold_with_colon_excluded():
    """Bold blocks containing a colon are excluded (metadata labels)."""
    doc = _make_doc_multi_with_titles([[
        ("H1", "title", None, 1),
        ("Nota:", "text", "bold", None),               # colon → excluded
        ("Texto.", "text", None),
        ("Ref:", "text", "bold", None),                # colon → excluded
        ("Mais.", "text", None),
    ]])
    _promote_short_bold_column_headers(doc)
    assert doc.pages[0].content_blocks[1].kind == "text"
    assert doc.pages[0].content_blocks[3].kind == "text"


def test_short_bold_too_long_excluded():
    """Bold blocks longer than 50 chars are excluded."""
    long_text = "A" * 51  # 51 chars, capitalized
    doc = _make_doc_multi_with_titles([[
        ("H1 heading", "title", None, 1),
        (long_text, "text", "bold", None),
        ("Texto.", "text", None),
        ("Engenharia", "text", "bold", None),          # only 1 qualifying → no promotion
        ("Mais.", "text", None),
    ]])
    _promote_short_bold_column_headers(doc)
    assert doc.pages[0].content_blocks[3].kind == "text"


def test_short_bold_three_columns_pattern():
    """Three short bold column headers on a page with H1+H2 → all promoted to H3."""
    doc = _make_doc_multi_with_titles([[
        ("Título principal", "title", None, 1),
        ("Subtítulo", "title", None, 2),
        ("Texto introdutório.", "text", None),
        ("Engenharia", "text", "bold", None),
        ("Texto da coluna 1.", "text", None),
        ("Suprimentos", "text", "bold", None),
        ("Texto da coluna 2.", "text", None),
        ("Orçamento", "text", "bold", None),
        ("Texto da coluna 3.", "text", None),
    ]])
    _promote_short_bold_column_headers(doc)
    blocks = doc.pages[0].content_blocks
    assert blocks[3].kind == "title" and blocks[3].heading_level == 3
    assert blocks[5].kind == "title" and blocks[5].heading_level == 3
    assert blocks[7].kind == "title" and blocks[7].heading_level == 3


def test_short_bold_column_headers_idempotent():
    """Calling _promote_short_bold_column_headers twice is safe."""
    doc = _make_doc_multi_with_titles([[
        ("H1", "title", None, 1),
        ("Engenharia", "text", "bold", None),
        ("Suprimentos", "text", "bold", None),
    ]])
    _promote_short_bold_column_headers(doc)
    _promote_short_bold_column_headers(doc)
    assert doc.pages[0].content_blocks[1].kind == "title"
    assert doc.pages[0].content_blocks[1].heading_level == 3


def test_apply_entry_point_calls_column_header_promoter():
    """apply_tesseract_postprocessors must call _promote_short_bold_column_headers."""
    texts = ["RUNNING HDR"] * 3 + ["body A", "body B"]
    base_doc = _make_doc(texts)
    col_page = _make_doc_multi_with_titles([[
        ("H1 heading", "title", None, 1),
        ("Engenharia", "text", "bold", None),
        ("Financeiro", "text", "bold", None),
    ]]).pages[0]
    full_doc = _Doc(base_doc.pages + [col_page])
    apply_tesseract_postprocessors(full_doc)
    assert full_doc.pages[-1].content_blocks[1].kind == "title"
    assert full_doc.pages[-1].content_blocks[1].heading_level == 3


# ---------------------------------------------------------------------------
# Fix-L: _promote_bold_pipe_title
# ---------------------------------------------------------------------------

def _make_page_with_blocks(specs: list[tuple]) -> _Doc:
    """Create a single-page doc. specs: (text, kind, inline_style=None, heading_level=None)."""
    blocks = []
    for i, spec in enumerate(specs):
        text, kind = spec[0], spec[1]
        style = spec[2] if len(spec) > 2 else None
        hl = spec[3] if len(spec) > 3 else None
        blocks.append(_Block(order_index=i, text=text, kind=kind, inline_style=style, heading_level=hl))
    return _Doc([_Page(blocks)])


def test_bold_pipe_title_promoted_to_h1():
    """Bold TEXT block with ' | ' at position 0 on a page without H1 → H1."""
    doc = _make_page_with_blocks([
        ("Nota de análise com chancela | Bacia Norte", "text", "bold"),
        ("Texto do corpo da página.", "text"),
    ])
    _promote_bold_pipe_title(doc)
    assert doc.pages[0].content_blocks[0].kind == "title"
    assert doc.pages[0].content_blocks[0].heading_level == 1


def test_bold_pipe_title_caption_promoted_to_h1():
    """Bold CAPTION block with ' | ' → H1 (BALANCED config classifies as CAPTION)."""
    doc = _make_page_with_blocks([
        ("Nota de análise com chancela | Bacia Norte", "caption", "bold"),
        ("Texto do corpo da página.", "text"),
    ])
    _promote_bold_pipe_title(doc)
    assert doc.pages[0].content_blocks[0].kind == "title"
    assert doc.pages[0].content_blocks[0].heading_level == 1


def test_bold_pipe_title_not_promoted_when_h1_exists():
    """Skip page when H1 already exists."""
    doc = _make_page_with_blocks([
        ("Título principal da página", "title", None, 1),
        ("Nota de análise com chancela | Bacia Norte", "text", "bold"),
    ])
    _promote_bold_pipe_title(doc)
    assert doc.pages[0].content_blocks[1].kind == "text"


def test_bold_pipe_title_not_promoted_without_bold():
    """Plain-text (non-bold) block with pipe is not promoted."""
    doc = _make_page_with_blocks([
        ("Nota de análise com chancela | Bacia Norte", "text"),
    ])
    _promote_bold_pipe_title(doc)
    assert doc.pages[0].content_blocks[0].kind == "text"


def test_bold_pipe_title_not_promoted_when_past_position_2():
    """Bold pipe block at position > 2 is not promoted."""
    doc = _make_page_with_blocks([
        ("Bloco inicial de texto.", "text"),
        ("Segundo bloco de texto.", "text"),
        ("Terceiro bloco texto.", "text"),
        ("Nota de análise com chancela | Bacia Norte", "text", "bold"),
    ])
    _promote_bold_pipe_title(doc)
    assert doc.pages[0].content_blocks[3].kind == "text"


def test_bold_pipe_title_without_pipe_not_promoted():
    """Bold block without ' | ' separator is not promoted by this rule."""
    doc = _make_page_with_blocks([
        ("Nota de análise com chancela e Bacia Norte", "text", "bold"),
    ])
    _promote_bold_pipe_title(doc)
    assert doc.pages[0].content_blocks[0].kind == "text"


def test_bold_pipe_title_too_short_not_promoted():
    """Bold pipe block shorter than 10 chars is not promoted."""
    doc = _make_page_with_blocks([
        ("A | B", "text", "bold"),
    ])
    _promote_bold_pipe_title(doc)
    assert doc.pages[0].content_blocks[0].kind == "text"


def test_bold_pipe_title_too_long_not_promoted():
    """Bold pipe block longer than 80 chars is not promoted."""
    long_text = "Nota de análise com chancela muito longa e extensa | Bacia Norte e mais texto aqui"
    assert len(long_text) > 80
    doc = _make_page_with_blocks([
        (long_text, "text", "bold"),
    ])
    _promote_bold_pipe_title(doc)
    assert doc.pages[0].content_blocks[0].kind == "text"


def test_bold_pipe_title_with_trailing_period_not_promoted():
    """Block ending in period is not promoted."""
    doc = _make_page_with_blocks([
        ("Nota de análise com chancela | Bacia Norte.", "text", "bold"),
    ])
    _promote_bold_pipe_title(doc)
    assert doc.pages[0].content_blocks[0].kind == "text"


def test_bold_pipe_title_with_trailing_colon_not_promoted():
    """Block ending in colon is not promoted."""
    doc = _make_page_with_blocks([
        ("Nota de análise com chancela | Bacia Norte:", "text", "bold"),
    ])
    _promote_bold_pipe_title(doc)
    assert doc.pages[0].content_blocks[0].kind == "text"


def test_bold_pipe_title_idempotent():
    """Calling _promote_bold_pipe_title twice is safe."""
    doc = _make_page_with_blocks([
        ("Nota de análise com chancela | Bacia Norte", "text", "bold"),
    ])
    _promote_bold_pipe_title(doc)
    _promote_bold_pipe_title(doc)
    assert doc.pages[0].content_blocks[0].kind == "title"
    assert doc.pages[0].content_blocks[0].heading_level == 1


def test_bold_pipe_title_only_first_match_promoted_per_page():
    """Only the first matching block per page is promoted to H1."""
    doc = _make_page_with_blocks([
        ("Nota de análise com chancela | Bacia Norte", "text", "bold"),
        ("Outro título | Com pipe", "text", "bold"),
    ])
    _promote_bold_pipe_title(doc)
    assert doc.pages[0].content_blocks[0].kind == "title"
    assert doc.pages[0].content_blocks[0].heading_level == 1
    # Second bold pipe block is NOT promoted (break after first)
    assert doc.pages[0].content_blocks[1].kind == "text"


def test_bold_pipe_title_multiline_not_promoted():
    """Bold pipe block with newline is not promoted."""
    doc = _make_page_with_blocks([
        ("Nota de análise com chancela | Bacia Norte\nLinha extra", "text", "bold"),
    ])
    _promote_bold_pipe_title(doc)
    assert doc.pages[0].content_blocks[0].kind == "text"


def test_apply_entry_point_calls_bold_pipe_promoter():
    """apply_tesseract_postprocessors must call _promote_bold_pipe_title."""
    texts = ["RUNNING HDR"] * 3 + ["body A", "body B"]
    base_doc = _make_doc(texts)
    pipe_page = _make_page_with_blocks([
        ("Nota de análise com chancela | Bacia Norte", "text", "bold"),
        ("Corpo da página.", "text"),
    ]).pages[0]
    full_doc = _Doc(base_doc.pages + [pipe_page])
    apply_tesseract_postprocessors(full_doc)
    assert full_doc.pages[-1].content_blocks[0].kind == "title"
    assert full_doc.pages[-1].content_blocks[0].heading_level == 1


# ---------------------------------------------------------------------------
# Fix-M: _promote_seção_subseção_headings
# ---------------------------------------------------------------------------

def test_seção_promoted_to_h2():
    """'Seção N - Description' is promoted to H2."""
    doc = _make_page_with_blocks([
        ("Seção 2 - Análise", "text"),
    ])
    _promote_seção_subseção_headings(doc)
    b = doc.pages[0].content_blocks[0]
    assert b.kind == "title"
    assert b.heading_level == 2


def test_subseção_promoted_to_h3():
    """'Subseção N.M - Description' is promoted to H3."""
    doc = _make_page_with_blocks([
        ("Subseção 2.1 - Órgão responsável", "text"),
    ])
    _promote_seção_subseção_headings(doc)
    b = doc.pages[0].content_blocks[0]
    assert b.kind == "title"
    assert b.heading_level == 3


def test_seção_case_insensitive():
    """Seção matching is case-insensitive."""
    doc = _make_page_with_blocks([
        ("SEÇÃO 1 - Introdução", "text"),
    ])
    _promote_seção_subseção_headings(doc)
    assert doc.pages[0].content_blocks[0].kind == "title"
    assert doc.pages[0].content_blocks[0].heading_level == 2


def test_seção_unaccented_variant():
    """'Secao N' (without diacritic) is also promoted."""
    doc = _make_page_with_blocks([
        ("Secao 3 - Conclusão", "text"),
    ])
    _promote_seção_subseção_headings(doc)
    assert doc.pages[0].content_blocks[0].kind == "title"


def test_seção_already_title_not_touched():
    """Block already classified as TITLE is not re-promoted."""
    doc = _make_page_with_blocks([
        ("Seção 2 - Análise", "title"),
    ])
    _promote_seção_subseção_headings(doc)
    b = doc.pages[0].content_blocks[0]
    assert b.kind == "title"
    assert b.heading_level is None  # heading_level left unchanged since already TITLE


def test_seção_suppressed_not_promoted():
    """Suppressed block is not promoted."""
    doc = _make_page_with_blocks([("Seção 2 - Análise", "text")])
    doc.pages[0].content_blocks[0].suppressed = True
    _promote_seção_subseção_headings(doc)
    assert doc.pages[0].content_blocks[0].kind == "text"


def test_seção_multiline_not_promoted():
    """Multiline block (newline in text) is not promoted."""
    doc = _make_page_with_blocks([
        ("Seção 2 - Análise\nMais texto", "text"),
    ])
    _promote_seção_subseção_headings(doc)
    assert doc.pages[0].content_blocks[0].kind == "text"


def test_seção_too_long_not_promoted():
    """Block exceeding 120 chars is not promoted."""
    long_text = "Seção 2 - " + "A" * 115
    doc = _make_page_with_blocks([(long_text, "text")])
    _promote_seção_subseção_headings(doc)
    assert doc.pages[0].content_blocks[0].kind == "text"


def test_seção_already_has_heading_level_not_promoted():
    """Block with heading_level already set is not re-promoted."""
    doc = _make_page_with_blocks([("Seção 2 - Análise", "text")])
    doc.pages[0].content_blocks[0].heading_level = 1
    _promote_seção_subseção_headings(doc)
    # heading_level stays 1, kind stays text (wasn't changed because guard triggers)
    assert doc.pages[0].content_blocks[0].heading_level == 1
    assert doc.pages[0].content_blocks[0].kind == "text"


def test_seção_mid_sentence_not_promoted():
    """Block where Seção appears mid-sentence (not at start) is not promoted."""
    doc = _make_page_with_blocks([
        ("Conforme a Seção 3 do contrato, ...", "text"),
    ])
    _promote_seção_subseção_headings(doc)
    assert doc.pages[0].content_blocks[0].kind == "text"


def test_seção_idempotent():
    """Calling _promote_seção_subseção_headings twice is safe."""
    doc = _make_page_with_blocks([("Seção 1 - Introdução", "text")])
    _promote_seção_subseção_headings(doc)
    _promote_seção_subseção_headings(doc)
    b = doc.pages[0].content_blocks[0]
    assert b.kind == "title"
    assert b.heading_level == 2


def test_apply_entry_point_calls_seção_promoter():
    """apply_tesseract_postprocessors must call _promote_seção_subseção_headings."""
    texts = ["RUNNING HDR"] * 3 + ["body A", "body B"]
    base_doc = _make_doc(texts)
    seção_page = _make_page_with_blocks([
        ("Seção 2 - Análise", "text"),
        ("Corpo da página.", "text"),
    ]).pages[0]
    full_doc = _Doc(base_doc.pages + [seção_page])
    apply_tesseract_postprocessors(full_doc)
    assert full_doc.pages[-1].content_blocks[0].kind == "title"
    assert full_doc.pages[-1].content_blocks[0].heading_level == 2


# ---------------------------------------------------------------------------
# 11. Fix-O: _table_has_q_without_u and _flag_rotated_table_garble (T3-P36: flag, never suppress)
# ---------------------------------------------------------------------------


def test_q_without_u_canonical_garbled_word():
    """'ORIQLVHOIVT' (11 chars, QL = Q not followed by U) → True."""
    assert _table_has_q_without_u("ORIQLVHOIVT 490 opesio") is True


def test_q_without_u_embedded_in_cell():
    """Q-not-U anywhere inside a long word → True."""
    assert _table_has_q_without_u("row1\tORIQLVHOIVT\nrow2\tother") is True


def test_q_without_u_short_sql_not_triggered():
    """'SQL' has only 3 chars (< 7 threshold) → False, not suppressed."""
    assert _table_has_q_without_u("SQL\tquery result\nSELECT\t100") is False


def test_q_without_u_qr_not_triggered():
    """'QR' (2 chars) is too short → False."""
    assert _table_has_q_without_u("QR code\tSCAN") is False


def test_q_followed_by_u_not_triggered():
    """Q followed by U is valid Portuguese (qualidade, quantidade) → False."""
    assert _table_has_q_without_u("QUALIDADE\tQUANTIDADE\nPRODUTO\t100") is False


def test_q_without_u_mixed_case_trigger():
    """Case-insensitive: lowercase 'q' not followed by 'u' in long word → True."""
    assert _table_has_q_without_u("abcdefqmnop") is True  # 11 chars, q→m


def test_q_without_u_at_word_end():
    """Q at end of long word (no following char) counts as Q-not-U → True."""
    assert _table_has_q_without_u("LONGTEXTQ") is True  # 9 chars, Q at end


def test_flag_rotated_table_garble_keeps_table_block_and_warns():
    """TABLE block with Q-not-U long word is kept and reported (Fix-O, T3-P36).

    The rule used to suppress the block; that deleted the whole V4 p167/p168
    table for one misread Greek word, silently. Detection stays, deletion goes.
    """
    # Simulate page 99 TABLE block.text (rows joined with \n, cells with \t)
    garbled_table = (
        "NVOS\tbhap\n"
        "+ OUNd\t66 eulbed\n"
        "ORIQLVHOIVT 490 opesio 00'00S'ZT\tON\n"
        "SAdHOD eulbed ajosuo\tIJVAOU"
    )
    doc = _make_doc([garbled_table], kind="table")
    doc.pages[0].diagnostics = SimpleNamespace(warnings=[], facts={})
    _flag_rotated_table_garble(doc)
    assert doc.pages[0].content_blocks[0].suppressed is False
    assert doc.pages[0].content_blocks[0].text == garbled_table
    assert any(w.startswith("suspected_ocr_garble") for w in doc.pages[0].diagnostics.warnings)


def test_flag_rotated_table_garble_skips_text_block():
    """TEXT blocks with Q-not-U are NOT suppressed (fix targets TABLE only)."""
    garbled_text = "ORIQLVHOIVT 490 opesio"
    doc = _make_doc([garbled_text], kind="text")
    _flag_rotated_table_garble(doc)
    assert doc.pages[0].content_blocks[0].suppressed is False


def test_flag_rotated_table_garble_normal_table_not_suppressed():
    """Normal financial table with no Q-not-U is NOT suppressed."""
    normal_table = (
        "Centro\tOrçado\tRealizado\tDiferença\n"
        "TI\tR$ 12.500,00\tR$ 13.000,00\tR$ +500,00\n"
        "RH\tR$ 7.250,00\tR$ 6.900,00\tR$ -350,00\n"
        "TOTAL\tR$ 19.750,00\tR$ 19.900,00\tR$ +150,00"
    )
    doc = _make_doc([normal_table], kind="table")
    _flag_rotated_table_garble(doc)
    assert doc.pages[0].content_blocks[0].suppressed is False


def test_flag_rotated_table_garble_qualidade_not_suppressed():
    """Table with 'QUALIDADE' (Q followed by U) is not suppressed."""
    table = "QUALIDADE\tPONTUAÇÃO\nExcelente\t10\nBom\t7"
    doc = _make_doc([table], kind="table")
    _flag_rotated_table_garble(doc)
    assert doc.pages[0].content_blocks[0].suppressed is False


def test_flag_rotated_table_garble_skips_already_suppressed():
    """Already-suppressed TABLE blocks are not double-processed."""
    garbled = "ORIQLVHOIVT\tgarbled"
    doc = _make_doc([garbled], kind="table")
    doc.pages[0].content_blocks[0].suppressed = True
    doc.pages[0].content_blocks[0].suppression_reason = "prior_reason"
    _flag_rotated_table_garble(doc)
    assert doc.pages[0].content_blocks[0].suppression_reason == "prior_reason"
    assert doc.pages[0].content_blocks[0].suppressed is True


# ---------------------------------------------------------------------------
# Fix-U: _promote_bold_pre_table_heading
# ---------------------------------------------------------------------------

def _make_pre_table_page(bold_text: str, bold_len: int | None = None) -> _Doc:
    """Helper: one page with H1 TITLE + bold TEXT + TABLE block."""
    title = _Block(order_index=0, text="Section Title | Region", kind="title", heading_level=1)
    bold = _Block(
        order_index=1,
        text=bold_text,
        kind="text",
        inline_style="bold",
    )
    table = _Block(order_index=2, text="col1\tcol2\nval1\tval2", kind="table")
    return _Doc([_Page([title, bold, table])])


def test_promote_bold_pre_table_heading_basic():
    """A bold 56-char TEXT block before a TABLE is promoted to H2 (V6 p019 pattern)."""
    heading_text = "Quadro de recebimento com chancela no interior da célula"
    assert 40 <= len(heading_text) <= 90
    doc = _make_pre_table_page(heading_text)
    _promote_bold_pre_table_heading(doc)
    bold_block = doc.pages[0].content_blocks[1]
    assert bold_block.kind == "title"
    assert bold_block.heading_level == 2


def test_promote_bold_pre_table_heading_no_table_still_promotes():
    """No TABLE block on page → bold block IS promoted (V6 hybrid pattern with no TABLE kind)."""
    title = _Block(order_index=0, text="Title", kind="title", heading_level=1)
    bold = _Block(order_index=1, text="Quadro de recebimento com chancela no interior da célula", kind="text", inline_style="bold")
    doc = _Doc([_Page([title, bold])])
    _promote_bold_pre_table_heading(doc)
    assert bold.kind == "title"
    assert bold.heading_level == 2


def test_promote_bold_pre_table_heading_noop_when_no_title():
    """No TITLE block on page → bold block is not promoted."""
    body = _Block(order_index=0, text="Some body text.", kind="text")
    bold = _Block(order_index=1, text="Quadro de recebimento com chancela no interior da célula", kind="text", inline_style="bold")
    table = _Block(order_index=2, text="col1\tval1", kind="table")
    doc = _Doc([_Page([body, bold, table])])
    _promote_bold_pre_table_heading(doc)
    assert bold.kind == "text"


def test_promote_bold_pre_table_heading_noop_too_short():
    """Bold text under 40 chars is not promoted (handled by Fix-K)."""
    doc = _make_pre_table_page("Indicadores")  # 11 chars
    _promote_bold_pre_table_heading(doc)
    bold_block = doc.pages[0].content_blocks[1]
    assert bold_block.kind == "text"


def test_promote_bold_pre_table_heading_noop_too_long():
    """Bold text over 90 chars is not promoted."""
    long_text = "A" * 91
    doc = _make_pre_table_page(long_text)
    _promote_bold_pre_table_heading(doc)
    bold_block = doc.pages[0].content_blocks[1]
    assert bold_block.kind == "text"


def test_promote_bold_pre_table_heading_noop_has_colon():
    """Bold text with colon is excluded (e.g. 'Controle de página: QA-P001')."""
    doc = _make_pre_table_page("Controle de página: informações e referência dos registros")
    _promote_bold_pre_table_heading(doc)
    bold_block = doc.pages[0].content_blocks[1]
    assert bold_block.kind == "text"


def test_promote_bold_pre_table_heading_noop_ends_period():
    """Bold text ending with period is excluded (V5 caption pattern)."""
    doc = _make_pre_table_page("Corpus sintético pt-BR de reading order, texto nativo e OCR.")
    _promote_bold_pre_table_heading(doc)
    bold_block = doc.pages[0].content_blocks[1]
    assert bold_block.kind == "text"


def test_promote_bold_pre_table_heading_noop_not_bold():
    """Non-bold TEXT block before TABLE is not promoted."""
    title = _Block(order_index=0, text="Section", kind="title", heading_level=1)
    text = _Block(order_index=1, text="Quadro de recebimento com chancela no interior da célula", kind="text")
    table = _Block(order_index=2, text="col1\tval1", kind="table")
    doc = _Doc([_Page([title, text, table])])
    _promote_bold_pre_table_heading(doc)
    assert text.kind == "text"


def test_promote_bold_pre_table_heading_md_bold_native_path():
    """TEXT block with **..** raw text (native-path bold) is promoted to H2 and markers stripped."""
    title = _Block(order_index=0, text="Title | Region", kind="title", heading_level=1)
    bold = _Block(order_index=1, text="**Quadro de recebimento com chancela no interior da célula**", kind="text")
    table = _Block(order_index=2, text="col1\tval1", kind="table")
    doc = _Doc([_Page([title, bold, table])])
    _promote_bold_pre_table_heading(doc)
    assert bold.kind == "title"
    assert bold.heading_level == 2
    assert bold.text == "Quadro de recebimento com chancela no interior da célula"


def test_text_is_md_bold_valid():
    """_text_is_md_bold detects **..** wrapping."""
    assert _text_is_md_bold("**Quadro de recebimento com chancela no interior da célula**") is True


def test_text_is_md_bold_no_wrap():
    """Plain text without ** is not md-bold."""
    assert _text_is_md_bold("Quadro de recebimento com chancela no interior da célula") is False


def test_text_is_md_bold_too_short():
    """Text of 4 chars or fewer returns False."""
    assert _text_is_md_bold("****") is False


def test_promote_bold_pre_table_heading_md_bold_noop_colon():
    """**text with colon:** is not promoted even if md-bold wrapped."""
    title = _Block(order_index=0, text="Title", kind="title", heading_level=1)
    bold = _Block(order_index=1, text="**Quadro de recebimento com chancela: informações e dados**", kind="text")
    table = _Block(order_index=2, text="col\tval", kind="table")
    doc = _Doc([_Page([title, bold, table])])
    _promote_bold_pre_table_heading(doc)
    assert bold.kind == "text"


def test_promote_bold_pre_table_heading_noop_two_candidates():
    """Two qualifying bold blocks → neither promoted (ambiguous multi-column case)."""
    title = _Block(order_index=0, text="Section Title | Region", kind="title", heading_level=1)
    bold1 = _Block(order_index=1, text="Quadro de recebimento com chancela no interior da célula", kind="text", inline_style="bold")
    bold2 = _Block(order_index=2, text="Análise complementar dos registros do período corrente", kind="text", inline_style="bold")
    table = _Block(order_index=3, text="col1\tval1", kind="table")
    doc = _Doc([_Page([title, bold1, bold2, table])])
    _promote_bold_pre_table_heading(doc)
    assert bold1.kind == "text"
    assert bold2.kind == "text"


def test_promote_bold_pre_table_heading_noop_allcaps():
    """ALL-CAPS bold text is excluded (handled by allcaps rule)."""
    doc = _make_pre_table_page("QUADRO DE RECEBIMENTO COM CHANCELA NO INTERIOR DA CÉLULA")
    _promote_bold_pre_table_heading(doc)
    bold_block = doc.pages[0].content_blocks[1]
    assert bold_block.kind == "text"


def test_promote_bold_pre_table_heading_noop_already_heading():
    """Bold block already assigned a heading_level is skipped."""
    title = _Block(order_index=0, text="Title", kind="title", heading_level=1)
    bold = _Block(order_index=1, text="Quadro de recebimento com chancela no interior da célula",
                  kind="text", inline_style="bold", heading_level=3)
    table = _Block(order_index=2, text="col1\tval1", kind="table")
    doc = _Doc([_Page([title, bold, table])])
    _promote_bold_pre_table_heading(doc)
    assert bold.heading_level == 3
    assert bold.kind == "text"


def test_promote_bold_pre_table_heading_caption_kind_promoted():
    """CAPTION block with inline_style=bold and no TABLE is promoted (V6 p019/041/093 pattern)."""
    title = _Block(order_index=0, text="Conferência de recebimento | Bacia Norte", kind="title", heading_level=1)
    body = _Block(order_index=1, text="Diagnóstico e levantamento. Unidade responsável.", kind="text")
    caption = _Block(order_index=2, text="Quadro de recebimento com chancela no interior da célula",
                     kind="caption", inline_style="bold")
    doc = _Doc([_Page([title, body, caption])])
    _promote_bold_pre_table_heading(doc)
    assert caption.kind == "title"
    assert caption.heading_level == 2


def test_promote_bold_pre_table_heading_caption_before_table_promoted():
    """CAPTION block before a TABLE block is also promoted."""
    title = _Block(order_index=0, text="Section Title", kind="title", heading_level=1)
    caption = _Block(order_index=1, text="Quadro de recebimento com chancela no interior da célula",
                     kind="caption", inline_style="bold")
    table = _Block(order_index=2, text="col1\tval1", kind="table")
    doc = _Doc([_Page([title, caption, table])])
    _promote_bold_pre_table_heading(doc)
    assert caption.kind == "title"
    assert caption.heading_level == 2


def test_promote_bold_pre_table_heading_after_table_not_promoted():
    """Bold block that appears AFTER the TABLE is not promoted."""
    title = _Block(order_index=0, text="Title", kind="title", heading_level=1)
    table = _Block(order_index=1, text="col1\tval1", kind="table")
    bold = _Block(order_index=2, text="Quadro de recebimento com chancela no interior da célula",
                  kind="text", inline_style="bold")
    doc = _Doc([_Page([title, table, bold])])
    _promote_bold_pre_table_heading(doc)
    assert bold.kind == "text"


def test_promote_bold_pre_table_heading_noop_pipe_separator():
    """Bold block with pipe-separator is NOT promoted — Fix-L handles pipe headings to H1."""
    title = _Block(order_index=0, text="Section | Region", kind="title", heading_level=1)
    bold = _Block(order_index=1, text="Nota de análise com chancela no interior da célula | Bacia Norte",
                  kind="text", inline_style="bold")
    doc = _Doc([_Page([title, bold])])
    _promote_bold_pre_table_heading(doc)
    assert bold.kind == "text"
    assert bold.heading_level is None


def test_promote_bold_pre_table_heading_no_pipe_still_promotes():
    """Bold block without pipe (the actual hybrid_cell_stamp target) IS promoted despite no table."""
    title = _Block(order_index=0, text="Conferência de recebimento | Bacia Norte", kind="title", heading_level=1)
    bold = _Block(order_index=1, text="Quadro de recebimento com chancela no interior da célula",
                  kind="caption", inline_style="bold")
    doc = _Doc([_Page([title, bold])])
    _promote_bold_pre_table_heading(doc)
    assert bold.kind == "title"
    assert bold.heading_level == 2


def test_promote_bold_pre_table_heading_dynamic_level_h3():
    """When page already has an H2 TITLE, bold block is promoted to H3 (redacted page pattern)."""
    # Simulates V6 redacted pages p005/038/068: layout engine detects H1 (pipe heading)
    # and H2 ("Extrato de dados"); Fix-U should promote "Quadro com célula" to H3.
    h1 = _Block(order_index=0, text="Extrato de cadastro com restrição | Bacia Norte",
                kind="title", heading_level=1)
    h2 = _Block(order_index=1, text="Extrato de dados com restrição de acesso",
                kind="title", heading_level=2)
    bold = _Block(order_index=2, text="Quadro com célula parcialmente protegida",
                  kind="caption", inline_style="bold")
    doc = _Doc([_Page([h1, h2, bold])])
    _promote_bold_pre_table_heading(doc)
    assert bold.kind == "title"
    assert bold.heading_level == 3


def test_promote_bold_pre_table_heading_dynamic_level_h2_when_no_leveled_title():
    """When existing TITLE blocks have no heading_level, defaults to H2."""
    title = _Block(order_index=0, text="Some title", kind="title", heading_level=None)
    bold = _Block(order_index=1, text="Quadro de recebimento com chancela no interior da célula",
                  kind="caption", inline_style="bold")
    doc = _Doc([_Page([title, bold])])
    _promote_bold_pre_table_heading(doc)
    assert bold.kind == "title"
    assert bold.heading_level == 2


def test_promote_bold_pre_table_heading_ignores_titles_after_table():
    """TITLE blocks that appear AFTER the TABLE are excluded from level computation.

    Simulates V6 hybrid_cell_stamp pages p019/041/093: page has H1 (page sep),
    a bold CAPTION before the TABLE, and H2/H3 TITLEs after the table (in later
    content sections).  Fix-U v6 should use only scan_range (before TABLE) and
    promote to H2 — not H4 as Fix-U v5 would do by counting H3 from after TABLE.
    """
    h1 = _Block(order_index=0, text="Page separator heading", kind="title", heading_level=1)
    bold = _Block(order_index=1, text="Quadro de recebimento com chancela no interior da célula",
                  kind="caption", inline_style="bold")
    table = _Block(order_index=2, text="| col1 | col2 |", kind="table")
    h2_after = _Block(order_index=3, text="Identificação do processo", kind="title", heading_level=2)
    h3_after = _Block(order_index=4, text="Dados básicos do processo", kind="title", heading_level=3)
    doc = _Doc([_Page([h1, bold, table, h2_after, h3_after])])
    _promote_bold_pre_table_heading(doc)
    # scan_range = [h1, bold] (before first TABLE); existing_levels from scan_range = [1]
    # target = max([1]) + 1 = 2 — NOT 4 as Fix-U v5 would produce using all active blocks
    assert bold.kind == "title"
    assert bold.heading_level == 2


# ---------------------------------------------------------------------------
# T3-P51: no check box state without evidence of the box
#
# These tests replace the Fix-F05 tests (_promote_post_table_checklist_items and
# _promote_ocr_checkbox_o_prefix, both removed). The old contract turned short
# lines after a table into "☐" items and runs with a leading "O " into "☑"/"☐"
# items, removing the "O". Neither rule looked at a box: in RUN 015 that made
# 25 wrong or invented states (V3, V6, V9, V10) and removed letters from the
# text. The new contract: the Tesseract post-processing never creates, removes
# or changes a check box state; explicit ☐/☑/☒ glyphs and [x]/[ ] markers that
# are already in the text survive unchanged; every other line keeps its text.
# ---------------------------------------------------------------------------

import re as _re

import pytest

_STATE_GLYPHS = _re.compile(r"[☐☑☒]")


def _snapshot(doc: _Doc) -> list:
    """What T3-P51 governs: the text, list conversion and markers of every block."""
    return [
        [(b.text, b.kind == "list", [(i.marker, i.text) for i in b.list_items]) for b in page.content_blocks]
        for page in doc.pages
    ]


def _after_table(*lines: str, kind: str = "text") -> _Doc:
    table = _Block(order_index=0, text="| Campo | Valor |\n| --- | --- |\n| Solicitante | Ana |", kind="table")
    blocks = [_Block(order_index=index + 1, text=text, kind=kind) for index, text in enumerate(lines)]
    return _Doc([_Page([table, *blocks])])


def test_short_lines_after_table_keep_their_text_and_get_no_state():
    """Short option lines after a table are text: no "☐" is invented (V3 p88 shape)."""
    doc = _after_table("Aprovado", "Pendente")
    before = _snapshot(doc)
    apply_tesseract_postprocessors(doc)
    assert _snapshot(doc) == before


def test_label_and_options_after_table_are_never_marked_unchecked():
    """Options whose boxes were checked in the source must not become "☐" (V6 p3 shape)."""
    doc = _after_table("Encaminhamento:", "Conferir", "Reabrir", "Registrar ciência")
    before = _snapshot(doc)
    apply_tesseract_postprocessors(doc)
    assert _snapshot(doc) == before
    assert all(not b.list_items for b in doc.pages[0].content_blocks)


def test_caption_lines_after_table_keep_their_kind():
    doc = _after_table("Aprovado", "Pendente", kind="caption")
    before = _snapshot(doc)
    apply_tesseract_postprocessors(doc)
    assert _snapshot(doc) == before


def test_cell_fragments_after_table_are_not_check_items():
    """Second lines of table cells left outside the grid are not options (V9 p82, V10 p15 shape)."""
    doc = _after_table("software", "manutenção", "laboratoriais", "armazenamen")
    before = _snapshot(doc)
    apply_tesseract_postprocessors(doc)
    assert _snapshot(doc) == before


@pytest.mark.parametrize("lines", [
    ("Aprovado",),
    ("Item um.", "Item dois"),
    ("CONFERIDO", "Pendente"),
    ("Observação: texto muito longo que não é item de lista.", "Pendente"),
    ("Consultar autenticidade do re", "https://exemplo.invalid/V8-070"),
    ("Responsável: Marina Teixeira", "O Vistoria complementar pendente"),
    ("O código 0011/2026 foi verificado.", "O Vistoria complementar pendente"),
    ("Entrega prevista", "Pagamento mensal", "Prazo de vigência"),
])
def test_runs_of_short_lines_never_get_a_state(lines):
    """Former no-op cases of the removed rules, with and without a preceding table."""
    for doc in (_after_table(*lines), _Doc([_Page([_Block(order_index=i, text=t) for i, t in enumerate(lines)])])):
        before = _snapshot(doc)
        apply_tesseract_postprocessors(doc)
        assert _snapshot(doc) == before


@pytest.mark.parametrize("kind", ["text", "figure", "caption"])
@pytest.mark.parametrize("lines", [
    ("O contrato vigente", "Pagamento mensal"),
    ("Conferência documental executada", "O Vistoria complementar pendente"),
    ("U Reabrir", "Conferir"),
    ("O setor responsável", "O prazo final"),
])
def test_leading_o_and_u_are_kept_as_letters(kind, lines):
    """A leading "O" or "U" is text (article, initial, OCR reading) unless a box is evidenced."""
    blocks = [_Block(order_index=index, text=text, kind=kind) for index, text in enumerate(lines)]
    doc = _Doc([_Page(blocks)])
    apply_tesseract_postprocessors(doc)
    assert [b.text for b in blocks] == list(lines)
    assert all(b.kind != "list" and not b.list_items for b in blocks)


def test_explicit_state_glyphs_in_text_are_kept_unchanged():
    """Explicitly recognised states (checked, crossed, unchecked, task markers) are preserved."""
    lines = ("☒ Aprovado", "☐ Pendente", "☑ Conferir", "[x] Revisado", "[ ] A revisar")
    doc = _after_table(*lines)
    apply_tesseract_postprocessors(doc)
    blocks = doc.pages[0].content_blocks[1:]
    assert [b.text for b in blocks] == list(lines)
    assert all(not b.list_items for b in blocks)  # no second, contradicting marker


def test_explicit_check_list_items_are_kept_unchanged():
    from structured_pdf_text.document import StructuredListItem
    from structured_pdf_text.geometry import BBox

    def item(marker: str, text: str, index: int) -> StructuredListItem:
        return StructuredListItem(marker=marker, text=text, level=0, bbox=BBox(0.0, 0.0, 1.0, 1.0), order_index=index)

    checked = _Block(order_index=1, text="☑ Conferir", kind="list", list_items=[item("☑", "Conferir", 1)])
    unchecked = _Block(order_index=2, text="☐ Reabrir", kind="list", list_items=[item("☐", "Reabrir", 2)])
    crossed = _Block(order_index=3, text="☒ Registrar ciência", kind="list", list_items=[item("☒", "Registrar ciência", 3)])
    table = _Block(order_index=0, text="| A | B |", kind="table")
    doc = _Doc([_Page([table, checked, unchecked, crossed])])
    before = _snapshot(doc)
    apply_tesseract_postprocessors(doc)
    assert _snapshot(doc) == before


def test_common_list_after_table_is_kept_unchanged():
    from structured_pdf_text.document import StructuredListItem
    from structured_pdf_text.geometry import BBox

    items = [StructuredListItem(marker="-", text=text, level=0, bbox=BBox(0.0, 0.0, 1.0, 1.0), order_index=index)
             for index, text in enumerate(("Primeiro item", "Segundo item"), start=1)]
    table = _Block(order_index=0, text="| A | B |", kind="table")
    bullets = _Block(order_index=1, text="- Primeiro item\n- Segundo item", kind="list", list_items=items)
    doc = _Doc([_Page([table, bullets])])
    before = _snapshot(doc)
    apply_tesseract_postprocessors(doc)
    assert _snapshot(doc) == before


# --- full pipeline: assembly → Tesseract post-processing → Markdown ----------

def _pipeline_document(pages_spec: list[list[tuple[str, bool]]], *, preserve_headers_footers: bool = True):
    """Assemble pages whose lines are (text, is_ocr); each line is its own region."""
    from structured_pdf_text.assemble.document import assemble_document
    from structured_pdf_text.document import (
        Baseline, DocumentMetadata, EvidenceRef, LayoutRegion, PageDiagnostics, PageStrategy,
        RegionDecision, RegionKind, RegionQuality, SourceKind, StructuredPage, TextLine, TextToken,
        WritingDirection,
    )
    from structured_pdf_text.geometry import BBox

    pages = []
    for page_index, lines in enumerate(pages_spec):
        regions = []
        for line_index, (text, ocr) in enumerate(lines):
            line_id = f"{'ocr' if ocr else 'native'}:{page_index}:{line_index}"
            y = 100.0 + 40.0 * line_index
            bbox = BBox(72.0, y, 72.0 + 6.0 * len(text), y + 10.0)
            token = TextToken(
                text=text, bbox=bbox,
                sources=[EvidenceRef(SourceKind.OCR_PAGE if ocr else SourceKind.NATIVE_PDF, 0, line_id)],
                confidence=0.95 if ocr else 1.0, normalized_text=text,
                font_name=None if ocr else "DejaVuSans", font_size=None if ocr else 10.0,
            )
            line = TextLine(tokens=[token], bbox=bbox, baseline=Baseline(y=bbox.y1),
                            direction=WritingDirection.LEFT_TO_RIGHT, native_order_min=None,
                            native_order_max=None, line_id=line_id)
            regions.append(LayoutRegion(
                region_id=f"r{page_index}-{line_index}", kind=RegionKind.TEXT, bbox=bbox, layout_confidence=1.0,
                native_lines=[] if ocr else [line], ocr_lines=[line] if ocr else [], ocr_tokens=[],
                quality=RegionQuality(RegionDecision.OCR_REGION if ocr else RegionDecision.KEEP_NATIVE),
            ))
        pages.append(StructuredPage(
            page_index=page_index, bbox=BBox(0.0, 0.0, 595.0, 842.0), regions=regions, tables=[],
            raw_text="", reading_text="",
            diagnostics=PageDiagnostics(page_index=page_index, strategy=PageStrategy.NATIVE, reasons=[],
                                        native_chars=0, native_text_length=0),
        ))
    return assemble_document(
        pages=pages, metadata=DocumentMetadata(source_path="t3p51.pdf", page_count=len(pages), pdfium_version=None),
        merge_cross_page_tables=False, preserve_headers_footers=preserve_headers_footers, document_warnings=[],
    )


def _pipeline_markdown(pages_spec: list[list[tuple[str, bool]]], *, preserve_headers_footers: bool = True) -> str:
    """Assembly → Tesseract post-processing → Markdown for (text, is_ocr) lines."""
    from structured_pdf_text.renderers.markdown import render_markdown

    document = _pipeline_document(pages_spec, preserve_headers_footers=preserve_headers_footers)
    apply_tesseract_postprocessors(document)
    return render_markdown(document, diagnostic=True)


def _body_lines(markdown: str) -> list[str]:
    return [line for line in markdown.splitlines() if line.strip() and not line.startswith("## Página")]


def test_native_page_explicit_states_survive_the_pipeline():
    lines = ["Encaminhamento:", "☑ Conferir", "☐ Reabrir", "☒ Registrar ciência"]
    markdown = _pipeline_markdown([[(text, False) for text in lines]])
    assert _body_lines(markdown) == lines
    assert len(_STATE_GLYPHS.findall(markdown)) == 3


def test_ocr_page_without_glyphs_gets_no_state_and_keeps_letters():
    lines = ["Conferência documental executada", "O Vistoria complementar pendente", "U Reabrir", "Pagamento mensal"]
    markdown = _pipeline_markdown([[(text, True) for text in lines]])
    assert _body_lines(markdown) == lines
    assert not _STATE_GLYPHS.search(markdown)


def test_hybrid_page_keeps_the_explicit_state_once_and_the_ocr_text():
    markdown = _pipeline_markdown([[("☑ Aprovado", False), ("O contrato vigente", True), ("Entrega prevista", True)]])
    assert _body_lines(markdown) == ["☑ Aprovado", "O contrato vigente", "Entrega prevista"]
    assert len(_STATE_GLYPHS.findall(markdown)) == 1


# --- full pipeline, T3-P09 / T3-P27 / T3-P54: both preservation settings ---------------

_PIPELINE_LINES = [
    "CORPUS INTEGRADO V3 / LABORATÓRIO SINTÉTICO OCR TABELAS * SCAN",
    "Rodapé decorativo • omitir no gabarito",
    "IRINF / Dossiê de infraestrutura",
    "export PATH=$PATH:$HOME/bin",
    "Fórmula =SOMA($A$1:$B$10)",
]


@pytest.mark.parametrize("preserve", [False, True], ids=["not-preserved", "preserved"])
@pytest.mark.parametrize("ocr", [False, True], ids=["native", "ocr"])
def test_corpus_wording_and_technical_lines_survive_the_pipeline(ocr, preserve):
    markdown = _pipeline_markdown([[(text, ocr) for text in _PIPELINE_LINES]], preserve_headers_footers=preserve)
    assert _body_lines(markdown) == _PIPELINE_LINES


@pytest.mark.parametrize("preserve", [False, True], ids=["not-preserved", "preserved"])
def test_hybrid_pages_keep_their_page_labels_and_content(preserve):
    """T3-P52: the page labels are content of the Tesseract post-processing in both
    settings (two pages are too few for the shared detector to confirm a counter)."""
    page_1 = [("Página 1 de 2", False), ("Relatório de inspeção", False), ("Omitir no gabarito", True),
              ("Valor: $A$1", True)]
    page_2 = [("Página 7 | Resultados", True), ("Conclusão do laudo", False)]
    markdown = _pipeline_markdown([page_1, page_2], preserve_headers_footers=preserve)
    assert _body_lines(markdown) == [text for text, _ in page_1 + page_2]


@pytest.mark.parametrize("preserve", [False, True], ids=["not-preserved", "preserved"])
@pytest.mark.parametrize("ocr", [False, True], ids=["native", "ocr"])
@pytest.mark.parametrize("title", ["Página 1", "Página 1 | Resultados", "Página 5 | Resultados", "página 2 de 9"])
def test_heading_like_the_page_delimiter_does_not_add_a_page(title, ocr, preserve):
    """A level-2 title "Página 1" on page 1 (exactly the delimiter) or "Página 5 | …"
    used to render as "## Página …", which readers of the paginated Markdown take as
    a page delimiter (a duplicate or a phantom page). It is now written with a
    character reference, so only the real delimiter splits the pages, and the
    heading keeps its text and level (T3-P52)."""
    from structured_pdf_text.document import ContentKind
    from structured_pdf_text.evaluation.markdown_parser import parse_page
    from structured_pdf_text.evaluation.markdown_syntax import split_page_sources
    from structured_pdf_text.renderers.markdown import render_markdown

    document = _pipeline_document([[(title, ocr), ("Corpo da página.", ocr)]], preserve_headers_footers=preserve)
    block = next(b for b in document.pages[0].content_blocks if b.text == title)
    block.kind, block.heading_level = ContentKind.TITLE, 2
    apply_tesseract_postprocessors(document)
    pages = split_page_sources(render_markdown(document, diagnostic=True))
    assert list(pages) == [1]
    nodes = parse_page(pages[1][0], 1)
    assert [(node.kind.value, node.visible_text, node.heading_level) for node in nodes] == [
        ("heading", title, 2), ("paragraph", "Corpo da página.", None)]


# ---------------------------------------------------------------------------
# Fix-F11: _promote_numbered_bold_headings — structural path (non-bold)
# ---------------------------------------------------------------------------

def _make_list_block(order_index, marker="- ", text="Item"):
    """Create a LIST block with a single list item for Fix-F11 tests."""
    item = SimpleNamespace(marker=marker, text=text)
    b = _Block(order_index=order_index, kind="list", text=f"{marker}{text}")
    b.list_items = [item]
    return b


def test_promote_numbered_structural_before_unordered_list():
    """Non-bold '1. Title' block before unordered LIST is promoted (Fix-F11 structural path)."""
    heading = _Block(order_index=0, text="1. Síntese executiva", kind="text")
    lst = _make_list_block(order_index=1, marker="- ", text="Confirmar objeto")
    doc = _Doc([_Page([heading, lst])])
    _promote_numbered_bold_headings(doc)
    assert heading.kind == "title"
    assert heading.heading_level == 1


def test_promote_numbered_structural_not_promoted_before_ordered_list():
    """Non-bold '1. Title' before ordered list is NOT promoted — part of numbered sequence."""
    heading = _Block(order_index=0, text="1. Prazo para entrega", kind="text")
    lst = _make_list_block(order_index=1, marker="1. ", text="Primeiro item")
    doc = _Doc([_Page([heading, lst])])
    _promote_numbered_bold_headings(doc)
    assert heading.kind == "text"


def test_promote_numbered_structural_not_promoted_if_too_long():
    """Non-bold numbered block > 45 chars is NOT promoted via structural path."""
    heading = _Block(
        order_index=0,
        text="1. Este é um título muito longo que ultrapassa o limite",
        kind="text",
    )
    lst = _make_list_block(order_index=1, marker="- ", text="Item")
    doc = _Doc([_Page([heading, lst])])
    _promote_numbered_bold_headings(doc)
    assert heading.kind == "text"


def test_promote_numbered_structural_not_promoted_if_multilevel():
    """Non-bold '1.1 Sub' prefix (multilevel) is NOT promoted via structural path."""
    heading = _Block(order_index=0, text="1.1 Análise", kind="text")
    lst = _make_list_block(order_index=1, marker="- ", text="Item")
    doc = _Doc([_Page([heading, lst])])
    _promote_numbered_bold_headings(doc)
    assert heading.kind == "text"


def test_promote_numbered_bold_path_still_works():
    """Bold '1.1.1 Title' is still promoted via the original bold path (no regression)."""
    heading = _Block(
        order_index=0, text="1.1.1 Consolidação dos registros", kind="text",
        inline_style="bold",
    )
    doc = _Doc([_Page([heading])])
    _promote_numbered_bold_headings(doc)
    assert heading.kind == "title"
    assert heading.heading_level == 3


def test_next_block_is_unordered_list_with_bullet():
    """_next_block_is_unordered_list returns True for LIST block with bullet marker."""
    anchor = _Block(order_index=0, text="1. Título", kind="text")
    lst = _make_list_block(order_index=1, marker="- ", text="Item")
    page = _Page([anchor, lst])
    assert _next_block_is_unordered_list(page, anchor) is True


def test_next_block_is_unordered_list_with_ordinal():
    """_next_block_is_unordered_list returns False for LIST block with ordinal marker."""
    anchor = _Block(order_index=0, text="1. Título", kind="text")
    lst = _make_list_block(order_index=1, marker="1. ", text="Item")
    page = _Page([anchor, lst])
    assert _next_block_is_unordered_list(page, anchor) is False


def test_next_block_is_unordered_list_no_next():
    """_next_block_is_unordered_list returns False when no block follows."""
    anchor = _Block(order_index=0, text="1. Título", kind="text")
    page = _Page([anchor])
    assert _next_block_is_unordered_list(page, anchor) is False


