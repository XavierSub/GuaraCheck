"""Presença lançada pelo professor, no navegador (CT-1532 a CT-1560): formulário "Lançar presença manualmente", botão
"Lançar presença" na lista de ausentes, marca e "Desfazer" na lista de presentes, e o comprovante do aluno ("lançada pelo professor").
A API é simulada (imita o FastAPI); o servidor real não roda aqui.
Uso:  pip install playwright && playwright install chromium && python tests/navegador/presenca_manual_tela.py
(GC_HTML=caminho.html testa outra cópia do index.html; sai com código 1 se algo falhar)."""
import json, os, re, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

WEB = str(Path(__file__).resolve().parents[2]) + "/"
html = open(os.environ.get("GC_HTML", WEB + "index.html"), encoding="utf-8").read()
CFG = {"dominio_aluno": "alunos.unicerrado.edu.br", "dominios_professor": ["unicerrado.edu.br"],
       "matricula_regex": r"^[A-Za-z0-9][A-Za-z0-9._/-]{3,29}$", "coordenacao_aberta": False}
X1 = '<img src=x onerror="window.__xss=(window.__xss||0)+1">'
PROF = {"id": 2, "nome": "Marcos Lima", "email": "marcos@unicerrado.edu.br", "perfil": "professor", "departamento": "Exatas", "registro_funcional": "DOC-1"}
ALUNO = {"id": 1, "nome": "Ana Souza", "email": "ana@alunos.unicerrado.edu.br", "perfil": "aluno", "curso": "Gestão da TI", "periodo": 3, "matricula": "GTI00001"}
EVENTO = {"id": 11, "titulo": "Algoritmos", "latitude": -18.4, "longitude": -49.2, "raio_metros": 50, "ativa": True, "tipo": "aula",
          "local_tipo": "dentro", "local_descricao": "Sala 12", "curso_id": 1, "curso": "Gestão da TI", "periodo": 3, "disciplina": "ED",
          "aberta_em": "2026-11-11T14:00:00+00:00", "encerrada_em": None, "total": 2, "codigo_validacao": "K7Q2",
          "qr_rotativo": False, "exige_saida": False, "permanencia_min": None, "carga_horas": None}
estado = {}


def pres(i, **kw):
    d = {"id": i, "nome": f"Aluno {i}", "matricula": f"GTI{i:05d}", "email": f"a{i}@alunos.unicerrado.edu.br", "curso": "Gestão da TI",
         "periodo": 3, "distancia_m": 4.5, "registrado_em": "2026-11-11T14:05:00+00:00", "alertas": None, "manual": False,
         "justificativa": None, "saida_em": None, "permanencia_min": None}
    d.update(kw)
    return d


def reiniciar(**extra):
    estado.clear()
    estado.update(posts=[], encerrada=False, evento=dict(EVENTO), erro_post=None,
                  lista=[pres(1), pres(2, manual=True, justificativa=X1, alertas="lançada pelo professor", distancia_m=0.0)],
                  ausentes=[{"id": 5, "nome": "Carla Dias", "matricula": "GTI00005", "email": "c@alunos.unicerrado.edu.br", "periodo": 3},
                            {"id": 6, "nome": "Davi Melo", "matricula": None, "email": "d@alunos.unicerrado.edu.br", "periodo": 3}],
                  comp=[])
    estado.update(extra)


def comp(i, manual):
    return {"id": i, "registrado_em": "2026-11-11T14:05:00+00:00", "titulo": f"Evento {i}", "tipo": "aula", "local_tipo": "dentro",
            "disciplina": "ED", "periodo": 3, "professor": "Marcos Lima", "manual": manual, "saida_em": None, "exige_saida": False}


def handler(route):
    u = route.request.url
    if "localhost:8765" not in u:
        return route.abort()
    p = u.split("localhost:8765", 1)[1].split("?")[0]
    q = dict(kv.split("=", 1) for kv in (u.split("?", 1)[1].split("&") if "?" in u else []))
    js = lambda o, c=200: route.fulfill(status=c, content_type="application/json", body=json.dumps(o))
    met = route.request.method
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
    if p == "/api/professor/aulas":
        return js([dict(estado["evento"], ativa=not estado["encerrada"])] if q.get("pagina", "1") == "1" else [])
    if p == "/api/aulas/11/qr":
        return js({"url": "https://x/?aula=11", "svg": "<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10'></svg>", "rotativo": False, "renova_em_s": None})
    if p == "/api/aulas/11/presencas":
        apos = int(q.get("apos_id", 0))
        return js({"ativa": not estado["encerrada"], "total": len(estado["lista"]), "limite": 2000,
                   "presencas": [x for x in estado["lista"] if x["id"] > apos]})
    if p == "/api/aulas/11/tentativas":
        return js({"itens": []})
    if p == "/api/aulas/11/ausentes":
        return js({"disponivel": True, "total": len(estado["ausentes"]), "limite": 500, "itens": estado["ausentes"]})
    if p == "/api/aulas/11/presenca-manual" and met == "POST":
        corpo = json.loads(route.request.post_data)
        estado["posts"].append(("POST", p, corpo))
        if estado["erro_post"]:
            return js({"detail": estado["erro_post"]}, 409)
        alvo = next((a for a in estado["ausentes"] if a["id"] == corpo.get("aluno_id") or a["matricula"] == corpo.get("matricula")), None)
        if not alvo:
            return js({"detail": "Aluno não encontrado. Confira a matrícula."}, 404)
        estado["ausentes"].remove(alvo)
        novo = max(x["id"] for x in estado["lista"]) + 1
        estado["lista"].append(pres(novo, nome=alvo["nome"], matricula=alvo["matricula"], manual=True, justificativa=corpo["justificativa"], distancia_m=0.0))
        return js({"ok": True, "aluno": alvo["nome"], "matricula": alvo["matricula"]})
    m = re.fullmatch(r"/api/aulas/11/presenca-manual/(\d+)", p)
    if m and met == "DELETE":
        estado["posts"].append(("DELETE", p, None))
        estado["lista"] = [x for x in estado["lista"] if x["id"] != int(m.group(1))]
        return js({"ok": True})
    if p == "/api/aulas/ativas":
        return js([])
    if p == "/api/me":
        return js({"nome": ALUNO["nome"], "matricula": ALUNO["matricula"], "email": ALUNO["email"], "curso": ALUNO["curso"], "periodo": 3})
    if p == "/api/aluno/presencas":
        return js(estado["comp"])
    if p == "/api/aluno/notas":
        return js([])
    return js({"detail": "mock: " + p}, 404)


falhas = []


def conf(ok, txt):
    print(("OK   " if ok else "FALHA"), txt)
    if not ok:
        falhas.append(txt)


def entrar(pw, quem, vista, largura=1366):
    b = pw.chromium.launch()
    c = b.new_context(viewport={"width": largura, "height": 900})
    c.route("**/*", handler)
    pg = c.new_page()
    erros, dialogos = [], []
    resposta = {"aceitar": True}
    pg.on("pageerror", lambda e: erros.append(str(e)))
    pg.on("dialog", lambda d: (dialogos.append(d.message), d.accept() if resposta["aceitar"] else d.dismiss()))
    pg.goto("http://localhost:8765/")
    pg.wait_for_selector("#v-auth:not([hidden])")
    pg.click(f'.tab[data-p="{quem}"]')
    pg.fill("#email", (PROF if quem == "professor" else ALUNO)["email"])
    pg.fill("#senha", "senha12345")
    pg.click("#btn-auth")
    pg.wait_for_selector(vista + ":not([hidden])")
    return b, pg, erros, dialogos, resposta


def espera_msg(pg, seletor, trecho, t=6000):
    pg.wait_for_function("([s, t]) => document.querySelector(s).textContent.includes(t)", arg=[seletor, trecho], timeout=t)


with sync_playwright() as pw:
    # =========================== professor ===========================
    reiniciar()
    b, pg, erros, dialogos, resposta = entrar(pw, "professor", "#v-prof")
    pg.wait_for_selector("#tbody tr")
    pg.wait_for_timeout(600)
    linhas = pg.locator("#tbody tr")
    conf(linhas.count() == 2, "presentes: 2 linhas")
    topo = pg.inner_text("#tbody tr:nth-child(1)")   # a mais nova primeiro: a lançada (id 2)
    conf("Lançada por você" in topo and X1 in topo, "linha lançada: marca 'Lançada por você' e o motivo (texto malicioso aparece como texto)")
    conf(pg.locator('#tbody button[data-des]').count() == 1 and pg.locator('#tbody tr:nth-child(2) button[data-des]').count() == 0,
         "só a linha lançada pelo professor tem 'Desfazer'")
    conf("Aluno" in pg.inner_text("#tbody tr:nth-child(2)") and "4.5 m" in pg.inner_text("#tbody tr:nth-child(2)"), "linha do aluno: origem 'Aluno' e a distância")
    conf("0 m" not in topo and "lançada pelo professor" not in topo.lower().replace("lançada por você", ""), "linha lançada: não mostra distância 0 m nem o alerta interno")
    conf(pg.evaluate("window.__xss || 0") == 0 and pg.evaluate("document.querySelectorAll('#tbody img[src=\"x\"]').length") == 0, "o motivo malicioso não executou")
    conf(not pg.locator("#manual-box").evaluate("e => e.open"), "o formulário começa fechado")
    conf(pg.is_hidden("#man-saida-l"), "evento sem saída: a opção 'lançar também a saída' não aparece")

    # ---- pelo botão da lista de ausentes ----
    pg.click('#p-tabs button[data-pt="ausentes"]')
    pg.wait_for_selector("#tb-aus button[data-man]")
    conf(pg.locator("#tb-aus button[data-man]").count() == 2, "ausentes: um botão 'Lançar presença' por aluno")
    pg.click('#tb-aus button[data-man="5"]')
    conf(pg.locator("#manual-box").evaluate("e => e.open") and pg.input_value("#man-mat") == "GTI00005", "botão do ausente abre o formulário com a matrícula")
    conf(pg.evaluate("document.activeElement.id") == "man-just", "o foco vai para o campo do motivo")
    pg.click("#man-lancar")
    conf("pelo menos 5 letras" in pg.inner_text("#man-msg") and not estado["posts"], "motivo vazio: orienta e NÃO chama o servidor")
    pg.fill("#man-just", "abc")
    pg.click("#man-lancar")
    conf("pelo menos 5 letras" in pg.inner_text("#man-msg") and not estado["posts"], "motivo curto (3 letras): orienta e NÃO chama o servidor")
    pg.fill("#man-just", "Celular sem bateria")
    pg.press("#man-just", "Enter")
    espera_msg(pg, "#man-msg", "Carla Dias")
    conf(estado["posts"][-1][2] == {"justificativa": "Celular sem bateria", "com_saida": False, "aluno_id": 5},
         f"enviou aluno_id (não a matrícula) e o motivo: {estado['posts'][-1][2]}")
    conf("lançada" in pg.inner_text("#man-msg") and "ok" in (pg.get_attribute("#man-msg", "class") or ""), "mensagem de sucesso com o nome do aluno")
    conf(pg.input_value("#man-mat") == "" and pg.input_value("#man-just") == "", "campos limpos depois de lançar")
    pg.wait_for_function("document.querySelectorAll('#tbody tr').length === 3")
    conf(pg.inner_text("#p-total") == "3" and "Carla Dias" in pg.inner_text("#tbody"), "a nova presença aparece na lista e o total sobe para 3")
    pg.wait_for_function("document.querySelectorAll('#tb-aus button[data-man]').length === 1")
    conf(pg.inner_text("#n-aus") == "1", "Carla sai da lista de ausentes (contador 1)")

    # ---- digitando a matrícula ----
    pg.click('#tb-aus button[data-man="6"]')
    conf(pg.input_value("#man-mat") == "Davi Melo", "ausente sem matrícula: o campo mostra o nome")
    pg.fill("#man-mat", "gti00007")                # digitou outra coisa: vale a matrícula digitada, não o aluno do botão
    pg.fill("#man-just", "GPS fora do ar na sala")
    estado["erro_post"] = None
    pg.click("#man-lancar")
    espera_msg(pg, "#man-msg", "não encontrado")
    conf(estado["posts"][-1][2] == {"justificativa": "GPS fora do ar na sala", "com_saida": False, "matricula": "gti00007"},
         f"digitou outra matrícula: envia a matrícula, sem o aluno_id antigo: {estado['posts'][-1][2]}")
    conf("err" in (pg.get_attribute("#man-msg", "class") or "") and pg.input_value("#man-just") == "GPS fora do ar na sala" and pg.is_enabled("#man-lancar"),
         "erro do servidor: mostrado em vermelho, o texto digitado continua e o botão volta a funcionar")
    estado["erro_post"] = "Fulano já tem presença registrada nesta chamada."
    pg.click("#man-lancar")
    espera_msg(pg, "#man-msg", "já tem presença")
    conf(True, "409 do servidor: a mensagem aparece")
    estado["erro_post"] = None

    # ---- desfazer ----
    pg.click('#p-tabs button[data-pt="presentes"]')
    n_antes = len(estado["posts"])
    resposta["aceitar"] = False
    pg.click('#tbody button[data-des]:visible >> nth=0')
    conf(len(estado["posts"]) == n_antes and "Desfazer a presença lançada" in dialogos[-1], "desfazer: pede confirmação e, cancelando, não chama o servidor")
    resposta["aceitar"] = True
    ids_lancadas = pg.eval_on_selector_all("#tbody button[data-des]", "bs => bs.map(b => b.dataset.des)")
    pg.click('#tbody button[data-des]:visible >> nth=0')
    espera_msg(pg, "#msg-prof", "Presença desfeita")
    conf(estado["posts"][-1][0] == "DELETE" and estado["posts"][-1][1] == f"/api/aulas/11/presenca-manual/{ids_lancadas[0]}",
         f"desfazer: DELETE na presença certa ({ids_lancadas[0]})")
    pg.wait_for_function(f"document.querySelectorAll('#tbody button[data-des]').length === {len(ids_lancadas) - 1}")
    conf(pg.inner_text("#p-total") == "2", "depois de desfazer o total volta a descer (2)")
    # ---- chamada encerrada ----
    estado["encerrada"] = True
    pg.wait_for_function("document.querySelector('#p-estado').textContent.includes('encerrada')", timeout=8000)
    pg.click('#p-tabs button[data-pt="ausentes"]')
    pg.wait_for_selector("#tb-aus button[data-man]")
    pg.click('#tb-aus button[data-man]:visible >> nth=0')
    pg.fill("#man-just", "Correção depois da aula")
    pg.click("#man-lancar")
    espera_msg(pg, "#man-msg", "lançada")
    conf(estado["posts"][-1][0] == "POST" and pg.is_hidden("#btn-fim"), "chamada encerrada: ainda dá para lançar (correção posterior)")
    conf(not erros, "painel do professor: nenhum erro de JavaScript: %s" % erros)
    b.close()

    # ---- evento com saída: aparece a opção ----
    reiniciar(evento=dict(EVENTO, exige_saida=True, permanencia_min=60))
    b, pg, erros, dialogos, resposta = entrar(pw, "professor", "#v-prof")
    pg.wait_for_selector("#tbody tr")
    pg.click('#p-tabs button[data-pt="ausentes"]')
    pg.wait_for_selector("#tb-aus button[data-man]")
    pg.click('#tb-aus button[data-man="5"]')
    conf(not pg.is_hidden("#man-saida-l"), "evento com saída: aparece 'Lançar também a saída do aluno'")
    pg.check("#man-saida")
    pg.fill("#man-just", "Saiu cedo, autorizado")
    pg.click("#man-lancar")
    espera_msg(pg, "#man-msg", "Carla Dias")
    conf(estado["posts"][-1][2].get("com_saida") is True, f"a opção marcada vai no pedido: {estado['posts'][-1][2]}")
    conf(not erros, "evento com saída: nenhum erro de JavaScript: %s" % erros)
    b.close()

    # =========================== aluno ===========================
    reiniciar(comp=[comp(31, True), comp(30, False)])
    b, pg, erros, dialogos, resposta = entrar(pw, "aluno", "#v-aluno", largura=390)
    pg.wait_for_selector("#comprov .comp")
    t31, t30 = pg.inner_text("#comprov .comp:nth-child(1)"), pg.inner_text("#comprov .comp:nth-child(2)")
    conf("Presença lançada pelo professor" in t31 and "foi lançada pelo(a) prof. Marcos Lima" in t31 and "registrou presença" not in t31,
         "comprovante lançado: título e frase dizem que foi o professor (não que o aluno registrou)")
    conf("Presença registrada" in t30 and "registrou presença em" in t30 and "lançada" not in t30, "comprovante normal: texto de sempre")
    pg.click('#comprov .comp:nth-child(1) button[data-m]')
    pg.wait_for_selector("#mostrar:not([hidden])")
    conf("foi lançada pelo(a) prof. Marcos Lima" in pg.inner_text("#m-frase") and "Eu," not in pg.inner_text("#m-frase")
         and pg.inner_text("#m-quando").startswith("Lançada em"), "'Mostrar ao professor': frase e data dizem 'lançada', sem o 'Eu, ... registrei'")
    pg.click("#m-fechar") if pg.locator("#m-fechar").count() else pg.keyboard.press("Escape")
    pg.click('#comprov .comp:nth-child(2) button[data-m]')
    pg.wait_for_selector("#mostrar:not([hidden])")
    conf("registrei presença" in pg.inner_text("#m-frase") and pg.inner_text("#m-quando").startswith("Registrado em"), "comprovante normal em tela cheia: sem mudança")
    conf(not erros, "aluno: nenhum erro de JavaScript: %s" % erros)
    b.close()

print()
print("FALHA=%d" % len(falhas))
sys.exit(1 if falhas else 0)
