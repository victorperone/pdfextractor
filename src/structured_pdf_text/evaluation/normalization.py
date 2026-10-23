"""Normalisation profiles for the V2 evaluation pipeline (spec §2.6).

All profiles are pure functions — they never modify source files or the
original strings passed in.  The original text is always preserved; only
the comparison view is transformed.

Profiles
--------
raw              NFC + CRLF→LF only.  No collapsing, no stripping.
unicode_layout   raw + collapse intra-line repeated spaces + strip trailing WS.
visible_text     Extract readable content from Markdown (headings, cells, etc.).

The profile applied to any metric is recorded in MetricResult.normalization_profile
so that comparisons across runs are always traceable.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from structured_pdf_text.evaluation.models import NormProfile


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _crlf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _collapse_spaces(text: str) -> str:
    """Collapse runs of horizontal whitespace within each line."""
    lines = [" ".join(ln.split()) for ln in text.split("\n")]
    return "\n".join(lines)


# Patterns used by the visible_text profile
_HEADING_RE   = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_EMPHASIS_RE  = re.compile(r"\*{1,2}([^*\n]+)\*{1,2}|_{1,2}([^_\n]+)_{1,2}")
_CODE_INLINE  = re.compile(r"`([^`\n]+)`")
_LINK_RE      = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_IMG_RE       = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_COMMENT_RE   = re.compile(r"<!--.*?-->", re.DOTALL)
_BLOCKQUOTE   = re.compile(r"^>\s?", re.MULTILINE)
_PAGE_HDR_RE  = re.compile(r"^##\s+P[áa]gina\s+[^\n]*\n?", re.IGNORECASE | re.MULTILINE)
_FENCE_RE     = re.compile(r"^```[^\n]*\n(.*?)^```", re.MULTILINE | re.DOTALL)


def _visible_text(text: str) -> str:
    """Extract human-readable text from Markdown, keeping values/numbers intact."""
    # Remove HTML comments
    text = _COMMENT_RE.sub("", text)
    # Remove page-section markers (not semantic headings)
    text = _PAGE_HDR_RE.sub("", text)
    # Expand images to empty
    text = _IMG_RE.sub("", text)
    # Expand links to their label
    text = _LINK_RE.sub(r"\1", text)
    # Strip emphasis markers, keep text
    text = _EMPHASIS_RE.sub(lambda m: m.group(1) or m.group(2), text)
    # Strip inline code markers
    text = _CODE_INLINE.sub(r"\1", text)
    # Strip blockquote markers
    text = _BLOCKQUOTE.sub("", text)
    # Strip heading markers
    text = _HEADING_RE.sub("", text)
    # Extract text from table rows: "| A | B |" → "A  B"
    lines: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped[1:-1].split("|")]
            cells = [c for c in cells if c and not re.match(r"^[-: ]+$", c)]
            if cells:
                lines.append("  ".join(cells))
        else:
            lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize(text: str, profile: NormProfile = NormProfile.UNICODE_LAYOUT) -> str:
    """Apply the requested normalisation profile to *text*.

    Parameters
    ----------
    text     : Input Markdown or plain text string.
    profile  : Which normalisation to apply.

    Returns
    -------
    Normalised string.  The original *text* is never modified in place.

    Notes
    -----
    Profiles are additive: each profile includes the transforms of the
    profile to its left in the enum definition order. Typed entity
    normalisation is handled by the critical_data module.
    """
    profile = NormProfile(profile)
    # Node texts repeat across metrics; whole documents are not retained.
    if len(text) <= _CACHED_TEXT_LENGTH:
        return _cached_normalize(text, profile)
    return _normalize(text, profile)


_CACHED_TEXT_LENGTH = 4096


def _normalize(text: str, profile: NormProfile) -> str:
    if profile == NormProfile.RAW:
        return _nfc(_crlf(text))

    if profile == NormProfile.UNICODE_LAYOUT:
        return _collapse_spaces(_nfc(_crlf(text))).strip()

    if profile == NormProfile.VISIBLE_TEXT:
        base = _collapse_spaces(_nfc(_crlf(text))).strip()
        return _collapse_spaces(_visible_text(base)).strip()

    return _nfc(_crlf(text))


_cached_normalize = lru_cache(maxsize=1 << 16)(_normalize)
