"""Aggregation and comparison contract oracles. Markdown/tests only; no OCR."""
from __future__ import annotations

import copy
import itertools
import json
import math
import random
import statistics
import subprocess
import sys
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

from evaluation_contract_helpers import comparison_result
from scripts.compare_engines import (
    _comparability_key_v2, _validate_comparability_v2, _validate_schema_homogeneity,
)
from structured_pdf_text.evaluation import EvaluationRequest, evaluate
from structured_pdf_text.evaluation.aggregation import (
    V2DocumentMetrics, aggregate_metric, check_comparability, evaluate_rank_eligibility,
)
from structured_pdf_text.evaluation.comparability import comparability_key
from structured_pdf_text.evaluation.critical_data import compute_critical_data_metrics
from structured_pdf_text.evaluation.markdown_parser import parse_page
from structured_pdf_text.evaluation.models import MetricResult, MetricStatus

ROOT = Path(__file__).resolve().parents[1]
_FIELDS = (
    "schema_version", "inputs.reference_effective_sha256", "inputs.reference_serialization_version",
    "inputs.pdf_sha256", "inputs.manifest_sha256", "inputs.mode", "inputs.selected_pages",
    "operational.benchmark_protocol_id", "evaluator.metric_definitions_version",
    "evaluator.normalization_policy", "evaluator.parser", "evaluator.alignment_policy",
    "evaluator.entity_policy", "evaluator.text_projection_policy", "evaluator.edit_distance_policy",
    "evaluator.html_table_policy", "evaluator.table_metric_policy", "evaluator.aggregation_policy",
    "evaluator.comparability_policy", "evaluator.resource_policy",
    "evaluator.page_syntax_policy", "evaluator.paragraph_policy",
)


def metric(value, status=MetricStatus.OK, counts=None, **kwargs):
    return MetricResult(value=value, status=status, direction=kwargs.pop("direction", "higher_is_better"),
                        counts={} if counts is None else counts, **kwargs)


def set_field(data, path, value):
    parts = path.split(".")
    parent = data
    for part in parts[:-1]:
        parent = parent[part]
    parent[parts[-1]] = value


def compare(a, b):
    """Require identical decisions and diagnostics from API and CLI wrapper."""
    ok, reasons = check_comparability(a, b)
    errors = _validate_comparability_v2([("a", a), ("b", b)])
    assert errors == reasons
    assert ok == (errors == [])
    return ok, reasons


@pytest.mark.parametrize("status", [MetricStatus.FALSE_NEGATIVE_ONLY, MetricStatus.FALSE_POSITIVE_ONLY])
def test_zero_outcomes_are_present_in_every_statistic(status):
    result = aggregate_metric([metric(1), metric(0, status), metric(None, MetricStatus.NOT_APPLICABLE)])
    assert result.values == [1, 0]
    assert result.n_applicable == 2 and result.n_total == 3
    assert result.macro_value == result.median_value == 0.5
    assert result.worst_value == result.min_value == 0
    assert result.max_value == 1
    assert result.to_dict()["coverage"] == 0.6667


@pytest.mark.parametrize("reference,hypothesis,status", [
    ("R$ 10,00", "", MetricStatus.FALSE_NEGATIVE_ONLY),
    ("", "R$ 10,00", MetricStatus.FALSE_POSITIVE_ONLY),
])
def test_actual_critical_entity_zero_participates_in_aggregate(reference, hypothesis, status):
    def currency(ref, hyp):
        return compute_critical_data_metrics(parse_page(ref, 1, "reference"),
                                             parse_page(hyp, 1, "hypothesis")).currency
    zero = currency(reference, hypothesis)
    assert zero.status == status
    result = aggregate_metric([currency("R$ 10,00", "R$ 10,00"), zero])
    assert result.macro_value == 0.5
    assert result.n_applicable == 2


@pytest.mark.parametrize("status", [s for s in MetricStatus if s not in {
    MetricStatus.OK, MetricStatus.FALSE_NEGATIVE_ONLY, MetricStatus.FALSE_POSITIVE_ONLY}])
def test_unavailable_states_never_add_a_fabricated_scalar(status):
    result = aggregate_metric([metric(1), metric(0, status)])
    assert result.values == [1]
    assert result.n_applicable == 1


@pytest.mark.parametrize("value", [None, math.nan, math.inf, -math.inf, True, False, "0", [], {}])
def test_nonfinite_and_nonnumeric_scalars_are_excluded(value):
    result = aggregate_metric([metric(1), metric(value)])
    assert result.values == [1]
    assert result.macro_value == 1
    json.dumps(result.to_dict(), allow_nan=False)


def test_random_statistics_match_independent_fraction_oracle():
    generator = random.Random(183)
    for _ in range(200):
        values = [generator.randrange(1001) / 1000 for _ in range(generator.randrange(1, 21))]
        results = [metric(value) for value in values]
        results.extend([metric(0, MetricStatus.FALSE_NEGATIVE_ONLY),
                        metric(0, MetricStatus.FALSE_POSITIVE_ONLY),
                        metric(None, MetricStatus.NOT_APPLICABLE)])
        generator.shuffle(results)
        expected = values + [0, 0]
        aggregate = aggregate_metric(iter(results))
        assert aggregate.n_applicable == len(expected)
        assert aggregate.macro_value == float(sum(map(Fraction, expected)) / len(expected))
        assert aggregate.median_value == statistics.median(expected)
        assert aggregate.worst_value == min(expected)
        assert aggregate.min_value == min(expected) and aggregate.max_value == max(expected)


def test_finite_extreme_median_does_not_overflow():
    result = aggregate_metric([metric(1e308), metric(1e308)])
    assert result.median_value == result.macro_value == 1e308
    json.dumps(result.to_dict(), allow_nan=False)


@pytest.mark.parametrize("direction,expected", [("higher_is_better", 0), ("lower_is_better", 1)])
def test_zero_participates_in_directional_worst_case(direction, expected):
    result = aggregate_metric([metric(1, direction=direction),
                               metric(0, MetricStatus.FALSE_NEGATIVE_ONLY, direction=direction)])
    assert result.worst_value == expected


@pytest.mark.parametrize("field,value", [
    ("definition_id", "other"), ("direction", "lower_is_better"),
    ("unit", "percent"), ("normalization_profile", "raw"),
])
def test_different_metric_definitions_cannot_be_averaged(field, value):
    with pytest.raises(ValueError, match=field):
        aggregate_metric([metric(1), replace(metric(0), **{field: value})])


def test_micro_is_ratio_of_counts_and_macro_keeps_zero():
    results = [metric(1, counts={"num": 2, "den": 2}),
               metric(0, MetricStatus.FALSE_NEGATIVE_ONLY, {"num": 0, "den": 8})]
    result = aggregate_metric(results, numerator_key="num", denominator_key="den")
    assert result.macro_value == 0.5
    assert result.micro_value == 0.2


def test_micro_includes_insertion_only_pages_with_undefined_local_denominator():
    results = [metric(0, counts={"num": 0, "den": 5}),
               metric(None, MetricStatus.UNDEFINED_DENOMINATOR, {"num": 9, "den": 0}),
               metric(None, MetricStatus.NOT_APPLICABLE)]
    result = aggregate_metric(results, numerator_key="num", denominator_key="den")
    assert result.micro_value == 1.8
    assert result.macro_value == 0


@pytest.mark.parametrize("status", [MetricStatus.RESOURCE_LIMIT, MetricStatus.INVALID_INPUT,
    MetricStatus.PARTIAL_EVALUATION, MetricStatus.UNAVAILABLE_ALIGNMENT, MetricStatus.INSUFFICIENT_EVIDENCE])
def test_micro_is_unavailable_when_any_page_has_incomplete_evaluation(status):
    results = [metric(0.2, counts={"num": 2, "den": 10}),
               metric(None, status, {"num": 0, "den": 100})]
    result = aggregate_metric(results, numerator_key="num", denominator_key="den")
    assert result.micro_value is None
    assert result.n_applicable == 1


@pytest.mark.parametrize("counts", [{}, {"num": 0}, {"den": 2}, {"num": -1, "den": 2},
    {"num": math.nan, "den": 2}, {"num": 0, "den": math.inf}, {"num": True, "den": 2},
    {"num": "1", "den": 2}, {"num": 1, "den": []}])
def test_micro_rejects_missing_or_invalid_counts(counts):
    with pytest.raises(ValueError, match="Micro"):
        aggregate_metric([metric(0, counts=counts)], numerator_key="num", denominator_key="den")


@pytest.mark.parametrize("keys", [{"numerator_key": "num"}, {"denominator_key": "den"},
    {"numerator_key": "", "denominator_key": "den"}, {"numerator_key": True, "denominator_key": "den"}])
def test_micro_requires_two_explicit_count_keys(keys):
    with pytest.raises(ValueError, match="Micro"):
        aggregate_metric([], **keys)


def test_nonempty_na_counts_are_not_a_correct_absence():
    with pytest.raises(ValueError, match="correct absence"):
        aggregate_metric([metric(None, MetricStatus.NOT_APPLICABLE, {"num": 1, "den": 0})],
                         numerator_key="num", denominator_key="den")


def test_micro_keeps_integer_totals_exact():
    huge = 2 ** 60
    result = aggregate_metric([metric(1, counts={"num": huge, "den": huge + 1}),
                               metric(0, counts={"num": 0, "den": 1})],
                               numerator_key="num", denominator_key="den")
    assert result.micro_value == float(Fraction(huge, huge + 2))


def test_document_summary_and_eligibility_include_numeric_zero_outcomes():
    doc = V2DocumentMetrics()
    doc.critical_data = {
        "missing": metric(0, MetricStatus.FALSE_NEGATIVE_ONLY).to_dict(),
        "invented": metric(0, MetricStatus.FALSE_POSITIVE_ONLY).to_dict(),
        "absent": metric(None, MetricStatus.NOT_APPLICABLE).to_dict(),
        "invalid_value": metric(True).to_dict(),
    }
    doc.integrity = {"diagnostic": {"status": "ok"}}
    result = doc.to_dict()
    assert result["evaluation"]["applicable_metric_count"] == 2
    assert result["evaluation"]["unavailable_metric_count"] == 2
    assert evaluate_rank_eligibility(doc) == (True, [])


def test_full_valid_identity_is_comparable_and_inputs_are_not_mutated():
    a, b = comparison_result(), comparison_result()
    original = copy.deepcopy((a, b))
    assert compare(a, b) == (True, [])
    assert compare(b, a) == (True, [])
    assert (a, b) == original
    assert comparability_key(a) == _comparability_key_v2(a)


@pytest.mark.parametrize("path", _FIELDS)
def test_every_missing_required_field_fails_closed_even_if_missing_in_both(path):
    a, b = comparison_result(), comparison_result()
    for data in (a, b):
        set_field(data, path, None)
    ok, reasons = compare(a, b)
    assert not ok and reasons
    assert check_comparability(a, b, strict=False) == (ok, reasons)


@pytest.mark.parametrize("path", _FIELDS)
def test_every_input_or_policy_mismatch_is_rejected(path):
    a, b = comparison_result(), comparison_result()
    if path.endswith("sha256"):
        value = "d" * 64
    elif path.endswith("selected_pages"):
        value = [2, 3, 4]
    elif path.endswith("resource_policy"):
        value = {**b["evaluator"]["resource_policy"], "max_alignment_pairs": 1}
    else:
        value = "different"
    set_field(b, path, value)
    assert compare(a, b)[0] is False
    assert compare(b, a)[0] is False


@pytest.mark.parametrize("pages", [None, [], [1, 1], [True], [False], [1.0], [1.5],
    ["1"], [0], [-1], "1,2", {"1": "page"}, [[1]], [None], [1, True]])
def test_page_scope_is_validated_without_lossy_coercion(pages):
    a, b = comparison_result(), comparison_result()
    for data in (a, b):
        data["inputs"]["selected_pages"] = pages
    ok, reasons = compare(a, b)
    assert not ok
    assert any("selected_pages" in reason for reason in reasons)


def test_page_order_and_digest_letter_case_do_not_change_identity():
    a, b = comparison_result(), comparison_result()
    b["inputs"]["selected_pages"] = [3, 1, 2]
    for field in ("reference_effective_sha256", "pdf_sha256", "manifest_sha256"):
        b["inputs"][field] = b["inputs"][field].upper()
    assert compare(a, b) == (True, [])


@pytest.mark.parametrize("value", ["", "x" * 64, "a" * 63, "a" * 65, 123, True, [], {}])
def test_malformed_digest_is_rejected_even_on_self_comparison(value):
    a = comparison_result()
    a["inputs"]["pdf_sha256"] = value
    assert compare(a, a)[0] is False


@pytest.mark.parametrize("value", [None, "reference-effective-v0", "reference-effective-v2", True, {}])
def test_reference_protocol_must_be_recognised(value):
    a = comparison_result()
    a["inputs"]["reference_serialization_version"] = value
    assert compare(a, a)[0] is False


@pytest.mark.parametrize("value", ["", " ", " ocr", "ocr ", True, 1, [], {}])
def test_input_identifiers_are_validated(value):
    a = comparison_result()
    a["inputs"]["mode"] = value
    assert compare(a, a)[0] is False


@pytest.mark.parametrize("value", [None, [], {}, "unbounded", {"max_text_edit_cells": 1},
    {"max_text_edit_cells": True}, {"max_text_edit_cells": 0}])
def test_resource_policy_is_required_and_typed(value):
    a = comparison_result()
    a["evaluator"]["resource_policy"] = value
    assert compare(a, a)[0] is False


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "100", None])
def test_each_resource_limit_must_be_a_positive_integer(value):
    a = comparison_result()
    a["evaluator"]["resource_policy"]["max_alignment_pairs"] = value
    assert compare(a, a)[0] is False


@pytest.mark.parametrize("section,value", list(itertools.product(
    ("inputs", "evaluator", "operational"), (None, [], "wrong", 42))))
def test_malformed_sections_do_not_crash_or_become_comparable(section, value):
    a = comparison_result()
    a[section] = value
    assert compare(a, a)[0] is False


@pytest.mark.parametrize("value", [None, [], "wrong", 42, {}])
def test_malformed_document_does_not_crash(value):
    assert compare(value, value)[0] is False


@pytest.mark.parametrize("schema", [None, "evaluation-v1.0", "evaluation-v2.0", "evaluation-4.0.0"])
def test_legacy_or_unknown_schema_cannot_be_compared_even_to_itself(schema):
    a = comparison_result()
    a["schema_version"] = schema
    a["inputs"]["reference_sha256"] = "a" * 64
    assert compare(a, a)[0] is False


def test_raw_reference_hash_cannot_replace_effective_hash():
    a = comparison_result()
    del a["inputs"]["reference_effective_sha256"]
    a["inputs"]["reference_sha256"] = "a" * 64
    assert compare(a, a)[0] is False


def test_outcomes_source_paths_timestamps_and_engine_identity_are_not_comparison_inputs():
    a, b = comparison_result(), comparison_result()
    b["inputs"].update(engine="different", run_id="different", hypothesis_sha256="d" * 64,
        selected_missing_pages=[2], extra_pages_out_of_scope=[99], reference_source_sha256="d" * 64)
    b["operational"].update(benchmark_status="partial", stability_status="recovered",
        recovery_count=3, elapsed_s=1, fallback_rate=1, pages_selected_but_missing=[2],
        engine_identity={"provider": "other"})
    b["evaluation"] = {"status": "partial", "rank_eligible": False}
    b["quality"] = {"text": {"cer": {"value": 1}}}
    b["evaluator"].update(created_at_utc="different", implementation_commit="other")
    assert compare(a, b) == (True, [])


def test_single_run_and_repeated_engine_labels_are_still_validated():
    a = comparison_result()
    assert _validate_comparability_v2([("a", a)]) == []
    invalid = copy.deepcopy(a)
    invalid["inputs"]["pdf_sha256"] = None
    assert _validate_comparability_v2([("a", invalid)])
    assert _validate_comparability_v2([("a", a), ("a", invalid)])


def test_duplicate_label_cannot_hide_an_unsupported_schema():
    canonical = comparison_result()
    legacy = {"schema_version": "evaluation-v2.0"}
    assert _validate_schema_homogeneity([("same", legacy), ("same", canonical)])
    assert _validate_schema_homogeneity([("same", canonical), ("same", legacy)])


def cli(tmp_path, docs, *options):
    paths = []
    for index, doc in enumerate(docs):
        path = tmp_path / f"input_{index}.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        paths.append(str(path))
    out = tmp_path / "comparison.md"
    completed = subprocess.run([sys.executable, str(ROOT / "scripts" / "compare_engines.py"),
        *paths, "--output", str(out), "--quiet", *options], cwd=ROOT, capture_output=True,
        text=True, timeout=30)
    return completed, out


@pytest.mark.parametrize("case", ["pdf", "protocol", "missing_policy", "duplicate_pages", "resource"])
def test_cli_rejects_same_failures_and_preserves_prior_report(tmp_path, case):
    a, b = comparison_result(), comparison_result()
    if case == "pdf":
        b["inputs"]["pdf_sha256"] = "d" * 64
    elif case == "protocol":
        b["inputs"]["reference_serialization_version"] = None
    elif case == "missing_policy":
        del b["evaluator"]["entity_policy"]
    elif case == "duplicate_pages":
        b["inputs"]["selected_pages"] = [1, 1, 2, 3]
    else:
        b["evaluator"]["resource_policy"]["max_text_edit_cells"] = 1
    assert compare(a, b)[0] is False
    (tmp_path / "comparison.md").write_text("prior report", encoding="utf-8")
    completed, output = cli(tmp_path, [a, b])
    assert completed.returncode != 0
    assert "not comparable" in completed.stderr
    assert output.read_text() == "prior report"


def test_real_evaluator_documents_obey_shared_contract(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"pages": []}', encoding="utf-8")
    full = "## Página 1\n\nTexto R$ 10,00\n\n## Página 2\n\nOutro texto."
    request = EvaluationRequest(full, full, manifesto_path=manifest,
        pdf_sha256="b" * 64, mode="ocr", run_metadata={"benchmark_protocol_id": "synthetic-v1"})
    a = evaluate(request)
    b = evaluate(replace(request, hyp_text="## Página 1\n\nTexto R$ 10,00",
                         engine="different", run_id="partial"))
    assert a["evaluator"]["metric_definitions_version"] == "3.0.4"
    assert a["evaluator"]["aggregation_policy"] == "finite-numeric-outcomes-v1"
    assert compare(a, b) == (True, [])
    completed, output = cli(tmp_path, [a, b])
    assert completed.returncode == 0, completed.stderr
    assert output.is_file()
    # Quality and outcomes are intentionally different, while inputs agree.
    assert a["quality"] != b["quality"]
    assert a["inputs"]["selected_missing_pages"] != b["inputs"]["selected_missing_pages"]


def test_diagnostic_bypass_remains_explicitly_labelled(tmp_path):
    a, b = comparison_result(), comparison_result()
    b["inputs"]["pdf_sha256"] = "d" * 64
    completed, output = cli(tmp_path, [a, b], "--skip-validation")
    assert completed.returncode == 0, completed.stderr
    assert "Heterogeneous comparison" in output.read_text()


@pytest.mark.parametrize("contents", ["not JSON", "[]", "null", '"wrong"'])
def test_unreadable_artifact_cannot_be_silently_removed_from_comparison(tmp_path, contents):
    good, bad = tmp_path / "good.json", tmp_path / "bad.json"
    good.write_text(json.dumps(comparison_result()), encoding="utf-8")
    bad.write_text(contents, encoding="utf-8")
    out = tmp_path / "comparison.md"
    completed = subprocess.run([sys.executable, str(ROOT / "scripts" / "compare_engines.py"),
        str(good), str(bad), "--output", str(out), "--quiet"], cwd=ROOT,
        capture_output=True, text=True, timeout=30)
    assert completed.returncode != 0
    assert "cannot load metrics" in completed.stderr
    assert not out.exists()


@pytest.mark.parametrize("missing", ["missing.json", "unmatched_*.json"])
def test_missing_requested_artifact_cannot_be_silently_removed(tmp_path, missing):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(comparison_result()), encoding="utf-8")
    out = tmp_path / "comparison.md"
    completed = subprocess.run([sys.executable, str(ROOT / "scripts" / "compare_engines.py"),
        str(good), str(tmp_path / missing), "--output", str(out), "--quiet"], cwd=ROOT,
        capture_output=True, text=True, timeout=30)
    assert completed.returncode != 0
    assert not out.exists()
