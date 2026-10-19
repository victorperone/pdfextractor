"""Explicit canonical comparison fixture, independent of production extraction."""


def comparison_result(pages=(1, 2, 3), effective_hash="a" * 64):
    return {
        "schema_version": "evaluation-3.0.0",
        "inputs": {
            "reference_effective_sha256": effective_hash,
            "reference_serialization_version": "reference-effective-v1",
            "pdf_sha256": "b" * 64,
            "manifest_sha256": "c" * 64,
            "mode": "balanced",
            "selected_pages": list(pages),
        },
        "evaluator": {
            "metric_definitions_version": "3.0.4",
            "normalization_policy": "unicode_layout",
            "parser": "test-parser-v1",
            "page_syntax_policy": "top-level-page-markers-outside-code-html-v1",
            "paragraph_policy": "page-token-spans-exact-gaps-v1",
            "alignment_policy": "content-match-v1",
            "entity_policy": "entities-ptbr-v1",
            "text_projection_policy": "parser-visible-leaves-v2",
            "edit_distance_policy": "levenshtein-exact-banded-sdi-v1",
            "html_table_policy": "html-table-anchors-v1",
            "table_metric_policy": "all-cell-anchors-v2",
            "aggregation_policy": "finite-numeric-outcomes-v1",
            "comparability_policy": "canonical-inputs-and-policies-v1",
            "resource_policy": {
                "max_text_edit_cells": 8_000_000,
                "max_table_edit_cells": 8_000_000,
                "max_alignment_pairs": 250_000,
                "max_similarity_text_product": 50_000_000,
            },
        },
        "operational": {"benchmark_protocol_id": "protocol-v1"},
    }
