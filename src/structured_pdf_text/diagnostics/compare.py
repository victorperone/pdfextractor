"""Side-by-side extraction comparison between registered adapters.

Provides a small adapter registry (``structured-native``, ``structured-balanced``,
``pdfium-raw``, ``pymupdf``) and a ``compare_extractors`` entry point that runs
each requested adapter and reports pairwise fidelity signals relative to a chosen
reference. No single winner score is produced — the metrics are observational
until the reference is verified ground truth.
"""
from __future__ import annotations

import importlib.util
import re
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Protocol

from structured_pdf_text.api import PdfTextExtractor
from structured_pdf_text.config import ExtractionMode, ExtractorConfig
from structured_pdf_text.native.pdfium_source import PdfiumNativeEvidenceSource


@dataclass(frozen=True, slots=True)
class ComparisonExtraction:
    """Immutable result record produced by a single ``ComparisonAdapter`` run.

    Carries the adapter name, extraction status, page count, both text views,
    elapsed time, optional adapter-specific metadata, and an error string when
    the extraction failed.
    """

    adapter: str
    status: str
    page_count: int
    raw_text: str
    reading_text: str
    elapsed_ms: float
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class ComparisonAdapter(Protocol):
    """Protocol satisfied by every extraction backend registered for comparison.

    Concrete implementations must expose ``name``, ``available()``, and
    ``extract(path)`` so that ``compare_extractors`` can invoke them uniformly
    without importing optional dependencies eagerly.
    """

    name: str

    def available(self) -> bool:
        """Return whether the optional extractor runtime is importable."""

    def extract(self, path: str | Path) -> ComparisonExtraction:
        """Extract comparable raw and reading-order text views."""


class StructuredPdfTextAdapter:
    """Adapter that wraps ``PdfTextExtractor`` for extraction comparison.

    Supports all ``ExtractionMode`` values. The adapter name encodes the mode
    so that multiple instances (e.g. NATIVE and BALANCED) can coexist in the
    same comparison run.
    """

    def __init__(self, mode: ExtractionMode | str = ExtractionMode.NATIVE, language: str = "pt") -> None:
        self.mode = ExtractionMode(mode)
        self.language = language
        self.name = f"structured-{self.mode.value}"

    def available(self) -> bool:
        return True

    def extract(self, path: str | Path) -> ComparisonExtraction:
        started = time.perf_counter()
        document = PdfTextExtractor(
            ExtractorConfig(mode=self.mode, language=self.language)
        ).extract(path)
        return ComparisonExtraction(
            adapter=self.name,
            status=document.diagnostics.status.value,
            page_count=document.diagnostics.page_count,
            raw_text=document.raw_text,
            reading_text=document.reading_text,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            metadata={
                "tables": len(document.tables),
                "warnings": list(document.diagnostics.warnings),
                "pdfium_version": document.metadata.pdfium_version,
            },
        )


class PdfiumRawAdapter:
    """Expose PDFium's direct text stream before our reconstruction stages."""

    name = "pdfium-raw"

    def available(self) -> bool:
        return True

    def extract(self, path: str | Path) -> ComparisonExtraction:
        started = time.perf_counter()
        with PdfiumNativeEvidenceSource(path) as source:
            context = source.open()
            pages = [source.extract_page(index) for index in range(context.page_count)]
        text = "\n\n".join(page.extracted_text.strip() for page in pages if page.extracted_text.strip())
        return ComparisonExtraction(
            adapter=self.name,
            status="success",
            page_count=context.page_count,
            raw_text=text,
            reading_text=text,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            metadata={"pdfium_version": context.pdfium_version},
        )


class PyMuPdfAdapter:
    """Adapter that wraps PyMuPDF (``pymupdf`` / ``fitz``) for extraction comparison.

    Imports the library lazily inside ``extract`` so that the adapter remains
    instantiable even when PyMuPDF is not installed. ``available()`` returns
    ``False`` in that case and the adapter is skipped by ``compare_extractors``.
    """

    name = "pymupdf"

    def available(self) -> bool:
        return importlib.util.find_spec("pymupdf") is not None or importlib.util.find_spec("fitz") is not None

    def extract(self, path: str | Path) -> ComparisonExtraction:
        started = time.perf_counter()
        try:
            try:
                import pymupdf as fitz
            except ImportError:
                import fitz  # type: ignore[no-redef]

            document = fitz.open(str(path))
            try:
                raw_pages = [page.get_text("text") for page in document]
                reading_pages = [page.get_text("text", sort=True) for page in document]
                word_count = sum(len(page.get_text("words")) for page in document)
                page_count = len(document)
            finally:
                document.close()
            return ComparisonExtraction(
                adapter=self.name,
                status="success",
                page_count=page_count,
                raw_text="\n\n".join(page.strip() for page in raw_pages if page.strip()),
                reading_text="\n\n".join(page.strip() for page in reading_pages if page.strip()),
                elapsed_ms=(time.perf_counter() - started) * 1000,
                metadata={"word_boxes": word_count},
            )
        except Exception as exc:
            return ComparisonExtraction(
                adapter=self.name,
                status="failure",
                page_count=0,
                raw_text="",
                reading_text="",
                elapsed_ms=(time.perf_counter() - started) * 1000,
                error=f"{type(exc).__name__}: {exc}",
            )


def comparison_adapters(language: str = "pt") -> dict[str, ComparisonAdapter]:
    """Return built-in adapters without importing optional runtimes eagerly."""
    adapters: list[ComparisonAdapter] = [
        StructuredPdfTextAdapter(ExtractionMode.NATIVE, language),
        StructuredPdfTextAdapter(ExtractionMode.BALANCED, language),
        PdfiumRawAdapter(),
        PyMuPdfAdapter(),
    ]
    return {adapter.name: adapter for adapter in adapters}


def compare_extractors(
    path: str | Path,
    adapter_names: tuple[str, ...] | list[str] = ("structured-native", "pymupdf"),
    *,
    reference: str = "pymupdf",
    language: str = "pt",
    include_text: bool = False,
) -> dict[str, Any]:
    """Run local adapters and report separate fidelity signals.

    The report deliberately avoids a single winner score. Pairwise metrics are
    observational and only become accuracy metrics when the chosen reference
    is manually verified ground truth.
    """
    registry = comparison_adapters(language)
    requested = tuple(dict.fromkeys(adapter_names))
    unknown = [name for name in requested if name not in registry]
    if unknown:
        raise ValueError(f"Unknown comparison adapters: {', '.join(unknown)}")

    extractions: list[ComparisonExtraction] = []
    for name in requested:
        adapter = registry[name]
        if not adapter.available():
            extractions.append(
                ComparisonExtraction(
                    adapter=name,
                    status="unavailable",
                    page_count=0,
                    raw_text="",
                    reading_text="",
                    elapsed_ms=0.0,
                    error="optional runtime is not installed",
                )
            )
            continue
        try:
            extractions.append(adapter.extract(path))
        except Exception as exc:
            extractions.append(
                ComparisonExtraction(
                    adapter=name,
                    status="failure",
                    page_count=0,
                    raw_text="",
                    reading_text="",
                    elapsed_ms=0.0,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )

    successful = {item.adapter: item for item in extractions if item.status == "success"}
    reference_name = reference if reference in successful else next(iter(successful), None)
    reference_output = successful.get(reference_name) if reference_name else None
    results = [
        _extraction_entry(item, reference_output, include_text=include_text)
        for item in extractions
    ]
    return {
        "path": str(Path(path)),
        "reference": reference_name,
        "warning": (
            "comparison reference is observational, not ground truth"
            if reference_name is not None
            else "no comparison adapter completed successfully"
        ),
        "results": results,
    }


def _extraction_entry(
    extraction: ComparisonExtraction,
    reference: ComparisonExtraction | None,
    *,
    include_text: bool,
) -> dict[str, Any]:
    reading = extraction.reading_text
    entry: dict[str, Any] = {
        "adapter": extraction.adapter,
        "status": extraction.status,
        "pages": extraction.page_count,
        "elapsed_ms": round(extraction.elapsed_ms, 3),
        "raw_chars": len(extraction.raw_text),
        "reading_chars": len(reading),
        "reading_words": len(_words(reading)),
        "accented_words": len(_accented_words(reading)),
        "duplicate_line_ratio": round(_duplicate_line_ratio(reading), 6),
        "metadata": extraction.metadata,
        "error": extraction.error,
    }
    if reference is not None and extraction.status == "success":
        entry["relative_to_reference"] = _pairwise_metrics(
            reference.reading_text,
            reading,
        )
    if include_text:
        entry["raw_text"] = extraction.raw_text
        entry["reading_text"] = reading
    return entry


def _pairwise_metrics(reference: str, candidate: str) -> dict[str, float]:
    """Compute fidelity signals between a reference and a candidate text view.

    Returns character-level similarity, word recall, word precision, and
    accented-word recall. All values are rounded to six decimal places. These
    are observational metrics; they become accuracy measures only when the
    reference is verified ground truth.
    """
    normalized_reference = _normalize(reference)
    normalized_candidate = _normalize(candidate)
    reference_words = Counter(_words(normalized_reference))
    candidate_words = Counter(_words(normalized_candidate))
    common_words = sum((reference_words & candidate_words).values())
    accent_reference = Counter(_accented_words(reference))
    accent_candidate = Counter(_accented_words(candidate))
    common_accents = sum((accent_reference & accent_candidate).values())
    return {
        "normalized_character_similarity": round(
            SequenceMatcher(None, normalized_reference, normalized_candidate).ratio(),
            6,
        ),
        "normalized_word_recall": round(
            common_words / max(sum(reference_words.values()), 1),
            6,
        ),
        "normalized_word_precision": round(
            common_words / max(sum(candidate_words.values()), 1),
            6,
        ),
        "accented_word_recall": round(
            common_accents / max(sum(accent_reference.values()), 1),
            6,
        ),
    }


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def _words(value: str) -> list[str]:
    return re.findall(r"[\wÀ-ÖØ-öø-ÿ]+(?:[-'][\wÀ-ÖØ-öø-ÿ]+)*", value.casefold())


def _accented_words(value: str) -> list[str]:
    return [
        word.casefold()
        for word in _words(value)
        if any(unicodedata.combining(character) for character in unicodedata.normalize("NFD", word))
    ]


def _duplicate_line_ratio(value: str) -> float:
    lines = [" ".join(line.casefold().split()) for line in value.splitlines() if line.strip()]
    if not lines:
        return 0.0
    return (len(lines) - len(set(lines))) / len(lines)
