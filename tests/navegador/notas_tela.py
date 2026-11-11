"""F7 no navegador (CT-860 a CT-885): anotações privadas do aluno (escolher evento, contador, salvar, editar, excluir, PDFs, descartar
rascunho, erros do servidor), texto malicioso no título e no texto, rótulo "evento excluído" com a lista de comprovantes paginada e
tela de celular.
A API é simulada (imita o FastAPI); o servidor real não roda aqui.
Uso:  pip install playwright && playwright install chromium && python tests/navegador/notas_tela.py
(GC_HTML=caminho.html testa outra cópia do index.html; sai com código 1 se algo falhar)."""
import json, os, re, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

WEB = str(Path(__file__).resolve().parents[2]) + "/"
html = open(os.environ.get("GC_HTML", WEB + "index.html"), encoding="utf-8").read()
CFG = {"dominio_aluno": "alunos.unicerrado.edu.br", "dominios_professor": ["unicerrado.edu.br"],
       "matricula_regex": r"^[A-Za-z0-9][A-Za-z0-9._/-]{3,29}$", "coordenacao_aberta": False}
X1 = '<img src=x onerror="window.__xss=(window.__xss||0)+1">'
X2 = '"><svg onload="window.__xss=(window.__xss||0)+1">'
X3 = "'><script>window.__xss=(window.__xss||0)+99</script>"
ALUNO = {"id": 1, "nome": "Ana Souza", "email": "ana@alunos.unicerrado.edu.br", "perfil": "aluno", "curso": "Gestão da TI", "periodo": 3, "matricula": "GTI00001"}
PDF_MINIMO = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"
estado = {}


def zerar(n_comp=3):
    estado.clear()
    estado.update(n_comp=n_comp, notas=[], pedidos=[], put="ok", pdf="ok", prox_id=100)


def comp(i):
    return {"id": i, "registrado_em": f"2026-11-{max(1, 28 - i):02d}T14:05:00+00:00", "titulo": f"Evento {i}", "tipo": "aula", "local_tipo": "dentro",
            "disciplina": "ED", "periodo": 3, "professor": "Marcos Lima"}


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
        return js({"token": "t", "usuario": ALUNO})
    if p == "/api/aulas/ativas":
        return js([])
    if p == "/api/me":
        return js({"nome": ALUNO["nome"], "matricula": ALUNO["matricula"], "email": ALUNO["email"], "curso": ALUNO["curso"], "periodo": 3})
    if p == "/api/aluno/presencas":
        pag = int(q.get("pagina", 1))
        todos = [comp(i) for i in range(estado["n_comp"], 0, -1)]
        return js(todos[(pag - 1) * 20: pag * 20])
    if p == "/api/aluno/notas" and met == "GET":
        return js(sorted(estado["notas"], key=lambda n: n["evento_em"], reverse=True))
    m = re.fullmatch(r"/api/aluno/presencas/(\d+)/nota", p)
    if m and met == "PUT":
        corpo = json.loads(route.request.post_data)
        estado["pedidos"].append(("PUT", int(m.group(1)), corpo))
        if estado["put"] == "429":
            return js({"detail": "Muitas tentativas. Aguarde alguns minutos e tente de novo."}, 429)
        if estado["put"] == "409":
            return js({"detail": "Não foi possível salvar agora. Tente de novo."}, 409)
        if estado["put"] == "500":
            return route.fulfill(status=500, content_type="text/html", body="<html>erro</html>")
        pid = int(m.group(1))
        ev = comp(pid)
        antiga = next((n for n in estado["notas"] if n["presenca_id"] == pid), None)
        if antiga:
            antiga["texto"] = corpo["texto"]
            return js({"ok": True, "nota": antiga})
        nova = {"id": estado["prox_id"], "presenca_id": pid, "titulo": ev["titulo"], "tipo": "aula", "disciplina": "ED", "evento_em": ev["registrado_em"],
                "texto": corpo["texto"], "atualizado_em": "2026-11-11T15:00:00+00:00"}
        estado["prox_id"] += 1
        estado["notas"].append(nova)
        return js({"ok": True, "nota": nova})
    m = re.fullmatch(r"/api/aluno/notas/(\d+)", p)
    if m and met == "DELETE":
        estado["pedidos"].append(("DELETE", int(m.group(1)), None))
        estado["notas"] = [n for n in estado["notas"] if n["id"] != int(m.group(1))]
        return js({"ok": True})
    if p == "/api/aluno/notas/pdf" or re.fullmatch(r"/api/aluno/notas/(\d+)/pdf", p):
        estado["pedidos"].append(("PDF", p, None))
        if estado["pdf"] == "429":
            return js({"detail": "Muitas tentativas. Aguarde alguns minutos e tente de novo."}, 429)
        if estado["pdf"] == "404":
            return js({"detail": "Você ainda não tem anotações."}, 404)
        return route.fulfill(status=200, content_type="application/pdf", body=PDF_MINIMO, headers={"Content-Disposition": 'attachment; filename="x.pdf"'})
    return js({"detail": "mock: " + p}, 404)


falhas = []


def conf(ok, txt):
    print(("OK   " if ok else "FALHA"), txt)
    if not ok:
        falhas.append(txt)


def entrar(pw, largura=1366, confirmar=True):
    b = pw.chromium.launch()
    c = b.new_context(viewport={"width": largura, "height": 900}, accept_downloads=True)
    c.route("**/*", handler)
    pg = c.new_page()
    erros, dialogos = [], []
    pg.on("pageerror", lambda e: erros.append(str(e)))
    pg.on("dialog", lambda d: (dialogos.append(d.message), d.accept() if confirmar else d.dismiss()))
    pg.goto("http://localhost:8765/")
    pg.wait_for_selector("#v-auth:not([hidden])")
    pg.fill("#email", ALUNO["email"])
    pg.fill("#senha", "senha12345")
    pg.click("#btn-auth")
    pg.wait_for_selector("#v-aluno:not([hidden])")
    pg.wait_for_selector("#nota-evento")
    pg.wait_for_function("document.querySelectorAll('#nota-evento option').length > 1")
    return b, pg, erros, dialogos


def injetado(pg, raiz):
    return pg.evaluate(f"document.querySelectorAll('{raiz} img[src=\"x\"], {raiz} svg[onload], {raiz} script, {raiz} [onerror], {raiz} [onload]').length")


def msg_de(pg, sel="#msg-notas"):
    return pg.inner_text(sel).strip()


def opcoes(pg):
    return pg.eval_on_selector_all("#nota-evento option", "els => els.map(e => e.textContent)")


with sync_playwright() as pw:
    # =========================== fluxo principal ===========================
    zerar()
    b, pg, erros, dialogos = entrar(pw)
    conf("Você ainda não escreveu nenhuma anotação." in pg.inner_text("#notas") and pg.is_hidden("#notas-pdf-todas"),
         "CT-860 | sem anotações: aviso de lista vazia e botão de PDF escondido")
    conf(pg.is_disabled("#nota-texto") and pg.is_disabled("#nota-salvar"), "CT-861 | antes de escolher o evento, o texto e o botão Salvar ficam travados")
    conf(len(opcoes(pg)) == 4 and "Evento 1" in opcoes(pg)[1] and "Evento 3" in opcoes(pg)[3], "CT-862 | a lista de eventos traz os eventos em que o aluno registrou presença, do mais novo ao mais antigo")

    pg.select_option("#nota-evento", "3")
    conf(not pg.is_disabled("#nota-texto") and not pg.is_disabled("#nota-salvar") and pg.inner_text("#nota-cont") == "0 / 4000",
         "CT-863 | escolhido o evento: texto liberado e contador em 0 / 4000")
    pg.fill("#nota-texto", "Aprendi sobre pilhas.\nSegunda linha.")
    conf(pg.inner_text("#nota-cont") == "36 / 4000", "CT-864 | o contador acompanha o que é digitado")
    pg.click("#nota-salvar")
    pg.wait_for_function("document.querySelector('#msg-notas').textContent.includes('Anotação salva')")
    conf(estado["pedidos"][-1] == ("PUT", 3, {"texto": "Aprendi sobre pilhas.\nSegunda linha."}), "CT-865 | o pedido leva o texto e o id da presença escolhida")
    conf("Só você pode ver" in msg_de(pg) and "Aprendi sobre pilhas." in pg.inner_text("#notas") and not pg.is_hidden("#notas-pdf-todas"),
         "CT-866 | confirmação de privacidade, a anotação entra na lista e o botão de PDF aparece")
    conf("já tem uma anotação deste evento" in pg.inner_text("#nota-aviso"), "CT-867 | o formulário avisa que salvar de novo atualiza a anotação existente")
    conf(any("com anotação" in o for o in opcoes(pg)), "CT-868 | a lista de eventos marca 'com anotação'")
    conf(pg.evaluate("getComputedStyle(document.querySelector('.nota-txt')).whiteSpace") == "pre-wrap", "CT-869 | as quebras de linha do aluno são mantidas na lista")

    # editar
    n_antes = len(estado["pedidos"])
    pg.click('button[data-nota-edit="3"]')
    conf(pg.input_value("#nota-texto") == "Aprendi sobre pilhas.\nSegunda linha." and pg.input_value("#nota-evento") == "3", "CT-870 | Editar leva o texto salvo de volta ao formulário")
    pg.fill("#nota-texto", "Versão revisada.")
    pg.click("#nota-salvar")
    pg.wait_for_function("document.querySelector('#notas').innerText.includes('Versão revisada.')")
    conf(len(estado["notas"]) == 1 and estado["notas"][0]["texto"] == "Versão revisada.", "CT-871 | salvar a edição atualiza a mesma anotação (não cria outra)")

    # texto só com espaços
    pg.fill("#nota-texto", "  \n  ")
    n = len(estado["pedidos"])
    pg.click("#nota-salvar")
    conf("Escreva algo para salvar" in msg_de(pg) and len(estado["pedidos"]) == n, "CT-872 | texto em branco: mensagem clara e nenhum pedido ao servidor")

    # erros do servidor mantêm o texto digitado
    for modo, esperado in (("429", "Muitas tentativas."), ("409", "Tente de novo."), ("500", "Erro 500")):
        estado["put"] = modo
        pg.fill("#nota-texto", f"rascunho {modo}")
        pg.click("#nota-salvar")
        pg.wait_for_function(f"document.querySelector('#msg-notas').textContent.includes({json.dumps(esperado)})")
        conf(pg.input_value("#nota-texto") == f"rascunho {modo}" and not pg.is_disabled("#nota-salvar"),
             f"CT-873 | erro {modo} do servidor: mostra o aviso, mantém o que foi digitado e o botão volta a funcionar")
    estado["put"] = "ok"

    # rascunho x trocar de evento
    pg.fill("#nota-texto", "rascunho que não quero perder")
    dialogos.clear()
    pg.select_option("#nota-evento", "2")
    conf(any("Descartar" in d for d in dialogos), "CT-874 | trocar de evento com rascunho não salvo pergunta antes de descartar")
    b.close()

    # descartar = não
    zerar()
    b, pg, erros, dialogos = entrar(pw, confirmar=False)
    pg.select_option("#nota-evento", "3")
    pg.fill("#nota-texto", "meu rascunho")
    pg.select_option("#nota-evento", "2")
    conf(pg.input_value("#nota-evento") == "3" and pg.input_value("#nota-texto") == "meu rascunho", "CT-875 | respondendo 'não' ao descartar, nada muda (evento e texto ficam)")
    b.close()

    # excluir e PDFs
    zerar()
    estado["notas"] = [{"id": 50, "presenca_id": 3, "titulo": "Evento 3", "tipo": "aula", "disciplina": "ED", "evento_em": "2026-11-25T14:05:00+00:00",
                        "texto": "nota para apagar", "atualizado_em": "2026-11-25T15:00:00+00:00"}]
    b, pg, erros, dialogos = entrar(pw)
    pg.wait_for_selector(".nota")
    with pg.expect_download() as d:
        pg.click('button[data-nota-pdf="50"]')
    conf(d.value.suggested_filename == "anotacao-50.pdf", "CT-876 | PDF de uma anotação baixa com o nome certo")
    with pg.expect_download() as d:
        pg.click("#notas-pdf-todas")
    conf(d.value.suggested_filename == "minhas-anotacoes.pdf", "CT-877 | PDF de todas as anotações baixa com o nome certo")
    estado["pdf"] = "429"
    pg.click("#notas-pdf-todas")
    pg.wait_for_function("document.querySelector('#msg-notas').textContent.includes('Muitas tentativas')")
    conf(not pg.is_disabled("#notas-pdf-todas"), "CT-878 | PDF recusado (429): mostra o aviso e o botão volta a funcionar")
    estado["pdf"] = "ok"
    pg.click('button[data-nota-del="50"]')
    pg.wait_for_function("document.querySelector('#notas').innerText.includes('ainda não escreveu')")
    conf(estado["pedidos"][-1] == ("DELETE", 50, None) and any("Excluir esta anotação" in x for x in dialogos), "CT-879 | excluir pede confirmação, apaga e a lista volta ao estado vazio")
    b.close()

    # =========================== texto malicioso ===========================
    zerar()
    estado["notas"] = [{"id": 60, "presenca_id": 2, "titulo": "</select>" + X1, "tipo": "aula", "disciplina": X2, "evento_em": "2026-11-26T14:05:00+00:00",
                        "texto": X3 + "\n" + X1, "atualizado_em": "2026-11-26T15:00:00+00:00"}]
    b, pg, erros, dialogos = entrar(pw)
    pg.wait_for_selector(".nota")
    conf(injetado(pg, "#v-aluno") == 0 and pg.evaluate("window.__xss || 0") == 0 and not dialogos,
         "CT-880 | título e texto maliciosos aparecem como texto (lista, formulário e menu de eventos) e nada executa")
    pg.click('button[data-nota-edit="2"]')
    conf(pg.input_value("#nota-texto").startswith("'><script>") and injetado(pg, "#nota-form") == 0, "CT-881 | ao editar, o texto malicioso entra no campo como texto puro")
    conf(not erros, "CT-882 | nenhum erro de JavaScript")
    b.close()

    # =========================== comprovantes paginados x rótulo "evento excluído" ===========================
    zerar(n_comp=25)
    estado["notas"] = [
        {"id": 70, "presenca_id": 2, "titulo": "Evento 2", "tipo": "aula", "disciplina": "ED", "evento_em": "2026-11-26T14:05:00+00:00", "texto": "nota de evento antigo", "atualizado_em": "2026-11-26T15:00:00+00:00"},
        {"id": 71, "presenca_id": 900, "titulo": "Palestra apagada", "tipo": "palestra", "disciplina": None, "evento_em": "2026-10-01T14:05:00+00:00", "texto": "evento que não existe mais", "atualizado_em": "2026-10-01T15:00:00+00:00"}]
    b, pg, erros, dialogos = entrar(pw)
    ops = opcoes(pg)
    antigo = [o for o in ops if o.startswith("Evento 2 ")]
    conf(antigo and "evento excluído" not in antigo[0] and "com anotação" in antigo[0],
         "CT-883 | evento antigo ainda não carregado (comprovantes paginados) NÃO é chamado de 'evento excluído'")
    pg.click("#comp-mais")
    pg.wait_for_function("document.querySelectorAll('#comprov article').length >= 25")
    ops = opcoes(pg)
    antigo = [o for o in ops if o.startswith("Evento 2 ")]
    apagado = [o for o in ops if o.startswith("Palestra apagada")]
    conf(antigo and "evento excluído" not in antigo[0] and apagado and "evento excluído" in apagado[0],
         "CT-884 | com todos os comprovantes carregados, só o evento que realmente não existe mais fica marcado como 'evento excluído'")
    b.close()

    # =========================== celular ===========================
    zerar()
    longa = "https://exemplo.com/" + "a" * 300
    estado["notas"] = [{"id": 80, "presenca_id": 3, "titulo": "Evento " + "muito " * 20 + "longo", "tipo": "aula", "disciplina": "ED", "evento_em": "2026-11-25T14:05:00+00:00",
                        "texto": longa + "\n" + longa, "atualizado_em": "2026-11-25T15:00:00+00:00"}]
    b, pg, erros, dialogos = entrar(pw, largura=375)
    pg.wait_for_selector(".nota")
    conf(pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "CT-885 | celular (375 px): link gigante na anotação não cria rolagem lateral")
    pg.select_option("#nota-evento", "3")
    pg.fill("#nota-texto", longa)
    conf(pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1") and not erros, "CT-886 | celular: digitar texto longo no formulário também não cria rolagem lateral nem erro")
    b.close()

print()
print("RESULTADO:", "TUDO OK" if not falhas else f"{len(falhas)} FALHA(S)")
sys.exit(1 if falhas else 0)
