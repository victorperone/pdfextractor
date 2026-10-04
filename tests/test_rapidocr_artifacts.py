from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts import verify_rapidocr_artifacts as verifier


def test_verifier_accepts_pinned_recognizer_and_pt_br_dictionary(tmp_path: Path, monkeypatch) -> None:
    model = tmp_path / "recognizer.onnx"
    dictionary = tmp_path / "latin_dict.txt"
    model.write_bytes(b"model")
    dictionary.write_text("ã\nõ\ná\né\ní\nó\nú\nç\nê\nô\n", encoding="utf-8")
    monkeypatch.setattr(verifier, "EXPECTED_REC_SHA256", hashlib.sha256(b"model").hexdigest())
    verifier.verify(model, dictionary)


def test_verifier_rejects_model_checksum_mismatch(tmp_path: Path) -> None:
    model = tmp_path / "recognizer.onnx"
    dictionary = tmp_path / "latin_dict.txt"
    model.write_bytes(b"wrong model")
    dictionary.write_text("ãõáéíóúçêô", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        verifier.verify(model, dictionary)
