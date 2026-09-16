"""Tests for the canonical page content model (Commit 1).

Covers ContentKind, PageContentBlock, and StructuredPage.content_blocks.
Uses only synthetic minimal data — no reference PDF, no file I/O.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from structured_pdf_text.document import (
    ContentKind,
    PageContentBlock,
    StructuredPage,
)
from structured_pdf_text.geometry import BBox


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bbox(x0: float = 0.0, y0: float = 0.0, x1: float = 100.0, y1: float = 20.0) -> BBox:
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _block(
    kind: ContentKind = ContentKind.TEXT,
    *,
    block_id: str = "page-1:block-1",
    page_index: int = 0,
    order_index: int = 0,
    text: str = "hello",
    bbox: BBox | None = None,
) -> PageContentBlock:
    return PageContentBlock(
        block_id=block_id,
        page_index=page_index,
        kind=kind,
        bbox=bbox or _bbox(),
        order_index=order_index,
        text=text,
    )


# ---------------------------------------------------------------------------
# ContentKind enum
# ---------------------------------------------------------------------------

def test_content_kind_values() -> None:
    expected = {"text", "title", "list", "table", "caption", "header", "footer",
                "footnote", "marginalia", "figure", "unknown"}
    actual = {k.value for k in ContentKind}
    assert actual == expected


def test_content_kind_is_str() -> None:
    assert isinstance(ContentKind.TEXT, str)
    assert ContentKind.TABLE == "table"


# ---------------------------------------------------------------------------
# PageContentBlock construction
# ---------------------------------------------------------------------------

def test_block_default_fields() -> None:
    block = _block()
    assert block.text == "hello"
    assert block.table_id is None
    assert block.heading_level is None
    assert block.source_region_ids == []
    assert block.confidence is None
    assert block.fallback_from_table is False


def test_block_table_kind() -> None:
    block = PageContentBlock(
        block_id="page-1:block-2",
        page_index=0,
        kind=ContentKind.TABLE,
        bbox=_bbox(),
        order_index=1,
        table_id="page-1:native-grid",
    )
    assert block.kind == ContentKind.TABLE
    assert block.table_id == "page-1:native-grid"
    assert block.text == ""


def test_block_title_with_heading_level() -> None:
    block = PageContentBlock(
        block_id="page-1:block-1",
        page_index=0,
        kind=ContentKind.TITLE,
        bbox=_bbox(),
        order_index=0,
        text="Introdução",
        heading_level=2,
    )
    assert block.kind == ContentKind.TITLE
    assert block.heading_level == 2


def test_block_fallback_from_table() -> None:
    block = PageContentBlock(
        block_id="page-1:block-3",
        page_index=0,
        kind=ContentKind.TEXT,
        bbox=_bbox(),
        order_index=2,
        text="fallback text",
        fallback_from_table=True,
    )
    assert block.fallback_from_table is True


def test_block_source_region_ids() -> None:
    block = PageContentBlock(
        block_id="page-1:block-1",
        page_index=0,
        kind=ContentKind.TEXT,
        bbox=_bbox(),
        order_index=0,
        source_region_ids=["region-1", "region-2"],
    )
    assert block.source_region_ids == ["region-1", "region-2"]


# ---------------------------------------------------------------------------
# StructuredPage.content_blocks
# ---------------------------------------------------------------------------

def test_structured_page_has_content_blocks_field(
    make_structured_page,
) -> None:
    page = make_structured_page()
    assert hasattr(page, "content_blocks")
    assert page.content_blocks == []


def test_content_blocks_default_empty(make_structured_page) -> None:
    page = make_structured_page()
    assert isinstance(page.content_blocks, list)
    assert len(page.content_blocks) == 0


def test_content_blocks_can_be_assigned(make_structured_page) -> None:
    page = make_structured_page()
    blocks = [
        _block(ContentKind.TITLE, block_id="page-1:block-1", order_index=0),
        _block(ContentKind.TEXT, block_id="page-1:block-2", order_index=1),
    ]
    page.content_blocks = blocks
    assert len(page.content_blocks) == 2
    assert page.content_blocks[0].kind == ContentKind.TITLE
    assert page.content_blocks[1].kind == ContentKind.TEXT


def test_content_blocks_order_index_is_deterministic(make_structured_page) -> None:
    page = make_structured_page()
    page.content_blocks = [
        _block(order_index=2, block_id="page-1:block-3"),
        _block(order_index=0, block_id="page-1:block-1"),
        _block(order_index=1, block_id="page-1:block-2"),
    ]
    sorted_blocks = sorted(page.content_blocks, key=lambda b: b.order_index)
    assert [b.order_index for b in sorted_blocks] == [0, 1, 2]


# ---------------------------------------------------------------------------
# Serialization via to_plain_data
# ---------------------------------------------------------------------------

def test_to_plain_data_includes_content_blocks(make_structured_page) -> None:
    from structured_pdf_text.document import to_plain_data

    page = make_structured_page()
    page.content_blocks = [
        PageContentBlock(
            block_id="page-1:block-1",
            page_index=0,
            kind=ContentKind.TEXT,
            bbox=_bbox(),
            order_index=0,
            text="hello",
        )
    ]
    data = to_plain_data(page)
    assert "content_blocks" in data
    assert len(data["content_blocks"]) == 1
    assert data["content_blocks"][0]["kind"] == "text"
    assert data["content_blocks"][0]["text"] == "hello"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def make_structured_page():
    from structured_pdf_text.document import (
        ComplexityReason,
        PageDiagnostics,
        PageStrategy,
    )

    def _factory(page_index: int = 0) -> StructuredPage:
        return StructuredPage(
            page_index=page_index,
            bbox=_bbox(0, 0, 595, 842),
            regions=[],
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

    return _factory
