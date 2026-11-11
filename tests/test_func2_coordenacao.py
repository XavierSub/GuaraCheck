# -*- coding: utf-8 -*-
"""
QA - Funcionalidade 2: COORDENAÇÃO (Guará Check)
Contas (professores e coordenação), cursos, alunos, matrículas autorizadas (texto e arquivo) e pedidos de senha.

Como no teste da Funcionalidade 1: o código REAL do app.py é extraído por ast e executado contra um banco em memória
(harness_app.py). Listagens com JOIN/GROUP BY (/admin/alunos, /admin/cursos, /admin/professores, /admin/resumo,
/admin/matriculas) NÃO são executadas aqui: precisam de SQLAlchemy real.

Uso:  pip install pytest pydantic pyjwt openpyxl
      pytest test_func2_coordenacao.py -v
"""
import json as _json

import openpyxl

from harness_app import *  # noqa: F401,F403

ADMIN_EMAIL = "coord.teste@unicerrado.edu.br"
NOVA_SENHA = "Nova#Senha2027"


class CtxAdm(Ctx):
    """Ctx com uma conta da coordenação já criada (self.admin) e o usuário que as rotas /admin recebem (self.u)."""

    def __init__(self, senha_admin=SENHA, **env):
        super().__init__(**env)
        self.admin = self.semear_prof(email=ADMIN_EMAIL, senha=senha_admin, coordenacao=True, aprovado=True,
                                      nome="Coordenadora Teste", registro_funcional=None)
        self.u = {"id": self.admin.id, "role": "admin", "nome": self.admin.nome}

    def corpo(self, classe, **kw):
        try:
            return self.ns[classe](**kw)
        except ValidationError as e:
            return e

    def rota(self, nome, *pre, ip="198.51.100.9", u=None):
        """Chama a rota real: (parâmetros de caminho/corpo..., request, usuário admin, db)."""
        for x in pre:
            if isinstance(x, ValidationError):
                return 422, x
        return status_de(self.ns[nome], *pre, FakeReq(ip), u or self.u, self.db)

    def token_admin(self, p=None):
        return self.ns["_resposta_admin"](p or self.admin)["token"]

    def bearer(self, tok):
        return "Bearer " + tok

    def sessao(self, tok):
        """Valida o token pelo caminho real das rotas: usuario_atual (+ so_admin quando o papel é admin)."""
        r = self.usuario(self.bearer(tok))
        if r[0] == 200 and r[1]["role"] == "admin":
            return status_de(self.ns["so_admin"], r[1])
        return r

    def prof(self, **kw):
        return self.semear_prof(**kw)

    def aula(self, professor, titulo="Aula", **kw):
        a = Aula(professor_id=professor.id, titulo=titulo, **kw)
        self.db.add(a)
        self.db.commit()
        return a

    def presenca(self, aula, aluno):
        p = Presenca(aula_id=aula.id, aluno_id=aluno.id)
        self.db.add(p)
        self.db.commit()
        return p

    def curso_novo(self, nome, ativo=True):
        c = Curso(nome=nome, ativo=ativo)
        self.db.add(c)
        self.db.commit()
        return c

    def mats(self):
        return {m.matricula: m for m in self.db.tables[MatriculasAutorizadas]}

    def texto(self, texto, **kw):
        return self.rota("adm_matriculas_importar", self.corpo("MatriculasIn", texto=texto, **kw))

    def arquivo(self, conteudo: bytes, **kw):
        kw.setdefault("nome", "lista.csv")
        return self.rota("adm_matriculas_arquivo",
                         self.corpo("ArquivoMatriculasIn", dados=base64.b64encode(conteudo).decode(), **kw))


@pytest.fixture
def adm():
    return CtxAdm()


def xlsx(linhas, abas=None) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    for l in (abas[0] if abas else linhas):
        ws.append(l)
    for extra in (abas[1:] if abas else []):
        w = wb.create_sheet()
        for l in extra:
            w.append(l)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# =====================================================================================================
# BLOCO A - Acesso à área da coordenação
# =====================================================================================================
def _rotas_admin():
    achadas = {}
    for n in ast.parse(APP.read_text(encoding="utf-8")).body:
        if not isinstance(n, ast.FunctionDef):
            continue
        for d in n.decorator_list:
            if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.args
                    and d.func.attr in ("get", "post", "put", "patch", "delete")
                    and isinstance(d.args[0], ast.Constant) and str(d.args[0].value).startswith("/api/admin/")):
                protegida = any(isinstance(x, ast.Call) and getattr(x.func, "id", "") == "Depends" and x.args
                                and getattr(x.args[0], "id", "") == "so_admin" for x in ast.walk(n.args))
                achadas[n.name] = (d.func.attr.upper(), d.args[0].value, protegida)
    return achadas


def test_ct101_toda_rota_admin_exige_papel_de_coordenacao():
    rotas = _rotas_admin()
    assert len(rotas) >= 25, f"só {len(rotas)} rotas /api/admin achadas: o teste estático perdeu o rastro"
    sem_guarda = sorted(n for n, (_, _, ok) in rotas.items() if not ok)
    assert sem_guarda == ["admin_login", "admin_registro"], f"rotas /api/admin sem so_admin: {sem_guarda}"


def test_ct102_tokens_ruins_nao_abrem_a_coordenacao(adm):
    base = {"sub": str(adm.admin.id), "role": "admin", "nome": "X"}
    ok_exp = datetime.now(timezone.utc) + timedelta(hours=1)
    velho = datetime.now(timezone.utc) - timedelta(minutes=1)
    outro = "outro-segredo-qualquer-com-mais-de-32-caracteres"
    casos = {
        "sem cabeçalho": None, "sem Bearer": "abc", "lixo": "Bearer lixo",
        "segredo errado": "Bearer " + jwt.encode({**base, "exp": ok_exp}, outro, algorithm="HS256"),
        "expirado": "Bearer " + jwt.encode({**base, "exp": velho}, SEGREDO, algorithm="HS256"),
        "sem exp": "Bearer " + jwt.encode(base, SEGREDO, algorithm="HS256"),
        "alg none": "Bearer " + jwt.encode({**base, "exp": ok_exp}, None, algorithm="none"),
        "sub não numérico": "Bearer " + jwt.encode({**base, "sub": "abc", "exp": ok_exp}, SEGREDO, algorithm="HS256"),
        "papel inventado": "Bearer " + jwt.encode({**base, "role": "root", "exp": ok_exp}, SEGREDO, algorithm="HS256"),
        "conta inexistente": "Bearer " + jwt.encode({**base, "sub": "99999", "exp": ok_exp}, SEGREDO, algorithm="HS256"),
    }
    for nome, h in casos.items():
        assert cod(adm.usuario(h)) == 401, nome
    assert cod(adm.usuario(adm.bearer(adm.token_admin()))) == 200


def test_ct103_aluno_e_professor_nao_passam_em_so_admin(adm):
    aluno, prof = adm.semear_aluno(), adm.prof()
    for u in (aluno, prof):
        papel = "aluno" if u is aluno else "professor"
        r = adm.sessao(adm.token(u, papel))
        assert cod(r) == 200 and r[1]["role"] == papel                       # a sessão existe...
        assert cod(status_de(adm.ns["so_admin"], r[1])) == 403               # ...mas não abre /admin
    # a conta da coordenação, entrando como PROFESSOR (login comum), também não ganha o painel
    adm.db.tables[Professor][0].aprovado = True
    r = adm.login("professor", ADMIN_EMAIL, SENHA)
    assert cod(r) == 200 and adm.claims(r[1]["token"])["role"] == "professor"
    u = adm.usuario(adm.bearer(r[1]["token"]))
    assert cod(u) == 200 and cod(status_de(adm.ns["so_admin"], u[1])) == 403


def test_ct104_token_de_coordenacao_tem_papel_e_validade_curta(adm):
    r = adm.admin_login(ADMIN_EMAIL, SENHA)
    assert cod(r) == 200
    c = adm.claims(r[1]["token"])
    assert c["role"] == "admin" and "senha" not in _json.dumps(c).lower()
    assert 0 < c["exp"] - datetime.now(timezone.utc).timestamp() <= 4 * 3600 + 5     # ADMIN_TOKEN_HORAS=4 (padrão)
    assert cod(adm.sessao(r[1]["token"])) == 200


def test_ct105_coordenacao_rebaixada_desativada_ou_excluida_perde_o_painel_na_hora(adm):
    outra = adm.prof(email="outra.coord@unicerrado.edu.br", coordenacao=True)
    tok = adm.token_admin(outra)
    assert cod(adm.sessao(tok)) == 200
    assert cod(adm.rota("adm_professor_ativo", outra.id, adm.corpo("AtivoIn", ativo=False))) == 200
    assert cod(adm.sessao(tok)) == 401                                                   # desativada: cai já
    adm.rota("adm_professor_ativo", outra.id, adm.corpo("AtivoIn", ativo=True))
    assert cod(adm.sessao(tok)) == 200
    assert cod(adm.rota("adm_professor_coordenacao", outra.id, adm.corpo("AtivoIn", ativo=False))) == 200
    assert cod(adm.sessao(tok)) == 401                                                   # rebaixada: cai já
    assert cod(adm.rota("adm_professor_excluir", outra.id)) == 200
    assert cod(adm.sessao(tok)) == 401                                                   # excluída: cai já


def test_ct106_conta_de_admin_emails_e_coordenacao_e_nao_pode_ser_rebaixada():
    c = CtxAdm(ADMIN_EMAILS="chefe@unicerrado.edu.br")
    chefe = c.prof(email="chefe@unicerrado.edu.br", coordenacao=False)
    assert cod(c.admin_login("chefe@unicerrado.edu.br", SENHA)) == 200
    r = c.rota("adm_professor_coordenacao", chefe.id, c.corpo("AtivoIn", ativo=False))
    assert cod(r) == 400 and "ADMIN_EMAILS" in r[1]


# =====================================================================================================
# BLOCO B - Contas de professores e da coordenação
# =====================================================================================================
def _novo(adm, **over):
    d = dict(nome="Ana Paula Lima", email="ana.lima@unicerrado.edu.br", senha=SENHA,
             registro_funcional="DOC-2026-31", departamento="Direito", coordenacao=False)
    d.update(over)
    return adm.rota("adm_professor_novo", adm.corpo("ProfessorIn", **d))


def test_ct111_criar_professor_normaliza_dados_e_nasce_autorizado(adm):
    r = _novo(adm, nome="  Ana   Paula Lima ", email="  Ana.Lima@UniCerrado.edu.br ", registro_funcional=" doc-2026-31 ",
              departamento=" Direito ")
    assert cod(r) == 200, r
    p = next(x for x in adm.db.tables[Professor] if x.email == "ana.lima@unicerrado.edu.br")
    assert (p.nome, p.registro_funcional, p.departamento) == ("Ana Paula Lima", "DOC-2026-31", "Direito")
    assert p.aprovado is True and p.coordenacao is False and p.senha_hash.startswith("pbkdf2$") and SENHA not in p.senha_hash
    log = adm.logs("criar_professor")
    assert len(log) == 1 and log[0].usuario_id == adm.admin.id and SENHA not in (log[0].detalhe or "")


def test_ct112_professor_criado_pela_coordenacao_entra_com_a_senha_provisoria(adm):
    _novo(adm)
    r = adm.login("professor", "ana.lima@unicerrado.edu.br", SENHA)
    assert cod(r) == 200 and r[1]["usuario"]["perfil"] == "professor"


def test_ct113_criar_conta_da_coordenacao_sem_registro_funcional(adm):
    r = _novo(adm, nome="Nova Coordenadora", email="nova.coord@unicerrado.edu.br", registro_funcional=None,
              departamento=None, coordenacao=True)
    assert cod(r) == 200, r
    p = next(x for x in adm.db.tables[Professor] if x.email == "nova.coord@unicerrado.edu.br")
    assert p.coordenacao is True and p.registro_funcional is None and p.departamento == "Coordenação"
    a = adm.admin_login("nova.coord@unicerrado.edu.br", SENHA)
    assert cod(a) == 200 and adm.claims(a[1]["token"])["role"] == "admin"


def test_ct114_criar_professor_rejeita_dados_invalidos_e_duplicados(adm):
    _novo(adm)
    casos = [
        ("e-mail de outro domínio", dict(email="x@gmail.com", registro_funcional="DOC-1-1"), 403),
        ("e-mail de aluno", dict(email="x@alunos.unicerrado.edu.br", registro_funcional="DOC-1-2"), 403),
        ("e-mail sem arroba", dict(email="sem-arroba", registro_funcional="DOC-1-3"), 403),
        ("e-mail duplicado (outra caixa)", dict(email="ANA.LIMA@unicerrado.edu.br", registro_funcional="DOC-1-4"), 409),
        ("registro duplicado (outra caixa)", dict(email="outro@unicerrado.edu.br", registro_funcional="doc-2026-31"), 409),
        ("registro inválido", dict(email="b@unicerrado.edu.br", registro_funcional="a"), 422),
        ("registro ausente (professor)", dict(email="c@unicerrado.edu.br", registro_funcional=None), 422),
        ("departamento curto", dict(email="d@unicerrado.edu.br", registro_funcional="DOC-1-5", departamento="x"), 422),
        ("senha de 7 caracteres", dict(email="e@unicerrado.edu.br", registro_funcional="DOC-1-6", senha="1234567"), 422),
        ("nome só com espaços", dict(email="f@unicerrado.edu.br", registro_funcional="DOC-1-7", nome="     "), 422),
    ]
    for nome, over, esperado in casos:
        assert cod(_novo(adm, **over)) == esperado, nome
    assert len(adm.db.tables[Professor]) == 2                                          # coordenadora + Ana: nada vazou


def test_ct115_email_com_acento_nao_cria_conta_que_nunca_consegue_entrar(adm):
    r = _novo(adm, email="josé.silva@unicerrado.edu.br", registro_funcional="DOC-1-9")
    assert cod(r) in (403, 422), r


def test_ct116_corrida_na_criacao_do_professor_vira_409(adm):
    def concorrente():
        o = Professor(nome="Outro", email="ana.lima@unicerrado.edu.br", senha_hash="x", registro_funcional="DOC-9-9",
                      ativo=True, coordenacao=False, aprovado=True)
        o.id = 500
        adm.db.tables[Professor].append(o)
    adm.db.antes_do_commit = concorrente
    assert cod(_novo(adm)) == 409


def test_ct117_autorizar_professor_pendente(adm):
    pend = adm.prof(email="pendente@unicerrado.edu.br", aprovado=False)
    antes = adm.login("professor", "pendente@unicerrado.edu.br", SENHA)
    assert cod(antes) == 403                                                             # ainda não entra
    assert cod(adm.rota("adm_professor_autorizar", pend.id)) == 200
    assert cod(adm.login("professor", "pendente@unicerrado.edu.br", SENHA)) == 200
    r2 = adm.rota("adm_professor_autorizar", pend.id)                                    # dois cliques / duas pessoas
    assert cod(r2) == 200 and r2[1].get("ja_autorizado") is True and len(adm.logs("autorizar_professor")) == 1
    assert cod(adm.rota("adm_professor_autorizar", 9999)) == 404


def test_ct118_recusar_professor_pendente_so_apaga_cadastro_aguardando(adm):
    pend = adm.prof(email="pendente@unicerrado.edu.br", aprovado=False)
    ok = adm.prof(email="autorizado@unicerrado.edu.br")
    assert cod(adm.rota("adm_professor_recusar", pend.id)) == 200
    assert all(p.email != "pendente@unicerrado.edu.br" for p in adm.db.tables[Professor]) and len(adm.logs("recusar_professor")) == 1
    assert cod(adm.reg_prof(email="pendente@unicerrado.edu.br", registro_funcional="DOC-2026-99")) == 200  # pode pedir de novo
    r = adm.rota("adm_professor_recusar", ok.id)
    assert cod(r) == 409 and any(p.id == ok.id for p in adm.db.tables[Professor])
    assert cod(adm.rota("adm_professor_recusar", 9999)) == 404
    # corrida: o professor foi autorizado entre a checagem e o DELETE; a trava "aprovado = falso" do DELETE protege
    adm.ns["aguardando_autorizacao"] = lambda p: True
    assert cod(adm.rota("adm_professor_recusar", ok.id)) == 200
    assert any(p.id == ok.id for p in adm.db.tables[Professor]), "professor autorizado foi apagado por uma recusa atrasada"


def test_ct119_desativar_e_reativar_professor(adm):
    p = adm.prof(email="prof@unicerrado.edu.br")
    tok = adm.token(p, "professor")
    assert cod(adm.sessao(tok)) == 200
    r = adm.rota("adm_professor_ativo", adm.admin.id, adm.corpo("AtivoIn", ativo=False))
    assert cod(r) == 400                                                                 # não desativa a si mesma
    assert cod(adm.rota("adm_professor_ativo", p.id, adm.corpo("AtivoIn", ativo=False))) == 200
    assert cod(adm.sessao(tok)) == 401 and cod(adm.login("professor", "prof@unicerrado.edu.br", SENHA)) == 403
    assert cod(adm.rota("adm_professor_ativo", p.id, adm.corpo("AtivoIn", ativo=True))) == 200
    assert cod(adm.login("professor", "prof@unicerrado.edu.br", SENHA)) == 200
    assert cod(adm.rota("adm_professor_ativo", 9999, adm.corpo("AtivoIn", ativo=True))) == 404
    assert cod(adm.rota("adm_professor_ativo", p.id, adm.corpo("AtivoIn", ativo="talvez"))) == 422
    assert len(adm.logs("professor_ativo")) == 2                                         # só as duas mudanças reais


def test_ct120_promover_e_rebaixar_coordenacao(adm):
    pend = adm.prof(email="pend@unicerrado.edu.br", aprovado=False)
    prof = adm.prof(email="prof@unicerrado.edu.br")
    assert cod(adm.rota("adm_professor_coordenacao", pend.id, adm.corpo("AtivoIn", ativo=True))) == 409
    assert cod(adm.admin_login("prof@unicerrado.edu.br", SENHA)) == 401                 # ainda não é coordenação
    assert cod(adm.rota("adm_professor_coordenacao", prof.id, adm.corpo("AtivoIn", ativo=True))) == 200
    assert cod(adm.admin_login("prof@unicerrado.edu.br", SENHA)) == 200
    r = adm.rota("adm_professor_coordenacao", adm.admin.id, adm.corpo("AtivoIn", ativo=False))
    assert cod(r) == 400                                                                 # não se rebaixa
    assert cod(adm.rota("adm_professor_coordenacao", prof.id, adm.corpo("AtivoIn", ativo=False))) == 200
    assert cod(adm.admin_login("prof@unicerrado.edu.br", SENHA)) == 401
    assert cod(adm.rota("adm_professor_coordenacao", 9999, adm.corpo("AtivoIn", ativo=True))) == 404
    assert len(adm.logs("professor_coordenacao")) == 2


def test_ct121_excluir_professor_remove_aulas_e_presencas_so_dele(adm):
    a1, a2 = adm.semear_aluno(email="a1@alunos.unicerrado.edu.br"), adm.semear_aluno(email="a2@alunos.unicerrado.edu.br")
    alvo, outro = adm.prof(email="alvo@unicerrado.edu.br"), adm.prof(email="outro@unicerrado.edu.br")
    x1, x2, y1 = adm.aula(alvo, "X1", ativa=False), adm.aula(alvo, "X2"), adm.aula(outro, "Y1")
    adm.presenca(x1, a1), adm.presenca(x2, a2), adm.presenca(y1, a1)
    adm.db.add(Tentativa(aula_id=x1.id, aluno_id=a2.id))
    adm.db.add(Tentativa(aula_id=y1.id, aluno_id=a2.id))
    adm.db.commit()
    tok = adm.token(alvo, "professor")
    adm.ns["_cache_ativas"]["t"] = 123.0
    assert cod(adm.rota("adm_professor_excluir", alvo.id)) == 200
    assert [a.titulo for a in adm.db.tables[Aula]] == ["Y1"]
    assert [(p.aula_id) for p in adm.db.tables[Presenca]] == [y1.id] and [t.aula_id for t in adm.db.tables[Tentativa]] == [y1.id]
    assert all(p.id != alvo.id for p in adm.db.tables[Professor]) and any(p.id == outro.id for p in adm.db.tables[Professor])
    assert adm.ns["_cache_ativas"]["t"] == 0 and len(adm.logs("excluir_professor")) == 1
    assert cod(adm.sessao(tok)) == 401


def test_ct122_excluir_professor_protege_a_propria_conta_e_a_coordenacao(adm):
    outra = adm.prof(email="outra.coord@unicerrado.edu.br", coordenacao=True)
    assert cod(adm.rota("adm_professor_excluir", adm.admin.id)) == 400
    r = adm.rota("adm_professor_excluir", outra.id)
    assert cod(r) == 400 and "coordenação" in r[1].lower()
    assert cod(adm.rota("adm_professor_excluir", 9999)) == 404
    assert len(adm.db.tables[Professor]) == 2


def test_ct123_redefinir_senha_de_aluno_e_professor(adm):
    a, p = adm.semear_aluno(), adm.prof(email="prof@unicerrado.edu.br")
    for perfil, u, email in (("aluno", a, EMAIL_ALUNO), ("professor", p, "prof@unicerrado.edu.br")):
        r = adm.rota("adm_senha", adm.corpo("SenhaIn", perfil=perfil, id=u.id, nova_senha=NOVA_SENHA))
        assert cod(r) == 200, perfil
        assert cod(adm.login(perfil, email, NOVA_SENHA)) == 200 and cod(adm.login(perfil, email, SENHA)) == 401
    log = adm.logs("redefinir_senha")
    assert [l.detalhe for l in log] == [f"aluno={a.id}", f"professor={p.id}"]
    assert all(NOVA_SENHA not in (l.detalhe or "") for l in log)
    assert cod(adm.rota("adm_senha", adm.corpo("SenhaIn", perfil="aluno", id=9999, nova_senha=NOVA_SENHA))) == 404
    assert cod(adm.rota("adm_senha", adm.corpo("SenhaIn", perfil="aluno", id=a.id, nova_senha="curta"))) == 422
    assert cod(adm.rota("adm_senha", adm.corpo("SenhaIn", perfil="admin", id=a.id, nova_senha=NOVA_SENHA))) == 422


def test_ct124_redefinir_a_senha_derruba_as_sessoes_abertas_da_conta(adm):
    a = adm.semear_aluno()
    antiga = adm.login("aluno", EMAIL_ALUNO, SENHA)[1]["token"]                          # sessão de quem (talvez) invadiu a conta
    assert cod(adm.sessao(antiga)) == 200
    adm.rota("adm_senha", adm.corpo("SenhaIn", perfil="aluno", id=a.id, nova_senha=NOVA_SENHA))
    # sem esperar os 5 s do cache e sem limpar nada à mão: é a rota que tem de derrubar a sessão
    assert cod(adm.sessao(antiga)) == 401, "a sessão antiga continua valendo depois da redefinição da senha"


def _adm_com_provisoria():
    c = CtxAdm(senha_admin=SENHA_PROV, COORD_SENHA=SENHA_PROV, COORD_EMAIL=ADMIN_EMAIL)
    return c


def test_ct125_trocar_a_propria_senha(adm):
    c = _adm_com_provisoria()
    def troca(**kw):
        d = dict(senha_atual=SENHA_PROV, nova_senha=NOVA_SENHA, confirmar_senha=NOVA_SENHA)
        d.update(kw)
        return c.rota("adm_minha_senha", c.corpo("MinhaSenhaIn", **d))
    assert c.ns["senha_provisoria_ativa"](c.admin) is True
    assert cod(troca(senha_atual="errada-errada")) == 400
    assert cod(troca(confirmar_senha="Outra#Senha2027")) == 422
    assert cod(troca(nova_senha=SENHA_PROV, confirmar_senha=SENHA_PROV)) == 422          # não vale repetir a provisória
    assert cod(troca(nova_senha="curta", confirmar_senha="curta")) == 422
    assert cod(troca()) == 200
    assert c.ns["senha_provisoria_ativa"](c.admin) is False and len(c.logs("trocar_senha_admin")) == 1
    assert cod(c.admin_login(ADMIN_EMAIL, NOVA_SENHA)) == 200 and cod(c.admin_login(ADMIN_EMAIL, SENHA_PROV)) == 401


def test_ct125b_trocar_a_propria_senha_devolve_token_novo_que_continua_valendo():
    c = _adm_com_provisoria()
    r = c.rota("adm_minha_senha", c.corpo("MinhaSenhaIn", senha_atual=SENHA_PROV, nova_senha=NOVA_SENHA))
    assert cod(r) == 200 and r[1]["ok"] is True
    assert cod(c.sessao(r[1]["token"])) == 200 and c.claims(r[1]["token"])["role"] == "admin"


def test_ct126_trocar_senha_tem_freio_de_tentativas():
    c = _adm_com_provisoria()
    res = [cod(c.rota("adm_minha_senha", c.corpo("MinhaSenhaIn", senha_atual=f"errada-{i}-xx", nova_senha=NOVA_SENHA)))
           for i in range(10)]
    assert res[:8] == [400] * 8 and res[8:] == [429, 429], res


def test_ct127_trocar_a_propria_senha_derruba_as_outras_sessoes():
    c = _adm_com_provisoria()
    outra_sessao = c.admin_login(ADMIN_EMAIL, SENHA_PROV)[1]["token"]
    assert cod(c.sessao(outra_sessao)) == 200
    c.rota("adm_minha_senha", c.corpo("MinhaSenhaIn", senha_atual=SENHA_PROV, nova_senha=NOVA_SENHA))
    assert cod(c.sessao(outra_sessao)) == 401, "uma sessão aberta com a senha antiga sobrevive à troca de senha"


# =====================================================================================================
# BLOCO C - Cursos
# =====================================================================================================
def _curso(adm, **kw):
    return adm.rota("adm_curso_novo", adm.corpo("CursoIn", **kw))


def test_ct141_criar_curso(adm):
    assert cod(_curso(adm, nome="  Direito  ", departamento=" Ciências Jurídicas ")) == 200
    c = next(x for x in adm.db.tables[Curso] if x.nome == "Direito")
    assert c.departamento == "Ciências Jurídicas" and c.ativo is True
    assert len(adm.logs("criar_curso")) == 1
    assert cod(_curso(adm, nome="Pedagogia", departamento="   ")) == 200
    assert next(x for x in adm.db.tables[Curso] if x.nome == "Pedagogia").departamento is None


def test_ct142_curso_duplicado_em_outra_caixa_e_409(adm):
    assert cod(_curso(adm, nome="GESTÃO DA TI")) == 409                                  # o curso padrão do teste, em outra caixa
    assert cod(_curso(adm, nome="Direito")) == 200
    assert cod(_curso(adm, nome="direito")) == 409 and cod(_curso(adm, nome="  DIREITO ")) == 409


def test_ct143_curso_so_com_espacos_nao_pode_ser_criado(adm):
    r = _curso(adm, nome="   ")
    assert cod(r) == 422, f"curso com nome vazio foi aceito: {r}"
    assert all((c.nome or "").strip() for c in adm.db.tables[Curso])


def test_ct144_curso_quase_igual_nao_pode_confundir_a_importacao(adm):
    # a importação por arquivo compara 'Gestão da TI' e 'GESTAO  DA TI' como o mesmo curso; dois cursos assim
    # fariam as matrículas caírem num deles sem aviso
    for variante in ("Gestao da TI", "Gestão  da  TI", "GESTÃO DA TI"):
        r = _curso(adm, nome=variante)
        assert cod(r) == 409, f"'{variante}' foi aceito como curso novo ao lado de 'Gestão da TI'"


def test_ct145_limites_do_curso(adm):
    assert cod(_curso(adm, nome="A")) == 422
    assert cod(_curso(adm, nome="X" * 101)) == 422 and cod(_curso(adm, nome="X" * 100)) == 200
    assert cod(_curso(adm, nome="Letras", departamento="D" * 101)) == 422
    assert cod(_curso(adm, nome=None)) == 422


def test_ct146_corrida_na_criacao_de_curso_vira_409(adm):
    def concorrente():
        c = Curso(nome="Direito", ativo=True)
        c.id = 700
        adm.db.tables[Curso].append(c)
    adm.db.antes_do_commit = concorrente
    r = _curso(adm, nome="Direito")
    assert cod(r) == 409, f"duas criações simultâneas do mesmo curso devolvem {r[0]} em vez de 409"


def test_ct147_desativar_curso_some_da_lista_e_barra_novos_cadastros(adm):
    assert cod(adm.rota("adm_curso_ativo", adm.curso.id, adm.corpo("AtivoIn", ativo=False))) == 200
    assert adm.ns["listar_cursos"](adm.db) == []
    r = adm.reg_aluno()
    assert cod(r) == 422 and "curso" in r[1].lower()
    assert cod(adm.rota("adm_curso_ativo", adm.curso.id, adm.corpo("AtivoIn", ativo=True))) == 200
    assert [c["nome"] for c in adm.ns["listar_cursos"](adm.db)] == ["Gestão da TI"] and cod(adm.reg_aluno()) == 200
    assert cod(adm.rota("adm_curso_ativo", 9999, adm.corpo("AtivoIn", ativo=True))) == 404
    assert cod(adm.rota("adm_curso_ativo", adm.curso.id, adm.corpo("AtivoIn", ativo="talvez"))) == 422
    assert len(adm.logs("curso_ativo")) == 2


def test_ct148_aluno_de_curso_desativado_continua_entrando(adm):
    adm.semear_aluno()
    adm.rota("adm_curso_ativo", adm.curso.id, adm.corpo("AtivoIn", ativo=False))
    assert cod(adm.login("aluno", EMAIL_ALUNO, SENHA)) == 200


# =====================================================================================================
# BLOCO D - Matrículas autorizadas: importação por texto, exclusão e ligação com o cadastro
# =====================================================================================================
def test_ct151_importar_texto_conta_novas_repetidas_e_invalidas(adm):
    texto = "\n".join(["202610405", "  gti202610406  ", "202610405", "", "   ", "12", "ab cd!", "202610407;Gestão da TI",
                       "202610408,gestão da ti", "202610409\tGESTÃO DA TI", "202610410;Curso Que Não Existe"])
    r = adm.texto(texto)
    assert cod(r) == 200, r
    r = r[1]
    assert r["adicionadas"] == 5 and r["ja_existiam"] == 0 and r["invalidas"] == 3, r      # 12, "ab cd!", curso inexistente
    assert set(adm.mats()) == {"202610405", "GTI202610406", "202610407", "202610408", "202610409"}
    assert any("curso 'Curso Que Não Existe'" in e for e in r["exemplos_invalidas"])
    assert adm.mats()["202610407"].curso_id == adm.curso.id and adm.mats()["202610405"].curso_id is None
    assert adm.mats()["202610405"].utilizada is False
    log = adm.logs("importar_matriculas")
    assert len(log) == 1 and "5 nova(s)" in log[0].detalhe


def test_ct152_curso_padrao_da_importacao(adm):
    assert cod(adm.texto("202610405", curso_id=9999)) == 422
    r = adm.texto("202610405\n202610406;Gestão da TI", curso_id=adm.curso_id if hasattr(adm, "curso_id") else adm.curso.id)
    assert cod(r) == 200 and {m.curso_id for m in adm.mats().values()} == {adm.curso.id}
    assert adm.db.tables[MatriculasAutorizadas] and len(adm.mats()) == 2


def test_ct153_reimportar_nao_duplica_nem_muda_o_que_ja_existe(adm):
    adm.texto("202610405\n202610406")
    r = adm.texto("202610405\n202610406\n202610407")
    assert cod(r) == 200 and r[1]["adicionadas"] == 1 and r[1]["ja_existiam"] == 2
    assert len(adm.db.tables[MatriculasAutorizadas]) == 3


def test_ct154_matricula_de_aluno_ja_cadastrado_entra_como_utilizada(adm):
    a = adm.semear_aluno(matricula="202610405")
    adm.texto("202610405\n202610406")
    assert adm.mats()["202610405"].utilizada is True and adm.mats()["202610406"].utilizada is False


def test_ct155_limites_de_tamanho_da_importacao(adm):
    assert cod(adm.texto("\n".join(str(200000000 + i) for i in range(20001)))) == 413
    assert cod(adm.texto("1" * 1_000_001)) == 422                                       # campo texto > 1.000.000 caracteres
    assert cod(adm.texto("")) == 200 and adm.db.tables[MatriculasAutorizadas] == []     # texto vazio: nada acontece


def test_ct156_corrida_na_importacao_vira_409(adm):
    def concorrente():
        m = MatriculasAutorizadas(matricula="202610405", utilizada=False)
        m.id = 800
        adm.db.tables[MatriculasAutorizadas].append(m)
    adm.db.antes_do_commit = concorrente
    assert cod(adm.texto("202610405")) == 409


def test_ct157_excluir_matricula_so_se_estiver_livre(adm):
    adm.texto("202610405\n202610406")
    livre, usada = adm.mats()["202610405"], adm.mats()["202610406"]
    usada.utilizada = True
    assert cod(adm.rota("adm_matricula_excluir", usada.id)) == 409 and "202610406" in adm.mats()
    assert cod(adm.rota("adm_matricula_excluir", livre.id)) == 200 and "202610405" not in adm.mats()
    assert cod(adm.rota("adm_matricula_excluir", livre.id)) == 404
    assert [l.detalhe for l in adm.logs("excluir_matricula")] == ["202610405"]


def test_ct158_lista_obrigatoria_controla_o_cadastro_do_aluno():
    c = CtxAdm(MATRICULA_OBRIGATORIA=1)
    c.texto("202610405;Gestão da TI\n202610406")
    assert cod(c.reg_aluno(matricula="202699999")) == 403                                # fora da lista
    ok = c.reg_aluno(matricula="202610405")
    assert cod(ok) == 200 and c.mats()["202610405"].utilizada is True
    outro = c.reg_aluno(email="outra.pessoa@alunos.unicerrado.edu.br", matricula="202610405")
    assert cod(outro) == 409                                                             # uma matrícula, uma conta
    c.curso_novo("Direito")
    dir_id = next(x.id for x in c.db.tables[Curso] if x.nome == "Direito")
    r = c.reg_aluno(email="b@alunos.unicerrado.edu.br", matricula="202610405", curso_id=dir_id)
    assert cod(r) in (409, 422)                                                          # curso diferente do da lista
    r = c.reg_aluno(email="c@alunos.unicerrado.edu.br", matricula="202610406", curso_id=dir_id)
    assert cod(r) == 200                                                                 # sem curso na lista: qualquer um serve
    assert cod(c.reg_aluno(email="d@alunos.unicerrado.edu.br", matricula=" gti 202 ")) == 403


def test_ct159_excluir_aluno_devolve_a_matricula_para_a_lista():
    c = CtxAdm(MATRICULA_OBRIGATORIA=1)
    c.texto("202610405")
    c.reg_aluno(matricula="202610405")
    aluno = c.db.tables[Aluno][0]
    assert c.mats()["202610405"].utilizada is True
    assert cod(c.rota("adm_aluno_excluir", aluno.id)) == 200
    assert c.mats()["202610405"].utilizada is False and c.db.tables[Aluno] == []
    assert cod(c.reg_aluno(email="novo.dono@alunos.unicerrado.edu.br", matricula="202610405")) == 200


def test_ct160_texto_com_crlf_e_bom_e_normalizado_igual_ao_cadastro(adm):
    r = adm.texto("﻿202610405\r\n  gti 202610406 \r\n202610407;gestão da ti\r\n")
    assert cod(r) == 200, r
    assert set(adm.mats()) == {"202610405", "GTI202610406", "202610407"} or r[1]["invalidas"] == 1, adm.mats()


# =====================================================================================================
# BLOCO E - Matrículas autorizadas: importação por arquivo (CSV, TXT, XLSX)
# =====================================================================================================
CSV_PADRAO = ("Matrícula;Nome;CPF;Curso;Situação\n"
              "202610405;Ana Souza;123.456.789-09;Gestão da TI;Ativo\n"
              "202610406;Bruno Lima;987.654.321-00;GESTAO DA TI;Ativo\n"
              "202610407;Carla Dias;111.222.333-44;Direito;Ativo\n"
              "202610408;Davi Reis;555.666.777-88;Engenharia Civil;Ativo\n")


def test_ct171_previa_nao_grava_e_nao_mostra_dados_pessoais(adm):
    adm.curso_novo("Direito")
    r = adm.arquivo(CSV_PADRAO.encode("utf-8"))
    assert cod(r) == 200, r
    r = r[1]
    assert r["importado"] is False and r["novas"] == 4 and r["col_matricula"] == 0 and r["col_curso"] == 3
    assert r["formato"] == "csv" and r["separador"] == "ponto e vírgula" and r["n_cursos_desconhecidos"] == 1
    assert adm.db.tables[MatriculasAutorizadas] == [] and adm.logs("importar_matriculas") == []
    assert "123.456.789-09" not in _json.dumps(r, ensure_ascii=False) and "Ana Souza" not in _json.dumps(r, ensure_ascii=False)


def test_ct172_confirmar_importa_e_liga_cada_matricula_ao_curso(adm):
    dir_ = adm.curso_novo("Direito")
    r = adm.arquivo(CSV_PADRAO.encode(), confirmar=True)
    assert cod(r) == 200 and r[1]["importado"] is True and r[1]["adicionadas"] == 4, r
    m = adm.mats()
    assert (m["202610405"].curso_id, m["202610406"].curso_id, m["202610407"].curso_id) == (adm.curso.id, adm.curso.id, dir_.id)
    assert m["202610408"].curso_id is None                                               # curso desconhecido: sem curso
    log = adm.logs("importar_matriculas")[0].detalhe
    assert "123.456" not in log and "Ana" not in log and "4 nova(s)" in log


def test_ct173_mapa_de_cursos_resolve_curso_desconhecido(adm):
    dir_ = adm.curso_novo("Direito")
    r = adm.arquivo(CSV_PADRAO.encode(), confirmar=True, mapa_cursos={"Engenharia Civil": dir_.id})
    assert cod(r) == 200 and adm.mats()["202610408"].curso_id == dir_.id
    assert cod(adm.arquivo(CSV_PADRAO.encode(), mapa_cursos={"x": 9999})) == 422


def test_ct174_codificacoes_do_excel_brasileiro(adm):
    for nome, dados, cod_esperada in (("cp1252", CSV_PADRAO.encode("cp1252"), "windows-1252"),
                                      ("utf-8 com BOM", b"\xef\xbb\xbf" + CSV_PADRAO.encode(), "utf-8"),
                                      ("utf-16 (Texto Unicode)", CSV_PADRAO.encode("utf-16"), "utf-16")):
        r = adm.arquivo(dados)
        assert cod(r) == 200, nome
        assert r[1]["codificacao"] == cod_esperada and r[1]["novas"] == 4, (nome, r[1]["codificacao"], r[1]["novas"])
        assert r[1]["col_matricula"] == 0 and r[1]["col_curso"] == 3, nome


def test_ct175_separadores_virgula_tabulacao_e_barra(adm):
    for sep, rotulo in ((",", "vírgula"), ("\t", "tabulação"), ("|", "barra vertical")):
        r = adm.arquivo(CSV_PADRAO.replace(";", sep).encode())
        assert cod(r) == 200 and r[1]["separador"] == rotulo and r[1]["novas"] == 4, rotulo


def test_ct176_lista_simples_sem_cabecalho(adm):
    r = adm.arquivo(b"202610405\n202610406\n202610407\n")
    assert cod(r) == 200 and r[1]["novas"] == 3 and r[1]["col_curso"] == -1
    r = adm.arquivo("202610405;Gestão da TI\n202610406;Gestão da TI\n202610407;Direito\n".encode())
    assert cod(r) == 200 and r[1]["col_curso"] == 1 and r[1]["novas"] == 3


def test_ct177_sem_coluna_de_matricula_pede_a_escolha_e_nao_chuta(adm):
    arq = "Ana Souza;Gestão da TI\nBruno Lima;Direito\nCarla Dias;Direito\n".encode()
    r = adm.arquivo(arq)
    assert cod(r) == 200 and r[1]["precisa_coluna"] is True and adm.db.tables[MatriculasAutorizadas] == []
    assert cod(adm.arquivo(arq, confirmar=True)) == 422


def test_ct178_coluna_de_cpf_nunca_vira_matricula(adm):
    cpf = "CPF;Nome;Curso\n12345678909;Ana Souza;Gestão da TI\n98765432100;Bruno Lima;Gestão da TI\n11122233344;Carla;Direito\n"
    r = adm.arquivo(cpf.encode(), confirmar=True)
    gravou = sorted(adm.mats())
    assert not gravou, f"CPFs foram gravados como matrículas autorizadas: {gravou}"
    assert cod(r) in (200, 422)
    # escolha manual da coluna do CPF também é recusada
    r2 = adm.arquivo(cpf.encode(), confirmar=True, col_matricula=0)
    assert cod(r2) == 422 and "dados pessoais" in r2[1]


def test_ct179_datas_e_cpf_formatado_nao_sao_matriculas(adm):
    arq = "Matrícula;Data de Nascimento;Documento\n202610405;01/02/2003;123.456.789-09\n202610406;15/08/2002;987.654.321-00\n"
    r = adm.arquivo(arq.encode())
    assert cod(r) == 200 and r[1]["col_matricula"] == 0 and r[1]["novas"] == 2
    arq = "Data;Documento;Aluno\n01/02/2003;123.456.789-09;202610405\n15/08/2002;987.654.321-00;202610406\n"
    r = adm.arquivo(arq.encode())
    assert cod(r) == 200 and r[1]["col_matricula"] == 2 and r[1]["novas"] == 2, r


def test_ct180_numeros_do_excel_virgula_zero_e_notacao_cientifica(adm):
    r = adm.arquivo("Matrícula;Curso\n202610405.0;Gestão da TI\n202610406,0;Gestão da TI\n2,02610407E+08;Gestão da TI\n".encode(), confirmar=True)
    assert cod(r) == 200 and set(adm.mats()) == {"202610405", "202610406"}
    assert r[1]["invalidas"] == 1 and "notação científica" in r[1]["exemplos_invalidas"][0]


def test_ct181_planilha_xlsx_com_numeros_e_abas(adm):
    dados = xlsx(None, abas=[[], [["Matrícula", "Curso"], [202610405, "Gestão da TI"], [202610406.0, "Gestão da TI"],
                                  ["GTI202610407", None]]])
    r = adm.arquivo(dados, nome="alunos.xlsx", confirmar=True)
    assert cod(r) == 200 and r[1]["formato"] == "xlsx" and r[1]["adicionadas"] == 3, r
    assert set(adm.mats()) == {"202610405", "202610406", "GTI202610407"} and adm.mats()["202610405"].curso_id == adm.curso.id


def test_ct182_arquivos_que_nao_sao_lista_viram_mensagem_amigavel(adm):
    casos = {
        "xls antigo": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"x" * 50, "pdf": b"%PDF-1.7 ...", "png": b"\x89PNG\r\n\x1a\n" + b"x" * 20,
        "jpg": b"\xff\xd8\xff\xe0" + b"x" * 20, "html disfarçado": b"<html><table><tr><td>202610405</td></tr></table></html>",
        "binário": bytes(range(256)) * 8, "xlsx corrompido": b"PK\x03\x04" + b"lixo" * 20,
    }
    for nome, dados in casos.items():
        r = adm.arquivo(dados)
        assert cod(r) == 422 and isinstance(r[1], str) and len(r[1]) > 20, (nome, r)
    assert adm.db.tables[MatriculasAutorizadas] == []


def test_ct183_zip_bomb_e_planilha_gigante_sao_recusadas(adm):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", "<workbook/>")
        for i in range(401):
            z.writestr(f"x/{i}.xml", "a")
    assert cod(adm.arquivo(buf.getvalue(), nome="a.xlsx")) == 413
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/workbook.xml", "<workbook/>")
        z.writestr("xl/grande.xml", b"\0" * (61 * 1024 * 1024))
    assert len(buf.getvalue()) < 1_000_000 and cod(adm.arquivo(buf.getvalue(), nome="b.xlsx")) == 413


def test_ct184_tamanho_base64_e_vazio(adm):
    assert cod(adm.rota("adm_matriculas_arquivo", adm.corpo("ArquivoMatriculasIn", dados="@@@não-é-base64@@@"))) == 422
    assert cod(adm.rota("adm_matriculas_arquivo", adm.corpo("ArquivoMatriculasIn", dados=""))) == 422
    assert cod(adm.arquivo(b"\n\n  \n")) == 422                                          # só linhas em branco
    assert cod(adm.arquivo(b"x" * (5 * 1024 * 1024 + 1))) == 413
    assert cod(adm.rota("adm_matriculas_arquivo", adm.corpo("ArquivoMatriculasIn", dados="A" * (adm.ns["ARQ_B64_MAX"] + 1)))) == 422


def test_ct185_mais_de_20_mil_linhas_e_recusado(adm):
    arq = ("Matrícula\n" + "\n".join(str(300000000 + i) for i in range(20001))).encode()
    assert cod(adm.arquivo(arq)) == 413


def test_ct186_atualizar_cursos_so_mexe_nas_matriculas_livres(adm):
    dir_ = adm.curso_novo("Direito")
    adm.texto("202610405\n202610406\n202610407")
    adm.mats()["202610406"].utilizada = True
    arq = "Matrícula;Curso\n202610405;Direito\n202610406;Direito\n202610407;Gestão da TI\n".encode()
    r = adm.arquivo(arq, confirmar=True)
    assert cod(r) == 200 and r[1]["atualizadas"] == 0 and r[1]["atualizaveis"] == 2         # sem atualizar_cursos: nada muda
    assert all(m.curso_id in (None,) for m in adm.mats().values())
    r = adm.arquivo(arq, confirmar=True, atualizar_cursos=True)
    m = adm.mats()
    assert (m["202610405"].curso_id, m["202610407"].curso_id) == (dir_.id, adm.curso.id)
    assert m["202610406"].curso_id is None, "matrícula já utilizada teve o curso alterado"


def test_ct187_repetidas_no_arquivo_e_prévia_igual_a_importacao(adm):
    arq = "Matrícula\n202610405\n202610405\n202610406\n\n202610407\nxx\n".encode()
    previa = adm.arquivo(arq)[1]
    real = adm.arquivo(arq, confirmar=True)[1]
    assert (previa["novas"], previa["repetidas"], previa["invalidas"], previa["sem_matricula"]) == (3, 1, 1, 0)
    assert real["adicionadas"] == previa["novas"] and len(adm.mats()) == 3


def test_ct188_colunas_escolhidas_a_mao_sao_validadas(adm):
    arq = CSV_PADRAO.encode()
    assert cod(adm.arquivo(arq, col_matricula=40)) == 422
    assert cod(adm.arquivo(arq, col_matricula=0, col_curso=0)) == 422
    assert cod(adm.arquivo(arq, col_matricula=0, col_curso=2)) == 422                    # coluna do CPF como "curso"
    assert cod(adm.arquivo(arq, col_matricula=0, col_curso=-1)) == 200
    assert cod(adm.rota("adm_matriculas_arquivo", adm.corpo("ArquivoMatriculasIn", dados="QQ==", col_matricula=-1))) == 422


def test_ct189_nome_do_arquivo_no_log_nao_guarda_numeros_longos(adm):
    adm.arquivo(CSV_PADRAO.encode(), nome="alunos 123.456.789-09 202610405.csv", confirmar=True)
    d = adm.logs("importar_matriculas")[0].detalhe
    assert "123.456.789" not in d and "202610405" not in d, d


def test_ct190_freio_de_40_arquivos_em_10_minutos(adm):
    codigos = [cod(adm.arquivo(b"202610405\n")) for _ in range(42)]
    assert codigos[:40] == [200] * 40 and codigos[40:] == [429, 429], codigos


# =====================================================================================================
# BLOCO F - Pedidos de senha (lado da coordenação)
# =====================================================================================================
def _pedidos(adm):
    return status_de(adm.ns["adm_pedidos_senha"], adm.u, adm.db)


def test_ct201_pedido_aparece_para_a_coordenacao_com_dados_para_conferir(adm):
    a = adm.semear_aluno()
    assert cod(adm.esqueci("aluno", EMAIL_ALUNO, a.matricula)) == 200
    r = _pedidos(adm)
    assert cod(r) == 200 and len(r[1]) == 1
    p = r[1][0]
    assert (p["perfil"], p["usuario_id"], p["atendido"]) == ("aluno", a.id, False)
    assert EMAIL_ALUNO in p["info"] and a.matricula in p["info"] and "senha_hash" not in p["info"] and "pbkdf2" not in p["info"]


def test_ct202_pedido_atendido_quando_a_coordenacao_redefine_e_reabre_se_pedir_de_novo(adm):
    a = adm.semear_aluno()
    adm.esqueci("aluno", EMAIL_ALUNO, a.matricula, ip="198.51.100.21")
    assert _pedidos(adm)[1][0]["atendido"] is False
    adm.rota("adm_senha", adm.corpo("SenhaIn", perfil="aluno", id=a.id, nova_senha=NOVA_SENHA))
    assert [p["atendido"] for p in _pedidos(adm)[1]] == [True]
    adm.esqueci("aluno", EMAIL_ALUNO, a.matricula, ip="198.51.100.22")
    assert [p["atendido"] for p in _pedidos(adm)[1]] == [False, True]                    # novo pedido na frente, pendente
    assert sum(1 for p in _pedidos(adm)[1] if not p["atendido"]) == 1


def test_ct203_pedidos_de_professor_e_de_usuarios_diferentes_nao_se_misturam(adm):
    a, p = adm.semear_aluno(), adm.prof(email="prof@unicerrado.edu.br", registro_funcional="DOC-2026-50")
    adm.esqueci("aluno", EMAIL_ALUNO, a.matricula, ip="198.51.100.31")
    adm.esqueci("professor", "prof@unicerrado.edu.br", "doc-2026-50", ip="198.51.100.32")
    adm.rota("adm_senha", adm.corpo("SenhaIn", perfil="aluno", id=a.id, nova_senha=NOVA_SENHA))
    por = {x["perfil"]: x for x in _pedidos(adm)[1]}
    assert por["aluno"]["atendido"] is True and por["professor"]["atendido"] is False
    # redefinir a senha de um PROFESSOR com o mesmo número de id de um aluno não pode atender o pedido do aluno
    adm.esqueci("aluno", EMAIL_ALUNO, a.matricula, ip="198.51.100.33")
    adm.rota("adm_senha", adm.corpo("SenhaIn", perfil="professor", id=a.id, nova_senha=NOVA_SENHA))
    assert _pedidos(adm)[1][0]["perfil"] == "aluno" and _pedidos(adm)[1][0]["atendido"] is False


def test_ct204_lista_mostra_so_os_100_mais_recentes_do_mais_novo_para_o_mais_antigo(adm):
    for i in range(1, 121):
        adm.db.add(LogSeguranca(acao="pedido_senha", detalhe=f"aluno={i}; Fulano {i}; f{i}@x; ref=1", ip="198.51.100.1"))
    adm.db.commit()
    ped = _pedidos(adm)[1]
    assert len(ped) == 100 and ped[0]["usuario_id"] == 120 and ped[-1]["usuario_id"] == 21


def test_ct205_texto_digitado_no_pedido_nao_engana_a_leitura_do_pedido(adm):
    a = adm.semear_aluno()
    adm.db.add(LogSeguranca(acao="pedido_senha", detalhe=f"aluno={a.id}; Mallory; m@x; ref=1;professor=1", ip="198.51.100.1"))
    adm.db.commit()
    p = _pedidos(adm)[1][0]
    assert (p["perfil"], p["usuario_id"]) == ("aluno", a.id)
    assert cod(adm.esqueci("aluno", EMAIL_ALUNO, "9" * 40)) == 200                       # referência no tamanho máximo


# =====================================================================================================
# BLOCO G - Gestão de alunos
# =====================================================================================================
def _editar(adm, aluno_id, **kw):
    return adm.rota("adm_aluno_editar", aluno_id, adm.corpo("AlunoEdicaoIn", **kw))


def test_ct221_editar_curso_periodo_e_status(adm):
    a = adm.semear_aluno()
    dir_ = adm.curso_novo("Direito")
    assert cod(_editar(adm, a.id, curso_id=dir_.id, periodo=5, status="inativo")) == 200
    assert (a.curso_id, a.periodo, a.status) == (dir_.id, 5, "inativo")
    log = adm.logs("editar_aluno")[0].detalhe
    assert f"aluno={a.id}" in log and "periodo=5" in log and "status=inativo" in log
    assert cod(_editar(adm, a.id, status="ativo")) == 200 and a.status == "ativo"


def test_ct222_editar_aluno_rejeita_valores_invalidos(adm):
    a = adm.semear_aluno()
    assert cod(_editar(adm, a.id, periodo=0)) == 422 and cod(_editar(adm, a.id, periodo=11)) == 422
    assert cod(_editar(adm, a.id, status="banido")) == 422
    assert cod(_editar(adm, a.id)) == 422                                                # nada para alterar
    assert cod(_editar(adm, a.id, curso_id=9999)) == 404 and cod(_editar(adm, 9999, periodo=2)) == 404
    assert (a.curso_id, a.periodo, a.status) == (adm.curso.id, 2, "ativo")


def test_ct223_aluno_inativado_perde_a_sessao_na_hora_e_volta_ao_reativar(adm):
    a = adm.semear_aluno()
    tok = adm.login("aluno", EMAIL_ALUNO, SENHA)[1]["token"]
    assert cod(adm.sessao(tok)) == 200
    _editar(adm, a.id, status="inativo")
    assert cod(adm.sessao(tok)) == 401 and cod(adm.login("aluno", EMAIL_ALUNO, SENHA)) == 403
    _editar(adm, a.id, status="ativo")
    assert cod(adm.sessao(tok)) == 200 and cod(adm.login("aluno", EMAIL_ALUNO, SENHA)) == 200


def test_ct224_mudar_curso_ou_periodo_vale_na_sessao_aberta_do_aluno(adm):
    a = adm.semear_aluno()
    dir_ = adm.curso_novo("Direito")
    tok = adm.login("aluno", EMAIL_ALUNO, SENHA)[1]["token"]
    _editar(adm, a.id, curso_id=dir_.id, periodo=7)
    r = adm.sessao(tok)
    assert cod(r) == 200
    # /aulas/ativas filtra os eventos por estes dois valores: precisam ser os NOVOS, senão o aluno vê (e tenta
    # registrar) eventos do curso/período antigos e não enxerga os do novo, até o token vencer (12 h)
    assert (r[1]["cid"], r[1]["per"]) == (dir_.id, 7), f"sessão aberta ainda usa curso/período antigos: {(r[1]['cid'], r[1]['per'])}"


def test_ct225_excluir_aluno_apaga_so_os_dados_dele(adm):
    a, b = adm.semear_aluno(email="a@alunos.unicerrado.edu.br"), adm.semear_aluno(email="b@alunos.unicerrado.edu.br")
    prof = adm.prof()
    x = adm.aula(prof)
    adm.presenca(x, a), adm.presenca(x, b)
    adm.db.add(Tentativa(aula_id=x.id, aluno_id=a.id)); adm.db.add(Tentativa(aula_id=x.id, aluno_id=b.id))
    adm.db.add(NotaAluno(aluno_id=a.id, presenca_id=1)); adm.db.add(NotaAluno(aluno_id=b.id, presenca_id=2))
    adm.db.commit()
    tok = adm.login("aluno", "a@alunos.unicerrado.edu.br", SENHA)[1]["token"]
    assert cod(adm.rota("adm_aluno_excluir", a.id)) == 200
    assert [r.aluno_id for r in adm.db.tables[Presenca]] == [b.id] and [r.aluno_id for r in adm.db.tables[Tentativa]] == [b.id]
    assert [r.aluno_id for r in adm.db.tables[NotaAluno]] == [b.id] and [r.id for r in adm.db.tables[Aluno]] == [b.id]
    assert cod(adm.sessao(tok)) == 401 and len(adm.logs("excluir_aluno")) == 1
    assert cod(adm.rota("adm_aluno_excluir", a.id)) == 404


# =====================================================================================================
# BLOCO H - Reparos do QA: marca da senha no token, CPF e nomes de curso
# =====================================================================================================
def test_ct128_marca_da_senha_no_token_e_tokens_antigos(adm):
    a = adm.semear_aluno()
    tok = adm.login("aluno", EMAIL_ALUNO, SENHA)[1]["token"]
    c = adm.claims(tok)
    assert len(c["pv"]) == 16 and a.senha_hash.split("$")[2][:16] not in c["pv"] and SENHA not in _json.dumps(c)
    pv_certo = c["pv"]
    base = {k: v for k, v in c.items() if k != "pv"}
    adulterado = jwt.encode({**base, "pv": "0" * 16}, SEGREDO, algorithm="HS256")
    legado = jwt.encode(base, SEGREDO, algorithm="HS256")                    # emitido antes desta versão: sem 'pv'
    assert cod(adm.sessao(adulterado)) == 401
    assert cod(adm.sessao(legado)) == 200                                    # vale até vencer (no máx. TOKEN_HORAS)
    assert cod(adm.sessao(tok)) == 200 and c["pv"] == pv_certo


def test_ct129_marca_da_senha_vale_para_professor_e_coordenacao(adm):
    p = adm.prof(email="prof@unicerrado.edu.br")
    tp = adm.login("professor", "prof@unicerrado.edu.br", SENHA)[1]["token"]
    ta = adm.token_admin()
    assert cod(adm.sessao(tp)) == 200 and cod(adm.sessao(ta)) == 200
    adm.rota("adm_senha", adm.corpo("SenhaIn", perfil="professor", id=p.id, nova_senha=NOVA_SENHA))
    adm.rota("adm_senha", adm.corpo("SenhaIn", perfil="professor", id=adm.admin.id, nova_senha=NOVA_SENHA))
    assert cod(adm.sessao(tp)) == 401 and cod(adm.sessao(ta)) == 401                       # sem esperar o cache de 5 s
    assert cod(adm.sessao(adm.login("professor", "prof@unicerrado.edu.br", NOVA_SENHA)[1]["token"])) == 200


def _cpfs(n, base=100000000):
    """Gera n CPFs válidos (só dígitos) a partir de uma base, para o teste não depender de números decorados."""
    saida = []
    for i in range(n):
        d = [int(x) for x in str(base + i * 7919)]
        for k in (9, 10):
            d.append((sum(v * (k + 1 - j) for j, v in enumerate(d)) * 10 % 11) % 10)
        saida.append("".join(map(str, d)))
    return saida


_NOMES = ["Ana Souza", "Bruno Lima", "Carla Dias", "Diego Rocha", "Elisa Neves", "Fabio Cruz"]


def test_ct191_cpf_sem_pontuacao_com_cabecalho_generico_nao_vira_matricula(adm):
    cpfs = _cpfs(6)
    assert all(adm.ns["_cpf_valido"](c) for c in cpfs)
    arq = "Documento;Nome;Curso\n" + "\n".join(f"{c};{n};Gestão da TI" for c, n in zip(cpfs, _NOMES)) + "\n"
    # sem coluna de matrícula de verdade: o app não adivinha (antes, o CPF só-dígitos era a "melhor" coluna)
    r = adm.arquivo(arq.encode(), confirmar=True)
    assert cod(r) == 422 and "coluna da matrícula" in r[1] and adm.db.tables[MatriculasAutorizadas] == [], r
    prévia = adm.arquivo(arq.encode())
    assert cod(prévia) == 200 and prévia[1]["precisa_coluna"] is True and prévia[1]["importado"] is False
    # escolher a coluna do CPF à mão também é recusado
    r = adm.arquivo(arq.encode(), confirmar=True, col_matricula=0)
    assert cod(r) == 422 and "dados pessoais" in r[1] and adm.db.tables[MatriculasAutorizadas] == []
    # o mesmo arquivo em planilha (a célula vem como número)
    dados = xlsx([["Documento", "Nome"]] + [[int(c), n] for c, n in zip(cpfs, _NOMES)])
    r = adm.arquivo(dados, nome="a.xlsx", confirmar=True)
    assert cod(r) == 422 and adm.db.tables[MatriculasAutorizadas] == [], r


def test_ct191b_sem_cabecalho_com_cpf_antes_da_matricula_escolhe_a_matricula(adm):
    cpfs = _cpfs(6)
    arq = "\n".join(f"{c};2026{i:05d};{n}" for i, (c, n) in enumerate(zip(cpfs, _NOMES))) + "\n"
    r = adm.arquivo(arq.encode(), confirmar=True)
    assert cod(r) == 200 and r[1]["col_matricula"] == 1 and r[1]["adicionadas"] == 6, r
    guardadas = set(adm.mats())
    assert guardadas == {f"2026{i:05d}" for i in range(6)} and not guardadas & set(cpfs)


def test_ct192_matriculas_de_11_digitos_nao_sao_confundidas_com_cpf(adm):
    mats = [f"2026104{i:04d}" for i in range(50)]                                         # 11 dígitos, sequenciais
    assert not adm.ns["_coluna_parece_cpf"](mats)
    r = adm.arquivo(("Matrícula;Curso\n" + "\n".join(f"{m};Gestão da TI" for m in mats)).encode(), confirmar=True)
    assert cod(r) == 200 and r[1]["adicionadas"] == 50
    for c in ("111.111.111-11", "123", "abc", "12345678900", ""):
        assert not adm.ns["_cpf_valido"](c), c
    assert adm.ns["_cpf_valido"](_cpfs(1)[0]) and adm.ns["_cpf_valido"](
        f"{_cpfs(1)[0][:3]}.{_cpfs(1)[0][3:6]}.{_cpfs(1)[0][6:9]}-{_cpfs(1)[0][9:]}")


def test_ct193_importacao_por_texto_reconhece_curso_com_outra_grafia(adm):
    r = adm.texto("202610405;Gestao da TI\n202610406;GESTÃO  DA  T.I.")
    assert cod(r) == 200 and r[1]["adicionadas"] == 2 and r[1]["invalidas"] == 0, r
    assert {m.curso_id for m in adm.mats().values()} == {adm.curso.id}


def test_ct193b_curso_com_pontuacao_diferente_conta_como_duplicado(adm):
    for nome in ("Gestao da T.I.", "GESTÃO-DA-TI", "  gestão   da   ti "):
        r = adm.rota("adm_curso_novo", adm.corpo("CursoIn", nome=nome))
        assert cod(r) == 409 and adm.curso.nome in r[1], (nome, r)
    assert cod(adm.rota("adm_curso_novo", adm.corpo("CursoIn", nome="Gestão da Qualidade"))) == 200
