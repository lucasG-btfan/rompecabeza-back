"""
Tests del renombrado de partidas (C-15): `PATCH /api/partidas/{codigo}/nombre`.

Contrato (spec `partida-nombre`):
- El creador autenticado puede asignar, modificar o limpiar el nombre de su partida.
- String vacío o whitespace → se normaliza a null (limpia el nombre).
- El nombre se trimea de espacios al guardar.
- max 50 caracteres → 422; campo extra → 422 (extra='forbid').
- Sin sesión → 401; no-creador → 403; partida inexistente → 404;
  partida con creador_id NULL → 403 (no hay propietario).
- La respuesta es `ResumenPartidaResponse` completo (incluye nombre y progreso).

PostgreSQL real (regla dura 4): mismos fixtures y estilo que
`test_vista_publica.py`. Sin mocks de base de datos.
"""

import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.models.partida import Partida


def _registrar(client, prefijo="c15"):
    """Registra un usuario nuevo (cookie de sesión) y devuelve su username."""
    username = f"{prefijo}_{uuid.uuid4().hex[:10]}"
    res = client.post(
        "/api/auth/registro",
        json={"username": username, "password": "clave123"},
    )
    assert res.status_code == 201, res.text
    return username


def _crear_partida(client, tipo="sopa"):
    """Registra un usuario (queda como cookie del creador) y crea la partida."""
    _registrar(client)
    res = client.post(
        "/api/partidas",
        json={
            "tipo": tipo,
            "palabras": [
                {"palabra": "CASA", "explicacion": "Vivienda"},
                {"palabra": "SOL", "explicacion": "Astro"},
            ],
        },
    )
    assert res.status_code == 201, res.text
    return res.json()["codigo"]


def _renombrar(client, codigo, body):
    """Llama PATCH /api/partidas/{codigo}/nombre."""
    return client.patch(f"/api/partidas/{codigo}/nombre", json=body)


# ---------------------------------------------------------------------------
# Happy path — asignar, modificar, limpiar
# ---------------------------------------------------------------------------


def test_asigna_nombre(client):
    """El creador asigna un nombre: 200 + nombre persistido en la respuesta."""
    codigo = _crear_partida(client)

    res = _renombrar(client, codigo, {"nombre": "Tema Navidad"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["nombre"] == "Tema Navidad"
    assert body["codigo"] == codigo
    # El resumen completo viaja: id, progreso, palabras.
    assert body["id"]
    assert body["palabras_total"] == 2
    assert body["palabras_encontradas"] == 0

    # Verificamos persistencia con un GET público (misma cookie de creador).
    res_get = client.get(f"/api/partidas/{codigo}")
    assert res_get.status_code == 200, res_get.text
    assert res_get.json()["nombre"] == "Tema Navidad"


def test_modifica_nombre_existente(client):
    """El creador modifica un nombre ya asignado: 200 + reemplazado."""
    codigo = _crear_partida(client)
    assert _renombrar(client, codigo, {"nombre": "Primero"}).status_code == 200

    res = _renombrar(client, codigo, {"nombre": "Segundo"})
    assert res.status_code == 200, res.text
    assert res.json()["nombre"] == "Segundo"

    res_get = client.get(f"/api/partidas/{codigo}")
    assert res_get.json()["nombre"] == "Segundo"


def test_limpia_nombre_con_null(client):
    """El creador limpia el nombre enviando null: 200 + nombre null."""
    codigo = _crear_partida(client)
    assert _renombrar(client, codigo, {"nombre": "Temporal"}).status_code == 200

    res = _renombrar(client, codigo, {"nombre": None})
    assert res.status_code == 200, res.text
    assert res.json()["nombre"] is None

    res_get = client.get(f"/api/partidas/{codigo}")
    assert res_get.json()["nombre"] is None


# ---------------------------------------------------------------------------
# Normalización — string vacío / whitespace → null, trim de espacios
# ---------------------------------------------------------------------------


def test_string_vacio_normaliza_a_null(client):
    """String vacío: 200 + nombre null (no se persiste string vacío)."""
    codigo = _crear_partida(client)
    assert _renombrar(client, codigo, {"nombre": "Algo"}).status_code == 200

    res = _renombrar(client, codigo, {"nombre": ""})
    assert res.status_code == 200, res.text
    assert res.json()["nombre"] is None


def test_whitespace_normaliza_a_null(client):
    """Whitespace solo: 200 + nombre null (no se persiste espacios)."""
    codigo = _crear_partida(client)
    assert _renombrar(client, codigo, {"nombre": "Algo"}).status_code == 200

    res = _renombrar(client, codigo, {"nombre": "   "})
    assert res.status_code == 200, res.text
    assert res.json()["nombre"] is None


def test_trim_de_espacios(client):
    """Espacios al inicio/fin se trimean: 200 + 'Mi Partida' sin espacios."""
    codigo = _crear_partida(client)

    res = _renombrar(client, codigo, {"nombre": "  Mi Partida  "})
    assert res.status_code == 200, res.text
    assert res.json()["nombre"] == "Mi Partida"

    res_get = client.get(f"/api/partidas/{codigo}")
    assert res_get.json()["nombre"] == "Mi Partida"


# ---------------------------------------------------------------------------
# Límites — 50 chars válido, 51 chars → 422, campo extra → 422
# ---------------------------------------------------------------------------


def test_nombre_50_chars_valido(client):
    """Nombre de exactamente 50 caracteres: 200 y se persiste completo."""
    codigo = _crear_partida(client)
    nombre_largo = "A" * 50

    res = _renombrar(client, codigo, {"nombre": nombre_largo})
    assert res.status_code == 200, res.text
    assert res.json()["nombre"] == nombre_largo


def test_nombre_51_chars_rechazado(client):
    """Nombre de 51 caracteres: 422 (max_length=50 de Pydantic)."""
    codigo = _crear_partida(client)

    res = _renombrar(client, codigo, {"nombre": "A" * 51})
    assert res.status_code == 422, res.text


def test_campo_extra_rechazado(client):
    """Campo no declarado en el body: 422 (extra='forbid', regla dura 5)."""
    codigo = _crear_partida(client)

    res = _renombrar(client, codigo, {"nombre": "X", "otro": 1})
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# RBAC — 401 sin sesión, 403 no-creador, 404 inexistente, 403 creador NULL
# ---------------------------------------------------------------------------


def test_sin_sesion_401(client):
    """Cliente sin cookie: 401 (get_usuario_actual falla antes de llegar)."""
    codigo = _crear_partida(client)

    anon = TestClient(app)
    try:
        res = anon.patch(f"/api/partidas/{codigo}/nombre", json={"nombre": "X"})
        assert res.status_code == 401, res.text
    finally:
        anon.close()


def test_no_creador_403(client):
    """Usuario autenticado que NO es el creador: 403 y el nombre no se modifica."""
    codigo = _crear_partida(client)
    assert _renombrar(client, codigo, {"nombre": "Original"}).status_code == 200

    _registrar(client, prefijo="c15b")  # reemplaza la cookie del creador
    res = _renombrar(client, codigo, {"nombre": "Hackeado"})
    assert res.status_code == 403, res.text

    # El nombre original queda intacto.
    res_get = client.get(f"/api/partidas/{codigo}")
    assert res_get.json()["nombre"] == "Original"


def test_partida_inexistente_404(client):
    """Código que no existe: 404 'Partida no encontrada'.

    Precisa sesión de creador (mismo escenario que el resto del contrato;
    sin sesión el 401 se cubre en `test_sin_sesion_401` con un anon).
    """
    _registrar(client)  # cookie del creador autenticado
    res = _renombrar(client, "NOPE99", {"nombre": "X"})
    assert res.status_code == 404, res.text
    assert "no encontrada" in res.json()["detail"]


def test_creador_id_null_403(client, db_sesion):
    """Partida con creador_id NULL (legacy/invitada): 403, nadie puede renombrar."""
    codigo = _crear_partida(client)

    partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).first()
    partida.creador_id = None
    db_sesion.commit()

    res = _renombrar(client, codigo, {"nombre": "X"})
    assert res.status_code == 403, res.text

    res_get = client.get(f"/api/partidas/{codigo}")
    assert res_get.json()["nombre"] is None