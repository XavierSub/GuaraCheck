"""
GeoCheckIn - UniCerrado (Centro Universitário de Goiatuba)
Registro de presença por geolocalização (geofencing) - backend FastAPI.

Execução local:  python app.py   ->  http://localhost:8000
Produção:        uvicorn app:app --host 0.0.0.0 --port $PORT --workers 2
"""
import hashlib
import hmac
import math
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional

import jwt
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import (Boolean, DateTime, Float, ForeignKey, Index, Integer,
                        String, UniqueConstraint, create_engine, event, func,
                        select, text, update)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import (DeclarativeBase, Mapped, Session, mapped_column,
                            sessionmaker)

# =====================================================================
# CONFIGURAÇÃO (tudo pode ser sobrescrito por variáveis de ambiente / .env)
# =====================================================================
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.getenv("SECRET_KEY", "dev-inseguro-troque-em-producao")
if SECRET_KEY.startswith("dev-inseguro"):
    print("AVISO: defina SECRET_KEY no ambiente antes de ir para produção.")

DOMINIO_ALUNOS = os.getenv("DOMINIO_ALUNOS", "alunos.unicerrado.edu.br").lower()
DOMINIOS_PROFESSORES = [d.strip().lower() for d in
                        os.getenv("DOMINIOS_PROFESSORES", "unicerrado.edu.br").split(",") if d.strip()]
CODIGO_PROFESSOR = os.getenv("CODIGO_PROFESSOR", "").strip()  # opcional: exige código no cadastro de professor
TOKEN_HORAS = int(os.getenv("TOKEN_HORAS", "12"))
PRECISAO_MAX_M = float(os.getenv("PRECISAO_MAX_M", "150"))   # rejeita GPS muito impreciso
LIMITE_PRESENCAS_POR_CONSULTA = 2000

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{(BASE_DIR / 'geocheckin.db').as_posix()}")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

# =====================================================================
# BANCO DE DADOS (SQLAlchemy 2.0) - SQLite local, PostgreSQL em produção
# =====================================================================
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(conn, _):
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")   # leituras não bloqueiam escritas
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
else:
    engine = create_engine(
        DATABASE_URL,
        pool_size=int(os.getenv("DB_POOL_SIZE", "10")),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
        pool_timeout=10,
        pool_recycle=1800,
        pool_pre_ping=True,
    )

SessionLocal = sessionmaker(engine, expire_on_commit=False, autoflush=False)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


class Base(DeclarativeBase):
    pass


class Aluno(Base):
    __tablename__ = "alunos"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    matricula: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    senha_hash: Mapped[str] = mapped_column(String(200))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Professor(Base):
    __tablename__ = "professores"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    senha_hash: Mapped[str] = mapped_column(String(200))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Aula(Base):
    """Uma chamada aberta pelo professor, com o ponto GPS da sala e o raio."""
    __tablename__ = "aulas"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    professor_id: Mapped[int] = mapped_column(ForeignKey("professores.id"), index=True)
    titulo: Mapped[str] = mapped_column(String(140))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    raio_metros: Mapped[float] = mapped_column(Float)
    ativa: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    aberta_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    encerrada_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Presenca(Base):
    """
    Tabela de escrita pesada: só INSERTs, sem leitura-antes-de-escrever.
    A UNIQUE (aula_id, aluno_id) garante 1 presença por aluno por aula
    mesmo com milhares de requisições simultâneas (o banco decide a corrida).
    """
    __tablename__ = "presencas"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    aula_id: Mapped[int] = mapped_column(ForeignKey("aulas.id"))
    aluno_id: Mapped[int] = mapped_column(ForeignKey("alunos.id"), index=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    distancia_m: Mapped[float] = mapped_column(Float)
    precisao_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    registrado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint("aula_id", "aluno_id", name="uq_presenca_aula_aluno"),
        Index("ix_presenca_aula_id_id", "aula_id", "id"),  # painel do professor (consulta incremental)
    )


def get_db():
    with SessionLocal() as db:
        yield db


# =====================================================================
# GEODÉSIA - fórmula de Haversine (distância em metros entre 2 pontos)
# =====================================================================
def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371008.8  # raio médio da Terra (m)
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# =====================================================================
# AUTENTICAÇÃO (senha com PBKDF2 + token JWT)
# =====================================================================
EMAIL_RE = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$")


def dominio_do_email(email: str) -> Optional[str]:
    return email.rsplit("@", 1)[1] if EMAIL_RE.match(email) else None


def email_permitido(email: str, perfil: str) -> bool:
    """Comparação EXATA do domínio: 'x@alunos.unicerrado.edu.br' nunca vale como professor."""
    d = dominio_do_email(email)
    if d is None:
        return False
    return d == DOMINIO_ALUNOS if perfil == "aluno" else d in DOMINIOS_PROFESSORES


def hash_senha(senha: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", senha.encode(), salt, 150_000)
    return f"pbkdf2${salt.hex()}${h.hex()}"


def confere_senha(senha: str, guardado: str) -> bool:
    try:
        _, salt, h = guardado.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", senha.encode(), bytes.fromhex(salt), 150_000)
        return hmac.compare_digest(calc.hex(), h)
    except ValueError:
        return False


def resposta_token(u, perfil: str) -> dict:
    token = jwt.encode({"sub": str(u.id), "role": perfil, "nome": u.nome,
                        "exp": utcnow() + timedelta(hours=TOKEN_HORAS)}, SECRET_KEY, algorithm="HS256")
    return {"token": token, "usuario": {"id": u.id, "nome": u.nome, "email": u.email, "perfil": perfil}}


def usuario_atual(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Faça login para continuar.")
    try:
        p = jwt.decode(authorization[7:], SECRET_KEY, algorithms=["HS256"])
        return {"id": int(p["sub"]), "role": p["role"], "nome": p.get("nome", "")}
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(401, "Sessão expirada. Entre novamente.")


def so_aluno(u: dict = Depends(usuario_atual)) -> dict:
    if u["role"] != "aluno":
        raise HTTPException(403, "Ação permitida apenas para alunos.")
    return u


def so_professor(u: dict = Depends(usuario_atual)) -> dict:
    if u["role"] != "professor":
        raise HTTPException(403, "Ação permitida apenas para professores.")
    return u


# =====================================================================
# SCHEMAS
# =====================================================================
class RegistroIn(BaseModel):
    perfil: Literal["aluno", "professor"]
    nome: str = Field(min_length=3, max_length=120)
    email: str = Field(max_length=160)
    senha: str = Field(min_length=8, max_length=128)
    matricula: Optional[str] = Field(None, max_length=30)
    codigo: Optional[str] = Field(None, max_length=60)


class LoginIn(BaseModel):
    perfil: Literal["aluno", "professor"]
    email: str = Field(max_length=160)
    senha: str = Field(max_length=128)


class AulaIn(BaseModel):
    titulo: str = Field(min_length=2, max_length=140)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    raio_metros: float = Field(ge=5, le=5000)


class PresencaIn(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    precisao_m: Optional[float] = Field(None, ge=0, le=1_000_000)


def aula_dict(a: Aula, total: Optional[int] = None) -> dict:
    d = {"id": a.id, "titulo": a.titulo, "latitude": a.latitude, "longitude": a.longitude,
         "raio_metros": a.raio_metros, "ativa": a.ativa,
         "aberta_em": iso(a.aberta_em), "encerrada_em": iso(a.encerrada_em)}
    if total is not None:
        d["total"] = total
    return d


# =====================================================================
# APP
# =====================================================================
@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        Base.metadata.create_all(engine)  # em produção maior, prefira migrações (Alembic)
    except Exception as exc:  # várias instâncias iniciando juntas podem colidir; é inofensivo
        print("Aviso ao criar tabelas:", exc)
    yield


app = FastAPI(title="GeoCheckIn - UniCerrado", lifespan=lifespan)


@app.get("/")
def index():
    return FileResponse(BASE_DIR / "web" / "index.html")


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/api/config")
def config():
    return {"dominio_aluno": DOMINIO_ALUNOS, "dominios_professor": DOMINIOS_PROFESSORES}


# ------------------------------ Auth ---------------------------------
@app.post("/api/auth/registro")
def registro(b: RegistroIn, db: Session = Depends(get_db)):
    email = b.email.strip().lower()
    if not email_permitido(email, b.perfil):
        esperado = (f"@{DOMINIO_ALUNOS}" if b.perfil == "aluno"
                    else " ou ".join(f"@{d}" for d in DOMINIOS_PROFESSORES))
        raise HTTPException(403, f"E-mail não autorizado. Use seu e-mail institucional ({esperado}).")
    if b.perfil == "professor" and CODIGO_PROFESSOR and not hmac.compare_digest(b.codigo or "", CODIGO_PROFESSOR):
        raise HTTPException(403, "Código de acesso de professor inválido.")
    Model = Aluno if b.perfil == "aluno" else Professor
    if db.scalar(select(Model.id).where(Model.email == email)):
        raise HTTPException(409, "Este e-mail já possui cadastro. Use a opção Entrar.")
    u = Model(nome=b.nome.strip(), email=email, senha_hash=hash_senha(b.senha))
    if b.perfil == "aluno":
        u.matricula = (b.matricula or "").strip() or None
    db.add(u)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Este e-mail já possui cadastro. Use a opção Entrar.")
    return resposta_token(u, b.perfil)


@app.post("/api/auth/login")
def login(b: LoginIn, db: Session = Depends(get_db)):
    email = b.email.strip().lower()
    if not email_permitido(email, b.perfil):
        raise HTTPException(403, "E-mail não autorizado para este perfil.")
    Model = Aluno if b.perfil == "aluno" else Professor
    u = db.scalar(select(Model).where(Model.email == email))
    if not u or not confere_senha(b.senha, u.senha_hash):
        raise HTTPException(401, "E-mail ou senha incorretos.")
    return resposta_token(u, b.perfil)


# ---------------------------- Professor -------------------------------
_cache_ativas = {"t": 0.0, "v": []}  # cache de 2 s da lista de aulas ativas (alivia o banco no início da aula)


def _aulas_ativas(db: Session) -> list:
    if time.monotonic() - _cache_ativas["t"] < 2:
        return _cache_ativas["v"]
    rows = db.execute(select(Aula, Professor.nome).join(Professor, Professor.id == Aula.professor_id)
                      .where(Aula.ativa.is_(True)).order_by(Aula.id.desc()).limit(50)).all()
    _cache_ativas.update(t=time.monotonic(), v=[{**aula_dict(a), "professor": n} for a, n in rows])
    return _cache_ativas["v"]


def _aula_do_professor(db: Session, aula_id: int, prof_id: int) -> Aula:
    a = db.get(Aula, aula_id)
    if not a or a.professor_id != prof_id:
        raise HTTPException(404, "Aula não encontrada.")
    return a


@app.post("/api/aulas")
def abrir_aula(b: AulaIn, u: dict = Depends(so_professor), db: Session = Depends(get_db)):
    # um professor mantém apenas uma chamada aberta por vez
    db.execute(update(Aula).where(Aula.professor_id == u["id"], Aula.ativa.is_(True))
               .values(ativa=False, encerrada_em=utcnow()))
    a = Aula(professor_id=u["id"], titulo=b.titulo.strip(), latitude=b.latitude,
             longitude=b.longitude, raio_metros=b.raio_metros)
    db.add(a)
    db.commit()
    _cache_ativas["t"] = 0
    return aula_dict(a, 0)


@app.post("/api/aulas/{aula_id}/encerrar")
def encerrar_aula(aula_id: int, u: dict = Depends(so_professor), db: Session = Depends(get_db)):
    a = _aula_do_professor(db, aula_id, u["id"])
    if a.ativa:
        a.ativa, a.encerrada_em = False, utcnow()
        db.commit()
        _cache_ativas["t"] = 0
    return aula_dict(a)


@app.get("/api/professor/aulas")
def minhas_aulas(u: dict = Depends(so_professor), db: Session = Depends(get_db)):
    rows = db.execute(select(Aula, func.count(Presenca.id)).outerjoin(Presenca, Presenca.aula_id == Aula.id)
                      .where(Aula.professor_id == u["id"]).group_by(Aula.id)
                      .order_by(Aula.id.desc()).limit(20)).all()
    return [aula_dict(a, n) for a, n in rows]


@app.get("/api/aulas/{aula_id}/presencas")
def presencas_da_aula(aula_id: int, apos_id: int = 0, u: dict = Depends(so_professor),
                      db: Session = Depends(get_db)):
    """Consulta incremental (só o que chegou depois de apos_id): o painel faz polling a cada ~3 s."""
    a = _aula_do_professor(db, aula_id, u["id"])
    total = db.scalar(select(func.count()).select_from(Presenca).where(Presenca.aula_id == aula_id))
    rows = db.execute(
        select(Presenca.id, Aluno.nome, Aluno.matricula, Aluno.email, Presenca.distancia_m, Presenca.registrado_em)
        .join(Aluno, Aluno.id == Presenca.aluno_id)
        .where(Presenca.aula_id == aula_id, Presenca.id > apos_id)
        .order_by(Presenca.id).limit(LIMITE_PRESENCAS_POR_CONSULTA)).all()
    return {"ativa": a.ativa, "total": total, "limite": LIMITE_PRESENCAS_POR_CONSULTA,
            "presencas": [{"id": r.id, "nome": r.nome, "matricula": r.matricula, "email": r.email,
                           "distancia_m": round(r.distancia_m, 1), "registrado_em": iso(r.registrado_em)}
                          for r in rows]}


# ------------------------------ Aluno ---------------------------------
@app.get("/api/aulas/ativas")
def aulas_ativas(u: dict = Depends(so_aluno), db: Session = Depends(get_db)):
    ativas = _aulas_ativas(db)
    ids = [a["id"] for a in ativas]
    ja = set(db.scalars(select(Presenca.aula_id).where(Presenca.aluno_id == u["id"],
                                                       Presenca.aula_id.in_(ids)))) if ids else set()
    # as coordenadas da sala NÃO são enviadas ao aluno (dificulta falsificar a localização)
    return [{"id": a["id"], "titulo": a["titulo"], "professor": a["professor"],
             "raio_metros": a["raio_metros"], "aberta_em": a["aberta_em"],
             "ja_registrou": a["id"] in ja} for a in ativas]


@app.post("/api/aulas/{aula_id}/presenca")
def registrar_presenca(aula_id: int, b: PresencaIn, u: dict = Depends(so_aluno),
                       db: Session = Depends(get_db)):
    aula = db.get(Aula, aula_id)  # busca por PK: barata mesmo sob carga
    if not aula:
        raise HTTPException(404, "Aula não encontrada.")
    if not aula.ativa:
        raise HTTPException(409, "Esta chamada já foi encerrada.")
    if b.precisao_m is not None and b.precisao_m > PRECISAO_MAX_M:
        raise HTTPException(422, f"Sinal de GPS impreciso (±{round(b.precisao_m)} m). "
                                 "Ative o GPS, vá para um local aberto e tente de novo.")
    dist = haversine(b.latitude, b.longitude, aula.latitude, aula.longitude)
    if dist > aula.raio_metros:
        raise HTTPException(403, f"Você está a {round(dist)} m da sala; o limite desta aula é "
                                 f"{round(aula.raio_metros)} m. Aproxime-se e tente novamente.")
    db.add(Presenca(aula_id=aula_id, aluno_id=u["id"], latitude=b.latitude, longitude=b.longitude,
                    distancia_m=dist, precisao_m=b.precisao_m))
    try:
        db.commit()
    except IntegrityError:  # já registrou (duplo clique, duas abas...) -> resposta idempotente
        db.rollback()
        return {"ok": True, "ja_registrado": True, "distancia_m": round(dist, 1), "aula": aula.titulo}
    return {"ok": True, "ja_registrado": False, "distancia_m": round(dist, 1), "aula": aula.titulo}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")),
                reload=os.getenv("RELOAD", "1") == "1")
