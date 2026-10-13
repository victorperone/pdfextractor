"""Evaluation sub-package for PDFExtractor V2 metrics.

Modules
-------
models           — MarkdownNode, PageScope, AlignedPair, MetricResult
normalization    — Normalisation profiles (raw, unicode_layout, visible_text)
markdown_parser  — CommonMark+GFM parser producing MarkdownNode trees
page_scope       — Page-set selection, missing/extra page classification
block_alignment  — 1:1 block aligner with provenance (split/merge)
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

__all__ = [
    "MarkdownNode", "NodeKind", "NormProfile", "MetricStatus",
    "MetricResult", "AlignedPair", "AlignmentStatus", "PageScope",
    "normalize", "parse_document", "PARSER_VERSION", "build_page_scope",
    "align_blocks", "AlignmentSummary", "compute_pair_score",
    "MIN_SCORE", "AMBIGUITY_MARGIN",
]
