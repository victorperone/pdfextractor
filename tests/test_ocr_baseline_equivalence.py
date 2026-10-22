"""Adversarial checks for the refactoring comparator (no OCR runtimes)."""
from copy import deepcopy
import json
import sys

import pytest

from scripts.compare_ocr_baseline import check_run_identity, compare_features
from scripts import compare_ocr_baseline as comparator


def identity():
    return {"engine": "fake", "runtime": "fake", "profile": "fixture", "language": "pt-BR",
            "device": "cpu", "package_versions": {"fake": "1"},
            "artifact_hashes": {"model": "0" * 64}, "extra": {}}


def document():
    return {
        "pages": [{"page_index": 0, "tables": [{"cells": [
            {"text": "R$ -0,01", "bbox": {"x0": 1., "y0": 2., "x1": 10., "y1": 20.}},
        ]}], "content_blocks": [{"text": "Protocolo 0007"}],
            "diagnostics": {"status": "success", "processing_time_ms": 1.,
                            "facts": {"memory": {"rss": 10}, "page_ocr_requested": True}}}],
    }


def test_cpu_load_is_observational_but_effective_threads_are_semantic():
    before = document()
    before["pages"][0]["diagnostics"]["facts"]["env_probe"] = {"cpu_load_1m": 3., "effective_threads": 20}
    after = deepcopy(before)
    after["pages"][0]["diagnostics"]["facts"]["env_probe"]["cpu_load_1m"] = 42.
    report = compare_features(before, after)
    assert report["equivalent"]
    assert "/pages/0/diagnostics/facts/env_probe/cpu_load_1m" in report["costs"]
    after["pages"][0]["diagnostics"]["facts"]["env_probe"]["effective_threads"] = 10
    assert not compare_features(before, after)["equivalent"]


@pytest.mark.parametrize("mutation", ["cell_loss", "sign", "duplicate", "bbox", "status", "scope"])
def test_semantic_mutations_are_detected(mutation):
    before = document()
    after = deepcopy(before)
    page = after["pages"][0]
    if mutation == "cell_loss":
        page["tables"][0]["cells"].clear()
    elif mutation == "sign":
        page["tables"][0]["cells"][0]["text"] = "R$ +0,01"
    elif mutation == "duplicate":
        page["content_blocks"] *= 2
    elif mutation == "bbox":
        page["tables"][0]["cells"][0]["bbox"]["x0"] += .1
    elif mutation == "status":
        page["diagnostics"]["status"] = "partial_success"
    else:
        page["diagnostics"]["facts"]["page_ocr_requested"] = False
    assert not compare_features(before, after)["equivalent"]


def test_only_diagnostic_cost_is_excluded():
    before = document()
    after = deepcopy(before)
    after["pages"][0]["diagnostics"]["processing_time_ms"] = 999.
    after["pages"][0]["diagnostics"]["facts"]["memory"]["rss"] = 999
    after["pages"][0]["diagnostics"]["facts"]["content_assembly_ms"] = 999.
    result = compare_features(before, after)
    assert result["equivalent"] and result["costs"]
    after["pages"][0]["content_blocks"][0]["memory"] = "semantic content"
    assert not compare_features(before, after)["equivalent"]


def test_geometry_tolerance_does_not_relax_text():
    before = document()
    after = deepcopy(before)
    cell = after["pages"][0]["tables"][0]["cells"][0]
    cell["bbox"]["x0"] += .01
    assert compare_features(before, after, bbox_tolerance_pt=.02)["equivalent"]
    cell["text"] = "R$ 0,01"
    assert not compare_features(before, after, bbox_tolerance_pt=.02)["equivalent"]


def test_late_page_content_remains_visible_after_global_examples_are_truncated():
    before = document()
    before["pages"].append({"page_index": 30, "reading_text": "R$ -0,01"})
    after = deepcopy(before)
    for key in ("x0", "y0", "x1", "y1"):
        after["pages"][0]["tables"][0]["cells"][0]["bbox"][key] += 1
    after["pages"][1]["reading_text"] = "R$ 0,01"
    report = compare_features(before, after, max_examples=1)
    assert not report["equivalent"] and report["difference_count"] == 5
    assert report["examples_truncated"] and report["examples"][0]["category"] == "geometry"
    assert report["differences_by_page"]["1"]["difference_count"] == 4
    last = report["differences_by_page"]["31"]
    assert last["differences_by_category"] == {"content": 1}
    assert last["examples_by_category"]["content"][0]["after"] == "R$ 0,01"


def test_nonfinite_and_empty_inputs_are_rejected():
    after = document()
    after["pages"][0]["diagnostics"]["processing_time_ms"] = float("nan")
    with pytest.raises(ValueError):
        compare_features(document(), after)
    with pytest.raises(ValueError):
        compare_features({}, {})


def test_changed_provider_and_missing_identity_cannot_pass():
    assert check_run_identity({}, {})
    before = {key: "same" for key in (
        "pdf_sha256", "selected_pages", "engine", "mode", "language", "quality_policy",
    )}
    before.update(engine_identity=identity(), runtime_environment={"render_scale": 3.})
    before["engine_identity"]["runtime"] = "onnxruntime"
    before["engine_identity"]["extra"]["env_cpu_load_1m"] = 1
    after = deepcopy(before)
    after["engine_identity"]["extra"]["env_cpu_load_1m"] = 2
    assert not check_run_identity(before, after)
    after["engine_identity"]["runtime"] = "openvino"
    assert "identity_mismatch:engine_identity" in check_run_identity(before, after)


@pytest.mark.parametrize("change", [None, "config", "markdown"])
def test_cli_checks_resolved_settings_and_rendered_markdown(tmp_path, monkeypatch, change):
    manifests = []
    configs = []
    features = []
    for side in ("before", "after"):
        markdown = tmp_path / f"{side}.md"
        markdown.write_text("R$ +0,01" if side == "after" and change == "markdown" else "R$ -0,01")
        manifest = {key: "same" for key in (
            "pdf_sha256", "selected_pages", "engine", "mode", "language", "quality_policy",
        )}
        manifest.update(engine_identity=identity(), runtime_environment={},
                        extracted_markdown=str(markdown))
        path = tmp_path / f"{side}-manifest.json"
        path.write_text(json.dumps(manifest))
        manifests.append(path)
        config = tmp_path / f"{side}-config.json"
        config.write_text(json.dumps({"merge_cross_page_tables": side == "after" and change == "config"}))
        configs.append(config)
        feature = tmp_path / f"{side}-features.json"
        feature.write_text(json.dumps(document()))
        features.append(feature)
    output = tmp_path / "comparison.json"
    monkeypatch.setattr(sys, "argv", ["compare", *map(str, features),
        "--before-manifest", str(manifests[0]), "--after-manifest", str(manifests[1]),
        "--before-config", str(configs[0]), "--after-config", str(configs[1]),
        "--output", str(output)])
    assert comparator.main() == (0 if change is None else 1)
    report = json.loads(output.read_text())
    assert report["equivalent"] is (change is None)
    if change == "config":
        assert "identity_mismatch:resolved_config" in report["identity_errors"]
    if change == "markdown":
        assert report["markdown_identical"] is False


def test_incomplete_model_identity_is_not_equivalence():
    before = {key: "same" for key in (
        "pdf_sha256", "selected_pages", "engine", "mode", "language", "quality_policy",
    )}
    before.update(engine_identity={"runtime": "same"}, runtime_environment={})
    assert any("artifact_hashes" in error for error in check_run_identity(before, before))


def test_config_snapshot_normalizes_only_null_fields_added_after_legacy_baseline():
    before = {"mode": "balanced", "ocr_engine": "easyocr"}
    after = {**before, "ocr_settings": None, "rapidocr_model_size": None}

    assert comparator._normalize_config_snapshot(before) == comparator._normalize_config_snapshot(after)
    assert comparator._normalize_config_snapshot({**after, "rapidocr_model_size": "small"}) != before


def test_page_subset_comparison_normalizes_only_page_scope_and_adapter_identity(tmp_path, monkeypatch):
    pages = lambda indices: {"pages": [
        {"page_index": index, "content_blocks": [{"text": f"page-{index + 1}"}]}
        for index in indices
    ]}
    before = pages((0, 1, 2, 3))
    after = pages((1, 3))
    before_features, after_features = tmp_path / "before.json", tmp_path / "after.json"
    before_features.write_text(json.dumps(before))
    after_features.write_text(json.dumps(after))

    base_manifest = {key: "same" for key in (
        "pdf_sha256", "engine", "mode", "language", "quality_policy",
    )}
    base_manifest.update(
        engine_identity=identity(), runtime_environment={"render_scale": 3.},
        selected_pages=[1, 2, 3, 4],
    )
    before_manifest = deepcopy(base_manifest)
    after_manifest = deepcopy(base_manifest)
    after_manifest["selected_pages"] = [2, 4]
    after_manifest["engine_identity"]["extra"].update(adapter="authorized_backend", legacy_engine="fake")
    for side, manifest in (("before", before_manifest), ("after", after_manifest)):
        markdown = tmp_path / f"{side}.md"
        markdown.write_text("## Página 1\none\n## Página 2\ntwo\n## Página 3\nthree\n## Página 4\nfour\n")
        manifest["extracted_markdown"] = str(markdown)
        manifest_path = tmp_path / f"{side}-manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        if side == "before": before_path = manifest_path
        else: after_path = manifest_path
    before_config, after_config = tmp_path / "before-config.json", tmp_path / "after-config.json"
    before_config.write_text(json.dumps({"mode": "balanced", "page_indices": None}))
    after_config.write_text(json.dumps({"mode": "balanced", "page_indices": [1, 3]}))
    output = tmp_path / "subset-comparison.json"

    monkeypatch.setattr(sys, "argv", ["compare", str(before_features), str(after_features),
        "--before-manifest", str(before_path), "--after-manifest", str(after_path),
        "--before-config", str(before_config), "--after-config", str(after_config),
        "--pages", "2,4", "--output", str(output)])
    assert comparator.main() == 0
    report = json.loads(output.read_text())
    assert report["equivalent"] is True
    assert report["compared_pages"] == [2, 4]
    assert report["identity_errors"] == []


def test_page_subset_rejects_missing_source_or_markdown_pages(tmp_path):
    assert comparator._parse_pages("19,24,31,19") == (19, 24, 31)
    with pytest.raises(ValueError, match="absent"):
        comparator._select_feature_pages({"pages": [{"page_index": 0}]}, (2,))
    with pytest.raises(ValueError, match="Markdown pages"):
        comparator._select_markdown_pages("## Página 1\nA", (2,))
