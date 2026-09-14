# Corpus local

Coloque aqui PDFs reais usados para validação manual do extrator. Os arquivos
PDF são ignorados pelo Git por padrão.

Sugestão de organização:

```text
corpus/
├── digital-simple/
├── digital-multicolumn/
├── digital-bad-font/
├── duplicate-ocr-layer/
├── hybrid-page/
├── scan-clean/
├── scan-poor/
├── table-bordered/
├── table-borderless/
├── table-cross-page/
├── rotated/
└── legacy-system/
```

Para a entrega atual, o foco é observar a evidência nativa:

```bash
PYTHONPATH=src python3 -m structured_pdf_text.cli inspect \
  corpus/digital-simple/exemplo.pdf --page 1 --raw-page-json

PYTHONPATH=src python3 -m structured_pdf_text.cli extract \
  corpus/digital-simple/exemplo.pdf --output reading
```

O dump bruto mostra os caracteres na sequência exposta pelo PDFium, Unicode,
coordenadas, origem, fonte, tamanho, ângulo, flags, imagens, paths,
anotações, caixas da página e capacidades disponíveis.
