from pydantic import BaseModel, Field, ConfigDict
from uuid import UUID
from datetime import datetime
from typing import Optional, Literal


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


class CrearPartidaRequest(BaseModel):
    tipo: str = Field(..., pattern="^(sopa|crucigrama)$")
    palabras: list[PalabraCreate] = Field(..., min_length=1)
    config: Optional[dict] = None


class CrearPartidaResponse(BaseModel):
    id: UUID
    codigo: str
    tipo: str
    estado: str


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
    """Igual a PalabraResponse pero SIN filtrar la posicion de palabras no encontradas."""
    id: UUID
    palabra: str
    texto_mostrar: Optional[str] = None
    explicacion: Optional[str]
    posicion: Optional[dict]  # Se fuerza a None si encontrada=False (ver route)
    encontrada: bool


class PartidaPublicaResponse(BaseModel):
    """
    Vista segura de una partida para exponer por GET /partidas/{codigo}.
    A diferencia de PartidaResponse, nunca revela `posicion` de una palabra
    todavía no encontrada (evita cheat leyendo el endpoint directamente).
    """
    id: UUID
    codigo: str
    tipo: str
    estado: str
    palabras: list[PalabraPublicaResponse]
    config: Optional[dict]
    creado_en: datetime


# --------------------------------------------------------------------------
# Nuevos schemas - Sopa de Letras
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Nuevos schemas - Crucigrama
# Se definen ANTES de FinalizarResponse/EstadoPartidaResponse porque esos
# schemas los referencian en sus campos (`grilla`).
# --------------------------------------------------------------------------

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
    texto: str

    model_config = ConfigDict(extra="forbid")


class GrillaCrucigrama(BaseModel):
    celdas: list[GrillaCeldaCrucigrama]
    palabras: list[PalabraGrillaCrucigrama]

    model_config = ConfigDict(extra="forbid")


class AgregarPalabrasRequest(BaseModel):
    palabras: list[PalabraCreate] = Field(..., min_length=1)


class PosicionUpdate(BaseModel):
    fila: int = Field(..., ge=0)
    columna: int = Field(..., ge=0)
    orientacion: str  # "E","O","N","S","SE","SO","NE","NO"


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
    palabra: str
    texto_mostrar: Optional[str] = None
    encontrada: bool
    posicion: Optional[dict] = None  # Solo se revela si encontrada=True


class EstadoPartidaResponse(BaseModel):
    codigo: str
    tipo: str
    estado: str
    grilla: Optional[list[list[str]] | GrillaCrucigrama] = None
    palabras: list[EstadoPalabraResponse]


class EncontradaRequest(BaseModel):
    fila_inicio: int = Field(..., ge=0)
    columna_inicio: int = Field(..., ge=0)
    fila_fin: int = Field(..., ge=0)
    columna_fin: int = Field(..., ge=0)


class EncontradaResponse(BaseModel):
    encontrada: bool
    posicion: Optional[dict] = None


class ResumenPartidaResponse(BaseModel):
    """Vista resumida de una partida creada por el usuario, para la lista 'Mis partidas'."""
    id: UUID
    codigo: str
    tipo: str
    estado: str
    creado_en: datetime
    palabras_total: int
    palabras_encontradas: int
