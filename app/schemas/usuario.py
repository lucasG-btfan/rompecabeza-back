from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime
from typing import Optional


class UsuarioCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=100)


class LoginRequest(BaseModel):
    username: str
    password: str


class UsuarioResponse(BaseModel):
    id: UUID
    username: str
    creado_en: datetime

    class Config:
        from_attributes = True


class RankingEntry(BaseModel):
    username: str
    rol: str
    palabras_encontradas: int
    tiempo_segundos: Optional[int] = None
    puntaje: int


class UnirseResponse(BaseModel):
    modo: str  # "registrado" | "invitado"
    iniciado_en: Optional[datetime] = None
