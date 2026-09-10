
from pathlib import Path
import os, sqlite3, json
from datetime import datetime, date, timedelta
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI

BASE = Path(__file__).resolve().parent
STATIC = BASE / "static"
INDEX = STATIC / "index.html"
UPLOADS = BASE / "uploads"
DB = BASE / "professor_md.db"
UPLOADS.mkdir(exist_ok=True)

MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
VECTOR_STORE_ID = os.getenv("OPENAI_VECTOR_STORE_ID", "").strip()

app = FastAPI(title="Professor MD", version="4.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"]
)

def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY, value TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS materials(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT, openai_file_id TEXT, vector_store_id TEXT,
        subject TEXT, topic TEXT, created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS sessions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        discipline TEXT, topic TEXT, minutes INTEGER,
        questions INTEGER, correct INTEGER, score REAL,
        notes TEXT, created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS errors(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        discipline TEXT, topic TEXT, question TEXT,
        mistake TEXT, action TEXT, review_date TEXT,
        created_at TEXT)""")
    c.commit()
    return c

def get_setting(key):
    c = conn()
    row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    c.close()
    return row["value"] if row else ""

def set_setting(key, value):
    c = conn()
    c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))
    c.commit(); c.close()

def current_vs():
    return VECTOR_STORE_ID or get_setting("vector_store_id")

@app.get("/", response_class=HTMLResponse)
def home():
    return FileResponse(INDEX)

@app.get("/health")
def health():
    return {"status":"ok","app":"Professor MD","version":"4.0"}

@app.get("/api/status")
def status():
    return {
        "online": True,
        "openai_configured": bool(os.getenv("OPENAI_API_KEY")),
        "file_search_configured": bool(current_vs()),
        "model": MODEL
    }

def ai_answer(prompt, context_hint=""):
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("Configure OPENAI_API_KEY no Render antes de usar o Professor.")
    c = OpenAI(api_key=key)
    vs = current_vs()
    tools = [{"type":"file_search","vector_store_ids":[vs]}] if vs else None
    instructions = """Você é o Professor MD, professor particular para concursos, focado em TRT-MG/FUMARC e Contabilidade.
Se houver arquivos no File Search, use-os como fonte principal. Não invente páginas, regras ou conteúdo que não esteja disponível.
Explique em português do Brasil, de forma didática e objetiva.
Quando apropriado, organize: 1) conceito, 2) exemplo, 3) pegadinha de prova, 4) mini questão, 5) feedback.
Para questões, deixe claro que são questões originais inspiradas no estilo de cobrança, não questões oficiais da FUMARC.
Não diga que é afiliado à Estratégia Concursos."""
    if context_hint:
        instructions += "\nContexto adicional: " + context_hint
    r = c.responses.create(
        model=MODEL,
        instructions=instructions,
        input=prompt,
        tools=tools
    )
    return r.output_text

@app.post("/api/ask")
def ask(text: str = Form(...), mode: str = Form("tutor")):
    try:
        extra = {
            "explain":"Explique o conteúdo solicitado passo a passo.",
            "questions":"Crie 5 questões originais de múltipla escolha, inspiradas no estilo FUMARC, com gabarito e explicação.",
            "review":"Faça uma revisão ativa: resumo curto, 5 perguntas e depois aguarde minhas respostas.",
            "plan":"Use os materiais disponíveis para sugerir páginas/tópicos para a sessão de hoje. Não invente páginas."
        }.get(mode, "")
        return {"ok": True, "answer": ai_answer(text, extra)}
    except Exception as e:
        return JSONResponse({"ok":False,"error":str(e)}, status_code=500)

def ensure_vector_store(client):
    vs = current_vs()
    if vs:
        return vs
    created = client.vector_stores.create(name="Professor MD - PDFs")
    set_setting("vector_store_id", created.id)
    return created.id

@app.post("/api/upload")
async def upload(file: UploadFile = File(...), subject: str = Form(""), topic: str = Form("")):
    if not file.filename.lower().endswith(".pdf"):
        return JSONResponse({"ok":False,"error":"Envie um arquivo PDF."}, status_code=400)
    data = await file.read()
    name = Path(file.filename).name
    (UPLOADS / name).write_bytes(data)
    oid = None; vs = current_vs()
    msg = "PDF salvo no servidor."
    if os.getenv("OPENAI_API_KEY"):
        try:
            client = OpenAI()
            f = client.files.create(file=(name, data), purpose="assistants")
            oid = f.id
            vs = ensure_vector_store(client)
            client.vector_stores.files.create(vector_store_id=vs, file_id=oid)
            msg = "PDF processado e adicionado à base de conhecimento."
        except Exception as e:
            msg = "PDF salvo, mas a integração com a OpenAI falhou: " + str(e)
    c = conn()
    c.execute("""INSERT INTO materials(filename,openai_file_id,vector_store_id,subject,topic,created_at)
                 VALUES(?,?,?,?,?,?)""", (name,oid,vs,subject,topic,datetime.now().isoformat()))
    c.commit(); c.close()
    return {"ok":True,"filename":name,"message":msg,"file_search_configured":bool(vs)}

@app.post("/api/session")
def session(discipline: str=Form(""), topic: str=Form(""), minutes: int=Form(60),
            questions: int=Form(0), correct: int=Form(0), notes: str=Form("")):
    score = round((correct/questions)*100, 1) if questions else 0
    c=conn()
    c.execute("""INSERT INTO sessions(discipline,topic,minutes,questions,correct,score,notes,created_at)
                 VALUES(?,?,?,?,?,?,?,?)""",
              (discipline,topic,minutes,questions,correct,score,notes,datetime.now().isoformat()))
    c.commit(); c.close()
    return {"ok":True,"score":score}

@app.post("/api/error")
def error(discipline: str=Form(""), topic: str=Form(""), question: str=Form(""),
          mistake: str=Form(""), action: str=Form(""), review_date: str=Form("")):
    c=conn()
    c.execute("""INSERT INTO errors(discipline,topic,question,mistake,action,review_date,created_at)
                 VALUES(?,?,?,?,?,?,?)""",
              (discipline,topic,question,mistake,action,review_date,datetime.now().isoformat()))
    c.commit(); c.close()
    return {"ok":True}

@app.get("/api/dashboard")
def dashboard():
    c=conn()
    s=c.execute("SELECT * FROM sessions ORDER BY id DESC LIMIT 100").fetchall()
    m=c.execute("SELECT filename,subject,topic,created_at FROM materials ORDER BY id DESC LIMIT 20").fetchall()
    e=c.execute("SELECT * FROM errors ORDER BY id DESC LIMIT 20").fetchall()
    c.close()
    total_min=sum(int(x["minutes"] or 0) for x in s)
    q=sum(int(x["questions"] or 0) for x in s)
    correct=sum(int(x["correct"] or 0) for x in s)
    score=round(correct/q*100,1) if q else 0
    return {"hours":round(total_min/60,1),"questions":q,"correct":correct,
            "score":score,"weak_topics":len(set(x["topic"] for x in e if x["topic"])),
            "materials":[dict(x) for x in m],"errors":[dict(x) for x in e]}

@app.get("/api/today")
def today():
    c=conn()
    mats=c.execute("SELECT filename,subject,topic FROM materials ORDER BY id DESC LIMIT 10").fetchall()
    errors=c.execute("SELECT discipline,topic,review_date FROM errors WHERE review_date<=? ORDER BY id DESC LIMIT 5",
                     (date.today().isoformat(),)).fetchall()
    c.close()
    blocks = [
        {"discipline":"Contabilidade Geral","topic":"Ativo Imobilizado",
         "pages":"Definidas pelo PDF processado","theory":40,"reverse":20},
        {"discipline":"Contabilidade Pública","topic":"CASP / MCASP",
         "pages":"Definidas pelo PDF processado","theory":40,"reverse":20}
    ]
    if mats:
        for i,m in enumerate(mats[:2]):
            if m["subject"] or m["topic"]:
                blocks[i]["discipline"] = m["subject"] or blocks[i]["discipline"]
                blocks[i]["topic"] = m["topic"] or blocks[i]["topic"]
                blocks[i]["material"] = m["filename"]
    return {"date":date.today().strftime("%d/%m/%Y"),"blocks":blocks,
            "reviews":[dict(x) for x in errors]}

@app.get("/api/reviews")
def reviews():
    c=conn()
    rows=c.execute("SELECT * FROM errors ORDER BY review_date ASC LIMIT 50").fetchall()
    c.close()
    return {"items":[dict(x) for x in rows]}

@app.get("/api/materials")
def materials():
    c=conn(); rows=c.execute("SELECT * FROM materials ORDER BY id DESC").fetchall(); c.close()
    return {"items":[dict(x) for x in rows]}
