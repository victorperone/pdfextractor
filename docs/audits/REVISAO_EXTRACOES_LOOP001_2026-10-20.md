# Revisão das extrações V3, V4 e V5 contra os gabaritos

Data: 20/10/2026. Execuções `loop001-v{3,4,5}-features`, RapidOCR/OpenVINO,
perfil `balanced`, características e evidências nativas retidas. HEAD
inspecionado na revisão: `54885e6`. Esta revisão lê os resultados salvos; não
executa novamente os PDFs e não altera o extrator, o avaliador ou os gabaritos.

## Conclusão

**As extrações ainda apresentam erros relevantes.** O caso prioritário é o
vazamento de dados cobertos por tarjas no V3. Há também perdas de conteúdo
nativo, alterações de dados, tabelas rasterizadas convertidas em texto sem
estrutura e falhas na ordem de leitura.

Foram comparadas todas as **410 páginas**: V3 = 144, V4 = 224, V5 = 42.
As páginas estão presentes nas saídas, inclusive as páginas em branco; presença
de página não significa presença integral de seu conteúdo.

## Escopo, fontes e evidências completas

Gabaritos usados:

- V3: `corpus/V3/Document_AI_V3.md`, acompanhado do manifesto de metadados.
- V4: `expected_markdown` do manifesto, incluindo as páginas vazias 215 e 216.
  O Markdown de referência isolado omite essas duas páginas.
- V5: `corpus/V5/Document_AI_V5_REFERENCIA.md`, acompanhado do manifesto com
  origem, geometria e ordem esperada dos blocos.

Foram usados os contratos dos próprios corpora: conteúdo visível, exclusão de
camadas ocultas e decoração, preservação de estrutura e ordem canônica.
Texto interno de evidência nativa não é tratado como conteúdo que deveria ser
publicado no Markdown.

Inventário local completo:

- [Índice das 410 comparações](../../output/loop/001/review/INDEX.md): gabarito,
  extração e diff integral de cada página.
- [Inventário JSON](../../output/loop/001/review/page_inventory.json): texto
  visível, diferenças de tokens, entidades por página, tabelas/células,
  headings, estilos, métricas salvas e hashes dos arquivos analisados.
- [Diferenças de entidades em CSV](../../output/loop/001/review/entity_differences.csv):
  todos os valores canônicos ausentes/excedentes, com multiplicidade e página.
- [Conferência do inventário](../../output/loop/001/review/review_checks.json):
  cobertura, contagens, hashes de entrada e sentinelas adversariais do V4.
- [Script da revisão](review_loop001_extractions.py): reproduz o inventário a
  partir dos artefatos existentes, sem extração nem recálculo das métricas.

A comparação automática cobre todas as páginas; a confirmação semântica abaixo
usa os trechos e contratos indicados. **Um chunk de diff não equivale a um erro
independente:** pode representar reordenação, vários erros, metadados ou uma
serialização equivalente. Os diffs preservam todas essas diferenças; não há
uma contagem artificial de “erros de OCR” por substituição de linha.

Os arquivos sob `output/` estão ignorados pelo Git e permanecem locais. Este
relatório e o script são os artefatos versionáveis da revisão.

## Critério de gravidade

| Nível | Critério |
| --- | --- |
| **P0 — crítica** | Exposição de conteúdo que deveria permanecer oculto. |
| **P1 — alta** | Perda/alteração de conteúdo, dados, estado ou associação; ordem que muda o contexto; código cuja estrutura altera o significado. |
| **P2 — média** | Estrutura semântica/Markdown incorreta, decoração indevida, links ou formatação perdidos com texto ainda recuperável. |
| **P3 — baixa** | Diferença tipográfica isolada sem alteração de conteúdo ou associação. |

A prioridade se refere ao impacto observado no corpus. Os dados são sintéticos;
as mesmas falhas em documentos reais podem ter consequências diferentes.
As categorias se sobrepõem e suas ocorrências não devem ser somadas como erros
independentes.

## Erros confirmados, em ordem de gravidade

### E01 — P0: vazamento de células cobertas por tarjas

**V3, páginas 128, 137 e 141.** As células correspondentes estão vazias no
gabarito visual. O Markdown apresenta o marcador `[DADO COBERTO]` seguido do
próprio dado subjacente:

| Página | Esperado | Extraído |
| ---: | --- | --- |
| 128 | Cliente/Conta/Valor vazios | `CLIENTE-SEGREDO-999`, `CONTA-000999`, `R$ 88.888,88` |
| 137 | Clientes vazios nas duas linhas | `HIDDEN-CLIENT-001`, `HIDDEN-CLIENT-002` |
| 141 | Código da Peça B vazio | `HIDDEN-CLIENT-001` |

O marcador de cobertura não neutraliza a exposição. A integridade de conteúdo
visível deve ser corrigida antes de qualquer decisão de qualidade baseada em
F1 global. Evidências: [128](../../output/loop/001/review/V3/p128.md),
[137](../../output/loop/001/review/V3/p137.md),
[141](../../output/loop/001/review/V3/p141.md).

### E02 — P1: corrupção de valores, separadores e sinais

- **V3 p.111:** NPS `74` e SLA `98,5%` tornam-se o token `74%'86`. Há perda
  da associação e alteração dos dados.
- **V4 p.149:** `R$ 104.220,40` vira `R$ 104.220.40`; `R$ -0,01` vira
  `Rs -0.01`; `R$ +2.170,39` vira `RS +2.170.39`. O valor não é preservado
  no formato contratado e pode ser interpretado incorretamente.
- **V4 p.139:** `+56,25%` e `+63,25%` aparecem como `-56,25%` e
  `+-63,25%`, introduzindo sinal incompatível com o esperado.
- **V4 pp.125–126:** percentuais são fragmentados, por exemplo `+14,25%`
  aparece como `+14,2 5%` na p.125.
- **V4 p.172:** `0,01%` vira `0.01%`. O avaliador pt-BR passa a reconhecer
  `1%` nessa superfície; isso não significa que a saída literal contenha `1%`.

Diferenças por página e valor estão no CSV. No V4, há **164 ocorrências
monetárias esperadas sem correspondência e 3 excedentes**, além de **52
percentuais esperados sem correspondência e 33 excedentes**. Nem toda entidade
sem correspondência é uma quantia inteiramente apagada: `R$`→`Rs`, espaços e
separadores também impedem o reconhecimento correto.

Evidências: [V3/111](../../output/loop/001/review/V3/p111.md),
[V4/149](../../output/loop/001/review/V4/p149.md),
[V4/139](../../output/loop/001/review/V4/p139.md),
[V4/125](../../output/loop/001/review/V4/p125.md),
[V4/172](../../output/loop/001/review/V4/p172.md).

### E03 — P1: identificadores, datas e destinos textuais incorretos

- **V3 pp.85–86:** confundíveis deixam de ser distinguíveis. Na p.86,
  `OOOOI` vira `O000I`, `l1I0O` vira `100` e `1 I l` vira `1 1 1`.
- **V4:** códigos de controle, caminhos e caracteres `O/0/I/l/1/rn/m`
  sofrem substituições e segmentações em páginas OCR. Por exemplo, a p.166
  apresenta `https:/example.org?a=1&b=2` no lugar de
  `https://example.org?a=1&b=2`.
- **V4:** 24 datas esperadas não são recuperadas como entidades válidas,
  distribuídas pelas pp.90, 100, 103, 108–112, 118, 123 e 140. Inclui datas
  perdidas com blocos inteiros e superfícies danificadas.
- **V5 p.3:** o CNPJ `12.345.678/0001-90` desaparece junto com seu item de
  lista. Os outros CNPJs preservados no documento não compensam essa perda.

Os números genéricos do CSV exigem contexto: incluem títulos, numeração
decorativa e pontuação de listas. Não se deve classificar todo FP numérico
como um valor financeiro inventado.

### E04 — P1: omissões extensas de conteúdo, inclusive texto nativo

- **V4 p.109, rotação física de 90°:** restam o título/controle e fragmentos
  de frases. A cobertura de tokens salva é **3,8961%**. As nove quantias,
  nove taxas e nove datas esperadas não são recuperadas como entidades.
- **V4 p.111, rotação de 180°:** cobertura de tokens de **30,3424%**.
  A p.110 também apresenta perda extensa, com cobertura de **52,3936%**.
- **V5 p.3:** somem os itens 1 e 2, suas subdivisões, protocolo, CNPJ e
  verificações jurídica/financeira; resta apenas `3. Publicar o resultado`.
  Cobertura de tokens: **35,4839%**.
- **V5 p.30:** desaparecem os itens a), b), c) e suas subdivisões; resta
  d). Cobertura de tokens: **57,7465%**.

As páginas 3 e 30 do V5 são casos de listas nativas. Portanto, esses erros não
podem ser atribuídos apenas ao reconhecimento de imagens.

Evidências: [V4/109](../../output/loop/001/review/V4/p109.md),
[V4/111](../../output/loop/001/review/V4/p111.md),
[V5/3](../../output/loop/001/review/V5/p003.md),
[V5/30](../../output/loop/001/review/V5/p030.md).

### E05 — P1: perda da estrutura de tabelas

| Corpus | Tabelas esperadas | Detectadas | Não detectadas | Células exatas / universo da métrica |
| --- | ---: | ---: | ---: | ---: |
| V3 | 77 | 44 | **33** | 896 / 1294 |
| V4 | 93 | 45 | **48** | 1111 / 2240 |
| V5 | 22 | 20 | **2** | 494 / 530 |

São **83 tabelas não detectadas**, em 80 páginas. Os textos de muitas dessas
tabelas ainda aparecem em parágrafos; isso não preserva suas relações entre
linhas, colunas e células. O denominador de células inclui células faltantes e
extras e não é somente o conjunto das tabelas detectadas.

Páginas com FN na detecção por página:

- **V3:** 47, 49, 56, 65, 66, 88–95, 99, 107, 109, 111–119, 121,
  123–126, 140 e 143.
- **V4:** 69, 70, 145–186, 189 e 190. Nas pp.145–184, todas as tabelas
  OCR esperadas deixam de ser representadas como tabelas.
- **V5:** 19 e 24: a tabela nativa permanece; a tabela rasterizada é
  serializada como texto corrido.

Exemplo: [V5/19](../../output/loop/001/review/V5/p019.md) mantém os nomes,
datas e situações da tabela OCR em um parágrafo, sem associação estrutural.

### E06 — P1: tabelas independentes perdidas ou incorporadas à tabela vizinha

- **V3 pp.56 e 66; V4 pp.69–70:** a segunda tabela perde a estrutura e
  seus cabeçalhos/valores são deslocados para blocos separados.
- **V3 p.65:** a segunda tabela `Campo | Valor` é incorporada à primeira
  como linhas com `colspan="3"`, incluindo `Valor Campo` em ordem invertida.

É uma falha de delimitação e associação, mesmo quando a maioria das palavras
e números aparece em algum lugar da página.

### E07 — P1: conteúdo OCR fora da célula à qual pertence

**V5 pp.12 e 40:** `APROVADO` e `CONFERIDO`, respectivamente, deveriam
preencher a coluna Situação do registro R-03. Aparecem antes da tabela, enquanto
a célula permanece vazia. O reconhecimento da palavra isolada está correto;
a associação com o registro está errada.

Evidências: [12](../../output/loop/001/review/V5/p012.md),
[40](../../output/loop/001/review/V5/p040.md).

### E08 — P1: ordem de leitura incorreta

- **V3 p.1:** `ordem interna dos objetos PDF.` aparece antes do início da
  nota a que pertence. Na p.31, o número `0008` fica separado da frase da
  coluna esquerda e surge depois de texto da direita; o encerramento também
  precede a nota lateral, contrariando o gabarito.
- **V3 pp.99 e 116:** tabelas rotacionadas são lidas por blocos/colunas;
  valores deixam de permanecer junto aos respectivos centros e linhas.
- **V5 pp.4 e 23:** o bloco intermediário de largura total antecede as
  análises de colunas que deveriam vir antes dele.
- **V5 pp.5 e 36:** colunas e nota lateral são intercaladas. A p.5 insere
  conteúdo da coluna C e a nota lateral no meio do parágrafo da B.
- **V5 pp.18 e 38:** a coluna direita antecede a esquerda; lista, link,
  tabela e nota deixam a ordem canônica.
- **V5 pp.20 e 32:** todos os blocos nativos aparecem primeiro, seguidos
  dos blocos OCR; o contrato exige alternância nativo → OCR → nativo.
- **V5, recorrente:** títulos principais aparecem depois do corpo ou da
  tabela, inclusive pp.1–2, 7–11, 14–16, 21–22, 25, 27, 29, 31, 34–35,
  37, 39, 41–42. Ver comparações completas para cada posição.

### E09 — P1: duplicação de conteúdo semântico e contaminação de células

- **V3 p.42:** a descrição de ML-001 começa com `ML-001 Notebook...`,
  repetindo o código já presente na coluna Código.
- **V4 pp.69–70:** `04` é repetido junto à Instalação e novamente no bloco
  de horas da segunda tabela.
- **V4 p.1:** `Fonte` é repetido no conteúdo da célula de descrição, além
  de aparecer como rótulo da linha.
- **V5 p.1:** `Fonte | Conteúdo sintético...` vira
  `Fonte | Fonte Conteúdo sintético...`.
- **V5 p.42:** `native` também é copiado para a descrição da mesma linha.
- **V5 pp.8 e 39:** a nota sobre a marca d'água aparece uma vez íntegra e
  novamente como fragmento contaminado por texto da watermark.
- **V5 pp.17 e 26:** a situação da última linha (`Pendente`/`Concluído`)
  e parte da nota são repetidas fora da tabela.

### E10 — P1: indentação de código perdida

**V3 p.17:** o `return` deixa de estar indentado dentro de `def total(...)`.
As cercas de código também desaparecem. Esse defeito altera a estrutura do
exemplo Python, mesmo que quase todas as palavras estejam preservadas.

### E11 — P1: estado de caixas de seleção perdido

**V3 p.88:** os rótulos `Aprovado` e `Pendente` permanecem, mas os símbolos
`☒` e `☐` desaparecem. Não é possível recuperar o estado apenas pelos rótulos.
No V5 p.3, os estados jurídica/financeira desaparecem com os itens omitidos
em E04. As caixas do V3 p.16 e dos formulários V5 pp.15/29 não apresentam
essa mesma perda de estado.

### E12 — P1: títulos, controles e introduções inteiras omitidos

**V5 pp.4, 5, 6, 17, 18, 20, 23, 26, 28, 32, 36 e 38:** o controle
`V5-PNNN` desaparece, junto com o título/introdução superior da página.
Na p.6, por exemplo, os blocos B01–B04 estão preservados em ordem, mas somem
o controle, o título e a explicação sobre a ordem interna.

Essa perda é diferente de apenas remover `#` de um heading: as palavras estão
ausentes da saída.

### E13 — P2: headings sem nível correto ou convertidos em parágrafos

- **V3 pp.2 e 23:** subtítulos esperados como headings são texto comum.
  No V3, headings adicionais dos títulos de página também afetam a contagem;
  isso exige a ressalva A04 abaixo.
- **V4:** headings previstos no corpo tornam-se parágrafos em grande parte
  do corpus. Exemplo: `### Cobertura` da p.3 vira `Cobertura`.
- **V5 p.2:** `# Título de nível 1`, `## Seção 2 - Análise` e
  `### Subseção 2.1 - Órgão responsável` perdem seus níveis. Na p.25,
  `#### Título de nível 4 - Verificação complementar` também vira parágrafo.

### E14 — P2: estrutura de listas, sublistas e citações perdida

- **V3 pp.15–16:** bullets/checkboxes não são serializados como listas
  canônicas, embora os rótulos e, nesses exemplos, os estados estejam visíveis.
- **V5 pp.18 e 38:** `Conferir fotos` e `Conferir nota fiscal` são promovidos
  do segundo nível ao primeiro nível da lista.
- Notas e blockquotes são convertidos em parágrafos: o parser encontra
  **15 → 0** blockquotes no V3, **9 → 0** no V4 e **11 → 0** no V5.
- Blocos de código perdem as cercas: **7 → 0** no V3 (pp.14, 17, 18, 126),
  **4 → 0** no V4 (pp.33–36), **2 → 0** no V5 (pp.16, 35).

Contagens de AST descrevem a serialização; casos em que o texto também some
estão classificados em P1.

### E15 — P2: parágrafos e quebras internas de célula fundidos

- **V3 p.12:** os dois parágrafos e a linha final independente são unidos
  ao bloco do controle sem separação semântica de parágrafos.
- **V3 p.42; V5 pp.12/40:** quebras internas de células (`<br>`) são
  substituídas por espaços; listas curtas e linhas internas deixam de ser
  distinguíveis na tabela.
- Blocos de texto OCR também são frequentemente fundidos em parágrafos únicos.

Quebrar uma linha longa para ajustar largura, isoladamente, não foi considerado
erro semântico. A falha é perder fronteiras exigidas pelo gabarito.

### E16 — P2: reconstrução incorreta de conteúdo literal

- **V3 p.69:** o conteúdo vertical deveria usar quebras `<br>`; o extrator
  publica `V\<br\>E\<br\>...`, que exibe os caracteres da tag literalmente.
- **V3 p.51; V4 pp.61–62:** os delimitadores de `code` aparecem agrupados
  antes da palavra, como `` \` \` code ``, em vez de manter sua posição/estrutura.
- Espaçamento e estrutura de texto monoespaçado deixam de ser preservados.
  E10 cobre o caso em que isso altera a estrutura do código.

Nem todo escape é defeito. Escapar `|` dentro de célula e caracteres literais
reservados pode estar correto; ver A02.

### E17 — P2: links e estilos semânticos perdidos

- **V5 pp.8 e 39:** os textos e URLs aparecem, mas os links Markdown
  `[Portal de verificação](...)`, `[Consultar autenticidade](...)` e
  `[email](mailto:...)` perdem a associação entre rótulo e destino.
- Não há tokens de link explícito nas saídas, contra quatro na referência V5.
- Negrito e itálico canônicos também desaparecem; exemplo direto na p.2 do
  V5 e nos carimbos textuais. A simples preservação das letras não preserva
  esse contrato de estilo.

### E18 — P2: cabeçalhos, rodapés e marcas d'água decorativos publicados

- O texto exato do cabeçalho decorativo permanece em **107 páginas do V3**
  e **139 do V4**. Rodapés com `Página N de 144/224` são identificáveis em
  **103 páginas do V3** e **125 do V4**. Essas contagens não incluem todas
  as versões danificadas por OCR e não contam títulos únicos de caso.
- **V3 p.130:** `RASCUNHO` é publicado, embora a marca d'água seja decorativa.
- **V4 pp.203–204:** fragmentos de `DECORATIVO-NNN` entram no corpo como
  `-203/-204`, `IVO`, `AT`, `OR`, `DEC`.
- **V5 pp.8, 17, 26 e 39:** aparecem marcas como `CÓPIA DE`, `USO INTERNO`,
  `MINUTA DE CONFERÊNCIA` e fragmentos de `VIA PARA CONFERÊNCIA`.

Essas inserções aumentam CER/WER e podem contaminar buscas e sumários.
Quando duplicam ou cortam texto semântico, há também o impacto P1 descrito em E09.

### E19 — P1/P2/P3 conforme o efeito: símbolos e tipografia

- **P1, V5 p.2:** `m²` é separado em `m` e um `2` deslocado, junto com
  `1º` separado em `1` e `o`. A unidade e a associação do expoente se perdem.
- **P1, V4 p.172:** `µg` perde o `g` e `≠ 0` perde `≠`, alterando unidade
  e relação matemática.
- **P2, V3 p.103:** `2 × 3` vira `2 x 3`, perdendo a distinção literal
  entre símbolo de multiplicação e letra.
- **P2, V5 pp.3 e 30:** o título contém `su bl istas e a l i n ha mento`.
  Há outras segmentações/substituições em páginas OCR, preservadas nos diffs.
- **P3, quando isolado:** mudança de caixa como `Páginas`→`páginas`
  (V5 p.1) ou `Duração`→`duração` (V3 p.53), sem mudança de associação.

## Limitações e distorções da avaliação que não devem virar acusações ao extrator

### A01 — P1: página em branco do V3 penalizada por metadados do gabarito

A p.138 é vazia e seu manifesto declara `expected_page_control=false` e
`blocks=0`. O gabarito contém controle e explicação textual como metadados;
o preâmbulo declara que não são conteúdo a inventar. A saída vazia está correta
nesse aspecto. A avaliação textual salva, entretanto, tem cobertura zero e
penaliza a ausência desses metadados. É necessário resolver o contrato da
referência antes de interpretar essas métricas como perda de conteúdo real.

As páginas vazias V4 215–216 também foram incluídas na revisão e não receberam
acusação de ausência de controle.

### A02 — P1: literais do gabarito nem sempre escapados como literais Markdown

No V3 p.51, o contrato diz que `<tag>` e delimitadores são visíveis, mas a
referência contém `<tag>` sem escape; uma projeção Markdown pode interpretá-lo
como HTML e suprimi-lo. O `\<tag\>` da extração preserva melhor o literal
nesse caso. Há ambiguidade semelhante em `*z*` do V4 pp.61–62 e na menção
literal a `<br>` em prosa.

Essa inconsistência precisa de tratamento explícito do gabarito/contrato.
Não se deve “corrigir” o extrator para omitir caracteres visíveis só para obter
CER menor. E16 considera a posição incorreta dos delimitadores, não pune
automaticamente todos os escapes.

### A03 — P1: avaliação global incompleta

As três avaliações globais estão `partial`, com `rank_eligible=false`, por
`evaluation_resource_limit`. CER/WER sequenciais globais e vários cálculos
de alinhamento/estrutura não foram concluídos.

No V4, o CER normalizado por página tem **150 valores numéricos**, **72 páginas
com resource_limit** e **2 N/A** por vazio. A avaliação por página é parcial em
76 páginas, pois quatro outras têm indisponibilidade em métricas diferentes.

Isso é uma limitação de cobertura do avaliador, não prova de página faltante
na extração. CER por página agregado não substitui CER sequencial global.

### A04 — P2: títulos de caso embutidos no delimitador de página

V3/V4 usam `## Página N | título` no gabarito. O protocolo de páginas consome
a linha inteira como metadado. O extrator frequentemente mantém o título
visual no corpo como `# Página N | título` ou parágrafo. Isso faz o avaliador
tratar parte do título preservado como inserção e também afeta números e
contagens de headings.

O V3 declara explicitamente que título e controle devem ser preservados uma
vez. É preciso compatibilizar esse contrato com o escopo textual da avaliação.
Esse fenômeno é diferente da decoração indevida de E18.

### A05 — P2: pareamento global de tabelas repetidas não prova deslocamento

O V3 apresenta `table_page_location_accuracy=0.9318`. A inspeção reproduz o
pareamento global abaixo, entre tabelas de conteúdo idêntico:

| Página de referência | Página da hipótese pareada |
| ---: | ---: |
| 89 | 106 |
| 90 | 108 |
| 91 | 110 |

Nas páginas 89–91 a tabela perdeu a estrutura; as tabelas nativas de controle
106/108/110 continuam nas páginas corretas. O matching global guloso usa essas
ocorrências iguais para outras referências. Logo, não há evidência de que o
extrator tenha movido essas três tabelas de página. A interpretação de
localização deve examinar o pareamento e os controles repetidos.

Da mesma forma, ordem de linhas/colunas `1.0` nas tabelas pareadas não demonstra
que tabelas ausentes tenham ordem correta.

### A06 — P2: OCR vazio em figura sem texto produz estado degradado

O V5 fica operacionalmente `partial` pelas pp.21 e 31. As evidências mostram
`no_usable_tokens` em uma região `figure`, `ocr_required=false` e ausência de
erro de tentativa. O manifesto e o arquivo de validação declaram explicitamente
que a figura não tem texto e que seu OCR esperado é vazio.

Portanto, esse alerta isolado não é um erro de reconhecimento da figura.
Essas páginas ainda têm outros desvios de ordem de leitura, como título e
legenda fora da posição canônica. O status de OCR vazio merece revisão para
não confundir ausência correta de texto com degradação real.

## Aspectos preservados

- Nenhuma página selecionada faltando e nenhuma página fora do escopo nas
  três extrações.
- Os sentinelas ocultos declarados no manifesto V4 pp.191–196 e 201–202 não
  aparecem no Markdown. Isso não elimina o vazamento confirmado no V3.
- Os blocos B01–B04 de V5 pp.6/28 permanecem em ordem; a falha ali é a
  omissão do título/controle/introdução.
- V5 preserva todas as ocorrências monetárias, percentuais e datas nas
  categorias reconhecidas pelo avaliador. Isso não valida listas, ordem ou
  associação de carimbos a células.
- Tabelas mescladas V5 pp.11/22 e tabelas multipágina pp.13/14/33/41 mantêm
  os dados tabulares; há desvios de ordem externa descritos acima.
- Ausência de texto inventado nas figuras sem texto V5 pp.21/31.

## Ordem recomendada de correção

1. **E01:** impedir publicação de conteúdo coberto em tabelas, inclusive dados
   nativos recuperados junto ao marcador de cobertura.
2. **E02–E04, E10–E12, E19/P1:** garantir conteúdo nativo íntegro, estados,
   identificadores, sinais, unidades e recuperação de páginas rotacionadas.
3. **E05–E07:** preservar estrutura de tabelas rasterizadas, separar tabelas
   independentes e associar OCR à célula correta.
4. **E08–E09:** reconstruir a ordem com colunas/faixas e alternância OCR/nativo;
   eliminar duplicações e a captura de decoração que invade conteúdo.
5. **A01–A04:** compatibilizar gabaritos e avaliação, ampliar a cobertura de
   cálculo mantendo indisponibilidade explícita, antes de usar notas como
   critério conclusivo de homologação.
6. **E13–E18, E19/P2, A05–A06:** completar Markdown semântico, links e
   formatação; melhorar interpretação dos estados e pareamentos.
7. **E19/P3:** diferenças tipográficas residuais.

Esta etapa entrega a revisão e suas evidências. Nenhum erro acima foi corrigido
no código durante esta revisão; nenhuma nova extração, commit ou push foi feito.
