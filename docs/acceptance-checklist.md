# OCR release and acceptance checklist

Run this checklist for each release candidate. Preserve the logs and manifests
with the build artifacts. Windows native and WSL results are separate runs.

## Automated repository gates

- [ ] Start from a clean Python 3.12 environment using the platform setup guide.
- [ ] `python -m pip check` reports no broken requirements.
- [ ] `python scripts/check_ocr_install.py --imports` succeeds and reports exactly
  one installed OpenCV distribution.
- [ ] `pytest -q` and `python -m pytest -q` collect the same tests and pass with
  zero unexpected skips.
- [ ] `python -m compileall -q src scripts tests` succeeds.
- [ ] `python scripts/preflight_ocr_backends.py --language pt-BR` reports static
  readiness for PaddleOCR, RapidOCR/ONNX Runtime, RapidOCR/OpenVINO, EasyOCR,
  and Tesseract.
- [ ] `python scripts/preflight_ocr_backends.py --language pt-BR --deep-smoke`
  runs local inference for all five configurations without downloading models.
- [ ] Confirm test coverage for configuration errors, recovery budget blocks,
  worker timeout/crash behavior, atomic artifacts, region coordinate transforms,
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
- [ ] Review missing pages, runtime errors, timeouts, budget blocks, fallbacks,
  model downloads, partial pages, warnings, NaN metrics, and output truncation.
- [ ] Review reading order, merged/header tables, chart/flowchart false tables,
  redaction, rotations, CropBox offsets, and critical pt-BR data.
- [ ] Run an endurance workload of approximately 1,000 pages or equivalent;
  inspect parent/process-tree RSS, handles/file descriptors, child workers, and
  growth by page window.

## Manual platform acceptance

- [ ] Clean install, empty OCR cache, and static/deep readiness in Windows native.
- [ ] Clean install, empty OCR cache, and static/deep readiness in WSL.
- [ ] Full corpus runs completed independently in Windows native and WSL.
- [ ] Compare each platform's artifacts independently and document reviewed
  partial results and known limitations.

Do not mark a release accepted from a successful process exit alone. Attach the
artifacts above and explain any unchecked gate.
