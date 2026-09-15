from __future__ import annotations

from structured_pdf_text.document import (
    LayoutRegion,
    RegionKind,
    StructuredDocument,
    StructuredTable,
)


def render_markdown(document: StructuredDocument) -> str:
    """Render page boundaries and detected tables without changing the model."""
    preserve_hf: bool = document.diagnostics.facts.get(
        "preserve_headers_footers",
        True,
    )
    sections: list[str] = []

    for page in document.pages:
        # IMPORTANT:
        # Render the physical tables that belong to this page.
        #
        # document.tables may contain logical tables merged across multiple
        # pages. Using document.tables here causes all fragments of a merged
        # table to be rendered only on its first page, leaving continuation
        # pages empty.
        rendered_tables = [
            (table, rendered)
            for table in page.tables
            if (rendered := _render_table(table))
        ]

        # Pages without layout regions still need to preserve reading_text
        # and any structured tables physically associated with this page.
        if not page.regions:
            page_parts: list[str] = []

            body = page.reading_text.strip()
            if body:
                page_parts.append(body)

            for table, rendered in rendered_tables:
                page_parts.append(
                    _render_table_block(
                        table,
                        rendered,
                        page.page_index,
                    )
                )

            sections.append(
                _render_page_section(
                    page.page_index,
                    page_parts,
                )
            )
            continue

        page_parts: list[str] = []

        for region in page.regions:
            if region.kind == RegionKind.TABLE:
                # A TABLE region must only be suppressed when there is a
                # renderable StructuredTable covering that same region.
                #
                # Otherwise preserve the OCR/native lines as fallback.
                if _region_has_renderable_table(
                    region,
                    page.page_index,
                    rendered_tables,
                ):
                    continue

                fallback = _render_region_text(region)

                if fallback:
                    page_parts.append(fallback)

                continue

            if (
                not preserve_hf
                and region.kind in (
                    RegionKind.HEADER,
                    RegionKind.FOOTER,
                )
            ):
                continue

            if (
                region.kind == RegionKind.TITLE
                and region.heading_level is not None
            ):
                prefix = "#" * region.heading_level

                title_text = " ".join(
                    line.text.strip()
                    for line in region.native_lines
                    if line.text.strip()
                )

                if title_text:
                    page_parts.append(
                        f"{prefix} {title_text}"
                    )

                continue

            body = _render_region_text(region)

            if body:
                page_parts.append(body)

        # Append only the physical tables from the current page.
        for table, rendered in rendered_tables:
            page_parts.append(
                _render_table_block(
                    table,
                    rendered,
                    page.page_index,
                )
            )

        sections.append(
            _render_page_section(
                page.page_index,
                page_parts,
            )
        )

    return "\n\n".join(sections).strip()


def _render_page_section(
    page_index: int,
    parts: list[str],
) -> str:
    """Render one page boundary and its Markdown contents."""
    heading = f"## Página {page_index + 1}"

    if not parts:
        return heading

    return heading + "\n\n" + "\n\n".join(parts)


def _render_region_text(
    region: LayoutRegion,
) -> str:
    """Render the text lines already reconstructed for a layout region."""
    return "\n".join(
        line.text.rstrip()
        for line in region.native_lines
        if line.text.strip()
    ).strip()


def _region_has_renderable_table(
    region: LayoutRegion,
    page_index: int,
    rendered_tables: list[
        tuple[StructuredTable, str]
    ],
) -> bool:
    """
    Return True when a successfully rendered table covers this TABLE region.

    This prevents duplicate output while ensuring that OCR text is not lost
    when table detection succeeds but table serialization produces no cells.
    """
    for table, _ in rendered_tables:
        for fragment in table.page_fragments:
            if fragment.page_index != page_index:
                continue

            if fragment.bbox is None:
                continue

            # Test containment in both directions because a layout TABLE
            # region and the detected table fragment are not guaranteed to
            # have exactly the same bounding box.
            if (
                region.bbox.overlap_ratio(fragment.bbox) >= 0.50
                or fragment.bbox.overlap_ratio(region.bbox) >= 0.50
            ):
                return True

    return False


def _render_table_block(
    table: StructuredTable,
    rendered: str,
    page_index: int,
) -> str:
    """Wrap one physical page table in its Markdown heading."""
    return (
        f"#### Tabela {table.table_id} "
        f"(página: {page_index + 1})\n\n"
        f"{rendered}"
    )


def _render_table(
    table: StructuredTable,
) -> str:
    if not table.cells or table.column_count <= 0:
        return ""

    row_count = max(
        table.row_count,
        max(cell.row for cell in table.cells) + 1,
    )

    rows = [
        ["" for _ in range(table.column_count)]
        for _ in range(row_count)
    ]

    for cell in table.cells:
        if (
            0 <= cell.row < row_count
            and 0 <= cell.col < table.column_count
        ):
            rows[cell.row][cell.col] = _escape_cell(
                cell.text
            )

    if not any(any(row) for row in rows):
        return ""

    header = rows[0]

    output = [
        "| " + " | ".join(header) + " |",
        "| "
        + " | ".join("---" for _ in header)
        + " |",
    ]

    output.extend(
        "| " + " | ".join(row) + " |"
        for row in rows[1:]
    )

    return "\n".join(output)


def _escape_cell(
    value: str,
) -> str:
    return (
        value
        .replace("|", "\\|")
        .replace("\r\n", "<br>")
        .replace("\n", "<br>")
    )