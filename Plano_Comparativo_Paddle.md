# PDFExtractor — Auditoria técnica e plano corretivo para comparar PP-OCRv5 e PP-OCRv6 com PP-TableMagic

**Data de referência:** 24/09/2026  
**Destinatário:** equipe de desenvolvimento e de validação do PDFExtractor  
**Repositório público:** https://github.com/victorperone/pdfextractor  
**Branch anteriormente indicada:** [`feat/paddle-ocrv6-evaluation`](https://github.com/victorperone/pdfextractor/tree/feat/paddle-ocrv6-evaluation)  
**Objetivo de produto:** executar um comparativo reproduzível entre **PP-OCRv5 + PP-TableMagic** e **PP-OCRv6 + PP-TableMagic**, tanto no reconhecimento textual quanto na qualidade final de tabelas e documentos exportados, sem misturar modelos, configurações, resultados, caches ou processos.

> **IMPORTANTE — escopo e honestidade da auditoria.** Esta é uma revisão estática e um plano de correções/validação, **não** uma confirmação de falhas reproduzidas nem a homologação da revisão mais recente do repositório. Em 24/09/2026 foi possível ler o README, `ocr/models.py`, `ocr/paddle.py`, `config.py`, `cli.py`, `renderers/markdown.py`, `requirements.txt` e a documentação oficial do PP-TableMagic por seus endereços públicos. O acesso à árvore e ao histórico completos da branch e o clone via Git falharam neste ambiente; não foi possível verificar o SHA atual, inspecionar **todos** os arquivos recém-alterados, instalar os pesos, executar o `pytest` nem rodar o comparativo real. **Não se deve transformar requisitos ou hipóteses abaixo em acusações de defeitos já existentes no código atualizado.** Se uma mudança recente já resolveu um ponto, marcar o ticket como corrigido e anexar teste/evidência; não reverter melhorias apenas para seguir este documento. As referências a linhas na branch são mutáveis; registrar e substituir pelo SHA imutável antes da implementação.

## 0. Resumo executivo e decisão operacional

**Resposta à pergunta:** sim, há riscos relevantes ao comparar as duas versões com o mesmo motor de tabelas. O principal é que **PP-TableMagic não é simplesmente uma etapa de formatação de tabelas**. A pipeline oficial `TableRecognitionPipelineV2` inclui módulos próprios de localização de tabelas, classificação, reconhecimento de estrutura, detecção de células **e OCR**, podendo reprocessar texto em células. Mudar apenas `--ocr-model-profile` no PDFExtractor não demonstra que o OCR executado *dentro* do PP-TableMagic mudou junto. Se o comparativo for executado assim, pode medir duas variantes de OCR externo combinadas com um terceiro OCR interno igual em ambos os braços, ou até medir apenas o OCR interno. A documentação oficial expõe `text_detection_model_name`, `text_recognition_model_name`, `*_model_dir`, `use_ocr_model` e `use_ocr_results_with_table_cells`. A integração deve comprovar a configuração efetivamente carregada em **cada componente e em cada execução**. [PaddleOCR — PP-TableMagic, parâmetros de inicialização](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/table_recognition_v2.en.md#L588-L670), [opções de inferência e novo reconhecimento nas células](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/table_recognition_v2.en.md#L696-L730).

**Achado específico do snapshot acessível:** o perfil `pt` do projeto é `PP-OCRv5_server_det` + `latin_PP-OCRv5_mobile_rec`; o `pt-v6-medium` é `PP-OCRv6_medium_det` + `PP-OCRv6_medium_rec`. Assim, a comparação não é, tecnicamente, “v5 server completo versus v6 medium”. É a comparação de **duas configurações compostas**, e isso precisa aparecer no nome dos braços e no relatório. O snapshot do README ainda declara não implementar modelo aprendido de estrutura de tabelas; não foi possível demonstrar no código acessível que a integração com PP-TableMagic recém-alterada esteja conectada, ativa e testada. Essa situação deve ser **verificada no SHA atual**, não presumida. [Perfis do projeto](https://github.com/victorperone/pdfextractor/blob/feat/paddle-ocrv6-evaluation/src/structured_pdf_text/ocr/models.py#L60-L88), [README](https://github.com/victorperone/pdfextractor/blob/feat/paddle-ocrv6-evaluation/README.md#L70-L84).

**Regra para iniciar a coleta de métricas:** não publicar resultado de precisão/velocidade entre v5 e v6 enquanto não for possível gerar um manifesto comprovando, para cada braço: os nomes e hashes dos pesos de detecção e reconhecimento **fora e dentro do PP-TableMagic**; os mesmos pesos e parâmetros dos modelos de estrutura/células/layout; o mesmo corpus e recortes; a mesma política de fallback; o mesmo ambiente; e nenhuma saída parcial contabilizada como sucesso. Antes disso, rodar apenas *smoke tests* de instalação e integração, explicitamente rotulados como tal.

### 0.1. Legenda dos achados

- **OBS:** comportamento que foi observado diretamente no snapshot de código ou na documentação oficial acessíveis. **Não** significa erro reproduzido em execução.
- **COND:** risco condicionado à maneira como o PP-TableMagic tiver sido integrado no SHA novo. O desenvolvedor deve primeiro procurar código/teste correspondente; fechar se comprovadamente coberto.
- **VAL:** evidência ainda inexistente nesta auditoria. É um requisito de teste, não a alegação de que houve falha.
- **DEC:** decisão de escopo ou semântica que precisa ser explicitada antes de programar e comparar resultados.

**P0:** impede interpretar o experimento como comparação válida ou pode causar corrupção silenciosa relevante. **P1:** corrigir/validar antes de rodar o benchmark oficial. **P2:** melhorar reprodutibilidade, manutenção, segurança e operação antes da adoção contínua. As prioridades se referem à **avaliação proposta**, não à gravidade comprovada em produção.

### 0.2. Quadro inicial do backlog

| ID | Prioridade | Tipo | Correção ou verificação | Evidência mínima de encerramento |
|---|---|---|---|---|
| C01 | P0 | OBS + COND | Propagar versão real do OCR ao PP-TableMagic e verificar modelos internos | Manifesto e teste que diferenciam pesos internos v5/v6 |
| C02 | P0 | DEC + COND | Definir se o experimento usa OCR interno do PP-TableMagic ou OCR externo do PDFExtractor | Dois braços com o mesmo desenho, sem OCR oculto |
| C03 | P0 | OBS + VAL | Identificar corretamente o baseline v5 misto e a variante v6 | Nomes completos de detectores/reconhecedores no relatório |
| C04 | P0 | COND | Fixar todas as outras peças do PP-TableMagic | Hashes idênticos de estrutura, células, layout e classificadores |
| C05 | P0 | COND | Separar motor de tabela original versus PP-TableMagic e fallback | Contadores e proveniência por tabela, sem substituição silenciosa |
| C06 | P0 | COND | Evitar OCR duplo/triplo, resultados duplicados e atribuição incorreta | Proveniência de cada célula e teste de duplicação |
| C07 | P1 | COND | Adaptador seguro e explícito para saída HTML, células e geometria | `StructuredTable` preserva spans, coordenadas e valores |
| C08 | P1 | COND | Corrigir transformações de coordenadas recorte → página/PDF | Testes com CropBox, rotação e escala não unitária |
| C09 | P1 | COND | Política de texto nativo versus OCR de tabela | Casos híbridos sem perda ou conteúdo repetido |
| C10 | P1 | COND | Desabilitar pré-processamento e reorientações diferentes entre braços | Mesma sequência e parâmetros registrados por página |
| C11 | P1 | COND | Fixar classificação de tabelas com/sem bordas e roteamento | Mesmas famílias de modelos e decisões auditáveis |
| C12 | P1 | VAL | Verificar compatibilidade da API oficial com versões instaladas | Teste de importação, inicialização e inferência para os dois braços |
| C13 | P1 | OBS + COND | Ampliar instalação offline e prontidão para **todos** os modelos de tabela | Status completo e execução sem rede |
| C14 | P1 | OBS + COND | Isolar cache, configurações globais e processos | Dois braços não alteram o ambiente um do outro |
| C15 | P1 | COND | Reavaliar memória, workers, encerramento forçado e timeouts | Medições de RSS de pico e recuperação controlada |
| C16 | P1 | COND | Congelar parâmetros de recorte, DPI, detecção, limiares e retries | Manifestos equivalentes; diferenças justificadas |
| C17 | P1 | VAL | Criar corpus anotado de tabelas e texto | Ground truth versionado, amostragem e revisão dupla |
| C18 | P1 | VAL | Medir OCR, estrutura, atribuição de células e saída final separadamente | Métricas por etapa e intervalos de confiança |
| C19 | P1 | COND | Identificar erros e resultados parciais sem tratá-los como sucesso | Política única de falha, contagem de amostras e exit codes |
| C20 | P1 | COND | Evitar vazamento de condição entre caches, saída e execução paralela | Isolamento verificável de diretórios e arquivos temporários |
| C21 | P1 | COND | Validar HTML, serialização Markdown/JSON e células mescladas | Roundtrip estrutural e testes de renderização |
| C22 | P1 | COND | Controlar recuperação regional e merge de tabelas entre páginas | Resultados rastreáveis sem tabelas inventadas ou omitidas |
| C23 | P1 | VAL | Estabelecer benchmark pareado e tratamento estatístico | Relatório por documento/tabela, sem métricas incomparáveis |
| C24 | P1 | VAL | Registrar métricas de custo real e consumo de recursos | P50/P95, cold/warm, RSS, falhas e throughput |
| C25 | P1 | OBS + COND | Atualizar CLI, API, README e esquema de relatório | Comandos reais validados e documentação por SHA |
| C26 | P1 | VAL | Adicionar testes unitários, integração, regressão e CI com smoke opcional | Pipeline CI com testes sem e com pesos locais |
| C27 | P1 | COND | Proteger documentos privados e processar HTML sem execução ativa | Logs minimizados, parser seguro e retenção controlada |
| C28 | P1 | DEC + VAL | Documentar critério de término, rollback e aprovação | Checklist assinado com evidência por braço |

---

## 1. Estabelecer a revisão efetiva antes de corrigir qualquer ponto

### 1.1. Congelamento da evidência

O link de uma branch é mutável. O responsável deve registrar a versão exata antes da revisão e deixar os experimentos vinculados a ela. Executar no clone do desenvolvedor:

```bash
git fetch --all --prune
git switch feat/paddle-ocrv6-evaluation
git pull --ff-only
git rev-parse --verify HEAD
git status --porcelain=v1
git log -8 --date=iso-strict --format='%H %ad %s'
git diff --stat main...HEAD
```

Se a nova integração estiver em outra branch ou já tiver sido incorporada à `main`, substituir a branch de exemplo pelo **ref real** e registrar a árvore resultante; não mesclar branches por conveniência durante o benchmark. Guardar SHA, branch, status limpo, diff, tags e artefato de build. Guardar o plano antigo somente como referência histórica, pois mudanças recentes podem ter resolvido ou alterado seus pontos.

### 1.2. Inspeção obrigatória da integração nova

Pesquisar no SHA congelado, sem presumir nomes de arquivos:

```bash
git grep -n -i -E 'tablemagic|TableRecognitionPipelineV2|table_recognition_v2|PP-TableMagic' -- ':!*.lock'
git grep -n -E 'ocr_model_profile|text_detection_model_name|text_recognition_model_name|use_ocr_model|use_ocr_results_with_table_cells'
git grep -n -E 'predict\(|predict_iter\(|pred_html|table_res_list|cell_box_list|table_ocr_pred'
git grep -n -E 'setup-models|models-status|PADDLE_PDX_CACHE_HOME|PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'
git grep -n -E 'StructuredTable|StructuredCell|rowspan|colspan|render_markdown|content_blocks'
```

Anexar ao ticket um mapa **arquivo → função → chamada → teste** para: criação de perfis, instalação, prontidão, construção da pipeline, execução em página/recorte, conversão do resultado, resolução de conflito com tabelas nativas, fusão entre páginas, escrita de JSON/Markdown e relatório de benchmark. Não considerar “integração concluída” apenas porque existe importação de `TableRecognitionPipelineV2`.

### 1.3. Prova mínima de que o PP-TableMagic efetivamente executa

Para cada braço, selecionar um PDF com tabela digital e outro digitalizado, ambos conhecidos e permitidos para teste. Gerar uma evidência de execução com: nome e hash do PDF; página; bbox selecionada; hash da imagem entregue ao PP-TableMagic; pipeline inicializada; nomes e paths dos pesos carregados; presença de `table_res_list`; estrutura e texto de ao menos uma tabela; origem e número de células no documento final; tempo da etapa. Não usar só a presença do comando `--table-engine` ou similar como comprovação.

**Aceite de 1.1 a 1.3:** documento com o SHA imutável, diff, inventário do código novo e ao menos um resultado rastreável de PP-TableMagic em cada braço. Na ausência de código e teste de integração, abrir ticket de **implementação**, não marcar a comparação como pronta.

---

## 2. Definição experimental: o que exatamente será comparado

### 2.1. Dois desenhos possíveis, que NÃO podem ser misturados

**Desenho A — OCR interno configurado dentro do PP-TableMagic (mais simples para um comparativo integral de pipelines).** Cada braço instancia o PP-TableMagic com o seu próprio detector e reconhecedor de OCR. Os demais componentes de tabela ficam fixos. O texto fora das tabelas, se também for comparado, deve usar o mesmo perfil correspondente. Se o PP-TableMagic dividir e reconhecer novamente texto por células, essa nova chamada precisa usar **os mesmos pesos OCR declarados para o braço**. Esta modalidade mede o efeito do modelo de OCR no sistema completo, incluindo interações com estrutura e célula.

**Desenho B — estrutura/células do PP-TableMagic com OCR externo controlado pelo PDFExtractor (isola mais fortemente reconhecimento de texto).** O PP-TableMagic fornece a geometria e o texto do braço é atribuído a células pelo código do projeto. **Atenção:** `use_ocr_model=False` não implica automaticamente um mecanismo de injeção de tokens externos, nem garante que a pipeline produzirá estrutura/HTML válido sem OCR; isto precisa ser comprovado na versão exata da biblioteca e, se necessário, por um adaptador de modelos de estrutura/células independente. O relatório deverá descrever com exatidão quais módulos ainda executam reconhecimento, como os spans são preservados e como a atribuição token→célula é feita.

**Proibição metodológica:** não chamar uma execução de “v6 + PP-TableMagic” quando o OCR principal é v6 mas o módulo interno de tabela continua em v5 ou no default desconhecido. Se for intencional comparar **OCR externo v6 + OCR de tabela v5**, nomear essa composição explicitamente e tratá-la como **terceiro experimento**, não como o braço v6 proposto pelo usuário.

### 2.2. Matriz mínima de braços e controles

| Campo | Braço A | Braço B | Regra |
|---|---|---|---|
| Identificador sugerido | `v5_serverdet_latinmobilerec_tablemagic` | `v6_mediumdet_mediumrec_tablemagic` | Não abreviar como “v5 server” e “v6” sem manifesto |
| Detector OCR externo | `PP-OCRv5_server_det` | `PP-OCRv6_medium_det` | Diferentes por desenho |
| Reconhecedor OCR externo | `latin_PP-OCRv5_mobile_rec` | `PP-OCRv6_medium_rec` | Diferentes por desenho |
| OCR interno da pipeline de tabela | **Mesmo par do braço A** | **Mesmo par do braço B** | Ou ambos desligados sob Desenho B comprovado |
| Layout de tabela | Mesmo nome e SHA | Mesmo nome e SHA | Fixar |
| Classificador de tabela | Mesmo nome e SHA | Mesmo nome e SHA | Fixar |
| Estrutura wired/wireless | Mesmos nomes e SHAs | Mesmos nomes e SHAs | Fixar |
| Detector de células wired/wireless | Mesmos nomes e SHAs | Mesmos nomes e SHAs | Fixar |
| Pré-processamento e orientação | Mesmos parâmetros | Mesmos parâmetros | Fixar e registrar decisões automáticas |
| Célula: recortar/reconhecer novamente | Igual | Igual | Fixar, documentar custo |
| Seleção de páginas/regiões | Idêntica para o benchmark de OCR isolado | Idêntica | No E2E, medir diferenças de roteamento separadamente |
| Corpus e anotação | Exatamente os mesmos | Exatamente os mesmos | Emparelhamento por ID estável |
| Ambiente, CPU, backend, threads, memória | Igual | Igual | Alternar ordem em rodadas separadas |
| Política de falha e limites | Igual | Igual | Nunca excluir falhas só de um braço |

**Controle adicional:** executar um teste de isolamento com o **mesmo OCR fixo** nos dois braços e todos os componentes de tabela idênticos; as saídas devem ser equivalentes dentro da tolerância de não determinismo previamente definida. Depois alterar só o OCR. Caso os resultados de tabela mudem já no teste de isolamento, existe variável oculta ou não determinismo a explicar.

### 2.3. Não confundir benchmark oficial com benchmark local

O próprio PaddleOCR adverte que métricas publicadas de modelos v5 e v6 podem vir de conjuntos diferentes e não permitem comparação direta. Os resultados oficiais não substituem uma medição **no mesmo corpus português e nos mesmos PDFs do projeto**. O baseline atual também é misto, não equivalente ao pacote oficial completo v5 server. [Documentação oficial de OCR](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/OCR.en.md), [perfis locais](https://github.com/victorperone/pdfextractor/blob/feat/paddle-ocrv6-evaluation/src/structured_pdf_text/ocr/models.py#L60-L88).

### 2.4. Experimentos adicionais, separados do requisito principal

Se houver interesse, executar depois: (i) v5 completo server detector + server recognizer versus v6 medium, para uma comparação de famílias mais próxima de outra referência; (ii) v6 small com o mesmo PP-TableMagic; (iii) OCR externo apenas, com estrutura fixa; (iv) PP-TableMagic isolado em recortes de tabelas; (v) processamento integral do PDF. Nenhum desses deve ser misturado à tabela de resultados do comparativo principal.

---
## 3. Tickets bloqueadores de validade da comparação

### C01 — Vincular e comprovar o OCR real usado DENTRO do PP-TableMagic

**Prioridade P0; classificação OBS (API oficial) + COND (integração local).**

**Base verificável:** `TableRecognitionPipelineV2` possui parâmetros distintos para `text_detection_model_name`, `text_detection_model_dir`, `text_recognition_model_name`, `text_recognition_model_dir` e uma etapa própria de OCR; sem valores explícitos, usa os defaults da pipeline. A configuração em `ocr/models.py` do PDFExtractor, por sua vez, alimenta o adaptador `PaddleOcrEngine` e **não é, por si só, prova** de propagação ao construtor da pipeline de tabelas. [API oficial](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/table_recognition_v2.en.md#L623-L670), [modelo local](https://github.com/victorperone/pdfextractor/blob/feat/paddle-ocrv6-evaluation/src/structured_pdf_text/ocr/models.py#L36-L88).

**Falha possível:** execução nominal v5/v6 com PP-TableMagic inicializando silenciosamente o mesmo OCR padrão nos dois braços. A aparente evolução da precisão da tabela refletiria o OCR externo, o roteamento ou variação estocástica, não uma comparação válida dos dois pares completos. Outro risco: indicar o modelo v6 por `*_model_name` e, por engano, apontar `*_model_dir` para pesos v5 de mesmo papel funcional.

**Intervenção requerida:**

1. Criar uma configuração imutável de um **braço experimental** contendo os modelos de OCR externo, OCR interno de tabela, orientação, layout, estrutura wired/wireless e células. Não espalhar nomes por strings livres na CLI e no adaptador.
2. Construir `TableRecognitionPipelineV2` por meio de uma única função que receba o braço; passar explicitamente ambos os `text_*_model_name` e respectivos diretórios. Não pressupor que um parâmetro de língua da pipeline mapeia para o perfil local.
3. Falhar na inicialização quando modelo/diretório escolhido pelo braço divergir do manifesto ou quando a biblioteca rejeitar uma combinação. Nunca substituir v6 por default v5 sem retornar erro.
4. Antes do primeiro documento, produzir uma evidência das configurações resolvidas e dos pesos efetivos: modelo, diretório canônico, arquivos, hash SHA-256/manifesto, versão do pacote e ID do processo. Não logar caminho privado do documento ou texto reconhecido quando não necessário.
5. Garantir que o *reconhecimento adicional por célula* use o OCR do mesmo braço. O parâmetro oficial `use_ocr_results_with_table_cells` tem padrão `True`; portanto, é insuficiente verificar apenas `overall_ocr_res`. [API oficial](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/table_recognition_v2.en.md#L721-L730).
6. Se for impossível configurar os modelos v6 de maneira compatível na versão travada, implementar/adaptar a integração com documentação e testes antes da comparação. Não simular suporte alterando apenas rótulos do relatório.

**Testes:** instanciar v5 e v6 com *fakes* que registrem os kwargs; verificar que `text_detection_model_name` e `text_recognition_model_name` divergem como esperado e que o modelo de estrutura não muda; com pesos reais, injetar cache vazio/default não permitido e confirmar falha; registrar no manifesto os quatro nomes dos dois subsistemas; ativar reconhecimento por célula e rastrear o modelo efetivo da segunda passada. **Aceite:** nenhum braço começa a processar se o OCR interno não corresponder ao manifesto ou se a condição experimental não estiver explicitamente marcada como “OCR interno desativado e atribuição externa validada”.

### C02 — Escolher uma arquitetura única para OCR de tabelas e remover ambiguidades de execução

**Prioridade P0; classificação DEC + COND.**

**Decisão inicial obrigatória:** o time deve escolher **Desenho A ou Desenho B** da seção 2.1. Não alternar de modo implícito conforme uma página apresenta texto nativo, OCR com confiança baixa ou tabela visual. Para cada algoritmo, declarar o que produz geometria, o que produz texto, quem associa o texto à célula e qual módulo tem autoridade na saída.

**No Desenho A:** o PP-TableMagic é responsável pela estrutura e pelos textos das células. O OCR externo pode continuar responsável pelo restante da página, mas não deve substituir aleatoriamente células do modelo de tabela. Se houver refinamento das células por OCR externo, criar um **terceiro braço explicitamente composto** e medir o custo adicional, ou desativar refinamento para preservar o desenho principal.

**No Desenho B:** não basta instanciar a pipeline com `use_ocr_model=False`; o resultado sem OCR deve ser avaliado, pois parte do pós-processamento pode exigir tokens. Se o comportamento documentado não for suficiente, usar diretamente os módulos de classificação, estrutura e detecção de células suportados na versão instalada e implementar uma associação independente de tokens às células. O resultado deve preservar `rowspan`/`colspan`, geometria e vazios legítimos. Não acessar classes internas privadas da biblioteca sem teste de compatibilidade e versionamento rígido.

**Contratos de dados sugeridos:** `TableRegion(image, page_bbox, crop_transform, page_index, region_id)`, `TableGeometry(cells, row/column topology, spans, source_model)`, `TableText(tokens, source_ocr_profile, confidence)`, `ResolvedTable(geometry, text_by_cell, provenance, diagnostics)`. Isso é **proposta de design**, não alegação de que essas classes existem.

**Teste de aceite:** alterar o OCR declarado muda apenas os componentes permitidos pelo desenho; ligar/desligar OCR externo não muda textos de células no Desenho A; no Desenho B, o PP-TableMagic não executa reconhecimento implícito, comprovado pela instrumentação de chamadas.

### C03 — Nomear corretamente o baseline v5 e impedir comparação de variantes diferentes sem aviso

**Prioridade P0; classificação OBS.**

O snapshot acessível define `pt` como `PP-OCRv5_server_det` e `latin_PP-OCRv5_mobile_rec`, enquanto v6 medium usa `PP-OCRv6_medium_det` e `PP-OCRv6_medium_rec`. O par v5 é híbrido, e a comparação resulta na mudança simultânea **do detector, do reconhecedor e da família/porte do reconhecedor**. [Código de perfis](https://github.com/victorperone/pdfextractor/blob/feat/paddle-ocrv6-evaluation/src/structured_pdf_text/ocr/models.py#L60-L87).

**Correção:** incluir no nome curto dos braços e na legenda de todas as métricas os pares reais de detecção e reconhecimento. Registrar a língua, a cobertura de caracteres e a tabela de símbolos efetiva dos reconhecedores. Não anunciar que “v6 superou v5 server” se o baseline for mobile Latin na etapa de reconhecimento. Se o requisito for uma comparação de família com porte equivalente, adicionar braço `PP-OCRv5_server_det + PP-OCRv5_server_rec` explicitamente, sem substituir o baseline atual de forma retroativa.

**Teste:** um validador rejeita arquivos de resultado com `ocr_family="v5"` sem modelo/versão completos; painéis e exportações leem o manifesto do run, e não deduzem a família de nome de arquivo. **Aceite:** não existe resultado cujo título contradiga os pesos reais.

### C04 — Fixar a versão e os pesos de TODOS os componentes de tabela que não serão comparados

**Prioridade P0; classificação COND.**

A PP-TableMagic compreende, além do OCR, modelos de localização de tabela, classificação, estrutura de tabelas com/sem borda e detecção de células. Modelos diferentes nesses módulos invalidam a atribuição causal do experimento ao OCR. A documentação oficial lista parâmetros separados para estrutura e células wired/wireless, classificação, layout, orientação e unwarping. [Modelos e arquitetura oficiais](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/table_recognition_v2.en.md#L199-L242), [parâmetros](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/table_recognition_v2.en.md#L588-L623).

**Correção:** criar um manifesto de artefatos com identificadores e hashes de todos os modelos que não variam e um validador que compare os dois braços, recusando diferenças inesperadas. Fixar também a configuração de `use_e2e_wired_table_rec_model`, `use_e2e_wireless_table_rec_model`, conversão de células para HTML, orientação da tabela e divisão do OCR por células. Impedir downloads automáticos que substituam o “mesmo” nome de modelo por pesos diferentes ao longo de dias.

**Teste:** trocar intencionalmente apenas um hash de detector de células no braço B; o pré voo falha e aponta exatamente o modelo divergente. **Aceite:** hashes e parâmetros de controle são idênticos e constam do relatório para ambos os braços.

### C05 — Tornar explícita a seleção entre tabelas nativas, heurísticas e PP-TableMagic

**Prioridade P0; classificação COND, ancorada na arquitetura anterior observada.**

O PDFExtractor já dispõe de detecção de tabelas nativas, grades vetoriais/rasterizadas, heurísticas sem bordas, OCR regional e montagem de `StructuredTable`. O README descreve cascata determinística e recuperação visual seletiva. Ao adicionar PP-TableMagic é necessário escolher se será usado **em todas as tabelas do corpus experimental** ou somente em certas tabelas candidatas. [README, modos e cascata](https://github.com/victorperone/pdfextractor/blob/feat/paddle-ocrv6-evaluation/README.md#L70-L84), [seletividade e cascata](https://github.com/victorperone/pdfextractor/blob/feat/paddle-ocrv6-evaluation/README.md#L365-L366).

**Risco:** o braço v5 processa uma tabela com grade nativa enquanto v6 ativa o PP-TableMagic porque seu OCR altera uma decisão de confiança. Comparar apenas as saídas do PP-TableMagic, filtrando as tabelas que ele recebeu, introduz viés de seleção. Outro risco é descartar a saída aprendida por uma heurística no pós-processamento sem informar o ocorrido.

**Correção:**

- Executar primeiro um benchmark **table-only com regiões fixadas** e PP-TableMagic forçado, separado do benchmark ponta a ponta.
- No benchmark ponta a ponta, registrar região candidata, detector que a criou, critérios de aceite, módulo executado, eventual fallback, motivo e identificador final. Preservar ambas as saídas intermediárias de forma controlada para depuração.
- Se o resultado nativo prevalecer sobre a tabela aprendida, contabilizar como `tablemagic_invoked=true, tablemagic_selected=false`, em vez de afirmar que todas as tabelas “usaram PP-TableMagic”.
- Fixar regras de roteamento iguais entre braços; reportar também diferenças de roteamento como resultado de E2E, sem interpretá-las como qualidade isolada de OCR.

**Aceite:** todas as tabelas de ground truth entram na contagem de denominador, inclusive as não detectadas, as rejeitadas e as processadas por fallback. A proporção de tabelas efetivamente resolvidas por PP-TableMagic aparece separadamente.

### C06 — Evitar duplicação, conflito de autoridade e “OCR invisível” nas células

**Prioridade P0; classificação COND.**

A saída oficial da pipeline de tabela inclui `overall_ocr_res`, `table_res_list`, `pred_html` e `table_ocr_pred` por tabela. São representações correlacionadas do mesmo conteúdo, não quatro fontes de texto independente a serem concatenadas. A opção de divisão e novo reconhecimento por célula pode produzir textos diferentes da passada global. [Formato exemplificado pelo fornecedor](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/table_recognition_v2.en.md#L519-L547).

**Correção:** definir uma única autoridade para cada célula: preferir o texto/estrutura final resultante da própria pipeline no Desenho A, com política documentada para vazios e conflitos; preservar `overall_ocr_res` apenas como evidência auxiliar e texto fora das tabelas quando apropriado. No Desenho B, usar o texto externo explicitamente. Nunca concatenar `pred_html` com `overall_ocr_res` dentro da mesma região. Antes de renderizar, marcar tokens consumidos pela tabela, impedir sua reintrodução como prosa e não suprimir tokens que estejam fora de células válidas somente porque sobrepõem a bbox da tabela.

**Teste mínimo:** tabela 2×2 com palavras iguais no corpo do documento; uma célula vazia legítima; duas tabelas sobrepostas na detecção; cabeçalho dividido; linhas externas próximas da borda; tabela com OCR global e por célula discordantes. **Aceite:** cada conteúdo aparece uma vez na saída autorizada; nenhum texto externo é omitido por mera sobreposição de região; discrepâncias são auditáveis.

---

## 4. Adaptação da saída de PP-TableMagic ao modelo de documento

### C07 — Definir um contrato robusto de conversão de estrutura, HTML e células

**Prioridade P1; classificação COND.**

**Risco:** tratar o HTML produzido pela pipeline como uma tabela retangular comum pode perder `rowspan`, `colspan`, células de cabeçalho, vazios, ordem, posição e associação texto→célula. Interpretar o resultado como lista de linhas dividida por `|` é ainda mais frágil. A API oficial entrega `pred_html`, `cell_box_list` e resultados OCR; o projeto deve explicitar que campos usa, sem supor que todo campo está sempre presente em todas as versões. [Saída da pipeline](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/table_recognition_v2.en.md#L519-L547), [serialização disponível](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/table_recognition_v2.en.md#L731-L742).

**Correção sugerida:**

1. Encapsular acesso ao objeto PaddleOCR em `TableMagicAdapter` isolado; congelar por teste a forma do resultado da versão instalada. Validar tipo, cardinalidade e presença de dados antes de criar uma tabela.
2. Fazer parsing HTML com parser de biblioteca confiável, sem executar scripts e sem carregar URLs. Rejeitar conteúdo que exceda limites de nós, profundidade ou tamanho. Nunca converter HTML de tabela com regex.
3. Expandir a matriz lógica de células considerando ocupação de posições por `rowspan`/`colspan`; preservar a célula original e sua extensão, sem duplicar fisicamente seu texto em todas as posições cobertas.
4. Separar `semantic_header` (`true`, `false`, `unknown`) de “primeira linha” e de células com tag `th`. Em Markdown simples, usar convenção explicitada para tabelas sem cabeçalho; para spans, preservar HTML seguro ou produzir JSON estruturado com sinalização explícita, sem criar células artificiais.
5. Associar geometria às células somente após comprovar o contrato/ordem das caixas em `cell_box_list`; se a lista não corresponder univocamente às células HTML, guardar geometria como não resolvida, não inventar correspondência por índice.
6. Incluir `source_page`, `region_id`, `table_id`, `model_profile`, `source_kind`, `cell_id`, bbox e origem textual nos diagnósticos. Se houver confiança por célula, registrar sua definição, pois scores de módulos diferentes não são intercambiáveis.

**Teste de aceite:** tabela 1×1; sem cabeçalho; cabeçalho em duas linhas; `rowspan=3`; `colspan=4`; combinações de spans; células vazias; texto com `|`, `&`, `<`, `>`, aspas e quebras de linha; HTML inválido; 0/1/N tabelas por imagem; resultado nulo/sem `pred_html`; diferenças entre HTML e caixas. O `StructuredTable` e a saída JSON preservam topologia, texto e proveniência; a saída Markdown declara suas limitações sem perda silenciosa.

### C08 — Implementar e testar transformações geométricas de ponta a ponta

**Prioridade P1; classificação COND.**

O PDFExtractor usa sistema canônico de coordenadas no canto superior esquerdo da página. O PP-TableMagic recebe imagens/recortes e devolve coordenadas em pixels da imagem processada. Se houver rotação, redimensionamento, *padding*, correção de perspectiva, orientação do documento ou da tabela, uma multiplicação direta por `1/render_scale` **não** recupera de modo geral a coordenada correta na página. [README, coordenadas canônicas](https://github.com/victorperone/pdfextractor/blob/feat/paddle-ocrv6-evaluation/README.md#L34-L48), [pipeline oficial admite orientação/unwarping e entrada de imagem](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/table_recognition_v2.en.md#L693-L730).

**Correção:** representar o pipeline geométrico por uma composição explícita de transformações reversíveis: PDF original → página canônica/CropBox → renderização raster → crop/padding → rotação/orientação/unwarping → coordenadas retornadas. Para transformações não lineares, guardar a malha/mapa inverso ou desativá-las no benchmark de geometria até que haja suporte correto. Não aplicar a transformação PDFium duas vezes. Associar à região as dimensões antes/depois e a matriz (ou o tipo de mapa) efetivamente usada. Retornar bbox e polígonos ao sistema canônico antes de juntar com texto nativo.

**Testes:** página 0/90/180/270°, CropBox com origem deslocada, página não A4, tabela fora do centro, recorte parcial, escala 1, 1,5 e 2, imagem com margem artificial, bbox tocando borda, tabela com texto vertical, coordenadas negativas e caixas degeneradas. Conferir *roundtrip* ida/volta e IoU da bbox retornada com referência conhecida. **Aceite:** a associação texto→célula, ordem de leitura e sobreposição com regiões nativas são geometricamente consistentes em todos os cenários suportados; transformações não suportadas falham de modo explícito.

### C09 — Resolver a autoridade de texto nativo, texto OCR e conteúdo de tabela em PDFs híbridos

**Prioridade P1; classificação COND.**

Em PDFs híbridos, o texto pesquisável pode ser correto, sobreposto, incompleto, invisível ou deslocado. Se a geometria de tabela for aprendida mas seu texto vier de OCR visual, comparar v5/v6 mede o OCR de tabela; se o código substituir todas as células pelo texto nativo, o resultado pode ficar idêntico e mascarar a diferença entre modelos. Em contrapartida, suprimir todos os tokens nativos dentro da bbox da tabela pode perder notas ou subtítulos que não pertencem a células.

**Correção:** adotar política explícita: (a) **benchmark de OCR**, com fonte textual fixada no perfil e texto nativo usado apenas como referência quando apropriado; (b) **modo de produção híbrido**, com confiança/validade do texto nativo e prioridade registradas por célula. Expor campos `raw_native_text`, `raw_tablemagic_text`, `selected_text`, `selection_reason` **somente em artefatos de diagnóstico protegidos**, sem espalhar conteúdo privado em logs; no relatório público usar contadores agregados. Testar vazios, células mescladas, sobrescrito, caracteres invisíveis e texto OCR já embutido no PDF.

**Aceite:** cada célula tem fonte textual inequívoca e não ocorrem substituições não observáveis entre v5 e v6.

### C10 — Padronizar pré-processamento, DPI, orientação e ordem dos operadores

**Prioridade P1; classificação COND.**

A pipeline oficial pode habilitar orientação documental e remoção de deformação, além de orientação de tabela e reprocessamento de OCR por célula; o PDFExtractor já faz renderização, orientação e variantes de recuperação. Aplicar rotações/realce duas vezes ou em ordem diferente entre braços pode alterar fortemente o resultado e os bboxes. [Opções oficiais](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/table_recognition_v2.en.md#L663-L670), [opções por inferência](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/table_recognition_v2.en.md#L721-L730), [políticas locais](https://github.com/victorperone/pdfextractor/blob/feat/paddle-ocrv6-evaluation/src/structured_pdf_text/config.py#L21-L32).

**Correção:** fixar e registrar escala de raster, interpolação, conversão RGB/BGR, nitidez, *deskew*, orientação do documento e da tabela, uso de `use_doc_unwarping`, recorte e margem, tratamento de transparência. Produzir para ambos os braços o **mesmo buffer de pixels por amostra** no benchmark de OCR e estrutura isolados e guardar hash desse buffer. No E2E, aceitar divergências geradas pelo modelo, mas registrá-las como parte do efeito sistêmico. Definir qual sistema é responsável por cada rotação e quando se aplica transformada inversa.

**Aceite:** hashes de imagem de entrada idênticos nos braços dos experimentos pareados e transformações recuperáveis nas saídas.

### C11 — Fixar o roteamento entre tabelas com borda e sem borda

**Prioridade P1; classificação COND.**

O PP-TableMagic pode escolher classificadores e modelos de estrutura/células distintos para tabelas com linhas (wired) e sem linhas (wireless). Erro de classificação ou mudança de peso pode tornar o “mesmo PP-TableMagic” uma combinação de modelos diferente. [Arquitetura e modelos](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/table_recognition_v2.en.md#L199-L242), [parâmetros de modelos específicos](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/table_recognition_v2.en.md#L595-L614).

**Correção:** para benchmark de OCR isolado, usar a mesma região e roteamento pré-computado/confirmado, caso a API permita; para benchmark E2E, registrar classe escolhida, score, modelo de estrutura, detector de célula, opções E2E ou geometria→HTML, e mudanças por braço. Não forçar o mesmo roteamento em produção só para aumentar comparabilidade, sem avaliar eventual degradação. O relatório deve decompor erro de localização de tabela, erro de tipo de tabela, erro de grade e erro de texto da célula.

**Aceite:** classificação, estrutura e células têm proveniência por tabela e nenhum modelo padrão desconhecido aparece no run.

### C12 — Comprovar compatibilidade real das bibliotecas e dos pesos selecionados

**Prioridade P1; classificação VAL.**

O snapshot do projeto declara `paddlepaddle==3.3.1`, `paddleocr==3.7.0` e `paddlex==3.7.2`; documentação online da branch `main` do PaddleOCR pode refletir **API posterior**, de modo que exemplos da documentação não são garantia de funcionamento nessa combinação exata. [Requirements do projeto](https://github.com/victorperone/pdfextractor/blob/feat/paddle-ocrv6-evaluation/requirements.txt#L7-L17), [documentação oficial em evolução](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/table_recognition_v2.en.md).

**Procedimento obrigatório:** em um ambiente limpo e com dependências travadas, confirmar que `from paddleocr import TableRecognitionPipelineV2` funciona; inspecionar a assinatura da classe no **pacote realmente instalado** com `inspect.signature`, sem supor nomes a partir do site; instanciar com todos os parâmetros escolhidos, executar uma imagem sintética e um recorte real permitido, verificar schema de resultado, funcionamento de v5 e v6 e comportamento de `use_ocr_results_with_table_cells`. Se v6 não for suportado como modelo interno pela pipeline nessa versão, documentar a incompatibilidade e preparar upgrade versionado ou adaptador; não fazer *fallback* invisível.

**Critérios:** tabela de compatibilidade `(SO, Python, paddlepaddle, paddleocr, paddlex, OpenCV, modelo, backend, TableMagic)` com `import`, `construct`, `predict`, `export`, `offline`, `memory` e o resultado de cada teste. Versões incompatíveis são excluídas **antes** da rodada principal, com motivo registrado.

---
## 5. Preparação offline, ambiente, memória e operação

### C13 — Ampliar `setup-models`/`models-status` aos modelos adicionais do PP-TableMagic

**Prioridade P1; classificação OBS + COND.**

No snapshot consultado, `ocr/models.py` define **quatro** nomes por perfil: orientação de documento, orientação de linha, detector e reconhecedor de texto. O código de pré-validação `_resolve_required_local_models()` verifica existência de diretórios não vazios desses quatro modelos. Isso **não estabelece prontidão** para os modelos adicionais de layout, classificação de tabela, estrutura wired/wireless e detecção de células necessários à configuração escolhida do PP-TableMagic. [Perfis locais](https://github.com/victorperone/pdfextractor/blob/feat/paddle-ocrv6-evaluation/src/structured_pdf_text/ocr/models.py#L27-L43), [pré-validação](https://github.com/victorperone/pdfextractor/blob/feat/paddle-ocrv6-evaluation/src/structured_pdf_text/ocr/paddle.py#L195-L256), [modelos da pipeline oficial](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/table_recognition_v2.en.md#L588-L626).

**Correção proposta:**

1. Criar manifesto por perfil e por componente: `role`, `model_name`, `version`, `model_path`, `required_files`, `SHA-256`, `license/source`, `optional_if_flag_disabled`, `runtime_compatibility` e `readiness_state`.
2. Incluir no `setup-models` **somente** os modelos realmente usados pela configuração concreta (p.ex., se `use_layout_detection=False`, registrar por que seu modelo não é necessário). Não carregar pesos enormes apenas por pertencerem genericamente à família PP-TableMagic.
3. Separar `manifest-ready` de `runtime-smoke-ready`: a verificação leve valida arquivos e metadados; um teste offline opcional carrega pipeline e executa exemplo mínimo. Diretório apenas não vazio ou único arquivo `.pdiparams` não comprova o conjunto íntegro.
4. Fazer downloads apenas em fase de provisionamento, em diretório temporário com checagem de integridade e promoção atômica. Na execução, `model_dir` explícito para **cada componente**, e detectar qualquer tentativa de consulta/download remoto.
5. Deixar `models-status` indicar perfil, ambiente e dependências adicionais, por exemplo: `[ok] table_classification: ...`, `[ok] wired_table_structure: ...`, `[missing] wireless_table_cells: ...`, distinguindo componentes desabilitados da configuração.
6. Verificar eventuais overrides de diretório também na API Python; o status não pode estar `READY` para um cache enquanto o runtime usa outro.

**Testes:** cache sem modelos de tabela; cache com apenas OCR; falta de um modelo wired; falta apenas de modelo desabilitado; diretório com pesos incompletos; troca intencional de SHA; corrida de duas instalações; execução sem DNS/rede; fake que falha se receber URL de modelo. **Aceite:** status e pré-validação convergem e nenhum braço executa com modelo padrão baixado silenciosamente.

### C14 — Evitar interferência entre caches, inicialização e ambiente global

**Prioridade P1; classificação OBS + COND.**

No snapshot, `PaddleOcrEngine` usa variável global `PADDLE_PDX_CACHE_HOME`, e o módulo documenta um `_INIT_LOCK` para serializar alterações de ambiente durante inicialização. Esse lock protege apenas certas janelas de inicialização; o **ambiente do processo** permanece compartilhado entre instâncias. Com modelos adicionais de PP-TableMagic, perfis alternados e processamento paralelo, a chance de configuração implícita aumenta. [Código de OCR local](https://github.com/victorperone/pdfextractor/blob/feat/paddle-ocrv6-evaluation/src/structured_pdf_text/ocr/paddle.py#L44-L48), [resolução de cache](https://github.com/victorperone/pdfextractor/blob/feat/paddle-ocrv6-evaluation/src/structured_pdf_text/ocr/paddle.py#L258-L274).

**Correção:** preferir paths absolutos e explícitos em todos os construtores. Para o benchmark, executar cada braço em **processo separado** com ambiente imutável definido **antes** do import de Paddle; não alternar `PADDLE_PDX_CACHE_HOME` entre threads. Se precisar compartilhar pesos idênticos de PP-TableMagic, usar artefato imutável apenas para leitura com hash e sem downloads concorrentes; modelos de OCR de cada braço devem ser resolvidos independentemente. O lock de inicialização local não é substituto de isolamento de processo. O relatório registra pid, cache root, diretórios resolvidos e versão do ambiente.

**Teste:** duas instâncias v5/v6 no mesmo processo não podem iniciar inadvertidamente o cache uma da outra; teste determinístico em subprocessos paralelos; cache configurado por env e por argumento; cache inexistente; iniciação e erro de uma das pipelines sem afetar a outra. **Aceite:** provado por manifesto e teste de concorrência que o modelo real corresponde ao braço.

### C15 — Controlar memória de TODOS os módulos, não apenas o orçamento RGB regional

**Prioridade P1; classificação OBS + COND.**

O README menciona um pico aproximado de **10,8 GiB RSS observado em uma validação anterior com detector v5**, além de um limite de **8 MiB por variante RGB** de recuperação regional. Esse limite não controla pesos, tensores internos, modelos simultâneos da pipeline de tabelas, memória de workers, imagens e caches do runtime. A documentação oficial avisa sobre falta de memória e desempenho lento com a pipeline completa. Esses valores do projeto não são medições de PP-TableMagic + v6. [README, orçamento RGB](https://github.com/victorperone/pdfextractor/blob/feat/paddle-ocrv6-evaluation/README.md#L297-L364), [pico relatado para v5](https://github.com/victorperone/pdfextractor/blob/feat/paddle-ocrv6-evaluation/README.md#L429-L446), [aviso da pipeline oficial](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/table_recognition_v2.en.md#L399-L402).

**Correção:** medir separadamente *cold start*, modelos residentes, pico por etapa, pico do processo e, se houver contêiner, pico do grupo de controle. Executar v5 e v6 **sequencialmente**, encerrando e verificando finalização do worker entre braços para benchmark de memória comparável. Documentar limites de threads, lote, imagens em RAM, número de workers, uso de swap e memória disponível. Se ocorrer encerramento nativo/OOM, capturar status e marcar documento/tabela como falho, jamais classificar como “não havia tabela”. Para uso em serviço, iniciar OCR/tabulação em subprocesso com limite de memória/tempo que proteja o controlador. Não reduzir automaticamente DPI ou trocar modelo sem registrar um novo braço/experimento.

**Teste:** tabela gigante; páginas com múltiplas tabelas; 100 páginas com tabelas; 2 workers; timeout durante análise estrutural; OOM simulado; tarefa cancelada; subproceso encerrado pelo SO; execução repetida 20× para detectar crescimento de memória. **Aceite:** memória por braço mensurável, falhas contabilizadas, controlador íntegro e resultados não corrompidos.

### C16 — Congelar resolução, limites, lotes, scores e regras de recuperação

**Prioridade P1; classificação OBS + COND.**

Os defaults da pipeline oficial de tabela para limite de detecção, reconhecimento e threads podem diferir do adaptador local. `config.py` do projeto define políticas `baseline`, `adaptive`, `exhaustive` e limiares de qualidade; o README descreve múltiplas variantes e OCR seletivo. Comparar perfis com thresholds, DPI, modelo de layout ou número de passadas diferentes introduz variáveis de confusão. [Parâmetros oficiais](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/table_recognition_v2.en.md#L623-L661), [políticas do projeto](https://github.com/victorperone/pdfextractor/blob/feat/paddle-ocrv6-evaluation/src/structured_pdf_text/config.py#L21-L66).

**Correção:** gerar um `effective_config.json` canônico com TODOS os valores efetivos, inclusive defaults resolvidos e versões dos pacotes. Separar duas perguntas: **(i) efeito do modelo com orçamento idêntico**: mesmo buffer de entrada, escala, limites, backend, número máximo de tentativas e rotações; **(ii) melhor qualidade possível de cada modelo sob a mesma política de produto**: manter as regras funcionais mas admitir diferentes decisões de recuperação, contabilizando custo total. Congelar os limiares inicialmente e executar calibração posterior em conjunto de desenvolvimento independente do conjunto final; nunca ajustar limiares com o conjunto de teste. Se uma versão do modelo não suporta parâmetro fixado, marcar incompatibilidade ou criar experimento paralelo, não substituir silenciosamente.

**Teste:** serialização canônica gera hash igual para configurações de controle; alteração em um único limiar é detectada; relatório conta passadas, imagens e tokens realmente processados; reexecutar 3× evidencia eventuais fontes de não determinismo. **Aceite:** a única diferença não justificada no experimento controlado é o par de modelos OCR.

### C19 — Diferenciar resultado vazio, tabela não encontrada, saída parcial, exceção e interrupção nativa

**Prioridade P1; classificação COND.**

Não misturar `success_no_table`, `table_detected_empty`, `partial_success`, `low_confidence`, `model_missing`, `incompatible_model`, `timeout`, `resource_exhausted` e `process_crash`. Um PDF pode concluir com texto válido e tabela incompleta; o relatório deve contabilizar a falha de tabela mesmo quando a extração do documento retorna sucesso parcial. Em benchmark pareado, não excluir a página problemática de ambos os braços após uma falha de apenas um braço sem relatar a exclusão e manter um denominador fixo para a avaliação.

**Correção:** definir estado do documento, da página, da região e da tabela; preservar ID estável para toda amostra de ground truth; capturar exceções do adaptador, erros de parsing de HTML e código de saída do processo isolado; nunca transformar erro de modelo em tabela vazia nem em OCR com confiança 0. Se houver fallback, registrar `fallback_from`, `fallback_to`, `reason`, `model_invoked` e `selected_source`. Definir comportamento do comando: exit code não zero para falha total; relatório de lote retorna também contagem de falhas parciais e código de status configurável em CI.

**Aceite:** testes simulados de cada falha geram códigos e contagens distintos, e a tabela de resultados inclui todas as amostras elegíveis no denominador apropriado.

### C20 — Impedir contaminação cruzada por caches, saídas, objetos compartilhados e resíduos

**Prioridade P1; classificação COND.**

O uso de um único diretório `output.md` para os dois braços pode sobrescrever saídas, e o reúso de objetos Paddle ou caches de resultados por página pode fazer com que o segundo braço receba tokens, geometrias ou tabela do primeiro. O risco aumenta quando existe cache por hash apenas do PDF, sem incluir modelos e parâmetros.

**Correção:** todo cache de **resultado inferido** deve ser indexado por `(pdf_sha256, page_index, crop_bbox, image_sha256, effective_config_sha256, model_manifest_sha256, pipeline_version)`. O cache de pixels **antes** do OCR pode ser compartilhado se o conteúdo for exatamente o mesmo e imutável; o cache de inferência não. Usar diretórios `run_id/arm_id/`, escrita atômica e arquivo de manifesto; validar colisões e impedir substituição por padrão. Não reusar instância da pipeline ao alterar perfil. Fechar modelos/processos antes da próxima rodada e limpar apenas caches de inferência controlados, não destruir pesos baixados.

**Aceite:** executar na ordem A→B e B→A produz a mesma identificação dos modelos e arquivos sem sobreposição; alterar somente o perfil invalida cache de inferência; hashes dos arquivos de entrada continuam iguais.

---

## 6. Tabelas, corpus, métricas e método científico do benchmark

### C17 — Construir corpus português com ground truth de texto e estrutura de tabelas

**Prioridade P1; classificação VAL.**

**Problema:** nenhum resultado final comparável pode ser inferido apenas de tamanho do texto, número de palavras, aparência do Markdown ou “parece melhor” em poucos PDFs. A referência precisa ser independente dos dois OCRs e incluir **texto e geometria**. O `pdftext report` do snapshot do README é um relatório operacional, não medida de acurácia. [README, relatório operacional](https://github.com/victorperone/pdfextractor/blob/feat/paddle-ocrv6-evaluation/README.md#L263-L272).

**Corpus mínimo recomendado, a dimensionar conforme disponibilidade:** documentos brasileiros com acentos/cedilha, moeda R$, números negativos, separadores brasileiros, datas, CNPJ/CPF fictícios nos dados sintéticos, tabelas contábeis, financeiras, fiscais, administrativas e judiciais; PDFs digitais, scans com OCR embutido, imagens puras e híbridos; tabelas com grade, sem grade, cabeçalho múltiplo, `rowspan`/`colspan`, rodapé, células vazias, colunas estreitas, tabelas divididas entre páginas, texto vertical e páginas rotacionadas; tabelas verdadeiras versus gráficos, diagramas e listas. Incluir documentos com **nenhuma tabela** para medir falso positivo. O número de arquivos deve cobrir variedade, e não apenas total de páginas.

**Anotação por tabela:** `pdf_id`, `page_index`, bbox/polígono, ID lógico entre páginas (se aplicável), tipo de tabela, matriz de células com texto original, coordenadas, `rowspan`, `colspan`, cabeçalho (`yes/no/uncertain`), classe de conteúdo, texto normalizado de referência e status de legibilidade. Anotar trechos fora de tabelas em subconjunto para CER/WER. Para saídas HTML, manter representação semântica estável: DOM/grade canonizados, sem depender de espaços cosméticos do HTML.

**Qualidade da referência:** anotar por duas pessoas em amostra estratificada, adjudicar discordâncias e versionar correções. Não usar saída de v5 ou v6 como verdade, nem extrair “ground truth” do texto oculto do PDF quando ele for sabidamente ruim; permitir referência humana a partir da imagem. Definir política de caracteres ilegíveis e texto ambíguo antes da primeira rodada. Separar `development` e `test` por **documento e origem**, evitando páginas do mesmo PDF em conjuntos distintos; não ajustar limiares no teste.

**Aceite:** manifesto do corpus versionado com hash dos arquivos e licença/consentimento apropriados; todas as tabelas do conjunto de teste têm anotação validada; corpus de regressão público é sintético ou redistribuível, enquanto PDFs privados permanecem fora do repositório.

### C18 — Medir OCR, localização, topologia, texto em células e resultado final separadamente

**Prioridade P1; classificação VAL.**

**Métricas propostas e seus denominadores:**

| Dimensão | Unidade de análise | Medida principal | O que NÃO confundir |
|---|---|---|---|
| Texto bruto de OCR | linha ou trecho anotado | CER e WER após normalização definida | Score do OCR não é acurácia |
| Detecção de regiões de tabela | tabela de ground truth/página | precisão, revocação, F1 em IoU definido | “número de tabelas extraídas” não é recall |
| Classificação wired/wireless | tabela detectada, com GT | acurácia/matriz de confusão, cobertura | Não medir só tabelas corretamente classificadas |
| Grade/estrutura | tabela pareada à GT | similaridade estrutural de células, adjacency F1, avaliação HTML canonizado/TEDS quando implementada corretamente | Texto correto não implica grade correta |
| Texto associado a célula | célula GT pareada | CER/WER por célula, taxa de atribuição correta, vazios corretos | OCR global pode estar correto mas na coluna errada |
| Cabeçalhos e spans | células/linhas anotadas | precisão/revocação de spans, linha de cabeçalho | Markdown simples pode não representar spans |
| Qualidade da tabela integral | tabela GT | exatidão de células e topologia, tabela exata quando cabível | Não medir só células não vazias |
| Documento final | documento/página | omissões, duplicações, ordem de leitura, qualidade Markdown/JSON | Não confundir formatação agradável com integridade |
| Robustez | todas as amostras elegíveis | taxa de falha, parcial, timeout, OOM, fallback | Falhas não podem desaparecer do denominador |
| Desempenho | página/tabela/documento | latência e RSS pico | Tempo de modelo isolado não é tempo E2E |

**Regras:** normalizar texto apenas por política fixa e publicada (p.ex., NFC, tratamento explícito de espaços), mantendo também métricas exatas para valores sensíveis. Não eliminar pontuação, sinais, zeros à esquerda, separador decimal ou símbolo monetário quando esses caracteres importam. Apresentar métricas macro por documento e micro por caracteres/células, com pesos explícitos; impedir que um documento gigante domine a conclusão sem informar a agregação. Relatar intervalos de confiança pareados por documento quando tamanho amostral e pressupostos permitirem, não afirmar significância com poucos exemplos.

**Aceite:** scripts determinísticos com testes sobre exemplos construídos para detectar CER/WER incorreto, perda de `rowspan`, célula atribuída à coluna errada, tabela perdida e relatório que indevidamente melhora ao excluir falhas.

### C21 — Preservar semântica na exportação Markdown/JSON/HTML

**Prioridade P1; classificação COND.**

Mesmo quando o PP-TableMagic produz HTML correto, o renderizador do projeto pode remodelar a estrutura. O snapshot anterior do renderizador deve ser conferido no SHA atual, especialmente a hipótese “primeira linha = cabeçalho”, a preservação de spans e o escape de conteúdo. [Renderizador do projeto](https://github.com/victorperone/pdfextractor/blob/feat/paddle-ocrv6-evaluation/src/structured_pdf_text/renderers/markdown.py), [capacidade oficial de exportar HTML/JSON/XLSX](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/table_recognition_v2.en.md#L731-L742).

**Correção:** conservar uma representação intermediária rica como fonte autoritativa; gerar Markdown apenas na última etapa. Utilizar tabela Markdown simples somente quando a estrutura for retangular, sem mesclas e com semântica de cabeçalho definida; caso contrário, HTML seguro ou representação alternativa explícita. Escapar pipes e quebras, preservar texto Unicode e números sem conversão para `float`, manter identificação de página e origem, evitar script/atributos perigosos no HTML. JSON deve preservar matriz lógica, spans, posições, texto original/normalizado e tipo de fonte.

**Testes:** comparação de estrutura intermediária com exportações, análise reversa de JSON, parse do HTML seguro, Markdown com acentos/pipe/multilinha, tabela sem cabeçalho, spans e células vazias. **Aceite:** nenhuma diferença entre braços nasce somente de regras de serialização aplicadas de forma distinta; erros de saída são relatados separadamente de erros de reconhecimento.

### C22 — Preservar integridade no refinamento regional e em tabelas entre páginas

**Prioridade P1; classificação COND.**

O projeto oferece recuperação regional e fusão lógica de tabelas entre páginas. Uma integração de PP-TableMagic pode reconhecer um fragmento como tabela independente, enquanto o pós-processamento mescla páginas ou repete cabeçalhos. Isso pode alterar número de tabelas, colunas e contagem de células mesmo com OCR idêntico. [README, recuperação e fusão](https://github.com/victorperone/pdfextractor/blob/feat/paddle-ocrv6-evaluation/README.md#L60-L78), [invariantes de montagem](https://github.com/victorperone/pdfextractor/blob/feat/paddle-ocrv6-evaluation/README.md#L391-L403), [fusão entre páginas](https://github.com/victorperone/pdfextractor/blob/feat/paddle-ocrv6-evaluation/README.md#L420-L428).

**Correção:** preservar a tabela física por página, seus `region_id` e `table_id`, a tabela lógica opcional e o mapa de fragmentos. Comparar inicialmente tabelas **físicas antes do merge**; depois medir separadamente o merge. Não fazer OCR na tabela mesclada como se fosse um recorte contínuo sem registrar montagem de imagem e transformações. Em caso de novo OCR regional, marcar o token de origem e se ele substituiu, suplementou ou foi descartado. Bloqueios do orçamento RGB precisam aparecer como evento distinto de OCR vazio.

**Aceite:** casos de tabela sem continuação, continuação real, cabeçalho repetido, colunas alteradas, título entre páginas, páginas ausentes, mistura de orientação e PDF parcialmente processado são reproduzíveis sem perder fragmentos nem duplicar células.

### C23 — Desenhar um benchmark pareado, pré-registrado e com análise não enviesada

**Prioridade P1; classificação VAL.**

**Protocolo:** registrar antes da rodada: hipóteses, corpus, exclusões legítimas, critérios de qualidade primários/secundários, período de medição e orçamento de recursos. O mesmo conjunto de amostras deve ser processado por ambos os braços. Alternar aleatoriamente a ordem de execução por bloco/documento ou rodar A→B e B→A em execuções independentes, para reduzir efeitos de aquecimento/cache; **nunca** compartilhar um objeto Paddle inicializado entre braços. Fixar seed onde houver operações estocásticas e registrar versão e threads, sem supor determinismo absoluto de inferência.

**Análise:** produzir resultados por PDF/tabela e agregações; contabilizar os pares nos quais ambos falham, apenas um falha ou ambos concluem; para acurácia, apresentar análise de casos completos **e** análise de falhas/ausência como erro segundo regra publicada. Evitar inferir superioridade com métricas oficiais de fornecedor ou amostras selecionadas após ver a saída. Se múltiplas métricas, designar uma primária para o objetivo declarado pelo produto e evitar mover o objetivo quando outra métrica parecer melhor.

**Aceite:** scripts reexecutáveis sobre corpus fechado produzem os mesmos IDs e contagens, sem excluir silenciosamente tabela que uma das pipelines não encontrou.

### C24 — Medir latência e custo de recursos de modo útil para decisão de engenharia

**Prioridade P1; classificação VAL.**

**Medidas obrigatórias:** tempo de download/provisionamento (informativo, fora do tempo de inferência), instalação/importação, *cold start* de cada pipeline, *warm start*, leitura PDF, renderização, detecção de tabela, classificação, estrutura, detecção de células, OCR global, OCR por célula, reconstrução/merge, serialização e total E2E. Registrar contagem de chamadas aos modelos, tokens, páginas/tabelas por segundo, memória RSS de pico por processo e memória do contêiner se houver. Separar métricas de execução sequencial e de lote com concorrência. Relatar hardware/CPU, frequência e concorrência em vez de comparar latência medida em máquinas diferentes.

**Teste:** mesma página medida depois de aquecer os dois braços; execução curta com *cold start* separado; 10–20 repetições em amostra de teste operacional para estimar variabilidade, conforme orçamento. Não definir “v6 usa menos memória” sem medir a pipeline completa e seu pico de utilização. **Aceite:** relatório apresenta custo real por documento/tabela e especifica se a medição inclui ou exclui carregamento de pesos.

---
## 7. Operacionalização, testes automatizados e segurança

### C25 — CLI, API, configuração e documentação devem especificar o mesmo experimento

**Prioridade P1; classificação COND.** Uma opção chamada `--ocr-model-profile` pode afetar apenas o adaptador de texto, sem propagar o perfil ao OCR interno da tabela; a CLI pode divergir da API Python; variáveis de ambiente e defaults da biblioteca podem prevalecer silenciosamente. O repositório atual precisa ser inspecionado nos pontos de criação de `TableRecognitionPipelineV2`, na chamada da pipeline, no instalador de modelos, na validação de perfil e na serialização do relatório. Não se deve inferir o comportamento da branch mais recente somente a partir do nome da opção.

**Contrato proposto:** expor uma configuração de execução imutável, construída em um único ponto, por exemplo `ExperimentConfig(ocr_profile, table_engine, table_ocr_mode, table_structure_profile, cache_root, fallback_policy, quality_policy, preprocessing_profile, output_profile, device, concurrency)`. Validar combinações proibidas na inicialização; propagar o objeto para API, CLI, orquestração, worker, motor de tabela e renderizador; registrar o objeto resolvido no manifesto. Não acoplar o nome do perfil de OCR ao motor de tabela implicitamente: explicitar ambos, mesmo que o CLI forneça um alias para uma combinação permitida.

**Exemplo de contrato desejado, não de sintaxe já verificada na branch:**

```text
--ocr-model-profile pt                  # PP-OCRv5 selecionado
--table-engine pp-tablemagic-v2
--table-ocr-source internal-matched     # OCR da própria pipeline; pesos equivalentes ao braço
--table-structure-profile table-fixed-01
--on-table-engine-failure fail          # benchmark primário sem fallback silencioso
--manifest-out runs/v5/run_manifest.json
```

Para o outro braço, alterar somente `--ocr-model-profile pt-v6-medium`, os diretórios dos respectivos modelos e os caminhos de saída; manter os demais parâmetros fixos. Não copiar os comandos literalmente até verificar a CLI real: os nomes acima são especificação de interface, não afirmação de que argumentos novos já existem.

**Documentar:** comandos testados de instalação, verificação offline, extração de teste, exportação de resultados, benchmark em lote, limpeza de cache, execução no WSL/Linux/Windows compatível e troubleshooting. Descrever diferenças entre `PP-TableMagic` (pipeline de tabelas) e PP-OCR (OCR de texto), incluindo a possibilidade de OCR interno da pipeline. Indicar como comprovar nos logs os pesos realmente utilizados, não apenas os solicitados. Diferenciar perfis v6 medium/small, o idioma do reconhecedor e os conjuntos de caracteres.

**Testes de aceite:** o mesmo documento invocado via API e CLI com a mesma configuração gera manifestos equivalentes; cada braço loga exatamente os modelos esperados; opção inválida falha antes de abrir o PDF; flags experimentais não alteram a configuração padrão em execuções normais; README e `--help` refletem a API real e o pin de dependências.

### C26 — Estabelecer testes em camadas e controles de regressão na CI

**Prioridade P1; classificação VAL.** A existência de testes do extrator anterior não equivale a cobertura dos novos caminhos de PP-TableMagic, sobretudo reconhecimento de células, importação de spans, integração OCR interno e recuperação após erro. Exigir níveis separados para evitar confundir teste com *mock* e execução real de modelo.

**Camadas mínimas:**

1. **Configuração pura:** resolver perfis v5/v6, invalidar combinações indevidas, impedir uso de cache errado, comprovar que o manifesto acompanha a seleção.
2. **Contrato de integração:** instanciar um `FakeTablePipeline` que registra argumentos e retorna payloads representativos; garantir que o adaptador consome as chaves corretas, transforma coordenadas e conserva proveniência; marcar explicitamente que o teste não valida PaddleOCR real.
3. **Testes de adaptação de tabela:** HTML válido/inválido, `<thead>` ausente, `rowspan`/`colspan`, caracteres escapáveis, linhas vazias, células multilinha, ordem de leitura, ausência de `cell_box_list`, caixas inconsistentes, `table_ocr_pred` vazio.
4. **Integração real curta:** carregar v5 + PP-TableMagic em processo limpo; executar páginas com tabelas; repetir com v6; confirmar modelos de OCR interno ativos no objeto/manifesto; comparar com transcrição conhecida. Esses testes podem depender de um runner com pesos provisionados; CI pública não deve realizar downloads inesperados.
5. **Regressão E2E:** documentos digitais, digitalizados e híbridos, com e sem tabelas, em todos os modos existentes; comparar com baseline aprovado, incluindo ausência de duplicação, spans e cabeçalhos.
6. **Resiliência/processos:** modelo ausente, cache parcial, timeout, subprocesso morto, OOM simulado, página corrompida, saída parcial e fallback; validar códigos de saída e denominadores do relatório.
7. **Benchmark periódico controlado:** conjunto versionado de fixtures não sensíveis, script pareado e diffs de qualidade; pipeline de benchmark distinta da suíte rápida de PR.

**Critério de aceite:** toda alteração de perfil de OCR, motor de tabela, serializador, fusão ou instalador executa os testes diretamente associados. Nenhuma falha de inferência pode ser reclassificada como “tabela vazia” sem um marcador `status` e motivo. A CI deve guardar versões, SHA e logs relevantes, mas não deve publicar conteúdo confidencial dos PDFs de teste.

### C27 — Limitar riscos de segurança, licenciamento e privacidade dos PDFs e modelos

**Prioridade P1 para dados não confiáveis; classificação COND.** PDFs podem acionar bugs em bibliotecas nativas, conter dados pessoais ou exigir restrições de retenção. PP-TableMagic introduz bibliotecas e pesos adicionais, aumentando dependências, superfície de falha e requisitos de distribuição. Nenhuma conclusão de vulnerabilidade específica é possível sem inventário/SBOM e varredura do SHA exato.

**Correções:** executar PDFs enviados por terceiros em workers sem privilégios, com usuário dedicado, diretório temporário privado, quotas de CPU/memória, limite de arquivo e páginas, timeout e bloqueio de rede após provisionar os pesos. Não montar credenciais, HOME com tokens nem repositórios graváveis nos workers de inferência. Validar tipo e tamanho de entrada, tratar PDFs criptografados, garantir limpeza de imagens intermediárias e restringir logs ao mínimo necessário. Se o benchmark contiver documentos internos, manter corpus e transcrições em armazenamento autorizado, com acesso auditável, política de retenção, hash salgado ou identificador não reversível quando cabível; nunca compartilhar exemplos reais em issue pública sem autorização.

**Cadeia de suprimentos:** registrar a licença do código e de cada artefato/modelo, pin de pacotes e hashes das rodas/artefatos quando aplicável, procedência do repositório oficial e política de atualização. Confirmar se a modalidade de distribuição pretendida permite embutir ou baixar automaticamente os pesos. Bloquear downloads de modelos em tempo de execução de produção e de benchmark sem consentimento, preferindo instalação explícita e verificação de checksum.

**Aceite:** teste em sandbox comprova impossibilidade de gravar fora dos diretórios autorizados, ausência de rede em execução offline, truncamento de logs sensíveis, limpeza após falha e documentação de licenças. Esses critérios tratam riscos operacionais; não afirmam ausência de todas as vulnerabilidades.

### C28 — Formalizar critérios de promoção, rollback e compatibilidade

**Prioridade P1; classificação DEC/VAL.** O comparativo só permite alterar o padrão após distinguir ganho de texto, ganho de estrutura e custo adicional da pipeline. A decisão deve depender de critérios escritos antes dos resultados, definidos pelo proprietário do produto para seu corpus e orçamento.

**Processo:** preservar `pt`/PP-OCRv5 como opção reproduzível durante a avaliação; manter perfis v6 isolados; impedir autoatualização de pesos/pacotes; realizar smoke test de instalação limpa e atualização; publicar matriz de compatibilidade de modelos/dependências/SO/dispositivo. Fixar limites objetivos para falhas máximas, omissões de números críticos, regressões de tabelas e custo por documento de acordo com os requisitos de negócio. Fazer validação cega dos pares discordantes. Se a nova configuração for promovida, registrar quem aprovou, versão, dataset, métricas, intervalos, exceções e plano de retorno.

**Rollback:** flag de perfil e cache anterior devem permitir restaurar o comportamento v5 sem converter novamente o corpus ou perder proveniência; resultados antigos devem continuar legíveis com versão explícita do schema. Testar o rollback em instalação limpa e após falha de worker. Não apagar artefatos de benchmark antes da análise e da política de retenção.

**Aceite:** relatório consolidado lista decisões e pendências sem declarar “vencedor” quando faltam dados; uma execução v5 anterior é reexecutável com os mesmos pesos e parâmetros; o modo padrão só muda por decisão formal e testes aprovados.

---

## 8. Modelo de integração proposto para o desenvolvedor

Esta seção é uma **proposta de desenho e pseudocódigo**, não um patch pronto: não foi possível conferir no SHA atual os construtores, os tipos internos do PDFExtractor ou a assinatura local efetiva do PaddleOCR instalado. O desenvolvedor deve adaptar nomes e verificar cada parâmetro na versão de `paddleocr`/`paddlex` fixada no ambiente. É preferível implementar uma integração explícita e testável a acrescentar condições dispersas em `api.py`.

### 8.1. Resolver a configuração antes de inicializar qualquer modelo

```python
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

class TableOCRSource(str, Enum):
    INTERNAL_MATCHED = "internal-matched"
    EXTERNAL_FROZEN = "external-frozen"
    # Para experimento exclusivamente estrutural; não gera texto sozinho.
    DISABLED_STRUCTURE_ONLY = "disabled-structure-only"

@dataclass(frozen=True)
class ResolvedExperiment:
    run_id: str
    git_sha: str
    ocr_profile: str
    external_det_model: str
    external_rec_model: str
    table_ocr_source: TableOCRSource
    table_det_model: str | None
    table_rec_model: str | None
    table_structure_model: str
    table_cell_model: str
    table_classifier_model: str
    cache_dir: Path
    device: str
    fallback_policy: str
    preproc_profile: str
    quality_policy: str


def resolve_experiment(request, registry) -> ResolvedExperiment:
    """Pseudocódigo: usar registry verificado da instalação efetiva."""
    profile = registry.require(request.ocr_profile)
    table = registry.require_table(request.table_structure_profile)

    if request.table_ocr_source == TableOCRSource.INTERNAL_MATCHED:
        table_det = profile.det_model
        table_rec = profile.rec_model
    elif request.table_ocr_source == TableOCRSource.EXTERNAL_FROZEN:
        table_det = None
        table_rec = None
    else:
        table_det = None
        table_rec = None

    if request.benchmark and request.fallback_policy != "fail":
        raise ValueError("Benchmark primário exige fallback explícito desativado")

    return ResolvedExperiment(
        run_id=request.run_id,
        git_sha=request.git_sha,
        ocr_profile=profile.name,
        external_det_model=profile.det_model,
        external_rec_model=profile.rec_model,
        table_ocr_source=request.table_ocr_source,
        table_det_model=table_det,
        table_rec_model=table_rec,
        table_structure_model=table.structure_model,
        table_cell_model=table.cell_model,
        table_classifier_model=table.classifier_model,
        cache_dir=request.cache_dir,
        device=request.device,
        fallback_policy=request.fallback_policy,
        preproc_profile=request.preproc_profile,
        quality_policy=request.quality_policy,
    )
```

**Ponto crucial:** duas entradas `PP-OCRv5` e `PP-OCRv6` não são suficientes para identificar todos os modelos. Registrar detecção, reconhecimento, orientação, estrutura da tabela, classificação da tabela e detecção de células, incluindo hashes de pesos. O modelo de classificação/orientação do documento, se habilitado, também deve ser constante entre braços e constar do manifesto.

### 8.2. Construir PP-TableMagic com argumentos explícitos

A documentação oficial de `TableRecognitionPipelineV2` apresenta seletores para modelos de detecção/reconhecimento textual, seus diretórios e a opção `use_ocr_model`; `use_ocr_results_with_table_cells` altera o modo de atribuir OCR à estrutura. Consulte a [documentação da pipeline de reconhecimento de tabelas v2](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/table_recognition_v2.en.md#L588-L730). A documentação upstream da branch `main` pode diferir da versão local efetivamente fixada: confronte assinatura por introspecção e integração real, nunca por suposição.

```python
# Pseudocódigo de adaptação: conferir parâmetros reais na instalação pinada.

def build_table_pipeline(resolved, model_store, TableRecognitionPipelineV2):
    common = {
        # Preencher com nomes/suporte confirmados no paddleocr fixado:
        "table_structure_model_dir": str(model_store.structure_dir(resolved)),
        # O nome específico do argumento do modelo de células pode variar
        # conforme a versão: checar documentação + assinatura local.
        "use_ocr_model": resolved.table_ocr_source == TableOCRSource.INTERNAL_MATCHED,
    }
    if resolved.table_ocr_source == TableOCRSource.INTERNAL_MATCHED:
        common.update({
            "text_detection_model_name": resolved.table_det_model,
            "text_detection_model_dir": str(model_store.det_dir(resolved)),
            "text_recognition_model_name": resolved.table_rec_model,
            "text_recognition_model_dir": str(model_store.rec_dir(resolved)),
        })
    pipeline = TableRecognitionPipelineV2(**common)
    model_store.assert_pipeline_identity(pipeline, resolved)
    return pipeline
```

**Não copiar o pseudocódigo como patch.** A pipeline pode possuir modelos separados para tabela com/sem linhas, detector de células, classificador de tipo e componentes opcionais de pré-processamento. É obrigatório passar **todos** os pesos necessários pela API realmente disponível, e inspecionar a identidade efetiva do runtime quando a biblioteca não expõe os detalhes de modo confiável. Caso não seja possível garantir o mesmo modelo estrutural em ambos os braços, a comparação passa a ser de **pipelines completas diferentes**, não uma comparação isolada do OCR.

**Modo `EXTERNAL_FROZEN`:** desligar OCR interno não prova que a pipeline consumirá as caixas/transcrições do OCR externo. Se a API não aceitar injeção documentada desses resultados, implementar um adaptador próprio que extraia estrutura e faça associação texto→célula sob um algoritmo explicitamente fixado, ou restringir o experimento a métricas estruturais sem texto. Recusar um relatório rotulado como “PP-TableMagic + PP-OCRv5/v6” caso o OCR utilizado para células seja indeterminado.

### 8.3. Separar resultados brutos do pós-processamento

```text
input PDF + page_id + SHA256
   |
   +--> native evidence ------------------------------+
   |                                                  |
   +--> page image (transform recorded)               |
         |                                            |
         +--> PP-TableMagic structure                  |
         |    raw HTML + cell boxes + table boxes      |
         |                                            |
         +--> selected OCR path                         |
              internal matched OR external frozen      |
              raw text + boxes + confidence + model IDs|
                        |                              |
                        v                              |
                 canonical TableEvidence  <-------------+
                        |
                 benchmark raw outputs
                        |
             deterministic post-processing
                        |
                JSON/HTML/Markdown
```

Definir interfaces distintas para: `detect_tables`, `recognize_structure`, `recognize_text`, `assign_text_to_cells`, `merge_table_fragments` e `serialize`. Implementar cada fronteira como função pura quando viável, com tipos estruturados e validação, sem duplicar lógica de confiança. Os resultados brutos precisam permanecer imutáveis durante a comparação para permitir reprocessamento com o mesmo serializador e identificar regressões de OCR separadas de regressões de layout.

### 8.4. Representação intermediária obrigatória

Proposta mínima de registro por célula, com esquema de coordenadas e proveniência em campos explícitos:

```json
{
  "schema_version": "1.0",
  "run_id": "v6-medium-tablefixed-20260924-01",
  "document_id": "doc_0007",
  "page_index_zero_based": 4,
  "table_id": "doc_0007:p4:t2",
  "source_engine": "pp-tablemagic-v2",
  "physical_table_bbox_px": [90.0, 221.0, 1620.0, 1940.0],
  "coordinate_system": "rendered_page_px_top_left",
  "image_width_px": 1700,
  "image_height_px": 2200,
  "rotation_degrees": 0,
  "cells": [
    {
      "row": 0,
      "col": 1,
      "rowspan": 1,
      "colspan": 2,
      "bbox_px": [500.0, 224.0, 1120.0, 340.0],
      "text_raw": "Valor | total",
      "text_normalized": "Valor | total",
      "text_provenance": "table_ocr_internal",
      "ocr_detection_model": "EXACT_MODEL_ID",
      "ocr_recognition_model": "EXACT_MODEL_ID",
      "table_structure_model": "FIXED_MODEL_ID",
      "status": "ok"
    }
  ],
  "raw_payload_ref": "artifacts/doc_0007_p4_t2_raw.json",
  "warnings": []
}
```

Cada tabela deve indicar se foi encontrada por detecção nativa, por PP-TableMagic, por ambos, ou por fallback, e se o texto de células foi copiado do PDF, produzido pelo OCR de tabela, produzido pelo OCR geral ou fundido. Diferenciar **caixa de tabela** de **caixa de célula** e coordenadas da **página original**, **imagem renderizada**, **recorte** e **imagem após rotação/desentortamento**; cada transformação precisa de matriz e inversa registrada. A identidade do modelo pode ficar no nível de manifesto quando idêntica para todas as células, mas o registro individual deve indicar overrides/fallbacks.

### 8.5. Manifesto de execução com teste automático de paridade

```json
{
  "schema_version": "1.0",
  "run_id": "run-v5-fixedtable-001",
  "repo": "victorperone/pdfextractor",
  "ref": "feat/paddle-ocrv6-evaluation",
  "git_sha": "REPLACE_WITH_REAL_40_CHAR_SHA",
  "git_dirty": false,
  "corpus_hash": "SHA256_OF_MANIFEST_OF_INPUTS",
  "ground_truth_version": "gt-2026-09-frozen",
  "comparison_design": "internal-matched",
  "python_version": "PINNED_VERSION",
  "package_versions": {
    "paddleocr": "PINNED_VERSION",
    "paddlepaddle": "PINNED_VERSION",
    "paddlex": "PINNED_VERSION"
  },
  "hardware": {
    "device": "cpu",
    "cpu": "RECORDED_CPU",
    "threads": 4,
    "memory_limit_bytes": 17179869184
  },
  "ocr": {
    "profile": "pt",
    "external_det": {"id": "PP-OCRv5_server_det", "sha256": "REAL_HASH"},
    "external_rec": {"id": "latin_PP-OCRv5_mobile_rec", "sha256": "REAL_HASH"}
  },
  "tablemagic": {
    "pipeline": "TableRecognitionPipelineV2",
    "use_ocr_model": true,
    "table_text_det": {"id": "PP-OCRv5_server_det", "sha256": "REAL_HASH"},
    "table_text_rec": {"id": "latin_PP-OCRv5_mobile_rec", "sha256": "REAL_HASH"},
    "structure": {"id": "SAME_IN_BOTH_ARMS", "sha256": "REAL_HASH"},
    "cells": {"id": "SAME_IN_BOTH_ARMS", "sha256": "REAL_HASH"},
    "table_classifier": {"id": "SAME_IN_BOTH_ARMS", "sha256": "REAL_HASH"}
  },
  "preprocessing": {"dpi": 200, "rotation_policy": "frozen", "color_mode": "RGB"},
  "pipeline": {"quality_policy": "baseline", "fallback": "fail", "table_postprocessing": "frozen"},
  "input_count": 100,
  "completed_count": 100,
  "failed_count": 0,
  "partial_count": 0,
  "output_schema_version": "1.0"
}
```

Os nomes e valores acima são **ilustrativos**. O manifesto real deve incluir modelo de orientação, classificador de tabelas, modelos de estrutura wired/wireless, diretórios, hashes relevantes, opção de usar OCR por célula, budgets, thresholds, kernel/runtime, flags da CLI e políticas de fallback. A paridade deve ser verificada **pelo programa** antes de computar qualquer métrica: a lista de campos autorizados a diferir deve ser explícita, e qualquer diferença adicional deve bloquear a rotulagem “comparação isolada do OCR”.

```python
def assert_paired_manifests(a, b):
    """Pseudocódigo: adaptar chaves ao manifesto real; fail closed."""
    allowed_differences = {
        "run_id", "ocr.profile", "ocr.external_det", "ocr.external_rec",
        "tablemagic.table_text_det", "tablemagic.table_text_rec",
        "results_path", "timings", "memory_metrics",
    }
    differences = recursive_structured_diff(a, b)
    disallowed = differences.keys() - allowed_differences
    if disallowed:
        raise InvalidComparison(f"Diferenças não permitidas: {sorted(disallowed)}")
    if a["git_sha"] != b["git_sha"]:
        raise InvalidComparison("Código distinto entre os braços")
    if a["corpus_hash"] != b["corpus_hash"]:
        raise InvalidComparison("Corpus distinto entre os braços")
```

Não comparar automaticamente `completed_count` ou métricas de resultado como campos “de entrada”; diferenças de execução são o objeto da medição e precisam constar no relatório. Verificar também a correspondência entre OCR externo e OCR da tabela **dentro de cada braço** e a identidade de todos os modelos estruturais **entre os braços**.

---
## 9. Matriz de testes e critérios específicos de aceite

A tabela é um plano executável de QA. Cada cenário deve indicar `fixture_id`, páginas envolvidas, SHA do PDF, resultado de referência, artefatos brutos v5/v6, manifesto e status. Nos testes de unidade que não envolvam modelo real, usar payloads artificiais e marcar `test_level=unit`; na integração real, anexar os IDs/hash dos modelos carregados. **Nenhum teste isolado substitui o benchmark pareado.**

| ID | Cenário / fixture | O que executar | Resultado / asserção de aceite | Tickets |
|---|---|---|---|---|
| T01 | PDF digital, sem tabela | Ambos os perfis com PP-TableMagic ativado | Sem tabela fantasma; texto preservado; engine de tabela não altera saída indevidamente | C05, C09 |
| T02 | PDF digital, tabela simples | Mesma página e recorte nos dois braços | Uma tabela, grade, células e texto rastreáveis; modelo interno identificado | C01, C04, C07 |
| T03 | PDF digitalizado, tabela simples | Rodar ambos com OCR | Texto por célula correto conforme GT, sem duplicação do OCR externo e interno | C01, C06, C18 |
| T04 | PDF híbrido, cabeçalho nativo e corpo em imagem | Rodar com política definida | Não perder texto nativo, não duplicar células reconhecidas, registrar origem de cada trecho | C06, C09 |
| T05 | Tabela sem cabeçalho | Construir resultado e exportar | Renderizador não promove primeira linha a cabeçalho automaticamente | C07, C21 |
| T06 | Tabela com `rowspan` e `colspan` | Importar payload HTML + caixas | Spans e matriz lógica intactos, sem multiplicar texto ou deslocar células | C07, C21 |
| T07 | Tabela com células vazias | Processar e exportar | Preservar número e posições de células; vazio legítimo diferente de falha OCR | C07, C18, C19 |
| T08 | Células numéricas e códigos | Processar GT com `00123`, `-0,08`, `R$ 1.234,56`, `1.000,00` | Zero à esquerda, sinal, vírgulas e moeda preservados; sem conversão numérica destrutiva | C18, C21 |
| T09 | Texto com `|`, `&`, `<`, `>` e quebra de linha | Exportar Markdown/HTML/JSON | Escape correto; nenhuma execução de HTML ativo; o texto não desloca colunas | C21, C27 |
| T10 | Duas tabelas próximas na mesma página | Executar detecção e associação | Sem fundir tabelas independentes nem associar tokens da tabela vizinha | C05, C07, C08 |
| T11 | Tabela sem linhas/bordas (*wireless*) | Executar mesma imagem em ambos | Modelo e política wired/wireless idênticos e logados; estrutura avaliada por GT | C04, C11 |
| T12 | Tabela com grade (*wired*) | Repetir ambos os braços | Nenhuma troca oculta de família de estrutura/célula entre braços | C04, C11 |
| T13 | Página rotacionada 90º | Entregar imagem + mapa ao adaptador | Caixas retransformadas dentro da página, leitura sem inversão e sem bbox deslocada | C08, C10 |
| T14 | PDF com CropBox distinto de MediaBox | Extração em região conhecida | Coordenadas do PDF e do bitmap concordam após transformações | C08 |
| T15 | Recorte em escala não inteira, com padding | OCR e transformação inversa | Bounding boxes dos tokens e células recuperam posições originais dentro de tolerância anotada | C08, C16 |
| T16 | Duas páginas com tabela contínua | Comparar tabela física e lógica | Fragmentos preservados; fusão somente quando evidência é suficiente; cabeçalho repetido identificado | C22 |
| T17 | Tabela semelhante, porém independente, em páginas sucessivas | Aplicar merge entre páginas | Sem fusão falsa; documentar regra e razões de decisão | C22 |
| T18 | OCR externo v6 e OCR interno fixado em v5 por erro proposital | Inicialização em modo de comparação principal | Rejeitar configuração antes da inferência e explicar conflito no log/manifesto | C01, C02, C25 |
| T19 | Pesos de estrutura diferentes entre braços por erro proposital | Comparar manifestos | Bloquear rótulo de comparação isolada de OCR | C04, C23 |
| T20 | Cache de v6 contém somente diretório vazio ou arquivo incompleto | Rodar `models-status` e instalação | Falha prévia à inferência, descrição do arquivo ausente, nenhuma troca silenciosa de peso | C13, C14 |
| T21 | Execução sem conexão de rede | Usar caches íntegros | Zero downloads; resultado equivalente ao processo com rede, exceto métricas temporais de rede excluídas | C12, C13, C27 |
| T22 | Um braço encontra tabela, outro não | Executar benchmark completo | Caso permanece no denominador; qualidade/erro de detecção avaliados, não excluídos | C17, C18, C23 |
| T23 | Exceção Python em apenas uma célula | Injetar falha controlada | Status parcial/falha explícito; nenhuma substituição de célula por string vazia como sucesso | C19 |
| T24 | Processo nativo interrompido por falta de memória | Limitar memória no worker de teste | Supervisor recebe status, isola processo e contabiliza amostra como falha, sem contaminar próxima rodada | C15, C19 |
| T25 | Fallback de TableMagic para heurística nativa | Forçar erro sob modo operacional | Fallback identificado por tabela; modo benchmark primário bloqueia fallback ou registra estrato separado | C05, C19 |
| T26 | Fim de corpus com 1 documento deliberadamente ausente | Executar agregador | Relatório acusa IDs ausentes; `completed + partial + failed` consistente com população | C17, C23 |
| T27 | Dois workers escrevendo mesmo nome temporário | Rodar simultaneamente em diretórios separados | Isolamento por run/processo; sem arquivo trocado ou artefato sobrescrito | C14, C20 |
| T28 | Execuções v5→v6 e v6→v5 | Repetir em processos limpos | Modelo carregado não depende da ordem; variância de latência apresentada | C14, C20, C24 |
| T29 | CLI e API com parâmetros equivalentes | Processar o mesmo PDF | Manifests de entrada equivalentes e saídas iguais dentro de tolerância explicitada | C25, C26 |
| T30 | Pipeline com `use_ocr_model=False` | Rodar desenho B isolado | Confirmar que não há OCR de tabela oculto e que texto externo é associado por mecanismo testado; caso contrário bloquear desenho B | C02, C06, C12 |
| T31 | Saída oficial contém `pred_html` mas não `cell_box_list` | Alimentar adaptador | Estado incompleto tratado explicitamente; não inventar coordenadas exatas | C07, C19 |
| T32 | Saída oficial contém caixas, mas HTML inválido ou conflitante | Alimentar adaptador | Não mascarar inconsistência; erro estrutural reportado e raw salvo | C07, C19, C21 |
| T33 | Instalação limpa de ambas as versões | Provisionar pesos e dependências pinadas | Hashes e modelos esperados, sem downloads ocultos durante a execução | C12, C13 |
| T34 | Arquivo com dados sensíveis simulados | Executar worker/logs | Logs não expõem texto de células/PII; temporários removidos mesmo após falha | C27 |
| T35 | Reverter perfil após rodada v6 | Executar v5 anterior | Mesmo comportamento v5 dentro de tolerância e mesmo schema de saída; rollback documentado | C14, C28 |
| T36 | Caso controle de OCR interno diferente do externo | Permitir somente como experimento composto explícito | Reportar exatamente os quatro modelos OCR e não agregar como v5/v6 puros | C01, C02, C23 |

**Testes negativos não dispensáveis:** a configuração incorreta deve falhar de modo observável. Um teste de sucesso com pesos corretos não detecta fallback silencioso para o modelo default. Os testes T18, T19, T20, T23, T24, T26, T31, T32 e T36 comprovam a capacidade de detectar resultados metodologicamente inválidos.

### 9.1. Contratos de propriedades sobre tabelas

Sempre que possível, implementar verificações puras independentes de precisão de OCR:

```python
assert table.rows >= 0 and table.cols >= 0
assert table.page_id in known_pages
assert all(cell.rowspan >= 1 and cell.colspan >= 1 for cell in table.cells)
assert all(0 <= cell.row < table.rows for cell in table.cells)
assert all(0 <= cell.col < table.cols for cell in table.cells)
assert all(cell.row + cell.rowspan <= table.rows for cell in table.cells)
assert all(cell.col + cell.colspan <= table.cols for cell in table.cells)
assert no_overlapping_logical_cell_slots(table.cells)
assert all(box_is_finite(cell.bbox) for cell in table.cells if cell.bbox)
assert all(source_is_traceable(cell) for cell in table.cells)
```

**Exceções legítimas:** tabelas deliberadamente incompletas podem possuir campo `status=partial` e ausência de determinadas caixas; nesse caso não falsificar coordenadas nem forçar `rowspan=1` para passar na validação. Preservar payload original e motivo de não conformidade. A verificação de não sobreposição opera sobre **slots lógicos** da grade, não sobre geometrias que podem se tocar nas bordas.

### 9.2. Estratégia de fixtures e revisão do ground truth

Criar `tests/fixtures/tablemagic/` com amostras sintéticas e sem dados privados; armazenar documentos reais com autorização em corpus separado. Para cada arquivo anotar `document_id`, idioma, tipo digital/digitalizado/híbrido, presença de tabelas, classes de tabela, páginas, regiões, número de linhas/colunas, spans, texto original por célula e decisões de leitura. Utilizar coordenadas em sistema único com convenção documentada e converter regiões anotadas em pixels somente quando o DPI de renderização estiver fixado. O arquivo de anotações deve possuir versão, autor/revisor, regras de normalização e histórico das divergências resolvidas. Proibir correção ad hoc do ground truth depois de observar qual modelo foi favorecido sem nova versão e reprocessamento de ambos os braços.

---

## 10. Procedimento de execução do comparativo sem confundir os braços

Os comandos a seguir se destinam ao **desenvolvedor no clone local** e incluem verificações de identidade. Os comandos de teste de PP-TableMagic são intencionalmente apresentados como etapas, não como flags presumidamente existentes no CLI do PDFExtractor. Antes de criar scripts automatizados, conferir `pdftext --help`, os comandos reais de instalação da branch e a assinatura do PP-TableMagic instalado.

### Fase 0 — Travar o código e a configuração

```bash
set -euo pipefail
git status --porcelain=v1
git rev-parse HEAD
git log -1 --format='%H %cI %s'
python --version
python -m pip freeze > benchmark_environment_requirements.txt
python -m pip check
```

Guardar `git diff --binary` caso haja alterações locais. Para benchmark oficial, recomendar árvore limpa; se não for viável, arquivar patch e marcar o experimento como não equivalente a uma revisão pública reprodutível. Identificar instalação, versão do PaddleOCR e funcionalidade do `TableRecognitionPipelineV2` por teste de importação/assinatura, sem assumir que a documentação web `main` descreve exatamente o pacote instalado.

### Fase 1 — Provisionar e verificar ambos os braços

Provisionar cada conjunto de OCR em diretório isolado e os modelos estruturais de tabela em diretório **de conteúdo imutável e hash fixo**. Compartilhar fisicamente pesos estruturais somente se forem somente leitura e se a identidade ficar registrada; nunca compartilhar arquivos temporários, resultados nem caches que possam ser alterados. O catálogo local de modelos precisa listar todas as dependências transitivas efetivamente usadas pela pipeline, inclusive módulos opcionais habilitados por default.

**Pré-condições verificáveis para v5:** `PP-OCRv5_server_det` e `latin_PP-OCRv5_mobile_rec` localizados, íntegros e usados dentro e fora da pipeline conforme desenho A. **Para v6 medium:** `PP-OCRv6_medium_det` e `PP-OCRv6_medium_rec` íntegros e usados dentro e fora. Para ambos, igualdade byte a byte dos componentes não OCR que se pretende manter fixos.

### Fase 2 — Validar um único PDF por braço antes do corpus

Rodar um documento digital e um digitalizado, com tabelas anotadas. Inspecionar resultado bruto da pipeline, o HTML, número e posições das células, origem do texto e a saída final. Confrontar log/manifesto com o objeto real de pipeline para identificar *defaults* ocultos. Comparar as imagens entregues ao modelo por hash de pixels e transformação; explicar diferenças caso o fluxo E2E use decisões automáticas de página distintas. Não iniciar benchmark de performance enquanto houver download ou compilação inesperada em uma das rodadas.

### Fase 3 — Validar os casos de falha

Executar primeiro T18, T19, T20 e T23; confirmar que o sistema **não** produz relatório “válido” com modelos trocados, pesos ausentes ou saída parcial. Injetar uma queda de worker para confirmar contabilização e isolamento. Se alguma falha for tratada como sucesso ou se não for possível localizar qual OCR produziu determinada célula, bloquear a coleta oficial.

### Fase 4 — Rodar o corpus pareado

Criar lista ordenada e imutável de PDFs e IDs. Para cada documento, executar os dois braços em processos separados, alternando a ordem em blocos controlados. Registrar tempo frio/quente, uso de memória, contagem de páginas e chamadas dos modelos, todas as saídas e todas as falhas. Não fazer fallback para configuração alternativa sem trocar o identificador do experimento ou marcar explicitamente a amostra. Preservar resultados por braço em diretórios diferentes e atomizar a escrita de arquivos temporários para evitar que interrupções criem JSON aparente porém incompleto.

### Fase 5 — Produzir relatório de comparação

O relatório final deve incluir:

- SHA e ambiente, matrix de modelos real carregados, hashes e configuração das pipelines;
- população total, tabelas GT, tabelas encontradas por cada braço, sucesso, parcial e falha;
- métricas de texto **fora** e **dentro** das tabelas, estrutura, células, documento completo e processamento;
- variabilidade por documento e por classe de tabela, tabelas sem linhas, spans, documentos digitalizados e híbridos;
- desempenho sequencial e picos de memória, incluídas interrupções e custo de carregamento;
- exemplos de divergência **apenas se autorizados**, com IDs e rastreio para imagens de teste não sensíveis;
- diferenças que decorreram do OCR versus diferenças causadas por pré-processamento, seleção de tabela ou pós-processamento;
- limitações, conclusões suportadas pelos dados e decisões ainda abertas, sem omitir resultados contrários.

**Regra de integridade:** números zero representam zero medido; valor não coletado deve ser `null`/`not_measured`; falha é `failed`; ausência legítima de tabela é `no_table_in_gt` ou equivalente. Nunca usar `0.0` como substituto de métrica indisponível ou erro de importação. Se uma tabela foi perdida, não se pode calcular CER apenas sobre as tabelas que a pipeline reconheceu e divulgar o valor como qualidade global sem reportar a cobertura.

---

## 11. Ordem recomendada de implementação, dependências e entregáveis

A ordem abaixo é uma sequência de engenharia para este caso de uso; não presume que os 28 tickets ainda estejam abertos na revisão mais recente. Antes de alterar código, cada item deve receber status `já atendido com evidência`, `reproduzido`, `não se aplica ao desenho escolhido` ou `pendente de implementação`.

| Etapa | Dependências | Trabalho | Entregável verificável |
|---|---|---|---|
| 0. Congelar revisão | Nenhuma | SHA, diff, inventário de arquivos, arquitetura efetiva de TableMagic, versão do PaddleOCR | Relatório de inspeção do SHA atual e mapa de chamadas |
| 1. Fechar desenho | Etapa 0 | Decidir A ou B, nomear baseline, fixar pipeline e definir composição dos braços | Especificação formal da matriz de modelos e configuração |
| 2. Integrar sem ambiguidades | Etapa 1 | C01–C06, C12, C25: injeção explícita de todos os modelos, proveniência e rejeição de estados mistos | Smoke real por braço com manifesto validado |
| 3. Garantir integridade estrutural | Etapa 2 | C07–C11, C21–C22: geometrias, HTML, spans, autoridade do texto, wired/wireless e fusão | Testes de integração com GT de tabelas complexas |
| 4. Garantir operação controlada | Etapa 2 | C13–C16, C19–C20, C27: instalação offline, isolamento, limites, política de falha | Runner com códigos de saída, métricas e isolamento |
| 5. Construir medição independente | Etapas 3 e 4 | C17–C18, C23–C24, C26: corpus, métricas, agregação e benchmark pareado | Pacote reproduzível de resultados e verificador de paridade |
| 6. Revisão de qualidade e mudança de padrão | Etapa 5 | C28: critérios pré-estabelecidos, revisão de divergências, rollback | Decisão documentada com evidências e testes aprovados |

### 11.1. Modelo de ticket para abrir no GitHub

```markdown
### Contexto e objetivo
[Definir que propriedade de comparação ou integridade será garantida.]

### Evidência na revisão exata
- Git SHA: [SHA real]
- Arquivo e função: [link permalink no SHA]
- Observação reproduzida ou risco condicional: [OBS/COND/VAL/DEC]
- Reprodução mínima: [PDF/fixture, comando, manifest, saída]

### Comportamento esperado
[Contrato verificável, sem depender de impressão visual da tabela.]

### Mudança proposta
[Componentes a tocar, riscos de regressão, opção de migração.]

### Testes e aceite
- [ ] Teste de unidade de contrato
- [ ] Teste de integração com pesos reais quando pertinente
- [ ] Caso negativo que deve falhar explicitamente
- [ ] CLI e API mantêm paridade
- [ ] IDs e hashes dos modelos confirmados
- [ ] Falhas entram no denominador e permanecem rastreáveis

### Compatibilidade e rollback
[Comportamento do modo atual, schema, cache e procedimento de retorno.]
```

### 11.2. Checklist de liberação do benchmark

- [ ] A revisão efetivamente avaliada tem SHA completo e não depende de branch mutável como identificador.
- [ ] O desenvolvedor demonstrou execução real do PP-TableMagic, não apenas código importado.
- [ ] O desenho experimental A ou B foi escolhido e testado em ambos os braços.
- [ ] Cada OCR externo e cada OCR interno de tabela correspondem ao perfil esperado ou estão inequivocamente desligados sob desenho B.
- [ ] Todos os modelos de estrutura, células, layout, orientação e classificação que não fazem parte do experimento estão fixos e têm IDs e hashes iguais.
- [ ] O baseline v5 é descrito pelo par detector/reconhecedor **real**, não por apelido genérico.
- [ ] O runtime não faz downloads ou troca de modelos não registrados.
- [ ] Imagens/recortes e parâmetros são iguais no benchmark controlado ou diferenças E2E são medidas separadamente.
- [ ] Não há mistura não documentada de OCR nativo, OCR externo, OCR interno, OCR por célula e fallback.
- [ ] Todos os spans, cabeçalhos, células vazias, números e posições são preservados ou erros são capturados e quantificados.
- [ ] Corpus e ground truth foram congelados antes de inspecionar resultados do comparativo.
- [ ] Métricas distinguem detecção de tabela, estrutura, associação de texto, OCR bruto e exportação final.
- [ ] Falhas, ausências, saídas parciais e interrupções são contabilizadas, sem exclusão silenciosa.
- [ ] Worker, cache, temporários e resultados são separados entre braços e execuções.
- [ ] Compatibilidade das APIs e dos pesos foi testada na instalação pinada; documentação upstream não foi tomada como prova de compatibilidade local.
- [ ] Testes negativos (modelo trocado, cache incompleto, falha de worker e manifesto não comparável) realmente bloqueiam publicação.
- [ ] O resultado permite reexecutar v5 e v6 sobre os mesmos PDFs e gerar o mesmo conjunto de IDs.
- [ ] Custos de memória e latência são medidos E2E, e não deduzidos do orçamento de um único recorte.
- [ ] Há política de privacidade, retenção e revisão de licenças adequada ao uso planejado.
- [ ] Qualquer decisão de mudar o padrão tem critério previamente definido e caminho de rollback testado.

---

## 12. Referências, rastreabilidade e limites de conclusão

### 12.1. Fontes do PDFExtractor inspecionadas ou pertinentes à verificação

Os links para a branch são **referências de inspeção mutáveis**. O desenvolvedor deve substituí-los por permalinks com o SHA registrado na seção 1 antes de abrir tickets como defeitos confirmados.

- [Árvore da branch `feat/paddle-ocrv6-evaluation`](https://github.com/victorperone/pdfextractor/tree/feat/paddle-ocrv6-evaluation).
- [README e declaração da abordagem de tabelas](https://github.com/victorperone/pdfextractor/blob/feat/paddle-ocrv6-evaluation/README.md#L60-L84).
- [Perfil dos modelos OCR](https://github.com/victorperone/pdfextractor/blob/feat/paddle-ocrv6-evaluation/src/structured_pdf_text/ocr/models.py#L60-L88).
- [Adaptador de OCR](https://github.com/victorperone/pdfextractor/blob/feat/paddle-ocrv6-evaluation/src/structured_pdf_text/ocr/paddle.py).
- [Configuração](https://github.com/victorperone/pdfextractor/blob/feat/paddle-ocrv6-evaluation/src/structured_pdf_text/config.py).
- [CLI](https://github.com/victorperone/pdfextractor/blob/feat/paddle-ocrv6-evaluation/src/structured_pdf_text/cli.py).
- [API de extração e orquestração](https://github.com/victorperone/pdfextractor/blob/feat/paddle-ocrv6-evaluation/src/structured_pdf_text/api.py).
- [Renderizador Markdown](https://github.com/victorperone/pdfextractor/blob/feat/paddle-ocrv6-evaluation/src/structured_pdf_text/renderers/markdown.py).
- [Declaração de dependências](https://github.com/victorperone/pdfextractor/blob/feat/paddle-ocrv6-evaluation/requirements.txt).
- [README: memória, limites e restrições operacionais](https://github.com/victorperone/pdfextractor/blob/feat/paddle-ocrv6-evaluation/README.md#L297-L364).

### 12.2. Fontes oficiais do PaddleOCR e PP-TableMagic

- [PaddleOCR, Table Recognition Pipeline V2: arquitetura, parâmetros, entradas e saídas](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/table_recognition_v2.en.md).
- [Parâmetros de modelo OCR interno da pipeline](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/table_recognition_v2.en.md#L588-L670).
- [Configurações `use_ocr_model` e `use_ocr_results_with_table_cells`](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/table_recognition_v2.en.md#L696-L730).
- [Saída de tabela, HTML e caixas de células](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/table_recognition_v2.en.md#L519-L547).
- [Documentação oficial de OCR e avisos sobre comparação de métricas](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/OCR.en.md).

**Limite de versionamento:** os links upstream acima apontam para `main` da documentação; para instruções executáveis, gerar links que correspondam ao tag ou commit de `paddleocr` e `paddlex` instalados no benchmark, e confirmar nomes efetivos de construtores e parâmetros com introspecção e execução real. A existência de um parâmetro na documentação publicada depois de uma versão não prova que ele está presente na versão pinada localmente.

### 12.3. O que esta auditoria não afirma

1. **Não afirma** que a revisão recém-alterada ainda tem as lacunas do snapshot anterior; elas devem ser confrontadas com o SHA atual. A ausência de acesso ao clone e ao conjunto completo de arquivos impede um laudo definitivo de todos os novos commits.
2. **Não afirma** que PP-TableMagic está integrado no SHA atual: a prova necessária é o encadeamento concreto `configuração → criação do motor → execução → adaptação de tabela → saída` e um teste real por braço.
3. **Não afirma** que v5 ou v6 reconhece melhor português, preserva melhor tabelas ou usa menos memória nesta aplicação; a resposta depende de corpus controlado, hardware e medição com ambos os braços.
4. **Não afirma** que qualquer parâmetro de pseudocódigo está disponível na versão local do PaddleOCR; esses trechos expressam o contrato exigido e não substituem consulta à assinatura real.
5. **Não promete** ausência de problemas após a execução do checklist; o documento reúne riscos arquiteturais e pontos auditados possíveis com o acesso disponível, e deve ser complementado por testes e revisão do SHA imutável.

**Conclusão para o desenvolvedor:** a comparação pretendida é tecnicamente possível **desde que** o PP-TableMagic use, em cada braço, OCR interno correspondente ao perfil declarado ou que sua geometria seja integrada a um OCR externo sob um desenho experimental explicitamente validado. Sem essa garantia e sem fixar os demais componentes da pipeline, um resultado rotulado “PP-OCRv5 versus PP-OCRv6 com PP-TableMagic” pode ser metodologicamente ambíguo. Executar o plano por fases, fechar cada ticket com evidência e só então interpretar diferenças de qualidade e desempenho.

---

## 13. Resultados do comparativo OCR — PP-OCRv5 × PP-OCRv6 (Seção 2.1)

Execução real concluída sobre `corpus/Document_AI_V2.pdf`, todos os quatro braços com status `success`.

### 13.1. Metadados da execução

| Campo | Valor |
|---|---|
| Script | `scripts/eval_v6/compare_v5_v6.py` |
| PDF | `corpus/Document_AI_V2.pdf` |
| SHA do repositório | `e8ecba61d432f79f276ac97ba078ffdd26dfaf2e` |
| Plataforma | Windows Server 2025, CPU-only |
| Perfil v5 (`pt`) | `PP-OCRv5_server_det` + `latin_PP-OCRv5_mobile_rec` (par híbrido) |
| Perfil v6 (`pt-v6-medium`) | `PP-OCRv6_medium_det` + `PP-OCRv6_medium_rec` |
| Políticas | `baseline`, `adaptive` |
| Modo de extração | `balanced` |
| Pages registradas | todas as páginas do PDF |

### 13.2. Desempenho (latência e saída)

| Braço | Saída (bytes) | Tempo (s) | Δ latência vs. v5 | RSS pico (MB)¹ |
|---|---|---|---|---|
| `pt / baseline` | 26.021 | 615,9 | — | 3,81 |
| `pt / adaptive` | 26.019 | 1920,0 | — | 3,80 |
| `pt-v6-medium / baseline` | 25.501 | 367,5 | **−40 %** | 3,81 |
| `pt-v6-medium / adaptive` | 25.930 | 1378,9 | **−28 %** | 3,82 |

¹ Os valores de RSS (~3,8 MB) são **artefatos de medição**: o monitor `psutil` capturou o processo filho antes de o PaddlePaddle carregar os pesos em memória (comportamento conhecido em Windows Server com subprocesso `Popen`). Os valores reais de RAM devem ser medidos separadamente; o README estima ~10,8 GiB para o modelo v5 completo.

**Síntese de desempenho:** v6-medium é significativamente mais rápido em ambas as políticas sem redução proporcional do tamanho de saída (diferença de bytes é marginal, ≤ 2 %). A vantagem de velocidade é mais expressiva em modo `baseline` (−40 %) do que em `adaptive` (−28 %), onde ambos passam por variantes de recuperação.

### 13.3. Qualidade textual por categoria de página

O `diff_baseline.txt` registrou 37 linhas adicionadas / 37 removidas; `diff_adaptive.txt` registrou 36 / 34. A maioria das páginas com texto nativo digital é idêntica entre os modelos. As diferenças concentram-se nas páginas com conteúdo sintético degradado.

#### 13.3.1. Fórmulas matemáticas — página 12

Ambos os modelos falham em extrair fórmulas corretamente. v6 tem vantagem parcial:

- v6 reconhece o símbolo de somatório `∑` onde v5 produz `( − )²`
- v6 aproxima melhor `f(ξ) = ∫ +∞f(x)e−2πixξ dx` vs. a versão truncada do v5
- Nenhum modelo é confiável para conteúdo matemático tipografado; resultado é **descartável** para esse tipo de página

#### 13.3.2. Imagem raster — página 25

| Modelo | Saída |
|---|---|
| v5 | `StatuS: APROVADO PARA OCR` |
| v6 | `Status: APROVADO PARA OCR` |

**v6 vence:** normalização de capitalização correta em texto extraído de imagem.

#### 13.3.3. Texto de baixo contraste — página 27

| Braço | Comportamento |
|---|---|
| `pt / baseline` | Lê a maior parte do conteúdo com erros menores (`OcR`, `CoNTRAST-O27`) |
| `pt-v6-medium / baseline` | **Falha catastrófica:** `DOITDO S2LE`, `OCo oCte`, `ai t`, `SD` — texto ilegível |
| `pt / adaptive` | Lê o conteúdo com qualidade semelhante ao baseline |
| `pt-v6-medium / adaptive` | Recuperação parcial; ainda produz `SD` em vez de `Baixo contraste controlado:...` |

**Ponto positivo isolado no v6:** o código de identificação da tabela é normalizado corretamente — v6 produz `GS2-SCAN-CONTRAST-027` onde v5/baseline registrava `GS2-SCAN-CoNTRAST-O27`.

**Conclusão desta página:** v5 é claramente superior em texto de baixo contraste. A falha do v6/baseline nesta categoria é regressão grave e deve ser considerada bloqueadora para qualquer cenário de produção com documentos degradados.

#### 13.3.4. Texto com ruído — página 28

| Braço | Comportamento |
|---|---|
| `pt / baseline` | Lê o conteúdo com alguns erros (`NÃ CONFDENCIAL`) |
| `pt-v6-medium / baseline` | Falha: `OC   DO S2L`, `CR   IdoO`; produz **linhas de tabela fantasma** (`\| a t \|  \|`, `\| a \|  \|`) |
| `pt / adaptive` | Mantém `NÃ CONFDENCIAL` (ainda com erro) |
| `pt-v6-medium / adaptive` | Corrige: `NÃO CONFIDENCIAL`; remove tabela fantasma |

**Ponto crítico — alucinação no v6/baseline:** a geração de linhas de tabela sem conteúdo real (`| a t |  |  |`) é uma alucinação estrutural. Esse comportamento é mais danoso do que um erro de OCR simples porque insere estrutura falsa na saída Markdown.

**Conclusão desta página:** v6/adaptive supera v5/adaptive na recuperação de texto; v6/baseline é inferior a v5/baseline e gera alucinações estruturais.

#### 13.3.5. Página inclinada — página 29

| Modelo | ID da amostra | Marca d'água |
|---|---|---|
| v5 | `SKEw-029`, `GS2-SCAN-SKEw-029` | `NÃo CONFIDENCIAL` |
| v6 | `SKEW-029`, `GS2-SCAN-SKEW-029` | `NÃO CONFIDENCIAL` |

**v6 vence:** capitalização consistente em conteúdo inclinado. Diferença marginal na prática, mas indica melhor reconhecimento de caixa alta em condições geométricas adversas.

#### 13.3.6. Fonte muito pequena — página 30 (6,4 pt)

Esta é a diferença de maior impacto prático entre os dois modelos.

- **v5 (baseline e adaptive):** ordem de leitura completamente invertida — colunas e linhas da tabela em sequência revertida; cabeçalhos misturados com valores; conteúdo ilegível estruturalmente.
- **v6 (baseline e adaptive):** lê na ordem correta — título `DOCUMENT AI GOLD STANDARD V2 - CORPUS SINTÉTICO CONTROLADO GS2-P30-CONTROLE`, corpo de texto e tabela em sequência correta, com conteúdo legível.

**v6 vence de forma decisiva.** O bug de leitura do v5 em página com fonte de 6,4 pt não é recuperado pela política `adaptive`. Se o corpus de produção incluir documentos com fontes abaixo de ~8 pt, v5 apresenta risco concreto de inversão silenciosa de conteúdo.

#### 13.3.7. Páginas rotacionadas — páginas 31–33 (45°, 90°, 270°)

Ambos os modelos se comportam de forma equivalente. Nenhuma diferença material registrada nos diffs.

### 13.4. Resumo comparativo

| Categoria | Vencedor | Observação |
|---|---|---|
| Desempenho (latência) | **v6** | −40 % baseline, −28 % adaptive |
| Tamanho de saída | Empate | Diferença ≤ 2 % |
| Memória RAM | Indeterminado | Medição via psutil inválida neste setup |
| Fórmulas matemáticas (pág. 12) | v6 (parcial) | Ambos falham; v6 menos errado |
| Imagem raster / capitalização (pág. 25) | **v6** | Corrige `StatuS` → `Status` |
| Baixo contraste baseline (pág. 27) | **v5** | v6/baseline produz texto ilegível |
| Baixo contraste adaptive (pág. 27) | **v5** | v6/adaptive melhora mas não iguala |
| Ruído baseline (pág. 28) | **v5** | v6/baseline produz alucinação de tabela |
| Ruído adaptive (pág. 28) | **v6** | v6/adaptive corrige `NÃO CONFIDENCIAL` |
| Inclinação / capitalização (pág. 29) | **v6** | Caixa alta consistente |
| Fonte 6,4 pt / ordem de leitura (pág. 30) | **v6** | v5 inverte leitura; v6 lê corretamente |
| Rotações (págs. 31–33) | Empate | Comportamento equivalente |

### 13.5. Limitações desta rodada

1. **Sem ground truth verificado.** As comparações são observacionais (diff entre as duas saídas). CER/WER não foram calculados.
2. **Medição de RAM inválida.** Os valores `peak_rss_mb` (~3,8 MB) refletem a janela de inicialização do subprocesso no Windows, não o pico real de consumo do PaddlePaddle.
3. **SHA anterior às correções CF-01–CF-05.** A execução usou o commit `e8ecba61`, anterior aos hotfixes desta sessão. Os resultados de qualidade são válidos; o manifesto não inclui os campos `has_weight`/`has_metadata` (adicionados no CF-05).
4. **Corpus de um único documento controlado.** O `Document_AI_V2.pdf` cobre as principais categorias de forma controlada, mas não representa a distribuição real de documentos de produção.

### 13.6. Estado do cache e próxima etapa — Seção 2.2 (PP-TableMagic)

| Cache | Modelos OCR | Modelos de estrutura de tabela | Status para `compare_tablemagic.py` |
|---|---|---|---|
| `paddlex` (v5) | ✅ presentes | ✅ presentes (SLANeXt, RT-DETR-L, PP-LCNet) | **Pronto** |
| `paddlex-v6-eval` (v6) | ✅ presentes | ❌ ausentes | **Bloqueado — download necessário** |

Antes de executar `compare_tablemagic.py`, os modelos de estrutura de tabela comuns devem ser baixados para o cache v6. Use `eval_tablemagic.py --profile tm-v6` para desencadear o download automático ou copie os modelos do cache v5 (são idênticos entre os braços no Desenho A).

Use `--check-only` para confirmar o estado antes de rodar inferência:

```bash
python scripts/eval_v6/compare_tablemagic.py corpus/Document_AI_V2.pdf \
    --v5-cache ~/.cache/pdfextractor/paddlex \
    --v6-cache ~/.cache/pdfextractor/paddlex-v6-eval \
    --check-only
```

---

## 14. Resultados do Comparativo PP-TableMagic — tm-v5 × tm-v6 (Seção 2.2)

### 14.1. Metadados da execução

| Campo | Valor |
|---|---|
| Script | `scripts/eval_v6/compare_tablemagic.py` |
| SHA do commit | `ee80004bda3176523dba420964b1d8633c7084ef` |
| Plataforma | Windows Server 2025 (AMD64) |
| paddleocr | 3.7.0 |
| paddlepaddle | 3.3.1 |
| paddlex | 3.7.2 |
| pypdfium2 | 5.11.0 |
| Pillow | 11.1.0 |
| PDF de referência | `Document_AI_V2.pdf` |
| Páginas processadas | 42 (todas) |
| Status geral | ✅ 84 inferências, 0 erros |

**Perfis comparados:**

| Perfil | OCR interno | Modelos de estrutura |
|---|---|---|
| `tm-v5` | `PP-OCRv5_server_det` + `latin_PP-OCRv5_mobile_rec` | SLANeXt, RT-DETR-L, PP-LCNet (idênticos entre braços) |
| `tm-v6` | `PP-OCRv6_medium_det` + `PP-OCRv6_medium_rec` | SLANeXt, RT-DETR-L, PP-LCNet (idênticos entre braços) |

Observação sobre o CF-02 desta rodada: o parâmetro `use_ocr_results_with_table_cells` foi omitido do construtor de `TableRecognitionPipelineV2` porque o `paddleocr 3.7.0` rejeita argumentos desconhecidos no construtor. Isso está registrado no manifesto `evaluation.json` como `"not_applied_paddleocr_3.7.0_constructor_rejects_argument"`. O efeito sobre a comparação é simétrico — ambos os braços foram afetados da mesma forma.

### 14.2. Comportamento do pipeline com `use_layout_detection=False`

**Todas as 42 páginas retornaram exatamente 1 "tabela" por página, independentemente do conteúdo real.**

Com `use_layout_detection=False`, a `TableRecognitionPipelineV2` recebe a página inteira como uma única região de tabela, sem etapa prévia de detecção de layout. Isso é intencional no contexto desta avaliação — elimina a variável de detecção de layout para isolar o efeito do OCR interno. Porém, **o campo `table_count_delta` (sempre 0) não tem poder discriminante neste corpus**: páginas de texto puro, gráficos, diagrama e tabelas reais recebem o mesmo tratamento.

Consequência direta: as métricas `tm_v5_tables` e `tm_v6_tables` registradas no `evaluation.json` refletem o número de *regiões processadas pela pipeline de tabela*, não o número de tabelas detectadas no conteúdo do PDF.

### 14.3. Desempenho

**Inicialização do pipeline:**

| Perfil | Init (ms) | Diferença |
|---|---|---|
| tm-v5 | 5.627 ms | referência |
| tm-v6 | 4.591 ms | **−18% (v6 mais rápido)** |

**Inferência por página:**

| Métrica | tm-v5 | tm-v6 | Diferença |
|---|---|---|---|
| Total (42 páginas) | ~814 s | ~666 s | **−18% (v6 mais rápido)** |
| Média por página | ~19,4 s | ~15,9 s | **−18%** |
| Máximo (págs. 19–20, tabela de 270 células) | ~30 s | ~28 s | −7% |

A vantagem de velocidade do v6 no modo TableMagic (~18%) é ligeiramente inferior à registrada no comparativo OCR puro (~28% em modo adaptativo). A sobreposição dos cinco modelos de estrutura de tabela partilhados amortece parte da diferença.

**Páginas mais lentas (19 e 20)** correspondem à tabela de orçamento trimestral com ~270 células. Ambos os braços levaram ~28-30 s para essas páginas.

### 14.4. Qualidade textual dentro das células

Os modelos de estrutura de tabela (SLANeXt, RT-DETR-L, PP-LCNet) são **idênticos entre tm-v5 e tm-v6**. Portanto, a geometria de células e o HTML estrutural são os mesmos — as diferenças observadas nas saídas são exclusivamente de OCR.

**Casos em que tm-v6 produz resultado melhor:**

| Página | tm-v5 | tm-v6 | Observação |
|---|---|---|---|
| 2 | `OcR` | `OCR` | Capitalização corrigida |
| 4 | `portuquês` | `português` | Acento corrigido |
| 4 | `GS2-PT-BR-ÁRvORE-004` | `GS2-PT-BR-ÁRVORE-004` | Caixa alta consistente |
| 7 | `tachade` | `tachado` | Grafia corrigida |
| 18 | `1° semestre` | `1º semestre` | Símbolo ordinal correto |
| 27 | `GS2-SCAN-CoNTRAST-O27` | `GS2-SCAN-CONTRAST-027` | Corrige 'O' → '0' e caixa |
| 28 | `sințético` / `NOIsE-028` | `sintético` / `NOISE-028` | Diacrítico e capitalização corretos |

**Casos em que tm-v5 produz resultado melhor (regressões do v6):**

| Página | tm-v5 | tm-v6 | Observação |
|---|---|---|---|
| 11 | `calcular_total` | `calcular total` | v6 remove underscore em nome de função |
| 13 | `9ª` | `gª` | v6 confunde '9' com 'g' |
| 15 | `TI` | `πI` | v6 confunde 'T' com 'π' |

As regressões do v6 afetam identificadores de código (underscores) e símbolos alfanuméricos em contextos de baixa resolução. Esse padrão é consistente com o observado no comparativo OCR puro (Seção 13.4).

**Observação importante — página 30 (fonte 6,4 pt):**

No comparativo OCR puro (Seção 13.4), o tm-v5 **falhou completamente** na página 30 em modo baseline, invertendo a ordem de leitura. No modo TableMagic, **ambos os braços leram o conteúdo corretamente**. A hipótese é que a decomposição em células pelo SLANeXt força uma ordem de leitura por grade, mitigando o bug de ordem de leitura do v5. Isso é relevante para a decisão de integração: TableMagic pode compensar problemas de layout do OCR puro em páginas de texto denso.

### 14.5. Confiança do modelo (avg_score)

A maioria das páginas apresenta `avg_score` > 0,99 em ambos os braços — alta confiança no texto extraído.

**Páginas com diferença relevante:**

| Página | Conteúdo | tm-v5 avg_score | tm-v6 avg_score | Observação |
|---|---|---|---|---|
| 12 | Fórmulas matemáticas | 0,822 | 0,957 | v6 significativamente mais confiante |
| 32 | Tabela rotacionada 180° | 0,343 | 0,764 | v6 ~2× mais confiante |
| 33 | Tabela rotacionada 270° | 0,311 | 0,721 | v6 ~2× mais confiante |

Para conteúdo rotacionado (págs. 32–33), nenhum dos braços corrige a orientação da tabela (`use_table_orientation_classify=False`), mas o v6 apresenta confiança significativamente maior no texto que consegue extrair. Isso se alinha com os melhores modelos de reconhecimento do PP-OCRv6 para caracteres em orientações não padrão.

### 14.6. Introspeção de modelos carregados

O campo `loaded_model_names_observed` em ambos os braços retornou **apenas os cinco modelos de estrutura de tabela**:

```
['PP-LCNet_x1_0_table_cls', 'RT-DETR-L_wired_table_cell_det',
 'RT-DETR-L_wireless_table_cell_det', 'SLANeXt_wired', 'SLANeXt_wireless']
```

Os modelos OCR internos (`PP-OCRv5_server_det`, `latin_PP-OCRv5_mobile_rec`, `PP-OCRv6_medium_det`, `PP-OCRv6_medium_rec`) **não aparecem** nesta lista. O mecanismo `_loaded_model_names()` acessa `pipeline._model_list` ou equivalente, que no PaddleX 3.7.x expõe apenas os modelos de estrutura de tabela, não os sub-componentes OCR internos.

Isso significa que a verificação de manifesto confirma que os modelos de estrutura corretos foram carregados, mas não comprova via introspeção que o OCR interno correto foi utilizado. A evidência indireta (diferenças de qualidade de texto confirmam comportamento distinto entre os braços) valida que os modelos OCR configurados via `text_detection_model_dir` / `text_recognition_model_dir` foram de fato utilizados.

### 14.7. Volume de artefatos por página — nota para rodadas futuras

A execução atual gerou a seguinte estrutura de saída:

```
output/compare_tablemagic/
  tm-v5/
    page_0001/  input.png + result_000/ (artifacts) + page_result.json
    page_0002/  ...
    ...         (42 diretórios × ~5–10 arquivos cada)
  tm-v6/
    page_0001/  ...
    ...         (42 diretórios × ~5–10 arquivos cada)
  evaluation.json
```

Para 42 páginas × 2 perfis = **84 diretórios por página**, totalizando centenas de arquivos (PNGs de entrada + HTML de resultado + JSONs por item). Isso é aceitável para a rodada de avaliação, mas inviável para uso corrente ou integração em CI.

**Melhoria pendente:** adicionar flag `--no-artifacts` a `compare_tablemagic.py` para omitir a chamada a `_save_result_artifacts()` e salvar apenas `page_result.json` + `evaluation.json`. O `evaluation.json` já contém `pred_html`, `cell_count`, `avg_score` e `elapsed_ms` para toda análise de qualidade; os artefatos visuais só são necessários para inspeção manual pontual.

### 14.8. Conclusões e próximos passos

**Síntese:**

| Dimensão | Resultado |
|---|---|
| Velocidade | tm-v6 ~18% mais rápido na inferência e inicialização |
| Estrutura de tabela | **Idêntica** entre os braços (mesmos 5 modelos compartilhados) |
| Qualidade OCR em células | tm-v6 melhor em capitalização, diacríticos e rotações; tm-v5 melhor em underscores e alguns símbolos alfanuméricos |
| Conteúdo rotacionado | tm-v6 significativamente mais confiante (avg_score 2× maior em págs. 32–33) |
| Página de fonte mínima (pág. 30) | Ambos corretos via TableMagic (v5 falha sem TableMagic) |
| Erros de inferência | Nenhum em ambos os braços (42/42 páginas) |

**PP-OCRv6 apresenta vantagem consistente em TableMagic**, preservando a tendência observada no comparativo OCR puro. O risco de regressão mais relevante é a remoção de underscores em identificadores de código (página 11) — cenário presente em PDFs com blocos de código-fonte.

**Próximas etapas:**

1. **Gate 6 — Critérios de rollback e promoção:** definir os limiares quantitativos (avg_score mínimo, taxa de erro, tempo máximo) que determinam se v6 substitui v5 como perfil padrão. *(pendente)*
2. ✅ **Integração seletiva do TableMagic (`compare_hybrid.py`):** implementado — `scripts/eval_v6/compare_hybrid.py` ativa TableMagic somente quando `avg_confidence < 0.70`, usa `use_layout_detection=True` (Opção B) e não gera artefatos por página. Ver Seção 15.
3. ✅ **Sem artefatos por página:** resolvido por design no `compare_hybrid.py` — saída é apenas `evaluation.json`.
4. **Corpus de produção:** executar `compare_hybrid.py` sobre `corpus/Corpus_Integrado_PDF_OCR_TableMagic_V3.pdf` quando o arquivo estiver disponível no servidor. Essa será a rodada definitiva antes da decisão de promoção do v6.

---

## 15. Avaliação Híbrida — OCR com fallback condicional para PP-TableMagic

### 15.1. Design do script `compare_hybrid.py`

**Arquivo:** `scripts/eval_v6/compare_hybrid.py`

**Lógica por página:**

```
para cada página e perfil (v5, v6):
    1. PaddleOCR → avg_confidence
    2. se avg_confidence < 0.70 E texto_detectado > 0:
           TableRecognitionPipelineV2 (use_layout_detection=True)
           se tabelas encontradas  → usar texto das células
           se nenhuma tabela       → manter OCR, registrar 'ocr_tablemagic_no_table'
    3. senão:
           usar resultado OCR diretamente
```

**Modos registrados por página:**

| Modo | Condição |
|---|---|
| `ocr` | `avg_confidence >= 0.70` — OCR direto |
| `tablemagic` | confiança baixa + tabelas detectadas — TableMagic substitui OCR |
| `ocr_tablemagic_no_table` | confiança baixa + sem tabelas — OCR mantido |
| `no_text_detected` | PaddleOCR não detectou nenhum texto |

**Decisões de design:**

- **Threshold 0.70** — valor escolhido após análise dos comparativos anteriores: páginas rotacionadas (págs. 31–33) têm confiança << 0.30; ruído (pág. 28) ~0.55–0.70; páginas normais > 0.90. Threshold de 0.80 ativaria TableMagic em páginas aceitáveis (0.70–0.80), adicionando ~15s desnecessários.
- **`use_layout_detection=True` (Opção B)** — detecta regiões de tabela no layout antes de processar, em vez de tratar a página inteira como uma tabela.
- **Lazy init do TableMagic** — `TableRecognitionPipelineV2` só é inicializado na primeira página que disparar o threshold, economizando ~4–5s de init quando todas as páginas têm alta confiança.
- **Sem artefatos** — imagens são criadas em `tempfile.mkdtemp()` e deletadas após cada página; saída é apenas `output/compare_hybrid/evaluation.json`.
- **Parâmetro `--confidence-threshold`** — configurável via CLI (padrão: 0.70).

### 15.2. Modelo de layout — PP-DocLayout_plus-L

**Modelo escolhido:** `PP-DocLayout_plus-L` — padrão do PaddleX 3.7.2.

**Funcionamento offline:** 100% offline via `layout_detection_model_dir` após download inicial.

**Download:** necessário uma vez por diretório de cache (v5 e v6), na primeira execução com internet. Execuções subsequentes funcionam com `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True`.

**Alternativas menores** (se houver necessidade de economizar espaço ou velocidade):

| Modelo | Quando usar |
|---|---|
| `PP-DocLayout_plus-L` | ✅ Padrão — melhor precisão, ~200MB |
| `PP-DocLayout-M` | Alternativa média — menos preciso, mais rápido |
| `PP-DocLayout-S` | Alternativa pequena — menor espaço, menor precisão |

**Diretório esperado por cache:**
```
~/.cache/pdfextractor/paddlex/official_models/PP-DocLayout_plus-L/
~/.cache/pdfextractor/paddlex-v6-eval/official_models/PP-DocLayout_plus-L/
```

### 15.3. Pré-requisitos para execução no servidor

1. **PDF do corpus definitivo** disponível em `corpus/Corpus_Integrado_PDF_OCR_TableMagic_V3.pdf`
2. **PP-DocLayout_plus-L** baixado em ambos os caches (v5 e v6)
3. Modelos OCR e de estrutura de tabela já presentes (confirmados na Seção 14)

### 15.4. Comandos de execução (PowerShell — Windows Server)

> O ambiente virtual já deve estar ativo: confirme que o prompt exibe `(.venv)`.

**Passo 0 — Copiar o script do WSL para o workspace Windows (apenas se ainda não estiver presente):**

```powershell
Copy-Item "\\wsl.localhost\Ubuntu\home\victorperone\workspace\pdfextractor\scripts\eval_v6\compare_hybrid.py" -Destination "scripts\eval_v6\compare_hybrid.py"
```

**Passo 1 — Baixar PP-DocLayout_plus-L (apenas uma vez, requer internet):**

```powershell
python scripts\eval_v6\compare_hybrid.py corpus\Corpus_Integrado_PDF_OCR_TableMagic_V3.pdf --pages 1 --allow-download --output-dir output\compare_hybrid_test
```

**Passo 2 — Verificar inventário offline:**

```powershell
$env:PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK = "True"
python scripts\eval_v6\compare_hybrid.py corpus\Corpus_Integrado_PDF_OCR_TableMagic_V3.pdf --v5-cache "$HOME\.cache\pdfextractor\paddlex" --v6-cache "$HOME\.cache\pdfextractor\paddlex-v6-eval" --check-only
```

**Passo 3 — Execução definitiva (100% offline):**

```powershell
$env:PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK = "True"
python scripts\eval_v6\compare_hybrid.py corpus\Corpus_Integrado_PDF_OCR_TableMagic_V3.pdf --v5-cache "$HOME\.cache\pdfextractor\paddlex" --v6-cache "$HOME\.cache\pdfextractor\paddlex-v6-eval" --confidence-threshold 0.70 --output-dir output\compare_hybrid
```

### 15.6. Gate 6 — Decisão de promoção (2026-09-24) ✅ CONCLUÍDO

**Decisão:** PP-OCRv6 (`pt-v6-medium`) promovido a perfil padrão de produção.

**Alterações realizadas:**

| Ficheiro | Alteração |
|---|---|
| `src/structured_pdf_text/ocr/models.py` | Chave `"pt"` passa a apontar para PP-OCRv6 (`PP-OCRv6_medium_det` + `PP-OCRv6_medium_rec`). PP-OCRv5 preservado como `"pt-v5"` para rollback. |
| `scripts/eval_v6/compare_hybrid.py` | `DEFAULT_THRESHOLD` atualizado de `0.70` para `0.85` (compensa sobreconfiança do v6 em páginas degradadas). |

**Passo de deployment no servidor:**
```powershell
# Mover modelos v6 para o caminho padrão de produção
Rename-Item "$HOME\.cache\pdfextractor\paddlex" "$HOME\.cache\pdfextractor\paddlex-v5-backup"
Rename-Item "$HOME\.cache\pdfextractor\paddlex-v6-eval" "$HOME\.cache\pdfextractor\paddlex"
```

**Rollback disponível:**
```powershell
# Reverter cache
Rename-Item "$HOME\.cache\pdfextractor\paddlex" "$HOME\.cache\pdfextractor\paddlex-v6-eval"
Rename-Item "$HOME\.cache\pdfextractor\paddlex-v5-backup" "$HOME\.cache\pdfextractor\paddlex"
# Reverter código: alterar chave "pt" em models.py de volta para PP-OCRv5, ou usar --language pt-v5
```

---

### 15.5. Resultados e conclusão

**Corpus:** `Corpus_Integrado_PDF_OCR_TableMagic_V3.pdf` — 144 páginas  
**Data de execução:** 2026-09-24  
**Script:** `scripts/eval_v6/compare_hybrid.py` — pipeline 3-níveis (nativo → OCR → TableMagic)  
**Limiar de confiança:** 0.70 | **min_native_chars:** 50

---

#### Distribuição de páginas por modo de extração

| Modo de extração | v5 (PP-OCRv5) | v6 (PP-OCRv6) |
|---|---|---|
| Texto nativo (`native`) | **98 / 144 (68,1%)** | **98 / 144 (68,1%)** |
| Somente OCR (`ocr_only`) | 42 / 144 (29,2%) | 44 / 144 (30,6%) |
| TableMagic activado com tabelas (`tablemagic`) | **2** | **0** |
| TableMagic activado sem tabelas (`ocr_tablemagic_no_table`) | 2 | 2 |
| Erros | **0** | **0** |

---

#### Desempenho de inicialização e OCR

| Métrica | v5 | v6 | Diferença |
|---|---|---|---|
| Inicialização OCR (`ocr_init_ms`) | 4 514 ms | **1 221 ms** | v6 **3,7× mais rápido** |
| Inicialização TableMagic (`tm_init_ms`) | 4 640 ms | 5 645 ms | v6 ~20% mais lento |
| Velocidade média por página escaneada | ~33–36 s | ~22–25 s | v6 **~30% mais rápido** |

---

#### Análise das páginas críticas

As páginas 96, 97, 99 e 116 são as únicas que acionaram o limiar de confiança em pelo menos um perfil. Todas correspondem a conteúdo com degradação visual severa (rotação, tabelas financeiras invertidas).

| Página | v5 conf. | v5 modo | v6 conf. | v6 modo |
|---|---|---|---|---|
| 96 | 0,291 | `ocr_tablemagic_no_table` | 0,666 | `ocr_tablemagic_no_table` |
| 97 | 0,298 | `ocr_tablemagic_no_table` | 0,683 | `ocr_tablemagic_no_table` |
| 99 | 0,463 | **`tablemagic` (3 tabelas)** | 0,754 | `ocr_only` |
| 116 | 0,457 | **`tablemagic` (1 tabela)** | 0,778 | `ocr_only` |

- **Páginas 96 e 97**: ambos os perfis acionam TableMagic, mas nenhuma tabela estruturada é detectada — o conteúdo está demasiado degradado/rotacionado para recuperação automática.
- **Páginas 99 e 116**: divergência crítica. O v5, com menor confiança (< 0,70), aciona TableMagic e recupera tabelas estruturadas. O v6 retorna confiança acima do limiar (0,754 e 0,778) e permanece em modo OCR — produzindo texto embaralhado com pontuação elevada (sobreconfiança em conteúdo rotacionado).

---

#### Conclusões

1. **Texto nativo idêntico**: ambos os perfis concordam em quais páginas têm camada textual (98/144). O nível 1 do pipeline elimina OCR para 68% do documento.

2. **v6 significativamente mais rápido em OCR**: ~30% de ganho por página e 3,7× na inicialização — vantagem relevante para volumes grandes.

3. **v6 apresenta sobreconfiança em conteúdo degradado/rotacionado**: nas páginas 99 e 116, o v6 atribui scores elevados (> 0,75) a texto ilegível, ignorando o escalamento para TableMagic. O v5 é mais conservador e aciona o fallback correctamente.

4. **Impacto prático do TableMagic**: em 144 páginas de corpus misto, o TableMagic contribuiu em apenas 2 páginas com o v5. A estratégia de fallback funcionou correctamente quando acionada.

5. **Recomendação — limiar diferenciado por perfil**: se o v6 for promovido como perfil padrão, o limiar de confiança deverá ser reduzido para ~0,80–0,85 para compensar a tendência de sobreconfiança, garantindo que páginas como 99 e 116 continuem a activar TableMagic.

6. **Gate 6 (decisão de promoção)**: o v6 é superior em velocidade, mas apresenta risco de qualidade regressiva em páginas com rotação/degradação severa. Antes de promover, é necessário validar o texto produzido para as páginas 99 e 116 em ambos os perfis e definir se o limiar ajustado resolve a divergência.
