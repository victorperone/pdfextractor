# A1 — inventário auditável da evidência textual nativa

Esta suíte mede somente a etapa anterior à classificação, montagem, OCR,
supressão de repetidos, tabelas, ordem de leitura e renderização. Ela mantém
duas trilhas:

1. `raw_pdfium`: chamadas diretas ao PDFium/pypdfium2, antes do adaptador de
   produção.
2. `native_adapter`: objetos reais retornados por
   `PdfiumNativeEvidenceSource.extract_page(page_index)`, com índice 0-based.

Assim, `reference -> raw_pdfium` não é atribuído ao adaptador. Uma divergência
em `raw_pdfium -> native_adapter` é a única que pode ser atribuída à conversão
do adaptador, e ainda deve ser confirmada pelos campos e flags capturados.

## Comandos

Validação inicial do corpus:

```bash
python tests/corpus/native_text_stress/validate_native_text_stress.py \
  --root tests/corpus/native_text_stress
pytest -q tests/test_native_text_stress_fixture.py
```

Uma página deterministicamente escolhida por família (20 páginas):

```bash
python tests/native_text_fidelity/a1_capture.py --variants-per-family 1
```

Três variantes deterministicamente espaçadas por família (60 páginas):

```bash
python tests/native_text_fidelity/a1_capture.py --variants-per-family 3
```

Corpus completo:

```bash
python tests/native_text_fidelity/a1_capture.py --all
```

Os artefatos ficam em `output/native_text_fidelity/a1/<codigo_sha>/`, que é
ignorado pelo Git:

- `run_metadata.json`: branch, SHA do código, data efetiva, hashes, versões,
  páginas selecionadas, opções e política;
- `raw_pdfium_chars.jsonl`: uma linha por índice de caractere do PDFium, com
  texto, Unicode, bbox original e bbox convertida, origem, ângulo e erros;
- `raw_pdfium_pages.jsonl`: geometria, rotação, contagem e `get_text_range()`
  agregado para inspeção, sem tratá-lo como fonte independente;
- `native_adapter_chars.jsonl` e `native_adapter_pages.jsonl`: evidência do
  adaptador sem remover índices, bboxes, IDs ou flags inválidos;
- `unit_alignment.jsonl`: alinhamento por ocorrência, cardinalidade um-para-um,
  candidatos, caracteres consumidos, categoria e razões;
- `page_summary.json`: contagens por página/estágio e numeradores/denominadores
  estritos e permissivos;
- `A1_report.md`: exemplos reproduzíveis e limites.

## Decisões de comparação

O `exact_text` é comparado literalmente primeiro. Uma segunda visão somente
diagnóstica converte whitespace Unicode para um espaço ASCII e usa NFKC para
identificar possíveis ligaturas/substituições; números, IDs e pontuação não
são normalizados. Duas ocorrências iguais continuam sendo duas ocorrências e
um caractere consumido não pode ser reutilizado.

As bboxes da referência são estimativas de métricas da fonte. A associação usa
proximidade/overlap com tolerância proporcional ao tamanho da fonte, sem exigir
igualdade da bbox. Texto rotacionado é avaliado no quadro top-origin canônico;
ordem de desenho e `logical_reading_order` não participam do veredito A1.

`not_assessable` fica fora do denominador. `unmatched_native` é mantido como
evidência de caracteres sem unidade correspondente e não conta como sucesso.
Whitespace alterado é `spacing_changed`, e não perda automática de caractere
não-espaço. A referência nunca é usada como saída do parser nem como fonte de
captura.

## Campos nativos e geometria

O `NativeCharacter` expõe: `page_index`, `char_index`, `text`,
`unicode_codepoint`, `bbox`, `origin`, `angle`, `font_name`, `font_size`,
`font_weight`, `fill_color`, `stroke_color`, `text_render_mode`,
`marked_content_id`, `generated`, `hyphen`, `unicode_mapping_failed` e
`visible_candidate`. O `NativePageEvidence` expõe `page_index`, `bbox`,
`characters`, `objects`, `extracted_text` e `capabilities`.

O adaptador aplica `BBox.from_pdfium_rect`: subtrai a origem x/y do quadro
efetivo, inverte y dentro da altura efetiva e retorna a bbox em top-origin.
A captura bruta repete apenas essa conversão matemática para registrar também
os valores PDFium originais; ela não chama os helpers privados do adaptador.

## Limites

O benchmark mede evidência textual nativa, não garante que a intenção do
gerador represente a tinta real, nem aprova 100% de texto/tabelas. `get_text_range`
e `get_text_bounded` seriam visões da mesma biblioteca e não são tratados como
fontes independentes. OCR, rasterização, assembler, conservação, ordem lógica
e estrutura de tabela ficam para etapas posteriores.

## A2 — auditoria de conservação IR

O primeiro incremento A2 está em `a2_conservation.py`. Ele é um avaliador
independente e não altera os blocos: confere ownership exatamente uma vez,
supressões explícitas, fontes transformadas (`DEDUPLICATED`), reivindicações
órfãs, conteúdo não contabilizado e linhas em branco não avaliáveis. Os testes
em `test_native_text_a2.py` cobrem esses eventos sem depender do corpus PDF.
O ledger de produção continua sendo o mecanismo que decide/reconstrói blocos;
este módulo verifica se sua saída é auditável. `audit_document_conservation`
agrega páginas já montadas e consome os fatos de fontes transformadas expostos
em `PageDiagnostics`, permitindo uma verificação documental sem rerodar
decisões do assembler.

Para executar a auditoria sobre a saída montada do extrator, sem OCR, layout
ou tabelas:

```bash
python tests/native_text_fidelity/a2_evaluate.py \
  tests/corpus/native_text_stress/Document_Text_Stress_V1.pdf \
  --output /tmp/pdfextractor-a2
```

O comando produz `a2_metadata.json`, `a2_summary.json` e
`a2_findings.jsonl` fora do Git.

## B1 — auditoria estrutural

O auditor B1 compara unidades por ocorrência, verifica se regiões de referência
foram fragmentadas, avalia a monotonicidade da ordem lógica e confere a forma
de células (`row`, `col`, `rowspan`, `colspan`) sem confundir `source_draw_order`
com reading order. Cabeçalhos e rodapés repetidos ficam fora da comparação de
ordem de conteúdo, pois são furniture explícito da página. Para executar no corpus:

```bash
python tests/native_text_fidelity/b1_evaluate.py \
  tests/corpus/native_text_stress/Document_Text_Stress_V1.pdf \
  tests/corpus/native_text_stress/Document_Text_Stress_V1.reference.json \
  --output /tmp/pdfextractor-b1
```

O resultado produz `b1_metadata.json`, `b1_summary.json`, `b1_findings.jsonl`
e `b1_report.md`. O relatório separa texto presente porém fora da geometria,
ausência textual ainda não resolvida e partições semânticas não bloqueantes.
`table_missing` e `table_cell_mismatch` são achados estruturais; não são
convertidos em perda de texto A1.

Quando uma região de referência é coberta por regiões observadas de tipos
semânticos diferentes (por exemplo, `title` e `text`), o resultado é registrado
como `region_partitioned`. A partição continua visível na auditoria e preserva
os IDs e tipos observados, mas não é confundida com um split estrutural de
regiões do mesmo tipo, que permanece `region_fragmented` e bloqueia o gate B1.
Na montagem das regiões, caixas fortemente aninhadas do mesmo tipo e com o
mesmo papel semântico são coalescidas; caixas adjacentes ou com papéis
semânticos diferentes continuam independentes.
Na ordem global, unidades `edge_*`, `rotation_*`, `vertical_*` e unidades de
célula/cabeçalho de tabela não são comparadas como linhas corridas: bordas e
rótulos têm fluxo geométrico próprio, enquanto tabelas são avaliadas pela
forma das células e pela associação de conteúdo.

Quando o texto exato existe, mas a ocorrência escolhida está distante da bbox
de referência, o auditor registra `unit_geometry_mismatch`; isso não é contado
como `unit_missing`, mas impede o gate estrutural. A associação continua por
ocorrência e proximidade, sem aceitar a referência como saída do extrator.

Quando uma unidade não existe como linha isolada, o auditor tenta uma
reconstrução conservadora por tokens nativos dentro da bbox de referência.
Isso cobre pontuação emitida em linha separada, quebras de linha e células de
tabela que compartilham uma linha com outro campo. O resultado é registrado
como `unit_fragmented`, preservando o texto observado e sem relaxar a igualdade
de conteúdo. Quando dois campos compartilham a mesma linha, o consumo é
controlado por índice de token; um campo não pode consumir novamente os tokens
já associados a outro.

Substituições exclusivamente compatíveis de ligaturas (`ﬁ`, `ﬂ`, `ﬀ`) são
registradas como `unit_unicode_substitution`. Essa categoria preserva a
diferença observada e não trata a unidade como texto exato; a normalização não
é aplicada a identificadores, números ou pontuação.

O detector de tabelas nativas aceita tanto segmentos finos quanto caminhos
retangulares repetidos que representam contornos de células. Em tabelas
borderless, linhas nativas com a mesma linha de base geométrica são agrupadas
antes da inferência das trilhas. Fragmentos de tabelas continuadas têm seus
índices de linha normalizados apenas para a forma relativa da página; isso não
altera a evidência nem a montagem de produção.

Na reconstrução nativa horizontal, a linha é ancorada pela borda inferior das
caixas dos glifos. Assim, ascendentes, acentos e descendentes permanecem na
mesma linha visual sem perder a separação geométrica entre colunas.
Na verificação de ordem, uma unidade reconstruída a partir de uma linha que
mistura colunas usa as caixas dos tokens consumidos, e não a caixa ampla da
linha nativa; isso evita atribuir à coluna errada uma ocorrência que foi
associada corretamente por geometria de tokens.
