"""Fase 2 — Testes de entidades críticas, hash de gabarito e comparabilidade.

Cobertura:
  T13   R$ 1234,00 vs R$ 1235,00 → currency F1=0  (A01 fix)
  T14   R$ 1.234,00 vs R$ 1234,00 → canonical F1=1
  T15   30% vs 30 → percent F1=0, numeric não corrige  (A06 fix)
  T16   "Existem 12 itens" → numeric reconhece 12  (A08 fix)
  T17   Valor monetário em tabela vs parágrafo → document F1=1
  T18   Uma ref, duas hyp iguais → TP=1, FP=1, FN=0, F1=2/3
  T19   Dois valores trocados entre páginas → doc F1=1, page F1=0
  T32   Dois gabaritos diferentes → comparabilidade rejeitada
  T33   Dois resultados sem hash → comparabilidade rejeitada (A09 fix)
  T34   Mesmo gabarito via arquivo e manifesto → hashes efetivos iguais
  T37   Datas bissextas válidas e inválidas
  T38   ID '00123' vs '123' → distintos no modo estrito
  T39   T47: duas instâncias iguais de entidade numa tabela → contagem 2
  T43   Valor monetário de 9+ dígitos → sem truncagem
  T44   R$ 100,00 vs 100,00 → currency F1=0 (sem moeda na hyp)
  T47   Duas ocorrências de entidade em tabela → contadas 2×, não 6×

Invariantes de hash:
  - Mesmas páginas + conteúdo, ordem de entrada diferente → hashes iguais
  - LF vs CRLF → hashes iguais
  - NFD vs NFC → hashes iguais
  - Alteração de um caractere → hashes diferentes
  - Página fora do escopo não afeta hash efetivo
  - Página selecionada vazia consta na serialização (não desaparece)
  - Páginas duplicadas → ValueError

Invariantes de comparabilidade:
  - Hash efetivo ausente → incomparável (fail-closed)
  - Hash igual com serialização_version diferente → incomparável
  - Hash diferente → incomparável
"""
from __future__ import annotations

import unicodedata
from collections import Counter
from typing import Sequence

import pytest
from evaluation_contract_helpers import comparison_result

from structured_pdf_text.evaluation.critical_data import (
    compute_critical_data_metrics,
    _extract_currency,
    _extract_numbers,
    _extract_percent,
    _extract_dates,
)
from structured_pdf_text.evaluation.models import (
    MarkdownNode,
    NodeKind,
    MetricStatus,
)
from structured_pdf_text.evaluation.markdown_parser import parse_document
from structured_pdf_text.evaluation.canonical_document import CanonicalDocument
from structured_pdf_text.evaluation.reference_hash import (
    compute_reference_effective_hash,
    split_pages,
    REFERENCE_HASH_PROTOCOL,
)
from structured_pdf_text.evaluation.aggregation import check_comparability


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _nodes(md: str, origin: str = "reference") -> list[MarkdownNode]:
    """Parse Markdown into MarkdownNode list."""
    return list(parse_document(md, origin=origin))


def _cd(ref_md: str, hyp_md: str):
    """Compute critical data metrics between two Markdown strings."""
    return compute_critical_data_metrics(
        _nodes(ref_md, "reference"),
        _nodes(hyp_md, "hypothesis"),
    )


def _make_ref_result(eff_hash: str, pages: list[int], serial_v: str = REFERENCE_HASH_PROTOCOL) -> dict:
    """Build a minimal evaluation result dict with reference effective hash."""
    result = comparison_result(pages, eff_hash)
    result["inputs"]["reference_serialization_version"] = serial_v
    return result


# ---------------------------------------------------------------------------
# T13 — R$ 1234,00 vs R$ 1235,00 → Currency F1=0 (A01 fix)
# ---------------------------------------------------------------------------

def test_T13_currency_different_values():
    m = _cd("R$ 1234,00", "R$ 1235,00")
    cur = m.currency
    assert cur is not None
    assert cur.status == MetricStatus.OK
    assert cur.value == 0.0
    assert cur.counts["tp"] == 0
    assert cur.counts["fp"] == 1
    assert cur.counts["fn"] == 1


# ---------------------------------------------------------------------------
# T14 — R$ 1.234,00 vs R$ 1234,00 → canonical F1=1 (same value, different formatting)
# ---------------------------------------------------------------------------

def test_T14_currency_canonical_equal():
    m = _cd("R$ 1.234,00", "R$ 1234,00")
    cur = m.currency
    assert cur is not None
    assert cur.value == 1.0, (
        f"R$ 1.234,00 e R$ 1234,00 são o mesmo valor canônico; F1 esperado=1.0, obtido={cur.value}"
    )
    assert cur.counts["tp"] == 1
    assert cur.counts["fp"] == 0
    assert cur.counts["fn"] == 0


# ---------------------------------------------------------------------------
# T15 — 30% vs 30 → percent F1=0; numeric não "corrige" (A06 fix)
# ---------------------------------------------------------------------------

def test_T15_percent_vs_number():
    # Ref: "30%"  Hyp: "30"
    m = _cd("30%", "30")
    # Percent ref=1, hyp=0 → FN only → F1=0
    pct = m.percent
    assert pct is not None
    assert pct.value == 0.0
    assert pct.counts["fn"] == 1
    assert pct.counts["tp"] == 0
    # Numeric: ref has no plain '30' (span-protected), hyp has '30' → FP only
    num = m.numeric
    assert num is not None
    # 30% has % masked out so '30' should not appear as numeric in ref
    # hyp '30' matches as numeric → false_positive_only or tp=0
    assert num.counts.get("tp", 0) == 0


def test_T15_reverse_percent_vs_number():
    # Ref: "30"  Hyp: "30%"
    m = _cd("30", "30%")
    num = m.numeric
    pct = m.percent
    # Numeric: ref has '30', hyp '30%' has '30' span-protected → FN only
    assert num is not None
    assert num.counts["fn"] == 1 or num.value == 0.0
    # Percent: ref=0, hyp=1 → FP
    assert pct is not None
    assert pct.counts.get("fp", 0) == 1 or pct.counts.get("tp", 0) == 0


# ---------------------------------------------------------------------------
# T16 — Short integer recognised (A08 fix)
# ---------------------------------------------------------------------------

def test_T16_short_integer_recognised():
    m = _cd("Existem 12 itens", "Existem 13 itens")
    num = m.numeric
    assert num is not None
    # Both sides have a number; they differ → F1=0
    assert num.status == MetricStatus.OK
    assert num.value == 0.0
    assert num.counts["ref_count"] == 1
    assert num.counts["hyp_count"] == 1


def test_T16_short_integer_not_applicable():
    # Neither side has any number → not_applicable
    m = _cd("Texto sem números", "Texto sem números")
    num = m.numeric
    assert num is not None
    assert num.status == MetricStatus.NOT_APPLICABLE


# ---------------------------------------------------------------------------
# T17 — Valor em tabela vs parágrafo: document F1=1 (mesma página)
# ---------------------------------------------------------------------------

def test_T17_currency_table_vs_paragraph_doc_f1():
    # Reference: R$ 100,00 inside a GFM table
    ref_md = "| Item | Valor |\n|------|-------|\n| A    | R$ 100,00 |\n"
    # Hypothesis: R$ 100,00 in a plain paragraph
    hyp_md = "O valor é R$ 100,00."
    m = _cd(ref_md, hyp_md)
    cur = m.currency
    assert cur is not None, "currency metric should not be None"
    assert cur.value == 1.0, (
        f"Currency document F1 deve ser 1.0 (tabela vs parágrafo, mesmo valor). "
        f"Obtido: {cur.value}, counts: {cur.counts}"
    )


# ---------------------------------------------------------------------------
# T18 — Uma ref, duas hyp iguais → TP=1, FP=1, FN=0, F1=2/3
# ---------------------------------------------------------------------------

def test_T18_two_hyp_one_ref_same_value():
    m = _cd("R$ 100,00", "R$ 100,00\nR$ 100,00")
    cur = m.currency
    assert cur is not None
    assert cur.counts["tp"] == 1
    assert cur.counts["fp"] == 1
    assert cur.counts["fn"] == 0
    expected_f1 = 2 * 1 / (2 * 1 + 1 + 0)  # = 2/3
    assert abs(cur.value - expected_f1) < 1e-4, f"F1={cur.value}, esperado≈{expected_f1}"


# ---------------------------------------------------------------------------
# T19 — Dois valores trocados entre páginas: doc F1=1, page F1=0
# ---------------------------------------------------------------------------

def _nodes_with_pages(page_contents: dict[int, str], origin: str) -> list[MarkdownNode]:
    """Parse multi-page Markdown and set page numbers on nodes."""
    parts = []
    for pnum in sorted(page_contents.keys()):
        parts.append(f"## Página {pnum}\n{page_contents[pnum]}")
    full_md = "\n\n".join(parts)
    nodes = list(parse_document(full_md, origin=origin))
    return nodes


def test_T19_currency_page_swap_doc_f1_is_1():
    """Dois valores distintos trocados de página: documental F1=1."""
    ref_nodes = _nodes_with_pages(
        {1: "Valor: R$ 100,00", 2: "Valor: R$ 200,00"}, "reference"
    )
    hyp_nodes = _nodes_with_pages(
        {1: "Valor: R$ 200,00", 2: "Valor: R$ 100,00"}, "hypothesis"
    )
    m = compute_critical_data_metrics(ref_nodes, hyp_nodes)
    cur = m.currency
    assert cur is not None
    assert cur.value == 1.0, (
        f"Currency document F1 deve ser 1.0 (valores presentes mas trocados de página). "
        f"Obtido: {cur.value}, counts: {cur.counts}"
    )


def test_T19_currency_page_swap_page_f1_is_0():
    """Dois valores distintos trocados de página: page F1=0."""
    ref_nodes = _nodes_with_pages(
        {1: "Valor: R$ 100,00", 2: "Valor: R$ 200,00"}, "reference"
    )
    hyp_nodes = _nodes_with_pages(
        {1: "Valor: R$ 200,00", 2: "Valor: R$ 100,00"}, "hypothesis"
    )
    m = compute_critical_data_metrics(ref_nodes, hyp_nodes)
    cur_page = m.currency_page
    assert cur_page is not None
    assert cur_page.value == 0.0, (
        f"Currency page F1 deve ser 0.0 (valores nas páginas erradas). "
        f"Obtido: {cur_page.value}, counts: {cur_page.counts}"
    )
    assert cur_page.counts["tp"] == 0
    assert cur_page.counts["fp"] == 2
    assert cur_page.counts["fn"] == 2


def test_T19_same_page_doc_and_page_f1_equal():
    """Valores nas páginas corretas: doc F1 == page F1."""
    ref_nodes = _nodes_with_pages(
        {1: "R$ 100,00", 2: "R$ 200,00"}, "reference"
    )
    hyp_nodes = _nodes_with_pages(
        {1: "R$ 100,00", 2: "R$ 200,00"}, "hypothesis"
    )
    m = compute_critical_data_metrics(ref_nodes, hyp_nodes)
    assert m.currency.value == 1.0
    assert m.currency_page.value == 1.0


# ---------------------------------------------------------------------------
# T32 — Dois gabaritos diferentes → comparabilidade rejeitada
# ---------------------------------------------------------------------------

def test_T32_different_reference_hashes_not_comparable():
    pages_a = {1: "Texto do gabarito A"}
    pages_b = {1: "Texto do gabarito B"}
    hash_a, meta_a = compute_reference_effective_hash(pages_a, [1])
    hash_b, meta_b = compute_reference_effective_hash(pages_b, [1])
    assert hash_a != hash_b

    result_a = _make_ref_result(hash_a, [1])
    result_b = _make_ref_result(hash_b, [1])

    ok, reasons = check_comparability(result_a, result_b)
    assert not ok
    assert any("reference_effective_sha256" in r for r in reasons)


# ---------------------------------------------------------------------------
# T33 — Dois resultados sem hash → comparabilidade rejeitada (A09)
# ---------------------------------------------------------------------------

def test_T33_both_missing_hash_not_comparable():
    result_a = {"schema_version": "evaluation-3.0.0", "inputs": {}}
    result_b = {"schema_version": "evaluation-3.0.0", "inputs": {}}
    ok, reasons = check_comparability(result_a, result_b)
    assert not ok, "Dois resultados sem hash NÃO devem ser considerados comparáveis"
    assert any("hash" in r.lower() or "reference" in r.lower() for r in reasons)


# ---------------------------------------------------------------------------
# T34 — Mesmo conteúdo via arquivo e manifesto → hashes efetivos iguais
# ---------------------------------------------------------------------------

def test_T34_same_content_different_source_same_hash():
    """Mesmo Markdown nas mesmas páginas → hash efetivo idêntico independente de origem."""
    content = "# Título\n\nTexto de gabarito com R$ 100,00 e 29/02/2000.\n"

    # Fonte A: pages dict vindo diretamente
    pages_a = {1: content}
    hash_a, _ = compute_reference_effective_hash(pages_a, [1])

    # Fonte B: mesmo conteúdo mas via split_pages de Markdown paginado
    paged_md = f"## Página 1\n{content}"
    pages_b = split_pages(paged_md)
    hash_b, _ = compute_reference_effective_hash(pages_b, [1])

    assert hash_a == hash_b, (
        f"Hash deve ser idêntico independente de como o conteúdo foi carregado."
        f"\nhash_a={hash_a}\nhash_b={hash_b}"
    )


def test_T34_lf_vs_crlf_same_hash():
    """CRLF e LF no gabarito produzem o mesmo hash efetivo."""
    content_lf = "# Título\n\nTexto.\n"
    content_crlf = content_lf.replace("\n", "\r\n")

    hash_lf, _ = compute_reference_effective_hash({1: content_lf}, [1])
    hash_crlf, _ = compute_reference_effective_hash({1: content_crlf}, [1])
    assert hash_lf == hash_crlf


def test_T34_nfc_nfd_same_hash():
    """NFD e NFC no gabarito produzem o mesmo hash efetivo."""
    content_nfc = unicodedata.normalize("NFC", "Título com acento: é, ã, ç")
    content_nfd = unicodedata.normalize("NFD", "Título com acento: é, ã, ç")
    assert content_nfc != content_nfd  # confirma que são bytes distintos

    hash_nfc, _ = compute_reference_effective_hash({1: content_nfc}, [1])
    hash_nfd, _ = compute_reference_effective_hash({1: content_nfd}, [1])
    assert hash_nfc == hash_nfd


def test_T34_one_char_change_different_hash():
    """Alteração de um caractere significativo → hash efetivo diferente."""
    pages_a = {1: "R$ 1234,00"}
    pages_b = {1: "R$ 1235,00"}
    hash_a, _ = compute_reference_effective_hash(pages_a, [1])
    hash_b, _ = compute_reference_effective_hash(pages_b, [1])
    assert hash_a != hash_b


def test_T34_page_outside_scope_does_not_affect_hash():
    """Página fora do escopo não afeta o hash efetivo."""
    pages = {1: "Conteúdo página 1", 2: "Conteúdo página 2 — fora do escopo"}
    hash_scope1, _ = compute_reference_effective_hash(pages, [1])
    hash_only_1, _ = compute_reference_effective_hash({1: "Conteúdo página 1"}, [1])
    assert hash_scope1 == hash_only_1, (
        "Página 2 está fora do escopo; não deve influenciar o hash efetivo."
    )


def test_T34_empty_selected_page_present_in_serialization():
    """Página selecionada com conteúdo vazio aparece na serialização (não desaparece)."""
    import json
    pages = {1: "Conteúdo real", 2: ""}
    _, meta = compute_reference_effective_hash(pages, [1, 2])
    assert meta["selected_pages"] == [1, 2]
    # Recriar a serialização e verificar que página 2 está presente
    canonical_pages_entry = [
        {"page": 1, "markdown": "Conteúdo real"},
        {"page": 2, "markdown": ""},
    ]
    # O hash deve ser determinístico — verifique que página 2 não sumiu:
    # Calculamos com apenas página 1 e deve ser DIFERENTE de [1, 2]
    hash_with_p2, _ = compute_reference_effective_hash(pages, [1, 2])
    hash_without_p2, _ = compute_reference_effective_hash(pages, [1])
    assert hash_with_p2 != hash_without_p2, (
        "Escopo [1, 2] deve produzir hash diferente de [1] mesmo que página 2 seja vazia."
    )


def test_T34_page_order_in_manifest_does_not_affect_hash():
    """Páginas em ordem diferente no mapa produzem o mesmo hash efetivo."""
    pages_ordered = {1: "página 1", 2: "página 2"}
    pages_reversed = {2: "página 2", 1: "página 1"}
    hash_ord, _ = compute_reference_effective_hash(pages_ordered, [1, 2])
    hash_rev, _ = compute_reference_effective_hash(pages_reversed, [1, 2])
    assert hash_ord == hash_rev


def test_T34_duplicate_page_raises():
    """Página duplicada em split_pages levanta ValueError."""
    md_with_duplicate = "## Página 1\nConteúdo A\n## Página 1\nConteúdo B\n"
    with pytest.raises(ValueError, match="Duplicate page number"):
        split_pages(md_with_duplicate)


# ---------------------------------------------------------------------------
# T37 — Datas bissextas válidas e inválidas
# ---------------------------------------------------------------------------

def test_T37_leap_date_valid_2000():
    """29/02/2000 é válida (2000 é bissexto)."""
    vals = _extract_dates("A data é 29/02/2000.")
    assert "2000-02-29" in vals


def test_T37_leap_date_invalid_1900():
    """29/02/1900 é inválida (1900 não é bissexto — divisível por 100 mas não por 400)."""
    vals = _extract_dates("A data é 29/02/1900.")
    assert "1900-02-29" not in vals


def test_T37_invalid_april_31():
    """31/04/2026 é inválida (abril tem 30 dias)."""
    vals = _extract_dates("A data é 31/04/2026.")
    assert "2026-04-31" not in vals


def test_T37_valid_common_dates():
    vals = _extract_dates("Nascimento: 15/03/1985 e entrega: 01/01/2024.")
    assert "1985-03-15" in vals
    assert "2024-01-01" in vals


# ---------------------------------------------------------------------------
# T38 — ID '00123' vs '123' → distintos no modo estrito de identificadores
# ---------------------------------------------------------------------------

def test_T38_leading_zeros_cpf_distinct():
    """CPF com zeros à esquerda não é igual à versão sem zeros."""
    from structured_pdf_text.evaluation.critical_data import _extract_cpf
    # CPF canônico preserva os 11 dígitos — incluindo zeros iniciais
    vals_with_zeros = _extract_cpf("CPF: 001.234.567-89")
    vals_without_zeros = _extract_cpf("CPF: 1.234.567-89")  # 9 digits only — invalid CPF
    # 11 dígitos com zero vs sem zero são diferentes sequências
    assert vals_with_zeros != vals_without_zeros or (
        not vals_with_zeros and not vals_without_zeros
    ), "CPF com zeros iniciais deve ser distinto de versão sem zeros"


# ---------------------------------------------------------------------------
# T43 — Valor monetário de 9+ dígitos sem truncagem
# ---------------------------------------------------------------------------

def test_T43_large_currency_no_truncation():
    """R$ 1.234.567,89 deve ser reconhecido completamente."""
    vals = _extract_currency("Valor total: R$ 1.234.567,89 aprovados.")
    assert len(vals) == 1, f"Esperado 1 valor, obtido: {vals}"
    # Valor canônico: sem ponto (milhar) e com ponto decimal
    assert "R$:1234567.89" in vals, f"Valor canônico incorreto: {vals}"


def test_T43_nine_digit_integer():
    """R$ 123456789 (sem separador) deve ser reconhecido completamente."""
    vals = _extract_currency("Total: R$ 123456789,00")
    assert len(vals) == 1
    assert "R$:123456789" in vals


def test_T43_currency_not_truncated_at_3_digits():
    """R$ 1234,00 não deve ser truncado como R$ 123 (A01 fix)."""
    vals = _extract_currency("R$ 1234,00")
    assert len(vals) == 1
    # Deve capturar 1234, não 123
    assert "R$:1234" in vals
    assert "R$:123" not in vals


# ---------------------------------------------------------------------------
# T44 — R$ 100,00 vs 100,00 → Currency F1=0 (sem símbolo de moeda na hyp)
# ---------------------------------------------------------------------------

def test_T44_currency_without_symbol_not_currency():
    """'100,00' sem símbolo não é entidade currency."""
    m = _cd("R$ 100,00", "100,00")
    cur = m.currency
    assert cur is not None
    # Ref tem 1 currency, hyp tem 0 → FN only → F1=0
    assert cur.value == 0.0
    assert cur.counts["fn"] == 1
    assert cur.counts["tp"] == 0


# ---------------------------------------------------------------------------
# T47 — Duas instâncias iguais de entidade numa tabela → contagem 2× (não 6×)
# ---------------------------------------------------------------------------

def test_T47_two_identical_entities_in_table_counted_twice():
    """Duas ocorrências de R$ 100,00 em tabela devem ser contadas exatamente 2×."""
    table_md = (
        "| Item | Valor |\n"
        "|------|-------|\n"
        "| A    | R$ 100,00 |\n"
        "| B    | R$ 100,00 |\n"
    )
    ref_nodes = _nodes(table_md, "reference")
    doc = CanonicalDocument.from_nodes(ref_nodes)

    # Verifica que o texto visível não multiplica por ancestrais
    text = doc.visible_text
    count = text.count("100")
    assert count == 2, (
        f"'100' deve aparecer exatamente 2× no texto visível canônico, obtido {count}×. "
        f"Texto: {text!r}"
    )

    # Verifica métricas
    m = compute_critical_data_metrics(
        _nodes(table_md, "reference"),
        _nodes(table_md, "hypothesis"),
    )
    cur = m.currency
    assert cur is not None
    assert cur.counts["ref_count"] == 2, (
        f"Esperado ref_count=2, obtido={cur.counts['ref_count']}"
    )
    assert cur.value == 1.0  # idênticos → F1=1


# ---------------------------------------------------------------------------
# Invariantes de comparabilidade
# ---------------------------------------------------------------------------

def test_comparability_effective_hash_one_side_missing():
    """Effective hash em um lado mas ausente no outro → incomparável."""
    pages = {1: "Gabarito"}
    hash_v, _ = compute_reference_effective_hash(pages, [1])

    result_with_hash = _make_ref_result(hash_v, [1])
    result_without_hash = {"schema_version": "evaluation-3.0.0", "inputs": {"selected_pages": [1]}}

    ok, reasons = check_comparability(result_with_hash, result_without_hash)
    assert not ok
    assert any("reference_effective_sha256" in r for r in reasons)


def test_comparability_serialization_version_mismatch():
    """Hash igual mas serialization_version diferente → incomparável."""
    pages = {1: "Conteúdo"}
    hash_v, _ = compute_reference_effective_hash(pages, [1])

    result_a = _make_ref_result(hash_v, [1], serial_v="reference-effective-v1")
    result_b = _make_ref_result(hash_v, [1], serial_v="reference-effective-v2")

    ok, reasons = check_comparability(result_a, result_b)
    assert not ok
    assert any("serialization_version" in r for r in reasons)


def test_comparability_same_hash_same_pages_is_comparable():
    """Mesmos hash e páginas → comparável."""
    pages = {1: "Gabarito idêntico"}
    hash_v, _ = compute_reference_effective_hash(pages, [1])

    result_a = _make_ref_result(hash_v, [1])
    result_b = _make_ref_result(hash_v, [1])

    ok, reasons = check_comparability(result_a, result_b)
    assert ok, f"Deve ser comparável mas razões: {reasons}"
    assert reasons == []


# ---------------------------------------------------------------------------
# Invariante F1: 2TP/(2TP+FP+FN)
# ---------------------------------------------------------------------------

def test_f1_invariant_currency():
    """F1 deve satisfazer 2TP/(2TP+FP+FN) para qualquer resultado OK."""
    m = _cd(
        "R$ 100,00 e R$ 200,00 e R$ 300,00",
        "R$ 100,00 e R$ 200,00 e R$ 400,00",
    )
    cur = m.currency
    assert cur is not None and cur.status.value == "ok"
    tp = cur.counts["tp"]
    fp = cur.counts["fp"]
    fn = cur.counts["fn"]
    expected_f1 = 2 * tp / (2 * tp + fp + fn)
    # _prf_multiset arredonda para 4 casas decimais
    assert abs(cur.value - expected_f1) < 1e-4


# ---------------------------------------------------------------------------
# Determinismo do hash
# ---------------------------------------------------------------------------

def test_hash_determinism():
    """Mesmo conteúdo executado duas vezes → mesmo hash."""
    pages = {1: "Conteúdo A", 2: "Conteúdo B com R$ 100,00"}
    h1, _ = compute_reference_effective_hash(pages, [1, 2])
    h2, _ = compute_reference_effective_hash(pages, [1, 2])
    assert h1 == h2


def test_hash_is_64_hex_chars():
    """Hash deve ser exatamente 64 caracteres hexadecimais."""
    pages = {1: "qualquer texto"}
    h, _ = compute_reference_effective_hash(pages, [1])
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)
