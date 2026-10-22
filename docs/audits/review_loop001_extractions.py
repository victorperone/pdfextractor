"""Inventory the saved loop 001 artifacts; never executes extraction or grading.

Run with .venv/bin/python docs/audits/review_loop001_extractions.py.
Diff chunks are review evidence, not Levenshtein metrics or confirmed defects.
"""
from __future__ import annotations

import difflib
import csv
import hashlib
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

from structured_pdf_text.evaluation import critical_data as cd
from structured_pdf_text.evaluation.canonical_document import CanonicalDocument
from structured_pdf_text.evaluation.markdown_parser import parse_page
from structured_pdf_text.evaluation.markdown_syntax import markdown_tokens
from structured_pdf_text.evaluation.reference_inputs import document_pages, load_reference_text

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / "output/loop/001/review"


def norm(text):
    return " ".join(unicodedata.normalize("NFC", text).split())


def tree(body, page, origin):
    nodes = parse_page(body, page, origin)
    doc = CanonicalDocument.from_nodes(nodes, origin, body)
    structure = Counter(n.kind.value for n in nodes)
    inline = Counter()
    for token in markdown_tokens(body):
        inline.update(t.type for t in token.children or [] if t.type in {
            "strong_open", "em_open", "link_open", "code_inline", "image"})
    return doc, dict(structure), dict(inline)


def table_inventory(doc):
    result = []
    children = {}
    for node in doc.all_nodes:
        children.setdefault(node.parent_id, []).append(node)
    for table in doc.all_nodes:
        if table.kind.value != "table":
            continue
        result.append({"metadata": {k: v for k, v in table.metadata.items()
                                    if k != "page_markdown"}, "rows": [
            [{"text": cell.visible_text, "metadata": cell.metadata}
             for cell in children.get(row.node_id, [])]
            for row in children.get(table.node_id, [])]})
    return result


def exports(pages):
    """Produce navigable evidence without assigning defects to every diff."""
    index = ["# Revisão por página — loop 001", "",
             "Inventário completo de divergências, não contagem de defeitos independentes. "
             "A gravidade dos erros confirmados está no relatório em docs/audits. "
             "Diferenças de serialização e metadados também são mantidas.", "",
             "| Documento | Página | Indicadores para revisão | Evidência |",
             "| --- | ---: | --- | --- |"]
    with (DEST / "entity_differences.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["document", "page", "category", "difference", "canonical_value",
                         "count", "priority", "interpretation"])
        for row in pages:
            v, p = row["document"], row["page"]
            signals = []
            for cat, diff in row["critical_entity_differences"].items():
                if cat != "numeric":
                    signals.append(cat)
                for side, values in diff.items():
                    for value, count in values.items():
                        priority = "triagem" if cat == "numeric" else "P1"
                        if v == "V3" and p == 128 and cat == "currency":
                            priority = "P0"
                        writer.writerow([v, p, cat, side, value, count, priority,
                            "Generic numbers include page metadata and decorative footers; "
                            "an entity mismatch does not prove the literal amount vanished."])
            t = row["saved_metrics"]["quality"]["tables"]["table_detection_f1"]["counts"]
            if t.get("fn"):
                signals.append(f"tabelas não detectadas: {t['fn']}")
            if row["structure_reference"].get("code"):
                signals.append("código sem bloco canônico")
            if not row["token_diff_chunks"]:
                signals.append("texto projetado igual")
            elif not signals:
                signals.append("texto/estrutura/ordem: ver comparação")
            index.append(f"| {v} | {p} | {', '.join(signals)} | "
                         f"[Comparação]({v}/p{p:03d}.md) · [Diff]({v}/p{p:03d}.diff) |")
            fence = "`" * (max([len(s) for s in re.findall(r"`+", row["reference"] +
                                                          row["extraction"])] + [3]) + 1)
            body = [f"# {v} — página {p}: {row['title']}", "",
                    "## Gabarito (fonte Markdown)", "", fence + "markdown",
                    row["reference"], fence, "", "## Extração salva (fonte Markdown)", "",
                    fence + "markdown", row["extraction"], fence, "",
                    "## Dados críticos: diferenças de multiconjunto", "", fence + "json",
                    json.dumps(row["critical_entity_differences"], ensure_ascii=False, indent=2),
                    fence, "", "As diferenças numéricas incluem números de página decorativos. "
                    "Um valor ausente como entidade pode estar presente com símbolo ou separador errado. "
                    "O diff conserva reordenações e diferenças benignas de Markdown.", "",
                    f"[Diff completo](p{p:03d}.diff) · [Índice](../INDEX.md)", ""]
            (DEST / v / f"p{p:03d}.md").write_text("\n".join(body), encoding="utf-8")
    (DEST / "INDEX.md").write_text("\n".join(index) + "\n", encoding="utf-8")


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    all_pages = []
    hashes = {}
    extractors = {"currency": cd._extract_currency, "percent": cd._extract_percent,
                  "dates": cd._extract_dates, "cnpj": cd._extract_cnpj,
                  "cpf": cd._extract_cpf, "process_numbers": cd._extract_process_numbers,
                  "numeric": cd._extract_numbers}
    for v in [3, 4, 5]:
        corpus = ROOT / f"corpus/V{v}"
        manifest_path = next(corpus.glob("*MANIFESTO.json"))
        manifest = json.loads(manifest_path.read_text())
        metadata = {p["page"]: p for p in manifest["pages"]}
        ref_path = (corpus / "Document_AI_V3.md" if v == 3 else
                    corpus / "Document_AI_V5_REFERENCIA.md" if v == 5 else None)
        refs = document_pages(load_reference_text(ref_path, manifest_path))
        extracted_path = next((ROOT / f"output/loop/001/V{v}").glob("extracted_*.md"))
        metrics_path = next(extracted_path.parent.glob("metrics_*.json"))
        hyps = document_pages(extracted_path.read_text())
        metrics = json.loads(metrics_path.read_text())
        metric_pages = {p["page"]: p for p in metrics["per_page"]}
        out = DEST / f"V{v}"
        out.mkdir(exist_ok=True)
        for source in [manifest_path, ref_path, extracted_path, metrics_path]:
            if source:
                hashes[str(source.relative_to(ROOT))] = hashlib.sha256(source.read_bytes()).hexdigest()
        for p in sorted(refs.keys() | hyps.keys()):
            ref, hyp = refs.get(p, ""), hyps.get(p, "")
            rd, rs, ri = tree(ref, p, "reference")
            hd, hs, hi = tree(hyp, p, "hypothesis")
            rt, ht = norm(rd.visible_text), norm(hd.visible_text)
            # NFC plus whitespace only. Case, signs, accents and punctuation stay.
            rwords, hwords = rt.split(), ht.split()
            rc, hc = Counter(rwords), Counter(hwords)
            chunks = []
            for tag, a, b, c, d in difflib.SequenceMatcher(
                    None, rwords, hwords, autojunk=True).get_opcodes():
                if tag != "equal":
                    chunks.append({"operation": tag, "reference_token_range": [a, b],
                                   "extraction_token_range": [c, d],
                                   "reference": " ".join(rwords[a:b]),
                                   "extraction": " ".join(hwords[c:d])})
            entities = {}
            for name, extract in extractors.items():
                a, b = Counter(extract(rt)), Counter(extract(ht))
                if a != b:
                    entities[name] = {"missing": dict(a-b), "extra": dict(b-a)}
            row = {"document": f"V{v}", "page": p,
                   "title": metadata.get(p, {}).get("title"),
                   "family": metadata.get(p, {}).get("family"),
                   "conditions": metadata.get(p, {}).get("conditions", []),
                   "reference": ref, "extraction": hyp,
                   "reference_visible": rt, "extraction_visible": ht,
                   "structure_reference": rs, "structure_extraction": hs,
                   "inline_reference": ri, "inline_extraction": hi,
                   "tables_reference": table_inventory(rd),
                   "tables_extraction": table_inventory(hd),
                   "headings_reference": [{"level": n.heading_level, "text": n.visible_text}
                                           for n in rd.leaf_nodes if n.kind.value == "heading"],
                   "headings_extraction": [{"level": n.heading_level, "text": n.visible_text}
                                            for n in hd.leaf_nodes if n.kind.value == "heading"],
                   "missing_tokens": dict(rc-hc), "extra_tokens": dict(hc-rc),
                   "critical_entity_differences": entities,
                   "token_diff_chunks": chunks,
                   "saved_metrics": metric_pages.get(p)}
            all_pages.append(row)
            diff = difflib.unified_diff(
                ref.splitlines(keepends=True), hyp.splitlines(keepends=True),
                fromfile=f"gabarito/V{v}/pagina-{p}",
                tofile=f"extracao/V{v}/pagina-{p}", n=3)
            (out / f"p{p:03d}.diff").write_text("".join(
                line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                for line in diff), encoding="utf-8")
        print(f"V{v}: {len(refs)} reference pages; {len(hyps)} extraction pages", flush=True)
    (DEST / "page_inventory.json").write_text(json.dumps({
        "method": "Saved artifacts only. Page-scoped NFC/whitespace token diffs (difflib, "
                  "autojunk=True), raw Markdown unified diffs, canonical AST and protected "
                  "entity multiset differences. Chunks are not independent defects or CER. "
                  "All changes are retained, including benign serialization differences. "
                  "Numeric entities include decorative page numbers; require contextual review.",
        "input_sha256": hashes, "pages": all_pages}, ensure_ascii=False, indent=2), encoding="utf-8")
    exports(all_pages)
    print(f"Saved {len(all_pages)} page inventories and complete Markdown diffs to {DEST}")


if __name__ == "__main__":
    main()
