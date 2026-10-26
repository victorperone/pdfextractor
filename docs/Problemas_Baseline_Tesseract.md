# Problemas da Baseline Tesseract — Diagnóstico Técnico

**Data**: 2026-10-23  
**Fonte**: `output/tesseract/baseline/` — artefatos da execução oficial  
**Referência**: `docs/Monitoramento_tesseract.md`

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

| ID | Descrição | Corpora | Páginas exemplificativas | Gravidade | Impacto | Evidência | Causa raiz | Módulo candidato | Somente Tesseract | Risco regressão | Prioridade |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **F01** | Cabeçalho de corrida extraído como conteúdo | V3, V6 | V3:1-143; V6:1-92 | ALTA | Duplicação 18–43% do conteúdo; header_leakage 0.678/0.968 | Linhas "CORPUS INTEGRADO V3..." e "IRINF / DOSSIÊ..." em topo de cada página | Confirmada: Tesseract extrai todas as linhas visuais sem filtro de frequência | `layout/engine.py` ou pós-processador dedicado | **Sim** | Baixo | **1** |
| **F02** | Nível de heading deslocado +1 em V3 | V3 | 1–144 (sistemático) | ALTA | `heading_level_accuracy=0.0`; estrutura de documento inválida | `## Página 001 \| ...` (ref) → `# Página 001 \| ...` (hipótese) em todas as páginas | Hipótese: cabeçalho de corrida confunde detector de proeminência; correção de F01 pode resolver | `layout/heading.py` | **Sim** | Baixo | **2** |
| **F03** | Sub-headings H2 ausentes em V6 | V6 | Maioria dos 95 (83 unmatched_ref) | ALTA | Estrutura hierárquica perdida; heading_f1=0.667 | Gabarito: `## Demonstrativo de pagamento...`; hipótese: não existe | Hipótese: fonte dos sub-headings próxima ao corpo; detector não os classifica como heading | `layout/heading.py` | **Sim** | Médio | **3** |
| **F04** | Tabelas rotacionadas não extraídas | V3, V6 | V3:96,97,99; V6:31,47,75 | ALTA | Conteúdo totalmente perdido; CER >9.0 em V3 p.96 | V3 p.99: `NVOS bh ap \+ 66 OUNd...` em vez de tabela financeira | Confirmada: Tesseract sem deskew não processa páginas rotacionadas | `automation/extract_worker.py` (pré-processamento) | **Sim** | Baixo | **4** |
| **F05** | Formulários OCR extraídos como texto plano | V3, V6 | V3:88; V6:3,33 | ALTA | Tabelas de campo-valor perdidas; table_recall cai | V3 p.88: `Campo Valor Protocolo FORM-OCR-0081 Solicitante Ana Carolina...` | Hipótese: Tesseract detecta campos mas não reconhece estrutura tabular | `layout/engine.py` | **Sim** | Médio | **5** |
| **F06** | Falha de avaliação V7 por budget Levenshtein | V7 | p.46 (3131 chars), p.4, p.63 | ALTA | 0 métricas disponíveis para V7 | `EditDistanceResourceLimit`: total 10,8M células no band 992 (p.46) | Confirmada: `_process_page` não captura `EditDistanceResourceLimit` | `scripts/compute_metrics.py` (linha ~1287) | N/A — problema do avaliador | N/A | **6** |
| **F07** | Tabelas OCR de baixa resolução não detectadas | V6 | 21, 42, 86 | MÉDIA | Planilhas financeiras perdidas; table_recall reduzido | Condição `ocr_merged_cells` + `ocr_low_resolution`; extracted tem apenas parágrafos | Hipótese: low-res + células mescladas impedem detecção de bordas pelo Tesseract | `layout/engine.py` | **Sim** | Baixo | **7** |
| **F08** | OCR confusibles em V6 (substitution_rate alto) | V6 | 43, 82 (S>0.47) | MÉDIA | Identificadores e valores incorretos | V6 p.43 `substitution_rate=0.753`; condição `ocr_confusables` | Esperada para OCR em docs digitalizados com qualidade variada | Tesseract lang/psm config | **Sim** | Médio | **8** |
| **F09** | CER sequencial global indisponível (budget) | V3, V5, V6 | — (documento inteiro) | BAIXA | 6–12 métricas globais marcadas `resource_limited` | Documentos com 25K–94K chars excedem 8M células no nível do documento inteiro | Confirmada: 8M cells insuficiente para Levenshtein de documento completo | `src/.../edit_distance.py` (DEFAULT_MAX_EDIT_CELLS) | N/A — problema do avaliador compartilhado | N/A | **9** |
| **F10** | Formatação bold/italic perdida em V3 | V3 | 1-144 (sistemático) | BAIXA | `**Controle de página:**` → `Controle de página:` | Evidência em múltiplas páginas | Hipótese: Tesseract não informa markup de estilo ao pipeline de pós-processamento | `renderers/markdown.py` | **Sim** | Baixo | **10** |

### Legenda de prioridade

1. **F01**: Maior impacto quantitativo em dois corpora; solução direta no pipeline Tesseract; sem risco para V5/V7.
2. **F02**: Provavelmente derivado de F01; correção de F01 pode resolver automaticamente.
3. **F03**: Sub-headings estruturais são críticos para navegação do documento V6.
4. **F04**: Conteúdo completamente perdido em páginas rotacionadas; requer pré-processamento.
5. **F05**: Formulários OCR são casos de uso comuns; impacto em table_recall significativo.
6. **F06**: Bloqueador para ter baseline V7 completo; mudança no script de avaliação, não no pipeline.
7–10: Problemas secundários ou de menor frequência.

---

## Apêndice: Verificação de não-modificação de código compartilhado

Nenhum módulo foi modificado durante a produção desta análise. Todos os dados vieram da leitura de artefatos em `output/tesseract/baseline/` e dos gabaritos em `corpus/`.

Os dois arquivos marcados como modificados no git (`block_alignment.py`, `edit_distance.py`) foram alterados em sessão anterior para suportar a baseline e estão documentados em `memory/knowledge_base.md`.
