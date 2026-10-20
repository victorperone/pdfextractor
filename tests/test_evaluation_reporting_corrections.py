"""Canonical operational reporting, through the API and actual Markdown CLIs.

No PDF extraction or OCR is invoked. Expected values exercise counts, scope,
zero/N/A behavior, unavailable computations and metadata provenance.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.compare_engines import _get_value_v2, _render_comparison_table_v2
from structured_pdf_text.evaluation import EvaluationRequest, evaluate
from structured_pdf_text.evaluation.artifacts import publication_identity
from structured_pdf_text.evaluation.reference_inputs import document_pages, pages_markdown
from structured_pdf_text.evaluation.report_renderer import cell, render_run_sections, unique_run_labels
from structured_pdf_text.evaluation.reporting import (
    REPORTING_POLICY, corpus_page_metadata, metric_value, run_page_metadata,
)

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = "## Página 1\n\n# Título\n\nR$ 10,00\n\n## Página 2\n\nR$ 20,00\n\n## Página 3\n\n"
HYPOTHESIS = "## Página 1\n\n# Título\n\nR$ 10,00\n\n## Página 3\n\n"


def request(tmp_path, **kwargs):
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps({"pages": [
        {"page": 1, "family": "financeiro", "conditions": ["blur", "blur", "noise"]},
        {"page": 2, "family": "financeiro", "conditions": "blur"},
        {"page": 3, "family": "controle", "conditions": []},
    ]}), encoding="utf-8")
    run = {"selected_pages": [1, 2, 3], "pdf_sha256": "a" * 64, "mode": "ocr",
           "benchmark_protocol_id": "report-test-v1", "benchmark_status": "partial",
           "document_status": "partial", "stability_status": "recovered", "recovery_count": 3,
           "fallback_rate": 0.25, "recovered_pages": [1], "degraded_pages": [2],
           "failed_ocr_pages": [], "elapsed_s": 12,
           "timings": {"render_s": 2, "ocr_s": 7, "assembly_s": 3},
           "memory": {"peak_rss_bytes": 104857600, "sampled_process_tree_peak_rss_bytes": 262144000,
                      "sample_interval_s": 0.2},
           "engine_identity": {"runtime": "onnxruntime", "profile": "latin/pt",
                               "extra": {"model_sha256": "b" * 64, "clahe": True}},
           "runtime_environment": {"render_scale": 2.5, "requested_threads": 1},
           "setup_complexity": "local models", "document_warnings": ["worker recovered"],
           "pages": [
               {"page": 1, "content_status": "valid", "stability_status": "recovered",
                "ocr_outcome": "success", "easyocr_calls": 4, "easyocr_fallback_count": 3,
                "easyocr_fallback_rate": 0.75, "easyocr_fallback_reasons": ["retry canvas"],
                "partial_reasons": [], "complexity_reasons": ["table"], "warnings": ["retry"],
                "family": "untrusted engine family", "conditions": ["untrusted engine condition"]},
               {"page": 2, "content_status": "partial", "stability_status": "degraded",
                "ocr_outcome": "timeout", "partial_reasons": ["ocr_timeout"]},
           ]}
    return replace(EvaluationRequest(REFERENCE, HYPOTHESIS, manifesto_path=path,
                                     engine="rapidocr-test", run_id="report-r1", run_metadata=run), **kwargs)


def report(data, engine="rapidocr-test"):
    return _render_comparison_table_v2([(engine, data)])


def section(text, title):
    return text.split(title + "\n", 1)[1].split("\n## ", 1)[0]


def test_selected_pages_include_missing_and_explicit_blank(tmp_path):
    result = evaluate(request(tmp_path))
    assert result["evaluator"]["reporting_policy"] == REPORTING_POLICY
    assert [p["page"] for p in result["per_page"]] == [1, 2, 3]
    first, missing, blank = result["per_page"]
    assert first["quality"]["text"]["cer_sequential_normalized"]["value"] == 0
    assert missing["selected_but_missing"] and missing["hypothesis_empty"]
    assert not missing["reference_empty"]
    assert missing["quality"]["text"]["cer_sequential_normalized"]["value"] == 1
    assert missing["quality"]["critical_data"]["currency"]["value"] == 0
    assert missing["quality"]["critical_data"]["currency"]["status"] == "false_negative_only"
    assert blank["reference_empty"] and blank["hypothesis_empty"]
    assert not blank["selected_but_missing"]
    assert blank["quality"]["text"]["cer_sequential_normalized"]["value"] is None
    assert blank["quality"]["text"]["cer_sequential_normalized"]["status"] == "not_applicable"
    assert missing["integrity"]["missing_page_rate"]["value"] == 1


def test_missing_page_one_is_not_converted_to_emitted_blank(tmp_path):
    result = evaluate(request(tmp_path, hyp_text="## Página 3\n\n"))
    assert result["per_page"][0]["selected_but_missing"]
    assert result["per_page"][0]["integrity"]["missing_page_rate"]["value"] == 1


def test_page_scope_applies_to_metadata_metrics_and_summary(tmp_path):
    req = request(tmp_path, selected_page_scope=[1])
    result = evaluate(req)
    assert [p["page"] for p in result["per_page"]] == [1]
    assert result["inputs"]["extra_pages_out_of_scope"] == [3]
    assert "controle" not in section(report(result), "## Breakdown por Família do Corpus")
    changed = evaluate(replace(req, hyp_text=HYPOTHESIS + "\n\n## Página 7\n\nBAD"))
    assert changed["quality"] == result["quality"]
    assert changed["per_page"] == result["per_page"]


def test_corpus_labels_and_run_observations_have_separate_provenance(tmp_path):
    req = request(tmp_path)
    before = copy.deepcopy(req.run_metadata)
    result = evaluate(req)
    first = result["per_page"][0]
    assert first["family"] == "financeiro"
    assert first["conditions"] == ["blur", "noise"]
    assert first["operational"] == before["pages"][0]
    assert req.run_metadata == before
    assert result["operational"]["benchmark_status"] == "partial"
    assert "pages" not in result["operational"]
    assert result["per_page"][2]["operational"] == {}


def test_returned_operational_snapshot_does_not_alias_mutable_request(tmp_path):
    req = request(tmp_path)
    result = evaluate(req)
    before = copy.deepcopy(result)
    req.run_metadata["engine_identity"]["extra"]["clahe"] = False
    req.run_metadata["pages"][0]["warnings"].append("later")
    req.run_metadata["recovered_pages"].append(2)
    assert result == before


@pytest.mark.parametrize("page", [1, 2, 3])
def test_page_metrics_equal_same_api_at_single_page_scope(tmp_path, page):
    req = request(tmp_path)
    full = evaluate(req)
    one = evaluate(replace(req, selected_page_scope=[page]))
    diagnostic = full["per_page"][page - 1]
    expected = copy.deepcopy(one["quality"])
    for group in expected.values():
        for metric in group.values():
            if isinstance(metric, dict) and "definition_id" in metric:
                metric["scope"] = "page"
    assert diagnostic["quality"] == expected
    assert diagnostic["evaluation"] == one["evaluation"]


def test_document_page_displacement_is_not_replaced_by_page_macro():
    ref = "## Página 1\n\nR$ 10,00\n\n## Página 2\n\nR$ 20,00"
    hyp = "## Página 2\n\nR$ 10,00\n\n## Página 1\n\nR$ 20,00"
    result = evaluate(EvaluationRequest(ref, hyp))
    assert metric_value(result, "critical_data.currency") == 1
    assert metric_value(result, "critical_data.currency_page") == 0
    assert all(metric_value(p, "critical_data.currency") == 0 for p in result["per_page"])
    reversed_pages = "## Página 2\n\nR$ 20,00\n\n## Página 1\n\nR$ 10,00"
    reversed_result = evaluate(EvaluationRequest(ref, reversed_pages))
    assert metric_value(reversed_result, "reading_order.pairwise_reading_order_accuracy") < 1
    assert all(metric_value(p, "critical_data.currency") == 1 for p in reversed_result["per_page"])


def test_page_budgets_remain_explicit_when_document_and_page_exceed_limit():
    result = evaluate(EvaluationRequest("a" * 80, "b" * 80, max_text_edit_cells=5))
    assert result["evaluation"]["status"] == "partial"
    assert result["per_page"][0]["evaluation"]["status"] == "partial"
    assert metric_value(result["per_page"][0], "text.cer_sequential_normalized") is None
    text = report(result)
    assert cell("resource_limit") in text
    assert "| rapidocr-test | 1 |" not in section(text, "### Piores páginas por CER normalizado")


def test_document_resource_limit_does_not_hide_available_page_diagnostics():
    body = "## Página 1\n\nAlfa\n\n## Página 2\n\nBeta"
    result = evaluate(EvaluationRequest(body, body.replace("Alfa", "# Alfa"), max_alignment_pairs=1))
    assert result["evaluation"]["status"] == "partial"
    assert all(p["evaluation"]["status"] == "ok" for p in result["per_page"])
    assert not result["evaluation"]["rank_eligible"]


@pytest.mark.parametrize("data", [[], {"pages": None}, {"pages": [None]},
    {"pages": [{"page": 0}]}, {"pages": [{"page": True}]},
    {"pages": [{"page": 1, "page_num": 2}]},
    {"pages": [{"page": 1}, {"page": "1"}]},
    {"pages": [{"page": 1, "family": []}]},
    {"pages": [{"page": 1, "conditions": [None]}]},
    {"pages": [{"page": 1, "conditions": {}}]},
])
def test_invalid_corpus_metadata_rejected(tmp_path, data):
    path = tmp_path / "metadata.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        evaluate(EvaluationRequest("Text", "Text", manifesto_path=path))


@pytest.mark.parametrize("pages", [None, {}, [None], [{"page": 0}], [{"page": False}],
    [{"page": 1}, {"page": 1}], [{"page": 1, "page_num": 2}]])
def test_selected_scope_does_not_bypass_invalid_operational_page_metadata(pages):
    with pytest.raises(ValueError):
        evaluate(EvaluationRequest("Text", "Text", run_metadata={"selected_pages": [1], "pages": pages}))


@pytest.mark.parametrize("family,conditions,expected", [
    (None, None, []), ("plain", [], []), ("plain", "blur", ["blur"]),
    ("plain", ["noise", "blur", "noise", ""], ["blur", "noise"]),
])
def test_metadata_labels_normalized_deterministically(tmp_path, family, conditions, expected):
    path = tmp_path / "metadata.json"
    path.write_text(json.dumps({"pages": [{"page_num": 7, "family": family, "conditions": conditions}]}))
    assert corpus_page_metadata(path)[7] == {"family": family, "conditions": expected}


def test_metadata_only_reference_and_run_page_num_supported(tmp_path):
    path = tmp_path / "corpus.json"
    path.write_text('{"pages": [{"page_num": 7, "family": "plain"}]}')
    result = evaluate(EvaluationRequest("## Página 7\n\nTexto", "## Página 7\n\nTexto",
        manifesto_path=path, run_metadata={"selected_pages": [7], "pages": [{"page_num": 7, "ocr_outcome": "success"}]}))
    assert result["per_page"][0]["family"] == "plain"
    assert result["per_page"][0]["operational"]["ocr_outcome"] == "success"


def test_missing_metadata_not_fabricated_as_clean_zero_recovery_or_elapsed():
    result = evaluate(EvaluationRequest("Texto", "Texto"))
    assert result["operational"]["benchmark_status"] is None
    text = report(result)
    assert "| rapidocr-test | — | — | — | — | — | — | — | — | — | — | — | — | — |" in text
    assert "| rapidocr-test | — | — | — | — | — | — | — | — | — | — |" in section(text, "## Operacional — observacional")


def test_report_shows_ids_statuses_timings_profiles_memory_and_reasons(tmp_path):
    result = evaluate(request(tmp_path))
    result["evaluator"].update(engine="wrong-engine", run_id="wrong-id")
    text = report(result)
    assert "report-r1" in text and "wrong-id" not in text
    assert "partial" in text and "recovered" in text
    assert "| 3 | 0.2500 |" in text
    assert "| 2.0000 | 7.0000 | 3.0000 | 12.0000 | 4.0000 | 100.0 | 250.0 |" in text
    for value in ("onnxruntime", "latin/pt", "model_sha256", "clahe", "render_scale",
                  "retry canvas", "ocr_timeout", "worker recovered", "local models", "sample_interval_s"):
        assert cell(value) in text
    assert "1 (2/3 observadas)" in text
    assert "0 (2/3 observadas)" in text


def test_content_empty_and_missing_counts_are_distinct(tmp_path):
    text = report(evaluate(request(tmp_path)))
    recall = section(text, "## Recall e páginas difíceis")
    assert f"| rapidocr-test | {cell([1, 2, 3])} | {cell([2])} | {cell([])} | 3 | 1 | 1 | 0.3333 |" in recall


def test_group_means_include_zero_exclude_na_and_deduplicate_conditions(tmp_path):
    text = report(evaluate(request(tmp_path)))
    conditions = section(text, "## Breakdown por Condição do Corpus")
    currency = conditions.split("### Currency Page F1 ↑\n", 1)[1].split("\n### ", 1)[0]
    assert "| blur | 0.5000 (2/2) |" in currency
    assert "| noise | 1.0000 (1/1) |" in currency
    assert "| sem condição | — (0/1) |" in currency
    assert "untrusted" not in conditions
    assert "| financeiro | 0.5000 (2/2) |" in section(text, "## Breakdown por Família do Corpus")


def test_group_macro_not_micro_and_omission_uses_actual_word_counts(tmp_path):
    req = request(tmp_path, ref_text="## Página 1\n\n" + "palavra " * 9 + "\n\n## Página 2\n\noutra",
                  hyp_text="## Página 1\n\n" + "palavra " * 9, selected_page_scope=[1, 2])
    result = evaluate(req)
    assert metric_value(result, "text.wer_sequential") == 0.1
    text = report(result)
    assert "| rapidocr-test | Word Deletion Rate | 0.1000 |" in text
    conditions = section(text, "## Breakdown por Condição do Corpus")
    deletion = conditions.split("### Word Deletion Rate ↓\n", 1)[1].split("\n### ", 1)[0]
    assert "| blur | 0.5000 (2/2) |" in deletion


def test_unavailable_numeric_payload_cannot_enter_group_mean_or_win(tmp_path):
    result = evaluate(request(tmp_path))
    result["quality"]["text"]["cer_sequential_normalized"].update(value=-100, status="resource_limit")
    result["per_page"][0]["quality"]["critical_data"]["currency"].update(value=100, status="resource_limit")
    text = report(result)
    assert "| CER Sequential Normalized ↓ | — |" in text
    assert "**-100.0000**" not in text
    conditions = section(text, "## Breakdown por Condição do Corpus")
    currency = conditions.split("### Currency Page F1 ↑\n", 1)[1].split("\n### ", 1)[0]
    assert "| blur | 0.0000 (1/2) |" in currency
    assert "| noise | — (0/1) |" in currency


def test_labels_and_operational_outcomes_do_not_change_quality(tmp_path):
    req = request(tmp_path)
    first = evaluate(req)
    req.manifesto_path.write_text('{"pages": [{"page": 1, "family": "changed", "conditions": ["skew"]}]}')
    changed_run = copy.deepcopy(req.run_metadata)
    changed_run.update(benchmark_status="invalid", stability_status="failed", elapsed_s=999, recovery_count=20)
    second = evaluate(replace(req, run_metadata=changed_run))
    assert first["quality"] == second["quality"]
    assert first["integrity"] == second["integrity"]
    assert [p["quality"] for p in first["per_page"]] == [p["quality"] for p in second["per_page"]]
    assert first["operational"] != second["operational"]
    assert first["per_page"][0]["family"] != second["per_page"][0]["family"]


def test_false_positive_only_entity_score_is_included_in_group_mean(tmp_path):
    req = request(tmp_path, ref_text="## Página 1\n\nTexto", hyp_text="## Página 1\n\nR$ 10,00",
                  selected_page_scope=[1])
    result = evaluate(req)
    assert result["per_page"][0]["quality"]["critical_data"]["currency"]["status"] == "false_positive_only"
    conditions = section(report(result), "## Breakdown por Condição do Corpus")
    currency = conditions.split("### Currency Page F1 ↑\n", 1)[1].split("\n### ", 1)[0]
    assert "| blur | 0.0000 (1/1) |" in currency


def test_precision_recall_and_denominator_zero_remain_distinct(tmp_path):
    req = request(tmp_path, ref_text="R$ 10,00", hyp_text="R$ 10,00 e R$ 20,00", selected_page_scope=[1])
    text = report(evaluate(req))
    assert "| rapidocr-test | currency Precision | 0.5000 |" in text
    assert "| rapidocr-test | currency Recall | 1.0000 |" in text
    text = report(evaluate(replace(req, ref_text="Texto", hyp_text="R$ 10,00")))
    assert "| rapidocr-test | currency Precision | 0.0000 |" in text
    assert "| rapidocr-test | currency Recall | — |" in text


@pytest.mark.parametrize("value,status", [(0, "false_positive_only"), (0, "false_negative_only"), (0.5, "ok")])
def test_renderer_accepts_all_finite_applicable_statuses(value, status):
    data = {"quality": {"text": {"metric": {"value": value, "status": status}}}}
    assert _get_value_v2(data, "text.metric") == value


@pytest.mark.parametrize("value,status", [(0, "resource_limit"), (1, "invalid_input"),
    (0, "not_applicable"), (1, None), (True, "ok"), ("0.5", "ok"),
    (float("nan"), "ok"), (float("inf"), "ok"), (-float("inf"), "ok"), (None, "ok")])
def test_renderer_excludes_unavailable_or_invalid_numeric_values(value, status):
    data = {"quality": {"text": {"metric": {"value": value, "status": status}}}}
    assert _get_value_v2(data, "text.metric") is None


def test_worst_pages_are_sorted_with_missing_pages_and_ties(tmp_path):
    text = report(evaluate(request(tmp_path)))
    worst = section(text, "### Piores páginas por CER normalizado")
    assert worst.index("| rapidocr-test | 2 |") < worst.index("| rapidocr-test | 1 |")
    assert "| rapidocr-test | 3 |" not in worst
    assert cell("not_applicable") in worst
    body = pages_markdown({n: "Texto" for n in range(25, 0, -1)})
    text = report(evaluate(EvaluationRequest(body, body)))
    worst = section(text, "### Piores páginas por CER normalizado")
    assert worst.index("| rapidocr-test | 1 |") < worst.index("| rapidocr-test | 2 |")
    assert "| rapidocr-test | 20 |" in worst
    assert "| rapidocr-test | 21 |" not in worst


def test_arbitrary_metadata_is_escaped_and_cannot_split_table_cells(tmp_path):
    result = evaluate(request(tmp_path))
    marker = 'a|b\n<script>alert("x")</script>`**[link](x)'
    result["per_page"][0]["family"] = marker
    result["operational"]["engine_identity"] = {"profile": marker}
    text = report(result, engine=marker)
    assert "<script>" not in text
    assert "&#124;" in text and "&lt;script&gt;" in text
    assert cell(marker) in text
    for line in text.splitlines():
        if line.startswith("| a&#124;"):
            assert "<script>" not in line


def test_rendering_is_deterministic_and_does_not_mutate_results(tmp_path):
    result = evaluate(request(tmp_path))
    before = copy.deepcopy(result)
    assert report(result) == report(result)
    assert result == before


def test_elapsed_per_page_uses_recorded_extraction_scope_not_evaluation_subset(tmp_path):
    result = evaluate(request(tmp_path, selected_page_scope=[1]))
    assert result["inputs"]["selected_pages"] == [1]
    assert result["operational"]["run_selected_pages"] == [1, 2, 3]
    text = report(result)
    assert "| 12.0000 | 4.0000 |" in section(text, "## Operacional — observacional")


def test_elapsed_without_recorded_extraction_scope_has_no_per_page_rate():
    result = evaluate(EvaluationRequest("Texto", "Texto", run_metadata={"elapsed_s": 12}))
    assert "| 12.0000 | — |" in section(report(result), "## Operacional — observacional")


def test_unknown_operational_outcome_does_not_claim_zero_failures(tmp_path):
    req = request(tmp_path)
    req.run_metadata["pages"] = [{"page": 1, "ocr_outcome": "unknown"}]
    text = section(report(evaluate(req)), "## Estabilidade e recovery")
    assert "0 (1/3 observadas)" not in text


@pytest.mark.parametrize("outcome", ["failure", "failed", "runtime_error", "error"])
def test_runtime_failure_outcomes_are_visible_in_operational_counts(tmp_path, outcome):
    req = request(tmp_path)
    req.run_metadata["pages"] = [{"page": 1, "ocr_outcome": outcome}]
    text = section(report(evaluate(req)), "## Estabilidade e recovery")
    assert "1 (1/3 observadas)" in text


@pytest.mark.parametrize("corpus,filename,selected", [
    ("V3", "Document_AI_V3.md", [1, 2, 3]),
    ("V5", "Document_AI_V5_REFERENCIA.md", [1, 12, 23]),
])
def test_real_corpus_reference_labels_and_canonical_page_identity(corpus, filename, selected):
    directory = ROOT / "corpus" / corpus
    path, manifesto = directory / filename, directory / f"Document_AI_{corpus}_MANIFESTO.json"
    if not path.is_file() or not manifesto.is_file():
        pytest.skip("Optional local reference and manifesto are absent")
    text = path.read_text(encoding="utf-8")
    result = evaluate(EvaluationRequest(text, text, manifesto_path=manifesto, selected_page_scope=selected))
    labels = corpus_page_metadata(manifesto)
    assert [p["page"] for p in result["per_page"]] == selected
    for page in result["per_page"]:
        assert page["family"] == labels[page["page"]]["family"]
        assert page["conditions"] == labels[page["page"]]["conditions"]
        assert metric_value(page, "text.cer_sequential_normalized") == 0
        assert not page["selected_but_missing"]
    assert "Breakdown por Família do Corpus" in report(result)


def test_same_engine_distinct_run_ids_do_not_collapse_columns():
    a = evaluate(EvaluationRequest("Texto", "Texto", engine="same", run_id="a"))
    b = evaluate(EvaluationRequest("Texto", "BAD", engine="same", run_id="b"))
    text = _render_comparison_table_v2([("same", a), ("same", b)])
    assert "**same / a** | **same / b**" in text
    assert "| CER Sequential Normalized ↓ | **0.0000** | 1.0000 |" in text


def test_ambiguous_and_identical_run_labels_are_uniquified():
    data = {"inputs": {"run_id": "r1"}}
    labels = unique_run_labels([("e", data), ("e", data), ("e / r1", data)])
    assert len(set(label for label, _ in labels)) == 3


def test_old_canonical_without_page_diagnostics_remains_readable():
    result = evaluate(EvaluationRequest("Texto", "Texto"))
    del result["per_page"]
    assert "Detalhamento por página indisponível" in report(result)


def test_reporting_policy_identifies_new_publication(tmp_path):
    req = request(tmp_path)
    result = evaluate(req)
    old = copy.deepcopy(result)
    del old["evaluator"]["reporting_policy"]
    kwargs = {"ref_text": req.ref_text, "hyp_text": req.hyp_text, "run_metadata": req.run_metadata}
    assert publication_identity(result, **kwargs) != publication_identity(old, **kwargs)


def cli(script, args):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / script), *map(str, args)],
                          cwd=ROOT, capture_output=True, text=True, timeout=60)


def cli_inputs(tmp_path):
    req = request(tmp_path)
    for name, text in (("ref.md", req.ref_text), ("hyp.md", req.hyp_text),
                       ("run.json", json.dumps(req.run_metadata))):
        (tmp_path / name).write_text(text, encoding="utf-8")
    return ["--reference", tmp_path / "ref.md", "--hypothesis", tmp_path / "hyp.md",
            "--manifesto", req.manifesto_path, "--run-manifest", tmp_path / "run.json",
            "--engine", req.engine, "--run-id", req.run_id, "--quiet"]


def test_both_clis_publish_identical_canonical_page_metrics_and_observations(tmp_path):
    args = cli_inputs(tmp_path)
    results = []
    for script, directory, filename in (("compute_metrics.py", "compute", "metrics_v3_rapidocr_test_report-r1.json"),
                                       ("regrade_existing.py", "regrade", "metrics_rapidocr-test_report-r1.json")):
        out = tmp_path / directory
        process = cli(script, [*args, "--output-dir", out])
        assert process.returncode == 0, process.stderr
        results.append(json.loads((out / filename).read_text()))
    assert results[0]["per_page"] == results[1]["per_page"]
    assert results[0]["quality"] == results[1]["quality"]
    assert results[0]["operational"] == results[1]["operational"]
    errors = (tmp_path / "regrade/errors_rapidocr-test_report-r1.md").read_text()
    for value in ("Breakdown por Família", "Breakdown por Condição", "recovered", "ocr_timeout", "100.0"):
        assert cell(value) in errors


def test_compare_cli_uses_canonical_input_ids_and_retains_repeated_engine_runs(tmp_path):
    req = request(tmp_path)
    paths = []
    for run_id, hyp in (("run-a", req.ref_text), ("run-b", req.hyp_text)):
        result = evaluate(replace(req, run_id=run_id, hyp_text=hyp))
        # A filename is not the engine identity.
        path = tmp_path / f"arbitrary-{run_id}.json"
        path.write_text(json.dumps(result))
        paths.append(path)
    out = tmp_path / "comparison.md"
    process = cli("compare_engines.py", [*paths, "--output", out, "--quiet"])
    assert process.returncode == 0, process.stderr
    text = out.read_text()
    assert "**rapidocr-test / run-a** | **rapidocr-test / run-b**" in text
    assert "arbitrary-" not in text
    assert "recovered" in text and "Breakdown por Condição" in text


@pytest.mark.parametrize("script", ["compute_metrics.py", "regrade_existing.py"])
def test_invalid_corpus_labels_fail_cli_before_publication(tmp_path, script):
    args = cli_inputs(tmp_path)
    (tmp_path / "corpus.json").write_text('{"pages": [{"page": 1, "conditions": [null]}]}')
    out = tmp_path / "out"
    process = cli(script, [*args, "--output-dir", out])
    assert process.returncode != 0
    assert not list(out.glob("metrics*.json"))
