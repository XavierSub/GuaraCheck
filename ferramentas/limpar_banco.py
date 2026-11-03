"""
GeoCheckIn - limpeza dos dados de TESTE no PostgreSQL da Render.

O que APAGA (só as linhas, as tabelas continuam existindo):
  alunos, professores, aulas, presenças, tentativas recusadas,
  matrículas autorizadas e o registro de auditoria.
O que MANTÉM: a lista de cursos.

Este arquivo NÃO faz parte do projeto: não mexe em app.py, index.html nem no GitHub.
Rode com o Python do projeto (o que já tem o psycopg2 instalado):

    cd (pasta GeoCheckIn)
    .venv\\Scripts\\python.exe (caminho deste arquivo)\\limpar_banco.py
"""
import sys
from urllib.parse import urlsplit

TABELAS = ["presencas", "tentativas", "aulas", "alunos", "professores", "matriculas_autorizadas", "logs_seguranca"]


def main():
    try:
        import psycopg2
    except ImportError:
        sys.exit("Falta o psycopg2. Rode este arquivo com o Python do projeto: .venv\\Scripts\\python.exe limpar_banco.py")

    print("\nGeoCheckIn - limpeza dos dados de teste\n")
    print("Na Render: banco 'geocheckin-db' > Connections > 'External Database URL' (botão de copiar).")
    print("Essa URL tem a senha do banco. Não envie para ninguém.\n")
    url = input("Cole a URL aqui e aperte Enter: ").strip()
    if not url.startswith(("postgres://", "postgresql://")):
        sys.exit("Isso não parece uma URL de PostgreSQL (tem que começar com postgres:// ou postgresql://).")
    if "sslmode" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"

    partes = urlsplit(url)
    print(f"\nConectando em {partes.hostname} / banco '{partes.path.lstrip('/')}' ...")
    try:
        con = psycopg2.connect(url, connect_timeout=20)
    except Exception as exc:
        sys.exit(f"Não consegui conectar: {exc}\nConfira se copiou a URL EXTERNA inteira.")

    try:
        cur = con.cursor()
        cur.execute("select table_name from information_schema.tables where table_schema = 'public'")
        existentes = {r[0] for r in cur.fetchall()}
        alvo = [t for t in TABELAS if t in existentes]
        if not alvo:
            sys.exit("Não encontrei as tabelas do GeoCheckIn neste banco. Era a URL certa?")

        def contar():
            n = {}
            for t in alvo:  # nomes vêm da lista fixa acima, não de texto digitado
                cur.execute(f'select count(*) from "{t}"')
                n[t] = cur.fetchone()[0]
            return n

        antes = contar()
        print("\nO que existe agora:")
        for t, n in antes.items():
            print(f"  {t:<24} {n}")
        if "cursos" in existentes:
            cur.execute('select count(*) from "cursos"')
            print(f"  {'cursos (será mantido)':<24} {cur.fetchone()[0]}")

        if sum(antes.values()) == 0:
            print("\nNão há nada para apagar.")
            return

        print("\nIsso apaga TODAS as linhas das tabelas acima e não tem volta.")
        if input("Para confirmar, digite APAGAR: ").strip() != "APAGAR":
            print("Cancelado. Nada foi alterado.")
            return

        cur.execute("TRUNCATE " + ", ".join(f'"{t}"' for t in alvo) + " RESTART IDENTITY CASCADE")
        con.commit()
        depois = contar()
        print("\nPronto. Agora:")
        for t, n in depois.items():
            print(f"  {t:<24} {n}")
        print("\nPode criar as contas novas pelo site.")
    except SystemExit:
        raise
    except Exception as exc:
        con.rollback()
        sys.exit(f"Deu erro e NADA foi apagado: {exc}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
