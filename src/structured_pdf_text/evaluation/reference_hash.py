"""Reference identity hash for the unified evaluation pipeline (spec §10.4).

Protocol ``reference-effective-v1``
------------------------------------
The *effective* ground truth hash captures exactly what was evaluated —
the canonical representation of the selected pages' Markdown content.
It is NOT the raw-file hash (which lives in ``reference_source_sha256``).

Hash algorithm
~~~~~~~~~~~~~~
1. For each page in the selected scope (or all pages when no scope given):
   a. Normalize text: Unicode NFC + CRLF→LF normalization.
   b. Do NOT strip internal spaces, blank lines or formatting markers.
      Only whitespace-line normalisation that affects identity (CRLF) is applied.
2. Build a canonical dict::

       {
           "serialization_version": "reference-effective-v1",
           "selected_pages": [1, 2, ...],   # sorted ascending integers
           "pages": [
               {"page": 1, "markdown": "<NFC+LF content>"},
               ...
           ]
       }

3. Serialize with ``json.dumps(..., ensure_ascii=False, sort_keys=True,
   separators=(',', ':'))`` — deterministic, no decorative whitespace.
4. Encode to UTF-8, compute SHA-256, return lowercase hex.

Notes
~~~~~
- Normalisation is **identity-only** (NFC + LF): does not remove punctuation,
  accents, Markdown syntax, blank lines or internal spaces.
- Empty-page Markdown is represented as ``""`` — pages do not disappear.
- Page numbers outside the selected scope do not influence the hash.
- Source-file hash (SHA-256 of raw bytes) is recorded separately as
  ``reference_source_sha256`` for provenance, never substituted.
- Hash is deterministic across platforms (no locale, timestamp, or path
  dependency).
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Sequence


# Public protocol identifier — increment when algorithm changes.
REFERENCE_HASH_PROTOCOL = "reference-effective-v1"

# Page-header pattern (matches ## Página N variants produced by the extractor)
_PAGE_HDR_RE = re.compile(
    r"^##\s+P[áa]gina\s+0*(\d+)[^\n]*$",
    re.IGNORECASE | re.MULTILINE,
)


def _normalize_text(text: str) -> str:
    """Apply identity normalisation: NFC + CRLF/CR → LF.

    Does NOT strip, lower-case, remove Markdown markers or collapse blank lines.
    """
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def split_pages(text: str) -> dict[int, str]:
    """Split Markdown text by ``## Página N`` markers.

    Returns ``{page_number: body_markdown}`` where body is the text between
    page markers (the marker line itself is excluded).

    If no markers are found, the entire text is treated as page 1.
    Empty bodies ARE preserved (page present but blank).

    Raises ``ValueError`` on duplicate page numbers.
    """
    parts = _PAGE_HDR_RE.split(text)
    pages: dict[int, str] = {}

    if len(parts) == 1:
        # No markers: single implicit page
        return {1: text}

    # parts[0] is the text before the first marker (usually empty — ignore)
    i = 1
    while i + 1 < len(parts):
        page_num = int(parts[i])
        body = parts[i + 1]
        # Normalize line endings but preserve internal content
        body = _normalize_text(body)
        # Remove only the leading/trailing newlines produced by the split
        # (the marker line ends with \n, so body starts with \n)
        body = body.lstrip("\n").rstrip("\n")
        if page_num in pages:
            raise ValueError(
                f"Duplicate page number {page_num} in reference Markdown"
            )
        pages[page_num] = body
        i += 2
    return pages


def compute_reference_effective_hash(
    pages: dict[int, str],
    selected_pages: Sequence[int] | None = None,
) -> tuple[str, dict]:
    """Compute the reference effective hash for the given pages and scope.

    Parameters
    ----------
    pages          : ``{page_number: markdown_body}`` mapping (from split_pages
                     or from a manifest's ``expected_markdown`` values).
    selected_pages : Pages included in this evaluation scope.  When ``None``,
                     all pages in *pages* are used.

    Returns
    -------
    (hex_digest, metadata) where metadata contains the fields that must be
    stored alongside the hash for future comparability verification:
      - ``reference_effective_sha256``  : 64-char hex
      - ``reference_serialization_version`` : protocol string
      - ``selected_pages``              : sorted list of ints
      - ``total_pages_in_reference``    : int

    Raises
    ------
    ValueError
        When selected_pages references a page absent from *pages* (a selected
        page without a reference entry is treated as empty, not an error —
        the caller decides scope policy).
    """
    if selected_pages is None:
        scope = sorted(pages.keys())
    else:
        scope = sorted(set(int(p) for p in selected_pages))

    # Build canonical page list (selected pages only; absent → empty string)
    canonical_pages = []
    for pnum in scope:
        body = pages.get(pnum, "")
        # Normalize: NFC + CRLF→LF + strip trailing newlines only
        # (trailing \n at page boundary is a delimiter artefact, not content).
        # Internal blank lines and spacing are preserved.
        normalized = _normalize_text(body).rstrip("\n")
        canonical_pages.append({
            "page": pnum,
            "markdown": normalized,
        })

    canonical = {
        "serialization_version": REFERENCE_HASH_PROTOCOL,
        "selected_pages": scope,
        "pages": canonical_pages,
    }

    # Deterministic JSON: sorted keys, no decorative whitespace, UTF-8
    serialized = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    digest = hashlib.sha256(serialized).hexdigest()

    metadata = {
        "reference_effective_sha256": digest,
        "reference_serialization_version": REFERENCE_HASH_PROTOCOL,
        "selected_pages": scope,
        "total_pages_in_reference": len(pages),
    }
    return digest, metadata


def compute_reference_source_hash(path: Path) -> str | None:
    """SHA-256 of the raw bytes of the reference file (provenance only).

    Returns None if the file does not exist.
    """
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
