"""F8 no navegador (CT-901 a CT-9xx): auditoria da INTERFACE nas telas do aluno, do professor e da coordenação, em 5 tamanhos de
tela (celular pequeno, celular, tablet, notebook e TV), com o que dá para conferir sem olho humano: erros de JavaScript, rolagem lateral,
elementos que passam da tela, contraste de cor (WCAG AA), nome de botões e campos, texto alternativo de imagens, tamanho dos alvos de toque,
foco do teclado, janela "Mostrar ao professor" (Esc, foco), animação com "reduzir movimento", celular em modo escuro e telas de
erro e de lista vazia. Também salva capturas de tela (SHOTS=pasta) para olhar com calma.
A API é simulada (imita o FastAPI); o servidor real, o GPS e a rede da faculdade não rodam aqui.
Uso:  pip install playwright && playwright install chromium && python tests/navegador/interface_tela.py
(GC_HTML=caminho.html testa outra cópia do index.html; SHOTS=pasta salva as capturas; sai com código 1 se algo falhar)."""
import json, os, re, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

WEB = str(Path(__file__).resolve().parents[2]) + "/"
html = open(os.environ.get("GC_HTML", WEB + "index.html"), encoding="utf-8").read()
SHOTS = os.environ.get("SHOTS")
CFG = {"dominio_aluno": "alunos.unicerrado.edu.br", "dominios_professor": ["unicerrado.edu.br"],
       "matricula_regex": r"^[A-Za-z0-9][A-Za-z0-9._/-]{3,29}$", "coordenacao_aberta": False}
ALUNO = {"id": 1, "nome": "Ana Beatriz de Albuquerque Souza", "email": "ana@alunos.unicerrado.edu.br", "perfil": "aluno", "curso": "Gestão da Tecnologia da Informação",
         "periodo": 3, "matricula": "GTI202610405"}
PROF = {"id": 2, "nome": "Marcos Lima", "email": "marcos@unicerrado.edu.br", "perfil": "professor", "departamento": "Exatas", "registro_funcional": "DOC-1"}
ADMIN = {"id": 9, "nome": "Coord Geral", "email": "coord@unicerrado.edu.br", "perfil": "admin", "curso": None}
LAT, LON = -18.4157, -49.2183
EVENTO = {"id": 11, "titulo": "Algoritmos e Estruturas de Dados II", "latitude": LAT, "longitude": LON, "raio_metros": 50, "ativa": True, "tipo": "aula",
          "local_tipo": "dentro", "local_descricao": "Bloco B, sala 12", "curso_id": 1, "curso": "Gestão da TI", "periodo": 3, "disciplina": "ED2",
          "aberta_em": "2026-11-11T14:00:00+00:00", "encerrada_em": None, "total": 2, "codigo_validacao": "K7Q2"}
EVENTO_ALUNO = {"id": 11, "titulo": EVENTO["titulo"], "professor": "Marcos Lima", "latitude": LAT, "longitude": LON, "raio_metros": 50, "tipo": "aula",
                "local_tipo": "dentro", "local_descricao": "Bloco B, sala 12", "curso": "Gestão da TI", "periodo": 3, "disciplina": "ED2",
                "aberta_em": "2026-11-11T14:00:00+00:00", "ja_registrou": False}
RESUMO = {"alunos": 312, "alunos_inativos": 4, "alunos_sem_curso": 1, "professores": 18, "professores_pendentes": 2, "aulas": 140, "aulas_ativas": 3,
          "presencas": 5120, "matricula_obrigatoria": True, "matriculas_autorizadas": 400, "matriculas_utilizadas": 312, "pedidos_senha_pendentes": 1,
          "verificacao_email": True, "senha_provisoria": False, "por_curso": [{"curso": "Gestão da Tecnologia da Informação", "alunos": 120}, {"curso": "Direito", "alunos": 192}]}
estado = {}


def zerar():
    estado.clear()
    estado.update(ativas="ok", prof_com_ativa=True, hist_vazio=False)


def pres(i):
    return {"id": i, "nome": f"Aluno {i}", "matricula": f"GTI{i:05d}", "email": f"a{i}@alunos.unicerrado.edu.br", "curso": "Gestão da TI", "periodo": 3,
            "distancia_m": 4.5, "registrado_em": "2026-11-11T14:05:00+00:00", "alertas": "VPN/proxy detectado" if i == 2 else None}


def comp(i):
    return {"id": i, "registrado_em": f"2026-11-{max(1, 20 - i):02d}T14:05:00+00:00", "titulo": EVENTO["titulo"] if i == 1 else f"Evento {i}", "tipo": "aula",
            "local_tipo": "dentro", "disciplina": "ED2", "periodo": 3, "professor": "Marcos Lima"}


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
        return js([{"id": 1, "nome": "Gestão da Tecnologia da Informação"}, {"id": 2, "nome": "Direito"}])
    if p == "/api/auth/login":
        return js({"token": "t", "usuario": PROF if route.request.post_data and '"professor"' in route.request.post_data else ALUNO})
    if p == "/api/admin/login":
        return js({"token": "t-adm", "usuario": ADMIN})
    # ---- aluno ----
    if p == "/api/aulas" and route.request.method == "POST":
        estado["post_aula"] = json.loads(route.request.post_data or "{}")
        return js(EVENTO)
    if p == "/api/aulas/ativas":
        if estado["ativas"] == "500":
            return js({"detail": "Erro interno. Tente de novo em instantes."}, 500)
        if estado["ativas"] == "abort":
            return route.abort()
        if estado["ativas"] == "vazio":
            return js([])
        return js([EVENTO_ALUNO, dict(EVENTO_ALUNO, id=12, titulo="Palestra de Segurança Digital", tipo="palestra", local_tipo="fora", ja_registrou=True, curso=None, periodo=None, disciplina=None)])
    if p == "/api/me":
        return js({"nome": ALUNO["nome"], "matricula": ALUNO["matricula"], "email": ALUNO["email"], "curso": ALUNO["curso"], "periodo": 3})
    if p == "/api/aluno/presencas":
        return js([comp(1), comp(2), comp(3)])
    if p == "/api/aluno/notas":
        return js([{"id": 5, "presenca_id": 1, "titulo": EVENTO["titulo"], "tipo": "aula", "disciplina": "ED2", "evento_em": "2026-11-19T14:05:00+00:00",
                    "texto": "Pilhas e filas. Revisar a lista de exercícios 4.", "atualizado_em": "2026-11-19T15:00:00+00:00"}])
    # ---- professor ----
    if p == "/api/professor/aulas":
        if estado["hist_vazio"]:
            return js([])
        return js([EVENTO if estado["prof_com_ativa"] else dict(EVENTO, ativa=False, encerrada_em="2026-11-11T15:00:00+00:00"),
                   dict(EVENTO, id=9, ativa=False, titulo="Cálculo II", encerrada_em="2026-11-10T15:00:00+00:00", total=31)])
    if p == "/api/aulas/11/presencas":
        return js({"ativa": True, "total": 3, "limite": 2000, "presencas": [pres(1), pres(2), pres(3)]})
    if p == "/api/aulas/11/tentativas":
        return js({"itens": [{"id": 1, "nome": "Carlos", "matricula": "GTI9", "curso": "Gestão da TI", "motivo": "fora_raio", "detalhe": "A 200 m da sala", "distancia_m": 200,
                              "quantidade": 3, "ultima_em": "2026-11-11T14:06:00+00:00", "registrou": False}]})
    if p == "/api/aulas/11/ausentes":
        return js({"disponivel": True, "total": 2, "limite": 500, "itens": [{"nome": "Daniela Prado", "matricula": "GTI7", "email": "d@alunos.unicerrado.edu.br", "periodo": 3}]})
    # ---- coordenação ----
    if p == "/api/admin/resumo":
        return js(RESUMO)
    if p == "/api/admin/cursos":
        return js([{"id": 1, "nome": "Gestão da Tecnologia da Informação", "departamento": "Exatas", "alunos": 120, "ativo": True},
                   {"id": 2, "nome": "Direito", "departamento": None, "alunos": 192, "ativo": False}])
    if p == "/api/admin/professores":
        return js([{"id": 2, "nome": "Marcos Lima", "email": "marcos@unicerrado.edu.br", "registro_funcional": "DOC-1", "departamento": "Exatas", "ativo": True, "pendente": False,
                    "criado_em": "2026-08-01T10:00:00+00:00", "aulas": 12, "admin": False, "fixo_env": False},
                   {"id": 3, "nome": "Patrícia Nogueira Fernandes de Oliveira", "email": "patricia.nogueira.fernandes@unicerrado.edu.br", "registro_funcional": "DOC-2026-08", "departamento": "Ciências Exatas e Tecnologia",
                    "ativo": True, "pendente": True, "criado_em": "2026-11-10T10:00:00+00:00", "aulas": 0, "admin": False, "fixo_env": False}])
    if p == "/api/admin/alunos":
        return js({"total": 2, "itens": [{"id": 1, "nome": ALUNO["nome"], "email": ALUNO["email"], "matricula": "GTI202610405", "curso_id": 1, "periodo": 3, "status": "ativo"},
                                         {"id": 2, "nome": "Bruno", "email": "bruno@alunos.unicerrado.edu.br", "matricula": None, "curso_id": None, "periodo": None, "status": "inativo"}]})
    if p == "/api/admin/matriculas":
        return js({"obrigatoria": True, "total": 2, "total_geral": 400, "utilizadas": 312, "itens": [{"id": 1, "matricula": "GTI202610405", "curso": "Gestão da TI", "utilizada": True},
                                                                                                       {"id": 2, "matricula": "GTI202610406", "curso": None, "utilizada": False}]})
    if p == "/api/admin/pedidos-senha":
        return js([{"id": 1, "data_hora": "2026-11-11T14:00:00+00:00", "perfil": "aluno", "info": "GTI202610405; Ana; ana@alunos.unicerrado.edu.br", "atendido": False, "usuario_id": 1}])
    if p == "/api/admin/aulas":
        return js([EVENTO | {"professor": "Marcos Lima"}])
    if p == "/api/admin/alertas":
        return js([{"id": 7, "aluno": "Aluno Com Nome Bem Comprido da Silva", "email": "aluno.com.nome.bem.comprido@alunos.unicerrado.edu.br", "aula": EVENTO["titulo"], "alertas": "VPN/proxy detectado; GPS suspeito",
                    "ip": "2804:14d:abcd:1234:5678:9abc:def0:1234", "distancia_m": 33.3, "registrado_em": "2026-11-11T14:07:00+00:00"}])
    if p == "/api/admin/auditoria":
        return js([{"id": 4, "usuario_id": 9, "usuario": "Coord Geral", "acao": "exportar_chamada", "detalhe": "perfil=admin; aula=11; titulo=" + EVENTO["titulo"], "ip": "177.1.1.1",
                    "data_hora": "2026-11-11T14:09:00+00:00"}])
    if p == "/api/meu-ip":
        return js({"ip_detectado": "177.1.1.1", "x_forwarded_for": None, "proxy_hops": 1, "vpn_modo": "avisar", "verificacao_ativa": True, "ip_e_vpn": False})
    return js({"detail": "mock: " + p}, 404)


falhas, avisos = [], []


def conf(ok, txt):
    print(("OK   " if ok else "FALHA"), txt)
    if not ok:
        falhas.append(txt)


VIEWPORTS = [("celular-pequeno 320", 320, 568), ("celular 375", 375, 812), ("tablet 768", 768, 1024), ("notebook 1366", 1366, 768), ("TV 1920", 1920, 1080)]

# ---- verificações dentro da página ----
JS_AUDITORIA = r"""
() => {
  const vw = window.innerWidth, saida = {};
  const visivel = el => { const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && parseFloat(cs.opacity) > 0.05 && !el.closest('[hidden]'); };
  saida.rolagem = document.documentElement.scrollWidth - vw;
  // elementos que passam da tela e não estão dentro de uma área com rolagem própria
  const emRolagem = el => { for (let e = el.parentElement; e && e !== document.body; e = e.parentElement) { const o = getComputedStyle(e).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden') return true; } return false; };
  saida.passam = [...document.querySelectorAll('body *')].filter(el => visivel(el) && !emRolagem(el) && !el.closest('svg, .fundo-tec, .geo, #mapa') && (el.getBoundingClientRect().right > vw + 1 || el.getBoundingClientRect().left < -1)
      && el.textContent.trim()).slice(0, 8).map(el => (el.id ? '#' + el.id : el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.split(' ')[0] : '')) + ' → ' + el.textContent.trim().slice(0, 30));
  // cor
  const cv = document.createElement('canvas').getContext('2d', {willReadFrequently: true});
  const rgba = c => { cv.clearRect(0, 0, 1, 1); cv.fillStyle = '#000'; cv.fillStyle = c; cv.fillRect(0, 0, 1, 1); const d = cv.getImageData(0, 0, 1, 1).data; return [d[0], d[1], d[2], d[3] / 255]; };
  const lum = ([r, g, b]) => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  const mistura = (f, b) => [0, 1, 2].map(i => f[i] * f[3] + b[i] * (1 - f[3]));
  const fundo = el => { const cam = []; let imagem = false;
    for (let e = el; e; e = e.parentElement) { const cs = getComputedStyle(e); if (cs.backgroundImage !== 'none') imagem = true; const c = rgba(cs.backgroundColor); if (c[3] > 0) cam.push(c); if (c[3] >= 1) break; }
    let base = [255, 255, 255]; for (const c of cam.reverse()) base = mistura(c, base); return {cor: base, imagem}; };
  const falhaCor = [], vistos = new Set();
  for (const el of document.querySelectorAll('body *')) {
    if (!visivel(el) || el.closest('svg') || el.disabled || el.closest('[disabled], [aria-disabled="true"]')) continue;
    const tem = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim().length > 1);
    if (!tem) continue;
    const cs = getComputedStyle(el), f = fundo(el);
    if (f.imagem) continue;                                   // texto sobre imagem/degradê: conferido por captura de tela
    let fg = rgba(cs.color); if (fg[3] < 1) fg = [...mistura(fg, f.cor), 1];
    const l1 = lum(fg), l2 = lum(f.cor), razao = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
    const px = parseFloat(cs.fontSize), forte = parseInt(cs.fontWeight) >= 700, grande = px >= 24 || (px >= 18.66 && forte);
    const minimo = grande ? 3 : 4.5;
    if (razao < minimo) { const k = el.className + '|' + cs.color + '|' + f.cor.join(','); if (!vistos.has(k)) { vistos.add(k); falhaCor.push(`${razao.toFixed(2)}:1 (mín. ${minimo}) "${el.textContent.trim().slice(0, 28)}" ${(el.id ? '#' + el.id : el.tagName.toLowerCase() + '.' + String(el.className).split(' ')[0])}`); } }
  }
  saida.contraste = falhaCor.slice(0, 12);
  // nomes acessíveis
  const nome = el => { const a = el.getAttribute('aria-label') || (el.getAttribute('aria-labelledby') && document.getElementById(el.getAttribute('aria-labelledby'))?.textContent) || '';
    if (a.trim()) return a.trim(); if (el.labels && el.labels.length) return [...el.labels].map(l => l.textContent).join(' ').trim();
    if (el.tagName === 'IMG') return (el.getAttribute('alt') || '').trim(); if (el.title) return el.title.trim(); if (el.placeholder && /INPUT|TEXTAREA/.test(el.tagName)) return el.placeholder.trim();
    return (el.innerText || el.textContent || '').trim(); };
  saida.semNome = [...document.querySelectorAll('button, a[href], input:not([type=hidden]), select, textarea, [role=button]')].filter(el => visivel(el) && !nome(el))
      .slice(0, 8).map(el => (el.id ? '#' + el.id : el.tagName.toLowerCase() + '.' + String(el.className).split(' ')[0] + (el.dataset.act ? '[' + el.dataset.act + ']' : '')));
  saida.imgSemAlt = [...document.querySelectorAll('img')].filter(el => visivel(el) && !el.hasAttribute('alt')).map(el => el.getAttribute('src')).slice(0, 5);
  // alvos de toque
  const alvos = [...document.querySelectorAll('button, a[href], input:not([type=hidden]):not([type=checkbox]):not([type=radio]), select, textarea')].filter(visivel);
  saida.alvosPequenos = alvos.map(el => { const r = el.getBoundingClientRect(); return {w: Math.round(r.width), h: Math.round(r.height), n: (nome(el) || el.tagName).slice(0, 24), c: String(el.className).split(' ')[0]}; })
      .filter(a => a.w < 24 || a.h < 24);
  saida.alvosMenor44 = alvos.map(el => { const r = el.getBoundingClientRect(); return {w: Math.round(r.width), h: Math.round(r.height), c: String(el.className).split(' ')[0] || el.tagName.toLowerCase()}; })
      .filter(a => a.h < 44 || a.w < 44).length;
  saida.totalAlvos = alvos.length;
  // títulos
  const hs = [...document.querySelectorAll('h1, h2, h3, h4')].filter(visivel).map(h => +h.tagName[1]);
  saida.titulosPulados = hs.some((n, i) => i > 0 && n - hs[i - 1] > 1);
  saida.h1 = hs.filter(n => n === 1).length;
  saida.fonte = parseFloat(getComputedStyle(document.body).fontSize);
  const cont = {}; document.querySelectorAll('[id]').forEach(e => { cont[e.id] = (cont[e.id] || 0) + 1; });
  saida.idsRepetidos = Object.keys(cont).filter(k => cont[k] > 1);
  return saida;
}
"""

JS_FOCO = r"""
() => { const el = document.activeElement; if (!el || el === document.body) return null; const cs = getComputedStyle(el);
  const contorno = cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0 && cs.outlineColor !== 'rgba(0, 0, 0, 0)';
  const sombra = cs.boxShadow !== 'none'; const borda = el.matches(':focus-visible');
  return {tag: el.tagName.toLowerCase() + (el.id ? '#' + el.id : ''), ok: contorno || sombra, fv: borda}; }
"""


def auditar(pg, tela, so_celular=False):
    """Roda a auditoria em todos os tamanhos de tela para a tela atual. Devolve nada; registra com conf()."""
    for nome_vp, w, h in VIEWPORTS:
        pg.set_viewport_size({"width": w, "height": h})
        pg.wait_for_timeout(250)
        r = pg.evaluate(JS_AUDITORIA)
        rot = f"{tela} @ {nome_vp}"
        conf(r["rolagem"] <= 1, f"CT-901 | {rot}: a página não tem rolagem lateral" + ("" if r["rolagem"] <= 1 else f" (passa {r['rolagem']} px)"))
        conf(not r["passam"], f"CT-902 | {rot}: nenhum elemento passa da borda da tela" + ("" if not r["passam"] else f" → {r['passam']}"))
        conf(not r["contraste"], f"CT-903 | {rot}: contraste de cor do texto no mínimo WCAG AA" + ("" if not r["contraste"] else f" → {r['contraste']}"))
        conf(not r["semNome"], f"CT-904 | {rot}: todo botão, link e campo tem nome acessível" + ("" if not r["semNome"] else f" → {r['semNome']}"))
        conf(not r["imgSemAlt"], f"CT-905 | {rot}: toda imagem tem atributo alt" + ("" if not r["imgSemAlt"] else f" → {r['imgSemAlt']}"))
        conf(not r["alvosPequenos"], f"CT-906 | {rot}: nenhum alvo de toque menor que 24×24 px (WCAG 2.2)" + ("" if not r["alvosPequenos"] else f" → {r['alvosPequenos'][:5]}"))
        if r["alvosMenor44"] and w <= 375:
            avisos.append(f"{rot}: {r['alvosMenor44']} de {r['totalAlvos']} alvos de toque com menos de 44 px")
        conf(not r["idsRepetidos"], f"CT-908 | {rot}: nenhum id repetido na página" + ("" if not r["idsRepetidos"] else f" → {r['idsRepetidos']}"))
        if r["titulosPulados"]:
            avisos.append(f"{rot}: ordem de títulos pula nível")
        if w >= 1920:
            conf(r["fonte"] >= 16, f"CT-907 | {rot}: texto base legível na TV (fonte {r['fonte']:.0f} px)")
        if SHOTS and w in (375, 1920, 768):
            Path(SHOTS).mkdir(parents=True, exist_ok=True)
            pg.screenshot(path=f"{SHOTS}/{re.sub(r'[^a-z0-9]+', '-', tela.lower())}-{w}.png", full_page=True)


def novo(pw, **kw):
    b = pw.chromium.launch()
    c = b.new_context(viewport={"width": 375, "height": 812}, accept_downloads=True, geolocation={"latitude": LAT, "longitude": LON}, permissions=["geolocation"], **kw)
    c.route("**/*", handler)
    pg = c.new_page()
    erros, dialogos, consoles = [], [], []
    pg.on("pageerror", lambda e: erros.append(str(e)))
    pg.on("console", lambda m: consoles.append(m.text) if m.type == "error" and "localhost" in (m.location.get("url") or "") else None)
    pg.on("dialog", lambda d: (dialogos.append(d.message), d.dismiss()))
    return b, pg, erros, dialogos, consoles


def entrar(pg, quem, vista):
    pg.goto("http://localhost:8765/")
    pg.wait_for_selector("#v-auth:not([hidden])")
    pg.click(f'.tab[data-p="{quem}"]')
    pg.fill("#email", {"aluno": ALUNO, "professor": PROF, "admin": ADMIN}[quem]["email"])
    pg.fill("#senha", "senha12345")
    pg.click("#btn-auth")
    pg.wait_for_selector(vista + ":not([hidden])")


with sync_playwright() as pw:
    # =========================== entrada (login e cadastro) ===========================
    zerar()
    b, pg, erros, dialogos, consoles = novo(pw)
    pg.goto("http://localhost:8765/")
    pg.wait_for_selector("#v-auth:not([hidden])")
    meta = pg.evaluate("({lang: document.documentElement.lang, vp: document.querySelector('meta[name=viewport]')?.content || '', titulo: document.title, desc: !!document.querySelector('meta[name=description]')})")
    conf(meta["lang"].lower().startswith("pt"), f"CT-910 | o idioma da página é português (lang='{meta['lang']}')")
    conf("user-scalable=no" not in meta["vp"].replace(" ", "") and "maximum-scale=1" not in meta["vp"].replace(" ", ""), f"CT-911 | o zoom por gesto não é bloqueado ('{meta['vp']}')")
    conf(len(meta["titulo"].strip()) >= 3, f"CT-912 | a aba do navegador tem título ('{meta['titulo']}')")
    auditar(pg, "Entrada: login do aluno")
    pg.click("#toggle")
    pg.wait_for_selector("#f-mat:not([hidden])")
    auditar(pg, "Entrada: cadastro do aluno")
    pg.click('.tab[data-p="professor"]')
    pg.wait_for_selector("#f-registro:not([hidden])")
    auditar(pg, "Entrada: cadastro do professor")
    pg.click('.tab[data-p="admin"]')
    pg.wait_for_timeout(300)
    auditar(pg, "Entrada: coordenação")
    conf(not erros and not consoles and not dialogos, f"CT-913 | entrada: nenhum erro de JavaScript ou de console {erros + consoles}")

    # teclado: Tab percorre o formulário, o foco é visível e Enter envia
    pg.goto("http://localhost:8765/")
    pg.wait_for_selector("#v-auth:not([hidden])")
    pg.set_viewport_size({"width": 1366, "height": 768})
    focos = []
    for _ in range(14):
        pg.keyboard.press("Tab")
        f = pg.evaluate(JS_FOCO)
        if f:
            focos.append(f)
    sem_anel = [f["tag"] for f in focos if not f["ok"]]
    conf(len(focos) >= 8 and not sem_anel, f"CT-914 | teclado: o foco passa por {len(focos)} controles e todos mostram anel de foco visível" + ("" if not sem_anel else f" → sem anel: {sem_anel}"))
    pg.fill("#email", ALUNO["email"])
    pg.fill("#senha", "senha12345")
    pg.press("#senha", "Enter")
    pg.wait_for_selector("#v-aluno:not([hidden])", timeout=5000)
    conf(True, "CT-915 | teclado: Enter no campo de senha faz o login")
    b.close()

    # =========================== aluno ===========================
    zerar()
    b, pg, erros, dialogos, consoles = novo(pw)
    entrar(pg, "aluno", "#v-aluno")
    pg.wait_for_selector("#comprov article")
    pg.wait_for_selector("#aulas .card, #aulas article, #aulas button")
    auditar(pg, "Aluno: chamadas abertas, comprovantes e anotações")
    conf(not erros and not consoles and not dialogos, f"CT-920 | aluno: nenhum erro de JavaScript ou de console {erros + consoles}")

    # janela "Mostrar ao professor"
    pg.set_viewport_size({"width": 375, "height": 812})
    pg.click('#comprov button[data-m="0"]')
    pg.wait_for_selector("#mostrar:not([hidden])")
    dlg = pg.evaluate("(() => { const m = document.getElementById('mostrar'); return {role: m.getAttribute('role'), modal: m.getAttribute('aria-modal'), rotulo: m.getAttribute('aria-label') || m.getAttribute('aria-labelledby')}; })()")
    conf(dlg["role"] == "dialog" and dlg["modal"] == "true", f"CT-921 | a janela 'Mostrar ao professor' é um diálogo acessível (role={dlg['role']}, aria-modal={dlg['modal']})")
    foco_dentro = pg.evaluate("document.getElementById('mostrar').contains(document.activeElement)")
    conf(foco_dentro, "CT-922 | ao abrir a janela, o foco do teclado vai para dentro dela")
    auditar(pg, "Aluno: comprovante em tela cheia")
    # teclado dentro da janela: Tab e Shift+Tab giram entre os botões e nunca vazam para a página de trás
    dentro = []
    for _ in range(5):
        pg.keyboard.press("Tab")
        dentro.append(pg.evaluate("document.getElementById('mostrar').contains(document.activeElement) ? document.activeElement.id : 'FORA'"))
    conf("FORA" not in dentro and "m-pdf" in dentro and "m-fechar" in dentro, f"CT-925 | Tab gira só entre os botões da janela {dentro}")
    dentro = []
    for _ in range(5):
        pg.keyboard.press("Shift+Tab")
        dentro.append(pg.evaluate("document.getElementById('mostrar').contains(document.activeElement) ? document.activeElement.id : 'FORA'"))
    conf("FORA" not in dentro and "m-pdf" in dentro and "m-fechar" in dentro, f"CT-925b | Shift+Tab gira só entre os botões da janela {dentro}")
    pg.keyboard.press("Escape")
    conf(pg.is_hidden("#mostrar") and pg.evaluate("document.body.style.overflow") == "", "CT-923 | Esc fecha a janela e devolve a rolagem da página")
    voltou = pg.evaluate("document.activeElement && document.activeElement.matches('button[data-m=\"0\"]')")
    conf(voltou, "CT-926 | ao fechar, o foco volta ao botão 'Mostrar ao professor' que abriu a janela")
    pg.keyboard.press("Escape")
    conf(pg.evaluate("document.activeElement && document.activeElement.matches('button[data-m=\"0\"]')") and not erros, "CT-927 | Esc com a janela fechada não faz nada (nem erro, nem perde o foco)")
    pg.click('#comprov button[data-m="0"]')
    pg.wait_for_selector("#mostrar:not([hidden])")
    pg.click("#m-fechar")
    conf(pg.is_hidden("#mostrar") and pg.evaluate("document.activeElement && document.activeElement.matches('button[data-m=\"0\"]')"), "CT-928 | o botão Fechar também devolve o foco")
    b.close()

    # erros e lista vazia
    for modo, esperado_re, nome in (("vazio", r"(nenhuma|não há|sem).*(chamada|aula)", "nenhuma chamada aberta"), ("500", r"(erro|tente|instantes)", "erro 500 do servidor"), ("abort", r"(conex|internet|rede)", "sem conexão")):
        zerar()
        estado["ativas"] = modo
        b, pg, erros, dialogos, consoles = novo(pw)
        entrar(pg, "aluno", "#v-aluno")
        pg.wait_for_timeout(1200)
        texto = (pg.inner_text("#aulas") + " " + pg.inner_text("#msg-aluno")).lower()
        conf(re.search(esperado_re, texto) is not None and not erros, f"CT-924 | aluno, {nome}: a tela explica o que houve em vez de ficar em branco → '{texto.strip()[:70]}'")
        if modo == "vazio":
            auditar(pg, "Aluno: sem chamada aberta")
        b.close()

    # =========================== professor ===========================
    zerar()
    b, pg, erros, dialogos, consoles = novo(pw)
    entrar(pg, "professor", "#v-prof")
    pg.wait_for_selector("#tbody tr")
    auditar(pg, "Professor: formulário, painel da chamada e histórico")
    for aba in ("ausentes", "tentativas"):
        pg.click(f'#p-tabs button[data-pt="{aba}"]')
        pg.wait_for_timeout(400)
        auditar(pg, f"Professor: aba {aba}")
    conf(not erros and not consoles and not dialogos, f"CT-930 | professor: nenhum erro de JavaScript ou de console {erros + consoles}")
    # o código de validação digitado pelo professor chega à API (e não o campo escondido do cadastro)
    pg.set_viewport_size({"width": 1366, "height": 768})
    campo = pg.get_by_label("Código de validação do PDF")
    pg.fill("#titulo", "Redes de Computadores")
    pg.fill("#lat", "-18.4157")
    pg.fill("#lon", "-49.2183")
    campo.fill("ab12cd")
    pg.click('#form-aula button[type="submit"]')
    pg.wait_for_function("true")
    for _ in range(40):
        if "post_aula" in estado:
            break
        pg.wait_for_timeout(100)
    conf(estado.get("post_aula", {}).get("codigo_validacao") == "ab12cd", f"CT-931 | o código de validação digitado pelo professor vai para a API → {estado.get('post_aula', {}).get('codigo_validacao')!r}")
    conf(campo.input_value() == "", "CT-932 | depois de abrir a chamada, o campo do código é limpo")
    conf(pg.input_value("#codigo") == "" if pg.locator("#codigo").count() else True, "CT-933 | o campo de código do cadastro (escondido) continua intocado")
    estado.pop("post_aula", None)
    pg.fill("#titulo", "Banco de Dados")
    pg.click('#form-aula button[type="submit"]')
    for _ in range(40):
        if "post_aula" in estado:
            break
        pg.wait_for_timeout(100)
    conf(estado.get("post_aula", {}).get("codigo_validacao") is None, f"CT-934 | com o campo vazio, o código vai como nulo (o servidor gera um) → {estado.get('post_aula', {}).get('codigo_validacao')!r}")
    b.close()

    zerar()
    estado["prof_com_ativa"], estado["hist_vazio"] = False, True
    b, pg, erros, dialogos, consoles = novo(pw)
    entrar(pg, "professor", "#v-prof")
    pg.wait_for_timeout(800)
    auditar(pg, "Professor: sem nenhum evento ainda")
    b.close()

    # =========================== coordenação ===========================
    zerar()
    b, pg, erros, dialogos, consoles = novo(pw)
    entrar(pg, "admin", "#v-admin")
    pg.wait_for_function("document.querySelector('#adm-corpo').innerText.length > 40")
    for aba, marca in (("resumo", "Alunos"), ("cursos", "Direito"), ("professores", "Patrícia"), ("alunos", "Bruno"), ("matriculas", "GTI202610406"), ("pedidos", "Definir senha"),
                       ("aulas", "Cálculo") if False else ("aulas", "Algoritmos"), ("alertas", "VPN"), ("auditoria", "Exportou")):
        pg.click(f'#adm-tabs button[data-t="{aba}"]')
        pg.wait_for_function("t => document.querySelector('#adm-corpo').innerText.includes(t)", arg=marca)
        auditar(pg, f"Coordenação: aba {aba}")
    conf(not erros and not consoles and not dialogos, f"CT-940 | coordenação: nenhum erro de JavaScript ou de console {erros + consoles}")
    b.close()

    # =========================== preferências do sistema ===========================
    zerar()
    b, pg, erros, dialogos, consoles = novo(pw, reduced_motion="reduce")
    entrar(pg, "aluno", "#v-aluno")
    pg.wait_for_selector("#comprov article")
    pg.wait_for_timeout(600)
    n = pg.evaluate("document.getAnimations().filter(a => a.playState === 'running' && a.effect && a.effect.getTiming().iterations === Infinity).length")
    conf(n == 0, f"CT-950 | 'reduzir movimento' ligado: nenhuma animação infinita rodando (encontradas: {n})")
    b.close()

    zerar()
    b, pg, erros, dialogos, consoles = novo(pw, color_scheme="dark")
    entrar(pg, "aluno", "#v-aluno")
    pg.wait_for_selector("#comprov article")
    auditar(pg, "Aluno com o sistema em modo escuro")
    b.close()

print()
if avisos:
    print("AVISOS (não reprovam):")
    for a in sorted(set(avisos)):
        print("  -", a)
print("RESULTADO:", "TUDO OK" if not falhas else f"{len(falhas)} FALHA(S)")
sys.exit(1 if falhas else 0)
