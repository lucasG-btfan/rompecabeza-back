import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.emparejamiento import Emparejamiento, EmparejamientoEstado
from app.models.partida import Partida
from app.models.usuario import Usuario
from app.schemas.emparejamiento import DueloResultadoResponse


VENTANA_CARRERA_LOCK_SEG = 60


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _usuario_es_jugador_del_duelo(
    fila: Emparejamiento, usuario: Optional[Usuario]
) -> bool:
    """True si el usuario es uno de los dos jugadores del duelo ."""
    return usuario is not None and (
        fila.jugador1_id == usuario.id or fila.jugador2_id == usuario.id
    )


def _resultado_duelo(
    db: Session, fila: Optional[Emparejamiento], usuario: Optional[Usuario]
) -> Optional[DueloResultadoResponse]:
    if fila is None or not _usuario_es_jugador_del_duelo(fila, usuario):
        return None

    if fila.jugador1_id == usuario.id:
        yo_palabras, rival_palabras = fila.jugador1_palabras, fila.jugador2_palabras
        rival_id = fila.jugador2_id
    else:
        yo_palabras, rival_palabras = fila.jugador2_palabras, fila.jugador1_palabras
        rival_id = fila.jugador1_id

    rival = db.query(Usuario).filter(Usuario.id == rival_id).first()

    gane = None
    motivo = "empate"
    if fila.ganador_id is not None:
        gane = fila.ganador_id == usuario.id
        partida = db.get(Partida, fila.partida_id)
        contador_del_ganador = (
            fila.jugador1_palabras
            if fila.jugador1_id == fila.ganador_id
            else fila.jugador2_palabras
        )
        motivo = (
            "corte" if contador_del_ganador == len(partida.palabras) else "abandono"
        )

    tiempo_total_seg = 0
    if fila.iniciado_en is not None and fila.finalizado_en is not None:
        tiempo_total_seg = max(
            0, int((fila.finalizado_en - fila.iniciado_en).total_seconds())
        )

    return DueloResultadoResponse(
        yo_palabras=yo_palabras,
        rival_palabras=rival_palabras,
        gane=gane,
        motivo=motivo,
        rival=rival.username if rival else None,
        tiempo_total_seg=tiempo_total_seg,
        finalizado_en=fila.finalizado_en,
    )


def _finalizar_duelo(
    db: Session,
    fila: Emparejamiento,
    usuario: Optional[Usuario],
    ganador_id: Optional[uuid.UUID] = None,
) -> DueloResultadoResponse:
    
    fila.estado = EmparejamientoEstado.FINALIZADO.value
    fila.finalizado_en = _now_utc()
    fila.ganador_id = ganador_id
    db.commit()
    db.refresh(fila)
    return _resultado_duelo(db, fila, usuario)


def _incrementar_hallazgo_duelo(
    db: Session, partida: Partida, usuario: Optional[Usuario]
) -> Optional[DueloResultadoResponse]:
    
    if usuario is None:
        return None

    fila = db.execute(
        select(Emparejamiento)
        .where(
            Emparejamiento.partida_id == partida.id,
            Emparejamiento.estado == EmparejamientoEstado.EMPAREJADO.value,
        )
        .with_for_update()
    ).scalar_one_or_none()

    if fila is None:
        
        finalizada = (
            db.query(Emparejamiento)
            .filter(
                Emparejamiento.partida_id == partida.id,
                Emparejamiento.estado == EmparejamientoEstado.FINALIZADO.value,
            )
            .order_by(Emparejamiento.finalizado_en.desc())
            .first()
        )
        if finalizada is None or finalizada.finalizado_en is None:
            return None
        if finalizada.finalizado_en < _now_utc() - timedelta(
            seconds=VENTANA_CARRERA_LOCK_SEG
        ):
            return None
        return _resultado_duelo(db, finalizada, usuario)

    if fila.iniciado_en is None or not _usuario_es_jugador_del_duelo(fila, usuario):
        return None

    if fila.jugador1_id == usuario.id:
        fila.jugador1_palabras += 1
        completo = fila.jugador1_palabras >= len(partida.palabras)
    else:
        fila.jugador2_palabras += 1
        completo = fila.jugador2_palabras >= len(partida.palabras)

    if completo:
        return _finalizar_duelo(db, fila, usuario, ganador_id=usuario.id)

    db.commit()
    return None