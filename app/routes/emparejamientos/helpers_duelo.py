"""
Cierre del duelo 1v1 (C-19 + AMEND feedback PO 2026-09-19) — conteo por jugador.

Este módulo es AUTOCONTENIDO a propósito (patrón D14): la lógica de cierre no
importa de `helpers.py` para que el paquete `routes/emparejamientos` no tenga
ciclos de import (`helpers._respuesta_estado` y los endpoints de `__init__.py`
necesitan estos helpers). Trae su propio `_now_utc`.

Contrato (spec `emparejamientos`, D3): el incremento exige sesión y duelo
activo `emparejado` con `iniciado_en` — invitados y solitario NUNCA
incrementan (C-14). La jugada que corta adjunta `duelo_finalizado` (D5).

AMEND (CAMBIO 1): la condición de victoria ya NO es "suma de contadores" —
gana el PRIMERO que completa `len(partida.palabras)` con SU contador propio
(`_finalizar_duelo` recibe el `ganador_id` explícito del que completó; el
abandono siempre pasa al rival; `None` = empate teórico).
AMEND (CAMBIO 4): `_finalizar_duelo` ya NO consume la partida (no toca
`partida.estado`): queda `activo`, vuelve al lobby y se re-juega.
C-22 (fix duelo fantasma): el branch de carrera del lock (`if fila is None`)
distingue una carrera REAL (el rival finalizó hace SEGUNDOS) de un duelo
VIEJO de una partida re-jugada (finalizado hace más de `VENTANA_CARRERA_LOCK_SEG`
= 60 s): el viejo NO se adjunta a jugadas en solitario (contrato C-14).
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.emparejamiento import Emparejamiento, EmparejamientoEstado
from app.models.partida import Partida
from app.models.usuario import Usuario
from app.schemas.emparejamiento import DueloResultadoResponse


# C-22 (D1): ventana de carrera del lock, en segundos. La carrera real es un
# evento de SEGUNDOS: el `SELECT ... FOR UPDATE` espera el commit de la jugada
# que cortó (desfase de ms), el poll del rival es cada 3 s y red/scheduler
# agregan segundos a lo sumo. 60 s es un margen de seguridad de 1 orden de
# magnitud sin alcanzar a un duelo terminado hace minutos: un hallazgo
# posterior al corte adjunta el resultado SOLO si la fila se finalizó dentro
# de esta ventana (carrera real legítima, contrato D5 del C-19); una fila
# `finalizado` más vieja es un duelo de una partida re-jugada y NO se adjunta
# (contrato C-14).
VENTANA_CARRERA_LOCK_SEG = 60


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _usuario_es_jugador_del_duelo(
    fila: Emparejamiento, usuario: Optional[Usuario]
) -> bool:
    """True si el usuario es uno de los dos jugadores del duelo (D3)."""
    return usuario is not None and (
        fila.jugador1_id == usuario.id or fila.jugador2_id == usuario.id
    )


def _resultado_duelo(
    db: Session, fila: Optional[Emparejamiento], usuario: Optional[Usuario]
) -> Optional[DueloResultadoResponse]:
    """Payload del resultado DEL DUELO normalizado por requester (D5):
    `yo`/`rival` dependen de quién consulta. Solo jugadores del duelo reciben
    resultado; `gane` es None en empate."""
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
    if fila.ganador_id is not None:
        gane = fila.ganador_id == usuario.id

    tiempo_total_seg = 0
    if fila.iniciado_en is not None and fila.finalizado_en is not None:
        tiempo_total_seg = max(
            0, int((fila.finalizado_en - fila.iniciado_en).total_seconds())
        )

    return DueloResultadoResponse(
        yo_palabras=yo_palabras,
        rival_palabras=rival_palabras,
        gane=gane,
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
    """Cierra el duelo: estado `finalizado`, `finalizado_en` y el `ganador_id`
    EXPLÍCITO (el que completó su contador o el rival en el abandono; `None`
    = empate teórico). NO consume la partida (CAMBIO 4): queda `activo` y
    vuelve al lobby re-jugable. Devuelve el resultado del duelo normalizado
    para `usuario` (D5)."""
    fila.estado = EmparejamientoEstado.FINALIZADO.value
    fila.finalizado_en = _now_utc()
    fila.ganador_id = ganador_id
    db.commit()
    db.refresh(fila)
    return _resultado_duelo(db, fila, usuario)


def _incrementar_hallazgo_duelo(
    db: Session, partida: Partida, usuario: Optional[Usuario]
) -> Optional[DueloResultadoResponse]:
    """Incremento atómico del conteo por jugador tras una jugada válida (D3).

    - Sin usuario (invitado) o sin duelo `emparejado` con `iniciado_en` →
      `None` (contrato C-14: solitario e invitado nunca incrementan).
    - `SELECT ... FOR UPDATE` sobre la fila del duelo: una jugada concurrente
      del otro jugador espera el lock (nunca dos cortes ni doble incremento).
    - Si la fila ya no está `emparejado` (carrera: la finalizó el otro
      proceso) → adjunta el resultado si el usuario es jugador del duelo.
    - Cuando el contador PROPIO del jugador llega a `len(partida.palabras)`
      → `_finalizar_duelo` con `ganador_id = usuario.id` (AMEND CAMBIO 1:
      gana el primero que completa EN SOLITARIO; ya no corta por SUMa) y
      devuelve el resultado adjunto a la respuesta de la jugada que corta.
    """
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
        # Carrera por lock o duelo viejo de una partida re-jugada (C-22).
        # Sin incremento; se distingue por RECENCIA de la fila finalizada más
        # reciente de la partida:
        # - `finalizado_en` dentro de `VENTANA_CARRERA_LOCK_SEG` → carrera
        #   REAL (el rival cortó bajo el lock, hace segundos): el hallazgo
        #   adjunta el resultado (spec "el hallazgo posterior al corte no
        #   cuenta y entera al instante" — D5 del C-19).
        # - fuera de ventana → duelo VIEJO de una partida re-jugada: NO se
        #   adjunta (contrato C-14: solitario y re-jugadas ni incrementan NI
        #   adjuntan). `None` también si no hay fila finalizada o le falta
        #   `finalizado_en` (dato pre-C-19 escrito a mano: sin evidencia de
        #   recencia → no es una carrera).
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