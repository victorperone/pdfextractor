# Execution result of the correction plan

## Starting point and validation

- Branch: `fix/pdfextractor-correcoes`.
- Initial commit: `cc36103527374579ca03b4e7163b72e09644b9a0`.
- Initial working tree clean.
- Available environment: WSL2/Linux x86_64; `python3` is Python 3.10.12. The project declares Python >=3.12.
- Baseline: `python3 -m pytest -q` ran the suite and found two pre-existing failures: native `overlay` required missing OCR weights and one test still expected PP-OCRv5 as the default model.
- Result after changes: `python3 -m pytest -q` passed. Focused tests and a subprocess call from the CLI module for native overlay and OCR overlay without a profile were also run.
- Python 3.12 is available but does not have `pytest` installed; `python3.12 -m pytest -q` could not run. The environment also lacks the `pdftext` entry point installed, so CLI validation was done via `python -m structured_pdf_text.cli` on the workspace code.
- No OCR weights in the local cache: no real inference was run. The Windows metrics API was validated with a mock; no native Windows is available for platform testing.

## Items approved in this round

| Item | Result |
| --- | --- |
| 1 — `setup-models` / `models-status` | Both were already registered and appear in help. They were not duplicated. Help, unknown profile, and readiness return were superficially covered, without inference or deep weight validation. |
| 2 — `pt-v6-medium` | Added as an alias of the same `pt` profile object; both use PP-OCRv6 medium weights. Unknown profile fails without substitution. |
| 3 — PP-OCRv6 default | `pt` uses PP-OCRv6 medium detection and recognition. `pt-v5` remains explicit; there is no fallback to v5. Metadata now records the profile and model names used. |
| 4 — documentation and help | README and help were aligned to profiles, explicit OCR selection, `overlay`, resolution, timeouts, and exit codes. Published examples were reviewed against the parser. |
| 8 — pixel limit | The calculation uses integer dimensions rounded by `ceil`, same as PDFium. The requested scale is preserved when the area fits, including at the boundary; it is only reduced above the limit. Reductions are recorded in `render_limit_reductions`. |
| 10 — `overlay` | The default is native and does not validate or initialize OCR. `balanced` and `ocr` require an explicit profile; a profile with native mode is rejected. Custom cache is accepted. The other OCR run commands also distinguish a supplied profile from an omitted default. |
| 12 — timeouts | The cooperative document timeout and `timed_out` status were removed. The inventory found no other execution deadlines controlled by the project. Calls blocked by dependencies may wait indefinitely; duration and progress metrics remain. |
| 13 — state and exit code | Warnings remain in the report without automatically degrading the result. Page failure, required OCR, required regional recovery, and required table detection are marked as partial/failure. Success returns `0`; partial/operational failure returns non-zero; usage error returns `2`. Partial outputs continue to be written when possible. |
| 14 — reference | The report preserves `requested_reference`; `effective_reference` is only populated when the requested reference completed successfully and there is a comparable baseline. No other adapter is chosen as a substitute. |
| 15 — valid comparison | A comparison requires an available reference and at least two valid adapters. Failures of remaining adapters mark the comparison as partial; without a reference or without a comparable minimum, it fails. Saving diagnostic JSON does not change the state or exit code. |
| 19 — memory | Centralized collection in bytes, with source, availability, and scope. Linux/WSL uses `VmRSS`/`VmHWM`; Windows uses `GetProcessMemoryInfo`. Peak is the high-water mark of the process since start; child processes are not summed. Unavailable metrics and unavailable aggregate maximums are `null`, not zero. |

## Deferred items, no algorithm change

- Item 5: out of scope for this round.
- Items 6 and 7: deep model integrity/inference validation remains deferred; `models-status` remains a superficial check.
- Item 9: no changes to table detection activation/configuration or algorithm. The suspicion about `enable_tables=False` continues for future triage, with native/OCR/hybrid scenarios and detector call counts.
- Item 11: no changes to rotation, geometry, or table joining. Reproduce headings and continuity on 0°, 90°, 180°, and 270° pages before proposing a change.
- Items 16 and 17: no changes to merged cell grid/rendering or header heuristics. Investigate tables with `rowspan`/`colspan`, without headers, and repeated headers.
- Item 18: no changes to OCR confidence or text composition. First investigate when `content_blocks` is absent and whether the alternative fallback is reachable without duplicating OCR lines.

## Evidence run and limitations

- Full suite in the environment with `pytest`: passed.
- CLI via module: native `overlay` finished with `0` and created the PNG without models; `overlay --mode balanced` without a profile finished with `2` before opening the PDF.
- Automated test covers the blocking case of all adapters failing: the diagnostic JSON is emitted and the CLI returns `1`.
- Windows metrics were exercised by mock and Linux/WSL metrics on the current process. Real execution on native Windows and OCR execution with installed weights remain pending due to environment unavailability.
