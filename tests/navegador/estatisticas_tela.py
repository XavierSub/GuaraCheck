"""Estatísticas da coordenação no navegador (CT-1831 a CT-1850).
Aba "Estatísticas": números do período, barras por mês, quebras por curso/professor, lista de eventos; filtros (datas e curso) chegam ao servidor
na consulta; "Baixar planilha (XLSX)" baixa o arquivo do mesmo período; erro de data do servidor aparece sem apagar a tela; período vazio e
corte por limite; nomes maliciosos aparecem como texto. A API é simulada (imita o FastAPI); o servidor real não roda aqui.
Uso:  pip install playwright && playwright install chromium && python tests/navegador/estatisticas_tela.py
(GC_HTML=caminho.html testa outra cópia do index.html; sai com código 1 se algo falhar)."""
import json, os, sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from playwright.sync_api import sync_playwright

WEB = str(Path(__file__).resolve().parents[2]) + "/"
html = open(os.environ.get("GC_HTML", WEB + "index.html"), encoding="utf-8").read()
CFG = {"dominio_aluno": "alunos.unicerrado.edu.br", "dominios_professor": ["unicerrado.edu.br"],
       "matricula_regex": r"^[A-Za-z0-9][A-Za-z0-9._/-]{3,29}$", "coordenacao_aberta": False}
X1 = '<img src=x onerror="window.__xss=(window.__xss||0)+1">'
ADMIN = {"id": 9, "nome": "Coord Geral", "email": "coord@unicerrado.edu.br", "perfil": "admin", "curso": None}
XLSX_FALSO = b"PK\x03\x04conteudo-de-mentira"
RESUMO_ADM = {"senha_provisoria": False, "alunos": 300, "professores": 4, "aulas": 30, "presencas": 900, "aulas_ativas": 0, "alunos_sem_curso": 0, "alunos_inativos": 0,
              "professores_pendentes": 0, "matriculas_autorizadas": 300, "matriculas_utilizadas": 280, "matricula_obrigatoria": True, "verificacao_email": True,
              "email_configurado": True, "pedidos_senha_pendentes": 0, "por_curso": []}
estado = {}


def dados(**over):
    d = {"de": "2026-05-24", "ate": "2026-11-20", "curso_id": None, "truncado": False, "limite": 5000,
         "resumo": {"eventos": 20, "presencas": 412, "participantes": 150, "alunos_ativos": 200, "adesao_pct": 75.0, "media_por_evento": 20.6,
                    "manuais": 3, "tentativas_recusadas": 11},
         "por_curso": [{"curso": X1, "alunos_ativos": 120, "participantes": 100, "presencas": 300, "adesao_pct": 83.3},
                       {"curso": "Direito", "alunos_ativos": 80, "participantes": 50, "presencas": 112, "adesao_pct": 62.5},
                       {"curso": "Sem curso", "alunos_ativos": 0, "participantes": 2, "presencas": 3, "adesao_pct": None}],
         "por_professor": [{"professor": "Carlos Lima", "eventos": 12, "presencas": 250}, {"professor": X1, "eventos": 8, "presencas": 162}],
         "por_mes": [{"mes": "2026-09", "eventos": 5, "presencas": 100}, {"mes": "2026-10", "eventos": 8, "presencas": 300}, {"mes": "2026-11", "eventos": 7, "presencas": 12}],
         "eventos": [{"id": 3, "titulo": X1, "tipo": "palestra", "data": "2026-11-10T15:00:00+00:00", "professor": "Carlos Lima", "curso": "Todos os cursos", "presencas": 80, "manuais": 2},
                     {"id": 2, "titulo": "Aula de Direito", "tipo": "aula", "data": "2026-11-03T15:00:00+00:00", "professor": X1, "curso": "Direito", "presencas": 12, "manuais": 0}]}
    d.update(over)
    return d


def reiniciar():
    estado.clear()
    estado.update(pedidos=[], xlsx=[], erro=None, resposta=None)


def handler(route):
    u = route.request.url
    if "localhost:8765" not in u:
        return route.abort()
    sp = urlsplit(u)
    p = sp.path
    q = {k: v[0] for k, v in parse_qs(sp.query).items()}
    js = lambda o, c=200: route.fulfill(status=c, content_type="application/json", body=json.dumps(o))
    if p == "/":
        return route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)
    if p.startswith("/assets/"):
        return route.fulfill(status=200, content_type="image/png", body=open(WEB + p[1:], "rb").read())
    if p == "/api/config":
        return js(CFG)
    if p == "/api/cursos":
        return js([{"id": 1, "nome": "Gestão da TI"}, {"id": 2, "nome": "Direito"}])
    if p == "/api/admin/login":
        return js({"token": "t-adm", "usuario": ADMIN})
    if p == "/api/admin/resumo":
        return js(RESUMO_ADM)
    if p == "/api/admin/estatisticas":
        estado["pedidos"].append(q)
        if estado["erro"]:
            return js({"detail": estado["erro"]}, 422)
        r = estado["resposta"] or dados()
        if q.get("de"):
            r = dict(r, de=q["de"])
        if q.get("ate"):
            r = dict(r, ate=q["ate"])
        if q.get("curso_id"):
            r = dict(r, curso_id=int(q["curso_id"]))
        return js(r)
    if p == "/api/admin/estatisticas/xlsx":
        estado["xlsx"].append((q, route.request.headers.get("authorization")))
        return route.fulfill(status=200, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", body=XLSX_FALSO,
                             headers={"Content-Disposition": 'attachment; filename="x.xlsx"'})
    return js({"detail": "mock: " + p}, 404)


falhas = []


def conf(ok, txt):
    print(("OK   " if ok else "FALHA"), txt)
    if not ok:
        falhas.append(txt)


def abrir(pw, largura=1366):
    b = pw.chromium.launch()
    c = b.new_context(viewport={"width": largura, "height": 900}, accept_downloads=True)
    c.route("**/*", handler)
    pg = c.new_page()
    erros = []
    pg.on("pageerror", lambda e: erros.append(str(e)))
    pg.on("dialog", lambda d: d.dismiss())
    pg.goto("http://localhost:8765/")
    pg.wait_for_selector("#v-auth:not([hidden])")
    pg.click('.tab[data-p="admin"]')
    pg.fill("#email", ADMIN["email"])
    pg.fill("#senha", "senha12345")
    pg.click("#btn-auth")
    pg.wait_for_selector("#v-admin:not([hidden])")
    return b, pg, erros


def aba(pg):
    pg.click('#adm-tabs button[data-t="estatisticas"]')
    pg.wait_for_selector("#adm-corpo #est-de")
    pg.wait_for_function("document.querySelector('#adm-corpo .stat') && document.querySelector('#adm-corpo .stat').textContent.includes('Eventos')")


with sync_playwright() as pw:
    reiniciar()
    b, pg, erros = abrir(pw)
    conf(pg.locator('#adm-tabs button[data-t="estatisticas"]').count() == 1, "a coordenação tem a aba 'Estatísticas'")
    aba(pg)
    corpo = pg.inner_text("#adm-corpo")
    tiles = {pg.inner_text(f"#adm-corpo .stat:nth-child({i}) >> nth=0").split("\n")[1]: pg.inner_text(f"#adm-corpo .stat:nth-child({i}) b") for i in range(1, 9)}
    conf(tiles == {"Eventos": "20", "Presenças": "412", "Alunos que participaram": "150", "Alunos ativos": "200", "Adesão": "75%",
                   "Média por evento": "20,6", "Lançadas pelo professor": "3", "Tentativas recusadas": "11"},
         f"os oito indicadores (adesão e média com vírgula): {tiles}")
    conf(pg.input_value("#est-de") == "2026-05-24" and pg.input_value("#est-ate") == "2026-11-20", "o período padrão do servidor preenche as datas")
    conf(estado["pedidos"][0] == {}, f"primeira consulta sem filtros (o servidor escolhe os últimos 180 dias): {estado['pedidos'][0]}")
    conf(pg.locator("#adm-corpo .barra-lin").count() == 3 and "09/2026" in corpo and "10/2026" in corpo, "barras por mês, com mês/ano")
    larguras = pg.eval_on_selector_all("#adm-corpo .barra i", "els => els.map(e => e.style.width)")
    conf(larguras == ["33%", "100%", "4%"], f"a maior barra ocupa 100% e as outras são proporcionais: {larguras}")
    conf("83,3%" in corpo and "62,5%" in corpo and "Sem curso" in corpo, "tabela por curso com adesão (vírgula) e 'Sem curso'")
    conf("—" in pg.inner_text("#adm-corpo table:nth-of-type(1) tbody tr:nth-child(3)"), "adesão não calculável aparece como '—'")
    conf("Carlos Lima" in corpo and "Palestra" in corpo and "Todos os cursos" in corpo, "tabelas de professor e de eventos")
    conf(pg.evaluate("window.__xss || 0") == 0 and pg.locator("#adm-corpo img[src='x']").count() == 0 and X1 in corpo, "nomes maliciosos aparecem como texto e não executam")
    conf(pg.locator("#adm-corpo .msg.err").count() == 0, "sem aviso de corte quando o período cabe")

    # filtros
    pg.fill("#est-de", "2026-10-01")
    pg.fill("#est-ate", "2026-10-31")
    pg.select_option("#est-curso", "2")
    pg.click('#adm-corpo button[data-act="est-ok"]')
    pg.wait_for_function("document.querySelector('#est-de') && document.querySelector('#est-de').value === '2026-10-01' && document.querySelector('#est-curso').value === '2'")
    conf(estado["pedidos"][-1] == {"de": "2026-10-01", "ate": "2026-10-31", "curso_id": "2"}, f"os filtros viram a consulta: {estado['pedidos'][-1]}")
    conf(pg.input_value("#est-ate") == "2026-10-31", "depois de atualizar, o formulário mantém o período e o curso escolhidos")

    # troca de aba e volta: o filtro continua
    pg.click('#adm-tabs button[data-t="cursos"]')
    pg.wait_for_timeout(300)
    pg.click('#adm-tabs button[data-t="estatisticas"]')
    pg.wait_for_function("document.querySelector('#est-curso') && document.querySelector('#est-curso').value === '2'")
    conf(True, "voltando para a aba, o filtro escolhido continua")

    # planilha
    with pg.expect_download() as d:
        pg.click('#adm-corpo button[data-act="est-xlsx"]')
    conf(d.value.suggested_filename == "estatisticas-2026-10-01-a-2026-10-31.xlsx", f"baixa a planilha com o período no nome: {d.value.suggested_filename}")
    q, auth = estado["xlsx"][-1]
    conf(q == {"de": "2026-10-01", "ate": "2026-10-31", "curso_id": "2"} and auth == "Bearer t-adm", f"a planilha é do mesmo período e curso e leva a autenticação: {q}")
    conf("Gerar" not in pg.inner_text('#adm-corpo button[data-act="est-xlsx"]') and pg.is_enabled('#adm-corpo button[data-act="est-xlsx"]'), "o botão volta ao normal depois do download")

    # data inválida: erro do servidor aparece e a tela anterior continua
    estado["erro"] = "A data final é anterior à data inicial."
    pg.click('#adm-corpo button[data-act="est-ok"]')
    pg.wait_for_function("document.querySelector('#msg-adm').textContent.includes('anterior')")
    conf(pg.locator("#adm-corpo .stats").count() == 1 and "err" in (pg.get_attribute("#msg-adm", "class") or ""), "422 do servidor: mensagem em vermelho e os números anteriores continuam na tela")
    conf(not erros, "nenhum erro de JavaScript: %s" % erros)
    b.close()

    # período vazio e corte
    reiniciar()
    estado["resposta"] = dados(por_mes=[], por_curso=[], por_professor=[], eventos=[],
                               resumo={"eventos": 0, "presencas": 0, "participantes": 0, "alunos_ativos": 0, "adesao_pct": None, "media_por_evento": 0, "manuais": 0, "tentativas_recusadas": 0})
    b, pg, erros = abrir(pw, largura=390)
    aba(pg)
    corpo = pg.inner_text("#adm-corpo")
    adesao = pg.inner_text("#adm-corpo .stat:nth-child(5) b")
    conf("Nenhum evento no período" in corpo and adesao == "—", f"período sem eventos: aviso 'Nenhum evento no período' e adesão '—' (veio {adesao!r})")
    conf(pg.locator("#adm-corpo .barra").count() == 0, "período sem eventos: nenhuma barra")
    conf(pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "no celular (390 px) a página não ganha rolagem horizontal")
    estado["resposta"] = dados(truncado=True, limite=5000)
    pg.click('#adm-corpo button[data-act="est-ok"]')
    pg.wait_for_selector("#adm-corpo .msg.err")
    conf("mais de 5000 eventos" in pg.inner_text("#adm-corpo .msg.err"), "período com mais eventos que o limite: avisa que os números são parciais")
    conf(not erros, "período vazio/corte: nenhum erro de JavaScript: %s" % erros)
    b.close()

print()
print("FALHA=%d" % len(falhas))
sys.exit(1 if falhas else 0)
