"""Marcar a sala com o GPS no navegador (CT-1130 a CT-1139): localização aproximada (±50 km, típica de computador sem GPS)
não é usada e vira uma instrução curta para clicar no mapa (não mais um erro vermelho); no celular o texto manda ligar a
"localização precisa"; com GPS bom o ponto é preenchido. A API é simulada; a geolocalização é a do Playwright.
Uso:  pip install playwright && playwright install chromium && python tests/navegador/gps_sala_tela.py
(GC_HTML=caminho.html testa outra cópia do index.html; GC_LEAFLET=pasta/dist do Leaflet 1.9.4 liga o mapa de verdade — sem isso o
mapa não carrega (a CDN é bloqueada no teste) e as verificações do mapa são puladas; sai com código 1 se algo falhar)."""
import json, os, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

WEB = str(Path(__file__).resolve().parents[2]) + "/"
html = open(os.environ.get("GC_HTML", WEB + "index.html"), encoding="utf-8").read()
CFG = {"dominio_aluno": "alunos.unicerrado.edu.br", "dominios_professor": ["unicerrado.edu.br"],
       "matricula_regex": r"^[A-Za-z0-9][A-Za-z0-9._/-]{3,29}$", "coordenacao_aberta": False}
PROF = {"id": 2, "nome": "Marcos Lima", "email": "marcos@unicerrado.edu.br", "perfil": "professor", "departamento": "Exatas"}
LEAFLET = os.environ.get("GC_LEAFLET")
UA_CELULAR = "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36"


def handler(route):
    u = route.request.url
    if LEAFLET and "leaflet.min.js" in u:
        return route.fulfill(status=200, content_type="application/javascript", body=open(LEAFLET + "/leaflet.js", "rb").read())
    if LEAFLET and "leaflet.min.css" in u:
        return route.fulfill(status=200, content_type="text/css", body=open(LEAFLET + "/leaflet.css", "rb").read())
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
        return js({"token": "t", "usuario": PROF})
    if p.startswith("/api/professor/") or p == "/api/aulas/ativas":
        return js([])
    return js({"detail": "mock: " + p}, 404)


falhas = []


def conf(ok, txt):
    print(("OK   " if ok else "FALHA"), txt)
    if not ok:
        falhas.append(txt)


def abrir(pw, precisao, celular=False):
    b = pw.chromium.launch()
    opc = {"viewport": {"width": 1366, "height": 800}, "permissions": ["geolocation"],
           "geolocation": {"latitude": -16.6869, "longitude": -49.2648, "accuracy": precisao}}
    if celular:
        opc["user_agent"] = UA_CELULAR
    c = b.new_context(**opc)
    c.route("**/*", handler)
    pg = c.new_page()
    erros = []
    pg.on("pageerror", lambda e: erros.append(str(e)))
    pg.goto("http://localhost:8765/")
    pg.wait_for_selector("#v-auth:not([hidden])")
    pg.click('.tab[data-p="professor"]')
    pg.fill("#email", PROF["email"])
    pg.fill("#senha", "senha12345")
    pg.click("#btn-auth")
    pg.wait_for_selector("#v-prof:not([hidden])")
    pg.wait_for_selector("#btn-gps")
    return b, pg, erros


def aviso(pg):
    pg.click("#btn-gps")
    pg.wait_for_function("(() => { const e = document.querySelector('#gps-info'); return e && !e.hidden && e.textContent.length > 10 })()", timeout=40000)
    return pg.evaluate("({txt: document.querySelector('#gps-info').textContent, cls: document.querySelector('#gps-info').className})")


with sync_playwright() as pw:
    # ---- computador sem GPS: ±50 km
    b, pg, erros = abrir(pw, 50000)
    r = aviso(pg)
    conf("err" not in r["cls"].split(), f"CT-1130 | computador com localização de ±50 km: o aviso não é mais um erro vermelho (classe: {r['cls']!r})")
    conf("Este computador não tem GPS" in r["txt"] and "±50 km" in r["txt"] and "Clique no mapa" in r["txt"], f"CT-1131 | o texto diz que o computador não tem GPS, a margem e que é para clicar no mapa: {r['txt'][:120]!r}")
    conf(pg.input_value("#lat") == "" and pg.input_value("#lon") == "", "CT-1132 | a localização aproximada NÃO preenche latitude e longitude (continua recusando)")
    pg.wait_for_timeout(900)
    if LEAFLET:
        vis = pg.evaluate("(() => { const r = document.querySelector('#mapa').getBoundingClientRect(); return r.height > 100 && r.bottom > 0 && r.top < window.innerHeight })()")
        conf(vis, "CT-1133 | a tela rola até o mapa para o professor já clicar no lugar da sala")
        box = pg.locator("#mapa").bounding_box()
        pg.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        try:
            pg.wait_for_function("document.querySelector('#lat').value !== ''", timeout=4000)
        except Exception:
            pass
        conf(pg.input_value("#lat") != "" and pg.input_value("#lon") != "", "CT-1134 | clicar no mapa preenche a localização da sala")
    else:
        vis = pg.evaluate("(() => { const r = document.querySelector('#gps-info').getBoundingClientRect(); return r.bottom > 0 && r.top < window.innerHeight })()")
        conf(vis, "CT-1133 | sem o mapa carregado, a tela rola até o aviso (para clicar no mapa use GC_LEAFLET)")
        print("PULADO CT-1134 | clique no mapa (precisa de GC_LEAFLET)")
    conf(not erros, f"CT-1135 | sem erro de JavaScript {erros}")
    b.close()

    # ---- celular com GPS aproximado
    b, pg, erros = abrir(pw, 50000, celular=True)
    r = aviso(pg)
    conf("localização precisa" in r["txt"] and "toque de novo" in r["txt"] and "computador" not in r["txt"] and "err" not in r["cls"].split(),
         f"CT-1136 | celular: o texto manda ligar a 'localização precisa' e tocar de novo (sem falar em computador): {r['txt'][:100]!r}")
    conf(pg.input_value("#lat") == "", "CT-1137 | celular com GPS aproximado também não preenche a sala")
    b.close()

    # ---- GPS bom
    b, pg, erros = abrir(pw, 8)
    pg.click("#btn-gps")
    pg.wait_for_function("document.querySelector('#lat').value !== ''", timeout=30000)
    lat, lon = float(pg.input_value("#lat").replace(",", ".")), float(pg.input_value("#lon").replace(",", "."))
    conf(abs(lat + 16.6869) < 0.0005 and abs(lon + 49.2648) < 0.0005, f"CT-1138 | GPS de ±8 m: a sala é marcada na posição certa ({lat}, {lon})")
    txt = pg.evaluate("document.querySelector('#gps-info').textContent")
    conf("aproximada" not in txt and "não tem GPS" not in txt and not erros, f"CT-1139 | GPS bom: nenhum aviso de localização aproximada {txt[:80]!r} {erros}")
    b.close()

print()
print("RESULTADO:", "TUDO OK" if not falhas else f"{len(falhas)} FALHA(S)")
sys.exit(1 if falhas else 0)
