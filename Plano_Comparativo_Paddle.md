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
| 8 | Benchmark E2E completo | `evaluate_e2e.py`, `compute_metrics.py`, `compare_engines.py` | ⬜ |

---

## Resultados de Performance (pp. 72–103, 32 páginas scan)

| Engine | Tempo total | s/pág | Páginas OK | CER médio |
|---|---|---|---|---|
| PaddleOCR (sem oneDNN — CF-1) | 3018 s | ~94 s | 32/32 | 0.4106 (parcial) |
| RapidOCR ONNX (PP-OCRv4 — CF-2) | 88.2 s | ~2.76 s | 32/32 | — aguarda Fase 8 |
| RapidOCR OpenVINO (PP-OCRv4 — CF-2) | 20.1 s | ~0.63 s | 32/32 | — |
| Tesseract 5 (por, PSM 3, OEM 1) | 18.5 s | ~0.58 s | 32/32 | — |
| EasyOCR (latin_g2, CPU) | 173.1 s | ~5.4 s | 32/32 | — |

---

## Correções Futuras

| ID | Descrição | Status |
|---|---|---|
| CF-1 | oneDNN desabilitado no Windows (bug PIR API Paddle 3.x) — restaurar via `FLAGS_enable_pir_api=False` | ⬜ |
| CF-2 | `paddle2onnx` DLL incompatível (0xC0000139) — RapidOCR usa PP-OCRv4 embutido em vez de PP-OCRv6 | ⬜ |
| CF-3 | numpy conflict: EasyOCR atualiza para 2.5.x, quebra openvino<2.1.0 — fix: `pip install "numpy==2.0.2"` | ✅ |

---

## Fase 8 — Benchmark E2E com Métricas Multidimensionais

### Objetivo

Avaliar a qualidade de extração de cada engine no **documento completo** via pipeline E2E, comparando contra ground truth manual. Separar erros de OCR de erros de reconstrução Markdown. Identificar exatamente onde cada erro ocorre.

### Corpus e Ground Truth

- Arquivo: `corpus/Document_AI_V3.pdf` — **todas as páginas** (nativas + scan)
- Ground truth: disponível — fornecer caminho via argumento ou `GROUND_TRUTH_DIR`
- O pipeline E2E aplica native-first; métricas avaliam a saída final completa

### Scripts a implementar

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

### Tabela comparativa (a preencher após execução)

| Métrica | Paddle | RapidOCR ONNX | RapidOCR OpenVINO | Tesseract | EasyOCR |
|---|---|---|---|---|---|
| CER Normalized | — | — | — | — | — |
| CER Text Only | — | — | — | — | — |
| WER | — | — | — | — | — |
| Block F1 | — | — | — | — | — |
| Heading F1 | — | — | — | — | — |
| Heading Level Accuracy | — | — | — | — | — |
| Paragraph Boundary F1 | — | — | — | — | — |
| Table F1 | — | — | — | — | — |
| Cell Exact Match | — | — | — | — | — |
| Cell Alignment Accuracy | — | — | — | — | — |
| Reading Order Accuracy | — | — | — | — | — |
| Duplicate Content Rate | — | — | — | — | — |
| Header Leakage Rate | — | — | — | — | — |
| Failure Rate | — | — | — | — | — |
| Numeric Exact Match | — | — | — | — | — |
| Currency Exact Match | — | — | — | — | — |
| Identifier Exact Match | — | — | — | — | — |

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
