# Plano de Implementação do MVP — Extrator Robusto de Texto e Estrutura de PDF

**Status:** proposta arquitetural revisada após estudo comparativo de parsers de mercado  
**Data da revisão:** 12 de setembro de 2026  
**Nome de trabalho:** `structured-pdf-text`  
**Objetivo:** construir um extrator local, auditável e comercialmente utilizável, orientado à máxima recuperação possível do texto realmente presente ou visível em documentos PDF, com tratamento explícito de múltiplas colunas, tabelas, tabelas entre páginas, camadas textuais defeituosas e documentos digitalizados.

---

## 1. Resumo executivo

A primeira versão deste plano propunha uma arquitetura essencialmente determinística:

```text
PDF
  ↓
PDFium
  ↓
caracteres + geometria + fontes
  ↓
normalização
  ↓
linhas
  ↓
palavras
  ↓
spans
  ↓
blocos
  ↓
ordem de leitura
  ↓
texto estruturado
```

Essa base continua correta e deve ser preservada. O estudo de Docling, MinerU, LiteParse, Xberg, Unstructured, PaddleOCR, MarkItDown e Marker, porém, mostra que ela é insuficiente como arquitetura completa para o objetivo agora definido: lidar de forma robusta com documentos empresariais heterogêneos, inclusive PDFs com duas colunas, tabelas ruins, tabelas divididas entre páginas, scans, textos desenhados como vetores, camadas OCR defeituosas, conteúdo parcialmente rasterizado e mistura de texto nativo com imagem.

A principal revisão é transformar o produto em um **pipeline híbrido de fusão de evidências**.

A nova arquitetura mantém o PDFium como fonte primária e exata para PDFs digitais, mas adiciona quatro conceitos que passam a ser centrais:

1. **Diagnóstico antes de escolher a estratégia.** Cada página e, quando possível, cada região recebe sinais de qualidade. O sistema não decide apenas “PDF nativo ou OCR”; ele decide onde a camada textual é confiável, incompleta, duplicada, invisível, corrompida ou ausente.
2. **Layout antes da ordem de leitura global.** Um detector de layout identifica regiões como prosa, título, tabela, figura, cabeçalho e rodapé. A ordem de leitura deixa de ser uma única heurística aplicada à página inteira.
3. **OCR como recuperação seletiva, não substituição automática.** Texto nativo confiável é preservado. OCR entra apenas em páginas ou regiões onde há evidência de perda, corrupção ou conteúdo exclusivamente visual.
4. **Tabelas como subsistema independente.** Tabelas não passam pela mesma lógica de ordenação usada para parágrafos. A detecção usa uma cascata de estratégias e a continuidade entre páginas é resolvida em uma etapa documental própria.

A arquitetura revisada pode ser resumida assim:

```text
                                  PDF
                                   │
                 ┌─────────────────┴─────────────────┐
                 │                                   │
                 ▼                                   ▼
        Evidência nativa PDFium              Renderização da página
      caracteres, objetos, paths,             baixa/alta resolução
      imagens, fontes, coordenadas                    │
                 │                                    ▼
                 │                           Análise visual de layout
                 │                           e OCR quando necessário
                 │                                    │
                 └──────────────┬─────────────────────┘
                                ▼
                     Mapa de evidências da página
                     + confiança + proveniência
                                │
                ┌───────────────┼──────────────────┐
                ▼               ▼                  ▼
          Regiões de prosa    Tabelas        Regiões problemáticas
                │               │                  │
                ▼               ▼                  ▼
        ordem de leitura   pipeline próprio   OCR seletivo
        e reconstrução     de tabela          ou página inteira
                │               │                  │
                └───────────────┼──────────────────┘
                                ▼
                         Fusão de evidências
                                │
                                ▼
                      Estrutura por página
                                │
                                ▼
                  Reconstrução entre páginas
                    tabelas, cabeçalhos, fluxo
                                │
                                ▼
          raw_text | reading_text | structured JSON | Markdown
```

### Decisão principal

**Nenhum dos projetos estudados deve ser copiado integralmente como arquitetura.** Para nosso objetivo, a melhor solução combina ideias distintas:

| Camada | Principal referência | O que aproveitar |
|---|---|---|
| Aquisição nativa | LiteParse + Docling | PDFium e separação entre parser e estrutura |
| Diagnóstico de qualidade | LiteParse + Marker | sinais determinísticos + avaliação de camada textual |
| Layout | Docling + PaddleOCR + Xberg | regiões visuais antes da reconstrução semântica |
| OCR seletivo | Marker + MinerU | reparo por região e promoção para página inteira quando necessário |
| Fusão nativo/OCR | LiteParse + MinerU | merge espacial com proveniência |
| Ordem de leitura | Marker + Xberg | sequência nativa extraída como evidência forte + XY-cut apenas em regiões adequadas |
| Tabelas | Xberg + PaddleOCR | cascata determinística e modelo estrutural como fallback |
| Tabelas entre páginas | MinerU | assinatura estrutural e continuidade documental |
| Modelo documental | Docling | estrutura intermediária rica antes da serialização |
| Orquestração de estratégias | Unstructured | estratégia automática e fallback explícito |
| Extensibilidade | MarkItDown | componentes substituíveis, sem amarrar o produto a um engine |

Se fosse necessário escolher **um único projeto como referência arquitetural mais próxima do núcleo**, seria o **LiteParse**, porque também parte de PDFium, faz detecção de complexidade, OCR seletivo, merge e reconstrução espacial. Para a arquitetura de produção, porém, o **Marker fornece o melhor padrão de decisão entre texto nativo e recuperação visual**, enquanto **Xberg fornece as melhores lições determinísticas para leitura e tabelas**, **MinerU fornece a referência mais útil para continuidade de tabelas**, e **PaddleOCR fornece o melhor conjunto de ferramentas visuais para o fallback**.

---

## 2. Objetivo do produto

O produto não deve ser apresentado apenas como “um leitor de PDF”. O objetivo é mais específico:

> **Recuperar com a maior fidelidade possível o conteúdo textual legível de um PDF e manter evidência suficiente para reconstruir sua organização, sem destruir texto correto ao tentar corrigir casos difíceis.**

O MVP será avaliado principalmente sobre documentos reais da empresa.

Não existe compromisso de ser superior ao MuPDF em qualquer PDF existente. O compromisso tecnicamente defensável é:

> **aproximar ou superar a extração do MuPDF/PyMuPDF no corpus representativo da empresa e, principalmente, recuperar classes de conteúdo que um caminho puramente baseado na camada textual perde.**

Isso inclui explicitamente:

* texto digital com acentuação em português do Brasil;
* documentos com fontes embutidas e CMaps incomuns;
* páginas em uma ou várias colunas;
* cabeçalhos, rodapés e numeração;
* tabelas com linhas explícitas;
* tabelas sem bordas;
* tabelas com células mescladas;
* tabelas desalinhadas ou mal formatadas;
* tabelas continuadas na página seguinte;
* PDFs gerados por sistemas legados;
* PDFs com camada OCR invisível;
* PDFs com camada OCR duplicada ou errada;
* páginas totalmente digitalizadas;
* páginas híbridas, com texto nativo e regiões rasterizadas;
* texto rotacionado;
* texto desenhado como vetores;
* texto em anotações ou appearance streams que não apareça no caminho textual normal;
* caracteres Unicode inválidos ou sem mapeamento confiável.

---

## 3. O que significa “melhor extração de texto”

Um erro comum em parsers é misturar três objetivos diferentes em uma única string. O nosso produto deve mantê-los separados desde o início.

### 3.1. `raw_text`

Objetivo: **máxima recuperação**.

Deve preservar o máximo de conteúdo textual detectado, inclusive elementos que depois possam ser classificados como cabeçalho, rodapé, texto marginal ou repetição.

Não deve remover silenciosamente texto apenas porque está perto da margem da página.

`raw_text` **não é a concatenação cega de todas as hipóteses**. Duplicatas óbvias podem ser resolvidas pela fusão. A diferença é que ele não aplica limpeza semântica agressiva como remover cabeçalhos, rodapés ou marginais. Todas as hipóteses rejeitadas continuam disponíveis em `structured_document`/diagnostics.

É a saída usada quando a pergunta é:

> “Quais textos conseguimos recuperar deste documento?”

### 3.2. `reading_text`

Objetivo: **leitura humana coerente**.

Pode:

* ordenar colunas;
* juntar linhas de um parágrafo;
* tratar hifenização;
* evitar duplicatas;
* representar tabelas de forma linear ou estruturada;
* sinalizar cabeçalhos e rodapés repetidos;
* opcionalmente suprimi-los.

É a saída usada quando a pergunta é:

> “Como este documento deve ser lido?”

### 3.3. `structured_document`

Objetivo: **não perder a evidência necessária para decisões futuras**.

Deve conter páginas, caracteres, tokens, linhas, regiões, tabelas, imagens relevantes, coordenadas, proveniência, confiança e relações entre elementos.

Essa representação é a fonte de verdade do produto. Texto, Markdown e JSON simplificado são renderizações dela.

### 3.4. Por que essa separação é obrigatória

Há um conflito real entre “recall máximo” e “texto limpo”. Um OCR pode recuperar um cabeçalho que um benchmark considera ruído; um filtro de cabeçalhos pode aumentar a qualidade de Markdown e ao mesmo tempo reduzir a cobertura textual. Esses objetivos não devem ser decididos de forma irreversível durante a aquisição.

A regra do produto será:

> **capturar primeiro, classificar depois, remover somente na renderização apropriada.**

---

## 4. Pesquisa arquitetural realizada

Foram analisadas as implementações e documentações atuais dos seguintes projetos, com foco em código de pipeline, roteamento de OCR, extração nativa, layout, leitura e tabelas:

| Projeto | Papel predominante | Licença do código observada | Relevância para nosso MVP |
|---|---|---|---|
| Docling | Document AI estruturado | MIT | muito alta |
| MinerU | parser híbrido com OCR/VLM | Apache 2.0 com termos adicionais | muito alta |
| LiteParse | parser PDF leve com PDFium | Apache 2.0 | muito alta |
| Xberg | parser multimodal em Rust | MIT | muito alta |
| Unstructured | orquestrador de particionamento | Apache 2.0 | média |
| PaddleOCR | OCR e Document AI visual | Apache 2.0 para o código | muito alta |
| MarkItDown | conversor leve para Markdown | MIT | baixa para o núcleo, média para extensibilidade |
| Marker | parser híbrido de alta qualidade | Apache 2.0 para o código; pesos têm termos próprios | muito alta |

**Importante:** licença do repositório não garante que todos os pesos de modelos, artefatos ou dependências tenham as mesmas condições. A seleção de modelos para produção deverá ter uma revisão de licenças separada.

---

## 5. Comparação geral das arquiteturas

### 5.1. Matriz técnica

| Projeto | Texto nativo | Layout visual | OCR | Seleção adaptativa | Merge nativo/OCR | Tabelas | Tabela entre páginas | Ordem de leitura |
|---|---|---|---|---|---|---|---|---|
| Docling | sim, backend abstrato | sim | sim | configurável | montagem por pipeline | modelo dedicado | estrutura documental permite evolução | modelo dedicado |
| MinerU | sim | sim | sim | pipeline/híbrido/VLM | híbrido por região | forte | sim, explícito | reconstrução humana/modelada |
| LiteParse | PDFium | heurística espacial | seletivo | sim, forte | sim | heurística | limitada comparada a MinerU | projeção/estrutura espacial |
| Xberg | parser nativo, opcional PDFium | opcional, ONNX | fallback | sim | Native/Mixed/OCR | cascata forte | suporte estrutural em evolução | XY-cut + sinais de estrutura |
| Unstructured | pdfminer | hi_res | OCR only/hi_res | AUTO/FAST/HI_RES/OCR_ONLY | por estratégia | hi_res | não é o foco principal | sorting/XY-cut |
| PaddleOCR | não é seu foco principal | forte | forte | pipeline configurável | predominantemente visual | muito forte | suportado | baseado em layout/modelos |
| MarkItDown | pdfminer/pdfplumber | limitado localmente | plugin/cloud | por converter | limitado | pdfplumber + heurísticas | não é foco | dependente do extrator |
| Marker | pdftext | sim | seletivo | muito forte | por página/bloco | forte + fallback visual | com LLM opcional | usa ordem nativa quando confiável |

### 5.2. Conclusão conjunta

Os projetos mais robustos convergem para algumas ideias:

* não confiar sempre no texto embutido;
* não aplicar OCR sempre;
* renderizar a página para validar ou complementar o que a camada textual afirma;
* detectar layout antes de reconstruir estruturas complexas;
* tratar tabelas como objetos estruturais, não apenas como linhas de texto;
* usar diferentes estratégias conforme a qualidade da página;
* preservar uma estrutura intermediária antes de produzir Markdown ou plain text.

A diferença entre eles está em **quanto trabalho fazem com heurística determinística** e **quanto delegam a modelos de visão**.

Nossa arquitetura deve ficar no meio desse espectro:

```text
texto digital confiável ───────────────► preservar determinismo

texto híbrido/problemático ────────────► fundir sinais

scan ou conteúdo visual ───────────────► usar percepção visual/OCR
```

---

## 6. Docling

### 6.1. Como a arquitetura funciona

Docling trata o PDF como uma entrada para um pipeline documental completo. A implementação atual separa claramente as etapas de preprocessing, layout, OCR, pós processamento de layout, estrutura de tabela, montagem da página e posterior resolução de ordem de leitura e hierarquia de títulos.

A estrutura conceitual é próxima de:

```text
PDF backend
   ↓
preprocessing
   ↓
layout
   ↓
OCR
   ↓
layout postprocessing
   ↓
table structure
   ↓
page assembly
   ↓
reading order
   ↓
heading hierarchy
   ↓
DoclingDocument
```

O projeto possui backends de PDF desacoplados do restante do pipeline. Entre eles existe suporte a pypdfium2, cujo backend expõe células de texto para as etapas superiores.

A decisão arquitetural mais importante do Docling é esta:

> **o parser de PDF não é responsável por entregar o documento final; ele entrega evidências que serão interpretadas por estágios posteriores.**

Isso coincide com a direção que devemos seguir.

### 6.2. Pontos fortes para nosso caso

**Modelo documental intermediário forte.** O resultado não nasce como uma string. Layout, tabela, imagem e texto sobrevivem tempo suficiente para serem reconciliados.

**Separação de estágios.** OCR, layout e tabela são componentes distintos. Isso facilita trocar um modelo sem reescrever o parser.

**Layout explícito.** A página é entendida visualmente antes de várias decisões estruturais.

**Reading order como etapa própria.** Isso evita misturar detecção de texto com a decisão de como o conteúdo deve ser lido.

**Produção e paralelismo.** O pipeline atual usa filas limitadas e batching por estágio, mostrando uma direção clara para escalar inferência sem transformar cada página em uma chamada monolítica.

### 6.3. Limitações para nosso objetivo

Para o nosso MVP, adotar todo o pipeline do Docling como núcleo teria custos:

* mais modelos e mais dependências desde o início;
* latência maior em PDFs digitais simples;
* risco de uma interpretação visual substituir informação nativa que já estava correta;
* maior dificuldade para entender exatamente por que determinado caractere foi escolhido.

Nosso produto prioriza recuperação textual e auditabilidade antes de enriquecimento semântico. Portanto, layout deve orientar decisões, mas não deve apagar automaticamente a camada textual correta.

### 6.4. Comparação com a arquitetura anterior

A arquitetura anterior já possuía um `RawPage` e uma sequência de detectores próprios. O Docling mostra que faltava uma camada explícita entre `RawPage` e reconstrução:

```text
ANTES
RawPage → LineDetector → WordDetector → BlockDetector

REVISADO
RawPage + PageImage
        ↓
PageEvidence
        ↓
LayoutRegions
        ↓
reconstrução específica por região
```

### 6.5. O que devemos incorporar

* backend desacoplado;
* `StructuredDocument` como modelo de domínio principal;
* estágios explícitos de layout, OCR, tabela, assembly e ordem;
* processamento em lote de modelos;
* possibilidade de trocar `LayoutEngine`, `OcrEngine` e `TableStructureEngine`.

### 6.6. O que não devemos copiar

* executar todos os modelos em toda página por padrão;
* transformar a classificação de layout em autoridade maior que o texto nativo sem um mecanismo de confiança;
* ampliar o escopo do MVP para fórmulas, imagens e semântica de títulos antes da recuperação textual estar sólida.

---

## 7. MinerU

### 7.1. Como a arquitetura funciona

MinerU oferece mais de uma rota de parsing e possui um backend híbrido particularmente relevante. A implementação atual usa pypdfium2 em partes do pipeline e combina inferência de layout, OCR, reconhecimento especializado e VLM em intensidades diferentes.

Uma representação simplificada do caminho híbrido é:

```text
PDF
 ↓
classificação / escolha de esforço
 ↓
renderização + layout
 ↓
regiões
 ├─ texto normal
 ├─ tabela
 ├─ imagem
 ├─ fórmula
 └─ outros tipos
 ↓
OCR/detecção por regiões candidatas
 ↓
reconciliação em middle representation
 ↓
tratamento documental
 ↓
merge de tabelas entre páginas
 ↓
Markdown/JSON
```

A arquitetura utiliza a região como unidade de decisão. OCR não é necessariamente uma operação cega sobre a página inteira. Há código específico para recortar regiões, mascarar fórmulas antes da detecção OCR e normalizar coordenadas para que as fontes de evidência possam ser combinadas.

### 7.2. Pontos fortes para nosso caso

**Pipeline realmente híbrido.** Texto nativo, OCR, layout e VLM não são tratados como alternativas mutuamente exclusivas.

**Região como unidade de recuperação.** Isso é exatamente o que precisamos para páginas parcialmente digitalizadas ou com tabelas rasterizadas em um PDF que também possui texto nativo.

**Representação intermediária antes da saída.** Permite correções posteriores sem reler o PDF.

**Tabelas entre páginas.** MinerU possui lógica explícita para unir tabelas continuadas. A implementação mantém estado estrutural de linhas, colunas, `colspan`, `rowspan`, cabeçalhos e ocupação que atravessa a fronteira entre páginas.

**Tratamento de continuação.** A lógica não depende apenas de “há uma tabela no fim de uma página e outra no começo da próxima”. Ela compara estrutura e cabeçalhos e trata textos de continuação e captions.

### 7.3. Limitações para nosso objetivo

MinerU é significativamente mais pesado que o MVP inicialmente proposto.

* múltiplos modelos;
* VLM em rotas de maior qualidade;
* footprint de execução maior;
* mais pontos de falha;
* mais difícil de embutir como biblioteca pequena;
* licença atual do projeto contém termos adicionais ao Apache 2.0 que precisam ser considerados se houver intenção de reutilizar código diretamente.

Além disso, nosso primeiro objetivo não é converter fórmulas ou entender imagens. Devemos absorver a arquitetura de fusão sem absorver todo o produto.

### 7.4. Comparação com a arquitetura anterior

A arquitetura anterior tratava OCR como um fallback tardio, predominantemente por página. MinerU demonstra que isso é insuficiente.

Mudança necessária:

```text
ANTES
page quality ruim → OCR da página → escolher resultado

REVISADO
layout region
   ↓
qualidade da evidência nativa naquela região
   ├─ boa → manter nativo
   ├─ ausente → OCR da região
   ├─ corrompida → OCR + comparação
   └─ maioria da página ruim → promover para OCR da página
```

### 7.5. O que devemos incorporar

* recuperação orientada a regiões;
* coordenadas normalizadas para fusão;
* uma representação intermediária rica;
* `CrossPageTableResolver` inspirado no conceito de assinatura estrutural;
* estado de `rowspan`/`colspan` durante fusão de tabelas;
* análise de cabeçalhos repetidos antes de concatenar tabelas;
* separação entre esforço normal e rota de recuperação pesada.

### 7.6. O que não devemos copiar

* dependência obrigatória de VLM para o MVP;
* pipeline de fórmulas e imagens fora do objetivo textual;
* serviços distribuídos antes de termos evidência de necessidade operacional.

---

## 8. LiteParse

### 8.1. Por que é o comparável mais direto

LiteParse é o projeto que mais se aproxima da arquitetura inicialmente proposta. Seu núcleo em Rust usa PDFium para a extração nativa, oferece OCR seletivo, merge entre OCR e texto nativo e projeção espacial para reconstruir layout.

Conceitualmente:

```text
PDF
 ↓
PDFium native extraction
 ↓
ParsedPage
 ↓
complexity detection
 ├─ simples → caminho nativo
 └─ precisa OCR → render + OCR
                     ↓
                 OCR merge
                     ↓
             grid projection
                     ↓
             texto/estrutura
```

### 8.2. O componente mais importante: complexity detection

O LiteParse implementa uma classificação explícita de motivos pelos quais a página precisa de mais que o caminho textual barato.

Entre os motivos presentes no código estão:

| Sinal | Significado para nosso produto |
|---|---|
| `Scanned` | página coberta por imagem, praticamente sem texto nativo |
| `NoText` | página sem camada textual útil |
| `SparseText` | existe texto nativo, mas sua cobertura parece insuficiente |
| `EmbeddedImages` | há regiões rasterizadas relevantes além do texto |
| `Garbled` | camada textual tem indícios de CMap/Unicode quebrado |
| `VectorText` | conteúdo visível pode estar desenhado como paths, não como texto |
| `AnnotationText` | texto aparece visualmente em appearance stream de anotação, fora da superfície textual normal |

Esse modelo é extremamente relevante. Ele mostra que “tem texto ou não tem texto?” é uma pergunta fraca. Nosso `QualityAnalyzer` precisa responder **por que** uma página ou região não é confiável.

### 8.3. Pontos fortes

**PDFium como base.** Valida nossa escolha inicial.

**Caminho barato para documentos simples.** Não força modelos em toda página.

**OCR seletivo.** OCR é enriquecimento e recuperação.

**Merge explícito.** Reconhece que nativo e OCR podem coexistir.

**Detecção de casos invisíveis ao text API.** Vetores e anotações são especialmente relevantes para documentos empresariais estranhos.

**Implementação nativa.** Rust evita parte do overhead de várias chamadas FFI por caractere.

### 8.4. Limitações

Os próprios resultados e documentação do LiteParse mostram que layout muito complexo, tabelas densas, scans antigos e certos casos de múltiplas colunas ainda se beneficiam de engines mais pesados.

A projeção em grid é útil, mas não deve ser nossa única solução de leitura. Ela é principalmente uma representação espacial e pode perder semântica de regiões.

### 8.5. Comparação com a arquitetura anterior

A arquitetura anterior estava correta no eixo PDFium → geometria → reconstrução. O que faltava era o roteador explícito.

A alteração é direta:

```text
ANTES
PDFium → reconstrução → diagnóstico → talvez OCR

MELHOR
PDFium → diagnóstico barato → estratégia por página/região
                             ├─ reconstrução nativa
                             ├─ OCR seletivo
                             └─ rota visual pesada
```

### 8.6. O que devemos incorporar

* taxonomia explícita de complexidade;
* detecção de imagem de página inteira;
* cobertura textual;
* cobertura de imagens;
* detecção de camada corrompida;
* detecção de vetores não cobertos por texto;
* inspeção de anotações quando a página parece vazia;
* `ExtractionDecision` com razões auditáveis;
* merge nativo/OCR por geometria.

### 8.7. O que devemos melhorar em relação ao LiteParse

* não depender apenas de grid projection para layout complexo;
* adicionar layout visual em páginas/regiões complexas;
* criar pipeline específico de tabelas;
* adicionar continuidade de tabelas entre páginas;
* preservar simultaneamente `raw_text` e `reading_text`.

---

## 9. Xberg

### 9.1. Como a arquitetura funciona

Xberg segue uma filosofia nativa e modular. Para PDF, possui um parser em Rust, fallback OCR, opção de layout por modelos ONNX, estrutura de tabelas e estratégias de ordem de leitura.

Um aspecto particularmente interessante é que ele modela o método de extração como estado:

```text
Native
Mixed
Ocr
```

Isso é superior a um simples booleano `used_ocr`, porque páginas e documentos podem realmente ser mistos.

### 9.2. A principal lição: prosa e tabela não podem compartilhar cegamente a mesma ordem

O código de XY-cut do Xberg documenta uma falha arquitetural real: tentativas de tornar o detector de colunas mais sensível para resolver prosa em duas colunas acabaram interpretando intervalos de células de tabela como divisões de coluna e corrompendo a sequência de números.

A lição é decisiva para nosso produto:

> **Antes de aplicar XY-cut ou outra heurística global de leitura, devemos saber se a região representa prosa, tabela ou outro tipo estrutural.**

Portanto:

```text
ERRADO
página inteira → XY-cut → texto/tabela

CORRETO
página → layout regions
          ├─ prose → reading order resolver
          ├─ table → table pipeline
          ├─ figure → visual/OCR policy
          └─ marginalia → policy própria
```

### 9.3. Tabelas em cascata

O subsistema nativo de tabelas do Xberg utiliza múltiplas estratégias em prioridade:

1. detector estrito de grid;
2. detector relaxado para tabelas com linhas e duas ou mais colunas;
3. reconstrução heurística pela camada textual para casos sem bordas ou grids explícitos.

Uma estratégia mais fraca só precisa rodar quando a anterior não encontrou resultado suficiente.

Essa é uma arquitetura melhor que escolher um único detector universal.

### 9.4. Layout opcional e seletivo

O projeto também permite combinar layout visual com semântica do PDF. A documentação atual enfatiza que o layout pode informar região, ordem e tabela, enquanto sinais nativos continuam relevantes.

Essa postura é alinhada ao nosso objetivo: usar visão para complementar, não substituir automaticamente.

### 9.5. Comparação com a arquitetura anterior

A arquitetura antiga tinha XY-cut como estratégia inicial de leitura e deixava tabelas como preocupação posterior. Isso precisa mudar.

A nova sequência será:

```text
LayoutRegionDetector
       ↓
RegionClassifier
       ↓
 ┌─────┼─────────────┐
 ▼     ▼             ▼
prose table          other
 │     │
 │     └─ TablePipeline
 └─────── ReadingOrderResolver
```

### 9.6. O que devemos incorporar

* `Native/Mixed/Ocr` como proveniência explícita;
* XY-cut apenas em regiões candidatas a prosa;
* detecção de heading/linhas largas antes de cortes de coluna;
* cascata de detectores de tabela;
* fallback de parser e warnings acionáveis;
* separar `layout signal` de `content source`.

### 9.7. O que não devemos copiar

Não há necessidade de substituir PDFium por um novo parser Rust no MVP. Isso aumentaria muito o escopo. O conhecimento do Xberg será usado principalmente na reconstrução e nas políticas de fallback.

---

## 10. Unstructured

### 10.1. Como a arquitetura funciona

Unstructured é mais útil como referência de **roteamento de estratégias** do que como referência de parser de baixo nível.

O `partition_pdf` atual expõe estratégias explícitas:

```text
AUTO
FAST
HI_RES
OCR_ONLY
```

O caminho `fast` extrai texto diretamente do PDF, hoje apoiado em pdfminer. O caminho `hi_res` utiliza um detector de layout. `ocr_only` força OCR. O modo `auto` observa se o texto do PDF é extraível e escolhe entre o caminho barato e o caminho de maior resolução.

A implementação também possui parâmetros específicos do pdfminer para margem de linha, caractere, overlap e palavra, mostrando que o projeto aceita que a reconstrução textual precisa ser calibrada.

### 10.2. Pontos fortes

**Roteamento compreensível.** O usuário e o sistema sabem qual estratégia foi usada.

**Fallback quando a extração falha.** Uma exceção no parser de texto não precisa abortar todo o documento.

**Separação de custo.** PDF simples não precisa pagar por layout e OCR.

**Integração com estrutura de tabela no modo de alta resolução.** Mostra que estrutura e extração textual são preocupações distintas.

### 10.3. Limitações para nosso caso

O caminho nativo baseado em pdfminer não é a escolha que eu faria para o nosso núcleo, porque já escolhemos PDFium para obter geometria e Unicode de forma próxima de engines de renderização modernos.

Além disso, a estratégia no Unstructured é predominantemente em nível de página/documento. Nosso objetivo exige uma granularidade adicional: regiões boas e ruins podem coexistir na mesma página.

### 10.4. Comparação com a arquitetura anterior

Nossa arquitetura tinha uma única sequência principal e um fallback OCR tardio. Unstructured reforça que precisamos formalizar estratégia e fallback como parte do domínio.

### 10.5. O que devemos incorporar

* `AUTO`, `NATIVE`, `HYBRID`, `OCR` como modos explícitos;
* fallback sem abortar o documento inteiro;
* telemetria da estratégia escolhida;
* configuração separada de qualidade versus velocidade;
* possibilidade futura de `fast` e `balanced` sem alterar a API.

### 10.6. O que não devemos incorporar

* pdfminer como fonte nativa primária;
* decisão somente em nível de documento;
* acoplar estrutura de tabela apenas ao modo de alta resolução.

---

## 11. PaddleOCR

### 11.1. O papel correto do PaddleOCR em nossa arquitetura

PaddleOCR é diferente dos parsers anteriores. Ele é principalmente uma plataforma de OCR e Document AI visual. Isso o torna excelente justamente para os casos em que o PDF não nos oferece texto nativo confiável.

A família atual inclui recursos como:

* orientação do documento;
* correção de deformação;
* orientação de linha;
* detecção e reconhecimento de texto;
* reconhecimento de tabela;
* reconhecimento de fórmulas;
* layout documental;
* pipelines estruturados como PP-StructureV3;
* modelos visuais mais completos na família PaddleOCR-VL.

Há suporte explícito a português em modelos multilíngues, o que é essencial para nosso corpus em pt-BR.

### 11.2. Pontos fortes

**Recupera conteúdo que não existe na camada textual.** Scans, imagens, vetores rasterizados e textos incorporados em figuras podem ser lidos.

**Português.** Modelos multilíngues incluem português e alfabeto latino com diacríticos.

**Orientação e unwarping.** Muito relevante para documentos digitalizados, fotos ou páginas mal alinhadas.

**Layout e tabela.** Não precisamos usar OCR apenas como “imagem para string”; podemos recuperar regiões e estruturas.

**Execução local.** Mantém possibilidade de processamento on premises.

### 11.3. Por que ele não deve ser o caminho primário

Fazer OCR em um PDF digital bom é, em geral, uma regressão:

* transforma texto exato em predição;
* pode perder acentos, pontuação e caracteres pequenos;
* pode confundir números semelhantes;
* perde parte da informação de fonte e ordem do content stream;
* custa mais CPU/GPU;
* pode gerar uma estrutura visual plausível, mas diferente do texto real codificado.

Portanto:

> **PaddleOCR deve ser nosso sensor visual de recuperação, não nossa fonte textual padrão.**

### 11.4. Comparação com a arquitetura anterior

O plano anterior tratava OCR como etapa futura e genérica. A revisão torna o OCR um componente de primeira classe, porém seletivo.

Novo contrato sugerido:

```python
class OcrEngine(Protocol):
    def recognize_page(...): ...
    def recognize_region(...): ...
```

E, separadamente:

```python
class LayoutEngine(Protocol):
    def detect_regions(...): ...
```

Mesmo que ambos sejam implementados inicialmente com componentes PaddleOCR, eles devem permanecer interfaces distintas.

### 11.5. Estratégia recomendada para português

No MVP:

* preservar Unicode nativo sempre que confiável;
* configurar OCR para modelo latino/português adequado;
* normalizar a saída final em Unicode NFC somente na visão normalizada;
* nunca converter acentos manualmente por regras frágeis;
* manter texto original de OCR e texto normalizado separadamente;
* registrar confiança por token/linha quando a engine fornecer.

### 11.6. O que devemos incorporar

* OCR local multilíngue;
* detecção de orientação;
* unwarping opcional para scans;
* layout visual pluggable;
* table structure model como fallback da cascata determinística;
* OCR por região.

### 11.7. O que não devemos incorporar

* OCR de toda página digital por padrão;
* dependência da representação Markdown gerada pelo pipeline visual;
* uso de um VLM grande como requisito do MVP.

---

## 12. MarkItDown

### 12.1. Como a arquitetura funciona para PDF

MarkItDown é intencionalmente leve e orientado a conversão para Markdown. No caminho local de PDF, a implementação atual importa pdfminer e pdfplumber. Há lógica adicional para tabelas/formulários baseada em posições de palavras e heurísticas de alinhamento de colunas.

O projeto também permite plugins e integração com serviços externos de maior capacidade, como OCR por visão ou serviços da Azure.

### 12.2. Pontos fortes

**API simples.** O produto não expõe a complexidade do backend ao consumidor.

**Plugins.** Funcionalidades pesadas podem ser opt in.

**Fallback por serviço externo.** Mostra uma forma limpa de manter o núcleo leve.

**Heurísticas específicas para documentos empresariais.** O conversor atual possui regras para formulários e numeração parcial, mostrando a utilidade de correções orientadas a padrões reais de documentos.

### 12.3. Limitações para nosso objetivo

MarkItDown otimiza para Markdown consumível por LLM, não para máxima recuperação textual auditável.

O uso de pdfminer/pdfplumber e heurísticas de formulário não oferece uma base tão rica quanto PDFium + geometria por caractere + objetos da página.

Também não há no núcleo local uma estratégia comparável à fusão detalhada de evidências que queremos.

### 12.4. Comparação com a arquitetura anterior

Nossa arquitetura anterior já era tecnicamente mais profunda no eixo PDF. Portanto não devemos alterar o core por causa do MarkItDown.

### 12.5. O que devemos incorporar

* API simples por cima de uma implementação complexa;
* plugins/engines substituíveis;
* renderer Markdown separado do core;
* possibilidade futura de um `RecoveryProvider` externo sem contaminar o pipeline local.

### 12.6. O que não devemos incorporar

* Markdown como modelo interno;
* pdfplumber como único mecanismo de tabela;
* heurísticas específicas de layout aplicadas antes de termos uma estrutura de evidência robusta.

---

## 13. Marker

### 13.1. A arquitetura mais relevante para decisão de qualidade

Marker tem uma das arquiteturas mais interessantes para nosso problema porque não considera “o PDF tem texto” suficiente para confiar nesse texto.

O pipeline atual constrói documento, layout, linhas, OCR e estrutura em componentes separados. O `LineBuilder` decide por página se a camada embutida está boa ou se a página precisa de OCR.

A decisão usa sinais como:

* classificador de erro OCR/texto;
* cobertura das linhas nativas em relação aos blocos de layout;
* overlaps anormais entre linhas, úteis para detectar camadas OCR duplicadas ou ruins;
* bboxes fora da página;
* verificação visual para descartar linhas cuja região renderizada está em branco;
* análise por bloco em páginas predominantemente boas.

O modo `fast` ainda implementa uma ideia particularmente boa:

> **uma página boa pode conter apenas alguns blocos ruins; esses blocos são reparados individualmente. Se muitos blocos estiverem ruins, a página é promovida para OCR completo.**

### 13.2. Ordem nativa como sinal forte

Marker também traz uma evidência importante contra uma suposição comum: um modelo aprendido de ordem de leitura não deve substituir automaticamente a sequência nativa.

No código atual, páginas com texto confiável podem usar a posição dos caracteres no fluxo extraído como sinal principal de ordem. O comentário de implementação registra que, no benchmark do projeto, essa ordem venceu a cabeça aprendida em múltiplas colunas.

Para nosso produto a conclusão não é “sequência nativa extraída sempre ganha”. A conclusão correta é:

> **sequência nativa extraída é uma evidência de primeira classe e deve competir com geometria e layout, não ser descartada.**

### 13.3. Pontos fortes

**Qualidade como decisão explícita.** Muito melhor que simplesmente testar string vazia.

**Validação contra a imagem.** A página renderizada funciona como evidência de que uma bbox realmente contém tinta visível.

**OCR por bloco.** Essencial para híbridos.

**Promoção adaptativa.** Evita centenas de recortes quando a página inteira está ruim.

**Layout e texto nativo cooperam.** O layout valida cobertura sem precisar substituir os caracteres.

### 13.4. Limitações para nosso caso

Marker depende de modelos próprios do ecossistema Datalab/Surya para grande parte da qualidade avançada. Os pesos possuem termos de uso que precisam ser analisados separadamente para um produto empresarial.

Além disso, recursos como merge sofisticado de tabelas entre páginas podem depender de LLM no modo híbrido. Nós queremos uma primeira implementação determinística para esse caso.

### 13.5. Comparação com a arquitetura anterior

Essa é a maior mudança de design provocada pelo estudo.

Antes:

```text
QualityAnalyzer por página
  ↓
OCR fallback
```

Depois:

```text
PageQualityAnalyzer
  +
LayoutCoverageAnalyzer
  +
NativeTextVisualVerifier
  ↓
RegionQualityMap
  ↓
para cada região:
    KEEP_NATIVE
    MERGE_OCR
    REPLACE_WITH_OCR
    ESCALATE_PAGE_OCR
```

### 13.6. O que devemos incorporar

* validação da camada textual contra layout;
* detecção de overlaps anormais;
* verificação de tinta na imagem para texto invisível;
* reparo por bloco/região;
* promoção para OCR de página quando a fração de regiões ruins ultrapassar um limite;
* sequência nativa extraída como sinal explícito;
* modo `fast` e modo `balanced` no futuro.

### 13.7. O que não devemos copiar

* dependência obrigatória dos modelos Surya;
* LLM obrigatório para fusão de tabelas;
* regras de benchmark específicas do Marker como se fossem universais.

---

## 14. Comparação da arquitetura anterior com todas as abordagens

A tabela abaixo resume o que muda no plano original depois desta revisão.

| Aspecto | Arquitetura anterior | Evidência dos projetos | Decisão revisada |
|---|---|---|---|
| Backend PDF | PDFium | LiteParse e Docling validam a escolha | manter PDFium |
| Unidade básica | caractere | correto, mas insuficiente sozinho | manter caracteres + objetos + raster |
| Diagnóstico | `QualityAnalyzer` posterior | LiteParse e Marker fazem gate cedo | mover diagnóstico para o início |
| OCR | fallback predominantemente por página | Marker/MinerU recuperam regiões | OCR por região + promoção de página |
| Layout | derivado principalmente por geometria | Docling/Marker/Paddle/Xberg usam detector dedicado | adicionar `LayoutEngine` |
| Ordem | XY-cut como estratégia inicial | Xberg mostra conflito com tabelas; Marker valoriza sequência nativa extraída | usar grafo + sinais múltiplos, somente em prosa |
| Tabela | preocupação posterior | Xberg/Paddle/MinerU tratam como subsistema | criar `TablePipeline` próprio |
| Tabela entre páginas | fora do núcleo | MinerU possui merge estrutural | incluir no MVP |
| Texto invisível | regra local | Marker valida bbox contra imagem | adicionar `VisualInkVerifier` |
| Vetor que simula texto | pouco explícito | LiteParse detecta área vetorial não coberta | adicionar sinal de `vector_text` |
| Anotações | secundário | LiteParse detecta appearance text | inspecionar quando página parece vazia |
| Proveniência | prevista parcialmente | Xberg Native/Mixed/OCR reforça valor | proveniência por token/região |
| Documento | páginas independentes | Docling/MinerU têm etapa documental | adicionar `DocumentAssembler` |
| Saída | text/raw/JSON | mercado tende a Markdown | manter estrutura como fonte de verdade; Markdown é renderer |

---

## 15. Qual abordagem é a melhor?

### 15.1. Se tivéssemos de escolher um único projeto

Para **núcleo técnico semelhante ao que queremos construir**, o melhor comparável é o **LiteParse**.

Motivos:

* PDFium;
* extração local;
* detecção de complexidade;
* OCR seletivo;
* merge nativo/OCR;
* geometria preservada;
* arquitetura relativamente pequena.

Porém, escolher somente LiteParse como inspiração ainda deixaria lacunas justamente nas classes mais importantes para a empresa: tabelas complexas, continuidade entre páginas e layout muito irregular.

### 15.2. Se escolhermos a melhor abordagem por camada

A melhor solução passa a ser:

```text
                 PDFium / abordagem LiteParse
                           │
                           ▼
             Quality Gate LiteParse + Marker
                           │
                           ▼
        Layout Docling / PaddleOCR / Xberg
                           │
              ┌────────────┴────────────┐
              ▼                         ▼
       Texto/prosa                  Tabela
   Marker + Xberg             Xberg + PaddleOCR
              │                         │
              │                         ▼
              │                 estrutura de células
              │                         │
              └────────────┬────────────┘
                           ▼
                   OCR seletivo
                   Marker + MinerU
                           │
                           ▼
                    Evidence Fusion
                           │
                           ▼
              CrossPageTableResolver
                        MinerU
                           │
                           ▼
                  StructuredDocument
```

### 15.3. Comparação por famílias arquiteturais

Os oito projetos podem ser agrupados em quatro famílias, o que ajuda a entender por que nenhuma isoladamente atende nosso objetivo.

| Família | Projetos | Vantagem | Fraqueza para nosso caso |
|---|---|---|---|
| native first adaptativo | LiteParse, Xberg | velocidade, auditabilidade, preserva texto digital | precisa de reforço visual em layouts/tabelas extremos |
| híbrido document AI | Docling, MinerU, Marker | layout, OCR e estrutura integrados | custo e complexidade maiores; risco de usar modelo onde texto exato bastava |
| visual first | PaddleOCR | excelente em scans e conteúdo sem camada textual | transforma texto digital exato em reconhecimento probabilístico se usado sempre |
| orquestrador/conversor | Unstructured, MarkItDown | API, routing, extensibilidade | não oferece o melhor core de PDF para nossa meta de fidelidade |

Nossa arquitetura fica deliberadamente entre as duas primeiras famílias: **native first na aquisição, híbrida na recuperação**. O PaddleOCR entra como sensor visual, e as ideias de orquestração de Unstructured/MarkItDown aparecem nas interfaces e modos, não no core textual.

### 15.4. Recomendação

**A nova arquitetura deve ser própria e baseada em fusão de evidências.** Não devemos construir “um clone do PyMuPDF”, “um LiteParse em Python” ou “um MinerU menor”.

Devemos construir um engine que tenha uma propriedade que nem todos esses projetos tornam central:

> **cada trecho de texto sabe de onde veio, por que foi aceito e quais evidências concorrentes existiam.**

Essa propriedade será nossa principal ferramenta para aumentar robustez com documentos reais sem acumular correções mágicas.

---

## 16. Arquitetura revisada: Hybrid Evidence Fusion Pipeline

Chamaremos a arquitetura do MVP de **Hybrid Evidence Fusion Pipeline**.

A palavra *hybrid* não significa que todo documento será processado por OCR ou modelo visual. Significa que o engine consegue combinar mais de uma fonte quando necessário.

A palavra *evidence* é ainda mais importante: nenhum resultado de OCR, nenhuma heurística de espaço e nenhuma decisão de layout deve virar texto final sem que o sistema consiga registrar de onde veio.

### 16.1. Visão completa

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│ 0. Document Intake                                                         │
│ bytes, senha, limites, metadata, páginas                                   │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 1. Native Evidence Collector — PDFium                                      │
│ chars | text ranges | sequência nativa extraída | fonts | bboxes | paths | images       │
│ annotations | page geometry | rotations                                    │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              │
                    render low resolution
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 2. Page Evidence & Complexity Analyzer                                     │
│ text coverage | image coverage | garbling | duplicates | blank glyphs      │
│ vector text | annotation text | scan likelihood | rotation                 │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 3. Layout Region Detector                                                  │
│ prose | title | list | table | figure | header | footer | marginalia       │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 4. Native Reconstruction & Region Assignment                               │
│ character normalization → line candidates → native tokens → region mapping │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 5. Region Quality Gate                                                     │
│ KEEP_NATIVE | MERGE_OCR | OCR_REGION | OCR_PAGE                            │
└──────────────┬───────────────────────────┬──────────────────────────────────┘
               │                           │
               ▼                           ▼
┌────────────────────────────┐  ┌────────────────────────────────────────────┐
│ 6A. Prose Reading Order    │  │ 6B. Table Pipeline                         │
│ stream + geometry + layout │  │ ruled → relaxed → text → model fallback   │
│ region graph + XY-cut      │  │ cell graph + confidence                    │
└──────────────┬─────────────┘  └────────────────┬───────────────────────────┘
               │                                 │
               └──────────────┬──────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 7. OCR Recovery                                                            │
│ region OCR or whole page OCR, orientation/unwarping when required          │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 8. Evidence Fusion                                                         │
│ native + OCR + layout + table evidence, dedup, conflict resolution         │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 9. Page Assembler                                                          │
│ regions, lines, paragraphs, tables, raw/reading views                      │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 10. Document Assembler                                                     │
│ repeated header/footer, cross-page tables, page continuity, provenance     │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              ▼
                raw_text | reading_text | JSON | Markdown
```

### 16.2. Regra de ouro

O pipeline deve tentar resolver cada problema no nível mais barato e determinístico possível.

```text
1. PDF contém texto correto?                 → use esse texto
2. Geometria resolve a estrutura?            → use geometria
3. Layout visual ajuda a separar regiões?    → use layout
4. Só uma região está ruim?                  → OCR da região
5. Página inteira está ruim?                 → OCR da página
6. Tabela determinística funciona?           → use detector determinístico
7. Tabela continua ambígua?                  → use modelo de estrutura
8. Ainda há conflito?                        → preserve alternativas e confiança
```

A ordem é importante. Quanto mais tarde entrarmos em inferência probabilística, menor a chance de trocar texto exato por texto “plausível”.

---

## 17. Princípios arquiteturais obrigatórios

### 17.1. Evidência bruta é imutável

`NativeEvidence` nunca deve ser alterado por normalização, deduplicação ou OCR.

Se um caractere foi extraído como `ã`, ele continua disponível mesmo que uma etapa posterior normalize outro detalhe.

Se duas cópias sobrepostas de uma palavra forem detectadas, ambas permanecem na evidência bruta, enquanto a visão de leitura escolhe apenas uma.

### 17.2. Proveniência em todos os elementos derivados

Todo token relevante deve poder indicar sua origem.

```python
class SourceKind(Enum):
    NATIVE_PDF = "native_pdf"
    NATIVE_GENERATED = "native_generated"
    OCR_REGION = "ocr_region"
    OCR_PAGE = "ocr_page"
    TABLE_NATIVE = "table_native"
    TABLE_MODEL = "table_model"
    RECOVERED_UNICODE = "recovered_unicode"
```

Elementos fundidos podem carregar mais de uma fonte.

### 17.3. OCR nunca destrói texto confiável silenciosamente

Se OCR e camada nativa discordarem, o sistema precisa saber que houve conflito.

No MVP a regra conservadora será:

```text
native confiável + OCR divergente   → manter native, registrar OCR
native corrompido + OCR confiável   → escolher OCR
native ausente + OCR                → escolher OCR
native parcial + OCR complementar   → merge geométrico
```

### 17.4. Tabela não é prosa em colunas

Esse princípio passa a ser arquitetural, não apenas uma heurística.

Nenhum algoritmo de leitura de colunas deve reordenar internamente uma região já classificada como tabela.

### 17.5. Página não é a única unidade de decisão

Precisamos dos níveis:

```text
document
  page
    region
      line/token/char
```

Uma página pode ser 80% texto nativo perfeito e 20% scan. Forçar um único método para a página inteira perde informação.

### 17.6. Estrutura precede serialização

Não construir Markdown durante detecção.

Fluxo correto:

```text
evidência → estrutura → renderer
```

Não:

```text
evidência → Markdown → tentar reconstruir estrutura
```

---

## 18. Modelo de dados revisado

### 18.1. `BBox`

```python
@dataclass(frozen=True)
class BBox:
    x0: float
    y0: float
    x1: float
    y1: float
```

Todas as geometrias internas devem usar um sistema canônico único.

Sugestão:

* origem no canto superior esquerdo;
* `x` cresce para direita;
* `y` cresce para baixo;
* unidade interna em pontos PDF quando a evidência vier do PDF;
* pixels de OCR sempre convertidos para esse espaço antes do merge.

O objeto original deve manter metadados para conversão reversível.

### 18.2. `NativeCharacter`

```python
@dataclass(frozen=True)
class NativeCharacter:
    page_index: int
    char_index: int
    text: str
    unicode_codepoint: int | None
    bbox: BBox
    origin: Point
    angle: float
    font_name: str | None
    font_size: float | None
    font_weight: int | None
    generated: bool
    hyphen: bool
    unicode_mapping_failed: bool
    visible_candidate: bool
```

`char_index` é importante porque mantém a ordem exposta pelo PDFium. Essa ordem **não deve ser confundida com a ordem bruta dos operadores `Tj/TJ` no content stream**. O `FPDFText_LoadPage` já construiu uma text page, pode inserir caracteres gerados e aplica parte de sua própria interpretação. Portanto, chamaremos esse sinal de **sequência nativa extraída**. Ele é valioso, mas é apenas uma evidência entre outras.

### 18.3. `NativeObjectEvidence`

```python
@dataclass(frozen=True)
class NativeObjectEvidence:
    images: tuple[ImageEvidence, ...]
    paths: tuple[PathEvidence, ...]
    annotations: tuple[AnnotationEvidence, ...]
    structure_tree: StructureTreeEvidence | None
    page_bbox: BBox
    crop_bbox: BBox
    rotation: int
```

Essa estrutura é necessária para detectar scans, grids de tabela, vector text e appearance text.

### 18.4. `OcrToken`

```python
@dataclass(frozen=True)
class OcrToken:
    text: str
    bbox: BBox
    confidence: float | None
    language: str | None
    source: SourceKind
```

### 18.5. `TextToken`

É o primeiro objeto derivado capaz de representar fusão.

```python
@dataclass
class TextToken:
    text: str
    bbox: BBox
    sources: list[EvidenceRef]
    confidence: float
    normalized_text: str | None
    flags: set[TokenFlag]
```

### 18.6. `TextLine`

```python
@dataclass
class TextLine:
    tokens: list[TextToken]
    bbox: BBox
    baseline: Baseline | None
    direction: WritingDirection
    native_order_min: int | None
    native_order_max: int | None
```

### 18.7. `LayoutRegion`

```python
class RegionKind(Enum):
    TEXT = "text"
    TITLE = "title"
    LIST = "list"
    TABLE = "table"
    FIGURE = "figure"
    CAPTION = "caption"
    HEADER = "header"
    FOOTER = "footer"
    FOOTNOTE = "footnote"
    MARGINALIA = "marginalia"
    UNKNOWN = "unknown"
```

```python
@dataclass
class LayoutRegion:
    region_id: str
    kind: RegionKind
    bbox: BBox
    layout_confidence: float | None
    native_lines: list[TextLine]
    ocr_tokens: list[OcrToken]
    quality: RegionQuality
```

### 18.8. `TableCell`

```python
@dataclass
class TableCell:
    row: int
    col: int
    rowspan: int
    colspan: int
    bbox: BBox | None
    text: str
    tokens: list[TextToken]
    confidence: float
```

### 18.9. `StructuredTable`

```python
@dataclass
class StructuredTable:
    table_id: str
    page_fragments: list[TableFragment]
    cells: list[TableCell]
    column_count: int
    row_count: int
    confidence: float
    method: TableMethod
    continued_from_previous_page: bool
    continues_to_next_page: bool
```

### 18.10. `StructuredPage`

```python
@dataclass
class StructuredPage:
    page_index: int
    bbox: BBox
    regions: list[LayoutRegion]
    tables: list[StructuredTable]
    raw_text: str
    reading_text: str
    diagnostics: PageDiagnostics
```

### 18.11. `StructuredDocument`

```python
@dataclass
class StructuredDocument:
    pages: list[StructuredPage]
    tables: list[StructuredTable]
    raw_text: str
    reading_text: str
    metadata: DocumentMetadata
    diagnostics: DocumentDiagnostics
```

---

## 19. Estágio 0 — Document Intake

### 19.1. Responsabilidades

* validar o header PDF;
* abrir o documento;
* lidar com senha quando configurada;
* obter contagem de páginas;
* aplicar limites de segurança;
* registrar versão do PDFium e engines configuradas;
* criar `DocumentContext`.

### 19.2. Limites de segurança do MVP

Devem ser configuráveis:

```python
@dataclass
class SecurityLimits:
    max_pages: int = 5000
    max_file_size_bytes: int = 1_000_000_000
    max_render_pixels: int = 100_000_000
    document_timeout_seconds: float | None = None
```

Os valores finais devem ser definidos pelo ambiente da empresa.

### 19.3. Falha parcial

Um erro em uma página não deve necessariamente invalidar as demais.

Resultado possível:

```text
SUCCESS
PARTIAL_SUCCESS
FAILURE
```

---

## 20. Estágio 1 — Native Evidence Collector

Essa é a fundação de alta fidelidade.

### 20.1. O que coletar do PDFium

Por página:

* quantidade de caracteres;
* Unicode de cada caractere;
* bbox;
* origem;
* ângulo;
* tamanho de fonte;
* informação de fonte disponível;
* índice da sequência textual do PDFium;
* indicador de caractere gerado;
* indicador de hífen;
* erro de mapeamento Unicode quando exposto;
* texto por range;
* imagens colocadas na página;
* paths relevantes;
* anotações;
* structure tree de PDFs tagged, quando presente;
* MCIDs/atributos estruturais que a API permitir recuperar;
* rotação da página;
* MediaBox/CropBox;
* matriz necessária para normalizar coordenadas.

### 20.2. Precisão sobre a sequência nativa

O índice de caractere exposto por PDFium representa a sequência da `FPDF_TEXTPAGE`, não uma gravação literal da ordem de operadores do content stream. O PDFium pode produzir caracteres gerados, inclusive quebras, e expõe APIs experimentais como `FPDFText_IsGenerated`, `FPDFText_IsHyphen` e `FPDFText_HasUnicodeMapError`.

Por isso, o engine deve registrar separadamente:

```text
pdfium_char_index
is_generated
is_hyphen
has_unicode_map_error
```

Quando conseguirmos recuperar a ordem de objetos da página com segurança, ela pode entrar como evidência adicional, mas não devemos chamar `char_index` de “ordem original do PDF”.

Essas APIs incluem pontos experimentais do PDFium. A implementação deve fazer **feature detection na versão empacotada pelo pypdfium2** e usar bindings de baixo nível somente quando disponíveis. A ausência de `IsGenerated`, `IsHyphen` ou `HasUnicodeMapError` em uma versão não pode impedir toda a extração; os campos correspondentes devem aceitar `None`/`unknown` e os diagnósticos devem registrar a capacidade efetivamente disponível.

### 20.3. Structure tree como evidência opcional

PDFs tagged podem trazer uma árvore lógica com elementos como parágrafo, heading, lista, tabela e células. PDFium possui API pública de structure tree (`FPDF_StructTree_GetForPage` e família `FPDF_StructElement_*`).

Quando presente, essa informação deve ser coletada como **evidência de alto valor, mas não verdade absoluta**. Muitos PDFs não são tagged; outros têm tags incompletas ou incorretas.

Uso sugerido:

```text
structure tree
  → sinal de tipo de região
  → sinal adicional de ordem
  → sinal de tabela/célula
  → títulos/semântica quando consistente
```

Não bloquear o MVP se a associação perfeita entre MCID e token exigir trabalho adicional. O primeiro passo pode preservar a árvore e seus identificadores para uso progressivo.

### 20.4. Batch extraction

A implementação inicial em pypdfium2 pode chamar APIs por caractere. Isso é simples, porém o overhead FFI precisa ser medido.

O design deve esconder esse detalhe atrás de:

```python
class NativeEvidenceSource(Protocol):
    def extract_page(self, page_index: int) -> NativePageEvidence:
        ...
```

Se o overhead for relevante, podemos trocar a implementação por uma extensão Rust/PyO3 que retorna arrays em lote sem modificar o restante do sistema.

### 20.5. Não reconstruir ainda

Esta etapa não deve decidir:

* palavras;
* parágrafos;
* colunas;
* tabelas;
* cabeçalhos;
* ordem final.

Ela coleta fatos.

---

## 21. Estágio 2 — Page Evidence & Complexity Analyzer

Essa etapa é inspirada principalmente em LiteParse e Marker.

### 21.1. Saída

```python
@dataclass
class PageComplexity:
    reasons: set[ComplexityReason]
    native_text_score: float
    visual_recovery_needed: bool
    layout_needed: bool
    full_page_ocr_candidate: bool
```

### 21.2. Razões iniciais

```python
class ComplexityReason(Enum):
    NO_TEXT = "no_text"
    SCANNED = "scanned"
    SPARSE_TEXT = "sparse_text"
    EMBEDDED_IMAGES = "embedded_images"
    GARBLED_UNICODE = "garbled_unicode"
    DUPLICATE_TEXT_LAYER = "duplicate_text_layer"
    INVISIBLE_TEXT = "invisible_text"
    VECTOR_TEXT = "vector_text"
    ANNOTATION_TEXT = "annotation_text"
    MULTI_COLUMN_LIKELY = "multi_column_likely"
    TABLE_LIKELY = "table_likely"
    ROTATED_TEXT = "rotated_text"
```

### 21.3. Sinais baratos

Primeiro calcular sem modelos:

* `native_char_count`;
* comprimento textual útil;
* razão de U+FFFD;
* razão de caracteres de controle;
* razão de codepoints improváveis;
* cobertura geométrica de texto;
* quantidade/área de imagens;
* maior imagem em relação à página;
* área de paths preenchidos não coberta por texto;
* sobreposição anormal de linhas/caracteres;
* distribuição de ângulos;
* densidade de bboxes.

### 21.4. Garbled text

Sinais possíveis:

```text
alta proporção de replacement chars
muitos caracteres de controle
sequências de codepoints privadas sem explicação
repetições anormais
texto grande com vocabulário praticamente nulo
mapeamento Unicode marcado como falho
```

Não usar dicionário de português como única prova. Nomes, processos, códigos e identificadores legítimos podem parecer “palavras ruins”.

### 21.5. Duplicate OCR layer

Detectar bboxes quase idênticos com strings idênticas ou altamente semelhantes.

Também detectar muitas linhas sobrepostas em posições incompatíveis, inspirando-se no Marker.

### 21.6. Invisible text

Essa é uma área importante para PDFs com camada OCR antiga.

Para linhas suspeitas:

1. mapear bbox para a página renderizada;
2. medir se existe tinta/pixel não branco na região;
3. se não houver conteúdo visual onde o texto afirma estar, marcar como `INVISIBLE_TEXT`.

Não realizar crop individual para milhares de caracteres. Fazer por linha ou bloco e, se possível, usar integral image/máscara de tinta para consulta barata.

### 21.7. Vector text

Se a página tem área significativa de paths preenchidos que não é explicada pelas bboxes textuais, marcar como possível `VECTOR_TEXT`.

O objetivo não é identificar letras vetoriais perfeitamente. O objetivo é acionar inspeção visual/OCR.

### 21.8. Annotation text

Se a página parece vazia mas possui anotações/appearance streams relevantes, considerar OCR ou inspeção adicional.

### 21.9. O resultado não deve ser apenas um score

Não queremos:

```text
quality = 0.63
```

sem explicação.

Queremos:

```json
{
  "native_text_score": 0.63,
  "reasons": ["garbled_unicode", "embedded_images"],
  "recommended_strategy": "hybrid"
}
```

---

## 22. Estágio 3 — Layout Region Detector

### 22.1. Por que layout visual passa a fazer parte do MVP

Somente geometria textual não responde com segurança se duas faixas verticais representam:

* duas colunas de artigo;
* duas colunas de uma tabela;
* texto e uma legenda;
* sidebar e corpo;
* valores de formulário;
* duas áreas independentes.

A maior mudança da arquitetura é usar layout como sinal anterior à ordem de leitura.

### 22.2. Contrato

```python
class LayoutEngine(Protocol):
    def detect(self, image: PageImage) -> list[LayoutRegionPrediction]:
        ...
```

A interface não deve depender de PaddleOCR, Docling ou outro fornecedor.

### 22.3. Engine inicial recomendada

Para o MVP, o **primeiro candidato concreto será PP-DocLayoutV3**, ou a distribuição standalone equivalente disponibilizada pelo ecossistema PaddleOCR na versão fixada pelo projeto, por três motivos:

* integração Python direta;
* modelos preparados para documentos;
* caminho natural para tabela e OCR no mesmo ecossistema.

Uma alternativa válida é RT-DETR/ONNX no estilo Xberg.

A escolha definitiva deverá considerar:

* licença dos pesos;
* desempenho em CPU;
* desempenho em GPU disponível na empresa;
* classes reconhecidas;
* acurácia no corpus real;
* tamanho dos modelos;
* facilidade de empacotamento on premises.

### 22.4. O layout não é autoridade textual

O detector pode dizer:

```text
bbox X = TABLE
```

mas não pode inventar ou substituir o conteúdo textual dessa bbox.

O papel dele é:

```text
onde está a região?
qual tipo estrutural ela provavelmente possui?
como devemos encaminhá-la?
```

### 22.5. Classes mínimas para o MVP

Não precisamos de dezenas de tipos sem uso. Classes mínimas:

```text
TEXT
TITLE
LIST
TABLE
FIGURE
CAPTION
HEADER
FOOTER
FOOTNOTE
UNKNOWN
```

### 22.6. Layout em toda página ou somente em páginas complexas?

Para maximizar qualidade, proponho dois perfis desde o MVP:

**`fast`**

* roda análise determinística;
* usa layout visual somente quando complexidade indica necessidade.

**`balanced`**

* roda layout de baixa resolução em todas as páginas;
* continua evitando OCR quando a camada textual é boa.

Durante desenvolvimento com documentos reais da empresa, `balanced` deve ser o padrão de avaliação. Depois medimos se `fast` entrega qualidade suficiente em categorias simples.

### 22.7. Assignment de texto nativo às regiões

Depois das regiões visuais, cada linha/token nativo é associado por interseção.

Não usar apenas centro do bbox. Sugerimos:

```text
intersection_area(text_bbox, region_bbox) / text_bbox_area
```

Com prioridade para a região com maior cobertura.

Casos ambíguos ficam marcados, não descartados.

---

## 23. Estágio 4 — Reconstrução do texto nativo

O engine ainda precisa fazer o trabalho que motivou o projeto inicialmente: reconstruir caracteres em linhas e tokens melhor do que simplesmente usar uma string devolvida pelo parser.

### 23.1. Normalização conservadora

Manter duas formas:

```python
raw_text: str
normalized_text: str
```

Normalizações permitidas na visão normalizada:

* Unicode NFC;
* whitespace equivalente para espaço normal quando apropriado;
* remoção de controles sem semântica;
* normalização de line ending.

Não:

* trocar caracteres acentuados por ASCII;
* “corrigir” palavras por dicionário;
* alterar números;
* adivinhar letra por contexto sem evidência adicional.

### 23.2. Deduplicação

Duplicatas podem surgir de:

* texto pintado mais de uma vez;
* falso bold;
* layer OCR duplicada;
* sombra;
* content streams repetidos.

Critérios:

```text
mesmo texto/codepoint
+ bbox com alto overlap
+ origem muito próxima
+ fonte/tamanho compatível
```

A ação será marcar uma evidência como duplicata derivada, não deletá-la do raw evidence.

### 23.3. Formação de linhas

Agrupar primeiro por orientação.

Para caracteres horizontais, estimar baseline e altura mediana.

Características adaptativas:

* diferença perpendicular à baseline;
* overlap vertical;
* tamanho de fonte;
* direção;
* distância normalizada pelo tamanho/advance;
* sequência nativa extraída.

Não usar tolerância fixa universal em pontos.

### 23.4. Espaços

Hierarquia de confiança:

```text
1. whitespace explícito no PDF
2. whitespace gerado pela engine
3. gap geométrico inferido
```

O threshold geométrico deve ser aprendido por linha/span.

Exemplo:

```text
median_char_advance = mediana dos advances
word_gap_candidate = gap / median_char_advance
```

A distribuição local permite diferenciar kerning de espaço real melhor que um valor fixo.

### 23.5. Palavras

Palavra é uma visão derivada, não unidade fundamental.

Manter pontuação no token apropriado conforme saída, mas preservar caracteres individuais na evidência.

### 23.6. Spans

Span pode ser criado quando caracteres adjacentes compartilham:

* fonte;
* tamanho compatível;
* peso;
* estilo;
* cor quando disponível;
* orientação;
* origem de evidência.

Não usar span para decidir leitura global.

---

## 24. Estágio 5 — Region Quality Gate

Depois de layout e reconstrução nativa, cada região recebe uma decisão.

### 24.1. Estados

```python
class RegionDecision(Enum):
    KEEP_NATIVE = "keep_native"
    MERGE_OCR = "merge_ocr"
    OCR_REGION = "ocr_region"
    ESCALATE_PAGE_OCR = "escalate_page_ocr"
```

### 24.2. `KEEP_NATIVE`

Usar quando:

* texto nativo existe;
* Unicode é saudável;
* cobertura visual é plausível;
* não há duplicação destrutiva;
* região tem linhas atribuídas;
* imagem não sugere texto ausente importante.

### 24.3. `MERGE_OCR`

Usar quando:

* região contém imagem/figura junto a texto;
* texto nativo cobre apenas parte do conteúdo visual;
* existe suspeita de labels rasterizados;
* tabela possui alguns valores rasterizados.

### 24.4. `OCR_REGION`

Usar quando:

* layout detectou região textual sem texto nativo;
* texto é garbled;
* texto nativo é invisível ou inconsistente com a imagem;
* há forte sinal de vector text;
* tabela/figura necessita leitura visual.

### 24.5. `ESCALATE_PAGE_OCR`

Usar quando:

* página é scan;
* grande parte das regiões textuais está ruim;
* custo de dezenas de crops supera OCR da página;
* orientação/unwarping precisa ser tratado globalmente.

### 24.6. Threshold de promoção

Não fixar definitivamente antes do corpus.

Começar com uma métrica simples:

```text
bad_text_area / total_text_region_area
```

mais

```text
bad_text_regions / total_text_regions
```

Se ambos forem altos, promover.

---

## 25. Estágio 6A — Ordem de leitura de prosa

### 25.1. Nenhuma fonte sozinha é suficiente

Usaremos quatro famílias de evidência:

```text
SEQUÊNCIA NATIVA EXTRAÍDA
sequência da text page do backend

ESTRUTURA LÓGICA
structure tree / tags / MCIDs quando presentes e consistentes

GEOMETRY
posição, colunas, gaps, alinhamento

LAYOUT VISUAL
regiões e tipos detectados
```

### 25.2. Construir um grafo, não apenas ordenar por `(y, x)`

Cada região de prosa vira nó.

Edges candidatam relações:

```text
A before B
```

com peso derivado de:

* `A` acima de `B` com overlap horizontal;
* coluna de `A` anterior à coluna de `B`;
* sequência nativa extraída;
* ordem/relacionamentos da structure tree quando confiáveis;
* layout order quando fornecido;
* continuidade de baseline/parágrafo;
* distância espacial.

Depois resolver uma ordem consistente.

### 25.3. XY-cut

XY-cut continua útil, mas agora somente dentro de regiões ou grupos classificados como prosa.

Uso recomendado:

```text
page prose regions
   ↓
separar faixas full-width
   ↓
XY-cut nos grupos restantes
   ↓
colunas
```

Isso evita que um título largo sobre duas colunas seja partido de forma incorreta.

### 25.4. Sequência nativa extraída

Se a camada nativa é saudável e o sequência nativa extraída já percorre as colunas corretamente, devemos valorizá-lo.

Criar um score:

```text
native_order_consistency
```

medindo quantas transições do sequência nativa extraída são geometricamente plausíveis.

Se alto, sequência nativa extraída recebe peso forte.

Se baixo, geometria/layout dominam.

### 25.5. Duas colunas

Cenário esperado:

```text
Título em largura total

Coluna A          Coluna B
A1                B1
A2                B2
A3                B3

Rodapé em largura total
```

Estrutura desejada:

```text
Title
ColumnGroup
  Column A
  Column B
Footer
```

Não:

```text
Title
A1
B1
A2
B2
...
```

### 25.6. Texto rotacionado

Agrupar por orientação canônica próxima de:

```text
0°, 90°, 180°, 270°
```

Rotações pequenas podem ser normalizadas por tolerância; rotações arbitrárias devem preservar quad/bbox e ser tratadas como grupo próprio.

Textos marginais verticais não devem ser inseridos no meio do corpo simplesmente porque compartilham Y.

---

## 26. Estágio 6B — Table Pipeline

Tabelas passam a ser parte explícita do MVP.

### 26.1. Por que uma cascata

Nenhum detector de tabela é ótimo em todos os casos.

Temos pelo menos estas classes:

```text
A. tabela com bordas completas
B. tabela com algumas linhas
C. tabela sem bordas, alinhada por colunas
D. tabela irregular
E. tabela digital com texto ruim
F. tabela em scan
G. tabela continuada em outra página
```

A estratégia deve ser progressiva.

### 26.2. Tier 1 — Grid vetorial estrito

Usar paths/linhas do PDF.

Detectar:

* segmentos horizontais;
* segmentos verticais;
* interseções;
* retângulos;
* tracks de coluna/linha.

Alta precisão.

Se grade coerente for encontrada, atribuir tokens por célula.

### 26.3. Tier 2 — Grid relaxado

Para:

* bordas incompletas;
* somente linhas horizontais;
* somente separadores principais;
* tabelas de duas colunas label/value.

Usar alinhamento textual para completar a estrutura.

### 26.4. Tier 3 — Tabela sem bordas por texto

Usar linhas/tokens nativos.

Sinais:

* tracks X recorrentes;
* números alinhados à direita;
* labels na primeira coluna;
* distribuição semelhante por várias linhas;
* gaps horizontais recorrentes;
* coerência vertical;
* baixa “prosa contínua”.

Precisamos de um `ProseVsTableClassifier` determinístico antes de aceitar.

### 26.5. Tier 4 — Modelo visual de estrutura

Se layout detectou `TABLE` mas tiers determinísticos falharam ou geraram baixa confiança, executar modelo estrutural.

Candidatos de avaliação:

* PaddleOCR Table Recognition v2;
* SLANet/SLANeXT;
* TATR;
* outro modelo ONNX com licença adequada.

O contrato deve ser nosso:

```python
class TableStructureEngine(Protocol):
    def recognize(self, image: RegionImage) -> TableStructurePrediction:
        ...
```

### 26.6. Tier 5 — OCR dentro das células/região

O modelo pode recuperar estrutura sem texto perfeito. Depois:

* se há texto nativo confiável, mapear native tokens às células;
* se não há, usar OCR;
* se há ambos, fazer merge.

### 26.7. Confiança da tabela

Combinar:

```text
grid coherence
row consistency
column track stability
cell assignment coverage
native text coverage
model confidence
OCR confidence
```

### 26.8. Não transformar tabela em Markdown cedo

Guardar células primeiro.

Markdown é apenas renderer:

```text
StructuredTable → MarkdownTableRenderer
```

Isso é essencial para `rowspan`, `colspan`, células vazias e tabelas entre páginas.

---

## 27. Tabelas continuadas entre páginas

Esse requisito agora faz parte do MVP, porque foi explicitamente citado como um caso real importante.

### 27.1. Problema

Página 10:

```text
| Processo | Parte | Valor |
| ...      | ...   | ...   |
| 123      | João  | 900   |
```

Página 11:

```text
| Processo | Parte | Valor |
| 124      | Maria | 300   |
| ...      | ...   | ...   |
```

Podem ser:

* duas tabelas independentes;
* uma única tabela continuada com cabeçalho repetido;
* uma tabela sem cabeçalho na segunda página;
* uma tabela cuja primeira linha da segunda página é continuação de uma célula da página anterior.

### 27.2. `TableSignature`

```python
@dataclass
class TableSignature:
    column_count: int
    normalized_x_tracks: tuple[float, ...]
    header_rows: tuple[RowSignature, ...]
    first_data_row: RowSignature | None
    last_data_row: RowSignature | None
    bbox: BBox
    page_index: int
    touches_top: bool
    touches_bottom: bool
```

### 27.3. `RowSignature`

Inspirado conceitualmente no MinerU:

```python
@dataclass
class RowSignature:
    effective_columns: int
    colspans: tuple[int, ...]
    rowspans: tuple[int, ...]
    normalized_cells: tuple[str, ...]
```

### 27.4. Evidências de continuação

Pontuar:

* tabela A próxima ao fim da página;
* tabela B próxima ao início da próxima;
* número de colunas compatível;
* tracks X compatíveis;
* cabeçalho idêntico ou semelhante;
* segunda página começa diretamente por dados;
* texto “continuação”, “continua”, “cont.” ou equivalentes;
* sem novo título forte entre as páginas;
* sem mudança drástica de largura;
* tipos de células semelhantes por coluna.

### 27.5. Cabeçalho repetido

Se B repete o cabeçalho de A, não duplicar o header na estrutura lógica da tabela, mas preservar a ocorrência por página em `page_fragments`.

### 27.6. Rowspan entre páginas

Se a estrutura sugere uma célula aberta ao fim da página, manter ocupação lógica para o fragmento seguinte.

Não precisa resolver todos os casos de `rowspan` do mundo no MVP, mas o modelo de dados precisa permitir evolução.

### 27.7. Não perder paginação

Mesmo após merge:

```python
table.page_fragments
```

deve indicar onde cada linha/célula apareceu.

Isso é importante para auditoria e eventual highlight no PDF.

---

## 28. Estágio 7 — OCR Recovery

Apesar da numeração, `OCR Recovery` deve ser implementado como **serviço invocável pelos processadores de região**, não como uma passagem linear obrigatória depois de toda prosa e tabela. Uma região de tabela, por exemplo, pode chamar OCR antes da montagem das células; uma região de prosa pode permanecer 100% nativa. O diagrama de estágios representa dependências lógicas, não a necessidade de executar cada bloco em sequência para toda página.

### 28.1. Engine recomendada

PaddleOCR é a primeira engine a ser avaliada para o MVP.

Motivos:

* português;
* OCR moderno;
* detecção + reconhecimento;
* orientação;
* possibilidade de unwarping;
* modelos de tabela e layout próximos do mesmo ecossistema;
* execução local.

### 28.2. OCR por região

Fluxo:

```text
LayoutRegion bbox em pontos
      ↓
converter para pixel bbox
      ↓
adicionar pequena margem
      ↓
crop em resolução adequada
      ↓
OCR
      ↓
converter tokens para coordenadas PDF canônicas
```

### 28.3. Resolução

Começar com:

```text
150 a 200 DPI → texto normal
250 a 300 DPI → texto pequeno ou scan ruim
```

Não tornar 300 DPI padrão universal antes de medir custo e qualidade.

### 28.4. OCR de página inteira

Quando necessário:

1. detectar orientação;
2. renderizar;
3. aplicar correção geométrica se habilitada;
4. OCR;
5. mapear tokens para espaço canônico;
6. reconstruir layout/regiões ou utilizar regiões detectadas.

### 28.5. OCR não deve “limpar” silenciosamente a saída

Guardar:

```python
ocr_raw_text
ocr_normalized_text
ocr_confidence
```

Se o OCR retornar `Justica` e o nativo trouxer `Justiça` confiável, não substituir pelo OCR.

### 28.6. OCR de português

Criar corpus específico com:

```text
ã õ ç á à â é ê í ó ô ú
```

Além de:

```text
Nº
§
R$
1ª
2º
```

E termos comuns do domínio da empresa.

O objetivo não é treinar um modelo no MVP, mas escolher/configurar corretamente a engine.

---

## 29. Estágio 8 — Evidence Fusion

Esta é a camada que diferencia o produto de simplesmente encadear bibliotecas.

### 29.1. Entradas

Para uma região podemos ter:

```text
native tokens
OCR tokens
layout bbox/type
native sequência nativa extraída
image ink map
font metadata
quality flags
```

### 29.2. Alinhamento espacial

Para cada token OCR, buscar candidatos nativos com:

* overlap de bbox;
* distância de centros;
* similaridade de linha;
* texto normalizado semelhante.

### 29.3. Casos

#### Mesmo texto, mesma região

```text
native: "Justiça"
ocr:    "Justiça"
```

Resultado:

```text
"Justiça"
sources = [native, ocr]
confidence elevada
```

#### OCR perde acento

```text
native: "Justiça"
ocr:    "Justica"
```

Se native está saudável:

```text
usar "Justiça"
registrar divergência
```

#### Native corrompido

```text
native: "Ju�ti�a"
ocr:    "Justiça"
```

Resultado:

```text
usar OCR
flag = recovered_from_garbled_native
```

#### OCR encontra texto ausente

```text
native: nenhuma bbox correspondente
ocr:    "TOTAL"
```

Se região visual e confiança forem suficientes:

```text
adicionar token OCR
```

#### Camada OCR invisível duplicada

```text
native A: bbox visual válida
native B: mesma palavra deslocada, sem tinta
OCR:      confirma A
```

Resultado:

```text
usar A
marcar B como invisible_duplicate
```

### 29.4. Similaridade textual

Usar similaridade somente como uma evidência, nunca sozinha.

Um número `1.234,56` não pode ser unido a `1.284,56` apenas porque strings são parecidas.

Para tokens predominantemente numéricos, exigir correspondência mais estrita.

### 29.5. Confiança

Evitar falso rigor matemático. O score inicial pode ser heurístico, desde que explicável.

Exemplo:

```text
+ native Unicode válido
+ visual ink presente
+ OCR concorda
+ bbox consistente
- camada marcada garbled
- overlap duplicado
- OCR baixa confiança
```

### 29.6. Conflito não resolvido

Se duas evidências plausíveis discordarem:

```python
TokenConflict(
    chosen="...",
    alternatives=[...],
    reason="..."
)
```

Isso é melhor que esconder incerteza.

---

## 30. Estágio 9 — Page Assembler

Depois que regiões estão resolvidas:

* montar linhas finais;
* juntar tokens;
* formar parágrafos;
* incluir tabelas como blocos estruturais;
* produzir ordem da página;
* gerar `raw_text` e `reading_text`.

### 30.1. Parágrafos

Sinais:

* distância vertical;
* indentação;
* continuidade tipográfica;
* pontuação final;
* largura de linha;
* tipo da região;
* sequência nativa extraída;
* hifenização.

### 30.2. Hifenização

Não remover `-` automaticamente.

Candidato quando:

```text
linha termina em letra + hífen
próxima linha começa em letra minúscula
mesmo parágrafo/região
```

Manter informação:

```python
HyphenJoin(
    original="implemen-\ntação",
    joined="implementação",
)
```

### 30.3. Cabeçalhos e rodapés

Em `raw_text`: preservar.

Em `reading_text`: política configurável.

Sinalizar repetição por:

* posição similar;
* texto igual/similar em várias páginas;
* fonte/estrutura compatível.

---

## 31. Estágio 10 — Document Assembler

### 31.1. Responsabilidades

* concatenar páginas sem perder boundaries;
* detectar elementos repetidos;
* resolver tabelas entre páginas;
* preservar links de proveniência;
* gerar saídas de documento.

### 31.2. Page boundaries

Nunca perder a relação:

```text
caractere → token → line → region → page
```

### 31.3. Tabelas entre páginas

Rodar `CrossPageTableResolver` após todas as páginas estarem estruturadas.

### 31.4. Continuidade de parágrafo entre páginas

Pode ser implementada após a tabela, mas no MVP deve ser conservadora.

Exemplo:

```text
página termina sem pontuação
próxima começa minúscula
mesmo padrão de coluna
```

Pode sinalizar continuidade, mas não precisa fundir irreversivelmente na estrutura primária.

---

## 32. Unicode e português do Brasil

### 32.1. Prioridade

Para documentos digitais, PDFium deve ser a primeira fonte para Unicode.

OCR entra quando o mapeamento nativo é inadequado.

### 32.2. Preservar diacríticos

NFC na saída normalizada:

```python
unicodedata.normalize("NFC", text)
```

Mas `raw_text` pode manter a sequência original se for necessário para auditoria.

### 32.3. Caracteres problemáticos

Monitorar:

```text
U+FFFD
Private Use Area
glyph sem Unicode
CID exposto como texto
controles inesperados
```

### 32.4. Recuperação progressiva

Ordem sugerida:

```text
1. Unicode nativo
2. informação alternativa do objeto/text range
3. mapa de glyph/font quando disponível
4. OCR da região
5. replacement char + diagnóstico
```

Não inventar caractere por linguagem natural no MVP.

### 32.5. Normalizações que NÃO fazer

Não transformar:

```text
ç → c
ã → a
º → o
ª → a
```

Não reformatar automaticamente:

```text
CPF
CNPJ
número de processo
valores monetários
datas
```

---

## 33. Casos difíceis e estratégia esperada

| Caso | Caminho preferido |
|---|---|
| PDF digital simples | PDFium nativo, sem OCR |
| PDF digital em duas colunas | structure tree quando útil + layout + sequência nativa extraída + XY-cut em prosa |
| scan completo | page OCR |
| página híbrida | native + region OCR |
| texto nativo garbled | region/page OCR conforme extensão |
| layer OCR duplicada | overlap + visual ink + dedup |
| texto invisível | visual verification e OCR se necessário |
| texto como vetor | path signal + OCR |
| texto em annotation appearance | annotation signal + OCR/inspection |
| tabela com bordas | vector grid detector |
| tabela de duas colunas com borda | relaxed grid detector |
| tabela sem borda | text track heuristic |
| tabela visual difícil | table structure model + OCR |
| tabela em scan | layout + table model + OCR |
| tabela entre páginas | cross page table resolver |
| texto rotacionado | orientation group + region handling |
| cabeçalho/rodapé repetido | preservar raw, classificar reading |

---

## 34. Stack tecnológica recomendada

### 34.1. Linguagem

**Python 3.12+** para o MVP.

Motivos:

* velocidade de implementação;
* ecossistema de OCR e modelos documentais;
* integração simples com PaddleOCR/ONNX/PyTorch quando necessário;
* facilidade de criar tooling de diagnóstico;
* integração com pypdfium2;
* prototipação rápida de heurísticas usando documentos reais.

### 34.2. PDF backend

**pypdfium2 + PDFium**.

Não mudar essa decisão agora.

### 34.3. OCR

Primeira opção a avaliar e implementar:

**PaddleOCR**, fixando uma versão do pipeline OCR que ofereça reconhecimento multilíngue com `pt`/Portuguese. O repositório atual documenta português entre os idiomas suportados; a versão exata do modelo será congelada depois do benchmark inicial do corpus.

Manter contrato pluggable para testar outro engine sem alterar pipeline.

### 34.4. Layout

Primeira avaliação:

* modelo de layout do ecossistema PaddleOCR;
* alternativa ONNX RT-DETR/PP-DocLayout compatível com nossos requisitos.

A seleção final deve sair do corpus empresarial, não de benchmark público isolado.

### 34.5. Tabela visual

Interface pluggable.

Candidatos:

* Paddle table recognition;
* SLANet/SLANeXT;
* TATR.

### 34.6. NumPy/OpenCV

Úteis para:

* matrizes de overlap;
* mask de tinta;
* análise de imagem;
* projections;
* agrupamento geométrico;
* transformação de coordenadas.

Não usar OpenCV como requisito para toda página se uma operação equivalente puder ser feita com arrays/Pillow mais barato.

### 34.7. Rust como otimização posterior

LiteParse mostra o valor de um core nativo. Não devemos ignorar isso, mas também não devemos começar reimplementando o projeto em Rust.

Limite claro:

> Se profiling mostrar que a enumeração PDFium e as transformações de arrays dominam o tempo, criar uma extensão Rust/PyO3 **somente para aquisição/batch geométrico**.

O restante do pipeline permanece Python.

---

## 35. Estrutura de repositório sugerida

```text
structured-pdf-text/
├── pyproject.toml
├── README.md
├── docs/
│   ├── architecture.md
│   ├── decisions/
│   ├── corpus-guide.md
│   └── diagnostics.md
├── src/
│   └── structured_pdf_text/
│       ├── __init__.py
│       ├── api.py
│       ├── config.py
│       ├── document.py
│       ├── geometry.py
│       │
│       ├── native/
│       │   ├── source.py
│       │   ├── pdfium_source.py
│       │   ├── characters.py
│       │   ├── objects.py
│       │   └── coordinates.py
│       │
│       ├── evidence/
│       │   ├── model.py
│       │   ├── complexity.py
│       │   ├── garbled.py
│       │   ├── duplicates.py
│       │   ├── visual_ink.py
│       │   └── decision.py
│       │
│       ├── layout/
│       │   ├── engine.py
│       │   ├── paddle.py
│       │   ├── assign.py
│       │   └── regions.py
│       │
│       ├── text/
│       │   ├── normalize.py
│       │   ├── line_detector.py
│       │   ├── word_detector.py
│       │   ├── spans.py
│       │   ├── paragraphs.py
│       │   ├── hyphenation.py
│       │   └── reading_order.py
│       │
│       ├── ocr/
│       │   ├── engine.py
│       │   ├── paddle.py
│       │   ├── render.py
│       │   └── recovery.py
│       │
│       ├── fusion/
│       │   ├── align.py
│       │   ├── token_fusion.py
│       │   ├── conflicts.py
│       │   └── confidence.py
│       │
│       ├── tables/
│       │   ├── model.py
│       │   ├── detector.py
│       │   ├── ruled.py
│       │   ├── relaxed.py
│       │   ├── text_tracks.py
│       │   ├── visual_engine.py
│       │   ├── cells.py
│       │   └── cross_page.py
│       │
│       ├── assemble/
│       │   ├── page.py
│       │   ├── document.py
│       │   └── repeated_regions.py
│       │
│       ├── renderers/
│       │   ├── text.py
│       │   ├── markdown.py
│       │   └── json.py
│       │
│       ├── diagnostics/
│       │   ├── dump.py
│       │   ├── overlay.py
│       │   ├── compare.py
│       │   └── report.py
│       │
│       └── cli.py
├── corpus/
│   └── .gitkeep
└── scripts/
    ├── benchmark.py
    ├── inspect_pdf.py
    └── compare_extractors.py
```

### 35.1. Motivo da separação

Queremos poder substituir:

```text
PDFium source
layout engine
OCR engine
table structure engine
```

sem alterar o modelo estrutural e os renderers.

---

## 36. API pública do MVP

### 36.1. Uso simples

```python
from structured_pdf_text import PdfTextExtractor

extractor = PdfTextExtractor()
result = extractor.extract("documento.pdf")

print(result.reading_text)
```

### 36.2. Máxima recuperação

```python
print(result.raw_text)
```

### 36.3. Estrutura

```python
for page in result.pages:
    for region in page.regions:
        print(region.kind, region.bbox)
```

### 36.4. Tabelas

```python
for table in result.tables:
    print(table.table_id)
    print(table.cells)
```

### 36.5. Diagnóstico

```python
for page in result.pages:
    print(page.diagnostics.strategy)
    print(page.diagnostics.reasons)
```

### 36.6. Configuração

```python
extractor = PdfTextExtractor(
    ExtractorConfig(
        mode="balanced",
        language="pt",
        enable_ocr=True,
        enable_layout=True,
        enable_tables=True,
        merge_cross_page_tables=True,
        preserve_headers_footers=True,
    )
)
```

### 36.7. Modos

```text
native
fast
balanced
ocr
```

**`native`**: PDFium + reconstrução determinística, nenhum modelo.

**`fast`**: análise de complexidade, modelos apenas quando acionados.

**`balanced`**: layout em toda página e OCR seletivo. Padrão recomendado para o objetivo de qualidade.

**`ocr`**: força OCR da página, principalmente para diagnóstico.

---

## 37. CLI do MVP

```bash
pdftext extract documento.pdf
```

Saída de máxima recuperação:

```bash
pdftext extract documento.pdf --output raw
```

JSON estruturado:

```bash
pdftext extract documento.pdf --output json
```

Markdown:

```bash
pdftext extract documento.pdf --output markdown
```

Modo:

```bash
pdftext extract documento.pdf --mode balanced
```

Português:

```bash
pdftext extract documento.pdf --language pt
```

Diagnóstico:

```bash
pdftext inspect documento.pdf --page 12
```

Overlay:

```bash
pdftext overlay documento.pdf --page 12 --out page-12.png
```

Comparação:

```bash
pdftext compare documento.pdf --against pymupdf,liteparse,marker,docling
```

---

## 38. Ferramentas de diagnóstico obrigatórias

Essas ferramentas não são “nice to have”. Com PDFs reais, serão o mecanismo principal de evolução.

### 38.1. Character dump

CSV/JSON:

```text
index
unicode
text
bbox
origin
font
size
angle
generated
mapping_failed
visible
```

### 38.2. Overlay visual

Gerar imagem com camadas selecionáveis:

```text
native chars
native lines
layout regions
OCR tokens
tables
cells
reading order
conflicts
```

### 38.3. Evidence report

Por página:

```text
strategy
complexity reasons
native chars
OCR chars
native coverage
OCR recovered count
conflicts
layout regions
tables
processing time
```

### 38.4. Diff textual

Comparar:

```text
nosso raw_text
nosso reading_text
PyMuPDF text
PyMuPDF sort=True
LiteParse
Docling
MinerU
Xberg
Marker
PaddleOCR visual
```

Não é obrigatório executar todos em todo ciclo. O script deve aceitar adapters disponíveis.

### 38.5. Table viewer

Gerar HTML simples mostrando:

* imagem da região;
* grid detectado;
* células;
* texto por célula;
* confiança;
* fragmentos entre páginas.

---

## 39. Escopo do MVP revisado

### 39.1. Obrigatório

O MVP só deve ser considerado completo quando possuir:

| Capacidade | Obrigatória |
|---|---|
| texto nativo PDFium | sim |
| bbox por caractere | sim |
| reconstrução de linhas | sim |
| inferência de espaços/palavras | sim |
| acentuação pt-BR preservada | sim |
| detecção de duplicação | sim |
| detecção de camada textual ruim | sim |
| renderização da página | sim |
| layout regions | sim |
| duas colunas | sim |
| OCR seletivo | sim |
| OCR de scan | sim |
| merge nativo/OCR | sim |
| tabela com bordas | sim |
| tabela sem borda básica | sim |
| fallback de table model | sim |
| tabela entre páginas | sim |
| raw_text | sim |
| reading_text | sim |
| JSON estruturado | sim |
| diagnóstico/overlay | sim |

### 39.2. Fora do MVP

Pode ficar para depois:

* fórmulas para LaTeX;
* descrição de figuras;
* entendimento de charts;
* handwriting especializado além do que OCR já fornecer;
* semântica jurídica;
* classificação de assunto;
* RAG/chunking;
* VLM genérico obrigatório;
* DOCX/PPTX;
* treinamento de modelos próprios.

### 39.3. Atenção ao escopo

Adicionar tabela visual e layout ao MVP aumenta o esforço em relação ao plano anterior. Isso é justificado porque o requisito mudou: “o máximo de texto possível nos mais variados PDFs” não é atendido de forma honesta por um engine baseado somente na camada textual.

---

## 40. Plano de implementação

A sequência abaixo prioriza um resultado utilizável cedo, mas evita construir uma arquitetura que precisará ser descartada quando chegarmos às tabelas e OCR.

### Milestone 0 — Baseline e corpus

**Objetivo:** saber contra o que estamos competindo.

Implementar:

* estrutura do repositório;
* CLI mínima;
* corpus local privado;
* runner por diretório;
* adapters de comparação disponíveis;
* registro de tempo e saída.

Separar documentos em categorias, sem tentar criar um benchmark acadêmico.

Sugestão de categorias:

```text
digital-simple
digital-multicolumn
digital-bad-font
duplicate-ocr-layer
hybrid-page
scan-clean
scan-poor
table-bordered
table-borderless
table-cross-page
table-bad-format
rotated
legacy-system
```

**Saída:** relatório bruto dos extratores existentes sobre documentos reais.

### Milestone 1 — Native Evidence Foundation

**Objetivo:** extrair tudo que PDFium sabe sem perder dados.

Implementar:

* `BBox`/coordenadas;
* `NativeCharacter`;
* `NativeObjectEvidence`;
* char index;
* Unicode;
* font/size/angle;
* flags do PDFium;
* images;
* paths mínimos;
* annotations mínimas;
* raw page dump.

**Saída:** JSON bruto por página + texto nativo trivial.

### Milestone 2 — Native Text Reconstruction

**Objetivo:** produzir texto nativo útil sem modelos.

Implementar:

* normalização conservadora;
* deduplicação;
* orientation groups;
* line detector;
* space detector;
* word/token detector;
* sequência nativa extraída diagnostics.

**Saída:** `native_reading_text` e overlays de linha/token.

### Milestone 3 — Complexity Analyzer

**Objetivo:** saber quando não confiar na camada textual.

Implementar:

* text coverage;
* full page image;
* image coverage;
* garbled Unicode;
* duplicate layer;
* visible ink verification;
* vector area signal;
* annotation text signal;
* page decision report.

**Saída:** `NATIVE`, `HYBRID_CANDIDATE`, `OCR_CANDIDATE` + razões.

### Milestone 4 — Layout Regions

**Objetivo:** separar prosa, tabela e outros tipos antes de resolver leitura.

Implementar:

* `LayoutEngine`;
* primeira engine;
* low-res rendering;
* region normalization;
* native line assignment;
* region overlay.

**Saída:** página segmentada.

### Milestone 5 — Reading Order robusto

**Objetivo:** resolver uma e duas colunas sem corromper tabelas.

Implementar:

* region graph;
* full-width bands;
* source-order consistency;
* XY-cut somente em prosa;
* rotated groups;
* title/footnote/caption handling básico.

**Saída:** `reading_text` bom para PDFs digitais complexos.

### Milestone 6 — OCR Engine e scans

**Objetivo:** recuperar páginas sem texto.

Implementar:

* `OcrEngine`;
* PaddleOCR adapter;
* português;
* page render;
* page OCR;
* coordinate transform;
* OCR overlay;
* confidence.

**Saída:** scans convertidos para tokens estruturados.

### Milestone 7 — OCR seletivo e Evidence Fusion

**Objetivo:** páginas híbridas.

Implementar:

* `RegionQuality`;
* OCR de crop;
* promoção page OCR;
* spatial alignment;
* native/OCR dedup;
* conflict handling;
* source provenance.

**Saída:** `Mixed` extraction real.

### Milestone 8 — Table Pipeline determinístico

**Objetivo:** resolver tabelas digitais comuns sem depender de modelo.

Implementar:

* path line extraction;
* strict grid;
* relaxed grid;
* cell assignment;
* borderless text tracks;
* prose rejection;
* table confidence.

**Saída:** `StructuredTable` para tabelas digitais.

### Milestone 9 — Table visual fallback

**Objetivo:** resolver tabelas que não têm geometria suficiente.

Implementar:

* `TableStructureEngine`;
* primeira engine visual;
* crop da tabela;
* estrutura predicted;
* native text → cell mapping;
* OCR → cell mapping;
* merge.

**Saída:** tabelas de scan e tabelas ruins.

### Milestone 10 — Cross Page Tables

**Objetivo:** unir fragmentos.

Implementar:

* `TableSignature`;
* header signatures;
* X tracks;
* boundary proximity;
* repeated header detection;
* continuation markers;
* row/col compatibility;
* page fragments;
* merge.

**Saída:** uma tabela lógica preservando páginas de origem.

### Milestone 11 — Document Assembly e renderers

Implementar:

* `StructuredDocument`;
* raw text;
* reading text;
* structured JSON;
* Markdown;
* repeated headers/footers policy;
* page boundaries.

### Milestone 12 — Performance e hardening

Medir:

* PDFium time;
* FFI calls;
* render time;
* layout time;
* OCR time;
* table time;
* merge time;
* peak memory.

Somente depois:

* batching;
* multiprocessing;
* model reuse;
* cache;
* Rust/PyO3 batch extraction se justificado.

---

## 41. Matriz de aderência arquitetural

A tabela abaixo não é um benchmark de qualidade. É uma avaliação arquitetural qualitativa de quão bem cada projeto cobre os problemas que nosso produto precisa resolver. Escala: 1 = fraco/não é foco, 5 = muito forte.

| Projeto | Nativo fiel | Diagnóstico adaptativo | Layout | OCR | Tabelas | Entre páginas | Explicabilidade | Fit geral para inspiração |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Docling | 4 | 4 | 5 | 4 | 5 | 3 | 3 | 5 |
| MinerU | 4 | 5 | 5 | 5 | 5 | 5 | 3 | 5 |
| LiteParse | 5 | 5 | 3 | 4 | 3 | 2 | 5 | 5 |
| Xberg | 5 | 5 | 4 | 4 | 5 | 3 | 5 | 5 |
| Unstructured | 3 | 4 | 4 | 3 | 3 | 2 | 4 | 3 |
| PaddleOCR | 1 | 3 | 5 | 5 | 5 | 4 | 3 | 4 |
| MarkItDown | 3 | 2 | 2 | 2 | 3 | 1 | 4 | 2 |
| Marker | 4 | 5 | 5 | 5 | 5 | 4 | 4 | 5 |
| **Nossa arquitetura alvo** | **5** | **5** | **5** | **5** | **5** | **5** | **5** | **5** |

A última linha representa **objetivo de design**, não capacidade já implementada.

### 41.1. Ranking por contribuição à nossa arquitetura

**LiteParse**: melhor validação do nosso core PDFium + selective OCR + complexity gate.

**Marker**: melhor padrão de decisão entre camada nativa boa, bloco ruim e OCR completo.

**Xberg**: melhores lições para ordem de leitura determinística e cascata de tabelas.

**MinerU**: melhor referência para fusão híbrida pesada e tabela entre páginas.

**PaddleOCR**: melhor candidato para a camada visual/OCR local.

**Docling**: melhor referência para organização do pipeline e modelo documental.

**Unstructured**: boa referência para estratégia automática e fallback.

**MarkItDown**: boa referência para extensibilidade, não para o núcleo PDF.

---

## 42. Avaliação com documentos reais da empresa

O usuário deste projeto explicitamente prefere validar com documentos reais em vez de montar primeiro uma suíte formal. A estratégia do MVP seguirá isso.

### 42.1. Corpus inicial

Não precisamos de milhares de PDFs. Precisamos de diversidade.

Começar com aproximadamente 50 a 100 documentos escolhidos intencionalmente.

Exemplo de composição:

| Categoria | Meta inicial |
|---|---:|
| digital simples | 10 |
| múltiplas colunas | 10 |
| fontes/Unicode problemáticos | 10 |
| tabelas digitais | 15 |
| tabelas entre páginas | 10 |
| scans | 10 |
| híbridos | 10 |
| casos conhecidos como ruins | 15 |

Um PDF pode pertencer a várias categorias.

### 42.2. Casos de ouro

Para cada problema recorrente, manter uma página ou trecho conhecido:

```text
"este parágrafo deve aparecer inteiro"
"esta coluna deve vir antes daquela"
"esta tabela possui 7 colunas"
"esta célula é R$ 1.234,56"
"a tabela continua na página 14"
"o nome contém Ç/Ã/Á"
```

Não precisa começar como teste automatizado. Pode ser um arquivo YAML de observações humanas:

```yaml
file: processo-001.pdf
page: 12
expectations:
  - contains: "Justiça"
  - contains: "São Cristóvão"
  - table_columns: 6
  - reading_order_note: "coluna esquerda antes da direita"
```

Depois esses dados podem virar regressões automáticas quando os problemas se estabilizarem.

### 42.3. Extratores a comparar

Quando puderem ser executados localmente e conforme suas licenças:

* PyMuPDF/MuPDF como referência técnica isolada;
* LiteParse;
* Docling;
* MinerU;
* Xberg;
* Marker;
* Unstructured;
* PaddleOCR para rota visual;
* MarkItDown apenas como referência simples.

### 42.4. O que observar

Não reduzir tudo a uma nota única.

Registrar pelo menos:

```text
text recall observado
texto inventado/duplicado
Unicode/acentos
ordem de leitura
tabelas detectadas
estrutura de célula
tabela entre páginas
necessidade de OCR
tempo
memória
```

### 42.5. Métricas práticas

#### Character recovery

Quando houver texto de referência disponível:

```text
CER = Character Error Rate
```

Útil especialmente para OCR.

#### Normalized text coverage

Para trechos esperados:

```text
found_expected_fragments / expected_fragments
```

#### Accent correctness

Contar divergências em palavras contendo diacríticos.

#### Reading order

Não precisamos inicialmente de métrica acadêmica complexa. Marcar página como:

```text
OK
MINOR
WRONG
```

#### Tabela

Avaliar separadamente:

```text
detectada?
número de linhas correto?
número de colunas correto?
texto nas células correto?
ordem correta?
merge entre páginas correto?
```

### 42.6. Critério de vitória contra MuPDF

Para o corpus empresarial:

* não perder casos que MuPDF extrai corretamente;
* recuperar uma parcela relevante dos casos que MuPDF perde por ausência/corrupção da camada textual;
* melhorar ordem em documentos de múltiplas colunas selecionados;
* extrair tabela de forma mais útil quando plain text do MuPDF embaralha células;
* manter desempenho aceitável no caminho nativo.

Não exigir que `balanced` seja tão rápido quanto MuPDF; exigir que `native/fast` seja competitivo e que o custo adicional tenha uma razão auditável.

---

## 43. Estratégia de comparação específica com MuPDF/PyMuPDF

### 43.1. Comparar diferentes visões

PyMuPDF possui mais de uma saída relevante.

Comparar:

```text
page.get_text("text")
page.get_text("text", sort=True)
page.get_text("words")
page.get_text("rawdict")
```

Nosso produto:

```text
raw_text
reading_text
tokens/lines
structured_document
```

### 43.2. Não copiar implementação

MuPDF/PyMuPDF continua sendo referência de comportamento e qualidade, não fonte de código.

Não copiar:

* constantes;
* thresholds;
* código;
* estruturas internas literais;
* heurísticas sob AGPL.

Usar:

* especificação PDF;
* documentação pública;
* comportamento observado;
* corpus empresarial;
* nossas próprias decisões.

### 43.3. Casos onde podemos superar

**Scans:** OCR seletivo/visual.

**Layer Unicode ruim:** OCR de região.

**Tabela:** estrutura própria em vez de plain text.

**Página híbrida:** merge por região.

**Ordem:** layout + sequência nativa extraída + geometria.

**Tabela entre páginas:** representação documental.

---

## 44. Desempenho

### 44.1. Dois budgets de desempenho

Não existe um único “tempo do parser”. Teremos caminhos diferentes.

**Native/fast:** deve permanecer próximo do custo de PDFium + heurísticas.

**Balanced:** aceita custo de layout.

**OCR/hybrid:** aceita custo maior porque há conteúdo que não seria recuperado de outra forma.

### 44.2. Medir tempo por estágio

```text
open_pdf_ms
native_extract_ms
native_reconstruct_ms
complexity_ms
render_lowres_ms
layout_ms
render_ocr_ms
ocr_ms
table_ms
fusion_ms
assemble_ms
```

### 44.3. FFI Python ↔ PDFium

Risco real do design Python.

Se cada caractere exige várias transições FFI:

```text
N chars × 5 chamadas
```

um documento grande pode acumular overhead.

Primeiro medir.

Se necessário:

```text
Python orchestrator
       ↓
Rust/PyO3 batch native extractor
       ↓
PDFium
```

### 44.4. Renderização

Evitar renderizar em alta resolução toda página antecipadamente.

Fluxo ideal:

```text
low-res para layout/ink
high-res somente páginas/regiões que precisam OCR/table visual
```

Essa ideia é semelhante ao comportamento seletivo observado em Marker.

### 44.5. Reuso de modelos

Modelos devem ser inicializados uma vez por worker/processo.

Não:

```text
page → load model → infer → unload
```

### 44.6. Batching

Layout e OCR devem aceitar batches quando a engine suportar.

### 44.7. Paralelismo

PDFium exige cautela com threads e objetos compartilhados.

Primeira estratégia de escala:

* um documento/processo ou páginas em workers independentes conforme segurança da binding;
* modelos compartilhados de forma compatível com o runtime;
* avaliar multiprocessing antes de threads para operações PDFium.

### 44.8. Meta inicial de desempenho

Não fixar um número artificial antes do corpus.

Registrar percentis:

```text
p50 ms/page
p95 ms/page
p99 ms/page
```

separados por estratégia.

---

## 45. Estimativa de esforço revisada

O novo escopo é materialmente maior que o documento original.

Uma implementação “PDFium + linhas + palavras + order” pode ser feita rapidamente. Um MVP que honestamente inclua OCR seletivo, layout, tabelas visuais e continuidade entre páginas é outro produto.

Faixa indicativa para **um desenvolvedor experiente com forte apoio de coding agent**, trabalhando iterativamente com corpus real:

| Marco | Faixa indicativa |
|---|---:|
| Baseline + native evidence | 1 a 2 semanas |
| Reconstrução + complexity | 2 a 3 semanas |
| Layout + reading order | 2 a 4 semanas |
| OCR + evidence fusion | 2 a 4 semanas |
| Table pipeline digital | 2 a 4 semanas |
| Table visual + cross page | 3 a 5 semanas |
| Hardening/performance | 2 a 4 semanas |

As etapas se sobrepõem e aprendizado do corpus muda a velocidade. Portanto não somar mecanicamente como cronograma contratual.

Uma expectativa mais realista é:

```text
Proof of concept forte:          ~4 a 6 semanas
MVP tecnicamente demonstrável:  ~8 a 12 semanas
MVP robusto para piloto interno: ~12 a 20 semanas
```

Isso é uma **estimativa de engenharia**, não compromisso de prazo.

### 45.1. Onde o risco está

Não está em “ler caracteres do PDFium”.

Está em:

```text
qualidade do gate
merge OCR/nativo
tabelas sem borda
ordem em layouts híbridos
continuidade de tabelas
edge cases de PDFs ruins
```

---

## 46. Performance versus PyMuPDF

### 46.1. Caminho nativo

É plausível que sejamos mais lentos que MuPDF inicialmente por:

* Python;
* FFI;
* mais diagnóstico;
* mais estruturas intermediárias.

Isso é aceitável se o gap for controlado.

### 46.2. Caminho balanceado

Será inevitavelmente mais lento que `page.get_text()` porque executa layout visual.

A comparação justa é qualidade/custo, não apenas velocidade.

### 46.3. Caminho OCR

Ordens de grandeza mais caro que extração nativa. Só deve ser usado quando necessário.

### 46.4. Vantagem potencial

Ao contrário de uma pipeline pesada executada sempre, nossa arquitetura pode manter um caminho rápido:

```text
PDF digital simples
  ↓
native evidence
  ↓
complexity = clean
  ↓
sem layout pesado no modo fast
  ↓
texto
```

Isso mantém escalabilidade para o caso comum.

---

## 47. Segurança, privacidade e implantação

### 47.1. Princípio

Todos os componentes padrão do MVP devem ser executáveis localmente.

Nenhum PDF empresarial deve ser enviado a serviço externo por padrão.

### 47.2. Engines remotas

Se futuramente existir:

```python
RemoteRecoveryEngine
```

ela precisa ser opt in e sujeita a política explícita.

### 47.3. Logs

Nunca registrar automaticamente:

* texto integral do documento;
* CPF;
* nomes;
* número de processo;
* conteúdo de células.

Logs operacionais usam IDs/hash e métricas.

Dumps completos só em tooling de diagnóstico controlado.

### 47.4. PDFs hostis

Defesas:

* limite de páginas;
* limite de tamanho;
* timeout;
* limite de pixels renderizados;
* captura de exceções por página;
* subprocesso opcional para isolamento no futuro.

---

## 48. Licenciamento

Esta seção não substitui avaliação jurídica.

### 48.1. Dependências que motivam a arquitetura

A intenção é evitar incorporar MuPDF/PyMuPDF no produto proprietário quando a empresa não quiser cumprir AGPL ou adquirir a licença comercial.

### 48.2. PDFium/pypdfium2

Continuam sendo a base preferida por licença permissiva do ecossistema e capacidade técnica. Verificar versões e notices antes da distribuição.

### 48.3. Projetos pesquisados

A pesquisa arquitetural **não significa que copiaremos código** deles.

Observações atuais:

* Docling: MIT no código;
* LiteParse: Apache 2.0;
* Xberg: MIT;
* Unstructured: Apache 2.0;
* PaddleOCR: Apache 2.0 para o código, com verificação separada dos pesos escolhidos;
* MarkItDown: MIT;
* Marker: código Apache 2.0; pesos têm termos próprios e precisam de análise separada;
* MinerU: licença atual baseada em Apache 2.0 com termos adicionais de uso comercial e atribuição para serviços online.

### 48.4. Regra de projeto

Antes de adicionar um modelo:

```text
1. registrar nome/versão
2. registrar URL
3. registrar licença do código
4. registrar licença dos pesos
5. registrar dependências relevantes
6. aprovação para uso empresarial
```

### 48.5. Clean room conceitual

Ao aprender com projetos permissivos ou copyleft, documentar **ideia arquitetural**, não portar heurísticas literais sem revisão de licença.

---

## 49. Principais riscos técnicos

### 49.1. PDFium não expor toda a evidência necessária

Algumas informações do PDF podem não estar disponíveis pela API textual pública da forma desejada.

Mitigação:

* usar APIs de objetos de página quando disponíveis;
* usar renderização como sensor visual;
* manter `NativeEvidenceSource` substituível;
* não acoplar algoritmo ao pypdfium2 diretamente.

### 49.2. Falso positivo de OCR

Um gate agressivo pode mandar páginas boas para OCR e piorar texto exato.

Mitigação:

* começar conservador;
* OCR seletivo;
* manter native como evidência concorrente;
* comparar divergência;
* medir por categoria.

### 49.3. Layout model errar tabela/prosa

Se tabela for classificada como texto, reading order pode corromper dados.

Mitigação:

* sinais determinísticos de grid/track podem promover região a tabela;
* table detector também roda por indícios geométricos;
* nunca depender de uma única classe do modelo.

### 49.4. Duas colunas confundirem tabela

Risco já observado em arquiteturas de mercado.

Mitigação:

* `ProseVsTableClassifier`;
* altura/comprimento das linhas;
* recorrência de tracks;
* numeric density;
* layout signal;
* isolamento do TablePipeline.

### 49.5. Tabela sem borda virar prosa

Mitigação:

* exigir recorrência em várias linhas;
* coerência de colunas;
* semântica simples de numeric tracks;
* model fallback somente quando layout também indicar tabela.

### 49.6. Tabela entre páginas ser unida erroneamente

Mitigação:

* nunca usar somente proximidade de página;
* exigir múltiplas evidências estruturais;
* preservar fragmentos mesmo quando merge ocorre;
* confidence e diagnostics.

### 49.7. OCR errar valores numéricos

Crítico em documentos empresariais.

Mitigação:

* preferir native quando válido;
* regras mais estritas de merge para números;
* guardar conflito;
* usar OCR de maior resolução em tabela quando necessário.

### 49.8. Python virar gargalo

Mitigação:

* medir por estágio;
* vectorizar geometria;
* evitar objetos Python excessivos no hot path quando necessário;
* PyO3/Rust somente depois de profiling.

### 49.9. Crescimento excessivo de escopo

Document AI completo é um problema muito maior que extração textual.

Mitigação:

O MVP não fará:

```text
image captioning
chart understanding
general VLM reasoning
semantic document QA
RAG
entity extraction
```

---

## 50. Observabilidade

### 50.1. Toda página registra decisão

Exemplo:

```json
{
  "page": 17,
  "strategy": "mixed",
  "reasons": ["embedded_images", "garbled_unicode"],
  "layout": true,
  "ocr_regions": 2,
  "page_ocr": false,
  "tables": 1,
  "native_tokens": 684,
  "ocr_tokens_added": 37,
  "conflicts": 3
}
```

### 50.2. Métricas agregadas

```text
native_pages
mixed_pages
ocr_pages
layout_pages
ocr_regions
tables_native
tables_visual
cross_page_tables
unicode_failures
conflicts
```

### 50.3. Motivo de cada fallback

Não aceitar log genérico:

```text
fallback to OCR
```

Preferir:

```text
page=17 region=table-2 OCR_REGION reason=native_text_missing visual_ink=0.42
```

---

## 51. Critérios de saída do MVP

O MVP pode ser apresentado como pronto para piloto interno quando:

1. extrair PDFs digitais comuns sem OCR;
2. preservar acentos e Unicode dos documentos selecionados;
3. resolver páginas de duas colunas do corpus sem intercalar linhas;
4. detectar quando a camada textual está ausente ou claramente ruim;
5. recuperar scans em português com qualidade útil;
6. recuperar regiões visuais sem substituir texto nativo bom no restante da página;
7. extrair tabelas com bordas em estrutura de células;
8. extrair parte significativa das tabelas sem borda representativas do corpus;
9. usar fallback visual quando a tabela determinística falhar;
10. unir os principais exemplos reais de tabela continuada entre páginas;
11. produzir `raw_text`, `reading_text` e JSON estruturado;
12. explicar por diagnóstico quais estratégias foram usadas;
13. possuir overlay que permita investigar uma página problemática;
14. ser executável inteiramente no ambiente da empresa;
15. não depender de MuPDF/PyMuPDF em runtime.

### 51.1. Gate comparativo

Sobre o corpus de aceitação:

```text
Nenhuma regressão grave sistemática contra MuPDF nos PDFs digitais simples.

Melhoria demonstrável nos casos que exigem layout, OCR ou tabela estruturada.

Erros remanescentes classificáveis por categoria e visíveis nos diagnostics.
```

---

## 52. Primeiro incremento recomendado

A primeira entrega deve deliberadamente **não** começar pelo modelo de layout.

Implementar:

```text
PDFium NativeEvidenceSource
        ↓
NativeCharacter[]
        ↓
page JSON dump
        ↓
line reconstruction
        ↓
native reading text
        ↓
visual overlay
```

Escolher cinco PDFs:

```text
1 simples
1 duas colunas
1 tabela
1 Unicode ruim
1 scan
```

No scan, o resultado vazio nesse primeiro incremento é esperado. Ele serve para validar o sinal de `NO_TEXT/SCANNED` depois.

### 52.1. Critério de conclusão

Conseguimos olhar uma página e responder exatamente:

```text
quais chars PDFium viu?
qual Unicode?
onde cada char está?
qual era sua ordem?
como nosso LineDetector os agrupou?
```

---

## 53. Segundo incremento recomendado

Adicionar `ComplexityAnalyzer` antes de OCR.

Trabalhar com casos reais:

```text
scan
camada duplicada
camada Unicode ruim
imagem com texto
```

Implementar somente sinais determinísticos.

Resultado esperado:

```text
página 1 clean
página 2 scanned
página 3 garbled
página 4 embedded_images
```

Não fazer OCR ainda.

---

## 54. Terceiro incremento recomendado

Adicionar layout low-res e separar regiões.

Objetivo:

```text
prosa ≠ tabela
```

Usar primeiro os PDFs de duas colunas e tabelas.

Só depois ligar XY-cut/reading order por região.

---

## 55. Quarto incremento recomendado

Adicionar PaddleOCR para scans inteiros.

Não implementar merge complexo primeiro.

Objetivo:

```text
scan → OCR tokens → reading text
```

Validar português e coordenadas.

---

## 56. Quinto incremento recomendado

OCR por região + evidence fusion.

Usar páginas híbridas reais.

Validar:

```text
texto native bom permanece idêntico
texto visual ausente é adicionado
não surgem duplicatas
```

---

## 57. Sexto incremento recomendado

Tabela digital.

Começar por bordas explícitas.

Depois:

```text
relaxed borders
borderless tracks
```

Evitar table model enquanto ainda não conseguimos inspecionar bem o grid determinístico.

---

## 58. Sétimo incremento recomendado

Table structure model + OCR por célula/região.

Somente agora adicionar a parte visual pesada das tabelas.

---

## 59. Oitavo incremento recomendado

Cross page table resolver.

Começar pelos exemplos reais da empresa, porque as heurísticas de continuidade são altamente dependentes do tipo de documento.

Construir regra genérica a partir dos sinais, não a partir de nomes fixos de documentos.

---

## 60. Decisões que não devemos tomar cedo

### 60.1. “Todo PDF complexo vai para OCR”

Errado. Pode perder texto exato e aumentar custo.

### 60.2. “Layout model sempre define ordem”

Errado. Sequência nativa extraída pode ser superior em muitos PDFs digitais.

### 60.3. “XY-cut resolve tabela e coluna”

Errado. Há evidência prática de corrupção de tabela quando o mesmo mecanismo tenta resolver ambos.

### 60.4. “PaddleOCR será nossa arquitetura”

Errado. É uma engine dentro da arquitetura.

### 60.5. “Markdown é o produto”

Errado. Markdown perde informação estrutural e de proveniência.

### 60.6. “Um score de qualidade basta”

Errado. Precisamos das razões.

### 60.7. “Se OCR concorda parcialmente, ele está certo”

Errado, especialmente em números.

### 60.8. “Header/footer deve ser removido”

Não da evidência. No máximo de uma renderização de leitura.

### 60.9. “Implementar tudo em Rust agora”

Prematuro. Python + engines nativas oferece velocidade de desenvolvimento e performance suficiente para medir o problema real.

---

## 61. Roadmap depois do MVP

### Fase 1 — Qualidade

* refinar classifier de complexidade;
* melhor region quality;
* melhores regras para documentos jurídicos/administrativos sem acoplamento ao domínio;
* segundo OCR engine opcional para divergências críticas.

### Fase 2 — Performance

* batch native extraction;
* PyO3/Rust se necessário;
* batching de modelos;
* cache;
* workers persistentes.

### Fase 3 — Estrutura

* listas;
* headings;
* forms/key-value;
* footnotes;
* melhor paragraph continuation.

### Fase 4 — Multimodal opcional

* fórmulas;
* charts;
* image text/description;
* VLM somente para regiões não resolvidas.

---

## 62. Recomendação final para apresentação à empresa

A proposta não deve ser apresentada como “vamos reimplementar PyMuPDF”.

A formulação correta é:

> **Construir um engine próprio de extração robusta de PDF, com backend permissivamente licenciado, que preserve texto digital exato quando ele existe e utilize layout e OCR de forma seletiva para recuperar conteúdo que parsers tradicionais perdem.**

A escolha inicial de PDFium permanece válida e foi reforçada pelo estudo dos parsers atuais. O que muda é a camada acima dele.

A arquitetura final do MVP é deliberadamente híbrida:

```text
PDFium                  → verdade nativa quando confiável
Layout detector         → entende regiões
Quality gate            → decide onde confiar
OCR                     → recupera o que não está disponível nativamente
Table pipeline          → evita tratar tabela como prosa
Evidence fusion         → escolhe sem apagar alternativas
Document assembler      → resolve relações entre páginas
```

O diferencial não será possuir “mais uma heurística de texto”. O diferencial será **orquestrar múltiplas evidências sem sacrificar o conteúdo correto que já estava no PDF**.

Isso também cria um caminho incremental seguro:

```text
primeiro somos bons em texto nativo
        ↓
adicionamos diagnóstico
        ↓
adicionamos layout
        ↓
adicionamos OCR seletivo
        ↓
adicionamos tabelas
        ↓
adicionamos continuidade entre páginas
```

Cada etapa melhora a cobertura sem obrigar a substituir o núcleo anterior.

---

## 63. Fontes técnicas consultadas nesta revisão

### Docling

* Repositório: <https://github.com/docling-project/docling>
* `docling/pipeline/standard_pdf_pipeline.py`
* `docling/backend/pypdfium2_backend.py`
* documentação de pipeline options: <https://docling-project.github.io/docling/reference/pipeline_options/>
* commit analisado no GitHub durante esta revisão: `5ea6490ffdc57b2fd7de5cc436f2d0a22f2214d4`

### MinerU

* Repositório: <https://github.com/opendatalab/MinerU>
* `mineru/backend/hybrid/hybrid_analyze.py`
* `mineru/backend/utils/runtime_utils.py`
* `mineru/utils/table_merge.py`
* licença atual: `LICENSE.md`
* commit analisado: `4fe4bde114a23ee5dd637eae99b767f4669bf58c`

### LiteParse

* Repositório: <https://github.com/run-llama/liteparse>
* `crates/liteparse/src/ocr_merge.rs`
* `crates/liteparse/src/projection.rs`
* `crates/liteparse/src/extract.rs`
* commit analisado: `c999b5302ac903e8bed5ce047ac4a0122cd869b1`

### Xberg

* Repositório: <https://github.com/xberg-io/xberg>
* `crates/xberg/src/extractors/pdf/mod.rs`
* `crates/xberg-native-pdf/src/pipeline/reading_order/xycut.rs`
* `crates/xberg/src/pdf/native/table.rs`
* documentação de layout: <https://docs.xberg.io/guides/layout-detection/>
* commit analisado: `606fa72230058da9b9d0a16e5c999b38b92dc847`

### Unstructured

* Repositório: <https://github.com/Unstructured-IO/unstructured>
* `unstructured/partition/pdf.py`
* `LICENSE.md`
* commit analisado: `ee2b3a350d314a2a4e0cb7dfe6e34a1d92fd426d`

### PaddleOCR

* Repositório: <https://github.com/PaddlePaddle/PaddleOCR>
* PP-StructureV3 documentation/source
* Table Recognition v2 documentation
* multilingual recognition documentation
* commit analisado: `2661c7c0ef5c613e8f93c6e93b2e052399f0f854`

### MarkItDown

* Repositório: <https://github.com/microsoft/markitdown>
* `packages/markitdown/src/markitdown/converters/_pdf_converter.py`
* `LICENSE`
* commit analisado: `5640da7142fb546da0ef712093f3e29d87c62e0b`

### Marker

* Repositório: <https://github.com/datalab-to/marker>
* `marker/converters/pdf.py`
* `marker/builders/document.py`
* `marker/builders/line.py`
* README do projeto e documentação dos modos `fast`/`balanced`
* commit analisado: `f6b072ad46a79026ee75d1777c4e3e798a2712a5`

### Referências anteriores

* PyMuPDF: <https://github.com/pymupdf/PyMuPDF>
* MuPDF: <https://github.com/ArtifexSoftware/mupdf>
* pypdfium2: <https://github.com/pypdfium2-team/pypdfium2>
* PDFium public text API: <https://pdfium.googlesource.com/pdfium/>
* PDFium structure tree API: `public/fpdf_structtree.h`

---

## 64. Registro da decisão arquitetural

**Decisão:** substituir a arquitetura linear `PDFium → reconstrução → OCR fallback` por um **Hybrid Evidence Fusion Pipeline**.

**Mantido:** PDFium/pypdfium2 como backend primário, Python para o MVP, reconstrução própria, estrutura interna independente.

**Adicionado ao MVP:**

* complexity gate cedo;
* layout regions;
* quality gate por região;
* OCR seletivo;
* fusão explícita nativo/OCR;
* table pipeline separado;
* table model fallback;
* tabelas entre páginas;
* proveniência e conflito por evidência.

**Removido como premissa:**

* XY-cut sobre a página inteira;
* OCR apenas como fallback final por página;
* tabela como preocupação pós-MVP;
* `QualityAnalyzer` somente após reconstrução completa.

**Justificativa:** o conjunto de arquiteturas atuais mais robustas converge para pipelines adaptativos e region-aware. A análise de código mostrou ainda riscos concretos de aplicar a mesma heurística espacial a prosa e tabelas. A arquitetura revisada maximiza a capacidade de recuperação sem tornar OCR ou modelo visual a fonte principal para PDFs digitais corretos.

---

**Fim do documento.**
