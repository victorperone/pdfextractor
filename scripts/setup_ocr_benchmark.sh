#!/usr/bin/env bash
# Provision one virtualenv per deployment profile; never combine cv2 wheels.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$REPO_ROOT/.venv/bin/python}"
OCR_PREFIX="$REPO_ROOT/.ocr-runtime"

if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "Create the Python 3.12 project virtualenv first, or set PYTHON_BIN." >&2
    exit 2
fi
# Keep the existing local Tesseract installation discoverable by every profile.
# Tesseract installation is explicit and separate from Python dependency setup.
if [[ -x "$OCR_PREFIX/bin/tesseract" ]]; then
    export PATH="$OCR_PREFIX/bin:$PATH"
    export TESSDATA_PREFIX="$OCR_PREFIX/share/tessdata"
fi
if ! command -v tesseract >/dev/null 2>&1; then
    echo "Install Tesseract 5.x + Portuguese traineddata, then re-run setup." >&2
    echo "For example: conda create --prefix .ocr-runtime -c conda-forge tesseract=5.5.3" >&2
    exit 2
fi

exec "$PYTHON_BIN" "$SCRIPT_DIR/setup_ocr_environments.py" \
    --configuration tesseract rapidocr-onnxruntime rapidocr-openvino easyocr paddle \
    --provision-models "$@"
