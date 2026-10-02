# Phase 8 - E2E Benchmark: all engines, sequential
#
# Defaults target the validated Stress OCR Markdown V4 corpus.
# All paths are relative to the repository root by default.
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
    [string]$Corpus     = "corpus\Corpus_Stress_OCR_Markdown_V4.pdf",
    [string]$Reference  = "corpus\Corpus_Stress_OCR_Markdown_V4_REFERENCIA.md",
    [string]$Manifesto  = "corpus\Corpus_Stress_OCR_Markdown_V4_MANIFESTO.json",
    [string]$Validation = "corpus\Corpus_Stress_OCR_Markdown_V4_VALIDACAO.txt",
    [string]$OutDir     = "output\fase8",
    [string]$PythonBin  = "python"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Resolve paths relative to the repository root (parent of scripts\).
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent $ScriptDir

Push-Location $RepoRoot
try {

# --- Validate corpus files exist before spending time on OCR ---
foreach ($file in @($Corpus, $Reference, $Manifesto, $Validation)) {
    if (-not (Test-Path $file)) {
        Write-Host "ERROR: file not found: $file" -ForegroundColor Red
        exit 2
    }
}

# --- RapidOCR Latin model (required for Portuguese diacritics) ---
$rapidocrCache = "$RepoRoot\.ocr-model-cache\rapidocr"
$rapidocrRec   = "$rapidocrCache\en_PP-OCRv4_rec_mobile.onnx"
$rapidocrKeys  = "$rapidocrCache\en_dict.txt"
if ((Test-Path $rapidocrRec) -and (Test-Path $rapidocrKeys)) {
    $env:RAPIDOCR_REC_MODEL = $rapidocrRec
    $env:RAPIDOCR_REC_KEYS  = $rapidocrKeys
} else {
    Write-Host "ERROR: RapidOCR Latin model not found in $rapidocrCache" -ForegroundColor Red
    Write-Host "       Run scripts\setup_ocr_benchmark.ps1 first to download the model." -ForegroundColor Red
    exit 2
}

# --- Corpus preflight (mirrors run_benchmark.sh inline Python check) ---
Write-Host ""
Write-Host "Running corpus preflight..." -ForegroundColor Cyan
$preflightScript = @'
import json, re, sys
from pathlib import Path

pdf_path, reference_path, manifesto_path, validation_path = map(Path, sys.argv[1:])
manifest = json.loads(manifesto_path.read_text(encoding='utf-8'))
pages = manifest.get('pages', [])
manifest_numbers = [int(p['page']) for p in pages]
reference = reference_path.read_text(encoding='utf-8')
page_pattern = re.compile(r'^##\s+P[\xe1a]gina\s+0*(\d+)', re.M | re.I)
reference_matches = list(page_pattern.finditer(reference))
reference_numbers = [int(m.group(1)) for m in reference_matches]
reference_sections = {
    int(m.group(1)): reference[m.start():(
        reference_matches[i+1].start() if i+1 < len(reference_matches) else len(reference)
    )].strip()
    for i, m in enumerate(reference_matches)
}
validation = validation_path.read_text(encoding='utf-8')
page_match = re.search(r'(?m)^P[\xe1a]ginas:\s*(\d+)\s*$', validation)
if not page_match:
    raise SystemExit("Validation report has no 'P\xe1ginas: N' summary")
validation_count = int(page_match.group(1))

try:
    import pypdfium2 as pdfium
except ImportError as exc:
    raise SystemExit('pypdfium2 is required to verify the PDF page count') from exc
pdf = pdfium.PdfDocument(str(pdf_path))
pdf_count = len(pdf)
pdf.close()

required_checks = (
    '[OK] JSON v\xe1lido',
    '[OK] Markdown reconstru\xeddo do JSON \xe9 id\xeantico byte a byte',
    '[OK] Hashes expected_markdown v\xe1lidos',
)
missing_checks = [c for c in required_checks if c not in validation]
if missing_checks:
    raise SystemExit('Validation report is missing checks: ' + ', '.join(missing_checks))
if '[FAIL]' in validation:
    raise SystemExit('Validation report contains a [FAIL] result')
if not pages or len(set(manifest_numbers)) != len(manifest_numbers):
    raise SystemExit('Manifesto has no pages or contains duplicate page numbers')
blank_pages = {
    int(p['page']): str(p.get('expected_markdown', '')).strip()
    for p in pages if p.get('blank')
}
expected_ref_numbers = [n for n in manifest_numbers if n not in blank_pages]
if reference_numbers != expected_ref_numbers:
    raise SystemExit('Reference page sections do not match nonblank manifesto pages')
for p in pages:
    number = int(p['page'])
    expected = str(p.get('expected_markdown', '')).strip()
    if number in blank_pages:
        if not expected or expected not in reference:
            raise SystemExit(f'Blank page {number} is not represented in the reference')
        continue
    actual = reference_sections.get(number, '')
    for blank_marker in blank_pages.values():
        actual = actual.replace(blank_marker, '')
    if actual.strip() != expected:
        raise SystemExit(f'Reference content differs from manifesto on page {number}')
if not (pdf_count == len(pages) == validation_count):
    raise SystemExit(
        f'Page count mismatch: PDF={pdf_count}, manifesto={len(pages)}, '
        f'validation report={validation_count}'
    )
expected_pdf_check = f'[OK] Quantidade de p\xe1ginas no PDF | {pdf_count}'
if expected_pdf_check not in validation:
    raise SystemExit('Validation report does not confirm the PDF page count')
print(f'Corpus preflight OK: {pdf_count} PDF pages, reference and manifesto aligned.')
'@

$tmpPreflight = [System.IO.Path]::GetTempFileName() + ".py"
[System.IO.File]::WriteAllText($tmpPreflight, $preflightScript, [System.Text.Encoding]::UTF8)
& $PythonBin $tmpPreflight $Corpus $Reference $Manifesto $Validation
Remove-Item $tmpPreflight -ErrorAction SilentlyContinue
if ($LASTEXITCODE -ne 0) {
    Write-Host "Corpus preflight failed; benchmark was not started." -ForegroundColor Red
    exit 2
}

# --- Engine loop ---
$allEngines = @("tesseract", "rapidocr-onnx", "rapidocr-openvino", "easyocr", "paddle")

if ($Engine -ne "") {
    $engines = $Engine -split "," | ForEach-Object { $_.Trim() }
} else {
    $engines = $allEngines
}

$failed      = @()
$metricFiles = @()

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

foreach ($engine in $engines) {
    $runId      = "$RunSuffix-$engine"
    $engineSlug = $engine -replace "-", "_"
    $hyp        = "$OutDir\extracted_${engineSlug}_${runId}.md"
    $metrics    = "$OutDir\metrics_${engineSlug}_${runId}.json"

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
        & $PythonBin scripts\evaluate_e2e.py $Corpus --engine $engine --run-id $runId --output-dir $OutDir
    } else {
        & $PythonBin scripts\evaluate_e2e.py $Corpus --engine $engine --pages $Pages --run-id $runId --output-dir $OutDir
    }

    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [ERROR] evaluate_e2e failed for $engine" -ForegroundColor Red
        $failed += $engine
        continue
    }

    # --- compute_metrics ---
    $runManifest = "$OutDir\manifesto_e2e_${engineSlug}_${runId}.json"
    & $PythonBin scripts\compute_metrics.py `
        --hypothesis $hyp `
        --manifesto $Manifesto `
        --run-manifest $runManifest `
        --engine $engine `
        --run-id $runId `
        --output-dir $OutDir

    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [ERROR] compute_metrics failed for $engine" -ForegroundColor Red
        $failed += $engine
        continue
    }

    # Collect only metrics files produced by this run (not stale files from
    # previous runs with the same suffix but different engines).
    if (Test-Path $metrics) {
        $metricFiles += $metrics
    }
}

# --- compare_engines ---
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Comparison table" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

$compareFailed = $false
if ($metricFiles.Count -gt 0) {
    & $PythonBin scripts\compare_engines.py @metricFiles --output "$OutDir\comparison_${RunSuffix}.md"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [ERROR] compare_engines failed (exit $LASTEXITCODE)" -ForegroundColor Red
        $compareFailed = $true
    } else {
        Write-Host "  Table: $OutDir\comparison_${RunSuffix}.md" -ForegroundColor Green
    }
} else {
    Write-Host "  No metrics files produced in this run." -ForegroundColor Yellow
}

Write-Host ""
if ($failed.Count -gt 0 -or $compareFailed) {
    if ($failed.Count -gt 0) {
        Write-Host "Failed engines: $($failed -join ', ')" -ForegroundColor Red
    }
    if ($compareFailed) {
        Write-Host "Comparison step failed - review errors above." -ForegroundColor Red
    }
    exit 1
} else {
    Write-Host "All engines completed successfully!" -ForegroundColor Green
}

} finally {
    Pop-Location
}
