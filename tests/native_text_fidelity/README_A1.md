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
