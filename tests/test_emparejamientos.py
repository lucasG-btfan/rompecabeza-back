"""
Tests de los endpoints de emparejamientos (C-17, D2-D8, D15).

Cubre los 3 endpoints (POST match-or-wait, GET estado, DELETE cancelar),
lazy expiry de espera (60 s), duelo emparejado sin arranque (D15, 10 min),
idempotencia, carrera, anti-cheat y consumición de cancelado/expirado.

PostgreSQL real (regla dura 4): helpers estilo test_crear_partida_nombre.py.
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

def _registrar(client, prefijo="c17e"):
    """Registra un usuario nuevo (cookie de sesión) y devuelve su username."""
    username = f"{prefijo}_{uuid.uuid4().hex[:10]}"
    res = client.post(
        "/api/auth/registro",
        json={"username": username, "password": "clave123"},
    )
    assert res.status_code == 201, res.text
    return username


def _client_nuevo(prefijo="c17n"):
    """Crea un TestClient y registra un usuario nuevo. Caller DEBE cerrar."""
    c = TestClient(app)
    username = f"{prefijo}_{uuid.uuid4().hex[:10]}"
    res = c.post(
        "/api/auth/registro",
        json={"username": username, "password": "clave123"},
    )
    assert res.status_code == 201, res.text
    return c, username


def _activar_partida(db_sesion, codigo):
    """Pone una partida en estado 'activo' directo en la DB."""
    partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).first()
    assert partida is not None, f"Partida {codigo} no encontrada en DB"
    partida.estado = "activo"
    db_sesion.commit()


def _crear_partida(client, prefijo):
    """Crea una partida sopa con 2 palabras y retorna su código."""
    _registrar(client, prefijo)
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
    return res.json()["codigo"]


def _crear_y_activar(client, prefijo, db_sesion):
    """Crea partida sopa y la activa. Retorna código."""
    codigo = _crear_partida(client, prefijo)
    _activar_partida(db_sesion, codigo)
    return codigo


def _emparejamiento_row(db_sesion, codigo):
    """Devuelve la fila Emparejamiento más reciente para la partida."""
    partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).first()
    return (
        db_sesion.query(Emparejamiento)
        .filter(Emparejamiento.partida_id == partida.id)
        .order_by(Emparejamiento.creado_en.desc())
        .first()
    )


# ===========================================================================
# POST /api/emparejamientos
# ===========================================================================


class TestPostEmparejamiento:
    def test_401_sin_sesion(self, client):
        res = client.post("/api/emparejamientos", json={"codigo_partida": "X"})
        assert res.status_code == 401

    def test_404_codigo_inexistente(self, client):
        _registrar(client, "ep1")
        res = client.post("/api/emparejamientos", json={"codigo_partida": "ZZZZZZ"})
        assert res.status_code == 404

    def test_400_partida_no_activa(self, client, db_sesion):
        """400 si la partida está en estado 'creando'."""
        c_creador, _ = _client_nuevo("ep2cr")
        c_req, _ = _client_nuevo("ep2r")
        try:
            codigo = _crear_partida(c_creador, "ep2cr")
            # NO se activa: queda en "creando"
            res = c_req.post(
                "/api/emparejamientos", json={"codigo_partida": codigo}
            )
            assert res.status_code == 400
            assert "activa" in res.json()["detail"].lower()
        finally:
            c_creador.close()
            c_req.close()

    def test_creador_puede_crear_espera_en_su_partida(self, client, db_sesion):
        """AMEND CAMBIO 3 (reemplaza DD-07): el creador SÍ puede participar
        del 1v1 en su propia partida — crea su espera normalmente (201)."""
        c_creador, _ = _client_nuevo("ep3cr")
        try:
            codigo = _crear_y_activar(c_creador, "ep3cr", db_sesion)
            res = c_creador.post(
                "/api/emparejamientos", json={"codigo_partida": codigo}
            )
            assert res.status_code == 201
            assert res.json()["estado"] == "esperando"
        finally:
            c_creador.close()

    def test_creador_y_otro_forman_duelo(self, client, db_sesion):
        """AMEND CAMBIO 3 end-to-end: la espera del creador es matcheable por
        otro jugador → duelo `emparejado` con el creador como jugador."""
        c_creador, _ = _client_nuevo("ep3bcr")
        c_b, _ = _client_nuevo("ep3bb")
        try:
            codigo = _crear_y_activar(c_creador, "ep3bcr", db_sesion)
            res = c_creador.post(
                "/api/emparejamientos", json={"codigo_partida": codigo}
            )
            assert res.status_code == 201
            res = c_b.post(
                "/api/emparejamientos", json={"codigo_partida": codigo}
            )
            assert res.status_code == 201
            assert res.json()["estado"] == "emparejado"
        finally:
            c_creador.close()
            c_b.close()

    def test_primer_jugador_esperando(self, client, db_sesion):
        """Primer jugador crea espera: 201 con estado esperando."""
        c_creador, _ = _client_nuevo("ep4cr")
        c_a, _ = _client_nuevo("ep4a")
        try:
            codigo = _crear_y_activar(c_creador, "ep4cr", db_sesion)
            res = c_a.post(
                "/api/emparejamientos", json={"codigo_partida": codigo}
            )
            assert res.status_code == 201
            body = res.json()
            assert body["estado"] == "esperando"
            assert body["emparejado_en"] is None
        finally:
            c_creador.close()
            c_a.close()

    def test_segundo_jugador_emparejado(self, client, db_sesion):
        """Segundo jugador matchea: 201 con estado emparejado + datos."""
        c_creador, _ = _client_nuevo("ep5cr")
        c_a, _ = _client_nuevo("ep5a")
        c_b, _ = _client_nuevo("ep5b")
        try:
            codigo = _crear_y_activar(c_creador, "ep5cr", db_sesion)
            # A crea espera
            res = c_a.post(
                "/api/emparejamientos", json={"codigo_partida": codigo}
            )
            assert res.status_code == 201

            # B matchea
            res = c_b.post(
                "/api/emparejamientos", json={"codigo_partida": codigo}
            )
            assert res.status_code == 201
            body = res.json()
            assert body["estado"] == "emparejado"
            assert body["emparejado_en"] is not None
        finally:
            c_creador.close()
            c_a.close()
            c_b.close()

    def test_idempotente_mismo_usuario(self, client, db_sesion):
        """Segunda llamada del mismo usuario → 200 esperando, sin duplicado."""
        c_creador, _ = _client_nuevo("ep6cr")
        c_a, _ = _client_nuevo("ep6a")
        try:
            codigo = _crear_y_activar(c_creador, "ep6cr", db_sesion)
            # Primera llamada: 201
            res1 = c_a.post(
                "/api/emparejamientos", json={"codigo_partida": codigo}
            )
            assert res1.status_code == 201
            assert res1.json()["estado"] == "esperando"

            # Segunda llamada: 200 (idempotente)
            res2 = c_a.post(
                "/api/emparejamientos", json={"codigo_partida": codigo}
            )
            assert res2.status_code == 200
            assert res2.json()["estado"] == "esperando"

            # Solo 1 fila activa en la DB
            fila = _emparejamiento_row(db_sesion, codigo)
            assert fila is not None
            assert fila.estado == "esperando"
        finally:
            c_creador.close()
            c_a.close()

    def test_carrera_matchea_o_409(self, client, db_sesion):
        """Insert directo de una fila esperando (simula carrera) → match o 409."""
        c_creador, _ = _client_nuevo("ep7cr")
        c_b, _ = _client_nuevo("ep7b")
        try:
            codigo = _crear_y_activar(c_creador, "ep7cr", db_sesion)
            # Insertar directamente una fila esperando de OTRO usuario
            # (simula que alguien creó la espera entre el SELECT y el INSERT)
            c_ajena, username_ajena = _client_nuevo("ep7aj")
            _crear_partida(c_ajena, "ep7aj2")
            # Necesitamos una fila en la DB directamente
            from app.models.usuario import Usuario

            usuario_ajena = (
                db_sesion.query(Usuario)
                .filter(Usuario.username == username_ajena)
                .first()
            )
            partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).first()
            fila_directa = Emparejamiento(
                id=uuid.uuid4(),
                partida_id=partida.id,
                jugador1_id=usuario_ajena.id,
                estado="esperando",
            )
            db_sesion.add(fila_directa)
            db_sesion.commit()

            # B intenta matchear → debe matchear o recibir 409
            res = c_b.post(
                "/api/emparejamientos", json={"codigo_partida": codigo}
            )
            assert res.status_code in (201, 409)
            c_ajena.close()
        finally:
            c_creador.close()
            c_b.close()

    def test_409_tercero_sobre_duelo_emparejado(self, client, db_sesion):
        """Un TERCER usuario sobre una partida ya emparejada no puede matchear
        (no hay fila `esperando`) ni insertar (índice UNIQUE parcial) → 409."""
        c_creador, _ = _client_nuevo("ep8cr")
        c_a, _ = _client_nuevo("ep8a")
        c_b, _ = _client_nuevo("ep8b")
        c_c, _ = _client_nuevo("ep8c")
        try:
            codigo = _crear_y_activar(c_creador, "ep8cr", db_sesion)
            c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})
            c_b.post("/api/emparejamientos", json={"codigo_partida": codigo})

            # C intenta unirse al duelo ya formado → carrera irresoluble → 409
            res = c_c.post(
                "/api/emparejamientos", json={"codigo_partida": codigo}
            )
            assert res.status_code == 409
        finally:
            c_creador.close()
            c_a.close()
            c_b.close()
            c_c.close()


# ===========================================================================
# GET /api/emparejamientos/estado
# ===========================================================================


class TestGetEstado:
    def test_401_sin_sesion(self, client):
        res = client.get("/api/emparejamientos/estado")
        assert res.status_code == 401

    def test_estado_null_sin_fila(self, client, db_sesion):
        """Sin emparejamiento activo → estado: null."""
        c_a, _ = _client_nuevo("ge1")
        try:
            res = c_a.get("/api/emparejamientos/estado")
            assert res.status_code == 200
            assert res.json()["estado"] is None
        finally:
            c_a.close()

    def test_estado_esperando(self, client, db_sesion):
        """Fila esperando → respuesta con metadatos de partida (anti-cheat)."""
        c_creador, _ = _client_nuevo("ge2cr")
        c_a, _ = _client_nuevo("ge2a")
        try:
            codigo = _crear_y_activar(c_creador, "ge2cr", db_sesion)
            c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})

            res = c_a.get("/api/emparejamientos/estado")
            assert res.status_code == 200
            body = res.json()
            assert body["estado"] == "esperando"
            # Anti-cheat: la respuesta NO contiene palabras, grilla, posiciones
            assert "palabras" not in body
            assert "grilla" not in body
            assert body["partida"] is not None
            assert body["partida"]["codigo"] == codigo
            assert body["partida"]["tipo"] == "sopa"
        finally:
            c_creador.close()
            c_a.close()

    def test_estado_emparejado_con_rival(self, client, db_sesion):
        """Fila emparejado → rival (username) + partida."""
        c_creador, _ = _client_nuevo("ge3cr")
        c_a, _ = _client_nuevo("ge3a")
        c_b, _ = _client_nuevo("ge3b")
        try:
            codigo = _crear_y_activar(c_creador, "ge3cr", db_sesion)
            c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})
            c_b.post("/api/emparejamientos", json={"codigo_partida": codigo})

            # A ve emparejado con rival
            res = c_a.get("/api/emparejamientos/estado")
            assert res.status_code == 200
            body = res.json()
            assert body["estado"] == "emparejado"
            assert body["rival"] is not None
            assert body["emparejado_en"] is not None
        finally:
            c_creador.close()
            c_a.close()
            c_b.close()

    def test_cancelado_consumido_una_vez(self, client, db_sesion):
        """cancelado se consume una vez: poll → cancelado; siguiente poll → null."""
        c_creador, _ = _client_nuevo("ge4cr")
        c_a, _ = _client_nuevo("ge4a")
        try:
            codigo = _crear_y_activar(c_creador, "ge4cr", db_sesion)
            c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})

            # Cancelar
            res = c_a.delete("/api/emparejamientos")
            assert res.status_code == 204

            # Primer poll: cancelado
            res = c_a.get("/api/emparejamientos/estado")
            assert res.status_code == 200
            assert res.json()["estado"] == "cancelado"

            # Segundo poll: null
            res = c_a.get("/api/emparejamientos/estado")
            assert res.status_code == 200
            assert res.json()["estado"] is None
        finally:
            c_creador.close()
            c_a.close()

    def test_expirado_consumido_una_vez(self, client, db_sesion):
        """expirado se consume una vez: poll → expirado; siguiente poll → null."""
        c_creador, _ = _client_nuevo("ge5cr")
        c_a, _ = _client_nuevo("ge5a")
        try:
            codigo = _crear_y_activar(c_creador, "ge5cr", db_sesion)
            c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})

            # Forzar expiración: creado_en hace 61s
            from datetime import datetime, timezone, timedelta
            fila = _emparejamiento_row(db_sesion, codigo)
            fila.creado_en = datetime.now(timezone.utc) - timedelta(seconds=61)
            db_sesion.commit()

            # Primer poll: expirado (lazy expiry)
            res = c_a.get("/api/emparejamientos/estado")
            assert res.status_code == 200
            assert res.json()["estado"] == "expirado"

            # Segundo poll: null (consumido)
            res = c_a.get("/api/emparejamientos/estado")
            assert res.status_code == 200
            assert res.json()["estado"] is None
        finally:
            c_creador.close()
            c_a.close()

    def test_lazy_expiry_libera_partida(self, client, db_sesion):
        """Espera vencida (creado_en +60s) → expirado; otro usuario puede esperar."""
        c_creador, _ = _client_nuevo("ge6cr")
        c_a, _ = _client_nuevo("ge6a")
        c_b, _ = _client_nuevo("ge6b")
        try:
            codigo = _crear_y_activar(c_creador, "ge6cr", db_sesion)
            c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})

            # Forzar expiración
            fila = _emparejamiento_row(db_sesion, codigo)
            fila.creado_en = datetime.now(timezone.utc) - timedelta(seconds=61)
            db_sesion.commit()

            # A poll: expirado
            res = c_a.get("/api/emparejamientos/estado")
            assert res.json()["estado"] == "expirado"

            # B puede ahora esperar en la misma partida
            res = c_b.post("/api/emparejamientos", json={"codigo_partida": codigo})
            assert res.status_code == 201
            assert res.json()["estado"] == "esperando"
        finally:
            c_creador.close()
            c_a.close()
            c_b.close()

    def test_espera_bajo_60s_no_expira(self, client, db_sesion):
        """Borde determinista: espera con MENOS de 60 s NO expira (el contrato
        es 'más de 60 s'; el 'exacto' es flaky por el desfase de reloj entre
        el seteo del test y la evaluación del servidor — microsegundos)."""
        c_creador, _ = _client_nuevo("ge7cr")
        c_a, _ = _client_nuevo("ge7a")
        try:
            codigo = _crear_y_activar(c_creador, "ge7cr", db_sesion)
            c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})

            fila = _emparejamiento_row(db_sesion, codigo)
            fila.creado_en = datetime.now(timezone.utc) - timedelta(seconds=59)
            db_sesion.commit()

            res = c_a.get("/api/emparejamientos/estado")
            assert res.status_code == 200
            assert res.json()["estado"] == "esperando"
        finally:
            c_creador.close()
            c_a.close()

    def test_fila_ajena_no_visible_en_estado_propio(self, client, db_sesion):
        """El estado de un usuario NUNCA ve la fila de otro (privacidad)."""
        c_creador, _ = _client_nuevo("ge8cr")
        c_a, _ = _client_nuevo("ge8a")
        c_b, _ = _client_nuevo("ge8b")
        try:
            codigo = _crear_y_activar(c_creador, "ge8cr", db_sesion)
            c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})

            # B no tiene fila → estado null (no ve la espera de A)
            res = c_b.get("/api/emparejamientos/estado")
            assert res.status_code == 200
            assert res.json()["estado"] is None
        finally:
            c_creador.close()
            c_a.close()
            c_b.close()


# ===========================================================================
# DELETE /api/emparejamientos
# ===========================================================================


class TestDeleteEmparejamiento:
    def test_401_sin_sesion(self, client):
        res = client.delete("/api/emparejamientos")
        assert res.status_code == 401

    def test_cancelar_esperando(self, client, db_sesion):
        """Cancela espera → 204; poll devuelve cancelado una vez."""
        c_creador, _ = _client_nuevo("de1cr")
        c_a, _ = _client_nuevo("de1a")
        try:
            codigo = _crear_y_activar(c_creador, "de1cr", db_sesion)
            c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})

            res = c_a.delete("/api/emparejamientos")
            assert res.status_code == 204

            res = c_a.get("/api/emparejamientos/estado")
            assert res.json()["estado"] == "cancelado"
        finally:
            c_creador.close()
            c_a.close()

    def test_cancelar_emparejado_rechazado(self, client, db_sesion):
        """Fila emparejado → 400 'El duelo ya comenzó'."""
        c_creador, _ = _client_nuevo("de2cr")
        c_a, _ = _client_nuevo("de2a")
        c_b, _ = _client_nuevo("de2b")
        try:
            codigo = _crear_y_activar(c_creador, "de2cr", db_sesion)
            c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})
            c_b.post("/api/emparejamientos", json={"codigo_partida": codigo})

            # A intenta cancelar estando emparejado
            res = c_a.delete("/api/emparejamientos")
            assert res.status_code == 400
            assert "comenzó" in res.json()["detail"].lower()
        finally:
            c_creador.close()
            c_a.close()
            c_b.close()

    def test_cancelar_sin_fila_idempotente(self, client, db_sesion):
        """Sin fila activa → 204 idempotente."""
        c_a, _ = _client_nuevo("de3a")
        try:
            res = c_a.delete("/api/emparejamientos")
            assert res.status_code == 204
        finally:
            c_a.close()


# ===========================================================================
# D15: duelo emparejado sin arranque → libera a los 10 min
# ===========================================================================


class TestDueloSinArranque:
    def test_emparejado_sin_iniciado_en_expira(self, client, db_sesion):
        """Duelo emparejado con iniciado_en=None y emparejado_en +10min → expirado."""
        c_creador, _ = _client_nuevo("d15cr")
        c_a, _ = _client_nuevo("d15a")
        c_b, _ = _client_nuevo("d15b")
        try:
            codigo = _crear_y_activar(c_creador, "d15cr", db_sesion)
            c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})
            c_b.post("/api/emparejamientos", json={"codigo_partida": codigo})

            # Manipular emparejado_en a hace 10+ min, iniciado_en sigue None
            fila = _emparejamiento_row(db_sesion, codigo)
            fila.emparejado_en = datetime.now(timezone.utc) - timedelta(seconds=601)
            db_sesion.commit()

            # Poll de A → expirado
            res = c_a.get("/api/emparejamientos/estado")
            assert res.status_code == 200
            assert res.json()["estado"] == "expirado"
        finally:
            c_creador.close()
            c_a.close()
            c_b.close()

    def test_emparejado_con_iniciado_en_no_expira(self, client, db_sesion):
        """Duelo emparejado con iniciado_en seteado → NO expira aunque pase 10 min."""
        c_creador, _ = _client_nuevo("d16cr")
        c_a, _ = _client_nuevo("d16a")
        c_b, _ = _client_nuevo("d16b")
        try:
            codigo = _crear_y_activar(c_creador, "d16cr", db_sesion)
            c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})
            c_b.post("/api/emparejamientos", json={"codigo_partida": codigo})

            # Setear emparejado_en + iniciado_en en el pasado
            fila = _emparejamiento_row(db_sesion, codigo)
            fila.emparejado_en = datetime.now(timezone.utc) - timedelta(seconds=601)
            fila.iniciado_en = datetime.now(timezone.utc) - timedelta(seconds=600)
            db_sesion.commit()

            # Poll de A → sigue emparejado (iniciado_en protege)
            res = c_a.get("/api/emparejamientos/estado")
            assert res.status_code == 200
            assert res.json()["estado"] == "emparejado"
        finally:
            c_creador.close()
            c_a.close()
            c_b.close()
