#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Teste de carga do Guará Check contra um servidor DE VERDADE (a Render, depois do deploy). Só usa a biblioteca padrão do Python.

O que faz: cada aluno virtual entra (login), consulta as chamadas abertas de ~10 em ~10 s (como a tela do aluno) e registra a
presença uma vez, com o GPS da sala. No fim mostra, por tipo de chamada, quantas deram certo, quais erros apareceram e o tempo de
resposta (mediana, p95, p99, máximo).

ATENÇÃO: isto CRIA presenças de verdade. Use só com contas de TESTE e com uma chamada de teste aberta por um professor de teste
(depois, apague a chamada pela coordenação). Não use contas de alunos reais. Por isso exige --confirmo.

Como preparar (uma vez):
  1. Na coordenação, importe matrículas de teste (ex.: TESTE0001 a TESTE0500) e cadastre as contas (ou peça os cadastros pela
     tela). Salve num arquivo contas_teste.csv, uma por linha:  email;senha
  2. Como professor de teste, abra uma chamada com o raio de 200 m na sala de teste e anote o número dela (aparece na URL do
     CSV exportado, ou no painel da coordenação).

Exemplo:
  python ferramentas/teste_de_carga.py --url https://guaracheck.onrender.com --contas contas_teste.csv --aula 123 \\
      --lat -18.4157 --lon -49.2183 --alunos 200 --rampa 30 --duracao 120 --confirmo

Dica: rode primeiro com --alunos 20 e vá subindo (50, 100, 200, 500). Se o tempo de resposta passar de 2-3 s ou aparecerem
erros 500/502/503/429, esse é o limite do plano atual.
"""
import argparse
import csv
import json
import random
import ssl
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter, defaultdict


def chamar(base, caminho, metodo="GET", corpo=None, token=None, timeout=30):
    """Devolve (status, json_ou_None, segundos). status 0 = sem resposta (timeout, conexão recusada, DNS...)."""
    dados = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(base + caminho, data=dados, method=metodo)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
            txt = r.read()
            st = r.status
    except urllib.error.HTTPError as e:
        txt, st = e.read(), e.code
    except Exception:
        return 0, None, time.perf_counter() - t0
    dt = time.perf_counter() - t0
    try:
        return st, json.loads(txt), dt
    except Exception:
        return st, None, dt


class Medidas:
    def __init__(self):
        self.trava = threading.Lock()
        self.tempos = defaultdict(list)       # tipo -> [segundos]
        self.status = defaultdict(Counter)    # tipo -> {status: quantidade}

    def anotar(self, tipo, status, seg):
        with self.trava:
            self.tempos[tipo].append(seg)
            self.status[tipo][status] += 1


def aluno_virtual(base, conta, a, medidas, fim, inicio_em):
    email, senha = conta
    time.sleep(max(0.0, inicio_em - time.monotonic()))
    st, d, seg = chamar(base, "/api/auth/login", "POST", {"email": email, "senha": senha, "perfil": "aluno"})
    medidas.anotar("login", st, seg)
    if st != 200 or not d:
        return
    token = d["token"]
    device = uuid.uuid4().hex                                        # 32 letras/números: cada aluno virtual tem o seu aparelho
    momento_checkin = time.monotonic() + random.uniform(0, min(30.0, a.duracao / 2))
    registrou = False
    proxima = time.monotonic()
    while time.monotonic() < fim:
        if time.monotonic() >= proxima:
            st, _, seg = chamar(base, "/api/aulas/ativas", token=token)
            medidas.anotar("consultar chamadas", st, seg)
            proxima = time.monotonic() + 10 * random.uniform(0.8, 1.2)        # como a tela do aluno: ~10 s, com variação
        if not registrou and time.monotonic() >= momento_checkin:
            corpo = {"latitude": a.lat + random.uniform(-1e-5, 1e-5), "longitude": a.lon + random.uniform(-1e-5, 1e-5), "precisao_m": 10.0,
                     "pos_ts": time.time() * 1000, "amostras": 5, "variacao_m": 2.0, "webdriver": False, "device_id": device,
                     "fp_hash": uuid.uuid4().hex}
            st, _, seg = chamar(base, f"/api/aulas/{a.aula}/presenca", "POST", corpo, token)
            medidas.anotar("registrar presença", st, seg)
            registrou = True
        acordar = min(fim, proxima, proxima if registrou else momento_checkin)
        time.sleep(max(0.0, acordar - time.monotonic()))


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))]


def relatorio(medidas, total_s):
    saida = {}
    for tipo in ("login", "consultar chamadas", "registrar presença"):
        t = medidas.tempos.get(tipo)
        if not t:
            continue
        st = medidas.status[tipo]
        ok = st.get(200, 0)
        saida[tipo] = {"total": len(t), "ok": ok, "status": {str(k): v for k, v in sorted(st.items())},
                       "mediana_ms": round(statistics.median(t) * 1000), "p95_ms": round(pct(t, 95) * 1000),
                       "p99_ms": round(pct(t, 99) * 1000), "max_ms": round(max(t) * 1000),
                       "por_segundo": round(len(t) / max(total_s, 1e-9), 1)}
    return saida


def main(argv=None):
    p = argparse.ArgumentParser(description="Teste de carga do Guará Check (cria presenças de verdade: use contas de teste).")
    p.add_argument("--url", required=True, help="endereço do servidor, ex.: https://guaracheck.onrender.com")
    p.add_argument("--contas", required=True, help="arquivo CSV com email;senha (uma conta de teste por linha)")
    p.add_argument("--aula", type=int, required=True, help="número da chamada de TESTE aberta")
    p.add_argument("--lat", type=float, required=True, help="latitude da sala de teste")
    p.add_argument("--lon", type=float, required=True, help="longitude da sala de teste")
    p.add_argument("--alunos", type=int, default=20, help="quantos alunos virtuais (padrão 20; não passa do nº de contas)")
    p.add_argument("--rampa", type=float, default=10.0, help="segundos para todos entrarem (padrão 10)")
    p.add_argument("--duracao", type=float, default=60.0, help="segundos de teste depois do início (padrão 60)")
    p.add_argument("--json", action="store_true", help="imprime o resultado em JSON")
    p.add_argument("--confirmo", action="store_true", help="confirma que as contas e a chamada são de TESTE")
    a = p.parse_args(argv)
    if not a.confirmo:
        print("Recusado: este teste cria presenças de verdade. Confirme com --confirmo (só com contas e chamada de TESTE).", file=sys.stderr)
        return 2
    if not a.url.startswith(("http://", "https://")):
        print("--url precisa começar com http:// ou https://", file=sys.stderr)
        return 2
    contas = []
    with open(a.contas, encoding="utf-8-sig", newline="") as f:
        for lin in csv.reader(f, delimiter=";" if ";" in open(a.contas, encoding="utf-8-sig").readline() else ","):
            if len(lin) >= 2 and "@" in lin[0]:
                contas.append((lin[0].strip().lower(), lin[1].strip()))
    if not contas:
        print("Nenhuma conta no arquivo (formato: email;senha).", file=sys.stderr)
        return 2
    n = min(a.alunos, len(contas))
    if n < a.alunos:
        print(f"Aviso: só há {len(contas)} contas; rodando com {n} alunos.", file=sys.stderr)
    base = a.url.rstrip("/")
    medidas = Medidas()
    inicio = time.monotonic()
    fim = inicio + a.rampa + a.duracao
    threads = [threading.Thread(target=aluno_virtual, args=(base, contas[i], a, medidas, fim, inicio + random.uniform(0, a.rampa)), daemon=True)
               for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=max(1.0, fim - time.monotonic() + 60))
    r = relatorio(medidas, time.monotonic() - inicio)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(f"\n{n} alunos virtuais, {time.monotonic() - inicio:.0f} s\n")
        print(f"{'tipo':<22}{'total':>7}{'ok':>7}{'mediana':>10}{'p95':>9}{'p99':>9}{'máx':>9}  status")
        for tipo, x in r.items():
            print(f"{tipo:<22}{x['total']:>7}{x['ok']:>7}{x['mediana_ms']:>8} ms{x['p95_ms']:>6} ms{x['p99_ms']:>6} ms{x['max_ms']:>6} ms  {x['status']}")
        print("\nStatus 0 = sem resposta (timeout/conexão). 429 = freio de tentativas. 5xx = erro do servidor (limite do plano?).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
