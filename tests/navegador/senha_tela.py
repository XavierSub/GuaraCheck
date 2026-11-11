"""Mostrar/ocultar senha no navegador (CT-1100 a CT-1115): botão de olho em todos os campos de senha (login, cadastro, chave da
coordenação, trocar senha e senha provisória), nome acessível, teclado, volta a esconder ao enviar e ao sair da aba, campos criados
depois pelas abas da coordenação, tamanho do alvo de toque, contraste e celular.
A API é simulada (imita o FastAPI); o servidor real não roda aqui.
Uso:  pip install playwright && playwright install chromium && python tests/navegador/senha_tela.py
(GC_HTML=caminho.html testa outra cópia do index.html; sai com código 1 se algo falhar)."""
import json, os, re, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

WEB = str(Path(__file__).resolve().parents[2]) + "/"
html = open(os.environ.get("GC_HTML", WEB + "index.html"), encoding="utf-8").read()
CFG = {"dominio_aluno": "alunos.unicerrado.edu.br", "dominios_professor": ["unicerrado.edu.br"],
       "matricula_regex": r"^[A-Za-z0-9][A-Za-z0-9._/-]{3,29}$", "coordenacao_aberta": False}
ADMIN = {"id": 9, "nome": "Coord Geral", "email": "coord@unicerrado.edu.br", "perfil": "admin", "curso": None}
RESUMO = {"alunos": 312, "alunos_inativos": 4, "alunos_sem_curso": 0, "professores": 18, "professores_pendentes": 0, "aulas": 140, "aulas_ativas": 0,
          "presencas": 5120, "matricula_obrigatoria": True, "matriculas_autorizadas": 400, "matriculas_utilizadas": 312, "pedidos_senha_pendentes": 0,
          "verificacao_email": True, "senha_provisoria": True, "por_curso": [{"curso": "Direito", "alunos": 192}]}
PROFESSORES = [{"id": 2, "nome": "Marcos Lima", "email": "marcos@unicerrado.edu.br", "registro_funcional": "DOC-1", "departamento": "Exatas", "ativo": True,
                "pendente": False, "criado_em": "2026-08-01T10:00:00+00:00", "aulas": 12, "admin": False, "fixo_env": False}]
estado = {}


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
        estado["login"] = json.loads(route.request.post_data or "{}")
        return js({"detail": "E-mail ou senha incorretos."}, 401)
    if p == "/api/admin/login":
        return js({"token": "t-adm", "usuario": ADMIN})
    if p == "/api/admin/resumo":
        return js(RESUMO)
    if p == "/api/admin/professores":
        return js(PROFESSORES)
    return js({"detail": "mock: " + p}, 404)


falhas = []


def conf(ok, txt):
    print(("OK   " if ok else "FALHA"), txt)
    if not ok:
        falhas.append(txt)


def novo(pw, largura=1366, toque=False):
    b = pw.chromium.launch()
    opc = {"viewport": {"width": largura, "height": 900}}
    if toque:
        opc.update(has_touch=True, is_mobile=True)
    c = b.new_context(**opc)
    c.route("**/*", handler)
    pg = c.new_page()
    erros = []
    pg.on("pageerror", lambda e: erros.append(str(e)))
    pg.goto("http://localhost:8765/")
    pg.wait_for_selector("#v-auth:not([hidden])")
    return b, pg, erros


def tipo(pg, sel):
    return pg.eval_on_selector(sel, "e => e.type")


def botao(pg, campo):
    return pg.locator(f"{campo} + .pw-btn")


def contraste(a, b):
    def lum(rgb):
        r, g, bb = [(v / 255) / 12.92 if v / 255 <= 0.03928 else (((v / 255) + 0.055) / 1.055) ** 2.4 for v in rgb]
        return 0.2126 * r + 0.7152 * g + 0.0722 * bb
    la, lb = sorted([lum(a), lum(b)], reverse=True)
    return (la + 0.05) / (lb + 0.05)


def rgb(txt):
    return [int(float(x)) for x in re.findall(r"[\d.]+", txt)[:3]]


with sync_playwright() as pw:
    # =========================== login ===========================
    b, pg, erros = novo(pw)
    conf(pg.eval_on_selector("#senha", "e => e.parentElement.classList.contains('pw')") and botao(pg, "#senha").count() == 1,
         "CT-1100 | login: o campo de senha ganha um botão de olho dentro de um invólucro .pw")
    conf(tipo(pg, "#senha") == "password" and botao(pg, "#senha").get_attribute("aria-label") == "Mostrar senha",
         "CT-1101 | login: começa escondida e o botão se chama 'Mostrar senha'")
    pg.fill("#senha", "Segredo#2026")
    botao(pg, "#senha").click()
    conf(tipo(pg, "#senha") == "text" and pg.input_value("#senha") == "Segredo#2026" and botao(pg, "#senha").get_attribute("aria-label") == "Ocultar senha",
         "CT-1102 | clicar no olho mostra a senha digitada e o botão passa a se chamar 'Ocultar senha'")
    conf(pg.evaluate("document.activeElement.classList.contains('pw-btn')"), "CT-1103 | o foco continua no botão depois de clicar (leitor de tela ouve o novo nome)")
    botao(pg, "#senha").click()
    conf(tipo(pg, "#senha") == "password" and pg.input_value("#senha") == "Segredo#2026", "CT-1104 | clicar de novo esconde a senha sem perder o que foi digitado")
    arvore = pg.locator("#f-senha").aria_snapshot()
    conf(re.search(r'- textbox "Senha"(:|\n|$)', arvore) is not None and pg.get_by_role("button", name="Mostrar senha").count() == 1,
         f"CT-1105 | árvore de acessibilidade: o campo se chama só 'Senha' (o botão não entra no nome) e há um único botão 'Mostrar senha' {arvore!r}")

    # teclado
    pg.focus("#senha")
    pg.keyboard.press("Tab")
    conf(pg.evaluate("document.activeElement.classList.contains('pw-btn')"), "CT-1106 | teclado: Tab sai do campo e cai direto no botão do olho")
    pg.keyboard.press("Space")
    ligou = tipo(pg, "#senha") == "text"
    pg.keyboard.press("Enter")
    conf(ligou and tipo(pg, "#senha") == "password", "CT-1107 | teclado: Espaço mostra e Enter esconde")

    # envio com a senha visível
    botao(pg, "#senha").click()
    pg.fill("#email", "ana@alunos.unicerrado.edu.br")
    pg.click("#btn-auth")
    pg.wait_for_function("document.querySelector('#msg-auth') && !document.querySelector('#msg-auth').hidden")
    conf(estado.get("login", {}).get("senha") == "Segredo#2026" and tipo(pg, "#senha") == "password",
         "CT-1108 | enviar com a senha visível manda o valor certo e o campo volta a esconder")

    # aba sai de cena
    botao(pg, "#senha").click()
    pg.evaluate("Object.defineProperty(document, 'hidden', {value: true, configurable: true}); document.dispatchEvent(new Event('visibilitychange'))")
    conf(tipo(pg, "#senha") == "password" and botao(pg, "#senha").get_attribute("aria-label") == "Mostrar senha",
         "CT-1109 | ao trocar de aba ou bloquear o celular, a senha volta a ficar escondida")
    pg.evaluate("delete document.hidden")

    # =========================== cadastro ===========================
    pg.click('.tab[data-p="professor"]')
    pg.click("#toggle")
    pg.wait_for_selector("#f-senha2:not([hidden])")
    nomes = {c: (botao(pg, c).get_attribute("aria-label") if botao(pg, c).count() else None) for c in ("#senha", "#senha2", "#chave")}
    conf(nomes == {"#senha": "Mostrar senha", "#senha2": "Mostrar confirmação da senha", "#chave": "Mostrar chave de acesso"},
         f"CT-1110 | cadastro de professor: senha, confirmação e chave da coordenação têm botões com nomes diferentes {nomes}")
    conf(all(botao(pg, c).is_visible() for c in ("#senha", "#senha2", "#chave")), "CT-1111 | cadastro: os três botões aparecem na tela")
    ar2, ar3 = pg.locator("#f-senha2").aria_snapshot(), pg.locator("#f-chave").aria_snapshot()
    conf(re.search(r'- textbox "Confirmar senha"(:|\n|$)', ar2) is not None and
         re.search(r'- textbox "Chave de Acesso da Coordenação \(fornecida pela coordenação\)"(:|\n|$)', ar3) is not None,
         f"CT-1112 | cadastro: os nomes dos campos não ganham o texto dos botões {ar2!r} {ar3!r}")
    b.close()

    # =========================== campos criados depois (coordenação) ===========================
    b, pg, erros = novo(pw)
    pg.click('.tab[data-p="admin"]')
    pg.fill("#email", ADMIN["email"])
    pg.fill("#senha", "senha12345")
    pg.click("#btn-auth")
    pg.wait_for_selector("#v-admin:not([hidden])")
    pg.wait_for_selector("#ms-nova2")
    esperado = {"#ms-atual": "Mostrar senha atual", "#ms-nova": "Mostrar nova senha", "#ms-nova2": "Mostrar repetição da nova senha"}
    achado = {c: (botao(pg, c).get_attribute("aria-label") if botao(pg, c).count() else None) for c in esperado}
    conf(achado == esperado, f"CT-1113 | coordenação: 'Trocar minha senha' (criado depois da tela) ganha os 3 botões {achado}")
    larg = pg.evaluate("['#ms-atual','#ms-nova','#ms-nova2'].map(s => document.querySelector(s).getBoundingClientRect().width)")
    dentro = pg.evaluate("""['#ms-atual','#ms-nova','#ms-nova2'].every(s => { const i = document.querySelector(s).getBoundingClientRect(), b = document.querySelector(s).nextElementSibling.getBoundingClientRect();
        return b.left >= i.left && b.right <= i.right && b.top >= i.top && b.bottom <= i.bottom })""")
    conf(all(w >= 140 for w in larg) and dentro, f"CT-1114 | os campos da linha mantêm a largura (>= 140 px: {[round(w) for w in larg]}) e cada olho fica dentro do seu campo")
    folga = pg.eval_on_selector("#ms-nova", "e => parseFloat(getComputedStyle(e).paddingRight)")
    conf(folga >= 44, f"CT-1115 | o texto digitado não passa por baixo do olho (folga à direita de {folga:.0f} px)")
    pg.click('#adm-tabs button[data-t="professores"]')
    pg.wait_for_selector("#np-senha")
    conf(botao(pg, "#np-senha").get_attribute("aria-label") == "Mostrar senha provisória" and botao(pg, "#np-reg").count() == 0,
         "CT-1116 | aba Professores: só a senha provisória ganha o olho (nome e registro não)")
    pg.click('#adm-tabs button[data-t="resumo"]')
    pg.wait_for_selector("#ms-nova2")
    pg.click('#adm-tabs button[data-t="professores"]')
    pg.wait_for_selector("#np-senha")
    conf(pg.locator(".pw-btn").count() == pg.locator("input.pw-in").count() == pg.locator("#v-admin .pw-btn").count() + pg.locator("#v-auth .pw-btn").count(),
         "CT-1117 | trocar de aba várias vezes não duplica botões")
    tam = pg.eval_on_selector("#np-senha + .pw-btn", "e => { const r = e.getBoundingClientRect(); return [r.width, r.height] }")
    conf(min(tam) >= 24, f"CT-1118 | alvo de toque do olho tem pelo menos 24x24 px (WCAG 2.2): {tam}")
    cor = rgb(pg.eval_on_selector("#np-senha + .pw-btn", "e => getComputedStyle(e).color"))
    fundo = rgb(pg.eval_on_selector("#np-senha", "e => getComputedStyle(e).backgroundColor"))
    conf(contraste(cor, fundo) >= 3, f"CT-1119 | contraste do ícone contra o campo é de pelo menos 3:1 (WCAG 1.4.11): {contraste(cor, fundo):.2f}")
    conf(not erros, f"CT-1120 | nenhum erro de JavaScript na coordenação {erros}")
    b.close()

    # =========================== celular ===========================
    b, pg, erros = novo(pw, largura=375, toque=True)
    tam = pg.eval_on_selector("#senha + .pw-btn", "e => { const r = e.getBoundingClientRect(); return [r.width, r.height] }")
    conf(min(tam) >= 44, f"CT-1121 | celular (toque): o olho tem 44x44 px ou mais: {tam}")
    pg.tap("#senha + .pw-btn")
    conf(tipo(pg, "#senha") == "text", "CT-1122 | celular: tocar no olho mostra a senha")
    conf(pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1") and not erros, "CT-1123 | celular (375 px): sem rolagem lateral nem erro")
    b.close()
    b, pg, erros = novo(pw, largura=320)
    pg.click('.tab[data-p="professor"]')
    pg.click("#toggle")
    pg.wait_for_selector("#f-chave:not([hidden])")
    conf(pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1") and not erros, "CT-1124 | 320 px: cadastro de professor com os três olhos, sem rolagem lateral")
    b.close()

print()
print("RESULTADO:", "TUDO OK" if not falhas else f"{len(falhas)} FALHA(S)")
sys.exit(1 if falhas else 0)
