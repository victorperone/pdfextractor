# Phase 8 - E2E Benchmark: five deployment configurations, sequential
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
# Single configuration or a subset:
#   .\scripts\run_benchmark.ps1 -Configuration easyocr -AllPages -RunSuffix "v2"
#   .\scripts\run_benchmark.ps1 -Configuration "easyocr,tesseract" -AllPages -RunSuffix "v2"

param(
    [string]$Pages      = "77-81",
    [switch]$AllPages,
    [string]$RunSuffix  = "smoke",
    [Alias("Engine")][string]$Configuration = "",
    [string]$Corpus     = "corpus\Corpus_Stress_OCR_Markdown_V4.pdf",
    [string]$Reference  = "corpus\Corpus_Stress_OCR_Markdown_V4_REFERENCIA.md",
    [string]$Manifesto  = "corpus\Corpus_Stress_OCR_Markdown_V4_MANIFESTO.json",
    [string]$Validation = "corpus\Corpus_Stress_OCR_Markdown_V4_VALIDACAO.txt",
    [string]$CorpusLock = "corpus\acceptance-corpus.lock.json",
    [string]$OutDir     = "output\fase8",
    [string]$PythonBin  = "python",
    [string]$EnvironmentRoot = "",
    [switch]$Gpu,
    [ValidateSet("medium", "small")][string]$RapidOcrModelSize = $(if ($env:RAPIDOCR_MODEL_SIZE) { $env:RAPIDOCR_MODEL_SIZE } else { "medium" })
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Resolve paths relative to the repository root (parent of scripts\).
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent $ScriptDir
$env:EASYOCR_MODULE_PATH = if ($env:EASYOCR_MODULE_PATH) { $env:EASYOCR_MODULE_PATH } else { Join-Path $HOME ".cache\pdfextractor\easyocr" }
if ($EnvironmentRoot -and -not [System.IO.Path]::IsPathRooted($EnvironmentRoot)) {
    $EnvironmentRoot = Join-Path $RepoRoot $EnvironmentRoot
}
$allConfigurations = @("tesseract", "rapidocr-onnxruntime", "rapidocr-openvino", "easyocr", "paddle")
$engines = if ($Configuration) { @($Configuration -split "," | ForEach-Object { $_.Trim() }) } else { $allConfigurations }
foreach ($engine in $engines) {
    if ($engine -notin $allConfigurations) { throw "Invalid deployment configuration: $engine" }
    if ($EnvironmentRoot) {
        $enginePath = Join-Path $EnvironmentRoot "$engine\Scripts\python.exe"
        if (-not (Test-Path -LiteralPath $enginePath -PathType Leaf)) { throw "Missing isolated Python: $enginePath" }
    }
}
$rapidocrRequested = @($engines | Where-Object { $_ -like "rapidocr-*" }).Count -gt 0
$rapidocrSize = $RapidOcrModelSize
if ($rapidocrRequested) {
$rapidocrCacheHome = if ($env:RAPIDOCR_CACHE_HOME) { $env:RAPIDOCR_CACHE_HOME } else { Join-Path $HOME ".cache\pdfextractor" }
$rapidocrCache = Join-Path $rapidocrCacheHome "rapidocr"
$rapidocrNames = @("RAPIDOCR_DET_MODEL", "RAPIDOCR_REC_MODEL", "RAPIDOCR_REC_KEYS", "RAPIDOCR_CLS_MODEL")
$rapidocrFiles = @("PP-OCRv6_det_$rapidocrSize.onnx", "PP-OCRv6_rec_$rapidocrSize.onnx", "ppocrv6_${rapidocrSize}_dict.txt", "ch_ppocr_mobile_v2.0_cls_mobile.onnx")
$setCount = 0
foreach ($name in $rapidocrNames) { if (Get-Item "Env:$name" -ErrorAction SilentlyContinue) { $setCount++ } }
if ($setCount -eq 0) {
    for ($i = 0; $i -lt $rapidocrNames.Count; $i++) { Set-Item "Env:$($rapidocrNames[$i])" (Join-Path $rapidocrCache $rapidocrFiles[$i]) }
} elseif ($setCount -ne $rapidocrNames.Count) {
    throw "Set all four RAPIDOCR_* model paths or none; partial overrides are rejected."
}

}

Push-Location $RepoRoot
try {

# --- Validate corpus files exist before spending time on OCR ---
foreach ($file in @($Corpus, $Reference, $Manifesto, $Validation)) {
    if (-not (Test-Path $file)) {
        Write-Host "ERROR: file not found: $file" -ForegroundColor Red
        exit 2
    }
}

& $PythonBin scripts\provision_acceptance_corpus.py --manifest $CorpusLock
if ($LASTEXITCODE -ne 0) {
    Write-Host "Acceptance corpus lock verification failed; benchmark was not started." -ForegroundColor Red
    exit 2
}

if ($rapidocrRequested) {
& $PythonBin scripts\verify_rapidocr_artifacts.py --size $rapidocrSize `
    --detector $env:RAPIDOCR_DET_MODEL --recognizer $env:RAPIDOCR_REC_MODEL `
    --dictionary $env:RAPIDOCR_REC_KEYS --classifier $env:RAPIDOCR_CLS_MODEL
if ($LASTEXITCODE -ne 0) { throw "RapidOCR v6 artifact verification failed." }
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

# --- Deployment configuration loop ---
$failed      = @()
$metricFiles = @()

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

foreach ($engine in $engines) {
    $enginePython = $PythonBin
    if ($EnvironmentRoot) { $enginePython = Join-Path $EnvironmentRoot "$engine\Scripts\python.exe" }
    $runId      = "$RunSuffix-$engine"
    $configSlug = $engine -replace "-", "_"
    $backend    = $engine
    $provider   = $null
    if ($engine -eq "rapidocr-onnxruntime") { $backend = "rapidocr"; $provider = "onnxruntime" }
    if ($engine -eq "rapidocr-openvino") { $backend = "rapidocr"; $provider = "openvino" }
    $engineSlug = $backend -replace "-", "_"
    $hyp        = "$OutDir\extracted_${engineSlug}_${runId}.md"
    $metrics    = "$OutDir\metrics_v3_${configSlug}_${runId}.json"

    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host " Deployment configuration: $engine" -ForegroundColor Cyan
    if ($AllPages) {
        Write-Host " Pages: all" -ForegroundColor Cyan
    } else {
        Write-Host " Pages: $Pages" -ForegroundColor Cyan
    }
    Write-Host "========================================" -ForegroundColor Cyan

    # --- evaluate_e2e ---
    $evalArgs = @("scripts\evaluate_e2e.py", $Corpus, "--engine", $backend, "--run-id", $runId, "--output-dir", $OutDir, "--allow-partial")
    if ($Gpu) { $evalArgs += "--gpu" }
    if ($provider) { $evalArgs += @("--provider", $provider, "--rapidocr-model-size", $rapidocrSize) }
    if ($AllPages) {
        & $enginePython @evalArgs
    } else {
        & $enginePython @evalArgs --pages $Pages
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
