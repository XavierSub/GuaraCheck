#!/usr/bin/env bash
# F9 - teste de carga NO BANCO (PostgreSQL de verdade): esquema igual ao do app, 5.000 alunos, 125 mil presenças, consultas de cada
# tela com EXPLAIN ANALYZE, 5.000 check-ins simultâneos e consultas de atualização da tela do aluno (pgbench).
# Precisa dos programas do PostgreSQL (initdb, postgres, psql, pgbench). Cria um banco TEMPORÁRIO numa pasta própria e apaga no fim.
# Uso:  bash tests/carga/rodar_carga.sh        (PG_BIN=/usr/lib/postgresql/16/bin  PG_PORTA=5545  PG_DIR=/var/tmp/guaracheck_carga)
set -euo pipefail
AQUI="$(cd "$(dirname "$0")" && pwd)"
BIN="${PG_BIN:-$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1)}"
[ -x "$BIN/postgres" ] && [ -x "$BIN/pgbench" ] || { echo "PostgreSQL/pgbench não encontrados. Defina PG_BIN=/caminho/do/bin"; exit 1; }
DIR="${PG_DIR:-/var/tmp/guaracheck_carga}"; PORTA="${PG_PORTA:-5545}"
SAIDA="${SAIDA:-$AQUI/saida}"; mkdir -p "$SAIDA"
como() { if [ "$(id -u)" = 0 ]; then runuser -u postgres -- "$@"; else "$@"; fi; }   # o postgres não roda como root
P=("$BIN/psql" -h "$DIR" -p "$PORTA" -U postgres -q -At -v ON_ERROR_STOP=1)
PB=("$BIN/pgbench" -h "$DIR" -p "$PORTA" -U postgres)
parar() { como "$BIN/pg_ctl" -D "$DIR/data" -m fast stop >/dev/null 2>&1 || true; rm -rf "$DIR"; }
trap parar EXIT
rm -rf "$DIR"; mkdir -p "$DIR"; [ "$(id -u)" = 0 ] && chown postgres "$DIR"
cd /tmp
como "$BIN/initdb" -D "$DIR/data" -A trust -E UTF8 --locale=C.UTF-8 >/dev/null
como "$BIN/pg_ctl" -D "$DIR/data" -o "-p $PORTA -k $DIR -c shared_buffers=128MB -c max_connections=200 -c listen_addresses=''" -l "$DIR/log.txt" -w start >/dev/null
echo "PostgreSQL: $("${P[@]}" -c 'select version()' postgres | cut -c1-40)"
"${P[@]}" -f "$AQUI/01_esquema.sql" postgres >/dev/null 2>&1
"${P[@]}" -f "$AQUI/02_popular.sql" postgres | tr '|' ' ' | sed 's/^/  /'
echo "Tamanho do banco: $("${P[@]}" -c "select pg_size_pretty(pg_database_size('postgres'))" postgres)"

echo; echo "== CONSULTAS (EXPLAIN ANALYZE, 2ª rodada com cache quente) =="
"${BIN}/psql" -h "$DIR" -p "$PORTA" -U postgres -q -f "$AQUI/03_consultas.sql" postgres >/dev/null 2>&1       # aquece
"${BIN}/psql" -h "$DIR" -p "$PORTA" -U postgres -q -f "$AQUI/03_consultas.sql" postgres > "$SAIDA/consultas.txt" 2>&1
grep -E '^== |Execution Time' "$SAIDA/consultas.txt" | awk '/^== /{t=$0; next} {printf "  %-95s %s\n", t, $3" ms"}' | sed 's/^== //'

preparar() {  # zera a palestra de teste e a sequência de alunos (1..5000)
  "${P[@]}" postgres -c "DELETE FROM presencas WHERE aula_id = (SELECT max(id) FROM aulas WHERE titulo='Palestra de carga')" \
            -c "DROP SEQUENCE IF EXISTS seq_f9" -c "CREATE SEQUENCE seq_f9 MAXVALUE 5000 START 1" >/dev/null 2>&1
}
"${P[@]}" postgres -c "INSERT INTO aulas (professor_id, titulo, latitude, longitude, raio_metros, tipo, local_tipo, ativa, aberta_em) VALUES (3, 'Palestra de carga', -18.4157, -49.2183, 200, 'palestra', 'dentro', true, now())" >/dev/null
AULA=$("${P[@]}" postgres -c "SELECT max(id) FROM aulas WHERE titulo='Palestra de carga'")
echo; echo "== 5.000 CHECK-INS (40 conexões = 2 workers x 20 do pool) =="
for espera in 0 250 2500; do
  preparar
  n=$((espera == 2500 ? 25 : 125))
  r=$("${PB[@]}" -n -f "$AQUI/04_checkin.pgbench" -D aula="$AULA" -D espera_ms="$espera" -c 40 -j 2 -t "$n" postgres 2>&1)
  tps=$(echo "$r" | sed -n 's/^tps = \([0-9.]*\).*/\1/p'); lat=$(echo "$r" | sed -n 's/^latency average = \([0-9.]*\) ms/\1/p'); feitas=$(echo "$r" | sed -n 's/^number of transactions actually processed: //p')
  printf "  consulta de VPN segurando a conexão por %5s ms: %s check-ins, %8.1f por segundo, latência média %8.1f ms\n" "$espera" "$feitas" "$tps" "$lat"
done
echo; echo "== ATUALIZAÇÃO DA TELA DO ALUNO (3 consultas por pedido, 40 conexões, 15 s) =="
r=$("${PB[@]}" -n -f "$AQUI/05_polling.pgbench" -c 40 -j 2 -T 15 postgres 2>&1)
echo "  $(echo "$r" | sed -n 's/^tps = \([0-9.]*\).*/\1/p') pedidos por segundo; latência média $(echo "$r" | sed -n 's/^latency average = \([0-9.]*\) ms/\1/p') ms"
