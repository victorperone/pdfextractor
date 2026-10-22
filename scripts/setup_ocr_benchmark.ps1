# Provision one virtualenv per deployment profile (Python 3.12).
param(
    [string]$PythonBin = "python",
    [string]$EnvironmentRoot = "",
    [string]$CacheHome = ""
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if ($null -eq (Get-Command tesseract -ErrorAction SilentlyContinue)) {
    throw "Install Tesseract 5.x with Portuguese traineddata and add it to PATH."
}
$setupScript = Join-Path $PSScriptRoot "setup_ocr_environments.py"
$setupArgs = @($setupScript, "--configuration", "tesseract", "rapidocr-onnxruntime",
               "rapidocr-openvino", "easyocr", "paddle", "--provision-models")
if ($EnvironmentRoot) { $setupArgs += @("--env-root", $EnvironmentRoot) }
if ($CacheHome) { $setupArgs += @("--cache-home", $CacheHome) }
& $PythonBin @setupArgs
if ($LASTEXITCODE -ne 0) { throw "OCR environment setup failed (exit $LASTEXITCODE)." }
