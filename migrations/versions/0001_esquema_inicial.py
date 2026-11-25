"""esquema inicial: cria todas as tabelas do Guará Check

Idempotente: em banco novo cria tudo; em banco que já existe (o do Render, por exemplo) não altera nada e só registra
a versão. As colunas que a V2 acrescentou a bancos da V1 continuam sendo tratadas por migrar_esquema() (app.py).
REGRA para as próximas revisões: antes de adicionar coluna/índice, confira se já existe (inspect), porque esta
revisão usa os modelos atuais e, num banco novo, já cria as colunas mais recentes.
"""
from alembic import op

revision = "0001_esquema_inicial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app import Base
    Base.metadata.create_all(bind=op.get_bind())  # checkfirst: só cria o que falta


def downgrade() -> None:
    raise NotImplementedError("Voltar a revisão inicial apagaria todas as tabelas; faça isso à mão, com backup.")
