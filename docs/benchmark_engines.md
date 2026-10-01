# OCR Engine Comparative Benchmark

This document describes the E2E (end-to-end) benchmark system implemented in
Phase 8 of the project. The benchmark evaluates the quality of text extraction and
Markdown structure from five different OCR engines, using a stress test corpus with
manual ground truth.

---

## Motivation

The project supports multiple OCR engines through the `OCRBackend` contract
(`src/structured_pdf_text/ocr/contracts.py`). Each engine has distinct characteristics
in terms of speed, quality, and dependencies. The benchmark enables:

- objectively comparing the quality of text extraction (CER, WER)
- evaluating the fidelity of the generated Markdown structure (headings, tables, lists)
- identifying which scanning conditions (low DPI, noise, rotation) each
  engine handles better
- quantifying trade-offs between speed and quality for deployment decisions

---

## Evaluated Engines

| Engine | Mode | Backend | Speed (32p corpus) | Dependencies |
|---|---|---|---|---|
| **PaddleOCR** | `paddle` | PaddlePaddle | ~94 s/page | `paddlepaddle`, `paddleocr` |
| **RapidOCR ONNX** | `rapidocr-onnx` | ONNX Runtime | ~2.76 s/page | `rapidocr-onnxruntime` |
| **RapidOCR OpenVINO** | `rapidocr-openvino` | Intel OpenVINO | ~0.63 s/page | `rapidocr-openvino` |
| **Tesseract 5** | `tesseract` | subprocess + TSV | ~0.58 s/page | `tesseract` (binary) |
| **EasyOCR** | `easyocr` | PyTorch CPU | ~5.4 s/page | `easyocr`, `torch` |

> The times above are from a RAW benchmark on 32 dense pages (pp. 72–103 of
> the corpus). The full E2E benchmark includes native pages (without OCR), which
> significantly reduces the total time for fast engines.

### Engine Selection in the API

```python
from structured_pdf_text.api import PdfTextExtractor
from structured_pdf_text.config import best_extraction_config
from dataclasses import replace

config = best_extraction_config(language="pt", preserve_headers=False)

# Switch engine:
config = replace(config, ocr_engine="tesseract")   # or "rapidocr-onnx", "easyocr", etc.

extractor = PdfTextExtractor(config)
document = extractor.extract("documento.pdf")
```

### Engine Selection in the CLI

Use `--ocr-engine` in the `pdftext extract` command:

```bash
# PaddleOCR (default — requires setup-models)
pdftext extract documento.pdf --mode balanced --ocr-model-profile pt --output markdown

# Tesseract (requires tesseract binary in PATH)
pdftext extract documento.pdf --mode balanced --ocr-engine tesseract --output markdown

# RapidOCR ONNX (no model download required)
pdftext extract documento.pdf --mode balanced --ocr-engine rapidocr-onnx --output markdown

# RapidOCR OpenVINO
pdftext extract documento.pdf --mode balanced --ocr-engine rapidocr-openvino --output markdown

# EasyOCR (downloads models on first use)
pdftext extract documento.pdf --mode balanced --ocr-engine easyocr --output markdown
```

Non-Paddle engines do not use `--ocr-model-profile`. The `--mode balanced` maintains
the fallback logic (native-first → OCR only on pages that need it).

---

## Engine Installation

The script `scripts/setup_ocr_benchmark.ps1` automates installation on Windows.

```powershell
.\scripts\setup_ocr_benchmark.ps1
```

### Manual Installation

```powershell
# PaddleOCR
pip install paddlepaddle paddleocr

# RapidOCR ONNX
pip install rapidocr-onnxruntime

# RapidOCR OpenVINO (install without dependencies to avoid numpy conflict)
pip install openvino
pip install rapidocr-openvino --no-deps

# EasyOCR (PyTorch CPU)
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install easyocr

# Fix numpy conflict (CF-3)
pip install "numpy==2.0.2"
```

---

## Known Issues

| ID | Description | Status |
|---|---|---|
| CF-1 | **oneDNN disabled by default on CPU** — Paddle 3.x may fail on the PIR/oneDNN path on Linux/WSL and Windows. `PADDLE_ENABLE_MKLDNN=1` is explicit opt-in; `FLAGS_enable_pir_api=False` remains applied on Windows. | Safe runtime policy |
| CF-2 | **Incompatible DLL `paddle2onnx`** — 0xC0000139 incompatibility on Windows. RapidOCR uses the built-in PP-OCRv4 instead of converting Paddle models. | Worked around |
| CF-3 | **numpy conflict** — EasyOCR forces numpy ≥ 2.5.x; OpenVINO < 2.1.0 requires numpy < 2.x. Fix: `pip install "numpy==2.0.2"` | ✅ Applied |

---

## Metric Groups

The benchmark computes five metric groups over the **complete Markdown document**
(not per individual page). The `per_page` array in the output JSON contains
per-page metrics for diagnostics.

### Group 1 — Text

Measures the fidelity of the extracted text against the ground truth.

| Metric | Description |
|---|---|
| `cer_raw` | CER without normalization, preserving Markdown differences, spaces, and line breaks. |
| `cer_normalized` | Character Error Rate after normalization (NFC, CRLF→LF, spaces). Primary OCR quality metric. |
| `cer_text_only` | CER with Markdown removed — isolates recognition error from structuring error. |
| `wer` | Word Error Rate — sensitive to word segmentation errors. |
| `word_accuracy` | 1 − WER |
| `substitution_rate` | Rate of words substituted by an incorrect one. |
| `deletion_rate` | Rate of omitted words. |
| `insertion_rate` | Rate of incorrectly inserted words. |
| `omission_rate` | Same as deletion_rate (lost content). |

> **Calculation**: micro-average weighted by reference length per page — equivalent
> to comparing the entire document without the quadratic cost of Levenshtein over
> millions of characters.

### Group 2 — Markdown Structure

Evaluates whether the document structure (headings, blocks, lists) was preserved.

| Metric | Description |
|---|---|
| `heading_f1` | F1 for heading detection by normalized text. |
| `heading_level_accuracy` | Fraction of detected headings with the correct level (#, ##, ###). |
| `heading_text_cer` | Average CER of the text in detected headings. |
| `block_f1` | F1 of block types (heading, paragraph, table, list, code) as a multiset. |
| `paragraph_boundary_f1` | F1 of paragraph boundaries (proxy: paragraph count). |
| `list_detection_f1` | F1 for list block detection. |
| `markdown_ast_similarity` | LCS of block type sequence / maximum length — global structural similarity. |

> **Calculation**: computed over the full concatenated Markdown (excluding the synthetic
> `## Página N` headers added by the renderer).

### Group 3 — Tables

Evaluates the fidelity of extracted GFM tables (pipe tables).

| Metric | Description |
|---|---|
| `table_f1` | F1 for table detection (count). |
| `row_f1` | F1 of rows within tables matched by position. |
| `column_f1` | F1 of columns within matched tables. |
| `table_dimension_accuracy` | Fraction of tables with exact dimensions (N rows × M columns). |
| `cell_exact_match` | Fraction of cells with content identical to the reference (position [row, column]). |
| `cell_cer` | Average CER per cell. |
| `cell_alignment_accuracy` | Fraction of cells at the correct [row, column] position. |
| `table_structure_similarity` | Geometric mean of row_f1 and col_f1. |

### Group 4 — Order and Integrity

Evaluates structural issues that do not depend on OCR quality.

| Metric | Description |
|---|---|
| `reading_order_accuracy` | LCS of text blocks in document order / total reference blocks. |
| `block_order_accuracy` | Same proxy as reading_order_accuracy. |
| `duplicate_content_rate` | Fraction of repeated paragraphs in the extracted document. |
| `duplicate_block_rate` | Fraction of consecutively repeated block types. |
| `header_leakage_rate` | Fraction of pages whose first block is identical in > 35% of pages (header leaking). |
| `footer_leakage_rate` | Same for the last block (footer). |
| `page_number_leakage_rate` | Rate of lines containing only a number (page number leaking). |
| `failure_rate` | Fraction of pages with content in the reference that produced empty output. |
| `invalid_markdown_rate` | Fraction of pages with malformed Markdown (unclosed fences, broken tables). |

### Group 5 — Critical Data

Evaluates the preservation of structured entities with specific semantics, via
regex over the full document.

| Metric | Description |
|---|---|
| `numeric_exact_match` | Fraction of numbers (integers, decimals, percentages) from the reference found in the hypothesis. |
| `date_exact_match` | Fraction of dates (dd/mm/yyyy and variants) preserved. |
| `currency_exact_match` | Fraction of monetary values (R$) preserved. |
| `identifier_exact_match` | Fraction of CPF, CNPJ, and case numbers preserved. |

---

## Scripts Pipeline

```
PDF + Ground Truth
       │
       ▼
┌─────────────────────┐
│  evaluate_e2e.py    │  Extracts with the chosen engine; saves Markdown + manifest
└──────────┬──────────┘
           │  extracted_{engine}_{run_id}.md
           │  manifesto_e2e_{engine}_{run_id}.json
           ▼
┌─────────────────────┐
│  compute_metrics.py │  Compares against ground truth; computes the 5 metric groups
└──────────┬──────────┘
           │  metrics_{engine}_{run_id}.json
           │  errors_{engine}_{run_id}.md
           ▼
┌─────────────────────┐
│  compare_engines.py │  Aggregates JSONs from all engines; generates comparison table
└──────────┬──────────┘
           │  comparison_{suffix}.md
           ▼
       Result
```

---

## How to Run

### Prerequisite

```powershell
pip install "numpy==2.0.2"   # CF-3: fixes EasyOCR/OpenVINO conflict
```

### Smoke Test — 5 Pages, All Engines

```powershell
.\scripts\run_benchmark.ps1
```

Default parameters: pages 77–81, suffix `smoke`.

### Full Document

```powershell
.\scripts\run_benchmark.ps1 -RunSuffix "v1" -AllPages
```

### Single Engine or Subset (PowerShell)

Use the `-Engine` parameter to limit execution to one or more engines:

```powershell
# Single engine — full
.\scripts\run_benchmark.ps1 -Engine tesseract -AllPages -RunSuffix "v2"

# Two engines — full
.\scripts\run_benchmark.ps1 -Engine "easyocr,tesseract" -AllPages -RunSuffix "v2"

# Single engine — smoke test
.\scripts\run_benchmark.ps1 -Engine paddle -RunSuffix "v2-smoke"
```

The comparison table (`compare_engines.py`) is generated with the engines that completed
successfully. If only one engine was run, the table contains a single column.

### Single Engine (Linux / WSL)

Use `--engines` for the Bash runner:

```bash
# Single engine — full
scripts/run_benchmark.sh --engines tesseract --all-pages --run-suffix wsl-v2

# Two engines — smoke test
scripts/run_benchmark.sh --engines "easyocr,tesseract" --run-suffix wsl-smoke
```

### Linux / WSL

```bash
# Install OCR runtimes in .venv and Tesseract in a prefix without sudo
scripts/setup_ocr_benchmark.sh

# Smoke test on Document AI V3 corpus (pages 77–81)
scripts/run_benchmark.sh --run-suffix wsl-smoke

# Full document
scripts/run_benchmark.sh --run-suffix wsl-v1 --all-pages
```

The Bash runner uses `corpus/Document_AI_V3.pdf`, `corpus/Document_AI_V3.md`, and
`corpus/Document_AI_V3_MANIFESTO.json` by default. The Markdown provides the reference
text; the manifest provides per-page metadata. Both can be overridden with `--pdf`,
`--reference`, and `--manifesto`.

### Single Engine (PowerShell, single line)

```powershell
# Extraction
python scripts\evaluate_e2e.py "corpus\Corpus_Stress_OCR_Markdown_V4.pdf" --engine tesseract --pages 1-5 --run-id smoke-tesseract --output-dir output\fase8

# Metrics
python scripts\compute_metrics.py --hypothesis output\fase8\extracted_tesseract_smoke-tesseract.md --manifesto "corpus\Corpus_Stress_OCR_Markdown_V4_MANIFESTO.json" --engine tesseract --run-id smoke-tesseract --output-dir output\fase8

# Comparison table (after running multiple engines)
python scripts\compare_engines.py output\fase8\metrics_*_smoke-*.json --output output\fase8\comparison_smoke.md
```

### PowerShell Script Parameters

| Parameter | Default | Description |
|---|---|---|
| `-Engine` | `""` (all) | Engine(s) to run. Accepts a single engine (`tesseract`) or CSV list (`"easyocr,tesseract"`). Omitting runs all five engines. |
| `-Pages` | `"77-81"` | Page range for smoke test |
| `-AllPages` | (switch) | Process full document |
| `-RunSuffix` | `"smoke"` | Output file suffix |
| `-Corpus` | *(default path)* | PDF path |
| `-Manifesto` | *(default path)* | JSON manifest path |
| `-OutDir` | `"output\fase8"` | Output directory |

---

## Output Files

All files are saved to `output/fase8/` by default.

| File | Generated by | Content |
|---|---|---|
| `extracted_{engine}_{run_id}.md` | `evaluate_e2e.py` | Markdown extracted by the E2E pipeline |
| `manifesto_e2e_{engine}_{run_id}.json` | `evaluate_e2e.py` | Run metadata (git SHA, engine identity, time per page) |
| `metrics_{engine}_{run_id}.json` | `compute_metrics.py` | All metric groups + `per_page` array |
| `errors_{engine}_{run_id}.md` | `compute_metrics.py` | Error report: 20 worst pages by CER |
| `comparison_{suffix}.md` | `compare_engines.py` | Side-by-side comparison table of all engines |

### Metrics JSON Structure

```json
{
  "engine": "tesseract",
  "run_id": "v1-tesseract",
  "pages_evaluated": 224,
  "pages_reference": 224,
  "pages_missing_in_hypothesis": 0,
  "grupo1_texto": {
    "cer_normalized": 0.042,
    "cer_text_only": 0.038,
    "wer": 0.091,
    "word_accuracy": 0.909,
    "substitution_rate": 0.031,
    "deletion_rate": 0.048,
    "insertion_rate": 0.012,
    "omission_rate": 0.048
  },
  "grupo2_estrutura_markdown": { ... },
  "grupo3_tabelas": { ... },
  "grupo4_ordem_integridade": { ... },
  "grupo5_dados_criticos": { ... },
  "per_page": [
    { "page": 1, "cer_normalized": 0.01, "heading_f1": 1.0, ... },
    ...
  ]
}
```

---

## Script Pipeline Performance

### compute_metrics.py — parallel processing

Metric calculation uses `ProcessPoolExecutor` to process each page in
parallel (one worker per CPU core). Pages are independent of each other,
so the results are identical to the sequential version.

| Condition | Time (224 pages) |
|---|---|
| Sequential (before Phase 9) | ~60 min |
| Parallel — 12 cores | ~10 min |

On Windows, `ProcessPoolExecutor` uses the `spawn` start method. The worker
function `_process_page` must be picklable (defined at module level, outside
`main()`), which is guaranteed by the current implementation.

---

## Test Corpus

**File**: `corpus/Corpus_Stress_OCR_Markdown_V4.pdf` — 224 pages

The corpus was designed to cover the scanning conditions found in real documents.
It includes native pages (directly extractable text) and OCR pages with controlled
variations:

| Condition | Description |
|---|---|
| `native_dense` | High-density native text (extraction without OCR) |
| `scan_clean_300` | Clean scan at 300 DPI |
| `scan_clean_200` | Clean scan at 200 DPI |
| `low_dpi` | Low-resolution scan |
| `noise` | Scan noise |
| `blur` | Blur |
| `skew` | Page skew |
| `rotation` | Rotation (90°, 180°) |
| `hybrid` | Combination of conditions |
| `adversarial` | Extreme conditions |

The ground truth file (`corpus/Corpus_Stress_OCR_Markdown_V4_MANIFESTO.json`)
contains the `expected_markdown` field per page — the expected Markdown output
for each condition.

---

## Required Files to Run the Benchmark

The pipeline requires three input files. The PDF can be any document;
the other two must be created manually or generated from an existing corpus.

```
corpus/
  MeuCorpus.pdf                   ← PDF to be extracted
  MeuCorpus_MANIFESTO.json        ← ground truth + per-page metadata  (preferred)
  MeuCorpus_REFERENCIA.md         ← simplified alternative to the manifest
```

> **Note:** The *corpus* manifest (ground truth) is different from the *run*
> manifest generated by `evaluate_e2e.py`. The run manifest describes the extraction
> result (time, engine, PDF hash). The corpus manifest describes what was expected.

---

### File 1 — Corpus PDF

Any valid PDF. The pipeline accepts native documents (selectable text),
scans, and hybrids. Native pages are extracted without OCR; raster pages
go through the selected engine.

Path passed as the first argument to `evaluate_e2e.py`:

```powershell
python scripts\evaluate_e2e.py corpus\MeuCorpus.pdf --engine tesseract ...
```

---

### File 2 — Corpus JSON Manifest (preferred)

**Format**: JSON with a `"pages"` key containing an array of objects, one per page.

```json
{
  "corpus": "MeuCorpus v1",
  "pages": [
    {
      "page": 1,
      "conditions": ["native_dense"],
      "expected_markdown": "## Página 1\n\nTexto esperado da página 1...\n\n| Col A | Col B |\n|---|---|\n| val | val |"
    },
    {
      "page": 2,
      "conditions": ["scan_clean_300"],
      "expected_markdown": "## Página 2\n\n# Título\n\nTexto esperado..."
    },
    {
      "page": 3,
      "conditions": ["noise", "low_dpi"],
      "expected_markdown": "## Página 3\n\nConteúdo esperado da página com ruído..."
    }
  ]
}
```

**Required fields per entry:**

| Field | Type | Description |
|---|---|---|
| `page` | integer | Page number (1-based) |
| `expected_markdown` | string | Ground truth Markdown for that page |

**Optional fields:**

| Field | Type | Description |
|---|---|---|
| `conditions` | list of strings | Scanning conditions for the page (used only in the error report) |

**Rules for `expected_markdown`:**

1. Must start with `## Página N` (the number must match `"page"`).
   `compute_metrics.py` uses this header to pair the reference with the hypothesis.
2. Headings use `#` / `##` / `###` — the level is evaluated by the `heading_level_accuracy` metric.
3. Tables must be GFM pipe tables to be detected by the table metrics.
4. Lists with `-`, `*`, or `1.` are detected by the `list_detection_f1` metric.
5. Do not include repeated page headers (document footer/header) — they must be
   absent from the ground truth for `header_leakage_rate` to work correctly.

**Passed to `compute_metrics.py` via `--manifesto`:**

```powershell
python scripts\compute_metrics.py \
  --hypothesis output\fase8\extracted_tesseract_v1-tesseract.md \
  --manifesto corpus\MeuCorpus_MANIFESTO.json \
  --engine tesseract --run-id v1-tesseract --output-dir output\fase8
```

---

### File 3 — Markdown Reference (simplified alternative)

When there is no manifest, `compute_metrics.py` accepts a plain Markdown
file with a `## Página N` section for each page. Per-page conditions
**will not be available** in the error report, but all metrics are
computed normally.

**Format:**

```markdown
## Página 1

Texto esperado da primeira página. Pode conter headings, tabelas e listas.

## Página 2

# Título da Seção

Texto esperado da segunda página.

| Coluna A | Coluna B |
|---|---|
| Valor 1  | Valor 2  |

## Página 3

Texto esperado...
```

**Rules:**

- The page separator is `## Página N` (also accepts `## Pagina N` without accent,
  case-insensitive, leading zeros such as `## Página 001`).
- There are no other required fields — the file is plain Markdown.
- Pages absent from the reference file are ignored; pages present in the
  reference but absent in the hypothesis increment `failure_rate`.

**Passed via `--reference`:**

```powershell
python scripts\compute_metrics.py \
  --hypothesis output\fase8\extracted_tesseract_v1-tesseract.md \
  --reference corpus\MeuCorpus_REFERENCIA.md \
  --engine tesseract --run-id v1-tesseract --output-dir output\fase8
```

---

### Using a custom corpus with run_benchmark.ps1

The script accepts corpus, manifest, and output directory paths via parameters:

```powershell
.\scripts\run_benchmark.ps1 `
  -Corpus    "corpus\MeuCorpus.pdf" `
  -Manifesto "corpus\MeuCorpus_MANIFESTO.json" `
  -OutDir    "output\meu-corpus" `
  -RunSuffix "v1" `
  -AllPages
```

For the Bash runner (Linux/WSL):

```bash
scripts/run_benchmark.sh \
  --pdf      corpus/MeuCorpus.pdf \
  --manifesto corpus/MeuCorpus_MANIFESTO.json \
  --output-dir output/meu-corpus \
  --run-suffix v1 \
  --all-pages
```

---

### How to generate the ground truth

There is no automatic tool — `expected_markdown` must be written or
reviewed manually. The recommended workflow:

1. Extract the PDF with the best available engine in native mode:
   ```bash
   pdftext extract MeuCorpus.pdf --output markdown -o MeuCorpus_DRAFT.md
   ```
2. Review the generated Markdown page by page, correcting OCR errors and structure.
3. Create the JSON manifest with the corrected Markdown as the `expected_markdown` for each page.
4. Add `conditions` fields to indicate the type of each page (optional,
   but useful for diagnosing which conditions each engine struggles with).

---

### Summary — what each file affects

| File | Required | Affects |
|---|---|---|
| PDF | yes | extraction by the selected engine |
| JSON Manifest (`--manifesto`) | one of the two | ground truth + per-page conditions |
| Markdown Reference (`--reference`) | one of the two | ground truth (without conditions) |

---

## Performance Results (RAW Benchmark — 32 dense pages)

> Measured on pp. 72–103 of the corpus (32 dense OCR pages). The full E2E pipeline
> produces different results since it includes native pages (much faster).

| Engine | Total time | s/page | Status |
|---|---|---|---|
| PaddleOCR (without oneDNN — CF-1) | 3018 s | ~94 s | 32/32 OK |
| RapidOCR ONNX (PP-OCRv4 — CF-2) | 88 s | ~2.76 s | 32/32 OK |
| RapidOCR OpenVINO (PP-OCRv4 — CF-2) | 20 s | ~0.63 s | 32/32 OK |
| Tesseract 5 (por, PSM 3) | 18 s | ~0.58 s | 32/32 OK |
| EasyOCR (latin_g2, CPU) | 173 s | ~5.4 s | 32/32 OK |

The quality metrics table (`comparison_v1.md`) is populated after
running the full E2E benchmark.

---

## Per-Engine Optimizations (Phase 9)

Each backend received specific optimizations after analyzing the Phase 8 results.
Full details are in [`Plano_Comparativo_Paddle.md`](../Plano_Comparativo_Paddle.md).

### Tesseract

| Parameter | Effect |
|---|---|
| `--dpi` calculated from `ocr_render_scale` | Fixes high deletion_rate (silent wrong DPI) |
| `textord_min_linesize=2.5` | Fixes PT diacritics read as a separate line (bug #4276) |
| `tessedit_char_blacklist=\`` | Eliminates backtick that breaks Markdown fences |
| `textord_noise_rej{rows,words}=0` | Preserves valid text classified as noise |
| `crunch_del_rating=40` | Preserves more borderline tokens |
| `preserve_interword_spaces=1` | Maintains separation between table columns |
| CLAHE grayscale preprocessing | Improves scans with uneven lighting |

Configurable environment variables: `TESSERACT_LANG`, `TESSERACT_PSM`, `TESSERACT_OEM`,
`TESSERACT_DPI`, `TESSERACT_TESSDATA_DIR`, `TESSERACT_CONF_MIN`.

### RapidOCR ONNX and OpenVINO

| Parameter | Phase 9 value | Previous value |
|---|---|---|
| `det_db_unclip_ratio` | `1.8` | `1.6` |
| `det_db_box_thresh` | `0.45` | `0.5` |
| `det_db_thresh` | `0.25` | `0.3` |
| CLAHE LAB L-channel | enabled | — |

CLAHE is applied to the L channel of the LAB color space (preserving color
information) before passing the image to the detector. Environment variables:
`RAPIDOCR_UNCLIP_RATIO`, `RAPIDOCR_BOX_THRESH`, `RAPIDOCR_DET_THRESH`,
`RAPIDOCR_TEXT_SCORE`, `RAPIDOCR_ANGLE_CLS`, `RAPIDOCR_REC_MODEL`, `RAPIDOCR_REC_KEYS`.

### EasyOCR

| Parameter | Effect |
|---|---|
| Separate detect + recognize pipeline | Allows configuring both stages independently |
| `canvas_size=max(h,w)` | Prevents CRAFT downscaling on large pages |
| `mag_ratio=1.5` | Improves detection of small text |
| `decoder='beamsearch'` | Fewer substitution errors on ambiguous characters |
| `adjust_contrast=1.0` | Contrast recovery in faded regions |
| CLAHE before recognition | Improves low-contrast scans |

Environment variables: `EASYOCR_MODULE_PATH`, `EASYOCR_RECOG_NETWORK`, `EASYOCR_BEAMWIDTH`,
`EASYOCR_WORKERS`, `EASYOCR_ALLOWLIST`, `EASYOCR_BLOCKLIST`.

---

## References

- OCR contract: [`src/structured_pdf_text/ocr/contracts.py`](../src/structured_pdf_text/ocr/contracts.py)
- Backends: [`src/structured_pdf_text/ocr/backends/`](../src/structured_pdf_text/ocr/backends/)
- Base diagnostics: [`src/structured_pdf_text/diagnostics/ocr_metrics.py`](../src/structured_pdf_text/diagnostics/ocr_metrics.py)
- Implementation plan: [`Plano_Comparativo_Paddle.md`](../Plano_Comparativo_Paddle.md)
