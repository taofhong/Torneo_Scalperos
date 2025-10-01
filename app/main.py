from __future__ import annotations
import os, json, hmac, hashlib, asyncio, csv, io, smtplib, ssl, secrets, uuid
from email.message import EmailMessage
from datetime import datetime, timezone, timedelta, date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pathlib import Path
from typing import Any, Dict, Optional, List

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

from sqlalchemy import create_engine, String, Integer, Float, Boolean, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

# ========= Config =========
DB_URL = "sqlite:///torneo.db"
engine = create_engine(DB_URL, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

class Base(DeclarativeBase):
    pass

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

# ======== Zona horaria del torneo (Colombia) ========
try:
    TZ_CO = ZoneInfo("America/Bogota")
except ZoneInfoNotFoundError:
    print("[WARN] No se encontró America/Bogota, usando UTC")
    TZ_CO = timezone.utc

# ========= Email =========
EMAIL_ENABLED   = os.getenv("EMAIL_ENABLED", "true").lower() in ("1","true","yes")
SMTP_HOST       = os.getenv("SMTP_HOST")
SMTP_PORT       = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER       = os.getenv("SMTP_USER")
SMTP_PASS       = os.getenv("SMTP_PASS")
MAIL_FROM       = os.getenv("MAIL_FROM") or SMTP_USER or "no-reply@localhost"
MAIL_TO_ADMIN   = os.getenv("MAIL_TO_ADMIN") or ""
SEND_CONFIRM    = os.getenv("SEND_CONFIRM", "false").lower() in ("1","true","yes")

def send_email(subject: str, body: str, to_addrs: List[str]) -> None:
    if not EMAIL_ENABLED:
        return
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
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls(context=context)
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        print(f"[MAIL] Enviado a {msg['To']}")
    except Exception as e:
        print("[MAIL] Error enviando correo:", e)

# ========= Modelos DB =========
class Participant(Base):
    __tablename__ = "participants"
    id: Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    handle: Mapped[str]  = mapped_column(String(64), unique=True, index=True)
    pnl: Mapped[float]   = mapped_column(Float, default=0.0)
    trades: Mapped[int]  = mapped_column(Integer, default=0)
    wins: Mapped[int]    = mapped_column(Integer, default=0)
    losses: Mapped[int]  = mapped_column(Integer, default=0)
    max_dd: Mapped[float]= mapped_column(Float, default=0.0)
    peak_equity: Mapped[float]= mapped_column(Float, default=0.0)
    last_symbol: Mapped[str]  = mapped_column(String(32), default="")
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
    id: Mapped[int]        = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str]      = mapped_column(String(120))
    email: Mapped[str]     = mapped_column(String(200), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class Credentials(Base):
    __tablename__ = "credentials"
    api_key: Mapped[str]    = mapped_column(String(128), primary_key=True, index=True)
    secret: Mapped[str]     = mapped_column(String(256))
    handle: Mapped[str]     = mapped_column(String(64), index=True)
    display_name: Mapped[str]= mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

Base.metadata.create_all(bind=engine)

# ========= Helpers ENV/credenciales =========
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

def generate_api_key() -> str: return uuid.uuid4().hex
def generate_secret() -> str: return secrets.token_urlsafe(32)

def update_env_credentials(creds: List[Credentials]):
    """Reescribe API_CREDENTIALS y DISPLAY_NAMES en .env según la tabla credentials"""
    lines = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    lines = [l for l in lines if not l.startswith("API_CREDENTIALS=") and not l.startswith("DISPLAY_NAMES=")]
    api_val = ",".join([f"{c.handle}:{c.api_key}:{c.secret}" for c in creds])
    disp_val = ",".join([f"{c.handle}:{c.display_name}" for c in creds])
    lines.append(f"API_CREDENTIALS={api_val}")
    lines.append(f"DISPLAY_NAMES={disp_val}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

# ========= Registro en memoria =========
REGISTRY_BY_KEY: dict[str, dict] = {}      # api_key -> {secret, handle, display_name}
DISPLAY_BY_HANDLE: dict[str, str] = {}     # handle -> display
LAST_HB: dict[str, dict] = {}              # handle -> {"account_balance": float, "unrealized_pnl": float, "asof": str}

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
    if not names:
        print("[BOOT] DISPLAY_NAMES vacío")
        return
    for chunk in names.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            handle, disp = chunk.split(":", 1)
            handle = handle.strip()
            disp = (disp or "").strip()
            DISPLAY_BY_HANDLE[handle] = disp or handle
        except ValueError:
            print(f"[WARN] DISPLAY_NAMES mal formado: {chunk}")
    print(f"[BOOT] display names cargados: {len(DISPLAY_BY_HANDLE)}")

reload_registry_from_db()
load_display_names_from_env()

# ========= FastAPI & estáticos =========
app = FastAPI(title="Torneo WS Demo")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

STATIC_DIR = (Path(__file__).resolve().parent.parent / "web").resolve()

class StaticFilesWithPost(StaticFiles):
    async def get_response(self, path, scope):
        if scope.get("method") == "POST":
            return Response(status_code=204)
        return await super().get_response(path, scope)

app.mount("/web", StaticFilesWithPost(directory=str(STATIC_DIR), html=True), name="web")

# ========= WS Manager =========
class WSManager:
    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        print("INFO:     connection open")

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: Any):
        stale = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.disconnect(ws)

manager = WSManager()

# ========= Schemas =========
class TradeEvent(BaseModel):
    handle: str
    symbol: str
    qty: int
    price: float
    position_size: int = Field(..., description="Tamaño de posición tras la ejecución (micros)")
    realized_pnl: float
    ts: Optional[int] = Field(None, description="timestamp en milisegundos (opcional)")
    order_id: Optional[str] = None
    seq: Optional[int] = None

class Heartbeat(BaseModel):
    handle: str
    account_balance: float
    unrealized_pnl: float
    seq: int
    ts: int

# ========= Reglas =========
MAX_MICROS = 10
MAX_LOSS   = -2000.0

def compute_drawdown(peak: float, equity: float) -> float:
    return min(0.0, equity - peak)  # negativo o 0

def participant_to_row(p: Participant) -> Dict[str, Any]:
    winrate = (p.wins / p.trades * 100.0) if p.trades > 0 else 0.0
    display = DISPLAY_BY_HANDLE.get(p.handle, p.handle)

    hb = LAST_HB.get(p.handle) or {}
    bal = hb.get("account_balance")
    unreal = hb.get("unrealized_pnl")

    return {
        "handle": p.handle,
        "display_name": display,
        "pnl": round(p.pnl, 2),
        "trades": p.trades,
        "winrate": round(winrate, 1),
        "max_dd": round(p.max_dd, 2),
        "position_size": p.position_size,
        "eliminated": p.eliminated,
        "updated_at": p.updated_at.isoformat(),
        # extras UI
        "last_symbol": p.last_symbol,
        "last_price": round(p.last_price, 2) if p.last_price is not None else None,
        "account_balance": float(bal) if bal is not None else None,
        "unrealized_pnl": float(unreal) if unreal is not None else None,
    }

# ========= Seguridad (HMAC) opcional =========
USE_HMAC = os.getenv("USE_HMAC", "false").lower() in ("1","true","yes")

def verify_hmac_headers(
    x_api_key: str | None,
    x_timestamp: str | None,
    x_signature: str | None,
    raw_body: str,
    claimed_handle: str | None
):
    if not USE_HMAC:
        return
    if not x_api_key or not x_timestamp or not x_signature:
        raise HTTPException(status_code=401, detail="missing hmac headers")

    info = REGISTRY_BY_KEY.get(x_api_key)
    if not info:
        raise HTTPException(status_code=401, detail="unknown api key")

    if claimed_handle and info.get("handle") != claimed_handle:
        raise HTTPException(status_code=401, detail="key/handle mismatch")

    secret = info.get("secret", "")
    msg = f"{x_timestamp}.{raw_body}".encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, x_signature):
        raise HTTPException(status_code=401, detail="bad signature")

# ========= REST =========
@app.get("/ping")
def ping():
    return {"ok": True, "ts": now_utc().isoformat(), "hmac": USE_HMAC, "participants": len(REGISTRY_BY_KEY)}

@app.get("/leaderboard")
def get_leaderboard():
    with SessionLocal() as db:
        rows = [participant_to_row(p) for p in db.query(Participant).all()]
    rows.sort(key=lambda r: r["pnl"], reverse=True)
    return {"type": "leaderboard", "asof": now_utc().isoformat(), "rows": rows}

@app.post("/events/heartbeat")
async def heartbeat(
    hb: Heartbeat,
    request: Request,
    x_api_key: str | None = Header(None),
    x_timestamp: str | None = Header(None),
    x_signature: str | None = Header(None),
):
    raw = (await request.body()).decode("utf-8")
    verify_hmac_headers(x_api_key, x_timestamp, x_signature, raw, hb.handle)
    print(f"[HB] {hb.handle} bal={hb.account_balance:.2f} unreal={hb.unrealized_pnl:.2f} seq={hb.seq}")

    # Guardar último heartbeat (para mostrar Saldo en tarjetas)
    LAST_HB[hb.handle] = {
        "account_balance": hb.account_balance,
        "unrealized_pnl": hb.unrealized_pnl,
        "asof": now_utc().isoformat()
    }

    await manager.broadcast({
        "type": "heartbeat",
        "handle": hb.handle,
        "account_balance": hb.account_balance,
        "unrealized_pnl": hb.unrealized_pnl,
        "asof": now_utc().isoformat()
    })
    return {"ok": True}

@app.post("/events/trade")
async def post_trade(
    ev: TradeEvent,
    request: Request,
    x_api_key: str | None = Header(None),
    x_timestamp: str | None = Header(None),
    x_signature: str | None = Header(None),
):
    raw = (await request.body()).decode("utf-8")
    verify_hmac_headers(x_api_key, x_timestamp, x_signature, raw, ev.handle)

    ts = datetime.fromtimestamp(ev.ts / 1000.0, tz=timezone.utc) if ev.ts else now_utc()
    print("[TRADE]", ev.handle, ev.symbol, ev.qty, ev.price, ev.position_size, ev.realized_pnl)

    with SessionLocal() as db:
        p = db.query(Participant).filter_by(handle=ev.handle).one_or_none()
        if not p:
            p = Participant(handle=ev.handle)
            db.add(p); db.flush()

        # Reglas
        if abs(ev.position_size) > MAX_MICROS:
            p.eliminated = True

        new_pnl = p.pnl + ev.realized_pnl
        p.trades += 1
        if ev.realized_pnl >= 0: p.wins += 1
        else: p.losses += 1

        p.peak_equity = max(p.peak_equity, new_pnl)
        dd = compute_drawdown(p.peak_equity, new_pnl)
        p.max_dd = min(p.max_dd, dd)

        p.pnl = new_pnl
        p.position_size = ev.position_size
        p.updated_at = now_utc()
        p.last_symbol = ev.symbol
        p.last_price  = ev.price

        if p.pnl <= MAX_LOSS:
            p.eliminated = True

        t = Trade(
            ts=ts, handle=ev.handle, symbol=ev.symbol, qty=ev.qty, price=ev.price,
            position_size_after=ev.position_size, realized_pnl=ev.realized_pnl,
            cumulative_pnl=p.pnl,
        )
        db.add(t); db.commit()

        rows = [participant_to_row(x) for x in db.query(Participant).all()]
        rows.sort(key=lambda r: r["pnl"], reverse=True)
        payload = {"type": "leaderboard", "asof": now_utc().isoformat(), "rows": rows}

    await manager.broadcast(payload)
    return {"ok": True}

# ========= Inscripción pública =========
class RegistrationIn(BaseModel):
    name: str
    email: EmailStr

@app.post("/register")
def register_user(data: RegistrationIn, background: BackgroundTasks):
    name = (data.name or "").strip()
    email = (data.email or "").strip().lower()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    with SessionLocal() as db:
        existing = db.query(Registration).filter_by(email=email).one_or_none()
        if existing:
            return {"ok": True, "message": "Ya estabas inscrito con este correo."}
        r = Registration(name=name, email=email)
        db.add(r); db.commit()

        handle = "P" + uuid.uuid4().hex[:8]
        api_key = generate_api_key(); secret = generate_secret()
        cred = Credentials(api_key=api_key, secret=secret, handle=handle, display_name=name)
        db.add(cred); db.commit()

        creds = db.query(Credentials).all()
        update_env_credentials(creds)
    reload_registry_from_db()
    load_display_names_from_env()

    body = (
        f"Hola {name},\n\n"
        "Bienvenido a uno de los torneos más importantes de trading en Latinoamérica, diviértete.\n"
        "Scalperos Torneo.\n\n"
        f"Estas son tus credenciales:\nHandle: {handle}\nAPI_KEY: {api_key}\nSECRET: {secret}\n"
    )
    background.add_task(send_email, "Bienvenido — Scalperos Torneo", body, [email])

    if MAIL_TO_ADMIN:
        background.add_task(send_email, "Nueva inscripción", f"{name} <{email}> se ha inscrito.", [MAIL_TO_ADMIN])

    return {"ok": True, "message": "Inscripción registrada. Revisa tu correo para tus credenciales."}

# ========= Admin =========
@app.post("/admin/reset")
def admin_reset(handle: str, token: str = Header(None)):
    admin_token = os.getenv("ADMIN_TOKEN")
    if admin_token and token != admin_token:
        raise HTTPException(status_code=401, detail="bad admin token")
    with SessionLocal() as db:
        p = db.query(Participant).filter_by(handle=handle).one_or_none()
        if not p:
            return {"ok": False, "detail": "handle not found"}
        p.pnl = 0.0; p.trades = 0; p.wins = 0; p.losses = 0
        p.max_dd = 0.0; p.peak_equity = 0.0; p.position_size = 0
        p.last_symbol = ""; p.last_price = 0.0
        p.eliminated = False; p.updated_at = now_utc()
        db.commit()
    return {"ok": True, "handle": handle}

@app.get("/admin/registrations")
def list_registrations(token: str = Header(None)):
    admin_token = os.getenv("ADMIN_TOKEN")
    if admin_token and token != admin_token:
        raise HTTPException(status_code=401, detail="bad admin token")
    with SessionLocal() as db:
        rows = db.query(Registration).order_by(Registration.created_at.desc()).all()
        return {"ok": True, "rows": [
            {"id": r.id, "name": r.name, "email": r.email, "created_at": r.created_at.isoformat()}
            for r in rows
        ]}

@app.get("/admin/export_csv")
def export_csv(token: str = Header(None)):
    admin_token = os.getenv("ADMIN_TOKEN")
    if admin_token and token != admin_token:
        raise HTTPException(status_code=401, detail="bad admin token")
    with SessionLocal() as db:
        rows = db.query(Registration).order_by(Registration.created_at.desc()).all()

    sio = io.StringIO()
    writer = csv.writer(sio)
    writer.writerow(["id", "name", "email", "created_at_utc"])
    for r in rows:
        writer.writerow([r.id, r.name, r.email, r.created_at.isoformat()])

    sio.seek(0)
    headers = {"Content-Disposition": "attachment; filename=registrations.csv"}
    return StreamingResponse(iter([sio.read()]), media_type="text/csv", headers=headers)

@app.post("/admin/upsert_credential")
def admin_upsert_credential(
    handle: str,
    api_key: str,
    secret: str,
    display_name: str,
    token: str = Header(None)
):
    admin_token = os.getenv("ADMIN_TOKEN")
    if admin_token and token != admin_token:
        raise HTTPException(status_code=401, detail="bad admin token")

    handle = handle.strip(); api_key = api_key.strip(); secret = secret.strip(); display_name = display_name.strip()
    if not (handle and api_key and secret and display_name):
        raise HTTPException(status_code=400, detail="missing fields")

    with SessionLocal() as db:
        cred = db.query(Credentials).filter_by(api_key=api_key).one_or_none()
        if cred:
            cred.handle = handle
            cred.secret = secret
            cred.display_name = display_name
        else:
            cred = Credentials(api_key=api_key, secret=secret, handle=handle, display_name=display_name)
            db.add(cred)
        db.commit()
        creds = db.query(Credentials).all()
        update_env_credentials(creds)

    reload_registry_from_db()
    load_display_names_from_env()
    return {"ok": True, "handle": handle, "display_name": display_name}

@app.post("/admin/reload_display_names")
def admin_reload_display_names(token: str = Header(None)):
    admin_token = os.getenv("ADMIN_TOKEN")
    if admin_token and token != admin_token:
        raise HTTPException(status_code=401, detail="bad admin token")
    load_display_names_from_env()
    return {"ok": True, "count": len(DISPLAY_BY_HANDLE), "display_by_handle": DISPLAY_BY_HANDLE}

# ========= WebSocket =========
@app.websocket("/ws/leaderboard")
async def ws_leaderboard(ws: WebSocket):
    await manager.connect(ws)
    try:
        await ws.send_json({"type": "hello", "ts": now_utc().isoformat()})
        with SessionLocal() as db:
            rows = [participant_to_row(p) for p in db.query(Participant).all()]
        rows.sort(key=lambda r: r["pnl"], reverse=True)
        await ws.send_json({"type": "leaderboard", "asof": now_utc().isoformat(), "rows": rows})

        while True:
            await asyncio.sleep(5)
            await ws.send_json({"type": "ping", "ts": now_utc().isoformat()})
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception:
        manager.disconnect(ws)

# ========= Debug =========
@app.get("/debug/auth")
def debug_auth():
    safe = { k: {"handle": v["handle"], "display_name": v.get("display_name")} for k, v in REGISTRY_BY_KEY.items() }
    return {"registry_size": len(REGISTRY_BY_KEY), "by_key": safe, "display_by_handle": DISPLAY_BY_HANDLE}

# ========= Scheduler (limpieza sábado post torneo) =========
def clear_all_credentials():
    with SessionLocal() as db:
        db.query(Credentials).delete()
        db.commit()
        update_env_credentials([])
    reload_registry_from_db()
    print("[RESET] Credenciales borradas.")

async def _reset_scheduler_loop():
    while True:
        try:
            now = now_utc().astimezone(TZ_CO)
            weekday = now.weekday()  # 5 = sábado
            if weekday == 5 and now.hour >= 13:  # 24h después del cierre del viernes 1pm
                print("[SCHED] Limpiando tablero + credenciales…")
                clear_all_credentials()
                with SessionLocal() as db:
                    db.query(Participant).delete()
                    db.query(Trade).delete()
                    db.commit()
        except Exception as e:
            print("[SCHED] error:", e)
        await asyncio.sleep(3600)

@app.on_event("startup")
async def _startup():
    asyncio.create_task(_reset_scheduler_loop())
