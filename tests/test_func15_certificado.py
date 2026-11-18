# -*- coding: utf-8 -*-
"""
QA - Funcionalidade 15: CERTIFICADO DE PARTICIPAÇÃO (PDF) E VALIDAÇÃO PÚBLICA
O aluno baixa o certificado da própria presença quando o professor informou a carga horária do evento e, se o evento exige
saída, quando a saída foi registrada com a permanência mínima. O PDF traz um código de verificação (HMAC com a chave do servidor)
que qualquer pessoa confere, sem login, em /api/certificados/validar/{codigo} (devolve só nome, evento, data e carga horária).
Uso:  pytest tests/test_func15_certificado.py -v
"""
from harness_app import *  # noqa: F401,F403
from test_func4_presenca import IP_PUBLICO, dev
from test_func5_chamada_comprovantes import texto_pdf, pypdf
from test_func6_exportacao_auditoria import CtxEx


@pytest.fixture
def ce():
    c = CtxEx()
    c.aula.carga_horas = 2.5
    c.aula.titulo = "Semana de Tecnologia"
    c.aula.tipo = "palestra"
    return c


def certificado(c, presenca_id, aluno=None):
    return status_de(c.ns["certificado_pdf"], presenca_id, c.sessao(aluno), c.db)


def validar(c, codigo, ip=IP_PUBLICO):
    return status_de(c.ns["validar_certificado"], codigo, FakeReq(ip), c.db)


def codigo_de(c, p=None):
    p = p or c.presencas()[0]
    return f"C{p.id}-{c.ns['assinatura_certificado'](p, c.aula)}"


def sair(c, aluno=None, **over):
    return status_de(c.ns["registrar_saida"], c.aula.id, c.corpo(0.0, 0.0, **over), FakeReq(IP_PUBLICO), c.sessao(aluno), c.db)


# --------------------------------------------------------------------------- emissão
def test_ct1701_aluno_baixa_o_proprio_certificado(ce):
    ce.registrar()
    p = ce.presencas()[0]
    st, resp = certificado(ce, p.id)
    assert st == 200 and resp.media_type == "application/pdf" and resp.body[:5] == b"%PDF-"
    assert resp.headers["Content-Disposition"] == f'attachment; filename="certificado-{p.id}.pdf"'
    assert resp.headers["Cache-Control"] == "no-store"
    leitor = pypdf.PdfReader(io.BytesIO(resp.body))
    assert len(leitor.pages) == 1 and leitor.pages[0].mediabox.width > leitor.pages[0].mediabox.height, "uma página, em paisagem"


def test_ct1702_conteudo_do_certificado(ce):
    ce.aula.disciplina, ce.aula.local_descricao = "Gestão", "Auditório"
    ce.registrar()
    p = ce.presencas()[0]
    t = texto_pdf(certificado(ce, p.id)[1].body).replace("\n", " ")
    for esperado in ("CERTIFICADO DE PARTICIPAÇÃO", "Aluno 1", ce.aluno.matricula, "Gestão da TI", "da palestra", "Semana de Tecnologia",
                     "disciplina Gestão", "em Auditório", "Carlos Lima", "carga horária de 2,5 horas", codigo_de(ce, p),
                     "Este certificado não contém CPF nem coordenadas"):
        assert esperado in t, esperado
    assert "Permanência registrada" not in t, "evento sem saída não fala de permanência"


def test_ct1703_aula_diz_da_aula_e_horas_no_singular(ce):
    ce.aula.tipo, ce.aula.carga_horas = "aula", 1
    ce.registrar()
    t = texto_pdf(certificado(ce, ce.presencas()[0].id)[1].body).replace("\n", " ")
    assert "participou da aula" in t and "carga horária de 1 hora." in t


@pytest.mark.parametrize("h,texto", [(1, "1 hora"), (0.5, "0,5 hora"), (1.5, "1,5 horas"), (2, "2 horas"), (8, "8 horas"), (12.25, "12,25 horas")])
def test_ct1704_horas_por_extenso(ce, h, texto):
    assert ce.ns["_horas_texto"](h) == texto


def test_ct1705_sem_carga_horaria_nao_emite(ce):
    ce.aula.carga_horas = None
    ce.registrar()
    st, r = certificado(ce, ce.presencas()[0].id)
    assert st == 409 and "carga horária" in r


def test_ct1706_presenca_lancada_pelo_professor_tem_certificado(ce):
    b = ce.ns["PresencaManualIn"](aluno_id=ce.aluno.id, justificativa="Celular sem bateria")
    status_de(ce.ns["lancar_presenca_manual"], ce.aula.id, b, FakeReq(IP_PUBLICO), ce.eu(), ce.db)
    assert certificado(ce, ce.presencas()[0].id)[0] == 200


# --------------------------------------------------------------------------- evento com saída
def test_ct1707_sem_saida_registrada_nao_emite(ce):
    ce.aula.exige_saida = True
    ce.registrar()
    st, r = certificado(ce, ce.presencas()[0].id)
    assert st == 409 and "não registrou a saída" in r


def test_ct1708_permanencia_abaixo_do_minimo_nao_emite(ce):
    ce.aula.exige_saida, ce.aula.permanencia_min = True, 60
    ce.registrar()
    ce.avancar(20 * 60)
    sair(ce)
    st, r = certificado(ce, ce.presencas()[0].id)
    assert st == 409 and "20 min" in r and "1 h 00 min" in r


def test_ct1709_cumprindo_o_minimo_emite_com_a_permanencia(ce):
    ce.aula.exige_saida, ce.aula.permanencia_min = True, 60
    ce.registrar()
    ce.avancar(95 * 60)
    sair(ce)
    st, resp = certificado(ce, ce.presencas()[0].id)
    assert st == 200
    assert "Permanência registrada: 1 h 35 min" in texto_pdf(resp.body).replace("\n", " ")


# --------------------------------------------------------------------------- acesso
def test_ct1710_certificado_de_outro_aluno_e_inexistente_404(ce):
    outro = ce.novo_aluno(2)
    ce.registrar(aluno=outro, device_id=dev(2))
    p_outro = ce.presencas()[0]
    assert certificado(ce, p_outro.id)[0] == 404, "o aluno 1 não pode baixar o do aluno 2"
    assert certificado(ce, 99999)[0] == 404


def test_ct1711_so_o_aluno_usa_a_rota(ce):
    for papel in ("professor", "admin"):
        assert status_de(ce.ns["so_aluno"], {"id": 1, "role": papel, "nome": "X"})[0] == 403


def test_ct1712_limite_de_30_downloads_em_10_minutos(ce):
    ce.registrar()
    pid = ce.presencas()[0].id
    for _ in range(30):
        assert certificado(ce, pid)[0] == 200
    assert certificado(ce, pid)[0] == 429


# --------------------------------------------------------------------------- código de verificação
def test_ct1713_codigo_tem_formato_e_e_estavel(ce):
    ce.registrar()
    p = ce.presencas()[0]
    cod = codigo_de(ce, p)
    assert re.fullmatch(r"C\d+-[0-9A-F]{12}", cod) and cod == codigo_de(ce, p)


def test_ct1714_codigo_do_certificado_nao_e_o_do_comprovante(ce):
    ce.registrar()
    p = ce.presencas()[0]
    assert ce.ns["assinatura_certificado"](p, ce.aula)[:10] != ce.ns["assinatura_presenca"](p, ce.aula)


def test_ct1715_codigo_depende_da_chave_do_servidor(ce):
    ce.registrar()
    outro = CtxEx(SECRET_KEY="outra-chave-secreta-com-mais-de-32-caracteres-ok")
    outro.aula.carga_horas = 2.5
    outro.registrar()
    assert ce.ns["assinatura_certificado"](ce.presencas()[0], ce.aula) != outro.ns["assinatura_certificado"](outro.presencas()[0], outro.aula)


def test_ct1716_codigo_do_pdf_confere_na_validacao_publica(ce):
    ce.registrar()
    p = ce.presencas()[0]
    cod = re.search(r"C\d+-[0-9A-F]{12}", texto_pdf(certificado(ce, p.id)[1].body).replace("\n", ""))
    assert cod
    st, r = validar(ce, cod.group(0))
    assert st == 200 and r["valido"] is True
    assert r["nome"] == "Aluno 1" and r["evento"] == "Semana de Tecnologia" and r["carga_horas"] == 2.5 and r["data"]


def test_ct1717_validacao_nao_devolve_dados_pessoais_alem_do_nome(ce):
    ce.registrar()
    r = validar(ce, codigo_de(ce))[1]
    assert set(r) == {"valido", "nome", "evento", "data", "carga_horas"}
    assert ce.aluno.matricula not in json.dumps(r) and ce.aluno.email not in json.dumps(r)


def test_ct1718_validacao_aceita_espacos_e_minusculas(ce):
    ce.registrar()
    assert validar(ce, "  " + codigo_de(ce).lower().replace("c", "C", 1) + " ")[1]["valido"] is True


@pytest.mark.parametrize("ruim", ["", "abc", "C1-ZZZZZZZZZZZZ", "C-123456789ABC", "1-0123456789AB", "C1-0123456789A", "C1-0123456789ABC",
                                  "C" + "9" * 13 + "-0123456789AB", "C1-0123456789AB; DROP TABLE", "../../etc/passwd"])
def test_ct1719_formato_invalido_da_422(ce, ruim):
    st, r = validar(ce, ruim)
    assert st == 422 and "Formato inválido" in r


def test_ct1720_codigo_falso_ou_de_outro_aluno_nao_vale(ce):
    outro = ce.novo_aluno(2)
    ce.registrar()
    ce.registrar(aluno=outro, device_id=dev(2))
    p1, p2 = ce.presencas()
    assert validar(ce, f"C{p1.id}-{'0' * 12}")[1] == {"valido": False}
    assert validar(ce, f"C{p2.id}-{ce.ns['assinatura_certificado'](p1, ce.aula)}")[1] == {"valido": False}, "assinatura de outro aluno"
    assert validar(ce, "C99999-0123456789AB")[1] == {"valido": False}


def test_ct1721_comprovante_nao_serve_como_certificado(ce):
    ce.registrar()
    p = ce.presencas()[0]
    assert validar(ce, f"C{p.id}-{ce.ns['assinatura_presenca'](p, ce.aula)}")[0] == 422, "tamanho diferente: nem passa do formato"
    assert validar(ce, f"C{p.id}-{ce.ns['assinatura_presenca'](p, ce.aula)}AB")[1] == {"valido": False}, "mesmo preenchido até 12 dígitos"


def test_ct1722_certificado_que_nao_seria_emitido_nao_valida(ce):
    ce.registrar()
    cod = codigo_de(ce)
    assert validar(ce, cod)[1]["valido"] is True
    ce.aula.exige_saida = True                      # passou a exigir saída e o aluno não saiu
    assert validar(ce, cod)[1] == {"valido": False}


def test_ct1723_cada_carga_horaria_tem_o_seu_codigo(ce):
    ce.registrar()
    p = ce.presencas()[0]
    cod = codigo_de(ce, p)
    ce.aula.carga_horas = 40.0                      # alguém tenta inflar as horas do certificado já emitido
    assert validar(ce, cod)[1] == {"valido": False}


def test_ct1724_limite_de_30_consultas_por_ip(ce):
    ce.registrar()
    cod = codigo_de(ce)
    for _ in range(30):
        assert validar(ce, cod)[0] == 200
    assert validar(ce, cod)[0] == 429
    assert validar(ce, cod, ip="177.99.99.99")[0] == 200, "outro IP não é afetado"


def test_ct1725_a_rota_de_validacao_e_publica_mas_a_de_emissao_nao(ce):
    import ast as _ast
    fontes = {}
    for n in _ast.parse((APP if "APP" in globals() else Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")).body:
        if isinstance(n, _ast.FunctionDef) and n.name in ("validar_certificado", "certificado_pdf"):
            fontes[n.name] = [x.args[0].id for x in _ast.walk(n.args) if isinstance(x, _ast.Call) and getattr(x.func, "id", "") == "Depends"
                              and x.args and isinstance(x.args[0], _ast.Name) and x.args[0].id != "get_db"]
    assert fontes == {"validar_certificado": [], "certificado_pdf": ["so_aluno"]}


# --------------------------------------------------------------------------- lista do aluno
def test_ct1726_lista_do_aluno_traz_a_carga_horaria(ce):
    ce.registrar()
    assert ce.meus()[1][0]["carga_horas"] == 2.5
    ce.aula.carga_horas = None
    assert ce.meus()[1][0]["carga_horas"] is None


# --------------------------------------------------------------------------- texto longo não estoura a página
def test_ct1727_titulo_e_nome_enormes_cabem_em_uma_pagina(ce):
    ce.aula.titulo = "Simpósio " + "Internacional de Tecnologia e Inovação " * 8
    ce.aluno.nome = "Maria " + "da Silva Pereira " * 8
    ce.aula.local_descricao = "Auditório " + "Central " * 12
    ce.registrar()
    st, resp = certificado(ce, ce.presencas()[0].id)
    assert st == 200 and len(pypdf.PdfReader(io.BytesIO(resp.body)).pages) == 1
