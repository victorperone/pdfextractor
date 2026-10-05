# A1 — Auditable Inventory of Native Text Evidence

This suite measures only the step preceding classification, assembly, OCR,
duplicate suppression, tables, reading order, and rendering. It maintains two
tracks:

1. `raw_pdfium`: direct calls to PDFium/pypdfium2, before the production
   adapter.
2. `native_adapter`: real objects returned by
   `PdfiumNativeEvidenceSource.extract_page(page_index)`, with 0-based index.

Therefore, `reference -> raw_pdfium` is not attributed to the adapter. A
divergence in `raw_pdfium -> native_adapter` is the only one attributable to
the adapter conversion, and must still be confirmed by the captured fields and
flags.

## Commands

Initial corpus validation:

```bash
python tests/corpus/native_text_stress/validate_native_text_stress.py \
  --root tests/corpus/native_text_stress
pytest -q tests/test_native_text_stress_fixture.py
```

One deterministically chosen page per family (20 pages):

```bash
python tests/native_text_fidelity/a1_capture.py --variants-per-family 1
```

Three deterministically spaced variants per family (60 pages):

```bash
python tests/native_text_fidelity/a1_capture.py --variants-per-family 3
```

Full corpus:

```bash
python tests/native_text_fidelity/a1_capture.py --all
```

Artifacts are stored in `output/native_text_fidelity/a1/<codigo_sha>/`, which is
ignored by Git:

- `run_metadata.json`: branch, code SHA, actual date, hashes, versions, selected
  pages, options, and policy;
- `raw_pdfium_chars.jsonl`: one line per PDFium character index, with text,
  Unicode, original bbox and converted bbox, origin, angle, and errors;
- `raw_pdfium_pages.jsonl`: geometry, rotation, count, and aggregated
  `get_text_range()` for inspection, without treating it as an independent
  source;
- `native_adapter_chars.jsonl` and `native_adapter_pages.jsonl`: adapter
  evidence without removing invalid indices, bboxes, IDs, or flags;
- `unit_alignment.jsonl`: per-occurrence alignment, one-to-one cardinality,
  candidates, consumed characters, category, and reasons;
- `page_summary.json`: per-page/stage counts and strict and permissive
  numerators/denominators;
- `A1_report.md`: reproducible examples and limits.

## Comparison Decisions

The `exact_text` is compared literally first. A second, diagnostics-only view
converts Unicode whitespace to an ASCII space and uses NFKC to identify possible
ligatures/substitutions; numbers, IDs, and punctuation are not normalized. Two
identical occurrences remain two occurrences and a consumed character cannot be
reused.

Reference bboxes are font-metric estimates. Association uses proximity/overlap
with tolerance proportional to font size, without requiring bbox equality.
Rotated text is evaluated in the canonical top-origin frame; drawing order and
`logical_reading_order` do not participate in the A1 verdict.

`not_assessable` is excluded from the denominator. `unmatched_native` is kept
as evidence of characters with no matching unit and does not count as a success.
Changed whitespace is `spacing_changed`, not an automatic non-space character
loss. The reference is never used as parser output or as a capture source.

## Native Fields and Geometry

The `NativeCharacter` exposes: `page_index`, `char_index`, `text`,
`unicode_codepoint`, `bbox`, `origin`, `angle`, `font_name`, `font_size`,
`font_weight`, `fill_color`, `stroke_color`, `text_render_mode`,
`marked_content_id`, `generated`, `hyphen`, `unicode_mapping_failed`, and
`visible_candidate`. `NativePageEvidence` exposes `page_index`, `bbox`,
`characters`, `objects`, `extracted_text`, and `capabilities`.

The adapter applies `BBox.from_pdfium_rect`: it subtracts the x/y origin of the
effective frame, inverts y within the effective height, and returns the bbox in
top-origin. The raw capture repeats only this mathematical conversion to also
record the original PDFium values; it does not call the adapter's private
helpers.

## Limits

The benchmark measures native text evidence; it does not guarantee that
generator intent represents real ink, nor does it approve 100% of text/tables.
`get_text_range` and `get_text_bounded` are views of the same library and are
not treated as independent sources. OCR, rasterization, assembler, conservation,
logical order, and table structure are left for later steps.

## A2 — IR Conservation Audit

The first A2 increment is in `a2_conservation.py`. It is an independent
evaluator and does not alter blocks: it checks ownership exactly once, explicit
suppressions, transformed sources (`DEDUPLICATED`), orphan claims, unaccounted
content, and non-assessable blank lines. Tests in `test_native_text_a2.py` cover
these events without depending on the PDF corpus. The production ledger remains
the mechanism that decides/reconstructs blocks; this module verifies that its
output is auditable. `audit_document_conservation` aggregates already-assembled
pages and consumes transformed-source facts exposed in `PageDiagnostics`,
enabling a documentary check without re-running assembler decisions.

To run the audit on the extractor's assembled output, without OCR, layout, or
tables:

```bash
python tests/native_text_fidelity/a2_evaluate.py \
  tests/corpus/native_text_stress/Document_Text_Stress_V1.pdf \
  --output /tmp/pdfextractor-a2
```

The command produces `a2_metadata.json`, `a2_summary.json`, `a2_findings.jsonl`,
and `a2_report.md` outside Git. The JSON is the source for automation; the
Markdown summarizes the gate and categories for human review.

## B1 — Structural Audit

The B1 auditor compares units per occurrence, checks whether reference regions
were fragmented, evaluates the monotonicity of logical order, and verifies cell
shape (`row`, `col`, `rowspan`, `colspan`) without conflating `source_draw_order`
with reading order. Repeated headers and footers are excluded from the content
order comparison, as they are explicit page furniture. To run on the corpus:

```bash
python tests/native_text_fidelity/b1_evaluate.py \
  tests/corpus/native_text_stress/Document_Text_Stress_V1.pdf \
  tests/corpus/native_text_stress/Document_Text_Stress_V1.reference.json \
  --output /tmp/pdfextractor-b1
```

The result produces `b1_metadata.json`, `b1_summary.json`, `b1_findings.jsonl`,
and `b1_report.md`. The report separates text present but outside the geometry,
unresolved textual absence, and non-blocking semantic partitions. `table_missing`
and `table_cell_mismatch` are structural findings; they are not converted into
A1 text loss.

When a reference region is covered by observed regions of different semantic
types (for example, `title` and `text`), the result is recorded as
`region_partitioned`. The partition remains visible in the audit and preserves
the observed IDs and types, but is not confused with a structural split of
regions of the same type, which remains `region_fragmented` and blocks the B1
gate. In region assembly, strongly nested boxes of the same type and semantic
role are coalesced; adjacent boxes or those with different semantic roles remain
independent.

## C1 — Final Rendering Audit

`c1_render_audit.py` verifies both renderings without changing the production
pipeline. The JSON output must be structurally equal to
`StructuredDocument.to_dict()`. The Markdown output is checked per page section
and by renderable fragments of non-suppressed blocks; the auditor does not
re-implement layout decisions.

```bash
python tests/native_text_fidelity/c1_render_audit.py \
  tests/corpus/native_text_stress/Document_Text_Stress_V1.pdf \
  --output /tmp/pdfextractor-c1
```

The command produces `c1_metadata.json`, `c1_summary.json`, `c1_findings.jsonl`,
`c1_rendered.json`, `c1_rendered.md`, and `c1_report.md` outside Git. In the
global order, `edge_*`, `rotation_*`, `vertical_*` units and table cell/header
units are not compared as running lines: borders and labels have their own
geometric flow, while tables are evaluated by cell shape and content association.

When the exact text exists but the chosen occurrence is far from the reference
bbox, the auditor records `unit_geometry_mismatch`; this is not counted as
`unit_missing`, but blocks the structural gate. Association continues by
occurrence and proximity, without accepting the reference as extractor output.

When a unit does not exist as an isolated line, the auditor attempts a
conservative reconstruction from native tokens within the reference bbox. This
covers punctuation emitted on a separate line, line breaks, and table cells that
share a line with another field. The result is recorded as `unit_fragmented`,
preserving the observed text and without relaxing content equality. When two
fields share the same line, consumption is controlled by token index; one field
cannot re-consume tokens already associated with another.

Exclusively compatible ligature substitutions (`ﬁ`, `ﬂ`, `ﬀ`) are recorded as
`unit_unicode_substitution`. This category preserves the observed difference and
does not treat the unit as exact text; normalization is not applied to
identifiers, numbers, or punctuation.

The native table detector accepts both thin segments and repeated rectangular
paths representing cell outlines. In borderless tables, native rows with the
same geometric baseline are grouped before track inference. Continued table
fragments have their row indices normalized only to the relative page shape; this
does not alter the evidence or production assembly.

In horizontal native reconstruction, the line is anchored by the bottom edge of
the glyph boxes. Thus, ascenders, accents, and descenders remain on the same
visual line without losing geometric separation between columns. In order
checking, a unit reconstructed from a line that mixes columns uses the boxes of
the consumed tokens, not the wide box of the native line; this avoids assigning
to the wrong column an occurrence that was correctly associated by token
geometry.

In the B1 audit, when the line has native tokens, the token text is the compared
source; the line's `text_override` acts as a fallback only when there are no
tokens. This keeps visible a divergence between line grouping and native text
evidence.

When grouping preserves a compatible ligature form but native tokens collapse
overlapping glyphs, the case is recorded as `unit_tokenization_variant`; it is
not counted as exact text nor as PDFium loss.
