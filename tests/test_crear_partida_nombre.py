"""
Tests del nombre opcional en la creación de partidas (C-16):
`POST /api/partidas` con `nombre` (spec `crear-partida`, R6).

Contrato:
- Crear con `nombre` → 201 con nombre en la respuesta y persistido (GET público).
- Crear sin `nombre` → 201 con nombre null (se persiste null).
- Trim de espacios alrededor del nombre.
- `""` / whitespace → se normaliza a null (no se persiste string vacío).
- 50 chars → 201; 51 chars → 422 (max_length=50).
- Campo no declarado en el body → 422 (extra='forbid', regla dura 5).

PostgreSQL real (regla dura 4): mismos fixtures y estilo que
`test_renombrar_partida.py`. Sin mocks de base de datos.
"""

import uuid

from fastapi.testclient import TestClient

from app.main import app


def _registrar(client, prefijo="c16"):
    """Registra un usuario nuevo (cookie de sesión) y devuelve su username."""
    username = f"{prefijo}_{uuid.uuid4().hex[:10]}"
    res = client.post(
        "/api/auth/registro",
        json={"username": username, "password": "clave123"},
    )
    assert res.status_code == 201, res.text
    return username


def _crear_partida(client, body):
    """Registra un usuario (cookie del creador) y crea la partida con el body dado."""
    _registrar(client)
    res = client.post("/api/partidas", json=body)
    assert res.status_code == 201, res.text
    return res.json()


def _body_minimo(**campos):
    """Body mínimo de POST /partidas; `**campos` agrega/sobrescribe campos."""
    body = {
        "tipo": "sopa",
        "palabras": [
            {"palabra": "CASA", "explicacion": "Vivienda"},
            {"palabra": "SOL", "explicacion": "Astro"},
        ],
    }
    body.update(campos)
    return body


# ---------------------------------------------------------------------------
# Happy path — crear con nombre / sin nombre
# ---------------------------------------------------------------------------


def test_crear_con_nombre(client):
    """Crear con nombre: 201 + nombre en la respuesta y persistido."""
    creada = _crear_partida(client, _body_minimo(nombre="Tema Navidad"))
    assert creada["nombre"] == "Tema Navidad"

    # Persistencia: el GET público (misma cookie de creador) lo confirma.
    res_get = client.get(f"/api/partidas/{creada['codigo']}")
    assert res_get.status_code == 200, res_get.text
    assert res_get.json()["nombre"] == "Tema Navidad"


def test_crear_sin_nombre(client):
    """Crear sin el campo nombre: 201 + nombre null y persistido null."""
    creada = _crear_partida(client, _body_minimo())
    assert creada["nombre"] is None

    res_get = client.get(f"/api/partidas/{creada['codigo']}")
    assert res_get.status_code == 200, res_get.text
    assert res_get.json()["nombre"] is None


# ---------------------------------------------------------------------------
# Normalización — trim y vacío/whitespace → null
# ---------------------------------------------------------------------------


def test_trim_de_espacios_en_nombre(client):
    """Espacios alrededor: 201 + 'Mi Partida' recortado en respuesta y GET."""
    creada = _crear_partida(client, _body_minimo(nombre="  Mi Partida  "))
    assert creada["nombre"] == "Mi Partida"

    res_get = client.get(f"/api/partidas/{creada['codigo']}")
    assert res_get.json()["nombre"] == "Mi Partida"


def test_nombre_vacio_normaliza_a_null(client):
    """nombre vacío: 201 + null persistido (no se persiste string vacío)."""
    creada = _crear_partida(client, _body_minimo(nombre=""))
    assert creada["nombre"] is None

    res_get = client.get(f"/api/partidas/{creada['codigo']}")
    assert res_get.json()["nombre"] is None


def test_nombre_whitespace_normaliza_a_null(client):
    """nombre de solo espacios: 201 + null persistido."""
    creada = _crear_partida(client, _body_minimo(nombre="   "))
    assert creada["nombre"] is None

    res_get = client.get(f"/api/partidas/{creada['codigo']}")
    assert res_get.json()["nombre"] is None


# ---------------------------------------------------------------------------
# Límites y contrato estricto
# ---------------------------------------------------------------------------


def test_nombre_50_chars_valido(client):
    """Nombre de exactamente 50 caracteres: 201 y se persiste completo."""
    _registrar(client)
    nombre_largo = "A" * 50

    res = client.post("/api/partidas", json=_body_minimo(nombre=nombre_largo))
    assert res.status_code == 201, res.text
    assert res.json()["nombre"] == nombre_largo


def test_nombre_51_chars_rechazado(client):
    """Nombre de 51 caracteres: 422 (max_length=50 del schema)."""
    _registrar(client)

    res = client.post("/api/partidas", json=_body_minimo(nombre="A" * 51))
    assert res.status_code == 422, res.text


def test_campo_extra_rechazado(client):
    """Campo no declarado en el body: 422 (extra='forbid', regla dura 5)."""
    _registrar(client)

    res = client.post("/api/partidas", json=_body_minimo(otro=1))
    assert res.status_code == 422, res.text


def test_mis_partidas_incluye_nombre(client):
    """C-16: 'Mis partidas' (GET /partidas) devuelve el nombre persistido.

    C-15 dejó el campo `nombre` en `ResumenPartidaResponse` pero la
    construcción del resumen no lo seteaba: sin esto el front mostraría el
    código en vez del nombre creado (QA 7.7 de C-16).
    """
    creada = _crear_partida(client, _body_minimo(nombre="Tema Navidad"))

    res_lista = client.get("/api/partidas")
    assert res_lista.status_code == 200, res_lista.text
    partida = next(p for p in res_lista.json() if p["codigo"] == creada["codigo"])
    assert partida["nombre"] == "Tema Navidad"


def test_sin_sesion_401(client):
    """Cliente sin cookie: 401 (get_usuario_actual falla antes de crear)."""
    anon = TestClient(app)
    try:
        res = anon.post("/api/partidas", json=_body_minimo(nombre="X"))
        assert res.status_code == 401, res.text
    finally:
        anon.close()