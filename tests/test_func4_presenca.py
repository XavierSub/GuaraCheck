# -*- coding: utf-8 -*-
"""
QA - Funcionalidade 4: REGISTRO DE PRESENÇA (GPS E ANTIFRAUDE) (Guará Check)
Check-in do aluno: permissões e restrições do evento, geofencing (Haversine), qualidade e idade da leitura de GPS,
pontuação de risco (localização falsa), deslocamento impossível, VPN/proxy, IP real atrás do proxy, duplicidade
(aluno e aparelho, inclusive em corrida) e registro das tentativas recusadas.

Como nas funcionalidades anteriores: o código REAL do app.py é extraído por ast e executado contra um banco em memória
(harness_app.py) com as validações reais do Pydantic. O harness agora também emula UNIQUE(aula, aluno), UNIQUE(aula,
aparelho), UNIQUE(aula, aluno, motivo) e a chave estrangeira presencas.aula_id.
NÃO é executado aqui: SQLAlchemy/PostgreSQL de verdade, o proxycheck.io (dublê), o GPS do celular de verdade.

Uso:  pip install pytest pydantic pyjwt openpyxl
      pytest test_func4_presenca.py -v
"""
import ast as _ast
import types as _types

from harness_app import *  # noqa: F401,F403

LAT, LON = -18.4157, -49.2183          # Goiatuba
M_POR_GRAU = 111194.9                  # metros por grau de latitude (raio médio da Terra do app)
IP_PUBLICO = "177.10.20.30"            # IP público de verdade (203.0.113.x é "privado" para o módulo ipaddress)
IP_VPN = "185.220.101.5"
DEV = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


def dev(i):
    return f"aparelho-{i:05d}-abcdef"


def ponto(norte=0.0, leste=0.0, lat=LAT, lon=LON):
    """Ponto a `norte` metros ao norte e `leste` metros ao leste de (lat, lon)."""
    return (lat + norte / M_POR_GRAU, lon + leste / (M_POR_GRAU * math.cos(math.radians(lat))))


class CtxPr(Ctx):
    """Ctx com 2 professores, um aluno e atalhos para o check-in. Contas baratas (sem gastar PBKDF2)."""

    def __init__(self, **env):
        super().__init__(**env)
        self.prof = Professor(nome="Carlos Lima", email=EMAIL_PROF, senha_hash="x", registro_funcional="DOC-1", departamento="TI")
        self.ana = Professor(nome="Ana Prof", email="ana.prof@unicerrado.edu.br", senha_hash="x", registro_funcional="DOC-2",
                             departamento="TI")
        self.db.add(self.prof)
        self.db.add(self.ana)
        self.db.commit()
        self.aluno = self.novo_aluno(1)
        self.aula = self.nova_aula()
        self.chamadas_vpn = []
        self.resposta_vpn = None

    # --- dados prontos ---
    def novo_aluno(self, i, **extra):
        d = dict(nome=f"Aluno {i}", email=f"aluno{i}@alunos.unicerrado.edu.br", matricula=f"GTI2026{i:05d}",
                 curso_id=self.curso.id, periodo=2, status="ativo", senha_hash="x")
        d.update(extra)
        a = Aluno(**d)
        self.db.add(a)
        self.db.commit()
        return a

    def nova_aula(self, prof=None, lat=LAT, lon=LON, raio=50.0, **kw):
        d = dict(professor_id=(prof or self.prof).id, titulo="Algoritmos", latitude=lat, longitude=lon, raio_metros=raio,
                 tipo="aula", local_tipo="dentro")
        d.update(kw)
        a = Aula(**d)
        self.db.add(a)
        self.db.commit()
        return a

    def outro(self):
        """Mais um professor (cada professor só pode ter UMA chamada ativa)."""
        self._np = getattr(self, "_np", 500) + 1
        p = Professor(nome=f"Prof {self._np}", email=f"prof{self._np}@unicerrado.edu.br", senha_hash="x",
                      registro_funcional=f"DOC-{self._np}", departamento="TI")
        self.db.add(p)
        self.db.commit()
        return p

    def sessao_paralela(self):
        """Outra requisição = outra sessão do banco, mesmo banco."""
        d = FakeDB(tables=self.db.tables)
        d.relogio = self.db.relogio
        return d

    def sessao(self, aluno=None):
        a = aluno or self.aluno
        return {"id": a.id, "role": "aluno", "nome": a.nome}

    # --- simulação de VPN (dublê do proxycheck.io) ---
    def ligar_vpn(self, resposta=None, erro=None):
        """Troca urllib.request.urlopen por um dublê. `resposta` é o JSON do proxycheck; `erro` é a exceção a levantar."""
        def urlopen(url, timeout=None):
            self.chamadas_vpn.append(url)
            if erro:
                raise erro
            return io.BytesIO(json.dumps(resposta).encode())
        self.ns["urllib"] = _types.SimpleNamespace(request=_types.SimpleNamespace(urlopen=urlopen))

    @staticmethod
    def resp_proxy(ip, sim):
        return {"status": "ok", ip: {"proxy": "yes" if sim else "no", "type": "VPN" if sim else ""}}

    # --- chamadas às rotas reais ---
    def agora_ms(self):
        return self.ns["time"].time() * 1000

    def corpo(self, norte=0.0, leste=0.0, **over):
        lat, lon = ponto(norte, leste)
        d = dict(latitude=lat, longitude=lon, precisao_m=12.0, pos_ts=self.agora_ms(), amostras=5, variacao_m=3.4,
                 webdriver=False, device_id=DEV, fp_hash="abcdef0123456789")
        d.update(over)
        return self.ns["PresencaIn"](**d)

    def registrar(self, aluno=None, aula=None, ip=IP_PUBLICO, db=None, xff=None, norte=0.0, leste=0.0, **over):
        try:
            b = self.corpo(norte, leste, **over)
        except ValidationError as e:
            return 422, e
        return status_de(self.ns["registrar_presenca"], (aula or self.aula).id, b, FakeReq(ip, xff),
                         self.sessao(aluno), db or self.db)

    def presencas(self, aula=None):
        return [p for p in self.db.tables[Presenca] if aula is None or p.aula_id == aula.id]

    def tentativas(self, motivo=None):
        return [t for t in self.db.tables[Tentativa] if motivo is None or t.motivo == motivo]


@pytest.fixture
def pr():
    return CtxPr()


def erro_em(r, campo):
    return r[0] == 422 and isinstance(r[1], ValidationError) and any(e["loc"] and e["loc"][0] == campo for e in r[1].errors())


# =====================================================================================================
# BLOCO A - Acesso, estado do evento e restrições (curso / período)
# =====================================================================================================
def test_ct401_professor_nao_registra_presenca(pr):
    """CT-401 | Professor tenta usar a rota do aluno | 403."""
    r = status_de(pr.ns["so_aluno"], {"id": pr.prof.id, "role": "professor", "nome": "Carlos"})
    assert r[0] == 403, r


def test_ct402_coordenacao_nao_registra_presenca(pr):
    """CT-402 | Coordenação tenta usar a rota do aluno | 403."""
    r = status_de(pr.ns["so_aluno"], {"id": 1, "role": "admin", "nome": "Coord"})
    assert r[0] == 403, r


def test_ct403_caminho_real_token_ate_o_checkin():
    """CT-403 | Aluno com token real (login -> usuario_atual -> so_aluno -> registrar) | 200."""
    c = CtxPr()
    sess = c.usuario("Bearer " + c.token(c.aluno, "aluno"))
    assert sess[0] == 200, sess
    ok = status_de(c.ns["so_aluno"], sess[1])
    assert ok[0] == 200
    r = status_de(c.ns["registrar_presenca"], c.aula.id, c.corpo(), FakeReq(IP_PUBLICO), ok[1], c.db)
    assert r[0] == 200 and r[1]["ok"] is True, r
    assert len(c.presencas()) == 1


def test_ct404_aula_inexistente(pr):
    """CT-404 | Check-in em aula que não existe | 404."""
    r = status_de(pr.ns["registrar_presenca"], 9999, pr.corpo(), FakeReq(IP_PUBLICO), pr.sessao(), pr.db)
    assert r[0] == 404, r


def test_ct405_chamada_encerrada(pr):
    """CT-405 | Check-in depois do professor encerrar | 409, nada gravado."""
    pr.aula.ativa = False
    r = pr.registrar()
    assert r[0] == 409 and "encerrada" in r[1], r
    assert pr.presencas() == [] and pr.tentativas() == []


def test_ct406_chamada_vencida_pelo_prazo():
    """CT-406 | Professor esqueceu a chamada aberta: depois de AULA_MAX_HORAS o check-in é recusado | 409."""
    c = CtxPr(AULA_MAX_HORAS=12)
    c.aula.aberta_em = c.agora
    c.avancar(12 * 3600 - 60)
    assert c.registrar()[0] == 200                                         # ainda dentro do prazo
    c2 = CtxPr(AULA_MAX_HORAS=12)
    c2.aula.aberta_em = c2.agora
    c2.avancar(12 * 3600 + 60)
    r = c2.registrar()
    assert r[0] == 409, r
    assert c2.presencas() == []


def test_ct407_aluno_inativo(pr):
    """CT-407 | Aluno com matrícula inativa | 403 'Matrícula inativa'."""
    pr.aluno.status = "inativo"
    r = pr.registrar()
    assert r[0] == 403 and "inativa" in r[1], r
    assert pr.presencas() == []


def test_ct408_conta_removida_depois_do_login(pr):
    """CT-408 | Token ainda válido, mas a coordenação excluiu o aluno | 404, sem 500."""
    sess = pr.sessao()
    pr.db.tables[Aluno].remove(pr.aluno)
    r = status_de(pr.ns["registrar_presenca"], pr.aula.id, pr.corpo(), FakeReq(IP_PUBLICO), sess, pr.db)
    assert r[0] == 404, r


def test_ct409_evento_exclusivo_de_curso(pr):
    """CT-409 | Evento só do curso X: aluno de outro curso | 403 com o nome do curso; do mesmo curso | 200."""
    outro = Curso(nome="Direito")
    pr.db.add(outro)
    pr.db.commit()
    pr.aula.curso_id = pr.curso.id
    de_fora = pr.novo_aluno(2, curso_id=outro.id)
    r = pr.registrar(aluno=de_fora)
    assert r[0] == 403 and "Gestão da TI" in r[1], r
    assert pr.registrar()[0] == 200
    sem_curso = pr.novo_aluno(3, curso_id=None)
    assert pr.registrar(aluno=sem_curso, device_id=dev(3))[0] == 403


def test_ct410_evento_exclusivo_de_periodo(pr):
    """CT-410 | Evento só do 3º período: aluno do 2º / sem período | 403; do 3º | 200."""
    pr.aula.periodo = 3
    r = pr.registrar()
    assert r[0] == 403 and "3º período" in r[1], r
    sem_periodo = pr.novo_aluno(2, periodo=None)
    assert pr.registrar(aluno=sem_periodo, device_id=dev(2))[0] == 403
    do_3 = pr.novo_aluno(3, periodo=3)
    assert pr.registrar(aluno=do_3, device_id=dev(3))[0] == 200


def test_ct411_evento_curso_e_periodo_juntos(pr):
    """CT-411 | Restrição de curso + período: só quem cumpre as duas entra."""
    outro = Curso(nome="Direito")
    pr.db.add(outro)
    pr.db.commit()
    pr.aula.curso_id, pr.aula.periodo = pr.curso.id, 3
    assert pr.registrar(aluno=pr.novo_aluno(2, periodo=2), device_id=dev(2))[0] == 403          # curso certo, período errado
    assert pr.registrar(aluno=pr.novo_aluno(3, curso_id=outro.id, periodo=3), device_id=dev(3))[0] == 403  # período certo, curso errado
    assert pr.registrar(aluno=pr.novo_aluno(4, periodo=3), device_id=dev(4))[0] == 200


def test_ct412_evento_aberto_a_todos(pr):
    """CT-412 | Evento sem restrição aceita qualquer curso e período."""
    outro = Curso(nome="Direito")
    pr.db.add(outro)
    pr.db.commit()
    assert pr.registrar(aluno=pr.novo_aluno(2, curso_id=outro.id, periodo=9), device_id=dev(2))[0] == 200
    assert pr.registrar(aluno=pr.novo_aluno(3, curso_id=None, periodo=None), device_id=dev(3))[0] == 200


# =====================================================================================================
# BLOCO B - Geofencing (Haversine)
# =====================================================================================================
def test_ct413_no_centro_da_sala(pr):
    """CT-413 | Aluno exatamente no ponto do evento | 200, distância ~0."""
    r = pr.registrar()
    assert r[0] == 200 and r[1]["distancia_m"] < 0.5, r
    assert r[1]["aula"] == "Algoritmos" and r[1]["ja_registrado"] is False


def test_ct414_dentro_do_raio(pr):
    """CT-414 | Aluno a 30 m (raio 50 m) | 200 e distância ~30 m."""
    r = pr.registrar(norte=30)
    assert r[0] == 200 and 29.5 < r[1]["distancia_m"] < 30.5, r


def test_ct415_margem_de_1_metro_do_limite(pr):
    """CT-415 | 49 m aceita; 51 m recusa | 200 / 400."""
    assert pr.registrar(norte=49)[0] == 200
    r = pr.registrar(aluno=pr.novo_aluno(2), norte=51, device_id=dev(2))
    assert r[0] == 400, r


def test_ct416_exatamente_no_limite_e_aceito(pr):
    """CT-416 | Distância == raio (limite inclusivo) | 200."""
    lat, lon = ponto(norte=40)
    pr.aula.raio_metros = pr.ns["haversine"](lat, lon, LAT, LON)
    assert pr.registrar(norte=40)[0] == 200


def test_ct417_fora_do_raio_recusa_e_registra_tentativa(pr):
    """CT-417 | Aluno a 200 m | 400 com distância e limite na mensagem; tentativa 'fora_raio' gravada; nenhuma presença."""
    r = pr.registrar(norte=200, ip=IP_PUBLICO)
    assert r[0] == 400 and "200 m" in r[1] and "50 m" in r[1], r
    assert pr.presencas() == []
    (t,) = pr.tentativas("fora_raio")
    assert t.aluno_id == pr.aluno.id and t.aula_id == pr.aula.id and 199 < t.distancia_m < 201
    assert t.latitude is not None and t.longitude is not None and t.ip == IP_PUBLICO and t.quantidade == 1


def test_ct418_repetir_fora_do_raio_nao_incha_a_tabela(pr):
    """CT-418 | 3 tentativas fora do raio | 1 linha, quantidade 3, distância da última."""
    for d in (200, 300, 400):
        assert pr.registrar(norte=d)[0] == 400
    (t,) = pr.tentativas("fora_raio")
    assert t.quantidade == 3 and 399 < t.distancia_m < 401


def test_ct419_raio_minimo_de_5_metros(pr):
    """CT-419 | Raio de 5 m: 4 m aceita, 8 m recusa."""
    pr.aula.raio_metros = 5.0
    assert pr.registrar(norte=4)[0] == 200
    assert pr.registrar(aluno=pr.novo_aluno(2), norte=8, device_id=dev(2))[0] == 400


def test_ct420_do_outro_lado_do_mundo(pr):
    """CT-420 | Coordenada em outro continente | 400 (sem 500 nem overflow)."""
    r = pr.registrar(latitude=48.8566, longitude=2.3522)
    assert r[0] == 400 and "da sala" in r[1], r


def test_ct421_haversine_valores_conhecidos(pr):
    """CT-421 | Haversine: zero, 1° de latitude, simetria, linha de data, polo a polo."""
    h = pr.ns["haversine"]
    assert h(LAT, LON, LAT, LON) == 0
    assert abs(h(0, 0, 1, 0) - M_POR_GRAU) < 1
    assert abs(h(0, 0, 0, 1) - M_POR_GRAU) < 1                      # 1° de longitude no equador
    assert abs(h(60, 0, 60, 1) - M_POR_GRAU / 2) < 100              # em 60° de latitude, metade
    assert abs(h(-18.4157, -49.2183, -18.4, -49.0) - h(-18.4, -49.0, -18.4157, -49.2183)) < 1e-6
    assert abs(h(0, 179.9, 0, -179.9) - 0.2 * M_POR_GRAU) < 5        # atravessa a linha de data
    assert abs(h(90, 0, -90, 0) - math.pi * 6371008.8) < 1          # polo a polo = meia circunferência


def test_ct422_recusas_nao_criam_presenca(pr):
    """CT-422 | Qualquer recusa (raio, risco, GPS ruim, hora errada) deixa 0 presenças."""
    pr.registrar(norte=500)
    pr.registrar(webdriver=True)
    pr.registrar(precisao_m=900)
    pr.registrar(pos_ts=pr.agora_ms() - 10 * 60 * 1000)
    assert pr.presencas() == []


@pytest.mark.parametrize("campo,valor", [("latitude", 90.0001), ("latitude", -90.0001), ("longitude", 180.0001),
                                         ("longitude", -180.0001), ("latitude", float("nan")), ("longitude", float("inf")),
                                         ("latitude", float("-inf"))])
def test_ct423_coordenadas_invalidas_sao_422(pr, campo, valor):
    """CT-423 | Latitude/longitude fora do globo, NaN ou infinito | 422."""
    assert erro_em(pr.registrar(**{campo: valor}), campo)


def test_ct424_limites_do_globo_sao_aceitos_pelo_schema(pr):
    """CT-424 | Latitude ±90 e longitude ±180 passam no schema (a recusa vem do raio, não de um 500)."""
    for la, lo in ((90, 180), (-90, -180)):
        r = pr.registrar(latitude=la, longitude=lo)
        assert r[0] == 400, r


def test_ct490_arredondada_exige_as_duas_coordenadas(pr):
    """CT-490 | Só UMA coordenada com poucas casas decimais não é 'arredondada' (as duas precisam ser)."""
    av = pr.ns["avaliar_risco"]
    lat_curta = pr.corpo(latitude=-18.416, longitude=-49.21834567891234)
    lon_curta = pr.corpo(latitude=-18.41570123456789, longitude=-49.218)
    ambas = pr.corpo(latitude=-18.416, longitude=-49.218)
    assert av(lat_curta) == (0, []) and av(lon_curta) == (0, [])
    assert av(ambas) == (2, ["coordenadas arredondadas"])


# =====================================================================================================
# BLOCO C - Qualidade e idade da leitura de GPS
# =====================================================================================================
def test_ct425_precisao_no_limite(pr):
    """CT-425 | Precisão 150 m (limite) aceita; 150,01 m | 422 'impreciso'."""
    assert pr.registrar(precisao_m=150.0)[0] == 200
    r = pr.registrar(aluno=pr.novo_aluno(2), precisao_m=150.01, device_id=dev(2))
    assert r[0] == 422 and "impreciso" in r[1], r


def test_ct426_precisao_negativa_e_ausente(pr):
    """CT-426 | Precisão negativa | 422; ausente | aceita, com alerta 'sem precisão do GPS'."""
    assert erro_em(pr.registrar(precisao_m=-1), "precisao_m")
    assert pr.registrar(precisao_m=None)[0] == 200
    assert "sem precisão do GPS" in pr.presencas()[0].alertas


def test_ct427_precisao_irreal(pr):
    """CT-427 | Precisão 0 ou 1 m (GPS de celular não chega a isso) | alerta; 1,5 m | sem alerta."""
    assert pr.registrar(precisao_m=0)[0] == 200
    assert "precisão irreal" in pr.presencas()[-1].alertas
    assert pr.registrar(aluno=pr.novo_aluno(2), precisao_m=1, device_id=dev(2))[0] == 200
    assert "precisão irreal" in pr.presencas()[-1].alertas
    assert pr.registrar(aluno=pr.novo_aluno(3), precisao_m=1.5, device_id=dev(3))[0] == 200
    assert pr.presencas()[-1].alertas is None


def test_ct428_leitura_com_120_segundos_aceita_121_recusada(pr):
    """CT-428 | Idade da leitura: 120 s ok (limite); 121 s | 422."""
    assert pr.registrar(pos_ts=pr.agora_ms() - 120_000)[0] == 200
    r = pr.registrar(aluno=pr.novo_aluno(2), pos_ts=pr.agora_ms() - 121_000, device_id=dev(2))
    assert r[0] == 422 and "desatualizada" in r[1], r


def test_ct429_leitura_do_futuro(pr):
    """CT-429 | Relógio do aparelho adiantado: +60 s ok; +121 s | 422."""
    assert pr.registrar(pos_ts=pr.agora_ms() + 60_000)[0] == 200
    r = pr.registrar(aluno=pr.novo_aluno(2), pos_ts=pr.agora_ms() + 121_000, device_id=dev(2))
    assert r[0] == 422, r


def test_ct430_timestamp_em_segundos_e_zero(pr):
    """CT-430 | pos_ts em segundos (erro de cliente), zero ou negativo | 422."""
    for ts in (pr.agora_ms() / 1000, 0, -5):
        assert pr.registrar(pos_ts=ts)[0] == 422


@pytest.mark.parametrize("valor", [float("nan"), float("inf"), float("-inf")])
def test_ct431_pos_ts_nan_e_infinito(pr, valor):
    """CT-431 | pos_ts = NaN/Infinity (o JSON do Python aceita NaN) | 422. NaN faria 'abs(...) > limite' ser falso e pularia o teste de idade."""
    r = pr.registrar(pos_ts=valor)
    assert r[0] == 422, r
    assert pr.presencas() == []


@pytest.mark.parametrize("campo", ["precisao_m", "variacao_m"])
@pytest.mark.parametrize("valor", [float("nan"), float("inf")])
def test_ct432_precisao_e_variacao_nan_ou_infinito(pr, campo, valor):
    """CT-432 | precisao_m / variacao_m NaN ou Infinity | 422."""
    assert erro_em(pr.registrar(**{campo: valor}), campo)


def test_ct433_numero_de_amostras(pr):
    """CT-433 | amostras 0 e 51 | 422; 1 e 50 | aceitas pelo schema."""
    assert erro_em(pr.registrar(amostras=0), "amostras")
    assert erro_em(pr.registrar(amostras=51), "amostras")
    assert pr.registrar(amostras=1)[0] == 200
    assert pr.registrar(aluno=pr.novo_aluno(2), amostras=50, device_id=dev(2))[0] == 200


# =====================================================================================================
# BLOCO D - Pontuação de risco (localização falsa)
# =====================================================================================================
def test_ct434_navegador_automatizado_e_bloqueado(pr):
    """CT-434 | navigator.webdriver = true (3 pontos) | 403 e tentativa 'gps_suspeito' com o motivo."""
    r = pr.registrar(webdriver=True)
    assert r[0] == 403 and "falsificada" in r[1], r
    (t,) = pr.tentativas("gps_suspeito")
    assert "navegador automatizado" in t.detalhe and pr.presencas() == []


def test_ct435_sem_precisao_mais_coordenadas_arredondadas(pr):
    """CT-435 | Sem precisão (1) + 3 casas decimais (2) = 3 | 403."""
    r = pr.registrar(precisao_m=None, latitude=-18.416, longitude=-49.218)
    assert r[0] == 403, r
    assert "coordenadas arredondadas" in pr.tentativas("gps_suspeito")[0].detalhe


def test_ct436_precisao_irreal_mais_sem_oscilacao(pr):
    """CT-436 | Precisão irreal (2) + leituras idênticas (2) = 4 | 403."""
    r = pr.registrar(precisao_m=0.5, amostras=4, variacao_m=0)
    assert r[0] == 403, r


def test_ct437_um_sinal_so_nao_bloqueia(pr):
    """CT-437 | Cada sinal isolado (2 pontos) deixa passar, mas grava o alerta para a auditoria."""
    assert pr.registrar(precisao_m=0.5)[0] == 200
    assert "precisão irreal" in pr.presencas()[-1].alertas
    assert pr.registrar(aluno=pr.novo_aluno(2), latitude=-18.416, longitude=-49.218, device_id=dev(2))[0] == 200
    assert "coordenadas arredondadas" in pr.presencas()[-1].alertas
    assert pr.registrar(aluno=pr.novo_aluno(3), amostras=3, variacao_m=0, precisao_m=5, device_id=dev(3))[0] == 200
    assert "coordenadas sem oscilação" in pr.presencas()[-1].alertas


def test_ct438_oscilacao_so_vale_com_3_amostras_e_precisao_boa(pr):
    """CT-438 | 'Sem oscilação' exige >= 3 amostras, variação 0 e precisão < 10 m."""
    av = pr.ns["avaliar_risco"]
    base = dict(amostras=3, variacao_m=0, precisao_m=5)
    assert any("oscilação" in a for a in av(pr.corpo(**base))[1])
    assert not any("oscilação" in a for a in av(pr.corpo(**{**base, "amostras": 2}))[1])
    assert not any("oscilação" in a for a in av(pr.corpo(**{**base, "variacao_m": 0.5}))[1])
    assert not any("oscilação" in a for a in av(pr.corpo(**{**base, "variacao_m": None}))[1])
    assert not any("oscilação" in a for a in av(pr.corpo(**{**base, "precisao_m": 10}))[1])


def test_ct439_limite_de_risco_configuravel():
    """CT-439 | RISCO_MAX=2 bloqueia 2 pontos; RISCO_MAX=4 deixa passar 3."""
    c2 = CtxPr(RISCO_MAX=2)
    assert c2.registrar(precisao_m=0.5)[0] == 403
    c4 = CtxPr(RISCO_MAX=4)
    assert c4.registrar(webdriver=True)[0] == 200                   # 3 pontos < 4


def test_ct440_leitura_normal_nao_gera_alerta(pr):
    """CT-440 | Leitura típica (12 m, 5 amostras, variação 3,4 m, coordenadas com 14 casas) | 200, sem alertas."""
    r = pr.registrar(norte=11.1234567, leste=7.7654321)
    assert r[0] == 200, r
    assert pr.presencas()[0].alertas is None
    assert pr.ns["avaliar_risco"](pr.corpo(norte=3.3))[0] == 0


def test_ct441_casas_decimais(pr):
    """CT-441 | casas_decimais: 4, 1, 1 (inteiro), 14, notação científica."""
    cd = pr.ns["casas_decimais"]
    assert cd(-18.4157) == 4 and cd(-18.4) == 1 and cd(12.0) == 1
    assert cd(-18.41570000000001) == 14
    assert cd(1e-05) == 0 and cd(1.5e-05) == 0                      # notação científica: conta como 0 (irrelevante em Goiás)


def test_ct442_ordem_das_checagens(pr):
    """CT-442 | GPS impreciso vem antes do risco e do raio; risco vem antes do raio."""
    assert pr.registrar(precisao_m=500, webdriver=True, norte=900)[0] == 422
    assert pr.tentativas() == []                                     # impreciso: nem tentativa se grava
    assert pr.registrar(webdriver=True, norte=900)[0] == 403
    assert pr.registrar(aluno=pr.novo_aluno(2), norte=900, device_id=dev(2))[0] == 400


# =====================================================================================================
# BLOCO E - Deslocamento impossível entre dois check-ins
# =====================================================================================================
def test_ct443_deslocamento_plausivel(pr):
    """CT-443 | 10 km em 5 min (33 m/s, carro) | 200."""
    lat2, lon2 = ponto(norte=10_000)
    aula2 = pr.nova_aula(pr.ana, lat=lat2, lon=lon2)
    assert pr.registrar()[0] == 200
    pr.avancar(300)
    assert pr.registrar(aula=aula2, latitude=lat2, longitude=lon2, device_id=dev(2))[0] == 200


def test_ct444_deslocamento_impossivel(pr):
    """CT-444 | 10 km em 1 min (166 m/s) | 403 'deslocamento', tentativa gravada com km e minutos."""
    lat2, lon2 = ponto(norte=10_000)
    aula2 = pr.nova_aula(pr.ana, lat=lat2, lon=lon2)
    assert pr.registrar()[0] == 200
    pr.avancar(60)
    r = pr.registrar(aula=aula2, latitude=lat2, longitude=lon2, device_id=dev(2))
    assert r[0] == 403 and "Deslocamento" in r[1], r
    (t,) = pr.tentativas("deslocamento")
    assert "km" in t.detalhe and "min" in t.detalhe
    assert pr.presencas(aula2) == []


def test_ct445_ate_2_km_nunca_e_impossivel(pr):
    """CT-445 | 1,9 km em 1 s | 200 (abaixo de 2 km o erro de GPS domina)."""
    lat2, lon2 = ponto(norte=1_900)
    aula2 = pr.nova_aula(pr.ana, lat=lat2, lon=lon2)
    assert pr.registrar()[0] == 200
    pr.avancar(1)
    assert pr.registrar(aula=aula2, latitude=lat2, longitude=lon2, device_id=dev(2))[0] == 200


@pytest.mark.parametrize("metros,segundos,esperado", [(6_990, 100, 200), (7_100, 100, 403)])
def test_ct446_limite_de_velocidade(metros, segundos, esperado):
    """CT-446 | Limite de 70 m/s: 69,9 m/s passa; 71 m/s recusa."""
    c = CtxPr()
    lat2, lon2 = ponto(norte=metros)
    aula2 = c.nova_aula(c.ana, lat=lat2, lon=lon2)
    assert c.registrar()[0] == 200
    c.avancar(segundos)
    assert c.registrar(aula=aula2, latitude=lat2, longitude=lon2, device_id=dev(2))[0] == esperado


def test_ct447_depois_de_6_horas_nao_compara(pr):
    """CT-447 | Mais de 6 h desde o último check-in: o teste de velocidade não se aplica (2.000 km em 6 h e 1 s = 92 m/s)."""
    lat2, lon2 = ponto(norte=2_000_000)
    aula2 = pr.nova_aula(pr.ana, lat=lat2, lon=lon2)
    assert pr.registrar()[0] == 200
    pr.avancar(6 * 3600 + 1)
    assert pr.registrar(aula=aula2, latitude=lat2, longitude=lon2, device_id=dev(2))[0] == 200
    pr2 = CtxPr()                                                   # a mesma viagem 1 s antes das 6 h é recusada
    aula3 = pr2.nova_aula(pr2.ana, lat=lat2, lon=lon2)
    assert pr2.registrar()[0] == 200
    pr2.avancar(6 * 3600 - 1)
    assert pr2.registrar(aula=aula3, latitude=lat2, longitude=lon2, device_id=dev(2))[0] == 403


def test_ct448_primeiro_checkin_e_historico_de_outro_aluno(pr):
    """CT-448 | O deslocamento compara só com o último check-in do PRÓPRIO aluno."""
    lat2, lon2 = ponto(norte=10_000)
    aula2 = pr.nova_aula(pr.ana, lat=lat2, lon=lon2)
    assert pr.registrar()[0] == 200                                  # o aluno 1 esteve na aula 1
    pr.avancar(30)
    outro = pr.novo_aluno(2)
    assert pr.registrar(aluno=outro, aula=aula2, latitude=lat2, longitude=lon2, device_id=dev(2))[0] == 200


def test_ct489_dois_checkins_no_mesmo_instante_em_lugares_distantes(pr):
    """CT-489 | Dois check-ins do mesmo aluno com 0 s de diferença, 10 km entre eles | 403 'deslocamento' (e não um 500 por
    divisão por zero: o intervalo mínimo considerado é 1 s)."""
    lat2, lon2 = ponto(norte=10_000)
    aula2 = pr.nova_aula(pr.ana, lat=lat2, lon=lon2)
    assert pr.registrar()[0] == 200
    r = pr.registrar(aula=aula2, latitude=lat2, longitude=lon2, device_id=dev(2))
    assert r[0] == 403, r


# =====================================================================================================
# BLOCO F - VPN / proxy
# =====================================================================================================
def test_ct449_sem_chave_nao_consulta_nada(pr):
    """CT-449 | Sem PROXYCHECK_KEY a verificação é desligada: ip_e_vpn -> None e nenhuma chamada externa."""
    pr.ligar_vpn(pr.resp_proxy(IP_VPN, True))
    assert pr.ns["ip_e_vpn"](IP_VPN) is None
    assert pr.registrar(ip=IP_VPN)[0] == 200
    assert pr.chamadas_vpn == []


def test_ct450_modo_avisar_deixa_passar_e_marca():
    """CT-450 | VPN detectada, modo 'avisar' | 200 e alerta 'IP de VPN/proxy' na presença."""
    c = CtxPr(PROXYCHECK_KEY="k", VPN_MODO="avisar")
    c.ligar_vpn(c.resp_proxy(IP_VPN, True))
    assert c.registrar(ip=IP_VPN)[0] == 200
    assert "IP de VPN/proxy" in c.presencas()[0].alertas


def test_ct451_modo_bloquear_recusa_e_registra():
    """CT-451 | VPN detectada, modo 'bloquear' | 403 e tentativa 'vpn'."""
    c = CtxPr(PROXYCHECK_KEY="k", VPN_MODO="bloquear")
    c.ligar_vpn(c.resp_proxy(IP_VPN, True))
    r = c.registrar(ip=IP_VPN)
    assert r[0] == 403 and "VPN" in r[1], r
    assert len(c.tentativas("vpn")) == 1 and c.presencas() == []


def test_ct452_ip_limpo_passa_sem_alerta():
    """CT-452 | IP sem VPN | 200 sem alertas (também em modo bloquear)."""
    c = CtxPr(PROXYCHECK_KEY="k", VPN_MODO="bloquear")
    c.ligar_vpn(c.resp_proxy(IP_PUBLICO, False))
    assert c.registrar(ip=IP_PUBLICO)[0] == 200
    assert c.presencas()[0].alertas is None


def test_ct453_modo_off_com_chave_nao_consulta():
    """CT-453 | VPN_MODO=off mesmo com chave | nenhuma consulta."""
    c = CtxPr(PROXYCHECK_KEY="k", VPN_MODO="off")
    c.ligar_vpn(c.resp_proxy(IP_VPN, True))
    assert c.registrar(ip=IP_VPN)[0] == 200 and c.chamadas_vpn == []


@pytest.mark.parametrize("ip", ["10.0.0.5", "192.168.1.20", "127.0.0.1", "169.254.1.1", "::1"])
def test_ct454_ips_internos_nunca_sao_consultados(ip):
    """CT-454 | IP privado, loopback ou link-local | False, sem consulta externa."""
    c = CtxPr(PROXYCHECK_KEY="k", VPN_MODO="bloquear")
    c.ligar_vpn(c.resp_proxy(ip, True))
    assert c.ns["ip_e_vpn"](ip) is False and c.chamadas_vpn == []


def test_ct455_rede_do_campus_nunca_e_consultada():
    """CT-455 | IP dentro de IPS_PERMITIDOS | False, sem consulta (mesmo que o provedor o chamasse de VPN)."""
    c = CtxPr(PROXYCHECK_KEY="k", VPN_MODO="bloquear", IPS_PERMITIDOS="200.1.2.0/24, 177.10.20.30")
    c.ligar_vpn(c.resp_proxy("200.1.2.9", True))
    assert c.ns["ip_e_vpn"]("200.1.2.9") is False and c.ns["ip_e_vpn"]("177.10.20.30") is False
    assert c.chamadas_vpn == []


def test_ct456_provedor_fora_do_ar_nao_bloqueia_ninguem():
    """CT-456 | Erro de rede / timeout / resposta com erro / lixo | None e o check-in segue (modo bloquear)."""
    for falha in (dict(erro=OSError("timeout")), dict(resposta={"status": "error", "message": "chave inválida"}),
                  dict(resposta={"status": "ok"}), dict(resposta=["lixo"])):
        c = CtxPr(PROXYCHECK_KEY="k", VPN_MODO="bloquear")
        c.ligar_vpn(**falha)
        assert c.ns["ip_e_vpn"](IP_PUBLICO) is None, falha
        assert c.registrar(ip=IP_PUBLICO)[0] == 200, falha


def test_ct457_resposta_positiva_e_cacheada_por_6_horas():
    """CT-457 | A mesma resposta é reaproveitada por 6 h (1 consulta); depois, consulta de novo."""
    c = CtxPr(PROXYCHECK_KEY="k", VPN_MODO="avisar")
    c.ligar_vpn(c.resp_proxy(IP_PUBLICO, False))
    for _ in range(5):
        c.ns["ip_e_vpn"](IP_PUBLICO)
    assert len(c.chamadas_vpn) == 1
    c.avancar(6 * 3600 - 10)
    c.ns["ip_e_vpn"](IP_PUBLICO)
    assert len(c.chamadas_vpn) == 1
    c.avancar(20)
    c.ns["ip_e_vpn"](IP_PUBLICO)
    assert len(c.chamadas_vpn) == 2


def test_ct458_falha_do_provedor_nao_se_repete_a_cada_aluno():
    """CT-458 | Provedor fora do ar: depois da 1ª falha o app NÃO pode esperar 2,5 s a cada check-in
    (cada espera segura uma conexão do banco e uma thread). Deve lembrar a falha por um tempo curto."""
    c = CtxPr(PROXYCHECK_KEY="k", VPN_MODO="avisar")
    c.ligar_vpn(erro=OSError("fora do ar"))
    for i in range(20):
        assert c.registrar(aluno=c.novo_aluno(10 + i), ip=IP_PUBLICO, device_id=dev(10 + i))[0] == 200
    assert len(c.chamadas_vpn) <= 2, f"{len(c.chamadas_vpn)} consultas ao provedor fora do ar"
    c.avancar(300)                                                   # passado um tempo, tenta de novo
    c.ligar_vpn(c.resp_proxy(IP_PUBLICO, True))
    assert c.ns["ip_e_vpn"](IP_PUBLICO) is True


def test_ct459_ip_invalido_ou_vazio():
    """CT-459 | IP vazio / 'unknown' / lixo | None, sem consulta."""
    c = CtxPr(PROXYCHECK_KEY="k")
    c.ligar_vpn(c.resp_proxy(IP_VPN, True))
    for ip in ("", "unknown", "999.1.1.1", "abc"):
        assert c.ns["ip_e_vpn"](ip) is None
    assert c.chamadas_vpn == []


def test_ct460_vpn_so_e_consultada_depois_das_outras_checagens():
    """CT-460 | Aluno fora do raio ou com GPS suspeito não gasta consulta externa."""
    c = CtxPr(PROXYCHECK_KEY="k", VPN_MODO="bloquear")
    c.ligar_vpn(c.resp_proxy(IP_VPN, True))
    assert c.registrar(ip=IP_VPN, norte=500)[0] == 400
    assert c.registrar(ip=IP_VPN, webdriver=True)[0] == 403
    assert c.chamadas_vpn == []


def test_ct461_chave_do_provedor_so_vai_na_url_https():
    """CT-461 | A consulta usa HTTPS, timeout curto e não vaza a chave em mensagens de erro ao aluno."""
    c = CtxPr(PROXYCHECK_KEY="chave-secreta-123", VPN_MODO="bloquear")
    c.ligar_vpn(erro=OSError("falha com chave-secreta-123"))
    r = c.registrar(ip=IP_PUBLICO)
    assert r[0] == 200 and "chave-secreta-123" not in str(r)
    assert c.chamadas_vpn and c.chamadas_vpn[0].startswith("https://")
    fonte = _ast.get_source_segment(APP.read_text(encoding="utf-8"),
                                    next(n for n in _ast.parse(APP.read_text(encoding="utf-8")).body
                                         if isinstance(n, _ast.FunctionDef) and n.name == "ip_e_vpn"))
    assert "timeout=" in fonte


# =====================================================================================================
# BLOCO G - IP do aluno atrás do proxy da hospedagem
# =====================================================================================================
def test_ct462_um_proxy_confia_so_no_ultimo_ip_da_lista():
    """CT-462 | PROXY_HOPS=1: o aluno forja o início do X-Forwarded-For, mas vale o último (o que o proxy anexou)."""
    c = CtxPr(PROXY_HOPS=1)
    ip = c.ns["ip_cliente"]
    assert ip(FakeReq("10.0.0.1", xff="9.9.9.9, 177.10.20.30")) == "177.10.20.30"
    assert ip(FakeReq("10.0.0.1", xff="177.10.20.30")) == "177.10.20.30"


def test_ct463_dois_proxies_e_lista_curta():
    """CT-463 | PROXY_HOPS=2 usa o penúltimo; hops maiores que a lista usam o primeiro."""
    c = CtxPr(PROXY_HOPS=2)
    ip = c.ns["ip_cliente"]
    assert ip(FakeReq("10.0.0.1", xff="9.9.9.9, 177.10.20.30, 10.1.1.1")) == "177.10.20.30"
    assert ip(FakeReq("10.0.0.1", xff="177.10.20.30")) == "177.10.20.30"


def test_ct464_sem_cabecalho_ou_sem_proxy():
    """CT-464 | Sem X-Forwarded-For (ou PROXY_HOPS=0) vale o IP da conexão; sem conexão, string vazia."""
    ip = CtxPr(PROXY_HOPS=1).ns["ip_cliente"]
    assert ip(FakeReq("177.10.20.30")) == "177.10.20.30"
    assert ip(FakeReq(client=False)) == ""
    assert ip(FakeReq("177.10.20.30", xff=" , ")) == "177.10.20.30"
    assert CtxPr(PROXY_HOPS=0).ns["ip_cliente"](FakeReq("177.10.20.30", xff="9.9.9.9")) == "177.10.20.30"


def test_ct465_presenca_grava_o_ip_real_e_nao_o_forjado(pr):
    """CT-465 | O IP gravado na presença é o do proxy confiável, não o que o aluno escreveu."""
    assert pr.registrar(ip="10.0.0.1", xff="1.2.3.4, 177.10.20.30")[0] == 200
    assert pr.presencas()[0].ip == "177.10.20.30"


# =====================================================================================================
# BLOCO H - Duplicidade: aluno, aparelho e corridas
# =====================================================================================================
def test_ct466_duplo_clique_e_idempotente(pr):
    """CT-466 | O mesmo aluno envia 2 vezes | 200 nas duas, 'ja_registrado' na segunda, 1 só linha."""
    a = pr.registrar()
    b = pr.registrar()
    assert a[0] == b[0] == 200 and a[1]["ja_registrado"] is False and b[1]["ja_registrado"] is True
    assert len(pr.presencas()) == 1


def test_ct467_dois_alunos_no_mesmo_aparelho(pr):
    """CT-467 | Aluno B usa o mesmo aparelho do aluno A na mesma aula | 409; tentativa 'aparelho_repetido' cita o aluno A
    (só para o professor), mas a resposta ao aluno não revela o nome."""
    assert pr.registrar()[0] == 200
    b = pr.novo_aluno(2, nome="Beatriz Prado")
    r = pr.registrar(aluno=b)
    assert r[0] == 409 and "outro aluno" in r[1] and "Aluno 1" not in r[1], r
    (t,) = pr.tentativas("aparelho_repetido")
    assert t.aluno_id == b.id and "Aluno 1" in t.detalhe
    assert len(pr.presencas()) == 1


def test_ct468_mesmo_aparelho_em_aulas_diferentes(pr):
    """CT-468 | O mesmo aparelho em aulas diferentes (outro dia/outra turma) | 200."""
    outra = pr.nova_aula(pr.ana)
    assert pr.registrar()[0] == 200
    assert pr.registrar(aluno=pr.novo_aluno(2), aula=outra)[0] == 200


def test_ct469_corrida_duplo_clique(pr):
    """CT-469 | 2 envios do mesmo aluno quase juntos: o outro comita no meio | 200 'ja_registrado', 1 linha, sem 500."""
    def outro_envio():
        s2 = pr.sessao_paralela()
        pr.registrar(db=s2)                                          # o envio concorrente vence o INSERT
    pr.db.antes_do_commit = outro_envio
    r = pr.registrar()
    assert r[0] == 200 and r[1]["ja_registrado"] is True, r
    assert len(pr.presencas()) == 1 and pr.tentativas() == []


def test_ct470_corrida_aparelho_repetido(pr):
    """CT-470 | Dois alunos, mesmo aparelho, ao mesmo tempo: o do outro comita antes | 409 e 1 presença."""
    b = pr.novo_aluno(2)

    def outro_envio():
        assert pr.registrar(aluno=b, db=pr.sessao_paralela())[0] == 200
    pr.db.antes_do_commit = outro_envio
    r = pr.registrar()
    assert r[0] == 409, r
    assert len(pr.presencas()) == 1 and len(pr.tentativas("aparelho_repetido")) == 1


@pytest.mark.parametrize("device", ["a" * 15, "x" * 65, "com_underline_0123456789", "com espaco 0123456789ab",
                                    "valido-0123456789ab\n", "acentuação-0123456789", "../../etc/passwd-012345"])
def test_ct471_device_id_invalido(pr, device):
    """CT-471 | device_id fora de [A-Za-z0-9-]{16,64} (curto, longo, símbolos, quebra de linha, acento) | 422."""
    assert erro_em(pr.registrar(device_id=device), "device_id")


def test_ct472_device_id_nos_limites(pr):
    """CT-472 | device_id com 16 e 64 caracteres, UUID e hífens | aceitos."""
    assert pr.registrar(device_id="a" * 16)[0] == 200
    assert pr.registrar(aluno=pr.novo_aluno(2), device_id="B" * 64)[0] == 200
    assert pr.registrar(aluno=pr.novo_aluno(3), device_id="----------------")[0] == 200


@pytest.mark.parametrize("fp", ["ABCDEF0123456789", "abc", "g" * 16, "a" * 65, "abcdef01 23456789"])
def test_ct473_fp_hash_invalido(pr, fp):
    """CT-473 | fp_hash fora de [a-f0-9]{8,64} (maiúsculo, curto, não-hex, longo) | 422; ausente | aceito."""
    assert erro_em(pr.registrar(fp_hash=fp), "fp_hash")


def test_ct474_fp_hash_ausente_e_aceito(pr):
    """CT-474 | Navegador sem canvas/SubtleCrypto manda fp_hash nulo | 200."""
    assert pr.registrar(fp_hash=None)[0] == 200


def test_ct475_aula_excluida_no_meio_do_checkin(pr):
    """CT-475 | O professor exclui o evento entre a checagem e o INSERT (FOREIGN KEY falha) | o aluno recebe 'aula não
    encontrada'/'encerrada', e NÃO a mensagem enganosa de 'aparelho já usado por outro aluno'."""
    aula = pr.aula
    pr.db.antes_do_commit = lambda: pr.db.tables[Aula].remove(aula)
    r = pr.registrar()
    assert r[0] in (404, 409), r
    assert "aparelho" not in str(r[1]).lower(), r
    assert pr.tentativas("aparelho_repetido") == []


def test_ct476_reenvio_de_quem_ja_esta_presente_nao_gera_tentativa(pr):
    """CT-476 | Aluno já presente reenvia (duplo toque, aba velha) com GPS ruim / longe | 200 'ja_registrado' e NENHUMA
    tentativa recusada (senão o painel mostra 'fora do raio' para quem está presente)."""
    assert pr.registrar()[0] == 200
    for over in (dict(norte=500), dict(precisao_m=900), dict(webdriver=True)):
        r = pr.registrar(**over)
        assert r[0] == 200 and r[1]["ja_registrado"] is True, (over, r)
    assert pr.tentativas() == [] and len(pr.presencas()) == 1


def test_ct477_reenvio_devolve_a_distancia_gravada(pr):
    """CT-477 | O reenvio informa a distância da presença já registrada (não uma nova medição)."""
    a = pr.registrar(norte=30)
    b = pr.registrar(norte=10)
    assert b[1]["ja_registrado"] is True and b[1]["distancia_m"] == a[1]["distancia_m"]


def test_ct478_reenvio_depois_de_encerrada_continua_409(pr):
    """CT-478 | Chamada encerrada vale para todos, inclusive quem já registrou (o comprovante fica em /aluno/presencas)."""
    assert pr.registrar()[0] == 200
    pr.aula.ativa = False
    assert pr.registrar()[0] == 409


def test_ct479_reenvio_de_aluno_que_virou_inativo(pr):
    """CT-479 | Aluno desativado depois de registrar: o reenvio continua 403 (inativo vale antes de tudo)."""
    assert pr.registrar()[0] == 200
    pr.aluno.status = "inativo"
    assert pr.registrar()[0] == 403


# =====================================================================================================
# BLOCO I - O que fica gravado
# =====================================================================================================
def test_ct480_linha_da_presenca_completa(pr):
    """CT-480 | A presença guarda posição, distância, precisão, aparelho, hash, IP e horário."""
    r = pr.registrar(norte=20, leste=10, precisao_m=8.5)
    p = pr.presencas()[0]
    assert r[0] == 200 and p.aluno_id == pr.aluno.id and p.aula_id == pr.aula.id
    assert abs(p.distancia_m - pr.ns["haversine"](p.latitude, p.longitude, LAT, LON)) < 1e-6
    assert p.precisao_m == 8.5 and p.device_id == DEV and p.fp_hash == "abcdef0123456789" and p.ip == IP_PUBLICO
    assert p.alertas is None and p.registrado_em == pr.agora


def test_ct481_resposta_arredonda_a_distancia_para_1_casa(pr):
    """CT-481 | distancia_m na resposta tem 1 casa decimal."""
    r = pr.registrar(norte=12.3456789)
    assert r[1]["distancia_m"] == round(r[1]["distancia_m"], 1)


def test_ct482_campos_gravados_respeitam_o_tamanho_da_coluna():
    """CT-482 | alertas <= 200 e ip <= 64 caracteres (colunas String(200)/String(64))."""
    c = CtxPr()
    c.registrar(ip="2001:db8:85a3:0000:0000:8a2e:0370:7334:aaaa:bbbb:cccc:dddd:eeee:ffff:0000", precisao_m=0.5)
    p = c.presencas()[0]
    assert len(p.ip) <= 64 and len(p.alertas or "") <= 200
    c.db.tables[Tentativa].clear()
    c.registrar(aluno=c.novo_aluno(2), norte=900, ip="2001:db8:85a3:0000:0000:8a2e:0370:7334:aaaa:bbbb:cccc:dddd:eeee:ffff:0000",
                device_id=dev(2))
    assert len(c.tentativas()[0].ip) <= 64


def test_ct483_falha_ao_gravar_tentativa_nao_vira_500(pr):
    """CT-483 | Erro de banco ao registrar a tentativa recusada: o aluno ainda recebe o 400 correto (não um 500)."""
    class DBQuebrado(FakeDB):
        def commit(self):
            raise RuntimeError("banco indisponível")
    db = DBQuebrado(tables=pr.db.tables)
    r = pr.registrar(norte=500, db=db)
    assert r[0] == 400, r


def test_ct484_tentativas_de_motivos_diferentes_sao_linhas_diferentes(pr):
    """CT-484 | fora_raio e gps_suspeito do mesmo aluno/aula são linhas separadas (UNIQUE inclui o motivo)."""
    pr.registrar(norte=500)
    pr.registrar(webdriver=True)
    assert sorted(t.motivo for t in pr.tentativas()) == ["fora_raio", "gps_suspeito"]


def test_ct485_detalhe_da_tentativa_cabe_na_coluna(pr):
    """CT-485 | Tentativa com detalhe enorme é cortada em 200 caracteres."""
    pr.ns["registrar_tentativa"](pr.db, pr.aula.id, pr.aluno.id, "vpn", "x" * 500, None, None, "1.2.3.4")
    assert len(pr.tentativas("vpn")[0].detalhe) == 200


# =====================================================================================================
# BLOCO J - Volume (turma inteira) e consistência do código
# =====================================================================================================
def test_ct486_turma_de_300_alunos_cada_um_no_seu_aparelho():
    """CT-486 | 300 alunos registram na mesma aula, cada um com o seu aparelho | 300 presenças, 0 recusas, 0 erros."""
    c = CtxPr()
    for i in range(2, 302):
        r = c.registrar(aluno=c.novo_aluno(i), device_id=dev(i), norte=(i % 40), leste=(i % 17), ip=f"177.{i % 200}.1.{i % 250}")
        assert r[0] == 200 and r[1]["ja_registrado"] is False, (i, r)
    assert len(c.presencas()) == 300 and c.tentativas() == []


def test_ct487_cinco_alunos_um_aparelho_so_o_primeiro_passa():
    """CT-487 | 5 alunos no mesmo celular | 1 presença, 4 recusas 409 e 4 tentativas 'aparelho_repetido'."""
    c = CtxPr()
    resultados = [c.registrar(aluno=c.novo_aluno(i)) for i in range(2, 7)]
    assert [r[0] for r in resultados] == [200, 409, 409, 409, 409]
    assert len(c.presencas()) == 1 and len(c.tentativas("aparelho_repetido")) == 4


def test_ct488_a_ordem_das_etapas_no_codigo():
    """CT-488 | Guarda de regressão: a ordem real das etapas é permissões -> GPS -> risco -> raio -> deslocamento -> VPN -> INSERT."""
    arvore = _ast.parse(APP.read_text(encoding="utf-8"))
    fn = next(n for n in arvore.body if isinstance(n, _ast.FunctionDef) and n.name == "registrar_presenca")
    src = _ast.get_source_segment(APP.read_text(encoding="utf-8"), fn)
    ordem = ["aula_aberta(aula)", "al.status", "PRECISAO_MAX_M", "IDADE_MAX_POSICAO_S", "avaliar_risco(b)", "haversine(b.latitude",
             "VELOCIDADE_MAX_MS", "ip_e_vpn(ip)", "db.add(Presenca("]
    pos = [src.index(t) for t in ordem]
    assert pos == sorted(pos), dict(zip(ordem, pos))
    assert "so_aluno" in src
