"""
Algoritmo de generación de Sopa de Letras.

Coloca cada palabra en una grilla en una de 8 direcciones posibles,
permitiendo que las palabras se crucen si comparten letras en la misma
celda, sin permitir solapamientos con letras distintas. Si no entran
todas las palabras, agranda la grilla automáticamente y reintenta.
"""

import random
import string

DIRECCIONES = {
    "E": (0, 1),      # Este  (horizontal, izquierda -> derecha)
    "O": (0, -1),     # Oeste (horizontal, derecha -> izquierda)
    "S": (1, 0),      # Sur   (vertical, arriba -> abajo)
    "N": (-1, 0),     # Norte (vertical, abajo -> arriba)
    "SE": (1, 1),     # Diagonal abajo-derecha
    "SO": (1, -1),    # Diagonal abajo-izquierda
    "NE": (-1, 1),    # Diagonal arriba-derecha
    "NO": (-1, -1),   # Diagonal arriba-izquierda
}

ALFABETO = string.ascii_uppercase
GRILLA_MAXIMA = 30


class SopaGeneratorError(Exception):
    """Error cuando no se puede generar una sopa válida."""


def calcular_tamano(palabras: list[str]) -> tuple[int, int]:
    """Calcula un tamaño de grilla razonable según cantidad/longitud de palabras."""
    palabra_mas_larga = max(len(p) for p in palabras)
    base = max(palabra_mas_larga + 2, 10)
    extra = len(palabras) // 2
    lado = min(base + extra, GRILLA_MAXIMA)
    return lado, lado


def _cabe_palabra(grid, palabra, fila, columna, df, dc):
    filas, columnas = len(grid), len(grid[0])
    f, c = fila, columna
    for letra in palabra:
        if not (0 <= f < filas and 0 <= c < columnas):
            return False
        celda = grid[f][c]
        if celda is not None and celda != letra:
            return False
        f += df
        c += dc
    return True


def _colocar_palabra(grid, palabra, fila, columna, df, dc):
    f, c = fila, columna
    for letra in palabra:
        grid[f][c] = letra
        f += df
        c += dc


def generar_sopa(
    palabras: list[str],
    filas: int | None = None,
    columnas: int | None = None,
    max_intentos_por_palabra: int = 200,
) -> tuple[list[list[str]], dict[str, dict]]:
    """
    Genera la grilla de la sopa de letras.

    Retorna:
      - grid: matriz filas x columnas de letras (list[list[str]])
      - posiciones: dict {palabra: {"fila", "columna", "orientacion"}}

    Si no entran todas las palabras en el tamaño dado (o calculado), agranda
    la grilla de a 2 en 2 hasta GRILLA_MAXIMA y reintenta desde cero.
    """
    palabras_norm = [p.upper() for p in palabras]

    if filas is None or columnas is None:
        filas, columnas = calcular_tamano(palabras_norm)

    while True:
        grid: list[list] = [[None] * columnas for _ in range(filas)]
        posiciones: dict[str, dict] = {}
        exito = True

        for palabra in sorted(palabras_norm, key=len, reverse=True):
            colocada = False

            for _ in range(max_intentos_por_palabra):
                orientacion = random.choice(list(DIRECCIONES.keys()))
                df, dc = DIRECCIONES[orientacion]
                fila_ini = random.randint(0, filas - 1)
                col_ini = random.randint(0, columnas - 1)

                if _cabe_palabra(grid, palabra, fila_ini, col_ini, df, dc):
                    _colocar_palabra(grid, palabra, fila_ini, col_ini, df, dc)
                    posiciones[palabra] = {
                        "fila": fila_ini,
                        "columna": col_ini,
                        "orientacion": orientacion,
                    }
                    colocada = True
                    break

            if not colocada:
                exito = False
                break

        if exito:
            break

        if filas >= GRILLA_MAXIMA or columnas >= GRILLA_MAXIMA:
            raise SopaGeneratorError(
                f"No se pudieron ubicar todas las palabras ni con la grilla máxima "
                f"({GRILLA_MAXIMA}x{GRILLA_MAXIMA}). Probá con menos palabras o palabras más cortas."
            )

        filas = min(filas + 2, GRILLA_MAXIMA)
        columnas = min(columnas + 2, GRILLA_MAXIMA)

    for f in range(filas):
        for c in range(columnas):
            if grid[f][c] is None:
                grid[f][c] = random.choice(ALFABETO)

    return grid, posiciones


def calcular_celda_final(fila: int, columna: int, orientacion: str, largo: int) -> tuple[int, int]:
    """Dada la celda inicial + orientación + largo de palabra, calcula la celda final."""
    if orientacion not in DIRECCIONES:
        raise ValueError(f"Orientación inválida: {orientacion}")
    df, dc = DIRECCIONES[orientacion]
    return fila + df * (largo - 1), columna + dc * (largo - 1)
