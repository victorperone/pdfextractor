from structured_pdf_text.assemble.document import (
    _apply_repeated_suppression,
)
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


def _line(
    text: str,
    *,
    line_id: str,
    y: float,
) -> TextLine:
    bbox = BBox(
        10.0,
        y,
        190.0,
        y + 10.0,
    )
    token = TextToken(
        text=text,
        bbox=bbox,
        sources=[
            EvidenceRef(
                SourceKind.NATIVE_PDF,
                0,
                line_id,
            )
        ],
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


def _page(
    lines: list[TextLine],
    *,
    edge_role: str,
) -> StructuredPage:
    region = LayoutRegion(
        region_id="region-1",
        kind=RegionKind.TEXT,
        bbox=BBox(0.0, 0.0, 200.0, 200.0),
        layout_confidence=1.0,
        native_lines=lines,
        ocr_tokens=[],
        quality=RegionQuality(
            RegionDecision.KEEP_NATIVE
        ),
        edge_role=edge_role,
    )

    return StructuredPage(
        page_index=0,
        bbox=BBox(0.0, 0.0, 200.0, 200.0),
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


def _block(
    *,
    line_ids: list[str],
    text: str,
) -> PageContentBlock:
    return PageContentBlock(
        block_id="block-1",
        page_index=0,
        kind=ContentKind.TEXT,
        bbox=BBox(10.0, 10.0, 190.0, 40.0),
        order_index=0,
        text=text,
        line_ids=line_ids,
    )


def test_repeated_header_suppresses_block_when_every_line_is_repeated():
    header = _line(
        "ACME CORPORATION",
        line_id="header-line",
        y=5.0,
    )
    page = _page(
        [header],
        edge_role="top_candidate",
    )
    block = _block(
        line_ids=["header-line"],
        text="ACME CORPORATION",
    )

    _apply_repeated_suppression(
        [block],
        page,
        {
            "header:acme corporation": [0, 1],
        },
        preserve_headers_footers=False,
    )

    assert block.suppressed is True
    assert block.suppression_reason == "repeated_header"


def test_repeated_footer_suppresses_block_when_every_line_is_repeated():
    footer = _line(
        "CONFIDENTIAL",
        line_id="footer-line",
        y=185.0,
    )
    page = _page(
        [footer],
        edge_role="bottom_candidate",
    )
    block = _block(
        line_ids=["footer-line"],
        text="CONFIDENTIAL",
    )

    _apply_repeated_suppression(
        [block],
        page,
        {
            "footer:confidential": [0, 1],
        },
        preserve_headers_footers=False,
    )

    assert block.suppressed is True
    assert block.suppression_reason == "repeated_footer"


def test_partial_repeated_header_match_preserves_unique_content():
    header = _line(
        "ACME CORPORATION",
        line_id="header-line",
        y=5.0,
    )
    unique = _line(
        "Relatório exclusivo do cliente",
        line_id="unique-line",
        y=18.0,
    )
    page = _page(
        [header, unique],
        edge_role="top_candidate",
    )
    block = _block(
        line_ids=[
            "header-line",
            "unique-line",
        ],
        text=(
            "ACME CORPORATION\n"
            "Relatório exclusivo do cliente"
        ),
    )

    _apply_repeated_suppression(
        [block],
        page,
        {
            "header:acme corporation": [0, 1],
        },
        preserve_headers_footers=False,
    )

    assert block.suppressed is False
    assert block.suppression_reason is None


def test_partial_repeated_footer_match_preserves_unique_content():
    unique = _line(
        "Conclusão semântica única",
        line_id="unique-line",
        y=170.0,
    )
    footer = _line(
        "CONFIDENTIAL",
        line_id="footer-line",
        y=185.0,
    )
    page = _page(
        [unique, footer],
        edge_role="bottom_candidate",
    )
    block = _block(
        line_ids=[
            "unique-line",
            "footer-line",
        ],
        text=(
            "Conclusão semântica única\n"
            "CONFIDENTIAL"
        ),
    )

    _apply_repeated_suppression(
        [block],
        page,
        {
            "footer:confidential": [0, 1],
        },
        preserve_headers_footers=False,
    )

    assert block.suppressed is False
    assert block.suppression_reason is None


def test_preserve_headers_footers_disables_repeated_suppression():
    header = _line(
        "ACME CORPORATION",
        line_id="header-line",
        y=5.0,
    )
    page = _page(
        [header],
        edge_role="top_candidate",
    )
    block = _block(
        line_ids=["header-line"],
        text="ACME CORPORATION",
    )

    _apply_repeated_suppression(
        [block],
        page,
        {
            "header:acme corporation": [0, 1],
        },
        preserve_headers_footers=True,
    )

    assert block.suppressed is False
    assert block.suppression_reason is None
