"""Independent table/HTML preservation regressions. No PDF extraction/OCR."""
from __future__ import annotations

import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

from structured_pdf_text.evaluation import CanonicalDocument, EvaluationRequest, evaluate
from structured_pdf_text.evaluation.markdown_parser import parse_document, parse_page
from structured_pdf_text.evaluation.models import MarkdownNode, NodeKind
from structured_pdf_text.evaluation.reference_inputs import document_pages
from structured_pdf_text.evaluation.table_metrics import compute_table_metrics

ROOT = Path(__file__).resolve().parents[1]
TABLE = "| Nome | Valor |\n|---|---|\n| Ana | 100 |"
HTML = "<table><tr><th>Nome</th><th>Valor</th></tr><tr><td>Ana</td><td>100</td></tr></table>"


def tree(rows, origin):
    """Construct actual anchor cells, including ragged rows GFM would pad."""
    nodes = []

    def add(kind, text, parent=None):
        seq = len(nodes)
        node = MarkdownNode(MarkdownNode.make_id(origin, 1, kind, seq), origin, 1,
                            kind, text, text, 0, len(text), seq, parent_id=parent)
        nodes.append(node)
        return node

    if rows is not None:
        table = add(NodeKind.TABLE, " ".join(" ".join(row) for row in rows))
        for values in rows:
            row = add(NodeKind.TABLE_ROW, " ".join(values), table.node_id)
            for value in values:
                add(NodeKind.TABLE_CELL, value, row.node_id)
    return nodes


def metrics(ref, hyp, **kwargs):
    return compute_table_metrics(tree(ref, "reference"), tree(hyp, "hypothesis"), **kwargs).to_dict()


def html_metrics(ref, hyp):
    return compute_table_metrics(parse_page(ref, 1, "reference"),
                                 parse_page(hyp, 1, "hypothesis")).to_dict()


def test_whole_table_missing_penalises_every_preservation_metric():
    m = metrics([["Nome", "Valor"], ["Ana", "100"]], None)
    for name in ("table_detection_f1", "table_row_detection_f1", "table_column_count_accuracy",
                 "cell_detection_f1", "cell_content_exact_match", "cell_alignment_accuracy",
                 "table_content_f1", "cell_span_accuracy"):
        assert m[name]["value"] == 0, name
    assert m["cell_cer"]["value"] == 1
    assert m["cell_cer"]["counts"]["edit_distance"] == 15
    assert m["cell_cer"]["counts"]["ref_chars"] == 15
    assert m["cell_content_exact_match"]["coverage"] == 0


@pytest.mark.parametrize("ref,hyp,exact,total,edits,chars,fn,fp", [
    ([["Nome", "Valor"], ["Ana", "100"], ["Bruno", "999"]],
     [["Nome", "Valor"], ["Ana", "100"]], 4, 6, 8, 23, 2, 0),
    ([["Nome", "Valor"], ["Ana", "100"]],
     [["Nome", "Valor"], ["Ana", "100"], ["Bruno", "999"]], 4, 6, 8, 15, 0, 2),
    ([["Nome", "Valor"], ["Ana", "100"]], [["Nome", "Valor"], ["Ana"]],
     3, 4, 3, 15, 1, 0),
    ([["Nome", "Valor"], ["Ana"]], [["Nome", "Valor"], ["Ana", "100"]],
     3, 4, 3, 12, 0, 1),
    ([["Nome", "Valor"], ["Ana", ""]], [["Nome", "Valor"], ["Ana", "100"]],
     3, 4, 3, 12, 0, 0),
    ([["Nome", "Valor"], ["Ana", "100"]], [["Nome", "Valor"], ["Ana", ""]],
     3, 4, 3, 15, 0, 0),
])
def test_lost_or_inserted_cells_have_exact_global_counts(ref, hyp, exact, total, edits, chars, fn, fp):
    m = metrics(ref, hyp)
    assert m["cell_content_exact_match"]["counts"]["exact"] == exact
    assert m["cell_content_exact_match"]["counts"]["total"] == total
    assert m["cell_content_exact_match"]["value"] == round(exact / total, 4)
    assert m["cell_detection_f1"]["counts"]["fn"] == fn
    assert m["cell_detection_f1"]["counts"]["fp"] == fp
    assert m["cell_cer"]["counts"]["edit_distance"] == edits
    assert m["cell_cer"]["counts"]["ref_chars"] == chars
    assert m["cell_cer"]["value"] == round(edits / chars, 4)


def test_conditional_exact_match_reports_coverage_without_hiding_loss():
    m = metrics([["Nome", "Valor"], ["Ana", "100"]], [["Nome", "Valor"]])
    assert m["cell_content_exact_match"]["value"] == 0.5
    assert m["cell_content_exact_match_matched"]["value"] == 1
    assert m["cell_content_exact_match_matched"]["coverage"] == 0.5


@pytest.mark.parametrize("ref,hyp,status,value", [
    (None, None, "not_applicable", None),
    ([["", ""]], [["", ""]], "not_applicable", None),
    ([["", ""]], None, "not_applicable", None),
    (None, [["abc"]], "undefined_denominator", None),
    ([[""]], [["abc"]], "undefined_denominator", None),
    ([["abc"]], None, "ok", 1),
])
def test_cer_zero_denominator_is_explicit(ref, hyp, status, value):
    result = metrics(ref, hyp)["cell_cer"]
    assert result["status"] == status
    assert result["value"] == value


def test_missing_empty_cells_are_not_exact_matches():
    m = metrics([["", ""]], None)
    assert m["cell_content_exact_match"]["value"] == 0
    assert m["cell_alignment_accuracy"]["value"] == 0
    assert m["cell_detection_f1"]["counts"]["fn"] == 2


def test_insertions_can_make_cer_exceed_one():
    ref = tree([["a"]], "reference")
    hyp = tree([["a"]], "hypothesis") + parse_page(
        "<table><tr><td>zzzzzzzzzz</td></tr></table>", 2, "hypothesis", start_seq=10)
    m = compute_table_metrics(ref, hyp).to_dict()
    assert m["cell_cer"]["value"] == 10
    assert m["cell_cer"]["counts"]["extra_chars"] == 10


def test_unmatched_tables_contribute_to_row_detection():
    ref = parse_page(TABLE, 1, "reference") + parse_page(TABLE, 2, "reference", start_seq=10)
    hyp = parse_page(TABLE, 1, "hypothesis")
    m = compute_table_metrics(ref, hyp).to_dict()
    assert m["table_row_detection_f1"]["counts"] == {"tp": 2, "fp": 0, "fn": 2}
    assert m["table_column_count_accuracy"]["value"] == 0.5
    assert m["cell_content_exact_match"]["value"] == 0.5
    assert m["cell_cer"]["value"] == 0.5


def test_random_whole_table_loss_counts_all_reference_characters():
    randomizer = random.Random(183)
    for _ in range(80):
        rows = [["".join(randomizer.choices("abc123", k=randomizer.randrange(8)))
                 for _ in range(randomizer.randrange(1, 6))] for _ in range(randomizer.randrange(1, 8))]
        m = metrics(rows, None)
        chars = sum(len(c) for row in rows for c in row)
        assert m["cell_cer"]["counts"]["edit_distance"] == chars
        assert m["cell_cer"]["counts"]["ref_chars"] == chars
        assert m["cell_content_exact_match"]["counts"]["total"] == sum(map(len, rows))
        assert m["cell_content_exact_match"]["value"] == 0


def test_cell_cer_respects_shared_budget_and_never_returns_subtotal():
    m = metrics([["header", "value"], ["aaaaa", "bbbbb"]],
                [["header", "value"], ["aaaax", "bbbbx"]], max_edit_cells=20)
    assert m["cell_cer"]["status"] == "resource_limit"
    assert m["cell_cer"]["value"] is None
    assert "edit_distance" not in m["cell_cer"]["counts"]
    assert 0 < m["cell_cer"]["counts"]["evaluated_cells"] <= 20
    assert m["cell_content_exact_match"]["value"] == 0.5


def test_table_budget_is_recorded_and_resource_failure_blocks_ranking(monkeypatch):
    import structured_pdf_text.evaluation.evaluator as api
    original = api.compute_text_metrics

    def text_with_sufficient_budget(*args, **kwargs):
        kwargs["max_edit_cells"] = 1_000_000
        return original(*args, **kwargs)

    monkeypatch.setattr(api, "compute_text_metrics", text_with_sufficient_budget)
    ref = TABLE + "\n| aaaaa | bbbbb |"
    hyp = TABLE + "\n| aaaax | bbbbx |"
    result = evaluate(EvaluationRequest(ref, hyp, max_text_edit_cells=20))
    assert all(m["status"] != "resource_limit" for m in result["quality"]["text"].values())
    assert result["quality"]["tables"]["cell_cer"]["status"] == "resource_limit"
    assert result["evaluation"]["rank_eligible"] is False
    assert result["evaluation"]["status"] == "partial"
    assert result["evaluator"]["metric_definitions_version"] == "3.1.0"
    assert result["evaluator"]["resource_policy"]["max_table_edit_cells"] == 20


@pytest.mark.parametrize("markup", [HTML, HTML.replace("<th>", "<td>").replace("</th>", "</td>"),
                                        HTML.replace("</td>", "").replace("</th>", "").replace("</tr>", "")])
def test_html_and_gfm_share_cells_and_text(markup):
    nodes = parse_page(markup, 7, "reference")
    doc = CanonicalDocument.from_nodes(nodes)
    assert doc.visible_text == "Nome Valor Ana 100"
    assert len(doc.leaf_nodes) == 4
    assert sum(n.kind == NodeKind.TABLE for n in nodes) == 1
    assert all(n.page == 7 for n in nodes)
    m = html_metrics(markup, TABLE)
    assert m["cell_content_exact_match"]["value"] == 1
    assert m["cell_cer"]["value"] == 0
    assert m["cell_span_accuracy"]["value"] == 1


def test_html_loss_is_visible_to_text_cells_and_critical_data():
    ref = "<table><tr><td>R$ 100,00</td><td>10%</td></tr></table>"
    result = evaluate(EvaluationRequest(ref, ""))
    assert result["quality"]["text"]["cer_sequential_normalized"]["value"] == 1
    assert result["quality"]["tables"]["cell_cer"]["value"] == 1
    assert result["quality"]["critical_data"]["currency"]["value"] == 0
    assert result["quality"]["critical_data"]["percent"]["value"] == 0


def test_merged_cell_anchor_coordinates_and_no_text_duplication():
    text = ('<table><tr><th rowspan="2">Área</th><th colspan="2">Receita</th></tr>'
            '<tr><td>A</td><td>B</td></tr><tr><td>Sul</td><td>10</td><td>20</td></tr></table>')
    nodes = parse_page(text, 1)
    cells = [n for n in nodes if n.kind == NodeKind.TABLE_CELL]
    assert [(c.metadata["row_index"], c.metadata["column_index"],
             c.metadata["rowspan"], c.metadata["colspan"]) for c in cells] == [
        (0, 0, 2, 1), (0, 1, 1, 2), (1, 1, 1, 1), (1, 2, 1, 1),
        (2, 0, 1, 1), (2, 1, 1, 1), (2, 2, 1, 1)]
    assert nodes[0].metadata["column_count"] == 3
    assert CanonicalDocument.from_nodes(nodes).visible_text == "Área Receita A B Sul 10 20"
    m = html_metrics(text, text)
    assert m["cell_detection_f1"]["counts"]["tp"] == 7
    assert m["cell_span_accuracy"]["value"] == 1
    assert m["cell_cer"]["counts"]["ref_chars"] == len("ÁreaReceitaABSul1020".lower())


def test_changed_span_is_measured_independently_from_same_cell_text():
    ref = '<table><tr><td colspan="2">Alpha</td></tr></table>'
    hyp = ref.replace('colspan="2"', 'colspan="1"')
    m = html_metrics(ref, hyp)
    assert m["cell_content_exact_match"]["value"] == 1
    assert m["cell_cer"]["value"] == 0
    assert m["cell_span_accuracy"]["value"] == 0
    assert m["table_column_count_accuracy"]["value"] == 0


def test_rowspan_reserves_columns_in_later_rows():
    ref = '<table><tr><td rowspan="2">Alpha</td><td>Beta</td></tr><tr><td>Gamma</td></tr></table>'
    hyp = ref.replace('rowspan="2"', 'rowspan="1"')
    m = html_metrics(ref, hyp)
    assert m["cell_span_accuracy"]["value"] < 1
    assert m["cell_content_exact_match"]["value"] < 1
    assert m["cell_alignment_accuracy"]["value"] < 1


def test_zero_rowspan_stops_at_its_row_group():
    text = ('<table><thead><tr><th rowspan="0">A</th><th>B</th></tr><tr><th>C</th></tr></thead>'
            '<tbody><tr><td>D</td><td>E</td></tr></tbody></table>')
    cells = [n for n in parse_page(text, 1) if n.kind == NodeKind.TABLE_CELL]
    assert cells[0].metadata["rowspan"] == 2
    assert cells[2].metadata["column_index"] == 1
    assert cells[3].metadata["column_index"] == 0


@pytest.mark.parametrize("markup,message", [
    ('<table><tr><td colspan="bad">a</td></tr></table>', "Invalid HTML table colspan"),
    ('<table><tr><td rowspan="-1">a</td></tr></table>', "Invalid HTML table rowspan"),
    ('<table><tr><td colspan="0">a</td></tr></table>', "Invalid HTML table colspan"),
    ('<table><tr><td><table><tr><td>a</td></tr></table></td></tr></table>', "Nested HTML tables"),
    ('<table>lost<tr><td>a</td></tr></table>', "outside a cell"),
    ('<table><tr><td>a</td></tr>', "Unclosed HTML table block"),
    ('<table>\n\n<tr><td>a</td></tr></table>', "Unclosed HTML table block"),
    ('<table><tr><td>A</td><td rowspan="2">B</td></tr><tr><td colspan="2">C</td></tr></table>',
     "Overlapping HTML table spans"),
])
def test_unsupported_or_ambiguous_html_is_explicitly_rejected(markup, message):
    with pytest.raises(ValueError, match=message):
        evaluate(EvaluationRequest(markup, markup))


def test_large_span_does_not_expand_or_duplicate_cells():
    markup = '<table><tr><td colspan="1000000000">Alpha</td></tr></table>'
    nodes = parse_page(markup, 1)
    assert len(nodes) == 3
    assert nodes[0].metadata["column_count"] == 1_000_000_000
    assert CanonicalDocument.from_nodes(nodes).visible_text == "Alpha"


def test_entities_inline_tags_breaks_caption_and_surrounding_text():
    text = ('<div>Antes<table><caption>Resumo</caption><tr><td>A<b>B</b>C<br>D &amp; E'
            '<!-- secret --><script>hidden</script><span hidden>hidden</span></td>'
            '<td><p>Um</p><p>Dois</p></td></tr></table>Depois</div>')
    nodes = parse_page(text, 1)
    assert CanonicalDocument.from_nodes(nodes).visible_text == "Antes Resumo ABC D & E Um Dois Depois"
    assert [n.visible_text for n in nodes if n.kind == NodeKind.TABLE_CELL] == ["ABC D & E", "Um Dois"]


def test_gfm_inline_html_break_preserves_word_boundary():
    text = '| A |\n|---|\n| Nota<br>Fiscal |'
    cells = [n.visible_text for n in parse_page(text, 1) if n.kind == NodeKind.TABLE_CELL]
    assert cells == ["A", "Nota Fiscal"]


def test_html_raw_source_ids_offsets_and_repeatability():
    text = "Introdução\n\n" + HTML
    nodes = parse_page(text, 8)
    again = parse_page(text, 8)
    assert nodes == again
    assert len({n.node_id for n in nodes}) == len(nodes)
    for node in nodes:
        if node.metadata.get("format") == "html":
            assert text[node.source_start:node.source_end] == node.raw_markdown
    assert CanonicalDocument.from_nodes(nodes).raw_markdown == text
    result = evaluate(EvaluationRequest(HTML, TABLE))
    assert result["quality"]["text"]["cer_markdown_raw"]["value"] > 0
    assert result["quality"]["text"]["cer_sequential_normalized"]["value"] == 0


def test_html_source_offsets_preserve_crlf_and_unicode():
    text = "Introdução\r\n\r\n<table>\r\n<tr><td>Ação</td></tr>\r\n</table>"
    nodes = parse_page(text, 8)
    for node in nodes:
        if node.metadata.get("format") == "html":
            assert text[node.source_start:node.source_end] == node.raw_markdown
    assert CanonicalDocument.from_nodes(nodes).visible_text == "Introdução Ação"


@pytest.mark.parametrize("script,prefix", [("compute_metrics.py", "metrics_v3"),
                                            ("regrade_existing.py", "metrics")])
def test_both_clis_use_canonical_html_metrics(tmp_path, script, prefix):
    ref, hyp = tmp_path / "ref.md", tmp_path / "hyp.md"
    ref.write_text(HTML, encoding="utf-8")
    hyp.write_text(TABLE, encoding="utf-8")
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / script),
        "--reference", str(ref), "--hypothesis", str(hyp), "--engine", "audit",
        "--run-id", "html", "--output-dir", str(tmp_path / "out"), "--quiet"],
        capture_output=True, text=True, cwd=ROOT, timeout=30)
    assert result.returncode == 0, result.stderr
    output = json.loads((tmp_path / "out" / f"{prefix}_audit_html.json").read_text())
    assert output["quality"] == evaluate(EvaluationRequest(HTML, TABLE))["quality"]


@pytest.mark.parametrize("version,filename,min_tables", [
    ("V3", "Document_AI_V3.md", 10), ("V5", "Document_AI_V5_REFERENCIA.md", 2),
])
def test_available_corpus_html_ground_truth_is_preserved(version, filename, min_tables):
    path = ROOT / "corpus" / version / filename
    if not path.is_file():
        pytest.skip(f"Optional corpus unavailable: {path.name}")
    pages = document_pages(path.read_text(encoding="utf-8"))
    tables = 0
    for number, body in pages.items():
        if "<table>" not in body:
            continue
        nodes = parse_page(body, number, "reference")
        html_tables = [n for n in nodes if n.kind == NodeKind.TABLE and n.metadata.get("format") == "html"]
        assert len(html_tables) == body.count("<table>")
        tables += len(html_tables)
        doc = CanonicalDocument.from_nodes(nodes)
        assert all(n.visible_text in doc.visible_text for n in nodes if n.kind == NodeKind.TABLE_CELL)
        result = evaluate(EvaluationRequest(body, body))
        assert result["quality"]["tables"]["cell_content_exact_match"]["value"] == 1
        assert result["quality"]["tables"]["cell_span_accuracy"]["value"] == 1
        assert result["quality"]["tables"]["cell_cer"]["value"] == 0
        deleted = evaluate(EvaluationRequest(body, ""))
        assert deleted["quality"]["tables"]["cell_content_exact_match"]["value"] == 0
        assert deleted["quality"]["tables"]["cell_alignment_accuracy"]["value"] == 0
        assert deleted["quality"]["tables"]["cell_cer"]["value"] == 1
        assert deleted["quality"]["text"]["cer_sequential_normalized"]["value"] == 1
    assert tables >= min_tables
