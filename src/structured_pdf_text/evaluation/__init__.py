"""Evaluation sub-package for PDFExtractor V2 metrics.

Modules
-------
models           — MarkdownNode, PageScope, AlignedPair, MetricResult
normalization    — Normalisation profiles (raw, unicode_layout, visible_text)
markdown_parser  — CommonMark+GFM parser producing MarkdownNode trees
page_scope       — Page-set selection, missing/extra page classification
block_alignment  — 1:1 block aligner with provenance (split/merge)
structure_metrics — Heading/paragraph/list/reading-order metrics
table_metrics    — Table detection, rows, columns, cells
critical_data    — Numbers, currency, dates, CPF/CNPJ, identifiers
"""
from structured_pdf_text.evaluation.models import (
    MarkdownNode,
    NodeKind,
    NormProfile,
    MetricStatus,
    MetricResult,
    AlignedPair,
    AlignmentStatus,
    PageScope,
)
from structured_pdf_text.evaluation.normalization import normalize
from structured_pdf_text.evaluation.markdown_parser import (
    parse_document,
    PARSER_VERSION,
)
from structured_pdf_text.evaluation.page_scope import build_page_scope
from structured_pdf_text.evaluation.block_alignment import (
    align_blocks,
    AlignmentSummary,
    compute_pair_score,
    MIN_SCORE,
    AMBIGUITY_MARGIN,
)
from structured_pdf_text.evaluation.structure_metrics import (
    compute_structure_metrics,
    StructureMetrics,
    heading_detection_f1,
    paragraph_detection_f1,
    list_metrics,
    pairwise_reading_order_accuracy,
    block_type_confusion,
)
from structured_pdf_text.evaluation.table_metrics import (
    compute_table_metrics,
    TableMetrics,
    table_detection_f1,
    table_row_and_column_metrics,
    cell_metrics,
    table_content_f1,
)
from structured_pdf_text.evaluation.critical_data import (
    compute_critical_data_metrics,
    CriticalDataMetrics,
)
from structured_pdf_text.evaluation.integrity_metrics import (
    compute_integrity_metrics,
    IntegrityMetrics,
    duplicate_excess_rate,
    missing_page_rate,
    page_content_coverage_recall,
    markdown_validity_report,
    edge_content_diagnostic,
    MarkdownValidityReport,
    EdgeContentDiagnostic,
)
from structured_pdf_text.evaluation.aggregation import (
    aggregate_metric,
    AggregatedMetric,
    V2DocumentMetrics,
    check_comparability,
    evaluate_rank_eligibility,
)

__all__ = [
    "MarkdownNode", "NodeKind", "NormProfile", "MetricStatus",
    "MetricResult", "AlignedPair", "AlignmentStatus", "PageScope",
    "normalize", "parse_document", "PARSER_VERSION", "build_page_scope",
    "align_blocks", "AlignmentSummary", "compute_pair_score",
    "MIN_SCORE", "AMBIGUITY_MARGIN",
    "compute_structure_metrics", "StructureMetrics",
    "heading_detection_f1", "paragraph_detection_f1",
    "list_metrics", "pairwise_reading_order_accuracy", "block_type_confusion",
    "compute_table_metrics", "TableMetrics",
    "table_detection_f1", "table_row_and_column_metrics",
    "cell_metrics", "table_content_f1",
    "compute_critical_data_metrics", "CriticalDataMetrics",
    "compute_integrity_metrics", "IntegrityMetrics",
    "duplicate_excess_rate", "missing_page_rate",
    "page_content_coverage_recall", "markdown_validity_report",
    "edge_content_diagnostic", "MarkdownValidityReport", "EdgeContentDiagnostic",
    "aggregate_metric", "AggregatedMetric", "V2DocumentMetrics",
    "check_comparability", "evaluate_rank_eligibility",
]
