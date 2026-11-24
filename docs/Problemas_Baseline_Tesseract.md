# Problemas da Baseline Tesseract — Registro Técnico

> **Escopo**: Série 3 (executor `scripts/run_tesseract_batch_wsl.py`, diretórios `output/tesseract/NNN`).
> A versão anterior deste documento (Série 2, runs 001–008 via `run_loop.py`) está preservada no git (`git show 5cc81d6:docs/Problemas_Baseline_Tesseract.md`); um resumo está em §7.
> **Protocolo**: atualizar após cada rodada. Um problema só é marcado como RESOLVIDO quando o critério mensurável dele for atendido numa rodada completa, sem regressão relevante nos demais corpora.
> **Classificação usada a partir da 003**: RESOLVIDO (critério atendido em rodada completa) · CORRIGIDO — VALIDADO PONTUALMENTE (testes por página, aguarda rodada completa) · PARCIAL · PENDENTE · INFO.

---

## Quadro principal de problemas

Resumo de todos os problemas (abertos e resolvidos). Ordenado por severidade (CRÍTICO > ALTO > MÉDIO > BAIXO > INFO); dentro de cada severidade, os abertos vêm primeiro, por prioridade. Diagnósticos, tentativas e histórico completos em §2.

- **Severidade**: perda de conteúdo ou corrupção de dados > leitura/estrutura > problemas menores. **Prioridade**: ordem de trabalho (P0 primeiro); independe da severidade.
- **Tentativas**: usadas / limite. O limite de 5 por problema vale desde o ciclo pós-006; "—" indica correção anterior a essa regra.
- **Status**: "confirmado na RUN NNN" = validado em rodada completa; "**validado pontualmente**" = extração só de páginas selecionadas, **sem** confirmação integral (números marcados "Pontual" vêm de subconjuntos e não são comparáveis às métricas documentais).
- **Última RUN**: última rodada completa em que o problema foi observado ou confirmado. Situação após a auditoria da RUN 013 (§1.32–§1.35, 2026-11-24; antes: RUN 012, §1.28–§1.30). **Prioridade** dos abertos = recomendação; a escolha do próximo problema é do responsável pelo projeto.

| ID | Descrição | Severidade | Prioridade | Documento / páginas | Impacto mensurado | Tentativas | Status | Última RUN | Próxima ação |
|---|---|---|---|---|---|---|---|---|---|
| T3-P44 | Perda silenciosa de texto: releitura endireitada mais completa descartada pela confiança média | CRÍTICO | **P0** (próximo ciclo; exige autorização) | V11 p71 (demonstrado); outras páginas inclinadas com releitura (1°–10°) possivelmente afetadas, sem medição | **Captura (§1.36)**: leitura direta 109 palavras / 638 caracteres / conf. 90.11 / cobertura 0.33; releitura endireitada 295 / 1727 / 86.45 / **0.95**, descartada porque a média caiu 3.7 > 1.0. Nas 100 palavras comuns, a releitura tem conf. **maior** (90.88) | 0 / 5 | **Causa localizada** (diagnóstico pós-013, 1 página, 1 execução): `ocr/backends/tesseract.py::_deskew_page`; nada se perde depois do TSV escolhido; sem aviso | 013 | Correção separada proposta na §1.36 (seleção por evidência comparável + observabilidade) |
| T3-P36 | Tabela OCR suprimida silenciosamente pelo pós-processamento Tesseract (regra de "tabela girada ilegível") | CRÍTICO | — | V4 p167, p168 | **010**: p167 CER 0.901 → 0.194, recall 0.095 → 0.548, tabela 7 × 3 detectada; p168 CER 0.853 → 0.531, recall 0.071 → 0.476; aviso `suspected_ocr_garble` nas duas; V4 sem páginas com perda de volume (2 → 0); nenhuma outra página alterada | 1 / 5 | **Corrigido — confirmado na RUN 010** (p167/p168). Residual: coluna "Idioma" da p168 não lida pelo Tesseract, sem aviso; supressões anteriores ao pós-processamento continuam sem aviso | 010 | — (monitorar) |
| T3-P00 | Executor sem tabelas e sem supressão de cabeçalho/rodapé | CRÍTICO | — | V3–V9: todas as páginas com tabela ou cabeçalho/rodapé repetido (ex.: V7 p2) | `table_detection_f1` 0.000 → 1.000 em V5–V8; V7 `cer_markdown_raw` 0.109 → 0.009 | 1 / — | Resolvido — confirmado na RUN 002 | 002 | — |
| T3-P01 | OCR de página descartado por figuras | CRÍTICO | — | V9: 47 páginas mistas e p10, p16, p33, p61, p87 | V9 recall 0.664 → 0.948 | 1 / — | Resolvido — confirmado na RUN 003 | 003 | — |
| T3-P10 | Linhas OCR fundidas entre colunas | CRÍTICO | — | V4 OCR de duas colunas (21 págs., p82–p142); V9 20 págs. multicoluna; V10 L01–L06 (35 págs.) e controles p41, p50 | CER multicoluna V4 0.743 → 0.178; V9 0.518 → 0.142 | 1 / — | Resolvido — confirmado na RUN 004 | 004 | — |
| T3-P23 | Parser TSV engolia linhas após palavra iniciada por aspas | CRÍTICO | — | V3 p96, p97; V4 p107, p150 | V4 recall 0.832 → 0.877 | 1 / — | Resolvido — confirmado na RUN 004 | 004 | — |
| T3-P27 | Página nativa inteira descartada como marcador do corpus | CRÍTICO | — | V4 p213, p214 | Recall das páginas 0.000 → 1.000 | 1 / — | Resolvido — confirmado na RUN 005 | 005 | — |
| T3-P43 | Cabeçalho/rodapé corrente com contador de página sem o rótulo "Página" não é suprimido | ALTO | — (aguarda RUN 014) | V11: 181 linhas de mobília em 94 págs.; V10: 65 págs. ("Documento sintético … \| Folha NN/84") | **Offline (capturas completas, §1.37)**: V11 65.9 → **70.4** (contrafactual 71.0), linhas de mobília 181 → 18, `numeric` fp 250 → 100, recall **idêntico** (0.967214); V10 75.5 → **78.3** (contrafactual 78.9), 65 → 12 (as 12 inclinadas L01/L02), fp 135 → 31; **0 linhas do gabarito removidas** nos 9 corpora; controles V3–V9 conforme §1.37 | 1 / 5 | **Corrigido — validado offline sobre capturas completas** (não commitado); "Continuação…" do V11 preservada | 013 | RUN 014 (§5) |
| T3-P46 | Texto nativo em colunas e lista com marcador detectados como tabela sem borda | ALTO | P1 | V11 p5 (3 colunas), p98 (4 colunas), p86 (lista "•"); tabelas falsas em p3, p7, p102 | **013**: p98 CER 0.763 e p5 0.697 com recall 1.0 (linhas das colunas intercaladas em células); p5 + p98 = 12.8% das edições do V11 | 0 / 5 | **Novo (auditoria da RUN 013)** | 013 | 2º candidato estrutural; exige autorização (detector nativo) |
| T3-P45 | Tabela nativa emitida duas vezes, com o título absorvido em células | ALTO | P1 | V11 p50, p93 | **013**: tabela inteira duplicada; título partido ("visibili \| dade") e perdido como título; 1 `currency` fp | 0 / 5 | **Novo (auditoria da RUN 013)** — hipótese: candidatos sobrepostos sem deduplicação | 013 | Diagnóstico por etapa (nativo, sem OCR) |
| T3-P47 | Tabelas nativas sem borda empilhadas ou de formulário desmontadas | ALTO | P2 | V11 p57, p9, p37, p41, p54, p110, p115, p120 (não detectadas); p45, p60, p63 (parciais) | **013**: p57 com 3 tabelas → só o cabeçalho em tabela, 32 células soltas coluna a coluna; V11 `table_detection_recall` 0.759 | 0 / 5 | **Novo (auditoria da RUN 013)**; família de T3-P37 | 013 | Junto com T3-P37 (exige autorização) |
| T3-P39 | Valores corrompidos por duas leituras do mesmo ponto (caixa contida) | ALTO | — | V9 p58, p66; também V6 p11, V8 p49, p67 | **010**: V9 p58 com `R$ 2.333,52`, `R$ 845,26`, `R$ 1.036,35` corretos, `currency` 0.857 → 1.000, CER 0.063 → 0.046; V8 p67 `02/10/20262026` → `02/10/2026` (`numeric` 0.824 → 1.000); V9 `currency` 0.962 → 0.970; nenhuma página perdeu acertos de dados críticos | 1 / 5 | **Corrigido — confirmado na RUN 010** | 010 | — |
| T3-P35 | Ordem de blocos em páginas raster multicoluna (calhas OCR fragmentadas) | ALTO | P0 (páginas inclinadas) | V10 L01/L02 inclinadas (12 págs.: p5, 13, 18, 25, 31, 45, 48, 53, 62, 68, 75, 83); resíduo V10 p32 | 011 (= 009): L01/L02 = **56% das edições do V10** (CER 0.157–0.378, recall ≥ 0.909); L04–L06 0.104 mantido; p32 0.311. **012**: inclinadas sem mudança de ordem (CER médio 0.297; p5/p31/p62 só com conteúdo recuperado, T3-P42); raster niveladas 0.085 → 0.082 | 2 / 5 | Corrigido nas páginas sem inclinação — confirmado na 008 e **mantido até a 012**; inclinadas pendentes. **Diagnóstico pós-011 (§1.26)**: caixas infladas em 10.5–12.5 pt; geometria endireitada recuperável sem OCR; abordagem nova fundamentada, não implementada | 012 | **1º recomendado (§1.30)**: Nível 1 da hipótese H1 (§1.26); exige autorização para `api.py` |
| T3-P37 | Duas tabelas nativas lado a lado: fundidas ou desmontadas | ALTO | P1 | V9 p62, p72 (desmontadas: valores separados das linhas); p14 (duas tabelas fundidas numa só, linhas corretas) | p62 CER 0.465, células exatas 0.194; p72 0.301 / 0.000; p14 0.161 / 0.400 (`table_column_count_accuracy` 0) | 0 / 5 | **Novo** (p72 constava como resíduo de T3-P22); sem mudança até a 011 | 011 | 3º na §1.24; exige autorização (detector de tabelas nativo, compartilhado) |
| T3-P34 | Tabela raster com inclinação pequena não detectada | ALTO | P1 | Corrigida: V9 p58. Pendentes: p66, p78, p25 (2ª tabela), p8 (1ª tabela) | **009**: p58 CER 0.157 → 0.063, células exatas 0 → 0.646; p44 0.366 → 0.204; p49 0.112 → 0.092; V9 células exatas 0.582 → 0.601. p66 recall 0.789, `currency` 0.632 (valores truncados) | 4 / 5 | Parcial — V9 p58/p44/p49 **confirmadas na RUN 009** e mantidas até a 011; demais com causas distintas | 011 | Última tentativa só com hipótese única para a p66 (reler a tabela no raster endireitado) |
| T3-P06 | Títulos não detectados ou com nível errado (concentrado nas regiões OCR) | ALTO | **P1** (resíduos, depois de T3-P35) | Resíduos: hierarquia OCR (V10, V9); **V11 (013): 22 de 30 títulos raster ausentes, falsos títulos de lixo de OCR (p75)**; falsos títulos V4 p95, p104, p110, p118, p152, p157, p160, p168, p176, p184; V9 p76, p84; V10 p11, p24, p40, p46, p56, p66, p78, p84; regressões V4 p95, p96, p145, p149, p104 | **012** (`18ccfb0`): `heading_recall` V4 0.335 → **0.562**, V9 0.351 → **0.457**, V10 0.298 → **0.634**; precisão V4 1.000 → **0.929**, V9 0.541 → 0.575, V10 0.772 → 0.835; F1 V4 0.502 → 0.700, V10 0.430 → 0.721; `heading_level_match_rate` V10 0.977 → **0.519**; 154 títulos verdadeiros novos e 28 falsos reais; nativas idênticas; Títulos V4 37.5 → 52.3, V10 48.9 → 62.9 | 3 / 5 | **Parcial — confirmado na RUN 012** (§1.28): recall e caminho nativo confirmados; **não atende** precisão do V4 (< 0.95), "nenhuma categoria nova de falso título" e CER da V4 p104; hierarquia OCR documental; não reverter | 012 | t4 depois de T3-P35 (§1.30): níveis OCR por página, regressões p95/p96/p145/p149; exige nova autorização para `layout/heading.py` |
| T3-P22 | Ordem de leitura em páginas nativas multicoluna | ALTO | P2 (encerrado) | Restantes: V9 p51, p43, p5, p12, p19 (p72 passa a T3-P37). Corrigidas: V10 p9, p54, p59; V4 p20, p25, p26, p28; V9 p90 | 009: corrigidas mantidas (V4 ≤ 0.010; V10 p9 0.088, p54 0.095, p59 0.069). Restantes: V9 p51 0.552 (11.2% das edições do V9), p43 0.470 (7.1%), p5 0.317 (5.4%) | 5 / 5 (encerrado) | Parcialmente corrigido — **mantido na RUN 009** | 009 | Casos restantes exigem novo problema; V9 p90: descrições em duas linhas fora da grade |
| T3-P32 | Tabela raster girada 90°/270° (escopo restante) | ALTO | P2 (encerrado) | Restantes: V9 p36, p92; V3 p116. Resolvidas: V9 p20, p55, p71 | 009: p20 0.950, p55 0.841, p71 1.000 de células exatas (mantidas). Restantes: p36 recall 0.845, `numeric` 0.348; p92 recall 0.842 | 5 / 5 (encerrado) | p20/p55/p71 resolvidas e **mantidas na 009**; p36/p92 sem mudança | 009 | Nova abordagem só com aprovação (integração da releitura girada no núcleo) |
| T3-P02 | Páginas escaneadas rotacionadas | ALTO | — | V3 p96–p99, p116; V4 p109–p114 | V4 p110 CER 0.970 → 0.060; V3 p97 0.892 → 0.000 | 1 / — | Resolvido — confirmado na RUN 004 (V3 p116 segue em T3-P32) | 004 | — |
| T3-P11 | V10 não avaliável pelo executor | ALTO | — | V10 (documento inteiro) | `metrics_status` erro → ok | 2 / — | Resolvido — confirmado na RUN 004 (via T3-P15) | 004 | — |
| T3-P15 | Corpus V10 trocado durante a rodada (`metrics_error`) | ALTO | — | V10 (RUN 003) | `metrics_error` → ok, 61 métricas | 1 / — | Resolvido — confirmado na RUN 004 | 004 | — |
| T3-P17 | Colunas de texto detectadas como tabela sem borda | ALTO | — | V10 L01/L02 (10 págs.; ex.: p13, p18) e p50; V9 p9 | V10 `table_detection_precision` 0.391 → 0.947 | 1 / — | Resolvido pelo critério — confirmado na RUN 004 | 004 | — |
| T3-P18 | Inclinação não compensada em páginas escaneadas | ALTO | — | V10 L01/L02 (12 págs.); V9 p9, p17, p23, p44, p81; V4 p105–p108, p135, p136; V3 p80 | V10 CER 0.264 → 0.194; L01/L02 média 0.314; V9 p81 0.906 → 0.000 | 6 / — (antes do limite) | Resolvido — confirmado na RUN 006 (efeitos colaterais: T3-P31, V4 p106/p156) | 006 | — |
| T3-P25 | Supressão do separador "Página NNN" apagava o corpo | ALTO | — | V3 p96 | V3 p96 CER 0.897 → 0.246, recall 0 → 1.000 | 1 / — | Resolvido — confirmado na RUN 004 | 004 | — |
| T3-P26 | Títulos-separadores "Página NNN" voltaram à saída | ALTO | — | V3 (12 págs.; ex.: p1, p44, p134) | V3 `heading_precision` 0.500 → 0.929 | 1 / — | Resolvido — confirmado na RUN 005 | 005 | — |
| T3-P28 | Valor monetário colado em célula de tabela | ALTO | — | V8 p4, p58 | `€ 98,4096,40` → `€ 98,40`; V8 `numeric` 0.988 → 0.991 | 1 / — | Resolvido — confirmado na RUN 006; **`€ 98,40` mantido na 009** | 009 | — (ver T3-P39, possível variante) |
| T3-P42 | Linhas OCR descartadas na fusão de fragmentos de título (perda silenciosa de conteúdo) | ALTO | — | V10 p5, p31, p62 (inclinadas), p11 | **012**: +83 palavras do gabarito recuperadas; recall p62 0.924 → 0.996, p31 0.969 → 0.997, p5 0.936 → 0.941, p11 0.976 → 0.982; V10 0.9915 → 0.9938 | 1 / 5 (correção de integridade dentro de T3-P06 t3) | **Corrigido — confirmado na RUN 012** (§1.28); a perda existia na 011 e antes, sem aviso | 012 | — |
| T3-P48 | Estado de caixas de seleção vetoriais (☑/☐) não representado | MÉDIO | P3 | V11 p54 | **013**: 15 estados ausentes (auditoria suplementar `checkbox_states` fn 15); tabela não detectada; sem aviso | 0 / 5 | **Novo (auditoria da RUN 013)** — recurso inexistente | 013 | Fora do escopo do Tesseract; registrar |
| T3-P49 | Linhas de parágrafo em ordem invertida em página raster inclinada | MÉDIO | P2 | V11 p61 (−3°, espiral); possivelmente p72 | **013**: p61 CER 0.539 com palavras corretas | 0 / 5 | **Novo (auditoria da RUN 013)**; família de T3-P35 | 013 | Junto com T3-P35 |
| T3-P41 | Parágrafos de texto nativo denso fundidos num único bloco | MÉDIO | **P0** (exige autorização) | V4 p9–p40 (32 págs.; ex.: p9 com 10 parágrafos num bloco) | **011** (igual desde a 001): 38 de 316 parágrafos certos; **227 das 417 fusões do V4**; 227 das 430 fronteiras ausentes do V4. **Estimativa não oficial e não validada**: V4 Parágrafos ≈ 48.7 → 64, nota ≈ +1.6 | 0 / 5 | **Pendente — causa raiz identificada (§1.31)**: em `assemble/content.py::_native_paragraphs`, o piso de 0.75 × altura da linha (≈ 5.8 pt) impede a quebra no vão real de 4.5 pt (≈ 3.4 × o vão entre linhas); estimativa +1.6 na nota do V4 (teto +2.2) | 012 | Candidato do ciclo pós-012 (§1.31), **adiado pela prioridade ao V11** (§1.35); exige autorização (nativo compartilhado) |
| T3-P38 | Fusão de parágrafos independentes em OCR denso de coluna única (regressão de T3-P20) | MÉDIO | — | V4 p83, p85, p99, p125, p129, p137, p138, p139, p141, p143; V10 p68 | **011**: V4 `paragraph_boundary_recall` 0.5595 → **0.5772** (008: 0.5782), `paragraph_detection_f1` 0.410 → 0.421; fusões V4 435 → **417** (008: 416), V10 44 → 43; 10 das 11 páginas-alvo iguais à 008; custo: +1 quebra em V4 p98/p118/p122/p128/p130 e V10 p37; palavras, conteúdo e dados idênticos | 3 / 5 | **Corrigido — confirmado na RUN 011** (t3, `2fdad33`). Resíduo: V4 p137 (137.04 + 137.05, atribuído à t5) | 011 | — (monitorar) |
| T3-P20 | Parágrafos OCR quebrados no meio da frase | MÉDIO | — (encerrado) | Páginas OCR de V4, V9, V10 (63 páginas mudaram só a segmentação na 009) | **009**: `paragraph_detection_f1` V4 0.378 → 0.410, V9 0.525 → 0.595, V10 0.409 → 0.458; quebras indevidas V4 392 → 271, V9 142 → 80, V10 268 → 213. **011**: ganhos mantidos (quebras V4 278, V9 80, V10 220 pelo script da §1.22) com as fusões de volta ao nível da 008 (T3-P38) | 5 / 5 (encerrado) | Parcial — t2 (`b1202ee`) + t5 **confirmadas na RUN 009**; t2 revertida por T3-P38 na 011, ganho mantido | 011 | Encerrado |
| T3-P07 | Tabelas sem borda e ordem embaralhada (páginas híbridas) | MÉDIO | P2 | V5 p3, p30 (ordem, recall 1.0) | V5 CER 0.109; 14 págs. > 0.15. A perda de recall de p4, p17, p26 etc. é T3-P40 (título ausente do PDF), não tabela | 0 / 5 | Pendente (sem mudança na 009) | 009 | Diagnóstico por etapa (só a parte de ordem) |
| T3-P12 | Extração lenta (detecção visual de tabelas) | MÉDIO | P2 | Todos os corpora (ex.: V4) | V4 2460 s (009); `HoughLinesP` ≈ 66% do tempo nas páginas pesadas | 0 / 5 (1 otimização sem perdas) | Pendente (causa medida) | 009 | Reduzir chamadas de `align_ruling_lines` mantendo as tabelas idênticas |
| T3-P29 | Página nativa com `/Rotate 90` montada como bloco único | MÉDIO | P2 | V4 p213, p214 | `paragraph_detection_f1` 0 nas páginas | 0 / 5 | Pendente | 009 | Investigar o layout de páginas nativas rotacionadas |
| T3-P31 | Títulos de coluna com travessão viram itens de lista | MÉDIO | P2 (bloqueado) | V10 L01/L02 (12 págs.; ex.: p5, p13, p62) | V10 `list_group_detection_precision` 0.354 (009 = 008) | 10 / 5 por série (2 séries encerradas) | Não resolvido | 009 | Nova abordagem só com aprovação |
| T3-P33 | Exceção `Invalid BBox` na tabela por trilhas OCR (V9 `partial`) | MÉDIO | P2 | V9 p36 | V9 `benchmark_status` `partial` (única causa; mesma exceção até a 011); −1.0 na nota do V9 | 0 / 5 | Pendente | 011 | Rejeitar candidato sem sequência vertical válida no detector sem borda (nativo compartilhado; exige autorização; baixo risco) |
| T3-P05 | Títulos nativos com espaçamento entre letras | MÉDIO | P3 | V8 p46, p47; V7 p1; V6 p69 | V8 p46/p47 = 85% das edições do V8; V7 p1 = 100% das edições do V7 (CER 0.754) | 0 / 5 | Documentado — fora do escopo | 009 | Requer autorização (montagem nativa) |
| T3-P08 | Dados críticos do V9 | MÉDIO | P3 | V9 | `numeric` 0.953, `currency` 0.962 (009 = 008); concentrados em p36, p58, p66, p92 | 0 / 5 | Monitorar | 009 | Acompanhar com T3-P32, T3-P34 e T3-P39 |
| T3-P03 | V8 `partial` por região menor que um glifo | MÉDIO | — | V8 p22, p25 | V8 `partial` → `success` | 1 / — | Resolvido — confirmado na RUN 003 | 003 | — |
| T3-P16 | V10 `partial_success` falso por página sem texto | MÉDIO | — | V10: as 19 páginas de imagem não textual (ex.: p2, p7) | V10 `partial_success` → `success` | 1 / — | Resolvido — confirmado na RUN 004 | 004 | — |
| T3-P13 | Regressões pontuais 001 → 002 | BAIXO | P3 | V3–V9 (métricas pontuais) | Sem piora desde a 003 | 0 / 5 | Sem mudança | 003 | Encerrar na próxima revisão |
| T3-P24 | Título quebrado com travessão virava item de lista | BAIXO | — | V10 (páginas L03–L06) | V10 `list_item_detection_precision` 0.641 → 0.771 | 1 / — | Resolvido — confirmado na RUN 004 | 004 | — |
| T3-P04 | Métricas do V4 lentas | INFO | P3 | V4 (avaliador) | 108 s de métricas na 009 (demais 1.5–53 s) | 0 / 5 | Aberta | 009 | Perfilar `compute_metrics.py` |
| T3-P09 | Regras ajustadas ao corpus no pós-processamento | INFO | P3 | `ocr/tesseract/postprocess.py` (ex.: V3 p96, V4 p213) | Materializado em T3-P23/P25/P27 (resolvidos) | — (3 mitigações) | Risco mitigado | 008 | Revisar se surgir novo caso |
| T3-P14 | Falhas pré-existentes na suíte de testes | INFO | P3 | 13 testes (`test_line_detector.py` e outros) | 13 falhas inalteradas | 0 / 5 | Aberta | suíte de 2026-11-17 | Tratar fora da Série 3 |
| T3-P21 | Recall ≈ 0.85–0.89 em OCR de texto, inclusive limpo; perdas extras nas páginas degradadas | INFO → **ALTO** (maior parcela da nota do V4) | P1 (exige experimento de OCR) | V11 (013): p63 (−8°, escuro), p75 (moiré), p61, p72 com IDs e números trocados; V4 OCR de texto (64 págs.; ex.: p81 limpa, p103/p104 blur, p117/p118 sombra) e tabelas OCR | **012 (§1.31)**: piso limpo CER 0.028 / recall 0.873; **82% dos 10 017 tokens ausentes estão nos segmentos adversariais** (IDs `O0I1l`, caminhos, sinais, confundíveis); 1 652 números falsos (precisão `numeric` 0.834); grupos D = 85% das edições do V4 | 0 / 5 | Aberta — **limite de reconhecimento** na configuração atual (`por`, `--oem 1`, `--psm 3`, perfil `default`, 288 dpi); hipótese pós-OCR H-esp (espaçamento) não verificada | 012 | Decisão do responsável: TSV da p81 (H-esp) e perfil CLAHE em 4 páginas |
| T3-P50 | Convenção de nível de título do gabarito V11 (`#` em todo título de página) | INFO | — | V11 (100 títulos) | **013**: `heading_level_match_rate` 0.056 (4/71), ≈ −1.9 na nota; resíduo real: o título da capa sai partido em dois `#` | — | **Registrado (classe E)**, como a convenção de V3/V4 (§1.16, §1.31) | 013 | Não ajustar ao gabarito |
| T3-P40 | Título/subtítulo exigidos pelo gabarito V5 ausentes do PDF (possível inconsistência do corpus) | INFO (limitação do gabarito) | — | V5 p4, 5, 6, 17, 18, 20, 23, 26, 28, 32, 36, 38 | Na área do bloco `heading` do manifesto não há texto, objeto nem tinta no PDF; ≈ 5.0% dos caracteres do gabarito V5; V5 idêntico na 010 | — (diagnóstico) | **Reclassificado (§1.19)**: não é defeito do extrator; corpus **não** alterado | 010 | Decisão do responsável sobre o corpus V5 |
| T3-P19 | `duplicate_excess_rate` não mede só duplicação | INFO | — | Avaliador | Na 009 cai em V4/V9/V10 só por segmentação (0.706 → 0.675; 0.482 → 0.417; 0.555 → 0.519) | — | Interpretação documentada (não é defeito) | 009 | — |
| T3-P30 | Executor gravava `threads` fixo no manifesto | INFO | — | Manifestos 001–005 | `threads: 6` fixo → 8 registrado | 1 / — | Resolvido — confirmado na RUN 006 | 006 | — |

---

## §0 Estado atual

| Campo | Valor |
|---|---|
| **Última atualização** | 2026-11-24 — **ciclo pós-013** (§1.36–§1.37): **T3-P44 diagnosticado** (causa localizada: a releitura endireitada com o texto completo é descartada por comparação de confiança média em `ocr/backends/tesseract.py::_deskew_page`; correção proposta, não implementada) e **T3-P43 implementado** (`assemble/repeated_regions.py`, contador de página confirmado por evidências convergentes; validado offline sobre capturas completas dos 9 corpora: V11 65.9 → 70.4, V10 75.5 → 78.3, nenhuma linha do gabarito removida; não commitado). Antes: 2026-11-24 — **auditoria da RUN 013 e inclusão do corpus V11** (§1.32–§1.35: V3–V10 idênticos byte a byte à 012; V11 = **65.9** na rubrica; 8 problemas novos, T3-P43 a T3-P50; prioridade passa ao V11; candidato único do próximo ciclo: **T3-P43**, mobília com contador de página não suprimida). Antes: 2026-11-19 — **meta interna de 85/100 por corpus e diagnóstico quantitativo do V4** (§1.31: decomposição de 66.9; 82% dos tokens ausentes do OCR de texto em segmentos adversariais; teto realista do V4 ≈ 80.7 com o Tesseract atual; candidato do próximo ciclo: T3-P41). Antes: **auditoria da RUN 012** (§1.28: T3-P06 **parcial — confirmado na RUN 012**, sem perda de conteúdo e com o caminho nativo idêntico; precisão do V4 e categorias de falso título não atendidas; novo T3-P42, perda antiga de conteúdo corrigida; §1.29: rubrica; §1.30: priorização). Antes: **T3-P06 t3 implementada** com autorização (§1.27; CORRIGIDO — VALIDADO PONTUALMENTE; alterações **não commitadas** em `layout/heading.py`, `layout/ocr_aware.py`, `ocr/reconstruct.py` e no teste novo). Antes: **ciclo pós-011** (§1.25: T3-P06; §1.26: diagnóstico de T3-P35). Antes: **auditoria da RUN 011** (§1.22: T3-P38 confirmado, sem regressão; §1.23: rubrica; §1.24: priorização; §5: controles de estabilidade × generalização; novo T3-P41). Antes: auditoria da RUN 010 (§1.20) e ciclo pós-010 (§1.21) |
| **Branch** | `feat/tesseract-optimization` — pós-005 `dfbf5e9`; pós-006 `f03710a` e `4a732c6`; T3-P22 tentativas 1–2 `bec686a`; pós-007 (T3-P22 tentativas 3–5, T3-P35) `ad43cb1`; pós-008 (T3-P34 t4, T3-P20 t2) `b1202ee`; pós-009 (T3-P20 t5, T3-P36, T3-P39) `95a6f72`; pós-010 (T3-P38 t3) `2fdad33`; pós-011 (T3-P06 t3) `18ccfb0` |
| **Baseline imutável** | `output/tesseract/001/` |
| **Baseline corrigida** | `output/tesseract/002/` (T3-P00) |
| **Baseline atual** | `output/tesseract/013/` (commit `b541246` = código de `18ccfb0` + V11 no executor); primeira referência do V11 |
| **Última rodada** | `output/tesseract/013/` (2026-11-23 15:45–17:22 UTC; commit `b541246`) — 9 corpora; V3–V10 idênticos à 012; V11 incluído (§1.32) |
| **Próxima rodada** | RUN 014 — para confirmar T3-P43 (implementado, não commitado); critérios na §5 (pós-013, atualizados pela §1.37) |
| **Configuração** | balanced · pt-BR · adaptive · tesseract · Markdown paginado · supplemental-audit. **Threads**: 6 até a 003; 8 desde a 005 (a 006 registra `threads: 8` e `tesseract_omp_thread_limit: 8`). O Tesseract 5.5.0 usa no máximo 4 threads OpenMP por chamada (`num_threads(4)` fixo no LSTM), então 6 e 8 equivalem a 4 (§1.10) |
| **Avaliador** | `evaluation-3.0.0`, métricas 3.1.0 — **não alterado** |
| **Corpus/gabaritos/manifestos** | **não alterados por nós**. **V11** (`corpus/V11/`, edição V11-R1C, 120 págs.) adicionado pelo responsável antes da RUN 013 e incluído em `scripts/run_tesseract_batch_wsl.py` (`b541246`). O diretório `corpus/V10/` foi substituído externamente em 2026-11-12, entre 07:56 e 07:59, durante a RUN 003 (ver T3-P15) |

### Registro de status (todos os problemas, incluindo os resolvidos)

| ID | Gravidade | Prioridade | Componente | Status |
|---|---|---|---|---|
| T3-P00 | CRÍTICO | — | executor | **RESOLVIDO** (002) |
| T3-P01 | CRÍTICO | — | `api.py` | **RESOLVIDO** (003) |
| T3-P02 | ALTO | — | backend Tesseract | **RESOLVIDO** (004; V3 p116 restante) |
| T3-P03 | MÉDIO | — | `api.py` | **RESOLVIDO** (003) |
| T3-P04 | INFO | P3 | avaliador | aberta |
| T3-P05 | MÉDIO | P3 | montagem de linhas nativas | DOCUMENTADO — fora do escopo |
| T3-P06 | ALTO | P1 | headings (`layout/heading.py` compartilhado; `layout/ocr_aware.py`) | **PARCIAL — CONFIRMADO NA RUN 012** (t3, `18ccfb0`, §1.28; 3/5): recall V4/V9/V10 0.562/0.457/0.634, nativas idênticas, nenhuma perda de conteúdo; precisão do V4 0.929 < 0.95, 3 categorias novas de falso título, hierarquia OCR documental (V10 `heading_level_match_rate` 0.519) e regressões V4 p95/p96/p104/p145/p149; inclinadas e V9 em regiões FIGURE fora do escopo |
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
| T3-P20 | MÉDIO | — | segmentação de parágrafos OCR (`ocr/reconstruct.py`) | **PARCIAL — CONFIRMADO NA RUN 009** (t2 `b1202ee` + t5 local; 5/5, encerrado): F1 de parágrafos V4/V9/V10 sobe sem mudar palavras; regressão de fusões no V4 de coluna única registrada em T3-P38 |
| T3-P21 | ALTO | P1 | reconhecimento Tesseract (limite) + pré-processamento | ABERTA — diagnóstico §1.31: piso adversarial do corpus (limite do Tesseract na configuração atual), degradação acima do piso e hipótese pós-OCR H-esp; exige experimento de OCR autorizado |
| T3-P22 | ALTO | — | ordem de linhas e regiões em páginas nativas multicoluna (`text/reading_order.py`, `layout/decorative.py`, compartilhados) | **CORRIGIDO — CONFIRMADO NA RUN 008** (5/5 tentativas, encerrado): V10 p9/p59/p54, V4 p20/p25/p26/p28, V9 p90; restam V9 p51, p5/p43, p12/p19/p72 |
| T3-P23 | CRÍTICO | — | backend Tesseract (`_parse_tsv`) | **RESOLVIDO** (004) |
| T3-P24 | BAIXO | — | `layout/ocr_aware.py` (listas) | **RESOLVIDO** (004) |
| T3-P25 | ALTO | — | `ocr/tesseract/postprocess.py` | **RESOLVIDO** (004), com efeito colateral corrigido em T3-P26 |
| T3-P26 | ALTO | — | `ocr/tesseract/postprocess.py` | **RESOLVIDO** (005) |
| T3-P27 | CRÍTICO | — | `ocr/tesseract/postprocess.py` | **RESOLVIDO** (005) |
| T3-P28 | ALTO | — | montagem de linhas Tesseract | **RESOLVIDO** (006: V8 p4/p58 `€ 98,40`, V8 `numeric` 0.991) |
| T3-P29 | MÉDIO | P2 | layout de página nativa com `/Rotate 90` | PENDENTE — página inteira num único bloco (V4 p213/p214) |
| T3-P30 | INFO | — | executor | **RESOLVIDO** (006 registra `threads: 8` e `tesseract_omp_thread_limit: 8`) |
| T3-P31 | **MÉDIO** (regressão confirmada na 006) | P0 | layout de regiões só-OCR + ordem genérica de linhas | **NÃO RESOLVIDO** — 5 tentativas pós-006; a melhor corrige as listas, mas intercala colunas (V10 p18); nenhuma mantida |
| T3-P32 | ALTO | — | backend Tesseract (orientação e reconhecimento) | **RESOLVIDO** (007: V9 p20/p71 corretas, p55 01–04 e `numeric` 1.000); V9 p36/p92: causa distinta na integração; tentativa 5 revertida; **5/5, encerrado** |
| T3-P33 | MÉDIO | P1 | tabela por trilhas OCR → `text_tracks._build_table` (compartilhado) | NOVO na 006 — exceção `Invalid BBox` na V9 p36 → V9 `partial_success`; PENDENTE |
| T3-P34 | ALTO | P1 | tabela raster com inclinação pequena em página mista (`tables/visual.py`, `assemble/content.py`, compartilhados) | **PARCIAL — CONFIRMADO NA RUN 009** (4/5): V9 p58 (tabela 8×6, células exatas 0.646), p44 e p49 confirmadas; p66/p78/p25/p8 com causas distintas; valores corrompidos da p58 em T3-P39 |
| T3-P35 | ALTO | P0 | calhas OCR fragmentadas → faixas de colunas (`ocr/reconstruct.py`, só Tesseract) | NOVO na 007 — **CORRIGIDO — CONFIRMADO NA RUN 008** para páginas raster sem inclinação (V10 L04–L06); inclinadas (L01/L02) pendentes; V10 p32 com reordenação a avaliar. **Mantido na RUN 009** (L04–L06 0.104; p32 0.311) |
| T3-P36 | CRÍTICO | — | pós-processamento Tesseract (`ocr/tesseract/postprocess.py`) | **CORRIGIDO — CONFIRMADO NA RUN 010** (V4 p167/p168); risco residual documentado (§1.20) |
| T3-P37 | ALTO | P1 | detector de tabelas nativo (compartilhado) | **NOVO na 009** — V9 p62/p72 desmontadas, p14 fundida |
| T3-P38 | MÉDIO | — | segmentação de parágrafos OCR (`ocr/reconstruct.py`) | **CORRIGIDO — CONFIRMADO NA RUN 011** (t3, `2fdad33`; §1.22): fusões de volta ao nível da 008, ganhos de T3-P20 mantidos; resíduo V4 p137 |
| T3-P39 | ALTO | — | montagem de linhas Tesseract (`ocr/reconstruct.py::_drop_conflicting_rereads`) | **CORRIGIDO — CONFIRMADO NA RUN 010** (V9 p58/p66; também V6 p11, V8 p49/p67) |
| T3-P40 | INFO | — | corpus/gabarito V5 | **RECLASSIFICADO** (§1.19): o PDF não contém os títulos exigidos pelo gabarito; sem alteração de código nem do corpus |
| T3-P41 | MÉDIO | P0 | segmentação de parágrafos **nativa** (compartilhada) | **PENDENTE — CAUSA RAIZ IDENTIFICADA (§1.31); adiado pela prioridade ao V11 (§1.35)** (registrado na auditoria da RUN 011; desde a 001): V4 p9–p40 com os parágrafos de cada página num só bloco; 227 das 417 fusões do V4; impacto na nota só estimado (não oficial, não validado); exige autorização |
| T3-P42 | ALTO | — | `layout/heading.py::_merge_heading_fragments` (descartava `ocr_lines` do 2º fragmento) | **CORRIGIDO — CONFIRMADO NA RUN 012** (§1.28): V10 p5/p11/p31/p62 recuperam 83 palavras do gabarito; perda silenciosa até a 011 |
| T3-P43 | ALTO | — | `assemble/repeated_regions.py` (assinatura de mobília; compartilhado) | **CORRIGIDO — VALIDADO OFFLINE SOBRE CAPTURAS COMPLETAS** (§1.37; não commitado); aguarda RUN 014 |
| T3-P44 | CRÍTICO | P0 | `ocr/backends/tesseract.py::_deskew_page` (seleção entre leituras) | **CAUSA LOCALIZADA** (§1.36) — releitura completa descartada pela confiança média; correção proposta, não implementada |
| T3-P45 | ALTO | P1 | detecção/fusão de tabelas nativas (compartilhado) | **NOVO (RUN 013)** — duplicação na V11 p50/p93 |
| T3-P46 | ALTO | P1 | detector de tabelas sem borda nativo (compartilhado) | **NOVO (RUN 013)** — colunas e lista nativas como tabela (V11 p5/p98/p86) |
| T3-P47 | ALTO | P2 | detector de tabelas nativo (compartilhado) | **NOVO (RUN 013)** — tabelas empilhadas/formulário desmontadas (V11 p57 e outras) |
| T3-P48 | MÉDIO | P3 | — (recurso inexistente) | **NOVO (RUN 013)** — checkboxes vetoriais (V11 p54) |
| T3-P49 | MÉDIO | P2 | reconstrução OCR em página inclinada | **NOVO (RUN 013)** — ordem de linhas invertida (V11 p61) |
| T3-P50 | INFO | — | gabarito V11 (convenção) | **REGISTRADO (RUN 013)** — nível `#` por página |

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

### §1.11 RUN 007 — comparação com a 006 (2026-11-17)

- **Comparabilidade**: configurações iguais (balanced, pt-BR, adaptive, tesseract, 8 threads, `OMP_THREAD_LIMIT` 8); hashes de PDF, referência e manifesto idênticos aos da 006 em todos os corpora; avaliador `evaluation-3.0.0`/métricas 3.1.0, sem alteração de código entre `dfbf5e9` e `bec686a` em `evaluation/`, `compute_metrics.py`, `evaluate_e2e.py`, executor e `corpus/` (o campo `implementation_commit` muda só porque registra o commit do repositório).
- **Execução**: 8/8 com métricas `ok` e as mesmas métricas aplicáveis. Extração `success`/`valid` em 7 corpora; **V9 continua `partial_success`/`partial` → `needs_review`**, de novo só pela V9 p36 (T3-P33, mesma exceção `Invalid BBox`).
- **Saídas**: **V3, V5, V6, V7 e V8 idênticos byte a byte à 006.** Mudaram só V4 p25; V9 p20, p55, p71; V10 p9, p54, p59, p62.

| Página | CER 006 → 007 | Esperado (pontual) | Correção |
|---|---|---|---|
| V10 p9 | 0.689 → **0.088** | 0.088 | T3-P22 ✅ |
| V10 p59 | 0.263 → **0.069** | 0.069 | T3-P22 ✅ |
| V10 p62 | 0.468 → **0.251** | 0.251 | T3-P22 (efeito na ordem OCR) ✅ |
| V4 p25 | 0.336 → **0.222** | 0.222 | T3-P22 ✅ (parcial) |
| V9 p20 | 0.069 → 0.001 (`cell_content_exact` 0.042 → 0.950) | idem | T3-P32 ✅ |
| V9 p55 | 0.179 → 0.177 (`numeric` 0.974 → 1.000; recall 0.977 → 0.982) | idem | T3-P32 ✅ |
| V9 p71 | 0.068 → 0.000 (`cell_content_exact` 0 → 1.000) | idem | T3-P32 ✅ |
| **V10 p54** | 0.222 → **0.231** | não previsto | **regressão de T3-P22** (ver T3-P22) |

| Métrica documental (006 → 007) | V4 | V9 | V10 |
|---|---|---|---|
| `cer_sequential_normalized` | 0.062 → 0.061 | 0.077 → 0.075 | 0.194 → **0.171** |
| `wer_sequential` | 0.154 → 0.153 | 0.088 → 0.085 | 0.231 → 0.203 |
| `text_coverage_recall` / precisão | = | = | = (0.991 / 0.974) |
| `paragraph_detection_f1` | = | 0.524 → 0.523 | 0.372 → **0.394** |
| `heading_detection_f1` / precisão | = | = | 0.430 → 0.428 / 0.772 → 0.759 |
| listas | = | = | = (T3-P31 inalterado: grupos 0.354) |
| `cell_content_exact_match` / `cell_cer` | = | 0.527 → **0.560** / 0.579 → **0.545** | = |
| `table_column_count_accuracy` | = | 0.600 → 0.631 | = |
| `numeric` / `currency` / `dates` | = | = | = |

- **T3-P32 confirmado** na rodada completa: V9 p20 e p71 orientadas, com números e células nas linhas certas; p55 com os identificadores 01–04 nas linhas dos prazos (palavras da coluna "Situação" presentes, fora da grade, como antes); V8 e páginas rotacionadas de V3/V4 idênticas à 006.
- **T3-P22 confirmado nas páginas-alvo**, com uma regressão: **V10 p54** (tabela nativa de 6 colunas sem borda, não detectada) passou a ser lida coluna a coluna, separando cada valor "Total" da sua "Situação" (`pairwise_reading_order_accuracy` 0.819 → 0.735, `heading_precision` 1.0 → 0.5). Causa: a calha "irregular" da tentativa 1 aceita lacunas entre células curtas. Corrigida na tentativa 3 (ver T3-P22).
- **Desempenho**: extração −2% a −8% (V3 +9%), CPU 102–135%, como nas rodadas anteriores; sem efeito mensurável das correções.
- **Erros remanescentes do V10 por cenário** (fração das edições): L02 25.0% e L01 21.1% (raster inclinado, CER 0.32/0.27); **L05 17.3%, L06 13.2%, L04 11.0% (raster sem inclinação, CER 0.22/0.17/0.14)**; L03 5.6% (nativo, 0.07); N06 2.3%; controles 2.3%. Nas páginas raster sem inclinação a sequência de procedimentos está certa, mas **segmentos de coluna mudam de lugar** (V10 p52: trechos da coluna 1 dentro da coluna 2; "Registro de exame final" antes de outro bloco) e **nenhum título é detectado** (nem o da página). Ver T3-P35 e T3-P06.
- **Demais problemas** (sem mudança na 007): T3-P31 (20 itens de lista falsos no V10), T3-P33 (V9 p36), T3-P34 (V9 p58/p66/p78), T3-P07 (V5: p17, p26, p23, p4 com recall 0.78–0.82), T3-P21 (V4 p104 recall 0.34), T3-P05 (V8 p46/p47, V7 p1), V3 p116/p138.

**Priorização pós-007** (critérios: perda/corrupção de conteúdo > dados críticos > leitura em documentos reais > regressões > estrutura > abrangência > viabilidade):

| Problema | Gravidade | Abrangência | Por quê | Prioridade |
|---|---|---|---|---|
| **T3-P22 (regressão V10 p54 + restante)** | ALTO — associação valor ↔ situação perdida (dado crítico) | V10 p54; V4 p26/p28 (≈ 8% do V4); V4 p25 parcial | regressão introduzida pela correção anterior; restam 3 tentativas; hipótese "carimbo" documentada e confirmada por censo | **P0 — escolhido (1)** |
| **T3-P35 ordem de blocos em páginas raster multicoluna** (novo) | ALTO — leitura | V10 L04–L06 (41% das edições) e influência em L01/L02; V9/V4 raster multicoluna | maior massa de erro do corpus prioritário; documentos reais escaneados | **P0 — escolhido (2)** |
| T3-P06 títulos em páginas OCR | MÉDIO (estrutura) | V10 `heading_recall` 0.30 | sem perda de texto | P1 |
| T3-P31 listas falsas | MÉDIO | V10, 12 págs. | 5 tentativas encerradas; nova abordagem só com aprovação | P1 (bloqueado) |
| T3-P34 tabelas raster inclinadas | ALTO (dados) | V9 5 págs. | detecção visual compartilhada | P1 |
| T3-P33 exceção V9 p36 | MÉDIO (execução) | V9 1 pág. | evita uma tabela falsa; muda o status | P2 |
| T3-P21, T3-P07, T3-P05, T3-P12 | — | V4 / V5 / V7–V8 / todos | inalterados | P2 |

### §1.12 RUN 008 — comparação com a 007 (2026-11-17, código `ad43cb1`)

Rodada completa das correções pós-007 (T3-P22 tentativas 3–5, T3-P35 tentativas 1–2). Todos os corpora `ok`; V9 continua `partial`/`needs_review` só por T3-P33 (esperado).

**Páginas que mudaram** (texto por página, 008 × 007): V3, V5, V6, V7 e V8 **idênticos**; V4 4 páginas; V9 1; V10 10. Todas eram previstas pelas extrações pontuais, com os mesmos valores.

| Corpus | Página | CER 007 → 008 | Outras mudanças |
|---|---|---|---|
| V4 | p20 / p25 / p26 / p28 | 0.061 / 0.222 / 0.272 / 0.260 → 0.000 / 0.010 / 0.000 / 0.010 | recall 1.000 mantido (T3-P22) |
| V9 | p90 | 0.245 → 0.097 | `cell_content_exact_match` 0.146 → 0.896; `paragraph_detection_f1` 0.500 → 0.727 (T3-P22) |
| V10 | p54 | 0.231 → 0.095 | tabela detectada (`table_detection_f1` 0 → 1), células exatas 0 → 0.881, título 0.667 → 1.000 (T3-P22) |
| V10 | p52 / p11 / p24 / p84 / p66 | 0.372 / 0.329 / 0.302 / 0.207 / 0.181 → 0.083 / 0.095 / 0.082 / 0.050 / 0.057 | recall mantido (p11 0.976, demais 1.000) (T3-P35) |
| V10 | p40 / p56 / p78 | 0.276 / 0.265 / 0.203 → 0.123 / 0.141 / 0.087 | (T3-P35) |
| V10 | **p32** | **0.213 → 0.311** | recall 1.000 mantido; `paragraph_detection_f1` 0.591 → 0.356 — **regressão confirmada, só de ordem** |

**Documento (007 → 008)**:

| Corpus | Métrica | 007 | 008 |
|---|---|---|---|
| V4 | `cer_sequential_normalized` | 0.061 | **0.054** |
| V4 | `paragraph_detection_f1` | 0.375 | 0.378 |
| V9 | `cell_content_exact_match` / `cell_cer` | 0.560 / 0.545 | **0.582 / 0.512** |
| V9 | `list_group_detection_precision` / `list_item_detection_precision` | 0.750 / 0.765 | **0.692 / 0.722** (piora conhecida, V9 p90) |
| V10 | `cer_sequential_normalized` / `wer_sequential` | 0.171 / 0.203 | **0.141 / 0.169** |
| V10 | `table_detection_f1` / `cell_content_exact_match` | 0.923 / 0.827 | **0.974 / 0.895** |
| V10 | `paragraph_detection_f1` | 0.394 | 0.409 |
| V10 | CER médio L04–L06 (18 págs.) | 0.177 | **0.104** |

**Sem mudança** (008 = 007): `text_coverage_recall` (V4 0.894, V9 0.990, V10 0.991); `numeric`/`currency`/`dates` de V4 (0.897/0.968/0.973), V9 (0.953/0.962/0.991) e V10 (0.945/1.000/0.991); `table_detection_f1` de V4/V9; V10 `list_group_detection_precision` 0.354 (T3-P31); V10 p9 0.088 e p59 0.069 mantidos; páginas inclinadas L01/L02 idênticas.

**Critérios do §5 (RUN 008)**: todos atendidos — V10 p54 0.095 com tabela; V4 p20/p25/p26/p28 ≤ 0.010; V9 p90 células 0.896; V10 p9/p59 mantidos; V4 documental 0.054 < 0.061; L04–L06 0.104 < 0.17; V10 documental 0.141 < 0.171 com recall 0.991; L01/L02 sem mudança; V3/V6/V7/V8 idênticos (nenhuma página do censo de agrupamentos "status" mudou); V5 sem mudança; tabelas e dados críticos sem piora; V9 `partial` por T3-P33.

**Regressões avaliadas**:
- **V10 p32** (T3-P35): reproduzida na rodada completa. Conteúdo íntegro (recall 1.000, sem mudança de números); só a ordem de blocos piorou. Não é regressão crítica pelo critério combinado (sem perda de conteúdo nem valor numérico errado); fica registrada em T3-P35.
- **V9 p90 — listas** (T3-P22): a tabela agora é detectada, mas a segunda linha de descrições quebradas na célula ("inspeção", "armazenamen", "rede", "laboratoriais", "manutenção") fica fora da grade; duas delas saem como itens de lista com "☐". Em 007 a página não tinha tabela nenhuma; o saldo é positivo (células 0.146 → 0.896). Resíduo: célula de descrição incompleta e 1 grupo de lista falso.

**Desempenho**: extração V3 410 → 365 s, V4 2440 → 2601 s (+6.6%), V6 608 → 654 s (+7.7%), V7 160 → 183 s (+14%), V9 588 → 625 s (+6.2%), V10 544 → 550 s. V3, V6 e V7 têm saída idêntica à 007, então a variação de −11% a +14% é ruído do ambiente (mesma faixa já observada na 007); V4 fica dentro de ±10%.

**Conclusão**: T3-P22 (tentativas 3–5) e T3-P35 (tentativas 1–2) **confirmados na RUN 008**, sem regressão crítica. Resíduos: V10 p32 (ordem) e V9 p90 (descrições de célula em duas linhas).

### §1.13 Validação pontual das correções pós-008 (2026-11-17)

Extrações reais só das páginas selecionadas (balanced, pt-BR, adaptive, Tesseract, 8 threads, Markdown paginado) com `compute_metrics.py` no escopo `selected_pages`. Cada conjunto foi extraído **duas vezes no mesmo contexto**, com a correção ligada e desligada em memória (harness no scratchpad), para isolar o efeito de diferenças de contexto do subconjunto.

| Problema | Páginas | Mudaram | Resultado |
|---|---|---|---|
| T3-P34 (tentativa 4) | 48 (V3 4, V4 11, V5 3, V6 3, V8 3, V9 16, V10 4) + controle ampliado 47 (V4 20, V9 21, V10 11) | 3 | V9 p58 CER 0.157 → 0.063, tabela detectada, células exatas 0 → 0.646, `cell_cer` 1.000 → 0.153; V9 p44 0.366 → 0.204 e p49 0.112 → 0.092 (só ordem, mesmas linhas). 92 páginas idênticas byte a byte, incluindo tabelas giradas (V9 p20/p55/p71/p36/p92, V3 p116) e as tabelas escaneadas do V4 |
| T3-P32 (tentativa 5) | sondas V9 p36, p92 | — | revertida antes da validação ampla (§2) |
| T3-P20 (tentativa 2) | 79 (V3 6, V4 15, V5 2, V6 2, V8 2, V9 19, V10 27; inclui L01/L02 inclinadas) | 29 (só segmentação) | escopo `paragraph_detection_f1` V4 0.071 → 0.133, V9 0.416 → 0.475, V10 0.334 → 0.370; nenhuma página piorou; CER, WER, recall, precisão textual, dados críticos, listas e títulos idênticos |

**Suíte completa** sobre o código final: 13 falhas = exatamente as de T3-P14; nenhuma falha nova.

### §1.14 Validação pontual de T3-P20, tentativas 3–5 (2026-11-17)

Mesmo protocolo de §1.13 (81 páginas dos oito corpora, base `b1202ee` reproduzida em memória no mesmo contexto). Resultado da tentativa 5 (mantida): só a segmentação de parágrafos muda; `paragraph_detection_f1` do escopo V9 0.475 → 0.540, V10 0.387 → 0.431, V4 0.133 → 0.135; V3, V5, V6, V7 e V8 idênticos byte a byte; nenhuma página piorou. Tentativas 3 e 4 rejeitadas por V9 p17 e V10 p25 (§2).

### §1.15 RUN 009 — auditoria integral e comparação com a 008 (análise de 2026-11-18)

Etapa exclusivamente de avaliação: nenhuma extração nova, nenhum código, teste, gabarito, configuração ou avaliador alterado. Todas as medidas abaixo vêm dos arquivos oficiais de `output/tesseract/008/` e `009/`; os diagnósticos adicionais (segmentação de parágrafos, eventos críticos, rubrica) foram calculados por scripts no scratchpad da sessão, só com leitura desses arquivos e do `corpus/`.

#### Verificação operacional

| Item | Resultado |
|---|---|
| Execução | 2026-11-17 16:36:30 → 18:06:28 UTC; 8/8 com extração `exit 0`, métricas `ok`, `rank_eligible`, mesmo número de métricas aplicáveis da 008 (V3 61, V4 61, V5 67, V6 63, V7 65, V8 65, V9 67, V10 61) |
| Código | `implementation_commit` registrado = `b1202ee` (008: `ad43cb1`). A árvore de trabalho continha **T3-P20 t5 não commitada** (`ocr/reconstruct.py`, modificado às 13:15 −03; a rodada começou às 13:36 −03 e o arquivo não mudou depois). Portanto a 009 mede `b1202ee` + t5 |
| Configuração | idêntica à 008: balanced · pt-BR · adaptive · tesseract · Markdown paginado · supplemental-audit · `threads` 8 · `tesseract_omp_thread_limit` 8; `extractor_flags` idênticos nos 8 manifestos |
| Hashes de entrada | PDF, referência e manifesto **idênticos à 008** nos 8 corpora (`input_hashes` do `run_summary.json`); `reference_effective_sha256` e `manifest_sha256` dos arquivos de métricas também idênticos; nenhum `corpus_changed` |
| Avaliador | `evaluation-3.0.0`, métricas 3.1.0; todas as políticas do bloco `evaluator` (normalização, parser, parágrafos, alinhamento, entidades, tabelas, agregação, distância de edição, recursos) **idênticas** às das rodadas 001–008. Só `implementation_commit` e `created_at_utc` mudam |
| Status | 7 corpora `success`/`valid`; **V9 `partial_success`/`partial` → `needs_review`**, de novo só pela **p36** (`ocr_table_structure_unavailable`: `ValueError: Invalid BBox coordinates: BBox(x0=63.25, y0=526.06, x1=237.84, y1=521.28)`, a mesma exceção da 006–008; T3-P33). Os estados de todas as páginas (`ocr_outcome`, `content_status`, `stability_status`) e os avisos de documento são **idênticos à 008** nos 8 corpora |
| Páginas recuperadas | iguais à 008 — V4 p148/p164/p180; V6 p6/p42; V8 p45/p70; V9 p3/p19/p24/p49/p56/p58/p66/p71 (recuperações bem-sucedidas, não falhas). Avisos `unaccounted_content` (1 linha recuperada por texto) em V9 p3/p24/p70/p93 e V5 p17/p26, iguais à 008 |
| Impacto real do `partial` | a p36 tem a tabela girada ilegível (recall 0.845, `numeric` 0.348) por T3-P32; a exceção de T3-P33 só impede uma tabela falsa. `metrics_status: ok` indica apenas que o cálculo foi válido |

#### Páginas que mudaram (texto por página, 009 × 008)

**V3, V5, V6, V7 e V8: arquivos idênticos byte a byte.** Mudaram 66 páginas, todas em V4 (27), V9 (19) e V10 (20):

| Corpus | Páginas | Natureza da mudança (sequência de palavras comparada token a token) |
|---|---|---|
| V4 | 82, 83, 85, 86, 88, 92, 94, 98, 99, 100, 116, 118, 122, 124, 125, 127, 128, 129, 130, 137, 138, 139, 140, 141, 142, 143, 187 | **só segmentação** (mesma sequência de palavras, incluindo marcação; muda apenas a divisão em blocos) — T3-P20 |
| V9 | 8, 12, 19, 31, 36, 39, 41, 52, 53, 60, 67, 73, 78, 80, 84, 89 | **só segmentação** — T3-P20 |
| V9 | 44, 49 | **só ordem** (mesmas palavras, reordenadas) — T3-P34 t4 |
| V9 | 58 | **conteúdo**: o texto solto da tabela virou tabela 8×6 (152 → 220 tokens com a marcação de tabela) — T3-P34 |
| V10 | 6, 8, 11, 19, 21, 24, 32, 37, 40, 46, 50, 52, 56, 63, 66, 68, 70, 78, 81, 84 | **só segmentação** — T3-P20 |

Nenhuma página fora do escopo previsto (regiões OCR para T3-P20; tabela raster inclinada para T3-P34) mudou. Nenhuma página nativa mudou.

#### Métricas oficiais, 008 → 009 (documento)

Valores em **negrito** mudaram; "(≈)" = mudou só na 4ª casa; `n/a` = não aplicável (sem objeto no gabarito).

| Métrica | V3 | V4 | V5 | V6 | V7 | V8 | V9 | V10 |
|---|---|---|---|---|---|---|---|---|
| CER bruto (`cer_markdown_raw`) | 0.028 | 0.063 (≈) | 0.125 | 0.021 | 0.009 | 0.011 | 0.114 → **0.110** | 0.151 (≈) |
| CER normalizado | 0.013 | 0.054 | 0.109 | 0.008 | 0.001 | 0.006 | 0.074 → **0.072** | 0.141 |
| WER | 0.024 | 0.145 | 0.116 | 0.016 | 0.003 | 0.007 | 0.084 → **0.082** | 0.169 |
| Recall textual | 0.983 | 0.894 | 0.921 | 0.988 | 0.999 | 0.994 | 0.990 | 0.991 |
| Precisão textual | 0.990 | 0.920 | 0.995 | 0.992 | 0.997 | 0.998 | 0.989 (≈) | 0.974 |
| Títulos F1 | 0.867 | 0.502 | 0.857 | 0.990 | 0.989 | 0.993 | 0.426 | 0.430 |
| Títulos precisão | 0.929 | 1.000 | 1.000 | 0.996 | 1.000 | 0.993 | 0.541 | 0.772 |
| Títulos recall | 0.812 | 0.335 | 0.750 | 0.985 | 0.978 | 0.993 | 0.351 | 0.298 |
| Nível de título | 0.000 | 0.000 | 0.961 | 1.000 | 1.000 | 1.000 | 0.924 | 0.977 |
| CER do texto dos títulos | 0.199 | 0.631 | 0.344 | 0.019 | 0.026 | 0.009 | 0.667 | 0.821 |
| Parágrafos F1 | 0.923 | 0.378 → **0.410** | 0.822 | 0.963 | 0.968 | 0.953 | 0.525 → **0.595** | 0.409 → **0.458** |
| Parágrafos precisão | 0.920 | 0.348 → **0.400** | 0.866 | 0.956 | 0.975 | 0.948 | 0.415 → **0.487** | 0.310 → **0.353** |
| Parágrafos recall | 0.927 | 0.412 → **0.421** | 0.781 | 0.970 | 0.961 | 0.957 | 0.712 → **0.765** | 0.601 → **0.653** |
| Fronteiras F1 | 0.917 | 0.520 → **0.542** | 0.855 | 0.964 | 0.984 | 0.972 | 0.654 → **0.697** | 0.573 → **0.591** |
| Fronteiras recall | 0.929 | 0.578 → **0.559** | 0.803 | 0.972 | 0.976 | 0.978 | 0.942 | 0.869 → **0.867** |
| Grupos de lista F1 | 0.933 | 0.560 | 0.867 | 1.000 | 1.000 | 1.000 | 0.818 | 0.523 |
| Itens de lista F1 | 1.000 | 0.875 | 1.000 | 1.000 | 1.000 | 1.000 | 0.839 | 0.795 |
| Hierarquia de listas | 1.000 | 1.000 | 0.872 | 1.000 | 1.000 | 1.000 | 0.923 | 1.000 |
| Ordem de leitura (pares) | 1.000 | 1.000 (≈) | 1.000 | 1.000 | 1.000 | 1.000 | 0.999 → **1.000** | 1.000 |
| Ordem de blocos (pares) | 1.000 | 1.000 (≈) | 0.999 | 1.000 | 1.000 | 1.000 | 1.000 (≈) | 0.999 |
| Tabelas F1 | 1.000 | 0.896 | 1.000 | 1.000 | 1.000 | 1.000 | 0.850 → **0.860** | 0.974 |
| Linhas F1 | 0.997 | 0.913 | 1.000 | 1.000 | 1.000 | 1.000 | 0.832 → **0.847** | 0.970 |
| Nº de colunas | 1.000 | 0.743 | 1.000 | 1.000 | 1.000 | 1.000 | 0.646 → **0.661** | 0.950 |
| Células F1 | 0.998 | 0.877 | 1.000 | 1.000 | 1.000 | 1.000 | 0.808 → **0.829** | 0.965 |
| Células exatas | 0.985 | 0.634 | 1.000 | 0.938 | 1.000 | 0.999 | 0.582 → **0.601** | 0.895 |
| Alinhamento de células | 0.995 | 0.733 | 1.000 | 0.981 | 1.000 | 1.000 | 0.610 → **0.632** | 0.930 |
| CER das células | 0.004 | 0.333 | 0.000 | 0.014 | 0.000 | 0.000 | 0.512 → **0.481** | 0.109 |
| Conteúdo de tabela F1 | 0.988 | 0.759 | 1.000 | 0.942 | 1.000 | 0.999 | 0.786 → **0.797** | 0.931 |
| Números | 0.991 | 0.897 | 0.993 | 0.965 | 1.000 | 0.991 | 0.953 | 0.945 |
| Moedas | 1.000 | 0.968 | 1.000 | 1.000 | 1.000 | 1.000 | 0.962 | 1.000 |
| Porcentagens | 1.000 | 0.979 | 1.000 | n/a | 1.000 | 1.000 | 1.000 | 1.000 |
| Datas | 1.000 | 0.973 | 1.000 | 0.993 | 1.000 | 0.998 | 0.991 | 0.991 |
| Identificadores (CPF/CNPJ) | n/a | n/a | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | n/a |
| `duplicate_excess_rate` (T3-P19) | 0.038 | 0.706 → **0.675** | 0.055 | 0.033 | 0.015 | 0.039 | 0.482 → **0.417** | 0.555 → **0.519** |
| Tempo de extração (s) | 365 → 374 (+2.5%) | 2601 → 2460 (−5.4%) | 87 → 89 (+2.3%) | 654 → 601 (−8.1%) | 183 → 165 (−9.7%) | 404 → 393 (−2.6%) | 625 → 586 (−6.3%) | 550 → 546 (−0.6%) |

Outras observações:
- `process_numbers` é não aplicável em todos os corpora; `cpf`/`cnpj` só existem em parte deles. Mudaram também, em quantidades pequenas, V9 `cell_content_exact_match_matched` 0.859 → 0.850 e `table_column_order_accuracy` 1.000 → 0.9995. Os dois vêm da tabela nova da p58, que entra no conjunto casado com 0.646 de células exatas. Não é regressão de tabela anterior.
- **Duplicação**: a queda de `duplicate_excess_rate` em V4/V9/V10 decorre só da segmentação (blocos que passam a casar com o gabarito; T3-P19). A sequência de palavras é idêntica, então não há duplicação real a mais nem a menos.
- **Ordem de leitura por página**: em páginas que mudaram só a segmentação, a `pairwise_reading_order_accuracy` da página oscila (ex.: V4 p99 1.000 → 0.927, V10 p11 0.931 → 0.866, V9 p8 0.894 → 0.932). A métrica compara blocos casados, e fundir ou partir blocos muda o casamento. A sequência de palavras é idêntica byte a byte, então **não há mudança real de ordem** nessas páginas. No documento, o efeito é ≤ 0.0001.
- **Desempenho**: V3, V5, V6, V7 e V8 têm saída idêntica e variaram de −9.7% a +2.5%. É ruído do ambiente, na faixa já observada (−11% a +14%). V4 −5.4%, V9 −6.3% e V10 −0.6% estão dentro desse ruído, e as alterações não têm custo esperado. Nenhuma variação pode ser atribuída ao código. CPU 102–135% (igual às rodadas anteriores); métricas do V4 em 108 s.

#### Distribuição dos erros na 009 (páginas com CER > 0.15, recall < 0.90 e `numeric` < 0.90)

| Corpus | CER > 0.15 (008 → 009) | Recall < 0.90 | `numeric` < 0.90 | Concentração das edições (009) |
|---|---|---|---|---|
| V3 | 5 → 5 (p86, p96, p99, p116, p138) | 7 (p86, 87, 94, 99, 103, 116, 138) | 5 | p116 27.5% (tabela girada), p138 19.2% (página em branco anotada no gabarito), p96 11.1% |
| V4 | 38 → 38 | 105 (quase todo o OCR de texto p81–p144 e de tabelas p145–p184) | 91 | p104 7.8% (blur, recall 0.34), p118 5.0%, p88 5.0%, p142 4.8%; OCR de tabelas p145–p184 com CER 0.2–0.9 |
| V5 | 14 → 14 | 12 (as páginas de T3-P40) | 1 | edições espalhadas; p17 8.3%, p26 7.9%, p23 7.4% |
| V6 | 0 → 0 | 3 (p3, p33, p89: varreduras, recall 0.86) | 12 | p33/p3/p89 ≈ 31% |
| V7 | 1 → 1 (p1) | 1 | 0 | p1 100% (T3-P05) |
| V8 | 2 → 2 (p46, p47) | 2 | 6 | p46+p47 84.7% (T3-P05) |
| V9 | 20 → **19** (sai a p58) | 4 (p36, p58, p66, p92) | 17 | p51 11.2%, p9 8.1%, p43 7.1%, p5 5.4%, p17 4.9%, p62 4.1%, p23 3.7%, p72 3.5% |
| V10 | 14 → 14 | 0 | 4 | L02 30.4%, L01 25.6% (inclinadas), L04 13.2%, L06 9.1%, L05 7.4%, L03 6.9% |

**Perda de conteúdo (páginas com ≥ 100 caracteres de referência)**:
- **Volume ausente** (saída com < 50% dos caracteres da referência): só **V4 p167 e p168**, inalteradas desde a 002 (T3-P36, novo).
- **Conteúdo presente, mas ilegível** (recall < 0.50 com volume presente): V3 p86 (caracteres confundíveis), V4 p103 e p104 (blur), p153, p163–p166 e p172 (tabelas OCR a 120 dpi). Também inalteradas.
- Nenhuma página nova perdeu conteúdo na 009. A varredura de recall por página contra a melhor rodada anterior (001–008) só aponta perdas antigas: V4 p156 (efeito de T3-P18), p151, p178, p167, p168 e V3 p94 (estas desde a ativação de tabelas na 002).

**Corrupção de dados críticos**: nenhuma página nova na 009, e o conjunto de páginas com `numeric`/`currency` < 0.80 é idêntico à 008. Principais casos: V9 p36, p58, p66, p92; V4 OCR degradado (43 páginas, na maior parte números inventados pelo OCR: `numeric` com 1642 FP contra 254 FN); V6 p3/p33/p89 (varreduras). Na V9 p58, os valores corrompidos agora estão **dentro** da tabela (T3-P39).

#### T3-P34 — tabelas raster com inclinação pequena (critérios da §5.0c)

| Verificação | Critério | 009 | Situação |
|---|---|---|---|
| V9 p58 | tabela 8×6, células exatas ≥ 0.6, CER ≤ 0.08 | tabela 8 linhas × 6 colunas detectada (`table_detection_f1` 1.000, `table_column_count_accuracy` 1.000); células exatas **0.646**; CER **0.063**; `paragraph_detection_f1` 0.444 → 1.000 | ✅ (igual ao pontual) |
| V9 p44 / p49 | ≤ 0.21 / ≤ 0.10 | **0.204** / **0.092** (só ordem; mesmas palavras) | ✅ |
| V9 documental | células exatas > 0.582; `numeric`/`currency` ≥ 0.953/0.962 | 0.601; 0.953/0.962 | ✅ |
| Tabelas anteriores | sem regressão | V3, V5–V8 idênticos; V4 e V10 com todas as métricas de tabela iguais; V9 sem mudança fora da p58 (tabelas giradas p20/p55/p71 idênticas) | ✅ |

- **O que a p58 ainda tem de errado** (o pontual não mediu isso):
  - Os três primeiros títulos de coluna estão vazios ("Rubrica", "Descrição", "Qtde.").
  - As linhas 005–007 estão sem "Rubrica" e "Qtde.".
  - Há sujeira de réguas ("\|1", "\| R$ 206,74").
  - Três valores monetários estão **corrompidos**: `R$ 2.333,52333,92` (ref. `R$ 2.333,52`), `R$ER$ 845,26`, `R$ 1.036,351.` e o prefixo `lrs:R$ 388,92`.
  - `numeric` 0.741 e `currency` 0.857 são iguais à 008, ou seja, a correção estrutural não piorou nem corrigiu os valores.
  - Os valores ficaram associados às linhas certas. A corrupção vem da leitura (registrada em T3-P39).
- **Pendentes inalteradas** (idênticas à 008): p66 (recall 0.789, `currency` 0.632; linhas 004–006 truncadas, "R$ 7"), p78 (tabela não detectada, texto completo), p25 2ª tabela (células exatas 0.118) e p8 1ª tabela (0.375).
- **Conclusão**: T3-P34 t4 **confirmada na rodada completa**, sem regressão. Parcial (4/5).

#### T3-P20 — parágrafos OCR quebrados no meio da frase (t2 `b1202ee` + t5 local)

**Métricas oficiais** (critérios da §5.0c):

| Verificação | Critério | 009 | Situação |
|---|---|---|---|
| `paragraph_detection_f1` documental | V10 > 0.409, V9 > 0.525, V4 > 0.378 | **0.458**, **0.595**, **0.410** | ✅ (ganhos pontuais confirmados: V9 +0.070, V10 +0.049, V4 +0.033) |
| `paragraph_boundary_f1` | sem piora | V4 0.520 → 0.542, V9 0.654 → 0.697, V10 0.573 → 0.591 | ✅ — mas `paragraph_boundary_recall` cai em V4 (0.578 → 0.559) e V10 (0.869 → 0.867): fusões |
| CER e recall | iguais à 008 em V10 (0.141/0.991) e V4 (0.054/0.894); V9 ≤ 0.074 e 0.990 | iguais; V9 0.072/0.990 | ✅ |
| Listas, títulos, tabelas, dados críticos | sem piora > 0.005 | idênticos em todos os corpora | ✅ |
| V9 p17, V10 p25 | sem piora de `paragraph_detection_f1` | idênticas à 008 | ✅ |
| Controle V3, V5–V8 | idênticos salvo segmentação OCR | idênticos byte a byte (o V3 também não mudou) | ✅ |

**Análise por tipo de erro** (diagnóstico próprio, não oficial: as palavras de cada bloco da saída foram alinhadas às dos parágrafos de prosa do gabarito, com títulos, tabelas, listas e controle excluídos. "Quebra" = parágrafo do gabarito partido em mais blocos; "fusão" = bloco com palavras de ≥ 2 parágrafos do gabarito; "mistura" = bloco que alterna parágrafos do gabarito):

| Tipo | V4 (008 → 009) | V9 | V10 | Leitura |
|---|---|---|---|---|
| 1. Fragmentação incorreta | 392 → **271** | 142 → **80** | 268 → **213** | objetivo atingido; todos os ganhos estão em texto nivelado |
| 2. Fusão de parágrafos independentes | 412 → **431 (+19)** | 4 → 4 | 42 → **43 (+1)** | **regressão** (T3-P38): V4 p83 +2, p85 +1, p99 +1, p125 +1, p129 +3, p137 +5, p138 +3, p139 +1, p141 +1, p143 +1; V10 p68 +1 (L02, inclinada −4.6°) |
| 3. Mistura de colunas | 18 → 17 | 52 → 51 | 45 → 40 | sem piora (heurística; a mistura vem da ordem OCR, não da segmentação) |
| 4. Perda de palavras | 0 | 0 | 0 | sequência de palavras idêntica byte a byte nas 65 páginas de segmentação |
| 5. Duplicação | 0 | 0 | 0 | idem |
| 6. Títulos e listas | inalterados | inalterados | inalterados | métricas de títulos e listas idênticas; os marcadores `#`/`-` estão nas mesmas posições da sequência |

- **Onde ficam as fusões**: as 10 páginas do V4 são de **OCR de texto em coluna única, densas** (9 parágrafos, cerca de 800 palavras, 110–200 dpi, ruído, blur, JPEG, listras). Nelas o vão entre parágrafos é pequeno. Exemplo: na **V4 p138** (120 dpi, ruído + blur), a 009 junta "Parágrafo 138.03 … 138.04 … 138.05" num único bloco; a 008 já juntava 138.01 + 138.02.
  - Efeito por página: p137 0.417 → 0.105, p138 0.182 → 0.000, p129 0.696 → 0.400, p83 0.696 → 0.571.
  - As páginas de **duas colunas** do mesmo corpus melhoraram todas (p82, 86, 88, 92, 94, 98, 100, 116, 118, 122, 124, 128, 130, 140, 142).
- **V4 p140 (controle contra fusões)**: `paragraph_detection_f1` 0.000 → **0.417**, `paragraph_boundary_f1` 0.533 → 0.727, quebras 11 → 3, **fusões 0 → 0**. Os parágrafos 140.01–140.03 continuam separados. ✅
- **Divergência com a validação pontual**: as validações de §1.13/§1.14 (79 e 81 páginas) registraram "nenhuma página piorou". As 10 páginas de coluna única que pioraram não fazem parte das listas de controle registradas, ou não foram registradas página a página (a lista exata das 79/81 páginas não consta no documento). **Não é possível atribuir as fusões a t2 ou a t5 sem extração pontual com cada tentativa ligada e desligada.** A V10 p68 (inclinada) mudou, e a t5 não age em texto inclinado (`level_text`), o que sugere a t2 nesse caso.
- **Conclusão**: T3-P20 **confirmado na RUN 009 como melhoria líquida**. A segmentação melhora nos três corpora, sem mudar palavras, ordem, títulos ou listas e sem piorar CER/WER/recall/precisão. O ganho vem com **uma regressão estrutural reproduzível em OCR denso de coluna única** (V4), registrada como **T3-P38** (MÉDIO: sem perda de conteúdo). O limite de tentativas de T3-P20 (5/5) está esgotado.

#### Correções anteriores — preservação

| Problema | Páginas | 008 | 009 | Situação |
|---|---|---|---|---|
| T3-P22 | V10 p9 / p54 / p59 | 0.088 / 0.095 / 0.069 | idênticas (p54 com tabela, células exatas 0.881) | ✅ mantido |
| T3-P22 | V4 p20 / p25 / p26 / p28 | 0.000 / 0.010 / 0.000 / 0.010 | idênticas | ✅ mantido |
| T3-P32 | V9 p20 / p55 / p71 | células exatas 0.950 / 0.841 / 1.000; `numeric` 0.917 / 1.000 / 1.000 | idênticas (texto byte a byte); orientação correta | ✅ mantido |
| T3-P35 | V10 L04–L06 (18 págs.) | CER médio 0.104 (L04 0.138, L05 0.078, L06 0.096) | idêntico (só segmentação mudou em 16 delas) | ✅ mantido |
| T3-P35 | V10 p32 (regressão conhecida) | 0.311, recall 1.000 | 0.311; `paragraph_detection_f1` 0.356 → 0.476 (T3-P20) | ⚠️ ordem continua pior que a 007 (0.213) |
| T3-P28 | V8 p4 / p58 | `€ 98,40`, CER 0.000 | V8 idêntico byte a byte | ✅ mantido |
| T3-P33 | V9 p36 | `partial`, células exatas 0 | igual; a p36 mudou só a segmentação de um parágrafo (`paragraph_detection_f1` 0.083 → 0.087) | ⚠️ sem mudança |
| T3-P22 (resíduo) | V9 p90 (listas) | células exatas 0.896; descrições de 2ª linha fora da grade, 2 itens "☐" falsos | idêntica | ⚠️ sem mudança (V9 `list_item_detection_precision` 0.722) |

#### Critérios da §5.0c (RUN 009) — resumo

| Verificação | Situação |
|---|---|
| T3-P34 | ✅ todos os critérios |
| T3-P20 | ✅ todos os critérios numéricos; ⚠️ regressão de fusões fora dos critérios previstos (T3-P38) |
| Controle (V5–V8 idênticos; V3 idêntico; giradas; T3-P22/T3-P35) | ✅ |
| Desempenho | ✅ dentro do ruído |
| Geral (sem `extract_error`/`metrics_error`/`corpus_changed`; V9 `partial` só por T3-P33) | ✅ |

### §1.16 Rubrica de qualidade 0–100 (avaliação externa; **não é métrica oficial do PDFExtractor**)

Três camadas, que não devem ser confundidas:
1. **Métricas oficiais**: valores de `metrics_v3_*.json`, gerados pelo avaliador `evaluation-3.0.0`.
2. **Notas da rubrica**: transformação fixa das métricas oficiais, definida aqui, igual para todos os documentos e rodadas. Reproduzível a partir dos arquivos de métricas e do `run_summary.json`.
3. **Julgamentos qualitativos**: limitações do gabarito e interpretação. Aparecem separados, em §1.17, e **não** alteram a nota.

**Transformações** (`clamp` em [0, 1]):
- `ALTO(x, piso)` = 100 · clamp((x − piso)/(1 − piso)): maior é melhor; o piso vale 0 e 1.0 vale 100.
- `BAIXO(e, teto)` = 100 · clamp(1 − e/teto): menor é melhor; 0 vale 100 e o teto vale 0.

| Categoria | Peso | Métricas oficiais e fórmula | Justificativa dos limiares |
|---|---:|---|---|
| Fidelidade textual | 20% | ½·BAIXO(`cer_sequential_normalized`, 0.30) + ½·BAIXO(`wer_sequential`, 0.40) | CER 0.30 / WER 0.40 = texto inutilizável na prática; CER 0.15 vale 50 |
| Integridade e cobertura | 15% | 0.6·ALTO(`text_coverage_recall`, 0.70) + 0.4·ALTO(`text_coverage_precision`, 0.70) | perder conteúdo pesa mais que excedente; 70% de cobertura vale 0 |
| Títulos e hierarquia | 10% | 0.6·100·`heading_detection_f1` + 0.2·100·`heading_level_match_rate` + 0.2·BAIXO(`heading_text_cer`, 1.0) | F1 já é uma razão 0–1 |
| Parágrafos e segmentação | 10% | ½·100·`paragraph_detection_f1` + ½·100·`paragraph_boundary_f1` | — |
| Listas e hierarquia | 5% | 0.4·100·`list_group_detection_f1` + 0.4·100·`list_item_detection_f1` + 0.2·100·`list_hierarchy_accuracy` | — |
| Detecção e estrutura de tabelas | 10% | média de 100·{`table_detection_f1`, `table_row_detection_f1`, `table_column_count_accuracy`, `cell_detection_f1`} | — |
| Conteúdo e associação das células | 10% | média de {100·`cell_content_exact_match`, 100·`cell_alignment_accuracy`, BAIXO(`cell_cer`, 1.0), 100·`table_content_f1`} | o alinhamento mede valor na célula/linha errada |
| Dados críticos | 15% | média ponderada de ALTO(x, 0.80): `numeric` 0.40, `currency` 0.25, `dates` 0.15, `percent` 0.10, `identifiers_combined` (CPF/CNPJ) 0.10 | 20% de valores errados = 0; um dado crítico errado tem custo alto |
| Ordem de leitura | 5% | ½·ALTO(`pairwise_reading_order_accuracy`, 0.99) + ½·BAIXO(f, 0.20), em que f = fração das páginas com texto no gabarito que têm `cer_sequential_normalized` > 0.15 **e** `text_coverage_recall` ≥ 0.95 | a métrica de pares fica ≥ 0.999 em todos os corpora e não vê a ordem dentro dos blocos (§2 T3-P10). f é a "assinatura de ordem" usada desde a 003 (§1.3): conteúdo presente, sequência errada |

**Valores ausentes e não aplicáveis**:
- Métrica com status ≠ `ok` (por exemplo `not_applicable`) é retirada, e os pesos internos da categoria são renormalizados. Casos: `percent` no V6; `identifiers_combined` em V3, V4 e V10; `process_numbers`, nunca usado.
- Categoria sem nenhuma métrica aplicável fica **N/A** e o peso é renormalizado na nota geral. Na 009 **todas as 9 categorias são aplicáveis nos 8 corpora**: todos têm títulos, parágrafos, listas, tabelas e dados críticos no gabarito.
- Nenhuma categoria recebe 100 por ausência de exemplos.

**Nota geral**: `nota_geral = Σ(nota_categoria × peso) / Σ(pesos aplicáveis) − penalidade`.

**Penalidade por falha crítica** (regra fixa, definida antes do cálculo e aplicada a todos os documentos e rodadas). Ela existe porque as médias documentais diluem falhas concentradas: uma página inteira perdida move pouco o recall de um documento de 224 páginas.

| Evento (de métricas oficiais por página) | Penalidade |
|---|---|
| Página com ≥ 100 caracteres de referência e **saída com < 50% do volume de caracteres** (`counts.hyp_chars / counts.ref_chars` de `cer_sequential_normalized`): perda de conteúdo | −1.0 por página |
| Página com ≥ 100 caracteres de referência, volume presente e `text_coverage_recall` < 0.50: conteúdo ilegível ou substituído | −0.5 por página |
| `benchmark_status` ≠ `valid` (extração parcial) | −1.0 |
| Teto por documento | −5.0 |

- **Sem penalidade adicional** para valores numéricos corrompidos ou valores na célula errada. Eles já entram em "Dados críticos" (piso 0.80) e em "Conteúdo e associação das células" (`cell_alignment_accuracy`); penalizar de novo seria contagem dupla. Esses casos são listados à parte, em §1.17.
- A regra de ≥ 100 caracteres impede que páginas mínimas, ou páginas cujo gabarito é só uma anotação, gerem penalidade. Exemplo: V3 p138, "*Página intencionalmente em branco*", com saída vazia correta.

**Sensibilidade conhecida**: em V3 e V4, `heading_level_match_rate` = 0.000 (0/13 e 0/62). Isso é **convenção do gabarito**, não erro de nível: o gabarito usa `## Página NNN` como separador e coloca os títulos em `###`, enquanto a extração os emite em `##`, sempre um nível acima e de forma uniforme. Pela regra uniforme, isso custa 20 pontos na categoria Títulos e **2.0 pontos na nota geral** de V3 e V4. Sem esse efeito, V3 ficaria com 95.1 e V4 com 65.1, e a ordem do ranking não muda. A nota oficial da rubrica mantém a métrica como está.

**Notas da RUN 009** (ordenadas da maior para a menor):

| # | Corpus | Texto (20) | Cobertura (15) | Títulos (10) | Parágrafos (10) | Listas (5) | Tabelas: estrutura (10) | Tabelas: células (10) | Dados críticos (15) | Ordem (5) | Base ponderada | Penalidade | **Nota geral** |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | V7 | 99.4 | 99.5 | 98.8 | 97.6 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 99.4 | 0 | **99.4** |
| 2 | V8 | 98.2 | 98.5 | 99.4 | 96.2 | 100.0 | 100.0 | 99.9 | 98.0 | 100.0 | 98.7 | 0 | **98.7** |
| 3 | V6 | 96.7 | 96.5 | 99.0 | 96.3 | 100.0 | 100.0 | 96.2 | 91.6 | 100.0 | 96.7 | 0 | **96.7** |
| 4 | V3 | 94.9 | 95.2 | 68.0 | 92.0 | 97.3 | 99.9 | 99.1 | 98.0 | 98.3 | 93.6 | −0.5 | **93.1** |
| 5 | V5 | 67.3 | 83.5 | 83.8 | 83.8 | 92.1 | 100.0 | 100.0 | 98.5 | 87.1 | 86.5 | 0 | **86.5** |
| 6 | V9 | 77.7 | 96.6 | 50.7 | 64.6 | 84.7 | 79.9 | 63.7 | 85.3 | 58.4 | 75.9 | −1.0 | **74.9** |
| 7 | V10 | 55.4 | 94.9 | 48.9 | 52.5 | 72.7 | 96.5 | 91.2 | 86.9 | 55.2 | 73.6 | 0 | **73.6** |
| 8 | V4 | 72.9 | 68.0 | 37.5 | 47.6 | 77.4 | 85.7 | 69.8 | 69.2 | 99.5 | 68.1 | −5.0 | **63.1** |

Penalidades aplicadas:
- **V3**: −0.5 (p86 ilegível).
- **V9**: −1.0 (`partial`, T3-P33).
- **V4**: −5.0 pelo teto. O valor bruto seria −6.0: p167 e p168 com perda (−2.0) e oito páginas ilegíveis (p103, 104, 153, 163, 164, 165, 166, 172; −4.0).
- **Pesos**: os 9 aplicáveis em todos os corpora, sem renormalização da nota geral.

**Notas por rodada** (mesma rubrica, recalculada a partir das métricas oficiais de cada rodada; avaliador e políticas idênticos em 001–009):

| Corpus | 003 | 004 | 005 | 006 | 007 | 008 | 009 | Δ 008 → 009 | Motivo da mudança 008 → 009 |
|---|---|---|---|---|---|---|---|---|---|
| V3 | 83.1 | 87.1 | 92.9 | 93.1 | 93.1 | 93.1 | 93.1 | 0.0 | saída idêntica |
| V4 | 46.7 | 60.7 | 62.0 | 62.1 | 62.2 | 62.8 | 63.1 | +0.2 | Parágrafos 44.9 → 47.6 (T3-P20; inclui as fusões de T3-P38); Ordem 100.0 → 99.5 (efeito de casamento de blocos, sem mudança real de ordem) |
| V5 | 86.5 | 86.5 | 86.5 | 86.5 | 86.5 | 86.5 | 86.5 | 0.0 | saída idêntica |
| V6 | 96.8 | 96.8 | 96.8 | 96.7 | 96.7 | 96.7 | 96.7 | 0.0 | saída idêntica |
| V7 | 99.4 | 99.4 | 99.4 | 99.4 | 99.4 | 99.4 | 99.4 | 0.0 | saída idêntica |
| V8 | 98.6 | 98.6 | 98.6 | 98.7 | 98.7 | 98.7 | 98.7 | 0.0 | saída idêntica |
| V9 | 57.9 | 69.5 | 69.5 | 72.9 | 73.5 | 73.8 | 74.9 | +1.1 | Parágrafos 58.9 → 64.6 (T3-P20); Tabelas: células 61.7 → 63.7 e estrutura 78.4 → 79.9 (T3-P34 p58); Texto 77.2 → 77.7; Ordem 57.9 → 58.4 |
| V10 | n/d | 57.0 | 57.0 | 68.0 | 69.7 | 73.3 | 73.6 | +0.3 | Parágrafos 49.1 → 52.5 (T3-P20) |

- **003 do V10 = n/d**: a rodada oficial terminou em `metrics_error` (T3-P15). As métricas recalculadas no scratchpad em 2026-11-12 não estão em `output/` e **não** foram usadas.
- **001/002** não entram: configuração diferente da canônica (T3-P00) e sem V10.
- As notas subjetivas antigas não foram usadas.
- **Maiores saltos históricos**:
  - V4: 003 → 004 (+14.0; T3-P10, T3-P23, T3-P02).
  - V9: 003 → 004 (+11.6; T3-P10) e 005 → 006 (+3.4; T3-P18).
  - V10: 005 → 006 (+11.0; T3-P18) e 007 → 008 (+3.6; T3-P22/T3-P35).
  - V3: 003 → 005 (+9.8; T3-P02/T3-P25/T3-P26).
  - Desde a 006, V5–V8 estão estáveis a ±0.1.

### §1.17 Avaliação individual dos documentos (RUN 009)

Notas = rubrica de §1.16 (não oficiais). "Edições" = parcela da distância de edição do documento (`cer_sequential_normalized`). Os documentos estão em ordem de nota.

#### V7 — 99.4 (1º)

- **Documento**: 80 páginas. Documento institucional fictício, nativo, para regressão de capacidades consolidadas: 28 famílias (compras, sumário, tabelas largas de 12 colunas, tabelas multipágina, Unicode, conteúdo de borda); carimbos raster.
- **Status**: `success`/`valid`, nenhuma página recuperada.
- **Métricas oficiais**: CER 0.001, WER 0.003; recall 0.999, precisão 0.997.
- **Notas**: Texto 99.4 · Cobertura 99.5 · Títulos 98.8 · Parágrafos 97.6 · Listas 100 · Tabelas: estrutura 100 · Tabelas: células 100 · Dados 100 · Ordem 100.
- **Pontos fortes**:
  - 42/42 tabelas.
  - 2010/2010 células exatas.
  - 1075 números, 240 moedas e 329 datas, todos corretos.
- **Problemas**: a p1 concentra 100% das edições: título com espaçamento entre letras (CER 0.754, recall 0.697; T3-P05, nativo, fora do escopo).
- **Perda ou corrupção**: nenhuma além da p1.
- **Mudanças**: saída idêntica à 008.
- **Limitações**: corpus de regressão, pouco adversarial; só 3 identificadores.

#### V8 — 98.7 (2º)

- **Documento**: 90 páginas. Dossiê sintético que combina os acertos do V7 com casos adversariais do V6: 45 famílias (identificadores, tabelas sem borda, conflito de camadas, OCR financeiro, rowspan HTML); p22/p25 com `/Rotate 180`.
- **Status**: `success`/`valid`; p45/p70 recuperadas.
- **Métricas oficiais**: CER 0.006, WER 0.007; recall 0.994, precisão 0.998.
- **Notas**: Texto 98.2 · Cobertura 98.5 · Títulos 99.4 · Parágrafos 96.2 · Listas 100 · Tabelas: estrutura 100 · Tabelas: células 99.9 · Dados 98.0 · Ordem 100.
- **Pontos fortes**:
  - 81/81 tabelas.
  - 1318/1320 células exatas.
  - `€ 98,40` preservado em p4/p58 (T3-P28).
- **Problemas**: p46/p47 (títulos com espaçamento, `tracked_heading`; T3-P05) concentram 84.7% das edições (CER 0.20, recall 0.79, `numeric` 0.67/0.75).
- **Dados críticos**: `numeric` 0.991 (13 FP, 6 FN); 1 data ausente.
- **Mudanças**: saída idêntica à 008.
- **Limitações**: nenhuma relevante.

#### V6 — 96.7 (3º)

- **Documento**: 95 páginas. Dossiê técnico em três volumes, nativo, com desafios adversariais: 33 famílias (notação matemática, varredura de página inteira, tarja, varredura inclinada, tabelas longas, sem borda).
- **Status**: `success`/`valid`; p6/p42 recuperadas.
- **Métricas oficiais**: CER 0.008, WER 0.016; recall 0.988, precisão 0.992.
- **Notas**: Texto 96.7 · Cobertura 96.5 · Títulos 99.0 · Parágrafos 96.3 · Listas 100 · Tabelas: estrutura 100 · Tabelas: células 96.2 · Dados 91.6 · Ordem 100.
- **Pontos fortes**:
  - 66/66 tabelas.
  - Listas 52/52 grupos.
  - Nenhuma página com CER > 0.15.
- **Problemas**:
  - Varreduras p3/p33/p89: recall 0.86, `numeric` 0.67–0.77, 31% das edições. Limite de reconhecimento, não registrado como problema próprio.
  - Células exatas 0.938 (63 células com diferenças pequenas; `cell_cer` 0.014).
  - `numeric` < 0.80 em 11 páginas, com poucas ocorrências por página.
- **Dados críticos**: `numeric` 0.965 (33 FP, 18 FN); datas 0.993 (3 FN).
- **Mudanças**: saída idêntica à 008.
- **Limitações**: `percent` não aplicável (sem porcentagens no gabarito). O gabarito declara que a informação encoberta continua acessível na camada nativa.

#### V3 — 93.1 (4º)

- **Documento**: 144 páginas. Corpus integrado: 32 de texto nativo, 35 de tabela nativa, 33 de OCR puro, 16 adversariais, 8 híbridas, 7 de OCR de tabelas, controles e protocolo. Inclui páginas rotacionadas (p96–p99), tabela girada (p116), caracteres confundíveis (p86) e página em branco (p138).
- **Status**: `success`/`valid`.
- **Métricas oficiais**: CER 0.013, WER 0.024; recall 0.983, precisão 0.990.
- **Notas**: Texto 94.9 · Cobertura 95.2 · Títulos 68.0 · Parágrafos 92.0 · Listas 97.3 · Tabelas: estrutura 99.9 · Tabelas: células 99.1 · Dados 98.0 · Ordem 98.3; penalidade −0.5.
- **Pontos fortes**:
  - 77/77 tabelas.
  - 1273/1292 células exatas.
  - 236/236 moedas, 46/46 porcentagens, 31/31 datas.
- **Problemas**:
  - p116 (tabela girada, recall 0.58, 27.5% das edições; T3-P32).
  - p96 (separador "Página 096" mantido junto ao corpo, 11.1%; T3-P25).
  - p99 (recall 0.83), p86 (confundíveis, recall 0.35 — penalizada), p103 (recall 0.50).
  - Títulos: recall 0.81 (13/16) e nível 0/13 (convenção do gabarito).
- **Perda ou corrupção**: p86, p103 e p116 parciais; `numeric` < 0.80 em p86/p92/p118.
- **Mudanças**: saída idêntica à 008 (T3-P20 não afetou nenhuma região OCR do V3).
- **Limitações do gabarito**:
  - A p138 é "página em branco" com uma frase anotada no gabarito. A saída vazia está correta, mas a página responde por 19.2% das edições (CER do V3 sem ela: 0.010 em vez de 0.013).
  - O gabarito do V3 declara não ser um benchmark exato de OCR.
  - Níveis de título com deslocamento uniforme (§1.16).

#### V5 — 86.5 (5º)

- **Documento**: 42 páginas. Document AI V5: 19 famílias × 2 repetições (ordem de leitura em colunas e sidebar, tabela multipágina, contrato, tipografia nativa, listas nativas, ordem interna embaralhada, assinatura híbrida, watermark).
- **Status**: `success`/`valid`.
- **Métricas oficiais**: CER 0.109, WER 0.116; recall 0.921, precisão 0.995.
- **Notas**: Texto 67.3 · Cobertura 83.5 · Títulos 83.8 · Parágrafos 83.8 · Listas 92.1 · Tabelas: estrutura 100 · Tabelas: células 100 · Dados 98.5 · Ordem 87.1.
- **Pontos fortes**:
  - 22/22 tabelas.
  - 530/530 células exatas.
  - Dados críticos quase perfeitos (`numeric` 0.993).
- **Problemas**:
  - **T3-P40 (novo; reclassificado em §1.19 como limitação do gabarito: os títulos não existem no PDF)**: em 12 páginas (p4, 5, 6, 17, 18, 20, 23, 26, 28, 32, 36, 38) o **título H1 e o subtítulo** da página estão ausentes. São blocos nativos `heading` na faixa superior. Recall dessas páginas 0.70–0.82; ausentes desde a 001. Isso explica toda a perda de recall atribuída antes a T3-P07.
  - p3/p30: CER 0.27/0.35 com recall 1.0, ou seja, ordem (T3-P07).
  - 14 páginas com CER > 0.15.
- **Perda ou corrupção**: 24 linhas de título/subtítulo; nenhum valor crítico corrompido.
- **Mudanças**: saída idêntica à 008.
- **Limitações**: corpus pequeno (42 páginas, 10 porcentagens, 5 identificadores). Cada família aparece só duas vezes, então um único defeito afeta 5% do corpus.

#### V9 — 74.9 (6º)

- **Documento**: 96 páginas. Document AI V9 (`needs_review`): 58 mistas, 22 nativas e 16 raster; 12 inclinadas, 20 multicoluna, tabelas raster giradas e inclinadas, duas tabelas lado a lado, formulários.
- **Status**: `partial_success`/`partial` (só p36; T3-P33); 8 páginas recuperadas.
- **Métricas oficiais**: CER 0.072, WER 0.082; recall 0.990, precisão 0.989.
- **Notas**: Texto 77.7 · Cobertura 96.6 · Títulos 50.7 · Parágrafos 64.6 · Listas 84.7 · Tabelas: estrutura 79.9 · Tabelas: células 63.7 · Dados 85.3 · Ordem 58.4; penalidade −1.0.
- **Pontos fortes**:
  - Recall alto.
  - Tabelas giradas p20/p55/p71 corretas.
  - p58 recuperada como tabela (T3-P34).
  - Ganho de parágrafos (T3-P20).
- **Problemas**:
  - Títulos: F1 0.426 (recall 0.351, precisão 0.541; T3-P06).
  - Ordem em páginas nativas multicoluna: p51 0.552 (11.2% das edições), p43 0.470 (7.1%), p5 0.317 (5.4%) (T3-P22, encerrado).
  - Inclinadas: p9 0.394 (8.1%), p17 0.269.
  - Tabelas: 49/61 detectadas; células exatas 0.601; alinhamento 0.632.
  - 15/96 páginas com assinatura de ordem.
- **Piores páginas**: p51, p9, p43, p62 (0.465), p5, p72 (0.301), p92 (0.286), p17.
- **Perda ou corrupção**:
  - p36: tabela girada ilegível, recall 0.845, `numeric` 0.348 (T3-P32/T3-P33).
  - p92: recall 0.842 (T3-P32).
  - p66: recall 0.789, `currency` 0.632, valores das linhas 004–006 truncados (T3-P34).
  - **p58**: três valores monetários corrompidos na tabela (T3-P39).
  - **p62/p72**: tabelas lado a lado desmontadas, com valores separados das linhas (células exatas 0.194/0.000; T3-P37).
  - p14: duas tabelas fundidas numa só (linhas corretas).
- **Dados críticos**: `numeric` 0.953 (59 FN), `currency` 0.962 (15 FN).
- **Mudanças**: +1.1 ponto; ver §1.15 (T3-P34 em p58/p44/p49; T3-P20 em 16 páginas).
- **Problemas documentados que ainda afetam**: T3-P06, T3-P22, T3-P32, T3-P33, T3-P34, T3-P37, T3-P39, T3-P08.
- **Limitações**: `duplicate_excess_rate` 0.417 não indica duplicação (T3-P19). Nas páginas de ordem nativa o recall é 1.0: o texto está todo lá.

#### V10 — 73.6 (7º)

- **Documento**: 84 páginas. Document AI V10: 50 raster, 25 mistas e 9 nativas; cenários L01–L06 de 3+2 colunas (L01/L02 raster inclinado ±4–5°, L03 nativo, L04–L06 raster nivelado), N01–N06 e controles; 19 páginas sem texto (corretamente vazias).
- **Status**: `success`/`valid`.
- **Métricas oficiais**: CER 0.141, WER 0.169; recall 0.991, precisão 0.974.
- **Notas**: Texto 55.4 · Cobertura 94.9 · Títulos 48.9 · Parágrafos 52.5 · Listas 72.7 · Tabelas: estrutura 96.5 · Tabelas: células 91.2 · Dados 86.9 · Ordem 55.2.
- **Pontos fortes**:
  - Nenhuma página com recall < 0.90.
  - 19/20 tabelas, células exatas 0.895.
  - Moedas 113/113.
  - L03–L06 com CER 0.07–0.14.
- **Problemas**:
  - **L01/L02 inclinadas = 56% das edições** (CER médio 0.271/0.321; p68 0.378, p48, p25, p75, p18, p83, p53, p13, p31, p45, p62; T3-P35 inclinadas).
  - Títulos: recall 0.298 (T3-P06).
  - Listas: precisão de grupos 0.354, 42 grupos falsos (T3-P31).
  - Parágrafos: F1 0.458 (precisão 0.353).
  - p32: ordem 0.311 (T3-P35).
- **Perda ou corrupção**: nenhuma perda. `numeric` 0.945 por **excesso** (135 FP contra 10 FN; origem não diagnosticada nesta etapa).
- **Mudanças**: +0.3 (T3-P20 em 20 páginas). Uma fusão nova na p68 (inclinada).
- **Problemas documentados que ainda afetam**: T3-P35, T3-P31, T3-P06, T3-P20/T3-P38.
- **Limitações**: 19 páginas de gabarito vazio não contam no texto. A métrica de ordem por pares (0.9995) não vê a ordem dentro dos blocos.

#### V4 — 63.1 (8º)

- **Documento**: 224 páginas. Corpus de estresse OCR: 10 de protocolo, 32 de texto nativo denso, 40 de tabela nativa, **64 de OCR de texto** (p81–p144, uma e duas colunas, 72–300 dpi, ruído, blur, JPEG, sombra, listras), **40 de OCR de tabelas** (p145–p184, 120 dpi), 32 adversariais, 6 controles.
- **Status**: `success`/`valid`; p148/p164/p180 recuperadas.
- **Métricas oficiais**: CER 0.054, WER 0.145; recall 0.894, precisão 0.920.
- **Notas**: Texto 72.9 · Cobertura 68.0 · Títulos 37.5 · Parágrafos 47.6 · Listas 77.4 · Tabelas: estrutura 85.7 · Tabelas: células 69.8 · Dados 69.2 · Ordem 99.5; penalidade −5.0 (teto).
- **Pontos fortes**:
  - Páginas nativas quase perfeitas (p20/p25/p26/p28 ≤ 0.010; T3-P22).
  - p213/p214 recuperadas (T3-P27).
  - Rotação p109–p114 corrigida.
  - Sem problema de ordem.
- **Problemas**:
  - Reconhecimento OCR degradado: recall ≈ 0.84 no OCR de texto (T3-P21); p104 blur, recall 0.339, 7.8% das edições.
  - OCR de tabelas a 120 dpi: CER 0.2–0.9, células exatas 0.634.
  - Títulos: recall 0.335, nível 0 por convenção.
  - Parágrafos: F1 0.410, e 19 fusões novas (T3-P38).
  - Dados críticos: `numeric` 0.897, com 1642 números a mais inventados pelo OCR em identificadores ruidosos e 254 ausentes; `currency` 0.968; `dates` 0.973.
- **Piores páginas**: p104, p118, p88, p142, p130, p103, p98, p100.
- **Perda ou corrupção**:
  - **p167/p168: tabela inteira ausente desde a 002** (T3-P36, perda silenciosa).
  - Ilegíveis: p103, p104, p153, p163–p166, p172.
  - p156: recall 0.647, efeito antigo de T3-P18.
- **Mudanças**: +0.2. Das 27 páginas que mudaram, 17 só perderam quebras indevidas e 10 ganharam fusões (T3-P38). Sem mudança de texto.
- **Problemas documentados que ainda afetam**: T3-P21, T3-P36, T3-P38, T3-P06, T3-P29, T3-P12 (2460 s), T3-P04.
- **Limitações**:
  - Nível de título com deslocamento uniforme.
  - Parte do recall ≈ 0.85 pode ser diferença de projeção do avaliador (T3-P21, não confirmada).
  - O gabarito de OCR adversarial exige caracteres confundíveis (O/0/I/l/1, rn/m) que o OCR a 120 dpi não distingue.

### §1.18 Priorização pós-009 (ranking dos problemas abertos)

Critérios, em ordem: (1) perda de conteúdo; (2) corrupção de dados críticos; (3) severidade estrutural; (4) frequência e número de páginas; (5) impacto na nota (rubrica); (6) possibilidade de resolução; (7) risco de regressão. A nota geral do documento **não** foi usada como critério isolado. Corpora prioritários: V10, V9 e V4.

| # | Problema | Classe | Páginas | Impacto na nota (estimado) | Componente / risco | Tentativas | Recomendação |
|---|---|---|---|---|---|---|---|
| 1 | **T3-P36** tabela OCR descartada silenciosamente | perda (silenciosa) | V4 p167, p168 | baixo na nota (o V4 já está no teto de penalidade); alto em confiabilidade: o manifesto diz `valid`/`clean` | a identificar (tabela OCR; provável integração compartilhada) | 0 / 5 | **P0** — diagnóstico primeiro; perda silenciosa é a classe mais grave |
| 2 | **T3-P40** título/subtítulo omitidos | perda (silenciosa) | V5, 12 de 42 páginas | V5 Cobertura 83.5 → ~95 e Texto +; ≈ +3–4 na nota do V5 | layout nativo (compartilhado; V5–V8 consolidados em risco) | 0 / 5 | P1 — **exige autorização** para a camada nativa; diagnóstico sem alterar código |
| 3 | T3-P34 (p66) e T3-P32 (p36/p92): tabelas raster ilegíveis ou truncadas | perda parcial + dados | V9 p66, p36, p92 | V9 Dados/Células | detecção visual / integração (compartilhada) | 4/5; 5/5 (encerrado) | P1 (p66, 1 tentativa); p36/p92 só com nova aprovação |
| 4 | **T3-P39** valores colados em tabela raster inclinada | corrupção de dados críticos | V9 p58 (3 valores) | pequeno (1 página); grave por natureza | Tesseract (provavelmente a montagem de linhas, como T3-P28) | 0 / 5 | **P0** — causa provavelmente localizada e de baixo risco |
| 5 | **T3-P37** tabelas lado a lado | associação valor ↔ linha perdida | V9 p62, p72 (p14 fundida) | V9 Células/Tabelas | detector de tabelas nativo (compartilhado) | 0 / 5 | P1 — risco em V5–V8 |
| 6 | **T3-P35 inclinadas** (L01/L02) | estrutura/ordem (sem perda) | V10, 12 páginas = **56% das edições do V10** | **o maior**: V10 Texto 55.4 e Ordem 55.2; reduzir L01/L02 ao nível de L04–L06 levaria o CER do V10 de 0.141 para ≈ 0.09 (≈ +4–5 na nota) | `ocr/reconstruct.py` e layout OCR (Tesseract); risco em T3-P31/T3-P18 | 2 / 5 | **P0** para o corpus prioritário |
| 7 | T3-P06 títulos em páginas OCR | estrutura | V10, V9, V4 (≈ 430 títulos ausentes) | Títulos 37.5–50.7 em três corpora (≈ +2–3 por corpus se o recall dobrar) | layout OCR | 0 / 5 | P1 |
| 8 | **T3-P38** fusões de parágrafos (regressão) | estrutura (sem perda) | V4, 10 páginas | pequeno (V4 Parágrafos) | `segment_ocr_paragraphs` (regiões OCR) | 0 / 5 | P1 — atribuir a t2/t5 antes de mexer; risco de desfazer o ganho de T3-P20 |
| 9 | T3-P22 resíduo (V9 p51/p43/p5) | ordem nativa | V9, 3 páginas = 24% das edições do V9 | V9 Texto/Ordem | nativo compartilhado | 5 / 5 (encerrado) | P2 — exige novo problema |
| 10 | T3-P21 recall do OCR degradado (V4) | limite de reconhecimento ou avaliador | V4, ~100 páginas | V4 Cobertura 68.0 | Tesseract / avaliador | 0 / 5 | P2 — investigar sem alterar o avaliador |
| 11 | T3-P31, T3-P07, T3-P33, T3-P29, T3-P05, T3-P12, T3-P04 | estrutura / status / desempenho | — | pequeno | — | ver quadro | P2–P3 |

**Os três problemas mais importantes para o próximo ciclo** (recomendação; a escolha é do responsável):
1. **T3-P36 — perda silenciosa de tabela inteira (V4 p167/p168)**.
   - É a única perda de volume de conteúdo nos oito corpora, e o executor a reporta como `valid`/`clean`.
   - Existe desde a 002 (na 001 as linhas latinas eram lidas). Portanto foi introduzida pela ativação de tabelas, um mecanismo que pode afetar qualquer tabela OCR rejeitada.
   - Primeiro passo: diagnóstico por etapa sem alterar código. Risco desconhecido até o diagnóstico.
2. **T3-P39 — valores monetários corrompidos na V9 p58**.
   - É dado crítico errado dentro de uma tabela agora detectada (`R$ 2.333,52333,92`).
   - O padrão é o mesmo de T3-P28 (duas leituras coladas), cuja correção é só Tesseract e de baixo risco.
   - Junto com T3-P34 p66 (1 tentativa restante), cobre a corrupção de dados críticos do V9.
3. **T3-P35, páginas inclinadas (V10 L01/L02)**.
   - Não há perda de conteúdo, mas são 56% das edições do corpus prioritário. É o maior ganho possível de nota (V10 ≈ +4–5) com código só do Tesseract (`reconstruct`/layout OCR).
   - Restam 3 tentativas.
   - O risco conhecido (T3-P31, perda por deduplicação na tentativa 1 de T3-P35) exige controles de recall por página.

**Ficam fora do top 3, com justificativa**:
- **T3-P40** é perda de conteúdo e mais frequente que T3-P36, mas está na camada nativa compartilhada (V5–V8 consolidados), fora do escopo autorizado até agora, e não está nos corpora prioritários. Recomenda-se **autorização explícita** e começar só com o diagnóstico.
- **T3-P38** é regressão, mas estrutural e sem perda; deve entrar como **controle obrigatório** de qualquer trabalho de parágrafos.
- **T3-P06** tem impacto de nota amplo, mas é só estrutural.

**Limites de tentativas** (contagem atualizada pela evidência documental de §1.14, §2 e §3):
- T3-P34: 4/5.
- T3-P32: 5/5 (encerrado; a t5 foi revertida).
- **T3-P20: 5/5** (encerrado). As tentativas 3–5 de 2026-11-17 estão registradas em §1.14 e na §2; o pedido da auditoria citava 2/5, contagem anterior a esse trabalho.
- T3-P22: 5/5 (encerrado).
- T3-P35: 2/5.
- Novos problemas: T3-P36–T3-P40, 0/5.

### §1.19 Ciclo pós-009: T3-P38, T3-P36, T3-P39 e T3-P40 (2026-11-18)

**Resultados pontuais**: extrações só de subconjuntos de páginas; **nenhum resultado desta seção foi confirmado em rodada completa**.

**Método**:
- **Harness** no scratchpad da sessão. Usa `build_config()` do executor (balanced, pt-BR, adaptive, Tesseract, 8 threads) com `page_indices`, confere que só as páginas pedidas foram processadas e gera o Markdown paginado com os números originais.
- **Métricas**: `compute_metrics.py` oficial no escopo `selected_pages`.
- **Variantes**: aplicadas em memória (monkeypatch). As alternativas de T3-P38 **não** foram gravadas no repositório.
- **Comparações**: sempre entre variantes no **mesmo** subconjunto e contexto. Em subconjunto, o cabeçalho repetido de algumas páginas não é suprimido (falta contexto de documento), então os resultados não são comparáveis byte a byte com `output/tesseract/009`.
- **Concorrência**: no máximo 3 processos simultâneos. Uma primeira bateria com 12 processos (12 × 4 threads OpenMP em 12 núcleos) praticamente parou e foi descartada.

#### T3-P38: atribuição da regressão de T3-P20 (não corrigida neste ciclo)

**Atribuição**: as mesmas linhas OCR com o código anterior a T3-P20 (`ad43cb1`), só t2 (`b1202ee`), só t5 e t2+t5 (código da RUN 009). V4 16 páginas, V10 6, V9 4. Em todas as variantes a sequência de palavras de todas as páginas é idêntica.

| Variante | V4: quebras reais perdidas (fusões) | V4: quebras falsas | Observação |
|---|---|---|---|
| antes de T3-P20 | 6 | 39 | — |
| só t2 | 21 | 2 | fusões em p83, p99, p125, p129, p137 (2 → 7), p138 (4 → 7), p141, p143; **V10 p68 (+1)** |
| só t5 | 7 | 2 | +1 fusão (p137); ganhos de V4 p82/p116/p124/p140, V10 p11/p32/p84 e V9 p31/p67 iguais aos de t2+t5 |
| t2 + t5 (RUN 009) | 25 | 1 | a combinação acrescenta fusões em p83, p85, p129, p139 |

**Causa**:
- Em OCR denso de coluna única (V4, 110–200 dpi), o vão real entre parágrafos é de 2.75–3.5 pt, com vão mediano entre linhas de 1.25–1.75 pt e altura típica de cerca de 8 pt.
- O limite da t2, `m + 0.2·h` (≈ 3.35 pt), fica **acima** desses vãos reais.
- A t5 (descendente ausente, só em texto nivelado) reduz ainda mais o vão medido e soma fusões à t2.
- Variação de caixa e quebra real de parágrafo têm a mesma ordem de grandeza nessas páginas.

**Candidatas avaliadas**:
- **h1** (simulação, sem extração; não conta como tentativa): quebrar na faixa ambígua só quando a linha anterior termina frase. Fusões no V4 25 → 8.
  - Descartada: no V10 as frases que abririam parágrafo aparecem tanto no início quanto no meio de parágrafos do gabarito, o que geraria quebras falsas.
- **h2 (tentativa 1 de T3-P38, em memória)**: a tolerância da t2 passa a ser a variação de altura medida no bloco (altura típica − 10º percentil, no máximo 0.2·h).
- **h3 (tentativa 2, em memória)**: h2 sem tolerância em texto inclinado.
- **"Só t5"** (reverter t2): variante de atribuição, candidata a tentativa 3.

**Bateria das candidatas**: as 63 páginas que mudaram na RUN 009 mais controles (V4 27, V9 21, V10 25), código atual como base.

| Candidata | V4: quebras / fusões / mistura | V4 escopo: `paragraph_detection_f1` / `boundary_f1` / `boundary_recall` | V9 | V10 | Palavras, CER, recall |
|---|---|---|---|---|---|
| RUN 009 (t2+t5) | 104 / 27 / 7 | 0.311 / 0.613 / 0.878 | 26 / 3 / 2 | 112 / 22 / 24 | — |
| **só t5** | 108 / **9** / 7 | **0.352 / 0.646 / 0.959** | idêntico | 111 / 21 / 25 (p68: 2 → 1 fusão) | idênticos em todas as páginas |
| h2 | 104 / 16 / 7 | 0.347 / 0.637 / 0.928 | idêntico | idêntico | idênticos |
| h3 | não comparável | — | — | — | — |

- A **h3** rodou num subconjunto diferente (só L01/L02). O contexto muda o resultado, e a direção observada (quebras 47 → 12) contradiz a hipótese, que deveria gerar mais quebras. Não serve para decidir.
- **Decisão**: **nenhuma alteração aplicada**. O código de segmentação continua idêntico ao da RUN 009 (t2 + t5).
  - "Só t5" é a melhor candidata: elimina 18 de 27 fusões e mantém os ganhos de V9 e V10. Em contrapartida, acrescenta 4 quebras indevidas em páginas de duas colunas do V4 (p98, p122, p128, p130).
  - Também remove a tolerância da t2, que é coberta por um teste de T3-P20 (V10 p11). Por isso precisa de ciclo próprio com validação progressiva (§5).
- **Custo**: 24 extrações, 337 extrações de página e 2.4 h de extração somada (cerca de 2 h 10 de relógio, com 3 processos), mais ~25 min perdidos na bateria descartada.

#### T3-P36: tabela OCR suprimida silenciosamente (V4 p167/p168)

**Rastreamento** (V4 p167, extração da página com o código da RUN 009): o conteúdo **não** se perde na detecção, na validação nem na integração.
- A grade visual 7 × 3 é detectada (`page-167:visual-grid`, `VISUAL_MODEL`) e as células saem quase todas corretas: "Idioma | Texto | Controle", "Português | ação, órgão, coração | PT-TIZA" … "Cirílico | MockBa, naHHbie, aHanv3 | RU-TIZA".
- O bloco TABLE é montado normalmente e entra no registro de conservação.
- **Ponto exato do descarte**: `ocr/tesseract/postprocess.py::_suppress_rotated_table_garble`, executado em `apply_tesseract_postprocessors` **depois** de `assemble_document`.
  - A regra suprime o bloco TABLE inteiro quando existe **um** token alfabético de 7 ou mais letras com "Q" não seguido de "U".
  - Na p167 o token é a leitura latina da linha grega ("αβγΔΩπλ" → "aByAQnA").
- **Por que o manifesto dizia `valid`/`clean`**: o registro de conservação já tinha contabilizado a linha como pertencente à tabela, e o pós-processamento não registra nada. Nenhum aviso, nenhum `partial_reason`, e a página mantém conteúdo (o controle).
- **Uso histórico da regra**: nas rodadas 005–009 ela dispara exatamente 2 vezes, nos 8 corpora (V4 p167/p168); na 002 também em V3 e V9. As tabelas giradas que motivaram a regra (V3 p99/p116) são orientadas desde a 004 pelo retry de quarto de volta (T3-P02/T3-P32).

**Tentativa 1 (mantida)**: a regra deixa de suprimir; mantém a tabela e registra o aviso `suspected_ocr_garble` na página e no documento.
- Além disso, `_report_postprocess_suppressions` registra em `facts` toda supressão do pós-processamento. Qualquer supressão que não seja mobília reconhecida (cabeçalho corrente, separador "Página NNN", marcador do corpus, artefato IRINF) vira aviso `content_suppressed_by_postprocess`.
- **Fallback para texto não foi necessário**: a estrutura existe e o conteúdo está nela. A correção remove a supressão em vez de criar outra cópia, então não há risco de duplicação.

| Página | CER | Recall | Precisão | Tabela | Observação |
|---|---|---|---|---|---|
| V4 p167 | 0.901 → **0.194** | 0.095 → **0.548** | 1.000 → 0.605 | detectada 7 × 3 (`table_detection_f1` 1.000; células exatas 0 → 0.476) | linhas grega e cirílica ilegíveis (limite do `por` do Tesseract) |
| V4 p168 | 0.853 → **0.531** | 0.071 → **0.476** | 0.214 → 0.513 | detectada 7 × 2 | a coluna "Idioma" (na sombra) **não é lida pelo Tesseract** nem na 001; limite de reconhecimento, não perda do pipeline |

#### T3-P39: valores corrompidos na V9 p58

**Rastreamento** (tokens da região, caixas, confiança e `layout_line_id`; o prefixo identifica a chamada do Tesseract): cada célula corrompida recebe **duas leituras do mesmo ponto vindas de chamadas diferentes**, com a caixa de uma quase toda dentro da outra.

| Célula (gabarito) | Leitura boa | Leitura conflitante | Relação | Saída 009 |
|---|---|---|---|---|
| `R$ 2.333,52` | "2.333,52" 0.966 | "333,92" 0.672 (outro recorte) | contida; IoU 0.57 < 0.6 de T3-P28 | `R$ 2.333,52333,92` |
| `R$ 845,26` | "R$" 0.959 | "R$E" 0.315 | a ruidosa envolve a boa | `R$ER$ 845,26` |
| `R$ 1.036,35` | "1.036,35" 0.964 | "1." 0.000 | contida | `R$ 1.036,351.` |
| `R$ 388,92` | "R$" 0.961 | "lrs:" 0.140 | a ruidosa envolve a boa | `lrs:R$ 388,92` |
| `Conferido` | "Conferido" 0.911 | "ota" 0.345, "Es" 0.148, "Do" 0.148 | contidas | `Conferidoota Es Do` |

- Não é duplicação de token nem montagem errada da grade: valores e linhas estão certos, a montagem só cola as duas leituras.
- **Tentativa 1 (mantida)**: `_drop_conflicting_rereads` (`ocr/reconstruct.py`, só tokens com identidade de linha, ou seja, Tesseract) passa a tratar **contenção** entre chamadas diferentes, quando ≥ 80% da caixa menor está dentro da maior:
  - a leitura interna menos confiável sai (leitura parcial);
  - a externa sai só se tiver confiança < 0.5 e menor que a interna (ruído sobre uma palavra bem lida);
  - duas leituras confiantes ficam ambas;
  - leituras da mesma chamada e de outros motores não são tocadas.
- Não há substituição de texto ou número específico do corpus.

| Página | CER | Recall | Precisão | Células exatas | `numeric` / `currency` | Saída |
|---|---|---|---|---|---|---|
| V9 p58 | 0.063 → **0.046** | 0.890 → 0.922 | 0.919 → 0.966 | 0.646 → **0.750** | 0.741 → 0.769 / 0.857 → **1.000** | `R$ 388,92`, `R$ 2.333,52`, `R$ 845,26`, `R$ 1.036,35`, `Conferido`; estrutura de T3-P34 (8 × 6) preservada |
| V9 p66 | 0.224 → 0.221 | 0.789 → 0.803 | 0.703 → 0.720 | 0 (tabela continua não detectada, T3-P34) | iguais | ruído removido ("ValorV:" → "Valor", "FoR$" → "R$", ":!", "ER") |

- **Leituras removidas**: no subconjunto de validação, só na V9 (p58, p66): `lrs:`, `R$E`, `333,92`, `1.`, `ota`, `Es`, `Do`, `Fo`, `V:`, `:!`, `ER`. Em todos os casos ficou uma leitura mais confiável do mesmo ponto.
- Nenhuma leitura foi removida em V3, V4, V5, V6, V7, V8 ou V10.
- O `numeric` restante da p58 (0.769) vem de valores **não lidos** ("Rubrica" e "Qtde." das linhas 005–007), não de valores corrompidos.

#### Validação conjunta de T3-P36 + T3-P39 (pontual)

72 páginas dos oito corpora, extraídas com o código anterior e com as correções no mesmo contexto. As correções foram medidas em memória e **depois no código do repositório**, com saída idêntica byte a byte entre as duas.

| Corpus | Páginas | Mudaram |
|---|---|---|
| V3 | 1, 20, 60, 80, 96, 97, 99, 116, 138 | nenhuma |
| V4 | 85, 107, 110, 113, 145, 146, 150, 156, 164, 165, 166, 167, 168, 172, 180, 187, 190, 213 | só p167, p168 |
| V5 | 4, 10, 17, 19, 24 | nenhuma |
| V6 | 3, 21, 31, 33, 89 | nenhuma |
| V7 | 1, 25, 53 | nenhuma |
| V8 | 4, 22, 46, 58 | nenhuma (`€ 98,40` mantido) |
| V9 | 3, 8, 9, 20, 23, 25, 36, 44, 49, 55, 58, 66, 71, 78, 90, 92 | só p58, p66 (p20/p55/p71 idênticas) |
| V10 | 4, 5, 9, 13, 17, 25, 48, 54, 62 | nenhuma |

- **Escopo (anterior → corrigido)**:
  - V4: CER 0.079 → 0.069, recall 0.823 → 0.831, `table_detection_f1` 0.476 → 0.609.
  - V9: CER 0.130 → 0.129, `currency` 0.925 → 0.945, `numeric` 0.918 → 0.920, células exatas 0.543 → 0.552.
  - Demais corpora: idênticos.
  - Só pioram métricas das duas páginas cujo conteúdo voltou:
    - p167: precisão 1.000 → 0.605 (as linhas grega e cirílica voltaram ilegíveis);
    - p168: ordem por pares 1.000 → 0.967 e `cell_cer` 1.000 → 1.284 (tabela de 2 colunas contra 3 do gabarito);
    - V4 escopo: precisão 0.848 → 0.845.
  - Nenhuma outra métrica piorou em nenhuma página.
- **Avisos novos**: só `suspected_ocr_garble` em V4 p167/p168. Nenhum `content_suppressed_by_postprocess`.
- **Custo**: 26 extrações, 209 extrações de página, 3167 s.

#### T3-P40: diagnóstico (sem alteração de código nem do corpus)

- As 12 páginas são as duas repetições de 6 famílias:
  - `reading_order`: p4, 5, 23, 36;
  - `scrambled_internal_order`: p6, 28;
  - `hybrid_page`: p17, 26;
  - `reading_order_stress`: p18, 38;
  - `interleaved_native_ocr`: p20, 32.
- Em cada uma, a área do bloco `heading` (H1 + subtítulo) indicada pelo manifesto (`bbox_pt`) **não contém nenhum objeto do PDF nem tinta** na renderização. A camada de texto da página também não contém o título, que não aparece em nenhum lugar da página, nem fora da área visível.
- Nas outras 30 páginas, a mesma área tem 2 objetos de texto com o título esperado.
- Conclusão: o **PDF do V5 não contém** esses títulos; o gabarito os exige. É uma possível inconsistência do gerador ou do gabarito, **não** perda do extrator. O `VALIDACAO.txt` do V5 verifica só JSON, hashes e famílias, e não compara o PDF renderizado com o Markdown esperado.
- **Impacto**: 1114 de 22446 caracteres não-espaço do gabarito V5 (≈ 5.0%); recall dessas páginas 0.70–0.82; parte relevante do CER de 0.109 do V5 e da nota de Cobertura 83.5 na rubrica (§1.16).
- **Risco para outros motores**: nenhum, porque não há alteração de código. A camada nativa não precisa de correção.
- **Recomendação** (decisão do responsável): registrar como limitação do gabarito. Se o corpus for regenerado ou corrigido fora desta série, recalcular o V5. Até lá, descontar esse efeito ao ler as métricas do V5. O corpus **não** foi alterado.

### §1.20 RUN 010 — auditoria integral e comparação com a 009 (análise de 2026-11-19, commit `95a6f72`)

Fontes: só os arquivos oficiais de `output/tesseract/009` e `010`. Nenhuma extração foi feita para esta auditoria.

#### Verificação operacional

| Item | Resultado |
|---|---|
| Execução | 2026-11-18 13:41 → 15:13 UTC. 8/8 com extração `exit 0`, métricas `ok` e `rank_eligible`, com as mesmas métricas aplicáveis da 009 |
| Código | `implementation_commit` `95a6f72`, árvore limpa. É `b1202ee` + T3-P20 t5 + T3-P36 t1 + T3-P39 t1 |
| Entradas, configuração, avaliador | `input_hashes` de PDF, gabarito e manifesto idênticos aos da 009 nos 8 corpora. `settings` e `extractor_flags` idênticos. Políticas do avaliador e `reference_effective_sha256` idênticos |
| Status | 7 corpora `success`/`valid`. O V9 continua `partial_success`/`partial` → `needs_review` só pela p36 (T3-P33, mesma exceção `Invalid BBox`). Estados de todas as páginas idênticos à 009; páginas recuperadas iguais |
| Avisos | Os únicos novos são `suspected_ocr_garble` em V4 p167/p168. Nenhum aviso `content_suppressed_by_postprocess` em nenhum corpus, ou seja, o pós-processamento Tesseract só suprimiu mobília reconhecida |
| Tempo de extração (009 → 010) | V3 374 → 375 · V4 2460 → 2517 (+2%) · V5 89 → 86 · V6 601 → 598 · V7 165 → 182 (+10%, saída idêntica) · V8 393 → 389 · V9 586 → 656 (+12%, só 2 páginas mudaram) · V10 546 → 562. Tudo dentro do ruído observado (−11% a +14%) |

#### Páginas que mudaram (009 → 010)

**V3, V5, V7 e V10 idênticos byte a byte.**

| Corpus | Página | Mudança | Origem |
|---|---|---|---|
| V4 | p167, p168 | tabela que antes era suprimida passa a sair (4 → 73 e 14 → 65 tokens) | T3-P36 |
| V9 | p58 | `R$ 2.333,52333,92` → `R$ 2.333,52`; `R$ER$ 845,26` → `R$ 845,26`; `R$ 1.036,351.` → `R$ 1.036,35`; `lrs:R$` → `R$`; `Conferidoota Es Do` → `Conferido` | T3-P39 |
| V9 | p66 | ruído removido (`ValorV:`, `FoR$`, `:!`, `ER`) | T3-P39 |
| **V6** | **p11** | `ValValor` → `Valor` (cabeçalho de tabela) | T3-P39, **não previsto** pelo critério da §5 (que esperava V6 idêntico) |
| **V8** | **p49** | `COMPLEMENTONTO` → `COMPLEMENTO` | T3-P39, não previsto |
| **V8** | **p67** | `Data: 02/10/20262026` → `Data: 02/10/2026` | T3-P39, não previsto |

- As três mudanças não previstas são **só remoções** de uma segunda leitura colada à primeira. É o mesmo mecanismo da V9 p58, em leituras de região de documentos nativos, e a regra o corrige corretamente.
- Efeitos: V8 p67 `numeric` 0.824 → 1.000 e CER 0.009 → 0.000; V6 p11 células exatas 0.769 → 0.808.
- Lição para os critérios: a regra de contenção atua em **todo** caminho Tesseract com mais de uma chamada, e não só em tabela raster. A validação pontual (72 páginas) não incluiu essas páginas.

#### Métricas documentais que mudaram (009 → 010)

| Corpus | Mudanças |
|---|---|
| V4 | CER 0.0540 → 0.0535 · WER 0.1452 → 0.1449 · recall 0.8937 → 0.8941 · precisão 0.9197 → 0.9196 · `table_detection_f1` 0.896 → 0.908 · `table_column_count_accuracy` 0.743 → 0.753 · células exatas 0.634 → 0.638 · `cell_cer` 0.333 → 0.327 |
| V6 | CER 0.00798 → 0.00794 · células exatas 0.938 → 0.939 · `cell_cer` 0.0142 → 0.0138 |
| V8 | CER 0.00577 → 0.00572 · `numeric` 0.9910 → 0.9924 · `dates` 0.9977 → **1.000** |
| V9 | CER 0.0719 → 0.0717 · WER 0.0824 → 0.0819 · recall 0.9905 → 0.9909 · precisão 0.9892 → 0.9897 · células exatas 0.601 → 0.604 · `numeric` 0.9531 → 0.9537 · `currency` 0.9624 → **0.9696** |
| V3, V5, V7, V10 | idênticas |

#### Critérios da §5.0d (RUN 010)

| Verificação | Resultado | Situação |
|---|---|---|
| Geral (hashes, avaliador, status, V9 `partial` só p36) | conforme | ✅ |
| Páginas alteradas | V4 p167/p168 e V9 p58/p66, como previsto; **também V6 p11 e V8 p49/p67**, só remoções explicadas pela regra de contenção | ⚠️ critério de "idêntico" não atendido, mudança benéfica e explicada |
| T3-P36 | p167: tabela detectada, recall **0.548**, CER **0.194**; p168: recall **0.476**; aviso nas duas; V4 recall 0.8941 ≥ 0.894, CER 0.0535 ≤ 0.054 | ✅ |
| Preservação de conteúdo | nenhuma página dos 8 corpora com queda de recall > 0.005; V4 sem páginas com perda de volume (2 → 0); nenhum `content_suppressed_by_postprocess` | ✅ |
| Descarte indevido de valores | nenhuma página com queda de acertos (`tp`) de `numeric`/`currency`/`dates`/`percent`; V9 p58 `currency` 1.000, `numeric` 0.769, CER 0.046; V9 `currency` 0.970 ≥ 0.962 | ✅ |
| Controles de tabela | V8 p4/p58 `€ 98,40` e métricas iguais; V9 p20/p55/p71 e V10 p54 idênticas; tabelas OCR V4 p145–p184 idênticas, salvo p167/p168 | ✅ |
| T3-P38 inalterado | `paragraph_detection_f1` V4 0.410, V9 0.595, V10 0.458 | ✅ |
| Rubrica | nenhum documento abaixo da 009 | ✅ |
| Desempenho | dentro do ruído | ✅ |

**T3-P36 confirmado na RUN 010, mas não "resolvido" no sentido amplo**:
- As duas páginas recuperam o conteúdo que o Tesseract lê.
- O aviso `suspected_ocr_garble` sinaliza **leitura suspeita mantida**, não perda. Na p167 ele marca corretamente as linhas grega e cirílica ilegíveis.
- A coluna "Idioma" da p168 continua não lida (limite de reconhecimento) e **nenhum aviso a identifica**: o pipeline não tem como saber que havia texto ali.
- Outros caminhos de supressão anteriores ao pós-processamento continuam contabilizados sem aviso (§2 T3-P36).

**T3-P39 confirmado na RUN 010**:
- Valores da V9 p58 corretos; nenhuma leitura legítima descartada. Nenhuma página perdeu acertos de dados críticos, e todas as 6 páginas alteradas só perderam leituras duplicadas.
- Controles intactos.

#### Notas da rubrica §1.16 (RUN 010)

A rubrica (pesos, limiares e penalidades) não foi alterada.

| # | Corpus | Texto | Cobertura | Títulos | Parágrafos | Listas | Tab. estrutura | Tab. células | Dados | Ordem | Penalidade | **Nota 010** | Nota 009 | Δ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | V7 | 99.4 | 99.5 | 98.8 | 97.6 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 0 | **99.4** | 99.4 | 0.0 |
| 2 | V8 | 98.2 | 98.6 | 99.4 | 96.2 | 100.0 | 100.0 | 99.9 | 98.5 | 100.0 | 0 | **98.8** | 98.7 | +0.1 |
| 3 | V6 | 96.7 | 96.5 | 99.0 | 96.3 | 100.0 | 100.0 | 96.3 | 91.6 | 100.0 | 0 | **96.7** | 96.7 | 0.0 |
| 4 | V3 | 94.9 | 95.2 | 68.0 | 92.0 | 97.3 | 99.9 | 99.1 | 98.0 | 98.3 | −0.5 | **93.1** | 93.1 | 0.0 |
| 5 | V5 | 67.3 | 83.5 | 83.8 | 83.8 | 92.1 | 100.0 | 100.0 | 98.5 | 87.1 | 0 | **86.5** | 86.5 | 0.0 |
| 6 | V9 | 77.8 | 96.8 | 50.7 | 64.6 | 84.7 | 79.9 | 63.9 | 86.3 | 58.4 | −1.0 | **75.1** | 74.9 | +0.2 |
| 7 | V10 | 55.4 | 94.9 | 48.9 | 52.5 | 72.7 | 96.5 | 91.2 | 86.9 | 55.2 | 0 | **73.6** | 73.6 | 0.0 |
| 8 | V4 | 73.0 | 68.1 | 37.5 | 47.6 | 77.4 | 86.8 | 70.4 | 69.2 | 99.5 | −4.5 | **63.8** | 63.1 | +0.7 |

- **V4**: as páginas com perda de volume caem de 2 para 0 (p167/p168). A p168 passa a contar como "ilegível" (recall 0.476 < 0.50), e a penalidade fica em −4.5 (9 × 0.5), abaixo do teto.
- **V5**: continua penalizado pela inconsistência do gabarito (T3-P40, ≈ 5% dos caracteres).
- **Piores páginas**: inalteradas em relação à §1.15. V4 p104/p118/p88/p142; V9 p51/p9/p43/p5; V10 L01/L02 (56% das edições); V3 p116/p138; V8 p46/p47; V7 p1.
- **Problemas críticos de integridade** (independentes da nota):
  - V4 p163–p166/p172 e V3 p86: OCR ilegível em tabelas e caracteres confundíveis.
  - V9 p36/p92: tabelas giradas ilegíveis (T3-P32).
  - V9 p66: valores truncados (T3-P34).
  - V9 p62/p72: tabelas lado a lado desmontadas (T3-P37).
  - V4 p168: coluna não lida.
- **Regressões**: nenhuma regressão de conteúdo, valores ou estrutura na RUN 010.

### §1.21 Ciclo pós-010: T3-P38 com o protocolo de validação progressiva (2026-11-19)

**Seleção** (prioridades da §5):
1. Nenhuma perda ou corrupção nova na 010, e T3-P36/T3-P39 sem regressão.
2. **T3-P38 foi escolhido**: é regressão estrutural confirmada, com causa atribuída e candidata medida no ciclo anterior.
3. T3-P35 (páginas inclinadas do V10) ficou só no Nível 0. As tentativas relacionadas de T3-P31 (10, em duas séries) falharam na ordem compartilhada de páginas inclinadas, e não há hipótese nova refutável que justifique extrações. **Nenhuma alteração.**

#### T3-P38, tentativa 3 (reverter a t2 de T3-P20, manter a t5)

**Nível 0, diagnóstico sem extração** (dados do ciclo anterior):
- Em OCR denso de coluna única o vão real entre parágrafos (3.0–4.75 pt na V4 p129) é só ~1.5 pt maior que o vão entre linhas (0–1.75 pt, mediana 1.5; caixas de ~8 pt).
- O limite da t2, `max(1.4·m, m + 0.2·h)` = 3.1 pt, fica acima de parte dos vãos reais.
- A variação de caixa que a t2 queria absorver já é compensada linha a linha pela t5 (descendente ausente, só em texto nivelado).
- Componente: `ocr/reconstruct.py::segment_ocr_paragraphs`. Usado por regiões OCR de todos os motores que passam pelo montador; não toca texto nativo.
- **Hipótese**: remover o termo `m + 0.2·h` elimina as fusões sem reabrir as quebras que T3-P20 corrigiu.
- **Teste mínimo de refutação**: os testes de T3-P20, inclusive o da geometria da V10 p11 criado para a t2, devem continuar passando.
- **O que não pode piorar**: palavras, CER, recall, títulos, listas, tabelas e dados críticos.

**Nível 1, componente** (sem OCR):
- Novo teste com a **geometria real medida na extração da V4 p129** (altura de cada uma das 45 caixas e cada vão, do registro da segmentação): `tests/test_tesseract_run010_regressions.py::test_dense_single_column_paragraphs_are_not_merged`.
- **Falha no código de `95a6f72`** (verificado com a função antiga carregada por plugin no scratchpad) e **passa na candidata**.
- Os 8 testes de T3-P20/T3-P34 de `test_tesseract_run008_regressions.py` passam, inclusive o da V10 p11.
- Tempo: 1.7 s. Aprovado.

**Níveis 2–3, por reaproveitamento** (nenhuma extração nova):
- A variante "só t5" do ciclo anterior é exatamente esta candidata: limite `1.4·m`, descendente 0.2·h, texto nivelado.
- Ela foi extraída no mesmo subconjunto e contexto que a base, com o mesmo código de `src/`: `git diff 95a6f72` vazio; arquivos modificados às 09:09, extrações a partir de 09:10.
- **146 extrações de página evitadas** (73 páginas × 2 variantes).

| Escopo (73 págs.) | `paragraph_detection_f1` | `paragraph_boundary_f1` | `paragraph_boundary_recall` | Fusões / quebras (alinhamento de palavras) | Demais métricas oficiais |
|---|---|---|---|---|---|
| V4 (27 págs.) | 0.311 → **0.352** | 0.613 → 0.646 | 0.878 → **0.959** | 27 → **9** / 104 → 108 | idênticas; ordem por pares da p137 0.972 → 0.927 (casamento de blocos, palavras idênticas) |
| V9 (21 págs.) | idêntico | idêntico | idêntico | 3 → 3 / 26 → 26 | idênticas |
| V10 (25 págs.) | 0.429 → 0.430 | 0.588 → 0.589 | 0.870 → 0.873 | 22 → 21 / 112 → 111 (p68 corrigida) | idênticas |

- Pioras de F1 de parágrafos por página (pequenas): V4 p98, p118, p122, p128, p130, p141 (duas colunas) e V10 p37 (0.605 → 0.545).
- Nenhuma página com mudança de palavras, CER, WER, recall, precisão, títulos, listas, tabelas ou dados críticos.

**Nível 4, validação final** (controles definidos **antes** da execução, só páginas OCR **não usadas** no desenvolvimento):
- 43 páginas:
  - V3: 80, 81, 96, 97, 103, 105.
  - V4: 81, 87, 90, 95, 103, 109, 115, 120, 126, 133 (coluna única) e 104, 106, 112, 134, 136 (duas colunas).
  - V9: 2, 4, 10, 16, 33, 43, 50, 75, 81, 87.
  - V10: 31, 33, 41, 45, 53, 60, 74, 75, 83.
  - V6: 3, 33, 89 (varreduras).
- Base = código de `95a6f72` em memória (cópia verificada byte a byte); candidata = código do repositório.
- Execução **sequencial, 1 processo OCR por vez**: 10 processos, 86 extrações de página, **1363 s**.
- **Resultado**: as 43 páginas saíram **idênticas byte a byte** entre base e candidata (palavras, métricas oficiais, fusões e quebras). Na 009 a t2 só alterou páginas que estão no conjunto de desenvolvimento, então isso confirma a ausência de efeito colateral fora delas.

**Controles de outros caminhos** (segmentação compartilhada com outros motores OCR): `test_rapidocr_improvement_005.py`, `test_rapidocr_improvement_011.py`, `test_easyocr_pipeline_integrations.py` e `test_conservation_round.py` deram 85 passed e 1 failed. A falha é a pré-existente de T3-P14 (`test_quoted_and_verbatim_blocks_render`).

**Decisão**: **mantida**. T3-P38 fica como CORRIGIDO — VALIDADO PONTUALMENTE (tentativa 3 de 5), a confirmar na RUN 011.
- Custo conhecido: 4 quebras indevidas a mais em páginas OCR de duas colunas do V4.
- A constante `_GAP_JITTER_RATIO` continua usada na compensação da t5.
- A contagem de T3-P20 continua 5/5: esta é uma correção de regressão registrada em T3-P38.

#### Custo do ciclo e comparação com a metodologia anterior (medido)

| Etapa | Processos OCR | Extrações de página | Tempo de extração | Testes automatizados |
|---|---|---|---|---|
| Auditoria da RUN 010 | 0 | 0 | 0 | — |
| T3-P38, Nível 1 | 0 | 0 | 0 | 1.7 s (9 testes) |
| T3-P38, Níveis 2–3 | 0 (reaproveitado) | 0 (146 evitadas) | 0 | — |
| T3-P38, Nível 4 | 10, sequenciais | 86 | 1363 s | 3.5 s (414 testes Tesseract/parser); 0.8 s (86 de outros motores) |
| **Total do ciclo** | **10** | **86** | **1363 s (≈ 23 min)** | ≈ 6 s |

- **Ciclo pós-009, metodologia anterior (medido)**:
  - T3-P38: 24 extrações, 337 extrações de página, 8474 s somados (3 processos em paralelo, ~2 h 10 de relógio), mais ~25 min perdidos numa bateria com 12 processos.
  - T3-P36 + T3-P39: 26 extrações, 209 extrações de página, 3167 s.
- O consumo de contexto do Claude não pode ser medido diretamente e não é estimado aqui.

### §1.22 RUN 011 — auditoria integral e comparação com a 010 (análise de 2026-11-19, commit `2fdad33`)

Etapa exclusivamente de auditoria: nenhuma extração, nenhum teste, nenhuma alteração de código, testes, gabaritos, configuração ou avaliador. Fontes: só os arquivos oficiais de `output/tesseract/008`–`011` e o `corpus/`. Os diagnósticos próprios (fusões e quebras, rubrica) foram recalculados por scripts novos no scratchpad, só de leitura; os scripts das auditorias anteriores não existiam mais. **Calibração**: os scripts novos reproduzem exatamente as notas da rubrica da 009 e da 010 (§1.16, §1.20), e as contagens de fusões e quebras da 009 ficam próximas das da §1.15 (V4 435/274 contra 431/271; V9 7/80 contra 4/80; V10 44/221 contra 43/213), com a definição explícita na nota da tabela de T3-P38 abaixo. Comparações entre rodadas usam sempre o mesmo script.

#### Verificação operacional

| Item | Resultado |
|---|---|
| Execução | 2026-11-18 16:02:32 → 17:29:06 UTC. 8/8 com extração `exit 0`, métricas `ok`, `rank_eligible`, e as mesmas métricas aplicáveis da 010 (V3 61, V4 61, V5 67, V6 63, V7 65, V8 65, V9 67, V10 61) |
| Código | `implementation_commit` `2fdad33` nos 8 arquivos de métricas = `95a6f72` + T3-P38 t3. O `reconstruct.py` foi modificado às 12:15 −03 e commitado às 12:52 −03; a rodada começou às 13:02 −03; árvore limpa hoje e `git diff 2fdad33` vazio |
| Entradas e avaliador | `input_hashes` (PDF, gabarito, manifesto), `settings` e `extractor_flags` idênticos aos da 010. Todas as políticas do bloco `evaluator` idênticas; só `implementation_commit` e `created_at_utc` mudam. Nenhum `corpus_changed` |
| Manifestos de execução | idênticos aos da 010 nos 8 corpora, salvo `elapsed_s`, `run_id` e caminho de saída: mesmos estados de página, páginas recuperadas, avisos e fatos. **Nenhum aviso novo** |
| Status | 7 corpora `success`/`valid`; V9 `partial_success`/`partial` → `needs_review` só pela p36 (T3-P33, mesma exceção `Invalid BBox`) |
| Tempo de extração (010 → 011, s) | V3 375 → 363 (−3.3%) · V4 2517 → 2310 (−8.2%) · V5 86 → 83 (−3.7%) · V6 598 → 585 (−2.3%) · V7 182 → 163 (−10.4%) · V8 389 → 383 (−1.6%) · V9 656 → 586 (−10.6%) · V10 562 → 540 (−3.9%). Todos mais rápidos, inclusive os de saída idêntica: ruído do ambiente, dentro da faixa (−11% a +14%) |

#### Páginas que mudaram (011 × 010)

**V3, V5, V6, V7, V8 e V9 idênticos byte a byte** (o V9 tem o mesmo `hypothesis_sha256`). Mudaram 17 páginas, todas de OCR de texto e todas **só na divisão em blocos**: a sequência de caracteres sem espaços é idêntica em cada uma, ou seja, nenhuma palavra foi alterada, perdida, duplicada ou reordenada.

| Corpus | Páginas | Natureza |
|---|---|---|
| V4 | 83, 85, 99, 125, 129, 137, 138, 139, 141, 143 (coluna única) | fusões desfeitas (+1 a +4 blocos por página) |
| V4 | 98, 118, 122, 128, 130 (duas colunas) | +1 bloco cada: um parágrafo partido no meio (quebra nova) |
| V10 | 68 (L02, inclinada −4.6°) | uma fusão desfeita (mesmo número de blocos, fronteira deslocada) |
| V10 | 37 (L05, nivelada) | +1 bloco: a última palavra de um parágrafo ("anexos.") isolada |

- Todas as 17 páginas estão no conjunto de desenvolvimento do ciclo pós-010 (as 27 páginas do V4 e as do V10 alteradas pela t2 na 009). **Nenhuma página fora desse alcance mudou**, como previsto.
- V4 p140 (controle contra fusões), V9 p17 e V10 p25 idênticos à 010.

#### Métricas oficiais que mudaram (documento)

| Corpus | Métrica | 010 | 011 | Leitura |
|---|---|---|---|---|
| V4 | `paragraph_detection_f1` | 0.4104 | **0.4207** | ↑ |
| V4 | `paragraph_detection_precision` / `recall` | 0.4000 / 0.4213 | 0.4066 / 0.4358 | ↑ / ↑ |
| V4 | `paragraph_boundary_f1` | 0.5419 | **0.5530** | ↑ |
| V4 | `paragraph_boundary_precision` / `recall` | 0.5254 / 0.5595 | 0.5307 / **0.5772** | ↑ / ↑ (008, antes da regressão: 0.5782) |
| V4 | `pairwise_reading_order_accuracy` / `block_order_pairwise_accuracy` | 0.9999 / 0.9997 | 1.0000 / 0.9998 | casamento de blocos; sem mudança real de ordem |
| V4 | `cer_markdown_raw` / `aligned_block_text_cer` | 0.06213 / 0.8910 | 0.06208 / 0.8758 | só separadores de bloco (o CER bruto conta as linhas em branco) |
| V4 | `duplicate_excess_rate` | 0.6736 | 0.6784 | mais blocos na hipótese (T3-P19); nenhuma duplicação real |
| V10 | `paragraph_detection_f1` | 0.4583 | **0.4590** | ↑ |
| V10 | `paragraph_boundary_f1` / `recall` | 0.5909 / 0.8674 | 0.5916 / 0.8690 | ↑ (008: recall 0.8690) |
| V10 | `cer_markdown_raw` / `duplicate_excess_rate` | 0.15082 / 0.5186 | 0.15081 / 0.5173 | separadores de bloco |
| V9 | todas | — | idênticas | `paragraph_detection_f1` 0.595 |

**Idênticas à 010 em todos os corpora**: CER normalizado, WER, recall e precisão textual, títulos, listas, todas as métricas de tabela, `numeric`, `currency`, `dates`, `percent`, identificadores e integridade de páginas.

#### T3-P38 — critérios da §5.0e (RUN 011)

| Verificação | Critério | 011 | Situação |
|---|---|---|---|
| V4 `paragraph_boundary_recall` | > 0.5595 | **0.5772** | ✅ |
| V4 `paragraph_detection_f1` | ≥ 0.410 | **0.4207** | ✅ |
| V9 `paragraph_detection_f1` | ≥ 0.590 | **0.595** (idêntico) | ✅ |
| V10 `paragraph_detection_f1` | ≥ 0.455 | **0.459** | ✅ |
| V4 p129 / p137 / p138 | ≥ 0.400 / 0.105 / 0.000 | **0.696 / 0.348 / 0.182** | ✅ |
| V10 p68 | ≥ 0.182 | **0.255** | ✅ |
| Ganhos de T3-P20 | V4 p140 com 140.01–140.03 separados; V9 p17 e V10 p25 sem piora | p140, V9 p17, V10 p25 idênticos à 010 | ✅ |
| Sequência de palavras | idêntica em toda página | idêntica nas 17 páginas alteradas; demais byte a byte | ✅ |
| Dados críticos e conteúdo | idênticos; nenhum aviso novo | idênticos; nenhum aviso novo | ✅ |
| T3-P36 / T3-P39 | V4 p167/p168, V9 p58/p66, V6 p11, V8 p49/p67 idênticos | idênticos | ✅ |
| Páginas alteradas | V3, V5–V8 e V9 idênticos; V4/V10 só nas páginas de T3-P38 | conforme | ✅ |
| Rubrica | nenhum documento abaixo da 010; Parágrafos do V4 sobe | V4 Parágrafos 47.6 → 48.7; nenhum documento cai | ✅ |
| Desempenho | ruído (−11% a +14%) | −1.6% a −10.6% | ✅ |
| Geral | 8/8 `ok`; V9 `partial` só por T3-P33 | conforme | ✅ |

**Fusões e quebras por página** (diagnóstico próprio, não oficial: as palavras dos blocos de parágrafo da saída são alinhadas às dos parágrafos de prosa do gabarito com `difflib`; um par bloco ↔ parágrafo conta quando compartilha ≥ 2 palavras; fusões = Σ(parágrafos do gabarito por bloco − 1); quebras = Σ(blocos por parágrafo do gabarito − 1)):

| Página | Condição | Fusões 008 / 010 / **011** | Quebras 010 → 011 | `paragraph_detection_f1` 008 / 010 / **011** | `paragraph_boundary_recall` 010 → 011 |
|---|---|---|---|---|---|
| V4 p83 | coluna única | 0 / 2 / **0** | 2 → 2 | 0.696 / 0.571 / **0.696** | 0.778 → 1.000 |
| V4 p85 | coluna única | 0 / 1 / **0** | 1 → 1 | 0.727 / 0.667 / **0.727** | 0.889 → 1.000 |
| V4 p99 | coluna única | 0 / 1 / **0** | 1 → 1 | 0.696 / 0.545 / **0.696** | 0.889 → 1.000 |
| V4 p125 | coluna única | 0 / 1 / **0** | 1 → 1 | 0.818 / 0.667 / **0.818** | 0.889 → 1.000 |
| V4 p129 | coluna única | 0 / 3 / **0** | 2 → 2 | 0.696 / 0.400 / **0.696** | 0.667 → 1.000 |
| V4 p137 | coluna única | 2 / 7 / **3** | 4 → 4 | 0.417 / 0.105 / **0.348** | 0.222 → 0.667 |
| V4 p138 | coluna única | 4 / 7 / **4** | 5 → 5 | 0.182 / 0.000 / **0.182** | 0.222 → 0.556 |
| V4 p139 | coluna única | 0 / 1 / **0** | 0 → 0 | 0.909 / 0.762 / **0.909** | 0.889 → 1.000 |
| V4 p141 | coluna única | 0 / 1 / **0** | 6 → 6 | 0.357 / 0.370 / **0.357** | 0.889 → 1.000 |
| V4 p143 | coluna única | 0 / 1 / **0** | 1 → 1 | 0.818 / 0.762 / **0.818** | 0.889 → 1.000 |
| V4 p98 | duas colunas | 0 / 0 / 0 | 9 → **10** | 0.044 / 0.125 / **0.061** | 1.000 → 1.000 |
| V4 p118 | duas colunas | 0 / 0 / 0 | 5 → 5 (¹) | 0.044 / 0.105 / **0.103** | 1.000 → 1.000 |
| V4 p122 | duas colunas | 1 / 1 / 1 | 5 → **6** | 0.129 / 0.417 / **0.400** | 0.875 → 0.875 |
| V4 p128 | duas colunas | 0 / 0 / 0 | 2 → **3** | 0.051 / 0.143 / **0.138** | 1.000 → 1.000 |
| V4 p130 | duas colunas | 0 / 0 / 0 | 4 → **5** | 0.048 / 0.138 / **0.133** | 1.000 → 1.000 |
| V10 p68 | L02, inclinada | 1 / 2 / **1** | 8 → 7 | 0.255 / 0.182 / **0.255** | 0.667 → 0.733 |
| V10 p37 | L05, nivelada | 0 / 0 / 0 | 3 → 3 (²) | 0.468 / 0.605 / **0.545** | 1.000 → 1.000 |

(¹) a p118 ganha um corte no meio do parágrafo 118.02 que o limiar de 2 palavras não separa de um corte já existente; o F1 da página registra a piora. (²) a quebra nova isola uma única palavra ("anexos."), abaixo do limiar de 2 palavras; o F1 a registra.

| Totais (documento) | Fusões 008 / 010 / **011** | Quebras 008 / 010 / **011** |
|---|---|---|
| V4 | 416 / 435 / **417** | 395 / 274 / **278** |
| V9 | 7 / 7 / **7** | 142 / 80 / **80** |
| V10 | 43 / 44 / **43** | 279 / 221 / **220** |

- A rodada completa reproduz **exatamente** o resultado pontual da §1.21: V4 −18 fusões e +4 quebras; V10 −1 fusão e −1 quebra; V9 idêntico. O reaproveitamento dos Níveis 2–3 (146 extrações evitadas) previu corretamente o efeito.
- **As fusões voltaram ao nível da 008** (V4 416 → 417; V9 e V10 iguais), enquanto as quebras continuam no nível de T3-P20 (V4 395 → 278, V9 142 → 80, V10 279 → 220). A regressão de T3-P38 foi desfeita e o ganho de T3-P20 foi preservado.
- **Páginas que melhoraram** (11): V4 p83, p85, p99, p125, p129, p137, p138, p139, p141, p143 e V10 p68. Na V4 p141 a fusão some e as fronteiras melhoram (`paragraph_boundary_f1` 0.640 → 0.692), mas o F1 de detecção cai um pouco (0.370 → 0.357, igual à 008). Dez páginas (todas menos a p137) voltam **exatamente** aos valores da 008.
- **Páginas que pioraram** (6, todas previstas na §1.21 e pequenas): V4 p98 (0.125 → 0.061), p118, p122, p128, p130 (−0.002 a −0.017 de F1) e V10 p37 (0.605 → 0.545). Todas continuam acima da 008 (ex.: V10 p37 0.468 na 008).
- **Resíduo**: a V4 p137 mantém 137.04 + 137.05 no mesmo bloco, que a 008 separava (fusões 2 → 3; F1 0.348 contra 0.417 na 008). É a única página com mais fusões que a 008. Atribuição do ciclo pós-009: a t5 sozinha acrescenta exatamente essa fusão.

**Conclusão: T3-P38 CONFIRMADO NA RUN 011** (tentativa 3 de 5). Todos os critérios objetivos foram atendidos, sem mudança de palavras, conteúdo, dados críticos, títulos, listas, tabelas ou ordem real. Custo conhecido e aceito: 5 quebras novas no meio de parágrafos em páginas OCR de duas colunas do V4 e uma palavra isolada na V10 p37. Resíduo: uma fusão na V4 p137 atribuída à t5.

#### Correções anteriores — preservação na RUN 011

| Problema | Páginas | Situação |
|---|---|---|
| T3-P20 | V4 duas colunas, V4 p140, V9 (todo), V10 p25 | ganhos mantidos (quebras V4 278, V9 80, V10 220; p140 idêntica) ✅ |
| T3-P22 | V4 p20/p25/p26/p28; V10 p9/p54/p59; V9 p90 | idênticas ✅ |
| T3-P32 | V9 p20/p55/p71 | idênticas (V9 byte a byte) ✅ |
| T3-P34 | V9 p58/p44/p49 | idênticas ✅ |
| T3-P35 | V10 L04–L06 (CER médio 0.104); p32 0.311 | CER idêntico; só a V10 p37 (L05) mudou, na segmentação ✅ |
| T3-P36 | V4 p167/p168 (com aviso) | idênticas ✅ |
| T3-P39 | V9 p58/p66, V6 p11, V8 p49/p67 | idênticas ✅ |
| T3-P28 | V8 p4/p58 (`€ 98,40`) | idênticas ✅ |

**Regressões novas**: nenhuma de conteúdo, valores, ordem, títulos, listas ou tabelas. As únicas pioras são as 6 páginas de segmentação acima, previstas e quantificadas antes da rodada.

#### Achado da auditoria: parágrafos nativos densos fundidos no V4 (registrado como T3-P41)

- O diagnóstico de fusões mostra que **227 das 417 fusões do V4 (54%)** estão nas 32 páginas de **texto nativo denso** (p9–p40), e não no OCR (120 fusões nas 64 páginas de OCR de texto).
- Exemplo: na V4 p9, os 10 parágrafos ("Parágrafo 009.01" a "009.10") saem num único bloco de 6250 caracteres; `paragraph_detection_f1` 0.154 e `paragraph_boundary_recall` 0.100.
- Nas 32 páginas: 38 parágrafos certos de 316 no gabarito; 227 das 430 fronteiras ausentes do documento.
- Existe desde a **001** (a família tem 39 acertos na 001 e 38 na 011); não é regressão. Camada **nativa** (fora do escopo sem autorização).
- **Estimativa** (interpretação, não oficial): com essas páginas corretas, o `paragraph_detection_f1` do V4 iria de 0.42 para ≈ 0.59 e o `paragraph_boundary_f1` de 0.55 para ≈ 0.70; Parágrafos do V4 ≈ 64 e a nota do V4 ≈ +1.6.

### §1.23 Notas da rubrica §1.16 (RUN 011)

**Resultados oficiais** (rubrica, pesos, limiares, penalidades, gabaritos e avaliador **não alterados**; os scripts recalculados reproduzem a 009 e a 010 exatamente):

| # | Corpus | Texto | Cobertura | Títulos | Parágrafos | Listas | Tab. estrutura | Tab. células | Dados | Ordem | Penalidade | **Nota 011** | Nota 010 | Δ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | V7 | 99.4 | 99.5 | 98.8 | 97.6 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 0 | **99.4** | 99.4 | 0.0 |
| 2 | V8 | 98.2 | 98.6 | 99.4 | 96.2 | 100.0 | 100.0 | 99.9 | 98.5 | 100.0 | 0 | **98.8** | 98.8 | 0.0 |
| 3 | V6 | 96.7 | 96.5 | 99.0 | 96.3 | 100.0 | 100.0 | 96.3 | 91.6 | 100.0 | 0 | **96.7** | 96.7 | 0.0 |
| 4 | V3 | 94.9 | 95.2 | 68.0 | 92.0 | 97.3 | 99.9 | 99.1 | 98.0 | 98.3 | −0.5 | **93.1** | 93.1 | 0.0 |
| 5 | V5 | 67.3 | 83.5 | 83.8 | 83.8 | 92.1 | 100.0 | 100.0 | 98.5 | 87.1 | 0 | **86.5** | 86.5 | 0.0 |
| 6 | V9 | 77.8 | 96.8 | 50.7 | 64.6 | 84.7 | 79.9 | 63.9 | 86.3 | 58.4 | −1.0 | **75.1** | 75.1 | 0.0 |
| 7 | V10 | 55.4 | 94.9 | 48.9 | **52.5** (52.46 → 52.53) | 72.7 | 96.5 | 91.2 | 86.9 | 55.2 | 0 | **73.6** | 73.6 | +0.0 (73.636 → 73.643) |
| 8 | V4 | 73.0 | 68.1 | 37.5 | **48.7** (47.6) | 77.4 | 86.8 | 70.4 | 69.2 | **100.0** (99.5) | −4.5 | **63.9** | 63.8 | +0.1 (63.755 → 63.887) |

- Penalidades iguais às da 010: V3 −0.5 (p86); V9 −1.0 (`partial`); V4 −4.5 (9 páginas ilegíveis: p103, 104, 153, 163, 164, 165, 166, 168, 172).
- Ordem do V4 99.5 → 100.0: `pairwise_reading_order_accuracy` 0.9999 → 1.0000 por casamento de blocos, **não** por mudança real de ordem.
- **Notas por rodada** (continuação da §1.16): V3 93.1 · V4 62.8 (008) → 63.1 (009) → 63.8 (010) → **63.9** · V5 86.5 · V6 96.7 · V7 99.4 · V8 98.7 → 98.8 (010) → **98.8** · V9 73.8 → 74.9 → 75.1 → **75.1** · V10 73.3 → 73.6 → 73.6 → **73.6**.

**Interpretação qualitativa** (não altera as notas):
- A RUN 011 é uma correção estrutural pequena: o efeito na nota é +0.1 no V4 e ≈ 0 no V10. O peso de Parágrafos é 10%, e a maior parte das fusões e quebras restantes do V4 não é de OCR (T3-P41, nativo; T3-P21, OCR degradado).
- **Piores páginas** (sem mudança): V4 p104/p118/p88/p142; V9 p51/p9/p43/p5; V10 L01/L02 (56% das edições do V10); V3 p116/p138; V8 p46/p47; V7 p1.
- **Integridade** (sem mudança): V4 p163–p166/p172 e V3 p86 ilegíveis; V9 p36/p92 tabelas giradas ilegíveis (T3-P32); V9 p66 valores truncados (T3-P34); V9 p62/p72 tabelas lado a lado desmontadas (T3-P37); V4 p168 coluna não lida.
- **Limitações conhecidas**: V5 penalizado por T3-P40 (≈ 5% dos caracteres); V3/V4 com nível de título por convenção do gabarito (−2.0 cada).

### §1.24 Priorização pós-011 (reavaliação dos problemas abertos)

Critérios, em ordem: (1) perda de conteúdo; (2) corrupção de dados críticos; (3) severidade estrutural; (4) número de páginas; (5) impacto na nota; (6) chance de resolução; (7) risco de regressão; (8) custo de validação. Nenhuma perda ou corrupção nova na 011.

| Problema | Situação na 011 | Evidência atual | Componente / autorização | Tentativas | Risco | Benefício | Custo de validação | Avaliação |
|---|---|---|---|---|---|---|---|---|
| **T3-P06** títulos em regiões OCR | sem mudança | Regiões OCR quase não emitem títulos: V4 páginas escaneadas **0 de 92** títulos do gabarito; V10 escaneadas **2 de 170** e mistas 0 de 33; V9 mistas 17 de 98, escaneadas 0 de 15. Nativas: V10 86 de 92, V9 36 de 38. Exemplos: V4 p83 "OCR denso scan clean 200 - instância A" (`###` no gabarito) sai como parágrafo; V10 p52 título da página e títulos de coluna saem como parágrafos | layout OCR (`layout/ocr_aware.py`, classificação de título por altura) — caminho de regiões OCR; **sem** autorização nativa | 0 / 5 | médio: mudar a classificação muda a divisão em regiões, e nas páginas inclinadas isso já embaralhou colunas (T3-P31). Mitigação: começar só por texto nivelado e exigir sequência de palavras idêntica | **alto e amplo**: ≈ 400 títulos ausentes em V4/V9/V10; Títulos 37.5 / 50.7 / 48.9 | **baixo**: a mudança não deve tocar palavras; o critério "palavras idênticas, só marcadores `#`" é barato e forte | **recomendado (1º)** |
| **T3-P35** ordem nas páginas inclinadas (V10 L01/L02) | sem mudança | 12 páginas = **56% das edições do V10**; CER 0.157–0.378, recall 0.909–0.997 | `ocr/reconstruct.py` e layout OCR (Tesseract); a abordagem anterior de T3-P31 dependia de `text/reading_order.py` (compartilhado) | 2 / 5 (T3-P31: 10, encerradas) | **alto**: 10 tentativas falharam na mesma ordem compartilhada | **o maior**: V10 ≈ +4–5 se L01/L02 chegarem ao nível de L04–L06 | médio-alto: ordem exige CER/recall por página e comparação de sequência | **recomendado (2º), só com a abordagem nova abaixo** |
| T3-P37 tabelas lado a lado (V9) | sem mudança | p62 CER 0.465, células exatas 0.194; p72 0.301 / 0.000, `currency` 0.824; p14 fundida (0.400) | detector de tabelas **nativo** (compartilhado) — **exige autorização** | 0 / 5 | médio-alto (V5–V8) | associação valor ↔ linha em 3 páginas; V9 Células | médio | 3º; depende de autorização |
| T3-P34 tabelas raster restantes (V9) | sem mudança | p66 recall 0.803, `currency` 0.632 (truncados); p78 não detectada; p25 0.118; p8 0.375 | detecção visual e releitura (compartilhados) | **4 / 5** | médio | p66 é dado crítico; as outras três têm causas distintas | médio | só com hipótese nova e única para a p66 (última tentativa) |
| T3-P33 `Invalid BBox` (V9 p36) | sem mudança | V9 `partial` (−1.0 na nota) só por ela | `text_tracks._build_table` (nativo compartilhado) — **exige autorização** | 0 / 5 | baixo (rejeitar candidato sem sequência vertical válida; a exceção já impede a tabela falsa) | V9 `valid`, +1.0 na nota; nenhum conteúdo | baixo | correção pequena, candidata a acompanhar outro ciclo se autorizada |
| T3-P38 resíduo | confirmado | V4 p137 (1 fusão da t5); 5 quebras novas em duas colunas | `segment_ocr_paragraphs` | 3 / 5 | — | pequeno | — | monitorar; não reabrir |
| T3-P41 parágrafos nativos densos (novo) | desde a 001 | 227 fusões em 32 páginas do V4 | segmentação **nativa** — exige autorização | 0 / 5 | médio (V5–V8) | V4 Parágrafos ≈ +16 (nota ≈ +1.6) | baixo (estrutural) | registrar; decisão do responsável |
| T3-P22 resíduo, T3-P32 (p36/p92), T3-P20, T3-P31 | encerrados (5/5 ou 10) | sem mudança | — | esgotadas | — | — | — | **não reiniciar** sem causa ou abordagem nova (ver abaixo) |
| T3-P07, T3-P21, T3-P29, T3-P12, T3-P05, T3-P04, T3-P40 | sem mudança | — | — | — | — | — | — | P2–P3, como na §1.18 |

**Os dois problemas recomendados para o próximo ciclo** (a escolha é do responsável):

1. **T3-P06 — títulos em regiões OCR niveladas** (V4 OCR de texto, V10 L03–L06, V9 páginas mistas).
   - **Por quê**: maior número de erros estruturais ainda não atacados nos três corpora prioritários, com componente só do caminho OCR. A mudança esperada não altera palavras, então a validação pode exigir sequência de palavras idêntica página a página, além de títulos melhores. É o critério mais barato e mais forte do protocolo.
   - **Nível 0 primeiro**: medir, a partir das saídas existentes, (a) quantos títulos ausentes existem como texto na saída (parágrafo com o mesmo texto) e quantos são perda de texto; (b) quanto do V4 é a linha "OCR denso … instância A" e se os títulos `## Página NNN | …` do gabarito existem no PDF (lição de T3-P40); (c) a razão de altura título/corpo nas linhas OCR niveladas, para saber se o critério de altura (1.28) explica as perdas.
   - **Riscos**: falsos títulos (V9 já tem 45 falsos positivos), divisão em regiões e, portanto, ordem (T3-P31). Excluir páginas inclinadas da primeira tentativa.
2. **T3-P35 — ordem nas páginas inclinadas do V10**, só com uma causa ou abordagem nova.
   - **Por quê**: maior massa de erro do corpus prioritário (56% das edições do V10).
   - **Abordagem nova** (não tentada em T3-P31/T3-P35): fazer o layout OCR e a ordem das linhas no **referencial retificado** (os tokens já são lidos no raster endireitado por T3-P18 e depois desrotacionados para o referencial da página). A causa medida em T3-P31 é a caixa alinhada aos eixos inflada pela inclinação; no referencial retificado ela desaparece para títulos, calhas e ordem ao mesmo tempo, sem tocar a ordem genérica compartilhada.
   - **Condição para avançar além do Nível 1**: demonstrar com as linhas reais capturadas de uma página L02 (ex.: p18) que a ordem no referencial retificado é correta sem alterar `text/reading_order.py`. Se exigir o componente compartilhado, parar e pedir autorização.
   - **Riscos**: coordenadas de saída (tabelas, figuras, avisos) e deduplicação (perda da V10 p25 na tentativa 1 de T3-P35). Controles obrigatórios de recall por página.

**Ficam fora dos dois primeiros**:
- **T3-P37**: perda de associação de valores, mas só 3 páginas e exige autorização para o detector nativo, com risco em V5–V8. Recomendado como 3º, após autorização.
- **T3-P34**: só resta 1 tentativa e as quatro páginas têm causas distintas; usar a tentativa só com uma hipótese única para a p66.
- **T3-P33**: correção pequena e de baixo risco, mas sem ganho de conteúdo e no detector nativo. Pode ser incluída em qualquer ciclo autorizado para a camada nativa.

**Problemas com tentativas esgotadas** (não reiniciar sem justificativa nova):
- T3-P20 (5/5) e T3-P38 (3/5, confirmado): não há causa nova; os resíduos (p137 e as 5 quebras em duas colunas) são pequenos.
- T3-P22 (5/5): os resíduos V9 p51/p43/p5 têm mecanismo distinto ainda não diagnosticado; só como problema novo e com autorização nativa.
- T3-P32 (5/5): p36/p92 exigem integrar a releitura girada no núcleo (aprovação).
- T3-P31 (10): a abordagem do referencial retificado proposta para T3-P35 é a primeira que ataca a causa medida (caixa inflada) sem tocar a ordem compartilhada; se funcionar, as listas falsas de T3-P31 devem ser **medidas** como efeito colateral, não perseguidas como alvo.

### §1.25 Ciclo pós-011: T3-P06 — títulos em regiões OCR (2026-11-19, 10:47–12:47 UTC)

**Resumo**: causa raiz demonstrada. A correção exige alterar `layout/heading.py`, compartilhado com o caminho nativo, por isso **nenhuma alteração foi aplicada ao repositório**. A candidata final (t3) foi validada **em memória** (scratchpad, aplicada por monkeypatch) até o Nível 4 e fica **para aprovação**. Tentativas usadas: **3 de 5** (t1 aprovada nos Níveis 1–3; t2 rejeitada no Nível 4 por perda de conteúdo; t3 aprovada no Nível 4 com falsos positivos residuais).

#### Nível 0 — onde o erro é introduzido

- **Não é reconhecimento nem montagem.** Comparando as saídas da 011 com o gabarito, o texto do título existe como **bloco próprio** e sai como parágrafo em V4 escaneadas (77 de 92 títulos ausentes), V9 mistas (69) e escaneadas (15), e V10 escaneadas (107) e mistas (30). Os casos "ausente" do V10 (44) estão nas páginas inclinadas (T3-P31/T3-P35).
- **Não é a classificação do layout OCR.** A captura da entrada e da saída real de `layout/ocr_aware.py::reconstruct_ocr_layout` (V4 p82/p83/p91, V9 p2/p8/p10, V10 p6/p8/p9/p52) mostra que os títulos verdadeiros **são** classificados como TITLE (V4 "OCR denso … instância A" com razão 1.56; V10 H1 2.19–2.36; títulos de coluna 1.38–1.56).
- **Causa raiz 1 (rebaixamento total)**: `layout/heading.py::assign_heading_levels` escolhe o caminho **por documento**. Basta uma página com `font_size` nativo (V4, V9 e V10 têm) para que **toda** região TITLE seja julgada pelo critério nativo. Para uma região só-OCR, `_heading_candidate_score` lê o texto de `native_lines` (vazio), devolve 0 (< `HEADING_ACCEPTANCE_THRESHOLD` = 1.0), e a região é rebaixada para TEXT. É determinístico: nenhum título OCR sobrevive nesses corpora. O caminho OCR (§28) só é usado em documentos 100% escaneados.
  - **Contraprova**: num subconjunto só com páginas escaneadas (V4 p82/p83/p91, sem página nativa), o documento cai no caminho OCR e os títulos **aparecem** (`# OCR denso scan clean 200 - instância A`).
  - Consequência para a validação: todo subconjunto de páginas precisa de uma **página nativa âncora** para reproduzir o contexto da rodada completa (V4 p20, V9 p5, V10 p3). Com a âncora, as páginas OCR da base saem idênticas à 011.
- **Riscos escondidos pelo mesmo defeito** (medidos nas mesmas capturas; hoje inofensivos porque tudo é rebaixado):
  - (b) **linhas de corpo embutidas** num parágrafo com caixa alta (razão 1.28–1.42; vão acima −0.7 a 1.7 pt e abaixo −0.5 a 1.2 pt) viram regiões TITLE. Ao serem rebaixadas como **regiões separadas**, elas também **intercalam as colunas** em páginas de duas colunas (V4 p82, CER 0.196) e quebram parágrafos;
  - (c) linhas TITLE consecutivas são agrupadas sem considerar o vão: cabeçalho institucional, H1 e linha de metadados viram uma região só (V10 p6, vãos de 35–38 pt);
  - (d) a regex de legenda ("Quadro/Tabela/Figura…") é testada antes da altura: o H1 "Quadro de acompanhamento | …" (razão 1.64) vira CAPTION (V9 p2);
  - (e) o prefixo de seção `^[A-Z][.)]?\s+` aceita **artigos** do português ("A revisão…", "O documento…") como numeração; uma linha numerada de registro ("001 V10-83617-00 21/09/2026 …") também passa.
- **Causa raiz 2 (fora desta correção)**: nas páginas **mistas** do V9, o texto OCR fica dentro de regiões FIGURE (associação de OCR a figuras), que o layout OCR não reclassifica ("Análise técnica", "Situação financeira", "Encaminhamento" na V9 p8). Caminho distinto (`api.py`, compartilhado).
- **Distinções exigidas** (sem lista de palavras, números de página ou textos do gabarito): cabeçalho e rodapé de borda continuam com as regras de mobília (a remoção de repetidos testa a linha pela posição, qualquer que seja o tipo da região); tabelas e figuras são âncoras não tocadas; marcadores de lista são testados antes de título; legenda só abaixo do tamanho de título; linha de registro numerada não é seção.

#### Conjuntos (registrados às 11:05, antes de qualquer alteração)

| Conjunto | V4 | V9 | V10 | Outros |
|---|---|---|---|---|
| D (desenvolvimento, nivelado) | 82, 83, 91 (+ 98, 118 no Nível 3) | 2, 8, 10 | 6, 8, 9, 52 (+ 37) | — |
| G (generalização, t1–t2) | 84, 89, 97, 119, 143, 92, 116, 124 | 12, 15, 31, 33, 50, 60, 84 | 4, 17, 19, 24, 29, 34, 46, 70, 73 | — |
| S (estabilidade) | 110, 129, 137, 140, 145, 150, 164, 167, 168 | 17, 20, 55, 58, 66, 71 | 25, 32, 54, 68 | V3 1, 80, 96; V5 3, 17; V6 3, 11, 33, 89; V8 4, 58 |
| Inclinadas (avaliação) | 105, 107 | 9, 23 | 13, 18 | — |
| **G′** (definido às 12:15, antes de qualquer resultado da t3; páginas nunca vistas) | 93, 101, 121, 127, 94, 100 | 1, 6, 16, 38, 61 | 21, 23, 26, 40, 63, 66 | — |
| Âncoras nativas | 20 | 5 | 3 | — |

As páginas G foram usadas para decidir a t2 e passam a contar como vistas; a evidência independente da t3 é o **G′**.

#### Tentativas

| # | Hipótese | Alteração (só em memória) | Resultado | Decisão |
|---|---|---|---|---|
| t1 | (A) caminho por região: região TITLE só-OCR num documento com fonte nativa é julgada pelo critério OCR (§28) contra a altura de corpo OCR **da página**; níveis agrupados à parte; aceite estrito (altura ≥ 1.28× o corpo, o mesmo limiar do layout, ou prefixo de seção estrito, em que a letra só conta com "." ou ")"). (B) linha embutida num parágrafo (linha anterior TEXT, vãos acima e abaixo ≤ 0.35× o corpo) continua TEXT. (C) linha TITLE com vão maior que a altura da linha inicia outra região. (D) linha em tamanho de título não é legenda | `assign_heading_levels` e `reconstruct_ocr_layout` substituídos por cópias modificadas | **N1**: as 8 linhas embutidas deixam de ser TITLE, nenhum título verdadeiro perdido, palavras idênticas. **N2** (V4 20/82/83, V10 3/6/52): títulos V4 0/3 → 2/3, V10 0/12 → 10/12, **0 falsos**; V4 p82 CER 0.196 → 0.032 (só reordenação: 703 = 703 palavras). **N3** (V4 20/91/98/118, V9 5/2/8/10, V10 3/8/9/37): V4 p98 CER 0.244 → 0.065; V4 p118 com 1 falso real ("Parágrafo 118.23…", razão 1.28) | aprovada nos Níveis 1–3 |
| t2 | t1 + (B′): linha de altura < 1.5× o corpo, seguida na mesma coluna por linha TEXT colada, é início de parágrafo (só pela V4 p118) | idem | **N4** (74 págs., 14 processos, 2530 s): **perda de conteúdo** em V3 p80 (recall 1.000 → 0.826), V4 p145 (0.808 → 0.654), V4 p110, V10 p68 (−60 palavras; `numeric` 17 → 15 acertos) e V10 p13 (−30). Mecanismos: (B′) juntou o separador "Página NNN | …" às linhas seguintes, e a supressão de separador do pós-processamento Tesseract levou a linha "Controle de página"; nas páginas inclinadas, as caixas infladas fizeram linhas de corpo virarem títulos (níveis 4–6), fundidos com perda. Títulos falsos de registro numerado (V10 p4: +6) | **rejeitada** (perda de conteúdo); generalização excessiva de um caso de desenvolvimento |
| t3 | t1 **sem (B′)**; tudo restrito a **texto nivelado** (\|inclinação\| ≤ 0.005, o critério de T3-P35 t2): em página inclinada, layout e títulos ficam exatamente como hoje; prefixo numerado só conta com texto majoritariamente alfabético depois do número (< 20% de dígitos) | idem | ver Níveis 1 e 4 abaixo | **candidata final, para aprovação** |

#### t3 — Nível 1 (componente, entradas reais capturadas)

- Teste `test_t3p06_component.py` (scratchpad, 8 testes, < 1 s), com as regiões reais de V4 p82/p83/p118, V9 p2/p8, V10 p6/p52, V3 p80 e as páginas reais de entrada de `assign_heading_levels` (V10 p3 nativa + p6 + p52).
- **Código do repositório: 4 falham** (linhas embutidas viram TITLE; H1 vira legenda; título, cabeçalho e metadados numa região; títulos OCR rebaixados), 3 de proteção passam e 1 é pulado. **t3: 8 passam.**
- Proteções incluídas: multiconjunto de palavras e regiões TABLE/FIGURE idênticos; layout da página inclinada V3 p80 idêntico; decisões das regiões **nativas** idênticas às do código atual; "Publicação:" e primeiras linhas de parágrafo não viram títulos; linha de registro numerada não é seção.
- Na camada de layout, a t3 é **idêntica à t1** em todas as páginas capturadas. Os resultados dos Níveis 2–3 da t1 (todas niveladas, sem título numerado) valem para a t3 sem reextração (**36 extrações de página evitadas**).
- Suíte relacionada (701 testes: `test_tesseract_*`, montagem, Markdown, revisões, EasyOCR, RapidOCR 005/011, conservação): repositório e t3 com o **mesmo resultado**, 700 passam e 1 falha (pré-existente, T3-P14).

#### t3 — Nível 4 (base `l4_base` reaproveitada: mesmo código, entradas e subconjunto)

Execução sequencial, 1 processo OCR por vez: 7 processos t3 nos conjuntos originais (74 páginas) + 6 no G′ (base e t3, 20 páginas): 1719 s. **68 extrações de página da base reaproveitadas** da t2.

| Verificação | Resultado |
|---|---|
| Conteúdo | **multiconjunto de palavras idêntico em todas as páginas**; nenhuma queda de recall; nenhuma página perdeu acertos de `numeric`/`currency`/`dates`/`percent` |
| Páginas inclinadas (V4 105/107, V9 9/23, V10 13/18) e S inclinadas (V10 25/68, V9 17) | idênticas à base |
| V3, V5, V6, V8 (S) e V9 S (tabelas p20/p55/p58/p66/p71) | idênticos à base |
| Tabelas | métricas de tabela idênticas em todas as páginas |
| Ordem | 8 páginas mudam só a ordem (mesmas palavras) e **todas melhoram ou empatam**: V4 p116 CER 0.191 → 0.033, p92 0.105 → 0.035, p98 0.219 → 0.039, p118 0.343 → 0.132, p100 (G′) 0.215 → 0.058, p124 empate; V10 p24 0.082 → 0.065, p40 (G′) 0.123 → 0.098 |
| Parágrafos | `paragraph_detection_f1` melhora em todas as páginas alteradas, salvo V4 p118 (0.103 → 0.065) |

**Títulos** (`heading_detection_f1` por página, contagens somadas; V4/V9/V10, sem âncoras):

| Conjunto | tp | fp | fn | Precisão | Recall | F1 |
|---|---|---|---|---|---|---|
| D + G + S + inclinadas (54 págs.) | 22 → **67** | 14 → 20 | 113 → **68** | 0.61 → **0.77** | 0.16 → **0.50** | 0.26 → **0.60** |
| **G′** (17 págs., independente) | 7 → **32** | 6 → 10 | 34 → **9** | 0.54 → **0.76** | 0.17 → **0.78** | 0.26 → **0.77** |
| G′ V4 (6) | 0 → 4 | 0 → 0 | 8 → 4 | — | — | — |
| G′ V9 (5) | 2 → 3 | 2 → 2 | 1 → 0 | — | — | — |
| G′ V10 (6) | 5 → 25 | 4 → 8 | 25 → 5 | — | — | — |

- **Falsos positivos novos** (10 no total), por tipo:
  - rodapé decorativo da página ("Documento sintético … | Folha NN/84", V10 p24/p40/p46): o texto já saía na base como parágrafo; só muda o marcador;
  - separadores do corpus danificados pelo OCR ("58 | OCR de tabela…" V4 p168; "gina 118 | …" V4 p118);
  - linhas de corpo (V10 p40: 2; V10 p66: 1; V4 p118: 1).
- **Títulos que continuam ausentes**: páginas mistas do V9 (causa 2, FIGURE); páginas inclinadas (fora do escopo, T3-P35); "Coluna direita" nas páginas de duas colunas do V4 (razão 1.03, sem sinal de altura); títulos de coluna com continuação "— …" que vira TEXT.
- **Hierarquia**: os títulos OCR recebem níveis pelo agrupamento das alturas OCR do documento. `heading_level_match_rate` das páginas novas varia de 0.0 a 1.0; no V4 os níveis ficam desalinhados pela convenção do gabarito (§1.16), que **não** foi compensada.
- **Generalização**: comprovada no G′ (17 páginas niveladas nunca vistas, de três corpora) para **texto nivelado**. **Não comprovada** para páginas inclinadas e mistas, que ficam fora do escopo por construção.

**Proposta para aprovação** (componentes e riscos):
- `layout/heading.py` (**compartilhado**: aprovação necessária): (A), aplicada só a regiões TITLE sem `font_size` nativo, com OCR, em página nivelada. Documentos 100% escaneados e todas as regiões nativas mantêm o comportamento atual (verificado no Nível 1).
- `layout/ocr_aware.py` (regiões só-OCR de qualquer motor): (B), (C) e (D), só em texto nivelado.
- Prefixo de seção estrito e com texto alfabético: só no caminho novo (a regex atual continua no caminho nativo).
- Riscos: falsos positivos residuais acima; a ordem de páginas de duas colunas muda (sempre para melhor nos 8 casos medidos, mas é mudança de sequência e precisa ser declarada na RUN 012); EasyOCR/RapidOCR em documentos mistos passam a emitir títulos OCR.
- **Fora desta proposta**: V9 mistas (causa 2), inclinadas (T3-P35) e rodapé decorativo como título (possível t4: rejeitar título OCR na faixa inferior da página, como o score nativo já penaliza).

**Custo do ciclo T3-P06** (medido):

| Etapa | Processos OCR | Extrações de página | Tempo de extração | Testes |
|---|---|---|---|---|
| Nível 0 (captura D, sem âncora) | 3 | 10 | 241 s | — |
| t1 Nível 2 | 4 | 12 | 96 s | — |
| t1 Nível 3 | 6 | 24 | 515 s | — |
| Capturas de componente (V4 p118, V10 p3/6/52, V3 p80) | 3 | 7 | 67 s | componente < 1 s por execução |
| t2 Nível 4 (base + t2) | 14 | 136 | 2530 s | — |
| t3 Nível 4 + G′ (t3 + base/t3 do G′) | 13 | 108 | 1719 s | suíte de 701 testes, 3 execuções |
| **Total** | **43** (sequenciais) | **297** | **5168 s (≈ 86 min)** | — |

Reaproveitado: 68 extrações de página da base no Nível 4 da t3 e 36 dos Níveis 2–3 da t1, ou seja 104 extrações evitadas. Tempo total do ciclo (relógio): ≈ 2 h.

### §1.26 Ciclo pós-011: T3-P35 — diagnóstico da reconstrução no referencial endireitado (2026-11-19, sem alteração de código)

Somente investigação (Nível 0): nenhuma extração nova para T3-P35, nenhuma alteração. Fontes: saídas e métricas da RUN 011, manifesto V10, código de `ocr/backends/tesseract.py`, `ocr/reconstruct.py` e `layout/ocr_aware.py`.

**Mecanismo medido**:
- Em página inclinada, o Tesseract lê o raster **endireitado** (T3-P18, `_deskew_page`), mas `_unrotate_tsv` devolve cada palavra como o **retângulo alinhado aos eixos que envolve os quatro cantos girados**. A geometria exata da leitura endireitada é descartada. Todo o restante (linhas, alturas, calhas, títulos, parágrafos, ordem) trabalha com caixas infladas e com uma inclinação reestimada (`_engine_line_slope`).
- **Inflação nas 12 páginas L01/L02** (manifesto: inclinação 4.10°–4.87°, colunas de 147 pt): cada linha da largura da coluna ganha `147·sen θ` = **10.5–12.5 pt** de altura, 1.6–1.9× a altura de uma linha de corpo (≈ 6.5 pt, passo ≈ 9 pt). As caixas de linhas vizinhas se sobrepõem.
- **Efeito na 011**: recall 0.909–0.997 com CER 0.157–0.378 (erro de ordem, não de leitura); `heading_detection_f1` 0.00–0.29; `paragraph_detection_f1` 0.08–0.34; `list_item_detection_precision` 0.0 (as continuações "— …" de títulos de coluna, T3-P31).
- **Coordenadas reaproveitáveis**: a caixa atual é o envelope exato de um retângulo girado, então `W = w·cos θ + h·sen θ` e `H = w·sen θ + h·cos θ` devolvem a largura e a altura reais. Verificado com a própria `_unrotate_tsv` em 800 caixas sintéticas (±1.5°–4.6°): erro ≤ 0.01 px. Além disso, os tokens Tesseract já levam `polygon_px` (hoje retangular), e `reconstruct._dominant_slope` já sabe usar polígonos: o backend pode entregar o quadrilátero girado **sem mudar o modelo de dados**.

**A abordagem difere das tentativas anteriores?** Sim:

| Tentativas anteriores | O que fizeram | Por que falharam |
|---|---|---|
| T3-P31 série 1, t4 (pós-005) | altura corrigida pela inclinação só na classificação | mudou a divisão em regiões; a montagem continuou ordenando com caixas infladas e embaralhou linhas (V10 p13/p48) |
| T3-P31 série 2, t1–t5 (pós-006) | junção de continuações, quebra de região por calha, `order_mode` na ordem compartilhada, TITLE por junção, fechamento de grupo | correções locais sobre geometria inflada; a ordem genérica compartilhada (`text/reading_order.py`) continuou recebendo caixas infladas (V10 p18 0.322 → 0.525) |
| T3-P35 t1–t2 (pós-007) | unir pedaços colineares de calha; t2 desliga a união em página inclinada | t1 perdeu conteúdo na V10 p25 (deduplicação com títulos inflados); t2 só evita o problema |

Todas misturaram geometria corrigida e inflada no mesmo fluxo. A proposta nova muda o **referencial de toda a cadeia OCR da página de uma vez** e mantém inalterados os componentes compartilhados de ordem.

**Desenho técnico proposto (não implementado)**:
1. **Escopo**: só páginas raster integrais (sem linhas nativas) em que o Tesseract aplicou o endireitamento (|θ| entre 1° e 10°, o mesmo critério de `_deskew_page`). Páginas niveladas não entram, porque θ = 0 e o caminho fica idêntico byte a byte.
2. **Geometria**: o backend grava o quadrilátero girado em `polygon_px` e o ângulo nos fatos da página; alternativa sem tocar o backend: inverter as caixas pela fórmula acima com θ medido.
3. **Fronteira**: antes do agrupamento de linhas (`reconstruct`), girar tokens e âncoras (tabelas e figuras) da página por −θ em torno do centro. Linhas, layout OCR, parágrafos e ordem dentro das regiões passam a ver uma página nivelada. Depois da montagem, mapear as caixas de saída de volta ao referencial da página (envelope do retângulo girado).
4. **Componentes**: `ocr/backends/tesseract.py` (só Tesseract) e o ponto de orquestração da página em `api.py` (**compartilhado**: exige autorização). `text/reading_order.py`, `layout/heading.py` e a montagem não mudam.

**Hipóteses verificáveis** (em ordem de custo):
- H1 (Nível 1, segundos): com as entradas reais capturadas de uma página L02 (ex.: p18) endireitadas pela fórmula, a ordem das linhas produzida por `reconstruct` + `reconstruct_ocr_layout` segue as colunas do gabarito (CER da sequência de linhas perto do das L04–L06, ≈ 0.10) e as alturas de título voltam a ≥ 1.28× o corpo. **Refuta** se a ordem continuar intercalando colunas com geometria nivelada.
- H2 (Nível 2): p18, p13 + controle nivelado (V10 p52) + V4 p107 (2.5°): CER das inclinadas cai, recall não cai, controle idêntico byte a byte.
- H3: as listas falsas de T3-P31 desaparecem como efeito colateral (medir, não perseguir).

**Limitações e riscos**:
- Páginas mistas inclinadas (V9 p23, p44, com texto nativo nivelado e raster inclinado) ficam fora: não há um único referencial para a página.
- Tabelas raster inclinadas (T3-P34, V9 p58/p66) têm transformação própria (`source_transform`); a fronteira precisa transformar as âncoras sem alterar a detecção visual.
- A deduplicação que perdeu conteúdo na V10 p25 (T3-P35 t1) precisa de controle de recall por página.
- Coordenadas expostas no JSON mudam (envelope recalculado), mesmo com Markdown igual.

**Custo estimado de validação**:

| Nível | Páginas | Processos OCR |
|---|---|---|
| 0 (captura das entradas) | V10 p18, p13, p52 | 1 |
| 1 | captura (offline) | 0 |
| 2 | 4 páginas, base × candidata | 2–4 |
| 3 | L01/L02 de desenvolvimento (6) + V4 p105/p107 + V9 p9/p17 | 4–6 |
| 4 | G: L01/L02 reservadas (p45, p53, p75, p83) + inclinadas de outros corpora; S: suíte fixa da §5 | ~10 |

Total estimado: **≈ 17–21 processos sequenciais** (≈ 1–1.5 h de extração), mais a autorização para `api.py`.

**Conclusão**: existe uma abordagem nova tecnicamente fundamentada. Ela ataca a causa medida (caixa inflada) em toda a cadeia OCR de uma vez, reaproveita coordenadas já existentes e é refutável no Nível 1 sem OCR. T3-P35 continua com 2/5 tentativas; nenhuma foi gasta neste ciclo.

### §1.27 T3-P06 t3 implementada no repositório (autorização condicionada, 2026-11-19, 13:03–13:22 UTC; não commitada)

**Autorização**: alterar `layout/heading.py` exclusivamente para T3-P06, preservando o caminho nativo. T3-P35 e T3-P41 não foram tocados.

#### Alteração implementada

| Arquivo | Mudança |
|---|---|
| `src/structured_pdf_text/layout/heading.py` | `assign_heading_levels` escolhe o critério **pela proveniência de cada região TITLE**: `_is_ocr_only` (tem `ocr_lines` e nenhum `font_size` nativo) → `_assign_ocr_title_levels`; demais regiões → `_assign_typography_levels`, que é o corpo anterior **sem nenhuma mudança** e roda depois da fusão de fragmentos. `_assign_ocr_title_levels`: referência = mediana das alturas de linha OCR de corpo da página (≥ 5 linhas; senão, do documento); página com \|inclinação\| > `LEVEL_TEXT_MAX_SLOPE` não aceita título OCR; aceite = score §28 ≥ 1.0 **e** (altura ≥ `OCR_TITLE_LINE_RATIO` × corpo, ou prefixo de seção estrito com texto alfabético, ou score ≥ 2.0); o bônus de prefixo do score é retirado quando o prefixo não é seção ("A revisão…", "001 V10-…"); níveis por agrupamento das razões de altura dos títulos OCR aceitos. **Integridade**: `_merge_heading_fragments` passa a concatenar também `ocr_lines` (antes só tokens e linhas nativas; a montagem renderiza regiões OCR por `ocr_lines`) |
| `src/structured_pdf_text/layout/ocr_aware.py` | `OCR_TITLE_LINE_RATIO = 1.28` (substitui o literal já usado); só em texto nivelado: (B) `_is_embedded_in_paragraph` (vãos acima e abaixo ≤ 0.35 × corpo, linha anterior TEXT) mantém TEXT; (C) `_starts_new_heading` (vão > altura da linha) separa regiões TITLE; (D) linha com razão ≥ 1.28 não vira legenda pela palavra inicial |
| `src/structured_pdf_text/ocr/reconstruct.py` | `LEVEL_TEXT_MAX_SLOPE = 0.005` (público); `_MERGE_GUTTERS_MAX_SLOPE` passa a ser um alias dele (mesmo valor, T3-P35 t2 inalterado) |
| `tests/test_tesseract_run011_regressions.py` (novo) | 8 testes com geometria real (V10 p52/p6, V9 p2) |

**Diferenças em relação à t3 em memória**, ambas exigidas ou justificadas:
1. **Documentos 100% OCR**: a t3 mantinha o caminho antigo quando o documento não tinha nenhuma fonte nativa, uma decisão global que a autorização veda. A implementação usa o critério por proveniência em qualquer documento.
2. **`ocr_lines` na fusão de fragmentos de título**: correção de integridade, não heurística. Nas entradas reais capturadas (5 conjuntos) nenhuma fusão envolve linhas OCR, então nada do que foi validado muda. Sem ela, um título OCR de duas regiões fundidas perderia a segunda linha.

#### Evidência de equivalência com a candidata

| Verificação | Resultado |
|---|---|
| Nível 1 offline: implementação × t3 (código de `2fdad33` extraído por `git archive`) sobre todas as entradas reais capturadas: 16 layouts de página e `assign_heading_levels` de 6 subconjuntos mistos | **22 / 22 idênticos** |
| Nível 2: V4 {20, 82, 83} e V10 {3, 6, 52} com a implementação × saídas da candidata no mesmo subconjunto | **Markdown idêntico byte a byte** |
| Consequência | métricas e bateria do Nível 4 da t3 (§1.25: 74 páginas + G′ 20) **reaproveitadas**; 297 extrações não repetidas |

#### Controles adicionais com a implementação real

| Controle | Resultado |
|---|---|
| Documento só-OCR (diferença 1): V4 {82, 83, 91} e V10 {6, 8, 9, 52} sem âncora, contra as bases já extraídas no mesmo contexto | palavras idênticas em todas as páginas; V4 p82: 5 títulos falsos do caminho antigo → 0, CER 0.180 → 0.032; V10 p6/p8/p52: títulos 0 → 5 cada, 0 falsos; V4 p83/p91 e V10 p9 inalteradas; `heading_detection_f1` V10 0.313 → 0.851, V4 0.364 → 0.667 |
| **Nativo, documento inteiro**: V7 (80 págs., fora do alcance) | **idêntico à RUN 011 byte a byte** |
| **Híbrido, documento inteiro**: V5 (42 págs., páginas híbridas) | **idêntico à RUN 011 byte a byte** |
| Nativo no Nível 4 (t3 ≡ implementação): âncoras V4 p20, V9 p5, V10 p3; V3 p1; V6 p11; V8 p4/p58 | idênticos à base (texto, títulos e níveis, listas, tabelas, dados, ordem, parágrafos) |
| Suíte completa (`pytest tests`) | **3491 passed, 13 failed, 4 skipped, 4 xfailed**. As 13 falhas são exatamente as de T3-P14: `test_line_detector.py` (8 `test_reconciliation_*`), `test_rapidocr_improvement_005.py::test_quoted_and_verbatim_blocks_render`, `test_rapidocr_improvement_006.py::test_unshaded_table_does_not_acquire_an_invented_header`, `test_rapidocr_improvement_008.py::test_block_above_and_left_precedes_block_below_right`, `test_no_timeouts.py::test_production_code_has_no_timeout_keyword_argument` e `test_evaluation_exact_optimizations.py::test_full_ground_truth_self_control_is_complete_and_perfect[V3…]`. **Nenhuma falha nova**, inclusive nos testes de EasyOCR/RapidOCR e de montagem nativa |
| `tests/test_tesseract_run011_regressions.py` | repositório: **8 passed**. Código de `2fdad33`: **6 failed** pelos motivos esperados (linha embutida vira TITLE; cabeçalho, H1 e metadados numa região; H1 vira CAPTION; título OCR rebaixado em documento misto; linhas de metadados, artigo e registro não testáveis porque o H1 também some; fusão perde "serviços") e 2 passam (proteções: decisões nativas independentes das páginas OCR; título OCR em texto inclinado continua TEXT) |

#### Páginas reordenadas (8), verificação individual

| Página | Palavras | Valores críticos (acertos num/cur/dat/pct) | CER | Ordem de blocos | Parágrafos F1 | Fusões / quebras (§1.22) |
|---|---|---|---|---|---|---|
| V4 p92 | idênticas (702) | iguais (51/8/8/8) | 0.105 → 0.035 | 1.000 → 1.000 | 0.462 → 0.727 | 0 → 0 / 4 → 2 |
| **V4 p116** | idênticas (706) | iguais (56/8/8/8) | **0.191 → 0.033** | 0.709 → 1.000 | 0.154 → 0.636 | 0 → 0 / 2 → 2 |
| V4 p124 | idênticas (707) | iguais | 0.058 → 0.058 | 1.000 → 1.000 | 0.462 → 0.609 | 0 → 0 / 5 → 2 |
| V4 p98 | idênticas (694) | iguais | 0.219 → 0.039 | 0.818 → 1.000 | 0.061 → 0.636 | 0 → 0 / 10 → 3 |
| V4 p118 | idênticas (685) | iguais | 0.343 → 0.132 | 0.782 → 0.964 | **0.103 → 0.065** | 0 → 0 / **5 → 12** |
| V4 p100 (G′) | idênticas (702) | iguais (55/8/8/8) | 0.215 → 0.058 | 0.782 → 1.000 | 0.065 → 0.522 | 0 → 0 / 6 → 3 |
| V10 p24 | idênticas (720) | iguais | 0.082 → 0.065 | 0.965 → 0.974 | 0.578 → 0.757 | 0 → 0 / 4 → 2 |
| V10 p40 (G′) | idênticas (721) | iguais | 0.123 → 0.098 | 0.905 → 0.937 | 0.370 → 0.435 | 0 → 0 / 7 → 6 |

- Total: nenhuma fusão; quebras 43 → 32. CER nunca aumenta.
- **V4 p116**: o 0.191 → 0.033 vem da intercalação de colunas eliminada. As linhas de corpo de caixa alta viravam regiões TITLE separadas que a montagem intercalava; com (B) elas ficam na região da coluna, e a ordem de blocos vai a 1.000. É coerente com o gabarito: parágrafos 116.01–116.20 da esquerda e depois os da direita.
- **V4 p100**: num ponto, "R$" ficou no fim de um bloco e "14.057,00," no início do seguinte (quebra no meio da expressão monetária). O valor é idêntico e o acerto de `currency` se mantém (8 → 8), mas é uma quebra indevida. No total da página as quebras caem de 6 para 3.
- **V4 p118 (regressão conhecida, sem correção neste ciclo)**: página `left_shadow`, OCR muito degradado. O falso título residual "Parágrafo 118.23…" (razão 1.28, exatamente o limiar) vira região própria e corta a coluna direita em pedaços; a montagem os intercala e a segmentação quebra os parágrafos (quebras 5 → 12, F1 0.103 → 0.065), embora o CER caia de 0.343 para 0.132. A regra que resolvia esse caso ((B′), t2) foi rejeitada por perda de conteúdo.

#### Falsos positivos residuais (10, iguais aos da validação anterior; nenhum novo)

| Tipo | Qtde. | Páginas | Observação |
|---|---|---|---|
| Rodapé decorativo de página ("Documento sintético … \| Folha NN/84") | 3 | V10 p24, p40, p46 | o texto já saía na base como parágrafo; só muda o marcador |
| Separador do corpus danificado pelo OCR ("58 \| OCR de tabela…", "gina 118 \| …") | 2 | V4 p168, p118 | a supressão de separador não os reconhece por causa do OCR |
| Linha de corpo | 4 | V10 p40 (2), p66; V4 p118 | — |
| Fragmento de título (2ª linha do H1 separada: "### continuados") | 1 | V9 p84 | o H1 é detectado (1 acerto); a 2ª linha conta como falso |

- Precisão × recall de títulos (base → t3): conjuntos D+G+S+inclinadas 0.61 → 0.77 / 0.16 → 0.50; G′ 0.54 → 0.76 / 0.17 → 0.78. **A precisão sobe**: o ganho de recall não esconde queda de precisão.
- **Melhoria proposta, não implementada** (precisa de aprovação separada): rejeitar título só-OCR na faixa inferior da página (≤ 12% da altura), como o score nativo e o §28 já penalizam. Nos dados, eliminaria os 3 rodapés (y ≈ 810 de 842) sem atingir nenhum título verdadeiro medido (o mais baixo está em y ≈ 448). Estimativa: precisão D+G+S 0.77 → ≈ 0.80 e G′ 0.76 → ≈ 0.78 (só 1 dos falsos do G′ é rodapé).

#### Limitações de generalização

- Comprovada para **texto OCR nivelado** em V4, V9 e V10 (G′ independente: F1 de títulos 0.26 → 0.77).
- **Não se aplica** a páginas inclinadas (T3-P35; mantidas idênticas por construção) nem às páginas mistas do V9, onde o texto OCR está em regiões FIGURE (causa 2, sem alteração).
- **Hierarquia**: os níveis dos títulos OCR vêm do agrupamento de alturas; `heading_level_match_rate` das páginas novas varia de 0.0 a 1.0, e o V4 segue a convenção do gabarito sem compensação.
- Documentos só-OCR (fora dos 8 corpora) mudam para o critério novo; validado só nos subconjuntos V4/V10 sem âncora.
- **EasyOCR/RapidOCR**: as regras valem para regiões só-OCR de qualquer motor. A suíte desses motores passa; não houve extração com eles.

#### Custo desta etapa

| Etapa | Processos OCR | Extrações de página | Tempo |
|---|---|---|---|
| Integração mínima (equivalência) | 2 | 6 | 51 s |
| Contexto só-OCR | 2 | 7 | 231 s |
| V7 inteiro (1ª tentativa com só 50 de 80 páginas, refeita) + V5 inteiro + V7 inteiro | 3 | 172 | 344 s |
| Suíte completa | — | — | 120 s |
| **Total** | **7** (sequenciais) | **185** | **≈ 12.5 min** |

Reaproveitado: a bateria inteira da t3 (§1.25), por equivalência demonstrada.

**Status**: T3-P06 **CORRIGIDO — VALIDADO PONTUALMENTE** (t3, 3/5): todos os controles exigidos passaram (equivalência, nativo idêntico, só-OCR sem perda, suíte sem falha nova, reordenações sem perda nem aumento de CER). Regressão conhecida: V4 p118 (parágrafos). **Não é RESOLVIDO** antes da RUN 012.

### §1.28 RUN 012 — auditoria integral e comparação com a 011 (análise de 2026-11-19, commit `18ccfb0`)

Etapa exclusivamente de auditoria: nenhuma extração, nenhum teste, nenhuma alteração de código, testes, PDFs, gabaritos, manifestos, configuração ou avaliador. Fontes: `output/tesseract/011` e `012`, `corpus/` e, só para comparar com a candidata, as saídas de subconjunto já extraídas no ciclo pós-011 (scratchpad). Scripts novos, só de leitura, no scratchpad (`a12/`); fusões/quebras e rubrica usam os **mesmos** scripts da §1.22/§1.23.

#### Verificação operacional

| Item | Resultado |
|---|---|
| Execução | 2026-11-19 13:34:59 → 15:06:13 UTC. 8/8 com extração `exit 0`, métricas `ok`, `rank_eligible` e as mesmas métricas aplicáveis da 011 (V3 61, V4 61, V5 67, V6 63, V7 65, V8 65, V9 67, V10 61) |
| Código | `implementation_commit` `18ccfb0` nos 8 arquivos de métricas = `2fdad33` + T3-P06 t3 (§1.27). Commit às 13:29 UTC, antes do início da rodada. `git diff 2fdad33 18ccfb0`: só `layout/heading.py`, `layout/ocr_aware.py`, `ocr/reconstruct.py`, o teste novo e este documento. `src/`, `tests/` e `corpus/` sem diferença para `HEAD` |
| Entradas | `input_hashes` (PDF, gabarito, manifesto) iguais aos da 011 nos 8 corpora; os hashes recalculados hoje dos arquivos em `corpus/` conferem. `settings` e `extractor_flags` iguais |
| Avaliador | `evaluation-3.0.0`, métricas 3.1.0; todas as políticas do bloco `evaluator` iguais; só `implementation_commit` e `created_at_utc` mudam. Nenhum `corpus_changed` |
| Manifestos de execução | idênticos aos da 011 em todos os campos, salvo `run_id`, `elapsed_s` e caminho de saída: mesmos estados de página, páginas recuperadas e degradadas e os mesmos avisos. **Nenhum aviso novo** |
| Status | 7 corpora `success`/`valid`; V9 `partial_success`/`partial` → `needs_review` só pela p36 (T3-P33, mesma exceção `Invalid BBox`) |
| Tempo de extração (011 → 012) | V3 +2.5% · V4 +7.2% · V5 +6.2% · V6 +7.4% · V7 +8.2% · V8 +4.0% · V9 +0.5% · V10 +5.0%. Os corpora de saída idêntica também ficaram mais lentos: ruído do ambiente, dentro da faixa (−11% a +14%) |

#### Páginas que mudaram (012 × 011, comparação byte a byte)

**V3, V5, V6, V7 e V8 idênticos byte a byte** (mesmo `hypothesis_sha256`). Mudaram **103 páginas** (V4 58, V9 16, V10 29), todas com regiões OCR. **Nenhuma página nativa mudou.**

| Classe | V4 | V9 | V10 | Total |
|---|---|---|---|---|
| 1. Só marcador ou nível de título (texto idêntico sem os `#`) | 38 | 16 | 19 | **73** |
| 2. Só segmentação de parágrafos (mesma sequência de palavras) | p94, p122 | — | p84 | 3 |
| 3. Ordem de leitura (mesmo multiconjunto de palavras, sequência diferente; com títulos e segmentação) | p82, 86, 88, 92, 95, 96, 98, 100, 104, 116, 118, 124, 128, 130, 142 | — | p24, 40, 52, 56, 78 | 20 |
| 4. Conteúdo | p157 (−4 sinais inventados) | — | p5, p11, p31, p62 (+83 palavras recuperadas) | 5 |
| 5. Tabelas, listas ou dados críticos | p145, p149 (linhas de tabela raster não detectada unidas num parágrafo); p157 (lista falsa desfeita, também na classe 4) | — | — | 2 (+ p157) |

- **Métricas de tabela e de dados críticos (`numeric`, `currency`, `dates`, `percent`, identificadores) idênticas em todas as páginas dos 8 corpora.** Nenhuma página perdeu acertos.
- **Conteúdo, V10 p5, p11, p31, p62 (+4, +4, +22, +53 palavras)**: são palavras que **faltavam na 011** e voltam na 012. Recall da página 0.936 → 0.941, 0.976 → 0.982, 0.969 → 0.997, 0.924 → 0.996; recall do V10 0.9915 → 0.9938. Mecanismo: a correção de integridade de `_merge_heading_fragments` (§1.27), que passou a juntar também `ocr_lines`. Até a 011, quando duas regiões TITLE só-OCR eram fundidas (inclusive em páginas inclinadas, onde depois o título é rebaixado a texto), as linhas OCR da segunda região eram descartadas e a montagem, que renderiza regiões OCR por `ocr_lines`, as perdia **sem aviso**. Três das quatro páginas são inclinadas (L01/L02). A §1.27 dizia que essa correção não mudava nada nas entradas capturadas; na rodada completa ela recuperou conteúdo perdido. Registrado como **T3-P42** (perda silenciosa desde antes da Série 3 atual, corrigida e confirmada na 012).
- **Conteúdo, V4 p157 (−2 × "-" e −2 × "☐")**: os sinais **não existem no gabarito**; eram inventados pela regra `_promote_post_table_checklist_items` do pós-processamento Tesseract (≥ 2 blocos de texto curtos depois de uma tabela viram lista "- ☐"). Na 012 uma das duas linhas virou título (falso: linha da tabela), a regra deixa de disparar e as duas linhas saem sem o marcador. CER 0.272 → 0.240; listas do V4 sobem (precisão de grupos 0.389 → 0.412). Efeito positivo, obtido por meio de um falso título.
- **V4 p145 e p149 (classe 5)**: as linhas de uma tabela raster **não detectada** (como na 011) saíam uma por parágrafo e agora saem unidas num parágrafo só ("| Caneta … | Pago | Caderno … | Pendente | Borracha … | Pago"). Mesmas palavras e valores; nenhuma métrica oficial muda (não há tabela detectada em nenhuma das rodadas), mas a separação visual das linhas se perde. A p145 é página S do ciclo pós-011 e já mudava na t3 do Nível 4 (a saída da candidata é igual à 012); a mudança **não** foi destacada na §1.25. Mecanismo provável: (B)/(C) do layout OCR no agrupamento das linhas; não isolado nesta auditoria (sem captura).
- **Páginas inclinadas**: V4 p105–p108, p135, p136, V9 p9, p17, p23, p44, p81, V3 p80 e 9 das 12 L01/L02 do V10 idênticas byte a byte. **V10 p5, p31 e p62 mudaram** só pela recuperação de conteúdo acima (sem título, segmentação ou ordem nova; o texto recuperado entra no bloco do fragmento, o que explica o CER um pouco maior na p5 e na p31).

#### Métricas oficiais que mudaram (documento)

| Corpus | Métrica | 011 | 012 |
|---|---|---|---|
| V4 | `heading_detection_f1` / `precision` / `recall` | 0.502 / 1.000 / 0.335 | **0.700** / 0.929 / **0.562** |
| V4 | `heading_text_cer` / `heading_level_match_rate` | 0.631 / 0.000 | 0.486 / 0.000 |
| V4 | `cer_sequential_normalized` / `wer_sequential` | 0.0535 / 0.1449 | **0.0376** / 0.1312 |
| V4 | `paragraph_detection_f1` / `paragraph_boundary_f1` | 0.421 / 0.553 | **0.489** / **0.591** |
| V4 | `paragraph_boundary_recall` | 0.5772 | 0.5762 |
| V4 | `list_group_detection_f1` / `list_item_detection_f1` | 0.560 / 0.875 | 0.583 / 0.894 |
| V9 | `heading_detection_f1` / `precision` / `recall` | 0.426 / 0.541 / 0.351 | **0.509** / 0.575 / **0.457** |
| V9 | `heading_level_match_rate` / `heading_text_cer` | 0.925 / 0.667 | **0.768** / 0.570 |
| V9 | `paragraph_detection_f1` / `paragraph_boundary_f1` | 0.595 / 0.697 | 0.605 / 0.711 |
| V10 | `heading_detection_f1` / `precision` / `recall` | 0.430 / 0.772 / 0.298 | **0.721** / **0.835** / **0.634** |
| V10 | `heading_level_match_rate` / `heading_text_cer` | 0.977 / 0.821 | **0.519** / 0.534 |
| V10 | `cer_sequential_normalized` / `wer_sequential` | 0.1409 / 0.1692 | 0.1373 / 0.1649 |
| V10 | `text_coverage_recall` / `precision` | 0.99147 / 0.97423 | **0.99384** / 0.97408 |
| V10 | `paragraph_detection_f1` / `paragraph_boundary_f1` | 0.459 / 0.592 | 0.497 / 0.630 |

**Idênticas à 011**: todas as métricas de V3, V5, V6, V7 e V8; em V4/V9/V10, todas as de tabela, dados críticos, identificadores e integridade de páginas; `pairwise_reading_order_accuracy` (V4 1.0000, V10 0.9995).

#### T3-P06 — títulos por origem da página (contagens oficiais `tp`/`fp`/`fn`, 011 → 012)

Origem pela classificação do manifesto (`complexity_reasons`: `scanned` = só OCR; `embedded_images` = mista/híbrida; demais = nativa) e as páginas inclinadas da §5. Todas as páginas OCR de V4/V9/V10 estão em **documentos mistos** (cada um tem páginas nativas); nenhum dos 8 corpora é só-OCR.

| Corpus | Nativas | Híbridas / mistas | Só OCR, niveladas | Inclinadas |
|---|---|---|---|---|
| V4 | 56/0/29 → **igual** | 6/0/2 → **igual** (8 págs. híbridas adversariais) | 0/0/84 → **42/8/42** | 0/0/8 → igual |
| V9 | 36/31/2 → **igual** | 17/14/77 → **28/19/66** | 0/0/8 → **5/1/3** | 0/0/11 → igual |
| V10 | 86/26/6 → **igual** | 0/0/33 → **26/6/7** | 0/0/98 → **73/5/25** | 2/0/70 → igual |

- **Nativas**: idênticas byte a byte; nenhum título nativo mudou de decisão ou nível. O caminho nativo compartilhado de `layout/heading.py` ficou preservado.
- **Híbridas do V4** (texto nativo + raster): inalteradas.
- **Mistas do V9** (texto OCR em regiões de figura, causa 2): +11 acertos em páginas onde o título OCR fica fora da figura; os demais continuam ausentes, como previsto.
- **Inclinadas**: sem título novo, por construção (critério de texto nivelado). 89 títulos do gabarito continuam ausentes nelas (V10 70, V9 11, V4 8).
- **Documentos só-OCR**: não há nenhum nos 8 corpora; o comportamento só-OCR da implementação continua validado apenas nos subconjuntos sem âncora da §1.27.

**Comparação com a candidata validada em memória**: nas 77 páginas de V4/V9/V10 extraídas com a candidata no ciclo pós-011 (Nível 4 + G′), **o conjunto e o texto dos títulos são os mesmos da 012 em todas**. Em 26 páginas só o **nível** difere (ex.: V10 p21 `### Coordenação…` na candidata e `###### Coordenação…` na 012). Causa: os níveis OCR vêm do agrupamento das razões de altura de **todos** os títulos OCR aceitos no documento (vão de 0.12, no máximo 6 grupos); no documento inteiro há mais grupos do que no subconjunto e os títulos de coluna caem no 6º. As diferenças de palavras em V4 p20/p140, V9 p38 e V10 p73 já existiam entre a base do subconjunto e a 011 (efeito de contexto do subconjunto) e o delta base → candidata é igual ao 011 → 012.

**Títulos novos, um a um** (interpretação: semelhança do texto com o título do gabarito na página; ≥ 0.8 forte, 0.6–0.8 parcial, < 0.6 falso):

| Corpus | Títulos novos | Fortes | Parciais | Falsos reais | `fp` oficiais | Falsos contados como `tp` pelo avaliador |
|---|---|---|---|---|---|---|
| V4 | 50 | 38 | 1 | 11 | 8 | 3 (p95, p104, p110) |
| V9 | 22 | 16 | 0 | 6 | 6 | 0 |
| V10 | 110 | 94 | 5 | 11 | 11 | 0 |
| **Total** | **182** | **148** | **6** | **28** | **25** | **3** |

- O avaliador casa títulos de baixa semelhança: V4 p95 (separador danificado "Págino 095 L OCR de texto…", `heading_text_cer` 1.00), p104 (linha ilegível, 3.48) e p110 (cabeçalho corrido "CORPUS STRESS OCR MARKDOWN V4 / …", 2.90) contam como acertos. A precisão real do V4 é menor que a oficial (≈ 39 de 50 títulos novos verdadeiros).
- Os parciais são títulos de coluna cujo complemento foi para a linha seguinte ("Encaminhamento e providências:" sem "recolhimento de mat…"), V10 p6, p19, p66, p70, p81, e "| esquerda" na V4 p118.

**Hierarquia** (níveis dos títulos novos verdadeiros, gabarito → saída):
- V4: 39 de 39 `###` → `######` (`heading_level_match_rate` já era 0 pela convenção do gabarito, §1.16).
- V9: 16 `#` → `#` 4, `##` 4, `###` 8. `heading_level_match_rate` 0.925 → 0.768.
- V10: 26 `#` → `#` 11, `##` 6, `###` 1, `####` 2, `#####` 6; 73 `###` → `######` 65, `#####` 7, `####` 1. `heading_level_match_rate` 0.977 → **0.519**.
- **Nenhum título verdadeiro perdeu acerto** (nenhuma página com `tp` menor) e nenhum título nativo mudou de nível; a queda da taxa vem só dos títulos novos com nível errado.

#### Critérios da §5 (RUN 012)

| Verificação | Critério | 012 | Situação |
|---|---|---|---|
| Geral | 8/8 `ok`; hashes e avaliador iguais; V9 `partial` só por T3-P33 | conforme | ✅ |
| Títulos — recall | V4 ≥ 0.45; V10 ≥ 0.55; V9 ≥ 0.351 | **0.562 / 0.634 / 0.457** | ✅ |
| Títulos — precisão | V4 ≥ 0.95; V10 ≥ 0.70; V9 ≥ 0.50 | **0.929** / 0.835 / 0.575 | ❌ V4 |
| Títulos — falsos positivos | nenhuma categoria nova | 3 categorias novas: linha de tabela raster não detectada (V9 p76 ×5, V4 p157), cabeçalho corrido (V4 p110), linha ilegível (V4 p104) | ❌ |
| Conteúdo | multiconjunto de palavras idêntico em toda página; nenhuma queda de recall > 0.001; nenhuma perda de dados críticos | multiconjunto diferente em 5 páginas, mas **só ganhos** (V10 +83 palavras do gabarito recuperadas) e 4 sinais inventados removidos (V4 p157); nenhuma queda de recall; dados críticos idênticos | ⚠️ critério literal não atendido, sem perda |
| Ordem | páginas reordenadas com CER ≤ 011 | 18 de 20 melhoram ou empatam; **V4 p104** 0.528 → 0.546; V4 p124 0.05817 → 0.05837 (empate na 3ª casa) | ❌ p104 |
| Fora do escopo | V5, V7, inclinadas e nativas idênticas byte a byte; V3/V6/V8 idênticos | V3, V5–V8 e nativas idênticos; inclinadas idênticas salvo **V10 p5/p31/p62** (recuperação de conteúdo, T3-P42) | ⚠️ explicado pelo mecanismo |
| Regressão conhecida | V4 p118 F1 de parágrafos ≈ 0.065 com CER melhor; nenhuma outra página com piora de F1 de parágrafos > 0.05 sem explicação | p118 0.103 → 0.065, CER 0.343 → 0.132 (como previsto); **V4 p157** 0.667 → 0.500 (explicada: lista falsa desfeita e falso título) | ✅ com explicação |
| Parágrafos (T3-P38/T3-P20) | F1 V4 ≥ 0.421, V9 ≥ 0.595, V10 ≥ 0.459; V4 coluna única sem piora | 0.489 / 0.605 / 0.497; V4 p83, p85, p99, p125, p129, p137–p143 sem mudança de fusões ou quebras | ✅ |
| Tabelas, listas e dados | métricas de tabela e dados iguais; listas sem piora > 0.005 | iguais; listas do V4 sobem | ✅ |
| Rubrica | nenhum documento abaixo da 011; Títulos sobe em V4 e V10 | nenhum cai; Títulos V4 37.5 → 52.3, V10 48.9 → 62.9 (§1.29) | ✅ |
| Desempenho | ruído | +0.5% a +8.2% | ✅ |

#### Falsos títulos (28 reais: 25 oficiais + 3 contados como acerto)

| Categoria | Já conhecidos (10 da §1.27, todos reproduzidos) | Novos fora das páginas validadas |
|---|---|---|
| Rodapé decorativo ("Documento sintético … \| Folha NN/84") | V10 p24, p40, p46 | V10 p56, p78, p84 |
| Separador do corpus danificado pelo OCR | V4 p118 ("gina 118 \| …"), p168 ("58 \| OCR de tabela…") | V4 p152, p160, p176, p184 (páginas de tabela da família `shadow`: o OCR perde "Página 1" e a supressão de separador não reconhece o resto); V4 p95 ("Págino 095 L …", contado como acerto) |
| Linha de corpo | V10 p40 (2), p66; V4 p118 ("Parágrafo 118.23…") | V10 p11 (fragmento fundido que inclui as palavras recuperadas), p56 |
| Fragmento de título | V9 p84 ("continuados") | — |
| **Linha de tabela raster não detectada** (nova) | — | V9 p76 (cabeçalho "Item Descrição Data …" e 4 linhas "001 peças de reposição 25/09/2026 …"; a regra de prefixo numerado aceita "001 " seguido de texto majoritariamente alfabético); V4 p157 |
| **Cabeçalho corrido** (nova) | — | V4 p110 ("CORPUS STRESS OCR MARKDOWN V4 / LABORATÓRIO SINTÉTICO …", contado como acerto) |
| **Linha ilegível** (nova) | — | V4 p104 ("permanecem (AB) v<77y»7. …", contado como acerto; vai para o topo da página) |

- Em todos os casos o texto **já saía na 011** como parágrafo; muda o marcador (e, na p104, a posição).
- A proposta de faixa inferior (§1.27, não implementada) eliminaria os 6 rodapés do V10 (precisão V10 0.835 → ≈ 0.97), mas **não** resolve o V4, cujos falsos estão no topo da página (separadores) ou em tabelas.

#### Regressões estruturais verificadas

| Página | Verificação | Resultado |
|---|---|---|
| **V4 p118** | F1 de parágrafos, quebras, falso título, CER e palavras | F1 0.103 → **0.065**; quebras 5 → **12**; o falso título "Parágrafo 118.23…" (`######`) corta a coluna direita; CER 0.343 → 0.132; 685 = 685 palavras, multiconjunto idêntico; dados críticos iguais. **Igual ao previsto** na §1.27 |
| **V4 p100** | `R$` × `14.057,00`; ordem; integridade semântica | "… o valor associado é R$" fecha o bloco do parágrafo 100.21 e "14.057,00, a taxa é +50,25% …" abre o seguinte: **quebra no meio da expressão monetária** (como previsto). Valor idêntico e acerto de `currency` mantido (8 → 8). Ordem de blocos 0.782 → 1.000 (coluna esquerda 100.01–100.04, depois a direita 100.21–100.24). Para um leitor, a associação símbolo–valor fica separada por uma quebra de parágrafo |
| **V4 p116** | CER e colunas | CER **0.191 → 0.033** (confirmado); ordem de blocos 0.709 → 1.000; parágrafos 116.01–116.20 da coluna esquerda e depois os da direita, como no gabarito |
| **V4 p95, p96** (novo) | título verdadeiro | o título "OCR denso heavy noise – instância A/B" passa a ser **unido à linha "Controle de página: V4-P09x"** num só bloco (era bloco próprio na 011; nas duas rodadas não é marcado como título). Na p95 o separador danificado vira `#` no topo e é contado como acerto. Ordem de blocos 1.000 → 0.982 e 0.909 → 0.818; CER igual / 0.086 → 0.075. Mecanismo provável: (B) do layout OCR com caixas ruidosas (família `heavy_noise`); não isolado |
| **V4 p104** (novo) | ordem | página ilegível (penalidade desde a 009); a linha ilegível vira `######` e vai para o topo; CER 0.528 → **0.546**, ordem de blocos 0.909 → 0.800 |
| **V4 p145, p149** (novo) | linhas de tabela | linhas de tabela não detectada unidas num parágrafo (acima); sem efeito nas métricas |

**Páginas reordenadas (20), uma a uma** — palavras e multiconjunto idênticos e acertos de `numeric`/`currency`/`dates`/`percent` iguais em **todas**:

| Página | CER 011 → 012 | Ordem de blocos | `paragraph_detection_f1` | Fusões / quebras (§1.22) |
|---|---|---|---|---|
| V4 p82 | 0.196 → 0.032 | 0.782 → 1.000 | 0.125 → 0.522 | 0/7 → 0/4 |
| V4 p86 | 0.213 → 0.035 | 0.818 → 1.000 | 0.000 → 0.545 | 0/8 → 0/3 |
| V4 p88 | 0.340 → 0.074 | 0.873 → 1.000 | 0.000 → 0.296 | 1/7 → 0/6 |
| V4 p92 | 0.105 → 0.035 | 1.000 → 1.000 | 0.462 → 0.727 | 0/4 → 0/2 |
| V4 p95 | 0.085 → 0.085 | 1.000 → 0.982 | 0.071 → 0.077 | 5/10 → 5/10 |
| V4 p96 | 0.086 → 0.075 | 0.909 → 0.818 | 0.071 → 0.077 | 6/13 → 6/12 |
| V4 p98 | 0.219 → 0.039 | 0.818 → 1.000 | 0.061 → 0.636 | 0/10 → 0/3 |
| V4 p100 | 0.215 → 0.058 | 0.782 → 1.000 | 0.065 → 0.522 | 0/6 → 0/3 |
| **V4 p104** | **0.528 → 0.546** | 0.909 → 0.800 | 0.000 → 0.000 | 1/6 → 1/6 |
| V4 p116 | 0.191 → 0.033 | 0.709 → 1.000 | 0.154 → 0.636 | 0/2 → 0/2 |
| V4 p118 | 0.343 → 0.132 | 0.782 → 0.964 | **0.103 → 0.065** | 0/5 → **0/12** |
| V4 p124 | 0.0582 → 0.0584 | 1.000 → 1.000 | 0.462 → 0.609 | 0/5 → 0/2 |
| V4 p128 | 0.208 → 0.062 | 0.782 → 1.000 | 0.138 → 0.727 | 0/3 → 0/1 |
| V4 p130 | 0.259 → 0.040 | 0.818 → 1.000 | 0.133 → 0.500 | 0/5 → 0/5 |
| V4 p142 | 0.325 → 0.050 | 0.800 → 1.000 | 0.051 → 0.143 | 0/10 → 0/8 |
| V10 p24 | 0.082 → 0.065 | 0.965 → 0.974 | 0.578 → 0.757 | 0/4 → 0/2 |
| V10 p40 | 0.123 → 0.098 | 0.905 → 0.937 | 0.370 → 0.435 | 0/7 → 0/6 |
| V10 p52 | 0.083 → 0.063 | 0.970 → 0.987 | 0.511 → 0.650 | 0/6 → 0/4 |
| V10 p56 | 0.141 → 0.056 | 0.976 → 0.976 | 0.542 → 0.769 | 0/4 → 0/3 |
| V10 p78 | 0.087 → 0.069 | 0.974 → 0.987 | 0.449 → 0.585 | 0/8 → 0/6 |

- As 8 páginas da §1.27 reproduzem exatamente os valores previstos; 12 páginas novas (V4 p82, p86, p88, p95, p96, p104, p128, p130, p142; V10 p52, p56, p78) entram no alcance na rodada completa.
- **Nenhuma perda nem duplicação real de palavras.** CER aumenta só na V4 p104 (+0.018) e, na 4ª casa, na p124.

**Fusões e quebras** (script da §1.22): V4 417/278 → **415/249**; V9 7/80 → 7/80; V10 43/220 → **46/210**. As 3 fusões novas do V10: p31 e p62 (texto recuperado entra no bloco de um fragmento, páginas inclinadas) e p32 (artefato do script: o título deixa de contar como parágrafo; o F1 oficial da p32 sobe 0.476 → 0.541).

**Artefatos do avaliador, sem mudança de texto**: V4 p184 `block_order_pairwise_accuracy` 1.0 → 0.0 (1 par de blocos; só o `######` foi acrescentado); V4 p119/p120 `paragraph_boundary_f1` 1.0 → 0.941 (o título deixa de ser bloco de parágrafo).

#### Correções anteriores — preservação na RUN 012

| Problema | Páginas | Situação |
|---|---|---|
| T3-P20 / T3-P38 | V4 coluna única (p83, p85, p99, p125, p129, p137–p143), V4 p140, V9 p17, V10 p25/p37/p68 | fusões e quebras sem mudança nessas páginas (p83 e outras só ganham o `#` do título); p140, V9 p17, V10 p25/p68 idênticas; totais V4 415/249 ≤ 417/278 ✅ |
| T3-P22 | V4 p20/p25/p26/p28; V10 p9/p54/p59; V9 p90 | idênticas ✅ |
| T3-P28 / T3-P39 | V8 p4/p58 (`€ 98,40`); V9 p58/p66; V6 p11; V8 p49/p67 | idênticas ✅ |
| T3-P32 | V9 p20/p55/p71 | idênticas ✅ |
| T3-P34 | V9 p58/p44/p49 | idênticas ✅ |
| T3-P35 | V10 raster niveladas (20 págs., L03–L06 e controles) e p32 | CER médio 0.0854 → **0.0823**; p32 0.311 → 0.311 (só marcadores) ✅. Inclinadas: CER médio 0.2960 → 0.2968 (p5/p31 com texto recuperado), recall mínimo 0.909 → 0.909 |
| T3-P36 | V4 p167/p168 (aviso) | p167 idêntica; p168 só ganha o `######` no separador danificado; tabela e aviso iguais ✅ |
| V5, V7 e nativas | documentos e páginas nativas inteiras | idênticos byte a byte ✅ (`layout/heading.py` compartilhado sem efeito nativo) |

#### Avaliação de T3-P06

- **Funciona na extração integral** para regiões OCR niveladas: V4 0 → 42 acertos nas páginas só-OCR, V10 0 → 99 em só-OCR e mistas, V9 +16; recall acima dos critérios nos três corpora; nenhum título nativo afetado; decisões **idênticas às da candidata** validada.
- **Não atende** a três critérios: precisão do V4 (0.929 < 0.95), categorias novas de falso título e CER da V4 p104. A hierarquia piora fortemente no V10 (0.977 → 0.519), um efeito da regra de níveis documental que os subconjuntos não mostraram.
- **A melhoria de recall justifica os falsos positivos?** Sim, na interpretação técnica: 154 títulos verdadeiros novos (148 fortes + 6 parciais) contra 28 falsos reais (5.5 : 1). Os falsos não mudam texto (o conteúdo já saía como parágrafo). Mesmo assim, o resultado não basta para RESOLVIDO: a precisão do V4 e as categorias novas eram critérios explícitos, e quatro páginas têm regressão estrutural nova (p95, p96, p145, p149) que a validação pontual não cobriu.
- **Status: PARCIAL — CONFIRMADO NA RUN 012** (tentativa 3 de 5). Recall e caminho nativo confirmados. Pendentes: hierarquia OCR, falsos títulos (separadores danificados, rodapés, linhas de tabela) e as quatro regressões novas. **Não reverter**: o ganho é amplo e não houve perda de conteúdo ou de dados.

### §1.29 Notas da rubrica §1.16 (RUN 012)

**Resultados** (rubrica, pesos, limiares, penalidades, gabaritos e avaliador **não alterados**; mesmo script da §1.23, que reproduz a 011 exatamente):

| # | Corpus | Texto | Cobertura | Títulos | Parágrafos | Listas | Tab. estrutura | Tab. células | Dados | Ordem | Penalidade | **Nota 012** | Nota 011 | Δ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | V7 | 99.4 | 99.5 | 98.8 | 97.6 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 0 | **99.4** | 99.4 | 0.0 |
| 2 | V8 | 98.2 | 98.6 | 99.4 | 96.2 | 100.0 | 100.0 | 99.9 | 98.5 | 100.0 | 0 | **98.8** | 98.8 | 0.0 |
| 3 | V6 | 96.7 | 96.5 | 99.0 | 96.3 | 100.0 | 100.0 | 96.3 | 91.6 | 100.0 | 0 | **96.7** | 96.7 | 0.0 |
| 4 | V3 | 94.9 | 95.2 | 68.0 | 92.0 | 97.3 | 99.9 | 99.1 | 98.0 | 98.3 | −0.5 | **93.1** | 93.1 | 0.0 |
| 5 | V5 | 67.3 | 83.5 | 83.8 | 83.8 | 92.1 | 100.0 | 100.0 | 98.5 | 87.1 | 0 | **86.5** | 86.5 | 0.0 |
| 6 | V9 | 77.8 | 96.8 | **54.5** (50.7) | **65.8** (64.6) | 84.7 | 79.9 | 63.9 | 86.3 | 58.4 | −1.0 | **75.6** | 75.1 | **+0.5** |
| 7 | V10 | **56.5** (55.4) | **95.3** (94.9) | **62.9** (48.9) | **56.3** (52.5) | 72.7 | 96.5 | 91.2 | 86.9 | **51.3** (55.2) | 0 | **75.5** | 73.6 | **+1.9** |
| 8 | V4 | **77.3** (73.0) | 68.1 | **52.3** (37.5) | **54.0** (48.7) | **79.1** (77.4) | 86.8 | 70.4 | 69.2 | 100.0 | −4.5 | **66.9** | 63.9 | **+3.0** |

- Penalidades iguais às da 011: V3 −0.5 (p86); V9 −1.0 (`partial`); V4 −4.5 (9 páginas ilegíveis: p103, 104, 153, 163–166, 168, 172).
- **V10 Ordem 55.2 → 51.3** (−0.2 na nota): artefato da regra da rubrica (página com CER > 0.15 **e** recall ≥ 0.95 conta como erro de ordem). A V10 p62 passou de recall 0.924 para 0.996 pela recuperação de conteúdo (T3-P42) e entrou na contagem com o **mesmo** CER (0.251). Não houve piora real de ordem no V10.
- **Notas por rodada** (continuação da §1.23): V4 63.1 (009) → 63.8 → 63.9 → **66.9** · V9 74.9 → 75.1 → 75.1 → **75.6** · V10 73.6 → 73.6 → 73.6 → **75.5** · V3, V5–V8 iguais.

**Interpretação qualitativa** (não altera as notas):
- **Impacto de T3-P06 na qualidade estrutural**: é o maior ganho de uma rodada desde a 008 no V4 (+3.0). No V4 ele vem de Títulos (+14.8), Texto (+4.4, pela ordem das páginas de duas colunas) e Parágrafos (+5.3). No V10 vem de Títulos (+14.0) e Parágrafos (+3.8).
- A hierarquia limita o ganho: com `heading_level_match_rate` no nível da 011, Títulos do V10 seria ≈ 72 (≈ +0.9 na nota) e o do V9 ≈ +3.
- **Erros graves de integridade** (sem mudança): V4 p163–p166/p172 e V3 p86 ilegíveis; V9 p36/p92 (T3-P32); V9 p66 (T3-P34); V9 p62/p72 (T3-P37); V4 p168 coluna não lida. Melhorou: V10 p5/p11/p31/p62 com conteúdo recuperado (T3-P42). Nenhum novo.
- **Páginas que melhoraram**: 88 páginas melhoram alguma métrica oficial sem piorar nenhuma. As maiores: V4 p88 (CER 0.340 → 0.074), p142 (0.325 → 0.050), p130 (0.259 → 0.040), p98, p100, p86, p82, p116, p128; V10 p56 (0.141 → 0.056); V10 p62 (recall 0.924 → 0.996).
- **Páginas que pioraram** (12 com alguma métrica pior): V4 p104 (CER e ordem), p95 e p96 (ordem de blocos), p118 (parágrafos), p157 (parágrafos), p124 (CER, 4ª casa), p184 e p119/p120 (artefatos do avaliador); V10 p5 e p31 (CER +0.003 e +0.006 com mais conteúdo) e p62 (fronteiras de parágrafo, com mais conteúdo).

### §1.30 Priorização pós-012 (reavaliação dos problemas abertos)

Critérios, em ordem (§1.24): (1) perda de conteúdo; (2) corrupção de dados críticos; (3) severidade estrutural; (4) número de páginas; (5) impacto na nota; (6) chance de resolução; (7) risco de regressão; (8) custo de validação. Nenhuma perda ou corrupção nova na 012; uma perda antiga foi corrigida (T3-P42).

| Problema | Evidência na 012 | Componente / autorização | Tentativas | Risco | Benefício | Validação sem OCR | Avaliação |
|---|---|---|---|---|---|---|---|
| **T3-P35** ordem nas inclinadas (V10 L01/L02) | sem mudança: CER médio 0.297 contra 0.082 das raster niveladas; 12 páginas; 70 títulos do gabarito ausentes nelas (o caminho OCR de T3-P06 não se aplica a texto inclinado) | `ocr/backends/tesseract.py` e orquestração em `api.py` (**compartilhado**: exige autorização); `text/reading_order.py` e `layout/heading.py` intactos | 2 / 5 | alto (T3-P31: 10 falhas; T3-P35 t1 perdeu conteúdo) | **o maior**: V10 Texto, Ordem e Títulos; estimativa ≈ +4–5 na nota do V10 (não validada) | **sim no Nível 1** (H1, §1.26): 1 captura de V10 p13/p18/p52 e depois tudo offline | **recomendado (1º)** |
| **T3-P06 resíduos** (t4) | hierarquia OCR documental (V10 0.977 → 0.519); 28 falsos títulos (6 rodapés, 7 separadores danificados, 6 linhas de tabela, 6 linhas de corpo, 1 cabeçalho, 1 ilegível, 1 fragmento); regressões V4 p95/p96 (título unido ao controle), p145/p149 (linhas de tabela unidas), p104 | `layout/heading.py` (compartilhado; o caminho OCR é separado do nativo) e `layout/ocr_aware.py` | 3 / 5 | baixo a médio: os falsos são de 7 tipos; não perseguir todos | Títulos V10 ≈ +9 e V9 ≈ +3 só pela hierarquia; V4 precisão ≥ 0.95 exige eliminar ≥ 3 dos 8 falsos oficiais | parcial: a hierarquia é documental e precisa de captura do documento inteiro (3 extrações) ou de um desenho por página; os falsos são verificáveis por palavras idênticas | **recomendado (2º), depois de T3-P35** |
| T3-P41 parágrafos nativos densos (V4) | sem mudança (227 fusões em p9–p40) | segmentação nativa — exige autorização | 0 / 5 | médio (V5–V8) | V4 Parágrafos ≈ +15, nota ≈ +1.5 (extrapolação da estimativa da §1.22; não oficial, não validada) | sim (nativo: extração rápida, sem OCR) | 3º; depende de autorização |
| T3-P37 tabelas nativas lado a lado (V9) | sem mudança (p62, p72, p14) | detector de tabelas nativo — exige autorização | 0 / 5 | médio-alto (V5–V8) | associação valor ↔ linha em 3 páginas | sim (nativo) | 4º |
| T3-P34 tabelas raster restantes (V9) | sem mudança (p66 recall 0.803, `currency` 0.632; p78; p25; p8) | detecção visual e releitura (compartilhados) | **4 / 5** | médio | p66 é dado crítico | não (exige OCR de releitura) | só com hipótese única para a p66 |
| T3-P33 `Invalid BBox` (V9 p36) | sem mudança | `text_tracks._build_table` (nativo) — exige autorização | 0 / 5 | baixo | V9 `valid`, +1.0 na nota | sim | acompanhar qualquer ciclo nativo autorizado |
| T3-P42 conteúdo perdido na fusão de títulos OCR | **corrigido e confirmado na 012** | — | — | — | — | — | encerrar |

**Os dois problemas recomendados** (a escolha é do responsável):

1. **T3-P35 — ordem nas páginas inclinadas do V10, pelo referencial retificado (§1.26).**
   - **Por quê**: maior massa de erro restante do corpus prioritário (CER médio 0.297 nas 12 inclinadas, 3.6× o das raster niveladas) e o único bloqueio do caminho de títulos de T3-P06 nessas páginas (70 títulos do V10). A RUN 012 reforça a proposta: as inclinadas foram mantidas fora de T3-P06 pelo critério de texto nivelado e continuam iguais. Com o layout no referencial endireitado, a inclinação medida dentro da cadeia passa a ≈ 0 e o critério OCR de títulos de T3-P06 passaria a valer nelas **sem código novo de títulos** (a verificar).
   - **Avaliação da proposta**: continua válida e é a única que ataca a causa medida (caixa alinhada aos eixos inflada em 10.5–12.5 pt) sem mexer na ordem compartilhada. Ponto novo da 012: a recuperação de conteúdo de T3-P42 mudou o texto de 3 inclinadas (p5/p31/p62), então a base de comparação do ciclo passa a ser a **012**.
   - **Componentes**: `ocr/backends/tesseract.py` (quadrilátero girado em `polygon_px` ou inversão pela fórmula) e a fronteira de orquestração em `api.py` (**exige autorização**). Não tocar `text/reading_order.py` nem `layout/heading.py`.
   - **Riscos**: coordenadas expostas no JSON; deduplicação (perda da V10 p25 na T3-P35 t1); tabelas inclinadas com transformação própria (T3-P34); páginas mistas inclinadas (V9 p23/p44) fora do escopo.
   - **Validação sem repetir OCR**: H1 no Nível 1 com uma única captura (V10 p13, p18 e o controle p52); se refutada, parar sem gastar tentativa de extração. Custo total estimado de 17–21 processos sequenciais só se H1 passar.
2. **T3-P06 — resíduos (tentativa 4 de 5), depois de T3-P35.**
   - **Por quê**: fechar o critério de precisão do V4, a hierarquia e as quatro regressões novas desta correção. É barato e de risco baixo, mas o benefício na nota é menor (≈ +1 no V10).
   - **Por que depois de T3-P35**: os níveis OCR são agrupados no documento inteiro. Se T3-P35 liberar os títulos das inclinadas, a população de títulos muda e a hierarquia teria de ser refeita.
   - **Escopo sugerido**: (a) níveis OCR relativos à página (o título principal da página e os demais abaixo dele) ou agrupamento com número de grupos limitado por vãos significativos, para tirar a dependência do documento inteiro; (b) a causa da união título + "Controle de página" (V4 p95/p96) e das linhas de tabela unidas (p145/p149), com captura; (c) só depois, e com proposta separada, as categorias de falso título de causa única: faixa inferior (rodapés, já proposta) e linha de tabela com prefixo numerado e data.
   - **Componentes**: `layout/heading.py` (só o caminho OCR) e `layout/ocr_aware.py`; exige nova autorização para o arquivo compartilhado.
   - **Validação sem OCR**: os falsos títulos e as regressões são verificáveis com entradas capturadas e palavras idênticas. A hierarquia documental exige capturar a entrada de `assign_heading_levels` dos documentos inteiros (3 extrações, ≈ 1 h, uma vez), ou um desenho por página, verificável em subconjuntos.

**Ficam fora dos dois primeiros**: T3-P41 (maior ganho estimado fora do OCR, mas nativo e sem autorização); T3-P37 e T3-P33 (nativos, poucas páginas); T3-P34 (última tentativa, só com hipótese única para a p66).

### §1.31 Meta interna de 85/100 e diagnóstico quantitativo do V4 (pós-012, 2026-11-19; sem extração)

**Meta definida pelo responsável**: nota geral **≥ 85/100 em cada um dos 8 corpora**, com a rubrica §1.16 **sem nenhuma alteração** (pesos, limiares, métricas, gabaritos e corpora intocados). A meta é critério interno de qualidade e **não substitui** os critérios de integridade, que continuam separados e obrigatórios (§5): nenhuma perda de conteúdo, nenhum dado crítico corrompido, nenhuma falha grave silenciosa. Situação na 012: V3 93.1, **V4 66.9**, V5 86.5, V6 96.7, V7 99.4, V8 98.8, **V9 75.6**, **V10 75.5**. O V4 é a prioridade absoluta (−18.1); V9 e V10 ficam como controles de regressão neste ciclo; T3-P35 não entra.

**Método** (só leitura de `output/tesseract/012`, `corpus/` e código; nenhuma extração, nenhum OCR): as métricas documentais do avaliador são somas das contagens por página (verificado: CER, WER, títulos, parágrafos, listas, tabelas, células, `currency`, `dates`, `percent`; recall textual, `numeric` e `table_content_f1` têm um pequeno ajuste documental, mantido como constante). Um simulador recalcula a rubrica a partir dessas contagens: reproduz 66.85 para o V4 (oficial 66.86) e os demais corpora a ±0.2. **Todo ganho abaixo é diferença cenário − base no mesmo simulador**, e os cenários de várias etapas são aplicados **cumulativamente** sobre as mesmas contagens, sem somar estimativas independentes. Scripts no scratchpad (`v4/sim.py`, `gstats.py`, `scen.py`, `plan.py`, `tokcls.py`).

#### Fase 1 — decomposição da nota 66.86

Perda de cada categoria = peso × (100 − nota)/100. Soma das perdas 28.65 → base 71.35; penalidade −4.5 (9 páginas ilegíveis × 0.5).

| Categoria (peso) | Nota | Fórmula com as métricas oficiais | Perda | Páginas que mais pesam (ganho se a página fosse perfeita só nessa categoria) | Causa técnica principal | Correção possível? |
|---|---|---|---|---|---|---|
| Texto (20) | 77.3 | ½·BAIXO(CER 0.0376; 0.30) = 87.5 e ½·BAIXO(WER 0.1312; 0.40) = 67.2 | 4.53 | p104 +0.31, p103 +0.20, p106 +0.12, p95, p118, p96, p117 | reconhecimento: segmentos adversariais (piso) + páginas degradadas | piso: não com o Tesseract atual; degradação: em parte, com pré-processamento (exige OCR) |
| Cobertura (15) | 68.1 | 0.6·ALTO(recall 0.8941; 0.70) = 64.7 e 0.4·ALTO(precisão 0.9196; 0.70) = 73.2 | 4.79 | p103, p104, p163, p164, p172, p153, p168, p166 (com a penalidade) | idem; 5 tabelas com conteúdo ilegível | idem |
| Títulos (10) | 52.3 | 0.6·F1 0.7003 + 0.2·nível **0.000** + 0.2·BAIXO(CER do título 0.486; 1.0) | 4.77 | nível: 104 títulos (2.0 pontos fixos); p118, p26/p27 (colunas nativas), p104, p69/p70 | convenção de nível do gabarito (§1.16); títulos de coluna ausentes (nativos e OCR); 8 falsos | detecção: sim; nível: decisão (abaixo) |
| Parágrafos (10) | 54.0 | ½·F1 detecção 0.4889 + ½·F1 fronteira 0.5913 | 4.60 | nativos densos (T3-P41: B1, B2, G1, F1) e OCR (p111/p112 girados, p104, p106, p95/p96) | limiar de quebra nativa (T3-P41); segmentação OCR | **sim, sem OCR** (nativo) |
| Listas (5) | 79.1 | 0.4·grupos 0.5833 + 0.4·itens 0.8936 + 0.2·hierarquia 1.0 | 1.05 | p95, p103, p87, p118, p96 (10 grupos falsos) | listas falsas em OCR ruidoso | sim |
| Tab. estrutura (10) | 86.8 | média de detecção 0.9081, linhas 0.9269, colunas 0.7525, células 0.8857 | 1.32 | p163, p164 (casamento por conteúdo falha), p161, p165/p166, p149, p172, p156 | tabelas não detectadas; sombra lateral; casamento por conteúdo | parcial |
| Tab. células (10) | 70.4 | média de exatas 0.638, alinhamento 0.7408, BAIXO(CER 0.3265; 1.0) = 67.4, conteúdo 0.7621 | 2.96 | p163/p164 (+0.46 cada), p184, p180, p177, p161, p178, p149 | reconhecimento de IDs e símbolos; sombra (1ª coluna perdida) | parcial |
| Dados (15) | 69.2 | ALTO(x; 0.80): `numeric` 0.8971 → 48.6 (×0.4), `currency` 0.9676 → 83.8, `dates` 0.9725 → 86.3, `percent` 0.9787 → 89.4; ÷ 0.9 (sem identificadores) | 4.63 | p103, p164, p104, p99, p93, p139, p106, p138 | **1 642 números falsos** (precisão `numeric` 0.834), quase todos de segmentos adversariais lidos errado | piso: não; parte talvez pós-OCR (hipótese H-esp) |
| Ordem (5) | 100.0 | pares 1.0000; nenhuma página com CER > 0.15 e recall ≥ 0.95 | 0.00 | — | — | — |

**Limites estruturais da rubrica para o V4** (simulador, cumulativo):
- **S1b — estrutura perfeita em tudo** (títulos com o nível do gabarito, parágrafos, listas, estrutura de tabelas, ordem), **reconhecimento como hoje**: **78.6**. Texto, Cobertura, Dados e Células (60% do peso) ficam como estão, e a penalidade continua −4.5.
- **S2b — OCR no piso das digitalizações limpas** (cada página de OCR de texto com as taxas das páginas limpas p81/p83/p84/p115, ou p82/p116 em duas colunas; cada tabela OCR com as taxas da melhor página do mesmo tipo de tabela), estrutura como hoje: **73.7**.
- **S3 = S2b + estrutura perfeita**: **85.4**. **S4 = S3 + células das tabelas OCR perfeitas**: 88.3.
- Conclusão: **85 só aparece quando a estrutura é perfeita em todas as categorias e, ao mesmo tempo, todo o OCR sobe ao piso limpo**. Nenhuma das duas condições sozinha basta.

#### Fase 2 — distribuição por grupo de páginas

Agregados a partir das contagens (CER = Σ edições / Σ caracteres; idem WER, recall e precisão). "Teto" = ganho na nota se o grupo fosse perfeito em tudo (não somável entre grupos).

| Grupo (págs.) | Páginas | CER | WER | Recall / Precisão | Títulos tp/fp/fn | Parágrafos tp/fp/fn (fronteira F1) | Células exatas | `numeric` tp/fp/fn | % das edições | % dos tokens ausentes | % dos números falsos | Teto |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A protocolo, nativo (10) | 1–8, 223–224 | 0.000 | 0.000 | 1.000 / 1.000 | 7/0/1 | 13/8/11 (0.56) | 42/42 | 36/0/0 | 0.0% | 0.0% | 0.0% | +0.10 |
| B1 nativo denso multicoluna (12) | 17–28 | 0.001 | 0.002 | 1.000 / 1.000 | 6/0/**22** | 14/45/**114** (0.41) | — | 824/0/0 | 0.5% | 0.0% | 0.0% | +1.37 |
| B2 nativo denso coluna única (20) | 9–16, 29–40 | 0.000 | 0.000 | 1.000 / 1.000 | 20/0/0 | 24/24/**164** (0.25) | — | 1274/0/0 | 0.0% | 0.0% | 0.0% | +0.99 |
| C tabela nativa (40) | 41–80 | 0.000 | 0.000 | 1.000 / 1.000 | 0/0/4 | 40/6/4 (0.40) | **1070/1070** | 766/0/0 | 0.0% | 0.0% | 0.0% | +0.17 |
| D1 OCR texto, coluna única (32) | 81, 83–85, 87, 89–91, 93, 95–97, 99, 101–103, 115, 117, 119–121, 123, 125–127, 129, 137–139, 141, 143, 144 | 0.049 | 0.202 | 0.830 / 0.867 | 28/0/4 | 237/143/83 (0.81) | — | 1854/**890**/162 | **37.4%** | **45.2%** | **48.6%** | +9.06 |
| D2 OCR texto, duas colunas (16) | 82, 86, 88, 92, 94, 98, 100, 104, 116, 118, 122, 124, 128, 130, 140, 142 | 0.083 | 0.246 | 0.793 / 0.857 | 8/2/**24** | 87/156/57 (0.68) | — | 836/373/60 | **28.1%** | 24.4% | 20.3% | +5.28 |
| D3 OCR texto girado 90/180/270 (6) | 109–114 | 0.047 | 0.186 | 0.837 / 0.882 | 2/0/6 | 9/**118**/49 (0.29) | — | 357/153/7 | 6.5% | 7.8% | 8.3% | +1.73 |
| D4 OCR texto inclinado/perspectiva (10) | 105–108, 131–136 | 0.055 | 0.190 | 0.850 / 0.894 | 0/0/13 | 14/42/83 (0.30) | — | 587/233/22 | 12.6% | 12.0% | 12.7% | +2.66 |
| E1 OCR de tabela (33) | 145–184, salvo E2/E3 | 0.206 | 0.495 | 0.692 / 0.620 | 1/5/3 | 33/16/3 (0.32) | 333/940 | 452/48/69 | 7.2% | 3.9% | 2.6% | +6.46 |
| E2 OCR de tabela inclinada (5) | 148, 156, 164, 172, 180 | 0.286 | 0.740 | 0.483 / 0.404 | 0/0/0 | 4/7/2 | 28/229 | 80/12/93 | 2.2% | 1.3% | 0.7% | +2.59 |
| E3 OCR de tabela paisagem (2) | 183, 184 | 0.268 | 0.330 | 0.786 / 0.746 | 0/1/0 | 2/0/0 | 46/104 | 58/10/14 | 0.6% | 0.2% | 0.5% | +0.30 |
| F1 adversarial nativo (24) | 191–196, 199–216 | 0.009 | 0.009 | 1.000 / 0.991 | 20/0/2 | 58/16/54 (0.68) | — | 458/6/0 | 1.5% | 0.0% | 0.3% | +0.46 |
| F2 adversarial com OCR/híbrido (8) | 185–190, 197, 198 | 0.028 | 0.126 | 0.897 / 0.926 | 6/0/2 | 31/10/1 (0.84) | 51/58 | 198/50/8 | 1.8% | 2.4% | 2.7% | +0.44 |
| G1 controle nativo (3) | 217, 219, 221 | 0.000 | 0.000 | 1.000 / 1.000 | 3/0/0 | 3/3/21 (0.25) | — | 150/0/0 | 0.0% | 0.0% | 0.0% | +0.13 |
| G2 controle escaneado (3) | 218, 220, 222 | 0.030 | 0.160 | 0.862 / 0.906 | 3/0/0 | 24/0/0 (1.00) | — | 140/58/10 | 1.7% | 2.7% | 3.2% | +0.37 |

- **Páginas com valores críticos**: todas as páginas D têm R$, percentuais e datas em cada parágrafo, e `currency`/`dates`/`percent` estão bons (0.968/0.973/0.979). A perda de Dados é a **precisão de `numeric`**: 1 652 dos números falsos estão nos grupos D.
- **Falhas de títulos e parágrafos**: concentradas em B1 (títulos de coluna nativos ausentes, parágrafos fundidos), B2 (parágrafos fundidos), D2 (títulos de coluna OCR ausentes) e D3/D4 (segmentação).
- **O que responde pela maior parte da perda**: o OCR de texto (D1–D4, 64 páginas: 85% das edições, 89% dos tokens ausentes e 90% dos números falsos) e as tabelas OCR (E, 40 páginas: Células, Estrutura e 7 das 9 penalidades). O texto nativo perde só estrutura (≈ 2.5 pontos de teto).

#### Fase 3 — investigação dos problemas prioritários

**T3-P21, OCR degradado: o que é do Tesseract e o que vem depois.**
- **Piso das páginas limpas**: mesmo na digitalização limpa de 300 dpi (p81), o recall é 0.880, o WER 0.144 e há 19 números falsos. As páginas limpas (p81, p83, p84, p115) somam CER 0.028, WER 0.150, recall 0.873 e 0.32 número falso por número do gabarito. O piso é sistemático, não degradação.
- **Onde estão os tokens perdidos** (alinhamento de palavras gabarito × saída nas 64 páginas D e nos 3 controles escaneados; 54 193 tokens do gabarito): **82% dos 10 017 tokens ausentes estão nos segmentos adversariais do corpus**: IDs de controle `V4-P081-B01-O0I1l-rnm-08101` (100% perdidos), caminhos `C:\QA\lote_081\arquivo_01.pdf` (100%), sinais literais `[A|B], x < 7, y > 2, A & B, 'texto', "aspas"` (56.6%) e a lista de confundíveis `O, 0, I, l, 1, rn e m` (54.5%). Palavras comuns perdem só **4.3%**, concentradas nas páginas degradadas.
- **Números falsos**: "0, I," lido como `0,1,` vira o decimal 0.1 (**405** vezes); `1,1` (126); os dígitos de `arquivo_01.pdf`, lido como `081larquivo 01.pdf` (≈ 300); dígitos de IDs. Ausentes: o `0` da lista de confundíveis (177).
- **Exemplos da p81 (limpa)**: `O0I1l` → `00I1l`; `rn` → `rm`; `y > 2, A & B` → `y>2,A8&B`; `C:\QA\lote_081\arquivo_01.pdf` → `CiQAVote 081larquivo 01.pdf`. São erros de **reconhecimento** de glifos ambíguos por desenho do corpus (estresse de OCR).
- **Configuração atual**: Tesseract 5, `por`, `--oem 1`, `--psm 3`, perfil `default` (sem CLAHE, sem opções), renderização a **288 dpi** (escala 4.0). Os rasters do corpus têm 72–300 dpi (manifesto `raster_profile`). Aumentar o DPI não acrescenta informação aos rasters de origem. O perfil `tesseract-degraded-scan-v1` (CLAHE + opções de ruído) existe no código e é ativado só por variável de ambiente, mas **nunca foi medido** na Série 3.
- **Perdas específicas de degradação, acima do piso**: blur forte (p103/p104: recall 0.46/0.34, penalizadas), sombra lateral (p117/p118: as palavras da borda esquerda de cada linha somem, 422/444 caracteres apagados), ruído forte (p95/p96, recall 0.75), 72 dpi (p87/p88), JPEG 16 (p99/p100), combinações escuras (p139/p140).
- **Hipótese H-esp (pós-OCR, não verificada)**: `ocr/reconstruct.py::_needs_space` não insere espaço entre tokens com caixas sobrepostas quando a fronteira não é alfanumérica. Isso **pode** produzir `0,1,` e `y>2,` (≈ 531 números falsos). A alternativa é o próprio Tesseract emitir uma palavra só. Teste decisivo: a TSV bruta de **uma** página limpa (p81, 1 chamada do Tesseract). Se confirmada, o ganho estimado é ≈ +0.9 (`numeric` F1 0.897 → 0.924 → Dados +5.9 × 0.15).
- **Atribuição**: (1) piso adversarial = limite de reconhecimento do Tesseract na configuração atual, a maior parcela de Texto, Cobertura, Dados e Células; (2) degradação acima do piso = qualidade da imagem, em parte tratável por pré-processamento, que **exige reexecutar o Tesseract**; (3) pós-OCR = espaçamento (H-esp, a verificar), segmentação, listas falsas e títulos.

**T3-P41, parágrafos nativos densos.**
- **Causa raiz (geometria do PDF, lida com `pypdfium2`, sem extração)**: na V4 p9, o vão entre linhas do mesmo parágrafo é 1.1–1.8 pt e o vão entre parágrafos é **4.5 pt**, com a última linha de cada parágrafo curta (x1 ≈ 372 contra ≈ 555). `assemble/content.py::_native_paragraphs` quebra só quando `vão > max(0.75 × altura da linha, min(1.5 × vão típico, 1.2 × altura))`. Com linhas de ≈ 7.7 pt, o piso de 0.75 × altura (≈ 5.8 pt) domina e o vão de 4.5 pt (≈ 3.4 × o vão típico) nunca quebra. A docstring assume que as linhas de um parágrafo ficam "meia linha" afastadas; no texto denso elas ficam a ≈ 0.15 linha.
- **Etapa**: segmentação de parágrafos nativa (montagem); não é detecção de blocos, ordem de leitura nem Markdown. A altura exata de `TextLine.bbox` precisa ser confirmada no Nível 1 (captura nativa, sem OCR).
- **Extensão** (parágrafos tp/fp/fn): B2 24/24/164, B1 14/45/114, G1 3/3/21, F1 58/16/54, A 13/8/11. No B1 há também a ordem por coluna (T3-P22).
- **Ganho**: teto (parágrafos nativos perfeitos) ≈ +2.2; **estimativa realista +1.6** (85% da lacuna em A/B2/G1, 70% em B1, 50% em F1: Parágrafos 54.0 → ≈ 70).
- **Componente**: nativo e compartilhado, **exige autorização**; risco em V3 e V5–V8.

**T3-P06, regressões da RUN 012 (V4).**

| Página | Tipo | Causa provável | Corrigível sem perder o recall da t3? |
|---|---|---|---|
| p95, p96 | segmentação: título verdadeiro unido à linha "Controle de página" | regra (B) "linha embutida" com caixas ruidosas | **sim**: (B) não deve valer quando a linha anterior termina curta (fecha parágrafo); não afeta títulos verdadeiros. Exige captura (1 processo) |
| p100 | segmentação: "R$" \| "14.057,00" em blocos diferentes | segmentação OCR após a reordenação das colunas | provável, causa não isolada |
| p104 | falso título (linha ilegível) + ordem | título OCR sem filtro de qualidade de texto | arriscado; página já penalizada |
| p118 | 2 falsos títulos + segmentação | separador danificado; linha de corpo com razão exatamente 1.28 | parcial; não ajustar limiar a uma página |
| p145, p149 | associação de tabela: linhas de tabela não detectada unidas num parágrafo | tabela raster **não detectada** + (B)/(C) | sim, pela detecção da tabela (E5) |
| p157 | falso título (linha de tabela) | tabela com cabeçalho mesclado mal estruturada | pequeno; efeito colateral positivo (lista falsa desfeita) |

Ganho na nota de corrigir todas: ≈ +0.1–0.2 (Títulos: 8 falsos → 0 dá F1 0.700 → 0.720). O valor é de **qualidade real** (blocos corretos), não de nota. Não aplicar t4 automaticamente.

**Tabelas OCR e dados críticos** (40 páginas E; 1 273 células do gabarito, 407 exatas = 32%):

| Causa | Páginas | Células afetadas | Origem | Causa comum? |
|---|---|---|---|---|
| Sombra lateral: 1ª coluna não lida, colunas deslocadas | p152, p160, p168, p176, p184 | 128 (0 exatas, 1 alinhada) | imagem → reconhecimento | **sim**: mesma família; também p117/p118 no texto |
| Tabela não detectada (texto sai como parágrafo) | p145, p149, p156, p172 | 77 ausentes | detecção visual | sim, 4 páginas de degradações diferentes |
| Conteúdo ilegível impede o casamento por conteúdo (estrutura certa, todas as células erradas) | p163, p164 (IDs `T10A-01-01` → `TIDA`/`TIOA`/`T104`), p165, p166 (símbolos Markdown), p161 | 321 | reconhecimento (confundíveis por desenho) | não pós-OCR |
| Texto de célula lido com erro em tabela correta | demais | ≈ 450 inexatas | reconhecimento | não |
| Valores inventados | `numeric` fp 70 nas tabelas (p178: 27) | — | reconhecimento | não |

- **Estimativas** (simulador, páginas copiadas com as contagens da irmã do mesmo tipo de tabela sem a degradação): sombra lateral, 7 páginas → **+1.28**; tabelas não detectadas, 4 páginas → **+0.93**.

#### Fase 4 — plano por etapas (simulação cumulativa, na ordem)

| Etapa | Problema | Componentes | Isolado | Cumulativo | Justificativa do número | Depende de | Risco | Custo | Reexecutar Tesseract? |
|---|---|---|---|---|---|---|---|---|---|
| E1 | T3-P41 parágrafos nativos | `assemble/content.py::_native_paragraphs` (nativo, compartilhado) | +1.59 | 68.44 | 85% dos fn/fp de parágrafos em A/B2/G1, 70% em B1, 50% em F1 | — | médio (V3, V5–V8) | baixo: páginas nativas não usam OCR | **não** |
| E2 | nível de título relativo ao título de página suprimido | `layout/heading.py` | +2.00 | 70.44 | `heading_level_match_rate` 0 → 1 (0.2 × 100 em Títulos) | **decisão do responsável** | alto de "ajuste ao gabarito" | baixo | não |
| E3 | sombra lateral | pré-processamento Tesseract (perfil CLAHE já existente, ou normalização de iluminação) | +1.28 | 71.72 | 7 páginas com as taxas da irmã sem sombra | experimento de OCR | médio (afeta todas as páginas OCR se for global) | médio | **sim** |
| E4 | segmentação OCR de parágrafos | `ocr/reconstruct.py::segment_ocr_paragraphs`, layout OCR | +1.14 | 72.83 | 50% dos fn/fp de parágrafos nos grupos D | — | médio (T3-P20/P38) | baixo (captura de linhas) | não, após captura |
| E5 | tabelas OCR não detectadas | detecção visual de tabelas (compartilhada) | +0.93 | 73.76 | 4 páginas com as taxas da irmã | — | médio-alto (V5–V9) | médio | sim |
| E6 | títulos de coluna (nativos e OCR) e falsos | `layout/heading.py`, layout OCR | +1.21 | 74.97 | 70% dos fn/fp de títulos em B1, D e E | E2 muda o peso do nível | médio | médio | não, após captura |
| E7 | listas falsas no OCR ruidoso | `ocr/tesseract/postprocess.py` / listas | +1.05 | 76.02 | grupos/itens falsos (10) → 0 | — | baixo | baixo | não, após captura |
| E8 | OCR degradado (não sombra) até o piso limpo | pré-processamento por página | +4.69 | **80.71** | todas as páginas D com as taxas das páginas limpas | experimento de OCR | alto | alto | **sim** (especulativo) |

- **Cumulativo com tudo, incluindo as etapas especulativas E2 e E8: ≈ 80.7.** Categorias resultantes: Texto 84.0, Cobertura 75.4, Títulos 84.4, Parágrafos 81.0, Listas 100, Tab. estrutura 90.4, Tab. células 75.0, Dados 80.3, penalidade −2.5.
- **Por que 85 não é alcançável com o Tesseract na configuração atual**: o que falta, além do piso limpo, é reconhecer os segmentos adversariais (IDs com `O0I1l`, `rn`/`m`, sinais e caminhos), que a própria página limpa de 300 dpi erra. Faltariam ≈ +4.3 pontos de Cobertura, Dados e Células acima do piso: por exemplo Cobertura 75 → 88 e Dados 80 → 90. Evidência: S1b = 78.6 (estrutura perfeita sem melhorar o reconhecimento) e S3 = 85.4 (estrutura perfeita **e** OCR no piso limpo).
- **O que poderia mudar essa conclusão** (não medido): variantes de configuração do Tesseract (perfil CLAHE; dicionário desligado, `load_system_dawg=0`/`load_freq_dawg=0`, contra o viés de palavra do português nos IDs; outro `--psm`) e a hipótese H-esp. As duas exigem OCR e autorização.
- **Inconsistências do gabarito**: nenhuma comprovada. Registro separado de uma **convenção** que pesa 2.0 pontos: os 185 títulos do gabarito V4 são `###`, e o título visível de cada página ("Página 009 | Texto nativo denso…", 12.6 pt no PDF) é omitido do gabarito, embora não seja cabeçalho global (o contrato só declara omitidos cabeçalho/rodapé globais e marcas d'água). A extração emite `##` (nativo) e `######` (OCR). Nenhum arquivo foi alterado.

#### Fase 5 — correção recomendada para o próximo ciclo: **T3-P41**

- **Impacto no V4**: +1.6 estimado (teto +2.2), o maior entre as correções que **não** precisam de OCR nem de decisão sobre convenção do gabarito.
- **Qualidade real**: 32 páginas nativas do V4 com até 10 parágrafos num único bloco passam a ter os parágrafos certos; palavras, ordem e dados não mudam.
- **Generalização**: texto nativo denso com espaçamento compacto é comum fora do corpus; a regra proposta é relativa ao próprio espaçamento da página.
- **Risco de perda de conteúdo**: nenhum por construção (só fronteiras de bloco).
- **Risco nos outros corpora**: V3 e V5–V8 têm parágrafos nativos com F1 de 0.82 a 0.97. Controle total possível, porque páginas nativas não chamam o Tesseract.
- **Custo de validação**: o menor de todos; a bateria nativa inteira dos 8 corpora pode ser comparada sem OCR.
- **Desenho (para aprovação, não implementado)**: em `_native_paragraphs`, aceitar também a quebra **relativa**: vão ≥ k × vão típico (k ≈ 2.5) **e** vão ≥ um piso absoluto pequeno (≈ 0.35 × altura), exigindo **corroboração** da linha anterior curta (termina antes de ≈ 85% da largura útil do bloco). Manter a regra atual, sem mudança, quando o espaçamento é uniforme. Não usar texto ("Parágrafo NNN"), número de página nem nome de corpus.
- **Controles**: palavras idênticas byte a byte (sem os separadores de bloco) em toda página nativa; V3, V5–V8 sem piora de `paragraph_detection_f1` por página; código (p33–p36) intacto; listas e tabelas nativas idênticas; motores OCR não afetados (o caminho só roda com linhas nativas).

#### Fase 6 — validação progressiva para T3-P41 (após autorização)

| Nível | Conteúdo | Custo |
|---|---|---|
| 0 | este diagnóstico; refutação: se as `TextLine` reais não tiverem o vão relativo ≥ 2.5 × o típico nas fronteiras de parágrafo, a hipótese cai | 0 |
| 1 | capturar as `TextLine` de entrada de `_native_paragraphs` em **todas as páginas nativas** dos 8 corpora (sem OCR) e comparar base × candidata offline: fronteiras × gabarito, palavras idênticas | segundos a poucos minutos; 0 Tesseract |
| 2 | V4 p9, p17 (D) + V7 p1 (controle) + V4 p33 (código), extração nativa | 1–2 processos, sem OCR |
| 3 | D: V4 p9, p13, p17, p25, p29, p37; S: V5 p3, p10; V8 p4 | poucos processos, sem OCR |
| 4 | **D** acima; **G** (escolhido agora, antes de ajustar): V4 p11, p15, p19, p21, p27, p31, p39, p217, p219, p221, p193, p205; V3/V5–V8 páginas de prosa nativa nunca vistas (sorteadas no início do ciclo). **S**: todas as páginas nativas de V3 e V5–V8, nativas de V9/V10, V4 p41–p80 (tabelas) e p33–p36 (código), idênticas ou com piora zero | 30–50 páginas de G/D; S pode ser o conjunto nativo inteiro, sem OCR |
| 5 | RUN completa, executada pelo responsável | — |

- Reaproveitamento: as páginas com OCR (D, E, V9/V10 raster) não passam pelo caminho alterado e podem ser reaproveitadas da 012, desde que a extração das nativas seja feita com o mesmo código e contexto.
- **Lição da 012 aplicada**: a mudança não tem agregado documental (a decisão é por bloco), e o G cobre todas as famílias nativas do alcance (coluna única, multicoluna, formatação inline, continuação, adversarial nativo, controles).

#### Decisões pendentes do responsável

1. **Meta de 85 no V4**: pela evidência acima, inalcançável com o Tesseract na configuração atual mesmo com todas as correções estruturais (teto realista ≈ 80.7). Opções: aceitar uma meta diferenciada para o V4; autorizar experimentos de configuração do Tesseract (perfil CLAHE, dicionário, `psm`) em poucas páginas; ou avaliar outro motor no V4 (fora da Série 3).
2. **Nível de título no V4 (+2.0)**: tratar como convenção do gabarito (sem ação) ou como hierarquia relativa ao título de página suprimido. A segunda opção só deve ser implementada se for verdadeira no documento, e não como ajuste ao gabarito.
3. **Autorização para o componente nativo** `assemble/content.py::_native_paragraphs` (T3-P41).
4. **Autorização para dois testes mínimos de OCR**: a TSV bruta da p81 (H-esp, 1 chamada) e o perfil `tesseract-degraded-scan-v1` em 4 páginas (sombra e ruído).

### §1.10 Paralelismo e desempenho do OCR (investigação de 2026-11-16)

- **Tesseract 5.5.0** (com OpenMP): o paralelismo interno existe só no LSTM e tem teto fixo de 4 threads (`num_threads(GFS=4)` em `src/lstm/lstm.cpp`, `kNumThreads = 4` em `src/lstm/fullyconnected.cpp`); `OMP_THREAD_LIMIT` só reduz. Segmentação, binarização e carga do modelo são seriais.
- **Medição** (8 páginas a 288 DPI, 3 repetições): `OMP_THREAD_LIMIT` 1/2/4/8 → 16.0 / 14.8 / 14.6 / 14.7 s, com CPU 100/134/196/197%. **8 ≡ 4**; o ganho máximo sobre 1 thread é ≈ 9%. TSV idêntico byte a byte em todas as configurações.
- **Processos concorrentes** escalam (OMP=1: K=3 → 2.6×, K=4 → 3.0×), mas dentro de uma página só ≈ 6% do tempo está em tentativas OCR independentes (retry de rotação, escalas de refino) e ≈ 3% entre regiões. **Paralelismo interno à página não foi implementado** (ganho pequeno frente ao risco).
- **Aplicado**: o PNG temporário entregue ao Tesseract é gravado com `compress_level=1` (era o padrão 6). É sem perdas: pixels decodificados e TSV idênticos nas 8 páginas; o tempo de gravação + Tesseract cai 11–14% por chamada de página inteira (≈ 3% do tempo total da extração). Teste: `tests/test_tesseract_effective.py::test_temporary_image_given_to_tesseract_is_lossless`.

### §1.32 RUN 013 — auditoria operacional e comparação com a 012 (análise de 2026-11-24, commit `b541246`)

Etapa exclusivamente de auditoria e diagnóstico: nenhuma extração, nenhum OCR, nenhum teste, nenhuma alteração de código, testes, PDFs, gabaritos, manifestos, configuração ou avaliador. Fontes: `output/tesseract/012` e `013` e `corpus/`. O avaliador oficial foi chamado **em memória** (`evaluate_document`, que não grava arquivos) para reproduzir as métricas da 013 e para dois contrafactuais (§1.34). A rubrica §1.16 foi recalculada por um script novo, só de leitura, que reproduz exatamente as notas da 012 (§1.29) nos 8 corpora.

#### Verificação operacional

| Item | Resultado |
|---|---|
| Execução | 2026-11-23 15:45:40 → 17:22:38 UTC. **9/9** com extração `exit 0`, métricas `ok` e `rank_eligible`; métricas aplicáveis V3 61, V4 61, V5 67, V6 63, V7 65, V8 65, V9 67, V10 61, **V11 60** |
| Código | `implementation_commit` `b541246` nos 9 arquivos de métricas. Commit às 15:44:50 UTC, 50 s antes do início. `git diff 18ccfb0 b541246`: só `scripts/run_tesseract_batch_wsl.py` (+1 linha: V11) e este documento; `src/` e `tests/` iguais ao código da 012. Os artefatos não registram se a árvore estava suja; a identidade byte a byte de V3–V10 confirma o mesmo código |
| Entradas | `input_hashes` de V3–V10 iguais aos da 012. Hashes do V11 (PDF `23dc2b76…`, manifesto `27e8b62d…`) conferem com os recalculados hoje e com o `VALIDATION_REPORT` do corpus |
| Configuração | `settings` e `extractor_flags` iguais aos da 012 (balanced, pt-BR, adaptive, threads 8, OMP 8, `preserve_headers_footers: false`) |
| Avaliador | `evaluation-3.0.0`, métricas 3.1.0; todas as políticas do bloco `evaluator` iguais; só `implementation_commit` e `created_at_utc` mudam |
| Status | 8 corpora `success`/`valid`; V9 `partial_success`/`partial` → `needs_review` só pela p36 (T3-P33, mesma exceção) |
| V11 | **120/120 páginas** extraídas e avaliadas, `success`/`valid`. Avisos: p10, p84, p87, p119 sem tokens (controles negativos, saída vazia correta); p118 `unaccounted_content` (1 linha recuperada). Recuperadas: p26, p32, p39, p63. Nenhuma exceção. **A p71 perde texto sem aviso** (T3-P44) |
| Formato paginado | V11: 120 marcadores `## Página N`, `markdown_validity` válido, 0 erros de marcador, 0 páginas ausentes ou fora do escopo |
| Tempo de extração (012 → 013) | V3 −4.6% · V4 −4.7% · V5 −7.6% · V6 −5.8% · V7 −8.4% · V8 −5.9% · V9 +3.5% · V10 −5.4% (saídas idênticas: ruído do ambiente). V11: 555 s (4.6 s/página) |

#### V3–V10: 013 × 012

**Idênticos byte a byte nos 8 corpora** (mesmo `hypothesis_sha256`). Métricas documentais e por página com 0 diferenças; auditorias suplementares idênticas; manifestos de execução idênticos salvo `run_id`, `elapsed_s` e caminho. Nenhuma página mudou, nenhuma regressão, nenhum aviso novo. Notas da rubrica iguais às da 012 (§1.33). **A inclusão do V11 não interferiu nos demais corpora.**

### §1.33 V11 — características, gabarito e notas da rubrica (RUN 013)

**Corpus**: edição V11-R1C, 120 páginas (58 nativas, 34 raster, 28 mistas), 87 tabelas físicas e 8 tabelas contínuas de 3 páginas. Famílias: F01 capas e controle (p1–12), F02 tabelas contínuas (p13–36), F03 geometrias tabulares (p37–60), F04 scans degradados (p61–78), F05 símbolos e regiões gráficas (p79–94), F06 ordem em colunas (p95–106), F07 limites e deduplicação (p107–114), F08 controles finais (p115–120). Gabarito gerado do modelo de composição, sem OCR.

**Gabarito**:
- O executor escolheu o **manifesto** (`expected_markdown`, 120/120): `REFERENCIA.md` e `REFERENCIA_PAGINADA.md` marcam as páginas com `<!-- PAGE_START -->`, que `split_pages` não reconhece (1 "página" cada). O conteúdo das três fontes foi comparado e é **idêntico nas 120 páginas**: a escolha não tem impacto.
- Cabeçalho e rodapé correntes ("Coleção sintética · N", "Ensaio V11-R1 · Folha N", "INSTITUTO FICTÍCIO…") **não** estão no gabarito, coerente com o contrato `preserve_headers_footers: false`.
- **Limitações e ambiguidades (classe E; nenhum arquivo alterado)**:
  1. Convenção de nível: o gabarito usa `#` em todo título de página; a extração aplica hierarquia documental (o título da capa é `#`, os de página `##`). `heading_level_match_rate` 0.056 (4/71), ≈ −1.9 na nota (T3-P50), como a convenção de V3/V4 (§1.16).
  2. "Continuação do registro técnico · folha sintética N" (p15, 21, 24, 27, 30, 33, 36) está **no** gabarito, embora seja estruturalmente um cabeçalho corrente com contador de página. Uma correção genérica de T3-P43 tende a suprimi-la (custo medido: −0.3).
  3. Denominadores pequenos: `currency` tem 5 valores e `percent` 3. Os dois falsos positivos (p50, tabela duplicada; p61, lixo de OCR) custam ≈ 3.1 na nota.
  4. p54: o gabarito exige ☑/☐ que só existem como desenho vetorial no PDF (T3-P48). Semanticamente legítimo.
  5. O gerador registra o conflito da Tabela H (p34–36, 7 atributos em vez de 6); sem efeito medido (CER ≤ 0.025 nessas páginas).

**Notas da rubrica §1.16 (RUN 013)** — rubrica, pesos, limiares e penalidades **não alterados**:

| # | Corpus | Texto | Cobertura | Títulos | Parágrafos | Listas | Tab. estrutura | Tab. células | Dados | Ordem | Penalidade | **Nota 013** | Nota 012 | Δ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | V7 | 99.4 | 99.5 | 98.8 | 97.6 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 0 | **99.4** | 99.4 | 0.0 |
| 2 | V8 | 98.2 | 98.6 | 99.4 | 96.2 | 100.0 | 100.0 | 99.9 | 98.5 | 100.0 | 0 | **98.8** | 98.8 | 0.0 |
| 3 | V6 | 96.7 | 96.5 | 99.0 | 96.3 | 100.0 | 100.0 | 96.3 | 91.6 | 100.0 | 0 | **96.7** | 96.7 | 0.0 |
| 4 | V3 | 94.9 | 95.2 | 68.0 | 92.0 | 97.3 | 99.9 | 99.1 | 98.0 | 98.3 | −0.5 | **93.1** | 93.1 | 0.0 |
| 5 | V5 | 67.3 | 83.5 | 83.8 | 83.8 | 92.1 | 100.0 | 100.0 | 98.5 | 87.1 | 0 | **86.5** | 86.5 | 0.0 |
| 6 | V9 | 77.8 | 96.8 | 54.5 | 65.8 | 84.7 | 79.9 | 63.9 | 86.3 | 58.4 | −1.0 | **75.6** | 75.6 | 0.0 |
| 7 | V10 | 56.5 | 95.3 | 62.9 | 56.3 | 72.7 | 96.5 | 91.2 | 86.9 | 51.3 | 0 | **75.5** | 75.5 | 0.0 |
| 8 | V4 | 77.3 | 68.1 | 52.3 | 54.0 | 79.1 | 86.8 | 70.4 | 69.2 | 100.0 | −4.5 | **66.9** | 66.9 | 0.0 |
| 9 | **V11** | 61.9 | 84.3 | 53.1 | 58.4 | 43.4 | 79.6 | 75.9 | 65.1 | 64.5 | −1.0 | **65.9** | — | — |

- **V11**: base ponderada 66.9; penalidade −1.0 pela p71 (saída com < 50% do volume de referência). Todas as 9 categorias são aplicáveis; `identifiers_combined` é N/A e foi renormalizado dentro de Dados (como em V3, V4 e V10). Ordem: 16 das 116 páginas com texto têm CER > 0.15 e recall ≥ 0.95.
- **Métricas oficiais do V11**: CER 0.1139, WER 0.1528, recall 0.9672, precisão 0.9315; títulos tp 71 / fp 13 / fn 50 (F1 0.693), nível 0.056, CER de título 0.480; parágrafos tp 390 / fp 475 / fn 100 (F1 0.576), fronteiras F1 0.592; listas: grupos F1 0.273, itens tp 7 / fp 6 / fn 25; tabelas tp 66 / fp 11 / fn 21 (F1 0.805), colunas 0.643, células F1 0.886, células exatas 0.750, alinhamento 0.784, `cell_cer` 0.351; `numeric` 0.935 (tp 2169 / fp 250 / fn 50), `currency` 0.909 (5 valores), `percent` 0.857 (3), `dates` 1.000 (36); `duplicate_excess_rate` 0.422 (ver T3-P19).
- **Distribuição das edições** (`cer_sequential_normalized`, 20 094 no total): F04 44.4% (CER 0.187), F06 21.0% (0.199), F03 11.4%, F01 9.4%, F08 4.9%, F05 4.7%, F07 2.4%, F02 1.7% (0.018). Por origem: raster 57%, nativa 33%, mista 9%. Maiores páginas: p61 (2097), p63 (1683), p98 (1467), p71 (1302), p5 (1097), p95 (989), p72 (947), p75 (775), p96 (698), p78 (411).
- **Meta de 85**: V11 está a −19.1. Junto com V4 (−18.1), V9 (−9.4) e V10 (−9.5), é um dos quatro corpora abaixo da meta.

### §1.34 V11 — problemas encontrados, origem técnica e quantificação (RUN 013)

**Contrafactuais com o avaliador oficial (em memória; nenhum arquivo gravado)**. A avaliação em memória da saída original reproduz a nota oficial (65.9). Removendo **só** as linhas de cabeçalho/rodapé corrente ausentes do gabarito:

| Corpus | Linhas removidas | CER | WER | Recall | Precisão | `numeric` | Nota |
|---|---|---|---|---|---|---|---|
| V11 original | — | 0.1139 | 0.1528 | 0.9672 | 0.9315 | 0.935 | 65.9 |
| V11 sem mobília | 185 | 0.0821 | 0.1132 | 0.9671 | 0.9697 | — | **71.0** (Texto 72.2, Cobertura 89.4, Parágrafos 68.0, Dados 72.1, Ordem 71.0) |
| V11 sem mobília e sem "Continuação…" | 192 | 0.0842 | 0.1155 | 0.9648 | 0.9697 | — | 70.7 |
| V10 original | — | 0.1373 | 0.1649 | 0.9938 | 0.9741 | 0.945 | 75.5 |
| V10 sem "Documento sintético … \| Folha NN/84" | 65 | 0.1203 | 0.1430 | 0.9930 | 0.9950 | 0.992 | **78.9** (Texto 62.1, Cobertura 97.9, Títulos 64.0, Parágrafos 58.2, Dados 97.5) |

- No V10, a queda de recall (0.9938 → 0.9930, p5, p11, p18, p37, p50, p81) é efeito do casamento por bolsa de tokens: palavras do rodapé ("de", "para", "OCR") contavam para ocorrências do corpo que o OCR perdeu. Não houve perda de texto do corpo.
- Esses números são **limites superiores** de uma supressão perfeita, não a previsão de uma implementação.

**Problemas por origem** (A reconhecimento · B reconstrução · C estrutura · D integridade · E avaliação). Os novos recebem T3-P43 a T3-P50 (§2).

| ID | Classe | Severidade | Páginas | Esperado → obtido | Causa | Componente | Impacto |
|---|---|---|---|---|---|---|---|
| **T3-P44** | D (perda silenciosa) | **CRÍTICO** | p71; parciais p63, p75 | 4 parágrafos + tabela de 3 linhas → só o 1º parágrafo; "FETIZ", "E" no lugar dos valores | **hipótese**: texto de baixo contraste sob carimbo sem tokens do Tesseract; a política adaptativa reage a texto ilegível, não a falta de cobertura. Etapa não localizada | reconhecimento + política OCR | −1.0 de penalidade; recall 0.327 |
| **T3-P43** | D/B (conteúdo injetado) | ALTO | 94 págs. do V11; 65 do V10 | corpo sem mobília → `Coleção sintética · 103` … `Ensaio V11-R1 · Folha 103` | **demonstrada**: `_normalize_signature` não generaliza o contador sem rótulo "página/page" | `assemble/repeated_regions.py` | +5.1 (V11) e +3.4 (V10) no contrafactual; ≈ 185 `numeric` fp |
| **T3-P46** | C/B | ALTO | p5, p98, p86; p3, p7, p102 | parágrafos em colunas → linhas das colunas intercaladas em `<td>`; lista "•" → tabela de 2 colunas | detector de tabela sem borda aceita colunas de texto corrido e marcador de lista | detector nativo | p5 + p98 = 12.8% das edições; listas do V11 |
| **T3-P45** | D (duplicação) | ALTO | p50, p93 | 1 tabela → 2 cópias; título partido em células | hipótese: dois candidatos sobrepostos sem deduplicação | detecção/fusão de tabelas | `currency` fp; título perdido |
| T3-P47 | C (associação) | ALTO | p57, p9, p37, p41, p54, p110, p115, p120; p45, p60, p63 parciais | tabelas → células soltas coluna a coluna (p57) ou campos de formulário fora de ordem (p9) | segmentação de tabelas sem borda empilhadas (família de T3-P37) | detector nativo | tabelas fn 21; células exatas 0.750 |
| T3-P48 | D (informação visual) | MÉDIO | p54 | 15 estados ☑/☐ → nada | caixas vetoriais; recurso inexistente | — | `checkbox_states` fn 15 (auditoria suplementar) |
| T3-P49 | B (ordem) | MÉDIO | p61; possivelmente p72 | linhas de cada parágrafo em ordem invertida | hipótese: ordem no referencial inclinado (família T3-P35) | reconstrução OCR | p61 CER 0.539 |
| (T3-P06) | C | ALTO | raster F04/F06 | 22 dos 30 títulos raster ausentes; falsos títulos de lixo OCR (p75) | títulos OCR (problema existente) | `layout/heading.py` | Títulos 53.1 |
| (T3-P21) | A | ALTO | p63 (−8°), p75 (moiré), p61, p72 | `4D8AAGA3`, "custádia", fragmentos ilegíveis; p63 `numeric` fn 49 | limite do Tesseract nas degradações | OCR | F04 = 44% das edições |
| T3-P50 | E | INFO | 100 títulos | `#` → `##` | convenção do gabarito | — | ≈ −1.9 |

- **Integridade crítica** (prioridade sobre a nota): T3-P44 (perda silenciosa), T3-P45 (duplicação e título perdido), T3-P48 (estado visual perdido). T3-P43 injeta conteúdo que não pertence ao corpo e contamina os dados críticos com números falsos.
- **Estruturais**: T3-P46, T3-P47, T3-P49, títulos OCR (T3-P06), listas nativas não reconhecidas (p3, p86, p102, p120: 20 dos 25 itens fn).
- **Não confundir**: em p5 e p98 todas as palavras estão corretas e presentes (recall 1.0); o erro é de estrutura e ordem, não de reconhecimento. Em p61 as palavras também estão corretas; só a ordem das linhas está invertida.
- **Páginas sem problema relevante**: F02 nativa (p13–p36) com tabelas contínuas exatas (CER ≤ 0.09), datas 36/36, controles negativos p10/p84/p87/p119 corretamente vazios.

### §1.35 Priorização pós-013 e proposta para T3-P43

**Decisão do responsável (2026-11-24)**: o **V11 passa a ser a prioridade** da otimização do Tesseract; V3–V10 são controles de regressão. A meta de 85/100 vale para os 9 corpora, com a rubrica inalterada. T3-P41 (V4) continua pendente, adiado e não descartado.

**Os três problemas mais importantes do V11**:

| | **T3-P43** mobília com contador | **T3-P44** perda silenciosa em raster | **T3-P46** colunas/lista nativas como tabela |
|---|---|---|---|
| Severidade | ALTO (conteúdo injetado; ≈ 185 números falsos) | **CRÍTICO** | ALTO (leitura destruída, sem perda de palavras) |
| Impacto | **medido** (contrafactual): V11 +5.1 (+4.8 com a "Continuação"), V10 +3.4 | p71: +1.0 de penalidade e texto; ≈ +1 a +2 (não medido) | p5 + p98 = 12.8% das edições; ≈ +2 a +3 (não medido) |
| Causa | **demonstrada no código** | não localizada | hipótese |
| Generalização | alta; controle independente (V10) | alta, se for falta de cobertura não sinalizada | alta |
| Risco de regressão | baixo, triado offline | médio (tempo e variantes OCR) | médio-alto (V5–V9 dependem do detector) |
| Componente | `assemble/repeated_regions.py` (compartilhado) | política OCR/Tesseract | detector nativo (compartilhado) |
| Repetir OCR | não para a lógica; só para capturar páginas raster | sim | não |
| Custo de validação | baixo | alto | médio |
| Benefício em outros documentos | V10 comprovado | V4/V9 degradados (provável) | V9/V10 nativas multicoluna (provável) |

**Correção recomendada para o 1º ciclo: T3-P43.** É a única com causa demonstrada, efeito medido em dois corpora (um independente), risco triado e validação barata; é também um defeito de integridade (conteúdo estranho ao corpo e números falsos). **T3-P44 é o mais grave**, mas não pode ser corrigido com responsabilidade sem localizar a etapa da perda: recomenda-se, no mesmo ciclo e **sem alterar código**, uma captura diagnóstica da V11 p71 (1 página com OCR, TSV bruto e regiões antes e depois do pós-processamento), sujeita a autorização. T3-P44 fica como candidato do 2º ciclo.

**Proposta técnica para T3-P43 (não implementada; exige autorização para o componente compartilhado)**:
1. **Assinatura com marcador**: em `_normalize_signature`, todo número **isolado** (delimitado por espaço, início/fim ou pelos separadores `· | : / -` cercados de espaço; nunca colado a letras ou a hífen de identificador) vira `<n>`; os valores são guardados por observação.
2. **Confirmação por posição numérica** em `detect_repeated_headers_footers`: valor constante em todas as ocorrências → literal (comportamento atual); valor que acompanha o índice da página (valor − número físico = k constante, estritamente crescente) em **≥ 3 páginas** → contador; qualquer outro padrão → o grupo é desfeito nas assinaturas exatas atuais. `_stable_edge_position`, `_stable_style` e o veto `_SEMANTIC_CONTROL_RE` continuam valendo.
3. `repeated_line_keys` devolve a mesma forma com `<n>` (assinatura preservada); a chave só entra em `confirmed` pela regra 2, então `_split_mixed_repeated_blocks` e `_apply_repeated_suppression` não mudam.
4. OCR: `_confirm_ocr_variants` já agrupa por letras e números; com o contador em `<n>`, as variantes "·", ":" e "-" do mesmo rodapé caem no mesmo grupo.

**Prevenção de overfitting (respostas da Fase 5)**:
- **Causa raiz**: o número da página torna a assinatura única por página e impede a confirmação da repetição.
- **Por que resolve**: separa número de página de número semântico pela **covariância com o índice da página**, não por texto. Datas, IDs e totais não acompanham a página.
- **Quem se beneficia**: documentos paginados com "Folha N", "Fls. N", "· N", "N/M" sem a palavra "Página" (processos, contratos, relatórios em português).
- **Onde pode falhar**: numeração por seção (reinicia; deslocamento não constante → não confirma, comportamento atual), numeração romana, mobília OCR muito inclinada (posição instável, como V11 p63).
- **Refutação já feita (Nível 0)**: V10 não foi usado para formular a hipótese e tem o mesmo defeito (+3.4 no contrafactual). Triagem das linhas de borda **mantidas no gabarito** que contêm o número da página, nos 9 corpora: só a "Continuação…" do V11 (custo aceito, −0.3); V4 `TOKEN-035-06`, `HYB-TWO-190`, `COVERED-OK-194`, `SEQ-END-200` (número embutido em identificador, excluído pela regra de token isolado, e em só 2 páginas); "Controle de página" em V3/V4/V5 (vetado por `_SEMANTIC_CONTROL_RE`). V5–V9 não têm contador vazado: previsão de saída idêntica.
- **Detecção automática de regressão**: diff das linhas suprimidas por página (013 × candidata), palavras idênticas fora delas, `unaccounted_content` e avisos inalterados.
- **Camada alterada**: montagem (assemble), pós-OCR. Não altera reconhecimento, geometria, layout nem serialização.
- **Rejeitados como overfitting**: regras por texto ("Coleção sintética", "Ensaio V11"); remoção por número de página do V11; ajustar o nível de título ao `#` do gabarito (T3-P50).

**Aceitação do V11**: continua **não satisfatória**. A projeção depois de T3-P43 é ≈ 70.7; ainda faltam T3-P44, T3-P45 e T3-P48 (integridade), T3-P46 e T3-P47 (estrutura) e o limite de OCR (T3-P21). Nada aqui prova que 85 seja inalcançável: as projeções não são limites.

### §1.36 Ciclo pós-013: diagnóstico de T3-P44 na V11 p71 (2026-11-24; 1 página, 1 execução, sem alteração de código)

**Autorização**: captura diagnóstica de OCR da V11 p71, limitada a uma página e uma execução. **Execução**: a p71 foi capturada **dentro** da extração completa do V11 feita para T3-P43 (a mesma execução serviu às duas finalidades; nenhuma extração extra). Harness no scratchpad, sem alterar o repositório: intercepta `_save_temp_png`/`_run_tesseract_tsv` do backend (imagem **efetivamente** gravada para o Tesseract, parâmetros e TSV bruto de cada chamada da página) e `assemble_document` (entrada documental completa). A saída da captura é **idêntica byte a byte** à da RUN 013 (mesmo código, `b541246`).

**Medida por etapa** — fração das palavras de cada região do gabarito que sobrevive (multiconjunto):

| Etapa | Total | Título | Tabela | P1 (R000) | P2 (R200) | P3 (R201) | P4 (R202) | P5 (R075) |
|---|---|---|---|---|---|---|---|---|
| 1a. Tesseract, leitura direta (2382 × 3368 px, 288 dpi, `psm 3`, perfil `default`): 109 palavras, conf. média 90.11, 638 caracteres | 0.33 | 1.00 | 0.22 | 1.00 | 0.96 | 0.60 | 0.58 | 0.52 |
| 1b. Tesseract, releitura endireitada (`_deskew_page`, ângulo medido 2.01°, 2500 × 3450 px): 295 palavras, conf. média 86.45, 1727 caracteres | **0.95** | 1.00 | 0.56 | 1.00 | 1.00 | **1.00** | **1.00** | **0.94** |
| 2. Tokens e linhas OCR na `StructuredPage` (11 regiões) | 0.33 | 1.00 | 0.22 | 1.00 | 0.96 | 0.60 | 0.58 | 0.52 |
| 3. Blocos (montagem + pós-processamento) | 0.33 | idem | | | | | | |
| 4. Markdown final | 0.33 | idem | | | | | | |

(Nas colunas P3–P5 da leitura direta, os valores 0.52–0.60 são palavras comuns, "de", "o", "setor", casadas em outros parágrafos; o texto desses parágrafos não existe na leitura direta.)

**Onde o conteúdo desaparece pela primeira vez**:
- Na **leitura direta**, a segmentação do Tesseract não cria **nenhum bloco** entre y ≈ 1167 px e o rodapé (y ≈ 3186): os níveis 2–4 do TSV param no 2º parágrafo. A página tem −2° de inclinação, texto claro e legível e o carimbo "ARQUIVO DE TESTE" sobre o último parágrafo.
- O backend detecta a inclinação e **relê a página endireitada**: essa leitura encontra os blocos ausentes (y = 1329, 1537, 1744 px) e reconhece **95%** do gabarito.
- `_deskew_page` **descarta** a releitura: aceita só se `conf_releitura ≥ conf_direta − _DESKEW_MAX_CONFIDENCE_LOSS` (1.0) **e** `caracteres_releitura ≥ 0.9 × caracteres_diretos`. 86.45 < 90.11 − 1.0 → rejeitada, apesar de 2.7× mais caracteres.
- Daí em diante **nada mais se perde**: reconstrução, layout, montagem, pós-processamento e Markdown preservam exatamente o que a leitura direta trouxe (0.33 em todas as etapas). A única supressão na página é o rodapé corrente.

**Classificação** (A/B/C pedidos):
- **Não é A** como causa final: o Tesseract reconhece o texto (releitura com 0.95). A leitura direta falha na segmentação, e o próprio backend já tem a leitura correta em mãos.
- **É B no backend**: o conteúdo reconhecido é descartado na **seleção entre duas leituras** (`ocr/backends/tesseract.py::_deskew_page`), antes da reconstrução.
- **É também C**: nada registra a perda. Fatos da página: `ocr_outcome = success`, `ocr_degraded = false`, `ocr_recovery_triggered = false`, `ocr_variants_attempted = []`, `content_preservation_final_ok = true` (a conservação só contabiliza linhas que existem), nenhum aviso. A decisão de `_deskew_page` não é registrada em nenhum fato.

**Defeito geral (descarte silencioso)**: a comparação usa a **média de confiança sobre conjuntos diferentes de palavras**. A leitura incompleta omite justamente as áreas difíceis, cujas palavras têm confiança menor, e por isso tem média maior. Nas **100 palavras comuns às duas leituras, a releitura tem confiança maior** (90.88 contra 90.11); a média caiu só porque ela acrescentou 186 palavras com confiança média ≈ 84. O critério penaliza a leitura mais completa. Qualquer página com inclinação entre 1° e 10° em que a leitura direta perca blocos inteiros está sujeita ao mesmo descarte; `_retry_quarter_turns` usa médias de confiança de forma análoga (não verificado se tem o mesmo efeito).

**Escopo**: não medido. A decisão não deixa rastro, e saber quais páginas são afetadas exige OCR. Páginas candidatas (inclinadas, com recall < 0.92 na 013): V11 p63 (0.766), p62 (0.899), p70 (0.908), p72 (0.914), p67 (0.912); V10 L01/L02; V4 e V9 inclinadas. **Não ampliei o experimento** (limite autorizado: 1 página, 1 execução).

**Correção proposta (separada, prioridade CRÍTICA; não implementada; exige autorização para o backend Tesseract)**:
1. **Seleção por evidência comparável**: comparar a confiança **nas palavras que as duas leituras compartilham** (por posição, após mapear a releitura de volta ao referencial original, como `_unrotate_tsv` já faz) e tratar o excedente de caracteres como evidência a favor da leitura mais completa. Aceitar a releitura quando ela cobre substancialmente mais texto (por exemplo, ≥ 1.25× caracteres) e não piora a confiança nas palavras comuns. Não deve haver número tirado do V11: o limiar de cobertura deve ser justificado e testado em páginas inclinadas de V4, V9 e V10.
2. **Observabilidade (sem mudar a saída)**: registrar em fatos da página a decisão de cada releitura (palavras, caracteres e confiança das duas leituras, motivo) e emitir aviso quando a leitura descartada tiver muito mais caracteres que a escolhida (`ocr_more_complete_read_discarded`). Isso torna a falha não silenciosa e, numa rodada completa, mede o escopo **sem OCR extra**.
3. **Validação**: Nível 1 com os TSV já capturados da p71 (a decisão é uma função pura dos dois TSV); Nível 2 com 2–5 páginas inclinadas de corpora distintos; generalização em páginas inclinadas de V4 e V9 não usadas no ajuste; risco principal: aceitar releituras com mais lixo (moiré, ruído); controle por precisão, `numeric` fp e CER.

### §1.37 Ciclo pós-013: T3-P43 implementado e validado offline (2026-11-24; não commitado)

**Autorização**: alterar `assemble/repeated_regions.py` só para T3-P43 e testes estritamente necessários. **Restrições do responsável**: a coincidência com o índice da página, sozinha, não basta; três páginas não bastam; a linha "Continuação do registro técnico · folha sintética N" (V11, 7 páginas, **no** gabarito) não pode ser removida para melhorar a nota; nenhuma regra por texto literal, corpus ou número de página.

#### Implementação

Nova fase em `detect_repeated_headers_footers`, executada **depois** das confirmações existentes (repetição exata e variantes OCR), que **só acrescenta** ao resultado as assinaturas exatas das linhas aceitas. `repeated_line_keys`, a divisão de blocos e a supressão em `assemble/document.py` não mudaram; casos que já funcionavam não passam pela fase nova (linhas já confirmadas são excluídas dela). Uma linha de borda com número isolado é mobília com contador somente se **todas** as evidências convergem:

| Evidência | Regra | Por que generaliza |
|---|---|---|
| Borda e veto semântico | mesmo teste de faixa de borda de qualquer mobília; `_SEMANTIC_CONTROL_RE` continua vetando (controle de página, datas, processos) | inalterado |
| Número isolado | dígitos não colados a letras, hífen, separador decimal ou `%` ("Folha 12", "· 012", "03/84" sim; "V11-R1", "TOKEN-035", "1.245,90", "12%" não) | identificadores, valores e percentuais nunca viram contador |
| Forma textual igual | mesmas letras e demais dígitos; pontuação livre (OCR lê "·" como "-" ou ":") | variação de OCR não separa a mesma linha |
| Contador da paginação | cada número variável tem **um único** deslocamento valor − índice em **todas** as ocorrências; números constantes ficam literais | contador que reinicia, salta ou se repete invalida o grupo inteiro (preservado) |
| Repetição corrente | ao menos **3 páginas consecutivas** | mobília corre página a página; linha repetida só onde o conteúdo pede (continuação de tabela, folha de anexo) é conteúdo |
| Posição estável | evidências exigidas do **núcleo** de ocorrências com centro vertical a ±0.0175 da mediana (espalhamento ≤ 0.035, a mesma tolerância de `_stable_edge_position`); ocorrências fora do núcleo **não** são confirmadas | caixa deslocada (scan inclinado ou reenquadrado) fica preservada |
| Estilo estável | `_stable_style` (tamanho, família, peso) | inalterado |
| Proeminência | mediana do tamanho da linha ≤ 1.1 × mediana do corpo nativo das mesmas páginas (sem tamanhos, OCR, a evidência é neutra) | título numerado de seção é maior que o corpo; mobília não |
| Ambiguidade | duas linhas da mesma forma numa página → grupo preservado | — |

**Tentativas** (1 de 5): uma implementação, com um refinamento antes de qualquer extração da candidata. Na 1ª forma, a posição era exigida do grupo inteiro e o grupo dos cabeçalhos raster do V11 era rejeitado: centro de 0.030 a 0.083, por páginas inclinadas ou reenquadradas (p61, p62, p69, p72, p78, p95). O refinamento passou a confirmar só o núcleo estável e a **preservar** as ocorrências deslocadas. Nenhuma regra por texto, corpus ou página.

#### Testes (Nível 1)

`tests/test_repeated_page_counters.py` (novo, 21 testes) + os 26 testes existentes de mobília (`test_repeated_suppression.py`, `test_furniture_body_protection.py`, `test_candidate_lines_h004b.py`): **47 passam** (executados).
- **Positivos**: contador no rodapé; no cabeçalho sem rótulo; com prefixo e total ("Folha 03/84"); deslocamento constante (capa sem número); variantes de pontuação OCR; supressão restrita ao bloco do contador.
- **Negativos**: números no corpo; IDs com hífen; datas e valores monetários; linhas de continuação esparsas com o número igual à página; texto citando outras folhas (na borda e no corpo); títulos de seção numerados, um por página (maiores que o corpo); números de itens; contador que reinicia (textos únicos); numeração não sequencial; linha legítima com o número da página em só 2 páginas; "Controle de página"; posição instável; tipografia inconsistente; ocorrência deslocada preservada com núcleo confirmado; repetição exata e "Página N de M" inalteradas.
- **Cada negativo foi verificado como discriminante**: com a evidência correspondente desligada (sequência, proeminência, contador, posição, estilo, padrão do número), o teste falha.
- **Limite**: linhas de **texto idêntico** (por exemplo "Seção 1" repetida em páginas da mesma seção) continuam sendo confirmadas pela regra de repetição exata **anterior**, como antes do ciclo. Não foi alterado (fora do escopo de T3-P43).

#### Validação sobre o contexto documental real (Níveis 1–4 em uma etapa, sem OCR repetido)

A detecção depende do documento inteiro, e as estruturas de página não estavam disponíveis (as rodadas gravam só Markdown e manifestos). Por isso foi capturada, **uma vez por corpus e em sequência**, a entrada completa de `assemble_document` (`StructuredPage` com geometria, origem e estilo de cada linha) durante uma extração real com a configuração da RUN 013; nenhum OCR concorrente. A montagem, a detecção e o pós-processamento Tesseract foram então reproduzidos offline, com o módulo **original** (base) e com o **candidato**, sobre exatamente as mesmas estruturas:
- **Equivalência comprovada**: a reprodução **base** é idêntica byte a byte à RUN 013 em todos os corpora capturados (tabela abaixo).
- **A captura não depende do módulo alterado**: o módulo só é usado na montagem, depois do ponto de captura.
- **Ressalva**: as extrações de V10 em diante carregaram o código candidato (alterado durante a cadeia). A validade dessas capturas vem da reprodução base, idêntica à 013.

**Resultados de V11 e V10** (avaliador oficial em memória e rubrica §1.16 inalterada):

| | V11 base (013) | V11 candidata | V10 base (013) | V10 candidata |
|---|---|---|---|---|
| **Nota** | 65.9 | **70.4** | 75.5 | **78.3** |
| Contrafactual ideal (§1.34) | — | 71.0 | — | 78.9 |
| Texto / Cobertura / Parágrafos / Dados / Ordem | 61.9 / 84.3 / 58.4 / 65.1 / 64.5 | 70.6 / 88.6 / 66.8 / 71.3 / 71.0 | 56.5 / 95.3 / 56.3 / 86.9 / 51.3 | 61.1 / 97.6 / 57.9 / 95.4 / 51.3 |
| CER / WER | 0.1139 / 0.1528 | 0.0869 / 0.1191 | 0.1373 / 0.1649 | 0.1234 / 0.1470 |
| Recall / precisão | 0.967214 / 0.9315 | **0.967214** / 0.9635 | 0.993842 / 0.9741 | 0.993717 / 0.9916 |
| `numeric` (fp) | 0.935 (250) | 0.963 (100) | 0.945 (135) | 0.983 (31) |
| Títulos, tabelas, listas | — | iguais | — | Títulos 62.9 → 64.0 (falsos títulos de rodapé removidos); demais iguais |
| Linhas de mobília no Markdown | 181 | 18 | 65 | 12 |

- **V11, linhas removidas**: 164, todas ausentes do gabarito (cabeçalho "Coleção sintética · N", rodapé "Ensaio V11-R1 · Folha N" e variantes OCR, cabeçalho raster "INSTITUTO … Coleção sintética - N"); **0 linhas do gabarito removidas**; nenhuma linha acrescentada; fora das linhas removidas, a sequência de palavras é idêntica em todas as 91 páginas alteradas. Recall idêntico à 013.
- **V11, grupos confirmados**: rodapé (núcleo 83 de 89; deslocamento valor − índice = 1, ou seja, o número físico da página), cabeçalho nativo (64 de 64) e cabeçalho raster (17 de 23). Lista completa das linhas suprimidas, com página e grupo: `rep/V11_repo.new_suppressions.json` no scratchpad do ciclo.
- **V11, mobília que permaneceu (18)**: páginas raster inclinadas ou reenquadradas fora do núcleo de posição (p61, p62, p69, p70, p72, p74, p76, p78, p95, p96), leituras OCR divergentes (p63 "V1I-A1"; p66 "STITUTO…") e p112 (página nativa girada). Diferença para o contrafactual: −0.6.
- **V11, "Continuação do registro técnico · folha sintética N"**: **preservada nas 7 páginas**. Na estrutura capturada a linha tem centro em 0.0827 da altura, logo fora da faixa de borda (0.08). Para não depender dessa margem, a faixa foi alargada a 0.12 numa simulação: as 7 linhas viram candidatas e **mesmo assim não são confirmadas**, porque não aparecem em páginas consecutivas (p15, 21, 24, 27, 30, 33, 36). A proteção é estrutural, não por texto. "MARGEM SUPERIOR DE ENSAIO — REGISTRO 111" (título legítimo, 1 página) também não é confirmado.
- **V10, linhas removidas**: 53 rodapés "Documento sintético … | Folha NN/84", **0 linhas do gabarito**. Grupo confirmado: núcleo 53 de 59, deslocamento 1. **As 12 que permanecem são exatamente as 12 páginas inclinadas L01/L02** (p5, 13, 18, 25, 31, 45, 48, 53, 62, 68, 75, 83; T3-P35).
- **V10, queda de recall de 0.000125**: **não é perda**. Nas p11, p37, p50 e p81 a única linha removida é o rodapé; 6 tokens dele ("de", "e") contavam como acerto de ocorrências do corpo que o OCR já perdia (casamento por bolsa de tokens), e a sequência de palavras fora do rodapé é idêntica.

**Comparação com o contrafactual**: V11 70.4 contra 71.0 (−0.6) e V10 78.3 contra 78.9 (−0.6). A diferença é integralmente a mobília preservada por falta de evidência estável (páginas inclinadas, leituras OCR divergentes), troca aceita a favor da integridade.

#### Controles V3–V9 (e resumo dos 9 corpora)

Capturas sequenciais (um processo OCR por vez) com a configuração da RUN 013; reprodução offline base × candidata sobre as mesmas estruturas:

| Corpus | Extração de captura | Saída da captura = 013 | Reprodução base = 013 | Candidata × base | Supressões de mobília (base → candidata) | Linhas do gabarito removidas | Nota (base → candidata) |
|---|---|---|---|---|---|---|---|
| V3 | 414 s | sim | sim | **idêntica byte a byte** | 273 → 273 | 0 | 93.1 → 93.1 |
| V4 | 2667 s | sim | sim | **idêntica byte a byte** | 415 → 415 | 0 | 66.9 → 66.9 |
| V5 | 94 s | sim | sim | **idêntica byte a byte** | 80 → 80 | 0 | 86.5 → 86.5 |
| V6 | 656 s | sim | sim | **idêntica byte a byte** | 184 → 184 | 0 | 96.7 → 96.7 |
| V7 | 224 s | sim | sim | **idêntica byte a byte** | 158 → 158 | 0 | 99.4 → 99.4 |
| V8 | 449 s | sim | sim | **idêntica byte a byte** | 180 → 180 | 0 | 98.8 → 98.8 |
| V9 | 632 s | sim | sim | **idêntica byte a byte** | 192 → 192 | 0 | 75.6 → 75.6 |
| V10 | 593 s | não (*) | sim | 53 páginas: só rodapés removidos | 60 → 113 | 0 | 75.5 → **78.3** |
| V11 | 590 s | sim | sim | 91 páginas: só mobília removida | 64 → 164 | 0 | 65.9 → **70.4** |

(*) A extração de captura do V10 começou depois da 1ª forma da candidata entrar no repositório e carregou esse código, que só atua na montagem, depois do ponto de captura. A reprodução com o módulo base é idêntica à 013, o que valida a captura.

- **Supressões que já funcionavam**: todas mantidas nos 9 corpora (as contagens da base estão contidas nas da candidata), incluindo V7 "Página N de M", V3 "Página NNN", cabeçalhos corporativos de V6/V8 e "Controle de página" vetado em V3/V4/V5.
- **Linhas com números e identificadores**: em V3–V9 a regra nova **não confirma nenhum grupo**. Os grupos com número que ela examinou foram rejeitados por evidência explícita:
  - **V4**: "Página NNN | OCR de texto: … - repetição N" (contador inconsistente); "Controle pareado N" (páginas não consecutivas).
  - **V4, títulos "Página 037 | Texto nativo contínuo multipágina - parte N" (p37–p40)**: o número acompanha a paginação, as páginas são consecutivas, a posição e o estilo são estáveis, mas **a proeminência rejeita**: o título é maior que o corpo. É um caso real, **não usado no desenho da regra**, de título numerado por página, o mesmo que motivou essa evidência nos testes negativos.
  - **V5**: itens de lista numerados (1 página cada).
- **Suíte completa** (executada, sem OCR concorrente): **3512 aprovados, 13 falhas, 4 ignorados, 4 xfail**. As 13 falhas são as pré-existentes de T3-P14 (`test_line_detector.py` ×8, `test_no_timeouts.py`, `test_evaluation_exact_optimizations.py` V3, `test_rapidocr_improvement_005/006/008`) e **falham igualmente com o módulo original** (verificado com uma cópia de `src/` contendo o `repeated_regions.py` original).

#### Riscos residuais

- **Seção por página em OCR**: título numerado "Seção N" na faixa de borda, uma seção por página, em páginas só-OCR (sem tamanho de fonte), passa por todas as evidências, exceto a de proeminência, que fica neutra. Nenhum caso nos 9 corpora.
- **Numeração deslocada, romana ou por seção**: deslocamento constante é coberto por teste unitário; numeração romana não é reconhecida (preservada); numeração por seção ou reiniciada é preservada inteira. Nenhum corpus real tem esses casos: a **generalização para eles se apoia só em testes unitários**.
- **Faixa de borda**: a "Continuação…" está a 0.0027 da altura do limite da faixa; a proteção estrutural (sequência consecutiva) foi verificada com a faixa alargada.
- **Mobília em páginas inclinadas**: permanece (V11 10 páginas, V10 12). Sua remoção depende de T3-P35 (geometria no referencial endireitado), não de afrouxar a posição.
- **Repetição exata anterior**: linhas idênticas e estáveis na borda em ≥ 2 páginas continuam sendo suprimidas, como antes do ciclo (não alterado).

#### Custo de validação

- 9 extrações completas sequenciais para captura (V11 590 s, V10 593 s, V7, V8, V3, V6, V9, V4, V5; total na tabela de controles), uma vez cada; **nenhuma extração repetida** para a candidata: todas as comparações base × candidata foram reproduções offline (≈ 10–60 s por corpus).
- As capturas (`assemble_input.pkl` por corpus, scratchpad) permitem validar offline qualquer mudança futura **posterior ao OCR** (montagem, mobília, pós-processamento) sobre o contexto documental completo, sem OCR. Elas valem enquanto o código anterior à montagem não mudar.
- A p71 de T3-P44 foi capturada dentro da extração do V11 (custo zero adicional).

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

### T3-P06 | ALTO | Headings — PARCIAL: CONFIRMADO NA RUN 012 (t3, 3/5)

- Na 003: `heading_level_match_rate`=0.000 em V3 e V4; `heading_recall` V4=0.335, V9=0.351, V10=0.295; `heading_text_cer` V4=0.631, V9=0.667, V10=0.826.
- Parte do V9/V10 vinha de títulos de coluna fundidos (T3-P10). No escopo pontual do V10, a precisão de headings subiu (0.833 → 0.882), mas `heading_level_match_rate` caiu (0.967 → 0.933). Reavaliar na 004.
- **Reavaliação na RUN 011 (§1.24, sem extração)**: o problema está concentrado nas **regiões OCR**. Títulos do gabarito detectados, por tipo de página (classificação pelo `complexity_reasons` do manifesto de execução):

| Corpus | Nativas | Mistas (imagem embutida) | Escaneadas | Documento (P / R) |
|---|---|---|---|---|
| V4 | 56 de 85 | 6 de 8 | **0 de 92** | 1.000 / 0.335 |
| V9 | 36 de 38 (31 FP) | **17 de 98** (14 FP) | **0 de 15** | 0.541 / 0.351 |
| V10 | 86 de 92 (26 FP) | **0 de 33** | **2 de 170** | 0.772 / 0.298 |

  - Exemplos: V4 p83, "OCR denso scan clean 200 - instância A" (`###` no gabarito) sai como parágrafo; V10 p52 (L05, nivelada), o título da página e os títulos de coluna ("Secretaria Regional de Controle — Prestação de contas") saem como parágrafos.
  - Antes de qualquer tentativa (Nível 0): separar títulos ausentes que estão na saída como parágrafo de títulos ausentes como texto; verificar se os títulos `## Página NNN | …` do gabarito V4 existem no PDF (lição de T3-P40); medir a razão de altura título/corpo nas linhas OCR niveladas frente ao limiar de 1.28 da classificação.
  - **Prioridade**: recomendado como 1º problema do próximo ciclo (§1.24), começando por texto nivelado.
- **Ciclo pós-011 (§1.25)**:
  - **Causa raiz 1 (demonstrada)**: `layout/heading.py::assign_heading_levels` escolhe o caminho por documento; com qualquer página nativa, as regiões TITLE só-OCR são julgadas pelo critério nativo, que lê só `native_lines`, recebem score 0 e são rebaixadas. O layout OCR já as classificava corretamente.
  - **Causa raiz 2**: nas páginas mistas do V9, o texto OCR fica em regiões FIGURE, que o layout OCR não reclassifica (fora desta proposta).
  - Tentativas: t1 aprovada nos Níveis 1–3; t2 rejeitada no Nível 4 (perda de conteúdo); **t3 (candidata final)**: sem perda de conteúdo, inclinadas idênticas, G′ independente com F1 de títulos 0.26 → 0.77 e 10 falsos positivos residuais nas 71 páginas avaliadas.
  - **Nenhuma alteração no repositório**: a correção exige `layout/heading.py` (compartilhado) e fica para aprovação.
  - **Tentativas: 3 / 5.**
- **Implementação autorizada (§1.27)**: t3 aplicada com o critério por proveniência da região (também em documentos só-OCR) e com a correção de `ocr_lines` na fusão de fragmentos de título. É equivalente à candidata (22/22 itens offline; Markdown idêntico byte a byte no Nível 2). V5 e V7 inteiros saem idênticos à RUN 011; a suíte completa não tem falha nova. **Status: CORRIGIDO — VALIDADO PONTUALMENTE**, não commitado; aguarda RUN 012.
- **RUN 012 (§1.28, commit `18ccfb0`)**:
  - **Confirmado**: recall V4 0.335 → 0.562, V9 0.351 → 0.457, V10 0.298 → 0.634 (critérios ≥ 0.45 / ≥ 0.351 / ≥ 0.55); precisão V10 0.835 e V9 0.575 (≥ 0.70 / ≥ 0.50); 154 títulos verdadeiros novos; nativas idênticas byte a byte; decisões de título idênticas às da candidata nas 77 páginas comparáveis; nenhuma perda de conteúdo ou de dados.
  - **Não atendido**: precisão do V4 **0.929** (< 0.95; 8 falsos oficiais, 7 deles separadores danificados ou linhas de tabela) e "nenhuma categoria nova de falso título" (linha de tabela raster não detectada: V9 p76 ×5, V4 p157; cabeçalho corrido V4 p110; linha ilegível V4 p104). 28 falsos reais no total (25 oficiais + 3 contados como acerto pelo avaliador).
  - **Hierarquia**: os níveis OCR são agrupados no documento inteiro (vão de 0.12, até 6 grupos); na rodada completa os títulos de coluna caem no `######` (V10: 65 de 73 `###` do gabarito). `heading_level_match_rate` V10 0.977 → 0.519, V9 0.925 → 0.768. Nos subconjuntos da validação pontual eles saíam com `###`/`####`: o efeito não era visível sem o documento inteiro.
  - **Regressões novas não cobertas pela validação pontual**: V4 p95/p96 (título unido à linha "Controle de página"), p145/p149 (linhas de tabela não detectada unidas num parágrafo), p104 (CER 0.528 → 0.546). Previstas e confirmadas: p118 (parágrafos) e p100 ("R$" | "14.057,00").
  - **Status**: PARCIAL — CONFIRMADO NA RUN 012. Não reverter. Próxima tentativa (t4, 4/5) recomendada depois de T3-P35 (§1.30), com escopo em hierarquia por página e nas regressões; exige nova autorização para `layout/heading.py`.

### T3-P07 | MÉDIO | V5: tabelas sem borda e ordem embaralhada — PENDENTE

- **Reavaliação pós-009**:
  - As 12 páginas com recall < 0.9 (p4, 5, 6, 17, 18, 20, 23, 26, 28, 32, 36, 38) perdem exatamente o **título H1 e o subtítulo** da página. As tabelas dessas páginas estão completas (22/22 tabelas e 530/530 células exatas no V5). Esse mecanismo passa a **T3-P40**.
  - Em T3-P07 fica a ordem: p3 (CER 0.27) e p30 (0.35), com recall 1.0.

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

### T3-P20 | MÉDIO | Parágrafos OCR quebrados no meio da frase — PARCIAL: CONFIRMADO NA RUN 009 (5/5, encerrado; regressão em T3-P38)

- **RUN 009 (t2 + t5, rodada completa; §1.15)**:
  - `paragraph_detection_f1` V4 0.378 → 0.410, V9 0.525 → 0.595, V10 0.409 → 0.458. Todos os critérios da §5.0c foram atendidos.
  - 63 páginas mudaram **só a segmentação**: a sequência de palavras é idêntica byte a byte, e títulos, listas, tabelas, CER, WER, recall e dados críticos ficaram iguais.
  - Quebras indevidas: V4 392 → 271, V9 142 → 80, V10 268 → 213.
  - **Fusões novas: V4 +19** em 10 páginas OCR densas de coluna única (p83, 85, 99, 125, 129, 137, 138, 139, 141, 143) e V10 +1 (p68, inclinada). Registradas como **T3-P38**.
  - V4 p140 (controle): 140.01–140.03 continuam separados.
  - A atribuição das fusões a t2 ou t5 não foi feita (exige extração pontual).

- Exemplo da V9 p2 na 003: "A divergência ¶ encontrada exige…". V9 `paragraph_detection_precision` 0.496 → 0.423 na 003; V10 0.153.
- **Efeito das correções pós-003**: no escopo pontual, `paragraph_detection_f1` V9 0.305 → 0.332, V10 0.134 → 0.314, V3 0.372 → 0.629. Algumas páginas pioraram individualmente (V9 p31 0.533 → 0.320, p67 0.500 → 0.240).
- **Hipótese**: `segment_ocr_paragraphs` (regras de vão, recuo e linha curta) com linhas reais de coluna.
- **Diagnóstico pós-008** (008, V4/V9/V10; alinhamento por palavras entre parágrafos do gabarito e da saída, e rastreamento das regras de `segment_ocr_paragraphs` em páginas reais):
  - Classificação dos erros (79 páginas): **quebra incorreta** é o erro dominante (V4 46, V9 29, V10 117 parágrafos do gabarito partidos); **fusão** V4 7, V9 2, V10 26; **mistura de colunas** 1 por corpus; **perda** e **duplicação** de palavras não dependem da segmentação (idênticas com e sem a correção).
  - Causa raiz (V10 p11): a regra 1 (vão vertical) usa `1.4 × vão mediano`. Com linhas a ~2.2 pt, o limite é ~3.1 pt, dentro da **variação das caixas OCR**: uma linha sem ascendentes/descendentes tem caixa de 5.2–5.5 pt contra 6.5 pt das demais, e o vão aparente entre linhas do mesmo parágrafo oscila entre 2.0 e 3.3 pt. As quebras reais têm 10.5–11 pt. Resultado típico: a 1ª ou 2ª linha do parágrafo vira parágrafo à parte ("…examinou a ¶ verificação de empenhos…").
- **Tentativas** (79 páginas V3–V10 — V10 27 raster incl. L01/L02, V9 19, V4 15 OCR de texto/duas colunas, V3 6, V5/V6/V8 controles — com a correção ligada e desligada em memória):

| # | Hipótese | Alteração | Resultado | Decisão |
|---|---|---|---|---|
| 1 | O vão que separa parágrafos deve exceder a variação da caixa (~⅓ da linha) | limite relativo `max(1.4·m, m + 0.35·h)` (o limite absoluto `0.75·h` não muda) | escopo `paragraph_detection_f1` V4 0.071 → 0.118, V9 0.416 → 0.540, V10 0.334 → 0.411; quebras 192 → 121, mas **fusões 35 → 47** (V4 p140: parágrafos 140.01–140.03 fundidos — vão entre parágrafos de 3.5 pt com linhas a ~1 pt) e V10 p18/p25 pioraram | rejeitada (fusões) |
| 2 | A variação medida é a profundidade de uma descendente (~1.1 pt em linhas de 6.3 pt ≈ 0.17·h) | `_GAP_JITTER_RATIO = 0.2`: limite `max(1.4·m, m + 0.2·h)` | escopo V4 0.071 → **0.133**, V9 0.416 → **0.475**, V10 0.334 → **0.370**; quebras 192 → 151; **fusões, mistura, perda e duplicação inalteradas**; nenhuma página piorou; CER, WER, recall, precisão textual, `numeric`, listas e títulos idênticos em todas as 79 páginas | **mantida** |

- **Escopo**: `ocr/reconstruct.py::segment_ocr_paragraphs` — só regiões OCR (Tesseract e demais motores OCR que usam o montador); páginas nativas não passam por ela (V5–V8 nativas idênticas).
- **Restante**: quebras por recuo e por linha curta (regras 2–3) e títulos de coluna fundidos (T3-P31). V10 continua com ~2× parágrafos do gabarito nas páginas inclinadas.
- **Ciclo seguinte (após `b1202ee`, 2026-11-17)** — atribuição de cada decisão errada a uma regra (`segment_ocr_paragraphs` instrumentada em 28 páginas de V4/V9/V10, cada par de linhas julgado contra o gabarito):
  - **Quebras falsas por regra**: R1 (vão) V10 16, V9 11; R0 (mudança de fonte) V10 7, V4 5, V9 1; R3 (linha curta) V10 4 (todas em L01/L02 inclinadas); R2 1. **Continuações falsas (fusões)**: V10 16, V9 3 — quase todas em páginas inclinadas (caixas infladas pela inclinação), já existentes antes do ciclo.
  - **R1, dois grupos**: (a) vão de 3.7–5.2 pt contra limite de 3.4–5.0 pt, sempre com a **linha de cima mais baixa** que a típica (4.7 contra 6.0 pt; 6.2–6.7 contra 8.0 pt) — linha sem descendentes, cuja base aparente sobe; (b) vão de 11–14 pt: quebra visual real na imagem que o gabarito trata como um parágrafo — não corrigível por geometria sem criar fusões.
  - **R0**: duas linhas de largura semelhante cujas caixas diferem por 1.30–1.39× (linha só com altura x contra linha com acentos e descendentes) disparam a "mudança de fonte".
  - **Mistura de colunas, perda e duplicação**: 1 mistura por corpus, perda e duplicação de palavras **idênticas** com e sem qualquer tentativa — não dependem da segmentação (vêm da leitura OCR e da ordem de leitura).

| # | Hipótese | Alteração | Resultado (81 páginas: V3 6, V4 15, V5 2, V6 2, V7 2, V8 2, V9 19, V10 29; base = `b1202ee` no mesmo contexto) | Decisão |
|---|---|---|---|---|
| H4 (simulação, sem código) | R0 só com a linha mais alta também 1.3× acima da altura típica do bloco | — | elimina 12/13 quebras falsas de R0, mas fundiria **9 separações reais título/texto** ("Coluna esquerda" + "Parágrafo 140.01"; "cadastrais" + "Processo:") — nos blocos pequenos o título infla a altura típica | descartada antes de implementar (não conta como tentativa) |
| 3 | Uma caixa mais baixa que a típica perdeu a descendente; o vão deve ser medido de onde a descendente terminaria (no máximo 0.2·h) | `missing_descender` na regra 1 | V9 0.475 → 0.528, V10 0.387 → 0.428, V4 0.133 → 0.135; quebras V10 98 → 77, V9 24 → 17; mas **V9 p17 0.353 → 0.125** e **V10 p25 0.345 → 0.276** (fusões +1 em V9 e V10): na página inclinada a linha "Processo: …" (caixa 14.3 contra 19.7 pt) foi estendida e fundida ao parágrafo seguinte | rejeitada |
| 4 | Linhas estreitas (fim de parágrafo) não recebem a correção | correção só se a linha tem ≥ 0.6 da largura mediana | **resultado idêntico à tentativa 3** (a linha da p17 tem 0.72 da largura) — causa atribuída errada | rejeitada |
| 5 | Em página inclinada a altura da caixa acompanha a largura, não as descendentes; a correção só vale em texto nivelado, reconhecido pela caixa típica ≤ 1.5× o corpo do texto estimado pelo avanço por caractere (≈ ½ em; nivelado ≈ 0.9–1.0, inclinado ≈ 2.0) | `_LEVEL_BOX_MAX_RATIO = 1.5`, `level_text` em `segment_ocr_paragraphs` | V9 0.475 → **0.540**, V10 0.387 → **0.431**, V4 0.133 → 0.135; quebras V10 98 → 76, V9 24 → 17, V4 29 → 27; **fusões, mistura, perda e duplicação iguais à base**; V9 p17 e V10 p25 idênticas; **nenhuma página piorou**; CER, WER, recall, precisão textual, `numeric`/`currency`/`dates`, tabelas, listas e títulos idênticos nas 81 páginas | **mantida** |

- **Páginas que melhoraram na tentativa 5**: V9 p8 0.455 → 0.571, p31 0.500 → 0.778, p67 0.300 → 0.857, p84 0.353 → 0.500; V10 p6, p8, p19, p21, p24, p32 (0.364 → 0.476), p40, p46, p50, p52, p56, p63, p66, p78, p84 (0.440 → 0.553); V4 p116, p118, p130.
- **Preservado**: T3-P34 (V9 p58 e as tabelas giradas não mudam), T3-P22 (V4 p20/p25/p26/p28, V10 p54/p59) e T3-P35 (L04–L06) — idênticos à base em texto, ordem e tabelas.
- **Restante (sem tentativas)**: quebras visuais reais que o gabarito une (vão 11–14 pt), R0 por variação de caixa (exige estimar o corpo do texto por linha), e as páginas inclinadas L01/L02 (T3-P31/T3-P18).
- **Testes** (acréscimos): linha sem descendentes não abre parágrafo (geometria da V10 p32; falha no código de `b1202ee`); caixas infladas pela inclinação não emprestam descendente através de um vão real (geometria da V9 p17; falha com a tentativa 3).
- **Testes**: `tests/test_tesseract_run008_regressions.py` — variação de caixa não parte o parágrafo (geometria da V10 p11); vão apertado entre parágrafos continua separando (geometria da V4 p140; falha com 0.35).

### T3-P21 | ALTO | V4: recall ≈ 0.85 em OCR de texto, inclusive em digitalização limpa — ABERTA (diagnóstico §1.31)

- O bug do TSV (T3-P23) explica só uma parte (V4 p81 0.869 → 0.880; p85 0.867 → 0.875).
- **Hipótese**: diferença entre a projeção de cobertura e o CER (por exemplo, marcação no gabarito, como `## Página …` e `**Controle de página:**`). Investigar no avaliador sem alterá-lo.
- **Diagnóstico pós-012 (§1.31)**: a hipótese de marcação do gabarito não se confirma. O piso vem do **reconhecimento** dos segmentos adversariais do corpus (IDs `O0I1l-rnm`, caminhos com `\` e `_`, sinais `x < 7, y > 2, A & B`, lista de confundíveis): 82% dos tokens ausentes; palavras comuns perdem só 4.3%. Configuração atual: `por`, `--oem 1`, `--psm 3`, perfil `default`, 288 dpi (rasters de origem 72–300 dpi; mais DPI não acrescenta informação). Pós-OCR possível: H-esp (`_needs_space` cola tokens sobrepostos em fronteira não alfanumérica; ≈ 531 números falsos `0.1`/`1.1`), não verificada. Experimentos propostos (exigem OCR e autorização): TSV da p81; perfil `tesseract-degraded-scan-v1` (CLAHE) em sombra e ruído.

### T3-P22 | ALTO | Colunas fundidas em páginas **nativas** multicoluna — CORRIGIDO — CONFIRMADO NA RUN 008 (5/5 tentativas; parcial)

- **RUN 005**: V4 p25/p26/p28 (texto nativo em 3 colunas; 11% das edições do V4); V9 p5, p43, p51 (17% do V9); V10 p9 (0.69) e p59 (0.26) (10% do V10). Recall 1.0: o texto está completo e só a ordem está errada. As páginas nativas de 2 colunas do V4 estão corretas (0.008).
- **Diagnóstico (V4 p25)**: as linhas nativas **estão** separadas por coluna (`_split_groups_by_column_gap`: 156 linhas, só o título largo). O defeito está no **layout**: uma região TEXT ocupa quase a página toda ([29,57,566,542], 111 linhas) e ordena as linhas por altura, intercalando as colunas.
- **Componente**: layout de regiões e ordem de leitura de páginas nativas (`text/reading_order.py`, compartilhado). Requer alteração fora do Tesseract, com validação em V5–V8. É a próxima prioridade.

- V10 p9 (`mixed`, camada de texto nativa, L03 + N04): CER 0.689, sem mudança, com o mesmo padrão de linhas intercaladas.
- Outras L03 nativas estão corretas (p23, p39, p73, p79 ≈ 0.07); p59 tem 0.263.
- O componente é a montagem de linhas nativas (compartilhada, ligada a T3-P05). Requer autorização.

- **Ciclo pré-007 (2026-11-16) — autorizado a alterar componentes compartilhados só para T3-P22.**
- **Diagnóstico confirmado por etapa** (V10 p9): (1–2) linhas nativas **corretas**, cada uma dentro da sua coluna (127 linhas; só cabeçalho, título e "Publicação" largos) — não é segmentação; (4) o layout forma **uma** região TEXT com as duas faixas (3 colunas em cima, 2 embaixo; 116 linhas), enquanto nas páginas de controle p23/39/73/79 forma duas; (5) a ordenação da região (`text/reading_order.py::_order_prose_lines_with_decision`) decide **FORM** e lê linha a linha — **é aqui que as colunas se misturam**; (6–7) blocos e Markdown só herdam a ordem.
- **T3-P22 reúne mecanismos distintos** (dados intermediários):

| Mecanismo | Páginas | Etapa |
|---|---|---|
| Calha ao lado de coluna alinhada à esquerda e irregular à direita não detectada (agrupamento das lacunas pelo **centro**, tolerância 12 pt; os centros variam 15 pt) | V10 p59 (faixa de cima com 2 pistas em vez de 3), V4 p25 | ordenação |
| Região com faixas de número de colunas diferente: nenhuma calha persiste na altura toda → FORM/FALLBACK | V10 p9 | ordenação (a região única vem do layout `_body_bands`, que não corta nos títulos da faixa de baixo porque o texto de cima termina a < 3 alturas de linha deles) |
| Trechos de prosa de uma coluna classificados como **carimbo** (`layout/decorative.py::_looks_like_semantic_status`, ramo "contraste escuro + ≥ 3 linhas" em fonte do corpo) viram regiões separadas e são posicionados fora do lugar | V4 p25 (parágrafos 51/52), p26, p28 | layout (semântico) |
| Três colunas detectadas como tabela sem borda | V9 p51 | detector de tabelas |
| Títulos de colunas vizinhas fundidos numa linha ("Antecedentes Resultados") | V9 p5, p43 | montagem de linhas nativas (T3-P05) |

- **Tentativas**:

| # | Hipótese | Alteração | Resultado (62 páginas de V3–V10) | Decisão |
|---|---|---|---|---|
| 1 | Lacunas da mesma calha devem ser agrupadas pela **sobreposição** (núcleo vazio comum), não pelo centro | `_detect_persistent_gutters` + `_ragged_column_gutters` (aditivo: calhas já encontradas não mudam; mesmo limiar de persistência) | **V10 p59 CER 0.263 → 0.069** (`paragraph_f1` 0.625 → 0.914); **V4 p25 0.336 → 0.222**; V10 p62 (OCR inclinada) 0.468 → 0.251; demais 59 páginas idênticas | **mantida** |
| 2 | Região sem colunas na altura toda pode ter faixas com estruturas diferentes: cortar nos vãos verticais livres (≥ 2 alturas de linha) e decidir cada faixa com o mesmo detector | `_order_horizontal_slabs` + `allow_slabs` em `_order_prose_lines_with_decision`; só age se a região **não** foi decidida multicoluna e se **≥ 2 faixas** forem decididamente multicoluna (com 1 só, pares chave/valor viravam colunas — pego pelo teste existente `test_table_lines_do_not_drive_mixed_prose_to_form_flow`) | **V10 p9 CER 0.689 → 0.088** (`paragraph_f1` 0.095 → 0.941); demais 61 páginas idênticas | **mantida** |

- **Ordem semântica verificada**: V10 p9, p59 e p62 com os procedimentos 01→05 na ordem do gabarito; V4 p25 com as três colunas da região principal em ordem (40–42, 50, 60–62), restando 51/52 (regiões "carimbo") no fim.
- **Censo do ramo de status semântico** (modo nativo, só layout, sem gravar saída): 110 agrupamentos em V3–V10, **apenas 1 com fonte acima do corpo** (V5 "Seção 2 - Análise"); os demais são texto de corpo (prosa do V4/V9, listas do V10, código do V6, metadados do V7). Indica que o ramo "contraste + fragmentado" gera falsos positivos em todos os corpora — **próxima hipótese** para V4 p25/p26/p28 (não implementada: ciclo encerrado a pedido antes da RUN 007).
- **Arquivos**: `src/structured_pdf_text/text/reading_order.py` (compartilhado: consumidores `assemble/content.py`, `assemble/page.py`, `renderers/markdown.py`; nativo e regiões só-OCR de qualquer motor); `tests/test_native_multicolumn_order.py` (novo, 9 testes; os de detecção e ordem falham sem a correção).
- **Testes**: `test_native_multicolumn_order.py`, `test_priority2_reading_order.py`, `test_content_assembly.py`, `test_final_review_blockers.py`, `test_ocr_parser_regressions.py`, `test_easyocr_figures_and_policy_regressions.py` — 218 passaram. Suíte completa **não executada** neste ciclo.
- **Riscos**: a mudança vale para regiões nativas e só-OCR de todos os motores; nas 62 páginas só mudaram V10 p9/p59/p62 e V4 p25. Variante não coberta: região já decidida multicoluna cuja faixa de baixo tem outra estrutura (teste sintético mostra intercalação na faixa de baixo; comportamento anterior, não alterado).
- **Status**: CORRIGIDO — VALIDADO PONTUALMENTE (V10 p9/p59; V4 p25 parcial). Pendente de validação integral na RUN 007.

- **RUN 007**: tentativas 1–2 confirmadas na rodada completa (V10 p9 0.088, p59 0.069, p62 0.251; V4 p25 0.222; V3, V5–V8 idênticos à 006). **Regressão**: V10 p54 0.222 → 0.231 (ver tentativa 3).
- **Ciclo pós-007 (2026-11-17)** — tentativas 3 a 5 (total: 5 de 5; **encerrado**):

| # | Hipótese | Alteração | Resultado | Decisão |
|---|---|---|---|---|
| 3 | A calha "irregular" da tentativa 1 só vale para colunas de **prosa**; células curtas de tabela também têm borda direita variável, mas formam linhas que devem ficar juntas (V10 p54: "R$ 850,16" … "R$ 10.645,60", tabela sem borda não detectada) | `reading_order._ends_with_prose_line` (linha que delimita a lacuna com ≥ 20 caracteres visíveis); só lacunas assim sustentam `_ragged_column_gutters` | V10 p54 0.231 → **0.222** (= 006; títulos 0.667 → 1.000); V10 p9/p59/p62 e V4 p25 mantidos; diferenças em V3 p1, V4 p25/28/30/150 e V6 p3 provadas de contexto de subconjunto (saída idêntica com a tentativa desligada) | **mantida** |
| 4 | O ramo "contraste escuro + ≥ 3 linhas" de `_looks_like_semantic_status` classifica prosa comum como carimbo (censo: 109 agrupamentos em 94 páginas de V3–V10, só 1 com fonte acima do corpo); exigir fonte acima do corpo | `layout/decorative.py`: `return font_above_body and (contrast or fragmented)` | ganhos: V4 p25 0.222 → 0.010, p26 0.272 → 0.000, p28 0.260 → 0.010, p20 0.061 → 0.000; V9 p43 0.470 → 0.000, p12, p19; tabelas V9 p72/p90 e V10 p54 (detectada, células 0 → 0.881). **Regressões**: V9 p3/p20/p71 (tabela girada intacta, posicionada antes dos parágrafos: 0.00 → 0.18); V10 p57 (lista de verificação reposicionada); V5 p4/p18/p23/p38 (+0.05–0.07; listas/títulos); **V9 p5** (linhas de duas colunas intercaladas num parágrafo; tabela nativa vira texto). Causa: parágrafos isolados ao lado de tabelas voltam à região de corpo, que passa a envolver a tabela | apresentada ao usuário; substituída pela 5 (decisão do usuário) |
| 5 | Só o trecho que **continua uma coluna** de texto deixa de ser carimbo; blocos isolados (parágrafo ao lado de tabela, lista de verificação) mantêm a classificação | regra original restaurada + `decorative._continues_text_column` (linha fora do grupo, mesma borda esquerda, largura ≤ 1.3×, a ≤ 1.6 altura de linha acima ou abaixo) | **V4 p25 0.222 → 0.010, p26 0.272 → 0.000, p28 0.260 → 0.010, p20 0.061 → 0.000; V9 p90 células 0.146 → 0.896; V10 p54 0.222 → 0.095** (tabela detectada, células 0 → 0.881); V3, V5, V6, V7 e V9 p3/p5/p20/p71, V10 p57 **idênticos**; ganhos da 4 em V9 p12/p19/p43/p72 não mantidos | **mantida** |

  Páginas da medição das tentativas 4–5 (linha de base e tentativa no mesmo subconjunto, T3-P35 desligado no processo de teste): V3 15, 17, 18, 24; V4 20, 25, 26, 28; V5 2, 4, 5, 7, 17, 18, 23, 26, 38; V6 2, 9, 19, 29, 40, 94; V7 3, 16, 34, 40, 72, 78; V9 3, 5, 12, 19, 20, 43, 55, 71, 72, 90; V10 1, 10, 14, 30, 36, 44, 54, 57, 64 (páginas com agrupamentos "status" pelo censo). Tentativa 3: V3 1/20/96/116; V4 20/25/26/28/30/82/107/150; V5 4/10/17/23/30; V6 3/21/74; V7 1/24/25; V8 4/46/58; V9 4/5/8/20/43/51/55/62/71/72; V10 1/5/6/9/11/13/18/21/23/28/30/36/39/41/44/48/50/52/54/59/62/73/79.
- **Ponto menor para avaliação**: na tentativa 5, a precisão de itens de lista do escopo V9 cai 1.00 → 0.75 (vem da p90, cuja tabela foi recuperada).
- **Testes**: `tests/test_native_multicolumn_order.py` (+3: calha entre células curtas não aceita e a mesma geometria com prosa aceita; trecho de coluna não é carimbo e bloco isolado continua sendo — falham sem as correções).
- **RUN 009**: correções mantidas (V10 p9/p54/p59, V4 p20/p25/p26/p28, V9 p90 idênticas à 008). A V9 p72 (duas tabelas nativas lado a lado) passa a ser acompanhada em **T3-P37**.
- **Restante de T3-P22 (sem tentativas)**: V9 p51 (três colunas lidas como tabela), V9 p5/p43 (títulos de colunas fundidos, T3-P05), V9 p43/p12/p19 (blocos isolados de coluna classificados como "status" — a tentativa 4 os corrigia com efeitos colaterais).
- **Status**: CORRIGIDO — VALIDADO PONTUALMENTE (V10 p9/p59/p54, V4 p20/p25/p26/p28, V9 p90). Pendente de validação integral na RUN 008; tentativas esgotadas.

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

### T3-P31 | MÉDIO | Títulos de coluna com travessão viram item de lista em páginas endireitadas — NÃO RESOLVIDO (10 tentativas, encerradas)

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

### T3-P32 | ALTO | Tabela raster girada 90°/270° com a grade transposta — RESOLVIDO na 007 (p20/p55/p71); p36/p92 pendentes — ENCERRADO (5/5)

- **Evidência (006)**: no V9, das 6 tabelas raster giradas (manifesto `rotation_degrees` ±90/270), só p3 sai correta. p20 e p71 saem com o **texto certo e a grade transposta e invertida** (primeira linha "004 | 003 | 002 | 001 | Rubrica"; `cell_content_exact_match` 0.042 e 0.000; os valores monetários ficam em células erradas). p36 e p92 saem ilegíveis; p55 mistura as duas coisas.
- **Gravidade**: dados críticos (valores R$, quantidades) associados à linha errada ou perdidos em tabela; mecanismo genérico de qualquer documento com tabela girada.
- **Causa raiz (CONFIRMADA por rastreamento)**: o Tesseract (PSM 3) também lê texto vertical, e o faz com confiança alta. No recorte da tabela da p20 a leitura direta tem 24 palavras com confiança média 95, todas com caixa mais alta que larga. O retry de quarto de volta (T3-P02) só disparava com confiança < 60 ou com ≥ 60% das **linhas de 3+ palavras** verticais; células de tabela têm 1–2 palavras por linha, então nenhum gatilho valia. Os tokens ficavam com `rotation = 0` e o construtor de tabelas montava a grade no referencial da página. Na p3 a leitura direta tinha confiança 45, o retry disparava, os tokens recebiam `rotation = 270` e a tabela saía certa.
- **Tentativas**:

| # | Hipótese | Alteração | Resultado (54 páginas, 6 corpora) | Decisão |
|---|---|---|---|---|
| 1 | A forma das palavras (≥ 3 alfanuméricos, altura ≥ 1.2 × largura) indica leitura vertical também em linhas curtas | `_tsv_vertical_word_ratio`/`_tsv_reads_vertically`; usado no gatilho **e** como filtro dos candidatos girados | p20 0.042 → 0.950, p71 0.000 → 1.000; **regressão** V8 p4/p58 ("L-01" → "1-01", `numeric` 1.000 → 0.909) | ajustar |
| 2 | Leitura vertical confiante pode ter o texto certo em qualquer orientação; o formato deve **ordenar** os candidatos, não eliminá-los | candidatos ≥ 70 ordenados por (palavras horizontais, confiança); quando o retry foi disparado só pelas palavras verticais, só uma leitura horizontal substitui a original | p20/p71 mantidos; V8 volta a "L-01"; V3, V4, V6, V8, V10 idênticos ao ANTES | mantida (commit `f03710a`); regressão numérica em V9 p55 investigada nas tentativas 3–4 |
| 3 | Orientação e texto são separáveis: a leitura horizontal fixa a orientação; uma leitura **vertical** do mesmo recorte, ≥ 5 pontos mais confiante e com as mesmas palavras nas mesmas posições (IoU ≥ 0.3 em ≥ 60% das palavras, nos dois sentidos), fornece o texto, com as caixas levadas ao referencial da orientação escolhida por transformação exata de quarto de volta | `_quarter_turn_point`, `_retarget_tsv`, `_tsv_word_boxes`, `_same_word_ratio`, `_vertical_reading_in_frame` (substitui a leitura inteira) | V9 p55 `numeric` 0.919 → **1.000** e identificadores 01–04 nas linhas certas; **mas a coluna "Situação" some da saída** (recall 0.968 → 0.959) | ajustar |
| 4 | A leitura vertical agrupa a tabela girada por linhas da tabela; com a grade visual parcial (2 de 3 colunas), a palavra fora da grade é descartada junto com a linha reivindicada pela tabela. A leitura vertical deve fornecer **só o reconhecimento, palavra a palavra**, mantendo a segmentação (caixas, linhas, blocos) da leitura horizontal | `_adopt_better_word_texts`: pares por melhor sobreposição mútua; cada palavra horizontal recebe texto e confiança da vertical mais confiante; palavras sem par ficam; nenhuma palavra é acrescentada | V9 p55: `numeric` **1.000**, recall **0.982**, precisão **0.995**, `cell_content_exact_match` **0.841**, `cell_cer` **0.168**; p20/p71 mantidas; demais páginas idênticas | **mantida** |

  **Diagnóstico da regressão de p55 (antes das tentativas 3–4)**, comparando tokens, coordenadas, orientação e confiança das leituras do recorte da tabela (região, escala 4.0, 860 × 975 px): direta 53 (vertical); 90° **horizontal, 84** — "Ação Prazo **oi** | 18/09/2026 **=** 21/09/2026 **pm** …"; 180° **vertical, 92** — "Situação Ação Prazo **01** | 18/09/2026 Regular **02** | 21/09/2026 Revisão **03** …"; 270° 47 (lixo). O mesmo padrão aparece na escala 6.0 (75 × 87) e no recorte menor (76 × 85). A **escolha da orientação** (90°) estava certa e a **transformação de coordenadas** também; o defeito é de **reconhecimento**: a leitura na orientação correta reconhece pior dígitos curtos em células. A regra anterior à 006 escolhia a leitura vertical (números certos, grade transposta); a tentativa 2 escolhe a horizontal (grade certa, números errados). A **montagem da tabela** não regrediu: a coluna "Situação" já ficava fora da grade na 005/006 (a grade visual detectada tem 2 das 3 colunas, limitação anterior do detector compartilhado).

  A regressão do V8 p4 da tentativa 1 foi explicada: o recorte de uma linha da tabela tem confiança 56 ("-01"); a leitura a 90° é vertical, mas com o texto certo ("L-01", 82), e era aceita antes. Na tentativa 2 ela continua aceita, porque não há candidato horizontal com confiança ≥ 70.
- **Correção** (`ocr/backends/tesseract.py`, só Tesseract): `_VERTICAL_WORD_MIN_CHARS = 3`, `_VERTICAL_WORD_MIN_ASPECT = 1.2`, `_tsv_vertical_word_ratio`, `_tsv_reads_vertically`; `_retry_quarter_turns` com gatilho pelas palavras e preferência por leitura horizontal (tentativas 1–2, commit `f03710a`); `_VERTICAL_TEXT_MIN_GAIN = 5`, `_SAME_WORD_MIN_IOU = 0.3`, `_SAME_WORDS_MIN_RATIO = 0.6`, `_quarter_turn_point`, `_retarget_tsv`, `_vertical_reading_in_frame`, `_adopt_better_word_texts` (tentativas 3–4, não commitadas). Só age quando o retry aceitou uma leitura girada horizontal; nenhuma regra depende de texto ou de página. Nenhum componente compartilhado foi alterado.
- **Validação pontual (ANTES → DEPOIS)**:

| Página | CER | `cell_content_exact_match` | `cell_cer` | Observação |
|---|---|---|---|---|
| V9 p20 | 0.069 → **0.001** | 0.042 → **0.950** | 1.294 → 0.008 | grade correta |
| V9 p71 | 0.068 → **0.000** | 0.000 → **1.000** | 1.372 → 0.000 | recall 0.991 → 1.000 |
| V9 p55 (tentativa 2) | 0.179 → 0.177 | 0.657 → 0.794 | 0.319 → 0.180 | `numeric` 0.974 → 0.919 |
| **V9 p55 (tentativa 4)** | 0.179 → 0.177 | 0.657 → **0.841** | 0.319 → **0.168** | `numeric` 0.974 → **1.000**; recall 0.977 → 0.982; precisão 0.986 → 0.995 |
| V9 p3 (controle girado) | 0.026 | 1.000 | 0.000 | idêntica |
| Escopo V9 (15 págs.) | 0.149 → 0.139 | 0.350 → 0.479 | 0.747 → 0.592 | `numeric` 0.906 → 0.906 |

- **p55 após a tentativa 4**: tabela "Ação | Prazo" orientada, com os identificadores 01–04 nas linhas dos respectivos prazos. As palavras da coluna "Situação" são preservadas, mas fora da grade, como parágrafos (como na 005/006): a grade visual detectada tem 2 das 3 colunas (detector compartilhado, fora do escopo). `paragraph_detection_f1` da página 0.889 → 0.615 por esses parágrafos soltos (efeito estrutural, sem perda de conteúdo).
- **Validação ampla da tentativa 4** (mesmos subconjuntos das medições anteriores, mesmo contexto): 54 páginas do conjunto principal (V3, V4, V6, V8, V9, V10) e 44 de controle (V4, V5, V6, V7, V8, V9, V10) — **só a V9 p55 muda** em relação ao código de `f03710a`; todas as demais, incluindo V8 p4/p58, V3 p96–p99/p116, V4 p109–p114/p164 e V9 p20/p71, saem idênticas byte a byte. As diferenças de V4 p110–p114/p164 e V10 p5/p13/p48 em relação à 006 no subconjunto da tentativa 3 vêm do contexto do subconjunto (provado: com a troca de texto desligada a saída é idêntica).
- **Não resolvido**: p36 (tabela girada dentro de página raster inclinada; nenhuma leitura de região da tabela é feita) e p92 (tabela girada não detectada). Exigem detectar a região da tabela antes de relê-la.
- **Testes**: `tests/test_tesseract_run006_regressions.py` — (tentativas 3–4) texto melhor da leitura vertical no referencial escolhido, ganho mínimo exigido, posições diferentes não usadas, retry completo mantém a orientação horizontal com o texto melhor, segmentação da leitura horizontal preservada, palavras sem par mantidas e nenhuma acrescentada; (tentativas 1–2) medida das palavras verticais, gatilho em recorte confiante vertical, recorte horizontal não relido, números curtos não disparam, leitura vertical de baixa confiança continua aceitando o candidato vertical (comportamento T3-P02), preferência pela leitura horizontal, retry por palavras não troca vertical por vertical.

- **Verificação pós-008 e tentativa 5 (2026-11-17)**:
  - **Estado na 008 e após T3-P34**: V9 p36 e p92 idênticas à 007 (células exatas 0); as alterações de T3-P34 não as tocam. p20/p55/p71 preservadas (idênticas byte a byte).
  - **Causa restante — distinta da original** (rastreamento por etapa): a original (tentativas 1–4) era a **escolha da orientação** na leitura do recorte. Em p36/p92 **a região e a orientação estão certas**: a grade da tabela girada é detectada (p92 3×5, p36 4×5) e a releitura da região (`_refine_visual_table_ocr`) sai com rotação 90° e o texto correto (p36: 24 palavras, "Rubrica 001…004 Qtde. … R$ 1.514,58 … Aprovado"; re-detecção 5×4 com todas as células certas). O que falha é a **integração** dessa releitura, em barreiras sucessivas: (1) `_preserve_visual_grid` vê a grade transposta (5×3 contra 3×5) como "mais grossa" e mantém a original; (2) a conservação de conteúdo (`table_integration.py`) compara com o texto anterior das células — leitura horizontal de texto vertical ("E S (a[02]MNMoo", "OT'ZOT'SSU") — e acusa "perda de conteúdo ou valor protegido"; (3) a validação usa como linhas de origem as leituras horizontais antigas que atravessam a tabela (`source_assignment_conflicts`); (4) na p36, "Visual table draft extends outside its authorized page or region".
  - **Tentativa 5** (hipótese nova e fundamentada: leituras perpendiculares não são leituras do mesmo texto): grade transposta aceita como equivalente; conservação e linhas de origem ignorando leituras OCR perpendiculares à releitura. Resultado na sonda: (1) e (2) superadas na p92 (releitura aceita, 5×3), mas (3) persistiu e a p36 parou em (4). **Revertida integralmente** (código igual a `ad43cb1` nesses arquivos): seguir exigiria afrouxar em cadeia verificações de autorização e validação do núcleo de integração, sem resultado medido — não é correção segura.
  - **Situação**: 5/5 tentativas, **encerrado**. Nova abordagem só com aprovação; a direção identificada é tratar a releitura girada da região como **substituta** da leitura da página dentro da tabela (no núcleo de integração), com regras explícitas de autorização.

### T3-P33 | MÉDIO | Exceção `Invalid BBox` na estrutura de tabela por trilhas OCR (V9 p36) — PENDENTE

- **Evidência**: único motivo do `partial_success` do V9 na 006: `OCR table structure unavailable: ValueError: Invalid BBox coordinates: BBox(x0=63.2, y0=526.1, x1=237.8, y1=521.3)`. Reproduzido no harness.
- **Causa (CONFIRMADA)**: `text_tracks._build_table` (detector nativo de tabelas sem borda, compartilhado) define as bordas das linhas da tabela pelo ponto médio entre centros consecutivos, supondo centros crescentes. O candidato da p36 é formado por linhas OCR que se sobrepõem verticalmente (alturas 9–32 pt, y0 513–536): o parágrafo da coluna esquerda fundido com o lixo da tabela girada 270°. Uma linha alta tem centro abaixo da seguinte e a borda inverte.
- **Impacto real**: a exceção impediu uma tabela falsa (na 005 o mesmo candidato virou `<table>` com "Os comprovantes de pagamento." nas células). O defeito é de execução/status, não de conteúdo.
- **Não escolhido nesta etapa**: a correção natural é no detector compartilhado (rejeitar candidato cujas linhas não formam uma sequência vertical válida), fora do pipeline Tesseract e do escopo autorizado (não alterar o extrator nativo). A causa da página (tabela girada ilegível) é T3-P32.

### T3-P34 | ALTO | Tabela raster com inclinação pequena em página mista não detectada — PARCIAL: CONFIRMADO NA RUN 009 (V9 p58; 4/5 tentativas)

- **RUN 009 (§1.15)**:
  - V9 p58: tabela 8 × 6 detectada; CER 0.157 → **0.063**; células exatas 0 → **0.646**; `cell_cer` 1.000 → 0.153; `paragraph_detection_f1` 0.444 → 1.000.
  - V9 p44: 0.366 → **0.204**. V9 p49: 0.112 → **0.092** (só ordem).
  - V9 documental: células exatas 0.582 → 0.601; `numeric`/`currency` 0.953/0.962 inalterados.
  - Nenhuma outra tabela mudou nos 8 corpora (giradas V9 p20/p55/p71 e V3 p116 idênticas).
  - **Resíduos da p58**:
    - Cabeçalhos "Rubrica/Descrição/Qtde." vazios; "Rubrica" e "Qtde." ausentes nas linhas 005–007; sujeira de réguas ("\|1").
    - Três valores monetários corrompidos (`R$ 2.333,52333,92`, `R$ER$ 845,26`, `R$ 1.036,351.`), que vêm da leitura e não da estrutura. Registrados como **T3-P39**.
  - p66, p78, p25 e p8: idênticas à 008.

- **Evidência (006)**: V9 p58 (−3.4°), p66 (−4.0°), p78 (−3.6°) sem tabela detectada (`cell_content_exact_match` 0; `currency` 0.86 / 0.63); p25 (−4.0°) 0.118 e p8 (−1.8°) 0.375. As linhas da tabela saem como texto corrido ("001 peças de reposição |1 | R$ 206,74 R$ 206,74 Regular 002 | …").
- **Hipótese**: a detecção visual da grade (`tables/visual.py`, Hough + grade alinhada aos eixos) rejeita réguas inclinadas, e a releitura endireitada (T3-P18) só corrige o texto. Componente de detecção visual compartilhado. Próxima prioridade de tabelas.
- **Diagnóstico pós-008 (sondas por etapa, extração real das páginas)** — a hipótese inicial estava errada: a grade **é** encontrada e endireitada (`align_ruling_lines` aceita até ~8°; V9 p58 7×9 e p66 7×8, confiança 0.97). Onde cada página falha:

| Página | Região | Grade | Compensação de inclinação | Reconhecimento | Células | Integração |
|---|---|---|---|---|---|---|
| p58 | ok | ok | ok | ok (corrupções "R$ 2.333,52333,92", "R$ER$" vêm da leitura OCR da página) | — | **rejeitada**: a tabela escaneada inclinada foi recortada no quadro da imagem (cantos cortados); os cantos extrapolados da grade saem até ~16 pt da imagem e a contenção "grade dentro de um objeto de imagem" (tolerância 6 pt) descarta a tabela |
| p66 | ok | ok | ok | **ruim**: a leitura da região inclinada lê as réguas como "\|" e trunca valores ("R$ 7") | — | após a contenção, rejeitada pelo filtro de pipes embutidos (≥ 8% das células); desligar o filtro (experimento em memória) daria tabela com valores truncados — não é correção |
| p78 | ok | **falha**: bordas externas cortadas pela imagem, restam 2 réguas verticais (mínimo 3) | — | — | — | — |
| p25 | ok | 1ª tabela ok; 2ª tabela **sem nenhuma régua vertical** (só linhas horizontais inclinadas, coluna Ação cortada) | — | — | colunas fundidas (Descrição+Qtde.) | — |
| p8 | ok | 2ª tabela ok; 1ª tabela não detectada (réguas claras, borda esquerda cortada) | — | — | — | — |

- **Tentativas** (validação: 48 páginas V3–V10 e controle ampliado de 47 páginas de tabela raster V4/V9/V10, cada uma com a correção ligada e desligada em memória):

| # | Hipótese | Alteração | Resultado | Decisão |
|---|---|---|---|---|
| 1 | A contenção deve testar as réguas **observadas**, não os cantos extrapolados de uma grade endireitada | `_observed_rules_bbox`; contenção pelas réguas quando `source_transform` existe; fragmento limitado à imagem | p58 detectada, mas rejeitada na validação (`cell_outside_candidate_bbox`); 48 páginas idênticas | sem efeito — substituída |
| 2 | Limitar também as caixas das células à imagem | `cell.bbox ∩ imagem` | polígonos inconsistentes (`invalid_cell_polygon`, `unexpected_cell_overlap`) na sonda da p58 | rejeitada |
| 3 | O fragmento mantém a extensão da grade (contém todas as células) | fragmento = grade extrapolada | p58 detectada: células exatas 0 → 0.646, `cell_cer` 1.000 → 0.153, mas **CER 0.157 → 0.342**: o parágrafo abaixo da imagem passou para antes da tabela | ordem errada — refinada |
| 4 | `_place_visual_tables_between_prose` projetava a prosa nativa (nivelada) no referencial **inclinado** da tabela; uma linha larga projetada a −3.4° invade a faixa da tabela. Só o quarto de volta muda a leitura da página, não a inclinação de escaneamento | `_quarter_turn_frame` em `assemble/content.py`: o referencial da tabela é arredondado ao quarto de volta mais próximo antes de comparar posições (giradas 90°/180°/270° inalteradas) | **p58 CER 0.157 → 0.063**, células exatas 0 → 0.646, `table_detection_f1` → 1.000, `paragraph_detection_f1` 0.444 → 1.000; `numeric`/`currency` iguais. Controle ampliado: **V9 p44 0.366 → 0.204 e p49 0.112 → 0.092** (só ordem, mesmas linhas); demais 92 páginas idênticas, incluindo as giradas V9 p20/p55/p71/p36/p92, V3 p116, V4 p146–p190, V5, V8, V10 | **mantida** |

- **Escopo**: `tables/visual.py` (detecção visual, compartilhada) e `assemble/content.py` (montagem, compartilhada). A contenção nova só vale para grades endireitadas e exige que a rede inteira de réguas esteja dentro de um único objeto de imagem.
- **Pendentes (causas distintas, 1 tentativa restante)**: p66 (qualidade da leitura OCR da região inclinada; exigiria reler a tabela no raster endireitado), p78 (bordas externas cortadas — grade com 2 réguas), p25 2ª tabela (sem réguas verticais), p8 1ª tabela.
- **Testes**: `tests/test_tesseract_run008_regressions.py` — tabela inclinada com cantos cortados detectada; rede de réguas fora do objeto de imagem continua rejeitada; prosa nivelada abaixo de tabela inclinada continua depois dela; referencial de quarto de volta preservado.

### T3-P35 | ALTO | Ordem de blocos em páginas raster multicoluna: calhas fragmentadas — CORRIGIDO — CONFIRMADO NA RUN 008 (sem inclinação)

- **Evidência (007)**: V10 raster multicoluna concentra 87% das edições (L01/L02 inclinadas 46%; **L04/L05/L06 sem inclinação 41%**, CER do grupo 0.14–0.22). Nas páginas sem inclinação a ordem dos procedimentos está certa, mas **segmentos de coluna mudam de lugar** (V10 p52: trechos da coluna 1 dentro da coluna 2; bloco "Registro de exame final" antecipado).
- **Diagnóstico por etapa (V10 p52)**: tokens e linhas do Tesseract corretos; `_validated_gutters` valida a **mesma** calha em pedaços (x 211: y 162–304 e 291–391; x 377: 162–304 e 291–416) porque as linhas de apoio se agrupam por contiguidade vertical; `text_column_bands` transforma os pedaços em **faixas diferentes**; na fronteira, o agrupamento por tipo do layout OCR junta numa única região TEXT o fim de uma coluna com as demais (`ocr-6` x 57–535; `ocr-8` 28 linhas nas 3 colunas); a montagem reordena essa região com a detecção genérica e embaralha os segmentos. Mesmo padrão em p11 (faixa de baixo partida), p24/p6/p19/p50 (dois "meios" quase iguais na mesma faixa: 209/212, 202/212, 376/380, 376/382).
- **Tentativas**:

| # | Hipótese | Alteração | Resultado (66 páginas: V3 80/96/97; V4 82/86/107/108/113/116/136/140/150; V6 3/33; V8 4/58; V9 4/8/9/17/20/23/31/41/50/53/55/67/71/84; V10 3/5/6/8/9/11/13/18/19/21/24/25/31/32/37/40/41/46/48/50/52/56/59/62/63/66/68/70/72/78/81/84) | Decisão |
|---|---|---|---|---|
| 1 | Pedaços colineares da mesma calha (x a ≤ 2 alturas de texto) que se sobrepõem, ou cujo vão não é atravessado por nenhuma linha do motor, são uma calha só | `reconstruct._merge_collinear_gutters` no fim de `_validated_gutters` | escopo V10 0.199 → 0.147; p52 0.372 → 0.083, p11 0.329 → 0.095, p24 0.302 → 0.082, p84 0.207 → 0.050, p66 0.181 → 0.057, p40, p56, p78, p18 0.322 → 0.219; V9 p9 0.394 → 0.367; **p25 (inclinada) perde uma ocorrência de um parágrafo repetido (~40 palavras; recall 0.997 → 0.946)**; p32 0.213 → 0.311 | ajustar (perda de conteúdo) |
| 2 | Em página endireitada o layout é frágil (títulos inflados pela inclinação, T3-P31); unir pedaços só com o texto nivelado | `_MERGE_GUTTERS_MAX_SLOPE = 0.005` (≈ 0.3°); `_validated_gutters(..., slope)` | escopo V10 0.199 → **0.153**; p52 → 0.083, p11 → 0.095, p24 → 0.082, p84 → 0.050, p66 → 0.057, p40 → 0.123, p56 → 0.141, p78 → 0.087; recall inalterado; **páginas inclinadas e V3/V4/V6/V8/V9 idênticas à linha de base**; resta p32 | **mantida** |

- **Perda de conteúdo da tentativa 1 (V10 p25)**: a página tem o mesmo parágrafo 4 vezes; com as novas fronteiras de região, linhas de corpo classificadas como título por altura inflada (dezenas de regiões TITLE de 1–4 linhas) e uma ocorrência desaparece no Markdown final. Mecanismo de deduplicação não isolado; ligado ao problema de títulos em páginas endireitadas (T3-P31/T3-P06). Evitado pelo limite de inclinação.
- **Resíduo para avaliação (V10 p32, L04 sem inclinação)**: CER 0.213 → 0.311 sem perda de palavras (735 = 735). A calha da faixa de baixo termina em y 687 e as últimas linhas das duas colunas (y 690–704) caem numa região TEXT que abrange as duas; uma linha de corpo vira rodapé. Só reordenação.
- **Testes**: `tests/test_tesseract_run007_regressions.py` (novo, 3): pedaços separados por um vão unidos; separados por linha de largura total mantidos; texto inclinado não unido.
- **Pendente**: L01/L02 (inclinadas, 46% das edições do V10) — títulos inflados e deduplicação; fim de faixa (p32).
- **RUN 009**:
  - L04–L06 inalteradas em texto (CER médio 0.104); p32 continua com 0.311, e só a segmentação mudou (T3-P20).
  - Com as páginas niveladas corrigidas, **L01/L02 respondem por 56% das edições do V10** (L02 30.4%, L01 25.6%; CER médio 0.321/0.271, recall ≥ 0.91). É a maior massa de erro do corpus prioritário (§1.18).
- **Diagnóstico pós-011 (§1.26, sem alteração)**: a leitura endireitada de T3-P18 é devolvida como envelope alinhado aos eixos, inflando cada linha de coluna em 10.5–12.5 pt (1.6–1.9× uma linha de corpo). Proposta nova: fazer linhas, layout OCR, parágrafos e ordem no referencial endireitado em páginas raster integrais com |θ| ≥ 1°, recuperando a geometria exata (inversão verificada, erro ≤ 0.01 px) e mantendo inalterados os componentes compartilhados de ordem. Hipótese H1 refutável no Nível 1, sem OCR. Exige autorização para o ponto de orquestração em `api.py`. Tentativas: 2 / 5 (nenhuma gasta).


### T3-P36 | CRÍTICO | Tabela OCR suprimida silenciosamente (V4 p167/p168) — CORRIGIDO — VALIDADO PONTUALMENTE (pós-009, 1/5)

- **Evidência**:
  - V4 p167 (`ocr_table`, `grayscale`, `table_unicode`, 120 dpi) e p168 (mesma tabela, outra degradação) devem conter uma tabela 3 × 7 multilíngue: Português, English, Español, Deutsch, Grego, Cirílico.
  - Na saída da 002 até a 009, a página tem **só** "Controle de página: V4-P167": 27 de 273 caracteres e recall 0.095. A p168 tem 71 caracteres e recall 0.071.
  - Na **001** (sem tabelas, T3-P00) as linhas latinas eram lidas como texto ("Português ação, órgão, coração PT-TIZA English | evidence and reading order …"), com recall 0.286/0.500.
  - O manifesto marca as duas páginas como `ocr_outcome: success`, `content_status: valid`, `stability_status: clean`, **sem aviso**. A perda é silenciosa.
- **Abrangência**:
  - A varredura de recall por página das rodadas 001–009 encontrou perda total de volume (< 50% dos caracteres) **só** nessas duas páginas.
  - Quedas menores desde a 002: V4 p151, p178 e V3 p94 (tabelas OCR).
- **Hipótese** (não testada nesta etapa): a tabela OCR é detectada e depois rejeitada (validação/integração), e o texto da região não volta ao fluxo. A presença de escritas não latinas (grego, cirílico) com `pt-BR` pode ser o gatilho, mas também se perdem as linhas latinas.
- **Componente**: a identificar (detecção de tabela OCR → validação → integração). Provavelmente compartilhado.
- **Diagnóstico (pós-009, §1.19)**: a hipótese acima estava errada; a tabela não é rejeitada na validação nem na integração.
  - A grade 7 × 3 é detectada e lida.
  - Quem a remove é a regra de pós-processamento `_suppress_rotated_table_garble`: um token com "Q" sem "U" ("aByAQnA", leitura latina do grego) suprime o bloco TABLE inteiro, depois do registro de conservação, sem aviso.
- **Tentativa 1 (mantida)**: `_flag_rotated_table_garble` mantém o bloco e registra o aviso `suspected_ocr_garble`. `_report_postprocess_suppressions` grava em `facts` toda supressão do pós-processamento e transforma em aviso as que não são mobília reconhecida.
- **Resultado pontual**: p167 CER 0.901 → 0.194, recall 0.095 → 0.548; p168 0.853 → 0.531, recall 0.071 → 0.476. Nenhuma outra página dos 8 corpora mudou (72 páginas).
- **Testes**:
  - `tests/test_tesseract_run009_regressions.py`: tabela legível com uma palavra impossível é mantida e reportada; supressão de conteúdo vira aviso e a de mobília não. Os dois falham com o código de `b1202ee` (verificado).
  - `tests/test_tesseract_postprocess.py`: o teste da Fix-O passa a exigir que o bloco seja **mantido** e sinalizado (mudança de contrato autorizada para T3-P36).
- **RUN 010**: confirmado (p167 recall 0.548, CER 0.194; p168 recall 0.476; aviso nas duas; nenhuma outra página alterada; nenhum `content_suppressed_by_postprocess` nos 8 corpora). O aviso identifica leitura suspeita mantida, não perda: a coluna "Idioma" da p168, não lida pelo Tesseract, não gera aviso. Supressões feitas antes do pós-processamento (por exemplo `figure_no_text_coverage` no registro de conservação) continuam sem aviso.
- **Status**: CORRIGIDO — CONFIRMADO NA RUN 010.

### T3-P37 | ALTO | Duas tabelas nativas lado a lado: fundidas ou desmontadas (V9) — NOVO (análise da RUN 009)

- **Evidência (009, igual à 008)**:
  - **p62** (CER 0.465, células exatas 0.194): a página tem as tabelas "Rubrica | Qtde. | Total | Situação" e "Ação | Prazo | Situação" lado a lado.
    - A saída mistura uma tabela HTML parcial ("R$ 240,22 Conferido 002 01" numa célula) com títulos `### Rubrica`, `### Total`, `### Situação` e valores soltos.
    - **Os valores monetários ficam separados das linhas**: associação perdida.
  - **p72**: CER 0.301, células exatas 0.000, `numeric` 0.880.
  - **p14**: as duas tabelas saem fundidas numa só, de 7 colunas. Cada linha mantém os valores corretos, mas `table_column_count_accuracy` é 0 e as células exatas 0.400.
- **Relação**: a p72 constava como resíduo de T3-P22 ("blocos isolados de coluna classificados como status"). Esse mecanismo não foi confirmado para p14/p62.
- **Componente**: detector de tabelas e layout **nativos** (compartilhados). Risco de regressão em V5–V8.
- **Próximo passo**: diagnóstico por etapa; sem tentativa.

### T3-P38 | MÉDIO | Fusão de parágrafos independentes em OCR denso de coluna única — CORRIGIDO — CONFIRMADO NA RUN 011 (t3, 3/5)

- **Evidência**:
  - Nas 10 páginas abaixo, todas de OCR de texto em **coluna única** (V4, 9 parágrafos por página, cerca de 800 palavras, 110–200 dpi com ruído/blur/JPEG/listras), a 009 junta parágrafos que a 008 separava:

| Página | Condição | Fusões 008 → 009 | `paragraph_detection_f1` | `paragraph_boundary_recall` |
|---|---|---|---|---|
| V4 p83 | `scan_clean_200` | 0 → 2 | 0.696 → 0.571 | 1.000 → 0.778 |
| V4 p85 | `low_dpi_110` | 0 → 1 | 0.727 → 0.667 | 1.000 → 0.889 |
| V4 p99 | `jpeg_compression_16` | 0 → 1 | 0.696 → 0.545 | 1.000 → 0.889 |
| V4 p125 | `vertical_streaks` | 0 → 1 | 0.818 → 0.667 | 1.000 → 0.889 |
| V4 p129 | `photocopy_threshold` | 0 → 3 | 0.696 → 0.400 | 1.000 → 0.667 |
| V4 p137 | `combined_noise_blur` | 2 → 7 | 0.417 → 0.105 | 0.778 → 0.222 |
| V4 p138 | `combined_noise_blur` | 4 → 7 | 0.182 → 0.000 | 0.556 → 0.222 |
| V4 p139 | `combined_dark_jpeg` | 0 → 1 | 0.909 → 0.762 | 1.000 → 0.889 |
| V4 p141 | `small_text_dense` | 0 → 1 | 0.357 → 0.370 | 1.000 → 0.889 |
| V4 p143 | `edge_text_dense` | 0 → 1 | 0.818 → 0.762 | 1.000 → 0.889 |

  - V10 p68 (L02, inclinada −4.6°): +1 fusão, `paragraph_detection_f1` 0.255 → 0.182.
  - Exemplo (V4 p138): "… deslocada para outro bloco. Parágrafo 138.04: Transparência …" no mesmo bloco que 138.03 e 138.05.
- **Sem perda de conteúdo**: a sequência de palavras das páginas é idêntica byte a byte à 008, e CER, recall e dados críticos não mudam.
- **Saldo**:
  - As 17 outras páginas do V4 que mudaram (principalmente as de duas colunas, incluindo o controle p140) melhoraram.
  - No documento, `paragraph_detection_f1` sobe (0.378 → 0.410), mas `paragraph_boundary_recall` cai (0.578 → 0.559).
- **Causa**:
  - Hipótese: em texto denso de coluna única o vão entre parágrafos é pequeno, perto do novo limite relativo `m + 0.2·h` (t2) ou da descendente estimada (t5).
  - A V10 p68 é inclinada, onde a t5 não age (`level_text`), o que aponta para a t2 nesse caso.
  - **Não atribuído**: exige extração pontual das 10 páginas com t2 e t5 ligadas e desligadas.
- **Por que as validações pontuais não pegaram**: as listas de controle de §1.13/§1.14 não registram essas páginas. Futuras mudanças de segmentação devem incluí-las (§5).
- **Componente**: `ocr/reconstruct.py::segment_ocr_paragraphs` (regiões OCR).
- **Ciclo pós-009 (§1.19)**:
  - A atribuição por extração das mesmas páginas mostra que a **t2** causa a regressão; a t5 sozinha acrescenta 1 fusão (p137).
  - Candidatas: h2 e h3 (tentativas 1–2, só em memória); "só t5" (reverter a t2) é a melhor (V4 fusões 27 → 9; F1 0.311 → 0.352; +4 quebras em duas colunas).
  - **Nenhuma alteração aplicada**: o código continua como na RUN 009.
  - "Só t5" exige ciclo próprio com validação progressiva, porque remove a tolerância de T3-P20 coberta pelo teste da V10 p11.
  - A contagem de T3-P20 continua 5/5; a de T3-P38 é 2/5.
- **Ciclo pós-010, tentativa 3 (§1.21, mantida)**:
  - Em `segment_ocr_paragraphs`, o limite relativo da regra 1 volta a `1.4·m`; a compensação da descendente ausente em texto nivelado (T3-P20 t5) é mantida.
  - Nível 1: novo teste com a geometria real da V4 p129 (falha em `95a6f72`, passa na candidata); testes de T3-P20 passam.
  - Níveis 2–3 reaproveitados (73 págs.): V4 fusões 27 → 9 e F1 0.311 → 0.352.
  - Nível 4: 43 controles finais idênticos byte a byte.
  - Custo: +4 quebras em duas colunas do V4.
  - **Status**: CORRIGIDO — VALIDADO PONTUALMENTE (3/5), a confirmar na RUN 011.
- **RUN 011 (§1.22)**: confirmado.
  - V4 `paragraph_boundary_recall` 0.5595 → **0.5772** (008: 0.5782); `paragraph_detection_f1` 0.410 → **0.421**; V9 0.595 (idêntico); V10 0.458 → 0.459.
  - Fusões V4 435 → **417** (008: 416), V10 44 → 43; quebras V4 274 → 278, V9 80, V10 221 → 220. Resultado idêntico ao previsto pelo pontual.
  - Dez das onze páginas-alvo (V4 p83, p85, p99, p125, p129, p138, p139, p141, p143; V10 p68) voltam exatamente aos valores da 008.
  - **Resíduo**: V4 p137, 137.04 + 137.05 fundidos (atribuído à t5; F1 0.348 contra 0.417 na 008).
  - **Custo**: uma quebra nova no meio de parágrafo em V4 p98, p118, p122, p128, p130 e uma palavra isolada na V10 p37 (L05); todas continuam acima da 008.
  - Sequência de palavras idêntica em todas as páginas; nenhuma outra métrica de conteúdo, dados críticos, títulos, listas ou tabelas mudou.
  - **Status**: CORRIGIDO — CONFIRMADO NA RUN 011. Monitorar; não reabrir pelos resíduos.

### T3-P39 | ALTO | Valores monetários corrompidos em tabela raster inclinada (V9 p58) — CORRIGIDO — VALIDADO PONTUALMENTE (pós-009, 1/5)

- **Evidência**: a tabela da p58 é detectada desde a 009 (T3-P34), mas três células de valor contêm leituras corrompidas:
  - `R$ 2.333,52333,92` (gabarito `R$ 2.333,52`);
  - `R$ER$ 845,26` (`R$ 845,26`);
  - `R$ 1.036,351.` (`R$ 1.036,35`);
  - mais o prefixo `lrs:R$ 388,92`.
- Na 006–008 os mesmos valores já saíam corrompidos como texto solto, então não é regressão. `numeric` da página 0.741 e `currency` 0.857, iguais desde a 006.
- **Hipótese** (a confirmar): duas leituras do mesmo valor (página e releitura de região, ou leituras de passes diferentes) colam na mesma linha, como em T3-P28 (`€ 98,4096,40`). A regra de T3-P28 só une leituras de **chamadas diferentes** com IoU ≥ 0.6, e na tabela inclinada as caixas podem ficar abaixo desse limiar.
- **Componente provável**: montagem de linhas do Tesseract (`ocr/reconstruct.py`) ou refinamento de células. Só Tesseract se a hipótese se confirmar.
- **Diagnóstico (pós-009, §1.19)**: confirmado como leituras do mesmo ponto vindas de chamadas diferentes, com uma caixa contida na outra ("333,92" 0.672 dentro de "2.333,52" 0.966, IoU 0.57; "R$E" 0.315 em volta de "R$" 0.959). A regra de T3-P28 (IoU ≥ 0.6) não se aplica a esses pares.
- **Tentativa 1 (mantida)**: `_drop_conflicting_rereads` também resolve contenção (≥ 80% da caixa menor dentro da maior, chamadas diferentes).
  - A interna menos confiável sai; a externa sai só se tiver confiança < 0.5 e for menos confiável que a interna; duas leituras confiantes ficam.
  - Sem regra de texto ou número.
- **Resultado pontual**: p58 `R$ 2.333,52`, `R$ 845,26`, `R$ 1.036,35`, `R$ 388,92` e `Conferido` corretos; CER 0.063 → 0.046; `currency` 0.857 → 1.000; células exatas 0.646 → 0.750. p66 com ruído removido (recall 0.789 → 0.803). V9 p20/p55/p71, V8 p4/p58 e as demais 66 páginas idênticas.
- **Testes** (`tests/test_tesseract_run009_regressions.py`): leitura parcial contida descartada; leitura ruidosa envolvente descartada (os dois falham com o código de `b1202ee`, verificado); proteções (duas leituras confiantes, mesma chamada, vizinhas lado a lado) mantidas.
- **RUN 010**: confirmado. V9 p58 com os três valores corretos (`currency` 1.000); a regra também removeu leituras coladas em V9 p66, V6 p11 (`ValValor`), V8 p49 (`COMPLEMENTONTO`) e V8 p67 (`02/10/20262026` → `02/10/2026`). Só remoções; nenhuma página perdeu acertos de dados críticos; controles V8 p4/p58 e V9 p20/p55/p71 idênticos.
- **Status**: CORRIGIDO — CONFIRMADO NA RUN 010.

### T3-P40 | INFO | Título e subtítulo exigidos pelo gabarito V5 ausentes do PDF — RECLASSIFICADO (limitação do gabarito)

- **Sintoma (009)**: em 12 das 42 páginas do V5 (p4, 5, 6, 17, 18, 20, 23, 26, 28, 32, 36, 38), o título H1 e o subtítulo do bloco `heading` do gabarito não aparecem na saída; recall das páginas 0.70–0.82; ausentes desde a 001.
- **Diagnóstico (pós-009, §1.19; investigação na camada nativa, sem alteração)**:
  - Na área `bbox_pt` do bloco `heading` não há nenhum objeto do PDF nem tinta na renderização.
  - A camada de texto da página não contém o título em nenhum lugar, nem fora da página.
  - Nas outras 30 páginas, a mesma área tem 2 objetos de texto com o título esperado.
  - As 12 páginas são as duas repetições de 6 famílias (`reading_order`, `scrambled_internal_order`, `hybrid_page`, `reading_order_stress`, `interleaved_native_ocr`).
  - O `VALIDACAO.txt` do V5 não compara o PDF com o Markdown esperado.
- **Conclusão**: possível inconsistência do PDF ou do gabarito V5; **não** é perda do extrator. Corresponde a ≈ 5.0% dos caracteres não-espaço do gabarito V5.
- **Riscos para outros motores**: nenhum (sem alteração de código).
- **Abordagem recomendada**: nenhuma correção na camada nativa. Decisão do responsável sobre o corpus (regenerar ou corrigir fora desta série). Até lá, descontar esse efeito ao interpretar as métricas e a nota do V5. O corpus **não** foi alterado.

### T3-P41 | MÉDIO | Parágrafos de texto nativo denso fundidos num único bloco (V4 p9–p40) — NOVO (auditoria da RUN 011; existe desde a 001)

- **Evidência (011, igual desde a 001)**:
  - Nas 32 páginas de texto nativo denso do V4, os parágrafos de cada página saem num só bloco. Exemplo: V4 p9, 10 parágrafos ("Parágrafo 009.01" a "009.10") num bloco de 6250 caracteres; `paragraph_detection_f1` 0.154, `paragraph_boundary_recall` 0.100.
  - Na família: 38 parágrafos certos de 316 (69 FP, 278 FN); 227 das 430 fronteiras ausentes do V4 inteiro.
  - Pelo diagnóstico de alinhamento da §1.22, são **227 das 417 fusões do V4 (54%)**; o OCR de texto tem 120.
  - Família com 39 acertos na 001 e 38 na 011: não é regressão.
- **Impacto estimado** (interpretação **não oficial e não validada**; só pode ser usado como métrica depois de uma correção medida em rodada completa): com essas páginas corretas, o `paragraph_detection_f1` do V4 iria de 0.42 para ≈ 0.59 e o `paragraph_boundary_f1` de 0.55 para ≈ 0.70; Parágrafos do V4 ≈ 64 na rubrica e nota ≈ +1.6.
- **Componente**: segmentação de parágrafos do texto **nativo** (compartilhada com os demais motores). Fora do escopo sem autorização explícita; risco em V5–V8, cujos parágrafos estão em 0.82–0.97.
- **Sem conteúdo afetado**: palavras, ordem e dados críticos estão corretos nessas páginas.
- **Próximo passo**: só com autorização. Começar pelo Nível 0, medindo nas saídas existentes o espaçamento entre parágrafos das páginas p9–p40 frente às páginas nativas de V5–V8 que já separam bem.
- **Causa raiz (pós-012, §1.31, Nível 0 sem extração)**: na V4 p9 (geometria lida do PDF), as linhas do mesmo parágrafo distam 1.1–1.8 pt e os parágrafos 4.5 pt, com a última linha curta. `assemble/content.py::_native_paragraphs` exige `vão > max(0.75 × altura, min(1.5 × vão típico, 1.2 × altura))`; com altura ≈ 7.7 pt, o piso ≈ 5.8 pt domina. Desenho proposto: quebra relativa (vão ≥ ≈ 2.5 × o típico e ≥ ≈ 0.35 × altura) com corroboração da linha anterior curta; regra atual mantida em espaçamento uniforme. Estimativa +1.6 na nota do V4 (Parágrafos 54 → ≈ 70). **Candidato único do próximo ciclo**, sujeito a autorização (nativo compartilhado). Validação quase toda sem OCR (§1.31, Fase 6).

### T3-P42 | ALTO | Linhas OCR descartadas na fusão de fragmentos de título — CORRIGIDO: CONFIRMADO NA RUN 012

- **Achado da auditoria da RUN 012** (§1.28): V10 p5, p31, p62 (inclinadas L01/L02) e p11 (nivelada) ganham 4, 22, 53 e 4 palavras que faltavam na 011; recall da página 0.936 → 0.941, 0.969 → 0.997, 0.924 → 0.996, 0.976 → 0.982; V10 0.9915 → 0.9938.
- **Mecanismo**: `layout/heading.py::_merge_heading_fragments` juntava `native_lines` e `ocr_tokens` de dois fragmentos de título, mas mantinha só os `ocr_lines` do primeiro. A montagem renderiza regiões OCR por `ocr_lines`, então as linhas do segundo fragmento sumiam **sem aviso** (o pós-processamento não as recuperava). Nas páginas inclinadas, as caixas infladas criam fragmentos TITLE que depois são rebaixados a texto: a perda acontecia mesmo sem nenhum título na saída.
- **Correção**: incluída como correção de integridade na implementação de T3-P06 t3 (§1.27, `18ccfb0`), que concatena também `ocr_lines`. A §1.27 registrou que ela não mudava as entradas capturadas; o efeito só apareceu na rodada completa.
- **Efeitos colaterais**: o texto recuperado entra no bloco do primeiro fragmento (CER da p5 0.157 → 0.160 e da p31 0.277 → 0.283; 1 fusão nova em p31 e em p62); a V10 p62 passa a contar como "erro de ordem" na rubrica (recall ≥ 0.95 com CER 0.251), o que baixa a categoria Ordem do V10 sem piora real. Na p11 o fragmento fundido virou um falso título (linha de corpo).
- **Status**: CORRIGIDO — CONFIRMADO NA RUN 012. Desde quando a perda existia não foi medido (as saídas anteriores à 011 não foram reanalisadas para isso).

### T3-P43 | ALTO | Cabeçalho/rodapé corrente com contador de página sem rótulo não suprimido — NOVO (auditoria da RUN 013)

- **Corpora/páginas**: V11, 185 linhas em 94 páginas ("Coleção sintética · N" em 63 nativas/mistas; "INSTITUTO FICTÍCIO DE ENSAIOS DOCUMENTAIS / V11-R1 Coleção sintética - N" em 20 raster; "Ensaio V11-R1 · Folha N" e variantes OCR com "-" e ":"). V10, 65 páginas ("Documento sintético para teste de OCR e estrutura | Folha NN/84"; já citado na §1.25 como "rodapé decorativo", sem identificar a causa).
- **Mecanismo (demonstrado no código)**: `assemble/repeated_regions.py::_normalize_signature` só substitui o número da página por `<page-number>` quando há o rótulo "página/pagina/page" ou quando a linha inteira é `N / M`. Em "· 103" ou "Folha 103" o número fica na assinatura, que passa a ser única por página; `detect_repeated_headers_footers` exige ≥ 2 páginas com a mesma assinatura e nunca confirma. Nas páginas nativas do V11, a parte esquerda do cabeçalho ("INSTITUTO…"), idêntica em todas, é confirmada e suprimida; sobra a parte com o contador.
- **Impacto**: contrafactual com o avaliador oficial (§1.34): V11 65.9 → 71.0, V10 75.5 → 78.9. No V11, ≈ 185 dos 250 `numeric` fp são esses contadores, e a mobília cria ≈ 2 parágrafos falsos por página.
- **Proposta**: §1.35 (contador confirmado por covariância com o índice da página em ≥ 3 páginas). Exige autorização: componente compartilhado pelos caminhos nativo e OCR.
- **Risco conhecido**: a linha "Continuação do registro técnico · folha sintética N" do V11 (7 páginas, no gabarito) tem a mesma estrutura e tende a ser suprimida (−0.3).
- **Status**: NOVO — causa raiz demonstrada; candidato único do próximo ciclo.

- **Atualização do ciclo pós-013 (§1.37)**: implementado em `assemble/repeated_regions.py` (fase `_confirm_page_counters`), com evidências convergentes (borda, forma textual, contador com deslocamento único em todas as ocorrências, ≥ 3 páginas consecutivas, núcleo de posição estável, estilo, proeminência ≤ corpo). Validado offline sobre capturas completas: V11 65.9 → 70.4, V10 75.5 → 78.3, 0 linhas do gabarito removidas; a "Continuação…" é preservada por evidência estrutural. **Status**: CORRIGIDO — VALIDADO OFFLINE (não commitado); aguarda RUN 014.

### T3-P44 | CRÍTICO | Perda silenciosa de texto em página raster degradada — NOVO (auditoria da RUN 013)

- **Corpora/páginas**: V11 p71 (`faint_text_stamp`: texto claro com carimbo, −2°, 220 dpi); parciais p63 (recall 0.766; −8°, escuro e de baixa resolução) e p75 (0.643; moiré).
- **Evidência**: na p71 saem o título, o 1º parágrafo e restos ilegíveis da tabela ("FETIZ", "E", "FG7I3"); somem os parágrafos R201, R202 e R075 e os valores de massa. Recall 0.327, 746 de 1880 caracteres. A página é `success`/`clean`, sem nenhum aviso.
- **Hipótese**: o Tesseract não produz tokens no texto de baixo contraste, e a política adaptativa (variantes de qualidade) reage a texto ilegível, não à falta de cobertura de tinta textual. **A etapa onde o texto se perde não foi localizada**: exige o TSV bruto da p71 (OCR novo, não feito nesta auditoria).
- **Classe da correção possível**: (1) tornar a falha não silenciosa (aviso por região com tinta textual e sem tokens); (2) acionar uma variante de reconhecimento quando a cobertura for baixa. As duas exigem experimento de OCR.
- **Status**: NOVO — diagnóstico pendente (captura de 1 página, com autorização).

- **Atualização do ciclo pós-013 (§1.36)**: **causa localizada**. O Tesseract reconhece o texto na releitura endireitada de `_deskew_page` (295 palavras, cobertura 0.95), mas ela é descartada por `_DESKEW_MAX_CONFIDENCE_LOSS` (média 86.45 contra 90.11 da leitura direta, que tem 109 palavras e cobertura 0.33). Nas palavras comuns, a releitura tem confiança maior. Defeito geral: comparação de médias sobre conjuntos diferentes de palavras, sem cobertura, e decisão não registrada. Nenhuma perda depois do TSV escolhido. Correção separada proposta (prioridade CRÍTICA, não implementada).

### T3-P45 | ALTO | Tabela nativa emitida duas vezes, com o título absorvido em células — NOVO (auditoria da RUN 013)

- **Corpora/páginas**: V11 p50 (tabela monetária com convenções BR e internacional), p93 (figura fora da área visível).
- **Evidência**: a 1ª cópia inclui a linha do título partida em colunas ("| Comparação | de formatos d | e registro fin | anceiro |"; "| Área técnica de recortes e visibili | dade |"); a 2ª é a tabela correta. O título se perde como título; valores duplicados (1 `currency` fp).
- **Hipótese**: dois candidatos de tabela sobrepostos (com e sem a linha do título) sem deduplicação. A camada de texto nativo da p50 e da p93 tem o título numa linha própria, acima da tabela.
- **Status**: NOVO — diagnóstico por etapa possível sem OCR (páginas nativas).

### T3-P46 | ALTO | Texto nativo em colunas e lista com marcador detectados como tabela sem borda — NOVO (auditoria da RUN 013)

- **Corpora/páginas**: V11 p5 (3 colunas), p98 (4 colunas estreitas), p86 (lista com "•" emitida como tabela de 2 colunas); tabelas falsas também em p3, p7, p102.
- **Evidência**: p98 com recall 1.0 e CER 0.763: cada linha visual das 4 colunas vira uma linha de tabela, e os parágrafos das equipes ficam intercalados. p5 e p98 somam 12.8% das edições do V11. Os subtítulos das colunas viram uma linha em negrito ("**Ametista Safira Topázio Jade**").
- **Relação**: análogo nativo de T3-P17 (OCR, resolvido); afeta também a ordem de leitura (T3-P22).
- **Status**: NOVO — exige autorização (detector nativo compartilhado; risco para V5–V9).

### T3-P47 | ALTO | Tabelas nativas sem borda empilhadas ou de formulário desmontadas — NOVO (auditoria da RUN 013)

- **Corpora/páginas**: V11 p57 (3 tabelas empilhadas: só o cabeçalho vira tabela e as 32 células saem como parágrafos soltos, coluna a coluna), p9 (formulário em paisagem: campos fora de ordem), p37, p41, p54, p110, p115, p120 (não detectadas); p45, p60, p63 (parciais).
- **Impacto**: o vínculo valor ↔ linha se perde; V11 `table_detection_recall` 0.759, células exatas 0.750.
- **Relação**: família de T3-P37 (V9 p62/p72/p14).
- **Status**: NOVO.

### T3-P48 | MÉDIO | Estado de caixas de seleção vetoriais não representado — NOVO (auditoria da RUN 013)

- **Corpora/páginas**: V11 p54 (tabela de inspeção com 15 caixas ☑/☐).
- **Evidência**: as caixas são desenhos vetoriais, não texto (camada de texto nativo sem os símbolos); a saída não tem os estados e a tabela não é detectada. Sem aviso. Auditoria suplementar: `checkbox_states` fn 15.
- **Status**: NOVO — recurso inexistente no extrator; fora do escopo da otimização do Tesseract.

### T3-P49 | MÉDIO | Linhas de parágrafo em ordem invertida em página raster inclinada — NOVO (auditoria da RUN 013)

- **Corpora/páginas**: V11 p61 (−3°, caderno com espiral); possivelmente p72.
- **Evidência**: as palavras estão corretas, mas as linhas de cada parágrafo saem de baixo para cima (a 1ª linha, "No expediente…", aparece no fim). CER 0.539.
- **Hipótese**: ordem das linhas no referencial inclinado (família de T3-P35).
- **Status**: NOVO.

### T3-P50 | INFO | Convenção de nível de título do gabarito V11 — REGISTRADO (auditoria da RUN 013)

- O gabarito do V11 usa `#` em todos os títulos de página (100). A extração aplica a hierarquia do documento: o título da capa (maior) é `#`, e os títulos de página são `##`. `heading_level_match_rate` 0.056 (4/71), ≈ −1.9 na nota.
- Resíduo real: o título da capa sai partido em dois `#` ("AMOSTRA SINTÉTICA DE CONTAS E REGISTROS —" / "EXERCÍCIO 2025").
- **Não ajustar o extrator ao gabarito** (seria overfitting). Mesma natureza da convenção registrada para V3/V4 (§1.16, §1.31).

## §3 Alterações de código

### Pós-013, ciclo T3-P43 / diagnóstico de T3-P44 (2026-11-24, não commitado)

- `src/structured_pdf_text/assemble/repeated_regions.py`: nova fase `_confirm_page_counters` (com `_counter_form`, `_counter_slots`, `_has_consecutive_run`, `_stable_position_core`, `_not_more_prominent_than_body`), chamada no fim de `detect_repeated_headers_footers`. Nenhuma outra função nem assinatura alterada.
- `tests/test_repeated_page_counters.py`: novo (21 testes positivos e negativos).
- T3-P44: nenhuma alteração de código (diagnóstico por captura no scratchpad).

### Pós-012 → RUN 013 e auditoria (2026-11-23/24)

- `b541246` (responsável): inclusão do V11 em `scripts/run_tesseract_batch_wsl.py` (+1 linha). Nenhuma alteração em `src/` ou `tests/` desde `18ccfb0`.
- Auditoria da RUN 013 (§1.32–§1.35): nenhuma alteração de código, testes, PDFs, gabaritos, manifestos, avaliador ou configuração; nenhuma extração nem OCR; só este documento. Contrafactuais com o avaliador em memória, sem gravar arquivos. Nenhum teste executado.

### Pós-012, diagnóstico do V4 e meta de 85 (2026-11-19)

Nenhuma alteração de código, testes, PDFs, gabaritos, manifestos, avaliador, métricas ou configuração; nenhuma extração. Só este documento (§1.31, §5, quadros, T3-P21 e T3-P41). Scripts de leitura no scratchpad (`v4/`).

### Pós-012 (2026-11-19, auditoria)

Nenhuma alteração de código, testes, PDFs, gabaritos, manifestos, configurações ou avaliador. Só este documento foi atualizado (§1.28–§1.30, §5, quadros, T3-P06 e o novo T3-P42). Árvore limpa em `18ccfb0` antes da auditoria.

### Pós-011, T3-P06 t3 implementada (2026-11-19, commit `18ccfb0`; avaliada na RUN 012)

| Item | Arquivo | Mudança | Escopo |
|---|---|---|---|
| T3-P06 t3 (A) | `src/structured_pdf_text/layout/heading.py` | `assign_heading_levels` por proveniência; `_is_ocr_only`, `_has_native_font_evidence`, `_assign_ocr_title_levels`, `_has_section_prefix`, `_ocr_region_text`; corpo anterior em `_assign_typography_levels` (inalterado); `_merge_heading_fragments` concatena `ocr_lines` | regiões TITLE só-OCR de qualquer motor, em texto nivelado; regiões nativas inalteradas |
| T3-P06 t3 (B, C, D) | `src/structured_pdf_text/layout/ocr_aware.py` | `OCR_TITLE_LINE_RATIO`, `_EMBEDDED_LINE_GAP_RATIO`, `_is_embedded_in_paragraph`, `_starts_new_heading`; guarda de legenda em tamanho de título | regiões só-OCR, texto nivelado |
| critério de texto nivelado | `src/structured_pdf_text/ocr/reconstruct.py` | `LEVEL_TEXT_MAX_SLOPE = 0.005`; `_MERGE_GUTTERS_MAX_SLOPE` vira alias | sem mudança de comportamento |
| testes | `tests/test_tesseract_run011_regressions.py` (novo, 8 testes) | — | — |

### Pós-011, ciclo T3-P06 / T3-P35 (2026-11-19)

Nenhuma alteração de código, testes, gabaritos, PDFs, manifestos, configurações ou avaliador no repositório. Só este documento foi atualizado (§1.25, §1.26, §5 e quadros).

As candidatas e os testes de T3-P06 existem **só no scratchpad da sessão** (podem ser apagados por reinicialização; o desenho completo está em §1.25):
- `cand_t1.py`, `cand_t2.py`, `cand_t3.py` (monkeypatch de `layout/heading.py::assign_heading_levels` e `layout/ocr_aware.py::reconstruct_ocr_layout`);
- `test_t3p06_component.py` (8 testes com entradas reais capturadas);
- `harness.py` (extração de subconjunto de páginas com a configuração canônica do executor e métricas oficiais do subconjunto).

### Pós-011 (2026-11-19, auditoria)

Nenhuma alteração de código, testes, gabaritos, configurações ou avaliador. Só este documento foi atualizado (§1.22–§1.24, §5). Árvore de trabalho limpa em `2fdad33` antes da auditoria.

### Pós-010 (commit `2fdad33`, 2026-11-18 12:52 −03; avaliado na RUN 011)

| Item | Arquivo | Mudança | Escopo |
|---|---|---|---|
| T3-P38 t3 | `src/structured_pdf_text/ocr/reconstruct.py` | `segment_ocr_paragraphs`: `relative_gap = median_gap * 1.4` (remove o termo `m + 0.2·h` da t2 de T3-P20); compensação da descendente ausente (t5) mantida | regiões OCR (todos os motores que passam pelo montador) |
| testes | `tests/test_tesseract_run010_regressions.py` (novo, 1 teste com a geometria real da V4 p129) | — | — |

Commit `95a6f72` (2026-11-18): T3-P20 t5, T3-P36 t1, T3-P39 t1 e testes (§3 "Pós-009, ciclo de correções").

### Pós-009, ciclo de correções (2026-11-18, commit `95a6f72`)

| Item | Arquivo | Mudança | Escopo |
|---|---|---|---|
| T3-P36 t1 | `src/structured_pdf_text/ocr/tesseract/postprocess.py` | `_suppress_rotated_table_garble` → `_flag_rotated_table_garble` (mantém o bloco e avisa); `_FURNITURE_SUPPRESSION_REASONS`, `_report_postprocess_suppressions`, `_add_page_warning` (fato `tesseract_postprocess_suppressed_chars`; aviso `content_suppressed_by_postprocess` para supressões que não são mobília) | só Tesseract (pós-processamento) |
| T3-P39 t1 | `src/structured_pdf_text/ocr/reconstruct.py` | `_REREAD_CONTAINED_MIN = 0.8`, `_REREAD_NOISE_MAX_CONFIDENCE = 0.5`, segunda passagem em `_drop_conflicting_rereads`, `_contained_reread_loser` | só tokens com identidade de linha (Tesseract) |
| T3-P38 | — | **nenhuma** (candidatas só em memória, no scratchpad) | — |
| testes | `tests/test_tesseract_run009_regressions.py` (novo, 5 testes); `tests/test_tesseract_postprocess.py` (testes da Fix-O renomeados; o primeiro passa a exigir bloco mantido e aviso) | — | — |

**Continua não commitado** (anterior a este ciclo, participou da RUN 009): T3-P20 t5 em `src/structured_pdf_text/ocr/reconstruct.py` (`_LEVEL_BOX_MAX_RATIO`, `level_text`, `missing_descender` na regra 1) e os 2 testes correspondentes em `tests/test_tesseract_run008_regressions.py`.

### Pós-009 (2026-11-18, auditoria)

Nenhuma alteração de código, testes, gabaritos, configurações ou avaliador. A árvore de trabalho continua com T3-P20 t5 e os 2 testes correspondentes **não commitados** (`ocr/reconstruct.py`, `tests/test_tesseract_run008_regressions.py`); a RUN 009 foi executada com eles.

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

### Pós-005 (2026-11-13, commit `dfbf5e9`)

| Item | Arquivo | Mudança | Escopo |
|---|---|---|---|
| T3-P18 | `src/structured_pdf_text/ocr/backends/tesseract.py` | `_tsv_line_slope`, `_unrotate_tsv`, `_tsv_recognized_chars`, `_deskew_page`; `TESSERACT_DESKEW`; identidade `deskew` | só Tesseract |
| T3-P18 | `src/structured_pdf_text/ocr/reconstruct.py` | `_engine_rows` mede extensões no referencial retificado e devolve a inclinação; `ocr_text_column_layout` | só com identidade de linha |
| T3-P18 | `src/structured_pdf_text/layout/ocr_aware.py` | `_order_ocr_lines_by_text_gutters(..., slope)` | só com calhas do motor |
| T3-P18 | `src/structured_pdf_text/tables/ocr_tracks.py` | `_crosses_text_column_gutter(..., slope)` | só com calhas do motor |
| T3-P28 | `src/structured_pdf_text/ocr/reconstruct.py` | `_drop_conflicting_rereads` | só com identidade de linha |
| T3-P30 | `scripts/run_tesseract_batch_wsl.py` | `tesseract_thread_limit`, `round_settings`; threads da config no manifesto e no resumo | executor |
| testes | `tests/test_tesseract_run005_regressions.py` (novo, 12 testes); `tests/test_tesseract_batch_runner_config.py` (+2, expectativa de threads = 8) | — | — |

Avaliadas na RUN 006.

### Pós-006 (2026-11-16, commit `f03710a`)

| Item | Arquivo | Mudança | Escopo |
|---|---|---|---|
| T3-P32 | `src/structured_pdf_text/ocr/backends/tesseract.py` | `_VERTICAL_WORD_MIN_CHARS`, `_VERTICAL_WORD_MIN_ASPECT`, `_tsv_vertical_word_ratio`, `_tsv_reads_vertically`; `_retry_quarter_turns` também dispara quando a maioria das palavras é vertical e prefere a leitura girada com palavras horizontais | só Tesseract |
| T3-P12 | `src/structured_pdf_text/ocr/backends/tesseract.py` | `_TEMP_PNG_COMPRESS_LEVEL = 1` / `_save_temp_png` para o PNG temporário (TSV e OSD) | só Tesseract; sem perdas |
| testes | `tests/test_tesseract_run006_regressions.py` (novo, 9 testes); `tests/test_tesseract_effective.py` (+1) | — | — |
| T3-P31 | `layout/ocr_aware.py`, `text/reading_order.py` | 5 tentativas, **todas revertidas** (arquivos idênticos a `dfbf5e9`) | — |

### Pós-008, T3-P20 tentativas 3–5 (2026-11-17, não commitadas)

| Item | Arquivo | Mudança | Escopo |
|---|---|---|---|
| T3-P20 t5 | `src/structured_pdf_text/ocr/reconstruct.py` | `_LEVEL_BOX_MAX_RATIO = 1.5`; em texto nivelado (caixa típica ≤ 1.5× o corpo estimado pelo avanço por caractere), a regra 1 mede o vão a partir de onde terminaria a descendente de uma caixa mais baixa que a típica (no máximo `0.2·h`) | regiões OCR |
| testes | `tests/test_tesseract_run008_regressions.py` (+2) | — | — |

### Pós-008 (2026-11-17, commit `b1202ee`)

| Item | Arquivo | Mudança | Escopo |
|---|---|---|---|
| T3-P34 t1/t3 | `src/structured_pdf_text/tables/visual.py` | `_observed_rules_bbox`; quando a grade foi endireitada (`source_transform`) e os cantos extrapolados saem da imagem, a contenção usa as réguas observadas (toda a rede dentro de um objeto de imagem); o fragmento mantém a extensão da grade | compartilhado (detecção visual de tabela) |
| T3-P34 t4 | `src/structured_pdf_text/assemble/content.py` | `_quarter_turn_frame`: `_place_visual_tables_between_prose` compara posições no referencial da tabela arredondado ao quarto de volta (giradas inalteradas; a inclinação de escaneamento não é aplicada à prosa nivelada) | compartilhado (montagem) |
| T3-P20 t2 | `src/structured_pdf_text/ocr/reconstruct.py` | `_GAP_JITTER_RATIO = 0.2`; limite relativo da regra de vão `max(1.4·m, m + 0.2·h)` | regiões OCR |
| T3-P32 t5 | `api.py`, `ocr/pipeline/table_integration.py` | **revertida** (nenhuma alteração permanece) | — |
| testes | `tests/test_tesseract_run008_regressions.py` (novo, 6 testes) | — | — |

### Pós-007 (2026-11-17, commit `ad43cb1`)

| Item | Arquivo | Mudança | Escopo |
|---|---|---|---|
| T3-P22 t3 | `src/structured_pdf_text/text/reading_order.py` | `_ends_with_prose_line`, `_RAGGED_PROSE_MIN_CHARS = 20`: calha irregular só ao lado de linha de prosa | compartilhado (nativo e regiões só-OCR) |
| T3-P22 t5 | `src/structured_pdf_text/layout/decorative.py` | `_continues_text_column`: agrupamento que continua uma coluna de texto não é status semântico | compartilhado (layout nativo) |
| T3-P35 | `src/structured_pdf_text/ocr/reconstruct.py` | `_merge_collinear_gutters`, `_MERGE_GUTTERS_MAX_SLOPE`, `_validated_gutters(..., slope)` | só com identidade de linha (Tesseract) |
| testes | `tests/test_native_multicolumn_order.py` (+3), `tests/test_tesseract_run007_regressions.py` (novo, 3) | — | — |

### Pós-006, T3-P32 tentativas 3–4 (2026-11-16, commit `4a732c6`)

| Item | Arquivo | Mudança | Escopo |
|---|---|---|---|
| T3-P32 | `src/structured_pdf_text/ocr/backends/tesseract.py` | `_vertical_reading_in_frame` + `_adopt_better_word_texts`: quando o retry aceita uma leitura girada horizontal, palavras pareadas com uma leitura vertical do mesmo recorte ≥ 5 pontos mais confiante recebem o texto mais confiante; segmentação inalterada | só Tesseract |
| testes | `tests/test_tesseract_run006_regressions.py` (+6) | — | — |

## §4 Testes executados

### Pós-013 (2026-11-24)

- `tests/test_repeated_page_counters.py` (21, novo) + `test_repeated_suppression.py`, `test_furniture_body_protection.py`, `test_candidate_lines_h004b.py` (26): **47 passam** (executados). Discriminância dos negativos verificada desligando cada evidência.
- Suíte completa: ver §1.37 (controles).

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

**Não executado (situação em 2026-11-13, antes da 006)**: nenhuma rodada completa após as correções pós-005; suíte completa não executada.

| 2026-11-16 | RUN 006 completa (manual) | 8/8 com métricas; V9 `needs_review` (T3-P33) |
| 2026-11-16 | benchmarks pontuais de threads/concorrência/PNG (scratchpad, §1.10) | 8 ≡ 4 threads; TSV idêntico; PNG nível 1 sem perdas |
| 2026-11-16 | harness por página (`page_indices`, métricas oficiais `selected_pages`): ANTES (54 págs.) | 49/51 páginas comparáveis idênticas à 006 (V4 p85/p110 dependem do documento) |
| 2026-11-16 | T3-P32 tentativas 1 e 2; T3-P31 tentativas 1–5 (mesmas 54 páginas; a 5ª só no V10) | §2 T3-P32 e T3-P31 |
| 2026-11-16 | validação final: 39 páginas de controle extras + alvos do T3-P32 (V4, V5, V6, V7, V8, V9, V10) | só V9 p20/p55/p71 mudam por efeito do código; diferenças em V4 p25/86/140/150/190, V6 p3/p33, V10 p59 são de contexto de documento (rastreamento: nenhuma decisão de retry diferente da regra antiga) |
| 2026-11-16 | `pytest` direcionado (`test_tesseract_*`, `test_easyocr_pipeline_integrations.py`, ordem de leitura) | todos passaram |
| 2026-11-16 | T3-P32 tentativas 3 e 4 (V3 96–99/116, V4 109–114/164, V6 31, V8 4/22/58, V9 3/4/20/23/36/55/58/71/92, V10 5/13/48) | §2 T3-P32 |
| 2026-11-16 | validação ampla da tentativa 4: conjunto principal (54) e controles (44) repetidos | só V9 p55 muda (melhora); demais idênticas ao código de `f03710a` |
| 2026-11-16 | `pytest tests` (código com T3-P32 tentativa 4) | **13 falhas = exatamente as de T3-P14**; nenhuma falha nova |
| 2026-11-16 | T3-P22: linha de base + tentativas 1 e 2 em 62 páginas (V3 1/20/44/60/80/96; V4 20/25/26/28/30/82/107/108/150; V5 4/10/17/23/30; V6 3/21/31/47/74; V7 1/24/25; V8 4/22/46/58; V9 4/5/8/20/23/43/51/55/71/81; V10 4/5/6/9/13/18/23/39/41/48/50/59/62/73/79) | só V10 p9/p59/p62 e V4 p25 mudam (melhoram); T3-P18, T3-P28 (V8 p4/p58) e T3-P32 (V9 p20/p55/p71) idênticos |
| 2026-11-16 | `pytest` direcionado de ordem de leitura/montagem (6 arquivos) | 218 passaram; suíte completa não executada |
| 2026-11-17 | RUN 007 completa (manual) | 8/8 com métricas; V9 `needs_review` (T3-P33) |
| 2026-11-17 | T3-P22 tentativas 3–5; T3-P35 tentativas 1–2 (páginas em §2 de cada problema; linhas de base no mesmo subconjunto; T3-P35 desligado dentro do processo de teste ao medir T3-P22) | §2 T3-P22 e T3-P35 |
| 2026-11-17 | censo de agrupamentos "status semântico" (modo nativo, só layout, sem gravar saída) | 109 em 94 páginas de V3–V10 |
| 2026-11-17 | `pytest tests` (código final pós-007) | **13 falhas = exatamente as de T3-P14**; nenhuma falha nova |
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

| Data | Execução | Resultado |
|---|---|---|
| 2026-11-17 | RUN 008 completa (manual, código `ad43cb1`) | 8/8 `ok`; V9 `partial` por T3-P33; análise em §1.12 |
| 2026-11-17 | validação pontual pós-008 (§1.13) | T3-P34: 95 páginas; T3-P20: 79 páginas; cada uma com a correção ligada e desligada |
| 2026-11-17 | `pytest tests/test_tesseract_run008_regressions.py` | 6 passed. Os 3 testes de comportamento novo (tabela inclinada com cantos cortados, prosa abaixo da tabela inclinada, variação de caixa no parágrafo) falham sem a correção (verificado); os outros 3 protegem o que não pode mudar (rede de réguas fora da imagem rejeitada, quarto de volta preservado, vão apertado entre parágrafos — falha com a tentativa 1 de T3-P20) |
| 2026-11-17 | testes de tabela/montagem/visual (`-k "visual or place or orphan or content or table"`) e de parágrafos (`-k "paragraph or segment or reconstruct"`) | só falhas de T3-P14 |
| 2026-11-17 | `pytest tests` (código final pós-008) | **13 failed = exatamente as de T3-P14**; nenhuma falha nova |
| 2026-11-17 | instrumentação de `segment_ocr_paragraphs` (28 páginas V4/V9/V10) e simulação de H3/H4 sobre os pares registrados | atribuição de erros por regra (§2, T3-P20) |
| 2026-11-17 | validação pontual T3-P20 tentativas 3, 4 e 5 (§1.14) | 81 páginas dos oito corpora, base `b1202ee` no mesmo contexto |
| 2026-11-17 | `pytest tests/test_tesseract_run008_regressions.py` | 8 passed; os 2 testes novos falham sem a tentativa 5 (um no código de `b1202ee`, outro com o comportamento da tentativa 3) |
| 2026-11-17 | `pytest tests` (código final com T3-P20 t5) | **13 failed = exatamente as de T3-P14**; nenhuma falha nova |
| 2026-11-17 | RUN 009 completa (manual; `b1202ee` + T3-P20 t5 não commitada) | 8/8 com métricas; V9 `needs_review` (T3-P33) |
| 2026-11-18 | auditoria da RUN 009 (§1.15–§1.18): leitura de `run_summary.json`, `run.log`, manifestos e métricas de 008/009; comparação byte a byte por página; diagnóstico de quebras/fusões por alinhamento de palavras; varredura de recall por página 001–009; rubrica 0–100 recalculada para 003–009 (scripts no scratchpad, só leitura) | 66 páginas mudaram (V4 27, V9 19, V10 20); novos problemas T3-P36–T3-P40 |
| 2026-11-18 | testes / extrações | **nenhum executado** na auditoria (só avaliação e documentação) |
| 2026-11-18 | ciclo pós-009: extrações pontuais (harness no scratchpad) | T3-P38: 24 extrações / 337 extrações de página; T3-P36+T3-P39: 26 / 209; nada gravado em `output/` (§1.19) |
| 2026-11-18 | `pytest tests/test_tesseract_*.py tests/test_ocr_parser_regressions.py` (código final) | **413 passed** (inclui os 5 testes novos de `test_tesseract_run009_regressions.py`) |
| 2026-11-18 | `test_tesseract_run009_regressions.py` com as funções de `b1202ee` (plugin no scratchpad, sem alterar o repositório) | os 4 testes de comportamento falham; o de proteção passa nas duas versões |
| 2026-11-18 | módulos relacionados (conservação, tabelas, pipeline OCR, `test_line_detector.py`, `test_rapidocr_improvement_005/006/008/011.py` e outros 11 arquivos) | **323 passed, 11 failed** — as 11 falhas são exatamente as de T3-P14 (8 `test_line_detector`, 3 RapidOCR) |
| 2026-11-19 | auditoria da RUN 010 (só arquivos oficiais) | nenhuma extração |
| 2026-11-19 | T3-P38 t3 — `pytest tests/test_tesseract_run010_regressions.py tests/test_tesseract_run008_regressions.py` (candidata) | 9 passed (1.7 s); o teste novo falha com a função de `95a6f72` (plugin no scratchpad) |
| 2026-11-19 | `pytest tests/test_tesseract_*.py tests/test_ocr_parser_regressions.py` (candidata) | **414 passed** (3.5 s) |
| 2026-11-19 | `test_rapidocr_improvement_005.py`, `test_rapidocr_improvement_011.py`, `test_easyocr_pipeline_integrations.py`, `test_conservation_round.py` | 85 passed, 1 failed (pré-existente, T3-P14) |
| 2026-11-19 | T3-P38 t3 — validação final (43 págs., 10 processos sequenciais) | 1363 s; 43 páginas idênticas byte a byte (§1.21) |
| 2026-11-18 | suíte completa | **não executada** neste ciclo (alterações restritas a tokens Tesseract com identidade de linha e ao pós-processamento Tesseract; cobertas pelos testes direcionados) |
| 2026-11-18 | RUN 011 completa (manual, código `2fdad33`) | 8/8 com métricas; V9 `needs_review` (T3-P33); análise em §1.22 |
| 2026-11-19 | auditoria da RUN 011: `run_summary.json`, `run.log`, manifestos e métricas de 008–011; comparação byte a byte por página; fusões e quebras por alinhamento de palavras (008–011); títulos por tipo de página; rubrica 0–100 recalculada (009–011, reproduz a 009 e a 010) — scripts novos no scratchpad, só leitura | 17 páginas mudaram (V4 15, V10 2), todas só na segmentação; T3-P38 confirmado; novo T3-P41 |
| 2026-11-19 | testes / extrações | **nenhum executado** na auditoria (só avaliação e documentação) |
| 2026-11-19 | ciclo pós-011, T3-P06: extrações de subconjunto (harness no scratchpad, 1 processo OCR por vez) | 43 processos, 297 extrações de página, 5168 s; 104 extrações evitadas por reaproveitamento (§1.25) |
| 2026-11-19 | `test_t3p06_component.py` (scratchpad, entradas reais) | código do repositório: 4 failed, 3 passed, 1 skipped (defeito reproduzido); t3 em memória: **8 passed** |
| 2026-11-19 | suíte relacionada (701 testes: `test_tesseract_*`, montagem, Markdown, revisões, EasyOCR, RapidOCR 005/011, conservação), executada sem e com t2/t3 em memória | repositório, t2 e t3: **700 passed, 1 failed** (pré-existente, T3-P14) |
| 2026-11-19 | T3-P35: verificação numérica da inversão de `_unrotate_tsv` (800 caixas sintéticas) | erro ≤ 0.01 px; nenhuma extração |
| 2026-11-19 | suíte completa | **não executada** no ciclo de diagnóstico (nenhuma alteração no repositório) |
| 2026-11-19 | T3-P06 t3 implementada: `pytest tests/test_tesseract_run011_regressions.py` | repositório **8 passed**; código de `2fdad33` (cópia por `git archive`): 6 failed, 2 passed (proteções) |
| 2026-11-19 | equivalência implementação × candidata (entradas reais capturadas) | 22/22 idênticos |
| 2026-11-19 | integração mínima e controles (harness, 7 processos sequenciais) | V4/V10 com âncora idênticos byte a byte à candidata; só-OCR sem perda; **V5 e V7 inteiros idênticos à RUN 011** |
| 2026-11-19 | `pytest tests` (código com T3-P06 t3) | **3491 passed, 13 failed (exatamente as de T3-P14), 4 skipped, 4 xfailed**; 120 s |


## §5 Próximo ciclo (pós-013)

**Prioridade (decisão do responsável, 2026-11-24)**: **V11**. V3–V10 são controles de regressão. Meta interna: nota ≥ 85/100 em **cada um dos 9 corpora**, com a rubrica §1.16 **inalterada**. Critérios de integridade separados e sempre obrigatórios: nenhuma perda de conteúdo, nenhum dado crítico corrompido, nenhuma duplicação, nenhuma falha grave silenciosa. Situação na 013: V11 **65.9**, V4 66.9, V10 75.5, V9 75.6 abaixo da meta; demais ≥ 86.5.

- **T3-P43 implementado** (§1.37, não commitado): validado offline sobre capturas completas; aguarda a RUN 014 com os critérios revisados abaixo.
- **Próximo candidato recomendado: T3-P44** (§1.36), causa localizada no backend Tesseract; exige autorização e começa pela observabilidade da decisão de releitura.
- (Histórico) Candidato único do ciclo pós-013: **T3-P43** (§1.35), sujeito a autorização para `assemble/repeated_regions.py` (compartilhado).
- **Diagnóstico paralelo, sem código**: captura da V11 p71 (T3-P44), 1 página com OCR, sujeita a autorização.
- **Decisão pendente do responsável**: a linha "Continuação do registro técnico · folha sintética N" (V11, 7 páginas, no gabarito): aceitar o custo de −0.3 ou registrá-la como questão de gabarito.
- **T3-P41** (V4) continua pendente, adiado pela prioridade ao V11.
- **Baseline de comparação da próxima rodada**: `output/tesseract/013/` (commit `b541246`).

### Validação progressiva de T3-P43

A detecção de mobília é **documental** (≥ 3 páginas): cada subconjunto precisa de várias páginas do mesmo documento. Sem OCR concorrente; páginas nativas e saídas da 013 reaproveitadas quando a equivalência for comprovada.

| Nível | Conteúdo | Custo estimado |
|---|---|---|
| 0 | feito (§1.34–§1.35): causa no código, 2 contrafactuais oficiais, triagem de falsos positivos nos 9 corpora | 0 extrações |
| 1 | testes no padrão de `tests/test_repeated_suppression.py`: contador "· N"; "Folha N/M"; deslocamento k ≠ 0; contador em só 2 páginas (não confirma); data ou ID variáveis (não); identificador com hífen (não); "Controle de página" (veto); número constante (literal, comportamento atual); variantes OCR "·/:/-". Mais `StructuredPage` capturadas de páginas nativas | segundos, 0 OCR |
| 2 | V11 p2, p3, p4 (cabeçalho e rodapé nativos), p15 (risco "Continuação") | 1 processo, sem OCR, < 1 min |
| 3 | **Desenvolvimento**: V11 p2–p4, p13 (sem cabeçalho), p15, p21, p40, p51, p60 (raster) + V10 p1, p3, p4 (variante sem "\|") = 12 páginas | 2 processos sequenciais, ≈ 4–6 min |
| 4 | **Desenvolvimento**: o conjunto acima. **Estabilidade**: V7 p1–p8 (mobília já suprimida), V5 p1–p6 ("Controle de página"), V3 p96, p97, p134, V4 p35, p36, p190, p194, p196, p200 (números de página dentro de identificadores do gabarito). **Generalização** (fixada agora, fora do ajuste): V10 p20–p27 (não inspecionadas) e V11 p100–p117 nativas e p68–p78 raster não usadas. ≈ 48 páginas | ≈ 6 processos sequenciais, ≈ 12–18 min |
| 5 | RUN 014 completa, executada pelo responsável | — |

- **Limitações**: nenhum corpus tem numeração com deslocamento ≠ 0, romana ou por seção. Isso fica coberto só por testes unitários, e a estabilidade não prova generalização. Subconjuntos não reproduzem exatamente a confirmação documental: a prova final é a RUN 014.

### Critérios da RUN 014 (revisados pela §1.37)

Os critérios abaixo da linha "originais" foram propostos antes da implementação, com base no contrafactual ideal. A candidata segura **não atinge três deles** (V11 nota ≥ 70.5, ≤ 10 linhas restantes, `numeric` fp ≤ 80) porque preserva a mobília sem evidência estável. A revisão substitui esses três pelos valores medidos da candidata e **precisa de aprovação do responsável**. A extração é determinística (V3–V10 idênticos byte a byte entre 012 e 013), então a RUN 014 deve reproduzir as reproduções offline:

- **V11**: nota 70.4 (± 0.1); `text_coverage_recall` = 0.967214 (igual à 013); ≤ 18 linhas de mobília; `numeric` fp ≤ 100; "Continuação…" presente nas 7 páginas; 0 linhas do gabarito removidas.
- **V10**: nota 78.3 (± 0.1); recall ≥ 0.9937 (queda de 0.000125 explicada como artefato de tokens); ≤ 12 rodapés restantes (as inclinadas); `numeric` fp ≤ 31.
- **V3–V9**: conforme a tabela de controles da §1.37 (idênticos byte a byte onde a reprodução foi idêntica).
- **Integridade**: lista de supressões novas igual à da §1.37; nenhum aviso novo.

Critérios originais (pré-implementação):

- **T3-P43, V11**: ≤ 10 das 185 linhas de mobília restantes; nota ≥ 70.5; `text_coverage_recall` ≥ 0.964; `numeric` fp ≤ 80.
- **T3-P43, V10**: ≥ 60 das 65 linhas "Folha NN/84" removidas; nota ≥ 78.5; recall ≥ 0.9925 (queda prevista de 0.0008 por casamento de tokens, sem perda real).
- **Controles**: V3 e V5–V9 idênticos byte a byte; V4 idêntico, ou toda diferença explicada página a página.
- **Integridade**: nenhuma linha de corpo suprimida (lista de linhas suprimidas auditada por página); nenhum aviso novo; `unaccounted_content` sem aumento.
- **Rubrica**: nenhum documento abaixo da 013 (V11 65.9; V4 66.9; V9 75.6; V10 75.5; demais iguais).
- **Gerais**: os da §5.0g (T3-P38/T3-P20, T3-P06 e T3-P42 mantidos) e a tabela "Critérios gerais para correções futuras" abaixo.

## §5.0g (histórico) Próximo ciclo pós-012 — prioridade ao V4, substituída pela prioridade ao V11 (§1.35). O protocolo, os controles e os critérios gerais abaixo continuam em vigor

**Meta interna de qualidade (definida em 2026-11-19, §1.31)**: nota ≥ 85/100 em cada um dos 8 corpora, pela rubrica §1.16 **inalterada**. A meta não autoriza mudar pesos, limiares, métricas, gabaritos ou corpora. **Critérios de integridade, separados e sempre obrigatórios**: nenhuma perda de conteúdo, nenhum dado crítico corrompido, nenhuma falha grave silenciosa; uma correção que suba a nota violando qualquer um deles é rejeitada. Prioridade: V4 (66.9); V9/V10 como controles neste ciclo; T3-P35 fora.

- **Candidato único do próximo ciclo (§1.31)**: **T3-P41** (parágrafos nativos densos), sujeito a autorização para `assemble/content.py`. Validação na Fase 6 da §1.31.

- **Situação**: a RUN 012 confirmou T3-P06 só em parte (§1.28: recall e caminho nativo confirmados; precisão do V4, categorias novas de falso título e hierarquia pendentes) e confirmou T3-P42. Nenhuma correção pendente de confirmação. **A RUN 013 não deve ser executada** até que um ciclo de correção produza uma candidata validada; os critérios específicos serão escritos aqui, antes da execução, pelo ciclo que a produzir.
- **Baseline de comparação da próxima rodada**: `output/tesseract/012/` (commit `18ccfb0`).
- **Problemas recomendados na §1.30** (1º T3-P35, 2º T3-P06 t4): **substituídos pela prioridade ao V4** definida pelo responsável em 2026-11-19 (§1.31). T3-P35 fica fora deste ciclo; T3-P06 t4 só com decisão separada.
- **Critérios mínimos de qualquer RUN 013** (somam-se aos específicos):
  - os da tabela "Critérios gerais para correções futuras" abaixo;
  - T3-P38/T3-P20 mantidos: V4 fusões ≤ 415 e quebras ≤ 249 (script da §1.22), `paragraph_detection_f1` V4 ≥ 0.488, V9 ≥ 0.605, V10 ≥ 0.496, salvo se o alvo for segmentação;
  - T3-P06 mantido: `heading_recall` V4 ≥ 0.562, V9 ≥ 0.457, V10 ≥ 0.633; nenhum título nativo alterado; T3-P42: recall de V10 p5/p11/p31/p62 sem queda;
  - rubrica: nenhum documento abaixo da 012 (V4 66.9, V9 75.6, V10 75.5; demais iguais à 012).

### Protocolo de validação progressiva (obrigatório a partir do ciclo pós-010)

| Nível | O que é | Quando avançar | Custo de referência (pós-010) |
|---|---|---|---|
| 0 — Diagnóstico sem extração | causa raiz, componente e dependentes a partir das saídas, métricas e auditorias **já existentes**; hipótese verificável; o que deve melhorar e o que não pode piorar; teste mínimo capaz de refutar | hipótese e teste de refutação definidos | 0 extrações |
| 1 — Componente | teste direto do componente com **entradas reais** capturadas (linhas, caixas, tokens, blocos) mais testes sintéticos complementares; o teste do defeito deve **falhar no código anterior e passar na candidata** | sem perda de conteúdo, sem corrupção de valores, sem mudança inesperada de tokens; se a candidata alterar etapas anteriores à entrada capturada, ir para integração real | segundos |
| 2 — Integração mínima | 2–5 páginas: alvo + ≥ 1 controle negativo + caso de fronteira; base e candidata no **mesmo** subconjunto e contexto | nenhuma dimensão importante pior (CER/WER, recall/precisão, perdas e duplicações, dados críticos, tabelas, listas, títulos, parágrafos, ordem) | 1–2 processos |
| 3 — Intermediária | 8–12 páginas diversas, escolhidas pelo alcance real da mudança (T3-P38: obrigatório V4 com fusões e páginas que melhoraram) | ganhos consistentes; não ajustar a regra a páginas específicas | poucos processos |
| 4a — Estabilidade (atualizado no pós-011) | páginas **fora** do alcance da mudança, que devem sair **idênticas byte a byte** (suíte fixa abaixo). Verificam ausência de efeito colateral; **não** são evidência de que a correção generaliza | só para o candidato final; qualquer diferença precisa de explicação pelo mecanismo, senão reprovar | o mínimo de processos; reaproveitar saídas quando o código do caminho não mudou |
| 4b — Generalização (novo no pós-011) | páginas **dentro** do alcance da mudança (mesma família, mesmo mecanismo), **nunca vistas** no desenvolvimento, escolhidas e registradas antes da implementação, com a **direção esperada** de cada uma (melhora / igual / risco); estratificadas por família afetada (ex.: coluna única × duas colunas; nivelada × inclinada; raster × mista; corpus diferente do alvo) | a candidata melhora a maioria das páginas onde se espera melhora, sem perda de conteúdo ou dados; se falhar, registrar e reabrir, sem reajustar sobre essas páginas | ~10 processos sequenciais (somados à 4a) |
| 5 — RUN completa | só o responsável executa; `RESOLVIDO` exige confirmação nela | — | — |

**Regras operacionais**:
- Reaproveitar resultados (rodadas, métricas, auditorias, extrações pontuais com o **mesmo** código, configuração e contexto) antes de extrair.
- **Um processo OCR por vez** (12 processos com 4 threads OpenMP cada praticamente pararam a máquina no pós-009).
- Logs no scratchpad, nunca no contexto.
- Interromper ao primeiro sinal de perda de conteúdo, corrupção de valores, piora significativa de ordem ou regressão estrutural injustificada.
- Para cada tentativa, registrar: tempo de testes, tempo de extração, páginas, processos, métricas antes e depois, decisão e extrações evitadas.
- O limite de 5 tentativas por problema continua, sem reinício de contagem.

### Controles de estabilidade × generalização (lição da RUN 011)

**O que a RUN 011 mostrou**:
- As 43 páginas da validação final do pós-010 saíram idênticas byte a byte, e as 17 páginas que mudaram na RUN 011 estavam **todas** no conjunto de desenvolvimento. Os 43 controles eram, na prática, de **estabilidade**: confirmaram a ausência de efeito colateral, mas não deram nenhuma evidência independente de generalização.
- A previsão da rodada completa veio do **reaproveitamento** dos Níveis 2–3 (V4 −18 fusões / +4 quebras; V10 −1 / −1) e foi exata. Neste caso o alcance da mudança era fechado e enumerável (as páginas alteradas pela t2 na 009), então não havia páginas novas dentro do alcance. **Isso precisa ser declarado** no registro do ciclo, em vez de apresentar os controles como prova de generalização.

**Regras**:
1. **Três conjuntos disjuntos, registrados antes da implementação**: D (desenvolvimento: Níveis 0–3), S (estabilidade, 4a) e G (generalização, 4b). Uma página vista em D nunca entra em G no mesmo ciclo.
2. **Uma página de G usada para decidir uma tentativa passa a D** para as tentativas seguintes do mesmo problema. Para a próxima tentativa, sortear novas páginas de G na mesma família.
3. **Alcance**: antes de escolher G, delimitar o alcance da mudança (que caminho do código, que famílias de página). Se o alcance for fechado e já totalmente coberto por D, escrever "G vazio: alcance coberto por D" e justificar.
4. **S fixa e reaproveitável** (não precisa ser sorteada a cada ciclo; protege correções anteriores):
   - V3, V5, V6, V7, V8: amostra nativa (V3 p1, p20, p44; V5 p3, p10, p30; V6 p11, p21; V7 p1, p24; V8 p4, p46, p49, p58, p67);
   - tabelas: V9 p20, p55, p58, p66, p71; V10 p54; V4 p145, p150, p164, p167, p168;
   - ordem: V4 p20, p25, p26, p28; V10 p9, p59, p32, p52;
   - parágrafos: V4 p129, p137, p140; V9 p17; V10 p25, p68.
   Quando o caminho alterado não é executado numa página de S, a saída anterior pode ser reaproveitada (regra de reaproveitamento), e o registro diz quais foram reaproveitadas.
5. **G por problema** (exemplos para os candidatos da §1.24; definir as listas finais antes da implementação, excluindo D):
   - **T3-P06**: páginas OCR niveladas com títulos no gabarito, em pelo menos três corpora (V4 OCR de texto de coluna única e de duas colunas; V10 L03–L06; V9 páginas mistas); incluir páginas nativas com títulos (risco de falsos positivos), e nenhuma inclinada na 1ª tentativa.
   - **T3-P35 (inclinadas)**: L01/L02 não usadas em D (há 12; reservar ≥ 4 para G), mais páginas inclinadas de outros corpora (V9 p9, p17, p23, p44, p81; V4 p105–p108, p135, p136; V3 p80).
6. **Reaproveitamento de OCR**: para mudanças posteriores ao reconhecimento (segmentação, layout, ordem, montagem), a leitura Tesseract é a mesma entre base e candidata. Reaproveitar resultados já extraídos com o mesmo código, configuração e contexto; quando houver captura das entradas reais do componente (linhas, caixas, tokens), validar D e G primeiro no Nível 1, sem OCR. Uma ferramenta de cache de TSV por página (chave: hash do PDF, página, parâmetros de raster, versão do Tesseract e configuração) reduziria o custo dos Níveis 2–4. Ela **não** foi implementada; exige aprovação, porque mexe no harness.
7. **Um processo OCR por vez** continua obrigatório.

**O que a RUN 012 mostrou** (complemento às regras acima):
- A validação pontual previu exatamente as decisões de título (77 páginas comparáveis idênticas em conjunto e texto), mas **não** previu: (a) os **níveis**, porque o agrupamento é feito sobre o documento inteiro e o subconjunto tem menos títulos; (b) falsos títulos em famílias dentro do alcance que não foram amostradas (páginas de tabela OCR `shadow` do V4, OCR `heavy_noise`, tabelas raster não detectadas do V9); (c) o efeito da correção de integridade (T3-P42) em páginas inclinadas, que ficaram fora dos controles da mudança por serem "inclinadas e idênticas por construção".
- **Regras adicionais**:
  1. Toda decisão que agrega o documento inteiro (agrupamentos, medianas documentais, repetição entre páginas) precisa de verificação no documento inteiro **antes** da rodada: capturar uma vez a entrada do componente para o documento completo e validar offline, ou trocar o agregado por um local à página.
  2. O G deve cobrir **todas** as famílias de página dentro do alcance (no V4: OCR de texto por condição de degradação e OCR de tabelas por família), não só as famílias-alvo.
  3. Correções de integridade que acompanham uma mudança têm alcance próprio, que deve ser delimitado e controlado (inclusive em páginas fora do escopo da mudança principal).
  4. Toda página de S que mudar precisa ser listada e explicada no registro do ciclo, mesmo sem efeito nas métricas.

### Critérios gerais para correções futuras

| Item | Critério |
|---|---|
| Conteúdo | nenhuma página com queda de `text_coverage_recall` > 0.01 nem de volume de caracteres em relação à rodada de referência; nenhuma perda nova (§1.16, eventos de penalidade) |
| Dados críticos | nenhuma página com queda de acertos de `numeric`/`currency`/`dates`; listar valores alterados por página |
| Mudanças só estruturais | quando a correção não deve mudar texto, comparar a sequência de palavras página a página (deve ser idêntica byte a byte) |
| Parágrafos | relatar quebras e fusões por página (alinhamento de palavras, definição na §1.22); controles obrigatórios: V4 coluna única densa (p83, p85, p99, p125, p129, p137–p143), V4 duas colunas (p98, p118, p122, p128, p130), V4 p140, V9 p17, V10 p25/p37/p68. Referência 012: V4 415 fusões / 249 quebras, V9 7 / 80, V10 46 / 210 (011: 417/278, 7/80, 43/220) |
| Títulos | relatar `tp`/`fp`/`fn` de títulos por página e por tipo de página (nativa, mista, escaneada); nenhuma página com aumento de falsos positivos sem explicação; listar também os títulos que o avaliador casa com baixa semelhança (`heading_text_cer` ≥ 1.0 na página), que escondem falsos títulos (§1.28) |
| Tabelas | V9 p20/p55/p58/p71, V8 p4/p58 (`€ 98,40`), V10 p54 e V4 p145–p184 idênticas, salvo o alvo |
| Ordem | V4 p20/p25/p26/p28 ≤ 0.010; V10 p9/p54/p59 e L04–L06 (≤ 0.104) mantidos |
| Controle | V3, V5–V8 idênticos byte a byte, salvo páginas-alvo declaradas; mudanças em leituras Tesseract de várias chamadas podem atingir documentos nativos (V6 p11, V8 p49/p67 na 010) e precisam ser explicadas |
| Rubrica (§1.16) | nenhum documento perde > 0.5 ponto; o documento-alvo melhora na categoria correspondente |
| Validação pontual | listar todas as páginas do subconjunto no documento, separadas em D (desenvolvimento), S (estabilidade) e G (generalização); páginas de D nunca contam como evidência de generalização |
| Desempenho | dentro do ruído observado (−11% a +14% com saída idêntica) |
| Geral | nenhum `extract_error`/`metrics_error`/`corpus_changed`; V9 continua `partial` por T3-P33, salvo se este for o alvo |

## §5.0f (histórico) Validação esperada na RUN 012 — resultado em §1.28

Comparar 012 × 011. **Resultado**: recall de títulos, caminho nativo, conteúdo (sem perda), parágrafos, tabelas, dados, rubrica e desempenho atendidos; **não atendidos** a precisão de títulos do V4, "nenhuma categoria nova de falso título" e o CER da V4 p104; conteúdo e inclinadas mudaram só por recuperação de conteúdo (T3-P42) e pela remoção de sinais inventados na V4 p157 (§1.28).

- **Código esperado na RUN 012**: `2fdad33` + T3-P06 t3 (`layout/heading.py`, `layout/ocr_aware.py`, `ocr/reconstruct.py`, `tests/test_tesseract_run011_regressions.py`), árvore limpa. **Executada com `18ccfb0`** (conforme).

**Critérios da RUN 012 (T3-P06 t3)** — comparar 012 × 011:

| Verificação | Critério |
|---|---|
| Geral | 8/8 `ok`, `rank_eligible`, mesmas métricas aplicáveis; hashes e avaliador iguais; V9 `partial` só por T3-P33 |
| Conteúdo | em **toda** página dos 8 corpora, multiconjunto de palavras idêntico à 011 (ignorando só os marcadores `#`); nenhuma queda de recall > 0.001; nenhuma página com perda de acertos de `numeric`/`currency`/`dates`/`percent` |
| Ordem | páginas com sequência alterada só podem ser de regiões OCR niveladas e devem ter CER ≤ o da 011 (esperado: V4 de duas colunas melhora, ex.: p82, p92, p98, p100, p116, p118) |
| Fora do escopo | **idênticos byte a byte à 011**: V5 e V7 (já verificados por extração integral); páginas inclinadas (V10 L01/L02, V4 p105–p108/p135/p136, V9 p9/p17/p23/p44/p81, V3 p80); páginas nativas sem regiões só-OCR. Qualquer mudança em V3/V6/V8 só é aceitável em região só-OCR nivelada e com explicação pelo mecanismo |
| Títulos | `heading_recall` V4 ≥ 0.45 (0.335), V10 ≥ 0.55 (0.298), V9 ≥ 0.351; `heading_precision` V4 ≥ 0.95, V10 ≥ 0.70, V9 ≥ 0.50; listar todos os títulos novos por página e classificar os falsos (rodapé decorativo, separador danificado, linha de corpo, fragmento de título); nenhuma categoria nova de falso positivo |
| Regressão conhecida | V4 p118: `paragraph_detection_f1` pode cair (0.103 → ≈ 0.065), com CER melhor; nenhuma outra página com piora de F1 de parágrafos > 0.05 sem explicação |
| Parágrafos (T3-P38/T3-P20) | `paragraph_detection_f1` V4 ≥ 0.421, V9 ≥ 0.595, V10 ≥ 0.459; fusões e quebras pelo script da §1.22 sem piora em V4 coluna única (p83, p85, p99, p125, p129, p137–p143) |
| Tabelas, listas e dados | métricas de tabela e de dados críticos iguais à 011; listas sem piora > 0.005 |
| Rubrica (§1.16) | nenhum documento abaixo da 011; Títulos sobe em V4 e V10 |
| Desempenho | dentro do ruído (−11% a +14%) |

## §5.0e (histórico) Validação esperada na RUN 011 — resultado em §1.22

Comparar 011 × 010. **Resultado**: todos os critérios atendidos (§1.22).

- **Código esperado**: `95a6f72` + T3-P38 t3, a ser commitado pelo responsável (commitado como `2fdad33`).
- **T3-P38 t3**: em `segment_ocr_paragraphs`, o limite relativo da regra 1 volta a `1.4·m`; a compensação da descendente ausente (T3-P20 t5) é mantida.

| Verificação | Critério objetivo |
|---|---|
| Geral | 8/8 com métricas `ok`, `rank_eligible` e as mesmas métricas aplicáveis; nenhum `extract_error`, `metrics_error` ou `corpus_changed`; hashes e políticas do avaliador iguais aos da 010; V9 `partial` só pela p36 (T3-P33) |
| Páginas alteradas | V3, V5, V6, V7 e V8 idênticos byte a byte à 010. No V4, V9 e V10 só podem mudar páginas com regiões OCR, e **só na divisão em blocos**: a sequência de palavras de **toda** página deve ser idêntica à 010. Esperado: V4 nas 27 páginas OCR alteradas na 009 (ex.: p83, p85, p99, p125, p129, p137, p138, p139, p141, p143); V10 p68 e poucas outras; V9 sem mudança |
| T3-P38 | V4 `paragraph_boundary_recall` > 0.5595 (010; esperado próximo de 0.578, valor da 008 antes da regressão) e `paragraph_detection_f1` ≥ 0.410; V4 p129, p137, p138 com `paragraph_detection_f1` ≥ o da 010 (0.400, 0.105, 0.000); V10 p68 ≥ 0.182 |
| Preservação dos ganhos de T3-P20 | `paragraph_detection_f1` documental V9 ≥ 0.590 e V10 ≥ 0.455; V4 p140 com os parágrafos 140.01–140.03 separados; V9 p17 e V10 p25 sem piora |
| Conteúdo e valores | CER, WER, recall, precisão, títulos, listas, tabelas e dados críticos **idênticos** à 010 em todos os corpora (a mudança não toca texto); nenhum aviso novo nos manifestos |
| T3-P36 / T3-P39 (mantidos) | V4 p167/p168, V9 p58/p66, V6 p11, V8 p49/p67 idênticos à 010 |
| Rubrica (§1.16) | nenhum documento abaixo da 010; Parágrafos do V4 sobe |
| Desempenho | dentro do ruído observado (−11% a +14%) |

Comando: `python3 -u scripts/run_tesseract_batch_wsl.py 011` (pré-verificação: `--check-only`). **Não altere `corpus/` durante a rodada.**

## §5.0d (histórico) Validação esperada na RUN 010 — resultado em §1.20

Comparar 010 × 009.

- **Código esperado**: `b1202ee` + T3-P20 t5 + T3-P36 t1 + T3-P39 t1, a ser commitado pelo responsável antes da rodada.
- **T3-P38**: não corrigido; a segmentação de parágrafos é a mesma da RUN 009.
- **Objetivo**: confirmar em rodada completa os resultados pontuais de T3-P36 e T3-P39, sem descarte indevido de valores e sem perda de conteúdo OCR.

| Verificação | Critério objetivo |
|---|---|
| Geral | 8/8 com extração `exit 0`, métricas `ok`, `rank_eligible` e as mesmas métricas aplicáveis da 009; nenhum `extract_error`, `metrics_error` ou `corpus_changed`; hashes de PDF, gabarito e manifesto iguais aos da 009; políticas do avaliador idênticas; V9 `partial` só pela p36 (T3-P33) |
| Páginas alteradas | V3, V5, V6, V7, V8 e V10 **idênticos byte a byte** à 009. No V4 só podem mudar p167/p168 e páginas com tabela OCR cuja regra antiga suprimia algo (esperado: nenhuma outra). No V9 só podem mudar páginas com leituras de chamadas diferentes contidas uma na outra (esperado: p58, p66; qualquer outra página alterada precisa ser explicada pela regra de contenção, só com **remoção** de tokens) |
| T3-P36 | V4 p167 com tabela detectada, recall ≥ 0.50 e CER ≤ 0.25; p168 com recall ≥ 0.45; aviso `suspected_ocr_garble` nas duas páginas (manifesto); V4 `text_coverage_recall` ≥ 0.894 e CER ≤ 0.054 |
| Preservação de conteúdo OCR | nenhuma página de nenhum corpus com queda de `text_coverage_recall` > 0.005 em relação à 009; nenhuma página nova com saída < 50% do volume de caracteres da referência (eventos de penalidade da §1.16 não aumentam; esperado: V4 de 2 para 0 páginas com perda de volume); nenhum aviso `content_suppressed_by_postprocess` sem explicação (esperado: zero) |
| Descarte indevido de valores (T3-P39) | em **toda** página, os acertos (`tp`) de `numeric`, `currency`, `dates` e `percent` ≥ os da 009; nenhum corpus com `numeric`/`currency` documental abaixo da 009; V9 p58 com `currency` 1.000, `numeric` ≥ 0.769 e CER ≤ 0.05; V9 `currency` ≥ 0.962, `numeric` ≥ 0.953, células exatas ≥ 0.601 |
| Controles de tabela | V8 p4/p58 com `€ 98,40` e métricas iguais; V9 p20/p55/p71 idênticas; V10 p54 idêntica; tabelas OCR V4 p145–p184 idênticas, salvo p167/p168 |
| T3-P38 (inalterado) | `paragraph_detection_f1` documental V4 0.410, V9 0.595, V10 0.458 (variação só pelas páginas de T3-P36/T3-P39); nenhuma melhora deve ser atribuída a T3-P38 |
| Rubrica (§1.16) | nenhum documento abaixo da nota da 009; V4 e V9 sem queda em Cobertura e Dados críticos |
| Desempenho | dentro do ruído observado (−11% a +14% com saída idêntica) |

**Ao analisar a 010**, comparar também:
- a lista de avisos dos manifestos (os novos avisos só podem ser `suspected_ocr_garble`);
- os fatos `tesseract_postprocess_suppressed_chars` por página, para conferir que só mobília reconhecida é suprimida.

Comando: `python3 -u scripts/run_tesseract_batch_wsl.py 010` (pré-verificação: `--check-only`). **Não altere `corpus/` durante a rodada.**

### (histórico) Critérios gerais do pós-009

**Etapas**:
1. **Teste de componente** com a geometria ou as leituras reais do caso (sem OCR).
2. **Poucas páginas por tentativa** (alvos + 2–4 controles diretos).
3. **Controles intermediários** só para a tentativa promissora.
4. **Bateria ampla** (todas as famílias afetadas nos 8 corpora) só antes de aceitar a versão final.

Nunca mais de 3 processos de extração simultâneos. Os testes de regressão existentes não são removidos.

| Item | Critério |
|---|---|
| Conteúdo | nenhuma página com queda de `text_coverage_recall` > 0.01 nem de volume de caracteres em relação à rodada de referência; nenhuma perda nova (§1.16, eventos de penalidade) |
| Dados críticos | nenhuma página com queda de acertos de `numeric`/`currency`/`dates`; listar valores alterados por página |
| Mudanças só estruturais | quando a correção não deve mudar texto, comparar a sequência de palavras página a página (deve ser idêntica byte a byte) |
| Parágrafos | relatar quebras e fusões por página (alinhamento de palavras, §1.15); controles obrigatórios: V4 coluna única densa (p83, p85, p99, p125, p129, p137–p143), V4 p140, V9 p17, V10 p25/p68 |
| Tabelas | V9 p20/p55/p58/p71, V8 p4/p58 (`€ 98,40`), V10 p54 e V4 p145–p184 idênticas, salvo o alvo |
| Ordem | V4 p20/p25/p26/p28 ≤ 0.010; V10 p9/p54/p59 e L04–L06 (≤ 0.104) mantidos |
| Controle | V3, V5–V8 idênticos byte a byte, salvo páginas-alvo declaradas |
| Rubrica (§1.16) | nenhum documento perde > 0.5 ponto; o documento-alvo melhora na categoria correspondente |
| Validação pontual | listar **todas** as páginas do subconjunto no documento; incluir controles de cada família afetada (coluna única × duas colunas; nivelada × inclinada; nativa × raster); comparar só variantes no mesmo subconjunto |
| Desempenho | dentro do ruído observado (−11% a +14% com saída idêntica) |
| Geral | nenhum `extract_error`/`metrics_error`/`corpus_changed`; V9 continua `partial` por T3-P33, salvo se este for o alvo |

## §5.0c (histórico) Validação esperada na RUN 009 — resultado em §1.15

Comparar 009 × 008.

| Verificação | Critério |
|---|---|
| T3-P34 | V9 p58 com tabela 8×6 (`cell_content_exact_match` da página ≥ 0.6) e CER ≤ 0.08; V9 p44 ≤ 0.21 e p49 ≤ 0.10; V9 `cell_content_exact_match` documental > 0.582; `numeric`/`currency` do V9 ≥ 0.953/0.962 |
| T3-P20 (t2 + t5) | `paragraph_detection_f1` documental V10 > 0.409, V9 > 0.525, V4 > 0.378; `paragraph_boundary_f1` sem piora; **CER e recall iguais à 008** em V10 (0.141/0.991) e V4 (0.054/0.894); V9 CER ≤ 0.074 (só melhora por T3-P34) e recall 0.990; listas, títulos, tabelas e dados críticos sem piora > 0.005; V9 p17 e V10 p25 sem piora de `paragraph_detection_f1` |
| Controle | V5–V8 idênticos à 008 (páginas nativas); V3 idêntico salvo segmentação de parágrafos OCR; tabelas giradas V9 p20/p55/p71 inalteradas; T3-P22/T3-P35 mantidos (V10 L04–L06 CER médio ≤ 0.104; V4 p20/p25/p26/p28 ≤ 0.010) |
| Desempenho | extração dentro do ruído observado (−11% a +14% com saída idêntica) |
| Geral | nenhum `extract_error`/`metrics_error`/`corpus_changed`; V9 continua `partial` por T3-P33 |

**Código da 009**: `b1202ee` + T3-P20 tentativa 5 e testes **não commitados** (árvore de trabalho).

**Limitações**: T3-P20 vale para todas as regiões OCR (as 79 páginas cobrem V3/V4/V9/V10, mas só a 009 confirma o restante); T3-P34 t4 vale para toda tabela raster com polígonos inclinados (95 páginas cobertas).

Comando: `python3 -u scripts/run_tesseract_batch_wsl.py 009` (pré-verificação: `--check-only`). **Não altere `corpus/` durante a rodada.**

## §5.0b (histórico) Validação esperada na RUN 008 — resultado em §1.12

Comparar 008 × 007.

| Verificação | Critério |
|---|---|
| T3-P22 | V10 p54 ≤ 0.10 com a tabela detectada; V4 p20/p25/p26/p28 ≤ 0.05; V9 p90 `cell_content_exact_match` ≥ 0.85; V10 p9/p59 mantidos (≤ 0.10); V9 p3/p5/p20/p71, V10 p57 e V5 sem mudança; V4 documental CER < 0.061 |
| T3-P35 | V10 L04–L06 (p6, 8, 11, 19, 21, 24, 32, 37, 40, 46, 52, 56, 63, 66, 70, 78, 81, 84): CER do grupo < 0.17 (007: 0.14–0.22 por cenário); V10 documental CER < 0.171 sem queda de recall (≥ 0.991); páginas inclinadas L01/L02 sem mudança; **V10 p32: conferir a reordenação** |
| Controle | V3, V6, V7 e V8 idênticos à 007, salvo páginas com agrupamentos "status" que continuam coluna (censo: V3 15/17/18/24, V6, V7 listados em T3-P22) — conferir que mudanças nelas sejam só de ordem dentro da coluna; V5 sem mudança; métricas de tabela e dados críticos de V4/V9/V10 sem piora > 0.01 |
| Desempenho | extração dentro de ±10% da 007 |
| Geral | nenhum `extract_error`/`metrics_error`/`corpus_changed`; V9 continua `partial` por T3-P33 |

**Limitações**: os subconjuntos cobrem 66 páginas (T3-P35) e 49 (T3-P22 t4–t5); a regra de status semântico e a fusão de calhas valem para todas as páginas, então só a 008 confirma o restante.

## §5.0a (histórico) Validação esperada na RUN 007 — resultado em §1.11

**Correção (pós-007)**: o critério "Controle — V3, V4, V5, V6, V7, V8 e V10 idênticos byte a byte à 006" não valia para as páginas que T3-P22 modificava de propósito (V10 p9, p59, p62 e V4 p25) nem para efeitos indiretos de T3-P22 (V10 p54). O resultado da 007 foi avaliado com essa exceção.


Comparar 007 × 006.

| Verificação | Critério |
|---|---|
| T3-P32 | V9 p20 e p71 com a tabela na orientação de leitura (`cell_content_exact_match` ≥ 0.9 nas páginas); V9 `cell_content_exact_match` documental > 0.527 e `cell_cer` < 0.579; V9 `numeric` ≥ 0.950 e `currency` ≥ 0.962 |
| T3-P22 | V10 p9 CER ≤ 0.15 e p59 ≤ 0.10 com recall ≥ 0.99; V4 p25 < 0.336; V10 documental CER < 0.194 e `paragraph_detection_f1` > 0.372; V10 p23/p39/p73/p79 e páginas nativas de 2 colunas do V4 sem piora; V5–V8 idênticos à 006 salvo ruído de contexto |
| T3-P32 (p55) | V9 p55 com os identificadores 01–04 nas linhas dos prazos e `numeric` da página ≥ 0.97; palavras da coluna "Situação" presentes (ainda fora da grade); V3 p96–p99/p116 e V4 p109–p114 (rotação de página) sem mudança de CER > 0.01 |
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

### Diagnóstico da rodada 009 (detalhe em §1.15 e §1.17)

- **V3**: idêntico à 008; p116 (tabela girada), p138 (página em branco anotada no gabarito), p96, p86.
- **V4**: só segmentação mudou (27 páginas). Perda silenciosa em p167/p168 (T3-P36); fusões em 10 páginas de coluna única (T3-P38); OCR degradado (T3-P21).
- **V5**: idêntico; título e subtítulo ausentes em 12 páginas (T3-P40).
- **V6/V7/V8**: idênticos; V7 p1 e V8 p46/p47 (T3-P05); varreduras do V6 p3/p33/p89 com recall 0.86.
- **V9**: p58 tabela recuperada (T3-P34) com valores corrompidos (T3-P39); p44/p49 em ordem; tabelas lado a lado p14/p62/p72 (T3-P37); ordem nativa p51/p43/p5 (T3-P22); p36 `partial` (T3-P33).
- **V10**: só segmentação mudou (20 páginas); L01/L02 = 56% das edições (T3-P35); títulos (T3-P06); listas (T3-P31).

## §7 Histórico resumido da Série 2 (2026-11-05 a 2026-11-06, executor `run_loop.py`)

| Correção | Arquivo | Decisão |
|---|---|---|
| Fix-F-V9-01 — guarda de altura (> 85%) em `_detect_rectangular_grid` | `tables/detector.py` | aceita (V9 CER 0.4849 → 0.4318) |
| Fix-F-V9-02 — `_is_form_label_colon_pattern` | `tables/text_tracks.py` | aceita (V9 CER → 0.4285) |
| Fix-F-V9-03 — agrupamento em `ocr_tracks.py` | `tables/ocr_tracks.py` | revertida (sem efeito) |
| Fix-F-V9-04 — `_group_short_regions` | `tables/detector.py` | inconclusiva, código preservado |

A hipótese "Tipo A" da Série 2 (V9: Tesseract não reconhece imagens de baixa DPI) foi **refutada** na Série 3 (T3-P01). Notas técnicas mantidas: o avaliador aceita a chave `page_number` do manifesto V9; o limiar de memória do V4 é 11 GB.
