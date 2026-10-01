# PP-OCRv6 model preparation — Windows Server.
#
# Downloads the v6 model weights into the evaluation cache, isolated from v5.
# Run with network available, BEFORE blocking internet access.
#
# Uso (PowerShell):
#   cd C:\caminho\para\pdfextractor
#   .\scripts\eval_v6\setup_v6_windows.ps1
#
# Ou com cache personalizado:
#   .\scripts\eval_v6\setup_v6_windows.ps1 -CacheHome "D:\models\paddlex-v6-eval"

param(
    [string]$CacheHome = "$env:USERPROFILE\.cache\pdfextractor\paddlex-v6-eval"
)

$ErrorActionPreference = "Stop"

Write-Host "Cache v6 : $CacheHome"
Write-Host "Cache v5 : $env:USERPROFILE\.cache\pdfextractor\paddlex  (untouched)"
Write-Host ""

# Download pt-v6-medium models
Write-Host "Downloading pt-v6-medium models..."
pdftext setup-models --ocr-model-profile pt-v6-medium --cache-home $CacheHome

Write-Host ""
Write-Host "Downloading pt-v6-small models..."
pdftext setup-models --ocr-model-profile pt-v6-small --cache-home $CacheHome

Write-Host ""
Write-Host "v6 model status:"
pdftext models-status --ocr-model-profile pt-v6-medium --cache-home $CacheHome

Write-Host ""
Write-Host "Modelos em: $CacheHome\official_models\"
Get-ChildItem "$CacheHome\official_models\" -ErrorAction SilentlyContinue | Select-Object Name

Write-Host ""
Write-Host "Done. To test:"
Write-Host "  python scripts\eval_v6\smoke_test_v6.py --cache-home $CacheHome --pdf corpus\Document_AI_V2.pdf"
Write-Host ""
Write-Host "To compare v5 vs v6:"
Write-Host "  python scripts\eval_v6\compare_v5_v6.py --v6-cache $CacheHome"
