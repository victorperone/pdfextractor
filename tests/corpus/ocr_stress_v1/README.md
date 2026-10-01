# Document_OCR_Stress_V1

Modular synthetic corpus of 60 pages for OCR inspection, geometry,
raster regions, tables, and integration. All content is fictitious and generated
locally; there are no business documents, downloaded images, or commercial
fonts in the corpus.

This delivery contains only the generator, the manifest, and the integrity tests.
It does not modify `src/structured_pdf_text`, does not load OCR models, and does not attempt
to fix parser defects found during inspection.

## Dependencies

The generator uses the dependencies already planned in the project:

- ReportLab to create the PDF layer and QR/Code128;
- Pillow for deterministic transformations;
- pypdfium2 to temporarily rasterize image content and validate the
  final PDF.

No dependency is automatically installed. The generator does not depend on
external fonts: the base text uses ReportLab's PDF14 fonts.

## Generation on WSL

From the repository root:

```bash
python3 tests/corpus/ocr_stress_v1/generate.py
```

This creates:

- `manifest.json` and `scenarios.md` alongside the generator;
- `outputs/Document_OCR_Stress_V1.pdf`, ignored by Git.

The complete PDF has exactly 60 pages. The manifest records the SHA of the PDF,
dimensions, block, presence of native text, raster regions, tables,
figures, continuations, and expected fictitious indicators.

## Subsets

Select pages by number, list, or range. The subset manifest
preserves `source_page` to relate each generated page to the original page:

```bash
python3 tests/corpus/ocr_stress_v1/generate.py --pages 15-16
python3 tests/corpus/ocr_stress_v1/generate.py --pages 36-37 38-39
python3 tests/corpus/ocr_stress_v1/generate.py --pages 56-57 --output-dir /tmp/ocr-stress-v1
```

Continuations 15–16, 36–37, 38–39, and 56–57 must be run together
when the goal is to evaluate continuity. A subset is a new PDF and
may have different internal references than the complete PDF.

## Integrity Tests

The tests do not load PaddleOCR, PaddlePaddle, or any model:

```bash
pytest -q tests/test_ocr_stress_corpus.py
```

They generate temporary copies, check 60 pages and their order, dimensions,
hashes, markers, presence/absence of selectable text, image objects,
subsets, continuations, and low-resolution rendering.

For an independent manual validation of the parser:

```bash
python3 - <<'PY'
from pathlib import Path
import pypdfium2 as pdfium

pdf = pdfium.PdfDocument(Path("tests/corpus/ocr_stress_v1/outputs/Document_OCR_Stress_V1.pdf"))
print("pages:", len(pdf))
PY
```

After the visual review, the PDF can be used in separate PDFExtractor invocations. Record the SHA of the code, pages, mode, OCR policy, threads,
time, memory, exit code, and observations; do not turn manifest indicators into production rules.
