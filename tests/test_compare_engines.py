from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from evaluation_contract_helpers import comparison_result

from scripts.compare_engines import (
    _comparability_key,
    _comparability_key_v2,
    _detect_schema,
    _get_value_v2,
    _render_comparison_table,
    _render_comparison_table_v2,
    _validate_comparability,
    _validate_comparability_v2,
    _validate_schema_homogeneity,
)


def _run(**updates):
    data = {
        "pdf_sha256": "pdf-sha",
        "reference_sha256": "reference-sha",
        "manifest_sha256": "manifest-sha",
        "mode": "balanced",
        "benchmark_protocol_id": "protocol-v1",
        "selected_pages": [1, 2],
        "pages_selected": 2,
        "pages_selected_but_missing": [],
        "missing_pages_penalised": False,
        "benchmark_status": "valid",
        "grupo1_texto": {"cer_text_only": 0.0},
    }
    data.update(updates)
    return data


def test_missing_page_and_partial_status_do_not_make_same_input_incomparable():
    complete = _run()
    partial = _run(
        benchmark_status="partial",
        pages_selected_but_missing=[2],
        missing_pages_penalised=True,
        pages_selected_and_present=1,
        grupo1_texto={"cer_text_only": 0.5},
    )

    assert _comparability_key(complete) == _comparability_key(partial)
    assert _validate_comparability(
        [("engine-a", complete), ("engine-b", partial)], allow_partial=False
    ) == []
    report = _render_comparison_table([("engine-a", complete), ("engine-b", partial)])
    assert "partial" in report
    assert "0.5000" in report


def test_different_pdf_is_not_comparable():
    errors = _validate_comparability(
        [("engine-a", _run()), ("engine-b", _run(pdf_sha256="other-pdf"))],
        allow_partial=False,
    )

    assert any("pdf_sha256" in error for error in errors)


def test_different_reference_or_manifest_is_not_comparable():
    errors = _validate_comparability(
        [
            ("engine-a", _run()),
            ("engine-b", _run(reference_sha256="other-reference")),
            ("engine-c", _run(manifest_sha256="other-manifest")),
        ],
        allow_partial=False,
    )

    assert any("reference_sha256" in error for error in errors)
    assert any("manifest_sha256" in error for error in errors)


def test_different_selected_page_sets_are_not_comparable():
    errors = _validate_comparability(
        [("engine-a", _run()), ("engine-b", _run(selected_pages=[3, 4]))],
        allow_partial=False,
    )

    assert any("selected_pages" in error for error in errors)


def test_same_page_count_with_different_page_numbers_is_not_comparable():
    errors = _validate_comparability(
        [("engine-a", _run()), ("engine-b", _run(selected_pages=[10, 11]))],
        allow_partial=False,
    )

    assert any("selected_pages" in error for error in errors)


def test_canonical_comparator_uses_operational_protocol_and_input_page_scope():
    a = _v2_run("engine-a")
    b = _v2_run("engine-b")
    for run in (a, b):
        run["operational"]["benchmark_protocol_id"] = "protocol-v1"
        run["inputs"]["selected_pages"] = [1, 2]

    assert _validate_comparability_v2([("engine-a", a), ("engine-b", b)]) == []


def test_canonical_comparator_rejects_different_benchmark_protocol():
    a = _v2_run("engine-a")
    b = _v2_run("engine-b")
    for run in (a, b):
        run["operational"]["benchmark_protocol_id"] = "protocol-v1"
        run["inputs"]["selected_pages"] = [1, 2]
    b["operational"]["benchmark_protocol_id"] = "protocol-v2"

    errors = _validate_comparability_v2([("engine-a", a), ("engine-b", b)])
    assert any("benchmark_protocol_id" in error for error in errors)


def test_compare_engines_cli_routes_evaluation_3_to_canonical_comparison(tmp_path):
    from structured_pdf_text.evaluation import EvaluationRequest, evaluate

    runs = []
    for engine in ("engine-a", "engine-b"):
        body = "## Página 1\n\nAlfa\n\n## Página 2\n\nBeta"
        run = evaluate(EvaluationRequest(body, body, engine=engine, mode="balanced",
            pdf_sha256="b" * 64, run_metadata={"benchmark_protocol_id": "protocol-v1"}))
        run["inputs"]["manifest_sha256"] = "c" * 64
        path = tmp_path / f"{engine}.json"
        path.write_text(json.dumps(run), encoding="utf-8")
        runs.append(path)
    output = tmp_path / "comparison.md"

    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parents[1] / "scripts" / "compare_engines.py"),
            *(str(path) for path in runs),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert output.exists()
    assert "engine-a" in output.read_text(encoding="utf-8")


def test_comparison_requires_exact_page_list_for_multiple_runs():
    errors = _validate_comparability(
        [("engine-a", _run()), ("engine-b", _run(selected_pages=None))],
        allow_partial=False,
    )

    assert any("exact selected page list" in error for error in errors)


def test_condition_lists_are_split_and_profile_recovery_metadata_is_reported():
    report = _render_comparison_table([
        ("rapidocr-onnx", _run(
            stability_status="recovered",
            recovery_count=2,
            elapsed_s=12.5,
            memory={"peak_rss_bytes": 1024 * 1024},
            engine_identity={
                "runtime": "onnxruntime", "profile": "latin/pt-compatible",
                "language": "pt", "extra": {"rec_model": "latin.onnx"},
            },
            per_page=[{
                "conditions": ["small_text", "low_contrast"],
                "family": "OCR de tabelas",
                "cer_text_only": 0.1,
                "wer": 0.2,
                "deletion_rate": 0.1,
                "currency_f1": 0.9,
                "identifier_f1": 0.8,
                "cell_cer": 0.1,
                "missing_page_rate": 1.0,
                "easyocr_fallback_rate": 0.0,
            }],
        ))
    ])
    assert "small_text" in report and "low_contrast" in report
    assert "OCR de tabelas" in report
    assert "latin/pt-compatible" in report
    assert "recovered" in report and "2" in report
    assert "Peak RSS MB" in report


# ---------------------------------------------------------------------------
# V2 schema detection and homogeneity
# ---------------------------------------------------------------------------

def _v2_run(engine: str = "test_engine", **overrides) -> dict:
    """Minimal valid evaluation-3.0.0 metrics document for testing.

    Named _v2_run for backwards-compatibility with existing test code that
    calls this fixture; the schema is now evaluation-3.0.0 (canonical).
    """
    doc = {
        "schema_version": "evaluation-3.0.0",
        "evaluator": {
            **comparison_result()["evaluator"],
            "engine": engine,
            "run_id": "run001",
        },
        "inputs": {
            "reference_effective_sha256": "a" * 64,
            "reference_serialization_version": "reference-effective-v1",
            "pdf_sha256": "b" * 64,
            "manifest_sha256": "c" * 64,
            "mode": "balanced",
            "hypothesis_sha256": "hyp-sha-xyz",
            "selected_pages": [1, 2, 3],
        },
        "operational": {"benchmark_protocol_id": "protocol-v1"},
        "quality": {
            "text": {
                "cer_sequential_normalized": {"value": 0.05, "status": "ok", "direction": "lower_is_better"},
                "wer_sequential":            {"value": 0.10, "status": "ok", "direction": "lower_is_better"},
                "text_coverage_recall":      {"value": 0.90, "status": "ok", "direction": "higher_is_better"},
            },
            "structure": {
                "heading_detection_f1": {"value": 1.0, "status": "ok", "direction": "higher_is_better"},
            },
            "reading_order": {
                "pairwise_reading_order_accuracy": {"value": 1.0, "status": "ok", "direction": "higher_is_better"},
            },
            "tables": {},
            "critical_data": {
                "currency": {"value": 1.0, "status": "ok", "direction": "higher_is_better"},
            },
        },
        "integrity": {
            "duplicate_excess_rate": {"value": 0.0, "status": "ok", "direction": "lower_is_better"},
        },
        "evaluation": {
            "status": "ok",
            "rank_eligible": True,
            "applicable_metric_count": 10,
            "unavailable_metric_count": 1,
        },
    }
    # Allow deep overrides via dot notation keys
    for key, val in overrides.items():
        doc[key] = val
    return doc


class TestDetectSchema:
    def test_v3_detected(self):
        assert _detect_schema(_v2_run()) == "v3"

    def test_v2_detected_for_legacy_schema(self):
        assert _detect_schema({"schema_version": "evaluation-v2.0"}) == "v2"

    def test_v1_detected_when_no_schema_version(self):
        assert _detect_schema(_run()) == "v1"

    def test_v1_detected_when_wrong_schema_version(self):
        assert _detect_schema({"schema_version": "v1.0"}) == "v1"


class TestSchemaHomogeneity:
    def test_all_v3_passes(self):
        """evaluation-3.0.0 runs are accepted for comparison."""
        errors = _validate_schema_homogeneity([
            ("engine-a", _v2_run("engine-a")),
            ("engine-b", _v2_run("engine-b")),
        ])
        assert errors == []

    def test_all_v2_legacy_rejected(self):
        """evaluation-v2.0 runs are rejected; must re-evaluate with regrade_existing.py."""
        errors = _validate_schema_homogeneity([
            ("engine-a", {"schema_version": "evaluation-v2.0"}),
            ("engine-b", {"schema_version": "evaluation-v2.0"}),
        ])
        assert len(errors) == 2
        assert all("regrade_existing.py" in e for e in errors)

    def test_all_v1_rejected(self):
        """V1 (no schema_version) runs are rejected."""
        errors = _validate_schema_homogeneity([("engine-a", _run()), ("engine-b", _run())])
        assert len(errors) == 2
        assert all("regrade_existing.py" in e for e in errors)

    def test_mixed_v1_v3_rejected(self):
        """V1 and V3 runs cannot be compared — both sides must be v3."""
        errors = _validate_schema_homogeneity([
            ("engine-a", _run()),
            ("engine-b", _v2_run("engine-b")),
        ])
        assert len(errors) >= 1
        assert any("regrade_existing.py" in e for e in errors)


class TestComparabilityV2:
    def test_identical_runs_are_comparable(self):
        errors = _validate_comparability_v2([
            ("engine-a", _v2_run("engine-a")),
            ("engine-b", _v2_run("engine-b")),
        ])
        assert errors == []

    def test_single_run_always_comparable(self):
        assert _validate_comparability_v2([("engine-a", _v2_run())]) == []

    def test_different_reference_effective_sha_rejected(self):
        """Different reference_effective_sha256 → runs are not comparable."""
        a = _v2_run("engine-a")
        b = _v2_run("engine-b")
        b["inputs"]["reference_effective_sha256"] = "different-eff-sha"
        errors = _validate_comparability_v2([("engine-a", a), ("engine-b", b)])
        assert any("reference_effective_sha256" in e for e in errors)

    def test_different_selected_pages_rejected(self):
        a = _v2_run("engine-a")
        b = _v2_run("engine-b")
        b["inputs"]["selected_pages"] = [4, 5, 6]
        errors = _validate_comparability_v2([("engine-a", a), ("engine-b", b)])
        assert any("selected_pages" in e for e in errors)

    def test_different_pdf_manifest_or_mode_rejected(self):
        for field, value in (
            ("pdf_sha256", "other-pdf"),
            ("manifest_sha256", "other-manifest"),
            ("mode", "fast"),
        ):
            a = _v2_run("engine-a")
            b = _v2_run("engine-b")
            b["inputs"][field] = value
            errors = _validate_comparability_v2([("engine-a", a), ("engine-b", b)])
            assert any(field in error for error in errors)

    def test_missing_input_identity_fails_closed(self):
        a = _v2_run("engine-a")
        b = _v2_run("engine-b")
        for run in (a, b):
            run["inputs"]["pdf_sha256"] = None
        errors = _validate_comparability_v2([("engine-a", a), ("engine-b", b)])
        assert sum("pdf_sha256" in error for error in errors) >= 2

    def test_malformed_digest_and_unsupported_reference_protocol_rejected(self):
        a = _v2_run("engine-a")
        b = _v2_run("engine-b")
        b["inputs"]["manifest_sha256"] = "not-a-digest"
        b["inputs"]["reference_serialization_version"] = "reference-effective-v0"
        errors = _validate_comparability_v2([("engine-a", a), ("engine-b", b)])
        assert any("manifest_sha256" in error and "SHA-256" in error for error in errors)
        assert any("reference serialization protocol" in error for error in errors)

    def test_engine_output_failures_do_not_change_comparability(self):
        a = _v2_run("engine-a")
        b = _v2_run("engine-b")
        b["inputs"]["selected_missing_pages"] = [3]
        b["evaluation"]["status"] = "partial"
        assert _validate_comparability_v2([("engine-a", a), ("engine-b", b)]) == []

    def test_comparability_key_is_order_independent(self):
        a = _v2_run("engine-a")
        b = _v2_run("engine-b")
        # Change page order — should still be equal after sorting
        b["inputs"]["selected_pages"] = [3, 1, 2]
        errors = _validate_comparability_v2([("engine-a", a), ("engine-b", b)])
        assert errors == []


class TestGetValueV2:
    def test_reads_quality_text_metric(self):
        data = _v2_run()
        assert _get_value_v2(data, "text.cer_sequential_normalized") == 0.05

    def test_reads_integrity_metric(self):
        data = _v2_run()
        assert _get_value_v2(data, "integrity.duplicate_excess_rate") == 0.0

    def test_returns_none_for_missing_key(self):
        data = _v2_run()
        assert _get_value_v2(data, "text.nonexistent_metric") is None

    def test_returns_none_for_na_metric(self):
        data = _v2_run()
        data["quality"]["text"]["aligned_block_text_cer"] = {"value": None, "status": "not_applicable"}
        assert _get_value_v2(data, "text.aligned_block_text_cer") is None


class TestRenderComparisonTableV2:
    def test_renders_without_error(self):
        report = _render_comparison_table_v2([
            ("engine-a", _v2_run("engine-a")),
            ("engine-b", _v2_run("engine-b")),
        ])
        assert "evaluation-3.0.0" in report
        assert "engine-a" in report and "engine-b" in report

    def test_shows_all_section_headers(self):
        report = _render_comparison_table_v2([("engine-a", _v2_run())])
        assert "MET-TX01" in report
        assert "Estrutura Markdown" in report
        assert "Dados Críticos" in report
        assert "Integridade" in report

    def test_bolds_best_value_higher_is_better(self):
        a = _v2_run("engine-a")
        b = _v2_run("engine-b")
        # Engine-b has better coverage recall
        b["quality"]["text"]["text_coverage_recall"] = {"value": 0.99, "status": "ok", "direction": "higher_is_better"}
        report = _render_comparison_table_v2([("engine-a", a), ("engine-b", b)])
        assert "**0.9900**" in report

    def test_bolds_best_value_lower_is_better(self):
        a = _v2_run("engine-a")
        b = _v2_run("engine-b")
        # Engine-a has lower (better) CER
        a["quality"]["text"]["cer_sequential_normalized"] = {"value": 0.01, "status": "ok", "direction": "lower_is_better"}
        b["quality"]["text"]["cer_sequential_normalized"] = {"value": 0.10, "status": "ok", "direction": "lower_is_better"}
        report = _render_comparison_table_v2([("engine-a", a), ("engine-b", b)])
        assert "**0.0100**" in report

    def test_shows_rank_eligibility_in_metadata(self):
        a = _v2_run("engine-a")
        b = _v2_run("engine-b")
        b["evaluation"]["rank_eligible"] = False
        report = _render_comparison_table_v2([("engine-a", a), ("engine-b", b)])
        assert "sim" in report and "não" in report

    def test_na_values_shown_as_dash(self):
        data = _v2_run("engine-a")
        data["quality"]["tables"] = {}  # no table metrics
        report = _render_comparison_table_v2([("engine-a", data)])
        assert "—" in report
