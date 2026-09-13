import pytest

from app.services.crucigrama_generator import (
    CrucigramaGeneratorError,
    GRILLA_MAXIMA,
    MAX_INTENTOS,
    ORIENTACIONES_CRUCE,
    cabe_palabra,
    check_fantasma,
    cruzar,
    generar_crucigrama,
    numerar_pistas,
    puede_cruzar,
)


def _celdas_de_palabra(fila, columna, orientacion, texto):
    dr, dc = (0, 1) if orientacion == "H" else (1, 0)
    return {(fila + i * dr, columna + i * dc, letra) for i, letra in enumerate(texto)}


def _mapa_celdas(celdas):
    return {(f, c): letra for f, c, letra in celdas}


def _colocada(texto, fila, columna, orientacion):
    return {
        "palabra": texto,
        "posicion": {"fila": fila, "columna": columna},
        "orientacion": orientacion,
    }


def _celdas_h(grilla):
    return {
        (f, c)
        for w in grilla["palabras"]
        if w["orientacion"] == "H"
        for f, c, _ in _celdas_de_palabra(
            w["posicion"]["fila"], w["posicion"]["columna"], "H", w["texto"]
        )
    }


def _celdas_v(grilla):
    return {
        (f, c)
        for w in grilla["palabras"]
        if w["orientacion"] == "V"
        for f, c, _ in _celdas_de_palabra(
            w["posicion"]["fila"], w["posicion"]["columna"], "V", w["texto"]
        )
    }


class TestConstantesYApi:
    def test_constantes_del_modulo(self):
        assert ORIENTACIONES_CRUCE == {"H", "V"}
        assert GRILLA_MAXIMA == 30
        assert MAX_INTENTOS == 500

    def test_cruzar_horizontal(self):
        celdas = cruzar({"fila": 0, "columna": 0}, "CASA", "H")
        assert celdas == {(0, 0, "C"), (0, 1, "A"), (0, 2, "S"), (0, 3, "A")}

    def test_cruzar_vertical(self):
        celdas = cruzar({"fila": 1, "columna": 2}, "SOL", "V")
        assert celdas == {(1, 2, "S"), (2, 2, "O"), (3, 2, "L")}

    def test_cruzar_rechaza_diagonal(self):
        with pytest.raises(CrucigramaGeneratorError):
            cruzar({"fila": 0, "columna": 0}, "CASA", "D")


class TestColocacionOrtogonal:
    def test_el_ancla_inicial_queda_en_el_origen(self):
        # C-11 (defecto QA 6.7): el orden de colocación se baraja por intento,
        # así que la "más larga primero" dejó de ser garantía. El invariante
        # real se mantiene: el bounding box mínimo se normaliza a (0,0) y esa
        # celda SIEMPRE pertenece a alguna palabra (el ancla del layout).
        grilla, _ = generar_crucigrama(["CASA", "SOL"], seed=1)
        celdas_ocupadas = {
            (f, c)
            for w in grilla["palabras"]
            for f, c, _ in _celdas_de_palabra(
                w["posicion"]["fila"], w["posicion"]["columna"], w["orientacion"], w["texto"]
            )
        }
        assert (0, 0) in celdas_ocupadas

    def test_todas_las_palabras_son_h_o_v(self):
        grilla, _ = generar_crucigrama(["AVION", "CASA", "LUNA", "SOL"], seed=1)
        assert all(w["orientacion"] in {"H", "V"} for w in grilla["palabras"])
        assert all(w["posicion"]["fila"] >= 0 and w["posicion"]["columna"] >= 0 for w in grilla["palabras"])


class TestCruces:
    def test_dos_palabras_cruzan_perpendicularmente(self):
        grilla, _ = generar_crucigrama(["CASA", "SOL"], seed=1)
        interseccion = _celdas_h(grilla) & _celdas_v(grilla)
        assert interseccion, "las dos palabras deberían compartir un cruce perpendicular"

    def test_toda_palabra_cruza_al_menos_una_existente(self):
        grilla, _ = generar_crucigrama(["CASA", "SOL", "LUNA"], seed=1)
        celdas_por_palabra = [
            {
                (f, c)
                for f, c, _ in _celdas_de_palabra(
                    w["posicion"]["fila"], w["posicion"]["columna"], w["orientacion"], w["texto"]
                )
            }
            for w in grilla["palabras"]
        ]
        for i, celdas in enumerate(celdas_por_palabra):
            resto = set().union(*(celdas_por_palabra[:i] + celdas_por_palabra[i + 1 :]))
            assert celdas & resto, "toda palabra (salvo la primera) debe cruzar ≥1 existente"

    def test_cabe_palabra_rechaza_letra_distinta(self):
        provisionales = _mapa_celdas(_celdas_de_palabra(0, 0, "H", "CASA"))
        celdas_h = {(f, c) for f, c, _ in _celdas_de_palabra(0, 0, "H", "CASA")}
        # "SOL" vertical sobre (0,1) pondría 'S' donde CASA tiene 'A'
        assert cabe_palabra(provisionales, "SOL", 0, 1, "V", celdas_h=celdas_h, celdas_v=set()) is False

    def test_puede_cruzar_exige_celda_ocupada(self):
        provisionales = _mapa_celdas(_celdas_de_palabra(0, 0, "H", "CASA"))
        # arranca en una celda vacía → no cruza
        assert puede_cruzar("SOL", 3, 3, "V", provisionales) is False
        # arranca sobre una celda ocupada (0,2='S') → cruza
        assert puede_cruzar("SOL", 0, 2, "V", provisionales) is True

    def test_generador_nunca_deja_letras_distintas(self):
        grilla, _ = generar_crucigrama(["CASA", "SOL", "ALTO"], seed=1)
        mapa = {}
        for w in grilla["palabras"]:
            for f, c, letra in _celdas_de_palabra(
                w["posicion"]["fila"], w["posicion"]["columna"], w["orientacion"], w["texto"]
            ):
                if (f, c) in mapa:
                    assert mapa[(f, c)] == letra
                mapa[(f, c)] = letra


class TestAntiFantasma:
    def test_rechaza_paralela_adyacente(self):
        prov = _mapa_celdas(_celdas_de_palabra(0, 0, "H", "CASA"))
        colocadas = [_colocada("CASA", 0, 0, "H")]
        assert check_fantasma(prov, "SOL", 1, 0, "H", colocadas) is False

    def test_rechaza_extension_en_linea(self):
        prov = _mapa_celdas(_celdas_de_palabra(0, 0, "H", "CASA"))
        colocadas = [_colocada("CASA", 0, 0, "H")]
        # "PAL" pegada a la derecha: la celda anterior (0,3) está ocupada
        assert check_fantasma(prov, "PAL", 0, 4, "H", colocadas) is False

    def test_permite_cruce_perpendicular_legitimo(self):
        prov = _mapa_celdas(_celdas_de_palabra(0, 0, "H", "CASA"))
        colocadas = [_colocada("CASA", 0, 0, "H")]
        # "SOL" vertical cruzando en la S de (0,2)
        assert check_fantasma(prov, "SOL", 0, 2, "V", colocadas) is True


class TestNumeracion:
    def test_numeracion_caso_2x2_minimo(self):
        grilla, posiciones = generar_crucigrama(["OS", "LO"], seed=1)
        numeros = sorted(w["numero"] for w in grilla["palabras"])
        assert numeros == [1, 2]
        por_texto = {w["texto"]: w["numero"] for w in grilla["palabras"]}
        assert por_texto["LO"] == 1
        assert por_texto["OS"] == 2
        assert posiciones["LO"]["numero"] == 1
        assert posiciones["OS"]["numero"] == 2

    def test_numerar_pistas_barrido_fila_major(self):
        numeros, total = numerar_pistas(
            [
                {"fila": 0, "columna": 0, "orientacion": "V"},
                {"fila": 1, "columna": 0, "orientacion": "H"},
            ],
            filas=2,
            columnas=2,
        )
        assert total == 2
        assert numeros[0] == 1  # celda (0,0)
        assert numeros[2] == 2  # celda (1,0)
        assert 1 not in numeros  # celda (0,1) no inicia ninguna pista

    def test_una_celda_con_dos_inicios_recibe_un_solo_numero(self):
        numeros, total = numerar_pistas(
            [
                {"fila": 0, "columna": 0, "orientacion": "H"},
                {"fila": 0, "columna": 0, "orientacion": "V"},
            ],
            filas=2,
            columnas=2,
        )
        assert total == 1
        assert numeros[0] == 1


class TestDeterminismo:
    def test_misma_semilla_misma_grilla(self):
        palabras = ["CASA", "SOL", "LUNA", "RELOJ", "AVION"]
        g1, p1 = generar_crucigrama(palabras, seed=42)
        g2, p2 = generar_crucigrama(palabras, seed=42)
        assert g1 == g2
        assert p1 == p2

    def test_semilla_aleatoria_por_defecto_no_falla(self):
        grilla, _ = generar_crucigrama(["CASA", "SOL"])
        assert grilla["celdas"]


class TestErrores:
    def test_error_sin_letras_en_comun(self):
        with pytest.raises(CrucigramaGeneratorError):
            generar_crucigrama(["REY", "SOL"])

    def test_error_palabra_aislada_mensaje_diferenciado(self):
        # C-11 (defecto QA B): el grafo de compatibilidad es desconectado —
        # PERRO (P,E,R,O) no comparte NINGUNA letra con LUNA (L,U,N,A) ni
        # con CASA (C,A,S). Mensaje con los nombres + fallback manual.
        with pytest.raises(CrucigramaGeneratorError) as exc:
            generar_crucigrama(["LUNA", "PERRO", "CASA"])
        texto = str(exc.value)
        assert "PERRO" in texto
        assert "no comparten ninguna letra con el resto" in texto
        assert "editor manual" in texto

    def test_error_intentos_agotados_mensaje_con_fallback_editor(self, monkeypatch):
        # Mecanismo: el grafo es conexo (CASA↔SOL por S) pero los intentos se
        # agotan → mensaje honesto con fallback al editor manual. Se fuerza
        # con MAX_INTENTOS=0 (determinístico — el caso real "conexo sin
        # solución en 500" no se puede garantizar por datos: depende del
        # greedy; documentado en la task 6.7).
        import app.services.crucigrama_generator as gen

        monkeypatch.setattr(gen, "MAX_INTENTOS", 0)
        with pytest.raises(CrucigramaGeneratorError) as exc:
            generar_crucigrama(["CASA", "SOL"])
        texto = str(exc.value)
        assert "crucigrama automático" in texto
        assert "manualmente en el editor" in texto

    def test_error_menos_de_dos_palabras(self):
        with pytest.raises(CrucigramaGeneratorError):
            generar_crucigrama(["CASA"])

    def test_error_palabra_de_una_letra(self):
        with pytest.raises(CrucigramaGeneratorError):
            generar_crucigrama(["A", "CASA"])

    def test_error_bounding_box_excede_el_maximo(self):
        with pytest.raises(CrucigramaGeneratorError):
            generar_crucigrama(["A" * 35, "A" * 30])


class TestCeldasNegras:
    def test_negras_en_celdas_vacias_y_letras_en_ocupadas(self):
        grilla, _ = generar_crucigrama(["CASA", "SOL"], seed=1)
        celdas = grilla["celdas"]

        mapa = {}
        for w in grilla["palabras"]:
            for f, c, letra in _celdas_de_palabra(
                w["posicion"]["fila"], w["posicion"]["columna"], w["orientacion"], w["texto"]
            ):
                mapa[(f, c)] = letra

        filas = max(
            w["posicion"]["fila"] + (len(w["texto"]) if w["orientacion"] == "V" else 1)
            for w in grilla["palabras"]
        )
        columnas = max(
            w["posicion"]["columna"] + (len(w["texto"]) if w["orientacion"] == "H" else 1)
            for w in grilla["palabras"]
        )
        assert len(celdas) == filas * columnas

        for f in range(filas):
            for c in range(columnas):
                celda = celdas[f * columnas + c]
                if (f, c) in mapa:
                    assert celda["tipo"] == "letra"
                    assert celda["letra"] == mapa[(f, c)]
                else:
                    assert celda["tipo"] == "negra"
                    assert celda["letra"] is None
                    assert celda["numero"] is None


class TestRobustezOrdenEntrada:
    """C-11/C-08 (defecto QA 6.7): el generador greedy usaba un ORDEN de
    colocación fijo (más larga primero, estable) construido UNA vez fuera del
    loop de intentos — la resolubilidad dependía de cómo el creador tipeaba
    las palabras (600 de 720 permutaciones del set del PO fallaban). El orden
    de ENTRADA no debe afectar la generación."""

    SET_PO = ["PERRO", "GATO", "AGUA", "PAN", "LUNA", "CASA"]

    def test_set_del_po_en_orden_adverso_genera(self):
        # Orden reportado por el PO (LUNA antes que AGUA): fallaba SIEMPRE
        # hoy — el sort estable lo mantenía y la solución conocida arranca
        # con PERRO VERTICAL, inalcanzable con la primera en (0,0,"H").
        grilla, _ = generar_crucigrama(
            ["PERRO", "GATO", "LUNA", "AGUA", "CASA", "PAN"], seed=7
        )
        assert {w["texto"] for w in grilla["palabras"]} == set(self.SET_PO)

    def test_permutaciones_distintas_todas_generan(self):
        for orden in [
            ["PERRO", "LUNA", "GATO", "PAN", "CASA", "AGUA"],
            ["PAN", "CASA", "AGUA", "LUNA", "GATO", "PERRO"],
            ["GATO", "AGUA", "PERRO", "CASA", "PAN", "LUNA"],
        ]:
            grilla, _ = generar_crucigrama(orden, seed=7)
            assert {w["texto"] for w in grilla["palabras"]} == set(self.SET_PO), orden

    def test_grilla_valida_triangulate(self):
        # Validación estructural de la solución generada: las 6 palabras
        # presentes, ortogonales, dentro de la grilla normalizada y cada una
        # cruza ≥1 existente (anti-fantasma estructural, helpers del archivo).
        grilla, _ = generar_crucigrama(self.SET_PO, seed=7)
        assert {w["texto"] for w in grilla["palabras"]} == set(self.SET_PO)
        assert all(w["orientacion"] in {"H", "V"} for w in grilla["palabras"])
        assert all(
            w["posicion"]["fila"] >= 0 and w["posicion"]["columna"] >= 0
            for w in grilla["palabras"]
        )
        celdas_por_palabra = [
            {
                (f, c)
                for f, c, _ in _celdas_de_palabra(
                    w["posicion"]["fila"],
                    w["posicion"]["columna"],
                    w["orientacion"],
                    w["texto"],
                )
            }
            for w in grilla["palabras"]
        ]
        for i, celdas in enumerate(celdas_por_palabra):
            resto = set().union(*(celdas_por_palabra[:i] + celdas_por_palabra[i + 1 :]))
            assert celdas & resto, "toda palabra debe cruzar ≥1 existente (sin fantasma)"