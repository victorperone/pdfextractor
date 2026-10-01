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

## Métricas Futuras (fora do escopo da Fase 8 inicial)

Documentadas em `metricas_avaliacao_parser_ocr_markdown.md` — implementar em fases posteriores conforme necessidade:

- **Estrutura:** Bold F1, Italic F1, Code Span F1, Link Detection F1, Tree Edit Distance
- **Tabelas:** TEDS, Cell WER
- **Ordem:** Kendall Tau, Sequence Edit Distance, Duplicate Line Rate
- **Agregação:** Micro/Macro Average, percentis P90/P95, pior caso por página
- **Recursos:** Peak RAM, VRAM, CPU/GPU Usage
- **Exatidão total:** Document Exact Match, Page Exact Match, Block Exact Match
- **Semântica:** Semantic Similarity (baixa prioridade — não substitui métricas textuais)
