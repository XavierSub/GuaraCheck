"""Aviso por e-mail no navegador (CT-1971 a CT-1990).
Professor: a caixa "Avisar os alunos por e-mail" vem desmarcada e vai no pedido de abrir a chamada (avisar_email true/false); o painel
mostra quantos alunos recebem, ou o MOTIVO quando o aviso não saiu (a chamada abriu do mesmo jeito); o motivo malicioso aparece como
texto; reabrir a chamada pelo histórico não mostra aviso nenhum.
Aluno: a caixa "Avisos por e-mail" começa desabilitada, reflete /api/me, liga/desliga por PUT /api/me/avisos e volta ao estado anterior
quando o servidor recusa. A API é simulada (imita o FastAPI); o servidor real não roda aqui.
Uso:  pip install playwright && playwright install chromium && python tests/navegador/aviso_email_tela.py
(GC_HTML=caminho.html testa outra cópia do index.html; sai com código 1 se algo falhar)."""
import json, os, sys
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
          "aberta_em": "2026-11-11T14:00:00+00:00", "encerrada_em": None, "total": 0, "codigo_validacao": "K7Q2",
          "qr_rotativo": False, "exige_saida": False, "permanencia_min": None, "carga_horas": None}
estado = {}


def reiniciar(**extra):
    estado.clear()
    estado.update(posts=[], aviso=None, avisos=[], me_avisos=True, erro_avisos=None, hist=[])
    estado.update(extra)


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
    if p == "/api/aulas" and met == "POST":
        corpo = json.loads(route.request.post_data)
        estado["posts"].append(corpo)
        r = dict(EVENTO, titulo=corpo["titulo"])
        if estado["aviso"] is not None and corpo.get("avisar_email"):
            r["aviso"] = estado["aviso"]
        return js(r)
    if p == "/api/professor/aulas":
        return js(estado["hist"] if q.get("pagina", "1") == "1" else [])
    if p == "/api/aulas/11/qr":
        return js({"url": "https://x/?aula=11", "svg": "<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10'></svg>", "rotativo": False, "renova_em_s": None})
    if p == "/api/aulas/11/presencas":
        return js({"ativa": True, "total": 0, "limite": 2000, "presencas": []})
    if p in ("/api/aulas/11/tentativas",):
        return js({"itens": []})
    if p == "/api/aulas/11/ausentes":
        return js({"disponivel": True, "total": 0, "limite": 500, "itens": []})
    if p == "/api/aulas/ativas":
        return js([])
    if p == "/api/me":
        return js({"nome": ALUNO["nome"], "matricula": ALUNO["matricula"], "email": ALUNO["email"], "curso": ALUNO["curso"], "periodo": 3,
                   "avisos_email": estado["me_avisos"]})
    if p == "/api/me/avisos" and met == "PUT":
        corpo = json.loads(route.request.post_data)
        estado["avisos"].append(corpo)
        if estado["erro_avisos"]:
            return js({"detail": estado["erro_avisos"]}, 500)
        estado["me_avisos"] = corpo["receber"]
        return js({"avisos_email": corpo["receber"]})
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


def entrar(pw, quem, vista, largura=1366):
    b = pw.chromium.launch()
    c = b.new_context(viewport={"width": largura, "height": 900})
    c.route("**/*", handler)
    pg = c.new_page()
    erros = []
    pg.on("pageerror", lambda e: erros.append(str(e)))
    pg.on("dialog", lambda d: d.dismiss())
    pg.goto("http://localhost:8765/")
    pg.wait_for_selector("#v-auth:not([hidden])")
    pg.click(f'.tab[data-p="{quem}"]')
    pg.fill("#email", (PROF if quem == "professor" else ALUNO)["email"])
    pg.fill("#senha", "senha12345")
    pg.click("#btn-auth")
    pg.wait_for_selector(vista + ":not([hidden])")
    return b, pg, erros


def abrir_chamada(pg, titulo="Algoritmos"):
    pg.fill("#titulo", titulo)
    pg.fill("#lat", "-18.4157")
    pg.fill("#lon", "-49.2183")
    pg.click('#form-aula button[type="submit"]')
    pg.wait_for_selector("#painel:not([hidden])")
    pg.wait_for_function("document.querySelector('#p-titulo').textContent === arguments[0]".replace("arguments[0]", json.dumps(titulo)))


with sync_playwright() as pw:
    # =========================== professor ===========================
    reiniciar(aviso={"enviado": True, "destinatarios": 143, "motivo": None})
    b, pg, erros = entrar(pw, "professor", "#v-prof")
    conf(pg.is_visible("#avisar-email") and not pg.is_checked("#avisar-email"), "a caixa 'Avisar os alunos por e-mail' existe e vem DESMARCADA")
    txt_label = pg.inner_text('label.ck:has(#avisar-email)')
    conf("curso e do período" in txt_label and "desligou" in txt_label, "o texto explica quem recebe (curso/período) e que quem desligou não recebe")
    abrir_chamada(pg, "Sem aviso")
    conf(estado["posts"][-1]["avisar_email"] is False, "sem marcar: o pedido leva avisar_email=false")
    conf(not pg.is_visible("#p-aviso"), "sem pedir aviso, o painel não mostra linha de aviso")

    pg.check("#avisar-email")
    abrir_chamada(pg, "Com aviso")
    conf(estado["posts"][-1]["avisar_email"] is True, "marcada: o pedido leva avisar_email=true")
    t = pg.inner_text("#p-aviso")
    conf(pg.is_visible("#p-aviso") and "143 alunos" in t and "encerrar a chamada" in t, f"painel diz quantos alunos recebem e que para ao encerrar: {t!r}")
    conf("warn" not in (pg.get_attribute("#p-aviso", "class") or ""), "aviso enviado não vem em cor de alerta")

    estado["aviso"] = {"enviado": True, "destinatarios": 1, "motivo": None}
    abrir_chamada(pg, "Um so")
    conf("para 1 aluno. Leva" in pg.inner_text("#p-aviso") and "1 alunos" not in pg.inner_text("#p-aviso"), "1 aluno no singular")

    estado["aviso"] = {"enviado": False, "destinatarios": 260, "motivo": "Este público tem 260 alunos e o limite é de 200 avisos por evento, então a chamada abriu sem aviso. Escolha um curso ou um período para reduzir o público."}
    abrir_chamada(pg, "Publico grande")
    t = pg.inner_text("#p-aviso")
    conf(t.startswith("Aviso por e-mail NÃO enviado:") and "260 alunos" in t and "a chamada abriu sem aviso" in t, f"aviso recusado mostra o MOTIVO e que a chamada abriu: {t!r}")
    conf("warn" in (pg.get_attribute("#p-aviso", "class") or "") and pg.is_visible("#p-titulo") and pg.inner_text("#p-titulo") == "Publico grande", "motivo em cor de alerta; a chamada está aberta no painel")

    estado["aviso"] = {"enviado": False, "destinatarios": 0, "motivo": X1}
    abrir_chamada(pg, "Motivo malicioso")
    conf(pg.evaluate("window.__xss || 0") == 0 and pg.locator("#p-aviso img").count() == 0 and X1 in pg.inner_text("#p-aviso"), "motivo malicioso aparece como TEXTO e não executa")

    estado["aviso"] = None            # servidor antigo / sem o campo: nada de linha de aviso
    pg.check("#avisar-email")
    abrir_chamada(pg, "Servidor sem campo")
    conf(not pg.is_visible("#p-aviso"), "resposta sem o campo 'aviso': nenhuma linha de aviso, nenhum erro")
    conf(not erros, "professor: nenhum erro de JavaScript: %s" % erros)
    b.close()

    # =========================== aluno ===========================
    reiniciar(me_avisos=True)
    b, pg, erros = entrar(pw, "aluno", "#v-aluno")
    pg.wait_for_function("!document.querySelector('#avisos-email').disabled")
    conf(pg.is_visible("#avisos-email") and pg.is_checked("#avisos-email"), "aluno com avisos ligados: a caixa vem marcada e habilitada")
    pg.uncheck("#avisos-email")
    pg.wait_for_function("document.querySelector('#msg-aluno').textContent.includes('desligados')")
    conf(estado["avisos"][-1] == {"receber": False} and not pg.is_checked("#avisos-email") and pg.is_enabled("#avisos-email"), "desmarcar envia PUT /me/avisos com receber=false e a caixa fica desmarcada")
    conf("ok" in (pg.get_attribute("#msg-aluno", "class") or ""), "confirmação em verde")
    pg.check("#avisos-email")
    pg.wait_for_function("document.querySelector('#msg-aluno').textContent.includes('ligados')")
    conf(estado["avisos"][-1] == {"receber": True} and pg.is_checked("#avisos-email"), "marcar de novo envia receber=true")

    estado["erro_avisos"] = "Erro interno."
    pg.uncheck("#avisos-email")
    pg.wait_for_function("document.querySelector('#msg-aluno').textContent.includes('Erro interno')")
    conf(pg.is_checked("#avisos-email") and pg.is_enabled("#avisos-email") and "err" in (pg.get_attribute("#msg-aluno", "class") or ""),
         "servidor recusou: a caixa volta ao estado anterior (marcada), mensagem em vermelho e o aluno pode tentar de novo")
    conf(not erros, "aluno: nenhum erro de JavaScript: %s" % erros)
    b.close()

    reiniciar(me_avisos=False)
    b, pg, erros = entrar(pw, "aluno", "#v-aluno", largura=390)
    pg.wait_for_function("!document.querySelector('#avisos-email').disabled")
    conf(not pg.is_checked("#avisos-email"), "aluno que já tinha desligado: a caixa vem DESMARCADA")
    conf(pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "no celular (390 px) a página não ganha rolagem horizontal")
    conf(not erros, "celular: nenhum erro de JavaScript: %s" % erros)
    b.close()

print()
print("FALHA=%d" % len(falhas))
sys.exit(1 if falhas else 0)
