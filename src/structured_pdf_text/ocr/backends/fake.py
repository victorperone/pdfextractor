"""FakeOCRBackend — deterministic stub for unit and contract tests.

Returns a fixed set of tokens without loading any OCR runtime or model files.
All tests that need to exercise the pipeline without a real OCR engine should
use this backend.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from structured_pdf_text.document import OcrToken, SourceKind
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.contracts import (
    OCRBackendIdentity,
    OCRCapabilities,
    OCRRequest,
    OCRResult,
    OCRToken,
    UnsupportedOCREngine,
)


@dataclass
class FakeOCRBackend:
    """Configurable stub backend for tests.

    Args:
        tokens: OcrToken list to return for every recognize_page call.
        status: Status string for OCRResult (default "ok").
        raise_on_recognize: If True, raises RuntimeError on recognize().
    """

    tokens: list[OcrToken] = field(default_factory=list)
    status: str = "ok"
    raise_on_recognize: bool = False

    # OCRBackend identity / capabilities
    @property
    def identity(self) -> OCRBackendIdentity:
        return OCRBackendIdentity(
            engine="fake",
            runtime="fake",
            profile="fake-profile",
            language="pt",
            device="cpu",
            package_versions={},
            artifact_hashes={},
        )

    @property
    def capabilities(self) -> OCRCapabilities:
        return OCRCapabilities(
            detection=True,
            recognition=True,
            line_orientation=False,
            page_orientation=False,
            quadrilateral_boxes=False,
            per_token_confidence=True,
        )

    def recognize(self, request: OCRRequest) -> OCRResult:
        if self.raise_on_recognize:
            raise RuntimeError("FakeOCRBackend.recognize: forced error")

        canonical_tokens = tuple(
            OCRToken(
                text=t.text,
                polygon_px=(
                    (t.bbox.x0, t.bbox.y0),
                    (t.bbox.x1, t.bbox.y0),
                    (t.bbox.x1, t.bbox.y1),
                    (t.bbox.x0, t.bbox.y1),
                ),
                bbox_px=(t.bbox.x0, t.bbox.y0, t.bbox.x1, t.bbox.y1),
                confidence_native=t.confidence,
                confidence_scale="0..1",
                level="line",
                source_engine="fake",
            )
            for t in self.tokens
        )
        return OCRResult(
            status=self.status,
            tokens=canonical_tokens,
            text=" ".join(t.text for t in self.tokens),
            engine_identity=self.identity,
            elapsed_total_s=0.0,
            elapsed_detection_s=0.0,
            elapsed_recognition_s=0.0,
        )

    # OcrEngine protocol — consumed by recovery.py and the rest of the pipeline
    def recognize_page(
        self,
        page_image: object,
        page_index: int,
        page_bbox: BBox | None = None,
        *,
        quality_variants: bool | None = None,
        quality_policy: str | None = None,
    ) -> list[OcrToken]:
        return list(self.tokens)

    def recognize_region(
        self, page_image: object, page_index: int, region_bbox: BBox,
        *, page_bbox: BBox | None = None,
    ) -> list[OcrToken]:
        return list(self.tokens)

    def healthcheck(self) -> str:
        return "ready"

    def close(self) -> None:
        pass
