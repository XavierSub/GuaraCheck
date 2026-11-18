# -*- coding: utf-8 -*-
"""
Os arquivos do Docker combinam com o resto do projeto. NÃO constrói a imagem (isso é o workflow .github/workflows/docker.yml);
só pega o erro bobo antes de ele chegar ao servidor: arquivo copiado que não existe ou que o .dockerignore esconde, porta ou
rota de health check diferentes do app, variável do compose que ninguém documentou.
Uso:  pytest tests/test_docker_arquivos.py -v
"""
import fnmatch
import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
DOCKERFILE = (RAIZ / "Dockerfile").read_text(encoding="utf-8")
IGNORE = [l.strip() for l in (RAIZ / ".dockerignore").read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
APP = (RAIZ / "app.py").read_text(encoding="utf-8")
ENV_EXEMPLO = (RAIZ / ".env.example").read_text(encoding="utf-8")


def instrucoes(nome):
    """Linhas do Dockerfile que começam com `nome`, já com as continuações (\\) juntadas."""
    texto = re.sub(r"\\\n\s*", " ", DOCKERFILE)
    return [l.strip() for l in texto.splitlines() if re.match(rf"\s*{nome}\b", l, re.I)]


def ignorado(caminho):
    for padrao in IGNORE:
        p = padrao.lstrip("/")
        if p.startswith("**/"):
            p = p[3:]
        if fnmatch.fnmatch(caminho, p) or fnmatch.fnmatch(Path(caminho).name, p) or caminho.startswith(p.rstrip("/") + "/"):
            return True
    return False


def origens_do_copy():
    saida = []
    for linha in instrucoes("COPY"):
        partes = linha.split()[1:]
        saida += [p for p in partes[:-1] if not p.startswith("--")]
    return saida


def test_dt01_tudo_que_o_dockerfile_copia_existe():
    origens = origens_do_copy()
    assert {"requirements.txt", "app.py", "index.html", "assets", "ferramentas"} <= set(origens)
    for o in origens:
        assert (RAIZ / o).exists(), f"COPY de algo que não existe: {o}"


def test_dt02_o_dockerignore_nao_esconde_o_que_a_imagem_precisa():
    for o in origens_do_copy():
        assert not ignorado(o), f".dockerignore esconde {o}, que o Dockerfile copia"
    for dentro in ("assets/icone-192.png", "ferramentas/limpar_banco.py"):
        assert (RAIZ / dentro).exists() and not ignorado(dentro)


@pytest.mark.parametrize("segredo_ou_lixo", [".env", ".git", "tests", "geocheckin.db", ".venv", ".github", "render.yaml"])
def test_dt03_o_dockerignore_deixa_de_fora_segredos_e_lixo(segredo_ou_lixo):
    assert ignorado(segredo_ou_lixo), f"{segredo_ou_lixo} entraria na imagem"


def test_dt04_o_comando_sobe_o_app_que_existe():
    (cmd,) = instrucoes("CMD")
    assert "uvicorn app:app" in cmd and re.search(r"^app = FastAPI\(", APP, re.M)
    assert "exec " in cmd, "sem exec o uvicorn não recebe o SIGTERM do docker stop"
    assert "${PORT}" in cmd
    assert re.search(r"^uvicorn\b", (RAIZ / "requirements.txt").read_text(encoding="utf-8"), re.M)


def test_dt05_o_health_check_chama_uma_rota_que_o_app_tem():
    (hc,) = instrucoes("HEALTHCHECK")
    rota = re.search(r"(/api/[a-z\-]+)", hc).group(1)
    assert re.search(rf'@app\.get\("{re.escape(rota)}"\)', APP), f"o app não tem a rota {rota}"
    assert "--start-period" in hc


def test_dt06_a_porta_e_a_mesma_em_todo_lugar():
    env = "\n".join(instrucoes("ENV"))
    porta = re.search(r"PORT=(\d+)", env).group(1)
    (expose,) = instrucoes("EXPOSE")
    assert expose.split()[1] == porta
    assert re.search(rf":{porta}\"", (RAIZ / "docker-compose.yml").read_text(encoding="utf-8"))
    assert 'os.getenv("PORT"' in APP        # o app também lê PORT quando roda direto (app.py)


def test_dt07_roda_sem_root_e_so_grava_em_data():
    assert any(l.split()[1] == "guara" for l in instrucoes("USER"))
    run = " ".join(instrucoes("RUN"))
    assert "useradd" in run and "10001" in run and "chown guara /data" in run
    assert "VOLUME" in DOCKERFILE and "/data" in " ".join(instrucoes("VOLUME"))
    assert "sqlite:////data/" in " ".join(instrucoes("ENV"))        # o banco de teste cai no volume, não em /app (somente leitura)


def test_dt08_dependencias_instaladas_antes_do_codigo():
    linhas = [l.strip() for l in DOCKERFILE.splitlines()]
    i_pip = next(i for i, l in enumerate(linhas) if l.startswith("RUN pip install"))
    i_app = next(i for i, l in enumerate(linhas) if l.startswith("COPY app.py"))
    assert i_pip < i_app, "mexer no app.py refaria o pip install"
    assert "psycopg2" in (RAIZ / "requirements.txt").read_text(encoding="utf-8")      # a URL do compose é de PostgreSQL


# --------------------------------------------------------------------------- compose e workflow
yaml = pytest.importorskip("yaml")
COMPOSE_TXT = (RAIZ / "docker-compose.yml").read_text(encoding="utf-8")
COMPOSE = yaml.safe_load(COMPOSE_TXT)


def test_dt20_compose_tem_app_e_banco_com_espera_pela_saude_do_banco():
    s = COMPOSE["services"]
    assert set(s) == {"app", "db"}
    assert s["app"]["depends_on"]["db"]["condition"] == "service_healthy" and "healthcheck" in s["db"]
    assert s["app"]["env_file"] == [".env"] and "pgdata" in COMPOSE["volumes"]
    assert any("pgdata:/var/lib/postgresql/data" in v for v in s["db"]["volumes"])


def test_dt21_compose_liga_o_app_ao_banco_do_compose():
    env = COMPOSE["services"]["app"]["environment"]
    assert env["DATABASE_URL"] == "postgresql://guara:${POSTGRES_PASSWORD}@db:5432/guaracheck"
    db = COMPOSE["services"]["db"]["environment"]
    assert (db["POSTGRES_USER"], db["POSTGRES_DB"]) == ("guara", "guaracheck")
    assert db["POSTGRES_PASSWORD"].startswith("${POSTGRES_PASSWORD:?")      # sem senha o compose nem sobe
    assert env["SECRET_KEY"].startswith("${SECRET_KEY:?")


def test_dt22_o_app_so_aceita_o_esquema_do_compose():
    # o app troca postgresql:// por postgresql+psycopg2:// (psycopg2 está no requirements)
    assert 'DATABASE_URL.startswith("postgresql://")' in APP


def test_dt23_porta_publicada_so_na_propria_maquina_por_padrao():
    (porta,) = COMPOSE["services"]["app"]["ports"]
    assert porta.startswith("${APP_BIND:-127.0.0.1}:")


def test_dt24_toda_variavel_obrigatoria_do_compose_esta_no_env_exemplo():
    usadas = set(re.findall(r"\$\{([A-Z_]+)(?::[?-])?", COMPOSE_TXT))
    assert {"POSTGRES_PASSWORD", "SECRET_KEY", "APP_BIND", "APP_PORTA"} <= usadas
    for v in usadas:
        assert re.search(rf"^{v}=", ENV_EXEMPLO, re.M), f"{v} é usada no docker-compose.yml e não está no .env.example"


def test_dt25_workflow_valido_e_aponta_para_o_que_existe():
    txt = (RAIZ / ".github" / "workflows" / "docker.yml").read_text(encoding="utf-8")
    wf = yaml.safe_load(txt)
    assert set(wf[True]) >= {"push", "pull_request", "workflow_dispatch"}      # "on" vira True no YAML 1.1
    passos = wf["jobs"]["imagem"]["steps"]
    usos = [p["uses"] for p in passos if "uses" in p]
    assert any(u.startswith("actions/checkout@") for u in usos) and any(u.startswith("docker/build-push-action@") for u in usos)
    assert "guaracheck:latest" in txt and "docker compose up" in txt and "/api/health" in txt
    # publicar no ghcr.io só em tag v* ou a pedido: um push comum na main não publica nada
    publicar = [p for p in passos if "docker push" in p.get("run", "")]
    assert publicar and all("refs/tags/v" in p["if"] and "inputs.publicar" in p["if"] for p in publicar)


def test_dt26_documentacao_cita_os_arquivos_e_os_avisos_importantes():
    doc = (RAIZ / "DOCKER.md").read_text(encoding="utf-8")
    for termo in ("docker compose up -d --build", "PROXY_HOPS", "HTTPS", "pg_dump", "docker load", "10001", "PUBLIC_URL",
                  "POSTGRES_PASSWORD", "SECRET_KEY", "CHAVE_MASTER", "Não foi testado"):
        assert termo in doc, f"DOCKER.md não menciona {termo}"
