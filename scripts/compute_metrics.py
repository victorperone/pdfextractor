#!/usr/bin/env python3
"""
Compute E2E metrics — Fase 8.

Compares extracted Markdown against the reference ground truth and computes
the minimum metric set (section 50 of metricas_avaliacao_parser_ocr_markdown.md):

  Group 1 — Text
  Group 2 — Markdown Structure
  Group 3 — Tables
  Group 4 — Order and Integrity
  Group 5 — Critical Data

Pages are processed in parallel using ProcessPoolExecutor (one worker per
CPU core). On a 12-core machine this reduces wall-clock time from ~60 min to
~10 min for the 224-page corpus, while producing results identical to the
sequential implementation.

Usage (Windows server):
    python scripts\\compute_metrics.py ^
        --hypothesis output\\fase8\\extracted_paddle_20261001.md ^
        --manifesto corpus\\Corpus_Stress_OCR_Markdown_V4_MANIFESTO.json ^
        --engine paddle ^
        --output-dir output\\fase8

Or with a plain reference Markdown file:
    python scripts\\compute_metrics.py ^
        --hypothesis output\\fase8\\extracted_paddle_20261001.md ^
        --reference corpus\\Corpus_Stress_OCR_Markdown_V4_REFERENCIA.md ^
        --engine paddle ^
        --output-dir output\\fase8

Output:
    output/fase8/metrics_{engine}_{run_id}.json
    output/fase8/errors_{engine}_{run_id}.md
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

_SRC = Path(__file__).parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _sha256_file(path: Path | None) -> str | None:
    """Return a streaming SHA-256 for an input file when it is available."""
    if path is None or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

# ---------------------------------------------------------------------------
# Text normalization (mirrors ocr_metrics.py — no external deps)
# ---------------------------------------------------------------------------

def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _normalize(text: str) -> str:
    """NFC + CRLF→LF + collapse intra-line spaces + strip trailing whitespace."""
    text = _nfc(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [" ".join(ln.split()) for ln in text.split("\n")]
    return "\n".join(lines).strip()


def _strip_md(text: str) -> str:
    """Remove Markdown formatting, keeping readable content."""
    out: list[str] = []
    for line in text.split("\n"):
        s = line.strip()
        if s.startswith("<!--") and s.endswith("-->"):
            continue
        if s.startswith("<!--"):
            continue
        m = re.match(r"^#{1,6}\s+", line)
        if m:
            line = line[m.end():]
        if line.startswith("> "):
            line = line[2:]
        if "|" in line:
            cells = [c.strip() for c in line.split("|")]
            cells = [c for c in cells if c and not re.match(r"^[-:]+$", c)]
            if cells:
                line = "  ".join(cells)
            else:
                continue
        line = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", line)
        line = re.sub(r"_{1,2}([^_]+)_{1,2}", r"\1", line)
        line = re.sub(r"`([^`]+)`", r"\1", line)
        out.append(line)
    return _normalize("\n".join(out))


def _strip_page_header(text: str) -> str:
    """Remove the '## Página N …' line at the start of a page section."""
    return re.sub(
        r"^##\s+P[áa]gina\s+[^\n]*\n?", "", text, flags=re.IGNORECASE
    ).strip()


def _selected_document_bodies(
    hyp_pages: dict[int, str], ref_pages: dict[int, str], selected_ref_pages: set[int]
) -> tuple[str, str]:
    """Build structure-comparison bodies, retaining missing selected pages as empty."""
    page_numbers = sorted(selected_ref_pages)
    hyp = "\n\n".join(_strip_page_header(hyp_pages.get(pn, "")) for pn in page_numbers)
    ref = "\n\n".join(_strip_page_header(ref_pages[pn]) for pn in page_numbers)
    return hyp, ref


# ---------------------------------------------------------------------------
# Levenshtein — char and word level with S/D/I traceback
# ---------------------------------------------------------------------------

def _lev_distance(a: list, b: list) -> int:
    """Compute Levenshtein edit distance between two token sequences using O(n) space DP."""
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    curr = [0] * (lb + 1)
    for i in range(1, la + 1):
        curr[0] = i
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev, curr = curr, prev
    return prev[lb]


def _lev_ops(a: list, b: list, max_len: int = 3000) -> tuple[int, int, int]:
    """Return (substitutions, deletions, insertions) via DP traceback.

    Capped at max_len to stay memory-safe for large pages.
    """
    a = a[:max_len]
    b = b[:max_len]
    la, lb = len(a), len(b)
    if la == 0:
        return 0, 0, lb
    if lb == 0:
        return 0, la, 0

    dp = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        dp[i][0] = i
    for j in range(lb + 1):
        dp[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)

    S = D = I = 0
    i, j = la, lb
    while i > 0 or j > 0:
        if i > 0 and j > 0 and a[i - 1] == b[j - 1]:
            i -= 1; j -= 1
        elif i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + 1:
            S += 1; i -= 1; j -= 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            D += 1; i -= 1
        else:
            I += 1; j -= 1
    return S, D, I


def _lcs_len(a: list, b: list, max_len: int = 1000) -> int:
    """Longest Common Subsequence length."""
    a, b = a[:max_len], b[:max_len]
    la, lb = len(a), len(b)
    prev = [0] * (lb + 1)
    curr = [0] * (lb + 1)
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            curr[j] = prev[j - 1] + 1 if a[i - 1] == b[j - 1] else max(prev[j], curr[j - 1])
        prev, curr = curr, [0] * (lb + 1)
    return prev[lb]


# ---------------------------------------------------------------------------
# Grupo 1 — Text metrics
# ---------------------------------------------------------------------------

def _cer_raw(hyp: str, ref: str) -> float:
    if not ref:
        return 0.0
    return _lev_distance(list(hyp), list(ref)) / len(ref)


def _cer_normalized(hyp: str, ref: str) -> float:
    hyp_n, ref_n = _normalize(hyp), _normalize(ref)
    if not ref_n:
        return 0.0
    return _lev_distance(list(hyp_n), list(ref_n)) / len(ref_n)


def _cer_text_only(hyp: str, ref: str) -> float:
    hyp_t, ref_t = _strip_md(hyp), _strip_md(ref)
    if not ref_t:
        return 0.0
    return _lev_distance(list(hyp_t), list(ref_t)) / len(ref_t)


def _wer(hyp: str, ref: str) -> float:
    hyp_w = _normalize(hyp).split()
    ref_w = _normalize(ref).split()
    if not ref_w:
        return 0.0
    return _lev_distance(hyp_w, ref_w) / len(ref_w)


def compute_text_metrics(hyp: str, ref: str) -> dict:
    """Compute all Group 1 text-quality metrics for script/report consumers.

    Returns a dict with cer_raw, cer_normalized, cer_text_only, wer, word_accuracy,
    substitution_rate, deletion_rate, insertion_rate, omission_rate.
    """
    ref_n = _normalize(ref)
    ref_words = ref_n.split()
    N_chars = len(ref_n) or 1
    N_words = len(ref_words) or 1

    hyp_words = _normalize(hyp).split()
    S, D, I = _lev_ops(hyp_words, ref_words)

    wer_val = _wer(hyp, ref)
    return {
        "cer_raw": round(_cer_raw(hyp, ref), 6),
        "cer_normalized": round(_cer_normalized(hyp, ref), 6),
        "cer_text_only": round(_cer_text_only(hyp, ref), 6),
        "wer": round(wer_val, 6),
        "word_accuracy": round(max(0.0, 1.0 - wer_val), 6),
        "substitution_rate": round(S / N_words, 6),
        "deletion_rate": round(D / N_words, 6),
        "insertion_rate": round(I / N_words, 6),
        "omission_rate": round(D / N_words, 6),
    }


# ---------------------------------------------------------------------------
# Grupo 2 — Markdown structure
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_LIST_BLOCK_RE = re.compile(r"^([-*+]|\d+\.)\s+", re.MULTILINE)
_CODE_FENCE_RE = re.compile(r"^```", re.MULTILINE)


def _parse_headings(text: str) -> list[tuple[int, str]]:
    """Return [(level, normalized_text)] for all headings, skipping ## Página lines."""
    result = []
    for m in _HEADING_RE.finditer(text):
        level = len(m.group(1))
        content = _normalize(m.group(2))
        # Skip page-section headers generated by the extractor
        if re.match(r"P[áa]gina\s+\d+", content, re.IGNORECASE):
            continue
        result.append((level, content))
    return result


def _block_type_sequence(text: str) -> list[str]:
    """Sequence of block types: h1..h6, table, code, list, para."""
    types: list[str] = []
    in_code = False
    current: list[str] = []

    def flush():
        if not current:
            return
        block = "\n".join(current).strip()
        if not block:
            pass
        elif re.match(r"^(#{1,6})\s+", block):
            level = len(re.match(r"^(#+)", block).group(1))
            types.append(f"h{min(level,6)}")
        elif "|" in block and re.search(r"^\|", block, re.MULTILINE):
            types.append("table")
        elif re.match(r"```", block):
            types.append("code")
        elif re.match(r"^([-*+]|\d+\.)\s", block):
            types.append("list")
        elif block:
            types.append("para")
        current.clear()

    for line in text.split("\n"):
        if line.strip().startswith("```"):
            in_code = not in_code
            current.append(line)
            if not in_code:
                flush()
            continue
        if in_code:
            current.append(line)
            continue
        if line.strip() == "":
            flush()
        else:
            current.append(line)

    flush()
    return types


def _f1(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    """Compute (precision, recall, F1) from counts."""
    p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
    return round(p, 4), round(r, 4), round(f, 4)


def _count_by_type(seq: list[str]) -> Counter:
    return Counter(seq)


def compute_structure_metrics(hyp: str, ref: str) -> dict:
    """Compute Group 2 Markdown structure metrics on the full document body.

    Evaluates headings (F1, level accuracy, text CER), block types (F1),
    paragraph boundaries, list detection, and AST-level similarity via LCS.
    """
    ref_headings = _parse_headings(ref)
    hyp_headings = _parse_headings(hyp)

    # Heading F1: match by normalized content (exact after normalization)
    ref_h_texts = Counter(_normalize(h[1]) for h in ref_headings)
    hyp_h_texts = Counter(_normalize(h[1]) for h in hyp_headings)
    h_tp = sum((ref_h_texts & hyp_h_texts).values())
    h_fp = sum(hyp_h_texts.values()) - h_tp
    h_fn = sum(ref_h_texts.values()) - h_tp
    _, _, heading_f1 = _f1(h_tp, h_fp, h_fn)

    # Heading Level Accuracy: among matched headings, fraction with correct level
    ref_h_map = {_normalize(h[1]): h[0] for h in ref_headings}
    level_correct = level_total = 0
    for level, text in hyp_headings:
        norm = _normalize(text)
        if norm in ref_h_map:
            level_total += 1
            if ref_h_map[norm] == level:
                level_correct += 1
    heading_level_accuracy = level_correct / level_total if level_total > 0 else 1.0

    # Heading Text CER: align by position order and compute CER on all pairs
    # (including imperfect matches).  Only computing CER on exact-match pairs
    # produces an optimistic bias — headings with OCR errors are silently skipped.
    heading_text_cer_vals: list[float] = []
    for (_, hyp_ht), (_, ref_ht) in zip(hyp_headings, ref_headings):
        heading_text_cer_vals.append(_cer_normalized(hyp_ht, ref_ht))
    heading_text_cer = sum(heading_text_cer_vals) / len(heading_text_cer_vals) if heading_text_cer_vals else 0.0

    # Block F1: compare block type sequences as multisets
    ref_seq = _block_type_sequence(ref)
    hyp_seq = _block_type_sequence(hyp)
    ref_block_counts = _count_by_type(ref_seq)
    hyp_block_counts = _count_by_type(hyp_seq)
    b_tp = sum((ref_block_counts & hyp_block_counts).values())
    b_fp = sum(hyp_block_counts.values()) - b_tp
    b_fn = sum(ref_block_counts.values()) - b_tp
    _, _, block_f1 = _f1(b_tp, b_fp, b_fn)

    # Paragraph Boundary F1: compare paragraph count as proxy
    def para_count(text: str) -> int:
        return len([p for p in re.split(r"\n{2,}", text) if p.strip()])

    ref_paras = para_count(ref)
    hyp_paras = para_count(hyp)
    pb_tp = min(ref_paras, hyp_paras)
    pb_fp = max(0, hyp_paras - ref_paras)
    pb_fn = max(0, ref_paras - hyp_paras)
    _, _, para_boundary_f1 = _f1(pb_tp, pb_fp, pb_fn)

    # List Detection F1
    ref_list_blocks = len(re.findall(r"(?:^(?:[-*+]|\d+\.)\s.+\n?)+", ref, re.MULTILINE))
    hyp_list_blocks = len(re.findall(r"(?:^(?:[-*+]|\d+\.)\s.+\n?)+", hyp, re.MULTILINE))
    l_tp = min(ref_list_blocks, hyp_list_blocks)
    l_fp = max(0, hyp_list_blocks - ref_list_blocks)
    l_fn = max(0, ref_list_blocks - hyp_list_blocks)
    _, _, list_f1 = _f1(l_tp, l_fp, l_fn)
    if ref_list_blocks == 0 and hyp_list_blocks == 0:
        list_f1 = 1.0

    # Markdown AST Similarity: LCS of block type sequences / max length
    lcs = _lcs_len(ref_seq, hyp_seq)
    max_len = max(len(ref_seq), len(hyp_seq), 1)
    md_ast_similarity = round(lcs / max_len, 4)

    return {
        "heading_f1": heading_f1,
        "heading_level_accuracy": round(heading_level_accuracy, 4),
        "heading_text_cer": round(heading_text_cer, 6),
        "block_f1": block_f1,
        "paragraph_boundary_f1": para_boundary_f1,
        "list_detection_f1": list_f1,
        "markdown_ast_similarity": md_ast_similarity,
    }


# ---------------------------------------------------------------------------
# Grupo 3 — Tables
# ---------------------------------------------------------------------------

def _parse_md_tables(text: str) -> list[list[list[str]]]:
    """Parse GFM pipe tables → list of 2D cell arrays (no separator rows)."""
    tables: list[list[list[str]]] = []
    current: list[list[str]] = []

    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped[1:-1].split("|")]
            if all(re.match(r"^[-: ]+$", c) for c in cells if c.strip()):
                continue  # separator row
            current.append(cells)
        else:
            if len(current) >= 1:
                tables.append(current)
            current = []

    if current:
        tables.append(current)

    return tables


def _normalize_cell(text: str) -> str:
    return _normalize(text.strip())


def compute_table_metrics(hyp: str, ref: str) -> dict:
    """Compute Group 3 GFM table metrics on the full document body.

    Evaluates table detection F1, row/column F1, dimension accuracy,
    cell exact match, cell CER, cell alignment accuracy, and table structure similarity.
    """
    ref_tables = _parse_md_tables(ref)
    hyp_tables = _parse_md_tables(hyp)

    n_ref = len(ref_tables)
    n_hyp = len(hyp_tables)
    table_tp = min(n_ref, n_hyp)
    table_fp = max(0, n_hyp - n_ref)
    table_fn = max(0, n_ref - n_hyp)
    if n_ref == 0 and n_hyp == 0:
        table_precision = table_recall = table_f1 = 1.0
    else:
        table_precision, table_recall, table_f1 = _f1(table_tp, table_fp, table_fn)

    row_f1_sum = column_f1_sum = dimension_accuracy_sum = structure_similarity_sum = 0.0
    table_metric_count = max(n_ref, n_hyp)
    cell_ref_count = cell_exact_count = cell_alignment_count = 0
    cell_edit_sum = cell_ref_char_sum = 0

    # Match tables within the page by content and shape. Greedy best-pairing
    # prevents an extra leading hypothesis table from shifting every match.
    pairs = _match_tables(ref_tables, hyp_tables)
    hyp_for_ref = {ref_i: hyp_i for ref_i, hyp_i in pairs}
    matched_hyp = {hyp_i for _, hyp_i in pairs}
    for table_index, ref_t in enumerate(ref_tables):
        hyp_i = hyp_for_ref.get(table_index)
        hyp_t = hyp_tables[hyp_i] if hyp_i is not None else []
        n_ref_rows, n_hyp_rows = len(ref_t), len(hyp_t)
        n_ref_cols = max((len(row) for row in ref_t), default=0)
        n_hyp_cols = max((len(row) for row in hyp_t), default=0)

        _, _, row_f1 = _f1(
            min(n_ref_rows, n_hyp_rows),
            max(0, n_hyp_rows - n_ref_rows),
            max(0, n_ref_rows - n_hyp_rows),
        )
        _, _, column_f1 = _f1(
            min(n_ref_cols, n_hyp_cols),
            max(0, n_hyp_cols - n_ref_cols),
            max(0, n_ref_cols - n_hyp_cols),
        )
        row_f1_sum += row_f1
        column_f1_sum += column_f1
        dimension_accuracy_sum += float(
            n_ref_rows == n_hyp_rows and n_ref_cols == n_hyp_cols
        )
        structure_similarity_sum += (row_f1 * column_f1) ** 0.5

        for row_index, ref_row in enumerate(ref_t):
            hyp_row = hyp_t[row_index] if row_index < len(hyp_t) else []
            for column_index, ref_cell in enumerate(ref_row):
                hyp_cell = hyp_row[column_index] if column_index < len(hyp_row) else ""
                ref_norm = _normalize_cell(ref_cell)
                hyp_norm = _normalize_cell(hyp_cell)
                cell_ref_count += 1
                cell_exact_count += int(ref_norm == hyp_norm)
                cell_alignment_count += int(ref_norm == hyp_norm)
                cell_edit_sum += _lev_distance(list(hyp_norm), list(ref_norm))
                cell_ref_char_sum += len(ref_norm)

    # Unmatched hypothesis tables count as false positives for table shape.
    # Their non-empty cells also count as inserted characters for CER.
    for hyp_i, hyp_t in enumerate(hyp_tables):
        if hyp_i in matched_hyp:
            continue
        for row in hyp_t:
            for cell in row:
                cell_edit_sum += len(_normalize_cell(cell))

    ref_cells = Counter(
        _normalize_cell(cell)
        for table in ref_tables for row in table for cell in row
        if _normalize_cell(cell)
    )
    hyp_cells = Counter(
        _normalize_cell(cell)
        for table in hyp_tables for row in table for cell in row
        if _normalize_cell(cell)
    )
    content_tp = sum((ref_cells & hyp_cells).values())
    content_fn = sum(ref_cells.values()) - content_tp
    content_fp = sum(hyp_cells.values()) - content_tp
    if not ref_cells and not hyp_cells:
        table_content_f1 = 1.0
    else:
        _, _, table_content_f1 = _f1(content_tp, content_fp, content_fn)

    # Extra tables have no reference cells to align to. Include them in the
    # exact-match denominator so invented tables cannot appear cell-perfect.
    hyp_only_cell_count = sum(
        1 for hyp_i, table in enumerate(hyp_tables) if hyp_i not in matched_hyp
        for row in table for cell in row
    )
    cell_match_denominator = cell_ref_count + hyp_only_cell_count
    both_without_tables = n_ref == 0 and n_hyp == 0
    cell_exact_match = (
        cell_exact_count / cell_match_denominator
        if cell_match_denominator else float(both_without_tables)
    )
    cell_alignment_accuracy = (
        cell_alignment_count / cell_match_denominator
        if cell_match_denominator else float(both_without_tables)
    )
    if cell_ref_char_sum:
        cell_cer = cell_edit_sum / cell_ref_char_sum
    else:
        cell_cer = float(cell_edit_sum > 0)
    if table_metric_count:
        row_f1 = row_f1_sum / table_metric_count
        column_f1 = column_f1_sum / table_metric_count
        dimension_accuracy = dimension_accuracy_sum / table_metric_count
        structure_similarity = structure_similarity_sum / table_metric_count
    else:
        row_f1 = column_f1 = dimension_accuracy = structure_similarity = 1.0

    # The extra counters let the document aggregate compute true micro metrics
    # instead of averaging page-level F1/CER values.
    return {
        "table_precision": table_precision,
        "table_recall": table_recall,
        "table_f1": table_f1,
        "row_f1": round(row_f1, 4),
        "column_f1": round(column_f1, 4),
        "table_dimension_accuracy": round(dimension_accuracy, 4),
        "cell_exact_match": round(cell_exact_match, 4),
        "cell_cer": round(cell_cer, 6),
        "cell_alignment_accuracy": round(cell_alignment_accuracy, 4),
        "table_structure_similarity": round(structure_similarity, 4),
        "table_content_f1": table_content_f1,
        "table_tp": table_tp,
        "table_fp": table_fp,
        "table_fn": table_fn,
        "cell_ref_count": cell_ref_count,
        "cell_exact_count": cell_exact_count,
        "cell_alignment_count": cell_alignment_count,
        "cell_edit_sum": cell_edit_sum,
        "cell_ref_char_sum": cell_ref_char_sum,
        "cell_match_denominator": cell_match_denominator,
        "table_content_tp": content_tp,
        "table_content_fp": content_fp,
        "table_content_fn": content_fn,
        "table_metric_count": table_metric_count,
        "row_f1_sum": row_f1_sum,
        "column_f1_sum": column_f1_sum,
        "table_dimension_accuracy_sum": dimension_accuracy_sum,
        "table_structure_similarity_sum": structure_similarity_sum,
    }


def _match_tables(
    ref_tables: list[list[list[str]]], hyp_tables: list[list[list[str]]]
) -> list[tuple[int, int]]:
    """Greedily pair the most similar tables on a page.

    Content overlap dominates shape so an inserted table does not displace
    otherwise exact matches. Ordinal distance is only a deterministic tie-break.
    """
    candidates: list[tuple[float, int, int]] = []
    for ri, ref in enumerate(ref_tables):
        ref_cells = Counter(_normalize_cell(cell) for row in ref for cell in row if _normalize_cell(cell))
        ref_shape = (len(ref), max((len(row) for row in ref), default=0))
        for hi, hyp in enumerate(hyp_tables):
            hyp_cells = Counter(_normalize_cell(cell) for row in hyp for cell in row if _normalize_cell(cell))
            overlap = sum((ref_cells & hyp_cells).values())
            union = sum((ref_cells | hyp_cells).values())
            content_score = overlap / union if union else 1.0
            hyp_shape = (len(hyp), max((len(row) for row in hyp), default=0))
            shape_score = sum(a == b for a, b in zip(ref_shape, hyp_shape)) / 2
            score = content_score * 0.8 + shape_score * 0.2
            candidates.append((score, ri, hi))
    result: list[tuple[int, int]] = []
    used_ref: set[int] = set()
    used_hyp: set[int] = set()
    for _, ri, hi in sorted(candidates, key=lambda item: (-item[0], abs(item[1] - item[2]), item[1], item[2])):
        if ri not in used_ref and hi not in used_hyp:
            result.append((ri, hi))
            used_ref.add(ri)
            used_hyp.add(hi)
    return result


def aggregate_table_metrics_from_pages(
    per_page_results: list[dict],
    selected_but_missing: set[int],
    ref_pages: dict[int, str],
) -> dict:
    """Aggregate Group 3 table metrics from per-page results.

    Per-page computation already pairs tables within the same page (via
    compute_table_metrics on each page body), which avoids the cross-page
    positional shift that occurs when pairing tables in a concatenated document.

    Missing pages contribute empty hypothesis tables (all reference tables become FN).
    """
    totals = Counter()
    for entry in per_page_results:
        if entry.get("missing_from_hypothesis"):
            continue
        for key in (
            "table_tp", "table_fp", "table_fn",
            "cell_ref_count", "cell_exact_count", "cell_alignment_count",
            "cell_edit_sum", "cell_ref_char_sum", "cell_match_denominator",
            "table_content_tp", "table_content_fp", "table_content_fn",
            "table_metric_count", "row_f1_sum", "column_f1_sum",
            "table_dimension_accuracy_sum", "table_structure_similarity_sum",
        ):
            totals[key] += entry.get(key, 0)

    # Missing pages are empty hypotheses against their selected reference pages.
    for pn in selected_but_missing:
        ref_body = _strip_page_header(ref_pages.get(pn, ""))
        missing_metrics = compute_table_metrics("", ref_body)
        for key, value in missing_metrics.items():
            if key in {
                "table_tp", "table_fp", "table_fn",
                "cell_ref_count", "cell_exact_count", "cell_alignment_count",
                "cell_edit_sum", "cell_ref_char_sum", "cell_match_denominator",
                "table_content_tp", "table_content_fp", "table_content_fn",
                "table_metric_count", "row_f1_sum", "column_f1_sum",
                "table_dimension_accuracy_sum", "table_structure_similarity_sum",
            }:
                totals[key] += value

    table_precision, table_recall, table_f1 = _f1(
        totals["table_tp"], totals["table_fp"], totals["table_fn"]
    )
    # An all-no-table document is a perfect detection result, matching the
    # page-level true-negative convention without letting TN pages dilute F1.
    if not any(totals[k] for k in ("table_tp", "table_fp", "table_fn")):
        table_precision = table_recall = table_f1 = 1.0

    def _ratio(numerator: float, denominator: float, empty_value: float = 1.0) -> float:
        return numerator / denominator if denominator else empty_value

    cell_cer = (
        totals["cell_edit_sum"] / totals["cell_ref_char_sum"]
        if totals["cell_ref_char_sum"]
        else float(totals["cell_edit_sum"] > 0)
    )
    content_precision, content_recall, content_f1 = _f1(
        totals["table_content_tp"],
        totals["table_content_fp"],
        totals["table_content_fn"],
    )
    if not any(totals[k] for k in (
        "table_content_tp", "table_content_fp", "table_content_fn"
    )):
        content_precision = content_recall = content_f1 = 1.0

    table_count = totals["table_metric_count"]
    return {
        "table_precision": table_precision,
        "table_recall": table_recall,
        "table_f1": table_f1,
        "row_f1": round(_ratio(totals["row_f1_sum"], table_count), 4),
        "column_f1": round(_ratio(totals["column_f1_sum"], table_count), 4),
        "table_dimension_accuracy": round(
            _ratio(totals["table_dimension_accuracy_sum"], table_count), 4
        ),
        "cell_exact_match": round(
            _ratio(totals["cell_exact_count"], totals["cell_match_denominator"]), 4
        ),
        "cell_cer": round(cell_cer, 6),
        "cell_alignment_accuracy": round(
            _ratio(totals["cell_alignment_count"], totals["cell_match_denominator"]), 4
        ),
        "table_structure_similarity": round(
            _ratio(totals["table_structure_similarity_sum"], table_count), 4
        ),
        "table_content_precision": content_precision,
        "table_content_recall": content_recall,
        "table_content_f1": content_f1,
        "table_tp": totals["table_tp"],
        "table_fp": totals["table_fp"],
        "table_fn": totals["table_fn"],
        "cell_ref_count": totals["cell_ref_count"],
        "cell_exact_count": totals["cell_exact_count"],
        "cell_edit_sum": totals["cell_edit_sum"],
        "cell_ref_char_sum": totals["cell_ref_char_sum"],
        "table_content_tp": totals["table_content_tp"],
        "table_content_fp": totals["table_content_fp"],
        "table_content_fn": totals["table_content_fn"],
    }


# ---------------------------------------------------------------------------
# Grupo 4 — Order and integrity
# ---------------------------------------------------------------------------

_PAGE_NUM_RE = re.compile(r"^\s*\d{1,4}\s*$", re.MULTILINE)
_DATE_INLINE_RE = re.compile(r"\b\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4}\b")


def _extract_text_blocks(text: str, min_len: int = 20) -> list[str]:
    """Extract non-empty paragraphs of at least min_len characters."""
    blocks = []
    for p in re.split(r"\n{2,}", text):
        p = _strip_md(p).strip()
        if len(p) >= min_len:
            blocks.append(p[:100])  # use prefix as identifier
    return blocks


def compute_integrity_metrics(
    hyp: str,
    ref: str,
    hyp_pages: dict[int, str],
    ref_pages: dict[int, str],
    selected_but_missing: set[int] | None = None,
) -> dict:
    """Compute Group 4 order and integrity metrics on the full document.

    Evaluates reading order accuracy (LCS of blocks), duplicate content/block rate,
    header/footer/page-number leakage, failure rate, and invalid Markdown rate.
    """
    # Reading Order Accuracy: LCS of block sequences / ref length
    ref_blocks = _extract_text_blocks(ref)
    hyp_blocks = _extract_text_blocks(hyp)
    if ref_blocks:
        lcs = _lcs_len(ref_blocks, hyp_blocks, max_len=500)
        reading_order = round(lcs / len(ref_blocks), 4)
    else:
        reading_order = 1.0

    # Duplicate Content Rate
    hyp_paras = [
        _normalize(p)[:100]
        for p in re.split(r"\n{2,}", _strip_md(hyp))
        if len(p.strip()) >= 20
    ]
    total_paras = len(hyp_paras)
    if total_paras > 1:
        seen: set[str] = set()
        dups = 0
        for p in hyp_paras:
            if p in seen:
                dups += 1
            seen.add(p)
        dup_rate = dups / total_paras
    else:
        dup_rate = 0.0

    # Duplicate Block Rate: fraction of blocks whose *content fingerprint* has
    # been seen before.  Using block-type alone would flag every second paragraph
    # in any normal document as a duplicate.
    hyp_seq = _block_type_sequence(hyp)
    hyp_block_fingerprints: list[str] = []
    for block in re.split(r"\n{2,}", _strip_md(hyp)):
        block = block.strip()
        if len(block) >= 10:
            hyp_block_fingerprints.append(_normalize(block)[:80])
    if len(hyp_block_fingerprints) > 1:
        fp_seen: set[str] = set()
        fp_dups = 0
        for fp in hyp_block_fingerprints:
            if fp in fp_seen:
                fp_dups += 1
            fp_seen.add(fp)
        dup_block_rate = fp_dups / len(hyp_block_fingerprints)
    else:
        dup_block_rate = 0.0

    # Header Leakage Rate: first non-blank text block in each page,
    # check if same text appears in > 40% of pages
    def _first_block(text: str) -> str:
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        # skip page header line
        lines = [l for l in lines if not re.match(r"^#{1,6}\s+P[áa]gina", l, re.IGNORECASE)]
        return lines[0][:60] if lines else ""

    def _last_block(text: str) -> str:
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        lines = [l for l in lines if not re.match(r"^#{1,6}\s+P[áa]gina", l, re.IGNORECASE)]
        return lines[-1][:60] if lines else ""

    n_pages = len(hyp_pages)
    if n_pages >= 3:
        first_blocks = [_first_block(v) for v in hyp_pages.values() if v.strip()]
        last_blocks = [_last_block(v) for v in hyp_pages.values() if v.strip()]

        def _repetition_rate(blocks: list[str], threshold: float = 0.35) -> float:
            if not blocks:
                return 0.0
            counter = Counter(b for b in blocks if b)
            n = len(blocks)
            repeated = sum(cnt for cnt in counter.values() if cnt / n >= threshold)
            return min(1.0, repeated / n)

        header_leakage = round(_repetition_rate(first_blocks), 4)
        footer_leakage = round(_repetition_rate(last_blocks), 4)
    else:
        header_leakage = 0.0
        footer_leakage = 0.0

    # Page Number Leakage Rate: standalone numbers that look like page numbers
    standalone_nums = len(re.findall(r"(?m)^\s*\d{1,4}\s*$", hyp))
    total_lines = hyp.count("\n") + 1
    page_num_leakage = round(min(1.0, standalone_nums / max(total_lines, 1)), 4)

    # Failure Rate: among selected pages (attempted + completely absent), how many
    # produced no content despite having reference content. Completely absent pages
    # (selected but never written to hypothesis at all) count as failures.
    absent: set[int] = selected_but_missing if selected_but_missing is not None else set()
    selected = set(hyp_pages.keys()) | absent
    n_ref_with_content = sum(
        1 for pn, v in ref_pages.items()
        if pn in selected and _strip_page_header(v).strip()
    )
    n_hyp_empty = sum(
        1 for pn, rv in ref_pages.items()
        if pn in selected
        and _strip_page_header(rv).strip()
        and not _strip_page_header(hyp_pages.get(pn, "")).strip()
    )
    failure_rate = round(n_hyp_empty / n_ref_with_content, 4) if n_ref_with_content > 0 else 0.0

    # Invalid Markdown Rate: pages with unclosed code fences or broken tables
    def _is_invalid(text: str) -> bool:
        fence_count = text.count("```")
        if fence_count % 2 != 0:
            return True
        table_lines = [l for l in text.split("\n") if "|" in l and l.strip().startswith("|")]
        if len(table_lines) > 1:
            col_counts = [l.count("|") for l in table_lines]
            if max(col_counts) - min(col_counts) > 2:
                return True
        return False

    invalid_pages = sum(1 for v in hyp_pages.values() if _is_invalid(v))
    invalid_md_rate = round(invalid_pages / len(hyp_pages), 4) if hyp_pages else 0.0

    return {
        "reading_order_accuracy": reading_order,
        "block_order_accuracy": reading_order,  # same proxy for now
        "duplicate_content_rate": round(dup_rate, 4),
        "duplicate_block_rate": round(dup_block_rate, 4),
        "header_leakage_rate": header_leakage,
        "footer_leakage_rate": footer_leakage,
        "page_number_leakage_rate": page_num_leakage,
        "failure_rate": failure_rate,
        "invalid_markdown_rate": invalid_md_rate,
    }


# ---------------------------------------------------------------------------
# Grupo 5 — Critical data
# ---------------------------------------------------------------------------

_NUM_RE = re.compile(r"\b\d+(?:[.,]\d+)+\b|\b\d{4,}\b")
_DATE_RE = re.compile(r"\b\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4}\b")
_CURRENCY_RE = re.compile(r"R\$\s*[\d\.,]+")
_CPF_RE = re.compile(r"\d{3}\.?\d{3}\.?\d{3}[-\s]?\d{2}")
_CNPJ_RE = re.compile(r"\d{2}\.?\d{3}\.?\d{3}/?\d{4}[-\s]?\d{2}")
_PROC_RE = re.compile(r"\d{7}[-\s.]?\d{2}[-\s.]?\d{4}[-\s.]?\d{1}[-\s.]?\d{2}[-\s.]?\d{4}")


def _exact_match_prf(hyp: str, ref: str, pattern: re.Pattern) -> tuple[float, float, float]:
    """Compute (precision, recall, F1) for regex pattern matches using multiset matching.

    Multiset matching preserves multiplicity: if ref contains R$10 twice and hyp
    contains R$10 once, only one match is credited.  Using set() would give recall=1.0
    even though half the occurrences are missing.  Precision penalises invented values.
    """
    ref_counts = Counter(pattern.findall(ref))
    hyp_counts = Counter(pattern.findall(hyp))
    if not ref_counts:
        # Nothing to find — perfect score by convention
        return 1.0, 1.0, 1.0
    tp = sum((ref_counts & hyp_counts).values())
    n_ref = sum(ref_counts.values())
    n_hyp = sum(hyp_counts.values())
    precision = round(tp / n_hyp, 4) if n_hyp > 0 else 0.0
    recall    = round(tp / n_ref, 4) if n_ref > 0 else 0.0
    f1        = round(2 * precision * recall / (precision + recall), 4) if (precision + recall) > 0 else 0.0
    return precision, recall, f1


def compute_critical_data_metrics(hyp: str, ref: str) -> dict:
    """Compute Group 5 critical data exact-match metrics on the full document.

    Checks preservation of numbers, dates, currency values (R$), and identifiers
    (CPF, CNPJ, process numbers) using multiset matching so that both missing and
    invented values are penalised.

    Keys ending in ``_exact_match`` are recall (backward-compatible with compare_engines.py).
    Keys ending in ``_precision`` and ``_f1`` are the additional multiset metrics.
    """
    num_p, num_r, num_f1   = _exact_match_prf(hyp, ref, _NUM_RE)
    date_p, date_r, date_f1 = _exact_match_prf(hyp, ref, _DATE_RE)
    cur_p, cur_r, cur_f1   = _exact_match_prf(hyp, ref, _CURRENCY_RE)

    ref_ids = Counter(_CPF_RE.findall(ref)) + Counter(_CNPJ_RE.findall(ref)) + Counter(_PROC_RE.findall(ref))
    hyp_ids = Counter(_CPF_RE.findall(hyp)) + Counter(_CNPJ_RE.findall(hyp)) + Counter(_PROC_RE.findall(hyp))
    id_tp  = sum((ref_ids & hyp_ids).values())
    id_ref = sum(ref_ids.values())
    id_hyp = sum(hyp_ids.values())
    id_p   = round(id_tp / id_hyp, 4) if id_hyp > 0 else (1.0 if not ref_ids else 0.0)
    id_r   = round(id_tp / id_ref, 4) if id_ref > 0 else 1.0
    id_f1  = round(2 * id_p * id_r / (id_p + id_r), 4) if (id_p + id_r) > 0 else 0.0

    return {
        # Recall — kept under original key names for backward compatibility with compare_engines.py
        "numeric_exact_match":    num_r,
        "date_exact_match":       date_r,
        "currency_exact_match":   cur_r,
        "identifier_exact_match": id_r,
        # Precision and F1 — additional multiset metrics
        "numeric_precision":      num_p,
        "numeric_f1":             num_f1,
        "date_precision":         date_p,
        "date_f1":                date_f1,
        "currency_precision":     cur_p,
        "currency_f1":            cur_f1,
        "identifier_precision":   id_p,
        "identifier_f1":          id_f1,
    }


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _mean(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def aggregate_page_metrics(per_page: list[dict]) -> dict:
    """Aggregate per-page metrics for evaluation reports and downstream scripts."""
    if not per_page:
        return {}

    keys = list(per_page[0].keys())
    result: dict = {}

    for key in keys:
        vals = [p[key] for p in per_page if isinstance(p.get(key), (int, float))]
        if vals:
            result[f"{key}_mean"] = round(_mean(vals), 6)
            result[f"{key}_median"] = round(sorted(vals)[len(vals) // 2], 6)
            result[f"{key}_worst"] = round(max(vals), 6) if "rate" not in key and "error" not in key and "leakage" not in key else round(max(vals), 6)

    return result


# ---------------------------------------------------------------------------
# Page parsing
# ---------------------------------------------------------------------------

def _parse_pages(text: str) -> dict[int, str]:
    """Split a Markdown document into a dict of {1-based page number → page text}, handling both '## Página 1' and '## Página 001 |' formats."""
    pattern = re.compile(
        r"^##\s+P[áa]gina\s+0*(\d+)",
        re.MULTILINE | re.IGNORECASE,
    )
    matches = list(pattern.finditer(text))
    if not matches:
        return {}
    pages: dict[int, str] = {}
    for i, m in enumerate(matches):
        num = int(m.group(1))
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        pages[num] = text[start:end].strip()
    return pages


def _load_reference_from_manifesto(manifesto_path: Path) -> dict[int, str]:
    """Load per-page expected_markdown from the corpus manifesto JSON, returning {page_number → expected_markdown}."""
    with open(manifesto_path, encoding="utf-8") as f:
        data = json.load(f)
    pages: dict[int, str] = {}
    for entry in data.get("pages", []):
        page_num = entry.get("page")
        expected_md = entry.get("expected_markdown", "")
        if page_num is not None and expected_md is not None:
            pages[int(page_num)] = expected_md
    return pages


# ---------------------------------------------------------------------------
# Error report
# ---------------------------------------------------------------------------

def _build_error_report(
    engine: str,
    run_id: str,
    per_page: list[dict],
    summary: dict,
) -> str:
    """Generate a Markdown error report listing global summary and the 20 worst pages by CER."""
    lines = [
        f"# Relatório de Erros — {engine}",
        f"Run: {run_id}",
        "",
        "## Resumo Global",
        "",
        "| Métrica | Valor |",
        "|---|---|",
    ]
    for k, v in summary.items():
        if isinstance(v, float):
            lines.append(f"| {k} | {v:.4f} |")
        else:
            lines.append(f"| {k} | {v} |")

    lines += [
        "",
        "## Páginas por CER Normalized (piores primeiras)",
        "",
    ]
    sorted_pages = sorted(per_page, key=lambda p: p.get("cer_normalized", 0.0), reverse=True)
    for entry in sorted_pages[:20]:
        pn = entry["page"]
        cer_n = entry.get("cer_normalized", 0.0)
        wer = entry.get("wer", 0.0)
        table_f1 = entry.get("table_f1", None)
        cond_value = entry.get("conditions", [])
        cond = ", ".join(cond_value) if isinstance(cond_value, list) else cond_value
        lines += [
            f"### Página {pn} — CER={cer_n:.4f} WER={wer:.4f}"
            + (f" [{cond}]" if cond else ""),
            "",
            f"- Substitutions: {entry.get('substitution_rate', 0):.4f}",
            f"- Deletions:     {entry.get('deletion_rate', 0):.4f}",
            f"- Insertions:    {entry.get('insertion_rate', 0):.4f}",
            f"- Heading F1:    {entry.get('heading_f1', 0):.4f}",
            f"- Block F1:      {entry.get('block_f1', 0):.4f}",
        ]
        if table_f1 is not None:
            lines.append(f"- Table F1:      {table_f1:.4f}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Per-page worker (top-level so ProcessPoolExecutor can pickle it on Windows)
# ---------------------------------------------------------------------------

def _process_page(args: tuple) -> dict:
    """Compute all metric groups for a single page.

    Must be a module-level function so ProcessPoolExecutor can pickle it on
    Windows (spawn start method). Each page is independent, so the results can
    be computed in any order and sorted by page number afterward.

    Args:
        args: (page_number, ref_content, hyp_content, page_metadata) —
            page_number is 1-based; ref/hyp_content are raw page-section
            strings (including the '## Página N' header); page_metadata maps page
            numbers to their family and complete list of conditions.

    Returns:
        {"pn": int, "acc": accumulated_counters_dict, "entry": per_page_metrics_dict}
    """
    pn, ref_content, hyp_content, page_metadata = args
    ref_body = _strip_page_header(ref_content)
    hyp_body = _strip_page_header(hyp_content)

    ref_raw = ref_body
    hyp_raw = hyp_body
    ref_n = _normalize(ref_body)
    hyp_n = _normalize(hyp_body)
    ref_t = _strip_md(ref_body)
    hyp_t = _strip_md(hyp_body)
    ref_w = ref_n.split()
    hyp_w = hyp_n.split()

    edits_raw  = _lev_distance(list(hyp_raw), list(ref_raw))
    edits_norm = _lev_distance(list(hyp_n),   list(ref_n))
    edits_text = _lev_distance(list(hyp_t),   list(ref_t))
    word_edits = _lev_distance(hyp_w, ref_w)
    S, D, I    = _lev_ops(hyp_w, ref_w)

    rc = max(len(ref_raw), 1)
    nc = max(len(ref_n), 1)
    tc = max(len(ref_t), 1)
    nw = max(len(ref_w), 1)

    wer_val = word_edits / nw
    text_m = {
        "cer_raw":           round(edits_raw  / rc, 6),
        "cer_normalized":    round(edits_norm / nc, 6),
        "cer_text_only":     round(edits_text / tc, 6),
        "wer":               round(wer_val, 6),
        "word_accuracy":     round(max(0.0, 1.0 - wer_val), 6),
        "substitution_rate": round(S / nw, 6),
        "deletion_rate":     round(D / nw, 6),
        "insertion_rate":    round(I / nw, 6),
        "omission_rate":     round(D / nw, 6),
    }
    struct_m = compute_structure_metrics(hyp_body, ref_body)
    table_m  = compute_table_metrics(hyp_body, ref_body)
    crit_m   = compute_critical_data_metrics(hyp_body, ref_body)

    entry = {
        "page": pn,
        "failure_rate": float(bool(ref_body.strip()) and not bool(hyp_body.strip())),
        "family": page_metadata.get(pn, {}).get("family"),
        "conditions": list(page_metadata.get(pn, {}).get("conditions", [])),
        **text_m,
        **struct_m,
        **table_m,
        **crit_m,
    }
    acc = {
        "chars_raw":  rc,  "edits_raw":  edits_raw,
        "chars_norm": nc,  "edits_norm": edits_norm,
        "chars_text": tc,  "edits_text": edits_text,
        "words":      nw,  "word_edits": word_edits,
        "S": S, "D": D, "I": I,
    }
    return {"pn": pn, "acc": acc, "entry": entry}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    """Entry point: compare extracted Markdown against ground truth and save metrics JSON + error report."""
    ap = argparse.ArgumentParser(
        description="Compute E2E quality metrics (Fase 8)."
    )
    ap.add_argument("--hypothesis", type=Path, required=True, help="Extracted Markdown file")
    ap.add_argument("--reference", type=Path, default=None, help="Reference Markdown file")
    ap.add_argument(
        "--manifesto", type=Path, default=None,
        help="Corpus manifesto JSON (preferred over --reference; contains per-page expected_markdown)"
    )
    ap.add_argument(
        "--run-manifest", type=Path, default=None,
        metavar="RUN_MANIFEST",
        help=(
            "E2E run manifest JSON (schema v2, output of evaluate_e2e.py). "
            "When provided, pages listed as 'selected' in the run manifest but "
            "absent from the hypothesis receive a maximum CER/WER penalty instead "
            "of being silently excluded from quality metrics."
        ),
    )
    ap.add_argument("--engine", default="unknown")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--output-dir", type=Path, default=Path("output/fase8"))
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if not args.hypothesis.exists():
        print(f"ERROR: hypothesis file not found: {args.hypothesis}", file=sys.stderr)
        return 1

    if args.manifesto is None and args.reference is None:
        print("ERROR: must provide --manifesto or --reference", file=sys.stderr)
        return 1

    run_id = args.run_id or args.hypothesis.stem
    args.output_dir.mkdir(parents=True, exist_ok=True)
    engine_slug = args.engine.replace("-", "_")
    metrics_path = args.output_dir / f"metrics_{engine_slug}_{run_id}.json"
    errors_path = args.output_dir / f"errors_{engine_slug}_{run_id}.md"

    if not args.quiet:
        print(f"=== Compute Metrics — {args.engine} ===")
        print(f"  Hypothesis : {args.hypothesis}")
        print(f"  Source     : {args.manifesto or args.reference}")

    hyp_text = args.hypothesis.read_text(encoding="utf-8")
    hyp_pages = _parse_pages(hyp_text)

    # A separate Markdown reference takes precedence; the manifesto can then
    # provide page conditions and run metadata even when it has no reference text.
    ref_pages: dict[int, str] = {}
    if args.reference and args.reference.exists():
        ref_text = args.reference.read_text(encoding="utf-8")
        ref_pages = _parse_pages(ref_text)
    elif args.manifesto and args.manifesto.exists():
        ref_pages = _load_reference_from_manifesto(args.manifesto)
    else:
        print("ERROR: reference Markdown or manifesto not found", file=sys.stderr)
        return 1

    if not ref_pages:
        print("ERROR: no pages found in reference", file=sys.stderr)
        return 1

    if not args.quiet:
        print(f"  Ref pages  : {len(ref_pages)}")
        print(f"  Hyp pages  : {len(hyp_pages)}")

    # Load conditions metadata from manifesto (for error report)
    page_metadata: dict[int, dict] = {}
    if args.manifesto and args.manifesto.exists():
        with open(args.manifesto, encoding="utf-8") as f:
            mdata = json.load(f)
        for entry in mdata.get("pages", []):
            pn = entry.get("page")
            if pn:
                page_metadata[int(pn)] = {
                    "family": entry.get("family"),
                    "conditions": list(entry.get("conditions", []) or []),
                }

    # --- Determine the selected page set ---
    # When a run manifest (schema v2 from evaluate_e2e.py) is available, the
    # "pages" list there tells us exactly which pages the engine was asked to
    # process.  Pages selected but absent from the hypothesis are penalised with
    # maximum CER/WER (all reference characters counted as deletions) rather than
    # silently excluded from quality metrics.
    #
    # Without a run manifest we fall back to the reference page set — every
    # reference page is treated as selected, which is conservative but correct.
    run_manifest_pages: set[int] | None = None
    _run_meta: dict = {}
    run_page_meta: dict[int, dict] = {}
    if args.run_manifest and args.run_manifest.exists():
        with open(args.run_manifest, encoding="utf-8") as f:
            _rm = json.load(f)
        run_manifest_pages = {
            int(e["page"])
            for e in _rm.get("pages", [])
            if e.get("page") is not None
        }
        run_page_meta = {
            int(page["page"]): page for page in _rm.get("pages", [])
            if page.get("page") is not None
        }
        # Propagate E2E run metadata so compare_engines.py can validate runs
        # and display benchmark_status without needing to re-read the manifest.
        _run_meta = {
            "benchmark_protocol_id": _rm.get("benchmark_protocol_id"),
            "benchmark_status":  _rm.get("benchmark_status"),
            "document_status":   _rm.get("document_status"),
            "pdf_sha256":        _rm.get("pdf_sha256"),
            "mode":              _rm.get("mode"),
            "engine_identity":   _rm.get("engine_identity", {}),
            "degraded_pages":    _rm.get("degraded_pages", []),
            "failed_ocr_pages":  _rm.get("failed_ocr_pages", []),
            "recovered_pages":   _rm.get("recovered_pages", []),
            "recovery_count":    _rm.get("recovery_count", 0),
            "fallback_rate":     _rm.get("fallback_rate", 0.0),
            "stability_status":  _rm.get("stability_status"),
            "elapsed_s":         _rm.get("elapsed_s"),
            "timings":           _rm.get("timings", {}),
            "memory":            _rm.get("memory", {}),
            "selected_pages":    sorted(run_manifest_pages) if run_manifest_pages else [],
        }

    # Selected = pages the engine was supposed to produce output for.
    selected_pages: set[int] = run_manifest_pages if run_manifest_pages is not None else set(ref_pages.keys())

    # Pages that are in the reference and selected but completely absent from the
    # hypothesis output — these receive maximum penalty, not a free pass.
    selected_ref_pages: set[int] = selected_pages & ref_pages.keys()
    selected_but_missing: set[int] = selected_ref_pages - hyp_pages.keys()
    in_hyp_not_selected: set[int] = hyp_pages.keys() - selected_pages

    # Zero-overlap guard: fail clearly rather than producing NaN metrics.
    eval_page_nums_set: set[int] = (hyp_pages.keys() & ref_pages.keys()) | selected_but_missing
    if not eval_page_nums_set:
        print(
            "ERROR: hypothesis and reference have zero comparable pages. "
            "Check --hypothesis, --reference/--manifesto, and --run-manifest.",
            file=sys.stderr,
        )
        return 1

    # --- Compute per-page metrics (diagnostics) + accumulate for micro-average ---
    # Pages present in both hypothesis and reference: evaluated normally.
    # Pages selected but missing from hypothesis: contribute max-penalty to text
    # accumulators (all ref chars as deletions, all ref words as deletions).
    per_page_results: list[dict] = []
    all_page_nums = sorted(hyp_pages.keys() & ref_pages.keys())

    # Accumulators for group 1 micro-average (weighted by ref length)
    _t_chars_raw = _t_edits_raw = 0
    _t_chars_norm = _t_edits_norm = 0
    _t_chars_text = _t_edits_text = 0
    _t_words = _t_word_edits = 0
    _t_S = _t_D = _t_I = 0

    total_pages = len(all_page_nums)
    if not args.quiet:
        print(f"  Processando {total_pages} páginas em paralelo "
              f"(workers={os.cpu_count()})...")

    if total_pages == 0 and not selected_but_missing:
        print(
            "ERROR: hypothesis and reference have zero comparable pages.",
            file=sys.stderr,
        )
        return 1

    page_args = [
        (pn, ref_pages[pn], hyp_pages.get(pn, ""), page_metadata)
        for pn in all_page_nums
    ]
    results = []
    if page_args:
        with ProcessPoolExecutor(max_workers=os.cpu_count()) as executor:
            futures = {executor.submit(_process_page, arg): arg[0] for arg in page_args}
            done = 0
            for future in as_completed(futures):
                results.append(future.result())
                done += 1
                if not args.quiet:
                    print(f"\r  {done}/{total_pages} páginas processadas...", end="", flush=True)
    if not args.quiet:
        print()  # newline after progress output

    results.sort(key=lambda r: r["pn"])

    for r in results:
        a = r["acc"]
        _t_chars_raw  += a["chars_raw"];  _t_edits_raw  += a["edits_raw"]
        _t_chars_norm += a["chars_norm"]; _t_edits_norm += a["edits_norm"]
        _t_chars_text += a["chars_text"]; _t_edits_text += a["edits_text"]
        _t_words      += a["words"];      _t_word_edits += a["word_edits"]
        _t_S += a["S"]; _t_D += a["D"]; _t_I += a["I"]
        per_page_results.append(r["entry"])

    # --- Penalise selected pages that are missing from hypothesis ---
    # All reference characters count as deletions; all reference words as deletions.
    for pn in sorted(selected_but_missing):
        ref_text = ref_pages[pn]
        ref_body = _strip_page_header(ref_text)
        ref_norm = _normalize(ref_text)
        ref_stripped = _normalize(_strip_md(ref_text))
        n_chars_raw = max(1, len(ref_text))
        n_chars_norm = max(1, len(ref_norm))
        n_chars_text = max(1, len(ref_stripped))
        n_words = max(1, len(ref_stripped.split()))
        _t_chars_raw  += n_chars_raw;  _t_edits_raw  += n_chars_raw
        _t_chars_norm += n_chars_norm; _t_edits_norm += n_chars_norm
        _t_chars_text += n_chars_text; _t_edits_text += n_chars_text
        _t_words      += n_words;      _t_word_edits += n_words
        _t_D += n_words  # all reference words are deletions
        per_page_results.append({
            "page": pn,
            "missing_from_hypothesis": True,
            "selected_but_missing": True,
            "family": page_metadata.get(pn, {}).get("family"),
            "conditions": list(page_metadata.get(pn, {}).get("conditions", [])),
            "cer_raw": 1.0,
            "cer_normalized": 1.0,
            "cer_text_only": 1.0,
            "wer": 1.0,
            "failure_rate": float(bool(_strip_page_header(ref_text).strip())),
            "ref_chars": n_chars_raw,
            "hyp_chars": 0,
            **compute_structure_metrics("", ref_body),
            **compute_table_metrics("", ref_body),
            **compute_critical_data_metrics("", ref_body),
        })

    for page_result in per_page_results:
        run_page = run_page_meta.get(page_result["page"], {})
        page_result["stability_status"] = run_page.get("stability_status", "unknown")
        page_result["easyocr_fallback_count"] = int(run_page.get("easyocr_fallback_count", 0))
        page_result["easyocr_fallback_rate"] = float(run_page.get("easyocr_fallback_rate", 0.0))
        page_result["missing_page_rate"] = float(bool(page_result.get("missing_from_hypothesis")))

    # --- Group 1: full-document text (micro-average, weighted by ref length) ---
    if _t_chars_raw == 0 or _t_words == 0:
        print("ERROR: zero reference characters/words after page accumulation.", file=sys.stderr)
        return 1

    _wer_val = _t_word_edits / _t_words
    full_text_m = {
        "cer_raw": round(_t_edits_raw / _t_chars_raw, 6),
        "cer_normalized": round(_t_edits_norm / _t_chars_norm, 6),
        "cer_text_only": round(_t_edits_text / _t_chars_text, 6),
        "wer": round(_wer_val, 6),
        "word_accuracy": round(max(0.0, 1.0 - _wer_val), 6),
        "substitution_rate": round(_t_S / _t_words, 6),
        "deletion_rate": round(_t_D / _t_words, 6),
        "insertion_rate": round(_t_I / _t_words, 6),
        "omission_rate": round(_t_D / _t_words, 6),
    }

    # --- Groups 2, 3, 4, 5: full-document Markdown ---
    # Structure must include every selected reference page. A missing page has
    # an empty hypothesis so its expected headings, lists and blocks are FNs.
    hyp_full_body, ref_full_body = _selected_document_bodies(
        hyp_pages, ref_pages, selected_ref_pages
    )

    # Groups 3 and 5 (tables, critical data): include selected-but-missing pages
    # so that absent tables and critical values are penalised (ref content present,
    # hypothesis contributes empty string).
    penalised_page_nums = sorted((hyp_pages.keys() & ref_pages.keys()) | selected_but_missing)
    hyp_penalised_body = "\n\n".join(
        _strip_page_header(hyp_pages[pn]) if pn in hyp_pages else ""
        for pn in penalised_page_nums
    )
    ref_penalised_body = "\n\n".join(
        _strip_page_header(ref_pages[pn])
        for pn in penalised_page_nums
    )

    if not args.quiet:
        print("  Calculando métricas estruturais e de tabelas (full-doc)...")

    full_struct_m = compute_structure_metrics(hyp_full_body, ref_full_body)
    # Table metrics: aggregate from per-page results (already page-paired) rather
    # than from the concatenated document, which would shift pairings across pages.
    full_table_m = aggregate_table_metrics_from_pages(per_page_results, selected_but_missing, ref_pages)
    full_crit_m = compute_critical_data_metrics(hyp_penalised_body, ref_penalised_body)
    integrity_m = compute_integrity_metrics(
        hyp_full_body, ref_full_body, hyp_pages, ref_pages, selected_but_missing
    )

    def _breakdown(key_name: str) -> dict:
        groups: dict[str, list[dict]] = {}
        for page in per_page_results:
            values = page.get(key_name) or []
            if isinstance(values, str):
                values = [values] if values else []
            for value in values:
                groups.setdefault(str(value), []).append(page)
        output = {}
        for name, pages in sorted(groups.items()):
            output[name] = {
                "pages": len(pages),
                "cer_text_only_macro": round(sum(p.get("cer_text_only", 1.0) for p in pages) / len(pages), 6),
                "wer_macro": round(sum(p.get("wer", 1.0) for p in pages) / len(pages), 6),
                "missing_pages": sum(bool(p.get("missing_from_hypothesis")) for p in pages),
                "table_f1_macro": round(sum(p.get("table_f1", 1.0) for p in pages) / len(pages), 6),
                "currency_f1_macro": round(sum(p.get("currency_f1", 1.0) for p in pages) / len(pages), 6),
                "identifier_f1_macro": round(sum(p.get("identifier_f1", 1.0) for p in pages) / len(pages), 6),
            }
        return output

    # Correct set-difference page counts (fixes the cardinality-subtraction bug).
    pages_in_ref_not_hyp = sorted(ref_pages.keys() - hyp_pages.keys())
    pages_in_hyp_not_ref = sorted(hyp_pages.keys() - ref_pages.keys())
    reference_path = (
        args.reference
        if args.reference and args.reference.exists()
        else args.manifesto
    )
    reference_sha256 = _sha256_file(reference_path)
    manifest_sha256 = _sha256_file(args.manifesto)

    summary = {
        "engine": args.engine,
        "run_id": run_id,
        "pages_evaluated": len(per_page_results),
        "pages_reference": len(ref_pages),
        "pages_in_hypothesis": len(hyp_pages),
        "pages_selected": len(selected_pages),
        "selected_pages": sorted(selected_pages),
        "pages_selected_and_present": len(selected_ref_pages & hyp_pages.keys()),
        "pages_selected_but_missing": sorted(selected_but_missing),
        "pages_selected_but_missing_count": len(selected_but_missing),
        "pages_in_hypothesis_not_selected": sorted(in_hyp_not_selected),
        "pages_in_ref_not_hyp": pages_in_ref_not_hyp,
        "pages_in_hyp_not_ref": pages_in_hyp_not_ref,
        "missing_pages_penalised": len(selected_but_missing) > 0,
        # Top-level shorthands so compare_engines.py can read them directly
        # without needing to access the nested "run" block.
        "benchmark_status": _run_meta.get("benchmark_status"),
        "stability_status": _run_meta.get("stability_status"),
        "recovered_pages": _run_meta.get("recovered_pages", []),
        "recovery_count": _run_meta.get("recovery_count", 0),
        "fallback_rate": _run_meta.get("fallback_rate", 0.0),
        "timings": _run_meta.get("timings", {}),
        "memory": _run_meta.get("memory", {}),
        "pdf_sha256": _run_meta.get("pdf_sha256"),
        "reference_sha256": reference_sha256,
        "manifest_sha256": manifest_sha256,
        "mode": _run_meta.get("mode"),
        "run": _run_meta if _run_meta else None,
        "grupo1_texto": full_text_m,
        "grupo2_estrutura_markdown": full_struct_m,
        "grupo3_tabelas": full_table_m,
        "grupo4_ordem_integridade": integrity_m,
        "grupo5_dados_criticos": full_crit_m,
        "per_page": per_page_results,
        "by_family": _breakdown("family"),
        "by_condition": _breakdown("conditions"),
    }

    # --- Save metrics JSON ---
    from structured_pdf_text.atomic_io import atomic_write_json, atomic_write_text
    atomic_write_json(metrics_path, summary)

    # --- Build error report for summary ---
    flat_summary = {
        **summary["grupo1_texto"],
        **summary["grupo2_estrutura_markdown"],
        **summary["grupo3_tabelas"],
        **integrity_m,
        **summary["grupo5_dados_criticos"],
    }
    error_report = _build_error_report(args.engine, run_id, per_page_results, flat_summary)
    atomic_write_text(errors_path, error_report)

    if not args.quiet:
        g1 = summary["grupo1_texto"]
        print()
        print("  === Resultado ===")
        print(f"  CER Normalized  : {g1.get('cer_normalized', 0):.4f}")
        print(f"  CER Text Only   : {g1.get('cer_text_only', 0):.4f}")
        print(f"  WER             : {g1.get('wer', 0):.4f}")
        print(f"  Word Accuracy   : {g1.get('word_accuracy', 0):.4f}")
        g2 = summary["grupo2_estrutura_markdown"]
        print(f"  Heading F1      : {g2.get('heading_f1', 0):.4f}")
        print(f"  Block F1        : {g2.get('block_f1', 0):.4f}")
        print(f"  AST Similarity  : {g2.get('markdown_ast_similarity', 0):.4f}")
        g3 = summary["grupo3_tabelas"]
        print(f"  Table F1        : {g3.get('table_f1', 0):.4f}")
        print(f"  Cell Exact Match: {g3.get('cell_exact_match', 0):.4f}")
        g4 = integrity_m
        print(f"  Reading Order   : {g4.get('reading_order_accuracy', 0):.4f}")
        print(f"  Failure Rate    : {g4.get('failure_rate', 0):.4f}")
        g5 = summary["grupo5_dados_criticos"]
        print(f"  Numeric Match   : {g5.get('numeric_exact_match', 0):.4f}")
        print(f"  Currency Match  : {g5.get('currency_exact_match', 0):.4f}")
        print()
        print(f"  Metrics : {metrics_path}")
        print(f"  Errors  : {errors_path}")
        print()
        print("  Próximo passo — comparar engines:")
        print(f"  python scripts\\compare_engines.py \\")
        print(f"    {metrics_path} \\")
        print(f"    [outros metrics_*.json] \\")
        print(f"    --output {args.output_dir}\\comparison_table.md")

    return 0


if __name__ == "__main__":
    sys.exit(main())
