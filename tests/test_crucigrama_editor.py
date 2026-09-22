import pytest

from app.services.crucigrama_editor import (
    EditorCrucigramaError,
    construir_layout,
    validar_posicion,
)
from app.services.crucigrama_generator import construir_grilla


def _colocada(texto, fila, columna, orientacion):
    return {
        "palabra": texto,
        "posicion": {"fila": fila, "columna": columna},
        "orientacion": orientacion,
    }


def _celdas_de_palabra(fila, columna, orientacion, texto):
    dr, dc = (0, 1) if orientacion == "H" else (1, 0)
    return {(fila + i * dr, columna + i * dc, letra) for i, letra in enumerate(texto)}


class TestCrucesValidos:
    def test_h_sobre_v_existente_con_letra_coincidente(self):
        # CASA H en (0,0) cruza SOL V en la S de (0,2)
        validar_posicion([_colocada("SOL", 0, 2, "V")], "CASA", 0, 0, "H")

    def test_v_sobre_h_existente_con_letra_coincidente(self):
        validar_posicion([_colocada("CASA", 0, 0, "H")], "SOL", 0, 2, "V")


class TestCruceLetraDistinta:
    def test_letra_distinta_en_interseccion(self):
        # (0,1) de CASA es 'A' y SOL pondría 'S' ahí
        with pytest.raises(EditorCrucigramaError) as e:
            validar_posicion([_colocada("CASA", 0, 0, "H")], "SOL", 0, 1, "V")
        assert "no coincide" in str(e.value)


class TestSolapamientoParalelo:
    def test_misma_orientacion_superpuesta(self):
        # SOL H en (0,0) se superpondría a CASA H en las mismas celdas
        with pytest.raises(EditorCrucigramaError) as e:
            validar_posicion([_colocada("CASA", 0, 0, "H")], "SOL", 0, 0, "H")
        assert "paralela" in str(e.value)


class TestAntiFantasma:
    def test_paralela_adyacente_rechazada(self):
        # CARTA H (0,0) + ALTO V (0,1). PLATO H (1,0) cruza ALTO en la L
        # (cruce perpendicular legítimo) pero su celda (1,0) queda
        # paralela-adyacente a CARTA (fila 0) -> palabra fantasma.
        colocadas = [
            _colocada("CARTA", 0, 0, "H"),
            _colocada("ALTO", 0, 1, "V"),
        ]
        with pytest.raises(EditorCrucigramaError) as e:
            validar_posicion(colocadas, "PLATO", 1, 0, "H")
        assert "fantasma" in str(e.value).lower()

    def test_extension_en_linea_rechazada(self):
        # CASA H (0,0) cruza MAR V (0,3) en la A; RELOJ V (0,5) cruza ARMA:
        # ARMA H (0,4) cruza RELOJ en la R (índice 1, conectividad + letra ok)
        # pero su celda anterior (0,3) está ocupada por MAR -> se pegaría en
        # línea (palabra fantasma). El set está conectado (todas las palabras
        # cruzan >= 1), así la rama que se ejercita es la anti-fantasma, no la
        # red anti-desconexión (D4, que corre primero).
        colocadas = [
            _colocada("CASA", 0, 0, "H"),
            _colocada("MAR", 0, 3, "V"),
            _colocada("RELOJ", 0, 5, "V"),
        ]
        with pytest.raises(EditorCrucigramaError) as e:
            validar_posicion(colocadas, "ARMA", 0, 4, "H")
        assert "fantasma" in str(e.value).lower()

    def test_cruce_perpendicular_legitimo_no_marcado_como_fantasma(self):
        # SOL V en (0,2) cruza CASA en la S: la celda perpendicular adyacente
        # corresponde a un cruce real -> no es fantasma.
        validar_posicion([_colocada("CASA", 0, 0, "H")], "SOL", 0, 2, "V")


class TestConectividad:
    def test_primera_palabra_flota(self):
        validar_posicion([], "CASA", 4, 7, "H")

    # REVISIÓN 9.x (opción C): la conectividad global dejó de exigirse. Una
    # palabra posterior puede quedar como componente separado y el orden de
    # inserción es irrelevante (D4 REVISADO).
    def test_palabra_posterior_sin_cruces_aceptada(self):
        validar_posicion([_colocada("CASA", 0, 0, "H")], "SOL", 5, 5, "V")

    def test_movida_sin_cruce_aceptada(self):
        # Movida: la palabra que se mueve NO está en `colocadas` (se valida
        # contra las demás). Mover SOL lejos de CASA deja el puzzle sin cruce
        # -> aceptado (componente separado).
        validar_posicion([_colocada("CASA", 0, 0, "H")], "SOL", 9, 9, "V")

    def test_movida_que_desconecta_otra_palabra_aceptada(self):
        # PATO H (0,0) era el único cruce de AS V (0,1) y ORO V (0,3). Moverlo
        # a (4,0) — celda limpia, sin choques ni fantasmas — deja a AS y ORO
        # sin ningún cruce -> aceptado: no se re-valida la conectividad global
        # del puzzle (D4 REVISADO). NOTA: la posición original del caso (2,0)
        # quedó inválida por la anti-fantasma (la A de PATO en (2,1) genera
        # "SA" vertical con la S de AS en (1,1)), NO por desconexión.
        colocadas = [
            _colocada("ORO", 0, 3, "V"),
            _colocada("AS", 0, 1, "V"),
        ]
        validar_posicion(colocadas, "PATO", 4, 0, "H")

    def test_coordenadas_negativas_aceptadas(self):
        # REVISIÓN 9.x: el editor acepta coordenadas negativas (solo
        # crucigrama). Caso real del PO: ESPEJO H en (0,0) + ARENA V anclada
        # en (-2,0) — la E de ARENA (índice 2) coincide con la E de ESPEJO y
        # la palabra se extiende hacia filas negativas. Sin el fix, el 422
        # (ge=0) bloqueaba este cruce válido.
        validar_posicion([_colocada("ESPEJO", 0, 0, "H")], "ARENA", -2, 0, "V")

    def test_coordenadas_negativas_sin_chocar_aceptadas(self):
        # Palabra aislada en fila negativa y sin compartir celdas: la
        # colocación libre (D4 REVISADO) la acepta igual aunque quede como
        # componente separado.
        validar_posicion([_colocada("CASA", 0, 0, "H")], "ARENA", -2, 9, "V")

    def test_orientacion_invalida_error(self):
        with pytest.raises(EditorCrucigramaError) as e:
            validar_posicion([], "CASA", 0, 0, "SE")
        assert "H o V" in str(e.value)


class TestConstruirLayout:
    def test_bounding_box_traslacion_numeracion_y_shape_d6(self):
        colocadas = [
            _colocada("CASA", 1, 1, "H"),
            _colocada("SOL", 1, 3, "V"),
        ]
        grilla, posiciones = construir_layout(colocadas)

        # Traslación a (0,0): min fila/col era (1,1)
        assert posiciones["CASA"] == {
            "fila": 0,
            "columna": 0,
            "orientacion": "H",
            "numero": 1,
        }
        assert posiciones["SOL"] == {
            "fila": 0,
            "columna": 2,
            "orientacion": "V",
            "numero": 2,
        }

        # Shape D6 exacto
        assert set(grilla.keys()) == {"celdas", "palabras"}
        assert len(grilla["celdas"]) == 3 * 4  # filas x columnas
        for celda in grilla["celdas"]:
            assert set(celda.keys()) == {"letra", "numero", "tipo"}
            assert celda["tipo"] in {"letra", "negra"}

        # Celdas conocidas de la grilla normalizada
        assert grilla["celdas"][0] == {"letra": "C", "numero": 1, "tipo": "letra"}
        assert grilla["celdas"][2] == {"letra": "S", "numero": 2, "tipo": "letra"}
        assert grilla["celdas"][6] == {"letra": "O", "numero": None, "tipo": "letra"}
        assert sum(1 for c in grilla["celdas"] if c["tipo"] == "negra") == 6

        # Palabras ordenadas por número (barrido fila-major: CASA antes que SOL)
        assert [w["texto"] for w in grilla["palabras"]] == ["CASA", "SOL"]
        casa, sol = grilla["palabras"]
        assert casa["numero"] == 1 and casa["orientacion"] == "H"
        assert casa["posicion"] == {"fila": 0, "columna": 0}
        assert casa["longitud"] == 4
        assert sol["numero"] == 2 and sol["orientacion"] == "V"
        assert sol["posicion"] == {"fila": 0, "columna": 2}

    # REVISIÓN 9.x (opción C): un layout con componentes separados es válido
    # (D4 REVISADO). La numeración fila-major es consistente sin conectividad;
    # la grilla se construye por bounding box (traslación a (0,0)).
    def test_layout_desconectado_aceptado(self):
        colocadas = [
            _colocada("CASA", 0, 0, "H"),
            _colocada("SOL", 5, 5, "V"),
        ]
        grilla, posiciones = construir_layout(colocadas)
        assert posiciones["CASA"]["fila"] == 0
        assert posiciones["CASA"]["columna"] == 0
        # SOL se traslada al bounding box mínimo: (5,5) - (0,0) = (5,5)
        assert posiciones["SOL"]["fila"] == 5
        assert posiciones["SOL"]["columna"] == 5
        assert grilla is not None

    def test_layout_que_excede_grilla_maxima_error(self):
        # Una palabra vertical de 31 celdas hace que el bbox (31 filas)
        # exceda GRILLA_MAXIMA (30): el error del generador se propaga como
        # EditorCrucigramaError.
        colocadas = [_colocada("A" * 31, 0, 0, "V")]
        with pytest.raises(EditorCrucigramaError):
            construir_layout(colocadas)


class TestRegresionRefactor:
    def test_construir_layout_delega_en_construir_grilla_del_generador(self):
        colocadas = [
            _colocada("CASA", 1, 1, "H"),
            _colocada("SOL", 1, 3, "V"),
        ]
        g_generador, p_generador = construir_grilla(colocadas)
        g_editor, p_editor = construir_layout(colocadas)
        assert g_editor == g_generador
        assert p_editor == p_generador

    def test_celdas_ocupadas_mantienen_la_misma_letra(self):
        colocadas = [
            _colocada("CASA", 0, 0, "H"),
            _colocada("SOL", 0, 2, "V"),
        ]
        grilla, _ = construir_layout(colocadas)
        mapa = {}
        for w in colocadas:
            for f, c, letra in _celdas_de_palabra(
                w["posicion"]["fila"], w["posicion"]["columna"], w["orientacion"], w["palabra"]
            ):
                mapa[(f, c)] = letra
        for f in range(3):
            for c in range(4):
                celda = grilla["celdas"][f * 4 + c]
                if (f, c) in mapa:
                    assert celda["letra"] == mapa[(f, c)]