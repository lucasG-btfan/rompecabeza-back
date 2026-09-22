import uuid

from fastapi.testclient import TestClient

from app.main import app


def _registrar(client, prefijo="c12"):
    """Registra un usuario nuevo (cookie de sesión) y devuelve su username."""
    username = f"{prefijo}_{uuid.uuid4().hex[:10]}"
    res = client.post(
        "/api/auth/registro",
        json={"username": username, "password": "clave123"},
    )
    assert res.status_code == 201, res.text
    return username


def _crear_partida(client, tipo, palabras):
    """Registra un usuario (queda como cookie del creador) y crea la partida."""
    _registrar(client, prefijo=f"c12_{tipo[:3]}")
    res = client.post(
        "/api/partidas",
        json={"tipo": tipo, "palabras": palabras},
    )
    assert res.status_code == 201, res.text
    return res.json()["codigo"]


def _palabra_id(client, codigo, texto):
    """ID de una palabra vía el GET público CON la cookie del creador (ve la
    solución; el no-creador la recibe null y no puede matchear por texto)."""
    res = client.get(f"/api/partidas/{codigo}")
    assert res.status_code == 200, res.text
    return next(p["id"] for p in res.json()["palabras"] if p["palabra"] == texto)


def _posicionar(client, codigo, palabra_id, fila, columna, orientacion):
    return client.put(
        f"/api/partidas/{codigo}/palabras/{palabra_id}/posicion",
        json={"fila": fila, "columna": columna, "orientacion": orientacion},
    )


def _finalizar(client, codigo):
    return client.post(f"/api/partidas/{codigo}/finalizar")


def _crucigrama_pato(client):
    codigo = _crear_partida(
        client,
        "crucigrama",
        [
            {"palabra": "PATO", "explicacion": "Animal de granja"},
            {"palabra": "ORO", "explicacion": "Metal precioso"},
            {"palabra": "AS", "explicacion": "Carta de la baraja"},
        ],
    )
    pato = _palabra_id(client, codigo, "PATO")
    oro = _palabra_id(client, codigo, "ORO")
    as_ = _palabra_id(client, codigo, "AS")
    assert _posicionar(client, codigo, pato, 0, 0, "H").status_code == 200
    assert _posicionar(client, codigo, oro, 0, 3, "V").status_code == 200
    assert _posicionar(client, codigo, as_, 0, 1, "V").status_code == 200
    assert _finalizar(client, codigo).status_code == 200
    return codigo


def _sopa(client):
    codigo = _crear_partida(
        client,
        "sopa",
        [
            {"palabra": "CASA", "explicacion": "Vivienda"},
            {"palabra": "SOL", "explicacion": "Astro"},
        ],
    )
    assert _finalizar(client, codigo).status_code == 200
    return codigo


# ---------------------------------------------------------------------------
# Crucigrama: la solución se oculta a no-creadores (spec partidas-vista-publica)
# ---------------------------------------------------------------------------


def test_publica_crucigrama_creador_ve_solucion(client):
    """El creador autenticado ve palabra/texto_mostrar y recibe es_creador true."""
    codigo = _crucigrama_pato(client)  # la cookie del TestClient es del creador

    res = client.get(f"/api/partidas/{codigo}")
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["es_creador"] is True
    por_texto = {p["palabra"]: p for p in body["palabras"]}
    assert set(por_texto) == {"PATO", "ORO", "AS"}
    assert por_texto["PATO"]["texto_mostrar"] == "PATO"
    assert all(p["posicion"] is None for p in body["palabras"])


def test_publica_crucigrama_no_creador_oculta_solucion(client):
    """Jugador registrado que no es creador: palabra/texto_mostrar null,
    pista (explicacion) visible, posicion oculta, es_creador false."""
    codigo = _crucigrama_pato(client)
    _registrar(client, prefijo="c12b")  # reemplaza la cookie del creador

    res = client.get(f"/api/partidas/{codigo}")
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["es_creador"] is False
    assert len(body["palabras"]) == 3
    for p in body["palabras"]:
        assert p["palabra"] is None
        assert p["texto_mostrar"] is None
        assert isinstance(p["explicacion"], str) and len(p["explicacion"]) > 0
        assert p["posicion"] is None


def test_publica_crucigrama_invitado_anonimo_oculta_solucion(client):
    """Invitado sin sesión: mismas reglas que el no-creador + es_creador false."""
    codigo = _crucigrama_pato(client)

    anon = TestClient(app)
    try:
        res = anon.get(f"/api/partidas/{codigo}")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["es_creador"] is False
        assert all(p["palabra"] is None for p in body["palabras"])
        assert all(p["texto_mostrar"] is None for p in body["palabras"])
        assert all(p["posicion"] is None for p in body["palabras"])
    finally:
        anon.close()



def test_publica_sopa_creador_ve_palabras_y_es_creador(client):
    """En sopa el creador ve la lista (la lista ES el juego) y es_creador true."""
    codigo = _sopa(client)

    res = client.get(f"/api/partidas/{codigo}")
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["es_creador"] is True
    textos = {p["palabra"] for p in body["palabras"]}
    assert textos == {"CASA", "SOL"}
    assert all(p["texto_mostrar"] is not None for p in body["palabras"])


def test_publica_sopa_invitado_ve_palabras_y_es_creador_false(client):
    
    codigo = _sopa(client)

    anon = TestClient(app)
    try:
        res = anon.get(f"/api/partidas/{codigo}")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["es_creador"] is False
        textos = {p["palabra"] for p in body["palabras"]}
        assert textos == {"CASA", "SOL"}
        assert all(p["texto_mostrar"] is not None for p in body["palabras"])
        assert all(p["posicion"] is None for p in body["palabras"])
    finally:
        anon.close()


# ---------------------------------------------------------------------------
# 404 y contrato
# ---------------------------------------------------------------------------


def test_publica_404_codigo_inexistente(client):
    """El endpoint público sigue devolviendo 404 ante un código inexistente."""
    res = client.get("/api/partidas/NOPE99")
    assert res.status_code == 404, res.text
    assert "no encontrada" in res.json()["detail"]