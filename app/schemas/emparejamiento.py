"""
Schemas de emparejamientos (C-17, D7/D16).

`EmparejamientoCreateRequest` con `extra='forbid'` (regla dura 5).
`EmparejamientoEstadoResponse` es la respuesta del POST y del poll GET
estado: `partida` viaja con los metadatos del lobby (anti-cheat).
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from app.schemas.lobby import PartidaLobbyResponse


class EmparejamientoCreateRequest(BaseModel):
    codigo_partida: str

    model_config = ConfigDict(extra="forbid")


class EmparejamientoEstadoResponse(BaseModel):
    """Estado del emparejamiento del usuario logueado, o la última transición
    relevante para su flujo (`cancelado`/`expirado` se reportan una vez)."""

    estado: Optional[Literal["esperando", "emparejado", "cancelado", "expirado"]] = None
    partida: Optional[PartidaLobbyResponse] = None
    rival: Optional[str] = None  # username del otro jugador (solo si emparejado)
    creado_en: Optional[datetime] = None
    emparejado_en: Optional[datetime] = None