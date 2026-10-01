# Conditional OCR Review — step `916e1dc`

## Conclusion

The previous delivery was audited in the real repository. The current commit is
`916e1dc30af66bb74b9977438e2bc0de5a06521c`, the working tree was clean at the
start of the review, and `origin/feat/ocr-adaptive-quality` pointed to the same
SHA. The comparison with `f18a03149c251a8b48b26c251484db642027ee02` contains only
corpus infrastructure, manifest, tests, and documentation; there are no changes
in `src/structured_pdf_text`.

The existing corpus was reused for the already-validated controls because its
main PDF and subsets remained consistent with the previous report. During the
investigation of the table sequence, page 30 revealed that the scenario declared
as multiline was not represented by the generator. The generator was fixed before
interpreting the OCR of page 30, the manifest and PDF were regenerated, and the
new integrity was tested. Structural validation continues to confirm 60 pages,
37 with native layer and 23 pure raster, manifest consistent with the PDF, and
source order `1..60`.

## Delivery Verification

| File/feature | Change in previous delivery | Test run | Result | Pending risk |
|---|---|---|---|---|
| `generate.py` | Deterministic generator and real crops of figure 15–16 | `generate.py --help`; manifest/PDF; specific continuity test | approved | continuations of other pairs are not shared source images |
| `manifest.json` | Region, source, layer metadata and `shared_image_id` | PDF SHA, 60 pages, dimensions, and order | approved | Code128 generated but not decoded locally |
| `test_ocr_stress_corpus.py` | Integrity, subsets, and 15–16 regression | `pytest -q tests/test_ocr_stress_corpus.py` | approved | none |
| `corpus_validation.md` | Structural evidence and previous baseline | review of hashes and artifacts | approved | visual inspection remains sampled |
| generator page 30 | Scenario corrected for multiline raster cells | manifest test and baseline extraction p30 | corpus approved; OCR reproduced the row association failure | table assembly remains pending |
| production OCR/native text | no change | `git diff f18a031..HEAD -- src/structured_pdf_text` | no file changed | no blocker at this step |

Commands executed in this review:

```bash
python3 tests/corpus/ocr_stress_v1/generate.py --help
PYTHONPATH=src python3 -m structured_pdf_text.cli extract --help
python3 -m compileall -q src tests
pytest -q
git diff --check
```

All completed successfully. No commit, push, merge, or PR was made in this round.

## Incremental OCR Diagnosis

The previous baseline was reused for pages 1, 2, 13–14, 15–16, and 19, since
the PDFs and implementation match the current corpus. Negative control 24 was
run in this review with:

```bash
PYTHONPATH=src python3 -m structured_pdf_text.cli extract \
  /tmp/ocr-stress-audit/subsets/p24/Document_OCR_Stress_V1_pages_24.pdf \
  --mode balanced --ocr-quality-policy baseline --output markdown \
  -o /tmp/ocr-stress-audit/p24.md
```

| Source page | Scenario | Policy | Observation | Result |
|---:|---|---|---|---|
| 1 | native control | baseline | native marker once | approved |
| 19 | decorative image without text | baseline | native text preserved, no invented OCR | approved |
| 2 | simple raster | baseline | marker, accents, currency, date, and percentage recovered | approved |
| 13 | mixed with raster receipt | baseline | image text alongside native text, no duplication observed | approved |
| 14 | mixed with raster block | baseline | raster block and native text preserved | approved |
| 24 | textual raster + graphic without text | baseline | `OCRS-024` recovered; the graphic label comes from the native layer, not from indiscriminate OCR | approved |
| 15–16 | continuous figure in landscape | baseline | two markers, common content, and two pages preserved | approved |

Outputs are in `/tmp/ocr-stress-audit/`, with Markdown, logs, manifests, and
timing statistics. Batch p24 completed with exit code 0, no error lines, in
9.85 s of wall time, with a peak of 2,472,668 kB.

The `adaptive` mode was not run on the approved controls. It was run only after
the failure was reproduced on page 29, per the plan's rule, and its results are
recorded below.

## Page 30: Multiline Cell Association Failure

The generator was fixed to draw real line breaks in cells and record this intent
in the manifest. With the corrected corpus, OCR recognized the secondary texts
(`mensal`, `estimado`, `revisado`, `controle`, `interno`, `atenção`), but the
table was serialized as additional rows with empty cells in the remaining
columns. The JSON showed seven physical rows, while the scenario requires four
logical rows with multiline content.

The diagnosis separates the stages: tokens appear in seven OCR lines with
coherent geometry; the table contains all tokens, but creates rows 2, 4, and 6
for the continuations. There was no loss in the OCR detector. The confirmed
origin is table association/serialization after recognition.

This fix is blocked in this round: the plan prohibits modifying text/assembly
modules without specific authorization. No production code was changed to mask
the failure.

## Decision and Next Step

There is no evidence-authorized OCR fix at this step. The diagnosis did not find
a loss in the enumeration, selection, crop, detection, recognition, or assembly
stages; therefore, there is no production module to change.

Next step: after reviewing this report, select the first remaining OCR case with
a reproduced failure — prioritizing tables 27–40 or orientation/figures 42–50 —
and run the stage-by-stage diagnosis. Only then implement, at most, one targeted
regional OCR fix. No change to native text, assembly, reading order, ledger, or
deduplication is authorized by this plan.

## First Remaining Defect: Page 29

Page 28 was approved with `baseline`. Page 29, the next variant, reproduced a
recognition failure:

| Policy | Observed result | Stage evidence |
|---|---|---|
| `baseline` | `Fictício` came out as `Ficticio`; `C` came out as `c`; the title came out as `—TABELA` | page OCR requested; 17 tokens detected; quality `1.0`; selected variant `baseline` |
| `adaptive` | corrected `C` and the title space, but kept `Ficticio` | tried `baseline`, `sharpness`, and `unsharp`; selected `sharpness`; 16 cells preserved |

The `inspect` of the baseline run confirmed: region `page-1:region-1` classified
as `escalate_page_ocr`, `page_ocr_requested=true`, one OCR pass, valid
`text_tracks` table, 16 cells, token coverage `1.0`, association conflicts `0`,
and no substitution/duplication in the merge. Therefore, the failure arises in
pixel recognition, before assembly. The adaptive `inspect` also confirmed cell
coverage `1.0`; no variant produced the required accented spelling.

The baseline received sufficient quality despite the semantic error because
confidence scores and spatial coverage were high. It is not safe to fix this
with text substitution, lexicon, or rule for synthetic content. There is also no
authorization in this plan to swap model, resolution, runtime, or globally alter
the quality criterion based on a single word.

This is the stopping point of the cycle: no change was made to `src/` and no
production test was created to mask the recognizer's limitation. The complete
artifacts are in `/tmp/ocr-stress-audit/p28*`, `/tmp/ocr-stress-audit/p29*`,
and `/tmp/ocr-stress-audit/p29-inspect-*`.

## Authorized Fix: Multiline Raster Cells

With specific authorization, the next step changed only `tables/text_tracks.py`.
Detection now groups a row that vertically overlaps the previous row and occupies
a subset of the same column tracks; serialization orders groups by `y` within
each cell and joins the content with a space. Normal rows with vertical
separation continue to be distinct rows.

Fix validations:

- unit test: `test_borderless_detector_keeps_wrapped_cell_lines_in_one_row`;
- page 30 baseline before/after: from 7 physical rows/extra rows to 4 logical
  rows and 16 cells;
- final Markdown output preserves `123,45 mensal`, `67,89 estimado`,
  `90,12 revisado`, `Fictício controle`, `Controle interno`, and `V1 atenção`;
- pages 27–29 re-run without errors; pages 27 and 28 retained previous values
  and page 29 retained the already-diagnosed residual content;
- controls 1, 2, 13, 14, 19, and 24 re-run without error, duplication, or
  native text change;
- `python3 -m compileall -q src tests`, `pytest -q`, and `git diff --check`
  approved.

There was no change to `native` mode, regional OCR, model, resolution, runtime,
or ledger. The next residual risk is the recognition quality of page 29
(`Ficticio` without accent), which was not corrected by this structural change.

## Fix: Raster OCR Within Native Cells

In the subsequent audit of scenarios 31–40 and continuation 56–57, two
inconsistencies in the fixture itself were found: the images declared as raster
cells on pages 33 and 57 were drawn below the tables. The generator and manifest
were corrected to position them inside real cells, with legible text and without
changing the production parser.

With the corrected fixture, the diagnosis of page 33 showed that regional OCR
recognized `CÉLULA OCRS-033`, but the token was emitted as a separate figure.
The cause was in assembly: the table geometry already contained the cell, but
the OCR token from the image was not associated with the cell and the overlapping
native row was not claimed by the table block.

The production fix is geometric and generic:

- OCR tokens from a region substantially contained in a native cell are converted
  into cell evidence;
- the consumed token is no longer sent to supplemental text;
- native rows whose tokens compose cells are claimed by the table block, avoiding
  fallback or duplication in figure/prose;
- visual tables continue on the existing refinement path.

End-to-end results:

- page 33: `3-3 CÉLULA OCRS-033` inside the cell, no residual block;
- page 57: `3-3 CÉLULA OCRS-057` inside the cell, preserving continuation 56–57;
- `ocr_unmatched_tokens=1` before association and no supplemental text or
  fallback after association;
- controls 1, 2, 13, 14, 19, and 24 re-run without error;
- targeted tests, `pytest -q`, `compileall`, and `git diff --check` approved.

There was no swap of model, runtime, resolution, or rule conditioned on corpus
text. The residual of page 29 (`Ficticio` without accent) remains separate and
was not masked by text substitution.

## Final Orientation and Integration Audit

Pages 42–50 were reproduced with the `baseline` policy: landscape, rotated
native text, rotated image, three columns, QR/Code128, multiple images, and
objects partially or entirely outside the page all preserved the expected
content. Page 57 continued to preserve the raster cell of the table in
continuation.

In integration 51–60, pages 52 and 55 initially appeared to lose the last row
of the raster tables. Diagnosis showed that the recognized tokens matched only
the existing pixels: rows `B 53,00` and `B 56,00` had been drawn outside the
bitmap height by the generator. The fix was restricted to the fixture, increasing
the regions to `390×170` and `340×160` points; no production file was changed.
The new extraction recovered all rows and page 55 again formed a complete visual
table.

The test `test_raster_table_fixtures_expose_all_declared_rows` fixes this corpus
invariant. The real residual failure remains the accentuation of `Ficticio` on
page 29, located in pixel recognition and without a safe generic fix available
at this step.

## Full Extraction After Fixture Fix

The complete PDF was regenerated and extracted with `balanced`/`baseline`.
Execution completed with code 0; the 60 markers `OCRS-P01-CONTROL` …
`OCRS-P60-CONTROL` each appear exactly once. The full output was preserved in
`/tmp/ocr-stress-audit/full-after-fixture.md` and the log recorded only the
expected zero-visible-area warning for the figure on page 50.

Also confirmed in the full output: `B 53,00` on page 52, the visual table with
`A 55,00` and `B 56,00` on page 55, and `3-3 CÉLULA OCRS-057` on page 57. The
structural validation command, `compileall`, the targeted suite, `pytest -q`,
and `git diff --check` remain approved.
