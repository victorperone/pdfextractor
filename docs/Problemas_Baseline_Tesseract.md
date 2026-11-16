# Problemas da Baseline Tesseract — Registro Técnico

> **Escopo**: Série 3 (executor `scripts/run_tesseract_batch_wsl.py`, diretórios `output/tesseract/NNN`).
> A versão anterior deste documento (Série 2, runs 001–008 via `run_loop.py`) está preservada no git (`git show 5cc81d6:docs/Problemas_Baseline_Tesseract.md`); um resumo está em §7.
> **Protocolo**: atualizar após cada rodada. Um problema só é marcado como RESOLVIDO quando o critério mensurável dele for atendido numa rodada completa, sem regressão relevante nos demais corpora.
> **Classificação usada a partir da 003**: RESOLVIDO (critério atendido em rodada completa) · CORRIGIDO — VALIDADO PONTUALMENTE (testes por página, aguarda rodada completa) · PARCIAL · PENDENTE · INFO.

---

## §0 Estado atual

| Campo | Valor |
|---|---|
| **Última atualização** | 2026-11-16 — análise da RUN 006; correções pós-006 validadas por página (T3-P32, T3-P31) e PNG temporário sem perdas mais rápido (T3-P12) |
| **Branch** | `feat/tesseract-optimization` (alterações pós-005 e pós-006 não commitadas) |
| **Baseline imutável** | `output/tesseract/001/` |
| **Baseline corrigida** | `output/tesseract/002/` (T3-P00) |
| **Baseline atual** | `output/tesseract/006/` (commit `dfbf5e9`) |
| **Próxima rodada** | `python3 -u scripts/run_tesseract_batch_wsl.py 007` (manual) — valida T3-P32, T3-P31 e o PNG temporário (§5) |
| **Configuração** | balanced · pt-BR · adaptive · tesseract · Markdown paginado · supplemental-audit. **Threads**: 6 até a 003; 8 desde a 005 (a 006 registra `threads: 8` e `tesseract_omp_thread_limit: 8`). O Tesseract 5.5.0 usa no máximo 4 threads OpenMP por chamada (`num_threads(4)` fixo no LSTM), então 6 e 8 equivalem a 4 (§1.10) |
| **Avaliador** | `evaluation-3.0.0`, métricas 3.1.0 — **não alterado** |
| **Corpus/gabaritos/manifestos** | **não alterados por nós**. O diretório `corpus/V10/` foi substituído externamente em 2026-11-12, entre 07:56 e 07:59, durante a RUN 003 (ver T3-P15) |

### Situação dos problemas

| ID | Gravidade | Prioridade | Componente | Status |
|---|---|---|---|---|
| T3-P00 | CRÍTICO | — | executor | **RESOLVIDO** (002) |
| T3-P01 | CRÍTICO | — | `api.py` | **RESOLVIDO** (003) |
| T3-P02 | ALTO | — | backend Tesseract | **RESOLVIDO** (004; V3 p116 restante) |
| T3-P03 | MÉDIO | — | `api.py` | **RESOLVIDO** (003) |
| T3-P04 | INFO | P3 | avaliador | aberta |
| T3-P05 | MÉDIO | P3 | montagem de linhas nativas | DOCUMENTADO — fora do escopo |
| T3-P06 | ALTO | P2 | headings | PENDENTE (parte era consequência de T3-P10) |
| T3-P07 | MÉDIO | P2 | V5 tabelas sem borda/ordem | PENDENTE (sem mudança na 003) |
| T3-P08 | MÉDIO | P3 | dados críticos V9 | MELHOROU na 003 — monitorar |
| T3-P09 | INFO | — | `ocr/tesseract/postprocess.py` | RISCO MATERIALIZADO — mitigado em T3-P25, T3-P26 e T3-P27 |
| T3-P10 | CRÍTICO | — | reconstrução de linhas OCR + ordem de leitura | **RESOLVIDO** (004) |
| T3-P11 | ALTO | — | executor (V10) | **RESOLVIDO** (004, via T3-P15) |
| T3-P12 | MÉDIO | P2 | detecção visual de tabelas | PENDENTE — causa medida e reconfirmada na 006 (HoughLinesP ≈ 66% do tempo nas páginas pesadas; Tesseract 26%); PNG temporário mais rápido aplicado (§1.10) |
| T3-P13 | BAIXO | — | regressões pontuais 001→002 | sem mudança na 003 (saídas V3–V8 idênticas) |
| T3-P14 | INFO | — | suíte de testes | 13 falhas pré-existentes (inalteradas) |
| T3-P15 | ALTO | — | executor | **RESOLVIDO** (004) |
| T3-P16 | MÉDIO | — | classificação de página OCR vazia (`api.py`) | **RESOLVIDO** (004) |
| T3-P17 | ALTO | P2 | `tables/ocr_tracks.py` | **RESOLVIDO** pelo critério (V10 precisão 0.947); L01/L02 inclinadas restantes |
| T3-P18 | ALTO | — | backend Tesseract + ordem de colunas | **RESOLVIDO** (006: critérios de CER/recall atendidos); efeitos colaterais em T3-P31 e V4 p106/p156 |
| T3-P19 | INFO | — | avaliador (`duplicate_excess_rate`) | INTERPRETAÇÃO DOCUMENTADA — não alterar |
| T3-P20 | MÉDIO | P2 | segmentação de parágrafos OCR | PARCIAL (melhorou com T3-P10) |
| T3-P21 | INFO | P3 | V4: recall ≈ 0.85 em OCR de coluna única | ABERTA (T3-P23 explica só uma parte) |
| T3-P22 | ALTO | P1 | layout de regiões em páginas nativas multicoluna | PENDENTE — diagnóstico refinado na 005 (8 páginas em V4/V9/V10) |
| T3-P23 | CRÍTICO | — | backend Tesseract (`_parse_tsv`) | **RESOLVIDO** (004) |
| T3-P24 | BAIXO | — | `layout/ocr_aware.py` (listas) | **RESOLVIDO** (004) |
| T3-P25 | ALTO | — | `ocr/tesseract/postprocess.py` | **RESOLVIDO** (004), com efeito colateral corrigido em T3-P26 |
| T3-P26 | ALTO | — | `ocr/tesseract/postprocess.py` | **RESOLVIDO** (005) |
| T3-P27 | CRÍTICO | — | `ocr/tesseract/postprocess.py` | **RESOLVIDO** (005) |
| T3-P28 | ALTO | — | montagem de linhas Tesseract | **RESOLVIDO** (006: V8 p4/p58 `€ 98,40`, V8 `numeric` 0.991) |
| T3-P29 | MÉDIO | P2 | layout de página nativa com `/Rotate 90` | PENDENTE — página inteira num único bloco (V4 p213/p214) |
| T3-P30 | INFO | — | executor | **RESOLVIDO** (006 registra `threads: 8` e `tesseract_omp_thread_limit: 8`) |
| T3-P31 | **MÉDIO** (regressão confirmada na 006) | P0 | layout de regiões só-OCR + ordem genérica de linhas | **NÃO RESOLVIDO** — 5 tentativas pós-006; a melhor corrige as listas, mas intercala colunas (V10 p18); nenhuma mantida |
| T3-P32 | ALTO | P0 | backend Tesseract (orientação) | NOVO na 006 — **CORRIGIDO — VALIDADO PONTUALMENTE** para tabelas lidas como palavras verticais (V9 p20/p71); V9 p36/p92 pendentes |
| T3-P33 | MÉDIO | P1 | tabela por trilhas OCR → `text_tracks._build_table` (compartilhado) | NOVO na 006 — exceção `Invalid BBox` na V9 p36 → V9 `partial_success`; PENDENTE |
| T3-P34 | ALTO | P1 | tabela raster com inclinação pequena em página mista | NOVO na 006 (inventário) — tabela não detectada (V9 p58/p66/p78; fraca em p8/p25); PENDENTE |

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

### §1.5 RUN 004 — comparação com a 003 (2026-11-13)

- **Execução**: 8/8 corpora `ok`; extração `success`/`valid` em todos, incluindo V8 e V10. V10 avaliado com o manifesto (`metrics_status=ok`, `rank_eligible`, 61 métricas aplicáveis). Nenhum `corpus_changed`.
- **Comparabilidade**: avaliador e gabaritos inalterados. O V10 da 003 foi recalculado com o manifesto (mesmos hashes de PDF `5c694f86…` e manifesto `5262e4ed…`). Saídas de **V5 e V7 idênticas byte a byte** às da 003. V6 mudou só a p21 (ordem dentro de uma célula de cabeçalho de tabela: "o Semestre Il" → "Semestre Il o"); V8 só p4/p58 (ver T3-P28).
- **Tempo de extração (s, 003 → 004)**: V3 357 → 349 · V4 2380 → 2460 · V5 86 → 130 · V6 619 → 782 · V7 165 → 182 · V8 385 → 385 · V9 592 → 585 · V10 609 → 514. O V5 (+51%) e o V7 (+10%) têm saída idêntica, então essas variações não vêm das correções. A provável mudança local para 8 threads e a carga da máquina impedem atribuir causa (T3-P30). Métricas do V4: 222 → 130 s; do V10: 87 s.

| Métrica (003 → 004) | V3 | V4 | V9 | V10 |
|---|---|---|---|---|
| `cer_sequential_normalized` | 0.039 → 0.035 | 0.231 → **0.080** | 0.214 → **0.102** | 0.516 → **0.264** |
| `wer_sequential` | 0.051 → 0.050 | 0.318 → 0.172 | 0.260 → 0.119 | 0.641 → 0.327 |
| `text_coverage_recall` | 0.959 → 0.985 | 0.832 → 0.877 | 0.948 → 0.964 | 0.876 → 0.919 |
| `text_coverage_precision` | 0.979 → **0.968** | 0.902 → 0.918 | 0.983 → 0.982 | 0.943 → 0.945 |
| `heading_detection_f1` | 0.867 → **0.619** | 0.502 → 0.502 | 0.426 → 0.426 | 0.423 → 0.426 |
| `heading_text_cer` | 0.199 → **2.694** | 0.631 → 0.631 | 0.667 → 0.667 | 0.826 → 0.821 |
| `paragraph_detection_f1` | 0.897 → 0.925 | 0.371 → 0.372 | 0.519 → 0.516 | 0.196 → 0.359 |
| `list_item_detection_precision` | 0.939 → 1.000 | 0.778 → 0.764 | 0.765 → 0.765 | 0.641 → 0.771 |
| `table_detection_f1` | 0.974 → 1.000 | 0.875 → 0.901 | 0.789 → 0.842 | 0.545 → 0.923 |
| `table_detection_precision` | 0.974 → 1.000 | 0.928 → 0.921 | 0.896 → 0.906 | 0.391 → **0.947** |
| `cell_content_exact_match` | 0.953 → 0.985 | 0.621 → 0.650 | 0.498 → 0.520 | 0.306 → 0.827 |
| `cell_cer` | 0.040 → 0.004 | 0.355 → 0.302 | 0.618 → 0.594 | 7.048 → 0.181 |
| `numeric` | 0.972 → 0.985 | 0.891 → 0.904 | 0.934 → 0.954 | 0.927 → 0.925 |
| `currency` | 0.963 → 1.000 | 0.884 → 0.960 | 0.936 → 0.962 | 1.000 → 1.000 |
| `dates` | 1.000 → 1.000 | 0.953 → 0.976 | 0.964 → 0.981 | 0.967 → 0.974 |
| `duplicate_excess_rate` | 0.056 → 0.064 | 0.702 → 0.713 | 0.480 → 0.491 | 0.821 → 0.579 |

V5–V8: todas as métricas dentro de ±0.005 da 003 (V8 `numeric` 0.989 → 0.988).

**Critérios da §5 para a 004:**

| Verificação | Critério | 004 | Situação |
|---|---|---|---|
| T3-P15 | V10 `metrics_status=ok`, `rank_eligible` | ok, `rank_eligible` | ✅ |
| T3-P16 | V10 `success` | `success`/`valid` | ✅ |
| T3-P10 | multicoluna V4 ≤ 0.25, V9 ≤ 0.30, V10 L03–L06 ≤ 0.30 | 0.743 → **0.178**; 0.518 → **0.142**; **0.181** | ✅ |
| T3-P10 | CER documental V4 ≤ 0.17, V9 ≤ 0.15, V10 ≤ 0.35 | 0.080; 0.102; 0.264 | ✅ |
| T3-P02 | V3 p96–p99, V4 p109–p114 < 0.30 | V3 0.246 / 0.000 / 0.000 / 0.159; V4 0.031–0.060 | ✅ (V3 p116 = 0.436, fora do critério) |
| T3-P17 | V10 `table_detection_precision` ≥ 0.85 | 0.947 | ✅ |
| T3-P18 | V3 p80, V4 p105–p108, p135–p136 < 0.30 | p80 0.005; p105 0.040; p106 0.134; **p107 0.311**; **p108 0.466**; p135 0.066; p136 0.111 | ⚠️ parcial |
| T3-P23 | V4 recall > 0.832 | 0.877 | ✅ |
| Controle V3 | Δ ≤ ±0.005 | **títulos regrediram** (T3-P26) | ❌ → corrigido pontualmente |
| Controle V5–V8 | Δ ≤ ±0.005 | atendido | ✅ |
| Desempenho | ±10% | não comparável (T3-P30) | ⚠️ |

**Erros remanescentes de maior peso na 004** (edições por página):
- V10: L01/L02 (raster de baixa qualidade, ±4–5°: p5, 13, 18, 25, 31, 45, 48, 53, 62, 68, 75, 83; CER 0.44–0.70) e p9 nativa (T3-P22).
- V4: p213/p214 vazias (T3-P27), p104/p108 (blur, inclinação −2.5°).
- V9: p81 (inclinação, quase sem texto), p23 (3 colunas + 2 tabelas, 3–5°), p43/p51 (multicoluna 0.47/0.55, inalteradas desde a 003).
- V3: as páginas com títulos "Página NNN" (T3-P26).

### §1.6 Validação pontual das correções pós-004 (2026-11-13)

**Método**: o mesmo harness da §1.4, recriado no scratchpad. Usa `build_config()` atual do executor + `page_indices`, com verificação de que só as páginas pedidas foram processadas, e métricas oficiais com escopo `selected_pages`. Usa a configuração local do executor (8 threads), igual entre ANTES e DEPOIS. Etapas: ANTES (código `976f7f0`) → após T3-P26 → após T3-P27.

**Páginas**:

| Corpus | Páginas |
|---|---|
| V3 | 1, 2, 44, 64, 66, 67, 69, 108, 123, 124, 126, 134 (alvos T3-P26); controles 5, 20, 60, 80, 96, 97, 116, 138 |
| V4 | 213, 214 (alvos T3-P27); controles 10, 50, 81, 82, 85, 107, 150, 190, 220 |
| Controles | V5 10, 17 · V6 3, 21 · V7 1, 25 · V8 4, 22, 58 · V9 2, 8, 25 · V10 1, 4, 6, 9 |

**Resultados**: ver T3-P26 e T3-P27. Em cada etapa, as páginas fora do alvo saíram **idênticas byte a byte** à etapa anterior, e V5–V10 ficaram idênticos ao ANTES.

**Limitação**: subconjuntos não substituem a rodada completa (§5).

### §1.7 RUN 005 — comparação com a 004 (2026-11-13)

- **Execução**: 8/8 corpora `ok`, extração `success`/`valid` em todos; V10 avaliado pelo manifesto. Avaliador e gabaritos inalterados.
- **Saídas**: V5–V10 **idênticas byte a byte** à 004. No V3 mudaram exatamente as 12 páginas-alvo de T3-P26, e no V4 exatamente p213/p214 (T3-P27).
- **Métricas que mudaram (004 → 005)**:

| Métrica | V3 | V4 |
|---|---|---|
| `cer_sequential_normalized` | 0.035 → **0.013** | 0.080 → **0.069** |
| `wer_sequential` | 0.050 → 0.025 | 0.172 → 0.161 |
| `text_coverage_recall` | 0.985 → 0.982 | 0.877 → 0.888 |
| `text_coverage_precision` | 0.968 → 0.989 | 0.918 → 0.918 |
| `heading_detection_f1` | 0.619 → 0.867 | = |
| `heading_precision` / `heading_text_cer` | 0.500 → 0.929 / 2.694 → 0.199 | = |
| `numeric` / `currency` / `dates` | 0.985 → 0.991 / = / = | 0.904 / 0.960 → 0.967 / 0.976 → 0.983 |
| `duplicate_excess_rate` | 0.064 → 0.042 | = |

- **Páginas-alvo**: V3 p1, 2, 44, 64, 66, 67, 69, 108, 123, 124, 134 com CER 0.000 (p126 0.006); V4 p213/p214 com CER 1.000 → 0.041 e recall 0 → 1.000.
- **Critérios da §5 para a 005**: T3-P26 ✅ (`heading_precision` 0.929, `heading_text_cer` 0.199, `text_coverage_precision` 0.989); T3-P27 ✅ (p213/p214 recall 1.000; V4 recall 0.888 > 0.877); controle ✅ (V5–V10 idênticos); sem `extract_error`/`metrics_error`/`corpus_changed`.
- **Tempo de extração (s, 004 → 005)**: V3 349 → 375 · V4 2460 → 2418 · V5 130 → 82 · V6 782 → 595 · V7 182 → 162 · V8 385 → 376 · V9 585 → 575 · V10 514 → 510. V5–V10 têm saída idêntica e variaram de −37% a −1%, ou seja, a variação vem do ambiente (carga da máquina), não do código. Uso de CPU da extração: 101–131% nas rodadas 003, 004 e 005.
- **Threads (T3-P30)**: `fff2a4b` (09:34 −03) commitou `num_threads=8`, e a 005 começou depois (09:35 −03), então rodou com 8. A 004 rodou antes do commit, com a alteração presente só localmente; não há registro que permita confirmar. Medição controlada (V4 p85, 4 repetições alternadas): `OMP_THREAD_LIMIT` 6 = 3.95 s, 8 = 3.96 s de mediana. A mudança não tem efeito mensurável, porque o OCR é uma parte pequena do tempo (T3-P12) e o Tesseract não escala além disso nesta máquina.

**Erros remanescentes de maior peso na 005** (edições por página):

| Grupo | Páginas | CER | Observação |
|---|---|---|---|
| Inclinação (T3-P18) | V10 L01/L02: 5, 13, 18, 25, 31, 45, 48, 53, 62, 68, 75, 83 | 0.44–0.70 | recall até 0.35; ~60% das edições do V10 |
| | V9 p81, p17, p9, p44, p23 | 0.35–0.91 | recall até 0.07 |
| | V4 p107/p108 | 0.31 / 0.47 | — |
| Nativo multicoluna (T3-P22) | V4 p25, 26, 28 (11% das edições do V4); V9 p5, 43, 51 (17% do V9); V10 p9, 59 (10% do V10) | 0.26–0.69 | recall 1.0: texto completo, ordem errada |
| Valor colado (T3-P28) | V8 p4/p58 | — | `€ 98,4096,40` |
| Demais | V4 OCR de duas colunas (0.178, recall ≈ 0.84, T3-P21); V4 OCR de tabelas (0.267); V5 (T3-P07) | — | — |

**Prioridade escolhida para o ciclo pós-005**:
1. **T3-P18** — maior perda de conteúdo restante (≈ 20 páginas em 3 corpora, recall até 0.07), correção possível só no backend Tesseract.
2. **T3-P28** — dado crítico corrompido, introduzido pelo ciclo pós-003, com causa localizada e correção restrita ao Tesseract.

**Não escolhido agora**: T3-P22 é maior em páginas, mas o texto está completo (recall 1.0) e a causa está no layout nativo compartilhado (ver T3-P22), com risco de regressão nos corpora nativos.

### §1.8 Validação pontual das correções pós-005 (2026-11-13)

**Método**: harness no scratchpad, com `build_config()` atual (8 threads) e `page_indices`. O harness confere que só as páginas pedidas foram processadas e calcula métricas oficiais com escopo `selected_pages`. Etapas: ANTES (código `fff2a4b`) → endireitamento (3 iterações, ver T3-P18) → T3-P28. Métricas de subconjunto **não** são comparáveis às documentais.

**Páginas (59)**:

| Corpus | Páginas |
|---|---|
| V3 | 1, 80, 96, 97, 116 |
| V4 | 10, 82, 85, 103, 104, 105, 107, 108, 110, 113, 135, 150 |
| V5 | 10, 17 |
| V6 | 3, 21, 31 |
| V7 | 25 |
| V8 | 4, 22, 39, 46, 58 |
| V9 | 2, 4, 8, 9, 17, 23, 25, 31, 44, 50, 62, 81 |
| V10 | 1, 4, 5, 6, 13, 18, 19, 20, 25, 31, 35, 41, 45, 48, 53, 62, 68, 75, 83 |

**Resultado no escopo (ANTES → FINAL)**:

| Corpus | CER | Recall | Precisão | Outros |
|---|---|---|---|---|
| V3 | sem mudança relevante (p80 0.005 → 0.000) | — | — | — |
| V4 | 0.176 → **0.108** | 0.737 → 0.786 | 0.820 → 0.831 | `numeric` 0.816 → 0.797 (ver T3-P18) |
| V5, V7 | idênticos | — | — | — |
| V6 | p21 0.005 → 0.002 | — | — | `cell_alignment_accuracy` 1.000 → 0.963 |
| V8 | sem mudança de texto | — | — | `cell_cer` 0.023 → 0.002; `numeric` 0.872 → 0.947 (T3-P28) |
| V9 | 0.303 → **0.130** | 0.807 → 0.978 | 0.947 → 0.965 | `numeric` 0.915 → 0.964; `dates` 0.925 → 0.990 |
| V10 | 0.460 → **0.255** | 0.761 → 0.981 | 0.870 → 0.974 | `numeric` 0.866 → 0.939; `dates` 0.919 → 0.978; listas: precisão de itens 0.571 → 0.333 (T3-P31) |

**Controles inalterados**: V10 p1, 4, 6, 19, 20, 35, 41; V9 p2, 8, 25, 31, 50, 62; V4 p10, 82, 85, 103, 104, 110, 113, 150; V3 p1, 96, 97, 116; V5, V7 e V8 p22/39/46 com saída idêntica.

**Tempo de extração do subconjunto (s, ANTES → FINAL; mesma concorrência)**: V3 48 → 51 · V4 295 → 323 · V5 5 → 7 · V6 159 → 166 · V7 2 → 2 · V8 146 → 153 · V9 190 → 211 · V10 301 → 346. O aumento nos corpora com páginas inclinadas (V4 +9%, V9 +11%, V10 +15%) é o custo da releitura endireitada. Corpora sem páginas disparadas variaram até +28% por ruído (V5 com saída idêntica).

### §1.9 RUN 006 — comparação com a 005 (2026-11-16)

- **Execução**: 8/8 corpora com métricas `ok`, `rank_eligible` e o mesmo número de métricas aplicáveis da 005. Extração `success`/`valid` em 7 corpora; **V9 `partial_success`/`partial` → `needs_review`**. Avaliador e gabaritos inalterados; nenhum `corpus_changed`.
- **V9 `partial`**: causado por **uma única página, a p36** (`ocr_table_structure_unavailable`). É um **problema de execução**: `tables/ocr_tracks.py::detect_ocr_track_tables` → `text_tracks._build_table` levanta `ValueError: Invalid BBox` (y1 < y0). As demais páginas `recovered` (3, 19, 24, 49, 56, 58, 66, 71) são recuperações bem-sucedidas, não falhas. Em qualidade, a p36 é uma página raster inclinada (1.2°) com uma **tabela girada 270°** que sai ilegível na 005 e na 006 (T3-P32); a exceção ocorreu justamente num candidato de tabela formado por linhas do parágrafo vizinho fundidas com o lixo da tabela girada (na 005 o mesmo candidato virou uma tabela falsa). Ver T3-P33. `metrics_status: ok` só indica que o cálculo foi válido.
- **Saídas**: V5 e V7 idênticas byte a byte à 005. Mudaram: V3 p80, p81, p115 (endireitamento: CER → 0.000); V4 10 páginas; V6 p6, p21, p45, p89 (Δ ≤ 0.003); V8 p4/p58 (T3-P28); V9 10 páginas; V10 as 12 L01/L02.

| Métrica (005 → 006) | V3 | V4 | V9 | V10 |
|---|---|---|---|---|
| `cer_sequential_normalized` | 0.013 → 0.013 | 0.069 → **0.062** | 0.102 → **0.077** | 0.264 → **0.194** |
| `wer_sequential` | = | 0.161 → 0.154 | 0.119 → 0.088 | 0.327 → 0.231 |
| `text_coverage_recall` | = | 0.888 → 0.894 | 0.964 → **0.990** | 0.919 → **0.991** |
| `text_coverage_precision` | = | 0.918 → 0.920 | 0.982 → 0.989 | 0.945 → 0.974 |
| `numeric` / `dates` | = | 0.904 → 0.897 / 0.983 → 0.973 | 0.954 → 0.953 / 0.981 → 0.991 | 0.925 → 0.945 / 0.974 → 0.991 |
| `cell_content_exact_match` / `cell_cer` | = | 0.650 → 0.634 / 0.302 → 0.333 | 0.520 → 0.527 / 0.594 → 0.579 | = |
| `table_detection_precision` | = | 0.921 → 0.911 | 0.906 → 0.923 | = |
| `list_item_detection_precision` | = | 0.764 → 0.778 | = | 0.771 → **0.659** |
| `list_group_detection_precision` | = | 0.368 → 0.389 | = | 0.548 → **0.354** |
| `heading_level_match_rate` | = | = | = | 0.989 → 0.977 |
| `paragraph_detection_f1` | 0.925 → 0.923 | 0.372 → 0.375 | 0.516 → 0.524 | 0.359 → 0.372 |

V5–V8: dentro de ±0.003, exceto V8 `numeric` 0.988 → 0.991 e `cell_content_exact_match` 0.997 → 0.999 (T3-P28).

**Critérios da §5 para a 006:**

| Verificação | Critério | 006 | Situação |
|---|---|---|---|
| T3-P18 | V10 L01/L02: CER médio ≤ 0.40 e recall ≥ 0.90 | CER médio **0.314** (0.157–0.468); recall 0.909–0.997 | ✅ |
| T3-P18 | V9 p81, p17, p23 < 0.30 | 0.000 / 0.269 / 0.156 | ✅ |
| T3-P18 | V4 p107/p108 < 0.10 | 0.035 / 0.035 | ✅ |
| T3-P18 | CER documental V10 < 0.264, V9 < 0.102, V4 < 0.069 | 0.194 / 0.077 / 0.062 | ✅ |
| T3-P28 | V8 p4/p58 `€ 98,40`; V8 `numeric` ≥ 0.988 | `€ 98,40` nas duas; 0.991 | ✅ |
| T3-P30 | `threads: 8` e `tesseract_omp_thread_limit: 8` | registrados em resumo e manifestos | ✅ |
| Controle | V3, V5–V8 iguais à 005 fora das páginas inclinadas e de V8 p4/p58 | V5/V7 idênticos; V3 só páginas inclinadas; V6 4 páginas com Δ ≤ 0.003 | ✅ |
| Controle | tabelas/listas/dados críticos de V4/V9/V10 sem piora > 0.01 (exceto listas do V10) | V4 `cell_cer` +0.031, `cell_detection_precision` −0.026, `cell_content_exact` −0.016, `dates` −0.010; V10 listas −0.19 (T3-P31) | ⚠️ |
| Desempenho | V4/V9/V10 até +10% | +3% / +12% / +12% (V5 +11% e V8 +14% com saída idêntica ou quase: ruído do ambiente) | ⚠️ dentro do ruído |
| Geral | sem `extract_error`/`metrics_error`/`corpus_changed` | nenhum; V9 `partial` (T3-P33) | ⚠️ |

**Leitura das pioras do V4** (por página, critério de gravidade): a queda de tabelas vem quase toda da **p164** (tabela OCR inclinada de 12 colunas), que na 005 saía como texto ilegível (CER 0.663) e na 006 passou a sair **como tabela** de 12 colunas com os códigos legíveis (CER 0.345). A métrica de detecção não casa essa tabela com o gabarito (precisão 0) e o `cell_cer` sobe, mas o conteúdo melhorou. Pioras reais e reproduzíveis, porém pequenas: **p106** (`currency` 0.933 → 0.769: um valor monetário a menos reconhecido; CER 0.134 → 0.211) e **p156** (recall 0.706 → 0.647). Registradas como efeito colateral de T3-P18, prioridade baixa.

**Desempenho**: extração com 102–134% de CPU (igual às rodadas anteriores). Perfil medido (17 páginas de V3–V10, 2026-11-16): subprocessos Tesseract 26% do tempo, gravação do PNG temporário 6%, Python 68% — dominado por `cv2.HoughLinesP` em `tables/visual_alignment.py` (≈ 66% nas páginas pesadas, T3-P12). Ver §1.10.

**Erros remanescentes de maior peso na 006** (fração das edições de cada corpus):

| Grupo | Páginas | Observação |
|---|---|---|
| Nativo multicoluna (T3-P22) | V9 p51 (10.5%), p43 (6.6%), p5 (5.0%); V10 p9 (7.9%); V4 p25/26/28 (12.4%) | recall 1.0; ordem errada; componente nativo (fora do escopo desta etapa) |
| L01/L02 restantes (T3-P18) | V10 p62 (5.4%), p68, p48, p25, p75 | CER 0.32–0.47 |
| OCR de texto degradado (T3-P21) | V4 p104 (blur, recall 0.34, 6.8%), p118, p88, p142, p130 | limite de reconhecimento |
| Tabelas raster giradas (T3-P32) | V9 p20, p36, p55, p71, p92 | `cell_content_exact` 0–0.04 (p55 0.66) |
| Tabelas raster inclinadas (T3-P34) | V9 p58, p66, p78 (não detectadas), p8, p25 | `currency` 0.63–0.86 |
| Títulos espaçados (T3-P05) | V8 p46/p47 (85% das edições do V8), V7 p1 | nativo |
| V3 | p116 (tabela girada, 27.5%), p138 (adversarial, recall 0), p86, p103 | — |

**Inventário e priorização pós-006** (gravidade considerando dados críticos > conteúdo > estrutura; só regressões reproduzíveis):

| Problema | Gravidade | Abrangência (006) | Evidência | Componente | Tratável no Tesseract? | Prioridade |
|---|---|---|---|---|---|---|
| T3-P32 tabela raster girada transposta/ilegível | ALTO (dados críticos em tabela) | V9: 5 de 6 tabelas giradas (p20, p36, p55, p71, p92) | `cell_content_exact` 0–0.04; R$ na célula errada | backend Tesseract (orientação) | sim | **P0 — escolhido (1)** |
| T3-P31 listas falsas em páginas endireitadas | MÉDIO (regressão confirmada, estrutura) | V10: 12 páginas, 20 itens | `list_group_precision` −0.19 | layout só-OCR | sim (provável) | **P0 — escolhido (2)** |
| T3-P22 nativo multicoluna | ALTO (ordem) | V4 3, V9 3, V10 2 páginas; 10–25% das edições | recall 1.0, CER 0.26–0.69 | layout nativo | não (nativo; fora do escopo) | P1 |
| T3-P34 tabela raster inclinada não detectada | ALTO (dados críticos) | V9: 5 páginas | `currency` 0.63–0.86 | detecção visual de tabela (compartilhada) | parcial | P1 |
| T3-P33 exceção na estrutura por trilhas OCR | MÉDIO (execução) | V9 p36 → documento `partial` | `ValueError: Invalid BBox` | `text_tracks` (compartilhado) | não (nativo) | P1 |
| T3-P21 OCR de texto degradado | MÉDIO | V4 p104 (recall 0.34) e outras | limite do reconhecimento | Tesseract | baixa | P2 |
| Efeitos colaterais de T3-P18 no V4 | BAIXO | p106 (`currency` −0.16), p156 (recall −0.06) | pequenos e pontuais | backend | sim | P2 |
| T3-P06/T3-P20 títulos e parágrafos | MÉDIO | V4/V9/V10 | `heading_recall` V10 0.30 | layout | — | P2 |
| T3-P12 desempenho | MÉDIO | todos | 102–134% de CPU; Hough 66% | detecção visual de tabelas | não (nativo) | P2 |

- **Por que T3-P32 primeiro**: é o único problema que corrompe **valores numéricos de tabela** com causa localizada no backend Tesseract; afeta qualquer documento real com tabela girada e a correção não depende do corpus. O `partial` do V9 (T3-P33) foi comparado e tem impacto menor: muda o status, mas a exceção impediu uma tabela falsa.
- **Por que T3-P31 em segundo**: é uma regressão introduzida pela correção anterior (T3-P18) e confirmada na rodada completa, no pipeline Tesseract. Causa raiz independente de T3-P32.
- **Por que não T3-P22/T3-P33/T3-P34 agora**: maiores em páginas ou em dados, mas dependem de componentes compartilhados/nativos (layout nativo, detector de tabelas sem borda, detecção visual), fora do escopo autorizado (prioridade a correções exclusivas do Tesseract e não alterar o extrator nativo).

### §1.10 Paralelismo e desempenho do OCR (investigação de 2026-11-16)

- **Tesseract 5.5.0** (com OpenMP): o paralelismo interno existe só no LSTM e tem teto fixo de 4 threads (`num_threads(GFS=4)` em `src/lstm/lstm.cpp`, `kNumThreads = 4` em `src/lstm/fullyconnected.cpp`); `OMP_THREAD_LIMIT` só reduz. Segmentação, binarização e carga do modelo são seriais.
- **Medição** (8 páginas a 288 DPI, 3 repetições): `OMP_THREAD_LIMIT` 1/2/4/8 → 16.0 / 14.8 / 14.6 / 14.7 s, com CPU 100/134/196/197%. **8 ≡ 4**; o ganho máximo sobre 1 thread é ≈ 9%. TSV idêntico byte a byte em todas as configurações.
- **Processos concorrentes** escalam (OMP=1: K=3 → 2.6×, K=4 → 3.0×), mas dentro de uma página só ≈ 6% do tempo está em tentativas OCR independentes (retry de rotação, escalas de refino) e ≈ 3% entre regiões. **Paralelismo interno à página não foi implementado** (ganho pequeno frente ao risco).
- **Aplicado**: o PNG temporário entregue ao Tesseract é gravado com `compress_level=1` (era o padrão 6). É sem perdas: pixels decodificados e TSV idênticos nas 8 páginas; o tempo de gravação + Tesseract cai 11–14% por chamada de página inteira (≈ 3% do tempo total da extração). Teste: `tests/test_tesseract_effective.py::test_temporary_image_given_to_tesseract_is_lossless`.

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

### T3-P02 | ALTO | Páginas escaneadas rotacionadas — RESOLVIDO (004)

- **RUN 004**: V3 p96 0.246, p97 0.000, p98 0.000, p99 0.159; V4 p109–p114 0.031–0.060. Critério atendido. Restante: V3 p116 (tabela girada) 0.436.

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

### T3-P10 | CRÍTICO | Linhas OCR fundidas entre colunas — RESOLVIDO (004)

- **RUN 004**: CER multicoluna V4 0.743 → 0.178, V9 0.518 → 0.142, V10 L03–L06 + controles 41/50 = 0.181; CER documental V4 0.080, V9 0.102, V10 0.264. Critérios atendidos. Restante: V9 p43/p51 (0.47/0.55, inalteradas desde a 003 — a investigar) e V10 L01/L02 (T3-P18).

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

- **RUN 004**: tempos entre −16% e +51% em relação à 003, inclusive em corpora com saída idêntica (V5 +51%, V7 +10%). Não é possível atribuir às correções (T3-P30).

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

### T3-P15 | ALTO | V10 `metrics_error` na 003: corpus trocado durante a rodada — RESOLVIDO (004)

- **RUN 004**: V10 com `metrics_status=ok`, `rank_eligible`, 61 métricas; nenhum `corpus_changed`.

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

### T3-P16 | MÉDIO | V10 `partial_success` falso — RESOLVIDO (004)

- **RUN 004**: V10 `success`/`valid`.

- **Evidência (003)**: as 19 páginas `degraded` (`ocr_no_text`) são **exatamente** as 19 com gabarito vazio (N01/N02/N03 e o controle 72 `graphic_text`): só brasão ou imagem não textual. A saída vazia está correta, mas o documento inteiro era rebaixado.
- **Causa**: `api.py` só aceitava OCR vazio sem falha quando a página era branca uniforme (`_is_visually_blank`).
- **Correção** (`api.py`, comum aos motores, porque a classificação é compartilhada):
  - `_has_text_like_ink(image, page_long_side_pt)` conta componentes conexos com tamanho de glifo (2.5–30 pt, proporção ≤ 3) alinhados em linhas ou colunas de texto (≥ 4 por faixa de 4 pt; ≥ 8 no total).
  - Sem tinta com forma de texto, o OCR vazio é tratado como página sem texto (`blank_page`, aviso próprio). Qualquer incerteza conta como texto, mantendo o `partial`.
- **Validação**: as 19 páginas negativas do V10 → sem tinta de texto. Todas as páginas com texto testadas → com texto, incluindo carimbos esparsos (V9 p10/p16: 139 glifos alinhados), rotacionadas (V3 p96–98, V4 p109–111) e degradadas (V10 p75, V4 p104). No escopo pontual, o V10 passa de `partial_success` (degradadas 2, 7, 72) para `success`/`valid`. Os tamanhos são físicos (pt), e não relativos à página: com frações da página, o teste pré-existente `test_plan_corrections.py::test_page_ocr_exception_and_successful_empty_result_have_distinct_diagnostics` (página 240×240 pt) falhou e foi corrigido.
- **Testes**: `tests/test_tesseract_run003_regressions.py` (texto em qualquer orientação; página gráfica; entrada ilegível → conservador).
- **Critério para a 004**: V10 `extraction_status=success`; nenhuma página com texto marcada como `blank_page`.

### T3-P17 | ALTO | Colunas de texto detectadas como tabela sem borda — RESOLVIDO pelo critério (004)

- **RUN 004**: V10 `table_detection_precision` 0.391 → 0.947, `table_row_detection_precision` 0.191 → 0.972, `cell_cer` 7.05 → 0.18. As páginas L01/L02 seguem com CER alto por causa do OCR (T3-P18).

- **Evidência (003)**: V10 com 63 tabelas para 20 esperadas, todas as extras em páginas raster L01/L02 inclinadas (p13, 18, 25, 31, 45, 48, 53, 62, 68, 83) e no controle p50.
- **Causa**: o adaptador `tables/ocr_tracks.py` passa as linhas OCR ao detector nativo de tabelas sem borda (`text_tracks.detect_borderless_tables`). Em página inclinada, `_merge_same_baseline_lines` junta a linha k de uma coluna com a k+1 da vizinha, e o classificador prosa × tabela deixa passar. Com T3-P10 separando as colunas, o mesmo mecanismo passou a gerar uma tabela falsa na V9 p9 (detectado no teste pontual e corrigido).
- **Correção** (`tables/ocr_tracks.py`): uma tabela candidata atravessada por uma calha validada de texto corrido (`ocr_text_column_gutters`, mesma evidência de T3-P10, cobrindo ≥ 50% da altura da tabela) é descartada. Sem identidade de linha (outros motores) não há calhas, e o comportamento é idêntico ao anterior.
- **Resultados**: no escopo pontual do V10, a precisão de tabelas vai de 0.40 para **1.00**, `table_content_f1` de 0.41 para 0.88 e `cell_cer` de 8.6 para 0.24. A tabela falsa da V9 p9 desapareceu, e as tabelas reais de V3/V4/V9 foram mantidas (`table_detection_f1` V3 1.000, V4 1.000, V9 0.846).
- **Restante**: L01/L02 com Tesseract fundindo colunas na própria segmentação (V10 p18), onde não há calha validada. Ver T3-P18.
- **Testes**: `tests/test_tesseract_run003_regressions.py::test_ocr_track_table_crossed_by_a_text_column_gutter_is_rejected`.

### T3-P18 | ALTO | Inclinação não compensada em tokens Tesseract — RESOLVIDO (006)

- **RUN 006**: todos os critérios atendidos (§1.9): V10 L01/L02 CER médio 0.314 e recall ≥ 0.909; V9 p81 0.000, p17 0.269, p23 0.156; V4 p107/p108 0.035; CER documental V10 0.194, V9 0.077, V4 0.062. Efeitos colaterais: T3-P31 (listas no V10, confirmado) e pequenas pioras pontuais no V4 (p106 `currency`, p156 recall). V10 p62 continua com 0.468.

- **RUN 005 (antes da correção)**: V10 L01/L02 com CER 0.44–0.70 e recall 0.35–0.93; V9 p81 0.91 (recall 0.07), p17 0.55, p9 0.52, p23 0.64; V4 p107 0.311, p108 0.466.
- **Causa raiz (CONFIRMADA por experimento)**: o Tesseract reconhece palavras inclinadas, mas a segmentação de página se degrada. Linhas inteiras se perdem (V10 p48: 353 palavras lidas; 728 com a imagem endireitada; V4 p107: 358 → 838) e linhas de colunas vizinhas se fundem (V10 p18: 34 → 13 linhas atravessando colunas). Agrupar linhas (correção de 004) não recupera o que o Tesseract não leu.
- **Correção pós-005**:
  1. `ocr/backends/tesseract.py::_deskew_page`: depois da leitura da página inteira, `_tsv_line_slope` mede a inclinação pelas linhas do próprio Tesseract (≥ 3 linhas concordantes). Entre 1° e 10°, a página é relida endireitada (`PIL.rotate(expand=True)`). As caixas voltam às coordenadas originais por `_unrotate_tsv` (envoltória dos 4 cantos; erro < 0.05 px verificado), mantendo as identidades de linha. Variável `TESSERACT_DESKEW=0` desativa; o estado aparece na identidade do backend.
  2. Aceite da releitura: rejeitada se a confiança média cair mais de 1 ponto ou se os caracteres reconhecidos ficarem abaixo de 90% dos originais. O primeiro critério testado (palavras × confiança) rejeitava releituras boas, porque a leitura inclinada fragmenta palavras e infla a contagem (V10 p25: 859 "palavras" com confiança 90.7).
  3. Colunas e faixas no referencial retificado: com as caixas de volta às coordenadas originais, a calha entre colunas de uma página inclinada não é vertical (a 4.5° desloca ~16 pt por coluna). `_engine_rows` mede as extensões em `x + m·y`; `ocr_text_column_layout` devolve as calhas nesse referencial e a inclinação; `_order_ocr_lines_by_text_gutters` e `tables/ocr_tracks.py::_crosses_text_column_gutter` comparam no mesmo referencial. Com inclinação 0 o resultado é idêntico ao anterior.
- **Tentativas descartadas** (para a limitação de listas, T3-P31): altura de linha corrigida pela inclinação em `reconstruct_ocr_layout` (eliminou os itens de lista falsos, mas piorou V10 p13 0.278 → 0.492 e p48 0.337 → 0.445) e atribuição de tokens às regiões por `evidence_id` (sem efeito). As duas foram revertidas.
- **Validação pontual (CER, ANTES → FINAL)**:

| Corpus | Página | CER | Recall |
|---|---|---|---|
| V10 | p5 | 0.668 → **0.157** | — |
| V10 | p13 | 0.560 → 0.278 | 0.486 → 0.997 |
| V10 | p18 | 0.468 → 0.322 | — |
| V10 | p25 | 0.703 → 0.333 | — |
| V10 | p31 | 0.670 → 0.277 | 0.370 → 0.969 |
| V10 | p45 | 0.455 → 0.265 | — |
| V10 | p48 | 0.681 → 0.337 | 0.347 → 0.993 |
| V10 | p53 | 0.592 → 0.310 | — |
| V10 | p62 | 0.475 → 0.468 | — |
| V10 | p68 | 0.437 → 0.378 | — |
| V10 | p75 | 0.662 → 0.325 | — |
| V10 | p83 | 0.569 → 0.319 | — |
| V9 | p81 | 0.906 → **0.000** | — |
| V9 | p17 | 0.548 → 0.269 | — |
| V9 | p23 | 0.638 → **0.156** | — |
| V9 | p9 | 0.517 → 0.394 | — |
| V9 | p44 | 0.350 → 0.366 | 0.667 → 0.991 |
| V4 | p107 | 0.311 → **0.035** | — |
| V4 | p108 | 0.466 → **0.035** | — |
| V4 | p105 | 0.040 → 0.035 | — |
| V4 | p135 | 0.066 → 0.045 | — |
| V3 | p80 | 0.005 → 0.000 | — |

  Escopo: V10 0.460 → 0.255, V9 0.303 → 0.130, V4 0.176 → 0.108.
- **Regressões e riscos observados**:
  - **Listas no V10**: precisão de itens 0.571 → 0.333 no escopo (T3-P31).
  - **`numeric` no V4**: escopo 0.816 → 0.797. Nas p107/p108 os acertos sobem (49 → 57; 46 → 59), mas também os números excedentes; na p105, 0.838 → 0.803.
  - **V6 p21**: tabela com `cell_alignment_accuracy` 1.000 → 0.963 no escopo, apesar de o texto do cabeçalho ter melhorado ("Semestre Il o" → "Semestre Il").
  - **V9 p44**: CER 0.350 → 0.366, com recall 0.667 → 0.991.
  - **`heading_level_match_rate` no V10**: 0.833 → 0.714 no escopo.
- **Custo**: uma leitura extra do Tesseract por página com inclinação ≥ 1°. Pelos manifestos, cerca de 12 páginas no V9, 15 no V10 e 11 no V4. No subconjunto, o tempo subiu 9–15% nesses corpora.
- **Restante**: V10 p62 (0.468: endireitada, recall 0.924, mas a ordem segue errada) e V9 p44/p9 (ordem e estrutura).
- **Testes**: `tests/test_tesseract_run005_regressions.py` — medição da inclinação, concordância exigida, mapeamento de volta (marcador sintético a +4.5° e −3°), envoltória dos cantos, releitura aceita/rejeitada, leitura fragmentada não bloqueia a releitura, página reta ou < 1° não relida, colunas de página inclinada ordenadas coluna a coluna, verificação de tabela no referencial retificado.
- **Critério para a 006**: CER de V10 L01/L02 ≤ 0.40 em média e recall ≥ 0.90; V9 p81, p17, p23 < 0.30; V4 p107/p108 < 0.10; páginas sem inclinação com saída idêntica à 005.

- **RUN 004**: V3 p80 0.005; V4 p105 0.040, p106 0.134, p135 0.066, p136 0.111. **Acima do critério**: V4 p107 0.311 e p108 0.466 (−2.5°), V10 L01/L02 0.44–0.70 (±4–5°, baixa qualidade). Próximo passo: endireitar a imagem antes do OCR nas páginas em que o Tesseract funde ou perde linhas.

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

### T3-P22 | ALTO | Colunas fundidas em páginas **nativas** multicoluna — PENDENTE (diagnóstico refinado na 005)

- **RUN 005**: V4 p25/p26/p28 (texto nativo em 3 colunas; 11% das edições do V4); V9 p5, p43, p51 (17% do V9); V10 p9 (0.69) e p59 (0.26) (10% do V10). Recall 1.0: o texto está completo e só a ordem está errada. As páginas nativas de 2 colunas do V4 estão corretas (0.008).
- **Diagnóstico (V4 p25)**: as linhas nativas **estão** separadas por coluna (`_split_groups_by_column_gap`: 156 linhas, só o título largo). O defeito está no **layout**: uma região TEXT ocupa quase a página toda ([29,57,566,542], 111 linhas) e ordena as linhas por altura, intercalando as colunas.
- **Componente**: layout de regiões e ordem de leitura de páginas nativas (`text/reading_order.py`, compartilhado). Requer alteração fora do Tesseract, com validação em V5–V8. É a próxima prioridade.

- V10 p9 (`mixed`, camada de texto nativa, L03 + N04): CER 0.689, sem mudança, com o mesmo padrão de linhas intercaladas.
- Outras L03 nativas estão corretas (p23, p39, p73, p79 ≈ 0.07); p59 tem 0.263.
- O componente é a montagem de linhas nativas (compartilhada, ligada a T3-P05). Requer autorização.

### T3-P23 | CRÍTICO | Parser TSV engolia linhas após palavra iniciada por aspas — RESOLVIDO (004)

- **RUN 004**: V4 `text_coverage_recall` 0.832 → 0.877; V4 `currency` 0.884 → 0.960, `dates` 0.953 → 0.976.

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

### T3-P24 | BAIXO | Título quebrado com travessão virava item de lista — RESOLVIDO (004)

- **RUN 004**: V10 `list_item_detection_precision` 0.641 → 0.771, `list_group_detection_precision` 0.377 → 0.548.

- **Evidência**: com as colunas separadas (T3-P10), cada título de coluna quebrado ("Secretaria Regional de Controle / — Extrato de tramitação") gerou um falso item de lista. Na validação intermediária, a precisão de itens do V10 caiu de 0.765 para 0.639.
- **Causa**: `reconstruct_ocr_layout` trata toda linha OCR iniciada por `–`/`—` como marcador de lista.
- **Correção** (`layout/ocr_aware.py::_is_dash_continuation`): uma linha iniciada por travessão ou meia-risca é continuação quando vem colada à anterior no mesmo fluxo, a anterior não termina em pontuação de frase ou de enumeração e não é item de lista. Hífen e símbolos de bullet continuam iguais.
- **Resultado (V10, escopo pontual)**: precisão de itens 0.765 → **0.886**, de grupos 0.455 → **0.667**. Os itens falsos que já existiam antes também foram removidos.
- **Testes**: `test_em_dash_line_continuing_a_wrapped_title_is_not_a_list_item`.

### T3-P25 | ALTO | Supressão de separador "Página NNN" apagava o corpo da página — RESOLVIDO (004), com efeito colateral (T3-P26)

- **RUN 004**: V3 p96 0.246 com recall 1.0. **Efeito colateral**: a exigência de "uma linha" deixou de suprimir títulos-separadores quebrados em duas linhas em 12 páginas nativas do V3 (T3-P26).

- **Evidência**: com a rotação corrigida, a V3 p96 foi lida corretamente, mas a página saiu **vazia**. O bloco "Página 096 | OCR com rotação física de 90 graus Controle de página: QA-P096 O lote…" foi suprimido inteiro por `_suppress_pagina_separator_blocks`, que suprimia qualquer bloco **iniciado** por "Página N".
- **Correção** (`ocr/tesseract/postprocess.py`): só blocos com formato de separador (uma linha, ≤ 160 caracteres) são suprimidos. Um bloco que continua com o corpo é mantido, ao custo de o texto do separador ficar na saída.
- **Resultado**: V3 p96 0.897 → 0.246, recall 0 → 1.0.
- **Testes**: `tests/test_tesseract_postprocess.py::test_pagina_block_that_continues_with_body_text_is_kept`; os 7 testes anteriores do separador continuam passando.

---

### T3-P26 | ALTO | Títulos-separadores "Página NNN" do V3 voltaram à saída — RESOLVIDO (005)

- **RUN 005**: V3 `heading_precision` 0.929, `heading_text_cer` 0.199, `text_coverage_precision` 0.989 (critérios atendidos); mudaram só as 12 páginas-alvo.

- **Origem**: regressão da RUN 004, causada por T3-P25.
- **Evidência (004)**: 12 títulos "# Página NNN | …" a mais no V3 (p1, 2, 44, 64, 66, 67, 69, 108, 123, 124, 126, 134). O gabarito do V3 declara essas linhas como "delimitadores canônicos" (no V4 elas são marcadores de página removidos por `manifest_pages`). Métricas do V3: `heading_precision` 0.929 → 0.500, `heading_detection_f1` 0.867 → 0.619, `heading_text_cer` 0.199 → 2.694, `text_coverage_precision` 0.979 → 0.968.
- **Causa raiz (CONFIRMADA)**: esses títulos são blocos TITLE longos, que quebram em duas linhas ("Página 001 | Corpus integrado de validação: texto nativo,\nOCR e tabelas", 71 caracteres). A condição "sem quebra de linha" introduzida em T3-P25 deixou de suprimi-los.
- **Correção** (`ocr/tesseract/postprocess.py::_suppress_pagina_separator_blocks`, só Tesseract): um bloco com o padrão do separador e no máximo 160 caracteres é suprimido quando é TITLE (pode ter duas linhas) ou tem uma linha só. Blocos TEXT longos ou com corpo (caso de T3-P25, V3 p96) continuam preservados.
- **Validação pontual (CER, ANTES → DEPOIS)**: V3 p1 0.097 → 0.000, p2 0.053 → 0.000, p44 0.333 → 0.000, p64 0.265 → 0.000, p66 0.387 → 0.000, p67 0.600 → 0.000, p69 0.656 → 0.000, p108 0.425 → 0.123, p123 0.309 → 0.000, p124 0.312 → 0.000, p126 0.343 → 0.006, p134 0.353 → 0.000.
- **Escopo V3 (20 páginas)**:
  - CER 0.162 → 0.044.
  - `heading_detection_f1` 0.400 → 1.000; `heading_text_cer` 9.500 → 0.000.
  - `text_coverage_precision` 0.876 → 0.984; `numeric` 0.951 → 0.993; `duplicate_excess_rate` 0.162 → 0.046.
  - `text_coverage_recall` 0.980 → 0.969. A cobertura é contada por palavras do documento, e palavras dos títulos removidos também aparecem no gabarito; o recall por página não mudou.
- **Controles**: V3 p96/p97 (separador com corpo; rotação) inalterados; V4–V10 com saída idêntica byte a byte.
- **Testes**: `tests/test_tesseract_postprocess.py::test_wrapped_pagina_title_block_is_suppressed`. `test_pagina_block_that_continues_with_body_text_is_kept` (T3-P25) continua passando.
- **Critério para a 005**: V3 `heading_precision` ≥ 0.92 e `heading_text_cer` ≤ 0.20 (valores da 003); V3 `text_coverage_precision` ≥ 0.979.

### T3-P27 | CRÍTICO | Página nativa inteira descartada como marcador do corpus (V4 p213/p214) — RESOLVIDO (005)

- **RUN 005**: p213/p214 com CER 0.041 e recall 1.000; V4 `text_coverage_recall` 0.888. Mudaram só essas duas páginas.

- **Evidência**: V4 p213 e p214 (`rotation_metadata_90`, página nativa com `/Rotate 90` e cerca de 4000 caracteres) saem **vazias** desde a 001: CER 1.000 e recall 0.000 nas rodadas 001–004. O run manifest marca as páginas como `valid`/`clean`, ou seja, a perda era silenciosa.
- **Causa raiz (CONFIRMADA)**:
  1. O layout de página nativa com `/Rotate 90` junta a página inteira num único bloco HEADER (3925 caracteres, 35 linhas). Ver T3-P29.
  2. Esse bloco contém o cabeçalho do corpus e o rodapé "RODAPÉ DECORATIVO • OMITIR NO GABARITO".
  3. `_suppress_ocr_corpus_markers` suprime o bloco quando `_OMITIR_GABARITO_RE.search` encontra o marcador **em qualquer ponto** do texto. É o risco registrado em T3-P09.
- **Medição**: os blocos de marcador legítimos suprimidos em V3 p96/97/99/116 e V4 p82/111/150/190/213 têm 39–239 caracteres e no máximo 2 linhas.
- **Correção** (`ocr/tesseract/postprocess.py`, só Tesseract): `_MARKER_BLOCK_MAX_CHARS = 400`. Um bloco não-tabela maior que isso não é suprimido como marcador, porque contém conteúdo. Blocos de tabela mantêm a regra do marcador partido entre linhas (Fix-N).
- **Validação pontual (ANTES → DEPOIS)**:
  - V4 p213/p214: CER 1.000 → **0.041**, recall 0.000 → **1.000**, precisão 0.963, `numeric` 0.977, `dates` 1.000.
  - Escopo V4 (11 páginas): CER 0.264 → 0.090, recall 0.694 → 0.876, `dates` 0.889 → 0.992, `currency` 0.890 → 0.980.
  - Todas as outras páginas do V4 e todos os demais corpora: saída **idêntica byte a byte**.
- **Restante**:
  - O cabeçalho e o rodapé do corpus ficam no texto dessas duas páginas (custo pequeno de CER).
  - A página continua num único bloco: `paragraph_detection_f1` 0 nessas páginas, e `aligned_block_text_cer` do escopo V4 0.754 → 0.884. Ver T3-P29.
- **Testes**: `tests/test_tesseract_postprocess.py::test_marker_inside_whole_page_block_does_not_suppress_page_content`, `test_marker_sized_blocks_are_still_suppressed`. Os testes existentes de marcadores, inclusive os de tabela, continuam passando.
- **Critério para a 005**: V4 p213/p214 com recall ≥ 0.95; V4 `text_coverage_recall` > 0.877.

### T3-P28 | ALTO | Valor monetário colado em célula de tabela (V8 p4/p58) — RESOLVIDO (006)

- **RUN 006**: V8 p4/p58 com `€ 98,40` e CER 0.000; V8 `numeric` 0.991, `cell_content_exact_match` 0.999; nenhuma outra página do V8 alterada.

- **Causa raiz (CONFIRMADA)**:
  1. Na mesma célula existem duas leituras do mesmo valor, com caixas quase idênticas (x 310.07 e 310.45): a da página, "96,40" (confiança 0.632, errada), e a de uma releitura de região, "98,40" (confiança 0.925, correta).
  2. A releitura entra como proposta de COMPLEMENTO (`ocr/pipeline/integrator.py`). `_reject_duplicates` só barra duplicatas de **texto igual**, então as duas ficam.
  3. O agrupamento por linha do Tesseract (T3-P10) as põe na mesma linha, que vira a célula `€ 98,4096,40`.
- **Alternativas**:
  - Resolver no integrador compartilhado: o complemento substituiria a leitura conflitante. Afeta todos os motores e o contrato "toda evidência é integrada por exatamente uma proposta".
  - Resolver na montagem de linhas do Tesseract (escolhida).
- **Correção** (`ocr/reconstruct.py::_drop_conflicting_rereads`, caminho com identidade de linha, só Tesseract): duas leituras de **chamadas diferentes** do Tesseract (prefixo do `layout_line_id`) com IoU ≥ 0.6 são o mesmo ponto lido duas vezes, e fica a de maior confiança. Leituras de uma mesma chamada, sobreposições parciais em bordas de recorte e tokens de outros motores não são tocados.
- **Validação pontual**: V8 p4/p58 voltam a `€ 98,40`; escopo V8 `cell_cer` 0.023 → 0.002, `cell_content_exact_match` 0.958 → 0.986, `numeric` 0.872 → 0.947. Todas as outras 54 páginas: saída idêntica à etapa anterior.
- **Testes**: `test_conflicting_rereads_of_one_cell_keep_the_most_confident_reading`, `test_rereads_are_only_resolved_across_calls_and_for_near_identical_boxes`.
- **Critério para a 006**: V8 p4/p58 com `€ 98,40` e `numeric` da página ≥ 0.909; nenhuma outra página do V8 alterada.

- **Evidência (004)**: célula "€ 98,40" sai como `€ 98,4096,40` em V8 p4 e p58 (a mesma tabela). Na mesma linha, "1-01" passou a "L-01", que é o correto segundo o gabarito. `numeric` da página 0.909 → 0.857.
- **Hipótese**: duas leituras sobrepostas do mesmo valor ("98,40" e "96,40", de passes diferentes de OCR de célula) ficaram no mesmo grupo de linha. O agrupamento por linha do Tesseract (T3-P10) não as deduplica como o agrupamento anterior por palavra. Precisa de depuração no refinamento de células (`_refine_visual_table_ocr`).
- **Abrangência**: busca por números colados nas saídas de V3–V10 da 004 encontrou só esse caso.
- **Critério**: V8 p4/p58 com `€ 98,40` e `numeric` ≥ 0.909.

### T3-P29 | MÉDIO | Página nativa com `/Rotate 90` montada como um único bloco — NOVO

- V4 p213/p214: toda a página vira um bloco HEADER, sem parágrafos nem títulos. Após T3-P27 o texto é preservado, mas a estrutura não.
- **Componente**: layout de regiões e montagem de linhas nativas em páginas rotacionadas (compartilhado, não Tesseract). Requer investigação própria.

### T3-P30 | INFO | Executor grava `threads` fixo no run manifest — RESOLVIDO (006)

- **RUN 006**: `run_summary.json` e os 8 manifestos registram `threads: 8`, `tesseract_omp_thread_limit: 8` e `requested_threads: 8`.

- **Correção** (`scripts/run_tesseract_batch_wsl.py`):
  - `round_settings(config)` gera as configurações do `run_summary.json` a partir de `build_config()`.
  - O run manifest grava `threads = config.num_threads`, `tesseract_omp_thread_limit` (o `OMP_THREAD_LIMIT` efetivo do backend), e `runtime_environment.requested_threads`/`cpu_count`.
  - A configuração de 8 threads foi mantida.
- **Histórico (não alterado)**: os manifestos e resumos das rodadas 001–005 registram `threads: 6`. Configuração efetiva: 001–003 = 6; 004 = indeterminada (alteração só local); 005 = 8.
- **Testes**: `tests/test_tesseract_batch_runner_config.py` — `round_settings` reflete a config, nenhum número de threads fixo no script, e a expectativa de `num_threads` atualizada para 8 (configuração estabelecida para a 005).

- `extract_one` grava `"threads": 6` e `requested_threads: 6` literais, independentemente de `build_config().num_threads`. Com a alteração local para 8 threads, a 004 não registra a configuração efetiva.
- **Ação sugerida**: gravar `config.num_threads` (e `effective_threads`). Não alterado nesta etapa, por estar fora das duas correções autorizadas.

### T3-P31 | BAIXO | Títulos de coluna com travessão viram item de lista em páginas endireitadas — NOVO (não resolvido)

- **Evidência (pós-005, pontual)**: nas páginas V10 L01/L02 recuperadas por T3-P18, a continuação "— Prestação de contas" de títulos de coluna aparece como item de lista. Precisão de itens de lista no escopo V10: 0.571 → 0.333.
- **Diagnóstico**: a regra de T3-P24 reconhece a continuação corretamente (verificado). A divisão do bloco ocorre antes: numa página inclinada, a caixa de cada linha (alinhada aos eixos) fica mais alta por |inclinação| × largura, mais nas linhas longas do corpo do que nos títulos. A razão título/corpo cai abaixo de 1.28 e o título vira TEXT; a continuação vai para outro bloco, e a detecção de listas da montagem de conteúdo usa o travessão como marcador.
- **Tentativas (5)**: endireitar (causa), ordenação retificada, critério de aceite (mantidas por T3-P18); altura corrigida pela inclinação na classificação (resolveu as listas, mas regrediu o CER de V10 p13/p48 por mudar a divisão em regiões, que embaralha linhas na montagem); tokens por `evidence_id` (sem efeito). As duas últimas foram revertidas.
- **Próximo passo**: entender por que a divisão em mais regiões embaralha linhas na montagem de conteúdo de páginas inclinadas antes de reaplicar a correção de altura.

- **RUN 006 (confirmação)**: a regressão se materializou no documento inteiro. V10 `list_item_detection_precision` 0.771 → 0.659, `list_group_detection_precision` 0.548 → 0.354, `list_item_detection_f1` 0.871 → 0.795; `heading_level_match_rate` 0.989 → 0.977. **20 itens de lista falsos** em 12 páginas (as L01/L02: 5, 13, 18, 25, 31, 45, 48, 53, 62, 68, 75, 83), todos continuações "— Prestação de contas" / "— Extrato de tramitação" / "— Procedimento de verificação" de títulos de coluna. O gabarito espera esses pares como um título ("### Secretaria Regional de Controle — Prestação de contas"). Texto preservado (recall 0.91–1.00).
- **Mecanismo (medido pós-006)**: a regra de T3-P24 marca a continuação como não-lista **no layout OCR**, mas, como o título inclinado não passa no teste de altura, título, continuação e corpo ficam numa mesma região TEXT; na montagem, `text/lists.py::segment_list_lines` (compartilhado) vê duas ou mais linhas iniciadas por travessão e cria a lista. Além disso, na p18 a ordem correta das colunas dentro dessa região dependia da geometria das linhas: `text/reading_order.py::_order_prose_lines` (compartilhado) mantém um único grupo com as linhas como estão, e qualquer mudança de geometria ou de fronteiras de região faz a detecção genérica intercalar as colunas inclinadas.
- **Tentativas pós-006 (5, nenhuma mantida)** — 54 páginas de 6 corpora (V3, V4, V6, V8, V9, V10) com o código do Problema 1 como referência:

| # | Hipótese | Alteração | Resultado no escopo V10 (19 págs.) | Decisão |
|---|---|---|---|---|
| 1 | Unir a continuação à linha que ela continua impede qualquer componente de ler o travessão como marcador | `layout/ocr_aware.py`: `_join_dash_continuations` + `_merge_continuation` (altura de classificação = maior linha de origem) | listas: precisão de itens 0.182 → **0.533**, de grupos 0.059 → **0.400**, F1 de itens 0.308 → 0.696; recall +0.003 a +0.013; demais corpora idênticos; **p18 CER 0.322 → 0.525** (parágrafos das duas colunas intercalados linha a linha) | descartada (regressão grave e reproduzível de ordem) |
| 2 | Região só-OCR não deve atravessar uma calha de coluna validada | + quebra de região a cada mudança de coluna | listas iguais à #1; p5 0.157 → 0.356, p45 0.265 → 0.498, p31, p48, p75 pioram | descartada |
| 3 | A ordem por calhas do motor deve ser preservada na montagem | + marca `order_mode` e ramo em `text/reading_order.py` | p62 0.468 → 0.210, p13, p31 melhoram; p45 → 0.498, p48 → 0.470, p75 → 0.439 e **controle reto p50 0.064 → 0.123** pioram | descartada |
| 4 | O par "Órgão — Assunto" unido é título de seção | + TITLE para linha unida curta (≤ 80 caracteres, sem pontuação de frase) | listas iguais à #1; p5 → 0.308, p45 → 0.524, p75 → 0.549, controle p6 0.036 → 0.053 | descartada |
| 5 | Preservar as fronteiras de região anteriores (a linha unida fecha a região) | + fechamento de grupo após a linha unida | premissa refutada (a continuação não formava região própria no layout); medição: p45 → 0.539, p18 → 0.532, p75 → 0.511 | descartada |

- **Conclusão**: com o pipeline atual, a separação correta das colunas inclinadas nessas páginas é frágil; os falsos itens de lista funcionavam como separadores acidentais. Corrigir as listas sem tocar a ordem exige uma ordem de linhas robusta à inclinação na montagem (`text/reading_order.py`, compartilhado com o nativo) ou uma junção que não altere a geometria vista por ela — fora do escopo autorizado nesta etapa (não alterar o extrator nativo). Todo o código das 5 tentativas foi revertido; `layout/ocr_aware.py` e `text/reading_order.py` estão idênticos ao commit `dfbf5e9`.
- **Próximos passos sugeridos**: (a) junção que mantém a caixa da linha de título (a altura de duas linhas é o que dispara a separação em pistas, verificado no rastreamento da p18); (b) com autorização para o componente compartilhado, ordenar linhas OCR no referencial retificado pela inclinação medida (T3-P18).

### T3-P32 | ALTO | Tabela raster girada 90°/270° com a grade transposta — CORRIGIDO — VALIDADO PONTUALMENTE (pós-006, parcial)

- **Evidência (006)**: no V9, das 6 tabelas raster giradas (manifesto `rotation_degrees` ±90/270), só p3 sai correta. p20 e p71 saem com o **texto certo e a grade transposta e invertida** (primeira linha "004 | 003 | 002 | 001 | Rubrica"; `cell_content_exact_match` 0.042 e 0.000; os valores monetários ficam em células erradas). p36 e p92 saem ilegíveis; p55 mistura as duas coisas.
- **Gravidade**: dados críticos (valores R$, quantidades) associados à linha errada ou perdidos em tabela; mecanismo genérico de qualquer documento com tabela girada.
- **Causa raiz (CONFIRMADA por rastreamento)**: o Tesseract (PSM 3) também lê texto vertical, e o faz com confiança alta. No recorte da tabela da p20 a leitura direta tem 24 palavras com confiança média 95, todas com caixa mais alta que larga. O retry de quarto de volta (T3-P02) só disparava com confiança < 60 ou com ≥ 60% das **linhas de 3+ palavras** verticais; células de tabela têm 1–2 palavras por linha, então nenhum gatilho valia. Os tokens ficavam com `rotation = 0` e o construtor de tabelas montava a grade no referencial da página. Na p3 a leitura direta tinha confiança 45, o retry disparava, os tokens recebiam `rotation = 270` e a tabela saía certa.
- **Tentativas**:

| # | Hipótese | Alteração | Resultado (54 páginas, 6 corpora) | Decisão |
|---|---|---|---|---|
| 1 | A forma das palavras (≥ 3 alfanuméricos, altura ≥ 1.2 × largura) indica leitura vertical também em linhas curtas | `_tsv_vertical_word_ratio`/`_tsv_reads_vertically`; usado no gatilho **e** como filtro dos candidatos girados | p20 0.042 → 0.950, p71 0.000 → 1.000; **regressão** V8 p4/p58 ("L-01" → "1-01", `numeric` 1.000 → 0.909) | ajustar |
| 2 | Leitura vertical confiante pode ter o texto certo em qualquer orientação; o formato deve **ordenar** os candidatos, não eliminá-los | candidatos ≥ 70 ordenados por (palavras horizontais, confiança); quando o retry foi disparado só pelas palavras verticais, só uma leitura horizontal substitui a original | p20/p71 mantidos; V8 volta a "L-01"; V3, V4, V6, V8, V10 idênticos ao ANTES | **mantida** |

  A regressão do V8 p4 da tentativa 1 foi explicada: o recorte de uma linha da tabela tem confiança 56 ("-01"); a leitura a 90° é vertical, mas com o texto certo ("L-01", 82), e era aceita antes. Na tentativa 2 ela continua aceita, porque não há candidato horizontal com confiança ≥ 70.
- **Correção** (`ocr/backends/tesseract.py`, só Tesseract): `_VERTICAL_WORD_MIN_CHARS = 3`, `_VERTICAL_WORD_MIN_ASPECT = 1.2`, `_tsv_vertical_word_ratio`, `_tsv_reads_vertically`; `_retry_quarter_turns` com gatilho pelas palavras e preferência por leitura horizontal. Nenhuma regra depende de texto ou de página.
- **Validação pontual (ANTES → DEPOIS)**:

| Página | CER | `cell_content_exact_match` | `cell_cer` | Observação |
|---|---|---|---|---|
| V9 p20 | 0.069 → **0.001** | 0.042 → **0.950** | 1.294 → 0.008 | grade correta |
| V9 p71 | 0.068 → **0.000** | 0.000 → **1.000** | 1.372 → 0.000 | recall 0.991 → 1.000 |
| V9 p55 | 0.179 → 0.177 | 0.657 → 0.794 | 0.319 → 0.180 | ver abaixo |
| V9 p3 (controle girado) | 0.026 | 1.000 | 0.000 | idêntica |
| Escopo V9 (15 págs.) | 0.149 → 0.139 | 0.350 → 0.479 | 0.747 → 0.592 | `numeric` 0.906 → 0.906 |

- **Resíduo e risco (p55)**: a tabela girada passa a sair orientada (Ação × Prazo nas linhas certas), mas a coluna "Situação" fica fora da grade como parágrafos soltos, e dois números da coluna "Ação" ("02", "03") saem como "=" / "pm" (antes eram lidos, mas em células transpostas). `numeric` da página 0.974 → 0.919. É uma perda pontual de dado numérico, reproduzível, que fica registrada para a 007.
- **Não resolvido**: p36 (tabela girada dentro de página raster inclinada; nenhuma leitura de região da tabela é feita) e p92 (tabela girada não detectada). Exigem detectar a região da tabela antes de relê-la.
- **Testes**: `tests/test_tesseract_run006_regressions.py` — medida das palavras verticais, gatilho em recorte confiante vertical, recorte horizontal não relido, números curtos não disparam, leitura vertical de baixa confiança continua aceitando o candidato vertical (comportamento T3-P02), preferência pela leitura horizontal, retry por palavras não troca vertical por vertical.

### T3-P33 | MÉDIO | Exceção `Invalid BBox` na estrutura de tabela por trilhas OCR (V9 p36) — PENDENTE

- **Evidência**: único motivo do `partial_success` do V9 na 006: `OCR table structure unavailable: ValueError: Invalid BBox coordinates: BBox(x0=63.2, y0=526.1, x1=237.8, y1=521.3)`. Reproduzido no harness.
- **Causa (CONFIRMADA)**: `text_tracks._build_table` (detector nativo de tabelas sem borda, compartilhado) define as bordas das linhas da tabela pelo ponto médio entre centros consecutivos, supondo centros crescentes. O candidato da p36 é formado por linhas OCR que se sobrepõem verticalmente (alturas 9–32 pt, y0 513–536): o parágrafo da coluna esquerda fundido com o lixo da tabela girada 270°. Uma linha alta tem centro abaixo da seguinte e a borda inverte.
- **Impacto real**: a exceção impediu uma tabela falsa (na 005 o mesmo candidato virou `<table>` com "Os comprovantes de pagamento." nas células). O defeito é de execução/status, não de conteúdo.
- **Não escolhido nesta etapa**: a correção natural é no detector compartilhado (rejeitar candidato cujas linhas não formam uma sequência vertical válida), fora do pipeline Tesseract e do escopo autorizado (não alterar o extrator nativo). A causa da página (tabela girada ilegível) é T3-P32.

### T3-P34 | ALTO | Tabela raster com inclinação pequena em página mista não detectada — PENDENTE (inventário 006)

- **Evidência (006)**: V9 p58 (−3.4°), p66 (−4.0°), p78 (−3.6°) sem tabela detectada (`cell_content_exact_match` 0; `currency` 0.86 / 0.63); p25 (−4.0°) 0.118 e p8 (−1.8°) 0.375. As linhas da tabela saem como texto corrido ("001 peças de reposição |1 | R$ 206,74 R$ 206,74 Regular 002 | …").
- **Hipótese**: a detecção visual da grade (`tables/visual.py`, Hough + grade alinhada aos eixos) rejeita réguas inclinadas, e a releitura endireitada (T3-P18) só corrige o texto. Componente de detecção visual compartilhado. Próxima prioridade de tabelas.

## §3 Alterações de código

### Série 3 até a 003 (commit `db2ecb8`)

| Item | Arquivo | Mudança |
|---|---|---|
| T3-P00 | `scripts/run_tesseract_batch_wsl.py` | `build_config()` canônica; `extractor_flags` no run manifest |
| T3-P11 | `scripts/run_tesseract_batch_wsl.py` | V10 em `CORPORA`; fallback `_REFERENCIA_PAGINADA.md` com prioridade −1 |
| T3-P01 | `src/structured_pdf_text/api.py` | `_append_hybrid_ocr_regions(page_raster_primary=…)`, `_PAGE_SIZED_FIGURE_COVERAGE=0.75`, atribuição de OCR de página a figuras `OCR_REGION`/`MERGE_OCR` |
| T3-P03 | `src/structured_pdf_text/api.py` | `_MIN_TEXT_REGION_SIDE_PT=8.0` em `_recover_selected_regions` |

### Pós-003 (2026-11-12, commit `976f7f0`)

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

### Pós-004 (2026-11-13, commit `fff2a4b`)

| Item | Arquivo | Mudança | Escopo |
|---|---|---|---|
| T3-P26 | `src/structured_pdf_text/ocr/tesseract/postprocess.py` | separador "Página NNN": TITLE de até 160 caracteres pode ter duas linhas | só Tesseract |
| T3-P27 | `src/structured_pdf_text/ocr/tesseract/postprocess.py` | `_MARKER_BLOCK_MAX_CHARS = 400` em `_suppress_ocr_corpus_markers` (exceto tabelas) | só Tesseract |
| testes | `tests/test_tesseract_postprocess.py` | +3 testes | — |

A alteração local `num_threads=8` em `scripts/run_tesseract_batch_wsl.py` não é desta etapa e foi mantida como está.

### Pós-005 (2026-11-13, não commitadas)

| Item | Arquivo | Mudança | Escopo |
|---|---|---|---|
| T3-P18 | `src/structured_pdf_text/ocr/backends/tesseract.py` | `_tsv_line_slope`, `_unrotate_tsv`, `_tsv_recognized_chars`, `_deskew_page`; `TESSERACT_DESKEW`; identidade `deskew` | só Tesseract |
| T3-P18 | `src/structured_pdf_text/ocr/reconstruct.py` | `_engine_rows` mede extensões no referencial retificado e devolve a inclinação; `ocr_text_column_layout` | só com identidade de linha |
| T3-P18 | `src/structured_pdf_text/layout/ocr_aware.py` | `_order_ocr_lines_by_text_gutters(..., slope)` | só com calhas do motor |
| T3-P18 | `src/structured_pdf_text/tables/ocr_tracks.py` | `_crosses_text_column_gutter(..., slope)` | só com calhas do motor |
| T3-P28 | `src/structured_pdf_text/ocr/reconstruct.py` | `_drop_conflicting_rereads` | só com identidade de linha |
| T3-P30 | `scripts/run_tesseract_batch_wsl.py` | `tesseract_thread_limit`, `round_settings`; threads da config no manifesto e no resumo | executor |
| testes | `tests/test_tesseract_run005_regressions.py` (novo, 12 testes); `tests/test_tesseract_batch_runner_config.py` (+2, expectativa de threads = 8) | — | — |

As alterações pós-005 entraram no commit `dfbf5e9` (avaliadas na RUN 006).

### Pós-006 (2026-11-16, não commitadas)

| Item | Arquivo | Mudança | Escopo |
|---|---|---|---|
| T3-P32 | `src/structured_pdf_text/ocr/backends/tesseract.py` | `_VERTICAL_WORD_MIN_CHARS`, `_VERTICAL_WORD_MIN_ASPECT`, `_tsv_vertical_word_ratio`, `_tsv_reads_vertically`; `_retry_quarter_turns` também dispara quando a maioria das palavras é vertical e prefere a leitura girada com palavras horizontais | só Tesseract |
| T3-P12 | `src/structured_pdf_text/ocr/backends/tesseract.py` | `_TEMP_PNG_COMPRESS_LEVEL = 1` / `_save_temp_png` para o PNG temporário (TSV e OSD) | só Tesseract; sem perdas |
| testes | `tests/test_tesseract_run006_regressions.py` (novo, 9 testes); `tests/test_tesseract_effective.py` (+1) | — | — |
| T3-P31 | `layout/ocr_aware.py`, `text/reading_order.py` | 5 tentativas, **todas revertidas** (arquivos idênticos a `dfbf5e9`) | — |

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

| 2026-11-13 | RUN 004 completa (manual) | 8/8 `ok` |
| 2026-11-13 | métricas da 003 do V10 recalculadas com o manifesto (scratchpad) | `ok`, 61 aplicáveis |
| 2026-11-13 | validação pontual T3-P26/T3-P27 (§1.6) | ANTES → T3-P26 → T3-P27, 8 corpora, 47 páginas |
| 2026-11-13 | `pytest tests/test_tesseract_postprocess.py` | todos passaram (3 testes novos) |
| 2026-11-13 | `pytest tests` (após T3-P26/T3-P27) | 14 falhas: as 13 de T3-P14 + `test_tesseract_batch_runner_config.py::test_runner_config_keeps_requested_round_parameters`, que espera `num_threads == 6` e falha pela alteração local para 8 threads (T3-P30), não pelas correções |

| 2026-11-13 | RUN 005 completa (manual) | 8/8 `ok` |
| 2026-11-13 | validação pontual pós-005 (§1.8) | ANTES → endireitamento (3 iterações) → T3-P28; 59 páginas, 8 corpora |
| 2026-11-13 | medição controlada de threads (V4 p85, Tesseract direto) | 6 = 3.95 s, 8 = 3.96 s (mediana de 4) |
| 2026-11-13 | testes direcionados: `test_tesseract_run005_regressions.py`, `test_tesseract_run003_regressions.py`, `test_tesseract_effective.py`, `test_tesseract_batch_runner_config.py`, `test_tesseract_postprocess.py`, `test_ocr_parser_regressions.py`, `test_furniture_body_protection.py` | todos passaram |
| 2026-11-13 | suíte completa | **não executada** nesta etapa (interrompida a pedido) |

**Não executado**: nenhuma rodada completa após as correções pós-005; `output/tesseract/006` não existe; suíte completa não executada.

| 2026-11-16 | RUN 006 completa (manual) | 8/8 com métricas; V9 `needs_review` (T3-P33) |
| 2026-11-16 | benchmarks pontuais de threads/concorrência/PNG (scratchpad, §1.10) | 8 ≡ 4 threads; TSV idêntico; PNG nível 1 sem perdas |
| 2026-11-16 | harness por página (`page_indices`, métricas oficiais `selected_pages`): ANTES (54 págs.) | 49/51 páginas comparáveis idênticas à 006 (V4 p85/p110 dependem do documento) |
| 2026-11-16 | T3-P32 tentativas 1 e 2; T3-P31 tentativas 1–5 (mesmas 54 páginas; a 5ª só no V10) | §2 T3-P32 e T3-P31 |
| 2026-11-16 | validação final: 39 páginas de controle extras + alvos do T3-P32 (V4, V5, V6, V7, V8, V9, V10) | só V9 p20/p55/p71 mudam por efeito do código; diferenças em V4 p25/86/140/150/190, V6 p3/p33, V10 p59 são de contexto de documento (rastreamento: nenhuma decisão de retry diferente da regra antiga) |
| 2026-11-16 | `pytest` direcionado (`test_tesseract_*`, `test_easyocr_pipeline_integrations.py`, ordem de leitura) | todos passaram |
| 2026-11-16 | `pytest tests` (código final pós-006) | **13 falhas = exatamente as de T3-P14** (`test_line_detector.py` 8 `test_reconciliation_*`, `test_rapidocr_improvement_005/006/008.py` 3, `test_no_timeouts.py` 1, `test_evaluation_exact_optimizations.py[V3]` 1); nenhuma falha nova |

**Páginas processadas nos testes pontuais pós-006** (extração real apenas dessas páginas, via `page_indices`; resultados em diretórios experimentais no scratchpad, nada gravado em `output/`):

| Corpus | Conjunto principal (ANTES, T1, T2, P2T1–P2T4; P2T5 só V10) | Controles extras (FINAL) |
|---|---|---|
| V3 | 80, 96, 97, 99, 116 | — |
| V4 | 85, 107, 108, 109, 110, 113, 164 | 25, 26, 82, 86, 116, 136, 140, 150, 190 |
| V5 | — | 10, 17, 23 |
| V6 | 21, 31 | 3, 33, 89 |
| V7 | — | 24, 25 |
| V8 | 4, 22, 58 | 4, 46, 58 |
| V9 | 3, 4, 9, 17, 20, 23, 25, 36, 44, 50, 55, 58, 71, 81, 92 | 2, 3, 8, 20, 31, 41, 51, 53, 55, 62, 67, 71, 72, 84 |
| V10 | 4, 5, 6, 9, 13, 15, 18, 25, 30, 31, 41, 45, 48, 50, 53, 62, 68, 75, 83 | 8, 11, 19, 21, 24, 40, 52, 59, 72 |

Configuração: `build_config()` do executor (tesseract, balanced, pt-BR, adaptive, 8 threads, `OMP_THREAD_LIMIT` 8), Markdown paginado com os números de página originais.

**Tempo de extração do conjunto principal (s, ANTES → T2 final)**: V3 28.7 → 29.0 · V4 123.3 → 125.1 · V6 53.4 → 49.4 · V8 42.2 → 43.2 · V9 157.8 → 158.9 · V10 224.6 → 228.8 — sem mudança além do ruído. O retry extra só ocorre em recortes com palavras majoritariamente verticais (3 leituras a mais por recorte).

## §5 Validação esperada na RUN 007

Comparar 007 × 006.

| Verificação | Critério |
|---|---|
| T3-P32 | V9 p20 e p71 com a tabela na orientação de leitura (`cell_content_exact_match` ≥ 0.9 nas páginas); V9 `cell_content_exact_match` documental > 0.527 e `cell_cer` < 0.579; V9 `numeric` ≥ 0.950 e `currency` ≥ 0.962 |
| T3-P32 (risco) | V9 p55: conferir a coluna "Situação" e os números "01–04" da tabela girada; V3 p96–p99/p116 e V4 p109–p114 (rotação de página) sem mudança de CER > 0.01 |
| PNG temporário | saídas de V3–V10 idênticas à 006 em todas as páginas sem tabela girada (a mudança é sem perdas) |
| Controle | V3, V4, V5, V6, V7, V8 e V10 idênticos byte a byte à 006, exceto páginas com recortes lidos como texto vertical (esperado: nenhuma fora do V9); métricas de texto de V9/V10 sem piora > 0.005 |
| T3-P31 | sem mudança (não corrigido): V10 `list_group_detection_precision` = 0.354 |
| Desempenho | extração dentro de ±10% da 006 (ruído observado: até +14% em corpora com saída idêntica) |
| Geral | nenhum `extract_error`/`metrics_error`/`corpus_changed`; V9 continua `partial` por T3-P33 (esperado) |

**Limitações**:
- A validação cobriu 93 páginas distintas; o gatilho por palavras verticais roda em toda leitura Tesseract, então só a 007 confirma a ausência de efeito no restante.
- Fusão de tabelas entre páginas e supressão de repetidos não são exercitadas por subconjuntos (ver diferenças de contexto acima).

Comando: `python3 -u scripts/run_tesseract_batch_wsl.py 007` (pré-verificação: `--check-only`). **Não altere `corpus/` durante a rodada.**

## §5.0 (histórico) Validação esperada na RUN 006 — resultado em §1.9

Comparar 006 × 005.

| Verificação | Critério |
|---|---|
| T3-P18 | V10 L01/L02 (p5, 13, 18, 25, 31, 45, 48, 53, 62, 68, 75, 83): CER médio ≤ 0.40 e recall ≥ 0.90; V9 p81, p17, p23 < 0.30; V4 p107/p108 < 0.10; CER documental V10 < 0.264, V9 < 0.102, V4 < 0.069 |
| T3-P28 | V8 p4/p58 com `€ 98,40`; V8 `numeric` ≥ 0.988 |
| T3-P30 | `run_summary.json` e manifestos com `threads: 8` e `tesseract_omp_thread_limit: 8` |
| Controle | V3, V5–V8: saída idêntica à 005 fora das páginas inclinadas e da V8 p4/p58; métricas de tabela, listas e dados críticos de V4/V9/V10 sem piora > 0.01, exceto a precisão de listas do V10 (T3-P31, risco conhecido) |
| Desempenho | extração de V4/V9/V10 até +10% (leituras extras em ~38 páginas inclinadas); demais corpora dentro do ruído observado |
| Geral | nenhum `extract_error`/`metrics_error`/`corpus_changed` |

**Limitações**:
- A validação pontual cobriu 59 páginas. Páginas inclinadas fora do conjunto (V9 p36, 49, 55, 75, 87, 91; V10 p12, 16, 72; V4 p106, 136, 148, 156, 164, 172, 180) não foram testadas.
- A suíte completa de testes não foi executada nesta etapa.

Comando: `python3 -u scripts/run_tesseract_batch_wsl.py 006` (pré-verificação: `--check-only`). **Não altere `corpus/` durante a rodada.**

## §5.1 (histórico) Validação esperada na RUN 005 — resultado em §1.7

Comparar 005 × 004.

| Verificação | Critério |
|---|---|
| T3-P26 | V3 `heading_precision` ≥ 0.92, `heading_text_cer` ≤ 0.20, `text_coverage_precision` ≥ 0.979 |
| T3-P27 | V4 p213/p214 com recall ≥ 0.95; V4 `text_coverage_recall` > 0.877 |
| Controle | V5–V10: saída idêntica à 004 nas páginas sem os padrões de T3-P26/T3-P27 (esperado: idênticas); métricas dentro de ±0.005 |
| Geral | nenhum `extract_error`/`metrics_error`/`corpus_changed` |

**Limitações**: a validação pontual cobriu 47 páginas. As regras alteradas rodam em toda página Tesseract, então só a 005 confirma a ausência de efeito em páginas não testadas com blocos "Página NNN" de duas linhas ou blocos grandes com marcador. Tempos dependem da configuração de threads (T3-P30).

Comando: `python3 -u scripts/run_tesseract_batch_wsl.py 005` (pré-verificação: `--check-only`). **Não altere `corpus/` durante a rodada.**

## §5.2 (histórico) Validação esperada na RUN 004 — resultado em §1.5

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
