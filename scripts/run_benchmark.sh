#!/usr/bin/env bash
# Fase 8 E2E benchmark runner for Linux and WSL.
# Defaults mirror run_benchmark.ps1's five-page smoke run.
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python}"
if [[ -x "$REPO_ROOT/.ocr-runtime/bin/tesseract" ]]; then
    PATH="$REPO_ROOT/.ocr-runtime/bin:$PATH"
    export PATH
fi
EASYOCR_MODULE_PATH="${EASYOCR_MODULE_PATH:-$REPO_ROOT/.ocr-model-cache/easyocr}"
export EASYOCR_MODULE_PATH
PDF="corpus/Document_AI_V3.pdf"
REFERENCE="corpus/Document_AI_V3.md"
MANIFESTO="corpus/Document_AI_V3_MANIFESTO.json"
PAGES="77-81"
RUN_SUFFIX="smoke"
OUT_DIR="output/fase8"
ENGINES="tesseract,rapidocr-onnx,rapidocr-openvino,easyocr,paddle"
ALL_PAGES=0

usage() {
    cat <<'EOF'
Usage: scripts/run_benchmark.sh [options]
  --pdf PATH          PDF input (default: corpus/Document_AI_V3.pdf)
  --reference PATH    Markdown ground truth
  --manifesto PATH    Corpus metadata JSON
  --pages RANGE       1-based page range/list (default: 77-81)
  --all-pages         Evaluate the complete PDF
  --run-suffix NAME   Output run suffix (default: smoke)
  --output-dir PATH   Artifact directory (default: output/fase8)
  --engines LIST      Comma-separated engines (default: all five)
  --python PATH       Python executable (default: python or PYTHON_BIN)
EOF
}

while (($#)); do
    case "$1" in
        --pdf) PDF="$2"; shift 2 ;;
        --reference) REFERENCE="$2"; shift 2 ;;
        --manifesto) MANIFESTO="$2"; shift 2 ;;
        --pages) PAGES="$2"; shift 2 ;;
        --all-pages) ALL_PAGES=1; shift ;;
        --run-suffix) RUN_SUFFIX="$2"; shift 2 ;;
        --output-dir) OUT_DIR="$2"; shift 2 ;;
        --engines) ENGINES="$2"; shift 2 ;;
        --python) PYTHON_BIN="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

cd "$REPO_ROOT"
[[ -f "$PDF" ]] || { echo "PDF not found: $PDF" >&2; exit 2; }
[[ -f "$REFERENCE" ]] || { echo "Reference not found: $REFERENCE" >&2; exit 2; }
[[ -f "$MANIFESTO" ]] || { echo "Manifesto not found: $MANIFESTO" >&2; exit 2; }
mkdir -p "$OUT_DIR"

failed=()
metric_files=()
IFS=',' read -r -a engine_list <<< "$ENGINES"
for engine in "${engine_list[@]}"; do
    engine="${engine//[[:space:]]/}"
    run_id="$RUN_SUFFIX-$engine"
    engine_slug="${engine//-/_}"
    hypothesis="$OUT_DIR/extracted_${engine_slug}_${run_id}.md"
    metrics="$OUT_DIR/metrics_${engine_slug}_${run_id}.json"

    echo
    echo "========================================"
    echo " Engine: $engine"
    if ((ALL_PAGES)); then echo " Pages: all"; else echo " Pages: $PAGES"; fi
    echo "========================================"

    eval_args=("$PDF" --engine "$engine" --run-id "$run_id" --output-dir "$OUT_DIR")
    if ((!ALL_PAGES)); then eval_args+=(--pages "$PAGES"); fi
    if ! "$PYTHON_BIN" "$SCRIPT_DIR/evaluate_e2e.py" "${eval_args[@]}"; then
        echo "[ERROR] E2E extraction failed for $engine" >&2
        failed+=("$engine")
        continue
    fi

    if ! "$PYTHON_BIN" "$SCRIPT_DIR/compute_metrics.py" \
        --hypothesis "$hypothesis" \
        --reference "$REFERENCE" \
        --manifesto "$MANIFESTO" \
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
    "$PYTHON_BIN" "$SCRIPT_DIR/compare_engines.py" "${metric_files[@]}" \
        --output "$OUT_DIR/comparison_${RUN_SUFFIX}.md"
fi

if ((${#failed[@]})); then
    printf '\nEngines with failures: %s\n' "${failed[*]}" >&2
    exit 1
fi
