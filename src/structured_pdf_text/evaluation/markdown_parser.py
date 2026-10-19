"""CommonMark + GFM Markdown parser producing MarkdownNode trees (spec §2.5).

Uses markdown-it-py (CommonMark spec, GFM tables extension) with a locked
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
* Table: only GFM pipe tables with a valid separator row are recognised.
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

PARSER_VERSION = "markdown-it-py-3.0.0+gfm"

# Page header pattern used by the PDFExtractor renderer
_PAGE_HDR_RE = re.compile(
    r"^##\s+P[áa]gina\s+0*(\d+)[^\n]*", re.IGNORECASE | re.MULTILINE
)

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
            sub.content if sub.type in ("text", "text_special", "code_inline") else
            " " if sub.type in ("softbreak", "hardbreak") else ""
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
    within the page body (not the full document).

    Raises ValueError on duplicate page markers.
    """
    matches = list(_PAGE_HDR_RE.finditer(text))
    if not matches:
        # No page markers: treat the entire document as a single "page 0"
        return {0: (text, 0)}

    seen: set[int] = set()
    pages: dict[int, tuple[str, int]] = {}
    for i, m in enumerate(matches):
        pnum = int(m.group(1))
        if pnum in seen:
            raise ValueError(
                f"Duplicate page marker '## Página {pnum}' at offset {m.start()}"
            )
        seen.add(pnum)
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip("\n")
        pages[pnum] = (body, body_start)
    return pages


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

    # Skip the synthetic root and pure inline/text containers
    if ntype in ("root", "inline", "text", "softbreak", "hardbreak",
                 "strong", "em", "link", "image", "code_inline",
                 "html_inline", "html_block"):
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
        lines = page_body.split("\n")
        s_line, e_line = tok.map
        src_start = page_body_offset + sum(len(l) + 1 for l in lines[:s_line])
        src_end   = page_body_offset + sum(len(l) + 1 for l in lines[:e_line])

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

    # Determine nesting context for children
    new_list_depth = list_depth + 1 if kind == NodeKind.LIST else list_depth
    new_ordered    = l_ordered if kind == NodeKind.LIST else in_ordered

    for child in (node.children or []):
        _walk(child, page_body, page_body_offset, origin, page,
              counter, node_id, result, new_list_depth, new_ordered)


def _raw_lines(body: str, tok) -> str:
    """Return the raw Markdown source lines for a token."""
    if tok and tok.map:
        lines = body.split("\n")
        s, e = tok.map
        return "\n".join(lines[s:e])
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

    for page_num in sorted(pages.keys()):
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
