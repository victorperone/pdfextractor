#!/usr/bin/env bash
# Fase 1 — Preparação do ambiente Python e de modelos PP-OCRv6.
#
# Cria um venv isolado (.venv-paddle-v6-eval) com as mesmas versões fixadas
# do projeto e baixa os pesos dos modelos v6 no cache separado de avaliação.
#
# Uso:
#   bash scripts/eval_v6/setup_v6_env.sh
#
# Requisitos:
#   - python3.12 disponível no PATH
#   - Acesso à internet (apenas durante setup — bloqueado após isso)
#   - ~4 GB livres em ~/.cache/pdfextractor/paddlex-v6-eval/
#
# NÃO EXECUTE com rede bloqueada. NÃO execute com o venv de produção ativo.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
V6_VENV="$REPO_ROOT/.venv-paddle-v6-eval"
V6_CACHE="$HOME/.cache/pdfextractor/paddlex-v6-eval"
V5_CACHE="$HOME/.cache/pdfextractor/paddlex"

echo "=== Fase 1: Setup do ambiente v6 ==="
echo "Repositório : $REPO_ROOT"
echo "Venv        : $V6_VENV"
echo "Cache v6    : $V6_CACHE"
echo "Cache v5    : $V5_CACHE (INTOCÁVEL)"
echo ""

# ── Verificações de segurança ──────────────────────────────────────────────
if [ -d "$V6_VENV" ]; then
    echo "[OK] Venv já existe: $V6_VENV"
else
    echo "[...] Criando venv Python 3.12..."
    python3.12 -m venv "$V6_VENV"
    echo "[OK] Venv criado."
fi

# Ativar venv de avaliação
# shellcheck source=/dev/null
source "$V6_VENV/bin/activate"

echo "[...] Instalando setuptools e wheel..."
pip install --quiet setuptools wheel

echo "[...] Instalando paddlepaddle (CPU)..."
pip install --quiet paddlepaddle==3.3.1 \
    -i https://www.paddlepaddle.org.cn/packages/stable/cpu/

echo "[...] Instalando pacotes Python (versões fixadas do projeto)..."
pip install --quiet \
    "paddleocr==3.7.0" \
    "paddlex==3.7.2" \
    "pypdfium2==5.13.0" \
    "Pillow==12.3.0" \
    "numpy==2.3.5" \
    "opencv-contrib-python==4.10.0.84"

echo "[...] Verificando consistência de dependências..."
pip check

echo ""
echo "=== Versões instaladas ==="
pip show paddleocr paddlepaddle paddlex | grep -E "^Name:|^Version:"

echo ""
echo "[...] Salvando freeze do ambiente para reprodutibilidade..."
pip freeze > "$REPO_ROOT/docs/env-v6-eval-freeze.txt"
echo "[OK] Salvo em docs/env-v6-eval-freeze.txt"

# ── Download dos modelos v6 ────────────────────────────────────────────────
echo ""
echo "=== Download de modelos PP-OCRv6 ==="
echo "Destino: $V6_CACHE/official_models/"
echo ""
echo "ATENÇÃO: Modelos serão baixados agora. A internet é necessária."
echo "Após o download, a execução é 100% offline."
echo ""

export PADDLE_PDX_CACHE_HOME="$V6_CACHE"

# Usar o próprio setup-models do projeto para baixar via perfil pt-v6-medium
# Isso garante que os modelos ficam no formato esperado pelo engine.
echo "[...] Baixando perfil pt-v6-medium via pdftext setup-models..."
if python -m structured_pdf_text.cli setup-models \
        --ocr-model-profile pt-v6-medium \
        --cache-home "$V6_CACHE" 2>&1; then
    echo "[OK] Modelos pt-v6-medium baixados."
else
    echo "[WARN] setup-models retornou erro. Verifique manualmente:"
    echo "  ls -la $V6_CACHE/official_models/"
fi

echo ""
echo "[...] Baixando perfil pt-v6-small via pdftext setup-models..."
if python -m structured_pdf_text.cli setup-models \
        --ocr-model-profile pt-v6-small \
        --cache-home "$V6_CACHE" 2>&1; then
    echo "[OK] Modelos pt-v6-small baixados."
else
    echo "[WARN] setup-models retornou erro para pt-v6-small."
fi

echo ""
echo "=== Status final dos modelos v6 ==="
python -m structured_pdf_text.cli models-status \
    --ocr-model-profile pt-v6-medium \
    --cache-home "$V6_CACHE" || true

echo ""
echo "=== Conteúdo do cache v6 ==="
ls -la "$V6_CACHE/official_models/" 2>/dev/null || echo "(cache vazio)"

echo ""
echo "=== Cache v5 (deve permanecer inalterado) ==="
ls -la "$V5_CACHE/official_models/" 2>/dev/null || echo "(cache v5 não encontrado)"

echo ""
echo "=== Setup concluído ==="
echo "Para usar o ambiente de avaliação:"
echo "  source $V6_VENV/bin/activate"
echo "  python scripts/eval_v6/smoke_test_v6.py"
echo ""
echo "Para usar o perfil v6 na extração:"
echo "  pdftext extract documento.pdf --ocr-model-profile pt-v6-medium \\"
echo "      --cache-home $V6_CACHE"
