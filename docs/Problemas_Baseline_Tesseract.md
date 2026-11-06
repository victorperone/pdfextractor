# Problemas da Baseline Tesseract — Registro Técnico Vivo

> **Protocolo de atualização**: este documento deve ser atualizado obrigatoriamente após cada run. Contém apenas problemas da **Série 2** (2026-11-05+). Histórico da Série 1 em `docs/Problemas_Baseline_Tesseract_SERIE1_historico.md`.

---

## §0 Estado Atual

| Campo | Valor |
|---|---|
| **Última atualização** | 2026-11-06 (pós-run 008 — Fix-F-V9-04 INCONCLUSIVO) |
| **Série** | 2 (nova — inclui V9) |
| **Branch** | `feat/tesseract-optimization` |
| **Correção aceita** | Fix-F-V9-02 — form_label_colon_pattern em text_tracks.py (cp_0122) |
| **Fase atual** | DECIDING → WAITING_FOR_RESOURCES (disco C: 100%) |
| **Engine** | tesseract, balanced/adaptive, pt-BR |
| **Esquema do avaliador** | `evaluation-3.0.0` |
| **Cron** | `a8e6758f` — ativo, 5 min, expira ~2026-11-12 |
| **Corpora ativos** | V3, V4, V5, V6, V7, V8, **V9** |
| **provisional_corrections_count** | 2 (Fix-F-V9-01 + Fix-F-V9-02 aceitos) |

### Métricas baseline — Runs 001+002 (sem correção) e Run 003 (Fix-F-V9-01)

| Métrica | V3 (144p) | V4 (224p) | V5 (42p) | V6 (95p) | V7 (80p) | V8 (90p) | **V9 (96p) baseline** | **V9 run 003** |
|---|---|---|---|---|---|---|---|---|
| `cer_markdown_raw` | 0.0508 | 0.2315 | 0.1253 | 0.0209 | 0.0089 | 0.0113 | **0.4849** | **0.4318 (−10.95%)** |
| `text_coverage_recall` | 0.9593 | — | 0.9210 | 0.9881 | 0.9994 | 0.9938 | **0.6577** | **0.6659 (+1.2%)** |
| `heading_detection_f1` | 0.8667 | — | 0.8571 | 0.9904 | 0.9891 | 0.9927 | **0.3846** | 0.3846 |
| `heading_level_match_rate` | **0.0000** | — | 0.9608 | 1.0000 | 1.0000 | 1.0000 | 0.9111 | 0.9111 |
| `heading_text_cer` | 0.1987 | — | 0.3442 | 0.0187 | 0.0258 | 0.0094 | **0.7195** | 0.6737 |
| `table_detection_f1` | 0.9740 | — | 1.0000 | 1.0000 | 1.0000 | 1.0000 | **0.7027** | 0.6847 (−2.6%) |
| `cell_cer` | 0.0400 | — | 0.0000 | 0.0148 | 0.0000 | 0.0006 | **1.2173** | — |
| `table_column_count_accuracy` | 0.9494 | — | 1.0000 | 1.0000 | 1.0000 | 1.0000 | **0.4167** | — |
| `reading_order_accuracy` | 1.0000 | — | 0.9998 | 1.0000 | 1.0000 | 1.0000 | 0.9988 | 0.9985 |
| `paragraph_detection_f1` | 0.8970 | — | 0.8215 | 0.9627 | 0.9679 | 0.9527 | **0.4570** | — |

**Run 003 decisão**: ACEITAR_PROVISORIAMENTE — V9 CER −10.95%, V3–V8 idênticos ao baseline. `table_detection_f1` V9 ligeiramente menor (−2.6%) esperado: remoção de tabelas falsas reduz falsos positivos que por acaso coincidiam com referência. `provisional_corrections_count` = 1.

---

## §P — Fila de Problemas (por prioridade decrescente)

### PRIORIDADE 1 — V9: Extração massivamente deficiente (CER=0.4849)

> V9 é o corpus mais novo e apresenta os piores resultados da série. É a prioridade máxima da série 2.

#### P-V9-01 | CRÍTICO | Cobertura de texto: apenas 65.8% do conteúdo detectado

- **Métrica afetada**: `text_coverage_recall = 0.6577` (baseline) → **0.6659** (run 003 com Fix-F-V9-01)
- **Impacto**: 34% do texto do gabarito não aparecia na extração (agora 33.4% com fix parcial)
- **Corpora afetados**: V9 exclusivamente
- **Causa raiz confirmada (análise run 002)**: Três categorias distintas de falha:
  - **Tipo A** (17 páginas): Imagens rasterizadas (DPI 115-155), Tesseract falha ao extrair conteúdo — difícil de corrigir sem melhoria de pré-processamento de imagem
  - **Tipo B** (11 páginas): Mix de texto nativo + imagens → tabela falsa via detecção de colunas
  - **Tipo C** (7 páginas, ✅ **corrigido por Fix-F-V9-01**): Zero imagens, `_detect_rectangular_grid` criava tabela cobrindo ~89% da página com bordas do formulário
- **Fix-F-V9-01** (run 003): Height guard em `_detect_rectangular_grid` → grid >85% altura rejeitado. CER −10.95%. Checkpoint `cp_0120`.
- **Próximo**: Tipo B (11 páginas) — false table via mix native+imagens

#### P-V9-02 | CRÍTICO | Detecção de headings: apenas 38.5% dos títulos detectados

- **Métrica afetada**: `heading_detection_f1 = 0.3846`, `heading_recall = 0.2980`
- **Impacto**: 70% dos headings de V9 não são detectados
- **Corpora afetados**: V9 (V3–V8 todos ≥ 0.857 F1)
- **Hipótese**: Headings de V9 têm características visuais distintas (tamanho de fonte, posicionamento) que a heurística de detecção atual não captura
- **Comparação**: V6=0.9904, V7=0.9891, V8=0.9927 — problema isolado em V9

#### P-V9-03 | CRÍTICO | Texto de headings corrompido (CER=0.7195)

- **Métrica afetada**: `heading_text_cer = 0.7195`
- **Impacto**: Dos headings detectados, 72% do texto está errado
- **Corpora afetados**: V9 (demais ≤ 0.344)
- **Hipótese**: Tesseract tem baixa acurácia em texto de heading de V9 — possivelmente texto em fonte especial, negrito ou sobre fundo não-branco

#### P-V9-04 | CRÍTICO | Tabelas: 30% não detectadas, conteúdo completamente errado

- **Métricas afetadas**:
  - `table_detection_f1 = 0.7027` (recall=0.6393 — 36% de tabelas faltando)
  - `table_column_count_accuracy = 0.4167` — colunas erradas em 58% das tabelas detectadas
  - `cell_cer = 1.2173` — conteúdo de células com mais de 100% de erros (inserções + substituições)
  - `cell_content_exact_match = 0.3865` — apenas 39% das células corretas
  - `cell_alignment_accuracy = 0.3955` — alinhamento de células quase aleatório
- **Impacto**: Tabelas de V9 praticamente ilegíveis
- **Corpora afetados**: V9 (V5–V8 todas com `cell_cer ≤ 0.015`)
- **Hipótese**: V9 contém tabelas rasterizadas (imagem) ou com layout complexo — Tesseract não reconhece estrutura tabular; extrator interpreta incorretamente

#### P-V9-05 | ALTO | Detecção de parágrafos: apenas 45.7% dos limites corretos

- **Métrica afetada**: `paragraph_detection_f1 = 0.4570`
- **Impacto**: Metade das quebras de parágrafo erradas — afeta a estrutura lógica do documento
- **Corpora afetados**: V9 (demais ≥ 0.820)
- **Hipótese**: Consequência direta do CER alto — quando o texto não é extraído, os blocos de parágrafo são fragmentados ou fundidos incorretamente

#### P-V9-06 | MÉDIO | Dados críticos com recall incompleto

- **Métricas afetadas**:
  - `numeric = 0.8445` (recall de números: 15.6% perdidos)
  - `dates = 0.8451` (recall de datas: 15.5% perdidas)
  - `numeric_page = 0.8074` (por página: 19.3% perdidos)
- **Impacto**: Dados financeiros e datas do documento V9 não são extraídos corretamente
- **Corpora afetados**: V9 (V7 e V8 com 1.0000; V3 e V6 com ≥ 0.962)
- **Observação**: CPF, CNPJ e moeda em bom nível (≥ 0.909)

---

### PRIORIDADE 2 — V3: Heading levels incorretos (crítico estrutural)

#### P-V3-01 | ALTO | Níveis de heading todos errados (H1/H2/H3 indiferenciados)

- **Métrica afetada**: `heading_level_match_rate = 0.0000`
- **Impacto**: NENHUM heading de V3 tem o nível correto — todos são promovidos ou rebaixados incorretamente
- **Corpora afetados**: V3 exclusivamente (V5=0.9608, V6-V8=1.0000, V9=0.9111)
- **Hipótese**: V3 usa `--reference` (sem `expected_markdown` por página no manifesto) — a heurística de níveis não funciona para o perfil de headings de V3 (documento de 144 páginas com estrutura profunda)
- **Prioridade alta**: afeta toda navegação estrutural do documento mais longo

#### P-V3-02 | MÉDIO | 18.75% dos headings não detectados

- **Métrica afetada**: `heading_recall = 0.8125`, `heading_detection_f1 = 0.8667`
- **Impacto**: ~19% dos títulos de V3 ausentes na extração
- **Corpora afetados**: V3 (V6=0.9847, V7=0.9784, V8=0.9927)
- **Hipótese**: Headings em páginas de alta densidade (V3 tem 144 páginas com múltiplas seções) não satisfazem os critérios de promoção atuais

#### P-V3-03 | MÉDIO | Texto de headings com 19.9% de erro

- **Métrica afetada**: `heading_text_cer = 0.1987`
- **Impacto**: Erros OCR nos títulos de V3
- **Corpora afetados**: V3 (demais ≤ 0.034 exceto V5=0.344 e V9=0.720)

#### P-V3-04 | BAIXO | Listas: precisão de grupo baixa (0.700)

- **Métrica afetada**: `list_group_detection_precision = 0.7000`
- **Impacto**: 30% de falsos positivos em grupos de lista — blocos não-lista classificados como lista
- **Corpora afetados**: V3 (V5-V8 ≥ 0.820)

---

### PRIORIDADE 3 — V5: Headings e parágrafos com recall baixo

#### P-V5-01 | MÉDIO | 25% dos headings não detectados

- **Métrica afetada**: `heading_recall = 0.7500`, `heading_detection_f1 = 0.8571`
- **Impacto**: 25% dos títulos de V5 ausentes
- **Corpora afetados**: V5 (outros ≥ 0.812)

#### P-V5-02 | MÉDIO | Heading text CER = 0.344

- **Métrica afetada**: `heading_text_cer = 0.3442`
- **Impacto**: Erros OCR significativos nos títulos de V5 (melhor que V9, pior que V3-V8)
- **Corpora afetados**: V5

#### P-V5-03 | BAIXO | Parágrafo recall = 78.1%

- **Métrica afetada**: `paragraph_detection_recall = 0.7814`
- **Impacto**: 22% das quebras de parágrafo de V5 não detectadas
- **Corpora afetados**: V5 (demais ≥ 0.927)

---

### PRIORIDADE 4 — V6/V8: Problemas pontuais de baixo impacto

#### P-V6-01 | BAIXO | Conteúdo de células: 6.2% de erros exatos

- **Métrica afetada**: `cell_content_exact_match = 0.9382`, `cell_cer = 0.0148`
- **Impacto**: Pequena proporção de células com texto incorreto
- **Corpora afetados**: V6 (V5, V7 com 1.0000; V8=0.9955)

#### P-V8-01 | BAIXO | Dados numéricos: recall 1.1% abaixo do perfeito

- **Métrica afetada**: `numeric = 0.9891`
- **Impacto**: Muito pequeno — pode ser arredondamento ou formatação
- **Corpora afetados**: V8

---

### PRIORIDADE 5 — V7: Sem problemas significativos (referência de excelência)

V7 atingiu pontuação perfeita (1.0000) em 55 de 55 métricas avaliadas. Serve como referência do melhor resultado possível com o pipeline atual.

---

## §V4 — Resultados confirmados

V4 (224 páginas) extraído com sucesso na run 002: `cer_markdown_raw = 0.231543` — idêntico ao best_known série 1 (run 0091).

Nenhuma regressão em V4. O threshold de memória foi reduzido para 11.0 GB por instrução do usuário em 2026-11-05 (Tesseract usa muito menos RAM que EasyOCR).

---

## §Histórico de Runs — Série 2

| Run | Data | V3 | V4 | V5 | V6 | V7 | V8 | V9 | Status | Fix |
|---|---|---|---|---|---|---|---|---|---|---|
| **001** | 2026-11-05 | 0.0508 | pulado | 0.1253 | 0.0209 | 0.0089 | 0.0113 | 0.4849 | finished_partial | baseline |
| **002** | 2026-11-05 | 0.0508 | 0.2315 | 0.1253 | 0.0209 | 0.0089 | 0.0113 | 0.4849 | finished_complete | baseline |
| **003** | 2026-11-05 | 0.0508 | 0.2315 | 0.1253 | 0.0209 | 0.0089 | 0.0113 | **0.4318** | finished_complete | Fix-F-V9-01 ✅ |
| **004** | 2026-11-06 | pulado | pulado | 0.1253 | 0.0209 | 0.0089 | 0.0113 | **0.4285** | finished_partial | Fix-F-V9-02 (V3+V4 sem memória) |
| **005** | 2026-11-06 | 0.0508 | 0.2315 | 0.1253 | 0.0209 | 0.0089 | 0.0113 | **0.4285** | finished_complete | Fix-F-V9-02 ✅ best_known |
| **006** | 2026-11-06 | 0.0508 | 0.2315 | 0.1253 | 0.0209 | 0.0089 | FALHOU | FALHOU | disco_cheio | Fix-F-V9-03 (disco C: 100%) |
| **007** | 2026-11-06 | — | — | — | — | — | 0.0113 | 0.4285 | retry_parcial | Fix-F-V9-03 V8+V9 → REVERTER |
| **008** | 2026-11-06 | — | — | 0.1253 | — | — | — | 0.4285 | parcial_INVALIDO | Fix-F-V9-04 → INCONCLUSIVO |

---

## §Resolvidos — Série 2

### Fix-F-V9-02 | form_label_colon_pattern — Supressão de rótulo de formulário como tabela (V9)

- **Arquivo**: `src/structured_pdf_text/tables/text_tracks.py` — classe `ProseVsTableClassifier`
- **Problema**: Rótulos de formulário no padrão "Campo: valor" eram interpretados como linhas de tabela borderless
- **Fix**: Novo método `_is_form_label_colon_pattern()` → rejeita candidatos com padrão "PALAVRA: valor" em proporção alta
- **Checkpoint**: `cp_0122`
- **Testes**: adicionados em `tests/test_audit_mixed_visual_tables.py`
- **Run validação**: 005 (completo) — V9 CER 0.4318 → 0.4285 (−0.76%), table_prec +8.7%, cell_cer −5.9%
- **Decisão**: ACEITAR_PROVISORIAMENTE — contador: 1→2

---

### Fix-F-V9-03 | _group_adjacent_text_regions + Pass2 em ocr_tracks.py — REVERTER

- **Arquivo**: `src/structured_pdf_text/tables/ocr_tracks.py`
- **Hipótese**: Tesseract gera uma LayoutRegion por parágrafo OCR (1 linha) → `_candidates()` vê muito poucas linhas
- **Fix**: Pass 2 agrupando regiões TEXT curtas adjacentes em composita para detector borderless
- **Runs**: 006 (disco cheio, V9 não avaliado), 007 (V8+V9 retry) — V9 CER=0.4285=best_known
- **Diagnóstico**: Código não ativou para V9 — `image_boxes` vazio → `detect_ocr_track_tables()` não chamado para tabelas nativas
- **Decisão**: REVERTER — zero efeito, código morto para V9. cp_0123 restaurado. Contador=2 (não incrementado).

---

### Fix-F-V9-04 | _group_short_regions + Pass2 em detector.py — INCONCLUSIVO

- **Arquivo**: `src/structured_pdf_text/tables/detector.py` — `detect_tables_native()`
- **Hipótese**: Tesseract gera LayoutRegion com 1-2 native_lines → `_candidates()` requer ≥3 linhas → tabela perdida
- **Fix**: `_group_short_regions()` agrupa regiões curtas adjacentes em região composta para Pass 2
- **Checkpoint**: `cp_0124`
- **Testes**: `tests/test_v9_grouped_borderless.py` — 6/6 PASS
- **Run**: 008 (parcial V5+V9) — V9 CER=0.4285=best_known, tbl_f1=0.7103=best_known, fn=23 páginas (inalterado)
- **Diagnóstico zero efeito**: Hipótese errada — páginas 42/82 de V9 usam nativo PDF (não OCR curto). Regiões não são curtas (≥3 linhas cada). `_group_short_regions()` não ativa. Causa real: layout engine/reading_order reordena colunas de tabela nativa.
- **Código**: PRESERVADO (válido para outros casos com regiões curtas Tesseract)
- **Decisão**: INCONCLUSIVO — zero efeito, sem regressão. Contador=2 (não incrementado).

---

### Fix-F-V9-01 | Tipo C — Tabela falsa por borda de formulário (7 páginas V9)

- **Arquivo**: `src/structured_pdf_text/tables/detector.py` — função `_detect_rectangular_grid`
- **Problema**: Bordas de formulário (2 horizontais topo/base + ~20 retângulos internos) criavam grid cobrindo 89% da altura da página → tabela falsa absorvia todo o conteúdo
- **Fix**: Guard de altura — grid rejeitado se `y1 - y0 > limit.height * 0.85`
- **Páginas corrigidas**: p042, p047, p057, p068, p077, p090, p094 (Tipo C de V9)
- **Checkpoint**: `cp_0120` (pre-fix-v9-false-table)
- **Testes**: `tests/test_v9_false_table_guard.py` — 3/3 PASS
- **Run validação**: 003 — V9 CER 0.4849 → 0.4318 (−10.95%), V3–V8 inalterados
- **Decisão**: ACEITAR_PROVISORIAMENTE

---

## §Análise V9 — Classificação de Páginas por Tipo de Falha (run 002)

Diagnóstico realizado com `PdfiumNativeEvidenceSource` + `ComplexityAnalyzer` sobre as 96 páginas do V9.

| Tipo | Páginas | Padrão | Mecanismo de Falha |
|---|---|---|---|
| **A** | 17 | Imagens cobrindo 5-100% da página, `useful_len < 200` | Tesseract não reconhece imagens de baixa DPI (115-155 DPI). `raster_primary` só trigga quando `image_coverage >= 0.75`. |
| **B** | 11 | Imagens presentes, texto nativo moderado, resultado com `<table>` | Mix OCR+nativo interpretado como tabela multi-coluna |
| **C** | 7 | Zero imagens, `useful_len >= 680`, `path_coverage ≈ 0.24-0.26` | `_detect_rectangular_grid` cria grid cobrindo ~89% da página com bordas do formulário |
| **OK** | 61 | Diversos | Extração correta |

**Páginas Tipo C**: p042, p047, p057, p068, p077, p090, p094

**Diagnóstico p068 (exemplar)**:
- 68 paths no total; apenas 2 qualificam como horizontais (bordas superior e inferior da página a y≈47 e y≈795)
- Zero verticals candidatos
- `_detect_strict_grid` → `_detect_rectangular_grid` com todos os 68 paths
- Grid resultante cobre 89% da altura da página → tabela falsa absorvendo heading + metadados + parágrafos + tabela real
- `grid_line_count = 0` (complexidade não detecta grid real)

**Fix planejado (Fix-F-V9-01)**: Em `_detect_rectangular_grid`, rejeitar grids que cobrem > 85% da altura da região limite. Discriminador generalista: bordas de formulário de página inteira geram grids de 85%+ de altura; tabelas de dados reais são subseções da página.

---

## §Notas Técnicas

### Fix de compatibilidade do avaliador com V9 (2026-11-05)
O manifesto V9 usa a chave `page_number` em vez de `page`/`page_num` usada pelos demais corpora. O avaliador (`reference_inputs.py` linha 41 e `reporting.py` linha 22) foi atualizado para aceitar `page_number` como fallback. Checkpoint: `pre-fix-v9-page-number-key`.

### Threshold V4 reduzido (2026-11-05)
`CORPUS_MIN_COMMIT_GB["V4"]` reduzido de 15.0 → 11.0 GB por instrução do usuário. Tesseract usa significativamente menos memória que EasyOCR (baseline de EasyOCR que motivou 15 GB não se aplica ao pipeline Tesseract atual).
