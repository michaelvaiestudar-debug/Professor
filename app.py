# Professor MD 4.6
from pathlib import Path
import os, sqlite3, json, threading, time
from datetime import datetime, date, timedelta
import re
import textwrap
import hashlib
import urllib.request
import urllib.parse
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
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

app = FastAPI(title="Professor MD", version="4.6")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"]
)


def supabase_configured():
    return bool(os.getenv("SUPABASE_URL", "").strip() and os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip())

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "professor-md-pdfs").strip() or "professor-md-pdfs"

def _sb_request(method, path, data=None, content_type=None):
    if not supabase_configured():
        raise RuntimeError("Biblioteca permanente não configurada. Defina SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY no Render.")
    url = SUPABASE_URL + path
    headers = {"Authorization": "Bearer " + SUPABASE_SERVICE_ROLE_KEY, "apikey": SUPABASE_SERVICE_ROLE_KEY}
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return resp.read(), resp.headers.get("content-type", "")
    except urllib.error.HTTPError as e:
        # Supabase returns the useful reason in the response body. Expose it
        # instead of only showing the generic HTTP status to the user.
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        detail = body[:1000].strip()
        if detail:
            raise RuntimeError(f"Supabase HTTP {e.code}: {detail}") from e
        raise RuntimeError(f"Supabase HTTP {e.code}: {e.reason}") from e

def supabase_upload(path, data, content_type="application/pdf", upsert=False):
    encoded = "/".join(urllib.parse.quote(x, safe="") for x in path.split("/"))
    endpoint = f"/storage/v1/object/{urllib.parse.quote(SUPABASE_BUCKET, safe='')}/{encoded}"
    if upsert:
        endpoint += "?upsert=true"
    _sb_request("POST", endpoint, data=data, content_type=content_type)

def supabase_download(path):
    encoded = "/".join(urllib.parse.quote(x, safe="") for x in path.split("/"))
    endpoint = f"/storage/v1/object/{urllib.parse.quote(SUPABASE_BUCKET, safe='')}/{encoded}"
    data, _ = _sb_request("GET", endpoint)
    return data

def supabase_list(prefix="pdfs/"):
    body=json.dumps({"prefix":prefix,"limit":1000,"offset":0,"sortBy":{"column":"name","order":"desc"}}).encode()
    data,_=_sb_request("POST", f"/storage/v1/object/list/{urllib.parse.quote(SUPABASE_BUCKET, safe='')}", body, "application/json")
    return json.loads(data.decode("utf-8"))

def storage_safe_filename(filename):
    # Supabase Storage is stricter with object keys. Keep the original
    # filename for display, but use an ASCII-safe key for Storage.
    import unicodedata
    base = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode("ascii")
    base = re.sub(r"[^A-Za-z0-9._,\-!*$@=;:+?()' ]+", "_", base)
    base = re.sub(r"\s+", "_", base).strip("._")
    if not base.lower().endswith(".pdf"):
        base += ".pdf"
    return base[:180]

def storage_path_for(filename, data):
    digest=hashlib.sha256(data).hexdigest()[:20]
    return f"pdfs/{digest}_{storage_safe_filename(filename)}"

def metadata_path_for(storage_path):
    name=storage_path.split("/",1)[1]
    return f"meta/{name}.json"

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
    if "storage_path" not in cols:
        c.execute("ALTER TABLE materials ADD COLUMN storage_path TEXT DEFAULT ''")
    if "storage_backend" not in cols:
        c.execute("ALTER TABLE materials ADD COLUMN storage_backend TEXT DEFAULT 'local'")
    if "sha256" not in cols:
        c.execute("ALTER TABLE materials ADD COLUMN sha256 TEXT DEFAULT ''")
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
    c.execute("""CREATE TABLE IF NOT EXISTS study_topics(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_no INTEGER UNIQUE, discipline TEXT, topic TEXT,
        priority TEXT DEFAULT 'normal', estimated_minutes INTEGER DEFAULT 60,
        status TEXT DEFAULT 'pending', completed_at TEXT, material_id INTEGER,
        notes TEXT DEFAULT '')""")
    c.execute("""CREATE TABLE IF NOT EXISTS reviews(
        id INTEGER PRIMARY KEY AUTOINCREMENT, topic_id INTEGER, discipline TEXT, topic TEXT,
        review_date TEXT, review_type TEXT, completed INTEGER DEFAULT 0, completed_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS quizzes(
        id INTEGER PRIMARY KEY AUTOINCREMENT, material_id INTEGER, discipline TEXT, topic TEXT,
        questions_json TEXT, created_at TEXT, completed INTEGER DEFAULT 0)""")
    seed_plan(c)
    c.commit()
    return c


PLAN = [
("Contabilidade Geral","Estrutura Conceitual: conceito, objetivos, usuários e necessidades de informação","A"),
("Contabilidade Geral","Patrimônio: ativo, passivo e patrimônio líquido; aspectos qualitativo e quantitativo","A"),
("Contabilidade Geral","Equação básica da contabilidade e representação gráfica do patrimônio","A"),
("Contabilidade Geral","Variações patrimoniais e apuração do resultado","A"),
("Contabilidade Geral","Plano de contas: classificação e natureza das contas","A"),
("Contabilidade Geral","Partidas dobradas, débito, crédito, origens e aplicações","A"),
("Contabilidade Geral","Lançamento contábil: elementos essenciais e registros","A"),
("Contabilidade Geral","Regime de competência e livros de escrituração","A"),
("Contabilidade Geral","Balancete de verificação e encerramento das contas de resultado","A"),
("Contabilidade Geral","Avaliação de ativos e passivos","A"),
("Contabilidade Geral","Balanço Patrimonial: estrutura, classificação e critérios","A"),
("Contabilidade Geral","DRE: estrutura, formação e apuração do resultado","A"),
("Contabilidade Geral","Demonstração do Resultado Abrangente e DLPA","B"),
("Contabilidade Geral","DMPL: estrutura e mutações do patrimônio líquido","B"),
("Contabilidade Geral","DFC: métodos e fluxos de caixa","A"),
("Contabilidade Geral","DVA e distribuição da riqueza","A"),
("Contabilidade Geral","Notas explicativas e divulgação contábil","A"),
("Contabilidade Geral","Lei 6.404/76 e alterações da Lei 11.638/07","A"),
("Contabilidade Geral","CPC 00 — Estrutura Conceitual","A"),
("Contabilidade Geral","CPC 26 — Apresentação das demonstrações contábeis","A"),
("Contabilidade Geral","CPC 03 — Demonstração dos Fluxos de Caixa","A"),
("Contabilidade Geral","CPC 09 — Demonstração do Valor Adicionado","A"),
("Contabilidade Geral","CPC 27 — Ativo Imobilizado: reconhecimento e mensuração","A"),
("Contabilidade Geral","CPC 27 — Depreciação, vida útil e valor residual","A"),
("Contabilidade Geral","CPC 01 — Redução ao Valor Recuperável de Ativos","A"),
("Contabilidade Geral","CPC 04 — Ativo Intangível","B"),
("Contabilidade Geral","CPC 16 — Estoques","B"),
("Contabilidade Geral","CPC 25 — Provisões, passivos e ativos contingentes","B"),
("Contabilidade Geral","CPC 47 — Receita de contrato com cliente","B"),
("Contabilidade Geral","CPC 48 — Instrumentos financeiros: classificação e mensuração","B"),
("Contabilidade Geral","CPC 06 — Arrendamentos","B"),
("Contabilidade Geral","CPC 32 — Tributos sobre o lucro","B"),
("Contabilidade Pública","CASP: conceito, campo de aplicação e regime orçamentário e patrimonial","A"),
("Contabilidade Pública","Estrutura Conceitual da NBC TSP: objetivos, elementos e características","A"),
("Contabilidade Pública","MCASP: estrutura e organização da 9ª edição prevista no edital","A"),
("Contabilidade Pública","PCASP: classes, natureza da informação e lógica dos registros","A"),
("Contabilidade Pública","PCASP: atributos, contas e lançamentos típicos","A"),
("Contabilidade Pública","Receita pública: conceito e classificações","A"),
("Contabilidade Pública","Receita pública: estágios, fontes e dívida ativa","A"),
("Contabilidade Pública","Despesa pública: conceito e classificações","A"),
("Contabilidade Pública","Despesa pública: empenho, liquidação e pagamento","A"),
("Contabilidade Pública","Restos a pagar e despesas de exercícios anteriores","A"),
("Contabilidade Pública","Procedimentos Contábeis Patrimoniais","A"),
("Contabilidade Pública","Procedimentos Contábeis Específicos","B"),
("Contabilidade Pública","Estoques no setor público","B"),
("Contabilidade Pública","Imobilizado e depreciação no setor público","B"),
("Contabilidade Pública","Intangível, provisões e contingências no setor público","B"),
("Contabilidade Pública","Balanço Orçamentário","A"),
("Contabilidade Pública","Balanço Financeiro","A"),
("Contabilidade Pública","Balanço Patrimonial","A"),
("Contabilidade Pública","Demonstração das Variações Patrimoniais","A"),
("Contabilidade Pública","DFC e DMPL no setor público","B"),
("Contabilidade Pública","Notas explicativas e DCASP","A"),
("Contabilidade Pública","LRF: princípios, limites e transparência","A"),
("Contabilidade Pública","LRF: despesa com pessoal e endividamento","A"),
("Contabilidade Pública","RGF e RREO","A"),
("Contabilidade Pública","Lei 4.320/64: pontos contábeis e orçamentários","A"),
("Contabilidade Pública","Direito tributário básico, competência tributária e retenções","B"),
("Contabilidade Pública","NBC TSP 01 a 34: visão geral e pontos de maior incidência","B"),
("Contabilidade Pública","IPSAS e convergência das normas contábeis públicas","B"),
("Contabilidade Pública","Auditoria no setor público: NBC TASP e asseguração","C"),
("Contabilidade Pública","Custos no setor público: NBC T 16.11 e informações de custos","B"),
("Contabilidade Pública","Prestação de contas e normas do TCU: IN 84/2020 e DN 198/2022","C"),
("AFO / Orçamento Público","Conceito, técnicas e princípios orçamentários","A"),
("AFO / Orçamento Público","Ciclo orçamentário e sistema de planejamento e orçamento","A"),
("AFO / Orçamento Público","PPA, LDO e LOA","A"),
("AFO / Orçamento Público","Sistema e processo de orçamentação","B"),
("AFO / Orçamento Público","Classificações orçamentárias e estrutura programática","A"),
("AFO / Orçamento Público","Alterações orçamentárias e créditos adicionais","A"),
("AFO / Orçamento Público","Programação e execução orçamentária e financeira","A"),
("AFO / Orçamento Público","Descentralização orçamentária e financeira","B"),
("AFO / Orçamento Público","Receita pública no orçamento: classificações, estágios e fontes","A"),
("AFO / Orçamento Público","Despesa pública no orçamento: classificações e estágios","A"),
("AFO / Orçamento Público","Restos a pagar, despesas de exercícios anteriores e dívida","A"),
("AFO / Orçamento Público","Dívida flutuante e dívida fundada","B"),
("AFO / Orçamento Público","LRF: limites das despesas e despesa com pessoal","A"),
("AFO / Orçamento Público","LRF: endividamento, RGF, RREO e transparência","A"),
("AFO / Orçamento Público","CF/88, Decreto 93.872/86, MTO 2022 e MDF 12ª edição","B"),
("Português","Compreensão e interpretação: informações literais e inferências","A"),
("Português","Articulação textual: referenciação, nexos, operadores, coesão e coerência","A"),
("Português","Significação contextual de palavras e expressões","B"),
("Português","Crase","A"),
("Português","Tempos e modos verbais","B"),
("Português","Emprego e colocação de pronomes","B"),
("Português","Regência nominal e verbal","A"),
("Português","Concordância verbal e nominal","A"),
("Português","Pontuação","A"),
("Português","Variação linguística e norma linguística","B"),
("Direito Constitucional","Conceito, aplicabilidade e interpretação das normas constitucionais","B"),
("Direito Constitucional","Princípios fundamentais","B"),
("Direito Constitucional","Direitos e garantias fundamentais","A"),
("Direito Constitucional","Organização político-administrativa do Estado","B"),
("Direito Constitucional","Administração Pública e servidores públicos","A"),
("Direito Constitucional","Organização dos Poderes","B"),
("Direito Constitucional","Poder Legislativo e processo legislativo","B"),
("Direito Constitucional","Poder Executivo: atribuições e responsabilidades","C"),
("Direito Constitucional","Poder Judiciário, STF, CNJ e STJ","A"),
("Direito Constitucional","Tribunais e Juízes do Trabalho e CSJT","A"),
("Direito Administrativo","Princípios básicos da Administração Pública","A"),
("Direito Administrativo","Organização administrativa: direta, indireta, centralização e descentralização","A"),
("Direito Administrativo","Autarquias, fundações, empresas públicas e sociedades de economia mista","B"),
("Direito Administrativo","Poderes administrativos","A"),
("Direito Administrativo","Servidores: cargo, emprego e função públicos","B"),
("Direito Administrativo","Atos administrativos: conceito, requisitos e atributos","A"),
("Direito Administrativo","Anulação, revogação, convalidação, discricionariedade e vinculação","A"),
("Direito Administrativo","Lei 8.112/90: provimento, vacância, remoção, redistribuição e substituição","A"),
("Direito Administrativo","Lei 8.112/90: direitos, vantagens, férias, licenças e afastamentos","A"),
("Direito Administrativo","Lei 8.112/90: regime disciplinar e penalidades","A"),
("Direito Administrativo","Processo administrativo disciplinar","B"),
("Direito Administrativo","Lei 14.133/2021 — Licitações e Contratos","A"),
("Direito Administrativo","Responsabilidade extracontratual do Estado","B"),
("Direito Administrativo","Lei 9.784/1999 — Processo administrativo","A"),
("Direito Administrativo","Lei 8.429/1992 — Improbidade Administrativa","A"),
("Legislação","LGPD — Lei 13.709/2018","A"),
("Legislação","Lei Brasileira de Inclusão — Lei 13.146/2015","B"),
("Legislação","Regimento Interno do TRT da 3ª Região","A"),
("Legislação","Código de Ética do TRT3","B"),
("Direito do Trabalho","Princípios, fontes e direitos constitucionais dos trabalhadores","B"),
("Direito do Trabalho","Relação de trabalho e relação de emprego; sujeitos do contrato","B"),
("Direito do Trabalho","Contrato individual, alteração, suspensão e interrupção","B"),
("Direito do Trabalho","Rescisão, aviso prévio, estabilidade e garantias provisórias","A"),
("Direito do Trabalho","Jornada, descansos, trabalho noturno e horas extras","A"),
("Direito do Trabalho","Férias, salário, remuneração e 13º salário","A"),
("Direito do Trabalho","Equiparação salarial, FGTS, prescrição e decadência","B"),
("Direito do Trabalho","Insalubridade, periculosidade e segurança do trabalho","B"),
("Direito do Trabalho","Negociação coletiva, greve e teletrabalho","B"),
("Direito do Trabalho","Dano moral, acidentes e responsabilidade civil trabalhista","B"),
("Direito Processual do Trabalho","Justiça do Trabalho: organização e competência","A"),
("Direito Processual do Trabalho","Princípios, atos, termos e prazos processuais","B"),
("Direito Processual do Trabalho","Partes, procuradores, assistência judiciária e honorários","B"),
("Direito Processual do Trabalho","Audiências, revelia, confissão e provas","A"),
("Direito Processual do Trabalho","Dissídios individuais e procedimentos ordinário e sumaríssimo","A"),
("Direito Processual do Trabalho","Sentença, coisa julgada e jurisdição voluntária","B"),
("Direito Processual do Trabalho","Liquidação e execução trabalhista","A"),
("Direito Processual do Trabalho","Penhora, embargos, impugnações e embargos de terceiros","B"),
("Direito Processual do Trabalho","Recursos no processo do trabalho","A"),
("Direito Processual do Trabalho","PJe, Reforma Trabalhista e jurisprudência do TST","B"),
("Contabilidade de Custos","Conceitos: custos, despesas, investimentos, ganhos, perdas e gastos","B"),
("Contabilidade de Custos","Classificação: fixos, variáveis, diretos, indiretos, controláveis e não controláveis","B"),
("Contabilidade de Custos","Custos primários, custos de transformação e objeto de custeio","B"),
("Contabilidade de Custos","Custeio por absorção","A"),
("Contabilidade de Custos","Custeio variável","A"),
("Contabilidade de Custos","Custeio ABC e Custeio Pleno (RKW)","B"),
("Contabilidade de Custos","Custo por produto, processo e atividade","B"),
("Matemática Financeira","Juros simples","B"),
("Matemática Financeira","Juros compostos","B"),
("Matemática Financeira","Taxas nominal, efetiva, real, equivalente e aparente","B"),
("Matemática Financeira","Desconto: valor presente, valor futuro e montante","B"),
("Informática","LibreOffice e Windows 10: arquivos, pastas, configurações e permissões","C"),
("Informática","Word 2016: edição, formatação, tabelas, impressão e recursos","C"),
("Informática","Excel 2016: fórmulas, funções, gráficos, classificação e dados externos","B"),
("Informática","PowerPoint 2016 e Outlook 2016","C"),
("Informática","Chrome e navegação na Internet","C"),
("Informática","Segurança: vírus, malware, phishing, ransomware, spam e ameaças","B"),
]

def ordered_plan():
    queues={}
    for item in PLAN:
        queues.setdefault(item[0],[]).append(item)
    rotation=[
        "Contabilidade Geral","Contabilidade Pública",
        "Português","Direito Constitucional",
        "AFO / Orçamento Público","Contabilidade Geral",
        "Contabilidade Pública","Direito Administrativo",
        "Português","Direito do Trabalho",
        "Contabilidade Geral","Direito Processual do Trabalho",
        "Contabilidade Geral","AFO / Orçamento Público",
        "Contabilidade Pública","Direito Administrativo",
        "Português","Legislação",
        "Contabilidade Geral","Contabilidade de Custos",
        "Contabilidade Pública","Matemática Financeira",
        "Direito Constitucional","Informática",
    ]
    out=[]
    while any(queues.values()):
        progressed=False
        for disc in rotation:
            if queues.get(disc):
                out.append(queues[disc].pop(0)); progressed=True
            if not any(queues.values()): break
        if not progressed: break
    # Any remaining subjects not represented in the cycle are appended, still respecting edital order.
    for disc,q in queues.items(): out.extend(q)
    return out

def seed_plan(c):
    count = c.execute("SELECT COUNT(*) FROM study_topics").fetchone()[0]
    if count:
        return
    for i,(disc,topic,priority) in enumerate(ordered_plan(),1):
        c.execute("INSERT INTO study_topics(order_no,discipline,topic,priority,estimated_minutes,status) VALUES(?,?,?,?,?,?)",
                  (i,disc,topic,priority,60,"pending"))

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
    return {"status": "ok", "app": "Professor MD", "version": "4.5"}


@app.get("/api/status")
def status():
    return {
        "online": True,
        "openai_configured": bool(os.getenv("OPENAI_API_KEY")),
        "file_search_configured": bool(current_vs()),
        "model": MODEL,
        "upload_mode": "background",
        "library_persistent": supabase_configured(),
        "library_mode": "Supabase Storage" if supabase_configured() else "filesystem local"
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
        # Keep the persistent library metadata synchronized so a Render restart
        # can rebuild the visible library and continue using the same OpenAI file.
        c=conn(); row=c.execute("SELECT * FROM materials WHERE id=?",(material_id,)).fetchone(); c.close()
        if row and row["storage_backend"]=="supabase" and row["storage_path"]:
            meta={"filename":row["filename"],"subject":row["subject"] or "","topic":row["topic"] or "",
                  "created_at":row["created_at"] or "","size_bytes":row["size_bytes"] or 0,"sha256":row["sha256"] or "",
                  "storage_path":row["storage_path"],"openai_file_id":oid,"vector_store_id":vs,"processing_status":"ready"}
            try: supabase_upload(metadata_path_for(row["storage_path"]), json.dumps(meta,ensure_ascii=False).encode("utf-8"), "application/json", upsert=True)
            except Exception: pass
    except Exception as e:
        update_material(material_id, processing_status="error", processing_error=str(e))
        try:
            c=conn(); row=c.execute("SELECT * FROM materials WHERE id=?",(material_id,)).fetchone(); c.close()
            if row and row["storage_backend"]=="supabase" and row["storage_path"]:
                meta={"filename":row["filename"],"subject":row["subject"] or "","topic":row["topic"] or "","created_at":row["created_at"] or "",
                      "size_bytes":row["size_bytes"] or 0,"sha256":row["sha256"] or "","storage_path":row["storage_path"],"processing_status":"error","processing_error":str(e)}
                supabase_upload(metadata_path_for(row["storage_path"]), json.dumps(meta,ensure_ascii=False).encode("utf-8"), "application/json", upsert=True)
        except Exception: pass


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
    digest = hashlib.sha256(data).hexdigest()
    storage_backend = "supabase" if supabase_configured() else "local"
    storage_path = ""

    try:
        if supabase_configured():
            storage_path = storage_path_for(name, data)
            supabase_upload(storage_path, data, "application/pdf", upsert=True)
            meta = {"filename": name, "subject": subject, "topic": topic, "created_at": datetime.now().isoformat(),
                    "size_bytes": len(data), "sha256": digest, "storage_path": storage_path}
            supabase_upload(metadata_path_for(storage_path), json.dumps(meta, ensure_ascii=False).encode("utf-8"), "application/json", upsert=True)
        else:
            safe_name = name
            target = UPLOADS / safe_name
            if target.exists():
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                safe_name = f"{target.stem}_{stamp}{target.suffix}"
                target = UPLOADS / safe_name
            target.write_bytes(data)
            name = safe_name
    except Exception as e:
        return JSONResponse({"ok": False, "error": "Não foi possível salvar o PDF na biblioteca: " + str(e)}, status_code=500)

    c = conn()
    cur = c.execute("""INSERT INTO materials(
        filename, openai_file_id, vector_store_id, subject, topic, created_at,
        processing_status, processing_error, size_bytes, storage_path, storage_backend, sha256)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (name, None, current_vs(), subject, topic, datetime.now().isoformat(), "queued", "", len(data), storage_path, storage_backend, digest))
    material_id = cur.lastrowid
    c.commit(); c.close()

    worker = threading.Thread(target=process_pdf_background, args=(material_id, name, data), daemon=True, name=f"professor-md-pdf-{material_id}")
    worker.start()

    return {
        "ok": True, "id": material_id, "filename": name, "status": "queued",
        "message": "PDF salvo na biblioteca. O processamento continuará em segundo plano." if supabase_configured() else "PDF recebido. O processamento continuará em segundo plano.",
        "library_persistent": supabase_configured(), "file_search_configured": bool(current_vs())
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
    if row["processing_status"] not in ("ready", "archived"):
        raise RuntimeError("Este PDF ainda não está pronto. Aguarde o processamento terminar.")
    if not row["openai_file_id"]:
        raise RuntimeError("Este PDF está salvo na biblioteca, mas ainda não possui arquivo processado pela IA.")
    filename = row["filename"]
    subject = row["subject"] or ""
    topic = row["topic"] or ""
    if kind == "flashcards":
        schema = {
          "title": "título curto",
          "cards": [{"question": "pergunta", "answer": "resposta objetiva e fiel ao PDF", "trap": "ponto de atenção somente se comprovado pelo PDF", "level": "basico|intermediario|fumarc"}]
        }
        instruction = f"""Você está criando material de revisão EXCLUSIVAMENTE a partir do arquivo PDF anexado, chamado \"{filename}\".
Disciplina: {subject}. Assunto informado: {topic}.
NÃO use conhecimento externo, outros arquivos ou memória geral para completar lacunas.
Crie exatamente 15 flashcards.
REGRA DE PRECISÃO: cada pergunta e cada resposta precisam estar claramente sustentadas pelo PDF. Não acrescente exceções, classificações, datas, leis, regras ou afirmações que não estejam no arquivo. Se houver dúvida sobre um detalhe, prefira não usá-lo.
Não transforme uma inferência sua em fato do PDF.
Use aproximadamente 5 básicos, 5 intermediários e 5 no nível \"fumarc\". O rótulo FUMARC significa apenas dificuldade/estilo, não questão oficial.
O campo trap deve ficar vazio quando não houver uma pegadinha explicitamente sustentada pelo PDF.
Faça perguntas objetivas, sem ambiguidades e com uma única resposta defensável.
Retorne SOMENTE JSON válido neste formato: {json.dumps(schema, ensure_ascii=False)}"""
    else:
        schema = {
          "title": "título",
          "central": "tema central",
          "branches": [{"title": "ramo", "items": ["item 1", "item 2", "item 3", "item 4"]}],
          "traps": ["pegadinha 1", "pegadinha 2"]
        }
        instruction = f"""Monte um mapa mental visual EXCLUSIVAMENTE a partir do arquivo PDF anexado, chamado \"{filename}\".
Disciplina: {subject}. Assunto informado: {topic}.
NÃO use conteúdo externo, outros arquivos ou memória geral.
Organize todo o conteúdo relevante do PDF em 5 a 8 ramos principais. Cada ramo pode ter de 3 a 7 itens curtos, mas não corte conceitos importantes para caber em uma linha.
Preserve a terminologia do material. Não invente conteúdo.
As pegadinhas devem aparecer somente se forem sustentadas pelo PDF.
Priorize cobertura completa e clareza. Não reduza o assunto a meia dúzia de frases.
Retorne SOMENTE JSON válido neste formato: {json.dumps(schema, ensure_ascii=False)}"""
    return row, instruction


def _response_with_file(client, row, instruction, json_mode=True):
    content = [
        {"type": "input_file", "file_id": row["openai_file_id"]},
        {"type": "input_text", "text": instruction},
    ]
    kwargs = {
        "model": MODEL,
        "instructions": "Você é o Professor MD. Trabalhe somente com o PDF anexado. Responda em português do Brasil.",
        "input": [{"role": "user", "content": content}],
    }
    if json_mode:
        kwargs["text"] = {"format": {"type": "json_object"}}
    return client.responses.create(**kwargs)


def _ai_json(material_id, kind):
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("Configure OPENAI_API_KEY no Render antes de gerar o material.")
    row, instruction = _source_prompt(material_id, kind)
    client = OpenAI(api_key=key, timeout=120.0, max_retries=2)
    r = _response_with_file(client, row, instruction, True)
    raw = _clean_json_text(r.output_text)
    try:
        data = json.loads(raw)
    except Exception as e:
        raise RuntimeError("A IA não retornou o formato estruturado esperado. Tente gerar novamente.") from e
    if kind == "flashcards":
        data = _audit_flashcards(client, row, data)
    return row, data


def _audit_flashcards(client, row, data):
    cards = data.get("cards") or []
    if not cards:
        return data
    schema = {"cards": [{"question":"", "answer":"", "trap":"", "level":"basico|intermediario|fumarc"}]}
    audit_prompt = f"""Audite os flashcards abaixo usando EXCLUSIVAMENTE o PDF anexado \"{row["filename"]}\".
Para cada card, verifique se pergunta, resposta e ponto de atenção são literalmente sustentáveis pelo conteúdo do PDF.
CORRIJA qualquer afirmação mais ampla que o PDF permita, remova qualquer informação externa e elimine cards ambíguos ou sem resposta única.
Mantenha somente cards corretos e claros. Se um ponto de atenção não estiver comprovado, deixe trap vazio.
Não crie fatos novos.
Cards para auditar:
{json.dumps(cards, ensure_ascii=False)}
Retorne SOMENTE JSON neste formato: {json.dumps(schema, ensure_ascii=False)}"""
    r = _response_with_file(client, row, audit_prompt, True)
    try:
        audited = json.loads(_clean_json_text(r.output_text))
        if audited.get("cards"):
            clean=[]
            for card in audited["cards"][:12]:
                if str(card.get("question","")).strip() and str(card.get("answer","")).strip() and str(card.get("evidence","")).strip():
                    clean.append(card)
            if clean:
                data["cards"] = clean
    except Exception:
        pass
    return data


def _generate_questions_json(material_id, count=5, focus=""):
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("Configure OPENAI_API_KEY no Render antes de gerar questões.")
    c = conn(); row = c.execute("SELECT * FROM materials WHERE id=?", (material_id,)).fetchone(); c.close()
    if not row or not row["openai_file_id"] or row["processing_status"] not in ("ready", "archived"):
        raise RuntimeError("Escolha um PDF processado e pronto para perguntas.")
    count = max(3, min(int(count or 5), 10))
    schema = {"questions": [{"id":1,"question":"","options":["A","B","C","D"],"correct_index":0,"explanation":"explicação fiel ao PDF","topic":""}]}
    prompt = f"""Crie {count} questões originais de múltipla escolha, inspiradas no estilo de cobrança da FUMARC, EXCLUSIVAMENTE com base no PDF anexado \"{row["filename"]}\".
Disciplina: {row["subject"] or ""}. Assunto: {row["topic"] or ""}. Foco adicional: {focus}.
Cada questão deve ter exatamente 4 alternativas e apenas 1 alternativa correta.
A alternativa correta e a explicação devem estar sustentadas pelo PDF. Não invente leis, números, exceções, conceitos ou exemplos.
Use pegadinhas apenas quando houver distinções realmente presentes no PDF.
Não são questões oficiais da FUMARC.
Retorne SOMENTE JSON neste formato: {json.dumps(schema, ensure_ascii=False)}"""
    client=OpenAI(api_key=key, timeout=120.0, max_retries=2)
    r=_response_with_file(client,row,prompt,True)
    try: data=json.loads(_clean_json_text(r.output_text))
    except Exception as e: raise RuntimeError("A IA não retornou questões em formato válido.") from e
    qs=data.get("questions") or []
    valid=[]
    for i,q in enumerate(qs[:count],1):
        opts=q.get("options") or []; ci=q.get("correct_index")
        if len(opts)==4 and isinstance(ci,int) and 0<=ci<4 and q.get("question"):
            q["id"]=i; valid.append(q)
    if len(valid)<3: raise RuntimeError("A IA gerou poucas questões válidas. Tente novamente.")
    data["questions"]=valid
    return row,data


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


def _font_path(name="DejaVuSans.ttf"):
    candidates=["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf","/usr/share/fonts/dejavu/DejaVuSans.ttf"]
    for p in candidates:
        if Path(p).exists(): return p
    return None


def _register_pdf_fonts():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    fp=_font_path(); bold=fp.replace("DejaVuSans.ttf","DejaVuSans-Bold.ttf") if fp else None
    if fp:
        try: pdfmetrics.registerFont(TTFont("DVS",fp))
        except Exception: pass
        if bold and Path(bold).exists():
            try: pdfmetrics.registerFont(TTFont("DVSB",bold))
            except Exception: pass
    return ("DVS" if fp else "Helvetica", "DVSB" if bold and Path(bold).exists() else "Helvetica-Bold")


def _build_mindmap_pdf(title, central, branches, traps, out_path):
    """Build a readable, non-overlapping mind-map PDF.

    The first page is a true overview (center + branch titles only). Every branch
    then receives one or more detail pages. Text is wrapped and paginated rather
    than truncated, so long concepts are never silently cut off.
    """
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.pdfbase.pdfmetrics import stringWidth

    W,H=landscape(A4)
    c=canvas.Canvas(str(out_path), pagesize=(W,H))
    c.setTitle("Mapa Mental - "+str(title))
    regular,bold=_register_pdf_fonts()

    def wrap(text,font,size,maxw):
        words=str(text or "").split()
        lines=[]; cur=""
        for word in words:
            # Break a single very long token instead of clipping it.
            if stringWidth(word,font,size) > maxw:
                if cur:
                    lines.append(cur); cur=""
                piece=""
                for ch in word:
                    test=piece+ch
                    if stringWidth(test,font,size) <= maxw:
                        piece=test
                    else:
                        if piece: lines.append(piece)
                        piece=ch
                if piece: lines.append(piece)
                continue
            test=(cur+" "+word).strip()
            if stringWidth(test,font,size) <= maxw:
                cur=test
            else:
                if cur: lines.append(cur)
                cur=word
        if cur: lines.append(cur)
        return lines or [""]

    # ---------- Overview page ----------
    c.setFont(bold,20)
    c.drawCentredString(W/2,H-15*mm,"MAPA MENTAL — "+str(title)[:100])
    c.setFont(regular,8)
    c.drawCentredString(W/2,H-21*mm,"Professor MD • visão geral • conteúdo organizado a partir do PDF selecionado")

    cx,cy=W/2,H/2-2*mm
    cw,ch=68*mm,34*mm
    c.setLineWidth(1.5)
    c.roundRect(cx-cw/2,cy-ch/2,cw,ch,6*mm,stroke=1,fill=0)
    center_lines=wrap(central,bold,13,cw-10*mm)
    yy=cy+(len(center_lines)-1)*2.5*mm
    for line in center_lines:
        c.setFont(bold,13); c.drawCentredString(cx,yy,line); yy-=5*mm
    c.setFont(regular,8); c.drawCentredString(cx,cy-12*mm,"TEMA CENTRAL")

    # 8 clean branch positions; only titles are placed here to avoid overlap.
    positions=[(W*.17,H*.72),(W*.17,H*.48),(W*.17,H*.24),(W*.39,H*.78),
               (W*.61,H*.78),(W*.83,H*.72),(W*.83,H*.48),(W*.83,H*.24)]
    for i,b in enumerate(list(branches or [])[:8]):
        x,y=positions[i]
        title_lines=wrap(b.get("title","Ramo"),bold,9.5,42*mm)[:3]
        bh=max(18*mm, (len(title_lines)*5+8)*mm)
        bw=48*mm
        # Connector terminates at the edge of the title box, not through text.
        edge_x = x+bw/2 if x<cx else x-bw/2
        c.line(cx,cy,edge_x,y)
        c.roundRect(x-bw/2,y-bh/2,bw,bh,4*mm,stroke=1,fill=0)
        ty=y+(len(title_lines)-1)*2.5*mm
        for line in title_lines:
            c.setFont(bold,9.5); c.drawCentredString(x,ty,line); ty-=5*mm
    c.showPage()

    # ---------- Detail pages ----------
    for idx,b in enumerate(list(branches or []),1):
        items=list(b.get("items") or [])
        # A branch can span multiple pages. Never slice its text.
        page_no=1
        item_index=0
        while item_index < len(items) or page_no==1:
            c.setFont(bold,18)
            heading=f"MAPA MENTAL — {idx}. {str(b.get('title','Ramo'))}"
            c.drawString(15*mm,H-16*mm,heading[:120])
            c.setFont(regular,8)
            c.drawString(15*mm,H-22*mm,f"Detalhamento • página {page_no}")

            x=18*mm; y=H-34*mm; max_y=18*mm; maxw=W-36*mm
            c.setLineWidth(0.8)
            while item_index < len(items):
                text="• "+str(items[item_index])
                lines=wrap(text,regular,10.5,maxw-6*mm)
                needed=(len(lines)*5.8*mm)+3*mm
                if y-needed < max_y and item_index>0:
                    break
                c.roundRect(x,y-needed+2*mm,maxw,needed,3*mm,stroke=1,fill=0)
                yy=y-4*mm
                for line in lines:
                    c.setFont(regular,10.5); c.drawString(x+4*mm,yy,line); yy-=5.8*mm
                y-=needed+3*mm
                item_index+=1
            if not items:
                c.setFont(regular,10.5); c.drawString(x+4*mm,y,"(Nenhum item detalhado foi retornado pelo PDF.)")
            c.showPage()
            page_no+=1
            if item_index>=len(items): break

    # ---------- Traps / exam attention ----------
    traps=list(traps or [])
    if traps:
        tindex=0; page=1
        while tindex<len(traps):
            c.setFont(bold,18); c.drawString(15*mm,H-16*mm,"PEGADINHAS DE PROVA")
            c.setFont(regular,8); c.drawString(15*mm,H-22*mm,"Somente pontos sustentados pelo PDF selecionado.")
            y=H-34*mm
            while tindex<len(traps):
                lines=wrap(str(traps[tindex]),regular,10.5,W-48*mm)
                needed=(len(lines)*5.8*mm)+8*mm
                if y-needed<18*mm and y < H-34*mm: break
                c.roundRect(18*mm,y-needed+2*mm,W-36*mm,needed,3*mm,stroke=1,fill=0)
                yy=y-4*mm
                c.setFont(bold,10.5); c.drawString(22*mm,yy,f"{tindex+1}.")
                yy-=5.8*mm
                for line in lines:
                    c.setFont(regular,10.5); c.drawString(30*mm,yy,line); yy-=5.8*mm
                y-=needed+3*mm; tindex+=1
            c.showPage(); page+=1
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


@app.post("/api/questions/generate")
def generate_questions(material_id:int=Form(...), count:int=Form(5), focus:str=Form("")):
    try:
        row,data=_generate_questions_json(material_id,count,focus)
        c=conn(); cur=c.execute("INSERT INTO quizzes(material_id,discipline,topic,questions_json,created_at,completed) VALUES(?,?,?,?,?,0)",(material_id,row["subject"] or "",row["topic"] or "",json.dumps(data["questions"],ensure_ascii=False),datetime.now().isoformat())); qid=cur.lastrowid; c.commit(); c.close()
        public=[{"id":q["id"],"question":q["question"],"options":q["options"]} for q in data["questions"]]
        return {"ok":True,"quiz_id":qid,"questions":public,"count":len(public),"material":row["filename"]}
    except Exception as e: return JSONResponse({"ok":False,"error":str(e)},status_code=500)

@app.post("/api/questions/submit")
def submit_questions(quiz_id:int=Form(...), answers:str=Form("{}")):
    try: ans=json.loads(answers or "{}")
    except Exception: return JSONResponse({"ok":False,"error":"Respostas inválidas."},status_code=400)
    c=conn(); row=c.execute("SELECT * FROM quizzes WHERE id=?",(quiz_id,)).fetchone()
    if not row: c.close(); return JSONResponse({"ok":False,"error":"Questionário não encontrado."},status_code=404)
    questions=json.loads(row["questions_json"] or "[]"); correct=0; wrong=[]
    for q in questions:
        chosen=ans.get(str(q["id"]),None)
        if isinstance(chosen,int) and chosen==q["correct_index"]: correct+=1
        else:
            chosen_text=q["options"][chosen] if isinstance(chosen,int) and 0<=chosen<4 else "Não respondida"; right_text=q["options"][q["correct_index"]]
            wrong.append({"question":q["question"],"chosen":chosen_text,"correct":right_text,"explanation":q.get("explanation","")})
            c.execute("INSERT INTO errors(discipline,topic,question,mistake,action,review_date,created_at) VALUES(?,?,?,?,?,?,?)",(row["discipline"],row["topic"],q["question"],f"Marquei: {chosen_text}. Correta: {right_text}.","Revisar a questão e reler o trecho do PDF que fundamenta o gabarito.",(date.today()+timedelta(days=1)).isoformat(),datetime.now().isoformat()))
    total=len(questions); score=round(correct/total*100,1) if total else 0
    c.execute("UPDATE quizzes SET completed=1 WHERE id=?",(quiz_id,)); c.execute("INSERT INTO sessions(discipline,topic,minutes,questions,correct,score,notes,created_at) VALUES(?,?,?,?,?,?,?,?)",(row["discipline"],row["topic"],20,total,correct,score,"Questionário Professor MD",datetime.now().isoformat())); c.commit(); c.close()
    return {"ok":True,"total":total,"correct":correct,"score":score,"wrong":wrong,"errors_added":len(wrong)}

@app.post("/api/lesson")
def lesson(topic_id:int=Form(...)):
    c=conn(); topic=c.execute("SELECT * FROM study_topics WHERE id=?",(topic_id,)).fetchone(); mats=c.execute("SELECT * FROM materials WHERE processing_status='ready' ORDER BY id DESC").fetchall(); c.close()
    if not topic: return JSONResponse({"ok":False,"error":"Assunto não encontrado."},status_code=404)
    chosen=None; rt=topic["topic"].lower()
    for m in mats:
        mt=(m["topic"] or "").lower(); ms=(m["subject"] or "").lower()
        if mt and (mt in rt or rt in mt): chosen=m; break
        if topic["discipline"].lower() in ms and chosen is None: chosen=m
    if not chosen or not chosen["openai_file_id"]: return {"ok":True,"answer":f"Não encontrei um PDF processado associado a “{topic['topic']}”. Envie o material correspondente para eu dar a aula baseada nele."}
    key=os.getenv("OPENAI_API_KEY")
    if not key: return JSONResponse({"ok":False,"error":"OPENAI_API_KEY não configurada."},status_code=500)
    prompt=f"""Dê uma aula curta e completa sobre “{topic['topic']}”, para o concurso de Contador do TRT-MG/FUMARC, usando EXCLUSIVAMENTE o PDF anexado “{chosen['filename']}”.
Estruture: 1) conceito, 2) pontos que mais merecem atenção, 3) exemplo se houver no PDF, 4) pegadinhas sustentadas pelo PDF, 5) 3 perguntas rápidas para o aluno.
Não invente conteúdo e não diga que é aula oficial da FUMARC."""
    client=OpenAI(api_key=key,timeout=120,max_retries=2); r=_response_with_file(client,chosen,prompt,False)
    return {"ok":True,"answer":r.output_text,"material":chosen["filename"],"topic":topic["topic"]}


@app.post("/api/focus/plan")
def focus_plan(material_id: int = Form(...)):
    try:
        key=os.getenv("OPENAI_API_KEY")
        if not key: raise RuntimeError("Configure OPENAI_API_KEY no Render antes de usar o Modo Foco.")
        c=conn(); row=c.execute("SELECT * FROM materials WHERE id=?",(material_id,)).fetchone(); c.close()
        if not row or not row["openai_file_id"] or row["processing_status"] not in ("ready","archived"):
            raise RuntimeError("Escolha um PDF processado e pronto para estudo.")
        schema={"title":"","microblocks":[{"title":"","objective":"","minutes":10,"key_points":["","",""],"recall":"","checkpoint":""}]}
        prompt=f"""Crie um PLANO DE MEMORIZAÇÃO EM MICRO-BLOCOS para estudar o PDF anexado "{row['filename']}".
Use EXCLUSIVAMENTE o PDF. Não acrescente conteúdo externo.
A pessoa tem dificuldade de manter a atenção em PDFs longos, então transforme o conteúdo em 6 a 8 micro-blocos curtos, sequenciais e independentes. Cada bloco deve durar 8 a 15 minutos.
Cada bloco deve ter: objetivo de uma frase; exatamente 3 pontos-chave; uma pergunta de recordação ativa sem consultar o PDF; e um checkpoint simples de conclusão.
Não tente resumir tudo em poucas frases: cubra os conceitos relevantes do PDF, mas em pequenas unidades.
Retorne SOMENTE JSON: {json.dumps(schema,ensure_ascii=False)}"""
        client=OpenAI(api_key=key,timeout=120,max_retries=2)
        r=_response_with_file(client,row,prompt,True)
        data=json.loads(_clean_json_text(r.output_text))
        blocks=data.get("microblocks") or []
        if not blocks: raise RuntimeError("Não foi possível montar os micro-blocos.")
        return {"ok":True,"material":row["filename"],"plan":data}
    except Exception as e:
        return JSONResponse({"ok":False,"error":str(e)},status_code=500)


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


@app.get("/api/plan")
def plan():
    c=conn()
    rows=c.execute("SELECT * FROM study_topics ORDER BY order_no").fetchall()
    c.close()
    items=[dict(x) for x in rows]
    first_pending=next((x["order_no"] for x in items if x["status"]!="completed"), None)
    for x in items:
        x["week"] = ((int(x["order_no"])-1)//12)+1
        x["day"] = ((int(x["order_no"])-1)//2)%6+1
        x["block"] = 1 if int(x["order_no"])%2 else 2
        if x["status"]=="pending" and first_pending is not None and x["order_no"]==first_pending:
            x["status"]="current"
    total=len(items); completed=sum(1 for x in items if x["status"]=="completed")
    pct=round(completed/total*100,1) if total else 0
    next5=[x for x in items if x["status"] in ("current","pending")][:5]
    groups=[]
    seen={}
    for x in items:
        d=x["discipline"]
        if d not in seen:
            seen[d]=[]; groups.append({"discipline":d,"items":seen[d]})
        seen[d].append(x)
    for g in groups:
        done=sum(1 for x in g["items"] if x["status"]=="completed")
        g["completed"]=done; g["total"]=len(g["items"]); g["pct"]=round(done/len(g["items"])*100,1) if g["items"] else 0
    return {"items":items,"groups":groups,"total":total,"completed":completed,"pct":pct,"next5":next5}

@app.post("/api/plan/complete")
def complete_topic(topic_id:int=Form(...), minutes:int=Form(60), questions:int=Form(0), correct:int=Form(0), notes:str=Form("")):
    c=conn(); row=c.execute("SELECT * FROM study_topics WHERE id=?",(topic_id,)).fetchone()
    if not row:
        c.close(); return JSONResponse({"ok":False,"error":"Assunto não encontrado."},status_code=404)
    now=datetime.now().isoformat()
    c.execute("UPDATE study_topics SET status='completed', completed_at=?, notes=? WHERE id=?",(now,notes,topic_id))
    # Optional session entry so the progress panel and topic completion stay aligned.
    score=round(correct/questions*100,1) if questions else 0
    c.execute("INSERT INTO sessions(discipline,topic,minutes,questions,correct,score,notes,created_at) VALUES(?,?,?,?,?,?,?,?)",
              (row["discipline"],row["topic"],minutes,questions,correct,score,notes,now))
    # Automatic review checkpoints for a completed topic.
    for days,kind in ((0,"R0"),(1,"R1"),(7,"R7"),(30,"R30")):
        rd=(date.today()+timedelta(days=days)).isoformat()
        c.execute("INSERT INTO reviews(topic_id,discipline,topic,review_date,review_type,completed) VALUES(?,?,?,?,?,0)",
                  (topic_id,row["discipline"],row["topic"],rd,kind))
    c.commit(); c.close()
    return {"ok":True,"message":"Assunto concluído e próximas revisões programadas.","score":score}

@app.post("/api/plan/reset")
def reset_plan():
    c=conn(); c.execute("UPDATE study_topics SET status='pending',completed_at=NULL,notes=''" ); c.commit(); c.close()
    return {"ok":True}

@app.get("/api/reviews")
def reviews():
    c=conn(); rows=c.execute("SELECT * FROM reviews WHERE completed=0 ORDER BY review_date,id LIMIT 100").fetchall(); c.close()
    return {"items":[dict(x) for x in rows]}

@app.post("/api/reviews/complete")
def complete_review(review_id:int=Form(...)):
    c=conn(); row=c.execute("SELECT * FROM reviews WHERE id=?",(review_id,)).fetchone()
    if not row:
        c.close(); return JSONResponse({"ok":False,"error":"Revisão não encontrada."},status_code=404)
    c.execute("UPDATE reviews SET completed=1,completed_at=? WHERE id=?",(datetime.now().isoformat(),review_id)); c.commit(); c.close()
    return {"ok":True}

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
    c2=conn(); total_topics=c2.execute("SELECT COUNT(*) FROM study_topics").fetchone()[0]; done_topics=c2.execute("SELECT COUNT(*) FROM study_topics WHERE status='completed'").fetchone()[0]; c2.close()
    plan_pct=round(done_topics/total_topics*100,1) if total_topics else 0
    return {"hours": round(total_min / 60, 1), "questions": q, "correct": correct,
            "score": score, "weak_topics": len(set(x["topic"] for x in e if x["topic"])),
            "materials": [dict(x) for x in m], "errors": [dict(x) for x in e],
            "plan_total": total_topics, "plan_completed": done_topics, "plan_pct": plan_pct}


@app.get("/api/today")
def today():
    c=conn()
    mats=c.execute("SELECT id,filename,subject,topic,processing_status FROM materials ORDER BY id DESC LIMIT 20").fetchall()
    rows=c.execute("SELECT * FROM study_topics WHERE status!='completed' ORDER BY order_no LIMIT 2").fetchall()
    reviews_due=c.execute("SELECT * FROM reviews WHERE completed=0 AND review_date<=? ORDER BY review_date,id LIMIT 5",(date.today().isoformat(),)).fetchall()
    c.close()
    blocks=[]
    for r in rows:
        material=None
        for m in mats:
            mt=(m["topic"] or "").strip().lower(); rt=r["topic"].lower()
            if m["processing_status"]=="ready" and mt and (mt in rt or rt in mt):
                material=m["filename"]; break
        blocks.append({"id":r["id"],"discipline":r["discipline"],"topic":r["topic"],"pages":"Consulte o PDF associado; o mapeamento exato de páginas será adicionado em versão futura","theory":40,"reverse":20,"priority":r["priority"],"material":material})
    return {"date":date.today().strftime("%d/%m/%Y"),"blocks":blocks,"reviews":[dict(x) for x in reviews_due]}


@app.get("/api/materials")
def materials():
    c = conn(); rows = [dict(x) for x in c.execute("SELECT * FROM materials ORDER BY id DESC").fetchall()]; c.close()
    if supabase_configured():
        try:
            objects = supabase_list("pdfs/")
            known = {x.get("storage_path") for x in rows if x.get("storage_path")}
            for obj in objects:
                name = obj.get("name", "")
                if not name or name.endswith("/"): continue
                sp = "pdfs/" + name
                if sp in known: continue
                try:
                    meta = json.loads(supabase_download("meta/" + name + ".json").decode("utf-8"))
                except Exception:
                    meta = {"filename": name, "subject":"", "topic":"", "created_at":"", "size_bytes":0, "sha256":"", "storage_path":sp, "processing_status":"archived"}
                c=conn()
                cur=c.execute("""INSERT INTO materials(filename,openai_file_id,vector_store_id,subject,topic,created_at,processing_status,processing_error,size_bytes,storage_path,storage_backend,sha256)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (meta.get("filename",name),meta.get("openai_file_id"),meta.get("vector_store_id"),meta.get("subject",""),meta.get("topic",""),meta.get("created_at",""),meta.get("processing_status","archived"),meta.get("processing_error",""),meta.get("size_bytes",0),sp,"supabase",meta.get("sha256","")))
                new_id=cur.lastrowid; c.commit(); c.close()
                if meta.get("vector_store_id") and not current_vs(): set_setting("vector_store_id",meta.get("vector_store_id"))
                c=conn(); recovered=c.execute("SELECT * FROM materials WHERE id=?",(new_id,)).fetchone(); c.close()
                rows.append(dict(recovered))
        except Exception:
            pass
    return {"items": rows}


@app.get("/api/materials/{material_id}/file")
def material_file(material_id:int):
    c=conn(); row=c.execute("SELECT * FROM materials WHERE id=?",(material_id,)).fetchone(); c.close()
    if not row: return JSONResponse({"ok":False,"error":"PDF não encontrado."},status_code=404)
    if row["storage_backend"]=="supabase" and row["storage_path"]:
        try:
            data=supabase_download(row["storage_path"])
            return Response(content=data, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{row["filename"]}"'})
        except Exception as e:
            return JSONResponse({"ok":False,"error":"Não foi possível abrir o PDF: "+str(e)},status_code=500)
    p=UPLOADS/Path(row["filename"]).name
    if not p.exists(): return JSONResponse({"ok":False,"error":"Arquivo PDF não está disponível no servidor."},status_code=404)
    return FileResponse(p,media_type="application/pdf",filename=row["filename"],headers={"Content-Disposition":f'inline; filename="{row["filename"]}"'})
