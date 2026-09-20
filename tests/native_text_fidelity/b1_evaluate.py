#!/usr/bin/env python3
"""Run the independent B1 structural audit on an extracted PDF document."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from datetime import datetime, timezone
import hashlib
from pathlib import Path

from structured_pdf_text.api import PdfTextExtractor
from structured_pdf_text.config import ExtractionMode, ExtractorConfig

try:
    from .a1_schema import write_json, write_jsonl
    from .b1_structure import B1Category, B1DocumentSummary, B1Finding, audit_document_structure
except ImportError:  # script execution from this directory
    from a1_schema import write_json, write_jsonl
    from b1_structure import B1Category, B1DocumentSummary, B1Finding, audit_document_structure


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render_report(
    reference: dict,
    summary: B1DocumentSummary,
    findings: list[B1Finding] | tuple[B1Finding, ...],
) -> str:
    """Render a concise, evidence-bounded B1 report for review."""
    family_by_page = {
        int(page["page"]): str(page.get("family", "unknown"))
        for page in reference.get("pages", [])
    }
    category_counts = Counter(finding.category.value for finding in findings)
    family_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for finding in findings:
        family_counts[family_by_page.get(finding.page, "unknown")][finding.category.value] += 1

    diagnostic_rows = (
        (
            B1Category.UNIT_GEOMETRY_MISMATCH.value,
            "text_present_geometry_inconsistent",
            "o texto exato foi observado, mas a associação ficou fora da bbox estimada; não é perda textual comprovada",
        ),
        (
            B1Category.UNIT_MISSING.value,
            "unresolved_text_absence",
            "nenhuma ocorrência exata, equivalente por whitespace ou reconstrução por tokens foi encontrada dentro da tolerância",
        ),
        (
            B1Category.UNIT_TOKENIZATION_VARIANT.value,
            "compatibility_ligature_tokenization_variant",
            "a linha preserva a variante compatível no agrupamento, mas os tokens nativos colapsam glifos sobrepostos; não é match exato",
        ),
        (
            B1Category.REGION_PARTITIONED.value,
            "semantic_partition_nonblocking",
            "uma região de referência foi distribuída entre papéis/tipos semânticos observados diferentes",
        ),
    )

    lines = [
        "# B1 — relatório estrutural",
        "",
        f"- `auditable`: **{str(summary.auditable).lower()}**",
        f"- páginas: {summary.page_count}",
        f"- unidades de referência: {summary.reference_units}",
        f"- unidades associadas: {summary.matched_units}",
        "",
        "## Categorias",
        "",
        "| Categoria | Quantidade |",
        "|---|---:|",
    ]
    lines.extend(
        f"| `{category}` | {count} |"
        for category, count in sorted(category_counts.items())
    )
    lines.extend([
        "",
        "## Classificação diagnóstica",
        "",
        "| Achado | Classificação | Quantidade | Interpretação |",
        "|---|---|---:|---|",
    ])
    for category, label, interpretation in diagnostic_rows:
        lines.append(
            f"| `{category}` | `{label}` | {category_counts.get(category, 0)} | {interpretation} |"
        )
    lines.extend([
        "",
        "## Distribuição por família",
        "",
        "| Família | Ausências | Geometria inconsistente | Partições semânticas |",
        "|---|---:|---:|---:|",
    ])
    for family in sorted(family_counts):
        counts = family_counts[family]
        lines.append(
            f"| `{family}` | {counts.get(B1Category.UNIT_MISSING.value, 0)} | "
            f"{counts.get(B1Category.UNIT_GEOMETRY_MISMATCH.value, 0)} | "
            f"{counts.get(B1Category.REGION_PARTITIONED.value, 0)} |"
        )
    lines.extend([
        "",
        "## Exemplos para revisão",
        "",
        "Os exemplos abaixo são evidência do auditor; a causa não é atribuída ao PDFium ou à referência sem uma comparação adicional.",
        "",
    ])
    for category, title in (
        (B1Category.UNIT_MISSING, "Ausências textuais"),
        (B1Category.UNIT_GEOMETRY_MISMATCH, "Texto presente fora da geometria"),
    ):
        lines.extend([f"### {title}", ""])
        examples = [finding for finding in findings if finding.category is category][:12]
        if not examples:
            lines.append("Nenhum caso.")
        else:
            for finding in examples:
                expected = _report_text(finding.expected_text or "")
                lines.append(f"- página {finding.page}, `{finding.reference_id}`: `{expected}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _report_text(value: str) -> str:
    return " ".join(value.replace("`", "'" ).replace("|", "\\|").split())


def run(pdf: Path, reference: Path, output: Path, pages: list[int] | None = None) -> Path:
    reference_data = json.loads(reference.read_text(encoding="utf-8"))
    selected_pages = None if pages is None else tuple(page - 1 for page in pages)
    config = ExtractorConfig(
        mode=ExtractionMode.NATIVE,
        enable_ocr=False,
        enable_layout=True,
        enable_tables=True,
        enable_complexity_render=False,
        page_indices=selected_pages,
    )
    document = PdfTextExtractor(config=config).extract(pdf)
    if pages is not None:
        selected = set(pages)
        reference_data = {
            **reference_data,
            "pages": [page for page in reference_data["pages"] if int(page["page"]) in selected],
        }
    summary, findings = audit_document_structure(reference_data, document)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "b1_summary.json", summary.to_dict())
    write_jsonl(output / "b1_findings.jsonl", [finding.to_dict() for finding in findings])
    (output / "b1_report.md").write_text(
        render_report(reference_data, summary, findings),
        encoding="utf-8",
    )
    write_json(
        output / "b1_metadata.json",
        {
            "schema": "pdfextractor.native_text_fidelity.b1.run.v1",
            "executed_at_utc": datetime.now(timezone.utc).isoformat(),
            "pdf": str(pdf),
            "pdf_sha256": _sha256(pdf),
            "reference": str(reference),
            "reference_sha256": _sha256(reference),
            "pages_requested": pages,
            "pages_extracted": len(document.pages),
            "mode": "native",
            "ocr": False,
            "layout": True,
            "tables": True,
            "report": "b1_report.md",
            "summary": summary.to_dict(),
        },
    )
    return output


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("pdf", type=Path)
    command.add_argument("reference", type=Path)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--pages", type=int, nargs="*")
    return command


if __name__ == "__main__":
    args = parser().parse_args()
    print(run(args.pdf, args.reference, args.output, args.pages))
