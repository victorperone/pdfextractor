from scripts.compute_metrics import (
    _selected_document_bodies,
    compute_structure_metrics,
    compute_table_metrics,
)


def test_missing_selected_page_penalizes_markdown_structure() -> None:
    hyp_pages = {1: "## Página 1\nTexto normal."}
    ref_pages = {
        1: "## Página 1\nTexto normal.",
        2: "## Página 2\n# Título importante\n\n- item A\n- item B\n\nParágrafo ausente com conteúdo suficiente.",
    }
    hyp, ref = _selected_document_bodies(hyp_pages, ref_pages, {1, 2})
    metrics = compute_structure_metrics(hyp, ref)
    assert metrics["heading_f1"] < 1
    assert metrics["list_detection_f1"] < 1
    assert metrics["block_f1"] < 1


def test_perfect_selected_pages_keep_perfect_structure() -> None:
    pages = {1: "## Página 1\n# Título\n\nTexto."}
    hyp, ref = _selected_document_bodies(pages, pages, {1})
    metrics = compute_structure_metrics(hyp, ref)
    assert metrics["heading_f1"] == 1
    assert metrics["block_f1"] == 1
    assert metrics["paragraph_boundary_f1"] == 1
    assert metrics["list_detection_f1"] == 1
    assert metrics["markdown_ast_similarity"] == 1
    assert metrics["heading_text_cer"] == 0


def test_extra_leading_table_does_not_shift_exact_table_matches() -> None:
    ref = "| A | B |\n|---|---|\n| 1 | 2 |\n\n| C | D |\n|---|---|\n| 3 | 4 |"
    hyp = "| X | Y |\n|---|---|\n| 8 | 9 |\n\n" + ref
    metrics = compute_table_metrics(hyp, ref)
    assert metrics["table_f1"] < 1
    assert metrics["cell_edit_sum"] == 4
