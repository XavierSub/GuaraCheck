# -*- coding: utf-8 -*-
"""
QA - Funcionalidade 22: CERTIFICADO BLOQUEADO ATÉ A FACULDADE LIBERAR O MODELO
Padrão: BLOQUEADO (CERTIFICADO_ATIVO desligado). O site continua mostrando a parte do certificado ("Certificado: em breve"), mas o PDF
não sai e a conferência pública do código responde que ainda não está liberada. Nada é apagado: a carga horária do evento continua
guardada. CERTIFICADO_ATIVO=1 (ou true/sim/on) libera, sem outra mudança de código. /api/config e o Resumo da coordenação informam o estado.
Uso:  pytest tests/test_func22_certificado_bloqueado.py -v
"""
import ast

from harness_app import *  # noqa: F401,F403
from test_func5_chamada_comprovantes import texto_pdf
from test_func6_exportacao_auditoria import CtxEx
from test_func15_certificado import certificado, validar, codigo_de


def montar(valor=None):
    c = CtxEx(CERTIFICADO_ATIVO=valor)              # None: a variável fica sem valor, como no deploy sem a chave
    c.aula.carga_horas = 2.5
    c.aula.titulo = "Semana de Tecnologia"
    c.aula.tipo = "palestra"
    return c


@pytest.fixture
def bl():
    return montar()


@pytest.fixture
def lib():
    return montar("1")


# --------------------------------------------------------------------------- padrão e leitura da variável
def test_ct2201_sem_a_variavel_o_certificado_fica_bloqueado(bl):
    assert bl.ns["CERTIFICADO_ATIVO"] is False


@pytest.mark.parametrize("valor", ["1", "true", "TRUE", "sim", "Sim", "on", " 1 "])
def test_ct2202_valores_que_liberam(valor):
    assert montar(valor).ns["CERTIFICADO_ATIVO"] is True


@pytest.mark.parametrize("valor", ["0", "", "nao", "não", "false", "off", "talvez", "2"])
def test_ct2203_qualquer_outro_valor_continua_bloqueado(valor):
    assert montar(valor).ns["CERTIFICADO_ATIVO"] is False


# --------------------------------------------------------------------------- emissão bloqueada
def test_ct2211_pdf_do_certificado_recusado_mesmo_com_tudo_certo(bl):
    bl.registrar()
    st, msg = certificado(bl, bl.presencas()[0].id)
    assert st == 403 and msg == bl.ns["MSG_CERTIFICADO_BLOQUEADO"] and "ainda não está liberado" in msg


def test_ct2212_bloqueio_vale_antes_de_qualquer_consulta(bl):
    """Presença de outro aluno ou inexistente também dá 403: o bloqueio não revela se o número existe."""
    for numero in (1, 999999):
        assert certificado(bl, numero)[0] == 403


def test_ct2213_bloqueio_nao_gasta_o_limite_de_downloads(bl):
    for _ in range(40):
        assert certificado(bl, 1)[0] == 403


def test_ct2214_liberado_o_pdf_sai_normalmente(lib):
    lib.registrar()
    st, resp = certificado(lib, lib.presencas()[0].id)
    assert st == 200 and resp.media_type == "application/pdf" and "CERTIFICADO DE PARTICIPAÇÃO" in texto_pdf(resp.body)


def test_ct2215_so_o_aluno_continua_passando_no_guarda_da_rota():
    fonte = open(APP, encoding="utf-8").read()
    rota = next(n for n in ast.parse(fonte).body if isinstance(n, ast.FunctionDef) and n.name == "certificado_pdf")
    assert "so_aluno" in ast.unparse(rota.args)


# --------------------------------------------------------------------------- conferência pública bloqueada
def test_ct2221_conferencia_do_codigo_bloqueada_ate_para_codigo_verdadeiro(lib):
    lib.registrar()
    codigo = codigo_de(lib)
    assert validar(lib, codigo)[1]["valido"] is True          # liberado: confere
    lib.ns["CERTIFICADO_ATIVO"] = False
    st, msg = validar(lib, codigo)
    assert st == 403 and msg == lib.ns["MSG_CERTIFICADO_BLOQUEADO"]


def test_ct2222_bloqueada_nao_confirma_nem_nega_codigo_e_nao_cobra_limite(bl):
    for ruim in ("C1-AAAAAAAAAAAA", "lixo", "", "C1042-9F3A7C21B4AA"):
        assert validar(bl, ruim)[0] == 403
    for _ in range(40):
        assert validar(bl, "C1-AAAAAAAAAAAA")[0] == 403      # nenhum 429: o bloqueio vem antes do limite por IP


# --------------------------------------------------------------------------- o resto do app não muda
def test_ct2231_comprovante_de_presenca_continua_saindo(bl):
    bl.registrar()
    st, resp = bl.pdf(bl.presencas()[0].id)
    assert st == 200 and resp.media_type == "application/pdf"


def test_ct2232_carga_horaria_continua_guardada_e_listada(bl):
    bl.registrar()
    st, itens = bl.meus()
    assert st == 200 and itens[0]["carga_horas"] == 2.5          # a tela usa isto para mostrar "Certificado: em breve"
    assert bl.aula.carga_horas == 2.5


def test_ct2233_professor_ainda_pode_informar_carga_horaria(bl):
    b = bl.ns["AulaIn"](titulo="Palestra", latitude=-18.4, longitude=-49.2, raio_metros=60, carga_horas=3)
    assert b.carga_horas == 3


# --------------------------------------------------------------------------- a tela fica sabendo
def test_ct2241_config_publica_informa_o_estado(bl, lib):
    st, c1 = status_de(bl.ns["config"], bl.db)
    st2, c2 = status_de(lib.ns["config"], lib.db)
    assert st == 200 and c1["certificado_ativo"] is False and st2 == 200 and c2["certificado_ativo"] is True


def test_ct2242_resumo_da_coordenacao_informa_o_estado():
    fonte = open(APP, encoding="utf-8").read()
    rota = next(n for n in ast.parse(fonte).body if isinstance(n, ast.FunctionDef) and n.name == "adm_resumo")
    assert '"certificado_ativo": CERTIFICADO_ATIVO' in ast.get_source_segment(fonte, rota)


def test_ct2243_render_e_docker_documentam_a_chave():
    raiz = Path(__file__).resolve().parents[1]
    assert "CERTIFICADO_ATIVO" in (raiz / "render.yaml").read_text(encoding="utf-8")
    assert "CERTIFICADO_ATIVO" in (raiz / "DOCKER.md").read_text(encoding="utf-8")
