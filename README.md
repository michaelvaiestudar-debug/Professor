# Professor MD 4.0

Versão visual inspirada no painel aprovado pelo usuário.
Inclui dashboard responsivo, plano diário, tutor IA, voz, PDFs, registro de estudo,
desempenho, revisões e caderno de erros.

Render:
Build: pip install -r requirements.txt
Start: uvicorn app:app --host 0.0.0.0 --port $PORT

Environment:
OPENAI_API_KEY = sua chave
OPENAI_MODEL = gpt-5.6-luna
OPENAI_VECTOR_STORE_ID = opcional; se vazio, o primeiro upload cria um vector store.

A aplicação mantém a chave apenas no servidor.
