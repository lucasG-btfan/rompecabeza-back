"""
Rutas de emparejamientos 1v1 (C-17, D2-D8, D15).

Match-or-wait con `SELECT ... FOR UPDATE` + índice UNIQUE parcial como red
de seguridad y un único reintento ante carrera (409 final). Expiración lazy
sin cron (D4): `_reciclar_esperas_vencidas` se llama en cada punto de lectura
(POST, GET estado, GET lobby).

Helpers compartidos (D2), reutilizados por `routes/lobby.py` y
`routes/partidas/jugador.py` (auto-match en unirse):
- `_reciclar_esperas_vencidas`
- `_intentar_emparejar`
- `_emparejamiento_activo_de`
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import get_usuario_actual
from app.database import get_db
from app.models.emparejamiento import Emparejamiento, EmparejamientoEstado
from app.models.partida import Partida
from app.models.usuario import Usuario
from app.routes.partidas.deps import _get_partida_o_404
from app.schemas.emparejamiento import (
    EmparejamientoCreateRequest,
    EmparejamientoEstadoResponse,
)
from app.schemas.lobby import PartidaLobbyResponse

router = APIRouter(tags=["emparejamientos"])

# TTLs (D4/D16): no configurables por .env — un change futuro (PA-03) puede
# moverlos a settings si hace falta.
EMPAREJAMIENTO_TTL_ESPERA_SEG = 60  # rival no-show libera la partida
DUELO_INICIO_MAX_SEG = 600  # duelo emparejado sin arranque (D15), 10 min

ESTADOS_ACTIVOS = (
    EmparejamientoEstado.ESPERANDO.value,
    EmparejamientoEstado.EMPAREJADO.value,
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Helpers compartidos (D2)
# ---------------------------------------------------------------------------


def _reciclar_esperas_vencidas(
    db: Session, partida_id: Optional[uuid.UUID] = None
) -> None:
    """Expiración lazy (D4/D15): recicla a `expirado` (a) filas `esperando`
    con más de 60 s y (b) filas `emparejado` sin `iniciado_en` con más de
    10 min desde `emparejado_en`. Sin cron: los puntos de lectura la llaman."""
    ahora = _now_utc()

    q_esperas = db.query(Emparejamiento).filter(
        Emparejamiento.estado == EmparejamientoEstado.ESPERANDO.value
    )
    q_duelos = db.query(Emparejamiento).filter(
        Emparejamiento.estado == EmparejamientoEstado.EMPAREJADO.value,
        Emparejamiento.iniciado_en.is_(None),
    )
    if partida_id is not None:
        q_esperas = q_esperas.filter(Emparejamiento.partida_id == partida_id)
        q_duelos = q_duelos.filter(Emparejamiento.partida_id == partida_id)

    vencidas = (
        q_esperas.filter(
            Emparejamiento.creado_en
            < ahora - timedelta(seconds=EMPAREJAMIENTO_TTL_ESPERA_SEG)
        )
        .all()
    )
    sin_arranque = (
        q_duelos.filter(
            Emparejamiento.emparejado_en
            < ahora - timedelta(seconds=DUELO_INICIO_MAX_SEG)
        )
        .all()
    )

    a_expirar = vencidas + sin_arranque
    if a_expirar:
        for fila in a_expirar:
            fila.estado = EmparejamientoEstado.EXPIRADO.value
        db.commit()


def _fila_esperando_con_lock(db: Session, partida_id: uuid.UUID) -> Optional[Emparejamiento]:
    """Fila `esperando` de la partida con `SELECT ... FOR UPDATE` (D6): el
    lock serializa dos joineros simultáneos — el segundo espera y matchea."""
    return db.execute(
        select(Emparejamiento)
        .where(
            Emparejamiento.partida_id == partida_id,
            Emparejamiento.estado == EmparejamientoEstado.ESPERANDO.value,
        )
        .with_for_update()
    ).scalar_one_or_none()


def _matchear(db: Session, fila: Emparejamiento, usuario: Usuario) -> Emparejamiento:
    """Asigna al segundo jugador y pasa la fila a `emparejado`."""
    fila.jugador2_id = usuario.id
    fila.estado = EmparejamientoEstado.EMPAREJADO.value
    fila.emparejado_en = _now_utc()
    db.commit()
    db.refresh(fila)
    return fila


def _intentar_emparejar(
    db: Session, partida: Partida, usuario: Usuario, crear_espera: bool = True
) -> Optional[Emparejamiento]:
    """Match-or-wait (D6/D9): si hay una espera de otro → match; si el propio
    usuario ya espera → devuelve su fila (idempotente, evita self-match);
    si no hay → crea la espera. Carrera → rollback + un único reintento; si
    persiste → 409.

    `crear_espera=False` (auto-match de `unirse`, D9): jamás se crea una
    espera — si no hay fila devuelve None (juego solitario sin cambios C-14).
    """
    _reciclar_esperas_vencidas(db, partida_id=partida.id)

    try:
        fila = _fila_esperando_con_lock(db, partida.id)
        if fila is None:
            if not crear_espera:
                return None
            nueva = Emparejamiento(
                id=uuid.uuid4(),
                partida_id=partida.id,
                jugador1_id=usuario.id,
                estado=EmparejamientoEstado.ESPERANDO.value,
            )
            db.add(nueva)
            db.commit()
            db.refresh(nueva)
            return nueva
        # El propio usuario ya espera en esta partida (idempotencia/self-match).
        if fila.jugador1_id == usuario.id:
            return fila
        return _matchear(db, fila, usuario)
    except IntegrityError:
        # Carrera: otra fila activa se insertó entre el SELECT y el INSERT.
        db.rollback()
        _reciclar_esperas_vencidas(db, partida_id=partida.id)
        fila = _fila_esperando_con_lock(db, partida.id)
        if fila is not None and fila.jugador1_id != usuario.id:
            try:
                return _matchear(db, fila, usuario)
            except IntegrityError:
                db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Esta partida ya tiene un duelo en curso o el rival se fue",
        )


def _emparejamiento_activo_de(
    db: Session, usuario_id: uuid.UUID, partida_id: Optional[uuid.UUID] = None
) -> Optional[Emparejamiento]:
    """Fila activa (`esperando|emparejado`) más reciente del usuario, con
    filtro opcional por partida."""
    q = db.query(Emparejamiento).filter(
        or_(
            Emparejamiento.jugador1_id == usuario_id,
            Emparejamiento.jugador2_id == usuario_id,
        ),
        Emparejamiento.estado.in_(ESTADOS_ACTIVOS),
    )
    if partida_id is not None:
        q = q.filter(Emparejamiento.partida_id == partida_id)
    return q.order_by(Emparejamiento.creado_en.desc()).first()


def _ultima_fila_de(db: Session, usuario_id: uuid.UUID) -> Optional[Emparejamiento]:
    """Última fila del usuario (cualquier estado), por `creado_en` desc."""
    return (
        db.query(Emparejamiento)
        .filter(
            or_(
                Emparejamiento.jugador1_id == usuario_id,
                Emparejamiento.jugador2_id == usuario_id,
            )
        )
        .order_by(Emparejamiento.creado_en.desc())
        .first()
    )


def _partida_lobby(db: Session, partida: Partida) -> PartidaLobbyResponse:
    """Metadatos anti-cheat de una partida para el lobby / estado del duelo."""
    return PartidaLobbyResponse(
        codigo=partida.codigo,
        tipo=partida.tipo,
        cantidad_palabras=len(partida.palabras),
        nombre=partida.nombre,
        en_duelo=_partida_en_duelo(db, partida.id),
    )


def _partida_en_duelo(db: Session, partida_id: uuid.UUID) -> bool:
    """True si la partida tiene un emparejamiento activo (esperando|emparejado)."""
    return (
        db.query(Emparejamiento.id)
        .filter(
            Emparejamiento.partida_id == partida_id,
            Emparejamiento.estado.in_(ESTADOS_ACTIVOS),
        )
        .first()
        is not None
    )


def _rival_username(db: Session, fila: Emparejamiento, usuario: Usuario) -> Optional[str]:
    """Username del OTRO jugador del duelo (D7): el rival de jugador1 es
    jugador2 y viceversa."""
    if fila.estado != EmparejamientoEstado.EMPAREJADO.value or fila.jugador2_id is None:
        return None
    rival_id = fila.jugador2_id if fila.jugador1_id == usuario.id else fila.jugador1_id
    rival = db.query(Usuario).filter(Usuario.id == rival_id).first()
    return rival.username if rival else None


def _respuesta_estado(
    db: Session, fila: Emparejamiento, usuario: Usuario
) -> EmparejamientoEstadoResponse:
    """Arma la respuesta del POST / estado con la fila dada."""
    partida = db.query(Partida).filter(Partida.id == fila.partida_id).first()
    return EmparejamientoEstadoResponse(
        estado=fila.estado,
        partida=_partida_lobby(db, partida) if partida else None,
        rival=_rival_username(db, fila, usuario),
        creado_en=fila.creado_en,
        emparejado_en=fila.emparejado_en,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


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
    - 403: el solicitante es el creador (DD-07): no juega 1v1 sus partidas.
    - 201: espera creada o match realizado.
    - 200: ya estaba esperando en esta partida (idempotente).
    """
    partida = _get_partida_o_404(db, req.codigo_partida)

    if partida.estado != "activo":
        raise HTTPException(
            status_code=400,
            detail="La partida todavía no está activa",
        )
    if partida.creador_id == usuario.id:
        raise HTTPException(
            status_code=403,
            detail="No podés jugar 1v1 contra tu propia partida",
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

    - Fila activa (esperando|emparejado) → estado con partida (+ rival si match).
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