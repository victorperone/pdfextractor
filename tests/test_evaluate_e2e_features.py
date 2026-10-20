"""Feature artifacts use the single extracted document; no corpus extraction."""
from __future__ import annotations

import json
import sys

import pytest

from scripts import evaluate_e2e as runner
from structured_pdf_text.document import (
    DocumentDiagnostics, DocumentMetadata, ExtractionStatus, StructuredDocument,
)


def document():
    return StructuredDocument([], [], "", "", DocumentMetadata("fixture.pdf", 0, None),
        DocumentDiagnostics(ExtractionStatus.SUCCESS, 0, 0, 0, 0))


def test_feature_snapshot_is_complete_json_and_cannot_overwrite(tmp_path):
    doc = document()
    paths = runner._save_features(doc, tmp_path, "fixture", "r1")
    assert json.loads((tmp_path / "features_fixture_r1.json").read_text()) == doc.to_dict()
    assert "status=success" in (tmp_path / "diagnostics_fixture_r1.txt").read_text()
    assert set(paths) == {"structured_document", "diagnostic_report"}
    with pytest.raises(FileExistsError):
        runner._save_features(doc, tmp_path, "fixture", "r1")


def test_feature_snapshot_rejects_nonfinite_before_publishing(tmp_path):
    doc = document()
    doc.diagnostics.facts["invalid"] = float("nan")
    with pytest.raises(ValueError):
        runner._save_features(doc, tmp_path, "fixture", "r1")
    assert list(tmp_path.iterdir()) == []


def test_runner_extracts_exactly_once_and_saves_features_from_that_document(tmp_path, monkeypatch):
    from structured_pdf_text import api
    calls = []

    class Extractor:
        ocr_engine = None

        def __init__(self, config):
            assert config.retain_native_evidence

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract(self, pdf, **kwargs):
            calls.append(pdf)
            return document()

    pdf = tmp_path / "fixture.pdf"
    pdf.write_bytes(b"mock PDF; never parsed")
    monkeypatch.setattr(api, "PdfTextExtractor", Extractor)
    monkeypatch.setattr(sys, "argv", ["evaluate_e2e.py", str(pdf), "--output-dir", str(tmp_path),
        "--run-id", "r1", "--mode", "native", "--save-features", "--retain-native-evidence", "--quiet"])
    assert runner.main() == 0
    assert calls == [pdf]
    manifest = json.loads((tmp_path / "manifesto_e2e_easyocr_r1.json").read_text())
    assert manifest["retain_native_evidence"] is True
    assert manifest["structured_document"] == str(tmp_path / "features_easyocr_r1.json")
    assert manifest["diagnostic_report"] == str(tmp_path / "diagnostics_easyocr_r1.txt")
    assert runner.main() == 1
    assert calls == [pdf]


def test_retaining_evidence_requires_saving_features(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["evaluate_e2e.py", str(tmp_path / "fixture.pdf"),
        "--output-dir", str(tmp_path), "--retain-native-evidence"])
    assert runner.main() == 1
    assert "requires --save-features" in capsys.readouterr().err
