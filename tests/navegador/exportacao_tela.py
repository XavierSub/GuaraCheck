"""F6 no navegador (CT-691 a CT-7xx): botão "Exportar Chamada (CSV)" do professor (download, erros, clique duplo, sessão expirada) e as abas
Aulas (CSV), Alertas e Auditoria da coordenação, incluindo texto malicioso em nome, título, detalhe e IP e telas de celular.
A API é simulada (imita o FastAPI); o servidor real não roda aqui.
Uso:  pip install playwright && playwright install chromium && python tests/navegador/exportacao_tela.py
(GC_HTML=caminho.html testa outra cópia do index.html; sai com código 1 se algo falhar)."""
import json, os, re, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

WEB = str(Path(__file__).resolve().parents[2]) + "/"
html = open(os.environ.get("GC_HTML", WEB + "index.html"), encoding="utf-8").read()
CFG = {"dominio_aluno": "alunos.unicerrado.edu.br", "dominios_professor": ["unicerrado.edu.br"],
       "matricula_regex": r"^[A-Za-z0-9][A-Za-z0-9._/-]{3,29}$", "coordenacao_aberta": False}
X1 = '<img src=x onerror="window.__xss=(window.__xss||0)+1">'
X2 = '"><svg onload="window.__xss=(window.__xss||0)+1">'
X3 = "'><script>window.__xss=(window.__xss||0)+99</script>"
PROF = {"id": 2, "nome": "Marcos Lima", "email": "marcos@unicerrado.edu.br", "perfil": "professor", "departamento": "Exatas", "registro_funcional": "DOC-1"}
ADMIN = {"id": 9, "nome": "Coord Geral", "email": "coord@unicerrado.edu.br", "perfil": "admin", "curso": None}
EVENTO = {"id": 11, "titulo": "Algoritmos", "latitude": -18.4, "longitude": -49.2, "raio_metros": 50, "ativa": True, "tipo": "aula",
          "local_tipo": "dentro", "local_descricao": "Sala 12", "curso_id": 1, "curso": "Gestão da TI", "periodo": 3, "disciplina": "ED",
          "aberta_em": "2026-11-11T14:00:00+00:00", "encerrada_em": None, "total": 2, "codigo_validacao": "K7Q2"}
CSV_OK = '﻿"Matrícula";"Nome do Aluno"\r\n"GTI00001";"Ana"\r\n'
RESUMO = {"alunos": 3, "alunos_inativos": 0, "alunos_sem_curso": 0, "professores": 1, "professores_pendentes": 0, "aulas": 1,
          "aulas_ativas": 1, "presencas": 2, "matricula_obrigatoria": True, "matriculas_autorizadas": 5, "matriculas_utilizadas": 3,
          "pedidos_senha_pendentes": 0, "verificacao_email": True, "senha_provisoria": False, "por_curso": []}
estado = {"csv": "ok", "csv_pedidos": [], "alertas": [], "auditoria": []}


def handler(route):
    u = route.request.url
    if "localhost:8765" not in u:
        return route.abort()
    p = u.split("localhost:8765", 1)[1].split("?")[0]
    js = lambda o, c=200: route.fulfill(status=c, content_type="application/json", body=json.dumps(o))
    if p == "/":
        return route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)
    if p.startswith("/assets/"):
        return route.fulfill(status=200, content_type="image/png", body=open(WEB + p[1:], "rb").read())
    if p == "/api/config":
        return js(CFG)
    if p == "/api/cursos":
        return js([{"id": 1, "nome": "Gestão da TI"}])
    if p == "/api/auth/login":
        return js({"token": "t-prof", "usuario": PROF})
    if p == "/api/admin/login":
        return js({"token": "t-adm", "usuario": ADMIN})
    # ---- professor ----
    if p == "/api/professor/aulas":
        return js([EVENTO] if "pagina=1" in u or "pagina" not in u else [])
    if p == "/api/aulas/11/presencas":
        return js({"ativa": True, "total": 0, "limite": 2000, "presencas": []})
    if p == "/api/aulas/11/tentativas":
        return js({"itens": []})
    if p == "/api/aulas/11/ausentes":
        return js({"disponivel": True, "total": 0, "limite": 500, "itens": []})
    if p == "/api/aulas/ativas":
        return js([])
    if p == "/api/v1/export/chamada":
        estado["csv_pedidos"].append({"url": u, "auth": route.request.headers.get("authorization")})
        m = estado["csv"]
        if m == "abort":
            return route.abort()
        if m == "404":
            return js({"detail": "Aula não encontrada."}, 404)
        if m == "500":
            return route.fulfill(status=500, content_type="text/html", body="<html>Internal Server Error</html>")
        if m == "422":
            return js({"detail": [{"type": "int_parsing", "loc": ["query", "aula_id"], "msg": "x"}]}, 422)
        if m == "401":
            return js({"detail": "Sessão expirada. Entre novamente."}, 401)
        return route.fulfill(status=200, content_type="text/csv; charset=utf-8", body=CSV_OK.encode("utf-8"),
                             headers={"Content-Disposition": 'attachment; filename="chamada-11.csv"', "Cache-Control": "no-store"})
    # ---- coordenação ----
    if p == "/api/admin/resumo":
        return js(RESUMO)
    if p == "/api/admin/aulas":
        return js([dict(EVENTO, professor=X1, titulo=X2)])
    if p == "/api/admin/alertas":
        return js(estado["alertas"])
    if p == "/api/admin/auditoria":
        return js(estado["auditoria"])
    return js({"detail": "mock: " + p}, 404)


falhas = []


def conf(ok, txt):
    print(("OK   " if ok else "FALHA"), txt)
    if not ok:
        falhas.append(txt)


def entrar(pw, quem, vista, largura=1366):
    b = pw.chromium.launch()
    c = b.new_context(viewport={"width": largura, "height": 900}, accept_downloads=True)
    c.route("**/*", handler)
    pg = c.new_page()
    erros, dialogos = [], []
    pg.on("pageerror", lambda e: erros.append(str(e)))
    pg.on("dialog", lambda d: (dialogos.append(d.message), d.dismiss()))
    pg.goto("http://localhost:8765/")
    pg.wait_for_selector("#v-auth:not([hidden])")
    pg.click(f'.tab[data-p="{quem}"]')
    pg.fill("#email", (ADMIN if quem == "admin" else PROF)["email"])
    pg.fill("#senha", "senha12345")
    pg.click("#btn-auth")
    pg.wait_for_selector(vista + ":not([hidden])")
    return b, pg, erros, dialogos


def injetado(pg, raiz):
    return pg.evaluate(f"document.querySelectorAll('{raiz} img[src=\"x\"], {raiz} svg[onload], {raiz} script, {raiz} [onerror], {raiz} [onload]').length")


def sem_rolagem_horizontal(pg):
    return pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")


def msg_de(pg, sel):
    return pg.inner_text(sel).strip()


with sync_playwright() as pw:
    # =========================== professor: botão CSV ===========================
    b, pg, erros, dialogos = entrar(pw, "professor", "#v-prof")
    pg.wait_for_selector("#btn-csv")
    estado["csv"] = "ok"
    with pg.expect_download() as d:
        pg.click("#btn-csv")
    arq = d.value
    conf(arq.suggested_filename == "chamada-11.csv", "CT-691 | o download se chama chamada-<id>.csv")
    conf(Path(arq.path()).read_bytes().decode("utf-8") == CSV_OK, "CT-692 | o arquivo baixado é exatamente o CSV do servidor (com BOM)")
    conf(estado["csv_pedidos"][-1]["auth"] == "Bearer t-prof" and estado["csv_pedidos"][-1]["url"].endswith("aula_id=11"),
         "CT-693 | o pedido leva o token no cabeçalho (nunca na URL) e o id da chamada")
    conf(not pg.is_disabled("#btn-csv"), "CT-694 | o botão volta a funcionar depois do download")

    n0 = len(estado["csv_pedidos"])
    with pg.expect_download():
        pg.dblclick("#btn-csv")
    pg.wait_for_timeout(600)
    conf(len(estado["csv_pedidos"]) - n0 == 1, "CT-695 | clique duplo gera UM pedido (um arquivo e um registro na auditoria)")
    with pg.expect_download():
        pg.click("#btn-csv")
    conf(len(estado["csv_pedidos"]) - n0 == 2, "CT-696 | depois de terminar, dá para exportar de novo")

    baixou = []
    pg.on("download", lambda dl: baixou.append(dl))
    for modo, esperado, nome in (("404", "Aula não encontrada.", "404 com mensagem do servidor"),
                                 ("500", "Não foi possível exportar a chamada.", "500 com página de erro (não é JSON)"),
                                 ("422", "Não foi possível exportar a chamada.", "422 com lista de erros"),
                                 ("abort", "Sem conexão com o servidor. Verifique sua internet.", "servidor fora do ar / sem internet")):
        estado["csv"] = modo
        baixou.clear()
        pg.click("#btn-csv")
        pg.wait_for_function("document.querySelector('#msg-prof').textContent.trim().length > 0")
        conf(msg_de(pg, "#msg-prof") == esperado and not baixou and not pg.is_disabled("#btn-csv"),
             f"CT-697 | {nome}: mostra \"{esperado}\", não baixa nada e o botão continua utilizável")
        pg.evaluate("document.querySelector('#msg-prof').textContent = ''")
    conf(not erros and not dialogos, "CT-698 | nenhum erro de JavaScript nem janela de alerta nos casos acima")

    estado["csv"] = "401"
    pg.click("#btn-csv")
    try:
        pg.wait_for_selector("#v-auth:not([hidden])", timeout=3000)
    except Exception:
        pass
    conf(pg.is_visible("#v-auth") and pg.is_hidden("#v-prof"), "CT-699 | sessão expirada durante a exportação leva de volta ao login (como as outras telas)")
    b.close()

    # =========================== coordenação ===========================
    estado["csv"] = "ok"
    estado["alertas"] = [
        {"id": 7, "aluno": X1, "email": X2, "aula": X3, "alertas": X1 + " VPN", "ip": X2, "distancia_m": 33.3, "registrado_em": "2026-11-11T14:07:00+00:00"},
        {"id": 5, "aluno": "Beatriz", "email": "b@alunos.unicerrado.edu.br", "aula": "Algoritmos", "alertas": "GPS suspeito", "ip": None,
         "distancia_m": 5.0, "registrado_em": "2026-11-11T14:05:00+00:00"}]
    estado["auditoria"] = [
        {"id": 4, "usuario_id": 9, "usuario": X1, "acao": "exportar_chamada", "detalhe": X2, "ip": X3, "data_hora": "2026-11-11T14:09:00+00:00"},
        {"id": 3, "usuario_id": 77, "usuario": None, "acao": "excluir_professor", "detalhe": "x@unicerrado.edu.br", "ip": "177.1.1.1", "data_hora": "2026-11-11T14:08:00+00:00"},
        {"id": 2, "usuario_id": None, "usuario": None, "acao": "login_admin_falha", "detalhe": None, "ip": None, "data_hora": "2026-11-11T14:07:00+00:00"},
        {"id": 1, "usuario_id": 9, "usuario": "Coord Geral", "acao": "acao_nova_sem_rotulo", "detalhe": "", "ip": "177.2.2.2", "data_hora": "2026-11-11T14:06:00+00:00"}]
    b, pg, erros, dialogos = entrar(pw, "admin", "#v-admin")

    pg.click('#adm-tabs button[data-t="aulas"]')
    pg.wait_for_selector('#adm-corpo button[data-act="aula-csv"]')
    conf(injetado(pg, "#adm-corpo") == 0, "CT-700 | aba Aulas: título e professor maliciosos aparecem como texto")
    n0 = len(estado["csv_pedidos"])
    with pg.expect_download() as d:
        pg.click('#adm-corpo button[data-act="aula-csv"]')
    conf(d.value.suggested_filename == "chamada-11.csv" and estado["csv_pedidos"][-1]["auth"] == "Bearer t-adm",
         "CT-701 | a coordenação baixa o CSV de qualquer aula (token da coordenação)")
    n1 = len(estado["csv_pedidos"])
    with pg.expect_download():
        pg.dblclick('#adm-corpo button[data-act="aula-csv"]')
    pg.wait_for_timeout(600)
    conf(len(estado["csv_pedidos"]) - n1 == 1, "CT-702a | clique duplo no link CSV da coordenação gera UM pedido")
    estado["csv"] = "404"
    pg.click('#adm-corpo button[data-act="aula-csv"]')
    pg.wait_for_function("document.querySelector('#msg-adm').textContent.trim().length > 0")
    conf(msg_de(pg, "#msg-adm") == "Aula não encontrada.", "CT-702 | erro da exportação aparece na própria aba da coordenação")
    estado["csv"] = "ok"

    pg.click('#adm-tabs button[data-t="alertas"]')
    pg.wait_for_function("document.querySelector('#adm-corpo').innerText.includes('Beatriz')")
    conf(injetado(pg, "#adm-corpo") == 0 and (pg.evaluate("window.__xss || 0") == 0), "CT-703 | aba Alertas: nome, e-mail, aula, alerta e IP maliciosos aparecem como texto")
    linhas = pg.eval_on_selector_all("#adm-corpo table tbody tr", "els => els.map(e => e.innerText.replace(/\\s+/g, ' '))")
    conf(len(linhas) == 2 and "33.3 m" in linhas[0] and "Beatriz" in linhas[1] and "GPS suspeito" in linhas[1] and "—" in linhas[1],
         "CT-704 | alertas na ordem do servidor, com distância e IP vazio mostrado como —")
    conf(pg.inner_text("#adm-corpo").count("mais recentes") >= 1, "CT-705 | a aba explica que os mais recentes aparecem primeiro")

    pg.click('#adm-tabs button[data-t="auditoria"]')
    pg.wait_for_function("document.querySelector('#adm-corpo').innerText.includes('Falha de login da coordenação')")
    conf(injetado(pg, "#adm-corpo") == 0 and pg.evaluate("window.__xss || 0") == 0 and not dialogos,
         "CT-706 | aba Auditoria: nome, detalhe e IP maliciosos aparecem como texto")
    linhas = pg.eval_on_selector_all("#adm-corpo table tbody tr", "els => els.map(e => e.innerText.replace(/\\s+/g, ' '))")
    conf(len(linhas) == 4 and "Exportou chamada (CSV)" in linhas[0] and "Excluiu professor" in linhas[1] and "#77" in linhas[1],
         "CT-707 | ações com rótulo em português; usuário excluído aparece como #id")
    conf("Falha de login da coordenação" in linhas[2] and "—" in linhas[2], "CT-708 | log sem usuário e sem IP continua legível")
    conf("acao_nova_sem_rotulo" in linhas[3], "CT-709 | ação nova sem rótulo aparece com o código da ação (nada some)")
    conf("150" in pg.inner_text("#adm-corpo"), "CT-710 | a tela avisa que mostra os 150 registros mais recentes")

    estado["alertas"], estado["auditoria"] = [], []
    pg.click('#adm-tabs button[data-t="alertas"]')
    pg.wait_for_timeout(500)
    conf(not erros, "CT-711 | listas vazias não geram erro de JavaScript")
    b.close()

    # =========================== celular ===========================
    estado["alertas"] = [{"id": 7, "aluno": "Nome Muito Comprido Da Silva Albuquerque Sobrenome", "email": "nome.muito.comprido@alunos.unicerrado.edu.br",
                          "aula": "Introdução à Algoritmos e Estruturas de Dados II", "alertas": "VPN/proxy detectado; GPS suspeito", "ip": "2804:14d:abcd:1234:5678:9abc:def0:1234",
                          "distancia_m": 33.3, "registrado_em": "2026-11-11T14:07:00+00:00"}]
    estado["auditoria"] = [{"id": 4, "usuario_id": 9, "usuario": "Coord Geral", "acao": "exportar_chamada", "detalhe": "perfil=admin; aula=11; titulo=" + "T" * 120,
                            "ip": "2804:14d:abcd:1234:5678:9abc:def0:1234", "data_hora": "2026-11-11T14:09:00+00:00"}]
    b, pg, erros, dialogos = entrar(pw, "admin", "#v-admin", largura=375)
    for aba in ("alertas", "auditoria"):
        pg.click(f'#adm-tabs button[data-t="{aba}"]')
        pg.wait_for_function("t => document.querySelector('#adm-corpo').innerText.includes(t)", arg="Coord Geral" if aba == "auditoria" else "Nome Muito Comprido")
        conf(sem_rolagem_horizontal(pg), f"CT-712 | celular (375 px), aba {aba}: a página não ganha rolagem lateral (a tabela rola dentro do próprio quadro)")
    conf(not erros, "CT-713 | celular: sem erros de JavaScript")
    b.close()

print()
print("RESULTADO:", "TUDO OK" if not falhas else f"{len(falhas)} FALHA(S)")
sys.exit(1 if falhas else 0)
