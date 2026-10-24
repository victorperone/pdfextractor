"""P1-1: edge furniture never suppresses body content (V4 p81 pattern).

A tall body region that only starts in the top band must stay body, and each
suppressed line must itself sit at the page edge, even when its text repeats
across pages. True running headers/footers stay suppressed, also with /Rotate.
"""
from __future__ import annotations

from structured_pdf_text.assemble.document import _apply_repeated_suppression, _split_mixed_repeated_blocks
from structured_pdf_text.assemble.repeated_regions import detect_repeated_headers_footers
from structured_pdf_text.document import (
    Baseline,
    ContentKind,
    EvidenceRef,
    LayoutRegion,
    PageContentBlock,
    PageDiagnostics,
    PageStrategy,
    RegionDecision,
    RegionKind,
    RegionQuality,
    SourceKind,
    StructuredPage,
    TextLine,
    TextToken,
    WritingDirection,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.layout.ocr_aware import _reclassify_one

WIDTH, HEIGHT = 595.0, 842.0
HEADER = "CORPUS STRESS OCR MARKDOWN V4 / LABORATORIO SINTETICO"
BODY = "deste parágrafo deve ser inventada, removida, duplicada ou deslocada para outro"
FOOTER = "RODAPE DECORATIVO OMITIR NO GABARITO"


def _line(text: str, line_id: str, bbox: BBox) -> TextLine:
    token = TextToken(text=text, bbox=bbox, sources=[EvidenceRef(SourceKind.OCR_PAGE, 0, line_id)],
                      confidence=0.99, normalized_text=text)
    return TextLine(tokens=[token], bbox=bbox, baseline=Baseline(y=bbox.y1),
                    direction=WritingDirection.LEFT_TO_RIGHT, native_order_min=None,
                    native_order_max=None, line_id=line_id)


def _region(kind: RegionKind, lines: list[TextLine], region_id: str) -> LayoutRegion:
    return LayoutRegion(region_id=region_id, kind=kind, bbox=BBox.union_all([l.bbox for l in lines]),
                        layout_confidence=1.0, native_lines=[], ocr_tokens=[],
                        quality=RegionQuality(RegionDecision.KEEP_NATIVE), ocr_lines=lines)


def _page(index: int, regions: list[LayoutRegion], *, rotation: int = 0,
          width: float = WIDTH, height: float = HEIGHT) -> StructuredPage:
    return StructuredPage(
        page_index=index, bbox=BBox(0.0, 0.0, width, height), regions=regions, tables=[],
        raw_text="", reading_text="", page_rotation=rotation,
        diagnostics=PageDiagnostics(page_index=index, strategy=PageStrategy.OCR_CANDIDATE, reasons=[],
                                    native_chars=0, native_text_length=0),
    )


def _v4_page(index: int) -> tuple[StructuredPage, list[PageContentBlock]]:
    """Running header, then a 47-line-like body region already labelled HEADER."""
    header = _line(HEADER, f"ocr:{index}:header", BBox(27, 11, 569, 20))
    body = [_line(BODY, f"ocr:{index}:body:{n}", BBox(30, 124 + 48 * n, 375, 134 + 48 * n)) for n in range(4)]
    footer = _line(FOOTER, f"ocr:{index}:footer", BBox(27, 821, 569, 830))
    page = _page(index, [
        _region(RegionKind.HEADER, [header], f"{index}:h"),
        _region(RegionKind.HEADER, [_line("Título", f"ocr:{index}:top-body", BBox(30, 58, 420, 70)), *body], f"{index}:b"),
        _region(RegionKind.FOOTNOTE, [footer], f"{index}:f"),
    ])
    blocks = [
        PageContentBlock(block_id=f"{index}:{n}", page_index=index, kind=ContentKind.TEXT, bbox=line.bbox,
                         order_index=n, text=line.text, line_ids=[line.line_id])
        for n, line in enumerate([header, *body, footer])
    ]
    return page, blocks


def test_tall_body_region_starting_in_top_band_is_not_header():
    lines = [_line(BODY, f"l{n}", BBox(30, 58 + 10 * n, 560, 66 + 10 * n)) for n in range(47)]
    region = _region(RegionKind.TEXT, lines, "tall")
    assert _reclassify_one(region, None, HEIGHT).kind == RegionKind.TEXT


def test_tall_body_region_ending_in_bottom_band_is_not_footer():
    lines = [_line(BODY, f"l{n}", BBox(30, 300 + 11 * n, 560, 308 + 11 * n)) for n in range(47)]
    region = _region(RegionKind.TEXT, lines, "tall")
    assert _reclassify_one(region, None, HEIGHT).kind == RegionKind.TEXT


def test_compact_edge_regions_are_still_furniture():
    top = _region(RegionKind.TEXT, [_line(HEADER, "h", BBox(27, 11, 569, 20))], "top")
    bottom = _region(RegionKind.TEXT, [_line(FOOTER, "f", BBox(27, 821, 569, 830))], "bottom")
    assert _reclassify_one(top, None, HEIGHT).kind == RegionKind.HEADER
    assert _reclassify_one(bottom, None, HEIGHT).kind == RegionKind.FOOTER


def test_repeated_body_lines_inside_header_region_are_never_suppressed():
    pages_blocks = [_v4_page(index) for index in range(3)]
    pages = [page for page, _ in pages_blocks]
    repeated = detect_repeated_headers_footers(pages)
    assert f"header:{HEADER.casefold()}" in repeated
    assert not any(BODY.casefold() in key for key in repeated)
    for page, blocks in pages_blocks:
        blocks = _split_mixed_repeated_blocks(blocks, page, repeated)
        _apply_repeated_suppression(blocks, page, repeated, preserve_headers_footers=False)
        by_text = {block.text: block for block in blocks}
        assert by_text[HEADER].suppressed and by_text[HEADER].suppression_reason == "repeated_header"
        assert all(not block.suppressed for block in blocks if block.text == BODY)


def test_mixed_header_block_keeps_its_body_lines():
    pages_blocks = [_v4_page(index) for index in range(2)]
    repeated = detect_repeated_headers_footers([page for page, _ in pages_blocks])
    page, blocks = pages_blocks[0]
    merged = PageContentBlock(block_id="merged", page_index=0, kind=ContentKind.HEADER,
                              bbox=BBox(27, 11, 569, 134), order_index=0, text=f"{HEADER} {BODY}",
                              line_ids=[blocks[0].line_ids[0], blocks[1].line_ids[0]])
    split = _split_mixed_repeated_blocks([merged], page, repeated)
    _apply_repeated_suppression(split, page, repeated, preserve_headers_footers=False)
    assert [(block.text, block.suppressed) for block in split] == [(HEADER, True), (BODY, False)]


def test_rotated_page_header_is_still_suppressed():
    # /Rotate 90: the visual top is canonical small x; visual height = canonical width.
    pages = []
    for index in range(2):
        header = _line(HEADER, f"ocr:{index}:rot-header", BBox(8, 100, 18, 700))
        body = _line(BODY, f"ocr:{index}:rot-body", BBox(300, 100, 310, 700))
        pages.append(_page(index, [_region(RegionKind.HEADER, [header, body], f"{index}:r")],
                           rotation=90, width=HEIGHT, height=WIDTH))
    repeated = detect_repeated_headers_footers(pages)
    assert sorted(repeated) == [f"header:{HEADER.casefold()}"]
