# Document_OCR_Stress_V1

Corpus sintético modular de 60 páginas para inspeção de OCR, geometria,
regiões raster, tabelas e integração. Todo o conteúdo é fictício e gerado
localmente; não há documentos empresariais, imagens baixadas ou fontes
comerciais no corpus.

Esta entrega contém somente o gerador, o manifesto e os testes de integridade.
Ela não altera `src/structured_pdf_text`, não carrega modelos OCR e não tenta
corrigir defeitos do parser encontrados durante a inspeção.

## Dependências

O gerador usa as dependências já previstas no projeto:

- ReportLab para criar a camada PDF e QR/Code128;
- Pillow para transformações determinísticas;
- pypdfium2 para rasterizar temporariamente o conteúdo das imagens e validar o
  PDF final.

Nenhuma dependência é instalada automaticamente. O gerador não depende de
fontes externas: o texto-base usa as fontes PDF14 do ReportLab.

## Geração no WSL

Na raiz do repositório:

```bash
python3 tests/corpus/ocr_stress_v1/generate.py
```

Isso cria:

- `manifest.json` e `scenarios.md` ao lado do gerador;
- `outputs/Document_OCR_Stress_V1.pdf`, ignorado pelo Git.

O PDF completo tem exatamente 60 páginas. O manifesto registra o SHA do PDF,
as dimensões, o bloco, a presença de texto nativo, regiões raster, tabelas,
figuras, continuações e indicadores fictícios esperados.

## Subconjuntos

Selecione páginas por número, lista ou intervalo. O manifesto do subconjunto
preserva `source_page` para relacionar cada página gerada à página original:

```bash
python3 tests/corpus/ocr_stress_v1/generate.py --pages 15-16
python3 tests/corpus/ocr_stress_v1/generate.py --pages 36-37 38-39
python3 tests/corpus/ocr_stress_v1/generate.py --pages 56-57 --output-dir /tmp/ocr-stress-v1
```

As continuações 15–16, 36–37, 38–39 e 56–57 devem ser executadas juntas
quando o objetivo for avaliar continuidade. Um subconjunto é um novo PDF e
pode ter referências internas diferentes do PDF completo.

## Testes de integridade

Os testes não carregam PaddleOCR, PaddlePaddle ou qualquer modelo:

```bash
pytest -q tests/test_ocr_stress_corpus.py
```

Eles geram cópias temporárias, conferem 60 páginas e sua ordem, dimensões,
hashes, marcadores, presença/ausência de texto selecionável, objetos de imagem,
subconjuntos, continuações e renderização em baixa resolução.

Para uma validação manual independente do parser:

```bash
python3 - <<'PY'
from pathlib import Path
import pypdfium2 as pdfium

pdf = pdfium.PdfDocument(Path("tests/corpus/ocr_stress_v1/outputs/Document_OCR_Stress_V1.pdf"))
print("pages:", len(pdf))
PY
```

Depois da revisão visual, o PDF pode ser usado em invocações separadas do
PDFExtractor. Registrar SHA do código, páginas, modo, política OCR, threads,
tempo, memória, exit code e observações; não transformar os indicadores do
manifesto em regras de produção.
