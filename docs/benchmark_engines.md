# OCR deployment profile benchmark

The benchmark measures end-to-end extraction for four OCR families across five
deployment configurations. It is a deployment comparison: each family may use
different models, preprocessing, orientation handling, and confidence behavior.
The two RapidOCR runs use the same configured artifacts with different providers
and can be compared as a provider experiment when the manifests confirm matching
effective model and OCR parameters.

| Run configuration | OCR family | Provider |
|---|---|---|
| `easyocr` | EasyOCR | PyTorch |
| `paddle` | PaddleOCR | Paddle runtime |
| `rapidocr-onnxruntime` | RapidOCR | ONNX Runtime |
| `rapidocr-openvino` | RapidOCR | OpenVINO |
| `tesseract` | Tesseract | Tesseract executable |

The run configuration names identify benchmark rows; the public OCR engine
family remains `rapidocr` for both RapidOCR providers. RapidOCR uses PP-OCRv6
medium by default; pass `--rapidocr-model-size small` for its small profile.
The manifests record the effective size and artifact hashes. Historical V3/V4
leaderboard claims describe their recorded model configurations; PP-OCRv6 must
be benchmarked in a new namespace before those comparisons are updated.

The authorized pipeline migration is still being validated. Reduced V5 controls
show differences from legacy outputs. RapidOCR's raster boundary correction
removed the authorization error in page 21, but negative controls still contain
spurious figure text with the small profile. A local evidence admission policy
cleared the V5-21/V5-31 negative controls for medium with both providers;
the small profile still reads `O` on V5-21. These reduced controls do not
establish general quality or replace the historical leaderboard or
establish release acceptance. See the
[implementation status](OCR_Decoupling_Implementation_Status.md) for run IDs,
model/provider evidence and remaining gates, and the
[extension guide](OCR_Pipeline_Extension.md) for the current pipeline contract.

## Choose a backend

The default engine is **EasyOCR**, which achieved the lowest average CER across
the V3/V4 benchmark corpora and zero header-leakage rate. Use PaddleOCR when
financial-data precision (Currency F1, Numeric F1, Identifier Precision) is the
priority.

```python
from structured_pdf_text.config import ExtractorConfig

# Default — EasyOCR
config = ExtractorConfig(mode="balanced", language="pt-BR")

# PaddleOCR — best critical-data metrics
config = ExtractorConfig(
    mode="balanced",
    language="pt-BR",
    ocr_engine="paddle",
    paddle_model_profile="pt",
)

# RapidOCR with an explicit provider
config = ExtractorConfig(
    mode="balanced",
    language="pt-BR",
    ocr_engine="rapidocr",
    ocr_provider="openvino",
    rapidocr_model_size="medium",  # default; set "small" for the smaller profile
)
```

The CLI exposes four families. RapidOCR takes an explicit provider; Paddle
profiles apply only to Paddle.

```bash
# EasyOCR (default — no flag required)
pdftext extract documento.pdf --mode balanced --language pt-BR --output markdown
# Other engines
pdftext extract documento.pdf --mode balanced --language pt-BR --ocr-engine tesseract --output markdown
pdftext extract documento.pdf --mode balanced --language pt-BR --ocr-engine rapidocr --ocr-provider onnxruntime --output markdown
pdftext extract documento.pdf --mode balanced --language pt-BR --ocr-engine rapidocr --ocr-provider openvino --output markdown
pdftext extract documento.pdf --mode balanced --language pt-BR --ocr-engine rapidocr --ocr-provider openvino --rapidocr-model-size small --output markdown
pdftext extract documento.pdf --mode balanced --language pt-BR --ocr-engine paddle --paddle-model-profile pt --output markdown
```

## Install and prepare models

Use the platform setup scripts from a Python 3.12 environment:

```bash
scripts/setup_ocr_benchmark.sh
```

```powershell
.\scripts\setup_ocr_benchmark.ps1
```

These scripts create a separate virtualenv for each deployment profile under
`.ocr-envs`, prepare model artifacts, check dependencies/OpenCV ownership and
run static readiness. Tesseract with Portuguese traineddata must already be in
`PATH`. Existing environments are preserved; use another environment root for
a fresh installation. See [isolated installation](OCR_Installation_Profiles.md)
for single-engine installation, offline wheels and JSON installation reports.
Model downloads occur during explicit setup; extraction uses local artifacts.

PP-OCRv6 with the OpenVINO provider pins OpenVINO 2026.4.1. A real smoke with
2024.4.0 failed while loading the v6 recognizer's ONNX graph. On Linux, the
2026.4.1 runtime passed both medium and small Portuguese smokes, and page 19 of
V5 passed E2E for both sizes. Windows installation and corpus-level gates remain
separate requirements before declaring the provider supported on that platform.

After setup, `python scripts/smoke_rapidocr_profiles.py --cache-home <cache-root>`
runs real Portuguese recognition for medium and small with ONNX Runtime and
OpenVINO. Treat the profiles as homologated only after the smoke passes with
the local model hashes recorded. This session's Linux matrix passed with the
local RapidOCR v6 artifact hashes recorded in its manifests.

## Run configurations

The default runners execute five configurations (four OCR families):

```bash
scripts/run_benchmark.sh --python .venv/bin/python --environment-root .ocr-envs --run-suffix acceptance-wsl --all-pages
```

```powershell
.\scripts\run_benchmark.ps1 -PythonBin .\.venv\Scripts\python.exe -EnvironmentRoot .ocr-envs -RunSuffix acceptance-windows -AllPages
```

The corpus, reference Markdown, manifest, and validation report must be
provisioned locally before running. Artifacts are written under the configured
output directory. Each run manifest records the benchmark protocol identifier;
the comparison command rejects incompatible protocols unless an explicit
override is supplied. Reports describe deployment profiles and should not be
read as isolating OCR runtime quality when models or preprocessing differ.

## Protocol and interpretation

The protocol records shared language, extraction mode, quality policy, selected
pages, corpus/reference hashes, and benchmark protocol version. Engine-specific
effective settings, package versions, and available model hashes are written to
the run manifest. `compare_engines.py` checks that the runs share a compatible
protocol before producing a comparison.

OCR confidence values are backend outputs on a common numeric range, not
calibrated probabilities that can be compared directly across families. Quality
thresholds are operating heuristics and need empirical calibration for the
specific backend/profile and corpus.

Peak memory reporting distinguishes parent-process RSS from sampled process-tree
RSS. Process-tree values are sampled and can miss short-lived peaks; the manifest
records the sample count and interval when available.

## Acceptance layers

For structured refactoring captures, `scripts/compare_ocr_baseline.py` keeps
strict comparison as its default. Independent authorized runs mint different
request/authorization/evidence UUIDs. The explicit `--canonicalize-lineage-ids`
option compares those UUID components by consistent renaming across known
lineage fields. It preserves parent/evidence references, distinct identities,
attempt/token suffixes, native lineage, text, model hashes, geometry, settings
and Markdown checks. Each report records the normalization policy and counts.
Keep both reports; an equivalent lineage graph does not approve recognition
quality or erase structural differences from a historical capture.

The automated suite and real-backend/corpus acceptance are separate gates. Run
the core suite with both entrypoints, then static and deep backend preflight,
then run the full corpus separately on Windows and WSL. Preserve each platform's
environment report, package freeze, model hashes, run manifests, metrics, logs,
and comparison report. See [the acceptance checklist](acceptance-checklist.md).
