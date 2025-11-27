from __future__ import annotations
import os, json, hmac, hashlib, asyncio, csv, io, smtplib, ssl, secrets, uuid
from email.message import EmailMessage
from datetime import datetime, timezone, timedelta, date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pathlib import Path
from typing import Any, Dict, Optional, List
print("🚨 app/main.py cargado. Estructura de rutas corregida. 🚨")

# ---- .env opcional ----
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Header, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import StreamingResponse, Response
from pydantic import BaseModel, Field, EmailStr
from fastapi.responses import FileResponse # Import para servir index.html

from sqlalchemy import create_engine, String, Integer, Float, Boolean, DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

# ====================================================================
# ========= CONFIGURACIÓN GLOBAL Y REGLAS ============================
# ====================================================================

# ========= Configuración DB =========
DB_URL = "sqlite:///torneo.db"
engine = create_engine(DB_URL, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

# === REGLAS DEL TORNEO ===
MAX_PARTICIPANTS = 20       # Límite de inscripción
MAX_MICROS = 10             # Límite de tamaño de posición
MAX_LOSS = -2000.0          # Límite de Stop Loss / Drawdown máximo (P&L acumulado)

class Base(DeclarativeBase):
    pass

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def compute_drawdown(peak: float, equity: float) -> float:
    return min(0.0, equity - peak)

try:
    TZ_CO = ZoneInfo("America/Bogota")
except ZoneInfoNotFoundError:
    TZ_CO = timezone.utc

# ====================================================================
# ========= MODELOS DE BASE DE DATOS =================================
# ====================================================================

class Participant(Base):
    __tablename__ = "participants"
    id: Mapped[int]     = mapped_column(Integer, primary_key=True, autoincrement=True)
    handle: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    pnl: Mapped[float]  = mapped_column(Float, default=0.0)
    trades: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int]   = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    max_dd: Mapped[float]= mapped_column(Float, default=0.0)
    peak_equity: Mapped[float]= mapped_column(Float, default=0.0)
    last_symbol: Mapped[str]   = mapped_column(String(32), default="")
    last_price:  Mapped[float]= mapped_column(Float, default=0.0)
    position_size: Mapped[int] = mapped_column(Integer, default=0)
    eliminated: Mapped[bool]   = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime]= mapped_column(DateTime(timezone=True), default=now_utc)

class Trade(Base):
    __tablename__ = "trades"
    id: Mapped[int]              = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime]         = mapped_column(DateTime(timezone=True), index=True)
    handle: Mapped[str]          = mapped_column(String(64), index=True)
    symbol: Mapped[str]          = mapped_column(String(32))
    qty: Mapped[int]             = mapped_column(Integer)
    price: Mapped[float]         = mapped_column(Float)
    position_size_after: Mapped[int] = mapped_column(Integer)
    realized_pnl: Mapped[float]  = mapped_column(Float)
    cumulative_pnl: Mapped[float]= mapped_column(Float)

class Registration(Base):
    __tablename__ = "registrations"
    id: Mapped[int]         = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str]        = mapped_column(String(120))
    email: Mapped[str]       = mapped_column(String(200), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class Credentials(Base):
    __tablename__ = "credentials"
    api_key: Mapped[str]     = mapped_column(String(128), primary_key=True, index=True)
    secret: Mapped[str]      = mapped_column(String(256))
    handle: Mapped[str]      = mapped_column(String(64), index=True)
    display_name: Mapped[str]= mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

Base.metadata.create_all(bind=engine)

# ====================================================================
# ========= HELPERS & LOGICA DE SEGURIDAD (HMAC) =====================
# ====================================================================

# Configuración de HMAC
USE_HMAC = os.getenv("USE_HMAC", "true").lower() in ("1", "true", "yes")

def compute_hmac(key: str, data: str, timestamp: str) -> str:
    msg = f"{data}|{timestamp}".encode("utf-8")
    signature = hmac.new(key.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return signature

# Configuración de Email
EMAIL_ENABLED   = os.getenv("EMAIL_ENABLED", "true").lower() in ("1","true","yes")
SMTP_HOST       = os.getenv("SMTP_HOST")
SMTP_PORT       = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER       = os.getenv("SMTP_USER")
SMTP_PASS       = os.getenv("SMTP_PASS")
MAIL_FROM       = os.getenv("MAIL_FROM") or SMTP_USER or "no-reply@localhost"
MAIL_TO_ADMIN   = os.getenv("MAIL_TO_ADMIN") or ""
SEND_CONFIRM    = os.getenv("SEND_CONFIRM", "false").lower() in ("1","true","yes")

def send_email(subject: str, body: str, to_addrs: List[str]) -> None:
    if not EMAIL_ENABLED: return
    if not (SMTP_HOST and SMTP_USER and SMTP_PASS and MAIL_FROM and to_addrs):
        print("[MAIL] Falta configuración SMTP; no se envía.")
        return
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"]    = MAIL_FROM
        msg["To"]      = ", ".join([a for a in to_addrs if a])
        msg.set_content(body)
        
        context = ssl.create_default_context()
        
        # --- BLOQUE CORREGIDO PARA PUERTO 465 (SMTP_SSL) ---
        # Si SMTP_PORT es 465, se usará SMTP_SSL.
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15, context=context) as server:
                server.login(SMTP_USER, SMTP_PASS)
                server.send_message(msg)
        # Si SMTP_PORT es 587 (el default), se usa SMTP con STARTTLS.
        else: 
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
                server.starttls(context=context)
                server.login(SMTP_USER, SMTP_PASS)
                server.send_message(msg)
        # ----------------------------------------------------
        
        print(f"[MAIL] Enviado a {msg['To']}")
    except Exception as e:
        print("[MAIL] Error enviando correo:", e)

# Helpers de Credenciales
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
REGISTRY_BY_KEY: dict[str, dict] = {} 
DISPLAY_BY_HANDLE: dict[str, str] = {}
LAST_HB: dict[str, dict] = {} 

def generate_api_key() -> str: return uuid.uuid4().hex
def generate_secret() -> str: return secrets.token_urlsafe(32)

def update_env_credentials(creds: List[Credentials]):
    """Actualiza API_CREDENTIALS y DISPLAY_NAMES en .env"""
    lines = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    lines = [l for l in lines if not l.startswith("API_CREDENTIALS=") and not l.startswith("DISPLAY_NAMES=")]
    api_val = ",".join([f"{c.handle}:{c.api_key}:{c.secret}" for c in creds])
    disp_val = ",".join([f"{c.handle}:{c.display_name}" for c in creds])
    lines.append(f"API_CREDENTIALS={api_val}")
    lines.append(f"DISPLAY_NAMES={disp_val}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

def reload_registry_from_db():
    with SessionLocal() as db:
        creds = db.query(Credentials).all()
        REGISTRY_BY_KEY.clear()
        for c in creds:
            REGISTRY_BY_KEY[c.api_key] = {"secret": c.secret, "handle": c.handle, "display_name": c.display_name}
    print(f"[BOOT] creds en memoria: {len(REGISTRY_BY_KEY)}")

def load_display_names_from_env():
    DISPLAY_BY_HANDLE.clear()
    names = (os.getenv("DISPLAY_NAMES") or "").strip()
    if not names: return
    for chunk in names.split(","):
        chunk = chunk.strip()
        if not chunk: continue
        try:
            handle, disp = chunk.split(":", 1)
            DISPLAY_BY_HANDLE[handle.strip()] = (disp or handle).strip()
        except ValueError:
            print(f"[WARN] DISPLAY_NAMES mal formado: {chunk}")
    print(f"[BOOT] display names cargados: {len(DISPLAY_BY_HANDLE)}")

def verify_hmac_headers(x_api_key: str, x_timestamp: str, x_signature: str, raw_body: str, handle: str) -> None:
    """Verifica que la API Key y la firma sean válidas."""
    if not USE_HMAC: return
    
    if not all([x_api_key, x_timestamp, x_signature]):
        raise HTTPException(status_code=401, detail="Faltan encabezados de autenticación (API, TS, SIG)")

    cred = REGISTRY_BY_KEY.get(x_api_key)
    if not cred:
        raise HTTPException(status_code=401, detail="API Key no reconocida")
    
    if cred["handle"] != handle:
        raise HTTPException(status_code=401, detail="API Key no corresponde al Handle")

    expected_sig = compute_hmac(cred["secret"], raw_body, x_timestamp)
    if not hmac.compare_digest(x_signature, expected_sig):
        raise HTTPException(status_code=401, detail="Firma HMAC no válida")
    
    # Simple check de tiempo (opcional: implementar más robusto)
    try:
        ts_diff = abs(int(x_timestamp) - int(datetime.now(timezone.utc).timestamp() * 1000))
        if ts_diff > 300000: # 5 minutos
            print(f"[WARN] Tiempo desfasado: {ts_diff/1000}s para {handle}")
    except:
        pass


reload_registry_from_db()
load_display_names_from_env()

# ====================================================================
# ========= FASTAPI, WS MANAGER & SCHEMAS ============================
# ====================================================================

# Inicialización de FastAPI
app = FastAPI(title="Torneo WS Demo")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# Definición de Static Dir
project_root = Path(os.getcwd())
STATIC_DIR = project_root / "web"

# WS Manager para manejar conexiones activas
class WSManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.broadcast_queue = asyncio.Queue()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        try:
            self.active_connections.remove(websocket)
        except ValueError:
            pass

    async def broadcast(self, message: Any):
        if not isinstance(message, str):
            message = json.dumps(message)
        
        dead_connections = []
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except WebSocketDisconnect:
                dead_connections.append(connection)
            except RuntimeError: # Para manejar "WebSocket has not been accepted"
                dead_connections.append(connection)
        
        for connection in dead_connections:
            self.disconnect(connection)

manager = WSManager()

# Schemas para Pydantic
class Heartbeat(BaseModel):
    handle: str
    account_balance: float
    unrealized_pnl: float

class TradeEvent(BaseModel):
    handle: str
    symbol: str
    qty: int
    price: float
    position_size: int = Field(..., description="Tamaño de la posición después del trade")
    realized_pnl: float
    ts: Optional[int] = None

class RegistrationIn(BaseModel):
    name: str
    email: EmailStr

# Función para dar formato a los datos del participante
def participant_to_row(p: Participant) -> Dict[str, Any]:
    # Usamos el display_name para mostrar el nombre de pila
    display_name = DISPLAY_BY_HANDLE.get(p.handle, p.handle)
    
    # Formateo de hora local para la tabla
    local_time_str = ""
    if p.updated_at:
        local_time = p.updated_at.astimezone(TZ_CO)
        local_time_str = local_time.strftime("%I:%M:%S %p")
        
    return {
        "handle": p.handle,
        "display_name": display_name,
        "pnl": round(p.pnl, 2),
        "trades": p.trades,
        "wins": p.wins,
        "losses": p.losses,
        "max_dd": round(p.max_dd, 2),
        "position_size": p.position_size,
        "last_trade": local_time_str,
        "is_eliminated": p.eliminated, # Aquí se envía el estado de descalificación
    }

# ====================================================================
# ========= ENDPOINTS HTTP (RUTAS REST) ==============================
# ====================================================================

# === 1. RUTA RAIZ HTTP (Soluciona 404/403) ===
@app.get("/")
async def serve_index():
    """Sirve el index.html en la raíz para evitar conflicto con WS/StaticFiles."""
    index_path = STATIC_DIR / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="index.html no encontrado.")
    return FileResponse(index_path)
# ============================================

@app.get("/ping")
def ping():
    return {"ok": True, "ts": now_utc().isoformat(), "hmac": USE_HMAC, "participants": len(REGISTRY_BY_KEY)}

# === 2. RUTA DE ESTADO (Límite de Inscripción) ===
@app.get("/status")
def get_tournament_status():
    """Ruta para que el frontend obtenga el conteo de inscripciones."""
    with SessionLocal() as db:
        current_count = db.query(func.count(Registration.id)).scalar()
        
    return {
        "participants": current_count,
        "max_participants": MAX_PARTICIPANTS,
        "is_full": current_count >= MAX_PARTICIPANTS
    }

@app.get("/leaderboard")
def get_leaderboard():
    with SessionLocal() as db:
        rows = [participant_to_row(p) for p in db.query(Participant).all()]
    rows.sort(key=lambda r: r["pnl"], reverse=True)
    return {"type": "leaderboard", "asof": now_utc().isoformat(), "rows": rows}

# === 3. RUTA DE INSCRIPCIÓN (Con Límite de 20) ===
@app.post("/register")
def register_user(data: RegistrationIn, background: BackgroundTasks):
    name = (data.name or "").strip()
    email = (data.email or "").strip().lower()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
        
    with SessionLocal() as db:
        # Límite de 20 Personas
        current_count = db.query(Registration).count()
        if current_count >= MAX_PARTICIPANTS:
            raise HTTPException(
                status_code=403, 
                detail=f"Inscripciones llenas. El límite de {MAX_PARTICIPANTS} participantes ha sido alcanzado."
            )
        
        # Verificar si ya existe
        existing = db.query(Registration).filter_by(email=email).one_or_none()
        if existing:
            return {"ok": True, "message": "Ya estabas inscrito con este correo. Tus credenciales son las mismas."}
            
        # Crear Registration
        r = Registration(name=name, email=email)
        db.add(r); db.commit()

        # Generar Credenciales
        handle = "P" + uuid.uuid4().hex[:8]
        api_key = generate_api_key(); secret = generate_secret()
        cred = Credentials(api_key=api_key, secret=secret, handle=handle, display_name=name)
        db.add(cred); db.commit()

        # Actualizar ENVs y registros en memoria
        creds = db.query(Credentials).all()
        update_env_credentials(creds)
        
    reload_registry_from_db()
    load_display_names_from_env()

    # Enviar Correo con Llaves
    body = (
        f"Hola {name},\n\n"
        "Bienvenido a uno de los torneos más importantes de trading en Latinoamérica.\n"
        "RECUERDA LAS REGLAS DE DESCALIFICACIÓN: 1) Máximo 10 micros. 2) Pérdida máxima de -$2000 USD.\n\n"
        f"Tus credenciales de conexión son:\nHandle: {handle}\nAPI_KEY: {api_key}\nSECRET: {secret}\n"
    )
    background.add_task(send_email, "✅ Credenciales Torneo Scalperos", body, [email]) 

    if MAIL_TO_ADMIN:
        background.add_task(send_email, "Nueva inscripción", f"{name} <{email}> se ha inscrito.", [MAIL_TO_ADMIN])

    return {"ok": True, "message": "Inscripción registrada. Revisa tu correo para tus credenciales."}

# === 4. RUTA DE TRADE (Con Reglas de Descalificación) ===
@app.post("/events/trade")
async def post_trade(
    ev: TradeEvent,
    request: Request,
    x_api_key: str | None = Header(None),
    x_timestamp: str | None = Header(None),
    x_signature: str | None = Header(None),
):
    raw = (await request.body()).decode("utf-8")
    # Autenticación de HMAC
    verify_hmac_headers(x_api_key, x_timestamp, x_signature, raw, ev.handle)

    ts = datetime.fromtimestamp(ev.ts / 1000.0, tz=timezone.utc) if ev.ts else now_utc()

    with SessionLocal() as db:
        p = db.query(Participant).filter_by(handle=ev.handle).one_or_none()
        if not p:
            p = Participant(handle=ev.handle)
            db.add(p); db.flush()

        # --- APLICACIÓN DE REGLAS DE DESCALIFICACIÓN ---
        if not p.eliminated:
            # Regla 1: Límite de Micros
            if abs(ev.position_size) > MAX_MICROS:
                p.eliminated = True
                print(f"[RULE FAIL] {p.handle}: Exceso de micros ({abs(ev.position_size)} > {MAX_MICROS})")

            # Actualizar estadísticas
            new_pnl = p.pnl + ev.realized_pnl
            p.trades += 1
            p.wins += (1 if ev.realized_pnl >= 0 else 0)
            p.losses += (1 if ev.realized_pnl < 0 else 0)

            p.peak_equity = max(p.peak_equity, new_pnl)
            dd = compute_drawdown(p.peak_equity, new_pnl)
            p.max_dd = min(p.max_dd, dd)
            p.pnl = new_pnl

            # Regla 2: Límite de Pérdida Máxima ($2000)
            if not p.eliminated and p.pnl <= MAX_LOSS:
                p.eliminated = True
                print(f"[RULE FAIL] {p.handle}: Pérdida máxima alcanzada ({p.pnl} <= {MAX_LOSS})")
                
            p.position_size = ev.position_size
            p.updated_at = now_utc()
            p.last_symbol = ev.symbol
            p.last_price  = ev.price
        
        # Guardar el trade
        t = Trade(
            ts=ts, handle=ev.handle, symbol=ev.symbol, qty=ev.qty, price=ev.price,
            position_size_after=ev.position_size, realized_pnl=ev.realized_pnl,
            cumulative_pnl=p.pnl,
        )
        db.add(t); db.commit()

        # Preparar y enviar Leaderboard
        rows = [participant_to_row(x) for x in db.query(Participant).all()]
        rows.sort(key=lambda r: r["pnl"], reverse=True)
        payload = {"type": "leaderboard", "asof": now_utc().isoformat(), "rows": rows}

    await manager.broadcast(payload)
    return {"ok": True}

# ====================================================================
# ========= WEB SOCKET ===============================================
# ====================================================================

@app.websocket("/ws/leaderboard")
async def ws_leaderboard(ws: WebSocket):
    await manager.connect(ws)
    try:
        await ws.send_json({"type": "hello", "ts": now_utc().isoformat()})
        while True:
            # Este loop mantendrá la conexión viva y recibirá mensajes (si los hay)
            await ws.receive_text() 
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception as e:
        print(f"[WS ERROR] {e}")
        manager.disconnect(ws)

# ====================================================================
# ========= MONTAJE DE ARCHIVOS ESTÁTICOS (FINAL) ====================
# ====================================================================

# Montaje de archivos estáticos en /static-files (Solución al conflicto)
app.mount("/static-files", StaticFiles(directory=STATIC_DIR), name="static")