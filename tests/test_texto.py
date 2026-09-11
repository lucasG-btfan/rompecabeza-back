import pytest

from app.services.texto import limpiar_para_grilla, texto_a_mostrar


class TestLimpiarParaGrilla:
    def test_quita_espacios_internos(self):
        assert limpiar_para_grilla("sr frio") == "SRFRIO"

    def test_quita_guiones(self):
        assert limpiar_para_grilla("co-autor") == "COAUTOR"

    def test_quita_apostrofes(self):
        assert limpiar_para_grilla("don't stop") == "DONTSTOP"

    def test_quita_puntos(self):
        assert limpiar_para_grilla("e.t.c") == "ETC"

    def test_pasa_a_mayusculas(self):
        assert limpiar_para_grilla("hola") == "HOLA"

    def test_conserva_letras_con_acento_y_enie(self):
        assert limpiar_para_grilla("mañana") == "MAÑANA"
        assert limpiar_para_grilla("menú") == "MENÚ"

    def test_vacia_si_solo_habia_separadores(self):
        assert limpiar_para_grilla(" - ") == ""

    def test_palabra_ya_limpia_queda_igual(self):
        assert limpiar_para_grilla("COAUTOR") == "COAUTOR"


class TestTextoAMostrar:
    def test_conserva_guiones(self):
        assert texto_a_mostrar("co-autor") == "CO-AUTOR"

    def test_normaliza_espacios_internos(self):
        assert texto_a_mostrar("sr   frio") == "SR FRIO"

    def test_pasa_a_mayusculas(self):
        assert texto_a_mostrar("hola") == "HOLA"

    def test_sin_separadores_es_igual_a_la_grilla(self):
        assert texto_a_mostrar("HOLA") == limpiar_para_grilla("HOLA")

    def test_conserva_guion_con_espacios_alrededor(self):
        assert texto_a_mostrar("co  -  autor") == "CO - AUTOR"

    def test_descarta_puntuacion_que_no_existe_en_la_grilla(self):
        assert texto_a_mostrar("el  zorro!") == "EL ZORRO"