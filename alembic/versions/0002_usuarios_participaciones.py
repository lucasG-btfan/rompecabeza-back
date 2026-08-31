"""usuarios y participaciones - sistema de identidad y puntaje

Agrega:
  - tabla usuarios
  - tabla participaciones (usuario x partida, con puntaje)
  - partidas.creador_id pasa de VARCHAR suelto a FK real -> usuarios.id
    (los valores existentes se limpian a NULL: nunca se usaron de verdad,
    así que no hay nada real que preservar ahí)
  - palabras.encontrada_por / palabras.encontrada_en (nullable, para
    invitados que no quedan registrados)

Revision ID: 0002_usuarios_participaciones
Revises: 0001_baseline
Create Date: 2026-08-27
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_usuarios_participaciones"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- usuarios ---
    op.create_table(
        "usuarios",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("username", sa.String(50), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_usuarios_username", "usuarios", ["username"], unique=True)

    # --- participaciones ---
    op.create_table(
        "participaciones",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "partida_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("partidas.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "usuario_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("usuarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rol", sa.String(20), nullable=False),
        sa.Column("palabras_encontradas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unido_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("iniciado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalizado_en", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("partida_id", "usuario_id", name="uq_participacion_partida_usuario"),
    )

    # --- palabras: quién y cuándo encontró cada una ---
    op.add_column(
        "palabras",
        sa.Column("encontrada_por", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "palabras",
        sa.Column("encontrada_en", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_palabras_encontrada_por_usuarios",
        "palabras", "usuarios",
        ["encontrada_por"], ["id"],
    )

    # --- partidas.creador_id: VARCHAR suelto -> UUID FK real ---
    # Los valores existentes nunca se usaron (siempre vacíos/None en la práctica),
    # así que los limpiamos antes de cambiar el tipo de columna.
    op.execute("UPDATE partidas SET creador_id = NULL")
    op.alter_column(
        "partidas",
        "creador_id",
        existing_type=sa.String(255),
        type_=postgresql.UUID(as_uuid=True),
        postgresql_using="NULL::uuid",
        nullable=True,
    )
    op.create_foreign_key(
        "fk_partidas_creador_id_usuarios",
        "partidas", "usuarios",
        ["creador_id"], ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_partidas_creador_id_usuarios", "partidas", type_="foreignkey")
    op.alter_column(
        "partidas",
        "creador_id",
        existing_type=postgresql.UUID(as_uuid=True),
        type_=sa.String(255),
        postgresql_using="NULL",
        nullable=True,
    )

    op.drop_constraint("fk_palabras_encontrada_por_usuarios", "palabras", type_="foreignkey")
    op.drop_column("palabras", "encontrada_en")
    op.drop_column("palabras", "encontrada_por")

    op.drop_table("participaciones")

    op.drop_index("ix_usuarios_username", table_name="usuarios")
    op.drop_table("usuarios")
