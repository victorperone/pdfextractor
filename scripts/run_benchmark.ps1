# Phase 8 — E2E Benchmark: all engines, sequential
#
# Smoke test (5 pages):
#   .\scripts\run_benchmark.ps1
#
# Full document:
#   .\scripts\run_benchmark.ps1 -RunSuffix "v1" -AllPages
#
# Single engine:
#   .\scripts\run_benchmark.ps1 -Engine easyocr -AllPages -RunSuffix "v2"
#   .\scripts\run_benchmark.ps1 -Engine "easyocr,tesseract" -AllPages -RunSuffix "v2"

param(
    [string]$Pages      = "77-81",
    [switch]$AllPages,
    [string]$RunSuffix  = "smoke",
    [string]$Engine     = "",
    [string]$Corpus     = "C:\Users\a_victor.perone\workspace\pdfextractor\corpus\Corpus_Stress_OCR_Markdown_V4.pdf",
    [string]$Manifesto  = "C:\Users\a_victor.perone\workspace\pdfextractor\corpus\Corpus_Stress_OCR_Markdown_V4_MANIFESTO.json",
    [string]$OutDir     = "output\fase8"
)

$allEngines = @("tesseract", "rapidocr-onnx", "rapidocr-openvino", "easyocr", "paddle")

if ($Engine -ne "") {
    $engines = $Engine -split "," | ForEach-Object { $_.Trim() }
} else {
    $engines = $allEngines
}
$failed  = @()

foreach ($engine in $engines) {
    $runId      = "$RunSuffix-$engine"
    $engineSlug = $engine -replace "-", "_"
    $hyp        = "$OutDir\extracted_${engineSlug}_${runId}.md"

    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host " Engine: $engine" -ForegroundColor Cyan
    if ($AllPages) {
        Write-Host " Pages: all" -ForegroundColor Cyan
    } else {
        Write-Host " Pages: $Pages" -ForegroundColor Cyan
    }
    Write-Host "========================================" -ForegroundColor Cyan

    # --- evaluate_e2e ---
    if ($AllPages) {
        python scripts\evaluate_e2e.py $Corpus --engine $engine --run-id $runId --output-dir $OutDir
    } else {
        python scripts\evaluate_e2e.py $Corpus --engine $engine --pages $Pages --run-id $runId --output-dir $OutDir
    }

    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [ERROR] evaluate_e2e failed for $engine" -ForegroundColor Red
        $failed += $engine
        continue
    }

    # --- compute_metrics ---
    python scripts\compute_metrics.py --hypothesis $hyp --manifesto $Manifesto --engine $engine --run-id $runId --output-dir $OutDir

    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [ERROR] compute_metrics failed for $engine" -ForegroundColor Red
        $failed += $engine
    }
}

# --- compare_engines ---
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Comparison table" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

$metricsFiles = @(Get-ChildItem "$OutDir\metrics_*_${RunSuffix}-*.json" -ErrorAction SilentlyContinue |
    ForEach-Object { $_.FullName })

if ($metricsFiles) {
    python scripts\compare_engines.py @metricsFiles --output "$OutDir\comparison_${RunSuffix}.md"
    Write-Host "  Table: $OutDir\comparison_${RunSuffix}.md" -ForegroundColor Green
} else {
    Write-Host "  No metrics files found in $OutDir" -ForegroundColor Yellow
}

Write-Host ""
if ($failed) {
    Write-Host "Failed engines: $($failed -join ', ')" -ForegroundColor Red
    exit 1
} else {
    Write-Host "All engines completed successfully!" -ForegroundColor Green
}
