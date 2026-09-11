import uuid

from app.models import Palabra, Partida


def _registrar_y_crear_partida(client, tipo, palabras, config=None):
    """Registra un usuario nuevo (cookie de sesión) y crea una partida."""
    username = f"c08_{uuid.uuid4().hex[:10]}"
    res = client.post(
        "/api/auth/registro",
        json={"username": username, "password": "clave123"},
    )
    assert res.status_code == 201, res.text
    res = client.post(
        "/api/partidas",
        json={"tipo": tipo, "palabras": palabras, "config": config},
    )
    assert res.status_code == 201, res.text
    return res.json()["codigo"]


def _finalizar(client, codigo, body=None):
    if body is None:
        return client.post(f"/api/partidas/{codigo}/finalizar")
    return client.post(f"/api/partidas/{codigo}/finalizar", json=body)


PALABRAS_HAPPY = [
    {"palabra": "CASA", "explicacion": "Vivienda"},
    {"palabra": "SOL", "explicacion": "Astro que ilumina"},
    {"palabra": "LUNA", "explicacion": "Satélite natural"},
]


def test_crucigrama_finalizar_happy_path_200(client, db_sesion):
    codigo = _registrar_y_crear_partida(client, "crucigrama", PALABRAS_HAPPY)

    res = _finalizar(client, codigo)
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["estado"] == "activo"
    assert body["filas"] > 0
    assert body["columnas"] > 0

    grilla = body["grilla"]
    assert grilla is not None
    assert len(grilla["celdas"]) == body["filas"] * body["columnas"]
    assert all(c["tipo"] in {"letra", "negra"} for c in grilla["celdas"])
    assert all(
        (c["tipo"] == "letra" and c["letra"]) or (c["tipo"] == "negra" and c["letra"] is None)
        for c in grilla["celdas"]
    )

    palabras = grilla["palabras"]
    assert {w["texto"] for w in palabras} == {"CASA", "SOL", "LUNA"}
    assert all(w["orientacion"] in {"H", "V"} for w in palabras)
    assert sorted(w["numero"] for w in palabras) == [1, 2, 3]

    partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).one()
    assert partida.tipo == "crucigrama"
    assert partida.estado == "activo"
    assert set(partida.grilla.keys()) == {"celdas", "palabras"}

    for p in partida.palabras:
        assert set(p.posicion.keys()) <= {"fila", "columna", "orientacion", "numero"}
        assert p.posicion["orientacion"] in {"H", "V"}
        assert p.posicion["numero"] in {1, 2, 3}


def test_crucigrama_400_sin_pistas(client):
    palabras = [{"palabra": "CASA", "explicacion": None}, {"palabra": "SOL", "explicacion": "Astro"}]
    codigo = _registrar_y_crear_partida(client, "crucigrama", palabras)

    res = _finalizar(client, codigo)
    assert res.status_code == 400, res.text
    detalle = res.json()["detail"]
    assert "Las palabras deben tener una pista (explicación)" in detalle
    assert "CASA" in detalle


def test_crucigrama_400_menos_de_dos_palabras(client):
    codigo = _registrar_y_crear_partida(
        client,
        "crucigrama",
        [{"palabra": "CASA", "explicacion": "Vivienda"}],
    )

    res = _finalizar(client, codigo)
    assert res.status_code == 400, res.text
    assert "al menos dos palabras" in res.json()["detail"]


def test_crucigrama_400_sin_cruces_posibles(client):
    codigo = _registrar_y_crear_partida(
        client,
        "crucigrama",
        [
            {"palabra": "REY", "explicacion": "Monarca"},
            {"palabra": "SOL", "explicacion": "Astro"},
        ],
    )

    res = _finalizar(client, codigo)
    assert res.status_code == 400, res.text
    assert "No se pudo generar un crucigrama" in res.json()["detail"]


def test_crucigrama_422_campo_no_declarado(client):
    codigo = _registrar_y_crear_partida(client, "crucigrama", PALABRAS_HAPPY)

    res = _finalizar(client, codigo, body={"tipo": "crucigrama"})
    assert res.status_code == 422, res.text


def test_crucigrama_ediciones_rechazadas(client):
    codigo = _registrar_y_crear_partida(client, "crucigrama", PALABRAS_HAPPY)
    assert _finalizar(client, codigo).status_code == 200

    res = client.put(
        f"/api/partidas/{codigo}/ediciones",
        json={"fila": 0, "columna": 0, "letra": "X"},
    )
    assert res.status_code == 400, res.text
    assert "solo está disponible para sopa de letras" in res.json()["detail"]


def test_sopa_ediciones_siguen_funcionando(client):
    codigo = _registrar_y_crear_partida(
        client,
        "sopa",
        [{"palabra": "CASA", "explicacion": "Vivienda"}, {"palabra": "SOL", "explicacion": "Astro"}],
    )

    res = _finalizar(client, codigo)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["codigo"] == codigo
    assert body["estado"] == "activo"
    assert body["filas"] > 0
    assert body["columnas"] > 0

    res = client.get(f"/api/partidas/{codigo}/estado")
    assert res.status_code == 200, res.text
    grilla = res.json()["grilla"]
    assert isinstance(grilla, list)
    assert isinstance(grilla[0], list)
    assert all(isinstance(celda, str) for fila in grilla for celda in fila)

    res = client.put(
        f"/api/partidas/{codigo}/ediciones",
        json={"fila": 0, "columna": 0, "letra": "Z"},
    )
    assert res.status_code == 200, res.text

    res = client.get(f"/api/partidas/{codigo}/estado")
    assert res.status_code == 200, res.text
    assert res.json()["grilla"][0][0] == "Z"


def test_obtener_estado_crucigrama_200(client):
    codigo = _registrar_y_crear_partida(client, "crucigrama", PALABRAS_HAPPY)
    assert _finalizar(client, codigo).status_code == 200

    res = client.get(f"/api/partidas/{codigo}/estado")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["tipo"] == "crucigrama"
    assert body["estado"] == "activo"
    assert set(body["grilla"].keys()) == {"celdas", "palabras"}


def test_crucigrama_requiere_creador_logueado(client):
    res = client.post("/api/partidas", json={"tipo": "crucigrama", "palabras": PALABRAS_HAPPY})
    assert res.status_code == 401