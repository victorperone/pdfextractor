"""Shared CommonMark grammar for page delimiters and fence diagnostics."""
from __future__ import annotations

import re
from dataclasses import dataclass
from importlib.metadata import version
from html.parser import HTMLParser

from markdown_it import MarkdownIt

PARSER_VERSION = f"markdown-it-py-{version('markdown-it-py')}+gfm"
PAGE_SYNTAX_POLICY = "top-level-page-markers-outside-code-html-v1"
_PAGE_HEADER = re.compile(r"##[ \t]+P(?:á|a\u0301|a)gina[ \t]+0*(\d+)(?=[ \t]|$)[^\r\n]*", re.I)


def markdown_tokens(text: str):
    # A fresh parser avoids sharing mutable parser state between callers.
    return MarkdownIt("commonmark").enable("table").parse(text)


def source_lines(text: str) -> list[str]:
    """CommonMark source lines with original CR/LF separators retained."""
    return [line for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", text) if line]


@dataclass(frozen=True)
class PageMarker:
    page: int
    start: int
    end: int


class DuplicatePageMarker(ValueError):
    def __init__(self, page: int, offset: int):
        self.page = page
        super().__init__(f"Duplicate page marker '## Página {page}' at offset {offset}")


class _HTMLContext(HTMLParser):
    """Track actual HTML elements across CommonMark type-6 HTML block lines."""
    _VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input",
                       "link", "meta", "param", "source", "track", "wbr"})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []

    def handle_starttag(self, tag, attrs):
        if tag not in self._VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.stack:
            del self.stack[len(self.stack) - 1 - self.stack[::-1].index(tag):]

    def handle_startendtag(self, tag, attrs):
        pass


def page_markers(text: str) -> list[PageMarker]:
    """Column-zero page headings outside code, containers and HTML elements.

    Code, HTML blocks, list items and blockquotes retain their literal content.
    A completed HTML element may be followed immediately by a page delimiter:
    CommonMark keeps that line in its HTML token until a blank line, whereas
    the page protocol must end the previous page after the closed element.
    Offsets address the original string, including CRLF line endings.
    """
    # CommonMark recognizes CR/LF, not Python's additional Unicode splitlines
    # characters (e.g. U+2028 inside ordinary paragraph text).
    lines = source_lines(text)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    result = []
    for token in markdown_tokens(text):
        if token.type == "html_block" and token.level == 0 and token.map:
            context = _HTMLContext()
            for line in range(*token.map):
                source = lines[line].rstrip("\r\n")
                match = _PAGE_HEADER.fullmatch(source)
                # Only immediately following completed HTML elements. Stop at
                # other data (including fence openers); its literal contents
                # must not manufacture delimiters later in this HTML token.
                if not context.stack and not context.rawdata.strip():
                    if match:
                        result.append(PageMarker(int(match[1]), offsets[line], offsets[line] + len(source)))
                        continue
                    if not source.lstrip().startswith("<"):
                        break
                context.feed(lines[line])
            continue
        if (token.type != "heading_open" or token.tag != "h2"
                or token.level != 0 or token.markup != "##" or not token.map):
            continue
        line = token.map[0]
        source = lines[line].rstrip("\r\n")
        match = _PAGE_HEADER.fullmatch(source)
        if match:
            result.append(PageMarker(int(match[1]), offsets[line], offsets[line] + len(source)))
    return result


def split_page_sources(text: str, *, implicit_page: int = 1) -> dict[int, tuple[str, int]]:
    """Preserve observed page order and body offsets; reject real duplicates."""
    markers = page_markers(text)
    if not markers:
        return {implicit_page: (text, 0)}
    pages = {}
    for i, marker in enumerate(markers):
        if marker.page in pages:
            raise DuplicatePageMarker(marker.page, marker.start)
        end = markers[i + 1].start if i + 1 < len(markers) else len(text)
        raw = text[marker.end:end]
        body = raw.strip("\r\n")
        start = marker.end + len(raw) - len(raw.lstrip("\r\n"))
        pages[marker.page] = (body, start)
    return pages


def unclosed_fence_count(text: str) -> int:
    """Use the parser's source maps, including fences nested in containers.

    A closed token spans its opener, every content line, and its closer.
    CommonMark also emits tokens for fences auto-closed at EOF/container end;
    these have no closing source line. No counting of literal fence strings.
    """
    count = 0
    for token in markdown_tokens(text):
        if token.type != "fence":
            continue
        content_lines = token.content.count("\n") + bool(token.content and not token.content.endswith("\n"))
        if token.map[1] - token.map[0] != content_lines + 2:
            count += 1
    return count
