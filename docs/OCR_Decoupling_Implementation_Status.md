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
| PR-11 — cancelamento | API aceita `cancellation_token`; Paddle e Tesseract podem interromper subprocessos sem deadline de inferência; trata corridas entre cancelamento e criação de processo. Os probes de identidade/healthcheck do backend Tesseract não impõem timeout. O `--list-langs` do preflight estático não tem prazo: enquanto roda, apenas registra aviso de demora. Não participa da extração. | Código e testes com executáveis/workers controlados implementados. Windows nativo e limitação de cancelamento durante chamadas EasyOCR/RapidOCR in-process continuam sem validação de plataforma. Não há prazo de limpeza após kill: a espera é bloqueante e apenas avisa se demorar. |
| PR-12 — Paddle v6 | Somente `pt`, `pt-v6-medium` e `pt-v6-small` ativos; `pt-v5` rejeitado. Worker deixa de importar o arquivo local `ocr/paddle.py` como se fosse o pacote Paddle, corrigindo sua inicialização por filename. | Testes e E2E medium válidos. Smoke de célula real passou em valor negativo, identificador, integração e linhagem; falhou em acento porque reconhece `ÃO` em lugar de `AÇÃO`, tanto direto quanto worker. Paridade em células foi comprovada por smokes reais direto/worker e teste do protocolo com runtime substituído; relatórios e falhas semânticas ficam preservados. Windows continua sem homologação. |
| PR-13 — RapidOCR v6 | Resolução explícita medium/small, manifesto/hash por estágio, setup, verificador, smoke e runners Bash/PowerShell atualizados. | Hashes dos quatro artefatos por tamanho verificados e charset português presente. Smokes medium e small passaram com ONNX Runtime e OpenVINO 2026.4.1 nos controles de acentos, valor, identificador e número. E2E real em V5 página 19 passou nas quatro combinações: medium/small × ONNX/OpenVINO; todos `valid`/`success`, com artefatos e providers identificados por hash/versão durante a execução. OpenVINO 2024.4.0 falhou ao importar o reconhecedor v6 (erro ONNX `Reshape`), validando a necessidade do novo pin. A [documentação upstream de suporte v6](https://github.com/RapidAI/RapidOCRDocs/blob/main/docs/blog/posts/inference_engine/support_PP-OCRv6_rec_engines.md) demonstra OpenVINO 2026.2.1. Instalação limpa Windows, comparação controlada de modelo e geometria/corpus ainda pendentes. |
| PR-14 — aceite | Gate AST inclui consumidores fora de `ocr/`; quinta engine via CLI, guia de extensão e ADR. Integrações sintéticas, endurance, instalação por perfil e comparação opcional da linhagem executáveis. | As cinco instalações limpas Linux passaram em dependências/OpenCV/imports. Aceite permanece pendente por divergências de corpus, smoke Paddle, endurance completo, defeitos P0 e Windows. Não declarar release aceita antes disso. |

## Integração de tabelas híbridas e endurance — 22/10/2026

Avanço de PR-06/PR-14 nos casos T07/T11/T13/T26: a descoberta de estrutura no
recorte de uma imagem usava a extensão da página inteira para converter edges
e amostrar separadores. `detect_visual_table_in_region` agora conserva o frame
real dos pixels após arredondamento, desfaz a rotação de apresentação antes do
recorte e libera os rasters próprios inclusive em falhas. O conteúdo continua
vindo da composição visível; o helper não acessa a imagem embutida original nem
autoriza inferência OCR. O algoritmo nativo permanece inalterado.

A passagem do resultado autorizado para `OcrToken`, linhas e células conserva
`evidence_id`. O campo é aditivo e opcional para consumidores raw/legados;
no caminho autorizado seu valor vem do ID atribuído à observação pelo pipeline.
Snapshots detectam também trocas de identidade com conteúdo/box iguais.
O rebuild de linhas da tabela conserva identidades de linhas de origem de
forma transitiva, evitando duplicação pelo fallback de conservação quando uma
figura passa a ter estrutura tabular. Uma célula refinada continua reivindicando
suas evidências de origem; apenas coincidência de texto/geometria não basta.

Os cinco skips incondicionais dos scaffolds V5/tabelas híbridas foram
substituídos por PDFs sintéticos determinísticos. PDFium, seleção/autorização,
integração, montagem e renderers são reais; reconhecimento e descoberta de
grid são injetados para o CI core dispensar modelos/OpenCV. Os testes verificam
duas tabelas distintas, rejeição da tabela visual que duplica a nativa, células
vazias, negação/identificadores/valor negativo, repetibilidade e JSON finito.
Isso cobre invariantes motivadas pelo V5, **não** homologa o corpus V5 real.
Os quatro xfails de consenso OCR futuro continuam explícitos.

`scripts/run_ocr_endurance.py` reutiliza um extrator por um número finito de
extrações, separa warmup do total medido e requer namespace novo. Registra
SHA-256 do PDF/código, configuração, identidade efetiva por repetição,
Markdown/JSON inicial, estados por página, repetibilidade, RSS de pai/árvore,
FDs ou handles, workers restantes após `close()` e pico amostrado. Medição
incompleta, extração parcial, página solicitada ausente, deriva de conteúdo,
código alterado ou worker restante impedem sucesso operacional. Não há
deadline de init/inferência nem ajuste automático de thresholds de qualidade.
O relatório mantém `release_accepted=false` e `quality_accepted=false`;
crescimento de memória/descritores exige revisão, sem limiar inventado.

Evidências desta etapa em `outputs/validation-ocr-decoupling-20261022/`:

- `pytest-hybrid-final-full-23.xml`: **2.840 passed, 4 xfailed, 0 skipped**,
  sem falhas/erros, em 107,029 s, após integrar as correções e o registry dos
  runners ao checkout principal. Gate de dependências, `compileall` e
  `git diff --check` passaram; `pip check` continua reprovado pelas exigências
  de nomes de distribuição OpenCV de EasyOCR e RapidOCR.
- `source-hybrid-final-23/`: manifesto e arquivo com 321 fontes/configurações
  usados na validação final. O código de aplicação coincide com o dos smokes
  híbridos reais; somente a consulta ao registry foi atualizada nos runners.
  O resumo consolidado é `validation-summary-hybrid-final-23.json`.
- A extensão final dos providers e a rejeição de imports de outro checkout
  passaram em **81 testes focados**, sem skips/falhas, em
  `pytest-runner-provenance-final-26.xml`. O manifesto final dos 321 arquivos
  está em `source-hybrid-final-26/`; o índice das evidências é
  `validation-summary-final-26.json`, com os resultados de corpus em
  `validation-summary-corpus-25.json`. O código de aplicação continua idêntico
  ao dos smokes reais; a última alteração acrescenta validação ao harness.
- `pytest-hybrid-isolated-full-21.xml`: **2.837 passed, 4 xfailed, zero skips
  incondicionais**, sem falhas/erros, em 152,349 s. A cópia isolada conservou o
  código usado nas capturas que ainda estavam executando no checkout principal.
- `source-hybrid-lineage-21/source-hashes.json` e `source.tar.gz`: hashes e
  cópia dos 165 arquivos Python de produção/runner usados na validação isolada.
- `endurance-tesseract-1000-21/report.json` e `samples.jsonl`: **1.000 páginas
  medidas + 1 warmup**, 123,35 s, conteúdo e identidade repetíveis, sem páginas
  ausentes, falhas operacionais ou workers restantes. Foi um PDF sintético
  pequeno com uma linha raster, relido pelo mesmo extrator; não é endurance de
  V3/V4/V5 completos nem evidência para outras engines/plataformas.
- Nesse endurance, o RSS médio nas primeiras/últimas 100 repetições foi
  80,43/83,23 MiB; o runner também retém metadados por repetição. FDs oscilaram
  entre 5–6 e terminaram em 5; o pico amostrado da árvore foi 124,45 MiB.
  São observações do workload, sem inferir ausência geral de vazamentos.
- `hybrid-synthetic-real-{easyocr,paddle,rapidocr,tesseract}-21/`:
  reconhecimento e OpenCV reais produziram duas tabelas (nativa 2×3 e raster
  3×3), células nativas vazias e zero fallback/duplicação nas quatro engines.
  Tesseract repetiu o conteúdo em warmup + duas extrações medidas; seu `AB-O1O`
  em vez de `AB-010` permanece erro de reconhecimento do controle. EasyOCR
  leu `4290`, `1200`, `AB009` e `AB010`, perdendo sinal/separadores do raster
  pequeno. Paddle e RapidOCR medium reconheceram corretamente esses valores e
  identificadores. O conteúdo nativo crítico permaneceu preservado nas quatro.
  São validações de integração; não aprovam qualidade de reconhecimento e,
  sem referência anterior desse PDF sintético, não medem regressão de modelo.
- A tentativa `hybrid-synthetic-real-tesseract-20/` não entra nas evidências:
  importou o checkout principal antes de carregar o runner isolado. A execução
  `21` foi repetida com `PYTHONPATH` explícito e assert da origem do pacote.
  O runner agora também rejeita pacote carregado de outro checkout antes de
  reservar o namespace ou atribuir hashes de fonte à captura.

Nos controles reais V5 19/21/24/31 desta etapa, Paddle concluiu operacionalmente
sem warnings, mas reconheceu `as` em V5-21 e `HXIS` em V5-31, ambas figuras sem
texto. São novos registros de reprovação do controle negativo, somados à falha
de acento no smoke de célula; não foram eliminados por um filtro ligado às
páginas. RapidOCR medium/ONNX e Tesseract retornaram `no_text` nos dois controles
negativos. As quatro páginas isoladas não aprovam qualidade no corpus inteiro.
EasyOCR concluiu as quatro páginas em balanced/adaptive, sem falhas de escopo
ou warnings, mas integrou seis falsos positivos em V5-31 (`0`, `C`, `0`, `0`,
`08`, `0`). Em V5-21 retornou `no_text`. Os relatórios por engine estão em
`hybrid-real-19-{engine}/`, com o código daquela captura congelado em
`source-hybrid-endurance-19/`. A correção posterior de linhagem foi validada
separadamente pelo PDF híbrido e pela suíte completa; não foi declarada uma
nova equivalência de baseline usando resultados anteriores à correção.

Continuação de PR-04/PR-14: os parsers de `evaluate_e2e.py`,
`smoke_ocr_cell_pipeline.py` e `run_ocr_endurance.py` consultam o registry no
momento da chamada, eliminando listas literais e snapshots estáticos de engines
e providers.
Uma quinta engine fake registrada **depois** de importar os runners percorre
o benchmark E2E real com provider próprio, Markdown/features/identidade e proteção contra
sobrescrita; os parsers de smoke/endurance também a aceitam sem carregar runtime.
As opções das quatro engines existentes permanecem as mesmas.

Continuação da homologação: `v5-full-tesseract-final-24/` concluiu as **42
páginas** com status operacional `success`, sem páginas ausentes ou workers
restantes. A repetição com a aplicação congelada antes desta etapa
(`source-figure-admission-16/`, usando o mesmo harness) permite comparar PDF,
configuração e identidade efetiva iguais. Markdown e texto de leitura de todas
as páginas são idênticos; a comparação estrita ainda registra 656 diferenças
(257 de estrutura e 399 de decisões), incluindo IDs/linhagem e diagnósticos.
O arquivo é `comparisons/v5-tesseract-hybrid-full-24.json`; não é equivalência
com a baseline anterior ao desacoplamento.

Os fallbacks de conservação em V5 8/12/17/26/39/40 já existiam no código
congelado. Surgiram avisos `table_structure_uncertain` em 17/26: a descoberta
com frame correto encontrou candidatas com spans sobrepostos, rejeitadas pela
validação central. Nenhuma tabela adicional foi renderizada e o Markdown ficou
inalterado. Esses avisos são diferenças funcionais explícitas; não foram
suprimidos nem usados para aprovar qualidade.

`v3-full-tesseract-final-24/`, `v4-full-tesseract-final-24/` e
`v5-full-tesseract-final-24/` concluíram respectivamente **144/224/42 páginas**
(410 no total), todos `success`, com configuração/identidade/código/PDF
registrados, sem páginas ausentes ou workers restantes. Os tempos observados
foram 72,38/459,88/18,00 s; houve execuções concorrentes, portanto esses tempos
não constituem comparação de desempenho. Warnings e fallbacks continuam nos
relatórios e exigem revisão de qualidade. Isso avança o gate de corpus de
Tesseract nesta plataforma; não aprova as demais engines, Windows ou a baseline
anterior ao desacoplamento.

## Admissão local de evidências RapidOCR — 22/10/2026

Avanço de PR-06/PR-13: o adapter executa uma policy local de admissão **depois**
de validar a geometria da observação. O default admite toda evidência válida;
EasyOCR, Paddle, Tesseract e a compatibilidade legada preservam essa escolha.

`RapidOCRFigureAdmissionPolicy` se abstém quando o núcleo autorizou uma figura,
a confiança normalizada é inferior a **0,70** e o box cobre pelo menos **85%**
da união exata das áreas autorizadas. Labels menores, leituras mais confiáveis
e confiança desconhecida continuam elegíveis. A regra não consulta texto,
alfabeto, número de página nem nome de documento. Células, dados críticos e
páginas completas não recebem essa calibração de figura.

É uma mudança funcional da composição v6, separada da equivalência estrutural:
o limiar e a versão da policy ficam na identidade efetiva da execução. Esses
limiares **não** demonstram ausência geral de perda de recall; a homologação
completa do corpus permanece necessária.

Observações rejeitadas continuam em `OCRPipelineResult.tokens`, com candidatas
`selected=False`, motivo, geometria, confiança e contadores. Elas não recebem
propostas nem são oferecidas à seleção diferida. O contrato rejeita propostas
que tentem reinseri-las e o integrador exige proposta única para toda evidência
elegível. Rejeição de qualidade não apaga falha de autorização/runtime nem
converte uma execução com incidente em vazio nominal.

Os **243 testes focados** passaram em `pytest-figure-admission-16.xml`, cobrindo
seleção imediata/diferida, candidatas mistas, dados críticos, confidências
desconhecidas, união com overlaps/vãos, tentativa de bypass, configuração
inválida e isolamento entre engines. Fontes e hashes estão preservados em
`outputs/validation-ocr-decoupling-20261022/source-figure-admission-16/`.
Suíte integral final: **2.796 passed, 5 skipped e 4 xfailed**, em **273,643 s**,
em `pytest-figure-admission-full-17.xml`. Gate de imports, `compileall` e
`git diff --check` também passaram. Não houve alteração do extrator nativo.

As quatro capturas reais, run IDs
`v5-rapidocr-{medium|small}-{onnxruntime|openvino}-admission-20261022-16`,
concluíram V5 **19/21/24/31** com `benchmark_status=valid`:

| Perfil/provider | Duração | V5-19 / V5-24 | V5-21 / V5-31 |
| --- | --- | --- | --- |
| medium / ONNX Runtime | 239,3 s | 16 / 16 tokens | zero / zero; `no_text` |
| medium / OpenVINO | 98,3 s | 16 / 16 tokens | zero / zero; `no_text` |
| small / ONNX Runtime | 52,4 s | 16 / 16 tokens | um / zero; primeiro controle reprovado |
| small / OpenVINO | 38,5 s | 16 / 16 tokens | um / zero; primeiro controle reprovado |

No medium, duas observações em V5-21 e uma em V5-31 foram rejeitadas com
motivo explícito, em cada provider, sem falha de autorização/runtime. O controle
positivo medium ONNX preserva **conteúdo, geometria e estrutura** de V5-19/24
em relação ao estágio `trim-11`, com os mesmos pesos/runtime; as **54**
diferenças são diagnósticas. Linhas nativas por região permanecem idênticas
nos quatro controles. Relatórios:
`rapidocr-admission-controls-16.json` e
`comparisons/rapidocr-admission-positive-16.json`.

Tesseract concluiu os quatro controles em **24,6 s**, `valid`, com Markdown
idêntico ao estágio anterior e somente **234** diferenças diagnósticas, sem
erros de identidade. Esse tempo foi capturado com outras inferências em
execução; não é comparação controlada de desempenho. Relatório:
`comparisons/tesseract-admission-16.json`.

O controle small em V5-21 ainda reconhece `O` (confiança aproximada **0,719**)
com box menor. Esse caso não satisfaz a condição de abstenção e permanece
**reprovado**. Não foi elevado o limiar para adaptar a regra a esse único
exemplo. Resultados operacionais `valid` continuam distintos de aprovação
de qualidade.

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
- Os cinco placeholders de E2E V5/tabelas visuais foram substituídos por
  integração de domínio sintética executável. Os quatro xfails de consenso OCR
  declaram uma fase ainda não implementada. Casos que dependem de ground truth
  local são condicionais; não são contados como homologação do corpus real.
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

## Instalação por configuração — continuação de PR-14

O instalador [`setup_ocr_environments.py`](../scripts/setup_ocr_environments.py)
cria ambientes independentes para as cinco configurações do benchmark. Os
wrappers Bash/PowerShell usam esse instalador; ambientes existentes e a `.venv`
de desenvolvimento são preservados. Cada ambiente instala somente seu extra e
a distribuição OpenCV exigida pelos metadados upstream, com constraints da
plataforma. Não foram alterados os pacotes instalados na `.venv` compartilhada.

`check_ocr_install.py` agora exige também `pip check`, verifica a distribuição
OpenCV esperada, isola os imports por configuração e grava JSON inclusive em
falha. Os relatórios incluem comandos, hashes dos scripts/constraints antes e
depois e os relatórios do pip com hashes dos wheels resolvidos. Os imports não
validam modelos nem qualidade. O benchmark aceita `--environment-root` /
`-EnvironmentRoot` para selecionar o Python por extração, recusa ambientes
ausentes antes de iniciar e não verifica pesos RapidOCR quando essa família não
foi solicitada. O preflight aceita o cache explícito, inclusive no re-exec.

O [guia de instalação](OCR_Installation_Profiles.md) documenta os comandos,
provisionamento explícito e os limites. O workflow manual
`OCR isolated installation` oferece Linux/Windows por configuração e publica
os relatórios em falha; sua criação não comprova execução remota Windows.

Validação local de código: `pytest-installation-profiles-29.xml` registra
**175 testes aprovados**; a suíte completa em
`pytest-installation-full-30.xml` registra **2.863 aprovados e quatro xfails**,
sem skips nesta plataforma. As quatro expectativas futuras de consenso genérico
permanecem inventariadas. Gate AST, compilação, `git diff --check` e
`uv lock --check --offline` passaram; o diretório do extrator nativo continua
sem diferenças contra `54885e6`.

Os relatórios de instalação reais ficam sob
`.ocr-envs/validation-20261022-29/<configuração>/`, com cópias dos JSONs em
`installation-reports-34/`. **As cinco configurações passaram** em dependências,
wheel OpenCV único e imports, com hashes dos scripts/constraints inalterados.
Tesseract, EasyOCR e os dois providers RapidOCR também passaram nos smokes reais,
com pesos locais,
incluindo os controles de moeda, data, porcentagem, português acentuado e hífen.
Os smokes não eliminam as falhas de corpus/figuras/células registradas acima.

Paddle executou inferência, mas o smoke retornou `INCOMPLETE`:
`currency_exact`, `accented_portuguese` e `hyphen_preserved` falharam, com CER
0,0203. Os prefixos `Ação`, `Segunda-feira` e `R$` foram reconhecidos como
`ção`, `egunda-feira` e `$`. A repetição no ambiente compartilhado anterior
produziu o mesmo texto e as mesmas três falhas, com os mesmos pesos/configuração:
`preflight-clean-paddle-29.log` e `preflight-shared-paddle-41.log`. Não foram
relaxados os checks nem declarado smoke Paddle aprovado. Isso distingue o gate
de instalação concluído da qualidade OCR ainda bloqueada.

Em `clean-v5-rapidocr-{onnxruntime,openvino}-31/`, a página 19 do V5 concluiu
com medium, balanced/adaptive e tabelas/células habilitadas. Todos os checks
operacionais passaram, sem warnings ou filhos restantes: 100,42 s em ONNX e
12,18 s em OpenVINO. As execuções se sobrepuseram; os tempos não estabelecem
comparação de desempenho. Essa amostra não aprova o corpus completo.

O par `shared-hybrid-tesseract-32/` e `clean-hybrid-tesseract-31/` usa a mesma
configuração, uma extração de aquecimento e duas medidas. O Markdown é idêntico;
o comparador estrito ainda encontra 38 diferenças de IDs opacos de requests,
autorizações e evidências, sem diferenças de texto, geometria ou status.
Essa execução usa `complexity_render_scale=0.5`, o default do CLI do harness;
não deve ser confundida com a captura sintética anterior em escala 1.0.
O erro de reconhecimento `AB-O1O` continua presente e impede aceite semântico.
Em `clean-hybrid-tesseract-scale1-33/`, a configuração explícita em escala 1.0
reproduziu as duas tabelas e o Markdown da captura
`hybrid-synthetic-real-tesseract-21/`. Aquecimento mais duas medidas passaram
nos checks operacionais e de limpeza; isso verifica o fluxo com OpenCV headless
sem corrigir o identificador reconhecido incorretamente.

## Comparação de linhagem — continuação de PR-00/PR-14

`compare_ocr_baseline.py` mantém o modo estrito por padrão. A opção explícita
`--canonicalize-lineage-ids` permite comparar capturas independentes por
renomeação consistente de componentes UUID4 em campos conhecidos de linhagem.
Preserva a distinção entre IDs, referências de pai/evidência, sufixos de
tentativa/token e linhagem nativa estável. Não normaliza UUIDs em texto,
identificadores de conteúdo ou hashes de modelo. Os checks de configuração,
identidade do modelo/provider e Markdown continuam obrigatórios no CLI. Os
artefatos de entrada não são modificados e a política/contagem constam no JSON.

O controle Tesseract pareado descrito acima tem 38 diferenças no relatório
estrito `comparisons/clean-hybrid-tesseract-34.json` e zero no relatório
adicional `comparisons/clean-hybrid-tesseract-lineage-35.json`. Cada lado contém
dez UUIDs distintos; ambos os relatórios e as capturas originais foram mantidos.
Igualdade com um controle que contém `AB-O1O` não aprova qualidade semântica.

A comparação V5 completa anterior, `v5-full-tesseract-pre-hybrid-24/` versus
`v5-full-tesseract-final-24/`, **continua não equivalente** com a opção:
`comparisons/v5-tesseract-hybrid-lineage-35.json` registra 556 diferenças
(301 decisões e 255 estrutura), enquanto o relatório estrito original mantém
656. Markdown permanece idêntico. Os novos warnings de estrutura em páginas
17/26 continuam registrados; a opção não remove campos/decisões nem reaprova
essa migração.

`pytest-lineage-comparison-35.xml` registra 66 testes focados aprovados antes da
variante adicional de entrada não finita; a suíte em
`pytest-lineage-full-36.xml` registra **2.883 aprovados, quatro xfails e nenhum
skip** nesta plataforma. Os testes adversariais mantêm a detecção de arestas
erradas, IDs colapsados, pai errado, alteração de sufixo, sinal perdido,
linhagem nativa, geometria, metadados de modelo e config/Markdown divergentes.

A proteção adicional contra tuples Python imitando a representação interna de
IDs passou nos 40 testes de `pytest-lineage-tuples-38.xml`. A suíte final em
`pytest-final-39.xml` registra **2.884 aprovados, quatro xfails e nenhum skip**.
O snapshot final de 338 arquivos de código/configuração está em
`source-final-39/`; os snapshots anteriores foram preservados.

O V5 completo foi repetido no perfil Tesseract limpo em
`clean-v5-tesseract-full-40/`: **42 páginas**, 18,47 s, todos os checks
operacionais aprovados e nenhuma alteração em PDF/código. Configuração e PDF
coincidem com `v5-full-tesseract-final-24/`; Markdown é idêntico e o comparador
com renomeação de UUIDs encontra zero diferenças nos features. O relatório
estrito mantém 648 diferenças de IDs. As versões/hashes do Tesseract coincidem,
mas a identidade bruta registra a mudança de comando de `.venv/bin/tesseract`
(wrapper) para `.ocr-runtime/bin/tesseract`. Essa diferença de composição é
explícita; não foi suprimida pelo checker de identidade. Os seis warnings de
página anteriores permanecem, incluindo estrutura incerta em 17/26. Relatórios:
`comparisons/v5-tesseract-clean-environment-{strict-,}41.json`. Essa comparação
de implantação não aprova equivalência com a baseline original nem qualidade.

O índice consolidado desta continuação é
`outputs/validation-ocr-decoupling-20261022/validation-summary-final-41.json`:
instalações, smokes, extrações, comparações e snapshot final, com aceite de
qualidade/release explicitamente falso. Nenhuma execução desta etapa permanece
em andamento.

## Gates ainda necessários

1. Executar os testes de cancelamento/readiness e instalação limpa RapidOCR em
   Windows nativo; o job CI configurado não substitui execução remota observada.
2. Comparar por região/página o caminho novo com o baseline para as quatro
   engines e revisar os controles críticos V3/V4/V5 sem sobrescrever os
   artefatos originais. A amostra EasyOCR V5 (páginas 19/24/31) já comprova
   divergências e mantém bloqueio de qualidade até análise e correção.
3. Homologar os fluxos tipados de dados críticos e refinamentos dedicados de
   figuras/tabelas visuais em corpus real e corrigir o falso positivo RapidOCR
   small em V5-21. Medium passou nos controles negativos V5-21/V5-31 nos dois
   providers, mas isso não homologa o corpus completo. O código está integrado; testes
   sintéticos e a conclusão operacional de uma extração não aprovam qualidade.
4. Executar gates de corpus/endurance e revisar skips/xfails. Os problemas
   anteriores de conteúdo coberto V3 e CNPJ nativo V5 continuam registrados;
   nenhuma alteração foi feita no extrator nativo.
5. Homologar os perfis isolados de instalação em Windows e ampliar a regressão
   cruzada para o corpus completo. Dependências/OpenCV/imports passaram nas
   cinco instalações limpas Linux; a `.venv` compartilhada histórica continua
   reprovada em `pip check` e não é a instalação recomendada para release.
6. Importar a baseline EasyOCR V4 da outra máquina para completar a referência
   original; a captura local foi cancelada pelo usuário em 182/224 páginas.

## Evolução autorizada: GPU no WSL2 (22/10/2026)

Após o desacoplamento, o usuário solicitou suporte CUDA antes de repetir os
documentos. Essa autorização acrescenta GPU ao escopo; a exclusão de GPU no
plano original continua descrevendo a migração original.

Implementados `use_gpu=False`, `--gpu`, propagação pelos settings tipados,
CLI/presets, worker Paddle e runners. EasyOCR desativa quantização CPU ao usar
CUDA. Paddle seleciona explicitamente CPU ou `gpu:0`. RapidOCR/ONNX verifica os
providers efetivos de detecção e reconhecimento e informa execução mista ou
fallback; OpenVINO permanece CPU na implementação 3.9.2. Erros de memória
continuam fatais. O extrator nativo não foi alterado.

O instalador oferece ambientes GPU isolados, constraints CUDA e checker de
colisões CPU/GPU. `scripts/smoke_ocr_gpu.py` exige inferência CUDA observada num
PDF sintético pelo pipeline autorizado; disponibilidade do provider não basta.
As instruções estão em [OCR_Installation_Profiles.md](OCR_Installation_Profiles.md#gpu-no-linuxwsl2).
Regressão final do código: **2.975 testes passaram, 4 xfails conhecidos e zero
skips**, registrados em `pytest-final-cpu-fallback.xml`. Os 75 casos de
seleção/fallback GPU estão incluídos nesse total; a auditoria independente do
avaliador passou nos 39 casos. O gate de dependências, a compilação Python,
`uv lock --check --offline` e `git diff --check` também passaram.

Evidências desta tarefa: `outputs/validation-ocr-gpu-20261022/`. A revisão de
código, os caminhos corretos desta máquina, os testes e os gates ainda abertos
estão em [OCR_GPU_Validation_2026-10-22.md](OCR_GPU_Validation_2026-10-22.md).
Os relatórios antigos `environment-status*.json` registram tentativas
interrompidas; não representam mais o estado corrente. As evidências atuais
ficam em `validation-final2/report.json`, `balanced-adaptive-final/report.json`
e no índice consolidado `validation-summary-final.json`: **ready**, cinco
instalações aprovadas e 14 casos de runtime aprovados. Fonte estável durante a
validação; o extrator nativo não mudou. A validação balanced/adaptive adicional
passou nas três engines.

CUDA foi observado em inferência real nas três engines, com pesos EasyOCR em
`cuda:0`, cinco predictors Paddle em GPU e eventos CUDA nos estágios Det/Rec
do ONNX Runtime. O modo balanced/adaptive também passou nas três engines com
identidade CUDA e tokens reais. A CLI EasyOCR `extract --gpu --mode balanced
--ocr-quality-policy adaptive` passou com uma página sintética.

A página 140 de `corpus/V3/Document_AI_V3.pdf` foi isolada e rasterizada para
validar OCR em CPU, GPU e ambientes CPU sem CUDA. Nenhum documento completo
V3/V4/V5 foi reextraído. O nativo permanece inalterado. Essa verificação não
aprova equivalência com baseline nem encerra os gates de corpus, Windows,
endurance, qualidade e P0 listados acima.

## Remoção de timeouts — 23/10/2026

Nenhum engine OCR, página ou documento tem mais prazo. Foram removidos os `timeout` de `ocr/readiness.py` (`--list-langs` do Tesseract), `ocr/backends/paddle.py` (esperas do worker e do fechamento), `evaluation/evaluator.py` (consulta git), `memory.py` (encerramento do amostrador), `scripts/setup_rapidocr_models.py` (download) e o ramo morto `TimeoutExpired` do Tesseract. A execução continua sem interrupção; `ocr/slow_work.py` registra aviso periódico em `AuthorizedBackendPipeline`, que cobre todos os engines. Os valores de status `timeout` permanecem apenas para leitura de capturas antigas.
