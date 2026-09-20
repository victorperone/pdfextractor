#!/usr/bin/env python3
"""Audit JSON and Markdown renderings without changing the production pipeline."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from structured_pdf_text.api import PdfTextExtractor
from structured_pdf_text.config import ExtractionMode, ExtractorConfig
from structured_pdf_text.document import ContentKind, StructuredDocument
from structured_pdf_text.renderers.json import render_json
from structured_pdf_text.renderers.markdown import render_markdown

try:
    from .a1_schema import write_json, write_jsonl
except ImportError:  # script execution from this directory
    from a1_schema import write_json, write_jsonl


class C1Category(str, Enum):
    JSON_MATCHED = "json_matched"
    JSON_MISMATCH = "json_mismatch"
    MARKDOWN_PAGE_MATCHED = "markdown_page_matched"
    MARKDOWN_PAGE_MISSING = "markdown_page_missing"
    BLOCK_RENDERED = "block_rendered"
    BLOCK_MISSING = "block_missing"


@dataclass(frozen=True, slots=True)
class C1Finding:
    category: C1Category
    page_index: int | None = None
    block_id: str | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "page_index": self.page_index,
            "block_id": self.block_id,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class C1Summary:
    page_count: int
    json_pages: int
    markdown_pages: int
    visible_blocks: int
    rendered_blocks: int
    category_counts: dict[str, int]

    @property
    def auditable(self) -> bool:
        return not any(
            self.category_counts.get(category.value, 0)
            for category in (C1Category.JSON_MISMATCH, C1Category.MARKDOWN_PAGE_MISSING, C1Category.BLOCK_MISSING)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_count": self.page_count,
            "json_pages": self.json_pages,
            "markdown_pages": self.markdown_pages,
            "visible_blocks": self.visible_blocks,
            "rendered_blocks": self.rendered_blocks,
            "category_counts": dict(sorted(self.category_counts.items())),
            "auditable": self.auditable,
        }


def _markdown_pages(markdown: str) -> dict[int, str]:
    matches = list(re.finditer(r"(?m)^## Página (\d+)\s*$", markdown))
    pages: dict[int, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        pages[int(match.group(1)) - 1] = markdown[match.end():end]
    return pages


def _fragment_in_markdown(fragment: str, page_markdown: str) -> bool:
    normalized = fragment.replace("\r\n", "\n")
    candidates = {
        normalized,
        normalized.replace("|", r"\|"),
        normalized.replace("\n", "<br>"),
        normalized.replace("\n", "<br>").replace("|", r"\|"),
    }
    return any(candidate in page_markdown for candidate in candidates if candidate)


def _block_fragments(block: Any, page: Any) -> tuple[str, ...]:
    if block.suppressed:
        return ()
    if block.kind == ContentKind.TABLE:
        table = next((item for item in page.tables if item.table_id == block.table_id), None)
        if table is None:
            return ()
        return tuple(cell.text for cell in table.cells if cell.text.strip())
    if block.kind == ContentKind.LIST and block.list_items:
        return tuple(item.text for item in block.list_items if item.text.strip())
    return (block.text,) if block.text.strip() else ()


def audit_rendered_outputs(
    document: StructuredDocument,
    json_output: str,
    markdown_output: str,
) -> tuple[C1Summary, tuple[C1Finding, ...]]:
    findings: list[C1Finding] = []
    expected_json = document.to_dict()
    try:
        actual_json = json.loads(json_output)
    except json.JSONDecodeError as exc:
        actual_json = None
        findings.append(C1Finding(C1Category.JSON_MISMATCH, detail=f"invalid_json:{exc.msg}"))

    if actual_json == expected_json:
        findings.append(C1Finding(C1Category.JSON_MATCHED))
    elif actual_json is not None:
        findings.append(C1Finding(C1Category.JSON_MISMATCH, detail="parsed_output_differs_from_document"))

    markdown_pages = _markdown_pages(markdown_output)
    for page in document.pages:
        page_markdown = markdown_pages.get(page.page_index)
        if page_markdown is None:
            findings.append(
                C1Finding(
                    C1Category.MARKDOWN_PAGE_MISSING,
                    page_index=page.page_index,
                    detail="missing_page_heading",
                )
            )
            continue
        findings.append(C1Finding(C1Category.MARKDOWN_PAGE_MATCHED, page_index=page.page_index))
        for block in sorted(page.content_blocks, key=lambda item: item.order_index):
            fragments = _block_fragments(block, page)
            if not fragments:
                continue
            if all(_fragment_in_markdown(fragment, page_markdown) for fragment in fragments):
                findings.append(C1Finding(C1Category.BLOCK_RENDERED, page_index=page.page_index, block_id=block.block_id))
            else:
                findings.append(
                    C1Finding(
                        C1Category.BLOCK_MISSING,
                        page_index=page.page_index,
                        block_id=block.block_id,
                        detail="visible_block_fragment_missing_from_page_section",
                    )
                )

    category_counts = Counter(finding.category.value for finding in findings)
    summary = C1Summary(
        page_count=len(document.pages),
        json_pages=len(actual_json.get("pages", [])) if isinstance(actual_json, dict) else 0,
        markdown_pages=len(markdown_pages),
        visible_blocks=sum(
            bool(_block_fragments(block, page))
            for page in document.pages
            for block in page.content_blocks
        ),
        rendered_blocks=category_counts[C1Category.BLOCK_RENDERED.value],
        category_counts=dict(category_counts),
    )
    return summary, tuple(findings)


def render_report(summary: C1Summary, findings: Sequence[C1Finding]) -> str:
    lines = [
        "# C1 — relatório de renderizações finais",
        "",
        f"- `auditable`: **{str(summary.auditable).lower()}**",
        f"- páginas no documento: {summary.page_count}",
        f"- páginas no JSON: {summary.json_pages}",
        f"- páginas no Markdown: {summary.markdown_pages}",
        f"- blocos visíveis verificáveis: {summary.visible_blocks}",
        f"- blocos encontrados no Markdown: {summary.rendered_blocks}",
        "",
        "## Categorias",
        "",
        "| Categoria | Quantidade |",
        "| --- | ---: |",
    ]
    for category, count in sorted(summary.category_counts.items()):
        lines.append(f"| `{category}` | {count} |")
    lines.extend(
        [
            "",
            "A comparação JSON exige igualdade estrutural com `StructuredDocument.to_dict()`. "
            "A comparação Markdown é limitada à seção da página e aos fragmentos "
            "renderizáveis dos blocos não suprimidos; não reimplementa decisões de layout.",
            "",
        ]
    )
    return "\n".join(lines)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(pdf: Path, output: Path, pages: list[int] | None = None) -> Path:
    page_indices = None if pages is None else tuple(page - 1 for page in pages)
    config = ExtractorConfig(
        mode=ExtractionMode.NATIVE,
        enable_ocr=False,
        enable_layout=False,
        enable_tables=False,
        enable_complexity_render=False,
        page_indices=page_indices,
    )
    document = PdfTextExtractor(config=config).extract(pdf)
    json_output = render_json(document)
    markdown_output = render_markdown(document)
    summary, findings = audit_rendered_outputs(document, json_output, markdown_output)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "c1_summary.json", summary.to_dict())
    write_jsonl(output / "c1_findings.jsonl", [finding.to_dict() for finding in findings])
    (output / "c1_rendered.json").write_text(json_output + "\n", encoding="utf-8")
    (output / "c1_rendered.md").write_text(markdown_output + "\n", encoding="utf-8")
    (output / "c1_report.md").write_text(render_report(summary, findings), encoding="utf-8")
    write_json(
        output / "c1_metadata.json",
        {
            "schema": "pdfextractor.native_text_fidelity.c1.run.v1",
            "executed_at_utc": datetime.now(timezone.utc).isoformat(),
            "pdf": str(pdf),
            "pdf_sha256": _sha256(pdf),
            "pages_requested": pages,
            "pages_extracted": len(document.pages),
            "mode": "native",
            "ocr": False,
            "layout": False,
            "tables": False,
            "summary": summary.to_dict(),
            "json": "c1_rendered.json",
            "markdown": "c1_rendered.md",
            "report": "c1_report.md",
        },
    )
    return output


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("pdf", type=Path)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--pages", type=int, nargs="*")
    return command


if __name__ == "__main__":
    args = parser().parse_args()
    print(run(args.pdf, args.output, args.pages))
