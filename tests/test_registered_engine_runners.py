"""Tools resolve the current registry after import, including new engines."""
import json
import sys

import pytest

from scripts import evaluate_e2e, run_ocr_endurance, smoke_ocr_cell_pipeline
from structured_pdf_text.document import OcrToken, SourceKind
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.registry import BackendEntry, registered_for_test

CALLS = []


def build_backend(config):
    class Backend:
        identity = None
        capabilities = {"recognition": True}
        def recognize_page(self, image, page_index, page_bbox=None, **kwargs):
            CALLS.append((config.ocr_engine, config.ocr_provider))
            box = page_bbox
            return [OcrToken("registered engine output", BBox(
                box.x0 + .1 * box.width, box.y0 + .4 * box.height,
                box.x0 + .9 * box.width, box.y0 + .6 * box.height),
                .99, "pt-BR", SourceKind.OCR_PAGE)]
        def close(self): CALLS.append("closed")
    return Backend()


@pytest.fixture
def registered():
    CALLS.clear()
    entry = BackendEntry(
        name="benchfixture", display_name="Benchmark fixture", default_runtime="fake",
        default_profile="fixture", extras=None, setup_hint="test only",
        backend_factory=f"{__name__}:build_backend",
        settings_resolver="structured_pdf_text.ocr.pipeline.settings:resolve_generic_settings",
        capabilities=("page",),
        providers=("fixture-provider",), default_provider="fixture-provider",
    )
    with registered_for_test(entry):
        yield entry


def test_e2e_registered_engine_extracts_with_identity_and_preserves_artifacts(tmp_path, monkeypatch, registered):
    from reportlab.pdfgen import canvas

    pdf = tmp_path / "fixture.pdf"
    writer = canvas.Canvas(str(pdf), pagesize=(200, 100))
    writer.drawString(20, 50, "native evidence")
    writer.save()
    output = tmp_path / "run"
    monkeypatch.setattr(sys, "argv", ["evaluate_e2e.py", str(pdf), "--engine", registered.name,
                                    "--provider", "fixture-provider",
                                    "--mode", "ocr", "--run-id", "registered", "--quiet",
                                    "--save-features", "--output-dir", str(output)])
    assert evaluate_e2e.main() == 0
    manifest = output / "manifesto_e2e_benchfixture_registered.json"
    initial = manifest.read_bytes()
    data = json.loads(initial)
    assert data["benchmark_status"] == "valid"
    assert data["engine_identity"]["engine"] == registered.name
    assert "registered engine output" in (output / "extracted_benchfixture_registered.md").read_text()
    assert (registered.name, "fixture-provider") in CALLS and "closed" in CALLS
    calls_before = tuple(CALLS)
    assert evaluate_e2e.main() == 1  # The namespace cannot be reused.
    assert manifest.read_bytes() == initial
    assert tuple(CALLS) == calls_before


@pytest.mark.parametrize("module", [run_ocr_endurance, smoke_ocr_cell_pipeline])
def test_smoke_and_endurance_parse_current_registry_without_initializing_runtime(module, registered, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [module.__name__, "--engine", registered.name,
                                    "--provider", "fixture-provider", "--help"])
    with pytest.raises(SystemExit) as finished:
        module.main()
    assert finished.value.code == 0
    assert registered.name in capsys.readouterr().out
    assert CALLS == []
