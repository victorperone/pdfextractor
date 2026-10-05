# Comparativo OCR — pdfextractor

## Status de Implementação

| # | Etapa | Entregáveis | Status |
|---|---|---|---|
| 0 | Freeze baseline + golden output | git sha, pip freeze, golden baseline | ✅ |
| 1 | Contrato | `contracts.py`, `factory.py`, `registry.py`, fake backend | ✅ |
| 2 | Backend Paddle | `backends/paddle.py`, diff zero no corpus | ✅ |
| 3 | Benchmark RAW | `scripts/benchmark_raw_ocr.py`, manifesto JSON | ✅ |
| 4 | RapidOCR ONNX | `backends/rapidocr.py` (onnxruntime), 32/32 OK | ✅ |
| 5 | RapidOCR OpenVINO | `backends/rapidocr.py` (openvino), 32/32 OK | ✅ |
| 6 | Tesseract 5 | `backends/tesseract.py`, PSM 3 + OEM 1 | ✅ |
| 7 | EasyOCR | `backends/easyocr.py`, PyTorch CPU | ✅ |
| 8 | Benchmark E2E completo | `evaluate_e2e.py`, `compute_metrics.py`, `compare_engines.py` — 5 engines, 144/144 páginas | ✅ |

---

## Default Engine Decision (2026-10-05)

**Chosen default: EasyOCR** (`ocr_engine: str = "easyocr"` in `ExtractorConfig`).

Decision is based on the Phase 8 full-corpus benchmark (`Comparativo_Backends_OCR_Fase8.md`,
2026-10-04, V3: 144 pages, V4: 224 pages):

| Criterion | EasyOCR | PaddleOCR | Winner |
|---|---|---|---|
| Average CER (V3+V4) | **0.3892** | 0.4270 | EasyOCR |
| Header leakage avg | **0.0000** | 0.4412 | EasyOCR |
| Duplicate content avg | **0.0245** | 0.1819 | EasyOCR |
| Currency F1 avg | 0.7297 | **0.8839** | Paddle |
| Numeric F1 avg | 0.9121 | **0.9778** | Paddle |
| Identifier Precision avg | 0.5333 | **1.0000** | Paddle |

**When to use PaddleOCR instead**: set `ocr_engine="paddle"` explicitly when
financial-data precision is critical — Currency F1, Numeric F1, and Identifier
Precision are all substantially better with Paddle. EasyOCR's Identifier
Precision collapses in V4 (0.0667), making it unsuitable for CPF/CNPJ/process
number extraction.

**Open known issue affecting RapidOCR (CF-2)**: `paddle2onnx` DLL incompatible
(0xC0000139) on Windows — RapidOCR still uses the bundled PP-OCRv4 Chinese
model instead of PP-OCRv6. This does not affect EasyOCR or PaddleOCR. CF-2
remains open and is documented under "Correções Futuras" below.

**EasyOCR pending improvements**: CLAHE preprocessing (item 9.3) and model
fine-tuning (item 9.6) remain unimplemented pending A/B validation. All other
Phase 9 EasyOCR optimizations are implemented. See "Fase 9 — Otimizações
EasyOCR" below for details.

---

## Resultados de Performance (pp. 72–103, 32 páginas scan)

| Engine | Tempo total | s/pág | Páginas OK | CER médio |
|---|---|---|---|---|
| PaddleOCR (sem oneDNN — CF-1) | 3018 s | ~94 s | 32/32 | 0.4106 (parcial) |
| RapidOCR ONNX (PP-OCRv4 — CF-2) | 88.2 s | ~2.76 s | 32/32 | ver resultados completos da Fase 8 abaixo |
| RapidOCR OpenVINO (PP-OCRv4 — CF-2) | 20.1 s | ~0.63 s | 32/32 | — |
| Tesseract 5 (por, PSM 3, OEM 1) | 18.5 s | ~0.58 s | 32/32 | — |
| EasyOCR (latin_g2, CPU) | 173.1 s | ~5.4 s | 32/32 | — |

---

## Correções Futuras

| ID | Descrição | Status |
|---|---|---|
| CF-1 | oneDNN desabilitado por padrão em CPU (Paddle 3.x PIR/oneDNN); opt-in via `PADDLE_ENABLE_MKLDNN=1` | ✅ |
| CF-2 | `paddle2onnx` DLL incompatível (0xC0000139) — RapidOCR usa PP-OCRv4 embutido em vez de PP-OCRv6 | ⬜ |
| CF-3 | numpy conflict: EasyOCR atualiza para 2.5.x, quebra openvino<2.1.0 — fix: `pip install "numpy==2.0.2"` | ✅ |
| CF-4 | **Paddle crash ao coexistir com EasyOCR no mesmo venv** — `paddleocr → modelscope → torch` causa DLL 0xC0000139 no Windows. Fix definitivo: `PaddleOCRBackend` detecta `torch` instalado via `importlib.util.find_spec` e entra em **subprocess mode** — todas as chamadas OCR são roteadas para um worker de longa duração (`_paddle_subprocess_worker.py`) que roda em processo isolado sem DLLs do torch. Transparente ao usuário: mesmo venv, sem configuração extra. | ✅ |

---

## Fase 8 — Benchmark E2E com Métricas Multidimensionais

### Objetivo

Avaliar a qualidade de extração de cada engine no **documento completo** via pipeline E2E, comparando contra ground truth manual. Separar erros de OCR de erros de reconstrução Markdown. Identificar exatamente onde cada erro ocorre.

### Corpus e Ground Truth

- Arquivo: `corpus/Document_AI_V3.pdf` — **todas as páginas** (nativas + scan)
- Ground truth: `corpus/Document_AI_V3.md` e `corpus/Document_AI_V3_MANIFESTO.json`; o Markdown é a referência textual e o manifesto fornece metadados por página
- O pipeline E2E aplica native-first; métricas avaliam a saída final completa

### Scripts utilizados

| Arquivo | Responsabilidade |
|---|---|
| `scripts/evaluate_e2e.py` | Roda o pipeline E2E para uma engine no documento completo; salva manifesto + Markdown de saída |
| `scripts/compute_metrics.py` | Recebe ground truth + saída; calcula todos os grupos de métricas; gera JSON + relatório de erros por página |
| `scripts/compare_engines.py` | Agrega JSONs de métricas de todas as engines; gera tabela comparativa |

### Métricas — Grupo 1: Texto

| Métrica | Descrição |
|---|---|
| CER Raw | Comparação exata — caracteres, espaços, quebras, Markdown, pontuação |
| CER Normalized | Após CRLF→LF, Unicode NFC, remoção de espaços finais |
| CER Text Only | Markdown removido antes de comparar (separa erro OCR de erro Markdown) |
| WER | Word Error Rate |
| Word Accuracy | 1 − WER |
| Substitution Rate | Taxa de caracteres/palavras substituídos |
| Deletion Rate | Taxa de conteúdo ausente |
| Insertion Rate | Taxa de conteúdo inserido indevidamente |
| Omission Rate | Conteúdo omitido por linha/bloco |

### Métricas — Grupo 2: Estrutura Markdown

| Métrica | Descrição |
|---|---|
| Block F1 | Precision/Recall/F1 por tipo de bloco (heading, paragraph, table, list…) |
| Heading F1 | Detecção de headings |
| Heading Level Accuracy | Nível preservado (#, ##, ###) |
| Heading Text CER | CER só no conteúdo textual dos headings |
| Paragraph Boundary F1 | Limites de parágrafo corretos |
| List Detection F1 | Detecção de listas |
| List Item Accuracy | Itens corretos |
| List Nesting Accuracy | Hierarquia de listas preservada |
| Markdown AST Similarity | Similaridade da árvore sintática completa |

### Métricas — Grupo 3: Tabelas

| Métrica | Descrição |
|---|---|
| Table F1 | Precision/Recall/F1 de detecção de tabelas |
| Row F1 | Linhas corretas |
| Column F1 | Colunas corretas |
| Table Dimension Accuracy | Dimensão estrutural (N linhas × M colunas) |
| Cell Exact Match | Célula com conteúdo idêntico à referência (após normalização) |
| Cell CER | CER por célula |
| Cell Alignment Accuracy | Conteúdo na posição [linha, coluna] correta |
| Table Structure Similarity | Estrutura completa da tabela |

### Métricas — Grupo 4: Ordem e Integridade

| Métrica | Descrição |
|---|---|
| Reading Order Accuracy | Ordem de leitura do documento completo |
| Block Order Accuracy | Blocos na sequência correta |
| Duplicate Content Rate | Parágrafos/blocos duplicados |
| Duplicate Block Rate | Blocos lidos duas vezes |
| Header Leakage Rate | Cabeçalhos de página indevidamente incluídos |
| Footer Leakage Rate | Rodapés indevidamente incluídos |
| Page Number Leakage Rate | Números de página indevidamente incluídos |
| Failure Rate | Páginas não processadas / exceções |
| Invalid Markdown Rate | Markdown mal-formado (tabelas quebradas, fences não fechados) |

### Métricas — Grupo 5: Dados Críticos

| Métrica | Descrição |
|---|---|
| Numeric Exact Match | Números (inteiros, decimais, percentuais, separadores) |
| Date Exact Match | Datas |
| Currency Exact Match | Valores monetários (R$) |
| Identifier Exact Match | CPF, CNPJ, processos, códigos, protocolos |

### Saídas esperadas

```
output/fase8/
├── manifesto_{engine}.json      — run identity, git sha, engine, por-page results
├── metrics_{engine}.json        — todas as métricas dos 5 grupos
├── errors_{engine}.md           — lista de erros por página (tipo, referência, extração, posição)
└── comparison_table.md          — tabela comparativa de todas as engines
```

### Resultado completo obtido no WSL (2026-10-01)

- Documento: `corpus/Document_AI_V3.pdf`, 144 páginas.
- Referências: `corpus/Document_AI_V3.md` e `corpus/Document_AI_V3_MANIFESTO.json` (144 páginas de referência, nenhuma ausente na saída).
- Execução: `scripts/run_benchmark.sh --all-pages --run-suffix wsl-full-20260930`.
- Modo E2E: `balanced`; os tempos incluem o pipeline completo por engine, não apenas a chamada isolada ao OCR.
- Ambiente: WSL, Python 3.12 no `.venv`; runtimes configurados pelo `scripts/setup_ocr_benchmark.sh`.
- Artefatos (Markdown extraído, manifesto, métricas JSON e erros por página): `output/fase8/`. A tabela comparativa gerada está em [`output/fase8/comparison_wsl-full-20260930.md`](output/fase8/comparison_wsl-full-20260930.md).

#### Tempo de execução

| Engine | Páginas avaliadas | Tempo E2E | s/página | Run ID |
|---|---:|---:|---:|---|
| paddle | 144/144 | 2274.9 s | 15.80 | `wsl-full-20260930-paddle` |
| rapidocr-onnx | 144/144 | 91.4 s | 0.63 | `wsl-full-20260930-rapidocr-onnx` |
| rapidocr-openvino | 144/144 | 38.2 s | 0.27 | `wsl-full-20260930-rapidocr-openvino` |
| tesseract | 144/144 | 37.0 s | 0.26 | `wsl-full-20260930-tesseract` |
| easyocr | 144/144 | 479.5 s | 3.33 | `wsl-full-20260930-easyocr` |

#### Todas as métricas CER e WER

CER/WER e suas taxas são valores agregados pelo avaliador sobre o documento. Para CER e WER, menor é melhor; para Word Accuracy, maior é melhor. Heading Text CER igual a zero indica que nenhum par de heading textual entrou no cálculo, não uma taxa de erro perfeita.

| Métrica | **paddle** | **rapidocr-onnx** | **rapidocr-openvino** | **tesseract** | **easyocr** |
|---|---|---|---|---|---|
| CER Raw ↓ | 0.477071 | 0.511163 | 0.511163 | 0.488814 | 0.474609 |
| CER Normalized ↓ | 0.479743 | 0.515389 | 0.515389 | 0.492034 | 0.477694 |
| CER Text Only ↓ | 0.509125 | 0.548293 | 0.548293 | 0.518416 | 0.500495 |
| Heading Text CER ↓ | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| Cell CER ↓ | 1.077918 | 1.065245 | 1.065245 | 1.404613 | 1.128853 |
| WER ↓ | 0.509370 | 0.506693 | 0.506693 | 0.546710 | 0.547837 |
| Word Accuracy ↑ | 0.490630 | 0.493307 | 0.493307 | 0.453290 | 0.452163 |
| Substitution Rate ↓ | 0.117092 | 0.204453 | 0.204453 | 0.155136 | 0.182190 |
| Deletion Rate ↓ | 0.371847 | 0.228265 | 0.228265 | 0.367479 | 0.324644 |
| Insertion Rate ↓ | 0.020431 | 0.073975 | 0.073975 | 0.024095 | 0.041003 |
| Omission Rate ↓ | 0.371847 | 0.228265 | 0.228265 | 0.367479 | 0.324644 |

#### Demais métricas multidimensionais

#### Grupo 2 — Estrutura Markdown

| Métrica | **easyocr** | **paddle** | **rapidocr-onnx** | **rapidocr-openvino** | **tesseract** |
|---|---|---|---|---|---|
| Heading F1 ↑ | 0.1600 | 0.1584 | **0.1616** | 0.1616 | 0.1584 |
| Heading Level Acc. ↑ | **0.0000** | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| Heading Text CER ↓ | **0.0000** | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| Block F1 ↑ | 0.7257 | **0.8094** | 0.7399 | 0.7399 | 0.7250 |
| Paragraph Boundary F1 ↑ | **0.9602** | 0.8957 | 0.9509 | 0.9509 | 0.9592 |
| List Detection F1 ↑ | **0.8235** | 0.8235 | 0.8235 | 0.8235 | 0.7000 |
| Markdown AST Sim. ↑ | 0.6430 | **0.6617** | 0.6532 | 0.6532 | 0.6418 |

#### Grupo 3 — Tabelas

| Métrica | **easyocr** | **paddle** | **rapidocr-onnx** | **rapidocr-openvino** | **tesseract** |
|---|---|---|---|---|---|
| Table F1 ↑ | 0.9365 | **0.9449** | 0.9280 | 0.9280 | 0.9449 |
| Row F1 ↑ | 0.9092 | **0.9142** | 0.9089 | 0.9089 | 0.9073 |
| Column F1 ↑ | 0.8984 | 0.8968 | 0.8819 | 0.8819 | **0.8990** |
| Table Dim. Accuracy ↑ | 0.4746 | **0.5000** | 0.4828 | 0.4828 | 0.4833 |
| Cell Exact Match ↑ | 0.3246 | **0.3460** | 0.3163 | 0.3163 | 0.3429 |
| Cell CER ↓ | 1.1289 | 1.0779 | **1.0652** | 1.0652 | 1.4046 |
| Cell Alignment Acc. ↑ | 0.3246 | **0.3460** | 0.3163 | 0.3163 | 0.3429 |
| Table Structure Sim. ↑ | 0.8994 | **0.9015** | 0.8909 | 0.8909 | 0.8991 |

#### Grupo 4 — Ordem e Integridade

| Métrica | **easyocr** | **paddle** | **rapidocr-onnx** | **rapidocr-openvino** | **tesseract** |
|---|---|---|---|---|---|
| Reading Order Acc. ↑ | 0.1784 | **0.1992** | 0.1680 | 0.1680 | 0.1846 |
| Duplicate Content ↓ | **0.0071** | 0.1585 | 0.0093 | 0.0093 | 0.0095 |
| Header Leakage ↓ | **0.0000** | 0.5208 | 0.0000 | 0.0000 | 0.0000 |
| Footer Leakage ↓ | **0.0000** | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| Page# Leakage ↓ | 0.0041 | **0.0020** | 0.0020 | 0.0020 | 0.0030 |
| Failure Rate ↓ | **0.0069** | 0.0069 | 0.0069 | 0.0069 | 0.0069 |
| Invalid Markdown ↓ | **0.0000** | 0.0000 | 0.0000 | 0.0000 | 0.0000 |

#### Grupo 5 — Dados Críticos

| Métrica | **easyocr** | **paddle** | **rapidocr-onnx** | **rapidocr-openvino** | **tesseract** |
|---|---|---|---|---|---|
| Numeric Exact Match ↑ | **0.9962** | 0.9924 | 0.9848 | 0.9848 | 0.9962 |
| Date Exact Match ↑ | **1.0000** | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| Currency Exact Match ↑ | 0.9621 | 0.9848 | 0.9091 | 0.9091 | **1.0000** |
| Identifier Exact Match ↑ | **1.0000** | 1.0000 | 1.0000 | 1.0000 | 1.0000 |

#### Resumo dos resultados

- EasyOCR teve o menor CER global (Raw `0.4746`, Normalized `0.4777` e Text Only `0.5005`).
- RapidOCR ONNX e OpenVINO tiveram o menor WER (`0.5067`) e a maior Word Accuracy (`0.4933`).
- Paddle teve o maior Block F1 (`0.8094`) e levou `37.9` minutos no documento completo.
- O Failure Rate foi `0.0069` para as cinco engines; todas produziram e tiveram avaliação para 144/144 páginas.

Os valores Heading Text CER são zero porque a implementação não encontrou pares textuais de headings para calcular essa métrica; esse zero deve ser lido como ausência de amostras comparáveis, e não como acerto perfeito. Os detalhes por página e relatórios de erro permanecem nos artefatos indicados acima.

---

## Fase 9 — Otimizações EasyOCR (pontos de melhoria identificados)

> Pesquisa realizada em 2026-10-01. **Requisito:** execução 100% offline — apenas download inicial de modelos requer internet.  
> Base de comparação: Fase 8 v1 (224 págs, `balanced`, `pt`) — CER 0.2860, substitution_rate 0.2834, currency_exact 0.3803, cell_cer 1.1575, 11.83 s/pág.

### 9.1 Parâmetros críticos não configurados

| ID | Parâmetro | Valor atual | Valor proposto | Justificativa | Impacto esperado |
|---|---|---|---|---|---|
| E-01 | `canvas_size` | 2560 (padrão) | 3500 | A4@300 DPI = 2480×3508 px — o CRAFT está fazendo downscale da altura das páginas, destruindo texto pequeno | Alto — direto no cell_cer e detecção de rodapés/tabelas |
| E-02 | `decoder` | `'greedy'` (padrão) | `'beamsearch'` | Mantém N caminhos alternativos no CTC antes de decidir; reduz ambiguidade de caractere | Alto — reduz substitution_rate (28.3% atual) |
| E-03 | `beamWidth` | 5 (padrão) | 10 | Mais alternativas no beamsearch = mais acurácia em palavras ambíguas | Médio — complementa E-02 |
| E-04 | `mag_ratio` | 1.0 (padrão) | 1.5 | Magnifica input antes do CRAFT; melhora detecção de texto pequeno em células de tabela | Alto — cell_cer 1.1575 é diretamente impactado |
| E-05 | `adjust_contrast` | 0.5 (padrão) | 1.0 | Target de contraste para reprocessamento de regiões de baixo contraste; documentos financeiros escaneados têm texto desbotado | Médio — melhora recovery de texto cinza/desbotado |
| E-06 | `workers` | 0 (padrão) | 4 | Paralelismo no DataLoader para pré-carga de crops; ~30% speedup no CPU sem impacto na qualidade | Médio — 11.83 s/pág → ~8 s/pág estimado |
| E-07 | `allowlist` | não usado | `'0123456789.,R$%()-/ '` (em regiões financeiras) | Restringe o decodificador ao charset esperado; elimina confusão de caractere em valores monetários | Muito alto — currency_exact_match 38% → estimado 60%+ |
| E-08 | `blocklist` | não usado | `'OoIlBSZ'` (em regiões numéricas) | O EasyOCR confunde O→0, l→1, B→8, S→5 em PT (issue #1131); blocklist força exclusão desses caracteres | Alto — numeric_exact_match e currency |

### 9.2 Feature não utilizada: pipeline separado detect + recognize

Atualmente usamos `reader.readtext(image)` que executa detecção e reconhecimento em sequência com os mesmos parâmetros. O EasyOCR expõe os dois estágios separadamente:

```python
# Estágio 1 — Detecção (CRAFT): usar parâmetros de detecção otimizados
horizontal_list, free_list = reader.detect(
    image,
    canvas_size=3500,
    mag_ratio=1.5,
    text_threshold=0.7,
    low_text=0.4,
    link_threshold=0.4,
)

# Estágio 2 — Reconhecimento (CRNN): usar parâmetros de reconhecimento otimizados
result = reader.recognize(
    image,
    horizontal_list=horizontal_list,
    free_list=free_list,
    decoder='beamsearch',
    beamWidth=10,
    batch_size=8,
    workers=4,
    adjust_contrast=1.0,
)
```

**Por que isso importa:**
- Permite aplicar preprocessing diferente por tipo de região (tabela vs. texto corrido)
- Permite experimentar parâmetros de reconhecimento sem reexecutar a detecção cara
- Permite usar `allowlist`/`blocklist` diferentes por tipo de coluna em tabelas

### 9.3 Preprocessing com CLAHE (antes de passar ao EasyOCR)

Para páginas escaneadas com iluminação irregular (frequente em documentos financeiros em PT):

```python
import cv2

def preprocess_page_for_easyocr(img_bgr):
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    return cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)  # EasyOCR espera BGR
```

- **CLAHE** (equalização local de contraste): preserva bordas, melhora texto em regiões com iluminação desuniforme
- Ganho documentado: 29.69% → 67.97% de accuracy em um estudo com documentos escaneados
- **NÃO usar**: binarização global (Otsu/threshold) — destrói strokes finos e confunde o CRAFT
- **NÃO usar**: sharpening excessivo — cria artefatos que fragmentam detecção de regiões de texto
- Totalmente offline (OpenCV)

### 9.4 Modelo alternativo `latin_g1`

```python
reader = easyocr.Reader(['pt'], recog_network='latin_g1')
```

- Padrão atual: `latin_g2` (menor, mais rápido, lançado em EasyOCR 1.3+)
- `latin_g1`: modelo mais antigo, maior, mais lento, mas pode ter melhor acurácia em textos financeiros portugueses com fontes específicas
- Requer benchmark comparativo para confirmar se melhora ou piora no corpus
- Cacheia localmente após primeiro download; execução offline ✅

### 9.5 O que foi descartado (e por quê)

| Opção | Motivo do descarte |
|---|---|
| `detect_network='dbnet18'` | 50× mais lento que CRAFT no CPU (confirmado no issue #855) |
| `decoder='wordbeamsearch'` | Requer `CTCWordBeamSearch` extra; bug de 10× lentidão por chamada excessiva a `simplify_label` (issue #267); instável em 1.7.x |
| GPU acceleration | Constraint: execução offline em CPU-only |
| Binarização Otsu antes do EasyOCR | Destrói strokes finos; EasyOCR faz sua própria binarização interna via `contrast_ths/adjust_contrast` |

### 9.6 Fine-tuning do modelo de reconhecimento (longo prazo)

**Potencial:** Benchmark publicado mostrou 2.35% → 96.03% de accuracy em domínio financeiro numérico após fine-tune.

**Abordagem:**
1. Gerar dados sintéticos com [TextRecognitionDataGenerator](https://github.com/Belval/TextRecognitionDataGenerator) usando charset financeiro PT (`0-9`, `.,R$%()/-`, `áàãâéêíóõôúç`)
2. Anotar amostras reais do corpus com EasyOCRLabel
3. Fine-tune via `/trainer` no repo do EasyOCR com `latin_g2.pth` como ponto de partida
4. Deploy via `recog_network='pt_financial'` + `user_network_directory`

**Constraint offline:** inference do modelo fine-tunado é 100% offline; só o treinamento requer acesso aos dados.

### 9.7 Resumo — Prioridade de implementação

| Prioridade | ID | Ação | Esforço | Impacto | Status |
|---|---|---|---|---|---|
| 🔴 Alta | E-01 | `canvas_size` dinâmico (`max(h,w)`) | Mínimo | Nunca faz downscale da imagem | ✅ Impl. |
| 🔴 Alta | E-07 | `allowlist` via `EASYOCR_ALLOWLIST` | Baixo | currency_exact_match 38% → >60% | ✅ Impl. |
| 🔴 Alta | E-02+E-03 | `decoder` via `EASYOCR_DECODER` (padrão `greedy`) | Mínimo | A/B necessário para confirmar ganho de beamsearch | ✅ Impl. |
| 🟡 Média | E-04 | `mag_ratio=1.2` via `EASYOCR_MAG_RATIO` | Mínimo | Melhora detecção em tabelas | ✅ Impl. |
| 🟡 Média | E-08 | `blocklist` via `EASYOCR_BLOCKLIST` | Baixo | Configurável por deployment | ✅ Impl. |
| 🟡 Média | 9.2 | Pipeline detect + recognize separado (contrato upstream) | Médio | Corrige unwrap [0] do detect(); grayscale para recognize() | ✅ Impl. |
| 🟡 Média | 9.3 | CLAHE preprocessing (grayscale p/ recognize) | Baixo-Médio | Melhora páginas escaneadas | ⬜ Pendente (A/B necessário) |
| 🟡 Média | E-06 | `workers` via `EASYOCR_WORKERS` (padrão 0) | Mínimo | ~30% speedup em Linux; 0 no Windows | ✅ Impl. |
| 🟢 Baixa | 9.4 | `latin_g1` via `EASYOCR_RECOG_NETWORK` | Baixo | Benchmark necessário para confirmar ganho | ✅ Impl. |
| 🟢 Baixa | 9.6 | Fine-tuning do modelo | Alto | Teto máximo de qualidade | ⬜ Pendente |

### Nota de implementação (2026-10-02)

Otimizações implementadas em `src/structured_pdf_text/ocr/backends/easyocr.py`.
O backend usa o **pipeline detect + recognize separado** espelhando o contrato upstream do EasyOCR:
`reformat_input()` → `detect(reformat=False)` → `[0]` unwrap → `recognize(img_gray, reformat=False)`.
Fallback explícito para `readtext()` em caso de falha, com `RuntimeWarning` e `status="recovered"`.

**Parâmetros configuráveis por env var:**
- `EASYOCR_DECODER` — `greedy` (padrão, upstream default) ou `beamsearch` (A/B ainda não executado)
- `EASYOCR_BEAMWIDTH` — beam width para beamsearch (padrão `5`, upstream default)
- `EASYOCR_MAG_RATIO` — magnification do CRAFT (padrão `1.2`)
- `EASYOCR_ADJUST_CONTRAST` — contraste interno do recognize (padrão `0.5`)
- `EASYOCR_WORKERS` — paralelismo DataLoader (padrão auto: 0 no Windows, cpu//2 no Linux, máx 4)
- `EASYOCR_ALLOWLIST` — allowlist global (ex: `'0123456789.,R$%()-/ '`)
- `EASYOCR_BLOCKLIST` — blocklist global (atenção: não bloquear 'O'/'o' em PT)
- `EASYOCR_RECOG_NETWORK` — modelo alternativo (ex: `latin_g1`)
- `EASYOCR_MODULE_PATH` — diretório de cache dos modelos

**Não implementado (requer A/B antes de integrar):**
- CLAHE preprocessing — `_clahe_grey()` foi removida por ser código morto; reintegrar somente após A/B com ganho mensurável comprovado

**Próximo passo:** Re-executar o benchmark Fase 8 v2 com estas otimizações para medir o delta de melhoria.

---

## Métricas Futuras (fora do escopo da Fase 8 inicial)

Documentadas em `metricas_avaliacao_parser_ocr_markdown.md` — implementar em fases posteriores conforme necessidade:

- **Estrutura:** Bold F1, Italic F1, Code Span F1, Link Detection F1, Tree Edit Distance
- **Tabelas:** TEDS, Cell WER
- **Ordem:** Kendall Tau, Sequence Edit Distance, Duplicate Line Rate
- **Agregação:** Micro/Macro Average, percentis P90/P95, pior caso por página
- **Recursos:** Peak RAM, VRAM, CPU/GPU Usage
- **Exatidão total:** Document Exact Match, Page Exact Match, Block Exact Match
- **Semântica:** Semantic Similarity (baixa prioridade — não substitui métricas textuais)

---

## Fase 9 — Otimizações Tesseract (espelho da Fase 9 EasyOCR)

### Contexto

O benchmark Fase 8 revelou problemas críticos no Tesseract que têm causa-raiz clara e corrigível via configuração de CLI:

| Métrica | Valor Fase 8 | Causa raiz |
|---|---|---|
| deletion_rate | 27.4% (pior) | `--dpi` ausente → Tesseract usa 70 DPI internamente |
| CER | 43.15% | DPI bug + diacríticos PT mal lidos |
| invalid_markdown_rate | 3.57% | backtick gerado → quebra fences Markdown |
| cell_cer | 1.14 | espaços entre colunas colapsados |

### Itens implementados

| ID | Ação | Impacto esperado | Status |
|---|---|---|---|
| T-01 | `--dpi` calculado automaticamente de `config.ocr_render_scale` (padrão 144 para scale=2.0) | Corrige deletion_rate 27.4% → meta <15% | ✅ Impl. |
| T-02 | `-c textord_min_linesize=2.5` — corrige bug de diacríticos PT (ã,ç,ê,õ lidos como linha separada) | Reduz CER em documentos PT | ✅ Impl. |
| T-03 | `-c tessedit_char_blacklist=\`` — elimina backtick do output | Elimina invalid_markdown_rate 3.57% → 0% | ✅ Impl. |
| T-04 | `-c textord_noise_rejrows=0 -c textord_noise_rejwords=0` — desabilita rejeição agressiva de linhas/palavras | Reduz deleções de texto válido | ✅ Impl. |
| T-05 | `-c crunch_del_rating=40` (padrão 60) — threshold mais permissivo para deleção de palavras | Preserva mais tokens borderline | ✅ Impl. |
| T-06 | `-c language_model_penalty_non_dict_word=0.05` (padrão 0.15) — menos penalidade para vocab financeiro | Melhora CNPJ, ATIVO, EBITDA etc. | ✅ Impl. |
| T-07 | `-c preserve_interword_spaces=1` — preserva espaços entre colunas de tabela | Melhora cell_cer | ✅ Impl. |
| T-08 | CLAHE preprocessing via `_clahe_preprocess()` — converte para grayscale + aplica CLAHE antes do OCR | Melhora scans com iluminação irregular | ✅ Impl. |
| T-09 | `TESSERACT_TESSDATA_DIR` env var → `--tessdata-dir` flag — suporte a `tessdata_best` | ~5% CER adicional (download manual necessário) | ✅ Impl. |
| T-10 | `TESSERACT_DPI` env var — sobrescreve DPI calculado | Override para casos especiais | ✅ Impl. |
| T-11 | `TESSERACT_CONF_MIN` env var — filtro de confiança mínima (0–100, padrão 0 = sem filtro) | Reduz tokens de baixa qualidade | ✅ Impl. |

### Parâmetros fixos (sempre ativos)

Todos fixos no código — sem configuração necessária:

| Parâmetro | Valor | Motivo |
|---|---|---|
| `textord_min_linesize` | `2.5` | Bug diacríticos PT — sempre necessário para `por` |
| `tessedit_char_blacklist` | `` ` `` | Sempre gera Markdown inválido |
| `textord_noise_rejrows` | `0` | Deleção falsa de linhas válidas |
| `textord_noise_rejwords` | `0` | Deleção falsa de palavras válidas |
| `crunch_del_rating` | `40` | Menos agressivo que padrão 60 |
| `language_model_penalty_non_dict_word` | `0.05` | Vocab financeiro |
| `preserve_interword_spaces` | `1` | Separação de colunas em tabelas |
| CLAHE preprocessing | clipLimit=2.0, tileGridSize=8×8 | Local contrast enhancement |

### Variáveis de ambiente

| Var | Padrão | Efeito |
|---|---|---|
| `TESSERACT_LANG` | `por` | Idioma Tesseract |
| `TESSERACT_PSM` | `3` | Page segmentation mode |
| `TESSERACT_OEM` | `1` | OCR engine mode (LSTM) |
| `TESSERACT_DPI` | `72 × ocr_render_scale` | Override DPI calculado |
| `TESSERACT_TESSDATA_DIR` | não definido | Caminho para tessdata_best |
| `TESSERACT_CONF_MIN` | `0` | Confiança mínima (0–100) |

### O que NÃO foi implementado

| Item | Motivo |
|---|---|
| PSM padrão diferente (4, 11) | Precisa de benchmark para validar — PSM 3 correto para documentos mistos |
| `user_words` / `user_patterns` | Manutenção custosa; ganho menor que DPI+diacritics fix |
| Fine-tuning (tesstrain) | Explicitamente excluído |
| `OMP_THREAD_LIMIT` | Variável de ambiente do SO — documentada na docstring do módulo |

### Benchmark esperado após implementação

```powershell
.\scripts\run_benchmark.ps1 -Engine tesseract -AllPages -RunSuffix "v2"
```

Metas:
- deletion_rate: 27.4% → <15%
- CER: 43.15% → <30%
- invalid_markdown_rate: 3.57% → 0%

---

## Fase 9 — RapidOCR ONNX e OpenVINO

### Diagnóstico Fase 8

Ambas as variantes partilham as mesmas fraquezas estruturais (failure_rate ≈ 0.9% — engine estável, mas qualidade baixa):

| Métrica | RapidOCR-ONNX | RapidOCR-OpenVINO | Causa raiz |
|---|---|---|---|
| cer_normalized | 0.3596 | 0.3633 | modelo ch + diacríticos perdidos |
| insertion_rate (omissões) | **0.4968** | **0.4987** | texto não detectado/reconhecido |
| deletion_rate (extras) | 0.0273 | 0.0278 | baixo — poucas alucinações |
| substitution_rate | 0.0761 | 0.0754 | diacríticos → caractere errado |
| cell_cer | 0.6702 | 0.8889 | texto pequeno perdido em células |
| currency_exact_match | 0.3403 | 0.3361 | R$ perdido por diacríticos adjacentes |

**Nota:** `insertion_rate` nesta codebase representa palavras da referência **ausentes** no output (semântica invertida vs ASR padrão).

**Piores páginas (ambas):** `ocr_table` com degradação (skew, lowdpi, noise, blur, grayscale, jpeg). Páginas 156, 162, 148, 181, 161, 182 — coincidentes em ambas as variantes.

### Causas raiz identificadas

1. **Modelo padrão PP-OCRv4 ch (Chinês):** não contém ã, ç, ê, õ na codificação de saída — modelo bundled em `rapidocr-onnxruntime 1.4.4` é treinado para Chinês simplificado + ASCII básico.
2. **`unclip_ratio=1.6` clipa diacríticos:** ascendentes de ã, â, ê e descenders de ç estouram caixas de detecção não expandidas o suficiente.
3. **`box_thresh=0.5` e `det_thresh=0.3` conservadores:** perdem caixas fracas em scans degradados (lowdpi, jpeg, blur).
4. **CLAHE ausente:** scans chegam ao detector sem enhancement local de contraste.

### Otimizações implementadas (Fase 9)

| ID | Ação | Impacto esperado | Status |
|---|---|---|---|
| R-01 | CLAHE via canal L do espaço LAB — `_clahe_preprocess()` retorna BGR 3-channel (preserva cor vs grayscale dos outros backends) | Melhora recall em scans degradados | ✅ Impl. |
| R-02 | `det_db_unclip_ratio` 1.6 → 1.8 — expande caixas detectadas para incluir diacríticos | Reduz clipagem de ã, ç, ê nas bordas | ✅ Impl. |
| R-03 | `det_db_box_thresh` 0.5 → 0.45 — recupera caixas fracas em scans de baixo contraste | Melhora recall em tabelas degradadas | ✅ Impl. |
| R-04 | `det_db_thresh` 0.3 → 0.25 — threshold pixel-level mais permissivo para ink fraco | Recupera texto em fotocópias | ✅ Impl. |
| R-05 | `text_score` exposto via `RAPIDOCR_TEXT_SCORE` (padrão 0.5 mantido) | Tunável por env var | ✅ Impl. |
| R-06 | `with_angle_cls=False` por padrão — documentos PT portrait não precisam | Remove latência desnecessária | ✅ Impl. |
| R-07 | `RAPIDOCR_REC_KEYS` env var para dict de caracteres — necessário ao trocar para modelo Latin | Habilita modelos Latin sem hardcode | ✅ Impl. |
| R-08 | try/except no construtor — fallback gracioso para versões do pacote que não aceitam os kwargs | Robustez cross-version | ✅ Impl. |

### Parâmetros e seus padrões (Fase 9)

| Parâmetro | Padrão v1 (antes) | Padrão v2 (Fase 9) | Motivo |
|---|---|---|---|
| CLAHE preprocessing | ausente | LAB L-channel, clipLimit=2.0, 8×8 | Contrast enhancement preservando cor |
| `det_db_unclip_ratio` | 1.6 | **1.8** | Diacríticos clipados nas bordas |
| `det_db_box_thresh` | 0.5 | **0.45** | Caixas fracas em scans degradados |
| `det_db_thresh` | 0.3 | **0.25** | Ink fraco em fotocópias |
| `text_score` | 0.5 | 0.5 (exposto) | Tunável, padrão mantido |
| `with_angle_cls` | True (padrão pacote) | **False** | Documentos portrait não precisam |

### Variáveis de ambiente

| Var | Padrão | Efeito |
|---|---|---|
| `RAPIDOCR_DET_MODEL` | não definido | Caminho para modelo de detecção alternativo |
| `RAPIDOCR_REC_MODEL` | não definido | Caminho para modelo Latin (PP-OCRv4 latin, PP-OCRv6) |
| `RAPIDOCR_REC_KEYS` | não definido | Dict de caracteres para modelo não-padrão (ex: `en_dict.txt`) |
| `RAPIDOCR_UNCLIP_RATIO` | `1.8` | Expansão de caixas DB (1.5–2.0) |
| `RAPIDOCR_BOX_THRESH` | `0.45` | Score mínimo por caixa (0.3–0.6) |
| `RAPIDOCR_DET_THRESH` | `0.25` | Threshold pixel-level do mapa DB (0.2–0.4) |
| `RAPIDOCR_TEXT_SCORE` | `0.5` | Confiança mínima por linha |
| `RAPIDOCR_ANGLE_CLS` | `0` | Ativar classificador de ângulo (`1` = opt-in) |

### O que NÃO foi implementado

| Item | Motivo |
|---|---|
| Modelo Latin como padrão | Requer download de model + dict — opt-in via env var existente |
| Modelo PP-OCRv6 como padrão | CF-2: DLL incompatibility no Windows; opt-in via env var |
| `score_mode: slow` | Ganho marginal; aumenta latência em todas as páginas |
| `intra_op_num_threads` / `inference_num_threads` | Padrão automático do ONNX/OpenVINO é suficiente |

### Benchmark esperado após implementação

```powershell
.\scripts\run_benchmark.ps1 -Engine rapidocr-onnx -AllPages -RunSuffix "v2"
.\scripts\run_benchmark.ps1 -Engine rapidocr-openvino -AllPages -RunSuffix "v2"
```

Metas (v1 → v2, sem troca de modelo):
- insertion_rate: 49.7% → <40%
- substitution_rate: 7.6% → <5%
- cer_normalized: 0.360 → <0.30
- `deletion_rate` não deve piorar (CLAHE não gera falsos positivos)

---

## Melhorias de Infraestrutura de Benchmark (2026-10-01)

### compute_metrics.py — Paralelização com ProcessPoolExecutor

O loop de páginas de `scripts/compute_metrics.py` foi paralelizado usando
`ProcessPoolExecutor` com um worker por core de CPU.

| Antes | Depois | Hardware |
|---|---|---|
| ~60 min (sequencial) | ~10 min | 12 cores, corpus 224 páginas |

**Por que é seguro:**
- Cada página é processada de forma independente (sem estado compartilhado).
- A função worker `_process_page` é definida em nível de módulo (picklable), garantindo
  compatibilidade com o método `spawn` do Windows.
- Os resultados são ordenados por número de página após a coleta, produzindo
  output idêntico ao da versão sequencial.

**Guard necessário no Windows:** o bloco `if __name__ == "__main__":` já existia
no script antes da paralelização (linha 1102+), protegendo contra execução
recursiva nos workers.

### run_benchmark.ps1 — Correção do splatting @metricsFiles

**Problema:** quando apenas uma engine é executada, `Get-ChildItem | ForEach-Object { $_.FullName }`
retorna uma string (não array). `@string` em PowerShell itera sobre os caracteres
individuais, passando `\` e `.` como argumentos ao `compare_engines.py`, que
os interpretava como caminhos de arquivo válidos e falhava silenciosamente.

**Sintoma observado:**
```
WARNING: skipping \: ...
WARNING: skipping .: ...
ERROR: no valid metrics files loaded
```

**Correção:** envolver a atribuição em `@(...)` força o resultado a ser sempre
um array, independente do número de arquivos retornados:
```powershell
$metricsFiles = @(Get-ChildItem "$OutDir\metrics_*_${RunSuffix}-*.json" ... |
    ForEach-Object { $_.FullName })
```
