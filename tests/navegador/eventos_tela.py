"""F3 no navegador (CT-381 a CT-385): duplo clique em 'Abrir chamada', mensagem de erro de validação e injeção de código em títulos de evento
(painel do professor e lista do aluno). A API é simulada (imita o formato do FastAPI); o servidor real não roda aqui.
Uso:  pip install playwright && playwright install chromium && python tests/navegador/eventos_tela.py
(GC_HTML=caminho.html testa outra cópia do index.html; sai com código 1 se algo falhar)."""
import json, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright
WEB = str(Path(__file__).resolve().parents[2]) + "/"
import os
html = open(os.environ.get("GC_HTML", WEB + "index.html"), encoding="utf-8").read()
CFG = {"dominio_aluno": "alunos.unicerrado.edu.br", "dominios_professor": ["unicerrado.edu.br"], "matricula_regex": r"^[A-Za-z0-9][A-Za-z0-9._/-]{3,29}$", "coordenacao_aberta": False}
X1 = '<img src=x onerror="window.__xss=(window.__xss||0)+1">'
X2 = '"><svg onload="window.__xss=(window.__xss||0)+1">'
X3 = "'><script>window.__xss=(window.__xss||0)+99</script>"
ADMIN = {"id": 3, "nome": "Coord", "email": "c@unicerrado.edu.br", "perfil": "admin", "departamento": "Coordenação"}
USERS = {"admin": ADMIN, "professor": {"id": 2, "nome": "Marcos Lima", "email": "marcos@unicerrado.edu.br", "perfil": "professor", "departamento": "Exatas", "registro_funcional": "DOC-1"},
         "aluno": {"id": 1, "nome": "Ana", "email": "ana@alunos.unicerrado.edu.br", "perfil": "aluno", "curso": X1, "periodo": 3, "matricula": "202610405"}}
EVENTO = {"id": 11, "titulo": X1, "latitude": -18.4, "longitude": -49.2, "raio_metros": 50, "ativa": True, "tipo": "aula", "local_tipo": "dentro",
          "local_descricao": X2, "curso_id": 1, "curso": X3, "periodo": 3, "disciplina": X2, "aberta_em": "2026-11-11T14:00:00+00:00",
          "encerrada_em": None, "total": 0, "codigo_validacao": X3, "professor": X1}
HIST = [dict(EVENTO, id=12, titulo=X2), dict(EVENTO, id=11, ativa=False)]
posts = []
modo = {"post": "ok", "hist": "xss"}
def _ev(i, titulo=None): return dict(EVENTO, id=i, titulo=titulo or f"Evento {i}", ativa=False, codigo_validacao="123", total=1)
def handler_de(quem):
    def handler(route):
        u = route.request.url
        if "localhost:8765" not in u: return route.abort()
        p = u.split("localhost:8765", 1)[1].split("?")[0]
        js = lambda o, c=200: route.fulfill(status=c, content_type="application/json", body=json.dumps(o))
        if p == "/": return route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)
        if p.startswith("/assets/"): return route.fulfill(status=200, content_type="image/png", body=open(WEB + p[1:], "rb").read())
        if p == "/api/config": return js(CFG)
        if p == "/api/cursos": return js([{"id": 1, "nome": X1}])
        if p == "/api/auth/login": return js({"token": "t", "usuario": USERS[quem]})
        pagina = int((u.split("pagina=")[1].split("&")[0]) if "pagina=" in u else 1)
        if p == "/api/professor/aulas":
            if modo["hist"] == "xss": return js(HIST)
            return js([_ev(i) for i in range(100, 70, -1)] if pagina == 1 else [_ev(i) for i in range(70, 65, -1)] if pagina == 2 else [])
        if p == "/api/admin/aulas":
            return js([dict(_ev(i), professor="Prof", total=1) for i in range(300, 240, -1)] if pagina == 1 else [dict(_ev(i), professor="Prof", total=1) for i in range(240, 237, -1)])
        if p == "/api/admin/login": return js({"token": "t", "usuario": USERS["admin"]})
        if p.startswith("/api/admin/"): return js([])
        if p == "/api/aulas/ativas": return js([dict(EVENTO, ja_registrou=False)])
        if p == "/api/aulas" and route.request.method == "POST":
            posts.append(time.time())
            if modo["post"] == "422":   # o que o FastAPI devolve quando o Pydantic recusa o corpo
                return js({"detail": [{"type": "greater_than_equal", "loc": ["body", "raio_metros"], "msg": "Input should be greater than or equal to 5", "input": 2, "ctx": {"ge": 5}}]}, 422)
            time.sleep(0.4)
            return js(EVENTO)
        if p.endswith("/presencas"): return js({"ativa": True, "total": 0, "limite": 2000, "presencas": []})
        if p.endswith("/tentativas") or p.endswith("/ausentes"): return js({"tentativas": [], "ausentes": [], "total": 0, "aviso": None})
        if p in ("/api/aluno/presencas", "/api/aluno/notas"): return js([])
        return js({"detail": "mock: " + p}, 404)
    return handler
falhas = []
def conf(ok, txt):
    print(("OK   " if ok else "FALHA"), txt)
    if not ok: falhas.append(txt)
def entrar(pw, quem, vista):
    b = pw.chromium.launch(); c = b.new_context(viewport={"width": 1366, "height": 900}); c.route("**/*", handler_de(quem))
    pg = c.new_page(); erros = []; dialogos = []
    pg.on("pageerror", lambda e: erros.append(str(e))); pg.on("dialog", lambda d: (dialogos.append(d.message), d.dismiss()))
    pg.goto("http://localhost:8765/"); pg.wait_for_selector("#v-auth:not([hidden])")
    pg.click(f'.tab[data-p="{quem}"]'); pg.fill("#email", USERS[quem]["email"]); pg.fill("#senha", "senha12345"); pg.click("#btn-auth")
    pg.wait_for_selector(vista + ":not([hidden])"); pg.wait_for_timeout(1200)
    return b, pg, erros, dialogos
def injetado(pg, raiz):
    return pg.evaluate(f"document.querySelectorAll('{raiz} img[src=\"x\"], {raiz} svg[onload], {raiz} script, {raiz} [onerror], {raiz} [onload]').length")
with sync_playwright() as pw:
    # ---------- professor ----------
    b, pg, erros, dialogos = entrar(pw, "professor", "#v-prof")
    conf(injetado(pg, "#hist") == 0 and pg.evaluate("window.__xss || 0") == 0, "histórico do professor: títulos maliciosos aparecem como texto (nada executou)")
    pg.fill("#titulo", "Aula de teste"); pg.fill("#lat", "-18.4157"); pg.fill("#lon", "-49.2183")
    posts.clear()
    pg.dblclick('#form-aula button[type="submit"]'); pg.wait_for_timeout(1500)
    conf(len(posts) == 1, f"duplo clique em 'Abrir chamada' envia 1 pedido (enviou {len(posts)})")
    conf(injetado(pg, "#painel") == 0 and pg.evaluate("window.__xss || 0") == 0, "painel da chamada aberta: título, local, disciplina e código maliciosos aparecem como texto")
    modo["post"] = "422"; pg.fill("#raio", "2"); pg.click('#form-aula button[type="submit"]'); pg.wait_for_timeout(600)
    texto = pg.inner_text("#msg-prof")
    conf("Raio de tolerância" in texto and "no mínimo 5" in texto, f"erro de validação do servidor diz qual campo errou e o limite (mensagem mostrada: {texto!r})")
    conf(not dialogos and not erros, f"sem diálogos nem erros de JS: {dialogos} {erros}")
    b.close()
    # ---------- histórico em páginas (professor) ----------
    modo["hist"] = "muitos"
    b, pg, erros, dialogos = entrar(pw, "professor", "#v-prof")
    n1 = pg.locator("#hist li").count(); visivel1 = pg.is_visible("#hist-mais")
    conf(n1 == 30 and visivel1, f"histórico do professor mostra 30 eventos e o botão 'Ver eventos mais antigos' (viu {n1}, botão visível={visivel1})")
    pg.click("#hist-mais"); pg.wait_for_timeout(500)
    n2 = pg.locator("#hist li").count(); visivel2 = pg.is_visible("#hist-mais")
    conf(n2 == 35 and not visivel2, f"depois de 'Ver mais' aparecem os 35 e o botão some (viu {n2}, botão visível={visivel2})")
    conf(not dialogos and not erros, f"sem diálogos nem erros de JS: {dialogos} {erros}")
    b.close()
    # ---------- painel da coordenação em páginas ----------
    b, pg, erros, dialogos = entrar(pw, "admin", "#v-admin")
    pg.click('#adm-tabs button[data-t="aulas"]'); pg.wait_for_timeout(600)
    l1 = pg.locator("#adm-corpo tbody tr").count(); prox = pg.locator('#adm-corpo [data-act="pag"][data-v="1"]')
    conf(l1 == 60 and prox.count() == 1 and prox.is_enabled(), f"coordenação: 60 eventos na página 1 e 'Próxima' habilitada (viu {l1})")
    prox.click(); pg.wait_for_timeout(600)
    l2 = pg.locator("#adm-corpo tbody tr").count(); prox2 = pg.locator('#adm-corpo [data-act="pag"][data-v="1"]'); ant = pg.locator('#adm-corpo [data-act="pag"][data-v="-1"]')
    conf(l2 == 3 and prox2.is_disabled() and ant.is_enabled(), f"coordenação: a página 2 tem os 3 restantes e só 'Anterior' habilitada (viu {l2})")
    conf(not dialogos and not erros, f"sem diálogos nem erros de JS: {dialogos} {erros}")
    b.close()
    # ---------- aluno ----------
    b, pg, erros, dialogos = entrar(pw, "aluno", "#v-aluno")
    conf(injetado(pg, "#aulas") == 0 and pg.evaluate("window.__xss || 0") == 0, "lista de chamadas do aluno: título, curso, disciplina e professor maliciosos aparecem como texto")
    conf(not dialogos and not erros, f"sem diálogos nem erros de JS: {dialogos} {erros}")
    b.close()
print("\nRESULTADO:", "tudo certo" if not falhas else f"{len(falhas)} FALHA(S): {falhas}")
sys.exit(1 if falhas else 0)
