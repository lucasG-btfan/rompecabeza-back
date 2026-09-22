from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.auth import get_usuario_actual
from app.database import get_db
from app.models.emparejamiento import Emparejamiento, EmparejamientoEstado
from app.models.usuario import Usuario
from app.routes.partidas.deps import _get_partida_o_404
from app.schemas.emparejamiento import (
    AbandonarRequest,
    DueloResultadoResponse,
    EmparejamientoCreateRequest,
    EmparejamientoEstadoResponse,
)

from .helpers import (
    ESTADOS_ACTIVOS,
    _emparejamiento_activo_de,
    _intentar_emparejar,
    _now_utc,
    _reciclar_esperas_vencidas,
    _respuesta_estado,
    _ultima_fila_de,
)
from .helpers_duelo import (
    _finalizar_duelo,
    _incrementar_hallazgo_duelo,
    _resultado_duelo,
)

# Re-exports públicos del paquete (contrato de imports externos).
__all__ = [
    "ESTADOS_ACTIVOS",
    "_reciclar_esperas_vencidas",
    "_intentar_emparejar",
    "_emparejamiento_activo_de",
    "_now_utc",
    "_incrementar_hallazgo_duelo",
    "_finalizar_duelo",
    "_resultado_duelo",
]

router = APIRouter(tags=["emparejamientos"])


@router.post("/emparejamientos", response_model=EmparejamientoEstadoResponse)
def crear_emparejamiento(
    req: EmparejamientoCreateRequest,
    response: Response,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    partida = _get_partida_o_404(db, req.codigo_partida)

    if partida.estado != "activo":
        raise HTTPException(
            status_code=400,
            detail="La partida todavía no está activa",
        )

    ya_esperando = _emparejamiento_activo_de(db, usuario.id, partida.id)
    if ya_esperando is not None and ya_esperando.jugador1_id == usuario.id:
        # Idempotencia: el usuario ya es jugador1 de la espera de esta partida.
        response.status_code = 200
        return _respuesta_estado(db, ya_esperando, usuario)

    fila = _intentar_emparejar(db, partida, usuario)
    response.status_code = 201
    return _respuesta_estado(db, fila, usuario)


@router.get("/emparejamientos/estado", response_model=EmparejamientoEstadoResponse)
def obtener_estado(
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    _reciclar_esperas_vencidas(db)

    fila = _ultima_fila_de(db, usuario.id)
    if fila is None:
        return EmparejamientoEstadoResponse(estado=None)

    if fila.estado in ESTADOS_ACTIVOS:
        return _respuesta_estado(db, fila, usuario)

    if fila.estado == EmparejamientoEstado.FINALIZADO.value:
        return _respuesta_estado(
            db,
            fila,
            usuario,
            resultado=_resultado_duelo(db, fila, usuario),
        )

    if (
        fila.estado == EmparejamientoEstado.EXPIRADO.value
        and fila.jugador2_id is not None
    ):
        return EmparejamientoEstadoResponse(estado=fila.estado)

    if fila.finalizado_en is not None:
        return EmparejamientoEstadoResponse(estado=None)

    fila.finalizado_en = _now_utc()  
    db.commit()
    return EmparejamientoEstadoResponse(estado=fila.estado)


@router.delete("/emparejamientos", status_code=204)
def cancelar_emparejamiento(
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
   
    fila = _emparejamiento_activo_de(db, usuario.id)
    if fila is None:
        return

    if fila.estado == EmparejamientoEstado.EMPAREJADO.value:
        raise HTTPException(status_code=400, detail="El duelo ya comenzó")

    if fila.jugador1_id == usuario.id:
        fila.estado = EmparejamientoEstado.CANCELADO.value
        db.commit()


@router.post("/emparejamientos/abandonar", response_model=DueloResultadoResponse)
def abandonar_duelo(
    req: AbandonarRequest,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
   
    partida = _get_partida_o_404(db, req.codigo_partida)

    fila = (
        db.query(Emparejamiento)
        .filter(
            Emparejamiento.partida_id == partida.id,
            Emparejamiento.estado == EmparejamientoEstado.EMPAREJADO.value,
            or_(
                Emparejamiento.jugador1_id == usuario.id,
                Emparejamiento.jugador2_id == usuario.id,
            ),
        )
        .order_by(Emparejamiento.creado_en.desc())
        .first()
    )
    if fila is None:
        raise HTTPException(
            status_code=400,
            detail="No hay un duelo en curso en esta partida",
        )

    rival_id = fila.jugador2_id if fila.jugador1_id == usuario.id else fila.jugador1_id
    return _finalizar_duelo(db, fila, usuario, ganador_id=rival_id)