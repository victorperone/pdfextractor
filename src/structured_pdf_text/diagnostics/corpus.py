"""Operational corpus-level diagnostics for batch extraction runs.

Extracts every PDF in a corpus and aggregates per-document and per-page
diagnostics into a report structure. The output is deliberately not a quality
benchmark — without annotated ground truth, counts cannot prove correctness.
It is an operational record intended for manual review, regression tracking,
and identifying pages where OCR, rotation handling, or table recovery was
invoked.
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import multiprocessing as mp
from pathlib import Path
from typing import Iterable

from structured_pdf_text.api import PdfTextExtractor
from structured_pdf_text.config import ExtractorConfig
from structured_pdf_text.document import StructuredDocument


def corpus_report(
    paths: Iterable[str | Path],
    config: ExtractorConfig | None = None,
    workers: int = 1,
) -> dict[str, object]:
    """Extract each corpus PDF and return comparable operational diagnostics.

    This is intentionally not a quality benchmark: without annotated ground
    truth, counts cannot prove that a token is correct. It records the real
    extraction run so manual review can focus on pages where recovery was
    escalated, rotated, ambiguous or expensive.
    """
    normalized_config = config or ExtractorConfig()
    normalized_paths = [Path(path) for path in paths]
    if workers <= 1 or len(normalized_paths) <= 1:
        extractor = PdfTextExtractor(normalized_config)
        documents = [
            _document_entry(extractor.extract(path))
            for path in normalized_paths
        ]
    else:
        # ``spawn`` avoids inheriting a loaded Paddle runtime/model. Each
        # process owns one extractor; callers should keep OCR worker counts
        # conservative because models are intentionally not shared.
        context = mp.get_context("spawn")
        with ProcessPoolExecutor(max_workers=workers, mp_context=context) as pool:
            documents = list(
                pool.map(
                    _extract_document_entry,
                    [(str(path), normalized_config) for path in normalized_paths],
                )
            )

    return {
        "mode": normalized_config.normalized_mode().value,
        "workers": max(1, workers),
        "documents": documents,
        "totals": _totals(documents),
    }


def _extract_document_entry(argument: tuple[str, ExtractorConfig]) -> dict[str, object]:
    path, config = argument
    return _document_entry(PdfTextExtractor(config).extract(path))


def _document_entry(document: StructuredDocument) -> dict[str, object]:
    """Build a serialisable diagnostics entry for one extracted document.

    Aggregates strategy and reason counters, region kind counts, table method
    counts, processing-time percentiles by strategy, OCR page lists, and the
    full per-page diagnostic records.
    """
    strategy_counts = Counter(page.diagnostics.strategy.value for page in document.pages)
    reason_counts = Counter(
        reason.value
        for page in document.pages
        for reason in page.diagnostics.reasons
    )
    region_counts = Counter(
        region.kind.value
        for page in document.pages
        for region in page.regions
    )
    table_method_counts = Counter(
        table.method.value
        for table in document.tables
    )
    page_times = [
        page.diagnostics.processing_time_ms
        for page in document.pages
        if page.diagnostics.processing_time_ms is not None
    ]
    strategy_times: dict[str, list[float]] = {}
    for page in document.pages:
        if page.diagnostics.processing_time_ms is None:
            continue
        strategy_times.setdefault(page.diagnostics.strategy.value, []).append(
            page.diagnostics.processing_time_ms
        )

    return {
        "path": document.metadata.source_path,
        "pdfium_version": document.metadata.pdfium_version,
        "status": document.diagnostics.status.value,
        "pages": document.diagnostics.page_count,
        "processed_page_boundaries": document.diagnostics.facts.get("page_boundaries", []),
        "raw_chars": len(document.raw_text),
        "reading_chars": len(document.reading_text),
        "logical_tables": len(document.tables),
        "strategy_counts": dict(sorted(strategy_counts.items())),
        "reason_counts": dict(sorted(reason_counts.items())),
        "region_counts": dict(sorted(region_counts.items())),
        "table_method_counts": dict(sorted(table_method_counts.items())),
        "warnings": list(document.diagnostics.warnings),
        "total_ms": document.diagnostics.facts.get("total_ms"),
        "open_pdf_ms": document.diagnostics.facts.get("open_pdf_ms"),
        "assemble_ms": document.diagnostics.facts.get("assemble_ms"),
        "cross_page_table_ms": document.diagnostics.facts.get("cross_page_table_ms"),
        "max_page_ms": max(page_times, default=None),
        "page_latency_percentiles_ms": {
            "all": _percentiles(page_times),
            "by_strategy": {
                strategy: _percentiles(values)
                for strategy, values in sorted(strategy_times.items())
            },
        },
        "memory": document.diagnostics.facts.get("memory", {}),
        "native_source_calls": document.diagnostics.facts.get("native_source_calls", {}),
        "pages_with_ocr": [
            page.page_index + 1
            for page in document.pages
            if page.diagnostics.facts.get("ocr_requested")
        ],
        "pages_with_rotated_ocr": [
            page.page_index + 1
            for page in document.pages
            if any(rotation for rotation in page.diagnostics.facts.get("ocr_rotations", []))
        ],
        "pages_with_visual_tables": [
            page.page_index + 1
            for page in document.pages
            if "visual_model" in page.diagnostics.facts.get("table_methods", [])
        ],
        "page_diagnostics": [
            {
                "page": page.page_index + 1,
                "strategy": page.diagnostics.strategy.value,
                "reasons": [reason.value for reason in page.diagnostics.reasons],
                "tables": page.diagnostics.tables,
                "ocr_tokens_added": page.diagnostics.ocr_tokens_added,
                "ocr_passes": page.diagnostics.facts.get("ocr_passes"),
                "ocr_batches": page.diagnostics.facts.get("ocr_batches"),
                "ocr_figure_tokens": page.diagnostics.facts.get("ocr_figure_tokens", 0),
                "ocr_rotations": page.diagnostics.facts.get("ocr_rotations", []),
                "ocr_outcome": page.diagnostics.facts.get("ocr_outcome", "not_requested"),
                "ocr_degraded": page.diagnostics.facts.get("ocr_degraded", False),
                "ocr_degraded_reasons": page.diagnostics.facts.get("ocr_degraded_reasons", []),
                "page_ms": page.diagnostics.processing_time_ms,
                "memory": page.diagnostics.facts.get("memory", {}),
                "native_source_calls": page.diagnostics.facts.get("native_source_calls", {}),
                "warnings": list(page.diagnostics.warnings),
            }
            for page in document.pages
        ],
    }


def _totals(documents: list[dict[str, object]]) -> dict[str, object]:
    strategy_counts = Counter()
    reason_counts = Counter()
    for document in documents:
        strategy_counts.update(document["strategy_counts"])
        reason_counts.update(document["reason_counts"])
    peak_values = [
        int(memory.get("peak_rss_bytes", 0))
        for document in documents
        if isinstance((memory := document.get("memory")), dict)
    ]
    return {
        "documents": len(documents),
        "pages": sum(int(document["pages"]) for document in documents),
        "logical_tables": sum(int(document["logical_tables"]) for document in documents),
        "warnings": sum(len(document["warnings"]) for document in documents),
        "strategy_counts": dict(sorted(strategy_counts.items())),
        "reason_counts": dict(sorted(reason_counts.items())),
        "max_worker_peak_rss_bytes": max(peak_values, default=0),
    }


def _percentiles(values: list[float]) -> dict[str, float | None]:
    ordered = sorted(float(value) for value in values)
    return {
        "p50": _percentile(ordered, 0.50),
        "p95": _percentile(ordered, 0.95),
        "p99": _percentile(ordered, 0.99),
    }


def _percentile(ordered: list[float], quantile: float) -> float | None:
    if not ordered:
        return None
    if len(ordered) == 1:
        return round(ordered[0], 6)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    value = ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction
    return round(value, 6)
