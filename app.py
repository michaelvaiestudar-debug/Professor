from pathlib import Path
import os
import sqlite3
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

BASE = Path(__file__).resolve().parent
STATIC_DIR = BASE / "static"
INDEX_FILE = STATIC_DIR / "index.html"
UPLOADS_DIR = BASE / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Professor MD", version="2.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/", response_class=HTMLResponse)
def home():
    if not INDEX_FILE.exists():
        return HTMLResponse(
            "<h1>Professor MD</h1><p>O arquivo da página inicial não foi encontrado.</p>",
            status_code=500,
        )
    return FileResponse(INDEX_FILE)

@app.get("/health")
def health():
    return {"status": "ok", "app": "Professor MD"}

@app.get("/api/status")
def api_status():
    return {
        "status": "online",
        "app": "Professor MD",
        "openai_configured": bool(os.getenv("OPENAI_API_KEY"))
    }

@app.post("/api/upload")
async def upload_pdf(file: UploadFile = File(...)):
    if not file.filename:
        return {"ok": False, "error": "Arquivo sem nome."}
    destination = UPLOADS_DIR / Path(file.filename).name
    content = await file.read()
    destination.write_bytes(content)
    return {"ok": True, "filename": destination.name}

@app.get("/docs-link")
def docs_link():
    return {"message": "A documentação FastAPI está disponível em /docs."}
