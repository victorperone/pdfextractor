"""Shared semantic conservation rules for OCR refinements and fusion.

These checks are core invariants. Engine-specific ranking may decide which
hypothesis to propose, but every integration path can use the same guards.
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal
from difflib import SequenceMatcher
import re
import unicodedata


_STRUCTURAL_CRITICAL_PATTERNS = (
    ("process", re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d{1}\.\d{2}\.\d{4}\b")),
    ("cnpj", re.compile(r"\b\d{2}[.\s]?\d{3}[.\s]?\d{3}[/\s]?\d{4}[-\s]?\d{2}\b")),
    ("cpf", re.compile(r"\b\d{3}[.\s]?\d{3}[.\s]?\d{3}[-\s]?\d{2}\b")),
    ("date", re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{4}\b")),
)

_SIGNED_MONEY_PATTERN = re.compile(
    r"(?<![\w\d])r\$\s*([+-]?)\s*((?:\d{1,3}(?:\.\d{3})*)|\d+),(\d{2})(?!\d)",
    re.IGNORECASE,
)
_SIGNED_PERCENTAGE_PATTERN = re.compile(
    r"(?<![\w\d.,])([+-]?)\s*(\d{1,3})(?:([,.])(\d{1,3}))?\s*%(?!\w)"
)
_PROTECTED_NEGATION_WORDS: frozenset[str] = frozenset({
    "não", "nao", "nem", "sem", "exceto", "salvo", "jamais", "nunca",
    "menor", "maior", "diferente", "deferido", "indeferido",
    "aprovado", "reprovado", "ativo", "inativo", "válido", "invalido",
})
_PROTECTED_OPERATORS_RE = re.compile(
    r"(?:<=|>=|<>|!=|[<>]=?)|(?:(?<!\w)-(?=\s*\d))"
)


def _similarity_key(text: str) -> str:
    """Collapse whitespace and normalize text for character similarity."""
    text = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"\s", "", text)


def _extract_structural_critical_values(text: str) -> Counter[str]:
    """Extract typed identifiers and dates while retaining repetitions."""
    values: Counter[str] = Counter()
    occupied: list[tuple[int, int]] = []
    for kind, pattern in _STRUCTURAL_CRITICAL_PATTERNS:
        for match in pattern.finditer(text):
            span = match.span()
            if any(span[0] < end and start < span[1] for start, end in occupied):
                continue
            normalized = re.sub(r"[\s.,/\-]", "", match.group())
            if normalized:
                values[f"{kind}:{normalized}"] += 1
                occupied.append(span)
    return values


def _extract_signed_numeric_critical_values(text: str) -> Counter[str]:
    """Extract canonical sign-sensitive monetary and percentage values."""
    values: Counter[str] = Counter()
    for match in _SIGNED_MONEY_PATTERN.finditer(text):
        sign = "-" if match.group(1) == "-" else "+"
        integer = match.group(2).replace(".", "")
        amount = Decimal(f"{integer}.{match.group(3)}")
        values[f"money:{sign}:{format(amount, '.2f')}"] += 1
    for match in _SIGNED_PERCENTAGE_PATTERN.finditer(text):
        sign = "-" if match.group(1) == "-" else "+"
        integer = match.group(2)
        fraction = match.group(4)
        number = Decimal(f"{integer}.{fraction}" if fraction else integer)
        values[f"percentage:{sign}:{format(number.normalize(), 'f')}"] += 1
    return values


def _extract_protected_semantic_tokens(text: str) -> frozenset[str]:
    """Return negation words and comparison operators protected from loss."""
    tokens: set[str] = set()
    for word in re.split(r"\s+", text.lower()):
        clean = re.sub(r"[^\w]", "", word, flags=re.UNICODE)
        if clean in _PROTECTED_NEGATION_WORDS:
            tokens.add(clean)
    for match in _PROTECTED_OPERATORS_RE.finditer(text):
        tokens.add(match.group())
    return frozenset(tokens)


def _refinement_conserves_content(
    old_tokens,
    new_tokens,
    *,
    min_coverage: float = 0.60,
    min_similarity: float = 0.60,
) -> bool:
    """Check semantic values, protected operators, coverage, and similarity."""
    old_text = " ".join(token.text.strip() for token in old_tokens if token.text.strip())
    new_text = " ".join(token.text.strip() for token in new_tokens if token.text.strip())
    old_chars = len(re.sub(r"\s+", "", old_text))
    new_chars = len(re.sub(r"\s+", "", new_text))

    old_protected = _extract_protected_semantic_tokens(old_text)
    if old_protected - _extract_protected_semantic_tokens(new_text):
        return False
    old_critical = _extract_structural_critical_values(old_text)
    if old_critical - _extract_structural_critical_values(new_text):
        return False
    old_signed_critical = _extract_signed_numeric_critical_values(old_text)
    if old_signed_critical - _extract_signed_numeric_critical_values(new_text):
        return False

    if old_chars <= 4:
        return True
    if new_chars < old_chars * min_coverage:
        return False
    if old_chars >= 15:
        if SequenceMatcher(None, _similarity_key(old_text), _similarity_key(new_text)).ratio() < min_similarity:
            return False
    return True


def _collect_token_critical_fingerprint(tokens) -> Counter[str]:
    """Snapshot critical values from tokens for before/after drift checks."""
    text = " ".join(token.text.strip() for token in tokens if token.text.strip())
    snapshot: Counter[str] = Counter()
    snapshot.update(_extract_structural_critical_values(text))
    snapshot.update(_extract_signed_numeric_critical_values(text))
    snapshot.update(f"protected:{token}" for token in _extract_protected_semantic_tokens(text))
    return snapshot


def _emit_conservation_warning(stage, before, after, page_index, warnings_list) -> None:
    """Record critical values lost during one named processing stage."""
    for key, count in (before - after).items():
        warnings_list.append(f"conservation_drift:p{page_index}:{stage}: lost {count}x '{key}'")


def _critical_surface_preserved(original: str, proposed: str) -> bool:
    """Preserve explicit signs and leading zeroes when replacing a value."""
    if not original.strip():
        return True
    orig = re.sub(r"\s", "", original)
    prop = re.sub(r"\s", "", proposed)
    if not prop:
        return False
    orig_sign = orig[0] if orig and orig[0] in "+-" else ""
    prop_sign = prop[0] if prop and prop[0] in "+-" else ""
    if orig_sign and orig_sign != prop_sign:
        return False
    if orig.lstrip("+-") and orig.lstrip("+-")[0] == "0":
        prop_body = prop.lstrip("+-")
        if not prop_body or prop_body[0] != "0":
            return False
    return True
