# -*- coding: utf-8 -*-
"""
QA - Funcionalidade 17: AVISO POR E-MAIL QUANDO A CHAMADA ABRE
Opcional por evento (AulaIn.avisar_email). Vai só para alunos ATIVOS do curso e do período do evento que não desligaram os avisos.
A chamada SEMPRE abre; se o aviso não puder sair (e-mail sem configuração, público vazio ou acima de AVISO_EMAIL_MAX, limite por
hora, erro ao preparar) a resposta traz o motivo. O envio roda depois da resposta (BackgroundTasks), com sessão própria, um e-mail por
aluno, e para se a chamada for encerrada no meio ou se o provedor recusar cinco envios seguidos. O log guarda só contagens.
Aluno liga/desliga em PUT /api/me/avisos; /api/me informa o estado.
Uso:  pytest tests/test_func17_aviso_email.py -v
"""
import types as _types

from harness_app import *  # noqa: F401,F403
from harness_app import FakeTarefas
from test_func4_presenca import CtxPr

SITE = "https://guaracheck.example.org"
LAT, LON = -18.4157, -49.2183


class CtxAv(CtxPr):
    """CtxPr com e-mail configurado, endereço público fixo, segundo curso e um professor SEM chamada aberta."""

    def __init__(self, email=True, **env):
        base = dict(PUBLIC_URL=SITE, AVISO_EMAIL_PAUSA_S=0)
        if email:
            base.update(ENV_EMAIL)
        base.update(env)
        super().__init__(**base)
        self.aula.ativa = False
        self.aluno.status = "inativo"             # o aluno-base do CtxPr fica fora do público: cada teste monta o seu
        self.db.commit()
        self.direito = Curso(nome="Direito")
        self.db.add(self.direito)
        self.db.commit()
        self.ns["SessionLocal"] = self._sessao_nova
        self.sessoes_abertas = 0

    def _sessao_nova(self):
        self.sessoes_abertas += 1
        d = FakeDB(tables=self.db.tables)
        d.relogio = self.db.relogio
        return d

    def alunos(self, n, curso=None, periodo=2, **extra):
        base = getattr(self, "_i", 100)
        self._i = base + n
        return [self.novo_aluno(base + k, curso_id=(curso or self.curso).id, periodo=periodo, **extra) for k in range(n)]

    def eu(self, p=None):
        p = p or self.prof
        return {"id": p.id, "role": "professor", "nome": p.nome}

    def abrir(self, avisar=True, p=None, request=None, **over):
        d = dict(titulo="Palestra de Segurança", latitude=LAT, longitude=LON, raio_metros=60, avisar_email=avisar)
        d.update(over)
        try:
            b = self.ns["AulaIn"](**d)
        except ValidationError as e:
            return 422, e, None
        tarefas = FakeTarefas()
        st, r = status_de(self.ns["abrir_aula"], b, request or FakeReq(), tarefas, self.eu(p), self.db)
        return st, r, tarefas

    def destinos(self):
        return [e[0] for e in self.emails]


@pytest.fixture
def av():
    return CtxAv()


def todos_os_corpos(c):
    return "\n".join(e[1] + "\n" + e[2] for e in c.emails)


# --------------------------------------------------------------------------- sem pedir aviso
def test_ct1901_sem_marcar_nada_e_enviado(av):
    av.alunos(3)
    st, r, t = av.abrir(avisar=False)
    assert st == 200 and "aviso" not in r and t.fila == []
    assert av.emails == []


def test_ct1902_o_pedido_padrao_nao_avisa():
    assert CtxAv().ns["AulaIn"](titulo="Xy", latitude=0, longitude=0, raio_metros=50).avisar_email is False


# --------------------------------------------------------------------------- quem recebe
def test_ct1903_envia_para_o_curso_e_o_periodo_do_evento(av):
    certos = av.alunos(3, periodo=2)
    av.alunos(2, periodo=3)                         # outro período
    av.alunos(2, curso=av.direito, periodo=2)       # outro curso
    st, r, t = av.abrir(curso_id=av.curso.id, periodo=2)
    assert st == 200 and r["aviso"] == {"enviado": True, "destinatarios": 3, "motivo": None}
    assert len(t.fila) == 1 and av.emails == []     # só depois da resposta
    t.rodar()
    assert sorted(av.destinos()) == sorted(a.email for a in certos)


def test_ct1904_sem_curso_e_sem_periodo_vai_para_todos_os_ativos(av):
    a = av.alunos(2, periodo=1)
    b = av.alunos(2, curso=av.direito, periodo=5)
    st, r, t = av.abrir()
    assert r["aviso"]["destinatarios"] == 4
    t.rodar()
    assert sorted(av.destinos()) == sorted(x.email for x in a + b)


def test_ct1905_inativos_e_quem_desligou_ficam_de_fora(av):
    ok = av.alunos(2)
    av.alunos(1, status="inativo")
    av.alunos(2, avisos_email=False)
    st, r, t = av.abrir()
    assert r["aviso"]["destinatarios"] == 2
    t.rodar()
    assert sorted(av.destinos()) == sorted(x.email for x in ok)


def test_ct1906_contas_antigas_com_campos_vazios_recebem(av):
    antigo = av.alunos(1)[0]
    antigo.status, antigo.avisos_email = None, None        # linha da V1: os campos novos vazios (depois de gravar: o insert poria o padrão)
    av.db.commit()
    st, r, t = av.abrir()
    assert r["aviso"]["destinatarios"] == 1
    t.rodar()
    assert av.destinos() == [antigo.email]


def test_ct1907_aluno_sem_curso_so_entra_em_evento_de_todos_os_cursos(av):
    sem = av.alunos(1)[0]
    sem.curso_id = None
    av.db.commit()
    st, r, t = av.abrir(curso_id=av.curso.id)
    assert r["aviso"]["destinatarios"] == 0
    st, r, t = av.abrir()
    assert r["aviso"]["destinatarios"] == 1
    t.rodar()
    assert av.destinos() == [sem.email]


def test_ct1908_professores_nao_recebem(av):
    av.alunos(1)
    st, r, t = av.abrir()
    t.rodar()
    assert av.prof.email not in av.destinos() and av.ana.email not in av.destinos() and len(av.emails) == 1


# --------------------------------------------------------------------------- o e-mail
def test_ct1911_conteudo_do_aviso(av):
    (maria,) = av.alunos(1, nome="Maria da Silva Souza")
    st, r, t = av.abrir(titulo="Palestra de Segurança", disciplina="Redes", local_descricao="Auditório, Bloco B")
    t.rodar()
    ((para, assunto, texto),) = av.emails
    assert para == maria.email and assunto == "Chamada aberta: Palestra de Segurança"
    assert texto.startswith("Olá, Maria!") and "Carlos Lima abriu a chamada de presença: Palestra de Segurança (Redes)." in texto
    assert "Local: Auditório, Bloco B" in texto
    assert f"{SITE}/?aula={r['id']}" in texto
    assert "desmarque \"Avisos por e-mail\"" in texto


def test_ct1912_cada_aluno_recebe_o_proprio_e_ninguem_ve_o_endereco_dos_outros(av):
    ys = av.alunos(4)
    st, r, t = av.abrir()
    t.rodar()
    assert len(av.emails) == 4
    for para, assunto, texto in av.emails:
        assert isinstance(para, str) and "," not in para
        for outro in ys:
            if outro.email != para:
                assert outro.email not in texto and outro.email not in assunto


def test_ct1913_disciplina_igual_ao_titulo_nao_repete(av):
    av.alunos(1)
    st, r, t = av.abrir(titulo="Cálculo II", disciplina="cálculo ii")
    t.rodar()
    assert "chamada de presença: Cálculo II.\n" in av.emails[0][2]


def test_ct1914_quebras_de_linha_nao_viram_cabecalho(av):
    av.alunos(1)
    av.prof.nome = "Carlos\r\nBcc: invasor@x.com"
    st, r, t = av.abrir(titulo="Evento\r\nBcc: outro@x.com", disciplina="Redes\nX", local_descricao="Sala\r\n1")
    t.rodar()
    ((para, assunto, texto),) = av.emails
    assert "\n" not in assunto and "\r" not in assunto
    assert "Carlos Bcc: invasor@x.com abriu" in texto and "Evento Bcc: outro@x.com (Redes X)" in texto and "Local: Sala 1" in texto


def test_ct1915_limpo_tira_controles_e_corta():
    ns = CtxAv().ns
    assert ns["_limpo"]("  a\t b\x00c\n  d  ") == "a bc d"
    assert ns["_limpo"]("x" * 500) == "x" * 140 and ns["_limpo"]("x" * 500, 10) == "x" * 10
    assert ns["_limpo"](None) == "" and ns["_limpo"]("\x07\x08") == ""


def test_ct1916_nome_do_aluno_estranho_nao_quebra(av):
    av.alunos(1, nome="\n\x00 ")
    st, r, t = av.abrir()
    t.rodar()
    assert av.emails[0][2].startswith("Olá, aluno!")


# --------------------------------------------------------------------------- a chamada sempre abre
def test_ct1921_sem_envio_configurado_a_chamada_abre_sem_aviso():
    c = CtxAv(email=False)
    c.alunos(2)
    st, r, t = c.abrir()
    assert st == 200 and r["ativa"] is True and r["aviso"]["enviado"] is False and "configurado" in r["aviso"]["motivo"]
    assert t.fila == [] and c.emails == []
    assert [a.ativa for a in c.db.tables[Aula]].count(True) == 1


def test_ct1922_publico_vazio(av):
    st, r, t = av.abrir()
    assert st == 200 and r["aviso"]["enviado"] is False and r["aviso"]["destinatarios"] == 0 and "Nenhum aluno" in r["aviso"]["motivo"]
    assert t.fila == []


def test_ct1923_publico_acima_do_limite_nao_manda_para_parte():
    c = CtxAv(AVISO_EMAIL_MAX=5)
    c.alunos(6)
    st, r, t = c.abrir()
    assert st == 200 and r["ativa"] is True
    assert r["aviso"]["enviado"] is False and r["aviso"]["destinatarios"] == 6 and "limite é de 5" in r["aviso"]["motivo"]
    assert t.fila == [] and c.emails == []


def test_ct1924_exatamente_no_limite_envia():
    c = CtxAv(AVISO_EMAIL_MAX=5)
    c.alunos(5)
    st, r, t = c.abrir()
    assert r["aviso"]["enviado"] is True
    t.rodar()
    assert len(c.emails) == 5


def test_ct1925_limite_de_avisos_por_hora_do_professor():
    c = CtxAv(AVISO_EMAIL_HORA=2)
    c.alunos(1)
    for _ in range(2):
        st, r, t = c.abrir()
        assert r["aviso"]["enviado"] is True
    st, r, t = c.abrir()
    assert st == 200 and r["ativa"] is True and r["aviso"]["enviado"] is False and "2 avisos na última hora" in r["aviso"]["motivo"]
    assert len(t.fila) == 0
    c.avancar(3601)
    st, r, t = c.abrir()
    assert r["aviso"]["enviado"] is True


def test_ct1926_o_limite_por_hora_e_de_cada_professor():
    c = CtxAv(AVISO_EMAIL_HORA=1)
    c.alunos(1)
    assert c.abrir()[1]["aviso"]["enviado"] is True
    assert c.abrir()[1]["aviso"]["enviado"] is False
    assert c.abrir(p=c.ana)[1]["aviso"]["enviado"] is True


def test_ct1927_pedido_recusado_por_publico_nao_gasta_o_limite_por_hora():
    c = CtxAv(AVISO_EMAIL_HORA=1)
    assert c.abrir()[1]["aviso"]["enviado"] is False          # ninguém no público
    c.alunos(1)
    assert c.abrir()[1]["aviso"]["enviado"] is True


def test_ct1928_endereco_do_site_impossivel_de_descobrir():
    c = CtxAv(PUBLIC_URL="")
    c.alunos(1)
    req = _types.SimpleNamespace(headers={"host": "host invalido!"}, url=_types.SimpleNamespace(scheme="https", netloc="host invalido!"))
    st, r, t = c.abrir(request=req)
    assert st == 200 and r["ativa"] is True and r["aviso"]["enviado"] is False and "PUBLIC_URL" in r["aviso"]["motivo"]
    assert t.fila == []


def test_ct1929_sem_PUBLIC_URL_usa_o_endereco_da_requisicao():
    c = CtxAv(PUBLIC_URL="")
    c.alunos(1)
    req = _types.SimpleNamespace(headers={"host": "check.unicerrado.edu.br", "x-forwarded-proto": "https"},
                                 url=_types.SimpleNamespace(scheme="http", netloc="check.unicerrado.edu.br"))
    st, r, t = c.abrir(request=req)
    t.rodar()
    assert f"https://check.unicerrado.edu.br/?aula={r['id']}" in c.emails[0][2]


def test_ct1930_erro_inesperado_ao_preparar_nao_vira_500(av):
    av.alunos(1)

    def quebrado(*a, **k):
        raise RuntimeError("banco caiu")
    av.ns["_contar"] = quebrado
    st, r, t = av.abrir()
    assert st == 200 and r["ativa"] is True and r["aviso"]["enviado"] is False and "Não foi possível" in r["aviso"]["motivo"]
    assert t.fila == []


def test_ct1931_resposta_sem_o_campo_aviso_e_igual_a_de_antes(av):
    av.alunos(1)
    com = av.abrir(avisar=True)[1]
    sem = av.abrir(avisar=False)[1]
    assert set(com) - {"aviso"} == set(sem)


def test_ct1932_codigo_de_validacao_nao_vai_no_aviso(av):
    av.alunos(1)
    st, r, t = av.abrir(codigo_validacao="ABC123")
    t.rodar()
    assert "ABC123" not in todos_os_corpos(av) and r["codigo_validacao"] == "ABC123"


# --------------------------------------------------------------------------- o envio em segundo plano
def test_ct1941_chamada_ja_encerrada_nao_envia_nada(av):
    av.alunos(3)
    st, r, t = av.abrir()
    av.db.get(Aula, r["id"]).ativa = False
    av.db.commit()
    t.rodar()
    assert av.emails == [] and av.logs("aviso_email") == []


def test_ct1942_chamada_apagada_nao_envia_nada(av):
    av.alunos(3)
    st, r, t = av.abrir()
    av.db.tables[Aula].remove(av.db.get(Aula, r["id"]))
    t.rodar()
    assert av.emails == []


def test_ct1943_para_quando_a_chamada_e_encerrada_no_meio(av):
    av.alunos(25)
    st, r, t = av.abrir()
    original = av.ns["enviar_email"]

    def enviar_e_fechar(para, assunto, texto):
        original(para, assunto, texto)
        if len(av.emails) == 10:
            av.db.get(Aula, r["id"]).ativa = False
            av.db.commit()
    av.ns["enviar_email"] = enviar_e_fechar
    t.rodar()
    assert len(av.emails) == 10
    (log,) = av.logs("aviso_email")
    assert "enviados=10" in log.detalhe and "parou=chamada encerrada" in log.detalhe


def test_ct1944_provedor_fora_do_ar_para_depois_de_cinco_falhas_seguidas(av):
    av.alunos(30)
    st, r, t = av.abrir()
    av.envio_falha = True
    t.rodar()
    (log,) = av.logs("aviso_email")
    assert "enviados=0" in log.detalhe and "falhas=5" in log.detalhe and "parou=provedor recusando" in log.detalhe


def test_ct1945_uma_falha_isolada_nao_interrompe(av):
    ys = av.alunos(6)
    st, r, t = av.abrir()
    original = av.ns["enviar_email"]

    def falha_so_no_terceiro(para, assunto, texto):
        if para == sorted(y.email for y in ys)[2] and not getattr(av, "_ja", False):
            av._ja = True
            raise OSError("caixa cheia")
        original(para, assunto, texto)
    av.ns["enviar_email"] = falha_so_no_terceiro
    t.rodar()
    assert len(av.emails) == 5
    (log,) = av.logs("aviso_email")
    assert "enviados=5" in log.detalhe and "falhas=1" in log.detalhe and "parou" not in log.detalhe


def test_ct1946_falhas_nao_seguidas_nao_somam_para_parar(av):
    av.alunos(20)
    st, r, t = av.abrir()
    original = av.ns["enviar_email"]
    n = {"i": 0}

    def alterna(para, assunto, texto):
        n["i"] += 1
        if n["i"] % 2:
            raise OSError("instável")
        original(para, assunto, texto)
    av.ns["enviar_email"] = alterna
    t.rodar()
    (log,) = av.logs("aviso_email")
    assert "enviados=10" in log.detalhe and "falhas=10" in log.detalhe and "parou" not in log.detalhe


def test_ct1947_o_log_guarda_so_contagens(av):
    av.alunos(3)
    st, r, t = av.abrir()
    t.rodar()
    (log,) = av.logs("aviso_email")
    assert log.usuario_id == av.prof.id and log.detalhe == f"aula={r['id']}; enviados=3; falhas=0"
    assert "@" not in log.detalhe


def test_ct1948_erro_na_tarefa_nao_escapa(av):
    av.alunos(2)
    st, r, t = av.abrir()

    def quebrada():
        raise RuntimeError("sem conexão com o banco")
    av.ns["SessionLocal"] = quebrada
    t.rodar()                                   # não levanta: não existe requisição para receber o erro
    assert av.emails == []


def test_ct1949_publico_que_cresceu_antes_do_envio_nao_recebe_parcial():
    c = CtxAv(AVISO_EMAIL_MAX=3)
    c.alunos(3)
    st, r, t = c.abrir()
    assert r["aviso"]["enviado"] is True
    c.alunos(1)                                # chegou mais um aluno entre o pedido e o envio
    t.rodar()
    assert c.emails == []
    (log,) = c.logs("aviso_email")
    assert "enviados=0" in log.detalhe and "parou=público acima do limite" in log.detalhe


def test_ct1950_o_publico_e_lido_na_hora_do_envio(av):
    (a, b) = av.alunos(2)
    st, r, t = av.abrir()
    b.avisos_email = False                      # b desligou os avisos depois do pedido
    av.db.commit()
    t.rodar()
    assert av.destinos() == [a.email]


def test_ct1951_usa_sessao_propria(av):
    av.alunos(1)
    st, r, t = av.abrir()
    antes = av.sessoes_abertas
    t.rodar()
    assert av.sessoes_abertas == antes + 1


def test_ct1952_envia_em_ordem_estavel_de_cadastro(av):
    ys = av.alunos(5)
    st, r, t = av.abrir()
    t.rodar()
    assert av.destinos() == [y.email for y in sorted(ys, key=lambda y: y.id)]


def test_ct1953_pausa_entre_envios_quando_configurada():
    c = CtxAv(AVISO_EMAIL_PAUSA_S=0.5)
    c.alunos(4)
    st, r, t = c.abrir()
    antes = c.ns["time"].t
    t.rodar()
    assert c.ns["time"].t - antes == pytest.approx(2.0)


# --------------------------------------------------------------------------- o aluno liga e desliga
def test_ct1961_por_padrao_o_aluno_recebe(av):
    assert status_de(av.ns["meu_perfil"], av.sessao(), av.db)[1]["avisos_email"] is True


@pytest.mark.parametrize("valor,esperado", [(None, True), (True, True), (False, False)])
def test_ct1962_perfil_informa_o_estado(av, valor, esperado):
    av.aluno.avisos_email = valor
    av.db.commit()
    assert status_de(av.ns["meu_perfil"], av.sessao(), av.db)[1]["avisos_email"] is esperado


def test_ct1963_aluno_desliga_e_religa(av):
    st, r = status_de(av.ns["meus_avisos"], av.ns["AvisosIn"](receber=False), av.sessao(), av.db)
    assert st == 200 and r == {"avisos_email": False} and av.aluno.avisos_email is False
    assert status_de(av.ns["meu_perfil"], av.sessao(), av.db)[1]["avisos_email"] is False
    st, r = status_de(av.ns["meus_avisos"], av.ns["AvisosIn"](receber=True), av.sessao(), av.db)
    assert r == {"avisos_email": True} and av.aluno.avisos_email is True


def test_ct1964_desligar_vale_na_hora_do_aviso(av):
    quem_desliga, quem_fica = av.alunos(2)
    status_de(av.ns["meus_avisos"], av.ns["AvisosIn"](receber=False), av.sessao(quem_desliga), av.db)
    st, r, t = av.abrir()
    assert r["aviso"]["destinatarios"] == 1
    t.rodar()
    assert av.destinos() == [quem_fica.email]


def test_ct1965_desligar_so_afeta_o_proprio_aluno(av):
    outro = av.alunos(1)[0]
    status_de(av.ns["meus_avisos"], av.ns["AvisosIn"](receber=False), av.sessao(), av.db)
    assert outro.avisos_email is True


def test_ct1966_corpo_invalido_e_recusado(av):
    for ruim in ("talvez", None, [], {}):
        with pytest.raises(ValidationError):
            av.ns["AvisosIn"](receber=ruim)


def test_ct1967_aluno_inexistente(av):
    st, r = status_de(av.ns["meus_avisos"], av.ns["AvisosIn"](receber=False), {"id": 9999, "role": "aluno"}, av.db)
    assert st == 404
