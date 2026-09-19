# Document_Text_Stress_V1 — corpus textual digital (PDFExtractor)

## Propósito e limites

Este conjunto foi gerado para a nova branch `feat/native-text-fidelity`, sem mudar o parser nem o OCR. O PDF contém **texto digital nativo**, não páginas rasterizadas. O objetivo é determinar em que estágio uma ocorrência textual se perde ou muda de contexto: captura nativa, IR/conservação, estrutura (tabelas/colunas), renderer.

**Não há promessa de que todas as features possíveis do formato PDF estejam cobertas.** A V1 contém 200 páginas, com 20 famílias de desafios e 10 variantes determinísticas por família. Há repetições de cabeçalhos e rodapés em todas as páginas, texto em bordas, texto rotacionado, fontes e tamanhos diferentes, Unicode/identificadores, listas, hierarquia, parágrafos, 2–3 colunas, colunas assimétricas, sidebars, tabelas com/sem bordas, células mescladas, valores financeiros, continuação em duas páginas e cenários combinados. Algumas páginas invertem deliberadamente a ordem do desenho no PDF em relação à leitura lógica. OCR fica **intocado** neste projeto: os testes atuais de OCR devem continuar passando, e documentos com imagens terão uma suíte própria depois.

### Arquivos

- `Document_Text_Stress_V1.pdf`: PDF de 200 páginas, nativo, para avaliação.
- `Document_Text_Stress_V1.reference.json`: referência consolidada, contendo intenção de geração por página: regiões, unidades textuais, ordem lógica, fontes, coordenadas estimadas e células de tabelas.
- `Document_Text_Stress_V1.reference.jsonl`: os mesmos dados, uma página por linha, para processamento em streaming.
- `Document_Text_Stress_V1.manifest.json`: contagens, famílias, versão do schema, seed e SHA-256 do PDF e das referências.
- `Document_Text_Stress_V1_generator.py`: gerador reproduzível **somente para testes**, requer ReportLab e fontes DejaVu instaladas localmente. Não empacotar nem distribuir arquivos de fonte.
- `validate_native_text_stress.py`: verificador da integridade dos artefatos, independente de PDFExtractor; usa pypdfium2 para verificar páginas e marcadores digitais sem OCR.
- `test_native_text_stress_fixture.py`: testes pytest da integridade do corpus, antes de testar o parser.

### Contrato de referência

Cada registro de página tem `units`, `regions`, `tables`. Cada unidade tem `unit_id`, `page`, `exact_text`, `source_kind`, `visible`, `region_id`, `role`, `source_draw_order`, `logical_reading_order`, `bbox_top_origin_pt`, `font_family`, `font_size_pt`, `rotation_deg`, `table_id`, `cell_id`. A tabela declara `row_index`, `column_index`, `rowspan`, `colspan`, `exact_text` e `text_unit_ids` por célula. `logical_reading_order` foi definido pelo gerador, NÃO inferido da ordem interna do PDF.

`bbox_top_origin_pt` das unidades é uma estimativa via métricas de fonte (não a medição de tinta visível). Considere tolerância geométrica; em contrapartida, o texto/célula e a identidade da ocorrência devem ser validados de forma estrita. O `exact_text` da célula é o texto que o autor quis desenhar; seus `text_unit_ids` registram a quebra visual efetivamente desenhada. Diferencie igualdade literal e normalização permissiva (ligaturas, Unicode, espaços e hifenização), relatando ambas; **nunca normalizar números/IDs de forma que erros desapareçam**. Nem toda sequência visual de linhas é uma sentença/linha nativa única do PDFium.

`is_repeated_header` identifica a cópia do cabeçalho na segunda página de uma tabela; não significa que ela deva desaparecer no teste de conservação. Não ligar supressão de cabeçalhos/rodapés durante as primeiras baterias.

### Validação do corpus (antes de testar o parser)

```bash
python validate_native_text_stress.py --root .
pytest -q test_native_text_stress_fixture.py
```

A validação deve confirmar páginas, unicidade e cobertura dos IDs, associações de células e hashes. Também deve encontrar um `CASE-NNN` distinto no texto nativo de cada página. **Isso não prova que todo o texto foi extraído pelo PDFium ou pelo PDFExtractor**; o teste real do parser vem depois.

### Etapas de desenvolvimento (gate independente por fase)

1. **Captura nativa:** extrair caracteres/linhas da camada PDF sem usar OCR para mascarar perdas. Comparar unidades de referência por página e geometria; listar missing, extra, duplicates e transformações Unicode. Analisar separadamente falhas de Unicode da fonte, sem apagar casos adversariais do benchmark. `source_draw_order` não é reading order.
2. **IR e conservação:** cada ocorrência capturada deve estar renderizada/owned por tabela/figura ou suprimida explicitamente com ledger auditável. Verificar `content_unaccounted_lines`, duplicates e lineage; uma página pode estar completa em caracteres e ainda falhar nesta etapa.
3. **Estrutura:** validar células por `(table_id, page, row_index, column_index, rowspan, colspan)`, headings/listas, footnotes, sidebars e `logical_reading_order`. Requer referência estruturada/JSON, não somente Markdown. Uma tabela completa mas com linhas/colunas trocadas falha.
4. **Serialização/integração:** validar JSON e Markdown sem perder/duplicar texto. Rodar `balanced` sem omitir repetidos. Testar separadamente efeitos de classificação, fallback e eventuais complementos OCR.
5. **Repetição e normalização** ficam para bateria própria posterior. Não usar o PDF/JSON como lookup em produção, nem adaptar regras do parser aos seus textos, IDs, coordenadas ou páginas.

### Desenvolvimento inicial na branch já criada

A branch `feat/native-text-fidelity` já foi criada a partir de `feat/ocr-adaptive-quality`. Confirme:

```bash
git branch --show-current
git status --short
git rev-parse HEAD
```

Sugestão de organização dos arquivos no repositório:

```text
tests/corpus/native_text_stress/
    Document_Text_Stress_V1.pdf
    Document_Text_Stress_V1.reference.json
    Document_Text_Stress_V1.reference.jsonl
    Document_Text_Stress_V1.manifest.json
    Document_Text_Stress_V1_generator.py
    validate_native_text_stress.py
    README_NATIVE_TEXT_STRESS.md

tests/test_native_text_stress_fixture.py
```

1. Copiar os artefatos; executar `python tests/corpus/native_text_stress/validate_native_text_stress.py --root tests/corpus/native_text_stress`.
2. Executar `pytest -q tests/test_native_text_stress_fixture.py` e `pytest -q`.
3. Fazer **primeiro commit apenas com corpus/avaliação**, sem mudar o parser: `test(corpus): add deterministic native text fidelity benchmark`.
4. Na próxima rodada, implementar medições por etapa, começando por uma seleção pequena de páginas, e somente depois executar as 200. O benchmark de 200 páginas não substitui os testes OCR já existentes.
5. Guardar o SHA do código, hashes dos fixtures, data real e parâmetros de execução no relatório.

### Limitações conhecidas da V1 e evolução futura

O PDF foi criado por um mecanismo independente do PDFExtractor (ReportLab) e pré-verificado pelo PDFium, mas **somente um gerador de PDF** foi usado nesta versão. Depois da primeira rodada, adicionar um segundo backend com referência gerada antes do PDF (por exemplo, um fluxo HTML/Chromium ou outra ferramenta de layout), páginas com CJK/RTL usando shaping/bidi corretos, conteúdo opcional, clipping, anotações/AcroForm, transparência e sobreposições deliberadas. Essas features exigem critérios de visibilidade separados para evitar que uma referência inválida seja tratada como falha do parser. Esta V1 não testa imagens rasterizadas nem OCR; a arquitetura OCR deve ser preservada.
