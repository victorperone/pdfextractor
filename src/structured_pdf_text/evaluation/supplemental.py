"""Versioned critical/context audit; canonical 3.1.0 scores remain unchanged.

Monetary syntax is pt-BR, with BRL/USD/EUR aliases. IDs are exact, case
sensitive alphanumeric lexemes. Table entities are compared at anchor row/
column coordinates within content-matched tables on the same page. These
diagnostics do not certify the reference, visual truth or OCR confidence.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
from hashlib import sha256
from html.parser import HTMLParser
import re
import unicodedata

from .block_alignment import align_blocks
from .canonical_document import CanonicalDocument
from .markdown_parser import _md, parse_page
from .models import NodeKind
from .order_counts import concordance_counts
from .reference_inputs import document_pages
from .table_metrics import _TableContext, _children_of_kind

AUDIT_VERSION = "critical-context-1.0.0"
_MONEY = re.compile(
    r"(?<![\w$€])(?P<before>[-+−]?)\s*(?P<unit>R\$|BRL|US\$|USD|€|EUR)"
    r"(?![A-Za-z])\s*(?P<after>[-+−]?)\s*(?P<number>\d[\d.,]*)(?P<tail>[\w.,]*)"
)
_NUMBER = re.compile(r"(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d+)?\Z")
_LEXEME = re.compile(r"(?<!\w)\w+(?:[-./]\w+)*(?!\w)", re.UNICODE)
_UNITS = {"R$": "BRL", "BRL": "BRL", "US$": "USD", "USD": "USD", "€": "EUR", "EUR": "EUR"}


def critical_entities(text: str) -> tuple[Counter, list[str]]:
    """Read entire lexemes; never count a valid prefix of malformed money."""
    text = unicodedata.normalize("NFC", text)
    result, invalid = Counter(), []
    masked = list(text)
    for match in _MONEY.finditer(text):
        raw = match.group("number")
        # A final sentence period is punctuation, not a thousands separator.
        if raw.endswith(".") and not raw.endswith(".."):
            raw = raw[:-1]
        if (match.group("before") and match.group("after") or match.group("tail")
                or not _NUMBER.fullmatch(raw)):
            invalid.append(match.group().strip())
        else:
            number = Decimal(raw.replace(".", "").replace(",", "."))
            sign = match.group("before") or match.group("after")
            if sign in {"-", "−"}:
                number = -number
            value = format(number, "f")
            if "." in value:
                value = value.rstrip("0").rstrip(".")
            if number == 0:
                value = "0"
            result[("currency", f"{_UNITS[match.group('unit')]}:{value}")] += 1
        masked[match.start():match.end()] = " "*(match.end()-match.start())
    for match in _LEXEME.finditer("".join(masked)):
        value = match.group()
        if any(ch.isalpha() for ch in value) and any(ch.isdigit() for ch in value):
            result[("identifier", value)] += 1
    return result, invalid


def _score(reference: Counter, hypothesis: Counter) -> dict:
    tp = sum((reference & hypothesis).values())
    fn, fp = sum(reference.values())-tp, sum(hypothesis.values())-tp
    total = 2*tp+fn+fp
    return {"tp": tp, "fp": fp, "fn": fn,
            "f1": 2*tp/total if total else None,
            "missing": [[list(key), count] for key, count in sorted((reference-hypothesis).items())],
            "extra": [[list(key), count] for key, count in sorted((hypothesis-reference).items())]}


def _grid(table, parent_map):
    result = {}
    for row_index, row in enumerate(_children_of_kind(table, parent_map, NodeKind.TABLE_ROW)):
        for column_index, cell in enumerate(_children_of_kind(row, parent_map, NodeKind.TABLE_CELL)):
            coordinate = (cell.metadata.get("row_index", row_index), cell.metadata.get("column_index", column_index))
            result[coordinate] = cell
    return result


def _table_context(reference, hypothesis):
    context = _TableContext.build(reference, hypothesis)
    expected, observed = Counter(), Counter()
    paired_ref, paired_hyp = set(), set()
    for index, (ref, hyp, _) in enumerate(context.matched):
        paired_ref.add(ref.node_id)
        paired_hyp.add(hyp.node_id)
        for table, parent_map, target in ((ref, context.ref_map, expected), (hyp, context.hyp_map, observed)):
            for (row, column), cell in _grid(table, parent_map).items():
                entities, _ = critical_entities(cell.visible_text)
                for (kind, value), count in entities.items():
                    target[(str(index), str(row), str(column), kind, value)] += count
    for tables, paired, parent_map, target, side in (
        (context.ref_tables, paired_ref, context.ref_map, expected, "missing"),
        (context.hyp_tables, paired_hyp, context.hyp_map, observed, "extra"),
    ):
        for table in tables:
            if table.node_id in paired:
                continue
            for (row, column), cell in _grid(table, parent_map).items():
                entities, _ = critical_entities(cell.visible_text)
                for (kind, value), count in entities.items():
                    target[(side+table.node_id, str(row), str(column), kind, value)] += count
    return _score(expected, observed)


def _coverage(reference, hypothesis):
    ref = CanonicalDocument.from_nodes(reference, "reference").leaf_nodes
    hyp = CanonicalDocument.from_nodes(hypothesis).leaf_nodes
    pairs = align_blocks(ref, hyp, detect_provenance=False)
    matched = [p for p in pairs if p.reference_id is not None and p.hypothesis_id is not None]
    ambiguous = sum(p.ambiguity for p in matched)
    by_id = {n.node_id: n for n in ref}
    missing = sum(len(by_id[p.reference_id].visible_text) for p in pairs
                  if p.reference_id in by_id and p.hypothesis_id is None)
    ref_chars = sum(len(n.visible_text) for n in ref)
    concordant, total = concordance_counts([p.hypothesis_order for p in sorted(matched, key=lambda p: p.reference_order)])
    return {"reference_blocks": len(ref), "hypothesis_blocks": len(hyp), "matched_blocks": len(matched),
            "ambiguous_matches": ambiguous, "reference_coverage": len(matched)/len(ref) if ref else None,
            "hypothesis_coverage": len(matched)/len(hyp) if hyp else None,
            "unmatched_reference_character_rate": missing/ref_chars if ref_chars else None,
            "matched_pair_order_accuracy": concordant/total if total else None,
            "ordered_pairs": total}


def _representation(nodes):
    """Count active syntax, excluding code payloads and duplicate containers."""
    hrefs, marks, styles = Counter(), Counter(), Counter()

    class Links(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag == "a":
                href = dict(attrs).get("href")
                if href:
                    hrefs[("href", href)] += 1

    html = Links()
    bodies = [n.metadata["page_markdown"] for n in nodes if "page_markdown" in n.metadata]
    for body in bodies:
        for token in _md.parse(body):
            if token.type in {"fence", "code_block", "blockquote_open"}:
                styles[token.type] += 1
            if token.type == "html_block":
                html.feed(token.content)
            if token.type != "inline":
                continue
            for child in token.children or []:
                if child.type == "link_open":
                    hrefs[("href", child.attrGet("href"))] += 1
                elif child.type == "html_inline":
                    html.feed(child.content)
                elif child.type in {"strong_open", "em_open", "code_inline"}:
                    styles[child.type] += 1
                elif child.type == "text":
                    for match in re.finditer(r"[☐☑☒]|\[(?:[ xX])\]", child.content):
                        marks[("checkbox", "unchecked" if match.group() in {"☐", "[ ]"} else "checked")] += 1
    # HTML explicitly records th/td; pipe tables have a header first row.
    parent_map = defaultdict(list)
    for node in nodes:
        parent_map[node.parent_id].append(node)
    headers = sum(cell.metadata.get("header", row == 0)
                  for table in nodes if table.kind == NodeKind.TABLE
                  for (row, _), cell in _grid(table, parent_map).items())
    return hrefs, marks, {**styles, "header_cells": headers}


def audit_extraction(reference: str, hypothesis: str, *, selected_pages=None) -> dict:
    """Audit selected pages only, including absent pages, without invoking OCR."""
    ref_bodies, hyp_bodies = document_pages(reference), document_pages(hypothesis)
    ref_pages = {page: parse_page(body, page, "reference") for page, body in ref_bodies.items()}
    hyp_pages = {page: parse_page(body, page, "hypothesis") for page, body in hyp_bodies.items()}
    selected = set(selected_pages) if selected_pages is not None else set(ref_pages)
    pages = []
    for page in sorted(selected, key=lambda x: (x is not None, x or 0)):
        refs, hyps = ref_pages.get(page, []), hyp_pages.get(page, [])
        ref_text = CanonicalDocument.from_nodes(refs).visible_text
        hyp_text = CanonicalDocument.from_nodes(hyps).visible_text
        re_, ri = critical_entities(ref_text)
        he, hi = critical_entities(hyp_text)
        ref_hrefs, ref_marks, ref_styles = _representation(refs)
        hyp_hrefs, hyp_marks, hyp_styles = _representation(hyps)
        pages.append({"page": page, "critical_entities": _score(re_, he),
                      "invalid_currency_reference": ri, "invalid_currency_hypothesis": hi,
                      "table_cell_entities": _table_context(refs, hyps), "alignment": _coverage(refs, hyps),
                      "link_destinations": _score(ref_hrefs, hyp_hrefs), "checkbox_states": _score(ref_marks, hyp_marks),
                      "representation_counts": {"reference": ref_styles, "hypothesis": hyp_styles}})
    summary = {}
    for key in ("critical_entities", "table_cell_entities", "link_destinations", "checkbox_states"):
        counts = {name: sum(p[key][name] for p in pages) for name in ("tp", "fp", "fn")}
        total = 2*counts["tp"]+counts["fp"]+counts["fn"]
        summary[key] = {**counts, "f1": 2*counts["tp"]/total if total else None}
    for key in ("reference_coverage", "hypothesis_coverage", "unmatched_reference_character_rate", "matched_pair_order_accuracy"):
        values = [(p["page"], p["alignment"][key]) for p in pages if p["alignment"][key] is not None]
        worst = (max if key == "unmatched_reference_character_rate" else min)(values, key=lambda item: item[1]) if values else (None, None)
        summary[key] = {"macro": sum(v for _, v in values)/len(values) if values else None,
                        "worst_page": worst[0], "worst_value": worst[1], "applicable_pages": len(values)}
    return {"audit_version": AUDIT_VERSION, "canonical_definitions_unchanged": "3.1.0",
            "reference_sha256": sha256(reference.encode()).hexdigest(),
            "hypothesis_sha256": sha256(hypothesis.encode()).hexdigest(),
            "limitations": ["Currency uses pt-BR separators; IDs require letters and digits, with case preserved.",
                            "Table context compares anchor coordinates; moving a whole row changes its context score.",
                            "Order applies to matched blocks only; read coverage and deletion diagnostics alongside it.",
                            "Reference/PDF incompatibilities remain part of this audit; no ground truth is rewritten."],
            "summary": summary, "per_page": pages}
