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
    # Versión que va a la GRILLA: solo letras, mayúsculas (ej. "COAUTOR").
    # El posicionamiento, el largo físico y el anti-cheat operan sobre este valor.
    palabra = Column(String(50), nullable=False)
    # Versión PRESENTABLE: conserva separadores (ej. "CO-AUTOR", "SR FRIO").
    # Se usa para los chips de la sopa, las pistas del crucigrama y el editor.
    # Nullable porque las filas pre-migración no lo tienen (ver backfill).
    texto_mostrar = Column(String(50), nullable=True)
    explicacion = Column(Text, nullable=True)  # Solo para crucigrama
    posicion = Column(JSON, nullable=True)  # {fila, columna, orientacion}
    encontrada = Column(Boolean, default=False)

    # Quién y cuándo la encontró (nullable: invitados no quedan registrados)
    encontrada_por = Column(UUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=True)
    encontrada_en = Column(DateTime(timezone=True), nullable=True)

    partida = relationship("Partida", back_populates="palabras")
