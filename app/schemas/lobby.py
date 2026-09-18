"""
Schemas del lobby (C-17, D5/D16) — lista pública de partidas activas.

ANTI-CHEAT (spec `lobby`): solo `codigo`, `tipo`, `cantidad_palabras`,
`nombre` y `en_duelo`. NUNCA palabras, posiciones, grilla ni explicaciones.
"""

from typing import Optional

from pydantic import BaseModel


class PartidaLobbyResponse(BaseModel):
    codigo: str
    tipo: str  # "sopa" | "crucigrama"
    cantidad_palabras: int
    nombre: Optional[str] = None
    en_duelo: bool = False