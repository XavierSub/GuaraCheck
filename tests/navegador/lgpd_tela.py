"""LGPD no navegador (CT-2101 a CT-2160).
Aluno: o cartão "Li e aceito" aparece enquanto falta o aceite; SEM o aceite o app nem liga o GPS (nem pede a localização ao navegador) e o
botão da chamada fica travado com a explicação; aceitar manda PUT /me/privacidade, liga o GPS e libera o botão; quem já aceitou não vê
o cartão nem um "pisca" da mensagem de bloqueio; servidor antigo (sem os campos), instituição que não exige o aceite e falha do /me
continuam registrando como antes; a seção "Meus dados e privacidade" baixa o JSON (com o token), retira o aceite (com confirmação),
envia pedidos (a exclusão pede confirmação) e lista os pedidos com a resposta da coordenação; um 403 de aceite vindo do servidor traz o
cartão de volta; o rodapé tem o link do aviso, já na tela de login.
Coordenação: a aba LGPD mostra aceites, avisos de configuração, pedidos em aberto (com prazo vencido), a limpeza (com confirmação) e
atende/recusa pedidos (a recusa exige motivo; a exclusão pede confirmação); textos vindos do servidor aparecem como TEXTO.
A API é simulada (imita o FastAPI); o servidor real não roda aqui.
Uso:  pip install playwright && playwright install chromium && python tests/navegador/lgpd_tela.py
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
X1 = '<img src=x onerror="window.__xss=(window.__xss||0)+1">'
ALUNO = {"id": 1, "nome": "Ana Souza", "email": "ana@alunos.unicerrado.edu.br", "perfil": "aluno", "curso": "Gestão da TI", "periodo": 3, "matricula": "GTI00001"}
ADMIN = {"id": 9, "nome": "Coord Geral", "email": "coord@unicerrado.edu.br", "perfil": "admin", "curso": None}
LAT, LON = -18.4157, -49.2183
DENTRO = {"lat": LAT, "lon": LON}
EVENTO = {"id": 11, "titulo": "Algoritmos", "latitude": LAT, "longitude": LON, "raio_metros": 50, "ativa": True, "tipo": "aula",
          "local_tipo": "dentro", "local_descricao": "Sala 12", "curso_id": 1, "curso": "Gestão da TI", "periodo": 3, "disciplina": "ED",
          "professor": "Marcos Lima", "aberta_em": "2026-11-11T14:00:00+00:00", "encerrada_em": None, "total": 0, "codigo_validacao": "K7Q2",
          "qr_rotativo": False, "exige_saida": False, "permanencia_min": None, "carga_horas": None, "ja_registrou": False}
RESUMO_ADM = {"senha_provisoria": False, "alunos": 300, "professores": 4, "aulas": 30, "presencas": 900, "aulas_ativas": 0, "alunos_sem_curso": 0, "alunos_inativos": 0,
              "professores_pendentes": 0, "matriculas_autorizadas": 300, "matriculas_utilizadas": 280, "matricula_obrigatoria": True, "verificacao_email": True,
              "email_configurado": True, "pedidos_senha_pendentes": 0, "por_curso": []}
estado = {}
dialogos = []
RODAPE_LOGIN = [""]


def painel_padrao():
    return {
        "config": {"versao": "2026-11", "controlador": "UniCerrado - Centro Universitário de Goiatuba", "encarregado_nome": "Maria Dados",
                   "encarregado_email": "dpo@unicerrado.edu.br", "exigir_aceite": True, "retencao_dias": 180, "retencao_logs_dias": 180, "prazo_dias": 15},
        "aceites": {"alunos": 120, "aceitaram": 87},
        "retencao": {"simulado": True, "presencas": 40, "tentativas": 5, "logs": 12, "corte_presencas": None, "corte_logs": None},
        "pedidos": [
            {"id": 31, "tipo": "exclusao", "mensagem": "Quero sair do sistema", "status": "aberto", "criado_em": "2026-10-01T12:00:00+00:00",
             "resolvido_em": None, "resposta": None, "dias_aberto": 20, "atrasado": True,
             "aluno": {"id": 1, "nome": X1, "email": "ana@alunos.unicerrado.edu.br", "matricula": "GTI00001"}},
            {"id": 32, "tipo": "correcao", "mensagem": "Meu período está errado", "status": "aberto", "criado_em": "2026-11-15T12:00:00+00:00",
             "resolvido_em": None, "resposta": None, "dias_aberto": 2, "atrasado": False,
             "aluno": {"id": 2, "nome": "Bruno Lima", "email": "bruno@alunos.unicerrado.edu.br", "matricula": None}}],
        "respondidos": [
            {"id": 20, "tipo": "informacao", "mensagem": None, "status": "atendido", "criado_em": "2026-09-01T12:00:00+00:00",
             "resolvido_em": "2026-09-03T12:00:00+00:00", "resposta": "Enviamos por e-mail.", "aluno": {"id": 3, "nome": "Carla Dias", "email": "c@alunos.unicerrado.edu.br", "matricula": "X1"}},
            {"id": 21, "tipo": "exclusao", "mensagem": None, "status": "atendido", "criado_em": "2026-09-05T12:00:00+00:00",
             "resolvido_em": "2026-09-06T12:00:00+00:00", "resposta": "Sua conta e os dados dela foram excluídos.", "aluno": None}],
    }


def reiniciar(**extra):
    estado.clear()
    estado.update(exigida=True, aceite=False, versao="2026-11", chaves=True, me_erro=False, puts=[], pedidos_post=[], pedidos=[], erro_pedido=None,
                  baixou=[], auth=[], painel=painel_padrao(), admin_puts=[], retencao_posts=[], erro_admin=None, erro_checkin=None, checkins=[],
                  auditoria=[], aceitar=True)
    estado.update(extra)
    dialogos.clear()


def handler(route):
    u = route.request.url
    if "localhost:8765" not in u:
        return route.abort()
    p = u.split("localhost:8765", 1)[1].split("?")[0]
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
        return js({"token": "t-aluno", "usuario": ALUNO})
    if p == "/api/admin/login":
        return js({"token": "t-adm", "usuario": ADMIN})
    if p == "/api/admin/resumo":
        return js(RESUMO_ADM)
    if p == "/api/admin/lgpd":
        return js(estado["painel"])
    if p == "/api/admin/lgpd/retencao" and met == "POST":
        corpo = json.loads(route.request.post_data)
        estado["retencao_posts"].append(corpo)
        estado["painel"]["retencao"] = dict(estado["painel"]["retencao"], presencas=0, tentativas=0, logs=0)
        return js({"simulado": False, "presencas": 40, "tentativas": 5, "logs": 12, "corte_presencas": None, "corte_logs": None})
    if p.startswith("/api/admin/lgpd/pedidos/") and met == "PUT":
        corpo = json.loads(route.request.post_data)
        pid = int(p.rsplit("/", 1)[1])
        estado["admin_puts"].append((pid, corpo))
        if estado["erro_admin"]:
            return js({"detail": estado["erro_admin"]}, 409)
        estado["painel"]["pedidos"] = [x for x in estado["painel"]["pedidos"] if x["id"] != pid]
        return js({"ok": True, "status": "atendido" if corpo["acao"] == "atender" else "recusado"})
    if p == "/api/admin/auditoria":
        return js(estado["auditoria"])
    if p == "/api/aulas/ativas":
        return js([dict(EVENTO)])
    if p == "/api/me" and met == "GET":
        if estado["me_erro"]:
            return js({"detail": "Falha no servidor."}, 500)
        d = {"nome": ALUNO["nome"], "matricula": ALUNO["matricula"], "email": ALUNO["email"], "curso": ALUNO["curso"], "periodo": 3}
        if estado["chaves"]:
            d.update(aceite_privacidade=estado["aceite"], privacidade_exigida=estado["exigida"], privacidade_versao=estado["versao"])
        return js(d)
    if p == "/api/me/privacidade" and met == "PUT":
        corpo = json.loads(route.request.post_data)
        estado["puts"].append(corpo)
        estado["aceite"] = corpo["aceitar"]
        return js({"aceite_privacidade": corpo["aceitar"], "versao": estado["versao"]})
    if p == "/api/me/dados":
        estado["auth"].append(route.request.headers.get("authorization"))
        return route.fulfill(status=200, content_type="application/json", body=json.dumps({"aluno": {"nome": ALUNO["nome"]}}),
                             headers={"Content-Disposition": 'attachment; filename="meus-dados-guaracheck.json"'})
    if p == "/api/me/pedidos" and met == "GET":
        return js({"prazo_dias": 15, "itens": estado["pedidos"]})
    if p == "/api/me/pedidos" and met == "POST":
        corpo = json.loads(route.request.post_data)
        estado["pedidos_post"].append(corpo)
        if estado["erro_pedido"]:
            return js({"detail": estado["erro_pedido"]}, 409)
        estado["pedidos"].insert(0, {"id": 50 + len(estado["pedidos_post"]), "tipo": corpo["tipo"], "mensagem": corpo["mensagem"], "status": "aberto",
                                     "criado_em": "2026-11-17T12:00:00+00:00", "resolvido_em": None, "resposta": None})
        return js({"ok": True, "prazo_dias": 15})
    if p == "/api/aluno/presencas":
        return js([])
    if p == "/api/aluno/notas":
        return js([])
    if p == "/api/aulas/11/presenca" and met == "POST":
        estado["checkins"].append(json.loads(route.request.post_data))
        if estado["erro_checkin"]:
            return js({"detail": estado["erro_checkin"]}, 403)
        return js({"ok": True, "aula": "Algoritmos", "distancia_m": 3, "alertas": []})
    return js({"detail": "mock: " + p}, 404)


falhas = []


def conf(ok, txt):
    print(("OK   " if ok else "FALHA"), txt)
    if not ok:
        falhas.append(txt)


def abrir(pw, quem="aluno", largura=390, geo=DENTRO):
    b = pw.chromium.launch()
    c = b.new_context(viewport={"width": largura, "height": 900}, is_mobile=largura < 500, accept_downloads=True)
    c.add_init_script(gps_falso(geo["lat"], geo["lon"]))
    c.add_init_script("""
      (() => { const g = navigator.geolocation, w = g.watchPosition.bind(g); window.__pedidosGps = 0;
        g.watchPosition = (...a) => { window.__pedidosGps++; return w(...a); }; })();""")
    c.add_init_script("""
      window.__piscou = false;
      document.addEventListener('DOMContentLoaded', () => {
        new MutationObserver(() => { const a = document.querySelector('#aulas'); if (a && a.textContent.includes('Aceite o aviso de privacidade')) window.__piscou = true; })
          .observe(document.body, {childList: true, subtree: true, characterData: true});
      });""")
    c.route("**/*", handler)
    pg = c.new_page()
    erros = []
    pg.on("pageerror", lambda e: erros.append(str(e)))

    def dlg(d):
        dialogos.append(d.message)
        d.accept() if estado["aceitar"] else d.dismiss()
    pg.on("dialog", dlg)
    pg.goto("http://localhost:8765/")
    pg.wait_for_selector("#v-auth:not([hidden])")
    RODAPE_LOGIN[:] = [pg.inner_html("#rodape-priv") if pg.locator("#rodape-priv").count() else ""]
    pg.click(f'.tab[data-p="{quem}"]')
    pg.fill("#email", (ADMIN if quem == "admin" else ALUNO)["email"])
    pg.fill("#senha", "senha12345")
    pg.click("#btn-auth")
    pg.wait_for_selector("#v-admin:not([hidden])" if quem == "admin" else "#v-aluno:not([hidden])")
    if quem == "aluno":
        pg.wait_for_selector("#aulas article")
        pg.wait_for_function("document.querySelector('#lgpd-estado').textContent !== ''")
    return b, c, pg, erros


def botao(pg):
    return pg.locator("#aulas button[data-id]")


def espera_habilitado(pg):
    pg.wait_for_function("document.querySelector('#aulas button[data-id]') && !document.querySelector('#aulas button[data-id]').disabled", timeout=8000)


with sync_playwright() as pw:
    # =========================== aluno que ainda não aceitou ===========================
    reiniciar(exigida=True, aceite=False)
    b, c, pg, erros = abrir(pw)
    conf(pg.locator("#rodape-priv a[href='/privacidade'][target='_blank']").count() == 1 and "noopener" in (pg.get_attribute("#rodape-priv a", "rel") or ""),
         "rodapé: link 'Aviso de privacidade' para /privacidade (nova aba, noopener), visível já na tela de login")
    conf('href="/privacidade"' in RODAPE_LOGIN[0], "o link do aviso existe antes do login")
    conf(pg.is_visible("#lgpd-aceite"), "sem aceite: o cartão do aviso de privacidade aparece")
    t = pg.inner_text("#lgpd-aceite")
    conf("só é enviada ao servidor quando você toca em Registrar" in t and "apagados depois de um tempo" in t, "o cartão explica: posição só vai ao servidor no toque e dados detalhados são apagados")
    conf(pg.get_attribute("#lgpd-aceite a", "href") == "/privacidade" and pg.get_attribute("#lgpd-aceite a", "target") == "_blank" and "noopener" in (pg.get_attribute("#lgpd-aceite a", "rel") or ""),
         "o cartão linka o aviso completo em nova aba (rel=noopener)")
    pg.wait_for_timeout(1500)
    conf(pg.evaluate("window.__gps.ouvindo") == 0 and pg.evaluate("window.__pedidosGps") == 0,
         "SEM aceite o GPS NÃO é ligado: o app nem chega a pedir a localização ao navegador (pedidos: %s)" % pg.evaluate("window.__pedidosGps"))
    conf(botao(pg).is_disabled() and botao(pg).inner_text().strip() == "Aceite o aviso de privacidade", "SEM aceite o botão da chamada está travado: 'Aceite o aviso de privacidade'")
    conf("aceite o aviso de privacidade" in pg.inner_text("#aulas .zona").lower(), "a linha sob o botão explica o que falta")
    conf(not estado["checkins"], "nada foi enviado ao servidor")

    pg.click("#lgpd-aceitar")
    pg.wait_for_function("document.querySelector('#msg-aluno').textContent.includes('Aviso aceito')")
    conf(estado["puts"] == [{"aceitar": True}], "'Li e aceito' manda PUT /me/privacidade com aceitar=true: %r" % estado["puts"])
    conf(not pg.is_visible("#lgpd-aceite"), "depois de aceitar o cartão some")
    espera_habilitado(pg)
    conf(pg.evaluate("window.__gps.ouvindo") == 1, "depois do aceite o GPS liga (uma leitura aberta)")
    conf(botao(pg).inner_text().strip() == "Registrar Presença" and "Você está na área da aula" in pg.inner_text("#aulas .zona"), "dentro do raio o botão libera: 'Registrar Presença'")
    botao(pg).click()
    pg.wait_for_function("document.querySelector('#msg-aluno').textContent.includes('Presença registrada')", timeout=10000)
    conf(len(estado["checkins"]) == 1, "o registro segue o fluxo normal depois do aceite")

    # ---- seção "Meus dados e privacidade"
    conf(not pg.evaluate("document.querySelector('#lgpd-box').open"), "a seção 'Meus dados e privacidade' começa fechada")
    pg.click("#lgpd-box summary")
    pg.wait_for_function("document.querySelector('#lgpd-prazo').textContent === '15'")
    est = pg.inner_text("#lgpd-estado")
    conf("Você aceitou o aviso de privacidade (versão 2026-11)" in est and "o professor ainda pode lançar a sua presença" in est, "mostra que o aluno aceitou (com a versão) e o que acontece se retirar")
    conf(pg.inner_text("#lgpd-troca").strip() == "Retirar o aceite da localização" and pg.is_enabled("#lgpd-troca"), "botão 'Retirar o aceite da localização' habilitado")

    with pg.expect_download() as dl:
        pg.click("#lgpd-baixar")
    d = dl.value
    conf(d.suggested_filename == "meus-dados-guaracheck.json", "baixar meus dados: o arquivo se chama meus-dados-guaracheck.json (%s)" % d.suggested_filename)
    conf(json.load(open(d.path(), encoding="utf-8")) == {"aluno": {"nome": ALUNO["nome"]}}, "o conteúdo baixado é o que o servidor mandou")
    conf(estado["auth"][-1] == "Bearer t-aluno", "o download vai com o token do aluno (não é link aberto)")
    conf(pg.inner_text("#lgpd-baixar").strip() == "Baixar meus dados (JSON)" and pg.is_enabled("#lgpd-baixar"), "o botão volta ao normal depois do download")

    estado["aceitar"] = False
    estado["puts"].clear()
    pg.click("#lgpd-troca")
    conf(any("Retirar o aceite?" in m for m in dialogos) and estado["puts"] == [] and not pg.is_visible("#lgpd-aceite"), "retirar o aceite pede confirmação; cancelando, nada muda")
    estado["aceitar"] = True
    pg.click("#lgpd-troca")
    pg.wait_for_function("document.querySelector('#msg-lgpd').textContent.includes('Aceite retirado')")
    conf(estado["puts"] == [{"aceitar": False}], "confirmando, manda PUT com aceitar=false: %r" % estado["puts"])
    conf(pg.is_visible("#lgpd-aceite"), "o cartão volta")
    pg.wait_for_function("window.__gps.ouvindo === 0")
    conf(botao(pg).is_disabled() and botao(pg).inner_text().strip() == "Aceite o aviso de privacidade", "o botão da chamada trava de novo e o GPS desliga")
    conf(pg.inner_text("#lgpd-troca").strip() == "Aceitar o aviso de privacidade" and "ainda não aceitou" in pg.inner_text("#lgpd-estado"), "a seção passa a oferecer 'Aceitar o aviso de privacidade'")
    pg.click("#lgpd-troca")
    pg.wait_for_function("document.querySelector('#msg-lgpd').textContent.includes('Aviso aceito')")
    conf(estado["puts"][-1] == {"aceitar": True} and not pg.is_visible("#lgpd-aceite"), "aceitar pela seção também funciona")
    pg.wait_for_function("window.__gps.ouvindo === 1")

    # ---- pedidos
    pg.select_option("#lgpd-tipo", "exclusao")
    pg.fill("#lgpd-msg", "Quero sair do sistema")
    estado["aceitar"] = False
    pg.click("#lgpd-enviar")
    conf(any("exclusão da sua conta" in m and "não pode ser desfeito" in m for m in dialogos) and estado["pedidos_post"] == [], "pedido de EXCLUSÃO pede confirmação; cancelando, nada é enviado")
    estado["aceitar"] = True
    pg.click("#lgpd-enviar")
    pg.wait_for_function("document.querySelector('#msg-lgpd').textContent.includes('Pedido enviado')")
    conf(estado["pedidos_post"] == [{"tipo": "exclusao", "mensagem": "Quero sair do sistema"}], "envia POST /me/pedidos com tipo e mensagem: %r" % estado["pedidos_post"])
    conf("até 15 dias" in pg.inner_text("#msg-lgpd") and pg.input_value("#lgpd-msg") == "", "diz o prazo e limpa o campo")
    lista = pg.inner_text("#lgpd-pedidos")
    conf("Excluir a minha conta e os meus dados" in lista and "Em andamento" in lista and "Quero sair do sistema" in lista, "o pedido aparece na lista como 'Em andamento'")
    pg.select_option("#lgpd-tipo", "informacao")
    pg.click("#lgpd-enviar")
    pg.wait_for_function("document.querySelectorAll('#lgpd-pedidos .lgpd-ped').length === 2")
    conf(estado["pedidos_post"][-1] == {"tipo": "informacao", "mensagem": None}, "sem texto, a mensagem vai como null: %r" % estado["pedidos_post"][-1])
    estado["erro_pedido"] = "Você já tem um pedido desse tipo em andamento. Aguarde a resposta da coordenação."
    pg.click("#lgpd-enviar")
    pg.wait_for_function("document.querySelector('#msg-lgpd').textContent.includes('já tem um pedido')")
    conf("err" in pg.get_attribute("#msg-lgpd", "class") and pg.is_enabled("#lgpd-enviar"), "pedido repetido: erro do servidor em vermelho e o botão volta")
    conf(not erros, "aluno sem aceite: nenhum erro de JavaScript: %s" % erros)
    b.close()

    # ---- pedidos já respondidos, com texto malicioso
    reiniciar(exigida=True, aceite=True, pedidos=[
        {"id": 5, "tipo": "correcao", "mensagem": X1, "status": "recusado", "criado_em": "2026-10-01T12:00:00+00:00", "resolvido_em": "2026-10-03T12:00:00+00:00", "resposta": X1},
        {"id": 4, "tipo": "informacao", "mensagem": None, "status": "atendido", "criado_em": "2026-09-01T12:00:00+00:00", "resolvido_em": "2026-09-02T12:00:00+00:00", "resposta": "Enviamos por e-mail."}])
    b, c, pg, erros = abrir(pw)
    pg.click("#lgpd-box summary")
    pg.wait_for_selector("#lgpd-pedidos .lgpd-ped")
    lista = pg.inner_text("#lgpd-pedidos")
    conf("Recusado" in lista and "Atendido" in lista and "Resposta da coordenação:" in lista and "Enviamos por e-mail." in lista, "lista pedidos respondidos com a situação e a resposta da coordenação")
    conf(pg.evaluate("window.__xss || 0") == 0 and pg.locator("#lgpd-pedidos img").count() == 0 and X1 in lista, "mensagem e resposta maliciosas aparecem como TEXTO")
    conf(not erros, "pedidos respondidos: nenhum erro de JavaScript: %s" % erros)
    b.close()

    # =========================== aluno que já aceitou ===========================
    reiniciar(exigida=True, aceite=True)
    b, c, pg, erros = abrir(pw)
    espera_habilitado(pg)
    conf(not pg.is_visible("#lgpd-aceite"), "quem já aceitou não vê o cartão")
    conf(pg.evaluate("window.__piscou") is False, "quem já aceitou nunca vê 'Aceite o aviso de privacidade' piscar no botão enquanto o /me carrega")
    conf(pg.evaluate("window.__gps.ouvindo") == 1 and botao(pg).inner_text().strip() == "Registrar Presença", "GPS ligado e botão liberado como sempre")
    conf(estado["puts"] == [], "nenhum PUT de aceite foi feito sozinho")
    conf(pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "no celular (390 px) a página não ganha rolagem horizontal")
    conf(not erros, "aluno que aceitou: nenhum erro de JavaScript: %s" % erros)

    # aceite retirado em outro aparelho: o servidor recusa (403) e o cartão volta
    estado["erro_checkin"] = "Antes de registrar presença, leia e aceite o aviso de privacidade (cartão no alto da tela). Se preferir não aceitar, peça ao professor para lançar a sua presença manualmente."
    estado["aceite"] = False
    botao(pg).click()
    pg.wait_for_function("document.querySelector('#msg-aluno').textContent.includes('aviso de privacidade')")
    pg.wait_for_function("!document.querySelector('#lgpd-aceite').hidden")
    conf("err" in pg.get_attribute("#msg-aluno", "class"), "403 do servidor: a mensagem aparece em vermelho")
    conf(pg.is_visible("#lgpd-aceite") and botao(pg).is_disabled() and botao(pg).inner_text().strip() == "Aceite o aviso de privacidade",
         "e o cartão volta, com o botão travado, sem o aluno precisar recarregar a página")
    pg.wait_for_function("window.__gps.ouvindo === 0")
    b.close()

    # =========================== sair: nada do aluno anterior fica na tela ===========================
    reiniciar(exigida=True, aceite=False)
    b, c, pg, erros = abrir(pw)
    pg.click("#lgpd-box summary")
    pg.fill("#lgpd-msg", "rascunho do aluno anterior")
    pg.wait_for_selector("#lgpd-pedidos", state="attached")
    conf(pg.is_visible("#lgpd-aceite") and pg.evaluate("S.lgpdPendente") is True, "antes de sair: cartão visível e estado 'falta aceitar'")
    pg.click("#btn-sair")
    pg.wait_for_selector("#v-auth:not([hidden])")
    conf(pg.evaluate("S.lgpdPendente") is None and not pg.is_visible("#lgpd-aceite") and pg.input_value("#lgpd-msg") == "" and not pg.evaluate("document.querySelector('#lgpd-box').open"),
         "ao sair: estado volta a 'desconhecido', cartão escondido, rascunho do pedido apagado e a seção fechada")
    conf(pg.evaluate("window.__gps.ouvindo") == 0, "ao sair o GPS está desligado")
    conf(not erros, "sair: nenhum erro de JavaScript: %s" % erros)
    b.close()

    # =========================== a instituição não exige o aceite ===========================
    reiniciar(exigida=False, aceite=False)
    b, c, pg, erros = abrir(pw)
    espera_habilitado(pg)
    conf(not pg.is_visible("#lgpd-aceite") and pg.evaluate("window.__gps.ouvindo") == 1, "aceite não exigido: sem cartão, GPS ligado, botão liberado")
    pg.click("#lgpd-box summary")
    conf("não exige o aceite" in pg.inner_text("#lgpd-estado") and pg.is_disabled("#lgpd-troca"), "a seção diz que o aceite não é exigido e não oferece o botão")
    conf(not erros, "aceite não exigido: nenhum erro de JavaScript: %s" % erros)
    b.close()

    # =========================== servidor antigo (sem os campos novos) ===========================
    reiniciar(chaves=False)
    b, c, pg, erros = abrir(pw)
    espera_habilitado(pg)
    conf(not pg.is_visible("#lgpd-aceite") and botao(pg).inner_text().strip() == "Registrar Presença", "/me sem os campos novos: o app se comporta como antes (não bloqueia)")
    conf(not erros, "servidor antigo: nenhum erro de JavaScript: %s" % erros)
    b.close()

    # =========================== /me fora do ar ===========================
    reiniciar(me_erro=True)
    b = pw.chromium.launch()
    c = b.new_context(viewport={"width": 390, "height": 900})
    c.add_init_script(gps_falso(LAT, LON))
    c.route("**/*", handler)
    pg = c.new_page()
    erros = []
    pg.on("pageerror", lambda e: erros.append(str(e)))
    pg.goto("http://localhost:8765/")
    pg.wait_for_selector("#v-auth:not([hidden])")
    pg.click('.tab[data-p="aluno"]')
    pg.fill("#email", ALUNO["email"])
    pg.fill("#senha", "senha12345")
    pg.click("#btn-auth")
    pg.wait_for_selector("#aulas article")
    espera_habilitado(pg)
    conf(not pg.is_visible("#lgpd-aceite") and pg.evaluate("window.__gps.ouvindo") == 1, "/me falhou: o app não trava o aluno (o servidor é quem exige o aceite) e o GPS liga")
    conf("Falha no servidor" in pg.inner_text("#comprov"), "o erro do /me aparece na área dos comprovantes")
    conf(not erros, "/me fora do ar: nenhum erro de JavaScript: %s" % erros)
    b.close()

    # =========================== coordenação: aba LGPD ===========================
    reiniciar(auditoria=[
        {"id": 1, "data_hora": "2026-11-17T10:00:00+00:00", "usuario_id": 1, "usuario": "Ana Souza", "acao": "aceite_lgpd", "detalhe": "versao=2026-11", "ip": None},
        {"id": 2, "data_hora": "2026-11-17T10:01:00+00:00", "usuario_id": 1, "usuario": "Ana Souza", "acao": "revogou_lgpd", "detalhe": "versao=2026-11", "ip": None},
        {"id": 3, "data_hora": "2026-11-17T10:02:00+00:00", "usuario_id": 1, "usuario": "Ana Souza", "acao": "dados_titular", "detalhe": "baixou os próprios dados", "ip": None},
        {"id": 4, "data_hora": "2026-11-17T10:03:00+00:00", "usuario_id": 1, "usuario": "Ana Souza", "acao": "pedido_lgpd", "detalhe": "tipo=exclusao", "ip": None},
        {"id": 5, "data_hora": "2026-11-17T10:04:00+00:00", "usuario_id": 9, "usuario": "Coord Geral", "acao": "resp_pedido_lgpd", "detalhe": "pedido=31", "ip": None},
        {"id": 6, "data_hora": "2026-11-17T10:05:00+00:00", "usuario_id": None, "usuario": None, "acao": "retencao_lgpd", "detalhe": "presencas=3", "ip": None},
        {"id": 7, "data_hora": "2026-11-17T10:06:00+00:00", "usuario_id": 9, "usuario": "Coord Geral", "acao": "retencao_manual", "detalhe": "presencas=3", "ip": None}])
    b, c, pg, erros = abrir(pw, quem="admin", largura=1366)
    conf(pg.locator('#adm-tabs button[data-t="lgpd"]').count() == 1, "a coordenação tem a aba 'LGPD'")
    pg.click('#adm-tabs button[data-t="lgpd"]')
    pg.wait_for_function("document.querySelector('#adm-corpo').textContent.includes('Pedidos dos alunos')")
    corpo = pg.inner_text("#adm-corpo")
    tiles = {pg.inner_text(f"#adm-corpo .stat:nth-child({i})").split("\n")[-1]: pg.inner_text(f"#adm-corpo .stat:nth-child({i}) b") for i in range(1, 4)}
    conf(tiles == {"Alunos que aceitaram o aviso": "87 de 120", "Pedidos em aberto": "2", "Prazo para responder": "15 dias"}, "painel: aceitaram 87 de 120, 2 pedidos em aberto, prazo de 15 dias: %r" % tiles)
    conf("Excluir conta e dados" in corpo and "Corrigir dado" in corpo and "Bruno Lima" in corpo and "fora do prazo de 15 dias" in corpo and "aberto há 20 dia(s)" in corpo, "pedidos em aberto, com o prazo vencido destacado")
    conf(pg.locator("#adm-corpo .lgpd-ped .warn").count() == 1, "só o pedido vencido tem o aviso de prazo")
    conf(pg.evaluate("window.__xss || 0") == 0 and pg.locator("#adm-corpo img").count() == 0 and X1 in corpo, "o nome malicioso do aluno aparece como TEXTO")
    conf("conta já excluída" not in corpo.split("Pedidos já respondidos")[0], "pedidos em aberto sempre têm o aluno")
    conf("Enviamos por e-mail." in corpo and "Sua conta e os dados dela foram excluídos." in corpo, "lista os pedidos já respondidos com a resposta")
    conf("alcançaria 40 presença(s), 5 tentativa(s) e 12 registro(s)" in " ".join(corpo.split()), "mostra o que a limpeza alcançaria agora")
    conf(not pg.locator(".msg.err").filter(has_text="encarregado").count(), "com e-mail do encarregado informado, não há aviso de configuração")
    conf("Maria Dados" in corpo and "versão 2026-11" in corpo and pg.locator("#adm-corpo a[href='/privacidade'][target='_blank']").count() == 1, "mostra versão do aviso, controlador, encarregado e o link")

    # recusar exige motivo
    pg.click('button[data-act="lgpd-ped"][data-id="32"][data-v="recusar"]')
    conf("explique ao aluno" in pg.inner_text("#msg-adm") and estado["admin_puts"] == [], "recusar sem motivo: avisa e não envia")
    pg.fill("#lg-r-32", "Seu período já está correto no sistema.")
    pg.click('button[data-act="lgpd-ped"][data-id="32"][data-v="recusar"]')
    pg.wait_for_function("document.querySelector('#msg-adm').textContent.includes('recusado')")
    conf(estado["admin_puts"] == [(32, {"acao": "recusar", "resposta": "Seu período já está correto no sistema."})], "recusar com motivo manda PUT acao=recusar + resposta: %r" % estado["admin_puts"])
    conf(pg.locator('#adm-corpo .lgpd-ped').count() == 1, "o pedido recusado sai da fila")

    # atender a exclusão
    estado["aceitar"] = False
    pg.click('button[data-act="lgpd-ped"][data-id="31"][data-v="atender"]')
    conf(any("exclusão" in m and "não pode ser desfeito" in m for m in dialogos) and len(estado["admin_puts"]) == 1, "atender EXCLUSÃO pede confirmação; cancelando, nada é enviado")
    estado["aceitar"] = True
    pg.click('button[data-act="lgpd-ped"][data-id="31"][data-v="atender"]')
    pg.wait_for_function("document.querySelector('#msg-adm').textContent.includes('atendido')")
    conf(estado["admin_puts"][-1] == (31, {"acao": "atender", "resposta": None}), "atender manda PUT acao=atender, resposta=null: %r" % (estado["admin_puts"][-1],))
    conf("Nenhum pedido em aberto" in pg.inner_text("#adm-corpo"), "fila vazia depois de responder tudo")

    # limpeza
    estado["aceitar"] = False
    pg.click('button[data-act="lgpd-ret"]')
    conf(any("Rodar a limpeza agora?" in m for m in dialogos) and estado["retencao_posts"] == [], "a limpeza pede confirmação; cancelando, nada roda")
    estado["aceitar"] = True
    pg.click('button[data-act="lgpd-ret"]')
    pg.wait_for_function("document.querySelector('#msg-adm').textContent.includes('Limpeza feita')")
    conf(estado["retencao_posts"] == [{"simular": False}], "confirmando, manda POST /admin/lgpd/retencao com simular=false")
    conf("40 presença(s), 5 tentativa(s) e 12 registro(s)" in pg.inner_text("#msg-adm"), "mostra quanto foi limpo: %r" % pg.inner_text("#msg-adm"))
    conf(pg.is_disabled('button[data-act="lgpd-ret"]'), "sem nada para limpar, o botão da limpeza fica desabilitado")

    # erro do servidor ao responder
    estado["painel"]["pedidos"] = [dict(painel_padrao()["pedidos"][1])]
    estado["erro_admin"] = "Este pedido já foi respondido."
    pg.click('#adm-tabs button[data-t="auditoria"]')
    pg.click('#adm-tabs button[data-t="lgpd"]')
    pg.wait_for_selector('button[data-act="lgpd-ped"][data-id="32"]')
    pg.fill("#lg-r-32", "Motivo qualquer da recusa.")
    pg.click('button[data-act="lgpd-ped"][data-id="32"][data-v="recusar"]')
    pg.wait_for_function("document.querySelector('#msg-adm').textContent.includes('já foi respondido')")
    conf("err" in pg.get_attribute("#msg-adm", "class"), "erro do servidor (409) aparece em vermelho")

    # auditoria com rótulos legíveis
    pg.click('#adm-tabs button[data-t="auditoria"]')
    pg.wait_for_function("document.querySelector('#adm-corpo').textContent.includes('Trilha de auditoria')")
    aud = pg.inner_text("#adm-corpo")
    for rot in ["Aluno aceitou o aviso de privacidade", "Aluno retirou o aceite do aviso", "Aluno baixou os próprios dados", "Aluno fez um pedido sobre os dados",
                "Coordenação respondeu a um pedido (LGPD)", "Limpeza automática de dados antigos", "Coordenação rodou a limpeza de dados antigos"]:
        conf(rot in aud, "auditoria mostra o rótulo: %s" % rot)
    conf(not erros, "coordenação: nenhum erro de JavaScript: %s" % erros)
    b.close()

    # ---- avisos de configuração
    p = painel_padrao()
    p["config"].update(encarregado_email="", encarregado_nome="", exigir_aceite=False)
    reiniciar(painel=p)
    b, c, pg, erros = abrir(pw, quem="admin", largura=390)
    pg.click('#adm-tabs button[data-t="lgpd"]')
    pg.wait_for_function("document.querySelector('#adm-corpo').textContent.includes('Pedidos dos alunos')")
    corpo = pg.inner_text("#adm-corpo")
    conf("LGPD_ENCARREGADO_EMAIL" in corpo and pg.locator("#adm-corpo .msg.err").count() == 1, "sem e-mail do encarregado: aviso em vermelho com o nome da variável")
    conf("DESLIGADO" in corpo and "LGPD_EXIGIR_ACEITE" in corpo, "aceite desligado: avisa")
    conf(pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "no celular (390 px) a aba não ganha rolagem horizontal")
    conf(not erros, "avisos de configuração: nenhum erro de JavaScript: %s" % erros)
    b.close()

    p = painel_padrao()
    p["config"].update(retencao_dias=0, retencao_logs_dias=0)
    reiniciar(painel=p)
    b, c, pg, erros = abrir(pw, quem="admin", largura=1366)
    pg.click('#adm-tabs button[data-t="lgpd"]')
    pg.wait_for_function("document.querySelector('#adm-corpo').textContent.includes('Pedidos dos alunos')")
    conf("A limpeza automática está DESLIGADA" in pg.inner_text("#adm-corpo") and pg.locator('button[data-act="lgpd-ret"]').count() == 0, "limpeza desligada: avisa e não oferece o botão de limpar")
    conf(not erros, "limpeza desligada: nenhum erro de JavaScript: %s" % erros)
    b.close()

print()
print("FALHA=%d" % len(falhas))
sys.exit(1 if falhas else 0)
