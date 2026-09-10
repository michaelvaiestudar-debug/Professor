from pathlib import Path
import os,sqlite3
from datetime import datetime,date
from fastapi import FastAPI,UploadFile,File,Form
from fastapi.responses import FileResponse,HTMLResponse,JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
BASE=Path(__file__).resolve().parent; INDEX=BASE/"static/index.html"; UP=BASE/"uploads"; DB=BASE/"professor_md.db"
UP.mkdir(exist_ok=True); MODEL=os.getenv("OPENAI_MODEL","gpt-5.6-luna"); VS=os.getenv("OPENAI_VECTOR_STORE_ID","").strip()
app=FastAPI(title="Professor MD",version="3.0")
app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_credentials=True,allow_methods=["*"],allow_headers=["*"])
def db():
 c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
 c.execute("CREATE TABLE IF NOT EXISTS sessions(id INTEGER PRIMARY KEY,discipline TEXT,topic TEXT,minutes INTEGER,score REAL,notes TEXT,created_at TEXT)")
 c.execute("CREATE TABLE IF NOT EXISTS materials(id INTEGER PRIMARY KEY,filename TEXT,openai_file_id TEXT,created_at TEXT)"); c.commit(); return c
@app.get("/",response_class=HTMLResponse)
def home(): return FileResponse(INDEX)
@app.get("/health")
def health(): return {"status":"ok","app":"Professor MD","version":"3.0"}
@app.get("/api/status")
def status(): return {"online":True,"openai_configured":bool(os.getenv("OPENAI_API_KEY")),"file_search_configured":bool(VS),"model":MODEL}
def ai(prompt):
 key=os.getenv("OPENAI_API_KEY")
 if not key: raise RuntimeError("OPENAI_API_KEY não configurada no Render.")
 tools=[{"type":"file_search","vector_store_ids":[VS]}] if VS else None
 r=OpenAI(api_key=key).responses.create(model=MODEL,instructions="""Você é o Professor MD, professor particular para concursos, focado em TRT-MG/FUMARC e Contabilidade. Se houver PDFs via file_search, use-os como fonte principal. Não invente conteúdo do PDF. Ensine: conceito, exemplo, pegadinha FUMARC, mini questão e feedback. Não diga que é afiliado à Estratégia Concursos.""",input=prompt,tools=tools)
 return r.output_text
@app.post("/api/ask")
def ask(text:str=Form(...)):
 try:return {"ok":True,"answer":ai(text)}
 except Exception as e:return JSONResponse({"ok":False,"error":str(e)},500)
@app.post("/api/upload")
async def upload(file:UploadFile=File(...)):
 if not file.filename.lower().endswith(".pdf"): return JSONResponse({"ok":False,"error":"Envie um PDF."},400)
 data=await file.read(); name=Path(file.filename).name; (UP/name).write_bytes(data); oid=None; msg="PDF salvo no servidor."
 if os.getenv("OPENAI_API_KEY"):
  try:
   c=OpenAI(); f=c.files.create(file=(name,data),purpose="assistants"); oid=f.id
   if VS: c.vector_stores.files.create(vector_store_id=VS,file_id=oid); msg="PDF enviado e adicionado à base de conhecimento."
   else: msg="PDF enviado, mas falta OPENAI_VECTOR_STORE_ID para o Professor usar o PDF nas respostas."
  except Exception as e: msg="PDF salvo, mas integração OpenAI falhou: "+str(e)
 c=db(); c.execute("INSERT INTO materials(filename,openai_file_id,created_at) VALUES(?,?,?)",(name,oid,datetime.now().isoformat())); c.commit(); c.close()
 return {"ok":True,"filename":name,"message":msg}
@app.post("/api/session")
def session(discipline:str=Form(""),topic:str=Form(""),minutes:int=Form(60),score:float=Form(0),notes:str=Form("")):
 c=db(); c.execute("INSERT INTO sessions(discipline,topic,minutes,score,notes,created_at) VALUES(?,?,?,?,?,?)",(discipline,topic,minutes,score,notes,datetime.now().isoformat())); c.commit(); c.close(); return {"ok":True}
@app.get("/api/dashboard")
def dashboard():
 c=db(); m=c.execute("SELECT filename FROM materials ORDER BY id DESC LIMIT 10").fetchall(); s=c.execute("SELECT discipline,topic,minutes,score FROM sessions ORDER BY id DESC LIMIT 20").fetchall(); c.close()
 scores=[float(x["score"]) for x in s if x["score"] and x["score"]>0]; avg=round(sum(scores)/len(scores),1) if scores else 0
 return {"materials":[dict(x) for x in m],"average_score":avg,"study_minutes":sum(int(x["minutes"]) for x in s)}
@app.get("/api/today")
def today():
 return {"date":date.today().strftime("%d/%m/%Y"),"blocks":[
 {"discipline":"Contabilidade Geral","topic":"Ativo Imobilizado","theory":"40 min","reverse":"20 min","action":"Estudar o PDF e marcar reconhecimento, mensuração e depreciação."},
 {"discipline":"Contabilidade Pública","topic":"CASP / MCASP","theory":"40 min","reverse":"20 min","action":"Estudar o tópico correspondente e fazer Engenharia Reversa FUMARC."}]}
