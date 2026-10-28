# Problemas da Baseline Tesseract — Registro Técnico Vivo

> **Protocolo de atualização**: este documento deve ser atualizado obrigatoriamente após cada run, mesmo quando a run falhar ou não produzir métricas elegíveis. As seções §0, §Resolvidos, §Regressões e §Histórico devem refletir sempre o estado mais recente.

---

## §0 Estado Atual

| Campo | Valor |
|---|---|
| **Última atualização** | 2026-10-28 |
| **Branch** | `feat/tesseract-optimization` |
| **Commit base** | `92cd6c5` + Fix-A1/A2b/A3 + Fix-E + Fix-F + Fix-G + Fix-I + Fix-J + Fix-K + Fix-L + Fix-M + Fix-N + Fix-O + Fix-P |
| **Última run avaliada** | **0035** (Fix-S REVERTIDA — best_known permanece run 0032) |
| **Run em andamento** | nenhuma |
| **Melhor série 2** | **RUN 0032** — HEAD+A1+A2b+A3+Fix-E+Fix-F+Fix-G+Fix-I+Fix-J+Fix-K+Fix-L+Fix-M+Fix-N+Fix-O+Fix-P |
| **Esquema do avaliador** | `evaluation-3.0.0` |
| **Engine** | tesseract, balanced/adaptive, pt-BR |
| **Fase atual** | `analyzing` — P5+P7 ENCERRADOS. Próximo: identificar novo problema fixável |
| **Cron** | `32fde507` — ativo, 5 min, renovado 2026-10-27, expira ~2026-11-03 |
| **provisional_corrections_count** | 15 |

### Métricas atuais — melhor resultado série 2 (run 0032, 2026-10-27)

| Métrica | V3 (144p) | V5 (42p) | V6 (95p) | V7 (80p) | V8 (90p) |
|---|---|---|---|---|---|
| `cer_markdown_raw` | **0.08270** | 0.19619 | **0.04405** | 0.01364 | **0.01887** |
| `cer_sequential_normalized` | 0.04212 | 0.1461 | **0.0202** | 0.0011 | — |
| `wer_sequential` | **0.05406** | 0.1554 | **0.0281** | 0.0012 | — |
| `heading_f1` | 0.867 | **0.838** | **0.973** | **0.989** | **0.991** |
| `heading_precision` | 0.929 | 1.000 | **0.996** | 1.000 | 0.989 |
| `heading_recall` | 0.813 | **0.721** | **0.950** | 0.978 | **0.993** |
| `heading_level_match_rate` | 0.0¹ | 1.0 | 1.0 | 1.0 | 1.0 |
| `heading_text_cer` | 0.145 | 0.368 | **0.079** | 0.026 | — |
| `text_coverage_recall` | 0.9597 | 0.893 | **0.989** | 1.000 | 0.9937 |
| `text_coverage_precision` | **0.9775** | 0.995 | **0.993** | 0.999 | — |
| `rank_eligible` | ✅ | ✅ | ✅ | ✅ | ✅ |

¹ V3 heading_level_match_rate=0.0 ignorado por instrução do usuário.

### Delta run 0022 vs run 0021 (Fix-F + Fix-G)

| Métrica | V3 Δ | V5 Δ | V6 Δ | V7 Δ |
|---|---|---|---|---|
| `cer_markdown_raw` | **−0.0911 (−42.9%)** | 0.000 | **−0.0019 (−4.0%)** | −0.0002 |
| `cer_sequential_normalized` | **−0.1126 (−67.0%)** | 0.000 | **−0.0019 (−8.5%)** | 0.000 |
| `wer_sequential` | **−0.2338 (−76.8%)** | 0.000 | **−0.0025 (−8.2%)** | 0.000 |
| `duplicate_excess_rate` | −0.0048 | 0.000 | **−0.0027** | 0.000 |
| `excess_blocks` | **−3** (66→63) | — | **−3** (90→87) | — |
| `text_coverage_precision` | **+0.181 (+18.1pp)** | 0.000 | **+0.002** | 0.000 |
| `text_coverage_recall` | −0.003 (negligível) | 0.000 | −0.000 | 0.000 |
| **Regressões** | nenhuma | nenhuma | nenhuma | nenhuma |

**Fix-F aplicado** (run 0022): regex `_IRINF_DOSSI_RE` corrigida (`**` tornado opcional) + nova `_VOLUME_SUBTITLE_RE`. V6 dup_excess −3 blocos (esperado −6 — VOLUME parcialmente suprimido, investigar p31/47/75).

**Fix-G aplicado** (run 0022): `_suppress_tesseract_coordinate_dump()`. V3 CER/WER caiu enormemente (−42.9%/−76.8%) — páginas 96/97 pararam de gerar garbage de bounding-box no output. V3 txt_precision +18.1pp.

### Análise run 0023 (2026-10-27) — Fix-H + Fix-I

Run 0023: dois fixes experimentados simultaneamente. Resultado divergente.

**Fix-H (TESSERACT_OSD=1 em run_loop.py): REVERTIDA — regressão grave em V3**

| Página V3 | CER 0022 | CER 0023 | Δ |
|-----------|----------|----------|---|
| 96 | 0.896 | **3.287** | **+2.391 ↑↑** |
| 97 | 0.871 | **1.480** | **+0.609 ↑** |
| 98 | 3.891 | 3.891 | 0.000 |
| 99 | 2.498 | 2.423 | −0.075 |
| 116 | 2.678 | 2.536 | −0.142 |

O OSD detectou orientação incorreta nas páginas 96 e 97, rotacionando-as na direção errada e piorando gravemente o CER. V3 global: cer_raw 0.1214→0.1341 (+10.5%), dup_excess 0.1165→0.1325 (+0.016). Fix-H revertida de `scripts/run_loop.py`.

**Fix-I (_promote_allcaps_section_headers em postprocess.py): MELHORADO — mantida**

| Métrica | V6 0022 | V6 0023 | Δ |
|---------|---------|---------|---|
| `heading_f1` | 0.9155 | **0.9221** | **+0.0066** |
| `heading_recall` | 0.8473 | **0.8588** | **+0.0115** |
| `heading_precision` | 0.9955 | 0.9956 | +0.0001 |
| `heading_text_cer` | 0.1322 | **0.1233** | **−0.0089** |
| `cer_markdown_raw` | 0.0454 | 0.0454 | ≈0 |
| `dup_excess` | 0.0852 | 0.0852 | 0 |

Fix-I promoveu blocos all-caps TEXT→TITLE em scan_fullpage V6. V3/V5/V7 sem impacto. Fix-I mantida no código. Run 0024 valida Fix-I isolada (sem Fix-H).

### Análise run 0025 (2026-10-27) — Fix-J

**Fix-J: Rule A de `_promote_allcaps_section_headers()` estendida para blocos CAPTION**

Causa raiz identificada na sessão anterior: nas páginas 3/33/89 do V6 (família scan_fullpage), o bloco "QUADRO DE MEDIÇÕES" antes de uma TABLE era classificado como `ContentKind.CAPTION` pelo layout engine do Tesseract, não como `ContentKind.TEXT`. O guard original `block.kind not in (ContentKind.TEXT,)` pulava esses blocos.

Fix-J alterou o guard para `block.kind not in (ContentKind.TEXT, ContentKind.CAPTION)`, permitindo que Rule A promova CAPTION antes de TABLE para H3. Rule B permanece TEXT-only (primeiro bloco all-caps da página → H1 só faz sentido para TEXT, não para CAPTION que é label de figura/tabela).

**Resultado run 0025:**

| Métrica | V6 0024 | V6 0025 | Δ |
|---------|---------|---------|---|
| `heading_f1` | 0.9221 | **0.9287** | **+0.0066** |
| `heading_recall` | 0.8588 | **0.8702** | **+0.0114** |
| `heading_precision` | 0.9956 | 0.9956 | 0.000 |
| `heading_text_cer` | 0.1233 | **0.1182** | **−0.0051** |
| `cer_markdown_raw` | 0.045383 | 0.045256 | −0.000127 |
| `dup_excess` | 0.0852 | 0.0852 | 0.000 |

V3, V5, V7: **idênticos** ao run 0024 (delta=0.000 em todos os campos). Sem regressões.

**Decisão: A.ACEITAR_PROVISORIAMENTE** — Fix-J melhora V6 h_rec de 85.88% para 87.02%, sem regredir nenhum corpus. counter 8→9. **best_known atualizado para run 0025**.

### Análise run 0026 (2026-10-27) — Fix-K

**Fix-K: `_promote_short_bold_column_headers()` — bold column headers em layouts multi-coluna**

Após análise dos 34 FNs remanescentes em V6 (run 0025), a família `two_columns` (6 FNs) e `three_columns` (9 FNs) apresentavam blocos curtos em negrito que eram cabeçalhos de coluna (`**Engenharia**`, `**Suprimentos**`, `**Comunicações**` etc.) mas não eram promovidos pois não eram all-caps nem numerados.

Fix-K adiciona `_promote_short_bold_column_headers()`: em páginas com ≥ 2 blocos TEXT bold curtos (3–50 chars, sem newline, sem `:` ou `.` final, não all-caps, não numerados) + TITLE já existente na página → promove todos para H3. O threshold ≥ 2 evita falsos positivos de assinaturas (1 bloco bold isolado).

**Resultado run 0026:**

| Métrica | V6 0025 | V6 0026 | Δ |
|---------|---------|---------|---|
| `heading_f1` | 0.9287 | **0.9605** | **+3.18pp** |
| `heading_recall` | 0.8702 | **0.9275** | **+5.73pp** |
| `heading_precision` | 0.9956 | 0.9960 | +0.04pp |
| `heading_text_cer` | 0.1182 | **0.1040** | **−1.42pp** |
| `cer_markdown_raw` | 0.045256 | 0.044302 | −0.000954 |
| `dup_excess` | 0.0852 | 0.0852 | 0.000 |
| TPs recuperados | 228 | **243** | **+15** |

V7 bônus (famílias similares):

| Métrica | V7 0025 | V7 0026 | Δ |
|---------|---------|---------|---|
| `heading_f1` | 0.9722 | **0.9891** | **+1.69pp** |
| `heading_recall` | 0.9459 | **0.9784** | **+3.25pp** |
| `heading_text_cer` | 0.0490 | **0.0258** | **−2.32pp** |
| TPs recuperados | 175 | **181** | **+6** |

V3, V5: **idênticos** ao run 0025 (delta=0.000 em todos os campos). Sem regressões.

**Decisão: A.ACEITAR_PROVISORIAMENTE** — Fix-K melhora V6 h_f1 de 92.87% para 96.05% (+3.18pp) e V7 de 97.22% para 98.91% (+1.69pp), sem regredir nenhum corpus. counter 9→10. **best_known atualizado para run 0026**.

FNs remanescentes V6 após Fix-K (19 de 34 recuperados = 15 TPs novos):
- `watermark_stamp`: 6 (páginas 26/61/77 — bold mixed-case H1 + `### Parecer sucinto de encaminhamento`)
- `redacted`: 3
- `rotation`: 3
- `borderless_table`: 3
- `hybrid_cell_stamp`: 3
- `frontmatter`: 1

### Análise run 0027 (2026-10-27) — Fix-L v1 (sem efeito)

**Fix-L v1: `_promote_bold_pipe_title()` — bold TEXT block com ` | ` separator → H1**

Run 0027 = run 0026 em todos os campos. ZERO diffs. Fix-L v1 não teve efeito.

**Causa raiz identificada por debug**: com a config BALANCED (`best_extraction_config(language='pt-BR')`), o Tesseract classifica o bloco "Nota de análise com chancela | Bacia Norte" como `ContentKind.CAPTION`, não `ContentKind.TEXT`. Fix-L v1 verificava apenas `block.kind != ContentKind.TEXT`, pulando blocos CAPTION. Debug confirmou:

```
active[0]: kind=ContentKind.CAPTION hl=None bold=True pipe=True text='Nota de análise com chancela | Bacia Norte'
```

Fix-L v2 implementada com guard `block.kind not in (ContentKind.TEXT, ContentKind.CAPTION)` — mesmo padrão de Fix-J. Counter não incrementado (Fix-L v1 não alterou comportamento).

### Análise run 0028 (2026-10-27) — Fix-L v2

**Fix-L v2: `_promote_bold_pipe_title()` com suporte a CAPTION**

Fix-L v2 estendeu o guard para `(ContentKind.TEXT, ContentKind.CAPTION)`. Debug prévio confirmou 6 páginas V6 que seriam promovidas: `watermark_stamp` (p26/61/77: "Nota de análise com chancela | Bacia Norte") + `borderless_table` (p9/39/71: "Quadro comparativo de preços | X"). Gabarito confirma que todos são H1.

**Resultado run 0028:**

| Métrica | V6 0026 | V6 0028 | Δ |
|---------|---------|---------|---|
| `heading_f1` | 0.9605 | **0.9727** | **+1.22pp** |
| `heading_recall` | 0.9275 | **0.9504** | **+2.29pp** |
| `heading_precision` | 0.9960 | 0.9960 | 0.000 |
| `heading_text_cer` | 0.1040 | **0.0787** | **−2.53pp (−24.3%)** |
| `cer_markdown_raw` | 0.044302 | **0.044048** | −0.000254 |
| TPs | 243 | **249** | **+6** |
| FPs | 1 | 1 | 0 |
| FNs | 19 | **13** | **−6** |

V3, V5, V7: **idênticos** ao run 0026 (Fix-L não afeta esses corpora). Sem regressões.

**Decisão: A.ACEITAR_PROVISORIAMENTE** — Fix-L v2 recupera +6 TPs em V6 (h_rec 0.9275→0.9504, meta de 0.95 atingida), sem regredir nenhum corpus. h_text_cer também melhorou −24.3%. counter 10→11. **best_known atualizado para run 0028**.

FNs remanescentes V6 após Fix-L v2 (13 de 262 headings de referência):
- `rotation`: 3 (páginas rotacionadas — OCR garbled)
- `redacted`: 3 (conteúdo redigido/scrambled)
- `hybrid_cell_stamp`: 3
- `watermark_stamp` H3 plain text: 3 (páginas 26/61/77 — "Parecer sucinto de encaminhamento", "Histórico de revisões", "Partes interessadas" — sem bold/allcaps/número)
- `frontmatter`: 1

### Análise run 0029 (2026-10-27) — Fix-M

**Fix-M: `_promote_seção_subseção_headings()` — padrões de seção em PT-BR**

Fix-M identificou que TEXT blocks iniciando com "Seção N" ou "Subseção N.M" são tipicamente cabeçalhos de seção em documentos brasileiros. Em V5 página 2, o bloco "Seção 2 - Análise" estava presente como TEXT simples (não detectado como heading pelo engine nativo). O bloco "Subseção 2.1 - Órgão responsável" estava fundido com corpo de texto (bloco multilinha), impedindo promoção pelo guard de linha única.

Regra generalista (não corpus-específica): verifica `^se[çc][aã]o\s+\d+` → H2 e `^subse[çc][aã]o\s+\d+` → H3. Guards: bloco de linha única, 4-120 chars, não já TITLE, não suprimido.

Verificação zero FP: nenhuma ocorrência dos padrões em V3, V6, V7 como texto simples.

**Resultado run 0029:**

| Métrica | V5 0028 | V5 0029 | Δ |
|---------|---------|---------|---|
| `heading_f1` | 0.8276 | **0.8376** | **+1.00pp** |
| `heading_recall` | 0.7059 | **0.7206** | **+1.47pp** |
| `heading_precision` | 1.0000 | 1.0000 | 0.000 |
| `heading_level_match_rate` | 1.0 | 1.0 | 0.000 |
| TPs | 48 | **49** | **+1** |
| FNs | 20 | **19** | **−1** |

V3, V6, V7: **idênticos** ao run 0028. Sem regressões.

**Decisão: A.ACEITAR_PROVISORIAMENTE** — Fix-M adiciona +1 TP ao V5 sem introduzir FPs, sem regredir nenhum corpus. h_level_match_rate=1.0 confirma nível H2 correto para "Seção 2 - Análise". counter 11→12. **best_known atualizado para run 0029**.

**P3 tentativa 5/5 — conclusão final:**
- **V6 heading recall**: RESOLVIDO ✅ (h_rec=0.9504 ≥ 0.95, após Fix-I+J+K+L em 4 tentativas)
- **V5 heading recall**: ADIADO após 5 tentativas (h_rec=0.7206, 19 FNs remanescentes) — maioria são headings ausentes da extração (texto não capturado como bloco separado), não resolvível com postprocessors de promoção de bloco

FNs remanescentes V5 (19 de 68): majoritariamente H1 completamente ausentes da extração (p4, p5, p6, p18, p20, p23, p28, p32, p36, p38 etc.) + 1 "Subseção" fundida com corpo + 2 "Título de nível N" (corpus-específicos) + outros.

### Análise run 0030 (2026-10-27) — Fix-N

**Fix-N: `_omitir_gabarito_split()` — detecção de marcador "OMITIR NO GABARITO" dividido entre linhas de tabela**

**Causa raiz do problema (P5 tentativa 1/5)**: a página 98 do V3 é uma varredura com rotação física de 270°. O Tesseract OCRiza a página na orientação errada e produz uma grande TABLE onde as células contêm o texto do rodapé decorativo ("RODAPÉ DECORATIVO — OMITIR NO GABARITO") fragmentado em múltiplas linhas. O marcador "OMITIR NO" aparece em uma linha e "GABARITO" na linha seguinte — separados pelo conteúdo da coluna do meio — portanto o regex existente `\bomitir\s+n[ao]\s+gabarito\b` nunca disparava.

Fix-N adicionou `_omitir_gabarito_split(text)`: janela deslizante de 4 linhas em `text.split("\n")`. Se "OMITIR NO" aparece em uma linha e "GABARITO" aparece dentro das 3 linhas seguintes, o bloco é suprimido. Solução generalista — detecta um marcador semântico legítimo, não texto corpus-específico.

**Impacto por página (V3):**

| Página | Problema | CER 0029 | CER 0030 | Δ |
|--------|----------|----------|----------|---|
| p098 | Rotação 270° — TABLE garbled suprimida | 3.891 | **0.955** | **−2.936** |
| p099 | Rotação 90° tabela — sem marcador | 2.498 | 2.498 | 0.000 |
| p116 | Tabela financeira rotacionada — sem marcador | 2.678 | 2.678 | 0.000 |
| p096 | Rotação 90° — coord dump já suprimido | 0.896 | 0.896 | 0.000 |
| p097 | Rotação 180° — coord dump já suprimido | 0.871 | 0.871 | 0.000 |

**Resultado run 0030:**

| Métrica | V3 0029 | V3 0030 | Δ |
|---------|---------|---------|---|
| `cer_markdown_raw` | 0.1214 | **0.1073** | **−0.0140 (−11.6%)** |
| `heading_f1` | 0.8667 | 0.8667 | 0.000 |
| `heading_precision` | 0.929 | 0.929 | 0.000 |
| `heading_recall` | 0.813 | 0.813 | 0.000 |
| `text_coverage_recall` | 0.963 | 0.963 | ≈0 |
| `text_coverage_precision` | 0.954 | 0.954 | ≈0 |

V5, V6, V7: **idênticos** ao run 0029. Sem regressões em nenhum corpus.

**Decisão: A.ACEITAR_PROVISORIAMENTE** — Fix-N reduz V3 cer_raw −11.6% suprimindo o TABLE garbled na página 98 (CER 3.891→0.955). Totalmente generalista (detecta marcador semântico existente). Zero regressões. counter 12→13. **best_known atualizado para run 0030**.

**Estado P5 após Fix-N:** páginas 99 e 116 permanecem com CER > 2.0 (sem marcador OMITIR). Próxima tentativa: detectar e suprimir tabelas com texto completamente invertido/girado (sem marcador semântico).

### Análise run 0031 (2026-10-27) — Fix-O + V8 integrado

**Fix-O: `_suppress_rotated_table_garble()` — supressão de TABLE blocks com tokens Q-sem-U**

**Causa raiz (P5 tentativa 2/5)**: nas páginas 99 e 116 do V3, a rotação ~90° produz TABLE blocks cujas células contêm tokens gerados pela leitura lateral de texto girado. O token "ORIQLVHOIVT" (p99, TABLE2) contém 'Q' não seguido de 'U' — evento fonologicamente impossível em português, espanhol, francês, italiano e todas as línguas latinas. O critério "Q não seguido de U em token ≥ 7 caracteres alfabéticos" é um sinal altamente específico de garble.

Fix-O adicionou `_table_has_q_without_u(text)` + `_suppress_rotated_table_garble()` em `src/structured_pdf_text/ocr/tesseract/postprocess.py`. Guards: (1) bloco do tipo TABLE, (2) não suprimido, (3) contém ≥ 1 token ≥ 7 letras com Q não seguido de U. Mínimo de 7 letras filtra abreviações legítimas (SQL, QR etc.).

12 testes novos adicionados (`tests/test_tesseract_postprocess.py`). Total: 158 (eram 146). Todos passam.

**Impacto por página (V3):**

| Página | Problema | CER 0030 | CER 0031 | Δ |
|--------|----------|----------|----------|---|
| p099 | Rotação 90° — TABLE2 com "ORIQLVHOIVT" suprimida | 2.498 | **1.055** | **−1.443 (−57.7%)** |
| p116 | Tabela financeira rotacionada — Q-not-U suprimida | 2.678 | **1.487** | **−1.191 (−44.5%)** |
| p098 | Já suprimida por Fix-N | 0.955 | 0.955 | 0.000 |
| p096 | Coord dump já suprimido por Fix-G | 0.896 | 0.896 | 0.000 |
| p097 | Coord dump já suprimido por Fix-G | 0.871 | 0.871 | 0.000 |

**Resultado run 0031 — comparação V3:**

| Métrica | V3 0030 | V3 0031 | Δ |
|---------|---------|---------|---|
| `cer_markdown_raw` | 0.1073 | **0.08897** | **−0.01833 (−17.1%)** |
| `cer_sequential_normalized` | — | 0.04236 | — |
| `wer_sequential` | 0.0705 | **0.05406** | **−0.01644 (−23.3%)** |
| `heading_f1` | 0.8667 | 0.8667 | 0.000 |
| `heading_precision` | 0.929 | 0.929 | 0.000 |
| `heading_recall` | 0.813 | 0.813 | 0.000 |
| `text_coverage_recall` | 0.963 | **0.9599** | −0.003 (−0.34pp) |
| `text_coverage_precision` | 0.954 | **0.9701** | **+0.016 (+1.6pp)** |

V5, V6, V7: **idênticos** ao run 0030. Sem regressões.

**V8 — baseline inaugural (run 0031):**

| Métrica | V8 valor |
|---------|----------|
| `cer_markdown_raw` | **0.01887** |
| `heading_detection_f1` | **0.9909** |
| `heading_precision` | 0.9891 |
| `heading_recall` | **0.9927** |
| `heading_level_match_rate` | 1.0 |
| `text_coverage_recall` | **0.9937** |
| `rank_eligible` | ✅ |

V8 (90p) apresenta excelente qualidade — CER muito baixo, heading recall quase perfeito. Corpus bem comportado, sem rotações ou artifacts detectados. Corpus integrado ao CORPUS_ORDER: `["V3", "V5", "V6", "V7", "V8"]`.

**Decisão: A.ACEITAR_PROVISORIAMENTE** — Fix-O reduz V3 cer_raw −17.1% e wer −23.3%, suprimindo TABLE garbled nas páginas 99 e 116 via heurística fonológica generalista (Q-sem-U). V5/V6/V7 inalterados. V8 baseline excelente estabelecida. counter 13→14. **best_known atualizado para run 0031**.

**Estado P5 após Fix-O (tentativa 2/5 MELHORADO):** p99 CER 2.498→1.055 (−57.7%), p116 CER 2.678→1.487 (−44.5%). Ainda acima do threshold de 0.05 por página. Remanescentes: TABLE1 nas páginas (sem token Q-sem-U — células "NVOS/bhap/ejusJasa") e múltiplos blocos TEXT garbled ("$Y $H $U", "sneJb 06", "ODUILNIS").

**Estado P5 após Fix-P (tentativa 3/5 MELHORADO):** `_suppress_dollar_letter_garble()` — TABLE e TEXT blocks com ≥2 ocorrências de `$[A-Z]` (dollar+maiúscula, impossível em PT). p99 CER 1.055→0.862 (−18.4%), p116 CER 1.487→0.861 (−42.1%). V3 cer_raw 0.08897→0.082696 (−7.1%). Remanescentes: "$Y $4 $4", "sneJb 06", "ODUILNIS", "ejogqea".

**Fix-Q (tentativa 4/5 REVERTIDA):** expandir `$[A-Z]` para `$[A-Z0-9]` piorou CER (0.082696→0.083714 +0.12%, p99 0.862→0.877, p116 0.861→0.872). "$Y $4 $4" tinha coincidência de chars com referência.

**Fix-R (tentativa 5/5 REVERTIDA):** estender Q-sem-U de TABLE para TABLE+TEXT piorou CER (V3 0.082696→0.083288 +0.71%, p116 0.861→0.933 +8.4%). "ejogqea..." tinha numerais coincidentes.

**P5 ENCERRADO** (5/5 tentativas: Fix-N+O+P aceitas, Fix-Q+R revertidas). Best: run 0032, p99 CER=0.862, p116 CER=0.861. Residual irreducível via postprocessors — fix definitivo requer detecção de rotação de imagem no pipeline OCR.

### Resumo da situação atual

**Série 2 progresso** (run 0031, 2026-10-27): 14 correções aprovadas provisoriamente.

**Problemas resolvidos** (fixes A1–A3, B, C, D, E, F, G, I, J, K, L, M, N, O):
- ✅ V3 header_leakage: 0.0 (A1/A2b/A3)
- ✅ V3 duplicate_excess: 0.3324 → 0.1165 (B: pagina_separator, C: currency, D: corpus_markers, G-parcial)
- ✅ V3 heading_f1: 0.208 → 0.867 (efeito colateral do Fix-B)
- ✅ V3 CER/WER: 0.2125/0.3043 → **0.08897/0.05406** (Fix-G coordinate dump −42.9% + Fix-N OMITIR split −11.6% + Fix-O Q-sem-U −17.1%)
- ✅ V3 txt_precision: 0.773 → 0.9701 (Fix-G + Fix-O)
- ✅ V7 heading_f1: 0.964 → **0.989** (Fix-A + Fix-K)
- ✅ V6 IRINF running headers: suprimidos (Fix-E)
- ✅ V6 CER/WER: 0.0474/0.0306 → 0.04405/0.0281 (Fix-F+Fix-K+Fix-L)
- ✅ V6 heading_f1: 0.915 → **0.973** (Fix-I+J+K+L; h_rec 0.847→0.950 ≥ 0.95 ✅)
- ✅ V5 heading_f1: 0.828 → **0.838** (Fix-M Seção/Subseção; h_prec=1.0 mantida)
- ✅ V8 baseline: cer=0.01887, h_f1=0.9909, h_rec=0.9927, h_lmr=1.0 (corpus integrado run 0031)

**Problemas ativos** (por prioridade — atualizado pós-run 0031):

#### P3 — V6 heading_f1 / V5 heading_f1 — **PARCIALMENTE RESOLVIDO / ADIADO**
- **V6 heading recall**: ✅ RESOLVIDO — h_rec=0.9504 ≥ 0.95 após 4 tentativas (Fix-I+J+K+L)
- **V5 heading recall**: ⏸ ADIADO após 5 tentativas — h_rec=0.7206, 19 FNs remanescentes
- **Tentativa 1** (Fix-I, runs 0023+0024): `_promote_allcaps_section_headers()`. V6 h_f1 0.9155→0.9221. **ACEITAR**.
- **Tentativa 2** (Fix-J, run 0025): Rule A estendida para CAPTION (QUADRO DE MEDIÇÕES). V6 h_f1 0.9221→0.9287. **ACEITAR**.
- **Tentativa 3** (Fix-K, run 0026): `_promote_short_bold_column_headers()`. V6 h_f1 0.9287→0.9605, V7 bônus. **ACEITAR**.
- **Tentativa 4** (Fix-L v2, run 0028): `_promote_bold_pipe_title()` com CAPTION. V6 h_f1 0.9605→0.9727, h_rec 0.9275→0.9504 ✅. **ACEITAR**.
- **Tentativa 5** (Fix-M, run 0029): `_promote_seção_subseção_headings()`. V5 +1 TP, h_f1 0.8276→0.8376, h_rec 0.7059→0.7206, h_prec=1.0. **ACEITAR**. P3 encerrado.
- **Tentativas**: 5/5 — **COMPLETO**
- **FNs V5 remanescentes** (19 de 68): maioria H1 ausentes da extração (blocos não capturados). Não fixáveis com postprocessors. Marcado `deferred_after_5_attempts`.
- **FNs V6 remanescentes** (13 de 262): rotation=3, redacted=3, hybrid_cell_stamp=3, watermark_stamp H3=3, frontmatter=1.

#### P5 — V3 páginas rotacionadas (96-99, 111, 115-116, 118 + mais) — CER elevado — **ENCERRADO** (5/5) MELHORADO
- **Problema**: 13 páginas com CER > 0.30 antes de Fix-N. Páginas fisicamente rotacionadas ~90°/180°/270° geram output garbled com CER >> 1.0.
- **Fix-H (OSD) tentada (run 0023): REVERTIDA** — causou regressão em p96 (0.896→3.287) e p97 (0.871→1.480). NÃO conta como tentativa P5.
- **Tentativa 1 (Fix-N, run 0030): ACEITAR** — `_omitir_gabarito_split()` suprime TABLE garbled na p98. CER p98: 3.891→0.955 (−75%). V3 cer_raw: 0.1214→0.1073 (−11.6%).
- **Tentativa 2 (Fix-O, run 0031): ACEITAR** — `_suppress_rotated_table_garble()` suprime TABLE blocks com token Q-sem-U (≥7 letras). p99: CER 2.498→1.055 (−57.7%), p116: CER 2.678→1.487 (−44.5%). V3 cer_raw: 0.1073→0.08897 (−17.1%).
- **Tentativa 3 (Fix-P, run 0032): ACEITAR** — `_suppress_dollar_letter_garble()` suprime TABLE+TEXT blocks com ≥2 ocorrências de `$[A-Z]`. p99: CER 1.055→0.862 (−18.4%), p116: CER 1.487→0.861 (−42.1%). V3 cer_raw: 0.08897→0.082696 (−7.1%).
- **Tentativa 4 (Fix-Q, run 0033): REVERTIDA** — expandir `$[A-Z]`→`$[A-Z0-9]`. CER piorou (artefato: "$Y $4 $4" tinha coincidência métrica com referência). V3 cer_raw: 0.082696→0.083714 (+0.12%).
- **Tentativa 5 (Fix-R, run 0034): REVERTIDA** — estender Q-sem-U de TABLE para TABLE+TEXT. p116 CER piorou 0.861→0.933 (+8.4%). V3 cer_raw: 0.082696→0.083288 (+0.71%).
- **ENCERRADO** — best: run 0032, p99 CER=0.862, p116 CER=0.861. Residual irreducível via postprocessors. Fix definitivo: detecção de rotação de imagem no pipeline OCR.
- **Métricas finais** (run 0032): V3 cer_raw=0.082696. V3 cer_raw baseline era 0.3888 — melhora total P5+outros: −78.7%.
- **Tentativas**: 2/5
- **Nota**: `block.confidence` não é setado para blocos TEXT/CAPTION pelo pipeline Tesseract — apenas para TABLE blocks (via `_table_plain_text`). OSD descartado (Fix-H regressão p96/97).

#### P4 — V3 CER/WER residual (0.1073 / 0.0705) — relacionado a P5
- Melhora enorme após Fix-G. Fix-N reduziu mais 11.6%. Restam páginas rotacionadas sem marcador nem coordinate dump (ver P5).
- **Estado**: fix parcial continuando. Melhora adicional depende de P5 (tentativas 2-5).

#### P6 — V5 CER elevado (0.1963) — NÃO FIXÁVEL via postprocessor
- p3/p30 [native_lists]: CER=0.739/0.672 — causa raiz: problema de reading order em extração nativa (item 3 aparece no topo, itens 1 e 2 ausentes). NÃO é problema de OCR Tesseract — é extração nativa. Fora do escopo das correções autorizadas.
- p5/p36 [reading_order]: CER=0.487/0.478 — leitura de colunas em ordem incorreta (extração nativa). Mesma limitação.
- **Estado**: investigado pós-run 0029. Não fixável com postprocessors Tesseract. Marcado como `blocked_native_path`.

#### P1 — V3 excess content (deprecated metric: duplicate_excess_rate)
- **Estado**: `duplicate_excess_rate` não mais disponível no evaluation-3.0.0 schema. P1 absorvido por P5 (excess vem de páginas rotacionadas garbled). Problema reformulado como CER de páginas garbled (P5).

#### P2 — V6 excess content (deprecated metric: duplicate_excess_rate)
- **Estado**: `duplicate_excess_rate` não mais disponível no evaluation-3.0.0 schema. V6 cer_raw=0.0440 já baixo. Reavaliação necessária com métricas disponíveis (text_coverage).

---

### Histórico de correções série 2

| Run | Fix | Descrição | Impacto principal | Estado |
|---|---|---|---|---|
| 0013 | — | Baseline série 2 | — | — |
| 0015 | _promote_numbered_bold_headings | Numbered bold headings → TITLE | V7 heading_f1 +0.86pp | ✅ aceita |
| 0016 | _suppress_pagina_separator_blocks | Suprime "Página NNN" separadores | V3 dup_excess −50%, cer −43% | ✅ aceita |
| 0017 | _normalize_brl_currency_spacing | Corrige "R$9" → "R$ 9" | V3 dup_excess −16.6% | ✅ aceita |
| 0018 | _suppress_ocr_corpus_markers (1) | Suprime OCR puro/tabelas/híbrido | V3 dup_excess −4.6% | ✅ aceita |
| 0019 | _suppress_ocr_corpus_markers (2) | Suprime CORPUS INTEGRADO + ADVERSARIAL | V3 dup_excess −7.3% | ✅ aceita |
| 0021 | Fix-E: _suppress_v6_irinf_artifacts | Suprime IRINF headers/footers V6 | V6 cer −4.8%, wer −12.3%, dup −8% | ✅ aceita |
| 0022 | Fix-F: _IRINF_DOSSI_RE (regex fix) + _VOLUME_SUBTITLE_RE | Suprime IRINF sem `**` + VOLUME subtitle em rotação V6 | V6 cer −4%, wer −8.2%, dup −3 blocos | ✅ aceita |
| 0022 | Fix-G: _suppress_tesseract_coordinate_dump | Suprime dados internos Tesseract (bounding-box/conf) em rotação V3 | V3 cer −42.9%, wer −76.8%, precision +18pp | ✅ aceita |
| 0023 | Fix-H: TESSERACT_OSD=1 em run_loop.py | OSD rotation correction para páginas rotacionadas | V3 cer_raw **+10.5% (regressão)** — p96: 0.896→3.287, p97: 0.871→1.480 | ❌ revertida |
| 0023 | Fix-I: _promote_allcaps_section_headers | Blocos all-caps TEXT→TITLE em scan_fullpage V6 | V6 h_f1 +0.66pp (0.9155→0.9221), h_rec +1.15pp | ✅ aceita (run 0024) |
| 0024 | Validação Fix-I isolada | Confirmação Fix-I sem interferência Fix-H | V3/V5/V7 idênticos 0022; V6 h_f1 0.9221 confirmado | ✅ best_known |
| 0025 | Fix-J: Rule A estendida para CAPTION | CAPTION all-caps antes de TABLE → H3 (causa: Tesseract classifica "QUADRO DE MEDIÇÕES" como CAPTION, não TEXT) | V6 h_f1 +0.66pp (0.9221→0.9287), h_rec +1.14pp (0.8588→0.8702), h_cer −0.51pp | ✅ aceita |
| 0026 | Fix-K: _promote_short_bold_column_headers | Blocos TEXT bold curtos (3–50 chars) em páginas com ≥2 candidatos + TITLE → H3. Famílias: two_columns, three_columns | V6 h_f1 +3.18pp (0.9287→0.9605), h_rec +5.73pp (0.8702→0.9275), +15 TPs. V7 bônus +6 TPs, h_f1 +1.69pp | ✅ aceita |
| 0027 | Fix-L v1: _promote_bold_pipe_title (TEXT only) | Bold TEXT block com ` \| ` separator entre primeiros 3 blocos sem H1 → H1 | Zero efeito — Tesseract BALANCED classifica bloco como CAPTION, não TEXT. Guard TEXT-only não disparou | ❌ sem efeito |
| 0028 | Fix-L v2: _promote_bold_pipe_title (TEXT+CAPTION) | Guard estendido para `(TEXT, CAPTION)` — mesmo padrão Fix-J. Watermark_stamp p26/61/77 + borderless_table p9/39/71 | V6 h_f1 +1.22pp (0.9605→0.9727), h_rec +2.29pp (0.9275→0.9504 ≥0.95 ✅), +6 TPs, h_cer −24.3% | ✅ aceita |
| 0029 | Fix-M: _promote_seção_subseção_headings | TEXT blocks com `^Seção N` → H2, `^Subseção N.M` → H3. Single-line, 4-120 chars. Padrão generalista PT-BR. Zero FP em V3/V6/V7 | V5 h_f1 +1.00pp (0.8276→0.8376), h_rec +1.47pp (0.7059→0.7206), +1 TP, h_prec=1.0 ✅ | ✅ aceita |
| 0030 | Fix-N: _omitir_gabarito_split() | Janela deslizante de 4 linhas para detectar "OMITIR NO" + "GABARITO" em linhas separadas de TABLE (página rotacionada 270° divide o marcador entre células). Extensão generalista do supressor existente. | V3 cer_raw −11.6% (0.1214→0.1073), p98: CER 3.891→0.955. V5/V6/V7 inalterados. | ✅ aceita |
| 0031 | Fix-O: _suppress_rotated_table_garble() + V8 baseline | Suprime TABLE blocks com token ≥7 letras contendo Q não seguido de U (fonologicamente impossível em PT/ES/FR/IT — sinal de texto girado 90°). V8 (90p) adicionado ao CORPUS_ORDER como 5.° corpus. | V3 cer_raw −17.1% (0.1073→0.08897), p99: CER 2.498→1.055, p116: CER 2.678→1.487. V8 baseline: cer=0.01887, h_f1=0.9909. V5/V6/V7 inalterados. | ✅ aceita |
| 0032 | Fix-P: _suppress_dollar_letter_garble() | Suprime TABLE e TEXT blocks com ≥2 ocorrências de `$[A-Z]` (dollar+maiúscula, impossível em PT — moeda sempre "R$ " com espaço). Captura "$Y $H $U" de páginas rotacionadas. | V3 cer_raw −7.1% (0.08897→0.082696), p99: CER 1.055→0.862, p116: CER 1.487→0.861. txt_precision +0.74pp. V5 inalterado. | ✅ aceita (best_known) |
| 0033 | Fix-Q (REVERTIDA): expandir $[A-Z] para $[A-Z0-9] | Tentar capturar "$Y $4 $4" expandindo regex para incluir dígitos. "$Y $4 $4" tinha coincidência de chars com referência — suprimir piorou CER. | V3 cer_raw +0.12% (0.082696→0.083714), p99 0.862→0.877, p116 0.861→0.872. Revertido para cp_0063. | ❌ revertida |
| 0034 | Fix-R (REVERTIDA): Q-sem-U para TABLE+TEXT | Tentar estender detecção Q-sem-U de TABLE para também TEXT. "ejogqea..." no p116 tinha numerais coincidentes com referência. | V3 cer_raw +0.71% (0.082696→0.083288), p116 CER 0.861→0.933 (+8.4%). Revertido para cp_0063. P5 ENCERRADO. | ❌ revertida |
| 0035 | Fix-S (REVERTIDA): _suppress_merged_header_floaters | Tentar suprimir TEXT blocks curtos (≤3 palavras, sem dígito/pipe) antes de TABLE — alvo: floaters "Trimestre Indicadores" + "Area." em V3 p91/111/118 (cabeçalhos mesclados colspan/rowspan extraídos como blocos separados). Os blocos contêm texto CORRETO presente na referência como cabeçalhos HTML — suprimí-los remove concordância de chars, piora CER. Mesmo padrão de falha de Fix-Q/R. P7 ENCERRADO. | V3 cer_raw +1.4% (0.082696→0.083856), p91 0.608→0.639, p111 0.612→0.642, p118 0.575→0.605. dup_excess 0.079→0.0736 (melhora não compensa CER). Revertido para cp_0064. | ❌ revertida |

---

## A. Estado e Validade da Baseline Original (2026-10-23)

> **Nota**: a baseline original foi executada com o schema antigo (pré-evaluation-3.0.0). Os valores desta seção NÃO são comparáveis com métricas das runs 0006 em diante (evaluation-3.0.0). Mantidos como referência histórica.

---

## A. Estado e Validade da Baseline

| Corpus | Páginas | Tempo extração | Engine | Status extração | Status avaliação | Métricas |
|---|---|---|---|---|---|---|
| V3 | 144 | 104,2 s | tesseract ✓ | **SUCCESS** | **COMPLETA** | 57 aplicáveis, 12 indisponíveis |
| V5 | 42 | 32,8 s | tesseract ✓ | **SUCCESS** | **COMPLETA** | 63 aplicáveis, 6 indisponíveis |
| V6 | 95 | 134,5 s | tesseract ✓ | **SUCCESS** | **COMPLETA** | 60 aplicáveis, 9 indisponíveis |
| V7 | 80 | 95,2 s | tesseract ✓ | **SUCCESS** | **FALHOU** | 0 disponíveis |

**Extração**: todos os 361 páginas foram extraídas com sucesso. Engine Tesseract confirmada em todos os corpora.  
**Avaliação**: V3, V5 e V6 têm métricas completas. V7 não tem métricas — falha técnica no avaliador (ver §C.P5 e §P5 detalhado).

### Localização dos artefatos

| Corpus | Diretório | Artefatos completos |
|---|---|---|
| V3 | `output/tesseract/baseline/V3/` | 10/10 ✓ |
| V5 | `output/tesseract/baseline/V5/` | 10/10 ✓ |
| V6 | `output/tesseract/baseline/V6/` | 10/10 ✓ |
| V7 | `output/tesseract/baseline/V7/` | 7/10 ✗ (sem métricas) |

### Métricas indisponíveis (comuns a V3/V5/V6)

As 6–12 métricas indisponíveis por corpus são todas de **CER sequencial global**: `cer_markdown_raw`, `cer_sequential_normalized`, `cer_sequential_visible`, `wer_sequential`. Motivo: o orçamento de 8M células Levenshtein é insuficiente para comparar documentos inteiros (V3 tem 42K × 62K chars ≈ 2,6 bilhões de pares potenciais). O avaliador retorna `resource_limited` para essas métricas sem interromper a avaliação. O impacto é baixo: as métricas de CER/WER do avaliador legado (per-page, agregado) cobrem o mesmo conteúdo com metodologia ligeiramente diferente.

---

## B. Principais Problemas por Documento

### B.1 — V3 (144 páginas, testes mistos)

**B.1.1 — Cabeçalho de corrida presente em cada página**

O documento V3 tem um cabeçalho de corrida no topo de cada página física: `CORPUS INTEGRADO V3 / LABORATÓRIO SINTÉTICO`. O Tesseract extrai esse texto como conteúdo regular. Ele aparece na linha 1 de praticamente todas as páginas do `extracted_paginated.md`:

```
## Página 1
CORPUS INTEGRADO V3 / LABORATÓRIO SINTÉTICO
# Página 001 | Corpus integrado de validação: texto nativo,
OCR e tabelas
Controle de página: QA-P001
```

Referência esperada para a mesma página:
```markdown
## Página 001 | Corpus integrado de validação: texto nativo, OCR e tabelas
**Controle de página:** QA-P001
```

Páginas afetadas: 143/144 (99,3%). `header_leakage_rate = 0.678` no avaliador legado.

**B.1.2 — Nível de headings deslocado em +1 nível**

Em V3, os títulos de página deveriam ser `## Página NNN | <título>` (H2) e subtítulos `### <subtítulo>` (H3). O Tesseract produz `# Página NNN | <título>` (H1) e `## <subtítulo>` (H2). O deslocamento é sistemático em todo o documento.

| Nível esperado (referência) | Nível extraído | Exemplo |
|---|---|---|
| `##` (H2) | `#` (H1) | `# Página 003 \| Como conduzir a comparação...` |
| `###` (H3) | `##` (H2) | `## Matriz experimental recomendada` |

`heading_level_accuracy = 0.0` — nenhum heading matched tem o nível correto.  
Páginas exemplificativas: 1–144 (sistemático).

**B.1.3 — Texto dos títulos truncado em quebras de página**

O título `# Página 001 | Corpus integrado de validação: texto nativo,` é seguido de `OCR e tabelas` na linha seguinte. O Tesseract quebra a linha do heading em duas, fazendo com que `heading_text_cer` seja extremamente alto (14.62) — o alinhamento canônico tenta combinar o heading com fragmentos de texto.

**B.1.4 — Tabelas ausentes em 24 páginas**

33 tabelas de referência não foram detectadas (`table_recall = 0.571`, `table_fn = 33`). Padrão identificado:

- **Páginas rotacionadas 90°** (ex: página 99): saída completamente ininteligível. Exemplo da extração da página 99:
  ```
  NVOS bh ap
  \+ 66 OUNd eulbed HDO ejusJasa 00'00S\+ 00'0SE-...
  ```
  A referência esperava uma tabela financeira de 4 colunas.

- **Páginas rotacionadas 90° com dados internos do Tesseract** (páginas 96, 97): em vez de texto, o Tesseract produz suas coordenadas e pontuações de confiança brutas. Exemplo da página 96 (CER = 11.04):
  ```
  PPT ap 96 euibed
  L00-9Z0Z-0VÍVANVA-HDO
  5  1  2  1  1  2  553  2107  17  74  24.107033  :ojexa
  5  1  2  1  1  3  551  2190  19  152  12.635422  Jopeoyyuap|
  ```
  Referência esperava parágrafo de texto simples + identificador.

- **Formulários OCR digitalizados** (ex: página 88): campos tabelados extraídos como texto plano em linha única:
  ```
  Página 088 | OCR com formulário digitalizado
  Campo Valor Protocolo FORM-OCR-0081 Solicitante Ana Carolina Valor R$1.209,75 Data 15/03/2026
  ```
  Referência esperava tabela de 2 colunas com 4 linhas + lista de checkboxes.

Páginas V3 com tabelas ausentes: 47, 65, 88–90, 92–95, 99, 107, 109, 112–115, 117, 119, 123–126, 140, 143.

**B.1.5 — Formatação inline perdida**

A formatação `**bold**` do gabarito não é reproduzida. Exemplo: `**Controle de página:** QA-P001` → `Controle de página: QA-P001`.

**B.1.6 — Duplicação de conteúdo**

O cabeçalho de corrida "CORPUS INTEGRADO V3 / LABORATÓRIO SINTÉTICO" aparece 143 vezes no documento extraído. `duplicate_content_rate = 0.181`, `duplicate_block_rate = 0.173`. `insertion_rate = 0.573` (muito acima de V5 e V6) é explicado principalmente pela inserção repetida desse cabeçalho.

---

### B.2 — V5 (42 páginas, validação de referência)

V5 é o corpus mais limpo. Principais problemas:

**B.2.1 — CER elevado em páginas com imagens OCR + texto nativo**

Páginas 18, 20, 32, 38 têm condições `raster_ocr` + `reading_order` e CER próximo a 0.65. Nesses casos o documento mistura texto nativo (extraído perfeitamente) com imagem OCR onde blocos são perdidos ou reordenados.

**B.2.2 — Headings não detectados em algumas páginas**

`heading_unmatched_ref_count = 20` — 20 headings do gabarito não encontrados. Páginas exemplificativas: 3, 6, 28.

Exemplo na página 6 (CER=0.324):
- Referência: heading `## Como conduzir a comparação sem viés`
- Hipótese: sem heading; conteúdo como parágrafo.

**B.2.3 — Conteúdo ausente em páginas com raster gráfico**

Páginas 21 e 31 (`raster_graphic`, `image_without_text`) têm CER ~0.39. Conteúdo visual-only é ignorado (esperado), mas o bloco de introdução associado à imagem às vezes também é omitido.

**B.2.4 — `reading_order_accuracy` legado baixo (0.213) — artefato, não problema real**

O avaliador canônico registra `pairwise_reading_order_accuracy = 0.9946` para V5 — indicando que 99,5% dos blocos alinhados estão na ordem correta. O valor legado baixo se explica pelo algoritmo LCS/total_ref_blocks: com blocos faltando (content loss), o LCS é menor mesmo sem reordenação real.

---

### B.3 — V6 (95 páginas, erros conhecidos)

**B.3.1 — Cabeçalho de corrida em 92/95 páginas**

O documento V6 tem dois cabeçalhos de corrida no topo de cada página:
1. `IRINF / DOSSIÊ DE INFRAESTRUTURA`
2. `VOLUME I · OUT 2026` (ou VOLUME II, III conforme seção)

Ambos são extraídos como conteúdo regular na linha 1 de cada página. Exemplo (página 1):
```
## Página 1

IRINF / DOSSIÊ DE INFRAESTRUTURA
VOLUME I · OUT 2026
# Relatório consolidado de fiscalização e conformidade
...
```

Referência esperada:
```markdown
## Página 001 | Relatório consolidado de fiscalização e conformidade

# Relatório consolidado de fiscalização e conformidade
```

`header_leakage_rate = 0.968` (92/95 páginas = 96.8%).

**B.3.2 — Sub-headings H2 ausentes (83 em 95 páginas)**

O gabarito define sub-headings H2 (ex: `## Objeto e abrangência`, `## Demonstrativo de pagamento apresentado em cópia digitalizada`) que não são detectados como headings na extração. Nesses casos, o texto que seguiria o heading é extraído corretamente como parágrafo, mas o heading em si é omitido.

Evidência: `heading_unmatched_ref_count = 83`, `heading_unmatched_hyp_count = 0`. Os 179 headings encontrados correspondem aos H1 de seção (nível correto), mas os H2 dentro de cada seção são perdidos.

**B.3.3 — Tabelas ausentes (27 tabelas, table_recall = 0.591)**

13 páginas com `table_f1 = 0.0`. Padrão por tipo:
- **Páginas rotacionadas** (31, 47, 75 — condição `rotated_page` + `rotated_table`): tabelas em orientação landscape não extraídas. A referência espera tabelas de múltiplas colunas; a extração contém apenas parágrafos de texto.
- **OCR de baixa resolução** (21, 42, 86 — condição `ocr_merged_cells` + `ocr_low_resolution`): tabelas digitalizadas com baixa qualidade não reconhecidas como tabela.
- **Formulário OCR full-page** (3, 33 — condição `fullpage_ocr`): tabela OCR de página inteira não convertida para markdown.
- **Tabelas adjacentes** (14, 45 — condição `adjacent_tables`): múltiplas tabelas lado a lado não detectadas.

**B.3.4 — Duplicação severa de conteúdo**

`duplicate_content_rate = 0.433` — 43,3% dos parágrafos são duplicados. Causa direta: os 2 cabeçalhos de corrida repetem-se em 92 páginas, e cada um conta como parágrafo duplicado. `duplicate_block_rate = 0.432`.

**B.3.5 — Identificadores com confusibles**

Condição `ocr_confusables` presente em múltiplas páginas. Páginas como 43, 82 têm `substitution_rate` acima de 0.70, indicando que caracteres visualmente similares (0/O, l/1, rn/m) são trocados pelo OCR.

---

### B.4 — V7 (80 páginas, acertos conhecidos)

A extração de V7 produziu conteúdo de alta qualidade. Sem métricas quantitativas para comparação direta com o gabarito. Análise baseada nos artefatos extraídos — ver §E para detalhe.

---

## C. Problemas Prioritários

### C.P1 — Header Leakage: V6 = 0.968, V3 = 0.678

**Definição da métrica**: Para cada página, coleta-se a primeira linha de texto não-vazia e não-marcador-de-página. Se uma dessas "primeiras linhas" aparece em ≥35% de todas as páginas, ela é contada como "cabeçalho vazado". A taxa é a fração de páginas cujo primeiro bloco é um cabeçalho vazado.

**Denominador**: número de páginas com conteúdo não vazio.

**V6 — causa raiz confirmada**: 
O documento V6 tem dois cabeçalhos físicos de corrida:
- `IRINF / DOSSIÊ DE INFRAESTRUTURA` (aparece em ~92/95 páginas como primeiro bloco)
- `VOLUME I · OUT 2026` / `VOLUME II · OUT 2026` / `VOLUME III · OUT 2026`

Como `IRINF / DOSSIÊ DE INFRAESTRUTURA` aparece em ~96.8% das páginas → taxa = 0.968.

**V3 — causa raiz confirmada**:
`CORPUS INTEGRADO V3 / LABORATÓRIO SINTÉTICO` aparece na primeira linha de ~97/144 páginas = 67.4% ≈ 0.678.

**Relação com `duplicate_content_rate`**:
- V6: `duplicate_content_rate = 0.433`. Os dois cabeçalhos de corrida (cada um repetido ~92 vezes) somam ~184 blocos duplicados num total de ~430 blocos → contribuição de ~43%. Correlação direta.
- V3: `duplicate_content_rate = 0.181`. O cabeçalho único repetido ~97 vezes num total de ~537 blocos → contribuição de ~18%. Correlação direta.

**V5 — ausência confirmada**:
V5 não tem cabeçalho de corrida repetido → `header_leakage_rate = 0.0`.

**Causa técnica**: O Tesseract extrai o conteúdo de cada linha física da página, incluindo os cabeçalhos repetidos de corrida que estão no layout. O pipeline não tem filtro para remover linhas que aparecem repetidamente no topo de páginas.

**Correção possível exclusivamente no Tesseract**: Sim. O pipeline de pós-processamento Tesseract pode detectar blocos que ocorrem na mesma posição vertical (no topo da página) e com o mesmo texto em múltiplas páginas, e suprimi-los. Isso não requer mudança no extrator nativo ou em outros engines.

**Risco de regressão em V5**: Baixo — V5 já tem `header_leakage_rate = 0.0`, portanto não seria afetado por um filtro bem calibrado.

---

### C.P2 — Heading Level Accuracy no V3: `heading_level_accuracy = 0.0`

**Definição**: Fração de headings que, ao serem associados por texto ao gabarito, têm o mesmo nível (`#` count). `heading_text_cer` usa alinhamento canônico de blocos.

**Causa raiz confirmada — deslocamento sistemático de +1 nível**:

| Referência | Hipótese (extraído) |
|---|---|
| `## Página 001 \| Corpus integrado...` (H2) | `# Página 001 \| Corpus integrado...` (H1) |
| `### Matriz experimental recomendada` (H3) | `## Matriz experimental recomendada` (H2) |

O deslocamento ocorre em todas as 144 páginas. Hipótese técnica: o cabeçalho de corrida `CORPUS INTEGRADO V3 / LABORATÓRIO SINTÉTICO` ocupa a primeira linha de cada página, "empurrando" o título real para uma posição visual secundária. O detector de headings do Tesseract pode estar atribuindo o nível H1 ao primeiro elemento prominente após o cabeçalho, que é o título da página.

**Nota sobre `heading_text_cer = 14.62`**: O alinhamento canônico de headings associa headings da hipótese com blocos do gabarito. Com headings em excesso (86 unmatched_hyp — provavelmente os `# Página NNN |` com texto truncado) sendo alinhados a blocos de conteúdo, o CER calculado nos pares alinhados reflete a diferença entre textos de heading e textos de parágrafo, resultando em CER > 100%.

**V5 e V6**: `heading_level_accuracy = 1.0` porque nesses documentos os títulos H1 de seção são corretamente extraídos como H1 (sem deslocamento). O problema de nível é específico de V3.

**Correção possível exclusivamente no Tesseract**: Provável. A lógica de atribuição de nível de heading está no pipeline de pós-processamento Tesseract. Se o cabeçalho de corrida for suprimido (P1), o detector de nível pode corrigir automaticamente. Requer investigação em `src/structured_pdf_text/layout/heading.py`.

---

### C.P3 — Ordem de Leitura: legado baixo, mas NÃO é problema real

**Aviso**: Não confundir métrica legada com métrica canônica.

| Corpus | `reading_order_accuracy` (legado) | `pairwise_reading_order_accuracy` (canônico) |
|---|---|---|
| V3 | 0.245 | **0.985** |
| V5 | 0.213 | **0.994** |
| V6 | 0.293 | **0.994** |

**O que a métrica legada mede**: LCS (Longest Common Subsequence) de blocos de texto × 100 chars, dividido pelo número de blocos de referência. Se blocos do gabarito não aparecem na hipótese (por serem tabelas não extraídas ou headings ausentes), o LCS diminui mesmo que a ordem dos blocos presentes seja correta.

**O que a métrica canônica mede**: Dado o conjunto de blocos alinhados entre gabarito e hipótese, qual fração dos pares de blocos está na ordem correta. Blocos ausentes não entram no cálculo.

**Conclusão**: A ordem de leitura dos blocos extraídos é excelente (>98% em todos os corpora). O valor baixo da métrica legada é um **artefato de cobertura de conteúdo**, não uma falha de ordenação. Os problemas reais (tabelas ausentes, headings ausentes, conteúdo perdido) fazem o LCS cair, mas os blocos presentes estão na ordem correta.

**Implicação**: Não é necessário corrigir o pipeline de ordenação de blocos. O esforço deve ser direcionado à detecção de tabelas (P4) e supressão de cabeçalhos (P1), que aumentarão o LCS e, por consequência, o valor da métrica legada.

---

### C.P4 — Tabelas: recall = 0.571 (V3) e 0.591 (V6)

**V3 — 33 tabelas não detectadas (table_fn = 33)**

Causas identificadas:

1. **Páginas rotacionadas (90° ou 180°)**: Tesseract produz saída ininteligível. Páginas 96 (90°), 97 (180°), 99 (tabela 90°) têm CER acima de 9.0 com saída completamente errada. Tesseract não consegue orientar a página — produz coordenadas internas ou texto invertido.

2. **Formulários OCR**: Tabela de campo-valor extraída como linha de texto contígua. Página 88 (`ocr_form`): `Campo Valor Protocolo FORM-OCR-0081 Solicitante Ana Carolina Valor R$1.209,75 Data 15/03/2026` em vez de tabela markdown.

3. **Tabelas em páginas com alto CER geral**: Páginas 47, 49, 65, 116 — tabelas presentes no gabarito não detectadas, provavelmente porque o layout OCR não é estruturado o suficiente para o detector de tabelas identificar bordas.

**V6 — 27 tabelas não detectadas (table_fn = 27)**

| Tipo de problema | Condição no manifesto | Páginas exemplificativas |
|---|---|---|
| Página rotacionada | `rotated_page`, `rotated_table` | 31, 47, 75 |
| OCR baixa resolução | `ocr_merged_cells`, `ocr_low_resolution` | 21, 42, 86 |
| OCR full-page | `fullpage_ocr`, `ocr_tables` | 3, 33 |
| Tabelas adjacentes | `adjacent_tables`, `narrow_ocr_table` | 14, 45 |

**Correção possível exclusivamente no Tesseract**: Parcial. O detector de tabelas do pipeline Tesseract pode ser melhorado para:
- OCR forms: usar heurística de alinhamento de campos para detectar estrutura tabular
- Full-page OCR: reconhecer padrão de linhas horizontais no output do Tesseract
- Tabelas adjacentes: não depende de outro engine

Páginas rotacionadas são mais complexas e podem requerer pré-processamento de imagem (deskew/rotate) que está fora do escopo do pipeline de pós-processamento.

---

### C.P5 — Falha de Avaliação do V7: `EditDistanceResourceLimit`

**Sintoma**: `compute_metrics.py` termina com rc=1 após processar 4/80 páginas. Nenhum arquivo de métricas é gravado.

**Causa raiz — técnica, não qualidade**:

A função `_process_page` em `scripts/compute_metrics.py` (linha ~1287) chama `_lev_distance(list(hyp_raw), list(ref_raw))` sem capturar `EditDistanceResourceLimit`. Quando o worker do `ProcessPoolExecutor` lança essa exceção, ela escapa da função e propaga até o handler `__main__` (linha 1834), que imprime o erro e chama `sys.exit(1)` antes de gravar qualquer resultado.

**Página gatilho identificada — página 4** (confirmado computacionalmente):

A página 4 é a maior página no primeiro lote submetido ao `ProcessPoolExecutor` (páginas 1–8 submetidas simultaneamente). Com 4/80 páginas completadas antes da falha, a página gatilho é necessariamente uma das 8 primeiras. A página 4 tem ref=3.156 chars, hyp=3.095 chars — é uma tabela de 12 colunas (`wide_12_columns`) completamente perdida na extração (conteúdo como prosa, CER real confirmado = **60,5%** via cálculo com budget=200M).

Custo Levenshtein banded para a página 4 (ref=3.156, hyp=3.095, band_inicial=61):

| Iteração | Band | Células | Acumulado |
|---|---|---|---|
| 1 | 61 | 378.917 | 378.917 |
| 2 | 122 | 749.126 | 1.128.043 |
| 3 | 244 | 1.467.218 | 2.595.261 |
| 4 | 488 | 2.814.098 | 5.409.359 |
| 5 | 976 | 5.150.642 | **10.559.001** → EXCEDE 8M |

O budget é excedido na 5ª iteração. A exceção é levantada dentro do worker e não é capturada.

**Nota**: a página 46 (3.131 chars hyp) também excede o budget (band_inicial=62, mesmo padrão de iterações), mas seria processada apenas no lote 6 (páginas 41–48), muito depois do crash.

**Por que V5 não falhou**: Páginas do V5 têm média de ~600 chars. Para 600 chars, mesmo com band de 992, cells ≈ 600 × 1985 = 1,2M — bem dentro do budget.

**Por que o canonical completou mas o legacy não**: Para V3/V5/V6, o `EditDistanceResourceLimit` é capturado dentro de `evaluate_document` (canonical pipeline em `src/structured_pdf_text/evaluation/`) e retornado como `resource_limited`. Isso não afeta a avaliação legada porque o canonical termina antes. Para V7, a exceção ocorre dentro de um worker do `ProcessPoolExecutor` que processa os per-page metrics (canonical + legacy juntos). A exceção não é capturada em `_process_page`, escalando para o handler principal.

**Resultados preservados**: Nenhum. O crash ocorre antes da gravação de qualquer arquivo de métricas.

**Solução necessária**: Adicionar `try/except EditDistanceResourceLimit` dentro de `_process_page` em `scripts/compute_metrics.py`, retornando status `resource_limited` para as métricas de CER daquela página em vez de propagar a exceção. Não altera nenhuma fórmula ou algoritmo. Requer autorização.

---

## D. Análise Especial do V6 (Corpus de Erros)

### D.1 — Header Leakage: distribuição por página

De 95 páginas:
- 92/95 têm `IRINF / DOSSIÊ DE INFRAESTRUTURA` como primeiro bloco  
- As 3 exceções são páginas onde outro tipo de conteúdo domina o início (ex: página 3 começa com `BOLETIM DE INSPEÇÃO N° 040/2026`)

Cabeçalhos detectados no V6:
| Cabeçalho | Ocorrências estimadas |
|---|---|
| `IRINF / DOSSIÊ DE INFRAESTRUTURA` | ~92 páginas |
| `VOLUME I · OUT 2026` | ~30 páginas |
| `VOLUME II · OUT 2026` | ~30 páginas |
| `VOLUME III · OUT 2026` | ~32 páginas |

Os cabeçalhos `VOLUME X · OUT 2026` aparecem como segunda linha após o principal. Como o avaliador usa apenas a **primeira** linha de cada página, `VOLUME X` não é contado como cabeçalho vazado separadamente.

### D.2 — Gravidade e frequência dos problemas

| Problema | Páginas afetadas | Gravidade |
|---|---|---|
| Header leakage | 92/95 (96,8%) | **ALTA** — afeta toda a extração |
| Sub-headings H2 ausentes | 83/95 (87,4%) | **ALTA** — estrutura fragmentada |
| Duplicação de conteúdo | 95/95 (100%) | **ALTA** — decorrente do header leakage |
| Tabelas rotacionadas não extraídas | 3/95 (3,2%) | **ALTA** — conteúdo crítico perdido |
| Tabelas OCR low-res não extraídas | 3/95 (3,2%) | **MÉDIA** — tabelas financeiras |
| OCR confusibles (caracteres trocados) | ~10/95 (10,5%) | **MÉDIA** — erros em identificadores |
| Tabelas adjacentes não detectadas | 2/95 (2,1%) | **MÉDIA** |
| invalid_markdown_rate | 3/95 (3,2%) | **BAIXA** |

### D.3 — Alternativas de correção no pipeline Tesseract

**Header leakage (P1)**:  
O pipeline Tesseract pode implementar um filtro de pós-processamento: 
1. Analisar as primeiras `N` linhas de cada página extraída
2. Construir um índice de frequência de linhas de topo (`Counter`)
3. Suprimir linhas que aparecem em >40% das páginas (threshold conservador)
4. Módulo candidato: `src/structured_pdf_text/layout/engine.py` ou um novo pós-processador Tesseract

Risco de regressão: **Baixo** — V5 não tem headers repetidos, V7 tem apenas "AGÊNCIA DE INFRAESTRUTURA E OBRAS" na página 1 (não repetido).

**Sub-headings H2 ausentes**:  
Tesseract detecta texto por confiança de OCR. Sub-headings em documentos OCR podem ter tamanho de fonte levemente maior que o corpo, mas não grande o suficiente para ser classificado como heading pelo detector atual. 

Alternativa no pipeline Tesseract:
1. Usar a metadata de fonte do Tesseract (`page.blocks[].paragraphs[].words[].symbols[].property.detected_break`) para identificar texto em fonte maior
2. Aplicar heurística de posição + tamanho relativo para classificar como heading
3. Módulo candidato: `src/structured_pdf_text/layout/heading.py`

Risco de regressão: **Médio** — pode introduzir falsos positivos de headings em V5.

**Tabelas rotacionadas**:  
Requer pré-processamento de imagem (deskew) antes do OCR. Isso está fora do pós-processamento de texto. Possível via Tesseract com `--psm 0` (detect orientation) + rotação da imagem antes do OCR. Módulo candidato: `automation/extract_worker.py` ou pipeline de imagem Tesseract.

Risco de regressão: **Baixo para V5/V3** — V5 não tem páginas rotacionadas; V3 tem (páginas 96/97/99).

---

## E. Análise Especial do V7 (Corpus de Acertos)

**Nota metodológica**: Sem métricas calculadas, esta análise é inteiramente baseada no arquivo `output/tesseract/baseline/V7/extracted_paginated.md`. Não são apresentados valores numéricos não calculados.

### E.1 — Características que parecem funcionar corretamente

**E.1.1 — Tabelas markdown de alta qualidade**

Páginas 2, 5, 6, 7 contêm tabelas amplas extraídas corretamente em markdown.

> **Nota de correção**: A página 4 foi REMOVIDA desta lista. Embora o manifesto a descreva como uma tabela de 12 colunas (`wide_12_columns`, ref=3.156 chars), o hipótese extraído contém **texto em prosa**, não estrutura de tabela — CER_visible=68% (confirmado pelo benchmark 64M, status=ok). Esta é uma das páginas mais difíceis do V7 e constitui um caso de falha de extração tabular, não um acerto. Ver §C.P5 para análise da falha.

Exemplo — página 2 (tabela financeira, `wide_table`):
```markdown
| 001 | Tubo PVC \| Série 1 | 12 | R$ 1.234,56 | R$ 14.814,72 | +2,50% |
| 002 | Conexão 90° | 03 | R$ 9.876,54 | R$ 29.629,62 | -0,75% |
```
Estrutura de separadores `| --- |` correta, alinhamento de colunas preservado.

**Resultado do benchmark 64M** (métricas V7 por-página, status=ok em todas as 80 páginas):
- `table_detection_f1 = 0,9655` — 96,5% das tabelas detectadas
- `cell_content_exact_match = 0,9824` — conteúdo de células quase perfeito
- `table_column_count_accuracy = 0,9333` — contagem de colunas correta em 93%
- Exceção: página 4 (wide_12, 12 colunas) extraída como prosa — falha de detecção

**E.1.2 — Valores monetários e percentuais precisos**

Página 2 (tabela financeira):
```
| 001 | Tubo PVC \| Série 1 | 12 | R$ 1.234,56 | R$ 14.814,72 | +2,50% |
| 002 | Conexão 90° | 03 | R$ 9.876,54 | R$ 29.629,62 | -0,75% |
| 003 | Devolução | 01 | -R$ 250,00 | -R$ 250,00 | 0,00% |
| 005 | Serviço complementar | 02 | € 98,40 | € 196,80 | 12,5% |
```
Formatação brasileira correta (`R$ 1.234,56`), moeda estrangeira (`€`), percentuais com sinal (`+2,50%`, `-0,75%`), célula vazia preservada como célula vazia.

**E.1.3 — Identificadores complexos preservados**

Página 3:
```
PROTOCOLO=AIO-V7-001
HASH=6fa4-92bd-ptBR
STATUS=VALIDO
```
Página 5 (série de ordens de serviço):
```
OS-2026-010001, OS-2026-010002, ..., OS-2026-010018
```
Sequência de 18 identificadores consecutivos extraídos sem troca de dígitos.

**E.1.4 — Datas no formato brasileiro**

Página 7: `04/09/2026`, `05/09/2026`, ... — série de 15 datas extraídas corretamente.  
Página 2: `03/10/2026` (data no corpo do texto).

**E.1.5 — Tabelas de múltiplas páginas sem duplicação de dados**

Páginas 5 e 6 são continuação de uma tabela (`multipage_tables`). A página 6 inicia nos registros 019–036, sem repetir os registros 001–018 da página 5. O texto da página 6 indica:
> "Encerrada a listagem desta regional, a equipe confirmou 36 ordens de serviço sem repetição de sequenciais."

**E.1.6 — Referências marginais preservadas**

Página 8 (`near_page_edges`): `BORDA-ESQ-001` e `BORDA-DIR-001` aparecem no texto extraído, indicando que o conteúdo próximo às margens foi capturado.

**E.1.7 — HTML de tabela preservado (página 3)**

A tabela de índice com `<table>`, `<tr>`, `<td>` foi preservada como HTML no markdown extraído. Pontilhados do sumário (`........`) foram mantidos.

### E.2 — Comportamentos a proteger nas futuras iterações

1. **Formatação de tabelas markdown**: estrutura de pipe `|` preservada exatamente
2. **Moedas brasileiras** com separador de milhar ponto e decimal vírgula (`R$ 1.234,56`)
3. **Sinal de variação percentual** com precisão (`+2,50%`, `-0,75%`)
4. **Identificadores com zeros à esquerda**: `001`, `002`, `#2001`, `NF-89001` — sem truncamento
5. **Células vazias vs. zero**: `|  |` (vazia) vs. `| R$ 0,00 |` (zero — distinção preservada)
6. **Sequências de datas** em formato pt-BR (dd/mm/aaaa)
7. **Multi-página sem duplicação** de linhas de tabela
8. **Identificadores complexos** com padrão `OS-YYYY-NNNNNN`
9. **Cabeçalhos de tabela de 12 colunas** sem colapso ou omissão de colunas

---

## F. Classificação e Priorização

| ID | Descrição | Corpora | Páginas exemplificativas | Gravidade | Impacto | Evidência | Causa raiz | Módulo candidato | Somente Tesseract | Risco regressão | Prioridade | **Status** |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **F01** | Cabeçalho de corrida extraído como conteúdo | V3, V6 | V3:1-143; V6:1-92 | ALTA | Duplicação 18–43% do conteúdo; header_leakage 0.678/0.968 | Linhas "CORPUS INTEGRADO V3..." e "IRINF / DOSSIÊ..." em topo de cada página | Confirmada: Tesseract extrai todas as linhas visuais sem filtro de frequência | `layout/engine.py` ou pós-processador dedicado | **Sim** | Baixo | **1** | ✅ **RESOLVIDO** (runs 0001–0003, A1+A2b+A3) |
| **F02** | Nível de heading deslocado +1 em V3 | V3 | 1–144 (sistemático) | ALTA | `heading_level_accuracy=0.0`; estrutura de documento inválida | `## Página 001 \| ...` (ref) → `# Página 001 \| ...` (hipótese) em todas as páginas | Confirmada: TITLE regions têm text em ocr_lines (não native_lines); override loop requer ocr fallback + F02h majority shift | `layout/heading.py` | **Bloqueio arquitetural** (ver §Isolamento) | Baixo (aguarda isolamento) | **1** (ativo) | 🔄 **ATIVO** — fix implementado (F02f+F02e+F02h+F02g), aguarda nova run de validação + autorização isolamento |
| **F03** | Sub-headings H2 ausentes em V6 | V6 | Maioria dos 95 (83 unmatched_ref) | ALTA | Estrutura hierárquica perdida; heading_f1=0.667 na baseline | Gabarito: `## Demonstrativo de pagamento...`; hipótese: não existe | Hipótese: fonte dos sub-headings próxima ao corpo; detector não os classifica como heading | `layout/heading.py` | **Sim** | Médio | **2** (ativo) | ⚠️ **PARCIAL** — run 0011 V6 heading_f1=0.9155 (melhora significativa, mas origem incerta: mudança de schema ou fix) |
| **F04** | Tabelas rotacionadas não extraídas | V3, V6 | V3:96,97,99; V6:31,47,75 | ALTA | Conteúdo totalmente perdido; CER >9.0 em V3 p.96 | V3 p.99: `NVOS bh ap \+ 66 OUNd...` em vez de tabela financeira | Confirmada: Tesseract sem deskew não processa páginas rotacionadas | `automation/extract_worker.py` (pré-processamento) | **Sim** | Baixo | **3** (ativo) | 🔄 **ATIVO** — sem tentativa de correção |
| **F05** | Formulários OCR extraídos como texto plano | V3, V6 | V3:88; V6:3,33 | ALTA | Tabelas de campo-valor perdidas; table_recall cai | V3 p.88: `Campo Valor Protocolo FORM-OCR-0081 Solicitante Ana Carolina...` | Hipótese: Tesseract detecta campos mas não reconhece estrutura tabular | `layout/engine.py` | **Sim** | Médio | **4** (ativo) | 🔄 **ATIVO** — sem tentativa de correção |
| **F06** | Falha de avaliação V7 por budget Levenshtein | V7 | p.46 (3131 chars), p.4, p.63 | ALTA | 0 métricas disponíveis para V7 | `EditDistanceResourceLimit`: total 10,8M células no band 992 (p.46) | Confirmada: `_process_page` não captura `EditDistanceResourceLimit` | `scripts/compute_metrics.py` (linha ~1287) | N/A — problema do avaliador | N/A | N/A | ✅ **RESOLVIDO** (avaliador atualizado evaluation-3.0.0; V7 produz métricas desde run 0006) |
| **F07** | Tabelas OCR de baixa resolução não detectadas | V6 | 21, 42, 86 | MÉDIA | Planilhas financeiras perdidas; table_recall reduzido | Condição `ocr_merged_cells` + `ocr_low_resolution`; extracted tem apenas parágrafos | Hipótese: low-res + células mescladas impedem detecção de bordas pelo Tesseract | `layout/engine.py` | **Sim** | Baixo | **5** | 🔄 **ATIVO** — sem tentativa de correção |
| **F08** | OCR confusibles em V6 (substitution_rate alto) | V6 | 43, 82 (S>0.47) | MÉDIA | Identificadores e valores incorretos | V6 p.43 `substitution_rate=0.753`; condição `ocr_confusables` | Esperada para OCR em docs digitalizados com qualidade variada | Tesseract lang/psm config | **Sim** | Médio | **6** | 🔄 **ATIVO** — sem tentativa de correção |
| **F09** | CER sequencial global indisponível (budget) | V3, V5, V6 | — (documento inteiro) | BAIXA | 6–12 métricas globais marcadas `resource_limited` | Documentos com 25K–94K chars excedem 8M células no nível do documento inteiro | Confirmada: 8M cells insuficiente para Levenshtein de documento completo | `src/.../edit_distance.py` (DEFAULT_MAX_EDIT_CELLS) | N/A — problema do avaliador compartilhado | N/A | **7** | ⚠️ **PARCIAL** — evaluation-3.0.0 aumentou métricas disponíveis (8 indisponíveis em run 0011, antes 12); globais ainda resource_limited |
| **F10** | Formatação bold/italic perdida em V3 | V3 | 1-144 (sistemático) | BAIXA | `**Controle de página:**` → `Controle de página:` | Evidência em múltiplas páginas | Hipótese: Tesseract não informa markup de estilo ao pipeline de pós-processamento | `renderers/markdown.py` | **Sim** | Baixo | **8** | 🔄 **ATIVO** — sem tentativa de correção |

### Legenda de prioridade e status

- ✅ **RESOLVIDO**: confirmado por métricas em pelo menos uma run válida
- 🔄 **ATIVO**: problema identificado, sem correção validada
- ⚠️ **PARCIAL**: melhora observada, mas causa e magnitude incertas
- 🚨 **REGREDIDO**: problema introduzido ou agravado por uma correção recente

Prioridade atual da fila ativa: F02 → F03 → F04 → F05. F07/F08/F10 secundários.

---

## G. Problemas Resolvidos

### G.1 — F01: Cabeçalho de corrida (header leakage)

**Resolvido em**: runs 0001–0003 (A1 + A2b + A3)  
**Evidência**: run 0011 V3 `header_leakage_rate=0.0` (era 0.678 na baseline), V6 `header_leakage_rate=0.0` (era 0.968), V5/V7 `header_leakage_rate=0.0` (inalterados).  
**Correções aplicadas**:
- A1: `repeated_regions.py` — edge_band 8% → 12% para melhor detecção de headers/footers repetidos
- A2b: `repeated_regions.py` — coverage_rate threshold 12% (entre section headings ~5% e OCR-variant furniture ~20%)
- A3: `extract_worker.py` — frequency suppressor Tesseract

**Cautela**: run 0003 usou schema antigo. Confirmação definitiva: run 0011 (evaluation-3.0.0, rank_eligible=True).

### G.2 — F06: Falha de avaliação V7 por budget Levenshtein

**Resolvido em**: evaluador atualizado para evaluation-3.0.0 (entre runs 0005 e 0006)  
**Evidência**: run 0011 V7 `status=complete`, `heading_f1=0.9636`, `cer_raw=0.014055`.  
**Causa da resolução**: o avaliador passou a capturar `EditDistanceResourceLimit` por página e retornar `resource_limited` em vez de propagar a exceção e interromper a avaliação inteira.

---

## H. Regressões

*Nenhuma regressão confirmada na série atual.* O run 0012 teve `metrics_failed` em V3, mas foi uma falha técnica de separador de página (F02h shift para H2 colidiu com bare separator `## Página N`), não uma regressão de qualidade.

O fix de separador foi implementado em `markdown.py` (`_render_page_section`) e aguarda validação na próxima run.

---

## I. Problemas Novos (identificados após baseline)

### I.1 — Bloqueio arquitetural: F02 afeta todas as engines

**Identificado em**: análise de reorganização (2026-10-26)  
**Descrição**: As correções F02f/F02e/F02h/F02g implementadas em `heading.py` e `markdown.py` não têm guards de engine. `assign_heading_levels()` não recebe parâmetro `engine` — o call site em `assemble/document.py:124` também não tem essa informação. Como resultado, qualquer engine OCR (EasyOCR, PaddleOCR, RapidOCR) que produza regiões com texto `^#{1,6}\s+\S` seria afetada pelas promoções F02f/F02e/F02h.  
**Impacto**: regressão potencial em outras engines não testada.  
**Status**: aguardando autorização para proposta de interface mínima (`HeadingOptions`).

### I.2 — Colisão de separador de página (resolvida em markdown.py, não validada)

**Identificado em**: run 0012 (V3 metrics_failed)  
**Descrição**: F02h promove `# Página NNN | Title` para `## Página NNN | Title`. O renderer também emite `## Página N` como separador de seção. Com a promoção, ambos aparecem no mesmo H2 nível para a página N — o avaliador rejeita como "Duplicate page number N in reference Markdown".  
**Fix implementado**: `markdown.py` `_render_page_section` agora pula o separador bare `## Página N` quando o primeiro bloco de conteúdo já é `## Página NNN | Title` para a mesma página.  
**Status**: aguarda validação na nova run de baseline.

---

## J. Isolamento Arquitetural — Decisão D1

**Contexto**: CLAUDE.md proíbe modificar contratos e comportamentos compartilhados sem autorização.

### Opção A: HeadingOptions dataclass — **Descartada**

Descartada pelo usuário (2026-10-26): modificaria contrato de `assign_heading_levels()` em código compartilhado.

### Opção B-extended (AUTORIZADA): ContentBlock post-processor

**Decisão do usuário (2026-10-26)**: implementar como módulo de **produção** em `src/structured_pdf_text/`, seguindo o padrão existente `_suppress_tesseract_running_headers` em `extract_worker.py`.

- Criar `src/structured_pdf_text/layout/tesseract_heading.py` (ou similar)
- Função chamada por `extract_worker.py` APÓS `assemble_document()`, SOMENTE quando `ocr_engine == "tesseract"`
- Operaria sobre `ContentBlock` objetos já montados
- Sem modificar `assign_heading_levels()` nem qualquer código compartilhado
- Deve ser acessível via CLI (não apenas em automation/)

**Restrições** (do usuário):
- Não promover `#` de forma indiscriminada — deve ter critério específico para `Página NNN`
- Adicionar teste de regressão para colisão de separador de página
- Não adicionar `if engine ==` em código compartilhado

**Status**: ✅ AUTORIZADO — aguarda implementação (pós-baseline).

---

## K. Histórico das Runs

| Run | Código (commit/checkpoint) | Alteração investigada | CER V3 | heading_f1 V3 | heading_level_acc V3 | CER V6 | heading_f1 V6 | CER V7 | Decisão | F afetados |
|---|---|---|---|---|---|---|---|---|---|---|
| baseline | `output/tesseract/baseline/` | — (baseline original, schema antigo) | 0.612 | 0.4848 | 0.0 | 0.402 | 0.667 | n/a | baseline | — |
| 0001 | cp_0023 + A1 | A1: edge_band 8%→12% | n/d | n/d | n/d | **0.0** (leak fixado) | n/d | n/d | C.AJUSTAR (V5 regression heading_f1 0.79→0.45) | F01 parcial |
| 0002 | cp_0025 + A1+A2+A3 | A2 (35%) + A3 | n/d | n/d | n/d | n/d | n/d | n/d | C.AJUSTAR (V3 CER regression +18.9%; A2 35% excessivo) | F01 parcial |
| 0003 | cp_0028/cp_A2b | A2b (12%) — threshold ótimo | 0.612 | 0.4848 | 0.0 | 0.402 | 0.667 | n/d | **A.ACEITAR_PROV.** (provisional_count=1) | **F01 RESOLVIDO** |
| 0004–0009 | cp_0029–cp_0036 | F02a–F02g (tentativas de fix heading V3) | ~0.38 | ~0.2 | 0.0 | ~0.05 | ~0.92 | ~0.014 | C.AJUSTAR (F02a-F02g cada um no-op por razão diferente) | F02 ativo |
| 0010 | cp_0039 | F02h (majority shift) | ~0.39 | ~0.2 | 0.0 | ~0.05 | ~0.92 | ~0.014 | C.AJUSTAR (F02g no-op para V3: OCR path; F02h implementado) | F02 ativo |
| 0011 | cp_0039 + F02h | F02h + early-return fix | 0.3889 | 0.208 | 0.0 | 0.0497 | 0.9155 | 0.01406 | E.INCONCLUSIVO (schema mudou para evaluation-3.0.0; F02h pendente) | F02 ativo |
| 0012 | cp_0041 | F02f+F02e+F02h+F02g+renderer_strip | n/a (V3 fail) | n/a | n/a | 0.0497 | 0.9155 | 0.01406 | E.INCONCLUSIVO (V3 metrics_failed: duplicate page anchor) | F02 ativo |
| **0013** | **cp_0042 → HEAD+A1+A2b+A3 (F02 revertido)** | **nova baseline série 2** | **0.3889** | **0.208** | **0.0** | **0.0497** | **0.916** | **0.0141** | **BASELINE_SERIE2** (todos rank_eligible, evaluation-3.0.0) | **baseline série 2** |

> Nota: métricas das runs 0001–0005 usam schema antigo (grupo1_texto / grupo4). A partir de run 0006, evaluation-3.0.0. A partir de run 0013, baseline série 2 (F02 revertido, código limpo).

---

## Apêndice: Verificação de não-modificação de código compartilhado

Nenhum módulo foi modificado durante a produção desta análise. Todos os dados vieram da leitura de artefatos em `output/tesseract/baseline/` e dos gabaritos em `corpus/`.

Os dois arquivos marcados como modificados no git (`block_alignment.py`, `edit_distance.py`) foram alterados em sessão anterior para suportar a baseline e estão documentados em `memory/knowledge_base.md`.
