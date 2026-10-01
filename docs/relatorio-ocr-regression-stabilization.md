# Report — OCR regression stabilization

## Scope and identification

Validation run on 22/09/2026 on WSL, without commit, push, or merge.

| Item | Value |
|---|---|
| Working branch | `feat/ocr-regression-stabilization` |
| Reference branch | `feat/ocr-adaptive-quality` |
| Initial and reference SHA | `0461d04f905bec78192df0c4e9ad6a04a4022209` |
| Local Python | `3.10.12` (the project declares `>=3.12`; environment gap) |
| PaddlePaddle / PaddleOCR / PaddleX | `3.3.1 / 3.7.0 / 3.7.2` |
| PDFium / Pillow / pytest | `pypdfium2 5.13.0 / 10.3.0 / 9.1.1` |
| Models | `PP-LCNet_x1_0_doc_ori`, `PP-LCNet_x1_0_textline_ori`, `PP-OCRv5_server_det`, `latin_PP-OCRv5_mobile_rec` (profile `pt-v5`; since 2026-09-24 the production default is PP-OCRv6 medium, profile `pt`) |
| Model state | `Offline OCR readiness: READY` |
| Corpus | 60 pages; PDF SHA-256 `be42cdfe9ee3f161b3ed04a0eb2edbaf41b5ae1dd06755b1be8df564b6d8f15d` |

The `numpy` effectively loaded by the runtime was `1.26.3`; the dependency file
pins `2.3.5`. This difference was recorded and was not changed.
Warnings for missing `ccache` and `urllib3/chardet` compatibility also appeared;
none were converted into functional OCR warnings.

## Audited implementation

The existing policies remain as implemented:

- `baseline`: a single pass per page/region;
- `adaptive`: baseline followed only by the variants triggered by quality
  signals, with early stopping when a sufficient candidate is available;
- `exhaustive`: runs all eligible variants for diagnostics.

The configuration default is `adaptive`. The existing regional chain requests
scales `(1.0, 1.5, 2.0)` when the region is eligible; the 8 MiB RGB budget
determines which ones can be run. The 2× variant, its parameters,
transformations, criteria, and logs were not modified. No new engine, new model,
or runtime dependency was integrated.

The existing diagnostics were preserved and allowed tracking of:

- policy, selected variant, attempts, and candidate metrics;
- `REGION_SELECTED`, `OCR_SCALE_PLAN`, image and crop dimensions;
- start/end of each call and batch, duration, RSS, available memory, and
  runtime versions;
- final result, supplementary lines, unassociated tokens, and geometry warnings.

Logging was enabled only with `PDFEXTRACTOR_OCR_DEBUG_LOG` pointing to
files in `/tmp/ocr-regression-stabilization/`; no production log was changed.

## Coverage added

`tests/test_ocr_adaptive_upscale_regressions.py` was added, using only
synthetic images and bboxes. The tests verify the already-existing behavior
of the 2× variant:

1. region crop and `2×` dimensions delivered to the engine;
2. mapping of the enlarged coordinates back to the page;
3. correct intersection of a partially visible region;
4. blocking of the 2× scale only when the existing RGB budget is exceeded.

The test imposes no new selection, quality, or resolution rule and does not
load PaddleOCR.

## Incremental runs

All commands used the real corpus generator and `PYTHONPATH=src`; the
extraction CLI has no page selection, while `inspect --page` selects a single
page. Outputs, logs, and metrics were preserved in
`/tmp/ocr-regression-stabilization/`.

| Subset | Policy | Observed result | Time / peak RSS |
|---|---|---|---|
| page 2 | baseline, adaptive | marker and content recovered once per policy; identical Markdown | 23.26 s / 10.7 GiB; 1:19.23 / 10.8 GiB |
| pages 13, 14, 19, 24 | baseline, adaptive | native text preserved, raster regions recovered, decoration without text did not fabricate content; four control markers once each | 26.18 s / 4.8 GiB; 1:36.39 / 4.9 GiB |
| 15, 16, 27–30 | baseline | continuation and tables processed; control markers once and `OCRS-CONTINUA-15-16` twice | 2:00.51 / 10.7 GiB |
| 42, 46–50 | baseline | landscape, QR, bars, multiple images and partial figure processed; figure outside the page rejected geometrically | 35.30 s / 10.7 GiB |
| 52, 55–58, 60 | baseline | integration, tables, continuation, and QR/bars processed; control markers once | 50.32 s / 6.3 GiB |
| page 10 | baseline, adaptive | failure reproduced: `OCRS-P10-CONTROL` absent in both policies; adaptive recovered less content | 22.26 s / 10.7 GiB; 1:15.79 / 10.9 GiB |

In eligible scenarios, logs showed `allowed_scales=1.0,1.5,2.0` and calls
completed with `result=OK`. Page 50 produced only the expected warning
`figure_ocr_skipped: ... no_visible_area_in_page`.

## Full corpus

The full corpus was run with `balanced/baseline`, finished with exit code
0, and processed 93 OCR calls. 59 of the 60 control markers were preserved:
`OCRS-P10-CONTROL` was absent. Wall time: `10:57.50`;
peak RSS: `11,344,284 kB`. The only functional warning was for the figure
entirely outside the visible area on page 50.

Page 10 was reproduced in isolation:

- baseline recognized partial lines (`Texto raster...`, subsequent content, and
  `OCRS-SCAN-10-B`), but did not recognize the marker or the first complete
  line;
- adaptive selected an even smaller output and also did not recognize the
  marker;
- `inspect` showed `page_ocr_requested=True`, `ocr_outcome=success`,
  `ocr_selected_variant=baseline`, one accepted supplementary line, and three
  unassociated tokens; there was no crop error, crash, assembly loss, or
  geometry warning.

Diagnostic conclusion: the loss occurs in pixel detection/recognition, before
final assembly. There is no evidence to change regional selection, geometry,
2× upscaling, text assembly, or ledger. The defect remains recorded for an
authorized recognizer quality round; it was not masked with a corpus rule.

## Final checks and limitations

- The full suite was run before and after the added coverage; the final run,
  compilation, and `git diff --check` must be repeated before delivering this
  branch.
- No changes were made to `src/structured_pdf_text/ocr/recovery.py`, to 2×
  upscaling, to `native` mode, to text processing, to assembly, or to the
  Content Conservation Ledger.
- The Windows `0xC0000005` incident in `paddle\libs\phi.dll` did not reappear
  on WSL and remains independent. No Windows Server was accessed in this
  environment; therefore, no corporate documents, native Windows runtime, or
  WSL/Windows comparison was validated. This is a runtime gap, not an implicit
  Windows approval.

## Suggested files and commits

No commits were created. For review, it is suggested to split the change into:

1. `test(ocr): protect existing 2x regional recovery contract`
   - include only `tests/test_ocr_adaptive_upscale_regressions.py`;
2. `docs(ocr): record regression stabilization validation`
   - include only this report.

Do not include `/tmp` artifacts, models, generated PDFs, or corporate files.
