"""
Guará Check (antes GeoCheckIn) - UniCerrado (Centro Universitário de Goiatuba)
Presença acadêmica por geolocalização (geofencing) - FastAPI + SQLAlchemy.

Local:     python app.py            -> http://localhost:8000
Produção:  uvicorn app:app --host 0.0.0.0 --port $PORT --workers 2
"""
import base64
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
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Literal, Optional

import jwt
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import (Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text,
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
# Prazo máximo de uma chamada aberta (horas). Passou disso, o sistema encerra sozinho e o aluno não registra mais presença:
# um evento esquecido aberto não vira presença tardia nem acumula na lista. 0 = sem prazo.
try:
    AULA_MAX_HORAS = max(0.0, float(os.getenv("AULA_MAX_HORAS", "12")))
except ValueError:
    AULA_MAX_HORAS = 12.0
HIST_POR_PAGINA = 30        # eventos por página no histórico do professor e no painel da coordenação (60)
COMPROVANTES_POR_PAGINA = 20  # comprovantes do aluno por página (?pagina=2 traz os mais antigos)
AULAS_ATIVAS_MAX = 500      # teto da lista de chamadas abertas (filtrada por curso/período depois, por aluno)

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

# Conta PROVISÓRIA da coordenação (opcional): só é criada se COORD_SENHA estiver definida no ambiente (Render) E ainda
# não existir nenhuma conta da coordenação. NÃO há senha padrão no código. Sem COORD_SENHA, o primeiro acesso é feito
# pela aba Coordenação, com a CHAVE_MASTER. Troque a senha logo em Resumo > "Trocar minha senha". COORD_PROVISORIA=0 desliga.
COORD_PROVISORIA = os.getenv("COORD_PROVISORIA", "1").strip().lower() not in ("0", "false", "nao", "não", "off")
COORD_EMAIL = os.getenv("COORD_EMAIL", "coordenacao355@unicerrado.edu.br").strip().lower()
COORD_SENHA = os.getenv("COORD_SENHA", "")

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

# Freio do cadastro de ALUNO por IP (contas novas por janela). Alto de propósito: o Wi-Fi da faculdade sai por um IP só e
# uma turma inteira se cadastra junto. Redes do campus listadas em IPS_PERMITIDOS não entram nessa conta.
CADASTRO_ALUNO_MAX = int(os.getenv("CADASTRO_ALUNO_MAX", "100"))
CADASTRO_ALUNO_JANELA_S = int(os.getenv("CADASTRO_ALUNO_JANELA_S", "3600"))
# Login da coordenação: só as FALHAS contam. Por IP, por conta+IP e um teto geral por conta (contra chute vindo de muitos IPs).
ADMIN_FALHAS_IP = int(os.getenv("ADMIN_FALHAS_IP", "10"))
ADMIN_FALHAS_CONTA = int(os.getenv("ADMIN_FALHAS_CONTA", "8"))
ADMIN_FALHAS_GERAL_HORA = int(os.getenv("ADMIN_FALHAS_GERAL_HORA", "100"))

# Confirmação do e-mail por código de 6 números (impede cadastrar o e-mail de outra pessoa). A Render GRATUITA bloqueia SMTP
# (portas 25, 465 e 587): lá use EMAIL_PROVEDOR=brevo (API por HTTPS, plano grátis de 300 e-mails/dia). "smtp" só em plano pago
# ou fora da Render. O remetente (EMAIL_REMETENTE) precisa estar verificado no provedor.
EMAIL_PROVEDOR = os.getenv("EMAIL_PROVEDOR", "").strip().lower()          # brevo | smtp | vazio (sem envio)
EMAIL_API_KEY = os.getenv("EMAIL_API_KEY", "").strip()
EMAIL_REMETENTE = os.getenv("EMAIL_REMETENTE", "").strip()
EMAIL_REMETENTE_NOME = os.getenv("EMAIL_REMETENTE_NOME", "Guará Check").strip() or "Guará Check"
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USUARIO = os.getenv("SMTP_USUARIO", "").strip()
SMTP_SENHA = os.getenv("SMTP_SENHA", "")
# auto (padrão) = confirma o e-mail quando o envio está configurado; 1 = obrigatório (sem envio configurado o cadastro fica
# indisponível, nunca aceita e-mail não confirmado); 0 = desligado.
VERIFICAR_EMAIL = os.getenv("VERIFICAR_EMAIL", "auto").strip().lower()
CODIGO_EMAIL_MIN = int(os.getenv("CODIGO_EMAIL_MIN", "15"))               # validade do código
CODIGO_MAX_TENTATIVAS = int(os.getenv("CODIGO_MAX_TENTATIVAS", "5"))      # erros antes de o código ser descartado
CODIGO_INTERVALO_S = int(os.getenv("CODIGO_INTERVALO_S", "30"))           # espera entre dois envios para o mesmo e-mail
CODIGO_ENVIOS_HORA = int(os.getenv("CODIGO_ENVIOS_HORA", "5"))            # envios por e-mail por hora
CONFIRMA_FALHAS_IP = int(os.getenv("CONFIRMA_FALHAS_IP", "40"))           # códigos errados por IP em 10 min

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
    # uma matrícula, uma conta: a trava é do banco (à prova de dois cadastros ao mesmo tempo); NULL das contas da V1 pode repetir
    __table_args__ = (Index("uq_alunos_matricula", "matricula", unique=True),)


class Professor(Base):
    __tablename__ = "professores"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ativo: Mapped[Optional[bool]] = mapped_column(Boolean, default=True)  # a coordenação pode desativar
    coordenacao: Mapped[Optional[bool]] = mapped_column(Boolean, default=False)  # conta da coordenação (painel administrativo)
    # False = cadastro feito pelo próprio professor, ainda sem a autorização da coordenação (não consegue entrar).
    # True/None = autorizado (contas antigas e contas criadas pela coordenação ficam como True).
    aprovado: Mapped[Optional[bool]] = mapped_column(Boolean, default=True)
    registro_funcional: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)  # ex.: DOC-2026-08
    departamento: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    nome: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    senha_hash: Mapped[str] = mapped_column(String(200))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (Index("uq_professores_registro", "registro_funcional", unique=True),)


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
    # no máximo UMA chamada ativa por professor (dois envios simultâneos não criam duas: o segundo recebe 409)
    __table_args__ = (Index("uq_aula_ativa_prof", "professor_id", unique=True,
                            postgresql_where=text("ativa"), sqlite_where=text("ativa")),)


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


NOTA_MAX = 4000  # caracteres por anotação
NOTAS_MAX_PDF = 200  # anotações no PDF com todas


class NotaAluno(Base):
    """
    Anotação PRIVADA do aluno sobre um evento em que ele registrou presença ("como foi", o que aprendeu...).
    Só o próprio aluno lê, edita e baixa: NENHUMA rota de professor ou da coordenação consulta esta tabela
    (a coordenação só a apaga junto com o aluno). Guarda uma cópia do título/tipo/disciplina/data do evento, então a
    anotação continua existindo se o professor excluir o evento. presenca_id não é chave estrangeira, de propósito.
    """
    __tablename__ = "notas_aluno"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    aluno_id: Mapped[int] = mapped_column(ForeignKey("alunos.id", ondelete="CASCADE"), index=True)
    presenca_id: Mapped[int] = mapped_column(Integer)
    evento_titulo: Mapped[str] = mapped_column(String(140))
    evento_tipo: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    evento_disciplina: Mapped[Optional[str]] = mapped_column(String(140), nullable=True)
    evento_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # quando o aluno registrou presença
    texto: Mapped[str] = mapped_column(Text)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("aluno_id", "presenca_id", name="uq_nota_aluno_presenca"),)


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


class CadastroPendente(Base):
    """Cadastro que ainda espera o código enviado por e-mail. Nada é reservado (e-mail, matrícula) até o código ser
    confirmado: por isso ninguém consegue 'tomar' o e-mail ou a matrícula de outra pessoa só digitando."""
    __tablename__ = "cadastros_pendentes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    perfil: Mapped[str] = mapped_column(String(10))
    email: Mapped[str] = mapped_column(String(160))
    dados: Mapped[str] = mapped_column(Text)                       # JSON: nome, matrícula, curso, período, registro, depto
    senha_hash: Mapped[str] = mapped_column(String(200))           # só o hash; a senha em si nunca é guardada
    codigo_hash: Mapped[str] = mapped_column(String(64))           # HMAC do código; o código em si nunca é guardado
    tentativas: Mapped[int] = mapped_column(Integer, default=0)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    enviado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    __table_args__ = (UniqueConstraint("perfil", "email", name="uq_pendente_perfil_email"),)


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
                        ("aprovado", "BOOLEAN DEFAULT TRUE"),  # professores que já existem continuam autorizados
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
    # Matrícula e registro funcional únicos no banco (create_all não mexe em tabela que já existe).
    # Se já houver valores repetidos, o índice não é criado e o app segue funcionando: corrija os repetidos e reinicie.
    for indice, tabela, coluna in (("uq_alunos_matricula", "alunos", "matricula"),
                                   ("uq_professores_registro", "professores", "registro_funcional")):
        try:
            with engine.begin() as conn:
                conn.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS {indice} ON {tabela} ({coluna})"))
        except Exception as exc:
            print(f"Índice único {indice} não criado (há {coluna} repetido?):", exc)
    # Uma chamada ativa por professor. Bancos antigos podem ter duas ativas do mesmo professor: fica só a mais nova.
    try:
        with engine.begin() as conn:
            conn.execute(text("UPDATE aulas SET ativa = FALSE, encerrada_em = COALESCE(encerrada_em, CURRENT_TIMESTAMP) "
                              "WHERE ativa AND id NOT IN (SELECT MAX(id) FROM aulas WHERE ativa GROUP BY professor_id)"))
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_aula_ativa_prof ON aulas (professor_id) WHERE ativa"))
    except Exception as exc:
        print("Índice uq_aula_ativa_prof não criado:", exc)


def resetar_dados_de_teste():
    """
    Apaga alunos, professores, aulas, presenças, tentativas, anotações dos alunos, matrículas autorizadas e a auditoria.
    Mantém os cursos.
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
        for modelo in (NotaAluno, Tentativa, Presenca, Aula, Aluno, Professor, MatriculasAutorizadas, LogSeguranca,
                       CadastroPendente):
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


def ip_em_rede_permitida(ip: str) -> bool:
    """True se o IP está numa rede de IPS_PERMITIDOS (ex.: o Wi-Fi do campus)."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in n for n in IPS_PERMITIDOS)


_cache_ip: dict = {}   # ip -> (expira em time.monotonic(), True/False/None)
VPN_CACHE_S = 6 * 3600       # resposta válida do provedor
VPN_FALHA_CACHE_S = 60       # sem resposta (fora do ar, cota esgotada, chave errada): não insiste a cada aluno
# Disjuntor: o cache acima é por IP, e cada aluno em dados móveis tem um IP diferente. Com o provedor fora do ar, cada um
# esperaria 2,5 s (uma thread presa por consulta). Depois de VPN_DISJUNTOR_FALHAS falhas seguidas, NENHUM IP é consultado por
# VPN_DISJUNTOR_S segundos (todos passam como "não verificado", sem bloquear ninguém). Ao fim da pausa, uma falha basta para
# voltar a pausar; uma resposta boa zera a contagem.
VPN_DISJUNTOR_FALHAS = 5
VPN_DISJUNTOR_S = 60
_vpn_disjuntor = {"falhas": 0, "aberto_ate": 0.0}


def ip_e_vpn(ip: str, liberar_conexao=None) -> Optional[bool]:
    """True = VPN/proxy; False = limpo; None = não foi possível verificar (nunca bloqueia nesse caso).
    `liberar_conexao` (ex.: db.commit) é chamada só quando vai haver consulta externa: ela pode levar até 2,5 s, e esse
    tempo não pode prender uma das poucas conexões do banco (o professor e os outros alunos ficariam sem conexão)."""
    if not PROXYCHECK_KEY or not ip:
        return None
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if addr.is_private or addr.is_loopback or addr.is_link_local or any(addr in n for n in IPS_PERMITIDOS):
        return False
    agora = time.monotonic()
    hit = _cache_ip.get(ip)
    if hit and agora < hit[0]:
        return hit[1]
    if agora < _vpn_disjuntor["aberto_ate"]:
        return None        # provedor fora do ar: não consulta e não guarda no cache (a recuperação é imediata)
    if liberar_conexao is not None:
        liberar_conexao()
    resultado = None
    try:
        with urllib.request.urlopen(f"https://proxycheck.io/v2/{ip}?key={PROXYCHECK_KEY}&vpn=1", timeout=2.5) as r:
            dados = json.load(r)
        info = next((v for v in dados.values() if isinstance(v, dict)), None)
        if dados.get("status") in ("ok", "warning") and info is not None:
            resultado = str(info.get("proxy", "")).lower() == "yes"
    except Exception:
        resultado = None
    if len(_cache_ip) > 20000:
        _cache_ip.clear()
    # Lembrar também a FALHA: cada consulta sem resposta custa até 2,5 s e segura uma thread e uma conexão do banco; sem isto,
    # com o provedor fora do ar a turma inteira esperaria 2,5 s no check-in.
    _cache_ip[ip] = (agora + (VPN_CACHE_S if resultado is not None else VPN_FALHA_CACHE_S), resultado)
    if resultado is None:
        _vpn_disjuntor["falhas"] += 1
        if _vpn_disjuntor["falhas"] >= VPN_DISJUNTOR_FALHAS:
            _vpn_disjuntor.update(aberto_ate=time.monotonic() + VPN_DISJUNTOR_S, falhas=VPN_DISJUNTOR_FALHAS - 1)
    else:
        _vpn_disjuntor["falhas"] = 0
    return resultado


_tentativas: dict = {}
TENTATIVAS_MAX_CHAVES = 5000
TENTATIVAS_RETENCAO_S = max(3600, CADASTRO_ALUNO_JANELA_S)   # a maior janela de qualquer freio


def _podar_tentativas(agora: float):
    """Segura o tamanho do dicionário de freios SEM zerar as contagens que ainda valem. Antes, ao passar de 5.000 chaves
    ele era apagado inteiro: uma turma grande (uma chave por aluno nas anotações, PDFs e conferência) fazia isso sozinha, e quem
    estava travado por tentar senhas em excesso voltava a ter todas as tentativas. Agora: 1) some só o que já venceu;
    2) se ainda passar do teto, descarta até sobrar metade dele, começando pelas chaves com MENOS tentativas (e, em empate, as
    de uso mais antigo): quem está travado, com muitas tentativas, é o último a sair."""
    if len(_tentativas) <= TENTATIVAS_MAX_CHAVES:
        return
    for k in [k for k, v in _tentativas.items() if not v or agora - v[-1] >= TENTATIVAS_RETENCAO_S]:
        del _tentativas[k]
    if len(_tentativas) > TENTATIVAS_MAX_CHAVES:
        antigas = sorted(_tentativas, key=lambda k: (len(_tentativas[k]), _tentativas[k][-1]))[: len(_tentativas) - TENTATIVAS_MAX_CHAVES // 2]
        for k in antigas:
            del _tentativas[k]


def limitar_tentativas(chave: str, maximo: int = 5, janela: int = 600):
    agora = time.monotonic()
    _podar_tentativas(agora)
    recentes = [t for t in _tentativas.get(chave, []) if agora - t < janela]
    if len(recentes) >= maximo:
        raise HTTPException(429, "Muitas tentativas. Aguarde alguns minutos e tente de novo.")
    recentes.append(agora)
    _tentativas[chave] = recentes


# Login: só as FALHAS contam (quem acerta a senha não gasta tentativa), então uma turma inteira entrando ao mesmo
# tempo pela mesma rede da faculdade não se bloqueia. Duas travas: por IP (varrer várias contas) e por conta+IP
# (adivinhar a senha de uma conta; outra pessoa, em outro IP, não consegue travar a conta de ninguém).
LOGIN_FALHAS_IP = int(os.getenv("LOGIN_FALHAS_IP", "30"))
LOGIN_FALHAS_CONTA = int(os.getenv("LOGIN_FALHAS_CONTA", "8"))
LOGIN_JANELA_S = 600


def checar_falhas(chave: str, maximo: int, janela: int):
    """Levanta 429 se a chave já acumulou `maximo` falhas dentro da janela. Não conta a chamada atual."""
    agora = time.monotonic()
    recentes = [t for t in _tentativas.get(chave, []) if agora - t < janela]
    if recentes:
        _tentativas[chave] = recentes
    else:
        _tentativas.pop(chave, None)
    if len(recentes) >= maximo:
        raise HTTPException(429, "Muitas tentativas de entrada. Aguarde alguns minutos e tente de novo.")


def registrar_falha(chave: str):
    agora = time.monotonic()
    _podar_tentativas(agora)
    _tentativas.setdefault(chave, []).append(agora)


def esquecer_falhas(chave: str):
    _tentativas.pop(chave, None)


# =====================================================================
# AUTENTICAÇÃO
# =====================================================================
EMAIL_RE = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$")


def normalizar_matricula(m: Optional[str]) -> str:
    """Tira espaços e põe as letras a-z em maiúsculas, para 'gti 2026' e 'GTI2026' serem a mesma matrícula.
    Só ASCII: .upper() puro transformaria 'ſ' em 'S' e 'ı' em 'I', e a validação aceitaria o que não é letra a-z."""
    return "".join(c.upper() if "a" <= c <= "z" else c for c in re.sub(r"\s+", "", m or ""))


def nome_limpo(s: Optional[str]) -> str:
    """Tira espaços sobrando. O nome precisa ter 3 caracteres de verdade: o Pydantic conta os espaços, então
    '   ' passava e a conta era criada com nome vazio."""
    n = re.sub(r"\s+", " ", s or "").strip()
    if len(n) < 3:
        raise HTTPException(422, "Informe o nome completo (mínimo 3 letras).")
    return n


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


HASH_FALSO = hash_senha(secrets.token_hex(16))  # confere_senha contra e-mail inexistente leva o mesmo tempo que contra um real


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


def aguardando_autorizacao(p: "Professor") -> bool:
    """True = professor que se cadastrou sozinho e ainda não foi autorizado pela coordenação (não pode entrar).
    Conta da coordenação nunca fica aguardando: senão ADMIN_EMAILS poderia ficar sem acesso ao próprio painel."""
    return p.aprovado is False and not (bool(p.coordenacao) or p.email in ADMIN_EMAILS)


MSG_AGUARDANDO = ("Seu cadastro de professor foi recebido e está aguardando a autorização da coordenação. "
                  "Você poderá entrar assim que ele for aprovado.")


def marca_senha(senha_hash: Optional[str]) -> str:
    """Impressão digital da senha atual (HMAC do hash): vai no token como 'pv'. Quando a senha muda, a marca muda e as
    sessões abertas com a senha antiga deixam de valer. Não revela nada do hash."""
    return hmac.new(SECRET_KEY.encode(), ("pv|" + (senha_hash or "")).encode(), hashlib.sha256).hexdigest()[:16]


def resposta_token(u, perfil: str, curso: Optional[str] = None) -> dict:
    claims = {"sub": str(u.id), "role": perfil, "nome": u.nome, "pv": marca_senha(u.senha_hash),
              "exp": utcnow() + timedelta(hours=TOKEN_HORAS)}
    if perfil == "aluno":
        claims["cid"] = u.curso_id
        claims["per"] = u.periodo
    token = jwt.encode(claims, SECRET_KEY, algorithm="HS256")
    return {"token": token, "usuario": {"id": u.id, "nome": u.nome, "email": u.email, "perfil": perfil,
                                        "curso": curso, "periodo": getattr(u, "periodo", None),
                                        "matricula": getattr(u, "matricula", None)}}


_cache_contas: dict = {}
CONTA_CACHE_S = 5  # uma conta desativada perde o acesso em até 5 s (as rotas da coordenação limpam o cache na hora)


def esquecer_contas():
    _cache_contas.clear()


def dados_conta(db: Session, role: str, uid: int) -> Optional[dict]:
    """A conta do token ainda existe e continua liberada? Sem isso, quem foi desativado, excluído ou rebaixado
    manteria a sessão até o token vencer (TOKEN_HORAS). Devolve None se não estiver liberada; senão, os dados ATUAIS
    da conta: a marca da senha (pv) e, para aluno, o curso e o período de hoje (o que a coordenação editou vale na
    hora, sem esperar novo login). O resultado fica 5 s em cache para não consultar o banco a cada chamada da tela do
    aluno, que atualiza sozinha; as rotas da coordenação limpam o cache quando mudam a conta."""
    chave, agora = (role, uid), time.monotonic()
    hit = _cache_contas.get(chave)
    if hit and agora - hit[0] < CONTA_CACHE_S:
        return hit[1]
    dados = None
    if role == "aluno":
        a = db.get(Aluno, uid)
        if a is not None and a.status != "inativo":
            dados = {"pv": marca_senha(a.senha_hash), "cid": a.curso_id, "per": a.periodo}
    elif role == "professor":
        p = db.get(Professor, uid)
        if p is not None and p.ativo is not False and not aguardando_autorizacao(p):
            dados = {"pv": marca_senha(p.senha_hash)}
    elif role == "admin":
        p = db.get(Professor, uid)
        if p is not None and p.ativo is not False and eh_coordenacao(p):
            dados = {"pv": marca_senha(p.senha_hash)}
    if len(_cache_contas) > 5000:
        _cache_contas.clear()
    _cache_contas[chave] = (agora, dados)
    return dados


def conta_liberada(db: Session, role: str, uid: int) -> bool:
    return dados_conta(db, role, uid) is not None


def usuario_atual(authorization: Optional[str] = Header(None), db: Session = Depends(get_db)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Faça login para continuar.")
    try:
        p = jwt.decode(authorization[7:], SECRET_KEY, algorithms=["HS256"], options={"require": ["exp"]})
        u = {"id": int(p["sub"]), "role": p["role"], "nome": p.get("nome", ""),
             "cid": p.get("cid"), "tem_cid": "cid" in p,
             "per": p.get("per"), "tem_per": "per" in p}
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(401, "Sessão expirada. Entre novamente.")
    dados = dados_conta(db, u["role"], u["id"])
    if dados is None:
        raise HTTPException(401, "Esta conta foi desativada ou removida. Procure a coordenação.")
    # Token com 'pv' (todos os emitidos a partir desta versão): a senha mudou depois do login = sessão encerrada.
    # Tokens antigos, sem 'pv', valem até vencer (no máximo TOKEN_HORAS) e depois não existem mais.
    if "pv" in p and not hmac.compare_digest(str(p["pv"]), dados["pv"]):
        raise HTTPException(401, "A senha desta conta foi alterada. Entre novamente.")
    if u["role"] == "aluno":  # curso e período de HOJE, não os de quando o token foi emitido
        u["cid"], u["per"], u["tem_cid"], u["tem_per"] = dados["cid"], dados["per"], True, True
    return u


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
    # allow_inf_nan=False: o json do Python aceita NaN/Infinity; NaN em pos_ts fazia "abs(agora - pos_ts) > limite" dar
    # falso e pulava o teste de idade da leitura.
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    precisao_m: Optional[float] = Field(None, ge=0, le=1_000_000, allow_inf_nan=False)
    pos_ts: float = Field(allow_inf_nan=False)      # instante da leitura do GPS (ms desde 1970)
    amostras: int = Field(1, ge=1, le=50)           # nº de leituras de GPS coletadas
    variacao_m: Optional[float] = Field(None, ge=0, allow_inf_nan=False)  # maior distância entre as leituras
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


def _pdf_marca(c, W, H, M) -> None:
    """Ícone do lobo-guará num selo branco, à direita da faixa colorida do topo. Se o arquivo não existir, o PDF sai sem ele."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.utils import ImageReader

        for pasta in (BASE_DIR / "web" / "assets", BASE_DIR / "assets"):
            arq = pasta / "guara-icone.png"
            if arq.is_file():
                img = ImageReader(str(arq))
                iw, ih = img.getSize()
                h = 56.0
                w = h * iw / ih
                bw, bh = w + 16, h + 12
                x, y = W - M - bw, H - 86 + (86 - bh) / 2
                c.setFillColor(colors.white)
                c.roundRect(x, y, bw, bh, 8, stroke=0, fill=1)
                c.drawImage(img, x + 8, y + 6, width=w, height=h, mask="auto")
                return
    except Exception:
        pass


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
    c.setAuthor("Guará Check - UniCerrado")
    c.setSubject("Comprovante de presença")

    c.setFillColor(verde)
    c.rect(0, H - 86, W, 86, stroke=0, fill=1)
    _pdf_marca(c, W, H, M)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(M, H - 42, _l1("Guará Check"))
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
        for lin in _quebrar_texto(valor, "Helvetica-Bold", 12, W - 2 * M):  # parte também um endereço sem espaços
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
    linhas = _quebrar_texto(frase, "Helvetica", 10.5, W - 2 * M - 16)
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


def limpar_nota(s) -> str:
    """Texto da anotação: quebras de linha padronizadas, sem caracteres de controle, sem espaços nas pontas.
    O ZWJ (U+200D) e o ZWNJ (U+200C) ficam: sem eles os emojis compostos (👩‍💻, 👨‍👩‍👧) viravam figuras soltas."""
    s = (s or "").replace("\r\n", "\n").replace("\r", "\n")
    return "".join(ch for ch in s if ch in "\n\t\u200d\u200c" or unicodedata.category(ch)[0] != "C").strip()


def _quebrar_texto(texto: str, fonte: str, tam: float, largura: float) -> list:
    """Quebra o texto em linhas que cabem na largura: respeita as quebras do aluno e parte palavras enormes (links)."""
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfbase.pdfmetrics import stringWidth

    saida = []
    for par in _l1(texto).split("\n"):
        if not par.strip():
            saida.append("")
            continue
        for lin in simpleSplit(par, fonte, tam, largura):
            while stringWidth(lin, fonte, tam) > largura:  # uma palavra maior que a linha inteira
                corte = len(lin)
                while corte > 1 and stringWidth(lin[:corte], fonte, tam) > largura:
                    corte -= 1
                saida.append(lin[:corte])
                lin = lin[corte:]
            saida.append(lin)
    return saida


def gerar_pdf_anotacoes(d: dict) -> bytes:
    """
    Anotações pessoais do aluno em PDF. É um documento SEPARADO do comprovante: cabeçalho azul (o comprovante é verde),
    sem código de validação nem autenticação, e com aviso de que não vale como comprovante de presença.
    d: {"nome", "notas": [{"titulo","tipo","disciplina","evento_em","atualizado_em","texto"}], "emitido_em", "unico"}.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfgen import canvas

    W, H = A4
    M = 48
    azul, cinza, escuro = colors.HexColor("#23589a"), colors.HexColor("#5b6670"), colors.HexColor("#14202b")
    notas = d["notas"]
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(_l1("Minhas anotações" if not d.get("unico") else f"Anotação: {notas[0]['titulo']}"))
    c.setAuthor("Guará Check - UniCerrado")
    c.setSubject("Anotações pessoais do aluno")
    estado = {"y": 0.0, "pagina": 0}
    emitido = d["emitido_em"].astimezone(BRT).strftime("%d/%m/%Y %H:%M")

    def rodape():
        c.setStrokeColor(colors.HexColor("#cfd8d3"))
        c.setLineWidth(0.8)
        c.line(M, 62, W - M, 62)
        c.setFillColor(cinza)
        c.setFont("Helvetica", 8)
        aviso = ("Documento pessoal do aluno. Não vale como comprovante de presença, não tem código de validação e "
                 "não é visto por professores nem pela coordenação. "
                 f"Gerado em {emitido} (horário de Brasília).")
        yy = 50
        for lin in simpleSplit(_l1(aviso), "Helvetica", 8, W - 2 * M - 60):
            c.drawString(M, yy, lin)
            yy -= 10
        c.drawRightString(W - M, 50, _l1(f"Página {estado['pagina']}"))

    def nova_pagina():
        if estado["pagina"]:
            rodape()
            c.showPage()
        estado["pagina"] += 1
        c.setFillColor(azul)
        if estado["pagina"] == 1:
            c.rect(0, H - 86, W, 86, stroke=0, fill=1)
            _pdf_marca(c, W, H, M)
            c.setFillColor(colors.white)
            c.setFont("Helvetica-Bold", 22)
            c.drawString(M, H - 42, _l1("Guará Check"))
            c.setFont("Helvetica", 12)
            c.drawString(M, H - 62, _l1("Minhas anotações · Centro Universitário de Goiatuba (UniCerrado)"))
            estado["y"] = H - 124
        else:
            c.rect(0, H - 34, W, 34, stroke=0, fill=1)
            c.setFillColor(colors.white)
            c.setFont("Helvetica-Bold", 11)
            c.drawString(M, H - 22, _l1("Guará Check · Minhas anotações"))
            estado["y"] = H - 64

    def garantir(espaco: float):
        if estado["y"] - espaco < 76:
            nova_pagina()

    nova_pagina()
    c.setFillColor(azul)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(M, estado["y"], _l1("ANOTAÇÃO DO EVENTO" if d.get("unico") else "ANOTAÇÕES PESSOAIS"))
    estado["y"] -= 24
    c.setFillColor(cinza)
    c.setFont("Helvetica", 10)
    resumo = f"{d['nome']} · {len(notas)} " + ("anotação" if len(notas) == 1 else "anotações")
    for lin in simpleSplit(_l1(resumo), "Helvetica", 10, W - 2 * M):
        c.drawString(M, estado["y"], lin)
        estado["y"] -= 13
    estado["y"] -= 14

    for n in notas:
        titulos = _quebrar_texto(n["titulo"], "Helvetica-Bold", 12.5, W - 2 * M)
        garantir(len(titulos) * 16 + 14 + 3 * 14)
        c.setFillColor(escuro)
        c.setFont("Helvetica-Bold", 12.5)
        for lin in titulos:
            c.drawString(M, estado["y"], lin)
            estado["y"] -= 16
        quando = aware(n["evento_em"]).astimezone(BRT)
        meta = (("Palestra" if n.get("tipo") == "palestra" else "Aula")
                + (f" · disciplina {n['disciplina']}" if n.get("disciplina") else "")
                + f" · presença em {quando.strftime('%d/%m/%Y às %H:%M')}")
        c.setFillColor(cinza)
        c.setFont("Helvetica", 9)
        for lin in _quebrar_texto(meta, "Helvetica", 9, W - 2 * M):
            c.drawString(M, estado["y"], lin)
            estado["y"] -= 12
        estado["y"] -= 6
        c.setFillColor(escuro)
        for lin in _quebrar_texto(n["texto"], "Helvetica", 10.5, W - 2 * M - 14):
            garantir(14)
            c.setFillColor(azul)
            c.rect(M, estado["y"] - 4, 2.5, 14, stroke=0, fill=1)  # filete à esquerda do texto
            c.setFillColor(escuro)
            c.setFont("Helvetica", 10.5)
            c.drawString(M + 12, estado["y"], lin)
            estado["y"] -= 14
        estado["y"] -= 10
        if n is not notas[-1]:
            garantir(24)
            c.setStrokeColor(colors.HexColor("#d9e0e6"))
            c.setLineWidth(0.6)
            c.line(M, estado["y"] + 4, W - M, estado["y"] + 4)
            estado["y"] -= 18
    rodape()
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
    if not verificacao_email_ativa():
        print("AVISO: confirmação de e-mail por código DESLIGADA (configure EMAIL_PROVEDOR, EMAIL_API_KEY e EMAIL_REMETENTE).")
    elif not email_configurado():
        print("AVISO: VERIFICAR_EMAIL=1, mas o envio de e-mail não está configurado: o cadastro fica indisponível.")
    try:
        semear_coordenacao_provisoria()  # depois da limpeza, para a conta nova não ser apagada
    except Exception as exc:
        print("Coordenação provisória não foi criada:", exc)
    yield


# /docs e /openapi.json desligados: não expõem o mapa da API (nem das rotas da coordenação)
app = FastAPI(title="Guará Check - UniCerrado", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)

# Content-Security-Policy da página: só carrega scripts, estilos, fontes, imagens e mapas dos endereços que o site
# realmente usa (Leaflet no cdnjs, fontes do Google, mapas Esri e OpenStreetMap). Os scripts e estilos da própria
# página são inline, por isso 'unsafe-inline'. Se o site passar a usar outro endereço externo, inclua-o aqui.
CSP_PAGINA = ("default-src 'self'; "
              "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
              "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
              "font-src 'self' https://fonts.gstatic.com; "
              "img-src 'self' data: blob: https://cdnjs.cloudflare.com https://server.arcgisonline.com "
              "https://tile.openstreetmap.org https://*.tile.openstreetmap.org; "
              "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'")


LIMITE_CORPO_API = 8 * 1024 * 1024  # nenhuma requisição da API passa disso (o maior envio é o arquivo de matrículas, 5 MB + base64)


@app.middleware("http")
async def cabecalhos_de_seguranca(request: Request, call_next):
    tam = request.headers.get("content-length") or ""
    if request.url.path.startswith("/api/") and tam.isdigit() and int(tam) > LIMITE_CORPO_API:
        # recusa antes de ler o corpo: ninguém (logado ou não) enche a memória do servidor com um envio gigante
        resp = JSONResponse({"detail": "Envio grande demais."}, status_code=413)
    else:
        resp = await call_next(request)
    h = resp.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")  # o OpenStreetMap exige Referer nos mapas
    h.setdefault("Permissions-Policy", "geolocation=(self), camera=(), microphone=(), payment=()")
    if request.headers.get("x-forwarded-proto", request.url.scheme) == "https":
        h.setdefault("Strict-Transport-Security", "max-age=31536000")
    if (h.get("content-type") or "").startswith("text/html"):
        h.setdefault("Content-Security-Policy", CSP_PAGINA)
    return resp


def _arquivo_index() -> Path:
    for p in (BASE_DIR / "web" / "index.html", BASE_DIR / "index.html"):
        if p.exists():
            return p
    raise HTTPException(404, "index.html não encontrado no repositório")


@app.get("/")
def index():
    return FileResponse(_arquivo_index())


ASSET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}\.(png|jpg|jpeg|webp|svg|ico)$", re.I)


@app.get("/assets/{nome}")
def asset(nome: str):
    """Logos e ícones do site: pasta assets/ ao lado do index.html (web/assets ou assets). Só nomes simples, sem subpastas."""
    if ASSET_RE.fullmatch(nome):
        for pasta in (BASE_DIR / "web" / "assets", BASE_DIR / "assets"):
            arq = pasta / nome
            if arq.is_file():
                return FileResponse(arq, headers={"Cache-Control": "public, max-age=3600"})
    raise HTTPException(404, "Arquivo não encontrado")


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/api/config")
def config(db: Session = Depends(get_db)):
    sem_coordenacao = not ADMIN_EMAILS and not _contar(db, Professor, Professor.coordenacao.is_(True))
    return {"dominio_aluno": DOMINIO_ALUNOS, "dominios_professor": DOMINIOS_PROFESSORES,
            "matricula_regex": MATRICULA_RE.pattern, "coordenacao_aberta": bool(sem_coordenacao),
            "verificacao_email": verificacao_email_ativa()}


@app.get("/api/cursos")
def listar_cursos(db: Session = Depends(get_db)):
    rows = db.execute(select(Curso.id, Curso.nome).where(Curso.ativo.is_(True)).order_by(Curso.nome)).all()
    return [{"id": r.id, "nome": r.nome} for r in rows]


# ------------------------------ Auth ---------------------------------
def email_configurado() -> bool:
    if EMAIL_PROVEDOR == "brevo":
        return bool(EMAIL_API_KEY and EMAIL_REMETENTE)
    if EMAIL_PROVEDOR == "smtp":
        return bool(SMTP_HOST and EMAIL_REMETENTE)
    return False


def verificacao_email_ativa() -> bool:
    """Confirmar o e-mail por código no cadastro? VERIFICAR_EMAIL: auto (liga quando o envio está configurado), 1 ou 0."""
    if VERIFICAR_EMAIL in ("0", "false", "nao", "não", "off"):
        return False
    if VERIFICAR_EMAIL in ("1", "true", "sim", "on"):
        return True
    return email_configurado()


def enviar_email(para: str, assunto: str, texto: str) -> None:
    """Envia um e-mail de texto simples. Levanta exceção se o provedor recusar (quem chama trata)."""
    if EMAIL_PROVEDOR == "brevo":
        corpo = json.dumps({"sender": {"name": EMAIL_REMETENTE_NOME, "email": EMAIL_REMETENTE}, "to": [{"email": para}],
                            "subject": assunto, "textContent": texto}).encode()
        req = urllib.request.Request("https://api.brevo.com/v3/smtp/email", data=corpo, method="POST",
                                     headers={"api-key": EMAIL_API_KEY, "content-type": "application/json",
                                              "accept": "application/json", "user-agent": "GuaraCheck/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            r.read()
    elif EMAIL_PROVEDOR == "smtp":
        import smtplib
        from email.message import EmailMessage
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = f"{EMAIL_REMETENTE_NOME} <{EMAIL_REMETENTE}>", para, assunto
        m.set_content(texto)
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=10) as s:
                if SMTP_USUARIO:
                    s.login(SMTP_USUARIO, SMTP_SENHA)
                s.send_message(m)
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as s:
                s.starttls()
                if SMTP_USUARIO:
                    s.login(SMTP_USUARIO, SMTP_SENHA)
                s.send_message(m)
    else:
        raise RuntimeError("envio de e-mail não configurado")


def _hash_codigo(perfil: str, email: str, codigo: str) -> str:
    return hmac.new(SECRET_KEY.encode(), f"cod|{perfil}|{email}|{codigo}".encode(), hashlib.sha256).hexdigest()


def _emitir_codigo(db: Session, p: "CadastroPendente", primeiro_nome: str) -> None:
    """Gera um código NOVO (o anterior deixa de valer), grava só o hash e envia por e-mail."""
    agora = utcnow()
    codigo = f"{secrets.randbelow(10 ** 6):06d}"
    p.codigo_hash, p.tentativas = _hash_codigo(p.perfil, p.email, codigo), 0
    p.enviado_em, p.expira_em = agora, agora + timedelta(minutes=CODIGO_EMAIL_MIN)
    db.add(p)
    try:
        db.commit()
    except IntegrityError:  # dois pedidos do mesmo e-mail no mesmo instante
        db.rollback()
        raise HTTPException(409, "Já existe um pedido de código para este e-mail. Aguarde alguns segundos e tente de novo.")
    texto = (f"Olá, {primeiro_nome}!\n\nSeu código para confirmar o cadastro no Guará Check é: {codigo}\n\n"
             f"Ele vale por {CODIGO_EMAIL_MIN} minutos. Se você não pediu este cadastro, ignore este e-mail: "
             f"nada é criado sem o código.\n\nGuará Check · UniCerrado")
    try:
        enviar_email(p.email, f"Guará Check: seu código é {codigo}", texto)
    except Exception as exc:  # não imprime o e-mail nem o código
        print("Falha ao enviar o e-mail de confirmação:", type(exc).__name__, exc)
        raise HTTPException(503, "Não foi possível enviar o código agora. Tente de novo em alguns minutos.")


def _espera_restante(p: "CadastroPendente", ip: str) -> int:
    """Segundos até poder pedir outro código. A espera vale só para quem pediu o último (mesmo IP): outra pessoa, de outro
    IP, não consegue segurar o cadastro do dono do e-mail."""
    if p.ip != ip[:64]:
        return 0
    return max(0, int(CODIGO_INTERVALO_S - (utcnow() - aware(p.enviado_em)).total_seconds()))


def _iniciar_verificacao(db: Session, request: Request, b: "RegistroIn", email: str, dados: dict) -> dict:
    """1ª etapa: guarda o cadastro como PENDENTE (sem criar a conta) e envia o código para o e-mail informado."""
    if not email_configurado():
        raise HTTPException(503, "A confirmação de e-mail ainda não foi configurada no servidor. Procure a coordenação.")
    db.execute(delete(CadastroPendente).where(CadastroPendente.expira_em < utcnow()))  # limpa os vencidos
    p = db.scalar(select(CadastroPendente).where(CadastroPendente.perfil == b.perfil, CadastroPendente.email == email))
    ip = ip_cliente(request)
    if p and _espera_restante(p, ip) > 0:
        raise HTTPException(429, f"Aguarde {_espera_restante(p, ip)} segundos para pedir outro código.")
    limitar_tentativas(f"env-em:{email}|{ip}", CODIGO_ENVIOS_HORA, 3600)  # e-mail+IP: um terceiro não esgota a cota do dono
    if not p:
        p = CadastroPendente(perfil=b.perfil, email=email)
    p.dados, p.senha_hash, p.ip = json.dumps(dados), hash_senha(b.senha), ip[:64]
    p.expira_em = utcnow() + timedelta(minutes=CODIGO_EMAIL_MIN)
    partes = (dados.get("nome") or "").split()
    try:
        _emitir_codigo(db, p, partes[0] if partes else "")
    except HTTPException:
        if p.id:  # falhou: não deixa um pedido sem código válido
            db.delete(p)
            db.commit()
        raise
    return {"confirmar": True, "email": email, "validade_min": CODIGO_EMAIL_MIN,
            "mensagem": f"Enviamos um código de 6 números para {email}. Digite-o abaixo para concluir o cadastro. "
                        f"Ele vale por {CODIGO_EMAIL_MIN} minutos."}


@app.post("/api/auth/registro")
def registro(b: RegistroIn, request: Request, db: Session = Depends(get_db)):
    return _registrar(b, request, db)


def _registrar(b: "RegistroIn", request: Request, db: Session, *, senha_hash: Optional[str] = None,
               confirmado: bool = False):
    """Cadastro. Com a confirmação de e-mail ligada, a 1ª chamada só valida tudo e envia o código (nada é criado);
    a conta nasce na confirmação, que chama esta função de novo com confirmado=True e o hash da senha já guardado
    (a chave da coordenação e os freios da 1ª etapa não se repetem)."""
    if b.confirmar_senha is not None and b.confirmar_senha != b.senha:
        raise HTTPException(422, "As senhas não conferem. Digite a mesma senha nos dois campos.")
    nome = nome_limpo(b.nome)
    email = b.email.strip().lower()
    if not email_permitido(email, b.perfil):
        esperado = (f"@{DOMINIO_ALUNOS}" if b.perfil == "aluno"
                    else " ou ".join(f"@{d}" for d in DOMINIOS_PROFESSORES))
        raise HTTPException(403, f"E-mail não autorizado. Use seu e-mail institucional ({esperado}).")

    curso = None
    if b.perfil == "professor":
        if not confirmado:
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
        registro = registro.upper()  # 'doc-2026-08' e 'DOC-2026-08' são o mesmo registro (já validado como ASCII acima)
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
    if b.perfil == "aluno" and not confirmado and not ip_em_rede_permitida(ip_cliente(request)):
        # freio contra criação de contas em massa (alto, por causa do Wi-Fi do campus); vem antes do hash, que é caro
        limitar_tentativas("cad:" + ip_cliente(request), CADASTRO_ALUNO_MAX, CADASTRO_ALUNO_JANELA_S)
    if not confirmado and verificacao_email_ativa():
        dados = {"nome": b.nome, "matricula": b.matricula, "curso_id": b.curso_id, "periodo": b.periodo,
                 "registro_funcional": b.registro_funcional, "departamento": b.departamento}
        return _iniciar_verificacao(db, request, b, email, dados)
    u = Model(nome=nome, email=email, senha_hash=senha_hash or hash_senha(b.senha))
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
        # o professor só entra depois da autorização da coordenação (e-mails de ADMIN_EMAILS já são da coordenação)
        u.aprovado = email in ADMIN_EMAILS
    db.add(u)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # a trava do banco pode ser do e-mail OU da matrícula / registro funcional (dois cadastros ao mesmo tempo)
        if not db.scalar(select(Model.id).where(Model.email == email)):
            if b.perfil == "aluno":
                raise HTTPException(409, "Esta matrícula já está vinculada a outro e-mail. Procure a coordenação.")
            raise HTTPException(409, "Este registro funcional já está cadastrado. Procure a coordenação.")
        raise HTTPException(409, "Este e-mail já possui cadastro. Use a opção Entrar.")
    if b.perfil == "professor":
        pendente = aguardando_autorizacao(u)
        log_seg(db, u.id, "cadastro_professor", f"registro={registro}; {'aguardando autorização' if pendente else 'autorizado'}",
                ip_cliente(request), commit=True)
        if pendente:  # nenhum token é emitido: a pessoa só consegue entrar depois que a coordenação autorizar
            return {"pendente": True, "mensagem": MSG_AGUARDANDO}
    return resposta_token(u, b.perfil, curso.nome if curso else None)


class ConfirmarIn(BaseModel):
    perfil: Literal["aluno", "professor"]
    email: str = Field(max_length=160)
    codigo: str = Field(max_length=20)


class ReenviarIn(BaseModel):
    perfil: Literal["aluno", "professor"]
    email: str = Field(max_length=160)


def _pendente(db: Session, perfil: str, email: str) -> Optional["CadastroPendente"]:
    return db.scalar(select(CadastroPendente).where(CadastroPendente.perfil == perfil, CadastroPendente.email == email))


@app.post("/api/auth/confirmar")
def confirmar_cadastro(b: ConfirmarIn, request: Request, db: Session = Depends(get_db)):
    """2ª etapa do cadastro: confere o código do e-mail e só então cria a conta (o aluno já entra; o professor fica
    aguardando a autorização da coordenação). Código errado: 5 tentativas, depois o pedido é descartado."""
    email, ip = b.email.strip().lower(), ip_cliente(request)
    k_ip = "cf-ip:" + ip
    checar_falhas(k_ip, CONFIRMA_FALHAS_IP, 600)  # só os erros contam: uma turma inteira confirma pela mesma rede
    codigo = re.sub(r"\D", "", b.codigo)
    if len(codigo) != 6:
        raise HTTPException(400, "Digite os 6 números do código que chegou no seu e-mail.")
    p = _pendente(db, b.perfil, email)
    if not p or aware(p.expira_em) < utcnow():
        registrar_falha(k_ip)
        raise HTTPException(400, "Código inválido ou vencido. Volte e peça um novo código.")
    if not hmac.compare_digest(_hash_codigo(b.perfil, email, codigo), p.codigo_hash):
        registrar_falha(k_ip)
        p.tentativas = (p.tentativas or 0) + 1
        if p.tentativas >= CODIGO_MAX_TENTATIVAS:
            db.delete(p)
            db.commit()
            raise HTTPException(400, "Código incorreto várias vezes. Volte e comece o cadastro de novo.")
        db.commit()
        raise HTTPException(400, f"Código incorreto. Você ainda tem {CODIGO_MAX_TENTATIVAS - p.tentativas} tentativa(s).")
    d = json.loads(p.dados)
    dados = RegistroIn(perfil=b.perfil, nome=d.get("nome") or "", email=email, senha="x" * 8, matricula=d.get("matricula"),
                       curso_id=d.get("curso_id"), periodo=d.get("periodo"),
                       registro_funcional=d.get("registro_funcional"), departamento=d.get("departamento"))
    try:  # as regras do cadastro rodam de novo: a matrícula pode ter sido tomada, o curso desativado, etc.
        r = _registrar(dados, request, db, senha_hash=p.senha_hash, confirmado=True)
    except HTTPException:
        db.delete(p)
        db.commit()
        raise
    db.delete(p)
    db.commit()
    return r


@app.post("/api/auth/reenviar-codigo")
def reenviar_codigo(b: ReenviarIn, request: Request, db: Session = Depends(get_db)):
    email = b.email.strip().lower()
    ip = ip_cliente(request)
    limitar_tentativas("env-ip:" + ip, 200, 3600)
    p = _pendente(db, b.perfil, email)
    if not p or aware(p.expira_em) < utcnow():
        raise HTTPException(400, "Nenhum cadastro aguardando confirmação para este e-mail. Preencha o cadastro de novo.")
    if _espera_restante(p, ip) > 0:
        raise HTTPException(429, f"Aguarde {_espera_restante(p, ip)} segundos para pedir outro código.")
    limitar_tentativas(f"env-em:{email}|{ip}", CODIGO_ENVIOS_HORA, 3600)
    partes = (json.loads(p.dados).get("nome") or "").split()
    _emitir_codigo(db, p, partes[0] if partes else "")
    return {"ok": True, "validade_min": CODIGO_EMAIL_MIN, "mensagem": "Enviamos um novo código. O anterior deixou de valer."}


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
    limitar_tentativas(f"esq-em:{email}|{ip}", 3, 900)  # e-mail+IP: um terceiro, de outro IP, não esgota o pedido da dona da conta
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
def login(b: LoginIn, request: Request, db: Session = Depends(get_db)):
    email = b.email.strip().lower()
    if not email_permitido(email, b.perfil):
        raise HTTPException(403, "E-mail não autorizado para este perfil.")
    ip = ip_cliente(request)
    k_ip, k_conta = "lg-ip:" + ip, f"lg-em:{b.perfil}:{email}|{ip}"
    checar_falhas(k_ip, LOGIN_FALHAS_IP, LOGIN_JANELA_S)
    checar_falhas(k_conta, LOGIN_FALHAS_CONTA, LOGIN_JANELA_S)
    Model = Aluno if b.perfil == "aluno" else Professor
    u = db.scalar(select(Model).where(Model.email == email))
    senha_ok = confere_senha(b.senha, u.senha_hash if u else HASH_FALSO)  # sempre gasta o mesmo tempo: não revela se o e-mail existe
    if not u or not senha_ok:
        registrar_falha(k_ip)
        registrar_falha(k_conta)
        raise HTTPException(401, "E-mail ou senha incorretos.")
    esquecer_falhas(k_conta)
    if b.perfil == "professor" and aguardando_autorizacao(u):  # só depois de conferir a senha: não revela o estado a estranhos
        raise HTTPException(403, MSG_AGUARDANDO)
    if b.perfil == "professor" and u.ativo is False:
        raise HTTPException(403, "Conta desativada. Procure a coordenação.")
    if b.perfil == "aluno" and u.status == "inativo":
        raise HTTPException(403, "Matrícula inativa. Procure a coordenação.")
    curso = db.scalar(select(Curso.nome).where(Curso.id == u.curso_id)) if b.perfil == "aluno" and u.curso_id else None
    return resposta_token(u, b.perfil, curso)


# ---------------------------- Professor -------------------------------
_cache_ativas = {"t": 0.0, "v": []}


def aula_aberta(a) -> bool:
    """Aberta de verdade: marcada como ativa E dentro do prazo (AULA_MAX_HORAS). Quem registra presença confere isto."""
    if not a.ativa:
        return False
    if AULA_MAX_HORAS <= 0 or a.aberta_em is None:
        return True
    return aware(a.aberta_em) + timedelta(hours=AULA_MAX_HORAS) > utcnow()


def encerrar_vencidas(db: Session) -> int:
    """Encerra de vez as chamadas que passaram do prazo (a hora de encerramento é a do vencimento, não a de agora)."""
    if AULA_MAX_HORAS <= 0:
        return 0
    prazo = timedelta(hours=AULA_MAX_HORAS)
    vencidas = db.scalars(select(Aula).where(Aula.ativa.is_(True), Aula.aberta_em < utcnow() - prazo)).all()
    for a in vencidas:
        a.ativa, a.encerrada_em = False, aware(a.aberta_em) + prazo
    if vencidas:
        db.commit()
        _cache_ativas["t"] = 0
    return len(vencidas)


def _aulas_ativas(db: Session) -> list:
    if time.monotonic() - _cache_ativas["t"] < 2:
        return _cache_ativas["v"]
    encerrar_vencidas(db)
    # o teto é alto de propósito: o filtro por curso/período é feito depois, por aluno. Com 50, vinte chamadas de um
    # curso escondiam a chamada de outro.
    rows = db.execute(select(Aula, Professor.nome, Curso.nome).join(Professor, Professor.id == Aula.professor_id)
                      .outerjoin(Curso, Curso.id == Aula.curso_id)
                      .where(Aula.ativa.is_(True)).order_by(Aula.id.desc()).limit(AULAS_ATIVAS_MAX)).all()
    _cache_ativas.update(t=time.monotonic(), v=[{**aula_dict(a, curso=cn), "professor": n} for a, n, cn in rows])
    return _cache_ativas["v"]


def _aula_do_professor(db: Session, aula_id: int, prof_id: int) -> Aula:
    a = db.get(Aula, aula_id)
    if not a or a.professor_id != prof_id:
        raise HTTPException(404, "Aula não encontrada.")
    return a


@app.post("/api/aulas")
def abrir_aula(b: AulaIn, u: dict = Depends(so_professor), db: Session = Depends(get_db)):
    titulo = re.sub(r"\s+", " ", b.titulo).strip()  # o Pydantic conta os espaços: '   ' passava e abria evento sem nome
    if len(titulo) < 2:
        raise HTTPException(422, "Informe o nome do evento (mínimo 2 letras ou números).")
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
    a = Aula(professor_id=u["id"], titulo=titulo, latitude=b.latitude, longitude=b.longitude,
             raio_metros=b.raio_metros, tipo=b.tipo, local_tipo=b.local_tipo,
             local_descricao=(b.local_descricao or "").strip() or None, curso_id=b.curso_id,
             periodo=b.periodo, disciplina=(b.disciplina or "").strip() or None,
             codigo_validacao=codigo or gerar_codigo())
    db.add(a)
    try:
        db.commit()
    except IntegrityError:  # dois envios ao mesmo tempo: o banco só aceita uma chamada ativa por professor
        db.rollback()
        _cache_ativas["t"] = 0
        raise HTTPException(409, "A chamada já foi aberta por outro envio. Atualize a tela e confira o histórico.")
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
    """Apaga TODAS as aulas do PROFESSOR LOGADO (nunca as de outros professores) e, em cascata, as presenças delas."""
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
def minhas_aulas(u: dict = Depends(so_professor), db: Session = Depends(get_db), pagina: int = 1):
    """Histórico do professor, do mais novo ao mais antigo, HIST_POR_PAGINA por página (?pagina=2 traz os mais antigos)."""
    encerrar_vencidas(db)
    pagina = min(max(pagina, 1), 10_000)
    rows = db.execute(select(Aula, func.count(Presenca.id), Curso.nome)
                      .outerjoin(Presenca, Presenca.aula_id == Aula.id).outerjoin(Curso, Curso.id == Aula.curso_id)
                      .where(Aula.professor_id == u["id"]).group_by(Aula.id, Curso.nome)
                      .order_by(Aula.id.desc()).limit(HIST_POR_PAGINA).offset((pagina - 1) * HIST_POR_PAGINA)).all()
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
    return {"ativa": aula_aberta(a), "total": total, "limite": LIMITE_PRESENCAS_POR_CONSULTA,
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
    """Evita injeção de fórmula ao abrir o CSV no Excel. Além de = + - @, um TAB ou um CR no começo também
    iniciam fórmula (lista da OWASP) e passavam direto antes."""
    v = str(v or "")
    return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


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
def minhas_presencas(u: dict = Depends(so_aluno), db: Session = Depends(get_db), pagina: int = 1):
    """Comprovantes do aluno, do mais novo ao mais antigo, COMPROVANTES_POR_PAGINA por página (?pagina=2 traz os mais
    antigos). Continuam visíveis depois que a chamada fecha; sem paginação o aluno perdia o acesso ao 21º em diante."""
    pagina = min(max(pagina, 1), 10_000)
    rows = db.execute(
        select(Presenca.id, Presenca.registrado_em, Aula.titulo, Aula.tipo, Aula.local_tipo, Aula.disciplina,
               Aula.periodo, Professor.nome.label("professor"))
        .select_from(Presenca).join(Aula, Aula.id == Presenca.aula_id)
        .join(Professor, Professor.id == Aula.professor_id)
        .where(Presenca.aluno_id == u["id"]).order_by(Presenca.id.desc())
        .limit(COMPROVANTES_POR_PAGINA).offset((pagina - 1) * COMPROVANTES_POR_PAGINA)).all()
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


# ----------------- Anotações PRIVADAS do aluno (só ele vê) -----------------
# Todas as rotas abaixo exigem papel "aluno" e filtram SEMPRE por NotaAluno.aluno_id == u["id"].
# Nenhuma rota de professor ou da coordenação lê esta tabela.
class NotaIn(BaseModel):
    texto: str = Field(max_length=NOTA_MAX)  # vazio apaga a anotação


def _nota_dict(n: NotaAluno) -> dict:
    return {"id": n.id, "presenca_id": n.presenca_id, "titulo": n.evento_titulo, "tipo": n.evento_tipo or "aula",
            "disciplina": n.evento_disciplina, "evento_em": iso(n.evento_em), "texto": n.texto,
            "atualizado_em": iso(n.atualizado_em)}


@app.get("/api/aluno/notas")
def minhas_notas(u: dict = Depends(so_aluno), db: Session = Depends(get_db)):
    """As anotações do próprio aluno, da mais recente para a mais antiga."""
    rows = db.scalars(select(NotaAluno).where(NotaAluno.aluno_id == u["id"])
                      .order_by(NotaAluno.evento_em.desc(), NotaAluno.id.desc()).limit(NOTAS_MAX_PDF)).all()
    return [_nota_dict(n) for n in rows]


@app.put("/api/aluno/presencas/{presenca_id}/nota")
def salvar_nota(presenca_id: int, b: NotaIn, u: dict = Depends(so_aluno), db: Session = Depends(get_db)):
    """Cria ou atualiza a anotação da PRÓPRIA presença. Texto vazio apaga. Se o evento já foi excluído mas a anotação
    existe, ela ainda pode ser editada."""
    limitar_tentativas("nota:" + str(u["id"]), 60, 600)
    texto = limpar_nota(b.texto)
    nota = db.scalar(select(NotaAluno).where(NotaAluno.aluno_id == u["id"], NotaAluno.presenca_id == presenca_id))
    if not texto:
        if nota:
            db.delete(nota)
            db.commit()
        return {"ok": True, "nota": None}
    if nota:
        nota.texto, nota.atualizado_em = texto, utcnow()
    else:
        ev = db.execute(select(Presenca.registrado_em, Aula.titulo, Aula.tipo, Aula.disciplina)
                        .select_from(Presenca).join(Aula, Aula.id == Presenca.aula_id)
                        .where(Presenca.id == presenca_id, Presenca.aluno_id == u["id"])).first()  # só a PRÓPRIA presença
        if not ev:
            raise HTTPException(404, "Presença não encontrada.")
        nota = NotaAluno(aluno_id=u["id"], presenca_id=presenca_id, evento_titulo=ev.titulo, evento_tipo=ev.tipo,
                         evento_disciplina=ev.disciplina, evento_em=aware(ev.registrado_em), texto=texto)
        db.add(nota)
    try:
        db.commit()
    except IntegrityError:  # dois salvamentos ao mesmo tempo criando a mesma anotação
        db.rollback()
        raise HTTPException(409, "Não foi possível salvar agora. Tente de novo.")
    db.refresh(nota)
    return {"ok": True, "nota": _nota_dict(nota)}


@app.delete("/api/aluno/notas/{nota_id}")
def excluir_nota(nota_id: int, u: dict = Depends(so_aluno), db: Session = Depends(get_db)):
    r = db.execute(delete(NotaAluno).where(NotaAluno.id == nota_id, NotaAluno.aluno_id == u["id"]))
    if not r.rowcount:
        raise HTTPException(404, "Anotação não encontrada.")
    db.commit()
    return {"ok": True}


def _pdf_notas(db: Session, u: dict, notas: list, arquivo: str, unico: bool) -> Response:
    nome = db.scalar(select(Aluno.nome).where(Aluno.id == u["id"])) or ""
    try:
        pdf = gerar_pdf_anotacoes({"nome": nome, "emitido_em": utcnow(), "unico": unico, "notas": [
            {"titulo": n.evento_titulo, "tipo": n.evento_tipo, "disciplina": n.evento_disciplina,
             "evento_em": n.evento_em, "atualizado_em": n.atualizado_em, "texto": n.texto} for n in notas]})
    except ImportError:
        raise HTTPException(503, "A geração de PDF não está disponível no servidor. Avise a coordenação.")
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{arquivo}"', "Cache-Control": "no-store"})


@app.get("/api/aluno/notas/pdf")
def notas_pdf_todas(u: dict = Depends(so_aluno), db: Session = Depends(get_db)):
    """PDF com TODAS as anotações do próprio aluno (documento separado do comprovante)."""
    limitar_tentativas("pdfnotas:" + str(u["id"]), 30, 600)
    notas = db.scalars(select(NotaAluno).where(NotaAluno.aluno_id == u["id"])
                       .order_by(NotaAluno.evento_em.desc(), NotaAluno.id.desc()).limit(NOTAS_MAX_PDF)).all()
    if not notas:
        raise HTTPException(404, "Você ainda não tem anotações.")
    return _pdf_notas(db, u, notas, "minhas-anotacoes.pdf", False)


@app.get("/api/aluno/notas/{nota_id}/pdf")
def nota_pdf(nota_id: int, u: dict = Depends(so_aluno), db: Session = Depends(get_db)):
    """PDF de UMA anotação do próprio aluno."""
    limitar_tentativas("pdfnotas:" + str(u["id"]), 30, 600)
    n = db.scalar(select(NotaAluno).where(NotaAluno.id == nota_id, NotaAluno.aluno_id == u["id"]))
    if not n:
        raise HTTPException(404, "Anotação não encontrada.")
    return _pdf_notas(db, u, [n], f"anotacao-evento-{n.presenca_id}.pdf", True)


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
    if not aula_aberta(aula):  # encerrada pelo professor, pela coordenação ou por passar do prazo (AULA_MAX_HORAS)
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
    # Reenvio de quem JÁ está presente (duplo toque, aba antiga): responde como o primeiro envio. Sem isto, o reenvio passava
    # pelo antifraude de novo e podia virar um "fora do raio" ou "GPS suspeito" falso no painel de quem já registrou.
    ja = db.execute(select(Presenca.distancia_m).where(Presenca.aula_id == aula_id, Presenca.aluno_id == u["id"])).first()
    if ja is not None:
        return {"ok": True, "ja_registrado": True, "distancia_m": round(ja.distancia_m or 0, 1), "aula": aula.titulo}

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
    if VPN_MODO != "off" and ip_e_vpn(ip, db.commit) is True:   # db.commit solta a conexão antes da consulta externa
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
        # o professor excluiu o evento entre a checagem e o INSERT (chave estrangeira): não é "aparelho repetido"
        atual = db.get(Aula, aula_id)
        if atual is None:
            raise HTTPException(404, "Aula não encontrada. O evento foi excluído.")
        # duplicata do próprio aluno (duplo clique) -> idempotente; senão, o aparelho já foi usado por outro aluno
        if db.scalar(select(Presenca.id).where(Presenca.aula_id == aula_id, Presenca.aluno_id == u["id"])):
            return {"ok": True, "ja_registrado": True, "distancia_m": round(dist, 1), "aula": atual.titulo}
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
    token = jwt.encode({"sub": str(p.id), "role": "admin", "nome": p.nome, "pv": marca_senha(p.senha_hash),
                        "exp": utcnow() + timedelta(hours=ADMIN_TOKEN_HORAS)}, SECRET_KEY, algorithm="HS256")
    return {"token": token, "usuario": {"id": p.id, "nome": p.nome, "email": p.email, "perfil": "admin", "curso": None}}


@app.post("/api/admin/login")
def admin_login(b: AdminLoginIn, request: Request, db: Session = Depends(get_db)):
    email = sem_acentos(b.email.strip().lower())
    ip = ip_cliente(request)
    # Como no login comum, só as FALHAS contam. Três travas: por IP, por conta+IP (outra pessoa, de outro IP, não consegue
    # trancar a coordenadora para fora) e um teto geral por conta por hora, contra chute espalhado por muitos IPs.
    k_ip, k_conta, k_geral = "adm-ip:" + ip, f"adm-em:{email}|{ip}", "adm-ge:" + email
    checar_falhas(k_ip, ADMIN_FALHAS_IP, 600)
    checar_falhas(k_conta, ADMIN_FALHAS_CONTA, 600)
    checar_falhas(k_geral, ADMIN_FALHAS_GERAL_HORA, 3600)
    p = db.scalar(select(Professor).where(Professor.email == email))
    if not p or p.ativo is False or not eh_coordenacao(p) or not senha_admin_confere(p, b.senha):
        for k in (k_ip, k_conta, k_geral):
            registrar_falha(k)
        log_seg(db, None, "login_admin_falha", email, ip, commit=True)
        raise HTTPException(401, "E-mail ou senha incorretos.")  # mesma resposta para qualquer falha
    esquecer_falhas(k_conta)
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
        p.aprovado = True
    else:
        p = Professor(nome=nome_limpo(b.nome), email=email, senha_hash=hash_senha(b.senha),
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
    encerrar_vencidas(db)  # o contador de chamadas abertas não pode incluir as que já passaram do prazo
    por_curso = db.execute(select(Curso.nome, func.count(Aluno.id)).select_from(Curso)
                           .outerjoin(Aluno, Aluno.curso_id == Curso.id).group_by(Curso.id, Curso.nome)
                           .order_by(Curso.nome)).all()
    eu = db.get(Professor, u["id"])
    return {"senha_provisoria": bool(eu and senha_provisoria_ativa(eu)),
            "alunos": _contar(db, Aluno), "professores": _contar(db, Professor), "aulas": _contar(db, Aula),
            "presencas": _contar(db, Presenca), "aulas_ativas": _contar(db, Aula, Aula.ativa.is_(True)),
            "alunos_sem_curso": _contar(db, Aluno, Aluno.curso_id.is_(None)),
            "alunos_inativos": _contar(db, Aluno, Aluno.status == "inativo"),
            "professores_pendentes": _contar(db, Professor, Professor.aprovado.is_(False),
                                             or_(Professor.coordenacao.is_(None), Professor.coordenacao.is_(False))),
            "matriculas_autorizadas": _contar(db, MatriculasAutorizadas),
            "matriculas_utilizadas": _contar(db, MatriculasAutorizadas, MatriculasAutorizadas.utilizada.is_(True)),
            "matricula_obrigatoria": MATRICULA_OBRIGATORIA,
            "verificacao_email": verificacao_email_ativa(), "email_configurado": email_configurado(),
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
    nome = re.sub(r"\s+", " ", b.nome).strip()  # o Pydantic conta os espaços: '   ' passava e criava curso sem nome
    chave = _chave_curso(nome)
    if len(nome) < 2 or not chave:
        raise HTTPException(422, "Informe o nome do curso (mínimo 2 letras ou números).")
    # 'Gestão da TI', 'GESTAO  DA TI' e 'Gestao da T.I.' são o mesmo curso: a importação por arquivo compara assim, e dois
    # cursos com a mesma chave fariam as matrículas caírem num deles sem aviso
    for existente in db.scalars(select(Curso.nome)):
        if _chave_curso(existente) == chave:
            raise HTTPException(409, f"Já existe um curso com esse nome ({existente}).")
    db.add(Curso(nome=nome, departamento=(b.departamento or "").strip() or None))
    log_seg(db, u["id"], "criar_curso", nome, ip_cliente(request))
    try:
        db.commit()
    except IntegrityError:  # outra requisição criou o mesmo curso no mesmo instante
        db.rollback()
        raise HTTPException(409, "Já existe um curso com esse nome.")
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
             "pendente": aguardando_autorizacao(p), "criado_em": iso(p.criado_em),
             "aulas": n, "admin": eh_coordenacao(p), "fixo_env": p.email in ADMIN_EMAILS} for p, n in rows]


@app.post("/api/admin/professores/{prof_id}/autorizar")
def adm_professor_autorizar(prof_id: int, request: Request, u: dict = Depends(so_admin),
                            db: Session = Depends(get_db)):
    """A coordenação autoriza o cadastro de um professor que está aguardando. Depois disso ele consegue entrar."""
    p = db.get(Professor, prof_id)
    if not p:
        raise HTTPException(404, "Professor não encontrado.")
    if not aguardando_autorizacao(p):
        return {"ok": True, "ja_autorizado": True}  # idempotente: dois cliques ou duas pessoas da coordenação
    p.aprovado = True
    log_seg(db, u["id"], "autorizar_professor", f"{p.email}; registro={p.registro_funcional or '-'}", ip_cliente(request))
    db.commit()
    return {"ok": True}


@app.post("/api/admin/professores/{prof_id}/recusar")
def adm_professor_recusar(prof_id: int, request: Request, u: dict = Depends(so_admin),
                          db: Session = Depends(get_db)):
    """Recusa um cadastro que está aguardando: apaga a conta (a pessoa pode se cadastrar de novo, se for o caso).
    Só vale para cadastro AGUARDANDO; para bloquear quem já foi autorizado, use desativar."""
    p = db.get(Professor, prof_id)
    if not p:
        raise HTTPException(404, "Professor não encontrado.")
    if not aguardando_autorizacao(p):
        raise HTTPException(409, "Este professor já está autorizado. Para bloquear o acesso, desative a conta.")
    db.execute(delete(Professor).where(Professor.id == prof_id, Professor.aprovado.is_(False)))
    log_seg(db, u["id"], "recusar_professor", f"{p.email}; registro={p.registro_funcional or '-'}", ip_cliente(request))
    db.commit()
    esquecer_contas()
    return {"ok": True}


@app.patch("/api/admin/professores/{prof_id}")
def adm_professor_ativo(prof_id: int, b: AtivoIn, request: Request, u: dict = Depends(so_admin),
                        db: Session = Depends(get_db)):
    p = db.get(Professor, prof_id)
    if not p:
        raise HTTPException(404, "Professor não encontrado.")
    if p.id == u["id"] and not b.ativo:
        raise HTTPException(400, "Você não pode desativar a própria conta.")
    p.ativo = b.ativo
    encerradas = 0
    if not b.ativo:  # ele não consegue mais entrar para encerrar: a chamada aberta dele não pode continuar valendo
        encerradas = db.execute(update(Aula).where(Aula.professor_id == p.id, Aula.ativa.is_(True))
                                .values(ativa=False, encerrada_em=utcnow())).rowcount
    log_seg(db, u["id"], "professor_ativo", f"{p.email}: {'ativo' if b.ativo else 'desativado'}"
            + (f"; chamadas encerradas={encerradas}" if encerradas else ""), ip_cliente(request))
    db.commit()
    esquecer_contas()  # desativar vale já: a sessão aberta do professor deixa de funcionar na próxima chamada
    _cache_ativas["t"] = 0
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
    nome = nome_limpo(b.nome)
    if registro or not coord:  # professor sempre precisa de registro; coordenação só se informar
        if not REGISTRO_RE.fullmatch(registro):
            raise HTTPException(422, "Registro funcional inválido (ex.: DOC-2026-08).")
        registro = registro.upper()
        if db.scalar(select(Professor.id).where(Professor.registro_funcional == registro)):
            raise HTTPException(409, "Já existe um professor com esse registro funcional.")
    if len(depto) < 2:
        raise HTTPException(422, "Informe o departamento / área.")
    if db.scalar(select(Professor.id).where(Professor.email == email)):
        raise HTTPException(409, "Já existe um professor com esse e-mail.")
    db.add(Professor(nome=nome, email=email, senha_hash=hash_senha(b.senha),
                     registro_funcional=registro or None, departamento=depto, coordenacao=coord,
                     aprovado=True))  # conta criada pela própria coordenação já nasce autorizada
    log_seg(db, u["id"], "criar_professor", f"{email}; registro={registro or '-'}; coordenacao={coord}", ip_cliente(request))
    try:
        db.commit()
    except IntegrityError:  # e-mail ou registro funcional criado por outra requisição no mesmo instante
        db.rollback()
        raise HTTPException(409, "Já existe um professor com esse e-mail ou registro funcional.")
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
    elif aguardando_autorizacao(p):
        raise HTTPException(409, "Autorize o cadastro deste professor antes de colocá-lo na coordenação.")
    p.coordenacao = b.ativo
    log_seg(db, u["id"], "professor_coordenacao", f"{p.email}: {'coordenação' if b.ativo else 'professor'}",
            ip_cliente(request))
    db.commit()
    esquecer_contas()  # quem saiu da coordenação perde o painel na hora
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
    esquecer_contas()
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
    esquecer_contas()  # status "inativo" vale já para quem está com a sessão aberta
    return {"ok": True}


@app.delete("/api/admin/alunos/{aluno_id}")
def adm_aluno_excluir(aluno_id: int, request: Request, u: dict = Depends(so_admin), db: Session = Depends(get_db)):
    a = db.get(Aluno, aluno_id)
    if not a:
        raise HTTPException(404, "Aluno não encontrado.")
    db.execute(delete(Tentativa).where(Tentativa.aluno_id == aluno_id))
    db.execute(delete(Presenca).where(Presenca.aluno_id == aluno_id))
    db.execute(delete(NotaAluno).where(NotaAluno.aluno_id == aluno_id))  # só apaga: a coordenação nunca lê as anotações
    if a.matricula:  # libera a matrícula na lista autorizada para um novo cadastro
        db.execute(update(MatriculasAutorizadas).where(MatriculasAutorizadas.matricula == a.matricula)
                   .values(utilizada=False))
    db.execute(delete(Aluno).where(Aluno.id == aluno_id))
    log_seg(db, u["id"], "excluir_aluno", f"aluno={aluno_id}; matricula={a.matricula}", ip_cliente(request))
    db.commit()
    esquecer_contas()
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
    esquecer_contas()  # as outras sessões desta conta (senha antiga) caem agora
    return {"ok": True, "token": _resposta_admin(p)["token"]}  # esta sessão continua, com a marca da senha nova


@app.post("/api/admin/senha")
def adm_senha(b: SenhaIn, request: Request, u: dict = Depends(so_admin), db: Session = Depends(get_db)):
    """Não existe 'esqueci a senha' por e-mail: a coordenação redefine e informa a nova senha ao usuário."""
    alvo = db.get(Aluno if b.perfil == "aluno" else Professor, b.id)
    if not alvo:
        raise HTTPException(404, "Usuário não encontrado.")
    alvo.senha_hash = hash_senha(b.nova_senha)
    log_seg(db, u["id"], "redefinir_senha", f"{b.perfil}={b.id}", ip_cliente(request))
    db.commit()
    esquecer_contas()  # quem estava logado com a senha antiga (inclusive um invasor) é desconectado agora
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
    cursos = {_chave_curso(c.nome): c.id for c in db.scalars(select(Curso))}  # mesma chave da importação por arquivo
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
            cid = cursos.get(_chave_curso(partes[1]))
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


# ---- Importação de matrículas por ARQUIVO (CSV, TXT ou XLSX do sistema da faculdade) ----
# Só duas colunas são lidas: matrícula e curso. Nome, CPF, e-mail etc. são ignorados e nunca guardados nem registrados.
ARQ_MAX_BYTES = 5 * 1024 * 1024          # tamanho do arquivo
ARQ_B64_MAX = ARQ_MAX_BYTES * 4 // 3 + 16  # o mesmo limite, já em base64
ARQ_MAX_LINHAS = 20000
ARQ_MAX_COLUNAS = 60
ARQ_XLSX_MAX_DESCOMPACTADO = 60 * 1024 * 1024  # trava contra "zip bomb"

_CAB_MATRICULA = {"matricula", "ra", "ra aluno", "ra do aluno", "registro academico", "reg academico", "registro",
                  "cod aluno", "codigo aluno", "codigo do aluno", "cod do aluno", "n matricula", "no matricula",
                  "nr matricula", "num matricula", "numero matricula", "numero da matricula", "matricula aluno",
                  "matricula do aluno", "id aluno"}
_CAB_MATRICULA_NAO = {"data", "dt", "situacao", "status", "ano", "periodo", "curso", "turno", "tipo", "forma",
                      "ingresso", "anterior"}
_CAB_CURSO = {"curso", "nome curso", "nome do curso", "curso aluno", "curso do aluno", "descricao curso",
              "descricao do curso", "graduacao", "nome da graduacao"}
_CAB_CURSO_NAO = {"codigo", "cod", "id", "situacao", "status", "periodo", "turno", "sigla", "data", "dt", "ingresso",
                  "anterior", "tipo"}
# colunas que nunca viram matrícula nem curso (dados pessoais): a importação recusa em vez de gravar um CPF por engano
_CAB_PESSOAL = {"cpf", "rg", "senha", "nascimento", "telefone", "celular", "email", "mail", "mae", "pai", "endereco"}
_NOME_SEPARADOR = {";": "ponto e vírgula", "\t": "tabulação", ",": "vírgula", "|": "barra vertical"}


def _chave_texto(s) -> str:
    """'Gestão da TI ' e 'GESTAO  DA TI' viram a mesma chave: sem acento, minúsculas, espaços únicos."""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _chave_curso(s) -> str:
    """Chave para comparar nomes de curso: 'Gestão da TI', 'GESTAO  DA T.I.' e 'gestao-da-ti' dão a mesma chave
    (sem acento, sem espaço, sem pontuação). Os cabeçalhos continuam usando _chave_texto, que preserva as palavras."""
    return _chave_texto(s).replace(" ", "")


def _pontos_cab_matricula(c) -> int:
    k = _chave_texto(c)
    toks = set(k.split())
    if not k or toks & _CAB_PESSOAL:
        return 0
    if k in _CAB_MATRICULA:
        return 3
    return 2 if "matricula" in toks and not toks & _CAB_MATRICULA_NAO else 0


def _pontos_cab_curso(c) -> int:
    k = _chave_texto(c)
    toks = set(k.split())
    if not k or toks & _CAB_PESSOAL:
        return 0
    if k in _CAB_CURSO:
        return 3
    return 2 if "curso" in toks and not toks & _CAB_CURSO_NAO else 0


def _parece_matricula(v: str) -> bool:
    """Valor que se comporta como matrícula: formato válido, tem algum dígito, e não é data nem CPF com pontuação."""
    v = (v or "").strip()
    if not v or not any(ch.isdigit() for ch in v) or not matricula_valida(v):
        return False
    return not (re.fullmatch(r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}", v) or re.fullmatch(r"\d{3}\.\d{3}\.\d{3}-\d{2}", v))


def _decodificar_texto(dados: bytes):
    """UTF-8 (com ou sem BOM), UTF-16 (o 'Texto Unicode' do Excel) e Windows-1252 (o 'CSV' do Excel no Brasil)."""
    if dados[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return dados.decode("utf-16"), "utf-16"
        except UnicodeError:
            raise HTTPException(422, "Não consegui ler este arquivo de texto. Salve-o como CSV (UTF-8) e envie de novo.")
    if dados and dados.count(b"\x00") > len(dados) // 4:  # UTF-16 sem BOM, ou um arquivo que não é de texto
        try:
            t = dados.decode("utf-16-le")
            if sum(1 for ch in t if ord(ch) < 256) >= 0.9 * len(t):
                return t, "utf-16"
        except UnicodeError:
            pass
        raise HTTPException(422, "Este arquivo não parece ser texto nem planilha. Envie um CSV, TXT ou XLSX.")
    if dados[:3] == b"\xef\xbb\xbf":
        dados = dados[3:]
    try:
        return dados.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return dados.decode("cp1252", errors="replace"), "windows-1252"


def _detectar_delimitador(texto: str) -> Optional[str]:
    """Escolhe o separador que dá o número de colunas mais constante nas primeiras linhas (None = uma coluna só)."""
    amostra = [l for l in texto.splitlines() if l.strip()][:40]
    melhor, nota_melhor = None, (0, 0)
    for d in (";", "\t", ",", "|"):
        try:
            tams = [len(r) for r in csv.reader(amostra, delimiter=d)]
        except csv.Error:
            continue
        if not tams:
            continue
        moda = max(set(tams), key=lambda t: (tams.count(t), t))
        consistencia = round(tams.count(moda) / len(tams), 2)
        if moda < 2 or consistencia < 0.6:
            continue
        if (consistencia, moda) > nota_melhor:
            melhor, nota_melhor = d, (consistencia, moda)
    return melhor


def _celula_texto(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "sim" if v else "nao"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):  # o Excel guarda 202610405 como número: 202610405.0 -> '202610405'
        return str(int(v)) if v.is_integer() else repr(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()[:10]
    return str(v).strip()


def _ler_xlsx(dados: bytes) -> list:
    try:
        import openpyxl
    except ImportError:
        raise HTTPException(422, "Este servidor ainda não consegue ler planilhas .xlsx. No Excel, use Arquivo > Salvar como > "
                                 "CSV e envie o CSV.")
    try:
        with zipfile.ZipFile(io.BytesIO(dados)) as z:
            itens = z.infolist()
            if len(itens) > 400 or sum(i.file_size for i in itens) > ARQ_XLSX_MAX_DESCOMPACTADO:
                raise HTTPException(413, "A planilha é grande demais para importar. Salve só as colunas necessárias e envie de novo.")
            if "xl/workbook.xml" not in z.namelist():
                raise HTTPException(422, "Este arquivo não é uma planilha do Excel (.xlsx). Envie um CSV, TXT ou XLSX.")
        wb = openpyxl.load_workbook(io.BytesIO(dados), read_only=True, data_only=True)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, "Não consegui abrir a planilha. Ela pode estar protegida por senha ou danificada. "
                                 "Salve como CSV e envie de novo.")
    try:
        for ws in wb.worksheets:
            try:
                ws.reset_dimensions()  # alguns sistemas gravam a dimensão errada e o Excel só mostra 1 linha
            except Exception:
                pass
            linhas = []
            for i, row in enumerate(ws.iter_rows(max_col=ARQ_MAX_COLUNAS, values_only=True)):
                if i >= ARQ_MAX_LINHAS * 2:
                    raise HTTPException(413, f"Importe no máximo {ARQ_MAX_LINHAS:,} linhas por vez.".replace(",", "."))
                linhas.append([_celula_texto(v) for v in row])
            if any(any(c for c in l) for l in linhas):
                return linhas
        return []
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, "Não consegui ler as linhas da planilha. Salve como CSV e envie de novo.")
    finally:
        try:
            wb.close()
        except Exception:
            pass


def _tabela_do_arquivo(dados: bytes):
    """Reconhece o tipo pelo CONTEÚDO (não pelo nome). Devolve (linhas, formato, codificacao, separador)."""
    if dados[:4] == b"PK\x03\x04":
        return _ler_xlsx(dados), "xlsx", None, None
    if dados[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise HTTPException(422, "Este é um arquivo .xls antigo do Excel. Abra-o no Excel, use Arquivo > Salvar como e escolha "
                                 "'Pasta de Trabalho do Excel (.xlsx)' ou 'CSV'.")
    if dados[:4] == b"%PDF":
        raise HTTPException(422, "PDF não pode ser importado. Peça ao sistema da faculdade a exportação em CSV ou Excel.")
    if dados[:4] in (b"\x89PNG", b"GIF8") or dados[:3] == b"\xff\xd8\xff":
        raise HTTPException(422, "Imagem não pode ser importada. Envie um CSV, TXT ou XLSX.")
    texto, cod = _decodificar_texto(dados)
    amostra_txt = texto[:20000]
    if sum(1 for ch in amostra_txt if ord(ch) < 32 and ch not in "\t\r\n\f\x00") > max(3, len(amostra_txt) // 50):
        raise HTTPException(422, "Este arquivo não parece ser texto nem planilha. Envie um CSV, TXT ou XLSX.")
    if texto.lstrip()[:1] == "<" and "<table" in texto[:20000].lower():
        raise HTTPException(422, "Este arquivo é uma página da web (HTML) com extensão de planilha. Abra-o no Excel e salve "
                                 "como CSV ou .xlsx.")
    texto = texto.replace("\x00", "")
    delim = _detectar_delimitador(texto)
    limite = ARQ_MAX_LINHAS * 2
    if delim is None:
        linhas = [[l.strip()] for l in texto.splitlines()[:limite + 1]]
    else:
        linhas = []
        try:
            for r in csv.reader(io.StringIO(texto), delimiter=delim):
                linhas.append(r)
                if len(linhas) > limite:
                    break
        except csv.Error:
            raise HTTPException(422, "Não consegui separar as colunas deste arquivo. Salve-o como CSV e envie de novo.")
    if len(linhas) > limite:
        raise HTTPException(413, f"Importe no máximo {ARQ_MAX_LINHAS:,} linhas por vez.".replace(",", "."))
    return [[c.strip() for c in l[:ARQ_MAX_COLUNAS]] for l in linhas], "csv", cod, delim


def _cpf_valido(v: str) -> bool:
    """CPF com os dois dígitos verificadores certos (com ou sem pontuação). Matrícula comum quase nunca passa nisso."""
    v = (v or "").strip()
    if not re.fullmatch(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}", v):
        return False
    d = re.sub(r"\D", "", v)
    if d == d[0] * 11:
        return False
    for n in (9, 10):
        if (sum(int(d[i]) * (n + 1 - i) for i in range(n)) * 10 % 11) % 10 != int(d[n]):
            return False
    return True


def _coluna_parece_cpf(valores) -> bool:
    """A coluna é de CPF se quase todos os valores (com dígito) são CPFs válidos. Pelo menos 2 valores."""
    vs = [str(v).strip() for v in valores if v and any(ch.isdigit() for ch in str(v))]
    return len(vs) >= 2 and sum(1 for v in vs if _cpf_valido(v)) / len(vs) >= 0.8


def _nota_matricula(dados, j: int) -> float:
    """0 a 1: quanto os valores da coluna j parecem matrículas (formato válido, com dígito, quase todos diferentes)."""
    vals = [l[j].strip() for _, l in dados[:300] if j < len(l) and l[j].strip()]
    if not vals or _coluna_parece_cpf(vals):
        return 0.0
    boas = sum(1 for v in vals if _parece_matricula(v))
    return (boas / len(vals)) * (len({v.upper() for v in vals}) / len(vals))


def _coluna_matricula_por_dados(dados, ncols: int, fora=frozenset()) -> Optional[int]:
    """Sem cabeçalho: a coluna cujos valores mais parecem matrículas (menos as colunas em `fora`, de dados pessoais)."""
    melhor, nota_melhor = None, 0.5
    for j in range(ncols):
        if j in fora:
            continue
        nota = _nota_matricula(dados, j)
        if nota >= nota_melhor and (melhor is None or nota > nota_melhor):
            melhor, nota_melhor = j, nota
    return melhor


def _ler_matriculas(linhas, cursos: dict, padrao, mapa: dict, col_mat=None, col_curso=None) -> dict:
    """Interpreta a tabela: acha o cabeçalho e as colunas de matrícula e curso (ou usa a escolha da coordenação) e
    devolve as matrículas únicas com o curso de cada uma. Não mexe no banco."""
    corpo = [(n, l) for n, l in enumerate(linhas, 1) if any(c.strip() for c in l)]
    if not corpo:
        raise HTTPException(422, "O arquivo está vazio.")
    ncols = max(len(l) for _, l in corpo)
    # cabeçalho pelo NOME das colunas (olha as 10 primeiras linhas: alguns sistemas põem um título antes)
    cab_pos, melhor = None, 0
    for pos, (_, l) in enumerate(corpo[:10]):
        pts = max((_pontos_cab_matricula(c) for c in l), default=0)
        if pts > melhor:
            cab_pos, melhor = pos, pts
    nomes = None
    if cab_pos is not None:
        nomes = list(corpo[cab_pos][1]) + [""] * (ncols - len(corpo[cab_pos][1]))
        dados = corpo[cab_pos + 1:]
        # várias colunas com nome de matrícula (ex.: 'RA' e 'Matrícula'): vale a que tem cara de matrícula nos dados
        auto_mat = max((j for j in range(ncols) if _pontos_cab_matricula(nomes[j]) > 0),
                       key=lambda j: (_nota_matricula(dados, j) >= 0.5, _pontos_cab_matricula(nomes[j]), _nota_matricula(dados, j), -j))
    else:
        dados = corpo
        primeira = corpo[0][1]  # sem cabeçalho reconhecido: a 1ª linha pode ser um cabeçalho só de dados pessoais ("CPF")
        fora = {j for j in range(ncols) if j < len(primeira) and set(_chave_texto(primeira[j]).split()) & _CAB_PESSOAL}
        auto_mat = _coluna_matricula_por_dados(dados, ncols, fora)

    def nome_col(j):
        return nomes[j] if nomes and j < len(nomes) else ""

    def pessoal(j):
        """Coluna de dado pessoal (CPF, e-mail, telefone...): pelo cabeçalho (mesmo sem cabeçalho reconhecido, olha a
        1ª linha) ou, no caso do CPF, pelo conteúdo."""
        rotulo = nome_col(j) or (corpo[0][1][j] if cab_pos is None and j < len(corpo[0][1]) else "")
        return bool(set(_chave_texto(rotulo).split()) & _CAB_PESSOAL) or \
            _coluna_parece_cpf([l[j] for _, l in dados[:300] if j < len(l)])

    if col_mat is not None:
        if not 0 <= col_mat < ncols:
            raise HTTPException(422, "A coluna escolhida para a matrícula não existe neste arquivo.")
        if pessoal(col_mat):
            raise HTTPException(422, f"A coluna '{nome_col(col_mat)[:40] or col_mat + 1}' parece ter dados pessoais (CPF, e-mail, "
                                     "telefone...) e não é importada. Escolha a coluna da matrícula.")
        mat = col_mat
    else:
        mat = auto_mat
    tem_cab_nome = cab_pos is not None
    if mat is None:
        cols = [nome_col(j) or f"Coluna {j + 1}" for j in range(ncols)]
        return {"precisa_coluna": True, "tem_cabecalho": False, "colunas": [c[:60] for c in cols],
                "linhas_lidas": len(dados), "amostra_bruta": [[c[:30] for c in l[:8]] for _, l in dados[:4]]}
    # sem nome de coluna conhecido: se a 1ª linha não tem dígito na coluna da matrícula, ela é o cabeçalho
    implicito = False
    if cab_pos is None and dados:
        primeira = dados[0][1]
        if not any(ch.isdigit() for ch in (primeira[mat] if mat < len(primeira) else "")):
            implicito = True
            nomes = list(primeira) + [""] * (ncols - len(primeira))
            dados = dados[1:]
    # coluna do curso
    if col_curso is not None:
        if col_curso == -1:
            curso_col = -1
        elif not 0 <= col_curso < ncols or col_curso == mat:
            raise HTTPException(422, "A coluna escolhida para o curso não existe neste arquivo ou é a mesma da matrícula.")
        elif pessoal(col_curso):
            raise HTTPException(422, f"A coluna '{nome_col(col_curso)[:40] or col_curso + 1}' parece ter dados pessoais e não pode ser o curso.")
        else:
            curso_col = col_curso
    else:
        curso_col = -1
        if nomes:
            cand = max((j for j in range(ncols) if j != mat), key=lambda j: (_pontos_cab_curso(nome_col(j)), -j), default=None)
            if cand is not None and _pontos_cab_curso(nome_col(cand)) > 0:
                curso_col = cand
        if curso_col == -1 and cab_pos is None and not implicito and ncols == 2 and mat in (0, 1):
            # lista simples "matrícula;curso" sem cabeçalho (formato da importação por texto)
            cand = 1 - mat
            vs = [l[cand].strip() for _, l in dados if cand < len(l) and l[cand].strip()]
            if vs and len({_chave_texto(v) for v in vs}) <= max(20, len(vs) // 3):
                curso_col = cand
    itens, repetidas, sem_matricula = {}, 0, 0
    invalidas, n_invalidas, desconhecidos, amostra = [], 0, {}, []
    for n, l in dados:
        bruto = l[mat].strip() if mat < len(l) else ""
        if not bruto:
            sem_matricula += 1
            continue
        ctexto = l[curso_col].strip() if 0 <= curso_col < len(l) else ""
        if re.fullmatch(r"\d+[.,]0+", bruto):  # '202610405.0': sobra de planilha que tratou a matrícula como número
            bruto = re.split(r"[.,]", bruto)[0]
        mat_n = normalizar_matricula(bruto)
        valida = matricula_valida(mat_n)
        if len(amostra) < 6:
            amostra.append({"matricula": bruto[:30], "curso": ctexto[:60], "valida": valida})
        if not valida:
            n_invalidas += 1
            if len(invalidas) < 15:
                cientifica = re.fullmatch(r"\d+[.,]?\d*E[+-]?\d+", mat_n) is not None
                invalidas.append(f"linha {n}: matrícula em notação científica ({bruto[:20]}); no Excel, formate a coluna como "
                                 "Texto e salve de novo" if cientifica else f"linha {n}: matrícula inválida ({mat_n[:20]})")
            continue
        if mat_n in itens:
            repetidas += 1
            continue
        cid = padrao
        chave = _chave_curso(ctexto)
        if chave:
            if chave in mapa:
                cid = mapa[chave] if mapa[chave] is not None else padrao
            elif chave in cursos:
                cid = cursos[chave][0]
            else:
                d = desconhecidos.setdefault(chave, [ctexto[:60], 0])
                d[1] += 1
        itens[mat_n] = cid
    colunas = [(nome_col(j) or f"Coluna {j + 1}")[:60] for j in range(ncols)]
    return {"precisa_coluna": False, "tem_cabecalho": tem_cab_nome or implicito, "colunas": colunas, "col_matricula": mat,
            "col_curso": curso_col, "linhas_lidas": len(dados), "itens": itens, "repetidas": repetidas,
            "sem_matricula": sem_matricula, "n_invalidas": n_invalidas, "exemplos_invalidas": invalidas,
            "desconhecidos": desconhecidos, "amostra": amostra}


class ArquivoMatriculasIn(BaseModel):
    nome: str = Field("", max_length=200)
    dados: str = Field(max_length=ARQ_B64_MAX)  # o arquivo em base64
    confirmar: bool = False                     # False = só a prévia (nada é gravado)
    curso_id: Optional[int] = None              # curso para as linhas sem curso (ou com curso não reconhecido)
    col_matricula: Optional[int] = Field(None, ge=0, le=ARQ_MAX_COLUNAS)
    col_curso: Optional[int] = Field(None, ge=-1, le=ARQ_MAX_COLUNAS)  # -1 = não usar o curso do arquivo
    mapa_cursos: Dict[str, Optional[int]] = Field(default_factory=dict)  # curso do arquivo -> curso daqui
    atualizar_cursos: bool = False              # corrige o curso das matrículas que já estavam na lista (e não foram usadas)


@app.post("/api/admin/matriculas/arquivo")
def adm_matriculas_arquivo(b: ArquivoMatriculasIn, request: Request, u: dict = Depends(so_admin),
                           db: Session = Depends(get_db)):
    """Prévia (confirmar=False) e importação (confirmar=True) de matrículas a partir de um arquivo CSV, TXT ou XLSX."""
    limitar_tentativas(f"arqmat:{u['id']}", 40, 600)
    if len(b.mapa_cursos) > 500:
        raise HTTPException(422, "Correspondências de curso demais.")
    try:
        dados = base64.b64decode(b.dados, validate=True)
    except ValueError:
        raise HTTPException(422, "Não consegui ler o arquivo enviado. Escolha o arquivo de novo.")
    if not dados:
        raise HTTPException(422, "O arquivo está vazio.")
    if len(dados) > ARQ_MAX_BYTES:
        raise HTTPException(413, "Arquivo grande demais (máximo 5 MB).")
    padrao = None
    if b.curso_id is not None:
        if not db.get(Curso, b.curso_id):
            raise HTTPException(422, "Curso inválido.")
        padrao = b.curso_id
    cursos_db = {c.id: c.nome for c in db.scalars(select(Curso))}
    cursos = {_chave_curso(nome): (cid, nome) for cid, nome in cursos_db.items()}
    mapa = {}
    for k, v in b.mapa_cursos.items():
        if v is not None and v not in cursos_db:
            raise HTTPException(422, "Curso inválido na correspondência de cursos.")
        mapa[_chave_curso(k)] = v
    linhas, formato, cod, delim = _tabela_do_arquivo(dados)
    r = _ler_matriculas(linhas, cursos, padrao, mapa, b.col_matricula, b.col_curso)
    base = {"formato": formato, "codificacao": cod, "separador": _NOME_SEPARADOR.get(delim), "importado": False}
    if r["precisa_coluna"]:
        if b.confirmar:
            raise HTTPException(422, "Não achei a coluna da matrícula. Escolha qual coluna é a matrícula e confira a prévia.")
        return {**base, **r}
    if len(r["itens"]) + r["repetidas"] + r["n_invalidas"] > ARQ_MAX_LINHAS:
        raise HTTPException(413, f"Importe no máximo {ARQ_MAX_LINHAS:,} linhas por vez.".replace(",", "."))
    itens = r["itens"]
    M = MatriculasAutorizadas
    chaves = list(itens)
    existentes, com_aluno = {}, set()
    for i in range(0, len(chaves), 500):
        bloco = chaves[i:i + 500]
        for mat, cid, usada in db.execute(select(M.matricula, M.curso_id, M.utilizada).where(M.matricula.in_(bloco))):
            existentes[mat] = (cid, usada)
        com_aluno.update(db.scalars(select(Aluno.matricula).where(Aluno.matricula.in_(bloco))))
    novas = [m for m in chaves if m not in existentes]
    atualizaveis = {m: itens[m] for m, (cid, usada) in existentes.items() if not usada and itens[m] is not None and itens[m] != cid}
    por_curso = {}
    for cid in itens.values():
        nome = cursos_db.get(cid, "Qualquer curso") if cid is not None else "Qualquer curso"
        por_curso[nome] = por_curso.get(nome, 0) + 1
    desc = sorted(r["desconhecidos"].items(), key=lambda kv: -kv[1][1])
    resumo = {**base, "precisa_coluna": False, "tem_cabecalho": r["tem_cabecalho"], "colunas": r["colunas"],
              "col_matricula": r["col_matricula"], "col_curso": r["col_curso"], "linhas_lidas": r["linhas_lidas"],
              "novas": len(novas), "ja_existiam": len(existentes), "atualizaveis": len(atualizaveis),
              "repetidas": r["repetidas"], "sem_matricula": r["sem_matricula"], "invalidas": r["n_invalidas"],
              "exemplos_invalidas": r["exemplos_invalidas"], "amostra": r["amostra"],
              "por_curso": [{"curso": k, "quantidade": v} for k, v in sorted(por_curso.items(), key=lambda kv: -kv[1])],
              "n_cursos_desconhecidos": len(desc),
              "cursos_desconhecidos": [{"chave": k, "nome": v[0], "quantidade": v[1]} for k, v in desc[:40]]}
    if not b.confirmar:
        return resumo
    for mat in novas:
        # se já existe aluno com essa matrícula, ela entra como utilizada (não permite um 2º cadastro)
        db.add(M(matricula=mat, curso_id=itens[mat], utilizada=mat in com_aluno))
    atualizadas = 0
    if b.atualizar_cursos and atualizaveis:
        por_id = {}
        for mat, cid in atualizaveis.items():
            por_id.setdefault(cid, []).append(mat)
        for cid, mats in por_id.items():
            for i in range(0, len(mats), 500):
                db.execute(update(M).where(M.matricula.in_(mats[i:i + 500]), M.utilizada.is_(False)).values(curso_id=cid))
        atualizadas = len(atualizaveis)
    nome_arq = re.sub(r"\d[\d.\-/]{5,}", "…", re.sub(r"[^\w.\- ]", "_", b.nome))[:60]  # sem números longos (CPF, matrícula) no log
    log_seg(db, u["id"], "importar_matriculas",
            f"arquivo {nome_arq or '(sem nome)'} ({formato}): {len(novas)} nova(s); {len(existentes)} já existiam; "
            f"{atualizadas} curso(s) atualizado(s); {r['n_invalidas']} inválida(s)", ip_cliente(request))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Outra importação estava em andamento. Tente de novo.")
    return {**resumo, "importado": True, "adicionadas": len(novas), "atualizadas": atualizadas}


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
def adm_aulas(u: dict = Depends(so_admin), db: Session = Depends(get_db), pagina: int = 1):
    """Eventos abertos primeiro (um esquecido aberto nunca some do painel), depois os mais novos; 60 por página."""
    encerrar_vencidas(db)
    pagina = min(max(pagina, 1), 10_000)
    # Primeiro escolhe as 60 aulas da página; só então conta as presenças DELAS. Contar antes (JOIN + GROUP BY em todas as aulas)
    # lia o histórico inteiro a cada abertura da aba: 163 ms com 1.600 aulas e 125 mil presenças, e crescendo a cada semestre.
    rows = db.execute(select(Aula, Professor.nome, Curso.nome)
                      .join(Professor, Professor.id == Aula.professor_id)
                      .outerjoin(Curso, Curso.id == Aula.curso_id)
                      .order_by(Aula.ativa.desc(), Aula.id.desc())
                      .limit(60).offset((pagina - 1) * 60)).all()
    ids = [a.id for a, _, _ in rows]
    totais = dict(db.execute(select(Presenca.aula_id, func.count(Presenca.id)).where(Presenca.aula_id.in_(ids))
                             .group_by(Presenca.aula_id)).all()) if ids else {}
    return [{**aula_dict(a, totais.get(a.id, 0), cn), "professor": nome} for a, nome, cn in rows]


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
