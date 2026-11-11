# -*- coding: utf-8 -*-
"""
QA - Funcionalidade 7: ANOTAÇÕES PRIVADAS DO ALUNO E PDFs (Guará Check)
Criar, atualizar, listar, apagar e baixar em PDF as anotações do aluno sobre os eventos em que registrou presença.
O que mais importa aqui é a PRIVACIDADE (só o dono lê) e que a anotação sobreviva ao evento excluído.

Como nas funcionalidades anteriores: o código REAL do app.py é extraído por ast e executado contra um banco em memória
(harness_app.py). Os PDFs são gerados DE VERDADE (reportlab) e lidos de volta (pypdf).
NÃO é executado aqui: SQLAlchemy/PostgreSQL de verdade e o download no navegador (veja tests/navegador).

Uso:  pip install pytest pydantic pyjwt openpyxl reportlab pypdf
      pytest test_func7_anotacoes.py -v
"""
import ast as _ast
import time as _t
import types as _types
from datetime import datetime as _dt, timedelta as _td, timezone as _tz

from harness_app import *  # noqa: F401,F403
from harness_app import APP, FakeDB, FakeReq, NotaAluno, Presenca  # noqa: F401
from test_func5_chamada_comprovantes import CtxCh, texto_pdf

pypdf = pytest.importorskip("pypdf")


class CtxNt(CtxCh):
    """CtxCh + atalhos para as rotas de anotações do aluno."""

    def nota(self, texto, presenca, aluno=None, db=None):
        pid = getattr(presenca, "id", presenca)
        try:
            corpo = self.ns["NotaIn"](texto=texto)
        except ValidationError as e:
            return 422, e
        return status_de(self.ns["salvar_nota"], pid, corpo, self.sessao(aluno), db or self.db)

    def notas(self, aluno=None):
        return status_de(self.ns["minhas_notas"], self.sessao(aluno), self.db)

    def apagar(self, nota_id, aluno=None):
        return status_de(self.ns["excluir_nota"], nota_id, self.sessao(aluno), self.db)

    def pdf_todas(self, aluno=None):
        return status_de(self.ns["notas_pdf_todas"], self.sessao(aluno), self.db)

    def pdf_uma(self, nota_id, aluno=None):
        return status_de(self.ns["nota_pdf"], nota_id, self.sessao(aluno), self.db)

    def linhas_notas(self):
        return self.db.tables[NotaAluno]

    def presenca_do(self, aluno=None, aula=None, **kw):
        return self.semear_presenca(aluno or self.aluno, aula=aula, **kw)


@pytest.fixture
def nt():
    return CtxNt()


def todos_os_fragmentos(corpo: bytes):
    """[(pagina, texto, x, fonte, tamanho, y)] de cada trecho de texto de todas as páginas do PDF."""
    achados = []
    leitor = pypdf.PdfReader(io.BytesIO(corpo))
    for n, pagina in enumerate(leitor.pages, start=1):
        def visitante(texto, cm, tm, fonte, tam, n=n):
            if texto.strip():
                nome = str((fonte or {}).get("/BaseFont", "Helvetica")).lstrip("/")
                achados.append((n, texto.strip(), tm[4], nome, tam, tm[5]))
        pagina.extract_text(visitor_text=visitante)
    return achados


# =====================================================================================================
# BLOCO A - Privacidade: só o dono
# =====================================================================================================
def test_ct801_nao_anota_presenca_de_outro_aluno(nt):
    """CT-801 | Aluno tenta anotar na presença de OUTRO aluno | 404 e nada é gravado."""
    outro = nt.novo_aluno(2)
    p_outro = nt.presenca_do(outro)
    st, det = nt.nota("espionando", p_outro)
    assert st == 404 and det == "Presença não encontrada." and nt.linhas_notas() == []


def test_ct802_nao_le_nem_apaga_nem_baixa_nota_alheia(nt):
    """CT-802 | Nota de outro aluno: listar, apagar e PDF | não aparece, 404 e a nota continua lá."""
    outro = nt.novo_aluno(2)
    p_outro = nt.presenca_do(outro)
    n = nt.nota("segredo do outro", p_outro, aluno=outro)[1]["nota"]
    assert nt.notas()[1] == []
    assert nt.apagar(n["id"])[0] == 404
    assert nt.pdf_uma(n["id"])[0] == 404
    assert nt.pdf_todas()[0] == 404                                   # eu não tenho nenhuma
    assert [r.texto for r in nt.linhas_notas()] == ["segredo do outro"]


def test_ct802b_nao_sobrescreve_a_nota_de_outro_aluno(nt):
    """CT-802b | O outro aluno JÁ tem anotação nessa presença e eu tento salvar nela | 404 e a anotação dele fica intacta."""
    outro = nt.novo_aluno(2)
    p_outro = nt.presenca_do(outro)
    nt.nota("original do outro", p_outro, aluno=outro)
    assert nt.nota("sobrescrevi!", p_outro)[0] == 404
    assert nt.nota("", p_outro)[1] == {"ok": True, "nota": None}          # texto vazio meu também não apaga a dele
    assert [r.texto for r in nt.linhas_notas()] == ["original do outro"]


def test_ct803_pdf_de_todas_so_leva_as_minhas(nt):
    """CT-803 | PDF com todas as anotações | nunca contém texto de outro aluno."""
    outro = nt.novo_aluno(2)
    nt.nota("NOTA-DO-OUTRO-777", nt.presenca_do(outro), aluno=outro)
    nt.nota("minha nota 111", nt.presenca_do())
    st, resp = nt.pdf_todas()
    texto = texto_pdf(resp.body)
    assert st == 200 and "minha nota 111" in texto and "NOTA-DO-OUTRO-777" not in texto


@pytest.mark.parametrize("papel", ["professor", "admin"])
def test_ct804_professor_e_coordenacao_nao_acessam(nt, papel):
    """CT-804 | Professor ou coordenação nas rotas de anotação | 403 (a dependência exige perfil de aluno)."""
    st, det = status_de(nt.ns["so_aluno"], {"id": 1, "role": papel, "nome": "X"})
    assert st == 403


def test_ct805_so_o_aluno_le_a_tabela_de_anotacoes():
    """CT-805 | Análise do código: nenhuma rota de professor ou da coordenação lê a tabela de anotações (só apaga junto com o aluno)."""
    arvore = _ast.parse(APP.read_text(encoding="utf-8"))
    usam = sorted(f.name for f in arvore.body if isinstance(f, _ast.FunctionDef)
                  and any(isinstance(n, _ast.Name) and n.id == "NotaAluno" for n in _ast.walk(f)))
    permitidas = {"_nota_dict", "minhas_notas", "salvar_nota", "excluir_nota", "notas_pdf_todas", "nota_pdf", "_pdf_notas",
                  "adm_aluno_excluir", "resetar_dados_de_teste"}      # esta só roda com RESET_BANCO ligado (apaga tudo)
    fora = [n for n in usam if n not in permitidas]
    assert fora == [], f"funções que tocam NotaAluno fora da lista aprovada: {fora}"
    adm = next(f for f in arvore.body if isinstance(f, _ast.FunctionDef) and f.name == "adm_aluno_excluir")
    leituras = [n for n in _ast.walk(adm) if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Name) and n.func.id in ("select", "scalar")
                and any(isinstance(m, _ast.Name) and m.id == "NotaAluno" for m in _ast.walk(n))]
    assert leituras == [], "a coordenação só pode APAGAR anotações, nunca lê-las"


# =====================================================================================================
# BLOCO B - Criar, atualizar, listar, apagar
# =====================================================================================================
def test_ct810_criar_anotacao(nt):
    """CT-810 | Criar | devolve a nota com cópia do título/tipo/disciplina e a hora da presença."""
    nt.aula.disciplina = "Estruturas de Dados"
    quando = _dt(2026, 11, 11, 14, 5, 0, tzinfo=_tz.utc)
    p = nt.presenca_do(quando=quando)
    st, r = nt.nota("Aprendi sobre pilhas", p)
    n = r["nota"]
    assert st == 200 and r["ok"] is True
    assert (n["presenca_id"], n["titulo"], n["tipo"], n["disciplina"], n["texto"]) == (p.id, "Algoritmos", "aula", "Estruturas de Dados", "Aprendi sobre pilhas")
    assert n["evento_em"] == nt.ns["iso"](quando) and n["atualizado_em"]
    assert set(n) == {"id", "presenca_id", "titulo", "tipo", "disciplina", "evento_em", "texto", "atualizado_em"}


def test_ct811_atualizar_mantem_o_mesmo_registro(nt):
    """CT-811 | Salvar de novo o mesmo evento | atualiza a mesma nota (mesmo id), muda a hora de atualização, não a data do evento."""
    p = nt.presenca_do()
    a = nt.nota("primeira versão", p)[1]["nota"]
    nt.avancar(120)
    b = nt.nota("segunda versão", p)[1]["nota"]
    assert b["id"] == a["id"] and b["texto"] == "segunda versão" and len(nt.linhas_notas()) == 1
    assert b["evento_em"] == a["evento_em"] and b["atualizado_em"] > a["atualizado_em"]


def test_ct812_texto_vazio_apaga(nt):
    """CT-812 | Salvar texto vazio ou só espaços | apaga a anotação existente; sem anotação, não cria nada."""
    p = nt.presenca_do()
    assert nt.nota("   \n\t ", p)[1] == {"ok": True, "nota": None} and nt.linhas_notas() == []
    nt.nota("algo", p)
    assert nt.nota("  ", p)[1] == {"ok": True, "nota": None} and nt.linhas_notas() == []


def test_ct813_apagar_pela_rota_de_exclusao(nt):
    """CT-813 | Excluir | remove só a nota escolhida; excluir de novo dá 404."""
    p1, p2 = nt.presenca_do(), nt.presenca_do(nt.novo_aluno(2))
    n1 = nt.nota("um", p1)[1]["nota"]
    nt.nota("dois", p2, aluno=nt.db.tables[Aluno][1])
    assert nt.apagar(n1["id"])[1] == {"ok": True}
    assert [r.texto for r in nt.linhas_notas()] == ["dois"]
    assert nt.apagar(n1["id"])[0] == 404


def test_ct814_lista_da_mais_nova_para_a_mais_antiga(nt):
    """CT-814 | Lista ordenada pela data do evento (e pelo id em empate) | mais recente primeiro."""
    a2, a3 = nt.nova_aula(nt.ana, titulo="B"), nt.nova_aula(nt.outro(), titulo="C")
    ps = [nt.presenca_do(quando=_dt(2026, 11, d, 12, tzinfo=_tz.utc), aula=a) for d, a in ((3, nt.aula), (9, a2), (6, a3))]
    for p in ps:
        nt.nota(f"nota {p.id}", p)
    assert [n["titulo"] for n in nt.notas()[1]] == ["B", "C", "Algoritmos"]


def test_ct815_lista_tem_teto_de_200(nt):
    """CT-815 | Mais anotações que o teto | a lista e o PDF trazem só as NOTAS_MAX_PDF mais recentes."""
    nt.ns["NOTAS_MAX_PDF"] = 3
    for i in range(5):
        aula = nt.nova_aula(nt.outro(), titulo=f"E{i}")
        nt.nota(f"n{i}", nt.presenca_do(quando=_dt(2026, 11, 1 + i, 12, tzinfo=_tz.utc), aula=aula))
    assert [n["titulo"] for n in nt.notas()[1]] == ["E4", "E3", "E2"]
    assert nt.ns["NOTAS_MAX_PDF"] == 3 and texto_pdf(nt.pdf_todas()[1].body).count("E1") == 0


def test_ct816_so_uma_anotacao_por_presenca_mesmo_com_corrida(nt):
    """CT-816 | Dois salvamentos ao mesmo tempo criando a mesma anotação | o segundo recebe 409 (não vira duplicata nem erro 500)."""
    p = nt.presenca_do()
    paralela = nt.sessao_paralela()
    nt.db.antes_do_commit = lambda: nt.nota("da outra aba", p, db=paralela)
    st, det = nt.nota("desta aba", p)
    assert st == 409 and "Tente de novo" in det
    assert [r.texto for r in nt.linhas_notas()] == ["da outra aba"]


def test_ct817_apos_o_conflito_tentar_de_novo_atualiza(nt):
    """CT-817 | Depois do 409, salvar de novo | atualiza a nota que a outra aba criou."""
    p = nt.presenca_do()
    paralela = nt.sessao_paralela()
    nt.db.antes_do_commit = lambda: nt.nota("da outra aba", p, db=paralela)
    nt.nota("desta aba", p)
    assert nt.nota("desta aba", p)[0] == 200 and [r.texto for r in nt.linhas_notas()] == ["desta aba"]


@pytest.mark.parametrize("pid", [0, -5, 99999, 10 ** 12])
def test_ct818_presenca_inexistente_404(nt, pid):
    """CT-818 | Presença que não existe (0, negativa, enorme) | 404, nunca 500."""
    assert nt.nota("x", pid)[0] == 404


# =====================================================================================================
# BLOCO C - Evento excluído, aluno excluído
# =====================================================================================================
def test_ct820_anotacao_sobrevive_ao_evento_excluido_e_continua_editavel(nt):
    """CT-820 | O professor exclui o evento | a anotação fica (com a cópia do título), aparece na lista e ainda pode ser editada e baixada."""
    p = nt.presenca_do()
    nt.nota("o que aprendi", p)
    st, _ = status_de(nt.ns["excluir_aula"], nt.aula.id, FakeReq(), nt.eu(), nt.db)
    assert st == 200 and nt.db.tables[Presenca] == []
    (n,) = nt.notas()[1]
    assert n["titulo"] == "Algoritmos" and n["texto"] == "o que aprendi"
    assert nt.nota("revisei depois", p.id)[1]["nota"]["texto"] == "revisei depois"
    assert nt.pdf_uma(n["id"])[0] == 200


def test_ct821_nao_cria_nota_nova_para_evento_excluido(nt):
    """CT-821 | Sem anotação prévia e com o evento já excluído | 404 (não há de onde copiar o título)."""
    p = nt.presenca_do()
    status_de(nt.ns["excluir_aula"], nt.aula.id, FakeReq(), nt.eu(), nt.db)
    assert nt.nota("tarde demais", p.id)[0] == 404


def test_ct822_coordenacao_ao_excluir_o_aluno_apaga_as_anotacoes_dele(nt):
    """CT-822 | Coordenação exclui o aluno | as anotações dele somem junto (e as dos outros ficam)."""
    outro = nt.novo_aluno(2)
    nt.nota("minha", nt.presenca_do())
    nt.nota("do outro", nt.presenca_do(outro), aluno=outro)
    coord = Professor(nome="Coord", email="coord@unicerrado.edu.br", senha_hash="x", registro_funcional="C-1", departamento="C", coordenacao=True)
    nt.db.add(coord)
    nt.db.commit()
    st, _ = status_de(nt.ns["adm_aluno_excluir"], nt.aluno.id, FakeReq(), {"id": coord.id, "role": "admin", "nome": "C"}, nt.db)
    assert st == 200 and [r.texto for r in nt.linhas_notas()] == ["do outro"]


# =====================================================================================================
# BLOCO D - Limpeza e tamanho do texto
# =====================================================================================================
@pytest.mark.parametrize("entrada,saida", [
    ("  olá  ", "olá"), ("a\r\nb\rc", "a\nb\nc"), ("coluna\tum", "coluna\tum"), ("a\x00b\x07c\x1bd", "abcd"),
    ("linha1\n\nlinha3", "linha1\n\nlinha3"), ("‮evil‬", "evil"), ("", ""), (None, ""),
    ("café ção ÇÃO ñ ü", "café ção ÇÃO ñ ü"), ("日本語 العربية", "日本語 العربية"),
    ("😀 vamos", "😀 vamos"), ("👩‍💻 dev", "👩‍💻 dev"), ("👨‍👩‍👧", "👨‍👩‍👧"),
    ("❤️", "❤️"),
])
def test_ct830_limpar_nota(nt, entrada, saida):
    """CT-830 | Limpeza do texto | quebras padronizadas, controles e marcas de direção removidos; acentos, emoji (inclusive composto) e escritas não latinas preservados."""
    assert nt.ns["limpar_nota"](entrada) == saida


def test_ct831_limite_de_4000_caracteres(nt):
    """CT-831 | 4000 caracteres passam; 4001 são recusados com 422."""
    p = nt.presenca_do()
    assert nt.nota("a" * 4000, p)[0] == 200
    assert nt.nota("a" * 4001, p)[0] == 422
    assert len(nt.linhas_notas()[0].texto) == 4000


def test_ct832_byte_nulo_nao_chega_ao_banco(nt):
    """CT-832 | Texto com byte nulo (o PostgreSQL recusa) | é removido antes de gravar."""
    p = nt.presenca_do()
    assert nt.nota("ab\x00cd", p)[1]["nota"]["texto"] == "abcd"


def test_ct833_texto_so_com_controles_vira_vazio(nt):
    """CT-833 | Texto só com caracteres de controle | tratado como vazio (não grava nota em branco)."""
    p = nt.presenca_do()
    assert nt.nota("\x00\x07\x1b", p)[1] == {"ok": True, "nota": None} and nt.linhas_notas() == []


def test_ct834_titulo_do_evento_e_copiado_no_momento_da_primeira_anotacao(nt):
    """CT-834 | O professor muda o título depois | a nota guarda o título de quando foi criada (e a atualização não o troca)."""
    p = nt.presenca_do()
    nt.nota("v1", p)
    nt.aula.titulo = "Título novo"
    assert nt.nota("v2", p)[1]["nota"]["titulo"] == "Algoritmos"


def test_ct835_tipo_nulo_vira_aula(nt):
    """CT-835 | Evento antigo sem tipo | a lista mostra 'aula'."""
    nt.aula.tipo = None
    nt.nota("x", nt.presenca_do())
    assert nt.notas()[1][0]["tipo"] == "aula"


def test_ct836_limite_de_60_salvamentos_em_10_minutos(nt):
    """CT-836 | 61º salvamento em 10 min | 429; passada a janela, volta a salvar."""
    p = nt.presenca_do()
    for i in range(60):
        assert nt.nota(f"v{i}", p)[0] == 200
    assert nt.nota("v61", p)[0] == 429
    nt.avancar(601)
    assert nt.nota("v62", p)[0] == 200


def test_ct837_limite_e_por_aluno(nt):
    """CT-837 | Um aluno estourando o limite não bloqueia os outros."""
    p = nt.presenca_do()
    for i in range(61):
        nt.nota(f"v{i}", p)
    outro = nt.novo_aluno(2)
    assert nt.nota("meu", nt.presenca_do(outro), aluno=outro)[0] == 200


# =====================================================================================================
# BLOCO E - PDFs
# =====================================================================================================
def test_ct840_pdf_de_uma_anotacao(nt):
    """CT-840 | PDF de uma anotação | arquivo PDF válido, com nome, título, texto e o aviso de que NÃO vale como comprovante."""
    nt.aluno.nome = "Maria Souza"
    nt.aula.disciplina = "Estruturas de Dados"
    p = nt.presenca_do(quando=_dt(2026, 11, 11, 15, 5, 0, tzinfo=_tz.utc))
    n = nt.nota("Pilhas e filas.\nSegunda linha.", p)[1]["nota"]
    st, resp = nt.pdf_uma(n["id"])
    assert st == 200 and resp.media_type == "application/pdf" and resp.body[:5] == b"%PDF-"
    assert resp.headers["Content-Disposition"] == f'attachment; filename="anotacao-evento-{p.id}.pdf"'
    assert resp.headers["Cache-Control"] == "no-store"
    t = " ".join(texto_pdf(resp.body).split())
    for esperado in ("Maria Souza", "1 anotação", "Algoritmos", "disciplina Estruturas de Dados", "presença em 11/11/2026 às 12:05",
                     "Pilhas e filas.", "Segunda linha.", "Não vale como comprovante de presença", "horário de Brasília"):
        assert esperado in t, esperado


def test_ct841_pdf_da_anotacao_nao_se_confunde_com_comprovante(nt):
    """CT-841 | O PDF da anotação nunca traz código de validação nem autenticação (para ninguém usar como comprovante)."""
    nt.aula.codigo_validacao = "KQ72Z"
    p = nt.presenca_do()
    n = nt.nota("texto", p)[1]["nota"]
    t = texto_pdf(nt.pdf_uma(n["id"])[1].body)
    assert "KQ72Z" not in t and "Autenticação" not in t and f"{p.id}-" not in t


def test_ct842_pdf_de_todas_em_ordem_e_com_contagem(nt):
    """CT-842 | PDF de todas | mais recente primeiro, com o total no resumo."""
    for i in range(3):
        aula = nt.nova_aula(nt.outro(), titulo=f"Evento{i}")
        nt.nota(f"texto{i}", nt.presenca_do(quando=_dt(2026, 11, 1 + i, 12, tzinfo=_tz.utc), aula=aula))
    st, resp = nt.pdf_todas()
    t = " ".join(texto_pdf(resp.body).split())
    assert st == 200 and "3 anotações" in t and t.index("Evento2") < t.index("Evento1") < t.index("Evento0")
    assert resp.headers["Content-Disposition"] == 'attachment; filename="minhas-anotacoes.pdf"'


def test_ct843_pdf_de_todas_sem_anotacoes_404(nt):
    """CT-843 | Sem nenhuma anotação | 404 com mensagem clara."""
    st, det = nt.pdf_todas()
    assert st == 404 and det == "Você ainda não tem anotações."


def test_ct844_pdf_de_nota_inexistente_404(nt):
    """CT-844 | PDF de anotação que não existe | 404."""
    assert nt.pdf_uma(9999)[0] == 404


def test_ct845_limite_de_30_pdfs_compartilhado(nt):
    """CT-845 | PDFs de anotações: 30 por 10 min no total (uma nota + todas)."""
    n = nt.nota("x", nt.presenca_do())[1]["nota"]
    for i in range(15):
        assert nt.pdf_uma(n["id"])[0] == 200
        assert nt.pdf_todas()[0] == 200
    assert nt.pdf_uma(n["id"])[0] == 429 and nt.pdf_todas()[0] == 429
    nt.avancar(601)
    assert nt.pdf_uma(n["id"])[0] == 200


def test_ct846_sem_reportlab_503(nt):
    """CT-846 | Servidor sem a biblioteca de PDF | 503 com orientação."""
    n = nt.nota("x", nt.presenca_do())[1]["nota"]

    def sem_pdf(_d):
        raise ImportError("reportlab")
    nt.ns["gerar_pdf_anotacoes"] = sem_pdf
    for r in (nt.pdf_uma(n["id"]), nt.pdf_todas()):
        assert r[0] == 503 and "PDF" in r[1]


def test_ct847_texto_longo_vira_varias_paginas_sem_perder_nada(nt):
    """CT-847 | Anotação de 4.000 caracteres | PDF com mais de uma página quando preciso, texto inteiro presente, rodapé em todas."""
    palavras = [f"palavra{i:04d}" for i in range(500)]
    texto = " ".join(palavras)[:4000]                                       # 4.000 caracteres
    nt.nota(texto, nt.presenca_do())
    t = texto_pdf(nt.pdf_todas()[1].body)
    corpo = " ".join(t.split())
    ultima = texto.split()[-2]                                              # a última palavra inteira antes do corte
    assert "palavra0000" in corpo and ultima in corpo
    assert t.count("Não vale como comprovante") == len(pypdf.PdfReader(io.BytesIO(nt.pdf_todas()[1].body)).pages)


def test_ct848_muitas_anotacoes_paginam_e_o_rodape_numera(nt):
    """CT-848 | 40 anotações de 3.000 caracteres | várias páginas, 'Página N' em cada uma, nenhum texto cortado."""
    for i in range(40):
        aula = nt.nova_aula(nt.outro(), titulo=f"Evento{i:02d}")
        nt.nota(("linha de texto numero %d. " % i) * 120, nt.presenca_do(aula=aula, quando=_dt(2026, 10, 1, 12, tzinfo=_tz.utc) + _td(days=i)))
    resp = nt.pdf_todas()[1]
    leitor = pypdf.PdfReader(io.BytesIO(resp.body))
    n = len(leitor.pages)
    assert n > 5
    for k, pag in enumerate(leitor.pages, start=1):
        assert f"Página {k}" in pag.extract_text()
    todo = " ".join(texto_pdf(resp.body).split())
    for i in range(40):
        assert f"Evento{i:02d}" in todo
    for pagina, texto, x, fonte, tam, y in todos_os_fragmentos(resp.body):
        assert not (60 < y < 76), f"p.{pagina}: '{texto[:20]}' invade a área do rodapé"


def test_ct849_palavra_enorme_nao_sai_pela_lateral(nt):
    """CT-849 | Link de 300 caracteres sem espaços na anotação e no título | nenhuma linha passa da margem direita da página."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    comprido = "https://exemplo.com/" + "a" * 280
    nt.aula.titulo = ("https://meet.google.com/" + "b" * 110)[:140]
    nt.nota("veja " + comprido + " e depois " + comprido, nt.presenca_do())
    corpo = nt.pdf_todas()[1].body
    for pagina, texto, x, fonte, tam, y in todos_os_fragmentos(corpo):
        f = fonte if fonte in ("Helvetica", "Helvetica-Bold") else "Helvetica"
        assert x + stringWidth(texto, f, tam) <= 595.28 - 20, f"p.{pagina}: '{texto[:25]}...' sai da página"


def test_ct850_caracteres_incomuns_nao_derrubam_o_pdf(nt):
    """CT-850 | Emoji, japonês, árabe e acentos na anotação, no título e no nome | PDF gerado sem erro (os símbolos fora do Latin-1 viram '?')."""
    nt.aluno.nome = "José Ñandú 😀"
    nt.aula.titulo = "Aula 日本語 العربية 😀"
    n = nt.nota("Olá 😀 日本語 العربية ção ñ", nt.presenca_do())[1]["nota"]
    for r in (nt.pdf_uma(n["id"]), nt.pdf_todas()):
        assert r[0] == 200 and r[1].body[:5] == b"%PDF-"
    assert "ção" in texto_pdf(nt.pdf_uma(n["id"])[1].body)


def test_ct851_titulo_de_140_caracteres_e_disciplina_nula(nt):
    """CT-851 | Título no limite e disciplina vazia | PDF normal, sem 'None' escrito."""
    nt.aula.titulo, nt.aula.disciplina = "T" * 140, None
    n = nt.nota("ok", nt.presenca_do())[1]["nota"]
    t = texto_pdf(nt.pdf_uma(n["id"])[1].body)
    assert "None" not in t and "disciplina" not in t


def test_ct852_palestra_aparece_como_palestra(nt):
    """CT-852 | Evento do tipo palestra | o PDF diz 'Palestra'."""
    nt.aula.tipo = "palestra"
    n = nt.nota("ok", nt.presenca_do())[1]["nota"]
    assert "Palestra" in texto_pdf(nt.pdf_uma(n["id"])[1].body)


def test_ct853_horario_do_pdf_e_o_de_brasilia(nt):
    """CT-853 | Presença às 02:30 UTC | o PDF mostra 23:30 do dia anterior e a hora de emissão também em Brasília."""
    n = nt.nota("ok", nt.presenca_do(quando=_dt(2026, 11, 11, 2, 30, tzinfo=_tz.utc)))[1]["nota"]
    nt.agora = _dt(2026, 11, 12, 1, 0, tzinfo=_tz.utc)
    t = " ".join(texto_pdf(nt.pdf_uma(n["id"])[1].body).split())
    assert "10/11/2026 às 23:30" in t and "Gerado em 11/11/2026 22:00" in t


def test_ct854_pdf_grande_gera_em_tempo_razoavel(nt):
    """CT-854 | 200 anotações de 4.000 caracteres (o pior caso) | o PDF sai em poucos segundos (medida para a análise de carga)."""
    texto = ("Anotação longa com bastante texto para ocupar linhas. " * 80)[:4000]
    for i in range(200):
        aula = nt.nova_aula(nt.outro(), titulo=f"Evento {i}")
        nt.db.add(NotaAluno(aluno_id=nt.aluno.id, presenca_id=1000 + i, evento_titulo=f"Evento {i}", evento_tipo="aula",
                            evento_disciplina="D", evento_em=_dt(2026, 1, 1, tzinfo=_tz.utc) + _td(days=i), texto=texto,
                            criado_em=nt.agora, atualizado_em=nt.agora))
    nt.db.commit()
    t0 = _t.perf_counter()
    st, resp = nt.pdf_todas()
    dur = _t.perf_counter() - t0
    paginas = len(pypdf.PdfReader(io.BytesIO(resp.body)).pages)
    print(f"\n[F9] PDF de 200 anotações x 4000 caracteres: {dur:.2f}s, {paginas} páginas, {len(resp.body) / 1024:.0f} KB")
    assert st == 200 and dur < 15 and paginas > 100


def test_ct855_pdf_leva_o_nome_de_quem_baixou(nt):
    """CT-855 | Dois alunos com anotações | cada PDF traz o nome do próprio dono (nunca o de outro)."""
    outro = nt.novo_aluno(2, nome="Beatriz Prado")
    nt.nota("de Ana", nt.presenca_do())
    n2 = nt.nota("de Beatriz", nt.presenca_do(outro), aluno=outro)[1]["nota"]
    t = " ".join(texto_pdf(nt.pdf_uma(n2["id"], aluno=outro)[1].body).split())
    assert "Beatriz Prado" in t and "Aluno 1" not in t
    t_todas = " ".join(texto_pdf(nt.pdf_todas(aluno=outro)[1].body).split())
    assert "Beatriz Prado" in t_todas and "de Ana" not in t_todas
