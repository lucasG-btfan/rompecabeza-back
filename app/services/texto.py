"""
Normalización de palabras: separación entre representación de grilla y de vista.

Una palabra como "co-autor" o "sr frio" tiene DOS representaciones válidas:

- `limpiar_para_grilla("co-autor")` → "COAUTOR"
  Es la que ocupa las celdas físicas de la sopa/crucigrama. Caracteres como
  espacio, guión o apóstrofe NO deben entrar a la grilla: son fáciles de
  encontrar y rompen la pureza visual del tablero.

- `texto_a_mostrar("co-autor")` → "CO-AUTOR"
  Es la que ve el jugador en la lista de palabras a buscar (chips), en la
  pista del crucigrama y en el editor. Conserva los separadores.

Este módulo es lógica pura (sin I/O) para poder testearla sin tocar la BD.
"""


def limpiar_para_grilla(texto: str) -> str:
    """Versión que va a la grilla: solo letras, en mayúsculas.

    Quita TODO lo que no sea letra (espacios, guiones, apóstrofes, puntos...)
    ya que esos caracteres serían triviales de localizar en el tablero.
    Conserva las letras con acento y la ñ (isalpha las acepta).
    """
    return "".join(c for c in texto.upper() if c.isalpha())


def texto_a_mostrar(texto: str) -> str:
    """Versión presentable: mayúsculas, solo letras, espacios y guiones.

    Conserva los separadores del caso de uso ("co-autor" → "CO-AUTOR",
    "sr frio" → "SR FRIO"). Cualquier OTRO símbolo (puntos, signos de
    exclamación...) se descarta: no existe en la grilla, así que mostrarlo
    en la lista confundiría al jugador ("EL ZORRO!" no se puede marcar,
    el signo no está en el tablero). Espacios múltiples → uno solo.
    """
    con_separadores = "".join(c if c.isalpha() or c in " -" else " " for c in texto.upper())
    return " ".join(con_separadores.split())