# Estado da implementação do desacoplamento OCR

Atualizado em 22/10/2026. O objetivo autorizado cobre PR-00–PR-14 do
[plano de desacoplamento](Plan_Decoupling_OCR_Engines.md). Este registro separa
alterações de código de homologação real; pendências externas não são marcadas
como aprovadas.

## Estado por etapa

| Etapa | Código/artefatos | Situação e lacunas verificáveis |
| --- | --- | --- |
| PR-00 — baseline | Capturas, manifests, configurações, comparação semântica e auditoria em `outputs/baseline-20-10-2026/` e nos documentos de baseline. | Capturas locais concluídas, exceto EasyOCR V4, cancelado pelo usuário em 182/224 páginas; o Markdown completo existe em outra máquina e não foi importado. Defeitos conhecidos e limites de avaliação estão registrados. |
| PR-01 — isolamento | Gate AST/imports, processo core sem runtimes e matriz CI Ubuntu/Windows. | Implementado e exercitado localmente; a execução remota Windows ainda é gate de plataforma. |
| PR-02 — contratos | Requests/resultados imutáveis, conversores, identidade, proveniência, adapter legado e serialização. | Implementado com testes de incompatibilidade, finitude, confiança, ownership e idempotência. |
| PR-03 — serviço autorizado | Autorizações centrais, união exata, raster leases, transforms, masking, recursos e validação de saída. | Implementado e testado com PDFium real, CropBox, rotações e negativas antes de render/inferência. O extrator em `src/structured_pdf_text/native/` permaneceu sem alterações. |
| PR-04 — registry/factory | Registry, settings lazy, configuração por engine/provider, factories e CLI. | Integrado; o registry aponta para resolvers dentro de cada pipeline (`easyocr`, `paddle`, `rapidocr`, `tesseract`) e readiness estática não carrega runtimes. A quinta engine fake continua usando resolver genérico. |
| PR-05 — orquestração | `PdfTextExtractor` usa `PageCoordinator` e `OCRPageFacade`; cada inferência passa pelo serviço. Escala/rotação e tiles constam no request autorizado. | Caminho de produção conectado; quinta engine fake percorre CLI, factory, render, autorização e saída. Comparações reais V5 EasyOCR/Tesseract foram executadas e não são equivalentes; detalhes abaixo. |
| PR-06 — policies/integrador | Policies locais de ranking/filtro/consenso numérico, fusão de candidatas, células, tokens e segmentação de figuras. `ProposalIntegrator` aplica regiões, células, dados críticos, figuras e tabelas visuais com snapshots, conservação, ownership, linhagem e replay. Tiles mantêm autorizações distintas e aplicação conjunta. | Os fluxos tipados previstos estão conectados à produção, incluindo os refinamentos dedicados. Somente seleções aceitas alteram os alvos; o helper público `ocr_table_cells(..., ocr_facade=...)` conserva o objeto original. Homologação por corpus continua pendente; isso não aprova equivalência com o legado. |
| PR-07–10 — pipelines | Pipelines/settings/policies locais nas quatro engines; cada engine possui objetos independentes para recuperação, candidatos, integração, células, tokens, fusão, figuras e representação da borda do raster. Estratégias maduras continuam nos backends específicos. Paddle worker usa protocolo 5 negociado com polígonos, proveniência, diagnósticos e falhas tipadas. | Código integrado. As quatro engines concluíram os controles V5. RapidOCR passou operacionalmente após remover bordas sintéticas antes da inferência, mas ainda produz falsos positivos em figuras sem texto. Equivalência com o legado, qualidade e homologação das plataformas continuam abertas. |
| PR-11 — cancelamento | API aceita `cancellation_token`; Paddle e Tesseract podem interromper subprocessos sem deadline de inferência; trata corridas entre cancelamento e criação de processo. Os probes de identidade/healthcheck do backend Tesseract não impõem timeout. O `--list-langs` do preflight estático mantém orçamento diagnóstico opcional de 10 s e retorna `UNKNOWN`; não participa da extração. | Código e testes com executáveis/workers controlados implementados. Windows nativo e limitação de cancelamento durante chamadas EasyOCR/RapidOCR in-process continuam sem validação de plataforma. Prazos curtos de limpeza após kill permanecem. |
| PR-12 — Paddle v6 | Somente `pt`, `pt-v6-medium` e `pt-v6-small` ativos; `pt-v5` rejeitado. Worker deixa de importar o arquivo local `ocr/paddle.py` como se fosse o pacote Paddle, corrigindo sua inicialização por filename. | Testes e E2E medium válidos. Smoke de célula real passou em valor negativo, identificador, integração e linhagem; falhou em acento porque reconhece `ÃO` em lugar de `AÇÃO`, tanto direto quanto worker. Paridade em células foi comprovada por smokes reais direto/worker e teste do protocolo com runtime substituído; relatórios e falhas semânticas ficam preservados. Windows continua sem homologação. |
| PR-13 — RapidOCR v6 | Resolução explícita medium/small, manifesto/hash por estágio, setup, verificador, smoke e runners Bash/PowerShell atualizados. | Hashes dos quatro artefatos por tamanho verificados e charset português presente. Smokes medium e small passaram com ONNX Runtime e OpenVINO 2026.4.1 nos controles de acentos, valor, identificador e número. E2E real em V5 página 19 passou nas quatro combinações: medium/small × ONNX/OpenVINO; todos `valid`/`success`, com artefatos e providers identificados por hash/versão durante a execução. OpenVINO 2024.4.0 falhou ao importar o reconhecedor v6 (erro ONNX `Reshape`), validando a necessidade do novo pin. A [documentação upstream de suporte v6](https://github.com/RapidAI/RapidOCRDocs/blob/main/docs/blog/posts/inference_engine/support_PP-OCRv6_rec_engines.md) demonstra OpenVINO 2026.2.1. Instalação limpa Windows, comparação controlada de modelo e geometria/corpus ainda pendentes. |
| PR-14 — aceite | Gate AST inclui também os consumidores fora de `ocr/`; quinta engine exercitada via CLI, guia de extensão e ADR publicados. | Consolidação de código avançou; aceite permanece pendente por divergências de corpus, smokes, dependências, defeitos P0 e plataformas. Não declarar release aceita antes disso. |

## Política de bordas do raster — 22/10/2026

O incidente de geometria RapidOCR foi tratado com uma mudança funcional local,
sem alterar o extrator nativo nem afrouxar a autorização. Cada engine possui
`RasterBoundaryPolicy` própria. EasyOCR, Paddle e Tesseract preservam `mask`;
RapidOCR seleciona `trim`.

Em `trim`, o serviço conserva somente pixels cujo footprint inteiro pertence
ao crop autorizado, remove a borda que antes era branca e recalcula o frame
para o retângulo efetivamente entregue. Valida esse retângulo antes da
publicação. Sem pixel completo, a operação falha antes da inferência. Tokens
externos continuam rejeitados; não há clipping de saída nem tolerância extra.
O custo do raster original e das cópias continua reservado antes da alocação;
o lease final libera a diferença de memória.

- **222 testes focados passaram**, incluindo detecções na borda, negativas de
  output externo, quatro rotações, origem deslocada, CropBox real, igualdade
  dos pixels conservados, roundtrip de coordenadas, pixel insuficiente e
  liberação de reservas. XML: `pytest-raster-boundary-11.xml`.
- Suíte integral: **2.775 passed, 5 skipped e 4 xfailed**, em **247,043 s**.
  XML: `outputs/validation-ocr-decoupling-20261022/pytest-raster-boundary-full-12.xml`.
  Gate de imports, `compileall` e `git diff --check` passaram.
- RapidOCR medium ONNX, V5 **19/21/24/31**, run
  `v5-rapidocr-medium-trim-20261022-11`: `valid` em **208,9 s**, sem falhas de
  autorização. Medium OpenVINO, V5 **21**, run
  `v5-rapidocr-medium-openvino-trim-20261022-11`: `valid` em **16,1 s**, também
  sem falhas de autorização. Fontes/hashes: `source-raster-boundary-11/`.
- A correção geométrica **não aprova qualidade**: ambos os providers reconhecem
  `D` com confiança aproximada de **0,612** na figura sem texto V5-21. ONNX
  também produz texto espúrio em V5-31. O controle negativo permanece
  **reprovado** em `rapidocr-negative-figure-control-11.json`.
- A comparação RapidOCR anterior/posterior registra **509 diferenças**,
  Markdown distinto e zero erros de identidade. É uma mudança funcional de
  raster, não uma equivalência estrutural aprovada. Linhas nativas por região
  permanecem idênticas nos quatro controles. Relatório:
  `comparisons/rapidocr-raster-boundary-11.json`.
- Os smokes de célula RapidOCR passaram nas quatro combinações
  **medium/small × ONNX Runtime/OpenVINO**, verificando acentos, valor negativo,
  identificador, integração e linhagem. Relatórios: `cells/*-trim-11.json`.
- O controle Tesseract, run `v5-tesseract-boundary-regression-20261022-11`,
  terminou `valid` em **4,8 s**. Comparado com `figure-local-policies-05`, mantém
  Markdown, conteúdo, geometria, estrutura e linhas nativas; as **84**
  diferenças são somente diagnósticas, sem erro de identidade.
- A auditoria de dependências preserva as exigências efetivas dos pacotes:
  EasyOCR 1.7.2 exige `opencv-python-headless`, RapidOCR 3.9.2 exige
  `opencv_python` e PaddleX 3.7.2 com extra OCR exige `opencv-contrib-python`.
  Só a distribuição contrib 4.10.0.84 está instalada. `pip check` continua
  reprovado; não foi alterado metadata upstream nem instalado outro pacote
  sobre o mesmo módulo `cv2`. Evidência: `opencv-dependency-audit-11.json`.

## Validação e consolidação anterior — 22/10/2026

- Dados críticos usam `TokenTarget` e composição explícita `join_critical`.
  O integrador valida a ordem das evidências selecionadas, conserva sinais e
  valores, preserva a posição no stream e registra IDs derivados e linhagem.
  Replay com composição ou ordem diferentes é rejeitado.
- `FigureRefinementTarget` aplica stream da página e região de figura juntos.
  `VisualTableTarget` valida o draft central contra evidências observadas,
  geometria, ocupação da grade, ownership e conteúdo anterior antes de mutar a
  tabela. Rejeições não deixam alterações parciais nem tentativas diferidas.
- As policies de fusão de candidatas e segmentação de figuras são instâncias
  locais das quatro engines. Os testes alteram somente a policy EasyOCR e
  conferem que as demais mantêm resultados. O núcleo valida os segmentos;
  eles não podem ampliar o crop nem se sobrepor. A atribuição regional de
  produção não repete a integração tipada por uma segunda regra de overlap.
- O gate de dependências agora cobre API, nativo, tabelas, fusão, renderers e
  diagnósticos, além de `ocr/`. CLI/setup e aliases legados de configuração
  continuam fronteiras de composição explícitas. O import sem uso de perfil
  Paddle saiu de `api.py`; o descritor RapidOCR anuncia `pp-ocrv6-medium`.
- A suíte anterior, `pytest-critical-figure-table-04.xml`, terminou com
  **2.732 passed, 6 skipped e 4 xfailed** (215,024 s). A validação focada atual
  de contratos, serviços, policies e geometria passou **274 casos** em
  `outputs/validation-ocr-decoupling-20261022/pytest-figure-policies-02.xml`.
  O gate ampliado passou separadamente em `pytest-architecture-04.xml`.
- A suíte completa de 22/10, `pytest-decoupling-05.xml`, passou com
  **2.749 passed, 6 skipped e 4 xfailed**, em **167,665 s**. A auditoria do
  avaliador passou **39 casos** em `pytest-evaluator-audit-06.xml`.
  Após corrigir o falso skip de pré-processamento, a suíte integral final
  `pytest-decoupling-08.xml` passou com **2.750 passed, 5 skipped e 4 xfailed**,
  em **167,283 s**. As fontes finais estão em `source-final-08/`; seu único
  delta para as fontes das capturas `05` é o teste corrigido.
- Os quatro controles V5 **19/21/24/31**, sob os run IDs
  `v5-*-critical-figure-table-20261021-04`, terminaram operacionais para
  EasyOCR (**827,6 s**), Paddle (**386,3 s**) e Tesseract (**6,6 s**).
  RapidOCR medium ONNX terminou **partial** em **285,8 s**, por V5-21.
  Essas capturas precedem a policy local de figuras; suas fontes estão
  preservadas em `source-critical-figure-table-04/` no namespace de 21/10.
- Comparações com o estágio `token-target-worker-v5-02` preservaram Markdown,
  conteúdo, geometria e estrutura nos controles EasyOCR e Tesseract. Houve
  **52** e **96** diferenças apenas de decisões/diagnósticos, respectivamente.
  Contra o legado permanecem **1.583** e **1.001** diferenças, Markdown distinto
  e zero erros de identidade. Os relatórios exatos estão em
  `outputs/validation-ocr-decoupling-20261022/comparisons/`; equivalência segue
  reprovada, sem relaxar campos do comparador.
- A reprodução RapidOCR `v5-rapidocr-geometry-20261022-01` confirmou a causa
  da parcialidade: o detector retorna um box com `y0=280.6299235026042`, fora
  do crop autorizado que começa em `280.6300048828125`. O serviço rejeita o
  resultado, conserva o incidente e a execução continua parcial. O erro agora
  inclui bbox e união autorizada para investigação; a validação não recebeu
  tolerância nem clipping de evidência inválida.
- A captura atual Tesseract `v5-tesseract-figure-local-policies-20261022-05`
  terminou `valid` em **5,4 s** nos quatro controles. Comparada com `04`, mantém
  Markdown idêntico e zero diferenças de conteúdo, geometria ou estrutura;
  as **84** diferenças são diagnósticas. Fontes e hashes atuais estão em
  `outputs/validation-ocr-decoupling-20261022/source-figure-local-policies-05/`.
- EasyOCR `v5-easyocr-figure-local-policies-20261022-05` também concluiu os
  quatro controles em **584,8 s**, balanced/adaptive, `valid`/`success`/`clean`.
  Comparado a `04`, o Markdown é idêntico e as **44** diferenças são somente
  diagnósticas, com zero erros de identidade. A comparação com a baseline
  continua reprovada com **1.583 diferenças** e Markdown distinto; não houve
  correção de qualidade que justificasse aprovar o gate legado.
- `compileall`, gate de imports e `git diff --check` passaram. O ambiente
  continua reprovado em `pip check`: EasyOCR exige `opencv-python-headless`
  e RapidOCR exige `opencv-python`, ambos ausentes como distribuições. Não foi
  instalada uma segunda variante de `cv2` para esconder essa inconsistência.
- O [guia de extensão](OCR_Pipeline_Extension.md) e a
  [ADR de integração autorizada](adr/0001-authorized-ocr-pipelines.md)
  documentam a composição e as fronteiras atuais. O diretório `native/`
  continua sem alterações em relação ao commit anterior à migração.
- A revisão dos skips encontrou uma classificação incorreta no teste de
  pré-processamento EasyOCR: imagem uniforme pode legitimamente gerar zero
  variantes porque todas repetem a entrada. O teste agora verifica isso e
  confere variantes distintas em imagem variada, sem transformar falha em skip.
  Os **37 casos** do arquivo passaram em `pytest-preprocessing-skips-07.xml`.
  Os demais skips dependem de fixtures/visual engine ainda não fornecidos;
  quatro xfails de consenso são especificações de evolução futura.
  Essa correção posterior ao snapshot `05` altera somente testes, sem mudar
  o código executado pelas capturas atuais.

## Histórico — células, tokens e worker

- A suíte integral com as policies locais regionais/células e integração de
  tiles passou: **2.704 passed, 6 skipped e 4 xfailed**, em 107,345 s.
  XML: `outputs/validation-ocr-decoupling-20261021/pytest-typed-cell-local-policies-01.xml`.
  Após adicionar `TokenTarget`, notas/linhas fracas, helper público e protocolo
  Paddle 5, a suíte integral `pytest-token-target-worker-v5-02.xml` passou:
  **2.719 passed, 6 skipped e 4 xfailed**, em 161,570 s. Uma guarda adicional
  preserva tokens que só intersectam parcialmente o crop de um refinamento
  opcional. A suíte focada passou **131 casos**; a suíte integral final
  `pytest-token-target-worker-v5-03.xml` passou com **2.720 passed, 6 skipped
  e 4 xfailed**, em 166,170 s. Os hashes e fontes finais ficam em
  `source-token-target-worker-v5-03/`; o delta após iniciar os E2E `02` limita-se
  à guarda de geometria e ao seu teste, sem mudança de reconhecimento.
- Testes de produção da fachada cobrem: células com sinal/zeros e linhagem;
  render exato e escalas reais; nota de rodapé sem render externo;
  substituição de linha fraca com falha recuperada; rejeição de perda de
  conteúdo; descarte de tentativas não selecionadas; alvo alterado/oculto;
  duas autorizações de tiles aplicadas juntas ou rejeitadas sem mutação parcial.
- A mudança de calibração de ranking de EasyOCR é testada deixando os
  resultados das outras engines iguais. Os objetos de policy de cada engine
  são distintos. O mecanismo comum não seleciona calibração por nome.
- Os smokes reais de células passaram para Tesseract, EasyOCR e RapidOCR nas
  quatro combinações medium/small × ONNX Runtime/OpenVINO. Evidências:
  `outputs/validation-ocr-decoupling-20261021/cells/`.
- Paddle worker inicialmente falhou com `module 'paddle' has no attribute
  'device'`: o diretório do script precedia site-packages e carregava o módulo
  de compatibilidade local. A correção retira esse diretório do caminho de
  imports. O erro genérico agora conserva causa, estágio e página na fronteira
  do worker. A inicialização e inferência reais passaram depois da correção.
  O protocolo 5 exige confirmação de versão na inicialização; versões antigas
  não são aceitas silenciosamente. Testes do worker/backend reais, com runtime
  substituído, verificam polígonos, nível, proveniência, contadores, scores e
  erros fatais. Os testes antigos de protocolo continuam úteis, mas suas
  cópias inline não são utilizadas como evidência dessa nova paridade.
- A comparação real `cells/paddle-medium-parity-07.json` comprovou igualdade
  de fixture, texto, tokens, geometria, confiança, ownership, proveniência,
  linhagem normalizada, identidade de pesos, diagnósticos e trabalho agregado
  entre direto e worker. Apenas IDs correlacionados por execução e custos
  temporais foram normalizados; o runtime foi conferido por transporte.
- O critério de acentos do Paddle permanece **reprovado**, mesmo após corrigir
  o transporte: direto e worker reconhecem `ÃO R$ -42,90 ID AB-009`. Os relatórios
  de falha são preservados; igualdade entre transportes não aprova esse smoke.
- E2E V5 páginas **19, 21, 24 e 31**, run IDs
  `v5-easyocr-typed-cell-local-policies-20261021-01` (553,4 s) e
  `v5-tesseract-typed-cell-local-policies-20261021-01` (5,2 s): extrações
  `valid`, sem erro de identidade na comparação com a baseline. A comparação
  exata continua **não equivalente**: EasyOCR 1.583 diferenças e Tesseract
  1.001, com Markdown diferente. A paginação do comparador agora inclui
  contagens e exemplos por categoria/página, preservando todos os critérios
  anteriores; não esconde diferenças tardias quando os exemplos globais lotam.
  Em Tesseract há mudanças de agrupamento das tabelas raster e correção de
  `degraded` para `no_text` em figuras vazias; isso ainda requer aceite
  estrutural explícito. EasyOCR continua emitindo falsos positivos em V5-31.
- As capturas dos mesmos quatro controles, após `TokenTarget`, terminaram sob
  `v5-*-token-target-worker-v5-20261021-02`: EasyOCR **589,0 s**,
  `valid`/`success`/`clean`, e Tesseract **6,4 s**, `valid`. A repetição EasyOCR
  mantém **1.583 diferenças exatas** para a baseline, Markdown diferente e
  zero erros de identidade; a qualidade permanece bloqueada. A comparação
  anterior/posterior à migração de `TokenTarget` preserva **Markdown idêntico**
  nos dois motores, com diferenças apenas em diagnósticos/decisões (104 no
  EasyOCR e 234 no Tesseract), sem diferença de conteúdo, geometria ou estrutura.
  Essa comparação incremental não aprova a equivalência com o código legado. O código/fontes, scripts, testes
  e dependências usados foram preservados com hashes em
  `outputs/validation-ocr-decoupling-20261021/source-token-target-worker-v5-02/`.
  Não há alterações no extrator `src/structured_pdf_text/native/`.
- Tesseract também concluiu V3 **128/137/141** e V4 **109–111/139/149/172**
  com status `valid` (0,4 s e 33,5 s). `valid` é estado operacional, não
  aprovação semântica: V3 reproduziu o vazamento conhecido dos valores
  subjacentes às tarjas. Esse caminho não pediu OCR e a detecção experimental
  de oclusão estava desativada na configuração capturada. O defeito continua
  bloqueador de release, sem mudança no extrator nativo. Os controles V4
  foram capturados para revisão de orientação e sinais; não são homologação
  automática. Tesseract V5 após `TokenTarget` terminou novamente `valid` em
  6,4 s, com as mesmas 1.001 diferenças exatas e zero erros de identidade.
  A comparação Tesseract V4 registrou **3.873 diferenças**, Markdown diferente
  e zero erros de identidade. V3 registrou 257 diferenças e não é comparável
  como execução OCR por faltar identidade efetiva nessa amostra sem OCR;
  o comparador recusou aprová-la. Esses relatórios também foram preservados
  em subdiretórios `comparisons/`.

## Histórico de validação anterior nesta sessão

- Após conectar a seleção regional ao integrador tipado, os testes de contratos,
  serviços, escalas adaptativas, figuras/policies e ownership passaram novamente
  (um caso condicional permaneceu skip). O teste do caminho real de
  `_recover_selected_regions` cobre região de texto e figura com duas variantes:
  somente a selecionada é aplicada, e a figura emite `associate_figure`. Essa
  validação focada não substitui a repetição da suíte integral nem o corpus após
  essa última conexão.

- Baseline completo continua preservado; EasyOCR V4 permanece a exceção
  autorizada e parcial descrita em
  [`OCR_Decoupling_Baseline_2026-10-20.md`](OCR_Decoupling_Baseline_2026-10-20.md).
- A captura EasyOCR V5 pós-desacoplamento nos controles 19/21/24/31 registrou
  identidade efetiva e saída nas páginas 19, 24 e 31. A página 21 foi repetida
  isoladamente após corrigir o estado de figura vazia: `benchmark_status=valid`,
  `ocr_outcome=no_text`, zero tokens OCR, sem warnings e com texto nativo
  preservado. A captura combinada anterior foi parcial por causa da classificação
  antiga de página 21; não é reportada como execução válida.
- Os testes de escala/rotação confirmam render autorizado em escala solicitada
  e saída em pontos PDF. O caminho tiled foi coberto para rotações físicas
  0°, 90°, 180° e 270°.
- O teste de extensão percorre a CLI real com uma quinta engine fake, sem
  importar implementação das quatro engines.
- `scripts/check_ocr_dependencies.py`, `compileall`, `bash -n`, `git diff --check`
  e `uv lock --check` passaram depois da extensão tipada de PR-06. A suíte
  integral `.venv/bin/python -m pytest -q --tb=short` passou antes da integração diferida de candidatas regionais,
  incluindo integração de região/célula/figura via `PageCoordinator`. Os testes
  também cobrem figura opcional vazia, settings locais, orçamento sem identidade
  de engine e healthcheck Tesseract do backend sem timeout.
- Após a correção de ownership OCR em figuras e do comparador de snapshots, a
  suíte integral `.venv/bin/python -m pytest -q --tb=short` terminou novamente
  com código 0; `compileall`, `git diff --check` e a verificação de dependências
  também passaram. Não há alterações em `src/structured_pdf_text/native/`.
- Os skips/xfails encontrados foram revisados: as especificações antigas de
  E2E V5 dependem de um PDF-fixture que não está em `tests/`; a integração de
  tabelas visuais depende de visual engine/corpus; e os xfails de consenso OCR
  declaram explicitamente a fase ainda não implementada. Casos que dependem de
  ground truth local são condicionais. Eles continuam pendências de cobertura,
  não são contados como homologação.
- `pytest docs/audits/test_evaluator_ground_truth_audit.py -q --tb=short`
  também passou (39 casos).
- O preflight estático de todas as quatro engines/provider RapidOCR passou com
  os caches isolados explicitamente configurados: EasyOCR, Paddle, RapidOCR
  ONNX/OpenVINO (modelos/hash conferidos) e Tesseract `por`. A execução sem
  essas configurações locais havia reportado apenas cache/executável ausente;
  readiness estática não substitui os smokes de inferência.
- `requirements-ocr-benchmark.txt`, as constraints, `pyproject.toml` e `uv.lock`
  fixam RapidOCR 3.9.2, ONNX Runtime 1.30.0 e OpenVINO 2026.4.1. A atualização
  de OpenVINO responde ao smoke real: 2024.4.0 não carrega o reconhecedor v6.
  `uv lock --check` passou após a troca do pin; o wheel foi instalado e os
  smokes medium/small com OpenVINO passaram.
- `pip check` falha no ambiente atual porque não há distribuição `opencv-python`
  nem `opencv-python-headless` instalada, embora os metadados de RapidOCR e
  EasyOCR exijam esses pacotes. A árvore de dependências declara OpenCV
  headless, enquanto RapidOCR 3.9.2 também declara o pacote com GUI; resolver
  isso exige validar uma instalação limpa sem misturar variantes que fornecem
  o mesmo módulo `cv2`. O ambiente atual fornece `cv2` via OpenCV contrib, mas
  isso não satisfaz os nomes de distribuição exigidos no metadata.
- Smoke E2E Tesseract V5 página 19 pós-desacoplamento: válido, 23 tokens OCR,
  sem parciais; identidade efetiva registrou Tesseract 5.5.3, idioma `por` e
  hash de `por.traineddata`.
- RapidOCR medium e small reais com ONNX Runtime passaram nos controles de
  acentos, valores, identificadores e números; o small fragmentou alguns tokens,
  mas os critérios semânticos do smoke passaram.
- A captura E2E real de RapidOCR PP-OCRv6 medium e small na página 19 de V5
  terminou `valid`/`success` em ambos os tamanhos, com os caminhos e hashes dos
  artefatos efetivos registrados. O primeiro medium revelou um erro de
  serialização de `FrozenMap` aninhado na identidade; a conversão recursiva foi
  corrigida e a execução repetida em namespace novo passou.
- A captura E2E real de Paddle PP-OCRv6 medium na página 19 de V5 terminou
  `valid`/`success`, com 16 tokens OCR, identidade runtime e hashes efetivos.
- A captura EasyOCR V5 balanced com policy adaptive (default explícito da
  configuração) pós-regra de conservação terminou
  `benchmark_status=valid`, `document_status=success` e `stability_status=clean`
  nas páginas 19, 24 e 31, sem warnings nem parcialidade. Ela está isolada em
  `outputs/validation-ocr-decoupling-20261021/V5/easyocr/` sob o run ID
  `v5-easyocr-post-conservation-20261021-01`; as capturas anteriores não foram
  sobrescritas.
- A repetição EasyOCR V5 com o integrador atômico atual terminou em 371,4 s,
  também com policy adaptive, status `valid`/`success`/`clean` e 16, 16 e 6
  tokens OCR adicionados nas páginas 19, 24 e 31, sem warnings ou parcialidade.
  O manifesto/Markdown/features ficam no mesmo diretório sob
  `v5-easyocr-post-typed-integrator-20261021-01`. Essa extração percorreu a
  fachada de produção e o adapter ainda emite propostas de complemento; os
  testes focados exercitam separadamente substituição/célula/figura tipadas.
- A comparação posterior dessas páginas, com identidade de execução e
  configuração compatíveis, **não é equivalente**: a captura com o integrador
  tipado difere em 2.528 campos antes da correção de ownership. Uma repetição
  após associar OCR residual à região `FIGURE` terminou `valid` em 335,7 s, sem
  criar região suplementar dentro da figura, e reduziu as diferenças para
  1.481 (724 geometria, 379 estrutura, 45 conteúdo e 333 decisões); ainda há
  diferenças em Markdown e conteúdo. Na página V5-31 persistem falsos positivos
  (`0`, `C`, `08`) no conteúdo da figura, com seis tokens contra três na
  captura de referência. A associação corrigiu a ownership, mas **não** corrige
  a regressão de reconhecimento; a equivalência e a qualidade EasyOCR seguem
  bloqueadas. Comparações ficam em
  `outputs/validation-ocr-decoupling-20261021/V5/easyocr/comparisons/`.
- As quatro capturas E2E RapidOCR v6 em V5 página 19 (medium/small × ONNX
  Runtime/OpenVINO) terminaram `valid`/`success`; as identidades registram
  OpenVINO 2026.4.1 ou ONNX Runtime 1.30.0 e hashes por estágio.

## Gates ainda necessários

1. Executar os testes de cancelamento/readiness e instalação limpa RapidOCR em
   Windows nativo; o job CI configurado não substitui execução remota observada.
2. Comparar por região/página o caminho novo com o baseline para as quatro
   engines e revisar os controles críticos V3/V4/V5 sem sobrescrever os
   artefatos originais. A amostra EasyOCR V5 (páginas 19/24/31) já comprova
   divergências e mantém bloqueio de qualidade até análise e correção.
3. Homologar os fluxos tipados de dados críticos e refinamentos dedicados de
   figuras/tabelas visuais em corpus real e corrigir falsos positivos RapidOCR
   nas figuras sem texto V5-21/V5-31. O incidente de autorização foi resolvido,
   mas o controle semântico permanece reprovado. O código está integrado; testes
   sintéticos e a conclusão operacional de uma extração não aprovam qualidade.
4. Executar gates de corpus/endurance e revisar skips/xfails. Os problemas
   anteriores de conteúdo coberto V3 e CNPJ nativo V5 continuam registrados;
   nenhuma alteração foi feita no extrator nativo.
5. Resolver o conflito de nomes de distribuição OpenCV de EasyOCR/RapidOCR em
   instalação limpa e repetir `pip check` mais a regressão cruzada das engines.
