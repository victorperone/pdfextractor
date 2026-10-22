"""The acceptance harness must report incomplete work and resource cleanup."""
import json
from types import SimpleNamespace
import sys

import pytest

from scripts import run_ocr_endurance as runner
from structured_pdf_text import ExtractorConfig, PdfTextExtractor
from structured_pdf_text.ocr.readiness import ReadinessResult, ReadinessStatus
from ocr_domain_fixtures import TableFixtureOCR, hybrid_table_pdf, install_table_structure


@pytest.fixture
def workload(tmp_path, monkeypatch):
    install_table_structure(monkeypatch)
    pdf = hybrid_table_pdf(tmp_path / "endurance.pdf")
    engine = TableFixtureOCR()
    monkeypatch.setattr(runner, "PdfTextExtractor", lambda config: PdfTextExtractor(config, ocr_engine=engine))
    monkeypatch.setattr(runner, "probe_static", lambda config: ReadinessResult(ReadinessStatus.READY))
    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(Process=lambda: object()))
    def snapshot(_process):
        return {"parent_rss_bytes": 1000, "tree_rss_bytes": 2000,
                "parent_descriptors": 8, "tree_descriptors": 9,
                "children": [{"pid": 200, "created": 1.}] if engine.calls and not engine.closed else [],
                "measurement_errors": []}
    monkeypatch.setattr(runner, "resources", snapshot)
    class Sampler:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def as_dict(self): return {"sample_count": 2}
    monkeypatch.setattr(runner, "ProcessTreeMemorySampler", Sampler)
    config = ExtractorConfig(mode="balanced", enable_tables=True, enable_table_cell_ocr=True,
                             complexity_render_scale=1.)
    return config, pdf, tmp_path / "run", engine


def test_repeated_real_pdf_extraction_records_windows_without_retaining_documents(workload):
    config, pdf, output, engine = workload
    report = runner.run(config, pdf, output, repeat=2, warmup=1)
    assert report["workload_status"] == "completed"
    assert report["measured_pages"] == 2
    assert len(report["iterations"]) == 3
    assert all(report["operational_checks"].values())
    assert report["release_accepted"] is report["quality_accepted"] is False
    assert engine.closed
    assert report["remaining_new_children"] == []
    assert all(item["resources"]["children"] for item in report["iterations"])
    persisted = json.loads((output / "report.json").read_text())
    assert persisted["iterations"][0]["identity"]["engine"] == "custom"
    samples = [json.loads(line) for line in (output / "samples.jsonl").read_text().splitlines()]
    assert sum(sample["event"] == "page_start" for sample in samples) == 3
    assert samples[-1]["event"] == "after_close"
    assert not samples[-1]["children"]
    assert (output / "first.md").read_text().count("Papel") == 1


def test_existing_namespace_prevents_runtime_probe_or_artifact_overwrite(workload, monkeypatch):
    config, pdf, output, _ = workload
    output.mkdir()
    marker = output / "report.json"
    marker.write_text("original")
    def forbidden(_config):
        pytest.fail("Readiness must not run when namespace is occupied")
    monkeypatch.setattr(runner, "probe_static", forbidden)
    with pytest.raises(FileExistsError):
        runner.run(config, pdf, output, repeat=1, warmup=0)
    assert marker.read_text() == "original"


def test_foreign_loaded_checkout_is_rejected_before_reserving_artifacts(workload, monkeypatch):
    import structured_pdf_text

    config, pdf, output, engine = workload
    monkeypatch.setattr(structured_pdf_text, "__file__", str(output.parent / "foreign/src/structured_pdf_text/__init__.py"))
    with pytest.raises(ValueError, match="outside this checkout"):
        runner.run(config, pdf, output, repeat=1, warmup=0)
    assert not output.exists()
    assert engine.calls == []


def test_unready_runtime_records_error_without_initialization(workload, monkeypatch):
    config, pdf, output, engine = workload
    monkeypatch.setattr(runner, "probe_static", lambda config: ReadinessResult(
        ReadinessStatus.MISSING, "model_missing"))
    report = runner.run(config, pdf, output, repeat=2, warmup=0)
    assert report["workload_status"] == "error"
    assert "model_missing" in report["error"]
    assert not engine.calls
    assert report["operational_checks"]["all_iterations_completed"] is False
    assert report["measured_pages"] == 0
    assert (output / "report.json").is_file()


def test_warmup_does_not_count_as_endurance_and_close_failure_is_reported(workload, monkeypatch):
    config, pdf, output, engine = workload
    original = engine.close
    def failing_close():
        original()
        raise RuntimeError("close failed")
    engine.close = failing_close
    report = runner.run(config, pdf, output, repeat=1, warmup=2)
    assert report["measured_pages"] == 1
    assert report["workload_status"] == "error"
    assert "close failed" in report["close_error"]
    assert report["release_accepted"] is False


def test_unavailable_tree_measurement_never_passes_gate(workload, monkeypatch):
    config, pdf, output, _ = workload
    monkeypatch.setattr(runner, "resources", lambda _process: {
        "parent_rss_bytes": 1000, "tree_rss_bytes": None, "children": [],
        "measurement_errors": ["AccessDenied"]})
    report = runner.run(config, pdf, output, repeat=1, warmup=0)
    assert report["workload_status"] == "completed"
    assert report["operational_checks"]["measurements_complete"] is False


def test_process_tree_access_failure_is_reported_as_unavailable(monkeypatch):
    class Denied(Exception): pass
    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(
        NoSuchProcess=type("Gone", (Exception,), {}), AccessDenied=Denied))
    class Process:
        pid = 10
        def children(self, recursive): raise Denied("tree unavailable")
        def num_fds(self): return 5
        def num_handles(self): return 7
        def create_time(self): return 1.
        def memory_info(self): return SimpleNamespace(rss=1000)
    result = runner.resources(Process())
    assert result["parent_rss_bytes"] == 1000
    assert result["tree_rss_bytes"] is None
    assert result["tree_descriptors"] is None
    assert "Denied" in result["measurement_errors"][0]


def test_windows_handles_are_measured_instead_of_file_descriptors(monkeypatch):
    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(
        NoSuchProcess=type("Gone", (Exception,), {}), AccessDenied=type("Denied", (Exception,), {})))
    monkeypatch.setattr(runner.sys, "platform", "win32")
    class Process:
        pid = 10
        def children(self, recursive): return []
        def num_handles(self): return 7
        def num_fds(self): pytest.fail("Windows does not expose num_fds")
        def create_time(self): return 1.
        def memory_info(self): return SimpleNamespace(rss=1000)
    result = runner.resources(Process())
    assert result["descriptor_kind"] == "handles"
    assert result["parent_descriptors"] == result["tree_descriptors"] == 7
    assert not result["measurement_errors"]
