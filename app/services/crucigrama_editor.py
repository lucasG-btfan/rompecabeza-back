"""
Servicio del editor manual de crucigramas (C-09).

Compone las primitivas puras de `crucigrama_generator.py` (`cabe_palabra`,
`check_fantasma`, `construir_grilla`) para validar posicionamientos manuales
ortogonales y construir la grilla final desde un layout manual completo. Un
solo punto de la verdad para la validación cruz/anti-fantasma (D7 de C-08) —
el editor no redefine las reglas, las reutiliza.

REVISIÓN 9.x (decisión post-QA, opción C): se ELIMINÓ la conectividad
obligatoria (D4 REVISADO). `validar_posicion` valida solo cabida de cruce +
anti-fantasma; una palabra puede quedar como componente separado y el orden
de inserción es irrelevante. `construir_layout` acepta layouts desconectados.
La relajación aplica solo al modo manual: `generar_crucigrama` (automático,
C-08) conserva su conectividad estricta.
"""

from app.services.crucigrama_generator import (
    DELTAS,
    ORIENTACIONES_CRUCE,
    CrucigramaGeneratorError,
    cabe_palabra,
    check_fantasma,
    construir_grilla,
    cruzar,
    _sets_por_orientacion,
)


class EditorCrucigramaError(Exception):
    """Error de validación del editor manual (mensaje accionable)."""


def _provisionales(colocadas: list[dict]) -> dict:
    """Mapa celda -> letra de TODAS las palabras colocadas."""
    provisionales: dict = {}
    for w in colocadas:
        for f, c, letra in cruzar(w["posicion"], w["palabra"], w["orientacion"]):
            provisionales[(f, c)] = letra
    return provisionales


def _raise_cabida_detallado(
    provisionales: dict,
    palabra: str,
    fila: int,
    columna: int,
    orientacion: str,
    celdas_h: set,
    celdas_v: set,
) -> None:
    """Mensaje accionable cuando `cabe_palabra` falla: distingue superposición
    paralela de letra distinta en la intersección."""
    perpendiculares = celdas_v if orientacion == "H" else celdas_h
    paralelas = celdas_h if orientacion == "H" else celdas_v
    dr, dc = DELTAS[orientacion]
    for i, letra in enumerate(palabra):
        celda = (fila + i * dr, columna + i * dc)
        if celda not in provisionales:
            continue
        if celda in paralelas:
            raise EditorCrucigramaError(
                "No puede superponerse a una palabra de la misma orientación (paralela)"
            )
        if provisionales[celda] != letra:
            raise EditorCrucigramaError(
                f"La letra en la intersección no coincide: se esperaba "
                f"'{provisionales[celda]}' y la palabra coloca '{letra}'"
            )


def validar_posicion(
    colocadas: list[dict],
    palabra: str,
    fila: int,
    columna: int,
    orientacion: str,
) -> None:
    """Valida una colocación manual (díada REVISADA 9.x: cabida -> fantasma).

    - `colocadas`: palabras YA posicionadas EXCEPTO la que se está moviendo
      (una movida se valida contra las demás, no contra su posición vieja).
    - NO exige conectividad (D4 REVISADO): cualquier palabra puede flotar como
      componente separado; el orden de inserción es irrelevante.
    - Acepta coordenadas negativas (D1 REVISADO): la palabra puede extenderse
      hacia arriba/izquierda del bounding box actual; `construir_grilla`
      traslada todo a (0,0) al finalizar.
    - Cada fallo lanza `EditorCrucigramaError` con mensaje accionable.
    """
    if orientacion not in ORIENTACIONES_CRUCE:
        raise EditorCrucigramaError("Para crucigrama la orientación debe ser H o V")

    provisionales = _provisionales(colocadas)
    celdas_h, celdas_v = _sets_por_orientacion(colocadas)

    if not cabe_palabra(
        provisionales, palabra, fila, columna, orientacion, celdas_h, celdas_v
    ):
        _raise_cabida_detallado(
            provisionales, palabra, fila, columna, orientacion, celdas_h, celdas_v
        )

    if not check_fantasma(provisionales, palabra, fila, columna, orientacion, colocadas):
        raise EditorCrucigramaError(
            "La colocación genera una palabra fantasma "
            "(adyacente paralela o pegada en línea)"
        )


def construir_layout(colocadas: list[dict]) -> tuple[dict, dict]:
    """Construye la grilla D6 desde un layout manual completo.

    Delega en `construir_grilla` del generador (un solo lugar de la verdad
    para el shape final) y verifica `GRILLA_MAXIMA` (ya dentro de
    `construir_grilla`). REVISIÓN 9.x: ya NO re-valida conectividad global —
    un layout con componentes separados es válido (D4 REVISADO); la
    normalización (bounding box mínimo + traslación a (0,0)) absorbe
    coordenadas negativas.

    Lanza `EditorCrucigramaError` con mensaje accionable.
    """
    if not colocadas:
        raise EditorCrucigramaError(
            "No hay palabras posicionadas para construir la grilla"
        )

    try:
        return construir_grilla(colocadas)
    except CrucigramaGeneratorError as e:
        raise EditorCrucigramaError(str(e)) from e