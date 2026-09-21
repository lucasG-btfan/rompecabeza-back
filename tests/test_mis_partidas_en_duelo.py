"""
Tests de los flags `en_duelo`/`en_espera` en "Mis partidas" (C-17 D10 + C-23 D1).

GET /api/partidas — `ResumenPartidaResponse` expone `en_duelo: bool` y
`en_espera: bool` (aditivo):
- C-23 (D1): `en_duelo` true SOLO con duelo formado (`emparejado`);
  `en_espera` true SOLO con espera de rival pendiente (`esperando`)
- `false` sin fila activa (campos presentes en TODOS los ítems — aditivos)

PostgreSQL real (regla dura 4): helpers estilo test_emparejamientos.py.
"""

import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.models.partida import Partida


def _client_nuevo(prefijo="c17m"):
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


def test_mis_partidas_espera_en_espera_true(client, db_sesion):
    """C-23 (4.1, redefine la semántica vieja): creador con partida que tiene
    una espera de rival activa → `en_espera: true` y `en_duelo: false`."""
    c_creador, _ = _client_nuevo("md1cr")
    c_a, _ = _client_nuevo("md1a")
    try:
        codigo = _crear_y_activar(c_creador, "md1cr", db_sesion)
        # A crea espera sobre la partida del creador
        c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})

        res = c_creador.get("/api/partidas")
        assert res.status_code == 200, res.text
        items = res.json()
        assert len(items) >= 1
        item = next(p for p in items if p["codigo"] == codigo)
        assert item["en_duelo"] is False
        assert item["en_espera"] is True
    finally:
        c_creador.close()
        c_a.close()


def test_mis_partidas_duelo_formado_en_duelo_true(client, db_sesion):
    """C-23 (3.1): creador con partida en duelo FORMADO (`emparejado`) →
    `en_duelo: true` y `en_espera: false` (contrato previo intacto)."""
    c_creador, _ = _client_nuevo("md4cr")
    c_a, _ = _client_nuevo("md4a")
    c_b, _ = _client_nuevo("md4b")
    try:
        codigo = _crear_y_activar(c_creador, "md4cr", db_sesion)
        # A espera, B matchea → duelo formado
        res = c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})
        assert res.status_code in (200, 201), res.text
        res = c_b.post("/api/emparejamientos", json={"codigo_partida": codigo})
        assert res.status_code in (200, 201), res.text
        assert res.json()["estado"] == "emparejado", res.text

        res = c_creador.get("/api/partidas")
        assert res.status_code == 200, res.text
        item = next(p for p in res.json() if p["codigo"] == codigo)
        assert item["en_duelo"] is True
        assert item["en_espera"] is False
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()


def test_mis_partidas_sin_duelo_false(client, db_sesion):
    """Creador sin emparejamiento → en_duelo: false y en_espera: false."""
    c_creador, _ = _client_nuevo("md2cr")
    try:
        codigo = _crear_y_activar(c_creador, "md2cr", db_sesion)

        res = c_creador.get("/api/partidas")
        assert res.status_code == 200, res.text
        items = res.json()
        assert len(items) >= 1
        # Los campos deben estar en TODOS los ítems (no solo en el esperado)
        for item in items:
            assert "en_duelo" in item, f"Falta en_duelo en {item['codigo']}"
            assert "en_espera" in item, f"Falta en_espera en {item['codigo']}"
            assert item["en_duelo"] is False
            assert item["en_espera"] is False
    finally:
        c_creador.close()


def test_mis_partidas_duelo_expirado_false(client, db_sesion):
    """Espera vencida (+60s) reciclada → en_duelo: false en el resumen."""
    from datetime import datetime, timezone, timedelta

    from app.models.emparejamiento import Emparejamiento

    c_creador, _ = _client_nuevo("md3cr")
    c_a, _ = _client_nuevo("md3a")
    try:
        codigo = _crear_y_activar(c_creador, "md3cr", db_sesion)
        c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})

        # Forzar expiración de la espera
        partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).first()
        fila = (
            db_sesion.query(Emparejamiento)
            .filter(Emparejamiento.partida_id == partida.id)
            .first()
        )
        fila.creado_en = datetime.now(timezone.utc) - timedelta(seconds=61)
        db_sesion.commit()

        # El lobby recicla; "Mis partidas" computa en_duelo con solo activas
        res = c_creador.get("/api/partidas")
        assert res.status_code == 200, res.text
        item = next(p for p in res.json() if p["codigo"] == codigo)
        assert item["en_duelo"] is False
    finally:
        c_creador.close()
        c_a.close()