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
import unicodedata
import urllib.request
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Literal, Optional

import jwt
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import (Boolean, DateTime, Float, ForeignKey, Index, Integer, String,
                        UniqueConstraint, create_engine, delete, event, func, inspect,
                        exists, or_, select, text, update)
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

# Matrícula (Pasta 1): formato numérico + lista pré-aprovada (tabela MatriculasAutorizadas)
# Matrícula: letras e números (ex.: 202610405 ou GTI202610405), de 4 a 30 caracteres, sem espaços. Guardada em MAIÚSCULAS.
MATRICULA_PADRAO = r"^[A-Za-z0-9][A-Za-z0-9._/-]{3,29}$"
try:
    MATRICULA_RE = re.compile(os.getenv("MATRICULA_REGEX", MATRICULA_PADRAO))
except re.error:
    MATRICULA_RE = re.compile(MATRICULA_PADRAO)
# Conferência da lista de matrículas autorizadas. DESLIGADA por padrão por enquanto (qualquer matrícula numérica
# válida pode se cadastrar). Para ligar de novo: MATRICULA_OBRIGATORIA=1 (importe a lista na coordenação antes).
MATRICULA_OBRIGATORIA = os.getenv("MATRICULA_OBRIGATORIA", "0").strip().lower() in ("1", "true", "sim", "on")
# Limpeza ÚNICA dos dados de teste na inicialização. Desligada por padrão: só age se esta variável estiver preenchida
# e ainda não tiver rodado com esse mesmo valor. Para repetir, troque o valor.
RESET_BANCO = os.getenv("RESET_BANCO", "").strip()
REGISTRO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]{2,29}$")  # Registro Funcional / ID Docente (ex.: DOC-2026-08)

# Coordenação (área administrativa escondida): só contas de PROFESSOR cujo e-mail esteja em ADMIN_EMAILS.
ADMIN_EMAILS = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}
ADMIN_PATH = re.sub(r"[^A-Za-z0-9_\-]", "", os.getenv("ADMIN_PATH", "coordenacao")) or "coordenacao"
ADMIN_TOKEN_HORAS = int(os.getenv("ADMIN_TOKEN_HORAS", "4"))

# Conta PROVISÓRIA da coordenação: criada na inicialização SÓ se ainda não existir nenhuma conta da coordenação.
# Serve para o primeiro acesso; a senha deve ser trocada logo na tela Resumo > "Trocar minha senha".
# Para não deixar a senha no GitHub, defina COORD_SENHA na Render. COORD_PROVISORIA=0 desliga tudo isto.
COORD_PROVISORIA = os.getenv("COORD_PROVISORIA", "1").strip().lower() not in ("0", "false", "nao", "não", "off")
COORD_EMAIL = os.getenv("COORD_EMAIL", "coordenacao355@unicerrado.edu.br").strip().lower()
COORD_SENHA = os.getenv("COORD_SENHA", "coordenacao4558")

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
                 "Medicina", "Pedagogia", "Psicologia"]  # ajuste com CURSOS_EXTRAS="Curso A,Curso B"

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
    departamento: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)


class Aluno(Base):
    __tablename__ = "alunos"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    matricula: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    # nullable no banco só para aceitar contas criadas na V1; a API exige o curso no cadastro
    curso_id: Mapped[Optional[int]] = mapped_column(ForeignKey("cursos.id"), nullable=True, index=True)
    periodo: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)                 # 1 a 10
    status: Mapped[Optional[str]] = mapped_column(String(10), default="ativo")             # ativo | inativo (interno)
    senha_hash: Mapped[str] = mapped_column(String(200))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Professor(Base):
    __tablename__ = "professores"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ativo: Mapped[Optional[bool]] = mapped_column(Boolean, default=True)  # a coordenação pode desativar
    coordenacao: Mapped[Optional[bool]] = mapped_column(Boolean, default=False)  # conta da coordenação (painel administrativo)
    registro_funcional: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)  # ex.: DOC-2026-08
    departamento: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
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
    tipo: Mapped[str] = mapped_column(String(20), default="aula")            # aula | palestra
    local_tipo: Mapped[str] = mapped_column(String(10), default="dentro")    # dentro | fora da faculdade
    local_descricao: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    curso_id: Mapped[Optional[int]] = mapped_column(ForeignKey("cursos.id"), nullable=True)  # None = todos os cursos
    periodo: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)                   # None = todos os períodos
    disciplina: Mapped[Optional[str]] = mapped_column(String(140), nullable=True)
    codigo_validacao: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)       # impresso no PDF do aluno
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
    aula_id: Mapped[int] = mapped_column(ForeignKey("aulas.id", ondelete="CASCADE"))
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


class Tentativa(Base):
    """
    Check-ins RECUSADOS (fora do raio, VPN, GPS suspeito, aparelho repetido, deslocamento impossível).
    Uma linha por aluno/aula/motivo: repetições só aumentam o contador, então a tabela não incha.
    """
    __tablename__ = "tentativas"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    aula_id: Mapped[int] = mapped_column(ForeignKey("aulas.id", ondelete="CASCADE"))
    aluno_id: Mapped[int] = mapped_column(ForeignKey("alunos.id"), index=True)
    motivo: Mapped[str] = mapped_column(String(20))
    detalhe: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    distancia_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    precisao_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    quantidade: Mapped[int] = mapped_column(Integer, default=1)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ultima_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint("aula_id", "aluno_id", "motivo", name="uq_tentativa"),
        Index("ix_tentativa_aula_ultima", "aula_id", "ultima_em"),
    )


class MatriculasAutorizadas(Base):
    """Lista pré-aprovada pela coordenação. O aluno só se cadastra com uma matrícula daqui e ainda não utilizada."""
    __tablename__ = "matriculas_autorizadas"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    matricula: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    curso_id: Mapped[Optional[int]] = mapped_column(ForeignKey("cursos.id"), nullable=True)
    utilizada: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class LogSeguranca(Base):
    """Trilha de auditoria: exportações de dados, exclusões em massa, ações da coordenação, logins de admin."""
    __tablename__ = "logs_seguranca"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    acao: Mapped[str] = mapped_column(String(40))
    detalhe: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    data_hora: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class ControleSistema(Base):
    """Marcas internas do sistema (ex.: a limpeza RESET_BANCO já executada). Nunca é apagada pela limpeza."""
    __tablename__ = "controle_sistema"
    chave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valor: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)


def get_db():
    with SessionLocal() as db:
        yield db


def migrar_esquema():
    """create_all não altera tabelas já existentes: adiciona as colunas novas da V2 em bancos da V1."""
    novas = {
        "cursos": [("departamento", "VARCHAR(100)")],
        "alunos": [("curso_id", "INTEGER REFERENCES cursos (id)"), ("periodo", "INTEGER"),
                   ("status", "VARCHAR(10) DEFAULT 'ativo'")],
        "professores": [("ativo", "BOOLEAN DEFAULT TRUE"), ("coordenacao", "BOOLEAN DEFAULT FALSE"),
                        ("registro_funcional", "VARCHAR(30)"),
                        ("departamento", "VARCHAR(100)")],
        "aulas": [("tipo", "VARCHAR(20) DEFAULT 'aula'"), ("local_tipo", "VARCHAR(10) DEFAULT 'dentro'"),
                  ("local_descricao", "VARCHAR(160)"), ("curso_id", "INTEGER REFERENCES cursos (id)"),
                  ("periodo", "INTEGER"), ("disciplina", "VARCHAR(140)"),
                  ("codigo_validacao", "VARCHAR(16)")],
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
                    if tabela == "alunos" and nome == "curso_id":
                        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_alunos_curso_id ON alunos (curso_id)"))
            except Exception as exc:  # outra instância pode ter feito ao mesmo tempo
                print(f"Migração {tabela}.{nome}:", exc)


def resetar_dados_de_teste():
    """
    Apaga alunos, professores, aulas, presenças, tentativas, matrículas autorizadas e a auditoria. Mantém os cursos.
    Roda no máximo UMA vez por valor de RESET_BANCO (o valor fica registrado, em forma de hash, em controle_sistema),
    então pode deixar a variável cadastrada: reiniciar o servidor não apaga de novo.
    """
    if not RESET_BANCO:
        return
    marca = "token:" + hashlib.sha256(RESET_BANCO.encode()).hexdigest()[:16]
    with SessionLocal() as db:
        if engine.dialect.name == "postgresql":  # vários workers subindo juntos: só um faz a limpeza
            db.execute(text("SELECT pg_advisory_xact_lock(727274)"))
        atual = db.get(ControleSistema, "reset_banco")
        if atual and atual.valor == marca:
            print("RESET_BANCO: já executado com este valor; nada a fazer.")
            return
        for modelo in (Tentativa, Presenca, Aula, Aluno, Professor, MatriculasAutorizadas, LogSeguranca):
            db.execute(delete(modelo))  # ordem respeita as chaves estrangeiras
        if atual:
            atual.valor = marca
        else:
            db.add(ControleSistema(chave="reset_banco", valor=marca))
        db.add(LogSeguranca(acao="reset_banco", detalhe="limpeza dos dados de teste na inicialização", ip="inicializacao"))
        db.commit()
    print("RESET_BANCO: dados de teste apagados (cursos mantidos).")


def semear_coordenacao_provisoria():
    """
    Primeiro acesso garantido: se NÃO existe nenhuma conta da coordenação (marcada no cadastro ou listada em
    ADMIN_EMAILS), cria a conta provisória COORD_EMAIL / COORD_SENHA. Nunca sobrescreve nada: se a conta já existe
    (inclusive com a senha já trocada), não faz nada. Se outra conta da coordenação já existe, também não cria.
    """
    if not (COORD_PROVISORIA and COORD_EMAIL and COORD_SENHA):
        return
    with SessionLocal() as db:
        if engine.dialect.name == "postgresql":  # vários workers subindo juntos: só um cria
            db.execute(text("SELECT pg_advisory_xact_lock(727275)"))
        cond = [Professor.coordenacao.is_(True)]
        if ADMIN_EMAILS:
            cond.append(Professor.email.in_(sorted(ADMIN_EMAILS)))
        if _contar(db, Professor, or_(*cond)) > 0:
            return
        if db.scalar(select(Professor.id).where(Professor.email == COORD_EMAIL)):
            print("Coordenação provisória: o e-mail", COORD_EMAIL, "já é uma conta de professor; nada foi criado.")
            return
        p = Professor(nome="Coordenação (conta provisória)", email=COORD_EMAIL, senha_hash=hash_senha(COORD_SENHA),
                      departamento="Coordenação", coordenacao=True, ativo=True)
        db.add(p)
        try:
            db.flush()
            log_seg(db, p.id, "criar_coordenacao", COORD_EMAIL + " (provisória)", "inicializacao")
            db.commit()
        except IntegrityError:
            db.rollback()
            return
    print("Coordenação provisória criada:", COORD_EMAIL, "(troque a senha no primeiro acesso)")


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


def normalizar_matricula(m: Optional[str]) -> str:
    """Tira espaços e põe em maiúsculas, para 'gti 2026' e 'GTI2026' serem a mesma matrícula."""
    return re.sub(r"\s+", "", m or "").upper()


def matricula_valida(m: Optional[str]) -> bool:
    return bool(MATRICULA_RE.fullmatch(normalizar_matricula(m)))


def log_seg(db: Session, usuario_id: Optional[int], acao: str, detalhe: str = "", ip: Optional[str] = None,
            commit: bool = False):
    """Grava uma linha em LogSeguranca. Com commit=True nunca derruba a requisição se o log falhar."""
    try:
        db.add(LogSeguranca(usuario_id=usuario_id, acao=acao[:40], detalhe=(detalhe or "")[:300] or None,
                            ip=(ip or "")[:64] or None))
        if commit:
            db.commit()
    except Exception:
        if commit:
            db.rollback()


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


def sem_acentos(s: str) -> str:
    """'coordenação355' -> 'coordenacao355' (o e-mail é guardado sem acento)."""
    return "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c))


def _forma_simples(s: str) -> str:
    """Sem acento, sem espaço, minúscula: 'Coordenação 4558' e 'coordenacao4558' ficam iguais."""
    return re.sub(r"\s+", "", sem_acentos(s)).lower()


def senha_provisoria_ativa(p: "Professor") -> bool:
    """True enquanto a conta provisória da coordenação ainda usa a senha provisória (ou seja, ainda não foi trocada)."""
    return bool(COORD_PROVISORIA and COORD_SENHA and p.email == COORD_EMAIL and confere_senha(COORD_SENHA, p.senha_hash))


def senha_admin_confere(p: "Professor", senha: str) -> bool:
    """Senha da coordenação. Na conta provisória, e só enquanto a senha não foi trocada, aceita variações
    de acento, espaço e maiúscula ('Coordenação 4558'); depois da troca vale só a senha exata."""
    if confere_senha(senha, p.senha_hash):
        return True
    return senha_provisoria_ativa(p) and _forma_simples(senha) == _forma_simples(COORD_SENHA)


def resposta_token(u, perfil: str, curso: Optional[str] = None) -> dict:
    claims = {"sub": str(u.id), "role": perfil, "nome": u.nome, "exp": utcnow() + timedelta(hours=TOKEN_HORAS)}
    if perfil == "aluno":
        claims["cid"] = u.curso_id
        claims["per"] = u.periodo
    token = jwt.encode(claims, SECRET_KEY, algorithm="HS256")
    return {"token": token, "usuario": {"id": u.id, "nome": u.nome, "email": u.email, "perfil": perfil,
                                        "curso": curso, "periodo": getattr(u, "periodo", None),
                                        "matricula": getattr(u, "matricula", None)}}


def usuario_atual(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Faça login para continuar.")
    try:
        p = jwt.decode(authorization[7:], SECRET_KEY, algorithms=["HS256"])
        return {"id": int(p["sub"]), "role": p["role"], "nome": p.get("nome", ""),
                "cid": p.get("cid"), "tem_cid": "cid" in p,
                "per": p.get("per"), "tem_per": "per" in p}
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


def _prof_ou_admin(u: dict = Depends(usuario_atual)) -> dict:
    """RBAC da exportação: só PROFESSOR ou ADMIN (coordenação)."""
    if u["role"] not in ("professor", "admin"):
        raise HTTPException(403, "Acesso negado.")
    return u


# =====================================================================
# SCHEMAS
# =====================================================================
class RegistroIn(BaseModel):
    perfil: Literal["aluno", "professor"]
    nome: str = Field(min_length=3, max_length=120)
    email: str = Field(max_length=160)
    senha: str = Field(min_length=8, max_length=128)
    confirmar_senha: Optional[str] = Field(None, max_length=128)
    matricula: Optional[str] = Field(None, max_length=30)
    curso_id: Optional[int] = None
    periodo: Optional[int] = Field(None, ge=1, le=10)
    registro_funcional: Optional[str] = Field(None, max_length=30)
    departamento: Optional[str] = Field(None, max_length=100)
    chave_master: Optional[str] = Field(None, max_length=100)  # Chave de Acesso da Coordenação


class LoginIn(BaseModel):
    perfil: Literal["aluno", "professor"]
    email: str = Field(max_length=160)
    senha: str = Field(max_length=128)


class AulaIn(BaseModel):
    titulo: str = Field(min_length=2, max_length=140)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    raio_metros: float = Field(ge=5, le=5000)
    tipo: Literal["aula", "palestra"] = "aula"
    local_tipo: Literal["dentro", "fora"] = "dentro"
    local_descricao: Optional[str] = Field(None, max_length=160)
    curso_id: Optional[int] = None  # None = aberto a todos os cursos
    periodo: Optional[int] = Field(None, ge=1, le=10)  # None = todos os períodos
    disciplina: Optional[str] = Field(None, max_length=140)
    codigo_validacao: Optional[str] = Field(None, max_length=32)  # em branco = o sistema gera um


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


def aula_dict(a: Aula, total: Optional[int] = None, curso: Optional[str] = None, com_codigo: bool = False) -> dict:
    d = {"id": a.id, "titulo": a.titulo, "latitude": a.latitude, "longitude": a.longitude,
         "raio_metros": a.raio_metros, "ativa": a.ativa, "tipo": a.tipo or "aula",
         "local_tipo": a.local_tipo or "dentro", "local_descricao": a.local_descricao,
         "curso_id": a.curso_id, "curso": curso, "periodo": a.periodo, "disciplina": a.disciplina,
         "aberta_em": iso(a.aberta_em), "encerrada_em": iso(a.encerrada_em)}
    if total is not None:
        d["total"] = total
    if com_codigo:  # só para o professor dono do evento: o aluno só vê o código dentro do PDF da própria presença
        d["codigo_validacao"] = a.codigo_validacao
    return d


# ---------------------------------------------------------------------
# Código de validação do evento + comprovante em PDF
# ---------------------------------------------------------------------
CODIGO_RE = re.compile(r"^[A-Z0-9]{3,12}$")
BRT = timezone(timedelta(hours=-3))  # o Brasil não tem horário de verão desde 2019


def normalizar_codigo(s: Optional[str]) -> Optional[str]:
    """' 08 1 ' -> '081'; 'abc12' -> 'ABC12'; vazio -> None; fora do padrão (3 a 12 letras/números) -> ValueError."""
    c = re.sub(r"\s+", "", sem_acentos(s or "")).upper()
    if not c:
        return None
    if not CODIGO_RE.match(c):
        raise ValueError(c)
    return c


def gerar_codigo() -> str:
    return f"{secrets.randbelow(10000):04d}"


def assinatura_presenca(p: "Presenca", a: "Aula") -> str:
    """Autenticação do comprovante: HMAC (chave do servidor) de presença, aluno, evento, hora e código do evento.
    Quem falsifica um PDF não consegue gerar esta parte, e o professor confere no painel."""
    base = f"{p.id}|{p.aluno_id}|{a.id}|{int(aware(p.registrado_em).timestamp())}|{a.codigo_validacao or ''}"
    return hmac.new(SECRET_KEY.encode(), base.encode(), hashlib.sha256).hexdigest()[:10].upper()


def _l1(s) -> str:
    """Texto seguro para as fontes padrão do PDF (cp1252): o que não couber vira '?'."""
    return str("" if s is None else s).encode("cp1252", "replace").decode("cp1252")


def gerar_pdf_comprovante(d: dict) -> bytes:
    """Comprovante de presença em PDF, uma página A4. O reportlab é importado aqui para o app subir mesmo sem ele."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfgen import canvas

    W, H = A4
    M = 48
    verde, cinza, escuro = colors.HexColor("#1d6b47"), colors.HexColor("#5b6670"), colors.HexColor("#14202b")
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(_l1(f"Comprovante de presença nº {d['protocolo']}"))
    c.setAuthor("GeoCheckIn - UniCerrado")
    c.setSubject("Comprovante de presença")

    c.setFillColor(verde)
    c.rect(0, H - 86, W, 86, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(M, H - 42, "GeoCheckIn")
    c.setFont("Helvetica", 12)
    c.drawString(M, H - 62, _l1("Comprovante de presença · Centro Universitário de Goiatuba (UniCerrado)"))

    y = H - 124
    c.setFillColor(verde)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(M, y, _l1("PRESENÇA REGISTRADA"))
    y -= 30

    def campo(rotulo: str, valor: str):
        nonlocal y
        c.setFillColor(cinza)
        c.setFont("Helvetica", 8.5)
        c.drawString(M, y, _l1(rotulo.upper()))
        y -= 14
        c.setFillColor(escuro)
        c.setFont("Helvetica-Bold", 12)
        for lin in simpleSplit(_l1(valor), "Helvetica-Bold", 12, W - 2 * M):
            c.drawString(M, y, lin)
            y -= 15
        y -= 7

    campo("Aluno", d["nome"])
    campo("Matrícula", d["matricula"] or "—")
    campo("Curso e período", f"{d['curso'] or '—'}" + (f" · {d['periodo']}º período" if d.get("periodo") else ""))
    campo("Evento", f"{'Palestra' if d['tipo'] == 'palestra' else 'Aula'} · {d['titulo']}"
                    + (f" (disciplina {d['disciplina']})" if d.get("disciplina") else ""))
    campo("Local", ("Fora da faculdade" if d["local_tipo"] == "fora" else "Dentro da faculdade")
                   + (f" · {d['local_descricao']}" if d.get("local_descricao") else ""))
    campo("Professor(a)", d["professor"])
    campo("Data e hora do registro (horário de Brasília)", d["registrado_em"].astimezone(BRT).strftime("%d/%m/%Y às %H:%M:%S"))
    campo("Protocolo nº", str(d["protocolo"]))

    y -= 2
    h = 104
    c.setStrokeColor(verde)
    c.setLineWidth(2.2)
    c.setFillColor(colors.HexColor("#eef7f1"))
    c.roundRect(M, y - h, W - 2 * M, h, 10, stroke=1, fill=1)
    c.setFillColor(cinza)
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(W / 2, y - 25, _l1("CÓDIGO DE VALIDAÇÃO DA AULA"))
    c.setFillColor(verde)
    c.setFont("Courier-Bold", 50)
    c.drawCentredString(W / 2, y - 78, _l1(d["codigo"]))
    y -= h + 24

    c.setFillColor(cinza)
    c.setFont("Helvetica", 8.5)
    c.drawString(M, y, _l1("AUTENTICAÇÃO DO COMPROVANTE"))
    y -= 17
    c.setFillColor(escuro)
    c.setFont("Courier-Bold", 14)
    c.drawString(M, y, _l1(d["autenticacao"]))
    y -= 28

    quando = d["registrado_em"].astimezone(BRT)
    frase = (f"Eu, {d['nome']}, matrícula {d['matricula'] or '—'}"
             + (f", do curso de {d['curso']}" if d.get("curso") else "")
             + f", registrei presença no evento \"{d['titulo']}\""
             + (f", disciplina {d['disciplina']}" if d.get("disciplina") else "")
             + f", em {quando.strftime('%d/%m/%Y')} às {quando.strftime('%H:%M')}.")
    linhas = simpleSplit(_l1(frase), "Helvetica", 10.5, W - 2 * M - 16)
    alt = len(linhas) * 14 + 12
    c.setFillColor(colors.HexColor("#f3f6f4"))
    c.rect(M, y - alt + 12, W - 2 * M, alt, stroke=0, fill=1)
    c.setFillColor(verde)
    c.rect(M, y - alt + 12, 3.5, alt, stroke=0, fill=1)
    c.setFillColor(escuro)
    c.setFont("Helvetica", 10.5)
    yy = y - 4
    for lin in linhas:
        c.drawString(M + 14, yy, lin)
        yy -= 14

    c.setStrokeColor(colors.HexColor("#cfd8d3"))
    c.setLineWidth(0.8)
    c.line(M, 74, W - M, 74)
    c.setFillColor(cinza)
    c.setFont("Helvetica", 8)
    rodape = (f"Como conferir: o código de validação acima deve ser igual ao definido pelo professor no cadastro do evento, "
              f"e a autenticação pode ser conferida no painel do professor (Conferir comprovante). "
              f"Documento gerado em {d['emitido_em'].astimezone(BRT).strftime('%d/%m/%Y %H:%M')} (horário de Brasília). "
              f"Este comprovante não contém CPF nem coordenadas de localização.")
    yy = 62
    for lin in simpleSplit(_l1(rodape), "Helvetica", 8, W - 2 * M):
        c.drawString(M, yy, lin)
        yy -= 10
    c.showPage()
    c.save()
    return buf.getvalue()


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
    try:
        resetar_dados_de_teste()
    except Exception as exc:
        print("RESET_BANCO falhou (nada foi apagado):", exc)
    try:
        semear_coordenacao_provisoria()  # depois da limpeza, para a conta nova não ser apagada
    except Exception as exc:
        print("Coordenação provisória não foi criada:", exc)
    yield


# /docs e /openapi.json desligados: não expõem o mapa da API (nem das rotas da coordenação)
app = FastAPI(title="GeoCheckIn V2 - UniCerrado", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)


def _arquivo_index() -> Path:
    for p in (BASE_DIR / "web" / "index.html", BASE_DIR / "index.html"):
        if p.exists():
            return p
    raise HTTPException(404, "index.html não encontrado no repositório")


@app.get("/")
def index():
    return FileResponse(_arquivo_index())


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/api/config")
def config(db: Session = Depends(get_db)):
    sem_coordenacao = not ADMIN_EMAILS and not _contar(db, Professor, Professor.coordenacao.is_(True))
    return {"dominio_aluno": DOMINIO_ALUNOS, "dominios_professor": DOMINIOS_PROFESSORES,
            "matricula_regex": MATRICULA_RE.pattern, "coordenacao_aberta": bool(sem_coordenacao)}


@app.get("/api/cursos")
def listar_cursos(db: Session = Depends(get_db)):
    rows = db.execute(select(Curso.id, Curso.nome).where(Curso.ativo.is_(True)).order_by(Curso.nome)).all()
    return [{"id": r.id, "nome": r.nome} for r in rows]


# ------------------------------ Auth ---------------------------------
@app.post("/api/auth/registro")
def registro(b: RegistroIn, request: Request, db: Session = Depends(get_db)):
    if b.confirmar_senha is not None and b.confirmar_senha != b.senha:
        raise HTTPException(422, "As senhas não conferem. Digite a mesma senha nos dois campos.")
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
            raise HTTPException(403, "Chave de Acesso da Coordenação inválida. Solicite à coordenação.")
        registro = (b.registro_funcional or "").strip()
        depto = (b.departamento or "").strip()
        if not REGISTRO_RE.fullmatch(registro):
            raise HTTPException(422, "Informe o Registro Funcional / ID Docente (ex.: DOC-2026-08).")
        if len(depto) < 2:
            raise HTTPException(422, "Informe o seu departamento ou área.")
        if db.scalar(select(Professor.id).where(Professor.registro_funcional == registro)):
            raise HTTPException(409, "Este registro funcional já está cadastrado. Procure a coordenação.")
    else:
        curso = db.get(Curso, b.curso_id) if b.curso_id else None
        if not curso or not curso.ativo:
            raise HTTPException(422, "Selecione o seu curso.")
        if not b.periodo:
            raise HTTPException(422, "Selecione o seu período (1º ao 10º).")
        mat = normalizar_matricula(b.matricula)
        if not matricula_valida(mat):
            raise HTTPException(422, "Matrícula inválida. Use letras e números, sem espaços (ex.: 202610405 ou GTI202610405).")
        dono = db.scalar(select(Aluno.email).where(Aluno.matricula == mat))
        if dono and dono != email:
            raise HTTPException(409, "Esta matrícula já está vinculada a outro e-mail. Procure a coordenação.")
        if MATRICULA_OBRIGATORIA:
            aut = db.scalar(select(MatriculasAutorizadas).where(MatriculasAutorizadas.matricula == mat))
            if not aut:
                raise HTTPException(403, "Matrícula não encontrada na lista autorizada. Confira o número "
                                         "ou procure a coordenação.")
            if aut.utilizada and dono != email:
                raise HTTPException(409, "Esta matrícula já está vinculada a outro e-mail. Procure a coordenação.")
            if aut.curso_id is not None and aut.curso_id != curso.id:
                raise HTTPException(422, "Esta matrícula pertence a outro curso. Confira o curso selecionado.")

    Model = Aluno if b.perfil == "aluno" else Professor
    if db.scalar(select(Model.id).where(Model.email == email)):
        raise HTTPException(409, "Este e-mail já possui cadastro. Use a opção Entrar.")
    u = Model(nome=b.nome.strip(), email=email, senha_hash=hash_senha(b.senha))
    if b.perfil == "aluno":
        u.matricula, u.curso_id, u.periodo, u.status = mat, curso.id, b.periodo, "ativo"
        if MATRICULA_OBRIGATORIA:
            # reserva atômica: se dois cadastros disputam a mesma matrícula, só um UPDATE acerta a linha
            r = db.execute(update(MatriculasAutorizadas)
                           .where(MatriculasAutorizadas.matricula == mat, MatriculasAutorizadas.utilizada.is_(False))
                           .values(utilizada=True))
            if r.rowcount != 1:
                db.rollback()
                raise HTTPException(409, "Esta matrícula já está vinculada a outro e-mail. Procure a coordenação.")
    else:
        u.registro_funcional, u.departamento = registro, depto
    db.add(u)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Este e-mail já possui cadastro. Use a opção Entrar.")
    if b.perfil == "professor":
        log_seg(db, u.id, "cadastro_professor", f"registro={registro}", ip_cliente(request), commit=True)
    return resposta_token(u, b.perfil, curso.nome if curso else None)


class EsqueciIn(BaseModel):
    perfil: Literal["aluno", "professor"]
    email: str = Field(max_length=160)
    referencia: str = Field(max_length=40)  # matrícula (aluno) ou registro funcional (professor)


@app.post("/api/auth/esqueci-senha")
def esqueci_senha(b: EsqueciIn, request: Request, db: Session = Depends(get_db)):
    """
    Pedido de nova senha. O sistema não envia e-mail: o pedido aparece na aba 'Pedidos de senha' da coordenação,
    que define uma senha provisória e avisa a pessoa. A resposta é sempre a mesma, para não revelar quais
    e-mails têm cadastro. Um pedido só é criado se e-mail e matrícula/registro conferem e não há outro pendente.
    """
    email, ref, ip = b.email.strip().lower(), b.referencia.strip(), ip_cliente(request)
    limitar_tentativas("esq-ip:" + ip, 8, 900)
    limitar_tentativas("esq-em:" + email, 3, 900)
    if b.perfil == "aluno":
        u = db.scalar(select(Aluno).where(Aluno.email == email))
        confere = bool(u and u.matricula and u.matricula == normalizar_matricula(ref))
    else:
        u = db.scalar(select(Professor).where(Professor.email == email))
        confere = bool(u and u.registro_funcional and u.registro_funcional.lower() == ref.lower())
    if confere:
        chave = f"{b.perfil}={u.id}"
        ultimo = db.scalar(select(func.max(LogSeguranca.id)).where(
            LogSeguranca.acao == "pedido_senha", LogSeguranca.detalhe.like(chave + ";%")))
        atendido = db.scalar(select(func.max(LogSeguranca.id)).where(
            LogSeguranca.acao == "redefinir_senha", LogSeguranca.detalhe == chave))
        if not ultimo or (atendido or 0) > ultimo:  # já existe um pedido pendente? então não duplica
            log_seg(db, None, "pedido_senha", f"{chave}; {u.nome}; {email}; ref={ref}", ip, commit=True)
    return {"ok": True, "mensagem": "Pedido enviado à coordenação. Ela vai definir uma senha provisória e avisar você. "
                                    "Se os dados estiverem corretos, o pedido aparece lá em instantes."}


@app.post("/api/auth/login")
def login(b: LoginIn, db: Session = Depends(get_db)):
    email = b.email.strip().lower()
    if not email_permitido(email, b.perfil):
        raise HTTPException(403, "E-mail não autorizado para este perfil.")
    Model = Aluno if b.perfil == "aluno" else Professor
    u = db.scalar(select(Model).where(Model.email == email))
    if not u or not confere_senha(b.senha, u.senha_hash):
        raise HTTPException(401, "E-mail ou senha incorretos.")
    if b.perfil == "professor" and u.ativo is False:
        raise HTTPException(403, "Conta desativada. Procure a coordenação.")
    if b.perfil == "aluno" and u.status == "inativo":
        raise HTTPException(403, "Matrícula inativa. Procure a coordenação.")
    curso = db.scalar(select(Curso.nome).where(Curso.id == u.curso_id)) if b.perfil == "aluno" and u.curso_id else None
    return resposta_token(u, b.perfil, curso)


# ---------------------------- Professor -------------------------------
_cache_ativas = {"t": 0.0, "v": []}


def _aulas_ativas(db: Session) -> list:
    if time.monotonic() - _cache_ativas["t"] < 2:
        return _cache_ativas["v"]
    rows = db.execute(select(Aula, Professor.nome, Curso.nome).join(Professor, Professor.id == Aula.professor_id)
                      .outerjoin(Curso, Curso.id == Aula.curso_id)
                      .where(Aula.ativa.is_(True)).order_by(Aula.id.desc()).limit(50)).all()
    _cache_ativas.update(t=time.monotonic(), v=[{**aula_dict(a, curso=cn), "professor": n} for a, n, cn in rows])
    return _cache_ativas["v"]


def _aula_do_professor(db: Session, aula_id: int, prof_id: int) -> Aula:
    a = db.get(Aula, aula_id)
    if not a or a.professor_id != prof_id:
        raise HTTPException(404, "Aula não encontrada.")
    return a


@app.post("/api/aulas")
def abrir_aula(b: AulaIn, u: dict = Depends(so_professor), db: Session = Depends(get_db)):
    curso = None
    if b.curso_id is not None:
        curso = db.get(Curso, b.curso_id)
        if not curso or not curso.ativo:
            raise HTTPException(422, "Curso inválido.")
    try:  # validado antes de encerrar a chamada anterior, para um erro de digitação não fechar a aula aberta
        codigo = normalizar_codigo(b.codigo_validacao)
    except ValueError:
        raise HTTPException(422, "O código de validação deve ter de 3 a 12 letras ou números, sem símbolos (ex.: 081).")
    db.execute(update(Aula).where(Aula.professor_id == u["id"], Aula.ativa.is_(True))
               .values(ativa=False, encerrada_em=utcnow()))
    a = Aula(professor_id=u["id"], titulo=b.titulo.strip(), latitude=b.latitude, longitude=b.longitude,
             raio_metros=b.raio_metros, tipo=b.tipo, local_tipo=b.local_tipo,
             local_descricao=(b.local_descricao or "").strip() or None, curso_id=b.curso_id,
             periodo=b.periodo, disciplina=(b.disciplina or "").strip() or None,
             codigo_validacao=codigo or gerar_codigo())
    db.add(a)
    db.commit()
    _cache_ativas["t"] = 0
    return aula_dict(a, 0, curso.nome if curso else None, com_codigo=True)


@app.post("/api/aulas/{aula_id}/encerrar")
def encerrar_aula(aula_id: int, u: dict = Depends(so_professor), db: Session = Depends(get_db)):
    a = _aula_do_professor(db, aula_id, u["id"])
    if a.ativa:
        a.ativa, a.encerrada_em = False, utcnow()
        db.commit()
        _cache_ativas["t"] = 0
    return aula_dict(a)


@app.delete("/api/aulas/{aula_id}")
def excluir_aula(aula_id: int, request: Request, u: dict = Depends(so_professor), db: Session = Depends(get_db)):
    """Exclui a aula e, em cascata, todas as presenças dela (na mesma transação)."""
    a = _aula_do_professor(db, aula_id, u["id"])
    db.execute(delete(Tentativa).where(Tentativa.aula_id == aula_id))
    removidas = db.execute(delete(Presenca).where(Presenca.aula_id == aula_id)).rowcount
    db.execute(delete(Aula).where(Aula.id == aula_id))
    log_seg(db, u["id"], "excluir_aula", f"aula={aula_id}; titulo={a.titulo}; presencas={removidas}", ip_cliente(request))
    db.commit()
    _cache_ativas["t"] = 0
    return {"ok": True, "presencas_removidas": removidas}


@app.delete("/api/aulas")
def excluir_todas_as_aulas(request: Request, confirmar: str = "", u: dict = Depends(so_professor),
                           db: Session = Depends(get_db)):
    """Exclusão de emergência: apaga TODAS as aulas do professor e, em cascata, as presenças delas."""
    if confirmar != "EXCLUIR":
        raise HTTPException(400, "Confirmação ausente. Envie confirmar=EXCLUIR.")
    ids = select(Aula.id).where(Aula.professor_id == u["id"])
    n_aulas = _contar(db, Aula, Aula.professor_id == u["id"])
    db.execute(delete(Tentativa).where(Tentativa.aula_id.in_(ids)))
    removidas = db.execute(delete(Presenca).where(Presenca.aula_id.in_(ids))).rowcount
    db.execute(delete(Aula).where(Aula.professor_id == u["id"]))
    log_seg(db, u["id"], "excluir_todas_aulas", f"aulas={n_aulas}; presencas={removidas}", ip_cliente(request))
    db.commit()
    _cache_ativas["t"] = 0
    return {"ok": True, "aulas_removidas": n_aulas, "presencas_removidas": removidas}


@app.get("/api/professor/aulas")
def minhas_aulas(u: dict = Depends(so_professor), db: Session = Depends(get_db)):
    rows = db.execute(select(Aula, func.count(Presenca.id), Curso.nome)
                      .outerjoin(Presenca, Presenca.aula_id == Aula.id).outerjoin(Curso, Curso.id == Aula.curso_id)
                      .where(Aula.professor_id == u["id"]).group_by(Aula.id, Curso.nome)
                      .order_by(Aula.id.desc()).limit(30)).all()
    return [aula_dict(a, n, cn, com_codigo=True) for a, n, cn in rows]


@app.get("/api/aulas/{aula_id}/conferir")
def conferir_comprovante(aula_id: int, codigo: str = "", u: dict = Depends(so_professor),
                         db: Session = Depends(get_db)):
    """O professor cola a 'Autenticação do comprovante' que está no PDF do aluno e vê se ela é verdadeira."""
    a = _aula_do_professor(db, aula_id, u["id"])
    limitar_tentativas("conf:" + str(u["id"]), 60, 600)
    m = re.fullmatch(r"\s*(\d{1,12})\s*-\s*([0-9A-Fa-f]{10})\s*", codigo or "")
    if not m:
        raise HTTPException(422, "Formato inválido. Cole a autenticação do PDF, como 1042-9F3A7C21B4.")
    row = db.execute(select(Presenca, Aluno.nome, Aluno.matricula, Curso.nome.label("curso"))
                     .select_from(Presenca).join(Aluno, Aluno.id == Presenca.aluno_id)
                     .outerjoin(Curso, Curso.id == Aluno.curso_id)
                     .where(Presenca.id == int(m.group(1)), Presenca.aula_id == aula_id)).first()
    if not row or not hmac.compare_digest(assinatura_presenca(row[0], a), m.group(2).upper()):
        return {"valido": False}
    return {"valido": True, "nome": row[1], "matricula": row[2], "curso": row[3],
            "registrado_em": iso(row[0].registrado_em), "codigo_validacao": a.codigo_validacao}


@app.get("/api/aulas/{aula_id}/presencas")
def presencas_da_aula(aula_id: int, apos_id: int = 0, u: dict = Depends(so_professor),
                      db: Session = Depends(get_db)):
    a = _aula_do_professor(db, aula_id, u["id"])
    total = db.scalar(select(func.count()).select_from(Presenca).where(Presenca.aula_id == aula_id))
    rows = db.execute(
        select(Presenca.id, Aluno.nome, Aluno.matricula, Aluno.email, Curso.nome.label("curso"), Aluno.periodo,
               Presenca.distancia_m, Presenca.registrado_em, Presenca.alertas)
        .select_from(Presenca).join(Aluno, Aluno.id == Presenca.aluno_id)
        .outerjoin(Curso, Curso.id == Aluno.curso_id)
        .where(Presenca.aula_id == aula_id, Presenca.id > apos_id)
        .order_by(Presenca.id).limit(LIMITE_PRESENCAS_POR_CONSULTA)).all()
    return {"ativa": a.ativa, "total": total, "limite": LIMITE_PRESENCAS_POR_CONSULTA,
            "presencas": [{"id": r.id, "nome": r.nome, "matricula": r.matricula, "email": r.email,
                           "curso": r.curso, "periodo": r.periodo, "distancia_m": round(r.distancia_m, 1),
                           "registrado_em": iso(r.registrado_em), "alertas": r.alertas} for r in rows]}


@app.get("/api/aulas/{aula_id}/tentativas")
def tentativas_da_aula(aula_id: int, u: dict = Depends(so_professor), db: Session = Depends(get_db)):
    """Alunos que TENTARAM registrar e foram recusados (separado de quem conseguiu)."""
    _aula_do_professor(db, aula_id, u["id"])
    registrou = Tentativa.aluno_id.in_(select(Presenca.aluno_id).where(Presenca.aula_id == aula_id))
    rows = db.execute(
        select(Tentativa.id, Aluno.nome, Aluno.matricula, Curso.nome.label("curso"), Tentativa.motivo,
               Tentativa.detalhe, Tentativa.distancia_m, Tentativa.quantidade, Tentativa.ultima_em,
               registrou.label("registrou"))
        .select_from(Tentativa).join(Aluno, Aluno.id == Tentativa.aluno_id)
        .outerjoin(Curso, Curso.id == Aluno.curso_id)
        .where(Tentativa.aula_id == aula_id).order_by(Tentativa.ultima_em.desc()).limit(300)).all()
    return {"itens": [{"id": r.id, "nome": r.nome, "matricula": r.matricula, "curso": r.curso, "motivo": r.motivo,
                       "detalhe": r.detalhe, "distancia_m": round(r.distancia_m, 1) if r.distancia_m is not None else None,
                       "quantidade": r.quantidade, "ultima_em": iso(r.ultima_em), "registrou": bool(r.registrou)}
                      for r in rows]}


def _seguro(v) -> str:
    """Evita injeção de fórmula ao abrir o CSV no Excel."""
    v = str(v or "")
    return "'" + v if v[:1] in ("=", "+", "-", "@") else v


def _cond_ausentes(aula_id: int, curso_id: int, periodo: Optional[int]) -> list:
    """Alunos ATIVOS matriculados no curso (e período, se a chamada tiver um) que não registraram presença."""
    cond = [Aluno.curso_id == curso_id,
            or_(Aluno.status.is_(None), Aluno.status != "inativo"),
            Aluno.id.not_in(select(Presenca.aluno_id).where(Presenca.aula_id == aula_id))]
    if periodo is not None:
        cond.append(Aluno.periodo == periodo)
    return cond


@app.get("/api/aulas/{aula_id}/ausentes")
def ausentes_da_aula(aula_id: int, u: dict = Depends(so_professor), db: Session = Depends(get_db)):
    """Aba 'Ausentes / Faltantes': cruza os alunos do curso/período da chamada com as presenças validadas."""
    a = _aula_do_professor(db, aula_id, u["id"])
    if a.curso_id is None:
        return {"disponivel": False, "total": 0, "itens": [],
                "motivo": "Esta chamada está aberta para todos os cursos. Para ver os ausentes, "
                          "abra a chamada para um curso específico."}
    cond = _cond_ausentes(a.id, a.curso_id, a.periodo)
    total = _contar(db, Aluno, *cond)
    rows = db.execute(select(Aluno.nome, Aluno.matricula, Aluno.email, Aluno.periodo)
                      .where(*cond).order_by(Aluno.nome).limit(500)).all()
    return {"disponivel": True, "total": total, "limite": 500,
            "itens": [{"nome": r.nome, "matricula": r.matricula, "email": r.email, "periodo": r.periodo}
                      for r in rows]}


EXPORT_CABECALHO = ["Matrícula", "Nome do Aluno", "E-mail", "Curso", "Período", "Status",
                    "Data/Hora do Check-in", "Distância (m)"]


def _gerar_csv(aula_id: int, curso_id: Optional[int], periodo: Optional[int]):
    """
    Gerador de CSV em lotes (HTTP streaming): lê o banco em blocos de 500 linhas e envia cada bloco
    assim que fica pronto, então relatórios com milhares de alunos não ocupam memória do servidor.
    Usa uma sessão própria porque o StreamingResponse continua depois que a rota retorna.
    """
    fuso = timezone(timedelta(hours=FUSO_HORAS))

    def pacote(linhas) -> str:
        buf = io.StringIO()
        csv.writer(buf, delimiter=";", quoting=csv.QUOTE_ALL).writerows(linhas)
        return buf.getvalue()

    yield "﻿" + pacote([EXPORT_CABECALHO])
    with SessionLocal() as db:
        presentes = (select(Aluno.matricula, Aluno.nome, Aluno.email, Curso.nome.label("curso"), Aluno.periodo,
                            Presenca.registrado_em, Presenca.distancia_m)
                     .select_from(Presenca).join(Aluno, Aluno.id == Presenca.aluno_id)
                     .outerjoin(Curso, Curso.id == Aluno.curso_id)
                     .where(Presenca.aula_id == aula_id).order_by(Aluno.nome))
        lote = []
        for mat, nome, email, curso, per, quando, dist in db.execute(presentes.execution_options(yield_per=500)):
            lote.append([_seguro(mat), _seguro(nome), _seguro(email), _seguro(curso), f"{per}º" if per else "",
                         "Presente", aware(quando).astimezone(fuso).strftime("%d/%m/%Y %H:%M:%S"),
                         f"{dist:.1f}".replace(".", ",")])
            if len(lote) >= 500:
                yield pacote(lote)
                lote = []
        if lote:
            yield pacote(lote)
        if curso_id is not None:  # só dá para listar ausentes quando a chamada é de um curso específico
            ausentes = (select(Aluno.matricula, Aluno.nome, Aluno.email, Curso.nome.label("curso"), Aluno.periodo)
                        .select_from(Aluno).outerjoin(Curso, Curso.id == Aluno.curso_id)
                        .where(*_cond_ausentes(aula_id, curso_id, periodo)).order_by(Aluno.nome))
            lote = []
            for mat, nome, email, curso, per in db.execute(ausentes.execution_options(yield_per=500)):
                lote.append([_seguro(mat), _seguro(nome), _seguro(email), _seguro(curso),
                             f"{per}º" if per else "", "Ausente", "", ""])
                if len(lote) >= 500:
                    yield pacote(lote)
                    lote = []
            if lote:
                yield pacote(lote)


@app.get("/api/v1/export/chamada")
def export_chamada(request: Request, aula_id: int, u: dict = Depends(_prof_ou_admin),
                   db: Session = Depends(get_db)):
    """Relatório da chamada (Presentes + Ausentes) em CSV com streaming. Só PROFESSOR (dono da aula) ou ADMIN."""
    a = db.get(Aula, aula_id)
    if not a or (u["role"] == "professor" and a.professor_id != u["id"]):
        raise HTTPException(404, "Aula não encontrada.")
    log_seg(db, u["id"], "exportar_chamada", f"perfil={u['role']}; aula={aula_id}; titulo={a.titulo}",
            ip_cliente(request), commit=True)
    return StreamingResponse(_gerar_csv(a.id, a.curso_id, a.periodo), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="chamada-{aula_id}.csv"',
                                      "Cache-Control": "no-store"})


@app.get("/api/aulas/{aula_id}/exportar")
def exportar_chamada(aula_id: int, request: Request, u: dict = Depends(so_professor),
                     db: Session = Depends(get_db)):
    """Rota antiga, mantida por compatibilidade: usa o mesmo gerador em lotes."""
    return export_chamada(request, aula_id, u, db)


@app.get("/api/meu-ip")
def meu_ip(request: Request, u: dict = Depends(_prof_ou_admin)):
    """Diagnóstico: mostra qual IP o servidor enxerga (para calibrar PROXY_HOPS antes de bloquear VPN)."""
    return {"ip_detectado": ip_cliente(request), "x_forwarded_for": request.headers.get("x-forwarded-for"),
            "proxy_hops": PROXY_HOPS, "vpn_modo": VPN_MODO, "verificacao_ativa": bool(PROXYCHECK_KEY),
            "ip_e_vpn": ip_e_vpn(ip_cliente(request))}


# ------------------------------ Aluno ---------------------------------
def registrar_tentativa(db: Session, aula_id: int, aluno_id: int, motivo: str, detalhe: Optional[str] = None,
                        b=None, dist: Optional[float] = None, ip: Optional[str] = None):
    """Guarda uma tentativa recusada. Nunca deixa um erro aqui atrapalhar a resposta ao aluno."""
    try:
        agora = utcnow()
        t = db.scalar(select(Tentativa).where(Tentativa.aula_id == aula_id, Tentativa.aluno_id == aluno_id,
                                                Tentativa.motivo == motivo))
        if t:
            t.quantidade += 1
            t.ultima_em, t.detalhe, t.distancia_m = agora, (detalhe or "")[:200] or None, dist
            if b is not None:
                t.latitude, t.longitude, t.precisao_m = b.latitude, b.longitude, b.precisao_m
        else:
            db.add(Tentativa(aula_id=aula_id, aluno_id=aluno_id, motivo=motivo, detalhe=(detalhe or "")[:200] or None,
                             latitude=b.latitude if b is not None else None,
                             longitude=b.longitude if b is not None else None,
                             precisao_m=b.precisao_m if b is not None else None,
                             distancia_m=dist, ip=(ip or "")[:64] or None, ultima_em=agora))
        db.commit()
    except Exception:
        db.rollback()


@app.get("/api/me")
def meu_perfil(u: dict = Depends(so_aluno), db: Session = Depends(get_db)):
    """Dados do aluno para o comprovante (sem CPF: o sistema não coleta esse dado)."""
    r = db.execute(select(Aluno.nome, Aluno.matricula, Aluno.email, Aluno.periodo, Curso.nome.label("curso"))
                   .outerjoin(Curso, Curso.id == Aluno.curso_id).where(Aluno.id == u["id"])).first()
    if not r:
        raise HTTPException(404, "Aluno não encontrado.")
    return {"nome": r.nome, "matricula": r.matricula, "email": r.email, "curso": r.curso, "periodo": r.periodo}


@app.get("/api/aluno/presencas")
def minhas_presencas(u: dict = Depends(so_aluno), db: Session = Depends(get_db)):
    """Comprovantes: as 20 últimas presenças do aluno (continuam visíveis depois que a chamada fecha)."""
    rows = db.execute(
        select(Presenca.id, Presenca.registrado_em, Aula.titulo, Aula.tipo, Aula.local_tipo, Aula.disciplina,
               Aula.periodo, Professor.nome.label("professor"))
        .select_from(Presenca).join(Aula, Aula.id == Presenca.aula_id)
        .join(Professor, Professor.id == Aula.professor_id)
        .where(Presenca.aluno_id == u["id"]).order_by(Presenca.id.desc()).limit(20)).all()
    return [{"id": r.id, "registrado_em": iso(r.registrado_em), "titulo": r.titulo, "tipo": r.tipo or "aula",
             "local_tipo": r.local_tipo or "dentro", "disciplina": r.disciplina, "periodo": r.periodo,
             "professor": r.professor} for r in rows]


@app.get("/api/aluno/presencas/{presenca_id}/pdf")
def comprovante_pdf(presenca_id: int, u: dict = Depends(so_aluno), db: Session = Depends(get_db)):
    """PDF da própria presença, com o código de validação do evento e a autenticação do comprovante."""
    limitar_tentativas("pdf:" + str(u["id"]), 30, 600)
    row = db.execute(
        select(Presenca, Aula, Professor.nome, Aluno.nome, Aluno.matricula, Aluno.periodo, Curso.nome)
        .select_from(Presenca).join(Aula, Aula.id == Presenca.aula_id)
        .join(Professor, Professor.id == Aula.professor_id).join(Aluno, Aluno.id == Presenca.aluno_id)
        .outerjoin(Curso, Curso.id == Aluno.curso_id)
        .where(Presenca.id == presenca_id, Presenca.aluno_id == u["id"])).first()  # só a PRÓPRIA presença
    if not row:
        raise HTTPException(404, "Comprovante não encontrado.")
    p, a, prof, nome, matricula, periodo, curso = row
    if not a.codigo_validacao:  # evento criado antes do código existir: gera uma vez (sem corrida entre alunos)
        db.execute(update(Aula).where(Aula.id == a.id, Aula.codigo_validacao.is_(None))
                   .values(codigo_validacao=gerar_codigo()))
        db.commit()
        db.refresh(a)
    try:
        pdf = gerar_pdf_comprovante({
            "nome": nome, "matricula": matricula, "curso": curso, "periodo": periodo, "titulo": a.titulo,
            "tipo": a.tipo or "aula", "local_tipo": a.local_tipo or "dentro", "local_descricao": a.local_descricao,
            "disciplina": a.disciplina, "professor": prof, "registrado_em": aware(p.registrado_em),
            "protocolo": p.id, "codigo": a.codigo_validacao, "emitido_em": utcnow(),
            "autenticacao": f"{p.id}-{assinatura_presenca(p, a)}"})
    except ImportError:
        raise HTTPException(503, "A geração de PDF não está disponível no servidor. Avise a coordenação.")
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="comprovante-presenca-{p.id}.pdf"', "Cache-Control": "no-store"})


@app.get("/api/aulas/ativas")
def aulas_ativas(u: dict = Depends(so_aluno), db: Session = Depends(get_db)):
    if u.get("tem_cid") and u.get("tem_per"):
        cid, per = u["cid"], u["per"]
    else:  # token de antes da V2: consulta o banco uma vez
        row = db.execute(select(Aluno.curso_id, Aluno.periodo).where(Aluno.id == u["id"])).first()
        cid, per = (row[0], row[1]) if row else (None, None)
    ativas = [a for a in _aulas_ativas(db)
              if a["curso_id"] in (None, cid) and a["periodo"] in (None, per)]
    ids = [a["id"] for a in ativas]
    ja = set(db.scalars(select(Presenca.aula_id).where(Presenca.aluno_id == u["id"],
                                                       Presenca.aula_id.in_(ids)))) if ids else set()
    # latitude/longitude vão para o aluno só para o navegador travar o botão fora da zona; quem manda é o servidor,
    # que recalcula a distância no check-in (HTTP 400 fora do raio). O código de validação NUNCA vai aqui.
    return [{"id": a["id"], "titulo": a["titulo"], "professor": a["professor"], "tipo": a["tipo"],
             "local_tipo": a["local_tipo"], "local_descricao": a["local_descricao"], "curso": a["curso"],
             "periodo": a["periodo"], "disciplina": a["disciplina"],
             "latitude": a["latitude"], "longitude": a["longitude"],
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
    al = db.execute(select(Aluno.curso_id, Aluno.periodo, Aluno.status).where(Aluno.id == u["id"])).first()
    if not al:
        raise HTTPException(404, "Conta de aluno não encontrada. Entre novamente.")
    if al.status == "inativo":
        raise HTTPException(403, "Matrícula inativa. Procure a coordenação.")
    if aula.curso_id is not None and al.curso_id != aula.curso_id:
        nome = db.scalar(select(Curso.nome).where(Curso.id == aula.curso_id))
        raise HTTPException(403, f"Este evento é exclusivo para alunos de {nome}.")
    if aula.periodo is not None and al.periodo != aula.periodo:
        raise HTTPException(403, f"Este evento é exclusivo para alunos do {aula.periodo}º período.")

    # 1) qualidade e coerência da leitura de GPS (barato, sem banco)
    if b.precisao_m is not None and b.precisao_m > PRECISAO_MAX_M:
        raise HTTPException(422, f"Sinal de GPS impreciso (±{round(b.precisao_m)} m). "
                                 "Ative o GPS, vá para um local aberto e tente de novo.")
    if abs(time.time() * 1000 - b.pos_ts) > IDADE_MAX_POSICAO_S * 1000:
        raise HTTPException(422, "Leitura de localização desatualizada. Deixe data e hora do aparelho em "
                                 "modo automático e tente novamente.")
    ip = ip_cliente(request)
    risco, alertas = avaliar_risco(b)
    if risco >= RISCO_MAX:
        registrar_tentativa(db, aula_id, u["id"], "gps_suspeito", "; ".join(alertas), b, None, ip)
        raise HTTPException(403, "Sua localização parece falsificada ou inconsistente. Desative apps de "
                                 "localização falsa e use o GPS real do aparelho.")

    # 2) geofencing (Haversine)
    dist = haversine(b.latitude, b.longitude, aula.latitude, aula.longitude)
    if dist > aula.raio_metros:
        registrar_tentativa(db, aula_id, u["id"], "fora_raio",
                            f"A {round(dist)} m da sala (limite de {round(aula.raio_metros)} m)", b, dist, ip)
        raise HTTPException(400, f"Você está a {round(dist)} m da sala; o limite desta aula é "
                                 f"{round(aula.raio_metros)} m. Aproxime-se e tente novamente.")

    # 3) deslocamento impossível em relação ao último check-in do aluno
    ult = db.execute(select(Presenca.latitude, Presenca.longitude, Presenca.registrado_em)
                     .where(Presenca.aluno_id == u["id"]).order_by(Presenca.id.desc()).limit(1)).first()
    if ult:
        dt = max((utcnow() - aware(ult.registrado_em)).total_seconds(), 1)
        d = haversine(b.latitude, b.longitude, ult.latitude, ult.longitude)
        if d > 2000 and dt < 6 * 3600 and d / dt > VELOCIDADE_MAX_MS:
            registrar_tentativa(db, aula_id, u["id"], "deslocamento",
                                f"{round(d / 1000, 1)} km em {round(dt / 60)} min desde o último check-in", b, dist, ip)
            raise HTTPException(403, "Deslocamento incompatível com seu último registro de presença. "
                                     "Se for um engano, procure o professor.")

    # 4) VPN / proxy (consulta externa, só para quem passou nas etapas anteriores; resultado em cache)
    if VPN_MODO != "off" and ip_e_vpn(ip) is True:
        if VPN_MODO == "bloquear":
            registrar_tentativa(db, aula_id, u["id"], "vpn", "Conexão por VPN ou proxy", b, dist, ip)
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
        outro = db.scalar(select(Aluno.nome).join(Presenca, Presenca.aluno_id == Aluno.id)
                          .where(Presenca.aula_id == aula_id, Presenca.device_id == b.device_id))
        registrar_tentativa(db, aula_id, u["id"], "aparelho_repetido",
                            f"Aparelho já usado por {outro}" if outro else "Aparelho já usado por outro aluno", b, dist, ip)
        raise HTTPException(409, "Este aparelho já foi usado para registrar a presença de outro aluno "
                                 "nesta aula. Cada aluno deve usar o próprio celular.")
    return {"ok": True, "ja_registrado": False, "distancia_m": round(dist, 1), "aula": aula.titulo}


# =====================================================================
# COORDENAÇÃO (área administrativa escondida)
# Acesso = conta de PROFESSOR cujo e-mail está em ADMIN_EMAILS + senha. A URL escondida só evita
# curiosos; a proteção real é este login (com limite de tentativas) e a checagem do papel "admin" em toda rota.
# Ações sensíveis ficam registradas em LogSeguranca (aba "Auditoria").
# =====================================================================
def so_admin(u: dict = Depends(usuario_atual)) -> dict:
    if u["role"] != "admin":
        raise HTTPException(403, "Acesso restrito à coordenação.")
    return u


def eh_coordenacao(p: Professor) -> bool:
    """Conta da coordenação: marcada no cadastro (coordenacao) ou listada em ADMIN_EMAILS (opcional)."""
    return bool(p.coordenacao) or p.email in ADMIN_EMAILS


class AdminLoginIn(BaseModel):
    email: str = Field(max_length=160)
    senha: str = Field(max_length=128)


class CursoIn(BaseModel):
    nome: str = Field(min_length=2, max_length=100)
    departamento: Optional[str] = Field(None, max_length=100)


class AtivoIn(BaseModel):
    ativo: bool


class AlunoEdicaoIn(BaseModel):
    curso_id: Optional[int] = None
    periodo: Optional[int] = Field(None, ge=1, le=10)
    status: Optional[Literal["ativo", "inativo"]] = None


class SenhaIn(BaseModel):
    perfil: Literal["aluno", "professor"]
    id: int
    nova_senha: str = Field(min_length=8, max_length=128)


class ProfessorIn(BaseModel):
    nome: str = Field(min_length=3, max_length=120)
    email: str = Field(max_length=160)
    senha: str = Field(min_length=8, max_length=128)
    registro_funcional: Optional[str] = Field(None, max_length=30)  # obrigatório para professor; opcional para a coordenação
    departamento: Optional[str] = Field(None, max_length=100)
    coordenacao: bool = False


class MatriculasIn(BaseModel):
    texto: str = Field(max_length=1_000_000)  # uma matrícula por linha, ou "matrícula;nome do curso"
    curso_id: Optional[int] = None


def _resposta_admin(p: Professor) -> dict:
    token = jwt.encode({"sub": str(p.id), "role": "admin", "nome": p.nome,
                        "exp": utcnow() + timedelta(hours=ADMIN_TOKEN_HORAS)}, SECRET_KEY, algorithm="HS256")
    return {"token": token, "usuario": {"id": p.id, "nome": p.nome, "email": p.email, "perfil": "admin", "curso": None}}


@app.post("/api/admin/login")
def admin_login(b: AdminLoginIn, request: Request, db: Session = Depends(get_db)):
    email = sem_acentos(b.email.strip().lower())
    ip = ip_cliente(request)
    limitar_tentativas("adm-ip:" + ip, 8, 600)
    limitar_tentativas("adm-em:" + email, 8, 600)
    p = db.scalar(select(Professor).where(Professor.email == email))
    if not p or p.ativo is False or not eh_coordenacao(p) or not senha_admin_confere(p, b.senha):
        log_seg(db, None, "login_admin_falha", email, ip, commit=True)
        raise HTTPException(401, "E-mail ou senha incorretos.")  # mesma resposta para qualquer falha
    log_seg(db, p.id, "login_admin", "", ip, commit=True)
    return _resposta_admin(p)


class AdminRegistroIn(BaseModel):
    nome: str = Field(min_length=3, max_length=120)
    email: str = Field(max_length=160)
    senha: str = Field(min_length=8, max_length=128)
    confirmar_senha: Optional[str] = Field(None, max_length=128)
    chave_master: str = Field(max_length=100)


@app.post("/api/admin/registro")
def admin_registro(b: AdminRegistroIn, request: Request, db: Session = Depends(get_db)):
    """
    Primeiro acesso da coordenação. Só funciona ENQUANTO NÃO EXISTE nenhuma conta da coordenação (e ADMIN_EMAILS
    está vazio) e exige a Chave de Acesso da Coordenação. Depois disso, novas contas da coordenação são criadas
    por quem já é da coordenação, na aba Professores do painel.
    """
    if b.confirmar_senha is not None and b.confirmar_senha != b.senha:
        raise HTTPException(422, "As senhas não conferem. Digite a mesma senha nos dois campos.")
    ip = ip_cliente(request)
    limitar_tentativas("chave:" + ip)  # mesmo freio da chave de professor
    email = b.email.strip().lower()
    if not email_permitido(email, "professor"):
        raise HTTPException(403, "Use o e-mail institucional (" + " ou ".join(f"@{d}" for d in DOMINIOS_PROFESSORES) + ").")
    if ADMIN_EMAILS or _contar(db, Professor, Professor.coordenacao.is_(True)) > 0:
        raise HTTPException(403, "A coordenação já foi criada. Peça a alguém da coordenação para criar a sua conta "
                                 "na aba Professores do painel.")
    if not CHAVE_MASTER:
        raise HTTPException(403, "Indisponível: a chave de acesso (CHAVE_MASTER) ainda não foi configurada no servidor.")
    if not hmac.compare_digest(b.chave_master.encode(), CHAVE_MASTER.encode()):
        raise HTTPException(403, "Chave de Acesso da Coordenação inválida.")
    p = db.scalar(select(Professor).where(Professor.email == email))
    if p:  # já tem conta de professor: vira coordenação se a senha confere
        if p.ativo is False:
            raise HTTPException(403, "Conta desativada.")
        if not confere_senha(b.senha, p.senha_hash):
            raise HTTPException(409, "Este e-mail já tem conta de professor com outra senha. Digite a senha dela.")
        p.coordenacao = True
    else:
        p = Professor(nome=b.nome.strip(), email=email, senha_hash=hash_senha(b.senha),
                      departamento="Coordenação", coordenacao=True)
        db.add(p)
    try:
        db.flush()
        log_seg(db, p.id, "criar_coordenacao", email, ip)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Este e-mail já possui cadastro. Use a opção Entrar.")
    return _resposta_admin(p)


def _pagina_admin():
    """Endereço /coordenacao (e ADMIN_PATH): abre a mesma tela inicial, já na aba Coordenação."""
    html = _arquivo_index().read_text(encoding="utf-8")
    return HTMLResponse(html.replace("<!--ADMIN-->", "<script>window.GC_ADMIN=true</script>"),
                        headers={"Cache-Control": "no-store"})


for _caminho in {"/" + ADMIN_PATH, "/coordenacao"}:
    app.add_api_route(_caminho, _pagina_admin, methods=["GET"], include_in_schema=False)


def _contar(db: Session, modelo, *cond) -> int:
    return db.scalar(select(func.count()).select_from(modelo).where(*cond)) or 0


def _pedidos_senha(db: Session, limite: int = 100) -> list:
    """Pedidos de nova senha (LogSeguranca 'pedido_senha'). Atendido = a coordenação redefiniu a senha depois do pedido."""
    pedidos = db.scalars(select(LogSeguranca).where(LogSeguranca.acao == "pedido_senha")
                         .order_by(LogSeguranca.id.desc()).limit(limite)).all()
    if not pedidos:
        return []
    menor = pedidos[-1].id
    feitos = {}
    for lid, det in db.execute(select(LogSeguranca.id, LogSeguranca.detalhe)
                               .where(LogSeguranca.acao == "redefinir_senha", LogSeguranca.id > menor)):
        feitos[det] = max(feitos.get(det, 0), lid)
    itens = []
    for p in pedidos:
        partes = (p.detalhe or "").split(";", 1)
        chave = partes[0].strip()
        perfil, _, uid = chave.partition("=")
        itens.append({"id": p.id, "perfil": perfil, "usuario_id": int(uid) if uid.isdigit() else None,
                      "info": partes[1].strip() if len(partes) > 1 else "", "ip": p.ip,
                      "data_hora": iso(p.data_hora), "atendido": feitos.get(chave, 0) > p.id})
    return itens


@app.get("/api/admin/pedidos-senha")
def adm_pedidos_senha(u: dict = Depends(so_admin), db: Session = Depends(get_db)):
    return _pedidos_senha(db)


@app.get("/api/admin/resumo")
def adm_resumo(u: dict = Depends(so_admin), db: Session = Depends(get_db)):
    por_curso = db.execute(select(Curso.nome, func.count(Aluno.id)).select_from(Curso)
                           .outerjoin(Aluno, Aluno.curso_id == Curso.id).group_by(Curso.id, Curso.nome)
                           .order_by(Curso.nome)).all()
    eu = db.get(Professor, u["id"])
    return {"senha_provisoria": bool(eu and senha_provisoria_ativa(eu)),
            "alunos": _contar(db, Aluno), "professores": _contar(db, Professor), "aulas": _contar(db, Aula),
            "presencas": _contar(db, Presenca), "aulas_ativas": _contar(db, Aula, Aula.ativa.is_(True)),
            "alunos_sem_curso": _contar(db, Aluno, Aluno.curso_id.is_(None)),
            "alunos_inativos": _contar(db, Aluno, Aluno.status == "inativo"),
            "matriculas_autorizadas": _contar(db, MatriculasAutorizadas),
            "matriculas_utilizadas": _contar(db, MatriculasAutorizadas, MatriculasAutorizadas.utilizada.is_(True)),
            "matricula_obrigatoria": MATRICULA_OBRIGATORIA,
            "pedidos_senha_pendentes": sum(1 for p in _pedidos_senha(db) if not p["atendido"]),
            "por_curso": [{"curso": n, "alunos": c} for n, c in por_curso]}


@app.get("/api/admin/cursos")
def adm_cursos(u: dict = Depends(so_admin), db: Session = Depends(get_db)):
    rows = db.execute(select(Curso, func.count(Aluno.id)).outerjoin(Aluno, Aluno.curso_id == Curso.id)
                      .group_by(Curso.id).order_by(Curso.nome)).all()
    return [{"id": c.id, "nome": c.nome, "departamento": c.departamento, "ativo": c.ativo, "alunos": n}
            for c, n in rows]


@app.post("/api/admin/cursos")
def adm_curso_novo(b: CursoIn, request: Request, u: dict = Depends(so_admin), db: Session = Depends(get_db)):
    nome = b.nome.strip()
    if db.scalar(select(Curso.id).where(func.lower(Curso.nome) == nome.lower())):
        raise HTTPException(409, "Já existe um curso com esse nome.")
    db.add(Curso(nome=nome, departamento=(b.departamento or "").strip() or None))
    log_seg(db, u["id"], "criar_curso", nome, ip_cliente(request))
    db.commit()
    return {"ok": True}


@app.patch("/api/admin/cursos/{curso_id}")
def adm_curso_ativo(curso_id: int, b: AtivoIn, request: Request, u: dict = Depends(so_admin),
                    db: Session = Depends(get_db)):
    c = db.get(Curso, curso_id)
    if not c:
        raise HTTPException(404, "Curso não encontrado.")
    c.ativo = b.ativo
    log_seg(db, u["id"], "curso_ativo", f"{c.nome}: {'ativo' if b.ativo else 'inativo'}", ip_cliente(request))
    db.commit()
    return {"ok": True}


@app.get("/api/admin/professores")
def adm_professores(u: dict = Depends(so_admin), db: Session = Depends(get_db)):
    rows = db.execute(select(Professor, func.count(Aula.id)).outerjoin(Aula, Aula.professor_id == Professor.id)
                      .group_by(Professor.id).order_by(Professor.nome)).all()
    return [{"id": p.id, "nome": p.nome, "email": p.email, "registro_funcional": p.registro_funcional,
             "departamento": p.departamento, "ativo": p.ativo is not False,
             "aulas": n, "admin": eh_coordenacao(p), "fixo_env": p.email in ADMIN_EMAILS} for p, n in rows]


@app.patch("/api/admin/professores/{prof_id}")
def adm_professor_ativo(prof_id: int, b: AtivoIn, request: Request, u: dict = Depends(so_admin),
                        db: Session = Depends(get_db)):
    p = db.get(Professor, prof_id)
    if not p:
        raise HTTPException(404, "Professor não encontrado.")
    if p.id == u["id"] and not b.ativo:
        raise HTTPException(400, "Você não pode desativar a própria conta.")
    p.ativo = b.ativo
    log_seg(db, u["id"], "professor_ativo", f"{p.email}: {'ativo' if b.ativo else 'desativado'}", ip_cliente(request))
    db.commit()
    return {"ok": True}


@app.post("/api/admin/professores")
def adm_professor_novo(b: ProfessorIn, request: Request, u: dict = Depends(so_admin),
                       db: Session = Depends(get_db)):
    """A coordenação cria a conta do professor (ou de outra pessoa da coordenação) e informa a senha provisória."""
    email = b.email.strip().lower()
    coord = bool(b.coordenacao)
    registro = (b.registro_funcional or "").strip()
    depto = (b.departamento or "").strip() or ("Coordenação" if coord else "")
    if not email_permitido(email, "professor"):
        raise HTTPException(403, "Use um e-mail de professor: " + " ou ".join(f"@{d}" for d in DOMINIOS_PROFESSORES))
    if registro or not coord:  # professor sempre precisa de registro; coordenação só se informar
        if not REGISTRO_RE.fullmatch(registro):
            raise HTTPException(422, "Registro funcional inválido (ex.: DOC-2026-08).")
        if db.scalar(select(Professor.id).where(Professor.registro_funcional == registro)):
            raise HTTPException(409, "Já existe um professor com esse registro funcional.")
    if len(depto) < 2:
        raise HTTPException(422, "Informe o departamento / área.")
    if db.scalar(select(Professor.id).where(Professor.email == email)):
        raise HTTPException(409, "Já existe um professor com esse e-mail.")
    db.add(Professor(nome=b.nome.strip(), email=email, senha_hash=hash_senha(b.senha),
                     registro_funcional=registro or None, departamento=depto, coordenacao=coord))
    log_seg(db, u["id"], "criar_professor", f"{email}; registro={registro or '-'}; coordenacao={coord}", ip_cliente(request))
    db.commit()
    return {"ok": True}


@app.patch("/api/admin/professores/{prof_id}/coordenacao")
def adm_professor_coordenacao(prof_id: int, b: AtivoIn, request: Request, u: dict = Depends(so_admin),
                              db: Session = Depends(get_db)):
    """Promove um professor à coordenação (ativo=true) ou tira dela (ativo=false)."""
    p = db.get(Professor, prof_id)
    if not p:
        raise HTTPException(404, "Professor não encontrado.")
    if not b.ativo:
        if p.id == u["id"]:
            raise HTTPException(400, "Você não pode tirar a si mesmo da coordenação.")
        if p.email in ADMIN_EMAILS:
            raise HTTPException(400, "Essa conta está em ADMIN_EMAILS (configuração do servidor).")
    p.coordenacao = b.ativo
    log_seg(db, u["id"], "professor_coordenacao", f"{p.email}: {'coordenação' if b.ativo else 'professor'}",
            ip_cliente(request))
    db.commit()
    return {"ok": True}


@app.delete("/api/admin/professores/{prof_id}")
def adm_professor_excluir(prof_id: int, request: Request, u: dict = Depends(so_admin),
                          db: Session = Depends(get_db)):
    """Exclui o professor, as aulas dele e as presenças dessas aulas (tudo na mesma transação)."""
    p = db.get(Professor, prof_id)
    if not p:
        raise HTTPException(404, "Professor não encontrado.")
    if p.id == u["id"]:
        raise HTTPException(400, "Você não pode excluir a própria conta.")
    if eh_coordenacao(p):
        raise HTTPException(400, "Essa conta é da coordenação. Tire-a da coordenação antes de excluí-la.")
    ids_aulas = select(Aula.id).where(Aula.professor_id == prof_id)
    db.execute(delete(Tentativa).where(Tentativa.aula_id.in_(ids_aulas)))
    db.execute(delete(Presenca).where(Presenca.aula_id.in_(ids_aulas)))
    db.execute(delete(Aula).where(Aula.professor_id == prof_id))
    db.execute(delete(Professor).where(Professor.id == prof_id))
    log_seg(db, u["id"], "excluir_professor", p.email, ip_cliente(request))
    db.commit()
    _cache_ativas["t"] = 0
    return {"ok": True}


@app.get("/api/admin/alunos")
def adm_alunos(q: str = "", curso_id: int = 0, pagina: int = 1,
               u: dict = Depends(so_admin), db: Session = Depends(get_db)):
    cond = []
    if q.strip():
        like = f"%{q.strip().lower()}%"
        cond.append(or_(func.lower(Aluno.nome).like(like), Aluno.email.like(like), func.lower(Aluno.matricula).like(like)))
    if curso_id:
        cond.append(Aluno.curso_id == curso_id)
    total = _contar(db, Aluno, *cond)
    rows = db.execute(select(Aluno, Curso.nome).outerjoin(Curso, Curso.id == Aluno.curso_id).where(*cond)
                      .order_by(Aluno.nome).limit(50).offset((max(pagina, 1) - 1) * 50)).all()
    return {"total": total, "itens": [{"id": a.id, "nome": a.nome, "email": a.email, "matricula": a.matricula,
                                       "curso_id": a.curso_id, "curso": c, "periodo": a.periodo,
                                       "status": a.status or "ativo"} for a, c in rows]}


@app.patch("/api/admin/alunos/{aluno_id}")
def adm_aluno_editar(aluno_id: int, b: AlunoEdicaoIn, request: Request, u: dict = Depends(so_admin),
                     db: Session = Depends(get_db)):
    a = db.get(Aluno, aluno_id)
    if not a:
        raise HTTPException(404, "Aluno não encontrado.")
    mudancas = []
    if b.curso_id is not None:
        c = db.get(Curso, b.curso_id)
        if not c:
            raise HTTPException(404, "Curso não encontrado.")
        a.curso_id = c.id
        mudancas.append(f"curso={c.nome}")
    if b.periodo is not None:
        a.periodo = b.periodo
        mudancas.append(f"periodo={b.periodo}")
    if b.status is not None:
        a.status = b.status
        mudancas.append(f"status={b.status}")
    if not mudancas:
        raise HTTPException(422, "Nada para alterar.")
    log_seg(db, u["id"], "editar_aluno", f"aluno={aluno_id}; " + "; ".join(mudancas), ip_cliente(request))
    db.commit()
    return {"ok": True}


@app.delete("/api/admin/alunos/{aluno_id}")
def adm_aluno_excluir(aluno_id: int, request: Request, u: dict = Depends(so_admin), db: Session = Depends(get_db)):
    a = db.get(Aluno, aluno_id)
    if not a:
        raise HTTPException(404, "Aluno não encontrado.")
    db.execute(delete(Tentativa).where(Tentativa.aluno_id == aluno_id))
    db.execute(delete(Presenca).where(Presenca.aluno_id == aluno_id))
    if a.matricula:  # libera a matrícula na lista autorizada para um novo cadastro
        db.execute(update(MatriculasAutorizadas).where(MatriculasAutorizadas.matricula == a.matricula)
                   .values(utilizada=False))
    db.execute(delete(Aluno).where(Aluno.id == aluno_id))
    log_seg(db, u["id"], "excluir_aluno", f"aluno={aluno_id}; matricula={a.matricula}", ip_cliente(request))
    db.commit()
    return {"ok": True}


class MinhaSenhaIn(BaseModel):
    senha_atual: str = Field(max_length=128)
    nova_senha: str = Field(min_length=8, max_length=128)
    confirmar_senha: Optional[str] = Field(None, max_length=128)


@app.post("/api/admin/minha-senha")
def adm_minha_senha(b: MinhaSenhaIn, request: Request, u: dict = Depends(so_admin), db: Session = Depends(get_db)):
    """A coordenação troca a PRÓPRIA senha (por exemplo, logo depois de entrar com a senha provisória)."""
    ip = ip_cliente(request)
    limitar_tentativas("adm-ms:" + str(u["id"]), 8, 600)
    if b.confirmar_senha is not None and b.confirmar_senha != b.nova_senha:
        raise HTTPException(422, "As senhas novas não conferem. Digite a mesma senha nos dois campos.")
    p = db.get(Professor, u["id"])
    if not p or p.ativo is False or not eh_coordenacao(p):
        raise HTTPException(403, "Acesso restrito à coordenação.")
    if not senha_admin_confere(p, b.senha_atual):  # 400 (e não 401): 401 deslogaria a pessoa na tela
        raise HTTPException(400, "A senha atual está incorreta.")
    if COORD_SENHA and _forma_simples(b.nova_senha) == _forma_simples(COORD_SENHA):
        raise HTTPException(422, "Escolha uma senha diferente da provisória.")
    p.senha_hash = hash_senha(b.nova_senha)
    log_seg(db, p.id, "trocar_senha_admin", "", ip)
    db.commit()
    return {"ok": True}


@app.post("/api/admin/senha")
def adm_senha(b: SenhaIn, request: Request, u: dict = Depends(so_admin), db: Session = Depends(get_db)):
    """Não existe 'esqueci a senha' por e-mail: a coordenação redefine e informa a nova senha ao usuário."""
    alvo = db.get(Aluno if b.perfil == "aluno" else Professor, b.id)
    if not alvo:
        raise HTTPException(404, "Usuário não encontrado.")
    alvo.senha_hash = hash_senha(b.nova_senha)
    log_seg(db, u["id"], "redefinir_senha", f"{b.perfil}={b.id}", ip_cliente(request))
    db.commit()
    return {"ok": True}


# ---- Matrículas autorizadas (lista pré-aprovada) ----
@app.get("/api/admin/matriculas")
def adm_matriculas(q: str = "", pagina: int = 1, u: dict = Depends(so_admin), db: Session = Depends(get_db)):
    M = MatriculasAutorizadas
    cond = [M.matricula.like(f"%{normalizar_matricula(q)}%")] if q.strip() else []
    rows = db.execute(select(M, Curso.nome).outerjoin(Curso, Curso.id == M.curso_id).where(*cond)
                      .order_by(M.matricula).limit(50).offset((max(pagina, 1) - 1) * 50)).all()
    return {"obrigatoria": MATRICULA_OBRIGATORIA, "total": _contar(db, M, *cond), "total_geral": _contar(db, M),
            "utilizadas": _contar(db, M, M.utilizada.is_(True)),
            "itens": [{"id": m.id, "matricula": m.matricula, "curso": c, "utilizada": m.utilizada} for m, c in rows]}


@app.post("/api/admin/matriculas/importar")
def adm_matriculas_importar(b: MatriculasIn, request: Request, u: dict = Depends(so_admin),
                            db: Session = Depends(get_db)):
    """Importa várias matrículas de uma vez. Linha = '202610405' ou '202610405;Gestão da TI' (curso opcional)."""
    padrao = None
    if b.curso_id is not None:
        if not db.get(Curso, b.curso_id):
            raise HTTPException(422, "Curso inválido.")
        padrao = b.curso_id
    cursos = {c.nome.lower(): c.id for c in db.scalars(select(Curso))}
    linhas = [l.strip() for l in b.texto.splitlines() if l.strip()]
    if len(linhas) > 20000:
        raise HTTPException(413, "Importe no máximo 20.000 linhas por vez.")
    novas, invalidas = {}, []
    for i, linha in enumerate(linhas, 1):
        partes = [p.strip() for p in re.split(r"[;,\t]", linha)]
        mat, cid = normalizar_matricula(partes[0]), padrao
        if not matricula_valida(mat):
            invalidas.append(f"linha {i}: matrícula inválida ({mat[:20]})")
            continue
        if len(partes) > 1 and partes[1]:
            cid = cursos.get(partes[1].lower())
            if cid is None:
                invalidas.append(f"linha {i}: curso '{partes[1][:40]}' não existe")
                continue
        novas.setdefault(mat, cid)
    chaves = list(novas)
    existentes, com_aluno = set(), set()
    for i in range(0, len(chaves), 500):
        bloco = chaves[i:i + 500]
        existentes.update(db.scalars(select(MatriculasAutorizadas.matricula)
                                     .where(MatriculasAutorizadas.matricula.in_(bloco))))
        com_aluno.update(db.scalars(select(Aluno.matricula).where(Aluno.matricula.in_(bloco))))
    adicionadas = 0
    for mat, cid in novas.items():
        if mat not in existentes:
            # se já existe aluno com essa matrícula, ela entra como utilizada (não permite um 2º cadastro)
            db.add(MatriculasAutorizadas(matricula=mat, curso_id=cid, utilizada=mat in com_aluno))
            adicionadas += 1
    log_seg(db, u["id"], "importar_matriculas",
            f"{adicionadas} nova(s); {len(existentes)} já existiam; {len(invalidas)} inválida(s)", ip_cliente(request))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Outra importação estava em andamento. Tente de novo.")
    return {"adicionadas": adicionadas, "ja_existiam": len(existentes), "invalidas": len(invalidas),
            "exemplos_invalidas": invalidas[:15]}


@app.delete("/api/admin/matriculas/{mid}")
def adm_matricula_excluir(mid: int, request: Request, u: dict = Depends(so_admin), db: Session = Depends(get_db)):
    m = db.get(MatriculasAutorizadas, mid)
    if not m:
        raise HTTPException(404, "Matrícula não encontrada.")
    if m.utilizada:
        raise HTTPException(409, "Esta matrícula já está vinculada a um aluno. Exclua ou edite o aluno primeiro.")
    db.execute(delete(MatriculasAutorizadas).where(MatriculasAutorizadas.id == mid))
    log_seg(db, u["id"], "excluir_matricula", m.matricula, ip_cliente(request))
    db.commit()
    return {"ok": True}


# ---- Aulas, alertas e auditoria ----
@app.get("/api/admin/aulas")
def adm_aulas(u: dict = Depends(so_admin), db: Session = Depends(get_db)):
    rows = db.execute(select(Aula, Professor.nome, func.count(Presenca.id), Curso.nome)
                      .join(Professor, Professor.id == Aula.professor_id)
                      .outerjoin(Presenca, Presenca.aula_id == Aula.id)
                      .outerjoin(Curso, Curso.id == Aula.curso_id)
                      .group_by(Aula.id, Professor.nome, Curso.nome).order_by(Aula.id.desc()).limit(60)).all()
    return [{**aula_dict(a, n, cn), "professor": nome} for a, nome, n, cn in rows]


@app.post("/api/admin/aulas/{aula_id}/encerrar")
def adm_aula_encerrar(aula_id: int, u: dict = Depends(so_admin), db: Session = Depends(get_db)):
    a = db.get(Aula, aula_id)
    if not a:
        raise HTTPException(404, "Aula não encontrada.")
    if a.ativa:
        a.ativa, a.encerrada_em = False, utcnow()
        db.commit()
        _cache_ativas["t"] = 0
    return {"ok": True}


@app.delete("/api/admin/aulas/{aula_id}")
def adm_aula_excluir(aula_id: int, request: Request, u: dict = Depends(so_admin), db: Session = Depends(get_db)):
    a = db.get(Aula, aula_id)
    if not a:
        raise HTTPException(404, "Aula não encontrada.")
    db.execute(delete(Tentativa).where(Tentativa.aula_id == aula_id))
    removidas = db.execute(delete(Presenca).where(Presenca.aula_id == aula_id)).rowcount
    db.execute(delete(Aula).where(Aula.id == aula_id))
    log_seg(db, u["id"], "excluir_aula_admin", f"aula={aula_id}; titulo={a.titulo}; presencas={removidas}",
            ip_cliente(request))
    db.commit()
    _cache_ativas["t"] = 0
    return {"ok": True, "presencas_removidas": removidas}


@app.get("/api/admin/alertas")
def adm_alertas(u: dict = Depends(so_admin), db: Session = Depends(get_db)):
    """Check-ins aceitos, mas com sinais de risco (GPS suspeito, VPN em modo 'avisar'...)."""
    rows = db.execute(select(Presenca.id, Aluno.nome, Aluno.email, Aula.titulo, Presenca.alertas, Presenca.ip,
                             Presenca.distancia_m, Presenca.registrado_em)
                      .join(Aluno, Aluno.id == Presenca.aluno_id).join(Aula, Aula.id == Presenca.aula_id)
                      .where(Presenca.alertas.is_not(None)).order_by(Presenca.id.desc()).limit(100)).all()
    return [{"id": r.id, "aluno": r.nome, "email": r.email, "aula": r.titulo, "alertas": r.alertas, "ip": r.ip,
             "distancia_m": round(r.distancia_m, 1), "registrado_em": iso(r.registrado_em)} for r in rows]


@app.get("/api/admin/auditoria")
def adm_auditoria(u: dict = Depends(so_admin), db: Session = Depends(get_db)):
    """Trilha de auditoria (LogSeguranca): quem fez o quê, quando e de qual IP. Últimos 150 registros."""
    rows = db.execute(select(LogSeguranca, Professor.nome)
                      .outerjoin(Professor, Professor.id == LogSeguranca.usuario_id)
                      .order_by(LogSeguranca.id.desc()).limit(150)).all()
    return [{"id": l.id, "usuario_id": l.usuario_id, "usuario": nome, "acao": l.acao, "detalhe": l.detalhe,
             "ip": l.ip, "data_hora": iso(l.data_hora)} for l, nome in rows]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")),
                reload=os.getenv("RELOAD", "1") == "1")
