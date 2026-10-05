# Manual corpus validation — M4/M5/M7/M8/M9 and OCR

Run date: 2026-09-13  
Modes: `balanced` for OCR and `native` for the full scan; PaddleOCR models in
local cache.

This validation is an inspection of a real run, not an accuracy metric.
The markers below were checked against the expected content indicated in the
corpus itself.

## Run summary

| Document | Pages | Tables | Warnings | Time |
|---|---:|---:|---:|---:|
| `benchmark_controlado_v1.pdf` | 12 | 7 | 0 | 262.5 s |
| `Document_AI_V2.pdf` | 42 | 7 | 0 | 410.9 s |

The file `benchmark_03_medium_268.pdf` was also fully processed in native mode
after the fixes: 268 pages, 444,648 raw characters, zero warnings, peak RSS of
approximately 444 MB, and 14 tables via text tracks. In targeted `balanced`
validations, a 14×8 visual matrix was preserved and charts/diagrams without
text cells were rejected as tables.

## Verified points

| Pages | Coverage | Observed result |
|---|---|---|
| Benchmark 1–2 | header, title, accents, and native table | recovered; table and special characters preserved |
| Benchmark 8 | formulas and table | native formulas preserved; table recovered |
| Benchmark 9–11 | raster tables | OCR recovered cells; page 10 used 270° rotation and page 11 0° rotation |
| DocumentAI 15–16 | wide/narrow tables | text and lines recovered; narrow cells maintained as table rows |
| DocumentAI 19–20 | multi-page table | fragments joined; sequence PED-2026001 to PED-2026030 present |
| DocumentAI 22 | charts and axes | titles, `R$ mil`, `Quantidade`, months `Jan`–`Jun`, percentages, and values `170`–`120` recovered by enlarged OCR |
| DocumentAI 26–30 | clean OCR, contrast, noise, skew, and small font | pages 28–30 validated in targeted runs; main codes and values present, with residual noise only in difficult characters |
| DocumentAI 31–33 | physical rotation | 90°, 180°, and 270° rotations corrected, with `GS2-ROT-*` codes present |
| DocumentAI 34 | metadata rotation / native layer | the visual signal remains diagnostic, native text is preserved, and unnecessary OCR was suppressed; text does not duplicate |
| DocumentAI 35 | incorrect hidden layer | OCR was promoted to main source; `GS2-VISIBLE-035`, `R$ 3.535,35`, and `APROVADO` prevail |
| DocumentAI 36 | watermark and stamp | main body recovered; watermark appears fragmented but does not replace the body |
| DocumentAI 39–42 | visual redaction, contract, and final marker | native content and `GS2-END-OF-CORPUS-42` recovered |

## Corrections applied after inspection

1. OCR became the main source on predominantly rasterized pages, preventing an
   incorrect hidden text layer from being retained.
2. `invisible_text` is treated only as evidence of complexity; native lines are
   not erased before the regional/OCR decision.
3. Pre-processing variants were added for noise, low contrast, and small font.
4. Axes, months, legends, and small chart labels receive enlarged OCR.
5. Variant selection merges compact high-confidence tokens when noise fragments
   a value or code into multiple pieces.
6. OCR variants are grouped into limited batches; reports can use opt-in
   multiprocessing per document.
7. Batch size and the use of quality variants are now configurable via the API
   and CLI, defaulting to the highest-recall configuration.
8. A generic per-region refiner centralizes crop, enlargement, rotation,
   coordinate remapping, and selection/fusion of text or numeric hypotheses;
   tables and figures use the same component.
9. The reading of page 22 recovered in a final run the three chart titles,
   `R$ mil`, `Quantidade`, `Jan`–`Jun`, `170`–`120`, `8`–`0`, and the four
   expected percentages, without warnings.
10. The pipeline now runs layout and quality gate before OCR. In a real run,
    page 22 triggered only the figure region (`mixed`), page 30 promoted full
    OCR, page 34 remained exclusively native, and page 35 prioritized the
    visible raster over the incorrect hidden layer.
11. Text tracks recovered the borderless table on page 17 as 5×5 and the
    digital tables on pages 13, 14, and 18; the prose classifier correctly
    rejected the column layouts on pages 8 and 9.
12. The multi-page resolution joined only tables 19–20 in the larger document
    and 10–11 in the benchmark. Independent adjacent pairs were preserved and
    each acceptance/rejection now exposes score, reasons, and geometric facts.
13. A full native benchmark run recorded p50/p95/p99 per strategy, FFI
    acquisitions/estimate, and peak RSS of approximately 108 MB, processing
    all 12 pages without warnings.
14. The native dump preserved RGBA, render mode, and matrix in
    characters/objects; on page 7 of the benchmark, tagged objects also exposed
    MCID and the `link` annotation reported bbox and object count without
    failing due to missing appearance.
15. The PDFium effective box now defines the coordinate system when inherited
    MediaBox/CropBox diverge from the render; this eliminated offsets of
    approximately 50 points in the 268-page document.
16. Detailed PDFium evidence became opt-in in the structured result and is
    automatically enabled by `inspect --raw-page-json`; the peak RSS in the
    268-page file dropped from approximately 965 MB to 444 MB.
17. The native reconstruction now preserves spaces in italic fonts and baseline
    glyphs (`_`, comma, and period), recovering identifiers such as
    `render_cppn` and expressions such as `norm_x`.
18. Table classifiers now reject prose with decorative rules, code blocks,
    charts without cells, and low-confidence diagrams; the control raster tables
    maintained 35/35 cells in all three orientations.
19. Figure OCR suppresses groups of large, repeated pictograms confused with
    characters, while keeping small labels and axis numbers.

## Quality backlogs

- page 29 can still improve accentuation of skewed text (`OCR` and `SKEW`
  characters); the deskew variant was evaluated but was not automatically
  enabled because it worsened spatial order in one run;
- charts are still not converted into semantic data, only into text;
- validation remains manual against the original image, as the corpus does not
  provide complete textual ground truth for each character.
