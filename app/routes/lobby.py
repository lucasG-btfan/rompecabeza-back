"""
Rutas del lobby (C-17, D5) — listado público de partidas activas.

GET /api/lobby/partidas (anti-cheat): solo `codigo`, `tipo`,
`cantidad_palabras`, `nombre`, `en_duelo` y `en_espera`. Accesible sin sesión;
con sesión excluye solo las partidas con su duelo activo propio (AMEND CAMBIO 3
— DD-07 reemplazado: el creador ya NO está excluido de su partida). Las esperas
vencidas se reciclan ANTES de calcular los flags (D4:
`_reciclar_esperas_vencidas` se importa de emparejamientos). C-23 (D1/D3):
`en_duelo` = solo duelo formado (`emparejado`); `en_espera` = solo espera de
rival pendiente (`esperando`).
"""

from fastapi import APIRouter, Depends
from sqlalchemy import or_
from sqlalchemy.orm import Session
from typing import Optional

from app.auth import get_usuario_opcional
from app.database import get_db
from app.models.emparejamiento import Emparejamiento, EmparejamientoEstado
from app.models.partida import Partida
from app.models.usuario import Usuario
from app.routes.emparejamientos import (
    ESTADOS_ACTIVOS,
    _reciclar_esperas_vencidas,
)
from app.schemas.lobby import PartidaLobbyResponse

router = APIRouter(tags=["lobby"])


@router.get("/lobby/partidas", response_model=list[PartidaLobbyResponse])
def listar_partidas_lobby(
    db: Session = Depends(get_db),
    usuario: Optional[Usuario] = Depends(get_usuario_opcional),
):
    """Partidas `activo` ordenadas por `creado_en` desc (más reciente primero).

    Exclusión si hay sesión (AMEND CAMBIO 3 — DD-07 reemplazado): solo
    partidas donde el usuario tiene un duelo propio activo
    (`esperando|emparejado`) — el duelo es privado para sus protagonistas.
    El creador YA ve su propia partida (puede jugar el 1v1). Invitado (sin
    sesión): ve TODO lo activo.

    Anti-cheat: solo metadatos. `cantidad_palabras` = `len(partida.palabras)`
    es el único dato derivado (no filtra la solución).
    """
    _reciclar_esperas_vencidas(db)

    partidas = (
        db.query(Partida)
        .filter(Partida.estado == "activo")
        .order_by(Partida.creado_en.desc())
        .all()
    )

    if usuario is not None:
        # Códigos de partida donde el usuario tiene un duelo activo.
        codigos_con_duelo_propio = {
            fila.partida_id
            for fila in db.query(Emparejamiento).filter(
                Emparejamiento.estado.in_(ESTADOS_ACTIVOS),
                or_(
                    Emparejamiento.jugador1_id == usuario.id,
                    Emparejamiento.jugador2_id == usuario.id,
                ),
            )
        }

        def _incluir(partida: Partida) -> bool:
            # DD-07 fue reemplazado por el AMEND c-19 (2026-09-19): el creador
            # SÍ ve y puede jugar 1v1 su propia partida. Solo se excluyen
            # partidas donde el usuario tiene un duelo propio VIVO.
            return partida.id not in codigos_con_duelo_propio

        partidas = [p for p in partidas if _incluir(p)]

    # C-23 (D3): una sola query en lote trae (partida_id, estado) y se
    # particiona en Python — `emparejado` → en_duelo, `esperando` → en_espera.
    # No se reutiliza `_partida_en_duelo` por ítem para no caer en N+1.
    ids_en_duelo: set = set()
    ids_en_espera: set = set()
    if partidas:
        filas_activas = (
            db.query(Emparejamiento.partida_id, Emparejamiento.estado)
            .filter(
                Emparejamiento.partida_id.in_([p.id for p in partidas]),
                Emparejamiento.estado.in_(ESTADOS_ACTIVOS),
            )
            .distinct()
            .all()
        )
        for partida_id, estado in filas_activas:
            if estado == EmparejamientoEstado.EMPAREJADO.value:
                ids_en_duelo.add(partida_id)
            elif estado == EmparejamientoEstado.ESPERANDO.value:
                ids_en_espera.add(partida_id)

    return [
        PartidaLobbyResponse(
            codigo=partida.codigo,
            tipo=partida.tipo,
            cantidad_palabras=len(partida.palabras),
            nombre=partida.nombre,
            en_duelo=partida.id in ids_en_duelo,
            en_espera=partida.id in ids_en_espera,
        )
        for partida in partidas
    ]