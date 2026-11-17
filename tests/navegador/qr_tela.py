"""F11 no navegador: presença por QR code.
Professor: o painel da chamada aberta mostra o QR (desenho vindo do servidor), o link, "Copiar link" e "Projetar QR code na tela"
(tela cheia com Esc, foco devolvido e rolagem da página travada); o QR some quando a chamada encerra; falha ao gerar não derruba o painel.
Aluno: abrir /?aula=ID pede login, depois destaca a chamada certa (ou avisa que ela não está aberta) e limpa o endereço; o link NUNCA registra
presença sozinho (nenhum POST /presenca). Valores estranhos em ?aula= são ignorados; professor ignora o link.
A API é simulada (imita o FastAPI); o desenho do QR vem da função REAL qr_svg do app.py.
Uso:  pip install playwright && playwright install chromium && python tests/navegador/qr_tela.py
(GC_HTML=caminho.html testa outra cópia do index.html; sai com código 1 se algo falhar)."""
import ast, json, os
from pathlib import Path
from playwright.sync_api import sync_playwright

WEB = str(Path(__file__).resolve().parents[2]) + "/"
html = open(os.environ.get("GC_HTML", WEB + "index.html"), encoding="utf-8").read()
_src = open(WEB + "app.py", encoding="utf-8").read()
_ns = {}
exec(ast.get_source_segment(_src, next(n for n in ast.parse(_src).body if isinstance(n, ast.FunctionDef) and n.name == "qr_svg")), _ns)
qr_svg = _ns["qr_svg"]

CFG = {"dominio_aluno": "alunos.unicerrado.edu.br", "dominios_professor": ["unicerrado.edu.br"],
       "matricula_regex": r"^[A-Za-z0-9][A-Za-z0-9._/-]{3,29}$", "coordenacao_aberta": False}
X1 = '<img src=x onerror="window.__xss=(window.__xss||0)+1">'
PROF = {"id": 2, "nome": "Marcos Lima", "email": "marcos@unicerrado.edu.br", "perfil": "professor", "departamento": "Exatas", "registro_funcional": "DOC-1"}
ALUNO = {"id": 1, "nome": "Ana Souza", "email": "ana@alunos.unicerrado.edu.br", "perfil": "aluno", "curso": "Gestão da TI", "periodo": 3, "matricula": "GTI00001"}
LAT, LON = -18.4, -49.2
EVENTO = {"id": 11, "titulo": "Algoritmos", "latitude": LAT, "longitude": LON, "raio_metros": 50, "ativa": True, "tipo": "aula",
          "local_tipo": "dentro", "local_descricao": "Sala 12", "curso_id": 1, "curso": "Gestão da TI", "periodo": 3, "disciplina": "ED",
          "aberta_em": "2026-11-17T14:00:00+00:00", "encerrada_em": None, "total": 0, "codigo_validacao": "K7Q2"}
LINK = "https://guaracheck.onrender.com/?aula=11"
estado = {}


def zerar(**kw):
    estado.clear()
    estado.update(encerrada=False, qr="ok", pedidos_qr=0, posts_presenca=0, titulo="Algoritmos", registrou=False, abertas=[11])
    estado.update(kw)


def aula_ativa(i):
    return {"id": i, "titulo": "Algoritmos" if i == 11 else "Banco de Dados", "tipo": "aula", "local_tipo": "dentro", "local_descricao": "Sala 12",
            "curso": "Gestão da TI", "periodo": 3, "disciplina": "ED", "professor": "Marcos Lima", "raio_metros": 50, "latitude": LAT, "longitude": LON,
            "ja_registrou": estado["registrou"] and i == 11, "ativa": True}


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
        return js({"token": "t", "usuario": PROF if route.request.post_data and '"professor"' in route.request.post_data else ALUNO})
    # ---- professor ----
    if p == "/api/professor/aulas":
        return js([dict(EVENTO, titulo=estado["titulo"], ativa=not estado["encerrada"])])
    if p == "/api/aulas/11/qr":
        estado["pedidos_qr"] += 1
        if estado["encerrada"]:
            return js({"detail": "Esta chamada já foi encerrada."}, 409)
        if estado["qr"] == "erro":
            return js({"detail": "Erro interno"}, 500)
        return js({"url": LINK, "svg": qr_svg(LINK)})
    if p == "/api/aulas/11/presencas":
        return js({"ativa": not estado["encerrada"], "total": 0, "limite": 2000, "presencas": []})
    if p == "/api/aulas/11/tentativas":
        return js({"itens": []})
    if p == "/api/aulas/11/ausentes":
        return js({"disponivel": True, "total": 0, "limite": 500, "itens": []})
    # ---- aluno ----
    if p == "/api/aulas/ativas":
        return js([aula_ativa(i) for i in estado["abertas"]])
    if p == "/api/aulas/11/presenca":
        estado["posts_presenca"] += 1
        return js({"detail": "mock"}, 400)
    if p == "/api/me":
        return js({"nome": ALUNO["nome"], "matricula": ALUNO["matricula"], "email": ALUNO["email"], "curso": ALUNO["curso"], "periodo": 3})
    if p == "/api/aluno/presencas":
        return js([])
    if p == "/api/aluno/notas":
        return js([])
    return js({"detail": "mock: " + p}, 404)


falhas = []


def conf(ok, txt):
    print(("OK   " if ok else "FALHA"), txt)
    if not ok:
        falhas.append(txt)


def abrir(pw, url="http://localhost:8765/", largura=1366, sessao=None, geo=False):
    b = pw.chromium.launch()
    kw = {"viewport": {"width": largura, "height": 900}}
    if geo:
        kw.update(geolocation={"latitude": LAT, "longitude": LON, "accuracy": 8}, permissions=["geolocation"])
    c = b.new_context(**kw)
    c.grant_permissions(["clipboard-read", "clipboard-write"], origin="http://localhost:8765") if not geo else None
    c.route("**/*", handler)
    pg = c.new_page()
    erros, dialogos = [], []
    pg.on("pageerror", lambda e: erros.append(str(e)))
    pg.on("dialog", lambda d: (dialogos.append(d.message), d.dismiss()))
    if sessao:
        pg.add_init_script("localStorage.setItem('gc_token','t'); localStorage.setItem('gc_user', %s)" % json.dumps(json.dumps(sessao)))
    pg.goto(url)
    return b, pg, erros, dialogos


def login(pg, quem, vista):
    pg.wait_for_selector("#v-auth:not([hidden])")
    pg.click(f'.tab[data-p="{quem}"]')
    pg.fill("#email", (PROF if quem == "professor" else ALUNO)["email"])
    pg.fill("#senha", "senha12345")
    pg.click("#btn-auth")
    pg.wait_for_selector(vista + ":not([hidden])")


with sync_playwright() as pw:
    # =========================== professor ===========================
    zerar()
    b, pg, erros, dialogos = abrir(pw)
    login(pg, "professor", "#v-prof")
    pg.wait_for_selector("#qr-box:not([hidden])")
    conf(pg.locator("#qr-img svg").count() == 1, "chamada aberta: o painel mostra o QR code (SVG do servidor)")
    conf(pg.inner_text("#qr-link") == LINK, "o link que está dentro do QR aparece em texto (%s)" % pg.inner_text("#qr-link"))
    conf(pg.inner_text("#qr-raio") == "50", "o raio de 50 m aparece no texto")
    caixa = pg.locator("#qr-img").bounding_box()
    conf(caixa["width"] >= 150 and abs(caixa["width"] - caixa["height"]) < 1, "o QR aparece quadrado e grande o bastante (%dx%d)" % (caixa["width"], caixa["height"]))
    conf(pg.evaluate("getComputedStyle(document.querySelector('#qr-img')).backgroundColor") == "rgb(255, 255, 255)", "fundo do QR é branco")
    conf(estado["pedidos_qr"] == 1, "um único pedido do QR ao abrir o painel (sem repetir a cada 3 s): %d" % estado["pedidos_qr"])
    # copiar
    pg.click("#qr-copiar")
    pg.wait_for_selector("#qr-msg.ok")
    conf("Link copiado" in pg.inner_text("#qr-msg"), "Copiar link: mostra a confirmação")
    conf(pg.evaluate("navigator.clipboard.readText()") == LINK, "Copiar link: o link está mesmo na área de transferência")
    # projetar
    pg.click("#qr-ampliar")
    pg.wait_for_selector("#qr-tela:not([hidden])")
    conf(pg.inner_text("#qr-t-titulo") == "Algoritmos" and pg.locator("#qr-t-img svg").count() == 1, "Projetar: tela cheia com o nome do evento e o QR")
    g = pg.locator("#qr-t-img").bounding_box()
    conf(g["width"] > caixa["width"] * 2 and abs(g["width"] - g["height"]) < 1 and g["width"] <= 640, "Projetar: o QR é bem maior que no painel e continua quadrado (%dpx)" % g["width"])
    conf("QR code" in pg.inner_text("#qr-tela") and pg.inner_text("#qr-t-link") == LINK, "Projetar: instrução ao aluno e link em texto")
    conf(pg.evaluate("document.body.style.overflow") == "hidden", "Projetar: a página de trás não rola")
    conf(pg.evaluate("document.activeElement.closest('#qr-tela') !== null"), "Projetar: o foco do teclado vai para dentro da janela")
    pg.keyboard.press("Tab")
    conf(pg.evaluate("document.activeElement.id") == "qr-t-fechar", "Projetar: Tab não escapa para a página de trás")
    pg.keyboard.press("Shift+Tab")
    conf(pg.evaluate("document.activeElement.id") == "qr-t-fechar", "Projetar: Shift+Tab também fica na janela")
    pg.keyboard.press("Escape")
    conf(pg.is_hidden("#qr-tela") and pg.evaluate("document.body.style.overflow") == "", "Esc fecha a tela cheia e libera a rolagem")
    conf(pg.evaluate("document.activeElement.id") == "qr-ampliar", "ao fechar, o foco volta ao botão que abriu")
    pg.click("#qr-ampliar")
    pg.click("#qr-t-fechar")
    conf(pg.is_hidden("#qr-tela"), "o botão Fechar também fecha")
    # encerrar com a tela cheia aberta
    pg.click("#qr-ampliar")
    pg.wait_for_selector("#qr-tela:not([hidden])")
    estado["encerrada"] = True
    pg.wait_for_selector("#qr-box", state="hidden", timeout=8000)
    conf(pg.is_hidden("#qr-tela"), "chamada encerrada: o QR some do painel e a tela cheia fecha sozinha")
    conf(pg.evaluate("document.body.style.overflow") == "", "chamada encerrada: a rolagem da página volta")
    conf(not dialogos and not erros, "professor: nenhum alerta nem erro de JavaScript: %s" % erros)
    b.close()

    # título malicioso na tela cheia (textContent, nunca HTML)
    zerar(titulo=X1)
    b, pg, erros, dialogos = abrir(pw)
    login(pg, "professor", "#v-prof")
    pg.wait_for_selector("#qr-box:not([hidden])")
    pg.click("#qr-ampliar")
    conf(pg.inner_text("#qr-t-titulo") == X1 and pg.evaluate("document.querySelectorAll('#qr-tela img[src=\"x\"], #qr-tela [onerror]').length") == 0
         and pg.evaluate("window.__xss || 0") == 0, "tela cheia: nome de evento malicioso aparece como texto")
    b.close()

    # chamada já encerrada ao entrar: sem QR e sem pedir ao servidor
    zerar(encerrada=True)
    b, pg, erros, _ = abrir(pw)
    login(pg, "professor", "#v-prof")
    pg.wait_for_selector('#hist button[data-i="11"]')
    pg.click('#hist button[data-i="11"]')      # abre a chamada encerrada pelo histórico
    pg.wait_for_selector("#painel:not([hidden])")
    pg.wait_for_timeout(500)
    conf(pg.is_hidden("#qr-box") and estado["pedidos_qr"] == 0, "chamada encerrada aberta pelo histórico: sem QR e sem pedido ao servidor (%d)" % estado["pedidos_qr"])
    b.close()

    # falha ao gerar: avisa dentro do quadro do QR, não pisa nas outras mensagens, não martela o servidor e se recupera sozinha
    zerar(qr="erro")
    b, pg, erros, _ = abrir(pw)
    login(pg, "professor", "#v-prof")
    pg.wait_for_selector("#qr-msg.err")
    conf("Não foi possível gerar o QR code" in pg.inner_text("#qr-msg"), "falha ao gerar: mensagem clara dentro do quadro do QR (%s)" % pg.inner_text("#qr-msg")[:50])
    conf(pg.is_visible("#qr-box") and pg.is_hidden("#qr-img") and pg.is_disabled("#qr-ampliar") and pg.is_disabled("#qr-copiar"),
         "falha ao gerar: sem desenho e com os botões do QR travados")
    conf(pg.is_hidden("#msg-prof") and pg.is_visible("#painel") and pg.is_visible("#btn-fim"), "falha ao gerar: o resto do painel segue funcionando e a mensagem geral não é pisada")
    pg.wait_for_timeout(7000)   # duas rodadas de atualização (3 s)
    conf(estado["pedidos_qr"] == 1, "falha ao gerar: não repete o pedido a cada 3 s (%d pedidos em 7 s)" % estado["pedidos_qr"])
    estado["qr"] = "ok"      # o servidor volta: em até ~15 s o QR aparece sozinho
    pg.wait_for_selector("#qr-img svg", timeout=20000)
    conf(pg.is_visible("#qr-img") and pg.is_hidden("#qr-msg") and pg.is_enabled("#qr-ampliar") and pg.is_enabled("#qr-copiar") and pg.inner_text("#qr-link") == LINK,
         "servidor de volta: o QR aparece sozinho, o aviso some e os botões voltam")
    b.close()

    # celular
    zerar()
    b, pg, erros, _ = abrir(pw, largura=390)
    login(pg, "professor", "#v-prof")
    pg.wait_for_selector("#qr-box:not([hidden])")
    conf(pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "390 px: sem rolagem horizontal com o QR no painel")
    pg.click("#qr-ampliar")
    g = pg.locator("#qr-t-img").bounding_box()
    conf(g["x"] >= 0 and g["x"] + g["width"] <= 390 and pg.evaluate("document.querySelector('#qr-tela').scrollWidth <= 391"), "390 px: o QR projetado cabe na tela (%dpx)" % g["width"])
    b.close()

    # =========================== aluno ===========================
    zerar()
    b, pg, erros, dialogos = abrir(pw, "http://localhost:8765/?aula=11", geo=True)
    pg.wait_for_selector("#v-auth:not([hidden])")
    conf("QR code" in pg.inner_text("#msg-auth") and pg.is_visible("#msg-auth"), "aluno sem login abre o link: a tela de entrada explica o que fazer")
    login(pg, "aluno", "#v-aluno")
    pg.wait_for_selector("#aula-11.destaque")
    conf(True, "depois do login a chamada do QR fica destacada")
    conf("Chamada do QR code encontrada" in pg.inner_text("#msg-aluno"), "aluno: mensagem orienta a aguardar o GPS (%s)" % pg.inner_text("#msg-aluno")[:50])
    conf(pg.evaluate("location.search") == "", "o ?aula= sai do endereço depois de usado (%s)" % pg.evaluate("location.search"))
    pg.wait_for_function("document.querySelector('#aula-11 button[data-id]') && !document.querySelector('#aula-11 button[data-id]').disabled", timeout=8000)
    conf(pg.inner_text("#aula-11 button[data-id]") == "Registrar Presença", "dentro do raio o botão libera, como sempre")
    pg.wait_for_timeout(1000)
    conf(estado["posts_presenca"] == 0, "O LINK DO QR NÃO REGISTRA PRESENÇA SOZINHO: nenhum POST /presenca antes do toque do aluno")
    conf(not erros and not dialogos, "aluno: nenhum erro de JavaScript: %s" % erros)
    b.close()

    # sessão já aberta: vai direto à chamada, sem tela de login
    zerar()
    b, pg, erros, _ = abrir(pw, "http://localhost:8765/?aula=11", sessao=ALUNO)
    pg.wait_for_selector("#aula-11.destaque")
    conf(pg.is_hidden("#v-auth"), "aluno já logado: o link abre direto na chamada")
    pg.wait_for_timeout(21000)
    conf(not pg.evaluate("document.querySelector('#aula-11').classList.contains('destaque')"), "o destaque some sozinho depois de uns 20 s")
    b.close()

    # com duas chamadas abertas, destaca só a do QR
    zerar(abertas=[11, 12])
    b, pg, erros, _ = abrir(pw, "http://localhost:8765/?aula=12", sessao=ALUNO)
    pg.wait_for_selector("#aula-12.destaque")
    conf(pg.locator("#aula-11.destaque").count() == 0 and pg.locator("#aulas .destaque").count() == 1, "duas chamadas abertas: só a do QR é destacada")
    b.close()

    # chamada do link não está aberta (encerrada, outro curso/período)
    zerar()
    b, pg, erros, _ = abrir(pw, "http://localhost:8765/?aula=99", sessao=ALUNO)
    pg.wait_for_selector("#msg-aluno.err")
    conf("não está aberta para você" in pg.inner_text("#msg-aluno"), "chamada do QR não aberta: aviso claro (%s)" % pg.inner_text("#msg-aluno")[:40])
    conf(pg.locator("#aulas .destaque").count() == 0 and pg.evaluate("location.search") == "", "chamada do QR não aberta: nada destacado e endereço limpo")
    b.close()

    # já registrou
    zerar(registrou=True)
    b, pg, erros, _ = abrir(pw, "http://localhost:8765/?aula=11", sessao=ALUNO)
    pg.wait_for_selector("#aula-11.destaque")
    conf("já está registrada" in pg.inner_text("#msg-aluno"), "aluno que já registrou: o app diz que a presença já está registrada")
    b.close()

    # valores estranhos em ?aula= são ignorados
    for v in ("abc", "-3", "0", "1.5", "99999999999", "%3Cscript%3E", ""):
        zerar()
        b, pg, erros, dialogos = abrir(pw, "http://localhost:8765/?aula=" + v, sessao=ALUNO)
        pg.wait_for_selector("#aula-11")
        pg.wait_for_timeout(300)
        conf(pg.locator("#aulas .destaque").count() == 0 and not pg.is_visible("#msg-aluno") and not erros and not dialogos,
             "?aula=%s é ignorado (nada destacado, sem mensagem, sem erro)" % (v or "(vazio)"))
        b.close()

    # professor/coordenação abrindo o link: ignora e limpa o endereço
    zerar()
    b, pg, erros, _ = abrir(pw, "http://localhost:8765/?aula=11", sessao=PROF)
    pg.wait_for_selector("#v-prof:not([hidden])")
    conf(pg.evaluate("location.search") == "" and pg.is_hidden("#msg-aluno"), "professor abrindo o link do QR: ignora e limpa o endereço")
    b.close()

print("\n%d falha(s)" % len(falhas))
raise SystemExit(1 if falhas else 0)
