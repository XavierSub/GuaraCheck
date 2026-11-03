"""
GeoCheckIn V2 - UniCerrado (Centro Universitário de Goiatuba)
Presença acadêmica por geolocalização (geofencing) - FastAPI + SQLAlchemy.

Local:     python app.py            -> http://localhost:8000
Produção:  uvicorn app:app --host 0.0.0.0 --port $PORT --workers 2
"""
import csv
import hashlib
import hmac
import io
import ipaddress
import json
import math
import os
import re
import secrets
import time
import urllib.request
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Literal, Optional

import jwt
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import (Boolean, DateTime, Float, ForeignKey, Index, Integer, String,
                        UniqueConstraint, create_engine, delete, event, func, inspect,
                        select, text, update)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

# =====================================================================
# CONFIGURAÇÃO (variáveis de ambiente / arquivo .env)
# =====================================================================
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.getenv("SECRET_KEY", "dev-inseguro-troque-em-producao")
if SECRET_KEY.startswith("dev-inseguro"):
    print("AVISO: defina SECRET_KEY no ambiente antes de ir para produção.")

DOMINIO_ALUNOS = os.getenv("DOMINIO_ALUNOS", "alunos.unicerrado.edu.br").lower()
DOMINIOS_PROFESSORES = [d.strip().lower() for d in
                        os.getenv("DOMINIOS_PROFESSORES", "unicerrado.edu.br").split(",") if d.strip()]
# Chave de Acesso Master da coordenação (validada só no backend). Sem ela, ninguém cria conta de professor.
CHAVE_MASTER = (os.getenv("CHAVE_MASTER") or os.getenv("CODIGO_PROFESSOR") or "").strip()
if not CHAVE_MASTER:
    print("AVISO: CHAVE_MASTER não definida - o cadastro de professores está BLOQUEADO.")

TOKEN_HORAS = int(os.getenv("TOKEN_HORAS", "12"))
FUSO_HORAS = int(os.getenv("FUSO_HORAS", "-3"))            # fuso usado no CSV (Brasília = -3)
PRECISAO_MAX_M = float(os.getenv("PRECISAO_MAX_M", "150"))  # GPS pior que isso é rejeitado
IDADE_MAX_POSICAO_S = int(os.getenv("IDADE_MAX_POSICAO_S", "120"))
RISCO_MAX = int(os.getenv("RISCO_MAX", "3"))                # pontuação de suspeita que bloqueia
VELOCIDADE_MAX_MS = float(os.getenv("VELOCIDADE_MAX_MS", "70"))  # ~250 km/h entre dois check-ins
LIMITE_PRESENCAS_POR_CONSULTA = 2000

# Anti-VPN/Proxy (opcional): consulta o proxycheck.io. Sem PROXYCHECK_KEY a verificação fica desligada.
PROXYCHECK_KEY = os.getenv("PROXYCHECK_KEY", "").strip()
VPN_MODO = os.getenv("VPN_MODO", "avisar" if PROXYCHECK_KEY else "off").lower()  # off | avisar | bloquear
PROXY_HOPS = int(os.getenv("PROXY_HOPS", "1"))  # quantos proxies da hospedagem ficam à frente do app
IPS_PERMITIDOS = []  # redes que nunca são checadas (ex.: Wi-Fi do campus): "200.1.2.3,200.1.2.0/24"
for _r in os.getenv("IPS_PERMITIDOS", "").split(","):
    try:
        if _r.strip():
            IPS_PERMITIDOS.append(ipaddress.ip_network(_r.strip(), strict=False))
    except ValueError:
        print("IPS_PERMITIDOS: valor ignorado:", _r)

CURSOS_PADRAO = ["Administração", "Ciências Contábeis", "Direito", "Educação Física", "Enfermagem",
                 "Engenharia Civil", "Engenharia de Software", "Fisioterapia", "Gestão da TI",
                 "Pedagogia", "Psicologia"]  # ajuste com CURSOS_EXTRAS="Curso A,Curso B"

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{(BASE_DIR / 'geocheckin.db').as_posix()}")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

# =====================================================================
# BANCO DE DADOS
# =====================================================================
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(conn, _):
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
else:
    engine = create_engine(DATABASE_URL, pool_size=int(os.getenv("DB_POOL_SIZE", "10")),
                           max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
                           pool_timeout=10, pool_recycle=1800, pool_pre_ping=True)

SessionLocal = sessionmaker(engine, expire_on_commit=False, autoflush=False)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(dt: Optional[datetime]) -> Optional[str]:
    return aware(dt).isoformat() if dt else None


class Base(DeclarativeBase):
    pass


class Curso(Base):
    __tablename__ = "cursos"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(100), unique=True)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)


class Aluno(Base):
    __tablename__ = "alunos"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    matricula: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    # nullable no banco só para aceitar contas criadas na V1; a API exige o curso no cadastro
    curso_id: Mapped[Optional[int]] = mapped_column(ForeignKey("cursos.id"), nullable=True, index=True)
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
    Só INSERTs sob carga. Duas travas do próprio banco, à prova de corrida:
      - UNIQUE(aula_id, aluno_id): um aluno, uma presença por aula;
      - UNIQUE(aula_id, device_id): um aparelho, um aluno por aula.
    """
    __tablename__ = "presencas"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    aula_id: Mapped[int] = mapped_column(ForeignKey("aulas.id"))
    aluno_id: Mapped[int] = mapped_column(ForeignKey("alunos.id"), index=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    distancia_m: Mapped[float] = mapped_column(Float)
    precisao_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    device_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    fp_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # só auditoria (colide entre aparelhos iguais)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    alertas: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    registrado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint("aula_id", "aluno_id", name="uq_presenca_aula_aluno"),
        UniqueConstraint("aula_id", "device_id", name="uq_presenca_aula_device"),
        Index("ix_presenca_aula_id_id", "aula_id", "id"),
    )


def get_db():
    with SessionLocal() as db:
        yield db


def migrar_esquema():
    """create_all não altera tabelas já existentes: adiciona as colunas novas da V2 em bancos da V1."""
    novas = {
        "alunos": [("curso_id", "INTEGER REFERENCES cursos (id)")],
        "presencas": [("device_id", "VARCHAR(64)"), ("fp_hash", "VARCHAR(64)"),
                      ("ip", "VARCHAR(64)"), ("alertas", "VARCHAR(200)")],
    }
    insp = inspect(engine)
    for tabela, colunas in novas.items():
        existentes = {c["name"] for c in insp.get_columns(tabela)}
        for nome, ddl in colunas:
            if nome in existentes:
                continue
            try:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {tabela} ADD COLUMN {nome} {ddl}"))
                    if nome == "device_id":
                        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_presenca_aula_device "
                                          "ON presencas (aula_id, device_id)"))
                    if nome == "curso_id":
                        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_alunos_curso_id ON alunos (curso_id)"))
            except Exception as exc:  # outra instância pode ter feito ao mesmo tempo
                print(f"Migração {tabela}.{nome}:", exc)


def semear_cursos():
    nomes = CURSOS_PADRAO + [c.strip() for c in os.getenv("CURSOS_EXTRAS", "").split(",") if c.strip()]
    with SessionLocal() as db:
        existentes = set(db.scalars(select(Curso.nome)))
        for n in nomes:
            if n not in existentes:
                db.add(Curso(nome=n))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()


# =====================================================================
# GEODÉSIA e ANTIFRAUDE
# =====================================================================
def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distância em metros entre dois pontos (fórmula de Haversine)."""
    R = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def casas_decimais(x: float) -> int:
    s = repr(float(x))
    return len(s.split(".")[1]) if "." in s and "e" not in s else 0


def avaliar_risco(b: "PresencaIn"):
    """
    Pontua sinais típicos de localização falsa. O navegador NÃO informa se há 'mock location',
    então isto é heurística: reduz fraude casual, não impede quem controla o aparelho ou chama a API direto.
    """
    pontos, alertas = 0, []
    if b.webdriver:
        pontos += 3; alertas.append("navegador automatizado")
    if b.precisao_m is None:
        pontos += 1; alertas.append("sem precisão do GPS")
    elif b.precisao_m <= 1:
        pontos += 2; alertas.append("precisão irreal")
    if b.amostras >= 3 and b.variacao_m is not None and b.variacao_m == 0 and (b.precisao_m or 0) < 10:
        pontos += 2; alertas.append("coordenadas sem oscilação")
    if casas_decimais(b.latitude) <= 3 and casas_decimais(b.longitude) <= 3:
        pontos += 2; alertas.append("coordenadas arredondadas")
    return pontos, alertas


def ip_cliente(request: Request) -> str:
    xff = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    if xff and PROXY_HOPS > 0:
        return xff[-min(PROXY_HOPS, len(xff))]  # o cliente pode forjar o início da lista, nunca o final
    return request.client.host if request.client else ""


_cache_ip: dict = {}


def ip_e_vpn(ip: str) -> Optional[bool]:
    """True = VPN/proxy; False = limpo; None = não foi possível verificar (nunca bloqueia nesse caso)."""
    if not PROXYCHECK_KEY or not ip:
        return None
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if addr.is_private or addr.is_loopback or addr.is_link_local or any(addr in n for n in IPS_PERMITIDOS):
        return False
    hit = _cache_ip.get(ip)
    if hit and time.monotonic() - hit[0] < 6 * 3600:
        return hit[1]
    try:
        with urllib.request.urlopen(f"https://proxycheck.io/v2/{ip}?key={PROXYCHECK_KEY}&vpn=1", timeout=2.5) as r:
            dados = json.load(r)
        info = next((v for v in dados.values() if isinstance(v, dict)), None)
        if dados.get("status") not in ("ok", "warning") or info is None:
            return None
        resultado = str(info.get("proxy", "")).lower() == "yes"
    except Exception:
        return None
    if len(_cache_ip) > 20000:
        _cache_ip.clear()
    _cache_ip[ip] = (time.monotonic(), resultado)
    return resultado


_tentativas: dict = {}


def limitar_tentativas(chave: str, maximo: int = 5, janela: int = 600):
    agora = time.monotonic()
    if len(_tentativas) > 5000:
        _tentativas.clear()
    recentes = [t for t in _tentativas.get(chave, []) if agora - t < janela]
    if len(recentes) >= maximo:
        raise HTTPException(429, "Muitas tentativas. Aguarde alguns minutos e tente de novo.")
    recentes.append(agora)
    _tentativas[chave] = recentes


# =====================================================================
# AUTENTICAÇÃO
# =====================================================================
EMAIL_RE = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$")


def email_permitido(email: str, perfil: str) -> bool:
    """Compara o domínio por igualdade exata: um e-mail de aluno nunca vale como professor."""
    if not EMAIL_RE.match(email):
        return False
    d = email.rsplit("@", 1)[1]
    return d == DOMINIO_ALUNOS if perfil == "aluno" else d in DOMINIOS_PROFESSORES


def hash_senha(senha: str) -> str:
    salt = secrets.token_bytes(16)
    return f"pbkdf2${salt.hex()}${hashlib.pbkdf2_hmac('sha256', senha.encode(), salt, 150_000).hex()}"


def confere_senha(senha: str, guardado: str) -> bool:
    try:
        _, salt, h = guardado.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", senha.encode(), bytes.fromhex(salt), 150_000)
        return hmac.compare_digest(calc.hex(), h)
    except ValueError:
        return False


def resposta_token(u, perfil: str, curso: Optional[str] = None) -> dict:
    token = jwt.encode({"sub": str(u.id), "role": perfil, "nome": u.nome,
                        "exp": utcnow() + timedelta(hours=TOKEN_HORAS)}, SECRET_KEY, algorithm="HS256")
    return {"token": token, "usuario": {"id": u.id, "nome": u.nome, "email": u.email,
                                        "perfil": perfil, "curso": curso}}


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
    curso_id: Optional[int] = None
    chave_master: Optional[str] = Field(None, max_length=100)


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
    pos_ts: float                                   # instante da leitura do GPS (ms desde 1970)
    amostras: int = Field(1, ge=1, le=50)           # nº de leituras de GPS coletadas
    variacao_m: Optional[float] = Field(None, ge=0)  # maior distância entre as leituras
    webdriver: bool = False                         # navigator.webdriver
    device_id: str = Field(pattern=r"^[A-Za-z0-9\-]{16,64}$")
    fp_hash: Optional[str] = Field(None, pattern=r"^[a-f0-9]{8,64}$")


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
        Base.metadata.create_all(engine)
        migrar_esquema()
        semear_cursos()
    except Exception as exc:
        print("Aviso na inicialização do banco:", exc)
    yield


app = FastAPI(title="GeoCheckIn V2 - UniCerrado", lifespan=lifespan)


@app.get("/")
def index():
    for p in (BASE_DIR / "web" / "index.html", BASE_DIR / "index.html"):
        if p.exists():
            return FileResponse(p)
    raise HTTPException(404, "index.html não encontrado no repositório")


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/api/config")
def config():
    return {"dominio_aluno": DOMINIO_ALUNOS, "dominios_professor": DOMINIOS_PROFESSORES}


@app.get("/api/cursos")
def listar_cursos(db: Session = Depends(get_db)):
    rows = db.execute(select(Curso.id, Curso.nome).where(Curso.ativo.is_(True)).order_by(Curso.nome)).all()
    return [{"id": r.id, "nome": r.nome} for r in rows]


# ------------------------------ Auth ---------------------------------
@app.post("/api/auth/registro")
def registro(b: RegistroIn, request: Request, db: Session = Depends(get_db)):
    email = b.email.strip().lower()
    if not email_permitido(email, b.perfil):
        esperado = (f"@{DOMINIO_ALUNOS}" if b.perfil == "aluno"
                    else " ou ".join(f"@{d}" for d in DOMINIOS_PROFESSORES))
        raise HTTPException(403, f"E-mail não autorizado. Use seu e-mail institucional ({esperado}).")

    curso = None
    if b.perfil == "professor":
        limitar_tentativas("chave:" + ip_cliente(request))  # freio contra tentativa e erro da chave
        if not CHAVE_MASTER:
            raise HTTPException(403, "Cadastro de professores indisponível: a coordenação ainda não configurou a chave.")
        if not hmac.compare_digest((b.chave_master or "").encode(), CHAVE_MASTER.encode()):
            raise HTTPException(403, "Chave de Acesso Master inválida. Solicite à coordenação.")
    else:
        curso = db.get(Curso, b.curso_id) if b.curso_id else None
        if not curso or not curso.ativo:
            raise HTTPException(422, "Selecione o seu curso.")

    Model = Aluno if b.perfil == "aluno" else Professor
    if db.scalar(select(Model.id).where(Model.email == email)):
        raise HTTPException(409, "Este e-mail já possui cadastro. Use a opção Entrar.")
    u = Model(nome=b.nome.strip(), email=email, senha_hash=hash_senha(b.senha))
    if b.perfil == "aluno":
        u.matricula = (b.matricula or "").strip() or None
        u.curso_id = curso.id
    db.add(u)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Este e-mail já possui cadastro. Use a opção Entrar.")
    return resposta_token(u, b.perfil, curso.nome if curso else None)


@app.post("/api/auth/login")
def login(b: LoginIn, db: Session = Depends(get_db)):
    email = b.email.strip().lower()
    if not email_permitido(email, b.perfil):
        raise HTTPException(403, "E-mail não autorizado para este perfil.")
    Model = Aluno if b.perfil == "aluno" else Professor
    u = db.scalar(select(Model).where(Model.email == email))
    if not u or not confere_senha(b.senha, u.senha_hash):
        raise HTTPException(401, "E-mail ou senha incorretos.")
    curso = db.scalar(select(Curso.nome).where(Curso.id == u.curso_id)) if b.perfil == "aluno" and u.curso_id else None
    return resposta_token(u, b.perfil, curso)


# ---------------------------- Professor -------------------------------
_cache_ativas = {"t": 0.0, "v": []}


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


@app.delete("/api/aulas/{aula_id}")
def excluir_aula(aula_id: int, u: dict = Depends(so_professor), db: Session = Depends(get_db)):
    """Exclui a aula e, em cascata, todas as presenças dela (na mesma transação)."""
    _aula_do_professor(db, aula_id, u["id"])
    removidas = db.execute(delete(Presenca).where(Presenca.aula_id == aula_id)).rowcount
    db.execute(delete(Aula).where(Aula.id == aula_id))
    db.commit()
    _cache_ativas["t"] = 0
    return {"ok": True, "presencas_removidas": removidas}


@app.get("/api/professor/aulas")
def minhas_aulas(u: dict = Depends(so_professor), db: Session = Depends(get_db)):
    rows = db.execute(select(Aula, func.count(Presenca.id)).outerjoin(Presenca, Presenca.aula_id == Aula.id)
                      .where(Aula.professor_id == u["id"]).group_by(Aula.id)
                      .order_by(Aula.id.desc()).limit(30)).all()
    return [aula_dict(a, n) for a, n in rows]


@app.get("/api/aulas/{aula_id}/presencas")
def presencas_da_aula(aula_id: int, apos_id: int = 0, u: dict = Depends(so_professor),
                      db: Session = Depends(get_db)):
    a = _aula_do_professor(db, aula_id, u["id"])
    total = db.scalar(select(func.count()).select_from(Presenca).where(Presenca.aula_id == aula_id))
    rows = db.execute(
        select(Presenca.id, Aluno.nome, Aluno.matricula, Aluno.email, Curso.nome.label("curso"),
               Presenca.distancia_m, Presenca.registrado_em, Presenca.alertas)
        .select_from(Presenca).join(Aluno, Aluno.id == Presenca.aluno_id)
        .outerjoin(Curso, Curso.id == Aluno.curso_id)
        .where(Presenca.aula_id == aula_id, Presenca.id > apos_id)
        .order_by(Presenca.id).limit(LIMITE_PRESENCAS_POR_CONSULTA)).all()
    return {"ativa": a.ativa, "total": total, "limite": LIMITE_PRESENCAS_POR_CONSULTA,
            "presencas": [{"id": r.id, "nome": r.nome, "matricula": r.matricula, "email": r.email,
                           "curso": r.curso, "distancia_m": round(r.distancia_m, 1),
                           "registrado_em": iso(r.registrado_em), "alertas": r.alertas} for r in rows]}


def _seguro(v) -> str:
    """Evita injeção de fórmula ao abrir o CSV no Excel."""
    v = str(v or "")
    return "'" + v if v[:1] in ("=", "+", "-", "@") else v


@app.get("/api/aulas/{aula_id}/exportar")
def exportar_chamada(aula_id: int, u: dict = Depends(so_professor), db: Session = Depends(get_db)):
    _aula_do_professor(db, aula_id, u["id"])
    rows = db.execute(
        select(Aluno.nome, Aluno.email, Curso.nome.label("curso"), Presenca.registrado_em, Presenca.distancia_m)
        .select_from(Presenca).join(Aluno, Aluno.id == Presenca.aluno_id)
        .outerjoin(Curso, Curso.id == Aluno.curso_id)
        .where(Presenca.aula_id == aula_id).order_by(Aluno.nome)).all()
    fuso = timezone(timedelta(hours=FUSO_HORAS))
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_ALL)
    w.writerow(["Nome do Aluno", "E-mail", "Curso", "Horário do Check-in", "Distância (m)"])
    for r in rows:
        w.writerow([_seguro(r.nome), _seguro(r.email), _seguro(r.curso),
                    aware(r.registrado_em).astimezone(fuso).strftime("%d/%m/%Y %H:%M:%S"),
                    f"{r.distancia_m:.1f}".replace(".", ",")])
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="chamada-{aula_id}.csv"'})


@app.get("/api/meu-ip")
def meu_ip(request: Request, u: dict = Depends(so_professor)):
    """Diagnóstico: mostra qual IP o servidor enxerga (para calibrar PROXY_HOPS antes de bloquear VPN)."""
    return {"ip_detectado": ip_cliente(request), "x_forwarded_for": request.headers.get("x-forwarded-for"),
            "proxy_hops": PROXY_HOPS, "vpn_modo": VPN_MODO, "verificacao_ativa": bool(PROXYCHECK_KEY),
            "ip_e_vpn": ip_e_vpn(ip_cliente(request))}


# ------------------------------ Aluno ---------------------------------
@app.get("/api/aulas/ativas")
def aulas_ativas(u: dict = Depends(so_aluno), db: Session = Depends(get_db)):
    ativas = _aulas_ativas(db)
    ids = [a["id"] for a in ativas]
    ja = set(db.scalars(select(Presenca.aula_id).where(Presenca.aluno_id == u["id"],
                                                       Presenca.aula_id.in_(ids)))) if ids else set()
    # coordenadas da sala não vão para o aluno (dificulta falsificar a posição)
    return [{"id": a["id"], "titulo": a["titulo"], "professor": a["professor"],
             "raio_metros": a["raio_metros"], "aberta_em": a["aberta_em"],
             "ja_registrou": a["id"] in ja} for a in ativas]


@app.post("/api/aulas/{aula_id}/presenca")
def registrar_presenca(aula_id: int, b: PresencaIn, request: Request,
                       u: dict = Depends(so_aluno), db: Session = Depends(get_db)):
    aula = db.get(Aula, aula_id)
    if not aula:
        raise HTTPException(404, "Aula não encontrada.")
    if not aula.ativa:
        raise HTTPException(409, "Esta chamada já foi encerrada.")

    # 1) qualidade e coerência da leitura de GPS (barato, sem banco)
    if b.precisao_m is not None and b.precisao_m > PRECISAO_MAX_M:
        raise HTTPException(422, f"Sinal de GPS impreciso (±{round(b.precisao_m)} m). "
                                 "Ative o GPS, vá para um local aberto e tente de novo.")
    if abs(time.time() * 1000 - b.pos_ts) > IDADE_MAX_POSICAO_S * 1000:
        raise HTTPException(422, "Leitura de localização desatualizada. Deixe data e hora do aparelho em "
                                 "modo automático e tente novamente.")
    risco, alertas = avaliar_risco(b)
    if risco >= RISCO_MAX:
        raise HTTPException(403, "Sua localização parece falsificada ou inconsistente. Desative apps de "
                                 "localização falsa e use o GPS real do aparelho.")

    # 2) geofencing (Haversine)
    dist = haversine(b.latitude, b.longitude, aula.latitude, aula.longitude)
    if dist > aula.raio_metros:
        raise HTTPException(403, f"Você está a {round(dist)} m da sala; o limite desta aula é "
                                 f"{round(aula.raio_metros)} m. Aproxime-se e tente novamente.")

    # 3) deslocamento impossível em relação ao último check-in do aluno
    ult = db.execute(select(Presenca.latitude, Presenca.longitude, Presenca.registrado_em)
                     .where(Presenca.aluno_id == u["id"]).order_by(Presenca.id.desc()).limit(1)).first()
    if ult:
        dt = max((utcnow() - aware(ult.registrado_em)).total_seconds(), 1)
        d = haversine(b.latitude, b.longitude, ult.latitude, ult.longitude)
        if d > 2000 and dt < 6 * 3600 and d / dt > VELOCIDADE_MAX_MS:
            raise HTTPException(403, "Deslocamento incompatível com seu último registro de presença. "
                                     "Se for um engano, procure o professor.")

    # 4) VPN / proxy (consulta externa, só para quem passou nas etapas anteriores; resultado em cache)
    ip = ip_cliente(request)
    if VPN_MODO != "off" and ip_e_vpn(ip) is True:
        if VPN_MODO == "bloquear":
            raise HTTPException(403, "Conexão via VPN ou proxy detectada. Desative a VPN e use o Wi-Fi da "
                                     "instituição ou os dados móveis.")
        alertas.append("IP de VPN/proxy")

    db.add(Presenca(aula_id=aula_id, aluno_id=u["id"], latitude=b.latitude, longitude=b.longitude,
                    distancia_m=dist, precisao_m=b.precisao_m, device_id=b.device_id, fp_hash=b.fp_hash,
                    ip=ip[:64] or None, alertas="; ".join(alertas)[:200] or None))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # duplicata do próprio aluno (duplo clique) -> idempotente; senão, o aparelho já foi usado por outro aluno
        if db.scalar(select(Presenca.id).where(Presenca.aula_id == aula_id, Presenca.aluno_id == u["id"])):
            return {"ok": True, "ja_registrado": True, "distancia_m": round(dist, 1), "aula": aula.titulo}
        raise HTTPException(409, "Este aparelho já foi usado para registrar a presença de outro aluno "
                                 "nesta aula. Cada aluno deve usar o próprio celular.")
    return {"ok": True, "ja_registrado": False, "distancia_m": round(dist, 1), "aula": aula.titulo}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")),
                reload=os.getenv("RELOAD", "1") == "1")
