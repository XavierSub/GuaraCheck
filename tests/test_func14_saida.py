# -*- coding: utf-8 -*-
"""
QA - Funcionalidade 14: ENTRADA E SAÍDA COM PERMANÊNCIA MÍNIMA
Em evento com Aula.exige_saida o aluno registra a entrada e, no fim, a saída (mesmo celular, dentro do raio, chamada aberta).
A permanência é o tempo entre as duas. Com Aula.permanencia_min, sem saída ou com permanência menor o relatório mostra
"Presente (sem saída)" / "Permanência insuficiente" e o comprovante diz que o mínimo não foi cumprido.
Uso:  pytest tests/test_func14_saida.py -v
"""
from datetime import timedelta as _td, timezone as _tz

from harness_app import *  # noqa: F401,F403
from test_func4_presenca import IP_PUBLICO, dev, DEV
from test_func5_chamada_comprovantes import texto_pdf
from test_func6_exportacao_auditoria import CtxEx


@pytest.fixture
def sd():
    c = CtxEx()
    c.aula.exige_saida = True
    return c


def sair(c, aluno=None, aula=None, norte=0.0, **over):
    try:
        b = c.corpo(norte, 0.0, **over)
    except ValidationError as e:
        return 422, e
    return status_de(c.ns["registrar_saida"], (aula or c.aula).id, b, FakeReq(IP_PUBLICO), c.sessao(aluno), c.db)


def entrada_e_saida(c, minutos, aluno=None, **over):
    assert c.registrar(aluno=aluno, **over)[0] == 200
    c.avancar(minutos * 60)
    return sair(c, aluno, **over)


# --------------------------------------------------------------------------- o fluxo feliz
def test_ct1601_registra_a_saida_e_informa_a_permanencia(sd):
    assert sd.registrar()[0] == 200
    sd.avancar(45 * 60 + 20)
    st, r = sair(sd)
    assert st == 200 and r["ok"] is True and r["ja_saiu"] is False
    assert r["permanencia_min"] == 45 and r["permanencia_exigida"] is None and r["cumpriu"] is True and r["saida_em"]
    (p,) = sd.presencas()
    assert p.saida_em is not None and p.saida_distancia_m == pytest.approx(0.0, abs=0.5)


def test_ct1602_distancia_da_saida_fica_gravada(sd):
    sd.registrar()
    st, r = sair(sd, norte=20)
    assert st == 200 and 19 < r["distancia_m"] < 21 and 19 < sd.presencas()[0].saida_distancia_m < 21


def test_ct1603_sem_entrada_nao_ha_saida(sd):
    st, r = sair(sd)
    assert st == 409 and "entrada" in r and sd.presencas() == []


def test_ct1604_evento_que_nao_exige_saida(sd):
    sd.aula.exige_saida = False
    sd.registrar()
    st, r = sair(sd)
    assert st == 409 and "não pede" in r and sd.presencas()[0].saida_em is None


def test_ct1605_chamada_inexistente(sd):
    st, _ = status_de(sd.ns["registrar_saida"], 9999, sd.corpo(), FakeReq(IP_PUBLICO), sd.sessao(), sd.db)
    assert st == 404


def test_ct1606_repetir_devolve_o_que_ja_foi_gravado(sd):
    sd.registrar()
    sd.avancar(1800)
    primeira = sair(sd)[1]
    sd.avancar(900)
    st, r = sair(sd)
    assert st == 200 and r["ja_saiu"] is True and r["saida_em"] == primeira["saida_em"] and r["permanencia_min"] == 30


def test_ct1607_quem_ja_saiu_e_respondido_mesmo_com_a_chamada_encerrada(sd):
    sd.registrar()
    sair(sd)
    sd.aula.ativa = False
    st, r = sair(sd)
    assert st == 200 and r["ja_saiu"] is True


def test_ct1608_chamada_encerrada_sem_saida_registrada(sd):
    sd.registrar()
    sd.aula.ativa = False
    st, r = sair(sd)
    assert st == 409 and "encerrada" in r and sd.presencas()[0].saida_em is None


def test_ct1609_a_entrada_depois_da_saida_continua_idempotente(sd):
    sd.registrar()
    sair(sd)
    st, d = sd.registrar()
    assert st == 200 and d["ja_registrado"] is True and sd.presencas()[0].saida_em is not None


# --------------------------------------------------------------------------- presença lançada pelo professor
def test_ct1610_presenca_manual_nao_tem_saida_pelo_aluno(sd):
    b = sd.ns["PresencaManualIn"](aluno_id=sd.aluno.id, justificativa="Celular sem bateria")
    assert status_de(sd.ns["lancar_presenca_manual"], sd.aula.id, b, FakeReq(IP_PUBLICO), sd.eu(), sd.db)[0] == 200
    st, r = sair(sd)
    assert st == 409 and "professor" in r and sd.presencas()[0].saida_em is None


# --------------------------------------------------------------------------- antifraude da saída
def test_ct1611_so_pelo_mesmo_celular_da_entrada(sd):
    sd.registrar()
    st, r = sair(sd, device_id=dev(77))
    assert st == 403 and "mesmo celular" in r and sd.presencas()[0].saida_em is None
    assert sair(sd, device_id=DEV)[0] == 200


def test_ct1612_fora_do_raio_nao_registra_e_vira_tentativa(sd):
    sd.registrar()
    st, r = sair(sd, norte=500)
    assert st == 400 and "limite" in r and sd.presencas()[0].saida_em is None
    (t,) = sd.tentativas("fora_raio")
    assert t.detalhe.startswith("Saída a ")


def test_ct1613_gps_impreciso_ou_leitura_velha(sd):
    sd.registrar()
    assert sair(sd, precisao_m=500.0)[0] == 422
    assert sair(sd, pos_ts=sd.agora_ms() - 10 * 60 * 1000)[0] == 422
    assert sd.presencas()[0].saida_em is None


def test_ct1614_localizacao_falsa_e_recusada(sd):
    sd.registrar()
    st, r = sair(sd, webdriver=True, amostras=1, variacao_m=0.0)
    assert st == 403 and "falsificada" in r and sd.presencas()[0].saida_em is None
    (t,) = sd.tentativas("gps_suspeito")
    assert t.detalhe.startswith("Saída: ")


def test_ct1615_recusa_nao_grava_saida_nem_de_leve(sd):
    sd.registrar()
    sair(sd, norte=500)
    sair(sd, device_id=dev(9))
    assert sd.presencas()[0].saida_em is None and sd.presencas()[0].saida_distancia_m is None


def test_ct1616_corrida_duas_saidas_no_mesmo_instante_gravam_uma_so(sd):
    sd.registrar()
    p = sd.presencas()[0]
    outra_hora = sd.agora
    original = sd.db.execute

    def execute(q):
        if type(q).__name__ == "UpdateStmt" and p.saida_em is None:    # a outra requisição grava antes desta
            p.saida_em, p.saida_distancia_m = outra_hora, 1.0
        return original(q)
    sd.db.execute = execute
    st, r = sair(sd)
    assert st == 200 and r["ja_saiu"] is True and p.saida_em == outra_hora and p.saida_distancia_m == 1.0


# --------------------------------------------------------------------------- permanência mínima
def test_ct1617_saida_antes_do_minimo_nao_cumpre(sd):
    sd.aula.permanencia_min = 60
    st, r = entrada_e_saida(sd, 30)
    assert st == 200 and r["permanencia_min"] == 30 and r["permanencia_exigida"] == 60 and r["cumpriu"] is False


def test_ct1618_saida_no_minimo_ou_depois_cumpre(sd):
    sd.aula.permanencia_min = 60
    assert entrada_e_saida(sd, 60)[1]["cumpriu"] is True
    al = sd.novo_aluno(2)
    assert entrada_e_saida(sd, 95, aluno=al, device_id=dev(2))[1]["cumpriu"] is True


def test_ct1619_sem_minimo_qualquer_saida_cumpre(sd):
    assert entrada_e_saida(sd, 1)[1]["cumpriu"] is True


@pytest.mark.parametrize("exige,minimo,saida,perm,esperado", [
    (False, 60, None, None, "Presente"), (False, None, None, None, "Presente"),
    (True, None, None, None, "Presente (sem saída)"), (True, 60, None, None, "Presente (sem saída)"),
    (True, 60, "x", 59, "Permanência insuficiente"), (True, 60, "x", 60, "Presente"), (True, 60, "x", 0, "Permanência insuficiente"),
    (True, None, "x", 0, "Presente"), (True, 0, "x", 5, "Presente"),
])
def test_ct1620_situacao_da_presenca(sd, exige, minimo, saida, perm, esperado):
    assert sd.ns["_status_presenca"](exige, minimo, saida, perm) == esperado


@pytest.mark.parametrize("minutos,texto", [(0, "0 min"), (5, "5 min"), (59, "59 min"), (60, "1 h 00 min"), (65, "1 h 05 min"), (135, "2 h 15 min"), (-3, "0 min")])
def test_ct1621_duracao_em_texto(sd, minutos, texto):
    assert sd.ns["_fmt_duracao"](minutos) == texto


# --------------------------------------------------------------------------- o que o professor vê
def test_ct1622_lista_do_professor_traz_saida_permanencia_e_situacao(sd):
    sd.aula.permanencia_min = 60
    a2, a3 = sd.novo_aluno(2), sd.novo_aluno(3)
    entrada_e_saida(sd, 30)
    entrada_e_saida(sd, 70, aluno=a2, device_id=dev(2))
    sd.registrar(aluno=a3, device_id=dev(3))
    l1, l2, l3 = sd.lista()[1]["presencas"]
    assert (l1["permanencia_min"], l1["situacao"]) == (30, "Permanência insuficiente")
    assert (l2["permanencia_min"], l2["situacao"]) == (70, "Presente") and l2["saida_em"]
    assert (l3["saida_em"], l3["permanencia_min"], l3["situacao"]) == (None, None, "Presente (sem saída)")


def test_ct1623_evento_sem_saida_nunca_mostra_pendencia(sd):
    sd.aula.exige_saida = False
    sd.registrar()
    assert sd.lista()[1]["presencas"][0]["situacao"] == "Presente"


def test_ct1624_csv_com_saida_permanencia_e_situacao(sd):
    sd.aula.permanencia_min = 60
    a2, a3 = sd.novo_aluno(2), sd.novo_aluno(3)
    entrada_e_saida(sd, 30)
    entrada_e_saida(sd, 70, aluno=a2, device_id=dev(2))
    sd.registrar(aluno=a3, device_id=dev(3))
    por_nome = {l[1]: l for l in sd.tabela()}
    assert por_nome["Aluno 1"][5] == "Permanência insuficiente" and por_nome["Aluno 1"][11] == "30" and por_nome["Aluno 1"][10]
    assert por_nome["Aluno 2"][5] == "Presente" and por_nome["Aluno 2"][11] == "70"
    assert por_nome["Aluno 3"][5] == "Presente (sem saída)" and por_nome["Aluno 3"][10] == "" and por_nome["Aluno 3"][11] == ""


def test_ct1625_csv_mostra_a_saida_no_horario_de_brasilia(sd):
    sd.registrar()
    sd.avancar(3600)
    sair(sd)
    p = sd.presencas()[0]
    linha = sd.tabela()[0]
    esperado = p.saida_em.replace(tzinfo=_tz.utc).astimezone(_tz(_td(hours=-3))).strftime("%d/%m/%Y %H:%M:%S")
    assert linha[10] == esperado and linha[11] == "60"


# --------------------------------------------------------------------------- o que o aluno vê
def test_ct1626_lista_ativa_diz_se_o_aluno_ja_entrou_e_saiu(sd):
    def minha():
        st, r = status_de(sd.ns["aulas_ativas"], sd.sessao(), sd.db)
        assert st == 200
        return r[0]
    assert (minha()["exige_saida"], minha()["ja_registrou"], minha()["ja_saiu"]) == (True, False, False)
    sd.registrar()
    a = minha()
    assert (a["ja_registrou"], a["ja_saiu"]) == (True, False) and a["entrada_em"]
    sair(sd)
    assert minha()["ja_saiu"] is True


def test_ct1627_comprovantes_do_aluno_trazem_a_saida_e_a_situacao(sd):
    sd.aula.permanencia_min = 60
    entrada_e_saida(sd, 30)
    (m,) = sd.meus()[1]
    assert m["exige_saida"] is True and m["saida_em"] and m["permanencia_min"] == 30
    assert m["permanencia_exigida"] == 60 and m["situacao"] == "Permanência insuficiente"


def test_ct1628_pdf_com_saida_cumprida(sd):
    sd.aula.permanencia_min = 60
    entrada_e_saida(sd, 65)
    t = texto_pdf(sd.pdf(sd.presencas()[0].id)[1].body).replace("\n", " ")
    assert "Saída registrada em" in t and "permanência de 1 h 05 min" in t and "mínimo do evento: 1 h 00 min" in t
    assert "abaixo do mínimo" not in t


def test_ct1629_pdf_com_permanencia_insuficiente(sd):
    sd.aula.permanencia_min = 60
    entrada_e_saida(sd, 20)
    t = texto_pdf(sd.pdf(sd.presencas()[0].id)[1].body).replace("\n", " ")
    assert "permanência de 20 min" in t and "abaixo do mínimo do evento" in t


def test_ct1630_pdf_sem_saida_registrada(sd):
    sd.registrar()
    t = texto_pdf(sd.pdf(sd.presencas()[0].id)[1].body).replace("\n", " ")
    assert "exige registro de saída e a saída não foi registrada" in t


def test_ct1631_pdf_de_evento_sem_saida_nao_fala_de_saida(sd):
    sd.aula.exige_saida = False
    sd.registrar()
    t = texto_pdf(sd.pdf(sd.presencas()[0].id)[1].body).replace("\n", " ")
    assert "Saída" not in t and "saída" not in t.replace("saída.", "")


# --------------------------------------------------------------------------- abrir o evento com saída
def corpo_aula(**over):
    d = dict(titulo="Palestra", latitude=-18.4, longitude=-49.2, raio_metros=60)
    d.update(over)
    return d


def abrir(c, **over):
    c.aula.ativa = False
    try:
        b = c.ns["AulaIn"](**corpo_aula(**over))
    except ValidationError as e:
        return 422, e
    return status_de(c.ns["abrir_aula"], b, c.eu(), c.db)


def test_ct1632_abre_com_saida_minimo_e_carga_horaria(sd):
    st, r = abrir(sd, exige_saida=True, permanencia_min=90, carga_horas=2.5)
    assert st == 200 and (r["exige_saida"], r["permanencia_min"], r["carga_horas"]) == (True, 90, 2.5)


def test_ct1633_minimo_sem_exigir_saida_e_recusado(sd):
    st, r = abrir(sd, permanencia_min=90)
    assert st == 422 and "saída" in str(r)


@pytest.mark.parametrize("campo,valor", [("permanencia_min", 0), ("permanencia_min", 1441), ("carga_horas", 0), ("carga_horas", -1),
                                          ("carga_horas", 201), ("carga_horas", float("nan"))])
def test_ct1634_limites_do_esquema(sd, campo, valor):
    assert abrir(sd, exige_saida=True, **{campo: valor})[0] == 422


def test_ct1635_sem_saida_o_minimo_nao_e_guardado(sd):
    st, r = abrir(sd, exige_saida=False, carga_horas=1)
    assert st == 200 and r["permanencia_min"] is None and r["carga_horas"] == 1


# --------------------------------------------------------------------------- só o aluno usa a rota
def test_ct1636_professor_e_coordenacao_nao_registram_saida(sd):
    for papel in ("professor", "admin"):
        assert status_de(sd.ns["so_aluno"], {"id": 1, "role": papel, "nome": "X"})[0] == 403
