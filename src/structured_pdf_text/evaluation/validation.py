"""Local JSON Schema and arithmetic validation for canonical evaluation results.

No remote schema resolution is used. JSON Schema covers shape and scalar
contracts; this module additionally checks finitude, counts and cross-field
relationships that JSON Schema cannot express.
"""
from __future__ import annotations

import json
import math
from copy import deepcopy
from functools import lru_cache
from importlib.resources import files

from jsonschema import Draft202012Validator, FormatChecker

from .aggregation import _is_applicable_value

VALIDATION_POLICY = "canonical-schema-and-invariants-v1"


class EvaluationValidationError(ValueError):
    """An evaluation result must not be published or ranked in this state."""


@lru_cache(maxsize=1)
def _schema() -> dict:
    return json.loads(files(__package__).joinpath("schema_v3.json").read_text(encoding="utf-8"))


def result_schema() -> dict:
    return deepcopy(_schema())


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema = _schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _require(condition: bool, path: str, message: str) -> None:
    if not condition:
        raise EvaluationValidationError(f"{path}: {message}")


def _json_tree(value: object, path: str = "result") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _require(isinstance(key, str), path, "JSON object keys must be strings")
            _json_tree(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _json_tree(item, f"{path}[{index}]")
    elif isinstance(value, float):
        _require(math.isfinite(value), path, "non-finite JSON number")
    else:
        _require(value is None or isinstance(value, (str, int, bool)), path, "not a JSON value")


def _integer(value: object, path: str) -> int:
    _require(type(value) is int and value >= 0, path, "count must be a nonnegative integer")
    return value


def _close(value: float, expected: float, path: str) -> None:
    # Canonical metric serialization currently rounds at 4 or 6 decimals.
    _require(math.isclose(value, expected, abs_tol=0.000051, rel_tol=1e-9), path,
             f"value {value!r} disagrees with counts ({expected!r})")


def _metric(metric: dict, path: str, scope: str) -> None:
    _require(metric["scope"] == scope, path, f"expected scope {scope}")
    counts, value, status = metric["counts"], metric["value"], metric["status"]
    for key, item in counts.items():
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            _require(item >= 0, f"{path}.counts.{key}", "negative count")
    applicable = _is_applicable_value(value, status)
    definition = metric["definition_id"]
    if status in {"ok", "false_positive_only", "false_negative_only", "not_applicable", "undefined_denominator"}:
        if definition in {"MET-TX01", "MET-TX02", "MET-TX03", "MET-TX04"}:
            unit = "words" if definition == "MET-TX04" else "chars"
            required = {f"ref_{unit}", f"hyp_{unit}"}
            if status != "resource_limit":
                required |= {"S", "D", "I"}
            _require(required <= counts.keys(), path, "missing sequential edit counts")
    if applicable:
        required_by_definition = {
            "MET-S02": {"level_matched", "level_total"},
            "MET-S03": {"edit_distance", "ref_chars"},
            "MET-S07": {"depth_matched", "depth_total"},
            "MET-S08": {"concordant_pairs", "total_pairs"},
            "MET-S10": {"concordant_pairs", "total_pairs"},
            "MET-SL01": {"edit_distance", "ref_chars"},
            "MET-SL02": {"kind_matched", "kind_total"},
            "MET-SL03": {"concordant_pairs", "total_pairs"},
            "MET-SL04": {"ordinal_correct", "ordinal_total"},
            "MET-T03": {"matched_tables", "total_tables"},
            "MET-T05": {"exact", "total"}, "MET-T05.matched": {"exact", "total"},
            "MET-T06": {"aligned", "total"}, "MET-T07": {"edit_distance", "ref_chars"},
            "MET-T09": {"concordant_pairs", "total_pairs"},
            "MET-T10": {"concordant_pairs", "total_pairs"},
            "MET-T11": {"correct_page", "total_with_page_info"},
            "MET-T12": {"correct_spans", "total"},
            "MET-I01": {"excess_blocks", "total_hyp_blocks"},
            "MET-I02": {"missing", "selected_reference"},
            "MET-TX05": {"edit_distance", "ref_chars"},
            "MET-TX06": {"tp_tokens", "ref_tokens", "hyp_tokens"},
            "MET-TX07": {"tp_tokens", "ref_tokens", "hyp_tokens"},
        }
        f1_definitions = {"MET-S01", "MET-S04", "MET-S05", "MET-S06", "MET-S11",
                          "MET-T01", "MET-T02", "MET-T04", "MET-T08"}
        required = ({"tp", "fp", "fn"} if definition in f1_definitions or definition.startswith("MET-CD")
                    else required_by_definition.get(definition, set()))
        _require(required <= counts.keys(), path, "missing metric counts")
    if {"tp", "fp", "fn"} <= counts.keys():
        tp, fp, fn = (_integer(counts[key], f"{path}.counts.{key}") for key in ("tp", "fp", "fn"))
        if status == "false_positive_only":
            _require(tp == fn == 0 and fp > 0 and value == 0, path, "invalid FP-only outcome")
        elif status == "false_negative_only":
            _require(tp == fp == 0 and fn > 0 and value == 0, path, "invalid FN-only outcome")
        elif status == "not_applicable":
            _require(tp == fp == fn == 0, path, "N/A has observed detection events")
        if applicable:
            denominator = 2 * tp + fp + fn
            _require(denominator > 0, path, "F1 has a zero denominator")
            _close(value, 2 * tp / denominator, path)
        for key, expected in (("ref_count", tp + fn), ("hyp_count", tp + fp),
                              ("ref_tables", tp + fn), ("hyp_tables", tp + fp),
                              ("ref_headings", tp + fn), ("hyp_headings", tp + fp)):
            if key in counts:
                _require(counts[key] == expected and type(counts[key]) is int, path, f"inconsistent {key}")
    if {"S", "D", "I"} <= counts.keys():
        s, d, i = (_integer(counts[key], f"{path}.counts.{key}") for key in ("S", "D", "I"))
        if "edit_distance" in counts:
            _require(counts["edit_distance"] == s + d + i, path, "S+D+I differs from edit distance")
        reference = counts.get("ref_chars", counts.get("ref_words"))
        hypothesis = counts.get("hyp_chars", counts.get("hyp_words"))
        if reference is not None:
            _integer(reference, path)
            _require(s + d <= reference, path, "substitutions/deletions exceed reference")
        if hypothesis is not None:
            _integer(hypothesis, path)
            _require(reference is not None and reference - d + i == hypothesis, path, "lengths disagree with D/I")
    if applicable:
        if "edit_distance" in counts:
            denominator = counts.get("ref_chars", counts.get("ref_words"))
            if denominator is not None:
                _integer(counts["edit_distance"], path)
                _integer(denominator, path)
                _require(denominator > 0, path, "error rate has zero reference length")
                _close(value, counts["edit_distance"] / denominator, path)
        ratios = (("level_matched", "level_total"), ("depth_matched", "depth_total"),
                  ("kind_matched", "kind_total"), ("ordinal_correct", "ordinal_total"),
                  ("concordant_pairs", "total_pairs"), ("exact", "total"),
                  ("aligned", "total"), ("correct_spans", "total"),
                  ("correct_page", "total_with_page_info"), ("missing", "selected_reference"),
                  ("matched_tables", "total_tables"),
                  ("excess_blocks", "total_hyp_blocks"))
        for numerator, denominator in ratios:
            if {numerator, denominator} <= counts.keys():
                n, d = (_integer(counts[key], path) for key in (numerator, denominator))
                _require(0 <= n <= d and d > 0, path, "invalid ratio counts")
                _close(value, n / d, path)
        if "tp_tokens" in counts:
            tp = _integer(counts["tp_tokens"], path)
            key = "hyp_tokens" if metric["definition_id"] == "MET-TX07" else "ref_tokens"
            if key in counts:
                denominator = _integer(counts[key], path)
                _require(tp <= denominator and denominator > 0, path, "invalid token coverage")
                _close(value, tp / denominator, path)


def _evaluation(data: dict, path: str, scope: str) -> None:
    metrics = []
    for group, values in {**data["quality"], "integrity": data["integrity"]}.items():
        for key, metric in values.items():
            if "definition_id" in metric:
                _metric(metric, f"{path}.{group}.{key}", scope)
                metrics.append(metric)
    by_definition = {m["definition_id"]: m for m in metrics}
    for metric in metrics:
        definition = metric["definition_id"]
        if not _is_applicable_value(metric["value"], metric["status"]):
            continue
        if definition.endswith((".precision", ".recall")):
            parent = by_definition.get(definition.rsplit(".", 1)[0])
            _require(parent is not None and {"tp", "fp", "fn"} <= parent["counts"].keys(),
                     path, "precision/recall lacks detection counts")
            counts = parent["counts"]
            denominator = counts["tp"] + counts["fp" if definition.endswith(".precision") else "fn"]
            expected = counts["tp"] / denominator if denominator else 0.0
            _close(metric["value"], expected, f"{path}.{definition}")
    evaluation = data["evaluation"]
    applicable = sum(_is_applicable_value(m["value"], m["status"]) for m in metrics)
    _require(evaluation["applicable_metric_count"] == applicable, path, "incorrect applicable metric count")
    _require(evaluation["unavailable_metric_count"] == len(metrics) - applicable, path,
             "incorrect unavailable metric count")
    reasons = evaluation["rank_ineligibility_reasons"]
    _require(evaluation["rank_eligible"] == (not reasons), path, "eligibility disagrees with reasons")
    if any(m["status"] == "resource_limit" for m in metrics):
        _require(evaluation["status"] == "partial" and not evaluation["rank_eligible"]
                 and "evaluation_resource_limit" in reasons, path, "resource exhaustion must block ranking")
    if data["integrity"]["markdown_validity"]["validity_status"] == "invalid":
        _require(not evaluation["rank_eligible"] and "invalid_markdown_in_hypothesis" in reasons,
                 path, "invalid Markdown must block ranking")


def validate_result(data: dict) -> None:
    """Raise EvaluationValidationError before publishing or comparing bad data."""
    _json_tree(data)
    error = next(_validator().iter_errors(data), None)
    if error is not None:
        path = ".".join(map(str, error.absolute_path)) or "result"
        raise EvaluationValidationError(f"{path}: {error.message}")
    inputs = data["inputs"]
    selected, missing, extra = (inputs[key] for key in (
        "selected_pages", "selected_missing_pages", "extra_pages_out_of_scope"))
    for key, values in (("selected_pages", selected), ("selected_missing_pages", missing),
                        ("extra_pages_out_of_scope", extra)):
        _require(values == sorted(values), f"inputs.{key}", "page IDs must be sorted")
    _require(set(missing) <= set(selected), "inputs", "missing pages outside evaluation scope")
    _require(not set(extra) & set(selected), "inputs", "extra pages overlap selected pages")
    _require(len(selected) <= inputs["total_pages_in_reference"], "inputs", "reference page count is too small")
    _require(data["operational"]["selected_pages"] == selected, "operational", "evaluation scope mismatch")
    missing_counts = data["integrity"]["missing_page_rate"]["counts"]
    _require(missing_counts.get("missing") == len(missing)
             and missing_counts.get("selected_reference") == len(selected), "integrity.missing_page_rate",
             "page counts disagree with evaluation scope")
    _require([page["page"] for page in data["per_page"]] == selected,
             "per_page", "diagnostics must cover each selected page exactly once, in order")
    _evaluation(data, "document", "document")
    for page in data["per_page"]:
        path = f"page[{page['page']}]"
        _evaluation(page, path, "page")
        _require(page["selected_but_missing"] == (page["page"] in missing), path, "missing-page flag mismatch")
        counts = page["quality"]["text"]["cer_sequential_visible"]["counts"]
        _require({"ref_chars", "hyp_chars"} <= counts.keys(), path, "missing visible character counts")
        _integer(counts["ref_chars"], path)
        _integer(counts["hyp_chars"], path)
        _require(page["reference_empty"] == (counts["ref_chars"] == 0), path, "reference-empty flag mismatch")
        _require(page["hypothesis_empty"] == (counts["hyp_chars"] == 0), path, "hypothesis-empty flag mismatch")
        _require(not page["selected_but_missing"] or page["hypothesis_empty"], path,
                 "missing page contains hypothesis text")
