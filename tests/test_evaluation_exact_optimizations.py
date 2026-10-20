"""Independent exhaustive/brute oracles for exact shortcuts and legacy adapters."""
from __future__ import annotations

import itertools
import random
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.compute_metrics import _parse_md_tables, compute_structure_metrics as legacy_structure
from scripts.compute_metrics import compute_table_metrics as legacy_tables
from structured_pdf_text.evaluation import EvaluationRequest, evaluate
from structured_pdf_text.evaluation import block_alignment as blocks
from structured_pdf_text.evaluation import table_metrics as tables
from structured_pdf_text.evaluation.markdown_parser import parse_document
from structured_pdf_text.evaluation.models import NodeKind
from structured_pdf_text.evaluation.order_counts import concordance_counts
from structured_pdf_text.evaluation.reference_inputs import load_reference_text


def test_strict_order_counts_against_exhaustive_tied_sequences():
    for length in range(8):
        for values in itertools.product(range(3), repeat=length):
            expected = sum(values[i] < values[j] for i in range(length) for j in range(i + 1, length))
            assert concordance_counts(values) == (expected, length * (length - 1) // 2)


@pytest.mark.parametrize("ordering,expected", [("ascending", 4_999_950_000),
                                                ("descending", 0), ("ties", 0)])
def test_large_order_counts_remain_exact(ordering, expected):
    values = list(range(100_000))
    if ordering == "descending":
        values.reverse()
    elif ordering == "ties":
        values = [1] * 100_000
    assert concordance_counts(values) == (expected, 4_999_950_000)


@pytest.mark.parametrize("body", [
    "Alfa\n\nBeta", "Repetido\n\nRepetido\n\nRepetido", "# Título\n\n- A\n- A",
    "| A | A |\n|---|---|\n| A | A |", "<table><tr><td></td></tr></table>",
    "## Página 1\n\nA\n\n## Página 2\n\nA", "```\n**literal**\n```",
])
@pytest.mark.parametrize("min_score,margin", [(0.3, 0.05), (0.9, 0.5), (0, 1), (1.1, 0.05)])
def test_identity_shortcut_matches_complete_greedy_result(body, min_score, margin):
    ref, hyp = parse_document(body, origin="reference"), parse_document(body, origin="hypothesis")
    actual = blocks.align_blocks(ref, hyp, min_score=min_score, ambiguity_margin=margin)
    with patch.object(blocks, "_identity_pairs", return_value=None):
        expected = blocks.align_blocks(ref, hyp, min_score=min_score, ambiguity_margin=margin)
    assert [asdict(p) for p in actual] == [asdict(p) for p in expected]


def test_identity_shortcut_handles_large_documents_with_tiny_candidate_budget():
    body = "\n\n".join(f"Parágrafo {index}" for index in range(600))
    result = evaluate(EvaluationRequest(body, body, max_alignment_pairs=1))
    assert result["evaluation"]["status"] == "ok"
    assert result["quality"]["text"]["aligned_block_text_cer"]["value"] == 0
    assert result["quality"]["reading_order"]["pairwise_reading_order_accuracy"]["value"] == 1


@pytest.mark.parametrize("hyp", ["# Alfa\n\nBeta", "Beta\n\nAlfa", "Alfa\n\nBeta\n\nExtra"])
def test_nonidentity_documents_still_fail_closed_at_candidate_budget(hyp):
    ref_nodes = parse_document("Alfa\n\nBeta", origin="reference")
    hyp_nodes = parse_document(hyp, origin="hypothesis")
    with pytest.raises(blocks.AlignmentResourceLimit):
        blocks.align_blocks(ref_nodes, hyp_nodes, max_candidate_pairs=1)


def test_raw_and_ordinal_errors_are_not_hidden_by_normalized_identity():
    result = evaluate(EvaluationRequest("1. **Alfa**\n2. Beta", "5. Alfa\n6. Beta"))
    assert result["quality"]["text"]["cer_markdown_raw"]["value"] > 0
    assert result["quality"]["structure"]["ordered_list_ordinal_accuracy"]["value"] == 0


def greedy_tables(ref, hyp, ref_map, hyp_map, threshold):
    scores = [(tables._table_similarity(r, h, ref_map, hyp_map), ri, hi)
              for ri, r in enumerate(ref) for hi, h in enumerate(hyp)]
    scores.sort(key=lambda entry: entry[0], reverse=True)
    result, used_ref, used_hyp = [], set(), set()
    for score, ri, hi in scores:
        if score >= threshold and ri not in used_ref and hi not in used_hyp:
            result.append((ref[ri], hyp[hi], score))
            used_ref.add(ri)
            used_hyp.add(hi)
    return result


@pytest.mark.parametrize("threshold", [0, 0.2, 1, 1.1])
def test_identical_table_shortcuts_match_brute_greedy_oracle(threshold):
    rng = random.Random(3608)
    for _ in range(20):
        body = "\n\n".join("| A | B |\n|---|---|\n| " + rng.choice(["A", "B", ""]) + " | |"
                            for _ in range(rng.randrange(1, 8)))
        ref_nodes, hyp_nodes = parse_document(body, origin="reference"), parse_document(body, origin="hypothesis")
        ref, hyp = ([n for n in nodes if n.kind == NodeKind.TABLE] for nodes in (ref_nodes, hyp_nodes))
        ref_map, hyp_map = tables._build_parent_map(ref_nodes), tables._build_parent_map(hyp_nodes)
        assert tables._match_tables(ref, hyp, ref_map, hyp_map, threshold) == greedy_tables(
            ref, hyp, ref_map, hyp_map, threshold)


@pytest.mark.parametrize("body", ["| A | B |\n|---|---|\n| | |", "| A | A |\n|---|---|\n| A | A |\n| A | A |"])
def test_identical_row_shortcut_matches_brute_greedy_oracle(body):
    ref = [n for n in parse_document(body, origin="reference") if n.kind == NodeKind.TABLE_ROW]
    hyp = [n for n in parse_document(body, origin="hypothesis") if n.kind == NodeKind.TABLE_ROW]
    candidates = [(tables._text_sim(r.visible_text, h.visible_text), ri, hi)
                  for ri, r in enumerate(ref) for hi, h in enumerate(hyp)]
    candidates.sort(key=lambda item: item[0], reverse=True)
    expected, used_ref, used_hyp = [], set(), set()
    for score, ri, hi in candidates:
        if score >= 0.2 and ri not in used_ref and hi not in used_hyp:
            expected.append((ref[ri], hyp[hi]))
            used_ref.add(ri)
            used_hyp.add(hi)
    assert tables._match_rows(ref, hyp) == expected


def test_table_identity_matching_does_not_hide_topology_changes():
    ref = "<table><tr><td>A</td><td>B</td></tr></table>"
    hyp = "<table><tr><td>A</td></tr><tr><td>B</td></tr></table>"
    result = evaluate(EvaluationRequest(ref, hyp))["quality"]["tables"]
    assert result["table_detection_f1"]["value"] == 1
    assert result["table_column_count_accuracy"]["value"] == 0


def test_legacy_adapter_preserves_all_empty_cells():
    body = "| A | B |\n|---|---|\n| | |"
    assert _parse_md_tables(body) == [[["A", "B"], ["", ""]]]
    assert legacy_tables("", body)["cell_ref_count"] == 4


@pytest.mark.parametrize("body,expected", [
    (r"| A | B |" + "\n|---|---|\n" + r"| a\|b | c |", [[["A", "B"], ["a|b", "c"]]]),
    ("a | b\nc | d", []), ("```\n| A | B |\n|---|---|\n| 1 | 2 |\n```", []),
    ("<table><tr><td>A</td><td></td></tr></table>", [[["A", ""]]]),
])
def test_legacy_adapter_uses_canonical_grammar(body, expected):
    assert _parse_md_tables(body) == expected


def test_legacy_heading_cer_measures_spelling_errors():
    metric = legacy_structure("# Titulo correto", "# Título correto")
    assert metric["heading_text_cer"] > 0


@pytest.mark.parametrize("version,stem,reference,pages", [
    ("V3", "Document_AI_V3", "Document_AI_V3.md", 144),
    ("V4", "Corpus_Stress_OCR_Markdown_V4", None, 224),
    ("V5", "Document_AI_V5", "Document_AI_V5_REFERENCIA.md", 42),
])
def test_full_ground_truth_self_control_is_complete_and_perfect(version, stem, reference, pages):
    directory = Path(__file__).resolve().parents[1] / "corpus" / version
    manifesto = directory / f"{stem}_MANIFESTO.json"
    # V4's Markdown intentionally omits two blank pages; its manifesto declares
    # their empty expected_markdown explicitly and covers all 224 PDF pages.
    path = directory / reference if reference else None
    text = load_reference_text(path, manifesto)
    result = evaluate(EvaluationRequest(text, text, ref_path=path, manifesto_path=manifesto))
    assert len(result["per_page"]) == pages
    assert result["evaluation"]["status"] == "ok"
    assert result["evaluation"]["rank_eligible"]
    for group in [*result["quality"].values(), result["integrity"]]:
        for name, metric in group.items():
            if isinstance(metric, dict) and metric.get("value") is not None and "direction" in metric:
                assert metric["value"] == (0 if metric["direction"] == "lower_is_better" else 1), name
