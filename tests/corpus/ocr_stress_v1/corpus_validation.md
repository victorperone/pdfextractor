# Validação do corpus OCR Stress V1

## Escopo e identidade

- Branch: `feat/ocr-adaptive-quality`
- `HEAD`: `f18a03149c251a8b48b26c251484db642027ee02`
- Commit de referência do corpus: `f18a03149c251a8b48b26c251484db642027ee02`
- Arquivos alterados nesta revisão: gerador, manifesto e teste de integridade do corpus.
- Arquivos em `src/structured_pdf_text`: nenhum.
- Modelos OCR carregados pelo gerador: não.
- Parser de produção importado pelo gerador: não.
- Nenhum commit ou push foi feito nesta revisão.

O SHA acima é o commit de base; as alterações descritas neste relatório estão
no working tree para revisão.

## Auditoria estrutural

O gerador foi executado para as 60 páginas A–E e para os subconjuntos exigidos.
O PDF completo atual é:

- arquivo: `tests/corpus/ocr_stress_v1/outputs/Document_OCR_Stress_V1.pdf`
- SHA-256: `f335b2ab558f2c640c0247ddede2c8e5be0b310bbe52639e3fda40fa64117db1`
- manifesto: `tests/corpus/ocr_stress_v1/manifest.json`
- SHA-256 do manifesto: `f07e6ff08c864944da65d520b694df1812443496783652fe16f99e928a9001f1`
- páginas: 60, na ordem `1..60`

O manifesto confirma os cinco blocos do corpus, as camadas nativas, regiões
raster, tabelas, figuras, orientação e os pares de continuação. As páginas
raster não têm texto selecionável e as páginas mistas preservam sua camada
nativa intencional.

As continuações foram geradas e verificadas juntas: 15–16, 36–37, 38–39 e
56–57. Em 15–16, o manifesto agora registra `shared_image_id` e os recortes
`left`/`right` de uma única imagem-fonte; o teste de integridade cobre essa
invariante.

Controles específicos verificados:

- 19: texto nativo e figura decorativa sem texto;
- 24: texto nativo, região raster textual e gráfico;
- 48: texto nativo e múltiplas imagens;
- 50: figura totalmente fora da área visível, registrada como `off_page`;
- 53: página somente digital entre páginas que exigem OCR.

As páginas de amostra 2, 13, 15–16, 27, 33, 38–39, 42–44, 46–50 e 56–60
foram renderizadas e inspecionadas. A inspeção de objetos confirmou a
combinação esperada de objetos de texto, caminhos e imagens, incluindo páginas
com tabelas raster, orientação paisagem, texto girado e regiões parcialmente
fora da página.

Os QR codes das páginas 46, 58 e 60 foram decodificados localmente com os
valores declarados no manifesto. O Code128 das páginas 47 e 58 foi gerado pelo
ReportLab e declarado válido pelo gerador, mas não foi decodificado localmente
porque não há decodificador de barras instalado no ambiente.

## Dependências e comandos

Versões observadas: ReportLab 5.0.1, Pillow 10.3.0, pypdfium2 5.13.0,
PaddlePaddle 3.3.1 e PaddleOCR 3.7.0. O gerador usa somente ReportLab, Pillow
e pypdfium2; nenhuma dependência foi instalada.

Comandos executados com sucesso:

```bash
python3 -m compileall -q src tests
pytest -q tests/test_ocr_stress_corpus.py
pytest -q
git diff --check
python3 tests/corpus/ocr_stress_v1/generate.py
```

Também foram gerados, em `/tmp/ocr-stress-audit/subsets/`, os subconjuntos:

```text
p2
p13-14
p15-16
tables (25, 27, 28)
p36-37
p38-39
p46-50 (46, 47, 49, 50)
p56-60 (56, 57, 58, 60)
```

Os nomes efetivos dos PDFs e os manifestos desses lotes preservam `source_page`
para comparação com o corpus completo.

## Baseline OCR curto

Foi executado o modo `balanced` com a política `baseline`, sem alterar o
código de OCR, nos controles 1, 2, 13, 15–16, 19, 27, 42, 46, 49, 50 e 53.
Os comandos usaram `PYTHONPATH=src` e salvaram Markdown, log e estatísticas de
`/usr/bin/time` em `/tmp/ocr-stress-audit/`.

Resultados observados:

- página 1 e páginas 13, 19, 46, 49, 50 e 53 preservaram a camada nativa;
- página 2 recuperou texto raster com acentos, moeda, data e percentual;
- páginas 15–16 recuperaram os marcadores e o conteúdo comum da figura contínua;
- página 27 recuperou a tabela raster com células e cabeçalho;
- página 42 recuperou texto raster em paisagem;
- o lote 46–50 preservou QR/rótulo, código/rótulo, imagem parcial e a figura
  totalmente externa sem inventar texto OCR para a região invisível.

Tempos de parede registrados: p1 0,53 s; p2 23,01 s; p13–14 22,48 s;
p15–16 36,90 s; p19 0,27 s; p27 21,42 s; p42 22,94 s; p46–50 10,75 s;
p53 0,29 s. Os logs não apresentaram erro de execução; avisos de ambiente
como ausência de `ccache` e cache temporário do Matplotlib não interromperam
as extrações.

## Limitações e decisão de escopo

- A inspeção visual foi amostral, não uma alegação de revisão visual das 60
  páginas.
- O baseline OCR foi curto e controlado; não foram executadas todas as
  políticas em todas as páginas.
- A validação local de Code128 ficou limitada à geração e à geometria porque o
  ambiente não possui decodificador de código de barras.
- Nenhuma alteração foi feita em reconstrução nativa, montagem textual, ordem
  de leitura, deduplicação, ledger, tabelas ou OCR de produção.

O corpus e sua infraestrutura estão prontos para a próxima etapa. A alteração
de produção deve começar somente após a aprovação responsável deste relatório;
depois disso, o próximo passo é registrar cada falha reproduzida no corpus e
aplicar uma correção OCR isolada por vez, com comparação contra este baseline.
