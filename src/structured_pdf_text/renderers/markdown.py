from __future__ import annotations

from structured_pdf_text.document import StructuredDocument, StructuredTable


def render_markdown(document: StructuredDocument) -> str:
    """Render page boundaries and detected tables without changing the model."""
    sections: list[str] = []
    for page in document.pages:
        body = page.reading_text.strip()
        if body:
            sections.append(f"## Página {page.page_index + 1}\n\n{body}")
        else:
            sections.append(f"## Página {page.page_index + 1}")
    for table in document.tables:
        rendered = _render_table(table)
        if rendered:
            pages = sorted({fragment.page_index + 1 for fragment in table.page_fragments})
            page_label = ", ".join(str(page) for page in pages) or "desconhecida"
            sections.append(
                f"### Tabela {table.table_id} (página(s): {page_label})\n\n{rendered}"
            )
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
