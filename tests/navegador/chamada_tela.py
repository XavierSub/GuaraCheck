"""F5 no navegador (CT-559 a CT-575): painel do professor (presentes em tempo real, tentativas, ausentes, conferir comprovante) e
comprovantes do aluno (paginação, "Mostrar ao professor", download do PDF), incluindo texto malicioso em nome/curso/alertas.
A API é simulada (imita o FastAPI); o servidor real não roda aqui.
Uso:  pip install playwright && playwright install chromium && python tests/navegador/chamada_tela.py
(GC_HTML=caminho.html testa outra cópia do index.html; sai com código 1 se algo falhar)."""
import json, os, re, time
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
ALUNO = {"id": 1, "nome": X1, "email": "ana@alunos.unicerrado.edu.br", "perfil": "aluno", "curso": X2, "periodo": 3, "matricula": X3}
EVENTO = {"id": 11, "titulo": "Algoritmos", "latitude": -18.4, "longitude": -49.2, "raio_metros": 50, "ativa": True, "tipo": "aula",
          "local_tipo": "dentro", "local_descricao": "Sala 12", "curso_id": 1, "curso": "Gestão da TI", "periodo": 3, "disciplina": "ED",
          "aberta_em": "2026-11-11T14:00:00+00:00", "encerrada_em": None, "total": 2, "codigo_validacao": "K7Q2"}
PDF_MINIMO = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"
estado = {"pedidos_presencas": [], "encerrada": False, "conferir": "ok", "pdf": "ok", "n_comp": 25}


def pres(i, **kw):
    d = {"id": i, "nome": f"Aluno {i}", "matricula": f"GTI{i:05d}", "email": f"a{i}@alunos.unicerrado.edu.br", "curso": "Gestão da TI",
         "periodo": 3, "distancia_m": 4.5, "registrado_em": "2026-11-11T14:05:00+00:00", "alertas": None}
    d.update(kw)
    return d


def comp(i):
    return {"id": i, "registrado_em": "2026-11-11T14:05:00+00:00", "titulo": f"Evento {i}", "tipo": "aula", "local_tipo": "dentro",
            "disciplina": "ED", "periodo": 3, "professor": X1 if i == estado["n_comp"] else "Marcos Lima"}


def handler(route):
    u = route.request.url
    if "localhost:8765" not in u:
        return route.abort()
    p = u.split("localhost:8765", 1)[1].split("?")[0]
    q = dict(kv.split("=", 1) for kv in (u.split("?", 1)[1].split("&") if "?" in u else []))
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
        return js({"token": "t", "usuario": PROF if route.request.post_data and '"professor"' in route.request.post_data else ALUNO})
    # ---- professor ----
    if p == "/api/professor/aulas":
        return js([dict(EVENTO, ativa=not estado["encerrada"])] if q.get("pagina", "1") == "1" else [])
    if p == "/api/aulas/11/presencas":
        apos = int(q.get("apos_id", 0))
        estado["pedidos_presencas"].append(apos)
        todas = estado.get("lista") or [pres(1), pres(2)]
        novas = [x for x in todas if x["id"] > apos]
        return js({"ativa": not estado["encerrada"], "total": len(todas), "limite": 2000, "presencas": novas})
    if p == "/api/aulas/11/tentativas":
        return js({"itens": estado.get("tentativas", [])})
    if p == "/api/aulas/11/ausentes":
        return js(estado.get("ausentes", {"disponivel": True, "total": 0, "limite": 500, "itens": []}))
    if p == "/api/aulas/11/conferir":
        m = estado["conferir"]
        if m == "422":
            return js({"detail": "Formato inválido. Cole a autenticação do PDF, como 1042-9F3A7C21B4."}, 422)
        if m == "falso":
            return js({"valido": False})
        return js({"valido": True, "nome": X1, "matricula": "GTI00001", "curso": "Gestão da TI", "registrado_em": "2026-11-11T14:05:00+00:00", "codigo_validacao": "K7Q2"})
    # ---- aluno ----
    if p == "/api/aulas/ativas":
        return js([])
    if p == "/api/me":
        return js({"nome": ALUNO["nome"], "matricula": ALUNO["matricula"], "email": ALUNO["email"], "curso": ALUNO["curso"], "periodo": 3})
    if p == "/api/aluno/presencas":
        pag = int(q.get("pagina", 1))
        todos = [comp(i) for i in range(estado["n_comp"], 0, -1)]
        return js(todos[(pag - 1) * 20: pag * 20])
    if p == "/api/aluno/notas":
        return js([])
    m = re.fullmatch(r"/api/aluno/presencas/(\d+)/pdf", p)
    if m:
        if estado["pdf"] == "429":
            return js({"detail": "Muitas tentativas. Aguarde alguns minutos e tente de novo."}, 429)
        return route.fulfill(status=200, content_type="application/pdf", body=PDF_MINIMO,
                             headers={"Content-Disposition": f'attachment; filename="comprovante-presenca-{m.group(1)}.pdf"'})
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
    pg.fill("#email", (PROF if quem == "professor" else ALUNO)["email"])
    pg.fill("#senha", "senha12345")
    pg.click("#btn-auth")
    pg.wait_for_selector(vista + ":not([hidden])")
    return b, pg, erros, dialogos


def injetado(pg, raiz):
    return pg.evaluate(f"document.querySelectorAll('{raiz} img[src=\"x\"], {raiz} svg[onload], {raiz} script, {raiz} [onerror], {raiz} [onload]').length")


with sync_playwright() as pw:
    # =========================== professor ===========================
    estado.update(lista=[pres(1, nome=X1, matricula=X2, curso=X3, alertas=X1), pres(2, email=X2)],
                  tentativas=[{"id": 1, "nome": X1, "matricula": X2, "curso": X3, "motivo": "fora_raio", "detalhe": X1, "distancia_m": 200, "quantidade": 3,
                               "ultima_em": "2026-11-11T14:06:00+00:00", "registrou": False},
                              {"id": 2, "nome": "Beatriz", "matricula": "GTI2", "curso": None, "motivo": X2, "detalhe": X3, "distancia_m": None, "quantidade": 1,
                               "ultima_em": "2026-11-11T14:07:00+00:00", "registrou": True}],
                  ausentes={"disponivel": True, "total": 3, "limite": 500, "itens": [{"nome": X1, "matricula": X2, "email": X3, "periodo": 3},
                                                                                       {"nome": "Carla", "matricula": None, "email": "c@a.b", "periodo": None}]})
    b, pg, erros, dialogos = entrar(pw, "professor", "#v-prof")
    pg.wait_for_selector("#tbody tr")
    pg.wait_for_timeout(1500)
    for raiz, nome in (("#tbody", "presentes"), ("#tb-fora", "tentativas fora do raio"), ("#tb-burla", "tentativas suspeitas"), ("#tb-aus", "ausentes")):
        conf(injetado(pg, raiz) == 0, f"painel / {nome}: nome, matrícula, curso, alertas e e-mail maliciosos aparecem como texto")
    conf(pg.evaluate("window.__xss || 0") == 0 and not dialogos, "nada executou (nenhum script, nenhum alerta)")
    conf(X1 in pg.inner_text("#tbody"), "o texto malicioso é MOSTRADO literalmente (não sumiu)")
    conf(pg.inner_text("#p-total") == "2" and "Chamada aberta" in pg.inner_text("#p-estado"), "contador de presentes e estado 'Chamada aberta'")
    conf(pg.inner_text("#n-tent") == "2" and pg.inner_text("#n-aus") == "3", "contadores das abas: tentativas 2, ausentes 3")
    conf("Registrou depois" in pg.inner_text("#tb-burla") and "Sem presença" in pg.inner_text("#tb-fora"), "tentativas: 'Registrou depois' x 'Sem presença'")
    conf("Mostrando 2 de 3" in pg.inner_text("#aus-aviso"), "ausentes: aviso 'Mostrando 2 de 3' quando a lista é cortada")
    # abas
    pg.click('#p-tabs button[data-pt="ausentes"]')
    conf(not pg.is_hidden("#pt-ausentes") and pg.is_hidden("#pt-tentativas"), "aba Ausentes abre e as outras fecham")
    pg.click('#p-tabs button[data-pt="tentativas"]')
    conf(not pg.is_hidden("#pt-tentativas") and pg.is_hidden("#pt-ausentes"), "aba Tentativas abre")
    # busca incremental
    estado["lista"] = [pres(1), pres(2), pres(3)]
    estado["pedidos_presencas"].clear()
    pg.wait_for_timeout(3600)
    conf(any(a == 2 for a in estado["pedidos_presencas"]), f"a atualização pede só as novas (apos_id=2): {estado['pedidos_presencas']}")
    conf(pg.locator("#tbody tr").count() == 3 and pg.inner_text("#p-total") == "3", "nova presença aparece sem recarregar (3 linhas)")
    # conferir comprovante
    pg.fill("#conf-cod", "")
    pg.click("#btn-conf")
    conf("Cole a autenticação" in pg.inner_text("#conf-res"), "conferir vazio: pede para colar a autenticação (sem chamar o servidor)")
    pg.fill("#conf-cod", "11-9F3A7C21B4")
    pg.press("#conf-cod", "Enter")
    pg.wait_for_function("document.querySelector('#conf-res').textContent.includes('AUTÊNTICO')")
    conf("AUTÊNTICO" in pg.inner_text("#conf-res") and "K7Q2" in pg.inner_text("#conf-res") and injetado(pg, "#conf-res") == 0 and pg.evaluate("window.__xss || 0") == 0,
         "conferir (Enter): 'AUTÊNTICO' com o código do evento e nome malicioso como texto")
    estado["conferir"] = "falso"
    pg.click("#btn-conf")
    pg.wait_for_function("document.querySelector('#conf-res').textContent.includes('NÃO confere')")
    conf("NÃO confere" in pg.inner_text("#conf-res") and "err" in (pg.get_attribute("#conf-res", "class") or ""), "conferir falso: 'NÃO confere' em vermelho")
    estado["conferir"] = "422"
    pg.click("#btn-conf")
    pg.wait_for_function("document.querySelector('#conf-res').textContent.includes('Formato inválido')")
    conf(True, "conferir 422: mostra a orientação do servidor")
    # encerrada: para de atualizar
    estado["encerrada"] = True
    pg.wait_for_function("document.querySelector('#p-estado').textContent.includes('encerrada')", timeout=8000)
    conf(pg.is_hidden("#btn-fim"), "chamada encerrada: botão 'Encerrar' some")
    estado["pedidos_presencas"].clear()
    pg.wait_for_timeout(7000)
    conf(len(estado["pedidos_presencas"]) == 0, f"chamada encerrada e lista completa: a atualização automática PARA ({len(estado['pedidos_presencas'])} pedidos em 7 s)")
    conf(not erros, "painel do professor: nenhum erro de JavaScript: %s" % erros)
    b.close()

    # mais de 200 linhas
    estado.update(encerrada=False, lista=[pres(i) for i in range(1, 251)], tentativas=[], ausentes=None)
    estado.pop("ausentes", None)
    b, pg, erros, _ = entrar(pw, "professor", "#v-prof")
    pg.wait_for_function("document.querySelector('#p-total').textContent === '250'", timeout=8000)
    conf(pg.locator("#tbody tr").count() == 200 and "200 mais recentes de 250" in pg.inner_text("#p-mais"), "250 presentes: mostra as 200 mais recentes e avisa que o CSV traz a lista completa")
    b.close()

    # =========================== aluno ===========================
    estado.update(n_comp=25, pdf="ok")
    b, pg, erros, dialogos = entrar(pw, "aluno", "#v-aluno", largura=390)
    pg.wait_for_selector("#comprov .comp")
    conf(pg.locator("#comprov .comp").count() == 20, "aluno com 25 comprovantes: a 1ª página mostra 20")
    conf(pg.locator("#comp-mais").count() == 1, "botão 'Ver comprovantes mais antigos' aparece")
    pg.click("#comp-mais")
    pg.wait_for_function("document.querySelectorAll('#comprov .comp').length === 25")
    conf(pg.locator("#comp-mais").count() == 0, "depois de carregar tudo o botão some")
    conf(pg.locator("#comprov .comp").count() == 25, "os 25 comprovantes aparecem (sem repetir)")
    conf(injetado(pg, "#comprov") == 0 and pg.evaluate("window.__xss || 0") == 0 and not dialogos, "comprovantes: nome, matrícula, curso e professor maliciosos aparecem como texto")
    # 'Mostrar ao professor' de um comprovante da 2ª página (índice certo após a paginação)
    pg.locator('#comprov button[data-m="22"]').click()
    pg.wait_for_selector("#mostrar:not([hidden])")
    conf("Evento 3" in pg.inner_text("#m-evento") and "Protocolo nº 3" in pg.inner_text("#m-quando"), "'Mostrar ao professor' do comprovante antigo traz o evento certo (%s)" % pg.inner_text("#m-evento")[:40])
    conf(pg.inner_text("#m-nome") == X1 and injetado(pg, "#mostrar") == 0, "tela cheia: nome malicioso como texto")
    pg.keyboard.press("Escape")
    conf(pg.is_hidden("#mostrar"), "Esc fecha a tela cheia")
    # PDF
    with pg.expect_download() as d:
        pg.locator('#comprov button[data-pdf="25"]').click()
    conf(d.value.suggested_filename == "comprovante-presenca-25.pdf", "Baixar PDF: arquivo comprovante-presenca-25.pdf (%s)" % d.value.suggested_filename)
    estado["pdf"] = "429"
    pg.locator('#comprov button[data-pdf="24"]').click()
    pg.wait_for_selector("#msg-aluno.err")
    conf("Muitas tentativas" in pg.inner_text("#msg-aluno"), "PDF com limite excedido: mostra a mensagem do servidor")
    conf(not pg.locator('#comprov button[data-pdf="24"]').is_disabled(), "depois do erro o botão 'Baixar PDF' volta a funcionar")
    conf(pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "390 px: sem rolagem horizontal nos comprovantes")
    conf(not erros, "tela do aluno: nenhum erro de JavaScript: %s" % erros)
    b.close()
    # 20 exatos: ainda mostra o botão (o servidor não diz se há mais); 5: não mostra
    estado["n_comp"] = 5
    b, pg, erros, _ = entrar(pw, "aluno", "#v-aluno")
    pg.wait_for_selector("#comprov .comp")
    conf(pg.locator("#comp-mais").count() == 0, "com menos de 20 comprovantes não aparece o botão de mais antigos")
    b.close()

print("\n%d falha(s)" % len(falhas))
raise SystemExit(1 if falhas else 0)
