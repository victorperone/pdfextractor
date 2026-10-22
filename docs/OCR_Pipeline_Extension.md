# Adicionar uma engine OCR

O registro é explícito e local ao processo. Uma engine nova não exige editar
`PdfTextExtractor`, `PageCoordinator` nem os pipelines existentes. Não há
descoberta automática de plugins nem fallback entre famílias.

## Composição e settings

Crie `ocr/pipelines/<nome>/settings.py`, `policy.py` e `pipeline.py`, além de
um backend próprio. Os módulos de settings e seus imports devem funcionar sem
carregar bibliotecas de inferência. O resolver recebe `(config, entry)` e retorna
um `EngineSettings` imutável, com `selection`, idioma, escala, qualidade e budget.
Para uma extensão mínima, use
`structured_pdf_text.ocr.pipeline.settings:resolve_generic_settings`.

O descritor contém caminhos de imports, não classes de runtime já construídas:

```python
from structured_pdf_text.ocr.registry import BackendEntry, ENGINE_REGISTRY

ENGINE_REGISTRY.register(BackendEntry(
    name="minhaocr",
    display_name="Minha OCR",
    default_runtime="meu-runtime",
    default_profile="perfil-homologado",
    extras=None,
    setup_hint="Prepare o runtime e os pesos locais antes da extração.",
    backend_factory="meu_pacote.backend:build_backend",
    pipeline_factory="meu_pacote.pipeline:build_pipeline",
    settings_resolver=(
        "structured_pdf_text.ocr.pipeline.settings:resolve_generic_settings"
    ),
    capabilities=("page", "region"),
    default_render_scale=3.0,
))
```

Faça o registro antes de construir a configuração ou executar a CLI no mesmo
processo. `ExtractorConfig(ocr_engine="minhaocr", mode="balanced")` passa pela
mesma seleção e autorização das engines existentes. Declare `providers` e
`default_provider` apenas quando houver providers reais da mesma família.
`default_profile` descreve a composição esperada; a identidade efetiva precisa
ser capturada pelo backend durante o carregamento dos artefatos usados.

## Pipeline e backend

Para reutilizar o mecanismo autorizado dos quatro pipelines atuais:

```python
from dataclasses import replace
from structured_pdf_text.ocr.factory import build_ocr_backend
from structured_pdf_text.ocr.pipeline.adapters import AuthorizedBackendPipeline
from structured_pdf_text.ocr.pipeline.candidate_policy import CandidateFusionPolicy
from structured_pdf_text.ocr.pipeline.policies import (
    CellRecoveryPolicy, ConservativeIntegrationPolicy, EvidenceAdmissionPolicy, FigureSegmentationPolicy,
    RasterBoundaryPolicy, RegionalRecoveryProfile, TokenRefinementPolicy,
)

class MinhaPipeline(AuthorizedBackendPipeline):
    recovery_policy = RegionalRecoveryProfile()
    integration_policy = ConservativeIntegrationPolicy("minhaocr-integration-v1")
    cell_policy = CellRecoveryPolicy()
    token_refinement_policy = TokenRefinementPolicy()
    fusion_policy = CandidateFusionPolicy()
    figure_segmentation_policy = FigureSegmentationPolicy()
    raster_boundary_policy = RasterBoundaryPolicy()
    admission_policy = EvidenceAdmissionPolicy()

def build_pipeline(resolved):
    backend = build_ocr_backend(replace(
        resolved.config, ocr_settings=resolved.settings,
    ))
    return MinhaPipeline(
        backend, selection=resolved.settings.selection,
        render_scale=resolved.settings.render_scale,
        settings=resolved.settings, capabilities=("page", "region"),
    )
```

Cada policy é uma instância própria. Calibre-a no pacote da engine; o núcleo
compartilha geometria, medições, recursos e conservação. Uma policy não recebe
o documento mutável e não pode ampliar a seleção inicial de regiões. O núcleo
valida os segmentos propostos para figuras e rejeita sobreposição ou expansão.

O backend implementa `recognize_page(image, page_index, page_bbox=None, ...)`
e `close()`. No adapter autorizado, `image` é um crop próprio em orientação
canônica, `page_bbox` é `None` e os `OcrToken` retornados estão em pixels desse
crop. Não aplique novamente escala ou rotação da página: `RasterFrame` realiza
a transformação para pontos PDF. Confianças do domínio ficam em `[0, 1]`;
preserve polígonos, nível, idioma e proveniência conhecidos. A identidade deve
informar runtime/provider, versões e hashes efetivos, sem inventar valores
para campos desconhecidos.

Uma implementação direta do protocolo `OCRPipeline` pode substituir o adapter.
Nesse caso, `execute(request, services)` usa somente leases e transforms dos
serviços para obter pixels, consome tentativas e devolve `OCRPipelineResult`
validável para o request original. Preserve IDs de autorizações, tokens e
tentativas; propostas se referem a evidência realmente observada. `no_text`
não carrega tokens, incidentes recuperados continuam nos diagnósticos e
cancelamento não produz sucesso parcial silencioso.

O modo de borda padrão `mask` substitui por branco pixels cujo footprint
ultrapassa a área autorizada. Uma engine pode escolher
`RasterBoundaryPolicy("trim")`: o serviço remove essas bordas sintéticas,
valida o retângulo restante contra a autorização original e recalcula o frame.
Não há tolerância adicional nem clipping de tokens inválidos. Sem pixel
inteiramente autorizado, `trim` falha antes da inferência. RapidOCR usa essa
opção; as demais engines preservam `mask`. Isso é uma mudança funcional local
e precisa de nova captura, sem substituir o baseline histórico.

## Integração e validação

Uma policy local de admissão recebe `TokenEvidence` já validado e o request.
`rejection_reason(token, request)` retorna `None` para admitir a observação ou
um motivo estável para rejeitá-la. A confiança normalizada está em
`token.confidence_normalized`; não derive confiança de campos desconhecidos.
O default admite todas as observações e preserva os demais pipelines.

Observações rejeitadas permanecem em `OCRPipelineResult.tokens` e em candidatas
com `selected=False`, mas não recebem proposta de integração. Os contadores
`observed_tokens`, `admitted_tokens` e `rejected_tokens` e os diagnósticos de
admissão conservam a distinção. Um trabalho pode retornar `ok` com observações
rejeitadas e nenhum texto integrado; o resultado final desse objetivo é
`no_text`, sem apagar o trabalho realizado. Falhas de runtime/geometria
continuam sendo falhas, independentemente de abstenção de qualidade.

O coordenador oferece à seleção diferida somente evidências referenciadas em
propostas válidas. O contrato proíbe propor evidência de candidata exclusivamente
não selecionada; o integrador ainda exige que toda evidência elegível tenha
exatamente uma proposta. Preservar observações brutas não permite recuperá-las
por um caminho paralelo depois da rejeição.

`ProposalIntegrator` aplica as propostas aos alvos vinculados pelo núcleo.
Substituições conservam conteúdo confiável, sinais, valores, ownership e
linhagem. Seleções de tiles guardam suas autorizações originais e são aplicadas
juntas; tentativas descartadas não podem alterar os alvos. Não invoque um
backend raw diretamente de API, tabelas ou figuras.

O conversor para `OcrToken` preserva o ID da observação autorizada em
`evidence_id`; a reconstrução deve transferi-lo para `TextToken`. Uma célula
refinada preserva IDs anteriores em `derived_from_ids`, inclusive quando o
novo reconhecimento desloca o box. Rebuilds de linhas preservam transitivamente
`merged_source_line_ids` para que conservação/assembly não emitam novamente o
conteúdo consumido pela tabela. Não substitua esses vínculos por coincidência
de texto ou posição. O campo em `OcrToken` é opcional para compatibilidade raw.

Na descoberta central de estrutura em raster, um recorte precisa conservar sua
extensão em pontos PDF (`image_bbox`). `detect_visual_table_in_region` faz a
conversão da composição visível, desfaz rotação e libera o recorte. Ele não
substitui os serviços autorizados que continuam obrigatórios para todo OCR.

Use como referência executável
`tests/test_ocr_pipeline_architecture.py::test_registered_fifth_engine_flows_through_cli_authorization_and_integration`.
Ele registra uma quinta engine fake e percorre configuração, CLI, factory,
render autorizado, integração e saída. Não comprova qualidade de um runtime real.
`tests/test_registered_engine_runners.py` também registra uma engine depois de
importar os runners e percorre o benchmark E2E, identidade, artefatos e proteção
contra sobrescrita. Os parsers de benchmark, smoke de célula e endurance devem
consultar `public_engines()` e `public_providers()` durante a chamada, sem
congelar as listas no import. O teste percorre também um provider próprio da
quinta engine.

Antes de declarar suporte, execute o gate de imports, testes de contrato e
serviços, negativas de pixels/resultados externos, conservação e cancelamento.
Depois valide identidade, pesos, provider e corpus em processos separados e
nas plataformas declaradas. Use namespaces novos para capturas e comparações.
Os gates atuais estão no
[estado de implementação](OCR_Decoupling_Implementation_Status.md).

## Compatibilidade e ciclo de vida

`OcrEngine`, `OCRBackend` e a injeção pública permanecem suportados. O adapter
legado é mantido para consumidores existentes; os backends raw continuam
disponíveis para benchmarks explicitamente raw. Eles não substituem o caminho
autorizado de produção. `close()` é terminal e idempotente. Carregamento e
inferência não têm deadline imposto pelo projeto; limites de memória e
trabalho continuam ativos. EasyOCR e RapidOCR verificam cancelamento entre
chamadas, sem promessa de interromper uma chamada nativa em curso.
