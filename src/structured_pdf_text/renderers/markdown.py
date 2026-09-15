from __future__ import annotations

from structured_pdf_text.document import RegionKind, StructuredDocument, StructuredTable


def render_markdown(document: StructuredDocument) -> str:
    """Render page boundaries and detected tables without changing the model."""
    tables_by_page: dict[int, list[StructuredTable]] = {}
    for table in document.tables:
        first_page = min(
            (fragment.page_index for fragment in table.page_fragments),
            default=None,
        )
        if first_page is not None:
            tables_by_page.setdefault(first_page, []).append(table)

    table_region_ids: set[str] = {
        region_id
        for table in document.tables
        for fragment in table.page_fragments
        for region_id in getattr(fragment, "region_ids", [])
    }

    sections: list[str] = []
    for page in document.pages:
        if not page.regions:
            body = page.reading_text.strip()
            if body:
                sections.append(f"## Página {page.page_index + 1}\n\n{body}")
            else:
                sections.append(f"## Página {page.page_index + 1}")
            for table in tables_by_page.get(page.page_index, []):
                rendered = _render_table(table)
                if rendered:
                    all_pages = sorted({fragment.page_index + 1 for fragment in table.page_fragments})
                    page_label = ", ".join(str(p) for p in all_pages) or "desconhecida"
                    sections.append(
                        f"#### Tabela {table.table_id} (página(s): {page_label})\n\n{rendered}"
                    )
            continue

        page_parts: list[str] = []
        for region in page.regions:
            if region.region_id in table_region_ids:
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
            else:
                body = "\n".join(
                    line.text.rstrip()
                    for line in region.native_lines
                    if line.text.strip()
                )
                if body:
                    page_parts.append(body)

        for table in tables_by_page.get(page.page_index, []):
            rendered = _render_table(table)
            if rendered:
                all_pages = sorted({fragment.page_index + 1 for fragment in table.page_fragments})
                page_label = ", ".join(str(p) for p in all_pages) or "desconhecida"
                page_parts.append(
                    f"#### Tabela {table.table_id} (página(s): {page_label})\n\n{rendered}"
                )

        if page_parts:
            sections.append(f"## Página {page.page_index + 1}\n\n" + "\n\n".join(page_parts))
        else:
            sections.append(f"## Página {page.page_index + 1}")

    return "\n\n".join(sections).strip()


def _render_table(table: StructuredTable) -> str:
    if not table.cells or table.column_count <= 0:
        return ""
    row_count = max(table.row_count, max(cell.row for cell in table.cells) + 1)
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


def _escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>")
