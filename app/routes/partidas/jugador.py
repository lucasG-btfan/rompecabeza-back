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
    partida = _get_partida_o_404(db, codigo)

    return _estado_response(db, partida)


@router.post("/partidas/{codigo}/unirse", response_model=UnirseResponse)
def unirse_partida(
    codigo: str,
    db: Session = Depends(get_db),
    usuario: Optional[Usuario] = Depends(get_usuario_opcional),
):
    
    partida = _get_partida_o_404(db, codigo)

    if partida.estado != "activo":
        raise HTTPException(
            status_code=400,
            detail="La partida todavía no está activa",
        )

    if usuario is None:
        return UnirseResponse(modo="invitado")

    fila_propia = _emparejamiento_activo_de(db, usuario.id, partida.id)
    if (
        fila_propia is not None
        and fila_propia.estado == EmparejamientoEstado.EMPAREJADO.value
    ):
        if fila_propia.iniciado_en is None:
            fila_propia.iniciado_en = _now_utc()
            db.commit()
        return UnirseResponse(modo="registrado", emparejado=True)

    fila = _intentar_emparejar(db, partida, usuario, crear_espera=False)
    if fila is None:
        return UnirseResponse(modo="registrado")
    if fila.estado == EmparejamientoEstado.EMPAREJADO.value:
        if fila.iniciado_en is None:
            fila.iniciado_en = _now_utc()
            db.commit()
        return UnirseResponse(modo="registrado", emparejado=True)
    return UnirseResponse(modo="registrado")


@router.put("/partidas/{codigo}/palabras/{palabra_id}/encontrada", response_model=EncontradaResponse)
def marcar_encontrada(
    codigo: str,
    palabra_id: uuid.UUID,
    req: EncontradaRequest,
    db: Session = Depends(get_db),
    usuario: Optional[Usuario] = Depends(get_usuario_opcional),
):
    
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

    duelo = _incrementar_hallazgo_duelo(db, partida, usuario)
    return EncontradaResponse(
        encontrada=True,
        posicion=palabra.posicion,
        duelo_finalizado=duelo,
    )