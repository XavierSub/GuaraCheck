# -*- coding: utf-8 -*-
"""
QA - Funcionalidade 13: PRESENÇA LANÇADA PELO PROFESSOR (com justificativa)
O professor dono da chamada lança a presença de quem não conseguiu registrar (celular sem bateria, GPS ruim). A justificativa é
obrigatória, a presença fica marcada (lista, CSV e PDF do aluno), a ação vai para a auditoria e só a presença LANÇADA pelo professor
pode ser desfeita - a que o aluno registrou é dele. Vale com a chamada já encerrada (correção posterior).
Uso:  pytest tests/test_func13_presenca_manual.py -v
"""
from harness_app import *  # noqa: F401,F403
from test_func4_presenca import dev
from test_func5_chamada_comprovantes import texto_pdf
from test_func6_exportacao_auditoria import CtxEx

JUST = "Celular sem bateria"


@pytest.fixture
def pm():
    return CtxEx()


def lancar(c, aula=None, p=None, **kw):
    corpo = dict(justificativa=JUST)
    corpo.update(kw)
    try:
        b = c.ns["PresencaManualIn"](**corpo)
    except ValidationError as e:
        return 422, e
    return status_de(c.ns["lancar_presenca_manual"], (aula or c.aula).id, b, FakeReq("177.9.9.9"), c.eu(p), c.db)


def desfazer(c, presenca_id, aula=None, p=None):
    return status_de(c.ns["desfazer_presenca_manual"], (aula or c.aula).id, presenca_id, FakeReq("177.9.9.9"), c.eu(p), c.db)


# --------------------------------------------------------------------------- quem pode
def test_ct1501_professor_dono_lanca_pelo_id_do_aluno(pm):
    al = pm.novo_aluno(2)
    st, r = lancar(pm, aluno_id=al.id)
    assert st == 200 and r == {"ok": True, "aluno": "Aluno 2", "matricula": al.matricula}
    (p,) = pm.presencas()
    assert p.aluno_id == al.id and p.manual is True and p.lancada_por == pm.prof.id and p.justificativa == JUST
    assert p.distancia_m == 0.0 and p.alertas == "lançada pelo professor" and p.saida_em is None


def test_ct1502_lanca_pela_matricula_com_espacos_e_minusculas(pm):
    al = pm.novo_aluno(3)
    st, r = lancar(pm, matricula=f"  {al.matricula.lower()} ")
    assert st == 200 and r["aluno"] == "Aluno 3"
    assert pm.presencas()[0].aluno_id == al.id


def test_ct1503_outro_professor_recebe_404_e_nada_e_gravado(pm):
    st, _ = lancar(pm, p=pm.ana, aluno_id=pm.aluno.id)
    assert st == 404 and pm.presencas() == []


@pytest.mark.parametrize("papel", ["aluno", "admin"])
def test_ct1504_so_professor(pm, papel):
    assert status_de(pm.ns["so_professor"], {"id": 1, "role": papel, "nome": "X"})[0] == 403


def test_ct1505_chamada_inexistente(pm):
    st, _ = status_de(pm.ns["lancar_presenca_manual"], 9999, pm.ns["PresencaManualIn"](aluno_id=pm.aluno.id, justificativa=JUST),
                      FakeReq("177.9.9.9"), pm.eu(), pm.db)
    assert st == 404


# --------------------------------------------------------------------------- validações
@pytest.mark.parametrize("just", ["", "    ", "abc", " a b ", "x" * 3])
def test_ct1506_justificativa_curta_ou_vazia_e_recusada(pm, just):
    st, r = lancar(pm, aluno_id=pm.aluno.id, justificativa=just)
    assert st == 422 and "motivo" in r
    assert pm.presencas() == []


def test_ct1507_justificativa_muito_longa_e_recusada_pelo_esquema(pm):
    assert lancar(pm, aluno_id=pm.aluno.id, justificativa="x" * 401)[0] == 422


def test_ct1508_justificativa_e_normalizada_e_cortada_em_200(pm):
    lancar(pm, aluno_id=pm.aluno.id, justificativa="  Celular \n\n  sem   bateria  ")
    assert pm.presencas()[0].justificativa == "Celular sem bateria"
    al = pm.novo_aluno(2)
    lancar(pm, aluno_id=al.id, justificativa="y" * 400)
    assert len(pm.presencas()[1].justificativa) == 200


def test_ct1509_sem_aluno_nem_matricula(pm):
    st, r = lancar(pm)
    assert st == 422 and "matrícula" in r
    st, r = lancar(pm, matricula="   ")
    assert st == 422


def test_ct1510_aluno_inexistente_404(pm):
    assert lancar(pm, aluno_id=99999)[0] == 404
    assert lancar(pm, matricula="NAOEXISTE1")[0] == 404
    assert pm.presencas() == []


@pytest.mark.parametrize("ruim", [0, -1, 2_147_483_648])
def test_ct1511_aluno_id_fora_do_limite_e_recusado_pelo_esquema(pm, ruim):
    assert lancar(pm, aluno_id=ruim)[0] == 422


def test_ct1512_aluno_inativo_409(pm):
    pm.aluno.status = "inativo"
    st, r = lancar(pm, aluno_id=pm.aluno.id)
    assert st == 409 and "inativa" in r and pm.presencas() == []


def test_ct1513_quem_ja_registrou_nao_duplica(pm):
    assert pm.registrar()[0] == 200
    st, r = lancar(pm, aluno_id=pm.aluno.id)
    assert st == 409 and "já tem presença" in r
    assert len(pm.presencas()) == 1 and pm.presencas()[0].manual in (None, False)


def test_ct1514_lancar_duas_vezes_da_409_na_segunda(pm):
    assert lancar(pm, aluno_id=pm.aluno.id)[0] == 200
    assert lancar(pm, aluno_id=pm.aluno.id)[0] == 409
    assert len(pm.presencas()) == 1


def test_ct1515_aluno_que_ja_lancado_nao_consegue_registrar_de_novo_mas_e_idempotente(pm):
    lancar(pm, aluno_id=pm.aluno.id)
    st, d = pm.registrar()
    assert st == 200 and d["ja_registrado"] is True and len(pm.presencas()) == 1


# --------------------------------------------------------------------------- chamada encerrada, saída, auditoria
def test_ct1516_vale_com_a_chamada_encerrada_para_corrigir_depois(pm):
    pm.aula.ativa = False
    assert lancar(pm, aluno_id=pm.aluno.id)[0] == 200


def test_ct1517_com_saida_so_vale_em_evento_que_exige_saida(pm):
    al2 = pm.novo_aluno(2)
    lancar(pm, aluno_id=pm.aluno.id, com_saida=True)
    assert pm.presencas()[0].saida_em is None, "evento sem saída: a opção é ignorada"
    pm.aula.exige_saida = True
    lancar(pm, aluno_id=al2.id, com_saida=True)
    assert pm.presencas()[1].saida_em is not None
    al3 = pm.novo_aluno(3)
    lancar(pm, aluno_id=al3.id)
    assert pm.presencas()[2].saida_em is None


def test_ct1518_auditoria_registra_quem_lancou_quem_e_o_motivo(pm):
    lancar(pm, aluno_id=pm.aluno.id)
    (log,) = pm.logs("presenca_manual")
    assert log.usuario_id == pm.prof.id and log.ip == "177.9.9.9"
    assert f"aula={pm.aula.id}" in log.detalhe and f"aluno={pm.aluno.id}" in log.detalhe and JUST in log.detalhe


def test_ct1519_recusa_nao_deixa_rastro_de_auditoria(pm):
    lancar(pm, aluno_id=99999)
    lancar(pm, aluno_id=pm.aluno.id, justificativa="a")
    assert pm.logs("presenca_manual") == []


# --------------------------------------------------------------------------- desfazer
def test_ct1520_desfaz_a_presenca_lancada_pelo_professor(pm):
    lancar(pm, aluno_id=pm.aluno.id)
    p = pm.presencas()[0]
    st, r = desfazer(pm, p.id)
    assert st == 200 and r == {"ok": True} and pm.presencas() == []
    (log,) = pm.logs("desfazer_presenca_manual")
    assert f"presenca={p.id}" in log.detalhe and f"aluno={pm.aluno.id}" in log.detalhe


def test_ct1521_nao_desfaz_presenca_que_o_aluno_registrou(pm):
    assert pm.registrar()[0] == 200
    p = pm.presencas()[0]
    st, r = desfazer(pm, p.id)
    assert st == 409 and "aluno registrou" in r
    assert len(pm.presencas()) == 1 and pm.logs("desfazer_presenca_manual") == []


def test_ct1522_desfazer_de_outra_chamada_ou_inexistente_404(pm):
    outra = pm.nova_aula(pm.ana)
    lancar(pm, aula=outra, p=pm.ana, aluno_id=pm.aluno.id)
    p = pm.presencas(outra)[0]
    assert desfazer(pm, p.id)[0] == 404, "o id existe, mas é de outra chamada"
    assert desfazer(pm, 99999)[0] == 404
    assert len(pm.presencas()) == 1


def test_ct1523_outro_professor_nao_desfaz(pm):
    lancar(pm, aluno_id=pm.aluno.id)
    p = pm.presencas()[0]
    assert desfazer(pm, p.id, p=pm.ana)[0] == 404 and len(pm.presencas()) == 1


def test_ct1524_depois_de_desfazer_o_aluno_volta_para_os_ausentes_e_pode_registrar(pm):
    pm.curso_na_aula(None)
    lancar(pm, aluno_id=pm.aluno.id)
    assert pm.aus()[1]["total"] == 0
    desfazer(pm, pm.presencas()[0].id)
    assert [i["id"] for i in pm.aus()[1]["itens"]] == [pm.aluno.id]
    assert pm.registrar()[0] == 200


# --------------------------------------------------------------------------- o que o professor e o aluno enxergam
def test_ct1525_lista_de_presentes_marca_a_manual(pm):
    al = pm.novo_aluno(2)
    pm.registrar()
    lancar(pm, aluno_id=al.id)
    a, b = pm.lista()[1]["presencas"]
    assert (a["manual"], a["justificativa"]) == (False, None)
    assert (b["manual"], b["justificativa"]) == (True, JUST)
    assert b["saida_em"] is None and b["permanencia_min"] is None


def test_ct1526_lista_de_ausentes_traz_o_id_para_o_botao_lancar(pm):
    pm.curso_na_aula(None)
    r = pm.aus()[1]
    assert r["disponivel"] is True and r["itens"][0]["id"] == pm.aluno.id
    st, _ = lancar(pm, aluno_id=r["itens"][0]["id"])
    assert st == 200


def test_ct1527_csv_traz_origem_e_justificativa(pm):
    al = pm.novo_aluno(2)
    pm.registrar()
    lancar(pm, aluno_id=al.id, justificativa="=cmd|' /C calc'!A0")
    linhas = pm.tabela()
    por_nome = {l[1]: l for l in linhas}
    assert por_nome["Aluno 1"][8:] == ["Aluno", "", "", ""]
    assert por_nome["Aluno 2"][5] == "Presente" and por_nome["Aluno 2"][8] == "Lançada pelo professor"
    assert por_nome["Aluno 2"][9].startswith("'="), "justificativa não vira fórmula no Excel"
    assert por_nome["Aluno 2"][7] == "0,0"


def test_ct1528_comprovante_do_aluno_mostra_que_foi_lancado_pelo_professor(pm):
    lancar(pm, aluno_id=pm.aluno.id)
    (m,) = pm.meus()[1]
    assert m["manual"] is True and m["exige_saida"] is False and m["saida_em"] is None
    st, resp = pm.pdf(pm.presencas()[0].id)
    assert st == 200 and resp.body[:5] == b"%PDF-"
    t = texto_pdf(resp.body).replace("\n", " ")
    assert "LANÇADA PELO PROFESSOR" in t and "lançada manualmente" in t and JUST in t
    assert "registrei presença" not in t, "o texto não pode dizer que o aluno registrou"


def test_ct1529_comprovante_normal_continua_igual(pm):
    pm.registrar()
    (m,) = pm.meus()[1]
    assert m["manual"] is False
    t = texto_pdf(pm.pdf(pm.presencas()[0].id)[1].body).replace("\n", " ")
    assert "PRESENÇA REGISTRADA" in t and "registrei presença" in t and "lançada manualmente" not in t


def test_ct1530_assinatura_do_comprovante_manual_confere_no_painel(pm):
    lancar(pm, aluno_id=pm.aluno.id)
    p = pm.presencas()[0]
    r = pm.conferir(pm.autenticacao(p))
    assert r[0] == 200 and r[1]["valido"] is True and r[1]["nome"] == "Aluno 1"


def test_ct1531_aluno_que_nao_registrou_continua_sem_comprovante(pm):
    pm.novo_aluno(2)
    assert pm.meus()[1] == []
