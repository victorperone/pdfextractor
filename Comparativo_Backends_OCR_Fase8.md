# OCR Backend Comparative Analysis — Phase 8 (Run v2)

**Execution date:** 2026-10-01  
**Corpus:** `Corpus_Stress_OCR_Markdown_V4.pdf` — 224 pages  
**SHA256:** `45c09f2f03eb0dfe99bef5b8ea5b1ee686351cae2fc1244b4dcfce98fd7d0970`  
**Extraction mode:** `balanced` | Language: `pt`  
**Run platform:** Windows Server (WSL2 for metrics computation)  
**Run suffix:** `v2`  
**Output directory:** `output/fase8/V2/`

---

## Executive Summary

| Engine | CER ↓ | WER ↓ | Table Cell Exact ↑ | Failure Rate ↓ | Total Time ↓ | Weighted Score ↑ |
|---|---|---|---|---|---|---|
| **RapidOCR-OpenVINO** | **0.3633** | 0.5979 | 0.4089 | **0.0089** | 617 s | **61.6** |
| **Tesseract** | 0.5094 | 0.7808 | 0.3297 | 0.0179 | **707 s** | **59.9** |
| **RapidOCR-ONNX** | 0.3669 | 0.5981 | 0.4092 | **0.0089** | 2283 s | **57.8** |
| **EasyOCR** | 0.4069 | 0.5081 | 0.3049 | **0.0089** | 3517 s | **56.6** |
| **Paddle** | 0.6344 | 0.6324 | **0.5942** | ⚠️ 0.4955 | 316 s | **49.8** |

> Weighted score (0–100) uses: CER 25 %, Markdown structure 15 %, cell quality 15 %, reliability 20 %, critical data 15 %, speed 10 %.  
> **Paddle shows a critical anomaly**: a failure_rate of 49.55 % and insertion_rate of 60.8 % invalidate a large portion of its text results in this benchmark.

---

## 1. Context and Benchmark Configuration

### 1.1 Stress Corpus

`Corpus_Stress_OCR_Markdown_V4.pdf` deliberately covers the most demanding PDF extraction scenarios across 224 pages:

- Dense text with multiple columns (1, 2, 3 columns)
- Tables with borders, borderless, financial, high numeric precision, multiline rows, empty cells
- Source code and lists
- Inline formatting (bold, italic, underline)
- Native (vector) and scanned text
- Handwritten, rotated, low-quality text
- Critical data: numbers, dates, monetary values, identifiers

Pages 1–8 are predominantly native text (no OCR required). Pages 9–224 are OCR-heavy scan scenarios.

### 1.2 Engine Versions

| Engine | Main package | Version | Runtime | Notes |
|---|---|---|---|---|
| Paddle | paddlepaddle | 3.3.1 | `paddle_subprocess` | paddleocr 3.7.0, paddlex 3.7.2 — isolated subprocess to avoid DLL conflict with torch on Windows |
| EasyOCR | easyocr | 1.7.2 | `torch-cpu` | torch 2.14.0+cpu — CPU-only |
| RapidOCR-ONNX | rapidocr-onnxruntime | 1.4.4 | `onnxruntime` | onnxruntime 1.30.0 |
| RapidOCR-OpenVINO | rapidocr-openvino | 1.4.4 | `openvino` | openvino 2024.4.0 — benefits from Intel hardware |
| Tesseract | tesseract-cli | v5.5.3 | `tesseract-cli` | tessdata `por`, PSM 3, OEM 1 (LSTM+Legacy) |

### 1.3 Coverage

All 5 backends processed 224 pages with zero process failures (`pages_missing: 0` for all). Differences lie in output quality and logical extraction failures (`failure_rate`).

---

## 2. Performance — Processing Speed

| Engine | Total Time (s) | Total Time (min) | Avg per Page (s/page) | Variability |
|---|---|---|---|---|
| Paddle | **316.4** | **5.3** | **1.41** | Low — consistent ~1–2 s/page |
| RapidOCR-OpenVINO | 616.8 | 10.3 | 2.74 | Low/Medium — 1–12 s/page |
| Tesseract | 707.0 | 11.8 | 3.16 | Medium — 1–12 s/page |
| RapidOCR-ONNX | 2283.4 | 38.1 | 10.19 | **Very High** — peaks of 15–60 s/page on pages 80–144 |
| EasyOCR | 3516.7 | **58.6** | 15.65 | High — peaks up to 72 s/page on pages 81–184 |

### 2.1 Speed Analysis

**Paddle** is the absolute speed winner: 11× faster than EasyOCR. This results from PP-OCRv6 medium running in an isolated subprocess leveraging PaddlePaddle's CPU optimisations.

**RapidOCR-OpenVINO** is a comfortable second (2.2× slower than Paddle). Intel OpenVINO hardware acceleration significantly reduces neural model inference time. On non-Intel hardware (AMD, ARM), expect performance to degrade toward the ONNX variant.

**Tesseract** occupies a reasonable middle ground (2.2× slower than Paddle, 5× faster than EasyOCR) with no heavy deep-learning framework dependencies.

**RapidOCR-ONNX** underperforms despite sharing the same model as OpenVINO. It is 3.7× slower because ONNXRuntime without hardware acceleration degrades badly on visually complex pages (page-count peaks on pages 80–184 — the same hotspot visible in EasyOCR, pointing to high visual complexity rather than model inefficiency).

**EasyOCR** is the slowest by a wide margin (58.6 min for 224 pages). Torch CPU inference is inherently heavy. Not viable for batch production workloads without GPU.

> **Practical implication:** For any interactive or batch production use on CPU-only hardware, EasyOCR and RapidOCR-ONNX are not viable without GPU. Only OpenVINO and Paddle have acceptable CPU throughput.

---

## 3. Group 1 — Text Quality (CER / WER)

### 3.1 Full Metrics Table

| Metric | EasyOCR | RapidOCR-ONNX | RapidOCR-OpenVINO | Tesseract | Paddle |
|---|---|---|---|---|---|
| **CER Raw** ↓ | 0.4065 | 0.3667 | **0.3631** | 0.5172 | 0.6343 |
| **CER Normalized** ↓ | 0.4069 | 0.3669 | **0.3633** | 0.5094 | 0.6344 |
| **CER Text Only** ↓ | 0.4068 | 0.3673 | **0.3637** | 0.5110 | 0.6342 |
| **WER** ↓ | **0.5081** | 0.5981 | 0.5979 | 0.7808 | 0.6324 |
| **Word Accuracy** ↑ | **0.4919** | 0.4019 | 0.4021 | 0.2192 | 0.3676 |
| **Substitution Rate** ↓ | 0.2343 | 0.0725 | 0.0745 | 0.2020 | **0.0063** |
| **Deletion Rate** ↓ | 0.0457 | 0.0235 | 0.0233 | 0.3147 | **0.0182** |
| **Insertion Rate** ↓ | 0.2281 | 0.5022 | 0.5001 | **0.1290** | 0.6079 ⚠️ |
| **Omission Rate** ↓ | 0.0457 | 0.0235 | 0.0233 | 0.3147 | **0.0182** |

### 3.2 Per-Page CER Distribution

| Engine | CER p10 | CER p50 (median) | CER p90 | CER p95 | Pages < 10 % ✅ | Pages > 60 % ❌ |
|---|---|---|---|---|---|---|
| RapidOCR-OpenVINO | 0.027 | 0.420 | 0.808 | 1.000 | 49 / 224 | **67 / 224** |
| RapidOCR-ONNX | 0.027 | 0.418 | 0.822 | 1.093 | 49 / 224 | 68 / 224 |
| EasyOCR | 0.030 | 0.496 | 0.995 | 1.119 | 43 / 224 | 77 / 224 |
| Tesseract | 0.030 | 0.438 | 1.005 | 1.540 | 51 / 224 | 88 / 224 |
| Paddle | 0.027 | **0.962** | 1.000 | 1.000 | 49 / 224 | **121 / 224** |

The p50 (median) column is the most revealing: Paddle's median page CER is 96.2 % — meaning more than half of all pages are essentially garbled. Every other engine has a median between 42–50 %, which is poor but not catastrophic.

### 3.3 Native vs OCR Page Split

| Engine | Avg CER pages 1–8 (native) | Avg CER pages 9–224 (OCR-heavy) |
|---|---|---|
| RapidOCR-OpenVINO | 0.154 | **0.448** |
| RapidOCR-ONNX | 0.154 | 0.456 |
| EasyOCR | 0.184 | 0.506 |
| Tesseract | 0.184 | 0.564 |
| Paddle | 0.154 | 0.630 |

All engines achieve similar CER on native-text pages (0.154–0.184), confirming the pipeline's native extraction path works comparably. The difference is entirely in OCR quality on scanned pages.

### 3.4 Error Type Decomposition

| Engine | Primary error source | Interpretation |
|---|---|---|
| **EasyOCR** | Substitutions (22.8 %) + Insertions (22.8 %) | Confuses visually similar characters; also generates spurious tokens |
| **RapidOCR-ONNX / OpenVINO** | **Insertions (50.0–50.2 %)** ⚠️ | Massive hallucination: the model generates words absent from the reference |
| **Tesseract** | Deletions (31.5 %) + Substitutions (20.2 %) | Loses much text (small fonts, low quality); also misreads what it does capture |
| **Paddle** | **Insertions (60.8 %)** ⚠️ | Worst-case hallucination: over half of generated words have no match in the reference |

> **Important:** The high insertion_rate in Paddle (60.8 %) and RapidOCR variants (~50 %) indicates these backends are **duplicating or fabricating content**, not just misreading characters. This is more dangerous than substitution errors because it introduces false information into the extracted document.

### 3.5 Ranking — Group 1

1. **RapidOCR-OpenVINO** — CER 36.3 %, fewest terrible pages (67), best p90
2. **RapidOCR-ONNX** — CER 36.7 %, virtually identical to OpenVINO
3. **EasyOCR** — WER 50.8 % (best WER), CER 40.7 %
4. **Tesseract** — CER 50.9 %, high deletion rate
5. **Paddle** — CER 63.4 %, catastrophic median page CER (96.2 %)

---

## 4. Group 2 — Markdown Structure

### 4.1 Full Metrics Table

| Metric | EasyOCR | RapidOCR-ONNX | RapidOCR-OpenVINO | Tesseract | Paddle |
|---|---|---|---|---|---|
| **Heading F1** ↑ | 0.0919 | 0.0925 | 0.0925 | 0.0932 | **0.1070** |
| **Heading Level Accuracy** ↑ | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| **Heading Text CER** ↓ | **0.0000** | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| **Block F1** ↑ | **0.4526** | 0.3763 | 0.3741 | 0.4446 | 0.2156 |
| **Paragraph Boundary F1** ↑ | **0.6357** | 0.5653 | 0.5633 | **0.6357** | 0.3885 |
| **List Detection F1** ↑ | 0.7273 | 0.7273 | 0.7273 | **0.7778** | 0.7273 |
| **Markdown AST Similarity** ↑ | 0.2950 | 0.2333 | 0.2313 | **0.2904** | 0.1293 |

### 4.2 Analysis

**Heading detection** is universally weak (F1 < 0.11) across all backends. `heading_level_accuracy = 0.0000` for all engines means no backend successfully identifies heading hierarchy — OCR does not preserve semantic heading structure. Paddle leads slightly (0.1070) but the absolute value is too low to be meaningful.

**Block F1** reveals a bimodal split: Paddle (~0.22) vs. all others (~0.37–0.45). EasyOCR and Tesseract lead (0.45). Paddle's massive insertion rate destroys block boundaries.

**Paragraph Boundary F1**: EasyOCR and Tesseract tie for first (0.636). RapidOCR variants are noticeably weaker (0.563–0.565), suggesting their high insertion rate breaks paragraph structure.

**List Detection F1**: Tesseract wins (0.778). Others tie at 0.727. Tesseract's word-level approach may better preserve list markers even when general text quality is lower.

**AST Similarity** (holistic document structure score): Tesseract leads (0.290), closely followed by EasyOCR (0.295 — effectively tied). RapidOCR variants trail (0.231–0.233). Paddle is far behind (0.129).

### 4.3 Ranking — Group 2

1. **EasyOCR** — best block_f1 (0.453), tied best paragraph_boundary (0.636)
2. **Tesseract** — tied best paragraph_boundary, best list_f1 (0.778), best AST similarity
3. **RapidOCR-ONNX** — mid-tier across all metrics
4. **RapidOCR-OpenVINO** — almost identical to ONNX
5. **Paddle** — significantly weaker on all structural metrics

---

## 5. Group 3 — Table Quality

### 5.1 Full Metrics Table

| Metric | EasyOCR | RapidOCR-ONNX | RapidOCR-OpenVINO | Tesseract | Paddle |
|---|---|---|---|---|---|
| **Table F1** ↑ | **1.0000** | 0.9875 | 0.9875 | 0.9747 | 0.6721 |
| **Row F1** ↑ | 0.8262 | 0.8946 | 0.8948 | 0.9030 | **0.9488** |
| **Column F1** ↑ | 0.8254 | 0.8484 | 0.8502 | 0.8397 | **0.8863** |
| **Table Dimension Accuracy** ↑ | 0.3457 | 0.4684 | 0.4684 | 0.3896 | **0.6341** |
| **Cell Exact Match** ↑ | 0.3049 | 0.4092 | 0.4089 | 0.3297 | **0.5942** |
| **Cell CER** ↓ | 1.2540 | 0.8003 | 0.8147 | 1.1383 | **0.5426** |
| **Cell Alignment Accuracy** ↑ | 0.3049 | 0.4092 | 0.4089 | 0.3297 | **0.5942** |
| **Table Structure Similarity** ↑ | 0.8111 | 0.8616 | 0.8627 | 0.8657 | **0.9130** |

### 5.2 Two Independent Quality Axes in Tables

Table metrics split into two independent dimensions:

**Axis A — Structure detection (does a table exist? where are rows and columns?):**
- `table_f1`, `row_f1`, `column_f1`
- Winner: **EasyOCR** detects every table (table_f1 = 1.000)
- Strong: RapidOCR variants (0.987), Tesseract (0.975)
- Loser: **Paddle** misses 33 % of tables (table_f1 = 0.672)

**Axis B — Cell content quality (is the text inside cells correct?):**
- `cell_exact_match`, `cell_cer`, `cell_alignment_accuracy`
- Winner: **Paddle** by a large margin (cell_exact 59.4 %, cell_cer 0.54)
- Second: **RapidOCR-ONNX** (cell_exact 40.9 %, cell_cer 0.80)
- Warning: EasyOCR (cell_cer 1.25) and Tesseract (cell_cer 1.14) have cell_cer > 1.0, meaning cell content contains *more errors per character than characters in the reference* — effectively noise.

### 5.3 The Paddle Table Paradox

Paddle shows an apparently paradoxical result: **poor table detection but superior cell content quality when it does detect a table**. This points to a layout detection problem — the model that handles OCR within detected cells is high quality, but the region detection and table localisation stage fails on ~33 % of tables. The massive insertion rate (60.8 %) appears to occur *outside* tables, not within them.

Cell CER > 1.0 for EasyOCR and Tesseract is a serious quality flag: those backends are inserting substantial noise or misformatting table cell content.

### 5.4 Ranking — Group 3

For **table structure detection**: EasyOCR > RapidOCR-ONNX ≈ RapidOCR-OpenVINO > Tesseract >> Paddle  
For **cell content quality**: Paddle >> RapidOCR-ONNX > RapidOCR-OpenVINO > Tesseract > EasyOCR  
**Best overall balance**: RapidOCR-ONNX (table_f1 0.987 + cell_exact 40.9 % + cell_cer 0.80)

---

## 6. Group 4 — Order, Integrity and Reliability

### 6.1 Full Metrics Table

| Metric | EasyOCR | RapidOCR-ONNX | RapidOCR-OpenVINO | Tesseract | Paddle |
|---|---|---|---|---|---|
| **Reading Order Accuracy** ↑ | 0.0523 | 0.0523 | 0.0523 | 0.0523 | 0.0523 |
| **Block Order Accuracy** ↑ | 0.0523 | 0.0523 | 0.0523 | 0.0523 | 0.0523 |
| **Duplicate Content Rate** ↓ | **0.0119** | 0.0185 | 0.0204 | 0.0120 | 0.0196 |
| **Duplicate Block Rate** ↓ | 0.9917 | 0.9901 | 0.9901 | **0.9917** | 0.9839 |
| **Header Leakage** ↓ | **0.0000** | **0.0000** | **0.0000** | **0.0000** | **0.0000** |
| **Footer Leakage** ↓ | **0.0000** | **0.0000** | **0.0000** | **0.0000** | **0.0000** |
| **Page Number Leakage** ↓ | 0.0012 | 0.0007 | **0.0006** | 0.0011 | 0.0010 |
| **Failure Rate** ↓ | **0.0089** | **0.0089** | **0.0089** | 0.0179 | ⚠️ **0.4955** |
| **Invalid Markdown Rate** ↓ | **0.0000** | **0.0000** | **0.0000** | 0.0268 ⚠️ | **0.0000** |

### 6.2 Critical Anomalies

#### Anomaly 1: Paddle — failure_rate = 49.55 %

**What it means:** In 111 of 224 pages, Paddle produced output that failed integrity validation — empty, corrupted, or structurally inconsistent output.

**Most likely root cause:** The `paddle_subprocess` isolation (implemented to avoid CF-4 DLL conflicts with torch on Windows) is introducing instability. The worker subprocess may be silently failing on specific page types (timeouts, uncaught exceptions, malformed output), or accumulating state between calls that produces duplicate/corrupt content. Combined with the 60.8 % insertion_rate, the hypothesis is: pages that "succeed" may have their content duplicated, while pages that "fail" produce garbage.

**Impact:** Paddle's CER/WER figures are **not directly comparable** to other backends because they include pages with degenerate output. The real quality of the underlying model on correctly processed pages could be significantly better than these aggregate numbers suggest. This is a tooling problem, not necessarily a model problem.

#### Anomaly 2: Tesseract — invalid_markdown_rate = 2.68 %

**What it means:** On approximately 6 of 224 pages, Tesseract produced syntactically invalid Markdown.

**Likely cause:** Tesseract operates at character/word level without layout understanding. On pages with dense tables or multi-column borders, it can emit sequences of `|` and `-` that resemble Markdown table syntax but violate the GFM specification.

**Impact:** Low (2.68 %), but relevant if downstream parsers are strict about Markdown validity.

#### Anomaly 3: Massive insertion in RapidOCR variants

The ~50 % insertion_rate in RapidOCR-ONNX/OpenVINO is notable even though their aggregate CER is the best among all backends. It indicates the models are generating plausible-looking text that was never in the source — semantic hallucination. For documents requiring strict source fidelity (contracts, clinical reports, legal filings), this is a significant risk even if the character-level error rate looks acceptable.

### 6.3 Reading Order — Systematic Failure

`reading_order_accuracy = 0.0523` (5.23 %) is **identical for all 5 backends**. This is not an OCR model problem — it is a pipeline-level ordering problem. The corpus contains many non-linear layouts (multi-column, tables, sidebars) where the expected reading order differs from the geometric top-to-bottom position used by the pipeline's block ordering algorithm. This requires a fix at the `structured-pdf-text` layout level, not at the backend level.

### 6.4 Ranking — Group 4

1. **EasyOCR** — failure_rate 0.89 %, lowest duplicate_content (1.19 %), no invalid Markdown
2. **RapidOCR-ONNX** / **RapidOCR-OpenVINO** — technical tie, same failure_rate
3. **Tesseract** — failure_rate 0.89 % but invalid_markdown 2.68 %
4. **Paddle** — failure_rate 49.55 % (disqualifying for production in current state)

---

## 7. Group 5 — Critical Data

### 7.1 Full Metrics Table

| Metric | EasyOCR | RapidOCR-ONNX | RapidOCR-OpenVINO | Tesseract | Paddle |
|---|---|---|---|---|---|
| **Numeric Exact Match** ↑ | 0.6203 | 0.5201 | 0.5187 | **0.8012** | 0.4178 |
| **Date Exact Match** ↑ | 0.9775 | 0.9494 | 0.9494 | **1.0000** | 0.9382 |
| **Currency Exact Match** ↑ | 0.3351 | 0.3298 | 0.3319 | **0.6376** | 0.3288 |
| **Identifier Exact Match** ↑ | **1.0000** | **1.0000** | **1.0000** | **1.0000** | **1.0000** |

### 7.2 Analysis by Data Type

**Numbers (numeric_exact_match):** Tesseract dominates (80.1 %) by a large margin. EasyOCR follows at 62.0 %. RapidOCR (~52 %) and Paddle (41.8 %) are hurt by their high insertion rates, which generate spurious digits.

**Dates (date_exact_match):** Tesseract achieves 100 % accuracy. EasyOCR reaches 97.75 %. All other engines are at 93–95 %. Dates have predictable formats that most OCR systems handle reasonably well.

**Monetary values (currency_exact_match):** The hardest category. Tesseract (63.76 %) leads by a very large margin over all competitors (33–34 %). This is likely because Tesseract's character-level precision handles Portuguese decimal separators (`R$ 1.234,56` — comma as decimal, period as thousands) more accurately than neural models that tend to swap or hallucinate separators. For financial document extraction, this gap is significant.

**Identifiers (identifier_exact_match):** 100 % for all backends. Alphanumeric identifiers with consistent patterns are reliably recognised by every OCR engine.

### 7.3 Ranking — Group 5

1. **Tesseract** — clear winner: numeric 80.1 %, date 100 %, currency 63.8 %
2. **EasyOCR** — second: numeric 62.0 %, date 97.75 %
3. **RapidOCR-ONNX** — numeric 52.0 %, date 94.9 %
4. **RapidOCR-OpenVINO** — nearly identical to ONNX
5. **Paddle** — weakest: numeric 41.8 %, currency 32.9 %

---

## 8. Per-Engine Profile

### 8.1 RapidOCR-OpenVINO

**Strengths:**
- **Best CER overall** (36.33 %) — best text fidelity among all backends
- Fewest pages with catastrophic CER > 90 %: only 18 out of 224
- Good speed for a neural model (2.74 s/page, ~10.3 min for 224 pages)
- Zero failure_rate (0.89 % floor), no invalid Markdown
- Reasonable table structure detection (table_f1 0.987)

**Weaknesses:**
- Insertion_rate 50.0 % — systematic hallucination is the largest risk
- Cell CER in tables 0.81 — moderate cell content quality
- Currency accuracy poor (33.2 %)
- **Requires Intel hardware** for OpenVINO acceleration — on AMD/ARM it degrades to ONNX-level speed

**Ideal use profile:** Production extraction on Intel CPU infrastructure where balance of speed and text quality matters. Not suitable for high-stakes financial data without post-processing.

---

### 8.2 Tesseract

**Strengths:**
- **Best for critical financial data**: currency 63.8 %, numeric 80.1 %, date 100 %
- Best list detection F1 (0.778) and AST similarity (0.290)
- Reasonable speed (3.16 s/page, ~11.8 min)
- No heavy ML framework dependencies (just CLI binary)
- Low insertion_rate (12.9 %) — what it gets wrong is mostly by omission, not hallucination
- Highest number of "perfect" pages (51 / 224 with CER < 10 %)

**Weaknesses:**
- **Highest deletion_rate** (31.5 %) — loses substantial text, especially in small fonts and scanned low-quality pages
- Overall CER 50.9 % — the text it produces has notable errors
- invalid_markdown_rate 2.68 % on complex layout pages
- Worst cell content in tables (cell_exact 33.0 %, cell_cer 1.14)
- 88 pages with CER > 60 %

**Ideal use profile:** Financial and accounting documents where precise recovery of numbers, dates, and currency values is paramount. Also appropriate when minimising ML dependencies is a constraint.

---

### 8.3 EasyOCR

**Strengths:**
- Best WER (50.8 %) and word accuracy (49.2 %)
- **Best table structure detection** (table_f1 = 1.000 — detects every table)
- Best block F1 for Markdown structure (0.453)
- Date accuracy 97.75 %; numeric 62.0 %
- Zero failure_rate (0.89 % floor), no invalid Markdown
- Lowest duplicate_content_rate (1.19 %)

**Weaknesses:**
- **Slowest of all** (15.65 s/page, 58.6 min for 224 pages) — completely impractical on CPU for production
- Insertion_rate 22.8 % — meaningful hallucination, though lower than RapidOCR variants
- Cell CER in tables 1.25 — worst cell content of all backends
- Currency accuracy poor (33.5 %)
- 77 pages with CER > 60 %

**Ideal use profile:** Offline high-precision analysis with GPU available; documents with many complex tables where structure detection matters more than cell content; research and benchmarking.

---

### 8.4 RapidOCR-ONNX

**Strengths:**
- CER 36.7 % — virtually identical to OpenVINO (best text quality tier)
- Better cell content than OpenVINO (cell_exact 40.9 % vs 40.9 %, cell_cer 0.80 vs 0.81 — similar)
- **Portable** — runs on any hardware without specific acceleration
- Zero failure_rate, no invalid Markdown

**Weaknesses:**
- **Very slow without hardware acceleration** (10.19 s/page, 38.1 min) — high variability (up to 60 s/page)
- Insertion_rate 50.2 % — same hallucination level as OpenVINO
- 3.7× slower than OpenVINO for equivalent quality
- Currency poor (33.0 %)

**Ideal use profile:** Cross-platform deployments where portability is critical and speed is not a constraint. Development and testing environments. In production on Intel hardware, OpenVINO is always preferable.

---

### 8.5 Paddle

**Current status: not recommended for production in this configuration**

**Strengths (when operating correctly):**
- **Fastest of all** (1.41 s/page, 5.3 min) — 2.2× faster than OpenVINO
- **Best cell content quality in tables**: cell_exact 59.4 %, cell_cer 0.54 — far ahead of all others
- Best row_f1 (0.949) and column_f1 (0.886)
- Lowest substitution_rate (0.63 %) and deletion_rate (1.82 %) — when it captures text, it is accurate

**Critical problems in this benchmark:**
- **Failure rate: 49.55 %** — 111 of 224 pages produced invalid or degenerate output
- **Insertion rate: 60.8 %** — massive hallucination on pages that "succeed"
- **Median page CER: 96.2 %** — over half of all pages are essentially unreadable output
- 113 pages with CER > 90 % (vs 18 for OpenVINO)
- Table F1 only 0.672 — fails to detect 33 % of tables

**Root cause hypothesis:** The `paddle_subprocess` isolation (CF-4 fix for Windows DLL conflict) is the most likely culprit. The worker process may be:
1. Accumulating OCR state between pages, causing content duplication
2. Silently crashing or timing out on specific page types (complex layouts, rotated content)
3. Returning previous-page results for failed pages (explaining the high insertion rate)

The underlying PP-OCRv6 model is likely high quality. In a Linux environment without subprocess isolation, or after the CF-4 issue is resolved, Paddle could be the best all-round backend due to its speed and superior cell quality.

**Ideal use profile (if fixed):** Primary production backend — speed without rival plus excellent table cell fidelity. Until the subprocess issue is resolved, restrict to Linux or non-torch environments where direct mode is used.

---

## 9. Weighted Score and Final Ranking

### 9.1 Scoring Method

Each engine receives a 0–100 score on 6 dimensions, then weighted:

| Dimension | Weight | Base metric |
|---|---|---|
| Text quality (CER) | 25 % | `1 − CER_normalized` |
| Markdown structure | 15 % | `block_f1` (proxy) |
| Table cell quality | 15 % | `cell_exact_match` |
| Reliability | 20 % | `1 − failure_rate − 0.5 × invalid_markdown_rate` |
| Critical data | 15 % | mean of `numeric + date + currency exact_match` |
| Speed | 10 % | `elapsed_paddle / elapsed_engine` (normalised to Paddle = 100) |

### 9.2 Dimension Scores (0–100)

| Engine | CER | Markdown | Tables | Reliability | Critical Data | Speed | **Final Score** |
|---|---|---|---|---|---|---|---|
| RapidOCR-OpenVINO | 63.7 | 37.4 | 40.9 | 99.1 | 60.0 | 51.2 | **61.6** |
| Tesseract | 49.1 | 44.5 | 33.0 | 96.9 | 81.3 | 44.7 | **59.9** |
| RapidOCR-ONNX | 63.3 | 37.6 | 40.9 | 99.1 | 60.0 | 13.8 | **57.8** |
| EasyOCR | 59.3 | 45.3 | 30.5 | 99.1 | 64.4 | 9.0 | **56.6** |
| Paddle | 36.6 | 21.6 | 59.4 | 50.5 | 56.2 | 100.0 | **49.8** |

### 9.3 Final Ranking

| Rank | Engine | Score | Key differentiator |
|---|---|---|---|
| 🥇 1st | **RapidOCR-OpenVINO** | 61.6 | Best CER, fewest catastrophic pages, reliable, fast enough |
| 🥈 2nd | **Tesseract** | 59.9 | Dominant in critical financial data; good structure; low dependencies |
| 🥉 3rd | **RapidOCR-ONNX** | 57.8 | Same quality as OpenVINO but 3.7× slower — portable alternative |
| 4th | **EasyOCR** | 56.6 | Best table detection and WER; unusable on CPU in production |
| 5th | **Paddle** | 49.8 | Critical subprocess failure makes results unreliable; needs investigation |

> **Scores are close (49.8–61.6).** The choice between the top three should be driven by specific use-case requirements, not the aggregate score alone.

---

## 10. Trade-off Analysis by Use Case

### Scenario 1: Financial and accounting documents (invoices, statements, reports)
**Recommended: Tesseract**
- Currency 63.8 %, numeric 80.1 % — no other backend is close
- Date extraction: 100 % accuracy
- No ML framework dependencies
- ⚠️ Watch deletion_rate 31.5 % — may lose lines in dense tables

### Scenario 2: Large-scale production on Intel CPU infrastructure
**Recommended: RapidOCR-OpenVINO**
- 2.74 s/page is industrially viable without GPU
- Best CER (36.3 %), fewest catastrophic pages
- ⚠️ Insertion_rate 50 % — add post-processing deduplication for high-fidelity requirements
- Falls back to ONNX speed on non-Intel hardware

### Scenario 3: Offline high-precision research or archival processing
**Recommended: EasyOCR** (with GPU) or **Tesseract** (CPU-only)
- EasyOCR: best WER (50.8 %), best table detection (table_f1 = 1.0)
- Tesseract: best financial data, lowest footprint, no GPU required

### Scenario 4: Documents with complex tables (financial grids, multi-column tables)
**Recommended: RapidOCR-ONNX** (balanced) or **Tesseract** (detection focus)
- ONNX: table_f1 0.987 + cell_exact 40.9 % + cell_cer 0.80 — best balance
- Tesseract: table_f1 0.975, best for knowing tables exist; cell content is weaker

### Scenario 5: Maximum portability (no GPU, no Intel, minimal dependencies)
**Recommended: Tesseract**
- CLI binary only, no Python ML frameworks
- Acceptable speed on any hardware (3.16 s/page)
- Best financial data recovery

### Scenario 6: If Paddle subprocess issue is resolved
**Recommended: Paddle** (investigate CF-4 fix first)
- Speed (1.41 s/page) and cell content quality (cell_exact 59.4 %) are unmatched
- Requires validation in Linux/direct mode or after subprocess state isolation is fixed

---

## 11. Issues Identified and Next Steps

### 11.1 Problems Requiring Investigation

| # | Engine | Problem | Severity | Suggested action |
|---|---|---|---|---|
| P-01 | Paddle | failure_rate 49.55 % in Windows subprocess mode | **Critical** | Investigate CF-4: log subprocess errors, check for state accumulation between calls, add page-level timeout and process restart |
| P-02 | Paddle | insertion_rate 60.8 % | **Critical** | Determine if subprocess accumulates previous-page OCR state; test direct mode on Linux |
| P-03 | RapidOCR-ONNX / OpenVINO | insertion_rate ~50 % | High | Evaluate post-processing filter to remove repeated or low-confidence token insertions |
| P-04 | Tesseract | invalid_markdown_rate 2.68 % | Medium | Identify which page types produce invalid Markdown; add output sanitisation for `|` / `-` sequences |
| P-05 | All engines | reading_order_accuracy 5.23 % | Medium | Pipeline-level problem, not backend — review block ordering algorithm for multi-column and table layouts |
| P-06 | All engines | heading_level_accuracy 0.0 % | Medium | No backend detects heading hierarchy — investigate post-processing based on font size or bold detection |
| P-07 | All except Tesseract | currency_exact_match < 34 % | High | Add Portuguese currency normalisation (comma as decimal, period as thousands separator) in post-processing |

### 11.2 Default Backend Recommendation

Based on v2 results, **no single backend dominates all use cases**. The recommendation depends on priorities:

| Priority | Recommended backend |
|---|---|
| Best text fidelity + reliability + speed balance | **RapidOCR-OpenVINO** |
| Financial / critical data accuracy | **Tesseract** |
| Table structure detection | **EasyOCR** (if GPU available) |
| Maximum portability / minimal dependencies | **Tesseract** |
| Best potential if CF-4 is resolved | **Paddle** |

For a single default that covers the broadest set of use cases without known critical failures, **RapidOCR-OpenVINO** is the current best choice, with the caveat that its 50 % insertion_rate should be monitored and mitigated with deduplication post-processing.

### 11.3 Confidence Limits of These Results

- **Paddle results are suspect**: the 49.55 % failure_rate means aggregate CER/WER figures include degenerate pages, making them unreliable for benchmarking the underlying model quality.
- **The corpus is intentionally adversarial**: results on real production documents (typically less complex) should be meaningfully better for all backends.
- **CPU-only benchmark on Windows**: backends with GPU support (EasyOCR, Paddle) could have dramatically different performance — both in speed and quality — with CUDA acceleration.
- **EasyOCR regression from v1**: EasyOCR CER regressed from 28.6 % (v1) to 40.7 % (v2). This warrants investigation — a configuration change or different run environment between v1 and v2 may explain the gap.

---

## Appendix A — Complete Raw Metrics Reference

### A.1 All Summary Metrics (v2)

```
Metric                          EasyOCR    ONNX       OpenVINO   Tesseract  Paddle
---                             ---        ---        ---        ---        ---
cer_raw                         0.4065     0.3667     0.3631     0.5172     0.6343
cer_normalized                  0.4069     0.3669     0.3633     0.5094     0.6344
cer_text_only                   0.4068     0.3673     0.3637     0.5110     0.6342
wer                             0.5081     0.5981     0.5979     0.7808     0.6324
word_accuracy                   0.4919     0.4019     0.4021     0.2192     0.3676
substitution_rate               0.2343     0.0725     0.0745     0.2020     0.0063
deletion_rate                   0.0457     0.0235     0.0233     0.3147     0.0182
insertion_rate                  0.2281     0.5022     0.5001     0.1290     0.6079
omission_rate                   0.0457     0.0235     0.0233     0.3147     0.0182
heading_f1                      0.0919     0.0925     0.0925     0.0932     0.1070
heading_level_accuracy          0.0000     0.0000     0.0000     0.0000     0.0000
heading_text_cer                0.0000     0.0000     0.0000     0.0000     0.0000
block_f1                        0.4526     0.3763     0.3741     0.4446     0.2156
paragraph_boundary_f1           0.6357     0.5653     0.5633     0.6357     0.3885
list_detection_f1               0.7273     0.7273     0.7273     0.7778     0.7273
markdown_ast_similarity         0.2950     0.2333     0.2313     0.2904     0.1293
table_f1                        1.0000     0.9875     0.9875     0.9747     0.6721
row_f1                          0.8262     0.8946     0.8948     0.9030     0.9488
column_f1                       0.8254     0.8484     0.8502     0.8397     0.8863
table_dimension_accuracy        0.3457     0.4684     0.4684     0.3896     0.6341
cell_exact_match                0.3049     0.4092     0.4089     0.3297     0.5942
cell_cer                        1.2540     0.8003     0.8147     1.1383     0.5426
cell_alignment_accuracy         0.3049     0.4092     0.4089     0.3297     0.5942
table_structure_similarity      0.8111     0.8616     0.8627     0.8657     0.9130
reading_order_accuracy          0.0523     0.0523     0.0523     0.0523     0.0523
block_order_accuracy            0.0523     0.0523     0.0523     0.0523     0.0523
duplicate_content_rate          0.0119     0.0185     0.0204     0.0120     0.0196
duplicate_block_rate            0.9917     0.9901     0.9901     0.9917     0.9839
header_leakage_rate             0.0000     0.0000     0.0000     0.0000     0.0000
footer_leakage_rate             0.0000     0.0000     0.0000     0.0000     0.0000
page_number_leakage_rate        0.0012     0.0007     0.0006     0.0011     0.0010
failure_rate                    0.0089     0.0089     0.0089     0.0179     0.4955
invalid_markdown_rate           0.0000     0.0000     0.0000     0.0268     0.0000
numeric_exact_match             0.6203     0.5201     0.5187     0.8012     0.4178
date_exact_match                0.9775     0.9494     0.9494     1.0000     0.9382
currency_exact_match            0.3351     0.3298     0.3319     0.6376     0.3288
identifier_exact_match          1.0000     1.0000     1.0000     1.0000     1.0000
elapsed_total_s                 3516.7     2283.4     616.8      707.0      316.4
avg_s_per_page                  15.65      10.19      2.74       3.16       1.41
total_chars_extracted           512085     547331     548429     732648     265541
pages_evaluated                 224        224        224        224        224
pages_missing_in_hypothesis     0          0          0          0          0
```

### A.2 Per-Page Quality Distribution

| Engine | CER < 10 % (perfect) | 10–30 % (good) | 30–60 % (poor) | > 60 % (bad) | > 90 % (catastrophic) |
|---|---|---|---|---|---|
| RapidOCR-OpenVINO | 49 | 35 | 73 | 67 | 18 |
| RapidOCR-ONNX | 49 | 35 | 72 | 68 | 19 |
| EasyOCR | 43 | 29 | 75 | 77 | 31 |
| Tesseract | 51 | 41 | 44 | 88 | 38 |
| Paddle | 49 | 27 | 27 | **121** | **113** |

### A.3 Runtime Metadata

| Engine | Runtime | Version | Framework | Notes |
|---|---|---|---|---|
| Paddle | paddle_subprocess | paddlepaddle 3.3.1 | PaddleOCR 3.7.0 + PaddleX 3.7.2 | PP-OCRv6 medium; subprocess isolation (CF-4 fix) |
| EasyOCR | torch-cpu | easyocr 1.7.2 | torch 2.14.0+cpu | ResNet+LSTM model |
| RapidOCR-ONNX | onnxruntime | rapidocr-onnxruntime 1.4.4 | onnxruntime 1.30.0 | No hardware acceleration |
| RapidOCR-OpenVINO | openvino | rapidocr-openvino 1.4.4 | openvino 2024.4.0 | Intel hardware acceleration active |
| Tesseract | tesseract-cli | v5.5.3 | — | tessdata `por`, PSM 3, OEM 1 (LSTM+Legacy) |

---

*Generated from `output/fase8/V2/metrics_*_v2-*.json` and `output/fase8/V2/manifesto_e2e_*_v2-*.json`. Corpus: 224 pages, run suffix v2, extraction mode balanced, language pt.*
