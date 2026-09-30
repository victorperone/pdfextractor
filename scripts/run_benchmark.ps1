# Fase 8 — Benchmark E2E: todas as engines, sequencial
#
# Smoke test (5 paginas):
#   .\scripts\run_benchmark.ps1
#
# Documento completo:
#   .\scripts\run_benchmark.ps1 -RunSuffix "v1" -AllPages

param(
    [string]$Pages      = "77-81",
    [switch]$AllPages,
    [string]$RunSuffix  = "smoke",
    [string]$Corpus     = "C:\Users\a_victor.perone\workspace\pdfextractor\corpus\Corpus_Stress_OCR_Markdown_V4.pdf",
    [string]$Manifesto  = "C:\Users\a_victor.perone\workspace\pdfextractor\corpus\Corpus_Stress_OCR_Markdown_V4_MANIFESTO.json",
    [string]$OutDir     = "output\fase8"
)

$engines = @("tesseract", "rapidocr-onnx", "rapidocr-openvino", "easyocr", "paddle")
$failed  = @()

foreach ($engine in $engines) {
    $runId      = "$RunSuffix-$engine"
    $engineSlug = $engine -replace "-", "_"
    $hyp        = "$OutDir\extracted_${engineSlug}_${runId}.md"

    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host " Engine: $engine" -ForegroundColor Cyan
    if ($AllPages) {
        Write-Host " Paginas: todas" -ForegroundColor Cyan
    } else {
        Write-Host " Paginas: $Pages" -ForegroundColor Cyan
    }
    Write-Host "========================================" -ForegroundColor Cyan

    # --- evaluate_e2e ---
    if ($AllPages) {
        python scripts\evaluate_e2e.py $Corpus --engine $engine --run-id $runId --output-dir $OutDir
    } else {
        python scripts\evaluate_e2e.py $Corpus --engine $engine --pages $Pages --run-id $runId --output-dir $OutDir
    }

    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [ERRO] evaluate_e2e falhou para $engine" -ForegroundColor Red
        $failed += $engine
        continue
    }

    # --- compute_metrics ---
    python scripts\compute_metrics.py --hypothesis $hyp --manifesto $Manifesto --engine $engine --run-id $runId --output-dir $OutDir

    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [ERRO] compute_metrics falhou para $engine" -ForegroundColor Red
        $failed += $engine
    }
}

# --- compare_engines ---
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Tabela comparativa" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

$metricsFiles = Get-ChildItem "$OutDir\metrics_*_${RunSuffix}-*.json" -ErrorAction SilentlyContinue |
    ForEach-Object { $_.FullName }

if ($metricsFiles) {
    python scripts\compare_engines.py @metricsFiles --output "$OutDir\comparison_${RunSuffix}.md"
    Write-Host "  Tabela: $OutDir\comparison_${RunSuffix}.md" -ForegroundColor Green
} else {
    Write-Host "  Nenhum arquivo de metricas encontrado em $OutDir" -ForegroundColor Yellow
}

Write-Host ""
if ($failed) {
    Write-Host "Engines com falha: $($failed -join ', ')" -ForegroundColor Red
    exit 1
} else {
    Write-Host "Todas as engines concluidas com sucesso!" -ForegroundColor Green
}
