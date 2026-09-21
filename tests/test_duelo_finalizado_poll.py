"""
Estado `finalizado` en el polling + lobby (C-19, D5/D6 — spec `emparejamientos`).

Requisito "Resultado del duelo en el polling":

- duelo finalizado → `GET /api/emparejamientos/estado` responde
  `{estado: "finalizado", resultado: DueloResultadoResponse}` normalizado por
  requester (el perdedor ve su contador como `yo_palabras` y `gane: false`).
- **estable (D6)**: a diferencia de `cancelado`/`expirado`, la rama
  `finalizado` NO consume la fila — la segunda consulta repite MISMA respuesta.
- el ganador consulta → `gane: true`.
- empate → `gane: null`.
- una fila `finalizado` seguida de una fila NUEVA del usuario (otra espera) →
  el poll reporta la fila más reciente (`esperando`), no el resultado viejo
  (`_ultima_fila_de` ordena por `creado_en` desc).
- abandono sin `iniciado_en` (borde D9/D14) → `tiempo_total_seg: 0`.
- (extra, same spec `lobby`) `partida.en_duelo` es False cuando el duelo
  terminó — la partida ya no está ocupada por un duelo activo.
- C-23 (D7): en estado `esperando` el objeto `partida` viaja con
  `en_duelo: false` + `en_espera: true` (una espera no es un duelo formado).

PostgreSQL real (regla dura 4): fixtures `client`/`db_sesion` de conftest.
"""

import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import app
from app.models import Partida
from app.models.emparejamiento import Emparejamiento

PALABRAS = [
    {"palabra": "CASA", "explicacion": "Vivienda"},
    {"palabra": "SOL", "explicacion": "Astro"},
]


# ---------------------------------------------------------------------------
# Helpers (mismo patrón que test_cierre_duelo_jugadas.py)
# ---------------------------------------------------------------------------


def _client_nuevo(prefijo="pl"):
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


def _abandonar(client, codigo):
    res = client.post(
        "/api/emparejamientos/abandonar",
        json={"codigo_partida": codigo},
    )
    assert res.status_code == 200, res.text
    return res.json()


# ---------------------------------------------------------------------------
# Poll con estado finalizado (D5/D6)
# ---------------------------------------------------------------------------


def test_poll_finalizado_para_perdedor_normaliza(client, db_sesion):
    """J2 (perdedor) consulta el estado: `estado: "finalizado"` con
    `resultado` normalizado — su contador como `yo_palabras` y `gane: false`."""
    c_creador, _ = _client_nuevo("p1cr")
    c_a, username_a = _client_nuevo("p1a")
    c_b, _ = _client_nuevo("p1b")
    try:
        codigo = _crear_y_publicar(c_creador)
        _match_y_unirse(c_creador, c_a, c_b, codigo)

        fila = _fila_de(db_sesion, codigo)
        fila.jugador1_palabras = 1  # J1 resolvió 1, J2 resolvió 3 y abandonó
        fila.jugador2_palabras = 3
        db_sesion.commit()
        _abandonar(c_b, codigo)

        body = _estado(c_b)
        assert body["estado"] == "finalizado"
        resultado = body["resultado"]
        assert resultado["yo_palabras"] == 3
        assert resultado["rival_palabras"] == 1
        assert resultado["gane"] is False
        assert resultado["rival"] == username_a
        assert body["partida"]["en_duelo"] is False  # la partida ya se liberó
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()


def test_poll_finalizado_estable_no_consumo(client, db_sesion):
    """D6: la rama `finalizado` es ESTABLE — la segunda consulta devuelve la
    MISMA respuesta (no se consume como `cancelado`/`expirado`)."""
    c_creador, _ = _client_nuevo("p2cr")
    c_a, _ = _client_nuevo("p2a")
    c_b, _ = _client_nuevo("p2b")
    try:
        codigo = _crear_y_publicar(c_creador)
        _match_y_unirse(c_creador, c_a, c_b, codigo)
        _abandonar(c_b, codigo)

        primera = _estado(c_b)
        segunda = _estado(c_b)
        assert primera == segunda
        assert segunda["estado"] == "finalizado"
        assert segunda["resultado"]["gane"] is False

        fila = _fila_de(db_sesion, codigo)
        assert fila.estado == "finalizado"
        assert fila.finalizado_en is not None  # no se tocó
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()


def test_poll_ganador_gane_true(client, db_sesion):
    """El GANADOR consulta el mismo duelo → normalización a favor propio."""
    c_creador, _ = _client_nuevo("p3cr")
    c_a, _ = _client_nuevo("p3a")
    c_b, username_b = _client_nuevo("p3b")
    try:
        codigo = _crear_y_publicar(c_creador)
        _match_y_unirse(c_creador, c_a, c_b, codigo)
        _abandonar(c_b, codigo)  # J2 abandona → gana J1

        body = _estado(c_a)
        assert body["estado"] == "finalizado"
        resultado = body["resultado"]
        assert resultado["gane"] is True
        assert resultado["rival"] == username_b
        assert resultado["yo_palabras"] == 0
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()


def test_poll_empate_gane_null(client, db_sesion):
    """Duelo finalizado en empate (`ganador_id` NULL, p. ej. corte por suma
    igual) → poll con `resultado.gane == null` para ambos."""
    c_creador, _ = _client_nuevo("p4cr")
    c_a, _ = _client_nuevo("p4a")
    c_b, _ = _client_nuevo("p4b")
    try:
        codigo = _crear_y_publicar(c_creador)
        _match_y_unirse(c_creador, c_a, c_b, codigo)

        # Setup: corte por suma con empate (same semántica que la sección 2).
        fila = _fila_de(db_sesion, codigo)
        fila.jugador1_palabras = 1
        fila.jugador2_palabras = 1
        fila.estado = "finalizado"
        fila.finalizado_en = datetime.now(timezone.utc)
        fila.ganador_id = None
        db_sesion.commit()

        body = _estado(c_a)
        assert body["estado"] == "finalizado"
        assert body["resultado"]["gane"] is None
        assert body["resultado"]["yo_palabras"] == 1
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()


def test_poll_tras_finalizado_reporta_fila_nueva(client, db_sesion):
    """Una fila `finalizado` NO tapa la siguiente: si el usuario crea una
    espera nueva, el poll (última fila por `creado_en`) reporta `esperando`."""
    c_creador, _ = _client_nuevo("p5cr")
    c_a, _ = _client_nuevo("p5a")
    c_b, _ = _client_nuevo("p5b")
    try:
        codigo1 = _crear_y_publicar(c_creador)
        _match_y_unirse(c_creador, c_a, c_b, codigo1)
        _abandonar(c_b, codigo1)

        # J2 crea una espera nueva en Otra partida (activa).
        codigo2 = _crear_y_publicar(c_creador)
        res = c_b.post("/api/emparejamientos", json={"codigo_partida": codigo2})
        assert res.status_code in (200, 201), res.text

        body = _estado(c_b)
        assert body["estado"] == "esperando"
        assert body["partida"]["codigo"] == codigo2
        assert body["resultado"] is None
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()


def test_poll_abandono_sin_iniciar_tiempo_cero(client, db_sesion):
    """Borde D9/D14: duelo `emparejado` sin `iniciado_en` (nadie entró) que un
    jugador abandona → `finalizado` con `tiempo_total_seg: 0` en el poll."""
    c_creador, _ = _client_nuevo("p6cr")
    c_a, _ = _client_nuevo("p6a")
    c_b, _ = _client_nuevo("p6b")
    try:
        codigo = _crear_y_publicar(c_creador)
        res = c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})
        assert res.status_code in (200, 201), res.text
        res = c_b.post("/api/emparejamientos", json={"codigo_partida": codigo})
        assert res.json()["estado"] == "emparejado"
        # Sin /unirse: iniciado_en se queda en None.

        _abandonar(c_b, codigo)

        body = _estado(c_b)
        assert body["estado"] == "finalizado"
        assert body["resultado"]["tiempo_total_seg"] == 0
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()


# ---------------------------------------------------------------------------
# C-23: flags del objeto `partida` en el poll (D7)
# ---------------------------------------------------------------------------


def test_poll_esperando_reporte_en_espera(client, db_sesion):
    """C-23 (3.4/D7): en estado `esperando` el objeto `partida` del poll viaja
    con `en_duelo: false` + `en_espera: true` — la espera PROPIA se reporta
    como espera, no como duelo formado."""
    c_creador, _ = _client_nuevo("p7cr")
    c_a, _ = _client_nuevo("p7a")
    try:
        codigo = _crear_y_publicar(c_creador)
        res = c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})
        assert res.status_code in (200, 201), res.text
        assert res.json()["estado"] == "esperando"

        body = _estado(c_a)
        assert body["estado"] == "esperando"
        assert body["partida"]["en_duelo"] is False
        assert body["partida"]["en_espera"] is True
    finally:
        c_creador.close()
        c_a.close()


def test_poll_emparejado_reporte_en_duelo(client, db_sesion):
    """C-23 (3.1/D7): en estado `emparejado` el objeto `partida` viaja con
    `en_duelo: true` + `en_espera: false`."""
    c_creador, _ = _client_nuevo("p8cr")
    c_a, _ = _client_nuevo("p8a")
    c_b, _ = _client_nuevo("p8b")
    try:
        codigo = _crear_y_publicar(c_creador)
        _match_y_unirse(c_creador, c_a, c_b, codigo)

        body = _estado(c_a)
        assert body["estado"] == "emparejado"
        assert body["partida"]["en_duelo"] is True
        assert body["partida"]["en_espera"] is False
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()