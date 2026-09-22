import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.main import app
from app.models import Partida, Usuario
from app.models.emparejamiento import Emparejamiento


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client_nuevo(prefijo="c19m"):
    c = TestClient(app)
    username = f"{prefijo}_{uuid.uuid4().hex[:10]}"
    res = c.post(
        "/api/auth/registro",
        json={"username": username, "password": "clave123"},
    )
    assert res.status_code == 201, res.text
    return c, username


def _registrar_id(client, db_sesion, prefijo):
    """Registra un usuario nuevo y devuelve su UUID (via DB real)."""
    username = f"{prefijo}_{uuid.uuid4().hex[:10]}"
    res = client.post(
        "/api/auth/registro",
        json={"username": username, "password": "clave123"},
    )
    assert res.status_code == 201, res.text
    return db_sesion.query(Usuario).filter(Usuario.username == username).one().id


def _crear_partida_activa(client, db_sesion, prefijo):
    """El usuario de `client` crea una sopa CASA/SOL y la activa en DB."""
    res = client.post(
        "/api/partidas",
        json={
            "tipo": "sopa",
            "palabras": [
                {"palabra": "CASA", "explicacion": "Vivienda"},
                {"palabra": "SOL", "explicacion": "Astro"},
            ],
        },
    )
    assert res.status_code == 201, res.text
    partida = db_sesion.query(Partida).filter(Partida.codigo == res.json()["codigo"]).one()
    partida.estado = "activo"
    db_sesion.commit()
    return partida


def _nueva_fila(db_sesion, partida_id, jugador1_id, jugador2_id=None, estado="esperando"):
    fila = Emparejamiento(
        partida_id=partida_id,
        jugador1_id=jugador1_id,
        jugador2_id=jugador2_id,
        estado=estado,
    )
    db_sesion.add(fila)
    db_sesion.commit()
    db_sesion.refresh(fila)
    return fila


# ---------------------------------------------------------------------------
# Estado "finalizado" no es activo (índice parcial UNIQUE)
# ---------------------------------------------------------------------------


def test_fila_finalizada_convive_con_espera_nueva(client, db_sesion):
    """Una fila `finalizado` deja libre el índice parcial por partida: una
    fila `esperando` nueva de la MISMA partida se inserta sin error."""
    c_creador, _ = _client_nuevo("m1cr")
    try:
        partida = _crear_partida_activa(c_creador, db_sesion, "m1")
        j1 = _registrar_id(c_creador, db_sesion, "m1p1")
        j2 = _registrar_id(c_creador, db_sesion, "m1p2")

        _nueva_fila(db_sesion, partida.id, j1, j2, estado="finalizado")
        nueva = _nueva_fila(db_sesion, partida.id, j1, j2, estado="esperando")

        assert nueva.estado == "esperando"
    finally:
        c_creador.close()


# ---------------------------------------------------------------------------
# Conteo por jugador: default 0 y NOT NULL en base
# ---------------------------------------------------------------------------


def test_columnas_conteo_nacen_en_cero_y_not_null(client, db_sesion):
    """`jugador1_palabras`/`jugador2_palabras` nacen en 0; insertar con NULL
    explícito viola la constraint NOT NULL a nivel de base."""
    c_creador, _ = _client_nuevo("m2cr")
    try:
        partida = _crear_partida_activa(c_creador, db_sesion, "m2")
        j1 = _registrar_id(c_creador, db_sesion, "m2p1")
        j2 = _registrar_id(c_creador, db_sesion, "m2p2")

        fila = _nueva_fila(db_sesion, partida.id, j1, j2, estado="emparejado")
        assert fila.jugador1_palabras == 0
        assert fila.jugador2_palabras == 0

        with pytest.raises(IntegrityError):
            db_sesion.execute(
                text(
                    "INSERT INTO emparejamientos "
                    "(id, partida_id, jugador1_id, estado, "
                    "jugador1_palabras, jugador2_palabras) "
                    "VALUES (:id, :partida, :j1, 'emparejado', NULL, NULL)"
                ),
                {"id": uuid.uuid4(), "partida": partida.id, "j1": j1},
            )
            db_sesion.commit()
        db_sesion.rollback()
    finally:
        c_creador.close()


# ---------------------------------------------------------------------------
# Ganador y timestamp persisten en la fila finalizada
# ---------------------------------------------------------------------------


def test_ganador_y_finalizado_persisten(client, db_sesion):
    """Un duelo finalizado guarda `ganador_id` y `finalizado_en`; la fila
    queda consultable con el resultado del duelo."""
    c_creador, _ = _client_nuevo("m3cr")
    try:
        partida = _crear_partida_activa(c_creador, db_sesion, "m3")
        j1 = _registrar_id(c_creador, db_sesion, "m3p1")
        j2 = _registrar_id(c_creador, db_sesion, "m3p2")
        ahora = datetime.now(timezone.utc)

        fila = _nueva_fila(db_sesion, partida.id, j1, j2, estado="emparejado")
        fila.estado = "finalizado"
        fila.ganador_id = j2
        fila.finalizado_en = ahora
        db_sesion.commit()
        db_sesion.refresh(fila)

        assert fila.estado == "finalizado"
        assert fila.ganador_id == j2
        assert fila.finalizado_en is not None
        assert fila.partida_id == partida.id
    finally:
        c_creador.close()


# ---------------------------------------------------------------------------
# Migración idempotente
# ---------------------------------------------------------------------------


def test_migracion_es_idempotente(db_engine):
    """`_migrar_emparejamientos` corre dos veces sin error sobre la base ya
    creada (dev/prod: create_all no altera tablas existentes)."""
    from app.models.emparejamiento import _migrar_emparejamientos  # noqa: PLC0415

    _migrar_emparejamientos(db_engine)
    _migrar_emparejamientos(db_engine)