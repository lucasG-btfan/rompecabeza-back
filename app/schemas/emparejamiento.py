from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from app.schemas.lobby import PartidaLobbyResponse


class EmparejamientoCreateRequest(BaseModel):
    codigo_partida: str

    model_config = ConfigDict(extra="forbid")


class AbandonarRequest(BaseModel):

    codigo_partida: str

    model_config = ConfigDict(extra="forbid")


class EmparejamientoEstadoResponse(BaseModel):

    estado: Optional[
        Literal["esperando", "emparejado", "cancelado", "expirado", "finalizado"]
    ] = None
    partida: Optional[PartidaLobbyResponse] = None
    rival: Optional[str] = None  # username del otro jugador (solo si emparejado)
    creado_en: Optional[datetime] = None
    emparejado_en: Optional[datetime] = None
    yo_palabras: Optional[int] = None  # contador propio (solo si emparejado)
    rival_palabras: Optional[int] = None  # contador del rival (solo si emparejado)
    resultado: Optional["DueloResultadoResponse"] = None  # solo si finalizado

    model_config = ConfigDict(extra="forbid")


class DueloResultadoResponse(BaseModel):

    yo_palabras: int
    rival_palabras: int
    gane: Optional[bool] = None
    motivo: Literal["corte", "abandono", "empate"]
    rival: Optional[str] = None  # username del otro jugador
    tiempo_total_seg: int = 0
    finalizado_en: Optional[datetime] = None

    model_config = ConfigDict(extra="forbid")