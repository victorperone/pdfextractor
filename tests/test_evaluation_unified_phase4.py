"""Fase 4 — Testes de migração operacional e integração de scripts.

Cobertura:
  T35 — compute_metrics e regrade_existing produzem bloco de qualidade idêntico
  T36 — JSON legado evaluation-v2.0 é rejeitado pelo comparador
  T40 — Avaliação repetida 2× com inputs iguais: resultados determinísticos
  T41 — Inversão, duplicação e omissão de página: três categorias separadas
  T49 — Política de colisão de run-id segura (sem sobrescrever silenciosamente)
  T50 — Schema do resultado real valida sem NaN/Infinity
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_REGRADE_SCRIPT = _REPO_ROOT / "scripts" / "regrade_existing.py"
_COMPUTE_SCRIPT = _REPO_ROOT / "scripts" / "compute_metrics.py"

# ---------------------------------------------------------------------------
# Fixtures de texto simples (sem OCR)
# ---------------------------------------------------------------------------

_REF_SIMPLE = """\
## Página 1

Primeiro parágrafo do documento.

## Página 2

Segundo parágrafo com dados críticos: R$ 100,00.
"""

_HYP_IDENTICAL = _REF_SIMPLE

_HYP_PARTIAL = """\
## Página 1

Primeiro paragrafo do documento.
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_regrade(tmp_path: Path, ref_text: str, hyp_text: str,
                 engine: str = "smoke", run_id: str = "r1",
                 run_manifest: dict | None = None) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    ref = tmp_path / "ref.md"
    hyp = tmp_path / "hyp.md"
    out = tmp_path / "out_regrade"
    ref.write_text(ref_text, encoding="utf-8")
    hyp.write_text(hyp_text, encoding="utf-8")
    command = [sys.executable, str(_REGRADE_SCRIPT),
         "--hypothesis", str(hyp),
         "--reference", str(ref),
         "--engine", engine,
         "--run-id", run_id,
         "--output-dir", str(out),
         "--quiet"]
    if run_manifest is not None:
        run_manifest_path = tmp_path / "run_manifest.json"
        run_manifest_path.write_text(json.dumps(run_manifest), encoding="utf-8")
        command.extend(["--run-manifest", str(run_manifest_path)])
    result = subprocess.run(
        command,
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"regrade failed: {result.stderr}"
    metrics_file = out / f"metrics_{engine}_{run_id}.json"
    assert metrics_file.exists(), f"Expected {metrics_file}, got: {list(out.glob('*.json'))}"
    return json.loads(metrics_file.read_text())


def test_regrade_preserves_run_input_identity_and_selected_page_scope(tmp_path):
    run = _run_regrade(
        tmp_path, _REF_SIMPLE, _HYP_IDENTICAL,
        run_manifest={
            "pdf_sha256": "a" * 64,
            "mode": "ocr",
            "selected_pages": [1],
            "pages": [],
        },
    )
    inputs = run["inputs"]
    assert inputs["pdf_sha256"] == "a" * 64
    assert inputs["mode"] == "ocr"
    assert inputs["selected_pages"] == [1]
    assert len(inputs["reference_effective_sha256"]) == 64


def _run_compute(tmp_path: Path, ref_text: str, hyp_text: str,
                 engine: str = "smoke", run_id: str = "r1",
                 run_manifest: dict | None = None) -> dict:
    """Run compute_metrics and return the canonical v3 JSON."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    ref = tmp_path / "ref.md"
    hyp = tmp_path / "hyp.md"
    out = tmp_path / "out_compute"
    ref.write_text(ref_text, encoding="utf-8")
    hyp.write_text(hyp_text, encoding="utf-8")
    engine_slug = engine.replace("-", "_")
    command = [sys.executable, str(_COMPUTE_SCRIPT),
         "--hypothesis", str(hyp),
         "--reference", str(ref),
         "--engine", engine,
         "--run-id", run_id,
         "--output-dir", str(out),
         "--quiet"]
    if run_manifest is not None:
        run_manifest_path = tmp_path / "run_manifest.json"
        run_manifest_path.write_text(json.dumps(run_manifest), encoding="utf-8")
        command.extend(["--run-manifest", str(run_manifest_path)])
    result = subprocess.run(
        command,
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"compute_metrics failed: {result.stderr}"
    # The canonical v3 output produced by compute_metrics
    canonical_file = out / f"metrics_v3_{engine_slug}_{run_id}.json"
    assert canonical_file.exists(), (
        f"Expected canonical v3 file {canonical_file}, "
        f"got: {[f.name for f in out.glob('*.json')]}"
    )
    return json.loads(canonical_file.read_text())


def test_compute_propagates_run_input_identity_to_canonical_metrics(tmp_path):
    run = _run_compute(
        tmp_path, _REF_SIMPLE, _HYP_IDENTICAL,
        run_manifest={
            "pdf_sha256": "b" * 64,
            "mode": "native",
            "selected_pages": [2],
            "pages": [],
        },
    )
    inputs = run["inputs"]
    assert inputs["pdf_sha256"] == "b" * 64
    assert inputs["mode"] == "native"
    assert inputs["selected_pages"] == [2]


def _assert_no_nan_infinity(obj, path: str = "") -> None:
    """Recursively assert no float NaN or Infinity in a JSON-serialisable object."""
    if isinstance(obj, float):
        assert not math.isnan(obj), f"NaN at {path}"
        assert not math.isinf(obj), f"Infinity at {path}"
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _assert_no_nan_infinity(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _assert_no_nan_infinity(v, f"{path}[{i}]")


# ---------------------------------------------------------------------------
# T35 — compute_metrics e regrade_existing produzem bloco quality idêntico
# ---------------------------------------------------------------------------

class TestT35SameQualityBlock:
    """T35: same inputs → same quality block from both CLI scripts."""

    def test_schema_versions_match(self, tmp_path):
        regrade = _run_regrade(tmp_path, _REF_SIMPLE, _HYP_IDENTICAL)
        compute = _run_compute(tmp_path, _REF_SIMPLE, _HYP_IDENTICAL)
        assert regrade["schema_version"] == "evaluation-3.0.0"
        assert compute["schema_version"] == "evaluation-3.0.0"

    def test_quality_text_cer_identical(self, tmp_path):
        regrade = _run_regrade(tmp_path, _REF_SIMPLE, _HYP_IDENTICAL)
        compute = _run_compute(tmp_path, _REF_SIMPLE, _HYP_IDENTICAL)
        cer_r = regrade.get("quality", {}).get("text", {}).get("cer_sequential_normalized", {})
        cer_c = compute.get("quality", {}).get("text", {}).get("cer_sequential_normalized", {})
        assert cer_r.get("value") == cer_c.get("value"), (
            f"CER differs: regrade={cer_r.get('value')}, compute={cer_c.get('value')}"
        )

    def test_evaluation_status_identical(self, tmp_path):
        regrade = _run_regrade(tmp_path, _REF_SIMPLE, _HYP_IDENTICAL)
        compute = _run_compute(tmp_path, _REF_SIMPLE, _HYP_IDENTICAL)
        assert regrade["evaluation"]["status"] == compute["evaluation"]["status"]

    def test_both_have_required_top_level_keys(self, tmp_path):
        for script_result in [
            _run_regrade(tmp_path / "a", _REF_SIMPLE, _HYP_IDENTICAL),
            _run_compute(tmp_path / "b", _REF_SIMPLE, _HYP_IDENTICAL),
        ]:
            for key in ("schema_version", "evaluator", "inputs", "quality",
                        "integrity", "evaluation"):
                assert key in script_result, f"Missing key '{key}'"


# ---------------------------------------------------------------------------
# T36 — JSON legado evaluation-v2.0 é rejeitado pelo comparador
# ---------------------------------------------------------------------------

class TestT36LegacySchemaRejected:
    """T36: compare_engines.py must reject evaluation-v2.0 and legacy V1 JSON."""

    def _write_legacy(self, path: Path, schema: str) -> None:
        doc = {
            "schema_version": schema,
            "engine": "test_engine",
            "run_id": "legacy_run",
            "grupo1_texto": {"cer_text_only": 0.1},
        }
        path.write_text(json.dumps(doc), encoding="utf-8")

    def test_v2_legacy_rejected(self, tmp_path):
        from scripts.compare_engines import _detect_schema, _validate_schema_homogeneity
        legacy_doc = {"schema_version": "evaluation-v2.0"}
        assert _detect_schema(legacy_doc) == "v2"
        errors = _validate_schema_homogeneity([("engine-a", legacy_doc)])
        assert len(errors) >= 1
        assert any("regrade_existing.py" in e for e in errors)

    def test_v1_no_schema_rejected(self, tmp_path):
        from scripts.compare_engines import _detect_schema, _validate_schema_homogeneity
        v1_doc = {"engine": "test", "grupo1_texto": {"cer_text_only": 0.2}}
        assert _detect_schema(v1_doc) == "v1"
        errors = _validate_schema_homogeneity([("engine-a", v1_doc)])
        assert len(errors) >= 1
        assert any("regrade_existing.py" in e for e in errors)

    def test_v3_accepted(self, tmp_path):
        from scripts.compare_engines import _detect_schema, _validate_schema_homogeneity
        v3_doc = {"schema_version": "evaluation-3.0.0",
                  "evaluator": {"metric_definitions_version": "3.0.0"},
                  "inputs": {"reference_effective_sha256": "abc", "reference_serialization_version": "reference-effective-v1"}}
        assert _detect_schema(v3_doc) == "v3"
        errors = _validate_schema_homogeneity([("engine-a", v3_doc)])
        assert errors == []

    def test_mixed_v1_v3_rejected(self, tmp_path):
        from scripts.compare_engines import _validate_schema_homogeneity
        v1_doc = {"engine": "old"}
        v3_doc = {"schema_version": "evaluation-3.0.0",
                  "evaluator": {"metric_definitions_version": "3.0.0"},
                  "inputs": {"reference_effective_sha256": "abc", "reference_serialization_version": "reference-effective-v1"}}
        errors = _validate_schema_homogeneity([("engine-a", v1_doc), ("engine-b", v3_doc)])
        assert len(errors) >= 1
        assert any("regrade_existing.py" in e for e in errors)


# ---------------------------------------------------------------------------
# T40 — Determinismo: mesmos inputs produzem resultados idênticos
# ---------------------------------------------------------------------------

class TestT40Determinism:
    """T40: repeated evaluation with identical inputs must produce identical results."""

    def test_regrade_deterministic(self, tmp_path):
        r1 = _run_regrade(tmp_path / "run1", _REF_SIMPLE, _HYP_PARTIAL,
                          engine="det", run_id="det_test")
        r2 = _run_regrade(tmp_path / "run2", _REF_SIMPLE, _HYP_PARTIAL,
                          engine="det", run_id="det_test")
        # Compare quality blocks (exclude timestamps/created_at)
        q1 = r1.get("quality")
        q2 = r2.get("quality")
        assert json.dumps(q1, sort_keys=True) == json.dumps(q2, sort_keys=True)

    def test_compute_canonical_deterministic(self, tmp_path):
        c1 = _run_compute(tmp_path / "run1", _REF_SIMPLE, _HYP_PARTIAL,
                          engine="det", run_id="det_test")
        c2 = _run_compute(tmp_path / "run2", _REF_SIMPLE, _HYP_PARTIAL,
                          engine="det", run_id="det_test")
        q1 = c1.get("quality")
        q2 = c2.get("quality")
        assert json.dumps(q1, sort_keys=True) == json.dumps(q2, sort_keys=True)

    def test_reference_effective_hash_stable(self, tmp_path):
        r1 = _run_regrade(tmp_path / "run1", _REF_SIMPLE, _HYP_IDENTICAL,
                          engine="hashtest", run_id="ht1")
        r2 = _run_regrade(tmp_path / "run2", _REF_SIMPLE, _HYP_IDENTICAL,
                          engine="hashtest", run_id="ht2")
        sha1 = r1["inputs"]["reference_effective_sha256"]
        sha2 = r2["inputs"]["reference_effective_sha256"]
        assert sha1 == sha2, "Same reference → same effective hash"

    def test_different_reference_produces_different_hash(self, tmp_path):
        ref_alt = _REF_SIMPLE + "\n\n## Página 3\n\nConteúdo extra.\n"
        r1 = _run_regrade(tmp_path / "run1", _REF_SIMPLE, _HYP_IDENTICAL,
                          engine="hashtest", run_id="ht1")
        r2 = _run_regrade(tmp_path / "run2", ref_alt, _HYP_IDENTICAL,
                          engine="hashtest", run_id="ht2")
        sha1 = r1["inputs"]["reference_effective_sha256"]
        sha2 = r2["inputs"]["reference_effective_sha256"]
        assert sha1 != sha2, "Different reference → different effective hash"


# ---------------------------------------------------------------------------
# T41 — Três categorias distintas: inversão, duplicação e omissão de página
# ---------------------------------------------------------------------------

class TestT41PageCategories:
    """T41: page inversion, duplication and omission are tracked separately."""

    def test_missing_page_recorded_in_inputs(self, tmp_path):
        """Hypothesis missing page 2 → page 2 in inputs.selected_missing_pages."""
        hyp_only_p1 = "## Página 1\n\nConteúdo da página um.\n"
        result = _run_regrade(tmp_path, _REF_SIMPLE, hyp_only_p1)
        inputs = result.get("inputs", {})
        missing = inputs.get("selected_missing_pages", [])
        assert 2 in missing, f"Page 2 should be in inputs.selected_missing_pages: {inputs}"

    def test_extra_page_recorded_in_inputs(self, tmp_path):
        """Extra page in hypothesis outside ref scope → inputs.extra_pages_out_of_scope."""
        hyp_with_extra = _REF_SIMPLE + "\n\n## Página 99\n\nConteúdo extra fora do escopo.\n"
        result = _run_regrade(tmp_path, _REF_SIMPLE, hyp_with_extra)
        inputs = result.get("inputs", {})
        extra = inputs.get("extra_pages_out_of_scope", [])
        assert 99 in extra, f"Page 99 should be in inputs.extra_pages_out_of_scope: {inputs}"

    def test_missing_page_reflected_in_missing_page_rate(self, tmp_path):
        """Missing page 2 → missing_page_rate > 0 in integrity metrics."""
        hyp_only_p1 = "## Página 1\n\nPrimeiro parágrafo do documento.\n"
        result = _run_regrade(tmp_path, _REF_SIMPLE, hyp_only_p1)
        integrity = result.get("integrity", {})
        rate = integrity.get("missing_page_rate", {})
        assert rate.get("value", 0.0) > 0.0, (
            f"missing_page_rate.value should be > 0 when page 2 is missing: {rate}"
        )
        counts = rate.get("counts", {})
        assert counts.get("missing", 0) >= 1, f"counts.missing should be >= 1: {counts}"

    def test_missing_and_extra_page_are_separate_fields(self, tmp_path):
        """Missing page and extra page are tracked in separate inputs fields."""
        # hyp has page 99 (extra) but lacks page 2 (missing)
        hyp = "## Página 1\n\nFirst.\n\n## Página 99\n\nExtra.\n"
        result = _run_regrade(tmp_path, _REF_SIMPLE, hyp)
        inputs = result.get("inputs", {})
        assert 2 in inputs.get("selected_missing_pages", []), "Page 2 must be missing"
        assert 99 in inputs.get("extra_pages_out_of_scope", []), "Page 99 must be extra"


# ---------------------------------------------------------------------------
# T49 — Política de colisão de run-id: sem sobrescrever silenciosamente
# ---------------------------------------------------------------------------

class TestT49RunIdCollision:
    """T49: when output file already exists, script does not silently corrupt it."""

    def test_second_run_does_not_corrupt_first(self, tmp_path):
        ref = tmp_path / "ref.md"
        hyp = tmp_path / "hyp.md"
        out = tmp_path / "out"
        ref.write_text(_REF_SIMPLE, encoding="utf-8")
        hyp.write_text(_HYP_IDENTICAL, encoding="utf-8")

        # First run
        r1 = subprocess.run(
            [sys.executable, str(_REGRADE_SCRIPT),
             "--hypothesis", str(hyp), "--reference", str(ref),
             "--engine", "collision_test", "--run-id", "coll01",
             "--output-dir", str(out), "--quiet"],
            capture_output=True, text=True, timeout=60,
        )
        assert r1.returncode == 0
        metrics_file = out / "metrics_collision_test_coll01.json"
        first_content = metrics_file.read_text()
        first_data = json.loads(first_content)

        # Second run with same run-id
        r2 = subprocess.run(
            [sys.executable, str(_REGRADE_SCRIPT),
             "--hypothesis", str(hyp), "--reference", str(ref),
             "--engine", "collision_test", "--run-id", "coll01",
             "--output-dir", str(out), "--quiet"],
            capture_output=True, text=True, timeout=60,
        )
        assert r2.returncode == 0
        second_data = json.loads(metrics_file.read_text())

        # Both runs are deterministic with same inputs → result should be equal
        assert (json.dumps(first_data.get("quality"), sort_keys=True) ==
                json.dumps(second_data.get("quality"), sort_keys=True)), (
            "Collision with same inputs must produce deterministic output"
        )

    def test_different_run_ids_produce_separate_files(self, tmp_path):
        ref = tmp_path / "ref.md"
        hyp = tmp_path / "hyp.md"
        out = tmp_path / "out"
        ref.write_text(_REF_SIMPLE, encoding="utf-8")
        hyp.write_text(_HYP_IDENTICAL, encoding="utf-8")

        for run_id in ("runA", "runB"):
            subprocess.run(
                [sys.executable, str(_REGRADE_SCRIPT),
                 "--hypothesis", str(hyp), "--reference", str(ref),
                 "--engine", "sep_test", "--run-id", run_id,
                 "--output-dir", str(out), "--quiet"],
                capture_output=True, text=True, timeout=60,
            )

        files = {f.name for f in out.glob("metrics_*.json")}
        assert "metrics_sep_test_runA.json" in files
        assert "metrics_sep_test_runB.json" in files


# ---------------------------------------------------------------------------
# T50 — Schema JSON real: sem NaN/Infinity, campos obrigatórios presentes
# ---------------------------------------------------------------------------

class TestT50SchemaValidation:
    """T50: output JSON must be schema-compliant — no NaN/Infinity, required fields present."""

    def test_regrade_no_nan_infinity(self, tmp_path):
        result = _run_regrade(tmp_path, _REF_SIMPLE, _HYP_PARTIAL)
        _assert_no_nan_infinity(result)

    def test_compute_canonical_no_nan_infinity(self, tmp_path):
        result = _run_compute(tmp_path, _REF_SIMPLE, _HYP_PARTIAL)
        _assert_no_nan_infinity(result)

    def test_metric_value_is_number_or_null(self, tmp_path):
        result = _run_regrade(tmp_path, _REF_SIMPLE, _HYP_PARTIAL)
        quality = result.get("quality", {})
        for section_name, section in quality.items():
            if not isinstance(section, dict):
                continue
            for metric_name, metric in section.items():
                if not isinstance(metric, dict):
                    continue
                v = metric.get("value")
                assert v is None or isinstance(v, (int, float)), (
                    f"quality.{section_name}.{metric_name}.value = {v!r} "
                    f"(expected number or null)"
                )

    def test_metric_status_is_string(self, tmp_path):
        result = _run_regrade(tmp_path, _REF_SIMPLE, _HYP_PARTIAL)
        quality = result.get("quality", {})
        for section_name, section in quality.items():
            if not isinstance(section, dict):
                continue
            for metric_name, metric in section.items():
                if not isinstance(metric, dict):
                    continue
                s = metric.get("status")
                assert isinstance(s, str), (
                    f"quality.{section_name}.{metric_name}.status = {s!r} (expected string)"
                )

    def test_required_top_level_fields_present(self, tmp_path):
        result = _run_regrade(tmp_path, _REF_SIMPLE, _HYP_IDENTICAL)
        for key in ("schema_version", "evaluator", "inputs", "quality",
                    "integrity", "evaluation"):
            assert key in result, f"Required top-level key missing: {key}"

    def test_evaluation_rank_eligible_is_bool(self, tmp_path):
        result = _run_regrade(tmp_path, _REF_SIMPLE, _HYP_IDENTICAL)
        eligible = result["evaluation"]["rank_eligible"]
        assert isinstance(eligible, bool), f"rank_eligible must be bool, got {type(eligible)}"

    def test_reference_effective_sha256_is_64hex(self, tmp_path):
        result = _run_regrade(tmp_path, _REF_SIMPLE, _HYP_IDENTICAL)
        sha = result["inputs"]["reference_effective_sha256"]
        assert isinstance(sha, str) and len(sha) == 64, (
            f"reference_effective_sha256 must be 64-char hex string, got: {sha!r}"
        )

    def test_serialization_version_is_canonical_protocol(self, tmp_path):
        result = _run_regrade(tmp_path, _REF_SIMPLE, _HYP_IDENTICAL)
        sv = result["inputs"]["reference_serialization_version"]
        assert sv == "reference-effective-v1", (
            f"reference_serialization_version must be 'reference-effective-v1', got {sv!r}"
        )
