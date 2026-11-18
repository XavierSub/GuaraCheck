"""Certificado de participação no navegador (CT-1728 a CT-1745).
Aluno: o botão "Certificado (PDF)" só aparece quando o evento tem carga horária e a presença cumpre o evento (saída e permanência); baixa
de /certificado com o nome certo; quando não libera, explica por quê; erro do servidor aparece sem quebrar a tela.
Tela de entrada (sem login): "Validar um certificado" mostra AUTÊNTICO / NÃO confere / erros do servidor, com nome malicioso como texto.
A API é simulada (imita o FastAPI); o servidor real não roda aqui.
Uso:  pip install playwright && playwright install chromium && python tests/navegador/certificado_tela.py
(GC_HTML=caminho.html testa outra cópia do index.html; sai com código 1 se algo falhar)."""
import json, os, re, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

WEB = str(Path(__file__).resolve().parents[2]) + "/"
html = open(os.environ.get("GC_HTML", WEB + "index.html"), encoding="utf-8").read()
CFG = {"dominio_aluno": "alunos.unicerrado.edu.br", "dominios_professor": ["unicerrado.edu.br"],
       "matricula_regex": r"^[A-Za-z0-9][A-Za-z0-9._/-]{3,29}$", "coordenacao_aberta": False}
X1 = '<img src=x onerror="window.__xss=(window.__xss||0)+1">'
ALUNO = {"id": 1, "nome": "Ana Souza", "email": "ana@alunos.unicerrado.edu.br", "perfil": "aluno", "curso": "Gestão da TI", "periodo": 3, "matricula": "GTI00001"}
PDF_MINIMO = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"
estado = {}


def reiniciar():
    estado.clear()
    estado.update(pedidos=[], validar=None, comp=[], cert=None)


def comp(i, **kw):
    d = {"id": i, "registrado_em": "2026-11-11T14:05:00+00:00", "titulo": f"Evento {i}", "tipo": "palestra", "local_tipo": "dentro", "disciplina": None,
         "periodo": 3, "professor": "Marcos Lima", "manual": False, "saida_em": None, "exige_saida": False, "permanencia_min": None,
         "permanencia_exigida": None, "situacao": "Presente", "carga_horas": 2.5}
    d.update(kw)
    return d


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
    m = re.fullmatch(r"/api/certificados/validar/(.+)", p)
    if m:
        from urllib.parse import unquote
        estado["pedidos"].append(unquote(m.group(1)))
        r = estado["validar"]
        if isinstance(r, tuple):
            return js({"detail": r[1]}, r[0])
        return js(r or {"valido": False})
    if p == "/api/aulas/ativas":
        return js([])
    if p == "/api/me":
        return js({"nome": ALUNO["nome"], "matricula": ALUNO["matricula"], "email": ALUNO["email"], "curso": ALUNO["curso"], "periodo": 3})
    if p == "/api/aluno/presencas":
        return js(estado["comp"])
    if p == "/api/aluno/notas":
        return js([])
    m = re.fullmatch(r"/api/aluno/presencas/(\d+)/certificado", p)
    if m:
        estado["pedidos"].append(p)
        if estado["cert"]:
            return js({"detail": estado["cert"]}, 409)
        return route.fulfill(status=200, content_type="application/pdf", body=PDF_MINIMO,
                             headers={"Content-Disposition": f'attachment; filename="certificado-{m.group(1)}.pdf"'})
    return js({"detail": "mock: " + p}, 404)


falhas = []


def conf(ok, txt):
    print(("OK   " if ok else "FALHA"), txt)
    if not ok:
        falhas.append(txt)


def abrir(pw, largura=1366):
    b = pw.chromium.launch()
    c = b.new_context(viewport={"width": largura, "height": 900}, accept_downloads=True)
    c.route("**/*", handler)
    pg = c.new_page()
    erros = []
    pg.on("pageerror", lambda e: erros.append(str(e)))
    pg.goto("http://localhost:8765/")
    pg.wait_for_selector("#v-auth:not([hidden])")
    return b, pg, erros


def espera_msg(pg, seletor, trecho, t=6000):
    pg.wait_for_function("([s, t]) => document.querySelector(s).textContent.includes(t)", arg=[seletor, trecho], timeout=t)


with sync_playwright() as pw:
    # =========================== validar certificado (sem login) ===========================
    reiniciar()
    b, pg, erros = abrir(pw)
    conf(pg.is_visible("#cert-box summary") and not pg.locator("#cert-box").evaluate("e => e.open"), "tela de entrada: 'Validar um certificado' começa fechado")
    pg.click("#cert-box summary")
    conf(pg.is_visible("#cert-cod") and pg.is_visible("#cert-ok"), "ao abrir mostra o campo do código e o botão 'Validar'")
    pg.click("#cert-ok")
    conf("Digite o código" in pg.inner_text("#cert-res") and not estado["pedidos"], "código vazio: orienta e NÃO chama o servidor")
    estado["validar"] = {"valido": True, "nome": X1, "evento": "Semana de Tecnologia", "data": "2026-11-11T14:00:00+00:00", "carga_horas": 2.5}
    pg.fill("#cert-cod", " C7-0123456789AB ")
    pg.press("#cert-cod", "Enter")
    espera_msg(pg, "#cert-res", "AUTÊNTICO")
    t = pg.inner_text("#cert-res")
    conf("Semana de Tecnologia" in t and "11/11/2026" in t and "2,5 horas" in t and X1 in t, "válido: nome, evento, data e '2,5 horas' (nome malicioso como TEXTO)")
    conf(pg.evaluate("window.__xss || 0") == 0 and pg.locator("#cert-res img").count() == 0, "o nome malicioso não executou")
    conf("ok" in (pg.get_attribute("#cert-res", "class") or "") and estado["pedidos"] == ["C7-0123456789AB"], f"resposta em verde; o servidor recebeu o código sem espaços: {estado['pedidos']}")
    estado["validar"] = {"valido": False}
    pg.click("#cert-ok")
    espera_msg(pg, "#cert-res", "NÃO confere")
    conf("err" in (pg.get_attribute("#cert-res", "class") or ""), "falso: 'NÃO confere' em vermelho")
    estado["validar"] = (422, "Formato inválido. Digite o código do certificado, como C1042-9F3A7C21B4AA.")
    pg.fill("#cert-cod", "abc")
    pg.click("#cert-ok")
    espera_msg(pg, "#cert-res", "Formato inválido")
    estado["validar"] = (429, "Muitas tentativas. Aguarde alguns minutos e tente de novo.")
    pg.click("#cert-ok")
    espera_msg(pg, "#cert-res", "Muitas tentativas")
    conf(pg.is_enabled("#cert-ok"), "422 e 429 do servidor: mensagem aparece e o botão volta a funcionar")
    estado["validar"] = {"valido": True, "nome": "Ana", "evento": "Aula", "data": None, "carga_horas": 1}
    pg.click("#cert-ok")
    espera_msg(pg, "#cert-res", "AUTÊNTICO")
    conf("1 hora." in pg.inner_text("#cert-res") and "1 horas" not in pg.inner_text("#cert-res"), "1 hora no singular; sem data não quebra")
    conf(not erros, "validação: nenhum erro de JavaScript: %s" % erros)
    b.close()

    # =========================== aluno ===========================
    reiniciar()
    estado["comp"] = [comp(33, situacao="Permanência insuficiente", exige_saida=True, saida_em="2026-11-11T14:35:00+00:00", permanencia_min=30, permanencia_exigida=60),
                      comp(32, carga_horas=None),
                      comp(31)]
    b, pg, erros = abrir(pw, largura=390)
    pg.click('.tab[data-p="aluno"]')
    pg.fill("#email", ALUNO["email"])
    pg.fill("#senha", "senha12345")
    pg.click("#btn-auth")
    pg.wait_for_selector("#comprov .comp")
    pg.wait_for_function("document.querySelectorAll('#comprov .comp').length === 3")
    c33, c32, c31 = (pg.inner_text(f"#comprov .comp:nth-child({i})") for i in (1, 2, 3))
    conf("Certificado (PDF)" in c31, "presença que cumpre o evento e tem carga horária: botão 'Certificado (PDF)'")
    conf("Certificado (PDF)" not in c32 and "certificado deste evento" not in c32, "evento sem carga horária: sem botão e sem aviso")
    conf("Certificado (PDF)" not in c33 and "só é liberado com a saída registrada e a permanência mínima" in c33, "permanência insuficiente: sem botão, com a explicação")
    with pg.expect_download() as d:
        pg.click('#comprov button[data-cert="31"]')
    conf(d.value.suggested_filename == "certificado-31.pdf" and "/api/aluno/presencas/31/certificado" in estado["pedidos"], "baixa o certificado pela rota certa, com o nome certificado-31.pdf")
    estado["cert"] = "Você não registrou a saída deste evento, por isso o certificado não pode ser emitido. Procure o professor."
    pg.click('#comprov button[data-cert="31"]')
    espera_msg(pg, "#msg-aluno", "não registrou a saída")
    conf("err" in (pg.get_attribute("#msg-aluno", "class") or "") and pg.is_enabled('#comprov button[data-cert="31"]'), "409 do servidor: mensagem em vermelho e o botão volta ao normal")
    conf(not erros, "aluno: nenhum erro de JavaScript: %s" % erros)
    b.close()

print()
print("FALHA=%d" % len(falhas))
sys.exit(1 if falhas else 0)
