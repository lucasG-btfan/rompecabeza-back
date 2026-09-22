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


class AbandonarRequest(BaseModel):
    """Abandono (forfeit) del duelo 1v1 (C-19, D4). Solo identifica la partida;
    quien abandona es el usuario de la sesión (`get_usuario_actual`)."""

    codigo_partida: str

    model_config = ConfigDict(extra="forbid")


class EmparejamientoEstadoResponse(BaseModel):
    """Estado del emparejamiento del usuario logueado, o la última transición
    relevante para su flujo (`cancelado`/`expirado` se reportan una vez).
    `finalizado` es ESTABLE (D6): trae `resultado` y nunca se consume.

    AMEND CAMBIO 2: `yo_palabras`/`rival_palabras` (solo si `emparejado`)
    normalizados por requester — el contador visible "[jugador1] n/m
    [jugador2] n/m" sin revelar quién es quién."""

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
    """Resultado del duelo 1v1 normalizado por requester (C-19, D5).

    `yo_palabras`/`rival_palabras` son relativos a quien consulta; `gane`
    es True/False para él, o `None` cuando el duelo terminó en empate.
    `tiempo_total_seg` = `iniciado_en → finalizado_en` (D14: el mismo reloj
    para ambos; un abandono antes del arranque da 0).
    `motivo` (C-25, D2) explica el POR QUÉ del resultado: `corte` (ganó el
    primero que completó `len(palabras)`, el copy del C-19 era honesto para
    este caso), `abandono` (forfeit del rival — quien gana por forfait puede
    tener MENOS palabras, RN-EM-07) o `empate`. Requerido (D4): el frontend
    abre el copy por `(gane, motivo)`.
    """

    yo_palabras: int
    rival_palabras: int
    gane: Optional[bool] = None
    motivo: Literal["corte", "abandono", "empate"]
    rival: Optional[str] = None  # username del otro jugador
    tiempo_total_seg: int = 0
    finalizado_en: Optional[datetime] = None

    model_config = ConfigDict(extra="forbid")