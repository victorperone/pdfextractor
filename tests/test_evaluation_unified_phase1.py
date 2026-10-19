"""Unified evaluation — Fase 1 regression tests (spec §11 matrix).

Covers:
  T01–T12   CER/WER correctness, empty states, Markdown visibility
  T13–T16   Critical data: currency, numeric, percent
  T27–T31   Page-level edge cases, empty and missing pages
  T45       Whitespace/newline normalisation profile
  Invariants S+D+I == edit_distance, no double-counting of table words, etc.

All tests go through the public API:
  parse_document(), CanonicalDocument.from_nodes(), cer_sequential(),
  cer_markdown_raw(), wer_sequential(), compute_critical_data_metrics(), etc.

No OCR, no PDF reading, no network access.
"""
from __future__ import annotations

import math
import pytest
from collections import Counter

from structured_pdf_text.evaluation.models import (
    MetricStatus,
    NormProfile,
)
from structured_pdf_text.evaluation.markdown_parser import parse_document
from structured_pdf_text.evaluation.canonical_document import CanonicalDocument
from structured_pdf_text.evaluation.text_metrics import (
    cer_sequential,
    cer_markdown_raw,
    wer_sequential,
    _lev_distance,
    _lev_ops,
)
from structured_pdf_text.evaluation.critical_data import (
    compute_critical_data_metrics,
    _extract_numbers,
    _extract_currency,
    _extract_percent,
    _extract_dates,
    _extract_cpf,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _nodes(md: str, origin: str = "reference"):
    return parse_document(md, origin=origin)


def _cer(ref_md: str, hyp_md: str, profile=NormProfile.VISIBLE_TEXT):
    r = _nodes(ref_md, "reference")
    h = _nodes(hyp_md, "hypothesis")
    return cer_sequential(r, h, profile, "TEST-CER")


def _wer(ref_md: str, hyp_md: str):
    r = _nodes(ref_md, "reference")
    h = _nodes(hyp_md, "hypothesis")
    return wer_sequential(r, h)


def _cd(ref_md: str, hyp_md: str):
    r = _nodes(ref_md, "reference")
    h = _nodes(hyp_md, "hypothesis")
    return compute_critical_data_metrics(r, h)


# ---------------------------------------------------------------------------
# T01 — identity: CER = 0
# ---------------------------------------------------------------------------

def test_T01_cer_identity():
    res = _cer("abc", "abc")
    assert res.status == MetricStatus.OK
    assert res.value == 0.0
    c = res.counts
    assert c["S"] == 0 and c["D"] == 0 and c["I"] == 0


# ---------------------------------------------------------------------------
# T02 — one char substitution
# ---------------------------------------------------------------------------

def test_T02_cer_one_substitution():
    res = _cer("abc", "abd")
    assert res.status == MetricStatus.OK
    c = res.counts
    assert c["S"] == 1 and c["D"] == 0 and c["I"] == 0
    assert abs(res.value - 1/3) < 1e-6


# ---------------------------------------------------------------------------
# T03 — WER one-word substitution
# ---------------------------------------------------------------------------

def test_T03_wer_one_substitution():
    res = _wer("um dois", "um três")
    assert res.status == MetricStatus.OK
    c = res.counts
    assert c["S"] == 1 and c["ref_words"] == 2
    assert abs(res.value - 0.5) < 1e-6


# ---------------------------------------------------------------------------
# T04 — WER with insertions > 1.0 (no truncation)
# ---------------------------------------------------------------------------

def test_T04_wer_no_truncation():
    res = _wer("a b", "a b b b b")
    assert res.status == MetricStatus.OK
    assert res.value > 1.0
    assert abs(res.value - 1.5) < 1e-6


# ---------------------------------------------------------------------------
# T05 — hyp empty → CER = 1.0, all deletions
# ---------------------------------------------------------------------------

def test_T05_cer_hyp_empty():
    res = _cer("abc", "")
    assert res.status == MetricStatus.OK
    assert abs(res.value - 1.0) < 1e-6
    c = res.counts
    assert c["D"] == 3 and c["S"] == 0 and c["I"] == 0


# ---------------------------------------------------------------------------
# T06 — both empty → NOT_APPLICABLE, value=None
# ---------------------------------------------------------------------------

def test_T06_both_empty_not_applicable():
    res = _cer("", "")
    assert res.status == MetricStatus.NOT_APPLICABLE
    assert res.value is None


def test_T06_wer_both_empty():
    res = _wer("", "")
    assert res.status == MetricStatus.NOT_APPLICABLE
    assert res.value is None


# ---------------------------------------------------------------------------
# T07 — ref empty, hyp non-empty → UNDEFINED_DENOMINATOR
# ---------------------------------------------------------------------------

def test_T07_undefined_denominator():
    res = _cer("", "abc")
    assert res.status == MetricStatus.UNDEFINED_DENOMINATOR
    assert res.value is None
    assert res.counts["I"] == 3


# ---------------------------------------------------------------------------
# T08 — **bold** in hyp: CER visible = 0, WER visible = 0
# ---------------------------------------------------------------------------

def test_T08_bold_invisible():
    res = _cer("Aula importante", "**Aula importante**")
    assert res.status == MetricStatus.OK
    assert res.value == 0.0, f"Expected CER=0 for bold, got {res.value}"

    res_wer = _wer("Aula importante", "**Aula importante**")
    assert res_wer.status == MetricStatus.OK
    assert res_wer.value == 0.0, f"Expected WER=0 for bold, got {res_wer.value}"


# ---------------------------------------------------------------------------
# T09 — heading level change: visible CER = 0, raw CER > 0
# ---------------------------------------------------------------------------

def test_T09_heading_level_visible_cer_zero():
    res_vis = _cer("# Título", "## Título", NormProfile.VISIBLE_TEXT)
    assert res_vis.status == MetricStatus.OK
    assert res_vis.value == 0.0, f"CER visível deve ser 0: {res_vis.value}"

    res_raw = cer_markdown_raw(_nodes("# Título", "reference"), _nodes("## Título", "hypothesis"))
    assert res_raw.status == MetricStatus.OK
    assert res_raw.value > 0.0, f"CER raw deve ser >0 para mudança de nível: {res_raw.value}"


# ---------------------------------------------------------------------------
# T10 — accent preserved in strict profile: CER > 0
# ---------------------------------------------------------------------------

def test_T10_accent_strict():
    res = _cer("Olá mundo", "Ola mundo", NormProfile.VISIBLE_TEXT)
    assert res.status == MetricStatus.OK
    assert res.value > 0.0, f"CER com acento perdido deve ser >0: {res.value}"


# ---------------------------------------------------------------------------
# T11 — GFM table: words counted once, not 3× (A02 fix)
# ---------------------------------------------------------------------------

def test_T11_table_no_triple_count():
    table_md = "| Nome | Valor |\n|---|---|\n| Ana | 100 |\n"
    ref_nodes = _nodes(table_md, "reference")
    hyp_nodes = _nodes(table_md, "hypothesis")

    ref_doc = CanonicalDocument.from_nodes(ref_nodes)
    words = ref_doc.visible_text.split()
    # "Nome", "Valor", "Ana", "100" — each should appear exactly once
    counts = Counter(words)
    for w in ("Nome", "Valor", "Ana", "100"):
        assert counts[w] == 1, (
            f"Palavra '{w}' contada {counts[w]}× (esperado 1). Texto: {ref_doc.visible_text!r}"
        )

    # CER between identical tables must be 0
    res = _cer(table_md, table_md)
    assert res.status == MetricStatus.OK
    assert res.value == 0.0, f"CER entre tabelas idênticas: {res.value}"


# ---------------------------------------------------------------------------
# T12 — nested list, identical: CER = 0, no double-counting
# ---------------------------------------------------------------------------

def test_T12_list_no_double_count():
    # Lista aninhada: "item A" (pai de sub-lista) + "sub A1" (filho) + "item B"
    # O parser produz: LIST > LIST_ITEM > PARAGRAPH("item A") + LIST > LIST_ITEM > PARAGRAPH("sub A1")
    #                          LIST_ITEM > PARAGRAPH("item B")
    # A projeção canônica deve selecionar só os PARAGRAPHs terminais,
    # sem duplicar texto dos nós ancestrais (LIST, LIST_ITEM).
    # Portanto "sub" e "A1" devem aparecer exatamente 1× (não 2 ou 3×),
    # e "item" aparece 2× legitimamente (dois itens distintos contêm a palavra).
    list_md = "- item A\n  - sub A1\n- item B\n"
    ref_nodes = _nodes(list_md, "reference")
    ref_doc = CanonicalDocument.from_nodes(ref_nodes)
    words = ref_doc.visible_text.split()
    counts = Counter(words)
    # Palavras únicas no texto não devem ser duplicadas por herança de ancestrais:
    for w in ("sub", "A1"):
        assert counts.get(w, 0) == 1, (
            f"Palavra '{w}' contada {counts.get(w,0)}× (esperado 1). Texto: {ref_doc.visible_text!r}"
        )
    # "item" aparece 2× legitimamente (dois LIST_ITEMs com essa palavra):
    assert counts.get("item", 0) == 2, (
        f"Palavra 'item' contada {counts.get('item',0)}× (esperado 2 — dois itens distintos)."
    )
    # CER entre lista idêntica é 0:
    res = _cer(list_md, list_md)
    assert res.status == MetricStatus.OK
    assert res.value == 0.0


# ---------------------------------------------------------------------------
# T13 — currency: R$ 1234,00 vs R$ 1235,00 → F1 = 0 (A01 fix)
# ---------------------------------------------------------------------------

def test_T13_currency_different_values():
    metrics = _cd("R$ 1234,00", "R$ 1235,00")
    cur = metrics.currency
    assert cur is not None
    assert cur.status == MetricStatus.OK
    assert cur.value == 0.0, f"Currency F1 deveria ser 0: {cur.value}"
    c = cur.counts
    assert c["tp"] == 0 and c["fp"] == 1 and c["fn"] == 1


# ---------------------------------------------------------------------------
# T14 — R$ 1.234,00 vs R$ 1234,00 → same canonical value → F1 = 1.0
# ---------------------------------------------------------------------------

def test_T14_currency_canonical_equal():
    metrics = _cd("R$ 1.234,00", "R$ 1234,00")
    cur = metrics.currency
    assert cur is not None
    assert cur.status == MetricStatus.OK
    assert cur.value == 1.0, f"Currency F1 deveria ser 1.0 (mesmo valor): {cur.value}"


# ---------------------------------------------------------------------------
# T15 — percent vs plain number: typed F1 = 0 (A06 fix)
# ---------------------------------------------------------------------------

def test_T15_percent_vs_number():
    metrics = _cd("30%", "30")
    pct = metrics.percent
    num = metrics.numeric
    # percent category: ref has "30%", hyp has no percent → F1 = 0
    assert pct is not None
    assert pct.value == 0.0 or pct.status in (
        MetricStatus.FALSE_NEGATIVE_ONLY, MetricStatus.OK
    ), f"percent status={pct.status} value={pct.value}"
    if pct.status == MetricStatus.OK:
        assert pct.value == 0.0

    # The number "30" in hyp is numeric; "30%" in ref is not numeric (span-protected)
    # So numeric: ref has nothing (30 is part of percent span), hyp has "30"
    # → either not_applicable or false_positive_only, NOT a match
    if num is not None and num.status == MetricStatus.OK:
        assert num.value == 0.0, (
            f"numeric F1 não deve ser 1.0 quando ref=percent e hyp=número puro: {num.value}"
        )


# ---------------------------------------------------------------------------
# T16 — integer recognition: "Existem 12 itens" → numeric F1 = 0 when mismatched (A08 fix)
# ---------------------------------------------------------------------------

def test_T16_short_integer_recognized():
    # Verify that small integers are extracted (A08 fix: no 4+ digit requirement)
    nums = _extract_numbers("Existem 12 itens no total")
    assert any("12" in n for n in nums), f"'12' não foi extraído: {nums}"

    # Mismatched: 12 vs 13
    metrics = _cd("Existem 12 itens", "Existem 13 itens")
    num = metrics.numeric
    assert num is not None
    assert num.status != MetricStatus.NOT_APPLICABLE, (
        "numeric não deve ser NOT_APPLICABLE quando há inteiros"
    )
    if num.status == MetricStatus.OK:
        assert num.value == 0.0, f"numeric F1 deveria ser 0 (12≠13): {num.value}"


# ---------------------------------------------------------------------------
# T27 — page 1 filled, page 2 empty in both: page 2 is N/A
# ---------------------------------------------------------------------------

def test_T27_empty_page_not_applicable():
    ref_md = "## Página 1\n\nTexto de referência.\n\n## Página 2\n\n"
    hyp_md = "## Página 1\n\nTexto de referência.\n\n## Página 2\n\n"
    ref_nodes = _nodes(ref_md, "reference")
    hyp_nodes = _nodes(hyp_md, "hypothesis")
    # page 2 contributes no nodes; page 1 should give CER=0
    res = cer_sequential(ref_nodes, hyp_nodes, NormProfile.VISIBLE_TEXT, "T27")
    assert res.status == MetricStatus.OK
    assert res.value == 0.0


# ---------------------------------------------------------------------------
# T29 — no ref, no hyp for a page: no crash, not_applicable
# ---------------------------------------------------------------------------

def test_T29_both_sides_no_content():
    res = _cer("", "")
    assert res.status == MetricStatus.NOT_APPLICABLE
    assert res.value is None


# ---------------------------------------------------------------------------
# T45 — whitespace: multiple spaces and newlines → canonical via profile
# ---------------------------------------------------------------------------

def test_T45_whitespace_normalisation():
    ref = "um   dois\n\ntrês"
    hyp = "um dois\ntrês"
    res = _cer(ref, hyp, NormProfile.UNICODE_LAYOUT)
    # After collapse, both become "um dois três" — CER should be 0
    assert res.status == MetricStatus.OK
    assert res.value == 0.0, f"CER com espaços extras deve ser 0: {res.value}"


# ---------------------------------------------------------------------------
# Invariant: S + D + I == edit_distance
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ref,hyp", [
    (list("abc"), list("abc")),
    (list("abc"), list("abd")),
    (list("kitten"), list("sitting")),
    ([], list("abc")),
    (list("abc"), []),
    (list("a"), list("a" * 10)),
    (list("abcde"), list("ace")),
    (list(""), list("")),
])
def test_invariant_sdi_equals_distance(ref, hyp):
    S, D, I = _lev_ops(ref, hyp)
    dist = _lev_distance(ref, hyp)
    assert S + D + I == dist, (
        f"S+D+I={S+D+I} ≠ edit_distance={dist} for ref={ref!r} hyp={hyp!r}"
    )


# ---------------------------------------------------------------------------
# Invariant: CER ≥ 0 when defined
# ---------------------------------------------------------------------------

def test_invariant_cer_nonnegative():
    for ref, hyp in [("abc", "xyz"), ("a", "aaa"), ("", "")]:
        r = _cer(ref, hyp)
        if r.value is not None:
            assert r.value >= 0.0


# ---------------------------------------------------------------------------
# Invariant: no NaN / Infinity in MetricResult values
# ---------------------------------------------------------------------------

def test_invariant_no_nan_inf():
    for ref, hyp in [("", ""), ("abc", ""), ("", "abc"), ("abc", "abc")]:
        res = _cer(ref, hyp)
        if res.value is not None:
            assert math.isfinite(res.value), f"CER non-finite: {res.value}"
        res_w = _wer(ref, hyp)
        if res_w.value is not None:
            assert math.isfinite(res_w.value), f"WER non-finite: {res_w.value}"


# ---------------------------------------------------------------------------
# Invariant: formatting marker in hyp does NOT change CER visible
# ---------------------------------------------------------------------------

def test_invariant_bold_does_not_change_visible_cer():
    plain = "Texto simples"
    bold = "**Texto simples**"
    res_plain = _cer(plain, plain)
    res_bold = _cer(plain, bold)
    assert res_plain.value == res_bold.value == 0.0


# ---------------------------------------------------------------------------
# Invariant: percent ≠ numeric for the same digit string (A06 fix)
# ---------------------------------------------------------------------------

def test_invariant_percent_not_numeric():
    pcts = _extract_percent("taxa de 30%")
    nums = _extract_numbers("taxa de 30%")
    assert any("30" in p for p in pcts), f"30% não extraído como percent: {pcts}"
    # After span protection, "30" (part of "30%") must NOT appear in numerics
    assert not any("30" == n for n in nums), (
        f"'30' não deveria aparecer em numeric após proteção de spans: {nums}"
    )


# ---------------------------------------------------------------------------
# Currency extraction: R$ 1.234,00 and R$ 1234,00 same canonical value
# ---------------------------------------------------------------------------

def test_currency_canonical_form():
    a = _extract_currency("R$ 1.234,00")
    b = _extract_currency("R$ 1234,00")
    assert a == b, f"Formas canônicas deveriam ser iguais: {a} vs {b}"


# ---------------------------------------------------------------------------
# Date validation: 29/02/1900 invalid, 29/02/2000 valid
# ---------------------------------------------------------------------------

def test_T37_date_validation():
    # 1900 is a century year not divisible by 400 → not a leap year
    invalid = _extract_dates("Data: 29/02/1900")
    assert len(invalid) == 0, f"29/02/1900 deveria ser inválida: {invalid}"

    # 2000 is divisible by 400 → leap year
    valid = _extract_dates("Data: 29/02/2000")
    assert len(valid) == 1 and valid[0] == "2000-02-29", f"29/02/2000 deveria ser válida: {valid}"


# ---------------------------------------------------------------------------
# T38 — CPF leading zeros: 00123 ≠ 123
# ---------------------------------------------------------------------------

def test_T38_cpf_leading_zeros():
    a = _extract_cpf("CPF: 001.234.567-89")
    b = _extract_cpf("CPF: 123456789")  # not a valid 11-digit CPF
    assert a != b or (not a and not b), (
        f"CPFs com e sem zeros iniciais devem ser distintos: {a} vs {b}"
    )
    # Specifically, the canonical form must preserve leading zeros
    if a:
        assert a[0].startswith("001"), f"Zero inicial perdido: {a[0]}"


# ---------------------------------------------------------------------------
# check_comparability: None == None is NOT comparable (A09 fix)
# ---------------------------------------------------------------------------

def test_A09_none_hash_not_comparable():
    from structured_pdf_text.evaluation.aggregation import check_comparability

    a = {"schema_version": "evaluation-3.0.0", "inputs": {"reference_sha256": None, "selected_pages": [1]}}
    b = {"schema_version": "evaluation-3.0.0", "inputs": {"reference_sha256": None, "selected_pages": [1]}}
    ok, reasons = check_comparability(a, b)
    assert not ok, "Dois hashes None não devem ser comparáveis (fail-closed)"
    assert any("sha256" in r for r in reasons)


# ---------------------------------------------------------------------------
# schema_version = evaluation-3.0.0
# ---------------------------------------------------------------------------

def test_schema_version_is_3():
    from structured_pdf_text.evaluation.aggregation import V2DocumentMetrics
    doc = V2DocumentMetrics()
    assert doc.schema_version == "evaluation-3.0.0"
    d = doc.to_dict()
    assert d["schema_version"] == "evaluation-3.0.0"


# ---------------------------------------------------------------------------
# CanonicalDocument: leaf projection excludes TABLE/TABLE_ROW/LIST containers
# ---------------------------------------------------------------------------

def test_canonical_doc_no_container_nodes():
    from structured_pdf_text.evaluation.models import NodeKind
    from structured_pdf_text.evaluation.canonical_document import _CONTAINER_KINDS

    table_md = "| A | B |\n|---|---|\n| 1 | 2 |\n"
    nodes = _nodes(table_md)
    doc = CanonicalDocument.from_nodes(nodes)

    for leaf in doc.leaf_nodes:
        assert leaf.kind not in _CONTAINER_KINDS, (
            f"Container node {leaf.kind} não deveria estar nos leaf_nodes"
        )
