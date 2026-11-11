# -*- coding: utf-8 -*-
"""
QA - Funcionalidade 1: CADASTRO E LOGIN (Guará Check)

Como funciona: o teste NÃO reescreve a lógica. Ele lê o app.py, extrai (via ast) o código REAL das funções de
autenticação, das rotas /api/auth/*, /api/admin/login|registro e dos schemas Pydantic, e executa esse código contra
um banco em memória que imita as consultas do SQLAlchemy usadas por elas. Assim dá para rodar mesmo sem
FastAPI/SQLAlchemy instalados. (Com tudo instalado, o ideal é complementar com TestClient + SQLite.)

Uso:  pip install pytest pydantic pyjwt
      pytest test_func1_autenticacao.py -v          (app.py na mesma pasta, na pasta acima, ou GC_APP_PY=caminho)
"""
import ast
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import statistics
import time as _time
import types
import unicodedata
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Literal, Optional

import jwt
import pytest
from pydantic import BaseModel, Field, ValidationError


# =====================================================================================================
# 1) Localiza e extrai o código real do app.py
# =====================================================================================================
def _achar_app() -> Path:
    if os.getenv("GC_APP_PY"):
        return Path(os.environ["GC_APP_PY"])
    aqui = Path(__file__).resolve().parent
    for p in (aqui / "app.py", aqui.parent / "app.py", Path("app.py")):
        if p.exists():
            return p
    raise FileNotFoundError("app.py não encontrado (defina GC_APP_PY)")


APP = _achar_app()

NOMES = {
    "utcnow", "normalizar_matricula", "matricula_valida", "log_seg", "email_permitido", "hash_senha", "confere_senha",
    "HASH_FALSO", "sem_acentos", "_forma_simples", "senha_provisoria_ativa", "senha_admin_confere",
    "aguardando_autorizacao", "MSG_AGUARDANDO", "resposta_token", "_cache_contas", "CONTA_CACHE_S", "esquecer_contas",
    "conta_liberada", "usuario_atual", "so_aluno", "so_professor", "_prof_ou_admin", "so_admin", "eh_coordenacao",
    "RegistroIn", "LoginIn", "EsqueciIn", "AdminLoginIn", "AdminRegistroIn", "registro", "esqueci_senha", "login",
    "admin_login", "admin_registro", "_resposta_admin", "_contar", "ip_cliente", "_tentativas", "limitar_tentativas",
    "LOGIN_FALHAS_IP", "LOGIN_FALHAS_CONTA", "LOGIN_JANELA_S", "checar_falhas", "registrar_falha", "esquecer_falhas",
    "EMAIL_RE", "nome_limpo", "ip_em_rede_permitida", "adm_professor_novo", "ProfessorIn",
}
ENV_DO_APP = ["SECRET_KEY", "DOMINIO_ALUNOS", "DOMINIOS_PROFESSORES", "CHAVE_MASTER", "CODIGO_PROFESSOR", "TOKEN_HORAS",
              "FUSO_HORAS", "PRECISAO_MAX_M", "IDADE_MAX_POSICAO_S", "RISCO_MAX", "VELOCIDADE_MAX_MS", "MATRICULA_REGEX",
              "MATRICULA_OBRIGATORIA", "RESET_BANCO", "ADMIN_EMAILS", "ADMIN_PATH", "ADMIN_TOKEN_HORAS",
              "COORD_PROVISORIA", "COORD_EMAIL", "COORD_SENHA", "PROXYCHECK_KEY", "VPN_MODO", "PROXY_HOPS",
              "IPS_PERMITIDOS", "DATABASE_URL", "LOGIN_FALHAS_IP", "LOGIN_FALHAS_CONTA", "CADASTRO_ALUNO_MAX",
              "CADASTRO_ALUNO_JANELA_S", "ADMIN_FALHAS_IP", "ADMIN_FALHAS_CONTA", "ADMIN_FALHAS_GERAL_HORA"]


def _compilar():
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    corpo, config, achou_base = [], True, False
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "BASE_DIR" for t in n.targets):
            achou_base = True
        if config and achou_base:
            if any(isinstance(x, ast.Name) and x.id == "create_engine" for x in ast.walk(n)):
                config = False          # acabou o bloco de configuração (variáveis de ambiente)
            else:
                corpo.append(n)
                continue
        nomes = set()
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)):
            nomes = {n.name}
        elif isinstance(n, ast.Assign):
            nomes = {t.id for t in n.targets if isinstance(t, ast.Name)}
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            nomes = {n.target.id}
        if nomes & NOMES:
            if isinstance(n, ast.FunctionDef):
                n.decorator_list = []   # tira @app.post(...)
            corpo.append(n)
    mod = ast.Module(body=corpo, type_ignores=[])
    ast.fix_missing_locations(mod)
    return compile(mod, str(APP), "exec")


CODIGO = _compilar()


# =====================================================================================================
# 2) Dublês: HTTPException, Request, relógio e um "banco" em memória (só o que as rotas de auth usam)
# =====================================================================================================
class HTTPException(Exception):
    def __init__(self, status_code, detail=None, headers=None):
        super().__init__(f"{status_code}: {detail}")
        self.status_code, self.detail = status_code, detail


class IntegrityError(Exception):
    pass


class FakeReq:
    def __init__(self, ip="203.0.113.10", xff=None, client=True):
        self.headers = {"x-forwarded-for": xff} if xff else {}
        self.client = types.SimpleNamespace(host=ip) if client else None


class FakeTime:
    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def time(self):
        return self.t


class Col:
    def __set_name__(self, owner, name):
        self.owner, self.name = owner, name

    def __get__(self, obj, owner=None):
        return self if obj is None else obj.__dict__.get(self.name)

    def __set__(self, obj, v):
        obj.__dict__[self.name] = v

    __hash__ = object.__hash__

    def __eq__(self, o):
        return Cmp(self, "eq", o)

    def like(self, p):
        return Cmp(self, "like", p)

    def is_(self, v):
        return Cmp(self, "is", v)

    def in_(self, vs):
        return Cmp(self, "in", list(vs))


class Cmp:
    def __init__(self, col, op, val):
        self.col, self.op, self.val = col, op, val

    def test(self, row):
        v = getattr(row, self.col.name)
        if self.op == "eq":
            return v is not None and self.val is not None and v == self.val    # SQL: NULL nunca é igual
        if self.op == "like":
            pad = "".join(".*" if c == "%" else "." if c == "_" else re.escape(c) for c in self.val)
            return v is not None and re.fullmatch(pad, v, re.S) is not None
        if self.op == "is":
            return v is self.val
        return v in self.val


class Modelo:
    _padroes: dict = {}
    _unicos: tuple = ()

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class Aluno(Modelo):
    id, nome, email, matricula, curso_id, periodo, status, senha_hash = (Col() for _ in range(8))
    _padroes, _unicos = {"status": "ativo"}, ("email", "matricula")      # UNIQUE(matricula): NULL pode repetir


class Professor(Modelo):
    (id, ativo, coordenacao, aprovado, registro_funcional, departamento, nome, email, senha_hash) = (Col() for _ in range(9))
    _padroes = {"ativo": True, "coordenacao": False, "aprovado": True}
    _unicos = ("email", "registro_funcional")


class Curso(Modelo):
    id, nome, ativo = (Col() for _ in range(3))
    _padroes, _unicos = {"ativo": True}, ("nome",)


class LogSeguranca(Modelo):
    id, usuario_id, acao, detalhe, ip = (Col() for _ in range(5))


class MatriculasAutorizadas(Modelo):
    id, matricula, curso_id, utilizada = (Col() for _ in range(4))


class Func:
    def __init__(self, kind, col=None):
        self.kind, self.col = kind, col


class _FuncNS:
    def max(self, col):
        return Func("max", col)

    def count(self):
        return Func("count")


class Select:
    def __init__(self, *alvos):
        self.alvos, self.conds, self.origem = alvos, [], None

    def where(self, *c):
        self.conds.extend(c)
        return self

    def select_from(self, m):
        self.origem = m
        return self


class FakeDB:
    """Sessão em memória: autoflush desligado (como o SessionLocal do app), e-mail único, ids sequenciais."""

    def __init__(self, tables=None):
        self.tables = tables if tables is not None else {Aluno: [], Professor: [], Curso: [], LogSeguranca: [],
                                                         MatriculasAutorizadas: []}
        self.pending, self.antes_do_commit = [], None

    def add(self, o):
        self.pending.append(o)

    def _persistir(self):
        novos = [o for o in self.pending if o not in self.tables[type(o)]]
        for i, o in enumerate(novos):
            for campo in o._unicos:
                v = getattr(o, campo)
                if v is None:
                    continue                                                  # UNIQUE ignora NULL
                if any(getattr(r, campo) == v for r in self.tables[type(o)]) or \
                        any(type(x) is type(o) and getattr(x, campo) == v for x in novos[:i]):
                    self.pending.clear()
                    raise IntegrityError(f"UNIQUE {campo}={v}")
        for o in novos:
            for k, v in o._padroes.items():
                if getattr(o, k) is None:
                    setattr(o, k, v)
            linhas = self.tables[type(o)]
            o.id = max([r.id for r in linhas] or [0]) + 1
            linhas.append(o)
        self.pending.clear()

    def flush(self):
        self._persistir()

    def commit(self):
        if self.antes_do_commit:
            f, self.antes_do_commit = self.antes_do_commit, None
            f()                                   # simula outra requisição que comita no meio desta
        self._persistir()

    def rollback(self):
        self.pending.clear()

    def get(self, modelo, pk):
        return next((r for r in self.tables[modelo] if r.id == pk), None)

    def _linhas(self, q):
        a = q.alvos[0]
        modelo = (a.col.owner if a.col is not None else q.origem) if isinstance(a, Func) else \
            a.owner if isinstance(a, Col) else a
        return [r for r in self.tables[modelo] if all(c.test(r) for c in q.conds)]

    def scalar(self, q):
        a, linhas = q.alvos[0], self._linhas(q)
        if isinstance(a, Func):
            if a.kind == "count":
                return len(linhas)
            vals = [getattr(r, a.col.name) for r in linhas]
            return max(vals) if vals else None
        if isinstance(a, Col):
            return getattr(linhas[0], a.name) if linhas else None
        return linhas[0] if linhas else None


# =====================================================================================================
# 3) Contexto de teste: código real do app + banco novo + utilitários
# =====================================================================================================
SENHA = "Senha#2026"
SENHA_PROV = "Provisoria#2026"          # COORD_SENHA definida no ambiente (o código não traz mais senha padrão)
CHAVE = "chave-mestra-de-teste"
SEGREDO = "segredo-de-teste-com-mais-de-32-caracteres-ok"
EMAIL_ALUNO = "maria.souza@alunos.unicerrado.edu.br"
EMAIL_PROF = "carlos.lima@unicerrado.edu.br"


def novo_ns(**env):
    base = {"SECRET_KEY": SEGREDO, "CHAVE_MASTER": CHAVE}
    base.update(env)
    guardado = {k: os.environ.pop(k, None) for k in ENV_DO_APP}
    os.environ.update({k: str(v) for k, v in base.items()})
    try:
        ns = dict(
            __file__=str(APP), __name__="app_extraido", os=os, re=re, time=FakeTime(), hashlib=hashlib, hmac=hmac,
            secrets=secrets, ipaddress=ipaddress, json=json, unicodedata=unicodedata, urllib=urllib, jwt=jwt,
            datetime=datetime, timedelta=timedelta, timezone=timezone, Path=Path, Dict=Dict, List=List,
            Literal=Literal, Optional=Optional, BaseModel=BaseModel, Field=Field,
            load_dotenv=lambda *a, **k: None, print=lambda *a, **k: None, HTTPException=HTTPException,
            IntegrityError=IntegrityError, Request=FakeReq, Session=object, Header=lambda *a, **k: None,
            Depends=lambda *a, **k: None, get_db=lambda: None, select=lambda *a: Select(*a), func=_FuncNS(),
            update=None, Aluno=Aluno, Professor=Professor, Curso=Curso, LogSeguranca=LogSeguranca,
            MatriculasAutorizadas=MatriculasAutorizadas)
        exec(CODIGO, ns)
    finally:
        for k in ENV_DO_APP:
            os.environ.pop(k, None)
            if guardado[k] is not None:
                os.environ[k] = guardado[k]
    return ns


def status_de(fn, *a, **k):
    """(status HTTP, conteúdo). ValidationError = 422 do FastAPI; qualquer outra exceção = 500."""
    try:
        return 200, fn(*a, **k)
    except HTTPException as e:
        return e.status_code, e.detail
    except ValidationError as e:
        return 422, e
    except Exception as e:                      # noqa: BLE001 - queremos ver o erro não tratado
        return 500, f"{type(e).__name__}: {e}"


class Ctx:
    def __init__(self, **env):
        self.ns = novo_ns(**env)
        self.db = FakeDB()
        self.curso = Curso(nome="Gestão da TI")
        self.db.add(self.curso)
        self.db.commit()
        self.n = 0

    # --- chamadas às rotas reais ---
    def adm_prof_novo(self, ip="198.51.100.7", **over):
        d = dict(nome="Professor Novo", email="novo.prof@unicerrado.edu.br", senha=SENHA,
                 registro_funcional="DOC-2026-77", departamento="Computação", coordenacao=False)
        d.update(over)
        return status_de(self.ns["adm_professor_novo"], self.ns["ProfessorIn"](**d), FakeReq(ip), {"id": 1, "role": "admin"}, self.db)

    def reg(self, perfil, db=None, ip="198.51.100.7", **campos):
        try:
            b = self.ns["RegistroIn"](perfil=perfil, **campos)
        except ValidationError as e:
            return 422, e
        return status_de(self.ns["registro"], b, FakeReq(ip), db or self.db)

    def reg_aluno(self, db=None, ip="198.51.100.7", **over):
        d = dict(nome="Maria Souza", email=EMAIL_ALUNO, senha=SENHA, confirmar_senha=SENHA, matricula="GTI202610405",
                 curso_id=self.curso.id, periodo=3)
        d.update(over)
        return self.reg("aluno", db, ip, **d)

    def reg_prof(self, db=None, ip="198.51.100.7", **over):
        d = dict(nome="Carlos Lima", email=EMAIL_PROF, senha=SENHA, confirmar_senha=SENHA,
                 registro_funcional="DOC-2026-08", departamento="Computação", chave_master=CHAVE)
        d.update(over)
        return self.reg("professor", db, ip, **d)

    def login(self, perfil, email, senha, ip="198.51.100.7"):
        try:
            b = self.ns["LoginIn"](perfil=perfil, email=email, senha=senha)
        except ValidationError as e:
            return 422, e
        return status_de(self.ns["login"], b, FakeReq(ip), self.db)

    def admin_login(self, email, senha, ip="198.51.100.7"):
        b = self.ns["AdminLoginIn"](email=email, senha=senha)
        return status_de(self.ns["admin_login"], b, FakeReq(ip), self.db)

    def admin_reg(self, ip="198.51.100.7", **over):
        d = dict(nome="Coordenadora Ana", email="ana.coord@unicerrado.edu.br", senha=SENHA, chave_master=CHAVE)
        d.update(over)
        return status_de(self.ns["admin_registro"], self.ns["AdminRegistroIn"](**d), FakeReq(ip), self.db)

    def esqueci(self, perfil, email, ref, ip="198.51.100.7"):
        b = self.ns["EsqueciIn"](perfil=perfil, email=email, referencia=ref)
        return status_de(self.ns["esqueci_senha"], b, FakeReq(ip), self.db)

    def usuario(self, authorization, db=None):
        return status_de(self.ns["usuario_atual"], authorization, db or self.db)

    # --- dados prontos ---
    def semear_aluno(self, email=EMAIL_ALUNO, senha=SENHA, **extra):
        self.n += 1
        d = dict(nome="Aluno Teste", email=email, matricula=f"GTI2026{self.n:05d}", curso_id=self.curso.id, periodo=2,
                 status="ativo", senha_hash=self.ns["hash_senha"](senha))
        d.update(extra)
        a = Aluno(**d)
        self.db.add(a)
        self.db.commit()
        return a

    def semear_prof(self, email=EMAIL_PROF, senha=SENHA, **extra):
        self.n += 1
        d = dict(nome="Professor Teste", email=email, senha_hash=self.ns["hash_senha"](senha),
                 registro_funcional=f"DOC-2026-{self.n:02d}", departamento="Computação")
        d.update(extra)
        p = Professor(**d)
        self.db.add(p)
        self.db.commit()
        return p

    def logs(self, acao):
        return [r for r in self.db.tables[LogSeguranca] if r.acao == acao]

    def token(self, u, perfil, curso=None):
        return self.ns["resposta_token"](u, perfil, curso)["token"]

    def claims(self, tok):
        return jwt.decode(tok, SEGREDO, algorithms=["HS256"])


@pytest.fixture
def ctx():
    return Ctx()


def cod(r):
    return r[0]


# =====================================================================================================
# BLOCO A - Validação de entrada (schemas Pydantic reais)
# =====================================================================================================
def test_ct01_perfil_invalido_e_campos_obrigatorios(ctx):
    base = dict(nome="Maria Souza", email=EMAIL_ALUNO, senha=SENHA)
    for perfil in ("admin", "", "ALUNO", None):
        with pytest.raises(ValidationError):
            ctx.ns["RegistroIn"](perfil=perfil, **base)
    with pytest.raises(ValidationError):
        ctx.ns["RegistroIn"](perfil="aluno", email=EMAIL_ALUNO, senha=SENHA)          # sem nome
    with pytest.raises(ValidationError):
        ctx.ns["LoginIn"](perfil="coordenacao", email=EMAIL_ALUNO, senha=SENHA)


def test_ct02_nome_curto_e_limites_de_tamanho(ctx):
    base = dict(perfil="aluno", email=EMAIL_ALUNO, senha=SENHA)
    for nome in ("Jo", "A", ""):
        with pytest.raises(ValidationError):
            ctx.ns["RegistroIn"](nome=nome, **base)
    ctx.ns["RegistroIn"](nome="Ana", **base)                                          # 3 = limite aceito
    with pytest.raises(ValidationError):
        ctx.ns["RegistroIn"](nome="A" * 121, **base)
    with pytest.raises(ValidationError):
        ctx.ns["RegistroIn"](nome="Maria Souza", perfil="aluno", email="a" * 150 + "@alunos.unicerrado.edu.br", senha=SENHA)


def test_ct03_nome_so_com_espacos_deve_ser_rejeitado(ctx):
    r = ctx.reg_aluno(nome="     ")
    gravado = [a.nome for a in ctx.db.tables[Aluno]]
    assert cod(r) == 422, f"conta criada com nome vazio: nomes gravados = {gravado!r}"
    for ruim in ("\t\n  ", "  A  ", " ab ", "\u00a0\u00a0\u00a0"):
        assert cod(ctx.reg_aluno(nome=ruim, ip=f"198.51.100.{abs(hash(ruim)) % 200 + 1}")) == 422, repr(ruim)
    assert len(ctx.db.tables[Aluno]) == 0


def test_ct04_limites_da_senha(ctx):
    base = dict(perfil="aluno", nome="Maria Souza", email=EMAIL_ALUNO)
    with pytest.raises(ValidationError):
        ctx.ns["RegistroIn"](senha="1234567", **base)
    ctx.ns["RegistroIn"](senha="12345678", **base)
    ctx.ns["RegistroIn"](senha="x" * 128, **base)
    with pytest.raises(ValidationError):
        ctx.ns["RegistroIn"](senha="x" * 129, **base)


def test_ct05_periodo_e_tipos(ctx):
    base = dict(perfil="aluno", nome="Maria Souza", email=EMAIL_ALUNO, senha=SENHA)
    for p in (0, 11, -1):
        with pytest.raises(ValidationError):
            ctx.ns["RegistroIn"](periodo=p, **base)
    for p in (1, 10):
        ctx.ns["RegistroIn"](periodo=p, **base)
    with pytest.raises(ValidationError):
        ctx.ns["RegistroIn"](curso_id="abc", **base)
    with pytest.raises(ValidationError):
        ctx.ns["RegistroIn"](periodo="cinco", **base)


def test_ct06_senha_com_caractere_invalido_nao_pode_gerar_erro_500(ctx):
    # JSON permite "\ud800" (surrogate solto); hash_senha() chama .encode() e estoura UnicodeEncodeError
    r = ctx.reg_aluno(senha="\ud800" * 8, confirmar_senha=None)
    assert cod(r) in (400, 422), f"resposta {r[0]} -> {r[1]}"


# =====================================================================================================
# BLOCO B - E-mail e domínios
# =====================================================================================================
EMAILS_INVALIDOS_ALUNO = [
    "joao@unicerrado.edu.br", "joao@gmail.com", "joao@alunos.unicerrado.edu.br.evil.com",
    "joao@evilalunos.unicerrado.edu.br", "joao@sub.alunos.unicerrado.edu.br", "joao@alunos.unicerrado.edu",
    "joaoalunos.unicerrado.edu.br", "joao@@alunos.unicerrado.edu.br", "@alunos.unicerrado.edu.br",
    "joão@alunos.unicerrado.edu.br", "jo ao@alunos.unicerrado.edu.br", "joao@аlunos.unicerrado.edu.br", "",
    "joao@alunos.unicerrado.edu.br,outro@x.com", "a@b@alunos.unicerrado.edu.br",
]


def test_ct07_dominios_invalidos_de_aluno_sao_rejeitados(ctx):
    aceitos = [e for e in EMAILS_INVALIDOS_ALUNO if ctx.ns["email_permitido"](e.strip().lower(), "aluno")]
    assert aceitos == [], f"aceitos indevidamente: {aceitos}"
    assert cod(ctx.reg_aluno(email="joao@unicerrado.edu.br")) == 403
    assert cod(ctx.reg_aluno(email="joao@alunos.unicerrado.edu.br.evil.com")) == 403


def test_ct08_perfil_e_dominio_cruzados(ctx):
    assert cod(ctx.reg_prof(email="prof@alunos.unicerrado.edu.br")) == 403            # aluno virando professor
    assert cod(ctx.reg_aluno(email="aluno@unicerrado.edu.br")) == 403                 # professor virando aluno
    ctx.semear_aluno()
    ctx.semear_prof()
    assert cod(ctx.login("professor", EMAIL_ALUNO, SENHA)) == 403
    assert cod(ctx.login("aluno", EMAIL_PROF, SENHA)) == 403


def test_ct09_email_com_maiusculas_e_espacos_e_normalizado(ctx):
    r = ctx.reg_aluno(email="  MARIA.SOUZA@ALUNOS.UNICERRADO.EDU.BR  ")
    assert cod(r) == 200, r
    assert [a.email for a in ctx.db.tables[Aluno]] == [EMAIL_ALUNO]


# =====================================================================================================
# BLOCO C - Matrícula
# =====================================================================================================
def test_ct10_normalizacao_e_formato_valido(ctx):
    n, v = ctx.ns["normalizar_matricula"], ctx.ns["matricula_valida"]
    assert n("gti 2026 10405") == "GTI202610405" and n(None) == "" and n("  ab12 ") == "AB12"
    for ok in ("202610405", "GTI202610405", "AB12", "A" * 30, "gti-2026/05.1"):
        assert v(ok), ok
    assert ctx.ns["matricula_valida"]("gti 2026 10405")


def test_ct11_matriculas_invalidas_sao_rejeitadas(ctx):
    v = ctx.ns["matricula_valida"]
    for ruim in ("AB1", "", None, "A" * 31, "GTI@2026", ".ABC123", "-ABC123", "1' OR '1'='1", "<script>1</script>",
                 "GTI2026;DROP"):
        assert not v(ruim), f"aceitou {ruim!r}"
    assert cod(ctx.reg_aluno(matricula="AB1")) == 422
    assert cod(ctx.reg_aluno(matricula=None)) == 422


def test_ct12_matricula_unicode_nao_pode_virar_ascii_escondido(ctx):
    # 'ſ'.upper() == 'S' e 'ı'.upper() == 'I': caracteres que não são [A-Za-z0-9] passam pela validação
    assert not ctx.ns["matricula_valida"]("GTıſ2026"), f"normalizou para {ctx.ns['normalizar_matricula']('GTıſ2026')!r}"


def test_ct13_matricula_duplicada_com_outro_email(ctx):
    assert cod(ctx.reg_aluno()) == 200
    assert cod(ctx.reg_aluno(email="outro@alunos.unicerrado.edu.br")) == 409
    assert cod(ctx.reg_aluno(email="outro@alunos.unicerrado.edu.br", matricula="gti202610405")) == 409
    assert cod(ctx.reg_aluno(email="outro@alunos.unicerrado.edu.br", matricula="gti 2026 10405")) == 409
    assert len(ctx.db.tables[Aluno]) == 1


def test_ct14_corrida_no_cadastro_nao_pode_duplicar_matricula(ctx):
    # A checa a matrícula (livre); antes de A comitar, B se cadastra com a mesma; depois A comita.
    # O app só confere a matrícula por SELECT e a coluna não tem UNIQUE no banco -> as duas entram.
    db_b = FakeDB(tables=ctx.db.tables)
    ctx.db.antes_do_commit = lambda: ctx.reg_aluno(db=db_b, email="b@alunos.unicerrado.edu.br")
    perdedor = ctx.reg_aluno(email="a@alunos.unicerrado.edu.br")
    donos = [a.email for a in ctx.db.tables[Aluno] if a.matricula == "GTI202610405"]
    assert len(donos) == 1, f"mesma matrícula em {len(donos)} contas: {donos}"
    assert cod(perdedor) == 409 and "matrícula" in perdedor[1], perdedor          # erro claro, nunca 500


# =====================================================================================================
# BLOCO D - Cadastro de aluno
# =====================================================================================================
def test_ct15_cadastro_de_aluno_com_sucesso(ctx):
    r = ctx.reg_aluno(email="MARIA.Souza@alunos.unicerrado.edu.br", matricula="gti 202610405", nome="  Maria Souza ")
    assert cod(r) == 200, r
    u = r[1]["usuario"]
    assert u["perfil"] == "aluno" and u["curso"] == "Gestão da TI" and u["periodo"] == 3
    assert u["matricula"] == "GTI202610405" and u["email"] == EMAIL_ALUNO and u["nome"] == "Maria Souza"
    row = ctx.db.tables[Aluno][0]
    assert row.status == "ativo" and row.matricula == "GTI202610405" and row.curso_id == ctx.curso.id
    c = ctx.claims(r[1]["token"])
    assert c["role"] == "aluno" and c["sub"] == str(row.id) and c["cid"] == ctx.curso.id and c["per"] == 3


def test_ct16_senha_guardada_com_hash_pbkdf2(ctx):
    ctx.reg_aluno()
    h = ctx.db.tables[Aluno][0].senha_hash
    assert h.startswith("pbkdf2$") and SENHA not in h and len(h.split("$")) == 3
    assert ctx.ns["confere_senha"](SENHA, h) and not ctx.ns["confere_senha"]("outra-senha", h)


def test_ct17_curso_periodo_e_confirmacao_obrigatorios(ctx):
    assert cod(ctx.reg_aluno(curso_id=None)) == 422
    assert cod(ctx.reg_aluno(curso_id=9999)) == 422
    ctx.curso.ativo = False
    assert cod(ctx.reg_aluno()) == 422                                                # curso inativo
    ctx.curso.ativo = True
    assert cod(ctx.reg_aluno(periodo=None)) == 422
    assert cod(ctx.reg_aluno(confirmar_senha="Diferente#1")) == 422
    assert len(ctx.db.tables[Aluno]) == 0


def test_ct18_email_duplicado_qualquer_caixa(ctx):
    assert cod(ctx.reg_aluno()) == 200
    assert cod(ctx.reg_aluno(email=EMAIL_ALUNO.upper(), matricula="OUTRA12345")) == 409
    assert len(ctx.db.tables[Aluno]) == 1


def test_ct19_cadastro_sem_confirmar_posse_do_email_nao_deveria_logar_direto(ctx):
    # Qualquer pessoa digita o e-mail de um colega + uma matrícula inventada e já recebe token.
    atacante = ctx.reg_aluno(email="colega.vitima@alunos.unicerrado.edu.br", matricula="INVENTADA99")
    vitima = ctx.reg_aluno(email="colega.vitima@alunos.unicerrado.edu.br", matricula="GTI202610777", ip="198.51.100.99")
    assert "token" not in (atacante[1] if isinstance(atacante[1], dict) else {}), \
        f"cadastro devolveu token na hora (sem confirmar o e-mail); a vítima real depois recebe {vitima[0]} 'já possui cadastro'"


def test_ct20_cadastro_de_aluno_tem_limite_por_ip(ctx):
    c = Ctx(CADASTRO_ALUNO_MAX=10)                                                    # limite baixo só para exercitar o freio
    reg = lambda i, ip="198.51.100.7": c.reg_aluno(email=f"aluno{i}@alunos.unicerrado.edu.br", matricula=f"GTI2026{i:05d}", ip=ip)
    codigos = [cod(reg(i)) for i in range(12)]
    assert codigos == [200] * 10 + [429, 429], codigos
    assert cod(reg(50, ip="198.51.100.222")) == 200                                   # outro IP não é afetado
    c.ns["time"].t += 3601
    assert cod(reg(51)) == 200                                                        # a janela vence


# =====================================================================================================
# BLOCO E - Cadastro de professor
# =====================================================================================================
def test_ct21_sem_chave_master_configurada_bloqueia(ctx):
    c = Ctx(CHAVE_MASTER="")
    assert cod(c.reg_prof(chave_master="qualquer")) == 403
    assert cod(c.reg_prof(chave_master="")) == 403
    assert len(c.db.tables[Professor]) == 0


def test_ct22_chave_errada_ou_vazia(ctx):
    for k in ("errada", "", None, CHAVE.upper(), CHAVE + " ", "chave-mestra-de-test"):
        assert cod(ctx.reg_prof(chave_master=k, ip=f"198.51.100.{abs(hash(k)) % 200 + 1}")) == 403, repr(k)
    assert len(ctx.db.tables[Professor]) == 0


def test_ct23_cadastro_de_professor_fica_pendente_sem_token(ctx):
    r = ctx.reg_prof()
    assert cod(r) == 200 and r[1].get("pendente") is True and "token" not in r[1], r
    p = ctx.db.tables[Professor][0]
    assert p.aprovado is False and p.registro_funcional == "DOC-2026-08" and p.departamento == "Computação"
    assert len(ctx.logs("cadastro_professor")) == 1
    assert cod(ctx.login("professor", EMAIL_PROF, SENHA)) == 403                      # ainda não pode entrar


def test_ct24_registro_funcional_e_departamento_obrigatorios(ctx):
    # cada tentativa de um IP diferente, para não esbarrar no freio de 5 por IP (testado no CT-28)
    for i, rf in enumerate(("A1", "", None, "  ", "DOC 2026", "-DOC-26", "D" * 31)):
        assert cod(ctx.reg_prof(registro_funcional=rf, ip=f"198.51.100.{i + 1}")) == 422, repr(rf)
    assert cod(ctx.reg_prof(departamento="X", ip="198.51.100.20")) == 422
    assert cod(ctx.reg_prof(departamento=None, ip="198.51.100.21")) == 422
    assert len(ctx.db.tables[Professor]) == 0


def test_ct25_registro_funcional_duplicado(ctx):
    assert cod(ctx.reg_prof()) == 200
    assert cod(ctx.reg_prof(email="outro.prof@unicerrado.edu.br")) == 409


def test_ct26_registro_funcional_duplicado_com_outra_caixa(ctx):
    assert cod(ctx.reg_prof()) == 200
    r = ctx.reg_prof(email="outro.prof@unicerrado.edu.br", registro_funcional="doc-2026-08")
    assert cod(r) == 409, f"aceitou 'doc-2026-08' como registro diferente de 'DOC-2026-08' ({r[0]})"


def test_ct27_email_de_admin_emails_entra_autorizado(ctx):
    c = Ctx(ADMIN_EMAILS=EMAIL_PROF)
    r = c.reg_prof()
    assert cod(r) == 200 and "token" in r[1] and r[1]["usuario"]["perfil"] == "professor"
    assert c.db.tables[Professor][0].aprovado is True


def test_ct28_freio_por_ip_nas_tentativas_de_cadastro_de_professor(ctx):
    codigos = [cod(ctx.reg_prof(email=f"prof{i}@unicerrado.edu.br", registro_funcional=f"DOC-2026-{i:02d}")) for i in range(6)]
    assert codigos == [200, 200, 200, 200, 200, 429], codigos
    ctx.ns["time"].t += 601
    assert cod(ctx.reg_prof(email="prof9@unicerrado.edu.br", registro_funcional="DOC-2026-99")) == 200   # janela vence
    assert cod(ctx.reg_prof(email="prof8@unicerrado.edu.br", registro_funcional="DOC-2026-98", ip="198.51.100.200")) == 200


# =====================================================================================================
# BLOCO F - Login
# =====================================================================================================
def test_ct29_login_com_sucesso_aluno_e_professor(ctx):
    ctx.semear_aluno()
    ctx.semear_prof()
    ra, rp = ctx.login("aluno", EMAIL_ALUNO, SENHA), ctx.login("professor", EMAIL_PROF, SENHA)
    assert cod(ra) == 200 and ra[1]["usuario"]["perfil"] == "aluno" and ra[1]["usuario"]["curso"] == "Gestão da TI"
    assert cod(rp) == 200 and rp[1]["usuario"]["perfil"] == "professor" and rp[1]["usuario"]["curso"] is None


def test_ct30_token_tem_papel_validade_e_nao_vaza_senha(ctx):
    a, p = ctx.semear_aluno(), ctx.semear_prof()
    ta, tp = ctx.token(a, "aluno", "Gestão da TI"), ctx.token(p, "professor")
    ca, cp = ctx.claims(ta), ctx.claims(tp)
    assert ca["role"] == "aluno" and cp["role"] == "professor" and "cid" not in cp and "per" not in cp
    horas = (datetime.fromtimestamp(ca["exp"], timezone.utc) - datetime.now(timezone.utc)).total_seconds() / 3600
    assert 11.9 < horas <= 12.0, horas
    assert "senha" not in json.dumps(ca).lower() and "pbkdf2" not in json.dumps(ca)
    assert "senha_hash" not in ctx.ns["resposta_token"](a, "aluno")["usuario"]


def test_ct31_senha_errada_e_email_inexistente_dao_a_mesma_resposta(ctx):
    ctx.semear_aluno()
    r1, r2 = ctx.login("aluno", EMAIL_ALUNO, "errada-123"), ctx.login("aluno", "fantasma@alunos.unicerrado.edu.br", SENHA)
    assert r1 == r2 == (401, "E-mail ou senha incorretos.")


def test_ct32_tempo_de_resposta_nao_revela_se_o_email_existe(ctx):
    ctx.semear_aluno()
    medir = lambda f: statistics.median(f(i) for i in range(5))
    def conhecido(i):
        t = _time.perf_counter(); ctx.login("aluno", EMAIL_ALUNO, f"errada-{i}"); return _time.perf_counter() - t
    def fantasma(i):
        t = _time.perf_counter(); ctx.login("aluno", f"fantasma{i}@alunos.unicerrado.edu.br", "errada"); return _time.perf_counter() - t
    razao = medir(conhecido) / medir(fantasma)
    assert 0.6 < razao < 1.7, f"razão de tempos {razao:.2f}"


def test_ct33_professor_pendente_so_e_avisado_com_senha_correta(ctx):
    ctx.semear_prof(aprovado=False)
    r = ctx.login("professor", EMAIL_PROF, SENHA)
    assert cod(r) == 403 and r[1] == ctx.ns["MSG_AGUARDANDO"]
    assert ctx.login("professor", EMAIL_PROF, "errada-123") == (401, "E-mail ou senha incorretos.")   # não revela estado


def test_ct34_professor_desativado_e_aluno_inativo_nao_entram(ctx):
    ctx.semear_prof(ativo=False)
    ctx.semear_aluno(status="inativo")
    assert cod(ctx.login("professor", EMAIL_PROF, SENHA)) == 403
    assert cod(ctx.login("aluno", EMAIL_ALUNO, SENHA)) == 403


def test_ct35_oito_falhas_na_mesma_conta_e_ip_bloqueiam_a_proxima(ctx):
    ctx.semear_aluno()
    assert [cod(ctx.login("aluno", EMAIL_ALUNO, f"errada-{i}")) for i in range(8)] == [401] * 8
    assert cod(ctx.login("aluno", EMAIL_ALUNO, SENHA)) == 429                         # nem a senha certa passa


def test_ct36_falhas_de_um_ip_nao_travam_a_conta_para_outro_ip(ctx):
    ctx.semear_aluno()
    for i in range(8):
        ctx.login("aluno", EMAIL_ALUNO, f"errada-{i}", ip="198.51.100.50")
    assert cod(ctx.login("aluno", EMAIL_ALUNO, SENHA, ip="198.51.100.60")) == 200


def test_ct37_login_correto_zera_as_falhas_da_conta(ctx):
    ctx.semear_aluno()
    for i in range(7):
        ctx.login("aluno", EMAIL_ALUNO, f"errada-{i}")
    assert cod(ctx.login("aluno", EMAIL_ALUNO, SENHA)) == 200
    for i in range(7):
        ctx.login("aluno", EMAIL_ALUNO, f"errada-{i}")
    assert cod(ctx.login("aluno", EMAIL_ALUNO, SENHA)) == 200


def test_ct38_varredura_de_contas_pelo_mesmo_ip_e_bloqueada(ctx):
    ctx.semear_aluno()
    cods = [cod(ctx.login("aluno", f"alvo{i}@alunos.unicerrado.edu.br", "chute-123")) for i in range(30)]
    assert set(cods) == {401}
    assert cod(ctx.login("aluno", EMAIL_ALUNO, SENHA)) == 429                         # o 31º, mesmo certo, é barrado


def test_ct39_janela_de_dez_minutos_libera_o_login(ctx):
    ctx.semear_aluno()
    for i in range(8):
        ctx.login("aluno", EMAIL_ALUNO, f"errada-{i}")
    assert cod(ctx.login("aluno", EMAIL_ALUNO, SENHA)) == 429
    ctx.ns["time"].t += 599
    assert cod(ctx.login("aluno", EMAIL_ALUNO, SENHA)) == 429
    ctx.ns["time"].t += 2
    assert cod(ctx.login("aluno", EMAIL_ALUNO, SENHA)) == 200


def test_ct40_login_aceita_email_com_caixa_e_espacos(ctx):
    ctx.semear_aluno()
    assert cod(ctx.login("aluno", "  Maria.SOUZA@Alunos.Unicerrado.edu.br ", SENHA)) == 200
    assert cod(ctx.login("aluno", EMAIL_ALUNO, "")) == 401                            # senha vazia


# =====================================================================================================
# BLOCO G - Esqueci minha senha
# =====================================================================================================
RESP_ESQUECI = None


def test_ct41_pedido_valido_cria_registro_e_resposta_nao_revela_cadastro(ctx):
    a = ctx.semear_aluno(matricula="GTI202610405")
    ok = ctx.esqueci("aluno", EMAIL_ALUNO, " gti 2026 10405 ")
    nao_existe = ctx.esqueci("aluno", "fantasma@alunos.unicerrado.edu.br", "GTI202610405", ip="198.51.100.20")
    ref_errada = ctx.esqueci("aluno", EMAIL_ALUNO, "GTI000000000", ip="198.51.100.21")
    assert ok == nao_existe == ref_errada and cod(ok) == 200 and ok[1]["ok"] is True
    pedidos = ctx.logs("pedido_senha")
    assert len(pedidos) == 1 and pedidos[0].detalhe.startswith(f"aluno={a.id};")


def test_ct42_pedido_pendente_nao_duplica_e_novo_pedido_apos_redefinir(ctx):
    a = ctx.semear_aluno(matricula="GTI202610405")
    ctx.esqueci("aluno", EMAIL_ALUNO, "GTI202610405", ip="198.51.100.30")
    ctx.esqueci("aluno", EMAIL_ALUNO, "GTI202610405", ip="198.51.100.31")
    assert len(ctx.logs("pedido_senha")) == 1
    ctx.db.add(LogSeguranca(usuario_id=1, acao="redefinir_senha", detalhe=f"aluno={a.id}"))   # coordenação atendeu
    ctx.db.commit()
    ctx.esqueci("aluno", EMAIL_ALUNO, "GTI202610405", ip="198.51.100.32")
    assert len(ctx.logs("pedido_senha")) == 2


def test_ct43_limites_por_email_e_por_ip(ctx):
    for i in range(3):
        assert cod(ctx.esqueci("aluno", "x@alunos.unicerrado.edu.br", "REF1", ip="198.51.100.40")) == 200
    assert cod(ctx.esqueci("aluno", "x@alunos.unicerrado.edu.br", "REF1", ip="198.51.100.40")) == 429   # 4ª do mesmo IP
    c = Ctx()
    for i in range(8):
        assert cod(c.esqueci("aluno", f"u{i}@alunos.unicerrado.edu.br", "REF1", ip="198.51.100.77")) == 200
    assert cod(c.esqueci("aluno", "u9@alunos.unicerrado.edu.br", "REF1", ip="198.51.100.77")) == 429


def test_ct44_terceiro_nao_pode_esgotar_a_cota_do_pedido_da_vitima(ctx):
    ctx.semear_aluno(matricula="GTI202610405")
    for i in range(3):                                                                # atacante, 3 IPs diferentes
        ctx.esqueci("aluno", EMAIL_ALUNO, "chute", ip=f"203.0.113.{i + 1}")
    r = ctx.esqueci("aluno", EMAIL_ALUNO, "GTI202610405", ip="198.51.100.250")        # a dona da conta
    assert cod(r) == 200 and len(ctx.logs("pedido_senha")) == 1, f"a dona da conta recebeu {r[0]}"


def test_ct45_professor_esqueci_senha_ignora_caixa_do_registro(ctx):
    ctx.semear_prof(registro_funcional="DOC-2026-08")
    ctx.esqueci("professor", EMAIL_PROF, "doc-2026-08")
    assert len(ctx.logs("pedido_senha")) == 1
    ctx.esqueci("professor", EMAIL_PROF, "DOC-0000-00", ip="198.51.100.5")
    assert len(ctx.logs("pedido_senha")) == 1


# =====================================================================================================
# BLOCO H - Coordenação (login e primeiro acesso)
# =====================================================================================================
def semear_coord(c, **extra):
    return c.semear_prof(email=c.ns["COORD_EMAIL"], senha=c.ns["COORD_SENHA"], coordenacao=True, **extra)


def ctx_coord():
    """Contexto com COORD_SENHA definida no ambiente e a conta provisória já criada."""
    c = Ctx(COORD_SENHA=SENHA_PROV)
    semear_coord(c)
    return c


def test_ct46_login_da_coordenacao_com_token_admin_de_4_horas():
    ctx = ctx_coord()
    r = ctx.admin_login(ctx.ns["COORD_EMAIL"], ctx.ns["COORD_SENHA"])
    assert cod(r) == 200 and r[1]["usuario"]["perfil"] == "admin"
    c = ctx.claims(r[1]["token"])
    assert c["role"] == "admin" and 3.9 < (datetime.fromtimestamp(c["exp"], timezone.utc) - datetime.now(timezone.utc)).total_seconds() / 3600 <= 4.0
    assert len(ctx.logs("login_admin")) == 1


def test_ct47_falhas_na_coordenacao_dao_a_mesma_resposta_e_sao_auditadas():
    ctx = ctx_coord()
    ctx.semear_prof(email="prof.comum@unicerrado.edu.br")
    ctx.semear_prof(email="coord.off@unicerrado.edu.br", coordenacao=True, ativo=False)
    r = [ctx.admin_login(ctx.ns["COORD_EMAIL"], "errada-123"), ctx.admin_login("prof.comum@unicerrado.edu.br", SENHA),
         ctx.admin_login("coord.off@unicerrado.edu.br", SENHA), ctx.admin_login("nao.existe@unicerrado.edu.br", SENHA)]
    assert set(r) == {(401, "E-mail ou senha incorretos.")}
    assert len(ctx.logs("login_admin_falha")) == 4
    com_acento = ctx.ns["COORD_EMAIL"].replace("coordenacao", "coordenação")
    assert cod(ctx.admin_login(com_acento, ctx.ns["COORD_SENHA"])) == 200            # acento no e-mail é normalizado


def test_ct48_senha_provisoria_tolera_variacoes_so_ate_ser_trocada():
    ctx = ctx_coord()
    p = ctx.db.tables[Professor][0]
    s = ctx.ns["COORD_SENHA"]
    assert cod(ctx.admin_login(ctx.ns["COORD_EMAIL"], s.upper())) == 200
    assert cod(ctx.admin_login(ctx.ns["COORD_EMAIL"], s[:4] + " " + s[4:])) == 200
    p.senha_hash = ctx.ns["hash_senha"]("NovaSenha#2026")                            # coordenação trocou a senha
    assert cod(ctx.admin_login(ctx.ns["COORD_EMAIL"], "NovaSenha#2026")) == 200
    assert cod(ctx.admin_login(ctx.ns["COORD_EMAIL"], s)) == 401
    assert cod(ctx.admin_login(ctx.ns["COORD_EMAIL"], s.upper())) == 401
    assert not ctx.ns["senha_provisoria_ativa"](p)


def test_ct49_terceiro_nao_pode_travar_o_login_da_coordenacao():
    ctx = ctx_coord()
    for i in range(8):                                                                # 8 chutes, de 8 IPs diferentes
        ctx.admin_login(ctx.ns["COORD_EMAIL"], f"chute-{i}", ip=f"203.0.113.{i + 1}")
    r = ctx.admin_login(ctx.ns["COORD_EMAIL"], ctx.ns["COORD_SENHA"], ip="198.51.100.250")   # a coordenadora de verdade
    assert cod(r) == 200, f"a coordenadora com a senha certa recebeu {r[0]}: {r[1]}"


def test_ct50_codigo_nao_deve_trazer_senha_de_coordenacao_utilizavel_por_padrao(ctx):
    assert ctx.ns["COORD_SENHA"] == "", "sem a variável COORD_SENHA não pode existir senha de coordenação"
    # Sem nenhuma variável de ambiente, o app cria uma conta de coordenação com e-mail e senha escritos no código.
    padrao_ativo = ctx.ns["COORD_PROVISORIA"] and bool(ctx.ns["COORD_SENHA"]) and bool(ctx.ns["COORD_EMAIL"])
    assert not padrao_ativo, "COORD_PROVISORIA=1 + e-mail e senha padrão no repositório (vale se COORD_SENHA não foi definida na Render)"


def test_ct51_primeiro_acesso_da_coordenacao(ctx):
    c = Ctx(); r = c.admin_reg()
    assert cod(r) == 200 and r[1]["usuario"]["perfil"] == "admin"
    p = c.db.tables[Professor][0]
    assert p.coordenacao is True and p.departamento == "Coordenação" and len(c.logs("criar_coordenacao")) == 1
    assert cod(c.admin_reg(email="outra@unicerrado.edu.br", ip="198.51.100.2")) == 403           # já existe coordenação
    assert cod(Ctx(CHAVE_MASTER="").admin_reg()) == 403                                           # chave não configurada
    assert cod(Ctx().admin_reg(chave_master="errada")) == 403
    assert cod(Ctx().admin_reg(email="ana@alunos.unicerrado.edu.br")) == 403                      # e-mail de aluno
    assert cod(Ctx().admin_reg(confirmar_senha="outra-senha-1")) == 422


def test_ct52_primeiro_acesso_promove_professor_existente_so_com_a_senha_dele(ctx):
    ctx.semear_prof(email="ana.coord@unicerrado.edu.br", senha="SenhaDaAna#1", aprovado=False)
    assert cod(ctx.admin_reg(senha="OutraSenha#99")) == 409
    r = ctx.admin_reg(senha="SenhaDaAna#1", ip="198.51.100.3")
    p = ctx.db.tables[Professor][0]
    assert cod(r) == 200 and p.coordenacao is True and p.aprovado is True


# =====================================================================================================
# BLOCO I - Sessão (token) e controle de acesso por papel
# =====================================================================================================
def test_ct53_tokens_invalidos_sao_recusados(ctx):
    a = ctx.semear_aluno()
    ok = "Bearer " + ctx.token(a, "aluno")
    assert cod(ctx.usuario(ok)) == 200
    agora = datetime.now(timezone.utc)
    forja = lambda claims, chave=SEGREDO, alg="HS256": "Bearer " + jwt.encode(claims, chave, algorithm=alg)
    base = {"sub": str(a.id), "role": "aluno", "exp": agora + timedelta(hours=1)}
    ruins = {
        "sem header": None, "esquema Basic": "Basic abc", "Bearer sem token": "Bearer", "lixo": "Bearer abc.def.ghi",
        "assinatura de outra chave": forja(base, "outra-chave-bem-longa-com-mais-de-32-caracteres"),
        "alg none": "Bearer " + jwt.encode(base, None, algorithm="none"),
        "expirado": forja({**base, "exp": agora - timedelta(seconds=5)}),
        "sem sub": forja({k: v for k, v in base.items() if k != "sub"}),
        "sub não numérico": forja({**base, "sub": "abc"}),
        "sem role": forja({k: v for k, v in base.items() if k != "role"}),
        "usuário removido": forja({**base, "sub": "999"}),
        "papel desconhecido": forja({**base, "role": "superuser"}),
        "adulterado": ok[:-3] + ("AAA" if not ok.endswith("AAA") else "BBB"),
    }
    liberados = {nome: r for nome, h in ruins.items() if (r := ctx.usuario(h))[0] == 200}
    assert not liberados, f"aceitos indevidamente: {list(liberados)}"


def test_ct54_conta_desativada_perde_acesso_em_ate_5s_ou_na_hora_com_limpeza_de_cache(ctx):
    a = ctx.semear_aluno()
    h = "Bearer " + ctx.token(a, "aluno")
    assert cod(ctx.usuario(h)) == 200
    a.status = "inativo"
    assert cod(ctx.usuario(h)) == 200                                                 # dentro dos 5 s ainda vale (cache)
    ctx.ns["time"].t += 6
    assert cod(ctx.usuario(h)) == 401
    a.status = "ativo"
    ctx.ns["time"].t += 6
    assert cod(ctx.usuario(h)) == 200
    a.status = "inativo"
    ctx.ns["esquecer_contas"]()
    assert cod(ctx.usuario(h)) == 401                                                 # rotas da coordenação limpam o cache


def test_ct55_professor_pendente_ou_desativado_e_admin_sem_coordenacao_nao_usam_token(ctx):
    p = ctx.semear_prof()
    h = "Bearer " + ctx.token(p, "professor")
    assert cod(ctx.usuario(h)) == 200
    p.aprovado = False; ctx.ns["esquecer_contas"]()
    assert cod(ctx.usuario(h)) == 401
    p.aprovado, p.ativo = True, False; ctx.ns["esquecer_contas"]()
    assert cod(ctx.usuario(h)) == 401
    p.ativo = True; ctx.ns["esquecer_contas"]()
    assert cod(ctx.usuario(h)) == 200
    h_admin = "Bearer " + ctx.ns["_resposta_admin"](p)["token"]                       # professor comum com token admin forjado
    assert cod(ctx.usuario(h_admin)) == 401
    p.coordenacao = True; ctx.ns["esquecer_contas"]()
    assert cod(ctx.usuario(h_admin)) == 200


def test_ct56_cache_de_conta_separa_papel_e_id(ctx):
    a, p = ctx.semear_aluno(status="inativo"), ctx.semear_prof()
    assert a.id == p.id == 1
    ha, hp = "Bearer " + ctx.token(a, "aluno"), "Bearer " + ctx.token(p, "professor")
    assert cod(ctx.usuario(ha)) == 401 and cod(ctx.usuario(hp)) == 200 and cod(ctx.usuario(ha)) == 401


def test_ct57_controle_de_acesso_por_papel(ctx):
    u = lambda role: {"id": 1, "role": role}
    ns = ctx.ns
    assert ns["so_aluno"](u("aluno")) and ns["so_professor"](u("professor")) and ns["so_admin"](u("admin"))
    assert ns["_prof_ou_admin"](u("professor")) and ns["_prof_ou_admin"](u("admin"))
    for fn, ruim in (("so_aluno", "professor"), ("so_aluno", "admin"), ("so_professor", "aluno"), ("so_professor", "admin"),
                     ("so_admin", "professor"), ("so_admin", "aluno"), ("_prof_ou_admin", "aluno")):
        assert cod(status_de(ns[fn], u(ruim))) == 403, (fn, ruim)


def test_ct58_token_sem_expiracao_nao_deveria_ser_aceito(ctx):
    a = ctx.semear_aluno()
    h = "Bearer " + jwt.encode({"sub": str(a.id), "role": "aluno"}, SEGREDO, algorithm="HS256")
    assert cod(ctx.usuario(h)) == 401, "token assinado sem 'exp' vale para sempre"


# =====================================================================================================
# BLOCO J - Senha (hash), IP do cliente e desempenho
# =====================================================================================================
def test_ct59_hash_de_senha_formato_salt_e_verificacao(ctx):
    h = ctx.ns["hash_senha"]
    a, b = h(SENHA), h(SENHA)
    assert a != b and a.startswith("pbkdf2$") and len(a.split("$")) == 3
    c = ctx.ns["confere_senha"]
    assert c(SENHA, a) and c(SENHA, b) and not c(SENHA.lower(), a) and not c("", a)
    assert c("senhaçãõ#2026", h("senhaçãõ#2026"))
    for corrompido in ("", "lixo", "pbkdf2$zz$aa", "pbkdf2$00$", "a$b", "a$b$c$d"):
        assert c(SENHA, corrompido) is False, corrompido


def test_ct60_ip_do_cliente_atras_do_proxy(ctx):
    ip = ctx.ns["ip_cliente"]
    assert ip(FakeReq("10.1.1.1")) == "10.1.1.1"
    assert ip(FakeReq("10.1.1.1", xff="200.1.1.1")) == "200.1.1.1"
    assert ip(FakeReq("10.1.1.1", xff="9.9.9.9, 200.1.1.1")) == "200.1.1.1"           # início da lista (forjável) é ignorado
    assert ip(FakeReq(client=False)) == ""


def test_ct61_custo_de_um_hash_de_senha(ctx):
    t = _time.perf_counter()
    for _ in range(3):
        ctx.ns["hash_senha"](SENHA)
    ms = (_time.perf_counter() - t) / 3 * 1000
    print(f"\n[metrica] hash_senha: {ms:.0f} ms por chamada (150.000 iterações PBKDF2-SHA256)")
    assert ms < 1000


# =====================================================================================================
# BLOCO K - Verificação das correções
# =====================================================================================================
def test_ct62_limite_padrao_aguenta_uma_turma_e_rede_do_campus_e_isenta():
    c = Ctx()                                                                         # padrão: 100 contas/hora por IP
    reg = lambda i, ip: c.reg_aluno(email=f"t{i}@alunos.unicerrado.edu.br", matricula=f"GTI2026{i:05d}", ip=ip)
    codigos = [cod(reg(i, "198.51.100.7")) for i in range(101)]
    assert codigos == [200] * 100 + [429], (codigos.count(200), codigos[-1])
    campus = Ctx(CADASTRO_ALUNO_MAX=3, IPS_PERMITIDOS="198.51.100.0/24")              # Wi-Fi do campus em IPS_PERMITIDOS
    assert [cod(campus.reg_aluno(email=f"c{i}@alunos.unicerrado.edu.br", matricula=f"GTI2026{i:05d}", ip="198.51.100.9"))
            for i in range(8)] == [200] * 8


def test_ct63_nome_e_limpo_em_todos_os_cadastros(ctx):
    r = ctx.reg_aluno(nome="  Maria \t  Souza\n ")
    assert cod(r) == 200 and r[1]["usuario"]["nome"] == "Maria Souza"
    c = Ctx()
    assert cod(c.reg_prof(nome="   ")) == 422
    assert cod(Ctx().admin_reg(nome="    ")) == 422
    assert cod(Ctx().adm_prof_novo(nome="   ")) == 422


def test_ct64_coordenacao_cria_professor_com_registro_normalizado(ctx):
    assert cod(ctx.adm_prof_novo(registro_funcional="doc-2026-77")) == 200
    assert ctx.db.tables[Professor][0].registro_funcional == "DOC-2026-77"
    assert cod(ctx.adm_prof_novo(email="b@unicerrado.edu.br", registro_funcional="Doc-2026-77")) == 409
    assert cod(ctx.adm_prof_novo(email="c@unicerrado.edu.br", registro_funcional=None, coordenacao=True)) == 200
    assert cod(ctx.adm_prof_novo(email="d@unicerrado.edu.br", registro_funcional=None, coordenacao=True)) == 200   # NULL repete
    assert cod(ctx.adm_prof_novo(email="e@unicerrado.edu.br", registro_funcional=None)) == 422       # professor exige registro


def test_ct65_indice_unico_do_banco_aceita_null_e_recusa_repetido():
    import re as _re
    import sqlite3
    src = APP.read_text(encoding="utf-8")
    assert "uq_alunos_matricula" in src and "uq_professores_registro" in src
    sql = _re.search(r'text\(f"(CREATE UNIQUE INDEX IF NOT EXISTS .+?)"\)', src).group(1)
    for indice, tabela, coluna in (("uq_alunos_matricula", "alunos", "matricula"),
                                   ("uq_professores_registro", "professores", "registro_funcional")):
        con = sqlite3.connect(":memory:")
        con.execute(f"CREATE TABLE {tabela} (id INTEGER PRIMARY KEY, {coluna} TEXT)")
        con.executemany(f"INSERT INTO {tabela} ({coluna}) VALUES (?)", [(None,), (None,), ("A1",)])   # contas da V1 sem valor
        con.execute(sql.format(indice=indice, tabela=tabela, coluna=coluna))
        con.execute(sql.format(indice=indice, tabela=tabela, coluna=coluna))                           # idempotente
        con.execute(f"INSERT INTO {tabela} ({coluna}) VALUES (NULL)")                                  # NULL continua repetindo
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(f"INSERT INTO {tabela} ({coluna}) VALUES ('A1')")
        con2 = sqlite3.connect(":memory:")                                                             # banco com repetidos
        con2.execute(f"CREATE TABLE {tabela} (id INTEGER PRIMARY KEY, {coluna} TEXT)")
        con2.executemany(f"INSERT INTO {tabela} ({coluna}) VALUES (?)", [("X",), ("X",)])
        with pytest.raises(sqlite3.IntegrityError):                                                    # o app só avisa e segue
            con2.execute(sql.format(indice=indice, tabela=tabela, coluna=coluna))


def test_ct66_teto_geral_da_coordenacao_limita_chute_espalhado_mas_sucesso_nao_conta():
    c = Ctx(COORD_SENHA=SENHA_PROV, ADMIN_FALHAS_GERAL_HORA=10)
    semear_coord(c)
    email = c.ns["COORD_EMAIL"]
    for i in range(10):                                                                # chute vindo de 10 IPs diferentes
        assert cod(c.admin_login(email, f"chute-{i}", ip=f"203.0.113.{i + 1}")) == 401
    assert cod(c.admin_login(email, SENHA_PROV, ip="198.51.100.250")) == 429         # teto da hora atingido
    c.ns["time"].t += 3601
    assert cod(c.admin_login(email, SENHA_PROV, ip="198.51.100.250")) == 200
    c2 = Ctx(COORD_SENHA=SENHA_PROV)
    semear_coord(c2)
    assert [cod(c2.admin_login(c2.ns["COORD_EMAIL"], SENHA_PROV)) for _ in range(20)] == [200] * 20   # sucessos não contam


def test_ct67_sem_coord_senha_no_ambiente_nao_ha_variacao_tolerada(ctx):
    c = Ctx()                                                                          # sem COORD_SENHA
    p = c.semear_prof(email=c.ns["COORD_EMAIL"], senha="Outra#Senha1", coordenacao=True)
    assert cod(c.admin_login(c.ns["COORD_EMAIL"], "Outra#Senha1")) == 200
    assert cod(c.admin_login(c.ns["COORD_EMAIL"], "OUTRA#SENHA1")) == 401
    assert not c.ns["senha_provisoria_ativa"](p)
