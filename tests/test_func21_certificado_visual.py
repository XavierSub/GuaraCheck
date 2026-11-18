# -*- coding: utf-8 -*-
"""
QA - Funcionalidade 21: VISUAL DO CERTIFICADO IGUAL AO DA PÁGINA INICIAL
O certificado em PDF repete a identidade do cabeçalho do site: topo branco com as logos (curso, UniCerrado, FESG), o nome Guará Check
com a versão, a pílula azul com o lema, fio verde-lima, fundo claro, título em azul e verde do site. As cores vêm do :root do index.html
(o teste lê o próprio index.html, então mudar a paleta de um lado sem o outro faz o teste falhar). Logo ausente ou corrompida não impede
a emissão. Nada se sobrepõe no topo e o texto longo não invade a assinatura nem o código.
Uso:  pytest tests/test_func21_certificado_visual.py -v
"""
import re
import subprocess

from harness_app import *  # noqa: F401,F403
from test_func5_chamada_comprovantes import texto_pdf, pypdf
from test_func15_certificado import ce, certificado  # noqa: F401  (fixture ce)

RAIZ = Path(__file__).resolve().parents[1]
INDEX = (RAIZ / "index.html").read_text(encoding="utf-8")
LOGOS = ["logo-curso.png", "logo-unicerrado.png", "logo-fesg.png", "guara-icone.png", "guara-nome.png", "guara-pino.png"]


def pdf_do_aluno(c):
    c.registrar()
    st, resp = certificado(c, c.presencas()[0].id)
    assert st == 200
    return resp.body


def paleta_do_site():
    raiz = re.search(r":root\{([^}]*)\}", INDEX).group(1)
    return dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-fA-F]{6})", raiz))


def rgb(hexa):
    h = hexa.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def cores_no_pdf(corpo):
    """Todas as cores `r g b rg/RG` usadas na página, como tuplas de 0 a 1."""
    leitor = pypdf.PdfReader(io.BytesIO(corpo))
    fluxo = leitor.pages[0].get_contents().get_data().decode("latin-1")
    return [tuple(float(x) for x in m.groups()[:3]) for m in re.finditer(r"([\d.]+) ([\d.]+) ([\d.]+) (rg|RG)\b", fluxo)]


def tem_cor(cores, hexa, tol=0.004):
    alvo = rgb(hexa)
    return any(all(abs(a - b) <= tol for a, b in zip(cor, alvo)) for cor in cores)


def luminancia(hexa):
    f = lambda v: v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (f(v) for v in rgb(hexa))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contraste(a, b):
    la, lb = sorted((luminancia(a), luminancia(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def palavras(corpo):
    """(texto, xMin, yMin, xMax, yMax) de cada palavra, em pontos, pelo pdftotext -bbox (y cresce para baixo)."""
    r = subprocess.run(["pdftotext", "-bbox", "-", "-"], input=corpo, capture_output=True, check=True)
    return [(t, float(x0), float(y0), float(x1), float(y1)) for x0, y0, x1, y1, t in
            re.findall(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>', r.stdout.decode("utf-8"))]


# --------------------------------------------------------------------------- imagens e conteúdo
def test_ct2101_uma_pagina_com_as_seis_imagens_do_topo(ce):
    corpo = pdf_do_aluno(ce)
    pag = pypdf.PdfReader(io.BytesIO(corpo)).pages
    assert len(pag) == 1
    assert len(pag[0].images) == 6        # curso, UniCerrado, FESG, ícone, nome e pino do Guará Check


def test_ct2102_as_logos_sao_as_do_cabecalho_do_site(ce):
    for arq in LOGOS[:5]:
        assert f'src="assets/{arq}"' in INDEX, arq        # o site usa os mesmos arquivos do certificado
    pdf = pdf_do_aluno(ce)
    tamanhos = sorted(im.image.size for im in pypdf.PdfReader(io.BytesIO(pdf)).pages[0].images)
    from PIL import Image
    esperados = sorted(Image.open(RAIZ / "assets" / a).size for a in LOGOS)
    assert tamanhos == esperados


def test_ct2103_texto_continua_completo(ce):
    t = texto_pdf(pdf_do_aluno(ce))
    for trecho in ("CERTIFICADO DE PARTICIPAÇÃO", "Certificamos que", "Semana de Tecnologia", "carga horária de 2,5 horas",
                   "CÓDIGO DE VERIFICAÇÃO", "Sua presença, no lugar certo.", "Centro Universitário de Goiatuba (UniCerrado)",
                   "não contém CPF nem coordenadas de localização"):
        assert trecho in t, trecho
    assert re.search(r"C\d+-[0-9A-F]{12}", t)


def test_ct2104_versao_do_pdf_e_a_do_site(ce):
    site = re.search(r"versao:\s*'([^']+)'", INDEX).group(1)
    assert ce.ns["PDF_VERSAO"] == site
    assert site in texto_pdf(pdf_do_aluno(ce)).split()


# --------------------------------------------------------------------------- cores do site
@pytest.mark.parametrize("var", ["bg", "azul", "lima", "pri", "okbg", "line", "ink", "mut"])
def test_ct2111_o_pdf_usa_a_cor_do_site(ce, var):
    cores = cores_no_pdf(pdf_do_aluno(ce))
    assert tem_cor(cores, paleta_do_site()[var]), f"--{var} ({paleta_do_site()[var]}) não aparece no certificado"


def test_ct2112_pilula_do_lema_tem_o_azul_escuro_do_site(ce):
    azul_lema = re.search(r"\.lema\{[^}]*background:(#[0-9a-fA-F]{6})", INDEX).group(1)
    assert tem_cor(cores_no_pdf(pdf_do_aluno(ce)), azul_lema)


def test_ct2113_nao_sobrou_o_verde_escuro_antigo(ce):
    cores = cores_no_pdf(pdf_do_aluno(ce))
    assert not tem_cor(cores, "#1d6b47") and not tem_cor(cores, "#eef7f1")


def test_ct2114_contraste_dos_textos_no_minimo_aa(ce):
    """WCAG AA: 4,5:1 para texto comum; 3:1 só para texto grande (a partir de 14 pt em negrito: título 30 pt e código 20 pt)."""
    p = paleta_do_site()
    branco = "#ffffff"
    comum = {"texto": (p["ink"], branco), "cinza (legenda e rodapé)": (p["mut"], branco), "rótulo e legenda do código": (p["mut"], p["okbg"]),
             "lema": (branco, "#0b2a6b"), "versão": ("#4d5f56", branco)}
    grande = {"título azul (30 pt)": (p["azul"], branco), "código (20 pt negrito)": (p["pri"], p["okbg"])}
    for nome, (fg, bg) in comum.items():
        assert contraste(fg, bg) >= 4.5, f"{nome}: {contraste(fg, bg):.2f}"
    for nome, (fg, bg) in grande.items():
        assert contraste(fg, bg) >= 3.0, f"{nome}: {contraste(fg, bg):.2f}"


# --------------------------------------------------------------------------- logo ausente ou ruim
def test_ct2121_sem_a_pasta_de_logos_o_certificado_sai_sem_imagens(ce, tmp_path):
    ce.ns["BASE_DIR"] = tmp_path
    corpo = pdf_do_aluno(ce)
    leitor = pypdf.PdfReader(io.BytesIO(corpo))
    assert len(leitor.pages) == 1 and len(leitor.pages[0].images) == 0
    assert "CERTIFICADO DE PARTICIPAÇÃO" in texto_pdf(corpo) and "Sua presença, no lugar certo." in texto_pdf(corpo)


def test_ct2122_uma_logo_corrompida_so_e_pulada(ce, tmp_path):
    pasta = tmp_path / "assets"
    pasta.mkdir()
    for a in LOGOS:
        (pasta / a).write_bytes((RAIZ / "assets" / a).read_bytes())
    (pasta / "logo-curso.png").write_bytes(b"isto nao e um png")
    ce.ns["BASE_DIR"] = tmp_path
    corpo = pdf_do_aluno(ce)
    leitor = pypdf.PdfReader(io.BytesIO(corpo))
    assert len(leitor.pages) == 1 and len(leitor.pages[0].images) == 5


def test_ct2123_imagem_ruim_na_hora_de_desenhar_nao_derruba(ce):
    class Ruim:
        def getSize(self):
            return (10, 10)

    class Canvas:
        def drawImage(self, *a, **k):
            raise ValueError("falhou")
    assert ce.ns["_pdf_imagem_altura"](Canvas(), Ruim(), 0, 0, 20) == 0.0
    assert ce.ns["_pdf_imagem_altura"](Canvas(), None, 0, 0, 20) == 0.0


# --------------------------------------------------------------------------- geometria
def test_ct2131_topo_sem_sobreposicao(ce):
    pal = {t: (x0, x1) for t, x0, _, x1, _ in palavras(pdf_do_aluno(ce))}
    assert pal["V1"][1] + 20 < pal["Sua"][0], "a versão encosta na pílula do lema"
    assert pal["lugar"][1] < 842 - 40 + 1 and pal["certo."][1] <= 842 - 40 + 1, "o lema passa da margem direita"


def test_ct2132_texto_longo_nao_invade_assinatura_nem_codigo(ce):
    ce.aula.titulo = "Simpósio " + "Internacional de Tecnologia e Inovação " * 3
    ce.aluno.nome = "Maria " + "da Silva Pereira " * 5
    ce.aula.local_descricao = "Auditório " + "Central " * 14
    ce.aula.disciplina = "Segurança " * 9
    ce.aula.carga_horas = 12.5
    ws = palavras(pdf_do_aluno(ce))
    assert len(pypdf.PdfReader(io.BytesIO(pdf_do_aluno(ce))).pages) == 1
    frase_fim = max(y1 for t, x0, y0, x1, y1 in ws if y0 > 150 and y1 < 450 and t not in ("Prof(a).",))
    topo_assinatura = min(y0 for t, x0, y0, x1, y1 in ws if t in ("Prof(a).", "CÓDIGO"))
    assert frase_fim + 6 < topo_assinatura, f"texto termina em {frase_fim:.0f} e a assinatura começa em {topo_assinatura:.0f}"
