"""F9 no navegador (CT-1040 a CT-1056): a atualização automática da tela do aluno, com o relógio do navegador controlado
(page.clock), sem esperar de verdade. Confere: intervalo de 10 s com variação aleatória (a turma não pergunta toda no mesmo
segundo), espera crescente quando o servidor falha (20, 40, até 60 s) e volta aos 10 s quando ele volta, nada de perguntas
com a aba escondida, e parar de perguntar depois de sair da conta ou de a sessão expirar.
A resposta de /api/aulas/ativas é simulada DENTRO da página (resolve na hora, sem rede de verdade), para o relógio falso poder
correr direto; o resto da API é simulado pelo Playwright. Uso: python tests/navegador/atualizacao_tela.py
(GC_HTML=outro.html testa outra cópia; sai com código 1 se algo falhar)."""
import json, os, sys, statistics
from pathlib import Path
from playwright.sync_api import sync_playwright

WEB = str(Path(__file__).resolve().parents[2]) + "/"
html = open(os.environ.get("GC_HTML", WEB + "index.html"), encoding="utf-8").read()
ALUNO = {"id": 1, "nome": "Ana Souza", "email": "ana@alunos.unicerrado.edu.br", "perfil": "aluno", "curso": "GTI", "periodo": 3, "matricula": "GTI1"}
CFG = {"dominio_aluno": "alunos.unicerrado.edu.br", "dominios_professor": ["unicerrado.edu.br"], "matricula_regex": r"^[A-Za-z0-9]{4,30}$", "coordenacao_aberta": False}
falhas = []


def conf(ok, txt):
    print(("OK   " if ok else "FALHA"), txt)
    if not ok:
        falhas.append(txt)


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
    if p == "/api/config": return js(CFG)
    if p == "/api/cursos": return js([])
    if p == "/api/auth/login": return js({"token": "t", "usuario": ALUNO})
    if p == "/api/aluno/presencas": return js([])
    if p == "/api/aluno/notas": return js([])
    return js({"detail": "mock"}, 404)


INIT = """
window.__t = []; window.__modo = 'ok';
const _f = window.fetch.bind(window);
window.fetch = (u, o) => {
  if (!String(u).includes('/aulas/ativas')) return _f(u, o);
  window.__t.push(Date.now());                         // hora (do relógio falso) de cada consulta
  const m = window.__modo;
  if (m === 'abort') return Promise.reject(new TypeError('Failed to fetch'));
  const st = m === '500' ? 500 : m === '401' ? 401 : 200;
  const corpo = m === '500' ? {detail: 'Erro interno.'} : m === '401' ? {detail: 'Sessão expirada. Entre novamente.'} : [];
  return Promise.resolve(new Response(JSON.stringify(corpo), {status: st, headers: {'Content-Type': 'application/json'}}));
};
"""


def abrir(pw, modo="ok"):
    b = pw.chromium.launch()
    c = b.new_context(viewport={"width": 375, "height": 812})
    c.route("**/*", handler)
    c.add_init_script(INIT)
    pg = c.new_page()
    erros = []
    pg.on("pageerror", lambda e: erros.append(str(e)))
    pg.clock.install()
    pg.goto("http://localhost:8765/")
    pg.wait_for_selector("#v-auth:not([hidden])")
    pg.evaluate(f"window.__modo = '{modo}'")
    pg.fill("#email", ALUNO["email"]); pg.fill("#senha", "senha12345"); pg.click("#btn-auth")
    pg.wait_for_selector("#v-aluno:not([hidden])")
    return b, pg, erros


def rodar(pg, ms, passo=5000):
    """Avança o relógio do navegador. Em passos, para a resposta do servidor simulado ter tempo de chegar entre um passo e outro."""
    for _ in range(max(1, ms // passo)):
        pg.clock.run_for(passo)
        pg.wait_for_timeout(5)


def modo(pg, m): pg.evaluate(f"window.__modo = '{m}'")
def tempos(pg): return pg.evaluate("window.__t")
def intervalos(pg):
    t = tempos(pg)
    return [b - a for a, b in zip(t, t[1:])]


with sync_playwright() as pw:
    # --- funcionamento normal: 10 s com variação ---
    b, pg, erros = abrir(pw)
    rodar(pg, 10 * 60 * 1000, 500)
    iv = intervalos(pg)
    conf(len(iv) >= 45, f"CT-1040 | em 10 min de relógio a tela consultou o servidor {len(iv) + 1} vezes (esperado cerca de 60)")
    conf(iv and all(7900 <= x <= 12600 for x in iv), f"CT-1041 | todo intervalo fica entre 8 e 12 s, com folga de 0,5 s do passo do relógio (mín {min(iv):.0f} ms, máx {max(iv):.0f} ms)")
    conf(len({round(x, -1) for x in iv}) >= 20, f"CT-1042 | os intervalos variam (a turma não pergunta toda junta): {len({round(x, -1) for x in iv})} valores diferentes")
    conf(9000 <= statistics.mean(iv) <= 11000, f"CT-1043 | a média continua perto de 10 s ({statistics.mean(iv):.0f} ms)")
    conf(not erros, f"CT-1044 | sem erro de JavaScript {erros}")
    b.close()

    # --- servidor com erro 500: a espera dobra até 60 s ---
    b, pg, erros = abrir(pw, "500")
    rodar(pg, 6 * 60 * 1000, 500)
    iv = intervalos(pg)
    esp = [round(x / 1000) for x in iv]
    faixas = [(8000, 12000), (16000, 24000), (32000, 48000), (48000, 72000), (48000, 72000)]
    ok = len(iv) >= 5 and all(lo <= x <= hi for x, (lo, hi) in zip(iv, faixas))
    conf(ok, f"CT-1045 | com o servidor errando a espera cresce 10, 20, 40 e para em 60 s: {esp}")
    conf(all(x <= 72100 for x in iv), "CT-1046 | e nunca passa de 60 s (+20%)")
    conf(len(tempos(pg)) <= 10, f"CT-1047 | a tela não insiste: {len(tempos(pg))} consultas em 6 min (sem backoff seriam ~36)")
    # servidor volta: na primeira consulta que der certo, volta ao ritmo de 10 s
    modo(pg, "ok")
    n = len(tempos(pg))
    rodar(pg, 3 * 60 * 1000, 500)
    iv2 = intervalos(pg)[n:]
    conf(len(iv2) >= 8 and all(7900 <= x <= 12600 for x in iv2[1:]), f"CT-1048 | servidor voltou: a espera volta para ~10 s {[round(x / 1000) for x in iv2[:5]]}")
    txt = pg.inner_text("#msg-aluno") if pg.is_visible("#msg-aluno") else ""
    conf(txt == "", f"CT-1049 | servidor voltou: o aviso de erro some sozinho (texto: '{txt}')")
    b.close()

    # --- sem rede ---
    b, pg, erros = abrir(pw, "abort")
    rodar(pg, 4 * 60 * 1000, 500)
    iv = intervalos(pg)
    conf(len(iv) >= 4 and iv[3] >= 47000, f"CT-1050 | sem rede a espera também cresce até 60 s: {[round(x / 1000) for x in iv]}")
    t = pg.inner_text("#msg-aluno") if pg.is_visible("#msg-aluno") else ""
    conf("conex" in t.lower(), f"CT-1051 | sem rede o aluno vê o aviso ('{t}')")
    b.close()

    # --- aba escondida ---
    b, pg, erros = abrir(pw)
    rodar(pg, 60_000, 500)
    n_ok = len(tempos(pg))
    conf(n_ok >= 4, f"CT-1052 | controle: com a aba à vista a tela consulta de tempos em tempos ({n_ok} em 60 s)")
    pg.evaluate("Object.defineProperty(document, 'hidden', {get: () => true, configurable: true})")
    rodar(pg, 20_000, 500)
    n0 = len(tempos(pg))
    rodar(pg, 120_000, 500)
    conf(len(tempos(pg)) == n0, f"CT-1053 | aba escondida: nenhuma consulta em 2 min ({len(tempos(pg)) - n0})")
    pg.evaluate("Object.defineProperty(document, 'hidden', {get: () => false, configurable: true})")
    rodar(pg, 30_000, 500)
    conf(len(tempos(pg)) > n0, "CT-1054 | ao voltar para a aba, as consultas recomeçam em até 12 s")
    b.close()

    # --- sair da conta ---
    b, pg, erros = abrir(pw)
    rodar(pg, 40_000, 500)
    conf(len(tempos(pg)) >= 3, "CT-1055 | controle: antes de sair, a tela consultava")
    pg.click("#btn-sair")
    pg.wait_for_selector("#v-auth:not([hidden])")
    n0 = len(tempos(pg))
    rodar(pg, 120_000, 500)
    conf(len(tempos(pg)) == n0, f"CT-1056 | depois de sair da conta, nenhuma consulta em 2 min ({len(tempos(pg)) - n0})")
    b.close()

    # --- sessão expirada (401) ---
    b, pg, erros = abrir(pw)
    rodar(pg, 30_000, 500)
    modo(pg, "401")
    rodar(pg, 30_000, 500)
    conf(pg.is_visible("#v-auth"), "CT-1057 | 401 na atualização: a tela volta ao login")
    n0 = len(tempos(pg))
    rodar(pg, 120_000, 500)
    conf(len(tempos(pg)) == n0, f"CT-1058 | e para de consultar ({len(tempos(pg)) - n0} consultas depois)")
    conf(not erros, f"CT-1059 | sem erro de JavaScript {erros}")
    b.close()

print()
print("RESULTADO:", "TUDO OK" if not falhas else f"{len(falhas)} FALHA(S)")
sys.exit(1 if falhas else 0)
