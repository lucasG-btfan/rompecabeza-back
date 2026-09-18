"""
Contrato de progreso EFÍMERO (C-14): el backend deja de persistir progreso
de juego para TODOS los roles (registrado o invitado), en sopa y crucigrama.

- `marcar_encontrada` / `responder_palabra` validan y responden 200 con
  `{encontrada: true, posicion}` SIN crear participación de jugador ni
  hallazgos (D2).
- `unirse_partida` responde el `modo` sin crear participación ni devolver
  `iniciado_en` (D1/D3).
- `obtener_estado` devuelve siempre `encontrada: false` / `posicion: null`
  y la grilla del crucigrama con todas las letras ocultas (D4/D5).

PostgreSQL real (regla dura): mismos fixtures y estilo que
`test_crucigrama_juego.py`. La grilla del ejemplo PATO/ORO/AS usa el layout
manual determinista de C-09 (ver docstring de test_crucigrama_juego.py).
"""

import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.models import Partida
from app.models.hallazgo import Hallazgo
from app.services.sopa_generator import calcular_celda_final


def _registrar(client, prefijo="c14"):
    """Registra un usuario nuevo (cookie de sesión) y devuelve su username."""
    username = f"{prefijo}_{uuid.uuid4().hex[:10]}"
    res = client.post(
        "/api/auth/registro",
        json={"username": username, "password": "clave123"},
    )
    assert res.status_code == 201, res.text
    return username


def _crear_partida(client, tipo, palabras):
    """Registra un usuario nuevo (cookie de sesión) y crea una partida."""
    _registrar(client)
    res = client.post(
        "/api/partidas",
        json={"tipo": tipo, "palabras": palabras},
    )
    assert res.status_code == 201, res.text
    return res.json()["codigo"]


def _palabra_id(client, codigo, texto):
    """ID de una palabra vía el GET público (sin revelar posiciones)."""
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
    """Partida crucigrama PATO/ORO/AS finalizada con layout manual determinista."""
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


def _sopa_casa_sol(client):
    """Partida sopa finalizada (CASA/SOL) con posiciones generadas."""
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


def _seleccion_sopa(db_sesion, codigo, texto):
    """Lee la posición real de una palabra de la sopa (DB real) y arma la
    selección directa `{fila_inicio..columna_fin}` para el endpoint."""
    partida_db = db_sesion.query(Partida).filter(Partida.codigo == codigo).one()
    palabra_db = next(p for p in partida_db.palabras if p.palabra == texto)
    pos = palabra_db.posicion
    fila_fin, col_fin = calcular_celda_final(
        pos["fila"], pos["columna"], pos["orientacion"], len(palabra_db.palabra)
    )
    return {
        "fila_inicio": pos["fila"],
        "columna_inicio": pos["columna"],
        "fila_fin": fila_fin,
        "columna_fin": col_fin,
    }, pos


def _respuesta(client, codigo, palabra_id, letras):
    return client.put(
        f"/api/partidas/{codigo}/palabras/{palabra_id}/respuesta",
        json={"letras": letras},
    )


# ---------------------------------------------------------------------------
# Marcar en la sopa — sin persistencia para registrado (D2)
# ---------------------------------------------------------------------------


def test_marcar_sopa_registrado_no_persiste(client, db_sesion):
    """Marcar CASA con la selección correcta como registrado: 200 + posicion,
    sin participación de jugador ni filas en `hallazgos` (C-14)."""
    codigo = _sopa_casa_sol(client)
    casa = _palabra_id(client, codigo, "CASA")
    _registrar(client, prefijo="c14b")  # jugador B

    seleccion, pos = _seleccion_sopa(db_sesion, codigo, "CASA")
    res = client.put(
        f"/api/partidas/{codigo}/palabras/{casa}/encontrada",
        json=seleccion,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["encontrada"] is True
    assert body["posicion"] == pos

    partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).one()
    assert len(partida.participaciones) == 1  # solo el creador
    assert partida.participaciones[0].rol == "creador"
    assert partida.participaciones[0].hallazgos == []
    assert db_sesion.query(Hallazgo).count() == 0


def test_marcar_sopa_seleccion_incorrecta_400(client, db_sesion):
    """Selección que no coincide con la palabra → 400 sin efectos en la DB."""
    codigo = _sopa_casa_sol(client)
    casa = _palabra_id(client, codigo, "CASA")
    _registrar(client, prefijo="c14f")

    seleccion, pos = _seleccion_sopa(db_sesion, codigo, "CASA")
    # Desplazo la celda final REAL una fila: nunca coincide (ni directo ni
    # invertido), aunque la palabra quede horizontal (fila_fin == fila).
    res = client.put(
        f"/api/partidas/{codigo}/palabras/{casa}/encontrada",
        json={**seleccion, "fila_fin": seleccion["fila_fin"] + 1},
    )
    assert res.status_code == 400, res.text
    assert "Selección incorrecta" in res.json()["detail"]

    partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).one()
    assert len(partida.participaciones) == 1  # solo el creador
    assert db_sesion.query(Hallazgo).count() == 0


def test_marcar_sopa_campo_extra_422(client):
    """Campo no declarado en el request de marcado → 422 (extra='forbid')."""
    codigo = _sopa_casa_sol(client)
    casa = _palabra_id(client, codigo, "CASA")

    res = client.put(
        f"/api/partidas/{codigo}/palabras/{casa}/encontrada",
        json={"fila_inicio": 0, "columna_inicio": 0, "fila_fin": 0, "columna_fin": 3, "extra": 1},
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# Responder en el crucigrama — sin persistencia para registrado (D2)
# ---------------------------------------------------------------------------


def test_responder_crucigrama_registrado_no_persiste(client, db_sesion):
    """Responder PATO y ORO como registrado: 200 sin participación de jugador
    ni hallazgos (C-14)."""
    codigo = _crucigrama_pato(client)
    pato = _palabra_id(client, codigo, "PATO")
    oro = _palabra_id(client, codigo, "ORO")
    _registrar(client, prefijo="c14c")

    assert _respuesta(client, codigo, pato, "PATO").status_code == 200
    res = _respuesta(client, codigo, oro, "ORO")
    assert res.status_code == 200, res.text
    assert res.json()["encontrada"] is True

    partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).one()
    assert len(partida.participaciones) == 1  # solo el creador
    assert partida.participaciones[0].rol == "creador"
    assert partida.participaciones[0].hallazgos == []
    assert db_sesion.query(Hallazgo).count() == 0


def test_estado_siempre_vacio_tras_jugar(client):
    """Tras responder correctamente, GET /estado sigue vacío: todas las
    palabras con `encontrada: false` / `posicion: null` y la grilla del
    crucigrama con todas las letras `null` (C-14)."""
    codigo = _crucigrama_pato(client)
    pato = _palabra_id(client, codigo, "PATO")
    _registrar(client, prefijo="c14d")

    assert _respuesta(client, codigo, pato, "PATO").status_code == 200

    res = client.get(f"/api/partidas/{codigo}/estado")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["tipo"] == "crucigrama"
    assert all(
        p["encontrada"] is False and p["posicion"] is None for p in body["palabras"]
    )
    # Grilla ciega: ninguna celda de letra revela su letra.
    assert all(c["letra"] is None for c in body["grilla"]["celdas"])


# ---------------------------------------------------------------------------
# Unirse — handshake sin participación (D1/D3)
# ---------------------------------------------------------------------------


def test_unirse_no_crea_participacion(client, db_sesion):
    """POST /unirse como registrado: 200 `{modo: "registrado"}` sin
    `iniciado_en` en el body y sin fila de participación de jugador (C-14)."""
    codigo = _crucigrama_pato(client)
    _registrar(client, prefijo="c14e")  # jugador B

    res = client.post(f"/api/partidas/{codigo}/unirse")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["modo"] == "registrado"
    assert "iniciado_en" not in body

    partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).one()
    assert len(partida.participaciones) == 1  # solo el creador
    assert partida.participaciones[0].rol == "creador"
    assert partida.participaciones[0].iniciado_en is None