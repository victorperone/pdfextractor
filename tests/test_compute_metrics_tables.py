from __future__ import annotations

import pytest

from scripts.compute_metrics import (
    aggregate_table_metrics_from_pages,
    compute_table_metrics,
)


TABLE = "| Campo | Valor |\n| --- | --- |\n| Total | R$ 12,50 |"


def test_no_table_page_does_not_reduce_perfect_document_table_f1():
    empty_page = {"page": 1, **compute_table_metrics("", "")}
    table_page = {"page": 2, **compute_table_metrics(TABLE, TABLE)}

    metrics = aggregate_table_metrics_from_pages(
        [empty_page, table_page], set(), {1: "", 2: TABLE}
    )

    assert metrics["table_precision"] == 1.0
    assert metrics["table_recall"] == 1.0
    assert metrics["table_f1"] == 1.0
    assert metrics["cell_exact_match"] == 1.0
    assert metrics["cell_cer"] == 0.0


def test_missing_table_is_penalized_in_detection_and_cell_metrics():
    metrics = compute_table_metrics("", TABLE)

    assert metrics["table_precision"] == 0.0
    assert metrics["table_recall"] == 0.0
    assert metrics["table_f1"] == 0.0
    assert metrics["cell_exact_match"] == 0.0
    assert metrics["cell_alignment_accuracy"] == 0.0
    assert metrics["cell_cer"] == 1.0
    assert metrics["table_content_f1"] == 0.0
    assert metrics["table_structure_similarity"] == 0.0


def test_missing_table_page_contributes_false_negative_to_document_aggregate():
    perfect_page = {"page": 1, **compute_table_metrics(TABLE, TABLE)}
    missing_page = {
        "page": 2,
        "missing_from_hypothesis": True,
        "selected_but_missing": True,
    }

    metrics = aggregate_table_metrics_from_pages(
        [perfect_page, missing_page], {2}, {1: TABLE, 2: TABLE}
    )

    assert metrics["table_tp"] == 1
    assert metrics["table_fn"] == 1
    assert metrics["table_recall"] == 0.5
    assert metrics["table_f1"] == pytest.approx(2 / 3, abs=0.0001)
    assert metrics["cell_cer"] == 0.5
    assert metrics["cell_exact_match"] == 0.5
    assert metrics["table_content_f1"] < 1.0


def test_extra_table_reduces_table_precision_and_content_f1():
    metrics = compute_table_metrics(TABLE, "")

    assert metrics["table_precision"] == 0.0
    assert metrics["table_f1"] == 0.0
    assert metrics["table_content_f1"] == 0.0
