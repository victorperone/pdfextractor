"""Input, scope and CLI regression tests. No PDFs or OCR are invoked."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from structured_pdf_text.evaluation import EvaluationRequest, evaluate
from structured_pdf_text.evaluation.reference_inputs import (
    document_pages, load_reference_text, manifest_pages, selected_pages_from_run,
)

ROOT = Path(__file__).resolve().parents[1]
CLIS = ("compute_metrics.py", "regrade_existing.py")
PAGE_ONE = "# Relatório\n\nTexto correto. R$ 10,00\n\n- Item"
REFERENCE = f"## Página 1\n\n{PAGE_ONE}\n\n## Página 2\n\n# Fora\n\nR$ 999,00"
HYPOTHESIS = f"## Página 1\n\n{PAGE_ONE}"


def cli(script, *args):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script), *map(str, args)],
        cwd=ROOT, text=True, capture_output=True, timeout=30,
    )


def inputs(tmp_path, reference=REFERENCE, hypothesis=HYPOTHESIS, selected=(1,)):
    for name, text in (("ref.md", reference), ("hyp.md", hypothesis)):
        (tmp_path / name).write_text(text, encoding="utf-8")
    run = {
        "selected_pages": list(selected), "pdf_sha256": "a" * 64,
        "mode": "ocr", "benchmark_protocol_id": "synthetic-v1",
        "benchmark_status": "partial", "stability_status": "recovered",
        "recovery_count": 3, "engine_identity": {"profile": "synthetic"},
        "timings": {"ocr_s": 2}, "memory": {"peak_rss_mb": 64},
    }
    (tmp_path / "run.json").write_text(json.dumps(run), encoding="utf-8")
    return ["--hypothesis", tmp_path / "hyp.md", "--reference", tmp_path / "ref.md",
            "--run-manifest", tmp_path / "run.json", "--engine", "audit",
            "--run-id", "r1", "--output-dir", tmp_path / "out", "--quiet"]


def artifact(tmp_path, script):
    prefix = "metrics_v3" if script == "compute_metrics.py" else "metrics"
    return tmp_path / "out" / f"{prefix}_audit_r1.json"


def test_selected_scope_limits_every_quality_group_and_integrity():
    request = EvaluationRequest(REFERENCE, HYPOTHESIS, selected_page_scope=[1])
    result = evaluate(request)
    baseline = evaluate(replace(request, ref_text=HYPOTHESIS))
    assert result["quality"] == baseline["quality"]
    assert result["integrity"] == baseline["integrity"]
    assert result["inputs"]["reference_effective_sha256"] == baseline["inputs"]["reference_effective_sha256"]
    assert result["inputs"]["selected_missing_pages"] == []
    assert result["integrity"]["missing_page_rate"]["value"] == 0
    assert result["quality"]["text"]["cer_sequential_normalized"]["value"] == 0


def test_changes_outside_scope_cannot_change_quality_or_integrity():
    request = EvaluationRequest(REFERENCE, HYPOTHESIS, selected_page_scope=[1])
    first = evaluate(request)
    second = evaluate(replace(
        request, ref_text=REFERENCE.replace("R$ 999,00", "# Outro gabarito\n\n30%"),
        hyp_text=HYPOTHESIS + "\n\n## Página 2\n\n```\nCódigo sem fechamento",
    ))
    assert first["quality"] == second["quality"]
    assert first["integrity"] == second["integrity"]
    assert first["inputs"]["reference_effective_sha256"] == second["inputs"]["reference_effective_sha256"]
    assert second["inputs"]["extra_pages_out_of_scope"] == [2]


def test_missing_selected_page_remains_penalised():
    result = evaluate(EvaluationRequest(REFERENCE, HYPOTHESIS, selected_page_scope=[1, 2]))
    assert result["inputs"]["selected_missing_pages"] == [2]
    assert result["integrity"]["missing_page_rate"]["value"] == 0.5
    assert result["quality"]["text"]["cer_sequential_normalized"]["value"] > 0
    assert result["quality"]["structure"]["heading_detection_f1"]["value"] < 1
    assert result["quality"]["critical_data"]["currency"]["value"] < 1


def test_reference_page_serialization_order_agrees_with_effective_hash():
    first_page = "## Página 1\n\nAlfa."
    second_page = "## Página 2\n\nBeta."
    ordered = first_page + "\n\n" + second_page
    inverted = second_page + "\n\n" + first_page
    baseline = evaluate(EvaluationRequest(ordered, ordered))
    reordered_reference = evaluate(EvaluationRequest(inverted, ordered))
    assert baseline["inputs"]["reference_effective_sha256"] == reordered_reference["inputs"]["reference_effective_sha256"]
    assert baseline["quality"] == reordered_reference["quality"]
    assert baseline["integrity"] == reordered_reference["integrity"]
    reordered_hypothesis = evaluate(EvaluationRequest(ordered, inverted))
    assert reordered_hypothesis["quality"]["reading_order"]["pairwise_reading_order_accuracy"]["value"] < 1


@pytest.mark.parametrize("selected", [[], [3], [0], [-1], [True], [1.5], [None], "1"])
def test_invalid_or_unreferenced_scope_is_rejected(selected):
    with pytest.raises(ValueError):
        evaluate(EvaluationRequest(REFERENCE, HYPOTHESIS, selected_page_scope=selected))


@pytest.mark.parametrize("expected", [None, 42, {}, []])
def test_ground_truth_must_be_string(expected):
    with pytest.raises(ValueError, match="expected_markdown"):
        manifest_pages({"pages": [{"page": 1, "expected_markdown": expected}]})


@pytest.mark.parametrize("pages", [
    [], [{"page": 1}], [{"page": True, "expected_markdown": ""}],
    [{"page": 0, "expected_markdown": ""}],
    [{"page": 1, "expected_markdown": ""}, {"page": 1, "expected_markdown": ""}],
    [{"page": 1, "page_num": 2, "expected_markdown": ""}],
    [{"page": 1, "expected_markdown": "## Página 2\nTexto"}],
    [{"page": 1, "expected_markdown": "## Página 1\nA\n## Página 2\nB"}],
    [{"page": 1, "expected_markdown": "## Página 1\nA\n## Página 1\nB"}],
])
def test_invalid_manifest_pages_are_rejected(pages):
    with pytest.raises(ValueError):
        manifest_pages({"pages": pages})


def test_explicit_blank_and_embedded_marker_are_preserved():
    assert manifest_pages({"pages": [
        {"page": 2, "expected_markdown": ""},
        {"page": 3, "expected_markdown": "## Página 003 |\n\nAção"},
    ]}) == {2: "", 3: "Ação"}
    assert document_pages("Texto sem marcador") == {1: "Texto sem marcador"}
    assert document_pages("") == {1: ""}


@pytest.mark.parametrize("metadata", [
    {"selected_pages": []}, {"selected_pages": None}, {"selected_pages": [True]},
    {"pages": []}, {"pages": [{"page": 0}]}, {"pages": [None]},
])
def test_invalid_run_scope_is_rejected(metadata):
    with pytest.raises(ValueError):
        selected_pages_from_run(metadata)


def test_explicit_scope_takes_precedence_over_operational_page_records():
    assert selected_pages_from_run({"selected_pages": [2, 1, 2], "pages": []}) == [1, 2]
    assert selected_pages_from_run({"pages": [{"page": 2}, {"page": 1}]}) == [1, 2]
    assert selected_pages_from_run({"benchmark_status": "partial"}) is None


@pytest.mark.parametrize("script", CLIS)
def test_cli_scope_and_operational_metadata_match_public_api(tmp_path, script):
    result = cli(script, *inputs(tmp_path))
    assert result.returncode == 0, result.stderr
    data = json.loads(artifact(tmp_path, script).read_text())
    direct = evaluate(EvaluationRequest(REFERENCE, HYPOTHESIS, selected_page_scope=[1]))
    assert data["quality"] == direct["quality"]
    assert data["integrity"] == direct["integrity"]
    assert data["inputs"]["selected_missing_pages"] == []
    operational = data["operational"]
    for key, expected in {"recovery_count": 3, "stability_status": "recovered",
                          "benchmark_status": "partial", "selected_pages": [1],
                          "timings": {"ocr_s": 2}, "memory": {"peak_rss_mb": 64}}.items():
        assert operational[key] == expected
    if script == "compute_metrics.py":
        legacy = json.loads((tmp_path / "out" / "metrics_audit_r1.json").read_text())
        assert legacy["grupo5_dados_criticos"]["currency_f1"] == 1


@pytest.mark.parametrize("script", CLIS)
@pytest.mark.parametrize("embedded", [False, True])
def test_manifest_and_markdown_reference_produce_identical_quality(tmp_path, script, embedded):
    args = inputs(tmp_path, reference=HYPOTHESIS)
    assert cli(script, *args).returncode == 0
    baseline = json.loads(artifact(tmp_path, script).read_text())
    manifesto = tmp_path / "manifest.json"
    manifesto.write_text(json.dumps({"pages": [
        {"page": 1, "expected_markdown": HYPOTHESIS if embedded else PAGE_ONE},
    ]}), encoding="utf-8")
    index = args.index("--reference")
    args[index:index + 2] = ["--manifesto", manifesto]
    # These have different source identities, even though effective ground
    # truth/quality agree. Each publication needs its own output namespace.
    manifest_output = tmp_path / "manifest_out"
    args[args.index("--output-dir") + 1] = manifest_output
    result = cli(script, *args)
    assert result.returncode == 0, result.stderr
    actual = json.loads((manifest_output / artifact(tmp_path, script).name).read_text())
    assert actual["quality"] == baseline["quality"]
    assert actual["inputs"]["reference_effective_sha256"] == baseline["inputs"]["reference_effective_sha256"]


@pytest.mark.parametrize("script", CLIS)
def test_duplicate_markers_fail_without_overwriting_prior_artifact(tmp_path, script):
    args = inputs(tmp_path)
    assert cli(script, *args).returncode == 0
    previous = artifact(tmp_path, script).read_bytes()
    (tmp_path / "hyp.md").write_text(HYPOTHESIS + "\n\n## Página 1\nOutra", encoding="utf-8")
    result = cli(script, *args)
    assert result.returncode != 0
    assert "Duplicate page" in result.stderr
    assert artifact(tmp_path, script).read_bytes() == previous


@pytest.mark.parametrize("script", CLIS)
def test_cli_does_not_accept_metadata_as_blank_ground_truth(tmp_path, script):
    args = inputs(tmp_path)
    manifesto = tmp_path / "metadata.json"
    manifesto.write_text('{"pages": [{"page": 1, "family": "plain"}]}')
    i = args.index("--reference")
    args[i:i + 2] = ["--manifesto", manifesto]
    result = cli(script, *args)
    assert result.returncode != 0
    assert "expected_markdown" in result.stderr
    assert not artifact(tmp_path, script).exists()
    # The same metadata may accompany a real Markdown ground truth.
    args += ["--reference", tmp_path / "ref.md"]
    result = cli(script, *args)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("script", CLIS)
@pytest.mark.parametrize("missing", ["ref.md", "run.json"])
def test_missing_explicit_reference_or_run_manifest_fails(tmp_path, script, missing):
    args = inputs(tmp_path)
    (tmp_path / missing).unlink()
    result = cli(script, *args)
    assert result.returncode != 0
    assert not artifact(tmp_path, script).exists()


@pytest.mark.parametrize("script", CLIS)
def test_implicit_single_page_is_supported(tmp_path, script):
    result = cli(script, *inputs(tmp_path, reference="Texto.", hypothesis="Texto."))
    assert result.returncode == 0, result.stderr
    data = json.loads(artifact(tmp_path, script).read_text())
    assert data["inputs"]["selected_pages"] == [1]
    assert data["quality"]["text"]["cer_sequential_normalized"]["value"] == 0


def test_repository_manifests_have_explicit_ground_truth_contract():
    # Only read existing data; do not evaluate the full corpus here.
    v4 = ROOT / "corpus/V4/Corpus_Stress_OCR_Markdown_V4_MANIFESTO.json"
    v3 = ROOT / "corpus/V3/Document_AI_V3_MANIFESTO.json"
    if not v4.is_file() or not v3.is_file():
        pytest.skip("Versioned local corpora are unavailable")
    pages = document_pages(load_reference_text(None, v4))
    assert len(pages) == 224
    with pytest.raises(ValueError, match="expected_markdown"):
        load_reference_text(None, v3)


@pytest.mark.parametrize("name, expression", [
    ("run_benchmark.sh", 'metrics="$OUT_DIR/metrics_v3_${config_slug}_${run_id}.json"'),
    ("run_benchmark.ps1", '$metrics    = "$OutDir\\metrics_v3_${configSlug}_${runId}.json"'),
    ("run_v3_v4_comparison.sh", 'metric_file="$out_dir/metrics_v3_${config//-/_}_${run_id}.json"'),
])
def test_runner_selects_canonical_artifact(name, expression):
    assert expression in (ROOT / "scripts" / name).read_text()


@pytest.mark.parametrize("failure", ["", "compute_metrics.py", "compare_engines.py"])
@pytest.mark.skipif(os.name != "posix", reason="Bash runner integration requires Linux/WSL")
def test_bash_runner_compute_compare_integration_without_extraction(tmp_path, failure):
    """Run the Bash loop with synthetic extraction and the real metric CLIs."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    models = tmp_path / "models"
    models.mkdir()
    for name in ("PP-OCRv6_det_medium.onnx", "PP-OCRv6_rec_medium.onnx",
                 "ppocrv6_medium_dict.txt", "ch_ppocr_mobile_v2.0_cls_mobile.onnx"):
        (models / name).write_text("placeholder; no inference")
    runner = scripts / "run_benchmark.sh"
    source = (ROOT / "scripts" / "run_benchmark.sh").read_text()
    source = source.replace('_RAPIDOCR_CACHE_HOME="${RAPIDOCR_CACHE_HOME:-$HOME/.cache/pdfextractor}"',
                            "_RAPIDOCR_CACHE_HOME=" + shlex.quote(str(tmp_path)))
    runner.write_text(source)
    fake_python = tmp_path / "python"
    # The extractor and preflight are intercepted. The real evaluator and
    # comparator execute against the synthetic Markdown produced below.
    fake_python.write_text(f"""#!{sys.executable}
import json, os, subprocess, sys
from pathlib import Path
name = Path(sys.argv[1]).name
with open(os.environ['AUDIT_CALLS'], 'a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
if name == os.environ.get('AUDIT_FAILURE'):
    raise SystemExit(17)
if name == 'evaluate_e2e.py':
    args = sys.argv[2:]
    def value(key):
        return args[args.index(key) + 1]
    out = Path(value('--output-dir'))
    engine, run_id = value('--engine').replace('-', '_'), value('--run-id')
    (out / f'extracted_{{engine}}_{{run_id}}.md').write_text('## Página 1\\n\\nTexto.')
    (out / f'manifesto_e2e_{{engine}}_{{run_id}}.json').write_text(json.dumps({{
        'selected_pages': [1], 'pdf_sha256': 'a' * 64, 'mode': 'ocr',
        'benchmark_protocol_id': 'synthetic-v1', 'benchmark_status': 'valid',
    }}))
elif name in ('compute_metrics.py', 'compare_engines.py'):
    raise SystemExit(subprocess.call([sys.executable, str(Path({str(ROOT)!r}) / 'scripts' / name), *sys.argv[2:]]))
""", encoding="utf-8")
    fake_python.chmod(0o755)
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps({"pages": [{"page": 1, "expected_markdown": "Texto."}]}))
    calls_path = tmp_path / "calls.jsonl"
    out = tmp_path / "out"
    result = subprocess.run([
        "bash", str(runner), "--pdf", str(fixture), "--reference", str(fixture),
        "--manifesto", str(fixture), "--validation", str(fixture),
        "--corpus-lock", str(fixture), "--output-dir", str(out),
        "--configurations", "tesseract,rapidocr-onnxruntime", "--pages", "1",
        "--python", str(fake_python),
    ], env={**os.environ, "AUDIT_CALLS": str(calls_path), "AUDIT_FAILURE": failure},
        capture_output=True, text=True, timeout=45)
    assert calls_path.is_file(), result.stdout + result.stderr
    calls = [json.loads(line) for line in calls_path.read_text().splitlines()]
    comparisons = [args for args in calls if Path(args[0]).name == "compare_engines.py"]
    if failure:
        assert result.returncode != 0
        if failure == "compute_metrics.py":
            assert not comparisons
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(comparisons) == 1
        compared_paths = [Path(arg) for arg in comparisons[0][1:] if arg.endswith(".json")]
        assert len(compared_paths) == 2
        for path in compared_paths:
            assert path.name.startswith("metrics_v3_")
            assert json.loads(path.read_text())["schema_version"] == "evaluation-3.0.0"
        assert (out / "comparison_smoke.md").is_file()


@pytest.mark.parametrize("module_name", ["compute_metrics", "regrade_existing"])
def test_unexpected_canonical_error_is_reported_without_output(tmp_path, monkeypatch, capsys, module_name):
    import importlib
    module = importlib.import_module(f"scripts.{module_name}")
    args = inputs(tmp_path)

    def fail(_request):
        raise RuntimeError("Synthetic canonical failure")

    monkeypatch.setattr(module, "evaluate_document", fail)
    monkeypatch.setattr(sys, "argv", [module_name, *map(str, args)])
    assert module.main() == 1
    assert "Synthetic canonical failure" in capsys.readouterr().err
    assert not list((tmp_path / "out").glob("*.json"))
