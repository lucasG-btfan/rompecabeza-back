from pydantic import BaseModel, Field, ConfigDict, field_validator
from uuid import UUID
from datetime import datetime
from typing import Optional, Literal

from app.schemas.emparejamiento import DueloResultadoResponse


class PalabraCreate(BaseModel):
    palabra: str = Field(..., min_length=1, max_length=50)
    explicacion: Optional[str] = None


class PalabraResponse(BaseModel):
    id: UUID
    palabra: str
    texto_mostrar: Optional[str] = None
    explicacion: Optional[str]
    posicion: Optional[dict]
    encontrada: bool

    class Config:
        from_attributes = True


class EditorPartidaResponse(BaseModel):

    codigo: str
    tipo: str
    estado: str
    palabras: list[PalabraResponse]
    nombre: Optional[str] = None  

    model_config = ConfigDict(extra="forbid")


class CrearPartidaRequest(BaseModel):

    tipo: str = Field(..., pattern="^(sopa|crucigrama)$")
    palabras: list[PalabraCreate] = Field(..., min_length=1)
    config: Optional[dict] = None
    nombre: Optional[str] = Field(None, max_length=50)  

    model_config = ConfigDict(extra="forbid")

    @field_validator("nombre")
    @classmethod
    def _normalizar_nombre(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        return v or None


class CrearPartidaResponse(BaseModel):
    id: UUID
    codigo: str
    tipo: str
    estado: str
    nombre: Optional[str] = None  


class PartidaResponse(BaseModel):
    id: UUID
    codigo: str
    tipo: str
    estado: str
    palabras: list[PalabraResponse]
    config: Optional[dict]
    creado_en: datetime

    class Config:
        from_attributes = True


class PalabraPublicaResponse(BaseModel):
    id: UUID
    palabra: Optional[str] = None
    texto_mostrar: Optional[str] = None
    explicacion: Optional[str]
    posicion: Optional[dict]  # Se fuerza a None si encontrada=False (ver route)
    encontrada: bool


class PartidaPublicaResponse(BaseModel):
    id: UUID
    codigo: str
    tipo: str
    estado: str
    palabras: list[PalabraPublicaResponse]
    config: Optional[dict]
    creado_en: datetime
    es_creador: bool = False
    nombre: Optional[str] = None  

class FinalizarRequest(BaseModel):
    """Body del POST /finalizar. El front actual lo llama sin body; se declara
    con `extra='forbid'` para que un campo no declarado falle con 422."""

    model_config = ConfigDict(extra="forbid")


class GrillaCeldaCrucigrama(BaseModel):
    letra: Optional[str] = None
    numero: Optional[int] = None
    tipo: Literal["letra", "negra"]

    model_config = ConfigDict(extra="forbid")


class PalabraGrillaCrucigrama(BaseModel):
    numero: int
    orientacion: Literal["H", "V"]
    posicion: dict
    longitud: int
    texto: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class GrillaCrucigrama(BaseModel):
    celdas: list[GrillaCeldaCrucigrama]
    palabras: list[PalabraGrillaCrucigrama]

    model_config = ConfigDict(extra="forbid")


class AgregarPalabrasRequest(BaseModel):
    palabras: list[PalabraCreate] = Field(..., min_length=1)


class PosicionUpdate(BaseModel):
    # REVISIÓN 9.x (D1): fila/columna pierden `ge=0` — el editor manual de
    # crucigrama necesita coordenadas negativas para extender palabras hacia
    # arriba/izquierda del bbox actual (bug ARENA/ESPEJO). La rama `sopa` de
    # la ruta agrega una validación explícita de no negatividad (400 amigable)
    # para preservar su comportamiento previo; la rama crucigrama las acepta.
    fila: int
    columna: int
    orientacion: str  # sopa: "E","O","N","S","SE","SO","NE","NO"; crucigrama: "H","V"

    model_config = ConfigDict(extra="forbid")


class EdicionRequest(BaseModel):
    fila: int = Field(..., ge=0)
    columna: int = Field(..., ge=0)
    letra: str = Field(..., min_length=1, max_length=1)


class FinalizarResponse(BaseModel):
    codigo: str
    estado: str
    filas: int
    columnas: int
    grilla: Optional[GrillaCrucigrama] = None


class EstadoPalabraResponse(BaseModel):
    id: UUID
    palabra: Optional[str] = None
    texto_mostrar: Optional[str] = None
    numero: Optional[int] = None
    encontrada: bool
    posicion: Optional[dict] = None  # Solo se revela si encontrada=True


class EstadoPartidaResponse(BaseModel):
    codigo: str
    tipo: str
    estado: str
    grilla: Optional[list[list[str]] | GrillaCrucigrama] = None
    palabras: list[EstadoPalabraResponse]


class EncontradaRequest(BaseModel):
    """Body del PUT /palabras/{id}/encontrada de sopa. `extra='forbid'`
    (regla dura 5): campo no declarado → 422."""

    fila_inicio: int = Field(..., ge=0)
    columna_inicio: int = Field(..., ge=0)
    fila_fin: int = Field(..., ge=0)
    columna_fin: int = Field(..., ge=0)

    model_config = ConfigDict(extra="forbid")


class RespuestaRequest(BaseModel):
    letras: str

    model_config = ConfigDict(extra="forbid")


class EncontradaResponse(BaseModel):
    encontrada: bool
    posicion: Optional[dict] = None
    duelo_finalizado: Optional[DueloResultadoResponse] = None


class ResumenPartidaResponse(BaseModel):
    """Vista resumida de una partida creada por el usuario, para la lista 'Mis partidas'."""
    id: UUID
    codigo: str
    tipo: str
    estado: str
    creado_en: datetime
    palabras_total: int
    palabras_encontradas: int
    nombre: Optional[str] = None  
    en_duelo: bool = False  
    en_espera: bool = False


class ActualizarNombrePartidaRequest(BaseModel):

    nombre: Optional[str] = Field(None, max_length=50)

    model_config = ConfigDict(extra="forbid")

    @field_validator("nombre")
    @classmethod
    def _normalizar_nombre(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        return v or None
