# Problemas da Baseline Tesseract — Registro Técnico

> **Escopo**: Série 3 (executor `scripts/run_tesseract_batch_wsl.py`, diretórios `output/tesseract/NNN`).
> A versão anterior deste documento (Série 2, runs 001–008 via `run_loop.py`) está preservada no git (`git show 5cc81d6:docs/Problemas_Baseline_Tesseract.md`); um resumo está em §7.
> **Protocolo**: atualizar após cada rodada. Um problema só é marcado como RESOLVIDO quando o critério mensurável dele for atendido numa rodada completa, sem regressão relevante nos demais corpora.
> **Classificação usada a partir da 003**: RESOLVIDO (critério atendido em rodada completa) · CORRIGIDO — VALIDADO PONTUALMENTE (testes por página, aguarda rodada completa) · PARCIAL · PENDENTE · INFO.

---

## §0 Estado atual

| Campo | Valor |
|---|---|
| **Última atualização** | 2026-11-12 — análise da RUN 003 e correções pós-003 validadas por página |
| **Branch** | `feat/tesseract-optimization` (alterações pós-003 não commitadas) |
| **Baseline imutável** | `output/tesseract/001/` |
| **Baseline corrigida** | `output/tesseract/002/` (T3-P00) |
| **Baseline atual** | `output/tesseract/003/` (T3-P01, T3-P03; V10 incluído) |
| **Próxima rodada** | `python3 -u scripts/run_tesseract_batch_wsl.py 004` (manual) — valida as correções pós-003 (§5) |
| **Configuração** | balanced · pt-BR · adaptive · tesseract · 6 threads · Markdown paginado · supplemental-audit |
| **Avaliador** | `evaluation-3.0.0`, métricas 3.1.0 — **não alterado** |
| **Corpus/gabaritos/manifestos** | **não alterados por nós**. O diretório `corpus/V10/` foi substituído externamente em 2026-11-12, entre 07:56 e 07:59, durante a RUN 003 (ver T3-P15) |

### Situação dos problemas

| ID | Gravidade | Prioridade | Componente | Status |
|---|---|---|---|---|
| T3-P00 | CRÍTICO | — | executor | **RESOLVIDO** (002) |
| T3-P01 | CRÍTICO | — | `api.py` | **RESOLVIDO** (003) |
| T3-P02 | ALTO | P1 | backend Tesseract | **CORRIGIDO — VALIDADO PONTUALMENTE** |
| T3-P03 | MÉDIO | — | `api.py` | **RESOLVIDO** (003) |
| T3-P04 | INFO | P3 | avaliador | aberta |
| T3-P05 | MÉDIO | P3 | montagem de linhas nativas | DOCUMENTADO — fora do escopo |
| T3-P06 | ALTO | P2 | headings | PENDENTE (parte era consequência de T3-P10) |
| T3-P07 | MÉDIO | P2 | V5 tabelas sem borda/ordem | PENDENTE (sem mudança na 003) |
| T3-P08 | MÉDIO | P3 | dados críticos V9 | MELHOROU na 003 — monitorar |
| T3-P09 | INFO | — | `ocr/tesseract/postprocess.py` | RISCO MATERIALIZADO e mitigado em um caso (T3-P25) |
| T3-P10 | CRÍTICO | P0 | reconstrução de linhas OCR + ordem de leitura | **CORRIGIDO — VALIDADO PONTUALMENTE** |
| T3-P11 | ALTO | — | executor (V10) | REABERTO na 003 → corrigido via T3-P15 |
| T3-P12 | MÉDIO | P2 | detecção visual de tabelas | PENDENTE — causa medida |
| T3-P13 | BAIXO | — | regressões pontuais 001→002 | sem mudança na 003 (saídas V3–V8 idênticas) |
| T3-P14 | INFO | — | suíte de testes | 13 falhas pré-existentes (inalteradas) |
| T3-P15 | ALTO | P1 | executor | **CORRIGIDO — VALIDADO POR TESTES** |
| T3-P16 | MÉDIO | P2 | classificação de página OCR vazia (`api.py`) | **CORRIGIDO — VALIDADO PONTUALMENTE** |
| T3-P17 | ALTO | P1 | `tables/ocr_tracks.py` | **PARCIAL** — falsos positivos em colunas eliminados; L01/L02 inclinadas restantes |
| T3-P18 | ALTO | P1 | reconstrução de linhas OCR | **CORRIGIDO — VALIDADO PONTUALMENTE** |
| T3-P19 | INFO | — | avaliador (`duplicate_excess_rate`) | INTERPRETAÇÃO DOCUMENTADA — não alterar |
| T3-P20 | MÉDIO | P2 | segmentação de parágrafos OCR | PARCIAL (melhorou com T3-P10) |
| T3-P21 | INFO | P3 | V4: recall ≈ 0.85 em OCR de coluna única | ABERTA (T3-P23 explica só uma parte) |
| T3-P22 | MÉDIO | P3 | montagem de linhas nativas multicoluna | NOVO — documentado, fora do escopo |
| T3-P23 | CRÍTICO | — | backend Tesseract (`_parse_tsv`) | **CORRIGIDO — VALIDADO PONTUALMENTE** |
| T3-P24 | BAIXO | — | `layout/ocr_aware.py` (listas) | **CORRIGIDO — VALIDADO PONTUALMENTE** |
| T3-P25 | ALTO | — | `ocr/tesseract/postprocess.py` | **CORRIGIDO — VALIDADO PONTUALMENTE** |

---

## §1 Rodadas da Série 3

### §1.1 Resumo operacional

| Corpus | Páginas | Gabarito | Extração 001 → 002 → 003 (s) | Métricas 003 (s) | Aplicáveis 001 → 002 → 003 | Resultado 001 / 002 / 003 |
|---|---|---|---|---|---|---|
| V3 | 144 | reference | 84 → 379 → 357 | 3.0 | 57 → 61 → 61 | ok / ok / ok |
| V4 | 224 | manifesto | 606 → 2491 → 2380 | 222.1 | 57 → 61 → 61 | ok / ok / ok |
| V5 | 42 | reference | 21 → 91 → 86 | 1.5 | 63 → 67 → 67 | ok / ok / ok |
| V6 | 95 | reference | 94 → 658 → 619 | 3.0 | 59 → 63 → 63 | ok / ok / ok |
| V7 | 80 | reference | 60 → 169 → 165 | 2.9 | 61 → 65 → 65 | ok / ok / ok |
| V8 | 90 | reference | 99 → 410 → 385 | 4.0 | 61 → 65 → 65 | needs_review / needs_review / **ok** |
| V9 | 96 | manifesto | 170 → 559 → 592 | 14.2 | 63 → 67 → 67 | ok / ok / ok |
| V10 | 84 | ref. paginada (pré-verificação) | — → — → 609 | 0.3 (falhou) | — → — → (61*) | — / — / **metrics_error** |

\* 61 métricas aplicáveis ao recalcular a 003 do V10 com o manifesto, no scratchpad, sem alterar `output/` (ver T3-P15).

### §1.2 Métricas-chave, 001 → 002 (histórico)

`n/a` = métrica não aplicável (sem objeto no gabarito; não é falha). Na 001, todas as métricas de tabela tinham status `false_negative_only` (ver T3-P00).

| Métrica | V3 | V4 | V5 | V6 | V7 | V8 | V9 |
|---|---|---|---|---|---|---|---|
| `cer_markdown_raw` | 0.234 → 0.051 | 0.257 → 0.237 | 0.293 → 0.125 | 0.073 → 0.021 | 0.109 → 0.009 | 0.078 → 0.011 | 0.476 → 0.418 |
| `cer_sequential_normalized` | 0.064 → 0.039 | 0.242 → 0.231 | 0.218 → 0.109 | 0.017 → 0.008 | 0.057 → 0.001 | 0.043 → 0.006 | 0.446 → 0.392 |
| `wer_sequential` | 0.076 → 0.051 | 0.331 → 0.318 | 0.269 → 0.116 | 0.031 → 0.015 | 0.063 → 0.003 | 0.052 → 0.007 | 0.484 → 0.410 |
| `text_coverage_recall` | 0.963 → 0.959 | 0.833 → 0.832 | 0.927 → 0.921 | 0.988 → 0.988 | 1.000 → 0.999 | 0.994 → 0.994 | 0.653 → 0.664 |
| `text_coverage_precision` | 0.961 → 0.979 | 0.889 → 0.902 | 0.885 → 0.995 | 0.989 → 0.992 | 0.950 → 0.997 | 0.958 → 0.998 | 0.932 → 0.979 |
| `heading_detection_f1` | 0.867 → 0.867 | 0.500 → 0.502 | 0.791 → 0.857 | 0.973 → 0.990 | 0.950 → 0.989 | 0.986 → 0.993 | 0.403 → 0.426 |
| `heading_level_match_rate` | 0.000 → 0.000 | 0.000 → 0.000 | 0.961 → 0.961 | 0.988 → 1.000 | 1.000 → 1.000 | 1.000 → 1.000 | 0.927 → 0.924 |
| `heading_text_cer` | 0.199 → 0.199 | 0.634 → 0.631 | 0.388 → 0.344 | 0.043 → 0.019 | 0.045 → 0.026 | 0.013 → 0.009 | 0.693 → 0.667 |
| `paragraph_detection_f1` | 0.601 → 0.897 | 0.299 → 0.371 | 0.556 → 0.822 | 0.744 → 0.963 | 0.703 → 0.968 | 0.692 → 0.953 | 0.373 → 0.524 |
| `list_group_detection_f1` | 0.632 → 0.824 | 0.500 → 0.560 | 0.765 → 0.867 | 0.877 → 1.000 | 1.000 → 1.000 | 0.931 → 1.000 | 0.947 → 0.857 |
| `list_item_detection_f1` | 0.892 → 0.969 | 0.840 → 0.875 | 0.959 → 1.000 | 0.934 → 1.000 | 1.000 → 1.000 | 0.975 → 1.000 | 1.000 → 0.867 |
| `pairwise_reading_order_accuracy` | 1.000 → 1.000 | 1.000 → 1.000 | 0.996 → 1.000 | 1.000 → 1.000 | 0.999 → 1.000 | 1.000 → 1.000 | 0.997 → 0.999 |
| `table_detection_f1` | 0.000 → 0.974 | 0.000 → 0.875 | 0.000 → 1.000 | 0.000 → 1.000 | 0.000 → 1.000 | 0.000 → 1.000 | 0.000 → 0.789 |
| `table_column_count_accuracy` | 0.000 → 0.949 | 0.000 → 0.707 | 0.000 → 1.000 | 0.000 → 1.000 | 0.000 → 1.000 | 0.000 → 1.000 | 0.000 → 0.545 |
| `cell_content_exact_match` | 0.000 → 0.953 | 0.000 → 0.621 | 0.000 → 1.000 | 0.000 → 0.939 | 0.000 → 1.000 | 0.000 → 0.996 | 0.000 → 0.498 |
| `cell_cer` | 1.000 → 0.040 | 1.000 → 0.355 | 1.000 → 0.000 | 1.000 → 0.014 | 1.000 → 0.000 | 1.000 → 0.001 | 1.000 → 0.618 |
| `numeric` | 0.957 → 0.972 | 0.890 → 0.891 | 0.871 → 0.993 | 0.953 → 0.967 | 0.872 → 1.000 | 0.917 → 0.989 | 0.743 → 0.852 |
| `currency` | 0.963 → 0.963 | 0.890 → 0.884 | 1.000 → 1.000 | 0.961 → 1.000 | 1.000 → 1.000 | 1.000 → 1.000 | 0.831 → 0.914 |
| `dates` | 1.000 → 1.000 | 0.953 → 0.953 | 1.000 → 1.000 | 0.993 → 0.993 | 1.000 → 1.000 | 0.998 → 0.998 | 0.784 → 0.859 |
| `duplicate_excess_rate` | 0.447 → 0.056 | 0.813 → 0.702 | 0.387 → 0.055 | 0.238 → 0.032 | 0.318 → 0.015 | 0.312 → 0.039 | 0.579 → 0.412 |

Métricas não aplicáveis em ambas as rodadas: `process_numbers` (nenhum corpus), `cpf` (só V5/V9), `cnpj`/`identifiers_combined` (não em V3/V4), `percent` (não em V6). Na 001 também eram n/a `table_row_order_accuracy`, `table_column_order_accuracy`, `table_page_location_accuracy` e `cell_content_exact_match_matched`, por falta de tabelas.

**Consistência**: os valores de `cer_markdown_raw` da 002 coincidem com os melhores da Série 2 (V3 0.0508, V5 0.1253, V6 0.0209, V7 0.0089, V8 0.0113). Isso confirma que a 001 media uma configuração diferente da canônica.

### §1.3 RUN 003 — comparação com a 002

- **Comparabilidade**: avaliador `evaluation-3.0.0`/métricas 3.1.0 (commit `db2ecb8` registrado), mesma configuração e mesmos gabaritos de V3–V9. Os `*_extracted_paginated.md` de **V3–V8 são idênticos byte a byte** aos da 002 (md5). A extração é determinística, e T3-P01/T3-P03 não tiveram efeito colateral nesses corpora. Só o V9 mudou.
- **V9 002 → 003**: `cer_sequential_normalized` 0.392 → **0.214** · `wer_sequential` 0.410 → 0.260 · `text_coverage_recall` 0.664 → **0.948** · `numeric` 0.852 → 0.934 · `dates` 0.859 → 0.964 · `cpf`/`cnpj` 0.909 → 1.000 · `paragraph_detection_precision` 0.496 → 0.423 (texto recuperado sai fragmentado, ver T3-P20) · `duplicate_excess_rate` 0.412 → 0.480 (artefato da métrica, ver T3-P19) · headings e tabelas sem mudança.
- **V8**: `success`/`valid`, CER 0.006 (T3-P03 atendido).
- **V10 (baseline nova, calculada no scratchpad com o manifesto)**: CER 0.516 · WER 0.641 · recall 0.876 · precisão 0.943 · `heading_recall` 0.295 · `heading_text_cer` 0.826 · `paragraph_detection_f1` 0.196 · `table_detection_f1` 0.545 (precisão 0.391: 63 tabelas para 20 esperadas) · `table_row_detection_precision` 0.191 · `cell_cer` 7.05 · `numeric` 0.927 · `dates` 0.967. É o pior corpus da série.
- **Desempenho**: o salto de tempo foi 001 → 002 (×3–7). A 003 ficou praticamente igual à 002 (V9 +6%, pelo OCR recuperado por T3-P01).

**Concentração dos erros na 003** (CER por página > 0.15, separado por recall): no V4, V9 e V10 o erro dominante é **conteúdo presente, mas na ordem errada** (recall ≈ 0.99, CER ≈ 0.7). As páginas multicoluna com OCR respondem por **54% dos erros de edição do V4 (21 págs.), 69% do V9 (20 págs.) e 96% do V10 (35 págs.)**. Sem elas, o CER do documento seria 0.129 / 0.094 / 0.088. No V4, as páginas `single` com a mesma degradação (ruído, JPEG, blur, sombra) têm CER de 0.03 a 0.06, e as `two_columns` correspondentes, de 0.72 a 0.75. É a origem de T3-P10.

### §1.4 Validação pontual das correções pós-003 (2026-11-12)

**Método** (sem rodada completa, sem tocar em `output/`):
- **Harness**: `pagerun.py`, no scratchpad da sessão. Usa `build_config()` do executor (balanced, pt-BR, tesseract, adaptive, 6 threads) restrito com `ExtractorConfig.page_indices`. Confere que `document.pages` e o progresso contêm **só** as páginas pedidas e gera o Markdown paginado com os números de página originais.
- **Métricas**: calculadas pelo `scripts/compute_metrics.py` oficial com escopo `selected_pages` e o mesmo gabarito escolhido pela pré-verificação.
- **Comparação**: ANTES (código de `HEAD` + T3-P01/P03, igual à 003) × FINAL (código final), nas mesmas páginas.
- **Atenção**: métricas de subconjunto **não são comparáveis** às métricas documentais das rodadas completas. Em páginas isoladas, por exemplo, a supressão de cabeçalho repetido tem menos contexto.

**Páginas usadas** (alvos + controles):

| Corpus | Páginas |
|---|---|
| V3 | 20, 60, 80, 96, 97, 116, 138 |
| V4 | 10, 50, 81, 82, 85, 86, 105, 107, 110, 111, 113, 116, 135, 136, 140, 150, 160, 190, 220 |
| V5 | 6, 10, 17, 20, 37 |
| V6 | 3, 31, 47 |
| V7 | 1, 25, 53 |
| V8 | 22, 39, 46 |
| V9 | 2, 4, 8, 9, 14, 17, 25, 31, 41, 44, 50, 53, 62, 67, 72, 81, 84 |
| V10 | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 13, 14, 18, 19, 20, 21, 24, 25, 27, 33, 40, 41, 50, 57, 64, 72, 80 |

**Resultado no escopo selecionado (ANTES → FINAL)**:

| Corpus | CER seq. | WER | Recall | Precisão | `paragraph_f1` | `table_f1` | `cell_content_exact` | `cell_cer` | `numeric` | `currency` | `dates` |
|---|---|---|---|---|---|---|---|---|---|---|---|
| V3 | 0.316 → **0.108** | 0.335 → 0.124 | 0.721 → 0.921 | 0.889 → 0.962 | 0.372 → 0.629 | 0.667 → 1.000 | 0.887 → 1.000 | 0.108 → 0.000 | 0.921 → 0.990 | 0.824 → 1.000 | n/a |
| V4 | 0.540 → **0.098** | 0.654 → 0.230 | 0.674 → 0.829 | 0.810 → 0.885 | 0.167 → 0.209 | 0.857 → 1.000 | 0.369 → 0.500 | 0.488 → 0.215 | 0.803 → 0.844 | 0.725 → 0.982 | 0.911 → 0.996 |
| V5 | 0.206 → 0.206 | = | = | = | = | = | = | = | = | = | n/a |
| V6 | 0.034 → 0.034 | = | = | = | = | = | = | = | = | = | = |
| V7 | 0.084 → 0.084 | = | = | = | = | = | = | = | = | = | n/a |
| V8 | 0.110 → 0.110 | = | = | = | = | = | = | = | = | = | = |
| V9 | 0.485 → **0.201** | 0.586 → 0.219 | 0.862 → 0.865 | 0.983 → 0.986 | 0.305 → 0.332 | 0.800 → 0.846 | 0.386 → 0.383 | 0.727 → 0.739 | 0.908 → 0.905 | 0.949 → 0.949 | 0.947 → 0.947 |
| V10 | 0.554 → **0.283** | 0.700 → 0.364 | 0.867 → 0.918 | 0.924 → 0.921 | 0.134 → 0.314 | 0.545 → 0.923 | 0.253 → 0.784 | 8.602 → 0.237 | 0.913 → 0.902 | 1.000 → 1.000 | 0.973 → 0.985 |

V10 no escopo, além da tabela:
- **Listas**: precisão de itens 0.765 → 0.886; de grupos 0.455 → 0.667.
- **Títulos**: `heading_level_match_rate` 0.967 → 0.933.
- **Status do documento**: `partial_success` → `success`/`valid`.

**CER por grupo de páginas** (soma das edições ÷ soma dos caracteres de referência):

| Grupo | ANTES → FINAL |
|---|---|
| Multicoluna (T3-P10): V4 82/86/116/136/140, V9 4/8/9/31/41/50/53/67/84, V10 5/6/8/13/18/19/21/24/25/40/41/50 | 0.676 → **0.238** |
| Inclinação, coluna única (T3-P18): V3 80, V4 105/107/135, V9 17/44/81 | 0.646 → **0.240** |
| Rotação (T3-P02): V3 96/97/116, V4 110/111/113 | 0.870 → **0.060** |
| Demais páginas (controles) | 0.123 → 0.119 |

**Páginas de controle com piora de CER > 0.005**: só a V9 p25 (0.165 → 0.178). A segunda tabela da página, presente no gabarito e antes emitida como texto solto, passou a ser detectada parcialmente. Não houve perda de texto.

**Tempo de extração do subconjunto (s, ANTES → FINAL)**: V3 69 → 82, V4 678 → 607, V5 17 → 18, V6 157 → 182, V7 66 → 70, V8 100 → 112, V9 234 → 267, V10 333 → 331. As execuções foram feitas com 8 processos concorrentes, o que impede conclusões de desempenho. O único custo adicional esperado por desenho é o retry de rotação (T3-P02).

---

## §2 Problemas — detalhamento

### T3-P00 | CRÍTICO | Executor sem tabelas e sem supressão de cabeçalho/rodapé — RESOLVIDO

- **Descrição**: `extract_one` criava `ExtractorConfig(mode="balanced", language="pt-BR", ocr_quality_policy="adaptive", ocr_engine="tesseract", num_threads=6)` diretamente. Assim herdava os defaults `enable_tables=False`, `merge_cross_page_tables=False` e `preserve_headers_footers=True`. O caminho canônico (`scripts/evaluate_e2e.py::_build_config`) parte de `best_extraction_config()`.
- **Corpora/páginas**: todos; todas as páginas com tabela ou com cabeçalho/rodapé repetido.
- **Métricas (001)**: `table_detection_f1`=0 e `cell_cer`=1.0 em V3–V9 (status `false_negative_only`); `cer_markdown_raw` V7=0.109; `paragraph_detection_precision` 0.24–0.60; `duplicate_excess_rate` 0.24–0.81.
- **Evidências**: nenhum bloco `<table>` ou pipe table nos 7 `*_extracted_paginated.md`; 79 rodapés "Página N de 80" no V7, cujo gabarito diz "Ignorar cabeçalhos e rodapés decorativos". Reprodução em memória na V7 p.2: 0 tabelas com a config antiga; tabela 6×6 idêntica ao gabarito com a config canônica.
- **Causa raiz**: CONFIRMADA — configuração do executor.
- **Correção**: `build_config()` em `scripts/run_tesseract_batch_wsl.py` = `replace(best_extraction_config(language="pt-BR", preserve_headers=False), mode=BALANCED, ocr_quality_policy="adaptive", ocr_engine="tesseract", num_threads=6)`. O run manifest passou a registrar `extractor_flags` (enable_tables, merge_cross_page_tables, preserve_headers_footers, ocr_quality_variants, política efetiva).
- **Testes**: `tests/test_tesseract_batch_runner_config.py` — flags da config e parâmetros da rodada.
- **Critério → resultado (002)**: `table_detection_f1` ≥ 0.97 em V5–V8 → 1.000 em V5, V6, V7 e V8 ✅; nenhum rodapé "Página N de M" no V7 → `cer_markdown_raw` 0.009 ✅.
- **Status**: RESOLVIDO.

### T3-P01 | CRÍTICO | OCR de página descartado por figuras — RESOLVIDO (003)

- **Descrição**: em páginas com OCR de página, as linhas OCR que caem dentro de regiões `FIGURE` eram removidas por `_append_hybrid_ocr_regions`, que impede o OCR de figura de virar texto de corpo. Há dois casos:
  1. **Página raster inteira** (raster-primary): a imagem da página vira uma `FIGURE` de 100% da página, e a camada nativa residual (≥ 20 caracteres, por exemplo "Autenticação digital: …") impede o OCR de ser primário (`_should_use_page_ocr_as_primary`). Resultado: todo o OCR é descartado.
  2. **Imagens de texto** (títulos, parágrafos e carimbos rasterizados, marcados pela política de região como `OCR_REGION`/`MERGE_OCR`): o OCR de página é removido e a figura fica sem texto.
- **Corpora/páginas**: V9 — 47 páginas `mixed` com `ocr_outcome=success` e CER médio 0.57 na 001, e 5 páginas raster não-full (p10, p16, p33, p61, p87).
- **Correção** (`src/structured_pdf_text/api.py`): `_append_hybrid_ocr_regions(..., page_raster_primary=…)`, `_PAGE_SIZED_FIGURE_COVERAGE=0.75`, atribuição do OCR de página a figuras `OCR_REGION`/`MERGE_OCR` (commit `db2ecb8`).
- **Testes**: `tests/test_tesseract_serie3_regressions.py` (6 testes de T3-P01).
- **Critério → resultado (003)**: V9 `text_coverage_recall` ≥ 0.80 → **0.948** ✅; V9 `cer_sequential_normalized` < 0.392 → **0.214** ✅; Δ em V3–V8 dentro de ±0.005 → **Δ = 0** (saídas idênticas) ✅.
- **Efeito colateral observado**: o texto recuperado expôs T3-P10 (colunas) e T3-P20 (parágrafos fragmentados) no V9.
- **Status**: RESOLVIDO.

### T3-P02 | ALTO | Páginas escaneadas rotacionadas — CORRIGIDO — VALIDADO PONTUALMENTE

- **Corpora/páginas**: V3 p96 (90°), p97 (180°), p98 (270°), p99 (tabela a 90°), p116 (tabela girada); **V4 p109–p114** (OCR de texto com rotação 90/180/270, CER 0.81–0.97 na 003).
- **Diagnóstico pós-003** (confiança média das palavras do Tesseract, 300 dpi):

  | Página | Situação | Confiança média | Linhas verticais | OSD |
  |---|---|---|---|---|
  | V3 p96 / p97 / p99 | rotacionada | 51 / 41 / 39 | — | 90 / 180 / 90 |
  | V4 p109 / p111 | rotacionada | 32 / 34 | — | **270 (errado)** / 180 |
  | V4 p113 / p114 | rotacionada 270° | **86** | **50 de 50** | 270 (conf. 2.6) |
  | V4 p103 / p104 | blur, em pé | 65 / 63 | 0 | 180 (errado) |
  | Controles V3/V4/V9 | em pé | 86–96 | 0 | — |
  | V6 p31 | em pé, já correta | 94 | 0 | **270, conf. 14 (errado)** |

  O OSD continua **não confiável**. Já a confiança do reconhecimento separa bem as páginas, e o próprio Tesseract às vezes lê a página girada como **linhas verticais** com confiança alta (V4 p113/p114).
- **Causa raiz**: CONFIRMADA. Não havia nenhuma correção de orientação ativa (OSD desligado por padrão, decisão mantida). O caminho OSD existente também não marcava a orientação nos tokens, de modo que a reconstrução os agruparia nas coordenadas giradas.
- **Correção** (`src/structured_pdf_text/ocr/backends/tesseract.py`, só Tesseract):
  - `_retry_quarter_turns`: a leitura de página inteira é refeita girada a 90°/180°/270° quando tem ≥ 3 palavras e (confiança média < 60 **ou** ≥ 60% das linhas verticais).
  - Uma leitura girada só é aceita se tiver linhas horizontais e confiança média ≥ 70. Quando o gatilho foi a confiança baixa, ela também precisa ganhar ≥ 25 pontos.
  - Os tokens voltam às coordenadas originais pela rotina já existente (`_map_rotated_tokens_to_original`) e recebem `rotation = (−r) mod 360`, a mesma convenção do EasyOCR.
  - Variável `TESSERACT_ROTATION_RETRY=0` desativa o retry; o estado aparece na identidade do backend.
  - Em `reconstruct.py`, as linhas do motor predominantemente verticais usam o agrupamento anterior por palavra (proteção quando o retry não age).
- **Páginas e resultados (CER, ANTES → FINAL)**: V3 p96 0.897 → **0.246** (recall 0 → 1.0; o resto é o cabeçalho "Página 096 | …" preservado, ver T3-P25), p97 0.892 → **0.000**, p116 0.934 → 0.436; V4 p110 0.970 → **0.060**, p111 0.837 → **0.044**, p113 0.808 → **0.053**. Controle V6 p31 inalterado (0.006).
- **Custo**: na estimativa sobre as 104 páginas OCR do V4 (81–184), o retry dispara em 7: as seis rotacionadas (109–114) e a p164 (tabela OCR degradada, confiança 54, sem rotação aceita). Cada disparo custa 3 leituras extras.
- **Testes**: `tests/test_tesseract_run003_regressions.py` — releitura e aceite, sem retry em página confiante/degradada em pé, exigência de ganho, gatilho por linhas verticais, orientação inversa nos tokens, proteção da reconstrução para linhas verticais.
- **Limitações**: V3 p98/p99 não estavam no conjunto pontual. A V3 p116 ainda tem CER 0.436 (tabela girada parcial).
- **Critério para a 004**: CER de V3 p96–p99 e V4 p109–p114 < 0.30, sem mudança em páginas em pé.

### T3-P03 | MÉDIO | V8 `partial_success` por região menor que um glifo — RESOLVIDO (003)

- **Por que `partial` com `metrics_status: ok`**: `metrics_status` indica apenas que o cálculo de métricas é válido. Já `benchmark_status` vem de `evaluate_e2e._classify_document_status`, e basta uma página com `partial_reasons` para o documento inteiro ficar `partial`.
- **Páginas**: V8 p22 e p25 (`/Rotate 180`, paisagem).
- **Correção** (`api.py::_recover_selected_regions`): uma região com largura **e** altura < 8 pt (`_MIN_TEXT_REGION_SIDE_PT`) e resultado vazio deixa de ser OCR obrigatório.
- **Critério → resultado (003)**: V8 `success`/`valid` ✅, CER 0.006 ✅.
- **Status**: RESOLVIDO.

### T3-P04 | INFO | V4: métricas em ~207–222 s — aberta

- **Dados**: 001: 206.4 s; 002: 208.0 s; 003: 222.1 s. Demais corpora: 1.5–14 s. É desempenho do **avaliador**, não qualidade da extração. Próximo passo: perfilar `compute_metrics.py` no V4 sem alterar o algoritmo.

### T3-P05 | MÉDIO | Títulos nativos com espaçamento entre letras — DOCUMENTADO

- V7 p1 (CER 0.754, sem mudança), V8 p46/p47 (`tracked_heading`). O texto é **nativo**, fora do pipeline Tesseract.

### T3-P06 | ALTO | Headings — PENDENTE

- Na 003: `heading_level_match_rate`=0.000 em V3 e V4; `heading_recall` V4=0.335, V9=0.351, V10=0.295; `heading_text_cer` V4=0.631, V9=0.667, V10=0.826.
- Parte do V9/V10 vinha de títulos de coluna fundidos (T3-P10). No escopo pontual do V10, a precisão de headings subiu (0.833 → 0.882), mas `heading_level_match_rate` caiu (0.967 → 0.933). Reavaliar na 004.

### T3-P07 | MÉDIO | V5: tabelas sem borda e ordem embaralhada — PENDENTE

- Sem mudança na 003 nem nas correções pós-003 (V5 idêntico no escopo pontual). Páginas com recall < 0.9 na 003: 4, 5, 6, 17, 18, 20, 23, 26, 28, 32, 36, 38.

### T3-P08 | MÉDIO | V9: dados críticos — MELHOROU (003)

- 002 → 003: `numeric` 0.852 → 0.934, `currency` 0.914 → 0.936, `dates` 0.859 → 0.964, `cpf`/`cnpj` 0.909 → 1.000. Monitorar.

### T3-P09 | INFO | Regras ajustadas ao corpus em `ocr/tesseract/postprocess.py` — RISCO MATERIALIZADO

- O módulo tem regras ligadas a marcadores do corpus ("OMITIR NO GABARITO", cabeçalhos "OCR PURO * SCAN", "Página NNN |").
- O risco se materializou duas vezes: a supressão de "dump de coordenadas" escondia o bug real do parser (T3-P23), e a supressão de separador "Página NNN" apagava corpo de texto (T3-P25). As duas foram tratadas.
- **Risco remanescente**: `_suppress_ocr_corpus_markers` ainda suprime o bloco inteiro quando `_OMITIR_GABARITO_RE` casa em qualquer ponto do texto. Não foi alterada, porque também trata intencionalmente tabelas com o marcador partido.

### T3-P10 | CRÍTICO | Linhas OCR fundidas entre colunas — CORRIGIDO — VALIDADO PONTUALMENTE

- **Sintoma (003)**: o texto é reconhecido (recall ≈ 0.99), mas cada linha junta a mesma altura de todas as colunas. Exemplo da V10 p6: "No procedimento V10-006-01-458, a No procedimento V10-006-02-498, a Unidade No procedimento V10-006-03-404, a Setor de…". Números de processo ficam associados ao texto errado.
- **Corpora/páginas (003)**: V4 — as 21 páginas OCR `two_columns` (82…142), CER 0.743, igual desde a 001; V9 — 20 páginas de 2–3 colunas rasterizadas; V10 — 35 páginas L01–L06 e os controles 41 e 50.
- **Por que as métricas de ordem não detectam**: `pairwise_reading_order_accuracy` = 0.999 no V10, porque compara a ordem de blocos, não a ordem dentro do bloco.
- **Causa raiz (CONFIRMADA por experimento em memória)**:
  1. O TSV do Tesseract (PSM 3) **separa corretamente** as colunas em blocos (V10 p6: x 238–830 / 938–1531 / 1641–2223 px), mas `_tsv_to_pipeline_tokens` descartava `block/par/line`.
  2. `ocr/reconstruct.py::reconstruct_ocr_lines` agrupava todas as palavras da página por altura, em toda a largura.
  3. A única defesa, `_split_at_prose_gutter`, exige no máximo 2 observações por lado (pensada para OCR por linha, como RapidOCR/EasyOCR), nunca dispara com tokens-palavra do Tesseract e só trata uma calha.
  4. `layout/ocr_aware.py::_order_ocr_lines_by_columns` agrupa âncoras por `x0` em toda a página, sem faixas: no V10, com 3 colunas em cima e 2 embaixo, as âncoras se fundem.
- **Correção**:
  - `OcrToken.layout_line_id` (opcional, `compare=False`, `None` nos demais motores). O backend Tesseract preenche `escopo-da-chamada:page:block:par:line`. O campo é transportado por `ocr/pipeline/converters.py` e `ocr/coordinates.py`.
  - `reconstruct_ocr_lines`: com identidade de linha, `_group_engine_lines` mantém cada linha do Tesseract inteira e junta as que estão na mesma altura, o que preserva linhas de tabela cujas células estão em blocos diferentes. A linha só é separada numa **calha validada** (`_validated_gutters`): vão ≥ 1.5× a altura do texto, com texto corrido dos dois lados (≥ 4 palavras e ≥ 15% da largura), na mesma faixa vertical, em ≥ 3 linhas contíguas. A calha vale só na extensão vertical dessas linhas, mais até 2 linhas adjacentes (títulos de coluna, linha final curta).
  - Ordem: `ocr_text_column_gutters()` e `text_column_bands()` alimentam `_order_ocr_lines_by_text_gutters` em `reconstruct_ocr_layout`, que lê coluna por coluna dentro de cada faixa. `_group_engine_lines` também emite os grupos nessa ordem, o que vale para tokens de páginas giradas.
  - Tokens sem identidade (outros motores) seguem o caminho anterior, sem alteração.
- **Alternativas avaliadas e descartadas**: separar em toda mudança de bloco (regra ingênua: regrediu tabelas, por exemplo V10 p4 0.073 → 0.151, porque o Tesseract também separa colunas de tabela); calha só geométrica sem a evidência do motor (mais falsos positivos e risco em RapidOCR/EasyOCR); OCR por coluna (custo de OCR multiplicado); linhas do Tesseract sem agrupamento por altura (quebraria linhas de tabela).
- **Páginas e resultados (CER, ANTES → FINAL)**:
  - V4: p82 0.736 → 0.196, p86 0.736 → 0.213, p116 0.725 → 0.191, p136 0.660 → 0.111, p140 0.765 → 0.082.
  - V9: p4 0.584 → **0.001**, p8 0.506 → 0.009, p31 0.522 → 0.014, p41 0.474 → **0.000**, p50 0.471 → 0.003, p53 0.518 → 0.004, p67 0.556 → 0.153, p84 0.443 → 0.008, p9 0.680 → 0.517 (inclinação 3–5°).
  - V10: p6 0.697 → **0.036**, p8 0.678 → 0.070, p19 0.664 → 0.088, p21 0.682 → 0.109, p41 0.498 → 0.045, p50 0.577 → 0.064, p24 0.681 → 0.302, p40 0.683 → 0.276.
  - Grupo multicoluna: **0.676 → 0.238**.
- **Restante**: o resíduo do V4 two-col (≈ 0.20) acompanha o recall de ≈ 0.84, o mesmo teto das páginas de coluna única (T3-P21). As V10 L01/L02 (inclinação ±4–5°, baixa qualidade: p5, p13, p18, p25) seguem com 0.47–0.72, porque o próprio Tesseract funde colunas nessas páginas (V10 p18: 34 de 60 linhas atravessam colunas).
- **Testes**: `tests/test_tesseract_run003_regressions.py` — 2 e 3 colunas, linha final curta, tabela com células em blocos, linha larga de metadados, calha fora da extensão vertical, tokens sem identidade (caminho anterior), ordem por faixas (3 + 2 colunas), títulos de coluna, transporte do campo, identidade no TSV.
- **Critério para a 004**: CER das páginas multicoluna V4 ≤ 0.25, V9 ≤ 0.30, V10 L03–L06 ≤ 0.30; CER documental V4 ≤ 0.17, V9 ≤ 0.15, V10 ≤ 0.35; V3/V5–V8 com Δ ≤ ±0.005 nas métricas de texto e tabela.

### T3-P11 | ALTO | V10 não avaliável pelo executor — REABERTO na 003, corrigido via T3-P15

- A correção da 002 (fallback `_REFERENCIA_PAGINADA.md`) funcionou na pré-verificação da 003. A falha veio da troca do corpus durante a rodada (T3-P15).
- **Limitação do gabarito V10 antigo, agora superada**: o V10 revisado tem `expected_markdown` por página no manifesto, 20 tabelas, 110 títulos e 22 listas (`VALIDATION_REPORT.md`: 837 checks aprovados). Com isso, estrutura, tabelas e listas passam a ser avaliáveis.

### T3-P12 | MÉDIO | Extração 3–7× mais lenta desde a 002 — PENDENTE (causa medida)

- **Medição** (cProfile em memória, subconjuntos de páginas):

  | Amostra | Config 001 | Config canônica | Chamadas ao Tesseract | Detecção visual de tabela | OCR |
  |---|---|---|---|---|---|
  | V6 p1–10 | 11.4 s | 46.3 s | 19 → 20 (4.7 → 5.0 s) | 30.0 s (65%), 196× `align_ruling_lines` | 11% |
  | V4 6 págs. | — | 50.0 s | 5 | 18.8 s (38%), **1424×** `align_ruling_lines` | 14% |
  | V9 5 págs. | — | 51.0 s | 11 | 35.5 s (70%), 295× | 25% |

- **Causa (CONFIRMADA)**:
  - **Não** é OCR, recuperação excessiva nem repetição de OCR. O custo está em `tables/visual.py::detect_visual_grid` → `_local_grid`, que roda `align_ruling_lines` (HoughLinesP, `tables/visual_alignment.py`) em **cada** componente conexo da rede de linhas, sem limite.
  - Além disso, a extração usa 102–131% de CPU com `threads=6`: o processamento por página é serial.
- **Por que não foi corrigido**: um filtro antes do Hough muda a evidência analisada no recorte, então não preserva exatamente as tabelas detectadas. A correção exige validação byte a byte das tabelas em todos os corpora.
- **Opções**: limitar o número de componentes por área ou estrutura; reaproveitar o resultado do Hough de página para componentes contidos; paralelizar páginas. Toda opção deve manter idênticas as métricas de tabela de V3–V9.

### T3-P13 | BAIXO | Regressões pontuais 001 → 002 — sem mudança

- As saídas de V3–V8 da 003 são idênticas às da 002. Os itens listados (V3 `percent`, V4 `currency`, V5 recall/`paragraph_boundary_recall`, V6 `paragraph_detection_recall`, V9 headings/listas) não pioraram.

### T3-P14 | INFO | Falhas pré-existentes na suíte de testes

- As mesmas 13 falhas: `test_line_detector.py` (8 `test_reconciliation_*`), `test_rapidocr_improvement_005/006/008.py` (3), `test_no_timeouts.py::test_production_code_has_no_timeout_keyword_argument` e `test_evaluation_exact_optimizations.py::test_full_ground_truth_self_control_is_complete_and_perfect[V3]`. Nenhuma toca o código alterado.

### T3-P15 | ALTO | V10 `metrics_error` na 003: corpus trocado durante a rodada — CORRIGIDO NO EXECUTOR

- **Sintoma**: `ERROR: canonical evaluation failed: Selected pages without ground truth: [2..84]` (run.log, linha 1373).
- **Causa raiz (CONFIRMADA)**:
  1. A pré-verificação da 003 rodou às 07:50:58 e validou o `_REFERENCIA_PAGINADA.md` antigo (`## Página NNN`, 84 páginas).
  2. Todo o `corpus/V10/` foi substituído entre 07:56 e 07:59 (mtimes), com o V3 ainda em extração. A extração do V10 (09:11) usou o PDF novo (`pdf_sha256 5c694f86…`).
  3. A referência nova delimita páginas com `<!-- PAGE_START V10-P001 … -->`, que `split_pages` não reconhece: o arquivo inteiro vira **1 página**.
  4. O executor guardava caminhos, mas não revalidava hashes, apesar de o protocolo declarar `corpus_policy: identical-pdf-reference-and-manifest-hashes`.
- **Gabarito**: o manifesto novo tem `expected_markdown` em 84/84 páginas, 19 delas vazias e legítimas. A pré-verificação atual (`--check-only`) escolhe `manifesto` 84/84 para o V10. A avaliação da 003 com `--manifesto`, no scratchpad, dá `ok`, `rank_eligible=True` e 61 métricas aplicáveis. **Nenhuma referência foi alterada** e o avaliador não precisa de correção.
- **Correção** (`scripts/run_tesseract_batch_wsl.py`):
  - A pré-verificação registra `input_hashes` (PDF, referência e manifesto usados).
  - Antes da extração e antes das métricas, `changed_inputs()` recalcula os hashes e compara também o `pdf_sha256` do run manifest. Se houver mudança, o corpus fica `corpus_changed` e as métricas não rodam.
  - `input_hashes` vai para o `run_summary.json`. O comentário desatualizado sobre o V10 foi corrigido.
- **Testes**: `tests/test_tesseract_batch_runner_config.py` — hashes registrados e mudança detectada (incluindo remoção); referência com `PAGE_START` + manifesto com `expected_markdown` → gabarito `manifesto` (7 testes passam).
- **Critério para a 004**: V10 `metrics_status=ok`, `rank_eligible=True`; nenhum `corpus_changed`. Não altere `corpus/` durante a rodada.

### T3-P16 | MÉDIO | V10 `partial_success` falso — CORRIGIDO — VALIDADO PONTUALMENTE

- **Evidência (003)**: as 19 páginas `degraded` (`ocr_no_text`) são **exatamente** as 19 com gabarito vazio (N01/N02/N03 e o controle 72 `graphic_text`): só brasão ou imagem não textual. A saída vazia está correta, mas o documento inteiro era rebaixado.
- **Causa**: `api.py` só aceitava OCR vazio sem falha quando a página era branca uniforme (`_is_visually_blank`).
- **Correção** (`api.py`, comum aos motores, porque a classificação é compartilhada):
  - `_has_text_like_ink(image, page_long_side_pt)` conta componentes conexos com tamanho de glifo (2.5–30 pt, proporção ≤ 3) alinhados em linhas ou colunas de texto (≥ 4 por faixa de 4 pt; ≥ 8 no total).
  - Sem tinta com forma de texto, o OCR vazio é tratado como página sem texto (`blank_page`, aviso próprio). Qualquer incerteza conta como texto, mantendo o `partial`.
- **Validação**: as 19 páginas negativas do V10 → sem tinta de texto. Todas as páginas com texto testadas → com texto, incluindo carimbos esparsos (V9 p10/p16: 139 glifos alinhados), rotacionadas (V3 p96–98, V4 p109–111) e degradadas (V10 p75, V4 p104). No escopo pontual, o V10 passa de `partial_success` (degradadas 2, 7, 72) para `success`/`valid`. Os tamanhos são físicos (pt), e não relativos à página: com frações da página, o teste pré-existente `test_plan_corrections.py::test_page_ocr_exception_and_successful_empty_result_have_distinct_diagnostics` (página 240×240 pt) falhou e foi corrigido.
- **Testes**: `tests/test_tesseract_run003_regressions.py` (texto em qualquer orientação; página gráfica; entrada ilegível → conservador).
- **Critério para a 004**: V10 `extraction_status=success`; nenhuma página com texto marcada como `blank_page`.

### T3-P17 | ALTO | Colunas de texto detectadas como tabela sem borda — PARCIAL

- **Evidência (003)**: V10 com 63 tabelas para 20 esperadas, todas as extras em páginas raster L01/L02 inclinadas (p13, 18, 25, 31, 45, 48, 53, 62, 68, 83) e no controle p50.
- **Causa**: o adaptador `tables/ocr_tracks.py` passa as linhas OCR ao detector nativo de tabelas sem borda (`text_tracks.detect_borderless_tables`). Em página inclinada, `_merge_same_baseline_lines` junta a linha k de uma coluna com a k+1 da vizinha, e o classificador prosa × tabela deixa passar. Com T3-P10 separando as colunas, o mesmo mecanismo passou a gerar uma tabela falsa na V9 p9 (detectado no teste pontual e corrigido).
- **Correção** (`tables/ocr_tracks.py`): uma tabela candidata atravessada por uma calha validada de texto corrido (`ocr_text_column_gutters`, mesma evidência de T3-P10, cobrindo ≥ 50% da altura da tabela) é descartada. Sem identidade de linha (outros motores) não há calhas, e o comportamento é idêntico ao anterior.
- **Resultados**: no escopo pontual do V10, a precisão de tabelas vai de 0.40 para **1.00**, `table_content_f1` de 0.41 para 0.88 e `cell_cer` de 8.6 para 0.24. A tabela falsa da V9 p9 desapareceu, e as tabelas reais de V3/V4/V9 foram mantidas (`table_detection_f1` V3 1.000, V4 1.000, V9 0.846).
- **Restante**: L01/L02 com Tesseract fundindo colunas na própria segmentação (V10 p18), onde não há calha validada. Ver T3-P18.
- **Testes**: `tests/test_tesseract_run003_regressions.py::test_ocr_track_table_crossed_by_a_text_column_gutter_is_rejected`.

### T3-P18 | ALTO | Inclinação não compensada em tokens Tesseract — CORRIGIDO — VALIDADO PONTUALMENTE

- **Causa (CONFIRMADA)**: `_dominant_slope` estima a inclinação pelos polígonos dos tokens, e os tokens Tesseract não têm polígono. A inclinação ficava sempre 0, e palavras de linhas vizinhas se misturavam. Exemplo da V3 p80 (+2°) ANTES: "amostra combina evidência, extração e leitura; sinais literaisvalor associado é…".
- **Correção** (`reconstruct.py`): com a identidade de linha, as linhas do Tesseract são mantidas inteiras. `_engine_line_slope` estima a inclinação ajustando os centros das palavras de cada linha (mesmos critérios de aceitação de `_dominant_slope`), usada para juntar células da mesma linha visual em tabelas inclinadas.
- **Resultados (CER)**: V3 p80 0.482 → **0.005**; V4 p105 0.572 → **0.040**, p135 0.623 → 0.066, p107 0.710 → 0.311 (também T3-P23); V9 p44 0.537 → 0.350, p17 0.574 → 0.548, p81 0.910 → 0.906 (praticamente sem texto reconhecido). Grupo: **0.646 → 0.240**.
- **Restante**: inclinação ≥ 4° com baixa qualidade (V10 L01/L02, V9 p81), onde o próprio Tesseract perde ou funde linhas. Exigiria endireitar a imagem antes do OCR. Não implementado.
- **Testes**: `test_skewed_tesseract_lines_are_not_mixed_with_neighbour_lines`. O mesmo cenário falha no caminho anterior, o que foi verificado.

### T3-P19 | INFO | `duplicate_excess_rate` não mede só duplicação — INTERPRETAÇÃO

- `integrity_metrics.py:67`: o excesso é `max(0, hyp_count − ref_count)` por impressão digital de bloco. Todo bloco da hipótese sem par **exato** no gabarito (`ref_count = 0`) conta como excesso. A métrica mede também diferença de segmentação.
- **Evidência**: na V9 p2 da 003, CER 0.000, recall/precisão 1.0 e `duplicate_excess_rate` 0.70 (7 de 10 blocos são parágrafos cortados em outro ponto). A alta do V9 (0.412 → 0.480) e o valor do V10 (0.82) **não** indicam duplicação real.
- **Ação**: não alterar o avaliador; ler sempre junto com CER e precisão.

### T3-P20 | MÉDIO | Parágrafos OCR quebrados no meio da frase — PARCIAL

- Exemplo da V9 p2 na 003: "A divergência ¶ encontrada exige…". V9 `paragraph_detection_precision` 0.496 → 0.423 na 003; V10 0.153.
- **Efeito das correções pós-003**: no escopo pontual, `paragraph_detection_f1` V9 0.305 → 0.332, V10 0.134 → 0.314, V3 0.372 → 0.629. Algumas páginas pioraram individualmente (V9 p31 0.533 → 0.320, p67 0.500 → 0.240).
- **Hipótese**: `segment_ocr_paragraphs` (regras de vão, recuo e linha curta) com linhas reais de coluna. Pendente.

### T3-P21 | INFO | V4: recall ≈ 0.85 em OCR de coluna única com CER ≈ 0.03 — ABERTA

- O bug do TSV (T3-P23) explica só uma parte (V4 p81 0.869 → 0.880; p85 0.867 → 0.875).
- **Hipótese**: diferença entre a projeção de cobertura e o CER (por exemplo, marcação no gabarito, como `## Página …` e `**Controle de página:**`). Investigar no avaliador sem alterá-lo.

### T3-P22 | MÉDIO | Colunas fundidas em páginas **nativas** multicoluna — NOVO, fora do escopo

- V10 p9 (`mixed`, camada de texto nativa, L03 + N04): CER 0.689, sem mudança, com o mesmo padrão de linhas intercaladas.
- Outras L03 nativas estão corretas (p23, p39, p73, p79 ≈ 0.07); p59 tem 0.263.
- O componente é a montagem de linhas nativas (compartilhada, ligada a T3-P05). Requer autorização.

### T3-P23 | CRÍTICO | Parser TSV engolia linhas após palavra iniciada por aspas — CORRIGIDO — VALIDADO PONTUALMENTE

- **Causa raiz (CONFIRMADA)**:
  - `_parse_tsv` usava `csv.DictReader` com a cotação padrão (`"`). Quando o OCR reconhece uma palavra que começa com aspas ASCII (o V4 tem `"aspas"` em todos os parágrafos OCR), o módulo `csv` abre um campo entre aspas que engole as linhas seguintes, com tabs e quebras de linha, até a próxima aspa.
  - O pós-processamento então suprimia o bloco como "dump de coordenadas Tesseract" (`_suppress_tesseract_coordinate_dump`, cuja docstring cita V3 p96/p97 como exemplos), descartando conteúdo real sem aviso.
- **Evidência**:
  - V3 p96: 54 palavras no TSV, 11 entregues pelo parser.
  - V3 p97: 55 → 19.
  - V4 p107 (no pipeline): um bloco de 9.6 mil caracteres suprimido, com a página saindo com 659 de 2718 caracteres (recall 0.098).
- **Correção** (`ocr/backends/tesseract.py`): `quoting=csv.QUOTE_NONE`. O TSV do Tesseract nunca usa aspas como delimitador.
- **Resultados**: V4 p107 recall 0.354 → 0.590; V4 p150 (tabela OCR) 0.904 → 0.185; parte dos ganhos de V3 p96/p97.
- **Testes**: `test_tsv_word_starting_with_a_quote_does_not_swallow_following_rows`.

### T3-P24 | BAIXO | Título quebrado com travessão virava item de lista — CORRIGIDO — VALIDADO PONTUALMENTE

- **Evidência**: com as colunas separadas (T3-P10), cada título de coluna quebrado ("Secretaria Regional de Controle / — Extrato de tramitação") gerou um falso item de lista. Na validação intermediária, a precisão de itens do V10 caiu de 0.765 para 0.639.
- **Causa**: `reconstruct_ocr_layout` trata toda linha OCR iniciada por `–`/`—` como marcador de lista.
- **Correção** (`layout/ocr_aware.py::_is_dash_continuation`): uma linha iniciada por travessão ou meia-risca é continuação quando vem colada à anterior no mesmo fluxo, a anterior não termina em pontuação de frase ou de enumeração e não é item de lista. Hífen e símbolos de bullet continuam iguais.
- **Resultado (V10, escopo pontual)**: precisão de itens 0.765 → **0.886**, de grupos 0.455 → **0.667**. Os itens falsos que já existiam antes também foram removidos.
- **Testes**: `test_em_dash_line_continuing_a_wrapped_title_is_not_a_list_item`.

### T3-P25 | ALTO | Supressão de separador "Página NNN" apagava o corpo da página — CORRIGIDO — VALIDADO PONTUALMENTE

- **Evidência**: com a rotação corrigida, a V3 p96 foi lida corretamente, mas a página saiu **vazia**. O bloco "Página 096 | OCR com rotação física de 90 graus Controle de página: QA-P096 O lote…" foi suprimido inteiro por `_suppress_pagina_separator_blocks`, que suprimia qualquer bloco **iniciado** por "Página N".
- **Correção** (`ocr/tesseract/postprocess.py`): só blocos com formato de separador (uma linha, ≤ 160 caracteres) são suprimidos. Um bloco que continua com o corpo é mantido, ao custo de o texto do separador ficar na saída.
- **Resultado**: V3 p96 0.897 → 0.246, recall 0 → 1.0.
- **Testes**: `tests/test_tesseract_postprocess.py::test_pagina_block_that_continues_with_body_text_is_kept`; os 7 testes anteriores do separador continuam passando.

---

## §3 Alterações de código

### Série 3 até a 003 (commit `db2ecb8`)

| Item | Arquivo | Mudança |
|---|---|---|
| T3-P00 | `scripts/run_tesseract_batch_wsl.py` | `build_config()` canônica; `extractor_flags` no run manifest |
| T3-P11 | `scripts/run_tesseract_batch_wsl.py` | V10 em `CORPORA`; fallback `_REFERENCIA_PAGINADA.md` com prioridade −1 |
| T3-P01 | `src/structured_pdf_text/api.py` | `_append_hybrid_ocr_regions(page_raster_primary=…)`, `_PAGE_SIZED_FIGURE_COVERAGE=0.75`, atribuição de OCR de página a figuras `OCR_REGION`/`MERGE_OCR` |
| T3-P03 | `src/structured_pdf_text/api.py` | `_MIN_TEXT_REGION_SIDE_PT=8.0` em `_recover_selected_regions` |

### Pós-003 (2026-11-12, não commitadas)

| Item | Arquivo | Mudança | Escopo |
|---|---|---|---|
| T3-P10/P18 | `src/structured_pdf_text/document.py` | campo opcional `OcrToken.layout_line_id` | compartilhado, `None` fora do Tesseract |
| T3-P10/P18 | `src/structured_pdf_text/ocr/pipeline/converters.py`, `ocr/coordinates.py` | transporte do campo | compartilhado, sem efeito sem o campo |
| T3-P10/P18/P23/P02 | `src/structured_pdf_text/ocr/backends/tesseract.py` | identidade de linha no TSV; `QUOTE_NONE`; retry de rotação; orientação nos tokens | só Tesseract |
| T3-P10/P18/P02 | `src/structured_pdf_text/ocr/reconstruct.py` | `_group_engine_lines`, `_validated_gutters`, `_extend_gutter`, `_engine_line_slope`, `ocr_text_column_gutters`, `text_column_bands`, proteção para linhas verticais; caminho anterior extraído sem mudança para `_group_tokens_by_level` | ativo só com identidade de linha |
| T3-P10/P24 | `src/structured_pdf_text/layout/ocr_aware.py` | ordem por faixas de colunas (só com calhas); travessão como continuação | ordem: só Tesseract; travessão: regiões só-OCR de qualquer motor |
| T3-P17 | `src/structured_pdf_text/tables/ocr_tracks.py` | rejeita tabela atravessada por calha de texto | só com identidade de linha |
| T3-P16 | `src/structured_pdf_text/api.py` | `_has_text_like_ink` para OCR vazio | classificação comum aos motores |
| T3-P25 | `src/structured_pdf_text/ocr/tesseract/postprocess.py` | separador "Página NNN" só com formato de separador | só Tesseract |
| T3-P15 | `scripts/run_tesseract_batch_wsl.py` | `input_hashes`, `changed_inputs`, resultado `corpus_changed` | executor |
| testes | `tests/test_tesseract_run003_regressions.py` (novo, 26 testes), `tests/test_tesseract_batch_runner_config.py` (+2), `tests/test_tesseract_postprocess.py` (+1) | — | — |

**Não alterados**: `corpus/`, gabaritos, manifestos, avaliador (`src/structured_pdf_text/evaluation/`), `output/tesseract/001–003/`.

## §4 Testes executados

| Data | Execução | Resultado |
|---|---|---|
| 2026-11-11 | `pytest tests/test_tesseract_batch_runner_config.py` / `tests/test_tesseract_serie3_regressions.py` | 5 / 8 passed |
| 2026-11-11 | `pytest tests` | 3109 passed, 13 failed (T3-P14), 4 skipped |
| 2026-11-11 | `run_tesseract_batch_wsl.py 003 --check-only` | aprovada |
| 2026-11-12 | RUN 003 completa (manual) | V3–V9 `ok`; V10 `metrics_error` (T3-P15) |
| 2026-11-12 | métricas da 003 do V10 com `--manifesto` (scratchpad) | `ok`, `rank_eligible`, 61 aplicáveis |
| 2026-11-12 | `run_tesseract_batch_wsl.py 999 --check-only` | aprovada; V10 → `manifesto` 84/84 |
| 2026-11-12 | `pytest tests/test_tesseract_run003_regressions.py` | 26 passed |
| 2026-11-12 | `pytest tests/test_tesseract_batch_runner_config.py` / `tests/test_tesseract_postprocess.py` / `tests/test_tesseract_effective.py` | todos passaram |
| 2026-11-12 | testes EasyOCR, RapidOCR, contratos OCR, coordenadas, layout | só as 3 falhas pré-existentes de RapidOCR (T3-P14) |
| 2026-11-12 | `pytest tests` (código final) | **13 failed = exatamente as de T3-P14**; nenhuma falha nova |
| 2026-11-12 | validação pontual por página (§1.4) | ANTES e FINAL, 8 corpora, ~85 páginas |
| 2026-11-12 | estimativa do gatilho de rotação (reconhecimento cru, V4 p81–184) | 7/104 páginas disparam |

**Não executado**: nenhuma rodada completa após as correções; `output/tesseract/004` não existe.

## §5 Validação esperada na RUN 004

Comparar 004 × 003. Para o V10, a 003 só tem métricas recalculadas com o manifesto (§1.3).

| Verificação | Critério |
|---|---|
| T3-P15 | V10 `metrics_status=ok`, `rank_eligible=True`; nenhum `corpus_changed` |
| T3-P16 | V10 `extraction_status=success` |
| T3-P10 | CER multicoluna V4 ≤ 0.25, V9 ≤ 0.30, V10 L03–L06 ≤ 0.30; CER documental V4 ≤ 0.17 (0.231), V9 ≤ 0.15 (0.214), V10 ≤ 0.35 (0.516) |
| T3-P02 | CER de V3 p96–p99 e V4 p109–p114 < 0.30 |
| T3-P17 | V10 `table_detection_precision` ≥ 0.85 |
| T3-P18 | CER de V3 p80 e V4 p105–p108/p135–p136 < 0.30 |
| T3-P23/P25 | nenhuma supressão `tesseract_coordinate_dump`; V4 `text_coverage_recall` > 0.832 |
| Controle | V3, V5–V8: Δ de `cer_sequential_normalized`, `text_coverage_precision`, `table_detection_f1`, `cell_content_exact_match` dentro de ±0.005 em relação à 003; V4/V9/V10: métricas de tabela e de dados críticos sem piora > 0.01 |
| Desempenho | tempo de extração dentro de ±10% da 003; o V4 pode ter até ~7 páginas com 3 leituras extras (T3-P02) |
| Geral | nenhum `extract_error`/`metrics_error`; não declarar sucesso geral com regressão relevante |

**Limitações que só a 004 esclarece**:
- **Representatividade**: as ~85 páginas testadas não cobrem todas as páginas dos corpora. A ausência de regressão no teste pontual não prova ausência de regressão nos corpora completos.
- **Contexto de documento**: supressão de cabeçalho/rodapé repetido e tabelas entre páginas (`merge_cross_page_tables`) dependem do documento inteiro e não foram exercitadas em subconjuntos.
- **Métricas documentais**: alinhamento de blocos e duplicação documental só existem na rodada completa.
- **Custo real do retry de rotação**: fora do V4 ainda não foi medido.
- **Páginas não testadas**: V3 p98/p99 (rotação), V10 L01/L02 restantes e páginas multicoluna fora do conjunto pontual.

Comando: `python3 -u scripts/run_tesseract_batch_wsl.py 004` (pré-verificação: `--check-only`). O diretório `output/tesseract/004` não pode existir antes da execução. **Não altere `corpus/` durante a rodada.**

## §6 Notas por corpus

### Diagnóstico da rodada 001

- **V3**: tabelas ausentes (T3-P00); p96–p99 rotacionadas (T3-P02); p116 "OCR tabelas" com CER 1.15; níveis de heading 0 (T3-P06).
- **V4**: tabelas ausentes; 83/224 páginas com CER > 0.15, concentradas em "OCR de tabelas" (p145–p182, CER 1.1–2.7); `heading_recall` 0.335; métricas lentas (T3-P04).
- **V5**: tabelas ausentes; páginas sem borda/embaralhadas/híbridas (T3-P07).
- **V6**: só T3-P00; páginas `rotation` p31/p47/p75 com CER 0.12–0.13.
- **V7**: T3-P00 (rodapés e tabelas); p1 com título espaçado (T3-P05); p25/p29/p73 `grid_borderless` com 0.20–0.23.
- **V8**: T3-P00; `partial` por T3-P03; p46/p47 `tracked_heading` (T3-P05).
- **V9**: T3-P01 (47+5 páginas); T3-P00; títulos e dados críticos como consequência (T3-P06/T3-P08).

### Diagnóstico da rodada 003

- **V3**: 8 páginas com CER > 0.15, sendo 7 por perda (p86, 96–99, 116, 138) e 1 por ordem (p80 — T3-P18).
- **V4**: 64 páginas com CER > 0.15. As "OCR de texto" (p81–144) concentram 87.6% das edições: todas as `two_columns` (T3-P10), rotação (T3-P02), inclinação (T3-P18), vazamento do TSV (T3-P23). "OCR de tabelas" (p145–184): CER médio 0.45.
- **V5**: 14 páginas com CER > 0.15 (T3-P07).
- **V6/V7/V8**: 0 / 1 / 2 páginas com CER > 0.15 (T3-P05).
- **V9**: 36 páginas com CER > 0.15; 28 com recall ≥ 0.90 (ordem — T3-P10, 20 delas multicoluna).
- **V10**: 35 páginas com CER > 0.15; 22 por ordem (T3-P10) e 13 por perda (L01/L02 inclinadas, T3-P17/T3-P18); p9 nativa (T3-P22).

## §7 Histórico resumido da Série 2 (2026-11-05 a 2026-11-06, executor `run_loop.py`)

| Correção | Arquivo | Decisão |
|---|---|---|
| Fix-F-V9-01 — guarda de altura (> 85%) em `_detect_rectangular_grid` | `tables/detector.py` | aceita (V9 CER 0.4849 → 0.4318) |
| Fix-F-V9-02 — `_is_form_label_colon_pattern` | `tables/text_tracks.py` | aceita (V9 CER → 0.4285) |
| Fix-F-V9-03 — agrupamento em `ocr_tracks.py` | `tables/ocr_tracks.py` | revertida (sem efeito) |
| Fix-F-V9-04 — `_group_short_regions` | `tables/detector.py` | inconclusiva, código preservado |

A hipótese "Tipo A" da Série 2 (V9: Tesseract não reconhece imagens de baixa DPI) foi **refutada** na Série 3 (T3-P01). Notas técnicas mantidas: o avaliador aceita a chave `page_number` do manifesto V9; o limiar de memória do V4 é 11 GB.
