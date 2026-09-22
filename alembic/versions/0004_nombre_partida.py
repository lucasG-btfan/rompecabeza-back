from alembic import op
import sqlalchemy as sa

revision = "0004_nombre_partida"
down_revision = "0003_hallazgos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("partidas", sa.Column("nombre", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("partidas", "nombre")