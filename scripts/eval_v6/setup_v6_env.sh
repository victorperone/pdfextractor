#!/usr/bin/env bash
# Preparação de modelos PP-OCRv6 — WSL/Linux apenas.
#
# Se os testes serão executados no Windows Server, use setup_v6_windows.ps1.
# Este script é apenas para quem quiser validar no WSL antes.
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

echo "Baixando modelos pt-v6-medium..."
PADDLE_PDX_CACHE_HOME="$CACHE" pdftext setup-models --ocr-model-profile pt-v6-medium --cache-home "$CACHE"

echo ""
echo "Baixando modelos pt-v6-small..."
PADDLE_PDX_CACHE_HOME="$CACHE" pdftext setup-models --ocr-model-profile pt-v6-small --cache-home "$CACHE"

echo ""
echo "Status:"
pdftext models-status --ocr-model-profile pt-v6-medium --cache-home "$CACHE"

echo ""
echo "Modelos disponíveis em: $CACHE/official_models/"
ls "$CACHE/official_models/" 2>/dev/null || echo "(vazio — verifique erros acima)"
