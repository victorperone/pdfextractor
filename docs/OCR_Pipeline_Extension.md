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
    CellRecoveryPolicy, ConservativeIntegrationPolicy, FigureSegmentationPolicy,
    RegionalRecoveryProfile, TokenRefinementPolicy,
)

class MinhaPipeline(AuthorizedBackendPipeline):
    recovery_policy = RegionalRecoveryProfile()
    integration_policy = ConservativeIntegrationPolicy("minhaocr-integration-v1")
    cell_policy = CellRecoveryPolicy()
    token_refinement_policy = TokenRefinementPolicy()
    fusion_policy = CandidateFusionPolicy()
    figure_segmentation_policy = FigureSegmentationPolicy()

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

## Integração e validação

`ProposalIntegrator` aplica as propostas aos alvos vinculados pelo núcleo.
Substituições conservam conteúdo confiável, sinais, valores, ownership e
linhagem. Seleções de tiles guardam suas autorizações originais e são aplicadas
juntas; tentativas descartadas não podem alterar os alvos. Não invoque um
backend raw diretamente de API, tabelas ou figuras.

Use como referência executável
`tests/test_ocr_pipeline_architecture.py::test_registered_fifth_engine_flows_through_cli_authorization_and_integration`.
Ele registra uma quinta engine fake e percorre configuração, CLI, factory,
render autorizado, integração e saída. Não comprova qualidade de um runtime real.

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
