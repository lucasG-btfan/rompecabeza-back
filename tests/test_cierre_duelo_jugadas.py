
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import app
from app.models import Partida, Usuario
from app.models.emparejamiento import Emparejamiento
from app.routes.emparejamientos.helpers_duelo import _finalizar_duelo
from app.services.sopa_generator import calcular_celda_final

# 12 palabras cortas para el ejemplo del spec (J1=7, J2=5 tras la última).
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client_nuevo(prefijo="c19j"):
    """Registra un usuario nuevo (cookie de sesión) y devuelve (client, user)."""
    c = TestClient(app)
    username = f"{prefijo}_{uuid.uuid4().hex[:10]}"
    res = c.post(
        "/api/auth/registro",
        json={"username": username, "password": "clave123"},
    )
    assert res.status_code == 201, res.text
    return c, username


def _crear_y_publicar(client, tipo, palabras):
    """Crea la partida y la publica (POST /finalizar): posiciones + `activo`."""
    res = client.post("/api/partidas", json={"tipo": tipo, "palabras": palabras})
    assert res.status_code == 201, res.text
    codigo = res.json()["codigo"]
    res = client.post(f"/api/partidas/{codigo}/finalizar")
    assert res.status_code == 200, res.text
    return codigo


def _palabra_id(client, codigo, texto):
    res = client.get(f"/api/partidas/{codigo}")
    assert res.status_code == 200, res.text
    return next(p["id"] for p in res.json()["palabras"] if p["palabra"] == texto)


def _posicionar(client, codigo, palabra_id, fila, columna, orientacion):
    return client.put(
        f"/api/partidas/{codigo}/palabras/{palabra_id}/posicion",
        json={"fila": fila, "columna": columna, "orientacion": orientacion},
    )


def _crucigrama_pato(client):
    res = client.post(
        "/api/partidas",
        json={
            "tipo": "crucigrama",
            "palabras": [
                {"palabra": "PATO", "explicacion": "Animal de granja"},
                {"palabra": "ORO", "explicacion": "Metal precioso"},
                {"palabra": "AS", "explicacion": "Carta de la baraja"},
            ],
        },
    )
    assert res.status_code == 201, res.text
    codigo = res.json()["codigo"]
    pato = _palabra_id(client, codigo, "PATO")
    oro = _palabra_id(client, codigo, "ORO")
    as_ = _palabra_id(client, codigo, "AS")
    assert _posicionar(client, codigo, pato, 0, 0, "H").status_code == 200
    assert _posicionar(client, codigo, oro, 0, 3, "V").status_code == 200
    assert _posicionar(client, codigo, as_, 0, 1, "V").status_code == 200
    assert client.post(f"/api/partidas/{codigo}/finalizar").status_code == 200
    return codigo


def _duelo_emparejado(c_creador, c_a, c_b, codigo):
    """A crea la espera, B matchea y B entra a jugar (setea `iniciado_en`)."""
    res = c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})
    assert res.status_code in (200, 201), res.text
    res = c_b.post("/api/emparejamientos", json={"codigo_partida": codigo})
    assert res.status_code in (200, 201), res.text
    body = res.json()
    assert body["estado"] == "emparejado", body
    # D15: primer unirse de cualquiera de los dos marca el inicio del duelo.
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


def _seleccion_sopa(db_sesion, codigo, texto):
    """Selección válida real de una palabra de la sopa (lee la DB)."""
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


def _marcar(client, codigo, texto, seleccion):
    palabra_id = _palabra_id(client, codigo, texto)
    return client.put(
        f"/api/partidas/{codigo}/palabras/{palabra_id}/encontrada",
        json=seleccion,
    )


def _responder_con(client, codigo, palabra_id, letras):
    return client.put(
        f"/api/partidas/{codigo}/palabras/{palabra_id}/respuesta",
        json={"letras": letras},
    )


# ---------------------------------------------------------------------------
# Conteo por jugador (D3)
# ---------------------------------------------------------------------------


def test_hallazgo_sopa_de_j1_incrementa_contador(client, db_sesion):
    """J1 marca una palabra válida en la sopa → `jugador1_palabras` = 1 y el
    de J2 queda en 0; la respuesta NO trae duelo_finalizado (todavía no corta)."""
    c_creador, _ = _client_nuevo("s1cr")
    c_a, _ = _client_nuevo("s1a")
    c_b, _ = _client_nuevo("s1b")
    try:
        codigo = _crear_y_publicar(c_creador, "sopa", PALABRAS_12)
        _duelo_emparejado(c_creador, c_a, c_b, codigo)

        seleccion = _seleccion_sopa(db_sesion, codigo, "CASA")
        res = _marcar(c_a, codigo, "CASA", seleccion)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["encontrada"] is True
        assert body.get("duelo_finalizado") is None

        fila = _fila_de(db_sesion, codigo)
        assert fila.jugador1_palabras == 1
        assert fila.jugador2_palabras == 0
        assert fila.estado == "emparejado"  # no cortó (1 < 12)
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()


def test_respuesta_crucigrama_de_j2_incrementa_contador(client, db_sesion):
    """J2 responde PATO en el crucigrama → `jugador2_palabras` = 1 (el MISMO
    helper en ambos endpoints, D3)."""
    c_creador, _ = _client_nuevo("s2cr")
    c_a, _ = _client_nuevo("s2a")
    c_b, _ = _client_nuevo("s2b")
    try:
        codigo = _crucigrama_pato(c_creador)
        _duelo_emparejado(c_creador, c_a, c_b, codigo)

        pato_id = _palabra_id(c_creador, codigo, "PATO")
        res = _responder_con(c_b, codigo, pato_id, "PATO")
        assert res.status_code == 200, res.text
        assert res.json()["encontrada"] is True

        fila = _fila_de(db_sesion, codigo)
        assert fila.jugador1_palabras == 0
        assert fila.jugador2_palabras == 1
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()


def test_solitario_no_incrementa(client, db_sesion):
    c_creador, _ = _client_nuevo("s3cr")
    c_jugador, _ = _client_nuevo("s3j")
    try:
        codigo = _crear_y_publicar(c_creador, "sopa", PALABRAS_12)

        seleccion = _seleccion_sopa(db_sesion, codigo, "CASA")
        res = _marcar(c_jugador, codigo, "CASA", seleccion)
        assert res.status_code == 200, res.text
        assert res.json().get("duelo_finalizado") is None

        partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).one()
        assert db_sesion.query(Emparejamiento).filter(
            Emparejamiento.partida_id == partida.id
        ).count() == 0
    finally:
        c_creador.close()
        c_jugador.close()


def test_invitado_en_duelo_no_incrementa(client, db_sesion):
    """Invitado (sin cookie) jugando una partida CON duelo activo: contadores
    intactos — el incremento exige sesión (D3)."""
    c_creador, _ = _client_nuevo("s4cr")
    c_a, _ = _client_nuevo("s4a")
    c_b, _ = _client_nuevo("s4b")
    try:
        codigo = _crear_y_publicar(c_creador, "sopa", PALABRAS_12)
        _duelo_emparejado(c_creador, c_a, c_b, codigo)

        seleccion = _seleccion_sopa(db_sesion, codigo, "CASA")
        res = _marcar(client, codigo, "CASA", seleccion)  # fixture sin cookie
        assert res.status_code == 200, res.text
        assert res.json().get("duelo_finalizado") is None

        fila = _fila_de(db_sesion, codigo)
        assert fila.jugador1_palabras == 0
        assert fila.jugador2_palabras == 0
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()


def test_duelo_de_otro_usuario_no_incrementa(client, db_sesion):
    """Un tercero registrado (no es jugador del duelo) marca: NO incrementa."""
    c_creador, _ = _client_nuevo("s5cr")
    c_a, _ = _client_nuevo("s5a")
    c_b, _ = _client_nuevo("s5b")
    c_c, _ = _client_nuevo("s5c")
    try:
        codigo = _crear_y_publicar(c_creador, "sopa", PALABRAS_12)
        _duelo_emparejado(c_creador, c_a, c_b, codigo)

        seleccion = _seleccion_sopa(db_sesion, codigo, "CASA")
        res = _marcar(c_c, codigo, "CASA", seleccion)
        assert res.status_code == 200, res.text
        assert res.json().get("duelo_finalizado") is None

        fila = _fila_de(db_sesion, codigo)
        assert fila.jugador1_palabras == 0
        assert fila.jugador2_palabras == 0
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()
        c_c.close()


def test_duelo_emparejado_sin_iniciar_no_incrementa(client, db_sesion):
    """Duelo `emparejado` pero nadie entró aún (`iniciado_en` None, D15):
    la jugada NO incrementa (el duelo no está corriendo)."""
    c_creador, _ = _client_nuevo("s6cr")
    c_a, _ = _client_nuevo("s6a")
    c_b, _ = _client_nuevo("s6b")
    try:
        codigo = _crear_y_publicar(c_creador, "sopa", PALABRAS_12)
        res = c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})
        assert res.status_code in (200, 201), res.text
        res = c_b.post("/api/emparejamientos", json={"codigo_partida": codigo})
        assert res.json()["estado"] == "emparejado"
        # NO llamamos a /unirse: iniciado_en queda None.

        fila = _fila_de(db_sesion, codigo)
        assert fila.iniciado_en is None

        seleccion = _seleccion_sopa(db_sesion, codigo, "CASA")
        res = _marcar(c_a, codigo, "CASA", seleccion)
        assert res.status_code == 200, res.text
        assert res.json().get("duelo_finalizado") is None

        db_sesion.refresh(fila)
        assert fila.jugador1_palabras == 0
        assert fila.jugador2_palabras == 0
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()


def test_hallazgo_sobre_duelo_expirado_no_incrementa(client, db_sesion):
    c_creador, _ = _client_nuevo("s7cr")
    c_a, _ = _client_nuevo("s7a")
    c_b, _ = _client_nuevo("s7b")
    try:
        codigo = _crear_y_publicar(c_creador, "sopa", PALABRAS_12)
        res = c_a.post("/api/emparejamientos", json={"codigo_partida": codigo})
        assert res.status_code in (200, 201), res.text
        res = c_b.post("/api/emparejamientos", json={"codigo_partida": codigo})
        assert res.json()["estado"] == "emparejado"
        res = c_b.post(f"/api/partidas/{codigo}/unirse")
        assert res.status_code == 200, res.text

        # Vida agotada: iniciado_en hace 61 min → la fila expira (lazy).
        fila = _fila_de(db_sesion, codigo)
        fila.iniciado_en = datetime.now(timezone.utc) - timedelta(seconds=3661)
        db_sesion.commit()
        res = c_a.get("/api/emparejamientos/estado")
        assert res.json()["estado"] == "expirado"

        seleccion = _seleccion_sopa(db_sesion, codigo, "CASA")
        res = _marcar(c_a, codigo, "CASA", seleccion)
        assert res.status_code == 200, res.text
        assert res.json().get("duelo_finalizado") is None

        db_sesion.refresh(fila)
        assert fila.estado == "expirado"
        assert fila.jugador1_palabras == 0
        assert fila.jugador2_palabras == 0
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()


# ---------------------------------------------------------------------------
# Corte por completar individual (AMEND CAMBIO 1/CAMBIO 4 — feedback PO)
# ---------------------------------------------------------------------------


def test_suma_supera_total_sin_completar_no_corta_y_gana_el_que_completa(
    client, db_sesion
):
    """Caso del PO (doble conteo): la SUMA de contadores ya superó el total
    (J1=7, J2=11 → 18 > 12; pudieron haber resuelto palabras REPETIDAS) pero
    ningún contador INDIVIDUAL llega a 12 → el duelo sigue corriendo. Cuando
    J2 marca su #12 individual → corta y gana J2 (el que completó, no el de
    "mayoría simulada" por suma). La partida NO se consume (CAMBIO 4)."""
    c_creador, _ = _client_nuevo("c1cr")
    c_a, username_a = _client_nuevo("c1a")
    c_b, _ = _client_nuevo("c1b")
    try:
        codigo = _crear_y_publicar(c_creador, "sopa", PALABRAS_12)
        _duelo_emparejado(c_creador, c_a, c_b, codigo)

        fila = _fila_de(db_sesion, codigo)
        fila.jugador1_palabras = 7  # suma 18 > 12: la regla vieja ya habría cortado
        fila.jugador2_palabras = 11  # la jugada de J2 completa 12 individual
        db_sesion.commit()

        seleccion = _seleccion_sopa(db_sesion, codigo, "SOL")
        res = _marcar(c_b, codigo, "SOL", seleccion)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["encontrada"] is True

        db_sesion.refresh(fila)
        assert fila.estado == "finalizado"
        assert fila.ganador_id == fila.jugador2_id  # el que completó, no el de mayoría
        assert fila.jugador1_palabras == 7
        assert fila.jugador2_palabras == 12
        assert fila.finalizado_en is not None

        partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).one()
        assert partida.estado == "activo"  # CAMBIO 4: no se consume

        duelo = body["duelo_finalizado"]
        assert duelo["yo_palabras"] == 12
        assert duelo["rival_palabras"] == 7
        assert duelo["gane"] is True
        assert duelo["motivo"] == "corte"  
        assert duelo["rival"] == username_a  # normalizado: el rival es J1
        assert duelo["tiempo_total_seg"] >= 0
        assert duelo["finalizado_en"] is not None
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()


def test_empate_teorico_finalizar_ganador_null(client, db_sesion):
    """`_finalizar_duelo(ganador_id=None)` — caso TEÓRICO de empate: con el
    `SELECT ... FOR UPDATE` el primero que completa finaliza el duelo y el
    rival ve la fila ya `finalizado` (el empate real es casi imposible). El
    helper igualmente soporta la rama: `ganador_id` NULL → resultado
    `gane: null`, y la partida sigue `activo` (CAMBIO 4)."""
    c_creador, _ = _client_nuevo("c2cr")
    c_a, _ = _client_nuevo("c2a")
    c_b, _ = _client_nuevo("c2b")
    try:
        codigo = _crear_y_publicar(c_creador, "sopa", PALABRAS_12)
        _duelo_emparejado(c_creador, c_a, c_b, codigo)

        fila = _fila_de(db_sesion, codigo)
        usuario_a = db_sesion.query(Usuario).filter(Usuario.id == fila.jugador1_id).one()
        duelo = _finalizar_duelo(db_sesion, fila, usuario_a, ganador_id=None)

        db_sesion.refresh(fila)
        assert fila.estado == "finalizado"
        assert fila.ganador_id is None
        assert fila.finalizado_en is not None

        assert duelo.gane is None
        assert duelo.motivo == "empate" 
        assert duelo.yo_palabras == 0
        assert duelo.rival_palabras == 0

        partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).one()
        assert partida.estado == "activo"
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()


def test_hallazgo_posterior_al_corte_no_incrementa(client, db_sesion):
    """Carrera por lock (estado transitorio): la fila quedó `finalizado` pero
    la partida sigue `activo` → la jugada NO incrementa y adjunta el resultado
    del duelo con `gane: true` para el ganador (D3, spec)."""
    c_creador, _ = _client_nuevo("c3cr")
    c_a, _ = _client_nuevo("c3a")
    c_b, _ = _client_nuevo("c3b")
    try:
        codigo = _crear_y_publicar(c_creador, "sopa", PALABRAS_12)
        _duelo_emparejado(c_creador, c_a, c_b, codigo)

        # Corte simulado por otro proceso (el ganador J1, la partida todavía
        # está activa porque el corte aún no committeó partida o ya lo hizo).
        fila = _fila_de(db_sesion, codigo)
        fila.jugador1_palabras = 12  # J1 resolvió todo
        fila.estado = "finalizado"
        fila.finalizado_en = datetime.now(timezone.utc)
        fila.ganador_id = fila.jugador1_id
        db_sesion.commit()

        seleccion = _seleccion_sopa(db_sesion, codigo, "CASA")
        res = _marcar(c_a, codigo, "CASA", seleccion)
        assert res.status_code == 200, res.text
        duelo = res.json()["duelo_finalizado"]

        db_sesion.refresh(fila)
        assert fila.jugador1_palabras == 12  # sin doble incremento
        assert fila.jugador2_palabras == 0

        assert duelo["yo_palabras"] == 12
        assert duelo["rival_palabras"] == 0
        assert duelo["gane"] is True
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()


# ---------------------------------------------------------------------------
# Borde (triangulación 2.3): total 1 → el primer hallazgo corta
# ---------------------------------------------------------------------------


def test_corte_con_total_una_palabra(client, db_sesion):
    """Sopa de UNA palabra: el primer hallazgo válido de J1 completa su
    contador (1 >= 1) → corta al instante y gana J1. La partida NO se
    consume (CAMBIO 4): sigue `activo` para re-jugarse."""
    c_creador, _ = _client_nuevo("c4cr")
    c_a, _ = _client_nuevo("c4a")
    c_b, _ = _client_nuevo("c4b")
    try:
        codigo = _crear_y_publicar(
            c_creador,
            "sopa",
            [{"palabra": "SOL", "explicacion": "Astro"}],
        )
        _duelo_emparejado(c_creador, c_a, c_b, codigo)

        seleccion = _seleccion_sopa(db_sesion, codigo, "SOL")
        res = _marcar(c_a, codigo, "SOL", seleccion)
        assert res.status_code == 200, res.text
        duelo = res.json()["duelo_finalizado"]

        fila = _fila_de(db_sesion, codigo)
        assert fila.estado == "finalizado"
        assert fila.ganador_id == fila.jugador1_id
        assert duelo["yo_palabras"] == 1
        assert duelo["gane"] is True

        partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).one()
        assert partida.estado == "activo"
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()


def test_duelo_viejo_finalizado_no_se_adjunta_a_solitario(client, db_sesion):
    c_creador, _ = _client_nuevo("c5cr")
    c_a, _ = _client_nuevo("c5a")  # "Lucas" (ex-jugador J1 del duelo viejo)
    c_b, _ = _client_nuevo("c5b")  # "test2" (J2, ganó el duelo viejo)
    try:
        codigo = _crear_y_publicar(c_creador, "sopa", PALABRAS_12)
        _duelo_emparejado(c_creador, c_a, c_b, codigo)

        # Duelo viejo finalizado hace 2 h (fuera de la ventana de 60 s):
        # Lucas = 10, test2 = 2, ganó test2 (igual al reporte del PO). La
        # partida SIGUE `activo` — no se toca (AMEND CAMBIO 4).
        fila = _fila_de(db_sesion, codigo)
        fila.jugador1_palabras = 10
        fila.jugador2_palabras = 2
        fila.estado = "finalizado"
        fila.finalizado_en = datetime.now(timezone.utc) - timedelta(hours=2)
        fila.ganador_id = fila.jugador2_id
        db_sesion.commit()

        partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).one()
        assert partida.estado == "activo"  # re-jugable (CAMBIO 4)

        # Lucas (ex-jugador del duelo) juega en SOLITARIO la partida
        # re-jugable: jugada válida → el fantasma NO se adjunta.
        seleccion = _seleccion_sopa(db_sesion, codigo, "CASA")
        res = _marcar(c_a, codigo, "CASA", seleccion)
        assert res.status_code == 200, res.text
        assert res.json().get("duelo_finalizado") is None

        # Sin efectos secundarios: contadores y fila intactos.
        db_sesion.refresh(fila)
        assert fila.estado == "finalizado"
        assert fila.jugador1_palabras == 10
        assert fila.jugador2_palabras == 2
        assert fila.ganador_id == fila.jugador2_id
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()


def test_duelo_finalizado_fuera_de_ventana_no_se_adjunta_y_dentro_si(
    client, db_sesion
):
    """La ventana de 60 s (`VENTANA_CARRERA_LOCK_SEG`) es un umbral REAL, no
    ornamental: (a) fila finalizada hace 90 s (fuera) → la jugada de un
    ex-jugador NO adjunta; (b) fila finalizada hace 30 s (dentro, carrera
    concurrente real) → la misma jugada SÍ adjunta el resultado normalizado
    por requester (`gane: false` — J1 perdió contra J2) con contadores
    intactos."""
    c_creador, _ = _client_nuevo("c6cr")
    c_a, _ = _client_nuevo("c6a")
    c_b, _ = _client_nuevo("c6b")
    try:
        # (a) FUERA de la ventana: finalizada hace 90 s → no se adjunta.
        codigo_a = _crear_y_publicar(c_creador, "sopa", PALABRAS_12)
        _duelo_emparejado(c_creador, c_a, c_b, codigo_a)
        fila_a = _fila_de(db_sesion, codigo_a)
        fila_a.jugador1_palabras = 10
        fila_a.jugador2_palabras = 2
        fila_a.estado = "finalizado"
        fila_a.finalizado_en = datetime.now(timezone.utc) - timedelta(seconds=90)
        fila_a.ganador_id = fila_a.jugador2_id
        db_sesion.commit()

        seleccion = _seleccion_sopa(db_sesion, codigo_a, "CASA")
        res = _marcar(c_a, codigo_a, "CASA", seleccion)
        assert res.status_code == 200, res.text
        assert res.json().get("duelo_finalizado") is None

        # (b) DENTRO de la ventana: finalizada hace 30 s (carrera real) →
        #     adjunta con `gane: false` para J1 y contadores intactos.
        codigo_b = _crear_y_publicar(c_creador, "sopa", PALABRAS_12)
        _duelo_emparejado(c_creador, c_a, c_b, codigo_b)
        fila_b = _fila_de(db_sesion, codigo_b)
        fila_b.jugador1_palabras = 10
        fila_b.jugador2_palabras = 2
        fila_b.estado = "finalizado"
        fila_b.finalizado_en = datetime.now(timezone.utc) - timedelta(seconds=30)
        fila_b.ganador_id = fila_b.jugador2_id
        db_sesion.commit()

        seleccion = _seleccion_sopa(db_sesion, codigo_b, "SOL")
        res = _marcar(c_a, codigo_b, "SOL", seleccion)
        assert res.status_code == 200, res.text
        duelo = res.json()["duelo_finalizado"]
        assert duelo is not None
        assert duelo["yo_palabras"] == 10
        assert duelo["rival_palabras"] == 2
        assert duelo["gane"] is False  # J1 ("Lucas") perdió contra J2

        db_sesion.refresh(fila_b)
        assert fila_b.jugador1_palabras == 10
        assert fila_b.jugador2_palabras == 2
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()


def test_duelo_viejo_no_se_adjunta_a_tercero_no_jugador(client, db_sesion):
    """Un TERCERO registrado que NUNCA fue jugador del duelo marca en la
    partida re-jugable → 200 con `duelo_finalizado: null`. (a) con la fila
    vieja (fuera de ventana): corta la guarda de recencia; (b) con la fila
    RECIENTE (dentro de la ventana, carrera real): corta el filtro
    `_usuario_es_jugador_del_duelo` de `_resultado_duelo` — el tercero jamás
    recibe un resultado de un duelo que no disputó."""
    c_creador, _ = _client_nuevo("c7cr")
    c_a, _ = _client_nuevo("c7a")
    c_b, _ = _client_nuevo("c7b")
    c_tercero, _ = _client_nuevo("c7c")
    try:
        # (a) Fila VIEJA (fuera de ventana) → None por la guarda de recencia.
        codigo_a = _crear_y_publicar(c_creador, "sopa", PALABRAS_12)
        _duelo_emparejado(c_creador, c_a, c_b, codigo_a)
        fila_a = _fila_de(db_sesion, codigo_a)
        fila_a.jugador1_palabras = 10
        fila_a.jugador2_palabras = 2
        fila_a.estado = "finalizado"
        fila_a.finalizado_en = datetime.now(timezone.utc) - timedelta(hours=2)
        fila_a.ganador_id = fila_a.jugador2_id
        db_sesion.commit()

        seleccion = _seleccion_sopa(db_sesion, codigo_a, "CASA")
        res = _marcar(c_tercero, codigo_a, "CASA", seleccion)
        assert res.status_code == 200, res.text
        assert res.json().get("duelo_finalizado") is None

        # (b) Fila RECIENTE (dentro de la ventana) → None por el filtro
        #     `_usuario_es_jugador_del_duelo` dentro del branch de carrera.
        codigo_b = _crear_y_publicar(c_creador, "sopa", PALABRAS_12)
        _duelo_emparejado(c_creador, c_a, c_b, codigo_b)
        fila_b = _fila_de(db_sesion, codigo_b)
        fila_b.jugador1_palabras = 10
        fila_b.jugador2_palabras = 2
        fila_b.estado = "finalizado"
        fila_b.finalizado_en = datetime.now(timezone.utc) - timedelta(seconds=30)
        fila_b.ganador_id = fila_b.jugador2_id
        db_sesion.commit()

        seleccion = _seleccion_sopa(db_sesion, codigo_b, "SOL")
        res = _marcar(c_tercero, codigo_b, "SOL", seleccion)
        assert res.status_code == 200, res.text
        assert res.json().get("duelo_finalizado") is None
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()
        c_tercero.close()


def test_sin_fila_finalizada_no_se_adjunta(client, db_sesion):
    """Partida `activo` SIN ninguna fila `finalizado` (el duelo viejo quedó
    `cancelado`, histórico): jugada válida de un ex-jugador → 200 con
    `duelo_finalizado: null` (rama `finalizada is None` del branch de
    carrera)."""
    c_creador, _ = _client_nuevo("c8cr")
    c_a, _ = _client_nuevo("c8a")
    c_b, _ = _client_nuevo("c8b")
    try:
        codigo = _crear_y_publicar(c_creador, "sopa", PALABRAS_12)
        _duelo_emparejado(c_creador, c_a, c_b, codigo)

        # El duelo quedó CANCELADO (histórico): no existe fila finalizada.
        fila = _fila_de(db_sesion, codigo)
        fila.estado = "cancelado"
        db_sesion.commit()

        seleccion = _seleccion_sopa(db_sesion, codigo, "CASA")
        res = _marcar(c_a, codigo, "CASA", seleccion)
        assert res.status_code == 200, res.text
        assert res.json().get("duelo_finalizado") is None

        db_sesion.refresh(fila)
        assert fila.estado == "cancelado"
        assert fila.jugador1_palabras == 0
        assert fila.jugador2_palabras == 0
    finally:
        c_creador.close()
        c_a.close()
        c_b.close()