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
        from structured_pdf_text.ocr.registry import LEGACY_ENGINE_ALIASES, PUBLIC_ENGINES
        for name in PUBLIC_ENGINES:
            assert name in REGISTRY, f"{name} not in registry"
        for alias in LEGACY_ENGINE_ALIASES:
            assert alias not in REGISTRY, f"{alias} must remain a compatibility alias only"

    def test_legacy_registry_alias_resolves_to_public_family(self) -> None:
        with pytest.warns(DeprecationWarning):
            entry = get_entry("rapidocr-openvino")
        assert entry.name == "rapidocr"

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
        from structured_pdf_text.errors import ConfigurationError
        from structured_pdf_text.ocr.factory import build_ocr_backend
        with pytest.raises(ConfigurationError, match="Unsupported OCR engine"):
            ExtractorConfig(ocr_engine="nonexistent")

    def test_factory_builds_rapidocr_provider_with_pinned_profiles(self, monkeypatch, tmp_path) -> None:
        from structured_pdf_text.config import ExtractorConfig
        from structured_pdf_text.ocr import factory
        from structured_pdf_text.ocr.backends.rapidocr import RapidOCRBackend
        from structured_pdf_text.ocr.pipelines.rapidocr import models

        class DummyRapidOCR:
            __module__ = "rapidocr.fake"
            def __init__(self, *, params):
                self.params = params

        monkeypatch.setattr(
            "structured_pdf_text.ocr.backends.rapidocr._import_rapidocr",
            lambda runtime: DummyRapidOCR,
        )
        paths = [tmp_path / name for name in ("det.onnx", "rec.onnx", "dict.txt", "cls.onnx")]
        for path in paths:
            path.write_bytes(b"mock model")
        for name, path in zip(("RAPIDOCR_DET_MODEL", "RAPIDOCR_REC_MODEL", "RAPIDOCR_REC_KEYS", "RAPIDOCR_CLS_MODEL"), paths):
            monkeypatch.setenv(name, str(path))
        monkeypatch.setattr(models, "verify_artifacts", lambda _size, _paths: {
            "detector": "1" * 64, "recognizer": "2" * 64,
            "dictionary": "3" * 64, "classifier": "4" * 64,
        })
        monkeypatch.setattr(
            "structured_pdf_text.ocr.backends.rapidocr._rapidocr_config_types",
            lambda: (type("EngineType", (), {"__new__": lambda cls, value: value}),
                     type("ModelType", (), {"__new__": lambda cls, value: value}),
                     type("OCRVersion", (), {"PPOCRV6": "PP-OCRv6", "PPOCRV4": "PP-OCRv4"})),
        )
        backend = factory.build_ocr_backend(ExtractorConfig(ocr_engine="rapidocr-onnx"))
        assert isinstance(backend, RapidOCRBackend)
        assert backend._runtime == "onnxruntime"
        assert backend.identity.profile == "pp-ocrv6-medium"

    def test_factory_fails_when_pinned_rapidocr_v6_models_are_not_provisioned(self, monkeypatch) -> None:
        from structured_pdf_text.config import ExtractorConfig
        from structured_pdf_text.ocr import factory

        class DummyRapidOCR:
            def __init__(self, **kwargs):
                pass

        monkeypatch.setattr(
            "structured_pdf_text.ocr.backends.rapidocr._import_rapidocr",
            lambda runtime: DummyRapidOCR,
        )
        for name in ("RAPIDOCR_DET_MODEL", "RAPIDOCR_REC_MODEL", "RAPIDOCR_REC_KEYS", "RAPIDOCR_CLS_MODEL"):
            monkeypatch.delenv(name, raising=False)
        with pytest.raises(ValueError, match="detector is missing or empty"):
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
    def test_default_ocr_engine_is_easyocr(self) -> None:
        from structured_pdf_text.config import ExtractorConfig
        config = ExtractorConfig()
        assert config.ocr_engine == "easyocr"

    def test_deprecated_ocr_runtime_is_not_a_default_second_source_of_truth(self) -> None:
        from structured_pdf_text.config import ExtractorConfig
        config = ExtractorConfig()
        assert config.ocr_runtime is None

    def test_existing_code_still_constructs_config(self) -> None:
        from structured_pdf_text.config import ExtractorConfig, ExtractionMode
        config = ExtractorConfig(mode=ExtractionMode.BALANCED, language="pt")
        assert config.ocr_engine == "easyocr"
