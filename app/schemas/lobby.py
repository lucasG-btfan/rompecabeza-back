"""
Schemas del lobby (C-17, D5/D16) — lista pública de partidas activas.

ANTI-CHEAT (spec `lobby`): solo `codigo`, `tipo`, `cantidad_palabras`,
`nombre`, `en_duelo` y `en_espera`. NUNCA palabras, posiciones, grilla ni
explicaciones.
"""

from typing import Optional

from pydantic import BaseModel


class PartidaLobbyResponse(BaseModel):
    codigo: str
    tipo: str  # "sopa" | "crucigrama"
    cantidad_palabras: int
    nombre: Optional[str] = None
    en_duelo: bool = False
    # C-23 (D4): aditivo — true SOLO si hay una espera de rival pendiente
    # (`esperando`); default False = partida libre (no rompe consumidores).
    en_espera: bool = False