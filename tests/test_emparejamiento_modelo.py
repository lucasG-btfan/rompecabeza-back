"""
Tests del modelo `Emparejamiento` (C-17, D1) — spec `emparejamientos`,
requisito "Modelo de emparejamientos con restricciones de integridad".

PostgreSQL real (regla dura 4): mismos fixtures y estilo de
`test_renombrar_partida.py` / `test_crear_partida_nombre.py`. Sin mocks.

Cubre:
- Defaults al crear una fila `esperando`.
- Índice UNIQUE parcial `uq_emparejamiento_partida_activo`: una sola fila
  activa (`esperando`|`emparejado`) por partida → IntegrityError.
- Índice UNIQUE parcial `uq_emparejamiento_jugador1_esperando`: un usuario
  espera a lo sumo en una partida → IntegrityError.
- Una fila `cancelado` convive con la fila activa de la misma partida.
- Un usuario puede estar `esperando` en una partida y `emparejado` en otra
  a la vez (los índices no se pisan).
"""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.emparejamiento import Emparejamiento, EmparejamientoEstado
from app.models.partida import Partida
from app.models.usuario import Usuario


def _usuario(db_sesion, prefijo="c17m"):
    """Crea un usuario directo en la DB real y devuelve su id."""
    usuario = Usuario(
        id=uuid.uuid4(),
        username=f"{prefijo}_{uuid.uuid4().hex[:10]}",
        password_hash="hash",
    )
    db_sesion.add(usuario)
    db_sesion.flush()
    return usuario


def _partida(db_sesion, prefijo="P17"):
    """Crea una partida `activo` directa en la DB real y devuelve su id."""
    partida = Partida(
        id=uuid.uuid4(),
        codigo=f"{prefijo}_{uuid.uuid4().hex[:4]}".upper(),
        tipo="sopa",
        estado="activo",
    )
    db_sesion.add(partida)
    db_sesion.flush()
    return partida


def _fila(db_sesion, partida_id, jugador1_id, jugador2_id=None, estado=None):
    """Crea una fila `esperando` (o el estado pedido) sin commitear."""
    fila = Emparejamiento(
        id=uuid.uuid4(),
        partida_id=partida_id,
        jugador1_id=jugador1_id,
        jugador2_id=jugador2_id,
        estado=estado or EmparejamientoEstado.ESPERANDO.value,
    )
    db_sesion.add(fila)
    return fila


# ---------------------------------------------------------------------------
# Defaults al crear una fila esperando
# ---------------------------------------------------------------------------


def test_fila_esperando_defaults(db_sesion):
    """Crear `esperando` → estado correcto, `creado_en` seteado, `jugador2` None."""
    jugador1 = _usuario(db_sesion)
    partida = _partida(db_sesion)

    fila = _fila(db_sesion, partida.id, jugador1.id)
    db_sesion.commit()
    db_sesion.refresh(fila)

    assert fila.estado == EmparejamientoEstado.ESPERANDO.value
    assert fila.creado_en is not None
    assert fila.jugador2_id is None
    assert fila.emparejado_en is None
    assert fila.iniciado_en is None
    assert fila.ganador_id is None


# ---------------------------------------------------------------------------
# Índice UNIQUE parcial uq_emparejamiento_partida_activo
# ---------------------------------------------------------------------------


def test_segunda_fila_activa_misma_partida_integrity_error(db_sesion):
    """Dos filas activas para la MISMA partida → IntegrityError (UNIQUE parcial)."""
    jugador1 = _usuario(db_sesion)
    jugador2 = _usuario(db_sesion)
    partida = _partida(db_sesion)

    _fila(db_sesion, partida.id, jugador1.id)
    db_sesion.flush()

    with pytest.raises(IntegrityError):
        _fila(
            db_sesion,
            partida.id,
            jugador2.id,
            estado=EmparejamientoEstado.EMPAREJADO.value,
        )
        db_sesion.flush()
    db_sesion.rollback()


# ---------------------------------------------------------------------------
# Índice UNIQUE parcial uq_emparejamiento_jugador1_esperando
# ---------------------------------------------------------------------------


def test_segunda_espera_mismo_jugador1_integrity_error(db_sesion):
    """El mismo jugador1 esperando en OTRA partida → IntegrityError (UNIQUE parcial)."""
    jugador1 = _usuario(db_sesion)
    partida_a = _partida(db_sesion)
    partida_b = _partida(db_sesion)

    _fila(db_sesion, partida_a.id, jugador1.id)
    db_sesion.flush()

    with pytest.raises(IntegrityError):
        _fila(db_sesion, partida_b.id, jugador1.id)
        db_sesion.flush()
    db_sesion.rollback()


# ---------------------------------------------------------------------------
# Filas no activas conviven con la activa
# ---------------------------------------------------------------------------


def test_fila_cancelada_convive_con_esperando(db_sesion):
    """`esperando` + `cancelado` de la misma partida → conviven (el índice
    parcial solo cubre `esperando`/`emparejado`)."""
    jugador1 = _usuario(db_sesion)
    jugador2 = _usuario(db_sesion)
    partida = _partida(db_sesion)

    _fila(db_sesion, partida.id, jugador1.id)
    _fila(
        db_sesion,
        partida.id,
        jugador2.id,
        estado=EmparejamientoEstado.CANCELADO.value,
    )
    db_sesion.commit()  # no debe lanzar IntegrityError


# ---------------------------------------------------------------------------
# Los índices no se pisan entre partidas distintas
# ---------------------------------------------------------------------------


def test_esperando_y_emparejado_en_partidas_distintas(db_sesion):
    """Un usuario espera en una partida y está emparejado en otra: los dos
    índices parciales cubren dominios distintos y conviven sin error."""
    jugador1 = _usuario(db_sesion)
    jugador2 = _usuario(db_sesion)
    partida_a = _partida(db_sesion)
    partida_b = _partida(db_sesion)

    fila_a = _fila(db_sesion, partida_a.id, jugador1.id)
    fila_b = _fila(
        db_sesion,
        partida_b.id,
        jugador1.id,
        jugador2_id=jugador2.id,
        estado=EmparejamientoEstado.EMPAREJADO.value,
    )
    db_sesion.commit()

    assert fila_a.estado == EmparejamientoEstado.ESPERANDO.value
    assert fila_b.estado == EmparejamientoEstado.EMPAREJADO.value