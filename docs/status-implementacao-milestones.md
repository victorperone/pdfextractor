# Status de implementação frente ao plano do MVP

Data da auditoria: 2026-09-13  
Documento de referência: `plano-mvp-extrator-estruturado-texto-pdf.md`

Esta matriz compara funcionalidades executáveis, não apenas a existência de
arquivos ou interfaces. `Parcial` significa que existe uma fatia funcional,
mas ao menos um item explicitamente previsto no milestone ainda não está
coberto de forma geral.

| Milestone | Status | Implementado | Lacunas verificadas |
|---|---|---|---|
| M0 — baseline e corpus | Implementado | CLI, corpus local, runner, tempos, relatório operacional e adaptadores `structured-*`, `pdfium-raw` e PyMuPDF opcional | categorização e casos de ouro crescem junto com o corpus empresarial |
| M1 — native evidence | Implementado | coordenadas, índice/Unicode, fonte/tamanho/peso/ângulo, cores, render mode, flags experimentais, origem, imagens, paths, matrizes/MCIDs, anotações/appearance streams, structure tree, capabilities e dump JSON | ampliar atributos somente quando novas APIs estáveis do PDFium justificarem |
| M2 — reconstrução nativa | Implementado | normalização conservadora, deduplicação, orientação, linhas, espaços, pontuação/glifos de baseline, palavras, ordem nativa e overlay | refinamentos futuros de qualidade não bloqueiam o milestone |
| M3 — complexity analyzer | Implementado | cobertura textual/visual, imagem dominante, Unicode ruim, duplicação, tinta visível, vetores, anotações, tabelas/colunas e relatório de decisão | pesos dos sinais continuam heurísticos e devem ser calibrados em corpus empresarial |
| M4 — layout regions | Implementado | protocolo substituível, engine heurística, render low-res, normalização, atribuição de linhas e overlay | um modelo aprendido pode ser plugado depois, sem mudança de domínio |
| M5 — reading order | Implementado | grafo ponderado de regiões, bandas de largura total, consistência da ordem nativa, colunas somente em prosa, grupos rotacionados e semântica básica | calibrar pesos com novos layouts empresariais |
| M6 — OCR e scans | Implementado | PaddleOCR local, português, render, tokens, coordenadas, confiança, rotações e variantes | modelos alternativos permanecem opcionais pela interface |
| M7 — OCR seletivo e fusion | Implementado | `RegionQuality`, decisão local após layout, OCR de crop, promoção por área/contagem, page OCR, alinhamento, deduplicação, conflitos, proveniência e refinador regional genérico | calibrar thresholds com o corpus empresarial sem alterar a cascata |
| M8 — tabelas determinísticas | Implementado | paths, strict grid, relaxed grid, células, confiança, text tracks sem borda e `ProseVsTableClassifier` com rejeição de prosa e código | calibrar confiança e spans complexos com novas tabelas empresariais |
| M9 — fallback visual | Implementado | interface de engine, backend OpenCV, estrutura visual, OCR de crop, mapeamento de células/spans e rejeição de gráficos/diagramas sem suporte textual | modelo visual aprendido pode substituir o backend sem alterar o pipeline |
| M10 — tabelas entre páginas | Implementado | assinatura, geometria real das bordas, cabeçalhos, trilhas X, largura, tipos de célula, títulos intervenientes, marcadores, fragments, merge e decisões positivas/negativas auditáveis | calibrar pesos para sequências empresariais com mais de dois fragmentos |
| M11 — assembly/renderers | Implementado | documento estruturado, raw/reading text, JSON, Markdown, política de cabeçalho/rodapé e limites de página | renderizações adicionais são evolução pós-MVP |
| M12 — performance/hardening | Implementado | tempos por estágio/estratégia, p50/p95/p99, RSS/pico, evidência nativa opt-in, diagnóstico por página direcionado, aquisições e estimativa FFI, timeout, limites, cache, reuso, batching e multiprocessing por documento | Rust/PyO3 e isolamento permanecem opções futuras condicionadas às medições, como definido no plano |

## Ordem de conclusão das lacunas

1. ~~Refinador OCR genérico por região e integração com tabelas/figuras.~~
2. ~~Adaptadores comparativos do M0.~~
3. ~~Grafo de reading order e consistência nativa do M5.~~
4. ~~Decisão automática por região do M7.~~
5. ~~Text tracks sem borda do M8.~~
6. ~~Explicabilidade adicional do M10.~~
7. ~~Métricas restantes do M12.~~
8. ~~Evidência PDFium adicional do M1 oferecida pela binding atual.~~

Nenhum item acima exige contrariar as decisões do documento original. A etapa
de qualidade adaptativa adicionou `OcrQualityPolicy` (`baseline`, `adaptive` e
`exhaustive`), avaliação ponderada por caracteres, perfil visual, consenso
espacial, tipografia nativa em `TextToken`, listas estruturadas, ordenação de
formulários/figura-caption e classificação conservadora de conteúdo decorativo.
Os diagnósticos dessas decisões ficam em `page.diagnostics.facts`. O corpus de
validação citado no plano não está neste checkout; a validação executada aqui é
sintética e baseada nos testes do projeto. Os backends permanecem substituíveis
e texto nativo confiável continua sendo a evidência principal.
