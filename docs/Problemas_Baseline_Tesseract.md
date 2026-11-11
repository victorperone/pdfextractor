# Problemas da Baseline Tesseract — Registro Técnico

> **Escopo**: Série 3 (executor `scripts/run_tesseract_batch_wsl.py`, diretórios `output/tesseract/NNN`).
> A versão anterior deste documento (Série 2, runs 001–008 via `run_loop.py`) está preservada no git (`git show 5cc81d6:docs/Problemas_Baseline_Tesseract.md`); um resumo está em §7.
> **Protocolo**: atualizar após cada rodada. Um problema só é marcado como RESOLVIDO quando o critério mensurável dele for atendido numa rodada completa, sem regressão relevante nos demais corpora.

---

## §0 Estado atual

| Campo | Valor |
|---|---|
| **Última atualização** | 2026-11-11 — diagnóstico da 001, rodada 002 intermediária e correções para a 003 |
| **Branch** | `feat/tesseract-optimization` (sem commit/push das alterações abaixo) |
| **Baseline imutável** | `output/tesseract/001/` |
| **Baseline corrigida** | `output/tesseract/002/` (somente a correção do executor, T3-P00) |
| **Próxima rodada** | `python3 -u scripts/run_tesseract_batch_wsl.py 003` — valida T3-P01, T3-P03 e inclui o V10 (T3-P11) |
| **Configuração** | balanced · pt-BR · adaptive · tesseract · 6 threads · Markdown paginado · supplemental-audit |
| **Avaliador** | `evaluation-3.0.0`, métricas 3.1.0 — **não alterado** |
| **Corpus/gabaritos/manifestos** | **não alterados** |

### Situação dos problemas

| ID | Gravidade | Prioridade | Componente | Status |
|---|---|---|---|---|
| T3-P00 | CRÍTICO | P0 | executor | **RESOLVIDO** (rodada 002) |
| T3-P01 | CRÍTICO | P1 | `api.py` (compartilhado, autorizado) | CORRIGIDO NO CÓDIGO — validar na 003 |
| T3-P02 | ALTO | P1 | backend Tesseract | PENDENTE — abordagem OSD avaliada e não adotada |
| T3-P03 | MÉDIO | P2 | `api.py` (compartilhado, autorizado) | CORRIGIDO NO CÓDIGO — validar na 003 |
| T3-P04 | INFO | P3 | avaliador | HIPÓTESE REFUTADA — causa em aberto |
| T3-P05 | MÉDIO | P3 | montagem de linhas nativas (compartilhado) | DOCUMENTADO — fora do escopo |
| T3-P06 | ALTO | P2 | headings | PENDENTE (persistiu na 002) |
| T3-P07 | MÉDIO | P2 | tabelas sem borda / ordem (V5) | PENDENTE (reavaliar na 003) |
| T3-P08 | MÉDIO | P2 | dados críticos V9 | PARCIAL na 002 — depende de T3-P01 |
| T3-P09 | INFO | — | `ocr/tesseract/postprocess.py` | RISCO DOCUMENTADO |
| T3-P10 | ALTO | P2 | ordem de leitura do OCR multicoluna (compartilhado) | NOVO — exposto por T3-P01 |
| T3-P11 | ALTO | P1 | executor (V10) | CORRIGIDO NO EXECUTOR — validar na 003 |
| T3-P12 | MÉDIO | P3 | desempenho de extração | NOVO — consequência de T3-P00 |
| T3-P13 | BAIXO | P3 | regressões pontuais 001→002 | MONITORAR |
| T3-P14 | INFO | — | suíte de testes | 13 falhas pré-existentes no `HEAD` |

---

## §1 Rodadas da Série 3

### §1.1 Resumo operacional

| Corpus | Páginas | Gabarito | Extração 001 → 002 (s) | Métricas 001 → 002 (s) | Aplicáveis 001 → 002 | Resultado 001 | Resultado 002 |
|---|---|---|---|---|---|---|---|
| V3 | 144 | reference | 84 → 379 | 3.3 → 2.8 | 57 → 61 | ok | ok |
| V4 | 224 | manifesto | 606 → 2491 | 206.4 → 208.0 | 57 → 61 | ok | ok |
| V5 | 42 | reference | 21 → 91 | 1.9 → 1.5 | 63 → 67 | ok | ok |
| V6 | 95 | reference | 94 → 658 | 3.5 → 3.2 | 59 → 63 | ok | ok |
| V7 | 80 | reference | 60 → 169 | 7.6 → 3.0 | 61 → 65 | ok | ok |
| V8 | 90 | reference | 99 → 410 | 7.8 → 4.0 | 61 → 65 | needs_review (partial_success/partial) | needs_review (partial_success/partial) |
| V9 | 96 | manifesto | 170 → 559 | 9.7 → 9.3 | 63 → 67 | ok | ok |
| V10 | 84 | reference_paginada | não executado | — | — | — | — (incluído a partir da 003) |

### §1.2 Métricas-chave, 001 → 002

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

---

## §2 Problemas — detalhamento

### T3-P00 | CRÍTICO | P0 | Executor sem tabelas e sem supressão de cabeçalho/rodapé — RESOLVIDO

- **Descrição**: `extract_one` criava `ExtractorConfig(mode="balanced", language="pt-BR", ocr_quality_policy="adaptive", ocr_engine="tesseract", num_threads=6)` diretamente. Assim herdava os defaults `enable_tables=False`, `merge_cross_page_tables=False` e `preserve_headers_footers=True`. O caminho canônico (`scripts/evaluate_e2e.py::_build_config`) parte de `best_extraction_config()`.
- **Corpora/páginas**: todos; todas as páginas com tabela ou com cabeçalho/rodapé repetido.
- **Métricas (001)**: `table_detection_f1`=0 e `cell_cer`=1.0 em V3–V9 (status `false_negative_only`); `cer_markdown_raw` V7=0.109; `paragraph_detection_precision` 0.24–0.60; `duplicate_excess_rate` 0.24–0.81.
- **Evidências**: nenhum bloco `<table>` ou pipe table nos 7 `*_extracted_paginated.md`; 79 rodapés "Página N de 80" no V7, cujo gabarito diz "Ignorar cabeçalhos e rodapés decorativos". Reprodução em memória na V7 p.2: 0 tabelas com a config antiga; tabela 6×6 idêntica ao gabarito com a config canônica.
- **Causa raiz**: CONFIRMADA — configuração do executor.
- **Correção**: `build_config()` em `scripts/run_tesseract_batch_wsl.py` = `replace(best_extraction_config(language="pt-BR", preserve_headers=False), mode=BALANCED, ocr_quality_policy="adaptive", ocr_engine="tesseract", num_threads=6)`. O run manifest passou a registrar `extractor_flags` (enable_tables, merge_cross_page_tables, preserve_headers_footers, ocr_quality_variants, política efetiva).
- **Testes**: `tests/test_tesseract_batch_runner_config.py` — flags da config e parâmetros da rodada.
- **Critério → resultado (002)**: `table_detection_f1` ≥ 0.97 em V5–V8 → 1.000 em V5, V6, V7 e V8 ✅; nenhum rodapé "Página N de M" no V7 → `cer_markdown_raw` 0.009 ✅.
- **Status**: RESOLVIDO.

### T3-P01 | CRÍTICO | P1 | OCR de página descartado por figuras — CORRIGIDO NO CÓDIGO

- **Descrição**: em páginas com OCR de página, as linhas OCR que caem dentro de regiões `FIGURE` eram removidas por `_append_hybrid_ocr_regions`, que impede o OCR de figura de virar texto de corpo. Há dois casos:
  1. **Página raster inteira** (raster-primary): a imagem da página vira uma `FIGURE` de 100% da página, e a camada nativa residual (≥ 20 caracteres, por exemplo "Autenticação digital: …") impede o OCR de ser primário (`_should_use_page_ocr_as_primary`). Resultado: todo o OCR é descartado.
  2. **Imagens de texto** (títulos, parágrafos e carimbos rasterizados, marcados pela política de região como `OCR_REGION`/`MERGE_OCR`): o OCR de página é removido e a figura fica sem texto.
- **Corpora/páginas**: V9 — 47 páginas `mixed` com `ocr_outcome=success` e CER médio 0.57 na 001 (p2, p4, p8, p11, p23, p28, p31, p50, p63, p76, p79, p80, p85, p92 ≈ 0.91–0.98) e 5 páginas raster não-full (p10, p16, p33, p61, p87). Na 002, `text_coverage_recall` do V9 = 0.664.
- **Evidências (V9 p.2, em memória)**: `page_ocr_requested=true`; 259 tokens aceitos (`core_authorized_page`); 23 `ocr_supplemental_lines`; região única `FIGURE [0,0,595,842]`; saída de 36 caracteres. A página já era marcada como `raster_primary_candidate` (imagem ≥ 75%, nativo ≤ 200 caracteres) para *pedir* o OCR, mas esse sinal não chegava à montagem.
- **Refuta** a hipótese "Tipo A" da Série 2 ("Tesseract não reconhece imagens de baixa DPI"): o Tesseract reconhece o texto e a montagem o descartava.
- **Correção** (`src/structured_pdf_text/api.py`):
  - `_append_hybrid_ocr_regions(..., page_raster_primary=False)`: em página raster-primary com OCR de página, uma figura com área ≥ 75% da página (`_PAGE_SIZED_FIGURE_COVERAGE`, o mesmo limiar de `_is_raster_primary_candidate`) não bloqueia o OCR, que vai para o suplemento de corpo. O texto nativo é preservado.
  - Uma figura sem OCR próprio, com decisão `OCR_REGION`/`MERGE_OCR`, recebe as linhas do OCR de página contidas nela (a figura continua dona do texto, que não vira corpo nem é duplicado). Figuras sem essa decisão (logos, decorativas) continuam sem receber texto.
  - Chamada: `page_raster_primary=raster_primary and page_ocr_requested`.
- **Validação em memória (V9)**: p2 36 → 1801 caracteres; p4 84 → 1535; p10 0 → 62 (carimbos "EM BRANCO / CONFIRMAÇÃO …"); p23 81 → 543; p31 287 → 2144; p50 221 → 1519. p8 e p16 sem mudança (já estavam corretas).
- **Testes**: `tests/test_tesseract_serie3_regressions.py` (6 testes de T3-P01, incluindo: figura pequena não é liberada; figura sem decisão de texto não recebe OCR; figura com OCR próprio não é sobrescrita; texto não é duplicado). O teste existente `tests/test_hybrid_figure_ocr_ownership.py` continua passando.
- **Riscos a observar na 003**: (a) texto de figuras legítimas aparecendo onde o gabarito não o espera (V5 páginas híbridas, V7 `decorative_graphic` p53/p75); (b) T3-P10.
- **Critério de resolução**: V9 `text_coverage_recall` ≥ 0.80 e queda do `cer_sequential_normalized` do V9 (< 0.392); nenhuma regressão > 0.005 em `text_coverage_precision`, `cer_sequential_normalized` ou `duplicate_excess_rate` em V3–V8 em relação à 002.
- **Status**: CORRIGIDO NO CÓDIGO — aguardando a rodada 003. As verificações em memória de controle em V3–V8 não foram executadas.

### T3-P02 | ALTO | P1 | Páginas escaneadas rotacionadas sem correção de orientação — PENDENTE

- **Corpora/páginas**: V3 p96 (90°), p97 (180°), p98 (270°), p99 (tabela a 90°); CER normalizado por página 0.89 / 0.90 / 1.38 / 1.42 (001). Provavelmente também V3 p116 (OSD indica 90°).
- **Evidência**: `V3_extracted_paginated.md` p.99 "00'00S+ 00'0SE-" (= "+500,00 -350,00" invertido); p.96 "96 euibed" (= "página 96").
- **Causa raiz**: CONFIRMADA. O backend tem OSD (`_run_tesseract_osd`), mas ele vem desligado (`TESSERACT_OSD=0`).
- **Experimento (2026-11-11)**: OSD em páginas renderizadas com escala 2.0:

  | Página | Rotação real | OSD rotate | Confiança |
  |---|---|---|---|
  | V3 p96 | 90° | 90 | 0.79 |
  | V3 p97 | 180° | 180 | 7.57 |
  | V3 p98 | 270° | 270 | 0.94 |
  | V3 p99 | 90° | 90 | 1.19 |
  | V3 p100 | 0° | 180 ❌ | 0.28 |
  | V6 p31 (`rotation`, CER 0.13 já correta) | — | 270 ⚠️ | 6.34 |
  | V3 p80/p95, V6 p3/p33, V8 p39/p67, V9 p9/p44 | 0° | 0 | 4.1–10.2 |
  | V9 p2 | 0° | 0 | 0.89 |

- **Conclusão**: com o limiar padrão (2.0), só a p97 seria corrigida. A V6 p31 seria girada com confiança alta, apesar de já sair correta (risco de regressão). Com limiar menor, entram falsos positivos (p100). **Não adotado** como default.
- **Próxima estratégia**: aplicar OSD apenas em OCR de página inteira sem texto nativo útil, aceitar a rotação só quando o reconhecimento girado tiver confiança média maior que o não girado (comparar as duas leituras) e verificar como a página é canonizada antes do OCR (`/Rotate`) para entender o caso V6 p31.
- **Critério**: CER de V3 p96–p98 < 0.30, sem mudança em páginas não rotacionadas de V3–V10.

### T3-P03 | MÉDIO | P2 | V8 `partial_success` por região menor que um glifo — CORRIGIDO NO CÓDIGO

- **Por que `partial` com `metrics_status: ok`**: `metrics_status` indica apenas que o cálculo de métricas é válido. Já `benchmark_status` vem de `evaluate_e2e._classify_document_status`, e basta uma página com `partial_reasons` para o documento inteiro ficar `partial`.
- **Páginas**: V8 p22 e p25 (`/Rotate 180`, paisagem). O resultado se repetiu na 002.
- **Evidência**: `page-22:region-9`, `UNKNOWN`, bbox 6×6 pt junto ao rodapé, `native_text_missing + visible_ink_present`. O OCR retorna `no_text` → `ocr_required` (não é figura) → `ocr_failed` → `ocr_region_recovery_unavailable`. O conteúdo da página está completo em relação ao gabarito.
- **Correção** (`api.py::_recover_selected_regions`): uma região com largura **e** altura < 8 pt (`_MIN_TEXT_REGION_SIDE_PT`) e resultado vazio deixa de ser OCR obrigatório. Erros de execução continuam contando como falha.
- **Testes**: `tests/test_tesseract_serie3_regressions.py` — a região 6×6 é opcional; uma região do tamanho de uma linha de texto continua falhando quando vazia.
- **Critério**: V8 `extraction_status=success` e `benchmark_status=valid` na 003, com `cer_sequential_normalized` do V8 inalterado (0.006).
- **Status**: CORRIGIDO NO CÓDIGO — aguardando a 003.

### T3-P04 | INFO | P3 | V4: métricas em ~207 s — HIPÓTESE REFUTADA

- **Dados**: 001: 206.4 s wall / 1850 s CPU; 002: 208.0 s. Demais corpora: 1.5–9.7 s.
- **Hipótese refutada**: o tempo não caiu com a emissão das tabelas (T3-P00), embora `duplicate_excess_rate` tenha caído de 0.81 para 0.70.
- **Observação**: o V4 continua com `duplicate_excess_rate` 0.70 e 83 páginas com CER > 0.15. O custo provavelmente está no alinhamento por blocos de um documento grande (224 páginas) com muitos blocos sem par. É desempenho do **avaliador**, não qualidade da extração.
- **Próximo passo**: perfilar `compute_metrics.py` no V4 (`python -m cProfile`) sem alterar o algoritmo. Não bloqueia a validação.

### T3-P05 | MÉDIO | P3 | Títulos nativos com espaçamento entre letras — DOCUMENTADO

- V7 p1 (CER 0.75 na 001: "R E LATÓ R I O D E M O N ITO RAM E NTO"), V8 p46/p47 (`tracked_heading`, 0.21). O texto é **nativo** (`ocr_outcome=not_requested`), então está fora do pipeline Tesseract. A correção exige mexer na montagem de linhas compartilhada e ainda não tem autorização.

### T3-P06 | ALTO | P2 | Headings — PENDENTE

- Valores na 002: `heading_level_match_rate`=0.000 em V3 e V4; `heading_recall` V4=0.335, V9=0.351; `heading_text_cer` V4=0.631, V9=0.667. Os números persistiram após T3-P00, portanto não se tratava de contaminação. Investigar a heurística de nível em V3/V4 (gabaritos `reference` × `manifesto`). Para o V9, reavaliar após T3-P01, porque os títulos rasterizados agora passam a ter texto.

### T3-P07 | MÉDIO | P2 | V5: tabelas sem borda e ordem embaralhada — PENDENTE

- 001: p10/p37 (`native_table_borderless`, CER 0.61), p6/p28 (`scrambled_internal_order`, 0.38–0.40), páginas híbridas p17/p20/p26/p32 (~0.4). Na 002, o CER do V5 caiu de 0.218 para 0.109. Reavaliar por página na 003.

### T3-P08 | MÉDIO | P2 | V9: dados críticos — PARCIAL

- 001 → 002: `numeric` 0.743 → 0.852, `currency` 0.831 → 0.914, `dates` 0.784 → 0.859. Espera-se nova melhora com T3-P01.

### T3-P09 | INFO | Risco de sobreajuste em `ocr/tesseract/postprocess.py`

- O módulo tem regras ligadas a marcadores do corpus ("OMITIR NO GABARITO", cabeçalhos "OCR PURO * SCAN"). Documentado; nenhuma regra desse tipo foi introduzida nesta série.

### T3-P10 | ALTO | P2 | OCR de páginas multicoluna intercala colunas — NOVO

- Exposto por T3-P01. Nas páginas do V9 com 2–3 colunas rasterizadas (p23, p28, p31, p50), o texto OCR recuperado intercala as colunas. Exemplo da p50: "Na reunião de acompanhamento, Marina Duarte A Unidade de Fiscalização consolidou os resultados destacou a necessidade…".
- **Causa provável**: a reconstrução de linhas OCR (`reconstruct_ocr_lines`/`reconstruct_ocr_layout`) agrupa palavras na mesma altura de colunas diferentes. Componente compartilhado — requer autorização.
- **Impacto**: mesmo assim, recuperar o texto aumenta a cobertura. A ordem afeta CER, WER e `pairwise_reading_order_accuracy`.
- **Critério**: `pairwise_reading_order_accuracy` do V9 ≥ 0.999 com o texto recuperado, e o CER dessas páginas < 0.30.

### T3-P11 | ALTO | P1 | V10 não era avaliável pelo executor — CORRIGIDO NO EXECUTOR

- **Sintoma**: a pré-verificação não encontrava um gabarito paginado válido para o V10.
- **Causa raiz (CONFIRMADA)**:
  1. `Document_AI_V10_REFERENCIA.md` delimita as páginas com `<!-- PAGE 001 START -->`/`<!-- PAGE 001 END -->`. O parser (`split_pages`) só reconhece `## Página NNN`, então o arquivo é lido como **1 página** e descartado como "não paginado".
  2. `Document_AI_V10_MANIFESTO.json` traz `expected_text`, mas não `expected_markdown`. `manifest_pages()` recusa: "Page 1 requires string expected_markdown; provide --reference for a metadata-only manifesto".
  3. `Document_AI_V10_REFERENCIA_PAGINADA.md` é válido (84/84 páginas, `## Página NNN`) e tem conteúdo idêntico ao `_REFERENCIA.md`, mas o executor não o procurava.
- **Correção (sem alterar `corpus/`)**: o executor inclui o V10 em `CORPORA` e, na pré-verificação, considera `<base>_REFERENCIA_PAGINADA.md` como gabarito de **último recurso** (prioridade abaixo de `_REFERENCIA.md` e do manifesto). Assim a escolha de gabarito de V3–V9 continua a mesma da 001 (confirmado em `--check-only`). O manifesto continua sendo passado como metadados.
- **Verificação**: autoavaliação no scratchpad (gabarito paginado × ele mesmo, com `--manifesto`): `status=ok`, `rank_eligible=True`, 28 métricas aplicáveis, todas perfeitas (CER 0, cobertura 1.0).
- **Limitação do gabarito V10 (não é defeito do executor)**: o gabarito é texto corrido, sem títulos de conteúdo (só o título do documento e os marcadores de página), sem tabelas (`TABLES.json` vazio) e sem listas. Por isso headings, tabelas e listas ficam `n/a` (41 métricas indisponíveis). As quebras de linha seguem as linhas visuais, que em Markdown viram quebras suaves dentro do mesmo parágrafo. Se você quiser avaliar estrutura no V10, o gerador precisa emitir títulos/listas/tabelas em Markdown e preencher `expected_markdown` no manifesto.
- **Testes**: `tests/test_tesseract_batch_runner_config.py` — uso do fallback quando os gabaritos primários são inutilizáveis, preferência pelo `_REFERENCIA.md` primário e presença do V10 em `CORPORA`.
- **Critério**: a 003 conclui o V10 com `metrics_status=ok` e `rank_eligible=True`.

### T3-P12 | MÉDIO | P3 | Tempo de extração 3–7× maior com a config canônica — NOVO

- 001 → 002: V3 84 → 379 s, V4 606 → 2491 s (41 min), V6 94 → 658 s, V8 99 → 410 s, V9 170 → 559 s. É o custo da detecção e do refinamento de tabelas (visual + OCR de tabela) que a 001 não executava. Não é regressão de qualidade, mas afeta a duração da rodada: a 003, com o V10 de 84 páginas, deve levar cerca de 1h30. Investigar onde está o tempo (`timings_ms` por página) antes de qualquer otimização.

### T3-P13 | BAIXO | P3 | Regressões pontuais 001 → 002 — MONITORAR

- Δ ≤ −0.005: V3 `percent` 0.956 → 0.944; V4 `currency` 0.890 → 0.884; V5 `text_coverage_recall` 0.928 → 0.921, `paragraph_boundary_recall` 0.821 → 0.804; V6 `paragraph_detection_recall` 0.976 → 0.970; V9 `heading_recall` 0.364 → 0.351, `paragraph_boundary_recall` 0.667 → 0.623, `list_item_detection_precision` 1.000 → 0.765, `list_group_detection_precision` 0.900 → 0.750.
- **Hipótese**: a mudança de representação (texto passa a ser célula de tabela e cabeçalhos/rodapés são suprimidos) desloca a contagem. Verificar na 003 se alguma delas se agrava.

### T3-P14 | INFO | — | Falhas pré-existentes na suíte de testes

- Estas 13 falhas também ocorrem no `HEAD` (`5cc81d6`) sem as alterações desta série; foram verificadas numa worktree temporária, já removida:
  - `test_line_detector.py` (8 testes `test_reconciliation_*`);
  - `test_rapidocr_improvement_005/006/008.py` (3);
  - `test_no_timeouts.py::test_production_code_has_no_timeout_keyword_argument`;
  - `test_evaluation_exact_optimizations.py::test_full_ground_truth_self_control_is_complete_and_perfect[V3]`.
- Não foram investigadas nesta série.

---

## §3 Alterações de código desta série (não commitadas)

| Item | Arquivo | Mudança |
|---|---|---|
| T3-P00 | `scripts/run_tesseract_batch_wsl.py` | `build_config()` canônica; `extractor_flags` no run manifest |
| T3-P11 | `scripts/run_tesseract_batch_wsl.py` | V10 em `CORPORA`; fallback `_REFERENCIA_PAGINADA.md` com prioridade −1 |
| T3-P01 | `src/structured_pdf_text/api.py` | `_append_hybrid_ocr_regions(page_raster_primary=…)`, `_PAGE_SIZED_FIGURE_COVERAGE=0.75`, atribuição de OCR de página a figuras `OCR_REGION`/`MERGE_OCR` |
| T3-P03 | `src/structured_pdf_text/api.py` | `_MIN_TEXT_REGION_SIDE_PT=8.0` em `_recover_selected_regions` |
| testes | `tests/test_tesseract_batch_runner_config.py` (5), `tests/test_tesseract_serie3_regressions.py` (8) | novos |

Não alterados: `corpus/`, gabaritos, manifestos, avaliador (`src/structured_pdf_text/evaluation/`), outros backends, `output/tesseract/001/` e `output/tesseract/002/`.

## §4 Testes executados (2026-11-11)

| Execução | Resultado |
|---|---|
| `pytest tests/test_tesseract_batch_runner_config.py` | 5 passed |
| `pytest tests/test_tesseract_serie3_regressions.py` | 8 passed |
| `pytest tests` (suíte completa, com todas as alterações) | 3109 passed, 13 failed, 4 skipped — as 13 falhas são pré-existentes (T3-P14) |
| `run_tesseract_batch_wsl.py 003 --check-only` | aprovada; V3–V9 com os mesmos gabaritos da 001; V10 `reference_paginada` 84/84 |
| Rodada completa 002 | executada (somente T3-P00) |
| Rodada completa 003 | **não executada** — será feita manualmente |

## §5 Validação esperada na rodada 003

Comparar 003 × 001 (baseline imutável) e 003 × 002 (efeito isolado de T3-P01/T3-P03). O V10 não tem rodada anterior, então a 003 vira sua baseline.

| Verificação | Critério |
|---|---|
| T3-P01 | V9 `text_coverage_recall` ≥ 0.80; V9 `cer_sequential_normalized` < 0.392 |
| Controle T3-P01 | V3–V8: Δ `text_coverage_precision`, `cer_sequential_normalized`, `duplicate_excess_rate` dentro de ±0.005 da 002 |
| T3-P03 | V8 `success`/`valid`; V8 `cer_sequential_normalized` ≈ 0.006 |
| T3-P11 | V10 `metrics_status=ok`, `rank_eligible=True` |
| Geral | nenhuma extração com `extract_error`/`metrics_error`; não declarar sucesso geral se houver regressão relevante |

Comando: `python3 -u scripts/run_tesseract_batch_wsl.py 003` (pré-verificação: `--check-only`). O diretório `output/tesseract/003` não pode existir antes da execução.

## §6 Diagnóstico da rodada 001 — notas por corpus

- **V3**: tabelas ausentes (T3-P00); p96–p99 rotacionadas (T3-P02); p116 "OCR tabelas" com CER 1.15; níveis de heading 0 (T3-P06).
- **V4**: tabelas ausentes; 83/224 páginas com CER > 0.15, concentradas em "OCR de tabelas" (p145–p182, CER 1.1–2.7); `heading_recall` 0.335; métricas lentas (T3-P04).
- **V5**: tabelas ausentes; páginas sem borda/embaralhadas/híbridas (T3-P07).
- **V6**: só T3-P00; páginas `rotation` p31/p47/p75 com CER 0.12–0.13.
- **V7**: T3-P00 (rodapés e tabelas); p1 com título espaçado (T3-P05); p25/p29/p73 `grid_borderless` com 0.20–0.23.
- **V8**: T3-P00; `partial` por T3-P03; p46/p47 `tracked_heading` (T3-P05).
- **V9**: T3-P01 (47+5 páginas); T3-P00; títulos e dados críticos como consequência (T3-P06/T3-P08).

## §7 Histórico resumido da Série 2 (2026-11-05 a 2026-11-06, executor `run_loop.py`)

| Correção | Arquivo | Decisão |
|---|---|---|
| Fix-F-V9-01 — guarda de altura (> 85%) em `_detect_rectangular_grid` | `tables/detector.py` | aceita (V9 CER 0.4849 → 0.4318) |
| Fix-F-V9-02 — `_is_form_label_colon_pattern` | `tables/text_tracks.py` | aceita (V9 CER → 0.4285) |
| Fix-F-V9-03 — agrupamento em `ocr_tracks.py` | `tables/ocr_tracks.py` | revertida (sem efeito) |
| Fix-F-V9-04 — `_group_short_regions` | `tables/detector.py` | inconclusiva, código preservado |

A hipótese "Tipo A" da Série 2 (V9: Tesseract não reconhece imagens de baixa DPI) foi **refutada** na Série 3 (T3-P01). Notas técnicas mantidas: o avaliador aceita a chave `page_number` do manifesto V9; o limiar de memória do V4 é 11 GB.
