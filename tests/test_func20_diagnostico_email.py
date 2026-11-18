# -*- coding: utf-8 -*-
"""
QA - Funcionalidade 20: DIAGNÓSTICO DO E-MAIL (por que o e-mail não chega)
Antes, quando o Brevo recusava os envios o professor via "aviso em envio" e nada chegava, e o log só dizia "HTTPError". Agora:
  * _motivo_falha_email traduz a recusa do provedor numa frase acionável (chave, IP bloqueado, remetente não verificado, cota, rede),
    sem endereço de e-mail e sem chave;
  * o log do aviso guarda o motivo da PRIMEIRA falha (erro=...) e GET /api/aulas/{id}/aviso entrega o resultado ao painel do professor;
  * POST /api/admin/email-teste deixa a coordenação mandar um e-mail de teste e ler o motivo na hora.
Uso:  pytest tests/test_func20_diagnostico_email.py -v
"""
import io
import urllib.error

from harness_app import *  # noqa: F401,F403
from harness_app import FakeTarefas
from test_func17_aviso_email import CtxAv

ADMIN = {"id": 1, "role": "admin", "nome": "Coordenação"}


def http(codigo, corpo=None):
    dados = corpo if isinstance(corpo, (bytes, bytearray)) else json.dumps(corpo if corpo is not None else {}).encode()
    return urllib.error.HTTPError("https://api.brevo.com/v3/smtp/email", codigo, "x", {}, io.BytesIO(dados))


@pytest.fixture
def av():
    return CtxAv()


def motivo(av, exc):
    return av.ns["_motivo_falha_email"](exc)


# --------------------------------------------------------------------------- tradução do motivo
def test_ct2001_chave_recusada(av):
    m = motivo(av, http(401, {"code": "unauthorized", "message": "Key not found"}))
    assert "EMAIL_API_KEY" in m


def test_ct2002_ip_bloqueado_vence_a_chave(av):
    m = motivo(av, http(401, {"code": "unauthorized", "message": "We have detected you are using an unrecognised IP address 203.0.113.9."}))
    assert "IP" in m and "Authorised IPs" in m and "EMAIL_API_KEY" not in m
    assert "203.0.113.9" not in m


def test_ct2003_remetente_nao_verificado(av):
    m = motivo(av, http(400, {"code": "invalid_parameter", "message": "Sender guaracheck@unicerrado.edu.br is not valid or not verified"}))
    assert "EMAIL_REMETENTE" in m and "verificado" in m
    assert "@" not in m


def test_ct2004_outro_400_mostra_o_texto_do_provedor_sem_enderecos(av):
    m = motivo(av, http(400, {"message": "Invalid recipient joao@exemplo.com in field to"}))
    assert "HTTP 400" in m and "[e-mail]" in m and "joao@exemplo.com" not in m


@pytest.mark.parametrize("codigo", [402, 403, 429])
def test_ct2005_limite_cota_ou_permissao(av, codigo):
    assert f"HTTP {codigo}" in motivo(av, http(codigo, {"message": "Daily limit reached"}))


def test_ct2006_corpo_ilegivel_cai_no_codigo_http(av):
    assert motivo(av, http(500, b"<html>erro")) == "o Brevo recusou o envio (HTTP 500)"
    assert motivo(av, http(502, [1, 2, 3])) == "o Brevo recusou o envio (HTTP 502)"


def test_ct2007_texto_do_provedor_e_cortado(av):
    m = motivo(av, http(400, {"message": "x" * 5000}))
    assert len(m) < 220


def test_ct2008_sem_rede(av):
    for exc in (urllib.error.URLError("dns"), TimeoutError("t"), OSError("rede")):
        assert "não foi possível falar com o provedor" in motivo(av, exc)


def test_ct2009_erro_qualquer_mostra_so_o_tipo(av):
    m = motivo(av, ValueError("segredo-da-chave abc@d.com"))
    assert m == "erro ao enviar o e-mail (ValueError)" and "segredo" not in m and "@" not in m


def test_ct2010_smtp_senha_errada(av):
    class SMTPAuthenticationError(Exception):
        pass
    assert "SMTP_USUARIO" in motivo(av, SMTPAuthenticationError(535, b"x"))


# --------------------------------------------------------------------------- o log do aviso guarda o motivo
def recusa_remetente(para, assunto, texto):
    raise http(400, {"message": "Sender guaracheck@unicerrado.edu.br is not valid"})


def test_ct2011_log_traz_o_motivo_da_primeira_falha(av):
    av.alunos(10)
    st, r, t = av.abrir()
    av.ns["enviar_email"] = recusa_remetente
    t.rodar()
    (log,) = av.logs("aviso_email")
    assert "falhas=5" in log.detalhe and "parou=provedor recusando" in log.detalhe and "erro=o remetente (EMAIL_REMETENTE)" in log.detalhe
    assert "@" not in log.detalhe and len(log.detalhe) <= 300


def test_ct2012_guarda_a_primeira_falha_e_nao_a_ultima(av):
    av.alunos(10)
    st, r, t = av.abrir()
    chamadas = {"n": 0}

    def primeiro_chave_depois_remetente(para, assunto, texto):
        chamadas["n"] += 1
        raise http(401 if chamadas["n"] == 1 else 400, {"message": "Sender not valid" if chamadas["n"] > 1 else "Key not found"})
    av.ns["enviar_email"] = primeiro_chave_depois_remetente
    t.rodar()
    (log,) = av.logs("aviso_email")
    assert "erro=o Brevo recusou a chave" in log.detalhe


def test_ct2013_sem_falhas_o_log_continua_so_com_contagens(av):
    av.alunos(3)
    st, r, t = av.abrir()
    t.rodar()
    (log,) = av.logs("aviso_email")
    assert log.detalhe == f"aula={r['id']}; enviados=3; falhas=0" and "erro" not in log.detalhe


def test_ct2014_o_motivo_nao_quebra_o_formato_do_log(av):
    av.alunos(10)
    st, r, t = av.abrir()

    def com_ponto_e_virgula(para, assunto, texto):
        raise http(400, {"message": "campo a; campo b; campo c"})
    av.ns["enviar_email"] = com_ponto_e_virgula
    t.rodar()
    (log,) = av.logs("aviso_email")
    erro = log.detalhe.split("; erro=")[1]
    assert "; " not in erro


# --------------------------------------------------------------------------- resultado do aviso para o painel
def resultado(av, aula_id, p=None):
    return status_de(av.ns["resultado_do_aviso"], aula_id, av.eu(p), av.db)


def test_ct2021_enquanto_o_envio_roda_nao_ha_fim(av):
    av.alunos(3)
    st, r, t = av.abrir()
    assert resultado(av, r["id"]) == (200, {"fim": False})


def test_ct2022_depois_do_envio_traz_as_contagens(av):
    av.alunos(3)
    st, r, t = av.abrir()
    t.rodar()
    assert resultado(av, r["id"]) == (200, {"fim": True, "enviados": 3, "falhas": 0, "parou": None, "erro": None})


def test_ct2023_com_falha_traz_o_motivo(av):
    av.alunos(10)
    st, r, t = av.abrir()
    av.ns["enviar_email"] = recusa_remetente
    t.rodar()
    st, d = resultado(av, r["id"])
    assert st == 200 and d["fim"] is True and d["enviados"] == 0 and d["falhas"] == 5
    assert d["parou"] == "provedor recusando os envios" and "EMAIL_REMETENTE" in d["erro"]


def test_ct2024_aula_com_numero_parecido_nao_se_confunde(av):
    av.alunos(2)
    st, r, t = av.abrir(titulo="Primeira")
    t.rodar()
    # outra chamada do mesmo professor cujo número começa com os mesmos dígitos ("1" e "11"): o prefixo "aula=1;" não pode casar com "aula=11;"
    outra = av.nova_aula(id=int(f"{r['id']}1"), ativa=False)
    assert resultado(av, outra.id) == (200, {"fim": False})
    assert resultado(av, r["id"])[1]["fim"] is True


def test_ct2025_so_o_dono_da_chamada_ve_o_resultado(av):
    av.alunos(2)
    st, r, t = av.abrir()
    t.rodar()
    assert resultado(av, r["id"], p=av.outro())[0] == 404
    assert resultado(av, 99999)[0] == 404


def test_ct2026_aluno_e_visitante_nao_passam_no_guarda():
    import ast
    fonte = open(APP, encoding="utf-8").read()
    rota = next(n for n in ast.parse(fonte).body if isinstance(n, ast.FunctionDef) and n.name == "resultado_do_aviso")
    assert "so_professor" in ast.unparse(rota.args)


# --------------------------------------------------------------------------- e-mail de teste da coordenação
def chamar_teste(av, para="coordenacao@unicerrado.edu.br", u=ADMIN):
    return status_de(av.ns["adm_email_teste"], av.ns["EmailTesteIn"](para=para), FakeReq(), u, av.db)


def test_ct2031_envia_um_unico_email_de_teste(av):
    st, r = chamar_teste(av)
    assert st == 200 and r["ok"] is True and "spam" in r["mensagem"]
    assert av.destinos() == ["coordenacao@unicerrado.edu.br"]
    assert av.emails[0][1] == "Teste do Guará Check"


def test_ct2032_endereco_e_normalizado(av):
    chamar_teste(av, para="  Coordenacao@UniCerrado.edu.br ")
    assert av.destinos() == ["coordenacao@unicerrado.edu.br"]


def test_ct2033_endereco_invalido_e_recusado(av):
    for ruim in ("sem-arroba", "a@bcd", "x y@z.com", "@nada.com"):
        st, r = chamar_teste(av, para=ruim)
        assert st == 422, ruim
    assert av.emails == []


def test_ct2034_campo_obrigatorio_e_com_tamanho(av):
    for ruim in ("", "a@b", "x" * 200 + "@a.com"):
        with pytest.raises(ValidationError):
            av.ns["EmailTesteIn"](para=ruim)


def test_ct2035_sem_configuracao_diz_o_que_definir_e_nao_envia():
    c = CtxAv(email=False)
    st, r = chamar_teste(c)
    assert st == 200 and r["ok"] is False and "EMAIL_API_KEY" in r["mensagem"] and "EMAIL_REMETENTE" in r["mensagem"]
    assert c.emails == []


def test_ct2036_falha_do_provedor_vira_frase_com_o_motivo(av):
    av.ns["enviar_email"] = recusa_remetente
    st, r = chamar_teste(av)
    assert st == 200 and r["ok"] is False
    assert r["mensagem"].startswith("O teste falhou: O remetente (EMAIL_REMETENTE)") and r["mensagem"].endswith(".")
    assert "@" not in r["mensagem"]


def test_ct2037_o_log_nao_guarda_o_endereco(av):
    chamar_teste(av)
    av.ns["enviar_email"] = recusa_remetente
    chamar_teste(av)
    logs = av.logs("email_teste")
    assert [l.detalhe[:7] for l in logs] == ["enviado", "falhou:"]
    assert all("@" not in (l.detalhe or "") for l in logs) and all(l.usuario_id == ADMIN["id"] for l in logs)


def test_ct2038_limite_de_cinco_testes_em_dez_minutos(av):
    for _ in range(5):
        assert chamar_teste(av)[0] == 200
    assert chamar_teste(av)[0] == 429
    assert len(av.emails) == 5


def test_ct2039_o_teste_sem_configuracao_nao_gasta_o_limite():
    c = CtxAv(email=False)
    for _ in range(8):
        assert chamar_teste(c)[0] == 200


def test_ct2040_a_rota_do_teste_exige_coordenacao():
    import ast
    fonte = open(APP, encoding="utf-8").read()
    rota = next(n for n in ast.parse(fonte).body if isinstance(n, ast.FunctionDef) and n.name == "adm_email_teste")
    assert "so_admin" in ast.unparse(rota.args)
