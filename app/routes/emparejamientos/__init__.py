"""
Rutas de emparejamientos 1v1 (C-17 D2-D8/D15 + C-19 D2-D7).

Paquete (refactor C-11, regla dura 8): la lógica match-or-wait y de estado
vive en `helpers.py`; la lógica de CIERRE del duelo (conteo, corte, resultado)
en `helpers_duelo.py` (autocontenida para evitar ciclos). Este `__init__.py`
expone el router + los endpoints y RE-EXPORTA todo lo que consumen otros
módulos (`routes/lobby.py`, `routes/partidas/crud.py`,
`routes/partidas/jugador.py` y `main.py`).
"""

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
    """Match-or-wait (D6): crea la espera o matchea a un rival que ya espera.

    - 404: la partida no existe (patrón `_get_partida_o_404`).
    - 400: la partida no está `activo`.
    - AMEND CAMBIO 3 (reemplaza DD-07): el CREADOR ya puede crear espera en
      su propia partida y jugar el 1v1 (la exclusión solo cubre su duelo
      activo propio en el lobby; el self-match se sigue evitando en
      `_intentar_emparejar`).
    - 201: espera creada o match realizado.
    - 200: ya estaba esperando en esta partida (idempotente).
    """
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
    """Polling del estado del emparejamiento (D7).

    - Fila activa (esperando|emparejado) → estado con partida (+ rival y
      contadores n/m si match — AMEND CAMBIO 2, normalizados por requester).
    - Última transición no activa (cancelado|expirado) sin reportar →
      se devuelve UNA vez (marca `finalizado_en` como consumida).
    - Sin fila (o transición ya consumida) → `estado: null`.
    """
    _reciclar_esperas_vencidas(db)

    fila = _ultima_fila_de(db, usuario.id)
    if fila is None:
        return EmparejamientoEstadoResponse(estado=None)

    if fila.estado in ESTADOS_ACTIVOS:
        return _respuesta_estado(db, fila, usuario)

    # ESTABLE (D6): `finalizado` jamás se consume — repetir el poll devuelve
    # el resultado una y otra vez hasta que el jugador inicie una fila nueva.
    if fila.estado == EmparejamientoEstado.FINALIZADO.value:
        return _respuesta_estado(
            db,
            fila,
            usuario,
            resultado=_resultado_duelo(db, fila, usuario),
        )

    # Transición cancelado/expirado: se reporta una sola vez.
    if fila.finalizado_en is not None:
        return EmparejamientoEstadoResponse(estado=None)

    fila.finalizado_en = _now_utc()  # marca "consumida" sin borrar historial
    db.commit()
    return EmparejamientoEstadoResponse(estado=fila.estado)


@router.delete("/emparejamientos", status_code=204)
def cancelar_emparejamiento(
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    """Cancela la espera del usuario (D8): solo su propia fila `esperando`.

    - `esperando` → `cancelado`, 204 (el poll reporta `cancelado` una vez).
    - `emparejado` → 400 "El duelo ya comenzó" (nadie puede cancelar el duelo).
    - Sin fila activa → 204 idempotente (y un jugador2 nunca está `esperando`).
    """
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
    """Abandono (forfeit) del duelo 1v1 (C-19, D4 — spec `emparejamientos`).

    Cierra el duelo de inmediato vía `_finalizar_duelo` con el rival como
    `ganador_id` explícito: el que abandona pierde por forfait. La partida NO
    se consume (AMEND CAMBIO 4): queda `activo` y vuelve al lobby re-jugable.
    La respuesta es el `DueloResultadoResponse` normalizado para quien
    abandona (véase `_resultado_duelo`, D5).

    - 404: la partida no existe (`_get_partida_o_404`).
    - 400: no hay duelo `emparejado` del usuario en esa partida (una fila
      `esperando` no es un duelo en curso — se cancela con DELETE).
    - Solo quien participa del duelo puede abandonarlo (la query filtra por
      `jugador1_id`/`jugador2_id` del usuario).
    """
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