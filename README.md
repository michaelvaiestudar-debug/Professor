# Professor MD 4.6

Versão web do Professor MD para estudo no celular/tablet, com biblioteca permanente no Supabase.

## Novidades 4.6
- Seleção de PDF com área grande e compatível com toque em Android/celular/tablet, mostrando o nome do arquivo escolhido.
- Upload continua com biblioteca permanente e processamento em segundo plano.
- Mapa mental PDF redesenhado: página de visão geral + detalhamento por ramo, quebra automática de texto e paginação. O conteúdo não é truncado nem sobreposto.
- Flashcards mais rigorosos: geração exclusiva a partir do PDF selecionado + evidência textual + auditoria automática; cards sem evidência são descartados.
- Questões dentro do site, sem PDF: 3–10 questões, correção na janela, resultado e erros enviados automaticamente ao Caderno de Erros.
- Modo Foco & Memorização: transforma PDF longo em 6–8 micro-blocos de 8–15 minutos, com objetivo, 3 pontos-chave, recordação ativa e checkpoint. Inclui temporizador de 15/25 minutos.
- Aula do cronograma: botão “Ver aula/Aula” chama uma aula baseada no PDF processado associado; se não houver associação exata, procura um PDF pronto da mesma disciplina e informa claramente quando não houver material.

## Variáveis de ambiente
- OPENAI_API_KEY
- OPENAI_MODEL=gpt-5.6-luna
- OPENAI_VECTOR_STORE_ID (opcional)
- SUPABASE_URL
- SUPABASE_SERVICE_ROLE_KEY (ou chave secreta compatível, se o código for adaptado)
- SUPABASE_BUCKET=Professor-md-pdfs (use exatamente o nome do bucket criado no Supabase)

## Observações
- A biblioteca permanente depende do Supabase Storage.
- O processamento dos PDFs e a geração de materiais dependem da OpenAI API.
- O Modo Foco é uma ferramenta de organização e estudo; não é tratamento médico.
- As questões são originais inspiradas no estilo de cobrança da FUMARC, não questões oficiais.
