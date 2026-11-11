"""F4 no navegador (CT-490 a CT-499): o botão "Registrar Presença" do aluno com GPS simulado.
Confere o que o celular manda ao servidor (campos e formato do corpo do check-in), o botão travado fora do raio, GPS negado,
navegador sem geolocalização, duplo clique, erros do servidor na tela, identidade do aparelho estável e texto malicioso do evento.
A API é simulada (imita o FastAPI) e o GPS também: um navigator.geolocation de mentira entrega uma leitura nova a cada 0,5 s com
oscilação de ~0,2 m, como um receptor de verdade (o GPS simulado do Chromium não entrega leituras novas a quem já está ouvindo).
O servidor real e o GPS do celular de verdade não rodam aqui.
Uso:  pip install playwright && playwright install chromium && python tests/navegador/checkin_tela.py
(GC_HTML=caminho.html testa outra cópia do index.html; sai com código 1 se algo falhar)."""
import json, os, re, time
from pathlib import Path
from playwright.sync_api import sync_playwright

WEB = str(Path(__file__).resolve().parents[2]) + "/"
html = open(os.environ.get("GC_HTML", WEB + "index.html"), encoding="utf-8").read()
CFG = {"dominio_aluno": "alunos.unicerrado.edu.br", "dominios_professor": ["unicerrado.edu.br"],
       "matricula_regex": r"^[A-Za-z0-9][A-Za-z0-9._/-]{3,29}$", "coordenacao_aberta": False}
ALUNO = {"id": 1, "nome": "Ana Souza", "email": "ana@alunos.unicerrado.edu.br", "perfil": "aluno", "curso": "Gestão da TI",
         "periodo": 3, "matricula": "GTI202610405"}
LAT, LON = -18.4157, -49.2183
X1 = '<img src=x onerror="window.__xss=(window.__xss||0)+1">'
EVENTO = {"id": 11, "titulo": "Algoritmos", "professor": "Marcos Lima", "latitude": LAT, "longitude": LON, "raio_metros": 50,
          "tipo": "aula", "local_tipo": "dentro", "local_descricao": "Sala 12", "curso": None, "periodo": None,
          "disciplina": "Algoritmos", "aberta_em": "2026-11-11T14:00:00+00:00", "ja_registrou": False}
DENTRO = {"lat": LAT, "lon": LON, "acc": 10}
FORA = {"lat": LAT + 0.001, "lon": LON, "acc": 10}          # ~111 m

GPS_FALSO = """
(() => {
  const st = window.__gps = Object.assign({lat: 0, lon: 0, acc: 10, erro: 0, jitter: 2e-6, ouvindo: 0}, __INICIAL__);
  let n = 0, seq = 0; const ws = new Map();
  const leitura = () => { n++; return {coords: {latitude: st.lat + ((n % 3) - 1) * st.jitter, longitude: st.lon + (n % 2) * st.jitter,
    accuracy: st.acc + (n % 2), altitude: null, altitudeAccuracy: null, heading: null, speed: null}, timestamp: Date.now()}; };
  const entrega = (ok, err) => st.erro ? err({code: st.erro, message: 'erro simulado'}) : ok(leitura());
  Object.defineProperty(navigator, 'geolocation', {configurable: true, value: {
    watchPosition(ok, err) { const id = ++seq; setTimeout(() => entrega(ok, err), 50); ws.set(id, setInterval(() => entrega(ok, err), 500)); st.ouvindo = ws.size; return id; },
    clearWatch(id) { clearInterval(ws.get(id)); ws.delete(id); st.ouvindo = ws.size; },
    getCurrentPosition(ok, err) { setTimeout(() => entrega(ok, err), 50); }}});
})();
"""
posts, estado = [], {"post": "ok", "registrou": False, "evento": EVENTO}


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
        return js({"token": "t", "usuario": ALUNO})
    if p == "/api/aulas/ativas":
        return js([dict(estado["evento"], ja_registrou=estado["registrou"])])
    if p == "/api/me":
        return js({"nome": ALUNO["nome"], "matricula": ALUNO["matricula"], "email": ALUNO["email"], "curso": ALUNO["curso"], "periodo": 3})
    if p == "/api/aluno/presencas":
        return js([{"id": 7, "registrado_em": "2026-11-11T14:05:00+00:00", "titulo": "Algoritmos", "tipo": "aula",
                    "local_tipo": "dentro", "disciplina": "Algoritmos", "periodo": 3, "professor": "Marcos Lima"}] if estado["registrou"] else [])
    if p == "/api/aluno/notas":
        return js([])
    if p == "/api/aulas/11/presenca" and route.request.method == "POST":
        posts.append(json.loads(route.request.post_data))
        m = estado["post"]
        if m == "400":
            return js({"detail": "Você está a 400 m da sala; o limite desta aula é 50 m. Aproxime-se e tente novamente."}, 400)
        if m == "409":
            return js({"detail": "Este aparelho já foi usado para registrar a presença de outro aluno nesta aula. Cada aluno deve usar o próprio celular."}, 409)
        if m == "422":
            return js({"detail": "Sinal de GPS impreciso (±200 m). Ative o GPS, vá para um local aberto e tente de novo."}, 422)
        if m == "500":
            return route.fulfill(status=500, content_type="text/plain", body="Internal Server Error")
        time.sleep(0.3)
        estado["registrou"] = True
        return js({"ok": True, "ja_registrado": False, "distancia_m": 3.2, "aula": "Algoritmos"})
    return js({"detail": "mock: " + p}, 404)


falhas = []


def conf(ok, txt):
    print(("OK   " if ok else "FALHA"), txt)
    if not ok:
        falhas.append(txt)


def abrir(pw, geo=DENTRO, erro=0, sem_geo=False, largura=390):
    b = pw.chromium.launch()
    c = b.new_context(viewport={"width": largura, "height": 800}, is_mobile=largura < 500)
    c.add_init_script(GPS_FALSO.replace("__INICIAL__", json.dumps(dict(geo, erro=erro))))
    if sem_geo:
        c.add_init_script("Object.defineProperty(navigator, 'geolocation', {value: undefined, configurable: true})")
    c.route("**/*", handler)
    pg = c.new_page()
    erros = []
    pg.on("pageerror", lambda e: erros.append(str(e)))
    pg.goto("http://localhost:8765/")
    pg.wait_for_selector("#v-auth:not([hidden])")
    pg.click('.tab[data-p="aluno"]')
    pg.fill("#email", ALUNO["email"])
    pg.fill("#senha", "senha12345")
    pg.click("#btn-auth")
    pg.wait_for_selector("#v-aluno:not([hidden])")
    pg.wait_for_selector("#aulas article")
    return b, c, pg, erros


def botao(pg):
    return pg.locator("#aulas button[data-id]")


with sync_playwright() as pw:
    # ---------- dentro do raio: o que o celular manda ----------
    b, c, pg, erros = abrir(pw)
    pg.wait_for_function("document.querySelector('#aulas button[data-id]') && !document.querySelector('#aulas button[data-id]').disabled", timeout=8000)
    conf(botao(pg).inner_text().strip() == "Registrar Presença", "dentro do raio: botão 'Registrar Presença' habilitado")
    conf("Você está na área da aula" in pg.inner_text("#aulas .zona"), "dentro do raio: aviso verde com a distância")
    posts.clear()
    t0 = time.time() * 1000
    botao(pg).click()
    pg.wait_for_selector("#msg-aluno.ok, #msg-aluno .ok, .msg.ok", timeout=10000)
    t1 = time.time() * 1000
    corpo = posts[0] if posts else {}
    conf(len(posts) == 1, "um clique envia 1 pedido (enviou %d)" % len(posts))
    conf(abs(corpo.get("latitude", 0) - LAT) < 1e-5 and abs(corpo.get("longitude", 0) - LON) < 1e-5, "corpo: latitude/longitude do GPS")
    conf(isinstance(corpo.get("precisao_m"), (int, float)) and 0 < corpo["precisao_m"] <= 150, "corpo: precisao_m numérica (%r)" % corpo.get("precisao_m"))
    conf(t0 - 5000 <= corpo.get("pos_ts", 0) <= t1 + 1000, "corpo: pos_ts em MILISSEGUNDOS e recente (%r)" % corpo.get("pos_ts"))
    conf(1 <= corpo.get("amostras", 0) <= 5 and corpo.get("variacao_m", -1) >= 0, "corpo: amostras (%r) e variação (%r)" % (corpo.get("amostras"), corpo.get("variacao_m")))
    conf(re.fullmatch(r"[A-Za-z0-9\-]{16,64}", corpo.get("device_id") or "") is not None, "corpo: device_id no formato aceito pelo servidor (%r)" % corpo.get("device_id"))
    conf(corpo.get("fp_hash") is None or re.fullmatch(r"[a-f0-9]{8,64}", corpo["fp_hash"]) is not None, "corpo: fp_hash hexadecimal minúsculo ou nulo")
    conf(isinstance(corpo.get("webdriver"), bool), "corpo: webdriver é booleano")
    conf(set(corpo) == {"latitude", "longitude", "precisao_m", "pos_ts", "amostras", "variacao_m", "webdriver", "device_id", "fp_hash"}, "corpo: exatamente os 9 campos do schema (%s)" % sorted(corpo))
    pg.wait_for_selector(".ok-badge", timeout=8000)
    conf("Presença registrada" in pg.inner_text("#aulas"), "depois de registrar: selo 'Presença registrada' no lugar do botão")
    conf(pg.locator("#comprov .comp").count() == 1, "o comprovante aparece logo abaixo")
    try:
        pg.wait_for_function("window.__gps.ouvindo === 0", timeout=4000)
        conf(True, "sem chamada pendente o GPS é desligado (economiza bateria)")
    except Exception:
        conf(False, "sem chamada pendente o GPS continua ligado (%s leituras abertas)" % pg.evaluate("window.__gps.ouvindo"))
    dev1 = corpo.get("device_id")
    # o aparelho é o mesmo depois de recarregar a página
    pg.reload()
    pg.wait_for_selector("#v-aluno:not([hidden])", timeout=8000)
    conf(pg.evaluate("localStorage.getItem('gc_dev')") == dev1, "device_id guardado no navegador (mesmo valor depois de recarregar)")
    conf(not erros, "nenhum erro de JavaScript na tela do aluno: %s" % erros)
    b.close()

    # ---------- duplo clique ----------
    estado.update(post="ok", registrou=False)
    b, c, pg, erros = abrir(pw)
    pg.wait_for_function("!document.querySelector('#aulas button[data-id]').disabled", timeout=8000)
    posts.clear()
    botao(pg).dblclick()
    pg.wait_for_timeout(5000)
    conf(len(posts) == 1, "duplo clique em 'Registrar Presença' envia 1 pedido (enviou %d)" % len(posts))
    b.close()

    # ---------- fora do raio: botão travado ----------
    estado.update(post="ok", registrou=False)
    b, c, pg, erros = abrir(pw, geo=FORA)
    pg.wait_for_function("document.querySelector('#aulas .zona').textContent.includes('Você está a')", timeout=8000)
    conf(botao(pg).is_disabled() and "Fora da área" in botao(pg).inner_text(), "fora do raio: botão desabilitado ('Fora da área da aula')")
    conf("limite de 50 m" in pg.inner_text("#aulas .zona"), "fora do raio: mostra a distância e o limite")
    conf(re.search(r"a (\d+) m do local", pg.inner_text("#aulas .zona")) and 105 <= int(re.search(r"a (\d+) m do local", pg.inner_text("#aulas .zona")).group(1)) <= 118, "fora do raio: distância calculada no navegador é ~111 m")
    posts.clear()
    botao(pg).click(force=True)
    pg.wait_for_timeout(600)
    conf(len(posts) == 0, "clicar no botão travado não envia nada")
    # o aluno anda até a sala: o botão libera sozinho
    pg.evaluate("g => Object.assign(window.__gps, g)", DENTRO)
    pg.wait_for_function("!document.querySelector('#aulas button[data-id]').disabled", timeout=10000)
    conf(True, "ao entrar no raio o botão libera sozinho")
    b.close()

    # ---------- GPS negado / sem suporte ----------
    b, c, pg, erros = abrir(pw, erro=1)
    pg.wait_for_timeout(1500)
    conf(botao(pg).is_disabled(), "sem permissão de localização: botão desabilitado")
    conf(pg.locator("#aulas .zona button[data-geo]").count() == 1 or "localização" in pg.inner_text("#aulas .zona").lower(), "sem permissão: explica o que fazer ('Tentar de novo' / liberar a localização)")
    b.close()
    b, c, pg, erros = abrir(pw, sem_geo=True)
    pg.wait_for_timeout(800)
    conf("não suporta geolocalização" in pg.inner_text("#aulas .zona") and botao(pg).is_disabled(), "navegador sem geolocalização: mensagem clara e botão desabilitado")
    conf(not erros, "navegador sem geolocalização: nenhum erro de JavaScript: %s" % erros)
    b.close()

    # ---------- erros do servidor aparecem na tela e o botão volta ----------
    for modo, trecho in (("400", "a 400 m da sala"), ("409", "outro aluno"), ("422", "impreciso"), ("500", "Erro 500")):
        estado.update(post=modo, registrou=False)
        b, c, pg, erros = abrir(pw)
        pg.wait_for_function("!document.querySelector('#aulas button[data-id]').disabled", timeout=8000)
        botao(pg).click()
        pg.wait_for_selector("#msg-aluno.err, .msg.err", timeout=10000)
        txt = pg.inner_text("#msg-aluno")
        pg.wait_for_function("!document.querySelector('#aulas button[data-id]').disabled", timeout=8000)
        conf(trecho in txt and botao(pg).inner_text().strip() == "Registrar Presença",
             "erro %s: mostra '%s...' e o botão volta a 'Registrar Presença' (%r)" % (modo, trecho, txt[:60]))
        b.close()

    # ---------- texto malicioso do evento ----------
    estado.update(post="ok", registrou=False, evento=dict(EVENTO, titulo=X1, professor=X1, disciplina=X1, local_descricao=X1))
    b, c, pg, erros = abrir(pw)
    pg.wait_for_timeout(1200)
    conf(pg.evaluate("window.__xss || 0") == 0 and pg.locator("#aulas img[src='x']").count() == 0, "título/professor/disciplina maliciosos do evento aparecem como texto")
    b.close()

    # ---------- celular pequeno ----------
    estado.update(post="ok", registrou=False, evento=EVENTO)
    b, c, pg, erros = abrir(pw, largura=320)
    pg.wait_for_function("!document.querySelector('#aulas button[data-id]').disabled", timeout=8000)
    conf(pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "320 px: sem rolagem horizontal na tela do aluno")
    caixa = botao(pg).bounding_box()
    conf(caixa is not None and caixa["height"] >= 44, "botão de registrar tem pelo menos 44 px de altura (alvo de toque): %s px" % (caixa or {}).get("height"))
    b.close()

print("\n%d falha(s)" % len(falhas))
raise SystemExit(1 if falhas else 0)
