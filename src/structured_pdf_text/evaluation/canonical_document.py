"""Canonical document representation for the unified evaluation pipeline (spec §3.1).

The central problem solved here is that ``parse_document()`` emits nodes for
structural containers AND their children (TABLE → TABLE_ROW → TABLE_CELL,
LIST → LIST_ITEM, etc.).  Concatenating ``visible_text`` from *all* nodes
would count every word multiple times.

This module builds a **non-overlapping projection** of terminal text units:
only *leaf* nodes (those with no children, or whose kind is a semantic leaf)
contribute text to CER/WER computation.  Container nodes contribute structure
but not duplicate text.

Leaf kinds (terminal for text purposes):
  PARAGRAPH, HEADING, TABLE_CELL, LIST_ITEM, CODE, BLOCKQUOTE, THEMATIC_BREAK

Container kinds (structure only, text comes from descendants):
  TABLE, TABLE_ROW, LIST

Usage
-----
    doc = CanonicalDocument.from_nodes(ref_nodes, origin="reference")
    text = doc.visible_text          # deduplicated, ordered
    leaf_nodes = doc.leaf_nodes      # only terminals
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from structured_pdf_text.evaluation.models import MarkdownNode, NodeKind


# Kinds whose visible_text is the authoritative text for CER/WER.
# Container kinds (TABLE, TABLE_ROW, LIST) are excluded — their text
# comes from their descendant leaf nodes.
_LEAF_KINDS: frozenset[NodeKind] = frozenset({
    NodeKind.PARAGRAPH,
    NodeKind.HEADING,
    NodeKind.TABLE_CELL,
    NodeKind.LIST_ITEM,
    NodeKind.CODE,
    NodeKind.BLOCKQUOTE,
    NodeKind.THEMATIC_BREAK,
})

@dataclass
class CanonicalDocument:
    """Non-overlapping projection of a parsed Markdown document.

    Attributes
    ----------
    leaf_nodes    : MarkdownNode list — only terminals in document order.
    all_nodes     : All parsed nodes (for structural metrics).
    visible_text  : Deduplicated visible text from leaf_nodes, space-separated.
    raw_markdown  : Original Markdown body (page markers stripped by parser).
    origin        : "reference" or "hypothesis".
    """
    leaf_nodes:   list[MarkdownNode]
    all_nodes:    list[MarkdownNode]
    visible_text: str
    raw_markdown: str
    origin:       str

    @classmethod
    def from_nodes(
        cls,
        nodes: Sequence[MarkdownNode],
        origin: str = "hypothesis",
        raw_markdown: str | None = None,
    ) -> "CanonicalDocument":
        """Build a CanonicalDocument from a flat node list.

        Strategy: select the **deepest** text-bearing nodes — nodes whose
        visible_text would not be duplicated by any ancestor.

        A node contributes text if and only if:
          1. Its kind is in _LEAF_KINDS, AND
          2. Its node_id does NOT appear as parent_id of any other node.

        This ensures:
          - TABLE contributes text only via TABLE_CELL (deepest).
          - LIST contributes text only via PARAGRAPH inside LIST_ITEM.
          - LIST_ITEM contributes text only via its PARAGRAPH children when
            present; if a LIST_ITEM has no PARAGRAPH child (simple flat list),
            the LIST_ITEM itself is the leaf.
          - Container kinds (TABLE, TABLE_ROW, LIST) never contribute text
            directly, even when they have no children (they have no text).

        Parameters
        ----------
        nodes        : Output of ``parse_document()`` — may contain overlapping
                       container+child pairs.
        origin       : "reference" or "hypothesis".
        raw_markdown : The original Markdown text before parsing (used for
                       cer_markdown_raw).
        """
        node_list = list(nodes)

        # Build set of node_ids that appear as parent_id of any other node.
        # Any node in this set has at least one child, so it is a container
        # for the purposes of text projection.
        parent_ids: set[str] = {
            n.parent_id for n in node_list if n.parent_id is not None
        }

        leaf_nodes: list[MarkdownNode] = []
        for n in node_list:
            # Must be a semantic leaf kind (excludes TABLE, TABLE_ROW, LIST)
            if n.kind not in _LEAF_KINDS:
                continue
            # Must not itself have children in this node list
            # (e.g., LIST_ITEM can have PARAGRAPH children)
            if n.node_id in parent_ids:
                continue
            leaf_nodes.append(n)

        # Build visible text: join non-empty visible_text from leaves,
        # with a single space separator.  NFC is applied by the parser already.
        parts = [n.visible_text.strip() for n in leaf_nodes if n.visible_text.strip()]
        visible = " ".join(parts)
        if raw_markdown is None:
            # Parsers retain a single source body per page, including syntax
            # and blank lines that cannot be reconstructed from terminal text.
            raw_markdown = "\n\n".join(
                n.metadata["page_markdown"] for n in node_list if "page_markdown" in n.metadata
            )

        return cls(
            leaf_nodes=leaf_nodes,
            all_nodes=node_list,
            visible_text=visible,
            raw_markdown=raw_markdown,
            origin=origin,
        )
