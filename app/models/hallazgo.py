import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, JSON, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class Hallazgo(Base):
    """Registro de una palabra encontrada por una participación (un jugador).
    Cada participación guarda SUS propias palabras encontradas.
    """

    __tablename__ = "hallazgos"
    __table_args__ = (
        UniqueConstraint("participacion_id", "palabra_id", name="uq_hallazgo_participacion_palabra"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    participacion_id = Column(
        UUID(as_uuid=True),
        ForeignKey("participaciones.id", ondelete="CASCADE"),
        nullable=False,
    )
    palabra_id = Column(
        UUID(as_uuid=True),
        ForeignKey("palabras.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Posición en la que ESE jugador encontró la palabra (copiada al momento).
    posicion = Column(JSON, nullable=True)
    encontrado_en = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
