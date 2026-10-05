# EasyOCR pipeline regression corpus

This dedicated corpus covers the scenario matrix from the development plan:
clean and degraded scans, small text, three rotations, two and three columns,
headings, lists, footnotes, forms, bordered and borderless tables, Brazilian
financial values and identifiers, hybrid pages, incorrect hidden OCR, screenshots,
Portuguese accents, hyphenation, dark backgrounds, JPEG artifacts, and stamps.

Generate the deterministic PDF, manifest, and per-page Markdown references with:

```bash
python3 tests/corpus/easyocr_pipeline/generate.py
```

The PDF is generated under `outputs/` and is intentionally not tracked. Its 29
pages are synthetic and use fictional data. The earlier 60-page OCR stress
corpus remains available for broader integration and table regressions.

The generated corpus supports repeatable local tests. It does not replace
validation against real documents or an EasyOCR installation with its models.
