# Preparação de modelos PP-OCRv6 — Windows Server.
#
# Baixa os pesos dos modelos v6 no cache de avaliação isolado do v5.
# Execute com rede disponível, ANTES de bloquear a internet.
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
Write-Host "Cache v5 : $env:USERPROFILE\.cache\pdfextractor\paddlex  (intocavel)"
Write-Host ""

# Baixar modelos pt-v6-medium
Write-Host "Baixando modelos pt-v6-medium..."
pdftext setup-models --ocr-model-profile pt-v6-medium --cache-home $CacheHome

Write-Host ""
Write-Host "Baixando modelos pt-v6-small..."
pdftext setup-models --ocr-model-profile pt-v6-small --cache-home $CacheHome

Write-Host ""
Write-Host "Status dos modelos v6:"
pdftext models-status --ocr-model-profile pt-v6-medium --cache-home $CacheHome

Write-Host ""
Write-Host "Modelos em: $CacheHome\official_models\"
Get-ChildItem "$CacheHome\official_models\" -ErrorAction SilentlyContinue | Select-Object Name

Write-Host ""
Write-Host "Pronto. Para testar:"
Write-Host "  python scripts\eval_v6\smoke_test_v6.py --cache-home $CacheHome --pdf corpus\Document_AI_V2.pdf"
Write-Host ""
Write-Host "Para comparar v5 vs v6:"
Write-Host "  python scripts\eval_v6\compare_v5_v6.py --v6-cache $CacheHome"
