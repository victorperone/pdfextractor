# Desacoplamento OCR — primeira etapa incremental

> Registro histórico da primeira entrega. O escopo foi ampliado para PR-00–PR-14;
> consulte o [estado atual da implementação](OCR_Decoupling_Implementation_Status.md).
> As descrições abaixo caracterizam aquele ponto da migração.

Base: `54885e699190015c0bd4c026a3b24f2b296aed8c`.
Escopo autorizado: PR-01 a PR-03 do [plano](Plan_Decoupling_OCR_Engines.md).
Os números identificam etapas do plano; não representam PRs publicados.

O trabalho cria uma fronteira utilizável entre seleção central e reconhecimento.
O primeiro consumidor é `LegacyInjectedPipeline`, testado com source PDFium
real e engines de teste. A produção continua no caminho existente de
`PdfTextExtractor`: a composição pelo registry, a injeção pública de pipeline e
a migração das estratégias de cada engine começam no PR-04 e seguintes.

## PR-01: isolamento e regressão

`scripts/check_ocr_dependencies.py` verifica imports estáticos, relativos e
imports dinâmicos literais conhecidos, incluindo aliases. Um pipeline/backend
não pode importar implementação ou biblioteca OCR de outra engine. Mecanismos
neutros não importam engines ou frameworks. A classificação aceita uma quinta
engine sem acrescentá-la à lista de famílias conhecidas.

O gate conhece os caminhos históricos específicos de Paddle. Não tenta provar
dependências de expressões Python arbitrárias. Fixtures artificiais comprovam
que imports proibidos são detectados mesmo sem diretórios futuros preenchidos.

`GLOBAL_PATHS` e `regression_engines()` registram a matriz: código compartilhado,
composição, contratos, geometria, tabelas e CI exigem as quatro engines;
alterações locais exigem a engine correspondente, além dos controles core.
Arquivos de produção não classificados são tratados como globais. O job core
existente continua em Ubuntu/Windows com Python 3.12 e `.[dev]`, sem instalar
frameworks OCR ou modelos, e executa o gate antes do pytest.

Os exports dos pacotes raiz e `ocr` passaram a ser lazy, preservando nomes,
`__all__`, `dir()`, identidade das classes e imports antigos. Isso permite
importar os novos contratos sem carregar PIL, NumPy ou PDFium. Configuração,
CLI, registry e factory também são verificados em processos novos para impedir
carga de frameworks OCR. Os arquivos de `native/` permanecem byte a byte iguais.

O import pesado de `easyocr.config` pelo probe estático já existia no baseline.
Um `xfail(strict=True)` o caracteriza, com remoção prevista na migração de
readiness do PR-04. Não é apresentado como isolamento já corrigido.

## PR-02: contratos e compatibilidade

Os módulos novos ficam em `src/structured_pdf_text/ocr/pipeline/`:

| Módulo | Responsabilidade |
| --- | --- |
| `contracts.py` | Requests/resultados versão 1, seleção/provider, identidade efetiva, escopo, autorizações, evidência, propostas, falhas e recursos |
| `converters.py` | Fronteiras explícitas com `document.OcrToken` e `ocr.contracts.OCRToken` |
| `geometry.py` | Frame raster real e composição affine com os mecanismos existentes |
| `serialization.py` | Projeção JSON finita `ocr-pipeline-1.0`, independente do avaliador |
| `adapters.py` | Compatibilidade opt-in com uma engine legada injetada |

Snapshots são copiados recursivamente para `FrozenMap`/tuplas. Objetos mutáveis
do documento, boxes vazios e valores não finitos são rejeitados. Autorizações
e resultados carregam documento, extração, página, alvo/revisão e versão da
evidência. IDs não substituem a validação dessas relações.

Confiança raw mantém valor e escala nativos. Só escalas declaradas `0..1` e
`0..100` produzem confiança normalizada, com conversão explícita. Ao receber
um token do domínio, o adapter conserva sua confiança normalizada e registra
que o valor nativo é desconhecido. Polígonos, nível, origem e proveniência são
preservados. Os dois tipos antigos continuam distintos e importáveis.

O adapter decide a assinatura uma vez, recebe somente crops autorizados e
orientados nos eixos canônicos, preserva evidência válida em resultados parciais
e não repete uma chamada após `TypeError` interno. Engine sem identidade recebe
identidade `custom`/desconhecida. Não é presumido um perfil Paddle e nenhuma
outra factory é consultada. O fechamento da engine é terminal e idempotente.

`adapt_injection()` rejeita dupla injeção antes de construir um adapter. É um
helper para a composição futura; `PdfTextExtractor` ainda não ganhou parâmetro
`ocr_pipeline`. `ResultReceiptLedger` caracteriza recepção idempotente, sem
aplicar alterações ao documento. A integração atômica pertence ao PR-06.

## PR-03: autorização, raster e recursos

`AuthorizationRegistry` pertence ao núcleo. O serviço confere o registro
central, o payload integral da autorização e a revisão atual antes de render.
A contenção é calculada na união exata dos retângulos. Um retângulo envolvente
que atravessa o vão entre duas regiões é negado, mesmo com centro autorizado.
Tokens e polígonos também são validados; transcrições externas não são
recortadas para parecer válidas.

`AuthorizedOCRServices` mantém o source privado e entrega `RasterLease` com
pixels próprios, frame imutável e liberação explícita. O wrapper adapta a
assinatura de render com/sem `page_rotation`, mantendo o protocolo nativo
inalterado conforme a restrição desta entrega.

O frame usa as dimensões realmente retornadas pelo PDFium. O serviço modela
o arredondamento inteiro de crops, traduz origem canônica deslocada e desfaz
as quatro rotações físicas. Pixels de borda cujo footprint atravessa o limite
solicitado são preenchidos de branco. Isso pode eliminar pixels em um crop
fracionário muito estreito; não amplia o contexto autorizado para recuperá-los.
Crop de um pixel inteiro permanece suportado.

Resize, rotação/deskew com canvas expandido, padding e tiles criam novas leases
com transformações inversíveis. O padding contém pixels sintéticos. O serviço
não entrega o bitmap original de imagem embutida sem prova de equivalência
visual: nesta etapa, a variante confere o índice e a posição do objeto na
evidência nativa e usa sempre a composição visível do PDF.

`ResourcePool` reserva RGB/cópias antes da alocação. Pais e filhos compartilham
contabilidade atômica. Liberar uma lease devolve bytes vivos; não devolve
trabalho acumulado, rasters ou tentativas. Settings do request não aumentam os
tetos centrais nem os limites mais restritivos do source. O serviço rejeita
resolução que dispararia a redução automática existente no renderizador;
não escolhe escala ou modelo alternativo. Bloqueio preventivo é distinguido de
OOM real, que conserva a classificação fatal de recurso.

O orçamento cobre alocações RGB controladas pelo projeto, sem garantir teto de
RSS dos tensores internos de bibliotecas. O cancelamento é cooperativo nas
fronteiras de render/tentativa; a interrupção de runtimes e a remoção dos
deadlines legados permanecem no PR-11. Não foram adicionados limites em segundos.

## Evidência de validação

- Testes de imports em subprocessos e contagem zero/um de construção para as
  quatro engines; modos `native`/`fast` também proíbem raster.
- Roundtrip raw/domínio, confiança, polígonos, origem, rotações e affines.
- Integração real com PDFium: quatro rotações, CropBox deslocado, escala
  fracionária, crop de um pixel e composição de imagem coberta.
- Negativos de vão, ID forjado, revisão antiga, bbox/polígono externo, overflow,
  canvas expandido, falha de render, OOM, cancelamento e liberação de reservas.
- Pixel marcador independente verifica a transformação de rotações candidatas.
- Comparação real reduzida do caminho público com as referências de V5, em
  processos separados e com as mesmas configurações/modelos; resultados e
  limites são registrados junto dos artefatos, sem confundir parcialidade com
  aprovação de qualidade.

Suíte completa: `2641 passed, 6 skipped, 5 xfailed`; o xfail novo caracteriza a
importação pesada pelo probe estático EasyOCR, atribuída ao PR-04. A validação
real reduzida de sete páginas V5 passou para Tesseract, RapidOCR ONNX/OpenVINO,
EasyOCR e PaddleOCR: Markdown idêntico, zero diferenças estruturais e zero
erros de identidade. Documentos `success` com avaliação `partial` servem como
validação de equivalência, não de qualidade. A execução local é Linux/WSL2; o
job Windows foi configurado, mas sua execução remota não foi realizada.

O [baseline](OCR_Decoupling_Baseline_2026-10-20.md) conserva artefatos, hashes,
modelos, limitações de avaliação e defeitos anteriores. Vazamento nativo de
conteúdo coberto V3 e perda de CNPJ em listas nativas V5 permanecem fora desta
alteração, conforme a restrição de não modificar o extrator nativo.

## Atualização de implementação — 21/10/2026

Este relatório registra a fatia original PR-01–03 e não descreve o estado atual
do caminho de produção. A composição por registry, `PdfTextExtractor` e a
execução por `PageCoordinator`/`OCRPageFacade` foram conectadas depois; pedidos
regionais, rotações e tiles também passam pelo serviço autorizado. O estado
atual, testes recentes e gates ainda pendentes estão em
[`OCR_Decoupling_Implementation_Status.md`](OCR_Decoupling_Implementation_Status.md).
