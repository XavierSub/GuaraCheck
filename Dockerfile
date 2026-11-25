# Guará Check (UniCerrado): imagem para o ambiente institucional.
# Construir:  docker build -t guaracheck .
# Subir com PostgreSQL:  veja docker-compose.yml e DOCKER.md.
#
# PYTHON_IMAGE existe para a instituição trocar a imagem base por uma cópia do seu registro interno
# (ex.: --build-arg PYTHON_IMAGE=registro.unicerrado.edu.br/base/python:3.12-slim).
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

LABEL org.opencontainers.image.title="Guará Check" \
      org.opencontainers.image.description="Chamada por geolocalização (FastAPI) - UniCerrado Goiatuba"

# PORT: porta dentro do contêiner. WEB_CONCURRENCY: processos do uvicorn (o mesmo valor do render.yaml).
# DATABASE_URL padrão = SQLite em /data (serve para testar). Em produção use PostgreSQL: docker-compose.yml já faz isso.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000 \
    WEB_CONCURRENCY=2 \
    DATABASE_URL=sqlite:////data/geocheckin.db

WORKDIR /app

# As dependências vêm antes do código: mexer no app.py não refaz o pip install.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Usuário sem privilégios; /data é a única pasta onde o app grava (banco SQLite, se for o caso).
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin guara \
    && mkdir /data && chown guara /data

COPY app.py index.html sw.js alembic.ini ./
COPY migrations ./migrations
COPY assets ./assets
COPY ferramentas ./ferramentas

USER guara
VOLUME ["/data"]
EXPOSE 8000

# /api/health consulta o banco: contêiner "unhealthy" = o app subiu, mas não alcança o banco.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import os,sys,urllib.request as u; sys.exit(0 if u.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT','8000'), timeout=4).status == 200 else 1)"]

# exec: o uvicorn vira o processo principal e recebe o SIGTERM do "docker stop".
# Migrações do banco antes de subir (se falhar, avisa e o app sobe mesmo assim: ele também cria o que falta).
CMD ["sh", "-c", "alembic upgrade head || echo AVISO: migracao falhou; exec uvicorn app:app --host 0.0.0.0 --port ${PORT} --no-server-header"]
