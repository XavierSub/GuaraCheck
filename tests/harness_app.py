# -*- coding: utf-8 -*-
"""
Harness compartilhado dos testes de QA do Guará Check.

O teste NÃO reescreve a lógica: lê o app.py, extrai (via ast) o código REAL das funções, rotas e schemas Pydantic
listados em NOMES e executa esse código contra um banco em memória que imita as consultas do SQLAlchemy usadas por elas.
Assim dá para rodar mesmo sem FastAPI/SQLAlchemy instalados. Limite conhecido: consultas com JOIN/GROUP BY (listagens do
painel) NÃO são executadas aqui; para elas o ideal é complementar com TestClient + SQLite quando as dependências existirem.
"""
import ast
import base64
import csv
import hashlib
import hmac
import io
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
import zipfile
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
    "_registrar", "_iniciar_verificacao", "_emitir_codigo", "_espera_restante", "_hash_codigo", "_pendente",
    "confirmar_cadastro", "reenviar_codigo", "ConfirmarIn", "ReenviarIn", "email_configurado", "verificacao_email_ativa",
    "enviar_email", "aware",
    # --- Funcionalidade 2: Coordenação ---
    "iso", "REGISTRO_RE", "_cache_ativas", "CursoIn", "AtivoIn", "AlunoEdicaoIn", "SenhaIn", "MatriculasIn",
    "MinhaSenhaIn", "ArquivoMatriculasIn", "_pedidos_senha", "adm_pedidos_senha", "adm_curso_novo", "adm_curso_ativo",
    "adm_professor_autorizar", "adm_professor_recusar", "adm_professor_ativo", "adm_professor_coordenacao",
    "adm_professor_excluir", "adm_aluno_editar", "adm_aluno_excluir", "adm_minha_senha", "adm_senha",
    "adm_matriculas_importar", "adm_matriculas_arquivo", "adm_matricula_excluir", "listar_cursos",
    "ARQ_MAX_BYTES", "ARQ_B64_MAX", "ARQ_MAX_LINHAS", "ARQ_MAX_COLUNAS", "ARQ_XLSX_MAX_DESCOMPACTADO", "_CAB_MATRICULA",
    "_CAB_MATRICULA_NAO", "_CAB_CURSO", "_CAB_CURSO_NAO", "_CAB_PESSOAL", "_NOME_SEPARADOR", "_chave_texto",
    "_pontos_cab_matricula", "_pontos_cab_curso", "_parece_matricula", "_decodificar_texto", "_detectar_delimitador",
    "_celula_texto", "_ler_xlsx", "_tabela_do_arquivo", "_nota_matricula", "_coluna_matricula_por_dados",
    "_ler_matriculas",
}
ENV_DO_APP = ["SECRET_KEY", "DOMINIO_ALUNOS", "DOMINIOS_PROFESSORES", "CHAVE_MASTER", "CODIGO_PROFESSOR", "TOKEN_HORAS",
              "FUSO_HORAS", "PRECISAO_MAX_M", "IDADE_MAX_POSICAO_S", "RISCO_MAX", "VELOCIDADE_MAX_MS", "MATRICULA_REGEX",
              "MATRICULA_OBRIGATORIA", "RESET_BANCO", "ADMIN_EMAILS", "ADMIN_PATH", "ADMIN_TOKEN_HORAS",
              "COORD_PROVISORIA", "COORD_EMAIL", "COORD_SENHA", "PROXYCHECK_KEY", "VPN_MODO", "PROXY_HOPS",
              "IPS_PERMITIDOS", "DATABASE_URL", "LOGIN_FALHAS_IP", "LOGIN_FALHAS_CONTA", "CADASTRO_ALUNO_MAX",
              "CADASTRO_ALUNO_JANELA_S", "ADMIN_FALHAS_IP", "ADMIN_FALHAS_CONTA", "ADMIN_FALHAS_GERAL_HORA",
              "EMAIL_PROVEDOR", "EMAIL_API_KEY", "EMAIL_REMETENTE", "EMAIL_REMETENTE_NOME", "SMTP_HOST", "SMTP_PORT",
              "SMTP_USUARIO", "SMTP_SENHA", "VERIFICAR_EMAIL", "CODIGO_EMAIL_MIN", "CODIGO_MAX_TENTATIVAS",
              "CODIGO_INTERVALO_S", "CODIGO_ENVIOS_HORA", "CONFIRMA_FALHAS_IP"]


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

    def __lt__(self, o):
        return Cmp(self, "lt", o)

    def is_(self, v):
        return Cmp(self, "is", v)

    def in_(self, vs):
        return Cmp(self, "in", vs if isinstance(vs, Select) else list(vs))

    def __gt__(self, o):
        return Cmp(self, "gt", o)

    def valor(self, row):
        return getattr(row, self.name)

    def desc(self):
        return Ordem(self, True)

    def asc(self):
        return Ordem(self, False)

    def label(self, _nome):
        return self


class Lower:
    """func.lower(coluna): o SQLite do app compara em minúsculas; aqui também."""
    def __init__(self, col):
        self.col, self.name, self.owner = col, col.name, col.owner

    def valor(self, row):
        v = getattr(row, self.name)
        return v.lower() if isinstance(v, str) else v

    def __eq__(self, o):
        return Cmp(self, "eq", o)

    __hash__ = object.__hash__

    def like(self, p):
        return Cmp(self, "like", p)


class Ordem:
    def __init__(self, col, desc):
        self.col, self.desc = col, desc


class Cmp:
    def __init__(self, col, op, val):
        self.col, self.op, self.val = col, op, val

    def test(self, row, db=None):
        v = self.col.valor(row)
        if self.op == "eq":
            return v is not None and self.val is not None and v == self.val    # SQL: NULL nunca é igual
        if self.op == "like":
            pad = "".join(".*" if c == "%" else "." if c == "_" else re.escape(c) for c in self.val)
            return v is not None and re.fullmatch(pad, v, re.S) is not None
        if self.op == "is":
            return v is self.val
        if self.op == "lt":
            return v is not None and v < self.val
        if self.op == "gt":
            return v is not None and v > self.val
        vals = list(db.scalars(self.val)) if isinstance(self.val, Select) else self.val       # IN (lista ou subconsulta)
        return v in vals


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
    id, nome, ativo, departamento = (Col() for _ in range(4))
    _padroes, _unicos = {"ativo": True}, ("nome",)


class CadastroPendente(Modelo):
    (id, perfil, email, dados, senha_hash, codigo_hash, tentativas, criado_em, enviado_em, expira_em, ip) = (Col() for _ in range(11))
    _padroes, _unicos = {"tentativas": 0}, (("perfil", "email"),)


class LogSeguranca(Modelo):
    id, usuario_id, acao, detalhe, ip, data_hora = (Col() for _ in range(6))


class MatriculasAutorizadas(Modelo):
    id, matricula, curso_id, utilizada = (Col() for _ in range(4))
    _padroes, _unicos = {"utilizada": False}, ("matricula",)


class Aula(Modelo):
    id, professor_id, titulo, ativa, curso_id, periodo = (Col() for _ in range(6))
    _padroes = {"ativa": True}


class Presenca(Modelo):
    id, aula_id, aluno_id = (Col() for _ in range(3))


class Tentativa(Modelo):
    id, aula_id, aluno_id = (Col() for _ in range(3))


class NotaAluno(Modelo):
    id, aluno_id, presenca_id = (Col() for _ in range(3))


class Func:
    def __init__(self, kind, col=None):
        self.kind, self.col = kind, col


class _FuncNS:
    def max(self, col):
        return Func("max", col)

    def lower(self, col):
        return Lower(col)

    def count(self):
        return Func("count")


class Select:
    def __init__(self, *alvos):
        self.alvos, self.conds, self.origem = alvos, [], None
        self.ordem, self.limite, self.deslocar = [], None, 0

    def order_by(self, *o):
        self.ordem.extend(x if isinstance(x, Ordem) else Ordem(x, False) for x in o)
        return self

    def limit(self, n):
        self.limite = n
        return self

    def offset(self, n):
        self.deslocar = n
        return self

    def where(self, *c):
        self.conds.extend(c)
        return self

    def select_from(self, m):
        self.origem = m
        return self


class DeleteStmt:
    def __init__(self, modelo):
        self.modelo, self.conds = modelo, []

    def where(self, *c):
        self.conds.extend(c)
        return self


class UpdateStmt:
    def __init__(self, modelo):
        self.modelo, self.conds, self.valores = modelo, [], {}

    def where(self, *c):
        self.conds.extend(c)
        return self

    def values(self, **kw):
        self.valores.update(kw)
        return self


class Linha(tuple):
    """Linha de resultado com acesso por nome (r.id), como o Row do SQLAlchemy."""
    _nomes: tuple = ()

    def __getattr__(self, n):
        try:
            return self[self._nomes.index(n)]
        except ValueError:
            raise AttributeError(n)


class Resultado(list):
    """O que db.execute devolve: lista de linhas (tuplas), com first()/all()/rowcount como no SQLAlchemy."""
    rowcount = 0

    def first(self):
        return self[0] if self else None

    def all(self):
        return list(self)


class FakeDB:
    """Sessão em memória: autoflush desligado (como o SessionLocal do app), e-mail único, ids sequenciais."""

    def __init__(self, tables=None):
        self.tables = tables if tables is not None else {Aluno: [], Professor: [], Curso: [], LogSeguranca: [],
                                                         MatriculasAutorizadas: [], CadastroPendente: [], Aula: [], Presenca: [],
                                                         Tentativa: [], NotaAluno: []}
        self.pending, self.antes_do_commit, self.apagar = [], None, []

    def add(self, o):
        self.pending.append(o)

    def _persistir(self):
        novos = [o for o in self.pending if o not in self.tables[type(o)]]
        for i, o in enumerate(novos):
            for campo in o._unicos:
                campos = campo if isinstance(campo, tuple) else (campo,)
                v = tuple(getattr(o, c) for c in campos)
                if any(x is None for x in v):
                    continue                                                  # UNIQUE ignora NULL
                if any(tuple(getattr(r, c) for c in campos) == v for r in self.tables[type(o)]) or \
                        any(type(x) is type(o) and tuple(getattr(x, c) for c in campos) == v for x in novos[:i]):
                    self.pending.clear()
                    raise IntegrityError(f"UNIQUE {campos}={v}")
        for o in novos:
            for k, v in o._padroes.items():
                if getattr(o, k) is None:
                    setattr(o, k, v)
            linhas = self.tables[type(o)]
            o.id = max([r.id for r in linhas] or [0]) + 1
            linhas.append(o)
        self.pending.clear()
        for o in self.apagar:
            if o in self.tables[type(o)]:
                self.tables[type(o)].remove(o)
        self.apagar.clear()

    def flush(self):
        self._persistir()

    def commit(self):
        if self.antes_do_commit:
            f, self.antes_do_commit = self.antes_do_commit, None
            f()                                   # simula outra requisição que comita no meio desta
        self._persistir()

    def rollback(self):
        self.pending.clear()
        self.apagar.clear()

    def delete(self, o):
        self.apagar.append(o)

    def execute(self, q):
        if isinstance(q, DeleteStmt):
            tabela = self.tables[q.modelo]
            fora = [r for r in tabela if all(c.test(r, self) for c in q.conds)]
            tabela[:] = [r for r in tabela if r not in fora]
            res = Resultado()
            res.rowcount = len(fora)
            return res
        if isinstance(q, UpdateStmt):
            alvo = [r for r in self.tables[q.modelo] if all(c.test(r, self) for c in q.conds)]
            for r in alvo:
                for k, v in q.valores.items():
                    setattr(r, k, v)
            res = Resultado()
            res.rowcount = len(alvo)
            return res
        if isinstance(q, Select):
            nomes = tuple(getattr(a, "name", None) for a in q.alvos)
            linhas = Resultado()
            for r in self._linhas(q):
                lin = Linha(self._valor(a, r) for a in q.alvos)
                lin._nomes = nomes
                linhas.append(lin)
            return linhas
        raise NotImplementedError(q)

    def get(self, modelo, pk):
        return next((r for r in self.tables[modelo] if r.id == pk), None)

    @staticmethod
    def _valor(a, r):
        return r if isinstance(a, type) else a.valor(r)

    def _modelo(self, q):
        a = q.alvos[0]
        if isinstance(a, Func):
            return a.col.owner if a.col is not None else q.origem
        return a.owner if isinstance(a, (Col, Lower)) else a

    def _linhas(self, q):
        linhas = [r for r in self.tables[self._modelo(q)] if all(c.test(r, self) for c in q.conds)]
        for o in reversed(q.ordem):
            linhas.sort(key=lambda r: (o.col.valor(r) is None, o.col.valor(r)), reverse=o.desc)
        linhas = linhas[q.deslocar:]
        return linhas if q.limite is None else linhas[:q.limite]

    def scalars(self, q):
        return Resultado(self._valor(q.alvos[0], r) for r in self._linhas(q))

    def scalar(self, q):
        a, linhas = q.alvos[0], self._linhas(q)
        if isinstance(a, Func):
            if a.kind == "count":
                return len(linhas)
            vals = [getattr(r, a.col.name) for r in linhas]
            return max(vals) if vals else None
        if isinstance(a, (Col, Lower)):
            return a.valor(linhas[0]) if linhas else None
        return linhas[0] if linhas else None


# =====================================================================================================
# 3) Contexto de teste: código real do app + banco novo + utilitários
# =====================================================================================================
SENHA = "Senha#2026"
SENHA_PROV = "Provisoria#2026"
ENV_EMAIL = dict(EMAIL_PROVEDOR="brevo", EMAIL_API_KEY="chave-de-teste", EMAIL_REMETENTE="guaracheck@unicerrado.edu.br")          # COORD_SENHA definida no ambiente (o código não traz mais senha padrão)
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
            update=lambda m: UpdateStmt(m), delete=lambda m: DeleteStmt(m), CadastroPendente=CadastroPendente,
            base64=base64, csv=csv, io=io, zipfile=zipfile, Aula=Aula, Presenca=Presenca, Tentativa=Tentativa,
            NotaAluno=NotaAluno, Aluno=Aluno, Professor=Professor, Curso=Curso, LogSeguranca=LogSeguranca,
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
        self.agora = datetime.now(timezone.utc)                                   # relógio de parede controlável
        self.ns["utcnow"] = lambda: self.agora
        self.emails, self.envio_falha = [], False

        def _enviar(para, assunto, texto):
            if self.envio_falha:
                raise OSError("provedor fora do ar")
            self.emails.append((para, assunto, texto))
        self.ns["enviar_email"] = _enviar
        self.curso = Curso(nome="Gestão da TI")
        self.db.add(self.curso)
        self.db.commit()
        self.n = 0

    def avancar(self, segundos):
        self.agora += timedelta(seconds=segundos)
        self.ns["time"].t += segundos

    def codigo(self, i=-1):
        import re as _re2
        return _re2.search(r"é: (\d{6})", self.emails[i][2]).group(1)

    def confirmar(self, perfil, email, codigo, ip="198.51.100.7"):
        return status_de(self.ns["confirmar_cadastro"], self.ns["ConfirmarIn"](perfil=perfil, email=email, codigo=codigo),
                         FakeReq(ip), self.db)

    def reenviar(self, perfil, email, ip="198.51.100.7"):
        return status_de(self.ns["reenviar_codigo"], self.ns["ReenviarIn"](perfil=perfil, email=email), FakeReq(ip), self.db)

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

