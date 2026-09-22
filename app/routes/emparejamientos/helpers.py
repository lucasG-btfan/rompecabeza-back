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

EMPAREJAMIENTO_TTL_ESPERA_SEG = 60  
DUELO_INICIO_MAX_SEG = 600  
TTL_DUELO_VIDA_SEG = 3600  

ESTADOS_ACTIVOS = (
    EmparejamientoEstado.ESPERANDO.value,
    EmparejamientoEstado.EMPAREJADO.value,
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _reciclar_esperas_vencidas(
    db: Session, partida_id: Optional[uuid.UUID] = None
) -> None:
    
    ahora = _now_utc()

    q_esperas = db.query(Emparejamiento).filter(
        Emparejamiento.estado == EmparejamientoEstado.ESPERANDO.value
    )
    q_duelos = db.query(Emparejamiento).filter(
        Emparejamiento.estado == EmparejamientoEstado.EMPAREJADO.value,
        Emparejamiento.iniciado_en.is_(None),
    )
    q_duelos_vida = db.query(Emparejamiento).filter(
        Emparejamiento.estado == EmparejamientoEstado.EMPAREJADO.value,
        Emparejamiento.iniciado_en.is_not(None),
    )
    if partida_id is not None:
        q_esperas = q_esperas.filter(Emparejamiento.partida_id == partida_id)
        q_duelos = q_duelos.filter(Emparejamiento.partida_id == partida_id)
        q_duelos_vida = q_duelos_vida.filter(Emparejamiento.partida_id == partida_id)

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
    vida_agotada = (
        q_duelos_vida.filter(
            Emparejamiento.iniciado_en
            < ahora - timedelta(seconds=TTL_DUELO_VIDA_SEG)
        )
        .all()
    )

    a_expirar = vencidas + sin_arranque + vida_agotada
    if a_expirar:
        for fila in a_expirar:
            fila.estado = EmparejamientoEstado.EXPIRADO.value
        db.commit()


def _fila_esperando_con_lock(db: Session, partida_id: uuid.UUID) -> Optional[Emparejamiento]:
    
    return db.execute(
        select(Emparejamiento)
        .where(
            Emparejamiento.partida_id == partida_id,
            Emparejamiento.estado == EmparejamientoEstado.ESPERANDO.value,
        )
        .with_for_update()
    ).scalar_one_or_none()


def _matchear(db: Session, fila: Emparejamiento, usuario: Usuario) -> Emparejamiento:
    
    fila.jugador2_id = usuario.id
    fila.estado = EmparejamientoEstado.EMPAREJADO.value
    fila.emparejado_en = _now_utc()
    db.commit()
    db.refresh(fila)
    return fila


def _intentar_emparejar(
    db: Session, partida: Partida, usuario: Usuario, crear_espera: bool = True
) -> Optional[Emparejamiento]:
    
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
    
    return PartidaLobbyResponse(
        codigo=partida.codigo,
        tipo=partida.tipo,
        cantidad_palabras=len(partida.palabras),
        nombre=partida.nombre,
        en_duelo=_partida_en_duelo(db, partida.id),
        en_espera=_partida_en_espera(db, partida.id),
    )


def _partida_en_duelo(db: Session, partida_id: uuid.UUID) -> bool:
    
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