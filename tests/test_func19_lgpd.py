# -*- coding: utf-8 -*-
"""
QA - Funcionalidade 19: LGPD (privacidade e direitos do titular)
  - aceite do aviso de privacidade (uso da localização), versionado e revogável; sem o aceite o servidor recusa entrada e saída;
  - "baixar meus dados": tudo o que o sistema guarda do aluno, só dele;
  - pedidos do titular (excluir a conta, corrigir, pedir informação) e a resposta da coordenação (prazo de 15 dias);
  - regra de retenção: depois de N dias as presenças perdem a posição exata, o aparelho e o IP (a presença continua valendo),
    as tentativas recusadas e os registros de segurança também; roda sozinha uma vez por dia, em lotes, e a coordenação pode rodar/simular;
  - página pública /privacidade, que só diz o que o sistema faz de verdade.
O SQL (is_not/or_/in_ em PostgreSQL) roda aqui num banco de mentira: precisa ser conferido no SQLAlchemy/PostgreSQL de verdade.
O texto de /privacidade é um MODELO: a instituição revisa com o encarregado e o jurídico (LGPD.md).
Uso:  pytest tests/test_func19_lgpd.py -v
"""
import ast as _ast
import asyncio as _asyncio
import builtins as _builtins
from datetime import timedelta as _td

from harness_app import *  # noqa: F401,F403
from test_func4_presenca import IP_PUBLICO, LAT, LON
from test_func6_exportacao_auditoria import CtxEx

APP_TXT = APP.read_text(encoding="utf-8")


class CtxLg(CtxEx):
    """CtxEx com o aceite de privacidade EXIGIDO (como em produção) e atalhos para as rotas da LGPD."""

    def __init__(self, **env):
        env.setdefault("LGPD_EXIGIR_ACEITE", "1")
        super().__init__(**env)

    # --- aluno ---
    def me(self, aluno=None):
        return status_de(self.ns["meu_perfil"], self.sessao(aluno), self.db)

    def aceitar(self, sim=True, aluno=None, ip=IP_PUBLICO):
        b = self.ns["AceiteIn"](aceitar=sim)
        return status_de(self.ns["meu_aceite"], b, FakeReq(ip), self.sessao(aluno), self.db)

    def dados(self, aluno=None):
        st, r = status_de(self.ns["meus_dados"], FakeReq(IP_PUBLICO), self.sessao(aluno), self.db)
        return st, (json.loads(r.body) if st == 200 else r), r

    def pedir(self, tipo="exclusao", mensagem=None, aluno=None):
        try:
            b = self.ns["PedidoIn"](tipo=tipo, mensagem=mensagem)
        except ValidationError as e:
            return 422, e
        return status_de(self.ns["novo_pedido"], b, FakeReq(IP_PUBLICO), self.sessao(aluno), self.db)

    def meus_pedidos(self, aluno=None):
        return status_de(self.ns["meus_pedidos"], self.sessao(aluno), self.db)

    # --- coordenação ---
    def painel(self):
        return status_de(self.ns["adm_lgpd"], self.eu_admin(), self.db)

    def responder(self, pid, acao="atender", resposta=None):
        try:
            b = self.ns["PedidoRespostaIn"](acao=acao, resposta=resposta)
        except ValidationError as e:
            return 422, e
        return status_de(self.ns["adm_lgpd_pedido"], pid, b, FakeReq(IP_PUBLICO), self.eu_admin(), self.db)

    def retencao(self, simular=True):
        b = self.ns["RetencaoIn"](simular=simular)
        return status_de(self.ns["adm_lgpd_retencao"], b, FakeReq(IP_PUBLICO), self.eu_admin(), self.db)

    # --- dados prontos ---
    def antigo(self, dias=400):
        return self.agora - _td(days=dias)

    def presenca_antiga(self, aluno, aula=None, dias=400, **over):
        aula = aula or self.nova_aula(ativa=False, aberta_em=self.antigo(dias))
        d = dict(aula_id=aula.id, aluno_id=aluno.id, latitude=LAT + 0.0003, longitude=LON - 0.0002, distancia_m=41.5,
                 precisao_m=9.0, device_id=f"dev-{aluno.id}-{aula.id}-0123456789", fp_hash="f" * 16, ip="177.1.2.3",
                 alertas="IP de VPN/proxy", registrado_em=self.antigo(dias))
        d.update(over)
        p = Presenca(**d)
        self.db.add(p)
        self.db.commit()
        return p

    def tentativa(self, aluno, aula, dias=400, motivo="fora_raio", **over):
        d = dict(aula_id=aula.id, aluno_id=aluno.id, motivo=motivo, detalhe="A 150 m da sala (limite de 50 m)", latitude=LAT + 0.01,
                 longitude=LON, distancia_m=150.0, precisao_m=12.0, ip="177.1.2.3", quantidade=3, criado_em=self.antigo(dias),
                 ultima_em=self.antigo(dias))
        d.update(over)
        t = Tentativa(**d)
        self.db.add(t)
        self.db.commit()
        return t

    def logs(self, acao=None):
        return [r for r in self.db.tables[LogSeguranca] if acao is None or r.acao == acao]


@pytest.fixture
def lg():
    return CtxLg()


# =====================================================================================================
# BLOCO A - Configuração
# =====================================================================================================
def test_ct2001_padroes_do_app_sem_nenhuma_variavel():
    """CT-2001 | Sem variáveis LGPD_*: o aceite é EXIGIDO e a retenção é de 180 dias (privacidade por padrão)."""
    c = CtxLg(LGPD_EXIGIR_ACEITE=None)
    n = c.ns
    assert n["LGPD_EXIGIR_ACEITE"] is True and n["LGPD_RETENCAO_DIAS"] == 180 and n["LGPD_RETENCAO_LOGS_DIAS"] == 180
    assert n["LGPD_PRAZO_PEDIDO_DIAS"] == 15 and n["LGPD_ENCARREGADO_EMAIL"] == "" and "UniCerrado" in n["LGPD_CONTROLADOR"]


@pytest.mark.parametrize("valor", ["0", "false", "FALSE", "nao", "não", "off", "no"])
def test_ct2002_aceite_pode_ser_desligado_por_variavel(valor):
    assert CtxLg(LGPD_EXIGIR_ACEITE=valor).ns["LGPD_EXIGIR_ACEITE"] is False


@pytest.mark.parametrize("valor,esperado", [("abc", 180), ("30", 30), ("99999", 3650), ("-5", 0), ("0", 0)])
def test_ct2003_prazo_de_retencao_invalido_nao_derruba_o_servidor(valor, esperado):
    """CT-2003 | LGPD_RETENCAO_DIAS com lixo, enorme ou negativo | usa o padrão ou limita (nunca erro ao subir)."""
    assert CtxLg(LGPD_RETENCAO_DIAS=valor).ns["LGPD_RETENCAO_DIAS"] == esperado


# =====================================================================================================
# BLOCO B - Aceite do aviso de privacidade
# =====================================================================================================
def test_ct2010_perfil_informa_o_estado_do_aceite(lg):
    st, r = lg.me()
    assert st == 200 and r["aceite_privacidade"] is False and r["privacidade_exigida"] is True
    assert r["privacidade_versao"] == lg.ns["LGPD_VERSAO"]


def test_ct2011_sem_aceite_o_checkin_e_recusado_antes_de_usar_a_localizacao(lg):
    st, r = lg.registrar()
    assert st == 403 and "aviso de privacidade" in r and "manualmente" in r, r
    assert lg.presencas() == [] and lg.tentativas() == [], "recusa por falta de aceite não vira tentativa suspeita"


def test_ct2012_aceitar_libera_o_checkin_e_registra_prova(lg):
    st, r = lg.aceitar()
    assert st == 200 and r == {"aceite_privacidade": True, "versao": lg.ns["LGPD_VERSAO"]}
    assert lg.aluno.aceite_privacidade_versao == lg.ns["LGPD_VERSAO"] and lg.aluno.aceite_privacidade_em == lg.agora
    assert lg.me()[1]["aceite_privacidade"] is True
    assert lg.registrar()[0] == 200 and len(lg.presencas()) == 1
    (log,) = lg.logs("aceite_lgpd")
    assert log.usuario_id == lg.aluno.id and lg.ns["LGPD_VERSAO"] in log.detalhe and log.ip == IP_PUBLICO


def test_ct2013_revogar_bloqueia_novos_registros_e_mantem_os_antigos(lg):
    lg.aceitar()
    assert lg.registrar()[0] == 200
    st, r = lg.aceitar(False)
    assert st == 200 and r["aceite_privacidade"] is False
    assert lg.aluno.aceite_privacidade_em is None and lg.aluno.aceite_privacidade_versao is None
    assert len(lg.logs("revogou_lgpd")) == 1
    outra = lg.nova_aula(prof=lg.ana)
    st, r = lg.registrar(aula=outra)
    assert st == 403 and "aviso de privacidade" in r
    assert len(lg.presencas()) == 1, "a presença já registrada continua valendo"


def test_ct2014_texto_novo_exige_novo_aceite(lg):
    """CT-2014 | Aceite de uma versão antiga do texto | não vale: o aluno precisa aceitar de novo."""
    lg.aluno.aceite_privacidade_em, lg.aluno.aceite_privacidade_versao = lg.agora, "1999-01"
    assert lg.me()[1]["aceite_privacidade"] is False
    assert lg.registrar()[0] == 403
    lg.aceitar()
    assert lg.registrar()[0] == 200


def test_ct2015_reenvio_de_quem_ja_esta_presente_continua_idempotente(lg):
    lg.aceitar()
    assert lg.registrar()[0] == 200
    lg.aluno.aceite_privacidade_versao = None
    st, r = lg.registrar()
    assert st == 200 and r["ja_registrado"] is True, "duplo toque não vira erro de aceite"


def test_ct2016_a_saida_tambem_exige_o_aceite(lg):
    lg.aula.exige_saida = True
    lg.aceitar()
    assert lg.registrar()[0] == 200
    lg.aceitar(False)
    b = lg.corpo()
    st, r = status_de(lg.ns["registrar_saida"], lg.aula.id, b, FakeReq(IP_PUBLICO), lg.sessao(), lg.db)
    assert st == 403 and "aviso de privacidade" in r
    lg.aceitar()
    st, r = status_de(lg.ns["registrar_saida"], lg.aula.id, lg.corpo(), FakeReq(IP_PUBLICO), lg.sessao(), lg.db)
    assert st == 200 and r["ok"] is True


def test_ct2017_com_a_exigencia_desligada_registra_sem_aceite():
    c = CtxLg(LGPD_EXIGIR_ACEITE="0")
    assert c.me()[1]["privacidade_exigida"] is False
    assert c.registrar()[0] == 200


def test_ct2018_aceite_vale_so_para_quem_aceitou(lg):
    outro = lg.novo_aluno(2)
    lg.aceitar(aluno=outro)
    assert lg.aluno.aceite_privacidade_versao is None and outro.aceite_privacidade_versao == lg.ns["LGPD_VERSAO"]
    assert lg.registrar()[0] == 403
    assert lg.registrar(aluno=outro)[0] == 200


def test_ct2019_conta_removida_no_aceite_e_404(lg):
    sess = lg.sessao()
    lg.db.tables[Aluno].remove(lg.aluno)
    assert status_de(lg.ns["meu_aceite"], lg.ns["AceiteIn"](aceitar=True), FakeReq(IP_PUBLICO), sess, lg.db)[0] == 404


# =====================================================================================================
# BLOCO C - Baixar meus dados
# =====================================================================================================
@pytest.fixture
def completo(lg):
    """Aluno com presença, tentativa recusada, anotação e pedido; mais um OUTRO aluno com os mesmos tipos de dado."""
    lg.aceitar()
    assert lg.registrar()[0] == 200
    lg.tentativa(lg.aluno, lg.aula, dias=1, motivo="fora_raio")
    lg.tentativa(lg.aluno, lg.aula, dias=1, motivo="aparelho_repetido", detalhe="Aparelho já usado por Beatriz Segredo")
    pres = lg.presencas()[0]
    lg.db.add(NotaAluno(aluno_id=lg.aluno.id, presenca_id=pres.id, evento_titulo="Algoritmos", evento_tipo="aula",
                        evento_em=lg.agora, texto="Aprendi sobre pilhas e filas."))
    lg.db.commit()
    lg.pedir("informacao", "Quem vê meus dados?")
    lg.outro_aluno = lg.novo_aluno(2, nome="Beatriz Segredo")
    lg.presenca_antiga(lg.outro_aluno, ip="200.200.200.200")
    lg.db.add(NotaAluno(aluno_id=lg.outro_aluno.id, presenca_id=99, evento_titulo="Evento da Beatriz", evento_em=lg.agora,
                        texto="Anotação secreta da Beatriz"))
    lg.db.commit()
    return lg


def test_ct2020_arquivo_traz_tudo_do_aluno(completo):
    st, d, resp = completo.dados()
    assert st == 200
    assert resp.media_type == "application/json" and resp.headers["Cache-Control"] == "no-store"
    assert resp.headers["Content-Disposition"] == 'attachment; filename="meus-dados-guaracheck.json"'
    assert d["controlador"] == completo.ns["LGPD_CONTROLADOR"] and d["gerado_em"] == completo.agora.isoformat()
    t = d["titular"]
    assert (t["nome"], t["email"], t["matricula"], t["curso"], t["periodo"]) == \
        ("Aluno 1", completo.aluno.email, completo.aluno.matricula, "Gestão da TI", 2)
    assert t["aceite_privacidade_versao"] == completo.ns["LGPD_VERSAO"] and t["avisos_por_email"] is True
    (p,) = d["presencas"]
    assert p["evento"] == "Algoritmos" and p["latitude"] and p["longitude"] and p["ip"] == IP_PUBLICO
    assert p["identificador_do_aparelho"] and p["impressao_digital_do_navegador"] and p["lancada_pelo_professor"] is False
    assert p["posicao_exata_retirada_em"] is None
    assert [t["motivo"] for t in d["tentativas_recusadas"]] == ["fora_raio", "aparelho_repetido"]
    assert d["anotacoes_privadas"][0]["texto"] == "Aprendi sobre pilhas e filas."
    assert d["pedidos_lgpd"][0]["tipo"] == "informacao" and d["pedidos_lgpd"][0]["mensagem"] == "Quem vê meus dados?"


def test_ct2021_nada_de_outra_pessoa_nem_a_senha(completo):
    _, d, resp = completo.dados()
    texto = resp.body
    for proibido in ("Beatriz", "200.200.200.200", "Anotação secreta", completo.outro_aluno.email, "senha_hash"):
        assert proibido not in texto, f"vazou: {proibido}"
    rep = next(t for t in d["tentativas_recusadas"] if t["motivo"] == "aparelho_repetido")
    assert rep["detalhe"] is None, "o detalhe cita o nome de OUTRO aluno"
    fora = next(t for t in d["tentativas_recusadas"] if t["motivo"] == "fora_raio")
    assert "150 m" in fora["detalhe"]
    # o outro aluno baixa o dele: só os dados dele
    _, d2, _ = completo.dados(aluno=completo.outro_aluno)
    assert d2["titular"]["nome"] == "Beatriz Segredo" and len(d2["presencas"]) == 1 and d2["tentativas_recusadas"] == []
    assert d2["anotacoes_privadas"][0]["texto"] == "Anotação secreta da Beatriz" and "Aprendi" not in json.dumps(d2)


def test_ct2022_o_download_fica_na_auditoria(completo):
    completo.dados()
    (log,) = completo.logs("dados_titular")
    assert log.usuario_id == completo.aluno.id and log.ip == IP_PUBLICO


def test_ct2023_aluno_sem_nada_recebe_listas_vazias(lg):
    st, d, _ = lg.dados()
    assert st == 200 and d["presencas"] == d["tentativas_recusadas"] == d["anotacoes_privadas"] == d["pedidos_lgpd"] == []
    assert d["titular"]["aceite_privacidade_em"] is None


def test_ct2024_presenca_sem_posicao_exata_aparece_marcada(lg):
    lg.presenca_antiga(lg.aluno)
    lg.retencao(simular=False)
    _, d, _ = lg.dados()
    assert d["presencas"][0]["posicao_exata_retirada_em"] == lg.agora.isoformat() and d["presencas"][0]["ip"] is None


def test_ct2025_conta_removida_no_download_e_404(lg):
    sess = lg.sessao()
    lg.db.tables[Aluno].remove(lg.aluno)
    assert status_de(lg.ns["meus_dados"], FakeReq(IP_PUBLICO), sess, lg.db)[0] == 404


# =====================================================================================================
# BLOCO D - Pedidos do titular
# =====================================================================================================
def test_ct2030_aluno_faz_um_pedido_e_acompanha(lg):
    st, r = lg.pedir("exclusao", "  Quero sair do sistema.  ")
    assert st == 200 and r == {"ok": True, "prazo_dias": 15}
    st, r = lg.meus_pedidos()
    (p,) = r["itens"]
    assert r["prazo_dias"] == 15 and p["tipo"] == "exclusao" and p["status"] == "aberto" and p["mensagem"] == "Quero sair do sistema."
    assert p["resposta"] is None and p["resolvido_em"] is None
    assert lg.logs("pedido_lgpd")[0].detalhe == "tipo=exclusao"


def test_ct2031_um_pedido_aberto_por_tipo(lg):
    assert lg.pedir("exclusao")[0] == 200
    st, r = lg.pedir("exclusao")
    assert st == 409 and "em andamento" in r
    assert lg.pedir("correcao")[0] == 200 and lg.pedir("informacao")[0] == 200
    assert len(lg.meus_pedidos()[1]["itens"]) == 3


def test_ct2032_validacao_do_pedido(lg):
    assert lg.pedir("excluir-tudo")[0] == 422
    assert lg.pedir("exclusao", "x" * 501)[0] == 422
    assert lg.pedir("exclusao", "x" * 500)[0] == 200
    lg.responder(1, "atender")
    st, _ = lg.pedir("correcao", "   ")
    assert st == 200 and lg.db.tables[PedidoTitular][-1].mensagem is None, "mensagem só com espaços vira vazia"


def test_ct2033_aluno_so_ve_os_proprios_pedidos(lg):
    outro = lg.novo_aluno(2)
    lg.pedir("exclusao", aluno=outro)
    assert lg.meus_pedidos()[1]["itens"] == []
    assert len(lg.meus_pedidos(aluno=outro)[1]["itens"]) == 1


def test_ct2034_painel_da_coordenacao_lista_abertos_com_prazo(lg):
    outro = lg.novo_aluno(2, nome="Bia Santos")
    lg.pedir("exclusao", "Quero sair", aluno=outro)
    lg.pedir("correcao")
    lg.agora += _td(days=16)
    lg.pedir("informacao")
    st, r = lg.painel()
    assert st == 200
    por_tipo = {p["tipo"]: p for p in r["pedidos"]}
    assert por_tipo["exclusao"]["aluno"] == {"id": outro.id, "nome": "Bia Santos", "email": outro.email, "matricula": outro.matricula}
    assert por_tipo["exclusao"]["dias_aberto"] == 16 and por_tipo["exclusao"]["atrasado"] is True
    assert por_tipo["informacao"]["dias_aberto"] == 0 and por_tipo["informacao"]["atrasado"] is False
    lg.agora += _td(days=-16)
    assert r["respondidos"] == []
    assert [p["id"] for p in r["pedidos"]] == sorted(p["id"] for p in r["pedidos"]), "o mais antigo primeiro"


def test_ct2035_atender_exclusao_apaga_a_conta_e_tudo_dela(lg):
    lg.aceitar()
    lg.registrar()
    lg.tentativa(lg.aluno, lg.aula, dias=1)
    lg.db.add(NotaAluno(aluno_id=lg.aluno.id, presenca_id=1, evento_titulo="X", evento_em=lg.agora, texto="minha nota"))
    lg.db.add(MatriculasAutorizadas(matricula=lg.aluno.matricula, utilizada=True))
    lg.db.commit()
    lg.pedir("exclusao", "Apaguem tudo, por favor")
    aluno_id, sess = lg.aluno.id, lg.sessao()
    st, r = lg.responder(1, "atender")
    assert st == 200 and r == {"ok": True, "status": "atendido"}
    assert lg.db.tables[Aluno] == [] and lg.presencas() == [] and lg.tentativas() == [] and lg.db.tables[NotaAluno] == []
    assert lg.db.tables[MatriculasAutorizadas][0].utilizada is False, "a matrícula volta para a lista autorizada"
    (p,) = lg.db.tables[PedidoTitular]
    assert p.status == "atendido" and p.mensagem is None and p.aluno_id == aluno_id and p.resolvido_por == lg.coord.id
    assert p.resposta == "Sua conta e os dados dela foram excluídos." and p.resolvido_em == lg.agora
    (log,) = lg.logs("resp_pedido_lgpd")
    assert log.usuario_id == lg.coord.id and "atendido" in log.detalhe
    assert status_de(lg.ns["meu_perfil"], sess, lg.db)[0] == 404


def test_ct2036_conta_excluida_perde_o_acesso_na_hora(lg):
    """CT-2036 | O token do aluno excluído deixa de valer imediatamente (cache de contas esquecido)."""
    tok = "Bearer " + lg.token(lg.aluno, "aluno")
    assert lg.usuario(tok)[0] == 200
    lg.pedir("exclusao")
    lg.responder(1, "atender")
    assert lg.usuario(tok)[0] == 401


def test_ct2037_atender_correcao_ou_informacao_nao_apaga_nada(lg):
    lg.aceitar()
    lg.registrar()
    lg.pedir("correcao", "Meu período está errado")
    lg.pedir("informacao")
    st, _ = lg.responder(1, "atender", "Corrigimos o seu período para o 3º.")
    assert st == 200
    st, _ = lg.responder(2, "atender")
    assert st == 200 and len(lg.db.tables[Aluno]) == 1 and len(lg.presencas()) == 1
    a, b = lg.db.tables[PedidoTitular]
    assert a.resposta == "Corrigimos o seu período para o 3º." and a.mensagem == "Meu período está errado"
    assert b.resposta is None and b.status == "atendido"
    visto = lg.meus_pedidos()[1]["itens"]
    assert {i["status"] for i in visto} == {"atendido"} and any(i["resposta"] == a.resposta for i in visto)


def test_ct2038_recusar_exige_explicacao_e_nao_apaga(lg):
    lg.aceitar()
    lg.registrar()
    lg.pedir("exclusao")
    assert lg.responder(1, "recusar")[0] == 422
    assert lg.responder(1, "recusar", "não")[0] == 422
    assert lg.db.tables[PedidoTitular][0].status == "aberto"
    st, r = lg.responder(1, "recusar", "Você tem um certificado em andamento; peça de novo depois do evento.")
    assert st == 200 and r["status"] == "recusado"
    assert len(lg.db.tables[Aluno]) == 1 and len(lg.presencas()) == 1
    assert lg.meus_pedidos()[1]["itens"][0]["resposta"].startswith("Você tem um certificado")


def test_ct2039_pedido_respondido_nao_responde_de_novo_e_inexistente_e_404(lg):
    lg.pedir("exclusao")
    assert lg.responder(1, "atender")[0] == 200
    st, r = lg.responder(1, "atender")
    assert st == 409 and "já foi respondido" in r
    assert lg.responder(99, "atender")[0] == 404


def test_ct2040_aluno_ja_removido_nao_quebra_o_atendimento(lg):
    lg.pedir("exclusao")
    lg.db.tables[Aluno].remove(lg.aluno)                       # a coordenação já tinha excluído a conta pelo painel de alunos
    st, r = lg.responder(1, "atender")
    assert st == 200 and lg.db.tables[PedidoTitular][0].status == "atendido"


def test_ct2041_respondidos_saem_da_fila_e_ficam_no_historico(lg):
    lg.pedir("correcao")
    lg.pedir("informacao")
    lg.responder(1, "atender", "Feito.")
    r = lg.painel()[1]
    assert [p["tipo"] for p in r["pedidos"]] == ["informacao"] and [p["tipo"] for p in r["respondidos"]] == ["correcao"]
    assert r["respondidos"][0]["resposta"] == "Feito." and "dias_aberto" not in r["respondidos"][0]


def test_ct2042_resposta_e_cortada_em_300_caracteres(lg):
    lg.pedir("informacao")
    assert lg.responder(1, "atender", "x" * 301)[0] == 422             # o modelo recusa antes de chegar ao banco
    assert lg.responder(1, "atender", "x" * 300)[0] == 200


def test_ct2043_painel_traz_configuracao_e_aceites(lg):
    lg.aceitar()
    lg.novo_aluno(2)
    r = lg.painel()[1]
    assert r["aceites"] == {"alunos": 2, "aceitaram": 1}
    assert r["config"]["versao"] == lg.ns["LGPD_VERSAO"] and r["config"]["retencao_dias"] == 180 and r["config"]["prazo_dias"] == 15
    assert r["config"]["exigir_aceite"] is True and r["retencao"]["simulado"] is True


# =====================================================================================================
# BLOCO E - Regra de retenção
# =====================================================================================================
def test_ct2050_presenca_antiga_perde_posicao_aparelho_e_ip_mas_continua_valendo(lg):
    antiga = lg.presenca_antiga(lg.aluno, saida_em=lg.antigo(400) + _td(hours=2), manual=False)
    sala = lg.db.get(Aula, antiga.aula_id)
    nova = lg.presenca_antiga(lg.aluno, dias=10)                       # dentro do prazo
    nova_antes = (nova.latitude, nova.longitude, nova.ip, nova.device_id, nova.fp_hash, nova.precisao_m, nova.anonimizada_em)
    st, r = lg.retencao(simular=False)
    assert st == 200 and r["presencas"] == 1 and r["simulado"] is False
    assert (antiga.latitude, antiga.longitude) == (sala.latitude, sala.longitude), "no lugar da posição do aluno, o ponto da sala"
    assert antiga.precisao_m is None and antiga.device_id is None and antiga.fp_hash is None and antiga.ip is None
    assert antiga.anonimizada_em == lg.agora
    # o que prova a presença continua
    assert antiga.aluno_id == lg.aluno.id and antiga.distancia_m == 41.5 and antiga.registrado_em == lg.antigo(400)
    assert antiga.saida_em == lg.antigo(400) + _td(hours=2) and antiga.alertas == "IP de VPN/proxy"
    assert (nova.latitude, nova.longitude, nova.ip, nova.device_id, nova.fp_hash, nova.precisao_m, nova.anonimizada_em) == nova_antes


def test_ct2051_tentativas_antigas_perdem_posicao_ip_e_detalhe(lg):
    aula = lg.nova_aula(ativa=False)
    velha = lg.tentativa(lg.aluno, aula, dias=300)
    recente = lg.tentativa(lg.aluno, aula, dias=5, motivo="vpn")
    antes = (recente.latitude, recente.ip, recente.detalhe)
    r = lg.retencao(simular=False)[1]
    assert r["tentativas"] == 1
    assert velha.latitude is velha.longitude is velha.distancia_m is velha.precisao_m is velha.ip is velha.detalhe is None
    assert velha.motivo == "fora_raio" and velha.quantidade == 3 and velha.aluno_id == lg.aluno.id
    assert (recente.latitude, recente.ip, recente.detalhe) == antes


def test_ct2052_registros_de_seguranca_perdem_so_o_ip(lg):
    velho = LogSeguranca(usuario_id=lg.coord.id, acao="excluir_aluno", detalhe="aluno=7", ip="177.9.9.9", data_hora=lg.antigo(300))
    novo = LogSeguranca(usuario_id=lg.coord.id, acao="excluir_aluno", detalhe="aluno=8", ip="177.9.9.8", data_hora=lg.antigo(10))
    lg.db.add(velho)
    lg.db.add(novo)
    lg.db.commit()
    r = lg.retencao(simular=False)[1]
    assert r["logs"] == 1
    assert velho.ip is None and velho.acao == "excluir_aluno" and velho.detalhe == "aluno=7" and velho.usuario_id == lg.coord.id
    assert novo.ip == "177.9.9.8"


def test_ct2053_simular_so_conta_e_o_real_confere(lg):
    lg.presenca_antiga(lg.aluno)
    lg.presenca_antiga(lg.novo_aluno(2))
    lg.tentativa(lg.aluno, lg.nova_aula(ativa=False))
    antes = [(p.latitude, p.ip, p.device_id, p.anonimizada_em) for p in lg.presencas()]
    st, sim = lg.retencao(simular=True)
    assert st == 200 and sim["simulado"] is True and (sim["presencas"], sim["tentativas"]) == (2, 1)
    assert [(p.latitude, p.ip, p.device_id, p.anonimizada_em) for p in lg.presencas()] == antes, "simular não mexe em nada"
    assert lg.logs("retencao_manual") == [] and lg.logs("retencao_lgpd") == []
    assert lg.retencao()[1]["presencas"] == 2, "o padrão da rota é simular"
    real = lg.retencao(simular=False)[1]
    assert (real["presencas"], real["tentativas"], real["logs"]) == (sim["presencas"], sim["tentativas"], sim["logs"])
    (m,) = lg.logs("retencao_manual")
    assert m.usuario_id == lg.coord.id and "presencas=2" in m.detalhe and "tentativas=1" in m.detalhe


def test_ct2054_segunda_rodada_nao_faz_nada(lg):
    p = lg.presenca_antiga(lg.aluno)
    lg.retencao(simular=False)
    quando = p.anonimizada_em
    lg.agora += _td(days=3)
    r = lg.retencao(simular=False)[1]
    assert (r["presencas"], r["tentativas"], r["logs"]) == (0, 0, 0) and p.anonimizada_em == quando, "não sobrescreve a data"


def test_ct2055_prazo_zero_desliga_cada_parte():
    c = CtxLg(LGPD_RETENCAO_DIAS="0", LGPD_RETENCAO_LOGS_DIAS="0")
    p = c.presenca_antiga(c.aluno)
    c.tentativa(c.aluno, c.nova_aula(ativa=False))
    c.db.add(LogSeguranca(usuario_id=1, acao="x", ip="1.2.3.4", data_hora=c.antigo(900)))
    c.db.commit()
    r = c.retencao(simular=False)[1]
    assert (r["presencas"], r["tentativas"], r["logs"]) == (0, 0, 0) and r["corte_presencas"] is None and r["corte_logs"] is None
    assert p.ip == "177.1.2.3" and p.anonimizada_em is None
    # só os registros de segurança desligados
    c2 = CtxLg(LGPD_RETENCAO_LOGS_DIAS="0")
    p2 = c2.presenca_antiga(c2.aluno)
    c2.db.add(LogSeguranca(usuario_id=1, acao="x", ip="1.2.3.4", data_hora=c2.antigo(900)))
    c2.db.commit()
    r2 = c2.retencao(simular=False)[1]
    assert r2["presencas"] == 1 and r2["logs"] == 0 and p2.ip is None


def test_ct2056_prazo_e_contado_em_dias_exatos():
    """CT-2056 | Com 30 dias de prazo: exatamente 30 dias ainda não passou; 30 dias e 1 segundo já passou."""
    c = CtxLg(LGPD_RETENCAO_DIAS="30")
    no_limite = c.presenca_antiga(c.aluno, dias=30)
    passou = c.presenca_antiga(c.novo_aluno(2), dias=30)
    passou.registrado_em -= _td(seconds=1)
    c.retencao(simular=False)
    assert no_limite.anonimizada_em is None and passou.anonimizada_em is not None


def test_ct2057_processa_em_lotes_e_confirma_um_lote_por_vez(lg):
    lg.ns["LGPD_LOTE"] = 2
    aula = lg.nova_aula(ativa=False)
    for i in range(7):
        lg.presenca_antiga(lg.novo_aluno(10 + i), aula=aula)
    commits = []
    original = lg.db.commit
    lg.db.commit = lambda: (commits.append(1), original())[1]
    r = lg.retencao(simular=False)[1]
    assert r["presencas"] == 7 and all(p.anonimizada_em for p in lg.presencas())
    assert len(commits) >= 4 + 1, f"um commit por lote de 2 (4 lotes) mais o log: {len(commits)}"


def test_ct2058_comprovante_e_certificado_nao_mudam_com_a_retencao(lg):
    aula = lg.nova_aula(ativa=False, aberta_em=lg.antigo(400), carga_horas=2.0)
    lg.presenca_antiga(lg.aluno, aula=aula)
    antes = status_de(lg.ns["minhas_presencas"], lg.sessao(), lg.db, 1)
    lg.retencao(simular=False)
    depois = status_de(lg.ns["minhas_presencas"], lg.sessao(), lg.db, 1)
    assert antes[0] == depois[0] == 200 and antes[1] == depois[1] and len(depois[1]) == 1
    pdf = status_de(lg.ns["comprovante_pdf"], 1, lg.sessao(), lg.db)
    assert pdf[0] == 200


def test_ct2059_registro_da_limpeza_so_quando_apagou_algo(lg):
    lg.retencao(simular=False)
    assert lg.logs("retencao_manual") != [] and lg.logs("retencao_lgpd") == []      # a rota manual sempre registra o pedido; o job só quando mexeu
    lg.presenca_antiga(lg.aluno)
    r = lg.ns["aplicar_retencao"](lg.db)
    assert r["presencas"] == 1
    (log,) = lg.logs("retencao_lgpd")
    assert log.usuario_id is None and log.detalhe == "presencas=1; tentativas=0; logs=0" and log.ip is None
    lg.ns["aplicar_retencao"](lg.db)
    assert len(lg.logs("retencao_lgpd")) == 1


def test_ct2060_painel_mostra_o_que_a_limpeza_apagaria_agora(lg):
    lg.presenca_antiga(lg.aluno)
    r = lg.painel()[1]["retencao"]
    assert r["simulado"] is True and r["presencas"] == 1 and r["corte_presencas"] == (lg.agora - _td(days=180)).isoformat()
    assert lg.presencas()[0].ip == "177.1.2.3", "abrir o painel não apaga nada"


def test_ct2061_deslocamento_impossivel_continua_funcionando_depois_da_retencao(lg):
    """CT-2061 | A regra anti-fraude olha a última presença do aluno; depois da limpeza ela tem o ponto da sala (nunca erro)."""
    lg.aceitar()
    lg.presenca_antiga(lg.aluno, dias=300)
    lg.retencao(simular=False)
    assert lg.registrar()[0] == 200


# --- limpeza automática (uma vez por dia) -------------------------------------------------------------
class AsyncioFalso:
    """Troca o asyncio do app: sleep anota o tempo pedido e, na N-ésima vez, cancela (como o shutdown do servidor)."""

    def __init__(self, parar_em):
        self.dormiu, self.parar_em, self.threads = [], parar_em, 0

    async def sleep(self, s):
        self.dormiu.append(s)
        if len(self.dormiu) >= self.parar_em:
            raise _asyncio.CancelledError()

    async def to_thread(self, fn, *a):
        self.threads += 1
        return fn(*a)


def _rodar_ciclo(c, parar_em):
    falso = AsyncioFalso(parar_em)
    c.ns["asyncio"] = falso
    with pytest.raises(_asyncio.CancelledError):
        _asyncio.run(c.ns["_ciclo_retencao"]())
    return falso


def test_ct2062_ciclo_espera_um_minuto_roda_e_repete_a_cada_dia(lg):
    lg.presenca_antiga(lg.aluno)
    lg.ns["SessionLocal"] = lg._sessao_do_stream
    f = _rodar_ciclo(lg, 3)
    assert f.dormiu == [60, 24 * 3600, 24 * 3600] and f.threads == 2
    assert lg.presencas()[0].anonimizada_em is not None
    assert lg.sessoes and all(s.fechada for s in lg.sessoes), "cada rodada usa e fecha a própria sessão do banco"


def test_ct2063_erro_na_limpeza_nao_para_o_ciclo(lg):
    def quebra():
        raise RuntimeError("banco fora do ar")
    lg.ns["_retencao_agendada"] = quebra
    f = _rodar_ciclo(lg, 4)
    assert f.dormiu == [60, 86400, 86400, 86400] and f.threads == 3, "continuou tentando depois de errar"


def test_ct2064_cancelar_o_servidor_para_o_ciclo(lg):
    f = _rodar_ciclo(lg, 1)
    assert f.dormiu == [60] and f.threads == 0


def test_ct2065_ciclo_so_liga_com_algum_prazo_e_para_no_desligamento():
    trecho = APP_TXT[APP_TXT.index("async def lifespan"):APP_TXT.index("# /docs e /openapi.json desligados")]
    assert "ciclo = asyncio.create_task(_ciclo_retencao()) if (LGPD_RETENCAO_DIAS or LGPD_RETENCAO_LOGS_DIAS) else None" in trecho
    assert trecho.index("yield") < trecho.index("ciclo.cancel()")
    assert "LGPD_ENCARREGADO_EMAIL" in trecho, "o servidor avisa no log quando o contato do encarregado não foi informado"


# =====================================================================================================
# BLOCO F - Página /privacidade
# =====================================================================================================
def pagina(**env):
    return CtxLg(**env).ns["pagina_privacidade"]()


def test_ct2070_rota_publica_devolve_html_sem_cache():
    c = CtxLg()
    r = c.ns["privacidade"]()
    assert r.status_code == 200 and r.media_type.startswith("text/html") and r.headers["Cache-Control"] == "no-cache"
    assert r.body.startswith("<!doctype html>") and 'lang="pt-BR"' in r.body
    assert "Depends" not in APP_TXT[APP_TXT.index('@app.get("/privacidade")'):][:200], "sem login"


def test_ct2071_pagina_cobre_o_que_a_lei_pede():
    p = pagina(LGPD_CONTROLADOR="UniCerrado Teste", LGPD_ENCARREGADO_NOME="Dra. Helena Dias", LGPD_ENCARREGADO_EMAIL="dpo@unicerrado.edu.br")
    for trecho in ("UniCerrado Teste", "Dra. Helena Dias", 'href="mailto:dpo@unicerrado.edu.br"', "Lei 13.709/2018",
                   "Que dados usamos", "Para que usamos", "Bases legais", "Quem vê os seus dados", "Por quanto tempo guardamos",
                   "Seus direitos", "Segurança", "consentimento", "Meus dados e privacidade", "revogar o aceite",
                   "<b>180 dias</b>", "até <b>15 dias</b>", "lançar a sua presença", "Se você não aceitar", "HTTPS"):
        assert trecho in p, f"a página não diz: {trecho}"
    assert f"Versão <b>{CtxLg().ns['LGPD_VERSAO']}</b>" in p


def test_ct2072_valores_da_instituicao_nao_viram_html():
    p = pagina(LGPD_CONTROLADOR='Uni<script>alert(1)</script>"', LGPD_ENCARREGADO_NOME="<b>X</b>",
               LGPD_ENCARREGADO_EMAIL='a@b.com"><script>alert(2)</script>')
    assert "<script>" not in p and "&lt;script&gt;" in p and "&lt;b&gt;X&lt;/b&gt;" in p
    assert 'href="mailto:a@b.com"' not in p, "e-mail com caracteres perigosos não vira link"


def test_ct2073_sem_contato_do_encarregado_a_pagina_avisa():
    p = pagina()
    assert "ainda não informou o contato do encarregado" in p and "mailto:" not in p


def test_ct2074_retencao_desligada_e_dita_com_clareza():
    p = pagina(LGPD_RETENCAO_DIAS="0", LGPD_RETENCAO_LOGS_DIAS="0")
    assert "<b>não</b> apaga automaticamente" in p and "dias</b> o sistema apaga" not in p and "registros de segurança (ações da coordenação, entradas e exclusões) é apagado" not in p
    p2 = pagina(LGPD_RETENCAO_DIAS="90", LGPD_RETENCAO_LOGS_DIAS="365")
    assert "<b>90 dias</b>" in p2 and "depois de 365 dias" in p2


def test_ct2075_consulta_de_vpn_so_e_citada_quando_existe():
    assert "proxycheck" not in pagina()
    assert "proxycheck.io" in pagina(PROXYCHECK_KEY="chave")


def test_ct2076_pagina_sem_script_e_sem_recurso_externo():
    p = pagina()
    assert "<script" not in p.lower() and "http://" not in p and "https://" not in p and "<iframe" not in p
    assert 'href="/"' in p, "tem o caminho de volta ao app"


def test_ct2077_o_que_a_pagina_promete_sobre_o_professor_e_verdade(lg):
    """CT-2077 | A página diz que o professor não vê coordenadas nem IP: o que a rota do professor devolve confere."""
    lg.aceitar()
    assert lg.registrar()[0] == 200
    st, r = status_de(lg.ns["presencas_da_aula"], lg.aula.id, 0, lg.eu(), lg.db)
    assert st == 200 and r["presencas"]
    chaves = set(r["presencas"][0])
    assert not chaves & {"latitude", "longitude", "ip", "device_id", "fp_hash"}, chaves
    assert "mas não as coordenadas nem o IP" in lg.ns["pagina_privacidade"]()
    st, t = status_de(lg.ns["tentativas_da_aula"], lg.aula.id, lg.eu(), lg.db)
    assert st == 200 and not any({"latitude", "longitude", "ip"} & set(i) for i in t["itens"])


def test_ct2078_o_que_a_pagina_diz_sobre_o_envio_da_posicao_confere_com_a_tela():
    """CT-2078 | 'só é enviada quando você toca em Registrar': no app, a posição só vai ao servidor por UM caminho, o clique do botão."""
    html_app = (APP.parent / "index.html").read_text(encoding="utf-8")
    assert html_app.count("${saida ? 'saida' : 'presenca'}") == 1
    ini = html_app.index("$('#aulas').onclick = async")
    clique = html_app[ini:html_app.index("/* PDF do comprovante", ini)]
    assert "await amostrar()" in clique and "${saida ? 'saida' : 'presenca'}" in clique and "method:'POST'" in clique


# =====================================================================================================
# BLOCO G - Estrutura do código (o que não dá para ver rodando aqui)
# =====================================================================================================
def _modulo():
    return _ast.parse(APP_TXT)


def test_ct2080_toda_funcao_so_usa_no_decorador_e_nos_padroes_nomes_ja_definidos():
    """CT-2080 | Depends(so_admin), modelos de entrada, etc. precisam existir ANTES da função, senão o servidor nem sobe (NameError ao
    importar). Aqui o app não é importado (sem FastAPI), então a ordem é conferida na análise do código."""
    arv = _modulo()
    definidos = {}

    def registra(n):
        if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
            definidos.setdefault(n.name, n.lineno)
        elif isinstance(n, _ast.Assign):
            for t in n.targets:
                for x in _ast.walk(t):
                    if isinstance(x, _ast.Name):
                        definidos.setdefault(x.id, n.lineno)
        elif isinstance(n, _ast.AnnAssign) and isinstance(n.target, _ast.Name):
            definidos.setdefault(n.target.id, n.lineno)
        elif isinstance(n, (_ast.Import, _ast.ImportFrom)):
            for a in n.names:
                definidos.setdefault((a.asname or a.name).split(".")[0], n.lineno)
        elif isinstance(n, (_ast.Try, _ast.If, _ast.With)):
            for filho in _ast.iter_child_nodes(n):
                registra(filho)
    for n in arv.body:
        registra(n)
    ruins = []
    for n in arv.body:
        if not isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            continue
        expr = list(n.decorator_list) + list(n.args.defaults) + [d for d in n.args.kw_defaults if d is not None]
        expr += [a.annotation for a in n.args.args + n.args.kwonlyargs if a.annotation is not None]
        if n.returns is not None:
            expr.append(n.returns)
        for e in expr:
            for x in _ast.walk(e):
                if isinstance(x, _ast.Name) and isinstance(x.ctx, _ast.Load) and x.id not in dir(_builtins):
                    if x.id not in definidos:
                        ruins.append(f"{n.name} (linha {n.lineno}): {x.id} nunca é definido")
                    elif definidos[x.id] > n.lineno:
                        ruins.append(f"{n.name} (linha {n.lineno}): {x.id} só é definido na linha {definidos[x.id]}")
    assert ruins == [], ruins


def _dependencias(nome):
    f = next(n for n in _modulo().body if isinstance(n, _ast.FunctionDef) and n.name == nome)
    return sorted({x.func.id + "(" + x.args[0].id + ")" for x in _ast.walk(f) if isinstance(x, _ast.Call) and isinstance(x.func, _ast.Name)
                   and x.func.id == "Depends" and x.args and isinstance(x.args[0], _ast.Name)})


@pytest.mark.parametrize("rota", ["meu_aceite", "meus_dados", "meus_pedidos", "novo_pedido"])
def test_ct2081_rotas_do_titular_sao_so_do_aluno(rota):
    assert "Depends(so_aluno)" in _dependencias(rota)


@pytest.mark.parametrize("rota", ["adm_lgpd", "adm_lgpd_retencao", "adm_lgpd_pedido"])
def test_ct2082_rotas_de_gestao_da_lgpd_sao_so_da_coordenacao(rota):
    assert "Depends(so_admin)" in _dependencias(rota)


def test_ct2083_rotas_e_metodos():
    for decorador in ('@app.put("/api/me/privacidade")', '@app.get("/api/me/dados")', '@app.get("/api/me/pedidos")',
                      '@app.post("/api/me/pedidos")', '@app.get("/api/admin/lgpd")', '@app.post("/api/admin/lgpd/retencao")',
                      '@app.put("/api/admin/lgpd/pedidos/{pedido_id}")', '@app.get("/privacidade")'):
        assert decorador in APP_TXT, decorador


def test_ct2084_bancos_antigos_ganham_as_colunas_novas():
    """CT-2084 | create_all não altera tabelas existentes: cada coluna nova precisa estar em migrar_esquema (e a tabela nova nasce no create_all)."""
    novas = APP_TXT[APP_TXT.index("def migrar_esquema"):APP_TXT.index("insp = inspect(engine)")]
    for coluna in ("aceite_privacidade_em", "aceite_privacidade_versao", "anonimizada_em"):
        assert f'("{coluna}", "' in novas, f"{coluna} não está em migrar_esquema"
        assert f"{coluna}: Mapped[" in APP_TXT
    assert "class PedidoTitular(Base):" in APP_TXT and '__tablename__ = "pedidos_titular"' in APP_TXT


def test_ct2085_pedido_nao_tem_chave_estrangeira_para_o_aluno():
    """CT-2085 | O pedido atendido de EXCLUSÃO tem que sobreviver à conta (sem FK, senão a exclusão cascatearia ou seria barrada)."""
    classe = next(n for n in _modulo().body if isinstance(n, _ast.ClassDef) and n.name == "PedidoTitular")
    trecho = _ast.get_source_segment(APP_TXT, classe)
    assert "ForeignKey" not in trecho and "aluno_id: Mapped[int] = mapped_column(Integer, index=True)" in trecho


def test_ct2086_toda_funcao_de_exclusao_de_aluno_passa_por_apagar_aluno():
    """CT-2086 | Um só jeito de apagar a conta (presenças, tentativas, anotações, matrícula): a coordenação e o pedido do titular usam o mesmo."""
    f = next(n for n in _modulo().body if isinstance(n, _ast.FunctionDef) and n.name == "_apagar_aluno")
    fonte = _ast.get_source_segment(APP_TXT, f)
    for modelo in ("Tentativa", "Presenca", "NotaAluno", "Aluno"):
        assert f"delete({modelo})" in fonte
    assert "MatriculasAutorizadas" in fonte and "db.commit" not in fonte
