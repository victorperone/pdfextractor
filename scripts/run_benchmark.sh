#!/usr/bin/env bash
# Fase 8 E2E benchmark runner for Linux and WSL.
# Defaults target the validated Stress OCR Markdown V4 corpus.
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python}"
if [[ -x "$REPO_ROOT/.ocr-runtime/bin/tesseract" ]]; then
    PATH="$REPO_ROOT/.ocr-runtime/bin:$PATH"
    export PATH
fi
EASYOCR_MODULE_PATH="${EASYOCR_MODULE_PATH:-$HOME/.cache/pdfextractor/easyocr}"
export EASYOCR_MODULE_PATH

# RapidOCR Latin model (required for Portuguese diacritics — ã ç ê õ).
# Downloaded by setup_ocr_benchmark.sh into .ocr-model-cache/rapidocr/.
_RAPIDOCR_CACHE="$HOME/.cache/pdfextractor/rapidocr"
_RAPIDOCR_REC="$_RAPIDOCR_CACHE/latin_PP-OCRv3_rec_mobile.onnx"
_RAPIDOCR_KEYS="$_RAPIDOCR_CACHE/latin_dict.txt"
if [[ -f "$_RAPIDOCR_REC" && -f "$_RAPIDOCR_KEYS" ]]; then
    export RAPIDOCR_REC_MODEL="$_RAPIDOCR_REC"
    export RAPIDOCR_REC_KEYS="$_RAPIDOCR_KEYS"
else
    echo "ERROR: RapidOCR Latin recognizer/dictionary pair not found in $_RAPIDOCR_CACHE" >&2
    echo "       Run scripts/setup_ocr_benchmark.sh first to download the pair." >&2
    exit 2
fi
PDF="corpus/Corpus_Stress_OCR_Markdown_V4.pdf"
REFERENCE="corpus/Corpus_Stress_OCR_Markdown_V4_REFERENCIA.md"
MANIFESTO="corpus/Corpus_Stress_OCR_Markdown_V4_MANIFESTO.json"
VALIDATION="corpus/Corpus_Stress_OCR_Markdown_V4_VALIDACAO.txt"
CORPUS_LOCK="corpus/acceptance-corpus.lock.json"
PAGES="77-81"
RUN_SUFFIX="smoke"
OUT_DIR="output/fase8/stress_v4"
CONFIGURATIONS="tesseract,rapidocr-onnxruntime,rapidocr-openvino,easyocr,paddle"
ALL_PAGES=0

usage() {
    cat <<'EOF'
Usage: scripts/run_benchmark.sh [options]
  --pdf PATH          PDF input (default: corpus/Corpus_Stress_OCR_Markdown_V4.pdf)
  --reference PATH    Markdown ground truth (Stress V4 by default)
  --manifesto PATH    Corpus metadata JSON (Stress V4 by default)
  --validation PATH   Corpus validation report (Stress V4 by default)
  --corpus-lock PATH  SHA-256 lock manifest for those local corpus files
  --pages RANGE       1-based page range/list (default: 77-81)
  --all-pages         Evaluate the complete PDF
  --run-suffix NAME   Output run suffix (default: smoke)
  --output-dir PATH   Artifact directory (default: output/fase8/stress_v4)
  --configurations LIST  Comma-separated deployment configurations (four families, five configurations)
  --engines LIST         Deprecated option alias for --configurations
  --python PATH       Python executable (default: python or PYTHON_BIN)
EOF
}

while (($#)); do
    case "$1" in
        --pdf) PDF="$2"; shift 2 ;;
        --reference) REFERENCE="$2"; shift 2 ;;
        --manifesto) MANIFESTO="$2"; shift 2 ;;
        --validation) VALIDATION="$2"; shift 2 ;;
        --corpus-lock) CORPUS_LOCK="$2"; shift 2 ;;
        --pages) PAGES="$2"; shift 2 ;;
        --all-pages) ALL_PAGES=1; shift ;;
        --run-suffix) RUN_SUFFIX="$2"; shift 2 ;;
        --output-dir) OUT_DIR="$2"; shift 2 ;;
        --configurations) CONFIGURATIONS="$2"; shift 2 ;;
        --engines) echo "Warning: --engines is deprecated; use --configurations" >&2; CONFIGURATIONS="$2"; shift 2 ;;
        --python) PYTHON_BIN="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

cd "$REPO_ROOT"
[[ -f "$PDF" ]] || { echo "PDF not found: $PDF" >&2; exit 2; }
[[ -f "$REFERENCE" ]] || { echo "Reference not found: $REFERENCE" >&2; exit 2; }
[[ -f "$MANIFESTO" ]] || { echo "Manifesto not found: $MANIFESTO" >&2; exit 2; }
[[ -f "$VALIDATION" ]] || { echo "Validation report not found: $VALIDATION" >&2; exit 2; }

"$PYTHON_BIN" "$SCRIPT_DIR/provision_acceptance_corpus.py" --manifest "$CORPUS_LOCK"

# Confirm that PDF, reference, manifesto, and the supplied validation report
# describe the same complete corpus before spending time on OCR.
if ! "$PYTHON_BIN" - "$PDF" "$REFERENCE" "$MANIFESTO" "$VALIDATION" <<'PY_PREFLIGHT'
import json
import re
import sys
from pathlib import Path

pdf_path, reference_path, manifesto_path, validation_path = map(Path, sys.argv[1:])
manifest = json.loads(manifesto_path.read_text(encoding="utf-8"))
pages = manifest.get("pages", [])
manifest_numbers = [int(page["page"]) for page in pages]
reference = reference_path.read_text(encoding="utf-8")
page_pattern = re.compile(r"^##\s+P[áa]gina\s+0*(\d+)", re.M | re.I)
reference_matches = list(page_pattern.finditer(reference))
reference_numbers = [int(match.group(1)) for match in reference_matches]
reference_sections = {
    int(match.group(1)): reference[match.start():(
        reference_matches[index + 1].start()
        if index + 1 < len(reference_matches) else len(reference)
    )].strip()
    for index, match in enumerate(reference_matches)
}
validation = validation_path.read_text(encoding="utf-8")
page_match = re.search(r"(?m)^Páginas:\s*(\d+)\s*$", validation)
if not page_match:
    raise SystemExit("Validation report has no 'Páginas: N' summary")
validation_count = int(page_match.group(1))

try:
    import pypdfium2 as pdfium
except ImportError as exc:
    raise SystemExit("pypdfium2 is required to verify the PDF page count") from exc
pdf = pdfium.PdfDocument(str(pdf_path))
pdf_count = len(pdf)
pdf.close()

required_checks = (
    "[OK] JSON válido",
    "[OK] Markdown reconstruído do JSON é idêntico byte a byte",
    "[OK] Hashes expected_markdown válidos",
)
missing_checks = [check for check in required_checks if check not in validation]
if missing_checks:
    raise SystemExit("Validation report is missing successful checks: " + ", ".join(missing_checks))
if "[FAIL]" in validation:
    raise SystemExit("Validation report contains a [FAIL] result")
if not pages or len(set(manifest_numbers)) != len(manifest_numbers):
    raise SystemExit("Manifesto has no pages or contains duplicate page numbers")
blank_pages = {
    int(page["page"]): str(page.get("expected_markdown", "")).strip()
    for page in pages if page.get("blank")
}
expected_reference_numbers = [number for number in manifest_numbers if number not in blank_pages]
if reference_numbers != expected_reference_numbers:
    raise SystemExit("Reference page sections do not match nonblank manifesto pages")
for page in pages:
    number = int(page["page"])
    expected = str(page.get("expected_markdown", "")).strip()
    if number in blank_pages:
        if not expected or expected not in reference:
            raise SystemExit(f"Blank page {number} is not represented in the reference")
        continue
    actual = reference_sections.get(number, "")
    for blank_marker in blank_pages.values():
        actual = actual.replace(blank_marker, "")
    if actual.strip() != expected:
        raise SystemExit(f"Reference content differs from manifesto on page {number}")
if not (pdf_count == len(pages) == validation_count):
    raise SystemExit(
        f"Page count mismatch: PDF={pdf_count}, manifesto={len(pages)}, "
        f"validation report={validation_count}"
    )
expected_pdf_check = f"[OK] Quantidade de páginas no PDF | {pdf_count}"
if expected_pdf_check not in validation:
    raise SystemExit("Validation report does not confirm the PDF page count")
print(f"Corpus preflight OK: {pdf_count} PDF pages, reference and manifesto aligned.")
PY_PREFLIGHT
then
    echo "Corpus preflight failed; benchmark was not started." >&2
    exit 2
fi

mkdir -p "$OUT_DIR"

failed=()
metric_files=()
IFS=',' read -r -a engine_list <<< "$CONFIGURATIONS"
for engine in "${engine_list[@]}"; do
    engine="${engine//[[:space:]]/}"
    run_id="$RUN_SUFFIX-$engine"
    config_slug="${engine//-/_}"
    metrics="$OUT_DIR/metrics_${config_slug}_${run_id}.json"
    backend="$engine"
    provider=""
    case "$engine" in
        rapidocr-onnxruntime) backend="rapidocr"; provider="onnxruntime" ;;
        rapidocr-openvino) backend="rapidocr"; provider="openvino" ;;
    esac
    engine_slug="${backend//-/_}"

    echo
    echo "========================================"
    echo " Deployment configuration: $engine"
    if ((ALL_PAGES)); then echo " Pages: all"; else echo " Pages: $PAGES"; fi
    echo "========================================"

    eval_args=("$PDF" --engine "$backend" --run-id "$run_id" --output-dir "$OUT_DIR" --allow-partial)
    if [[ -n "$provider" ]]; then eval_args+=(--provider "$provider"); fi
    if ((!ALL_PAGES)); then eval_args+=(--pages "$PAGES"); fi
    if ! "$PYTHON_BIN" "$SCRIPT_DIR/evaluate_e2e.py" "${eval_args[@]}"; then
        echo "[ERROR] E2E extraction failed for $engine" >&2
        failed+=("$engine")
        continue
    fi

    run_manifest="$OUT_DIR/manifesto_e2e_${engine_slug}_${run_id}.json"
    if ! "$PYTHON_BIN" "$SCRIPT_DIR/compute_metrics.py" \
        --hypothesis "$hypothesis" \
        --manifesto "$MANIFESTO" \
        --run-manifest "$run_manifest" \
        --engine "$engine" \
        --run-id "$run_id" \
        --output-dir "$OUT_DIR"; then
        echo "[ERROR] Metric computation failed for $engine" >&2
        failed+=("$engine")
        continue
    fi
    metric_files+=("$metrics")
done

if ((${#metric_files[@]})); then
    if ! "$PYTHON_BIN" "$SCRIPT_DIR/compare_engines.py" "${metric_files[@]}" \
        --output "$OUT_DIR/comparison_${RUN_SUFFIX}.md"; then
        echo "[ERROR] Engine comparison failed" >&2
        exit 1
    fi
fi

if ((${#failed[@]})); then
    printf '\nEngines with failures: %s\n' "${failed[*]}" >&2
    exit 1
fi
