"""Markdown renderer.

Serializes a StructuredDocument by iterating page.content_blocks in order_index
order. All layout, geometry, and ownership decisions are already finalized in the
blocks — this module only formats text and tables (INV-06).
"""
from __future__ import annotations

from structured_pdf_text.document import (
    ContentKind,
    PageContentBlock,
    StructuredDocument,
    StructuredTable,
)


def render_markdown(document: StructuredDocument) -> str:
    preserve_hf: bool = document.diagnostics.facts.get(
        "preserve_headers_footers",
        True,
    )
    sections: list[str] = []

    for page in document.pages:
        table_map: dict[str, StructuredTable] = {
            table.table_id: table
            for table in page.tables
        }

        if page.content_blocks:
            page_parts: list[str] = []
            for block in sorted(page.content_blocks, key=lambda b: b.order_index):
                rendered = _render_content_block(block, table_map, preserve_hf=preserve_hf)
                if rendered:
                    page_parts.append(rendered)
        else:
            # Fallback for pages where content_blocks were not assembled
            # (e.g. pages produced by older code paths in tests).
            page_parts = _render_page_legacy(page, table_map, preserve_hf)

        sections.append(_render_page_section(page.page_index, page_parts))

    return "\n\n".join(sections).strip()


def _render_content_block(
    block: PageContentBlock,
    table_map: dict[str, StructuredTable],
    *,
    preserve_hf: bool,
) -> str:
    if block.suppressed:
        return ""
    if block.kind in (ContentKind.HEADER, ContentKind.FOOTER):
        # Legacy blocks without line accounting predate document-level
        # repeated-furniture confirmation. Keep their old flag behavior while
        # canonical blocks rely exclusively on ``suppressed``.
        if not preserve_hf and not block.line_ids:
            return ""
        return block.text

    if block.kind == ContentKind.TITLE:
        level = block.heading_level or 1
        prefix = "#" * max(1, min(level, 6))
        return f"{prefix} {block.text}" if block.text else ""

    if block.kind == ContentKind.LIST and block.list_items:
        return "\n".join(
            f"{'  ' * max(0, item.level)}{item.marker} {item.text}"
            for item in block.list_items
        )

    if block.kind == ContentKind.TABLE:
        table = table_map.get(block.table_id or "")
        if table is None:
            return ""
        rendered = _render_table(table)
        if not rendered:
            return ""
        return (
            f"#### Tabela {table.table_id} "
            f"(página: {block.page_index + 1})\n\n"
            f"{rendered}"
        )

    if block.kind == ContentKind.FIGURE:
        # No semantic representation yet. Preserve OCR text when present so it
        # is not silently lost. Empty figures produce no Markdown output.
        return block.text or ""

    return block.text


# ---------------------------------------------------------------------------
# Legacy fallback (pages without content_blocks)
# ---------------------------------------------------------------------------

def _render_page_legacy(
    page,
    table_map: dict[str, StructuredTable],
    preserve_hf: bool,
) -> list[str]:
    """Render a page that has no content_blocks using region-based logic."""
    from structured_pdf_text.document import RegionKind

    rendered_tables = [
        (table, rendered)
        for table in page.tables
        if (rendered := _render_table(table))
    ]

    if not page.regions:
        page_parts: list[str] = []
        body = page.reading_text.strip()
        if body:
            page_parts.append(body)
        for table, rendered in rendered_tables:
            page_parts.append(
                f"#### Tabela {table.table_id} "
                f"(página: {page.page_index + 1})\n\n"
                f"{rendered}"
            )
        return page_parts

    page_parts: list[str] = []
    for region in page.regions:
        if region.kind == RegionKind.TABLE:
            if _region_has_renderable_table(region, page.page_index, rendered_tables):
                continue
            fallback = "\n".join(
                line.text.rstrip()
                for line in region.native_lines
                if line.text.strip()
            ).strip()
            if fallback:
                page_parts.append(fallback)
            continue

        if region.kind == RegionKind.TITLE and region.heading_level is not None:
            prefix = "#" * region.heading_level
            title_text = " ".join(
                line.text.strip()
                for line in region.native_lines
                if line.text.strip()
            )
            if title_text:
                page_parts.append(f"{prefix} {title_text}")
            continue

        body = "\n".join(
            line.text.rstrip()
            for line in region.native_lines
            if line.text.strip()
        ).strip()
        if body:
            page_parts.append(body)

    for table, rendered in rendered_tables:
        page_parts.append(
            f"#### Tabela {table.table_id} "
            f"(página: {page.page_index + 1})\n\n"
            f"{rendered}"
        )
    return page_parts


def _region_has_renderable_table(region, page_index, rendered_tables) -> bool:
    for table, _ in rendered_tables:
        for fragment in table.page_fragments:
            if fragment.page_index != page_index or fragment.bbox is None:
                continue
            if (
                region.bbox.overlap_ratio(fragment.bbox) >= 0.50
                or fragment.bbox.overlap_ratio(region.bbox) >= 0.50
            ):
                return True
    return False


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _render_page_section(page_index: int, parts: list[str]) -> str:
    heading = f"## Página {page_index + 1}"
    if not parts:
        return heading
    return heading + "\n\n" + "\n\n".join(parts)


def _render_table(table: StructuredTable) -> str:
    if not table.cells or table.column_count <= 0:
        return ""

    if any(cell.rowspan > 1 or cell.colspan > 1 for cell in table.cells):
        return _render_spanned_table_html(table)

    row_count = max(
        table.row_count,
        max(cell.row for cell in table.cells) + 1,
    )
    rows = [["" for _ in range(table.column_count)] for _ in range(row_count)]
    for cell in table.cells:
        if 0 <= cell.row < row_count and 0 <= cell.col < table.column_count:
            rows[cell.row][cell.col] = _escape_cell(cell.text)

    if not any(any(row) for row in rows):
        return ""

    header = rows[0]
    output = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    output.extend("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n".join(output)


def _render_spanned_table_html(table: StructuredTable) -> str:
    """Render merged cells as HTML embedded in Markdown.

    CommonMark tables cannot represent row/column spans. The structured JSON
    already retains them; HTML keeps the same semantics in the human-facing
    Markdown view without flattening headers into blank cells.
    """
    rows: list[list[str]] = [[] for _ in range(max(table.row_count, 1))]
    for cell in sorted(table.cells, key=lambda item: (item.row, item.col)):
        tag = "th" if cell.row == 0 else "td"
        attrs: list[str] = []
        if cell.rowspan > 1:
            attrs.append(f'rowspan="{cell.rowspan}"')
        if cell.colspan > 1:
            attrs.append(f'colspan="{cell.colspan}"')
        attribute_text = (" " + " ".join(attrs)) if attrs else ""
        value = _escape_html(cell.text).replace("\n", "<br>")
        rows[cell.row].append(f"<{tag}{attribute_text}>{value}</{tag}>")
    body = "\n".join(
        "  <tr>\n    " + "\n    ".join(cells) + "\n  </tr>"
        for cells in rows if cells
    )
    return "<table>\n" + body + "\n</table>"


def _escape_cell(value: str) -> str:
    return (
        value
        .replace("|", "\\|")
        .replace("\r\n", "<br>")
        .replace("\n", "<br>")
    )


def _escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
