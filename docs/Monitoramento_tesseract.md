# Monitoramento Tesseract — Baseline Oficial

**Data**: 2026-10-23  
**Executor**: `scripts/run_baseline.py`  
**Status final baseline**: `finished_partial` — V3✓ V5✓ V6✓ V7✗ (métricas indisponíveis com budget 8M)  
**Status benchmark 64M**: `complete` — V3✓ V5✓ V6✓ V7✓ (todas as métricas por-página disponíveis)

---

## 1. Configuração e Versões

| Parâmetro | Valor |
|---|---|
| Engine OCR | Tesseract |
| Perfil | BALANCED + ADAPTIVE |
| Idioma | pt-BR (modelo `por`) |
| Threads | 0 (CPU, sem GPU) |
| Formato saída | Markdown |
| Config ID | `balanced_adaptive_tesseract_pt-BR` |
| Script | `scripts/run_baseline.py` |
| Git SHA | `1482aec` (Finish decoupling OCR engines) |

---

## 2. Corpora e Resultados de Extração

| Corpus | Páginas | Status extração | Engine confirmado | Tempo extração |
|---|---|---|---|---|
| V3 | 144 | ✓ success | tesseract | 104,2 s |
| V5 | 42 | ✓ success | tesseract | 32,8 s |
| V6 | 95 | ✓ success | tesseract | 134,5 s |
| V7 | 80 | ✓ success | tesseract | 95,2 s |

**Todas as 361 páginas foram extraídas com sucesso.**

---

## 3. Performance por Corpus

| Corpus | Extração (s) | Métricas (s) | Peak RSS (MB) | Commit consumido (GB) |
|---|---|---|---|---|
| V3 | 104,2 | 347 | 272,7 | 0,61 |
| V5 | 32,8 | 120 | 93,6 | 0,29 |
| V6 | 134,5 | 1.157 | 279,4 | 0,52 |
| V7 | 95,2 | 1.826 (falhou) | 220,6 | 0,43 |

Tesseract provou ser dramaticamente mais leve que EasyOCR: pico de RSS de 280 MB (vs. >9 GB do EasyOCR no V3 exaustivo). O tempo de métricas cresce com o volume de texto (O(chars²) no alinhamento de blocos).

---

## 4. Métricas Iniciais por Corpus

### 4.1 V3 — Documento Misto (144 páginas)

**Fonte de referência**: `--reference` (REFERENCIA.md)  
**Métricas**: 57 aplicáveis, 12 indisponíveis (CER sequencial global excede budget 8M células)

| Grupo | Métrica | Valor | Direção |
|---|---|---|---|
| Texto | cer_raw (legacy) | 0,674 | ↓ melhor |
| Texto | wer (legacy) | 0,826 | ↓ melhor |
| Texto | aligned_block_text_cer | 1,123 | ↓ melhor |
| Estrutura | heading_f1 | 0,485 | ↑ melhor |
| Estrutura | heading_level_accuracy | 0,0 | ↑ melhor |
| Estrutura | heading_text_cer | 14,62 | ↓ melhor |
| Estrutura | heading_unmatched_hyp | 86 | — |
| Tabelas | table_f1 | 0,727 | ↑ melhor |
| Tabelas | table_recall | 0,571 | ↑ melhor |
| Ordem/Integridade | reading_order_accuracy | 0,245 | ↑ melhor |
| Ordem/Integridade | duplicate_content_rate | 0,181 | ↓ melhor |
| Ordem/Integridade | header_leakage_rate | 0,678 | ↓ melhor |
| Dados críticos | numeric_exact_match | 0,948 | ↑ melhor |
| Dados críticos | date_exact_match | 1,0 | ↑ melhor |
| Dados críticos | currency_exact_match | 0,545 | ↑ melhor |

**Hipóteses confirmadas em BALANCED+ADAPTIVE**:
- H001: `reading_order_accuracy=0,245` e `duplicate_content_rate=0,181` ✓
- H002: `heading_text_cer=14,62` (>100%) e `heading_level_match_rate=0,0` ✓
- H003: `cer_raw=0,674` / `wer=0,826` ✓

### 4.2 V5 — Documento de Referência (42 páginas)

**Fonte de referência**: `--manifesto`  
**Métricas**: 63 aplicáveis, 6 indisponíveis

| Grupo | Métrica | Valor | Direção |
|---|---|---|---|
| Texto | cer_raw (legacy) | 0,346 | ↓ melhor |
| Texto | wer (legacy) | 0,391 | ↓ melhor |
| Estrutura | heading_f1 | 0,793 | ↑ melhor |
| Estrutura | heading_level_accuracy | 1,0 | ↑ melhor |
| Estrutura | heading_text_cer | 0,386 | ↓ melhor |
| Tabelas | table_f1 | 0,952 | ↑ melhor |
| Tabelas | table_recall | 0,909 | ↑ melhor |
| Ordem/Integridade | reading_order_accuracy | 0,213 | ↑ melhor |
| Ordem/Integridade | duplicate_content_rate | 0,199 | ↓ melhor |
| Ordem/Integridade | header_leakage_rate | 0,0 | ↓ melhor |
| Dados críticos | numeric_exact_match | 0,955 | ↑ melhor |
| Dados críticos | date_exact_match | 1,0 | ↑ melhor |
| Dados críticos | currency_exact_match | 0,935 | ↑ melhor |

V5 é o corpus mais equilibrado: bom em heading_level, tabelas e dados críticos. `reading_order_accuracy` baixo (0,213) apesar de boa qualidade geral sugere problema sistemático na métrica ou no extrator.

### 4.3 V6 — Corpus de Erros Conhecidos (95 páginas)

**Fonte de referência**: `--manifesto`  
**Métricas**: 60 aplicáveis, 9 indisponíveis

| Grupo | Métrica | Valor | Direção |
|---|---|---|---|
| Texto | cer_raw (legacy) | 0,450 | ↓ melhor |
| Texto | wer (legacy) | 0,557 | ↓ melhor |
| Estrutura | heading_f1 | 0,667 | ↑ melhor |
| Estrutura | heading_level_accuracy | 1,0 | ↑ melhor |
| Estrutura | heading_text_cer | 0,252 | ↓ melhor |
| Tabelas | table_f1 | 0,743 | ↑ melhor |
| Tabelas | table_recall | 0,591 | ↑ melhor |
| Ordem/Integridade | reading_order_accuracy | 0,293 | ↑ melhor |
| Ordem/Integridade | duplicate_content_rate | 0,433 | ↓ melhor |
| Ordem/Integridade | duplicate_block_rate | 0,432 | ↓ melhor |
| Ordem/Integridade | **header_leakage_rate** | **0,968** | ↓ melhor |
| Dados críticos | numeric_exact_match | 0,976 | ↑ melhor |
| Dados críticos | date_exact_match | 0,985 | ↑ melhor |
| Dados críticos | currency_exact_match | 0,546 | ↑ melhor |

**Pior problema V6**: `header_leakage_rate = 0,968` — cabeçalhos de página estão sendo incluídos no corpo do texto em 96,8% das páginas. Combinado com `duplicate_content_rate=0,433`, esses cabeçalhos duplicados inflar o conteúdo repetitivo. Este é o erro prioritário a corrigir no pipeline Tesseract.

### 4.4 V7 — Corpus de Acertos Conhecidos (80 páginas)

**Fonte de referência**: `--manifesto`  
**Métricas**: INDISPONÍVEIS — falha técnica do avaliador (ver §5)

| Artefato | Status |
|---|---|
| extract_info.json | ✓ status=success, page_count=80, engine=tesseract |
| extracted.md | ✓ 145.950 bytes |
| extracted_paginated.md | ✓ 147.301 bytes |
| page_progress.json | ✓ 80/80 páginas |
| resource_profile.json | ✓ peak_rss=220,6 MB |
| metrics_v3_*.json | ✗ AUSENTE |
| metrics_tesseract_*.json | ✗ AUSENTE |
| errors_*.md | ✗ AUSENTE |

---

## 5. Limitações da Avaliação

### 5.1 CER sequencial global — todos os corpora

**Métrica**: `cer_sequential_normalized`, `cer_markdown_raw`, `wer_sequential`  
**Status**: `resource_limited` para todos os corpora, mesmo com budget 64M  
**Causa**: O documento completo concatenado é muito grande. Para V3 (69K chars), a band inicial ≈ 7K chars → cells ≈ 69K × 14K ≈ 966M >> 64M. O orçamento seria atingido imediatamente na primeira iteração.  
**Nota**: `wer_sequential` foi recuperado no V5 com budget 64M (4K palavras, band de 400 palavras → 1,6M células = dentro do orçamento).  
**Impacto**: Não compromete a qualidade da análise. CER/WER por-página estão completos (per_page entries) e idênticos entre baseline e benchmark.

### 5.2 Métricas V7 — RESOLVIDAS com budget 64M

**Problema anterior (8M)**: A função `_process_page` em `scripts/compute_metrics.py` não capturava `EditDistanceResourceLimit`. Página 4 (wide_12, ref=3.156 chars, CER=68%) exigia ~10,5M células → exceção propagada, rc=1, zero arquivos gravados.

**Resolução aplicada manualmente pelo usuário**:
- `DEFAULT_MAX_EDIT_CELLS` alterado de 8M para **64M** em `edit_distance.py`
- `ProcessPoolExecutor` paralelo adicionado em `evaluator.py` para avaliação por-página
- `DEFAULT_MAX_ALIGNMENT_PAIRS` alterado de 250K para 10B em `block_alignment.py` (sem efeito real: o avaliador já passava 10B explicitamente)

**Resultado com 64M**: V7 completou em 1577,8 s, **80/80 páginas**, `failure_rate=0,000`. Página 4: status=ok, CER_visible=0,680. Zero resource_limits por página em todos os corpora.

### 5.3 Métricas indisponíveis (benchmark 64M)

| Corpus | Indisponíveis (64M) | Anterior (8M) | Recuperadas |
|---|---|---|---|
| V3 | 12 | 12 | 0 |
| V5 | 5 | 6 | 1 (wer_sequential) |
| V6 | 8 | 9 | 1 (aligned_block_text_cer) |
| V7 | 8 | FALHA TOTAL | ∞ (todas as por-página) |

**Breakdown dos indisponíveis (64M)**:
- `resource_limit`: cer_markdown_raw, cer_sequential_normalized, cer_sequential_visible + wer_sequential (4 por corpus exceto V5)
- `not_applicable`: cpf/cnpj/process_numbers e variantes por página (4–8 por corpus)
- `false_positive_only`: cpf, cpf_page em V6 (conta como aplicável, não indisponível)

---

## 6. Problemas Prioritários Identificados (Resumo)

Para análise completa com evidências, ver `docs/Problemas_Baseline_Tesseract.md`.



### P1 — Header Leakage Crítico (V6)
**Métrica**: `header_leakage_rate = 0,968`  
**Impacto**: 96,8% das páginas do V6 têm cabeçalhos de página incluídos no corpo do texto extraído. Causa `duplicate_content_rate=0,433`.  
**Módulo candidato**: `src/structured_pdf_text/layout/engine.py` ou `layout/heading.py` — filtragem de headers repetitivos de página.  
**Presente também em V3**: `header_leakage_rate=0,678`.

### P2 — Heading Level / Text (V3)
**Métricas**: `heading_level_accuracy=0,0`, `heading_text_cer=14,62`  
**Impacto**: Nenhum heading do V3 tem nível correto. 86 headings sem match no gabarito (unmatched_hyp).  
**Módulo candidato**: `src/structured_pdf_text/layout/heading.py`, `renderers/markdown.py`.  
**V5 não tem esse problema**: `heading_level_accuracy=1,0`, `heading_text_cer=0,386`.

### P3 — Ordem de Leitura Baixa (métrica legada) — ARTEFATO, NÃO PROBLEMA REAL
**Métrica legada**: `reading_order_accuracy` = 0,245 (V3), 0,213 (V5), 0,293 (V6)  
**Métrica canônica**: `pairwise_reading_order_accuracy` = 0,985 (V3), 0,994 (V5), 0,994 (V6)  
**Conclusão**: A ordem de leitura real é excelente (>98,5%). A métrica legada mede LCS/total_ref_blocks — se blocos do gabarito estão ausentes (tabelas não extraídas, headings perdidos), o LCS cai mesmo que a ordem dos blocos presentes esteja correta. Não é necessário corrigir o pipeline de ordenação. Corrigir P1 (header leakage) e P4T (tabelas) melhorará a cobertura de blocos e, por consequência, o valor legado.  
**Detalhes**: `docs/Problemas_Baseline_Tesseract.md` §C.P3.

### P4 — CER/WER Elevado (V3)
**Métricas**: `cer_raw=0,674`, `wer=0,826` (legado per-page agregado)  
**Impacto**: Alta taxa de substituições (0,167) e inserções (0,573 = conteúdo duplicado).  
**Correlação**: `insertion_rate=0,573` alinha com `header_leakage_rate=0,678` — cabeçalhos duplicados são a causa principal das inserções.  
**V5 muito melhor**: `cer_raw=0,346`, `wer=0,391`.

### P5 — Tabelas Incompletas (V3, V6)
**Métricas**: `table_recall=0,571` (V3), `0,591` (V6)  
**Impacto**: ~41% das tabelas não detectadas em V3, ~41% em V6.  
**V5 melhor**: `table_recall=0,909`.  
**Módulo candidato**: Detector de tabelas no pipeline Tesseract.

---

## 7. Acertos a Preservar

### V5 — Referência de Qualidade
- `heading_level_accuracy = 1,0` — níveis de heading completamente corretos
- `table_f1 = 0,952`, `table_recall = 0,909` — detecção de tabelas excelente
- `header_leakage_rate = 0,0` — sem vazamento de cabeçalhos (!)
- `numeric_exact_match = 0,955`, `currency_exact_match = 0,935` — dados numéricos precisos
- `date_exact_match = 1,0` — datas 100% corretas
- `table_content_f1 = 0,957` — conteúdo de tabelas muito bem preservado

### V6 — Pontos Positivos (corpus de erros)
- `heading_level_accuracy = 1,0` — mesmo no corpus de erros, níveis de heading corretos
- `heading_text_cer = 0,252` — texto dos headings razoavelmente preservado
- `numeric_exact_match = 0,976` — dados numéricos quase perfeitos
- `date_exact_match = 0,985` — datas excelentes
- `block_f1 = 0,874` — estrutura de blocos bem preservada

### V3 — Pontos Positivos (apesar do CER alto)
- `table_precision = 1,0` — quando detecta tabela, é uma tabela real (sem falsos positivos)
- `numeric_exact_match = 0,948` — dados numéricos robustos
- `date_exact_match = 1,0` — datas 100% corretas
- `cell_cer = 0,289` — conteúdo interno de células relativamente preciso

---

## 8. Consumo de Recursos — Limites Propostos

### 8.1 Medições da Baseline (extração apenas)

| Corpus | Peak RSS (extração) | Commit consumido (extração) | Commit avail início |
|---|---|---|---|
| V3 (144 pgs) | 272,7 MB | 0,61 GB | 24,64 GB |
| V5 (42 pgs) | 93,6 MB | 0,29 GB | 24,67 GB |
| V6 (95 pgs) | 279,4 MB | 0,52 GB | 24,67 GB |
| V7 (80 pgs) | 220,6 MB | 0,43 GB | 24,74 GB |

**Nota de limitação**: valores obtidos com monitoramento a cada 30 s. Picos transitórios podem ter sido ignorados. O consumo da avaliação de métricas (`compute_metrics.py` com 8 workers Python paralelos) NÃO está refletido acima. Estimativa adicional: 800 MB–1,5 GB durante avaliação.

**Tesseract usa ~35× menos memória que EasyOCR** (V3 EXHAUSTIVE EasyOCR: peak RSS ≈ 9,8 GB; V3 Tesseract: 272,7 MB).

### 8.2 Salvaguardas globais inalteradas

As proteções globais de abort por disponibilidade crítica são mantidas:
- RAM disponível: **4 GB** (ResourceMonitor)
- Commit disponível: **8 GB** (ResourceMonitor)

### 8.3 Limites propostos (margem 15%, calibrados para Tesseract)

**Fórmula pre-flight commit**: `COMMIT_ABORT(8 GB) + consumo_extração_tesseract × 1,15 + buffer_métricas(1,5 GB)`  
**Fórmula pre-flight RAM**: `max(RAM_ABORT(4 GB) + RSS_tesseract × 1,15 + 0,5 GB, 5 GB)`

| Corpus | Commit proposto (Tesseract) | Commit anterior (EasyOCR) | RAM proposta | RSS medido × 1,15 | Redução commit |
|---|---|---|---|---|---|
| V3 | 10,2 GB → **11 GB** | 19 GB | **5 GB** | 313 MB | −42% |
| V5 | 9,8 GB → **10 GB** | 10 GB | **5 GB** | 108 MB | 0% |
| V6 | 10,1 GB → **11 GB** | 14 GB | **5 GB** | 321 MB | −21% |
| V7 | 10,0 GB → **10 GB** | 13 GB | **5 GB** | 254 MB | −23% |

**Memória usada por Tesseract é irrelevante para o threshold**: O consumo de extração (280–320 MB RSS com 15% de margem) é ~200× menor que as proteções globais de abort (RAM≥4 GB, commit≥8 GB). O fator limitante real é o buffer de métricas (8 workers Python paralelos ≈ 1,5 GB estimado, não medido diretamente).

**Riscos residuais da calibração**:
1. Amostragem de 30 s pode não capturar picos transitórios de RAM durante OCR de imagens grandes
2. Buffer de métricas (1,5 GB) é estimativa conservadora — medição direta requer instrumentação adicional
3. Variação por conteúdo: páginas com muitas imagens OCR pesam mais; V6 tem 95 pgs vs. V5 com 42

**Recomendação**: Aplicar imediatamente os limites propostos (mantendo salvaguardas globais). Revisar após 3 extrações completas bem-sucedidas com monitoramento de pico a cada 5 s.

---

## 9. Documento de Problemas

Para diagnóstico técnico detalhado com evidências concretas de extração vs. gabarito, ver:

**[docs/Problemas_Baseline_Tesseract.md](Problemas_Baseline_Tesseract.md)**

Contém:
- **§A** — Estado de validade da baseline por corpus (V3✓ V5✓ V6✓ V7✗)
- **§B** — Problemas concretos por documento com exemplos exatos de páginas (texto extraído vs. gabarito)
- **§C** — Deep dive em P1–P5 com raiz técnica confirmada: header leakage, nível de headings V3, ordem de leitura (artefato), tabelas, falha V7
- **§D** — Análise especial V6 (corpus de erros): distribuição de cabeçalhos por página, sub-headings ausentes, tipos de tabelas não detectadas
- **§E** — Análise especial V7 (corpus de acertos): 9 características preservadas a proteger em futuras iterações (tabelas 12 colunas, moedas pt-BR, identificadores, multi-página sem duplicação)
- **§F** — Tabela de priorização com 10 problemas (F01–F10): gravidade, evidência, módulo candidato, viabilidade Tesseract-only, risco de regressão

## 10. Artefatos Gerados

```
output/tesseract/baseline/
├── baseline_run.log          # log completo da execução
├── baseline_status.json      # estado final: finished_partial
├── V3/
│   ├── extracted.md
│   ├── extracted_paginated.md
│   ├── extract_info.json
│   ├── run_manifest.json
│   ├── resource_profile.json
│   ├── page_progress.json
│   ├── metrics_v3_tesseract_baseline_tesseract_V3.json  ✓
│   ├── metrics_tesseract_baseline_tesseract_V3.json     ✓
│   ├── errors_tesseract_baseline_tesseract_V3.md        ✓
│   └── quality_summary.json
├── V5/  (mesma estrutura)    ✓ completo
├── V6/  (mesma estrutura)    ✓ completo
└── V7/
    ├── extracted.md           ✓
    ├── extracted_paginated.md ✓
    ├── extract_info.json      ✓
    ├── run_manifest.json      ✓
    ├── resource_profile.json  ✓
    ├── page_progress.json     ✓
    ├── quality_summary.json   ✓ (parcial)
    ├── metrics_v3_*.json      ✗ AUSENTE
    ├── metrics_tesseract_*.json ✗ AUSENTE
    └── errors_*.md            ✗ AUSENTE
```

---

## 11. Benchmark — Avaliação com 64M células

**Data**: 2026-10-23  
**Diretório**: `output/tesseract/benchmark_metrics_64m/`  
**Alterações locais do usuário**:
- `DEFAULT_MAX_EDIT_CELLS`: 8M → 64M (`edit_distance.py`)
- `ProcessPoolExecutor` paralelo por-página adicionado (`evaluator.py`)
- `DEFAULT_MAX_ALIGNMENT_PAIRS`: 250K → 10B (`block_alignment.py`) — sem efeito real

### 11.1 Tempos de avaliação (compute_metrics.py)

| Corpus | Páginas | Tempo (s) | Tempo (min) | Peak RSS | Peak priv | Min RAM livre | Min Commit livre |
|---|---|---|---|---|---|---|---|
| V7 | 80 | 1577,8 | 26,3 | 1.076 MB | 1.067 MB | 13,17 GB | 23,32 GB |
| V3 | 144 | 354,8 | 5,9 | 432 MB | 422 MB | 13,49 GB | 23,62 GB |
| V5 | 42 | 94,1 | 1,6 | 350 MB | 273 MB | 14,05 GB | 24,16 GB |
| V6 | 95 | 958,9 | 16,0 | 446 MB | 436 MB | 13,85 GB | 23,99 GB |
| **Total** | **361** | **2985,9** | **49,8** | | | | |

**Observações de timing**:
- V7 demora 26 min por ter páginas de CER elevado (página 4: CER=68%) → muitas iterações de band-doubling
- V6 demora 16 min (>V3) apesar de menos páginas: páginas mais densas (avg 1.192 bytes/pg vs. 481 bytes/pg do V3)
- V5 é o mais rápido (42 páginas, textos curtos, CER moderado)
- **8 workers Python paralelos** em todos os corpora; pico durante per-page parallelism

### 11.2 Resultado final (todas as páginas completadas)

| Corpus | RC | Páginas | Failure Rate | CER_visible* | WER | Pairwise R.O. | Table F1 | Cell Exact |
|---|---|---|---|---|---|---|---|---|
| V7 | 0 | 80/80 | 0,0000 | [rl] | [rl] | 0,9957 | 0,9655 | 0,9824 |
| V3 | 0 | 144/144 | 0,0069 | [rl] | [rl] | 0,9850 | 0,7273 | 0,6924 |
| V5 | 0 | 42/42 | 0,0000 | [rl] | 0,3470 | 0,9946 | 0,9524 | 0,9321 |
| V6 | 0 | 95/95 | 0,0000 | [rl] | [rl] | 0,9940 | 0,7429 | 0,6775 |

*[rl] = resource_limited (full-doc concatenado excede 64M células)

### 11.3 Métricas canônicas V7 (disponíveis pela primeira vez)

| Métrica | Valor |
|---|---|
| heading_detection_f1 | 0,9459 |
| heading_text_cer | 0,0939 |
| table_detection_f1 | 0,9655 |
| table_column_count_accuracy | 0,9333 |
| cell_content_exact_match | 0,9824 |
| cell_cer | 0,0629 |
| pairwise_reading_order_accuracy | 0,9957 |
| numeric_match_rate | 0,9924 |
| currency_match_rate | 1,0000 |
| text_coverage_recall | 0,9976 |
| block_order_pairwise_accuracy | 0,9978 |

### 11.4 Consistência matemática

**Todos os valores comuns entre baseline (8M) e benchmark (64M) são idênticos (delta=0)**. Confirmado em métricas legadas (grupo1–grupo5) e canônicas para V3, V5, V6. As melhorias são aditivas (novos valores em métricas antes resource_limited), não alterações de valores existentes.

### 11.5 Segurança dos recursos

- Sistema: 31,88 GB RAM total, 51,1 GB commit total
- Mínimo RAM livre observado: 13,17 GB (V7) — **seguro, acima de 4 GB**
- Mínimo commit livre observado: 23,32 GB (V7) — **seguro, acima de 8 GB**
- Nenhuma interrupção por recurso durante as 4 avaliações

---

## 12. Pendências

1. **CER sequencial full-doc**: Permanece resource_limited para documentos grandes (V3, V6, V7). A única solução seria budget ainda maior (ex.: 512M) ou implementação Cython. Não prioritário: métricas por-página são suficientes para o loop.
2. **Thresholds de pré-voo**: Configurar em `automation/config.py` conforme §8.3 + medições de avaliação do §11 (peak avaliação V7=1,05 GB).
3. **Loop de melhoria**: **AUTORIZADO** — iniciar com Linha A (header leakage) e Linha B (tabelas). Primeira run em `output/loop/tesseract/0001/`.
4. **Segunda medição V7**: Em andamento em background (`output/tesseract/benchmark_metrics_64m/V7_run2/`). Resultado pendente para comparação de estabilidade.
