"""
Abandono del duelo 1v1 (C-19, D4 — spec `emparejamientos`).

Requisito "Abandono": un jugador puede abandonar un duelo EMPAREJADO y eso
cierra el duelo de inmediato (forfeit):

- `POST /api/emparejamientos/abandonar` con `{codigo_partida}` → 200 con
  `DueloResultadoResponse` normalizado para quien abandona (perdió; el rival
  gana forfait).
- la fila pasa a `finalizado`, `ganador_id = el rival`, `finalizado_en` se
  setea y la partida NO se consume (AMEND CAMBIO 4: queda `activo`, vuelve al
  lobby y se re-juega — misma semántica que el cierre por completar, D4/D7).
- `tiempo_total_seg` se calcula desde `iniciado_en` (D14: mismo reloj para
  ambos; acá forzamos iniciado 120 s atrás para validar la duración real).
- 400 sin duelo `emparejado` (incluye fila `esperando`: no hay duelo en curso);
- 404 partida inexistente; 401 sin sesión; 422 campos extra (regla dura 5);
- regresión C-17: `DELETE /emparejamientos` sobre un duelo `emparejado` sigue
  bloqueado con 400 (nadie puede borrar el duelo, solo abandonarlo).

PostgreSQL real (regla dura 4): fixtures `client`/`db_sesion` de conftest.
"""

import uuid
from datetime import datetime, timedelta, timezone

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


def _client_nuevo(prefijo="ab"):
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


def _duelo_emparejado(c_creador, c_a, c_b, codigo):
    res = c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})
    assert res.status_code in (200, 201), res.text
    res = c_b.post("/api/emparejamientos", json={"codigo_partida": codigo})
    assert res.status_code in (200, 201), res.text
    assert res.json()["estado"] == "emparejado", res.text
    # D15: el primer unirse marca el inicio del duelo.
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


def _abandonar(client, codigo):
    return client.post(
        "/api/emparejamientos/abandonar",
        json={"codigo_partida": codigo},
    )


# ---------------------------------------------------------------------------
# Forfeit (D4)
# ---------------------------------------------------------------------------


def test_j2_abandona_y_j1_gana_por_forfait(client, db_sesion):
    """J2 abandona el duelo emparejado → 200 con resultado normalizado para
    J2 (perdió), `ganador_id = J1` y fila `finalizado`. La partida NO se
    consume (AMEND CAMBIO 4): queda `activo` y re-jugable.
    `tiempo_total_seg` valida la duración real (iniciado 120 s atrás, D14)."""
    c_creador, _ = _client_nuevo("f1cr")
    c_a, username_a = _client_nuevo("f1a")
    c_b, _ = _client_nuevo("f1b")
    try:
        codigo = _crear_y_publicar(c_creador)
        _duelo_emparejado(c_creador, c_a, c_b, codigo)

        fila = _fila_de(db_sesion, codigo)
        fila.iniciado_en = datetime.now(timezone.utc) - timedelta(seconds=120)
        db_sesion.commit()

        res = _abandonar(c_b, codigo)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["yo_palabras"] == 0
        assert body["rival_palabras"] == 0
        assert body["gane"] is False
        assert body["rival"] == username_a
        assert body["tiempo_total_seg"] >= 120
        assert body["finalizado_en"] is not None

        db_sesion.refresh(fila)
        assert fila.estado == "finalizado"
        assert fila.ganador_id == fila.jugador1_id  # J1 gana por forfeit
        assert fila.finalizado_en is not None

        partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).one()
        assert partida.estado == "activo"  # CAMBIO 4: no se consume
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()


def test_j1_abandona_y_j2_gana_por_forfait(client, db_sesion):
    """Mismo contrato del lado del otro jugador: J1 abandona → gana J2."""
    c_creador, _ = _client_nuevo("f2cr")
    c_a, _ = _client_nuevo("f2a")
    c_b, username_b = _client_nuevo("f2b")
    try:
        codigo = _crear_y_publicar(c_creador)
        _duelo_emparejado(c_creador, c_a, c_b, codigo)

        res = _abandonar(c_a, codigo)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["gane"] is False
        assert body["rival"] == username_b  # quien abandona ve al rival como rival

        fila = _fila_de(db_sesion, codigo)
        assert fila.estado == "finalizado"
        assert fila.ganador_id == fila.jugador2_id  # J2 gana por forfeit
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()


def test_abandonar_sin_duelo_activo_400(client, db_sesion):
    """Registrado sin duelo en la partida → 400 y NO se crea ninguna fila."""
    c_creador, _ = _client_nuevo("f3cr")
    c_jugador, _ = _client_nuevo("f3j")
    try:
        codigo = _crear_y_publicar(c_creador)

        res = _abandonar(c_jugador, codigo)
        assert res.status_code == 400, res.text

        partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).one()
        assert db_sesion.query(Emparejamiento).filter(
            Emparejamiento.partida_id == partida.id
        ).count() == 0
    finally:
        c_creador.close()
        c_jugador.close()


def test_abandonar_mientras_espera_400(client, db_sesion):
    """Fila `esperando` NO es un duelo en curso: abandonar → 400 y la espera
    queda intacta (se sigue cancelando con DELETE, C-17)."""
    c_creador, _ = _client_nuevo("f4cr")
    c_a, _ = _client_nuevo("f4a")
    try:
        codigo = _crear_y_publicar(c_creador)
        res = c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})
        assert res.status_code in (200, 201), res.text
        assert res.json()["estado"] == "esperando"

        res = _abandonar(c_a, codigo)
        assert res.status_code == 400, res.text

        fila = _fila_de(db_sesion, codigo)
        assert fila.estado == "esperando"
    finally:
        c_creador.close()
        c_a.close()


# ---------------------------------------------------------------------------
# Bordes de contrato
# ---------------------------------------------------------------------------


def test_abandonar_partida_inexistente_404(client):
    c, _ = _client_nuevo("f5")
    try:
        res = _abandonar(c, "NOEXISTE")
        assert res.status_code == 404, res.text
    finally:
        c.close()


def test_abandonar_sin_sesion_401(client):
    res = _abandonar(client, "ALGUNA")
    assert res.status_code == 401, res.text


def test_abandonar_campos_extra_422(client):
    c_creador, _ = _client_nuevo("f6cr")
    c_jugador, _ = _client_nuevo("f6j")
    try:
        codigo = _crear_y_publicar(c_creador)
        res = c_jugador.post(
            "/api/emparejamientos/abandonar",
            json={"codigo_partida": codigo, "extra": 1},
        )
        assert res.status_code == 422, res.text
    finally:
        c_creador.close()
        c_jugador.close()


def test_delete_emparejado_sigue_bloqueado(client, db_sesion):
    """Regresión C-17: el DELETE sigue sin poder tocar un duelo `emparejado`
    (nadie puede cancelar el duelo; el abandono es el único escape)."""
    c_creador, _ = _client_nuevo("f7cr")
    c_a, _ = _client_nuevo("f7a")
    c_b, _ = _client_nuevo("f7b")
    try:
        codigo = _crear_y_publicar(c_creador)
        _duelo_emparejado(c_creador, c_a, c_b, codigo)

        res = c_a.delete("/api/emparejamientos")
        assert res.status_code == 400, res.text
        assert "comenzó" in res.text

        fila = _fila_de(db_sesion, codigo)
        assert fila.estado == "emparejado"
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()