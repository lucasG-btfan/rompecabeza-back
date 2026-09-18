"""
Rutas del lobby (C-17, D5) — listado público de partidas activas.

GET /api/lobby/partidas (anti-cheat): solo `codigo`, `tipo`,
`cantidad_palabras`, `nombre` y `en_duelo`. Accesible sin sesión; con sesión
excluye las partidas del usuario (las que creó + las que tienen su duelo
activo — DD-07 refinado). Las esperas vencidas se reciclan ANTES de calcular
`en_duelo` (D4: `_reciclar_esperas_vencidas` se importa de emparejamientos).
"""

from fastapi import APIRouter, Depends
from sqlalchemy import or_
from sqlalchemy.orm import Session
from typing import Optional

from app.auth import get_usuario_opcional
from app.database import get_db
from app.models.emparejamiento import Emparejamiento
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

    Exclusión si hay sesión (refinamiento DD-07): (a) partidas creadas por el
    usuario y (b) partidas donde el usuario tiene emparejamiento activo
    (`esperando|emparejado`) — el duelo es privado para sus protagonistas.
    Invitado (sin sesión): ve TODO lo activo.

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
            if partida.creador_id == usuario.id:
                return False
            return partida.id not in codigos_con_duelo_propio

        partidas = [p for p in partidas if _incluir(p)]

    # en_duelo: fila activa en la partida (esperando|emparejado), post-reciclaje.
    ids_con_duelo: set = set()
    if partidas:
        filas_activas = (
            db.query(Emparejamiento.partida_id)
            .filter(
                Emparejamiento.partida_id.in_([p.id for p in partidas]),
                Emparejamiento.estado.in_(ESTADOS_ACTIVOS),
            )
            .distinct()
            .all()
        )
        ids_con_duelo = {fila.partida_id for fila in filas_activas}

    return [
        PartidaLobbyResponse(
            codigo=partida.codigo,
            tipo=partida.tipo,
            cantidad_palabras=len(partida.palabras),
            nombre=partida.nombre,
            en_duelo=partida.id in ids_con_duelo,
        )
        for partida in partidas
    ]