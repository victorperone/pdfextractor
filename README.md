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
through an optional, lazy PaddleOCR adapter; the runtime/model must be
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
- OCR engine contract, OCR token-to-line reconstruction and lazy PaddleOCR
  adapter with explicit missing-runtime diagnostics, CPU/WSL compatibility and
  rotated-page fallback
- cross-page table signatures, repeated-header detection, continuation
  evidence and logical table merging with source-page fragments
- document-level diagnostics, page boundaries and repeated header/footer
  signatures with configurable reading-text filtering
- Markdown rendering with page sections and structured table blocks
- per-page stage timings, document assembly timing and cooperative timeout
  handling with partial-document status
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

The `balanced` and `ocr` extraction modes use PaddleOCR with locally stored
model weights. Model downloads happen during setup, not during extraction.

**The runtime never downloads models. If the setup is incomplete, extraction
fails immediately with a clear error before processing any page.**

### Step 1 — Download models

```bash
python -m structured_pdf_text.cli setup-models
```

This downloads the four required models to
`~/.cache/pdfextractor/paddlex/official_models/`:

- `PP-LCNet_x1_0_doc_ori` — document orientation classifier
- `PP-LCNet_x1_0_textline_ori` — text-line orientation classifier
- `PP-OCRv5_server_det` — text detection
- `latin_PP-OCRv5_mobile_rec` — text recognition (Latin script)

### Step 2 — Verify readiness

```bash
python -m structured_pdf_text.cli models-status
```

Expected output when ready:

```text
OCR model home:
  /home/user/.cache/pdfextractor/paddlex/official_models

[ok] PP-LCNet_x1_0_doc_ori
[ok] PP-LCNet_x1_0_textline_ori
[ok] PP-OCRv5_server_det
[ok] latin_PP-OCRv5_mobile_rec

Offline OCR readiness: READY
```

### Step 3 — Extract offline

```bash
python -m structured_pdf_text.cli extract documento.pdf \
  --mode balanced \
  --output markdown \
  -o documento.md
```

Extraction runs completely offline. If models are missing, the process fails
before reading any page and prints the path to run `setup-models`.

### Optional network-isolation check on Linux/WSL

When validating the offline runtime with `sudo unshare --net`, remember that
`sudo` normally changes `HOME` to `/root`. If the OCR models were installed in
the current user's cache, preserve the same PaddleX cache path explicitly.

```bash
export PADDLE_PDX_CACHE_HOME="$HOME/.cache/pdfextractor/paddlex"

sudo --preserve-env=PADDLE_PDX_CACHE_HOME unshare --net -- bash -lc '
cd /path/to/pdfextractor
source .venv/bin/activate

python -m structured_pdf_text.cli models-status

python -m structured_pdf_text.cli extract documento.pdf \
  --mode balanced \
  --output markdown \
  -o documento.md
'
```

`models-status` must report `Offline OCR readiness: READY`. During extraction,
no model hoster lookup or download should occur.

If the local `sudo` policy does not allow preserving environment variables,
set `PADDLE_PDX_CACHE_HOME` explicitly inside the isolated shell instead.
Use the same cache directory that was used during `setup-models`; do not copy
models into `/root` only for this validation.

If model hosts are blocked by organizational network policy, use offline model
provisioning from an approved machine or an internal artifact repository. Copy
the four model directories from an approved installation into:

```text
<cache>/official_models/
```

Then run `models-status`; it must report `Offline OCR readiness: READY`.

If the network allows the model hosts but TLS inspection requires a custom
corporate CA, configure Python/PaddleX using the CA and proxy settings approved
by your organization. Do not disable TLS verification or bypass organizational
network controls.

## Run tests

```bash
pytest
```

## CLI examples

```bash
# Native extraction (no OCR required)
pdftext extract documento.pdf
pdftext extract documento.pdf --output raw
pdftext extract documento.pdf --output json

# OCR-assisted extraction (requires setup-models)
pdftext extract documento.pdf --mode balanced --output markdown
pdftext extract documento.pdf --mode balanced --ocr-quality-policy adaptive
pdftext extract documento.pdf --mode balanced --ocr-quality-policy baseline
pdftext extract documento.pdf --mode balanced --ocr-quality-policy exhaustive
pdftext extract documento.pdf --mode balanced --output json
pdftext extract documento.pdf --mode ocr --output reading
pdftext extract documento.pdf --best --output markdown -o output.md

# Inspection and diagnostics
pdftext inspect documento.pdf --page 1
pdftext inspect documento.pdf --page 1 --raw-page-json
pdftext overlay documento.pdf --page 1 --out page-1.png
pdftext report corpus/*.pdf --mode native
pdftext report corpus/*.pdf --mode balanced --merge-cross-page-tables
pdftext report corpus/*.pdf --mode native --workers 2
pdftext compare documento.pdf --adapters structured-native pdfium-raw pymupdf

# OCR model management
pdftext setup-models
pdftext setup-models --language pt
pdftext models-status
```

`--mode balanced` and `--ocr-quality-policy` are independent choices: the
quality policy selects the OCR path used by a mode, rather than being an
alternative mode. `exhaustive` runs every eligible quality variant and emits
an informational resource-use warning; it does not automatically reduce OCR
quality. The `balanced` and `ocr` modes use the optional PaddleOCR adapter. On CPU/WSL
the adapter disables MKL-DNN/OneDNN for compatibility. Models are loaded from
explicit local paths; remote model-source checks are disabled at runtime.
Model paths and behaviour can be overridden through `PaddleOcrEngine`
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
`total_ms` and `assemble_ms`. A cooperative document budget can be set with
`SecurityLimits(document_timeout_seconds=...)`; when reached, the result is
returned as `partial_success` with processed page boundaries preserved.

Corpus reports include p50/p95/p99 page latency split by extraction strategy,
current/peak RSS, PDFium document/page/textpage acquisitions and an explicitly
labelled estimate of adapter-visible FFI calls. The estimate is intended to
decide whether a future native batch extension is justified; it is not
presented as an internal PDFium profiler.

`pdftext report` runs the real extractor over multiple corpus PDFs and emits
JSON with per-document and per-page strategies, escalation reasons, OCR
rotations, visual tables, warnings and timings. It is an operational report,
not an accuracy score; correctness still requires reviewing the recovered
text against the original PDF.

`pdftext compare` runs the built-in extractor views and optional PyMuPDF
reference adapter, reporting text size, timing, repeated lines, accented words
and pairwise normalized coverage. The selected reference is explicitly marked
as observational rather than ground truth; use `--include-text` when the full
outputs are needed for manual review.

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
only the requested page. `overlay --page` is likewise page-targeted.

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

The current PP-OCRv5 server detector can have a high peak memory footprint
during full-page OCR at the normal render scale. The observed validation peak
was approximately 10.8 GiB RSS.

Provisional guidance:

- 16 GiB available to the extraction environment: practical lower bound;
- 20–24 GiB available to WSL/VM: recommended;
- 32 GiB physical workstation RAM: recommended;
- 8 GiB swap: recommended.

These values are provisional and will be revisited after the planned 1000-page
endurance benchmark. The runtime does not automatically lower text-detection
resolution or switch to a lower-quality OCR profile when memory is scarce.
