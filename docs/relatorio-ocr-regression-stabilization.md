# Relatório — OCR regression stabilization

## Escopo e identificação

Validação executada em 22/09/2026 no WSL, sem commit, push ou merge.

| Item | Valor |
|---|---|
| Branch de trabalho | `feat/ocr-regression-stabilization` |
| Branch de referência | `feat/ocr-adaptive-quality` |
| SHA inicial e da referência | `0461d04f905bec78192df0c4e9ad6a04a4022209` |
| Python local | `3.10.12` (o projeto declara `>=3.12`; lacuna do ambiente) |
| PaddlePaddle / PaddleOCR / PaddleX | `3.3.1 / 3.7.0 / 3.7.2` |
| PDFium / Pillow / pytest | `pypdfium2 5.13.0 / 10.3.0 / 9.1.1` |
| Modelos | `PP-LCNet_x1_0_doc_ori`, `PP-LCNet_x1_0_textline_ori`, `PP-OCRv5_server_det`, `latin_PP-OCRv5_mobile_rec` |
| Estado dos modelos | `Offline OCR readiness: READY` |
| Corpus | 60 páginas; PDF SHA-256 `be42cdfe9ee3f161b3ed04a0eb2edbaf41b5ae1dd06755b1be8df564b6d8f15d` |

O `numpy` efetivamente carregado pelo runtime foi `1.26.3`; o arquivo de
dependências fixa `2.3.5`. Essa diferença foi registrada e não foi alterada.
Também apareceram os warnings de `ccache` ausente e de compatibilidade
`urllib3/chardet`; nenhum foi convertido em warning funcional do OCR.

## Implementação auditada

As políticas existentes permanecem como implementadas:

- `baseline`: uma passagem por página/região;
- `adaptive`: baseline seguido somente das variantes acionadas pelos sinais de
  qualidade, com parada antecipada quando há candidato suficiente;
- `exhaustive`: executa todas as variantes elegíveis para diagnóstico.

O default da configuração é `adaptive`. A cadeia regional existente solicita
as escalas `(1.0, 1.5, 2.0)` quando a região é elegível; o orçamento RGB de
8 MiB decide quais podem ser executadas. A variante 2×, seus parâmetros,
transformações, critérios e logs não foram modificados. Nenhum motor novo,
modelo novo ou dependência de execução foi integrado.

Os diagnósticos existentes foram preservados e permitiram acompanhar:

- política, variante selecionada, tentativas e métricas dos candidatos;
- `REGION_SELECTED`, `OCR_SCALE_PLAN`, dimensões da imagem e do recorte;
- início/fim de cada chamada e lote, duração, RSS, memória disponível e
  versões do runtime;
- resultado final, linhas suplementares, tokens não associados e warnings de
  geometria.

O log foi habilitado apenas com `PDFEXTRACTOR_OCR_DEBUG_LOG` apontando para
arquivos em `/tmp/ocr-regression-stabilization/`; nenhum log de produção foi
alterado.

## Cobertura adicionada

Foi adicionado `tests/test_ocr_adaptive_upscale_regressions.py`, somente com
imagens e bboxes sintéticos. Os testes verificam o comportamento já existente
da variante 2×:

1. recorte da região e dimensões `2×` entregues ao engine;
2. mapeamento das coordenadas ampliadas de volta para a página;
3. interseção correta de uma região parcialmente visível;
4. bloqueio da escala 2× somente quando o orçamento RGB existente é excedido.

O teste não impõe nova regra de seleção, qualidade ou resolução e não carrega
PaddleOCR.

## Execuções incrementais

Todos os comandos usaram o gerador real do corpus e `PYTHONPATH=src`; a CLI de
extração não possui seleção de páginas, enquanto `inspect --page` seleciona uma
página. Saídas, logs e métricas foram preservados em
`/tmp/ocr-regression-stabilization/`.

| Subconjunto | Política | Resultado observado | Tempo / pico RSS |
|---|---|---|---|
| página 2 | baseline, adaptive | marcador e conteúdo recuperados uma vez em cada política; Markdown idêntico | 23,26 s / 10,7 GiB; 1:19,23 / 10,8 GiB |
| páginas 13, 14, 19, 24 | baseline, adaptive | texto nativo preservado, regiões raster recuperadas, decoração sem texto não inventou conteúdo; quatro controles uma vez | 26,18 s / 4,8 GiB; 1:36,39 / 4,9 GiB |
| 15, 16, 27–30 | baseline | continuação e tabelas processadas; controles uma vez e `OCRS-CONTINUA-15-16` duas vezes | 2:00,51 / 10,7 GiB |
| 42, 46–50 | baseline | paisagem, QR, barras, múltiplas imagens e figura parcial processados; figura fora da página rejeitada geometricamente | 35,30 s / 10,7 GiB |
| 52, 55–58, 60 | baseline | integração, tabelas, continuação e QR/barras processados; controles uma vez | 50,32 s / 6,3 GiB |
| página 10 | baseline, adaptive | falha reproduzida: `OCRS-P10-CONTROL` ausente nas duas políticas; adaptive recuperou menos conteúdo | 22,26 s / 10,7 GiB; 1:15,79 / 10,9 GiB |

Nos cenários elegíveis, os logs mostraram `allowed_scales=1.0,1.5,2.0` e
chamadas concluídas com `result=OK`. A página 50 gerou somente o warning
esperado `figure_ocr_skipped: ... no_visible_area_in_page`.

## Corpus completo

O corpus completo foi executado com `balanced/baseline`, terminou com exit code
0 e processou 93 chamadas OCR. Foram preservados 59 dos 60 marcadores de
controle: `OCRS-P10-CONTROL` ficou ausente. Tempo de parede: `10:57,50`;
pico RSS: `11.344.284 kB`. O único warning funcional foi o da figura
completamente fora da área visível na página 50.

A página 10 foi reproduzida isoladamente:

- baseline reconheceu linhas parciais (`Texto raster...`, conteúdo posterior e
  `OCRS-SCAN-10-B`), mas não reconheceu o marcador nem a primeira linha
  completa;
- adaptive selecionou uma saída ainda menor e também não reconheceu o
  marcador;
- `inspect` mostrou `page_ocr_requested=True`, `ocr_outcome=success`,
  `ocr_selected_variant=baseline`, uma linha suplementar aceita e três tokens
  não associados; não houve erro de recorte, crash, perda no assembly ou
  warning de geometria.

Conclusão do diagnóstico: a perda ocorre na detecção/reconhecimento dos pixels,
antes da incorporação final. Não há evidência para alterar seleção regional,
geometria, upscaling 2×, montagem textual ou ledger. O defeito permanece
registrado para uma rodada autorizada de qualidade do reconhecedor; não foi
mascarado com regra para o corpus.

## Verificações finais e limitações

- A suíte completa foi executada antes e depois da cobertura adicionada; a
  execução final, a compilação e `git diff --check` devem ser repetidas antes
  da entrega desta branch.
- Não houve alteração em `src/structured_pdf_text/ocr/recovery.py`, no
  upscaling 2×, no modo `native`, no processamento textual, na montagem ou no
  Content Conservation Ledger.
- O incidente Windows `0xC0000005` em `paddle\libs\phi.dll` não reapareceu no
  WSL e continua independente. Não houve acesso ao Windows Server neste
  ambiente; portanto, nenhum documento empresarial, runtime nativo Windows ou
  comparação WSL/Windows foi validado. Essa é uma lacuna de execução, não uma
  aprovação implícita do Windows.

## Arquivos e commits sugeridos

Não foram criados commits. Para revisão, sugere-se separar a alteração em:

1. `test(ocr): protect existing 2x regional recovery contract`
   - incluir somente `tests/test_ocr_adaptive_upscale_regressions.py`;
2. `docs(ocr): record regression stabilization validation`
   - incluir somente este relatório.

Não incluir artefatos de `/tmp`, modelos, PDFs gerados ou arquivos
empresariais.
