# ADR 0002 — PaddleOCR: restauração do processamento histórico do raster regional

**Data:** 2026-11-09
**Status:** Implementado no working tree (sem commit); qualidade de inferência pendente de homologação
**Branch:** `PaddleOCR`
**Autoridade da decisão:** decisão de projeto registrada em 09/11/2026
**Commit de partida auditado:** `cfa0797c870cb235af5c186e58f7160e4f291d3d`
**Primeira tentativa (substituída):** `a926b892331038190e373fa969478e159cec8668`

## Contexto e histórico

O projeto tinha uma arquitetura anterior à separação das engines. A janela de desacoplamento da engine OCR ocorreu entre os commits `54885e699190015c0bd4c026a3b24f2b296aed8c` (20/10/2026) e `1482aec9a778471deaa6aec6be2b8750b31a8c0b` (23/10/2026). O propósito declarado do desacoplamento foi **isolar o desenvolvimento das engines**, não redesenhar o processamento OCR.

A auditoria de 09/11/2026 identificou uma mudança funcional na preparação do raster regional. No fluxo anterior, o `OcrRegionRefiner` recortava a imagem da página já renderizada (orientação visual), ampliava o recorte com `resize_image` (LANCZOS), girava com `rotate_image_expanded` e mapeava os tokens de volta com `_map_token_to_page`. Depois da migração, os caminhos coordenados passaram a pedir a cada tentativa uma renderização nova da fonte PDF, na escala final e na orientação canônica, convertida por `RasterFrame`. Arquivos relevantes: `ocr/recovery.py`, `ocr/pipeline/adapters.py`, `ocr/pipeline/coordinator.py`, `ocr/pipeline/services.py`.

**Não existe, nesta fase, ensaio de inferência demonstrando que a configuração histórica apresenta CER/WER melhores que a atual.** A escolha é uma preferência técnica do projeto, sujeita a homologação posterior.

## Decisão

Restaurar **exclusivamente para o PaddleOCR** a semântica histórica de preparação de pixels — recorte local da página autorizada em orientação visual, ampliação local pela escala candidata, rotação local, OCR e uma única transformação inversa para coordenadas canônicas — **dentro da arquitetura desacoplada atual**, sem desviar do coordenador, do ledger, da integração deferida, dos orçamentos ou do cancelamento.

Decisões complementares de 09/11/2026:

1. **Orientação visual** também para OCR de página inteira, regiões e tiles do refiner (páginas com `/Rotate`).
2. **Tentativa ampliada de célula** volta a ser ampliação LANCZOS exata (2×) sobre o render da fonte, como em 54885e69.
3. Nota de rodapé (modo exaustivo) volta a receber o render da fonte em orientação visual, como em 54885e69.
4. **Correção adicional (fora da restauração, aprovada em 09/11/2026):** caixas do PaddleOCR voltam ao referencial da imagem de entrada quando o pré-processador de documento gira a página (ver "Defeito pré-existente corrigido").

Acesso a pixels e aceitação de tokens continuam sujeitos à autorização do core. Os orçamentos de memória e rasterização continuam obrigatórios; as diferenças necessárias por causa desses controles estão listadas abaixo, não encobertas.

**Política de duração:** nenhuma extração é abortada por tempo decorrido. Avisos de lentidão (`warn_if_slow`) são apenas telemetria; cancelamento explícito, falhas fatais e proteções de recursos permanecem. Os antigos timeouts do worker Paddle não foram restaurados.

**Decisões preservadas:** PP-OCRv6 medium, `use_doc_unwarping=True`, sem PP-OCRv5; sem alteração funcional das demais engines.

## Justificativa

1. O desacoplamento tinha finalidade arquitetural; a mudança na origem dos pixels não foi planejada como mudança de reconhecimento.
2. O fluxo histórico continua no repositório (ramo local do `OcrRegionRefiner` para engines cruas) e serve de referência executável para pixels e geometria.
3. Restaurar só a preparação de pixels preserva melhorias posteriores em tabelas, diagnósticos, conservação e integração.
4. O registro em ADR torna visível a introdução do caminho novo e a decisão de revertê-lo para o PaddleOCR.

## Por que a primeira tentativa (a926b892) foi substituída

O parecer independente de 09/11/2026 reprovou a926b892:

| Achado | Causa em a926b892 | Correção |
|---|---|---|
| P0-01 — `_recover_selected_regions` falhava com `ValueError: Selected OCR evidence did not come from a deferred pipeline result` | `canonical_page_output=False` desligava a integração deferida | `canonical_page_output` voltou a `True`; o refiner sempre usa o coordenador; teste com `LayoutRegion` real |
| P0-02 — desvio para `pipeline.raw_engine` sem autorização, ledger, validação de tokens ou orçamento | Atalho em `OCRPageFacade.recognize_page` | Atalho removido; os pixels históricos são produzidos **dentro** de `PaddlePipeline.execute` a partir de leases dos serviços autorizados |
| P1-01 — `page_rotation` perdido no OCR de página inteira | Atalho repassava só `quality_policy`/`quality_variants` | A rotação física é aplicada uma vez, na orientação do lease (`RasterFrame`); o backend recebe o raster visual com `page_rotation=0` e o frame faz a única conversão |
| P1-02 — `ocr_passes`, `calls`, `last_result` zerados | Chamadas fora do coordenador | Todas as tentativas passam pelo coordenador e ficam registradas |

## Defeito pré-existente corrigido: referencial do pré-processador de documento

A extração real do V3 (09/11/2026) mostrou que as páginas com conteúdo **fisicamente girado** (raster girado sem `/Rotate`: V3 p. 96, 98, 99 e 116; p. 97 com 180°) perdiam toda a OCR. Com `use_doc_orientation_classify=True`, o PaddleX gira a imagem (`warp_image.rotate_image`, anti-horário por `doc_preprocessor_res.angle`), aplica UVDoc e devolve `rec_boxes` **no referencial girado**. A engine tratava essas caixas como se estivessem na imagem de entrada. Em 90°/270°, uma caixa saía da página e o ledger rejeitava o pedido obrigatório da página inteira; em 180°, as caixas ficavam espelhadas sem que nada acusasse.

- O defeito **já existia** na base `cfa0797`: reproduzido nas mesmas páginas, com as mesmas coordenadas. A conversão de caixas da engine é a mesma desde 54885e69, onde os tokens mal posicionados eram aceitos sem validação.
- Correção em `ocr/paddle.py` (`_document_preprocessor_frame`, `_document_turn_to_input`): para um único resultado com ângulo 90/180/270, as caixas são levadas de volta pela inversa da **mesma matriz** do PaddleX (`cv2.getRotationMatrix2D` em `(w/2, h/2)`, canvas `int`, amostragem por centro de pixel). Se a saída do UVDoc tiver outro tamanho, as caixas são primeiro reescaladas para o canvas girado. A rotação do token passa a somar o giro do documento, como nas candidatas de orientação da própria engine. Resultados ambíguos (vários resultados, sem pré-processador, ângulo desconhecido) ficam como antes.
- A validação de autorização **não** foi afrouxada; a correção eliminou a causa. UVDoc continua ligado. A deformação não linear do UVDoc não é invertida (limitação pré-existente); apenas o tamanho é compensado.
- Testes: `tests/test_ocr_parser_regressions.py::test_paddle_parser_*` (oráculo independente que gira uma imagem sintética com `warpAffine` exatamente como o PaddleX; reescala do UVDoc; ausência de giro; vários resultados).

## Implementação

### Mecanismo

1. **Intenção declarada pelo core (neutra quanto à engine).** O core informa no `native_context` do `OCRWorkRequest` a origem histórica dos pixels: `raster_preparation` ∈ {`page_crop`, `visual_source`, `enlarged_source`} e, para `enlarged_source`, `raster_enlargement`. O esquema do contrato não muda (`native_context` já existia). Quem declara:
   - `OCRPageFacade.recognize_page` → `page_crop` para o pedido de página inteira (`WorkGoal.PAGE`);
   - `OcrRegionRefiner` → `page_crop` para regiões e tiles (inclui regiões fracas, figuras, tabela visual e fallback de dado crítico);
   - `refine_cell` → `enlarged_source` (fator `CellRecoveryPolicy.enlarged_scale_factor`) só na tentativa ampliada;
   - `refine_footnote` → `visual_source`.
   Pedidos sem a marcação (célula normal, dado crítico direto, tiles de página `ocr_tiling`) continuam com o render direto da fonte — que já era o comportamento histórico desses caminhos.
2. **Estratégia por pipeline.** `AuthorizedBackendPipeline.execute` delega a obtenção do lease a `self.raster_preparation`. O padrão (`SOURCE_RASTER_PREPARATION`) é exatamente o código anterior (render na escala pedida + `services.rotate`). EasyOCR, RapidOCR e Tesseract continuam no padrão e ignoram a marcação.
3. **Estratégia Paddle** (`ocr/pipelines/paddle/raster.py`, `paddle-legacy-raster-v1`):
   - `page_crop`: `services.render` na escala base do pipeline → giro exato de 90° múltiplos de volta à orientação visual (`services.rotate`) → `resize_image` com fator `render_scale / escala_base` → `rotate_image_expanded` para rotação local ≥ 0,001°;
   - `visual_source`: render na escala pedida → orientação visual;
   - `enlarged_source`: render na escala pedida ÷ fator → LANCZOS exato (`round(w·k)`, `round(h·k)`).
   As funções `resize_image` e `rotate_image_expanded` são as mesmas do código histórico.
4. **Ponto de extensão nos serviços.** `AuthorizedOCRServices.derive(lease, w, h, operation, current_to_parent)` publica uma operação local sobre os pixels de um lease: reserva o orçamento pelas dimensões pré-calculadas, rejeita resultado maior, compõe o `RasterFrame` com a afim inversa informada e libera ao fechar. Tokens continuam passando por `validate_token`/ledger.
5. **Diagnóstico.** Cada tentativa Paddle registra `raster_preparation` (estratégia, origem, escala renderizada, rotação visual, fator de ampliação, rotação local, dimensões finais) em `engine_diagnostics.paddle.attempts[]`. As demais engines não ganham esse campo.

### Arquivos alterados (em relação a `cfa0797`)

| Arquivo | Alteração |
|---|---|
| `ocr/pipeline/contracts.py` | Vocabulário `RASTER_PREPARATIONS` e validação `raster_preparation_context` |
| `ocr/pipeline/coordinator.py` | `recognize`/`recognize_page` aceitam e validam a intenção; página inteira marca `page_crop`; atalho e `canonical_page_output=False` de a926b892 removidos |
| `ocr/pipeline/policies.py` | `SourceRasterPreparation` (comportamento anterior, sem mudança) |
| `ocr/pipeline/adapters.py` | `execute` obtém o lease pela estratégia do pipeline; diagnóstico opcional por tentativa |
| `ocr/pipeline/services.py` | `derive` (operação local com orçamento e frame composto) |
| `ocr/pipeline/cell_recovery.py`, `ocr/pipeline/token_refinement.py`, `ocr/recovery.py` | Declaram a intenção histórica nas chamadas ao facade |
| `ocr/pipelines/paddle/raster.py` (novo), `policy.py`, `pipeline.py` | Estratégia Paddle; flag `legacy_raster_preparation` de a926b892 removida |
| `ocr/paddle.py` | Caixas do pré-processador de documento voltam ao referencial de entrada (defeito pré-existente) |
| `ocr/backends/paddle.py`, `docs/OCR_Decoupling_PR01_PR03.md` | Alegações de equivalência "byte a byte" limitadas ao escopo verificado |
| `tests/test_paddle_legacy_raster.py` | Suíte substituída (ver Verificação) |
| `tests/test_ocr_parser_regressions.py` | Testes do referencial do pré-processador de documento |

### Diferenças conhecidas em relação ao raster histórico

1. **Pixels parcialmente fora da região.** O recorte histórico (`crop_page_region`: `floor` à esquerda/topo, `ceil` à direita/base) incluía até 1 pixel de conteúdo fora da região em cada borda. O render autorizado não entrega conteúdo fora da autorização (política `mask`: pixels parciais em branco). Quando as bordas da região caem em limites de pixel na escala base, a imagem é **idêntica**; caso contrário, difere apenas nessa faixa de no máximo 1 pixel na escala base (ampliada pelo fator local). A proteção não foi enfraquecida.
2. **Rotação local não múltipla de 90°.** O histórico recortava (`clamp`) a caixa do token aos limites do recorte; o caminho autorizado rejeita o token que, mapeado, sai da região (`invalid_region`). Hoje **nenhum chamador** usa rotação local diferente de 0 (todos passam `rotations=(0.0,)`). Teste: `test_paddle_rotated_local_variant_never_widens_authorization`.
3. **Página inteira.** Se largura/altura × escala não for inteira, a última coluna/linha parcial é mascarada em branco (antes vinha do `render_page`).
4. **Escala base.** O histórico usava a imagem da página de `safe_render_scale("ocr", …)`; os serviços recusam (`BudgetBlocked`) em vez de reduzir a escala quando o limite nativo seria excedido — comportamento já existente dos serviços.
5. **Custo.** A orientação visual é obtida renderizando (canônico) e girando de volta por transposição exata (sem perda; identidade de pixels verificada). Ampliações passam a ser LANCZOS em CPU em vez de render PDFium em escala maior.
6. **Páginas com conteúdo fisicamente girado:** depois da correção do referencial, os tokens ficam na posição real (vertical em 90°/270°) com `rotation` coerente. No histórico eles eram aceitos no referencial errado. O texto reconhecido é o mesmo; posição e ordem de leitura seguem a geometria corrigida.
7. **Fora do escopo:** tiles de página (`ocr_tiling`), célula não ampliada e dado crítico direto já usavam render da fonte em orientação canônica no histórico e não mudaram.

## Verificação

### Equivalência sintética (sem inferência)

`tests/test_paddle_legacy_raster.py` executa o **mesmo pedido** pelo ramo histórico do `OcrRegionRefiner` (código de 54885e69) e por `PaddlePipeline` atrás do `PageCoordinator`, com PDF real (PDFium) e backend falso que devolve a caixa em pixels do marcador vermelho:

| Teste | Verifica |
|---|---|
| `test_paddle_legacy_raster_crop_resize_rotate` (16 casos) | Rotação de página 0/90/180/270 × escala 1/1,5/2/3: imagem entregue **idêntica pixel a pixel**; bbox canônica igual (1e-6); fonte renderizada só na escala base; diagnóstico da tentativa; leases liberados |
| `test_paddle_legacy_raster_local_rotation` | Rotação local de 90° (`rotate_image_expanded`/OpenCV) idêntica |
| `test_paddle_legacy_raster_non_zero_origin` | CropBox com origem não zero |
| `test_paddle_legacy_raster_tile_geometry` (0°/90°) | Fallback por tiles com orçamento RGB: mesmas imagens por tile, mesma geometria, pedidos `tile` registrados, orçamento por tile respeitado |
| `test_paddle_legacy_raster_rotations_full_page` (0/90/180/270) | P1-01: OCR de página recebe o raster visual idêntico ao histórico, `page_rotation=0` no backend, conversão única igual à histórica |
| `test_paddle_recover_selected_regions_integrates_deferred_tokens` | P0-01/P1-02: `_recover_selected_regions` com `LayoutRegion` real integra pelo ledger; `calls`, `last_result`, recibos e decisões corretos; evidência forjada rejeitada |
| `test_paddle_authorization_and_rgb_budget_preserved` | P0-02: token fora da autorização rejeitado; ampliação local bloqueada pelo orçamento da requisição antes da inferência |
| `test_paddle_rotated_local_variant_never_widens_authorization` | Diferença conhecida 2 |
| `test_paddle_enlarged_cell_matches_historic_lanczos` | Tentativa ampliada de célula idêntica ao LANCZOS 2× histórico |
| `test_paddle_footnote_keeps_historic_visual_source_render` | Rodapé idêntico ao render visual histórico |
| `test_core_states_raster_intent_for_cell_and_footnote_attempts` | Core declara a intenção nos pontos certos |
| `test_raster_preparation_context_is_validated` | Valores inválidos recusados |
| `test_paddle_default_models_and_uvdoc_preserved` | PP-OCRv6 medium padrão, `use_doc_unwarping=True`, nenhum perfil v5 |
| `test_paddle_no_elapsed_time_abort` | Inferência bloqueada até o aviso de lentidão disparar; conclui `ok`, sem cancelamento (sincronizado por eventos) |
| `test_paddle_explicit_cancel_still_works` | Cancelamento explícito interrompe a inferência bloqueada |
| `test_other_engines_raster_paths_unchanged` | EasyOCR/RapidOCR/Tesseract usam a estratégia padrão; mesmo com `page_crop` declarado, re-renderizam canônico na escala pedida |

### Auditoria de timeouts (caminho Paddle)

CLI, `api.py`, coordenador, adaptador, serviços e refiner: nenhum prazo. Backend/worker (`ocr/backends/paddle.py`): leitura de resposta `fut.result()` sem timeout; encerramento `process.wait()` sem prazo, apenas com `warn_if_slow`; `kill` somente em cancelamento explícito ou falha de protocolo. Engine (`ocr/paddle.py`): tempo medido apenas para log. Fora do controle do programa: bibliotecas Paddle/CUDA e o sistema operacional.

### Resultados (WSL, 09/11/2026)

- Gate dirigido (seis módulos aprovados + `test_paddle_legacy_raster.py` + `test_ocr_pipeline_services.py`): **457 passed**, rc=0.
- Suíte completa: **3177 passed, 4 xfailed**, 0 falhas, rc=0 (Python 3.12, `.venv`, OpenCV disponível, sem skips nos testes novos).
- Extração real V3 (prova de funcionamento, **não homologação**): `.ocr-envs/gpu-ready-20261022/paddle` (paddlepaddle-gpu 3.3.1, PaddleOCR 3.7.0, RTX 3060, `gpu:0`), `pdftext extract --mode balanced --language pt-BR --progress --ocr-engine paddle --ocr-quality-policy adaptive --gpu --output markdown`, saída em `output/paddle09-11-2026/`. Modelos carregados: PP-OCRv6 medium det/rec, UVDoc, PP-LCNet doc/textline orientation.
  - 1ª rodada (só a restauração do raster): exit 1, `partial_success`; OCR descartada nas p. 96, 98, 99 e 116 (defeito pré-existente acima, reproduzido também em `cfa0797`).
  - Rodada final (com a correção do referencial): exit 0, `success`, 144 páginas em ~1 min 55 s, nenhum aviso de OCR.
  - Contra o gabarito (`compute_metrics.py`, Markdown paginado da mesma configuração), nas 44 páginas escaneadas/híbridas: substituições/ref = 0,68% nas duas rodadas; deleções/ref de 10,21% para 0,31%; p. 96/98/99/116 sem nenhuma deleção. As métricas globais (CER ≈ 0,68, tabelas 0) são dominadas pela configuração pedida — cabeçalhos/rodapés preservados (excluídos do gabarito) e extração de tabelas desligada — e não medem a restauração. **Não houve comparação controlada com a base**; nenhuma melhoria de CER/WER atribuível à restauração é alegada.

## Registro de implementação

- **SHA do commit:** pendente — alterações mantidas no working tree, sem commit nem push, por instrução do responsável (09/11/2026).
- **Testes:** ver "Resultados" acima.
- **Status:** `Implementado; qualidade de inferência pendente de homologação`.

## Consequências e riscos

- Mudança compartilhada pequena e neutra: intenção no `native_context`, estratégia por pipeline, `services.derive`. As demais engines mantêm o comportamento (testado).
- Páginas com `/Rotate` passam a ser lidas pelo Paddle em orientação visual — interação com o classificador de orientação de documento (`use_doc_orientation_classify`) e com UVDoc precisa ser observada na homologação.
- A extração real executada nesta fase demonstra funcionamento ponta a ponta, **não** homologa qualidade.

## Validação futura (fora desta fase)

No Windows Server CPU, rodar os mesmos documentos e gabaritos; capturar identidade de runtime, modelos, configuração, CER, WER, tabelas, estrutura Markdown, dados críticos e diagnóstico por página, comparando com a base `cfa0797`. No WSL com GPU, repetir cobertura de dispositivo e fallback. Esses ensaios decidem qualidade e desempenho.

## Referências

- `Relatorio_Auditoria_PaddleOCR_Desacoplamento_2026-11-09.md` (externo ao repositório)
- `Parecer_Revisao_PaddleOCR_a926b892_2026-11-09.md` (externo ao repositório)
- `cc36103527374579ca03b4e7163b72e09644b9a0` (referência histórica de qualidade)
- `54885e699190015c0bd4c026a3b24f2b296aed8c` (antes do desacoplamento; referência de pixels)
- `1482aec9a778471deaa6aec6be2b8750b31a8c0b` (fim do desacoplamento)
- `cfa0797c870cb235af5c186e58f7160e4f291d3d` (ponto de partida da branch)
- `docs/adr/0001-authorized-ocr-pipelines.md`
