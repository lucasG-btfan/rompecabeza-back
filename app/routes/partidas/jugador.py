from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import uuid

from app.database import get_db
from app.auth import get_usuario_opcional
from app.models.partida import Partida
from app.models.palabra import Palabra
from app.models.usuario import Usuario
from app.models.emparejamiento import EmparejamientoEstado
from app.schemas.partida import (
    EstadoPartidaResponse,
    EncontradaRequest,
    EncontradaResponse,
    RespuestaRequest,
)
from app.schemas.usuario import UnirseResponse
from app.services.sopa_generator import calcular_celda_final
from app.services.texto import limpiar_para_grilla
from app.routes.partidas.deps import (
    _get_partida_o_404,
    _get_palabra_o_404,
    _estado_response,
)
from app.routes.emparejamientos import (
    _emparejamiento_activo_de,
    _incrementar_hallazgo_duelo,
    _intentar_emparejar,
    _now_utc,
)

router = APIRouter(tags=["partidas"])


@router.get("/partidas/{codigo}/estado", response_model=EstadoPartidaResponse)
def obtener_estado(
    codigo: str,
    db: Session = Depends(get_db),
):
    """Devuelve la grilla actual y el estado de cada palabra SIN progreso por
    jugador (C-14): el progreso de una partida es EFÍMERO y vive en la sesión
    del frontend. Todas las palabras salen `encontrada=False` / `posicion=None`
    y la grilla del crucigrama viaja siempre ciega (ninguna letra revelada)."""
    partida = _get_partida_o_404(db, codigo)

    return _estado_response(db, partida)


@router.post("/partidas/{codigo}/unirse", response_model=UnirseResponse)
def unirse_partida(
    codigo: str,
    db: Session = Depends(get_db),
    usuario: Optional[Usuario] = Depends(get_usuario_opcional),
):
    """
    Handshake informativo al entrar a jugar (C-14): devuelve el modo de la
    sesión (registrado/invitado) sin crear participación ni fijar `iniciado_en`.
    El cronómetro es 100% del frontend: arranca con `Date.now()` al montar.
    """
    partida = _get_partida_o_404(db, codigo)

    # Solo se puede "unirse" a una partida que ya fue publicada (estado
    # 'activo'). Si la partida sigue en 'creando' no hay sopa generada ni
    # nada que jugar.
    if partida.estado != "activo":
        raise HTTPException(
            status_code=400,
            detail="La partida todavía no está activa",
        )

    if usuario is None:
        return UnirseResponse(modo="invitado")

    # C-17 (D15): si el usuario YA es parte de un duelo `emparejado` en esta
    # partida, el primer `unirse` de cualquiera de los dos marca `iniciado_en`
    # (a partir de ahí el duelo queda vivo y ya no expira por D15).
    fila_propia = _emparejamiento_activo_de(db, usuario.id, partida.id)
    if (
        fila_propia is not None
        and fila_propia.estado == EmparejamientoEstado.EMPAREJADO.value
    ):
        if fila_propia.iniciado_en is None:
            fila_propia.iniciado_en = _now_utc()
            db.commit()
        return UnirseResponse(modo="registrado", emparejado=True)

    # Auto-match (D9): match-only sobre la espera de OTRO jugador; jamás se
    # crea una espera acá (el solitario no cambia el contrato C-14).
    fila = _intentar_emparejar(db, partida, usuario, crear_espera=False)
    if fila is None:
        return UnirseResponse(modo="registrado")
    if fila.estado == EmparejamientoEstado.EMPAREJADO.value:
        # Primer unirse tras el match → marca el inicio del duelo (D15).
        if fila.iniciado_en is None:
            fila.iniciado_en = _now_utc()
            db.commit()
        return UnirseResponse(modo="registrado", emparejado=True)
    # La fila sigue `esperando`: es nuestra (self-match evitado).
    return UnirseResponse(modo="registrado")


@router.put("/partidas/{codigo}/palabras/{palabra_id}/encontrada", response_model=EncontradaResponse)
def marcar_encontrada(
    codigo: str,
    palabra_id: uuid.UUID,
    req: EncontradaRequest,
    db: Session = Depends(get_db),
    usuario: Optional[Usuario] = Depends(get_usuario_opcional),
):
    """
    Valida la selección del jugador (celda inicial y final) contra la posición
    real de la palabra y, si coincide (en cualquiera de los dos sentidos),
    responde 200 con la posición SIN persistir nada (C-14): el progreso es
    efímero y vive en la sesión del frontend, para registrado o invitado.

    C-19 (D3): post-validación, si el usuario está en un duelo `emparejado`
    con `iniciado_en`, incrementa su contador — y si la SUMA llega al total
    de palabras, corta el duelo y adjunta `duelo_finalizado` (D5).
    """
    partida = _get_partida_o_404(db, codigo)
    palabra = _get_palabra_o_404(db, partida, palabra_id)

    if partida.estado != "activo":
        raise HTTPException(status_code=400, detail="La partida todavía no está activa")
    if not palabra.posicion:
        raise HTTPException(status_code=400, detail="La palabra no tiene posición asignada")

    fila_real = palabra.posicion["fila"]
    col_real = palabra.posicion["columna"]
    orientacion = palabra.posicion["orientacion"]
    fila_fin_real, col_fin_real = calcular_celda_final(
        fila_real, col_real, orientacion, len(palabra.palabra)
    )

    seleccion_directa = (
        req.fila_inicio == fila_real
        and req.columna_inicio == col_real
        and req.fila_fin == fila_fin_real
        and req.columna_fin == col_fin_real
    )
    seleccion_invertida = (
        req.fila_inicio == fila_fin_real
        and req.columna_inicio == col_fin_real
        and req.fila_fin == fila_real
        and req.columna_fin == col_real
    )

    if not (seleccion_directa or seleccion_invertida):
        raise HTTPException(status_code=400, detail="Selección incorrecta")

    duelo = _incrementar_hallazgo_duelo(db, partida, usuario)
    return EncontradaResponse(
        encontrada=True,
        posicion=palabra.posicion,
        duelo_finalizado=duelo,
    )


@router.put(
    "/partidas/{codigo}/palabras/{palabra_id}/respuesta",
    response_model=EncontradaResponse,
)
def responder_palabra(
    codigo: str,
    palabra_id: uuid.UUID,
    req: RespuestaRequest,
    db: Session = Depends(get_db),
    usuario: Optional[Usuario] = Depends(get_usuario_opcional),
):
    """
    Validación de respuesta por palabra para CRUCIGRAMAS (C-10, D1):

    - Solo partidas de tipo 'crucigrama' (la sopa sigue usando la selección
      de celdas del endpoint /encontrada).
    - Solo en estado 'activo'. La palabra debe estar posicionada.
    - Las letras ingresadas se normalizan con `limpiar_para_grilla`
      (mayúsculas, sin acentos/espacios/símbolos) y se comparan contra la
      palabra real del crucigrama: si NO coinciden -> 400 (jugada válida,
      letras incorrectas). El front limpia SOLO esa palabra y deja reintentar.
    - Si coinciden: responde 200 con la posición SIN persistir nada (C-14):
      el progreso es efímero, para registrado o invitado.
    """
    partida = _get_partida_o_404(db, codigo)
    palabra = _get_palabra_o_404(db, partida, palabra_id)

    if partida.tipo != "crucigrama":
        raise HTTPException(
            status_code=400,
            detail=(
                "El ingreso de letras es solo para crucigramas: usá el endpoint "
                "de palabra encontrada (/encontrada) para sopa"
            ),
        )
    if partida.estado != "activo":
        raise HTTPException(status_code=400, detail="La partida todavía no está activa")
    if not palabra.posicion:
        raise HTTPException(status_code=400, detail="La palabra no tiene posición asignada")

    # Normalización del input del jugador (misma regla que el editor: solo
    # importan las letras reales, en la grilla no hay espacios ni símbolos).
    letras_limpias = limpiar_para_grilla(req.letras)
    if letras_limpias != palabra.palabra:
        raise HTTPException(
            status_code=400,
            detail="Las letras no coinciden con la palabra del crucigrama",
        )

    # C-19 (D3/D5): misma rama aditiva que `marcar_encontrada`.
    duelo = _incrementar_hallazgo_duelo(db, partida, usuario)
    return EncontradaResponse(
        encontrada=True,
        posicion=palabra.posicion,
        duelo_finalizado=duelo,
    )