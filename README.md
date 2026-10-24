# structured-pdf-text

Starter implementation for a local and auditable PDF text extraction engine.

structured-pdf-text is designed to run locally on Linux and Windows.

The native PDF extraction path is expected to be fully cross-platform.
Advanced visual recovery features, including OCR, layout detection and table
structure models, are optional components and must be validated per platform,
model version and deployment environment.

This repository intentionally starts with the native path first:

```text
PDFium NativeEvidenceSource
  -> NativeCharacter[]
  -> page JSON dump
  -> line reconstruction
  -> native reading text
  -> visual overlay
```

The current code covers executable slices of Milestones 0 through 12:
native evidence, conservative text reconstruction, complexity analysis,
heuristic layout regions and deterministic reading order. The layout and
reading-order adapters are intentionally vendor-neutral while the native path
is being validated against the local corpus. OCR integration is available
through an optional, lazily loaded OCR adapter; the selected runtime/model must be
installed separately. Document assembly now also aggregates page diagnostics,
detects repeated edge regions and renders page-bounded Markdown with detected
tables.
Milestone 1 is the native evidence foundation: the parser preserves the
PDFium character-index sequence and exposes the raw page evidence before any
normalization, deduplication or layout decision.

The current code covers:

- repository structure
- CLI entry point
- PDF header and security limit checks
- PDFium native character extraction
- per-character Unicode, bbox, origin, angle, font, size and PDFium flags
- page MediaBox/CropBox/bbox/rotation evidence and capability diagnostics
- image metadata, path bboxes, minimal annotation evidence and optional tagged structure trees
- optional immutable native evidence in `StructuredPage.native_evidence`
  (`retain_native_evidence=True`; enabled automatically by `inspect --raw-page-json`)
- raw native page JSON dumps (`--raw-page-json`)
- canonical top-left coordinate system
- conservative Unicode normalization
- simple duplicate detection
- horizontal line reconstruction
- token generation
- raw and reading text renderers
- structured JSON output
- diagnostic summary
- basic overlay for characters and lines
- deterministic complexity facts: text/image/path coverage, visible ink,
  duplicate/invisible text signals, table/vector/rotation reasons and page strategy
- deterministic layout regions for header, footer, text, native grid tables and figures
- strict vector-grid table detection with cell bboxes, native token assignment,
  confidence and `StructuredTable` output
- raster grid fallback that recovers cell geometry for embedded-image tables
- deterministic region-aware reading order, selective prose column splitting,
  table row ordering and rotated text groups
- OCR engine contract, OCR token-to-line reconstruction and pluggable backend
  adapters (EasyOCR, PaddleOCR, RapidOCR, Tesseract) with explicit
  missing-runtime diagnostics, CPU/WSL compatibility and rotated-page fallback
- cross-page table signatures, repeated-header detection, continuation
  evidence and logical table merging with source-page fragments
- document-level diagnostics, page boundaries and repeated header/footer
  signatures with configurable reading-text filtering
- Markdown rendering with page sections and structured table blocks
- per-page stage timings and document assembly timing
- isolated native-page failure placeholders so later pages can still be read
- quality-first OCR variant selection with page-edge orientation coherence
- hybrid OCR lines assigned to visual table regions and excluded from duplicate
  free-text supplements when they overlap native headers, footers or tables
- cell-authoritative serialization for OCR-recovered visual tables, preserving
  detected row/column order before page reading-order assembly
- dedicated high-resolution OCR refinement over visual-table crops, with
  fragment-aware spacing recovery inside cells
- visual-grid validation against embedded-image bounds to avoid promoting
  chart axes to structured tables while retaining their OCR text
- quality-first OCR refinement for wide figures, with independent vertical
  panel crops for dense multi-chart images

It does not yet implement learned layout models or a learned table structure
model. Those modules remain replaceable so the architecture can grow without
rewriting the API.

Private PDFs for manual validation should be placed under `corpus/`; see
[`corpus/README.md`](corpus/README.md).

## Install locally

CUDA inference is opt-in: `ExtractorConfig(use_gpu=True)` or `extract --gpu`.
CPU is the default, including in CUDA environments. Missing GPU libraries or
configuration produce a warning and a CPU retry within the same engine/model.
Use separate GPU environments for EasyOCR, PaddleOCR and RapidOCR/ONNX;
the pinned RapidOCR/OpenVINO profile and Tesseract run on CPU. See the
[WSL2 GPU setup and verification](docs/OCR_Installation_Profiles.md#gpu-no-linuxwsl2).
The native extraction path does not initialize OCR or GPU runtimes.
PaddleOCR and both RapidOCR providers default to PP-OCRv6 medium (Det/Rec).
See the [local WSL2 validation and review report](docs/OCR_GPU_Validation_2026-10-22.md)
for the prepared environment paths, evidence and remaining acceptance gates.

### Linux / WSL

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e .
```

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e .
```

## OCR setup (requires internet, run once)

The `balanced` and `ocr` extraction modes use EasyOCR by default. EasyOCR
model weights are pre-downloaded once via the benchmark setup script. Other
engines (PaddleOCR, RapidOCR, Tesseract) require their own setup steps.
Model downloads happen during setup, not during extraction.

**The runtime never downloads models. If the setup is incomplete, extraction
fails immediately with a clear error before processing any page.**

### Step 1 — Download models

```bash
python -m structured_pdf_text.cli setup-paddle-models
```

This downloads the required OCR models to
`~/.cache/pdfextractor/paddlex/official_models/`:

- `PP-LCNet_x1_0_doc_ori` — document orientation classifier
- `PP-LCNet_x1_0_textline_ori` — text-line orientation classifier
- `PP-OCRv6_medium_det` — text detection (default)
- `PP-OCRv6_medium_rec` — text recognition (default)
- `UVDoc` — document unwarping

Benchmark-managed EasyOCR and RapidOCR files use stable per-user locations at
`~/.cache/pdfextractor/easyocr/` and `~/.cache/pdfextractor/rapidocr/`. The
runtime and static preflight resolve those defaults in a fresh shell; explicit
environment variables still override them.

### Step 2 — Verify readiness

```bash
python -m structured_pdf_text.cli paddle-models-status
```

Expected output when ready:

```text
OCR model home:
  /home/user/.cache/pdfextractor/paddlex/official_models

[ok] PP-LCNet_x1_0_doc_ori
[ok] PP-LCNet_x1_0_textline_ori
[ok] PP-OCRv6_medium_det
[ok] PP-OCRv6_medium_rec
[ok] UVDoc

Offline OCR readiness: READY
```

### Step 3 — Extract offline

```bash
python -m structured_pdf_text.cli extract documento.pdf \
  --mode balanced \
  --ocr-engine paddle \
  --paddle-model-profile pt \
  --output markdown \
  -o documento.md
```

Extraction runs completely offline. If models are missing, the process fails
before reading any page and prints the path to run `setup-paddle-models`.

### Optional network-isolation check on Linux/WSL

When validating the offline runtime with `sudo unshare --net`, remember that
`sudo` normally changes `HOME` to `/root`. If the OCR models were installed in
the current user's cache, preserve the same PaddleX cache path explicitly.

```bash
export PADDLE_PDX_CACHE_HOME="$HOME/.cache/pdfextractor/paddlex"

sudo --preserve-env=PADDLE_PDX_CACHE_HOME unshare --net -- bash -lc '
cd /path/to/pdfextractor
source .venv/bin/activate

python -m structured_pdf_text.cli paddle-models-status

python -m structured_pdf_text.cli extract documento.pdf \
  --mode balanced \
  --ocr-engine paddle \
  --paddle-model-profile pt \
  --output markdown \
  -o documento.md
'
```

`paddle-models-status` must report `Offline OCR readiness: READY`. During extraction,
no model hoster lookup or download should occur.

If the local `sudo` policy does not allow preserving environment variables,
set `PADDLE_PDX_CACHE_HOME` explicitly inside the isolated shell instead.
Use the same cache directory that was used during `setup-paddle-models`; do not copy
models into `/root` only for this validation.

If model hosts are blocked by organizational network policy, use offline model
provisioning from an approved machine or an internal artifact repository. Copy
all model directories required by the selected profile from an approved installation into:

```text
<cache>/official_models/
```

Then run `paddle-models-status`; it must report `Offline OCR readiness: READY`.

If the network allows the model hosts but TLS inspection requires a custom
corporate CA, configure Python/PaddleX using the CA and proxy settings approved
by your organization. Do not disable TLS verification or bypass organizational
network controls.

## Run tests

```bash
pytest
python -m pytest
```

## Comparative benchmark of OCR engines

The project includes an E2E benchmark pipeline that evaluates four OCR families
(PaddleOCR, RapidOCR, Tesseract 5, EasyOCR) in five deployment configurations
(including RapidOCR with ONNX Runtime and OpenVINO) across
five metric groups: text (CER/WER), Markdown structure, tables,
order and integrity, and critical data.

See the full guide at [`docs/benchmark_engines.md`](docs/benchmark_engines.md).

### Quick start (Windows PowerShell)

```powershell
# Smoke test — 5 pages, all engines
.\scripts\run_benchmark.ps1 -EnvironmentRoot .ocr-envs

# Full document
.\scripts\run_benchmark.ps1 -EnvironmentRoot .ocr-envs -RunSuffix "v1" -AllPages

# Single engine or subset
.\scripts\run_benchmark.ps1 -EnvironmentRoot .ocr-envs -Engine tesseract -AllPages -RunSuffix "v2"
.\scripts\run_benchmark.ps1 -EnvironmentRoot .ocr-envs -Engine "easyocr,tesseract" -AllPages -RunSuffix "v2"
```

### Quick start (Linux / WSL)

```bash
# With Tesseract 5.x + Portuguese traineddata already in PATH:
# prepare one environment per deployment profile (preserves .venv)
scripts/setup_ocr_benchmark.sh

# Smoke test — pages 77–81 of Stress OCR Markdown V4
scripts/run_benchmark.sh --python .venv/bin/python --environment-root .ocr-envs --run-suffix wsl-smoke

# Full Stress V4 corpus
scripts/run_benchmark.sh --python .venv/bin/python --environment-root .ocr-envs --run-suffix wsl-v1 --all-pages
```

Scripts involved:

| Script | Function |
|---|---|
| `scripts/evaluate_e2e.py` | Extracts PDF with one engine; saves Markdown + manifest |
| `scripts/compute_metrics.py` | Computes canonical metrics against ground truth |
| `scripts/regrade_existing.py` | Evaluates existing Markdown without extraction or OCR |
| `scripts/compare_engines.py` | Generates comparative table of all engines |
| `scripts/run_benchmark.ps1` | Orchestrates the three steps sequentially on Windows |
| `scripts/setup_ocr_environments.py` | Installs isolated profiles on Linux/WSL and Windows; [guide](docs/OCR_Installation_Profiles.md) |
| `scripts/setup_ocr_benchmark.sh` | Prepares all isolated profiles and models on Linux/WSL |
| `scripts/run_benchmark.sh` | Orchestrates the three steps sequentially on Linux/WSL |

### Evaluating existing Markdown

Both metric CLIs are thin wrappers over `structured_pdf_text.evaluation.cli`
and schema `evaluation-3.0.0`; there is no other metric implementation.
`compute_metrics.py` writes `metrics_v3_{engine}_{run_id}.json` and
`errors_v3_{engine}_{run_id}.md` (default run ID: hypothesis file stem).
`regrade_existing.py` writes the same canonical result to
`metrics_{engine}_{run_id}.json` and `errors_{engine}_{run_id}.md`. The runners
pass the canonical result to the comparator, which accepts only
`evaluation-3.0.0`.

```python
from structured_pdf_text.evaluation import EvaluationRequest, evaluate

result = evaluate(EvaluationRequest(
    ref_text=reference_markdown,
    hyp_text=hypothesis_markdown,
    selected_page_scope=[1, 2],
))
```

Selected pages must have ground truth. All quality groups and integrity checks
use this scope; extra pages are recorded separately in `inputs`. Without page
markers, Markdown is treated as page 1. A manifesto used as ground truth must
contain a string `expected_markdown` for every page; an explicitly empty string
represents a blank page. Metadata-only manifestos require `--reference`, as with
`corpus/V3/Document_AI_V3.md`. Explicit Markdown takes precedence over manifesto
text. Invalid inputs or a canonical evaluation failure make the command fail.

Text metrics use exact Levenshtein S/D/I counts with bounded working memory
(a minimal band, else Myers/Hyyrö bit-parallel distance and one exact band).
Raw CER uses the original Markdown bodies; visible text comes from parser
tokens, preserving literal code. Aligned CER counts terminal text units once.
`cer_sequential_visible` (MET-TX03) is the same CER as MET-TX02, because parser
text is already visible; it is computed once and copied. MET-I03 likewise copies
MET-TX06. The evaluator records metric definitions version `3.1.0` and its
resource policy.

Add `--supplemental-audit` to either metric CLI to also write
`audit_{engine}_{run_id}.json` (`critical-context-1.0.0`). This separate audit
checks signed BRL/USD/EUR amounts with pt-BR separators, exact alphanumeric IDs,
critical entities at table-cell coordinates, link destinations, checkbox states,
and per-page alignment coverage, deletion and order. It also reports formatting
counts for inspection. Canonical definitions `3.1.0` and their 69 scores stay
unchanged; compare audit results only within the same audit version and scope.

Document metrics are page-anchored (`parser-visible-leaves-v3-page-anchored-micro`):
every selected page is evaluated once, in parallel, and the document CER/WER and
aligned CER are Σ page edits / Σ page reference length. Content moved to another
page counts as a deletion and an insertion. Structure and reading order use the
union of the page alignments with document-wide order, so page inversions remain
measurable; tables, critical data, token coverage and integrity compare the whole
document. The aligner first pairs blocks with identical content (same layer, type
and page) by occurrence, then scores the remaining blocks of the same layer — text
leaves with leaves, containers with containers (`content-match-v3-exact-first-layered`).
Reading order counts matched text leaves only.

The default budgets are 64,000,000 DP cells per page and metric (shared across
blocks for aligned CER) and 250,000 fuzzy candidate pairs per page alignment;
document-level recomputation receives one budget per page. Exceeding a budget
produces `resource_limit` with a null value; the evaluation is partial and
ineligible for ranking, and a page's failure makes the document metric
unavailable. `EvaluationRequest.max_text_edit_cells` and `max_alignment_pairs`
configure these budgets.

Paragraph detection checks both ends of each matched paragraph on an exact,
page-local token alignment. `paragraph_boundary_f1` separately measures internal
paragraph cuts; pages with no cuts on either side are N/A. Paragraphs of list
items and block quotes are the text of those blocks, not paragraph blocks; a list
item's text excludes its nested lists. Ordered-list ordinals use
CommonMark's displayed numbering (container start plus item index). Page markers
must be top-level, column-zero headings: markers inside code, HTML elements,
quotes or lists remain content. A delimiter immediately after a completed HTML
element is accepted even without a blank line (as in the V5 reference).
Physical page order is preserved. Fence validity follows the
parser's source maps, including backtick/tilde lengths and nested containers.
Parser identity records the installed `markdown-it-py` version; page and paragraph
policies are required by the shared comparison contract. Regrade older artifacts
before comparing them with definitions `3.1.0`.

Evaluation files are immutable within an engine/run ID/output-directory namespace.
Repeating identical inputs preserves the original files and timestamps. Changed
hypotheses, references, scope, evaluator policies or run metadata require a new
`--run-id` or output directory. This publication identity is stricter than
benchmark comparability. Existing files without a publication journal are
preserved and rejected rather than adopted automatically.

Compute and regrade can share a directory for identical inputs: the second CLI
adds its filenames as byte-identical aliases of the published canonical files
(`metrics_v3_*`/`errors_v3_*` when names collide). The CLI prints the actual
output paths. Keep the hidden `.evaluation-*.json` journal with the
artifacts. A `.evaluation-*.lock` indicates an active or interrupted publication;
an interrupted lock needs review before reuse. New artifacts use atomic creation
without replacement, requiring filesystem hard-link support; unsupported
filesystems fail explicitly. No overwrite option is provided.

Canonical results also contain `per_page` diagnostics, identified by
`evaluator.reporting_policy = canonical-page-diagnostics-v1`. Every selected
reference page is included, with page-local canonical metrics, evaluation status,
corpus `family`/`conditions`, and the extraction's recorded page outcomes.
Missing pages remain missing; an emitted blank page is distinct. Page metrics
use the same pipeline and independent instances of the recorded resource
budgets. They add computation and do not replace global metrics for reading
order or entities moved between pages.

The canonical comparison and regrade error report show run IDs from `inputs`,
content/evaluation/stability states, recovery counters and reasons, worst pages,
family/condition macro means with applicable/total page counts, separate
render/OCR/assembly times, elapsed, parent RSS, sampled process-tree RSS, and
complete recorded deployment profiles. Defined zero scores enter means; N/A
and unavailable computations do not. Missing operational information is shown
as unavailable; evaluating Markdown alone does not establish a valid extraction.
OCR failure/timeout counts refer to explicit `ocr_outcome` values, with observation
coverage; they are not a complete history of retries inside a backend.

The evaluation scope and the timed extraction scope are separate:
`operational.run_selected_pages` preserves the latter. Elapsed per page uses
that recorded extraction scope, including missing pages, even when evaluation
selects a subset. Without it, the rate is unavailable. Run recovery totals,
time and memory are not rescaled for an evaluation subset. Runs of the same
engine keep separate comparison columns. Metadata is escaped in Markdown tables.
The comparator validates the complete result before rendering it. Regrade old
canonical files with a new run ID or output directory. `--skip-validation` can
display them for diagnosis, with unavailable breakdowns marked explicitly.

Canonical results are checked against the packaged Draft 2020-12 schema
([documented copy](docs/evaluation-v3.schema.json)) and arithmetic invariants.
The API exports `validate_result()` and `result_schema()`. Invalid states, values,
counts, scopes, missing page diagnostics and nonfinite numbers are rejected
before publication or comparison. The policy is
`canonical-schema-and-invariants-v1`; runtime validation requires `jsonschema`.
Definitions 3.1.0 give undefined token coverage denominators an explicit null
state, keep CER directions constant, and include literal code in integrity
coverage. Heading/list CER shares the recorded structure edit budget. Tables are
paired by the Dice coefficient of their cell-word multisets (order-independent,
case-folded only for pairing); cell exact match and cell CER are case-sensitive,
table content F1 ignores empty cells, and column order compares content-matched
non-empty cells over all matched rows. Critical data reads the parser's leaf text
once, with priority identifiers > currency > valid dates > percent > numbers;
invalid date spellings remain numbers. Header/footer heuristics require a line
to repeat on at least two pages.

Equal normalized block sequences with the same types and pages have an exact
linear alignment shortcut; arbitrary differences still use the guarded greedy
alignment. Strict pairwise order counts use an exact O(n log n) algorithm.
Identical table/row sequences avoid redundant similarity matrices, and table and
row matches are computed once per evaluation.

To save extracted characteristics from one PDF pass, use:

```bash
.venv/bin/python scripts/evaluate_e2e.py corpus/V5/Document_AI_V5.pdf \
  --mode balanced --engine rapidocr --provider openvino --language pt-BR \
  --save-features --retain-native-evidence \
  --run-id features-v5 --output-dir output/loop/001/V5
```

RapidOCR uses pinned PP-OCRv6 Portuguese-capable models; `--rapidocr-model-size`
selects `small` explicitly and defaults to `medium`. Provision them once with
`python scripts/setup_rapidocr_models.py --size all`. `--save-features` adds
`features_*.json` (structured document) and
`diagnostics_*.txt` alongside Markdown and the run manifest. All come from the
same `extract()` call. `--retain-native-evidence` also retains native PDF
characters and objects and increases memory use. Existing artifact names are
rejected before extraction; choose a new run ID for another run. A saved partial
run still exits with code 2 unless `--allow-partial` is supplied.

Table preservation metrics include missing and extra cells, rows and tables.
`cell_content_exact_match` divides exact positional pairs by all paired,
missing and extra cells. `cell_content_exact_match_matched` is the conditional
diagnostic; its coverage reports the fraction of reference cells paired.
Cell CER sums edits over every reference cell character, including full
deletions and all insertions. A missing nonempty table has Cell CER 1; a
globally empty reference with inserted text has a null value and
`undefined_denominator`. CER can exceed 1. The edit budget is shared by all
cell distances, independently of the text metrics, and resource exhaustion
also makes the evaluation partial and ineligible for ranking.

HTML table blocks retain text, captions, empty cells and anchor coordinates.
Merged cells store `row_index`, `column_index`, `rowspan` and `colspan`;
their text is counted once. Column accuracy uses occupied width;
`cell_span_accuracy` compares effective spans over all paired/missing/extra
anchors. `rowspan=0` extends to the end of its row group; positive row spans
are clipped at that group boundary. Nested tables, overlapping spans, invalid
span values and incomplete HTML table blocks raise an explicit input error.
Keep each complete HTML table within a single Markdown HTML block (no internal
blank lines). HTML parsing does not render external CSS or execute scripts.
Previously generated results must be regraded before comparing versions.

Aggregation includes finite numeric outcomes with status `ok`,
`false_positive_only` or `false_negative_only`, including valid zero scores.
N/A and unavailable scalar values are excluded from macro/distribution
statistics; coverage reports the applicable fraction. Micro is a ratio of
complete summed counts. It retains insertion-only counts even when a page's
local denominator is zero. Missing/invalid count keys raise an error;
incomplete evaluation of any page makes the complete micro value null.
Different definitions, units, directions or normalization profiles cannot
be aggregated together.

The public `check_comparability()` and `compare_engines.py` share one
fail-closed validator. Canonical comparisons require valid effective-reference,
PDF and corpus-manifest SHA-256 identities; `reference-effective-v1`; a nonempty
scope of unique positive integer page IDs; mode and benchmark protocol; and
matching evaluator versions, policies and resource limits. Page order and
hexadecimal digest letter case do not alter identity. Missing provenance,
legacy schemas and raw-reference-hash fallbacks are rejected, including when
both runs omit the same field. Standalone Markdown can still be evaluated;
comparison requires its input provenance. Extraction failures, missing pages,
recovery, engine identity and quality scores are outcomes and do not invalidate
otherwise matching comparison inputs. `strict=False` does not bypass the API
validator; the CLI's explicit `--skip-validation` produces a labelled
heterogeneous diagnostic report. Missing or unreadable requested artifacts
make the CLI fail rather than silently shortening the comparison set.

## Maximum quality OCR

Accuracy-first extraction keeps native text selection and enables exhaustive
OCR candidates, overlapping page tiles, regional refinement, table-cell OCR,
and contextual critical-data refinement.

```bash
pdftext extract arquivo.pdf --max-quality --ocr-engine easyocr -o saida.md
pdftext setup-easyocr-models --language pt-BR --include-dbnet
```

DBNet18 is used as an additional candidate (N) in the exhaustive pipeline when
both its weights *and* runtime are functional.  Having the weights on disk is
not sufficient — on Windows, DBNet18 also requires compiled native extensions
(deformable convolution via MSVC Build Tools).  Two distinct failure modes are
reported separately:

- `dbnet18_weights_missing` — weights file absent; run `setup-easyocr-models --include-dbnet`
- `dbnet18_runtime_unavailable` — weights present but inference failed (install MSVC Build Tools)

Run `scripts/preflight_ocr_backends.py --deep-smoke --max-quality` to verify
full max-quality readiness.  When CRAFT and the exhaustive planner pass but
DBNet18 is unavailable, the result is reported as `INCOMPLETE` (not broken) and
extraction continues with the remaining candidate set (DBNet18 is skipped; all other candidates still run).

## CLI examples

```bash
# Native extraction (no OCR required)
pdftext extract documento.pdf
pdftext extract documento.pdf --output raw
pdftext extract documento.pdf --output json

# OCR-assisted extraction with EasyOCR (default engine, requires pre-downloaded weights)
pdftext extract documento.pdf --mode balanced --output markdown
pdftext extract documento.pdf --mode balanced --ocr-quality-policy adaptive
pdftext extract documento.pdf --mode balanced --ocr-quality-policy baseline
pdftext extract documento.pdf --mode balanced --ocr-quality-policy exhaustive
pdftext extract documento.pdf --mode balanced --output json
pdftext extract documento.pdf --mode ocr --output reading
pdftext extract documento.pdf --best --output markdown -o output.md

# Select another OCR engine explicitly
pdftext extract documento.pdf --mode balanced --ocr-engine paddle --paddle-model-profile pt --output markdown
pdftext extract documento.pdf --mode balanced --ocr-engine tesseract --output markdown
pdftext extract documento.pdf --mode balanced --ocr-engine rapidocr --ocr-provider openvino --output markdown
pdftext extract documento.pdf --mode balanced --ocr-engine rapidocr --ocr-provider onnxruntime --output markdown

# Inspection and diagnostics
pdftext inspect documento.pdf --page 1
pdftext inspect documento.pdf --page 1 --raw-page-json
# Native overlay is the default and needs no OCR models
pdftext overlay documento.pdf --page 1 --out page-1.png
# OCR-capable overlay selects its engine; Paddle profile is optional
pdftext overlay documento.pdf --page 1 --out page-1.png --mode balanced --ocr-engine tesseract
pdftext report corpus/*.pdf --mode native
pdftext report corpus/*.pdf --mode balanced --ocr-engine rapidocr --ocr-provider onnxruntime --merge-cross-page-tables
pdftext report corpus/*.pdf --mode native --workers 2
pdftext compare documento.pdf --adapters structured-native pdfium-raw pymupdf

# OCR model management
pdftext setup-paddle-models --paddle-model-profile pt
pdftext paddle-models-status --paddle-model-profile pt
```

`--mode` selects the extraction strategy; `--language` identifies recognized
text and defaults to `pt-BR`; `--paddle-model-profile` selects Paddle model
weights when `--ocr-engine paddle` is used. The Paddle profile defaults to `pt`
and selects PP-OCRv6 medium (`pt-v6-medium` is a supported alias for backward
compatibility). Runtime no longer accepts PP-OCRv5; historical manifests and
cached artifacts remain available for audits.
`--ocr-quality-policy` is independent: it selects the OCR variant strategy.
`exhaustive` emits an informational resource-use warning and does not
automatically reduce OCR quality.

`--ocr-engine` selects one of the four OCR families. The default is `easyocr`,
chosen for the lowest average CER across the V3/V4 benchmark corpora and for
zero header-leakage rate. Use `paddle` when financial-data precision (Currency
F1, Numeric F1, Identifier Precision) is the priority. RapidOCR has separate
ONNX Runtime and OpenVINO providers; the old provider-specific names remain as
deprecated aliases.

| Engine | Flag value | Notes |
|---|---|---|
| EasyOCR (PyTorch CPU) | `easyocr` **(default)** | Weights pre-downloaded via `setup_ocr_benchmark.sh/.ps1` |
| PaddleOCR PP-OCRv6 | `paddle` | Requires `pdftext setup-paddle-models`; best critical-data metrics |
| RapidOCR PP-OCRv6 | `rapidocr` | Medium is default; select `small` with `--rapidocr-model-size`; provider uses `--ocr-provider` |
| RapidOCR ONNX alias (deprecated) | `rapidocr-onnx` | Kept for command compatibility |
| RapidOCR OpenVINO alias (deprecated) | `rapidocr-openvino` | Kept for command compatibility |
| Tesseract 5 | `tesseract` | Requires Tesseract binary in PATH |

The public language tag `pt-BR` is accepted (with `pt` and `por` aliases).
`--paddle-model-profile` applies to Paddle; `--rapidocr-model-size` applies to
RapidOCR. The engine is also
selectable through the Python API via `ExtractorConfig(ocr_engine="easyocr")`,
`ExtractorConfig(ocr_engine="rapidocr", ocr_provider="openvino")`,
`ExtractorConfig(ocr_engine="tesseract")`, or
`dataclasses.replace(config, ocr_engine="rapidocr", ocr_provider="onnxruntime")`.
When using PaddleOCR: CPU OCR disables MKL-DNN/oneDNN by default because the
current Paddle 3.x PIR/oneDNN path is known to fail on some Linux/WSL CPU
stacks. Set `PADDLE_ENABLE_MKLDNN=1` to opt in explicitly. Document unwarping
(UVDoc) is enabled by default and is checked alongside the profile models by
`setup-paddle-models`, `paddle-models-status`, and the runtime. Models are
loaded from explicit local paths; remote model-source checks are disabled at
runtime. Model paths and behavior can be overridden through `PaddleOcrEngine`
constructor options.

OCR quality policies are available through the API and CLI:

- `baseline`: one normal inference, plus required orientation recovery;
- `adaptive`: baseline followed by only the recovery families indicated by
  quality and image evidence (the default);
- `exhaustive`: all available enhancement variants, intended for diagnostics
  and benchmark comparison.

`ocr_quality_variants=False` remains a compatibility alias for `baseline`.
Every policy records quality measurements, attempted variants, selection and
consensus decisions in `page.diagnostics.facts`.

Performance diagnostics are available in `page.diagnostics.facts` under
`timings_ms` and `ocr_passes`, and in `document.diagnostics.facts` under
`total_ms` and `assemble_ms`. OCR initialization, inference, and refinement do
not have product deadlines. Cancellation is explicit: Paddle and Tesseract
subprocesses are interrupted; EasyOCR and RapidOCR check cancellation between
in-process calls, which cannot be forcibly stopped while a native call blocks.
EasyOCR also sets PyTorch thread pools process-wide when an explicit thread
count is configured; its effective intra-op/inter-op values are recorded in the
engine identity. Callers sharing Torch with other components should use a
dedicated OCR process or leave the thread count at automatic.

EasyOCR CPU recognition defaults to zero DataLoader workers: upstream creates
a loader for each detected box, so additional processes add startup overhead
for individual crops. `--threads` controls Torch inference independently.
`EASYOCR_WORKERS` remains an explicit override; if using
`EASYOCR_MAX_QUALITY_THREADS=1`, also set `EASYOCR_WORKERS=0` for CPU recognition.
Exhaustive OCR reuses alternate Readers across pages and regions, and shares
detection between candidates only when pixels and detector parameters match.
All recognition candidates still run. Cached Readers stay in memory until the
extractor closes. Page diagnostics expose
`easyocr_exhaustive_detection_calls` (cache misses in exhaustive variants),
`easyocr_exhaustive_detection_cache_hits`, and
`timings_ms.ocr_footnote_refinement_ms` when footnote refinement is requested.
Readtext fallback detections are excluded from these cache counters; their
use remains reported by `easyocr_fallback_count`.
Alternate Readers inherit the saved constructor settings, including languages
and recognition network. DBNet18 uses a short-side canvas, while CRAFT uses a
long-side canvas; the availability probe uses a small canvas explicitly.

Exhaustive policy applies to higher-scale regional recovery and footnote
rereads too. A document can therefore execute many more inference calls than
the number of variants in its initial page pass. Measure `easyocr_calls`,
`timings_ms.ocr_ms`, `timings_ms.ocr_targeted_refinement_ms`, and the footnote
timing separately before estimating total runtime.

Rasterization preserves the requested scale when the resulting dimensions are
within `max_render_pixels`. It reduces scale only when PDFium's rounded pixel
dimensions would exceed that limit; reductions are recorded in page
diagnostics under `render_limit_reductions`.

Successful complete extraction returns exit code `0`, including recoverable
non-degrading warnings. Partial extraction and operation failure return
non-zero codes while retaining diagnostic output where possible. Usage errors
return `2`.

Corpus reports include p50/p95/p99 page latency split by extraction strategy,
current and peak RSS in bytes for the Python process, PDFium document/page/textpage
acquisitions and an explicitly labelled estimate of adapter-visible FFI calls.
Memory metrics identify their source and process scope; unavailable metrics are
null, never fabricated as 0.
The peak is the OS high-water mark for the Python process since process start,
not a sum of subprocesses or a per-document resettable peak. The FFI estimate
is intended to decide whether a future native batch extension is justified; it
is not presented as an internal PDFium profiler.

`pdftext report` runs the real extractor over multiple corpus PDFs and emits
JSON with per-document and per-page strategies, escalation reasons, OCR
rotations, visual tables, warnings and timings. It is an operational report,
not an accuracy score; correctness still requires reviewing the recovered
text against the original PDF.

`pdftext compare` runs the built-in extractor views and optional PyMuPDF
reference adapter, reporting text size, timing, repeated lines, accented words
and pairwise normalized coverage. It reports a non-zero exit status when the
requested reference is unavailable, fewer than two valid results exist, or any
requested adapter fails. It never substitutes another adapter as reference;
diagnostic results may still be printed. The reference is observational, not
ground truth; use `--include-text` for manual review. To compare explicit
Paddle OCR profiles, select the backend family and model profile separately:

```bash
pdftext compare documento.pdf --adapters structured-balanced pdfium-raw pymupdf \
  --ocr-engine paddle --language pt-BR --ocr-model-profile pt
pdftext compare documento.pdf --adapters structured-balanced pdfium-raw pymupdf \
  --ocr-engine paddle --language pt-BR --ocr-model-profile pt-v6-small
```

The language selects the recognition language, `--ocr-engine` selects the
backend family, and `--ocr-model-profile` selects a Paddle model set.

The raw native page dump preserves PDFium character index, Unicode, geometry,
origin, angle, font metadata, fill/stroke RGBA, text render mode, generated/
hyphen/mapping flags and MCIDs when available. Page objects retain type,
bounds, transform matrix and marked-content ID; annotations retain contents,
appearance streams and object counts. Every optional field has a corresponding
feature-detection capability flag instead of making extraction fail on older
PDFium builds.

Normal extraction keeps `native_evidence=None` to avoid retaining hundreds of
thousands of low-level PDFium objects in long documents. Set
`ExtractorConfig(retain_native_evidence=True)` when an in-memory evidence audit
is required; `inspect --raw-page-json` does this automatically and processes
only the requested page. `overlay --page` is likewise page-targeted. Overlay
uses native extraction by default; OCR-capable modes accept the same
engine/provider selection as extraction and never impose `balanced` implicitly.

OCR variants are batched in bounded groups inside one model process. The
default batch size is 3 and can be changed with `--ocr-batch-size`; use
`--no-ocr-quality-variants` when a single fast OCR pass is preferable. The
optional `--workers` flag parallelizes independent documents using spawned
processes; it is intentionally opt-in because each OCR worker owns a model
copy and therefore increases memory usage.

Region-level recovery is available through `OcrRegionRefiner`. A request uses
PDF coordinates and may combine scale factors, arbitrary rotations and a
textual or numeric selection goal; selected OCR tokens are always mapped back
to the original page and marked with `ocr_region` provenance. The same
component is used internally for visual tables, figure panels, axes, labels
and small numeric values.

### OCR RGB budget gate

Recovery regions are OCR-processed at multiple scale factors (1.0×, 1.5×,
2.0×) when quality signals indicate small, sparse or damaged text.  To prevent
`STATUS_ACCESS_VIOLATION` crashes in Paddle's C++ inference runtime — caused by
consecutive very large image allocations — the refiner applies a configurable
RGB memory budget before creating any upscaled variant.

The two controlling environment variables are:

| Variable | Default | Purpose |
|---|---|---|
| `PDFEXTRACTOR_OCR_RGB_BUDGET_MIB` | `8.0` | Max uncompressed RGB size (MiB) per OCR scale variant.  Variants whose estimated `width × height × 3` bytes exceed this limit are skipped. |
| `PDFEXTRACTOR_OCR_DEBUG_LOG` | (unset) | Absolute path to append a machine-readable debug log.  When set, every OCR call writes `CALL_START` / `CALL_END` entries with memory metrics, and every recovery decision writes `REGION_SELECTED` / `OCR_SCALE_PLAN` / `OCR_SCALE_BLOCKED` entries. |

The RGB budget is parsed when OCR recovery first needs it. Invalid values
produce a configuration error that identifies the variable. The debug log path
is resolved when diagnostics are emitted.

The detection model's `limit_side_len` is also derived from the budget
(`max(960, round(√(budget_bytes/3) × 1.2 / 32) × 32)`) so that variants
within budget are processed at full resolution rather than being normalised to
the legacy 960 px cap.  For the default 8 MiB budget this yields 2016 px.

See `docs/ocr-rgb-budget-crash-fix.md` for the full incident analysis.

In `balanced` mode, OCR is selective by default: layout and local region
quality are evaluated first, healthy native regions are kept, suspect crops
are recovered, and only scans or pages with broadly damaged regions are
promoted to full-page OCR. The decision, affected region IDs, promotion
reasons, pass counts and provenance are exposed in page diagnostics.

The deterministic table cascade handles strict vector grids, relaxed grids
and borderless tables. Acceptance requires recurrent X tracks, consistent row
shapes and explicit prose/code rejection; ordinary multi-column text and code
indentation therefore remain text. Visual structure/OCR remains the fallback
and requires raster containment plus cell-level textual support on mixed
pages, preventing chart axes and image diagrams from becoming tables.

## Architecture — canonical page content pipeline

Extraction follows a strict three-stage pipeline. Each stage has a single
responsibility; no stage makes decisions that belong to another.

```
Stage 1 — Evidence collection
  PDFium native characters + paths + images
    → NativePageEvidence

Stage 2 — Structure assembly  (assemble/)
  NativePageEvidence
    → layout regions  (layout/engine.py + layout/assign.py)
    → reading order   (text/reading_order.py)
    → table geometry  (tables/geometry.py)
    → StructuredTable (tables/)
    → PageContentBlock[]  (assemble/content.py)
        block_id, kind (ContentKind), bbox, order_index,
        text, table_id, heading_level, confidence
    → StructuredPage.content_blocks

Stage 3 — Serialization  (renderers/)
  PageContentBlock[]
    → render_markdown() reads blocks in order_index order
    → dispatches by ContentKind: text, title, table, header, footer, figure
    → no geometry, no region lookup, no table positioning
```

### Separation guarantees (invariants)

| ID | Guarantee |
|----|-----------|
| INV-01 | A line outside all valid table cells is never suppressed by a TABLE region label alone |
| INV-02 | A line represented by a table block does not also appear as prose |
| INV-03 | A table block appears only on the page that holds its physical fragment |
| INV-04 | The logical `document.tables` list does not reposition physical content across pages |
| INV-05 | A TABLE region with empty or missing cells falls back to prose text (no content loss) |
| INV-06 | The Markdown renderer contains no geometric logic (`BBox`, `overlap_ratio`, `area`) |
| INV-07 | Unicode NFC normalization is applied uniformly to all text blocks |
| INV-08 | Each valid table appears at most once per page regardless of region overlap |
| INV-09 | Block order is deterministic for identical input evidence |
| INV-10 | No assembly rule is specific to any reference document or corpus coordinate |

### Key types

| Type | Module | Role |
|------|--------|------|
| `ContentKind` | `document.py` | Enum for block semantic type (TEXT, TITLE, TABLE, HEADER, FOOTER, …) |
| `PageContentBlock` | `document.py` | One renderable unit on a page: kind + text + bbox + order |
| `PageContentAssemblyResult` | `assemble/content.py` | Output of `assemble_page_content()`: blocks + diagnostics |
| `TableGeometryCandidate` | `tables/geometry.py` | Connected-component grid region from native paths |

## Python API

```python
from structured_pdf_text import PdfTextExtractor

extractor = PdfTextExtractor()
result = extractor.extract("documento.pdf")
print(result.reading_text)
```

For logical tables that continue across adjacent pages:

```python
from structured_pdf_text.config import ExtractorConfig

extractor = PdfTextExtractor(
    ExtractorConfig(mode="balanced", merge_cross_page_tables=True)
)
result = extractor.extract("documento.pdf")
```

Cross-page table resolution uses actual page boundaries together with column
counts, normalized X tracks, repeated headers, widths, cell-type profiles,
continuation markers and intervening titles. Accepted and rejected candidate
pairs are available in
`result.diagnostics.facts["cross_page_table_decisions"]` with their scores and
reasons; merged tables retain every source page in `page_fragments`.

### Runtime resource failures

OCR extraction is intentionally strict about infrastructure failures. If the
required OCR operation cannot run because the process runs out of memory,
extraction is aborted and the CLI returns a non-zero exit status. A resource
failure is different from an imperfect OCR result:

- resource/runtime failure: abort;
- low-quality or empty OCR result without a resource failure: warning and continue.

The normal extraction runtime never silently reduces OCR resolution to fit
available memory. Insufficient resources are reported as a fatal runtime error
instead of silently changing extraction quality. Fatal failures that reach
Python/Paddle as exceptions are controlled here; hard process termination by a
kernel or cgroup OOM kill requires future OCR worker isolation.

### Memory guidance

OCR detectors can have a high peak memory footprint during full-page OCR at the
normal render scale. The observed validation peak
was approximately 10.8 GiB RSS.

Provisional guidance:

- 16 GiB available to the extraction environment: practical lower bound;
- 20–24 GiB available to WSL/VM: recommended;
- 32 GiB physical workstation RAM: recommended;
- 8 GiB swap: recommended.

These values are provisional and will be revisited after the planned 1000-page
endurance benchmark. The runtime does not automatically lower text-detection
resolution or switch to a lower-quality OCR profile when memory is scarce.


## License

This project is licensed under the Apache License 2.0.
See the [LICENSE](LICENSE) file for details.

Third-party components used by this project:

| Component       | License          | Notes                          |
|-----------------|------------------|--------------------------------|
| pypdfium2       | Apache 2.0       | bundles PDFium (BSD 3-Clause)  |
| PaddleOCR       | Apache 2.0       | optional backend               |
| EasyOCR         | Apache 2.0       | optional backend               |
| RapidOCR        | Apache 2.0       | optional backend               |
| ONNX Runtime    | MIT              | RapidOCR inference provider    |
| Tesseract 5     | Apache 2.0       | external tool, not bundled     |

See the [NOTICE](NOTICE) file for required attributions.
OCR model weights are obtained separately by the user and are not
distributed with this package.


## Desacoplamento das engines OCR

A migração conecta os quatro pipelines ao contrato de trabalho autorizado,
mantém integração e conservação no núcleo e fixa identidades de modelos e
providers. Consulte [o estado de implementação e os gates pendentes](docs/OCR_Decoupling_Implementation_Status.md).

RapidOCR usa uma policy local que remove bordas sintéticas do raster e mantém
o frame dentro da autorização original. Essa correção passou nos testes de
geometria e nos controles operacionais. Uma policy local de admissão preserva
observações rejeitadas nos diagnósticos e impede sua integração: no perfil
medium, os controles de figuras sem texto V5-21/V5-31 passaram nos dois providers.
O small ainda produz falso positivo em V5-21; a homologação completa segue aberta.

A seleção regional, células, notas, linhas fracas, dados críticos e refinamentos
dedicados de figuras/tabelas visuais aplicam candidatas por propostas tipadas,
com conservação central. Dados críticos preservam a posição e a linhagem dos
tokens; figuras e tabelas têm aplicação atômica nos alvos reais. Ranking, fusão,
recuperação e segmentação de figuras têm policies locais por engine.
O helper público de células aceita `ocr_facade=` para usar o mesmo serviço
autorizado. O worker Paddle usa protocolo 5 com geometria, proveniência e
diagnósticos preservados. Equivalência de corpus e homologação de plataforma
ainda estão pendentes; os relatórios registram os controles reprovados.

A descoberta de tabela em recortes preserva o frame do raster, a rotação e a
linhagem até as células refinadas. Há testes sintéticos pela API real para
tabelas híbridas, células vazias e conservação de conteúdo crítico. O runner
[`scripts/run_ocr_endurance.py`](scripts/run_ocr_endurance.py) mede repetições com
o mesmo runtime, memória/descritores e limpeza de workers; exemplos e limites
estão no [checklist de aceitação](docs/acceptance-checklist.md).

- [Histórico de PR-01 a PR-03](docs/OCR_Decoupling_PR01_PR03.md)
- [Baseline de 20/10/2026](docs/OCR_Decoupling_Baseline_2026-10-20.md)
- [Plano completo](docs/Plan_Decoupling_OCR_Engines.md)
- [Guia para adicionar uma engine](docs/OCR_Pipeline_Extension.md)
- [Decisão de arquitetura](docs/adr/0001-authorized-ocr-pipelines.md)
