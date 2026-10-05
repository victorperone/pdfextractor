# Local corpus

Place real PDFs here for manual validation of the extractor. PDF files
are ignored by Git by default.

The local Stress V4 acceptance set is verified against
[`acceptance-corpus.lock.json`](acceptance-corpus.lock.json). After placing the
PDF, reference, manifest, and validation report alongside the lock, run:

```bash
python scripts/provision_acceptance_corpus.py
```

The lock records sizes, page count, and SHA-256 values but does not redistribute
the corpus or assert its license. Operators remain responsible for the source
corpus terms.

Suggested organization:

```text
corpus/
├── digital-simple/
├── digital-multicolumn/
├── digital-bad-font/
├── duplicate-ocr-layer/
├── hybrid-page/
├── scan-clean/
├── scan-poor/
├── table-bordered/
├── table-borderless/
├── table-cross-page/
├── rotated/
└── legacy-system/
```

For the current deliverable, the focus is on observing native evidence:

```bash
PYTHONPATH=src python3 -m structured_pdf_text.cli inspect \
  corpus/digital-simple/exemplo.pdf --page 1 --raw-page-json

PYTHONPATH=src python3 -m structured_pdf_text.cli extract \
  corpus/digital-simple/exemplo.pdf --output reading
```

The raw dump shows the characters in the sequence exposed by PDFium, Unicode,
coordinates, origin, font, size, angle, flags, images, paths,
annotations, page boxes, and available capabilities.
