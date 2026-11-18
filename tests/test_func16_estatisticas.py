# -*- coding: utf-8 -*-
"""
QA - Funcionalidade 16: ESTATÍSTICAS DA COORDENAÇÃO (painel e planilha XLSX)
Só a coordenação. Números por período (datas de Brasília, por data de abertura do evento) e por curso: eventos, presenças, alunos que
participaram, adesão (participantes / alunos ativos), média por evento, presenças lançadas pelo professor e tentativas recusadas; quebras
por curso, professor e mês. A planilha traz as mesmas contas mais a lista completa de eventos e vai para a auditoria.
Os agrupamentos (JOIN + GROUP BY + COUNT) rodam aqui num banco de mentira: o SQL em si precisa ser conferido no SQLAlchemy/PostgreSQL de verdade.
Uso:  pytest tests/test_func16_estatisticas.py -v
"""
from datetime import datetime as _dt, timedelta as _td, timezone as _tz

from harness_app import *  # noqa: F401,F403
from test_func4_presenca import LAT, LON
from test_func6_exportacao_auditoria import CtxEx

openpyxl = pytest.importorskip("openpyxl")
UTC = _tz.utc


def dia(a, m, d, h=15):
    return _dt(a, m, d, h, 0, 0, tzinfo=UTC)


@pytest.fixture
def es():
    """Dois cursos, dois professores, três meses de eventos. 'Hoje' é 20/11/2026."""
    c = CtxEx()
    c.agora = dia(2026, 11, 20)
    c.aula.ativa, c.aula.aberta_em = False, dia(2020, 1, 1)        # o evento do fixture fica fora de todos os períodos
    c.direito = Curso(nome="Direito")
    c.db.add(c.direito)
    c.db.commit()
    # alunos: 4 da TI (1 inativo), 3 de Direito, 1 sem curso
    c.ti = [c.novo_aluno(i) for i in range(2, 6)]
    c.ti[3].status = "inativo"
    c.dir = [c.novo_aluno(i, curso_id=c.direito.id) for i in range(10, 13)]
    c.sem = c.novo_aluno(20, curso_id=None)
    c.aluno.status = "inativo"                                    # o aluno do fixture (id 1) não conta
    # eventos
    c.e_out = c.evento(c.prof, "Aula de Outubro", dia(2026, 10, 5), c.curso.id)
    c.e_nov1 = c.evento(c.prof, "Palestra de Novembro", dia(2026, 11, 3), c.curso.id, tipo="palestra")
    c.e_nov2 = c.evento(c.ana, "Aula de Direito", dia(2026, 11, 10), c.direito.id)
    c.e_todos = c.evento(c.ana, "Semana Acadêmica", dia(2026, 11, 12), None, tipo="palestra")
    c.e_velho = c.evento(c.prof, "Aula de Julho", dia(2026, 7, 1), c.curso.id)
    return c


def _evento(self, prof, titulo, quando, curso_id, tipo="aula"):
    a = Aula(professor_id=prof.id, titulo=titulo, latitude=LAT, longitude=LON, raio_metros=50.0, tipo=tipo, local_tipo="dentro",
             ativa=False, curso_id=curso_id, aberta_em=quando)
    self.db.add(a)
    self.db.commit()
    return a


def _presente(self, aluno, aula, manual=False):
    p = Presenca(aula_id=aula.id, aluno_id=aluno.id, latitude=LAT, longitude=LON, distancia_m=5.0, precisao_m=10.0,
                 device_id=f"d-{aula.id}-{aluno.id}-0123456789", fp_hash="abcdef0123456789", ip="177.1.1.1", manual=manual)
    self.db.add(p)
    self.db.commit()


CtxEx.evento, CtxEx.presente = _evento, _presente


def stats(c, de="", ate="", curso_id=0, admin=True):
    u = c.eu_admin() if admin else c.eu()
    return status_de(c.ns["adm_estatisticas"], de, ate, curso_id, u, c.db)


def popular(c):
    for a in c.ti[:3]:
        c.presente(a, c.e_out)
    c.presente(c.ti[0], c.e_nov1)
    c.presente(c.ti[1], c.e_nov1, manual=True)
    for a in c.dir:
        c.presente(a, c.e_nov2)
    c.presente(c.sem, c.e_todos)
    c.presente(c.ti[0], c.e_todos)
    c.presente(c.ti[2], c.e_velho)


# --------------------------------------------------------------------------- acesso
@pytest.mark.parametrize("papel", ["aluno", "professor"])
def test_ct1801_so_a_coordenacao(es, papel):
    assert status_de(es.ns["so_admin"], {"id": 1, "role": papel, "nome": "X"})[0] == 403


def test_ct1802_rotas_exigem_so_admin():
    import ast as _ast
    from pathlib import Path
    achadas = {}
    for n in _ast.parse((Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")).body:
        if isinstance(n, _ast.FunctionDef) and n.name in ("adm_estatisticas", "adm_estatisticas_xlsx"):
            achadas[n.name] = [x.args[0].id for x in _ast.walk(n.args) if isinstance(x, _ast.Call) and getattr(x.func, "id", "") == "Depends"
                               and x.args and isinstance(x.args[0], _ast.Name) and x.args[0].id != "get_db"]
    assert achadas == {"adm_estatisticas": ["so_admin"], "adm_estatisticas_xlsx": ["so_admin"]}


# --------------------------------------------------------------------------- período
def test_ct1803_sem_datas_usa_os_ultimos_180_dias(es):
    popular(es)
    st, r = stats(es)
    assert st == 200 and r["ate"] == "2026-11-20" and r["de"] == "2026-05-24"
    assert r["resumo"]["eventos"] == 5, "o evento de julho entra (está nos 180 dias)"


def test_ct1804_periodo_filtra_pela_data_de_abertura_do_evento(es):
    popular(es)
    r = stats(es, "2026-11-01", "2026-11-30")[1]
    assert r["resumo"]["eventos"] == 3 and r["resumo"]["presencas"] == 2 + 3 + 2
    assert [m["mes"] for m in r["por_mes"]] == ["2026-11"]


def test_ct1805_data_final_e_inclusiva_e_a_virada_do_dia_e_de_brasilia(es):
    q = es.evento(es.prof, "Noite", _dt(2026, 12, 1, 2, 30, tzinfo=UTC), es.curso.id)       # 23:30 de 30/11 em Brasília
    es.presente(es.ti[0], q)
    assert stats(es, "2026-11-30", "2026-11-30")[1]["resumo"]["eventos"] == 1
    assert stats(es, "2026-12-01", "2026-12-01")[1]["resumo"]["eventos"] == 0
    meia_noite = es.evento(es.prof, "Meia-noite", _dt(2026, 11, 30, 3, 0, tzinfo=UTC), es.curso.id)   # 00:00 de 30/11 em Brasília
    assert stats(es, "2026-11-30", "2026-11-30")[1]["resumo"]["eventos"] == 2 and meia_noite.id
    es.evento(es.prof, "Virada", _dt(2026, 12, 1, 3, 0, tzinfo=UTC), es.curso.id)               # 00:00 de 01/12 em Brasília
    assert stats(es, "2026-11-30", "2026-11-30")[1]["resumo"]["eventos"] == 2, "o instante do fim pertence ao dia seguinte"
    assert stats(es, "2026-12-01", "2026-12-01")[1]["resumo"]["eventos"] == 1


@pytest.mark.parametrize("de,ate", [("2026-13-01", ""), ("ontem", ""), ("", "31/10/2026"), ("2026-11-05", "2026-11-04"), ("2000-01-01", "2026-11-20")])
def test_ct1806_datas_invalidas_ou_periodo_enorme(es, de, ate):
    assert stats(es, de, ate)[0] == 422


def test_ct1807_periodo_vazio_nao_quebra_e_nao_divide_por_zero(es):
    r = stats(es, "2025-01-01", "2025-01-31")[1]
    assert r["resumo"]["eventos"] == 0 and r["resumo"]["presencas"] == 0 and r["resumo"]["media_por_evento"] == 0
    assert r["eventos"] == [] and r["por_mes"] == [] and r["por_professor"] == [] and r["truncado"] is False


# --------------------------------------------------------------------------- os números
def test_ct1808_resumo_do_periodo(es):
    popular(es)
    r = stats(es, "2026-10-01", "2026-11-30")[1]["resumo"]
    assert r["eventos"] == 4 and r["presencas"] == 3 + 2 + 3 + 2
    assert r["participantes"] == 3 + 3 + 1 - 0, "ti[0..2] (3), os 3 de Direito, e o aluno sem curso"
    assert r["alunos_ativos"] == 3 + 3 + 1, "3 da TI ativos (o 4º e o do fixture estão inativos), 3 de Direito, 1 sem curso"
    assert r["adesao_pct"] == 100.0 and r["media_por_evento"] == 2.5
    assert r["manuais"] == 1


def test_ct1809_adesao_com_quem_nao_participou(es):
    es.presente(es.ti[0], es.e_out)
    r = stats(es, "2026-10-01", "2026-10-31")[1]["resumo"]
    assert (r["participantes"], r["alunos_ativos"], r["adesao_pct"]) == (1, 7, 14.3)


def test_ct1810_aluno_inativo_conta_como_participante_mas_nao_como_ativo(es):
    es.presente(es.ti[3], es.e_out)                  # inativo hoje, mas esteve no evento
    r = stats(es, "2026-10-01", "2026-10-31")[1]["resumo"]
    assert r["participantes"] == 1 and r["alunos_ativos"] == 7


def test_ct1811_tentativas_recusadas_so_do_periodo(es):
    es.semear_tentativa(es.ti[0], aula=es.e_out)
    es.semear_tentativa(es.ti[1], aula=es.e_out)
    es.semear_tentativa(es.ti[0], aula=es.e_velho)
    assert stats(es, "2026-10-01", "2026-10-31")[1]["resumo"]["tentativas_recusadas"] == 2


def test_ct1812_por_curso(es):
    popular(es)
    r = stats(es, "2026-10-01", "2026-11-30")[1]
    por = {c["curso"]: c for c in r["por_curso"]}
    assert por["Gestão da TI"] == {"curso": "Gestão da TI", "alunos_ativos": 3, "participantes": 3, "presencas": 3 + 2 + 1, "adesao_pct": 100.0}
    assert por["Direito"]["participantes"] == 3 and por["Direito"]["presencas"] == 3 and por["Direito"]["adesao_pct"] == 100.0
    assert por["Sem curso"]["alunos_ativos"] == 1 and por["Sem curso"]["presencas"] == 1
    assert [c["curso"] for c in r["por_curso"]][0] == "Gestão da TI", "ordenado por presenças"


def test_ct1813_por_professor(es):
    popular(es)
    r = stats(es, "2026-10-01", "2026-11-30")[1]
    por = {p["professor"]: p for p in r["por_professor"]}
    assert por["Carlos Lima"] == {"professor": "Carlos Lima", "eventos": 2, "presencas": 3 + 2}
    assert por["Ana Prof"] == {"professor": "Ana Prof", "eventos": 2, "presencas": 3 + 2}


def test_ct1814_por_mes_em_ordem(es):
    popular(es)
    r = stats(es)[1]
    assert [(m["mes"], m["eventos"], m["presencas"]) for m in r["por_mes"]] == [("2026-07", 1, 1), ("2026-10", 1, 3), ("2026-11", 3, 7)]


def test_ct1815_lista_de_eventos_do_painel_tem_so_os_15_mais_novos(es):
    for i in range(20):
        es.evento(es.prof, f"Extra {i}", dia(2026, 11, 1) + _td(minutes=i), es.curso.id)
    r = stats(es, "2026-11-01", "2026-11-01")[1]
    assert len(r["eventos"]) == 15 and r["resumo"]["eventos"] == 20, "o resumo conta todos; a lista mostra 15"
    assert r["eventos"][0]["titulo"] == "Extra 19"


def test_ct1816_evento_para_todos_os_cursos_aparece_assim(es):
    popular(es)
    ev = {e["titulo"]: e for e in stats(es, "2026-11-01", "2026-11-30")[1]["eventos"]}
    assert ev["Semana Acadêmica"]["curso"] == "Todos os cursos" and ev["Semana Acadêmica"]["presencas"] == 2
    assert ev["Palestra de Novembro"]["manuais"] == 1 and ev["Palestra de Novembro"]["tipo"] == "palestra"


def test_ct1817_evento_sem_presenca_aparece_com_zero(es):
    r = stats(es, "2026-10-01", "2026-10-31")[1]
    assert [(e["titulo"], e["presencas"]) for e in r["eventos"]] == [("Aula de Outubro", 0)]


# --------------------------------------------------------------------------- filtro por curso
def test_ct1818_filtro_por_curso(es):
    popular(es)
    r = stats(es, "2026-10-01", "2026-11-30", es.direito.id)[1]
    assert r["curso_id"] == es.direito.id and r["resumo"]["eventos"] == 1 and r["resumo"]["presencas"] == 3
    assert r["resumo"]["alunos_ativos"] == 3 and r["resumo"]["adesao_pct"] == 100.0
    assert [c["curso"] for c in r["por_curso"]] == ["Direito"]
    assert [e["titulo"] for e in r["eventos"]] == ["Aula de Direito"]


def test_ct1819_curso_sem_eventos(es):
    outro = Curso(nome="Pedagogia")
    es.db.add(outro)
    es.db.commit()
    r = stats(es, curso_id=outro.id)[1]
    assert r["resumo"]["eventos"] == 0 and r["resumo"]["alunos_ativos"] == 0 and r["resumo"]["adesao_pct"] is None


# --------------------------------------------------------------------------- limite
def test_ct1820_passou_do_limite_avisa_que_cortou(es):
    es.ns["LIMITE_ESTATISTICAS"] = 3
    r = stats(es, "2026-01-01", "2026-12-31")[1]
    assert r["truncado"] is True and r["resumo"]["eventos"] == 3 and r["limite"] == 3
    assert [e["titulo"] for e in r["eventos"]] == ["Semana Acadêmica", "Aula de Direito", "Palestra de Novembro"], "os mais recentes ficam"


# --------------------------------------------------------------------------- planilha
def baixar(c, de="", ate="", curso_id=0):
    st, resp = status_de(c.ns["adm_estatisticas_xlsx"], FakeReq("177.9.9.9"), de, ate, curso_id, c.eu_admin(), c.db)
    assert st == 200, (st, resp)
    return resp, openpyxl.load_workbook(io.BytesIO(resp.body))


def test_ct1821_planilha_tem_as_cinco_abas_e_o_tipo_certo(es):
    popular(es)
    resp, wb = baixar(es, "2026-10-01", "2026-11-30")
    assert resp.media_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert resp.headers["Content-Disposition"] == 'attachment; filename="estatisticas-2026-10-01-a-2026-11-30.xlsx"'
    assert resp.headers["Cache-Control"] == "no-store" and resp.body[:2] == b"PK"
    assert wb.sheetnames == ["Resumo", "Por curso", "Por professor", "Por mês", "Eventos"]


def test_ct1822_resumo_da_planilha_bate_com_o_painel(es):
    popular(es)
    _, wb = baixar(es, "2026-10-01", "2026-11-30")
    linhas = {l[0]: l[1] for l in wb["Resumo"].iter_rows(values_only=True)}
    painel = stats(es, "2026-10-01", "2026-11-30")[1]["resumo"]
    assert linhas["Eventos"] == painel["eventos"] and linhas["Presenças"] == painel["presencas"]
    assert linhas["Alunos que participaram"] == painel["participantes"] and linhas["Alunos ativos"] == painel["alunos_ativos"]
    assert linhas["Adesão (%)"] == painel["adesao_pct"] and linhas["Curso"] == "Todos"
    assert linhas["Período (datas de Brasília)"] == "2026-10-01 a 2026-11-30"


def test_ct1823_aba_eventos_traz_todos_e_nao_so_os_15(es):
    for i in range(20):
        es.evento(es.prof, f"Extra {i}", dia(2026, 11, 1) + _td(minutes=i), es.curso.id)
    _, wb = baixar(es, "2026-11-01", "2026-11-30")
    linhas = list(wb["Eventos"].iter_rows(values_only=True))
    assert linhas[0] == ("Nº", "Evento", "Tipo", "Data", "Professor", "Curso", "Presenças", "Lançadas pelo professor")
    assert len(linhas) - 1 == 23


def test_ct1824_data_do_evento_na_planilha_e_horario_de_brasilia(es):
    es.evento(es.prof, "Noite", _dt(2026, 12, 1, 2, 30, tzinfo=UTC), es.curso.id)
    _, wb = baixar(es, "2026-11-30", "2026-11-30")
    assert list(wb["Eventos"].iter_rows(values_only=True))[1][3] == "30/11/2026 23:30"


def test_ct1825_planilha_filtrada_por_curso(es):
    popular(es)
    _, wb = baixar(es, "2026-10-01", "2026-11-30", es.direito.id)
    assert {l[0]: l[1] for l in wb["Resumo"].iter_rows(values_only=True)}["Curso"] == "Direito"
    assert [l[1] for l in list(wb["Eventos"].iter_rows(values_only=True))[1:]] == ["Aula de Direito"]


def test_ct1826_titulo_com_formula_nao_executa_no_excel(es):
    es.evento(es.prof, "=HYPERLINK(\"http://x\",\"clique\")", dia(2026, 11, 2), es.curso.id)
    _, wb = baixar(es, "2026-11-02", "2026-11-02")
    titulo = list(wb["Eventos"].iter_rows(values_only=True))[1][1]
    assert titulo.startswith("'=") and wb["Eventos"]["B2"].data_type == "s"


def test_ct1827_exportar_vai_para_a_auditoria(es):
    popular(es)
    baixar(es, "2026-10-01", "2026-11-30", es.direito.id)
    (log,) = es.logs("exportar_estatisticas")
    assert log.usuario_id == es.coord.id and log.ip == "177.9.9.9"
    assert "2026-10-01..2026-11-30" in log.detalhe and f"curso={es.direito.id}" in log.detalhe and "eventos=1" in log.detalhe


def test_ct1828_periodo_invalido_na_planilha_da_422_sem_auditoria(es):
    st, _ = status_de(es.ns["adm_estatisticas_xlsx"], FakeReq("177.9.9.9"), "x", "", 0, es.eu_admin(), es.db)
    assert st == 422 and es.logs("exportar_estatisticas") == []


def test_ct1829_planilha_sem_dados_continua_valida(es):
    _, wb = baixar(es, "2025-01-01", "2025-01-31")
    assert len(list(wb["Eventos"].iter_rows(values_only=True))) == 1
    assert {l[0]: l[1] for l in wb["Resumo"].iter_rows(values_only=True)}["Eventos"] == 0


def test_ct1830_sem_dados_pessoais_alem_do_nome_do_professor(es):
    popular(es)
    resp, wb = baixar(es, "2026-10-01", "2026-11-30")
    texto = json.dumps([list(l) for ws in wb for l in ws.iter_rows(values_only=True)], ensure_ascii=False, default=str)
    for a in es.ti + es.dir:
        assert a.nome not in texto and a.email not in texto and a.matricula not in texto, "a planilha é agregada: não traz alunos"
    r = stats(es, "2026-10-01", "2026-11-30")[1]
    assert "email" not in json.dumps(r) and "matricula" not in json.dumps(r)
