# Migrações do banco (Alembic)

O banco de dados **não vai dentro do repositório** (e nem deve: tem senhas, e-mails e presenças de alunos). O que vai é a
**receita** para montá-lo: as migrations em `migrations/versions/`. Quem clona o projeto roda um comando e o banco nasce
com as tabelas e os dados iniciais, igual em qualquer máquina.

| Revisão | O que faz |
|---|---|
| `0001_esquema_inicial` | Cria todas as tabelas (alunos, professores, aulas, presenças, tentativas, logs de segurança, LGPD...). |
| `0002_dados_iniciais` | Insere a lista de cursos. Só insere o que ainda não existe. |

## Como aplicar

```bash
pip install -r requirements.txt
export DATABASE_URL=postgresql://usuario:senha@servidor:5432/banco   # sem isso usa SQLite local (geocheckin.db)
alembic upgrade head      # aplica tudo que falta
alembic current           # mostra a versão em que o banco está
alembic history           # lista as revisões
```

No Docker e no Render isso já roda sozinho antes do app subir (veja `Dockerfile` e `render.yaml`). É seguro rodar várias
vezes e em banco que já tem dados: o que já existe não é alterado.

## O que NÃO é migration (e por quê)

| Item | Onde fica |
|---|---|
| **Anti-VPN** | Não é banco: são variáveis de ambiente `PROXYCHECK_KEY` e `VPN_MODO` (`off`, `avisar` ou `bloquear`). Cadastre no painel da hospedagem. |
| **Chave da coordenação e conta provisória** | `CHAVE_MASTER`, `COORD_EMAIL`, `COORD_SENHA`: segredos não podem ir para o GitHub. A conta provisória é criada na subida do app, se a variável existir. |
| **Alunos, professores, aulas, presenças** | São dados de produção. Se for preciso levar do banco antigo para o novo: `pg_dump` (backup) e `pg_restore`/`psql`, feito pela TI, nunca pelo repositório. |

## Como criar uma nova migration (quando o modelo mudar)

```bash
alembic revision -m "descrição curta"      # cria o arquivo em migrations/versions/
# edite upgrade() e downgrade(); confira se a coluna/índice já existe antes de criar
alembic upgrade head
```

O GitHub Actions (`.github/workflows/migracoes.yml`) aplica as migrations em um PostgreSQL vazio, confere as tabelas e os
cursos e roda uma segunda vez para provar que não duplica nada.
