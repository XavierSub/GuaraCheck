"""Procurar um lugar pelo nome no mapa da sala (CT-1140 a CT-1158): o professor, num computador sem GPS, digita
"Passeio das Águas Shopping" e o mapa vai até lá, com o marcador da sala e a latitude/longitude preenchidas. A API é simulada
(GET /api/lugares); a página NUNCA fala direto com o serviço de busca. Enter na caixa não pode abrir a chamada (a caixa fica
dentro do formulário). Nomes vindos de fora são texto, nunca HTML.
Uso:  pip install playwright && playwright install chromium && python tests/navegador/busca_lugar_tela.py
(GC_HTML=outra cópia do index.html; GC_LEAFLET=pasta/dist do Leaflet 1.9.4 liga o mapa de verdade — sem isso as verificações do
mapa são puladas; sai com código 1 se algo falhar)."""
import json, os, sys, urllib.parse
from pathlib import Path
from playwright.sync_api import sync_playwright

WEB = str(Path(__file__).resolve().parents[2]) + "/"
html = open(os.environ.get("GC_HTML", WEB + "index.html"), encoding="utf-8").read()
CFG = {"dominio_aluno": "alunos.unicerrado.edu.br", "dominios_professor": ["unicerrado.edu.br"],
       "matricula_regex": r"^[A-Za-z0-9][A-Za-z0-9._/-]{3,29}$", "coordenacao_aberta": False}
PROF = {"id": 2, "nome": "Marcos Lima", "email": "marcos@unicerrado.edu.br", "perfil": "professor", "departamento": "Exatas"}
LEAFLET = os.environ.get("GC_LEAFLET")
PASSEIO = {"nome": "Passeio das Águas Shopping, Avenida Perimetral Norte, Goiânia, Goiás, Brasil", "lat": -16.71, "lon": -49.275,
           "caixa": [-16.711, -49.276, -16.709, -49.274]}
FLAMBOYANT = {"nome": "Shopping Flamboyant, Goiânia, Goiás, Brasil", "lat": -16.704, "lon": -49.237,
              "caixa": [-16.705, -49.238, -16.703, -49.236]}
PARQUE = {"nome": "Parque Flamboyant, Goiânia, Goiás, Brasil", "lat": -16.700, "lon": -49.240, "caixa": None}
MSG503 = "A busca de lugares não respondeu agora. Clique no mapa no lugar da sala ou tente a busca de novo daqui a pouco."

E = {"res": [PASSEIO, FLAMBOYANT, PARQUE], "status": 200, "segurar": False}
PEDIDOS, POSTS, SEGURADAS, EXTERNOS = [], [], [], []


def handler(route):
    u = route.request.url
    if LEAFLET and "leaflet.min.js" in u:
        return route.fulfill(status=200, content_type="application/javascript", body=open(LEAFLET + "/leaflet.js", "rb").read())
    if LEAFLET and "leaflet.min.css" in u:
        return route.fulfill(status=200, content_type="text/css", body=open(LEAFLET + "/leaflet.css", "rb").read())
    if "localhost:8765" not in u:
        if "nominatim" in u:
            EXTERNOS.append(u)
        return route.abort()
    p, _, qs = u.split("localhost:8765", 1)[1].partition("?")
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
        return js({"token": "t", "usuario": PROF})
    if p == "/api/lugares":
        PEDIDOS.append({"q": urllib.parse.parse_qs(qs).get("q", [""])[0], "auth": route.request.headers.get("authorization")})
        if E["segurar"]:
            SEGURADAS.append(route)
            return
        return js({"resultados": E["res"]} if E["status"] == 200 else {"detail": MSG503}, E["status"])
    if p == "/api/aulas" and route.request.method == "POST":
        POSTS.append(route.request.post_data)
        return js({"detail": "mock"}, 500)
    if p.startswith("/api/professor/") or p == "/api/aulas/ativas":
        return js([])
    return js({"detail": "mock: " + p}, 404)


falhas = []


def conf(ok, txt):
    print(("OK   " if ok else "FALHA"), txt)
    if not ok:
        falhas.append(txt)


def abrir(pw, largura=1366, altura=800, mapa=True):
    b = pw.chromium.launch()
    c = b.new_context(viewport={"width": largura, "height": altura})
    def rota(route):
        if not mapa and ("leaflet" in route.request.url and "localhost" not in route.request.url):
            return route.abort()
        return handler(route)
    c.route("**/*", rota)
    pg = c.new_page()
    erros = []
    pg.on("pageerror", lambda e: erros.append(str(e)))
    pg.goto("http://localhost:8765/")
    pg.wait_for_selector("#v-auth:not([hidden])")
    entrar(pg)
    return b, pg, erros


def entrar(pg):
    pg.click('.tab[data-p="professor"]')
    pg.fill("#email", PROF["email"])
    pg.fill("#senha", "senha12345")
    pg.click("#btn-auth")
    pg.wait_for_selector("#v-prof:not([hidden])")
    pg.wait_for_selector("#busca-q")


def procurar(pg, texto, enter=False):
    pg.fill("#busca-q", texto)
    pg.press("#busca-q", "Enter") if enter else pg.click("#busca-ok")


def esperar_msg(pg, timeout=8000):
    pg.wait_for_function("(() => { const e = document.querySelector('#busca-msg'); return e && !e.hidden && e.textContent.length > 3 })()", timeout=timeout)


def estado(pg):
    return pg.evaluate("""(() => ({lat: document.querySelector('#lat').value, lon: document.querySelector('#lon').value,
        msg: document.querySelector('#busca-msg').textContent, cls: document.querySelector('#busca-msg').className,
        itens: [...document.querySelectorAll('#busca-res button')].map(b => ({t: b.textContent, atual: b.getAttribute('aria-current')})),
        listaVisivel: !document.querySelector('#busca-res').hidden,
        btn: document.querySelector('#busca-ok').textContent, btnOff: document.querySelector('#busca-ok').disabled}))()""")


def mapa_estado(pg):
    return pg.evaluate("(() => { const c = S.map.getCenter(), m = S.marker && S.marker.getLatLng(); return {c:[c.lat, c.lng], m: m && [m.lat, m.lng], z: S.map.getZoom()} })()")


perto = lambda a, b, tol=0.0005: a is not None and abs(a[0] - b[0]) < tol and abs(a[1] - b[1]) < tol

with sync_playwright() as pw:
    b, pg, erros = abrir(pw)
    # ---- a caixa existe e é acessível
    rot = pg.evaluate("document.querySelector('label[for=busca-q]').textContent")
    conf(rot == "Procurar um lugar no mapa" and pg.is_visible("#busca-q") and pg.is_visible("#busca-ok"), f"CT-1140 | a caixa 'Procurar um lugar no mapa' e o botão aparecem para o professor ({rot!r})")
    conf(pg.get_by_label("Procurar um lugar no mapa").count() == 1 and pg.get_attribute("#busca-q", "placeholder").startswith("Ex.: Passeio das Águas"),
         "CT-1140b | a caixa tem nome acessível e um exemplo no campo")

    # ---- busca pelo botão
    procurar(pg, "  Passeio das Águas   Shopping ")
    esperar_msg(pg)
    s = estado(pg)
    conf(len(PEDIDOS) == 1 and PEDIDOS[0]["q"] == "Passeio das Águas Shopping" and PEDIDOS[0]["auth"] == "Bearer t",
         f"CT-1141 | uma única chamada a /api/lugares, com o texto limpo e o token do professor: {PEDIDOS}")
    conf(not EXTERNOS, f"CT-1141b | a página nunca fala direto com o serviço de busca de fora {EXTERNOS}")
    conf(abs(float(s["lat"]) - PASSEIO["lat"]) < 1e-6 and abs(float(s["lon"]) - PASSEIO["lon"]) < 1e-6,
         f"CT-1142 | o primeiro resultado preenche latitude e longitude da sala ({s['lat']}, {s['lon']})")
    if LEAFLET:
        m = mapa_estado(pg)
        conf(perto(m["m"], (PASSEIO["lat"], PASSEIO["lon"])) and perto(m["c"], (PASSEIO["lat"], PASSEIO["lon"]), 0.002) and 16 <= m["z"] <= 18,
             f"CT-1143 | o mapa vai até o shopping, aproximado (zoom {m['z']}) e o marcador da sala fica lá: {m}")
    else:
        print("PULADO CT-1143 | mapa (precisa de GC_LEAFLET)")
    conf(len(s["itens"]) == 3 and s["itens"][0]["atual"] == "true" and s["itens"][1]["atual"] is None and s["listaVisivel"]
         and s["itens"][0]["t"] == PASSEIO["nome"] and "Marquei o primeiro resultado" in s["msg"] and "err" not in s["cls"].split(),
         f"CT-1144 | aparecem os 3 resultados, o primeiro marcado, com aviso verde: {s['msg'][:60]!r}")

    # ---- escolher outro resultado
    pg.click("#busca-res li:nth-child(2) button")
    s = estado(pg)
    conf(abs(float(s["lat"]) - FLAMBOYANT["lat"]) < 1e-6 and s["itens"][1]["atual"] == "true" and s["itens"][0]["atual"] is None,
         f"CT-1145 | escolher o segundo resultado move o ponto e a marca ({s['lat']}, {s['lon']})")
    if LEAFLET:
        m = mapa_estado(pg)
        conf(perto(m["m"], (FLAMBOYANT["lat"], FLAMBOYANT["lon"])), f"CT-1145b | o marcador acompanha a escolha {m['m']}")
    pg.click("#busca-res li:nth-child(3) button")      # sem caixa: usa o ponto e zoom 18
    s = estado(pg)
    conf(abs(float(s["lat"]) - PARQUE["lat"]) < 1e-6, "CT-1145c | resultado sem caixa também marca o ponto")
    if LEAFLET:
        m = mapa_estado(pg)
        conf(perto(m["c"], (PARQUE["lat"], PARQUE["lon"])) and m["z"] == 18, f"CT-1145d | sem caixa: centraliza no ponto com zoom 18 {m}")

    # ---- Enter busca, mas NÃO abre a chamada
    n = len(PEDIDOS)
    procurar(pg, "Shopping Flamboyant", enter=True)
    esperar_msg(pg)
    pg.wait_for_timeout(300)
    conf(len(PEDIDOS) == n + 1 and not POSTS and pg.evaluate("document.querySelector('#msg-prof').hidden"),
         f"CT-1146 | Enter na caixa procura e não envia o formulário (nenhum POST /api/aulas, nenhum aviso de formulário): {POSTS}")

    # ---- texto curto
    n = len(PEDIDOS)
    procurar(pg, "ab")
    s = estado(pg)
    conf(len(PEDIDOS) == n and "pelo menos 3 letras" in s["msg"] and "err" in s["cls"].split() and not s["listaVisivel"],
         f"CT-1147 | texto de 2 letras: aviso e nenhuma chamada ({s['msg'][:50]!r})")

    # ---- nada encontrado: não mexe no ponto atual
    E["res"] = []
    antes = estado(pg)
    procurar(pg, "lugar que não existe")
    esperar_msg(pg)
    s = estado(pg)
    conf("Não achei esse lugar" in s["msg"] and (s["lat"], s["lon"]) == (antes["lat"], antes["lon"]) and not s["listaVisivel"] and "err" not in s["cls"].split(),
         f"CT-1148 | sem resultado: avisa o que fazer e mantém o ponto que já estava ({s['msg'][:60]!r})")

    # ---- erro do servidor (503)
    E["status"] = 503
    procurar(pg, "Shopping Flamboyant 2")
    pg.wait_for_function("document.querySelector('#busca-ok').textContent === 'Procurar'")
    esperar_msg(pg)
    s = estado(pg)
    conf(s["msg"] == MSG503 and "err" in s["cls"].split() and not s["btnOff"] and (s["lat"], s["lon"]) == (antes["lat"], antes["lon"]),
         f"CT-1149 | busca fora do ar: mostra a mensagem do servidor, destrava o botão e não mexe no ponto ({s['msg'][:50]!r})")
    E["status"], E["res"] = 200, [PASSEIO, FLAMBOYANT, PARQUE]

    # ---- uma busca de cada vez
    E["segurar"] = True
    n = len(PEDIDOS)
    pg.fill("#busca-q", "Praça Cívica")
    pg.press("#busca-q", "Enter")
    pg.wait_for_function("document.querySelector('#busca-ok').disabled")
    s = estado(pg)
    pg.press("#busca-q", "Enter")
    pg.click("#busca-ok", force=True)
    pg.wait_for_timeout(300)
    conf(s["btnOff"] and s["btn"] == "Procurando…" and len(PEDIDOS) == n + 1,
         f"CT-1150 | enquanto procura, o botão mostra 'Procurando…' e Enter ou novo clique não mandam outra busca ({len(PEDIDOS) - n} chamada)")
    SEGURADAS.pop().fulfill(status=200, content_type="application/json", body=json.dumps({"resultados": [PASSEIO]}))
    pg.wait_for_function("document.querySelector('#busca-ok').textContent === 'Procurar'")
    s = estado(pg)
    conf(not s["btnOff"] and len(s["itens"]) == 0 and not s["listaVisivel"] and "Lugar marcado no mapa" in s["msg"],
         f"CT-1150b | com 1 resultado só: marca o lugar e não mostra lista ({s['msg'][:40]!r})")
    E["segurar"] = False

    # ---- nome vindo de fora é texto, nunca HTML
    ruim = '<img src=x onerror="window.__xss=1"><b>Shopping</b>'
    E["res"] = [dict(PASSEIO, nome=ruim), FLAMBOYANT]
    procurar(pg, "teste de segurança")
    esperar_msg(pg)
    pg.wait_for_timeout(300)
    conf(pg.evaluate("window.__xss") is None and estado(pg)["itens"][0]["t"] == ruim and pg.locator("#busca-res img, #busca-res b").count() == 0,
         "CT-1151 | um nome com HTML aparece como texto e nada executa")
    E["res"] = [PASSEIO, FLAMBOYANT, PARQUE]

    # ---- depois da busca, clicar no mapa ainda ajusta o ponto
    if LEAFLET:
        procurar(pg, "Passeio das Águas Shopping")
        esperar_msg(pg)
        pg.locator("#mapa").scroll_into_view_if_needed()
        pg.wait_for_timeout(300)
        box = pg.locator("#mapa").bounding_box()
        pg.mouse.click(box["x"] + box["width"] * 0.3, box["y"] + box["height"] * 0.7)
        pg.wait_for_timeout(300)
        s = estado(pg)
        conf(abs(float(s["lat"]) - PASSEIO["lat"]) > 1e-5, f"CT-1152 | clicar no mapa depois da busca ajusta o ponto ({s['lat']})")
    else:
        print("PULADO CT-1152 | clique no mapa (precisa de GC_LEAFLET)")

    # ---- sair limpa a busca e o ponto marcado não vaza para o próximo login
    procurar(pg, "Shopping Flamboyant")
    esperar_msg(pg)
    pg.click("#btn-sair")
    pg.wait_for_selector("#v-auth:not([hidden])")
    entrar(pg)
    s = estado(pg)
    conf(pg.input_value("#busca-q") == "" and s["itens"] == [] and not s["listaVisivel"] and s["msg"] == "" and not s["btnOff"] and s["btn"] == "Procurar",
         "CT-1153 | ao sair e entrar de novo, a caixa, a lista e o aviso da busca estão limpos")
    conf(not erros, f"CT-1155 | sem erro de JavaScript {erros}")
    conf(not EXTERNOS, f"CT-1155b | nenhuma chamada direta ao serviço de busca em toda a sessão {EXTERNOS}")
    b.close()

    # ---- celular pequeno: nada estoura a largura
    b, pg, erros = abrir(pw, 320, 640)
    procurar(pg, "Passeio das Águas Shopping")
    esperar_msg(pg)
    dims = pg.evaluate("""(() => ({sw: document.documentElement.scrollWidth, iw: window.innerWidth,
        caixa: document.querySelector('#busca-q').getBoundingClientRect().width, botao: document.querySelector('#busca-ok').getBoundingClientRect().height,
        lista: Math.max(...[...document.querySelectorAll('#busca-res button')].map(x => x.getBoundingClientRect().right))}))()""")
    conf(dims["sw"] <= dims["iw"] and dims["lista"] <= dims["iw"] and dims["caixa"] >= 140 and dims["botao"] >= 24,
         f"CT-1156 | a 320 px de largura nada passa da tela e os alvos de toque têm tamanho ({dims})")
    b.close()

    # ---- sem o mapa (Leaflet não carregou): a busca ainda preenche latitude e longitude
    b, pg, erros = abrir(pw, mapa=False)
    procurar(pg, "Passeio das Águas Shopping")
    esperar_msg(pg)
    s = estado(pg)
    conf(abs(float(s["lat"]) - PASSEIO["lat"]) < 1e-6 and not erros, f"CT-1157 | sem o mapa carregado, a busca ainda preenche a sala e não dá erro {erros}")
    b.close()

print()
print("RESULTADO:", "TUDO OK" if not falhas else f"{len(falhas)} FALHA(S)")
sys.exit(1 if falhas else 0)
