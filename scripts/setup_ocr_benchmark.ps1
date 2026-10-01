# setup_ocr_benchmark.ps1
# Reproducible OCR benchmark environment setup (Windows PowerShell)
#
# Tested on: Windows Server 2025, Python 3.12.10, AMD64
# Tesseract: v5.5.3 with tessdata por
#
# Uso:
#   cd C:\Users\...\workspace\pdfextractor
#   .\.venv\Scripts\Activate.ps1
#   .\scripts\setup_ocr_benchmark.ps1

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "=== OCR Benchmark Environment Setup ===" -ForegroundColor Cyan
Write-Host "Python: $(python --version)"
Write-Host ""

# --- 1. Paddle (engine principal / baseline) ---
Write-Host "[1/4] PaddleOCR + PaddleX..." -ForegroundColor Yellow
pip install "paddlepaddle==3.3.1" "paddleocr==3.7.0" "paddlex[ocr]==3.7.2" "psutil>=5.9"

# --- 2. RapidOCR ONNX Runtime ---
Write-Host ""
Write-Host "[2/4] RapidOCR + ONNX Runtime..." -ForegroundColor Yellow
pip install "rapidocr-onnxruntime==1.4.4" "onnxruntime==1.30.0"

# --- 3. RapidOCR OpenVINO (two-step install — conflicting constraint) ---
Write-Host ""
Write-Host "[3/4] RapidOCR + OpenVINO 2024.4.0..." -ForegroundColor Yellow
# openvino requires numpy<2.1.0; install first to resolve conflict
pip install "openvino==2024.4.0"
# rapidocr-openvino declares openvino<=2024.0.0 (no cp312 win64 wheel)
# use --no-deps to keep openvino==2024.4.0 which supports Python 3.12
pip install "rapidocr-openvino==1.4.4" --no-deps

# --- 4. EasyOCR + PyTorch CPU ---
Write-Host ""
Write-Host "[4/5] PyTorch CPU + EasyOCR..." -ForegroundColor Yellow
pip install torch==2.14.0+cpu torchvision==0.29.0+cpu --index-url https://download.pytorch.org/whl/cpu
pip install "easyocr==1.7.2"

# --- 5. Pin numpy (conflict: easyocr upgrades to 2.5.x; openvino requires <2.1.0) ---
Write-Host ""
Write-Host "[5/5] Pinning numpy==2.0.2 (compatible with openvino + easyocr + paddlex)..." -ForegroundColor Yellow
pip install "numpy==2.0.2"

# --- Final verification ---
Write-Host ""
Write-Host "=== Verifying installations ===" -ForegroundColor Cyan

$checks = @(
    @("PaddlePaddle",           "import paddle; print(paddle.__version__)"),
    @("PaddleOCR",              "from importlib.metadata import version; print(version('paddleocr'))"),
    @("PaddleX",                "from importlib.metadata import version; print(version('paddlex'))"),
    @("RapidOCR ONNX",         "from importlib.metadata import version; print(version('rapidocr-onnxruntime'))"),
    @("ONNX Runtime",           "from importlib.metadata import version; print(version('onnxruntime'))"),
    @("RapidOCR OpenVINO",     "from importlib.metadata import version; print(version('rapidocr-openvino'))"),
    @("OpenVINO",               "from importlib.metadata import version; print(version('openvino'))"),
    @("NumPy",                  "import numpy; print(numpy.__version__)")
)

foreach ($check in $checks) {
    $name = $check[0]
    $cmd  = $check[1]
    try {
        $ver = python -c $cmd 2>$null
        Write-Host "  OK  $name $ver" -ForegroundColor Green
    } catch {
        Write-Host "  FAIL $name" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "Checking Tesseract (system executable)..." -ForegroundColor Yellow
try {
    $tessVer = (tesseract --version 2>&1 | Select-String "tesseract").ToString().Trim()
    Write-Host "  OK  $tessVer" -ForegroundColor Green
    $tessLangs = tesseract --list-langs 2>&1 | Out-String
    if ($tessLangs -match "por") {
        Write-Host "  OK  tessdata: por (Portuguese) available" -ForegroundColor Green
    } else {
        Write-Host "  FAIL tessdata 'por' not found — download from:" -ForegroundColor Red
        Write-Host "       https://github.com/tesseract-ocr/tessdata_best/raw/main/por.traineddata" -ForegroundColor Red
        Write-Host "       Copy to: C:\Program Files\Tesseract-OCR\tessdata\" -ForegroundColor Red
    }
} catch {
    Write-Host "  FAIL Tesseract not found in PATH" -ForegroundColor Red
    Write-Host "       Install from: https://github.com/UB-Mannheim/tesseract/wiki" -ForegroundColor Red
}

Write-Host ""
Write-Host "=== Setup complete ===" -ForegroundColor Cyan
Write-Host "Run the E2E benchmark with:"
Write-Host "  # Smoke test — 5 pages, all engines"
Write-Host "  .\scripts\run_benchmark.ps1"
Write-Host ""
Write-Host "  # Full document"
Write-Host "  .\scripts\run_benchmark.ps1 -RunSuffix v2 -AllPages"
Write-Host ""
Write-Host "  # Single engine"
Write-Host "  .\scripts\run_benchmark.ps1 -Engine tesseract -AllPages -RunSuffix v2"
Write-Host "  .\scripts\run_benchmark.ps1 -Engine rapidocr-onnx -AllPages -RunSuffix v2"
