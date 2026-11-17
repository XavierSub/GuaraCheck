"""Entrada e saída no navegador (CT-1637 a CT-1665).
Aluno: o cartão do evento com saída explica as duas etapas; depois da entrada mostra "Presença registrada às HH:MM" e o botão "Registrar saída"
(só libera dentro do raio, pede confirmação, envia para /saida sem o código do QR); mostra a permanência e avisa quando fica abaixo do mínimo;
depois de sair, o botão some. O comprovante traz a saída e a permanência; sem saída avisa. "Mostrar ao professor" também.
Professor: a coluna "Saída" só aparece no evento com saída, com a permanência e o aviso de "abaixo do mínimo" / "Sem saída".
Evento sem saída: nada disso aparece. A API é simulada (imita o FastAPI); o servidor real não roda aqui.
Uso:  pip install playwright && playwright install chromium && python tests/navegador/saida_tela.py
(GC_HTML=caminho.html testa outra cópia do index.html; sai com código 1 se algo falhar)."""
import json, os, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gps import gps_falso

WEB = str(Path(__file__).resolve().parents[2]) + "/"
html = open(os.environ.get("GC_HTML", WEB + "index.html"), encoding="utf-8").read()
CFG = {"dominio_aluno": "alunos.unicerrado.edu.br", "dominios_professor": ["unicerrado.edu.br"],
       "matricula_regex": r"^[A-Za-z0-9][A-Za-z0-9._/-]{3,29}$", "coordenacao_aberta": False}
PROF = {"id": 2, "nome": "Marcos Lima", "email": "marcos@unicerrado.edu.br", "perfil": "professor", "departamento": "Exatas", "registro_funcional": "DOC-1"}
ALUNO = {"id": 1, "nome": "Ana Souza", "email": "ana@alunos.unicerrado.edu.br", "perfil": "aluno", "curso": "Gestão da TI", "periodo": 3, "matricula": "GTI00001"}
LAT, LON = -18.4, -49.2
BASE = {"id": 11, "titulo": "Algoritmos", "professor": "Marcos Lima", "latitude": LAT, "longitude": LON, "raio_metros": 50, "ativa": True, "tipo": "aula",
        "local_tipo": "dentro", "local_descricao": "Sala 12", "curso_id": 1, "curso": "Gestão da TI", "periodo": 3, "disciplina": "ED",
        "aberta_em": "2026-11-11T14:00:00+00:00", "encerrada_em": None, "total": 3, "codigo_validacao": "K7Q2", "qr_rotativo": False,
        "exige_saida": True, "permanencia_min": 60, "carga_horas": None, "ja_registrou": False, "entrada_em": None, "ja_saiu": False}
estado = {}


def reiniciar(**extra):
    estado.clear()
    estado.update(evento=dict(BASE), posts=[], resposta_saida=None, comp=[], lista=[])
    estado["evento"].update(extra)


def pres(i, **kw):
    d = {"id": i, "nome": f"Aluno {i}", "matricula": f"GTI{i:05d}", "email": f"a{i}@a.b", "curso": "Gestão da TI", "periodo": 3, "distancia_m": 4.5,
         "registrado_em": "2026-11-11T14:05:00+00:00", "alertas": None, "manual": False, "justificativa": None, "saida_em": None,
         "permanencia_min": None, "situacao": "Presente (sem saída)"}
    d.update(kw)
    return d


def comp(i, **kw):
    d = {"id": i, "registrado_em": "2026-11-11T14:05:00+00:00", "titulo": f"Evento {i}", "tipo": "aula", "local_tipo": "dentro", "disciplina": "ED",
         "periodo": 3, "professor": "Marcos Lima", "manual": False, "saida_em": None, "exige_saida": True, "permanencia_min": None,
         "permanencia_exigida": 60, "situacao": "Presente (sem saída)"}
    d.update(kw)
    return d


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
    if p == "/api/aulas/ativas":
        return js([estado["evento"]])
    if p == "/api/aulas/11/presenca" and route.request.method == "POST":
        corpo = json.loads(route.request.post_data)
        estado["posts"].append(("presenca", corpo))
        estado["evento"].update(ja_registrou=True, entrada_em="2026-11-11T14:05:00+00:00")
        ev = estado["evento"]
        estado["comp"] = [comp(31, exige_saida=ev["exige_saida"], permanencia_exigida=ev["permanencia_min"], situacao="Presente (sem saída)" if ev["exige_saida"] else "Presente")]
        return js({"ok": True, "ja_registrado": False, "distancia_m": 3.2, "aula": "Algoritmos"})
    if p == "/api/aulas/11/saida" and route.request.method == "POST":
        corpo = json.loads(route.request.post_data)
        estado["posts"].append(("saida", corpo))
        r = estado["resposta_saida"]
        if isinstance(r, tuple):
            return js({"detail": r[1]}, r[0])
        r = r or {"ok": True, "ja_saiu": False, "saida_em": "2026-11-11T15:20:00+00:00", "permanencia_min": 75, "permanencia_exigida": 60, "cumpriu": True, "distancia_m": 2.0}
        estado["evento"].update(ja_saiu=True)
        estado["comp"] = [comp(31, saida_em=r["saida_em"], permanencia_min=r["permanencia_min"], situacao="Presente" if r["cumpriu"] else "Permanência insuficiente")]
        return js(r)
    if p == "/api/me":
        return js({"nome": ALUNO["nome"], "matricula": ALUNO["matricula"], "email": ALUNO["email"], "curso": ALUNO["curso"], "periodo": 3})
    if p == "/api/aluno/presencas":
        return js(estado["comp"])
    if p == "/api/aluno/notas":
        return js([])
    # ---- professor ----
    if p == "/api/professor/aulas":
        return js([dict(estado["evento"], ativa=True)] if q.get("pagina", "1") == "1" else [])
    if p == "/api/aulas/11/qr":
        return js({"url": "https://x/?aula=11", "svg": "<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10'></svg>", "rotativo": False, "renova_em_s": None})
    if p == "/api/aulas/11/presencas":
        apos = int(q.get("apos_id", 0))
        return js({"ativa": True, "total": len(estado["lista"]), "limite": 2000, "presencas": [x for x in estado["lista"] if x["id"] > apos]})
    if p == "/api/aulas/11/tentativas":
        return js({"itens": []})
    if p == "/api/aulas/11/ausentes":
        return js({"disponivel": True, "total": 0, "limite": 500, "itens": []})
    return js({"detail": "mock: " + p}, 404)


falhas = []


def conf(ok, txt):
    print(("OK   " if ok else "FALHA"), txt)
    if not ok:
        falhas.append(txt)


def entrar(pw, quem, vista, largura=390, geo=True, aceitar=True):
    b = pw.chromium.launch()
    c = b.new_context(viewport={"width": largura, "height": 900})
    if geo:
        c.add_init_script(gps_falso(LAT, LON))
    c.route("**/*", handler)
    pg = c.new_page()
    erros, dialogos = [], []
    pg.on("pageerror", lambda e: erros.append(str(e)))
    pg.on("dialog", lambda d: (dialogos.append(d.message), d.accept() if aceitar else d.dismiss()))
    pg.goto("http://localhost:8765/")
    pg.wait_for_selector("#v-auth:not([hidden])")
    pg.click(f'.tab[data-p="{quem}"]')
    pg.fill("#email", (PROF if quem == "professor" else ALUNO)["email"])
    pg.fill("#senha", "senha12345")
    pg.click("#btn-auth")
    pg.wait_for_selector(vista + ":not([hidden])")
    return b, pg, erros, dialogos


def espera(pg, js, t=8000):
    pg.wait_for_function(js, timeout=t)


with sync_playwright() as pw:
    # =========================== aluno: entrada e saída ===========================
    reiniciar()
    b, pg, erros, dialogos = entrar(pw, "aluno", "#v-aluno")
    pg.wait_for_selector("#aulas article")
    card = pg.inner_text("#aulas article")
    conf("entrada e saída" in card and "permanência mínima de 1 h 00 min" in card, "cartão explica as duas etapas e o mínimo de permanência")
    conf(pg.locator('#aulas button[data-id]').count() == 1 and pg.locator('#aulas button[data-modo="saida"]').count() == 0, "antes da entrada: só o botão de presença (sem botão de saída)")
    espera(pg, "document.querySelector('#aulas button[data-id]') && !document.querySelector('#aulas button[data-id]').disabled")
    pg.click('#aulas button[data-id]')
    espera(pg, "document.querySelector('#msg-aluno').textContent.includes('Presença registrada')")
    espera(pg, "document.querySelector('#aulas button[data-modo=\"saida\"]')")
    conf(estado["posts"][0][0] == "presenca", "a entrada vai para /presenca")
    conf("Presença registrada às" in pg.inner_text("#aulas article") and pg.locator('#aulas button[data-modo="saida"]').count() == 1,
         "depois da entrada: 'Presença registrada às HH:MM' e o botão 'Registrar saída'")
    espera(pg, "!document.querySelector('#aulas button[data-modo=\"saida\"]').disabled")
    conf("Registrar saída" in pg.inner_text('#aulas button[data-modo="saida"]'), "o botão da saída é liberado dentro do raio")
    comp_txt = pg.inner_text("#comprov .comp")
    conf("Saída ainda não registrada" in comp_txt, "comprovante sem saída avisa que a saída ainda não foi registrada")

    # fora do raio: trava
    pg.evaluate(f"window.__gps.lat = {LAT + 0.001}")
    espera(pg, "document.querySelector('#aulas button[data-modo=\"saida\"]').disabled", 6000)
    conf("Fora da área" in pg.inner_text('#aulas button[data-modo="saida"]') and "da saída" in pg.inner_text("#aulas .zona >> nth=-1"),
         "fora do raio: o botão de saída trava e explica")
    pg.evaluate(f"window.__gps.lat = {LAT}")
    espera(pg, "!document.querySelector('#aulas button[data-modo=\"saida\"]').disabled", 6000)

    # saída cumprida
    n = len(dialogos)
    pg.click('#aulas button[data-modo="saida"]')
    espera(pg, "document.querySelector('#msg-aluno').textContent.includes('Saída registrada')")
    conf(len(dialogos) == n + 1 and "Registrar a sua saída agora" in dialogos[-1], "pede confirmação antes de registrar a saída")
    tipo, corpo = estado["posts"][-1]
    conf(tipo == "saida" and "qr" not in corpo and corpo["device_id"] and abs(corpo["latitude"] - LAT) < 0.001, f"a saída vai para /saida com GPS e aparelho, sem o código do QR")
    conf("Permanência de 1 h 15 min" in pg.inner_text("#msg-aluno") and "ok" in (pg.get_attribute("#msg-aluno", "class") or ""), "mensagem: permanência de 1 h 15 min, em verde")
    espera(pg, "!document.querySelector('#aulas button[data-modo=\"saida\"]')")
    conf("Saída registrada" in pg.inner_text("#aulas article") and pg.locator('#aulas button[data-id]').count() == 0, "depois de sair: aparece 'Saída registrada' e os botões somem")
    espera(pg, "document.querySelector('#comprov .comp').textContent.includes('permanência de')")
    ct = pg.inner_text("#comprov .comp")
    conf("permanência de 1 h 15 min" in ct and "mínimo: 1 h 00 min" in ct and "Abaixo do mínimo" not in ct, "comprovante: saída, permanência e mínimo (sem aviso)")
    pg.click('#comprov .comp button[data-m]')
    pg.wait_for_selector("#mostrar:not([hidden])")
    conf("Saída às" in pg.inner_text("#m-quando") and "1 h 15 min" in pg.inner_text("#m-quando"), "'Mostrar ao professor' traz a saída e a permanência")
    conf(not erros, "aluno: nenhum erro de JavaScript: %s" % erros)
    b.close()

    # =========================== aluno: permanência insuficiente, cancelar, erro ===========================
    reiniciar(ja_registrou=True, entrada_em="2026-11-11T14:05:00+00:00")
    estado["comp"] = [comp(31)]
    estado["resposta_saida"] = {"ok": True, "ja_saiu": False, "saida_em": "2026-11-11T14:35:00+00:00", "permanencia_min": 30, "permanencia_exigida": 60, "cumpriu": False, "distancia_m": 1.0}
    b, pg, erros, dialogos = entrar(pw, "aluno", "#v-aluno", aceitar=False)
    pg.wait_for_selector('#aulas button[data-modo="saida"]')
    espera(pg, "!document.querySelector('#aulas button[data-modo=\"saida\"]').disabled")
    pg.click('#aulas button[data-modo="saida"]')
    pg.wait_for_timeout(600)
    conf(not estado["posts"] and not pg.is_disabled('#aulas button[data-modo="saida"]'), "cancelando a confirmação: nada é enviado e o botão continua ativo")
    b.close()
    b, pg, erros, dialogos = entrar(pw, "aluno", "#v-aluno", aceitar=True)
    pg.wait_for_selector('#aulas button[data-modo="saida"]')
    espera(pg, "!document.querySelector('#aulas button[data-modo=\"saida\"]').disabled")
    pg.click('#aulas button[data-modo="saida"]')
    espera(pg, "document.querySelector('#msg-aluno').textContent.includes('Atenção')")
    msg = pg.inner_text("#msg-aluno")
    conf("Permanência de 30 min" in msg and "mínimo deste evento é 1 h 00 min" in msg and "err" in (pg.get_attribute("#msg-aluno", "class") or ""),
         "permanência abaixo do mínimo: avisa com o mínimo, em vermelho")
    espera(pg, "document.querySelector('#comprov .comp').textContent.includes('Abaixo do mínimo')")
    conf("Abaixo do mínimo do evento" in pg.inner_text("#comprov .comp"), "o comprovante marca 'Abaixo do mínimo do evento'")
    b.close()

    reiniciar(ja_registrou=True, entrada_em="2026-11-11T14:05:00+00:00")
    estado["comp"] = [comp(31)]
    estado["resposta_saida"] = (400, "Você está a 80 m da sala; o limite deste evento é 50 m. Aproxime-se para registrar a saída.")
    b, pg, erros, dialogos = entrar(pw, "aluno", "#v-aluno")
    pg.wait_for_selector('#aulas button[data-modo="saida"]')
    espera(pg, "!document.querySelector('#aulas button[data-modo=\"saida\"]').disabled")
    pg.click('#aulas button[data-modo="saida"]')
    espera(pg, "document.querySelector('#msg-aluno').textContent.includes('80 m da sala')")
    espera(pg, "!document.querySelector('#aulas button[data-modo=\"saida\"]').disabled")
    conf("err" in (pg.get_attribute("#msg-aluno", "class") or "") and "Registrar saída" in pg.inner_text('#aulas button[data-modo="saida"]'),
         "erro do servidor: mostra a mensagem e o botão volta a funcionar")
    estado["resposta_saida"] = {"ok": True, "ja_saiu": True, "saida_em": "2026-11-11T14:50:00+00:00", "permanencia_min": 45, "permanencia_exigida": 60, "cumpriu": False, "distancia_m": 1.0}
    pg.click('#aulas button[data-modo="saida"]')
    espera(pg, "document.querySelector('#msg-aluno').textContent.includes('já estava registrada')")
    conf(True, "saída repetida (aba antiga): 'Sua saída já estava registrada.'")
    conf(not erros, "aluno (erros): nenhum erro de JavaScript: %s" % erros)
    b.close()

    # =========================== aluno: evento SEM saída não muda ===========================
    reiniciar(exige_saida=False, permanencia_min=None)
    b, pg, erros, dialogos = entrar(pw, "aluno", "#v-aluno")
    pg.wait_for_selector("#aulas article")
    conf("entrada e saída" not in pg.inner_text("#aulas article"), "evento sem saída: o cartão não fala de saída")
    espera(pg, "document.querySelector('#aulas button[data-id]') && !document.querySelector('#aulas button[data-id]').disabled")
    pg.click('#aulas button[data-id]')
    espera(pg, "document.querySelector('#msg-aluno').textContent.includes('Presença registrada')")
    espera(pg, "document.querySelector('#comprov .comp')")
    conf(pg.locator('#aulas button[data-modo="saida"]').count() == 0 and "Presença registrada" in pg.inner_text("#aulas article"), "evento sem saída: depois da entrada não há botão de saída")
    conf("Saída" not in pg.inner_text("#comprov .comp"), "evento sem saída: o comprovante não fala de saída")
    conf(not erros, "evento sem saída: nenhum erro de JavaScript: %s" % erros)
    b.close()

    # =========================== professor ===========================
    reiniciar(ativa=True)
    estado["lista"] = [pres(1, saida_em="2026-11-11T15:20:00+00:00", permanencia_min=75, situacao="Presente"),
                       pres(2, saida_em="2026-11-11T14:35:00+00:00", permanencia_min=30, situacao="Permanência insuficiente"),
                       pres(3)]
    b, pg, erros, dialogos = entrar(pw, "professor", "#v-prof", largura=1366, geo=False)
    pg.wait_for_selector("#tbody tr")
    pg.wait_for_function("document.querySelectorAll('#tbody tr').length === 3")
    conf("entrada e saída" in pg.inner_text("#p-info") and "mínimo 1 h 00 min" in pg.inner_text("#p-info"), "painel diz que o evento tem entrada e saída e o mínimo")
    conf(pg.is_visible("#pt-presentes th.col-saida"), "coluna 'Saída' aparece")
    linhas = {i: pg.inner_text(f"#tbody tr:nth-child({4 - i})") for i in (1, 2, 3)}   # a mais nova primeiro
    conf("1 h 15 min" in linhas[1] and "abaixo do mínimo" not in linhas[1], "aluno que cumpriu: saída e permanência 1 h 15 min, sem aviso")
    conf("30 min" in linhas[2] and "abaixo do mínimo" in linhas[2], "permanência de 30 min: 'abaixo do mínimo'")
    conf("Sem saída" in linhas[3], "quem não saiu: 'Sem saída'")
    conf(not erros, "professor: nenhum erro de JavaScript: %s" % erros)
    b.close()

    reiniciar(exige_saida=False, permanencia_min=None)
    estado["lista"] = [pres(1, situacao="Presente")]
    b, pg, erros, dialogos = entrar(pw, "professor", "#v-prof", largura=1366, geo=False)
    pg.wait_for_selector("#tbody tr")
    conf(not pg.is_visible("#pt-presentes th.col-saida") and "Sem saída" not in pg.inner_text("#tbody") or not pg.is_visible("#tbody td.col-saida"),
         "evento sem saída: a coluna 'Saída' fica escondida")
    conf("entrada e saída" not in pg.inner_text("#p-info"), "evento sem saída: o painel não fala de saída")
    conf(not erros, "professor (sem saída): nenhum erro de JavaScript: %s" % erros)
    b.close()

print()
print("FALHA=%d" % len(falhas))
sys.exit(1 if falhas else 0)
