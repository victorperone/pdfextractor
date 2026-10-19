"""Validated Markdown inputs shared by the evaluation entry points."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .reference_hash import split_pages
from .markdown_syntax import page_markers


def page_number(value: Any) -> int:
    """Accept positive integer IDs, including decimal IDs from JSON keys."""
    if isinstance(value, bool) or not (
        isinstance(value, int) or isinstance(value, str) and value.isdecimal()
    ):
        raise ValueError(f"Invalid page number: {value!r}")
    number = int(value)
    if number < 1:
        raise ValueError(f"Invalid page number: {value!r}")
    return number


def document_pages(text: str) -> dict[int, str]:
    """Split without swallowing duplicate markers or changing implicit pages."""
    pages = split_pages(text)
    for number in pages:
        page_number(number)
    return pages


def manifest_pages(manifest: dict[str, Any]) -> dict[int, str]:
    """Require actual ground truth; an explicitly empty string is a valid page."""
    entries = manifest.get("pages")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Reference manifesto must contain a nonempty pages list")
    pages: dict[int, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Reference manifesto pages must be objects")
        number = page_number(entry.get("page", entry.get("page_num")))
        if "page_num" in entry and page_number(entry["page_num"]) != number:
            raise ValueError(f"Conflicting page IDs for manifesto page {number}")
        if number in pages:
            raise ValueError(f"Duplicate page number {number} in reference manifesto")
        if "expected_markdown" not in entry or not isinstance(entry["expected_markdown"], str):
            raise ValueError(
                f"Page {number} requires string expected_markdown; provide --reference "
                "for a metadata-only manifesto"
            )
        content = entry["expected_markdown"]
        # A field may contain either its body or its own page marker, never
        # another page or a multi-page document. parse_document is not used here.
        bodies = document_pages(content)
        if page_markers(content):
            if set(bodies) != {number}:
                raise ValueError(f"Page marker does not match manifesto page {number}")
            content = bodies[number]
        pages[number] = content
    return pages


def pages_markdown(pages: dict[int, str]) -> str:
    """Serialize page bodies with exactly one delimiter per page."""
    return "\n\n".join(f"## Página {number}\n\n{body}" for number, body in pages.items())


def load_reference_text(reference: Path | None, manifesto: Path | None) -> str:
    """Explicit Markdown takes precedence over a metadata-only manifesto."""
    if reference is not None:
        return reference.read_text(encoding="utf-8")
    if manifesto is None:
        raise ValueError("Provide --reference or --manifesto")
    data = json.loads(manifesto.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Reference manifesto must be a JSON object")
    return pages_markdown(manifest_pages(data))


def selected_pages_from_run(metadata: dict[str, Any]) -> list[int] | None:
    """Explicit scope takes precedence over older manifests' per-page entries."""
    if "selected_pages" in metadata:
        selected = metadata["selected_pages"]
        if not isinstance(selected, list):
            raise ValueError("selected_pages must be a list")
    elif "pages" in metadata:
        entries = metadata["pages"]
        if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
            raise ValueError("Run manifest pages must be a list of objects")
        selected = [e.get("page") for e in entries]
    else:
        return None
    if not selected:
        raise ValueError("Selected page scope cannot be empty")
    return sorted({page_number(number) for number in selected})


def load_run_metadata(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Run manifest must be a JSON object")
    selected_pages_from_run(data)
    return data
