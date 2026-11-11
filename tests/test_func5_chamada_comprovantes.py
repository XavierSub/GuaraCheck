# -*- coding: utf-8 -*-
"""
QA - Funcionalidade 5: CHAMADA E COMPROVANTES (Guará Check)
Painel do professor (presentes, tentativas recusadas, ausentes), conferência da autenticação do comprovante, lista de
comprovantes do aluno, PDF do comprovante (conteúdo real, gerado com o reportlab) e dados do perfil.

Como nas funcionalidades anteriores: o código REAL do app.py é extraído por ast e executado contra um banco em memória
(harness_app.py). Os PDFs são gerados DE VERDADE (reportlab) e lidos de volta (pypdf) para conferir o conteúdo.
NÃO é executado aqui: SQLAlchemy/PostgreSQL de verdade e o download no navegador (veja tests/navegador).

Uso:  pip install pytest pydantic pyjwt openpyxl reportlab pypdf
      pytest test_func5_chamada_comprovantes.py -v
"""
import types as _types
from datetime import datetime as _dt

from harness_app import *  # noqa: F401,F403
from test_func4_presenca import CtxPr, IP_PUBLICO, LAT, LON, dev, ponto  # noqa: F401

pypdf = pytest.importorskip("pypdf")


class CtxCh(CtxPr):
    """CtxPr + atalhos para as rotas do painel do professor e do comprovante do aluno."""

    # --- usuários ---
    def eu(self, p=None):
        p = p or self.prof
        return {"id": p.id, "role": "professor", "nome": p.nome}

    # --- dados prontos ---
    def semear_presenca(self, aluno, aula=None, dist=5.0, alertas=None, quando=None, device=None):
        """Presença direta no banco (barato). Para o fluxo real use self.registrar()."""
        aula = aula or self.aula
        p = Presenca(aula_id=aula.id, aluno_id=aluno.id, latitude=LAT, longitude=LON, distancia_m=dist, precisao_m=10.0,
                     device_id=device or f"semente-{aula.id}-{aluno.id}-0123456789", fp_hash="abcdef0123456789",
                     ip="177.1.1.1", alertas=alertas, registrado_em=quando)
        self.db.add(p)
        self.db.commit()
        return p

    def semear_tentativa(self, aluno, motivo="fora_raio", aula=None, quando=None, **kw):
        d = dict(aula_id=(aula or self.aula).id, aluno_id=aluno.id, motivo=motivo, detalhe="A 200 m da sala", distancia_m=200.0,
                 quantidade=1, ultima_em=quando)
        d.update(kw)
        t = Tentativa(**d)
        self.db.add(t)
        self.db.commit()
        return t

    # --- chamadas às rotas reais ---
    def lista(self, aula=None, apos_id=0, p=None):
        return status_de(self.ns["presencas_da_aula"], (aula or self.aula).id, apos_id, self.eu(p), self.db)

    def tent(self, aula=None, p=None):
        return status_de(self.ns["tentativas_da_aula"], (aula or self.aula).id, self.eu(p), self.db)

    def aus(self, aula=None, p=None):
        return status_de(self.ns["ausentes_da_aula"], (aula or self.aula).id, self.eu(p), self.db)

    def conferir(self, codigo, aula=None, p=None):
        return status_de(self.ns["conferir_comprovante"], (aula or self.aula).id, codigo, self.eu(p), self.db)

    def meus(self, aluno=None, **kw):
        return status_de(self.ns["minhas_presencas"], self.sessao(aluno), self.db, **kw)

    def pdf(self, presenca_id, aluno=None):
        return status_de(self.ns["comprovante_pdf"], presenca_id, self.sessao(aluno), self.db)

    def perfil(self, aluno=None):
        return status_de(self.ns["meu_perfil"], self.sessao(aluno), self.db)

    def autenticacao(self, p, aula=None):
        return f"{p.id}-{self.ns['assinatura_presenca'](p, aula or self.aula)}"


@pytest.fixture
def ch():
    return CtxCh()


def texto_pdf(corpo: bytes) -> str:
    leitor = pypdf.PdfReader(io.BytesIO(corpo))
    return "\n".join(p.extract_text() for p in leitor.pages)


# =====================================================================================================
# BLOCO A - Acesso: só o professor dono da chamada
# =====================================================================================================
ROTAS_PROF = ["lista", "tent", "aus"]


@pytest.mark.parametrize("rota", ROTAS_PROF)
def test_ct501_professor_de_outro_evento_recebe_404(ch, rota):
    """CT-501 | Outro professor tenta ver presentes / tentativas / ausentes da chamada alheia | 404 (não revela que existe)."""
    r = getattr(ch, rota)(p=ch.ana)
    assert r[0] == 404, r


def test_ct502_outro_professor_nao_confere_comprovante(ch):
    """CT-502 | Outro professor tenta conferir comprovante de chamada alheia | 404."""
    al = ch.novo_aluno(2)
    p = ch.semear_presenca(al)
    assert ch.conferir(ch.autenticacao(p), p=ch.ana)[0] == 404


@pytest.mark.parametrize("papel", ["aluno", "admin"])
def test_ct503_so_professor_nas_rotas_do_painel(ch, papel):
    """CT-503 | Aluno ou coordenação nas rotas do painel do professor | 403."""
    assert status_de(ch.ns["so_professor"], {"id": 1, "role": papel, "nome": "X"})[0] == 403


@pytest.mark.parametrize("rota", ROTAS_PROF)
def test_ct504_chamada_inexistente_404(ch, rota):
    """CT-504 | Chamada que não existe | 404."""
    ch.aula = _types.SimpleNamespace(id=9999)
    assert getattr(ch, rota)()[0] == 404


# =====================================================================================================
# BLOCO B - Lista de presentes (painel em tempo real)
# =====================================================================================================
def test_ct505_lista_vazia(ch):
    """CT-505 | Chamada sem ninguém | total 0, ativa, sem linhas, limite informado."""
    r = ch.lista()
    assert r[0] == 200 and r[1]["total"] == 0 and r[1]["ativa"] is True and r[1]["presencas"] == []
    assert r[1]["limite"] == ch.ns["LIMITE_PRESENCAS_POR_CONSULTA"] == 2000


def test_ct506_linha_com_os_dados_do_aluno(ch):
    """CT-506 | Presença feita pelo fluxo real | nome, matrícula, e-mail, curso, período, distância (1 casa), hora ISO."""
    assert ch.registrar(norte=12.34567)[0] == 200
    (l,) = ch.lista()[1]["presencas"]
    assert l["nome"] == "Aluno 1" and l["matricula"] == ch.aluno.matricula and l["email"] == ch.aluno.email
    assert l["curso"] == "Gestão da TI" and l["periodo"] == 2 and l["alertas"] is None
    assert l["distancia_m"] == round(l["distancia_m"], 1) and 12 < l["distancia_m"] < 13
    assert l["registrado_em"].startswith(ch.agora.strftime("%Y-%m-%d")) and l["id"] == ch.presencas()[0].id


def test_ct507_nao_vaza_dados_sensiveis(ch):
    """CT-507 | O painel do professor NÃO recebe coordenadas, aparelho, impressão digital, IP nem senha."""
    ch.registrar()
    (l,) = ch.lista()[1]["presencas"]
    assert set(l) == {"id", "nome", "matricula", "email", "curso", "periodo", "distancia_m", "registrado_em", "alertas"}
    assert set(ch.lista()[1]) == {"ativa", "total", "limite", "presencas"}


def test_ct508_ordem_e_busca_incremental(ch):
    """CT-508 | Lista por ordem de chegada; apos_id traz só as novas; o total conta todas."""
    alunos = [ch.novo_aluno(i) for i in range(2, 7)]
    ps = [ch.semear_presenca(a) for a in alunos]
    r = ch.lista()[1]
    assert [x["id"] for x in r["presencas"]] == [p.id for p in ps] and r["total"] == 5
    r2 = ch.lista(apos_id=ps[2].id)[1]
    assert [x["id"] for x in r2["presencas"]] == [ps[3].id, ps[4].id] and r2["total"] == 5
    assert ch.lista(apos_id=ps[-1].id)[1]["presencas"] == []
    assert len(ch.lista(apos_id=-10)[1]["presencas"]) == 5


def test_ct509_limite_por_consulta_e_continuacao(ch):
    """CT-509 | Mais presenças que o limite: cada consulta traz um lote e o apos_id continua de onde parou (sem perder nem repetir)."""
    ch.ns["LIMITE_PRESENCAS_POR_CONSULTA"] = 5
    ps = [ch.semear_presenca(ch.novo_aluno(i)) for i in range(2, 14)]            # 12 presenças
    vistos, apos, tamanhos = [], 0, []
    for _ in range(10):
        r = ch.lista(apos_id=apos)[1]
        assert r["total"] == 12 and r["limite"] == 5
        if not r["presencas"]:
            break
        tamanhos.append(len(r["presencas"]))
        vistos += [x["id"] for x in r["presencas"]]
        apos = r["presencas"][-1]["id"]
    assert vistos == [p.id for p in ps]
    assert tamanhos == [5, 5, 2]                                                   # o limite vale de verdade em cada consulta


def test_ct510_so_presencas_da_chamada_pedida(ch):
    """CT-510 | Presenças de outra chamada (de outro professor) não aparecem."""
    outra = ch.nova_aula(ch.ana)
    ch.semear_presenca(ch.novo_aluno(2), outra)
    ch.semear_presenca(ch.novo_aluno(3))
    r = ch.lista()[1]
    assert r["total"] == 1 and len(r["presencas"]) == 1


def test_ct511_estado_ativa_acompanha_encerramento_e_prazo():
    """CT-511 | 'ativa' vira falso ao encerrar e ao passar do prazo, mesmo com o marcador ativa=True no banco."""
    c = CtxCh(AULA_MAX_HORAS=12)
    assert c.lista()[1]["ativa"] is True
    c.avancar(12 * 3600 + 60)
    assert c.lista()[1]["ativa"] is False
    c2 = CtxCh()
    c2.aula.ativa = False
    assert c2.lista()[1]["ativa"] is False


def test_ct512_aluno_sem_curso_ou_matricula(ch):
    """CT-512 | Aluno sem curso e sem matrícula aparece com campos nulos (sem erro 500)."""
    a = ch.novo_aluno(2, curso_id=None, matricula=None, periodo=None)
    ch.semear_presenca(a)
    (l,) = ch.lista()[1]["presencas"]
    assert l["curso"] is None and l["matricula"] is None and l["periodo"] is None


def test_ct513_alertas_chegam_ao_professor(ch):
    """CT-513 | Sinais de risco gravados no check-in aparecem na linha (o professor decide)."""
    ch.registrar(precisao_m=0.5)
    (l,) = ch.lista()[1]["presencas"]
    assert "precisão irreal" in l["alertas"]


def test_ct514_chamada_encerrada_continua_consultavel(ch):
    """CT-514 | Depois de encerrar, a lista continua disponível (comprovação posterior)."""
    ch.semear_presenca(ch.novo_aluno(2))
    ch.aula.ativa = False
    r = ch.lista()[1]
    assert r["ativa"] is False and r["total"] == 1 and len(r["presencas"]) == 1


# =====================================================================================================
# BLOCO C - Tentativas recusadas
# =====================================================================================================
def test_ct515_sem_tentativas(ch):
    """CT-515 | Nenhuma tentativa | lista vazia."""
    assert ch.tent()[1] == {"itens": []}


def test_ct516_item_da_tentativa(ch):
    """CT-516 | Tentativa registrada pelo fluxo real (fora do raio) | nome, matrícula, motivo, detalhe, distância, contador."""
    ch.registrar(norte=200)
    ch.registrar(norte=300)
    (t,) = ch.tent()[1]["itens"]
    assert t["nome"] == "Aluno 1" and t["motivo"] == "fora_raio" and t["quantidade"] == 2
    assert t["curso"] == "Gestão da TI" and 299 < t["distancia_m"] < 301 and "300 m" in t["detalhe"]
    assert t["registrou"] is False


def test_ct517_nao_vaza_coordenadas_nem_ip(ch):
    """CT-517 | A tentativa NÃO traz coordenadas, precisão nem IP do aluno."""
    ch.registrar(norte=200)
    (t,) = ch.tent()[1]["itens"]
    assert set(t) == {"id", "nome", "matricula", "curso", "motivo", "detalhe", "distancia_m", "quantidade", "ultima_em", "registrou"}


def test_ct518_registrou_depois(ch):
    """CT-518 | Aluno tentou longe e depois conseguiu: 'registrou' = True (o professor não o trata como faltoso)."""
    ch.registrar(norte=200)
    assert ch.tent()[1]["itens"][0]["registrou"] is False
    ch.registrar(norte=5)
    assert ch.tent()[1]["itens"][0]["registrou"] is True


def test_ct519_mais_recente_primeiro(ch):
    """CT-519 | Ordenadas por última tentativa, da mais recente para a mais antiga."""
    t0 = ch.agora
    for i, minutos in enumerate((5, 1, 9, 3), start=2):
        ch.semear_tentativa(ch.novo_aluno(i), quando=t0 + timedelta(minutes=minutos))
    ordem = [t["ultima_em"] for t in ch.tent()[1]["itens"]]
    assert ordem == sorted(ordem, reverse=True)


def test_ct520_teto_de_300_tentativas(ch):
    """CT-520 | 350 alunos recusados: a tela recebe as 300 mais recentes (o painel não fica pesado)."""
    for i in range(2, 352):
        ch.semear_tentativa(ch.novo_aluno(i), quando=ch.agora + timedelta(seconds=i))
    itens = ch.tent()[1]["itens"]
    assert len(itens) == 300 and itens[0]["nome"] == "Aluno 351"


def test_ct521_so_tentativas_da_chamada_pedida(ch):
    """CT-521 | Tentativas de outra chamada não aparecem."""
    outra = ch.nova_aula(ch.ana)
    ch.semear_tentativa(ch.novo_aluno(2), aula=outra)
    assert ch.tent()[1]["itens"] == []


# =====================================================================================================
# BLOCO D - Alunos ausentes
# =====================================================================================================
def test_ct522_chamada_aberta_a_todos_nao_lista_ausentes(ch):
    """CT-522 | Chamada sem curso definido | disponivel=False com o motivo."""
    r = ch.aus()[1]
    assert r["disponivel"] is False and r["total"] == 0 and r["itens"] == [] and "curso específico" in r["motivo"]


def test_ct523_ausentes_do_curso(ch):
    """CT-523 | Chamada do curso: lista quem é do curso e ainda não registrou, em ordem alfabética."""
    ch.aula.curso_id = ch.curso.id
    nomes = ["Zélia Dias", "Ana Lima", "Bruno Reis", "Carla Nunes"]
    alunos = [ch.novo_aluno(i, nome=n) for i, n in zip(range(2, 6), nomes)]
    ch.semear_presenca(alunos[2])                                                  # Bruno registrou
    r = ch.aus()[1]
    assert r["disponivel"] is True and r["total"] == 4 and r["limite"] == 500       # Bruno registrou; sobra o aluno do contexto + 3
    assert [x["nome"] for x in r["itens"]] == ["Aluno 1", "Ana Lima", "Carla Nunes", "Zélia Dias"]


def test_ct524_inativos_nao_contam_e_status_nulo_conta(ch):
    """CT-524 | Aluno inativo não é cobrado; aluno com status nulo (conta antiga) é."""
    ch.aula.curso_id = ch.curso.id
    ch.aluno.status = "inativo"
    nulo = ch.novo_aluno(2)
    nulo.status = None                                                              # (o banco aplica o padrão só no INSERT)
    r = ch.aus()[1]
    assert [x["nome"] for x in r["itens"]] == [nulo.nome] and r["total"] == 1


def test_ct525_periodo_da_chamada(ch):
    """CT-525 | Chamada do 3º período: só alunos do 3º aparecem como ausentes."""
    ch.aula.curso_id, ch.aula.periodo = ch.curso.id, 3
    do3 = ch.novo_aluno(2, periodo=3)
    ch.novo_aluno(3, periodo=2)
    ch.novo_aluno(4, periodo=None)
    assert [x["nome"] for x in ch.aus()[1]["itens"]] == [do3.nome]


def test_ct526_outro_curso_nao_entra(ch):
    """CT-526 | Alunos de outro curso (ou sem curso) não são ausentes."""
    outro = Curso(nome="Direito")
    ch.db.add(outro)
    ch.db.commit()
    ch.aula.curso_id = outro.id
    ch.novo_aluno(2, curso_id=None)
    assert ch.aus()[1]["itens"] == []                                               # o aluno 1 e o 2 não são de Direito
    do_direito = ch.novo_aluno(3, curso_id=outro.id)
    assert [x["nome"] for x in ch.aus()[1]["itens"]] == [do_direito.nome]


def test_ct527_quem_registrou_sai_da_lista_e_quem_so_tentou_continua(ch):
    """CT-527 | Registrou: não é ausente. Só tentou (recusado): continua ausente."""
    ch.aula.curso_id = ch.curso.id
    b = ch.novo_aluno(2)
    ch.registrar()
    ch.registrar(aluno=b, norte=900, device_id=dev(2))                              # recusado
    assert [x["nome"] for x in ch.aus()[1]["itens"]] == [b.nome]


def test_ct528_teto_de_500_com_total_real(ch):
    """CT-528 | 600 ausentes: a lista traz 500 e informa o total real (o CSV traz todos)."""
    ch.aula.curso_id = ch.curso.id
    for i in range(2, 601):
        ch.novo_aluno(i)
    r = ch.aus()[1]
    assert r["total"] == 600 and len(r["itens"]) == 500


def test_ct529_ausente_nao_expoe_dados_alem_do_necessario(ch):
    """CT-529 | Cada ausente traz só nome, matrícula, e-mail e período (sem senha, curso_id nem status)."""
    ch.aula.curso_id = ch.curso.id
    (a,) = ch.aus()[1]["itens"]
    assert set(a) == {"nome", "matricula", "email", "periodo"}


# =====================================================================================================
# BLOCO E - Autenticação do comprovante (HMAC) e conferência pelo professor
# =====================================================================================================
def test_ct530_assinatura_formato_e_determinismo(ch):
    """CT-530 | Assinatura: 10 hexadecimais maiúsculos, igual em chamadas repetidas."""
    p = ch.semear_presenca(ch.aluno)
    a1, a2 = ch.ns["assinatura_presenca"](p, ch.aula), ch.ns["assinatura_presenca"](p, ch.aula)
    assert a1 == a2 and re.fullmatch(r"[0-9A-F]{10}", a1)


def test_ct531_assinatura_depende_de_cada_campo(ch):
    """CT-531 | Mudar presença, aluno, evento, hora (1 s) ou código do evento muda a assinatura."""
    sig = ch.ns["assinatura_presenca"]
    t0 = ch.agora.replace(microsecond=0)
    base = sig(_types.SimpleNamespace(id=7, aluno_id=3, registrado_em=t0), _types.SimpleNamespace(id=11, codigo_validacao="ABC1"))
    variantes = [
        (_types.SimpleNamespace(id=8, aluno_id=3, registrado_em=t0), _types.SimpleNamespace(id=11, codigo_validacao="ABC1")),
        (_types.SimpleNamespace(id=7, aluno_id=4, registrado_em=t0), _types.SimpleNamespace(id=11, codigo_validacao="ABC1")),
        (_types.SimpleNamespace(id=7, aluno_id=3, registrado_em=t0), _types.SimpleNamespace(id=12, codigo_validacao="ABC1")),
        (_types.SimpleNamespace(id=7, aluno_id=3, registrado_em=t0 + timedelta(seconds=1)), _types.SimpleNamespace(id=11, codigo_validacao="ABC1")),
        (_types.SimpleNamespace(id=7, aluno_id=3, registrado_em=t0), _types.SimpleNamespace(id=11, codigo_validacao="ABC2")),
        (_types.SimpleNamespace(id=7, aluno_id=3, registrado_em=t0), _types.SimpleNamespace(id=11, codigo_validacao=None)),
    ]
    assinaturas = [sig(p, a) for p, a in variantes]
    assert base not in assinaturas and len(set(assinaturas)) == len(assinaturas)


def test_ct532_microssegundos_nao_mudam_a_assinatura(ch):
    """CT-532 | Fração de segundo não altera a assinatura (o banco pode arredondar a hora)."""
    sig = ch.ns["assinatura_presenca"]
    t0 = ch.agora.replace(microsecond=0)
    a = _types.SimpleNamespace(id=11, codigo_validacao="ABC1")
    assert sig(_types.SimpleNamespace(id=7, aluno_id=3, registrado_em=t0), a) == \
        sig(_types.SimpleNamespace(id=7, aluno_id=3, registrado_em=t0 + timedelta(microseconds=999_999)), a)


def test_ct533_assinatura_depende_da_chave_do_servidor(ch):
    """CT-533 | Com outra SECRET_KEY a assinatura é outra (quem não tem a chave não consegue forjar)."""
    p = ch.semear_presenca(ch.aluno)
    outra = CtxCh(SECRET_KEY="outra-chave-de-teste-com-mais-de-32-caracteres")
    assert ch.ns["assinatura_presenca"](p, ch.aula) != outra.ns["assinatura_presenca"](p, ch.aula)


def test_ct534_comprovante_autentico(ch):
    """CT-534 | Autenticação verdadeira | valido=True com nome, matrícula, curso, hora e o código do evento."""
    ch.aula.codigo_validacao = "K7Q2"
    ch.registrar()
    p = ch.presencas()[0]
    r = ch.conferir(ch.autenticacao(p))
    assert r[0] == 200 and r[1]["valido"] is True
    assert r[1]["nome"] == "Aluno 1" and r[1]["matricula"] == ch.aluno.matricula and r[1]["curso"] == "Gestão da TI"
    assert r[1]["codigo_validacao"] == "K7Q2" and r[1]["registrado_em"].startswith(ch.agora.strftime("%Y-%m-%d"))


@pytest.mark.parametrize("formato", ["  {id} - {sig} ", "{id}-{sig_minusculo}", "{id} -{sig}", "0{id}-{sig}"])
def test_ct535_formatos_tolerados_na_colagem(ch, formato):
    """CT-535 | Espaços, hexadecimal minúsculo e zero à esquerda (colagem do PDF) | continua valendo."""
    p = ch.semear_presenca(ch.aluno)
    sig = ch.ns["assinatura_presenca"](p, ch.aula)
    r = ch.conferir(formato.format(id=p.id, sig=sig, sig_minusculo=sig.lower()))
    assert r[0] == 200 and r[1]["valido"] is True, r


@pytest.mark.parametrize("codigo", ["", "   ", "1042", "1042-", "-9F3A7C21B4", "1042-9F3A7C21B", "1042-9F3A7C21B4A", "1042-ZZZZZZZZZZ",
                                    "abc-9F3A7C21B4", "1042_9F3A7C21B4", "1042-9F3A7C21B4\n1043-9F3A7C21B4", "1" * 13 + "-9F3A7C21B4"])
def test_ct536_formato_invalido_e_422(ch, codigo):
    """CT-536 | Colagem fora do formato 'número-10 hexadecimais' | 422 com orientação."""
    r = ch.conferir(codigo)
    assert r[0] == 422 and "Formato inválido" in r[1], r


def test_ct537_assinatura_adulterada_ou_de_outro_evento(ch):
    """CT-537 | 1 caractere trocado, presença de outro evento ou id inexistente | valido=False (mesma resposta, sem dica)."""
    p = ch.semear_presenca(ch.aluno)
    sig = ch.ns["assinatura_presenca"](p, ch.aula)
    errada = sig[:-1] + ("0" if sig[-1] != "0" else "1")
    assert ch.conferir(f"{p.id}-{errada}")[1] == {"valido": False}
    outra = ch.nova_aula(ch.ana)
    p2 = ch.semear_presenca(ch.novo_aluno(2), outra)
    assert ch.conferir(ch.autenticacao(p2, outra))[1] == {"valido": False}          # presença de outra chamada, conferida nesta
    assert ch.conferir("99999-0123456789")[1] == {"valido": False}


def test_ct538_assinatura_do_pdf_de_aluno_a_nao_serve_para_o_aluno_b(ch):
    """CT-538 | Trocar o número do protocolo mantendo a assinatura do outro aluno | valido=False."""
    a, b = ch.novo_aluno(2), ch.novo_aluno(3)
    pa, pb = ch.semear_presenca(a), ch.semear_presenca(b)
    sig_a = ch.ns["assinatura_presenca"](pa, ch.aula)
    assert ch.conferir(f"{pb.id}-{sig_a}")[1] == {"valido": False}


def test_ct539_limite_de_60_conferencias(ch):
    """CT-539 | 61 conferências em 10 min | 429 na 61ª; depois da janela volta a funcionar (contra tentativa de adivinhar)."""
    p = ch.semear_presenca(ch.aluno)
    cod = ch.autenticacao(p)
    for _ in range(60):
        assert ch.conferir(cod)[0] == 200
    assert ch.conferir(cod)[0] == 429
    ch.avancar(601)
    assert ch.conferir(cod)[0] == 200


def test_ct540_comparacao_em_tempo_constante():
    """CT-540 | Guarda: a assinatura é comparada com hmac.compare_digest (sem vazar por tempo de resposta)."""
    fonte = APP.read_text(encoding="utf-8")
    trecho = fonte[fonte.index("def conferir_comprovante"):fonte.index("def presencas_da_aula")]
    assert "hmac.compare_digest" in trecho


# =====================================================================================================
# BLOCO F - Comprovantes do aluno (lista, PDF e perfil)
# =====================================================================================================
def test_ct541_lista_do_aluno(ch):
    """CT-541 | Comprovantes do aluno: só os dele, mais novo primeiro, com evento, professor e hora."""
    a2 = ch.novo_aluno(2)
    ch.aula.disciplina = "Estruturas de Dados"
    p_dele = ch.semear_presenca(ch.aluno)
    ch.semear_presenca(a2, device="outro-aparelho-0123456789")
    nova = ch.nova_aula(ch.ana, titulo="Palestra IA", tipo="palestra", local_tipo="fora", ativa=False)
    p2 = ch.semear_presenca(ch.aluno, nova)
    r = ch.meus()[1]
    assert [x["id"] for x in r] == [p2.id, p_dele.id]
    assert r[0]["titulo"] == "Palestra IA" and r[0]["tipo"] == "palestra" and r[0]["local_tipo"] == "fora" and r[0]["professor"] == "Ana Prof"
    assert r[1]["disciplina"] == "Estruturas de Dados" and r[1]["professor"] == "Carlos Lima"
    assert set(r[0]) == {"id", "registrado_em", "titulo", "tipo", "local_tipo", "disciplina", "periodo", "professor"}


def test_ct542_comprovante_continua_depois_de_encerrar(ch):
    """CT-542 | Chamada encerrada: o comprovante do aluno continua na lista e o PDF continua saindo."""
    p = ch.semear_presenca(ch.aluno)
    ch.aula.ativa = False
    assert [x["id"] for x in ch.meus()[1]] == [p.id]
    assert ch.pdf(p.id)[0] == 200


def test_ct543_mais_de_20_comprovantes_continuam_acessiveis(ch):
    """CT-543 | Aluno com 45 presenças: a 1ª página traz as 20 mais novas e as mais antigas ficam acessíveis (pagina=2, 3).
    Sem isso o aluno perde o acesso ao comprovante de um evento de 2 meses atrás."""
    ids = []
    for i in range(45):
        aula = ch.nova_aula(ch.outro(), titulo=f"Aula {i}", ativa=False)
        ids.append(ch.semear_presenca(ch.aluno, aula).id)
    p1, p2, p3 = (ch.meus(pagina=n) for n in (1, 2, 3))
    assert p1[0] == p2[0] == p3[0] == 200, (p1, p2, p3)
    assert [x["id"] for x in p1[1]] == ids[::-1][:20]
    assert [x["id"] for x in p2[1]] == ids[::-1][20:40]
    assert [x["id"] for x in p3[1]] == ids[::-1][40:]
    assert ch.meus(pagina=4)[1] == []
    assert [x["id"] for x in ch.meus()[1]] == [x["id"] for x in p1[1]]            # sem pagina = a primeira


def test_ct544_pagina_invalida_nao_quebra(ch):
    """CT-544 | pagina 0, negativa ou gigante | primeira página / vazia, sem erro."""
    ch.semear_presenca(ch.aluno)
    assert len(ch.meus(pagina=0)[1]) == 1 and len(ch.meus(pagina=-3)[1]) == 1
    assert ch.meus(pagina=10**9)[1] == []


def test_ct545_pdf_proprio_com_o_conteudo_certo(ch):
    """CT-545 | PDF do próprio comprovante: é PDF de 1 página com nome, matrícula, curso, evento, professor, protocolo, código e autenticação."""
    ch.aula.codigo_validacao, ch.aula.disciplina, ch.aula.local_descricao = "K7Q2", "Estruturas de Dados", "Sala 12"
    ch.aula.titulo = "Aula de Árvores AVL"
    ch.registrar()
    p = ch.presencas()[0]
    status, resp = ch.pdf(p.id)
    assert status == 200 and resp.media_type == "application/pdf" and resp.body[:5] == b"%PDF-"
    assert resp.headers["Content-Disposition"] == f'attachment; filename="comprovante-presenca-{p.id}.pdf"'
    assert resp.headers["Cache-Control"] == "no-store"
    assert len(pypdf.PdfReader(io.BytesIO(resp.body)).pages) == 1
    t = texto_pdf(resp.body)
    for esperado in ("Aluno 1", ch.aluno.matricula, "Gestão da TI", "Aula de Árvores AVL", "Estruturas de Dados", "Sala 12",
                     "Carlos Lima", str(p.id), "K7Q2", ch.autenticacao(p)):
        assert esperado in t.replace("\n", " "), f"{esperado!r} não está no PDF"


def test_ct546_autenticacao_do_pdf_confere_no_painel(ch):
    """CT-546 | Ida e volta: a autenticação que está no PDF é a que o professor cola e o painel aprova."""
    ch.registrar()
    p = ch.presencas()[0]
    t = texto_pdf(ch.pdf(p.id)[1].body)
    cola = re.search(rf"{p.id}-[0-9A-F]{{10}}", t.replace("\n", ""))
    assert cola, t
    r = ch.conferir(cola.group(0))
    assert r[1]["valido"] is True and r[1]["nome"] == "Aluno 1"


def test_ct547_pdf_de_outro_aluno_e_inexistente_dao_404(ch):
    """CT-547 | PDF da presença de outro aluno ou inexistente | 404 igual nos dois casos (não revela que existe)."""
    outro = ch.novo_aluno(2)
    p_outro = ch.semear_presenca(outro)
    r1, r2 = ch.pdf(p_outro.id), ch.pdf(99999)
    assert r1[0] == r2[0] == 404 and r1[1] == r2[1]


def test_ct548_pdf_gera_codigo_para_evento_antigo_uma_so_vez(ch):
    """CT-548 | Evento criado antes do código existir: o 1º PDF gera um código de 4 dígitos e os próximos usam o mesmo."""
    assert ch.aula.codigo_validacao is None
    p = ch.semear_presenca(ch.aluno)
    cod1 = None
    for _ in range(2):
        t = texto_pdf(ch.pdf(p.id)[1].body)
        assert ch.aula.codigo_validacao and re.fullmatch(r"\d{4}", ch.aula.codigo_validacao)
        cod1 = cod1 or ch.aula.codigo_validacao
        assert ch.aula.codigo_validacao == cod1 and cod1 in t


def test_ct549_pdf_nao_sobrescreve_codigo_existente(ch):
    """CT-549 | Evento que já tem código: o PDF não o troca."""
    ch.aula.codigo_validacao = "ABC9"
    p = ch.semear_presenca(ch.aluno)
    ch.pdf(p.id)
    assert ch.aula.codigo_validacao == "ABC9"


def test_ct550_limite_de_30_pdfs(ch):
    """CT-550 | 31 PDFs em 10 min | 429 no 31º."""
    p = ch.semear_presenca(ch.aluno)
    for _ in range(30):
        assert ch.pdf(p.id)[0] == 200
    assert ch.pdf(p.id)[0] == 429


def test_ct551_pdf_sem_cpf_nem_coordenadas(ch):
    """CT-551 | O PDF não carrega coordenadas, IP, aparelho nem hash (o rodapé promete isso)."""
    ch.registrar(norte=7.7)
    p = ch.presencas()[0]
    t = texto_pdf(ch.pdf(p.id)[1].body)
    for proibido in (f"{p.latitude:.4f}", f"{p.longitude:.4f}", "-18.41", "-49.21", p.device_id, p.fp_hash, IP_PUBLICO):
        assert proibido not in t
    assert "não contém CPF nem coordenadas" in t.replace("\n", " ")


def test_ct552_horario_no_pdf_e_o_de_brasilia(ch):
    """CT-552 | Presença às 15:00 UTC aparece como 12:00:00 (horário de Brasília) no PDF."""
    p = ch.semear_presenca(ch.aluno, quando=datetime(2026, 11, 11, 15, 0, 7, tzinfo=timezone.utc))
    t = texto_pdf(ch.pdf(p.id)[1].body).replace("\n", " ")
    assert "11/11/2026 às 12:00:07" in t and "às 12:00." in t


def test_ct553_textos_extremos_nao_quebram_o_pdf(ch):
    """CT-553 | Título/nome/disciplina/local no tamanho máximo, com emoji, CJK, setas e aspas | PDF de 1 página, sem erro 500."""
    ch.aula.titulo = ("Introdução à Computação Quântica → 日本語 🚀 “aspas” " * 4)[:140]
    ch.aula.disciplina = "D" * 140
    ch.aula.local_descricao = "Bloco " + "Z" * 130
    ch.aluno.nome = "Maria d'Ávila " + "Souza " * 15
    p = ch.semear_presenca(ch.aluno)
    status, resp = ch.pdf(p.id)
    assert status == 200, resp
    assert len(pypdf.PdfReader(io.BytesIO(resp.body)).pages) == 1
    t = texto_pdf(resp.body)
    assert "Maria d'Ávila" in t and "Introdução à Computação" in t


def _fragmentos(corpo: bytes):
    """[(texto, x, y, fonte, tamanho)] de cada trecho de texto da 1ª página do PDF."""
    achados = []

    def visitante(texto, cm, tm, fonte, tam):
        if texto.strip():
            nome = str((fonte or {}).get("/BaseFont", "Helvetica")).lstrip("/")
            achados.append((texto.strip(), tm[4], tm[5], nome, tam))
    pypdf.PdfReader(io.BytesIO(corpo)).pages[0].extract_text(visitor_text=visitante)
    return achados


def test_ct554_conteudo_do_pdf_nao_sai_da_pagina(ch):
    """CT-554 | Com todos os campos longos (com espaços), o último campo ainda fica ACIMA do quadro do código (nada sobreposto)."""
    ch.aula.codigo_validacao = "KQ72Z"
    ch.aula.titulo, ch.aula.disciplina, ch.aula.local_descricao = ("Titulo " * 20)[:140], ("Disc " * 30)[:140], ("Local " * 24)[:140]
    ch.aluno.nome = ("Nome " * 24)[:120]
    p = ch.semear_presenca(ch.aluno)
    frags = _fragmentos(ch.pdf(p.id)[1].body)
    rotulo_y = next(y for t, x, y, f, s in frags if t.startswith("PROTOCOLO"))
    codigo_y = next(y for t, x, y, f, s in frags if t == "KQ72Z")
    assert rotulo_y - 14 > codigo_y + 78, f"'Protocolo' (y={rotulo_y}) encosta no quadro do código (topo={codigo_y + 78})"


def test_ct558_palavra_enorme_nao_sai_pela_lateral_da_pagina(ch):
    """CT-558 | Título/disciplina/local com um endereço comprido SEM espaços (140 caracteres) | nenhuma linha passa da margem direita."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    comprido = "https://meet.google.com/" + "a" * 116
    ch.aula.titulo, ch.aula.disciplina, ch.aula.local_descricao = comprido, comprido, comprido
    p = ch.semear_presenca(ch.aluno)
    status, resp = ch.pdf(p.id)
    assert status == 200
    for texto, x, y, fonte, tam in _fragmentos(resp.body):
        borda = x + stringWidth(texto, fonte if fonte in ("Helvetica", "Helvetica-Bold", "Courier-Bold") else "Helvetica", tam)
        assert borda <= 595.28 - 20, f"'{texto[:30]}...' termina em x={borda:.0f}: sai da página (A4 = 595 pt)"


def test_ct555_sem_reportlab_503(ch):
    """CT-555 | Servidor sem a biblioteca de PDF | 503 com orientação (não um 500 genérico)."""
    p = ch.semear_presenca(ch.aluno)

    def sem_pdf(_d):
        raise ImportError("reportlab")
    ch.ns["gerar_pdf_comprovante"] = sem_pdf
    r = ch.pdf(p.id)
    assert r[0] == 503 and "PDF" in r[1]


def test_ct556_perfil_do_aluno(ch):
    """CT-556 | /me devolve nome, matrícula, e-mail, curso e período (sem senha nem CPF); conta removida | 404."""
    r = ch.perfil()
    assert r[0] == 200 and r[1] == {"nome": "Aluno 1", "matricula": ch.aluno.matricula, "email": ch.aluno.email,
                                    "curso": "Gestão da TI", "periodo": 2}
    sem_curso = ch.novo_aluno(2, curso_id=None, periodo=None)
    assert ch.perfil(sem_curso)[1]["curso"] is None
    sess = ch.sessao()
    ch.db.tables[Aluno].remove(ch.aluno)
    assert status_de(ch.ns["meu_perfil"], sess, ch.db)[0] == 404


def test_ct557_evento_excluido_leva_o_comprovante(ch):
    """CT-557 | Se o professor exclui o evento, as presenças vão junto: o comprovante deixa de existir (404) e some da lista."""
    p = ch.semear_presenca(ch.aluno)
    ch.db.tables[Presenca].remove(p)                  # o que excluir_aula faz (cascata)
    ch.db.tables[Aula].remove(ch.aula)
    assert ch.pdf(p.id)[0] == 404 and ch.meus()[1] == []


def test_ct559_comprovante_de_outro_evento_nao_vale_aqui(ch):
    """CT-559 | Autenticação válida de um evento colada em OUTRO evento do mesmo professor | valido=False (a assinatura prende o evento)."""
    outra = ch.nova_aula(ch.prof, titulo="Palestra IA", ativa=False)
    al = ch.novo_aluno(2)
    p_outra = ch.semear_presenca(al, aula=outra)
    assert ch.conferir(ch.autenticacao(p_outra, outra), aula=outra)[1]["valido"] is True
    assert ch.conferir(ch.autenticacao(p_outra, outra), aula=ch.aula)[1] == {"valido": False}
    assert ch.conferir(ch.autenticacao(p_outra, ch.aula), aula=ch.aula)[1] == {"valido": False}
