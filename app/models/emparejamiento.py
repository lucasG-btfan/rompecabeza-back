import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class EmparejamientoEstado(str, enum.Enum):
    ESPERANDO = "esperando"
    EMPAREJADO = "emparejado"
    CANCELADO = "cancelado"
    EXPIRADO = "expirado"
    FINALIZADO = "finalizado"


class Emparejamiento(Base):
    __tablename__ = "emparejamientos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    partida_id = Column(
        UUID(as_uuid=True), ForeignKey("partidas.id", ondelete="CASCADE"), nullable=False
    )
    jugador1_id = Column(
        UUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False
    )
    jugador2_id = Column(UUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=True)
    estado = Column(String(20), default=EmparejamientoEstado.ESPERANDO.value, nullable=False)
    creado_en = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    emparejado_en = Column(DateTime(timezone=True), nullable=True)
    iniciado_en = Column(DateTime(timezone=True), nullable=True)
    finalizado_en = Column(DateTime(timezone=True), nullable=True)
    ganador_id = Column(
        UUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    jugador1_palabras = Column(Integer, default=0, nullable=False)
    jugador2_palabras = Column(Integer, default=0, nullable=False)

    __table_args__ = (
        Index(
            "uq_emparejamiento_partida_activo",
            "partida_id",
            unique=True,
            postgresql_where=text("estado IN ('esperando', 'emparejado')"),
        ),
        Index(
            "uq_emparejamiento_jugador1_esperando",
            "jugador1_id",
            unique=True,
            postgresql_where=text("estado = 'esperando'"),
        ),
    )


def _migrar_emparejamientos(engine):
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE emparejamientos "
                "ADD COLUMN IF NOT EXISTS "
                "jugador1_palabras INTEGER NOT NULL DEFAULT 0, "
                "ADD COLUMN IF NOT EXISTS "
                "jugador2_palabras INTEGER NOT NULL DEFAULT 0"
            )
        )