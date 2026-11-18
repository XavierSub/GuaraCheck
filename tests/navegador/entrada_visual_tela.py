"""Visual da tela de entrada no navegador (CT-2101 a CT-2115).
Pedido: na tela do formulário, um verde mais claro, tons de azul ao fundo e as logos. Confere que
 - o fundo da tela de entrada é um degradê azul e as outras telas (aluno, professor, coordenação) NÃO herdam esse fundo nem o verde claro;
 - o botão "Entrar" usa o verde claro com texto escuro e contraste mínimo de 4,5:1; links e botão secundário verdes também passam de 4,5:1;
 - as três logos (curso, UniCerrado, FESG) aparecem no cartão do formulário, carregadas, e no cabeçalho com texto alternativo;
 - a faixa de logos fica dentro do cartão e a página não ganha rolagem horizontal em 1366, 390 e 320 px.
A API é simulada (só /api/config e /api/cursos); não precisa de servidor.
Uso:  pip install playwright && playwright install chromium && python tests/navegador/entrada_visual_tela.py
(GC_HTML=caminho.html testa outra cópia do index.html; sai com código 1 se algo falhar)."""
import json, os, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

WEB = str(Path(__file__).resolve().parents[2]) + "/"
html = open(os.environ.get("GC_HTML", WEB + "index.html"), encoding="utf-8").read()
CFG = {"dominio_aluno": "alunos.unicerrado.edu.br", "dominios_professor": ["unicerrado.edu.br"],
       "matricula_regex": r"^[A-Za-z0-9][A-Za-z0-9._/-]{3,29}$", "coordenacao_aberta": False}


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
    return js({"detail": "mock: " + p}, 404)


falhas = []


def conf(ok, txt):
    print(("OK   " if ok else "FALHA"), txt)
    if not ok:
        falhas.append(txt)


JS_CONTRASTE = """(sel) => {
  const el = document.querySelector(sel);
  const rgb = s => (s.match(/[\\d.]+/g) || []).slice(0, 4).map(Number);
  const lum = ([r, g, b]) => { const f = c => { c /= 255; return c <= .03928 ? c / 12.92 : Math.pow((c + .055) / 1.055, 2.4); }; return .2126 * f(r) + .7152 * f(g) + .0722 * f(b); };
  const fundo = e => { for (let x = e; x; x = x.parentElement) { const c = rgb(getComputedStyle(x).backgroundColor); if (c.length === 3 || (c.length === 4 && c[3] > 0.99)) return c; } return [255, 255, 255]; };
  const fg = rgb(getComputedStyle(el).color), bg = fundo(el);
  const a = lum(fg), b = lum(bg), hi = Math.max(a, b), lo = Math.min(a, b);
  return {razao: (hi + .05) / (lo + .05), bg: bg.join(','), fg: fg.join(',')};
}"""


def abrir(pw, largura, altura=900):
    b = pw.chromium.launch()
    c = b.new_context(viewport={"width": largura, "height": altura})
    c.route("**/*", handler)
    pg = c.new_page()
    erros = []
    pg.on("pageerror", lambda e: erros.append(str(e)))
    pg.goto("http://localhost:8765/")
    pg.wait_for_selector("#v-auth:not([hidden])")
    pg.wait_for_load_state("networkidle")
    return b, pg, erros


with sync_playwright() as pw:
    b, pg, erros = abrir(pw, 1366)

    bg = pg.evaluate("getComputedStyle(document.body).backgroundImage")
    conf("linear-gradient" in bg and "rgb(188, 215, 241)" in bg and "rgb(234, 243, 252)" in bg, f"entrada: fundo em degradê de tons de azul claro: {bg[:120]}")
    conf(pg.evaluate("document.body.offsetHeight >= window.innerHeight - 1"), "entrada: o degradê cobre a altura inteira da janela (a página não termina antes)")

    btn = pg.evaluate("(%s)('#btn-auth')" % JS_CONTRASTE)
    conf(btn["bg"] == "108,203,134", f"botão Entrar no verde claro (#6ccb86): fundo rgb({btn['bg']})")
    conf(btn["razao"] >= 4.5, f"botão Entrar: texto escuro sobre o verde claro, contraste {btn['razao']:.2f}:1 (mínimo 4,5)")
    for sel, nome in (("#btn-esqueci", "link 'Esqueci minha senha'"), ("#toggle", "link 'Primeiro acesso'"), ("#cert-ok", "botão 'Validar' do certificado")):
        if sel == "#cert-ok":
            pg.click("#cert-box summary")
        r = pg.evaluate("(%s)('%s')" % (JS_CONTRASTE, sel))
        conf(r["razao"] >= 4.5, f"{nome}: verde de texto com contraste {r['razao']:.2f}:1 (mínimo 4,5)")
    r = pg.evaluate("(%s)('.hero p')" % JS_CONTRASTE)
    conf(pg.evaluate("getComputedStyle(document.querySelector('.hero p')).color") == "rgb(47, 63, 74)", "texto de apoio do título em cinza-azulado escuro (legível sobre o azul)")

    # logos: cabeçalho (com texto alternativo) e faixa no cartão
    conf(pg.locator("header .marcas img").count() == 3 and all(pg.get_attribute(f"header .marcas img >> nth={i}", "alt") for i in range(3)),
         "cabeçalho: as três logos continuam, cada uma com texto alternativo")
    n = pg.locator("#v-auth .auth-card .parceiros img").count()
    carregadas = pg.evaluate("[...document.querySelectorAll('#v-auth .parceiros img')].every(i => i.complete && i.naturalWidth > 0)")
    srcs = pg.evaluate("[...document.querySelectorAll('#v-auth .parceiros img')].map(i => i.getAttribute('src'))")
    conf(n == 3 and carregadas and srcs == ["assets/logo-curso.png", "assets/logo-unicerrado.png", "assets/logo-fesg.png"],
         f"cartão: três logos (curso, UniCerrado, FESG) carregadas e na ordem do cabeçalho: {srcs}")
    conf(pg.get_attribute("#v-auth .parceiros", "aria-hidden") == "true" and all(pg.get_attribute(f"#v-auth .parceiros img >> nth={i}", "alt") == "" for i in range(3)),
         "faixa de logos do cartão é decorativa (o leitor de tela não repete as marcas do cabeçalho)")
    caixa = pg.evaluate("""() => { const c = document.querySelector('#v-auth .auth-card').getBoundingClientRect(), p = document.querySelector('#v-auth .parceiros').getBoundingClientRect();
        return {dentro: p.left >= c.left && p.right <= c.right && p.bottom <= c.bottom + 1, larg: p.width}; }""")
    conf(caixa["dentro"], "a faixa de logos fica dentro do cartão")

    # outras telas não herdam o visual da entrada
    for tela, vista in (("aluno", "#v-aluno"), ("prof", "#v-prof"), ("admin", "#v-admin")):
        pg.evaluate(f"show('{tela}')")
        info = pg.evaluate("({img: getComputedStyle(document.body).backgroundImage, cor: getComputedStyle(document.body).backgroundColor, tela: document.body.dataset.tela})")
        conf(info["img"] == "none" and info["cor"] == "rgb(245, 247, 237)" and info["tela"] == tela,
             f"tela '{tela}': fundo de sempre (creme), sem o degradê azul: {info}")
    pg.evaluate("show('aluno')")
    pri = pg.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--pri').trim() + '|' + getComputedStyle(document.querySelector('#v-aluno')).getPropertyValue('--pri').trim()")
    conf(pri == "#2f7d40|#2f7d40", f"fora da entrada o verde de sempre continua (--pri): {pri}")
    pg.evaluate("show('auth')")
    conf(pg.evaluate("getComputedStyle(document.querySelector('#v-auth')).getPropertyValue('--pri').trim()") == "#6ccb86", "voltando à entrada: o verde claro volta")

    # troca de aba e modo cadastro mantêm o visual
    pg.click('.tab[data-p="professor"]')
    pg.click("#toggle")
    pg.wait_for_selector("#f-nome:not([hidden])")
    r = pg.evaluate("(%s)('#btn-auth')" % JS_CONTRASTE)
    conf(r["bg"] == "108,203,134" and r["razao"] >= 4.5, f"modo cadastro (professor): botão continua verde claro com contraste {r['razao']:.2f}:1")
    conf(not erros, "entrada: nenhum erro de JavaScript: %s" % erros)
    b.close()

    for larg in (390, 320):
        b, pg, erros = abrir(pw, larg, 800)
        conf(pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), f"{larg} px: sem rolagem horizontal")
        dentro = pg.evaluate("""() => { const c = document.querySelector('#v-auth .auth-card').getBoundingClientRect(), p = document.querySelector('#v-auth .parceiros').getBoundingClientRect();
            return p.left >= c.left && p.right <= c.right; }""")
        conf(dentro, f"{larg} px: a faixa de logos cabe no cartão")
        conf(pg.evaluate("document.body.offsetHeight >= window.innerHeight - 1"), f"{larg} px: o degradê cobre a janela inteira")
        conf(not erros, f"{larg} px: nenhum erro de JavaScript: {erros}")
        b.close()

print()
print("FALHA=%d" % len(falhas))
sys.exit(1 if falhas else 0)
