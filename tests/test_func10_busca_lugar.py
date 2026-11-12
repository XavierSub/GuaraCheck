# -*- coding: utf-8 -*-
"""
QA - Funcionalidade 10: BUSCA DE LUGAR PELO NOME (mapa da sala)
O professor, num computador sem GPS, digita "Passeio das Águas Shopping, Goiânia" e o mapa vai até lá. A página chama
GET /api/lugares; o servidor consulta o Nominatim (OpenStreetMap), que permite 1 consulta por segundo somando todos os
usuários, exige User-Agent e pede cache. O Nominatim é substituído por um dublê: nenhum teste sai para a internet.

Como nas outras funcionalidades: o código REAL do app.py é extraído por ast e executado (harness_app.py).
Uso:  pytest tests/test_func10_busca_lugar.py -v
"""
import ast as _ast
import io
import types as _types
import urllib.error
import urllib.parse
import urllib.request

from harness_app import *  # noqa: F401,F403

PASSEIO = {"lat": "-16.7100", "lon": "-49.2750", "display_name": "Passeio das Águas Shopping, Avenida Perimetral Norte, Goiânia, Goiás, Brasil",
           "boundingbox": ["-16.7110", "-16.7090", "-49.2760", "-49.2740"]}
FLAMBOYANT = {"lat": "-16.7040", "lon": "-49.2370", "display_name": "Shopping Flamboyant, Goiânia, Goiás, Brasil",
              "boundingbox": ["-16.7050", "-16.7030", "-49.2380", "-49.2360"]}


class CtxLugar(Ctx):
    """Professor logado + dublê do Nominatim (guarda cada requisição feita e o instante do relógio de mentira)."""

    def __init__(self):
        super().__init__()
        self.prof = self.semear_prof(email=EMAIL_PROF, nome="Carlos Lima")
        self.ana = self.semear_prof(email="ana.prof@unicerrado.edu.br", nome="Ana Prof")
        self.pedidos, self.instantes, self.leituras = [], [], []
        self.resposta, self.erro = [PASSEIO], None
        ctx = self

        class Resp(io.BytesIO):
            def read(self, n=-1):
                ctx.leituras.append(n)
                return super().read(n)

        def urlopen(req, timeout=None):
            ctx.pedidos.append((req, timeout))
            ctx.instantes.append(ctx.ns["time"].t)
            if ctx.erro:
                raise ctx.erro
            corpo = ctx.resposta if isinstance(ctx.resposta, (bytes, bytearray)) else json.dumps(ctx.resposta).encode()
            return Resp(corpo)

        self.ns["urllib"] = _types.SimpleNamespace(
            request=_types.SimpleNamespace(urlopen=urlopen, Request=urllib.request.Request), parse=urllib.parse)

    def eu(self, p=None):
        p = p or self.prof
        return {"id": p.id, "role": "professor", "nome": p.nome}

    def buscar(self, q, p=None):
        return status_de(self.ns["buscar_lugares"], q, self.eu(p))

    def url(self, i=-1):
        return urllib.parse.urlsplit(self.pedidos[i][0].full_url)

    def params(self, i=-1):
        return dict(urllib.parse.parse_qsl(self.url(i).query))


@pytest.fixture
def lg():
    return CtxLugar()


def test_ct1201_a_rota_so_atende_professor_logado():
    achadas = {}
    for n in _ast.parse(APP.read_text(encoding="utf-8")).body:
        if isinstance(n, _ast.FunctionDef):
            for d in n.decorator_list:
                if (isinstance(d, _ast.Call) and d.args and isinstance(d.args[0], _ast.Constant)
                        and d.args[0].value == "/api/lugares"):
                    achadas[n.name] = [x.args[0].id for x in _ast.walk(n.args) if isinstance(x, _ast.Call)
                                       and getattr(x.func, "id", "") == "Depends" and x.args and isinstance(x.args[0], _ast.Name)]
    assert achadas == {"buscar_lugares": ["so_professor"]}, achadas


def test_ct1202_busca_valida_devolve_nome_ponto_e_caixa(lg):
    cod_, r = lg.buscar("Passeio das Águas Shopping, Goiânia")
    assert cod_ == 200
    assert r["resultados"] == [{"nome": PASSEIO["display_name"], "lat": -16.71, "lon": -49.275,
                                "caixa": [-16.711, -49.276, -16.709, -49.274]}]      # [sul, oeste, norte, leste]


def test_ct1203_pedido_ao_nominatim_identifica_o_app_e_usa_https(lg):
    lg.buscar("Passeio das Águas Shopping, Goiânia")
    u, req = lg.url(), lg.pedidos[-1][0]
    assert u.scheme == "https" and u.netloc == "nominatim.openstreetmap.org" and u.path == "/search"
    assert req.get_header("User-agent", "").startswith("GuaraCheck/"), "o Nominatim exige User-Agent que identifique o aplicativo"
    p = lg.params()
    assert p["q"] == "Passeio das Águas Shopping, Goiânia" and p["countrycodes"] == "br" and p["limit"] == "5"
    assert p["format"] == "jsonv2" and p["accept-language"] == "pt-BR" and p["bounded"] == "0"
    assert lg.pedidos[-1][1] and lg.pedidos[-1][1] <= 10, "tem de haver tempo limite (o servidor não fica pendurado)"


def test_ct1204_texto_curto_ou_enorme_e_recusado_sem_chamar_o_nominatim(lg):
    assert cod(lg.buscar("")) == 422 and cod(lg.buscar("  ab  ")) == 422 and cod(lg.buscar("a" * 121)) == 422
    assert cod(lg.buscar("abc")) == 200 and cod(lg.buscar("a" * 120)) == 200
    assert len(lg.pedidos) == 2


def test_ct1205_espacos_extras_sao_ignorados_e_o_texto_vai_limpo(lg):
    lg.buscar("  Passeio   das\tÁguas \n Shopping  ")
    assert lg.params()["q"] == "Passeio das Águas Shopping"


def test_ct1206_texto_com_simbolos_e_acentos_vai_codificado_sem_injetar_parametros(lg):
    lg.buscar("Rua 1&limit=50&format=xml#x")
    assert lg.params()["limit"] == "5" and lg.params()["format"] == "jsonv2" and lg.params()["q"] == "Rua 1&limit=50&format=xml#x"


def test_ct1207_busca_repetida_vem_do_cache_mesmo_com_outra_caixa_ou_espacos(lg):
    assert cod(lg.buscar("Passeio das Águas Shopping")) == 200
    for q in ("passeio das águas shopping", "  PASSEIO   DAS ÁGUAS   SHOPPING "):
        assert cod(lg.buscar(q)) == 200
    assert len(lg.pedidos) == 1, "as repetidas não podem gastar a cota de 1 consulta por segundo do OpenStreetMap"


def test_ct1208_cache_vale_24_horas_e_depois_consulta_de_novo(lg):
    lg.buscar("Shopping Flamboyant")
    lg.ns["time"].t += 24 * 3600 - 5
    lg.buscar("Shopping Flamboyant")
    assert len(lg.pedidos) == 1
    lg.ns["time"].t += 10
    lg.buscar("Shopping Flamboyant")
    assert len(lg.pedidos) == 2


def test_ct1209_o_cache_e_compartilhado_entre_professores(lg):
    lg.buscar("Shopping Flamboyant", lg.prof)
    lg.buscar("Shopping Flamboyant", lg.ana)
    assert len(lg.pedidos) == 1


def test_ct1210_buscas_diferentes_ficam_a_mais_de_1_segundo_uma_da_outra(lg):
    for q in ("Shopping Flamboyant", "Passeio das Águas", "Parque Vaca Brava", "Catedral de Goiânia"):
        assert cod(lg.buscar(q)) == 200
    folgas = [b - a for a, b in zip(lg.instantes, lg.instantes[1:])]
    assert len(lg.instantes) == 4 and all(f >= 1.1 - 1e-9 for f in folgas), folgas


def test_ct1211_busca_depois_de_um_tempo_nao_espera_a_toa(lg):
    lg.buscar("Shopping Flamboyant")
    lg.ns["time"].t += 30
    antes = lg.ns["time"].t
    lg.buscar("Passeio das Águas")
    assert lg.instantes[-1] == antes


def test_ct1212_cada_professor_tem_20_buscas_por_minuto_e_o_outro_nao_e_afetado(lg):
    for i in range(20):
        assert cod(lg.buscar(f"lugar numero {i}")) == 200
    assert cod(lg.buscar("lugar numero 21")) == 429
    assert cod(lg.buscar("lugar numero 0")) == 429, "mesmo a repetida (cache) conta: o freio vem antes"
    assert cod(lg.buscar("outro lugar", lg.ana)) == 200
    lg.ns["time"].t += 61
    assert cod(lg.buscar("lugar numero 21")) == 200


@pytest.mark.parametrize("erro", [urllib.error.URLError("sem rede"), TimeoutError("demorou"), OSError("reset"),
                                  urllib.error.HTTPError("https://x", 429, "Too Many Requests", {}, None)])
def test_ct1213_nominatim_fora_do_ar_vira_503_em_portugues_e_nao_vai_para_o_cache(lg, erro):
    lg.erro = erro
    r = lg.buscar("Shopping Flamboyant")
    assert cod(r) == 503 and "Clique no mapa" in r[1] and "Traceback" not in r[1] and "nominatim" not in r[1].lower()
    lg.erro = None
    assert cod(lg.buscar("Shopping Flamboyant")) == 200, "a falha não pode ficar guardada"
    assert len(lg.pedidos) == 2


@pytest.mark.parametrize("corpo", [b"<html>429</html>", b"", b'{"erro": "x"}', b"\xff\xfe"])
def test_ct1214_resposta_quebrada_do_nominatim_vira_503(lg, corpo):
    lg.resposta = corpo
    assert cod(lg.buscar("Shopping Flamboyant")) == 503


def test_ct1215_itens_invalidos_sao_descartados_e_so_5_passam(lg):
    lg.resposta = ["texto", None, {"lat": "abc", "lon": "-49"}, {"lat": "95", "lon": "0"}, {"lat": "0", "lon": "181"},
                   {"lat": "NaN", "lon": "0"}, {"lat": "-16.7", "lon": "inf"}, {"lat": None, "lon": None}] + \
                  [{"lat": str(-16 - i / 100), "lon": "-49.2", "display_name": f"Lugar {i}"} for i in range(8)]
    r = lg.buscar("Shopping Flamboyant")[1]["resultados"]
    assert [x["nome"] for x in r] == [f"Lugar {i}" for i in range(5)], r
    lg.resposta = [{"lat": "1", "lon": "2"}] * 5
    assert lg.buscar("outro")[1]["resultados"][0]["nome"] == "Lugar sem nome"


def test_ct1216_caixa_invalida_ou_invertida_vira_none_mas_o_ponto_continua(lg):
    for bb in (None, "x", ["1", "2"], ["a", "b", "c", "d"], ["-16.7", "-16.8", "-49.3", "-49.2"], ["-16.8", "-16.7", "-49.2", "-49.3"],
               ["-91", "-16.7", "-49.3", "-49.2"]):
        lg.resposta = [{"lat": "-16.71", "lon": "-49.27", "display_name": "X", "boundingbox": bb}]
        lg._chave = getattr(lg, "_chave", 0) + 1
        r = lg.buscar(f"lugar {lg._chave} qualquer")
        assert cod(r) == 200 and r[1]["resultados"][0]["caixa"] is None and r[1]["resultados"][0]["lat"] == -16.71, (bb, r)


def test_ct1217_nome_gigante_ou_com_quebras_de_linha_e_limpo(lg):
    lg.resposta = [{"lat": "-16.7", "lon": "-49.2", "display_name": "Praça\n\n  Cívica " + "x" * 500}]
    nome = lg.buscar("Praça Cívica")[1]["resultados"][0]["nome"]
    assert len(nome) <= 200 and "\n" not in nome and nome.startswith("Praça Cívica")


def test_ct1218_resposta_do_nominatim_e_lida_com_limite_de_tamanho(lg):
    lg.buscar("Shopping Flamboyant")
    assert lg.leituras and all(isinstance(n, int) and 0 < n <= 1_048_576 for n in lg.leituras), lg.leituras


def test_ct1219_lista_vazia_do_nominatim_vira_lista_vazia_e_e_guardada(lg):
    lg.resposta = []
    assert lg.buscar("lugar que não existe") == (200, {"resultados": []})
    assert lg.buscar("lugar que não existe") == (200, {"resultados": []}) and len(lg.pedidos) == 1


def test_ct1220_cache_nao_cresce_sem_limite(lg):
    n = lg.ns["LUGAR_CACHE_MAX"]
    lg.ns["LUGAR_POR_MINUTO"] = 10**9
    for i in range(n + 50):
        assert cod(lg.buscar(f"lugar numero {i}")) == 200
    assert len(lg.ns["_lugar_cache"]) <= n
    assert lg.ns["_lugar_cache"], "limpar não pode apagar tudo"


def test_ct1221_busca_ocupada_demais_devolve_503_em_vez_de_pendurar(lg):
    class Ocupado:                                  # outra busca está segurando a vez e não larga
        def acquire(self, timeout=None):
            assert timeout and timeout <= 15
            return False
        def release(self):
            raise AssertionError("não pegou a vez, não pode soltar")
    lg.ns["_lugar_lock"] = Ocupado()
    r = lg.buscar("Shopping Flamboyant")
    assert cod(r) == 503 and "instantes" in r[1] and not lg.pedidos


def test_ct1222_a_vez_e_devolvida_mesmo_quando_a_busca_falha(lg):
    lg.erro = OSError("x")
    lg.buscar("Shopping Flamboyant")
    assert lg.ns["_lugar_lock"].acquire(timeout=0.1), "a trava ficou presa depois do erro: toda busca seguinte travaria"
    lg.ns["_lugar_lock"].release()
