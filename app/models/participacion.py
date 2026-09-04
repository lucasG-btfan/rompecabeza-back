import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime, Integer, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class Participacion(Base):
    __tablename__ = "participaciones"
    __table_args__ = (
        UniqueConstraint("partida_id", "usuario_id", name="uq_participacion_partida_usuario"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    partida_id = Column(UUID(as_uuid=True), ForeignKey("partidas.id", ondelete="CASCADE"), nullable=False)
    usuario_id = Column(UUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    rol = Column(String(20), nullable=False)  # 'creador' | 'jugador'
    palabras_encontradas = Column(Integer, default=0, nullable=False)
    unido_en = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    iniciado_en = Column(DateTime(timezone=True), nullable=True)   # arranca el cronómetro
    finalizado_en = Column(DateTime(timezone=True), nullable=True)  # encontró todas las palabras

    partida = relationship("Partida", back_populates="participaciones")
    usuario = relationship("Usuario")
    hallazgos = relationship("Hallazgo", cascade="all, delete-orphan")
