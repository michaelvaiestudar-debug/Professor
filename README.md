# Professor MD 4.7 — interface móvel e seleção de PDFs corrigida

Esta versão corrige problemas observados no Android/tablet:

- seletor de PDF com área de toque real e grande, sem `display:none` no input;
- clique em toda a área de seleção abre o seletor nativo do dispositivo;
- nome e tamanho do PDF aparecem imediatamente após a escolha;
- o arquivo escolhido não é apagado da interface após o envio;
- seletores de material de revisão e Modo Foco mostram todos os PDFs da biblioteca com o status atual;
- PDFs em processamento aparecem como “Processando/Aguardando” e passam a ficar utilizáveis quando o servidor muda para “Pronto”;
- layout móvel reorganizado para evitar colunas comprimidas, botões espremidos e conteúdo excessivamente pequeno;
- botão “Ver aula” abre uma janela própria e visível para a aula do Professor MD, em vez de depender apenas do bloco de chat;
- biblioteca permanente via Supabase e processamento OpenAI mantidos;
- mapa mental, flashcards, questões no site, caderno de erros e Modo Foco mantidos.

## Deploy

Use os mesmos comandos/variáveis do Professor MD 4.6. Não é necessário trocar as chaves.

Variáveis:
- OPENAI_API_KEY
- OPENAI_MODEL (opcional; padrão gpt-5.6-luna)
- OPENAI_VECTOR_STORE_ID (opcional)
- SUPABASE_URL
- SUPABASE_SERVICE_ROLE_KEY
- SUPABASE_BUCKET (use exatamente o nome do bucket criado no Supabase)

## Observação

A seleção do arquivo depende do seletor nativo do Android/Samsung Browser. A página não consegue abrir o gerenciador de arquivos sem uma ação do usuário, mas a área de toque agora usa um input real, visível ao navegador e sobreposto à área inteira, em vez de esconder o input com `display:none`.
