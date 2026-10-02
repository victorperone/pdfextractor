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

# ---------------------------------------------------------------------------
# Helper: run a native executable and abort if it returns non-zero.
# $ErrorActionPreference = "Stop" does NOT throw on native exit codes in
# PowerShell 5.1, so every mandatory native call must use this wrapper.
# ---------------------------------------------------------------------------
function Invoke-Native {
    param(
        [Parameter(Mandatory=$true)][string]$Command,
        [Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments
    )
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "'$Command $($Arguments -join ' ')' failed with exit code $LASTEXITCODE"
    }
}

Write-Host ""
Write-Host "=== OCR Benchmark Environment Setup ===" -ForegroundColor Cyan
Write-Host "Python: $(python --version)"
Write-Host ""

# --- P1-07: install the package itself first so pdftext CLI is available ---
Write-Host "[0] Installing pdfextractor package (editable)..." -ForegroundColor Yellow
Invoke-Native python -m pip install -e .

# --- 1. Paddle (engine principal / baseline) ---
Write-Host ""
Write-Host "[1/5] PaddleOCR + PaddleX..." -ForegroundColor Yellow
Invoke-Native python -m pip install "paddlepaddle==3.3.1" "paddleocr==3.7.0" "paddlex[ocr]==3.7.2" "psutil>=5.9"

# --- 2. RapidOCR ONNX Runtime ---
Write-Host ""
Write-Host "[2/5] RapidOCR + ONNX Runtime..." -ForegroundColor Yellow
Invoke-Native python -m pip install "rapidocr-onnxruntime==1.4.4" "onnxruntime==1.30.0"

# --- 3. RapidOCR OpenVINO (two-step install) ---
Write-Host ""
Write-Host "[3/5] RapidOCR + OpenVINO 2024.4.0..." -ForegroundColor Yellow
# openvino requires numpy<2.1.0; install first to resolve conflict
Invoke-Native python -m pip install "openvino==2024.4.0"
# rapidocr-openvino declares openvino<=2024.0.0 (no cp312 win64 wheel)
# use --no-deps to keep openvino==2024.4.0 which supports Python 3.12
Invoke-Native python -m pip install "rapidocr-openvino==1.4.4" --no-deps

# --- 4. EasyOCR + PyTorch CPU ---
Write-Host ""
Write-Host "[4/5] PyTorch CPU + EasyOCR..." -ForegroundColor Yellow
Invoke-Native python -m pip install torch==2.14.0+cpu torchvision==0.29.0+cpu --index-url https://download.pytorch.org/whl/cpu
Invoke-Native python -m pip install "easyocr==1.7.2"

# --- 5. Pin numpy (conflict: easyocr upgrades to 2.5.x; openvino requires <2.1.0) ---
Write-Host ""
Write-Host "[5/5] Pinning numpy==2.0.2 (compatible with openvino + easyocr + paddlex)..." -ForegroundColor Yellow
Invoke-Native python -m pip install "numpy==2.0.2"

# --- 6. PaddleOCR model weights ---
# Models are stored in ~/.cache/pdfextractor/paddlex/official_models/
# Required: PP-LCNet_x1_0_doc_ori, PP-LCNet_x1_0_textline_ori,
#           PP-OCRv6_medium_det, PP-OCRv6_medium_rec, UVDoc
Write-Host ""
Write-Host "[6] PaddleOCR model weights (may download several hundred MB on first run)..." -ForegroundColor Yellow
pdftext setup-models --ocr-model-profile pt
if ($LASTEXITCODE -ne 0) {
    Write-Host "  WARN setup-models may have failed - check connectivity and re-run if needed." -ForegroundColor Yellow
}

# --- Final verification ---
Write-Host ""
Write-Host "=== Verifying installations ===" -ForegroundColor Cyan

$checks = @(
    @("PaddlePaddle",       "import paddle; print(paddle.__version__)"),
    @("PaddleOCR",          "from importlib.metadata import version; print(version('paddleocr'))"),
    @("PaddleX",            "from importlib.metadata import version; print(version('paddlex'))"),
    @("RapidOCR ONNX",     "from importlib.metadata import version; print(version('rapidocr-onnxruntime'))"),
    @("ONNX Runtime",       "from importlib.metadata import version; print(version('onnxruntime'))"),
    @("RapidOCR OpenVINO", "from importlib.metadata import version; print(version('rapidocr-openvino'))"),
    @("OpenVINO",           "from importlib.metadata import version; print(version('openvino'))"),
    @("EasyOCR",            "from importlib.metadata import version; print(version('easyocr'))"),
    @("PyTorch",            "import torch; print(torch.__version__)"),
    @("Torchvision",        "import torchvision; print(torchvision.__version__)"),
    @("NumPy",              "import numpy; print(numpy.__version__)")
)

$verifyFailed = $false
foreach ($check in $checks) {
    $name = $check[0]
    $cmd  = $check[1]
    try {
        $ver = python -c $cmd 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  OK  $name $ver" -ForegroundColor Green
        } else {
            Write-Host "  FAIL $name (exit $LASTEXITCODE)" -ForegroundColor Red
            $verifyFailed = $true
        }
    } catch {
        Write-Host "  FAIL $name" -ForegroundColor Red
        $verifyFailed = $true
    }
}

Write-Host ""
Write-Host "Checking Tesseract (system executable)..." -ForegroundColor Yellow
$tessExe = Get-Command tesseract -ErrorAction SilentlyContinue
if ($null -eq $tessExe) {
    Write-Host "  FAIL Tesseract not found in PATH" -ForegroundColor Red
    Write-Host "       Install from: https://github.com/UB-Mannheim/tesseract/wiki" -ForegroundColor Red
    $verifyFailed = $true
} else {
    $tessVersion = tesseract --version 2>&1 | Out-String
    $tessFirstLine = ($tessVersion -split "`n")[0].Trim()
    Write-Host "  OK  $tessFirstLine" -ForegroundColor Green
    $tessLangs = tesseract --list-langs 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0 -and $tessLangs -match "por") {
        Write-Host "  OK  tessdata: por (Portuguese) available" -ForegroundColor Green
    } else {
        Write-Host "  FAIL tessdata 'por' not found - download from:" -ForegroundColor Red
        Write-Host "       https://github.com/tesseract-ocr/tessdata_best/raw/main/por.traineddata" -ForegroundColor Red
        Write-Host "       Copy to: C:\Program Files\Tesseract-OCR\tessdata\" -ForegroundColor Red
        $verifyFailed = $true
    }
}

# Materialise EasyOCR weights (craft_mlt_25k.pth + latin_g2.pth) so that
# the measured benchmark run never triggers a network download.
Write-Host ""
Write-Host "Downloading EasyOCR weights (craft + latin_g2) - this may take a few minutes..." -ForegroundColor Yellow
$tmpPy = [System.IO.Path]::GetTempFileName() + ".py"
Set-Content -Path $tmpPy -Encoding UTF8 -Value @(
    "import easyocr, os",
    "cache = os.path.join(os.path.expanduser('~'), '.EasyOCR', 'model')",
    "easyocr.Reader(['pt'], gpu=False, model_storage_directory=cache, download_enabled=True)",
    "print('EasyOCR weights ready:', cache)"
)
python $tmpPy
if ($LASTEXITCODE -ne 0) {
    Write-Host "  WARN EasyOCR weight download may have failed - check connectivity." -ForegroundColor Yellow
}
Remove-Item $tmpPy -ErrorAction SilentlyContinue

# Download Latin ONNX model for RapidOCR (Portuguese diacritics: a~ c, e^ o~).
# The bundled ch model silently drops diacritics; the en model covers Latin.
Write-Host ""
Write-Host "Downloading RapidOCR Latin model (en_PP-OCRv4_rec_mobile) for Portuguese..." -ForegroundColor Yellow
$rapidocrDir  = "$(Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))\.ocr-model-cache\rapidocr"
$rapidocrRec  = "$rapidocrDir\en_PP-OCRv4_rec_mobile.onnx"
$rapidocrKeys = "$rapidocrDir\en_dict.txt"
New-Item -ItemType Directory -Force -Path $rapidocrDir | Out-Null

if (-not (Test-Path $rapidocrRec)) {
    Invoke-WebRequest `
        -Uri "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv4/rec/en_PP-OCRv4_rec_mobile.onnx" `
        -OutFile $rapidocrRec -UseBasicParsing
} else {
    Write-Host "  en_PP-OCRv4_rec_mobile.onnx already present - skipping." -ForegroundColor DarkGray
}
if (-not (Test-Path $rapidocrKeys)) {
    Invoke-WebRequest `
        -Uri "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/paddle/PP-OCRv4/rec/en_PP-OCRv4_rec_mobile/en_dict.txt" `
        -OutFile $rapidocrKeys -UseBasicParsing
} else {
    Write-Host "  en_dict.txt already present - skipping." -ForegroundColor DarkGray
}
$env:RAPIDOCR_REC_MODEL = $rapidocrRec
$env:RAPIDOCR_REC_KEYS  = $rapidocrKeys
Write-Host "  RapidOCR Latin model ready: $rapidocrDir" -ForegroundColor Green

# Run a full preflight across all five engines.
Write-Host ""
Write-Host "Running preflight across all five OCR backends..." -ForegroundColor Yellow
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
python "$scriptDir\preflight_ocr_backends.py" --language pt
if ($LASTEXITCODE -ne 0) {
    Write-Host "  FAIL One or more engines failed preflight - see output above." -ForegroundColor Red
    $verifyFailed = $true
}

Write-Host ""
if ($verifyFailed) {
    Write-Host "=== Setup FAILED - one or more mandatory steps did not complete ===" -ForegroundColor Red
    Write-Host "    Review the FAIL entries above, fix the issues, and re-run this script." -ForegroundColor Red
    exit 1
}

Write-Host "=== Setup complete ===" -ForegroundColor Cyan
Write-Host "Run the E2E benchmark with:"
Write-Host "  # Smoke test - 5 pages, all engines"
Write-Host "  .\scripts\run_benchmark.ps1"
Write-Host ""
Write-Host "  # Full document"
Write-Host "  .\scripts\run_benchmark.ps1 -RunSuffix v2 -AllPages"
Write-Host ""
Write-Host "  # Single engine"
Write-Host "  .\scripts\run_benchmark.ps1 -Engine tesseract -AllPages -RunSuffix v2"
Write-Host "  .\scripts\run_benchmark.ps1 -Engine rapidocr-onnx -AllPages -RunSuffix v2"
