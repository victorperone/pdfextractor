"""Data models for the V2 evaluation pipeline.

All dataclasses use __slots__ for memory efficiency on large documents.
IDs are deterministic (page + block-index sequence), never random UUIDs.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class NodeKind(str, Enum):
    """Canonical block type for a parsed Markdown node."""
    HEADING      = "heading"
    PARAGRAPH    = "paragraph"
    LIST         = "list"
    LIST_ITEM    = "list_item"
    TABLE        = "table"
    TABLE_ROW    = "table_row"
    TABLE_CELL   = "table_cell"
    CODE         = "code"
    BLOCKQUOTE   = "blockquote"
    THEMATIC_BREAK = "thematic_break"
    UNKNOWN      = "unknown"


class NormProfile(str, Enum):
    """Normalisation profile applied before comparison."""
    RAW           = "raw"            # NFC + CRLF→LF only
    UNICODE_LAYOUT = "unicode_layout" # + collapse intra-line spaces
    VISIBLE_TEXT  = "visible_text"   # extract text content from Markdown syntax
    ENTITY_SURFACE = "entity_surface" # typed tokenisation, surface preserved
    ENTITY_CANONICAL = "entity_canonical"  # semantic normalisation by type


class MetricStatus(str, Enum):
    """Applicability / availability status for a single metric."""
    OK                       = "ok"
    NOT_APPLICABLE           = "not_applicable"          # both sides empty
    UNDEFINED_DENOMINATOR    = "undefined_denominator"   # ref=0, hyp>0
    UNAVAILABLE_ALIGNMENT    = "unavailable_alignment"   # aligner could not produce pairs
    INSUFFICIENT_EVIDENCE    = "insufficient_reference_evidence"
    RESOURCE_LIMIT           = "resource_limit"
    INVALID_INPUT            = "invalid_input"
    PARTIAL_EVALUATION       = "partial_evaluation"
    FALSE_POSITIVE_ONLY      = "false_positive_only"     # ref=0 hyp>0 (entities)
    FALSE_NEGATIVE_ONLY      = "false_negative_only"     # ref>0 hyp=0 (entities)


class AlignmentStatus(str, Enum):
    """Result of a single element-pair alignment decision."""
    MATCHED    = "matched"
    AMBIGUOUS  = "ambiguous"
    UNMATCHED  = "unmatched"   # this ref element has no hyp counterpart
    EXTRA      = "extra"       # this hyp element has no ref counterpart


# ---------------------------------------------------------------------------
# Core node model
# ---------------------------------------------------------------------------

@dataclass
class MarkdownNode:
    """A single structural element parsed from a Markdown document.

    Attributes
    ----------
    node_id       Deterministic ID: ``{origin}:p{page:04d}:{kind.value}:{seq:04d}``
    origin        ``"reference"`` or ``"hypothesis"``
    page          1-based page number; None if document has no page markers
    kind          Canonical block type (NodeKind)
    raw_markdown  Exact source Markdown text for this node
    visible_text  Rendered plain text (Markdown syntax removed)
    source_start  Byte offset of the first character in the source document
    source_end    Byte offset one past the last character
    order_index   0-based position in document reading order
    heading_level ATX heading level 1-6, or None
    list_depth    Nesting depth (0 = top-level), or None for non-list nodes
    ordered_list  True if inside an ordered list, False for unordered, None if N/A
    parent_id     node_id of the containing node (list → list_item, table → row…)
    children      node_ids of direct children
    metadata      Arbitrary extension data (table alignment, code language, …)
    source_hash   SHA-256 of visible_text for fast equality checks
    """
    node_id:      str
    origin:       str                   # "reference" or "hypothesis"
    page:         int | None
    kind:         NodeKind
    raw_markdown: str
    visible_text: str
    source_start: int
    source_end:   int
    order_index:  int
    heading_level: int | None = None
    list_depth:    int | None = None
    ordered_list:  bool | None = None
    parent_id:     str | None = None
    children:      list[str] = field(default_factory=list)
    metadata:      dict[str, Any] = field(default_factory=dict)
    source_hash:   str = field(init=False)

    def __post_init__(self) -> None:
        self.source_hash = hashlib.sha256(
            self.visible_text.encode("utf-8")
        ).hexdigest()[:16]

    @staticmethod
    def make_id(origin: str, page: int | None, kind: NodeKind, seq: int) -> str:
        page_str = f"p{page:04d}" if page is not None else "pNone"
        return f"{origin}:{page_str}:{kind.value}:{seq:04d}"


# ---------------------------------------------------------------------------
# Metric result
# ---------------------------------------------------------------------------

@dataclass
class MetricResult:
    """Single metric result following the V2 contract (spec §10.1)."""
    value:          float | None        # None when status != ok
    status:         MetricStatus
    direction:      str                 # "higher_is_better" | "lower_is_better" | "descriptive_only"
    unit:           str = "ratio"
    definition_id:  str = ""
    counts:         dict[str, Any] = field(default_factory=dict)
    scope:          str = "document"
    normalization_profile: str | None = None
    warnings:       list[str] = field(default_factory=list)
    coverage:       float | None = None  # fraction of elements that could be evaluated

    def to_dict(self) -> dict:
        return {
            "value":          self.value,
            "status":         self.status.value,
            "direction":      self.direction,
            "unit":           self.unit,
            "definition_id":  self.definition_id,
            "counts":         self.counts,
            "scope":          self.scope,
            "normalization_profile": self.normalization_profile,
            "warnings":       self.warnings,
            "coverage":       self.coverage,
        }


# ---------------------------------------------------------------------------
# Alignment pair
# ---------------------------------------------------------------------------

@dataclass
class AlignedPair:
    """A matched (or unmatched) pair from the block aligner.

    Fields
    ------
    reference_id      node_id of the reference element
    hypothesis_id     node_id of the hypothesis element (None if unmatched ref)
    status            matched / ambiguous / unmatched / extra
    alignment_score   composite score from the aligner [0.0, 1.0]
    text_similarity   character-level similarity of visible_text
    type_expected     NodeKind of the reference element
    type_observed     NodeKind of the hypothesis element (None if unmatched)
    page_expected     page of the reference element
    page_observed     page of the hypothesis element (None if unmatched)
    reference_order   order_index in reference
    hypothesis_order  order_index in hypothesis (None if unmatched)
    ambiguity         True if this match competed with another near-equal candidate
    reasons           Short strings explaining why this pair was accepted/rejected
    """
    reference_id:     str | None
    hypothesis_id:    str | None
    status:           AlignmentStatus
    alignment_score:  float = 0.0
    text_similarity:  float = 0.0
    type_expected:    NodeKind | None = None
    type_observed:    NodeKind | None = None
    page_expected:    int | None = None
    page_observed:    int | None = None
    reference_order:  int | None = None
    hypothesis_order: int | None = None
    ambiguity:        bool = False
    reasons:          list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "reference_id":    self.reference_id,
            "hypothesis_id":   self.hypothesis_id,
            "status":          self.status.value,
            "alignment_score": round(self.alignment_score, 4),
            "text_similarity": round(self.text_similarity, 4),
            "type_expected":   self.type_expected.value if self.type_expected else None,
            "type_observed":   self.type_observed.value if self.type_observed else None,
            "page_expected":   self.page_expected,
            "page_observed":   self.page_observed,
            "reference_order": self.reference_order,
            "hypothesis_order": self.hypothesis_order,
            "ambiguity":       self.ambiguity,
            "reasons":         self.reasons,
        }


# ---------------------------------------------------------------------------
# Page scope summary
# ---------------------------------------------------------------------------

@dataclass
class PageScope:
    """Canonical page-set classification for one evaluation run (spec §3.1)."""
    selected:              set[int]   # pages the engine was asked to produce
    selected_reference:    set[int]   # selected ∩ ref pages
    selected_present:      set[int]   # selected_reference ∩ hyp pages
    selected_missing:      set[int]   # selected_reference − hyp pages
    selected_extra:        set[int]   # hyp pages − selected
    selected_without_ref:  set[int]   # selected − ref pages

    def to_dict(self) -> dict:
        return {
            "selected":             sorted(self.selected),
            "selected_reference":   sorted(self.selected_reference),
            "selected_present":     sorted(self.selected_present),
            "selected_missing":     sorted(self.selected_missing),
            "selected_extra":       sorted(self.selected_extra),
            "selected_without_ref": sorted(self.selected_without_ref),
        }
