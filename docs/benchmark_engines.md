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
family remains `rapidocr` for both RapidOCR providers.

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

These scripts install the supported OCR stack, prepare local EasyOCR and
RapidOCR artifacts, check package consistency/OpenCV uniqueness, and run static
readiness. The setup currently downloads models; benchmark execution itself is
expected to use the prepared local artifacts. The setup scripts and constraints
files are the source of exact package pins. Run `python scripts/check_ocr_install.py
--imports` and `python scripts/preflight_ocr_backends.py --language pt-BR` to
inspect an existing environment.

## Run configurations

The default runners execute five configurations (four OCR families):

```bash
scripts/run_benchmark.sh --run-suffix acceptance-wsl --all-pages
```

```powershell
.\scripts\run_benchmark.ps1 -RunSuffix acceptance-windows -AllPages
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

The automated suite and real-backend/corpus acceptance are separate gates. Run
the core suite with both entrypoints, then static and deep backend preflight,
then run the full corpus separately on Windows and WSL. Preserve each platform's
environment report, package freeze, model hashes, run manifests, metrics, logs,
and comparison report. See [the acceptance checklist](acceptance-checklist.md).
