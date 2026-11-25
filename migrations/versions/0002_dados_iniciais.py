"""dados iniciais: lista de cursos

Só dados públicos e fixos. Senhas, chaves e contas NUNCA entram em migration (ficam em variáveis de ambiente):
a conta provisória da coordenação continua sendo criada na subida do app, a partir de COORD_SENHA.
Idempotente: só insere o curso que ainda não existe, então pode rodar em banco novo ou em banco que já tem dados.
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_dados_iniciais"
down_revision = "0001_esquema_inicial"
branch_labels = None
depends_on = None

CURSOS = ["Administração", "Ciências Contábeis", "Direito", "Educação Física", "Enfermagem",
          "Engenharia Civil", "Engenharia de Software", "Fisioterapia", "Gestão da TI",
          "Medicina", "Pedagogia", "Psicologia"]

cursos = sa.table("cursos", sa.column("id", sa.Integer), sa.column("nome", sa.String), sa.column("ativo", sa.Boolean))


def upgrade() -> None:
    conn = op.get_bind()
    existentes = {r[0] for r in conn.execute(sa.select(cursos.c.nome))}
    novos = [{"nome": n, "ativo": True} for n in CURSOS if n not in existentes]
    if novos:
        op.bulk_insert(cursos, novos)


def downgrade() -> None:
    # Não apaga cursos: alunos e chamadas podem depender deles.
    pass
