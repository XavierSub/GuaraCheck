# -*- coding: utf-8 -*-
"""
QA - Funcionalidade 6: EXPORTAÇÃO E AUDITORIA (Guará Check)
Relatório da chamada em CSV (streaming, Presentes + Ausentes), proteção contra injeção de fórmula, trilha de auditoria
(LogSeguranca), alertas de check-in suspeito e rota de diagnóstico do IP.

Como nas funcionalidades anteriores: o código REAL do app.py é extraído por ast e executado contra um banco em memória
(harness_app.py). O StreamingResponse é um dublê que guarda o gerador: os testes consomem o gerador de verdade.
NÃO é executado aqui: SQLAlchemy/PostgreSQL de verdade (yield_per, collation da ordenação) e o download no navegador.

Uso:  pip install pytest pydantic pyjwt openpyxl reportlab pypdf
      pytest test_func6_exportacao_auditoria.py -v
"""
import ast as _ast
import types as _types
from datetime import datetime as _dt, timezone as _tz

from harness_app import *  # noqa: F401,F403
from harness_app import APP, FakeDB, FakeReq, LogSeguranca, Professor  # noqa: F401
from test_func4_presenca import DEV, IP_PUBLICO, LAT, LON, dev  # noqa: F401
from test_func5_chamada_comprovantes import CtxCh

CABECALHO = '"Matrícula";"Nome do Aluno";"E-mail";"Curso";"Período";"Status";"Data/Hora do Check-in";"Distância (m)"\r\n'


class CtxEx(CtxCh):
    """CtxCh + coordenação + atalhos para exportar e consultar a auditoria."""

    def __init__(self, **env):
        super().__init__(**env)
        self.sessoes = []                                  # sessões abertas pelo gerador do CSV (uma própria por download)
        self.ns["SessionLocal"] = self._sessao_do_stream
        self.coord = Professor(nome="Coord Geral", email="coord@unicerrado.edu.br", senha_hash="x", registro_funcional="COORD-1",
                               departamento="Coordenação", coordenacao=True)
        self.db.add(self.coord)
        self.db.commit()

    def _sessao_do_stream(self):
        s = FakeDB(tables=self.db.tables)
        s.relogio = self.db.relogio
        self.sessoes.append(s)
        return s

    def eu_admin(self):
        return {"id": self.coord.id, "role": "admin", "nome": self.coord.nome}

    # --- exportação ---
    def exportar(self, aula=None, p=None, admin=False, ip=IP_PUBLICO, xff=None, legado=False, db=None, u=None):
        u = u or (self.eu_admin() if admin else self.eu(p))
        a = (aula or self.aula).id
        if legado:
            return status_de(self.ns["exportar_chamada"], a, FakeReq(ip=ip, xff=xff), u, db or self.db)
        return status_de(self.ns["export_chamada"], FakeReq(ip=ip, xff=xff), a, u, db or self.db)

    def baixar(self, **kw):
        """(resposta, pedaços do stream, texto inteiro). Consome o gerador como o servidor faria."""
        st, resp = self.exportar(**kw)
        assert st == 200, (st, resp)
        pedacos = list(resp.body_iterator)
        return resp, pedacos, "".join(pedacos)

    @staticmethod
    def linhas(texto):
        assert texto.startswith("﻿"), "o CSV precisa começar com BOM (Excel)"
        return list(csv.reader(io.StringIO(texto[1:]), delimiter=";"))

    def tabela(self, **kw):
        """Linhas do CSV (sem o cabeçalho)."""
        return self.linhas(self.baixar(**kw)[2])[1:]

    def curso_na_aula(self, periodo=None, aula=None):
        a = aula or self.aula
        a.curso_id, a.periodo = self.curso.id, periodo
        return a

    def povoar(self, presentes, ausentes, prefixo="V"):
        """Muitos alunos de uma vez (um único commit): `presentes` com presença e `ausentes` sem."""
        alunos = []
        for i in range(presentes + ausentes):
            alunos.append(Aluno(nome=f"{prefixo}{i:05d}", email=f"{prefixo.lower()}{i}@alunos.unicerrado.edu.br",
                                matricula=f"{prefixo}{i:08d}", curso_id=self.curso.id, periodo=2, status="ativo", senha_hash="x"))
        for a in alunos:
            self.db.add(a)
        self.db.commit()
        for a in alunos[:presentes]:
            self.db.add(Presenca(aula_id=self.aula.id, aluno_id=a.id, latitude=LAT, longitude=LON, distancia_m=7.0,
                                 precisao_m=10.0, device_id=f"{prefixo}-dev-{a.id:012d}", fp_hash="abcdef0123456789",
                                 ip="177.1.1.1", alertas=None))
        self.db.commit()
        return alunos

    # --- auditoria / alertas ---
    def alertas(self, p=None, admin=True):
        u = self.eu_admin() if admin else self.eu(p)
        return status_de(self.ns["adm_alertas"], u, self.db)

    def auditoria(self, p=None, admin=True):
        u = self.eu_admin() if admin else self.eu(p)
        return status_de(self.ns["adm_auditoria"], u, self.db)


@pytest.fixture
def ex():
    return CtxEx()


# =====================================================================================================
# BLOCO A - Quem pode exportar
# =====================================================================================================
def test_ct601_aluno_nao_exporta(ex):
    """CT-601 | Aluno chama a exportação | 403 (RBAC: só professor ou coordenação)."""
    st, det = status_de(ex.ns["_prof_ou_admin"], {"id": ex.aluno.id, "role": "aluno", "nome": "X"})
    assert st == 403 and det == "Acesso negado."
    for papel in ("professor", "admin"):
        assert status_de(ex.ns["_prof_ou_admin"], {"id": 1, "role": papel, "nome": "X"})[0] == 200


def test_ct602_sem_login_nao_exporta(ex):
    """CT-602 | Sem token | 401 (a dependência de usuário barra antes de qualquer leitura)."""
    assert status_de(ex.ns["usuario_atual"], None)[0] == 401


def test_ct603_professor_dono_exporta(ex):
    """CT-603 | Professor dono da chamada | 200, CSV com os cabeçalhos certos de download."""
    ex.semear_presenca(ex.aluno)
    resp, _, _ = ex.baixar()
    assert resp.status_code == 200
    assert resp.media_type == "text/csv; charset=utf-8"
    assert resp.headers["Content-Disposition"] == f'attachment; filename="chamada-{ex.aula.id}.csv"'
    assert resp.headers["Cache-Control"] == "no-store"


def test_ct604_outro_professor_recebe_404_e_nada_e_registrado(ex):
    """CT-604 | Professor tenta exportar chamada alheia | 404 (não revela que existe) e nenhum log de exportação."""
    st, det = ex.exportar(p=ex.ana)
    assert st == 404 and "Aula não encontrada" in det
    assert ex.logs("exportar_chamada") == []


def test_ct605_aula_inexistente_404(ex):
    """CT-605 | Chamada que não existe | 404 (igual ao caso de chamada alheia: sem pista)."""
    ex.aula = _types.SimpleNamespace(id=9999)
    assert ex.exportar()[0] == 404
    assert ex.exportar(admin=True)[0] == 404
    assert ex.logs("exportar_chamada") == []


def test_ct606_coordenacao_exporta_qualquer_chamada(ex):
    """CT-606 | Coordenação exporta a chamada de qualquer professor | 200 e o log guarda perfil=admin."""
    ex.semear_presenca(ex.aluno)
    _, _, texto = ex.baixar(admin=True)
    assert len(ex.linhas(texto)) == 2
    log = ex.logs("exportar_chamada")[0]
    assert log.usuario_id == ex.coord.id and "perfil=admin" in log.detalhe


def test_ct607_rota_antiga_so_professor_e_mesmo_resultado(ex):
    """CT-607 | Rota antiga /exportar: só professor (coordenação 403 na dependência), mesmo CSV e mesmo log."""
    ex.semear_presenca(ex.aluno)
    assert status_de(ex.ns["so_professor"], ex.eu_admin())[0] == 403
    _, _, novo = ex.baixar()
    _, _, velho = ex.baixar(legado=True)
    assert novo == velho
    assert len(ex.logs("exportar_chamada")) == 2


# =====================================================================================================
# BLOCO B - Conteúdo e formato do CSV
# =====================================================================================================
def test_ct610_bom_cabecalho_e_formato(ex):
    """CT-610 | Formato para o Excel brasileiro | BOM, separador ';', todos os campos entre aspas, linhas CRLF, cabeçalho exato."""
    ex.semear_presenca(ex.aluno, dist=4.0)
    _, pedacos, texto = ex.baixar()
    assert pedacos[0] == "﻿" + CABECALHO
    corpo = texto[len("﻿" + CABECALHO):]
    assert corpo.endswith("\r\n")
    for linha in corpo.split("\r\n")[:-1]:
        assert linha.startswith('"') and linha.endswith('"') and '";"' in linha


def test_ct611_colunas_de_uma_presenca(ex):
    """CT-611 | Uma linha de presente | 8 colunas, valores do aluno, 'Presente', distância com vírgula decimal."""
    ex.semear_presenca(ex.aluno, dist=12.345, quando=_dt(2026, 11, 11, 15, 4, 5, tzinfo=_tz.utc))
    (linha,) = ex.tabela()
    assert linha == ["GTI202600001", "Aluno 1", "aluno1@alunos.unicerrado.edu.br", "Gestão da TI", "2º", "Presente",
                     "11/11/2026 12:04:05", "12,3"]


def test_ct612_fuso_de_brasilia_e_virada_de_dia(ex):
    """CT-612 | 02:30 UTC é 23:30 do dia anterior em Brasília | o relatório mostra o horário local."""
    ex.semear_presenca(ex.aluno, quando=_dt(2026, 11, 11, 2, 30, 0, tzinfo=_tz.utc))
    assert ex.tabela()[0][6] == "10/11/2026 23:30:00"


def test_ct613_data_sem_fuso_e_tratada_como_utc(ex):
    """CT-613 | Horário gravado sem fuso (conta antiga/SQLite) | tratado como UTC, não como horário do servidor."""
    ex.semear_presenca(ex.aluno, quando=_dt(2026, 11, 11, 15, 0, 0))
    assert ex.tabela()[0][6] == "11/11/2026 12:00:00"


def test_ct614_fuso_configuravel():
    """CT-614 | FUSO_HORAS=0 e +9 | o CSV acompanha a configuração."""
    for fuso, esperado in (("0", "11/11/2026 15:00:00"), ("9", "12/11/2026 00:00:00")):
        e = CtxEx(FUSO_HORAS=fuso)
        e.semear_presenca(e.aluno, quando=_dt(2026, 11, 11, 15, 0, 0, tzinfo=_tz.utc))
        assert e.tabela()[0][6] == esperado


def test_ct615_distancia_arredondada_com_virgula(ex):
    """CT-615 | Distâncias 0, 5, 49.96, 1234.56 | uma casa decimal e vírgula (nunca ponto, que o Excel BR leria como data/milhar)."""
    for i, (d, esp) in enumerate([(0.0, "0,0"), (5.0, "5,0"), (49.96, "50,0"), (1234.56, "1234,6")], start=2):
        ex.semear_presenca(ex.novo_aluno(i), dist=d)
    achados = {l[1]: l[7] for l in ex.tabela()}
    assert achados == {"Aluno 2": "0,0", "Aluno 3": "5,0", "Aluno 4": "50,0", "Aluno 5": "1234,6"}


def test_ct616_ordem_alfabetica_dos_presentes(ex):
    """CT-616 | Presentes saem em ordem alfabética do nome (não na ordem de chegada)."""
    for i, n in enumerate(["Carla", "Ana", "Bruno"], start=2):
        ex.semear_presenca(ex.novo_aluno(i, nome=n))
    assert [l[1] for l in ex.tabela()] == ["Ana", "Bruno", "Carla"]


def test_ct617_periodo_vazio_e_aluno_sem_curso(ex):
    """CT-617 | Aluno sem período e sem curso | colunas vazias, sem 'Nonenº' nem 'None'."""
    a = ex.novo_aluno(2, curso_id=None, periodo=None)
    ex.semear_presenca(a)
    (linha,) = ex.tabela()
    assert linha[3] == "" and linha[4] == ""
    assert "None" not in "".join(linha)


def test_ct618_aluno_inativo_que_registrou_continua_no_relatorio(ex):
    """CT-618 | Aluno desativado depois de registrar | a presença que ele fez continua no relatório."""
    ex.semear_presenca(ex.aluno)
    ex.aluno.status = "inativo"
    assert [l[5] for l in ex.tabela()] == ["Presente"]


def test_ct619_chamada_sem_curso_so_tem_presentes(ex):
    """CT-619 | Chamada aberta para todos os cursos | só Presentes (não dá para saber quem faltou), nem os alunos sem curso viram 'Ausente'."""
    ex.novo_aluno(2)
    ex.novo_aluno(3, curso_id=None, periodo=None)                  # sem curso: "curso_id == NULL" não pode listar essa gente
    ex.semear_presenca(ex.aluno)
    assert [l[5] for l in ex.tabela()] == ["Presente"]


def test_ct619b_presentes_so_da_chamada_exportada(ex):
    """CT-619b | Presenças de OUTRAS chamadas | nunca entram no relatório desta."""
    outra = ex.nova_aula(ex.ana, titulo="Outra aula")
    ex.semear_presenca(ex.novo_aluno(2, nome="Daqui"))
    ex.semear_presenca(ex.novo_aluno(3, nome="De la"), aula=outra)
    assert [l[1] for l in ex.tabela()] == ["Daqui"]
    assert [l[1] for l in ex.tabela(aula=outra, p=ex.ana)] == ["De la"]


def test_ct620_ausentes_do_curso_depois_dos_presentes(ex):
    """CT-620 | Chamada de curso | linhas 'Ausente' depois das 'Presente', sem data e sem distância."""
    ex.curso_na_aula()
    z, b = ex.novo_aluno(2, nome="Zélia"), ex.novo_aluno(3, nome="Ana")
    ex.semear_presenca(ex.aluno)
    linhas = ex.tabela()
    assert [l[5] for l in linhas] == ["Presente", "Ausente", "Ausente"]
    assert [l[1] for l in linhas[1:]] == ["Ana", "Zélia"]
    assert linhas[1][6] == "" and linhas[1][7] == "" and linhas[1][4] == "2º"


def test_ct621_ausentes_so_ativos_do_curso_e_periodo(ex):
    """CT-621 | Ausentes: não entram inativos, nem de outro curso, nem de outro período (quando a chamada tem período)."""
    ex.curso_na_aula(periodo=2)
    outro = Curso(nome="Direito")
    ex.db.add(outro)
    ex.db.commit()
    ex.novo_aluno(2, nome="Inativo", status="inativo")
    ex.novo_aluno(3, nome="Outro curso", curso_id=outro.id)
    ex.novo_aluno(4, nome="Outro periodo", periodo=5)
    ex.novo_aluno(5, nome="Sem curso", curso_id=None)
    ex.semear_presenca(ex.aluno)
    assert [l[1] for l in ex.tabela() if l[5] == "Ausente"] == []
    ex.aula.periodo = None
    assert [l[1] for l in ex.tabela() if l[5] == "Ausente"] == ["Outro periodo"]


def test_ct622_aluno_de_outro_curso_que_registrou_e_presente(ex):
    """CT-622 | Quem registrou, mesmo de outro curso, aparece como Presente com o curso dele."""
    ex.curso_na_aula()
    outro = Curso(nome="Direito")
    ex.db.add(outro)
    ex.db.commit()
    visitante = ex.novo_aluno(2, curso_id=outro.id)
    ex.semear_presenca(visitante)
    linhas = ex.tabela()
    assert ("Direito", "Presente") in [(l[3], l[5]) for l in linhas]


def test_ct623_status_nulo_conta_como_ausente(ex):
    """CT-623 | Conta antiga com status nulo | é cobrada como ausente (como na tela de ausentes)."""
    ex.curso_na_aula()
    ex.aluno.status = None
    assert [l[5] for l in ex.tabela()] == ["Ausente"]


def test_ct624_presentes_e_ausentes_da_tela_batem_com_o_csv(ex):
    """CT-624 | O CSV e as abas 'Presentes' e 'Ausentes' mostram as mesmas pessoas."""
    ex.curso_na_aula()
    for i in range(2, 9):
        al = ex.novo_aluno(i)
        if i % 2:
            ex.semear_presenca(al)
    linhas = ex.tabela()
    tela_p = {x["matricula"] for x in ex.lista()[1]["presencas"]}
    tela_a = {x["matricula"] for x in ex.aus()[1]["itens"]}
    assert {l[0] for l in linhas if l[5] == "Presente"} == tela_p
    assert {l[0] for l in linhas if l[5] == "Ausente"} == tela_a
    assert len(linhas) == len(tela_p) + len(tela_a)


def test_ct625_dados_pessoais_minimos(ex):
    """CT-625 | Minimização (LGPD) | o CSV não leva IP, aparelho, coordenadas, alertas nem hash de senha."""
    ex.semear_presenca(ex.aluno, alertas="VPN detectada")
    _, _, texto = ex.baixar()
    for proibido in ("177.1.1.1", "VPN", "semente", "abcdef0123456789", "senha", "-23.", "-49.", "x"):
        if proibido == "x":
            assert ";x;" not in texto and '"x"' not in texto
        else:
            assert proibido not in texto
    assert ex.linhas(texto)[0] == ["Matrícula", "Nome do Aluno", "E-mail", "Curso", "Período", "Status",
                                   "Data/Hora do Check-in", "Distância (m)"]


# =====================================================================================================
# BLOCO C - Injeção de fórmula e caracteres especiais
# =====================================================================================================
@pytest.mark.parametrize("entrada,saida", [
    ("=1+1", "'=1+1"), ("+1", "'+1"), ("-1", "'-1"), ("@SUM(A1)", "'@SUM(A1)"),
    ("=HYPERLINK(\"http://x\",\"a\")", "'=HYPERLINK(\"http://x\",\"a\")"),
    ("\t=1+1", "'\t=1+1"), ("\r=1+1", "'\r=1+1"),
    ("Maria", "Maria"), ("João-Pedro", "João-Pedro"), ("a=b", "a=b"), ("1+1", "1+1"), ("Ana @ casa", "Ana @ casa"),
    ("", ""), (None, ""),
])
def test_ct630_seguro(ex, entrada, saida):
    """CT-630 | _seguro: texto que o Excel leria como fórmula (= + - @, TAB e CR) ganha apóstrofo; o resto fica igual."""
    assert ex.ns["_seguro"](entrada) == saida


@pytest.mark.parametrize("campo", ["nome", "email", "matricula"])
@pytest.mark.parametrize("prefixo", ["=", "+", "-", "@", "\t", "\r"])
def test_ct631_injecao_nos_campos_do_aluno(ex, campo, prefixo):
    """CT-631 | Dados do aluno que começam com caractere de fórmula | o CSV grava com apóstrofo na frente."""
    v = prefixo + "cmd|' /C calc'!A0"
    al = ex.novo_aluno(2, **{campo: v})
    ex.semear_presenca(al)
    (linha,) = ex.tabela()
    col = {"nome": 1, "email": 2, "matricula": 0}[campo]
    assert linha[col] == "'" + v


def test_ct632_injecao_no_nome_do_curso(ex):
    """CT-632 | Nome de curso começando com '=' | também protegido (Presente e Ausente)."""
    ex.curso.nome = "=1+1"
    ex.curso_na_aula()
    ex.semear_presenca(ex.aluno)
    ex.novo_aluno(2)
    assert {l[3] for l in ex.tabela()} == {"'=1+1"}


def test_ct633_aspas_ponto_e_virgula_e_quebra_de_linha(ex):
    """CT-633 | Nome com aspas, ';' e quebra de linha | o CSV continua com 8 colunas por linha e o texto volta igual."""
    nome = 'Ana "Nana"; Dias\nSilva'
    ex.semear_presenca(ex.novo_aluno(2, nome=nome))
    (linha,) = ex.tabela()
    assert len(linha) == 8 and linha[1] == nome


def test_ct634_acentos_e_emoji(ex):
    """CT-634 | Acentos e emoji no nome | preservados em UTF-8 (o BOM faz o Excel ler certo)."""
    ex.semear_presenca(ex.novo_aluno(2, nome="José Antônio D'Ávila 😀"))
    assert ex.tabela()[0][1] == "José Antônio D'Ávila 😀"


# =====================================================================================================
# BLOCO D - Streaming, volume e sessão própria
# =====================================================================================================
def test_ct640_volume_em_lotes_de_500(ex):
    """CT-640 | 1.100 presentes + 600 ausentes | nenhum pedaço passa de 500 linhas, ninguém repete nem some."""
    ex.curso_na_aula()
    ex.aluno.status = "inativo"                       # tira o aluno do contexto da conta
    ex.povoar(1100, 600)
    resp, pedacos, texto = ex.baixar()
    assert pedacos[0].startswith("﻿") and len(pedacos) == 1 + 3 + 2
    for p in pedacos[1:]:
        assert 0 < len(list(csv.reader(io.StringIO(p), delimiter=";"))) <= 500
    linhas = ex.linhas(texto)[1:]
    assert len(linhas) == 1700 and len({l[0] for l in linhas}) == 1700
    assert [l[5] for l in linhas].count("Presente") == 1100 and [l[5] for l in linhas].count("Ausente") == 600


def test_ct641_lote_exato_nao_gera_pedaco_vazio(ex):
    """CT-641 | Exatamente 500 presentes | um pedaço só (sem pedaço vazio no fim)."""
    ex.aluno.status = "inativo"
    ex.povoar(500, 0)
    _, pedacos, _ = ex.baixar()
    assert len(pedacos) == 2 and all(p.strip() for p in pedacos)


def test_ct642_chamada_vazia_so_cabecalho(ex):
    """CT-642 | Chamada sem ninguém | só o cabeçalho (arquivo válido, não erro)."""
    _, pedacos, texto = ex.baixar()
    assert texto == "﻿" + CABECALHO and pedacos == [texto]


def test_ct643_cabecalho_sai_antes_de_abrir_o_banco(ex):
    """CT-643 | O primeiro pedaço (cabeçalho) sai sem tocar o banco | o download começa na hora e a sessão só abre depois."""
    st, resp = ex.exportar()
    it = iter(resp.body_iterator)
    assert next(it) == "﻿" + CABECALHO and ex.sessoes == []
    list(it)
    assert len(ex.sessoes) == 1


def test_ct644_sessao_propria_e_fechada_ao_terminar(ex):
    """CT-644 | O stream usa sessão própria (a da rota já terminou) e a fecha no fim."""
    ex.semear_presenca(ex.aluno)
    rota = FakeDB(tables=ex.db.tables)
    rota.relogio = ex.db.relogio
    st, resp = ex.exportar(db=rota)
    def proibido(*a, **k):
        raise AssertionError("o stream não pode usar a sessão da rota")
    rota.execute = proibido
    list(resp.body_iterator)
    assert len(ex.sessoes) == 1 and ex.sessoes[0].fechada is True


def test_ct645_sessao_fecha_se_o_cliente_desiste(ex):
    """CT-645 | Cliente cancela o download no meio | a sessão do banco é devolvida (não vaza conexão)."""
    ex.aluno.status = "inativo"
    ex.povoar(1200, 0)
    st, resp = ex.exportar()
    it = iter(resp.body_iterator)
    next(it)
    next(it)                                                 # primeiro lote de dados
    assert ex.sessoes and ex.sessoes[0].fechada is False
    it.close()
    assert ex.sessoes[0].fechada is True


def test_ct646_sessao_fecha_se_o_banco_falha(ex):
    """CT-646 | Erro do banco no meio do stream | a exceção sobe, mas a sessão é fechada."""
    ex.semear_presenca(ex.aluno)
    st, resp = ex.exportar()

    def quebrar(self):
        raise OSError("conexão perdida")
    orig = FakeDB.execute
    FakeDB.execute = lambda self, q: quebrar(self)
    try:
        with pytest.raises(OSError):
            list(resp.body_iterator)
    finally:
        FakeDB.execute = orig
    assert ex.sessoes[0].fechada is True


def test_ct647_leitura_em_blocos_pede_yield_per_500(ex):
    """CT-647 | A leitura do banco é feita em blocos de 500 (yield_per), não carregando tudo na memória."""
    ex.curso_na_aula()
    ex.semear_presenca(ex.aluno)
    ex.novo_aluno(2)
    pedidos = []
    orig = Select.execution_options

    def espiar(self, **kw):
        pedidos.append(kw)
        return orig(self, **kw)
    Select.execution_options = espiar
    try:
        ex.baixar()
    finally:
        Select.execution_options = orig
    assert pedidos == [{"yield_per": 500}, {"yield_per": 500}]


# =====================================================================================================
# BLOCO E - Auditoria da exportação
# =====================================================================================================
def test_ct650_exportacao_gera_log_completo(ex):
    """CT-650 | Exportar grava: quem, perfil, aula, título e IP."""
    ex.exportar(ip="177.5.5.5")
    (log,) = ex.logs("exportar_chamada")
    assert log.usuario_id == ex.prof.id and log.ip == "177.5.5.5"
    assert log.detalhe == f"perfil=professor; aula={ex.aula.id}; titulo=Algoritmos"
    assert log.data_hora is not None


def test_ct651_log_sai_antes_do_download_e_a_cada_download(ex):
    """CT-651 | O registro é gravado ao pedir o arquivo e cada download gera um novo (rastreabilidade)."""
    ex.exportar()
    assert len(ex.logs("exportar_chamada")) == 1        # antes de consumir o stream
    ex.baixar()
    ex.baixar()
    assert len(ex.logs("exportar_chamada")) == 3


def test_ct652_ip_do_log_e_o_do_cliente_nao_o_forjado(ex):
    """CT-652 | X-Forwarded-For forjado pelo cliente | o IP do log vem do proxy confiável, não do cabeçalho inteiro."""
    ex.exportar(ip="10.0.0.1", xff="1.2.3.4, 177.5.5.5")
    (log,) = ex.logs("exportar_chamada")
    assert log.ip == ex.ns["ip_cliente"](FakeReq(ip="10.0.0.1", xff="1.2.3.4, 177.5.5.5"))
    assert ", " not in (log.ip or "")


def test_ct653_titulo_enorme_e_cortado_no_log(ex):
    """CT-653 | Título longo | o log guarda no máximo 300 caracteres e a exportação funciona."""
    ex.aula.titulo = "T" * 400
    assert ex.exportar()[0] == 200
    assert len(ex.logs("exportar_chamada")[0].detalhe) == 300


def test_ct654_falha_ao_gravar_log_nao_derruba_a_exportacao(ex):
    """CT-654 | O banco falha ao gravar o log | a exportação continua (log_seg com commit=True engole o erro)."""
    ex.semear_presenca(ex.aluno)
    original = ex.db.commit

    def falhar():
        ex.db.commit = original
        raise OSError("disco cheio")
    ex.db.commit = falhar
    resp, _, texto = ex.baixar()
    assert len(ex.linhas(texto)) == 2


def test_ct655_exportacao_negada_nao_deixa_log(ex):
    """CT-655 | Tentativa negada não vira 'exportou' no log."""
    ex.exportar(p=ex.ana)
    assert status_de(ex.ns["_prof_ou_admin"], {"id": 1, "role": "aluno", "nome": "X"})[0] == 403
    assert ex.logs("exportar_chamada") == []


# =====================================================================================================
# BLOCO F - Diagnóstico de IP
# =====================================================================================================
def test_ct660_meu_ip_so_professor_ou_coordenacao(ex):
    """CT-660 | /api/meu-ip: aluno 403; professor e coordenação veem os dados de calibragem."""
    assert status_de(ex.ns["_prof_ou_admin"], {"id": 1, "role": "aluno", "nome": "X"})[0] == 403
    st, r = status_de(ex.ns["meu_ip"], FakeReq(ip="177.9.9.9"), ex.eu())
    assert st == 200 and r["ip_detectado"] == "177.9.9.9"
    assert set(r) == {"ip_detectado", "x_forwarded_for", "proxy_hops", "vpn_modo", "verificacao_ativa", "ip_e_vpn"}
    assert r["verificacao_ativa"] is False


def test_ct661_meu_ip_nunca_devolve_a_chave_do_provedor(ex):
    """CT-661 | Com PROXYCHECK_KEY configurada | a resposta diz só se a verificação está ativa, nunca a chave."""
    e = CtxEx(PROXYCHECK_KEY="chave-secreta-123")
    st, r = status_de(e.ns["meu_ip"], FakeReq(ip="177.9.9.9"), e.eu())
    assert r["verificacao_ativa"] is True and "chave-secreta-123" not in json.dumps(r, default=str)


# =====================================================================================================
# BLOCO G - Alertas de check-in suspeito (coordenação)
# =====================================================================================================
def test_ct670_so_coordenacao_ve_alertas_e_auditoria(ex):
    """CT-670 | Professor e aluno nas telas de alertas e auditoria | 403."""
    for papel in ("professor", "aluno"):
        u = {"id": 1, "role": papel, "nome": "X"}
        assert status_de(ex.ns["so_admin"], u)[0] == 403
    assert status_de(ex.ns["so_admin"], ex.eu_admin())[0] == 200


def test_ct671_alertas_so_com_sinal_de_risco(ex):
    """CT-671 | Só entram presenças COM alerta; check-in limpo fica de fora; campos completos."""
    limpo, suspeito = ex.novo_aluno(2), ex.novo_aluno(3)
    ex.semear_presenca(limpo)
    p = ex.semear_presenca(suspeito, alertas="VPN/proxy detectado", dist=33.33)
    st, r = ex.alertas()
    assert st == 200 and len(r) == 1
    assert r[0] == {"id": p.id, "aluno": "Aluno 3", "email": "aluno3@alunos.unicerrado.edu.br", "aula": "Algoritmos",
                    "alertas": "VPN/proxy detectado", "ip": "177.1.1.1", "distancia_m": 33.3,
                    "registrado_em": ex.ns["iso"](p.registrado_em)}


def test_ct672_alertas_mais_novos_primeiro_e_teto_de_100(ex):
    """CT-672 | 110 alertas | volta no máximo 100, do mais novo para o mais antigo."""
    ex.povoar(110, 0, prefixo="W")
    for p in ex.db.tables[Presenca]:
        p.alertas = "GPS suspeito"
    ids = [p.id for p in ex.db.tables[Presenca]]
    r = ex.alertas()[1]
    assert [x["id"] for x in r] == sorted(ids, reverse=True)[:100]


def test_ct673_check_in_real_gera_alerta_so_quando_deve(ex):
    """CT-673 | Fluxo real: VPN em modo 'avisar' gera alerta; check-in normal não."""
    e = CtxEx(VPN_MODO="avisar", PROXYCHECK_KEY="k")
    limpo, vpn = e.novo_aluno(2), e.novo_aluno(3)
    e.ligar_vpn(e.resp_proxy("177.7.7.7", False))
    st, _ = e.registrar(limpo, e.aula, ip="177.7.7.7", device_id=dev(2))
    assert st == 200
    assert e.alertas()[1] == []
    e.ligar_vpn(e.resp_proxy("177.8.8.8", True))
    st, _ = e.registrar(vpn, e.aula, ip="177.8.8.8", device_id=dev(3))
    assert st == 200
    r = e.alertas()[1]
    assert len(r) == 1 and r[0]["aluno"] == "Aluno 3" and "VPN" in r[0]["alertas"]


# =====================================================================================================
# BLOCO H - Trilha de auditoria
# =====================================================================================================
def test_ct680_auditoria_do_mais_novo_para_o_mais_antigo_com_nome(ex):
    """CT-680 | Trilha: id, usuário (nome pelo cadastro), ação, detalhe, IP e data; mais novo primeiro."""
    ex.exportar(ip="177.5.5.5")
    ex.exportar(admin=True, ip="177.6.6.6")
    st, r = ex.auditoria()
    assert st == 200 and [x["acao"] for x in r] == ["exportar_chamada", "exportar_chamada"]
    assert r[0]["usuario"] == "Coord Geral" and r[0]["ip"] == "177.6.6.6" and r[1]["usuario"] == "Carlos Lima"
    assert r[0]["id"] > r[1]["id"]
    assert set(r[0]) == {"id", "usuario_id", "usuario", "acao", "detalhe", "ip", "data_hora"}
    assert r[0]["data_hora"]


def test_ct681_auditoria_teto_de_150(ex):
    """CT-681 | 200 registros | a tela traz os 150 mais recentes."""
    for i in range(200):
        ex.db.add(LogSeguranca(usuario_id=ex.coord.id, acao="x", detalhe=str(i)))
    ex.db.commit()
    r = ex.auditoria()[1]
    assert len(r) == 150 and r[0]["detalhe"] == "199" and r[-1]["detalhe"] == "50"


def test_ct682_log_sem_usuario_ou_de_professor_excluido(ex):
    """CT-682 | Log sem usuário (falha de login) ou de professor que foi excluído | continua aparecendo, com nome vazio."""
    ex.db.add(LogSeguranca(usuario_id=None, acao="login_admin_falha", detalhe="x@x", ip="177.1.1.1"))
    ex.db.add(LogSeguranca(usuario_id=999, acao="criar_curso", detalhe="Direito", ip="177.1.1.2"))
    ex.db.commit()
    r = ex.auditoria()[1]
    assert [(x["acao"], x["usuario"]) for x in r] == [("criar_curso", None), ("login_admin_falha", None)]


def test_ct683_log_seg_corta_campos_e_aceita_vazio(ex):
    """CT-683 | log_seg: ação até 40, detalhe até 300, IP até 64; detalhe/IP vazios viram nulos."""
    ex.ns["log_seg"](ex.db, ex.prof.id, "a" * 80, "d" * 500, "9" * 100, commit=True)
    ex.ns["log_seg"](ex.db, None, "vazio", "", "", commit=True)
    a, b = ex.db.tables[LogSeguranca]
    assert len(a.acao) == 40 and len(a.detalhe) == 300 and len(a.ip) == 64
    assert b.detalhe is None and b.ip is None


def test_ct684_log_seg_com_commit_nao_derruba_a_requisicao(ex):
    """CT-684 | Falha ao comitar o log | log_seg não propaga o erro e faz rollback."""
    def falhar():
        raise OSError("sem espaço")
    ex.db.commit = falhar
    ex.ns["log_seg"](ex.db, 1, "x", "y", "z", commit=True)       # não levanta
    assert ex.db.pending == []


# =====================================================================================================
# BLOCO I - Cobertura da trilha (análise estática do app.py)
# =====================================================================================================
def _funcoes_do_app():
    arvore = _ast.parse(APP.read_text(encoding="utf-8"))
    return [n for n in arvore.body if isinstance(n, _ast.FunctionDef)]


def _chama(fn, nome):
    return any(isinstance(n, _ast.Call) and isinstance(n.func, _ast.Name) and n.func.id == nome for n in _ast.walk(fn))


def _apaga(fn, modelos):
    """A função tem delete(Modelo)... para algum dos modelos."""
    for n in _ast.walk(fn):
        if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Name) and n.func.id == "delete" and n.args:
            a = n.args[0]
            if isinstance(a, _ast.Name) and a.id in modelos:
                return True
    return False


def test_ct690_toda_exclusao_de_cadastro_ou_evento_e_auditada():
    """CT-690 | Rotas que apagam Aula, Professor, Aluno ou Matrícula autorizada | todas chamam log_seg (trilha completa)."""
    sem_log = [f.name for f in _funcoes_do_app()
               if _apaga(f, {"Aula", "Professor", "Aluno", "MatriculasAutorizadas"}) and not _chama(f, "log_seg")]
    assert sem_log == [], f"exclusões sem registro de auditoria: {sem_log}"


def test_ct691_toda_exportacao_de_dados_e_auditada():
    """CT-691 | Rotas que devolvem CSV/relatório para download | todas passam por log_seg."""
    exportam = [f.name for f in _funcoes_do_app() if _chama(f, "StreamingResponse")]
    sem_log = [n for n in exportam if not _chama(next(f for f in _funcoes_do_app() if f.name == n), "log_seg")]
    assert exportam and sem_log == [], f"exportações sem auditoria: {sem_log}"


def test_ct692_acoes_de_conta_da_coordenacao_sao_auditadas():
    """CT-692 | Rotas da coordenação que mudam permissões, senhas ou cursos | todas chamam log_seg."""
    sensiveis = {"adm_professor_autorizar", "adm_professor_recusar", "adm_professor_ativo", "adm_professor_coordenacao",
                 "adm_professor_novo", "adm_aluno_editar", "adm_senha", "adm_minha_senha", "adm_curso_novo",
                 "adm_curso_ativo", "adm_matriculas_importar", "adm_matriculas_arquivo", "adm_matricula_excluir",
                 "adm_aula_excluir", "adm_professor_excluir", "adm_aluno_excluir", "admin_login", "admin_registro"}
    por_nome = {f.name: f for f in _funcoes_do_app()}
    faltando = sorted(n for n in sensiveis if n in por_nome and not _chama(por_nome[n], "log_seg"))
    ausentes = sorted(n for n in sensiveis if n not in por_nome)
    assert ausentes == [], f"rotas esperadas não encontradas: {ausentes}"
    assert faltando == [], f"ações sensíveis sem auditoria: {faltando}"
