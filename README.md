# Professor MD 4.3

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


## 4.2 — geradores mantidos
- Gerador de mapa mental em PDF baseado no PDF selecionado.
- Gerador de 18 flashcards em PDF, com frente e resposta em páginas alternadas.
- Conteúdo dos geradores é solicitado via File Search e deve permanecer fiel ao PDF selecionado.
- PDFs gerados ficam disponíveis em `/api/generated/...`.

## 4.3 — Cronograma Inteligente
- Cronograma completo estruturado a partir dos conteúdos do edital enviado para o cargo de Analista Judiciário — Especialidade Contabilidade.
- 154 unidades de estudo organizadas em ciclo de 2 blocos por dia, 6 dias por semana.
- Painel dos próximos 5 assuntos.
- Botão `Dar OK` para concluir cada assunto e liberar automaticamente o próximo.
- Percentual geral do edital e percentual por disciplina.
- Status visual: `Em estudo`, `Pendente` e `Concluído`.
- Revisões automáticas R0, R1, R7 e R30 após a conclusão de um assunto, com botão para marcar a revisão como feita.
- O cronograma acompanha o progresso salvo no SQLite.

### Importante
O Professor MD 4.3 controla o **progresso dos assuntos**, mas ainda não faz a extração automática e confiável das páginas exatas de cada PDF. Quando um PDF é enviado, o assunto pode ser informado no campo de tópico e usado pelo Professor como material associado.
