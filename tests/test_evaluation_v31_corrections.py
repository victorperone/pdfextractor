"""Metric definition 3.1.0 regressions: minimal inputs with exact expectations.

Each case isolates one formula defect found in 3.0.5 (C1–C10, D1). No PDF
extraction or OCR is involved; inputs are canonical Markdown documents.
"""
from __future__ import annotations

import pytest

from structured_pdf_text.evaluation import EvaluationRequest, evaluate
from structured_pdf_text.evaluation.markdown_parser import parse_page


def run(ref: str, hyp: str) -> dict:
    return evaluate(EvaluationRequest(ref_text=ref, hyp_text=hyp))


def metric(result: dict, path: str) -> dict:
    group, key = path.split(".")
    return (result["integrity"] if group == "integrity" else result["quality"][group])[key]


def page(body: str, number: int = 1) -> str:
    return f"## Página {number}\n\n{body}\n"


ROWS = [f"| r{i} c | v{i} |" for i in range(10)]
TABLE_HEAD = "| h | k |\n|---|---|\n"
NEAR_IDENTICAL = [f"Parágrafo número {i} com texto distinto aqui." for i in range(6)]


def test_c1_reordered_rows_keep_the_table_detected():
    result = run(page(TABLE_HEAD + "\n".join(ROWS)), page(TABLE_HEAD + "\n".join(reversed(ROWS))))
    assert metric(result, "tables.table_detection_f1")["value"] == 1.0
    assert metric(result, "tables.cell_detection_f1")["value"] == 1.0
    order = metric(result, "tables.table_row_order_accuracy")
    assert order["status"] == "ok" and order["value"] < 0.5


def test_c2_reading_order_universe_excludes_containers():
    result = run(page(TABLE_HEAD + "\n".join(ROWS)), page(TABLE_HEAD + "\n".join(reversed(ROWS))))
    order = metric(result, "reading_order.pairwise_reading_order_accuracy")
    # Leaf cells only: the header row stays first, data rows are fully inverted.
    assert order["status"] == "ok" and order["value"] < 0.3
    assert order["counts"]["matched_elements"] == 22


def test_c3_aligner_prefers_exact_content_over_position():
    result = run(page("\n\n".join(NEAR_IDENTICAL)), page("\n\n".join(reversed(NEAR_IDENTICAL))))
    assert metric(result, "reading_order.pairwise_reading_order_accuracy")["value"] == 0.0
    assert metric(result, "text.aligned_block_text_cer")["value"] == 0.0


def test_c4_empty_cells_are_not_content_matches():
    ref = page("| a | b | c |\n|---|---|---|\n|  |  | x |\n|  |  | y |")
    hyp = page("| a | b | c |\n|---|---|---|\n|  |  | q |\n|  |  | w |")
    content = metric(run(ref, hyp), "tables.table_content_f1")
    assert (content["counts"]["tp"], content["counts"]["fp"], content["counts"]["fn"]) == (3, 2, 2)
    assert content["value"] == 0.6


def test_c5_cell_comparison_is_case_sensitive_like_text():
    result = run(page("| Nome | Valor |\n|---|---|\n| ABC | DEF |"),
                 page("| nome | valor |\n|---|---|\n| abc | def |"))
    assert metric(result, "tables.cell_cer")["value"] > 0
    assert metric(result, "tables.cell_content_exact_match")["value"] == 0.0


def test_c6_column_order_uses_the_header_and_all_rows():
    result = run(page("| A | B | C |\n|---|---|---|\n| x1 | y1 | z1 |"),
                 page("| C | B | A |\n|---|---|---|\n| x1 | y1 | z1 |"))
    order = metric(result, "tables.table_column_order_accuracy")
    assert order["status"] == "ok" and order["value"] < 1.0


def test_c7_paragraphs_inside_lists_and_quotes_are_not_paragraph_blocks():
    body = "- a um\n- b dois\n\n> citação\n\nTexto solto."
    detection = metric(run(page(body), page(body)), "structure.paragraph_detection_f1")
    assert detection["counts"] == {"tp": 1, "fp": 0, "fn": 0}


def test_c8_list_item_text_excludes_nested_lists():
    nodes = parse_page("- pai\n  - filho um\n  - filho dois\n", 1, "reference")
    items = [n.visible_text for n in nodes if n.kind.value == "list_item"]
    assert items == ["pai", "filho um", "filho dois"]


def test_c9_edge_heuristic_requires_actual_repetition():
    result = run(page("Linha A do documento.\n\nLinha B."), page("Linha A do documento!\n\nLinha B!"))
    edge = result["integrity"]["edge_content_heuristic_v1"]
    assert (edge["suspected_header_lines"], edge["suspected_footer_lines"]) == (0, 0)


def test_c10_entities_use_the_projected_text_once():
    body = "Código `[3](4)` e R$ 123.456.789-09 em 31/02/2020."
    result = run(page(body), page(body))
    numeric = metric(result, "critical_data.numeric")["counts"]
    # 3 and 4 survive literally; the invalid date contributes 31, 02 and 2020.
    assert numeric["ref_count"] == 5
    assert metric(result, "critical_data.currency")["counts"]["ref_count"] == 0
    assert metric(result, "critical_data.cpf")["counts"]["ref_count"] == 1


def test_i03_copies_text_coverage_recall():
    result = run(page("Alfa Beta gama"), page("alfa beta gama"))
    assert metric(result, "integrity.page_content_coverage_recall")["value"] == \
        metric(result, "text.text_coverage_recall")["value"]


def test_d1_document_counts_are_page_anchored_sums():
    ref = page("Primeira página com texto.", 1) + page("Segunda página com mais texto.", 2)
    hyp = page("Primeira pagina com texto", 1) + page("Segunda página com mais texto!", 2)
    result = run(ref, hyp)
    for key in ("cer_markdown_raw", "cer_sequential_normalized", "wer_sequential", "aligned_block_text_cer"):
        document = result["quality"]["text"][key]["counts"]
        pages = [entry["quality"]["text"][key]["counts"] for entry in result["per_page"]]
        assert all(document[op] == sum(p[op] for p in pages) for op in ("S", "D", "I"))


def test_d1_large_document_has_no_resource_limit():
    paragraphs = [f"Bloco {i}: valor R$ {i},00 em 0{1 + i % 9}/01/2020 com texto adicional." for i in range(40)]
    ref = "".join(page("\n\n".join(paragraphs), number) for number in range(1, 151))
    hyp = ref.replace("texto", "testo")
    result = run(ref, hyp)
    assert result["evaluation"]["status"] == "ok"
    assert result["evaluation"]["rank_eligible"]
    assert metric(result, "text.cer_sequential_normalized")["status"] == "ok"
    assert metric(result, "reading_order.pairwise_reading_order_accuracy")["status"] == "ok"
