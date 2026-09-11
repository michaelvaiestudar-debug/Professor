# Professor MD 4.4 — Biblioteca Permanente + Cronograma Organizado

Esta versão mantém os recursos do Professor MD 4.3 e adiciona duas melhorias importantes:

## 1. Biblioteca permanente de PDFs

A área **Minha biblioteca de PDFs** foi preparada para usar **Supabase Storage** como armazenamento permanente.

Quando configurada, o PDF é salvo no armazenamento externo antes do processamento da IA. A biblioteca também é reconstruída após reinícios ou novos deploys do Render, usando os metadados guardados junto ao arquivo.

### Configuração no Render

Defina estas variáveis de ambiente:

- `OPENAI_API_KEY` = sua chave da OpenAI
- `OPENAI_MODEL` = `gpt-5.6-luna`
- `OPENAI_VECTOR_STORE_ID` = opcional; pode ser usado para fixar um Vector Store permanente
- `SUPABASE_URL` = URL do seu projeto Supabase
- `SUPABASE_SERVICE_ROLE_KEY` = chave **service_role** do projeto Supabase (somente no servidor/Render)
- `SUPABASE_BUCKET` = `professor-md-pdfs`

No Supabase, crie um bucket privado com o mesmo nome de `SUPABASE_BUCKET`.

> Não coloque a `SUPABASE_SERVICE_ROLE_KEY` nem a `OPENAI_API_KEY` no GitHub ou no código do navegador.

### O que acontece quando a biblioteca está ativa

- PDF original fica no armazenamento externo.
- Metadados do PDF são preservados.
- Após reinício/redeploy, o Professor MD consegue reconstruir a lista da biblioteca.
- Cada PDF tem **Abrir PDF**.
- O PDF continua podendo ser usado pelo File Search quando o processamento da IA estiver concluído.

### Sem Supabase configurado

O aplicativo continua funcionando em modo local, mas a pasta `uploads/` depende do filesystem do serviço. Nesse modo, não há garantia de permanência após reinício/redeploy.

## 2. Interface reorganizada

A ordem principal agora é:

1. Fale com o Professor MD
2. Minha biblioteca de PDFs
3. Registrar estudo
4. Seu progresso
5. Caderno de Erros
6. Seu cronograma — próximos 5 assuntos
7. Cronograma completo do edital
8. Configurações

A ideia é deixar primeiro tudo o que você usa **durante o estudo** e concentrar os cronogramas na parte inferior.

## Recursos mantidos

- Cronograma baseado no edital para Analista Judiciário — Especialidade Contabilidade.
- Próximos 5 assuntos.
- `Dar OK` para concluir assunto.
- Percentual geral e por disciplina.
- Revisões R0, R1, R7 e R30.
- Caderno de erros.
- Upload de PDF em segundo plano.
- Professor MD com File Search.
- Voz do navegador.
- Mapa mental em PDF.
- Flashcards em PDF.

## Render

Start Command:

```bash
uvicorn app:app --host 0.0.0.0 --port $PORT
```

Após configurar as variáveis, faça o deploy da versão 4.4.
