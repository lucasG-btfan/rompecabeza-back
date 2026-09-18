"""
Tests del auto-match al unirse por código (C-17, D9/D15, spec `emparejamientos`).

POST /api/partidas/{codigo}/unirse — contrato C-14 ampliado:
- registrado (no creador) + fila `esperando` de otro → `{modo: "registrado",
  emparejado: true}` y la fila pasa a `emparejado`
- invitado → `{modo: "invitado", emparejado: false}` y la fila NO cambia
- creador → `emparejado: false` y la fila NO cambia (DD-07)
- sin espera → `emparejado: false` (solitario)
- self-match evitado: `jugador1` se une a su propia espera → `emparejado: false`
- primer `unirse` de cualquiera sobre duelo `emparejado` → setea `iniciado_en`

PostgreSQL real (regla dura 4): helpers estilo test_emparejamientos.py.
"""

import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import app
from app.models.emparejamiento import Emparejamiento
from app.models.partida import Partida


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client_nuevo(prefijo="c17u"):
    c = TestClient(app)
    username = f"{prefijo}_{uuid.uuid4().hex[:10]}"
    res = c.post(
        "/api/auth/registro",
        json={"username": username, "password": "clave123"},
    )
    assert res.status_code == 201, res.text
    return c, username


def _activar_partida(db_sesion, codigo):
    partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).first()
    assert partida is not None
    partida.estado = "activo"
    db_sesion.commit()


def _crear_y_activar(client, prefijo, db_sesion):
    """Registra creador, crea partida y la activa. Retorna código."""
    username = f"{prefijo}_{uuid.uuid4().hex[:10]}"
    res = client.post(
        "/api/auth/registro",
        json={"username": username, "password": "clave123"},
    )
    assert res.status_code == 201, res.text
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
    codigo = res.json()["codigo"]
    _activar_partida(db_sesion, codigo)
    return codigo


def _fila_de(db_sesion, codigo):
    partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).first()
    return (
        db_sesion.query(Emparejamiento)
        .filter(Emparejamiento.partida_id == partida.id)
        .order_by(Emparejamiento.creado_en.desc())
        .first()
    )


# ---------------------------------------------------------------------------
# Auto-match
# ---------------------------------------------------------------------------


def test_registrado_se_une_con_espera_de_otro_automatchea(client, db_sesion):
    """Registrado (no creador) se une a partida con espera de otro → emparejado."""
    c_creador, _ = _client_nuevo("am1cr")
    c_a, _ = _client_nuevo("am1a")
    c_b, _ = _client_nuevo("am1b")
    try:
        codigo = _crear_y_activar(c_creador, "am1cr", db_sesion)
        # A crea la espera
        c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})
        assert _fila_de(db_sesion, codigo).estado == "esperando"

        # B se une por código → auto-match
        res = c_b.post(f"/api/partidas/{codigo}/unirse")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["modo"] == "registrado"
        assert body["emparejado"] is True

        # La fila pasó a emparejado
        fila = _fila_de(db_sesion, codigo)
        assert fila.estado == "emparejado"
        assert fila.emparejado_en is not None
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()


def test_invitado_nunca_empareja(client, db_sesion):
    """Invitado se une con espera activa → emparejado: false, fila intacta."""
    c_creador, _ = _client_nuevo("am2cr")
    c_a, _ = _client_nuevo("am2a")
    try:
        codigo = _crear_y_activar(c_creador, "am2cr", db_sesion)
        c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})
        antes = _fila_de(db_sesion, codigo).estado

        # Invitado: client sin cookie
        res = client.post(f"/api/partidas/{codigo}/unirse")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["modo"] == "invitado"
        assert body["emparejado"] is False

        # Fila intacta y en DB sigue esperando
        fila = _fila_de(db_sesion, codigo)
        assert fila.estado == antes == "esperando"
        assert fila.jugador2_id is None
    finally:
        c_creador.close()
        c_a.close()


def test_creador_no_se_autoempareja(client, db_sesion):
    """Creador se une a su partida con espera → emparejado: false, fila intacta."""
    c_creador, _ = _client_nuevo("am3cr")
    c_a, _ = _client_nuevo("am3a")
    try:
        codigo = _crear_y_activar(c_creador, "am3cr", db_sesion)
        c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})

        # El creador se une a SU propia partida
        res = c_creador.post(f"/api/partidas/{codigo}/unirse")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["modo"] == "registrado"
        assert body["emparejado"] is False

        fila = _fila_de(db_sesion, codigo)
        assert fila.estado == "esperando"
        assert fila.jugador2_id is None
    finally:
        c_creador.close()
        c_a.close()


def test_sin_espera_emparejado_false(client, db_sesion):
    """Registrado se une a partida sin espera → emparejado: false (solitario)."""
    c_creador, _ = _client_nuevo("am4cr")
    c_b, _ = _client_nuevo("am4b")
    try:
        codigo = _crear_y_activar(c_creador, "am4cr", db_sesion)

        res = c_b.post(f"/api/partidas/{codigo}/unirse")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["modo"] == "registrado"
        assert body["emparejado"] is False

        # No se crea ninguna fila
        assert _fila_de(db_sesion, codigo) is None
    finally:
        c_creador.close()
        c_b.close()


def test_self_match_evitado(client, db_sesion):
    """El jugador1 de una espera se une a esa partida → emparejado: false
    (nunca se asigna como su propio jugador2)."""
    c_creador, _ = _client_nuevo("am5cr")
    c_a, _ = _client_nuevo("am5a")
    try:
        codigo = _crear_y_activar(c_creador, "am5cr", db_sesion)
        c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})

        # A (jugador1) se une por código a la misma partida
        res = c_a.post(f"/api/partidas/{codigo}/unirse")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["emparejado"] is False

        fila = _fila_de(db_sesion, codigo)
        assert fila.estado == "esperando"
        assert fila.jugador2_id is None
        assert fila.jugador1_id is not None
    finally:
        c_creador.close()
        c_a.close()


# ---------------------------------------------------------------------------
# D15: primer unirse sobre duelo emparejado setea iniciado_en
# ---------------------------------------------------------------------------


def test_primer_unirse_setea_iniciado_en(client, db_sesion):
    """Primer unirse de cualquiera de los dos sobre un duelo `emparejado`
    → setea `iniciado_en` (D15): el duelo queda vivo y no expira."""
    c_creador, _ = _client_nuevo("d15cr")
    c_a, _ = _client_nuevo("d15a")
    c_b, _ = _client_nuevo("d15b")
    try:
        codigo = _crear_y_activar(c_creador, "d15cr", db_sesion)
        c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})
        c_b.post("/api/emparejamientos", json={"codigo_partida": codigo})
        fila = _fila_de(db_sesion, codigo)
        assert fila.estado == "emparejado"
        assert fila.iniciado_en is None

        # A entra a jugar (unirse) → marca inicio del duelo
        res = c_a.post(f"/api/partidas/{codigo}/unirse")
        assert res.status_code == 200, res.text
        assert res.json()["emparejado"] is True  # informativo (ya estaba emparejado)

        db_sesion.refresh(fila)
        assert fila.iniciado_en is not None
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()


def test_unirse_duelo_iniciado_no_re_setea(client, db_sesion):
    """Unirse sobre un duelo ya iniciado NO cambia `iniciado_en`."""
    c_creador, _ = _client_nuevo("d15bcr")
    c_a, _ = _client_nuevo("d15ba")
    c_b, _ = _client_nuevo("d15bb")
    try:
        codigo = _crear_y_activar(c_creador, "d15bcr", db_sesion)
        c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})
        c_b.post("/api/emparejamientos", json={"codigo_partida": codigo})
        fila = _fila_de(db_sesion, codigo)
        fila.iniciado_en = datetime.now(timezone.utc)
        db_sesion.commit()
        iniciado_original = fila.iniciado_en

        c_b.post(f"/api/partidas/{codigo}/unirse")
        db_sesion.refresh(fila)
        assert fila.iniciado_en == iniciado_original
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()