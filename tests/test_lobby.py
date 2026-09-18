"""
Tests del lobby (C-17, spec `lobby`, D5).

GET /api/lobby/partidas — listado público anti-cheat:
- solo partidas `activo`, orden `creado_en` desc
- payload exacto por ítem (codigo/tipo/cantidad_palabras/nombre/en_duelo), SIN
  grilla/palabras/posicion/explicacion
- exclusión (autenticado): partidas propias + partidas con duelo activo propio
- invitado ve todo
- en_duelo true con fila activa, false sin fila; espera vencida → false
- lista vacía → []

PostgreSQL real (regla dura 4): helpers estilo test_emparejamientos.py.
"""

import uuid
from datetime import datetime, timezone, timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.models.emparejamiento import Emparejamiento
from app.models.partida import Partida


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CAMPOS_LOBBY = {"codigo", "tipo", "cantidad_palabras", "nombre", "en_duelo"}


def _registrar(client, prefijo="c17l"):
    username = f"{prefijo}_{uuid.uuid4().hex[:10]}"
    res = client.post(
        "/api/auth/registro",
        json={"username": username, "password": "clave123"},
    )
    assert res.status_code == 201, res.text
    return username


def _client_nuevo(prefijo="c17ln"):
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


def _crear_y_activar(client, prefijo, db_sesion, tipo="sopa", la_palabra="CASA"):
    """Registra creador, crea partida y la activa. Retorna código."""
    _registrar(client, prefijo)
    res = client.post(
        "/api/partidas",
        json={
            "tipo": tipo,
            "palabras": [
                {"palabra": la_palabra, "explicacion": f"Pista de {la_palabra}"},
                {"palabra": "SOL", "explicacion": "Astro"},
            ],
        },
    )
    assert res.status_code == 201, res.text
    codigo = res.json()["codigo"]
    _activar_partida(db_sesion, codigo)
    return codigo


def _emparejamiento_row(db_sesion, codigo):
    partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).first()
    return (
        db_sesion.query(Emparejamiento)
        .filter(Emparejamiento.partida_id == partida.id)
        .order_by(Emparejamiento.creado_en.desc())
        .first()
    )


# ---------------------------------------------------------------------------
# Listado básico
# ---------------------------------------------------------------------------


class TestListadoLobby:
    def test_200_sin_sesion_solo_activas(self, client, db_sesion):
        """Invitado ve solo partidas `activo`, orden creado_en desc."""
        c_creador1, _ = _client_nuevo("l1c1")
        c_creador2, _ = _client_nuevo("l1c2")
        try:
            codigo1 = _crear_y_activar(c_creador1, "l1c1", db_sesion)
            codigo2 = _crear_y_activar(c_creador2, "l1c2", db_sesion, la_palabra="LUNA")
            # Partida NO activa (queda 'creando') — no debe aparecer
            _registrar(c_creador1, "l1cr")
            c_creador1.post(
                "/api/partidas",
                json={
                    "tipo": "sopa",
                    "palabras": [{"palabra": "BOLSA", "explicacion": "x"}],
                },
            )

            # Invitado (sin cookie): ve las 2 activas
            res = client.get("/api/lobby/partidas")
            assert res.status_code == 200
            body = res.json()
            codigos = [p["codigo"] for p in body]
            assert codigo1 in codigos
            assert codigo2 in codigos
            assert len(body) == 2
            # Orden creado_en desc → codigo2 (la última creada) primero
            assert body[0]["codigo"] == codigo2
        finally:
            c_creador1.close()
            c_creador2.close()

    def test_payload_exacto_anti_cheat(self, client, db_sesion):
        """Cada ítem tiene SOLO codigo/tipo/cantidad_palabras/nombre/en_duelo."""
        c_creador, _ = _client_nuevo("l2cr")
        try:
            codigo = _crear_y_activar(c_creador, "l2cr", db_sesion)
            res = client.get("/api/lobby/partidas")
            assert res.status_code == 200
            item = next(p for p in res.json() if p["codigo"] == codigo)
            assert set(item.keys()) == CAMPOS_LOBBY
            assert item["tipo"] == "sopa"
            assert item["cantidad_palabras"] == 2
            assert item["nombre"] is None
            assert item["en_duelo"] is False
        finally:
            c_creador.close()

    def test_payload_con_nombre(self, client, db_sesion):
        """Partida con nombre → el lobby lo refleja."""
        c_creador, _ = _client_nuevo("l3cr")
        try:
            _registrar(c_creador, "l3cr")
            res = c_creador.post(
                "/api/partidas",
                json={
                    "tipo": "sopa",
                    "palabras": [{"palabra": "CASA", "explicacion": "x"}],
                    "nombre": "Tema Navidad",
                },
            )
            assert res.status_code == 201, res.text
            codigo = res.json()["codigo"]
            _activar_partida(db_sesion, codigo)

            res = client.get("/api/lobby/partidas")
            item = next(p for p in res.json() if p["codigo"] == codigo)
            assert item["nombre"] == "Tema Navidad"
        finally:
            c_creador.close()

    def test_lista_vacia(self, client):
        """Sin partidas activas → []."""
        res = client.get("/api/lobby/partidas")
        assert res.status_code == 200
        assert res.json() == []


# ---------------------------------------------------------------------------
# Exclusión de partidas propias (autenticado) — DD-07
# ---------------------------------------------------------------------------


class TestExclusion:
    def test_creador_no_ve_su_partida(self, client, db_sesion):
        """El creador autenticado NO ve su partida en el lobby."""
        c_creador, _ = _client_nuevo("ex1cr")
        c_ajeno, _ = _client_nuevo("ex1aj")
        try:
            codigo_propia = _crear_y_activar(c_creador, "ex1cr", db_sesion)
            codigo_ajena = _crear_y_activar(c_ajeno, "ex1aj", db_sesion, la_palabra="LUNA")

            # El creador ve solo la ajena
            res = c_creador.get("/api/lobby/partidas")
            codigos = [p["codigo"] for p in res.json()]
            assert codigo_propia not in codigos
            assert codigo_ajena in codigos
        finally:
            c_creador.close()
            c_ajeno.close()

    def test_invitado_ve_partida_del_creador(self, client, db_sesion):
        """Invitado (sin cookie) SÍ ve la partida del creador (lobby público)."""
        c_creador, _ = _client_nuevo("ex2cr")
        try:
            codigo = _crear_y_activar(c_creador, "ex2cr", db_sesion)
            res = client.get("/api/lobby/partidas")
            codigos = [p["codigo"] for p in res.json()]
            assert codigo in codigos
        finally:
            c_creador.close()

    def test_duelo_activo_no_se_re_lista_para_sus_protagonistas(self, client, db_sesion):
        """Usuario con duelo activo (esperando) en una partida NO la ve."""
        c_creador, _ = _client_nuevo("ex3cr")
        c_a, _ = _client_nuevo("ex3a")
        c_ajeno, _ = _client_nuevo("ex3aj")
        try:
            codigo = _crear_y_activar(c_creador, "ex3cr", db_sesion)
            # A crea espera en la partida de otro
            c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})

            # A (con espera activa) no la ve en el lobby
            res = c_a.get("/api/lobby/partidas")
            codigos = [p["codigo"] for p in res.json()]
            assert codigo not in codigos

            # Un tercero SIN duelo la ve
            res = c_ajeno.get("/api/lobby/partidas")
            codigos = [p["codigo"] for p in res.json()]
            assert codigo in codigos
        finally:
            c_creador.close()
            c_a.close()
            c_ajeno.close()


# ---------------------------------------------------------------------------
# Flag en_duelo
# ---------------------------------------------------------------------------


class TestEnDuelo:
    def test_en_duelo_true_con_fila_activa(self, client, db_sesion):
        """Partida con espera activa → en_duelo: true."""
        c_creador, _ = _client_nuevo("ed1cr")
        c_a, _ = _client_nuevo("ed1a")
        c_ajeno, _ = _client_nuevo("ed1aj")
        try:
            codigo = _crear_y_activar(c_creador, "ed1cr", db_sesion)
            c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})

            res = c_ajeno.get("/api/lobby/partidas")
            item = next(p for p in res.json() if p["codigo"] == codigo)
            assert item["en_duelo"] is True
        finally:
            c_creador.close()
            c_a.close()
            c_ajeno.close()

    def test_en_duelo_false_sin_fila(self, client, db_sesion):
        """Partida sin emparejamiento activo → en_duelo: false."""
        c_creador, _ = _client_nuevo("ed2cr")
        try:
            codigo = _crear_y_activar(c_creador, "ed2cr", db_sesion)
            res = client.get("/api/lobby/partidas")
            item = next(p for p in res.json() if p["codigo"] == codigo)
            assert item["en_duelo"] is False
        finally:
            c_creador.close()

    def test_espera_vencida_libera_en_duelo(self, client, db_sesion):
        """Espera vencida (+60s) → se recicla y la partida viaja en_duelo: false."""
        c_creador, _ = _client_nuevo("ed3cr")
        c_a, _ = _client_nuevo("ed3a")
        c_ajeno, _ = _client_nuevo("ed3aj")
        try:
            codigo = _crear_y_activar(c_creador, "ed3cr", db_sesion)
            c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})

            # Forzar expiración de la espera
            fila = _emparejamiento_row(db_sesion, codigo)
            fila.creado_en = datetime.now(timezone.utc) - timedelta(seconds=61)
            db_sesion.commit()

            res = c_ajeno.get("/api/lobby/partidas")
            item = next(p for p in res.json() if p["codigo"] == codigo)
            assert item["en_duelo"] is False
        finally:
            c_creador.close()
            c_a.close()
            c_ajeno.close()