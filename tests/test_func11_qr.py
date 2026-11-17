# -*- coding: utf-8 -*-
"""
QA - Funcionalidade 11: PRESENÇA POR QR CODE
O professor, com a chamada aberta, vê (e pode projetar) um QR code. O aluno lê com a câmera do celular e o app abre direto na
chamada (/?aula=ID). O QR NÃO registra presença sozinho: o aluno ainda precisa de login, GPS dentro do raio e de todas as checagens
antifraude. A rota é GET /api/aulas/{id}/qr (só o dono da chamada, só chamada aberta) e devolve {url, svg}.

Como nas outras funcionalidades: o código REAL do app.py é extraído por ast e executado (harness_app.py). Para provar que o desenho
é lido por uma câmera de verdade, o SVG é redesenhado em pixels e decodificado com o OpenCV (pulado se o OpenCV não estiver instalado).
Uso:  pytest tests/test_func11_qr.py -v
"""
import ast as _ast
import re as _re
import types as _types
import xml.etree.ElementTree as ET

from harness_app import *  # noqa: F401,F403

HOST = "guaracheck.onrender.com"


def req(host=HOST, proto="https", scheme="http", **extra):
    """Requisição de mentira com só o que url_base lê: cabeçalhos e o endereço que o servidor enxerga."""
    h = {}
    if host is not None:
        h["host"] = host
    if proto is not None:
        h["x-forwarded-proto"] = proto
    h.update(extra)
    return _types.SimpleNamespace(headers=h, url=_types.SimpleNamespace(scheme=scheme, netloc=host or "interno:10000"))


class CtxQr(Ctx):
    def __init__(self, **env):
        super().__init__(**env)
        self.prof = self.semear_prof(email=EMAIL_PROF, nome="Carlos Lima")
        self.ana = self.semear_prof(email="ana.prof@unicerrado.edu.br", nome="Ana Prof")
        self.aula = self.nova_aula(self.prof)

    def nova_aula(self, prof, **kw):
        d = dict(professor_id=prof.id, titulo="Algoritmos", latitude=-18.4, longitude=-49.2, raio_metros=50.0, tipo="aula",
                 local_tipo="dentro")
        d.update(kw)
        a = Aula(**d)
        self.db.add(a)
        self.db.commit()
        return a

    def eu(self, p=None):
        p = p or self.prof
        return {"id": p.id, "role": "professor", "nome": p.nome}

    def qr(self, aula=None, prof=None, request=None):
        aula = aula or self.aula
        return status_de(self.ns["qr_da_aula"], aula.id if hasattr(aula, "id") else aula, request or req(), self.eu(prof), self.db)


@pytest.fixture
def q():
    return CtxQr()


# --------------------------------------------------------------------------- acesso
def test_ct1301_a_rota_so_atende_professor_logado():
    achadas = {}
    for n in _ast.parse(APP.read_text(encoding="utf-8")).body:
        if isinstance(n, _ast.FunctionDef):
            for d in n.decorator_list:
                if (isinstance(d, _ast.Call) and d.args and isinstance(d.args[0], _ast.Constant)
                        and d.args[0].value == "/api/aulas/{aula_id}/qr"):
                    achadas[n.name] = ([x.args[0].id for x in _ast.walk(n.args) if isinstance(x, _ast.Call)
                                        and getattr(x.func, "id", "") == "Depends" and x.args and isinstance(x.args[0], _ast.Name)],
                                       d.func.attr)
    assert achadas == {"qr_da_aula": (["so_professor", "get_db"], "get")}, achadas


def test_ct1302_o_dono_da_chamada_recebe_link_e_desenho(q):
    st, r = q.qr()
    assert st == 200, r
    assert r["url"] == f"https://{HOST}/?aula={q.aula.id}"
    assert r["svg"].startswith("<svg ") and r["svg"].endswith("</svg>")


def test_ct1303_professor_de_outra_chamada_recebe_404(q):
    st, r = q.qr(prof=q.ana)
    assert st == 404 and "encontrada" in r, "um professor não pode pegar o QR da chamada de outro"


def test_ct1304_chamada_inexistente_recebe_404(q):
    st, _ = q.qr(aula=99999)
    assert st == 404


def test_ct1305_chamada_encerrada_nao_gera_qr(q):
    q.aula.ativa = False
    st, r = q.qr()
    assert st == 409 and "encerrada" in r


def test_ct1306_chamada_que_passou_do_prazo_nao_gera_qr():
    c = CtxQr(AULA_MAX_HORAS="2")
    c.aula.aberta_em = c.agora - timedelta(hours=3)
    st, r = c.qr()
    assert st == 409 and "encerrada" in r
    c.aula.aberta_em = c.agora - timedelta(hours=1)
    assert c.qr()[0] == 200, "dentro do prazo ainda gera"


def test_ct1307_cada_chamada_tem_o_seu_proprio_link(q):
    q.aula.ativa = False
    nova = q.nova_aula(q.prof, titulo="Banco de Dados")
    st, r = q.qr(aula=nova)
    assert st == 200 and r["url"].endswith(f"/?aula={nova.id}") and nova.id != q.aula.id


# --------------------------------------------------------------------------- endereço do site dentro do QR
def test_ct1308_endereco_vem_do_site_que_o_professor_esta_usando(q):
    assert q.qr(request=req("meu.site.edu.br", "https"))[1]["url"].startswith("https://meu.site.edu.br/?aula=")
    assert q.qr(request=req("localhost:8000", None, scheme="http"))[1]["url"].startswith("http://localhost:8000/?aula=")
    assert q.qr(request=req("127.0.0.1:8000", None, scheme="http"))[1]["url"].startswith("http://127.0.0.1:8000/?aula=")


def test_ct1309_atras_do_proxy_usa_o_esquema_original(q):
    """No Render o app enxerga http, mas o aluno chegou por https: o QR tem que levar https."""
    assert q.qr(request=req(proto="https", scheme="http"))[1]["url"].startswith("https://")
    assert q.qr(request=req(proto="https, http", scheme="http"))[1]["url"].startswith("https://"), "vale o primeiro da lista"
    assert q.qr(request=req(proto="ftp", scheme="http"))[1]["url"].startswith("https://"), "esquema estranho vira https"


def test_ct1310_public_url_manda_no_endereco():
    c = CtxQr(PUBLIC_URL="https://presenca.unicerrado.edu.br/")
    r = c.qr(request=req("qualquer.coisa"))[1]
    assert r["url"] == f"https://presenca.unicerrado.edu.br/?aula={c.aula.id}", "a barra final é aparada"


def test_ct1311_public_url_sem_http_e_ignorada():
    c = CtxQr(PUBLIC_URL="javascript:alert(1)")
    assert c.qr()[1]["url"].startswith(f"https://{HOST}/"), "PUBLIC_URL só vale se começar com http:// ou https://"


@pytest.mark.parametrize("host", ['evil.com/"><script>', "a b.com", "x.com\r\nSet-Cookie: a=b", "http://outro.com", "@evil.com"])
def test_ct1312_host_estranho_e_recusado(q, host):
    st, r = q.qr(request=req(host))
    assert st == 500 and "PUBLIC_URL" in r, f"host {host!r} não pode virar link"


def test_ct1313a_sem_cabecalho_host_usa_o_endereco_que_o_servidor_enxerga(q):
    assert q.qr(request=req(None, "https"))[1]["url"] == f"https://interno:10000/?aula={q.aula.id}"


def test_ct1313_host_ipv6_e_porta_sao_aceitos(q):
    assert q.qr(request=req("[::1]:8000", None, scheme="http"))[1]["url"].startswith("http://[::1]:8000/?aula=")


# --------------------------------------------------------------------------- o desenho
def _modulos(svg):
    """Lê de volta o SVG: (tamanho do viewBox, conjunto de (linha, coluna) escuras)."""
    raiz = ET.fromstring(svg)
    tam = int(raiz.attrib["viewBox"].split()[2])
    d = [e for e in raiz.iter() if e.tag.endswith("path")][0].attrib["d"]
    escuros = set()
    for x, y, w in _re.findall(r"M(\d+) (\d+)h(\d+)v1h-\d+z", d):
        for k in range(int(w)):
            escuros.add((int(y), int(x) + k))
    return tam, escuros


def test_ct1314_svg_e_xml_valido_sem_script_e_acessivel(q):
    svg = q.qr()[1]["svg"]
    raiz = ET.fromstring(svg)
    assert raiz.tag.endswith("svg") and raiz.attrib["role"] == "img" and raiz.attrib["aria-label"]
    assert "<script" not in svg.lower() and "onload" not in svg.lower() and "href" not in svg.lower()
    assert "http" not in _re.sub(r'xmlns="http://www\.w3\.org/2000/svg"', "", svg), "nenhum endereço externo dentro do desenho"


def test_ct1315_svg_tem_zona_silenciosa_de_4_modulos_e_fundo_branco(q):
    svg = q.qr()[1]["svg"]
    tam, escuros = _modulos(svg)
    assert min(r for r, _ in escuros) == 4 and min(c for _, c in escuros) == 4
    assert max(r for r, _ in escuros) == tam - 5 and max(c for _, c in escuros) == tam - 5
    assert 'fill="#fff"' in svg and 'fill="#000"' in svg, "fundo branco e módulos pretos, qualquer que seja o tema da página"


def test_ct1316_o_desenho_e_lido_por_uma_camera_de_verdade(q):
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    for chamada in (q.aula, q.nova_aula(q.ana, titulo="Outra")):
        prof = q.prof if chamada is q.aula else q.ana
        r = q.qr(aula=chamada, prof=prof)[1]
        tam, escuros = _modulos(r["svg"])
        esc = 10
        img = np.full((tam * esc, tam * esc), 255, dtype=np.uint8)
        for lin, col in escuros:
            img[lin * esc:(lin + 1) * esc, col * esc:(col + 1) * esc] = 0
        lido, _, _ = cv2.QRCodeDetector().detectAndDecode(cv2.cvtColor(img, cv2.COLOR_GRAY2BGR))
        assert lido == r["url"], f"a câmera leu {lido!r}, esperado {r['url']!r}"


def test_ct1317_link_comprido_continua_legivel_e_pequeno():
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    c = CtxQr(PUBLIC_URL="https://presenca-da-universidade-do-cerrado.exemplo.edu.br/guara-check")
    r = c.qr()[1]
    assert len(r["svg"]) < 12_000, "o SVG vai em toda consulta do painel: precisa ser leve"
    tam, escuros = _modulos(r["svg"])
    img = np.full((tam * 8, tam * 8), 255, dtype=np.uint8)
    for lin, col in escuros:
        img[lin * 8:(lin + 1) * 8, col * 8:(col + 1) * 8] = 0
    assert cv2.QRCodeDetector().detectAndDecode(cv2.cvtColor(img, cv2.COLOR_GRAY2BGR))[0] == r["url"]


def test_ct1318_o_qr_e_estavel_para_o_mesmo_link(q):
    assert q.qr()[1]["svg"] == q.qr()[1]["svg"]


# --------------------------------------------------------------------------- o QR não cria atalho para a presença
def test_ct1319_a_rota_do_qr_nao_toca_em_presenca_nem_em_tentativas(q):
    antes = (len(q.db.tables[Presenca]), len(q.db.tables[Tentativa]))
    for _ in range(3):
        assert q.qr()[0] == 200
    assert (len(q.db.tables[Presenca]), len(q.db.tables[Tentativa])) == antes
    fonte = _ast.get_source_segment(APP.read_text(encoding="utf-8"),
                                    next(n for n in _ast.parse(APP.read_text(encoding="utf-8")).body
                                         if isinstance(n, _ast.FunctionDef) and n.name == "qr_da_aula"))
    assert "Presenca(" not in fonte and "db.add" not in fonte and "commit" not in fonte, "só lê: nunca grava presença"
