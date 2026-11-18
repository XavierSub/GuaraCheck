# -*- coding: utf-8 -*-
"""
QA - Funcionalidade 12: QR CODE ROTATIVO
Opção por evento (Aula.qr_rotativo). O link do QR leva um código (?k=) calculado pelo servidor com a chave secreta, o número da chamada e
a janela de tempo (QR_JANELA_S, padrão 30 s). O check-in só vale com o código da janela atual ou das QR_JANELAS_ACEITAS - 1 anteriores
(padrão 4 janelas = até 2 min). O GPS e o antifraude continuam valendo por cima. Eventos sem a opção não mudam em nada.
Uso:  pytest tests/test_func12_qr_rotativo.py -v
"""
import types as _types
import urllib.parse as _up

from harness_app import *  # noqa: F401,F403
from test_func4_presenca import CtxPr, dev

HOST = "guaracheck.onrender.com"


def req():
    return _types.SimpleNamespace(headers={"host": HOST, "x-forwarded-proto": "https"},
                                  url=_types.SimpleNamespace(scheme="http", netloc=HOST))


@pytest.fixture
def rt():
    c = CtxPr()
    c.aula.ativa = False                      # cada professor só pode ter UMA chamada ativa
    c.aula = c.nova_aula(qr_rotativo=True)
    return c


def k_do_link(url):
    return _up.parse_qs(_up.urlsplit(url).query).get("k", [None])[0]


def janela(c):
    return int(c.ns["time"].time() // c.ns["QR_JANELA_S"])


# --------------------------------------------------------------------------- o código
def test_ct1401_codigo_e_deterministico_curto_e_so_do_servidor(rt):
    ns = rt.ns
    a = ns["qr_token"](7, 100)
    assert a == ns["qr_token"](7, 100) and len(a) == 12 and all(ch in "0123456789abcdef" for ch in a)
    assert a != ns["qr_token"](7, 101), "muda a cada janela"
    assert a != ns["qr_token"](8, 100), "cada chamada tem o seu"
    outro = CtxPr(SECRET_KEY="outra-chave-secreta-com-mais-de-32-caracteres-ok")
    assert outro.ns["qr_token"](7, 100) != a, "sem a chave do servidor não dá para calcular"


def test_ct1402_aceita_a_janela_atual_e_as_anteriores_ate_o_limite(rt):
    ns, n = rt.ns, rt.ns["QR_JANELAS_ACEITAS"]
    assert (ns["QR_JANELA_S"], n) == (30, 4)
    j = janela(rt)
    for i in range(n):
        assert ns["qr_token_valido"](rt.aula.id, ns["qr_token"](rt.aula.id, j - i)), f"janela -{i} deveria valer"
    assert not ns["qr_token_valido"](rt.aula.id, ns["qr_token"](rt.aula.id, j - n)), "uma janela além do limite já venceu"
    assert not ns["qr_token_valido"](rt.aula.id, ns["qr_token"](rt.aula.id, j + 1)), "código do futuro não vale"


@pytest.mark.parametrize("lixo", [None, "", "abc", "x" * 12, "0" * 12, "ABCDEF123456", 123456789012, "a" * 13, "a" * 64])
def test_ct1403_codigo_invalido_nunca_vale(rt, lixo):
    assert rt.ns["qr_token_valido"](rt.aula.id, lixo) is False


def test_ct1404_o_codigo_vale_so_para_a_chamada_dele(rt):
    outra = rt.nova_aula(rt.ana, qr_rotativo=True)
    k = rt.ns["qr_token"](outra.id, janela(rt))
    assert rt.ns["qr_token_valido"](outra.id, k) and not rt.ns["qr_token_valido"](rt.aula.id, k)


def test_ct1405_o_tempo_anda_e_o_codigo_vence(rt):
    k = rt.ns["qr_token"](rt.aula.id, janela(rt))
    assert rt.ns["qr_token_valido"](rt.aula.id, k)
    rt.avancar(90)
    assert rt.ns["qr_token_valido"](rt.aula.id, k), "ainda dentro das 4 janelas"
    rt.avancar(60)
    assert not rt.ns["qr_token_valido"](rt.aula.id, k), "passou de 2 minutos"


@pytest.mark.parametrize("valor,esperado", [("5", 10), ("9999", 300), ("45", 45), ("abc", 30), ("", 30)])
def test_ct1406_janela_configuravel_com_limites(valor, esperado):
    assert CtxPr(QR_JANELA_S=valor).ns["QR_JANELA_S"] == esperado


@pytest.mark.parametrize("valor,esperado", [("0", 1), ("99", 20), ("6", 6), ("x", 4)])
def test_ct1407_janelas_aceitas_configuravel_com_limites(valor, esperado):
    assert CtxPr(QR_JANELAS_ACEITAS=valor).ns["QR_JANELAS_ACEITAS"] == esperado


# --------------------------------------------------------------------------- a rota do QR
def test_ct1408_qr_rotativo_leva_o_codigo_no_link_e_diz_quando_renovar(rt):
    st, r = status_de(rt.ns["qr_da_aula"], rt.aula.id, req(), {"id": rt.prof.id, "role": "professor"}, rt.db)
    assert st == 200 and r["rotativo"] is True
    assert r["url"].startswith(f"https://{HOST}/?aula={rt.aula.id}&k=")
    assert rt.ns["qr_token_valido"](rt.aula.id, k_do_link(r["url"]))
    assert 1 <= r["renova_em_s"] <= 30


def test_ct1409_renova_em_acompanha_o_relogio(rt):
    rt.ns["time"].t = 3000.0                      # janela de 30 s: 3000 // 30 = 100 exato -> faltam 30 s
    assert rt.ns["qr_renova_em_s"]() == 30
    rt.ns["time"].t = 3012.5
    assert rt.ns["qr_renova_em_s"]() == 17
    rt.ns["time"].t = 3029.9
    assert rt.ns["qr_renova_em_s"]() == 1


def test_ct1410_o_link_do_qr_muda_de_uma_janela_para_a_outra(rt):
    eu = {"id": rt.prof.id, "role": "professor"}
    k1 = k_do_link(status_de(rt.ns["qr_da_aula"], rt.aula.id, req(), eu, rt.db)[1]["url"])
    rt.avancar(31)
    k2 = k_do_link(status_de(rt.ns["qr_da_aula"], rt.aula.id, req(), eu, rt.db)[1]["url"])
    assert k1 and k2 and k1 != k2


def test_ct1411_qr_fixo_continua_sem_codigo(rt):
    fixa = rt.nova_aula(rt.ana)
    st, r = status_de(rt.ns["qr_da_aula"], fixa.id, req(), {"id": rt.ana.id, "role": "professor"}, rt.db)
    assert st == 200 and r["rotativo"] is False and r["renova_em_s"] is None and "&k=" not in r["url"]


# --------------------------------------------------------------------------- o check-in
def test_ct1412_sem_codigo_nao_registra(rt):
    st, d = rt.registrar()
    assert st == 403 and "QR code do telão" in d
    assert rt.presencas() == [] and rt.tentativas() == [], "esquecer o QR não é fraude: nada vai para as tentativas"


def test_ct1413_codigo_errado_ou_de_outra_chamada_nao_registra(rt):
    outra = rt.nova_aula(rt.ana, qr_rotativo=True)
    for k in ("0" * 12, "lixo", rt.ns["qr_token"](outra.id, janela(rt))):
        assert rt.registrar(qr=k)[0] == 403, k
    assert rt.presencas() == []


def test_ct1414_codigo_certo_registra(rt):
    k = rt.ns["qr_token"](rt.aula.id, janela(rt))
    st, d = rt.registrar(qr=k)
    assert st == 200 and d["ok"] is True and d["ja_registrado"] is False
    assert len(rt.presencas()) == 1


def test_ct1415_codigo_da_ultima_janela_aceita_ainda_registra_e_o_vencido_nao(rt):
    k = rt.ns["qr_token"](rt.aula.id, janela(rt))
    rt.avancar(100)                                   # 3 janelas depois: ainda vale
    assert rt.registrar(qr=k)[0] == 200
    outro = rt.novo_aluno(2)
    rt.avancar(60)                                    # agora passou
    st, d = rt.registrar(aluno=outro, qr=k, device_id=dev(2))
    assert st == 403 and "QR code do telão" in d


def test_ct1416_o_link_gerado_pela_rota_serve_para_registrar(rt):
    r = status_de(rt.ns["qr_da_aula"], rt.aula.id, req(), {"id": rt.prof.id, "role": "professor"}, rt.db)[1]
    assert rt.registrar(qr=k_do_link(r["url"]))[0] == 200


def test_ct1417_quem_ja_registrou_e_respondido_sem_pedir_o_codigo_de_novo(rt):
    k = rt.ns["qr_token"](rt.aula.id, janela(rt))
    assert rt.registrar(qr=k)[0] == 200
    rt.avancar(600)
    st, d = rt.registrar()                            # duplo toque / aba antiga, sem código
    assert st == 200 and d["ja_registrado"] is True and len(rt.presencas()) == 1


def test_ct1418_com_codigo_certo_o_gps_continua_valendo(rt):
    k = rt.ns["qr_token"](rt.aula.id, janela(rt))
    st, d = rt.registrar(qr=k, norte=500)             # a 500 m da sala (raio de 50 m)
    assert st == 400 and "limite" in d
    assert rt.presencas() == [] and len(rt.tentativas("fora_raio")) == 1


def test_ct1419_evento_sem_a_opcao_ignora_o_codigo(rt):
    rt.aula.qr_rotativo = False
    assert rt.registrar(qr="qualquer-coisa")[0] == 200
    assert rt.registrar(aluno=rt.novo_aluno(3), device_id=dev(3))[0] == 200


def test_ct1420_codigo_muito_longo_e_recusado_pelo_esquema(rt):
    r = rt.registrar(qr="x" * 65)
    assert r[0] == 422


# --------------------------------------------------------------------------- abrir a chamada com a opção
def corpo_aula(**over):
    d = dict(titulo="Palestra", latitude=-18.4, longitude=-49.2, raio_metros=60)
    d.update(over)
    return d


def test_ct1421_professor_abre_o_evento_com_qr_rotativo(rt):
    rt.aula.ativa = False
    eu = {"id": rt.prof.id, "role": "professor"}
    st, r = status_de(rt.ns["abrir_aula"], rt.ns["AulaIn"](**corpo_aula(qr_rotativo=True)), FakeReq(), FakeTarefas(), eu, rt.db)
    assert st == 200 and r["qr_rotativo"] is True and r["exige_saida"] is False
    assert rt.db.get(Aula, r["id"]).qr_rotativo is True


def test_ct1422_por_padrao_o_evento_nao_e_rotativo(rt):
    rt.aula.ativa = False
    st, r = status_de(rt.ns["abrir_aula"], rt.ns["AulaIn"](**corpo_aula()), FakeReq(), FakeTarefas(), {"id": rt.prof.id, "role": "professor"}, rt.db)
    assert st == 200 and r["qr_rotativo"] is False and r["permanencia_min"] is None and r["carga_horas"] is None
