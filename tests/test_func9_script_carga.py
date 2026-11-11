# -*- coding: utf-8 -*-
"""
QA - Funcionalidade 9: o script de teste de carga (ferramentas/teste_de_carga.py), contra um servidor de mentira na própria máquina.
Confere o que o script PRECISA acertar para o teste na Render valer alguma coisa: recusa sem --confirmo, lê o CSV, manda o corpo do
check-in que o PresencaIn REAL (Pydantic) aceita, um aparelho diferente por aluno, conta status e tempos certos, não passa do nº de
contas e nunca chama outro endereço além do informado.
"""
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from harness_app import *  # noqa: F401,F403

ARQ = Path(APP).parent / "ferramentas" / "teste_de_carga.py"
spec = importlib.util.spec_from_file_location("teste_de_carga", ARQ)
carga = importlib.util.module_from_spec(spec)
spec.loader.exec_module(carga)


class Servidor:
    """Imita a API: login, chamadas abertas e check-in (validado com o PresencaIn real). Os 8 primeiros check-ins dão 200, o resto 409."""

    def __init__(self, ctx):
        self.PresencaIn = ctx.ns["PresencaIn"]
        self.pedidos = []
        self.aparelhos = []
        self.checkins = 0
        self.trava = threading.Lock()
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, st, obj):
                b = json.dumps(obj).encode()
                self.send_response(st)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                with srv.trava:
                    srv.pedidos.append(("GET", self.path, self.headers.get("Authorization")))
                if self.path == "/api/aulas/ativas":
                    return self._json(200, [])
                self._json(404, {"detail": "x"})

            def do_POST(self):
                corpo = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                with srv.trava:
                    srv.pedidos.append(("POST", self.path, self.headers.get("Authorization")))
                if self.path == "/api/auth/login":
                    if corpo.get("senha") == "errada":
                        return self._json(401, {"detail": "E-mail ou senha incorretos."})
                    return self._json(200, {"token": "tok-" + corpo["email"], "usuario": {}})
                if self.path == "/api/aulas/77/presenca":
                    try:
                        srv.PresencaIn(**corpo)
                    except Exception as e:  # noqa: BLE001
                        return self._json(422, {"detail": str(e)})
                    with srv.trava:
                        srv.aparelhos.append(corpo["device_id"])
                        srv.checkins += 1
                        n = srv.checkins
                    return self._json(200 if n <= 8 else 409, {"ok": True})
                self._json(404, {"detail": "x"})

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.http.server_address[1]}"
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def parar(self):
        self.http.shutdown()


@pytest.fixture
def srv():
    from test_func4_presenca import CtxPr
    s = Servidor(CtxPr())
    yield s
    s.parar()


def contas(tmp_path, n, sep=";", erradas=0):
    f = tmp_path / "contas.csv"
    linhas = [f"aluno{i}@alunos.unicerrado.edu.br{sep}{'errada' if i < erradas else 'senha12345'}" for i in range(n)]
    f.write_text("email;senha\n".replace(";", sep) + "\n".join(linhas) + "\n", encoding="utf-8")
    return str(f)


def rodar(srv, arq, capsys, *extra, alunos=12):
    rc = carga.main(["--url", srv.url, "--contas", arq, "--aula", "77", "--lat", "-18.4157", "--lon", "-49.2183", "--alunos", str(alunos),
                     "--rampa", "1", "--duracao", "2", "--json", *extra])
    return rc, capsys.readouterr()


def test_ct1060_recusa_sem_confirmo(srv, tmp_path, capsys):
    """CT-1060 | Sem --confirmo | recusa (código 2) e NÃO manda nenhum pedido ao servidor."""
    rc = carga.main(["--url", srv.url, "--contas", contas(tmp_path, 3), "--aula", "77", "--lat", "0", "--lon", "0"])
    assert rc == 2 and srv.pedidos == []


def test_ct1061_endereco_invalido_e_arquivo_sem_contas(srv, tmp_path, capsys):
    """CT-1061 | --url sem http(s) ou CSV sem contas | recusa (código 2), sem pedidos."""
    assert carga.main(["--url", "ftp://x", "--contas", contas(tmp_path, 3), "--aula", "1", "--lat", "0", "--lon", "0", "--confirmo"]) == 2
    vazio = tmp_path / "v.csv"
    vazio.write_text("email;senha\n", encoding="utf-8")
    assert carga.main(["--url", srv.url, "--contas", str(vazio), "--aula", "1", "--lat", "0", "--lon", "0", "--confirmo"]) == 2
    assert srv.pedidos == []


def test_ct1062_fluxo_completo_e_contagens(srv, tmp_path, capsys):
    """CT-1062 | 12 alunos | 12 logins ok; cada um consultou as chamadas; 12 check-ins (8 x 200 e 4 x 409); tempos presentes."""
    rc, out = rodar(srv, contas(tmp_path, 12), capsys, "--confirmo")
    r = json.loads(out.out)
    assert rc == 0
    assert r["login"]["total"] == 12 and r["login"]["ok"] == 12
    assert r["consultar chamadas"]["total"] >= 12 and r["consultar chamadas"]["ok"] == r["consultar chamadas"]["total"]
    assert r["registrar presença"]["total"] == 12 and r["registrar presença"]["status"] == {"200": 8, "409": 4}, r["registrar presença"]
    assert srv.checkins == 12
    for x in r.values():
        assert 0 <= x["mediana_ms"] <= x["p95_ms"] <= x["p99_ms"] <= x["max_ms"] and x["por_segundo"] > 0


def test_ct1063_corpo_do_checkin_e_aceito_pelo_pydantic_real_e_cada_aluno_tem_seu_aparelho(srv, tmp_path, capsys):
    """CT-1063 | O check-in respeita o PresencaIn real (sem nenhum 422) e os 12 aparelhos são todos diferentes."""
    rc, out = rodar(srv, contas(tmp_path, 12), capsys, "--confirmo")
    r = json.loads(out.out)["registrar presença"]
    assert "422" not in r["status"], r
    assert len(set(srv.aparelhos)) == 12


def test_ct1064_login_errado_nao_consulta_nem_registra(srv, tmp_path, capsys):
    """CT-1064 | 5 de 12 contas com senha errada | essas 5 contam 401 no login e não fazem mais nenhum pedido."""
    rc, out = rodar(srv, contas(tmp_path, 12, erradas=5), capsys, "--confirmo")
    r = json.loads(out.out)
    assert r["login"]["status"] == {"200": 7, "401": 5}, r["login"]
    assert r["registrar presença"]["total"] == 7 and srv.checkins == 7


def test_ct1065_nao_passa_do_numero_de_contas_e_aceita_virgula(srv, tmp_path, capsys):
    """CT-1065 | 5 contas, 50 alunos pedidos, CSV com vírgula | roda com 5."""
    rc, out = rodar(srv, contas(tmp_path, 5, sep=","), capsys, "--confirmo", alunos=50)
    r = json.loads(out.out)
    assert rc == 0 and r["login"]["total"] == 5 and "só há 5 contas" in out.err


def test_ct1066_so_fala_com_o_endereco_informado(srv, tmp_path, capsys):
    """CT-1066 | Todos os pedidos vão para o servidor informado e levam o token do aluno certo."""
    rodar(srv, contas(tmp_path, 4), capsys, "--confirmo", alunos=4)
    for metodo, caminho, auth in srv.pedidos:
        assert caminho.startswith("/api/")
        if caminho != "/api/auth/login":
            assert auth and auth.startswith("Bearer tok-aluno"), (caminho, auth)


def test_ct1067_servidor_fora_do_ar_aparece_como_status_0(tmp_path, capsys):
    """CT-1067 | Nada escutando no endereço | cada login conta status 0 (sem resposta) e o script termina sem travar."""
    rc = carga.main(["--url", "http://127.0.0.1:9", "--contas", contas(tmp_path, 3), "--aula", "77", "--lat", "0", "--lon", "0", "--alunos", "3",
                     "--rampa", "0.5", "--duracao", "1", "--json", "--confirmo"])
    r = json.loads(capsys.readouterr().out)
    assert rc == 0 and r["login"]["status"] == {"0": 3}


def test_ct1068_percentis():
    """CT-1068 | pct() | mediana, p95 e p99 de uma lista conhecida."""
    v = list(range(1, 101))
    assert carga.pct(v, 50) in (50, 51) and carga.pct(v, 95) in (95, 96) and carga.pct(v, 99) in (99, 100) and carga.pct([7], 99) == 7
