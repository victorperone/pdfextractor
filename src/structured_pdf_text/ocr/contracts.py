"""Canonical OCR contract types shared by all backends.

Defines the protocols and dataclasses that every OCR backend must satisfy.
The rest of the pipeline (recovery, fusion, tables, assembly) continues to
consume ``document.OcrToken`` via the existing ``OcrEngine`` protocol.  These
types serve the benchmarking/comparison layer and the factory abstraction.

Do NOT modify ``engine.py`` or ``recovery.py``.  New backends implement both
``OcrEngine`` (structurally, without explicit inheritance) and ``OCRBackend``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from structured_pdf_text.document import OcrToken
from structured_pdf_text.geometry import BBox


# ---------------------------------------------------------------------------
# Identity and capabilities
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class OCRBackendIdentity:
    """Fully-qualified identity of one running backend instance.

    Every benchmark run must record this; hashes allow reproducibility audits.
    ``extra`` holds backend-specific runtime parameters (e.g. workers,
    torch_num_threads, decoder) that do not fit the fixed schema.
    """

    engine: str
    runtime: str
    profile: str
    language: str
    device: str
    package_versions: dict[str, str] = field(default_factory=dict)
    artifact_hashes: dict[str, str] = field(default_factory=dict)
    extra: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OCRCapabilities:
    """Capabilities declared by a backend.

    Use these flags instead of ``if engine == "tesseract"`` branches.

    ``multiple_detectors`` is True only when the backend can run more than one
    text detector algorithm in the same session and all of them are confirmed
    functional at runtime (weights present AND inference probe passed).  A backend
    that knows about a second detector but cannot execute it must leave this False.
    """

    detection: bool
    recognition: bool
    line_orientation: bool
    page_orientation: bool
    quadrilateral_boxes: bool
    per_token_confidence: bool
    polygons: bool = False
    direct_recognition: bool = False
    detector_profiles: bool = False
    decoder_profiles: bool = False
    orientation_search: bool = False
    multiple_detectors: bool = False
    word_beam_search: bool = False
    native_confidence: bool = True


# ---------------------------------------------------------------------------
# Request / result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OCRRequest:
    """Input to the canonical ``recognize`` method.

    ``input_kind`` distinguishes full-page, sub-region, and line crops.
    The caller is responsible for rendering and hashing the image.
    """

    image: object
    image_sha256: str
    document_id: str
    page_index: int
    input_kind: str         # "page" | "region" | "line"
    language: str
    region_id: str | None = None
    region_bbox: tuple[float, float, float, float] | None = None  # (x0,y0,x1,y1) in page-pixel space
    dpi: int | None = None
    coordinate_system: str = "page"


@dataclass(frozen=True, slots=True)
class OCRToken:
    """Canonical OCR token for the benchmark/comparison layer.

    Distinct from ``document.OcrToken`` which is the internal pipeline token.
    ``polygon_px`` stores the raw quadrilateral in image-pixel space as
    returned by the detector; ``bbox_px`` is the axis-aligned envelope.
    ``confidence_scale`` documents the engine's native range so that scores
    are never mis-compared across backends.
    """

    text: str
    polygon_px: tuple[tuple[float, float], ...]
    bbox_px: tuple[float, float, float, float]   # x0, y0, x1, y1
    confidence_native: float | None
    confidence_scale: str    # "0..1" | "0..100" | "unknown"
    level: str               # "word" | "line"
    source_engine: str


_VALID_STATUSES = frozenset({
    "ok", "no_text", "partial", "model_missing",
    "timeout", "runtime_error", "budget_blocked", "invalid_input",
    "recovered",  # nominal path failed; result obtained via fallback (degraded)
})


@dataclass(frozen=True)
class OCRResult:
    """Full output of the canonical ``recognize`` method.

    The ``status`` field must be one of the values in ``_VALID_STATUSES``.
    """

    status: str
    tokens: tuple[OCRToken, ...]
    text: str
    engine_identity: OCRBackendIdentity
    elapsed_total_s: float
    elapsed_detection_s: float | None = None
    elapsed_recognition_s: float | None = None
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in _VALID_STATUSES:
            raise ValueError(f"Unknown OCRResult status: {self.status!r}")


# ---------------------------------------------------------------------------
# Backend protocol
# ---------------------------------------------------------------------------

class OCRBackend(Protocol):
    """Full contract that every OCR backend must satisfy.

    Backends implement this protocol structurally (no explicit inheritance).
    They also satisfy ``OcrEngine`` (from ``ocr/engine.py``) by providing
    ``recognize_page`` and ``recognize_region`` with the same signatures —
    this allows ``recovery.py`` to accept any backend without modification.
    """

    @property
    def identity(self) -> OCRBackendIdentity:
        """Fully-qualified identity of this instance."""
        ...

    @property
    def capabilities(self) -> OCRCapabilities:
        """Capabilities declared by this backend."""
        ...

    def recognize(self, request: OCRRequest) -> OCRResult:
        """Run OCR and return canonical result for the benchmarking layer."""
        ...

    def recognize_page(
        self,
        page_image: object,
        page_index: int,
        page_bbox: BBox | None = None,
        *,
        quality_variants: bool | None = None,
        quality_policy: str | None = None,
    ) -> list[OcrToken]:
        """Pipeline-layer page OCR — same signature as ``OcrEngine.recognize_page``."""
        ...

    def recognize_region(
        self, page_image: object, page_index: int, region_bbox: BBox,
        *, page_bbox: BBox | None = None,
    ) -> list[OcrToken]:
        """Pipeline-layer region OCR — same signature as ``OcrEngine.recognize_region``."""
        ...

    def healthcheck(self) -> str:
        """Run the backend's legacy health probe, which may load its runtime.

        Use ``ocr.readiness.probe_static`` for checks that must not construct a
        model, and ``probe_deep`` for an explicit inference smoke test. Returned
        values follow: ready, missing, incomplete, corrupt, unknown.
        """
        ...

    def close(self) -> None:
        """Release all resources held by this backend."""
        ...


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class UnsupportedOCREngine(ValueError):
    """Raised when ``build_ocr_backend`` receives an unknown engine name."""
