from typing import Optional

from pydantic import BaseModel


class PartidaLobbyResponse(BaseModel):
    codigo: str
    tipo: str  # "sopa" | "crucigrama"
    cantidad_palabras: int
    nombre: Optional[str] = None
    en_duelo: bool = False
    en_espera: bool = False