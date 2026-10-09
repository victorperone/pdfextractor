"""Human-readable one-line-per-page extraction report and per-block metrics.

Formats a ``StructuredDocument`` as a compact multi-line string where the
first line contains document-level summary facts and each subsequent line
contains per-page strategy, reason codes, and key diagnostic values. Intended
for CLI output and log emission; not a structured format.

VQ-27: ``block_metrics`` compares extracted content blocks against a manifest
dict and produces per-block labels (present/modified/missing/duplicated/reordered).
"""
from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from structured_pdf_text.document import StructuredDocument


# VQ-27 — per-block label values
BLOCK_PRESENT = "present"
BLOCK_MODIFIED = "modified"
BLOCK_MISSING = "missing"
BLOCK_DUPLICATED = "duplicated"
BLOCK_REORDERED = "reordered"


def block_metrics(
    document: StructuredDocument,
    manifest: dict[str, Any],
    *,
    similarity_threshold: float = 0.80,
) -> dict[str, Any]:
    """Compare extracted blocks against a manifest and return per-block labels.

    VQ-27: for each block in the manifest, check whether the content was
    extracted (present), partially extracted (modified), not found (missing),
    extracted more than once (duplicated), or found out of order (reordered).

    Args:
        document: the extracted ``StructuredDocument``.
        manifest: dict mapping block IDs to dicts with keys:
            ``page`` (0-based int), ``expected_markdown`` (str),
            ``expected_reading_order`` (int, optional).
        similarity_threshold: SequenceMatcher ratio required for "present"
            (vs "modified" when below threshold but content is found).

    Returns:
        A dict with:
        - ``by_block``: {block_id: {"label": ..., "similarity": float|None}}
        - ``summary``: counts per label
        - ``gates``: {label: count} for CI gate checking
    """
    # Build a flat list of (page_index, order_index, text) from the document
    extracted: list[tuple[int, int, str]] = []
    for page in document.pages:
        for block in sorted(page.content_blocks, key=lambda b: b.order_index):
            if not block.suppressed and block.text.strip():
                extracted.append((page.page_index, block.order_index, block.text.strip()))

    results: dict[str, dict[str, Any]] = {}
    seen_texts: dict[str, list[int]] = {}  # text → positions in extracted list

    for idx, (_, _, text) in enumerate(extracted):
        key = text[:80]
        seen_texts.setdefault(key, []).append(idx)

    for block_id, block_spec in manifest.items():
        expected_text = (block_spec.get("expected_markdown") or "").strip()
        expected_page = block_spec.get("page")
        expected_order = block_spec.get("expected_reading_order")

        if not expected_text:
            results[block_id] = {"label": BLOCK_MISSING, "similarity": None}
            continue

        best_ratio = 0.0
        best_idx: int | None = None
        for idx, (page_idx, order_idx, text) in enumerate(extracted):
            ratio = SequenceMatcher(None, expected_text, text).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_idx = idx

        if best_ratio < 0.30:
            results[block_id] = {"label": BLOCK_MISSING, "similarity": best_ratio}
            continue

        # Check for duplication (same text found multiple times)
        key = expected_text[:80]
        duplicate_positions = seen_texts.get(key, [])
        if len(duplicate_positions) > 1:
            label = BLOCK_DUPLICATED
        elif best_ratio >= similarity_threshold:
            label = BLOCK_PRESENT
        else:
            label = BLOCK_MODIFIED

        # Check reordering when expected_reading_order is provided
        if label == BLOCK_PRESENT and expected_order is not None and best_idx is not None:
            actual_order = extracted[best_idx][1]  # order_index from extracted
            # Reordered if the actual index in the extracted list differs from expected
            position_in_extracted = best_idx
            if abs(position_in_extracted - expected_order) > 2:
                label = BLOCK_REORDERED

        results[block_id] = {"label": label, "similarity": best_ratio}

    summary: dict[str, int] = {
        BLOCK_PRESENT: 0,
        BLOCK_MODIFIED: 0,
        BLOCK_MISSING: 0,
        BLOCK_DUPLICATED: 0,
        BLOCK_REORDERED: 0,
    }
    for block_result in results.values():
        lbl = block_result["label"]
        if lbl in summary:
            summary[lbl] += 1

    return {"by_block": results, "summary": summary, "gates": summary}


def document_report(document: StructuredDocument) -> str:
    """Format a ``StructuredDocument`` as a human-readable diagnostic report.

    Produces one summary line with document-level facts (status, page counts,
    timing, repeated regions) followed by one line per page showing strategy,
    reason codes, native character count, text length, coverage scores, layout
    decisions, and per-page timing. All values are read directly from the
    document's diagnostic structures with no recomputation.
    """
    lines = [
        f"status={document.diagnostics.status.value}",
        f"pages={document.diagnostics.page_count}",
        f"native_pages={document.diagnostics.native_pages}",
        f"mixed_pages={document.diagnostics.mixed_pages}",
        f"ocr_pages={document.diagnostics.ocr_pages}",
        f"total_ms={document.diagnostics.facts.get('total_ms', '?')}",
        f"assemble_ms={document.diagnostics.facts.get('assemble_ms', '?')}",
        f"memory={document.diagnostics.facts.get('memory', {})}",
        f"repeated_regions={len(document.diagnostics.facts.get('repeated_headers_footers', {}))}",
    ]
    for page in document.pages:
        reasons = ",".join(reason.value for reason in page.diagnostics.reasons) or "none"
        lines.append(
            "page="
            f"{page.page_index + 1} strategy={page.diagnostics.strategy.value} "
                f"reasons={reasons} native_chars={page.diagnostics.native_chars} "
                f"text_len={page.diagnostics.native_text_length} "
            f"native_score={page.diagnostics.facts.get('native_text_score', '?')} "
            f"text_coverage={page.diagnostics.facts.get('text_coverage', '?')} "
            f"image_coverage={page.diagnostics.facts.get('image_coverage', '?')} "
            f"tables={page.diagnostics.tables} "
            f"flow_mode={page.diagnostics.facts.get('reading_flow_mode', '?')} "
            f"lanes={page.diagnostics.facts.get('reading_lane_count', '?')} "
            f"gutters={page.diagnostics.facts.get('reading_gutter_count', '?')} "
            f"table_geometry_valid={page.diagnostics.facts.get('table_geometry_valid', '?')} "
            f"table_token_coverage={page.diagnostics.facts.get('table_token_coverage', '?')} "
            f"table_provenance={page.diagnostics.facts.get('table_source_provenance', '?')} "
            f"ocr_outcome={page.diagnostics.facts.get('ocr_outcome', 'not_requested')} "
            f"page_ms={page.diagnostics.processing_time_ms if page.diagnostics.processing_time_ms is not None else '?'}"
        )
    return "\n".join(lines)
