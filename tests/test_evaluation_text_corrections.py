"""Exact text metric regressions, with an independent full-matrix oracle."""
from __future__ import annotations

import itertools
import pickle
import random
import subprocess
import sys
import tracemalloc
from dataclasses import replace
from pathlib import Path

import pytest

from structured_pdf_text.evaluation import (
    EvaluationRequest, evaluate, parse_document, CanonicalDocument,
)
from structured_pdf_text.evaluation.edit_distance import (
    EditDistanceBudget, EditDistanceResourceLimit, _band_cells, levenshtein_ops,
)
from structured_pdf_text.evaluation.models import AlignedPair, AlignmentStatus, NodeKind
from structured_pdf_text.evaluation.text_metrics import cer_markdown_raw, aligned_block_text_cer


def oracle(ref, hyp):
    """Previous documented traceback, deliberately quadratic on small inputs."""
    rows, cols = len(ref), len(hyp)
    dp = [[0] * (cols + 1) for _ in range(rows + 1)]
    for i in range(rows + 1):
        dp[i][0] = i
    for j in range(cols + 1):
        dp[0][j] = j
    for i in range(1, rows + 1):
        for j in range(1, cols + 1):
            dp[i][j] = min(dp[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]),
                           dp[i - 1][j] + 1, dp[i][j - 1] + 1)
    s = d = ins = 0
    i, j = rows, cols
    while i or j:
        if i and j and ref[i - 1] == hyp[j - 1]:
            i, j = i - 1, j - 1
        elif i and j and dp[i][j] == dp[i - 1][j - 1] + 1:
            s, i, j = s + 1, i - 1, j - 1
        elif i and dp[i][j] == dp[i - 1][j] + 1:
            d, i = d + 1, i - 1
        else:
            ins, j = ins + 1, j - 1
    return s, d, ins


def test_exact_counts_match_oracle_exhaustively():
    strings = ["".join(chars) for n in range(6) for chars in itertools.product("ab", repeat=n)]
    for ref, hyp in itertools.product(strings, repeat=2):
        assert levenshtein_ops(ref, hyp) == oracle(ref, hyp), (ref, hyp)


def test_exact_counts_match_oracle_on_random_word_tokens():
    generator = random.Random(183)
    for _ in range(400):
        ref = generator.choices(["ação", "a", "b", "R$", "10%"], k=generator.randrange(40))
        hyp = generator.choices(["ação", "a", "b", "R$", "10%"], k=generator.randrange(40))
        assert levenshtein_ops(ref, hyp) == oracle(ref, hyp)


def test_band_reservation_counts_every_state():
    for rows, cols, width in itertools.product(range(12), repeat=3):
        expected = sum(abs(i - j) <= width for i in range(rows + 1) for j in range(cols + 1))
        assert _band_cells(rows, cols, width) == expected


@pytest.mark.parametrize("ref,hyp,expected", [
    ("", "", (0, 0, 0)), ("", "a" * 100_000, (0, 0, 100_000)),
    ("a" * 100_000, "", (0, 100_000, 0)),
    ("ação " * 140_000, "ação " * 140_000, (0, 0, 0)),
])
def test_large_trivial_cases_do_not_need_dp_budget(ref, hyp, expected):
    assert levenshtein_ops(ref, hyp, max_cells=1) == expected


def test_large_single_edit_is_exact_without_quadratic_matrix():
    ref = "a" * 320_000 + "b" + "c" * 320_000
    hyp = "a" * 320_000 + "x" + "c" * 320_000
    assert levenshtein_ops(ref, hyp, max_cells=2_000_000) == (1, 0, 0)


def test_banded_workspace_stays_small():
    ref = "a" * 5_000 + "b" + "c" * 5_000
    hyp = "a" * 5_000 + "x" + "c" * 5_000
    tracemalloc.start()
    try:
        assert levenshtein_ops(ref, hyp) == (1, 0, 0)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1_000_000


def test_budget_exhaustion_does_not_return_truncated_counts():
    with pytest.raises(EditDistanceResourceLimit) as error:
        levenshtein_ops("a" * 100_000, "b" * 100_000, max_cells=100)
    assert error.value.max_cells == 100
    assert error.value.evaluated_cells == 0


def test_resource_exception_survives_serialization():
    original = EditDistanceResourceLimit(42, 17)
    recovered = pickle.loads(pickle.dumps(original))
    assert recovered.max_cells == 42
    assert recovered.evaluated_cells == 17


def test_resource_exception_propagates_from_spawn_worker():
    # Other OCR tests replace module-level loggers with mocks. A fresh parent
    # isolates multiprocessing's own logger setup and still exercises spawn.
    source_root = Path(__file__).resolve().parents[1] / "src"
    code = f"""
import sys, multiprocessing
from concurrent.futures import ProcessPoolExecutor
sys.path.insert(0, {str(source_root)!r})
from structured_pdf_text.evaluation.edit_distance import levenshtein_ops, EditDistanceResourceLimit
with ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context('spawn')) as pool:
    future = pool.submit(levenshtein_ops, 'aaa', 'bbb', max_cells=1)
    try:
        future.result(timeout=20)
    except EditDistanceResourceLimit as error:
        assert error.max_cells == 1
        assert error.evaluated_cells == 0
    else:
        raise AssertionError('Worker must report the exact resource exception')
"""
    result = subprocess.run([sys.executable, "-c", code], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_metric_budget_accumulates_across_block_distances():
    work = EditDistanceBudget(20)
    assert levenshtein_ops("abc", "abX", budget=work) == (1, 0, 0)
    assert work.evaluated_cells == 10
    assert levenshtein_ops("abc", "abX", budget=work) == (1, 0, 0)
    with pytest.raises(EditDistanceResourceLimit):
        levenshtein_ops("abc", "abX", budget=work)


def test_aligned_metric_does_not_publish_partial_block_counts_as_a_score():
    ref = parse_document("abc\n\nuvw", "reference")
    hyp = parse_document("abX\n\nuvY", "hypothesis")
    pairs = [AlignedPair(r.node_id, h.node_id, AlignmentStatus.MATCHED) for r, h in zip(ref, hyp)]
    result = aligned_block_text_cer(ref, hyp, pairs, max_edit_cells=15)
    assert result.status.value == "resource_limit"
    assert result.value is None
    assert "edit_distance" not in result.counts


@pytest.mark.parametrize("budget", [0, -1, True, 1.5, None])
def test_invalid_edit_budgets_are_rejected(budget):
    with pytest.raises(ValueError):
        levenshtein_ops("a", "a", max_cells=budget)


@pytest.mark.parametrize("ref,hyp", [
    ("**importante**", "importante"), ("[link](https://example.test)", "link"),
    ("- Alfa\n- Beta", "Alfa\n\nBeta"),
    ("| A | B |\n|---|---|\n| C | D |", "A B C D"),
    ("# Título\n\n\nTexto.", "# Título\n\nTexto."),
    ("<!-- comentário -->\nTexto.", "Texto."),
])
def test_raw_cer_uses_original_body(ref, hyp):
    result = evaluate(EvaluationRequest(ref, hyp))["quality"]["text"]["cer_markdown_raw"]
    expected = oracle(ref, hyp)
    assert tuple(result["counts"][key] for key in ("S", "D", "I")) == expected
    assert result["counts"]["ref_chars"] == len(ref)
    assert result["value"] == round(sum(expected) / len(ref), 6)


def test_raw_node_api_keeps_source_markdown_and_blank_lines():
    ref = "**importante**\n\n\n- item"
    hyp = "importante\n\nitem"
    result = cer_markdown_raw(parse_document(ref, "reference"), parse_document(hyp, "hypothesis"))
    assert result.counts["ref_chars"] == len(ref)
    assert result.counts["edit_distance"] == sum(oracle(ref, hyp))
    assert CanonicalDocument.from_nodes(parse_document(ref)).raw_markdown == ref


@pytest.mark.parametrize("ref,hyp,visible", [
    ("a**b**c", "abc", "abc"),
    ("a`b`c", "abc", "abc"),
    ("a[b](https://example.test)c", "abc", "abc"),
    (r"a\*b\*c", "a`*b*`c", "a*b*c"),
    ("a&amp;b", "a&b", "a&b"),
])
def test_adjacent_inline_runs_are_not_separated(ref, hyp, visible):
    actual = CanonicalDocument.from_nodes(parse_document(ref)).visible_text
    assert actual == visible
    for key in ("cer_sequential_visible", "cer_sequential_normalized", "wer_sequential"):
        assert evaluate(EvaluationRequest(ref, hyp))["quality"]["text"][key]["value"] == 0


@pytest.mark.parametrize("literal", ["**literal**", "[x](y)", "# título", "| A | B |", "<!-- literal -->"])
def test_code_content_is_not_reparsed_as_markdown(literal):
    ref = f"```\n{literal}\n```"
    nodes = parse_document(ref)
    assert CanonicalDocument.from_nodes(nodes).visible_text == literal
    result = evaluate(EvaluationRequest(ref, "```\nsubstituído\n```"))
    assert result["quality"]["text"]["cer_sequential_visible"]["value"] > 0


@pytest.mark.parametrize("body", [
    "| Nome | Valor |\n|---|---|\n| Ana | 100 |",
    "- Alfa\n  - Beta\n- Gama", "> Alfa\n>\n> Beta",
])
def test_aligned_cer_counts_only_terminal_text(body):
    nodes = parse_document(body)
    expected = sum(len(n.visible_text) for n in CanonicalDocument.from_nodes(nodes).leaf_nodes)
    result = evaluate(EvaluationRequest(body, body))["quality"]["text"]["aligned_block_text_cer"]
    assert result["value"] == 0
    assert result["counts"]["ref_chars"] == expected


def test_text_resource_failure_is_explicit_and_prevents_ranking():
    request = EvaluationRequest("a" * 80, "b" * 80, max_text_edit_cells=10)
    result = evaluate(request)
    for key in ("cer_markdown_raw", "cer_sequential_normalized", "cer_sequential_visible", "aligned_block_text_cer"):
        metric = result["quality"]["text"][key]
        assert metric["value"] is None
        assert metric["status"] == "resource_limit"
        assert metric["warnings"]
    assert result["evaluation"]["status"] == "partial"
    assert not result["evaluation"]["rank_eligible"]
    assert "evaluation_resource_limit" in result["evaluation"]["rank_ineligibility_reasons"]
    assert result["operational"]["benchmark_status"] == "valid"
    assert result["evaluator"]["resource_policy"]["max_text_edit_cells"] == 10


def test_alignment_budget_is_not_reported_as_all_blocks_missing():
    body = "Alfa\n\nBeta"
    result = evaluate(EvaluationRequest(body, body, max_alignment_pairs=1))
    assert result["quality"]["text"]["cer_sequential_normalized"]["value"] == 0
    assert result["quality"]["text"]["aligned_block_text_cer"]["status"] == "resource_limit"
    for group in ("structure", "reading_order"):
        assert all(m["status"] == "resource_limit" and m["value"] is None
                   for m in result["quality"][group].values())
    assert result["operational"]["alignment_summary"]["status"] == "resource_limit"
    assert not result["evaluation"]["rank_eligible"]


def test_large_block_similarity_reports_resource_limit_without_stalling():
    result = evaluate(EvaluationRequest("a" * 8_000, "a" * 7_999 + "b"))
    assert result["quality"]["text"]["cer_sequential_normalized"]["counts"]["S"] == 1
    assert result["quality"]["structure"]["paragraph_detection_f1"]["status"] == "resource_limit"
    assert not result["evaluation"]["rank_eligible"]


@pytest.mark.parametrize("pairs", [[], [AlignedPair("r", "h", AlignmentStatus.MATCHED)] * 2])
def test_incomplete_or_duplicate_text_alignment_is_invalid(pairs):
    ref = parse_document("Alfa", "reference")
    hyp = parse_document("Alfa", "hypothesis")
    if pairs:
        pairs = [replace(p, reference_id=ref[0].node_id, hypothesis_id=hyp[0].node_id) for p in pairs]
    result = aligned_block_text_cer(ref, hyp, pairs)
    assert result.value is None
    assert result.status.value == "invalid_input"


def test_parser_keeps_indented_code_after_page_marker():
    nodes = parse_document("## Página 1\n\n    **literal**")
    assert len(nodes) == 1
    assert nodes[0].kind == NodeKind.CODE
    assert nodes[0].visible_text == "**literal**"


def test_raw_metric_normalizes_unicode_and_line_endings_only():
    ref, hyp = "Ação\r\n**a**", "Ac\u0327a\u0303o\n**a**"
    result = cer_markdown_raw([], [], ref_markdown=ref, hyp_markdown=hyp)
    assert result.value == 0
    result = cer_markdown_raw([], [], ref_markdown="a  b", hyp_markdown="a b")
    assert result.value > 0
