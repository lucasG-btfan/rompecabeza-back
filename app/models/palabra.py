import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Text, Boolean, ForeignKey, JSON, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class Palabra(Base):
    __tablename__ = "palabras"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    partida_id = Column(UUID(as_uuid=True), ForeignKey("partidas.id", ondelete="CASCADE"), nullable=False)
    palabra = Column(String(50), nullable=False)
    explicacion = Column(Text, nullable=True)  # Solo para crucigrama
    posicion = Column(JSON, nullable=True)  # {fila, columna, orientacion}
    encontrada = Column(Boolean, default=False)

    # Quién y cuándo la encontró (nullable: invitados no quedan registrados)
    encontrada_por = Column(UUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=True)
    encontrada_en = Column(DateTime(timezone=True), nullable=True)

    partida = relationship("Partida", back_populates="palabras")
