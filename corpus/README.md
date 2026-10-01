# Local corpus

Place real PDFs here for manual validation of the extractor. PDF files
are ignored by Git by default.

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
