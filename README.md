# Professor MD 2.1 — versão corrigida

## Render
Build Command:
pip install -r requirements.txt

Start Command:
uvicorn app:app --host 0.0.0.0 --port $PORT

A página inicial está em `/`.
Teste também:
- `/health`
- `/docs`

Esta versão corrige o problema em que o Render retornava `Not Found` na raiz.
