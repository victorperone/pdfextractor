# Comparativo de Backends OCR — Fase 8

**Data das execuções:** 2026-10-04  |  **Modo:** `balanced`  |  **Idioma:** `pt-BR`  |  **Plataforma:** Linux/WSL, Python 3.12

## Resumo executivo

Foram processados integralmente os PDFs de `corpus/V3/` e `corpus/V4/` com cinco configurações de implantação: PaddleOCR, RapidOCR com ONNX Runtime, RapidOCR com OpenVINO, EasyOCR e Tesseract. Cada engine executou em processo separado; as 10 extrações, os 10 manifestos E2E, os 10 arquivos de métricas, os 10 relatórios de erros, os logs por engine e as duas comparações estão em `output/V3/` e `output/V4/`.

- **V3:** 144/144 páginas selecionadas e presentes em cada saída. Todos os cinco runs terminaram como `partial`, sem página ausente nem falha total de OCR; as páginas 137 e 141 foram marcadas degradadas pelo mesmo problema de recuperação de regiões.
- **V4:** 224/224 páginas selecionadas e presentes em cada saída, incluindo as duas páginas em branco do gabarito (215 e 216). Os cinco runs terminaram `valid`, sem páginas degradadas ou falhadas.
- **Melhor texto em V3:** EasyOCR, com CER normalizado 0,4805 e WER 0,5979. **Melhor texto em V4:** Paddle, CER normalizado 0,2630 e WER 0,3268; o tempo E2E foi 6.207,6 s.
- **Maior velocidade em V4:** RapidOCR/OpenVINO, 352,0 s E2E, cerca de 3,0× mais rápido que RapidOCR/ONNX (1.048,9 s); ambos usaram RapidOCR 3.9.2 e os mesmos hashes de reconhecedor/dicionário.
- As métricas de tabela foram idênticas entre as cinco engines dentro de cada corpus; `reading_order_accuracy` também foi idêntica entre elas em V4. Esses campos não diferenciaram os backends nesta avaliação e devem ser lidos como comportamento compartilhado do pipeline/fixture, não como evidência de equivalência dos OCRs.

## Corpus e protocolo

| Corpus | PDF | Páginas | SHA-256 do PDF | Referência usada nas métricas | SHA-256 da referência usada |
|---|---|---:|---|---|---|
| V3 | `Document_AI_V3.pdf` | 144 | `6059d69eb62b87c7aa7149d58299c0c6b7d8712588b14757b4c7d463c67f35f2` | `Document_AI_V3.md` | `1297d734b9a0ae6045a19ab3c276615a762c8ccadf52ffe0c404666c71a957f4` |
| V4 | `Corpus_Stress_OCR_Markdown_V4.pdf` | 224 | `45c09f2f03eb0dfe99bef5b8ea5b1ee686351cae2fc1244b4dcfce98fd7d0970` | `Corpus_Stress_OCR_Markdown_V4_MANIFESTO.json` | `c21c164a3c473c0506965e586d63c7b1058715e31d1a1124e992f11a350e3813` |

V3 foi avaliado contra o Markdown de referência e usou o manifesto para famílias e condições disponíveis. O manifesto V3 não contém tags `conditions`, então o breakdown correspondente aparece como “sem condição”; o breakdown por família continua disponível. V4 foi avaliado diretamente pelos 224 `expected_markdown` do manifesto. Essa escolha inclui as páginas 215–216 em branco, ausentes como seções normais do arquivo Markdown de referência.

O protocolo comum registrado nos manifestos foi `ocr-deployment-profile-v1`: idioma, modo, política de qualidade, PDF, referência/manifesto e conjunto de páginas iguais dentro de cada corpus. As duas variantes RapidOCR são providers diferentes do mesmo pacote/modelo e foram tratadas como perfis de implantação distintos.

## Perfis efetivos registrados

| Configuração | Runtime e perfil | Versões | Artefatos relevantes (prefixo SHA-256) |
|---|---|---|---|
| `easyocr` | `torch-cpu` / `pt` | easyocr 1.7.2, torch 2.14.0+cpu | craft_mlt_25k.pth `4a5efbfb48b4`, latin_g2.pth `aaa95be1c4a9` |
| `paddle` | `paddle_static` / `pt` | paddlepaddle 3.3.1, paddleocr 3.7.0, paddlex 3.7.2 | 23 arquivos de modelo Paddle; PP-OCRv6 medium det/rec, orientação e UVDoc |
| `rapidocr-onnxruntime` | `onnxruntime` / `latin/pt-compatible` | rapidocr 3.9.2, onnxruntime 1.30.0 | rec_model `e9d7a33667e8`, rec_keys `8e6d4e362978`; recognizer/dictionary iguais nos dois providers |
| `rapidocr-openvino` | `openvino` / `latin/pt-compatible` | rapidocr 3.9.2, openvino 2024.4.0 | rec_model `e9d7a33667e8`, rec_keys `8e6d4e362978`; recognizer/dictionary iguais nos dois providers |
| `tesseract` | `tesseract-cli` / `default` | tesseract 5.5.3, tessdata_dir /home/victor-wsl/workspace/pdfextractor/.ocr-runtime/share/tessdata/, lang por | por.traineddata `c4932b937207` |

Os manifestos finais de execução guardam os hashes e versões do ambiente efetivamente usado. Como o avaliador original limpava o backend antes de serializar a identidade, a identidade dos runs já concluídos foi capturada depois da execução, em processos separados, no mesmo ambiente e com as mesmas configurações/modelos. O avaliador foi corrigido para capturar essa identidade antes do fechamento em próximas execuções.

RapidOCR usou `latin_PP-OCRv3_rec_mobile.onnx` com `latin_dict.txt` para português. Os hashes completos estão nos manifestos E2E; o perfil de detecção padrão do pacote permaneceu igual para os dois providers. Tesseract usou `por` com `tesseract 5.5.3`; EasyOCR usou `latin_g2` em CPU; Paddle usou o perfil `pt` e modelos PP-OCRv6 medium.

## Cobertura, status e custo E2E

### V3

| Engine | Status | Páginas | CER normalizado ↓ | WER ↓ | OCR (s) | E2E (s) | RSS pico da árvore amostrado (MiB) | Degradadas | Falhadas |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `easyocr` | partial | 144/144 | 0.4805 | 0.5979 | 819.0 | 836.2 | 13194.8 | 2 | 0 |
| `paddle` | partial | 144/144 | 0.5910 | 0.6787 | 2360.4 | 2375.1 | 1824.0 | 2 | 0 |
| `rapidocr-onnxruntime` | partial | 144/144 | 0.5552 | 0.6439 | 225.2 | 246.6 | 1359.8 | 2 | 0 |
| `rapidocr-openvino` | partial | 144/144 | 0.5552 | 0.6439 | 40.4 | 53.5 | 2292.9 | 2 | 0 |
| `tesseract` | partial | 144/144 | 0.5375 | 0.6731 | 41.1 | 55.6 | 360.7 | 2 | 0 |

### V4

| Engine | Status | Páginas | CER normalizado ↓ | WER ↓ | OCR (s) | E2E (s) | RSS pico da árvore amostrado (MiB) | Degradadas | Falhadas |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `easyocr` | valid | 224/224 | 0.2979 | 0.4150 | 3976.8 | 4126.9 | 10159.6 | 0 | 0 |
| `paddle` | valid | 224/224 | 0.2630 | 0.3268 | 6062.9 | 6207.6 | 2337.6 | 0 | 0 |
| `rapidocr-onnxruntime` | valid | 224/224 | 0.3120 | 0.4826 | 897.6 | 1048.8 | 2280.2 | 0 | 0 |
| `rapidocr-openvino` | valid | 224/224 | 0.3120 | 0.4826 | 206.9 | 352.0 | 3132.8 | 0 | 0 |
| `tesseract` | valid | 224/224 | 0.4069 | 0.6293 | 1138.1 | 1287.0 | 720.5 | 0 | 0 |

## Métricas agregadas — V3

### Grupo 1 — Texto

| Métrica | **easyocr** | **paddle** | **rapidocr-onnxruntime** | **rapidocr-openvino** | **tesseract** |
|---|---|---|---|---|---|
| CER Raw | 0.5146 | 0.6226 | 0.5867 | 0.5867 | 0.5704 |
| CER Normalized | 0.4805 | 0.5910 | 0.5552 | 0.5552 | 0.5375 |
| CER Text Only | 0.5140 | 0.6419 | 0.5998 | 0.5998 | 0.5774 |
| WER | 0.5979 | 0.6787 | 0.6439 | 0.6439 | 0.6731 |
| Word Accuracy | 0.4021 | 0.3213 | 0.3561 | 0.3561 | 0.3269 |
| Substitution Rate | 0.3086 | 0.2920 | 0.3324 | 0.3324 | 0.2944 |
| Deletion Rate | 0.1843 | 0.2958 | 0.2142 | 0.2142 | 0.2732 |
| Insertion Rate | 0.1050 | 0.0910 | 0.0974 | 0.0974 | 0.1055 |
| Omission Rate | 0.1843 | 0.2958 | 0.2142 | 0.2142 | 0.2732 |

### Grupo 2 — Estrutura Markdown

| Métrica | **easyocr** | **paddle** | **rapidocr-onnxruntime** | **rapidocr-openvino** | **tesseract** |
|---|---|---|---|---|---|
| Heading F1 | 0.4444 | 0.4444 | 0.4444 | 0.4444 | 0.4444 |
| Heading Level Acc. | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| Heading Text CER | 1.4775 | 1.4775 | 1.4775 | 1.4775 | 1.4775 |
| Block F1 | 0.6429 | 0.8486 | 0.6553 | 0.6553 | 0.6367 |
| Paragraph Boundary F1 | 0.8670 | 0.9518 | 0.8773 | 0.8773 | 0.8618 |
| List Detection F1 | 0.9333 | 0.9333 | 0.9333 | 0.9333 | 0.8750 |
| Markdown AST Sim. | 0.5477 | 0.7555 | 0.5619 | 0.5619 | 0.5396 |

### Grupo 3 — Tabelas

| Métrica | **easyocr** | **paddle** | **rapidocr-onnxruntime** | **rapidocr-openvino** | **tesseract** |
|---|---|---|---|---|---|
| Table F1 | 0.6465 | 0.6465 | 0.6465 | 0.6465 | 0.6465 |
| Row F1 | 0.4776 | 0.4776 | 0.4776 | 0.4776 | 0.4776 |
| Column F1 | 0.4776 | 0.4776 | 0.4776 | 0.4776 | 0.4776 |
| Table Dim. Accuracy | 0.4776 | 0.4776 | 0.4776 | 0.4776 | 0.4776 |
| Cell Exact Match | 0.3740 | 0.3740 | 0.3740 | 0.3740 | 0.3740 |
| Cell CER | 0.6089 | 0.6089 | 0.6089 | 0.6089 | 0.6089 |
| Cell Alignment Acc. | 0.3740 | 0.3740 | 0.3740 | 0.3740 | 0.3740 |
| Table Structure Sim. | 0.4776 | 0.4776 | 0.4776 | 0.4776 | 0.4776 |
| Table Content F1 | 0.5346 | 0.5346 | 0.5346 | 0.5346 | 0.5346 |

### Grupo 4 — Ordem e integridade

| Métrica | **easyocr** | **paddle** | **rapidocr-onnxruntime** | **rapidocr-openvino** | **tesseract** |
|---|---|---|---|---|---|
| Reading Order Acc. | 0.1577 | 0.1556 | 0.1515 | 0.1515 | 0.1618 |
| Duplicate Content | 0.0222 | 0.1795 | 0.0215 | 0.0215 | 0.0195 |
| Header Leakage | 0.0000 | 0.5208 | 0.0000 | 0.0000 | 0.0000 |
| Footer Leakage | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| Page# Leakage | 0.0056 | 0.0014 | 0.0016 | 0.0016 | 0.0028 |
| Failure Rate | 0.0069 | 0.0069 | 0.0069 | 0.0069 | 0.0069 |
| Invalid Markdown | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |

### Grupo 5 — Dados críticos

| Métrica | **easyocr** | **paddle** | **rapidocr-onnxruntime** | **rapidocr-openvino** | **tesseract** |
|---|---|---|---|---|---|
| Numeric Recall | 0.9277 | 0.9862 | 0.9518 | 0.9518 | 0.9380 |
| Numeric Precision | 0.9945 | 0.9965 | 0.9634 | 0.9634 | 0.8372 |
| Numeric F1 | 0.9599 | 0.9913 | 0.9576 | 0.9576 | 0.8847 |
| Date Recall | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| Date Precision | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| Date F1 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| Currency Recall | 0.8378 | 0.9910 | 0.6712 | 0.6712 | 0.9324 |
| Currency Precision | 0.9841 | 0.9910 | 0.6712 | 0.6712 | 1.0000 |
| Currency F1 | 0.9051 | 0.9910 | 0.6712 | 0.6712 | 0.9650 |
| Identifier Recall | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| Identifier Precision | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| Identifier F1 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |

## Métricas agregadas — V4

### Grupo 1 — Texto

| Métrica | **easyocr** | **paddle** | **rapidocr-onnxruntime** | **rapidocr-openvino** | **tesseract** |
|---|---|---|---|---|---|
| CER Raw | 0.3041 | 0.2690 | 0.3180 | 0.3180 | 0.4167 |
| CER Normalized | 0.2979 | 0.2630 | 0.3120 | 0.3120 | 0.4069 |
| CER Text Only | 0.2979 | 0.2621 | 0.3121 | 0.3121 | 0.4080 |
| WER | 0.4150 | 0.3268 | 0.4826 | 0.4826 | 0.6293 |
| Word Accuracy | 0.5850 | 0.6732 | 0.5174 | 0.5174 | 0.3707 |
| Substitution Rate | 0.3050 | 0.1888 | 0.2276 | 0.2276 | 0.2857 |
| Deletion Rate | 0.0365 | 0.0547 | 0.0291 | 0.0291 | 0.1825 |
| Insertion Rate | 0.0734 | 0.0833 | 0.2260 | 0.2259 | 0.0705 |
| Omission Rate | 0.0365 | 0.0547 | 0.0291 | 0.0291 | 0.1825 |

### Grupo 2 — Estrutura Markdown

| Métrica | **easyocr** | **paddle** | **rapidocr-onnxruntime** | **rapidocr-openvino** | **tesseract** |
|---|---|---|---|---|---|
| Heading F1 | 0.1313 | 0.1313 | 0.1313 | 0.1313 | 0.1313 |
| Heading Level Acc. | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| Heading Text CER | 0.8158 | 0.8158 | 0.8158 | 0.8158 | 0.8158 |
| Block F1 | 0.3741 | 0.4404 | 0.4615 | 0.4615 | 0.5243 |
| Paragraph Boundary F1 | 0.4917 | 0.5539 | 0.5726 | 0.5726 | 0.6381 |
| List Detection F1 | 0.7273 | 0.9333 | 0.7273 | 0.7273 | 0.7368 |
| Markdown AST Sim. | 0.2430 | 0.2976 | 0.3145 | 0.3145 | 0.3645 |

### Grupo 3 — Tabelas

| Métrica | **easyocr** | **paddle** | **rapidocr-onnxruntime** | **rapidocr-openvino** | **tesseract** |
|---|---|---|---|---|---|
| Table F1 | 0.5000 | 0.5000 | 0.5000 | 0.5000 | 0.5000 |
| Row F1 | 0.3333 | 0.3333 | 0.3333 | 0.3333 | 0.3333 |
| Column F1 | 0.3333 | 0.3333 | 0.3333 | 0.3333 | 0.3333 |
| Table Dim. Accuracy | 0.3333 | 0.3333 | 0.3333 | 0.3333 | 0.3333 |
| Cell Exact Match | 0.2327 | 0.2327 | 0.2327 | 0.2327 | 0.2327 |
| Cell CER | 0.7818 | 0.7818 | 0.7818 | 0.7818 | 0.7818 |
| Cell Alignment Acc. | 0.2327 | 0.2327 | 0.2327 | 0.2327 | 0.2327 |
| Table Structure Sim. | 0.3333 | 0.3333 | 0.3333 | 0.3333 | 0.3333 |
| Table Content F1 | 0.3604 | 0.3604 | 0.3604 | 0.3604 | 0.3604 |

### Grupo 4 — Ordem e integridade

| Métrica | **easyocr** | **paddle** | **rapidocr-onnxruntime** | **rapidocr-openvino** | **tesseract** |
|---|---|---|---|---|---|
| Reading Order Acc. | 0.0468 | 0.0468 | 0.0468 | 0.0468 | 0.0468 |
| Duplicate Content | 0.0268 | 0.1843 | 0.0370 | 0.0370 | 0.1502 |
| Header Leakage | 0.0000 | 0.3616 | 0.0000 | 0.0000 | 0.3616 |
| Footer Leakage | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| Page# Leakage | 0.0006 | 0.0005 | 0.0005 | 0.0005 | 0.0007 |
| Failure Rate | 0.0089 | 0.0089 | 0.0089 | 0.0089 | 0.0089 |
| Invalid Markdown | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |

### Grupo 5 — Dados críticos

| Métrica | **easyocr** | **paddle** | **rapidocr-onnxruntime** | **rapidocr-openvino** | **tesseract** |
|---|---|---|---|---|---|
| Numeric Recall | 0.8443 | 0.9405 | 0.8418 | 0.8418 | 0.8989 |
| Numeric Precision | 0.8850 | 0.9894 | 0.8131 | 0.8131 | 0.4963 |
| Numeric F1 | 0.8642 | 0.9643 | 0.8272 | 0.8272 | 0.6395 |
| Date Recall | 0.8906 | 0.9505 | 0.6078 | 0.6078 | 0.9195 |
| Date Precision | 0.9829 | 0.9989 | 0.9933 | 0.9933 | 0.9878 |
| Date F1 | 0.9345 | 0.9741 | 0.7541 | 0.7541 | 0.9524 |
| Currency Recall | 0.4191 | 0.7100 | 0.3605 | 0.3605 | 0.7130 |
| Currency Precision | 0.8178 | 0.8573 | 0.5269 | 0.5276 | 0.8417 |
| Currency F1 | 0.5542 | 0.7767 | 0.4281 | 0.4283 | 0.7720 |
| Identifier Recall | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| Identifier Precision | 0.0667 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| Identifier F1 | 0.1251 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |

## Leitura dos cinco grupos

### Grupo 1 — Texto

Em V3, EasyOCR lidera CER normalizado (0,4805) e WER (0,5979); Tesseract vem em seguida em CER (0,5375), e ONNX/OpenVINO empatam em 0,5552. Paddle tem o maior CER (0,5910) e WER (0,6787). Em V4, Paddle lidera claramente CER (0,2630) e WER (0,3268); EasyOCR fica em segundo no CER (0,2979), enquanto RapidOCR/OpenVINO e ONNX empatam praticamente em CER (0,3120) e WER (0,4826). Tesseract tem CER 0,4069 e WER 0,6293.

Os edit distances por página podem ultrapassar 1,0 quando a referência daquela página é muito curta e tem mais inserções/deleções do que caracteres de referência. Isso não invalida o CER/WER agregado; significa que algumas páginas isoladas são desproporcionalmente difíceis.

### Grupo 2 — Estrutura Markdown

Paddle tem melhor Block F1 em V3 (0,8486) e V4 (0,4404). Em V4, Tesseract lidera Block F1 (0,5243), Paragraph Boundary F1 (0,6381) e Markdown AST Similarity (0,3645), enquanto Paddle lidera List Detection F1 (0,9333). Heading Level Accuracy é zero em todos os perfis nos dois corpora; os valores de Heading F1 e Heading Text CER também são idênticos entre engines dentro de cada corpus. Portanto, o pipeline não recuperou níveis de título e a detecção de heading não separou os OCRs.

### Grupo 3 — Tabelas

V3: Table F1 0,6465, Cell Exact Match 0,3740 e Cell CER 0,6089 para todas as engines. V4: Table F1 0,5000, Cell Exact Match 0,2327 e Cell CER 0,7818 para todas. Row/Column F1, dimension accuracy e structure similarity também são constantes entre os cinco perfis em cada corpus. A igualdade completa aponta para uma medida dominada pela extração/estrutura compartilhada, ou para as mesmas tabelas reconhecidas nas saídas; esses valores não sustentam um ranking de OCR para tabelas nesta rodada.

### Grupo 4 — Ordem e integridade

Em V3, Tesseract lidera Reading Order Accuracy (0,1618) e tem a menor taxa de duplicação (0,0195). Paddle tem duplicação de 0,1795 e vazamento de cabeçalho 0,5208, muito acima dos demais. Em V4, Reading Order Accuracy é exatamente 0,0468 em todos os perfis; Paddle e Tesseract têm vazamento de cabeçalho 0,3616, enquanto EasyOCR e as duas variantes RapidOCR têm zero. Invalid Markdown Rate é zero em todos. A invariância da ordem em V4 merece revisão do avaliador/fixture antes de ser usada para ranquear engines.

O `failure_rate` das métricas (0,0069 em V3 e 0,0089 em V4) não equivale às páginas com falha E2E: os manifestos mostram zero página falhada e todas as páginas selecionadas presentes. É uma métrica de conteúdo/qualidade do avaliador; status de execução está na coluna `benchmark_status` e no manifesto.

### Grupo 5 — Dados críticos

Paddle lidera Numeric F1 nos dois corpora (0,9913 em V3; 0,9643 em V4), Currency F1 (0,9910; 0,7767) e Identifier F1 (1,0000 nos dois). Em V4, Tesseract fica próximo em Currency F1 (0,7720) e alcança Date F1 0,9524. Em V3, todos atingem Date e Identifier F1 1,0000; em V4, EasyOCR tem Identifier Recall 1,0000 mas Precision 0,0667, levando Identifier F1 a 0,1251. Esse caso mostra por que a precisão e F1 são mais informativas que recall isolado.

## Páginas mais difíceis e relatórios de erros

Cada `errors_*.md` contém as métricas globais e as 20 páginas com maior CER normalizado. As páginas no quadro abaixo são as três piores por CER de cada engine, extraídas do `per_page` do JSON de métricas:

| Corpus | Engine | Página 1 (CER / WER) | Página 2 (CER / WER) | Página 3 (CER / WER) |
|---|---|---|---|---|
| V3 | `easyocr` | P97 (1.5000 / 2.1739) | P93 (1.3902 / 1.0000) | P57 (1.3182 / 0.9848) |
| V3 | `paddle` | P49 (1.6047 / 2.7778) | P57 (1.5530 / 0.9848) | P99 (1.5178 / 1.1552) |
| V3 | `rapidocr-onnxruntime` | P93 (2.0915 / 1.1905) | P88 (1.6667 / 1.2381) | P109 (1.5072 / 1.1000) |
| V3 | `rapidocr-openvino` | P93 (2.0915 / 1.1905) | P88 (1.6667 / 1.2381) | P109 (1.5072 / 1.1000) |
| V3 | `tesseract` | P96 (11.0149 / 28.5652) | P98 (1.9703 / 2.2174) | P99 (1.7787 / 1.1552) |
| V4 | `easyocr` | P181 (2.0714 / 1.0204) | P148 (1.8543 / 1.0000) | P182 (1.8247 / 0.9796) |
| V4 | `paddle` | P181 (1.9286 / 0.9796) | P182 (1.9286 / 0.9796) | P71 (1.8612 / 0.9853) |
| V4 | `rapidocr-onnxruntime` | P181 (2.7403 / 1.0000) | P148 (2.4673 / 1.1569) | P182 (2.3117 / 1.0000) |
| V4 | `rapidocr-openvino` | P181 (2.7403 / 1.0000) | P148 (2.4673 / 1.1569) | P182 (2.3117 / 1.0000) |
| V4 | `tesseract` | P169 (10.3391 / 14.9184) | P110 (4.1799 / 8.0159) | P112 (3.9954 / 7.6353) |

Em V3, páginas 137 e 141 foram as únicas degradadas para todos os backends. O diagnóstico comum foi `ocr_region_recovery_unavailable`: a recuperação não produziu tokens utilizáveis nas regiões 6 e 5, respectivamente. Não houve falha total de OCR. Em V4, os maiores erros aparecem repetidamente nas páginas 181 e 182, ambas da família “OCR de tabelas”; o manifesto classifica P181 como `ocr_table`, JPEG e cabeçalhos duplicados, e P182 como `ocr_table`, blur e cabeçalhos duplicados. Tesseract tem P169 entre as piores, família OCR de tabelas com ruído/tabela de datas e horários.

Os relatórios por página são diagnósticos; um CER isolado elevado não significa que o documento inteiro falhou. Os JSONs de métricas preservam contagens, condições/famílias, detalhes por página e penalizações por páginas selecionadas ausentes.

## Inventário e análise dos artefatos de output

Cada diretório contém 26 arquivos: 5 Markdown extraídos, 5 manifestos E2E JSON, 5 JSONs de métricas, 5 relatórios de erros Markdown, 5 logs e 1 comparação Markdown. O total é 52 artefatos; `output/V3/` ocupa 2.587.221 bytes e `output/V4/` 7.207.024 bytes (aprox. 9,34 MiB no conjunto).

| Tipo de arquivo | Conteúdo e papel na análise |
|---|---|
| `extracted_*.md` (10) | Markdown estruturado por página produzido pelo extrator híbrido. Cada arquivo tem seções de página alinhadas ao PDF; abaixo estão bytes, caracteres e cobertura. Não é o dump cru do motor OCR: inclui extração nativa, layout, tabelas e montagem do pipeline.
| `manifesto_e2e_*.json` (10) | Identidade do run, hash do PDF, modo/idioma/protocolo, status por página, warnings, timings, perfil do OCR, versões/hash de modelos e memória. É a fonte de status de execução e proveniência.
| `metrics_*.json` (10) | Agregados dos cinco grupos, métricas `per_page`, breakdown por família e condição, hashes das referências e conjunto exato de páginas. Alimentam as tabelas comparativas.
| `errors_*.md` (10) | Resumo global e até 20 piores páginas por CER normalizado; relatório de qualidade, não log de falha de processo.
| `run_*.log` (10) | Saída de inicialização/executação por perfil. Não houve traceback ou erro nos logs finais. EasyOCR registra avisos de quantização Torch e `pin_memory` sem acelerador; Paddle registra ausência de `ccache`; são avisos de desempenho/ambiente, não falhas de extração.
| `comparison_*.md` (2) | Tabelas dos cinco grupos, estados/timings/memória e breakdowns por condição/família. Reúne os mesmos JSONs validados pelo comparador.

### Cobertura e tamanho dos Markdown extraídos

| Corpus | Engine | Seções de página | Caracteres | Tamanho |
|---|---|---:|---:|---:|
| V3 | `easyocr` | 144 | 59,205 | 60,828 bytes |
| V3 | `paddle` | 144 | 65,204 | 67,170 bytes |
| V3 | `rapidocr-onnxruntime` | 144 | 62,344 | 63,891 bytes |
| V3 | `rapidocr-openvino` | 144 | 62,344 | 63,891 bytes |
| V3 | `tesseract` | 144 | 61,547 | 63,199 bytes |
| V4 | `easyocr` | 224 | 659,923 | 676,530 bytes |
| V4 | `paddle` | 224 | 667,973 | 685,115 bytes |
| V4 | `rapidocr-onnxruntime` | 224 | 643,419 | 655,157 bytes |
| V4 | `rapidocr-openvino` | 224 | 643,421 | 655,159 bytes |
| V4 | `tesseract` | 224 | 746,010 | 764,645 bytes |

Os artefatos E2E/Markdown das páginas em branco de V4 continuam presentes como seções do run; a referência por manifesto fornece o comentário canônico em branco. A cobertura avaliada dos JSONs é 144 páginas V3 e 224 V4 para cada engine, sem selecionadas ausentes.

### Logs e advertências

Os logs finais registram a tentativa e a reutilização dos artefatos completos durante o recálculo, sem `Traceback`, `ERROR` ou falha. Em V4, os avisos de EasyOCR são: criação de tensores quantizados em uma futura versão do Torch e `pin_memory` sem acelerador (CPU-only). Paddle alerta que `ccache` não está instalado. Isso não altera os resultados produzidos; os manifestos registram execução CPU para os cinco perfis.

## Comparações completas e arquivos por engine

- Comparação V3: [`output/V3/comparison_v3-full-20261004.md`](output/V3/comparison_v3-full-20261004.md)
- Comparação V4: [`output/V4/comparison_v4-full-20261004.md`](output/V4/comparison_v4-full-20261004.md)

### Saídas de V3

- `easyocr`: [`Markdown`](output/V3/extracted_easyocr_v3-full-20261004-easyocr.md), [`manifesto E2E`](output/V3/manifesto_e2e_easyocr_v3-full-20261004-easyocr.json), [`métricas`](output/V3/metrics_easyocr_v3-full-20261004-easyocr.json), [`erros`](output/V3/errors_easyocr_v3-full-20261004-easyocr.md), [`log`](output/V3/run_easyocr_v3-full-20261004-easyocr.log).
- `paddle`: [`Markdown`](output/V3/extracted_paddle_v3-full-20261004-paddle.md), [`manifesto E2E`](output/V3/manifesto_e2e_paddle_v3-full-20261004-paddle.json), [`métricas`](output/V3/metrics_paddle_v3-full-20261004-paddle.json), [`erros`](output/V3/errors_paddle_v3-full-20261004-paddle.md), [`log`](output/V3/run_paddle_v3-full-20261004-paddle.log).
- `rapidocr-onnxruntime`: [`Markdown`](output/V3/extracted_rapidocr_v3-full-20261004-rapidocr-onnxruntime.md), [`manifesto E2E`](output/V3/manifesto_e2e_rapidocr_v3-full-20261004-rapidocr-onnxruntime.json), [`métricas`](output/V3/metrics_rapidocr_onnxruntime_v3-full-20261004-rapidocr-onnxruntime.json), [`erros`](output/V3/errors_rapidocr_onnxruntime_v3-full-20261004-rapidocr-onnxruntime.md), [`log`](output/V3/run_rapidocr-onnxruntime_v3-full-20261004-rapidocr-onnxruntime.log).
- `rapidocr-openvino`: [`Markdown`](output/V3/extracted_rapidocr_v3-full-20261004-rapidocr-openvino.md), [`manifesto E2E`](output/V3/manifesto_e2e_rapidocr_v3-full-20261004-rapidocr-openvino.json), [`métricas`](output/V3/metrics_rapidocr_openvino_v3-full-20261004-rapidocr-openvino.json), [`erros`](output/V3/errors_rapidocr_openvino_v3-full-20261004-rapidocr-openvino.md), [`log`](output/V3/run_rapidocr-openvino_v3-full-20261004-rapidocr-openvino.log).
- `tesseract`: [`Markdown`](output/V3/extracted_tesseract_v3-full-20261004-tesseract.md), [`manifesto E2E`](output/V3/manifesto_e2e_tesseract_v3-full-20261004-tesseract.json), [`métricas`](output/V3/metrics_tesseract_v3-full-20261004-tesseract.json), [`erros`](output/V3/errors_tesseract_v3-full-20261004-tesseract.md), [`log`](output/V3/run_tesseract_v3-full-20261004-tesseract.log).

### Saídas de V4

- `easyocr`: [`Markdown`](output/V4/extracted_easyocr_v4-full-20261004-easyocr.md), [`manifesto E2E`](output/V4/manifesto_e2e_easyocr_v4-full-20261004-easyocr.json), [`métricas`](output/V4/metrics_easyocr_v4-full-20261004-easyocr.json), [`erros`](output/V4/errors_easyocr_v4-full-20261004-easyocr.md), [`log`](output/V4/run_easyocr_v4-full-20261004-easyocr.log).
- `paddle`: [`Markdown`](output/V4/extracted_paddle_v4-full-20261004-paddle.md), [`manifesto E2E`](output/V4/manifesto_e2e_paddle_v4-full-20261004-paddle.json), [`métricas`](output/V4/metrics_paddle_v4-full-20261004-paddle.json), [`erros`](output/V4/errors_paddle_v4-full-20261004-paddle.md), [`log`](output/V4/run_paddle_v4-full-20261004-paddle.log).
- `rapidocr-onnxruntime`: [`Markdown`](output/V4/extracted_rapidocr_v4-full-20261004-rapidocr-onnxruntime.md), [`manifesto E2E`](output/V4/manifesto_e2e_rapidocr_v4-full-20261004-rapidocr-onnxruntime.json), [`métricas`](output/V4/metrics_rapidocr_onnxruntime_v4-full-20261004-rapidocr-onnxruntime.json), [`erros`](output/V4/errors_rapidocr_onnxruntime_v4-full-20261004-rapidocr-onnxruntime.md), [`log`](output/V4/run_rapidocr-onnxruntime_v4-full-20261004-rapidocr-onnxruntime.log).
- `rapidocr-openvino`: [`Markdown`](output/V4/extracted_rapidocr_v4-full-20261004-rapidocr-openvino.md), [`manifesto E2E`](output/V4/manifesto_e2e_rapidocr_v4-full-20261004-rapidocr-openvino.json), [`métricas`](output/V4/metrics_rapidocr_openvino_v4-full-20261004-rapidocr-openvino.json), [`erros`](output/V4/errors_rapidocr_openvino_v4-full-20261004-rapidocr-openvino.md), [`log`](output/V4/run_rapidocr-openvino_v4-full-20261004-rapidocr-openvino.log).
- `tesseract`: [`Markdown`](output/V4/extracted_tesseract_v4-full-20261004-tesseract.md), [`manifesto E2E`](output/V4/manifesto_e2e_tesseract_v4-full-20261004-tesseract.json), [`métricas`](output/V4/metrics_tesseract_v4-full-20261004-tesseract.json), [`erros`](output/V4/errors_tesseract_v4-full-20261004-tesseract.md), [`log`](output/V4/run_tesseract_v4-full-20261004-tesseract.log).

## Problemas corrigidos durante a preparação e execução

1. Removi do ambiente virtual os adaptadores RapidOCR legados que conflitavam com OpenVINO e removi distribuições OpenCV duplicadas; o runtime final usa uma única wheel `opencv-contrib-python 4.10.0.84`. Instalei o pacote unificado `rapidocr 3.9.2` e mantive os runtimes ONNX Runtime 1.30.0 e OpenVINO 2024.4.0.
2. Corrigi o adaptador RapidOCR para passar o enum `EngineType` exigido pela API 3.x e para converter o `RapidOCROutput` (`boxes`, `txts`, `scores`) em tokens. Antes disso, os dois providers falhavam durante a construção/conversão do resultado.
3. Ajustei o smoke de preflight: a fonte desenhada agora usa tamanho legível pelos detectores e cada deep smoke é executado em processo isolado. O smoke combinado carregava Paddle e depois Torch no mesmo processo e podia terminar em segmentation fault, embora ambos funcionassem isoladamente.
4. Corrigi `evaluate_e2e.py`: `char_count` apontava para uma variável inexistente (`content`), e a identidade do backend era lida depois que o extrator já o havia fechado. A contagem usa `page.reading_text`; próximas execuções capturam versão, perfil e hashes antes do fechamento.
5. Corrigi `compute_metrics.py` para propagar `benchmark_protocol_id` do manifesto E2E. Sem esse campo, `compare_engines.py` rejeitava runs compatíveis. Corrigi também a tabela comparativa para ler `elapsed_s` do bloco `run`.
6. Preparei um runner retomável em [`scripts/run_v3_v4_comparison.sh`](scripts/run_v3_v4_comparison.sh), com caminhos locais dos modelos, os dois corpora e as cinco configurações. Ele reutiliza Markdown e manifesto completos para recalcular métricas sem repetir OCR.

Após cada problema encontrado, corrigi o código/configuração e repeti a etapa necessária. As dez extrações finais e as duas comparações foram concluídas e conferidas contra hashes, seleção de páginas, status e identidades registradas.

## Limites de interpretação

- Este é um benchmark de perfis E2E: runtime, modelo, provider, pré-processamento e threads diferem. Os dois RapidOCR compartilham modelos reconhecedor/dicionário e permitem leitura mais direta do provider; as demais famílias não isolam somente o motor de inferência.
- V3 termina `partial` porque o pipeline não recuperou texto em duas regiões; suas métricas servem à comparação diagnóstica, mas não equivalem ao estado `valid` de V4.
- Métricas de confidence não foram comparadas como probabilidades entre engines. Tabelas, headings e leitura de ordem com valores idênticos entre backends não separam os recognizers nesta execução.
- O RSS de árvore é amostrado e pode perder picos curtos. O comparador mostra pico da árvore amostrado e pico do processo pai; os JSONs guardam intervalo e contagem de amostras.
- O manifesto V3 não tem tags de condição; por isso as análises detalhadas por condição só são completas em V4.

## Conclusões complementares — extrações diretas V3/V4

Esta seção incorpora a análise dos Markdown gerados por extrações completas e independentes de cada engine. A grafia correta do diretório é `output/comparison-v2-v3/` (com hífen após `comparison`). Os dez arquivos foram comparados com os artefatos E2E correspondentes: depois de remover apenas os separadores diagnósticos `## Página N`, o texto normalizado de cada par coincidiu integralmente. Assim, as métricas E2E acima descrevem o conteúdo das extrações diretas.

As médias abaixo são a média simples dos resultados V3 e V4; CER/WER e duplicação menores são melhores. A seleção de default considera a fidelidade textual, estrutural e dos dados extraídos, sem usar velocidade ou recursos.

| Engine | CER médio ↓ | WER médio ↓ | AST Markdown médio ↑ | Duplicação média ↓ | F1 numérico médio ↑ | F1 moeda médio ↑ | Precisão de IDs média ↑ |
|---|---:|---:|---:|---:|---:|---:|---:|
| EasyOCR | **0.3892** | 0.5064 | 0.3953 | **0.0245** | 0.9121 | 0.7297 | 0.5333 |
| Paddle | 0.4270 | **0.5028** | **0.5265** | 0.1819 | **0.9778** | **0.8839** | **1.0000** |
| RapidOCR ONNX Runtime | 0.4336 | 0.5633 | 0.4382 | 0.0292 | 0.8924 | 0.5496 | **1.0000** |
| RapidOCR OpenVINO | 0.4336 | 0.5633 | 0.4382 | 0.0292 | 0.8924 | 0.5497 | **1.0000** |
| Tesseract | 0.4722 | 0.6512 | 0.4520 | 0.0848 | 0.7621 | 0.8685 | **1.0000** |

### Interpretação e recomendação

- **Paddle é a recomendação geral para default** quando a extração precisa preservar também valores, datas e identificadores, além de texto corrido. Tem o melhor WER médio, os melhores F1 médios de números e moedas, precisão perfeita de IDs e a melhor similaridade Markdown média. No V4 também lidera CER/WER e os principais dados críticos.
- **EasyOCR é mais fiel para transcrição textual simples**, com o melhor CER médio e a menor duplicação. No V3 vence CER e WER; no V4 fica atrás de Paddle. Porém, no V4 sua precisão de identificadores é apenas 0.0667 (F1 0.1251), sinal de muitos falsos positivos, e seus F1 de números e moedas ficam abaixo de Paddle.
- A vantagem de Paddle vem com ressalvas: no V3 seu CER/WER são piores que EasyOCR, e sua duplicação média (0.1819) é bem maior. Paddle e, no V4, Tesseract também mantêm cabeçalhos repetidos que o Markdown de referência exclui; esses cabeçalhos estão visíveis nos PDFs, então a métrica de vazamento reflete a diferença entre o critério do gabarito e o conteúdo impresso.
- As métricas de tabela permanecem iguais entre engines: `Cell Exact Match` é 0.3740 no V3 e 0.2327 no V4, com `Cell CER` 0.6089 e 0.7818, respectivamente. Portanto, não há evidência nesta rodada de que trocar a engine resolva a extração de tabelas; a detecção e montagem compartilhadas do pipeline são o próximo ponto a revisar.
- A hierarquia de títulos continua sem recuperação (`Heading Level Accuracy = 0.0000` em ambos os corpora). Essa limitação estrutural não é resolvida escolhendo outro OCR.

Os dez Markdown independentes e o comparativo completo estão em [output/comparison-v2-v3/relatorio-comparativo.md](output/comparison-v2-v3/relatorio-comparativo.md), que também liga cada arquivo V3/V4 e os detalhamentos por corpus.
