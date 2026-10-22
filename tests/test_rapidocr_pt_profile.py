from __future__ import annotations

import pytest

from structured_pdf_text.config import ExtractorConfig
from structured_pdf_text.ocr.backends.rapidocr import (
    RapidOCRBackend,
    portuguese_dictionary_profile,
)


LATIN_PT_CHARS = "ãõçêáéíóú"


def test_rapidocr_pt_profile_rejects_english_dictionary(tmp_path):
    dictionary = tmp_path / "en_dict.txt"
    dictionary.write_text("\n".join("abcdefghijklmnopqrstuvwxyz"), encoding="utf-8")

    profile, missing = portuguese_dictionary_profile(dictionary)

    assert profile == "latin-unverified"
    assert set(missing) == set(LATIN_PT_CHARS)


def test_rapidocr_pt_profile_accepts_latin_dictionary(tmp_path):
    dictionary = tmp_path / "ppocrv5_latin_dict.txt"
    dictionary.write_text("\n".join(LATIN_PT_CHARS), encoding="utf-8")

    profile, missing = portuguese_dictionary_profile(dictionary)

    assert profile == "latin/pt-compatible"
    assert missing == ()


def test_rapidocr_profiles_resolve_medium_by_default_and_small_explicitly(tmp_path, monkeypatch):
    from structured_pdf_text.ocr.factory import resolve_config

    for name in ("RAPIDOCR_DET_MODEL", "RAPIDOCR_REC_MODEL", "RAPIDOCR_REC_KEYS", "RAPIDOCR_CLS_MODEL"):
        monkeypatch.delenv(name, raising=False)
    medium = resolve_config(ExtractorConfig(ocr_engine="rapidocr", ocr_cache_home=tmp_path)).settings
    small = resolve_config(ExtractorConfig(
        ocr_engine="rapidocr", ocr_provider="openvino", rapidocr_model_size="small",
        ocr_cache_home=tmp_path,
    )).settings

    assert medium.model_size == "medium"
    assert medium.selection.provider == "onnxruntime"
    assert medium.detector_path.endswith("PP-OCRv6_det_medium.onnx")
    assert medium.recognizer_path.endswith("PP-OCRv6_rec_medium.onnx")
    assert small.model_size == "small"
    assert small.selection.provider == "openvino"
    assert small.detector_path.endswith("PP-OCRv6_det_small.onnx")
    assert small.recognizer_path.endswith("PP-OCRv6_rec_small.onnx")


def test_rapidocr_rejects_legacy_v5_environment_before_selection(monkeypatch):
    from structured_pdf_text.ocr.factory import resolve_config

    monkeypatch.setenv("RAPIDOCR_REC_MODEL", "/cache/latin_PP-OCRv5_rec_mobile.onnx")
    monkeypatch.setenv("RAPIDOCR_REC_KEYS", "/cache/ppocrv5_latin_dict.txt")
    with pytest.raises(ValueError, match="detector, recognizer, dictionary and classifier"):
        resolve_config(ExtractorConfig(ocr_engine="rapidocr"))


def test_rapidocr_model_and_dictionary_are_registered_in_identity(tmp_path, monkeypatch):
    from structured_pdf_text.ocr.pipelines.rapidocr import models

    artifacts = [tmp_path / name for name in ("det.onnx", "rec.onnx", "dict.txt", "cls.onnx")]
    for artifact in artifacts:
        artifact.write_bytes(b"mock-model")
    for name, path in zip(("RAPIDOCR_DET_MODEL", "RAPIDOCR_REC_MODEL", "RAPIDOCR_REC_KEYS", "RAPIDOCR_CLS_MODEL"), artifacts):
        monkeypatch.setenv(name, str(path))
    hashes = {name: str(index) * 64 for index, name in enumerate(("detector", "recognizer", "dictionary", "classifier"), 1)}
    monkeypatch.setattr(models, "verify_artifacts", lambda _size, _paths: hashes)

    class DummyRapidOCR:
        __module__ = "rapidocr.fake"

        def __init__(self, *, params):
            self.params = params

    monkeypatch.setattr(
        "structured_pdf_text.ocr.backends.rapidocr._import_rapidocr",
        lambda _runtime: DummyRapidOCR,
    )
    monkeypatch.setattr(
        "structured_pdf_text.ocr.backends.rapidocr._rapidocr_config_types",
        lambda: (type("EngineType", (), {"__new__": lambda cls, value: value}),
                 type("ModelType", (), {"__new__": lambda cls, value: value}),
                 type("OCRVersion", (), {"PPOCRV6": "PP-OCRv6", "PPOCRV4": "PP-OCRv4"})),
    )
    backend = RapidOCRBackend(ExtractorConfig(ocr_engine="rapidocr", language="pt"))

    identity = backend.identity
    assert backend._engine.params["Det.model_path"] == str(artifacts[0])
    assert backend._engine.params["Det.model_type"] == "medium"
    assert backend._engine.params["Rec.model_path"] == str(artifacts[1])
    assert backend._engine.params["Rec.rec_keys_path"] == str(artifacts[2])
    assert backend._engine.params["Rec.ocr_version"] == "PP-OCRv6"
    assert backend._engine.params["Rec.lang_type"] == "pt"
    assert backend._engine.params["Cls.model_path"] == str(artifacts[3])
    assert backend._engine.params["Global.use_cls"] is False
    assert identity.profile == "pp-ocrv6-medium"
    assert identity.extra["model_size"] == "medium"
    assert identity.extra["classifier_inference"] is False
    assert identity.artifact_hashes["rec_model"] == hashes["recognizer"]
    assert identity.artifact_hashes["rec_keys"] == hashes["dictionary"]


def test_rapidocr_rejects_partial_legacy_model_override(monkeypatch):
    class DummyRapidOCR:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr(
        "structured_pdf_text.ocr.backends.rapidocr._import_rapidocr",
        lambda _runtime: DummyRapidOCR,
    )
    monkeypatch.setenv("RAPIDOCR_REC_MODEL", "/tmp/recognizer.onnx")
    monkeypatch.delenv("RAPIDOCR_REC_KEYS", raising=False)

    with pytest.raises(ValueError, match="detector, recognizer, dictionary and classifier"):
        RapidOCRBackend(ExtractorConfig(language="pt"))


def test_pt_preflight_rejects_english_dictionary(tmp_path, monkeypatch):
    from structured_pdf_text.config import ExtractorConfig
    from structured_pdf_text.ocr.readiness import ReadinessStatus, probe_static

    dictionary = tmp_path / "en_dict.txt"
    dictionary.write_text("\n".join("abcdefghijklmnopqrstuvwxyz"), encoding="utf-8")
    monkeypatch.setattr(
        "structured_pdf_text.ocr.readiness.importlib.util.find_spec",
        lambda name: object() if name in {"rapidocr", "onnxruntime"} else None,
    )

    result = probe_static(ExtractorConfig(ocr_engine="rapidocr", ocr_provider="onnxruntime"))

    assert result.status == ReadinessStatus.INCOMPLETE
    assert result.reason_code == "model_file_missing"
