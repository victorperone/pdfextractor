"""Regras de extração genéricas: nenhum código ajustado aos corpora de teste.

Os corpora de validação (V3–V11) trazem rótulos próprios ("Controle de página:
V5-P009"), nomes de arquivo e identificadores de página. A extração não pode
reconhecê-los: um documento corporativo qualquer precisa do mesmo tratamento.
Comentários e docstrings podem citar páginas como evidência; código executável
(literais de string fora de docstrings) não.
"""
from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

import pytest

from structured_pdf_text.assemble.repeated_regions import _signature_key

SRC = Path(__file__).resolve().parents[1] / "src" / "structured_pdf_text"
# O avaliador lê gabaritos e manifestos dos corpora por definição.
EVALUATION = {SRC / "evaluation"}

FORBIDDEN = re.compile(
    r"\bv\\?d?\+?\d*-p\\?d|\bV\d{1,2}-P\d|controle\\?s?\+?\s*de\\?s?\+?\s*p|document_ai|corpus_stress"
    r"|corpus/v\d|_manifesto\b|_referencia\b",
    re.IGNORECASE,
)


def _code_strings(path: Path):
    """Literais de string do arquivo, sem docstrings."""
    source = path.read_text(encoding="utf-8")
    previous = None
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.STRING:
            docstring = previous is None or previous.type in (tokenize.INDENT, tokenize.NEWLINE, tokenize.DEDENT) \
                or (previous.type == tokenize.OP and previous.string == ":")
            if not docstring:
                yield token.start[0], token.string
        if token.type not in (tokenize.NL, tokenize.COMMENT):
            previous = token


def test_extraction_code_has_no_corpus_specific_literals():
    offending = []
    for path in sorted(SRC.rglob("*.py")):
        if any(parent in path.parents for parent in EVALUATION):
            continue
        for line, literal in _code_strings(path):
            if FORBIDDEN.search(literal):
                offending.append(f"{path.relative_to(SRC)}:{line}: {literal[:80]}")
    assert not offending, "\n".join(offending)


@pytest.mark.parametrize("label", [
    "Controle de página: V5-P{n:03d}",       # rótulo dos corpora de teste
    "Ref. interna: DOC-{n:04d}/A",            # rótulo corporativo qualquer
    "Protocolo nº {n}7731 — via digital",
])
def test_per_page_identifiers_never_share_a_furniture_signature(label):
    first, second = (_signature_key("header", label.format(n=n)) for n in (9, 10))
    # Ou a linha está protegida (None), ou cada página tem a sua assinatura:
    # nunca se repete, então nunca é suprimida como cabeçalho.
    assert first is None or first != second


def test_page_counter_alone_is_still_normalized():
    assert _signature_key("footer", "Relatório anual — Página 3 de 10") == \
        _signature_key("footer", "Relatório anual — Página 4 de 10")
