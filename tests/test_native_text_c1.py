from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "native_text_fidelity"))

from structured_pdf_text.document import (  # noqa: E402
    ContentKind,
    DocumentDiagnostics,
    DocumentMetadata,
    ExtractionStatus,
    PageContentBlock,
    PageDiagnostics,
    PageStrategy,
    StructuredDocument,
    StructuredPage,
)
from structured_pdf_text.geometry import BBox  # noqa: E402
from c1_render_audit import (  # noqa: E402
    C1Category,
    audit_rendered_outputs,
    render_report,
)


def _document() -> StructuredDocument:
    page = StructuredPage(
        page_index=0,
        bbox=BBox(0, 0, 200, 200),
        regions=[],
        tables=[],
        raw_text="Texto",
        reading_text="Texto",
        diagnostics=PageDiagnostics(0, PageStrategy.NATIVE, [], 5, 5),
        content_blocks=[
            PageContentBlock(
                block_id="block-text",
                page_index=0,
                kind=ContentKind.TEXT,
                bbox=BBox(0, 0, 100, 20),
                order_index=0,
                text="Texto",
            ),
            PageContentBlock(
                block_id="block-suppressed",
                page_index=0,
                kind=ContentKind.TEXT,
                bbox=BBox(0, 30, 100, 50),
                order_index=1,
                text="não deve aparecer",
                suppressed=True,
            ),
        ],
    )
    return StructuredDocument(
        pages=[page],
        tables=[],
        raw_text="Texto",
        reading_text="Texto",
        metadata=DocumentMetadata("test.pdf", 1, None),
        diagnostics=DocumentDiagnostics(ExtractionStatus.SUCCESS, 1, 1, 0, 0),
    )


def test_c1_requires_structural_json_and_page_scoped_markdown_coverage():
    document = _document()
    summary, findings = audit_rendered_outputs(
        document,
        '{"pages": []}',
        "## Página 1\n\nTexto",
    )

    assert not summary.auditable
    assert summary.json_pages == 0
    assert C1Category.JSON_MISMATCH in [finding.category for finding in findings]
    assert C1Category.BLOCK_RENDERED in [finding.category for finding in findings]


def test_c1_accepts_renderer_outputs_and_reports_machine_counts():
    from structured_pdf_text.renderers.json import render_json
    from structured_pdf_text.renderers.markdown import render_markdown

    document = _document()
    summary, findings = audit_rendered_outputs(
        document,
        render_json(document),
        render_markdown(document),
    )
    report = render_report(summary, findings)

    assert summary.auditable
    assert summary.json_pages == 1
    assert summary.markdown_pages == 1
    assert summary.visible_blocks == 1
    assert summary.rendered_blocks == 1
    assert "`auditable`: **true**" in report
