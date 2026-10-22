# ADR 0001 — Pipelines OCR autorizados e integração central

Data: 22/10/2026. Estado: decisão implementada; homologação de release pendente.

## Contexto

Reconhecimento, refinamento regional e integração compartilhavam decisões que
impediam evoluir uma engine sem afetar as demais. O desacoplamento precisa
preservar o extrator nativo e impedir que variantes de OCR ampliem o acesso a
pixels ou substituam conteúdo confiável sem validação.

## Decisão

Uma extração seleciona uma família OCR e, quando aplicável, um provider.
Registry e factories fazem a composição lazy. Cada pipeline possui settings e
policies próprias de recuperação, ranking, fusão, células, tokens e segmentação
de figuras. Nenhum pipeline importa implementação privada de outra engine.

O núcleo escolhe objetivos e escopos. `PageCoordinator` emite requests imutáveis;
`AuthorizedOCRServices` entrega crops próprios, masks e transforms reversíveis,
com orçamentos finitos de trabalho e raster. Resultados são conferidos contra
a união exata das autorizações, sem tolerância que amplie acesso.

A representação de borda é uma policy local: `mask` mantém pixels parciais
brancos; `trim` remove esses pixels e valida um novo frame estritamente
contido na autorização original. RapidOCR usa `trim`; as outras engines
mantêm `mask`. Essa alteração funcional exige captura própria e não permite
aprovar reconhecimento espúrio apenas porque o resultado ficou dentro do escopo.

Policies propõem operações; `ProposalIntegrator` valida conservação,
visibilidade, estado do alvo, ownership e linhagem antes de aplicar. Composição
de evidências para dados críticos exige ordem explícita e preserva a posição
do token substituído. Refinamentos de figuras aplicam o stream da página e a
associação à região juntos. Tabelas visuais usam um draft de estrutura validado
contra os tokens observados; a engine não altera a grade diretamente.

Autorizações distintas de tiles permanecem distintas na seleção final.
Tentativas não selecionadas são descartadas. Snapshots e recibos impedem
mutação parcial, aplicação contra alvo alterado e replay inconsistente.

Backends raw e adapters públicos continuam disponíveis para seus consumidores.
A compatibilidade não dispensa o serviço autorizado na extração de produção.
Não se apagam interfaces úteis para cumprir uma meta de remoção de wrappers.

Inferência e inicialização não expiram por duração. Cancelamento de subprocessos
interrompe a espera e limpa recursos; chamadas nativas in-process têm cancelamento
cooperativo nas fronteiras. Prazos de limpeza após cancelamento não limitam OCR.

## Consequências e aceite

Alterações comuns exigem regressão cruzada; tuning local exige equivalência e
qualidade da engine alterada. A fixture de quinta engine valida extensibilidade,
não reconhecimento real. Metadados de modelo não substituem hashes capturados
na execução. Mudança de modelo RapidOCR v6 é uma comparação de modelo própria,
separada da equivalência estrutural.

A suíte automatizada, smokes e controles reduzidos não aprovam corpus completo,
Windows ou uma instalação limpa que não foi executada. Diferenças da baseline,
incidentes de escopo, falhas semânticas e defeitos nativos conhecidos continuam
gates abertos no [relatório de estado](../OCR_Decoupling_Implementation_Status.md).
O [guia de extensão](../OCR_Pipeline_Extension.md) documenta a composição atual.
