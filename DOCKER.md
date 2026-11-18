# Guará Check em Docker

Este guia leva o Guará Check para um servidor da instituição usando Docker. Há duas formas:

- **Docker Compose** (recomendado): sobe o app e um PostgreSQL juntos, com volume para os dados.
- **Só a imagem**: para quem já tem um PostgreSQL e uma plataforma de contêineres (Kubernetes, Portainer etc.).

## O que vem no repositório

| Arquivo | Para quê |
|---|---|
| `Dockerfile` | Receita da imagem (Python 3.12, usuário sem privilégios, health check). |
| `docker-compose.yml` | App + PostgreSQL 16 com volume `pgdata`. |
| `.dockerignore` | Garante que `.env`, testes, `.git` e bancos locais **não** entram na imagem. |
| `.env.example` | Todas as variáveis de configuração, comentadas. |
| `.github/workflows/docker.yml` | Constrói a imagem no GitHub, testa que ela sobe e deixa o arquivo da imagem para baixar. |

## 1. Subir com Docker Compose

No servidor (precisa de Docker com o plugin Compose):

```bash
git clone <endereço do repositório> guaracheck && cd guaracheck
cp .env.example .env
```

Edite o `.env` e preencha **no mínimo**:

| Variável | O que colocar |
|---|---|
| `SECRET_KEY` | Texto longo e aleatório (`openssl rand -hex 32`). Assina os logins: se mudar, todos precisam entrar de novo. |
| `POSTGRES_PASSWORD` | Só letras e números (`openssl rand -hex 24`), porque entra numa URL. |
| `CHAVE_MASTER` | Chave do primeiro acesso da coordenação (sem ela ninguém cria conta de professor). |
| `PUBLIC_URL` | Endereço público com `https://`, ex.: `https://presenca.unicerrado.edu.br`. Vai dentro do QR code da chamada. |
| `CERTIFICADO_ATIVO` | `1` libera o certificado de participação (PDF e conferência do código). Sem a variável (ou com `0`) ele fica **bloqueado**: o aluno vê "Certificado: em breve". |

Depois:

```bash
docker compose up -d --build
docker compose ps            # app e db devem aparecer como "healthy"
curl http://127.0.0.1:8000/api/health     # {"status":"ok"}
```

As tabelas e a lista de cursos são criadas sozinhas na primeira subida. O primeiro acesso da coordenação é pela aba **Coordenação** da tela inicial, com a `CHAVE_MASTER` (ou com a conta provisória, se você definiu `COORD_SENHA`; troque a senha logo em seguida).

## 2. HTTPS e proxy reverso (obrigatório)

O navegador **só libera o GPS em páginas HTTPS**. Sem HTTPS a tela do aluno não consegue registrar presença. Coloque na frente do app um proxy reverso com certificado da instituição (nginx, Apache, Traefik, Caddy, o balanceador da TI...) apontando para a porta 8000.

Pontos que precisam estar certos:

1. **`X-Forwarded-For` e `X-Forwarded-Proto`**: o proxy precisa enviá-los. O app usa o IP do cliente para os limites de tentativas e o anti-VPN.
2. **`PROXY_HOPS`** (no `.env`): quantos proxies existem entre o aluno e o app. Padrão `1`. Se houver dois (ex.: balanceador + nginx), use `2`. Valor errado faz o app confiar num IP que o aluno pode forjar, ou enxergar todos os alunos como um IP só.
3. **Porta**: por padrão o compose publica `127.0.0.1:8000`, ou seja, só a própria máquina alcança o app. Se o proxy está em outra máquina, ponha `APP_BIND=0.0.0.0` no `.env` e libere a porta 8000 **somente** para o IP do proxy no firewall.
4. Não defina `FORWARDED_ALLOW_IPS=*` no contêiner: o app já trata o `X-Forwarded-For` por conta própria.

Exemplo de nginx (**exemplo, não foi testado aqui**):

```nginx
server {
    listen 443 ssl;
    server_name presenca.unicerrado.edu.br;
    # ssl_certificate / ssl_certificate_key: os da instituição

    client_max_body_size 8m;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## 3. Saídas de rede que o servidor precisa

O servidor (não o celular do aluno) acessa, por HTTPS, estes endereços. Libere os que você for usar:

| Endereço | Para quê | Necessário? |
|---|---|---|
| `api.brevo.com` | Código de confirmação do cadastro e aviso de chamada aberta (`EMAIL_PROVEDOR=brevo`) | Só se usar e-mail por Brevo. Com SMTP institucional (`EMAIL_PROVEDOR=smtp`), libere o servidor SMTP. |
| `proxycheck.io` | Anti-VPN (`PROXYCHECK_KEY`) | Só se ligar o anti-VPN. |
| `nominatim.openstreetmap.org` | Busca do lugar pelo nome, na tela do professor | Opcional (dá para marcar no mapa). |

## 4. Só a imagem (sem Compose)

```bash
docker build -t guaracheck .
docker run -d --name guaracheck --restart unless-stopped -p 127.0.0.1:8000:8000 \
  --env-file .env \
  -e DATABASE_URL='postgresql://usuario:senha@servidor-do-banco:5432/guaracheck' \
  guaracheck
```

- Sem `DATABASE_URL`, a imagem usa **SQLite** em `/data` (monte um volume: `-v guaracheck-dados:/data`). Serve para teste, **não para produção**: com muitos alunos registrando ao mesmo tempo, use PostgreSQL.
- Se o `/data` for uma pasta do servidor (bind mount) e não um volume do Docker, ela precisa pertencer ao usuário `10001`: `chown 10001 /caminho/da/pasta`.
- Processos do servidor web: `WEB_CONCURRENCY` (padrão 2, o mesmo do `render.yaml`).
- A imagem aceita trocar a base: `--build-arg PYTHON_IMAGE=registro.da.instituicao/python:3.12-slim`.

## 5. Levar a imagem para um servidor sem internet

Quem tem acesso à internet gera o arquivo; o servidor só carrega:

```bash
docker save guaracheck | gzip > guaracheck-imagem.tar.gz    # na máquina com internet
# copie o arquivo para o servidor, e lá:
gunzip -c guaracheck-imagem.tar.gz | docker load
```

O workflow do GitHub (`Imagem Docker`) já deixa esse arquivo pronto: aba **Actions** > execução mais recente > **Artifacts** > `guaracheck-imagem`. Ele fica guardado por 30 dias. Para publicar a imagem no registro do GitHub (`ghcr.io`), crie uma tag (`git tag v1.0.0 && git push origin v1.0.0`) ou rode o workflow manualmente marcando **publicar**. O pacote nasce **privado**; para o servidor da instituição baixá-lo, use `docker login ghcr.io` com um token com permissão `read:packages`.

Depois do `docker load` a imagem fica como `guaracheck:latest`. Com Compose, suba **sem construir**: `docker compose up -d --no-build` (o `docker-compose.yml` já usa `image: guaracheck:latest`). O PostgreSQL (`postgres:16-alpine`) também precisa estar no servidor: baixe-o junto, do mesmo jeito (`docker save postgres:16-alpine`).

## 6. Dados, backup e atualização

Os dados ficam no volume `pgdata`. **Faça backup** (ex.: toda noite, pelo cron do servidor):

```bash
docker compose exec -T db pg_dump -U guara guaracheck | gzip > backup-$(date +%F).sql.gz
```

Restaurar em um banco vazio:

```bash
gunzip -c backup-AAAA-MM-DD.sql.gz | docker compose exec -T db psql -U guara guaracheck
```

Atualizar para uma versão nova:

```bash
git pull
docker compose up -d --build     # o banco e os dados continuam; colunas novas são criadas sozinhas
```

Ver o que o app está dizendo: `docker compose logs -f app`. Os avisos do início (`CHAVE_MASTER não definida`, `confirmação de e-mail DESLIGADA`...) mostram o que falta configurar.

## 7. O que a imagem faz de segurança

- Roda como usuário `guara` (uid 10001), não como root. O código em `/app` é somente leitura para ele; só `/data` é gravável.
- `.env`, testes, `.git` e bancos locais não entram na imagem.
- Sem `curl` ou ferramentas extras: só o Python e as bibliotecas do `requirements.txt`.
- O health check (`/api/health`) consulta o banco: contêiner "unhealthy" significa app sem acesso ao banco.

Opcional para endurecer mais (não foi testado aqui): no `docker-compose.yml`, `read_only: true` no serviço `app` com `tmpfs: [/tmp]`.

## 8. O que foi e o que não foi testado

- A receita (`Dockerfile`), o `docker-compose.yml` e o `.dockerignore` passam na verificação do Docker (`docker buildx build --check`, `docker compose config`).
- Num ambiente sem acesso ao Docker Hub, a imagem foi montada sobre uma base local, sem a etapa do `pip install`, e conferida por dentro: só os arquivos necessários, `/app` somente leitura para o usuário `guara`, `/data` gravável, comando de health check responde 0 com o servidor no ar e 1 com ele fora.
- **Não foi testado** neste ambiente: o `pip install` dentro da imagem, o app de fato subindo no contêiner e a conexão com o PostgreSQL. Isso é o que o workflow `Imagem Docker` faz no GitHub a cada push (sobe a imagem com SQLite e com PostgreSQL via Compose e consulta `/api/health`, `/` e `/api/cursos`). Confira a aba **Actions** do repositório: se estiver verde, está validado.
- As dependências do `requirements.txt` têm faixas de versão (não versões exatas). Para builds idênticos ao longo do tempo, fixe as versões depois do primeiro build aprovado (`pip freeze` dentro do contêiner).
