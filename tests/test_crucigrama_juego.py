"""
Tests del crucigrama JUGABLE (C-10): endpoint `PUT /palabras/{id}/respuesta`
y sanitización del `GET /estado` para crucigrama (anti-cheat de letras).

PostgreSQL real (regla dura): mismos fixtures y estilo que
`test_crucigrama_editor_endpoint.py`. La grilla del ejemplo PATO/ORO/AS usa el
layout manual determinista de C-09 (3 filas x 4 columnas):

    P A T O    PATO H (0,0) -> numero 1
      S   R    AS   V (0,1) -> numero 2
          O    ORO  V (0,3) -> numero 3

Celdas (índice plano = fila*4 + columna):
    idx 0 (0,0) P  | idx 1 (0,1) A  | idx 2 (0,2) T  | idx 3 (0,3) O
    idx 4 (1,0) -  | idx 5 (1,1) S  | idx 6 (1,2) -  | idx 7 (1,3) R
    idx 8 (2,0) -  | idx 9 (2,1) -  | idx10 (2,2) -  | idx11 (2,3) O
"""

import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.models import Partida


def _registrar(client, prefijo="c10"):
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


def _respuesta(client, codigo, palabra_id, letras):
    return client.put(
        f"/api/partidas/{codigo}/palabras/{palabra_id}/respuesta",
        json={"letras": letras},
    )


# ---------------------------------------------------------------------------
# PUT /palabras/{id}/respuesta — validación de respuesta por palabra (D1)
# ---------------------------------------------------------------------------


def test_respuesta_acierto_registrado_feliz(client, db_sesion):
    """Acierto de jugador registrado: 200 + posicion, SIN participación de
    jugador ni hallazgo (progreso efímero C-14)."""
    codigo = _crucigrama_pato(client)
    pato = _palabra_id(client, codigo, "PATO")
    _registrar(client, prefijo="c10b")  # jugador B (la cookie del creador se reemplaza)

    res = _respuesta(client, codigo, pato, "PATO")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["encontrada"] is True
    assert body["posicion"] == {"fila": 0, "columna": 0, "orientacion": "H", "numero": 1}

    # Nada se persiste: solo el creador tiene participación y no hay hallazgos.
    partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).one()
    assert len(partida.participaciones) == 1
    assert partida.participaciones[0].rol == "creador"
    assert partida.participaciones[0].hallazgos == []
    assert partida.participaciones[0].palabras_encontradas == 0


def test_respuesta_completa_sin_finalizacion(client, db_sesion):
    """Completar las 3 palabras responde 200 para todas, sin participación de
    jugador ni finalización: el backend no persiste progreso (C-14)."""
    codigo = _crucigrama_pato(client)
    pato = _palabra_id(client, codigo, "PATO")
    oro = _palabra_id(client, codigo, "ORO")
    as_ = _palabra_id(client, codigo, "AS")
    _registrar(client, prefijo="c10c")

    assert _respuesta(client, codigo, pato, "PATO").status_code == 200
    assert _respuesta(client, codigo, as_, "AS").status_code == 200
    res = _respuesta(client, codigo, oro, "ORO")
    assert res.status_code == 200, res.text
    assert res.json()["encontrada"] is True

    partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).one()
    assert len(partida.participaciones) == 1  # solo el creador
    assert partida.participaciones[0].rol == "creador"
    assert partida.participaciones[0].hallazgos == []
    assert partida.participaciones[0].palabras_encontradas == 0
    assert partida.participaciones[0].finalizado_en is None


def test_respuesta_letras_incorrectas_400(client, db_sesion):
    """Letras que no coinciden -> 400, sin crear participación ni hallazgo."""
    codigo = _crucigrama_pato(client)
    pato = _palabra_id(client, codigo, "PATO")
    _registrar(client, prefijo="c10d")

    res = _respuesta(client, codigo, pato, "GATO")
    assert res.status_code == 400, res.text
    assert "no coinciden" in res.json()["detail"]

    # Ninguna jugada persiste: un intento fallido no crea ni toca filas.
    partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).one()
    assert len(partida.participaciones) == 1  # solo el creador
    assert partida.participaciones[0].rol == "creador"


def test_respuesta_minusculas_simbolos_normalizados_200(client):
    """La normalización (limpiar_para_grilla) acepta minúsculas/espacios/símbolos."""
    codigo = _crucigrama_pato(client)
    oro = _palabra_id(client, codigo, "ORO")
    _registrar(client, prefijo="c10e")

    res = _respuesta(client, codigo, oro, " or-o!")
    assert res.status_code == 200, res.text
    assert res.json()["encontrada"] is True


def test_respuesta_invitado_sin_persistencia(client, db_sesion):
    """Invitado (sin sesión): validado con 200 pero NO se persiste nada."""
    codigo = _crucigrama_pato(client)
    pato = _palabra_id(client, codigo, "PATO")

    anon = TestClient(app)
    try:
        res = anon.put(
            f"/api/partidas/{codigo}/palabras/{pato}/respuesta",
            json={"letras": "PATO"},
        )
        assert res.status_code == 200, res.text
        assert res.json()["encontrada"] is True
    finally:
        anon.close()

    partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).one()
    assert len(partida.participaciones) == 1  # solo el creador
    assert partida.participaciones[0].rol == "creador"
    assert partida.participaciones[0].hallazgos == []


def test_respuesta_tipo_sopa_400(client):
    """El endpoint de respuesta es SOLO para crucigrama (sopa -> 400)."""
    codigo = _crear_partida(
        client,
        "sopa",
        [
            {"palabra": "CASA", "explicacion": "Vivienda"},
            {"palabra": "SOL", "explicacion": "Astro"},
        ],
    )
    assert _finalizar(client, codigo).status_code == 200
    casa = _palabra_id(client, codigo, "CASA")

    res = client.put(
        f"/api/partidas/{codigo}/palabras/{casa}/respuesta",
        json={"letras": "CASA"},
    )
    assert res.status_code == 400, res.text
    assert "crucigramas" in res.json()["detail"]
    assert "encontrada" in res.json()["detail"]


def test_respuesta_estado_no_activo_400(client):
    """Partida todavía en 'creando' -> 400."""
    codigo = _crear_partida(
        client,
        "crucigrama",
        [
            {"palabra": "PATO", "explicacion": "Animal de granja"},
            {"palabra": "ORO", "explicacion": "Metal precioso"},
        ],
    )
    pato = _palabra_id(client, codigo, "PATO")

    res = _respuesta(client, codigo, pato, "PATO")
    assert res.status_code == 400, res.text
    assert "todavía no está activa" in res.json()["detail"]


def test_respuesta_palabra_sin_posicion_400(client, db_sesion):
    """Palabra sin posicion (inconsistencia defensiva, estado activo) -> 400."""
    codigo = _crucigrama_pato(client)
    oro = _palabra_id(client, codigo, "ORO")

    partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).one()
    oro_db = next(p for p in partida.palabras if p.palabra == "ORO")
    oro_db.posicion = None
    db_sesion.commit()

    res = _respuesta(client, codigo, oro, "ORO")
    assert res.status_code == 400, res.text
    assert "posición asignada" in res.json()["detail"]


def test_respuesta_campo_extra_422(client):
    """Campo no declarado en el request -> 422 (schema con extra='forbid')."""
    codigo = _crucigrama_pato(client)
    pato = _palabra_id(client, codigo, "PATO")

    res = client.put(
        f"/api/partidas/{codigo}/palabras/{pato}/respuesta",
        json={"letras": "PATO", "extra": 1},
    )
    assert res.status_code == 422, res.text


def test_respuesta_reintento_no_duplica_nada(client, db_sesion):
    """Responder dos veces la misma palabra responde 200 ambas veces sin
    efectos en la base: ya no hay hallazgo que duplicar (C-14)."""
    codigo = _crucigrama_pato(client)
    as_ = _palabra_id(client, codigo, "AS")
    _registrar(client, prefijo="c10f")

    assert _respuesta(client, codigo, as_, "AS").status_code == 200
    assert _respuesta(client, codigo, as_, "AS").status_code == 200

    partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).one()
    assert len(partida.participaciones) == 1  # solo el creador
    assert partida.participaciones[0].rol == "creador"
    assert partida.participaciones[0].hallazgos == []
    assert partida.participaciones[0].palabras_encontradas == 0


# ---------------------------------------------------------------------------
# GET /estado — grilla sanitizada para crucigrama (D2/D3)
# ---------------------------------------------------------------------------


def test_estado_crucigrama_sin_progreso_sanitizado(client):
    """Sin progreso: celdas de letra con letra null (numeros/tipo visibles),
    negras intactas, textos de solución null y numero de pista en el estado."""
    codigo = _crucigrama_pato(client)

    res = client.get(f"/api/partidas/{codigo}/estado")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["tipo"] == "crucigrama"

    celdas = body["grilla"]["celdas"]
    assert len(celdas) == 12
    assert all(c["letra"] is None for c in celdas)

    por_indice = {i: celdas[i] for i in range(len(celdas))}
    # Numeros de pista visibles en las celdas de inicio (el puzzle es legible).
    assert por_indice[0]["numero"] == 1 and por_indice[0]["tipo"] == "letra"
    assert por_indice[1]["numero"] == 2 and por_indice[1]["tipo"] == "letra"
    assert por_indice[3]["numero"] == 3 and por_indice[3]["tipo"] == "letra"
    assert por_indice[2]["numero"] is None and por_indice[2]["tipo"] == "letra"
    # Negras intactas.
    assert por_indice[4]["tipo"] == "negra"
    assert por_indice[4]["letra"] is None and por_indice[4]["numero"] is None

    # Solución no expuesta en las palabras de la grilla.
    palabras_grilla = body["grilla"]["palabras"]
    assert [w["numero"] for w in palabras_grilla] == [1, 2, 3]
    assert all(w["texto"] is None for w in palabras_grilla)

    # EstadoPalabraResponse: palabra/texto_mostrar null, numero de pista presente.
    por_id = {p["id"]: p for p in body["palabras"]}
    assert len(por_id) == 3
    for p in por_id.values():
        assert p["palabra"] is None
        assert p["texto_mostrar"] is None
        assert p["posicion"] is None
        assert p["numero"] in {1, 2, 3}


def test_estado_crucigrama_siempre_ciego(client):
    """Tras responder PATO correctamente, el estado sigue 100% ciego para el
    mismo jugador: sin descubrimiento progresivo (C-14)."""
    codigo = _crucigrama_pato(client)
    pato = _palabra_id(client, codigo, "PATO")
    _registrar(client, prefijo="c10g")

    assert _respuesta(client, codigo, pato, "PATO").status_code == 200

    res = client.get(f"/api/partidas/{codigo}/estado")
    assert res.status_code == 200, res.text
    body = res.json()
    celdas = body["grilla"]["celdas"]
    assert all(c["letra"] is None for c in celdas)

    por_id = {p["id"]: p for p in body["palabras"]}
    assert all(p["encontrada"] is False and p["posicion"] is None for p in por_id.values())


def test_estado_crucigrama_numero_null_defensivo(client, db_sesion):
    """Palabra de crucigrama sin numero en posicion -> numero null (sin crash)."""
    codigo = _crucigrama_pato(client)
    oro = _palabra_id(client, codigo, "ORO")

    partida = db_sesion.query(Partida).filter(Partida.codigo == codigo).one()
    oro_db = next(p for p in partida.palabras if p.palabra == "ORO")
    # Re-asigno el dict (la mutación in-place de un JSON no se trackea sin MutableDict).
    oro_db.posicion = {k: v for k, v in oro_db.posicion.items() if k != "numero"}
    db_sesion.commit()

    res = client.get(f"/api/partidas/{codigo}/estado")
    assert res.status_code == 200, res.text
    por_id = {p["id"]: p for p in res.json()["palabras"]}
    assert por_id[oro]["numero"] is None
    others = [p for p in por_id.values() if p["id"] != oro]
    assert all(p["numero"] is not None for p in others)


def test_estado_sopa_sin_cambios_regresion(client):
    """Regresión: la sopa sigue devolviendo grilla string[][] con sus letras
    visibles y EstadoPalabraResponse con palabra (comportamiento C-05 intacto)."""
    codigo = _crear_partida(
        client,
        "sopa",
        [
            {"palabra": "CASA", "explicacion": "Vivienda"},
            {"palabra": "SOL", "explicacion": "Astro"},
        ],
    )
    assert _finalizar(client, codigo).status_code == 200

    res = client.get(f"/api/partidas/{codigo}/estado")
    assert res.status_code == 200, res.text
    body = res.json()
    grilla = body["grilla"]
    assert isinstance(grilla, list)
    assert isinstance(grilla[0], list)
    assert all(isinstance(celda, str) for fila in grilla for celda in fila)
    assert any(celda != "" for fila in grilla for celda in fila)  # letras visibles

    for p in body["palabras"]:
        assert isinstance(p["palabra"], str) and len(p["palabra"]) > 0