"""Document-level assembly orchestrator.

Combines per-page ``StructuredPage`` objects produced by the pipeline into a
single ``StructuredDocument``. Responsibilities:

- Optional cross-page table merging (``resolve_cross_page_tables_with_diagnostics``)
- Heading level assignment across the full document
- Repeated header/footer detection and suppression
- Per-page canonical content block assembly (``assemble_page_content``)
- Content conservation accounting and fallback block insertion
- Block reindexing after conservation adjustments
- Document-level text concatenation and diagnostic aggregation
"""
from __future__ import annotations

import dataclasses
from statistics import median
import time

from structured_pdf_text.document import (
    ContentKind,
    DocumentDiagnostics,
    DocumentMetadata,
    ExtractionStatus,
    PageStrategy,
    RegionKind,
    StructuredDocument,
    StructuredPage,
    StructuredTable,
)
from structured_pdf_text.assemble.content import assemble_page_content
from structured_pdf_text.assemble.content import _build_reading_text, _normalized_lines_text, _reindex_blocks
from structured_pdf_text.assemble.conservation import line_identity, record_content_conservation
from structured_pdf_text.assemble.repeated_regions import detect_repeated_headers_footers, repeated_line_keys
from structured_pdf_text.layout.heading import assign_heading_levels
from structured_pdf_text.geometry import BBox
from structured_pdf_text.tables.cross_page import resolve_cross_page_tables_with_diagnostics


def assemble_document(
    pages: list[StructuredPage],
    metadata: DocumentMetadata,
    merge_cross_page_tables: bool = False,
    preserve_headers_footers: bool = True,
    document_warnings: list[str] | None = None,
) -> StructuredDocument:
    """Assemble a final ``StructuredDocument`` from a list of structured pages.

    This is the top-level orchestration function. It performs the following
    steps in order:

    1. **Cross-page table merging** (when ``merge_cross_page_tables=True``):
       identifies tables that span a page boundary and merges them into a
       single logical ``StructuredTable`` with fragments on both pages.

    2. **Heading level assignment**: analyses font sizes and region kinds
       across all pages and assigns normalised H1–H6 levels.

    3. **Repeated header/footer detection**: finds text that recurs with
       stable geometry and typography across multiple pages.

    4. **Per-page content assembly**: calls ``assemble_page_content`` for each
       page, building the canonical ``PageContentBlock`` list.

    5. **Repeated region suppression** (when ``preserve_headers_footers=False``):
       marks confirmed furniture blocks as suppressed.

    6. **Content conservation**: runs ``record_content_conservation`` to ensure
       every accepted line is accounted for, inserting text fallback blocks for
       any unaccounted lines.

    7. **Block reindexing**: assigns final ``order_index`` and ``block_id``
       values after conservation adjustments.

    8. **Diagnostic aggregation**: merges per-page diagnostic facts into the
       ``DocumentDiagnostics`` object.

    Args:
        pages: Ordered list of ``StructuredPage`` objects produced by the
            pipeline. Page index values must be unique and stable.
        metadata: Document-level metadata (source path, PDFium version, etc.).
        merge_cross_page_tables: When ``True``, run the cross-page table
            resolution heuristic before content assembly.
        preserve_headers_footers: When ``True`` (default), confirmed repeated
            headers and footers are retained in the output. Set to ``False`` to
            suppress them.
        document_warnings: Optional list of pre-existing warning strings to
            include in the result diagnostics.

    Returns:
        A fully assembled ``StructuredDocument`` with ``reading_text``,
        ``raw_text``, content blocks on each page, and complete diagnostics.
    """
    tables: list[StructuredTable] = []
    for page in pages:
        tables.extend(page.tables)
    cross_page_decisions = []
    cross_page_start = time.perf_counter()
    if merge_cross_page_tables:
        resolution = resolve_cross_page_tables_with_diagnostics(
            tables,
            page_bboxes={page.page_index: page.bbox for page in pages},
            page_titles={
                page.page_index: _page_continuation_titles(page)
                for page in pages
            },
        )
        tables = list(resolution.tables)
        cross_page_decisions = [
            {
                "previous_table_id": decision.previous_table_id,
                "following_table_id": decision.following_table_id,
                "previous_page": decision.previous_page,
                "following_page": decision.following_page,
                "accepted": decision.accepted,
                "score": decision.score,
                "reasons": list(decision.reasons),
                "facts": decision.facts,
            }
            for decision in resolution.decisions
        ]
    cross_page_table_ms = (time.perf_counter() - cross_page_start) * 1000

    pages = assign_heading_levels(pages)
    repeated = detect_repeated_headers_footers(pages)

    # Build canonical content blocks for each page now that heading levels
    # and repeated header/footer detection are complete.
    assembled_pages: list[StructuredPage] = []
    for page in pages:
        result = assemble_page_content(page)
        blocks = list(result.blocks)
        if not preserve_headers_footers:
            blocks = _split_mixed_repeated_blocks(blocks, page, repeated)
        _apply_repeated_suppression(
            blocks,
            page,
            repeated,
            preserve_headers_footers,
        )
        blocks, conservation, records = record_content_conservation(
            page,
            blocks,
            canonical_line_order=result.canonical_line_order,
        )
        # VQ-20: propagate link URIs from LINK annotations to overlapping blocks.
        # Uses native_evidence annotations already extracted by pdfium_source.
        blocks = _propagate_link_uris(blocks, page)
        blocks = _mark_quoted_blocks(blocks, page)
        blocks = _mark_styled_runs(blocks, page)
        old_block_ids = {id(block): block.block_id for block in blocks}
        blocks = _reindex_blocks(blocks)
        owner_id_remap = {
            old_block_ids[id(block)]: block.block_id
            for block in blocks
            if id(block) in old_block_ids
        }
        records = tuple(
            dataclasses.replace(
                record,
                owner_id=owner_id_remap.get(record.owner_id, record.owner_id),
            )
            for record in records
        )
        if conservation.fallback_lines:
            page.diagnostics.warnings.append(
                f"unaccounted_content: {conservation.fallback_lines} line(s) recovered by text fallback"
            )
        if conservation.duplicate_assignment_count:
            page.diagnostics.warnings.append(
                "content_conservation_duplicate_assignment"
            )
        page_reading_text = _build_reading_text(blocks)

        page.diagnostics.facts.update(
            {
                # ``reading_line_preservation_ok`` is measured on the reading
                # order, before conservation; this is the final state after
                # fallback/deduplication, with the fallback count beside it.
                "reading_line_preservation_stage": "reading_order_before_conservation",
                "content_preservation_final_ok": (
                    conservation.unaccounted_lines == 0
                    and conservation.duplicate_assignment_count == 0
                ),
                "content_preservation_fallback_lines": conservation.fallback_lines,
                "content_block_count": len(blocks),
                "content_text_block_count": sum(
                    1 for b in blocks if b.kind not in (ContentKind.TABLE, ContentKind.FIGURE)
                ),
                "content_table_block_count": sum(1 for b in blocks if b.kind == ContentKind.TABLE),
                "content_table_fallbacks": result.table_fallbacks,
                "content_claimed_table_lines": result.claimed_table_lines,
                "content_deduplicated_lines": getattr(result, "deduplicated_lines", 0),
                "reading_deduplicated_lines": getattr(
                    getattr(result, "reading_decision", None),
                    "deduplicated_lines",
                    0,
                ),
                "content_orphan_tables": result.orphan_tables,
                "decorative_block_count": sum(1 for b in blocks if b.decorative),
                "list_segment_count": result.list_segment_count,
                "list_item_count": result.list_item_count,
                "list_inferred_marker_count": result.list_inferred_marker_count,
                "list_continuation_count": result.list_continuation_count,
                "list_unassigned_line_count": result.list_unassigned_line_count,
                "content_source_region_count": len(page.regions),
                "content_assembly_ms": round(result.assembly_ms, 3),
                "content_transformed_sources": [
                    {
                        "source_line_id": record.line_id,
                        "target_line_id": record.target_line_id,
                        "owner_id": record.owner_id,
                        "disposition": record.disposition.value,
                        "reason": record.reason,
                    }
                    for record in records
                    if record.reason == "script_merge"
                ],
                "content_block_order": [
                    {
                        "id": b.block_id,
                        "kind": b.kind.value,
                        "table_id": b.table_id,
                    }
                    for b in blocks
                ],
                **conservation.to_facts(),
            }
        )

        assembled_pages.append(
            dataclasses.replace(
                page,
                content_blocks=blocks,
                reading_text=page_reading_text,
            )
        )

    pages = assembled_pages

    raw_text = "\n\n".join(page.raw_text.strip() for page in pages if page.raw_text.strip())
    reading_text = "\n\n".join(page.reading_text for page in pages if page.reading_text.strip())

    warnings = list(document_warnings or [])
    warnings.extend(
        f"page {page.page_index + 1}: {warning}"
        for page in pages
        for warning in page.diagnostics.warnings
    )
    failed_pages = sum(bool(page.diagnostics.facts.get("failed")) for page in pages)
    partial_pages = any(page.diagnostics.facts.get("partial") for page in pages)
    if pages and failed_pages == len(pages):
        status = ExtractionStatus.FAILURE
    elif failed_pages or partial_pages:
        status = ExtractionStatus.PARTIAL_SUCCESS
    else:
        # Warning text can describe a recoverable or optional issue; it is
        # preserved in the report without automatically degrading the run.
        status = ExtractionStatus.SUCCESS
    native_pages = sum(1 for page in pages if page.diagnostics.strategy == PageStrategy.NATIVE)
    mixed_pages = sum(1 for page in pages if page.diagnostics.strategy == PageStrategy.MIXED)
    ocr_pages = sum(1 for page in pages if page.diagnostics.strategy == PageStrategy.OCR_CANDIDATE)
    return StructuredDocument(
        pages=pages,
        tables=tables,
        raw_text=raw_text,
        reading_text=reading_text,
        metadata=metadata,
        diagnostics=DocumentDiagnostics(
            status=status,
            page_count=len(pages),
            native_pages=native_pages,
            mixed_pages=mixed_pages,
            ocr_pages=ocr_pages,
            warnings=warnings,
            facts={
                "repeated_headers_footers": repeated,
                "preserve_headers_footers": preserve_headers_footers,
                "page_boundaries": [page.page_index for page in pages],
                "logical_table_count": len(tables),
                "cross_page_table_decisions": cross_page_decisions,
                "cross_page_table_ms": cross_page_table_ms,
            },
        ),
    )


def _page_continuation_titles(page: StructuredPage) -> tuple[str, ...]:
    """Collect title-like top-band text for cross-page decisions.

    The "top 22%" band is defined in visual reading order, not in raw canonical
    coordinates. For pages with a PDF /Rotate attribute the canonical axes are
    mapped to the visual axes before the threshold is computed.
    """
    rotation = page.page_rotation % 360

    candidates: list[str] = []
    for region in page.regions:
        for line in region.native_lines:
            text = line.text.strip()
            if not text or len(text) > 180:
                continue
            if region.kind == RegionKind.TITLE or _line_in_top_band(
                line.bbox, page.bbox, rotation
            ):
                candidates.append(text)
    return tuple(dict.fromkeys(candidates))


def _line_in_top_band(line_bbox: BBox, page_bbox: BBox, rotation: int) -> bool:
    """Return True if *line_bbox* falls within the visual top 22% of the page.

    ``rotation`` is the PDF /Rotate value (counterclockwise degrees: 0/90/180/270).
    All bboxes are in the canonical top-left coordinate system (y grows down).
    The mapping from canonical axes to visual axes depends on the rotation:

    * 0°   : visual-top  = canonical small-y  (default, y0 ≤ limit)
    * 90°  : visual-top  = canonical small-x  (x0 ≤ limit)
    * 180° : visual-top  = canonical large-y  (y1 ≥ limit)
    * 270° : visual-top  = canonical large-x  (x1 ≥ limit)
    """
    fraction = 0.22
    if rotation == 90:
        limit = page_bbox.x0 + page_bbox.width * fraction
        return line_bbox.x0 <= limit
    if rotation == 180:
        limit = page_bbox.y1 - page_bbox.height * fraction
        return line_bbox.y1 >= limit
    if rotation == 270:
        limit = page_bbox.x1 - page_bbox.width * fraction
        return line_bbox.x1 >= limit
    # 0° or unknown: canonical top-left, y grows down
    limit = page_bbox.y0 + page_bbox.height * fraction
    return line_bbox.y0 <= limit


def _split_mixed_repeated_blocks(
    blocks: list,
    page: StructuredPage,
    repeated: dict[str, list[int]],
) -> list:
    """Separate repeated edge lines from body lines before block suppression.

    Content assembly may group a running header with the first body line.
    Splitting only confirmed line-level furniture lets the header be omitted
    without discarding the body text or weakening conservation accounting.
    """
    if not repeated:
        return blocks

    keys_by_line = repeated_line_keys(page)
    repeated_ids = {
        line_id
        for line_id, key in keys_by_line.items()
        if page.page_index in repeated.get(key, ())
    }
    if not repeated_ids:
        return blocks

    lines_by_id = {
        line_identity(line): line
        for region in page.regions
        for line in [*region.native_lines, *region.ocr_lines]
    }
    splittable = {
        ContentKind.TEXT, ContentKind.TITLE, ContentKind.CAPTION,
        ContentKind.HEADER, ContentKind.FOOTER, ContentKind.FOOTNOTE,
        ContentKind.MARGINALIA, ContentKind.UNKNOWN,
    }
    result: list = []
    for block in blocks:
        if block.kind not in splittable or len(block.line_ids) < 2:
            result.append(block)
            continue
        statuses = [line_id in repeated_ids for line_id in block.line_ids]
        if all(status == statuses[0] for status in statuses):
            result.append(block)
            continue

        runs: list[tuple[bool, list[str]]] = []
        for line_id, is_repeated in zip(block.line_ids, statuses):
            if not runs or runs[-1][0] != is_repeated:
                runs.append((is_repeated, [line_id]))
            else:
                runs[-1][1].append(line_id)
        for run_index, (_, line_ids) in enumerate(runs, start=1):
            source_lines = [lines_by_id[line_id] for line_id in line_ids if line_id in lines_by_id]
            text = _normalized_lines_text(source_lines)
            if not source_lines or not text:
                continue
            segment = dataclasses.replace(
                block,
                block_id=f"{block.block_id}:repeat-split-{run_index}",
                bbox=BBox.union_all([line.bbox for line in source_lines]),
                text=text,
                line_ids=list(line_ids),
                list_items=[],
                suppressed=False,
                suppression_reason=None,
            )
            result.append(segment)
    return result


def _apply_repeated_suppression(
    blocks: list,
    page: StructuredPage,
    repeated: dict[str, list[int]],
    preserve_headers_footers: bool,
) -> None:
    """Suppress only blocks wholly owned by confirmed repeated furniture.

    A block may contain both an edge candidate and unique semantic content.
    In that case the safe behaviour is to preserve the complete block rather
    than allowing one repeated line to suppress unrelated lines.

    Partial suppression can be introduced later only with explicit line-level
    block splitting and conservation accounting.
    """
    if preserve_headers_footers or not repeated:
        return

    line_keys = repeated_line_keys(page)
    repeated_keys = set(repeated)

    repeated_role_by_line_id: dict[str, str] = {}

    for region in page.regions:
        for line in [*region.native_lines, *region.ocr_lines]:
            line_id = line.line_id or f"line:{id(line)}"
            key = line_keys.get(line_id)

            if key not in repeated_keys:
                continue
            if page.page_index not in repeated[key]:
                continue

            if key.startswith("header:"):
                reason = "repeated_header"
            elif key.startswith("footer:"):
                reason = "repeated_footer"
            else:
                continue

            previous = repeated_role_by_line_id.get(line_id)

            # If the same source line is ambiguously classified as two
            # different furniture roles, preserving it is safer than
            # suppressing it.
            if previous is not None and previous != reason:
                repeated_role_by_line_id[line_id] = "ambiguous"
            else:
                repeated_role_by_line_id[line_id] = reason

    for block in blocks:
        block_line_ids = {
            line_id
            for line_id in block.line_ids
            if line_id
        }

        if not block_line_ids:
            continue

        roles = {
            repeated_role_by_line_id.get(line_id)
            for line_id in block_line_ids
        }

        # Every line owned by this block must be confirmed as repeated
        # furniture. A partial match must never suppress unique content.
        if None in roles:
            continue

        # Mixed or ambiguous header/footer ownership is also preserved.
        if len(roles) != 1:
            continue

        reason = next(iter(roles))

        if reason == "ambiguous":
            continue

        block.suppressed = True
        block.suppression_reason = reason


def _propagate_link_uris(
    blocks: list,
    page: StructuredPage,
) -> list:
    """VQ-20: attach LINK annotation URIs to the blocks that show their anchors.

    A block lying (almost) entirely inside the annotation becomes a link as a
    whole. Otherwise each annotation is matched against the lines of the
    block: the
    glyphs whose centers lie inside the rectangle form the anchor text, so a
    link covering one line (or part of one) of a paragraph keeps its own label.
    Only PARAGRAPH/TITLE/FIGURE/LIST-like blocks are considered; TABLE blocks
    are left untouched.
    """
    links = [(link.bbox, link.uri) for link in page.links]
    if not links and page.native_evidence is not None:
        links = [
            (annot.bbox, annot.link_uri)
            for annot in page.native_evidence.objects.annotations
            if annot.subtype == "link" and annot.link_uri and annot.bbox is not None
        ]
    if not links:
        return blocks

    lines_by_id = {
        line.line_id: line
        for region in page.regions
        for line in (*region.native_lines, *region.ocr_lines)
        if line.line_id
    }
    result: list = []
    for block in blocks:
        if block.suppressed or block.kind == ContentKind.TABLE:
            result.append(block)
            continue
        uri = next((
            target for box, target in links
            if block.bbox.overlap_ratio(box.expand(1.0)) >= 0.8
        ), None)
        if uri is not None:
            result.append(dataclasses.replace(block, link_uri=uri))
            continue
        anchors: list[tuple[str, str]] = []
        block_text = " ".join(block.text.split())
        for box, target in links:
            for line_id in block.line_ids:
                line = lines_by_id.get(line_id)
                if line is None or line.bbox.overlap_ratio(box) <= 0.0:
                    continue
                anchor = _link_anchor_text(line, box)
                if anchor and anchor in block_text and (anchor, target) not in anchors:
                    anchors.append((anchor, target))
        result.append(dataclasses.replace(block, link_anchors=anchors) if anchors else block)
    return result


def _mark_quoted_blocks(blocks: list, page: StructuredPage) -> list:
    """Mark prose set off by a quote bar or by a frame drawn around it alone.

    A quote bar is a thin vertical rule just left of the paragraph spanning
    most of its height. A frame must enclose the paragraph closely and hold no
    other content block. Lists, tables, headings and verbatim blocks are
    never marked.
    """
    if not page.rule_boxes:
        return blocks
    rules = page.rule_boxes

    def isolated(rule: BBox) -> bool:
        # Table grids repeat rules: parallel bars over the same rows, or
        # frames touching their neighbour cells. A quote mark stands alone.
        if rule.width <= 8.0:
            return not any(
                other is not rule and other.width <= 8.0
                and min(other.y1, rule.y1) - max(other.y0, rule.y0) >= 0.8 * min(other.height, rule.height)
                for other in rules
            )
        return not any(other is not rule and other.intersection(rule.expand(2.0)) is not None for other in rules)

    visible = [block for block in blocks if not block.suppressed]
    result: list = []
    for block in blocks:
        box = block.bbox
        # Native prose over a background image is emitted as a FIGURE block.
        prose = block.kind == ContentKind.TEXT or (
            block.kind == ContentKind.FIGURE and block.line_ids
            and all(line_id.startswith("native:") for line_id in block.line_ids)
        )
        if (block.suppressed or not prose or block.preformatted
                or box.height <= 0 or not block.text.strip()):
            result.append(block)
            continue
        quoted = False
        for rule in rules:
            if not isolated(rule):
                continue
            vertical = min(rule.y1, box.y1) - max(rule.y0, box.y0)
            if (rule.width <= 8.0 and rule.x1 <= box.x0 + 1.0 and box.x0 - rule.x1 <= 24.0
                    and vertical >= 0.8 * box.height and rule.height <= box.height * 3.0 + 24.0):
                quoted = True
                break
            frame = rule.expand(1.0)
            if (rule.width > 8.0
                    and frame.x0 <= box.x0 and frame.y0 <= box.y0 and box.x1 <= frame.x1 and box.y1 <= frame.y1
                    and box.x0 - rule.x0 <= 24.0 and box.y0 - rule.y0 <= 20.0 and rule.y1 - box.y1 <= 20.0
                    and not any(
                        other is not block and rule.x0 <= other.bbox.cx <= rule.x1
                        and rule.y0 <= other.bbox.cy <= rule.y1
                        for other in visible
                    )):
                quoted = True
                break
        result.append(dataclasses.replace(block, blockquote=True) if quoted else block)
    return result


def _token_style(token, strokes: list[BBox], top: float, baseline: float, links: list[BBox]) -> str:
    """Emphasis of one glyph run; strokes are judged against its line.

    *top*/*baseline* are the line's typical glyph top and bottom, so letters
    with descenders or accents do not move the reference. Underline belongs
    to a stroke at the baseline, strike-through to one through the middle.
    A link's underline is the hyperlink's own decoration, not emphasis.
    """
    name = (token.font_name or "").casefold()
    style = ""
    if (token.font_weight is not None and token.font_weight >= 600) or any(
        key in name for key in ("bold", "heavy", "black", "semibold", "demi")
    ):
        style += "b"
    if any(key in name for key in ("italic", "oblique", "slanted")):
        style += "i"
    box = token.bbox
    height = max(baseline - top, 1.0)
    in_link = any(link.x0 <= box.cx <= link.x1 and link.y0 <= box.cy <= link.y1 for link in links)
    for stroke in strokes:
        covered = min(stroke.x1, box.x1) - max(stroke.x0, box.x0)
        if covered < 0.5 * box.width:
            continue
        position = (stroke.cy - top) / height
        if 0.9 <= position <= 1.45 and not in_link and "u" not in style:
            style += "u"
        elif 0.3 <= position <= 0.8 and "s" not in style:
            style += "s"
    return style


def _mark_styled_runs(blocks: list, page: StructuredPage) -> list:
    """Attach mixed emphasis runs from native font and stroke evidence.

    Bold/italic come from the font of each glyph run; underline and
    strike-through from a thin stroke under or through it. Runs are used only
    when the block mixes styles and their text reproduces the block text, so
    a style can never change or reorder the published words.
    """
    lines_by_id = {
        line.line_id: line
        for region in page.regions
        for line in region.native_lines
        if line.line_id
    }
    link_boxes = [link.bbox for link in page.links]
    result: list = []
    for block in blocks:
        if (block.suppressed or block.preformatted or block.kind not in {
                ContentKind.TEXT, ContentKind.CAPTION, ContentKind.FOOTNOTE} or not block.line_ids):
            result.append(block)
            continue
        runs: list[tuple[str, str]] = []
        lines = [lines_by_id.get(line_id) for line_id in block.line_ids]
        if any(line is None for line in lines):
            result.append(block)
            continue
        for index, line in enumerate(lines):
            if index:
                runs.append(("\n", ""))
            glyphs = [token for token in line.tokens if token.text.strip()]
            top = median([token.bbox.y0 for token in glyphs]) if glyphs else line.bbox.y0
            baseline = median([token.bbox.y1 for token in glyphs]) if glyphs else line.bbox.y1
            for token in line.tokens:
                style = (_token_style(token, page.stroke_boxes, top, baseline, link_boxes)
                         if token.text.strip() else None)
                if style is None:
                    style = runs[-1][1] if runs and runs[-1][0] != "\n" else ""
                if runs and runs[-1][1] == style and runs[-1][0] != "\n":
                    runs[-1] = (runs[-1][0] + token.text, style)
                else:
                    runs.append((token.text, style))
        styles = {style for text, style in runs if text.strip()}
        if len(styles) < 2 or "".join("".join(text for text, _ in runs).split()) != "".join(block.text.split()):
            result.append(block)
            continue
        result.append(dataclasses.replace(block, styled_runs=runs, inline_style=None))
    return result


def _link_anchor_text(line, box: BBox) -> str:
    inside = [
        token for token in line.tokens
        if token.text.strip()
        and box.x0 - 1.0 <= token.bbox.cx <= box.x1 + 1.0
        and box.y0 - 1.0 <= token.bbox.cy <= box.y1 + 1.0
    ]
    if not inside:
        return ""
    visible = [token for token in line.tokens if token.text.strip()]
    if len(inside) == len(visible):
        return " ".join(line.text.split())
    # Keep the line's own spacing: slice its text between the first and last
    # inside glyph, located by the visible-glyph order.
    first, last = visible.index(inside[0]), visible.index(inside[-1])
    if last - first + 1 != len(inside):
        return ""
    text = line.text
    positions = [index for index, char in enumerate(text) if not char.isspace()]
    glyphs = [char for token in visible for char in token.text if not char.isspace()]
    if len(positions) != len(glyphs):
        return ""
    offsets = [0]
    for token in visible:
        offsets.append(offsets[-1] + sum(1 for char in token.text if not char.isspace()))
    start, stop = offsets[first], offsets[last + 1]
    return " ".join(text[positions[start]:positions[stop - 1] + 1].split())
