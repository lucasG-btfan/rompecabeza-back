
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.models import Partida


def _registrar(client, prefijo="c09"):
    """Registra un usuario nuevo (cookie de sesión) y devuelve su username."""
    username = f"{prefijo}_{uuid.uuid4().hex[:10]}"
    res = client.post(
        "/api/auth/registro",
        json={"username": username, "password": "clave123"},
    )
    assert res.status_code == 201, res.text
    return username


def _crear_partida(client, tipo, palabras, config=None):
    """Registra un usuario nuevo (cookie de sesión A) y crea una partida."""
    _registrar(client)
    res = client.post(
        "/api/partidas",
        json={"tipo": tipo, "palabras": palabras, "config": config},
    )
    assert res.status_code == 201, res.text
    return res.json()["codigo"]


def _palabra_id(client, codigo, texto):
    """ID de una palabra vía el GET público (sin revelar posiciones)."""
    res = client.get(f"/api/partidas/{codigo}")
    assert res.status_code == 200, res.text
    return next(p["id"] for p in res.json()["palabras"] if p["palabra"] == texto)


def _posicionar(client, codigo, palabra_id, fila, columna, orientacion, **extra):
    body = {"fila": fila, "columna": columna, "orientacion": orientacion, **extra}
    return client.put(
        f"/api/partidas/{codigo}/palabras/{palabra_id}/posicion",
        json=body,
    )


def _crear_crucigrama_pato(client):
    """Partida crucigrama con PATO/ORO/AS (todas con pista)."""
    codigo = _crear_partida(
        client,
        "crucigrama",
        [
            {"palabra": "PATO", "explicacion": "Animal"},
            {"palabra": "ORO", "explicacion": "Metal"},
            {"palabra": "AS", "explicacion": "Carta de la baraja"},
        ],
    )
    return codigo


PALABRAS_CASA_SOL_LUNA = [
    {"palabra": "CASA", "explicacion": "Vivienda"},
    {"palabra": "SOL", "explicacion": "Astro"},
    {"palabra": "LUNA", "explicacion": "Satélite natural"},
]


# --- PUT posicion ----------------------------------------------------------


def test_posicion_h_200_persistida(client, db_sesion):
    codigo = _crear_partida(client, "crucigrama", PALABRAS_CASA_SOL_LUNA)
    casa = _palabra_id(client, codigo, "CASA")

    res = _posicionar(client, codigo, casa, 0, 0, "H")
    assert res.status_code == 200, res.text
    assert res.json()["posicion"] == {"fila": 0, "columna": 0, "orientacion": "H"}

    partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).one()
    casa_db = next(p for p in partida.palabras if p.palabra == "CASA")
    assert casa_db.posicion == {"fila": 0, "columna": 0, "orientacion": "H"}


def test_posicion_v_200(client):
    codigo = _crear_partida(client, "crucigrama", PALABRAS_CASA_SOL_LUNA)
    casa = _palabra_id(client, codigo, "CASA")

    res = _posicionar(client, codigo, casa, 1, 2, "V")
    assert res.status_code == 200, res.text
    assert res.json()["posicion"] == {"fila": 1, "columna": 2, "orientacion": "V"}


def test_posicion_orientacion_invalida_400(client):
    codigo = _crear_partida(client, "crucigrama", PALABRAS_CASA_SOL_LUNA)
    casa = _palabra_id(client, codigo, "CASA")

    for orientacion in ("E", "SE", "O"):
        res = _posicionar(client, codigo, casa, 0, 0, orientacion)
        assert res.status_code == 400, res.text
        assert "H o V" in res.json()["detail"]


def test_posicion_no_creador_403_y_sin_sesion_401(client):
    codigo = _crear_partida(client, "crucigrama", PALABRAS_CASA_SOL_LUNA)
    casa = _palabra_id(client, codigo, "CASA")

    # Sin sesión: un client nuevo sin cookie -> 401 (auth primero)
    anon = TestClient(app)
    res = anon.put(
        f"/api/partidas/{codigo}/palabras/{casa}/posicion",
        json={"fila": 0, "columna": 0, "orientacion": "H"},
    )
    assert res.status_code == 401
    anon.close()

    # No creador: usuario B reemplaza la cookie -> 403
    _registrar(client, prefijo="c09b")
    res = _posicionar(client, codigo, casa, 0, 0, "H")
    assert res.status_code == 403, res.text


def test_posicion_estado_no_creando_400(client):
    codigo = _crear_partida(client, "crucigrama", PALABRAS_CASA_SOL_LUNA)
    casa = _palabra_id(client, codigo, "CASA")
    sol = _palabra_id(client, codigo, "SOL")
    luna = _palabra_id(client, codigo, "LUNA")

    assert _posicionar(client, codigo, casa, 0, 0, "H").status_code == 200
    assert _posicionar(client, codigo, sol, 0, 2, "V").status_code == 200
    assert _posicionar(client, codigo, luna, 2, 2, "H").status_code == 200
    assert client.post(f"/api/partidas/{codigo}/finalizar").status_code == 200

    res = _posicionar(client, codigo, casa, 1, 0, "H")
    assert res.status_code == 400, res.text
    assert "solo se pueden posicionar palabras durante la creación" in res.json()["detail"].lower()


def test_posicion_campo_extra_422(client):
    codigo = _crear_partida(client, "crucigrama", PALABRAS_CASA_SOL_LUNA)
    casa = _palabra_id(client, codigo, "CASA")

    res = _posicionar(client, codigo, casa, 0, 0, "H", extra="no declarado")
    assert res.status_code == 422, res.text


def test_posicion_cruce_letra_distinta_400(client):
    codigo = _crear_partida(client, "crucigrama", PALABRAS_CASA_SOL_LUNA)
    casa = _palabra_id(client, codigo, "CASA")
    sol = _palabra_id(client, codigo, "SOL")

    assert _posicionar(client, codigo, casa, 0, 0, "H").status_code == 200
    res = _posicionar(client, codigo, sol, 0, 1, "V")  # (0,1) es 'A', SOL pondría 'S'
    assert res.status_code == 400, res.text
    assert "no coincide" in res.json()["detail"]


def test_posicion_solapamiento_paralelo_400(client):
    codigo = _crear_partida(client, "crucigrama", PALABRAS_CASA_SOL_LUNA)
    casa = _palabra_id(client, codigo, "CASA")
    sol = _palabra_id(client, codigo, "SOL")

    assert _posicionar(client, codigo, casa, 0, 0, "H").status_code == 200
    res = _posicionar(client, codigo, sol, 0, 0, "H")
    assert res.status_code == 400, res.text
    assert "paralela" in res.json()["detail"]


def test_posicion_palabra_fantasma_400(client):
    codigo = _crear_partida(
        client,
        "crucigrama",
        [
            {"palabra": "CARTA", "explicacion": "Mensaje escrito"},
            {"palabra": "ALTO", "explicacion": "Gran altura"},
            {"palabra": "PLATO", "explicacion": "Vajilla"},
        ],
    )
    carta = _palabra_id(client, codigo, "CARTA")
    alto = _palabra_id(client, codigo, "ALTO")
    plato = _palabra_id(client, codigo, "PLATO")

    assert _posicionar(client, codigo, carta, 0, 0, "H").status_code == 200
    assert _posicionar(client, codigo, alto, 0, 1, "V").status_code == 200
    res = _posicionar(client, codigo, plato, 1, 0, "H")  # paralela-adyacente
    assert res.status_code == 400, res.text
    assert "fantasma" in res.json()["detail"].lower()


# REVISIÓN 9.x (opción C): una palabra posterior sin cruce queda como
# componente separado -> 200 y la posición se persiste.
def test_posicion_posterior_sin_cruce_200(client, db_sesion):
    codigo = _crear_partida(client, "crucigrama", PALABRAS_CASA_SOL_LUNA)
    casa = _palabra_id(client, codigo, "CASA")
    sol = _palabra_id(client, codigo, "SOL")

    assert _posicionar(client, codigo, casa, 0, 0, "H").status_code == 200
    res = _posicionar(client, codigo, sol, 5, 5, "V")
    assert res.status_code == 200, res.text
    assert res.json()["posicion"] == {"fila": 5, "columna": 5, "orientacion": "V"}

    partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).one()
    sol_db = next(p for p in partida.palabras if p.palabra == "SOL")
    assert sol_db.posicion == {"fila": 5, "columna": 5, "orientacion": "V"}


# REVISIÓN 9.x (opción C): la movida que deja el puzzle desconectado es
# aceptada -> 200 y la posición movida se persiste (D4 REVISADO). NOTA: la
# posición original del caso (2,0) quedó inválida por la anti-fantasma (la A
# de PATO en (2,1) genera "SA" vertical con la S de AS en (1,1)); se usa
# (4,0), limpia, que deja a AS y ORO sin cruce.
def test_movida_que_desconecta_200_y_posicion_persistida(client, db_sesion):
    codigo = _crear_crucigrama_pato(client)
    pato = _palabra_id(client, codigo, "PATO")
    oro = _palabra_id(client, codigo, "ORO")
    as_ = _palabra_id(client, codigo, "AS")

    assert _posicionar(client, codigo, pato, 0, 0, "H").status_code == 200
    assert _posicionar(client, codigo, oro, 0, 3, "V").status_code == 200
    assert _posicionar(client, codigo, as_, 0, 1, "V").status_code == 200

    res = _posicionar(client, codigo, pato, 4, 0, "H")
    assert res.status_code == 200, res.text
    assert res.json()["posicion"] == {"fila": 4, "columna": 0, "orientacion": "H"}

    partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).one()
    pato_db = next(p for p in partida.palabras if p.palabra == "PATO")
    assert pato_db.posicion == {"fila": 4, "columna": 0, "orientacion": "H"}


# REVISIÓN 9.x (D1 REVISADO): el editor manual acepta coordenadas negativas
# (la palabra se extiende hacia filas/columnas negativas del origen actual).
def test_posicion_coordenadas_negativas_200(client, db_sesion):
    codigo = _crear_partida(client, "crucigrama", PALABRAS_CASA_SOL_LUNA)
    casa = _palabra_id(client, codigo, "CASA")
    sol = _palabra_id(client, codigo, "SOL")

    assert _posicionar(client, codigo, casa, 0, 0, "H").status_code == 200
    # Palabra aislada en fila negativa (componente separado): el PUT acepta la
    # coordenada -2 y la persiste tal cual; la normalización a (0,0) ocurre en
    # `finalizar` (construir_grilla traslada al bounding box mínimo).
    res = _posicionar(client, codigo, sol, -2, 9, "V")
    assert res.status_code == 200, res.text
    assert res.json()["posicion"] == {"fila": -2, "columna": 9, "orientacion": "V"}

    partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).one()
    sol_db = next(p for p in partida.palabras if p.palabra == "SOL")
    assert sol_db.posicion == {"fila": -2, "columna": 9, "orientacion": "V"}


def _quitar_posicion(client, codigo, palabra_id):
    return client.delete(f"/api/partidas/{codigo}/palabras/{palabra_id}/posicion")


def test_quitar_posicion_200_persistida(client, db_sesion):
    codigo = _crear_partida(client, "crucigrama", PALABRAS_CASA_SOL_LUNA)
    casa = _palabra_id(client, codigo, "CASA")
    casa_uuid = casa  # es el id en formato string del GET público

    assert _posicionar(client, codigo, casa, 0, 0, "H").status_code == 200

    res = _quitar_posicion(client, codigo, casa)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["id"] == casa_uuid  # misma shape de PalabraResponse que el PUT
    assert body["posicion"] is None
    assert body["encontrada"] is False

    partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).one()
    casa_db = next(p for p in partida.palabras if p.palabra == "CASA")
    assert casa_db.posicion is None


def test_quitar_posicion_no_creador_403_y_sin_sesion_401(client):
    codigo = _crear_partida(client, "crucigrama", PALABRAS_CASA_SOL_LUNA)
    casa = _palabra_id(client, codigo, "CASA")

    # Sin sesión: un client nuevo sin cookie -> 401 (auth primero)
    anon = TestClient(app)
    res = anon.delete(f"/api/partidas/{codigo}/palabras/{casa}/posicion")
    assert res.status_code == 401, res.text
    anon.close()

    # Otro usuario logueado -> 403 (solo el creador puede editar el layout).
    _registrar(client, prefijo="c11b")
    res = _quitar_posicion(client, codigo, casa)
    assert res.status_code == 403, res.text


def test_quitar_posicion_estado_no_creando_400(client):
    codigo = _crear_partida(client, "crucigrama", PALABRAS_CASA_SOL_LUNA)
    casa = _palabra_id(client, codigo, "CASA")
    sol = _palabra_id(client, codigo, "SOL")
    luna = _palabra_id(client, codigo, "LUNA")

    # Layout manual completo -> finalizar 200 -> estado 'activo' (mismo patrón
    # que test_posicion_estado_no_creando_400).
    assert _posicionar(client, codigo, casa, 0, 0, "H").status_code == 200
    assert _posicionar(client, codigo, sol, 0, 2, "V").status_code == 200
    assert _posicionar(client, codigo, luna, 2, 2, "H").status_code == 200
    assert client.post(f"/api/partidas/{codigo}/finalizar").status_code == 200

    res = _quitar_posicion(client, codigo, casa)
    assert res.status_code == 400, res.text
    assert "solo se pueden quitar posiciones durante la creación" in res.json()["detail"].lower()


def test_quitar_posicion_palabra_inexistente_404(client):
    codigo = _crear_partida(client, "crucigrama", PALABRAS_CASA_SOL_LUNA)

    res = _quitar_posicion(client, codigo, str(uuid.uuid4()))
    assert res.status_code == 404, res.text


# --- GET /editor ------------------------------------------------------------


def test_get_editor_200_creador_con_posiciones(client, db_sesion):
    codigo = _crear_partida(client, "crucigrama", PALABRAS_CASA_SOL_LUNA)
    casa = _palabra_id(client, codigo, "CASA")

    assert _posicionar(client, codigo, casa, 0, 0, "H").status_code == 200

    res = client.get(f"/api/partidas/{codigo}/editor")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["codigo"] == codigo
    assert body["tipo"] == "crucigrama"
    assert body["estado"] == "creando"

    por_texto = {p["palabra"]: p for p in body["palabras"]}
    assert por_texto["CASA"]["posicion"] == {
        "fila": 0,
        "columna": 0,
        "orientacion": "H",
    }
    assert por_texto["SOL"]["posicion"] is None
    assert por_texto["LUNA"]["posicion"] is None


def test_get_editor_incluye_nombre(client):

    codigo = _crear_partida(client, "crucigrama", PALABRAS_CASA_SOL_LUNA)

    res = client.patch(f"/api/partidas/{codigo}/nombre", json={"nombre": "Tema Navidad"})
    assert res.status_code == 200, res.text

    res = client.get(f"/api/partidas/{codigo}/editor")
    assert res.status_code == 200, res.text
    assert res.json()["nombre"] == "Tema Navidad"


def test_get_editor_no_creador_403(client):
    codigo = _crear_partida(client, "crucigrama", PALABRAS_CASA_SOL_LUNA)

    _registrar(client, prefijo="c09c")
    res = client.get(f"/api/partidas/{codigo}/editor")
    assert res.status_code == 403, res.text


# --- POST /finalizar híbrido -------------------------------------------------


def test_finalizar_layout_manual_completo_200(client, db_sesion):
    codigo = _crear_partida(client, "crucigrama", PALABRAS_CASA_SOL_LUNA)
    casa = _palabra_id(client, codigo, "CASA")
    sol = _palabra_id(client, codigo, "SOL")
    luna = _palabra_id(client, codigo, "LUNA")

    assert _posicionar(client, codigo, casa, 0, 0, "H").status_code == 200
    assert _posicionar(client, codigo, sol, 0, 2, "V").status_code == 200
    assert _posicionar(client, codigo, luna, 2, 2, "H").status_code == 200

    res = client.post(f"/api/partidas/{codigo}/finalizar")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["estado"] == "activo"
    assert body["filas"] == 3
    assert body["columnas"] == 6

    grilla = body["grilla"]
    assert set(grilla.keys()) == {"celdas", "palabras"}
    assert len(grilla["celdas"]) == 3 * 6
    assert {w["texto"] for w in grilla["palabras"]} == {"CASA", "SOL", "LUNA"}
    assert sorted(w["numero"] for w in grilla["palabras"]) == [1, 2, 3]

    partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).one()
    assert partida.estado == "activo"
    assert set(partida.grilla.keys()) == {"celdas", "palabras"}
    pos = {p.palabra: p.posicion for p in partida.palabras}
    assert pos["CASA"] == {
        "fila": 0,
        "columna": 0,
        "orientacion": "H",
        "numero": 1,
    }
    assert pos["SOL"] == {
        "fila": 0,
        "columna": 2,
        "orientacion": "V",
        "numero": 2,
    }
    assert pos["LUNA"] == {
        "fila": 2,
        "columna": 2,
        "orientacion": "H",
        "numero": 3,
    }


def test_finalizar_layout_parcial_400(client):
    codigo = _crear_partida(client, "crucigrama", PALABRAS_CASA_SOL_LUNA)
    casa = _palabra_id(client, codigo, "CASA")

    assert _posicionar(client, codigo, casa, 0, 0, "H").status_code == 200

    res = client.post(f"/api/partidas/{codigo}/finalizar")
    assert res.status_code == 400, res.text
    assert "todas o ninguna" in res.json()["detail"].lower()


def test_finalizar_sin_posiciones_generacion_automatica(client, db_sesion):
    
    codigo = _crear_partida(client, "crucigrama", PALABRAS_CASA_SOL_LUNA)

    res = client.post(f"/api/partidas/{codigo}/finalizar")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["estado"] == "activo"
    assert body["filas"] > 0 and body["columnas"] > 0
    assert set(body["grilla"].keys()) == {"celdas", "palabras"}

    partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).one()
    for p in partida.palabras:
        assert set(p.posicion.keys()) <= {"fila", "columna", "orientacion", "numero"}


def test_sopa_posicion_sigue_funcionando_200(client):
    # Regresión: la rama sopa de PUT posicion no cambió (8 direcciones).
    codigo = _crear_partida(
        client,
        "sopa",
        [
            {"palabra": "CASA", "explicacion": "Vivienda"},
            {"palabra": "SOL", "explicacion": "Astro"},
        ],
    )
    casa = _palabra_id(client, codigo, "CASA")

    res = _posicionar(client, codigo, casa, 0, 0, "SE")
    assert res.status_code == 200, res.text
    assert res.json()["posicion"] == {"fila": 0, "columna": 0, "orientacion": "SE"}


# REVISIÓN 9.x (D1 REVISADO): la relajación de `ge=0` es SOLO para crucigrama.
# La sopa no tiene otra cota de límites: coordenadas negativas -> 400 explícito.
def test_sopa_posicion_coordenadas_negativas_400(client):
    codigo = _crear_partida(
        client,
        "sopa",
        [
            {"palabra": "CASA", "explicacion": "Vivienda"},
            {"palabra": "SOL", "explicacion": "Astro"},
        ],
    )
    casa = _palabra_id(client, codigo, "CASA")

    res = _posicionar(client, codigo, casa, -1, 0, "E")
    assert res.status_code == 400, res.text
    assert "no negativas" in res.json()["detail"].lower()

    res = _posicionar(client, codigo, casa, 0, -3, "O")
    assert res.status_code == 400, res.text
    assert "no negativas" in res.json()["detail"].lower()