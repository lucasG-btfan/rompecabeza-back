"""
Modelo `Emparejamiento` (C-17, D1) — duelo 1v1 entre dos usuarios sobre una
partida del lobby.

Restricciones de integridad (spec `emparejamientos`):
- Índice UNIQUE parcial `uq_emparejamiento_partida_activo`: a lo sumo UNA
  fila activa (`esperando` o `emparejado`) por partida.
- Índice UNIQUE parcial `uq_emparejamiento_jugador1_esperando`: un usuario
  espera a lo sumo en UNA partida (estado `esperando`).
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
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

    # A lo sumo una fila activa (esperando|emparejado) por partida.
    __table_args__ = (
        Index(
            "uq_emparejamiento_partida_activo",
            "partida_id",
            unique=True,
            postgresql_where=text("estado IN ('esperando', 'emparejado')"),
        ),
        # Un usuario espera a lo sumo en una partida.
        Index(
            "uq_emparejamiento_jugador1_esperando",
            "jugador1_id",
            unique=True,
            postgresql_where=text("estado = 'esperando'"),
        ),
    )