import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.models import Partida
from app.models.emparejamiento import Emparejamiento
from app.services.sopa_generator import calcular_celda_final

PALABRAS_12 = [
    {"palabra": "CASA", "explicacion": "Vivienda"},
    {"palabra": "SOL", "explicacion": "Astro"},
    {"palabra": "LUNA", "explicacion": "Satélite"},
    {"palabra": "MAR", "explicacion": "Agua salada"},
    {"palabra": "PAN", "explicacion": "Alimento"},
    {"palabra": "MESA", "explicacion": "Mueble"},
    {"palabra": "FLOR", "explicacion": "Planta"},
    {"palabra": "NUBE", "explicacion": "Vapor"},
    {"palabra": "RIO", "explicacion": "Corriente"},
    {"palabra": "CIELO", "explicacion": "Esfera celeste"},
    {"palabra": "PLAYA", "explicacion": "Costa"},
    {"palabra": "ARENA", "explicacion": "Granos"},
]


def _client_nuevo(prefijo="amend12"):
    c = TestClient(app)
    username = f"{prefijo}_{uuid.uuid4().hex[:10]}"
    res = c.post(
        "/api/auth/registro",
        json={"username": username, "password": "clave123"},
    )
    assert res.status_code == 201, res.text
    return c, username


def _crear_y_publicar(client, tipo="sopa", palabras=PALABRAS_12):
    res = client.post("/api/partidas", json={"tipo": tipo, "palabras": palabras})
    assert res.status_code == 201, res.text
    codigo = res.json()["codigo"]
    res = client.post(f"/api/partidas/{codigo}/finalizar")
    assert res.status_code == 200, res.text
    return codigo


def _duelo_emparejado(c_creador, c_a, c_b, codigo):
    """A crea la espera, B matchea y B entra a jugar (setea `iniciado_en`)."""
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


def _palabra_id(client, codigo, texto):
    res = client.get(f"/api/partidas/{codigo}")
    assert res.status_code == 200, res.text
    return next(p["id"] for p in res.json()["palabras"] if p["palabra"] == texto)


def _seleccion_sopa(db_sesion, codigo, texto):
    partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).one()
    palabra = next(p for p in partida.palabras if p.palabra == texto)
    pos = palabra.posicion
    fila_fin, col_fin = calcular_celda_final(
        pos["fila"], pos["columna"], pos["orientacion"], len(palabra.palabra)
    )
    return {
        "fila_inicio": pos["fila"],
        "columna_inicio": pos["columna"],
        "fila_fin": fila_fin,
        "columna_fin": col_fin,
    }


def _marcar(client, db_sesion, codigo, texto):
    palabra_id = _palabra_id(client, codigo, texto)
    return client.put(
        f"/api/partidas/{codigo}/palabras/{palabra_id}/encontrada",
        json=_seleccion_sopa(db_sesion, codigo, texto),
    )




def test_primero_en_completar_individual_gana(client, db_sesion):
    c_creador, _ = _client_nuevo("v1cr")
    c_a, username_a = _client_nuevo("v1a")
    c_b, _ = _client_nuevo("v1b")
    try:
        codigo = _crear_y_publicar(c_creador)
        _duelo_emparejado(c_creador, c_a, c_b, codigo)

        fila = _fila_de(db_sesion, codigo)
        fila.jugador1_palabras = 11
        fila.jugador2_palabras = 11  # la jugada de J2 completa 12
        db_sesion.commit()

        res = _marcar(c_b, db_sesion, codigo, "SOL")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["encontrada"] is True

        db_sesion.refresh(fila)
        assert fila.estado == "finalizado"
        assert fila.ganador_id == fila.jugador2_id  # el que completó gana
        assert fila.jugador1_palabras == 11
        assert fila.jugador2_palabras == 12
        assert fila.finalizado_en is not None

        partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).one()
        assert partida.estado == "activo"  # CAMBIO 4: no se consume

        duelo = body["duelo_finalizado"]
        assert duelo["yo_palabras"] == 12
        assert duelo["rival_palabras"] == 11
        assert duelo["gane"] is True
        assert duelo["rival"] == username_a
        assert duelo["tiempo_total_seg"] >= 0
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()


def test_caso_po_7_5_ya_no_corta_por_suma(client, db_sesion):
    """Triangulación del ejemplo INVÁLIDO del PO (12 palabras, 7-5): la suma
    7+5 == 12 alcanza el total, pero ninguno completó EN SOLITARIO → el duelo
    NO corta. Antes (corte por suma con doble conteo) este escenario cortaba
    y ganaba J1 por mayoría; la regla nueva lo espera activo."""
    c_creador, _ = _client_nuevo("v2cr")
    c_a, _ = _client_nuevo("v2a")
    c_b, _ = _client_nuevo("v2b")
    try:
        codigo = _crear_y_publicar(c_creador)
        _duelo_emparejado(c_creador, c_a, c_b, codigo)

        fila = _fila_de(db_sesion, codigo)
        fila.jugador1_palabras = 7
        fila.jugador2_palabras = 5  # suma 12 == total, pero nadie tiene 12
        db_sesion.commit()

        res = _marcar(c_b, db_sesion, codigo, "SOL")
        assert res.status_code == 200, res.text
        assert res.json().get("duelo_finalizado") is None

        db_sesion.refresh(fila)
        assert fila.estado == "emparejado"  # NO cortó: 6 < 12
        assert fila.jugador2_palabras == 6
        assert fila.ganador_id is None
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()


def test_jugador_que_llega_a_total_corta_el_duelo(client, db_sesion):
    """Borde: J1 marca hasta completar 12/12 él solo (J2 con 0). El corte se
    dispara SOLO por el contador propio, no por la suma."""
    c_creador, _ = _client_nuevo("v3cr")
    c_a, _ = _client_nuevo("v3a")
    c_b, _ = _client_nuevo("v3b")
    try:
        codigo = _crear_y_publicar(c_creador)
        _duelo_emparejado(c_creador, c_a, c_b, codigo)

        fila = _fila_de(db_sesion, codigo)
        fila.jugador1_palabras = 11
        fila.jugador2_palabras = 0
        db_sesion.commit()

        res = _marcar(c_a, db_sesion, codigo, "SOL")
        assert res.status_code == 200, res.text
        duelo = res.json()["duelo_finalizado"]
        assert duelo["gane"] is True
        assert duelo["yo_palabras"] == 12

        db_sesion.refresh(fila)
        assert fila.estado == "finalizado"
        assert fila.ganador_id == fila.jugador1_id
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()




def test_partida_vuelve_al_lobby_sin_badge_post_duelo(client, db_sesion):
    """Tras un duelo finalizado (abandono), `GET /api/lobby/partidas` vuelve a
    mostrar la partida SIN badge `en_duelo`, y "Mis partidas" la muestra
    `activo` con `en_duelo: false`."""
    c_creador, _ = _client_nuevo("r1cr")
    c_a, _ = _client_nuevo("r1a")
    c_b, _ = _client_nuevo("r1b")
    try:
        codigo = _crear_y_publicar(c_creador)

        _duelo_emparejado(c_creador, c_a, c_b, codigo)
        res = c_b.post(
            "/api/emparejamientos/abandonar", json={"codigo_partida": codigo}
        )
        assert res.status_code == 200, res.text

        # Lobby del mundo: aparece de nuevo, sin duelo activo.
        res = c_creador.get("/api/lobby/partidas")
        assert res.status_code == 200, res.text
        item = next(p for p in res.json() if p["codigo"] == codigo)
        assert item["en_duelo"] is False

        # "Mis partidas" del creador: activa, sin badge.
        res = c_creador.get("/api/partidas")
        item = next(p for p in res.json() if p["codigo"] == codigo)
        assert item["estado"] == "activo"
        assert item["en_duelo"] is False
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()


def test_nuevo_duelo_posible_tras_finalizar_el_anterior(client, db_sesion):
    """Re-jugabilidad (pool infinito): tras finalizar un duelo, OTRO jugador
    puede crear una espera nueva sobre la MISMA partida (201) y formarse un
    1v1 nuevo (el índice UNIQUE parcial solo cubre esperando|emparejado)."""
    c_creador, _ = _client_nuevo("r2cr")
    c_a, _ = _client_nuevo("r2a")
    c_b, _ = _client_nuevo("r2b")
    c_c, _ = _client_nuevo("r2c")
    try:
        codigo = _crear_y_publicar(c_creador)

        _duelo_emparejado(c_creador, c_a, c_b, codigo)
        res = c_b.post(
            "/api/emparejamientos/abandonar", json={"codigo_partida": codigo}
        )
        assert res.status_code == 200, res.text

        # C crea una espera nueva → 201 (partida sigue activa, fila anterior
        # ya `finalizado` y fuera del índice).
        res = c_c.post("/api/emparejamientos", json={"codigo_partida": codigo})
        assert res.status_code == 201, res.text
        assert res.json()["estado"] == "esperando"

        # A (ex protagonista) matchea contra la espera de C → duelo nuevo.
        res = c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})
        assert res.status_code == 201, res.text
        assert res.json()["estado"] == "emparejado"
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()
        c_c.close()