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


class _Doc:
    def __init__(self, pages: list[_Page]):
        self.pages = pages


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_doc(page_texts: list[str], kind: str = "text") -> _Doc:
    """One block per page, with the given text and optional kind."""
    return _Doc([_Page([_Block(order_index=0, text=t, kind=kind)]) for t in page_texts])


# ---------------------------------------------------------------------------
# Import the function under test
# ---------------------------------------------------------------------------

from structured_pdf_text.ocr.tesseract.postprocess import (
    _block_is_bold,
    _next_block_is_unordered_list,
    _normalize_brl_currency_spacing,
    _omitir_gabarito_split,
    _promote_allcaps_section_headers,
    _promote_bold_pipe_title,
    _promote_bold_pre_table_heading,
    _promote_numbered_bold_headings,
    _promote_ocr_checkbox_o_prefix,
    _promote_post_table_checklist_items,
    _promote_seção_subseção_headings,
    _promote_short_bold_column_headers,
    _suppress_dollar_letter_garble,
    _suppress_ocr_corpus_markers,
    _suppress_pagina_separator_blocks,
    _suppress_rotated_table_garble,
    _suppress_tesseract_coordinate_dump,
    _suppress_v6_irinf_artifacts,
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
# 7. _suppress_pagina_separator_blocks
# ---------------------------------------------------------------------------

def test_pagina_title_block_suppressed():
    """TITLE block 'Página 005 | Title' (exact V3 pattern) is suppressed."""
    b = _Block(order_index=0, text="Página 005 | Português com acentos", kind="title")
    page = _Page([b])
    doc = _Doc([page])
    _suppress_pagina_separator_blocks(doc)
    assert b.suppressed is True
    assert b.suppression_reason == "tesseract_pagina_separator"


def test_pagina_text_block_with_hash_prefix_suppressed():
    """TEXT block '# Página 005 | Title' (OCR-read hash prefix) is suppressed."""
    b = _Block(order_index=0, text="# Página 005 | Title", kind="text")
    page = _Page([b])
    doc = _Doc([page])
    _suppress_pagina_separator_blocks(doc)
    assert b.suppressed is True


def test_pagina_bare_number_suppressed():
    """'Página 001' without pipe title is also suppressed."""
    b = _Block(order_index=0, text="Página 001", kind="title")
    page = _Page([b])
    doc = _Doc([page])
    _suppress_pagina_separator_blocks(doc)
    assert b.suppressed is True


def test_pagina_case_insensitive():
    """'pagina 003' (lowercase, no accent) is suppressed."""
    b = _Block(order_index=0, text="pagina 003 | content", kind="title")
    page = _Page([b])
    doc = _Doc([page])
    _suppress_pagina_separator_blocks(doc)
    assert b.suppressed is True


def test_body_text_with_pagina_word_not_suppressed():
    """Body text that merely CONTAINS 'Página' mid-sentence is NOT suppressed."""
    b = _Block(order_index=0, text="Consultar a Página 42 do manual.", kind="text")
    page = _Page([b])
    doc = _Doc([page])
    _suppress_pagina_separator_blocks(doc)
    assert b.suppressed is False


def test_pagina_suppressor_skips_already_suppressed():
    """Already-suppressed blocks are not re-processed."""
    b = _Block(order_index=0, text="Página 001", kind="title",
               suppressed=True, suppression_reason="prior_reason")
    page = _Page([b])
    doc = _Doc([page])
    _suppress_pagina_separator_blocks(doc)
    assert b.suppression_reason == "prior_reason"  # not overwritten


def test_pagina_suppressor_is_idempotent():
    """Calling _suppress_pagina_separator_blocks twice gives the same result."""
    b = _Block(order_index=0, text="Página 005 | Title", kind="title")
    page = _Page([b])
    doc = _Doc([page])
    _suppress_pagina_separator_blocks(doc)
    _suppress_pagina_separator_blocks(doc)
    assert b.suppressed is True
    assert b.suppression_reason == "tesseract_pagina_separator"


def test_apply_entry_point_calls_pagina_suppressor():
    """apply_tesseract_postprocessors must also run the Página suppressor."""
    texts = ["RUNNING HDR"] * 3 + ["body A", "body B"]
    base_doc = _make_doc(texts)
    pagina_page = _Page([_Block(order_index=0, text="Página 001 | Intro", kind="title")])
    full_doc = _Doc(base_doc.pages + [pagina_page])
    apply_tesseract_postprocessors(full_doc)
    assert full_doc.pages[-1].content_blocks[0].suppressed is True


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
# 9. _suppress_ocr_corpus_markers
# ---------------------------------------------------------------------------

def test_ocr_puro_header_suppressed():
    """Block starting with 'OCR PURO' is suppressed as corpus running header."""
    b = _Block(order_index=0, text="OCR PURO * SCAN")
    page = _Page([b])
    doc = _Doc([page])
    _suppress_ocr_corpus_markers(doc)
    assert b.suppressed is True
    assert b.suppression_reason == "tesseract_ocr_corpus_marker"


def test_ocr_tabelas_header_suppressed():
    """Block starting with 'OCR TABELAS' is suppressed."""
    b = _Block(order_index=0, text="OCR TABELAS * SCAN")
    page = _Page([b])
    doc = _Doc([page])
    _suppress_ocr_corpus_markers(doc)
    assert b.suppressed is True


def test_ocr_hibrido_header_suppressed():
    """Block starting with 'OCR HÍBRIDO' is suppressed."""
    b = _Block(order_index=0, text="OCR HÍBRIDO * SCAN")
    page = _Page([b])
    doc = _Doc([page])
    _suppress_ocr_corpus_markers(doc)
    assert b.suppressed is True


def test_ocr_header_case_insensitive():
    """OCR corpus header matching is case-insensitive."""
    b = _Block(order_index=0, text="ocr puro * scan")
    page = _Page([b])
    doc = _Doc([page])
    _suppress_ocr_corpus_markers(doc)
    assert b.suppressed is True


def test_omitir_gabarito_suppressed():
    """Block containing 'OMITIR NO GABARITO' is suppressed."""
    b = _Block(order_index=0, text="RODAPÉ DECORATIVO – OMITIR NO GABARITO\nPágina 29 de 144")
    page = _Page([b])
    doc = _Doc([page])
    _suppress_ocr_corpus_markers(doc)
    assert b.suppressed is True
    assert b.suppression_reason == "tesseract_ocr_corpus_marker"


def test_omitir_na_gabarito_suppressed():
    """'omitir na gabarito' variant (na instead of no) is also suppressed."""
    b = _Block(order_index=0, text="omitir na gabarito")
    page = _Page([b])
    doc = _Doc([page])
    _suppress_ocr_corpus_markers(doc)
    assert b.suppressed is True


def test_body_text_not_suppressed_by_corpus_marker():
    """Normal body text is not affected by the OCR corpus marker suppressor."""
    b = _Block(order_index=0, text="O resultado foi aprovado. Página 3 de 10.")
    page = _Page([b])
    doc = _Doc([page])
    _suppress_ocr_corpus_markers(doc)
    assert b.suppressed is False


def test_ocr_prefix_mid_sentence_not_suppressed():
    """'OCR' appearing mid-sentence does NOT trigger the header suppressor."""
    b = _Block(order_index=0, text="O identificador OCR PURO foi gerado pelo sistema.")
    page = _Page([b])
    doc = _Doc([page])
    _suppress_ocr_corpus_markers(doc)
    assert b.suppressed is False


def test_corpus_marker_suppressor_skips_already_suppressed():
    """Already-suppressed blocks are not re-processed."""
    b = _Block(order_index=0, text="OCR PURO * SCAN",
               suppressed=True, suppression_reason="prior_reason")
    page = _Page([b])
    doc = _Doc([page])
    _suppress_ocr_corpus_markers(doc)
    assert b.suppression_reason == "prior_reason"  # not overwritten


def test_corpus_marker_suppressor_is_idempotent():
    """Calling twice gives the same result."""
    b = _Block(order_index=0, text="OCR TABELAS * SCAN")
    page = _Page([b])
    doc = _Doc([page])
    _suppress_ocr_corpus_markers(doc)
    _suppress_ocr_corpus_markers(doc)
    assert b.suppressed is True
    assert b.suppression_reason == "tesseract_ocr_corpus_marker"


def test_apply_entry_point_calls_corpus_marker_suppressor():
    """apply_tesseract_postprocessors must also run the OCR corpus marker suppressor."""
    texts = ["RUNNING HDR"] * 3 + ["body A", "body B"]
    base_doc = _make_doc(texts)
    marker_page = _Page([_Block(order_index=0, text="OCR PURO * SCAN")])
    full_doc = _Doc(base_doc.pages + [marker_page])
    apply_tesseract_postprocessors(full_doc)
    assert full_doc.pages[-1].content_blocks[0].suppressed is True


# ---------------------------------------------------------------------------
# 10. Corpus document-header and family-suffix suppression (Fix D1)
# ---------------------------------------------------------------------------

def test_corpus_doc_header_suppressed():
    """Block starting with 'CORPUS INTEGRADO V3 / LABORAT...' is suppressed."""
    doc = _make_doc(["CORPUS INTEGRADO V3 / LABORATÓRIO SINTÉTICO"])
    _suppress_ocr_corpus_markers(doc)
    assert doc.pages[0].content_blocks[0].suppressed is True
    assert doc.pages[0].content_blocks[0].suppression_reason == "tesseract_ocr_corpus_marker"


def test_corpus_doc_header_with_adversarial_suffix_suppressed():
    """Compound header ending with 'ADVERSARIAL * SCAN DUPLICATE' is suppressed."""
    text = "CORPUS INTEGRADO V3 / LABORATÓRIO SINTÉTICO ADVERSARIAL * SCAN DUPLICATE"
    doc = _make_doc([text])
    _suppress_ocr_corpus_markers(doc)
    assert doc.pages[0].content_blocks[0].suppressed is True


def test_corpus_doc_header_v4_suppressed():
    """Document header for V4 corpus is also suppressed."""
    doc = _make_doc(["CORPUS INTEGRADO V4 / LABORATÓRIO SINTÉTICO"])
    _suppress_ocr_corpus_markers(doc)
    assert doc.pages[0].content_blocks[0].suppressed is True


def test_corpus_body_mention_not_suppressed():
    """Adversarial body paragraph 'CORPUS INTEGRADO V3…' without '/' is NOT suppressed."""
    text = "CORPUS INTEGRADO V3 não é cabeçalho repetido e deve permanecer."
    doc = _make_doc([text])
    _suppress_ocr_corpus_markers(doc)
    assert doc.pages[0].content_blocks[0].suppressed is False


def test_adversarial_bullet_redacted_suppressed():
    """'ADVERSARIAL • REDACTED' standalone marker is suppressed."""
    doc = _make_doc(["ADVERSARIAL • REDACTED"])
    _suppress_ocr_corpus_markers(doc)
    assert doc.pages[0].content_blocks[0].suppressed is True


def test_adversarial_bullet_watermark_suppressed():
    """'ADVERSARIAL • WATERMARK' standalone marker is suppressed."""
    doc = _make_doc(["ADVERSARIAL • WATERMARK"])
    _suppress_ocr_corpus_markers(doc)
    assert doc.pages[0].content_blocks[0].suppressed is True


def test_adversarial_bullet_scrambled_suppressed():
    """'ADVERSARIAL • SCRAMBLED' standalone marker is suppressed."""
    doc = _make_doc(["ADVERSARIAL • SCRAMBLED"])
    _suppress_ocr_corpus_markers(doc)
    assert doc.pages[0].content_blocks[0].suppressed is True


def test_adversarial_asterisk_variant_suppressed():
    """'ADVERSARIAL * OFFPAGE' (asterisk separator) is suppressed."""
    doc = _make_doc(["ADVERSARIAL * OFFPAGE"])
    _suppress_ocr_corpus_markers(doc)
    assert doc.pages[0].content_blocks[0].suppressed is True


def test_texto_nativo_native_suppressed():
    """'TEXTO NATIVO • NATIVE' family-suffix marker is suppressed."""
    doc = _make_doc(["TEXTO NATIVO • NATIVE"])
    _suppress_ocr_corpus_markers(doc)
    assert doc.pages[0].content_blocks[0].suppressed is True


def test_adversarial_mid_sentence_not_suppressed():
    """Body sentence containing 'adversarial' mid-sentence is NOT suppressed."""
    text = "Este parágrafo contém adversarial como parte do texto."
    doc = _make_doc([text])
    _suppress_ocr_corpus_markers(doc)
    assert doc.pages[0].content_blocks[0].suppressed is False


def test_corpus_multiline_block_suppressed():
    """Two-line block 'CORPUS INTEGRADO V3 / LABORAT...\nTEXTO NATIVO • NATIVE' is suppressed."""
    text = "CORPUS INTEGRADO V3 / LABORATÓRIO SINTÉTICO\nTEXTO NATIVO • NATIVE"
    doc = _make_doc([text])
    _suppress_ocr_corpus_markers(doc)
    assert doc.pages[0].content_blocks[0].suppressed is True


# ---------------------------------------------------------------------------
# 10. V6 IRINF artifact suppression
# ---------------------------------------------------------------------------

def test_irinf_dossi_header_suppressed():
    """**IRINF / DOSSIÊ DE INFRAESTRUTURA** running header is suppressed."""
    text = "**IRINF / DOSSIÊ DE INFRAESTRUTURA** \nVOLUME I — OUT 2026"
    doc = _make_doc([text])
    _suppress_v6_irinf_artifacts(doc)
    assert doc.pages[0].content_blocks[0].suppressed is True
    assert doc.pages[0].content_blocks[0].suppression_reason == "tesseract_v6_irinf_artifact"


def test_irinf_dossi_header_volume_ii_suppressed():
    """IRINF header with VOLUME II variant is also suppressed."""
    text = "**IRINF / DOSSIÊ DE INFRAESTRUTURA** \nVOLUME II — OUT 2026"
    doc = _make_doc([text])
    _suppress_v6_irinf_artifacts(doc)
    assert doc.pages[0].content_blocks[0].suppressed is True


def test_irinf_header_case_insensitive():
    """IRINF suppressor is case-insensitive on the DOSSIÊ variant."""
    text = "**IRINF / DOSSIE DE INFRAESTRUTURA**\nVOLUME III — OUT 2026"
    doc = _make_doc([text])
    _suppress_v6_irinf_artifacts(doc)
    assert doc.pages[0].content_blocks[0].suppressed is True


def test_processo_irinf_material_footer_suppressed():
    """Processo IRINF footer with Material institucional simulado is suppressed."""
    text = "Processo IRINF-2026-0084 | Material institucional simulado\n031 / 095"
    doc = _make_doc([text])
    _suppress_v6_irinf_artifacts(doc)
    assert doc.pages[0].content_blocks[0].suppressed is True
    assert doc.pages[0].content_blocks[0].suppression_reason == "tesseract_v6_irinf_artifact"


def test_processo_irinf_different_number_suppressed():
    """Processo IRINF footer with any 4-digit number is suppressed."""
    text = "Processo IRINF-2026-0314 | Material institucional simulado"
    doc = _make_doc([text])
    _suppress_v6_irinf_artifacts(doc)
    assert doc.pages[0].content_blocks[0].suppressed is True


def test_irinf_artifact_skips_already_suppressed():
    """Already-suppressed blocks are not touched by IRINF suppressor."""
    text = "**IRINF / DOSSIÊ DE INFRAESTRUTURA**"
    doc = _make_doc([text])
    doc.pages[0].content_blocks[0].suppressed = True
    doc.pages[0].content_blocks[0].suppression_reason = "prior_suppressor"
    _suppress_v6_irinf_artifacts(doc)
    assert doc.pages[0].content_blocks[0].suppression_reason == "prior_suppressor"


def test_irinf_artifact_suppressor_is_idempotent():
    """Calling the IRINF suppressor twice does not change the result."""
    text = "**IRINF / DOSSIÊ DE INFRAESTRUTURA**\nVOLUME I — OUT 2026"
    doc = _make_doc([text])
    _suppress_v6_irinf_artifacts(doc)
    _suppress_v6_irinf_artifacts(doc)
    assert doc.pages[0].content_blocks[0].suppressed is True


def test_regular_bold_text_not_suppressed_by_irinf():
    """A regular bold heading is not suppressed by the IRINF suppressor."""
    text = "**Quadro de coordenadas, quantitativos e cotas**"
    doc = _make_doc([text])
    _suppress_v6_irinf_artifacts(doc)
    assert doc.pages[0].content_blocks[0].suppressed is False


def test_processo_body_mention_not_suppressed():
    """A body paragraph mentioning 'Processo' is not suppressed."""
    text = "O processo IRINF-2026-0108 foi concluído com sucesso."
    doc = _make_doc([text])
    _suppress_v6_irinf_artifacts(doc)
    assert doc.pages[0].content_blocks[0].suppressed is False


def test_apply_entry_point_calls_irinf_suppressor():
    """apply_tesseract_postprocessors suppresses IRINF header via entry point."""
    texts = ["**IRINF / DOSSIÊ DE INFRAESTRUTURA**\nVOLUME I — OUT 2026"] + [
        "Content page " + str(i) for i in range(4)
    ]
    doc = _make_doc(texts)
    apply_tesseract_postprocessors(doc)
    assert doc.pages[0].content_blocks[0].suppressed is True
    assert doc.pages[0].content_blocks[0].suppression_reason == "tesseract_v6_irinf_artifact"


def test_irinf_without_bold_prefix_suppressed():
    """IRINF header without ** bold prefix is also suppressed (Fix F: rotation pages)."""
    text = "IRINF / DOSSIÊ DE INFRAESTRUTURA"
    doc = _make_doc([text])
    _suppress_v6_irinf_artifacts(doc)
    assert doc.pages[0].content_blocks[0].suppressed is True
    assert doc.pages[0].content_blocks[0].suppression_reason == "tesseract_v6_irinf_artifact"


def test_irinf_without_bold_prefix_dossie_variant():
    """IRINF/DOSSIE (no accent) without ** prefix is suppressed."""
    text = "IRINF / DOSSIE DE INFRAESTRUTURA"
    doc = _make_doc([text])
    _suppress_v6_irinf_artifacts(doc)
    assert doc.pages[0].content_blocks[0].suppressed is True


def test_volume_subtitle_i_suppressed():
    """VOLUME I · OUT YYYY standalone block is suppressed (Fix F: VOLUME regex)."""
    text = "VOLUME I · OUT 2026"
    doc = _make_doc([text])
    _suppress_v6_irinf_artifacts(doc)
    assert doc.pages[0].content_blocks[0].suppressed is True
    assert doc.pages[0].content_blocks[0].suppression_reason == "tesseract_v6_irinf_artifact"


def test_volume_subtitle_ii_suppressed():
    """VOLUME II · OUT YYYY is suppressed."""
    text = "VOLUME II · OUT 2026"
    doc = _make_doc([text])
    _suppress_v6_irinf_artifacts(doc)
    assert doc.pages[0].content_blocks[0].suppressed is True


def test_volume_subtitle_iii_suppressed():
    """VOLUME III · OUT YYYY is suppressed."""
    text = "VOLUME III · OUT 2026"
    doc = _make_doc([text])
    _suppress_v6_irinf_artifacts(doc)
    assert doc.pages[0].content_blocks[0].suppressed is True


def test_volume_bullet_variant_suppressed():
    """VOLUME I • OUT YYYY (bullet separator) is suppressed."""
    text = "VOLUME I • OUT 2026"
    doc = _make_doc([text])
    _suppress_v6_irinf_artifacts(doc)
    assert doc.pages[0].content_blocks[0].suppressed is True


def test_regular_volume_body_text_not_suppressed():
    """A body sentence mentioning 'Volume I' is not suppressed."""
    text = "Consultar o Volume I para detalhes adicionais."
    doc = _make_doc([text])
    _suppress_v6_irinf_artifacts(doc)
    assert doc.pages[0].content_blocks[0].suppressed is False


# ---------------------------------------------------------------------------
# 11. Tesseract coordinate dump suppression (Fix G)
# ---------------------------------------------------------------------------

_COORD_LINE = "5\t1\t2\t1\t1\t2\t553\t2107\t17\t74\t24.107033\t:ojexa"
_COORD_BLOCK = "\n".join([
    "5\t1\t2\t1\t1\t2\t553\t2107\t17\t74\t24.107033\t:ojexa",
    "5\t1\t2\t1\t1\t3\t551\t2190\t19\t152\t12.635422\tJopeoyyuap|",
    "4\t1\t2\t1\t2\t0\t478\t1631\t24\t713\t-1\t",
])


def test_coordinate_dump_block_suppressed():
    """A block containing Tesseract coordinate/confidence data is suppressed."""
    doc = _make_doc([_COORD_BLOCK])
    _suppress_tesseract_coordinate_dump(doc)
    assert doc.pages[0].content_blocks[0].suppressed is True
    assert doc.pages[0].content_blocks[0].suppression_reason == "tesseract_coordinate_dump"


def test_coordinate_dump_single_line_suppressed():
    """A single coordinate line in a block triggers suppression."""
    doc = _make_doc([_COORD_LINE])
    _suppress_tesseract_coordinate_dump(doc)
    assert doc.pages[0].content_blocks[0].suppressed is True


def test_coordinate_dump_mixed_block_suppressed():
    """A block mixing garbled text and coordinate lines is suppressed."""
    text = "PPT ap\n96 euibed\n" + _COORD_LINE
    doc = _make_doc([text])
    _suppress_tesseract_coordinate_dump(doc)
    assert doc.pages[0].content_blocks[0].suppressed is True


def test_legitimate_tab_table_not_suppressed():
    """A regular tab-separated table without coordinate format is not suppressed."""
    text = "Item\tQuantidade\tValor\nA\t10\tR$ 1.234,56\nB\t5\tR$ 987,00"
    doc = _make_doc([text])
    _suppress_tesseract_coordinate_dump(doc)
    assert doc.pages[0].content_blocks[0].suppressed is False


def test_coordinate_dump_skips_already_suppressed():
    """Already-suppressed blocks are not touched by the coordinate dump suppressor."""
    doc = _make_doc([_COORD_BLOCK])
    doc.pages[0].content_blocks[0].suppressed = True
    doc.pages[0].content_blocks[0].suppression_reason = "prior_suppressor"
    _suppress_tesseract_coordinate_dump(doc)
    assert doc.pages[0].content_blocks[0].suppression_reason == "prior_suppressor"


def test_coordinate_dump_suppressor_is_idempotent():
    """Calling the coordinate suppressor twice is safe."""
    doc = _make_doc([_COORD_BLOCK])
    _suppress_tesseract_coordinate_dump(doc)
    _suppress_tesseract_coordinate_dump(doc)
    assert doc.pages[0].content_blocks[0].suppressed is True


def test_apply_entry_point_calls_coordinate_suppressor():
    """apply_tesseract_postprocessors suppresses coordinate dump blocks."""
    texts = [_COORD_BLOCK] + ["Content page " + str(i) for i in range(4)]
    doc = _make_doc(texts)
    apply_tesseract_postprocessors(doc)
    assert doc.pages[0].content_blocks[0].suppressed is True
    assert doc.pages[0].content_blocks[0].suppression_reason == "tesseract_coordinate_dump"


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
# Fix-N: _omitir_gabarito_split — split-row OMITIR NO GABARITO detection
# ---------------------------------------------------------------------------

def test_omitir_gabarito_split_exact_adjacent():
    """'OMITIR NO' and 'GABARITO' on consecutive lines → True."""
    text = "RODAPÉ\tIdentificador R$Valor:\nOMITIR NO\tdesconto:\nGABARITO\tOCR-VAL"
    assert _omitir_gabarito_split(text) is True


def test_omitir_gabarito_split_gap_of_two():
    """'OMITIR NO' and 'GABARITO' within 3 lines → True."""
    text = "RODAPÉ\nDECORATIVO\nOMITIR NO\talgo\nGABARITO\t..."
    assert _omitir_gabarito_split(text) is True


def test_omitir_gabarito_split_gap_of_four_is_false():
    """'OMITIR NO' and 'GABARITO' more than 3 lines apart → False."""
    lines = ["OMITIR NO\talgo", "linha1", "linha2", "linha3", "GABARITO\t..."]
    text = "\n".join(lines)
    assert _omitir_gabarito_split(text) is False


def test_omitir_gabarito_split_same_line():
    """On the same line, the existing _OMITIR_GABARITO_RE already matches
    and _omitir_gabarito_split also returns True (no regression)."""
    text = "OMITIR NO GABARITO"
    assert _omitir_gabarito_split(text) is True


def test_omitir_gabarito_split_only_omitir_no():
    """Only 'OMITIR NO' present without 'GABARITO' → False."""
    text = "OMITIR NO\talgo\noutro texto"
    assert _omitir_gabarito_split(text) is False


def test_omitir_gabarito_split_only_gabarito():
    """Only 'GABARITO' present without 'OMITIR NO' → False."""
    text = "RODAPÉ\nGABARITO\t..."
    assert _omitir_gabarito_split(text) is False


def test_omitir_gabarito_split_case_insensitive():
    """Match is case-insensitive; both 'no' and 'na' are accepted (n[ao])."""
    # 'omitir na' matches n[ao] pattern, and 'gabarito' is within 3 lines
    text = "omitir na\ngarantia\ngabarito\t..."
    assert _omitir_gabarito_split(text) is True
    # lowercase 'omitir no' also matches
    text2 = "OMITIR NO\tnada\nGABARITO"
    assert _omitir_gabarito_split(text2) is True


def test_suppress_corpus_markers_table_with_split_omitir():
    """TABLE block with split OMITIR NO / GABARITO rows is suppressed (Fix-N)."""
    # Simulate page 98 table block.text: rows joined with \n, cells with \t
    table_text = (
        "RODAPÉ\tIdentificador R$Valor:\tOlote Controle Página CORPUS\n"
        "DECORATIVO *\texato: 9.876,54;\tOCR-2026-0007 página:de 098 INTEGRADO\n"
        "OMITIR NO\tdesconto:\tV3 apresenta QA-P098 |OCR /\n"
        "GABARITO\tOCR-VALIDAÇÃO-2026-007. -2,75%; ação,\tLABORATÓRIO"
    )
    doc = _make_doc([table_text])
    _suppress_ocr_corpus_markers(doc)
    assert doc.pages[0].content_blocks[0].suppressed is True
    assert doc.pages[0].content_blocks[0].suppression_reason == "tesseract_ocr_corpus_marker"


def test_suppress_corpus_markers_normal_table_not_suppressed():
    """A normal table without OMITIR NO GABARITO is not suppressed."""
    normal_table = "Nome\tValor\nProduto A\tR$ 100,00\nProduto B\tR$ 200,00"
    doc = _make_doc([normal_table])
    _suppress_ocr_corpus_markers(doc)
    assert doc.pages[0].content_blocks[0].suppressed is False


def test_suppress_corpus_markers_split_omitir_lowercase():
    """Case-insensitive split detection — 'omitir no' and 'gabarito' lowercase."""
    table_text = "rodapé\ndecoratTIVO\nomitir no\toutro\ngabarito\tlorem"
    doc = _make_doc([table_text])
    _suppress_ocr_corpus_markers(doc)


# ---------------------------------------------------------------------------
# 11. Fix-O: _table_has_q_without_u and _suppress_rotated_table_garble
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


def test_suppress_rotated_table_garble_triggers_on_table_block():
    """TABLE block with Q-not-U long word is suppressed (Fix-O)."""
    # Simulate page 99 TABLE block.text (rows joined with \n, cells with \t)
    garbled_table = (
        "NVOS\tbhap\n"
        "+ OUNd\t66 eulbed\n"
        "ORIQLVHOIVT 490 opesio 00'00S'ZT\tON\n"
        "SAdHOD eulbed ajosuo\tIJVAOU"
    )
    doc = _make_doc([garbled_table], kind="table")
    _suppress_rotated_table_garble(doc)
    assert doc.pages[0].content_blocks[0].suppressed is True
    assert doc.pages[0].content_blocks[0].suppression_reason == "tesseract_rotated_table_garble"


def test_suppress_rotated_table_garble_skips_text_block():
    """TEXT blocks with Q-not-U are NOT suppressed (fix targets TABLE only)."""
    garbled_text = "ORIQLVHOIVT 490 opesio"
    doc = _make_doc([garbled_text], kind="text")
    _suppress_rotated_table_garble(doc)
    assert doc.pages[0].content_blocks[0].suppressed is False


def test_suppress_rotated_table_garble_normal_table_not_suppressed():
    """Normal financial table with no Q-not-U is NOT suppressed."""
    normal_table = (
        "Centro\tOrçado\tRealizado\tDiferença\n"
        "TI\tR$ 12.500,00\tR$ 13.000,00\tR$ +500,00\n"
        "RH\tR$ 7.250,00\tR$ 6.900,00\tR$ -350,00\n"
        "TOTAL\tR$ 19.750,00\tR$ 19.900,00\tR$ +150,00"
    )
    doc = _make_doc([normal_table], kind="table")
    _suppress_rotated_table_garble(doc)
    assert doc.pages[0].content_blocks[0].suppressed is False


def test_suppress_rotated_table_garble_qualidade_not_suppressed():
    """Table with 'QUALIDADE' (Q followed by U) is not suppressed."""
    table = "QUALIDADE\tPONTUAÇÃO\nExcelente\t10\nBom\t7"
    doc = _make_doc([table], kind="table")
    _suppress_rotated_table_garble(doc)
    assert doc.pages[0].content_blocks[0].suppressed is False


def test_suppress_rotated_table_garble_skips_already_suppressed():
    """Already-suppressed TABLE blocks are not double-processed."""
    garbled = "ORIQLVHOIVT\tgarbled"
    doc = _make_doc([garbled], kind="table")
    doc.pages[0].content_blocks[0].suppressed = True
    doc.pages[0].content_blocks[0].suppression_reason = "prior_reason"
    _suppress_rotated_table_garble(doc)
    assert doc.pages[0].content_blocks[0].suppression_reason == "prior_reason"
    assert doc.pages[0].content_blocks[0].suppressed is True


# ---------------------------------------------------------------------------
# Fix-P: _suppress_dollar_letter_garble
# ---------------------------------------------------------------------------

def test_suppress_dollar_letter_garble_table_block_with_3_patterns():
    """TABLE block with ≥2 '$[A-Z]' patterns is suppressed (Fix-P)."""
    garbled_table = (
        "NVOS\tbhap\n"
        "HDO ejusJasa 00'00S+$Y 00'0SE-$H 00'0ST+$U\teulbed"
    )
    doc = _make_doc([garbled_table], kind="table")
    _suppress_dollar_letter_garble(doc)
    assert doc.pages[0].content_blocks[0].suppressed is True
    assert doc.pages[0].content_blocks[0].suppression_reason == "tesseract_dollar_letter_garble"


def test_suppress_dollar_letter_garble_text_block_with_3_patterns():
    """TEXT block '$Y $H $U' (3 dollar-letter patterns) is suppressed (Fix-P)."""
    garbled_text = "$Y $H $U"
    doc = _make_doc([garbled_text], kind="text")
    _suppress_dollar_letter_garble(doc)
    assert doc.pages[0].content_blocks[0].suppressed is True
    assert doc.pages[0].content_blocks[0].suppression_reason == "tesseract_dollar_letter_garble"


def test_suppress_dollar_letter_garble_two_patterns_threshold():
    """Exactly 2 '$[A-Z]' patterns meets the threshold and is suppressed."""
    text = "00'0SE-$H 00'0ST+$U"
    doc = _make_doc([text], kind="text")
    _suppress_dollar_letter_garble(doc)
    assert doc.pages[0].content_blocks[0].suppressed is True


def test_suppress_dollar_letter_garble_one_pattern_not_suppressed():
    """Only 1 '$[A-Z]' pattern is below threshold — NOT suppressed."""
    text = "Resultado final $Y aprovado"
    doc = _make_doc([text], kind="text")
    _suppress_dollar_letter_garble(doc)
    assert doc.pages[0].content_blocks[0].suppressed is False


def test_suppress_dollar_letter_garble_normal_currency_not_suppressed():
    """Normal 'R$ 12.500,00' currency ($ followed by digit) is NOT suppressed."""
    table = (
        "Orçado\tRealizado\n"
        "R$ 12.500,00\tR$ 13.000,00\n"
        "R$ 7.250,00\tR$ 6.900,00"
    )
    doc = _make_doc([table], kind="table")
    _suppress_dollar_letter_garble(doc)
    assert doc.pages[0].content_blocks[0].suppressed is False


def test_suppress_dollar_letter_garble_skips_non_table_text_kinds():
    """TITLE and CAPTION blocks are NOT targeted by Fix-P."""
    for kind in ("title", "caption"):
        doc = _make_doc(["$Y $H $U garbled"], kind=kind)
        _suppress_dollar_letter_garble(doc)
        assert doc.pages[0].content_blocks[0].suppressed is False, f"kind={kind} should not be suppressed"


def test_suppress_dollar_letter_garble_skips_already_suppressed():
    """Already-suppressed blocks are not re-processed."""
    doc = _make_doc(["$Y $H $U"], kind="text")
    doc.pages[0].content_blocks[0].suppressed = True
    doc.pages[0].content_blocks[0].suppression_reason = "prior_reason"
    _suppress_dollar_letter_garble(doc)
    assert doc.pages[0].content_blocks[0].suppression_reason == "prior_reason"


def test_suppress_dollar_letter_garble_lowercase_dollar_not_matched():
    """'$y $h' (lowercase) does NOT match '$[A-Z]' — regex requires uppercase."""
    text = "$y $h $u"
    doc = _make_doc([text], kind="text")
    _suppress_dollar_letter_garble(doc)
    assert doc.pages[0].content_blocks[0].suppressed is False


def test_suppress_dollar_letter_garble_p116_table_pattern():
    """Simulated V3 page 116 TABLE content is suppressed by Fix-P."""
    garbled_p116 = (
        "HO\t\u0000jensia eSussosa 00'005+ 00'0SE- 00'0ST+\n"
        "eis\toB5ipuo&gt; $ $H $U"
    )
    doc = _make_doc([garbled_p116], kind="table")
    _suppress_dollar_letter_garble(doc)
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
# Fix-F05: _promote_post_table_checklist_items
# ---------------------------------------------------------------------------

def test_promote_post_table_checklist_basic():
    """Two short TEXT blocks after a TABLE are promoted to list items — V3 p88 pattern."""
    table = _Block(order_index=0, text="| Campo | Valor |", kind="table")
    b1 = _Block(order_index=1, text="Aprovado", kind="text")
    b2 = _Block(order_index=2, text="Pendente", kind="text")
    doc = _Doc([_Page([table, b1, b2])])
    _promote_post_table_checklist_items(doc)
    assert b1.kind == "list"
    assert len(b1.list_items) == 1
    assert b1.list_items[0].text == "Aprovado"
    assert b1.list_items[0].marker == "☐"
    assert b2.kind == "list"
    assert b2.list_items[0].text == "Pendente"


def test_promote_post_table_checklist_with_label():
    """TABLE → colon-label → short blocks: V6 p3/p33 pattern."""
    table = _Block(order_index=0, text="| Medição | Valor |", kind="table")
    label = _Block(order_index=1, text="Encaminhamento:", kind="text")
    b1 = _Block(order_index=2, text="Conferir", kind="text")
    b2 = _Block(order_index=3, text="Reabrir", kind="text")
    b3 = _Block(order_index=4, text="Registrar ciência", kind="text")
    doc = _Doc([_Page([table, label, b1, b2, b3])])
    _promote_post_table_checklist_items(doc)
    assert label.kind == "text"  # label itself stays TEXT
    assert b1.kind == "list" and b1.list_items[0].text == "Conferir"
    assert b2.kind == "list" and b2.list_items[0].text == "Reabrir"
    assert b3.kind == "list" and b3.list_items[0].text == "Registrar ciência"
    assert b1.list_items[0].marker == "☐"


def test_promote_post_table_checklist_noop_single_block():
    """Only 1 short block after TABLE — threshold not met, no promotion."""
    table = _Block(order_index=0, text="| A | B |", kind="table")
    b1 = _Block(order_index=1, text="Aprovado", kind="text")
    doc = _Doc([_Page([table, b1])])
    _promote_post_table_checklist_items(doc)
    assert b1.kind == "text"


def test_promote_post_table_checklist_noop_sentence_ending():
    """Block ending with '.' stops candidate collection — no promotion."""
    table = _Block(order_index=0, text="| A | B |", kind="table")
    b1 = _Block(order_index=1, text="Item um.", kind="text")
    b2 = _Block(order_index=2, text="Item dois", kind="text")
    doc = _Doc([_Page([table, b1, b2])])
    _promote_post_table_checklist_items(doc)
    assert b1.kind == "text"
    assert b2.kind == "text"


def test_promote_post_table_checklist_noop_allcaps():
    """ALL-CAPS block stops candidate collection before threshold."""
    table = _Block(order_index=0, text="| A | B |", kind="table")
    b1 = _Block(order_index=1, text="CONFERIDO", kind="text")
    b2 = _Block(order_index=2, text="Pendente", kind="text")
    doc = _Doc([_Page([table, b1, b2])])
    _promote_post_table_checklist_items(doc)
    assert b1.kind == "text"
    assert b2.kind == "text"


def test_promote_post_table_checklist_noop_no_table():
    """Without a TABLE trigger, short blocks are not promoted."""
    b1 = _Block(order_index=0, text="Aprovado", kind="text")
    b2 = _Block(order_index=1, text="Pendente", kind="text")
    doc = _Doc([_Page([b1, b2])])
    _promote_post_table_checklist_items(doc)
    assert b1.kind == "text"
    assert b2.kind == "text"


def test_promote_post_table_checklist_stops_at_long_block():
    """Block exceeding 40 chars stops candidate collection."""
    table = _Block(order_index=0, text="| A | B |", kind="table")
    b1 = _Block(order_index=1, text="Observação: texto muito longo que não é item de lista.", kind="text")
    b2 = _Block(order_index=2, text="Pendente", kind="text")
    doc = _Doc([_Page([table, b1, b2])])
    _promote_post_table_checklist_items(doc)
    assert b1.kind == "text"
    assert b2.kind == "text"


def test_promote_post_table_checklist_caption_kind_promoted():
    """CAPTION blocks (not just TEXT) qualify as candidates."""
    table = _Block(order_index=0, text="| A | B |", kind="table")
    b1 = _Block(order_index=1, text="Aprovado", kind="caption")
    b2 = _Block(order_index=2, text="Pendente", kind="caption")
    doc = _Doc([_Page([table, b1, b2])])
    _promote_post_table_checklist_items(doc)
    assert b1.kind == "list"
    assert len(b1.list_items) == 1
    assert b1.list_items[0].text == "Aprovado"
    assert b2.kind == "list"


def test_promote_post_table_checklist_noop_url_candidate():
    """A URL block (containing '://') is not a valid checklist candidate."""
    table = _Block(order_index=0, text="| A | B |", kind="table")
    b1 = _Block(order_index=1, text="Consultar autenticidade do re", kind="text")
    b2 = _Block(order_index=2, text="https://exemplo.invalid/V8-070", kind="text")
    doc = _Doc([_Page([table, b1, b2])])
    _promote_post_table_checklist_items(doc)
    # b1 alone (before URL broke the scan) is < 2 candidates — no promotion
    assert b1.kind == "text"
    assert b2.kind == "text"



# ---------------------------------------------------------------------------
# Fix-F05 v4 — _promote_ocr_checkbox_o_prefix
# ---------------------------------------------------------------------------

def test_promote_o_prefix_basic_pair():
    """'O Vistoria...' + 'Conferência...' → ☐ + ☑ pair promoted."""
    b1 = _Block(order_index=0, text="Conferência documental executada", kind="text")
    b2 = _Block(order_index=1, text="O Vistoria complementar pendente", kind="text")
    doc = _Doc([_Page([b1, b2])])
    _promote_ocr_checkbox_o_prefix(doc)
    assert b1.kind == "list"
    assert len(b1.list_items) == 1
    assert b1.list_items[0].marker == "☑"
    assert b1.list_items[0].text == "Conferência documental executada"
    assert b2.kind == "list"
    assert len(b2.list_items) == 1
    assert b2.list_items[0].marker == "☐"
    assert b2.list_items[0].text == "Vistoria complementar pendente"


def test_promote_o_prefix_noop_single_block():
    """Single 'O ' block alone is not promoted (needs ≥2 in the run)."""
    b1 = _Block(order_index=0, text="Outro item qualquer longa sentença.", kind="text")
    b2 = _Block(order_index=1, text="O Vistoria complementar pendente", kind="text")
    b3 = _Block(order_index=2, text="Texto longo que vai além de 40 caracteres e não entra.", kind="text")
    doc = _Doc([_Page([b1, b2, b3])])
    _promote_ocr_checkbox_o_prefix(doc)
    # b1 ends with '.' → not a candidate; b3 > 40 chars → not a candidate
    # b2 alone (single O-prefix block) → no promotion
    assert b2.kind == "text"


def test_promote_o_prefix_noop_field_value():
    """'Responsável: Marina Teixeira' is a field-value block, excluded from promotion."""
    b1 = _Block(order_index=0, text="Responsável: Marina Teixeira", kind="text")
    b2 = _Block(order_index=1, text="O Vistoria complementar pendente", kind="text")
    doc = _Doc([_Page([b1, b2])])
    _promote_ocr_checkbox_o_prefix(doc)
    # b1 matches _FIELD_VALUE_RE → excluded; b2 alone is single block → no promotion
    assert b1.kind == "text"
    assert b2.kind == "text"


def test_promote_o_prefix_noop_ending_punct():
    """Block ending with '.' is excluded; isolated 'O ' block not promoted."""
    b1 = _Block(order_index=0, text="O código 0011/2026 foi verificado.", kind="text")
    b2 = _Block(order_index=1, text="O Vistoria complementar pendente", kind="text")
    doc = _Doc([_Page([b1, b2])])
    _promote_ocr_checkbox_o_prefix(doc)
    # b1 ends with '.' → excluded. b2 alone → no promotion.
    assert b1.kind == "text"
    assert b2.kind == "text"


def test_promote_o_prefix_noop_url():
    """URL block excluded (contains '://')."""
    b1 = _Block(order_index=0, text="O Vistoria complementar pendente", kind="text")
    b2 = _Block(order_index=1, text="https://exemplo.invalid/form", kind="text")
    doc = _Doc([_Page([b1, b2])])
    _promote_ocr_checkbox_o_prefix(doc)
    # b2 has '://' → excluded. b1 alone → no promotion.
    assert b1.kind == "text"
    assert b2.kind == "text"


def test_promote_o_prefix_figure_kind():
    """FIGURE kind blocks qualify (Fix-F05 v5: scanned forms use FIGURE).

    Root cause for V6 pages 22/43/87: Tesseract layout engine classifies
    form field content as ContentKind.FIGURE on scanned_form pages.
    Fix-F05 v4 missed these because it only accepted TEXT/CAPTION.
    """
    b1 = _Block(order_index=0, text="Conferência documental executada", kind="figure")
    b2 = _Block(order_index=1, text="O Vistoria complementar pendente", kind="figure")
    doc = _Doc([_Page([b1, b2])])
    _promote_ocr_checkbox_o_prefix(doc)
    assert b1.kind == "list"
    assert b2.kind == "list"
    assert len(b1.list_items) == 1
    assert b1.list_items[0].marker == "☑"
    assert b1.list_items[0].text == "Conferência documental executada"
    assert len(b2.list_items) == 1
    assert b2.list_items[0].marker == "☐"
    assert b2.list_items[0].text == "Vistoria complementar pendente"


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
