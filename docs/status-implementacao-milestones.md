# Implementation status against the MVP plan

Audit date: 2026-09-13  
Reference document: `plano-mvp-extrator-estruturado-texto-pdf.md`

This matrix compares executable functionality, not just the existence of
files or interfaces. `Partial` means that a functional slice exists, but at
least one item explicitly planned in the milestone is not yet covered in
general.

| Milestone | Status | Implemented | Verified gaps |
|---|---|---|---|
| M0 — baseline and corpus | Implemented | CLI, local corpus, runner, timings, operational report, and `structured-*`, `pdfium-raw`, and optional PyMuPDF adapters | categorization and gold cases grow together with the corporate corpus |
| M1 — native evidence | Implemented | coordinates, index/Unicode, font/size/weight/angle, colors, render mode, experimental flags, origin, images, paths, matrices/MCIDs, annotations/appearance streams, structure tree, capabilities, and JSON dump | expand attributes only when new stable PDFium APIs justify it |
| M2 — native reconstruction | Implemented | conservative normalization, deduplication, orientation, lines, spaces, punctuation/baseline glyphs, words, native order, and overlay | future quality refinements do not block the milestone |
| M3 — complexity analyzer | Implemented | text/visual coverage, dominant image, bad Unicode, duplication, visible ink, vectors, annotations, tables/columns, and decision report | signal weights remain heuristic and should be calibrated on corporate corpus |
| M4 — layout regions | Implemented | replaceable protocol, heuristic engine, low-res render, normalization, line assignment, and overlay | a learned model can be plugged in later, without domain change |
| M5 — reading order | Implemented | weighted region graph, full-width bands, native order consistency, columns only in prose, rotated groups, and basic semantics | calibrate weights with new corporate layouts |
| M6 — OCR and scans | Implemented | local PaddleOCR, Portuguese, render, tokens, coordinates, confidence, rotations, and variants | alternative models remain optional via the interface |
| M7 — selective OCR and fusion | Implemented | `RegionQuality`, local decision after layout, crop OCR, promotion by area/count, page OCR, alignment, deduplication, conflicts, provenance, and generic regional refiner | calibrate thresholds with the corporate corpus without changing the cascade |
| M8 — deterministic tables | Implemented | paths, strict grid, relaxed grid, cells, confidence, borderless text tracks, and `ProseVsTableClassifier` with prose and code rejection | calibrate confidence and complex spans with new corporate tables |
| M9 — visual fallback | Implemented | engine interface, OpenCV backend, visual structure, crop OCR, cell/span mapping, and rejection of charts/diagrams without text support | a learned visual model can replace the backend without changing the pipeline |
| M10 — cross-page tables | Implemented | signature, real border geometry, headers, X tracks, width, cell types, intervening titles, markers, fragments, merge, and auditable positive/negative decisions | calibrate weights for corporate sequences with more than two fragments |
| M11 — assembly/renderers | Implemented | structured document, raw/reading text, JSON, Markdown, header/footer policy, and page limits | additional renderings are post-MVP evolution |
| M12 — performance/hardening | Implemented | timings per stage/strategy, p50/p95/p99, RSS/peak, opt-in native evidence, targeted per-page diagnostics, FFI acquisitions/estimate, timeout, limits, cache, reuse, batching, and per-document multiprocessing | Rust/PyO3 and isolation remain future options conditioned on measurements, as defined in the plan |

## Gap closure order

1. ~~Generic per-region OCR refiner and integration with tables/figures.~~
2. ~~Comparative adapters from M0.~~
3. ~~Reading order graph and native consistency from M5.~~
4. ~~Automatic per-region decision from M7.~~
5. ~~Borderless text tracks from M8.~~
6. ~~Additional explainability from M10.~~
7. ~~Remaining metrics from M12.~~
8. ~~Additional PDFium evidence from M1 offered by the current binding.~~

None of the items above requires contradicting the decisions in the original
document. The adaptive quality stage added `OcrQualityPolicy` (`baseline`,
`adaptive`, and `exhaustive`), character-weighted evaluation, visual profile,
spatial consensus, native typography in `TextToken`, structured lists,
form/figure-caption ordering, and conservative classification of decorative
content. The diagnostics for these decisions are in `page.diagnostics.facts`.
The validation corpus referenced in the plan is not in this checkout; the
validation performed here is synthetic and based on the project's tests.
Backends remain replaceable and reliable native text continues to be the
primary evidence.
