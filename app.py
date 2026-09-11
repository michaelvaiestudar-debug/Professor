from pathlib import Path
import os, sqlite3, json, threading, time
from datetime import datetime, date
import re
import textwrap
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
GENERATED = BASE / "generated"
GENERATED.mkdir(exist_ok=True)

MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
VECTOR_STORE_ID = os.getenv("OPENAI_VECTOR_STORE_ID", "").strip()
MAX_PDF_BYTES = 25 * 1024 * 1024
VS_LOCK = threading.Lock()

app = FastAPI(title="Professor MD", version="4.2")
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
        subject TEXT, topic TEXT, created_at TEXT,
        processing_status TEXT DEFAULT 'pending',
        processing_error TEXT DEFAULT '',
        size_bytes INTEGER DEFAULT 0)""")
    # Migration for databases created by Professor MD 4.0.
    cols = {r[1] for r in c.execute("PRAGMA table_info(materials)").fetchall()}
    if "processing_status" not in cols:
        c.execute("ALTER TABLE materials ADD COLUMN processing_status TEXT DEFAULT 'ready'")
    if "processing_error" not in cols:
        c.execute("ALTER TABLE materials ADD COLUMN processing_error TEXT DEFAULT ''")
    if "size_bytes" not in cols:
        c.execute("ALTER TABLE materials ADD COLUMN size_bytes INTEGER DEFAULT 0")
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
    return {"status": "ok", "app": "Professor MD", "version": "4.2"}


@app.get("/api/status")
def status():
    return {
        "online": True,
        "openai_configured": bool(os.getenv("OPENAI_API_KEY")),
        "file_search_configured": bool(current_vs()),
        "model": MODEL,
        "upload_mode": "background"
    }


def ai_answer(prompt, context_hint=""):
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("Configure OPENAI_API_KEY no Render antes de usar o Professor.")
    c = OpenAI(api_key=key)
    vs = current_vs()
    tools = [{"type": "file_search", "vector_store_ids": [vs]}] if vs else None
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
            "explain": "Explique o conteúdo solicitado passo a passo.",
            "questions": "Crie 5 questões originais de múltipla escolha, inspiradas no estilo FUMARC, com gabarito e explicação.",
            "review": "Faça uma revisão ativa: resumo curto, 5 perguntas e depois aguarde minhas respostas.",
            "plan": "Use os materiais disponíveis para sugerir páginas/tópicos para a sessão de hoje. Não invente páginas."
        }.get(mode, "")
        return {"ok": True, "answer": ai_answer(text, extra)}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


def ensure_vector_store(client):
    vs = current_vs()
    if vs:
        return vs
    # Prevent two simultaneous PDF uploads from creating two vector stores.
    with VS_LOCK:
        vs = current_vs()
        if vs:
            return vs
        created = client.vector_stores.create(name="Professor MD - PDFs")
        set_setting("vector_store_id", created.id)
        return created.id


def update_material(material_id, **fields):
    if not fields:
        return
    c = conn()
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [material_id]
    c.execute(f"UPDATE materials SET {sets} WHERE id=?", vals)
    c.commit(); c.close()


def process_pdf_background(material_id, name, data):
    """Process the PDF outside the upload HTTP request.

    The browser receives the upload result immediately. This worker then uploads
    the file to OpenAI, attaches it to the vector store, and records success/error.
    """
    try:
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY não está configurada no Render.")
        update_material(material_id, processing_status="processing", processing_error="")
        client = OpenAI(api_key=key, timeout=60.0, max_retries=2)

        # Upload the file to OpenAI. This can take time, so it is intentionally
        # outside the browser request.
        f = client.files.create(file=(name, data), purpose="assistants")
        oid = f.id
        update_material(material_id, openai_file_id=oid)

        vs = ensure_vector_store(client)
        update_material(material_id, vector_store_id=vs)

        # Attach the file and return immediately. The vector-store service can
        # continue processing it after this call; the UI will show the state.
        client.vector_stores.files.create(vector_store_id=vs, file_id=oid)
        update_material(material_id, processing_status="ready", processing_error="")
    except Exception as e:
        update_material(material_id, processing_status="error", processing_error=str(e))


@app.post("/api/upload")
async def upload(file: UploadFile = File(...), subject: str = Form(""), topic: str = Form("")):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        return JSONResponse({"ok": False, "error": "Envie um arquivo PDF."}, status_code=400)

    data = await file.read()
    if not data:
        return JSONResponse({"ok": False, "error": "O PDF está vazio."}, status_code=400)
    if len(data) > MAX_PDF_BYTES:
        return JSONResponse({"ok": False, "error": "PDF muito grande. O limite desta versão é 25 MB."}, status_code=413)

    name = Path(file.filename).name
    # Avoid overwriting a previous file with the same name.
    safe_name = name
    target = UPLOADS / safe_name
    if target.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = f"{target.stem}_{stamp}{target.suffix}"
        target = UPLOADS / safe_name
    target.write_bytes(data)

    c = conn()
    cur = c.execute("""INSERT INTO materials(
        filename, openai_file_id, vector_store_id, subject, topic, created_at,
        processing_status, processing_error, size_bytes)
        VALUES(?,?,?,?,?,?,?,?,?)""",
        (safe_name, None, current_vs(), subject, topic,
         datetime.now().isoformat(), "queued", "", len(data)))
    material_id = cur.lastrowid
    c.commit(); c.close()

    # Start processing without blocking the browser request.
    worker = threading.Thread(
        target=process_pdf_background,
        args=(material_id, safe_name, data),
        daemon=True,
        name=f"professor-md-pdf-{material_id}"
    )
    worker.start()

    return {
        "ok": True,
        "id": material_id,
        "filename": safe_name,
        "status": "queued",
        "message": "PDF recebido. O processamento continuará em segundo plano.",
        "file_search_configured": bool(current_vs())
    }


@app.get("/api/materials/{material_id}")
def material_status(material_id: int):
    c = conn()
    row = c.execute("SELECT * FROM materials WHERE id=?", (material_id,)).fetchone()
    c.close()
    if not row:
        return JSONResponse({"ok": False, "error": "PDF não encontrado."}, status_code=404)
    return {"ok": True, "item": dict(row)}


def _clean_json_text(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _source_prompt(material_id, kind):
    c = conn()
    row = c.execute("SELECT * FROM materials WHERE id=?", (material_id,)).fetchone()
    c.close()
    if not row:
        raise RuntimeError("PDF não encontrado.")
    if row["processing_status"] != "ready":
        raise RuntimeError("Este PDF ainda não está pronto. Aguarde o processamento terminar.")
    filename = row["filename"]
    subject = row["subject"] or ""
    topic = row["topic"] or ""
    if kind == "flashcards":
        schema = {
          "title": "título curto",
          "cards": [{"question": "pergunta", "answer": "resposta objetiva e fiel ao PDF", "trap": "pegadinha ou ponto de atenção, se houver", "level": "basico|intermediario|fumarc"}]
        }
        instruction = f'''Crie 18 flashcards de estudo EXCLUSIVAMENTE com base no PDF chamado "{filename}".
Disciplina: {subject}. Assunto: {topic}.
Use o File Search e priorize esse arquivo; não misture conteúdo de outros PDFs.
Distribua aproximadamente 6 básicos, 6 intermediários e 6 no nível "fumarc".
O nível fumarc deve cobrar distinções, exceções, classificações e pegadinhas que estejam sustentadas pelo PDF.
Não invente leis, artigos, páginas, exemplos ou fatos que não estejam no arquivo.
Respostas curtas, precisas e úteis para revisão.
Retorne SOMENTE JSON válido neste formato: {json.dumps(schema, ensure_ascii=False)}'''
    else:
        schema = {
          "title": "título",
          "central": "tema central",
          "branches": [{"title": "ramo", "items": ["item 1", "item 2", "item 3"]}],
          "traps": ["pegadinha 1", "pegadinha 2"]
        }
        instruction = f'''Monte um mapa mental visual para estudo EXCLUSIVAMENTE com base no PDF chamado "{filename}".
Disciplina: {subject}. Assunto: {topic}.
Use o File Search e priorize esse arquivo; não misture conteúdo de outros PDFs.
Crie 4 a 7 ramos principais, com 2 a 5 itens curtos por ramo. Inclua de 3 a 6 pegadinhas somente quando sustentadas pelo PDF.
Preserve a terminologia e a organização do material. Não invente conteúdo.
Retorne SOMENTE JSON válido neste formato: {json.dumps(schema, ensure_ascii=False)}'''
    return row, instruction


def _ai_json(material_id, kind):
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("Configure OPENAI_API_KEY no Render antes de gerar o PDF.")
    vs = current_vs()
    if not vs:
        raise RuntimeError("O File Search ainda não está configurado. Envie e processe um PDF primeiro.")
    row, instruction = _source_prompt(material_id, kind)
    client = OpenAI(api_key=key, timeout=120.0, max_retries=2)
    r = client.responses.create(
        model=MODEL,
        instructions="Você é o Professor MD. Trabalhe estritamente com o arquivo solicitado. Responda em português do Brasil.",
        input=instruction,
        tools=[{"type": "file_search", "vector_store_ids": [vs]}],
        text={"format": {"type": "json_object"}}
    )
    raw = _clean_json_text(r.output_text)
    try:
        data = json.loads(raw)
    except Exception as e:
        raise RuntimeError("A IA não retornou o formato estruturado esperado. Tente gerar novamente.") from e
    return row, data


def _safe_filename(s):
    s = re.sub(r"[^A-Za-z0-9À-ÿ_-]+", "_", s or "material")
    return s.strip("_")[:80] or "material"


def _build_flashcards_pdf(title, cards, out_path):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle
    from reportlab.lib.units import mm
    doc = SimpleDocTemplate(str(out_path), pagesize=A4, rightMargin=14*mm, leftMargin=14*mm, topMargin=14*mm, bottomMargin=14*mm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("title", parent=styles["Title"], fontSize=20, leading=24, alignment=TA_CENTER, spaceAfter=10)
    q_style = ParagraphStyle("q", parent=styles["Heading2"], fontSize=16, leading=21, alignment=TA_CENTER, spaceAfter=10)
    a_style = ParagraphStyle("a", parent=styles["BodyText"], fontSize=12, leading=17)
    small = ParagraphStyle("small", parent=styles["BodyText"], fontSize=9, leading=12)
    story=[Paragraph("FLASHCARDS — " + title, title_style), Paragraph("Professor MD • revisão ativa", small), Spacer(1, 8)]
    for i, card in enumerate(cards, 1):
        story.append(Spacer(1, 25*mm))
        story.append(Paragraph(f"FLASHCARD {i} • {str(card.get('level','')).upper()}", small))
        story.append(Spacer(1, 6*mm))
        story.append(Paragraph(str(card.get("question", "")), q_style))
        story.append(Spacer(1, 65*mm))
        story.append(Paragraph("Vire a página para conferir a resposta.", small))
        story.append(PageBreak())
        story.append(Paragraph(f"FLASHCARD {i} — RESPOSTA", title_style))
        box = Table([[Paragraph("<b>Resposta</b><br/>" + str(card.get("answer", "")), a_style)], [Paragraph("<b>Ponto de atenção</b><br/>" + str(card.get("trap", "") or "—"), a_style)]], colWidths=[165*mm])
        box.setStyle(TableStyle([("BOX",(0,0),(-1,-1),1,colors.black),("INNERGRID",(0,0),(-1,-1),0.5,colors.grey),("BACKGROUND",(0,0),(-1,0),colors.whitesmoke),("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),10),("RIGHTPADDING",(0,0),(-1,-1),10),("TOPPADDING",(0,0),(-1,-1),10),("BOTTOMPADDING",(0,0),(-1,-1),10)]))
        story.append(Spacer(1, 20*mm)); story.append(box)
        if i < len(cards): story.append(PageBreak())
    doc.build(story)


def _build_mindmap_pdf(title, central, branches, traps, out_path):
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    W,H=landscape(A4)
    c=canvas.Canvas(str(out_path), pagesize=(W,H))
    c.setTitle("Mapa Mental - " + title)
    c.setFont("Helvetica-Bold", 20); c.drawCentredString(W/2,H-20*mm,"MAPA MENTAL — " + title)
    c.setFont("Helvetica", 8); c.drawCentredString(W/2,H-26*mm,"Professor MD • baseado no PDF selecionado")
    cx,cy=W/2,H/2+8*mm; cw,ch=62*mm,28*mm
    c.setLineWidth(1.2); c.roundRect(cx-cw/2,cy-ch/2,cw,ch,5*mm,stroke=1,fill=0)
    c.setFont("Helvetica-Bold", 13); c.drawCentredString(cx,cy+3*mm,str(central)[:48])
    c.setFont("Helvetica", 8); c.drawCentredString(cx,cy-6*mm,"TEMA CENTRAL")
    n=len(branches); cols=min(4,max(1,n))
    for idx,b in enumerate(branches):
        col=idx%cols; row=idx//cols
        x=(col+0.5)*W/cols; y=H-48*mm-row*48*mm
        c.line(cx,cy,x,y)
        bw,bh=55*mm,32*mm
        c.roundRect(x-bw/2,y-bh/2,bw,bh,4*mm,stroke=1,fill=0)
        c.setFont("Helvetica-Bold",9); c.drawCentredString(x,y+10*mm,str(b.get("title","Ramo"))[:34])
        c.setFont("Helvetica",7); yy=y+3*mm
        for it in b.get("items",[])[:5]:
            c.drawString(x-bw/2+3*mm,yy,"• "+str(it)[:54]); yy-=4*mm
    c.showPage()
    c.setFont("Helvetica-Bold",20); c.drawString(18*mm,H-20*mm,"PEGADINHAS DE PROVA")
    c.setFont("Helvetica",10); c.drawString(18*mm,H-28*mm,"Somente pontos sustentados pelo PDF selecionado.")
    y=H-42*mm
    for i,t in enumerate(traps[:10],1):
        lines=textwrap.wrap(str(t), width=95)
        c.setFont("Helvetica-Bold",10); c.drawString(20*mm,y,f"{i}.")
        c.setFont("Helvetica",10); yy=y
        for line in lines:
            c.drawString(28*mm,yy,line); yy-=5*mm
        y=yy-4*mm
        if y<20*mm:
            c.showPage(); y=H-20*mm
    c.save()


@app.post("/api/generate/flashcards")
def generate_flashcards(material_id: int = Form(...)):
    try:
        row, data = _ai_json(material_id, "flashcards")
        cards = data.get("cards", [])
        if not cards: raise RuntimeError("Nenhum flashcard foi gerado.")
        filename = f"flashcards_{_safe_filename(row['filename'])}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        out = GENERATED / filename
        _build_flashcards_pdf(data.get("title") or row["filename"], cards, out)
        return {"ok": True, "filename": filename, "url": f"/api/generated/{filename}", "count": len(cards)}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/generate/mindmap")
def generate_mindmap(material_id: int = Form(...)):
    try:
        row, data = _ai_json(material_id, "mindmap")
        branches = data.get("branches", [])
        if not branches: raise RuntimeError("Nenhum ramo de mapa mental foi gerado.")
        filename = f"mapa_mental_{_safe_filename(row['filename'])}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        out = GENERATED / filename
        _build_mindmap_pdf(data.get("title") or row["filename"], data.get("central") or row["topic"] or row["filename"], branches, data.get("traps", []), out)
        return {"ok": True, "filename": filename, "url": f"/api/generated/{filename}"}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/generated/{filename}")
def generated(filename: str):
    safe = Path(filename).name
    p = GENERATED / safe
    if not p.exists():
        return JSONResponse({"ok": False, "error": "Arquivo não encontrado."}, status_code=404)
    return FileResponse(p, media_type="application/pdf", filename=safe)


@app.post("/api/session")
def session(discipline: str = Form(""), topic: str = Form(""), minutes: int = Form(60),
            questions: int = Form(0), correct: int = Form(0), notes: str = Form("")):
    score = round((correct / questions) * 100, 1) if questions else 0
    c = conn()
    c.execute("""INSERT INTO sessions(discipline,topic,minutes,questions,correct,score,notes,created_at)
                 VALUES(?,?,?,?,?,?,?,?)""",
              (discipline, topic, minutes, questions, correct, score, notes, datetime.now().isoformat()))
    c.commit(); c.close()
    return {"ok": True, "score": score}


@app.post("/api/error")
def error(discipline: str = Form(""), topic: str = Form(""), question: str = Form(""),
          mistake: str = Form(""), action: str = Form(""), review_date: str = Form("")):
    c = conn()
    c.execute("""INSERT INTO errors(discipline,topic,question,mistake,action,review_date,created_at)
                 VALUES(?,?,?,?,?,?,?)""",
              (discipline, topic, question, mistake, action, review_date, datetime.now().isoformat()))
    c.commit(); c.close()
    return {"ok": True}


@app.get("/api/dashboard")
def dashboard():
    c = conn()
    s = c.execute("SELECT * FROM sessions ORDER BY id DESC LIMIT 100").fetchall()
    m = c.execute("SELECT filename,subject,topic,created_at,processing_status,processing_error,size_bytes FROM materials ORDER BY id DESC LIMIT 20").fetchall()
    e = c.execute("SELECT * FROM errors ORDER BY id DESC LIMIT 20").fetchall()
    c.close()
    total_min = sum(int(x["minutes"] or 0) for x in s)
    q = sum(int(x["questions"] or 0) for x in s)
    correct = sum(int(x["correct"] or 0) for x in s)
    score = round(correct / q * 100, 1) if q else 0
    return {"hours": round(total_min / 60, 1), "questions": q, "correct": correct,
            "score": score, "weak_topics": len(set(x["topic"] for x in e if x["topic"])),
            "materials": [dict(x) for x in m], "errors": [dict(x) for x in e]}


@app.get("/api/today")
def today():
    c = conn()
    mats = c.execute("SELECT filename,subject,topic,processing_status FROM materials ORDER BY id DESC LIMIT 10").fetchall()
    errors = c.execute("SELECT discipline,topic,review_date FROM errors WHERE review_date<=? ORDER BY id DESC LIMIT 5",
                       (date.today().isoformat(),)).fetchall()
    c.close()
    blocks = [
        {"discipline": "Contabilidade Geral", "topic": "Ativo Imobilizado",
         "pages": "Definidas pelo PDF processado", "theory": 40, "reverse": 20},
        {"discipline": "Contabilidade Pública", "topic": "CASP / MCASP",
         "pages": "Definidas pelo PDF processado", "theory": 40, "reverse": 20}
    ]
    ready_mats = [m for m in mats if m["processing_status"] in ("ready", "processing", "queued")]
    if ready_mats:
        for i, m in enumerate(ready_mats[:2]):
            if m["subject"] or m["topic"]:
                blocks[i]["discipline"] = m["subject"] or blocks[i]["discipline"]
                blocks[i]["topic"] = m["topic"] or blocks[i]["topic"]
                blocks[i]["material"] = m["filename"]
    return {"date": date.today().strftime("%d/%m/%Y"), "blocks": blocks,
            "reviews": [dict(x) for x in errors]}


@app.get("/api/reviews")
def reviews():
    c = conn(); rows = c.execute("SELECT * FROM errors ORDER BY review_date ASC LIMIT 50").fetchall(); c.close()
    return {"items": [dict(x) for x in rows]}


@app.get("/api/materials")
def materials():
    c = conn(); rows = c.execute("SELECT * FROM materials ORDER BY id DESC").fetchall(); c.close()
    return {"items": [dict(x) for x in rows]}
