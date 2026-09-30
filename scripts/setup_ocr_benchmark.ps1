# setup_ocr_benchmark.ps1
# Instalação reprodutível do ambiente de benchmark OCR (Windows PowerShell)
#
# Testado em: Windows Server 2025, Python 3.12.10, AMD64
# Tesseract: v5.5.3 com tessdata por
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

# --- 3. RapidOCR OpenVINO (instalação em duas etapas — constraint conflitante) ---
Write-Host ""
Write-Host "[3/4] RapidOCR + OpenVINO 2024.4.0..." -ForegroundColor Yellow
# openvino exige numpy<2.1.0; instalar antes para resolver conflito
pip install "openvino==2024.4.0"
# rapidocr-openvino declara openvino<=2024.0.0 (sem wheel cp312 win64)
# usamos --no-deps para manter openvino==2024.4.0 que tem suporte a Python 3.12
pip install "rapidocr-openvino==1.4.4" --no-deps

# --- 4. EasyOCR (Fase 7 — instalar antes de confirmar disponibilidade) ---
# Descomentado quando Fase 7 for implementada
# Write-Host ""
# Write-Host "[4/4] EasyOCR..." -ForegroundColor Yellow
# pip install "easyocr>=1.7"

# --- Verificação final ---
Write-Host ""
Write-Host "=== Verificando instalações ===" -ForegroundColor Cyan

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
Write-Host "Verificando Tesseract (executável do sistema)..." -ForegroundColor Yellow
try {
    $tessVer = (tesseract --version 2>&1 | Select-String "tesseract").ToString().Trim()
    Write-Host "  OK  $tessVer" -ForegroundColor Green
    $tessLangs = tesseract --list-langs 2>&1 | Out-String
    if ($tessLangs -match "por") {
        Write-Host "  OK  tessdata: por (Português) disponível" -ForegroundColor Green
    } else {
        Write-Host "  FAIL tessdata 'por' não encontrado — baixe em:" -ForegroundColor Red
        Write-Host "       https://github.com/tesseract-ocr/tessdata_best/raw/main/por.traineddata" -ForegroundColor Red
        Write-Host "       Copiar para: C:\Program Files\Tesseract-OCR\tessdata\" -ForegroundColor Red
    }
} catch {
    Write-Host "  FAIL Tesseract não encontrado no PATH" -ForegroundColor Red
    Write-Host "       Instale de: https://github.com/UB-Mannheim/tesseract/wiki" -ForegroundColor Red
}

Write-Host ""
Write-Host "=== Setup completo ===" -ForegroundColor Cyan
Write-Host "Execute o benchmark com:"
Write-Host "  python scripts\benchmark_raw_ocr.py corpus\Document_AI_V3.pdf --engine paddle --pages 72-103 --output-dir output\benchmark_raw"
Write-Host "  python scripts\benchmark_raw_ocr.py corpus\Document_AI_V3.pdf --engine rapidocr-onnx --pages 72-103 --output-dir output\benchmark_raw"
Write-Host "  python scripts\benchmark_raw_ocr.py corpus\Document_AI_V3.pdf --engine rapidocr-openvino --pages 72-103 --output-dir output\benchmark_raw"
Write-Host "  python scripts\benchmark_raw_ocr.py corpus\Document_AI_V3.pdf --engine tesseract --pages 72-103 --output-dir output\benchmark_raw"
