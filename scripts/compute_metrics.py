#!/usr/bin/env python3
"""
Compute E2E metrics — Fase 8.

Compares extracted Markdown against the reference ground truth and computes
the minimum metric set (section 50 of metricas_avaliacao_parser_ocr_markdown.md):

  Grupo 1 — Texto
  Grupo 2 — Estrutura Markdown
  Grupo 3 — Tabelas
  Grupo 4 — Ordem e integridade
  Grupo 5 — Dados críticos

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
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

_SRC = Path(__file__).parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

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
    """Compute all Group 1 text-quality metrics.

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

    # Heading Text CER: CER on matched heading texts
    heading_text_cer_vals: list[float] = []
    for level, text in hyp_headings:
        norm = _normalize(text)
        if norm in ref_h_map:
            # Compare with the original ref heading text
            for rl, rt in ref_headings:
                if _normalize(rt) == norm:
                    heading_text_cer_vals.append(_cer_normalized(text, rt))
                    break
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


def _cell_cer(hyp_cell: str, ref_cell: str) -> float:
    """Compute character error rate for a single table cell."""
    ref_n = _normalize_cell(ref_cell)
    if not ref_n:
        return 0.0
    return _lev_distance(list(_normalize_cell(hyp_cell)), list(ref_n)) / len(ref_n)


def compute_table_metrics(hyp: str, ref: str) -> dict:
    """Compute Group 3 GFM table metrics on the full document body.

    Evaluates table detection F1, row/column F1, dimension accuracy,
    cell exact match, cell CER, cell alignment accuracy, and table structure similarity.
    """
    ref_tables = _parse_md_tables(ref)
    hyp_tables = _parse_md_tables(hyp)

    n_ref = len(ref_tables)
    n_hyp = len(hyp_tables)
    t_tp = min(n_ref, n_hyp)
    t_fp = max(0, n_hyp - n_ref)
    t_fn = max(0, n_ref - n_hyp)
    _, _, table_f1 = _f1(t_tp, t_fp, t_fn)

    if not ref_tables or not hyp_tables:
        return {
            "table_f1": table_f1,
            "row_f1": 1.0 if (not ref_tables and not hyp_tables) else 0.0,
            "column_f1": 1.0 if (not ref_tables and not hyp_tables) else 0.0,
            "table_dimension_accuracy": 1.0 if (not ref_tables and not hyp_tables) else 0.0,
            "cell_exact_match": 1.0 if (not ref_tables and not hyp_tables) else 0.0,
            "cell_cer": 0.0,
            "cell_alignment_accuracy": 1.0 if (not ref_tables and not hyp_tables) else 0.0,
            "table_structure_similarity": 1.0 if (not ref_tables and not hyp_tables) else 0.0,
        }

    # For matched table pairs (by position order)
    row_f1s: list[float] = []
    col_f1s: list[float] = []
    dim_accs: list[float] = []
    cell_matches: list[float] = []
    cell_cers: list[float] = []
    alignment_accs: list[float] = []
    struct_sims: list[float] = []

    for ref_t, hyp_t in zip(ref_tables, hyp_tables):
        # Row F1
        n_ref_rows = len(ref_t)
        n_hyp_rows = len(hyp_t)
        r_tp = min(n_ref_rows, n_hyp_rows)
        r_fp = max(0, n_hyp_rows - n_ref_rows)
        r_fn = max(0, n_ref_rows - n_hyp_rows)
        _, _, row_f1 = _f1(r_tp, r_fp, r_fn)
        row_f1s.append(row_f1)

        # Column F1: use max column count per row
        n_ref_cols = max((len(r) for r in ref_t), default=0)
        n_hyp_cols = max((len(r) for r in hyp_t), default=0)
        c_tp = min(n_ref_cols, n_hyp_cols)
        c_fp = max(0, n_hyp_cols - n_ref_cols)
        c_fn = max(0, n_ref_cols - n_hyp_cols)
        _, _, col_f1 = _f1(c_tp, c_fp, c_fn)
        col_f1s.append(col_f1)

        # Table Dimension Accuracy: exact match on (rows, cols)
        dim_accs.append(1.0 if (n_ref_rows == n_hyp_rows and n_ref_cols == n_hyp_cols) else 0.0)

        # Cell metrics: iterate matching (row, col) positions
        total_cells = 0
        exact_matches = 0
        cer_sum = 0.0
        aligned = 0

        for i, ref_row in enumerate(ref_t):
            hyp_row = hyp_t[i] if i < len(hyp_t) else []
            for j, ref_cell in enumerate(ref_row):
                total_cells += 1
                hyp_cell = hyp_row[j] if j < len(hyp_row) else ""
                ref_norm = _normalize_cell(ref_cell)
                hyp_norm = _normalize_cell(hyp_cell)
                if ref_norm == hyp_norm:
                    exact_matches += 1
                    aligned += 1
                cer_sum += _cell_cer(hyp_cell, ref_cell)

        if total_cells > 0:
            cell_matches.append(exact_matches / total_cells)
            cell_cers.append(cer_sum / total_cells)
            alignment_accs.append(aligned / total_cells)
        else:
            cell_matches.append(1.0)
            cell_cers.append(0.0)
            alignment_accs.append(1.0)

        # Table Structure Similarity: geometric mean of row_f1 and col_f1
        struct = (row_f1 * col_f1) ** 0.5 if (row_f1 >= 0 and col_f1 >= 0) else 0.0
        struct_sims.append(struct)

    def _mean(lst: list[float]) -> float:
        return sum(lst) / len(lst) if lst else 0.0

    return {
        "table_f1": table_f1,
        "row_f1": round(_mean(row_f1s), 4),
        "column_f1": round(_mean(col_f1s), 4),
        "table_dimension_accuracy": round(_mean(dim_accs), 4),
        "cell_exact_match": round(_mean(cell_matches), 4),
        "cell_cer": round(_mean(cell_cers), 6),
        "cell_alignment_accuracy": round(_mean(alignment_accs), 4),
        "table_structure_similarity": round(_mean(struct_sims), 4),
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

    # Duplicate Block Rate (at block-type sequence level)
    hyp_seq = _block_type_sequence(hyp)
    if len(hyp_seq) > 1:
        seq_seen: set[str] = set()
        seq_dups = 0
        for item in hyp_seq:
            if item in seq_seen:
                seq_dups += 1
            seq_seen.add(item)
        dup_block_rate = seq_dups / len(hyp_seq)
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

    # Failure Rate: among attempted pages (present in hyp), how many came out empty
    # despite having content in the reference. Partial runs are not penalized for
    # pages that were never attempted.
    attempted = set(hyp_pages.keys())
    n_ref_with_content = sum(
        1 for pn, v in ref_pages.items()
        if pn in attempted and _strip_page_header(v).strip()
    )
    n_hyp_empty = sum(
        1 for pn, rv in ref_pages.items()
        if pn in attempted
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


def _exact_match_rate(hyp: str, ref: str, pattern: re.Pattern) -> float:
    """Fraction of regex matches in ref that also appear in hyp."""
    ref_vals = set(pattern.findall(ref))
    if not ref_vals:
        return 1.0
    hyp_vals = set(pattern.findall(hyp))
    found = ref_vals & hyp_vals
    return round(len(found) / len(ref_vals), 4)


def compute_critical_data_metrics(hyp: str, ref: str) -> dict:
    """Compute Group 5 critical data exact-match metrics on the full document.

    Checks preservation of numbers, dates, currency values (R$), and identifiers
    (CPF, CNPJ, process numbers) via regex set intersection.
    """
    identifiers_ref = set(_CPF_RE.findall(ref)) | set(_CNPJ_RE.findall(ref)) | set(_PROC_RE.findall(ref))
    identifiers_hyp = set(_CPF_RE.findall(hyp)) | set(_CNPJ_RE.findall(hyp)) | set(_PROC_RE.findall(hyp))
    id_rate = round(
        len(identifiers_ref & identifiers_hyp) / len(identifiers_ref), 4
    ) if identifiers_ref else 1.0

    return {
        "numeric_exact_match": _exact_match_rate(hyp, ref, _NUM_RE),
        "date_exact_match": _exact_match_rate(hyp, ref, _DATE_RE),
        "currency_exact_match": _exact_match_rate(hyp, ref, _CURRENCY_RE),
        "identifier_exact_match": id_rate,
    }


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _mean(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def aggregate_page_metrics(per_page: list[dict]) -> dict:
    """Compute mean, median, and worst value for each numeric metric across all per-page results."""
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
        cond = entry.get("conditions", "")
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

    # Load reference pages
    ref_pages: dict[int, str] = {}
    if args.manifesto and args.manifesto.exists():
        ref_pages = _load_reference_from_manifesto(args.manifesto)
    elif args.reference and args.reference.exists():
        ref_text = args.reference.read_text(encoding="utf-8")
        ref_pages = _parse_pages(ref_text)
    else:
        print("ERROR: reference source not found", file=sys.stderr)
        return 1

    if not ref_pages:
        print("ERROR: no pages found in reference", file=sys.stderr)
        return 1

    if not args.quiet:
        print(f"  Ref pages  : {len(ref_pages)}")
        print(f"  Hyp pages  : {len(hyp_pages)}")

    # Load conditions metadata from manifesto (for error report)
    page_conditions: dict[int, str] = {}
    if args.manifesto and args.manifesto.exists():
        with open(args.manifesto, encoding="utf-8") as f:
            mdata = json.load(f)
        for entry in mdata.get("pages", []):
            pn = entry.get("page")
            conds = entry.get("conditions", [])
            if pn and conds:
                page_conditions[int(pn)] = ", ".join(conds[:2])

    # --- Compute per-page metrics (diagnostics) + accumulate for micro-average ---
    # Only evaluate pages present in both hypothesis and reference.
    # Pages not extracted (e.g. in a partial smoke test) are not penalized here;
    # failure_rate in compute_integrity_metrics accounts for them separately.
    per_page_results: list[dict] = []
    all_page_nums = sorted(hyp_pages.keys() & ref_pages.keys())

    # Accumulators for group 1 micro-average (weighted by ref length)
    _t_chars_norm = _t_edits_norm = 0
    _t_chars_text = _t_edits_text = 0
    _t_words = _t_word_edits = 0
    _t_S = _t_D = _t_I = 0

    for pn in all_page_nums:
        ref_content = ref_pages[pn]
        hyp_content = hyp_pages.get(pn, "")
        ref_body = _strip_page_header(ref_content)
        hyp_body = _strip_page_header(hyp_content)

        # Compute Levenshtein once; feed both per-page diagnostics and accumulators
        ref_n = _normalize(ref_body)
        hyp_n = _normalize(hyp_body)
        ref_t = _strip_md(ref_body)
        hyp_t = _strip_md(hyp_body)
        ref_w = ref_n.split()
        hyp_w = hyp_n.split()

        edits_norm = _lev_distance(list(hyp_n), list(ref_n))
        edits_text = _lev_distance(list(hyp_t), list(ref_t))
        word_edits = _lev_distance(hyp_w, ref_w)
        S, D, I = _lev_ops(hyp_w, ref_w)

        nc = max(len(ref_n), 1)
        tc = max(len(ref_t), 1)
        nw = max(len(ref_w), 1)

        _t_chars_norm += nc; _t_edits_norm += edits_norm
        _t_chars_text += tc; _t_edits_text += edits_text
        _t_words += nw; _t_word_edits += word_edits
        _t_S += S; _t_D += D; _t_I += I

        wer_val = word_edits / nw
        text_m = {
            "cer_raw": round(_cer_raw(hyp_body, ref_body), 6),
            "cer_normalized": round(edits_norm / nc, 6),
            "cer_text_only": round(edits_text / tc, 6),
            "wer": round(wer_val, 6),
            "word_accuracy": round(max(0.0, 1.0 - wer_val), 6),
            "substitution_rate": round(S / nw, 6),
            "deletion_rate": round(D / nw, 6),
            "insertion_rate": round(I / nw, 6),
            "omission_rate": round(D / nw, 6),
        }

        struct_m = compute_structure_metrics(hyp_body, ref_body)
        table_m = compute_table_metrics(hyp_body, ref_body)
        crit_m = compute_critical_data_metrics(hyp_body, ref_body)

        page_entry = {
            "page": pn,
            "conditions": page_conditions.get(pn, ""),
            **text_m,
            **struct_m,
            **table_m,
            **crit_m,
        }
        per_page_results.append(page_entry)

        if not args.quiet:
            print(
                f"  P{pn:03d} CER={text_m['cer_normalized']:.3f} "
                f"WER={text_m['wer']:.3f} "
                f"TF1={table_m['table_f1']:.3f}",
                end="\r",
            )

    if not args.quiet:
        print()

    # --- Group 1: full-document text (micro-average, weighted by ref length) ---
    _wer_val = _t_word_edits / _t_words
    full_text_m = {
        "cer_normalized": round(_t_edits_norm / _t_chars_norm, 6),
        "cer_text_only": round(_t_edits_text / _t_chars_text, 6),
        "wer": round(_wer_val, 6),
        "word_accuracy": round(max(0.0, 1.0 - _wer_val), 6),
        "substitution_rate": round(_t_S / _t_words, 6),
        "deletion_rate": round(_t_D / _t_words, 6),
        "insertion_rate": round(_t_I / _t_words, 6),
        "omission_rate": round(_t_D / _t_words, 6),
    }

    # --- Groups 2, 3, 4, 5: full-document Markdown (evaluated pages only) ---
    eval_page_nums = sorted(hyp_pages.keys() & ref_pages.keys())
    hyp_full_body = "\n\n".join(
        _strip_page_header(hyp_pages[pn])
        for pn in eval_page_nums
    )
    ref_full_body = "\n\n".join(
        _strip_page_header(ref_pages[pn])
        for pn in eval_page_nums
    )

    if not args.quiet:
        print("  Calculando métricas estruturais e de tabelas (full-doc)...")

    full_struct_m = compute_structure_metrics(hyp_full_body, ref_full_body)
    full_table_m = compute_table_metrics(hyp_full_body, ref_full_body)
    full_crit_m = compute_critical_data_metrics(hyp_full_body, ref_full_body)
    integrity_m = compute_integrity_metrics(
        hyp_full_body, ref_full_body, hyp_pages, ref_pages
    )

    summary = {
        "engine": args.engine,
        "run_id": run_id,
        "pages_evaluated": len(per_page_results),
        "pages_reference": len(ref_pages),
        "pages_missing_in_hypothesis": len(ref_pages) - len(hyp_pages),
        "grupo1_texto": full_text_m,
        "grupo2_estrutura_markdown": full_struct_m,
        "grupo3_tabelas": full_table_m,
        "grupo4_ordem_integridade": integrity_m,
        "grupo5_dados_criticos": full_crit_m,
        "per_page": per_page_results,
    }

    # --- Save metrics JSON ---
    metrics_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # --- Build error report for summary ---
    flat_summary = {
        **summary["grupo1_texto"],
        **summary["grupo2_estrutura_markdown"],
        **summary["grupo3_tabelas"],
        **integrity_m,
        **summary["grupo5_dados_criticos"],
    }
    error_report = _build_error_report(args.engine, run_id, per_page_results, flat_summary)
    errors_path.write_text(error_report, encoding="utf-8")

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
