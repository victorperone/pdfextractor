# Validação manual do corpus — M4/M5/M7/M8/M9 e OCR

Data da execução: 2026-09-13  
Modos: `balanced` para OCR e `native` para a varredura integral; modelos
PaddleOCR em cache local.

Esta validação é uma inspeção de execução real, não uma métrica de acurácia.
Os marcadores abaixo foram conferidos contra o conteúdo esperado indicado no
próprio corpus.

## Resumo da execução

| Documento | Páginas | Tabelas | Warnings | Tempo |
|---|---:|---:|---:|---:|
| `benchmark_controlado_v1.pdf` | 12 | 7 | 0 | 262,5 s |
| `Document_AI_V2.pdf` | 42 | 7 | 0 | 410,9 s |

O arquivo `benchmark_03_medium_268.pdf` também foi processado integralmente em
modo nativo após as correções: 268 páginas, 444.648 caracteres brutos, zero
warnings, pico RSS de aproximadamente 444 MB e 14 tabelas por trilhas de texto.
Em validações `balanced` direcionadas, uma matriz visual 14×8 foi preservada e
gráficos/diagramas sem células textuais foram rejeitados como tabelas.

## Pontos verificados

| Páginas | Cobertura | Resultado observado |
|---|---|---|
| Benchmark 1–2 | cabeçalho, título, acentos e tabela nativa | recuperados; tabela e caracteres especiais preservados |
| Benchmark 8 | fórmulas e tabela | fórmulas nativas preservadas; tabela recuperada |
| Benchmark 9–11 | tabelas raster | OCR recuperou células; página 10 usou rotação 270° e página 11 rotação 0° |
| DocumentAI 15–16 | tabelas largas/estreitas | texto e linhas recuperados; células estreitas mantidas como linhas de tabela |
| DocumentAI 19–20 | tabela multipágina | fragmentos unidos; sequência PED-2026001 a PED-2026030 presente |
| DocumentAI 22 | gráficos e eixos | títulos, `R$ mil`, `Quantidade`, meses `Jan`–`Jun`, percentuais e valores `170`–`120` recuperados por OCR ampliado |
| DocumentAI 26–30 | OCR limpo, contraste, ruído, inclinação e fonte pequena | páginas 28–30 validadas em execuções direcionadas; códigos e valores principais presentes, com ruído residual apenas em caracteres difíceis |
| DocumentAI 31–33 | rotação física | rotações 90°, 180° e 270° corrigidas, com códigos `GS2-ROT-*` presentes |
| DocumentAI 34 | rotação por metadado/camada nativa | o sinal visual permanece diagnóstico, o texto nativo é preservado e OCR desnecessário foi suprimido; texto não duplica |
| DocumentAI 35 | camada oculta incorreta | OCR foi promovido a fonte principal; `GS2-VISIBLE-035`, `R$ 3.535,35` e `APROVADO` prevalecem |
| DocumentAI 36 | marca d’água e carimbo | corpo principal recuperado; marca d’água aparece fragmentada, mas não substitui o corpo |
| DocumentAI 39–42 | redação visual, contrato e marcador final | conteúdo nativo e `GS2-END-OF-CORPUS-42` recuperados |

## Correções aplicadas após a inspeção

1. OCR tornou-se fonte principal em páginas predominantemente rasterizadas,
   evitando que uma camada textual oculta incorreta seja mantida.
2. `invisible_text` é tratado apenas como evidência de complexidade; linhas
   nativas não são apagadas antes da decisão regional/OCR.
3. Foram adicionadas variantes de pré-processamento para ruído, baixo contraste e
   fonte pequena.
4. Eixos, meses, legendas e rótulos pequenos dos gráficos recebem OCR ampliado.
5. A seleção de variantes funde tokens compactos de alta confiança quando o
   ruído fragmenta um valor ou código em vários pedaços.
6. Variantes OCR são agrupadas em lotes limitados; relatórios podem usar
   multiprocessing opt-in por documento.
7. O tamanho do batch e o uso de variantes de qualidade agora são configuráveis
   pela API e pela CLI, mantendo por padrão a configuração de maior recall.
8. Um refinador genérico por região centraliza crop, ampliação, rotação,
   remapeamento de coordenadas e seleção/fusão de hipóteses textuais ou
   numéricas; tabelas e figuras usam o mesmo componente.
9. A leitura da página 22 recuperou em uma execução final os três títulos dos
   gráficos, `R$ mil`, `Quantidade`, `Jan`–`Jun`, `170`–`120`, `8`–`0` e os
   quatro percentuais esperados, sem warnings.
10. O pipeline passou a executar layout e quality gate antes do OCR. Em
    execução real, a página 22 acionou somente a região da figura (`mixed`), a
    página 30 promoveu OCR integral, a página 34 ficou exclusivamente nativa e
    a página 35 priorizou o raster visível sobre a camada oculta incorreta.
11. Text tracks recuperaram a tabela sem bordas da página 17 como 5×5 e as
    tabelas digitais das páginas 13, 14 e 18; o classificador de prosa rejeitou
    corretamente os layouts em colunas das páginas 8 e 9.
12. A resolução multipágina uniu apenas as tabelas 19–20 no documento maior e
    10–11 no benchmark. Pares adjacentes independentes foram preservados e
    cada aceitação/rejeição passou a expor score, razões e fatos geométricos.
13. Uma execução nativa integral do benchmark registrou p50/p95/p99 por
    estratégia, aquisições/estimativa FFI e pico RSS de aproximadamente 108 MB,
    processando as 12 páginas sem warnings.
14. O dump nativo preservou RGBA, render mode e matriz em caracteres/objetos;
    na página 7 do benchmark, objetos tagged também expuseram MCID e a anotação
    `link` informou bbox e contagem de objetos sem falhar por appearance ausente.
15. A caixa efetiva do PDFium passou a definir o sistema de coordenadas quando
    MediaBox/CropBox herdados divergem do render; isso eliminou deslocamentos de
    aproximadamente 50 pontos no documento de 268 páginas.
16. Evidência PDFium detalhada tornou-se opt-in no resultado estruturado e é
    ativada automaticamente por `inspect --raw-page-json`; o pico RSS no arquivo
    de 268 páginas caiu de aproximadamente 965 MB para 444 MB.
17. A reconstrução nativa passou a preservar espaços em fontes itálicas e
    glifos de linha de base (`_`, vírgula e ponto), recuperando identificadores
    como `render_cppn` e expressões como `norm_x`.
18. Os classificadores de tabela agora rejeitam prosa com regras decorativas,
    blocos de código, gráficos sem células e diagramas de baixa confiança; as
    tabelas raster de controle mantiveram 35/35 células nas três orientações.
19. OCR de figura suprime grupos de pictogramas grandes e repetidos confundidos
    com caracteres, mantendo pequenos rótulos e números de eixos.

## Pendências de qualidade

- a página 29 ainda pode melhorar a acentuação de textos inclinados (`OCR` e
  caracteres de `SKEW`); a variante de deskew foi avaliada, mas não ficou
  ativada automaticamente porque piorou a ordem espacial em uma execução;
- gráficos ainda não são convertidos em dados semânticos, apenas em texto;
- a validação continua sendo manual contra a imagem original, pois o corpus não
  fornece ground truth textual completo para cada caractere.
