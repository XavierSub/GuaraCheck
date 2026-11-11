# -*- coding: utf-8 -*-
"""
QA - Funcionalidade 3: ABRIR E GERENCIAR EVENTOS (Guará Check)
Abrir aula/palestra, validação do formulário, regra de uma chamada ativa por professor, encerrar, excluir (uma e todas),
listagens (professor, aluno e coordenação) e ciclo de vida do evento.

Como nas funcionalidades 1 e 2: o código REAL do app.py é extraído por ast e executado contra um banco em memória
(harness_app.py), com as validações reais do Pydantic. O harness agora executa também JOIN / OUTER JOIN / GROUP BY, o
que permite rodar o código real de _aulas_ativas (o que o aluno vê), minhas_aulas e adm_aulas.
NÃO é executado aqui: SQLAlchemy/PostgreSQL de verdade (travas do banco, isolamento de transações).

Uso:  pip install pytest pydantic pyjwt openpyxl
      pytest test_func3_eventos.py -v
"""
import ast as _ast
import json as _json

from harness_app import *  # noqa: F401,F403

ADMIN_EMAIL = "coord.teste@unicerrado.edu.br"
EMAIL_PROF2 = "ana.prof@unicerrado.edu.br"
EMAIL_ALUNO2 = "joao.silva@alunos.unicerrado.edu.br"
LAT, LON = -18.4157, -49.2183          # Goiatuba


class CtxEv(Ctx):
    """Ctx com: coordenação (self.admin), dois professores (self.prof = Carlos, self.ana) e um aluno (self.aluno)."""

    def __init__(self, **env):
        super().__init__(**env)
        self.admin = self.semear_prof(email=ADMIN_EMAIL, coordenacao=True, aprovado=True, nome="Coordenadora Teste",
                                      registro_funcional=None)
        self.adm = {"id": self.admin.id, "role": "admin", "nome": self.admin.nome}
        self.prof = self.semear_prof(email=EMAIL_PROF, nome="Carlos Lima")
        self.ana = self.semear_prof(email=EMAIL_PROF2, nome="Ana Prof")
        self.aluno = self.semear_aluno()

    # --- usuários ---
    def eu(self, p=None):
        p = p or self.prof
        return {"id": p.id, "role": "professor", "nome": p.nome}

    def sessao_aluno(self, aluno=None):
        """O dict que as rotas do aluno recebem, pelo caminho real (token -> usuario_atual)."""
        r = self.usuario("Bearer " + self.token(aluno or self.aluno, "aluno"))
        assert r[0] == 200, r
        return r[1]

    # --- chamadas às rotas reais ---
    def corpo(self, **over):
        d = dict(titulo="Algoritmos", latitude=LAT, longitude=LON, raio_metros=50)
        d.update(over)
        try:
            return self.ns["AulaIn"](**d)
        except ValidationError as e:
            return e

    def abrir(self, p=None, **over):
        b = self.corpo(**over)
        if isinstance(b, ValidationError):
            return 422, b
        return status_de(self.ns["abrir_aula"], b, self.eu(p), self.db)

    def encerrar(self, aula_id, p=None):
        return status_de(self.ns["encerrar_aula"], aula_id, self.eu(p), self.db)

    def excluir(self, aula_id, p=None):
        return status_de(self.ns["excluir_aula"], aula_id, FakeReq(), self.eu(p), self.db)

    def excluir_todas(self, confirmar="EXCLUIR", p=None):
        return status_de(self.ns["excluir_todas_as_aulas"], FakeReq(), confirmar, self.eu(p), self.db)

    def minhas(self, p=None, pagina=1):
        return status_de(self.ns["minhas_aulas"], self.eu(p), self.db, pagina)

    def ativas(self, aluno=None):
        return status_de(self.ns["aulas_ativas"], self.sessao_aluno(aluno), self.db)

    def adm_lista(self, pagina=1):
        return status_de(self.ns["adm_aulas"], self.adm, self.db, pagina)

    def adm_encerrar(self, aula_id):
        return status_de(self.ns["adm_aula_encerrar"], aula_id, self.adm, self.db)

    def adm_excluir(self, aula_id):
        return status_de(self.ns["adm_aula_excluir"], aula_id, FakeReq(), self.adm, self.db)

    def adm_prof_ativo(self, prof, ativo):
        return status_de(self.ns["adm_professor_ativo"], prof.id, self.ns["AtivoIn"](ativo=ativo), FakeReq(), self.adm, self.db)

    # --- dados prontos ---
    def aula(self, p=None, titulo="Aula", **kw):
        p = p or self.prof
        d = dict(professor_id=p.id, titulo=titulo, latitude=LAT, longitude=LON, raio_metros=50.0, tipo="aula", local_tipo="dentro")
        d.update(kw)
        a = Aula(**d)
        self.db.add(a)
        self.db.commit()
        return a

    def outro_prof(self):
        """Mais um professor (cada professor só pode ter UMA chamada ativa: o banco garante)."""
        self._extra = getattr(self, "_extra", 1000) + 1
        return self.prof_barato(self._extra)

    def prof_barato(self, i):
        """Professor sem gastar o PBKDF2 (para testes com dezenas de contas)."""
        p = Professor(nome=f"Prof {i}", email=f"prof{i}@unicerrado.edu.br", senha_hash="x", registro_funcional=f"DOC-{i}",
                      departamento="Computação")
        self.db.add(p)
        self.db.commit()
        return p

    def curso_novo(self, nome):
        c = Curso(nome=nome)
        self.db.add(c)
        self.db.commit()
        return c

    def presenca(self, aula, aluno=None):
        p = Presenca(aula_id=aula.id, aluno_id=(aluno or self.aluno).id)
        self.db.add(p)
        self.db.commit()
        return p

    def aulas_do_banco(self, p=None):
        return [a for a in self.db.tables[Aula] if p is None or a.professor_id == p.id]

    def ativas_do_banco(self, p=None):
        return [a for a in self.aulas_do_banco(p) if a.ativa]


@pytest.fixture
def ev():
    return CtxEv()


def erro_em(r, campo):
    """True se a resposta é um 422 do Pydantic apontando para o campo."""
    return r[0] == 422 and isinstance(r[1], ValidationError) and any(e["loc"] and e["loc"][0] == campo for e in r[1].errors())


# =====================================================================================================
# BLOCO A - Acesso e permissões
# =====================================================================================================
ROTAS_EVENTO = {
    "abrir_aula": "so_professor", "encerrar_aula": "so_professor", "excluir_aula": "so_professor",
    "excluir_todas_as_aulas": "so_professor", "minhas_aulas": "so_professor", "conferir_comprovante": "so_professor",
    "presencas_da_aula": "so_professor", "tentativas_da_aula": "so_professor", "ausentes_da_aula": "so_professor",
    "exportar_chamada": "so_professor", "aulas_ativas": "so_aluno", "registrar_presenca": "so_aluno",
    "adm_aulas": "so_admin", "adm_aula_encerrar": "so_admin", "adm_aula_excluir": "so_admin",
}


def _rotas_de_evento():
    achadas = {}
    for n in _ast.parse(APP.read_text(encoding="utf-8")).body:
        if not isinstance(n, _ast.FunctionDef):
            continue
        for d in n.decorator_list:
            if (isinstance(d, _ast.Call) and isinstance(d.func, _ast.Attribute) and d.args and isinstance(d.args[0], _ast.Constant)
                    and str(d.args[0].value).startswith(("/api/aulas", "/api/professor", "/api/admin/aulas"))):
                deps = [x.args[0].id for x in _ast.walk(n.args) if isinstance(x, _ast.Call) and getattr(x.func, "id", "") == "Depends"
                        and x.args and isinstance(x.args[0], _ast.Name) and x.args[0].id != "get_db"]
                achadas[n.name] = deps[0] if deps else None
    return achadas


def test_ct301_toda_rota_de_evento_exige_o_papel_certo():
    achadas = _rotas_de_evento()
    assert achadas == ROTAS_EVENTO, f"rotas de evento mudaram ou perderam a guarda: {achadas}"


def test_ct302_aluno_e_coordenacao_nao_abrem_evento_pelo_caminho_do_professor(ev):
    aluno = ev.sessao_aluno()
    r_aluno = status_de(ev.ns["so_professor"], aluno)
    r_coord = status_de(ev.ns["so_professor"], ev.adm)
    assert cod(r_aluno) == 403 and cod(r_coord) == 403
    assert cod(status_de(ev.ns["so_professor"], ev.eu())) == 200
    assert cod(status_de(ev.ns["so_aluno"], ev.eu())) == 403 and cod(status_de(ev.ns["so_admin"], ev.eu())) == 403


def test_ct303_professor_desativado_ou_excluido_perde_a_sessao_na_hora(ev):
    tok = "Bearer " + ev.token(ev.prof, "professor")
    assert cod(ev.usuario(tok)) == 200
    assert cod(ev.adm_prof_ativo(ev.prof, False)) == 200
    assert cod(ev.usuario(tok)) == 401                                   # não consegue mais abrir nem encerrar nada
    assert cod(ev.adm_prof_ativo(ev.prof, True)) == 200 and cod(ev.usuario(tok)) == 200
    tok2 = "Bearer " + ev.token(ev.ana, "professor")
    assert cod(ev.usuario(tok2)) == 200
    assert cod(status_de(ev.ns["adm_professor_excluir"], ev.ana.id, FakeReq(), ev.adm, ev.db)) == 200
    assert cod(ev.usuario(tok2)) == 401


def test_ct304_professor_nao_mexe_em_evento_de_outro_e_nao_descobre_que_existe(ev):
    a = ev.aula(ev.prof, "Do Carlos")
    ev.presenca(a)
    for fazer in (lambda i: ev.encerrar(i, ev.ana), lambda i: ev.excluir(i, ev.ana)):
        de_outro, inexistente = fazer(a.id), fazer(9999)
        assert cod(de_outro) == 404 and de_outro == inexistente, "a resposta revela se o evento existe"
    assert a.ativa and len(ev.aulas_do_banco()) == 1 and len(ev.db.tables[Presenca]) == 1


# =====================================================================================================
# BLOCO B - Abrir evento: formulário e validação
# =====================================================================================================
def test_ct311_abrir_evento_valido(ev):
    c = ev.curso
    ev.agora = ev.agora.replace(microsecond=0)
    r = ev.abrir(titulo="  Algoritmos  ", tipo="palestra", local_tipo="fora", local_descricao=" Auditório ", curso_id=c.id,
                 periodo=3, disciplina=" Estruturas de dados ")
    assert cod(r) == 200, r
    a = r[1]
    assert a["ativa"] is True and a["total"] == 0 and a["titulo"] == "Algoritmos" and a["tipo"] == "palestra"
    assert a["local_tipo"] == "fora" and a["local_descricao"] == "Auditório" and a["disciplina"] == "Estruturas de dados"
    assert a["curso_id"] == c.id and a["curso"] == c.nome and a["periodo"] == 3
    assert re.fullmatch(r"\d{4}", a["codigo_validacao"]), "sem código informado, o sistema gera 4 dígitos"
    assert a["aberta_em"] == ev.agora.isoformat() and a["encerrada_em"] is None
    guardada = ev.aulas_do_banco()[0]
    assert guardada.professor_id == ev.prof.id and guardada.ativa is True


def test_ct312_tamanho_do_titulo(ev):
    assert erro_em(ev.abrir(titulo=""), "titulo") and erro_em(ev.abrir(titulo="a"), "titulo")
    assert erro_em(ev.abrir(titulo="x" * 141), "titulo")
    assert cod(ev.abrir(titulo="ab")) == 200 and cod(ev.abrir(titulo="x" * 140)) == 200
    assert erro_em(ev.abrir(titulo=None), "titulo")


def test_ct313_titulo_so_com_espacos_nao_pode_abrir_evento_sem_nome(ev):
    # o Pydantic conta os espaços (min_length=2) e o app depois faz .strip(): sobraria um evento com título vazio
    anterior = ev.abrir(titulo="Chamada em andamento")
    assert cod(anterior) == 200
    for t in ("   ", "\t\t", "  a ", "\n \n"):
        r = ev.abrir(titulo=t)
        assert cod(r) == 422, f"título {t!r} aceito: evento criado com título {r[1].get('titulo')!r}" if cod(r) == 200 else r
    assert [a.titulo for a in ev.aulas_do_banco() if a.ativa] == ["Chamada em andamento"], "um título em branco fechou a chamada aberta"


def test_ct314_latitude_e_longitude_limites_e_valores_nao_numericos(ev):
    for campo, validos, invalidos in (("latitude", (90, -90, 0, -18.4157), (90.000001, -90.000001, 91, 1e9)),
                                      ("longitude", (180, -180, 0, -49.2183), (180.000001, -180.000001, 181, 1e9))):
        for v in validos:
            assert cod(ev.abrir(**{campo: v})) == 200, (campo, v)
        for v in invalidos + (float("nan"), float("inf"), float("-inf"), None, "abc", ""):
            r = ev.abrir(**{campo: v})
            assert erro_em(r, campo), (campo, v, r)


def test_ct315_raio_limites(ev):
    for v in (5, 5.0, 50, 5000):
        assert cod(ev.abrir(raio_metros=v)) == 200, v
    for v in (4.99, 0, -1, 5000.01, 100000, float("nan"), float("inf"), None, "muito"):
        assert erro_em(ev.abrir(raio_metros=v), "raio_metros"), v


def test_ct316_tipo_e_local_tipo(ev):
    for t in ("aula", "palestra"):
        for lt in ("dentro", "fora"):
            r = ev.abrir(tipo=t, local_tipo=lt)
            assert cod(r) == 200 and (r[1]["tipo"], r[1]["local_tipo"]) == (t, lt)
    for t in ("reunião", "", "AULA", None, 1):
        assert erro_em(ev.abrir(tipo=t), "tipo"), t
    for lt in ("campus", "", "DENTRO", None):
        assert erro_em(ev.abrir(local_tipo=lt), "local_tipo"), lt
    r = status_de(ev.ns["abrir_aula"], ev.ns["AulaIn"](titulo="Padrão", latitude=LAT, longitude=LON, raio_metros=50), ev.eu(), ev.db)
    assert (r[1]["tipo"], r[1]["local_tipo"]) == ("aula", "dentro")


def test_ct317_periodo(ev):
    for v in (1, 10, None):
        assert cod(ev.abrir(periodo=v)) == 200, v
    for v in (0, 11, -1, 100, 2.5, "x"):
        assert erro_em(ev.abrir(periodo=v), "periodo"), v


def test_ct318_codigo_de_validacao_normalizado_e_recusado_sem_fechar_a_chamada(ev):
    for entrada, esperado in ((" 08 1 ", "081"), ("abc12", "ABC12"), ("aç1", "AC1"), ("a b c", "ABC"), ("1" * 12, "1" * 12), ("081", "081")):
        r = ev.abrir(codigo_validacao=entrada)
        assert cod(r) == 200 and r[1]["codigo_validacao"] == esperado, (entrada, r)
    for vazio in (None, "", "   "):
        r = ev.abrir(codigo_validacao=vazio)
        assert cod(r) == 200 and re.fullmatch(r"\d{4}", r[1]["codigo_validacao"]), vazio
    aberta = ev.abrir(titulo="Em andamento")[1]["id"]
    for ruim in ("ab", "1" * 13, "ab#12", "A-B", "😀😀😀", "١٢٣"):
        r = ev.abrir(codigo_validacao=ruim)
        assert cod(r) == 422, (ruim, r)
    assert erro_em(ev.abrir(codigo_validacao="x" * 33), "codigo_validacao")
    # o código inválido é conferido ANTES de encerrar a chamada anterior (um erro de digitação não fecha a aula aberta)
    assert [a.id for a in ev.ativas_do_banco(ev.prof)] == [aberta]


def test_ct319_campos_opcionais_em_branco_viram_vazio(ev):
    r = ev.abrir(local_descricao="   ", disciplina="   ")
    assert cod(r) == 200 and r[1]["local_descricao"] is None and r[1]["disciplina"] is None
    r = ev.abrir(local_descricao="x" * 160, disciplina="y" * 140)
    assert cod(r) == 200
    assert erro_em(ev.abrir(local_descricao="x" * 161), "local_descricao") and erro_em(ev.abrir(disciplina="y" * 141), "disciplina")


def test_ct320_curso_inexistente_ou_inativo_e_recusado_sem_fechar_a_chamada(ev):
    aberta = ev.abrir(titulo="Em andamento")[1]["id"]
    inativo = ev.curso_novo("Curso Antigo")
    inativo.ativo = False
    for cid in (9999, inativo.id, 0, -1):
        r = ev.abrir(curso_id=cid)
        assert cod(r) == 422 and "Curso inválido" in r[1], (cid, r)
    assert [a.id for a in ev.ativas_do_banco(ev.prof)] == [aberta]
    r = ev.abrir(curso_id=ev.curso.id)
    assert cod(r) == 200 and r[1]["curso"] == ev.curso.nome
    assert cod(ev.abrir(curso_id=None)) == 200


def test_ct321_corpo_com_campos_a_mais_nao_muda_dono_nem_estado(ev):
    b = ev.ns["AulaIn"](**dict(titulo="Algoritmos", latitude=LAT, longitude=LON, raio_metros=50, professor_id=ev.ana.id,
                               ativa=False, id=77, encerrada_em="2020-01-01"))
    r = status_de(ev.ns["abrir_aula"], b, ev.eu(ev.prof), ev.db)
    assert cod(r) == 200 and r[1]["ativa"] is True and r[1]["id"] != 77
    a = ev.aulas_do_banco()[0]
    assert a.professor_id == ev.prof.id and a.ativa is True and a.encerrada_em is None


# =====================================================================================================
# BLOCO C - Uma chamada ativa por professor
# =====================================================================================================
def test_ct331_abrir_novo_evento_encerra_so_o_anterior_do_mesmo_professor(ev):
    do_carlos = ev.abrir(ev.prof, titulo="Carlos 1")[1]["id"]
    da_ana = ev.abrir(ev.ana, titulo="Ana 1")[1]["id"]
    ev.avancar(600)
    novo = ev.abrir(ev.prof, titulo="Carlos 2")[1]["id"]
    por_id = {a.id: a for a in ev.db.tables[Aula]}
    assert por_id[do_carlos].ativa is False and por_id[do_carlos].encerrada_em == ev.agora
    assert por_id[novo].ativa is True and por_id[da_ana].ativa is True and por_id[da_ana].encerrada_em is None
    assert len(ev.db.tables[Aula]) == 3, "encerrar a anterior não pode apagar nada"


def test_ct332_dois_envios_em_seguida_deixam_uma_chamada_so(ev):
    r1, r2 = ev.abrir(titulo="Chamada"), ev.abrir(titulo="Chamada")
    assert cod(r1) == cod(r2) == 200 and r1[1]["id"] != r2[1]["id"]
    assert [a.id for a in ev.ativas_do_banco(ev.prof)] == [r2[1]["id"]]
    assert [a.titulo for a in ev.aulas_do_banco(ev.prof)] == ["Chamada", "Chamada"]


def test_ct333_duplo_envio_simultaneo_nao_pode_deixar_duas_chamadas_ativas(ev):
    ev.abrir(titulo="Chamada anterior")
    segunda_requisicao = FakeDB(tables=ev.db.tables)         # outra requisição = outra sessão, mesmo banco
    segunda_requisicao.relogio = ev.db.relogio
    resposta = {}

    def outra():
        resposta["r"] = status_de(ev.ns["abrir_aula"], ev.corpo(titulo="Toque duplo"), ev.eu(), segunda_requisicao)
    ev.db.antes_do_commit = outra                            # ela comita no meio da primeira
    r1 = ev.abrir(titulo="Toque duplo")
    r2 = resposta["r"]
    ativas = ev.ativas_do_banco(ev.prof)
    assert len(ativas) == 1, f"{len(ativas)} chamadas ativas do mesmo professor: a presença dos alunos se divide entre elas"
    assert sorted([cod(r1), cod(r2)]) == [200, 409], (r1, r2)           # uma vence; a outra é avisada
    perdedora = r1 if cod(r1) == 409 else r2
    assert "outro envio" in perdedora[1]
    assert [a.id for a in ativas] == [(r2 if cod(r1) == 409 else r1)[1]["id"]], "a chamada ativa não é a que recebeu 200"


def test_ct333b_trava_de_uma_chamada_ativa_esta_no_modelo_e_na_migracao():
    fonte = APP.read_text(encoding="utf-8")
    modelo = fonte[fonte.index("class Aula(Base)"):fonte.index("class Presenca(Base)")]
    assert 'Index("uq_aula_ativa_prof", "professor_id", unique=True' in modelo and "postgresql_where" in modelo and "sqlite_where" in modelo
    migracao = fonte[fonte.index("def migrar_esquema"):fonte.index("def resetar_dados_de_teste")]
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_aula_ativa_prof ON aulas (professor_id) WHERE ativa" in migracao
    assert "SELECT MAX(id) FROM aulas WHERE ativa GROUP BY professor_id" in migracao, "a migração não limpa duplicatas antigas antes do índice"


# =====================================================================================================
# BLOCO D - Encerrar
# =====================================================================================================
def test_ct341_professor_encerra_o_proprio_evento_e_repetir_nao_muda_nada(ev):
    a = ev.abrir()[1]
    ev.avancar(300)
    r = ev.encerrar(a["id"])
    assert cod(r) == 200 and r[1]["ativa"] is False and r[1]["encerrada_em"] == ev.agora.isoformat()
    hora = r[1]["encerrada_em"]
    ev.avancar(300)
    r2 = ev.encerrar(a["id"])
    assert cod(r2) == 200 and r2[1]["encerrada_em"] == hora, "encerrar de novo não pode mover a hora de encerramento"


def test_ct342_encerrar_evento_de_outro_professor_ou_inexistente(ev):
    a = ev.aula(ev.prof)
    assert cod(ev.encerrar(a.id, ev.ana)) == 404 and cod(ev.encerrar(9999)) == 404 and cod(ev.encerrar(0)) == 404
    assert a.ativa is True and a.encerrada_em is None


def test_ct343_coordenacao_encerra_qualquer_evento(ev):
    a, b = ev.aula(ev.prof), ev.aula(ev.ana)
    ev.avancar(60)
    assert cod(ev.adm_encerrar(a.id)) == 200 and a.ativa is False and a.encerrada_em == ev.agora
    hora = a.encerrada_em
    ev.avancar(60)
    assert cod(ev.adm_encerrar(a.id)) == 200 and a.encerrada_em == hora
    assert b.ativa is True and cod(ev.adm_encerrar(9999)) == 404


def test_ct344_encerrar_tira_o_evento_da_lista_do_aluno_na_hora(ev):
    a = ev.abrir()[1]
    assert [x["id"] for x in ev.ativas()[1]] == [a["id"]]
    ev.encerrar(a["id"])
    assert ev.ativas()[1] == [], "o cache de 2 s não foi invalidado ao encerrar"
    b = ev.abrir(titulo="Outra")[1]
    assert [x["id"] for x in ev.ativas()[1]] == [b["id"]]
    ev.adm_encerrar(b["id"])
    assert ev.ativas()[1] == []


# =====================================================================================================
# BLOCO E - Excluir
# =====================================================================================================
def test_ct351_excluir_evento_apaga_presencas_e_tentativas_dele_e_so_dele(ev):
    a, b = ev.aula(ev.prof, "Apagar", ativa=False), ev.aula(ev.prof, "Manter")
    outro = ev.semear_aluno(email=EMAIL_ALUNO2)
    for aula in (a, b):
        ev.presenca(aula, ev.aluno)
        ev.presenca(aula, outro)
        ev.db.add(Tentativa(aula_id=aula.id, aluno_id=ev.aluno.id))
    ev.db.commit()
    r = ev.excluir(a.id)
    assert cod(r) == 200 and r[1] == {"ok": True, "presencas_removidas": 2}
    assert [x.id for x in ev.db.tables[Aula]] == [b.id]
    assert {p.aula_id for p in ev.db.tables[Presenca]} == {b.id} and len(ev.db.tables[Presenca]) == 2
    assert {t.aula_id for t in ev.db.tables[Tentativa]} == {b.id}
    log = ev.logs("excluir_aula")
    assert len(log) == 1 and "titulo=Apagar" in log[0].detalhe and "presencas=2" in log[0].detalhe and log[0].usuario_id == ev.prof.id


def test_ct352_anotacoes_do_aluno_sobrevivem_a_exclusao_do_evento(ev):
    a = ev.aula(ev.prof, "Palestra")
    p = ev.presenca(a)
    ev.db.add(NotaAluno(aluno_id=ev.aluno.id, presenca_id=p.id))
    ev.db.commit()
    assert cod(ev.excluir(a.id)) == 200
    assert len(ev.db.tables[NotaAluno]) == 1 and ev.db.tables[Presenca] == []
    assert cod(ev.adm_excluir(ev.aula(ev.prof).id)) == 200 and len(ev.db.tables[NotaAluno]) == 1


def test_ct353_excluir_evento_de_outro_professor_ou_inexistente_nao_apaga_nada(ev):
    a = ev.aula(ev.prof)
    ev.presenca(a)
    assert cod(ev.excluir(a.id, ev.ana)) == 404 and cod(ev.excluir(9999)) == 404
    assert len(ev.db.tables[Aula]) == 1 and len(ev.db.tables[Presenca]) == 1 and ev.logs("excluir_aula") == []


def test_ct354_excluir_todas_exige_confirmacao_e_so_apaga_as_do_professor_logado(ev):
    minhas = [ev.aula(ev.prof, f"M{i}", ativa=(i == 2)) for i in range(3)]
    dela = ev.aula(ev.ana, "Da Ana")
    for a in minhas + [dela]:
        ev.presenca(a)
        ev.db.add(Tentativa(aula_id=a.id, aluno_id=ev.aluno.id))
    ev.db.commit()
    for ruim in ("", "excluir", "Excluir", "SIM", "EXCLUIR ", " EXCLUIR", "EXCLUIR\n"):
        assert cod(ev.excluir_todas(ruim)) == 400, repr(ruim)
    assert len(ev.db.tables[Aula]) == 4 and len(ev.db.tables[Presenca]) == 4
    r = ev.excluir_todas("EXCLUIR")
    assert cod(r) == 200 and r[1] == {"ok": True, "aulas_removidas": 3, "presencas_removidas": 3}
    assert [a.id for a in ev.db.tables[Aula]] == [dela.id]
    assert [p.aula_id for p in ev.db.tables[Presenca]] == [dela.id] and [t.aula_id for t in ev.db.tables[Tentativa]] == [dela.id]
    log = ev.logs("excluir_todas_aulas")
    assert len(log) == 1 and log[0].detalhe == "aulas=3; presencas=3"
    assert cod(ev.excluir_todas("EXCLUIR")) == 200                         # sem nada para apagar: 200, zeros
    assert [a.id for a in ev.db.tables[Aula]] == [dela.id]


def test_ct355_coordenacao_exclui_evento_com_registro_na_auditoria(ev):
    a, b = ev.aula(ev.prof, "Apagar"), ev.aula(ev.ana, "Manter")
    ev.presenca(a)
    ev.presenca(b)
    r = ev.adm_excluir(a.id)
    assert cod(r) == 200 and r[1]["presencas_removidas"] == 1
    assert [x.id for x in ev.db.tables[Aula]] == [b.id] and len(ev.db.tables[Presenca]) == 1
    log = ev.logs("excluir_aula_admin")
    assert len(log) == 1 and log[0].usuario_id == ev.admin.id and "titulo=Apagar" in log[0].detalhe
    assert cod(ev.adm_excluir(a.id)) == 404 and cod(ev.adm_excluir(9999)) == 404


def test_ct356_excluir_evento_ativo_tira_da_lista_do_aluno_na_hora(ev):
    a = ev.abrir()[1]
    assert len(ev.ativas()[1]) == 1
    ev.excluir(a["id"])
    assert ev.ativas()[1] == []
    b = ev.abrir(titulo="Outra")[1]
    assert len(ev.ativas()[1]) == 1
    ev.excluir_todas()
    assert ev.ativas()[1] == []
    c = ev.abrir(titulo="Mais uma")[1]
    assert len(ev.ativas()[1]) == 1
    ev.adm_excluir(c["id"])
    assert ev.ativas()[1] == []


# =====================================================================================================
# BLOCO F - Listagens
# =====================================================================================================
def test_ct361_lista_do_professor_so_tem_os_dele_do_mais_novo_ao_mais_antigo_com_contagem(ev):
    a1, a2, a3 = (ev.aula(ev.prof, "A1", codigo_validacao="111", ativa=False), ev.aula(ev.prof, "A2", ativa=False),
                  ev.aula(ev.prof, "A3", curso_id=ev.curso.id))
    ev.aula(ev.ana, "Da Ana")
    outro = ev.semear_aluno(email=EMAIL_ALUNO2)
    ev.presenca(a1, ev.aluno)
    ev.presenca(a1, outro)
    ev.presenca(a3, ev.aluno)
    r = ev.minhas()
    assert cod(r) == 200 and [x["titulo"] for x in r[1]] == ["A3", "A2", "A1"]
    assert [x["total"] for x in r[1]] == [1, 0, 2], "contagem de presenças errada (evento sem presença deve aparecer com 0)"
    assert r[1][0]["curso"] == ev.curso.nome and r[1][1]["curso"] is None
    assert r[1][2]["codigo_validacao"] == "111" and all("codigo_validacao" in x for x in r[1])
    assert ev.minhas(ev.ana)[1][0]["titulo"] == "Da Ana" and len(ev.minhas(ev.ana)[1]) == 1


def test_ct362_professor_com_mais_de_30_eventos_consegue_chegar_aos_mais_antigos(ev):
    ids = [ev.aula(ev.prof, f"Evento {i:02d}", ativa=False).id for i in range(35)]
    vistos, pagina = [], 1
    while True:
        r = ev.minhas(pagina=pagina)
        assert cod(r) == 200
        if not r[1]:
            break
        vistos += [x["id"] for x in r[1]]
        pagina += 1
        assert pagina < 10, "paginação não termina"
    assert vistos == sorted(ids, reverse=True), "faltam eventos, ou repetem, ou fora de ordem (do mais novo ao mais antigo)"
    assert len(ev.minhas(pagina=1)[1]) == 30 and len(ev.minhas(pagina=2)[1]) == 5 and ev.minhas(pagina=3)[1] == []
    for ruim in (0, -3):                                                    # página inválida: devolve a primeira, não quebra
        assert [x["id"] for x in ev.minhas(pagina=ruim)[1]] == vistos[:30], ruim
    assert ev.minhas(pagina=999_999)[1] == []


def test_ct363_lista_do_aluno_filtra_por_curso_e_periodo_e_nunca_traz_o_codigo(ev):
    outro_curso = ev.curso_novo("Direito")
    todos = ev.aula(ev.outro_prof(), "Para todos", codigo_validacao="SEGREDO1")
    do_curso = ev.aula(ev.outro_prof(), "Só TI", curso_id=ev.curso.id, codigo_validacao="SEGREDO2")
    do_per = ev.aula(ev.outro_prof(), "Só 2º período", periodo=2, codigo_validacao="SEGREDO3")
    do_per5 = ev.aula(ev.outro_prof(), "Só 5º período", periodo=5, codigo_validacao="SEGREDO4")
    outro = ev.aula(ev.outro_prof(), "Só Direito", curso_id=outro_curso.id, codigo_validacao="SEGREDO5")
    exato = ev.aula(ev.outro_prof(), "TI 2º", curso_id=ev.curso.id, periodo=2, codigo_validacao="SEGREDO6")
    ev.aula(ev.outro_prof(), "Encerrado", ativa=False, codigo_validacao="SEGREDO7")
    r = ev.ativas()                                                          # aluno: curso TI, 2º período
    assert cod(r) == 200
    assert {x["titulo"] for x in r[1]} == {"Para todos", "Só TI", "Só 2º período", "TI 2º"}
    assert "SEGREDO" not in _json.dumps(r[1]), "o código de validação vazou para a lista do aluno"
    assert all(set(x) == {"id", "titulo", "professor", "tipo", "local_tipo", "local_descricao", "curso", "periodo", "disciplina",
                          "latitude", "longitude", "raio_metros", "aberta_em", "ja_registrou"} for x in r[1])
    assert all(x["ja_registrou"] is False for x in r[1])
    ev.presenca(do_curso)
    ja = {x["titulo"]: x["ja_registrou"] for x in ev.ativas()[1]}
    assert ja == {"Para todos": False, "Só TI": True, "Só 2º período": False, "TI 2º": False}


def test_ct364_aluno_sem_curso_ou_sem_periodo_so_ve_eventos_abertos_a_todos(ev):
    sem = ev.semear_aluno(email=EMAIL_ALUNO2, curso_id=None, periodo=None)
    ev.aula(ev.outro_prof(), "Para todos")
    ev.aula(ev.outro_prof(), "Só TI", curso_id=ev.curso.id)
    ev.aula(ev.outro_prof(), "Só 3º", periodo=3)
    assert [x["titulo"] for x in ev.ativas(sem)[1]] == ["Para todos"]


def test_ct365_evento_novo_aparece_na_hora_e_o_cache_dura_2_segundos(ev):
    assert ev.ativas()[1] == []
    ev.abrir(titulo="Novo")
    assert [x["titulo"] for x in ev.ativas()[1]] == ["Novo"], "o cache não foi invalidado ao abrir o evento"
    ev.aula(ev.ana, "Direto no banco")                                       # sem passar pela rota: o cache ainda vale
    assert [x["titulo"] for x in ev.ativas()[1]] == ["Novo"]
    ev.avancar(2.5)
    assert {x["titulo"] for x in ev.ativas()[1]} == {"Novo", "Direto no banco"}


def test_ct366_muitos_eventos_ativos_ao_mesmo_tempo_nao_escondem_o_do_aluno(ev):
    direito = ev.curso_novo("Direito")
    meu = ev.aula(ev.prof_barato(0), "O meu evento (o mais antigo)", curso_id=ev.curso.id, periodo=2)
    for i in range(1, 56):                                                   # 55 eventos de OUTRO curso, todos mais novos
        ev.aula(ev.prof_barato(i), f"Direito {i}", curso_id=direito.id)
    titulos = [x["titulo"] for x in ev.ativas()[1]]
    assert meu.titulo in titulos, (f"o aluno não vê o evento do próprio curso: a consulta corta em 50 eventos ANTES de filtrar "
                                   f"por curso/período ({len(titulos)} devolvidos)")


def test_ct367_lista_da_coordenacao_traz_todos_com_professor_e_contagem_sem_codigo(ev):
    a, b = ev.aula(ev.prof, "A", codigo_validacao="SEGREDO"), ev.aula(ev.ana, "B", ativa=False)
    ev.presenca(a)
    r = ev.adm_lista()
    assert cod(r) == 200 and [x["titulo"] for x in r[1]] == ["A", "B"], "os eventos abertos vêm primeiro, depois os mais novos"
    assert [x["professor"] for x in r[1]] == ["Carlos Lima", "Ana Prof"] and [x["total"] for x in r[1]] == [1, 0]
    assert "SEGREDO" not in _json.dumps(r[1]) and all("codigo_validacao" not in x for x in r[1])


def test_ct368_coordenacao_sempre_enxerga_os_eventos_ainda_abertos_mesmo_os_antigos(ev):
    esquecido = ev.aula(ev.prof, "Esquecido aberto", ativa=True)
    for i in range(70):
        ev.aula(ev.ana, f"Encerrado {i}", ativa=False)
    r = ev.adm_lista()
    assert cod(r) == 200
    assert esquecido.id in {x["id"] for x in r[1]}, (f"a lista só mostra os {len(r[1])} mais recentes: um evento esquecido "
                                                    "aberto há semanas some do painel e a coordenação não consegue encerrá-lo")


# =====================================================================================================
# BLOCO G - Ciclo de vida do evento
# =====================================================================================================
def test_ct371_evento_esquecido_aberto_nao_pode_aceitar_presenca_para_sempre(ev):
    a = ev.abrir(titulo="Aula de ontem")[1]
    ev.avancar(24 * 3600 + 60)                                               # 24 h depois, ninguém encerrou
    ids = [x["id"] for x in ev.ativas()[1]]
    assert a["id"] not in ids, "24 h depois o evento continua aberto na lista do aluno (não há encerramento automático)"


def test_ct372_professor_desativado_nao_deixa_evento_aberto_para_os_alunos(ev):
    a = ev.abrir(titulo="Aberta pelo professor")[1]
    assert [x["id"] for x in ev.ativas()[1]] == [a["id"]]
    assert cod(ev.adm_prof_ativo(ev.prof, False)) == 200
    assert ev.ativas()[1] == [], ("a coordenação desativou o professor, mas o evento dele continua aberto e aceitando presença "
                                  "(e ele não consegue mais entrar para encerrá-lo)")


def test_ct373_professor_excluido_leva_eventos_e_presencas_e_some_da_lista_do_aluno(ev):
    a = ev.abrir(titulo="Vai sumir")[1]
    ev.presenca(ev.db.get(Aula, a["id"]))
    assert len(ev.ativas()[1]) == 1
    assert cod(status_de(ev.ns["adm_professor_excluir"], ev.prof.id, FakeReq(), ev.adm, ev.db)) == 200
    assert ev.db.tables[Aula] == [] and ev.db.tables[Presenca] == [] and ev.ativas()[1] == []


def test_ct374_codigo_de_validacao_so_aparece_para_o_dono(ev):
    a = ev.aula(ev.prof, "Aula", codigo_validacao="ABC123")
    assert "codigo_validacao" not in ev.ns["aula_dict"](a)
    assert ev.ns["aula_dict"](a, com_codigo=True)["codigo_validacao"] == "ABC123"
    assert ev.minhas()[1][0]["codigo_validacao"] == "ABC123"
    assert ev.minhas(ev.ana)[1] == []


def test_ct369_painel_da_coordenacao_pagina_e_nao_perde_nem_repete_eventos(ev):
    abertos = [ev.aula(ev.outro_prof(), f"Aberto {i}").id for i in range(3)]
    fechados = [ev.aula(ev.ana, f"Fechado {i:02d}", ativa=False).id for i in range(70)]
    p1, p2, p3 = (ev.adm_lista(n) for n in (1, 2, 3))
    assert cod(p1) == cod(p2) == cod(p3) == 200
    assert [x["id"] for x in p1[1][:3]] == sorted(abertos, reverse=True), "os abertos devem ser os primeiros da primeira página"
    assert (len(p1[1]), len(p2[1]), p3[1]) == (60, 13, [])
    todos = [x["id"] for x in p1[1] + p2[1]]
    assert len(set(todos)) == 73 == len(todos) and set(todos) == set(abertos + fechados)
    assert [x["id"] for x in p1[1][3:]] == sorted(fechados, reverse=True)[:57]
    assert [x["id"] for x in ev.adm_lista(0)[1]] == [x["id"] for x in p1[1]]


def test_ct375_chamada_passa_do_prazo_e_e_encerrada_pelo_sistema(ev):
    a = ev.abrir(titulo="Aula de ontem")[1]
    aberta_em = ev.db.get(Aula, a["id"]).aberta_em
    ev.avancar(12 * 3600 - 60)                                              # 11 h 59 min: ainda vale
    assert [x["id"] for x in ev.ativas()[1]] == [a["id"]] and ev.db.get(Aula, a["id"]).ativa is True
    ev.avancar(120)                                                         # 12 h 01 min: passou do prazo
    assert ev.ativas()[1] == []
    guardada = ev.db.get(Aula, a["id"])
    assert guardada.ativa is False and guardada.encerrada_em == aberta_em + timedelta(hours=12), \
        "o encerramento automático deve gravar a hora do vencimento (abertura + 12 h), não a de agora"
    nova = ev.abrir(titulo="Aula de hoje")
    assert cod(nova) == 200 and [x["id"] for x in ev.ativas()[1]] == [nova[1]["id"]]


def test_ct375b_professor_e_coordenacao_tambem_veem_a_chamada_vencida_como_encerrada(ev):
    ev.abrir(titulo="Esquecida")
    ev.avancar(13 * 3600)
    h = ev.minhas()[1][0]
    assert h["ativa"] is False and h["encerrada_em"] is not None
    ev.abrir(ev.ana, titulo="Esquecida 2")
    ev.avancar(13 * 3600)
    assert {x["titulo"]: x["ativa"] for x in ev.adm_lista()[1]} == {"Esquecida": False, "Esquecida 2": False}


def test_ct375c_prazo_configuravel_e_zero_desliga(ev):
    curto = CtxEv(AULA_MAX_HORAS="2")
    a = curto.abrir()[1]
    curto.avancar(2 * 3600 - 60)
    assert len(curto.ativas()[1]) == 1
    curto.avancar(120)
    assert curto.ativas()[1] == []
    sem = CtxEv(AULA_MAX_HORAS="0")
    sem.abrir()
    sem.avancar(72 * 3600)
    assert len(sem.ativas()[1]) == 1, "AULA_MAX_HORAS=0 deve desligar o prazo"
    ruim = CtxEv(AULA_MAX_HORAS="doze")
    assert ruim.ns["AULA_MAX_HORAS"] == 12.0, "valor inválido volta ao padrão de 12 h"
    assert CtxEv().ns["AULA_MAX_HORAS"] == 12.0


def test_ct376_quem_registra_presenca_respeita_o_prazo():
    """registrar_presenca roda no teste da Funcionalidade 4; aqui: a regra que ele usa, e a garantia de que ele a usa."""
    ev = CtxEv()
    a = ev.aula(ev.prof, "Aula")
    aula_aberta = ev.ns["aula_aberta"]
    assert aula_aberta(a) is True
    ev.avancar(12 * 3600 + 1)
    assert aula_aberta(a) is False, "passou do prazo: não aceita presença, mesmo ainda marcada como ativa"
    a.ativa = False
    assert aula_aberta(a) is False
    sem_data = Aula(professor_id=ev.prof.id, titulo="x", ativa=True)
    assert aula_aberta(sem_data) is True                                    # registro antigo sem data: não bloqueia
    fontes = {n.name: n for n in _ast.parse(APP.read_text(encoding="utf-8")).body if isinstance(n, _ast.FunctionDef)}
    for nome in ("registrar_presenca", "presencas_da_aula"):
        chamadas = {x.func.id for x in _ast.walk(fontes[nome]) if isinstance(x, _ast.Call) and isinstance(x.func, _ast.Name)}
        assert "aula_aberta" in chamadas, f"{nome} deixou de usar aula_aberta (o prazo não vale mais ali)"
    # quem LÊ chamadas abertas limpa as vencidas antes (adm_resumo usa SQL que o harness não executa: conferido pelo código)
    for nome in ("_aulas_ativas", "minhas_aulas", "adm_aulas", "adm_resumo"):
        chamadas = {x.func.id for x in _ast.walk(fontes[nome]) if isinstance(x, _ast.Call) and isinstance(x.func, _ast.Name)}
        assert "encerrar_vencidas" in chamadas, f"{nome} deixou de encerrar as chamadas vencidas"


def test_ct377_desativar_professor_encerra_a_chamada_dele_e_so_a_dele(ev):
    dele = ev.abrir(ev.prof, titulo="Dele")[1]
    da_ana = ev.abrir(ev.ana, titulo="Da Ana")[1]
    ev.avancar(300)
    assert cod(ev.adm_prof_ativo(ev.prof, False)) == 200
    encerrada = ev.db.get(Aula, dele["id"])
    assert encerrada.ativa is False and encerrada.encerrada_em == ev.agora
    assert ev.db.get(Aula, da_ana["id"]).ativa is True and [x["titulo"] for x in ev.ativas()[1]] == ["Da Ana"]
    log = ev.logs("professor_ativo")
    assert len(log) == 1 and log[0].detalhe == f"{EMAIL_PROF}: desativado; chamadas encerradas=1"
    assert cod(ev.adm_prof_ativo(ev.prof, True)) == 200
    assert ev.db.get(Aula, dele["id"]).ativa is False, "reativar o professor não pode reabrir a chamada antiga"
    assert cod(ev.abrir(ev.prof, titulo="De novo")) == 200
    assert cod(ev.adm_prof_ativo(ev.ana, False)) == 200 and cod(ev.adm_prof_ativo(ev.ana, False)) == 200
    assert ev.logs("professor_ativo")[-1].detalhe == f"{EMAIL_PROF2}: desativado"      # sem chamada aberta: sem sufixo


def test_ct378_titulo_com_espacos_e_quebras_vira_uma_linha_so(ev):
    r = ev.abrir(titulo="  Cálculo   II\n\t(turma  B) ")
    assert cod(r) == 200 and r[1]["titulo"] == "Cálculo II (turma B)"
    r = ev.abrir(titulo="   ")
    assert cod(r) == 422 and "nome do evento" in r[1]
