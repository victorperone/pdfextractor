```text
fix: validar contrato canônico e consolidar diagnósticos do avaliador

- compartilhar a avaliação canônica entre documento e páginas selecionadas
- preservar family, conditions, estados, recovery e identidade dos runs
- completar relatórios de qualidade, omissões, cobertura, tempo e memória
- separar escopo avaliado do escopo da extração medida e manter runs distintos
- publicar JSON Schema v3 empacotado e validar resultados antes de uso
- verificar finitude, estados, definições, contadores, razões e escopos
- corrigir denominadores vazios, direções de CER e diagnósticos de hipótese vazia
- contar código literal e evitar sobreposição de listas na integridade
- incluir inserções de títulos extras no CER e limitar trabalho de títulos/listas
- otimizar alinhamentos idênticos e tabelas/linhas sem alterar o resultado exato
- calcular pares concordantes em O(n log n), preservando empates
- reutilizar parser de tabelas e CER de títulos canônicos nos diagnósticos legados
- identificar definições 3.0.5 e exigir reavaliação de artefatos anteriores
- adicionar exportação de características e evidências nativas em uma extração
- impedir reutilização de nomes de artefatos antes de abrir o PDF
- acrescentar regressões, oráculos independentes e controles completos V3/V4/V5
- documentar auditoria, testes e execução real em output/loop/001

Validação:
- suíte padrão: 2491 passed, 6 skipped, 4 xfailed; nenhuma falha inesperada
- bateria dirigida: 256 passed, incluindo as 39 reproduções da auditoria original
- preflight RapidOCR Latin: ONNX Runtime e OpenVINO aprovados
- controles de identidade: V3 com 144 páginas, V4 com 224 e V5 com 42
- wheel inspecionado com schema e dependência jsonschema; git diff --check limpo
- uma extração de cada PDF V3/V4/V5: 410 páginas e artefatos verificados
- V3/V4 operacionais válidos; V5 parcial, com degradação OCR nas páginas 21/31

As métricas globais indisponíveis por orçamento continuam explicitamente
parciais e inelegíveis para ranking completo. Diagnósticos por página não
substituem as definições globais. O OCR default não foi alterado.
```
