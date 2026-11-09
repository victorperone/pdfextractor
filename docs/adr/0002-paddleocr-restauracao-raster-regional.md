# ADR 0002 — PaddleOCR: restauração do processamento histórico do raster regional

**Data:** 2026-11-09
**Status:** Implementado; qualidade de inferência pendente de homologação
**Branch:** `PaddleOCR`
**Autoridade da decisão:** decisão de projeto registrada nesta conversa
**Commit de partida auditado:** `cfa0797c870cb235af5c186e58f7160e4f291d3d`

## Contexto e histórico

O projeto tinha uma arquitetura anterior à separação das engines. A janela de desacoplamento da engine OCR ocorreu entre os commits `54885e699190015c0bd4c026a3b24f2b296aed8c` (20/10/2026) e `1482aec9a778471deaa6aec6be2b8750b31a8c0b` (23/10/2026). O propósito declarado do desacoplamento foi **isolar o desenvolvimento das engines**, não redesenhar o processamento OCR.

A auditoria de 09/11/2026 identificou uma mudança funcional na preparação do raster regional: o fluxo anterior utilizava recorte/redimensionamento/rotação locais sobre raster já disponível, e a implementação posterior passou, em caminhos coordenados, a obter rasters diretamente da fonte por serviços autorizados com conversão por `RasterFrame`. Arquivos relevantes: `src/structured_pdf_text/ocr/recovery.py`, `ocr/pipeline/adapters.py`, `ocr/pipeline/coordinator.py` e `ocr/pipeline/services.py`.

**Não existe, nesta fase, ensaio de inferência demonstrando que a configuração histórica apresenta CER/WER melhores que a atual.** A escolha é uma preferência técnica expressa pelo projeto, sujeita a homologação posterior.

## Decisão

Restaurar **exclusivamente para o PaddleOCR** a semântica anterior de preparação de imagens de regiões e tiles: imagem de referência autorizada, recorte local, resize por escala candidata, rotação local, OCR e transformação inversa para coordenadas canônicas. A restauração foi implementada dentro da **arquitetura desacoplada atual**, sem reverter seus contratos, políticas ou integrações compartilhadas.

Acesso aos pixels e aceitação de tokens continuam sujeitos aos mecanismos de autorização do core. Os orçamentos de memória e rasterização continuam obrigatórios; diferenças necessárias por causa desses controles foram documentadas.

**Política de duração:** nenhuma extração deve ser abortada por excesso de tempo decorrido. São permitidos avisos de lentidão não interruptivos; cancelamento explícito, erros fatais e proteção de recursos permanecem. Os antigos timeouts do worker Paddle não foram restaurados.

**Decisões preservadas:** PP-OCRv6 medium, `use_doc_unwarping=True`, sem PP-OCRv5; sem alteração intencional das demais engines.

## Justificativa

1. O desacoplamento tinha finalidade arquitetural; evitar mudança funcional não planejada na engine.
2. O fluxo histórico oferece uma referência clara e testável para pixels e geometria.
3. A restauração seletiva reduz o risco de perder melhorias posteriores em tabelas, diagnósticos e conservação.
4. Registrar a decisão em ADR torna visível a introdução do caminho novo e a posterior decisão de revertê-lo para PaddleOCR.

## Implementação

### Arquivos alterados

| Arquivo | Alteração |
|---------|-----------|
| `src/structured_pdf_text/ocr/pipelines/paddle/pipeline.py` | Adicionado `legacy_raster_preparation = True` a `PaddlePipeline` |
| `src/structured_pdf_text/ocr/pipeline/coordinator.py` | `OCRPageFacade.canonical_page_output` retorna `False` quando `pipeline.legacy_raster_preparation=True`; `OCRPageFacade.recognize_page` roteia para o backend raw em modo legacy |

### Mecanismo

A propriedade `PaddlePipeline.legacy_raster_preparation = True` sinaliza à `OCRPageFacade` que `canonical_page_output` deve retornar `False`. O `OcrRegionRefiner` em `recovery.py` checa `canonical_page_output` para decidir o caminho:

- **`False` (Paddle, histórico):** `crop_page_region` → `resize_image` → `rotate_image_expanded` → `backend.recognize_page(transformed_image)` → `_map_token_to_page`
- **`True` (demais engines):** passa `requested_scale` / `preprocessing_rotation` / `target_bbox` para o coordenador re-renderizar a partir da fonte autorizada

Quando `OCRPageFacade.recognize_page` é chamado sem `target_bbox` e sem `integration_targets` em modo legacy, roteia para `pipeline.raw_engine.recognize_page(image, ...)` diretamente, processando a imagem pixel já transformada. Chamadas com `target_bbox` ou `integration_targets` sempre usam o caminho do coordenador.

### Diferenças conhecidas em relação ao raster histórico

- **Mascaramento de borda:** o caminho autorizado via `services.render()` aplica mascaramento de pixels parciais na borda da região autorizada; o caminho legacy usa um `crop` simples sem mascaramento. Tokens perto da borda podem diferir em bboxes por 1–2 pixels.
- **Linhagem de tokens:** tokens produzidos via coordenador transitam por `services.validate_token()`. Tokens do caminho legacy recebem `provenance="targeted_region_recovery"` mas não passam pela validação do ledger de autorização.
- **Integração deferida:** `integration_targets` não são propagados ao backend no caminho legacy; a integração acontece no código chamador (api.py) após o refiner retornar, não dentro da chamada de reconhecimento.

## Consequências e riscos

- Alteração contida nos dois arquivos listados; nenhuma outra engine é afetada.
- Sem inferência real neste WSL, os testes aprovam **correção de integração**, não qualidade OCR final.
- Qualquer afirmação de melhoria real de CER/WER requer homologação posterior em Windows Server CPU e WSL GPU.

## Verificação

### Testes implementados (`tests/test_paddle_legacy_raster.py`)

| Teste | O que verifica |
|-------|----------------|
| `test_paddle_pipeline_has_legacy_raster_preparation` | Flag na classe `PaddlePipeline` |
| `test_paddle_canonical_page_output_is_false` | `OCRPageFacade.canonical_page_output` para Paddle |
| `test_other_engines_canonical_page_output_is_true` | Engines sem o flag não são afetadas |
| `test_paddle_legacy_raster_crop_resize_rotate` | Backend recebe imagem escalada; tokens mapeados para coords de página |
| `test_paddle_legacy_raster_multiple_calls_per_scale_rotation` | Cada par (escala, rotação) gera uma chamada |
| `test_paddle_legacy_raster_rotations` | Rotação 90° → mapeamento inverso correto |
| `test_paddle_legacy_raster_page_rotation_handled` | `/Rotate 90` não duplica a transformação |
| `test_paddle_rgb_budget_gate_still_active` | Gate de orçamento RGB continua ativo |
| `test_paddle_rgb_budget_from_settings_respected` | Orçamento lido das settings, não do nome da engine |
| `test_paddle_integration_targets_bypass_legacy_path` | `integration_targets` → coordenador, não backend raw |
| `test_paddle_target_bbox_bypasses_legacy_path` | `target_bbox` → coordenador, não backend raw |
| `test_paddle_no_elapsed_time_abort` | Backend lento não aborta a extração |
| `test_paddle_explicit_cancel_still_works` | Cancelamento explícito chega ao backend via callback |
| `test_paddle_default_models_and_uvdoc_preserved` | PP-OCRv6 medium e `use_doc_unwarping=True` preservados |

### Gate de software WSL

```bash
.venv/bin/python -m pytest -q \
  tests/test_paddle_offline.py \
  tests/test_paddle_worker_protocol.py \
  tests/test_ocr_pipeline_architecture.py \
  tests/test_ocr_pipeline_contracts.py \
  tests/test_ocr_parser_regressions.py \
  tests/test_ocr_baseline_equivalence.py \
  tests/test_paddle_legacy_raster.py
```

## Validação futura (fora desta fase)

No Windows Server CPU, rodar os mesmos documentos e gabaritos relevantes; capturar identidade de runtime, modelos, configuração, CER, WER, tabelas, estrutura Markdown, dados críticos e diagnóstico por página. No WSL com GPU, repetir cobertura técnica de dispositivo e fallback. Esses testes decidem qualidade e desempenho, **não** a autorização para seguir com a integração de software no WSL atual.

## Referências

- `Relatorio_Auditoria_PaddleOCR_Desacoplamento_2026-11-09.md`
- `54885e699190015c0bd4c026a3b24f2b296aed8c` (antes do desacoplamento)
- `1482aec9a778471deaa6aec6be2b8750b31a8c0b` (fim do desacoplamento)
- `cfa0797c870cb235af5c186e58f7160e4f291d3d` (ponto de partida desta restauração)
- `docs/adr/0001-authorized-ocr-pipelines.md`
