"""T3-P00: o executor da série Tesseract deve usar a config canônica de benchmark."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from structured_pdf_text.config import ExtractionMode, OcrQualityPolicy

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_tesseract_batch_wsl.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_tesseract_batch_wsl", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_runner_config_enables_tables_and_suppresses_headers():
    config = _load_runner().build_config()
    assert config.enable_tables is True
    assert config.merge_cross_page_tables is True
    assert config.preserve_headers_footers is False


def test_runner_config_keeps_requested_round_parameters():
    config = _load_runner().build_config()
    assert ExtractionMode(config.mode) is ExtractionMode.BALANCED
    assert config.language == "pt-BR"
    assert config.ocr_engine == "tesseract"
    assert config.num_threads == 8  # estabelecido para a RUN 005 (antes: 6)
    assert config.effective_ocr_quality_policy() is OcrQualityPolicy.ADAPTIVE


def test_round_settings_record_the_effective_thread_configuration():
    """T3-P30: as rodadas 001–005 gravaram ``threads: 6`` fixo, mesmo com 8 threads."""
    from dataclasses import replace

    runner = _load_runner()
    settings = runner.round_settings(runner.build_config())
    assert settings["threads"] == runner.build_config().num_threads
    assert settings["tesseract_omp_thread_limit"] == runner.build_config().num_threads
    assert runner.round_settings(replace(runner.build_config(), num_threads=3))["threads"] == 3
    assert runner.tesseract_thread_limit(-1) is None
    assert runner.tesseract_thread_limit(0) >= 1


def test_runner_has_no_hard_coded_thread_count():
    """O manifesto e o resumo devem derivar as threads da config efetiva."""
    import re

    assert not re.search(r'"(?:requested_)?threads":\s*\d', SCRIPT.read_text(encoding="utf-8"))


def _write_pdf(path: Path, pages: int) -> None:
    import pypdfium2

    doc = pypdfium2.PdfDocument.new()
    try:
        for _ in range(pages):
            doc.new_page(595, 842)
        doc.save(str(path))
    finally:
        doc.close()


def _paged(pages: int, marker: str = "## Página {n:03d}") -> str:
    return "\n\n".join(f"{marker.format(n=n)}\n\nTexto da página {n}." for n in range(1, pages + 1)) + "\n"


def test_preflight_uses_paginated_reference_when_primary_sources_unusable(tmp_path, monkeypatch):
    """V10: _REFERENCIA.md com marcadores HTML e manifesto sem expected_markdown."""
    runner = _load_runner()
    folder = tmp_path / "corpus" / "VX"
    folder.mkdir(parents=True)
    _write_pdf(folder / "Doc_VX.pdf", 3)
    (folder / "Doc_VX_REFERENCIA.md").write_text(
        "\n".join(f"<!-- PAGE {n:03d} START -->\nTexto {n}\n<!-- PAGE {n:03d} END -->" for n in range(1, 4)),
        encoding="utf-8",
    )
    (folder / "Doc_VX_MANIFESTO.json").write_text(
        '{"pages": [{"page_number": 1, "expected_text": "Texto 1"}]}', encoding="utf-8"
    )
    paged = folder / "Doc_VX_REFERENCIA_PAGINADA.md"
    paged.write_text(_paged(3), encoding="utf-8")
    monkeypatch.setattr(runner, "REPO", tmp_path)
    monkeypatch.setattr(runner, "CORPORA", [("VX", "corpus/VX/Doc_VX.pdf")])

    (job,) = runner.preflight()

    assert job["source"] == "reference_paginada"
    assert job["reference"] == paged
    assert job["missing_reference_pages"] == []


def test_preflight_prefers_primary_reference_over_paginated_fallback(tmp_path, monkeypatch):
    runner = _load_runner()
    folder = tmp_path / "corpus" / "VX"
    folder.mkdir(parents=True)
    _write_pdf(folder / "Doc_VX.pdf", 2)
    primary = folder / "Doc_VX_REFERENCIA.md"
    primary.write_text(_paged(2, "## Página {n:03d} | título"), encoding="utf-8")
    (folder / "Doc_VX_REFERENCIA_PAGINADA.md").write_text(_paged(2), encoding="utf-8")
    monkeypatch.setattr(runner, "REPO", tmp_path)
    monkeypatch.setattr(runner, "CORPORA", [("VX", "corpus/VX/Doc_VX.pdf")])

    (job,) = runner.preflight()

    assert job["source"] == "reference"
    assert job["reference"] == primary


def test_runner_includes_v10():
    assert ("V10", "corpus/V10/Document_AI_V10.pdf") in _load_runner().CORPORA


def test_preflight_pins_input_hashes_and_detects_changes(tmp_path, monkeypatch):
    """T3-P15: o corpus V10 foi substituído durante a RUN 003; a mudança deve ser detectada."""
    runner = _load_runner()
    folder = tmp_path / "corpus" / "VX"
    folder.mkdir(parents=True)
    _write_pdf(folder / "Doc_VX.pdf", 2)
    reference = folder / "Doc_VX_REFERENCIA.md"
    reference.write_text(_paged(2), encoding="utf-8")
    monkeypatch.setattr(runner, "REPO", tmp_path)
    monkeypatch.setattr(runner, "CORPORA", [("VX", "corpus/VX/Doc_VX.pdf")])

    (job,) = runner.preflight()

    assert set(job["input_hashes"]) == {"pdf", "reference"}
    assert runner.changed_inputs(job) == []
    reference.write_text(_paged(2, marker="<!-- PAGE_START VX-P{n:03d} -->"), encoding="utf-8")
    assert runner.changed_inputs(job) == ["reference"]
    reference.unlink()
    assert runner.changed_inputs(job) == ["reference"]


def test_preflight_prefers_manifest_when_reference_uses_unrecognized_page_markers(tmp_path, monkeypatch):
    """V10 revisado: referência com <!-- PAGE_START --> e manifesto com expected_markdown."""
    import json

    runner = _load_runner()
    folder = tmp_path / "corpus" / "VX"
    folder.mkdir(parents=True)
    _write_pdf(folder / "Doc_VX.pdf", 3)
    marker = "<!-- PAGE_START VX-P{n:03d} -->"
    (folder / "Doc_VX_REFERENCIA.md").write_text(_paged(3, marker=marker), encoding="utf-8")
    (folder / "Doc_VX_REFERENCIA_PAGINADA.md").write_text(_paged(3, marker=marker), encoding="utf-8")
    pages = [{"page_number": n, "expected_markdown": f"Texto da página {n}."} for n in range(1, 4)]
    (folder / "Doc_VX_MANIFESTO.json").write_text(json.dumps({"pages": pages}), encoding="utf-8")
    monkeypatch.setattr(runner, "REPO", tmp_path)
    monkeypatch.setattr(runner, "CORPORA", [("VX", "corpus/VX/Doc_VX.pdf")])

    (job,) = runner.preflight()

    assert job["source"] == "manifesto"
    assert job["reference"] is None
    assert job["missing_reference_pages"] == []
    assert set(job["input_hashes"]) == {"pdf", "manifest"}
