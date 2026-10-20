"""Stage 5: independent token/path oracles, structural parsing and API controls."""
from __future__ import annotations

import itertools
import json
import random
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import pytest

from structured_pdf_text.evaluation import EvaluationRequest, evaluate
from structured_pdf_text.evaluation.block_alignment import align_blocks
from structured_pdf_text.evaluation.edit_distance import EditDistanceBudget, EditDistanceResourceLimit
from structured_pdf_text.evaluation.integrity_metrics import markdown_validity_report
from structured_pdf_text.evaluation.markdown_parser import PARSER_VERSION, parse_document, parse_page
from structured_pdf_text.evaluation.markdown_syntax import page_markers
from structured_pdf_text.evaluation.models import NodeKind
from structured_pdf_text.evaluation.paragraph_boundaries import matching_gaps
from structured_pdf_text.evaluation.reference_hash import compute_reference_effective_hash, split_pages
from structured_pdf_text.evaluation.reference_inputs import manifest_pages
from structured_pdf_text.evaluation.structure_metrics import compute_structure_metrics, paragraph_detection_f1

ROOT = Path(__file__).resolve().parents[1]


def metrics(ref, hyp):
    return evaluate(EvaluationRequest(ref_text=ref, hyp_text=hyp))["quality"]["structure"]


def full_matrix_vertices(a, b):
    """Independent full-matrix oracle, including every canonical traceback gap."""
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i, x in enumerate(a, 1):
        for j, y in enumerate(b, 1):
            d[i][j] = min(d[i-1][j-1] + (x != y), d[i-1][j] + 1, d[i][j-1] + 1)
    i, j = len(a), len(b)
    vertices = {(i, j)}
    while i or j:
        if i and j and d[i][j] == d[i-1][j-1] + (a[i-1] != b[j-1]):
            i, j = i-1, j-1
        elif i and d[i][j] == d[i-1][j] + 1:
            i -= 1
        else:
            j -= 1
        vertices.add((i, j))
    return vertices


def test_exact_gap_path_exhaustive_small_repeated_sequences():
    sequences = [list(p) for n in range(5) for p in itertools.product("ab", repeat=n)]
    for ref, hyp in itertools.product(sequences, repeat=2):
        actual = matching_gaps(ref, hyp, set(range(len(ref)+1)), set(range(len(hyp)+1)),
                               EditDistanceBudget(100_000))
        assert actual == full_matrix_vertices(ref, hyp), (ref, hyp)


def test_exact_gap_path_random_longer_sequences_and_filtered_vertices():
    rng = random.Random(183)
    for _ in range(200):
        ref = [rng.choice("abcd") for _ in range(rng.randrange(1, 35))]
        hyp = [rng.choice("abcd") for _ in range(rng.randrange(1, 35))]
        rg = {i for i in range(len(ref)+1) if rng.random() < .5}
        hg = {j for j in range(len(hyp)+1) if rng.random() < .5}
        expected = {(i, j) for i, j in full_matrix_vertices(ref, hyp) if i in rg and j in hg}
        assert matching_gaps(ref, hyp, rg, hg, EditDistanceBudget(100_000)) == expected


def test_gap_budget_is_reserved_before_allocation_and_shared():
    work = EditDistanceBudget(7)
    matching_gaps(["a"], ["b"], {0, 1}, {0, 1}, work)
    assert work.evaluated_cells == 4
    with pytest.raises(EditDistanceResourceLimit):
        matching_gaps(["c"], ["d"], {0, 1}, {0, 1}, work)
    assert work.evaluated_cells == 4


def test_long_single_token_edit_has_linear_band_work():
    ref = ["a"] * 20_000
    hyp = ref.copy()
    hyp[10_000] = "b"
    work = EditDistanceBudget(61_000)
    assert matching_gaps(ref, hyp, {0, 10_000, 20_000}, {0, 10_000, 20_000}, work) == {
        (0, 0), (10_000, 10_000), (20_000, 20_000)}
    assert work.evaluated_cells < 61_000


@pytest.mark.parametrize("ref,hyp,detection,boundary", [
    ("Long paragraph one\n\nShort", "Long\n\nparagraph one Short", 0, 0),
    ("a b\n\nc d", "a b c d", 0, 0),
    ("a b c d", "a b\n\nc d", 0, 0),
    ("a a\n\na a", "a\n\na a a", 0, 0),
    ("um dois\n\ntrês quatro", "um dois\n\ntrês quatro", 1, 1),
    ("um dois\n\ntrês quatro", "um erro\n\ntrês quatro", 1, 1),
    ("um dois", "um dois", 1, None),
    ("", "", None, None),
    ("# Título", "# Título", None, None),
    ("a\n\nb\n\nc", "a\n\nc", .8, .6667),
])
def test_paragraph_spans_and_internal_boundaries(ref, hyp, detection, boundary):
    m = metrics(ref, hyp)
    assert m["paragraph_detection_f1"]["value"] == detection
    assert m["paragraph_boundary_f1"]["value"] == boundary


def test_all_cuts_of_identical_stream_have_exact_boundary_counts():
    # Every split of the same token sequence; content cannot obscure the oracle.
    words = ["alfa", "beta", "gama", "delta", "épsilon"]
    def document(cuts):
        return "".join(word + ("\n\n" if i in cuts else " ")
                       for i, word in enumerate(words, 1)).rstrip()
    cuts = [set(i+1 for i in range(4) if mask & (1 << i)) for mask in range(16)]
    for r, h in itertools.product(cuts, repeat=2):
        ref = parse_document(document(r), "reference")
        hyp = parse_document(document(h), "hypothesis")
        result = paragraph_detection_f1(align_blocks(ref, hyp), ref, hyp)
        assert result["paragraph_boundary_f1"].counts == {
            "tp": len(r & h), "fp": len(h-r), "fn": len(r-h)}
        def spans(c):
            endpoints = [0, *sorted(c), len(words)]
            return set(zip(endpoints, endpoints[1:]))
        exact_spans = len(spans(r) & spans(h))
        assert result["paragraph_detection_f1"].counts == {
            "tp": exact_spans, "fp": len(spans(h)) - exact_spans, "fn": len(spans(r)) - exact_spans}


def test_paragraph_boundary_budget_exhaustion_returns_null_metrics():
    ref, hyp = parse_page("a b\n\nc d", 1, "reference"), parse_page("w x\n\ny z", 1)
    m = paragraph_detection_f1(align_blocks(ref, hyp), ref, hyp, max_edit_cells=1)
    assert len(m) == 6
    assert all(result.value is None and result.status.value == "resource_limit" for result in m.values())


def test_boundary_budget_failure_reaches_document_status():
    result = evaluate(EvaluationRequest(ref_text="a b\n\nc d", hyp_text="w x\n\ny z", max_text_edit_cells=1))
    assert result["quality"]["structure"]["paragraph_boundary_f1"]["status"] == "resource_limit"
    assert result["evaluation"]["rank_eligible"] is False


def test_paragraphs_moved_to_another_page_are_not_detected():
    ref = "## Página 1\n\na\n\nb\n\n## Página 2\n\nc"
    hyp = "## Página 1\n\nc\n\n## Página 2\n\na\n\nb"
    m = metrics(ref, hyp)
    assert m["paragraph_detection_f1"]["value"] < 1
    assert m["paragraph_boundary_f1"]["value"] == 0


@pytest.mark.parametrize("start", [0, 1, 5, 999_999_998])
@pytest.mark.parametrize("delimiter", [".", ")"])
def test_list_effective_ordinals_preserve_start_and_increment(start, delimiter):
    nodes = parse_document(f"{start}{delimiter} Primeiro\n1{delimiter} Segundo")
    container = next(n for n in nodes if n.kind == NodeKind.LIST)
    items = [n for n in nodes if n.kind == NodeKind.LIST_ITEM]
    assert container.metadata["list_start"] == start
    assert [n.metadata["ordered_ordinal"] for n in items] == [start, start+1]
    assert [n.visible_text for n in items] == ["Primeiro", "Segundo"]


@pytest.mark.parametrize("ref,hyp,value", [
    ("1. Primeiro\n2. Segundo", "5. Primeiro\n6. Segundo", 0),
    ("0. Primeiro\n1. Segundo", "1. Primeiro\n2. Segundo", 0),
    ("1. Primeiro\n1. Segundo", "1. Primeiro\n99. Segundo", 1),
    ("1) Primeiro\n2) Segundo", "1. Primeiro\n2. Segundo", 1),
    ("1. Primeiro\n2. Segundo", "- Primeiro\n- Segundo", 0),
])
def test_ordinal_metric_uses_rendered_numbering(ref, hyp, value):
    result = metrics(ref, hyp)["ordered_list_ordinal_accuracy"]
    assert result["value"] == value
    assert result["counts"]["ordinal_total"] == 2


def test_nested_list_ordinals_reset_per_container():
    nodes = parse_document("3. Pai\n\n   7. Filho\n   8. Outro\n4. Irmão")
    items = [n for n in nodes if n.kind == NodeKind.LIST_ITEM]
    assert [n.metadata["ordered_ordinal"] for n in items] == [3, 7, 8, 4]


LITERAL_MARKERS = [
    "```markdown\n## Página 2\n```", "````\n```\n## Página 2\n````",
    "~~~markdown\n## Página 2\n~~~", "    ## Página 2",
    "> ## Página 2", "- ## Página 2", "<!--\n## Página 2\n-->",
    "<pre>\n## Página 2\n</pre>", "<div>\n## Página 2\n</div>",
    "## Página\n2", "## Página 2abc", "\\## Página 2"
]


@pytest.mark.parametrize("literal", LITERAL_MARKERS)
def test_page_marker_must_be_real_top_level_heading(literal):
    text = f"## Página 1\n\n{literal}\n\n## Página 3\n\nÚltima."
    assert list(split_pages(text)) == [1, 3]
    # Hidden HTML comments need not produce a structural node for page 1.
    node_pages = {n.page for n in parse_document(text)}
    assert node_pages <= {1, 3} and 3 in node_pages
    assert [m.page for m in page_markers(text)] == [1, 3]
    assert markdown_validity_report(text).duplicate_page_markers == 0


@pytest.mark.parametrize("literal", LITERAL_MARKERS[:9])
def test_manifest_body_with_literal_marker_keeps_its_own_page(literal):
    assert manifest_pages({"pages": [{"page": 7, "expected_markdown": literal}]}) == {7: literal}


def test_fake_duplicate_marker_in_code_does_not_invalidate_markdown():
    text = "## Página 1\n\n```\n## Página 1\n```"
    assert markdown_validity_report(text).validity_status == "valid"
    assert list(split_pages(text)) == [1]


@pytest.mark.parametrize("html", ["<table><tr><td>A</td></tr></table>",
                                  "<div>\nConteúdo.\n</div>", "<hr>", "<!-- comentário -->"])
def test_page_marker_immediately_after_completed_html_delimits_next_page(html):
    text = f"## Página 1\n\n{html}\n## Página 2\n\nSegunda."
    assert list(split_pages(text)) == [1, 2]
    assert {n.page for n in parse_document(text)} <= {1, 2}
    assert split_pages(text)[2] == "Segunda."


@pytest.mark.parametrize("tail", ["```\n## Página 2\n```", "~~~\n## Página 2\n~~~",
                                  "Texto literal\n## Página 2"])
def test_completed_html_does_not_expose_later_literal_markers(tail):
    assert list(split_pages(f"## Página 1\n\n<hr>\n{tail}")) == [1]


def test_actual_duplicate_markers_normalize_page_ids():
    text = "## Página 001\n\nA\n\n## pagina 1 | B"
    for parser in [parse_document, split_pages]:
        with pytest.raises(ValueError, match="Duplicate"):
            parser(text)
    assert markdown_validity_report(text).duplicate_page_markers == 1


def test_public_parser_preserves_physical_page_order_and_offsets():
    text = "## Página 3\r\n\r\nTerceira.\r\n\r\n## Página 1\r\n\r\nPrimeira."
    nodes = parse_document(text)
    assert [n.page for n in nodes] == [3, 1]
    assert [n.order_index for n in nodes] == [0, 1]
    for node in nodes:
        assert text[node.source_start:node.source_end].strip() == node.visible_text
        assert node.source_end <= len(text)


@pytest.mark.parametrize("ending", ["\n", "\r\n", "\r"])
def test_all_source_spans_retain_actual_character_positions(ending):
    text = ending.join(["## Página 2", "", "Primeiro.", "", "Segundo.", "", "## Página 1", "", "Final."])
    nodes = parse_document(text)
    assert [n.visible_text for n in nodes] == ["Primeiro.", "Segundo.", "Final."]
    for node in nodes:
        assert text[node.source_start:node.source_end].strip() == node.visible_text
        assert node.raw_markdown == node.visible_text
    # Remove only the last source-line terminator, retaining a blank line that
    # belongs to a list's token map (Python '$' can match before final LF too).
    container = parse_page(f"- A{ending}{ending}Texto", 1)[0]
    assert container.raw_markdown == "- A" + ending


def test_hash_treats_code_marker_as_literal_content_and_keeps_protocol():
    a = split_pages("## Página 1\n\n```\n## Página 2\n```")
    b = split_pages("## Página 1\n\n```\n## Página 9\n```")
    ah, meta = compute_reference_effective_hash(a)
    bh, _ = compute_reference_effective_hash(b)
    assert ah != bh
    assert meta["selected_pages"] == [1]
    assert meta["reference_serialization_version"] == "reference-effective-v1"


@pytest.mark.parametrize("ending", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("label", ["Página", "pagina", "PA\u0301GINA"])
def test_page_marker_line_endings_and_unicode_labels(ending, label):
    text = f"## {label} 003 | metadados{ending}{ending}Texto."
    assert split_pages(text) == {3: "Texto."}
    assert {n.page for n in parse_document(text)} == {3}


def test_unicode_line_separator_does_not_change_parser_line_numbers():
    text = "alfa\u2028beta\n\n## Página 7\n\nTexto."
    assert [marker.page for marker in page_markers(text)] == [7]
    node = parse_document(text)[0]
    assert text[node.source_start:node.source_end] == "Texto."


@pytest.mark.parametrize("text", [
    "````markdown\n```\n````", "```\nx\n````", "~~~\nx\n~~~~",
    "  ```\nx\n  ```", "> ```\n> x\n> ```", "- ```\n  x\n  ```",
    "```\n~~~\n```", "~~~\n```\n~~~", "    ```\n    literal",
    "<!--\n```\n-->", "<pre>\n```\n</pre>", "inline ` ``` `",
    "```\n\n```", "```\n```", "```\r\nx\r\n```",
])
def test_valid_fences_are_not_rejected(text):
    result = markdown_validity_report(text)
    assert result.unclosed_fences == result.parse_errors == 0
    assert result.validity_status == "valid"


@pytest.mark.parametrize("text,count", [
    ("```", 1), ("```\nx", 1), ("```\nx\n", 1), ("~~~\nx", 1),
    ("````\n```", 1), ("```\nx\n```suffix", 1), ("```\nx\n    ```", 1),
    ("> ```\n> x\n\n- ~~~\n  y", 2), ("  ~~~\nx\n~~", 1),
    ("```\nx\n~~~", 1), ("~~~\nx\n```", 1),
])
def test_unterminated_fences_count_each_block(text, count):
    result = markdown_validity_report(text)
    assert result.unclosed_fences == result.parse_errors == count
    assert result.validity_status == "invalid"


def test_parser_identity_and_policy_provenance_are_current():
    result = evaluate(EvaluationRequest(ref_text="a", hyp_text="a"))
    assert version("markdown-it-py") in PARSER_VERSION
    assert result["evaluator"]["parser"] == PARSER_VERSION
    assert result["evaluator"]["metric_definitions_version"] == "3.0.5"
    assert result["evaluator"]["page_syntax_policy"] == "top-level-page-markers-outside-code-html-v1"
    assert result["evaluator"]["paragraph_policy"] == "page-token-spans-exact-gaps-v1"


def test_structure_metric_definition_ids_are_unique():
    text = "# Título\n\nUm.\n\nDois.\n\n3. Item\n4. Outro"
    ref, hyp = parse_page(text, 1, "reference"), parse_page(text, 1)
    result = compute_structure_metrics(align_blocks(ref, hyp), ref, hyp).to_dict()
    ids = [m["definition_id"] for m in result.values() if "definition_id" in m]
    assert len(set(ids)) == len(ids)


def test_compute_and_regrade_cli_agree_with_boundary_and_ordinal_metrics(tmp_path):
    ref = "Long paragraph one\n\nShort\n\n1. Primeiro\n2. Segundo"
    hyp = "Long\n\nparagraph one Short\n\n5. Primeiro\n6. Segundo"
    rp, hp = tmp_path / "ref.md", tmp_path / "hyp.md"
    rp.write_text(ref)
    hp.write_text(hyp)
    for script, output in [("compute_metrics.py", "compute"), ("regrade_existing.py", "regrade")]:
        command = [sys.executable, str(ROOT / "scripts" / script), "--reference", str(rp),
                   "--hypothesis", str(hp), "--engine", "stage5", "--run-id", "r1",
                   "--output-dir", str(tmp_path / output), "--quiet"]
        run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
        assert run.returncode == 0, run.stdout + run.stderr
    a = json.loads((tmp_path / "compute/metrics_v3_stage5_r1.json").read_text())
    b = json.loads((tmp_path / "regrade/metrics_stage5_r1.json").read_text())
    assert a["quality"] == b["quality"]
    assert a["quality"]["structure"]["paragraph_boundary_f1"]["value"] < 1
    assert a["quality"]["structure"]["ordered_list_ordinal_accuracy"]["value"] == 0


@pytest.mark.parametrize("corpus,filename,page_count", [
    ("V3", "Document_AI_V3.md", 144),
    ("V5", "Document_AI_V5_REFERENCIA.md", 42),
])
def test_real_reference_page_counts_and_structural_identity(corpus, filename, page_count):
    path = ROOT / "corpus" / corpus / filename
    if not path.is_file():
        pytest.skip("Optional local ground truth is absent")
    text = path.read_text(encoding="utf-8")
    pages = split_pages(text)
    assert list(pages) == list(range(1, page_count+1))
    assert {n.page for n in parse_document(text)} <= set(pages)
    for page, body in pages.items():
        ref, hyp = parse_page(body, page, "reference"), parse_page(body, page)
        result = compute_structure_metrics(align_blocks(ref, hyp), ref, hyp).to_dict()
        for name in ["paragraph_detection_f1", "paragraph_boundary_f1", "ordered_list_ordinal_accuracy"]:
            metric = result[name]
            assert metric["value"] in (1, None), (corpus, page, name, metric)
            if metric["value"] is None:
                assert metric["status"] == "not_applicable"
        for item in ref:
            if item.kind == NodeKind.LIST_ITEM and item.ordered_list:
                assert type(item.metadata["ordered_ordinal"]) is int
