import uuid
from datetime import datetime, timezone, timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.models.emparejamiento import Emparejamiento
from app.models.partida import Partida


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CAMPOS_LOBBY = {"codigo", "tipo", "cantidad_palabras", "nombre", "en_duelo", "en_espera"}


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


def _matchear_duelo(client_a, client_b, codigo):
    res = client_a.post("/api/emparejamientos", json={"codigo_partida": codigo})
    assert res.status_code in (200, 201), res.text
    res = client_b.post("/api/emparejamientos", json={"codigo_partida": codigo})
    assert res.status_code in (200, 201), res.text
    assert res.json()["estado"] == "emparejado", res.text


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
# Exclusión por duelo activo propio (AMEND CAMBIO 3 — DD-07 reemplazado)
# ---------------------------------------------------------------------------


class TestExclusion:
    def test_creador_ve_su_partida(self, client, db_sesion):
        """AMEND CAMBIO 3: el creador autenticado SÍ ve su propia partida en el
        lobby (ya puede participar del 1v1). La ajena también la ve."""
        c_creador, _ = _client_nuevo("ex1cr")
        c_ajeno, _ = _client_nuevo("ex1aj")
        try:
            codigo_propia = _crear_y_activar(c_creador, "ex1cr", db_sesion)
            codigo_ajena = _crear_y_activar(c_ajeno, "ex1aj", db_sesion, la_palabra="LUNA")

            # El creador ve AMBAS: la propia y la ajena
            res = c_creador.get("/api/lobby/partidas")
            codigos = [p["codigo"] for p in res.json()]
            assert codigo_propia in codigos
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
    def test_espera_ajena_no_bloquea_el_1v1(self, client, db_sesion):
        c_creador, _ = _client_nuevo("ed0cr")
        c_j1, _ = _client_nuevo("ed0j1")
        c_j2, _ = _client_nuevo("ed0j2")
        try:
            codigo = _crear_y_activar(c_creador, "ed0cr", db_sesion)
            # J1 elige la partida → crea la fila `esperando` (NO un duelo)
            res = c_j1.post("/api/emparejamientos", json={"codigo_partida": codigo})
            assert res.status_code in (200, 201), res.text
            assert res.json()["estado"] == "esperando", res.text

            # J2 la ve en el lobby: espera ajena ≠ duelo → 1v1 disponible
            res = c_j2.get("/api/lobby/partidas")
            item = next(p for p in res.json() if p["codigo"] == codigo)
            assert item["en_duelo"] is False
            assert item["en_espera"] is True

            # J2 se une al 1v1 → matchea contra la espera ajena de J1
            res = c_j2.post("/api/emparejamientos", json={"codigo_partida": codigo})
            assert res.status_code in (200, 201), res.text
            assert res.json()["estado"] == "emparejado", res.text
        finally:
            c_creador.close()
            c_j1.close()
            c_j2.close()

    def test_en_espera_true_con_fila_esperando(self, client, db_sesion):
        c_creador, _ = _client_nuevo("ed1cr")
        c_a, _ = _client_nuevo("ed1a")
        c_ajeno, _ = _client_nuevo("ed1aj")
        try:
            codigo = _crear_y_activar(c_creador, "ed1cr", db_sesion)
            c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})

            res = c_ajeno.get("/api/lobby/partidas")
            item = next(p for p in res.json() if p["codigo"] == codigo)
            assert item["en_duelo"] is False
            assert item["en_espera"] is True
        finally:
            c_creador.close()
            c_a.close()
            c_ajeno.close()

    def test_partida_emparejada_mantiene_en_duelo(self, client, db_sesion):
        """23 (3.1): duelo FORMADO (`emparejado`) → `en_duelo: true` y
        `en_espera: false` (el contrato previo de `en_duelo` sigue intacto)."""
        c_creador, _ = _client_nuevo("ed4cr")
        c_a, _ = _client_nuevo("ed4a")
        c_b, _ = _client_nuevo("ed4b")
        c_ajeno, _ = _client_nuevo("ed4aj")
        try:
            codigo = _crear_y_activar(c_creador, "ed4cr", db_sesion)
            _matchear_duelo(c_a, c_b, codigo)

            res = c_ajeno.get("/api/lobby/partidas")
            item = next(p for p in res.json() if p["codigo"] == codigo)
            assert item["en_duelo"] is True
            assert item["en_espera"] is False
        finally:
            c_creador.close()
            c_a.close()
            c_b.close()
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

    def test_partida_libre_sin_flags(self, client, db_sesion):
        c_creador, _ = _client_nuevo("ed5cr")
        c_a, _ = _client_nuevo("ed5a")
        try:
            codigo = _crear_y_activar(c_creador, "ed5cr", db_sesion)
            # A crea la espera y la cancela → la fila queda `cancelado` (no activa)
            res = c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})
            assert res.status_code in (200, 201), res.text
            res = c_a.delete("/api/emparejamientos")
            assert res.status_code == 204, res.text

            res = client.get("/api/lobby/partidas")
            item = next(p for p in res.json() if p["codigo"] == codigo)
            assert item["en_duelo"] is False
            assert item["en_espera"] is False
        finally:
            c_creador.close()
            c_a.close()

    def test_espera_vencida_libera_flags(self, client, db_sesion):
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
            assert item["en_espera"] is False
        finally:
            c_creador.close()
            c_a.close()
            c_ajeno.close()

    def test_duelo_vencido_ttl_libera_flags(self, client, db_sesion):
        c_creador, _ = _client_nuevo("ttlcr")
        c_a, _ = _client_nuevo("ttla")
        c_b, _ = _client_nuevo("ttlb")
        c_ajeno, _ = _client_nuevo("ttlaj")
        try:
            codigo = _crear_y_activar(c_creador, "ttlcr", db_sesion)
            _matchear_duelo(c_a, c_b, codigo)

            # Vida agotada: iniciado_en hace 61 min (la partida quedó marcada
            # en_duelo hasta que un punto de lectura recicle la fila)
            fila = _emparejamiento_row(db_sesion, codigo)
            fila.iniciado_en = datetime.now(timezone.utc) - timedelta(seconds=3661)
            db_sesion.commit()

            res = c_ajeno.get("/api/lobby/partidas")
            item = next(p for p in res.json() if p["codigo"] == codigo)
            assert item["en_duelo"] is False
            assert item["en_espera"] is False
        finally:
            c_creador.close()
            c_a.close()
            c_b.close()
            c_ajeno.close()

    def test_schema_lobby_aditivo(self, client, db_sesion):
        c_creador, _ = _client_nuevo("ed6cr")
        try:
            codigo = _crear_y_activar(c_creador, "ed6cr", db_sesion)
            res = client.get("/api/lobby/partidas")
            item = next(p for p in res.json() if p["codigo"] == codigo)

            # El campo nuevo viaja...
            assert item["en_espera"] is False
            # ...y el contrato previo conserva tipo y valor
            assert set(CAMPOS_LOBBY).issubset(item.keys())
            assert isinstance(item["codigo"], str)
            assert isinstance(item["tipo"], str)
            assert isinstance(item["cantidad_palabras"], int)
            assert item["nombre"] is None
            assert item["en_duelo"] is False
        finally:
            c_creador.close()


# ---------------------------------------------------------------------------
# Partida re-jugable tras el duelo (AMEND CAMBIO 4 — spec `lobby`)
# ---------------------------------------------------------------------------


class TestPartidaRejugablePostDuelo:
    """El duelo 1v1 ya NO consume la partida (AMEND CAMBIO 4): terminado el
    duelo (por abandono D4), la partida queda `activo` — reaparece en el
    lobby sin badge, 'Mis partidas' la muestra activa y acepta un nuevo
    duelo y jugadores."""

    def _setup_duelo_finalizado(self, db_sesion, c_creador, c_a, c_b):
        """Partida activa cuyo duelo terminó por abandono (D4) — sigue `activo`
        gracias a CAMBIO 4."""
        codigo = _crear_y_activar(c_creador, "lf", db_sesion)
        res = c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})
        assert res.status_code in (200, 201), res.text
        res = c_b.post("/api/emparejamientos", json={"codigo_partida": codigo})
        assert res.status_code in (200, 201), res.text
        assert res.json()["estado"] == "emparejado", res.text
        res = c_b.post(f"/api/partidas/{codigo}/unirse")
        assert res.status_code == 200, res.text
        res = c_b.post(
            "/api/emparejamientos/abandonar", json={"codigo_partida": codigo}
        )
        assert res.status_code == 200, res.text
        return codigo

    def test_partida_vuelve_al_lobby_tras_el_duelo(self, client, db_sesion):
        """CAMBIO 4: la partida cuyo duelo terminó sigue `activo` → aparece en
        el lobby (disponible para nuevos jugadores)."""
        c_creador, _ = _client_nuevo("lf1cr")
        c_a, _ = _client_nuevo("lf1a")
        c_b, _ = _client_nuevo("lf1b")
        c_ajeno, _ = _client_nuevo("lf1aj")
        try:
            codigo = self._setup_duelo_finalizado(db_sesion, c_creador, c_a, c_b)

            res = c_ajeno.get("/api/lobby/partidas")
            codigos = [p["codigo"] for p in res.json()]
            item = next(p for p in res.json() if p["codigo"] == codigo)
            assert item["en_duelo"] is False  # sin badge
            assert codigo in codigos
        finally:
            c_creador.close()
            c_a.close()
            c_b.close()
            c_ajeno.close()

    def test_mis_partidas_activa_sin_badge_post_duelo(self, client, db_sesion):
        """CAMBIO 4: 'Mis partidas' (GET /partidas) muestra la partida tras el
        duelo como `estado: "activo"` y `en_duelo: false` (re-jugable)."""
        c_creador, _ = _client_nuevo("lf2cr")
        c_a, _ = _client_nuevo("lf2a")
        c_b, _ = _client_nuevo("lf2b")
        try:
            codigo = self._setup_duelo_finalizado(db_sesion, c_creador, c_a, c_b)

            res = c_creador.get("/api/partidas")
            assert res.status_code == 200, res.text
            item = next(p for p in res.json() if p["codigo"] == codigo)
            assert item["estado"] == "activo"
            assert item["en_duelo"] is False
        finally:
            c_creador.close()
            c_a.close()
            c_b.close()

    def test_partida_acepta_nuevo_duelo_y_unirse_post_duelo(self, client, db_sesion):
        """CAMBIO 4: tras el duelo la partida acepta de nuevo POST
        /emparejamientos (espera 201) y /unirse (registrado 200)."""
        c_creador, _ = _client_nuevo("lf3cr")
        c_a, _ = _client_nuevo("lf3a")
        c_b, _ = _client_nuevo("lf3b")
        c_nuevo, _ = _client_nuevo("lf3n")
        try:
            codigo = self._setup_duelo_finalizado(db_sesion, c_creador, c_a, c_b)

            res = c_nuevo.post("/api/emparejamientos", json={"codigo_partida": codigo})
            assert res.status_code == 201, res.text
            assert res.json()["estado"] == "esperando"

            res = c_nuevo.post(f"/api/partidas/{codigo}/unirse")
            assert res.status_code == 200, res.text
            assert res.json().get("emparejado") is False  # registrado, sin duelo
        finally:
            c_creador.close()
            c_a.close()
            c_b.close()
            c_nuevo.close()