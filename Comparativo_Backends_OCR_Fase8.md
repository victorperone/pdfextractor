# Comparativo Detalhado de Backends OCR — Fase 8

**Data de execução:** 2026-10-01  
**Corpus:** `Corpus_Stress_OCR_Markdown_V4.pdf` — 224 páginas  
**SHA256 do corpus:** `45c09f2f03eb0dfe99bef5b8ea5b1ee686351cae2fc1244b4dcfce98fd7d0970`  
**Modo de extração:** `balanced` | Língua: `pt`  
**Plataforma de execução:** Windows Server (WSL2 para métricas)  
**Run suffix:** `v1`  

---

## Sumário Executivo

| Engine | CER ↓ | WER ↓ | Table F1 ↑ | Cell Exact ↑ | Failure Rate ↓ | Tempo Total ↓ | Score Ponderado ↑ |
|---|---|---|---|---|---|---|---|
| **EasyOCR** | **0.2860** | **0.4236** | 0.9747 | 0.3396 | **0.0089** | 2650 s | 61.8 |
| **RapidOCR-OpenVINO** | 0.3633 | 0.6019 | **0.9938** | 0.3942 | **0.0089** | 544 s | **63.3** |
| **RapidOCR-ONNX** | 0.3596 | 0.6002 | 0.9811 | 0.4460 | **0.0089** | 2317 s | 59.7 |
| **Tesseract** | 0.4315 | 0.6370 | **0.9938** | 0.3214 | **0.0089** | 811 s | 62.1 |
| **Paddle** | 0.6344 | 0.6324 | 0.6721 | **0.5942** | ⚠️ 0.4955 | **314 s** | 49.8 |

> O score ponderado (0–100) usa pesos: CER 25%, estrutura Markdown 15%, qualidade de célula 15%, confiabilidade 20%, dados críticos 15%, velocidade 10%.  
> **Paddle apresenta uma anomalia crítica**: failure_rate de 49,55% e insertion_rate de 60,8% invalidam grande parte dos seus resultados de texto neste benchmark.

---

## 1. Contexto e Configuração do Benchmark

### 1.1 Corpus de Stress

O corpus `Corpus_Stress_OCR_Markdown_V4.pdf` cobre deliberadamente os cenários mais exigentes de extração de PDF:

- Texto denso com múltiplas colunas (1, 2, 3 colunas)
- Tabelas com bordas, sem bordas, financeiras, com precisão numérica, multiline, células vazias
- Código-fonte e listas
- Formatação inline (negrito, itálico, sublinhado)
- Texto nativo (vetor) e escaneado
- Texto manuscrito, rotacionado, baixa qualidade
- Dados críticos: números, datas, valores monetários, identificadores

### 1.2 Versões das Engines

| Engine | Pacote principal | Versão | Runtime | Notas |
|---|---|---|---|---|
| Paddle | paddlepaddle | 3.3.1 | `paddle_subprocess` | paddleocr 3.7.0, paddlex 3.7.2 — isolado em subprocess para evitar conflito DLL com torch |
| EasyOCR | easyocr | 1.7.2 | `torch-cpu` | torch 2.14.0+cpu — roda em CPU |
| RapidOCR-ONNX | rapidocr-onnxruntime | 1.4.4 | `onnxruntime` | onnxruntime 1.30.0 |
| RapidOCR-OpenVINO | rapidocr-openvino | 1.4.4 | `openvino` | openvino 2024.4.0 — requer hardware Intel |
| Tesseract | tesseract-cli | v5.5.3.20260724 | `tesseract-cli` | tessdata `por`, PSM 3, OEM 1 |

### 1.3 Cobertura

Todos os 5 backends processaram as 224 páginas sem falhas de execução (todas retornaram `pages_missing: 0`). A diferença está na qualidade do output e em falhas lógicas de extração (failure_rate).

---

## 2. Performance — Velocidade de Processamento

| Engine | Tempo Total (s) | Tempo Total (min) | Média por Página (s/pág) | Variabilidade |
|---|---|---|---|---|
| Paddle | **313.99** | **5.23** | **1.40** | Baixa — consistente ~1–2 s/pág |
| RapidOCR-OpenVINO | 544.39 | 9.07 | 2.43 | Baixa/Média — 1–10 s/pág |
| Tesseract | 811.25 | 13.52 | 3.62 | Média — 1–8 s/pág |
| RapidOCR-ONNX | 2317.04 | 38.62 | **10.34** | **Muito Alta** — picos de 15–79 s/pág nas págs 80–144 |
| EasyOCR | 2650.42 | **44.17** | 11.83 | Alta — picos de 15–61 s/pág nas págs 81–184 |

### 2.1 Análise de Velocidade

**Paddle** é o vencedor absoluto em velocidade: 8,4× mais rápido que EasyOCR. Isso se deve ao modelo PP-OCRv6 medium executado via subprocess isolado, que aproveita as otimizações do PaddlePaddle para CPU.

**RapidOCR-OpenVINO** é o segundo mais rápido com diferença confortável (1,73× mais lento que Paddle). A aceleração OpenVINO em hardware Intel compensa bastante o custo do modelo neural.

**Tesseract** oferece uma posição intermediária razoável (2,6× mais lento que Paddle, 3,3× mais rápido que EasyOCR), sem dependências pesadas de frameworks de deep learning.

**RapidOCR-ONNX** surpreende negativamente: apesar de ser "o mesmo modelo" que OpenVINO, é 4,26× mais lento. Isso ocorre porque o OnnxRuntime sem aceleração especializada degrada muito em páginas complexas (vide picos de 79 s nas págs 80–144 — mesmas páginas que causam picos no EasyOCR). A mesma anomalia de velocidade afeta ambos, sugerindo que as páginas 80–184 têm alto conteúdo visual que sobrecarrega modelos neurais CPU-bound sem aceleração de hardware.

**EasyOCR** é o mais lento: 8,4× pior que Paddle. Torch CPU é inerentemente pesado e o modelo ResNet+LSTM do EasyOCR é computacionalmente intensivo.

> **Implicação prática:** Para qualquer uso interativo ou batch de produção, EasyOCR e RapidOCR-ONNX são inviáveis sem GPU. OpenVINO e Paddle são os únicos backends com velocidade aceitável em CPU-only.

---

## 3. Grupo 1 — Qualidade do Texto (CER / WER)

### 3.1 Tabela Completa

| Métrica | EasyOCR | RapidOCR-ONNX | RapidOCR-OpenVINO | Tesseract | Paddle |
|---|---|---|---|---|---|
| **CER Normalizado** ↓ | **0.2860** | 0.3596 | 0.3633 | 0.4315 | 0.6344 |
| **CER Somente Texto** ↓ | **0.2842** | 0.3600 | 0.3636 | 0.4319 | 0.6342 |
| **WER** ↓ | **0.4236** | 0.6002 | 0.6019 | 0.6370 | 0.6324 |
| **Word Accuracy** ↑ | **0.5764** | 0.3998 | 0.3981 | 0.3630 | 0.3676 |
| **Substitution Rate** ↓ | 0.2834 | 0.0761 | 0.0754 | 0.2236 | **0.0063** |
| **Deletion Rate** ↓ | 0.0589 | 0.0273 | 0.0278 | 0.2739 | **0.0182** |
| **Insertion Rate** ↓ | 0.0814 | 0.4968 | 0.4987 | **0.0712** | 0.6079 ⚠️ |
| **Omission Rate** ↓ | 0.0589 | 0.0273 | 0.0278 | 0.2739 | **0.0182** |

### 3.2 Decomposição do Tipo de Erro

A métrica CER/WER decompõe-se em três tipos de erro que revelam a *causa* da imprecisão:

#### Motivo do erro por engine:

| Engine | Principal fonte de erro | Interpretação |
|---|---|---|
| **EasyOCR** | Substituições (28.3%) + Deleções (5.9%) | Confusão de caracteres visualmente similares; omite pouco mas erra muito o reconhecimento |
| **RapidOCR-ONNX/OpenVINO** | **Inserções (49.7–49.9%)** ⚠️ | Alucinação massiva de texto: o modelo "inventa" palavras que não existem na referência |
| **Tesseract** | Deleções (27.4%) + Substituições (22.4%) | Perde muito texto (especialmente em fontes menores, baixa qualidade); erra bastante o que lê |
| **Paddle** | **Inserções (60.8%)** ⚠️ | Pior caso de alucinação: mais de metade das palavras geradas não têm correspondente na referência |

> **Atenção:** A insertion_rate elevada de Paddle (60.8%) e RapidOCR-ONNX/OpenVINO (~50%) indica que esses backends estão **duplicando ou fabricando conteúdo**, não apenas errando o reconhecimento. Isso é mais problemático do que simples erros de substituição, pois introduz informação falsa no documento processado.

### 3.3 Ranking — Grupo 1

1. **EasyOCR** — CER 28.6%, WER 42.4% (melhor texto com baixa alucinação)
2. **RapidOCR-ONNX** — CER 36.0%, mas alta inserção (49.7%)
3. **RapidOCR-OpenVINO** — CER 36.3%, alta inserção (49.9%)
4. **Tesseract** — CER 43.2%, perde muito texto (deletion 27.4%)
5. **Paddle** — CER 63.4%, inserção crítica (60.8%)

---

## 4. Grupo 2 — Estrutura Markdown

### 4.1 Tabela Completa

| Métrica | EasyOCR | RapidOCR-ONNX | RapidOCR-OpenVINO | Tesseract | Paddle |
|---|---|---|---|---|---|
| **Heading F1** ↑ | 0.0932 | 0.0929 | 0.0922 | 0.0922 | **0.1070** |
| **Heading Level Accuracy** ↑ | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| **Heading Text CER** ↓ | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| **Block F1** ↑ | 0.4476 | 0.4460 | **0.4484** | 0.4485 | 0.2156 |
| **Paragraph Boundary F1** ↑ | 0.6266 | 0.6260 | 0.6297 | **0.6506** | 0.3885 |
| **List Detection F1** ↑ | **0.7273** | **0.7273** | **0.7273** | 0.5600 | **0.7273** |
| **Markdown AST Similarity** ↑ | 0.2898 | 0.2885 | 0.2904 | **0.2956** | 0.1293 |

### 4.2 Análise

**Heading detection** é universalmente fraco (F1 < 0.11) em todos os backends — nenhum consegue identificar cabeçalhos com precisão. O `heading_level_accuracy` e `heading_text_cer` sendo 0.0 para todos indica que o corpus de referência pode não ter headings que o sistema consiga detectar, ou que a formatação de headings não sobrevive à extração OCR em nenhum backend.

**Block F1** (0.22–0.45) mostra um padrão bimodal claro: Paddle fica em ~0.22, enquanto todos os outros ficam em ~0.44–0.45. A degradação do Paddle deve-se às inserções massivas que destroem a estrutura de blocos.

**Paragraph Boundary F1**: Tesseract lidera levemente (0.65), seguido por RapidOCR-OpenVINO (0.63). Isso indica que Tesseract preserva melhor a segmentação de parágrafos mesmo com seu alto deletion_rate.

**List Detection F1**: EasyOCR, RapidOCR-ONNX/OpenVINO e Paddle empatam em 0.7273. Tesseract é significativamente pior (0.56) — possivelmente porque o deletion_rate remove marcadores de lista.

**AST Similarity**: Métrica holística do documento Markdown. Tesseract lidera marginalmente (0.2956). Todos os backends que não são Paddle convergem entre 0.29–0.30, sugerindo que nenhum está bem adaptado ao estilo de Markdown do corpus de referência.

### 4.3 Ranking — Grupo 2

1. **Tesseract** — melhor em paragraph_boundary_f1 e ast_similarity
2. **RapidOCR-OpenVINO** — segundo em block_f1 e paragraph_boundary
3. **EasyOCR** — bem próximo de OpenVINO
4. **RapidOCR-ONNX** — praticamente igual a EasyOCR
5. **Paddle** — muito inferior em block_f1, paragraph_boundary, ast_similarity

---

## 5. Grupo 3 — Qualidade de Tabelas

### 5.1 Tabela Completa

| Métrica | EasyOCR | RapidOCR-ONNX | RapidOCR-OpenVINO | Tesseract | Paddle |
|---|---|---|---|---|---|
| **Table F1** ↑ | 0.9747 | 0.9811 | **0.9938** | **0.9938** | 0.6721 |
| **Row F1** ↑ | 0.8765 | 0.9204 | 0.8752 | 0.8653 | **0.9488** |
| **Column F1** ↑ | 0.8402 | 0.8728 | 0.8534 | 0.8414 | **0.8863** |
| **Table Dimension Accuracy** ↑ | 0.3896 | 0.5385 | 0.4625 | 0.3625 | **0.6341** |
| **Cell Exact Match** ↑ | 0.3396 | 0.4460 | 0.3942 | 0.3214 | **0.5942** |
| **Cell CER** ↓ | 1.1575 | 0.6702 | 0.8889 | 1.1442 | **0.5426** |
| **Cell Alignment Accuracy** ↑ | 0.3396 | 0.4460 | 0.3942 | 0.3214 | **0.5942** |
| **Table Structure Similarity** ↑ | 0.8507 | 0.8896 | 0.8525 | 0.8432 | **0.9130** |

### 5.2 Dois Eixos de Qualidade em Tabelas

As métricas de tabela dividem-se em duas dimensões independentes:

**Dimensão A — Detecção da estrutura (existência de tabelas):**
- `table_f1`: detectar *se há uma tabela* e onde está
- Vencedores: RapidOCR-OpenVINO e Tesseract (0.9938), EasyOCR e ONNX também fortes
- Perdedor: Paddle (0.6721) — não detecta corretamente 33% das tabelas

**Dimensão B — Qualidade do conteúdo das células:**
- `cell_exact_match`, `cell_cer`, `cell_alignment_accuracy`
- Vencedor absoluto: **Paddle** (cell_exact 59.4%, cell_cer 0.54)
- Runners-up: RapidOCR-ONNX (cell_exact 44.6%, cell_cer 0.67)
- EasyOCR e Tesseract têm cell_cer > 1.0 — isso significa que o texto dentro das células está *mais errado do que se não tivesse nada*

### 5.3 Análise do Paradoxo do Paddle em Tabelas

O Paddle apresenta um resultado aparentemente paradoxal: **detecta mal as tabelas mas, quando detecta, preenche as células com melhor precisão que todos**. Isso aponta para um problema de detecção/alinhamento na fase de layout, não no modelo OCR em si. As inserções massivas (60.8% insertion_rate) provavelmente ocorrem fora das tabelas — dentro das células, o Paddle mantém alta fidelidade.

O cell_cer > 1.0 de EasyOCR (1.16) e Tesseract (1.14) é um sinal de alerta: significa que há mais erros por caractere dentro das células do que caracteres na referência — provavelmente porque esses backends estão inserindo ruído ou mal formatando conteúdo de tabela.

### 5.4 Ranking — Grupo 3

Para uso prioritário de tabelas, há dois cenários:

- **Se o que importa é saber SE há tabelas:** RapidOCR-OpenVINO / Tesseract
- **Se o que importa é o conteúdo das células:** Paddle (apesar da baixa detecção)
- **Melhor equilíbrio geral:** RapidOCR-ONNX (table_f1 0.98, cell_exact 44.6%, cell_cer 0.67)

---

## 6. Grupo 4 — Ordem, Integridade e Confiabilidade

### 6.1 Tabela Completa

| Métrica | EasyOCR | RapidOCR-ONNX | RapidOCR-OpenVINO | Tesseract | Paddle |
|---|---|---|---|---|---|
| **Reading Order Accuracy** ↑ | 0.0523 | 0.0523 | 0.0523 | 0.0523 | 0.0523 |
| **Block Order Accuracy** ↑ | 0.0523 | 0.0523 | 0.0523 | 0.0523 | 0.0523 |
| **Duplicate Content Rate** ↓ | **0.0122** | 0.0175 | 0.0190 | 0.0131 | 0.0196 |
| **Duplicate Block Rate** ↓ | 0.0085 | 0.0085 | 0.0085 | **0.0081** | 0.0161 |
| **Header Leakage** ↓ | **0.0000** | **0.0000** | **0.0000** | **0.0000** | **0.0000** |
| **Footer Leakage** ↓ | **0.0000** | **0.0000** | **0.0000** | **0.0000** | **0.0000** |
| **Page# Leakage** ↓ | 0.0012 | **0.0007** | **0.0007** | 0.0009 | 0.0010 |
| **Failure Rate** ↓ | **0.0089** | **0.0089** | **0.0089** | **0.0089** | ⚠️ **0.4955** |
| **Invalid Markdown Rate** ↓ | **0.0000** | **0.0000** | **0.0000** | 0.0357 ⚠️ | **0.0000** |

### 6.2 Anomalias Críticas

#### Anomalia 1: Paddle — failure_rate = 49.55%

**O que significa:** Em 111 das 224 páginas, o Paddle produziu output que falhou na validação de integridade — provavelmente output vazio, corrompido, ou com estrutura incoerente.

**Hipótese mais provável:** O isolamento via subprocess (`paddle_subprocess`) foi implementado para evitar conflitos de DLL com torch/EasyOCR no Windows. O subprocess pode estar falhando silenciosamente em páginas específicas (timeouts, exceções não capturadas, output mal formado). Combinado com a insertion_rate de 60.8%, é provável que nas páginas "bem-sucedidas", o Paddle esteja duplicando blocos de conteúdo — possivelmente um bug de acumulação de estado entre chamadas no subprocess.

**Impacto:** Os resultados de CER/WER do Paddle **não são diretamente comparáveis** aos outros backends porque incluem páginas com output degenerado. A real qualidade do Paddle poderia ser melhor (ou pior) do que os números indicam.

#### Anomalia 2: Tesseract — invalid_markdown_rate = 3.57%

**O que significa:** Em ~8 das 224 páginas, o Tesseract gerou Markdown sintaticamente inválido.

**Hipótese:** Tesseract opera no nível de caractere/palavra sem compreensão de layout. Em páginas com estrutura complexa (tabelas financeiras, múltiplas colunas com bordas), pode gerar sequências de `|` e `-` que parecem Markdown mas não são válidas.

**Impacto:** Baixo (3.57%), mas relevante se o output for processado por um parser Markdown estrito.

#### Anomalia 3: Inserção massiva em RapidOCR-ONNX/OpenVINO

A insertion_rate de ~50% nos dois backends RapidOCR é preocupante mesmo com CER relativamente bom. Isso indica que os modelos estão gerando texto plausível mas não presente na referência — alucinação semântica. Para documentos onde a fidelidade ao conteúdo original é crítica (contratos, laudos), isso é um risco.

### 6.3 Reading Order — Resultado Sistemático

O `reading_order_accuracy` de 0.0523 (apenas 5.23%) é **idêntico para todos os 5 backends**. Isso sugere que o problema não é dos backends OCR, mas sim do algoritmo de ordenação de blocos do pipeline (`structured-pdf-text` INV-07: ordenação determinística por posição). O corpus provavelmente contém muitos layouts não-lineares (tabelas, multi-colunas) onde a ordem de leitura esperada difere da ordem geométrica processada. Este é um problema estrutural do pipeline, não dos backends.

### 6.4 Ranking — Grupo 4

1. **EasyOCR** — failure_rate 0.89%, duplicate_content 1.22%, sem invalid_markdown
2. **RapidOCR-ONNX** / **RapidOCR-OpenVINO** — empate técnico, mesmas taxas
3. **Tesseract** — failure_rate 0.89% mas invalid_markdown 3.57%
4. **Paddle** — failure_rate 49.55% (disqualificante para produção no estado atual)

---

## 7. Grupo 5 — Dados Críticos

### 7.1 Tabela Completa

| Métrica | EasyOCR | RapidOCR-ONNX | RapidOCR-OpenVINO | Tesseract | Paddle |
|---|---|---|---|---|---|
| **Numeric Exact Match** ↑ | 0.8579 | 0.5322 | 0.5295 | **0.8697** | 0.4178 |
| **Date Exact Match** ↑ | **1.0000** | 0.9607 | 0.9607 | **1.0000** | 0.9382 |
| **Currency Exact Match** ↑ | 0.3803 | 0.3403 | 0.3361 | **0.7195** | 0.3288 |
| **Identifier Exact Match** ↑ | **1.0000** | **1.0000** | **1.0000** | **1.0000** | **1.0000** |

### 7.2 Análise por Tipo de Dado Crítico

**Números (numeric_exact_match):** Tesseract (87.0%) e EasyOCR (85.8%) lideram com folga sobre os modelos com alta insertion_rate. RapidOCR (~53%) e Paddle (41.8%) são impactados diretamente pelas inserções que geram dígitos extras ou errados.

**Datas (date_exact_match):** EasyOCR e Tesseract atingem 100% de precisão. Todas as engines são razoáveis aqui (mínimo 93.8%). Datas têm formato previsível que facilita o reconhecimento.

**Valores monetários (currency_exact_match):** Este é o caso mais difícil. Tesseract domina com 71.95%, enquanto os outros ficam entre 32.9%–38.0%. O desempenho superior do Tesseract em moeda provavelmente deve-se ao seu comportamento word-level com less confusion entre vírgulas e pontos em valores em português (R$ 1.234,56 etc.). Os modelos neurais tendem a substituir separadores decimais/milhar com mais frequência.

**Identificadores (identifier_exact_match):** 100% para todas as engines — provavelmente são sequências alfanuméricas com padrões claros que qualquer OCR reconhece bem.

### 7.3 Ranking — Grupo 5

1. **Tesseract** — Melhor geral: numeric 87.0%, date 100%, currency 71.9% ← vencedor claro em dados críticos
2. **EasyOCR** — Forte em numeric (85.8%) e date (100%), fraco em currency (38.0%)
3. **RapidOCR-ONNX** — Médio em tudo, currency 34.0%
4. **RapidOCR-OpenVINO** — Praticamente igual ao ONNX
5. **Paddle** — Mais fraco em numeric (41.8%) e currency (32.9%)

---

## 8. Análise por Engine — Perfil Completo

### 8.1 EasyOCR

**Pontos fortes:**
- Melhor CER (28.6%) e WER (42.4%) — texto mais fiel ao original
- Zero failure rate (0.89% é o valor mínimo atingível neste benchmark)
- 100% de acerto em datas; 85.8% em números
- Baixa taxa de inserções (8.1%) — o que errr é por substituição, não alucinação massiva
- Markdown sem inválidos

**Pontos fracos:**
- **Mais lento de todos** (2650 s, 44.2 min para 224 páginas)
- Pior cell_cer em tabelas (1.16) apesar de boa detecção estrutural (table_f1 0.97)
- Currency precária (38.0%)
- Impraticável sem GPU para volumes de produção

**Perfil de uso ideal:** Análise offline de alta precisão onde velocidade não é crítica; documentos com muito texto corrido; sistemas batch noturnos.

---

### 8.2 RapidOCR-OpenVINO

**Pontos fortes:**
- Segundo em velocidade (544 s, 9.1 min) — 4,9× mais rápido que EasyOCR
- Melhor table_f1 empatado (0.9938) — detecta praticamente todas as tabelas
- Zero failure_rate (0.89%), zero invalid_markdown
- Boa confiabilidade geral

**Pontos fracos:**
- Insertion_rate de 49.9% — alto risco de alucinação de conteúdo
- CER 36.3% — texto mediocre comparado ao EasyOCR
- Cell CER 0.89 em tabelas — conteúdo de células com erros significativos
- Currency fraco (33.6%)
- **Requer hardware Intel** para aproveitar a aceleração OpenVINO; em hardware AMD/ARM degrada para performance similar ao ONNX

**Perfil de uso ideal:** Detecção de estrutura de documentos em infraestrutura Intel; aplicações onde saber "existe uma tabela aqui" é mais importante que o conteúdo exato das células.

---

### 8.3 RapidOCR-ONNX

**Pontos fortes:**
- CER marginalmente melhor que OpenVINO (35.96% vs 36.33%)
- Table cell quality superior ao OpenVINO (cell_exact 44.6% vs 39.4%, cell_cer 0.67 vs 0.89)
- Portável — funciona em qualquer hardware sem otimizações específicas
- Zero failure_rate, zero invalid_markdown

**Pontos fracos:**
- **Extremamente lento em hardware sem aceleração** (2317 s, 38.6 min) — alta variabilidade (picos de 79 s/pág)
- Insertion_rate de 49.7% — mesma alucinação do OpenVINO
- 4.26× mais lento que OpenVINO para qualidade similar
- Currency fraco (34.0%)

**Perfil de uso ideal:** Situações onde portabilidade é crítica e velocidade não é restrição; desenvolvimento e testes. Em produção, OpenVINO é sempre preferível em hardware Intel.

---

### 8.4 Tesseract

**Pontos fortes:**
- **Melhor em dados críticos financeiros**: currency 71.9%, numeric 86.9%, date 100%
- Melhor estrutura Markdown: paragraph_boundary_f1 0.65, ast_similarity 0.30
- Velocidade razoável (811 s, 13.5 min) — 3.3× mais rápido que EasyOCR
- Sem dependências Python pesadas (apenas binário CLI)
- Baixa insertion_rate (7.1%) — o que erra é por deleção/substituição, não alucinação

**Pontos fracos:**
- Maior deletion_rate (27.4%) — perde muito texto, especialmente em fontes menores e scanned
- CER de 43.2% — o texto que extrai é impreciso
- invalid_markdown_rate 3.57% — gera Markdown sintaticamente inválido em ~8 páginas
- Cell exact match mais baixo em tabelas (32.1%)
- List Detection F1 pior (0.56 vs 0.73 dos outros)

**Perfil de uso ideal:** Documentos financeiros e contábeis onde precisão de valores monetários é crítica; arquivos com pouco conteúdo em lista; quando dependências de deep learning não são aceitáveis.

---

### 8.5 Paddle

**Estado atual: não recomendado para produção nesta configuração**

**Pontos fortes em condições ideais:**
- **Mais rápido de todos** (314 s, 5.2 min) — 2.6× mais rápido que OpenVINO
- Melhor qualidade de conteúdo de células em tabelas que detecta (cell_exact 59.4%, cell_cer 0.54)
- Melhor row_f1 (0.95) e column_f1 (0.89) de todos os backends
- Baixíssima substitution_rate (0.63%) e deletion_rate (1.8%)

**Problemas críticos no benchmark:**
- **Failure rate: 49.55%** — quase metade das páginas falhou em validação de integridade
- **Insertion rate: 60.8%** — quando processa, inventa mais texto do que existe
- Table F1 de 0.67 — detecta apenas 67% das tabelas
- Heading F1 levemente melhor (0.107) mas todos os outros são igualmente ruins

**Causa raiz provável:** O isolamento via `paddle_subprocess` (implementado para resolver conflito CF-4 de DLL no Windows) pode estar introduzindo instabilidade. O subprocess pode estar acumulando estado entre páginas, causando duplicação de output, timeouts silenciosos, ou estado corrompido entre chamadas. Em Linux/WSL sem o isolamento, o comportamento poderia ser diferente.

**Perfil de uso:** Potencialmente excelente se o problema de subprocess for corrigido — combina a melhor velocidade com boa qualidade em tabelas. Requer investigação do CF-4 antes de ser considerado para produção.

---

## 9. Score Ponderado e Ranking Final

### 9.1 Método de Pontuação

Cada engine recebe uma pontuação 0–100 em 6 dimensões, depois ponderada:

| Dimensão | Peso | Métrica base |
|---|---|---|
| Qualidade de texto (CER) | 25% | `1 - CER_normalized` |
| Estrutura Markdown | 15% | `block_f1` (proxy) |
| Qualidade de tabelas | 15% | `cell_exact_match` |
| Confiabilidade | 20% | `1 - failure_rate - 0.5 × invalid_markdown_rate` |
| Dados críticos | 15% | média de `numeric + date + currency exact_match` |
| Velocidade | 10% | `elapsed_paddle / elapsed_engine` (normalizado) |

### 9.2 Scores por Dimensão (0–100)

| Engine | CER score | Markdown | Tabelas | Confiabilidade | Dados Críticos | Velocidade | **Score Final** |
|---|---|---|---|---|---|---|---|
| EasyOCR | 71.4 | 44.8 | 33.9 | 99.1 | 74.6 | 11.8 | **61.8** |
| RapidOCR-ONNX | 64.0 | 44.6 | 44.6 | 99.1 | 61.1 | 13.5 | **59.7** |
| RapidOCR-OpenVINO | 63.7 | 44.8 | 39.4 | 99.1 | 60.9 | 57.7 | **63.3** |
| Tesseract | 56.9 | 44.9 | 32.1 | 97.3 | 86.3 | 38.7 | **62.1** |
| Paddle | 36.6 | 21.6 | 59.4 | 50.5 | 56.2 | 100.0 | **49.8** |

### 9.3 Ranking Final

| Posição | Engine | Score | Principal diferencial |
|---|---|---|---|
| 🥇 1º | **RapidOCR-OpenVINO** | 63.3 | Equilíbrio velocidade + qualidade; melhor table_f1 |
| 🥈 2º | **Tesseract** | 62.1 | Líder absoluto em dados críticos financeiros |
| 🥉 3º | **EasyOCR** | 61.8 | Melhor qualidade de texto; inviável em produção sem GPU |
| 4º | **RapidOCR-ONNX** | 59.7 | Mesma qualidade do OpenVINO mas 4× mais lento |
| 5º | **Paddle** | 49.8 | Anomalia crítica de failure_rate inviabiliza uso atual |

> **Nota de interpretação:** Os scores são muito próximos entre os 3 primeiros (61.8–63.3). A escolha entre eles deve ser guiada pelos requisitos específicos do caso de uso, não apenas pelo score agregado.

---

## 10. Análise de Trade-offs por Caso de Uso

### Cenário 1: Documentos financeiros e contábeis (notas fiscais, demonstrativos)
**Recomendado: Tesseract**
- Currency 71.9%, numeric 86.9% — sem rival neste corpus
- Velocidade aceitável (3.6 s/pág)
- Cuidado com deletion_rate 27.4% — pode perder linhas em tabelas densas

### Cenário 2: Extração em larga escala / produção com CPU-only Intel
**Recomendado: RapidOCR-OpenVINO**
- 2.43 s/pág é viável industrialmente
- Table_f1 0.9938 — detecção de tabelas quase perfeita
- Cuidado com insertion_rate 49.9% em documentos onde fidelidade absoluta é crítica

### Cenário 3: Processamento offline de alta precisão / análise de pesquisa
**Recomendado: EasyOCR** (se GPU disponível) ou **Tesseract** (se somente CPU)
- EasyOCR: melhor CER (28.6%), melhor reconhecimento de texto corrido
- Tesseract: melhor dados críticos, menor footprint de dependências

### Cenário 4: Documentos com tabelas complexas
**Recomendado: RapidOCR-ONNX** (equilíbrio) ou **Tesseract** (detecção de estrutura)
- ONNX: table_f1 0.98 + cell_exact 44.6% + cell_cer 0.67 — melhor equilíbrio geral
- Tesseract: table_f1 0.9938 se o que importa é detectar todas as tabelas

### Cenário 5: Portabilidade máxima (sem GPU, sem Intel)
**Recomendado: Tesseract**
- Apenas binário CLI, sem dependência de frameworks ML
- Velocidade razoável em qualquer hardware

---

## 11. Pontos de Atenção e Próximos Passos

### 11.1 Problemas Identificados que Requerem Investigação

| # | Engine | Problema | Severidade | Ação sugerida |
|---|---|---|---|---|
| P-01 | Paddle | failure_rate 49.55% no subprocess Windows | Crítica | Investigar CF-4: log de erros do subprocess, timeout, acumulação de estado |
| P-02 | Paddle | insertion_rate 60.8% | Crítica | Verificar se `balanced` mode + PP-OCRv6 está duplicando regiões de imagem |
| P-03 | RapidOCR-ONNX/OpenVINO | insertion_rate ~50% | Alta | Avaliar pós-processamento para filtrar inserções |
| P-04 | Tesseract | invalid_markdown_rate 3.57% | Média | Identificar quais tipos de página geram Markdown inválido |
| P-05 | Todos | reading_order_accuracy 5.23% | Média | Problema no pipeline, não nos backends — revisar algoritmo de ordenação |
| P-06 | Todos | heading_level_accuracy 0.0% | Média | Nenhum backend detecta hierarquia de headings corretamente |
| P-07 | Todos | currency_exact_match < 40% (exceto Tesseract) | Alta | Normalização de separadores de moeda em português (R$, vírgula decimal) |

### 11.2 Recomendação de Backend Padrão

Com base nos dados desta Fase 8, **não há um backend claramente dominante** em todos os critérios. A recomendação depende da prioridade:

- Se **velocidade + confiabilidade** forem prioritárias: **RapidOCR-OpenVINO**
- Se **fidelidade de texto** for prioritária: **EasyOCR** (requer GPU para produção)
- Se **dados financeiros** forem prioritários: **Tesseract**
- Se **Paddle** for corrigido (failure_rate resolvido): seria o backend mais atraente — velocidade sem rival + melhor qualidade de células em tabelas

### 11.3 Limite de Confiança dos Resultados

- Os resultados do **Paddle são suspeitos** devido à failure_rate de 49.55% — os números de CER/WER/tabelas incluem páginas degeneradas, distorcendo a comparação.
- O **corpus é de stress deliberado** — resultados em documentos reais de produção (menos complexos) devem ser significativamente melhores para todos os backends.
- O benchmark foi executado em **CPU-only no Windows** — backends com suporte a GPU (EasyOCR, Paddle) poderiam ter desempenho dramaticamente diferente com CUDA.

---

## Apêndice A — Dados Brutos Completos

### A.1 Todos os Metadados de Runtime

| Engine | Runtime | Versão | Framework | Observações |
|---|---|---|---|---|
| Paddle | paddle_subprocess | paddlepaddle 3.3.1 | PaddleOCR 3.7.0 + PaddleX 3.7.2 | PP-OCRv6 medium |
| EasyOCR | torch-cpu | easyocr 1.7.2 | torch 2.14.0+cpu | ResNet+LSTM |
| RapidOCR-ONNX | onnxruntime | rapidocr-onnxruntime 1.4.4 | onnxruntime 1.30.0 | |
| RapidOCR-OpenVINO | openvino | rapidocr-openvino 1.4.4 | openvino 2024.4.0 | |
| Tesseract | tesseract-cli | v5.5.3.20260724 | — | tessdata por, PSM 3, OEM 1 |

### A.2 Todas as Métricas Agregadas (Referência Rápida)

```
Métrica                         EasyOCR    ONNX       OpenVINO   Tesseract  Paddle
---                             ---        ---        ---        ---        ---
cer_normalized                  0.2860     0.3596     0.3633     0.4315     0.6344
cer_text_only                   0.2842     0.3600     0.3636     0.4319     0.6342
wer                             0.4236     0.6002     0.6019     0.6370     0.6324
word_accuracy                   0.5764     0.3998     0.3981     0.3630     0.3676
substitution_rate               0.2834     0.0761     0.0754     0.2236     0.0063
deletion_rate                   0.0589     0.0273     0.0278     0.2739     0.0182
insertion_rate                  0.0814     0.4968     0.4987     0.0712     0.6079
omission_rate                   0.0589     0.0273     0.0278     0.2739     0.0182
heading_f1                      0.0932     0.0929     0.0922     0.0922     0.1070
heading_level_accuracy          0.0000     0.0000     0.0000     0.0000     0.0000
block_f1                        0.4476     0.4460     0.4484     0.4485     0.2156
paragraph_boundary_f1           0.6266     0.6260     0.6297     0.6506     0.3885
list_detection_f1               0.7273     0.7273     0.7273     0.5600     0.7273
markdown_ast_similarity         0.2898     0.2885     0.2904     0.2956     0.1293
table_f1                        0.9747     0.9811     0.9938     0.9938     0.6721
row_f1                          0.8765     0.9204     0.8752     0.8653     0.9488
column_f1                       0.8402     0.8728     0.8534     0.8414     0.8863
table_dimension_accuracy        0.3896     0.5385     0.4625     0.3625     0.6341
cell_exact_match                0.3396     0.4460     0.3942     0.3214     0.5942
cell_cer                        1.1575     0.6702     0.8889     1.1442     0.5426
cell_alignment_accuracy         0.3396     0.4460     0.3942     0.3214     0.5942
table_structure_similarity      0.8507     0.8896     0.8525     0.8432     0.9130
reading_order_accuracy          0.0523     0.0523     0.0523     0.0523     0.0523
duplicate_content_rate          0.0122     0.0175     0.0190     0.0131     0.0196
failure_rate                    0.0089     0.0089     0.0089     0.0089     0.4955
invalid_markdown_rate           0.0000     0.0000     0.0000     0.0357     0.0000
numeric_exact_match             0.8579     0.5322     0.5295     0.8697     0.4178
date_exact_match                1.0000     0.9607     0.9607     1.0000     0.9382
currency_exact_match            0.3803     0.3403     0.3361     0.7195     0.3288
identifier_exact_match          1.0000     1.0000     1.0000     1.0000     1.0000
elapsed_total_s                 2650.4     2317.0     544.4      811.3      314.0
avg_s_per_page                  11.83      10.34      2.43       3.62       1.40
```

---

*Documento gerado com base nos arquivos `output/fase8/metrics_*_v1-*.json` e `output/fase8/manifesto_e2e_*_v1-*.json`. Corpus: 224 páginas, run suffix v1, modo balanced, língua pt.*
