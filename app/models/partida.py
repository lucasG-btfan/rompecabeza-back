from sqlalchemy import Column, String, DateTime, ForeignKey, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import uuid

from app.database import Base


class Partida(Base):
    __tablename__ = "partidas"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    codigo = Column(String(10), unique=True, nullable=False, index=True)
    tipo = Column(String(20), nullable=False)  # 'sopa' o 'crucigrama'
    # FK real (antes era un VARCHAR suelto que nunca se usaba)
    creador_id = Column(UUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=True)
    creado_en = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    estado = Column(String(20), default="creando")  # 'creando', 'activo', 'finalizado'
    config = Column(JSON, default=dict)
    grilla = Column(JSON, nullable=True)  # Matriz de letras generada al finalizar (sopa)

    palabras = relationship("Palabra", back_populates="partida", cascade="all, delete-orphan")
    participaciones = relationship("Participacion", back_populates="partida", cascade="all, delete-orphan")
