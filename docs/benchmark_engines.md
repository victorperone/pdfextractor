# Benchmark Comparativo de Engines OCR

Este documento descreve o sistema de benchmark E2E (end-to-end) implementado na
Fase 8 do projeto. O benchmark avalia a qualidade de extração de texto e estrutura
Markdown de cinco engines OCR diferentes, usando um corpus de stress test com
ground truth manual.

---

## Motivação

O projeto suporta múltiplas engines OCR através do contrato `OCRBackend`
(`src/structured_pdf_text/ocr/contracts.py`). Cada engine tem características
distintas de velocidade, qualidade e dependências. O benchmark permite:

- comparar objetivamente a qualidade de extração de texto (CER, WER)
- avaliar a fidelidade da estrutura Markdown gerada (headings, tabelas, listas)
- identificar quais condições de digitalização (baixa DPI, ruído, rotação) cada
  engine lida melhor
- quantificar trade-offs entre velocidade e qualidade para decisão de deploy

---

## Engines Avaliadas

| Engine | Modo | Backend | Velocidade (corpus 32p) | Dependências |
|---|---|---|---|---|
| **PaddleOCR** | `paddle` | PaddlePaddle | ~94 s/pág | `paddlepaddle`, `paddleocr` |
| **RapidOCR ONNX** | `rapidocr-onnx` | ONNX Runtime | ~2,76 s/pág | `rapidocr-onnxruntime` |
| **RapidOCR OpenVINO** | `rapidocr-openvino` | Intel OpenVINO | ~0,63 s/pág | `rapidocr-openvino` |
| **Tesseract 5** | `tesseract` | subprocess + TSV | ~0,58 s/pág | `tesseract` (binário) |
| **EasyOCR** | `easyocr` | PyTorch CPU | ~5,4 s/pág | `easyocr`, `torch` |

> Os tempos acima são de um benchmark RAW em 32 páginas densas (pp. 72–103 do
> corpus). O benchmark E2E completo inclui páginas nativas (sem OCR), o que
> reduz o tempo total significativamente para engines rápidas.

### Seleção de Engine na API

```python
from structured_pdf_text.api import PdfTextExtractor
from structured_pdf_text.config import best_extraction_config
from dataclasses import replace

config = best_extraction_config(language="pt", preserve_headers=False)

# Trocar engine:
config = replace(config, ocr_engine="tesseract")   # ou "rapidocr-onnx", "easyocr", etc.

extractor = PdfTextExtractor(config)
document = extractor.extract("documento.pdf")
```

---

## Instalação das Engines

O script `scripts/setup_ocr_benchmark.ps1` automatiza a instalação no Windows.

```powershell
.\scripts\setup_ocr_benchmark.ps1
```

### Instalação Manual

```powershell
# PaddleOCR
pip install paddlepaddle paddleocr

# RapidOCR ONNX
pip install rapidocr-onnxruntime

# RapidOCR OpenVINO (instalar sem dependências para evitar conflito com numpy)
pip install openvino
pip install rapidocr-openvino --no-deps

# EasyOCR (PyTorch CPU)
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install easyocr

# Corrigir conflito de numpy (CF-3)
pip install "numpy==2.0.2"
```

---

## Problemas Conhecidos

| ID | Descrição | Status |
|---|---|---|
| CF-1 | **oneDNN desabilitado por padrão em CPU** — Paddle 3.x pode falhar no caminho PIR/oneDNN em Linux/WSL e Windows. `PADDLE_ENABLE_MKLDNN=1` é opt-in explícito; `FLAGS_enable_pir_api=False` permanece aplicado no Windows. | Política segura no runtime |
| CF-2 | **DLL incompatível `paddle2onnx`** — Incompatibilidade 0xC0000139 no Windows. RapidOCR usa PP-OCRv4 embutido em vez de converter modelos Paddle. | Contornado |
| CF-3 | **Conflito de numpy** — EasyOCR força numpy ≥ 2.5.x; OpenVINO < 2.1.0 requer numpy < 2.x. Fix: `pip install "numpy==2.0.2"` | ✅ Aplicado |

---

## Grupos de Métricas

O benchmark calcula cinco grupos de métricas sobre o **documento Markdown
completo** (não por página individual). O array `per_page` no JSON de saída
contém as métricas por página para diagnóstico.

### Grupo 1 — Texto

Mede a fidelidade do texto extraído em relação ao ground truth.

| Métrica | Descrição |
|---|---|
| `cer_raw` | CER sem normalização, preservando diferenças de Markdown, espaços e quebras de linha. |
| `cer_normalized` | Character Error Rate após normalização (NFC, CRLF→LF, espaços). Principal métrica de qualidade OCR. |
| `cer_text_only` | CER com Markdown removido — isola erro de reconhecimento do erro de estruturação. |
| `wer` | Word Error Rate — sensível a erros de segmentação de palavras. |
| `word_accuracy` | 1 − WER |
| `substitution_rate` | Taxa de palavras substituídas por outra errada. |
| `deletion_rate` | Taxa de palavras omitidas. |
| `insertion_rate` | Taxa de palavras inseridas indevidamente. |
| `omission_rate` | Igual ao deletion_rate (conteúdo perdido). |

> **Cálculo**: micro-average ponderado pelo comprimento da referência em cada
> página — equivalente a comparar o documento inteiro sem o custo quadrático do
> Levenshtein sobre milhões de caracteres.

### Grupo 2 — Estrutura Markdown

Avalia se a estrutura do documento (headings, blocos, listas) foi preservada.

| Métrica | Descrição |
|---|---|
| `heading_f1` | F1 de detecção de headings por texto normalizado. |
| `heading_level_accuracy` | Fração de headings detectados com nível correto (#, ##, ###). |
| `heading_text_cer` | CER médio do texto dos headings detectados. |
| `block_f1` | F1 de tipos de bloco (heading, paragraph, table, list, code) como multiset. |
| `paragraph_boundary_f1` | F1 de limites de parágrafo (proxy: contagem de parágrafos). |
| `list_detection_f1` | F1 de detecção de blocos de lista. |
| `markdown_ast_similarity` | LCS da sequência de tipos de bloco / comprimento máximo — similidade estrutural global. |

> **Cálculo**: computed sobre o Markdown completo concatenado (sem os cabeçalhos
> sintéticos `## Página N` adicionados pelo renderer).

### Grupo 3 — Tabelas

Avalia a fidelidade de tabelas GFM (pipe tables) extraídas.

| Métrica | Descrição |
|---|---|
| `table_f1` | F1 de detecção de tabelas (contagem). |
| `row_f1` | F1 de linhas dentro de tabelas emparelhadas por posição. |
| `column_f1` | F1 de colunas dentro de tabelas emparelhadas. |
| `table_dimension_accuracy` | Fração de tabelas com dimensão exata (N linhas × M colunas). |
| `cell_exact_match` | Fração de células com conteúdo idêntico à referência (posição [linha, coluna]). |
| `cell_cer` | CER médio por célula. |
| `cell_alignment_accuracy` | Fração de células no posição [linha, coluna] correta. |
| `table_structure_similarity` | Média geométrica de row_f1 e col_f1. |

### Grupo 4 — Ordem e Integridade

Avalia problemas estruturais que não dependem de qualidade OCR.

| Métrica | Descrição |
|---|---|
| `reading_order_accuracy` | LCS dos blocos de texto na ordem do documento / total de blocos ref. |
| `block_order_accuracy` | Mesmo proxy de reading_order_accuracy. |
| `duplicate_content_rate` | Fração de parágrafos repetidos no documento extraído. |
| `duplicate_block_rate` | Fração de tipos de bloco repetidos consecutivamente. |
| `header_leakage_rate` | Fração de páginas cujo primeiro bloco é idêntico em > 35% das páginas (cabeçalho vazando). |
| `footer_leakage_rate` | Idem para o último bloco (rodapé). |
| `page_number_leakage_rate` | Taxa de linhas contendo apenas um número (número de página vazando). |
| `failure_rate` | Fração de páginas com conteúdo na referência que geraram saída vazia. |
| `invalid_markdown_rate` | Fração de páginas com Markdown mal-formado (fences não fechados, tabelas quebradas). |

### Grupo 5 — Dados Críticos

Avalia preservação de entidades estruturadas com semântica específica, via
regex sobre o documento completo.

| Métrica | Descrição |
|---|---|
| `numeric_exact_match` | Fração de números (inteiros, decimais, percentuais) da referência encontrados na hipótese. |
| `date_exact_match` | Fração de datas (dd/mm/aaaa e variantes) preservadas. |
| `currency_exact_match` | Fração de valores monetários (R$) preservados. |
| `identifier_exact_match` | Fração de CPF, CNPJ e números de processo preservados. |

---

## Pipeline de Scripts

```
PDF + Ground Truth
       │
       ▼
┌─────────────────────┐
│  evaluate_e2e.py    │  Extrai com a engine escolhida; salva Markdown + manifesto
└──────────┬──────────┘
           │  extracted_{engine}_{run_id}.md
           │  manifesto_e2e_{engine}_{run_id}.json
           ▼
┌─────────────────────┐
│  compute_metrics.py │  Compara contra ground truth; calcula os 5 grupos de métricas
└──────────┬──────────┘
           │  metrics_{engine}_{run_id}.json
           │  errors_{engine}_{run_id}.md
           ▼
┌─────────────────────┐
│  compare_engines.py │  Agrega JSONs de todas as engines; gera tabela comparativa
└──────────┬──────────┘
           │  comparison_{suffix}.md
           ▼
       Resultado
```

---

## Como Executar

### Pré-requisito

```powershell
pip install "numpy==2.0.2"   # CF-3: corrige conflito EasyOCR/OpenVINO
```

### Smoke Test — 5 Páginas, Todas as Engines

```powershell
.\scripts\run_benchmark.ps1
```

Parâmetros padrão: páginas 77–81, sufixo `smoke`.

### Documento Completo

```powershell
.\scripts\run_benchmark.ps1 -RunSuffix "v1" -AllPages
```

### Linux / WSL

```bash
# Instalar runtimes OCR no .venv e Tesseract em um prefixo sem sudo
scripts/setup_ocr_benchmark.sh

# Smoke test no corpus Document AI V3 (páginas 77–81)
scripts/run_benchmark.sh --run-suffix wsl-smoke

# Documento completo
scripts/run_benchmark.sh --run-suffix wsl-v1 --all-pages
```

O runner Bash usa `corpus/Document_AI_V3.pdf`, `corpus/Document_AI_V3.md` e
`corpus/Document_AI_V3_MANIFESTO.json` por padrão. O Markdown fornece o texto
referência; o manifesto fornece metadados por página. Os dois podem ser
sobrescritos com `--pdf`, `--reference` e `--manifesto`.

### Engine Individual (PowerShell, linha única)

```powershell
# Extração
python scripts\evaluate_e2e.py "corpus\Corpus_Stress_OCR_Markdown_V4.pdf" --engine tesseract --pages 1-5 --run-id smoke-tesseract --output-dir output\fase8

# Métricas
python scripts\compute_metrics.py --hypothesis output\fase8\extracted_tesseract_smoke-tesseract.md --manifesto "corpus\Corpus_Stress_OCR_Markdown_V4_MANIFESTO.json" --engine tesseract --run-id smoke-tesseract --output-dir output\fase8

# Tabela comparativa (após rodar múltiplas engines)
python scripts\compare_engines.py output\fase8\metrics_*_smoke-*.json --output output\fase8\comparison_smoke.md
```

### Parâmetros do Script PowerShell

| Parâmetro | Padrão | Descrição |
|---|---|---|
| `-Pages` | `"77-81"` | Intervalo de páginas para smoke test |
| `-AllPages` | (switch) | Processar documento completo |
| `-RunSuffix` | `"smoke"` | Sufixo dos arquivos de saída |
| `-Corpus` | *(caminho padrão)* | Caminho do PDF |
| `-Manifesto` | *(caminho padrão)* | Caminho do manifesto JSON |
| `-OutDir` | `"output\fase8"` | Diretório de saída |

---

## Arquivos de Saída

Todos os arquivos são salvos em `output/fase8/` por padrão.

| Arquivo | Gerado por | Conteúdo |
|---|---|---|
| `extracted_{engine}_{run_id}.md` | `evaluate_e2e.py` | Markdown extraído pelo pipeline E2E |
| `manifesto_e2e_{engine}_{run_id}.json` | `evaluate_e2e.py` | Metadados do run (git SHA, engine identity, tempo por página) |
| `metrics_{engine}_{run_id}.json` | `compute_metrics.py` | Todos os grupos de métricas + array `per_page` |
| `errors_{engine}_{run_id}.md` | `compute_metrics.py` | Relatório de erros: 20 piores páginas por CER |
| `comparison_{suffix}.md` | `compare_engines.py` | Tabela comparativa lado a lado de todas as engines |

### Estrutura do JSON de Métricas

```json
{
  "engine": "tesseract",
  "run_id": "v1-tesseract",
  "pages_evaluated": 224,
  "pages_reference": 224,
  "pages_missing_in_hypothesis": 0,
  "grupo1_texto": {
    "cer_normalized": 0.042,
    "cer_text_only": 0.038,
    "wer": 0.091,
    "word_accuracy": 0.909,
    "substitution_rate": 0.031,
    "deletion_rate": 0.048,
    "insertion_rate": 0.012,
    "omission_rate": 0.048
  },
  "grupo2_estrutura_markdown": { ... },
  "grupo3_tabelas": { ... },
  "grupo4_ordem_integridade": { ... },
  "grupo5_dados_criticos": { ... },
  "per_page": [
    { "page": 1, "cer_normalized": 0.01, "heading_f1": 1.0, ... },
    ...
  ]
}
```

---

## Corpus de Teste

**Arquivo**: `corpus/Corpus_Stress_OCR_Markdown_V4.pdf` — 224 páginas

O corpus foi projetado para cobrir as condições de digitalização encontradas em
documentos reais. Inclui páginas nativas (texto extraível diretamente) e páginas
OCR com variações controladas:

| Condição | Descrição |
|---|---|
| `native_dense` | Texto nativo de alta densidade (extração sem OCR) |
| `scan_clean_300` | Scan limpo a 300 DPI |
| `scan_clean_200` | Scan limpo a 200 DPI |
| `low_dpi` | Scan de baixa resolução |
| `noise` | Ruído de digitalização |
| `blur` | Borrão |
| `skew` | Inclinação da página |
| `rotation` | Rotação (90°, 180°) |
| `hybrid` | Combinação de condições |
| `adversarial` | Condições extremas |

O arquivo de ground truth (`corpus/Corpus_Stress_OCR_Markdown_V4_MANIFESTO.json`)
contém o campo `expected_markdown` por página — a saída Markdown esperada para
cada condição.

---

## Resultados de Desempenho (Benchmark RAW — 32 páginas densas)

> Medido em pp. 72–103 do corpus (32 páginas OCR densas). O pipeline E2E completo
> produz resultados diferentes pois inclui páginas nativas (muito mais rápidas).

| Engine | Tempo total | s/pág | Status |
|---|---|---|---|
| PaddleOCR (sem oneDNN — CF-1) | 3018 s | ~94 s | 32/32 OK |
| RapidOCR ONNX (PP-OCRv4 — CF-2) | 88 s | ~2,76 s | 32/32 OK |
| RapidOCR OpenVINO (PP-OCRv4 — CF-2) | 20 s | ~0,63 s | 32/32 OK |
| Tesseract 5 (por, PSM 3) | 18 s | ~0,58 s | 32/32 OK |
| EasyOCR (latin_g2, CPU) | 173 s | ~5,4 s | 32/32 OK |

A tabela de métricas de qualidade (`comparison_v1.md`) é preenchida após a
execução do benchmark E2E completo.

---

## Referências

- Contrato OCR: [`src/structured_pdf_text/ocr/contracts.py`](../src/structured_pdf_text/ocr/contracts.py)
- Backends: [`src/structured_pdf_text/ocr/backends/`](../src/structured_pdf_text/ocr/backends/)
- Diagnósticos base: [`src/structured_pdf_text/diagnostics/ocr_metrics.py`](../src/structured_pdf_text/diagnostics/ocr_metrics.py)
- Plano de implementação: [`Plano_Comparativo_Paddle.md`](../Plano_Comparativo_Paddle.md)
