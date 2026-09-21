# Revisão condicional de OCR — etapa `916e1dc`

## Conclusão

A entrega anterior foi auditada no repositório real. O commit atual é
`916e1dc30af66bb74b9977438e2bc0de5a06521c`, o working tree estava limpo no
início da revisão e `origin/feat/ocr-adaptive-quality` apontava para o mesmo
SHA. A comparação com `f18a03149c251a8b48b26c251484db642027ee02` contém apenas
infraestrutura, manifesto, testes e documentação do corpus; não há mudança em
`src/structured_pdf_text`.

O corpus existente foi reutilizado para os controles já validados porque seu
PDF principal e seus subconjuntos permaneciam consistentes com o relatório
anterior. Durante a investigação da sequência de tabelas, a página 30 revelou
que o cenário declarado como multilinha não era representado pelo gerador. O
gerador foi corrigido antes de interpretar o OCR da página 30, o manifesto e o
PDF foram regenerados e a nova integridade foi testada. A validação estrutural
continua confirmando 60 páginas, 37 com camada nativa e 23 raster puras,
manifesto consistente com o PDF e ordem de origem `1..60`.

## Verificação da entrega

| Arquivo/funcionalidade | Alteração na entrega anterior | Teste executado | Resultado | Risco pendente |
|---|---|---|---|---|
| `generate.py` | Gerador determinístico e recortes reais da figura 15–16 | `generate.py --help`; manifesto/PDF; teste específico de continuidade | aprovado | continuações de outros pares não são imagens-fonte compartilhadas |
| `manifest.json` | Metadados de regiões, origem, camadas e `shared_image_id` | SHA do PDF, 60 páginas, dimensões e ordem | aprovado | Code128 gerado, mas não decodificado localmente |
| `test_ocr_stress_corpus.py` | Integridade, subconjuntos e regressão 15–16 | `pytest -q tests/test_ocr_stress_corpus.py` | aprovado | nenhum |
| `corpus_validation.md` | Evidências estruturais e baseline anterior | revisão dos hashes e artefatos | aprovado | inspeção visual continua amostral |
| página 30 do gerador | Cenário corrigido para células raster multilinha | teste de manifesto e extração baseline p30 | corpus aprovado; OCR reproduziu a falha de associação de linhas | assembly de tabela permanece pendente |
| produção OCR/texto nativo | nenhuma alteração | `git diff f18a031..HEAD -- src/structured_pdf_text` | nenhum arquivo alterado | nenhum bloqueio nesta etapa |

Comandos executados nesta revisão:

```bash
python3 tests/corpus/ocr_stress_v1/generate.py --help
PYTHONPATH=src python3 -m structured_pdf_text.cli extract --help
python3 -m compileall -q src tests
pytest -q
git diff --check
```

Todos terminaram com sucesso. Nenhum commit, push, merge ou PR foi feito nesta
rodada.

## Diagnóstico OCR incremental

Foi reutilizado o baseline anterior para as páginas 1, 2, 13–14, 15–16 e 19,
pois os PDFs e a implementação correspondem ao corpus atual. O controle
negativo 24 foi executado nesta revisão com:

```bash
PYTHONPATH=src python3 -m structured_pdf_text.cli extract \
  /tmp/ocr-stress-audit/subsets/p24/Document_OCR_Stress_V1_pages_24.pdf \
  --mode balanced --ocr-quality-policy baseline --output markdown \
  -o /tmp/ocr-stress-audit/p24.md
```

| Página de origem | Cenário | Política | Observação | Resultado |
|---:|---|---|---|---|
| 1 | controle nativo | baseline | marcador nativo uma vez | aprovado |
| 19 | imagem decorativa sem texto | baseline | texto nativo preservado, sem OCR inventado | aprovado |
| 2 | raster simples | baseline | marcador, acentos, moeda, data e percentual recuperados | aprovado |
| 13 | misto com comprovante raster | baseline | texto da imagem junto ao texto nativo, sem duplicação observada | aprovado |
| 14 | misto com bloco raster | baseline | bloco raster e texto nativo preservados | aprovado |
| 24 | raster textual + gráfico sem texto | baseline | `OCRS-024` recuperado; o rótulo do gráfico vem da camada nativa, não de OCR indiscriminado | aprovado |
| 15–16 | figura contínua em paisagem | baseline | dois marcadores, conteúdo comum e duas páginas preservados | aprovado |

As saídas estão em `/tmp/ocr-stress-audit/`, com Markdown, logs, manifestos e
estatísticas de tempo. O lote p24 terminou com exit code 0, sem linhas de erro,
em 9,85 s de parede, com pico de 2.472.668 kB.

O `adaptive` não foi executado nos controles aprovados. Ele foi executado
somente depois da falha reproduzida na página 29, conforme a regra do plano, e
seus resultados estão registrados abaixo.

## Página 30: falha de associação de células multilinha

O gerador foi corrigido para desenhar quebras de linha reais nas células e
registrar essa intenção no manifesto. Com o corpus corrigido, o OCR reconheceu
os textos secundários (`mensal`, `estimado`, `revisado`, `controle`, `interno`,
`atenção`), mas a tabela foi serializada como linhas adicionais com células
vazias nas demais colunas. O JSON mostrou sete linhas físicas, enquanto o
cenário exige quatro linhas lógicas com conteúdo multilinha.

O diagnóstico separa as etapas: os tokens aparecem em sete linhas OCR com
geometria coerente; a tabela contém todos os tokens, mas cria linhas 2, 4 e 6
para as continuações. Não houve perda no detector OCR. A origem comprovada é a
associação/serialização de tabela após o reconhecimento.

Esta correção está bloqueada nesta rodada: o plano proíbe modificar módulos de
texto/assembly sem autorização específica. Não foi alterado código de produção
para mascarar a falha.

## Decisão e próximo passo

Não há correção de OCR autorizada por evidência nesta etapa. O diagnóstico não
chegou a uma perda nos estágios de enumeração, seleção, recorte, detecção,
reconhecimento ou montagem; portanto, não há módulo de produção para alterar.

Próximo passo: após a revisão deste relatório, selecionar o primeiro caso OCR
restante com falha reproduzida — priorizando tabelas 27–40 ou orientação/figuras
42–50 — e executar o diagnóstico por estágio. Só então implementar, no máximo,
uma correção pontual de OCR regional. Nenhuma alteração de texto nativo,
assembly, reading order, ledger ou deduplicação está autorizada por este plano.

## Primeiro defeito restante: página 29

A página 28 foi aprovada com `baseline`. A página 29, primeira variante
seguinte, reproduziu uma falha de reconhecimento:

| Política | Resultado observado | Evidência de estágio |
|---|---|---|
| `baseline` | `Fictício` saiu como `Ficticio`; `C` saiu como `c`; o título saiu como `—TABELA` | OCR de página solicitado; 17 tokens detectados; qualidade `1.0`; variante selecionada `baseline` |
| `adaptive` | corrigiu `C` e o espaço do título, mas manteve `Ficticio` | tentou `baseline`, `sharpness` e `unsharp`; selecionou `sharpness`; 16 células preservadas |

O `inspect` da execução baseline confirmou: região `page-1:region-1`
classificada como `escalate_page_ocr`, `page_ocr_requested=true`, uma passagem
OCR, tabela `text_tracks` válida, 16 células, cobertura de tokens `1.0`,
conflitos de associação `0` e nenhuma substituição/duplicação na fusão. Logo,
a falha surge no reconhecimento dos pixels, antes da montagem. O `inspect`
adaptive também confirmou cobertura de células `1.0`; nenhuma variante
produziu a grafia acentuada necessária.

O baseline recebeu qualidade suficiente apesar do erro semântico porque as
confianças e a cobertura espacial eram altas. Não é seguro corrigir isso com
substituição textual, léxico ou regra para o conteúdo sintético. Também não há
autorização neste plano para trocar modelo, resolução, runtime ou alterar
globalmente o critério de qualidade com base em uma única palavra.

Este é o ponto de parada do ciclo: não foi feita alteração em `src/` e nenhum
teste de produção foi criado para mascarar a limitação do reconhecedor. Os
artefatos completos estão em `/tmp/ocr-stress-audit/p28*`, `/tmp/ocr-stress-audit/p29*`
e `/tmp/ocr-stress-audit/p29-inspect-*`.

## Correção autorizada: células raster multilinha

Com autorização específica, a etapa seguinte alterou somente
`tables/text_tracks.py`. A detecção agora agrupa uma linha que sobrepõe
verticalmente a linha anterior e ocupa um subconjunto dos mesmos tracks de
coluna; a serialização ordena os grupos por `y` dentro de cada célula e une o
conteúdo com espaço. Linhas normais com separação vertical continuam sendo
linhas distintas.

Validações da correção:

- teste unitário: `test_borderless_detector_keeps_wrapped_cell_lines_in_one_row`;
- página 30 baseline antes/depois: de 7 linhas físicas/linhas extras para 4
  linhas lógicas e 16 células;
- saída Markdown final preserva `123,45 mensal`, `67,89 estimado`,
  `90,12 revisado`, `Fictício controle`, `Controle interno` e `V1 atenção`;
- páginas 27–29 repetidas sem erros; páginas 27 e 28 mantiveram os valores
  anteriores e a página 29 manteve o conteúdo residual já diagnosticado;
- controles 1, 2, 13, 14, 19 e 24 repetidos sem erro, duplicação ou alteração
  de texto nativo;
- `python3 -m compileall -q src tests`, `pytest -q` e `git diff --check`
  aprovados.

Não houve alteração no modo `native`, no OCR regional, no modelo, na resolução,
no runtime ou no ledger. O próximo risco residual é a qualidade de
reconhecimento da página 29 (`Ficticio` sem acento), que não foi corrigida por
esta mudança estrutural.
