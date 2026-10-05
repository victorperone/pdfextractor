# Document_Text_Stress_V1 — digital text corpus (PDFExtractor)

## Purpose and Limits

This set was generated for the new branch `feat/native-text-fidelity`, without
changing the parser or OCR. The PDF contains **native digital text**, not
rasterized pages. The goal is to determine at which stage a text occurrence is
lost or changes context: native capture, IR/conservation, structure
(tables/columns), renderer.

**There is no promise that all possible PDF format features are covered.** V1
contains 200 pages, with 20 challenge families and 10 deterministic variants per
family. There are repeated headers and footers on every page, text at borders,
rotated text, different fonts and sizes, Unicode/identifiers, lists, hierarchy,
paragraphs, 2–3 columns, asymmetric columns, sidebars, tables with/without
borders, merged cells, financial values, two-page continuation, and combined
scenarios. Some pages deliberately invert the drawing order in the PDF relative
to logical reading. OCR remains **untouched** in this project: the current OCR
tests must continue to pass, and documents with images will have their own suite
later.

### Files

- `Document_Text_Stress_V1.pdf`: 200-page native PDF for evaluation.
- `Document_Text_Stress_V1.reference.json`: consolidated reference containing
  generation intent per page: regions, text units, logical order, fonts,
  estimated coordinates, and table cells.
- `Document_Text_Stress_V1.reference.jsonl`: the same data, one page per line,
  for streaming processing.
- `Document_Text_Stress_V1.manifest.json`: counts, families, schema version,
  seed, and SHA-256 of the PDF and references.
- `Document_Text_Stress_V1_generator.py`: reproducible generator **for testing
  only**, requires ReportLab and DejaVu fonts installed locally. Do not package
  or distribute font files.
- `validate_native_text_stress.py`: artifact integrity checker, independent of
  PDFExtractor; uses pypdfium2 to verify pages and digital markers without OCR.
- `test_native_text_stress_fixture.py`: pytest tests for corpus integrity, before
  testing the parser.

### Reference Contract

Each page record has `units`, `regions`, `tables`. Each unit has `unit_id`,
`page`, `exact_text`, `source_kind`, `visible`, `region_id`, `role`,
`source_draw_order`, `logical_reading_order`, `bbox_top_origin_pt`,
`font_family`, `font_size_pt`, `rotation_deg`, `table_id`, `cell_id`. The table
declares `row_index`, `column_index`, `rowspan`, `colspan`, `exact_text`, and
`text_unit_ids` per cell. `logical_reading_order` was defined by the generator,
NOT inferred from the internal PDF order.

`bbox_top_origin_pt` of units is an estimate via font metrics (not visible ink
measurement). Allow geometric tolerance; conversely, the text/cell and the
occurrence identity must be strictly validated. The cell `exact_text` is the
text the author intended to draw; its `text_unit_ids` record the visual break
actually drawn. Differentiate literal equality from permissive normalization
(ligatures, Unicode, spaces, and hyphenation), reporting both; **never normalize
numbers/IDs in a way that makes errors disappear**. Not every visual line
sequence is a single PDFium native sentence/line.

`is_repeated_header` identifies the header copy on the second page of a table;
it does not mean it should disappear in the conservation test. Do not enable
header/footer suppression during the first test runs.

### Corpus Validation (before testing the parser)

```bash
python validate_native_text_stress.py --root .
pytest -q test_native_text_stress_fixture.py
```

Validation must confirm pages, ID uniqueness and coverage, cell associations,
and hashes. It must also find a distinct `CASE-NNN` in the native text of each
page. **This does not prove that all text was extracted by PDFium or
PDFExtractor**; the real parser test comes later.

### Development Steps (independent gate per phase)

1. **Native capture:** extract characters/lines from the PDF layer without using
   OCR to mask losses. Compare reference units per page and geometry; list
   missing, extra, duplicates, and Unicode transformations. Analyze font Unicode
   failures separately, without removing adversarial benchmark cases.
   `source_draw_order` is not reading order.
2. **IR and conservation:** each captured occurrence must be rendered/owned by a
   table/figure or explicitly suppressed with an auditable ledger. Check
   `content_unaccounted_lines`, duplicates, and lineage; a page may be
   character-complete and still fail at this step.
3. **Structure:** validate cells by `(table_id, page, row_index, column_index,
   rowspan, colspan)`, headings/lists, footnotes, sidebars, and
   `logical_reading_order`. Requires structured/JSON reference, not just
   Markdown. A complete table with swapped rows/columns fails.
4. **Serialization/integration:** validate JSON and Markdown without
   losing/duplicating text. Run `balanced` without omitting repeated content.
   Test classification effects, fallback, and any OCR supplements separately.
5. **Repetition and normalization** are reserved for a separate later test run.
   Do not use the PDF/JSON as a production lookup, nor adapt parser rules to its
   texts, IDs, coordinates, or pages.

### Initial Development on the Already-Created Branch

The branch `feat/native-text-fidelity` has already been created from
`feat/ocr-adaptive-quality`. Confirm:

```bash
git branch --show-current
git status --short
git rev-parse HEAD
```

Suggested file organization in the repository:

```text
tests/corpus/native_text_stress/
    Document_Text_Stress_V1.pdf
    Document_Text_Stress_V1.reference.json
    Document_Text_Stress_V1.reference.jsonl
    Document_Text_Stress_V1.manifest.json
    Document_Text_Stress_V1_generator.py
    validate_native_text_stress.py
    README_NATIVE_TEXT_STRESS.md

tests/test_native_text_stress_fixture.py
```

1. Copy the artifacts; run `python tests/corpus/native_text_stress/validate_native_text_stress.py --root tests/corpus/native_text_stress`.
2. Run `pytest -q tests/test_native_text_stress_fixture.py` and `pytest -q`.
3. Make the **first commit with corpus/evaluation only**, without changing the parser: `test(corpus): add deterministic native text fidelity benchmark`.
4. In the next round, implement per-step measurements, starting with a small page
   selection, and only then run all 200. The 200-page benchmark does not replace
   the existing OCR tests.
5. Save the code SHA, fixture hashes, actual date, and execution parameters in
   the report.

### Known V1 Limitations and Future Evolution

The PDF was created by a mechanism independent of PDFExtractor (ReportLab) and
pre-verified by PDFium, but **only one PDF generator** was used in this version.
After the first round, add a second backend with a reference generated before the
PDF (for example, an HTML/Chromium pipeline or another layout tool), pages with
CJK/RTL using correct shaping/bidi, optional content, clipping,
annotations/AcroForm, transparency, and deliberate overlaps. These features
require separate visibility criteria to avoid an invalid reference being treated
as a parser failure. This V1 does not test rasterized images or OCR; the OCR
architecture must be preserved.
