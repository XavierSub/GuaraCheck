"""App instalável (PWA) no navegador (CT-1991 a CT-2015), contra um servidor HTTP de verdade (sem simulação de rede do Playwright,
que não enxerga o service worker): manifesto e ícones; o service worker registra e passa a controlar a página; a página vem da REDE
quando há sinal (versão nova aparece) e da cópia guardada quando o servidor cai; /api/ nunca é guardado nem respondido de cópia; as
imagens ficam disponíveis sem sinal; uma versão nova do service worker apaga as cópias antigas. Tela do aluno: a dica de instalação
(Android: botão "Instalar o app"; iPhone: passo a passo; some depois de "Agora não", de instalar ou dentro do app já instalado).
Uso:  pip install playwright && playwright install chromium && python tests/navegador/pwa_tela.py
(sai com código 1 se algo falhar)."""
import ast, json, os, re, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

RAIZ = Path(__file__).resolve().parents[2]
HTML = open(os.environ.get("GC_HTML", RAIZ / "index.html"), encoding="utf-8").read()
SW = (RAIZ / "sw.js").read_text(encoding="utf-8")
PORTA = 8766
BASE = f"http://localhost:{PORTA}"
ALUNO = {"id": 1, "nome": "Ana Souza", "email": "ana@alunos.unicerrado.edu.br", "perfil": "aluno", "curso": "Gestão da TI", "periodo": 3, "matricula": "GTI00001"}
CFG = {"dominio_aluno": "alunos.unicerrado.edu.br", "dominios_professor": ["unicerrado.edu.br"],
       "matricula_regex": r"^[A-Za-z0-9][A-Za-z0-9._/-]{3,29}$", "coordenacao_aberta": False}
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"


def manifesto_do_app():
    for n in ast.parse((RAIZ / "app.py").read_text(encoding="utf-8")).body:
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "MANIFESTO" for t in n.targets):
            return ast.literal_eval(n.value)


MANIFESTO = manifesto_do_app()
estado = {"versao_pagina": 1, "sw_versao": "gc-v1", "api_chamadas": 0}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _enviar(self, corpo, tipo, status=200, extra=None):
        if isinstance(corpo, str):
            corpo = corpo.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        p = urlsplit(self.path).path
        if p == "/privacidade":
            return self._enviar("<!doctype html><title>Privacidade</title><body>PAGINA-DE-PRIVACIDADE</body>", "text/html; charset=utf-8")
        if p == "/":
            # o marcador de versão mostra, na página, de onde ela veio (rede nova ou cópia antiga)
            return self._enviar(HTML.replace("<body", f"<body data-pagina=\"v{estado['versao_pagina']}\"", 1), "text/html; charset=utf-8")
        if p == "/sw.js":
            return self._enviar(SW.replace("gc-v1", estado["sw_versao"]), "text/javascript", extra={"Cache-Control": "no-cache"})
        if p == "/manifest.webmanifest":
            return self._enviar(json.dumps(MANIFESTO), "application/manifest+json")
        if p.startswith("/assets/"):
            arq = RAIZ / p[1:]
            if arq.is_file():
                return self._enviar(arq.read_bytes(), "image/png")
            return self._enviar("{}", "application/json", 404)
        if p.startswith("/api/"):
            estado["api_chamadas"] += 1
            rotas = {"/api/config": CFG, "/api/cursos": [{"id": 1, "nome": "Gestão da TI"}], "/api/aulas/ativas": [],
                     "/api/me": {"nome": ALUNO["nome"], "matricula": ALUNO["matricula"], "email": ALUNO["email"], "curso": ALUNO["curso"], "periodo": 3, "avisos_email": True},
                     "/api/aluno/presencas": [], "/api/aluno/notas": []}
            if p in rotas:
                return self._enviar(json.dumps(rotas[p]), "application/json")        # como o FastAPI: sem Cache-Control
        return self._enviar('{"detail":"nao achei"}', "application/json", 404)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if urlsplit(self.path).path == "/api/auth/login":
            return self._enviar(json.dumps({"token": "t", "usuario": ALUNO}), "application/json")
        return self._enviar('{"detail":"nao achei"}', "application/json", 404)


class Servidor:
    def __init__(self):
        self.srv = None

    def ligar(self):
        self.srv = HTTPServer(("127.0.0.1", PORTA), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def desligar(self):
        if self.srv:
            self.srv.shutdown()
            self.srv.server_close()
            self.srv = None


falhas = []


def conf(ok, txt):
    print(("OK   " if ok else "FALHA"), txt)
    if not ok:
        falhas.append(txt)


def js(pg, expr):
    return pg.evaluate(expr)


def com_sw(pw, srv, **ctx):
    b = pw.chromium.launch()
    c = b.new_context(**ctx)
    pg = c.new_page()
    erros = []
    pg.on("pageerror", lambda e: erros.append(str(e)))
    pg.goto(BASE + "/")
    pg.wait_for_selector("#v-auth:not([hidden])")
    pg.evaluate("navigator.serviceWorker.ready.then(() => true)")
    return b, c, pg, erros


with sync_playwright() as pw:
    srv = Servidor()
    srv.ligar()

    # =========================== manifesto e ícones ===========================
    b, c, pg, erros = com_sw(pw, srv)
    conf(pg.get_attribute('link[rel="manifest"]', "href") == "/manifest.webmanifest", "a página aponta para /manifest.webmanifest")
    m = pg.evaluate("fetch('/manifest.webmanifest').then(async r => ({tipo: r.headers.get('content-type'), j: await r.json()}))")
    conf(m["tipo"].startswith("application/manifest+json") and m["j"]["display"] == "standalone" and m["j"]["start_url"] == "/", "o manifesto abre em tela cheia (standalone) na raiz")
    resp = pg.evaluate("Promise.all(%s.map(s => fetch(s).then(r => [r.status, r.headers.get('content-type')])))" % json.dumps([i["src"] for i in m["j"]["icons"]]))
    conf(all(r == [200, "image/png"] for r in resp), f"todos os ícones do manifesto respondem 200 image/png: {resp}")

    # =========================== o service worker assume a página ===========================
    pg.reload()
    pg.wait_for_selector("#v-auth:not([hidden])")
    pg.wait_for_function("navigator.serviceWorker.controller !== null", timeout=10000)
    conf(js(pg, "navigator.serviceWorker.controller.scriptURL").endswith("/sw.js"), "depois de recarregar, o service worker /sw.js controla a página")
    cache = js(pg, "caches.keys()")
    conf(cache == ["gc-v1"], f"a cópia da casca foi guardada em 'gc-v1': {cache}")
    guardadas = js(pg, "caches.open('gc-v1').then(c => c.keys()).then(k => k.map(r => new URL(r.url).pathname).sort())")
    casca = {"/", "/assets/icone-192.png", "/assets/guara-nome-branco.png", "/assets/guara-pino.png", "/assets/favicon.png"}
    conf(casca <= set(guardadas) and all(x == "/" or x.startswith("/assets/") for x in guardadas),
         f"a cópia tem a casca (página + imagens do topo) e só a página e /assets/: {guardadas}")

    # =========================== com sinal: a página vem da REDE ===========================
    estado["versao_pagina"] = 2
    pg.reload()
    pg.wait_for_selector("#v-auth:not([hidden])")
    conf(pg.get_attribute("body", "data-pagina") == "v2", "com sinal, a versão NOVA da página aparece (rede primeiro, não a cópia velha)")
    copia = js(pg, "caches.open('gc-v1').then(c => c.match('/')).then(r => r.text())")
    conf('data-pagina="v2"' in copia, "e a cópia guardada é atualizada para a versão nova")

    # =========================== outras páginas do servidor não entram na cópia ===========================
    pg.goto(BASE + "/privacidade")
    conf("PAGINA-DE-PRIVACIDADE" in pg.inner_text("body"), "com sinal, /privacidade abre normalmente pela rede")
    copia = js(pg, "caches.open('gc-v1').then(c => c.match('/')).then(r => r.text())")
    conf('data-pagina="v2"' in copia and "PAGINA-DE-PRIVACIDADE" not in copia, "e a cópia da página principal não foi trocada pela de privacidade")
    conf(js(pg, "caches.open('gc-v1').then(c => c.match('/privacidade'))") is None, "/privacidade não é guardada")
    pg.goto(BASE + "/")
    pg.wait_for_selector("#v-auth:not([hidden])")

    # =========================== /api/ nunca é guardado ===========================
    estado["api_chamadas"] = 0
    js(pg, "fetch('/api/config').then(r => r.json())")
    js(pg, "fetch('/api/config').then(r => r.json())")
    conf(estado["api_chamadas"] == 2, f"duas consultas a /api/ chegam as duas ao servidor (nada responde de cópia) ({estado['api_chamadas']})")
    conf(js(pg, "caches.match('/api/config')") is None and js(pg, "caches.keys().then(async ks => (await Promise.all(ks.map(k => caches.open(k).then(c => c.keys())))).flat().filter(r => r.url.includes('/api/')).length)") == 0,
         "nada de /api/ no armazenamento do service worker")

    # =========================== sem sinal (servidor fora do ar) ===========================
    srv.desligar()
    pg.goto(BASE + "/")
    pg.wait_for_selector("#v-auth:not([hidden])")
    conf(pg.get_attribute("body", "data-pagina") == "v2" and pg.is_visible("#btn-auth"), "servidor fora do ar: a página abre pela cópia (tela de entrada completa)")
    pg.goto(BASE + "/?aula=11")
    pg.wait_for_selector("#v-auth:not([hidden])")
    conf(pg.is_visible("#btn-auth"), "o link de uma chamada (/?aula=11) também abre pela cópia")
    erro = js(pg, "fetch('/api/config').then(() => 'respondeu').catch(e => e.name)")
    conf(erro == "TypeError", f"sem sinal, /api/ FALHA (nenhuma cópia responde no lugar do servidor): {erro}")
    img = js(pg, "fetch('/assets/icone-192.png').then(r => r.ok ? r.headers.get('content-type') : 'erro').catch(() => 'falhou')")
    conf(img == "image/png", "sem sinal, as imagens do site vêm da cópia")
    pg.click('.tab[data-p="aluno"]'); pg.fill("#email", ALUNO["email"]); pg.fill("#senha", "senha12345")
    pg.click("#btn-auth")
    pg.wait_for_function("document.querySelector('#msg-auth') && document.querySelector('#msg-auth').textContent.includes('Sem conexão')", timeout=8000)
    conf(True, "tentar entrar sem sinal mostra 'Sem conexão com o servidor' (e não um login falso)")
    conf(not erros, "nenhum erro de JavaScript: %s" % erros)
    b.close()

    # =========================== versão nova do service worker apaga as cópias antigas ===========================
    srv.ligar()
    b, c, pg, erros = com_sw(pw, srv)
    pg.reload(); pg.wait_for_function("navigator.serviceWorker.controller !== null", timeout=10000)
    conf(js(pg, "caches.keys()") == ["gc-v1"], "(recomeço) uma cópia 'gc-v1'")
    estado["sw_versao"] = "gc-v2"
    js(pg, "navigator.serviceWorker.getRegistration().then(r => r.update())")
    for _ in range(40):                                   # espera (até 10 s) o service worker novo instalar, assumir e limpar
        if js(pg, "caches.keys().then(k => k.join())") == "gc-v2":
            break
        pg.wait_for_timeout(250)
    conf(js(pg, "caches.keys()") == ["gc-v2"], "versão nova do service worker: a cópia 'gc-v1' foi apagada e só existe 'gc-v2'")
    guardadas = js(pg, "caches.open('gc-v2').then(c => c.keys()).then(k => k.length)")
    conf(guardadas == 5, f"a versão nova já guardou a casca ({guardadas} itens)")
    b.close()
    estado["sw_versao"] = "gc-v1"

    # =========================== dica de instalação (tela do aluno) ===========================
    def entrar_aluno(pg):
        pg.click('.tab[data-p="aluno"]'); pg.fill("#email", ALUNO["email"]); pg.fill("#senha", "senha12345"); pg.click("#btn-auth")
        pg.wait_for_selector("#v-aluno:not([hidden])")

    FALSO_PROMPT = """() => { const e = new Event('beforeinstallprompt', {cancelable: true});
        e.prompt = () => { window.__prompt = (window.__prompt || 0) + 1; return Promise.resolve(); };
        e.userChoice = Promise.resolve({outcome: 'accepted'}); window.dispatchEvent(e); return e.defaultPrevented; }"""

    # --- Android/Chrome: o navegador oferece instalar
    b = pw.chromium.launch(); c = b.new_context(viewport={"width": 390, "height": 800}); pg = c.new_page(); erros = []
    pg.on("pageerror", lambda e: erros.append(str(e)))
    pg.goto(BASE + "/"); pg.wait_for_selector("#v-auth:not([hidden])")
    conf(js(pg, "document.querySelector('#instalar').hidden") is True, "antes do navegador oferecer, a dica de instalação não aparece")
    segurou = pg.evaluate(FALSO_PROMPT)
    entrar_aluno(pg)
    conf(segurou is True, "o evento de instalação é guardado (preventDefault) para o botão usar depois")
    conf(pg.is_visible("#instalar") and pg.is_visible("#instalar-btn") and "Instalar o app" in pg.inner_text("#instalar-btn"), "tela do aluno: card 'Instale o Guará Check' com o botão 'Instalar o app'")
    pg.click("#instalar-btn")
    pg.wait_for_function("document.querySelector('#instalar').hidden === true")
    conf(js(pg, "window.__prompt") == 1, "o botão chama o pedido de instalação do navegador uma vez e o card some")
    pg.evaluate(FALSO_PROMPT)
    conf(pg.is_visible("#instalar"), "(se o navegador oferecer de novo, o card volta)")
    pg.click("#instalar-fecha")
    conf(not pg.is_visible("#instalar"), "'Agora não' esconde o card")
    pg.reload(); pg.wait_for_selector("#v-aluno:not([hidden])"); pg.evaluate(FALSO_PROMPT)     # a sessão continua aberta: volta direto à tela do aluno
    conf(not pg.is_visible("#instalar"), "depois de 'Agora não', o card não volta nas próximas visitas")
    pg.evaluate("window.dispatchEvent(new Event('appinstalled'))")
    conf(not pg.is_visible("#instalar"), "'appinstalled' também esconde")
    conf(js(pg, "document.documentElement.scrollWidth <= window.innerWidth + 1"), "no celular (390 px) a página não ganha rolagem horizontal")
    conf(not erros, "dica Android: nenhum erro de JavaScript: %s" % erros)
    b.close()

    # --- appinstalled depois de mostrar
    b = pw.chromium.launch(); c = b.new_context(); pg = c.new_page()
    pg.goto(BASE + "/"); pg.wait_for_selector("#v-auth:not([hidden])"); pg.evaluate(FALSO_PROMPT); entrar_aluno(pg)
    conf(pg.is_visible("#instalar"), "(novo contexto) o card aparece")
    pg.evaluate("window.dispatchEvent(new Event('appinstalled'))")
    conf(not pg.is_visible("#instalar"), "app instalado: o card some na hora")
    b.close()

    # --- iPhone/iPad: não existe botão; só o passo a passo
    b = pw.chromium.launch(); c = b.new_context(user_agent=IPHONE, viewport={"width": 390, "height": 800}); pg = c.new_page()
    pg.goto(BASE + "/"); pg.wait_for_selector("#v-auth:not([hidden])"); entrar_aluno(pg)
    t = pg.inner_text("#instalar") if pg.is_visible("#instalar") else ""
    conf(pg.is_visible("#instalar") and "Compartilhar" in t and "Adicionar à Tela de Início" in t and not pg.is_visible("#instalar-btn"), f"iPhone: passo a passo (Compartilhar > Adicionar à Tela de Início), sem botão: {t!r}")
    b.close()

    # --- já dentro do app instalado (modo standalone): nada a oferecer
    b = pw.chromium.launch(); c = b.new_context(user_agent=IPHONE); pg = c.new_page()
    pg.add_init_script("""const mm = window.matchMedia.bind(window); window.matchMedia = q => q.includes('display-mode: standalone')
        ? {matches: true, media: q, addEventListener(){}, removeEventListener(){}, addListener(){}, removeListener(){}} : mm(q);""")
    pg.goto(BASE + "/"); pg.wait_for_selector("#v-auth:not([hidden])"); pg.evaluate(FALSO_PROMPT); entrar_aluno(pg)
    conf(not pg.is_visible("#instalar"), "dentro do app já instalado (standalone) a dica não aparece")
    b.close()

    # --- computador sem oferta de instalação: nada
    b = pw.chromium.launch(); c = b.new_context(); pg = c.new_page()
    pg.goto(BASE + "/"); pg.wait_for_selector("#v-auth:not([hidden])"); entrar_aluno(pg)
    conf(not pg.is_visible("#instalar"), "navegador que não oferece instalar (e não é iPhone): a dica não aparece")
    b.close()

    srv.desligar()

print()
print("FALHA=%d" % len(falhas))
sys.exit(1 if falhas else 0)
