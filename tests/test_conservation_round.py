from __future__ import annotations

from types import SimpleNamespace

from structured_pdf_text.assemble.conservation import record_content_conservation
from structured_pdf_text.assemble.document import assemble_document
from structured_pdf_text.document import (
    Baseline,
    ContentKind,
    ContentDisposition,
    DocumentDiagnostics,
    DocumentMetadata,
    EvidenceRef,
    ExtractionStatus,
    LayoutRegion,
    NativeCharacter,
    OcrToken,
    PageDiagnostics,
    PageContentBlock,
    PageStrategy,
    RegionDecision,
    RegionKind,
    RegionQuality,
    SourceKind,
    StructuredPage,
    StructuredDocument,
    StructuredTable,
    TableCell,
    TableFragment,
    TableMethod,
    TextLine,
    TextToken,
    WritingDirection,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.paddle import _make_candidate, _select_best_candidate
from structured_pdf_text.ocr.quality import OcrQualityThresholds
from structured_pdf_text.tables.validation import validate_table_geometry
from structured_pdf_text.renderers.markdown import render_markdown
from structured_pdf_text.assemble.content import assemble_page_content
from structured_pdf_text.text.line_detector import reconstruct_native_lines
from structured_pdf_text.text.lists import segment_list_lines


def _line(text: str, bbox: BBox, line_id: str) -> TextLine:
    token = TextToken(
        text=text,
        bbox=bbox,
        sources=[EvidenceRef(SourceKind.NATIVE_PDF, 0, line_id)],
        confidence=1.0,
        normalized_text=text,
    )
    return TextLine(
        tokens=[token],
        bbox=bbox,
        baseline=Baseline(y=bbox.y1),
        direction=WritingDirection.LEFT_TO_RIGHT,
        native_order_min=None,
        native_order_max=None,
        line_id=line_id,
    )


def _page(index: int, regions: list[LayoutRegion]) -> StructuredPage:
    return StructuredPage(
        page_index=index,
        bbox=BBox(0, 0, 600, 800),
        regions=regions,
        tables=[],
        raw_text="",
        reading_text="",
        diagnostics=PageDiagnostics(
            page_index=index,
            strategy=PageStrategy.NATIVE,
            reasons=[],
            native_chars=0,
            native_text_length=0,
        ),
    )


def _region(kind: RegionKind, text: str, bbox: BBox, line_id: str) -> LayoutRegion:
    return LayoutRegion(
        region_id=f"region-{line_id}",
        kind=kind,
        bbox=bbox,
        layout_confidence=1.0,
        native_lines=[_line(text, bbox, line_id)],
        ocr_tokens=[],
        quality=RegionQuality(RegionDecision.KEEP_NATIVE),
    )


def test_unique_edge_content_is_preserved_when_suppression_is_enabled() -> None:
    pages = [
        _page(0, [_region(RegionKind.HEADER, "Relatório Cliente A", BBox(0, 0, 220, 20), "p0-header")]),
        _page(1, [_region(RegionKind.HEADER, "Relatório Cliente B", BBox(0, 0, 220, 20), "p1-header")]),
    ]

    document = assemble_document(
        pages,
        metadata=type("Metadata", (), {"source_path": "test.pdf", "page_count": 2, "pdfium_version": None})(),
        preserve_headers_footers=False,
    )

    assert "Relatório Cliente A" in document.reading_text
    assert "Relatório Cliente B" in document.reading_text
    assert all(not block.suppressed for page in document.pages for block in page.content_blocks)


def test_confirmed_repeated_header_is_explicitly_suppressed() -> None:
    pages = [
        _page(0, [_region(RegionKind.HEADER, "Documento interno", BBox(0, 0, 220, 20), "p0-header")]),
        _page(1, [_region(RegionKind.HEADER, "Documento interno", BBox(0, 0, 220, 20), "p1-header")]),
    ]

    document = assemble_document(
        pages,
        metadata=type("Metadata", (), {"source_path": "test.pdf", "page_count": 2, "pdfium_version": None})(),
        preserve_headers_footers=False,
    )

    assert "Documento interno" not in document.reading_text
    assert document.pages[0].content_blocks[0].suppression_reason == "repeated_header"
    assert document.pages[0].diagnostics.facts["content_suppressed_repeated_header_lines"] == 1


def test_unaccounted_line_gets_auditable_text_fallback() -> None:
    line = _line("conteúdo preservado", BBox(10, 20, 180, 35), "fallback-line")
    page = _page(0, [_region(RegionKind.TEXT, line.text, line.bbox, line.line_id or "fallback-line")])
    blocks, summary, records = record_content_conservation(page, [])

    assert summary.unaccounted_lines == 0
    assert summary.fallback_lines == 1
    assert blocks[-1].text == "conteúdo preservado"
    assert records[-1].reason == "unaccounted_content_fallback"


def test_ocr_candidate_with_more_spatial_coverage_wins_small_quality_tie() -> None:
    page_bbox = BBox(0, 0, 200, 100)
    thresholds = OcrQualityThresholds()
    high_quality_short = _make_candidate(
        "short",
        [
            # The missing second line is intentional.
            OcrToken("A", BBox(10, 10, 60, 20), 0.94, "pt", SourceKind.OCR_PAGE)
        ],
        None,
        page_bbox,
        thresholds,
    )
    fuller = _make_candidate(
        "full",
        [
            OcrToken("A", BBox(10, 10, 60, 20), 0.93, "pt", SourceKind.OCR_PAGE),
            OcrToken("B", BBox(10, 40, 60, 50), 0.93, "pt", SourceKind.OCR_PAGE),
        ],
        None,
        page_bbox,
        thresholds,
    )

    selected = _select_best_candidate([high_quality_short, fuller], page_bbox, thresholds)

    assert selected.name == "full"
    assert fuller.line_cluster_count > high_quality_short.line_cluster_count


def test_table_source_geometry_rejects_reversed_row_assignment() -> None:
    upper = BBox(10, 10, 100, 30)
    lower = BBox(10, 50, 100, 70)
    table = StructuredTable(
        table_id="reversed-source",
        page_fragments=[TableFragment(0, BBox(10, 10, 200, 70), 0, 1)],
        cells=[
            TableCell(0, 0, 1, 1, lower, "baixo", [] , 1.0),
            TableCell(1, 0, 1, 1, upper, "cima", [] , 1.0),
        ],
        column_count=1,
        row_count=2,
        confidence=0.9,
        method=TableMethod.TEXT_TRACKS,
    )
    source_lines = [
        _line("cima", upper, "source-upper"),
        _line("baixo", lower, "source-lower"),
    ]

    result = validate_table_geometry(table, source_lines=source_lines)

    assert not result.valid
    assert result.source_row_assignment_monotonicity < 1.0
    assert "source_row_assignment_not_monotonic" in result.reasons


def test_repeated_detection_does_not_generalize_arbitrary_numeric_identifiers() -> None:
    pages = [
        _page(
            0,
            [
                _region(
                    RegionKind.HEADER,
                    "Invoice 12345",
                    BBox(0, 0, 220, 20),
                    "p0-header-id",
                )
            ],
        ),
        _page(
            1,
            [
                _region(
                    RegionKind.HEADER,
                    "Invoice 98765",
                    BBox(0, 0, 220, 20),
                    "p1-header-id",
                )
            ],
        ),
    ]

    document = assemble_document(
        pages,
        metadata=type(
            "Metadata",
            (),
            {
                "source_path": "test.pdf",
                "page_count": 2,
                "pdfium_version": None,
            },
        )(),
        preserve_headers_footers=False,
    )

    # Stable position and style alone are not permission to suppress semantic
    # content. The changing identifiers must remain part of the signature.
    assert "Invoice 12345" in document.reading_text
    assert "Invoice 98765" in document.reading_text

    assert all(
        page.diagnostics.facts[
            "content_suppressed_repeated_header_lines"
        ] == 0
        for page in document.pages
    )


def _block(
    block_id: str,
    line_ids: list[str],
    lines: list[TextLine],
    kind: ContentKind = ContentKind.TEXT,
    page_index: int = 0,
) -> "PageContentBlock":
    from structured_pdf_text.document import PageContentBlock
    from structured_pdf_text.text.normalize import normalize_reading_text

    bboxes = [ln.bbox for ln in lines]
    bbox = BBox.union_all(bboxes) if bboxes else BBox(0, 0, 1, 1)
    text = "\n".join(
        t for t in (normalize_reading_text(ln.text).strip() for ln in lines) if t
    )
    return PageContentBlock(
        block_id=block_id,
        page_index=page_index,
        kind=kind,
        bbox=bbox,
        order_index=0,
        text=text,
        line_ids=list(line_ids),
    )


def _page_with_lines(*lines: TextLine, page_index: int = 0) -> StructuredPage:
    """Page with a single TEXT region containing the given lines in order."""
    region = LayoutRegion(
        region_id="region-main",
        kind=RegionKind.TEXT,
        bbox=BBox(0, 0, 600, 800),
        layout_confidence=1.0,
        native_lines=list(lines),
        ocr_tokens=[],
        quality=RegionQuality(RegionDecision.KEEP_NATIVE),
    )
    return StructuredPage(
        page_index=page_index,
        bbox=BBox(0, 0, 600, 800),
        regions=[region],
        tables=[],
        raw_text="",
        reading_text="",
        diagnostics=PageDiagnostics(
            page_index=page_index,
            strategy=PageStrategy.NATIVE,
            reasons=[],
            native_chars=0,
            native_text_length=0,
        ),
    )


# ── P0-1: exactly-once ownership ──────────────────────────────────────────────

def test_duplicate_claim_single_line_resolves_to_one_owner() -> None:
    """Two blocks claim the same line; second block is suppressed."""
    ln = _line("conteúdo único", BBox(0, 10, 200, 25), "line-1")
    page = _page_with_lines(ln)
    block_a = _block("page-1:block-1", ["line-1"], [ln])
    block_b = _block("page-1:block-2", ["line-1"], [ln])

    blocks, summary, records = record_content_conservation(page, [block_a, block_b])

    # line-1 must appear in exactly one non-suppressed block.
    owners = [b for b in blocks if "line-1" in b.line_ids and not b.suppressed]
    assert len(owners) == 1
    assert owners[0].block_id == "page-1:block-1"

    # No structural duplicates remain after resolution.
    assert summary.duplicate_assignment_count == 0
    assert summary.duplicate_claims_detected == 1
    assert summary.duplicate_claims_resolved == 1


def test_mixed_block_preserves_unique_lines_after_dedup() -> None:
    """Block losing a duplicate still keeps its unique lines."""
    ln1 = _line("compartilhada", BBox(0, 10, 200, 25), "line-1")
    ln2 = _line("exclusiva do B", BBox(0, 30, 200, 45), "line-2")
    page = _page_with_lines(ln1, ln2)
    block_a = _block("page-1:block-1", ["line-1"], [ln1])
    block_b = _block("page-1:block-2", ["line-1", "line-2"], [ln1, ln2])

    blocks, summary, _ = record_content_conservation(page, [block_a, block_b])

    # line-2 must survive in block_b.
    rebuilt_b = next(b for b in blocks if b.block_id == "page-1:block-2")
    assert "line-2" in rebuilt_b.line_ids
    assert "line-1" not in rebuilt_b.line_ids
    assert not rebuilt_b.suppressed
    assert "exclusiva do B" in rebuilt_b.text

    # line-1 stays only in block_a.
    rebuilt_a = next(b for b in blocks if b.block_id == "page-1:block-1")
    assert "line-1" in rebuilt_a.line_ids

    assert summary.duplicate_assignment_count == 0
    assert summary.duplicate_claims_detected == 1


def test_table_block_wins_prose_conflict() -> None:
    """A TABLE keeps ownership even when prose appears first."""

    ln = _line("dado da tabela", BBox(0, 10, 200, 25), "line-1")
    page = _page_with_lines(ln)
    table = StructuredTable(
        table_id="table-1",
        page_fragments=[TableFragment(0, BBox(0, 10, 200, 25), 0, 0)],
        cells=[TableCell(0, 0, 1, 1, ln.bbox, "dado da tabela", [], 1.0)],
        column_count=1,
        row_count=1,
        confidence=1.0,
        method=TableMethod.STRICT_GRID,
    )
    page.tables = [table]
    table_block = PageContentBlock(
        block_id="page-1:table-1",
        page_index=0,
        kind=ContentKind.TABLE,
        bbox=ln.bbox,
        order_index=0,
        table_id="table-1",
        line_ids=["line-1"],
    )
    prose_block = _block("page-1:block-2", ["line-1"], [ln])

    blocks, summary, records = record_content_conservation(page, [prose_block, table_block])

    table_record = next(r for r in records if r.line_id == "line-1")
    assert table_record.disposition.value == "table_owned"

    prose = next(b for b in blocks if b.block_id == "page-1:block-2")
    assert "line-1" not in prose.line_ids

    assert summary.duplicate_assignment_count == 0
    assert summary.duplicate_claims_detected == summary.duplicate_claims_resolved == 1

    page.content_blocks = blocks
    document = StructuredDocument(
        pages=[page],
        tables=[table],
        raw_text="",
        reading_text="",
        metadata=DocumentMetadata("test.pdf", 1, None),
        diagnostics=DocumentDiagnostics(
            status=ExtractionStatus.SUCCESS,
            page_count=1,
            native_pages=1,
            mixed_pages=0,
            ocr_pages=0,
        ),
    )
    markdown = render_markdown(document)
    assert markdown.count("dado da tabela") == 1


def test_figure_block_wins_prose_conflict_without_markdown_duplication() -> None:
    line = _line("rótulo da figura", BBox(20, 20, 160, 35), "figure-line")
    page = _page_with_lines(line)
    figure = PageContentBlock(
        block_id="page-1:figure-1",
        page_index=0,
        kind=ContentKind.FIGURE,
        bbox=line.bbox,
        order_index=0,
        text=line.text,
        line_ids=[line.line_id or "figure-line"],
    )
    prose = _block("page-1:block-2", ["figure-line"], [line])

    blocks, summary, records = record_content_conservation(page, [prose, figure])

    assert next(r for r in records if r.line_id == "figure-line").disposition == ContentDisposition.FIGURE_OWNED
    assert next(b for b in blocks if b.block_id == "page-1:block-2").line_ids == []
    assert summary.duplicate_claims_detected == summary.duplicate_claims_resolved == 1

    page.content_blocks = blocks
    document = StructuredDocument(
        pages=[page],
        tables=[],
        raw_text="",
        reading_text="",
        metadata=DocumentMetadata("test.pdf", 1, None),
        diagnostics=DocumentDiagnostics(
            status=ExtractionStatus.SUCCESS,
            page_count=1,
            native_pages=1,
            mixed_pages=0,
            ocr_pages=0,
        ),
    )
    assert render_markdown(document).count("rótulo da figura") == 1


def test_lane_ids_canonicalize_without_double_ownership() -> None:
    """lane-1 and lane-2 sub-lines of line-5 must not each grant ownership."""
    ln = _line("texto com lanes", BBox(0, 10, 200, 25), "line-5")
    page = _page_with_lines(ln)
    block_a = _block("page-1:block-1", ["line-5:lane-1"], [ln])
    block_b = _block("page-1:block-2", ["line-5:lane-2"], [ln])

    blocks, summary, _ = record_content_conservation(page, [block_a, block_b])

    owners = [b for b in blocks if not b.suppressed and any("line-5" in lid for lid in b.line_ids)]
    assert len(owners) == 1
    assert summary.duplicate_claims_detected == 1
    assert summary.duplicate_assignment_count == 0


# ── P0-2: fallback at correct reading position ────────────────────────────────

def test_fallback_inserted_between_surrounding_blocks() -> None:
    """An unaccounted line must appear between the blocks that flank it."""
    ln_a = _line("bloco A", BBox(0, 10, 200, 25), "line-a")
    ln_u = _line("não reclamada", BBox(0, 30, 200, 45), "line-unaccounted")
    ln_b = _line("bloco B", BBox(0, 50, 200, 65), "line-b")
    page = _page_with_lines(ln_a, ln_u, ln_b)

    block_a = _block("page-1:block-1", ["line-a"], [ln_a])
    block_b = _block("page-1:block-2", ["line-b"], [ln_b])

    blocks, summary, _ = record_content_conservation(page, [block_a, block_b])

    ids = [b.block_id for b in blocks]
    a_pos = ids.index("page-1:block-1")
    b_pos = ids.index("page-1:block-2")
    fallback_pos = next(i for i, b in enumerate(blocks) if "conservation-fallback" in b.block_id)

    assert a_pos < fallback_pos < b_pos, (
        f"Expected order A({a_pos}) < fallback({fallback_pos}) < B({b_pos})"
    )
    assert summary.fallback_lines == 1


def test_fallback_after_all_blocks_when_line_is_last() -> None:
    """A trailing unaccounted line is placed after all existing blocks."""
    ln_a = _line("bloco A", BBox(0, 10, 200, 25), "line-a")
    ln_u = _line("última linha", BBox(0, 50, 200, 65), "line-last")
    page = _page_with_lines(ln_a, ln_u)
    block_a = _block("page-1:block-1", ["line-a"], [ln_a])

    blocks, _, _ = record_content_conservation(page, [block_a])

    assert blocks[-1].block_id.endswith("conservation-fallback-1")
    assert blocks[0].block_id == "page-1:block-1"


# ── P0-3: truthful unaccounted_lines ──────────────────────────────────────────

def test_unaccounted_lines_is_zero_when_all_lines_are_covered() -> None:
    """After full fallback recovery, unaccounted_lines must be 0."""
    ln = _line("texto completo", BBox(0, 10, 200, 25), "line-full")
    page = _page_with_lines(ln)

    _, summary, _ = record_content_conservation(page, [])

    assert summary.unaccounted_lines == 0
    assert summary.fallback_lines == 1


def test_new_diagnostic_keys_are_present_in_facts() -> None:
    """to_facts() must expose the two new duplicate-claims counters."""
    ln = _line("qualquer coisa", BBox(0, 10, 200, 25), "line-x")
    page = _page_with_lines(ln)
    block = _block("page-1:block-1", ["line-x"], [ln])

    _, summary, _ = record_content_conservation(page, [block])

    facts = summary.to_facts()
    assert "content_duplicate_claims_detected" in facts
    assert "content_duplicate_claims_resolved" in facts
    assert facts["content_duplicate_claims_detected"] == 0
    assert facts["content_duplicate_claims_resolved"] == 0


def test_script_merge_source_appears_deduplicated_in_ledger() -> None:
    """A line consumed by _merge_script_lines appears as DEDUPLICATED in the ledger."""
    from dataclasses import replace as dc_replace
    # Two lines: body (owner) and a script source that was merged into it.
    body_line = _line("E²", BBox(0, 0, 40, 12), "body-line")
    script_source = _line("2", BBox(4, -4, 10, 2), "script-source")

    # Simulate the result of _merge_script_lines: body now carries the source ID,
    # and script_source no longer exists as a standalone accepted line.
    merged_body = dc_replace(body_line, merged_source_line_ids=("script-source",))

    # Only the merged body is accepted (script_source was removed from output).
    region = LayoutRegion(
        region_id="region-main",
        kind=RegionKind.TEXT,
        bbox=BBox(0, 0, 600, 800),
        layout_confidence=1.0,
        native_lines=[merged_body, script_source],
        ocr_tokens=[],
        quality=RegionQuality(RegionDecision.KEEP_NATIVE),
    )
    page = StructuredPage(
        page_index=0,
        bbox=BBox(0, 0, 600, 800),
        regions=[region],
        tables=[],
        raw_text="",
        reading_text="",
        diagnostics=PageDiagnostics(
            page_index=0,
            strategy=PageStrategy.NATIVE,
            reasons=[],
            native_chars=0,
            native_text_length=0,
        ),
    )
    block = _block("page-1:block-1", ["body-line"], [merged_body])
    _, _, records = record_content_conservation(page, [block])

    record_map = {r.line_id: r for r in records}
    assert "script-source" in record_map, "merged source must appear in ledger"
    assert record_map["script-source"].disposition == ContentDisposition.DEDUPLICATED
    assert record_map["script-source"].reason == "script_merge"
    assert record_map["script-source"].target_line_id == "body-line"
    assert record_map["script-source"].owner_id == "page-1:block-1"


def test_real_script_merge_source_is_audited_without_becoming_visible_or_fallback() -> None:
    characters = tuple(
        NativeCharacter(
            page_index=0,
            char_index=index,
            text=text,
            unicode_codepoint=ord(text),
            bbox=bbox,
            font_size=10.0,
        )
        for index, (text, bbox) in enumerate(
            (
                ("E", BBox(0, 0, 8, 12)),
                ("2", BBox(4, -4, 8, 2)),
            )
        )
    )
    merged_lines = reconstruct_native_lines(characters)
    assert len(merged_lines) == 1
    target_line_id = merged_lines[0].line_id
    assert target_line_id is not None
    source_line_id = "native:0:1:1"
    assert source_line_id in merged_lines[0].merged_source_line_ids

    page = _page(0, [_region(RegionKind.TEXT, merged_lines[0].text, merged_lines[0].bbox, target_line_id)])
    page.regions[0].native_lines = merged_lines
    document = assemble_document(
        [page],
        metadata=DocumentMetadata("test.pdf", 1, None),
    )

    transformed = document.pages[0].diagnostics.facts["content_transformed_sources"]
    source_fact = next(item for item in transformed if item["source_line_id"] == source_line_id)
    assert source_fact["target_line_id"] == target_line_id
    assert source_fact["disposition"] == "deduplicated"
    assert source_fact["reason"] == "script_merge"
    assert source_fact["owner_id"] is not None
    assert document.pages[0].diagnostics.facts["content_deduplicated_lines"] == 1
    assert document.pages[0].diagnostics.facts["content_fallback_lines"] == 0
    assert document.reading_text.count("E²") == 1
    assert source_line_id not in [
        line_id
        for block in document.pages[0].content_blocks
        for line_id in block.line_ids
    ]


def test_transformed_source_owner_id_points_to_final_reindexed_target(monkeypatch) -> None:
    before = _line("antes", BBox(0, 0, 40, 12), "before-line")
    target = _line("E²", BBox(0, 20, 40, 32), "target-line")
    target.merged_source_line_ids = ("script-source",)
    page = _page_with_lines(before, target)
    target_block = _block("legacy-target", ["target-line"], [target])
    fake_assembly = SimpleNamespace(
        blocks=(target_block,),
        canonical_line_order=("before-line", "target-line"),
        table_fallbacks=0,
        claimed_table_lines=0,
        orphan_tables=0,
        assembly_ms=0.0,
        list_segment_count=0,
        list_item_count=0,
        list_inferred_marker_count=0,
        list_continuation_count=0,
        list_unassigned_line_count=0,
    )
    monkeypatch.setattr(
        "structured_pdf_text.assemble.document.assemble_page_content",
        lambda _page: fake_assembly,
    )

    document = assemble_document(
        [page],
        metadata=DocumentMetadata("test.pdf", 1, None),
    )

    final_page = document.pages[0]
    transformed = final_page.diagnostics.facts["content_transformed_sources"]
    source_fact = next(item for item in transformed if item["source_line_id"] == "script-source")
    final_ids = {block.block_id for block in final_page.content_blocks}
    assert source_fact["owner_id"] in final_ids
    owner = next(block for block in final_page.content_blocks if block.block_id == source_fact["owner_id"])
    assert source_fact["target_line_id"] in owner.line_ids
    assert final_page.content_blocks[0].text == "antes"


def test_fallback_ignores_unanchored_block_when_finding_canonical_position() -> None:
    anchored_line = _line("âncora", BBox(0, 10, 50, 20), "anchored-line")
    fallback_line = _line("recuperar", BBox(0, 30, 60, 40), "fallback-line")
    page = _page_with_lines(anchored_line, fallback_line)
    anchored_block = _block("page-1:anchored", ["anchored-line"], [anchored_line])
    orphan_block = PageContentBlock(
        block_id="page-1:orphan-table",
        page_index=0,
        kind=ContentKind.TABLE,
        bbox=BBox(0, 45, 100, 70),
        order_index=1,
        table_id="orphan-table",
        line_ids=[],
    )

    blocks, _, _ = record_content_conservation(
        page,
        [anchored_block, orphan_block],
        canonical_line_order=("anchored-line", "fallback-line"),
    )

    assert [block.block_id for block in blocks] == [
        "page-1:anchored",
        "page-1:conservation-fallback-1",
        "page-1:orphan-table",
    ]


def test_partial_list_deduplication_rebuilds_list_structure() -> None:
    duplicate = _line("• duplicado", BBox(0, 10, 90, 20), "list-duplicate")
    unique = _line("• item único", BBox(0, 25, 90, 35), "list-unique")
    page = _page_with_lines(duplicate, unique)
    list_segments = segment_list_lines([duplicate, unique], allow_single=True).segments
    list_block = PageContentBlock(
        block_id="page-1:list",
        page_index=0,
        kind=ContentKind.LIST,
        bbox=BBox(0, 10, 90, 35),
        order_index=1,
        text="• duplicado\n• item único",
        line_ids=["list-duplicate", "list-unique"],
        list_items=[item for segment in list_segments for item in segment.items],
    )
    duplicate_block = _block("page-1:duplicate", ["list-duplicate"], [duplicate])

    blocks, _, _ = record_content_conservation(page, [duplicate_block, list_block])

    rebuilt = next(block for block in blocks if block.block_id == "page-1:list")
    assert rebuilt.kind == ContentKind.LIST
    assert [item.text for item in rebuilt.list_items] == ["item único"]
    page.content_blocks = blocks
    document = StructuredDocument(
        pages=[page],
        tables=[],
        raw_text="",
        reading_text="",
        metadata=DocumentMetadata("test.pdf", 1, None),
        diagnostics=DocumentDiagnostics(
            status=ExtractionStatus.SUCCESS,
            page_count=1,
            native_pages=1,
            mixed_pages=0,
            ocr_pages=0,
        ),
    )
    assert "• item único" in render_markdown(document)


def test_fallback_uses_canonical_sidebar_order_not_region_storage_order() -> None:
    right_line = _line("direita", BBox(82, 20, 99, 30), "right-line")
    left_early = _line("esquerda cedo", BBox(10, 20, 70, 30), "left-early")
    left_late = _line("esquerda tarde", BBox(10, 40, 70, 50), "left-late")
    right = _region(RegionKind.TEXT, right_line.text, BBox(82, 10, 100, 90), "right-line")
    left = _region(RegionKind.TEXT, left_early.text, BBox(0, 0, 78, 100), "left-early")
    left.native_lines = [left_late, left_early]
    page = _page(0, [right, left])
    assembly = assemble_page_content(page)
    right_block = _block("page-1:right", ["right-line"], [right_line])

    blocks, _, _ = record_content_conservation(
        page,
        [right_block],
        canonical_line_order=assembly.canonical_line_order,
    )

    assert assembly.canonical_line_order == ("left-early", "left-late", "right-line")
    assert [block.text for block in blocks] == [
        "esquerda cedo",
        "esquerda tarde",
        "direita",
    ]


def test_fallback_respects_canonical_position_around_spanning_block() -> None:
    spanning_line = _line("faixa", BBox(0, 40, 100, 50), "spanning-line")
    left_line = _line("coluna esquerda", BBox(0, 70, 45, 80), "left-line")
    right_line = _line("coluna direita", BBox(55, 70, 100, 80), "right-line")
    right = _region(RegionKind.TEXT, right_line.text, BBox(55, 65, 100, 100), "right-line")
    left = _region(RegionKind.TEXT, left_line.text, BBox(0, 65, 45, 100), "left-line")
    spanning = _region(RegionKind.TEXT, spanning_line.text, BBox(0, 30, 100, 60), "spanning-line")
    page = _page(0, [right, spanning, left])
    assembly = assemble_page_content(page)

    spanning_block = _block("page-1:spanning", ["spanning-line"], [spanning_line])
    right_block = _block("page-1:right", ["right-line"], [right_line])
    blocks, _, _ = record_content_conservation(
        page,
        [spanning_block, right_block],
        canonical_line_order=assembly.canonical_line_order,
    )

    assert assembly.canonical_line_order == ("spanning-line", "left-line", "right-line")
    assert [block.text for block in blocks] == [
        "faixa",
        "coluna esquerda",
        "coluna direita",
    ]


def test_explicit_page_numbering_is_still_normalized_for_repeated_furniture() -> None:
    pages = [
        _page(
            0,
            [
                _region(
                    RegionKind.FOOTER,
                    "Relatório interno · Página 1 de 2",
                    BBox(0, 775, 260, 795),
                    "p0-footer-page",
                )
            ],
        ),
        _page(
            1,
            [
                _region(
                    RegionKind.FOOTER,
                    "Relatório interno · Página 2 de 2",
                    BBox(0, 775, 260, 795),
                    "p1-footer-page",
                )
            ],
        ),
    ]

    document = assemble_document(
        pages,
        metadata=type(
            "Metadata",
            (),
            {
                "source_path": "test.pdf",
                "page_count": 2,
                "pdfium_version": None,
            },
        )(),
        preserve_headers_footers=False,
    )

    # Explicit page numbering remains a legitimate dynamic repeated template.
    assert "Relatório interno" not in document.reading_text

    assert all(
        page.diagnostics.facts[
            "content_suppressed_repeated_footer_lines"
        ] == 1
        for page in document.pages
    )
