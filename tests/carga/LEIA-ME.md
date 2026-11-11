# F9 - Carga (5.000 alunos)

O que dá para provar **sem** a Render: o banco (PostgreSQL de verdade) e a lógica do app. O que só dá para provar **depois do deploy**: o tempo
de resposta do app (FastAPI + SQLAlchemy) no plano da Render. Para isso existe o `ferramentas/teste_de_carga.py`.

## Como rodar

| O quê | Comando |
|---|---|
| Banco com 5.000 alunos, consultas e check-ins simultâneos (precisa do PostgreSQL e do pgbench instalados) | `bash tests/carga/rodar_carga.sh` |
| Testes das correções de carga (lógica do app) | `pytest tests/test_func9_carga.py tests/test_func9_script_carga.py` |
| Atualização automática da tela do aluno (relógio controlado) | `python tests/navegador/atualizacao_tela.py` |
| Carga de verdade na Render, **depois do deploy** (cria presenças: só contas e chamada de TESTE) | `python ferramentas/teste_de_carga.py --url ... --contas contas_teste.csv --aula N --lat ... --lon ... --alunos 50 --confirmo` |

## O que foi medido (PostgreSQL 16, 2 CPUs, esquema igual ao do app, 5.000 alunos, 1.601 aulas, 125 mil presenças, 25 mil tentativas, 60 mil linhas de auditoria, 67 MB)

Consultas (EXPLAIN ANALYZE, cache quente). **Todas usam índice**; a coluna da direita é o tempo no banco:

| Tela | Consulta | Tempo |
|---|---|---|
| Login | e-mail (único) | 0,04 ms |
| Qualquer pedido do aluno | conta pelo id | 0,02 ms |
| Aluno, a cada 10 s | chamadas abertas + "já registrei?" | 0,15 + 0,08 ms |
| Check-in | duplicata, último check-in, INSERT | 0,01 + 0,04 ms + INSERT |
| Professor, a cada 3 s | contagem de presentes (5.000) | 1,0 ms |
| Professor, 1ª carga | lista de 5.000 presentes (limite 2.000) | 7,7 ms |
| Professor, atualização | só as presenças novas | 0,12 ms |
| Professor | ausentes do curso | 0,6 ms |
| Aluno | meus comprovantes (página 1) | 0,4 ms |
| Coordenação | resumo (contagens) | 18 ms + 2 ms |
| Coordenação | lista de aulas, **antes** | **170 ms** |
| Coordenação | lista de aulas, **depois** | **2,3 + 4,2 ms** |
| Coordenação | alertas / auditoria / busca de aluno | 3,4 / 0,3 / 5 ms |
| Professor | exportação CSV de 5.000 presentes | 8,8 ms |

Simultaneidade (pgbench, 40 conexões = 2 workers x 20 do pool):

| Cenário | Check-ins por segundo | 5.000 alunos terminam em |
|---|---|---|
| Banco puro (como ficou depois da correção) | 3.200 | 1,6 s |
| Consulta de VPN de 250 ms **segurando a conexão** (como era) | 157 | 32 s |
| Consulta de VPN de 2,5 s (provedor lento) **segurando a conexão** (como era) | 16 | 5 min 13 s, e quem esperasse mais de 10 s pela conexão tomava erro 500 |
| Atualização da tela do aluno (3 consultas por pedido) | 5.750 pedidos/s | (equivale a 57 mil alunos conectados a cada 10 s) |

Conclusão: **o banco não é o gargalo**. Os limites estão no app e no plano da hospedagem.

## O que foi corrigido por causa da carga

1. **Consulta de VPN segurava a conexão do banco** (até 2,5 s). Agora a conexão é devolvida ao pool antes da consulta externa.
2. **Sem disjuntor**: com o provedor fora do ar, cada aluno em dados móveis (IP diferente) esperava 2,5 s. Agora, depois de 5 falhas seguidas,
   ninguém é consultado por 60 s (e ninguém é bloqueado por isso).
3. **Freios de tentativa apagados por volume**: passar de 5.000 chaves zerava TODAS as contagens (inclusive de quem estava travado por
   tentar senhas). Agora só somem as vencidas e, no teto, as de menos tentativas.
4. **Lista de aulas da coordenação** lia o histórico inteiro (170 ms hoje, crescendo a cada semestre). Agora conta só as 60 da página (6 ms).
5. **Tela do aluno consultava o servidor de 10 em 10 s, todos no mesmo segundo**, e insistia igual com o servidor com erro. Agora: variação
   aleatória de ±20% e espera crescente (20, 40, até 60 s) quando há erro.

## Limites que continuam e não dá para medir aqui (estimativas, não medições)

- **Login** (PBKDF2, 150.000 rodadas): medido 26 ms de CPU por login. Capacidade = CPU disponível / 0,026 s. Com 1 CPU inteira, ~38 logins/s
  (5.000 em ~2 min); com meia CPU, ~19/s (~4,5 min); com uma fração pequena de CPU (plano gratuito, valor não informado pela Render), pode
  levar dezenas de minutos. O login acontece uma vez a cada 12 h (TOKEN_HORAS), mas a turma toda entra junta no começo da aula.
- **Pedidos de atualização**: custo de CPU do FastAPI/SQLAlchemy por pedido não medido (depende do plano). Com X ms de CPU por pedido e uma
  CPU inteira, aguentam-se (1000 / X) pedidos por segundo = (10.000 / X) alunos conectados com a tela aberta (atualização de 10 s).
  Meça com `ferramentas/teste_de_carga.py` no plano que você contratar.
- **Plano gratuito da Render** (conforme render.com/docs/free e render.com/pricing.md, consultados em 11/11/2026): o serviço **dorme após 15 min
  sem acesso** (cerca de 1 min para acordar), tem 512 MB e uma única instância; o banco gratuito **expira 30 dias após a criação** (14 dias de
  carência, sem backup), tem 256 MB de RAM e 100 conexões. O app usa no máximo 40 conexões (2 workers x 20), dentro do limite.
- Memória: 2 workers com FastAPI, SQLAlchemy, reportlab e openpyxl em 512 MB: não medido; gerar PDFs/CSVs grandes ao mesmo tempo pode estourar.
- Cada aluno usa 1 conexão do banco só durante o pedido (o app libera ao terminar); mas o pool tem teto de 20 por worker e espera 10 s.
