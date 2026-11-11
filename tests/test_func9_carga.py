# -*- coding: utf-8 -*-
"""
QA - Funcionalidade 9: CARGA (5.000 alunos) - parte do código (Guará Check)
Aqui ficam os testes das correções que a análise de carga pediu. A parte que usa o banco de verdade (PostgreSQL 16,
EXPLAIN ANALYZE e pgbench) está em tests/carga/ (rodar_carga.sh). Como nas outras funcionalidades, o código REAL do app.py
é extraído por ast e executado contra o banco em memória (harness_app.py).

Defeitos que estes testes travam:
  - a consulta de VPN (até 2,5 s) segurava uma das 40 conexões do banco: com o provedor lento, 5.000 check-ins levariam 5 min
    e quem esperasse mais de 10 s pela conexão tomava erro 500;
  - com o provedor fora do ar, cada aluno em dados móveis (um IP diferente) esperava 2,5 s: não havia disjuntor, só cache por IP;
  - os freios de tentativa eram APAGADOS por inteiro quando passavam de 5.000 chaves (uma turma grande faz isso sozinha),
    o que devolvia todas as tentativas a quem estava travado por tentar senhas;
  - a lista de aulas da coordenação contava as presenças de TODAS as aulas antes de paginar (163 ms hoje, cresce a cada semestre).

Uso:  pip install pytest pydantic pyjwt openpyxl reportlab pypdf
      pytest test_func9_carga.py -v
"""
import time as _tempo

from harness_app import *  # noqa: F401,F403
from test_func3_eventos import CtxEv
from test_func4_presenca import CtxPr, IP_PUBLICO, dev


def ips(n):
    """n IPs públicos diferentes (cada aluno em dados móveis tem o seu)."""
    return [f"177.{10 + i // 250}.{i % 250}.{(i * 7) % 250 + 1}" for i in range(n)]


def gravar_ordem(c):
    """Registra, em ordem, os COMMITs do banco e as consultas externas de VPN, para ver quem vem antes de quem."""
    ordem = []
    commit = c.db.commit

    def commit_gravando():
        ordem.append("commit")
        return commit()
    c.db.commit = commit_gravando
    return ordem


# =====================================================================================================
# BLOCO A - a consulta de VPN não pode prender a conexão do banco
# =====================================================================================================
def test_ct1001_conexao_e_solta_antes_da_consulta_de_vpn():
    """CT-1001 | Check-in com consulta externa | O banco recebe COMMIT (que devolve a conexão ao pool) ANTES de a consulta
    sair, e o INSERT da presença vem depois: ['commit', 'consulta', 'commit']."""
    c = CtxPr(PROXYCHECK_KEY="k", VPN_MODO="bloquear")
    ordem = gravar_ordem(c)
    resp = c.resp_proxy(IP_PUBLICO, False)

    def urlopen(url, timeout=None):
        ordem.append("consulta")
        return io.BytesIO(json.dumps(resp).encode())
    c.ns["urllib"] = types.SimpleNamespace(request=types.SimpleNamespace(urlopen=urlopen))
    assert c.registrar(ip=IP_PUBLICO)[0] == 200
    assert ordem == ["commit", "consulta", "commit"], ordem
    assert len(c.presencas()) == 1


def test_ct1002_resposta_em_cache_nao_faz_commit_extra():
    """CT-1002 | IP já consultado (cache de 6 h) | nenhuma consulta e um único COMMIT (o do INSERT): sem viagem inútil ao banco."""
    c = CtxPr(PROXYCHECK_KEY="k", VPN_MODO="bloquear")
    c.ligar_vpn(c.resp_proxy(IP_PUBLICO, False))
    assert c.registrar(ip=IP_PUBLICO)[0] == 200                       # 1º aluno: consulta de verdade
    al2 = c.novo_aluno(2)
    ordem = gravar_ordem(c)
    assert c.registrar(aluno=al2, ip=IP_PUBLICO, device_id=dev(2))[0] == 200
    assert len(c.chamadas_vpn) == 1 and ordem == ["commit"], (c.chamadas_vpn, ordem)


def test_ct1003_sem_consulta_nao_ha_commit_extra():
    """CT-1003 | VPN desligada, IP da rede do campus e IP privado | nenhum COMMIT antes do INSERT."""
    for env, ip in ((dict(VPN_MODO="off", PROXYCHECK_KEY="k"), IP_PUBLICO),
                    (dict(VPN_MODO="bloquear", PROXYCHECK_KEY="k", IPS_PERMITIDOS="177.0.0.0/8"), "177.9.9.9"),
                    (dict(VPN_MODO="bloquear", PROXYCHECK_KEY="k"), "10.1.2.3"),
                    (dict(VPN_MODO="bloquear"), IP_PUBLICO)):
        c = CtxPr(**env)
        ordem = gravar_ordem(c)
        assert c.registrar(ip=ip)[0] == 200
        assert ordem == ["commit"], (env, ip, ordem)


def test_ct1004_dados_da_aula_continuam_validos_depois_do_commit():
    """CT-1004 | Depois de soltar a conexão, a resposta ainda traz o título da aula e a distância (sem reconsultar o banco)."""
    c = CtxPr(PROXYCHECK_KEY="k", VPN_MODO="bloquear")
    c.ligar_vpn(c.resp_proxy(IP_PUBLICO, False))
    r = c.registrar(ip=IP_PUBLICO)
    assert r[0] == 200 and r[1]["aula"] == "Algoritmos" and r[1]["ja_registrado"] is False and "distancia_m" in r[1], r


def test_ct1005_vpn_detectada_ainda_bloqueia_e_registra_a_tentativa():
    """CT-1005 | A mudança não afrouxou a regra: VPN em modo bloquear = 403 + tentativa 'vpn', sem presença."""
    c = CtxPr(PROXYCHECK_KEY="k", VPN_MODO="bloquear")
    c.ligar_vpn(c.resp_proxy(IP_PUBLICO, True))
    r = c.registrar(ip=IP_PUBLICO)
    assert r[0] == 403 and len(c.tentativas("vpn")) == 1 and c.presencas() == [], r


# =====================================================================================================
# BLOCO B - disjuntor do provedor de VPN
# =====================================================================================================
def test_ct1010_provedor_fora_do_ar_para_de_ser_consultado_para_ips_diferentes():
    """CT-1010 | 40 alunos, 40 IPs diferentes, provedor fora do ar | no máximo 5 consultas (não 40 x 2,5 s) e todos entram."""
    c = CtxPr(PROXYCHECK_KEY="k", VPN_MODO="bloquear")
    c.ligar_vpn(erro=OSError("timeout"))
    for i, ip in enumerate(ips(40)):
        assert c.registrar(aluno=c.novo_aluno(10 + i), ip=ip, device_id=dev(10 + i))[0] == 200
    assert len(c.chamadas_vpn) == 5, len(c.chamadas_vpn)
    assert len(c.presencas()) == 40


def test_ct1011_disjuntor_nao_libera_commit_a_toa():
    """CT-1011 | Com o disjuntor aberto, também não há COMMIT extra antes do INSERT."""
    c = CtxPr(PROXYCHECK_KEY="k", VPN_MODO="bloquear")
    c.ligar_vpn(erro=OSError("timeout"))
    for i, ip in enumerate(ips(5)):
        c.ns["ip_e_vpn"](ip)
    al50 = c.novo_aluno(50)
    ordem = gravar_ordem(c)
    assert c.registrar(aluno=al50, ip="177.99.1.1", device_id=dev(50))[0] == 200
    assert ordem == ["commit"] and len(c.chamadas_vpn) == 5


def test_ct1012_passada_a_pausa_tenta_de_novo_e_uma_falha_basta_para_pausar():
    """CT-1012 | Depois de 60 s o provedor é testado outra vez; se ainda estiver fora, UMA falha reabre a pausa."""
    c = CtxPr(PROXYCHECK_KEY="k", VPN_MODO="bloquear")
    c.ligar_vpn(erro=OSError("timeout"))
    for ip in ips(8):
        c.ns["ip_e_vpn"](ip)
    assert len(c.chamadas_vpn) == 5
    c.avancar(59)
    c.ns["ip_e_vpn"]("177.50.1.1")
    assert len(c.chamadas_vpn) == 5                                   # ainda em pausa
    c.avancar(2)                                                      # 61 s depois
    c.ns["ip_e_vpn"]("177.50.1.2")
    assert len(c.chamadas_vpn) == 6                                   # tentou uma vez
    for ip in ips(8)[3:]:
        c.ns["ip_e_vpn"]("177.60." + ip.split(".", 2)[2])
    assert len(c.chamadas_vpn) == 6, "uma falha na tentativa de volta já devia ter reaberto a pausa"


def test_ct1013_provedor_volta_e_o_ip_nao_fica_preso_como_nao_verificado():
    """CT-1013 | Um IP consultado durante a pausa não é guardado como falha: quando o provedor volta ele é verificado na hora."""
    c = CtxPr(PROXYCHECK_KEY="k", VPN_MODO="bloquear")
    c.ligar_vpn(erro=OSError("timeout"))
    for ip in ips(5):
        c.ns["ip_e_vpn"](ip)
    assert c.ns["ip_e_vpn"]("177.77.7.7") is None                     # durante a pausa
    assert "177.77.7.7" not in c.ns["_cache_ip"]
    c.avancar(61)
    c.ligar_vpn(c.resp_proxy("177.77.7.7", True))                     # provedor voltou
    assert c.ns["ip_e_vpn"]("177.77.7.7") is True


def test_ct1014_resposta_boa_zera_a_contagem_de_falhas():
    """CT-1014 | 4 falhas, 1 acerto, 4 falhas | o disjuntor NÃO abre (as falhas têm de ser seguidas)."""
    c = CtxPr(PROXYCHECK_KEY="k", VPN_MODO="bloquear")
    n = iter(range(100))
    c.ligar_vpn(erro=OSError("x"))
    for _ in range(4):
        c.ns["ip_e_vpn"](ips(100)[next(n)])
    c.ligar_vpn(c.resp_proxy(ips(100)[50], False))
    assert c.ns["ip_e_vpn"](ips(100)[50]) is False
    c.chamadas_vpn.clear()
    c.ligar_vpn(erro=OSError("x"))
    for _ in range(4):
        c.ns["ip_e_vpn"](ips(100)[10 + next(n)])
    assert len(c.chamadas_vpn) == 4 and c.ns["_vpn_disjuntor"]["aberto_ate"] == 0.0


def test_ct1015_ninguem_e_bloqueado_com_o_disjuntor_aberto():
    """CT-1015 | Modo 'bloquear' com provedor fora do ar | todos entram (o disjuntor nunca vira um bloqueio)."""
    c = CtxPr(PROXYCHECK_KEY="k", VPN_MODO="bloquear")
    c.ligar_vpn(erro=OSError("timeout"))
    r = [c.registrar(aluno=c.novo_aluno(100 + i), ip=ip, device_id=dev(100 + i))[0] for i, ip in enumerate(ips(12))]
    assert r == [200] * 12 and c.tentativas("vpn") == []


def test_ct1016_ip_ja_conhecido_continua_valendo_com_o_disjuntor_aberto():
    """CT-1016 | Um IP que o provedor já chamou de VPN continua bloqueado mesmo com o disjuntor aberto (o cache vem antes)."""
    c = CtxPr(PROXYCHECK_KEY="k", VPN_MODO="bloquear")
    c.ligar_vpn(c.resp_proxy("177.1.1.1", True))
    assert c.ns["ip_e_vpn"]("177.1.1.1") is True
    c.ligar_vpn(erro=OSError("timeout"))
    for ip in ips(8)[1:]:
        c.ns["ip_e_vpn"](ip)
    assert c.ns["ip_e_vpn"]("177.1.1.1") is True


def test_ct1017_resposta_com_erro_do_provedor_tambem_conta_como_falha():
    """CT-1017 | Cota esgotada / chave errada ('status: error', 'denied') | conta para o disjuntor, como um timeout."""
    c = CtxPr(PROXYCHECK_KEY="k", VPN_MODO="bloquear")
    c.ligar_vpn(resposta={"status": "error", "message": "limite diário"})
    for ip in ips(30):
        assert c.ns["ip_e_vpn"](ip) is None
    assert len(c.chamadas_vpn) == 5


def test_ct1018_sem_chave_o_disjuntor_nem_e_tocado():
    """CT-1018 | Sem PROXYCHECK_KEY | nenhuma consulta, nenhuma falha contada."""
    c = CtxPr(VPN_MODO="bloquear")
    c.ligar_vpn(erro=OSError("x"))
    for ip in ips(10):
        assert c.ns["ip_e_vpn"](ip) is None
    assert c.chamadas_vpn == [] and c.ns["_vpn_disjuntor"]["falhas"] == 0


# =====================================================================================================
# BLOCO C - freios de tentativa não são zerados por volume
# =====================================================================================================
def _travar_ip(c, ip, vezes=30):
    for _ in range(vezes):
        c.ns["registrar_falha"]("lg-ip:" + ip)


def _esta_travado(c, ip):
    try:
        c.ns["checar_falhas"]("lg-ip:" + ip, 30, 600)
        return False
    except HTTPException as e:
        return e.status_code == 429


def test_ct1020_travar_por_senha_errada_sobrevive_a_uma_turma_grande():
    """CT-1020 | Um IP travado (30 falhas) + 6.000 alunos diferentes usando os freios de PDF/anotação | o IP continua travado.
    (Antes, passar de 5.000 chaves apagava o dicionário inteiro e destravava todo mundo.)"""
    c = CtxPr()
    _travar_ip(c, "200.9.9.9")
    assert _esta_travado(c, "200.9.9.9")
    for i in range(6000):
        c.ns["limitar_tentativas"](f"pdf:{i}", 30, 600)
    assert _esta_travado(c, "200.9.9.9"), "o freio de força bruta foi zerado pelo volume de alunos"


def test_ct1021_travar_sobrevive_tambem_com_muitas_falhas_de_login_de_outros():
    """CT-1021 | 6.000 falhas de login de IPs diferentes (ataque distribuído) | o IP já travado continua travado."""
    c = CtxPr()
    _travar_ip(c, "200.9.9.9")
    for i in range(6000):
        c.ns["registrar_falha"](f"lg-ip:9.{i // 250}.{i % 250}.1")
    assert _esta_travado(c, "200.9.9.9")


def test_ct1022_o_que_venceu_sai_primeiro():
    """CT-1022 | 6.000 chaves vencidas (há mais de 1 h) + 1 viva | a poda tira só as vencidas; a viva fica."""
    c = CtxPr()
    for i in range(6000):
        c.ns["_tentativas"][f"velha:{i}"] = [c.ns["time"].monotonic()]
    c.avancar(3700)
    c.ns["registrar_falha"]("viva")
    t = c.ns["_tentativas"]
    assert "viva" in t and len(t) == 1, len(t)


def test_ct1023_vencimento_respeita_a_maior_janela():
    """CT-1023 | Chave de 50 min (janela de cadastro de 1 h ainda vale) | não pode ser podada como vencida."""
    c = CtxPr()
    for i in range(5001):
        c.ns["_tentativas"][f"x:{i}"] = [c.ns["time"].monotonic()]
    c.ns["_tentativas"]["cad:200.1.1.1"] = [c.ns["time"].monotonic()]
    c.avancar(50 * 60)
    c.ns["limitar_tentativas"]("qualquer", 5, 600)
    assert "cad:200.1.1.1" in c.ns["_tentativas"]


def test_ct1024_tamanho_do_dicionario_tem_teto():
    """CT-1024 | 30.000 chaves vivas | o dicionário nunca passa do teto (memória limitada) e as mais NOVAS ficam."""
    c = CtxPr()
    teto = c.ns["TENTATIVAS_MAX_CHAVES"]
    maior = 0
    for i in range(30000):
        c.ns["limitar_tentativas"](f"k:{i}", 30, 600)
        c.avancar(0.001)
        maior = max(maior, len(c.ns["_tentativas"]))
    t = c.ns["_tentativas"]
    assert maior <= teto + 1, maior
    assert "k:29999" in t and "k:29000" in t and "k:0" not in t


def test_ct1025_poda_nao_e_quadratica():
    """CT-1025 | 50.000 chaves vivas de uma vez | termina em menos de 3 s (a poda é amortizada, não roda a cada chamada)."""
    c = CtxPr()
    t0 = _tempo.perf_counter()
    for i in range(50000):
        c.ns["limitar_tentativas"](f"k:{i}", 30, 600)
    dt = _tempo.perf_counter() - t0
    print(f"\n[F9] 50.000 chaves nos freios: {dt:.2f}s, dicionário final com {len(c.ns['_tentativas'])} chaves")
    assert dt < 3.0, dt


def test_ct1026_freios_continuam_contando_e_travando_normalmente():
    """CT-1026 | Sem pressão de tamanho, o freio funciona como sempre: 5 usos passam, o 6º recebe 429; depois da janela, libera."""
    c = CtxPr()
    for _ in range(5):
        c.ns["limitar_tentativas"]("a", 5, 600)
    with pytest.raises(HTTPException) as e:
        c.ns["limitar_tentativas"]("a", 5, 600)
    assert e.value.status_code == 429
    c.avancar(601)
    c.ns["limitar_tentativas"]("a", 5, 600)


def test_ct1028_falhas_de_login_tambem_tem_teto_de_tamanho():
    """CT-1028 | 30.000 falhas de login de IPs diferentes | o dicionário também fica limitado (registrar_falha poda como limitar_tentativas)."""
    c = CtxPr()
    teto = c.ns["TENTATIVAS_MAX_CHAVES"]
    maior = 0
    for i in range(30000):
        c.ns["registrar_falha"](f"lg-ip:9.{i // 62500}.{i // 250 % 250}.{i % 250}")
        c.avancar(0.001)
        maior = max(maior, len(c.ns["_tentativas"]))
    assert maior <= teto + 1, maior


def test_ct1027_login_de_turma_inteira_pela_mesma_rede_nao_trava():
    """CT-1027 | 5.000 logins CORRETOS pelo mesmo IP do campus | só falhas contam: ninguém é travado."""
    c = CtxPr()
    for i in range(5000):
        c.ns["checar_falhas"]("lg-ip:200.1.1.1", 30, 600)
        c.ns["checar_falhas"](f"lg-em:aluno:a{i}@x|200.1.1.1", 8, 600)
        c.ns["esquecer_falhas"](f"lg-em:aluno:a{i}@x|200.1.1.1")


# =====================================================================================================
# BLOCO D - lista de aulas da coordenação: contar só a página
# =====================================================================================================
def test_ct1030_contagens_corretas_na_pagina():
    """CT-1030 | Aulas com 0, 1 e 3 presenças | cada uma mostra o seu total; abertas primeiro, depois as mais novas."""
    c = CtxEv()
    a0 = c.aula(titulo="Sem ninguém", ativa=False)
    a1 = c.aula(titulo="Uma", ativa=False)
    a3 = c.aula(p=c.ana, titulo="Três", ativa=False)
    alunos = [c.aluno] + [c.semear_aluno(email=f"a{i}@alunos.unicerrado.edu.br", matricula=f"M{i:06d}") for i in range(1, 3)]
    for i, al in enumerate(alunos):
        c.db.add(Presenca(aula_id=a3.id, aluno_id=al.id, latitude=LAT_, longitude=LON_, distancia_m=5.0, device_id=f"d{i}"))
    c.db.add(Presenca(aula_id=a1.id, aluno_id=c.aluno.id, latitude=LAT_, longitude=LON_, distancia_m=5.0, device_id="u"))
    c.db.commit()
    st, lista = c.adm_lista()
    assert st == 200
    total = {x["titulo"]: x["total"] for x in lista}
    assert total == {"Sem ninguém": 0, "Uma": 1, "Três": 3}, total
    assert [x["titulo"] for x in lista] == ["Três", "Uma", "Sem ninguém"]


def test_ct1034_chamada_aberta_aparece_primeiro_mesmo_sendo_a_mais_antiga():
    """CT-1034 | Uma chamada aberta esquecida (a mais antiga) e duas encerradas mais novas | a aberta vem primeiro,
    para nunca sumir do painel da coordenação."""
    c = CtxEv()
    velha = c.aula(titulo="Esquecida aberta", ativa=True)
    c.aula(titulo="Encerrada 1", ativa=False)
    c.aula(titulo="Encerrada 2", ativa=False)
    st, lista = c.adm_lista()
    assert st == 200 and [x["titulo"] for x in lista] == ["Esquecida aberta", "Encerrada 2", "Encerrada 1"], [x["titulo"] for x in lista]
    assert lista[0]["id"] == velha.id


def test_ct1031_pagina_de_60_e_segunda_pagina():
    """CT-1031 | 130 aulas, as de número ímpar com 1 presença | página 1 tem 60, página 2 tem 60, página 3 tem 10; as contagens
    batem em todas as páginas e nenhuma aula aparece duas vezes."""
    c = CtxEv()
    for i in range(130):
        a = c.aula(titulo=f"Aula {i}", ativa=False)
        if i % 2:
            c.db.add(Presenca(aula_id=a.id, aluno_id=c.aluno.id, latitude=LAT_, longitude=LON_, distancia_m=1.0, device_id=f"d{i}"))
    c.db.commit()
    vistos = set()
    for pg, esperado in ((1, 60), (2, 60), (3, 10), (4, 0)):
        st, lista = c.adm_lista(pg)
        assert st == 200 and len(lista) == esperado, (pg, st, len(lista))
        for x in lista:
            assert x["id"] not in vistos
            vistos.add(x["id"])
            assert x["total"] == int(x["titulo"].split()[1]) % 2, x
    assert len(vistos) == 130


def test_ct1032_lista_vazia_nao_quebra():
    """CT-1032 | Nenhuma aula | 200 com lista vazia (sem consulta de contagem com lista vazia de ids)."""
    c = CtxEv()
    st, lista = c.adm_lista()
    assert st == 200 and lista == []


def test_ct1033_a_consulta_nao_junta_presencas_de_todas_as_aulas():
    """CT-1033 | Guarda de regressão: adm_aulas escolhe a página primeiro e conta depois, só das aulas da página."""
    src = Path(APP).read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "adm_aulas")
    corpo = ast.get_source_segment(src, fn)
    assert "outerjoin(Presenca" not in corpo, "voltou a juntar presenças antes de paginar"
    assert ".in_(ids)" in corpo and "group_by(Presenca.aula_id)" in corpo and ".limit(60)" in corpo
    assert corpo.index(".limit(60)") < corpo.index("func.count(Presenca.id)")


LAT_, LON_ = -18.4157, -49.2183
