"""baseline - esquema actual (partidas, palabras)

Esta migración representa el estado en el que ya está tu base de datos hoy
(las tablas nacieron de create_all(), sin pasar por Alembic). NO la corras
con `alembic upgrade` sobre tu DB actual -- ejecutaría CREATE TABLE sobre
tablas que ya existen y va a fallar. En su lugar, marcá tu DB como si ya
estuviera en esta revisión:

    alembic stamp 0001_baseline

Para un setup nuevo desde cero (por ejemplo en Render), esta migración sí
se corre normalmente con `alembic upgrade head` y crea las tablas.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-08-27
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "partidas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("codigo", sa.String(10), nullable=False),
        sa.Column("tipo", sa.String(20), nullable=False),
        sa.Column("creador_id", sa.String(255), nullable=True),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("estado", sa.String(20), nullable=True),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("grilla", sa.JSON(), nullable=True),
    )
    op.create_index("ix_partidas_codigo", "partidas", ["codigo"], unique=True)

    op.create_table(
        "palabras",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "partida_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("partidas.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("palabra", sa.String(50), nullable=False),
        sa.Column("explicacion", sa.Text(), nullable=True),
        sa.Column("posicion", sa.JSON(), nullable=True),
        sa.Column("encontrada", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("palabras")
    op.drop_index("ix_partidas_codigo", table_name="partidas")
    op.drop_table("partidas")
