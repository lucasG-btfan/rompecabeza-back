"""hallazgos - progreso por jugador

El progreso de "palabras encontradas" debe ser POR participación (cada jugador
empieza de 0 y solo ve resaltadas las suyas), en lugar de la bandera global
`Palabra.encontrada`. Esta tabla registra, por cada participación, qué palabras
encontró y en qué posición.

Nota: los invitados no tienen participación, así que NO se persisten sus
hallazgos -- el front los guarda en localStorage.

Revision ID: 0003_hallazgos
Revises: 0002_usuarios_participaciones
Create Date: 2026-09-02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_hallazgos"
down_revision = "0002_usuarios_participaciones"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hallazgos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "participacion_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("participaciones.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "palabra_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("palabras.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("posicion", sa.JSON(), nullable=True),
        sa.Column("encontrado_en", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("participacion_id", "palabra_id", name="uq_hallazgo_participacion_palabra"),
    )
    op.create_index(
        "ix_hallazgos_participacion_id", "hallazgos", ["participacion_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_hallazgos_participacion_id", table_name="hallazgos")
    op.drop_table("hallazgos")
