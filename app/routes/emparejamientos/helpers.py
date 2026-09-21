"""
Helpers compartidos del emparejamiento 1v1 (C-17, D2-D8, D15).

La lógica de C-17 (match-or-wait, TTLs, expiración lazy, estado del duelo)
vive acá; la lógica de CIERRE del duelo (C-19: conteo por jugador, corte,
resultado) vive en `helpers_duelo.py` — autocontenido para no crear ciclos
con este módulo (`_respuesta_estado` y los endpoints importan de ambos).

Reutilización externa: `routes/lobby.py`, `routes/partidas/crud.py` y
`routes/partidas/jugador.py` importan desde `routes.emparejamientos` (el
paquete re-exporta todo en `__init__.py`).
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.emparejamiento import Emparejamiento, EmparejamientoEstado
from app.models.partida import Partida
from app.models.usuario import Usuario
from app.schemas.emparejamiento import DueloResultadoResponse, EmparejamientoEstadoResponse
from app.schemas.lobby import PartidaLobbyResponse

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
# Expiración lazy (D4/D15)
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
    """Metadatos anti-cheat de una partida para el lobby / estado del duelo.

    C-23 (D2): `en_duelo` (duelo formado) y `en_espera` (espera de rival
    pendiente) se computan con la semántica nueva — el poll hereda ambos.
    """
    return PartidaLobbyResponse(
        codigo=partida.codigo,
        tipo=partida.tipo,
        cantidad_palabras=len(partida.palabras),
        nombre=partida.nombre,
        en_duelo=_partida_en_duelo(db, partida.id),
        en_espera=_partida_en_espera(db, partida.id),
    )


def _partida_en_duelo(db: Session, partida_id: uuid.UUID) -> bool:
    """True SOLO si el duelo está formado (`emparejado`).

    C-23 (D1): una espera de rival (`esperando`) NO es un duelo — se reporta
    con `en_espera`. Antes esto miraba `ESTADOS_ACTIVOS` y una espera ajena
    bloqueaba el 1v1 en la UI.
    """
    return (
        db.query(Emparejamiento.id)
        .filter(
            Emparejamiento.partida_id == partida_id,
            Emparejamiento.estado == EmparejamientoEstado.EMPAREJADO.value,
        )
        .first()
        is not None
    )


def _partida_en_espera(db: Session, partida_id: uuid.UUID) -> bool:
    """True SOLO si hay una espera de rival pendiente (`esperando`).

    Post-reciclaje lazy: las filas `esperando` vencidas ya viajaron a
    `expirado` antes de llegar acá (los puntos de lectura reciclan primero).
    """
    return (
        db.query(Emparejamiento.id)
        .filter(
            Emparejamiento.partida_id == partida_id,
            Emparejamiento.estado == EmparejamientoEstado.ESPERANDO.value,
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
    db: Session,
    fila: Emparejamiento,
    usuario: Usuario,
    resultado: Optional[DueloResultadoResponse] = None,
) -> EmparejamientoEstadoResponse:
    """Arma la respuesta del POST / estado con la fila dada.

    `resultado` viaja pre-calculado por el endpoint (rama `finalizado`, D5):
    helpers.py no importa `helpers_duelo` — así se evita el ciclo con
    `_respuesta_estado`. El poll finalizado es ESTABLE (D6): el endpoint jamás
    llama a esta función con una fila cancelada/expirada para consumirla.

    AMEND CAMBIO 2: cuando el duelo está `emparejado`, los contadores
    `yo_palabras`/`rival_palabras` se normalizan por requester (misma lógica
    que `_rival_username`); en cualquier otro estado van en `null` (el
    contador definitivo del `finalizado` viaja en `resultado`)."""
    partida = db.query(Partida).filter(Partida.id == fila.partida_id).first()

    yo_palabras = rival_palabras = None
    if fila.estado == EmparejamientoEstado.EMPAREJADO.value:
        if fila.jugador1_id == usuario.id:
            yo_palabras, rival_palabras = (
                fila.jugador1_palabras,
                fila.jugador2_palabras,
            )
        else:
            yo_palabras, rival_palabras = (
                fila.jugador2_palabras,
                fila.jugador1_palabras,
            )

    return EmparejamientoEstadoResponse(
        estado=fila.estado,
        partida=_partida_lobby(db, partida) if partida else None,
        rival=_rival_username(db, fila, usuario),
        creado_en=fila.creado_en,
        emparejado_en=fila.emparejado_en,
        yo_palabras=yo_palabras,
        rival_palabras=rival_palabras,
        resultado=resultado,
    )