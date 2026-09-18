"""nombre de partida - etiqueta opcional del creador

Agrega la columna `partidas.nombre` (VARCHAR(50) NULL) para que el creador
pueda etiquetar su partida y distinguirla en "Mis partidas" (C-15).

Aditiva y SIN backfill: las partidas existentes quedan con NULL y el
frontend muestra el código como fallback. El nombre no afecta el juego.

Revision ID: 0004_nombre_partida
Revises: 0003_hallazgos
Create Date: 2026-09-17
"""
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