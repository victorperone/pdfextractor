# OCR release and acceptance checklist

Run this checklist for each release candidate. Preserve the logs and manifests
with the build artifacts. Windows native and WSL results are separate runs.

The engine decoupling implementation and its observed results are tracked in
[the implementation report](OCR_Decoupling_Implementation_Status.md). The
[architecture decision](adr/0001-authorized-ocr-pipelines.md) and
[extension guide](OCR_Pipeline_Extension.md) describe the current boundaries.
This checklist remains a per-release gate; code completion does not check it
automatically.

## Automated repository gates

- [ ] Create a fresh Python 3.12 environment for each deployment configuration
  using the [isolated installation guide](OCR_Installation_Profiles.md).
- [ ] `python -m pip check` reports no broken requirements in each environment.
- [ ] `python scripts/check_ocr_dependencies.py` accepts the full production
  tree, including core consumers outside `ocr/`; the fifth-engine CLI fixture
  passes without edits to the core or existing pipelines.
- [ ] Each environment's Python runs `scripts/check_ocr_install.py
  --configuration <profile> --imports` successfully and reports exactly the
  expected OpenCV distribution. Preserve JSON reports and resolved wheel hashes.
- [ ] `pytest -q` and `python -m pytest -q` collect the same tests and pass with
  zero unexpected skips.
- [ ] `python -m compileall -q src scripts tests` succeeds.
- [ ] Run `scripts/preflight_ocr_backends.py --language pt-BR --configuration
  <profile> --cache-home <engine-cache>` with each environment's Python; all
  five configurations report static readiness.
- [ ] Repeat the per-profile preflight with `--deep-smoke`; all five
  configurations run local inference without downloading models.
- [ ] Confirm test coverage for configuration errors, recovery budget blocks,
  slow-worker cancellation/crash behavior, atomic artifacts, region coordinate transforms,
  rotations, confidence policy, and backend `close()` lifecycle.

## Environment record (save once per platform)

- [ ] OS/build, WSL version/kernel when applicable, CPU, RAM, Python and pip.
- [ ] Git SHA, `git status --porcelain`, `pip freeze`, and `pip check` output.
- [ ] Tesseract version/languages and OpenCV distribution.
- [ ] OCR package/runtime versions and hashes of the exact model artifacts.
- [ ] Confirm all cache/model paths resolve after opening a fresh shell.

## Corpus and benchmark gates

- [ ] Validate corpus, reference, page count, manifest, and SHA-256 before OCR.
  Run `python scripts/provision_acceptance_corpus.py` to verify the locked local
  Stress V4 artifacts before using the benchmark runner.
- [ ] Run the entire corpus in each of five configurations, with unique run IDs.
- [ ] Preserve Markdown, run manifest, metrics, error report, comparison report,
  stdout/stderr, page statuses, warnings, recovery reasons, and memory samples.
- [ ] Confirm protocol IDs match before comparison; record explicit override if
  intentionally comparing incompatible protocols.
- [ ] Review missing pages, runtime errors, explicit cancellations, budget blocks, fallbacks,
  model downloads, partial pages, warnings, NaN metrics, and output truncation.
- [ ] Review reading order, merged/header tables, chart/flowchart false tables,
  redaction, rotations, CropBox offsets, and critical pt-BR data.
- [ ] Compare engine migration with its unchanged-model baseline separately
  from RapidOCR v6 model/provider experiments; review rejected out-of-scope
  detections and all content/geometry differences without weakening conservation.
- [ ] Preserve the strict report when independently minted UUIDs differ. If
  using `--canonicalize-lineage-ids`, preserve the additional report and its
  policy/counts; review changed parent/evidence relationships, token suffixes,
  critical text, hashes and settings without treating renaming as quality acceptance.
- [ ] Exercise critical fields, dedicated figures and visual tables through
  typed integration, including signs, ownership, source lineage, rejected
  selections, stale targets, atomicity and idempotent replay.
- [ ] Run an endurance workload of approximately 1,000 pages or equivalent;
  inspect parent/process-tree RSS, handles/file descriptors, child workers, and
  growth by page window.

Use the finite endurance runner after provisioning local models, for example:

```bash
python scripts/run_ocr_endurance.py corpus/V5/Document_AI_V5.pdf \
  --engine rapidocr --provider onnxruntime --model-size medium \
  --cache-home .ocr-model-cache --pages 19,21,24,31 \
  --repeat 250 --warmup 1 --output-dir outputs/endurance-v5-rapidocr-unique
```

This example measures 1,000 selected pages and excludes the four warmup pages.
The same extractor/runtime is reused across extractions. Use a new directory
for every engine/provider/platform; existing artifacts are never overwritten.
The runner requires `psutil` for complete process-tree RSS and FD/handle
measurements. `samples.jsonl` records page boundaries and iteration/close
snapshots; `report.json` records operational checks and effective identities.
It rejects a package already imported from a different checkout before
reserving artifacts, so source hashes cannot silently describe the wrong code.
The initial Markdown and structured JSON support separate quality review.
Inspect resource growth after warmup; process exit and repeatable content alone
do not approve memory stability, corpus accuracy or a release. A short or
simple synthetic workload must be identified as such in acceptance evidence.

## Manual platform acceptance

- [ ] Clean install, empty OCR cache, and static/deep readiness in Windows native.
- [ ] Clean install, empty OCR cache, and static/deep readiness in WSL.
- [ ] Full corpus runs completed independently in Windows native and WSL.
- [ ] Compare each platform's artifacts independently and document reviewed
  partial results and known limitations.

Do not mark a release accepted from a successful process exit alone. Attach the
artifacts above and explain any unchecked gate.
