"""Contract tests for the OCR backend abstraction layer.

These tests run without any real OCR runtime (no PaddlePaddle, no Tesseract).
They verify:
  1. FakeOCRBackend satisfies the full OCR contract.
  2. Factory creates PaddleOCRBackend for engine="paddle".
  3. Factory raises for unknown engines.
  4. OCRResult validates status strings.
  5. OCRBackendIdentity and OCRCapabilities are immutable.
"""
from __future__ import annotations

import math
import pytest

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
from structured_pdf_text.ocr.backends.fake import FakeOCRBackend
from structured_pdf_text.ocr.registry import REGISTRY, get_entry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ocr_token(text: str = "hello") -> OcrToken:
    return OcrToken(
        text=text,
        bbox=BBox(10.0, 20.0, 80.0, 35.0),
        confidence=0.95,
        language="pt",
        source=SourceKind.OCR_PAGE,
    )


def _make_request() -> OCRRequest:
    import numpy as np
    return OCRRequest(
        image=np.zeros((100, 100, 3), dtype="uint8"),
        image_sha256="abc123",
        document_id="test-doc",
        page_index=0,
        input_kind="page",
        language="pt",
    )


# ---------------------------------------------------------------------------
# FakeOCRBackend contract
# ---------------------------------------------------------------------------

class TestFakeOCRBackendContract:
    def test_identity_fields_present(self) -> None:
        backend = FakeOCRBackend()
        identity = backend.identity
        assert identity.engine == "fake"
        assert isinstance(identity.runtime, str)
        assert isinstance(identity.profile, str)
        assert isinstance(identity.language, str)
        assert isinstance(identity.device, str)
        assert isinstance(identity.package_versions, dict)
        assert isinstance(identity.artifact_hashes, dict)

    def test_capabilities_fields_present(self) -> None:
        caps = FakeOCRBackend().capabilities
        assert isinstance(caps.detection, bool)
        assert isinstance(caps.recognition, bool)
        assert isinstance(caps.line_orientation, bool)
        assert isinstance(caps.page_orientation, bool)
        assert isinstance(caps.quadrilateral_boxes, bool)
        assert isinstance(caps.per_token_confidence, bool)

    def test_healthcheck_returns_string(self) -> None:
        result = FakeOCRBackend().healthcheck()
        assert result in {"ready", "missing", "incomplete", "corrupt", "unknown"}

    def test_recognize_page_returns_list(self) -> None:
        token = _make_ocr_token("world")
        backend = FakeOCRBackend(tokens=[token])
        result = backend.recognize_page(object(), 0)
        assert isinstance(result, list)
        assert result[0].text == "world"

    def test_recognize_region_returns_list(self) -> None:
        token = _make_ocr_token("region-text")
        backend = FakeOCRBackend(tokens=[token])
        result = backend.recognize_region(object(), 0, BBox(0, 0, 100, 50))
        assert isinstance(result, list)

    def test_recognize_returns_ocr_result(self) -> None:
        token = _make_ocr_token("test")
        backend = FakeOCRBackend(tokens=[token])
        result = backend.recognize(_make_request())
        assert isinstance(result, OCRResult)

    def test_ocr_result_status_is_valid(self) -> None:
        result = FakeOCRBackend(tokens=[_make_ocr_token()]).recognize(_make_request())
        assert result.status in {"ok", "no_text", "partial", "model_missing",
                                  "timeout", "runtime_error", "budget_blocked", "invalid_input"}

    def test_ocr_result_timing_nonnegative(self) -> None:
        result = FakeOCRBackend().recognize(_make_request())
        assert result.elapsed_total_s >= 0.0
        if result.elapsed_detection_s is not None:
            assert result.elapsed_detection_s >= 0.0
        if result.elapsed_recognition_s is not None:
            assert result.elapsed_recognition_s >= 0.0

    def test_ocr_result_engine_identity_present(self) -> None:
        result = FakeOCRBackend().recognize(_make_request())
        assert result.engine_identity is not None
        assert result.engine_identity.engine == "fake"

    def test_ocr_result_no_text_when_empty_tokens(self) -> None:
        result = FakeOCRBackend(tokens=[]).recognize(_make_request())
        assert result.status in {"ok", "no_text"}

    def test_canonical_token_coordinates_finite(self) -> None:
        token = _make_ocr_token("finite")
        backend = FakeOCRBackend(tokens=[token])
        result = backend.recognize(_make_request())
        for ct in result.tokens:
            for pt in ct.polygon_px:
                assert math.isfinite(pt[0]) and math.isfinite(pt[1])
            assert all(math.isfinite(v) for v in ct.bbox_px)

    def test_canonical_token_no_nan_confidence(self) -> None:
        token = _make_ocr_token()
        backend = FakeOCRBackend(tokens=[token])
        result = backend.recognize(_make_request())
        for ct in result.tokens:
            if ct.confidence_native is not None:
                assert not math.isnan(ct.confidence_native)

    def test_canonical_token_text_is_str(self) -> None:
        token = _make_ocr_token("text")
        backend = FakeOCRBackend(tokens=[token])
        result = backend.recognize(_make_request())
        for ct in result.tokens:
            assert isinstance(ct.text, str)

    def test_close_does_not_raise(self) -> None:
        FakeOCRBackend().close()


# ---------------------------------------------------------------------------
# OCRResult validation
# ---------------------------------------------------------------------------

class TestOCRResultValidation:
    def test_valid_status_ok(self) -> None:
        result = OCRResult(
            status="ok",
            tokens=(),
            text="",
            engine_identity=OCRBackendIdentity(
                engine="x", runtime="y", profile="z", language="pt", device="cpu"
            ),
            elapsed_total_s=0.1,
        )
        assert result.status == "ok"

    def test_invalid_status_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown OCRResult status"):
            OCRResult(
                status="bad-status",
                tokens=(),
                text="",
                engine_identity=OCRBackendIdentity(
                    engine="x", runtime="y", profile="z", language="pt", device="cpu"
                ),
                elapsed_total_s=0.0,
            )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_paddle_registered(self) -> None:
        assert "paddle" in REGISTRY

    def test_all_planned_engines_registered(self) -> None:
        for name in ("paddle", "rapidocr", "rapidocr-onnx", "rapidocr-openvino", "tesseract", "easyocr"):
            assert name in REGISTRY, f"{name} not in registry"

    def test_get_entry_returns_entry(self) -> None:
        entry = get_entry("paddle")
        assert entry.name == "paddle"
        assert isinstance(entry.setup_hint, str)

    def test_get_entry_raises_for_unknown(self) -> None:
        with pytest.raises(KeyError):
            get_entry("nonexistent-engine")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

class TestFactory:
    def test_factory_raises_for_unknown_engine(self) -> None:
        from structured_pdf_text.config import ExtractorConfig
        from structured_pdf_text.ocr.factory import build_ocr_backend
        config = ExtractorConfig(ocr_engine="nonexistent")
        with pytest.raises(UnsupportedOCREngine):
            build_ocr_backend(config)

    def test_factory_builds_rapidocr_when_optional_runtime_is_stubbed(self, monkeypatch, tmp_path) -> None:
        from structured_pdf_text.config import ExtractorConfig
        from structured_pdf_text.ocr import factory
        from structured_pdf_text.ocr.backends.rapidocr import RapidOCRBackend

        class DummyRapidOCR:
            def __init__(self, **kwargs):
                pass

        monkeypatch.setattr(
            "structured_pdf_text.ocr.backends.rapidocr._import_rapidocr",
            lambda runtime: DummyRapidOCR,
        )
        model = tmp_path / "recognizer.onnx"
        dictionary = tmp_path / "dictionary.txt"
        model.write_bytes(b"mock model")
        dictionary.write_text("\n".join("ãõçêáéíóú"), encoding="utf-8")
        monkeypatch.setenv("RAPIDOCR_REC_MODEL", str(model))
        monkeypatch.setenv("RAPIDOCR_REC_KEYS", str(dictionary))
        backend = factory.build_ocr_backend(ExtractorConfig(ocr_engine="rapidocr-onnx"))
        assert isinstance(backend, RapidOCRBackend)
        assert backend._runtime == "onnxruntime"

    def test_factory_rejects_chinese_default_for_brazilian_portuguese(self, monkeypatch) -> None:
        from structured_pdf_text.config import ExtractorConfig
        from structured_pdf_text.ocr import factory
        from structured_pdf_text.ocr.backends.rapidocr import RapidOCRBackend

        class DummyRapidOCR:
            def __init__(self, **kwargs):
                pass

        monkeypatch.setattr(
            "structured_pdf_text.ocr.backends.rapidocr._import_rapidocr",
            lambda runtime: DummyRapidOCR,
        )
        with pytest.raises(ValueError, match="not compatible with pt-BR"):
            factory.build_ocr_backend(ExtractorConfig(ocr_engine="rapidocr"))

    def test_factory_builds_tesseract_backend(self) -> None:
        from structured_pdf_text.config import ExtractorConfig
        from structured_pdf_text.ocr.factory import build_ocr_backend
        from structured_pdf_text.ocr.backends.tesseract import TesseractBackend

        backend = build_ocr_backend(ExtractorConfig(ocr_engine="tesseract"))
        assert isinstance(backend, TesseractBackend)


# ---------------------------------------------------------------------------
# Config retrocompatibility
# ---------------------------------------------------------------------------

class TestConfigRetrocompat:
    def test_default_ocr_engine_is_paddle(self) -> None:
        from structured_pdf_text.config import ExtractorConfig
        config = ExtractorConfig()
        assert config.ocr_engine == "paddle"

    def test_default_ocr_runtime_is_paddle_static(self) -> None:
        from structured_pdf_text.config import ExtractorConfig
        config = ExtractorConfig()
        assert config.ocr_runtime == "paddle_static"

    def test_existing_code_still_constructs_config(self) -> None:
        from structured_pdf_text.config import ExtractorConfig, ExtractionMode
        config = ExtractorConfig(mode=ExtractionMode.BALANCED, language="pt")
        assert config.ocr_engine == "paddle"
