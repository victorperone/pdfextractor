"""OCR accuracy metrics for the RAW benchmark layer.

Provides CER, WER, normalization, and reference-markdown parsing.
All functions are pure Python; no optional dependencies required.
"""
from __future__ import annotations

import re
import unicodedata


# ---------------------------------------------------------------------------
# Text normalization
# ---------------------------------------------------------------------------

def normalize_for_comparison(text: str) -> str:
    """Normalize text for CER/WER comparison (NFC + whitespace).

    Applies only deterministic, reversible normalization.  Does NOT remove
    punctuation, accents, leading zeros, monetary symbols or separators —
    a CPF with a wrong digit must stay wrong.
    """
    text = unicodedata.normalize("NFC", text)
    # Unify CR+LF and bare CR to LF
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Collapse runs of spaces/tabs on each line; keep newlines
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return "\n".join(line for line in lines).strip()


def strip_markdown(text: str) -> str:
    """Remove markdown formatting for plain-text comparison.

    Keeps the readable content; removes heading markers, bold/italic, table
    pipes, blockquote markers, and HTML comment delimiters.
    """
    lines_out: list[str] = []
    for line in text.split("\n"):
        # Skip HTML comment lines (used in reference.md as metadata)
        stripped = line.strip()
        if stripped.startswith("<!--") and stripped.endswith("-->"):
            continue
        if stripped.startswith("<!--"):
            continue
        # Strip heading markers (# ## ### ...)
        heading = re.match(r"^#{1,6}\s+", line)
        if heading:
            line = line[heading.end():]
        # Strip blockquote marker
        if line.startswith("> "):
            line = line[2:]
        # Table rows: extract cell content
        if "|" in line:
            cells = [c.strip() for c in line.split("|")]
            cells = [c for c in cells if c and not re.match(r"^[-:]+$", c)]
            if cells:
                line = "  ".join(cells)
            else:
                continue
        # Remove bold/italic markers (**, *, __)
        line = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", line)
        line = re.sub(r"_{1,2}([^_]+)_{1,2}", r"\1", line)
        # Remove inline code backticks
        line = re.sub(r"`([^`]+)`", r"\1", line)
        lines_out.append(line)
    return normalize_for_comparison("\n".join(lines_out))


# ---------------------------------------------------------------------------
# Edit distance (pure Python — no external deps)
# ---------------------------------------------------------------------------

def _levenshtein_chars(a: str, b: str) -> int:
    """Character-level Levenshtein distance."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    # Use two-row DP to save memory
    prev = list(range(lb + 1))
    curr = [0] * (lb + 1)
    for i in range(1, la + 1):
        curr[0] = i
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev, curr = curr, prev
    return prev[lb]


def _levenshtein_words(a_words: list[str], b_words: list[str]) -> int:
    """Word-level Levenshtein distance."""
    la, lb = len(a_words), len(b_words)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    curr = [0] * (lb + 1)
    for i in range(1, la + 1):
        curr[0] = i
        for j in range(1, lb + 1):
            cost = 0 if a_words[i - 1] == b_words[j - 1] else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev, curr = curr, prev
    return prev[lb]


# ---------------------------------------------------------------------------
# CER / WER
# ---------------------------------------------------------------------------

def cer(hypothesis: str, reference: str) -> float:
    """Character Error Rate: edit_distance(hyp, ref) / len(ref).

    Returns 0.0 for empty reference.  Can exceed 1.0 when hypothesis is
    longer than reference (insertions).
    """
    hyp = normalize_for_comparison(hypothesis)
    ref = normalize_for_comparison(reference)
    if not ref:
        return 0.0
    distance = _levenshtein_chars(hyp, ref)
    return distance / len(ref)


def wer(hypothesis: str, reference: str) -> float:
    """Word Error Rate: word-level edit_distance(hyp, ref) / len(ref_words).

    Returns 0.0 for empty reference.  Can exceed 1.0 (many insertions).
    """
    hyp_words = normalize_for_comparison(hypothesis).split()
    ref_words = normalize_for_comparison(reference).split()
    if not ref_words:
        return 0.0
    distance = _levenshtein_words(hyp_words, ref_words)
    return distance / len(ref_words)


# ---------------------------------------------------------------------------
# Reference markdown parser
# ---------------------------------------------------------------------------

_PAGE_HEADER_RE = re.compile(
    r"^##\s+P[áa]gina\s+(\d+)\s*\|",
    re.MULTILINE | re.IGNORECASE,
)


def parse_reference_pages(md_text: str) -> dict[int, str]:
    """Split a reference Markdown file into per-page plain text.

    Returns a dict mapping 1-based page number to the stripped page content.
    """
    splits = list(_PAGE_HEADER_RE.finditer(md_text))
    if not splits:
        return {}

    pages: dict[int, str] = {}
    for i, match in enumerate(splits):
        page_num = int(match.group(1))
        start = match.start()
        end = splits[i + 1].start() if i + 1 < len(splits) else len(md_text)
        page_text = md_text[start:end]
        pages[page_num] = strip_markdown(page_text)

    return pages
