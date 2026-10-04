#!/usr/bin/env bash
# Install the complete OCR benchmark runtime in the project WSL environment.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
OCR_PREFIX="$REPO_ROOT/.ocr-runtime"
CONSTRAINTS="$REPO_ROOT/constraints-ocr-linux-py312.txt"
CONDA_BIN="${CONDA_BIN:-$(command -v conda || true)}"

if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "Create the project virtualenv first: python3.12 -m venv .venv" >&2
    exit 2
fi
if [[ ! -f "$CONSTRAINTS" ]]; then
    echo "OCR constraints file not found: $CONSTRAINTS" >&2
    exit 2
fi
if [[ -z "$CONDA_BIN" ]]; then
    echo "Conda is required to install a local Tesseract runtime without sudo." >&2
    exit 2
fi

if [[ ! -x "$OCR_PREFIX/bin/tesseract" ]]; then
    "$CONDA_BIN" create --prefix "$OCR_PREFIX" --override-channels -c conda-forge -y 'tesseract=5.5.3'
fi

"$PYTHON_BIN" -m pip install -c "$CONSTRAINTS" -e .
"$PYTHON_BIN" -m pip install -c "$CONSTRAINTS" \
    'paddlepaddle==3.3.1' 'paddleocr==3.7.0' 'paddlex[ocr]==3.7.2' \
    'rapidocr==3.9.2' \
    'onnxruntime==1.30.0' \
    'openvino==2024.4.0'
"$PYTHON_BIN" -m pip install \
    -c "$CONSTRAINTS" \
    'torch==2.14.0+cpu' 'torchvision==0.29.0+cpu' \
    --index-url https://download.pytorch.org/whl/cpu
"$PYTHON_BIN" -m pip install -c "$CONSTRAINTS" 'easyocr==1.7.2'
# EasyOCR pulls newer NumPy/tifffile releases; keep the compatible benchmark pins.
"$PYTHON_BIN" -m pip install -c "$CONSTRAINTS" 'numpy==2.0.2' 'tifffile<2026'

EASYOCR_MODULE_PATH="$HOME/.cache/pdfextractor/easyocr"
mkdir -p "$REPO_ROOT/.venv/bin" "$EASYOCR_MODULE_PATH"
cat > "$REPO_ROOT/.venv/bin/tesseract" <<EOF
#!/usr/bin/env bash
OCR_RUNTIME="$OCR_PREFIX"
export TESSDATA_PREFIX="\$OCR_RUNTIME/share/tessdata"
exec "\$OCR_RUNTIME/bin/tesseract" "\$@"
EOF
chmod +x "$REPO_ROOT/.venv/bin/tesseract"

"$PYTHON_BIN" - <<'PY'
import importlib.metadata as metadata
import numpy
import easyocr
import onnxruntime
import openvino
import paddleocr
import paddlex
import torch
import torchvision

for name in (
    "paddleocr", "paddlex", "rapidocr", "onnxruntime",
    "openvino", "easyocr", "torch", "torchvision",
):
    print(f"{name} {metadata.version(name)}")
print(f"numpy {numpy.__version__}")
print(f"torch device cpu={not torch.cuda.is_available()}")
PY
"$PYTHON_BIN" -m pip check

if ! "$REPO_ROOT/.venv/bin/tesseract" --list-langs 2>&1 | grep -qx 'por'; then
    echo "Tesseract Portuguese traineddata (por) is unavailable." >&2
    exit 1
fi
"$REPO_ROOT/.venv/bin/tesseract" --version | head -n 1
echo "Tesseract por traineddata: ready"

# Materialise EasyOCR weights (craft_mlt_25k.pth + latin_g2.pth) so that
# the measured benchmark run never triggers a network download.
echo "Downloading EasyOCR weights (craft + latin_g2) — this may take a few minutes..."
EASYOCR_MODULE_PATH="$EASYOCR_MODULE_PATH" \
"$PYTHON_BIN" - <<'PY'
import os, easyocr
# Instantiating Reader with download_enabled=True (default) fetches and caches
# craft_mlt_25k.pth and latin_g2.pth.  Subsequent runs find them on disk.
easyocr.Reader(["pt"], gpu=False,
               model_storage_directory=os.environ["EASYOCR_MODULE_PATH"],
               download_enabled=True)
print("EasyOCR weights ready.")
PY

# Download the official PP-OCRv4 Latin ONNX recognizer and its matching
# dictionary (Portuguese diacritics: ã ç ê õ).
echo ""
echo "Downloading RapidOCR Latin recognizer and dictionary for Portuguese..."
RAPIDOCR_MODEL_DIR="$HOME/.cache/pdfextractor/rapidocr"
mkdir -p "$RAPIDOCR_MODEL_DIR"
_RAPIDOCR_REC="$RAPIDOCR_MODEL_DIR/latin_PP-OCRv3_rec_mobile.onnx"
_RAPIDOCR_KEYS="$RAPIDOCR_MODEL_DIR/latin_dict.txt"
if [[ ! -f "$_RAPIDOCR_REC" ]]; then
    curl -fL \
        "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv4/rec/latin_PP-OCRv3_rec_mobile.onnx" \
        -o "$_RAPIDOCR_REC"
else
    echo "  latin_PP-OCRv3_rec_mobile.onnx already present — skipping."
fi
if [[ ! -f "$_RAPIDOCR_KEYS" ]]; then
    curl -fL \
        "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/paddle/PP-OCRv4/rec/latin_PP-OCRv3_rec_mobile/latin_dict.txt" \
        -o "$_RAPIDOCR_KEYS"
else
    echo "  latin_dict.txt already present — skipping."
fi
"$PYTHON_BIN" "$SCRIPT_DIR/verify_rapidocr_artifacts.py" \
    --recognizer "$_RAPIDOCR_REC" --dictionary "$_RAPIDOCR_KEYS"
export RAPIDOCR_REC_MODEL="$_RAPIDOCR_REC"
export RAPIDOCR_REC_KEYS="$_RAPIDOCR_KEYS"
echo "  RapidOCR Latin model ready: $RAPIDOCR_MODEL_DIR"

# Run static readiness for all four families and five deployment configurations.
echo ""
echo "Checking OpenCV uniqueness and OCR package imports..."
"$PYTHON_BIN" "$SCRIPT_DIR/check_ocr_install.py" --imports
echo "Running static preflight across all five OCR configurations..."
"$PYTHON_BIN" "$SCRIPT_DIR/preflight_ocr_backends.py" --language pt-BR
