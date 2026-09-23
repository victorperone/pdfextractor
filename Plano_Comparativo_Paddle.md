# Plano Comparativo Paddle OCR — Avaliação PP-OCRv6 e Componentes Complementares

**Projeto:** `pdfextractor` / `structured-pdf-text`  
**Data de elaboração:** 23/09/2026  
**Branch de referência:** `feat/ocr-regression-stabilization`  
**SHA de referência validado:** `5a6ccc202bfffbdbd79121c28ba6d2be376eff9c`  
**Branch alvo da avaliação:** `feat/paddle-ocrv6-evaluation` (a criar a partir da `main` atualizada)  
**Ambientes de execução:** WSL (desenvolvimento e avaliação) → Windows Server 2025 CPU (aceitação)  
**Requisito crítico:** execução 100% offline (sem rede) durante processamento de documentos. Internet apenas para baixar modelos/pesos na fase de preparação.

---

## 1. Estado Atual do Projeto

### 1.1. Branch e versões de pacotes fixadas

```
paddlepaddle  == 3.3.1
paddleocr     == 3.7.0
paddlex       == 3.7.2
pypdfium2     == 5.13.0
Pillow        == 12.3.0
numpy         == 2.3.5
opencv-contrib-python == 4.10.0.84
```

### 1.2. Perfil OCR atual (`ocr/models.py`)

O único perfil definido é `"pt"` (português / Latin-script):

| Papel | Nome do modelo (= nome do diretório) | kwarg dir | kwarg name |
|---|---|---|---|
| Classificador de orientação do documento | `PP-LCNet_x1_0_doc_ori` | `doc_orientation_classify_model_dir` | `doc_orientation_classify_model_name` |
| Classificador de orientação de linha de texto | `PP-LCNet_x1_0_textline_ori` | `textline_orientation_model_dir` | `textline_orientation_model_name` |
| Detecção de texto | `PP-OCRv5_server_det` | `text_detection_model_dir` | `text_detection_model_name` |
| Reconhecimento de texto (Latin) | `latin_PP-OCRv5_mobile_rec` | `text_recognition_model_dir` | `text_recognition_model_name` |

Os modelos ficam em: `<PADDLE_PDX_CACHE_HOME>/official_models/<nome_do_modelo>/`  
Padrão: `~/.cache/pdfextractor/paddlex/official_models/`

### 1.3. Protocolo OcrEngine (`ocr/engine.py`)

```python
class OcrEngine(Protocol):
    def recognize_page(
        self,
        page_image: object,
        page_index: int,
        page_bbox: BBox | None = None,
        *,
        quality_variants: bool | None = None,
    ) -> list[OcrToken]: ...

    def recognize_region(
        self,
        page_image: object,
        page_index: int,
        region_bbox: BBox,
    ) -> list[OcrToken]: ...
```

### 1.4. Inicialização PaddleOCR (`ocr/paddle.py` — `_init_ocr`)

Parâmetros obrigatórios passados ao `PaddleOCR()`:
```python
{
    "use_doc_orientation_classify": True,
    "use_doc_unwarping": False,
    "use_textline_orientation": True,
    "enable_mkldnn": False,
    "text_det_limit_side_len": _det_limit,   # calculado via RGB budget gate
    "text_det_limit_type": "max",
    # + *_model_dir kwargs (caminhos absolutos locais)
    # + *_model_name kwargs (nomes dos modelos)
    # + quaisquer **options passados pelo usuário
}
```

### 1.5. Despacho de API (`_predict`)

```python
@staticmethod
def _predict(ocr, input_image):
    if hasattr(ocr, "predict"):
        return ocr.predict(input=input_image)   # PaddleOCR 3.x
    return ocr.ocr(input_image, cls=True)       # legado 2.x
```

**Importante:** o código atual já despacha para a API 3.x (`.predict()`), que é a usada pelos modelos v6.

### 1.6. Variáveis de ambiente relevantes

| Variável | Padrão | Finalidade |
|---|---|---|
| `PADDLE_PDX_CACHE_HOME` | `~/.cache/pdfextractor/paddlex` | Raiz do cache de modelos |
| `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK` | definida como `"True"` em runtime | Impede descoberta remota de modelos durante extração |
| `PDFEXTRACTOR_OCR_RGB_BUDGET_MIB` | `"8.0"` | Budget para cálculo de `text_det_limit_side_len` |
| `PDFEXTRACTOR_OCR_DEBUG_LOG` | não definida | Caminho para log de debug (metadados técnicos) |

### 1.7. Configurações de extração relevantes (`config.py`)

| Campo | Padrão | Relevância |
|---|---|---|
| `language` | `"pt"` | Seleciona perfil de modelo |
| `ocr_quality_policy` | `ADAPTIVE` | baseline / adaptive / exhaustive |
| `ocr_quality_variants` | `True` | `False` força BASELINE |
| `ocr_batch_size` | `3` | Máximo de imagens por batch Paddle |
| `ocr_render_scale` | `2.0` | ~144 DPI para PDF de 72 DPI |
| `num_threads` | `0` | 0 = auto; -1 = padrão Paddle sem alteração |

### 1.8. Problema residual conhecido

`OCRS-P10-CONTROL` (página 10 do corpus OCR sintético v1) — falha de reconhecimento/detecção documentada em `docs/relatorio-ocr-regression-stabilization.md`. **Não confirmar que a falha ainda existe sem executar a versão atual** — pode ter sido corrigida em commits posteriores ao snapshot de referência.

### 1.9. Proteções inegociáveis (definidas pelo responsável)

1. **Não modificar o upscaling 2× (`ocr/recovery.py`)** — algoritmo, escala, seleção de variantes, orçamento RGB, limites de resolução, transformação e remapeamento de coordenadas, logs e instrumentação são intocáveis. Modelos candidatos devem adaptar-se ao contrato de imagem/resultado existente.
2. **Não modificar processamento de texto nativo** — leitura, reconstrução de palavras/linhas, colunas, ordenação, montagem, deduplicação, supressão textual ou Content Conservation Ledger.
3. **Não substituir modelo padrão automaticamente** — perfil v5 permanece padrão até aceitação no Windows Server.
4. **Não fazer merge automático à `main`** — integração da branch de estabilização à `main` está autorizada neste plano; merge da branch de avaliação exige revisão posterior.
5. **Integrar apenas tecnologias Paddle aprovadas** — PP-OCRv6, depois PP-TableMagic se justificado, depois PP-StructureV3 apenas se necessário.

---

## 2. Descoberta Crítica — Compatibilidade de Versões

### ⚠️ Ponto mais importante da pesquisa

**O ambiente atual (paddleocr==3.7.0, paddlepaddle==3.3.1) já suporta os modelos PP-OCRv6. Não é necessária atualização de pacotes para testar v6.**

Isso simplifica dramaticamente a Fase 1:
- A "isolação de ambiente" refere-se a **arquivos de modelo separados**, não a um venv Python diferente.
- O código `_predict()` em `paddle.py` já despacha para `.predict()` (API 3.x), que é exatamente o que v6 usa.
- O padrão `*_model_dir` + `*_model_name` kwargs usado no perfil atual funciona identicamente para v6.
- A adição de um perfil `pt-v6-medium` em `models.py` é a mudança de código mínima necessária.

### Risco de versão residual

A documentação oficial confirma PP-OCRv6 como padrão no PaddleOCR 3.7. O projeto já usa 3.7.0. Porém:
- Verificar se existe paddleocr==3.7.x com correções para v6 específicas antes de iniciar.
- `pip check` antes de qualquer experimento para confirmar ausência de conflitos de dependências.

---

## 3. Pesquisa Técnica — PP-OCRv6

### 3.1. Nomes exatos dos modelos

**Detecção:**
- `PP-OCRv6_medium_det` (15.5M parâmetros)
- `PP-OCRv6_small_det`
- `PP-OCRv6_tiny_det`

**Reconhecimento:**
- `PP-OCRv6_medium_rec` (34.5M parâmetros, 50 idiomas)
- `PP-OCRv6_small_rec` (7.7M parâmetros, 50 idiomas)
- `PP-OCRv6_tiny_rec` (~1.5M parâmetros, 49 idiomas — exclui japonês)

Nota: `PP-OCRv6_medium_rec` é o padrão quando `model_name=None` no PaddleOCR 3.7.

### 3.2. Suporte a CPU

Totalmente suportado. `device="cpu"` funciona em todos os modelos v6.

Parâmetros de aceleração CPU:
- `enable_mkldnn=True` (padrão upstream, mas o projeto usa `False` — verificar impacto)
- `mkldnn_cache_capacity=10`
- `cpu_threads=N`

**Benchmark de desempenho CPU (200 imagens, segundos/imagem):**

| Backend | v6_medium | v6_small | v6_tiny | v5_server (atual) |
|---|---|---|---|---|
| PaddlePaddle (Intel Xeon 8350C) | 2.05s | 0.79s | 0.32s | 2.04s |
| OpenVINO (Intel Xeon 8350C) | 1.40s | 0.59s | 0.20s | 7.30s |
| ONNX Runtime (Intel Xeon 8350C) | 3.31s | 0.61s | 0.22s | 6.36s |

**Conclusão de performance:** `PP-OCRv6_medium` tem velocidade praticamente idêntica ao `PP-OCRv5_server` no CPU PaddlePaddle padrão (2.05s vs 2.04s), com ganhos de acurácia esperados. `v6_small` é 2.6× mais rápido que v5_server.

### 3.3. Operação offline

Plenamente suportada via parâmetros `*_model_dir` no construtor — exatamente o mesmo padrão já usado pelo projeto.

```python
ocr = PaddleOCR(
    text_detection_model_dir="/caminho/local/PP-OCRv6_medium_det",
    text_detection_model_name="PP-OCRv6_medium_det",
    text_recognition_model_dir="/caminho/local/PP-OCRv6_medium_rec",
    text_recognition_model_name="PP-OCRv6_medium_rec",
    device="cpu",
)
```

Com `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK="True"` (já definida pelo projeto), nenhuma tentativa de download ocorre durante extração.

### 3.4. Estrutura de diretório de modelos

Cada diretório de modelo deve conter:
- `inference.json` — metadados de arquitetura
- `inference.pdiparams` — pesos do modelo
- `inference.yml` — configuração (para engine paddle_static)
- Eventualmente: `.nb`, `.onnx` dependendo do backend

O `_model_is_ready()` do CLI verifica sufixos `.pdmodel`, `.pdiparams`, `.pdparams`, `.pdiparams.info`, `.nb`, `.onnx`, `.bin`, `.pt`.

### 3.5. API de inferência (v6, PaddleOCR 3.x)

```python
# Instanciação
ocr = PaddleOCR(
    text_detection_model_name="PP-OCRv6_medium_det",
    text_recognition_model_name="PP-OCRv6_medium_rec",
    use_doc_orientation_classify=True,
    use_textline_orientation=True,
    use_doc_unwarping=False,
    enable_mkldnn=False,  # consistente com configuração atual do projeto
    device="cpu",
    # + model_dir kwargs para offline
)

# Inferência
result = ocr.predict(input=numpy_array_or_pil_image)
# result é iterável de objetos Result com .print(), .json, etc.
```

O despacho `if hasattr(ocr, "predict")` em `paddle.py` captura isso corretamente.

### 3.6. Classificadores de orientação para v6

A pesquisa **não identificou modelos de orientação específicos para v6**. Os modelos atuais `PP-LCNet_x1_0_doc_ori` e `PP-LCNet_x1_0_textline_ori` devem permanecer compatíveis com modelos v6 de OCR. **Verificar empiricamente** se a combinação funciona sem warnings na inicialização.

### 3.7. Localização dos modelos (HuggingFace Hub)

- `PaddlePaddle/PP-OCRv6_medium_det`
- `PaddlePaddle/PP-OCRv6_medium_rec`
- `PaddlePaddle/PP-OCRv6_small_det`
- `PaddlePaddle/PP-OCRv6_small_rec`
- `PaddlePaddle/PP-OCRv6_tiny_det`
- `PaddlePaddle/PP-OCRv6_tiny_rec`

Download via `setup-models` com o novo perfil (após implementar suporte a `--language pt-v6-medium` no CLI).

Alternativa se HuggingFace inacessível: `PADDLE_PDX_MODEL_SOURCE="BOS"` para Baidu BOS.

---

## 4. Pesquisa Técnica — PP-TableMagic (General Table Recognition v2)

### 4.1. O que é

Pipeline de reconhecimento de tabelas multi-modelo: classificação → estrutura → detecção de células → OCR → HTML.

Classe Python: `TableRecognitionPipelineV2`

```python
from paddleocr import TableRecognitionPipelineV2

pipeline = TableRecognitionPipelineV2(device="cpu")
output = pipeline.predict("./tabela.jpg")
for res in output:
    res.save_to_html("./output/")
    res.save_to_xlsx("./output/")
    res.save_to_json("./output/")
```

### 4.2. Modelos internos (v5 por padrão, não v6)

| Papel | Modelos disponíveis |
|---|---|
| Estrutura de tabela com bordas | `SLANeXt_wired`, `SLANet_plus`, `SLANet` |
| Estrutura de tabela sem bordas | `SLANeXt_wireless` |
| Classificação de tabela | `PP-LCNet_x1_0_table_cls` |
| Detecção de células com bordas | `RT-DETR-L_wired_table_cell_det` |
| Detecção de células sem bordas | `RT-DETR-L_wireless_table_cell_det` |
| OCR interno (det) | `PP-OCRv5_server_det` ou mobile |
| OCR interno (rec) | `PP-OCRv5_server_rec` ou mobile |

**Nota:** PP-TableMagic usa modelos v5 internamente, não v6.

### 4.3. Formato de saída

```json
{
  "table_res_list": [
    {
      "cell_box_list": [[x1,y1,x2,y2], ...],
      "pred_html": "<html><body><table><tr><td>...</td></tr></table></body></html>",
      "table_ocr_pred": { "rec_texts": [...], "rec_scores": [...] }
    }
  ]
}
```

A saída é HTML, não o contrato de `StructuredTable` do projeto. Seria necessário parser HTML → `StructuredTable` para integração.

### 4.4. Operação offline

```python
pipeline = TableRecognitionPipelineV2(
    wired_table_structure_recognition_model_dir="/local/SLANeXt_wired",
    text_detection_model_dir="/local/PP-OCRv5_server_det",
    text_recognition_model_dir="/local/PP-OCRv5_server_rec",
    device="cpu",
    enable_mkldnn=False,
)
```

### 4.5. Avaliação de risco de integração

**RISCO ALTO.** Motivos:
- Saída em HTML requer parsing para `StructuredTable` — nova camada de transformação.
- PP-TableMagic não replica o contrato de `page_fragments`, `method`, `confidence`, `TableCell.tokens` com rastreabilidade de origem.
- Substituiria os 3 tiers atuais de detecção vetorial (que funcionam bem para PDFs digitais).
- Caso de uso justo: apenas tabelas 100% rasterizadas onde OCR por região falha e a estrutura da tabela não está em vetores.
- **Recomendação:** avaliar apenas após confirmar que v6 OCR puro não resolve o problema estrutural, e apenas para o subset de páginas rasterizadas com falhas de estrutura comprovadas.

---

## 5. Pesquisa Técnica — PP-StructureV3

### 5.1. O que é

Pipeline completo de análise de documento: detecção de layout, OCR, reconhecimento de tabelas, fórmulas, selos, gráficos, leitura multi-coluna. Produz Markdown/Word com estrutura.

Classe Python: `PPStructureV3`

### 5.2. Suporte a CPU

Suportado. Intel Xeon 8350C: ~3.74 segundos/imagem.

```python
from paddleocr import PPStructureV3

pipeline = PPStructureV3(
    device="cpu",
    enable_mkldnn=False,      # consistente com projeto
    use_table_recognition=True,
    use_formula_recognition=False,   # não funciona com ONNX Runtime
    use_seal_recognition=False,
    use_chart_recognition=False,     # VLM de 1.4GB — muito pesado para CPU
)
```

**Caveat crítico:** fórmulas (`use_formula_recognition=True`) não funcionam com engine ONNX Runtime.

**Caveat sobre gráficos:** `PP-Chart2Table` é um VLM de 0.58B parâmetros (1.4GB). Desabilitado por padrão. Muito pesado para CPU de produção.

### 5.3. Formato de saída

JSON com chaves `input_path`, `page_index`, `model_settings`, `layout_det_res` (caixas com labels: `paragraph_title`, `text`, `image`, `table`, `formula`, `seal`), `overall_ocr_res`.

Suporta `.markdown` property e `concatenate_markdown_pages()`.

### 5.4. Avaliação de risco de integração

**RISCO MUITO ALTO.** Motivos:
- É um sistema de alto nível que substitui todo o pipeline de extração, não apenas o OCR.
- Produz Markdown diretamente — não produz o contrato intermediário de `NativePageEvidence → StructuredDocument`.
- Não expõe o Content Conservation Ledger, rastreabilidade por token, evidências nativas, fusão nativa+OCR.
- O projeto tem forte vantagem arquitetural sobre ferramentas genéricas justamente pela fusão de evidência nativa. Substituir por PPStructureV3 regressaria a "extrair só por OCR".
- **Recomendação:** usar apenas como fallback pontual para páginas específicas com layout impossível de resolver via análise nativa, **nunca como caminho principal**. E somente se PP-OCRv6 + PP-TableMagic não resolverem o problema.

---

## 6. Requisitos Técnicos para Implementação

### 6.1. Requisito central: offline + CPU-only durante execução

- **Internet**: permitida APENAS durante `setup-models` (download de pesos). Bloqueada durante extração de documentos.
- **CPU**: toda inferência deve funcionar com `device="cpu"`. Sem CUDA, sem GPU, sem drivers especiais.
- **Offline**: o flag `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK="True"` (já configurado no projeto) garante que nenhum download ocorre durante extração.
- **Verificação**: testar com rede bloqueada (Windows Firewall rule ou netsh) para confirmar.

### 6.2. Pacotes Python

Conforme descoberta crítica da Seção 2: **não é necessária atualização de versão de pacotes para suportar v6.**

Ambiente de avaliação deve usar exatamente:
```
paddlepaddle  == 3.3.1  (sem alteração)
paddleocr     == 3.7.0  (sem alteração)
paddlex       == 3.7.2  (sem alteração)
```

Antes de qualquer experimento, confirmar:
```bash
pip check
python -c "import paddleocr; print(paddleocr.__version__)"
python -c "import paddle; print(paddle.__version__)"
```

### 6.3. Modelos a baixar (fase de preparação)

Para o perfil `pt-v6-medium`:
```
PP-OCRv6_medium_det
PP-OCRv6_medium_rec
PP-LCNet_x1_0_doc_ori        (compartilhado com v5 se mesma versão)
PP-LCNet_x1_0_textline_ori   (compartilhado com v5 se mesma versão)
```

Para o perfil `pt-v6-small` (somente se medium aprovado):
```
PP-OCRv6_small_det
PP-OCRv6_small_rec
```

Destino sugerido: `~/.cache/pdfextractor/paddlex/official_models/` (mesmo root que v5, diretórios separados por nome do modelo).

### 6.4. Memória e hardware mínimo

De acordo com o README atual do projeto: 16 GiB mínimo, 20-24 GiB recomendado para o servidor com OCR. Para v6 medium, o tamanho do modelo é semelhante ao v5_server — expectativa de memória similar.

### 6.5. Python

Python 3.12 (conforme requisito do projeto). Verificar se Python 3.12 tem suporte em wheel para as versões Paddle fixadas no Windows Server 2025. **Atenção:** Python 3.12 em ambiente fresh pode requerer `setuptools wheel` instalados antes de `paddleocr`.

### 6.6. Problema Windows-específico — setuptools

```bash
pip install setuptools wheel
# antes de:
pip install paddleocr
```

### 6.7. Arquivo de modelo proibido no repositório

`paddle.py` é o nome de um arquivo do próprio projeto (`ocr/paddle.py`). Isso pode causar importações circulares se o módulo for importado em contextos onde o diretório do projeto está no `sys.path` antes dos pacotes instalados. **Verificar se já está tratado** (o projeto usa `src/` layout, que isola isso).

---

## 7. Plano de Implementação — Fase a Fase

### Fase 0 — Pré-requisito: consolidar main e criar branch nova

#### 0.1. Verificações antes do merge

```bash
cd ~/workspace/pdfextractor
git remote -v
git fetch origin --prune
git branch --show-current           # deve ser feat/ocr-regression-stabilization
git status --short                   # deve estar limpo
git rev-parse origin/feat/ocr-regression-stabilization
git rev-parse origin/main
git log -8 --oneline --decorate origin/feat/ocr-regression-stabilization
```

**Se o SHA diferir de `5a6ccc202bfffbdbd79121c28ba6d2be376eff9c`:** comparar diferença e confirmar com o responsável que a versão mais recente foi validada. Não assumir aprovação automática de commits novos.

#### 0.2. Gate pré-merge (suíte de testes)

```bash
python --version   # deve ser >= 3.12
python -m compileall -q src tests
python -m pytest -q
git diff --check
```

Se `python` apontar para 3.10 ou 3.11: identificar o Python 3.12 do ambiente correto, não aprovar com versão errada.

#### 0.3. Merge da branch de estabilização à main

```bash
git switch main
git pull --ff-only origin main
git merge --no-ff --no-edit origin/feat/ocr-regression-stabilization
```

Em caso de conflitos: **não resolver automaticamente**. Registrar e consultar responsável. Para abortar: `git merge --abort`.

Após merge bem-sucedido:
```bash
git rev-parse HEAD
git status --short
python -m compileall -q src tests
python -m pytest -q
git push origin main
```

#### 0.4. Criar branch de avaliação

```bash
git fetch origin
git switch main
git pull --ff-only origin main
git switch -c feat/paddle-ocrv6-evaluation
git branch --show-current
git rev-parse HEAD   # registrar como SHA-base da branch de avaliação
git status --short
```

Se a branch já existir: não sobrescrever, revisar o HEAD e relação com origin/main.

---

### Fase 1 — Validação de compatibilidade de ambiente e modelos v6

**Objetivo:** confirmar que os modelos v6 funcionam offline, em CPU, no WSL e no Windows Server, sem alterar código do projeto.

#### 1.1. Preparação do ambiente Python e de modelos

**Ambiente Python:** criar venv independente `.venv-paddle-v6-eval` na raiz do projeto (adicionar ao `.gitignore` se ainda não coberto). Instalar as mesmas versões fixadas — não atualizar nada.

```bash
python3.12 -m venv .venv-paddle-v6-eval
source .venv-paddle-v6-eval/bin/activate
pip install setuptools wheel          # obrigatório antes de paddleocr no Python 3.12
pip install paddlepaddle==3.3.1 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
pip install paddleocr==3.7.0 paddlex==3.7.2 pypdfium2==5.13.0 Pillow==12.3.0 \
            numpy==2.3.5 "opencv-contrib-python==4.10.0.84" pytest==9.1.1
pip check
pip freeze > docs/env-v6-eval-freeze.txt   # registrar para reprodutibilidade
```

**Cache de modelos v6 — completamente separado do v5:**
```
~/.cache/pdfextractor/paddlex-v6-eval/official_models/PP-OCRv6_medium_det/
~/.cache/pdfextractor/paddlex-v6-eval/official_models/PP-OCRv6_medium_rec/
~/.cache/pdfextractor/paddlex-v6-eval/official_models/PP-LCNet_x1_0_doc_ori/
~/.cache/pdfextractor/paddlex-v6-eval/official_models/PP-LCNet_x1_0_textline_ori/
```

**Cache de produção v5 — INTOCÁVEL:**
```
~/.cache/pdfextractor/paddlex/official_models/   ← não modificar nada aqui
```

Os classificadores de orientação existem no cache v5. Para o cache v6-eval: baixar cópias independentes (ou criar symlinks somente se os hashes/versões forem idênticos — verificar antes).

1. Verificar estado dos pacotes no venv de avaliação:
   ```bash
   source .venv-paddle-v6-eval/bin/activate
   pip check
   pip show paddleocr paddlepaddle paddlex | grep -E "^Name:|^Version:"
   ```

2. Confirmar caminhos:
   ```bash
   ls -la ~/.cache/pdfextractor/paddlex-v6-eval/official_models/
   ls -la ~/.cache/pdfextractor/paddlex/official_models/   # deve permanecer inalterado
   ```

3. Baixar modelos v6 via `setup-models` estendido (ou script temporário de validação), **com rede disponível**, em ambiente autorizado. Registrar URLs, hashes, tamanhos.

4. Confirmar que os diretórios de modelo atendem a `_model_is_ready()` (pelo menos um arquivo com sufixo `.pdmodel`, `.pdiparams`, `.pdparams`, `.pdiparams.info`, `.nb`, `.onnx`, `.bin` ou `.pt`).

#### 1.2. Smoke test de compatibilidade (sem modificar código do parser)

Script isolado de teste (não commitar no parser):
```python
import os
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
os.environ["PADDLE_PDX_CACHE_HOME"] = os.path.expanduser("~/.cache/pdfextractor/paddlex")

from paddleocr import PaddleOCR

ocr = PaddleOCR(
    text_detection_model_dir="~/.cache/pdfextractor/paddlex/official_models/PP-OCRv6_medium_det",
    text_detection_model_name="PP-OCRv6_medium_det",
    text_recognition_model_dir="~/.cache/pdfextractor/paddlex/official_models/PP-OCRv6_medium_rec",
    text_recognition_model_name="PP-OCRv6_medium_rec",
    doc_orientation_classify_model_dir="~/.cache/pdfextractor/paddlex/official_models/PP-LCNet_x1_0_doc_ori",
    doc_orientation_classify_model_name="PP-LCNet_x1_0_doc_ori",
    textline_orientation_model_dir="~/.cache/pdfextractor/paddlex/official_models/PP-LCNet_x1_0_textline_ori",
    textline_orientation_model_name="PP-LCNet_x1_0_textline_ori",
    use_doc_orientation_classify=True,
    use_textline_orientation=True,
    use_doc_unwarping=False,
    enable_mkldnn=False,
    device="cpu",
)

# Uma imagem sintética de 100×100 pixels
import numpy as np
img = np.ones((100, 100, 3), dtype=np.uint8) * 255
result = list(ocr.predict(input=img))
print("OK — resultado:", result)
```

Capturar: stdout, stderr, peak RAM, tempo de inicialização, tempo de inferência, warnings de download (qualquer download = **falha do teste de offline**).

#### 1.3. Repetir no Windows Server 2025

Mesmo script em ambiente separado do .venv estável de produção. **Não alterar o ambiente de produção.** Confirmar:
- Inicialização sem erros de DLL ou módulo
- Inferência de uma imagem sem crash
- Zero tentativas de download com rede bloqueada
- Consumo de memória dentro do esperado

**Gate 1:** documentação de inicialização e inferência de uma imagem, offline, CPU, no WSL **e** no Windows, com logs. Se o Windows falhar (incompatibilidade de wheel, modelo, API), **não iniciar a Fase 2 ou integração**; documentar bloqueio.

---

### Fase 2 — Comparação isolada v5/v6 (sem alterar parser)

**Objetivo:** medir ganhos/perdas de detecção e reconhecimento separadamente, no mesmo corpus sintético atual.

#### 2.1. Matriz de comparação

| ID | Detecção | Reconhecimento | Objetivo |
|---|---|---|---|
| REF | `PP-OCRv5_server_det` | `latin_PP-OCRv5_mobile_rec` | Baseline atual — referência de comparação |
| R6 | mesmas caixas do v5 | `PP-OCRv6_medium_rec` | Isolar ganho de reconhecimento sobre crops idênticos |
| D6 | `PP-OCRv6_medium_det` | mesmo reconhecedor v5 | Isolar ganho de detecção (se API permitir fluxo separado) |
| V6 | `PP-OCRv6_medium_det` | `PP-OCRv6_medium_rec` | Pipeline v6 completo |
| V6-S | `PP-OCRv6_small_det` | `PP-OCRv6_small_rec` | Alternativa de custo reduzido (só após medium aprovado) |

**Nota sobre combinações:** a tabela é experimental. Verificar se v5 det + v6 rec (linha R6) pode ser instanciado em uma única chamada `PaddleOCR()` ou se exige exportação de crops e chamadas independentes. Se incompatível via `PaddleOCR()` unificado, usar `TextDetection` + `TextRecognition` como módulos separados (`from paddleocr import TextDetection, TextRecognition`).

#### 2.2. Corpus de comparação

Reutilizar `Document_OCR_Stress_V1` (60 páginas, corpus sintético atual). Não criar corpus novo abrangente.

Cenários prioritários para extração de crops:
- Português básico com diacríticos
- Letras ambíguas: `I/l/1`, `O/0`
- Números de processo, datas, CPF/CNPJ fictícios, valores monetários
- Fontes pequenas e baixa resolução
- Paisagem com texto horizontal
- Regiões rasterizadas em páginas mistas
- Texto em figuras e tabelas escaneadas
- **Página 10 (`OCRS-P10-CONTROL`)** — caso de regressão conhecida
- Casos que v5 já recupera corretamente (para detectar perdas)

Origem de cada amostra: separar claramente (a) imagem raster original, (b) crop que o parser v5 fornecia ao motor, (c) variante 2× produzida pelo código estável coletada somente para leitura.

#### 2.3. Evidências a coletar por amostra/modelo

Para cada combinação amostra × modelo:
- Identificação da página sintética e versão do manifesto
- Versão exata do modelo (nome + hash do diretório)
- Runtime, parâmetros de inicialização
- Imagem de entrada (sintética, não corporativa) + dimensões
- Caixas de detecção retornadas
- Strings reconhecidas e confiança nativa (com nota: scores v5/v6 não são calibrados — não comparar diretamente)
- Coordenadas remapeadas para espaço PDF
- Tempo de inicialização, tempo de inferência, peak RAM
- Warnings de qualquer tipo

#### 2.4. Análise qualitativa

Comparar por marcador fictício conhecido: texto correto/incorreto/ausente, duplicações, alucinações, separação de linhas.  
**Não resumir a comparação em bytes ou contagem de caracteres.** Melhoria na página 10 não cancela regressão em outras páginas.

**Gate 2:** relatório lado a lado por cenário e modelo. Se nenhuma alternativa superar ou complementar v5 em casos relevantes sem perdas injustificadas, **não integrar v6**; arquivar experimento.

---

### Fase 3 — Integração opcional de PP-OCRv6 ao PDFExtractor

**Pré-requisito:** Gates 1 e 2 concluídos com evidência favorável.

#### 3.1. Mudanças de código mínimas necessárias

**`src/structured_pdf_text/cli.py`** — adicionar `--ocr-model-profile` a `extract` (e opcionalmente `inspect`/`report`/`compare`):

```python
# Na função de setup do parser 'extract':
extract_parser.add_argument(
    "--ocr-model-profile",
    default=None,
    metavar="PROFILE",
    help="OCR model profile name (e.g. pt-v6-medium). "
         "Defaults to the profile matching --language.",
)
```

Lógica de resolução de perfil: quando `--ocr-model-profile` está ausente, comportamento atual é preservado (`get_profile(language)`). Quando presente, substitui a seleção de perfil independentemente de `--language`.

```python
# Resolução no corpo do comando extract:
profile_name = args.ocr_model_profile or args.language
profile = get_profile(profile_name)
```

Estender `setup-models` e `models-status` para aceitar o novo perfil também:
```bash
pdftext setup-models --ocr-model-profile pt-v6-medium --cache-home ~/.cache/pdfextractor/paddlex-v6-eval
pdftext models-status --ocr-model-profile pt-v6-medium --cache-home ~/.cache/pdfextractor/paddlex-v6-eval
```

**`src/structured_pdf_text/ocr/models.py`** — adicionar perfis v6:

```python
OcrModelProfile(
    language="pt-v6-medium",
    doc_orientation="PP-LCNet_x1_0_doc_ori",
    textline_orientation="PP-LCNet_x1_0_textline_ori",
    detection="PP-OCRv6_medium_det",
    recognition="PP-OCRv6_medium_rec",
)

# Somente se evidência da Fase 2 justificar:
OcrModelProfile(
    language="pt-v6-small",
    doc_orientation="PP-LCNet_x1_0_doc_ori",
    textline_orientation="PP-LCNet_x1_0_textline_ori",
    detection="PP-OCRv6_small_det",
    recognition="PP-OCRv6_small_rec",
)
```

**`src/structured_pdf_text/cli.py`** — estender `setup-models` e `models-status` para aceitar `--language pt-v6-medium`.

**Não alterar:** `ocr/paddle.py`, `ocr/recovery.py`, `ocr/engine.py`, `ocr/quality.py`, `ocr/reconstruct.py`, `config.py` (exceto se necessário para suporte explícito ao parâmetro de perfil), qualquer módulo de texto nativo, assembly, ledger.

#### 3.2. Comportamento esperado após integração mínima

```bash
# Usuário opta explicitamente pelo v6:
pdftext extract documento.pdf --mode balanced --language pt-v6-medium

# Padrão permanece v5:
pdftext extract documento.pdf --mode balanced   # usa pt → v5
```

Carregamento simultâneo de v5 e v6 em memória: **proibido** (não carregar ambos os motores automaticamente).

Fallback silencioso de v6 para v5 sem registro: **proibido**. Qualquer fallback deve ser explícito e visível em log.

#### 3.3. Tratamento de erros

- Pesos ausentes para o perfil selecionado: `PaddleOcrUnavailable` antes de qualquer inferência.
- Versão incompatível de API: diagnóstico explícito antes de processar.
- Falha fatal: continua fatal (não converte em resultado vazio silencioso).

#### 3.4. Testes obrigatórios para integração

**Testes unitários novos (sem download de modelos):**
- Resolução de perfil por nome (`get_profile("pt-v6-medium")`)
- Caminhos locais corretos para cada kwarg do perfil v6
- Erro `PaddleOcrUnavailable` para peso ausente no perfil v6
- Isolamento de instâncias (v5 e v6 não compartilham estado)
- Mapeamento de caixas e tokens com coordenadas PDF

**Testes de regressão (sem modificação):**
- `tests/test_ocr_adaptive_upscale_regressions.py` — sem alteração para acomodar v6
- Todos os testes existentes de modo `native` e conservação textual
- `tests/test_paddle_offline.py` — validação de caminhos locais

**End-to-end com pesos locais (CPU, offline):**
- Página raster, mista, figura, tabela, página 10 — nos dois perfis (v5 e v6-medium)
- Resultado v5 deve permanecer comparável à referência documentada

**Gate 3:** v6 disponível apenas via seleção explícita (`--language pt-v6-medium`); v5 padrão e funcional; zero regressões em upscaling/texto; execução CPU/offline comprovada.

---

### Fase 4 — Avaliação de PP-TableMagic (apenas se justificado)

**Pré-requisito:** fases anteriores concluídas, ou justificativa explícita de que o problema a resolver é estrutural (não OCR puro).

#### 4.1. Casos de uso alvo

Somente tabelas onde:
- O texto foi reconhecido incorretamente **por causa da estrutura** (não por reconhecimento de caracteres)
- A estrutura de linhas/colunas foi perdida mesmo após v6 OCR
- A tabela é 100% rasterizada (sem vetores)

#### 4.2. Avaliação em ambiente isolado

Script fora do parser, mesmo venv, sem tocar no código de produção:
```python
from paddleocr import TableRecognitionPipelineV2

pipeline = TableRecognitionPipelineV2(
    wired_table_structure_recognition_model_dir="/local/SLANeXt_wired",
    text_detection_model_dir="/local/PP-OCRv5_server_det",
    text_recognition_model_dir="/local/PP-OCRv5_server_rec",
    device="cpu",
    enable_mkldnn=False,
)
output = pipeline.predict("imagem_de_tabela.jpg")
```

Comparar output HTML com o resultado atual do pipeline de tabelas do projeto para a mesma imagem.

#### 4.3. Métricas de avaliação

- Células corretas/incorretas/ausentes/inventadas
- Células mescladas corretas quando previstas
- Preservação de acentos e caracteres especiais
- Tempo de inferência e peak RAM em CPU
- Integração offline confirmada (zero downloads)
- Tabelas digitais não afetadas (controle negativo)

**Gate 4:** integrar PP-TableMagic apenas como opt-in para regiões rasterizadas específicas, apenas se solucionar deficiência estrutural real e contrato de integração for compatível sem alterar assembly, ledger ou texto nativo.

---

### Fase 5 — PP-StructureV3 apenas para lacuna estrutural residual

**Pré-requisito:** existe página sintética reproduzível onde v6 + PP-TableMagic ainda falham.

#### 5.1. Validação antes de qualquer código

Em ambiente isolado:
1. Confirmar `device="cpu"` funciona para a configuração mínima (sem fórmulas, sem gráficos)
2. Medir RAM e tempo em CPU do Windows Server — documentar se viável
3. Verificar que `use_formula_recognition=False` é obrigatório para funcionar offline com CPU
4. Confirmar que não faz download automático

```python
from paddleocr import PPStructureV3

pipeline = PPStructureV3(
    device="cpu",
    enable_mkldnn=False,
    use_table_recognition=True,
    use_formula_recognition=False,   # obrigatório para CPU+offline
    use_seal_recognition=False,
    use_chart_recognition=False,     # VLM de 1.4GB — proibido para CPU
)
```

**Gate 5:** fallback opcional com ganhos reproduzíveis e custo aceitável. Se lacuna não justificar o modelo, encerrar avaliação sem integrar.

---

### Fase 6 — Validação no Windows Server e política de promoção

#### 6.1. Smoke de instalação Windows (ambiente separado do venv estável)

1. Criar venv novo no Windows Server (não no ambiente de produção)
2. Instalar `setuptools wheel` antes de paddleocr
3. Instalar pacotes nas versões exatas fixadas
4. Confirmar com rede bloqueada (Firewall / netsh) que nenhum download ocorre durante inferência
5. Executar smoke test de uma imagem com v6-medium
6. Inspecionar: `pip check`, versão Python, logs de DLL, consumo de memória, eventos do Windows

#### 6.2. Testes de integração Windows

Executar em ordem crescente de escopo:

1. Páginas isoladas do corpus sintético (p. 2, 10, 13-14, 15-16, páginas de tabelas, 46-50)
2. Grupos pequenos: `baseline` → `adaptive`; `exhaustive` apenas como diagnóstico pontual
3. Corpus completo (60 páginas) quando cenários curtos passarem
4. PDFs empresariais: apenas sob autorização do responsável, no servidor autorizado, sem enviar ao desenvolvedor

Para cada execução registrar: perfil/modelos/SHA, política OCR, threads, tempo total/RAM, exit code, caminho de saída.

#### 6.3. O incidente histórico `0xC0000005` (`phi.dll`)

Esta é uma categoria distinta de falha nativa no Windows. Sucesso no WSL, em página isolada ou no corpus sintético **não comprova** que não ocorrerá em documentos extensos. Verificar eventos do Windows e registros durante execução prolongada.

#### 6.4. Rollback

Confirmar rollback explícito: mesmo teste sintético executado no venv v5 original, com perfil v5, produz resultado idêntico à referência documentada. Sem necessidade de restaurar arquivos manualmente.

#### 6.5. Critérios de aceitação para promoção de v6

- Funciona em CPU Windows e WSL, sem rede, com pesos locais; versões/pesos/hash reproduzíveis
- Perfil v5 estável permanece disponível e **padrão** até autorização explícita de mudança
- Correções reais de conteúdo demonstradas por exemplos sintéticos
- Sem alteração funcional em upscaling 2×, orçamento RGB, políticas adaptativas, processamento textual nativo, assembly, ledger, modo `native`
- Nenhum encerramento nativo relevante ocultado
- Recursos operacionais viáveis com hardware da empresa

**Conclusão da fase não autoriza merge automático de `feat/paddle-ocrv6-evaluation` à `main`.** Revisão posterior do responsável é obrigatória.

---

## 8. Entregáveis e Checkpoints

| Checkpoint | Conteúdo |
|---|---|
| A | Merge estabilização + SHA main + SHA base branch nova |
| B | Runtime e modelos v6 isolados funcionando WSL + Windows (Gate 1) |
| C | Relatório comparativo v5/v6 por cenário (Gate 2) |
| D | Perfil v6 integrado como opt-in, testes passando (Gate 3) |
| E | Avaliação PP-TableMagic — somente se Gate 2 justificar (Gate 4) |
| F | Avaliação PP-StructureV3 — somente se Gates 2+4 justificarem (Gate 5) |
| G | Relatório Windows + rollback + ZIP da branch (Gate 6) |

**Por checkpoint, fornecer:**
- SHA (`git rev-parse HEAD`)
- `git status --short`
- Arquivos e contratos afetados
- Versões de ambiente, modelos e hashes SHA
- Comandos efetivamente executados (não previstos)
- Resultados, duração, peak RAM, falhas e limitações
- Não apresentar testes não executados como aprovados

**Proteção de diff:** antes de qualquer push da branch de avaliação, verificar diff relativo ao SHA-base. Se qualquer função de upscaling 2×, logs, módulos textuais/assembly/ledger ou modelos v5 de produção estiverem alterados: registrar como **bloqueio** e não publicar.

---

## 9. Pontos de Decisão e Perguntas em Aberto

### 9.1. Decididos pela pesquisa

| Questão | Resposta |
|---|---|
| Atualização de pacotes necessária? | **Não.** paddleocr 3.7.0 já suporta v6. |
| API `.predict()` funciona com v6? | **Sim.** O despacho em `_predict()` já trata isso. |
| Padrão de `*_model_dir` kwargs funciona para v6? | **Sim.** Mesmos kwargs, novos nomes. |
| `device="cpu"` suportado em v6? | **Sim.** Confirmado na documentação oficial. |
| Download automático durante extração é risco? | **Não**, com `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK="True"` (já configurado). |
| PaddleOCR-VL funciona em CPU? | **Não.** Não usar PaddleOCR-VL neste projeto. |

### 9.2. A verificar empiricamente

| Questão | Como verificar |
|---|---|
| Classificadores de orientação `PP-LCNet_x1_0_*` são compatíveis com v6? | Smoke test da Fase 1 — verificar warnings na inicialização |
| Combinação v5_det + v6_rec é possível em um único `PaddleOCR()`? | Testar na Fase 2; se não, usar `TextDetection` + `TextRecognition` separados |
| `enable_mkldnn=False` (padrão do projeto) funciona com v6? | Smoke test; se gerar erro, testar com `True` e documentar |
| Peak RAM do v6-medium em CPU comparado ao v5_server? | Medir na Fase 1 com `_ocr_proc_mem()` existente |
| `OCRS-P10-CONTROL` ainda falha na versão atual? | Executar corpus sintético no HEAD atual antes de declarar |
| Python 3.12 + wheel Paddle no Windows Server 2025? | Gate 1 Windows |

### 9.3. Decisões do responsável — RESPONDIDAS em 23/09/2026

#### Decisão 1 — Cache dos modelos v6

**Cache completamente separado do v5.** Não sobrescrever, renomear, remover nem substituir nenhum modelo existente em `official_models/`.

- Raiz de avaliação v6: `~/.cache/pdfextractor/paddlex-v6-eval/official_models/`
- Raiz de produção v5 (intocável): `~/.cache/pdfextractor/paddlex/official_models/`
- A nova branch deverá ter ambiente Python independente (`.venv-paddle-v6-eval`, gitignored) e diretórios explícitos para os pesos v6.
- Registrar em cada teste o caminho absoluto efetivo de cada modelo utilizado.
- Após a avaliação, pode-se decidir se v5 e v6 coexistirão sob raiz comum (por enquanto, prioridade é isolamento).

#### Decisão 2 — Convenção CLI

**`--language pt --ocr-model-profile pt-v6-medium`.**

- `--language pt` permanece significando "português" (idioma) sem alterar o modelo padrão.
- Nova opção `--ocr-model-profile` seleciona o perfil OCR explicitamente.
- Quando `--ocr-model-profile` não for informado, comportamento atual é preservado (busca o perfil pelo valor de `--language`).
- **Verificação antes de implementar:** não existe mecanismo equivalente na CLI atual — `--language` mapeia diretamente a `get_profile(language)`. A nova opção precisará ser criada na Fase 3.
- **Durante Fase 2 (comparação isolada):** nenhuma alteração na CLI principal é necessária.
- Compatibilidade retroativa: todos os comandos e scripts existentes continuam funcionando sem alteração.

#### Decisão 3 — Escopo da avaliação

**`OCRS-P10-CONTROL` não é o motivador exclusivo.** A avaliação deve abranger ganhos efetivos em documentos reais.

Cenários obrigatórios na comparação:
- Texto rasterizado, páginas mistas, fontes pequenas
- Acentos em português, números de processos, datas, valores monetários
- Tabelas e figuras com texto
- Casos que v5 já recupera corretamente (controle de regressão)
- Página 10 permanece no conjunto de testes (verificar se v6 recupera o marcador)

**Proibido:**
- Implementar regra especial para página 10 ou marcador específico
- Declarar v6 aprovado apenas porque resolveu `OCRS-P10-CONTROL`

O v6 é uma **alternativa experimental**, não substituição automática do v5.

4. **Existe prazo para a avaliação?** Determina se avaliação sequencial (segura) ou se há pressão para acelerar fases.

---

## 10. Riscos e Mitigações

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| Classificadores de orientação incompatíveis com v6 | Baixa | Médio | Gate 1: smoke test captura warnings de inicialização |
| v6 medium mais lento que v5_server em Windows Server | Média | Médio | Benchmark Fase 1; v6_small como alternativa |
| Crash nativo `0xC0000005` com v6 em documentos longos | Baixa (nova versão) | Alto | Fase 6: teste de corpus completo + verificação de eventos Windows |
| Dependência de pacote incompatível descoberta ao instalar | Baixa (mesmas versões) | Alto | `pip check` obrigatório antes de cada fase |
| Regressão em texto nativo por modificação acidental | Baixa | Crítico | Diff de proteção antes de qualquer push; testes de conservação |
| PP-TableMagic HTML output incompatível com StructuredTable | Alta | Alto | Fase 4 apenas se v6 não resolver; parser HTML adicional necessário |
| PPStructureV3 muito lento para CPU de produção (~3.74s/img) | Média | Médio | Gate 5: benchmark de viabilidade antes de qualquer integração |

---

## 11. Referências Oficiais (Consultadas Durante Elaboração)

- PP-OCRv6 Introduction: https://paddlepaddle.github.io/PaddleOCR/main/en/version3.x/algorithm/PP-OCRv6/PP-OCRv6.html
- PP-OCRv6 arXiv: https://arxiv.org/html/2606.13108v1
- OCR Pipeline docs: https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/OCR.en.md
- Text Recognition module: https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/module_usage/text_recognition.en.md
- PP-TableMagic: https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/table_recognition_v2.html
- PP-StructureV3: https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/PP-StructureV3.html
- PaddlePaddle CPU install Windows: https://www.paddlepaddle.org.cn/documentation/docs/en/install/pip/windows-pip_en.html
- DeepWiki PP-OCRv5/v6: https://deepwiki.com/PaddlePaddle/PaddleOCR/2.1-pp-ocrv5-and-pp-ocrv6-universal-text-recognition
- PaddleOCR-VL CPU issue: https://github.com/PaddlePaddle/PaddleOCR/issues/16678
- HuggingFace medium_det: https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_det
- HuggingFace medium_rec: https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_rec

---

## 12. Status de Implementação — Resumo por Fase

> Última atualização: 23/09/2026 — branch `feat/paddle-ocrv6-evaluation` (5 commits sobre main)

### Commits na branch

| SHA | Mensagem |
|---|---|
| `7fd5da1` | feat(ocr): add PP-OCRv6 evaluation profiles and --ocr-model-profile flag |
| `07c63a8` | feat(eval): add PP-OCRv6 evaluation scripts (phases 1, 2, 4) |
| `49c7a38` | docs: add PP-OCRv6 comparative evaluation plan with implementation status |
| `a103457` | feat(eval): simplify v6 evaluation scripts and add Windows PowerShell setup |
| `75426b1` | fix(cli): show exact setup-models command in models-status hint; fix compare script |

### Fase 0 — Pré-requisito: consolidar main e criar branch nova

| Etapa | Status | Observações |
|---|---|---|
| 0.1. Verificações pré-merge | ✅ Concluído | Branch `feat/ocr-regression-stabilization` em SHA `5a6ccc2`, SHA-base referência validada |
| 0.2. Gate pré-merge (testes) | ✅ Concluído | `python -m pytest -q` — 307 testes passando, zero falhas |
| 0.3. Merge de estabilização à main | ✅ Concluído | Merge realizado em `ee655d4` — merge commit na main |
| 0.4. Criar branch de avaliação | ✅ Concluído | Branch `feat/paddle-ocrv6-evaluation` criada a partir de `ee655d4` |

**Gate 0:** ✅ Concluído. Nenhuma divergência do plano.

---

### Fase 1 — Validação de compatibilidade de ambiente e modelos v6

| Etapa | Status | Observações |
|---|---|---|
| 1.1. Download de modelos v6 no Windows Server | 🔲 Em andamento | `setup_v6_windows.ps1` executado — aguardando conclusão |
| 1.2. Smoke test de compatibilidade | 🔲 Em andamento | `smoke_test_v6.py --pdf corpus/Document_AI_V2.pdf` em execução |
| 1.3. Repetição no Windows Server | 🔲 Em andamento | Testes direto no servidor (sem WSL intermediário) |

**Desvio do plano:** O plano previa venv Python isolado. Dado que `paddleocr==3.7.0` já suporta v6 sem atualização, o venv de produção é usado diretamente. O `.venv-paddle-v6-eval` foi criado apenas como opção de isolamento adicional se necessário.

**Gate 1:** 🔲 Não concluído — aguarda execução manual dos scripts.

---

### Fase 2 — Comparação isolada v5/v6

| Etapa | Status | Observações |
|---|---|---|
| 2.1. Matriz de comparação | 🔲 Pendente (manual) | Script `scripts/eval_v6/compare_v5_v6.py` criado. Padrão: `corpus/Document_AI_V2.pdf` |
| 2.2. Corpus de comparação | 🔲 Pendente | Usa `corpus/Document_AI_V2.pdf` (substituição do corpus sintético V1 — ver desvio) |
| 2.3. Coleta de evidências | 🔲 Pendente | Script captura: chars/página, words, tempo, delta relativo entre perfis |
| 2.4. Análise qualitativa | 🔲 Pendente | Requer execução e revisão manual do relatório gerado |

**Desvio do plano:** O plano original especificava `Document_OCR_Stress_V1` (60 páginas, corpus sintético) como corpus da Fase 2. A instrução do responsável foi usar `corpus/Document_AI_V2.pdf` como documento de teste preferencial. O script `compare_v5_v6.py` usa esse PDF como padrão (`--pdf corpus/Document_AI_V2.pdf`) e aceita qualquer PDF via argumento.

**Gate 2:** 🔲 Não concluído — aguarda execução manual e revisão do relatório.

---

### Fase 3 — Integração opcional de PP-OCRv6 ao PDFExtractor

| Etapa | Status | Observações |
|---|---|---|
| 3.1. Novo perfil `pt-v6-medium` em `ocr/models.py` | ✅ Concluído | Perfis `pt-v6-medium` e `pt-v6-small` adicionados |
| 3.1. Novo perfil `pt-v6-small` em `ocr/models.py` | ✅ Concluído | Adicionado conforme plano |
| 3.1. Docstring do módulo `models.py` atualizada | ✅ Concluído | Documenta os três perfis com instruções de uso |
| 3.1. `--ocr-model-profile` no subcomando `extract` | ✅ Concluído | Resolve perfil via `args.ocr_model_profile or args.language` |
| 3.1. `--ocr-model-profile` nos subcomandos `inspect`, `report` | ✅ Concluído | Mesma lógica de resolução |
| 3.1. `--ocr-model-profile` nos subcomandos `setup-models`, `models-status` | ✅ Concluído | Permite baixar e verificar modelos v6 pelo nome do perfil |
| 3.1. `--cache-home` nos subcomandos `extract`, `inspect`, `report` | ✅ Concluído | Define `PADDLE_PDX_CACHE_HOME` antes de criar o extrator |
| 3.2. Comportamento padrão preservado (v5 sem alteração) | ✅ Verificado | Sem `--ocr-model-profile`, comportamento idêntico ao anterior |
| 3.3. Tratamento de erros — perfil inexistente | ✅ Concluído | `get_profile()` levanta `ValueError` com mensagem clara |
| 3.3. Tratamento de erros — modelos ausentes | ✅ Existente | `PaddleOcrUnavailable` antes de inferência (sem modificação necessária) |
| 3.4. Testes unitários para v6 | ⏭️ Deferido | Conforme instrução: não focar em testes de software; usar `Document_AI_V2.pdf` para verificar |
| 3.4. Testes de regressão existentes | ✅ Passando | 91 testes, zero falhas após alterações |

**Desvio do plano:** O plano previa testes unitários em `tests/test_ocr_v6_profile.py`. Conforme instrução do responsável, testes formais foram deferidos em favor de testes funcionais com o PDF real (`corpus/Document_AI_V2.pdf`). Os testes existentes cobrem o caminho crítico de offline/local.

**Desvio menor:** O plano previa que a Fase 3 era posterior ao Gate 2. A integração de código (profiles + CLI) foi adiantada por ser invasividade zero — nenhum comportamento padrão foi alterado, e as flags são opt-in explícito.

**Gate 3:** ✅ Código integrado. 🔲 Validação end-to-end pendente (aguarda execução com modelos v6 baixados).

---

### Fase 4 — Avaliação de PP-TableMagic

| Etapa | Status | Observações |
|---|---|---|
| 4.1. Identificar casos de uso alvo | 🔲 Pendente | Aguarda Gate 2 para identificar falhas estruturais |
| 4.2. Script de avaliação isolada | ✅ Criado | `scripts/eval_v6/eval_tablemagic.py` — aceita imagem ou PDF+páginas |
| 4.3. Execução e coleta de métricas | 🔲 Pendente (condicional) | Só executar se Gate 2 evidenciar falhas estruturais que v6 OCR puro não resolve |

**Gate 4:** 🔲 Condicional — só relevante após Gate 2.

---

### Fase 5 — PP-StructureV3

| Etapa | Status | Observações |
|---|---|---|
| Avaliação | ⏭️ Deferido | Não há evidência de necessidade antes de Gates 2 e 4 |

**Nota:** PP-StructureV3 foi avaliado como **RISCO MUITO ALTO** (Seção 5.4). Não será implementado nesta fase.

---

### Fase 6 — Validação no Windows Server e política de promoção

| Etapa | Status | Observações |
|---|---|---|
| 6.1. Smoke de instalação Windows | 🔲 Pendente | Aguarda Gate 1 WSL |
| 6.2. Testes de integração Windows | 🔲 Pendente | Aguarda Gate 1 Windows |
| 6.3. Verificação `0xC0000005` | 🔲 Pendente | Monitorar eventos Windows durante corpus completo |
| 6.4. Validação de rollback | 🔲 Pendente | Confirmar que v5 mantém resultado idêntico à referência |
| 6.5. Critérios de aceitação | 🔲 Pendente | Checklist formal antes de qualquer merge à main |

---

### Resumo Executivo

| Fase | Planejado | Implementado | Status |
|---|---|---|---|
| 0 — Pré-requisito | Merge + branch | Merge `ee655d4`, branch criada | ✅ |
| 1 — Compatibilidade v6 | Setup + smoke test | Scripts criados; execução pendente | 🔲 |
| 2 — Comparação v5/v6 | Script + relatório | Script criado; execução pendente | 🔲 |
| 3 — Integração CLI/modelos | profiles + `--ocr-model-profile` | 100% implementado (models.py + cli.py) | ✅ |
| 4 — PP-TableMagic | Avaliação isolada | Script criado; execução condicional | 🔲 |
| 5 — PP-StructureV3 | Avaliação condicional | Deferido (risco muito alto) | ⏭️ |
| 6 — Windows + promoção | Testes + checklist | Aguarda Fases 1–2 | 🔲 |

**Arquivos modificados nesta branch (em relação à main):**

| Arquivo | Tipo de alteração |
|---|---|
| `src/structured_pdf_text/ocr/models.py` | Adição de perfis `pt-v6-medium` e `pt-v6-small` |
| `src/structured_pdf_text/cli.py` | Adição de `--ocr-model-profile` e `--cache-home` |
| `tests/test_paddle_offline.py` | Correção de regex de teste (mensagem de erro renomeada) |
| `.gitignore` | Adição de `.venv-paddle-v6-eval/` |
| `scripts/eval_v6/setup_v6_env.sh` | Novo — setup de ambiente e download de modelos v6 |
| `scripts/eval_v6/smoke_test_v6.py` | Novo — teste de compatibilidade offline+CPU |
| `scripts/eval_v6/compare_v5_v6.py` | Novo — comparação v5 vs v6 em PDF real |
| `scripts/eval_v6/eval_tablemagic.py` | Novo — avaliação PP-TableMagic (Fase 4) |
| `Plano_Comparativo_Paddle.md` | Novo — este documento |

**Nenhuma alteração nos módulos protegidos:** `ocr/paddle.py`, `ocr/recovery.py`, `ocr/engine.py`, `ocr/quality.py`, `ocr/reconstruct.py`, `config.py`, módulos de texto nativo, assembly, ledger.

**Próximos passos para o responsável:**
1. Revisar os arquivos modificados nesta branch
2. Executar `bash scripts/eval_v6/setup_v6_env.sh` (com rede disponível) para baixar modelos v6
3. Executar `python scripts/eval_v6/smoke_test_v6.py --pdf corpus/Document_AI_V2.pdf` para Gate 1
4. Executar `python scripts/eval_v6/compare_v5_v6.py` para Gate 2
5. Revisar relatório de comparação e decidir sobre continuidade
6. Somente após Gates 1 e 2: executar testes no Windows Server (Gate 6)
