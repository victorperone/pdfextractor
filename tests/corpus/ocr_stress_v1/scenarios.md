# Document_OCR_Stress_V1 — cenários

Corpus sintético determinístico de 60 páginas. Todos os nomes, códigos, valores e imagens são fictícios.
O manifesto é a referência executável; esta tabela é somente um mapa para inspeção humana.

| Página | Bloco | Cenário | Foco de inspeção |
|---:|:---:|---|---|
| 1 | A | texto digital de controle | texto nativo — OCR básico |
| 2 | A | raster simples legível | OCR/raster/misto — OCR básico |
| 3 | A | raster em resolução moderada | OCR/raster/misto — OCR básico |
| 4 | A | raster de baixa resolução | OCR/raster/misto — OCR básico |
| 5 | A | raster com fonte pequena | OCR/raster/misto — OCR básico |
| 6 | A | raster com acentuação e Unicode | OCR/raster/misto — OCR básico |
| 7 | A | raster com valores e datas | OCR/raster/misto — OCR básico |
| 8 | A | raster com contraste reduzido | OCR/raster/misto — OCR básico |
| 9 | A | raster com ruído leve e compressão | OCR/raster/misto — OCR básico |
| 10 | A | raster com inclinação pequena | OCR/raster/misto — OCR básico |
| 11 | A | texto sobre textura e carimbo | OCR/raster/misto — OCR básico |
| 12 | A | raster com múltiplos blocos | OCR/raster/misto — OCR básico |
| 13 | B | texto digital e comprovante raster | OCR/raster/misto — páginas mistas |
| 14 | B | texto digital acima e abaixo de raster | OCR/raster/misto — páginas mistas |
| 15 | B | figura raster contínua — parte 1 | OCR/raster/misto — páginas mistas |
| 16 | B | figura raster contínua — parte 2 | OCR/raster/misto — páginas mistas |
| 17 | B | duas imagens raster afastadas | OCR/raster/misto — páginas mistas |
| 18 | B | raster sobre elemento decorativo | OCR/raster/misto — páginas mistas |
| 19 | B | imagem decorativa sem texto | texto nativo — páginas mistas |
| 20 | B | comprovante raster e texto semelhante | OCR/raster/misto — páginas mistas |
| 21 | B | nota marginal raster | OCR/raster/misto — páginas mistas |
| 22 | B | carimbo e assinatura raster | OCR/raster/misto — páginas mistas |
| 23 | B | duas colunas e imagem próxima | OCR/raster/misto — páginas mistas |
| 24 | B | raster textual e gráfico sem texto | OCR/raster/misto — páginas mistas |
| 25 | C | tabela digital com grade | texto nativo — tabelas |
| 26 | C | tabela digital sem bordas | texto nativo — tabelas |
| 27 | C | tabela raster financeira | OCR/raster/misto — tabelas |
| 28 | C | tabela raster sem bordas | OCR/raster/misto — tabelas |
| 29 | C | tabela raster com fonte pequena | OCR/raster/misto — tabelas |
| 30 | C | tabela raster com células multilinha | OCR/raster/misto — tabelas |
| 31 | C | tabela digital com células mescladas | texto nativo — tabelas |
| 32 | C | tabela raster com células mescladas | OCR/raster/misto — tabelas |
| 33 | C | tabela digital e imagem em célula | texto nativo — tabelas |
| 34 | C | tabela digital seguida de raster | OCR/raster/misto — tabelas |
| 35 | C | tabela raster com muitas colunas | OCR/raster/misto — tabelas |
| 36 | C | tabela digital continuada — parte 1 | texto nativo — tabelas |
| 37 | C | tabela digital continuada — parte 2 | texto nativo — tabelas |
| 38 | C | tabela raster continuada — parte 1 | OCR/raster/misto — tabelas |
| 39 | C | tabela raster continuada — parte 2 | OCR/raster/misto — tabelas |
| 40 | C | tabela raster em paisagem | OCR/raster/misto — tabelas |
| 41 | D | texto digital em paisagem | texto nativo — orientação, layout e figuras |
| 42 | D | raster horizontal em paisagem | OCR/raster/misto — orientação, layout e figuras |
| 43 | D | objeto de texto girado em 90 graus | texto nativo — orientação, layout e figuras |
| 44 | D | imagem textual girada dentro da página | OCR/raster/misto — orientação, layout e figuras |
| 45 | D | três colunas e nota lateral | texto nativo — orientação, layout e figuras |
| 46 | D | QR válido e rótulo separado | OCR/raster/misto — orientação, layout e figuras |
| 47 | D | código de barras válido e rótulo | OCR/raster/misto — orientação, layout e figuras |
| 48 | D | várias imagens, poucas com texto | OCR/raster/misto — orientação, layout e figuras |
| 49 | D | imagem textual parcialmente fora da página | OCR/raster/misto — orientação, layout e figuras |
| 50 | D | figura inteiramente fora da área visível | OCR/raster/misto — orientação, layout e figuras |
| 51 | E | texto, comprovante raster e tabela | OCR/raster/misto — integração |
| 52 | E | texto, tabela raster e figura decorativa | OCR/raster/misto — integração |
| 53 | E | página só digital entre páginas OCR | texto nativo — integração |
| 54 | E | várias regiões raster e decoração | OCR/raster/misto — integração |
| 55 | E | documento paisagem misto | OCR/raster/misto — integração |
| 56 | E | tabela digital continuada com OCR | OCR/raster/misto — integração |
| 57 | E | continuação com cabeçalho e célula raster | OCR/raster/misto — integração |
| 58 | E | QR, barras, texto e imagem | OCR/raster/misto — integração |
| 59 | E | figuras múltiplas e raster parcial | OCR/raster/misto — integração |
| 60 | E | integração densa | OCR/raster/misto — integração |

Continuações obrigatórias: 15–16, 36–37, 38–39 e 56–57.
Páginas 46, 47 e 58 usam QR/Code128 do ReportLab quando disponíveis; o manifesto registra `valid_generated`.
Páginas raster não recebem texto selecionável no PDF final; o texto existe somente nos pixels da imagem.
