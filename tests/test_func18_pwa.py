# -*- coding: utf-8 -*-
"""
QA - Funcionalidade 18: APP INSTALÁVEL NO CELULAR (PWA)
Manifesto (/manifest.webmanifest), ícones 192/512/maskable, service worker (/sw.js) e as ligações na página. O comportamento do
service worker (rede primeiro, cópia só sem sinal, /api/ nunca guardado) é conferido no navegador: tests/navegador/pwa_tela.py.
Uso:  pytest tests/test_func18_pwa.py -v
"""
import json
import re
from pathlib import Path

import pytest

from harness_app import *  # noqa: F401,F403

RAIZ = Path(__file__).resolve().parent.parent
HTML = (RAIZ / "index.html").read_text(encoding="utf-8")
APP_PY = (RAIZ / "app.py").read_text(encoding="utf-8")
SW = (RAIZ / "sw.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ns():
    return Ctx().ns


def test_ct1801_manifesto_tem_o_que_o_celular_exige(ns):
    m = ns["MANIFESTO"]
    assert m["name"] == "Guará Check" and m["short_name"] and len(m["short_name"]) <= 12
    assert m["display"] == "standalone" and m["start_url"] == "/" and m["scope"] == "/" and m["lang"] == "pt-BR"
    assert re.fullmatch(r"#[0-9a-f]{6}", m["theme_color"]) and re.fullmatch(r"#[0-9a-f]{6}", m["background_color"])
    json.dumps(m)                                           # serializável: a rota devolve exatamente isto


def test_ct1802_cor_do_tema_igual_a_da_pagina(ns):
    assert re.search(rf'<meta name="theme-color" content="{ns["MANIFESTO"]["theme_color"]}"', HTML)


def test_ct1803_icones_do_manifesto_existem_com_o_tamanho_declarado(ns):
    PIL = pytest.importorskip("PIL.Image")
    icones = ns["MANIFESTO"]["icons"]
    assert {i["sizes"] for i in icones} >= {"192x192", "512x512"} and any(i["purpose"] == "maskable" for i in icones)
    for i in icones:
        arq = RAIZ / i["src"].lstrip("/")
        assert arq.is_file(), f"ícone que não existe: {i['src']}"
        assert i["src"].startswith("/assets/") and ns["ASSET_RE"].fullmatch(i["src"].split("/")[-1]), "a rota /assets/ não serviria este nome"
        assert i["type"] == "image/png" and "x".join(map(str, PIL.open(arq).size)) == i["sizes"]


def test_ct1804_icones_com_o_fundo_do_manifesto(ns):
    PIL = pytest.importorskip("PIL.Image")
    fundo = ns["MANIFESTO"]["background_color"].lstrip("#")
    esperado = tuple(int(fundo[k:k + 2], 16) for k in (0, 2, 4))
    for nome in ("icone-512.png", "icone-maskable-512.png"):
        im = PIL.open(RAIZ / "assets" / nome).convert("RGB")
        assert im.getpixel((2, 2)) == esperado and im.getpixel((509, 509)) == esperado, f"{nome}: os cantos não têm a cor de fundo do site"


def test_ct1805_icone_maskable_deixa_o_desenho_na_zona_segura():
    """Android recorta o ícone maskable (círculo de 80% do lado): nenhum pixel do desenho pode ficar fora desse círculo."""
    PIL = pytest.importorskip("PIL.Image")
    im = PIL.open(RAIZ / "assets" / "icone-maskable-512.png").convert("RGB")
    fundo = im.getpixel((2, 2))
    cx = cy = 256
    raio = 0.40 * 512
    fora = [(x, y) for y in range(0, 512, 2) for x in range(0, 512, 2)
            if im.getpixel((x, y)) != fundo and ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 > raio]
    assert fora == [], f"{len(fora)} pontos do desenho fora da zona segura"


def test_ct1806_rotas_do_manifesto_e_do_service_worker_existem():
    assert '@app.get("/manifest.webmanifest")' in APP_PY and '@app.get("/sw.js")' in APP_PY
    assert 'media_type="application/manifest+json"' in APP_PY
    assert re.search(r'Response\(conteudo, media_type="text/javascript", headers=\{"Cache-Control": "no-cache"\}\)', APP_PY), \
        "o service worker precisa de no-cache para as versões novas chegarem"


def test_ct1807_pagina_liga_manifesto_e_registra_o_service_worker_no_prefixo():
    assert '<link rel="manifest" href="manifest.webmanifest">' in HTML
    assert "navigator.serviceWorker.register(appUrl('/sw.js')" in HTML
    assert "const APP_PREFIX = '__APP_PREFIX__'" in HTML
    assert "serviceWorker' in navigator" in HTML                           # navegador sem suporte não quebra a página


def test_ct1808_csp_nao_bloqueia_manifesto_nem_service_worker(ns):
    csp = ns["CSP_PAGINA"]
    assert "default-src 'self'" in csp and "script-src 'self'" in csp
    assert "worker-src" not in csp and "manifest-src" not in csp, "uma diretiva própria exigiria listar o endereço de novo"


def test_ct1809_service_worker_nunca_toca_na_api_nem_em_outros_enderecos():
    assert "url.pathname.startsWith(BASE + '/api/')) return" in SW
    assert "url.origin !== self.location.origin) return" in SW and "req.method !== 'GET'" in SW
    # as cópias só são feitas da página e de /assets/
    assert SW.count("respondWith(") == 2 and "e.respondWith(pagina(req))" in SW and "e.respondWith(imagem(req))" in SW


def test_ct1810_service_worker_guarda_so_arquivos_que_existem():
    casca = re.search(r"const CASCA = \[(.*?)\];", SW, re.S).group(1)
    caminhos = re.findall(r"'([^']+)'", casca)
    assert "INICIO" in casca
    for c in caminhos:
        caminho = c.replace("BASE + ", "").strip()
        if caminho.startswith("/assets/"):
            assert (RAIZ / caminho.lstrip("/")).is_file(), f"addAll falharia (instalação do service worker recusada): {c}"
    assert "const VERSAO = 'gc-v2-'" in SW


def test_ct1811_service_worker_troca_de_versao_apagando_as_copias_antigas():
    assert "k !== VERSAO" in SW and "caches.delete(k)" in SW and "self.clients.claim()" in SW and "self.skipWaiting()" in SW


def test_ct1812_pagina_so_e_guardada_quando_e_html_e_ok():
    assert "r.ok && tipo.startsWith('text/html')" in SW


def test_ct1814_so_a_pagina_principal_e_guardada_outras_paginas_vao_direto_a_rede():
    """Senão /privacidade (também HTML) sobrescreveria a cópia de '/' e quem abrisse sem sinal veria a página errada."""
    assert "req.mode === 'navigate' && url.pathname === INICIO" in SW


def test_ct1815_manifesto_e_casca_aceitam_subcaminho():
    assert 'APP_PREFIX = ("/" + os.getenv("APP_PREFIX", "").strip("/"))' in APP_PY
    assert '"start_url": f"{APP_PREFIX}/"' in APP_PY
    assert "replace(\"__APP_PREFIX__\", APP_PREFIX)" in APP_PY


def test_ct1813_dica_de_instalacao_existe_na_tela_do_aluno():
    for id_ in ("instalar", "instalar-btn", "instalar-fecha", "instalar-txt"):
        assert f'id="{id_}"' in HTML
    assert "beforeinstallprompt" in HTML and "appinstalled" in HTML and "display-mode: standalone" in HTML
