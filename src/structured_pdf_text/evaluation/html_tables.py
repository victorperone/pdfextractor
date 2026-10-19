"""Visible HTML blocks and table anchors for the canonical Markdown AST.

No browser, external resources or CSS execution. Cell text is emitted once;
rowspan/colspan reserve grid coordinates without copying the text into slots.
Unsupported/ambiguous table markup raises instead of silently losing content.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser

from .models import MarkdownNode, NodeKind, NormProfile
from .normalization import normalize

HTML_TABLE_POLICY = "html-table-anchors-v1"
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
         "meta", "param", "source", "track", "wbr"}
_HIDDEN = {"script", "style", "template", "head"}
_BLOCK = {"div", "p", "section", "article", "li", "ul", "ol", "pre", "blockquote",
          "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td", "th", "caption"}


@dataclass
class _Element:
    tag: str
    attrs: dict[str, str | None] = field(default_factory=dict)
    children: list[_Element | str] = field(default_factory=list)
    start: int = 0
    end: int = 0


class _FragmentParser(HTMLParser):
    def __init__(self, text: str):
        super().__init__(convert_charrefs=True)
        self.root = _Element("root", end=len(text))
        self.stack = [self.root]
        self.offsets = [0]
        for line in text.splitlines(keepends=True):
            self.offsets.append(self.offsets[-1] + len(line))
        self.text = text

    def _offset(self) -> int:
        line, column = self.getpos()
        return self.offsets[line - 1] + column

    def _close_from(self, index: int, end: int) -> None:
        for element in self.stack[index:]:
            element.end = end
        del self.stack[index:]

    def handle_starttag(self, tag, attrs):
        # Common HTML optional end tags; never cross the current table.
        close = ({"td", "th"} if tag in {"td", "th"} else
                 {"td", "th", "tr"} if tag == "tr" else
                 {"td", "th", "tr", "thead", "tbody", "tfoot"}
                 if tag in {"thead", "tbody", "tfoot"} else set())
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == "table":
                break
            if self.stack[index].tag in close:
                self._close_from(index, self._offset())
        element = _Element(tag, dict(attrs), start=self._offset(),
                           end=self._offset() + len(self.get_starttag_text()))
        self.stack[-1].children.append(element)
        if tag not in _VOID:
            self.stack.append(element)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self._close_from(len(self.stack) - 1, self._offset() + len(self.get_starttag_text()))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                end = self.text.find(">", self._offset()) + 1
                self._close_from(index, end)
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)

    def finish(self):
        self.feed(self.text)
        self.close()
        if any(element.tag == "table" for element in self.stack):
            # CommonMark HTML blocks may end at a blank line. Do not grade
            # just the opening fragment as if it were the complete table.
            raise ValueError("Unclosed HTML table block; keep the complete table in one HTML block")
        self._close_from(1, len(self.text))
        return self.root


def _hidden(element: _Element) -> bool:
    return element.tag in _HIDDEN or "hidden" in element.attrs


def _visible(element: _Element | str) -> str:
    if isinstance(element, str):
        return element
    if _hidden(element):
        return ""
    if element.tag in {"br", "hr"}:
        return " "
    if element.tag == "img":
        return element.attrs.get("alt") or ""
    text = "".join(_visible(child) for child in element.children)
    return f" {text} " if element.tag in _BLOCK else text


def _descendants(element: _Element, tag: str):
    for child in element.children:
        if isinstance(child, _Element) and not _hidden(child):
            if child.tag == tag:
                yield child
            yield from _descendants(child, tag)


def _span(cell: _Element, key: str) -> int:
    raw = cell.attrs.get(key, "1")
    try:
        value = int(raw) if raw is not None else 1
    except (ValueError, TypeError):
        raise ValueError(f"Invalid HTML table {key}: {raw!r}") from None
    if value < 0 or (key == "colspan" and value == 0):
        raise ValueError(f"Invalid HTML table {key}: {raw!r}")
    return value


def parse_html_block(text: str, *, origin: str, page: int | None,
                     start_seq: int, parent_id: str | None,
                     source_offset: int = 0) -> list[MarkdownNode]:
    """Parse a Markdown HTML block. Nested tables are explicitly unsupported.

    Plain text, captions and wrappers are retained. Malformed table structure
    and overlapping spans raise ValueError, preventing an apparently complete
    grade. rowspan=0 extends to the end of its row group.
    """
    root = _FragmentParser(text).finish()
    result: list[MarkdownNode] = []

    def emit(element, kind, parent, visible=None, **metadata):
        seq = start_seq + len(result)
        node = MarkdownNode(
            node_id=MarkdownNode.make_id(origin, page, kind, seq), origin=origin,
            page=page, kind=kind, raw_markdown=text[element.start:element.end],
            visible_text=normalize(_visible(element) if visible is None else visible,
                                   NormProfile.UNICODE_LAYOUT),
            source_start=source_offset + element.start,
            source_end=source_offset + element.end, order_index=seq,
            parent_id=parent, metadata={"format": "html", **metadata},
        )
        result.append(node)
        return node

    def table(element, parent):
        nested = list(_descendants(element, "table"))
        if nested:
            raise ValueError("Nested HTML tables are not supported by the evaluator")
        # Only row groups and rows may carry cell content. Reject markup that
        # would otherwise disappear when selecting table-cell text leaves.
        def check_container(container, allowed):
            for child in container.children:
                if isinstance(child, str):
                    if child.strip():
                        raise ValueError("HTML table has text outside a cell/caption")
                elif not _hidden(child) and child.tag not in allowed:
                    raise ValueError(f"Unsupported HTML table element: {child.tag}")

        check_container(element, {"caption", "colgroup", "col", "thead", "tbody", "tfoot", "tr"})
        rows: list[tuple[_Element, int]] = []
        for child in element.children:
            if not isinstance(child, _Element) or _hidden(child):
                continue
            if child.tag == "tr":
                rows.append((child, id(element)))
            elif child.tag in {"thead", "tbody", "tfoot"}:
                check_container(child, {"tr"})
                rows.extend((row, id(child)) for row in child.children
                            if isinstance(row, _Element) and row.tag == "tr" and not _hidden(row))
        node = emit(element, NodeKind.TABLE, parent)
        for caption in (c for c in element.children if isinstance(c, _Element)
                        and c.tag == "caption" and not _hidden(c)):
            emit(caption, NodeKind.PARAGRAPH, node.node_id)
        # Active intervals, not a potentially huge expanded grid.
        active: list[tuple[int, int, int]] = []  # column start/end, exclusive end row
        width = 0
        for ri, (row, group) in enumerate(rows):
            check_container(row, {"td", "th"})
            rn = emit(row, NodeKind.TABLE_ROW, node.node_id, row_index=ri)
            active = [slot for slot in active if slot[2] > ri]
            column = 0
            for cell in (c for c in row.children if isinstance(c, _Element)
                         and c.tag in {"td", "th"} and not _hidden(c)):
                while True:
                    occupying = next((slot for slot in active if slot[0] <= column < slot[1]), None)
                    if occupying is None:
                        break
                    column = occupying[1]
                colspan, rowspan = _span(cell, "colspan"), _span(cell, "rowspan")
                group_end = ri + 1
                while group_end < len(rows) and rows[group_end][1] == group:
                    group_end += 1
                rowspan = group_end - ri if rowspan == 0 else min(rowspan, group_end - ri)
                if any(left < column + colspan and right > column for left, right, _ in active):
                    raise ValueError("Overlapping HTML table spans")
                emit(cell, NodeKind.TABLE_CELL, rn.node_id, row_index=ri,
                     column_index=column, rowspan=rowspan, colspan=colspan,
                     header=cell.tag == "th")
                active.append((column, column + colspan, ri + rowspan))
                column += colspan
                width = max(width, column)
        node.metadata.update(row_count=len(rows), column_count=width,
                             topology_policy=HTML_TABLE_POLICY)

    def visit(element, parent):
        if _hidden(element):
            return
        if element.tag == "table":
            table(element, parent)
            return
        # Split wrappers around tables so surrounding text is never lost.
        if not list(_descendants(element, "table")):
            if _visible(element).strip():
                emit(element, NodeKind.PARAGRAPH, parent)
            return
        pending: list[_Element | str] = []

        def flush():
            if pending:
                fragment = _Element("div", children=list(pending), start=element.start, end=element.end)
                if _visible(fragment).strip():
                    emit(fragment, NodeKind.PARAGRAPH, parent)
                pending.clear()

        for child in element.children:
            if isinstance(child, _Element) and (child.tag == "table" or list(_descendants(child, "table"))):
                flush()
                visit(child, parent)
            else:
                pending.append(child)
        flush()

    visit(root, parent_id)
    return result
