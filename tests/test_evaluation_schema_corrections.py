"""Formal schema and independent counter/state mutation probes; no extraction."""
from __future__ import annotations

import copy
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from structured_pdf_text.evaluation import (
    EvaluationRequest, evaluate, validate_result, result_schema, EvaluationValidationError,
)
from structured_pdf_text.evaluation.block_alignment import align_blocks
from structured_pdf_text.evaluation.markdown_parser import parse_document
from structured_pdf_text.evaluation.structure_metrics import compute_structure_metrics

ROOT = Path(__file__).resolve().parents[1]


def canonical():
    return evaluate(EvaluationRequest(
        "## Página 1\n\n# Título\n\nR$ 10,00\n\n## Página 2\n\nTexto",
        "## Página 1\n\n# Titulo\n\nR$ 20,00\n\n## Página 2\n\nTexto",
        pdf_sha256="a" * 64, mode="native", run_metadata={"benchmark_protocol_id": "schema-test"},
    ))


def set_path(data, path, value):
    parts = path.split(".")
    target = data
    for key in parts[:-1]:
        target = target[int(key)] if isinstance(target, list) else target[key]
    if isinstance(target, list):
        target[int(parts[-1])] = value
    else:
        target[parts[-1]] = value


def test_packaged_schema_matches_documented_schema_and_metaschema():
    schema = result_schema()
    Draft202012Validator.check_schema(schema)
    assert schema == json.loads((ROOT / "docs/evaluation-v3.schema.json").read_text())
    assert len(schema["x-metric-registry"]) == 69
    schema["properties"].clear()
    assert result_schema()["properties"]


@pytest.mark.parametrize("reference,hypothesis", [
    ("", ""), ("", "R$ 10,00"), ("R$ 10,00", ""),
    ("# Título", "# Titulo"), ("- Alfa\n- Beta", "- Alfa\n- Beto"),
    ("| A | B |\n|---|---|\n| 1 | 2 |", ""),
    ("<table><tr><td>A</td></tr></table>", ""),
    ("```\n**literal**\n```", "Texto"), ("Texto", "```\nmalformado"),
])
def test_real_results_validate_for_absence_errors_and_structures(reference, hypothesis):
    result = evaluate(EvaluationRequest(reference, hypothesis))
    Draft202012Validator(result_schema()).validate(result)
    validate_result(result)
    assert json.loads(json.dumps(result, allow_nan=False)) == result


@pytest.mark.parametrize("path,value", [
    ("schema_version", "evaluation-v2.0"),
    ("evaluator.validation_policy", "unknown"),
    ("evaluator.metric_definitions_version", None),
    ("evaluator.resource_policy.max_structure_edit_cells", True),
    ("inputs.reference_effective_sha256", "x" * 64),
    ("inputs.hypothesis_sha256", "bad"),
    ("inputs.selected_pages", []), ("inputs.selected_pages", [True]),
    ("inputs.selected_pages", [1, 1]), ("inputs.selected_pages", [2, 1]),
    ("inputs.selected_missing_pages", [7]), ("inputs.extra_pages_out_of_scope", [1]),
    ("inputs.total_pages_in_reference", 1),
    ("operational.selected_pages", [1]),
    ("operational.elapsed_s", -1), ("operational.fallback_rate", 1.5),
    ("per_page.0.page", 2), ("per_page.0.selected_but_missing", True),
    ("per_page.0.reference_empty", True), ("per_page.0.hypothesis_empty", True),
    ("per_page.0.conditions", ["blur", "blur"]),
    ("quality.text.cer_markdown_raw.status", "unknown"),
    ("quality.text.cer_markdown_raw.value", None),
    ("quality.text.cer_markdown_raw.value", -1),
    ("quality.text.cer_markdown_raw.value", True),
    ("quality.text.cer_markdown_raw.direction", "higher_is_better"),
    ("quality.text.cer_markdown_raw.normalization_profile", "visible_text"),
    ("quality.text.cer_markdown_raw.definition_id", "MET-TX99"),
    ("quality.text.cer_markdown_raw.scope", "page"),
    ("quality.text.cer_markdown_raw.coverage", 1.1),
    ("quality.text.cer_markdown_raw.counts.D", -1),
    ("quality.text.cer_markdown_raw.counts.S", True),
    ("quality.text.cer_markdown_raw.counts.edit_distance", 999),
    ("quality.text.cer_markdown_raw.counts.hyp_chars", 999),
    ("quality.critical_data.currency.counts.ref_count", 999),
    ("quality.critical_data.currency.counts.tp", -1),
    ("quality.critical_data.currency.value", 0.75),
    ("quality.structure.heading_detection_f1.value", 1.1),
    ("quality.reading_order.pairwise_reading_order_accuracy.counts.concordant_pairs", 999),
    ("evaluation.applicable_metric_count", 999),
    ("evaluation.unavailable_metric_count", 999),
    ("evaluation.rank_ineligibility_reasons", ["contradictory"]),
    ("per_page.0.evaluation.applicable_metric_count", 999),
    ("per_page.0.quality.text.cer_markdown_raw.scope", "document"),
])
def test_mutated_result_fails_closed(path, value):
    result = canonical()
    set_path(result, path, value)
    with pytest.raises(EvaluationValidationError):
        validate_result(result)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize("path", ["quality.text.cer_markdown_raw.value",
                                  "operational.elapsed_s", "operational.unrecognized_extra"])
def test_nonfinite_values_rejected_everywhere(value, path):
    result = canonical()
    set_path(result, path, value)
    with pytest.raises(EvaluationValidationError, match="non-finite"):
        validate_result(result)


@pytest.mark.parametrize("status", ["resource_limit", "invalid_input", "not_applicable", "undefined_denominator"])
def test_unavailable_status_cannot_retain_numeric_value(status):
    result = canonical()
    result["quality"]["text"]["cer_markdown_raw"]["status"] = status
    with pytest.raises(EvaluationValidationError):
        validate_result(result)


@pytest.mark.parametrize("key", ["evaluator", "inputs", "quality", "integrity", "operational", "per_page", "evaluation"])
def test_required_sections_cannot_disappear(key):
    result = canonical()
    del result[key]
    with pytest.raises(EvaluationValidationError):
        validate_result(result)


def test_resource_limit_requires_partial_and_ineligible_outcome():
    result = evaluate(EvaluationRequest("a" * 80, "b" * 80, max_text_edit_cells=10))
    validate_result(result)
    for field, value in (("status", "ok"), ("rank_eligible", True), ("rank_ineligibility_reasons", [])):
        changed = copy.deepcopy(result)
        changed["evaluation"][field] = value
        with pytest.raises(EvaluationValidationError):
            validate_result(changed)


def test_zero_denominator_has_undefined_state_and_preserves_counts():
    deleted = evaluate(EvaluationRequest("Alfa", ""))
    inserted = evaluate(EvaluationRequest("", "Beta"))
    precision = deleted["quality"]["text"]["text_coverage_precision"]
    recall = inserted["quality"]["text"]["text_coverage_recall"]
    assert precision["value"] is recall["value"] is None
    assert precision["status"] == recall["status"] == "undefined_denominator"
    assert precision["counts"]["ref_tokens"] == recall["counts"]["hyp_tokens"] == 1


def test_integrity_projection_includes_literal_code_once():
    result = evaluate(EvaluationRequest("```\n**literal**\n```", ""))
    coverage = result["integrity"]["page_content_coverage_recall"]
    assert coverage["value"] == 0
    assert coverage["counts"]["ref_chars"] == len("**literal**")
    assert coverage["direction"] == "higher_is_better"


@pytest.mark.parametrize("reference,hypothesis,value,status,insertions", [
    ("# Alfa", "# Alfa\n\n# Zzz", 0.75, "ok", 3),
    ("Texto", "# Zzz", None, "undefined_denominator", 3),
])
def test_extra_heading_insertions_are_counted(reference, hypothesis, value, status, insertions):
    result = evaluate(EvaluationRequest(reference, hypothesis))
    metric = result["quality"]["structure"]["heading_text_cer"]
    assert metric["value"] == value and metric["status"] == status
    assert metric["counts"]["insertion_only_chars"] == insertions
    assert metric["counts"]["edit_distance"] == insertions


@pytest.mark.parametrize("body", ["```\n**literal suficientemente longo**\n```",
                                  "- Parágrafo suficientemente longo para repetição"])
def test_duplicate_projection_counts_literal_code_and_list_text_once(body):
    result = evaluate(EvaluationRequest(body, body + "\n\n" + body))
    metric = result["integrity"]["duplicate_excess_rate"]
    assert metric["value"] == 0.5
    assert metric["counts"]["excess_blocks"] == 1
    assert metric["counts"]["total_hyp_blocks"] == 2


@pytest.mark.parametrize("prefix,key", [("# ", "heading_text_cer"), ("- ", "list_item_text_cer_on_matched")])
def test_structural_cer_obeys_budget_and_stable_direction(prefix, key):
    ref = parse_document(prefix + "a" * 80, origin="reference")
    hyp = parse_document(prefix + "b" * 80, origin="hypothesis")
    pairs = align_blocks(ref, hyp)
    metric = compute_structure_metrics(pairs, ref, hyp, max_edit_cells=10).to_dict()[key]
    assert metric["value"] is None and metric["status"] == "resource_limit"
    assert metric["direction"] == "lower_is_better" and metric["warnings"]
    empty = compute_structure_metrics([], [], []).to_dict()[key]
    assert empty["direction"] == "lower_is_better"


def test_random_small_documents_satisfy_schema_and_arithmetic():
    rng = random.Random(8042)
    for _ in range(30):
        ref = " ".join(rng.choices(["a", "b", "R$ 10,00", "30%"], k=rng.randrange(6)))
        hyp = " ".join(rng.choices(["a", "c", "R$ 20,00", "50%"], k=rng.randrange(6)))
        validate_result(evaluate(EvaluationRequest(ref, hyp)))


def test_comparison_cli_rejects_corrupted_scores_before_writing_report(tmp_path):
    result = canonical()
    result["quality"]["critical_data"]["currency"]["value"] = 1.0
    path, output = tmp_path / "bad.json", tmp_path / "comparison.md"
    path.write_text(json.dumps(result))
    process = subprocess.run([sys.executable, str(ROOT / "scripts/compare_engines.py"),
                              str(path), "--output", str(output)],
                             capture_output=True, text=True, timeout=30)
    assert process.returncode == 1
    assert "invalid canonical result" in process.stderr
    assert not output.exists()
