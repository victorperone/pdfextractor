from __future__ import annotations

from types import SimpleNamespace

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
    dictionary = tmp_path / "latin_dict.txt"
    dictionary.write_text("\n".join(LATIN_PT_CHARS), encoding="utf-8")

    profile, missing = portuguese_dictionary_profile(dictionary)

    assert profile == "latin/pt-compatible"
    assert missing == ()


def test_rapidocr_model_and_dictionary_are_registered_in_identity(tmp_path, monkeypatch):
    model = tmp_path / "latin_PP-OCRv3_rec_mobile.onnx"
    dictionary = tmp_path / "latin_dict.txt"
    model.write_bytes(b"mock-model")
    dictionary.write_text("\n".join(LATIN_PT_CHARS), encoding="utf-8")
    monkeypatch.setenv("RAPIDOCR_REC_MODEL", str(model))
    monkeypatch.setenv("RAPIDOCR_REC_KEYS", str(dictionary))

    class DummyRapidOCR:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(
        "structured_pdf_text.ocr.backends.rapidocr._import_rapidocr",
        lambda _runtime: DummyRapidOCR,
    )
    backend = RapidOCRBackend(ExtractorConfig(language="pt"))

    identity = backend.identity
    assert backend._engine.kwargs["rec_model_path"] == str(model)
    assert backend._engine.kwargs["rec_keys_path"] == str(dictionary)
    assert "rec_char_dict_path" not in backend._engine.kwargs
    assert identity.profile == "latin/pt-compatible"
    assert identity.extra["rec_model"] == str(model)
    assert identity.extra["rec_keys"] == str(dictionary)
    assert identity.extra["language_profile"] == "latin/pt-compatible"
    assert identity.artifact_hashes["rec_model"]
    assert identity.artifact_hashes["rec_keys"]


def test_rapidocr_requires_recognizer_and_dictionary_as_a_pair(monkeypatch):
    class DummyRapidOCR:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr(
        "structured_pdf_text.ocr.backends.rapidocr._import_rapidocr",
        lambda _runtime: DummyRapidOCR,
    )
    monkeypatch.setenv("RAPIDOCR_REC_MODEL", "/tmp/recognizer.onnx")
    monkeypatch.delenv("RAPIDOCR_REC_KEYS", raising=False)

    with pytest.raises(ValueError, match="must be configured together"):
        RapidOCRBackend(ExtractorConfig(language="pt"))


def test_pt_preflight_rejects_english_dictionary(tmp_path, monkeypatch):
    from scripts.preflight_ocr_backends import _check

    dictionary = tmp_path / "en_dict.txt"
    dictionary.write_text("\n".join("abcdefghijklmnopqrstuvwxyz"), encoding="utf-8")
    identity = SimpleNamespace(
        package_versions={},
        artifact_hashes={},
        extra={"rec_model": "/models/english.onnx", "rec_keys": str(dictionary)},
        profile="latin-unverified",
    )

    class FakeBackend:
        def __init__(self):
            self.identity = identity

        def healthcheck(self):
            return "ready"

        def close(self):
            pass

    monkeypatch.setattr(
        "structured_pdf_text.ocr.factory.build_ocr_backend",
        lambda _config: FakeBackend(),
    )

    status, detail = _check("rapidocr-onnx", "pt")

    assert status == "not_ready_for_pt_comparison"
    assert "missing Portuguese character coverage" in detail
