#!/usr/bin/env bash
# PP-OCRv6 model preparation — WSL/Linux only.
#
# If tests will run on Windows Server, use setup_v6_windows.ps1.
# This script is only for those who want to validate on WSL first.
#
# Uso:
#   bash scripts/eval_v6/setup_v6_env.sh [cache-home]
#
# Exemplo:
#   bash scripts/eval_v6/setup_v6_env.sh ~/.cache/pdfextractor/paddlex-v6-eval

set -euo pipefail

CACHE="${1:-$HOME/.cache/pdfextractor/paddlex-v6-eval}"

echo "Cache v6: $CACHE"
echo ""

source .venv/bin/activate

echo "Downloading pt-v6-medium models..."
PADDLE_PDX_CACHE_HOME="$CACHE" pdftext setup-paddle-models --paddle-model-profile pt-v6-medium --cache-home "$CACHE"

echo ""
echo "Downloading pt-v6-small models..."
PADDLE_PDX_CACHE_HOME="$CACHE" pdftext setup-paddle-models --paddle-model-profile pt-v6-small --cache-home "$CACHE"

echo ""
echo "Status:"
pdftext paddle-models-status --paddle-model-profile pt-v6-medium --cache-home "$CACHE"

echo ""
echo "Available models in: $CACHE/official_models/"
ls "$CACHE/official_models/" 2>/dev/null || echo "(empty — check errors above)"
