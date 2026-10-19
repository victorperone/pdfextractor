"""CommonMark + GFM Markdown parser producing MarkdownNode trees (spec §2.5).

Uses markdown-it-py (CommonMark spec, GFM tables extension) with the installed
version recorded in PARSER_VERSION.  This version string must be included
in every MetricResult that depends on structural parsing so that runs are
comparable only when both used the same parser version.

Design decisions
----------------
* Page delimiters (``## Página N`` / ``## Página 001 |``) are treated as
  metadata, not semantic headings.  They are stripped before the AST is
  built and the page number is recorded on each node.
* Code fences: content inside ``` … ``` is NOT parsed for further structure.
  A ``#`` inside a fence does not produce a heading node.
* Tables: GFM pipe tables require a valid separator row. HTML table blocks
  retain anchor cells and rowspan/colspan coordinates, without duplicated text.
  Lines with ``|`` in body text or code do not produce table nodes.
* Duplicate page markers raise a ``ValueError`` with a descriptive message.
* Empty documents produce an empty node list, not an error.

Node IDs are deterministic within a (text, origin) pair.  They depend only
on the position in the document, not on random state, so repeated calls
with the same input produce the same IDs.
"""
from __future__ import annotations

import re

from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode

from structured_pdf_text.evaluation.models import MarkdownNode, NodeKind, NormProfile
from structured_pdf_text.evaluation.normalization import normalize
from structured_pdf_text.evaluation.html_tables import parse_html_block
from .markdown_syntax import PARSER_VERSION, source_lines, split_page_sources

# Build a shared parser instance (thread-safe for reading)
_md = MarkdownIt("commonmark").enable("table")

# Block-level node types that become structural MarkdownNode entries.
# Inline types are used only for text extraction.
_STRUCTURAL_TYPES: frozenset[str] = frozenset({
    "heading", "paragraph", "fence", "code_block",
    "bullet_list", "ordered_list", "list_item",
    "table", "tr", "td", "th",
    "blockquote", "hr",
})

# thead/tbody are transparent containers — we traverse into them but don't emit nodes
_TRANSPARENT_TYPES: frozenset[str] = frozenset({"thead", "tbody"})

_TYPE_TO_KIND: dict[str, NodeKind] = {
    "heading":      NodeKind.HEADING,
    "paragraph":    NodeKind.PARAGRAPH,
    "fence":        NodeKind.CODE,
    "code_block":   NodeKind.CODE,
    "bullet_list":  NodeKind.LIST,
    "ordered_list": NodeKind.LIST,
    "list_item":    NodeKind.LIST_ITEM,
    "table":        NodeKind.TABLE,
    "tr":           NodeKind.TABLE_ROW,
    "td":           NodeKind.TABLE_CELL,
    "th":           NodeKind.TABLE_CELL,
    "blockquote":   NodeKind.BLOCKQUOTE,
    "hr":           NodeKind.THEMATIC_BREAK,
}


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def _extract_visible_text(node: SyntaxTreeNode) -> str:
    """Join inline runs contiguously and separate structural child blocks."""
    tok = node.token
    if tok and tok.type == "inline":
        return "".join(
            " " if sub.type in ("softbreak", "hardbreak") or
            (sub.type == "html_inline" and re.fullmatch(r"<br\s*/?>", sub.content, re.I))
            else sub.content if sub.type in ("text", "text_special", "code_inline") else ""
            for sub in (tok.children or [])
        )
    if tok and tok.type in ("text", "text_special", "code_inline", "fence", "code_block"):
        return tok.content
    return " ".join(filter(None, (_extract_visible_text(child) for child in node.children)))


# ---------------------------------------------------------------------------
# Page splitting
# ---------------------------------------------------------------------------

def _split_pages(text: str) -> dict[int, tuple[str, int]]:
    """Split a document into ``{page_num: (body_text, char_offset_of_body)}``.

    Page headers are removed from the body text but their start positions are
    recorded so that source_start/source_end on each node refer to offsets
    within the original document, including its page marker lines.

    Raises ValueError on duplicate page markers.
    """
    return split_page_sources(text, implicit_page=0)


# ---------------------------------------------------------------------------
# Tree traversal
# ---------------------------------------------------------------------------

def _walk(
    node: SyntaxTreeNode,
    page_body: str,
    page_body_offset: int,
    origin: str,
    page: int | None,
    counter: list[int],
    parent_id: str | None,
    result: list[MarkdownNode],
    list_depth: int = 0,
    in_ordered: bool | None = None,
) -> None:
    """Depth-first traversal: emit one MarkdownNode per structural block."""
    ntype = node.type

    if ntype == "html_block":
        tok = node.token
        offset = page_body_offset
        if tok.map:
            offset += sum(map(len, source_lines(page_body)[:tok.map[0]]))
        html_nodes = parse_html_block(_raw_lines(page_body, tok), origin=origin, page=page,
                                     start_seq=counter[0], parent_id=parent_id,
                                     source_offset=offset)
        result.extend(html_nodes)
        counter[0] += len(html_nodes)
        return

    # Skip the synthetic root and pure inline/text containers
    if ntype in ("root", "inline", "text", "softbreak", "hardbreak",
                 "strong", "em", "link", "image", "code_inline",
                 "html_inline"):
        for child in (node.children or []):
            _walk(child, page_body, page_body_offset, origin, page,
                  counter, parent_id, result, list_depth, in_ordered)
        return

    # Transparent wrappers (thead, tbody): traverse into children without emitting a node
    if ntype in _TRANSPARENT_TYPES:
        for child in (node.children or []):
            _walk(child, page_body, page_body_offset, origin, page,
                  counter, parent_id, result, list_depth, in_ordered)
        return

    if ntype not in _STRUCTURAL_TYPES:
        # Unknown type — traverse children but don't emit a node
        for child in (node.children or []):
            _walk(child, page_body, page_body_offset, origin, page,
                  counter, parent_id, result, list_depth, in_ordered)
        return

    kind = _TYPE_TO_KIND.get(ntype, NodeKind.UNKNOWN)
    seq = counter[0]
    counter[0] += 1
    node_id = MarkdownNode.make_id(origin, page, kind, seq)

    # Paired SyntaxTreeNode groups expose their source on the opening token.
    tok = node.token or (node.nester_tokens.opening if node.nester_tokens else None)
    src_start = page_body_offset
    src_end   = page_body_offset
    if tok and tok.map:
        lines = source_lines(page_body)
        s_line, e_line = tok.map
        src_start = page_body_offset + sum(map(len, lines[:s_line]))
        src_end   = page_body_offset + sum(map(len, lines[:e_line]))

    visible_raw = _extract_visible_text(node)
    # The parser already projected Markdown. Reinterpreting the result as
    # Markdown would erase literal code markers and escaped punctuation.
    visible     = normalize(visible_raw, NormProfile.UNICODE_LAYOUT)

    # Heading-specific attributes: use node.tag (e.g. "h1") from SyntaxTreeNode,
    # not tok.tag which may be empty for paired open/close token groups.
    h_level: int | None = None
    if kind == NodeKind.HEADING:
        tag = getattr(node, "tag", None) or (tok.tag if tok else None)
        if tag and len(tag) == 2 and tag[0] == "h":
            try:
                h_level = int(tag[1])
            except ValueError:
                pass

    # List-specific attributes
    l_depth:    int | None = None
    l_ordered:  bool | None = None
    if kind in (NodeKind.LIST, NodeKind.LIST_ITEM):
        l_depth = list_depth
    if kind == NodeKind.LIST:
        l_ordered = (ntype == "ordered_list")
    elif kind == NodeKind.LIST_ITEM:
        l_ordered = in_ordered

    mn = MarkdownNode(
        node_id       = node_id,
        origin        = origin,
        page          = page,
        kind          = kind,
        raw_markdown  = _raw_lines(page_body, tok),
        visible_text  = visible,
        source_start  = src_start,
        source_end    = src_end,
        order_index   = seq,
        heading_level = h_level,
        list_depth    = l_depth,
        ordered_list  = l_ordered,
        parent_id     = parent_id,
        children      = [],
    )
    result.append(mn)

    if kind == NodeKind.LIST and l_ordered:
        start = tok.attrGet("start")
        mn.metadata["list_start"] = int(start) if start is not None else 1
        ordinal = mn.metadata["list_start"]
        for child in node.children or []:
            if child.type == "list_item":
                # CommonMark renders start + item index; subsequent source
                # markers may repeat 1 and do not change the displayed ordinal.
                opening = child.nester_tokens.opening
                opening.meta["ordered_ordinal"] = ordinal
                ordinal += 1
    elif kind == NodeKind.LIST_ITEM and l_ordered:
        mn.metadata["ordered_ordinal"] = tok.meta["ordered_ordinal"]

    # Determine nesting context for children
    new_list_depth = list_depth + 1 if kind == NodeKind.LIST else list_depth
    new_ordered    = l_ordered if kind == NodeKind.LIST else in_ordered

    for child in (node.children or []):
        _walk(child, page_body, page_body_offset, origin, page,
              counter, node_id, result, new_list_depth, new_ordered)


def _raw_lines(body: str, tok) -> str:
    """Return the raw Markdown source lines for a token."""
    if tok and tok.map:
        lines = source_lines(body)
        s, e = tok.map
        return re.sub(r"(?:\r\n|\r|\n)\Z", "", "".join(lines[s:e]))
    return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_document(
    text: str,
    origin: str = "hypothesis",
) -> list[MarkdownNode]:
    """Parse a Markdown document into a flat list of MarkdownNode objects.

    Parameters
    ----------
    text   : Full Markdown document text (may contain ``## Página N`` markers).
    origin : ``"reference"`` or ``"hypothesis"`` — embedded in all node IDs.

    Returns
    -------
    List of MarkdownNode in document reading order.

    Raises
    ------
    ValueError
        When a duplicate ``## Página N`` marker is detected.
    """
    pages = _split_pages(text)
    all_nodes: list[MarkdownNode] = []
    counter = [0]

    for page_num in pages:
        body, offset = pages[page_num]
        page = page_num if page_num != 0 else None
        if not body.strip():
            continue

        tokens = _md.parse(body)
        tree = SyntaxTreeNode(tokens)
        first_node = len(all_nodes)
        for child in tree.children:
            _walk(child, body, offset, origin, page,
                  counter=counter, parent_id=None, result=all_nodes)
        if len(all_nodes) > first_node:
            all_nodes[first_node].metadata["page_markdown"] = body

    return all_nodes


def parse_page(
    text: str,
    page: int,
    origin: str = "hypothesis",
    start_seq: int = 0,
) -> list[MarkdownNode]:
    """Parse a single page body (without page-marker header).

    Parameters
    ----------
    text      : Page body text (no ``## Página N`` prefix).
    page      : 1-based page number.
    origin    : ``"reference"`` or ``"hypothesis"``.
    start_seq : Starting order_index for this page's nodes.

    Returns
    -------
    List of MarkdownNode for this page in reading order.
    """
    if not text.strip():
        return []
    counter = [start_seq]
    tokens = _md.parse(text)
    tree = SyntaxTreeNode(tokens)
    result: list[MarkdownNode] = []
    for child in tree.children:
        _walk(child, text, 0, origin, page,
              counter=counter, parent_id=None, result=result)
    if result:
        result[0].metadata["page_markdown"] = text
    return result
