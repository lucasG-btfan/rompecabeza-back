"""
Generador de Crucigramas.

Colocación ortogonal (solo H y V), cruces perpendiculares con letra
coincidente, rechazo de palabras fantasma y numeración estándar de pistas.

Lógica pura (sin I/O), mismo patrón que `sopa_generator.py`. Las funciones
de validación (`cabe_palabra`, `check_fantasma`, `puede_cruzar`) se exponen
como API pública para que el editor manual (C-09) aplique exactamente la
misma validación cruz y anti-fantasma (un solo punto de la verdad).
"""

import random

ORIENTACIONES_CRUCE = {"H", "V"}
DELTAS = {"H": (0, 1), "V": (1, 0)}
GRILLA_MAXIMA = 30
MAX_INTENTOS = 500


class CrucigramaGeneratorError(Exception):
    """Error cuando no se puede generar un crucigrama válido."""


def cruzar(posicion: dict, palabra: str, orientacion: str) -> set:
    """Genera las celdas `(fila, columna, letra)` que una palabra ocuparía.

    Solo H (izquierda -> derecha) y V (arriba -> abajo): direcciones de un
    solo eje. Cualquier otra orientación es inválida para crucigrama.
    """
    if orientacion not in ORIENTACIONES_CRUCE:
        raise CrucigramaGeneratorError(
            f"Orientación inválida para crucigrama: {orientacion}"
        )
    dr, dc = DELTAS[orientacion]
    fila = posicion["fila"]
    columna = posicion["columna"]
    return {(fila + i * dr, columna + i * dc, letra) for i, letra in enumerate(palabra)}


def _sets_por_orientacion(colocadas: list[dict]) -> tuple[set, set]:
    """Celdas (fila, columna) cubiertas por palabras H y V ya colocadas."""
    celdas_h: set = set()
    celdas_v: set = set()
    for w in colocadas:
        celdas = cruzar(w["posicion"], w["palabra"], w["orientacion"])
        destino = celdas_h if w["orientacion"] == "H" else celdas_v
        destino.update((f, c) for f, c, _ in celdas)
    return celdas_h, celdas_v


def cabe_palabra(
    provisionales: dict,
    palabra: str,
    fila: int,
    columna: int,
    orientacion: str,
    celdas_h: set | None = None,
    celdas_v: set | None = None,
) -> bool:
    """True si la colocación no choca: cada celda ocupada debe ser un cruce
    perpendicular real con la MISMA letra (jamás un solapamiento paralelo o
    con letra distinta)."""
    if orientacion not in ORIENTACIONES_CRUCE:
        return False
    if celdas_h is None:
        celdas_h = set()
    if celdas_v is None:
        celdas_v = set()
    perpendiculares = celdas_v if orientacion == "H" else celdas_h
    paralelas = celdas_h if orientacion == "H" else celdas_v
    dr, dc = DELTAS[orientacion]

    for i, letra in enumerate(palabra):
        celda = (fila + i * dr, columna + i * dc)
        if celda not in provisionales:
            continue
        if celda in paralelas:
            return False
        if celda not in perpendiculares:
            return False
        if provisionales[celda] != letra:
            return False
    return True


def puede_cruzar(
    palabra: str,
    fila: int,
    columna: int,
    orientacion: str,
    provisionales: dict,
) -> bool:
    """True si la colocación cruza al menos una palabra ya colocada."""
    celdas = cruzar({"fila": fila, "columna": columna}, palabra, orientacion)
    return any((f, c) in provisionales for f, c, _ in celdas)


def check_fantasma(
    provisionales: dict,
    palabra: str,
    fila: int,
    columna: int,
    orientacion: str,
    colocadas: list[dict],
) -> bool:
    """Rechaza colocaciones que forman palabras accidentales (fantasma):

    1. Vecino perpendicular ocupado por una palabra PARALELA -> rechaza.
       Solo se tolera si corresponde a una palabra perpendicular que cruza
       en ese mismo punto (cruce legítimo).
    2. Continuación en línea: la celda anterior a la primera y la posterior
       a la última deben estar vacías (no pegarse a otra palabra paralela).
    """
    if orientacion not in ORIENTACIONES_CRUCE:
        return False
    dr, dc = DELTAS[orientacion]
    celdas_h, celdas_v = _sets_por_orientacion(colocadas)
    perpendiculares = celdas_v if orientacion == "H" else celdas_h
    celdas_nuevas = cruzar({"fila": fila, "columna": columna}, palabra, orientacion)

    for f, c, _ in celdas_nuevas:
        for signo in (1, -1):
            vecina = (f + signo * dc, c + signo * dr)
            if vecina in provisionales:
                if (f, c) not in perpendiculares or vecina not in perpendiculares:
                    return False

    antes = (fila - dr, columna - dc)
    despues = (fila + dr * len(palabra), columna + dc * len(palabra))
    if antes in provisionales or despues in provisionales:
        return False

    return True


def numerar_pistas(
    palabras: list[dict],
    filas: int,
    columnas: int,
) -> tuple[dict, int]:
    """Numeración estándar 1..N en barrido fila-major (arriba-izquierda).

    Una celda recibe el siguiente número si inicia una palabra H o V en la
    grilla normalizada; si inicia las dos, avanza una sola vez. Retorna
    `(numeros, total)` donde `numeros` mapea el índice plano de la celda
    (`fila * columnas + columna`, extremo inferior incluido, 0-based) al
    número de pista; `total` es la cantidad de pistas.
    """
    inicios_h: set = set()
    inicios_v: set = set()
    for w in palabras:
        celda = (w["fila"], w["columna"])
        if w["orientacion"] == "H":
            inicios_h.add(celda)
        else:
            inicios_v.add(celda)

    numeros = {}
    contador = 0
    for f in range(filas):
        for c in range(columnas):
            if (f, c) in inicios_h or (f, c) in inicios_v:
                contador += 1
                numeros[f * columnas + c] = contador
    return numeros, contador


def _generar_candidatos(palabra: str, colocadas: list[dict]) -> list[tuple]:
    """Posiciones (fila, columna, orientacion) donde `palabra` cruza una
    palabra existente con una letra idéntica (candidatos de anclaje)."""
    candidatos: set = set()
    for w in colocadas:
        celdas_existentes = cruzar(w["posicion"], w["palabra"], w["orientacion"])
        for f, c, letra in celdas_existentes:
            for orientacion in ORIENTACIONES_CRUCE - {w["orientacion"]}:
                dr, dc = DELTAS[orientacion]
                for i, letra_nueva in enumerate(palabra):
                    if letra_nueva == letra:
                        candidatos.add((f - i * dr, c - i * dc, orientacion))
    return list(candidatos)


def generar_crucigrama(
    palabras: list[str],
    seed: int | None = None,
) -> tuple[dict, dict]:
    """Genera una grilla de crucigrama ortogonal y conectada.

    Greedy aleatorizado (reintentos): la palabra más larga va en H en (0,0);
    el resto se ancla buscando cruces perpendiculares con letra coincidente,
    rechazando palabras fantasma. El mapa disperso admite coordenadas
    negativas; al final se traslada todo al bounding box mínimo (>= 0).

    Retorna:
      - grilla: `{"celdas": [...], "palabras": [...]}` (contrato D6)
      - posiciones: `{palabra_limpia: {"fila", "columna", "orientacion", "numero"}}`

    Lanza `CrucigramaGeneratorError` si no hay solución: menos de dos
    palabras, palabras que se limpian a una sola letra, sin letras en común
    o bounding box que excede `GRILLA_MAXIMA`.
    """
    palabras_norm = [p.upper() for p in palabras]

    if len(palabras_norm) < 2:
        raise CrucigramaGeneratorError(
            "Se necesitan al menos dos palabras para generar un crucigrama"
        )
    for p in palabras_norm:
        if len(p) < 2:
            raise CrucigramaGeneratorError(
                "Cada palabra debe tener al menos dos letras para poder cruzar"
            )

    ordenadas = sorted(palabras_norm, key=len, reverse=True)
    rng = random.Random(seed)
    resolucion: list[dict] | None = None

    for _ in range(MAX_INTENTOS):
        provisionales: dict = {}
        colocadas: list[dict] = []
        exito = True

        for palabra in ordenadas:
            if not colocadas:
                fila, columna, orientacion = 0, 0, "H"
            else:
                candidatos = _generar_candidatos(palabra, colocadas)
                rng.shuffle(candidatos)
                celdas_h, celdas_v = _sets_por_orientacion(colocadas)
                elegido = None
                for fila, columna, orientacion in candidatos:
                    if cabe_palabra(
                        provisionales,
                        palabra,
                        fila,
                        columna,
                        orientacion,
                        celdas_h,
                        celdas_v,
                    ) and check_fantasma(provisionales, palabra, fila, columna, orientacion, colocadas):
                        elegido = (fila, columna, orientacion)
                        break
                if elegido is None:
                    exito = False
                    break
                fila, columna, orientacion = elegido

            for f, c, letra in cruzar({"fila": fila, "columna": columna}, palabra, orientacion):
                provisionales[(f, c)] = letra
            colocadas.append(
                {
                    "palabra": palabra,
                    "posicion": {"fila": fila, "columna": columna},
                    "orientacion": orientacion,
                }
            )

        if exito:
            resolucion = colocadas
            break

    if resolucion is None:
        raise CrucigramaGeneratorError(
            "No se pudo generar un crucigrama válido con estas palabras. "
            "Probá con palabras que compartan letras o con más palabras de 2+ letras."
        )

    todas_las_celdas = []
    for w in resolucion:
        todas_las_celdas.extend(cruzar(w["posicion"], w["palabra"], w["orientacion"]))
    min_fila = min(f for f, _, _ in todas_las_celdas)
    min_col = min(c for _, c, _ in todas_las_celdas)
    total_filas = max(f for f, _, _ in todas_las_celdas) - min_fila + 1
    total_columnas = max(c for _, c, _ in todas_las_celdas) - min_col + 1

    if total_filas > GRILLA_MAXIMA or total_columnas > GRILLA_MAXIMA:
        raise CrucigramaGeneratorError(
            f"Las palabras no entran en una grilla de {GRILLA_MAXIMA} filas o columnas como máximo"
        )

    provisionales_tras: dict = {}
    trasladas: list[dict] = []
    for w in resolucion:
        f0 = w["posicion"]["fila"] - min_fila
        c0 = w["posicion"]["columna"] - min_col
        for f, c, letra in cruzar(w["posicion"], w["palabra"], w["orientacion"]):
            provisionales_tras[(f - min_fila, c - min_col)] = letra
        trasladas.append(
            {
                "palabra": w["palabra"],
                "posicion": {"fila": f0, "columna": c0},
                "orientacion": w["orientacion"],
            }
        )

    numeros, _total = numerar_pistas(
        [
            {
                "fila": w["posicion"]["fila"],
                "columna": w["posicion"]["columna"],
                "orientacion": w["orientacion"],
            }
            for w in trasladas
        ],
        total_filas,
        total_columnas,
    )

    celdas = []
    for f in range(total_filas):
        for c in range(total_columnas):
            indice = f * total_columnas + c
            if (f, c) in provisionales_tras:
                celdas.append(
                    {
                        "letra": provisionales_tras[(f, c)],
                        "numero": numeros.get(indice),
                        "tipo": "letra",
                    }
                )
            else:
                celdas.append({"letra": None, "numero": None, "tipo": "negra"})

    palabras_grilla = []
    posiciones = {}
    for w in sorted(
        trasladas,
        key=lambda w: numeros[w["posicion"]["fila"] * total_columnas + w["posicion"]["columna"]],
    ):
        f, c = w["posicion"]["fila"], w["posicion"]["columna"]
        numero = numeros[f * total_columnas + c]
        palabras_grilla.append(
            {
                "numero": numero,
                "orientacion": w["orientacion"],
                "posicion": {"fila": f, "columna": c},
                "longitud": len(w["palabra"]),
                "texto": w["palabra"],
            }
        )
        posiciones[w["palabra"]] = {
            "fila": f,
            "columna": c,
            "orientacion": w["orientacion"],
            "numero": numero,
        }

    return {"celdas": celdas, "palabras": palabras_grilla}, posiciones