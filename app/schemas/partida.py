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
    """Estado del editor manual del crucigrama (C-09), SOLO para el creador.

    A diferencia de `PartidaPublicaResponse`, aquí la `posicion` es SIEMPRE
    visible: el creador está armando el layout y necesita ver dónde quedó
    cada palabra (null si todavía no la posicionó).
    """

    codigo: str
    tipo: str
    estado: str
    palabras: list[PalabraResponse]
    nombre: Optional[str] = None  # C-15: nombre opcional asignado por el creador

    model_config = ConfigDict(extra="forbid")


class CrearPartidaRequest(BaseModel):
    """Body del POST /partidas (C-16): `nombre` opcional con la misma
    semántica de "sin nombre = NULL" que C-15 (`ActualizarNombrePartidaRequest`).

    El validador trimea de espacios y un string vacío/whitespace se normaliza
    a `None`. `extra='forbid'` (regla dura 5): campo no declarado → 422.
    """

    tipo: str = Field(..., pattern="^(sopa|crucigrama)$")
    palabras: list[PalabraCreate] = Field(..., min_length=1)
    config: Optional[dict] = None
    nombre: Optional[str] = Field(None, max_length=50)  # C-16: opcional, patrón C-15

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
    nombre: Optional[str] = None  # C-16: nombre persistido en la creación (D11)


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
    """Igual a PalabraResponse pero SIN filtrar la posicion de palabras no encontradas.

    C-12 (D1): `palabra` es `Optional` según el rol del consultante en la vista
    pública de un crucigrama — el creador autenticado la recibe completa (el
    editor C-09 la necesita); cualquier no-creador la recibe `None` (anti-cheat).
    En sopa siempre viaja completa: la lista de palabras ES el juego. La
    `explicacion` (pista) es pública para todos los roles.
    """
    id: UUID
    palabra: Optional[str] = None
    texto_mostrar: Optional[str] = None
    explicacion: Optional[str]
    posicion: Optional[dict]  # Se fuerza a None si encontrada=False (ver route)
    encontrada: bool


class PartidaPublicaResponse(BaseModel):
    """
    Vista segura de una partida para exponer por GET /partidas/{codigo}.
    A diferencia de PartidaResponse, nunca revela `posicion` de una palabra
    todavía no encontrada (evita cheat leyendo el endpoint directamente).

    C-12 (D1 REVISADO): `es_creador` informa si el consultante autenticado es
    el creador de la partida (el front la usa para gatEAR la pantalla del
    editor). Es del schema, no del tipo: la sopa la setea igual. Sin cookie
    o con otra sesión, viaja `False`.
    """
    id: UUID
    codigo: str
    tipo: str
    estado: str
    palabras: list[PalabraPublicaResponse]
    config: Optional[dict]
    creado_en: datetime
    es_creador: bool = False
    nombre: Optional[str] = None  # C-15: nombre opcional asignado por el creador


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
    # C-10 (D2): en el GET /estado este texto va null (anti-cheat). En la
    # respuesta del POST /finalizar sigue siendo la solución visible.
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
    # C-10 (D2): la solución va null en crucigrama (anti-cheat); en sopa sigue
    # exponiéndose (el jugador necesita ver la palabra que busca).
    palabra: Optional[str] = None
    texto_mostrar: Optional[str] = None
    # C-10 (D3): numero de pista del crucigrama, visible SIEMPRE (el panel de
    # pistas lo necesita) aunque la palabra no esté encontrada. Null defensivo
    # si la posicion almacenada no trajo numero. En sopa queda None.
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
    """Body del PUT /palabras/{id}/respuesta de crucigrama (C-10, D1).

    `letras` = letras que el jugador tipeó en la palabra del tablero. Se
    normalizan server-side con `limpiar_para_grilla` antes de comparar.
    `extra='forbid'` (regla dura): campo no declarado → 422.
    """

    letras: str

    model_config = ConfigDict(extra="forbid")


class EncontradaResponse(BaseModel):
    encontrada: bool
    posicion: Optional[dict] = None
    # C-19 (D5): si la jugada corta el duelo (o encuentra la fila finalizada
    # por una carrera), viaja el resultado normalizado; si no, null (aditivo).
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
    nombre: Optional[str] = None  # C-15: nombre opcional asignado por el creador
    en_duelo: bool = False  # C-17 (D10): la partida tiene un duelo 1v1 activo


class ActualizarNombrePartidaRequest(BaseModel):
    """Body del PATCH /partidas/{codigo}/nombre (C-15).

    El nombre es opcional (null limpia). Se trimea de espacios y un string
    vacío se normaliza a null: no se persiste un string vacío (la semántica
    de "sin nombre" es siempre NULL). `extra='forbid'` (regla dura 5).
    """

    nombre: Optional[str] = Field(None, max_length=50)

    model_config = ConfigDict(extra="forbid")

    @field_validator("nombre")
    @classmethod
    def _normalizar_nombre(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        return v or None
