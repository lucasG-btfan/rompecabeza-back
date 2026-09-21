"""
Contador visible de la partida dentro del duelo (AMEND feedback PO 2026-09-19,
CAMBIO 2 — spec `emparejamientos`).

`GET /api/emparejamientos/estado` expone, cuando el duelo está `emparejado`,
los contadores `yo_palabras`/`rival_palabras` NORMALIZADOS por requester para
pintar "[jugador1] n/m [jugador2] n/m" sin revelar quién es quién:

- J1 consulta → yo = jugador1_palabras, rival = jugador2_palabras
- J2 consulta → yo = jugador2_palabras, rival = jugador1_palabras
- la normalización por requester NO filtra la partida: el jugador ya ve la
  grilla completa; el anti-cheat vive en el lobby (nunca en el estado del
  duelo)
- `esperando` → contadores en `null` (todavía no hay duelo)
- `finalizado` → el contador DEFINITIVO viaja en `resultado` (D5); el
  top-level queda en `null` para no duplicar el dato

PostgreSQL real (regla dura 4): fixtures `client`/`db_sesion` de conftest.
"""

import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.models import Partida
from app.models.emparejamiento import Emparejamiento

PALABRAS = [
    {"palabra": "CASA", "explicacion": "Vivienda"},
    {"palabra": "SOL", "explicacion": "Astro"},
]


# ---------------------------------------------------------------------------
# Helpers (mismo patrón que test_duelo_finalizado_poll.py)
# ---------------------------------------------------------------------------


def _client_nuevo(prefijo="cd"):
    c = TestClient(app)
    username = f"{prefijo}_{uuid.uuid4().hex[:10]}"
    res = c.post(
        "/api/auth/registro",
        json={"username": username, "password": "clave123"},
    )
    assert res.status_code == 201, res.text
    return c, username


def _crear_y_publicar(client):
    res = client.post("/api/partidas", json={"tipo": "sopa", "palabras": PALABRAS})
    assert res.status_code == 201, res.text
    codigo = res.json()["codigo"]
    res = client.post(f"/api/partidas/{codigo}/finalizar")
    assert res.status_code == 200, res.text
    return codigo


def _match_y_unirse(c_creador, c_a, c_b, codigo):
    """Match J1-J2 y J2 entra a jugar (setea `iniciado_en`, D15)."""
    res = c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})
    assert res.status_code in (200, 201), res.text
    res = c_b.post("/api/emparejamientos", json={"codigo_partida": codigo})
    assert res.status_code in (200, 201), res.text
    assert res.json()["estado"] == "emparejado", res.text
    res = c_b.post(f"/api/partidas/{codigo}/unirse")
    assert res.status_code == 200, res.text
    assert res.json().get("emparejado") is True


def _fila_de(db_sesion, codigo):
    partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).one()
    return (
        db_sesion.query(Emparejamiento)
        .filter(Emparejamiento.partida_id == partida.id)
        .order_by(Emparejamiento.creado_en.desc())
        .first()
    )


def _estado(client):
    res = client.get("/api/emparejamientos/estado")
    assert res.status_code == 200, res.text
    return res.json()


# ---------------------------------------------------------------------------
# CAMBIO 2: contadores n/m en el estado del duelo
# ---------------------------------------------------------------------------


def test_estado_emparejado_expone_contadores_normalizados(client, db_sesion):
    """Duelo `emparejado`: el estado expone `yo_palabras`/`rival_palabras`
    normalizados por requester — J1 ve el propio contra el del rival."""
    c_creador, _ = _client_nuevo("cd1cr")
    c_a, _ = _client_nuevo("cd1a")
    c_b, _ = _client_nuevo("cd1b")
    try:
        codigo = _crear_y_publicar(c_creador)
        _match_y_unirse(c_creador, c_a, c_b, codigo)

        fila = _fila_de(db_sesion, codigo)
        fila.jugador1_palabras = 3  # A resolvió 3
        fila.jugador2_palabras = 5  # B resolvió 5
        db_sesion.commit()

        # J1 (A) consulta: yo = propio (3), rival = B (5)
        body = _estado(c_a)
        assert body["estado"] == "emparejado"
        assert body["yo_palabras"] == 3
        assert body["rival_palabras"] == 5

        # J2 (B) consulta: normalización invertida (yo = 5, rival = 3)
        body = _estado(c_b)
        assert body["estado"] == "emparejado"
        assert body["yo_palabras"] == 5
        assert body["rival_palabras"] == 3
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()


def test_estado_esperando_contadores_null(client, db_sesion):
    """Espera sin duelo aún → los contadores top-level van en `null` (el
    frontend los lee siempre; no hay duelo que contar todavía)."""
    c_creador, _ = _client_nuevo("cd2cr")
    c_a, _ = _client_nuevo("cd2a")
    try:
        codigo = _crear_y_publicar(c_creador)
        res = c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})
        assert res.status_code in (200, 201), res.text
        assert res.json()["estado"] == "esperando"

        body = _estado(c_a)
        assert body["estado"] == "esperando"
        assert body["yo_palabras"] is None
        assert body["rival_palabras"] is None
    finally:
        c_creador.close()
        c_a.close()


def test_estado_finalizado_contadores_solo_en_resultado(client, db_sesion):
    """Duelo `finalizado`: el contador DEFINITIVO viaja en `resultado` (D5);
    el top-level queda en `null` (no se duplica el dato del final)."""
    c_creador, _ = _client_nuevo("cd3cr")
    c_a, _ = _client_nuevo("cd3a")
    c_b, _ = _client_nuevo("cd3b")
    try:
        codigo = _crear_y_publicar(c_creador)
        _match_y_unirse(c_creador, c_a, c_b, codigo)
        res = c_b.post(
            "/api/emparejamientos/abandonar",
            json={"codigo_partida": codigo},
        )
        assert res.status_code == 200, res.text

        body = _estado(c_b)
        assert body["estado"] == "finalizado"
        assert body["yo_palabras"] is None
        assert body["rival_palabras"] is None
        assert body["resultado"]["yo_palabras"] == 0
        assert body["resultado"]["rival_palabras"] == 0
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()