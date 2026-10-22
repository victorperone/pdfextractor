# Estado da implementação do desacoplamento OCR

Atualizado em 21/10/2026. O objetivo autorizado cobre PR-00–PR-14 do
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
| PR-06 — policies/integrador | Policies locais de ranking/filtro/consenso numérico regional, células, notas e linhas fracas. `ProposalIntegrator` aplica complementos, substituições, células e figuras com snapshots, conservação, ownership, linhagem e replay. `TokenTarget` vincula seleções visíveis da coleção OCR real; tiles preservam suas autorizações e são aplicados atomicamente. Resultados tipados capturam métricas e diagnósticos por tentativa sem consumir acumuladores da página. | Parcial: produção regional inicial, associação a figura nesse fluxo, células, notas de rodapé e linhas OCR fracas já usam propostas tipadas e somente candidatas selecionadas alteram alvos. O helper público `ocr_table_cells(..., ocr_facade=...)` também usa serviços e conserva o objeto original. Faltam dados críticos e refinamentos dedicados de figuras/tabelas visuais no fluxo tipado, além da equivalência por corpus. |
| PR-07–10 — pipelines | Pipelines/settings/policies locais nas quatro engines; cada engine possui objetos independentes para recuperação regional, candidatos, integração, células e refinamentos de tokens. Estratégias maduras permanecem encapsuladas nos backends específicos. Paddle worker usa protocolo 5 negociado para transportar polígonos, proveniência estruturada, diagnósticos e falhas tipadas. | Parcial: Tesseract, RapidOCR medium/small e Paddle tiveram E2E reais válidos. EasyOCR V5 balanced/adaptive concluiu os quatro controles. Equivalência de corpus ainda reprovada; a migração dos fluxos restantes e plataformas permanece necessária. |
| PR-11 — cancelamento | API aceita `cancellation_token`; Paddle e Tesseract podem interromper subprocessos sem deadline de inferência; trata corridas entre cancelamento e criação de processo. Os probes de identidade/healthcheck do backend Tesseract não impõem timeout. O `--list-langs` do preflight estático mantém orçamento diagnóstico opcional de 10 s e retorna `UNKNOWN`; não participa da extração. | Código e testes com executáveis/workers controlados implementados. Windows nativo e limitação de cancelamento durante chamadas EasyOCR/RapidOCR in-process continuam sem validação de plataforma. Prazos curtos de limpeza após kill permanecem. |
| PR-12 — Paddle v6 | Somente `pt`, `pt-v6-medium` e `pt-v6-small` ativos; `pt-v5` rejeitado. Worker deixa de importar o arquivo local `ocr/paddle.py` como se fosse o pacote Paddle, corrigindo sua inicialização por filename. | Testes e E2E medium válidos. Smoke de célula real passou em valor negativo, identificador, integração e linhagem; falhou em acento porque reconhece `ÃO` em lugar de `AÇÃO`, tanto direto quanto worker. Paridade em células foi comprovada por smokes reais direto/worker e teste do protocolo com runtime substituído; relatórios e falhas semânticas ficam preservados. Windows continua sem homologação. |
| PR-13 — RapidOCR v6 | Resolução explícita medium/small, manifesto/hash por estágio, setup, verificador, smoke e runners Bash/PowerShell atualizados. | Hashes dos quatro artefatos por tamanho verificados e charset português presente. Smokes medium e small passaram com ONNX Runtime e OpenVINO 2026.4.1 nos controles de acentos, valor, identificador e número. E2E real em V5 página 19 passou nas quatro combinações: medium/small × ONNX/OpenVINO; todos `valid`/`success`, com artefatos e providers identificados por hash/versão durante a execução. OpenVINO 2024.4.0 falhou ao importar o reconhecedor v6 (erro ONNX `Reshape`), validando a necessidade do novo pin. A [documentação upstream de suporte v6](https://github.com/RapidAI/RapidOCRDocs/blob/main/docs/blog/posts/inference_engine/support_PP-OCRv6_rec_engines.md) demonstra OpenVINO 2026.2.1. Instalação limpa Windows, comparação controlada de modelo e geometria/corpus ainda pendentes. |
| PR-14 — aceite | Gates de arquitetura, extensibilidade e documentos atualizados. | Pendente até fechar PR-06, migrações PR-07–10, smokes/corpus, dependências e plataformas. Não declarar release aceita antes disso. |

## Validação mais recente — células, tokens e worker

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
3. Concluir os fluxos tipados de dados críticos e refinamentos dedicados de
   figuras/tabelas visuais, preservando sinais, valores críticos, visibilidade
   e ownership. Regiões iniciais, células, notas e linhas fracas já passaram
   para aplicação tipada das candidatas selecionadas.
4. Executar gates de corpus/endurance e revisar skips/xfails. Os problemas
   anteriores de conteúdo coberto V3 e CNPJ nativo V5 continuam registrados;
   nenhuma alteração foi feita no extrator nativo.
5. Resolver o conflito de nomes de distribuição OpenCV de EasyOCR/RapidOCR em
   instalação limpa e repetir `pip check` mais a regressão cruzada das engines.
