#!/usr/bin/env bash
# Run all five OCR deployment configurations on the V3 and V4 corpora.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$REPO_ROOT/.venv/bin/python}"
export EASYOCR_MODULE_PATH="${EASYOCR_MODULE_PATH:-$REPO_ROOT/.ocr-model-cache/easyocr}"
RAPIDOCR_MODEL_SIZE="${RAPIDOCR_MODEL_SIZE:-medium}"
RAPIDOCR_CACHE_HOME="${RAPIDOCR_CACHE_HOME:-$HOME/.cache/pdfextractor}"
RAPIDOCR_CACHE="$RAPIDOCR_CACHE_HOME/rapidocr"
RAPIDOCR_NAMES=(RAPIDOCR_DET_MODEL RAPIDOCR_REC_MODEL RAPIDOCR_REC_KEYS RAPIDOCR_CLS_MODEL)
RAPIDOCR_FILES=("PP-OCRv6_det_${RAPIDOCR_MODEL_SIZE}.onnx" "PP-OCRv6_rec_${RAPIDOCR_MODEL_SIZE}.onnx" "ppocrv6_${RAPIDOCR_MODEL_SIZE}_dict.txt" "ch_ppocr_mobile_v2.0_cls_mobile.onnx")
RAPIDOCR_SET=0
RAPIDOCR_UNSET=0
for name in "${RAPIDOCR_NAMES[@]}"; do
    if [[ -n "${!name:-}" ]]; then ((RAPIDOCR_SET+=1)); else ((RAPIDOCR_UNSET+=1)); fi
done
if ((RAPIDOCR_SET == 0)); then
    for index in "${!RAPIDOCR_NAMES[@]}"; do export "${RAPIDOCR_NAMES[$index]}=$RAPIDOCR_CACHE/${RAPIDOCR_FILES[$index]}"; done
elif ((RAPIDOCR_UNSET != 0)); then
    echo "Set all four RAPIDOCR_* model paths or none; partial overrides are rejected." >&2
    exit 2
fi
export PATH="$REPO_ROOT/.venv/bin:$REPO_ROOT/.ocr-runtime/bin:$PATH"

cd "$REPO_ROOT"

for required in "$EASYOCR_MODULE_PATH/craft_mlt_25k.pth" \
                "$EASYOCR_MODULE_PATH/latin_g2.pth" \
                "$REPO_ROOT/.venv/bin/tesseract"; do
    [[ -s "$required" ]] || { echo "Required OCR artifact is missing: $required" >&2; exit 2; }
done
"$PYTHON_BIN" scripts/verify_rapidocr_artifacts.py --size "$RAPIDOCR_MODEL_SIZE" \
    --detector "$RAPIDOCR_DET_MODEL" --recognizer "$RAPIDOCR_REC_MODEL" \
    --dictionary "$RAPIDOCR_REC_KEYS" --classifier "$RAPIDOCR_CLS_MODEL"

run_corpus() {
    local version="$1" pdf="$2" reference="$3" manifesto="$4"
    local run_id_prefix="${version,,}-ppocrv6-${RAPIDOCR_MODEL_SIZE}-full-20261021"
    local out_dir="output/$version"
    local config backend provider run_id hypothesis run_manifest metric_file log_file
    local -a metric_files=()
    mkdir -p "$out_dir"

    for config in tesseract rapidocr-onnxruntime rapidocr-openvino easyocr paddle; do
        backend="$config"
        provider=""
        case "$config" in
            rapidocr-onnxruntime) backend=rapidocr; provider=onnxruntime ;;
            rapidocr-openvino) backend=rapidocr; provider=openvino ;;
        esac
        run_id="$run_id_prefix-$config"
        hypothesis="$out_dir/extracted_${backend}_${run_id}.md"
        run_manifest="$out_dir/manifesto_e2e_${backend}_${run_id}.json"
        metric_file="$out_dir/metrics_v3_${config//-/_}_${run_id}.json"
        log_file="$out_dir/run_${config}_${run_id}.log"

        echo "[$version] $config: starting full-document extraction" | tee -a "$log_file"
        eval_args=("$pdf" --engine "$backend" --mode balanced --language pt-BR \
            --run-id "$run_id" --output-dir "$out_dir" --allow-partial --quiet)
        [[ -z "$provider" ]] || eval_args+=(--provider "$provider" --rapidocr-model-size "$RAPIDOCR_MODEL_SIZE")
        if [[ -s "$hypothesis" && -s "$run_manifest" ]] && \
           "$PYTHON_BIN" -c 'import json,sys; raise SystemExit(0 if json.load(open(sys.argv[1], encoding="utf-8")).get("engine_identity") else 1)' "$run_manifest"; then
            echo "[$version] $config: reusing completed extraction artifacts" | tee -a "$log_file"
        else
            if ! "$PYTHON_BIN" "$SCRIPT_DIR/evaluate_e2e.py" "${eval_args[@]}" >>"$log_file" 2>&1; then
                echo "[$version] $config extraction failed; see $log_file" >&2
                tail -n 80 "$log_file" >&2
                return 1
            fi
        fi

        metric_args=(--hypothesis "$hypothesis" --manifesto "$manifesto" \
            --run-manifest "$run_manifest" --engine "$config" --run-id "$run_id" \
            --output-dir "$out_dir" --quiet)
        # V3 has an external reference Markdown; V4 stores every page, including
        # its two blank-page controls, in the versioned manifest.
        if [[ -n "$reference" ]]; then metric_args+=(--reference "$reference"); fi
        if ! "$PYTHON_BIN" "$SCRIPT_DIR/compute_metrics.py" "${metric_args[@]}" >>"$log_file" 2>&1; then
            echo "[$version] $config metrics failed; see $log_file" >&2
            tail -n 80 "$log_file" >&2
            return 1
        fi
        metric_files+=("$metric_file")
        echo "[$version] $config: extraction and metrics complete ($run_id)"
    done

    "$PYTHON_BIN" "$SCRIPT_DIR/compare_engines.py" "${metric_files[@]}" \
        --output "$out_dir/comparison_${run_id_prefix}.md"
}

run_corpus V3 \
    corpus/V3/Document_AI_V3.pdf \
    corpus/V3/Document_AI_V3.md \
    corpus/V3/Document_AI_V3_MANIFESTO.json

run_corpus V4 \
    corpus/V4/Corpus_Stress_OCR_Markdown_V4.pdf \
    "" \
    corpus/V4/Corpus_Stress_OCR_Markdown_V4_MANIFESTO.json
