# Professor MD 4.1

Versão corrigida do Professor MD com envio de PDF mais robusto para Render.

## Principal correção
O upload agora é dividido em duas etapas:
1. O servidor recebe e salva o PDF rapidamente e responde ao navegador.
2. O processamento na OpenAI/File Search acontece em segundo plano.

A interface mostra `Recebido`, `Processando`, `Pronto` ou `Erro` e consulta o status automaticamente. Isso evita que a tela fique presa em `Processando PDF...` enquanto a API da OpenAI trabalha.

## Render
Start Command:
```bash
uvicorn app:app --host 0.0.0.0 --port $PORT
```

Environment Variables:
- `OPENAI_API_KEY` = sua chave da OpenAI (não publique no GitHub)
- `OPENAI_MODEL` = `gpt-5.6-luna`
- `OPENAI_VECTOR_STORE_ID` = opcional; se vazio, o primeiro PDF cria um vector store automaticamente

## Observações
- Limite de upload desta versão: 25 MB por PDF.
- SQLite e a pasta `uploads/` continuam dependentes do filesystem do serviço. Para produção com persistência após reinícios/deploys, use banco/armazenamento persistente.
- O processamento em segundo plano é intencionalmente simples e adequado ao protótipo no Render; para grande volume, migrar para uma fila de jobs é o próximo passo.
