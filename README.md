# Professor MD 4.5 — estudo inteligente

Versão focada em transformar o PDF enviado em uma experiência de estudo completa para o TRT-MG/FUMARC.

## Novidades
- Mapa mental PDF redesenhado em múltiplas páginas, com quebra de texto e sem sobreposição de balões.
- Flashcards com geração e segunda etapa automática de auditoria de fidelidade ao PDF.
- Geração de questões dentro do site, em janela própria, sem criar PDF de questões.
- Questões geradas a partir do arquivo PDF selecionado diretamente, evitando misturar documentos.
- Correção das questões dentro do site.
- Toda questão errada é enviada automaticamente ao Caderno de Erros, com sua alternativa, gabarito e explicação.
- Resultado do simulado entra no progresso.
- “Ver aula” no cronograma agora abre uma aula do Professor MD baseada no PDF associado e também funciona no cronograma completo.
- Mantidos Biblioteca Permanente, upload robusto, cronograma, revisões, mapa mental e flashcards.

## Variáveis
- OPENAI_API_KEY
- OPENAI_MODEL (padrão: gpt-5.6-luna)
- OPENAI_VECTOR_STORE_ID (opcional)
- SUPABASE_URL
- SUPABASE_SERVICE_ROLE_KEY
- SUPABASE_BUCKET

## Observação
As questões são originais e inspiradas no estilo de cobrança, não questões oficiais da FUMARC. O conteúdo gerado deve permanecer fiel ao PDF selecionado.
