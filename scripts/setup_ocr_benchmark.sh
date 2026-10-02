#!/usr/bin/env bash
# Install the complete OCR benchmark runtime in the project WSL environment.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
OCR_PREFIX="$REPO_ROOT/.ocr-runtime"
CONDA_BIN="${CONDA_BIN:-$(command -v conda || true)}"

if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "Create the project virtualenv first: python3.12 -m venv .venv" >&2
    exit 2
fi
if [[ -z "$CONDA_BIN" ]]; then
    echo "Conda is required to install a local Tesseract runtime without sudo." >&2
    exit 2
fi

if [[ ! -x "$OCR_PREFIX/bin/tesseract" ]]; then
    "$CONDA_BIN" create --prefix "$OCR_PREFIX" --override-channels -c conda-forge -y 'tesseract=5.5.3'
fi

"$PYTHON_BIN" -m pip install \
    'rapidocr-onnxruntime==1.4.4' \
    'onnxruntime==1.30.0' \
    'openvino==2024.4.0'
# rapidocr-openvino 1.4.4 pins OpenVINO <=2024.0.0, which lacks the required
# CPython 3.12 wheel here. The tested setup uses OpenVINO 2024.4.0 explicitly.
"$PYTHON_BIN" -m pip install 'rapidocr-openvino==1.4.4' --no-deps
"$PYTHON_BIN" -m pip install \
    'torch==2.14.0+cpu' 'torchvision==0.29.0+cpu' \
    --index-url https://download.pytorch.org/whl/cpu
"$PYTHON_BIN" -m pip install 'easyocr==1.7.2'
# EasyOCR pulls newer NumPy/tifffile releases; keep the compatible benchmark pins.
"$PYTHON_BIN" -m pip install 'numpy==2.0.2' 'tifffile<2026'

mkdir -p "$REPO_ROOT/.venv/bin" "$REPO_ROOT/.ocr-model-cache/easyocr"
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
    "paddleocr", "paddlex", "rapidocr-onnxruntime", "onnxruntime",
    "openvino", "rapidocr-openvino", "easyocr", "torch", "torchvision",
):
    print(f"{name} {metadata.version(name)}")
print(f"numpy {numpy.__version__}")
print(f"torch device cpu={not torch.cuda.is_available()}")
PY

if ! "$REPO_ROOT/.venv/bin/tesseract" --list-langs 2>&1 | grep -qx 'por'; then
    echo "Tesseract Portuguese traineddata (por) is unavailable." >&2
    exit 1
fi
"$REPO_ROOT/.venv/bin/tesseract" --version | head -n 1
echo "Tesseract por traineddata: ready"

# Materialise EasyOCR weights (craft_mlt_25k.pth + latin_g2.pth) so that
# the measured benchmark run never triggers a network download.
echo "Downloading EasyOCR weights (craft + latin_g2) — this may take a few minutes..."
EASYOCR_MODULE_PATH="$REPO_ROOT/.ocr-model-cache/easyocr" \
"$PYTHON_BIN" - <<'PY'
import os, easyocr
# Instantiating Reader with download_enabled=True (default) fetches and caches
# craft_mlt_25k.pth and latin_g2.pth.  Subsequent runs find them on disk.
easyocr.Reader(["pt"], gpu=False,
               model_storage_directory=os.environ["EASYOCR_MODULE_PATH"],
               download_enabled=True)
print("EasyOCR weights ready.")
PY

# Run a full preflight across all five engines and exit non-zero on any failure.
echo ""
echo "Running preflight across all five OCR backends..."
"$PYTHON_BIN" "$SCRIPT_DIR/preflight_ocr_backends.py" --language pt
