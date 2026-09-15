from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import uuid

from app.database import get_db
from app.auth import get_usuario_opcional
from app.models.partida import Partida
from app.models.palabra import Palabra
from app.models.usuario import Usuario
from app.models.participacion import Participacion
from app.models.hallazgo import Hallazgo
from app.schemas.partida import (
    EstadoPartidaResponse,
    EncontradaRequest,
    EncontradaResponse,
    RespuestaRequest,
)
from app.schemas.usuario import UnirseResponse
from app.services.sopa_generator import calcular_celda_final
from app.services.texto import limpiar_para_grilla
from app.routes.partidas.deps import (
    _get_partida_o_404,
    _get_palabra_o_404,
    _es_creador,
    _estado_response,
    _get_o_crear_participacion,
)

router = APIRouter(tags=["partidas"])


def _registrar_hallazgo(
    db: Session,
    participacion: Participacion,
    partida: Partida,
    palabra: Palabra,
    ahora: datetime,
) -> None:
    """Registra el hallazgo de una palabra para una participación (idempotente)
    y avanza el contador, marcando la finalización si se completaron todas."""
    ya_encontrada = (
        db.query(Hallazgo)
        .filter(
            Hallazgo.participacion_id == participacion.id,
            Hallazgo.palabra_id == palabra.id,
        )
        .first()
    )
    if ya_encontrada is None:
        db.add(
            Hallazgo(
                id=uuid.uuid4(),
                participacion_id=participacion.id,
                palabra_id=palabra.id,
                posicion=palabra.posicion,
                encontrado_en=ahora,
            )
        )
        participacion.palabras_encontradas += 1

        total_palabras = len(partida.palabras)
        if participacion.palabras_encontradas >= total_palabras and participacion.finalizado_en is None:
            participacion.finalizado_en = ahora


@router.get("/partidas/{codigo}/estado", response_model=EstadoPartidaResponse)
def obtener_estado(
    codigo: str,
    db: Session = Depends(get_db),
    usuario: Optional[Usuario] = Depends(get_usuario_opcional),
):
    """Devuelve la grilla actual y el estado de cada palabra PARA EL JUGADOR
    que consulta. Cada jugador ve su propio progreso (las palabras que encontró),
    no el global. Los invitados ven todo vacío (su progreso va por localStorage).
    No se revelan posiciones de palabras que este jugador no encontró."""
    partida = _get_partida_o_404(db, codigo)

    participacion = None
    if usuario is not None:
        participacion = (
            db.query(Participacion)
            .filter(
                Participacion.partida_id == partida.id,
                Participacion.usuario_id == usuario.id,
            )
            .first()
        )

    return _estado_response(db, partida, participacion)


@router.post("/partidas/{codigo}/unirse", response_model=UnirseResponse)
def unirse_partida(
    codigo: str,
    db: Session = Depends(get_db),
    usuario: Optional[Usuario] = Depends(get_usuario_opcional),
):
    """
    Llamar al entrar a jugar: arranca el cronómetro de la participación
    (de donde sale el tiempo final de la pantalla de completado). Funciona
    para invitados también, pero para ellos no se persiste nada -- el modo
    'invitado' es solo informativo.
    """
    partida = _get_partida_o_404(db, codigo)

    # Solo se puede "unirse" (empezar a jugar / arrancar el cronómetro) a una
    # partida que ya fue publicada (estado 'activo'). Si la partida sigue en
    # 'creando' no hay sopa generada ni nada que jugar.
    if partida.estado != "activo":
        raise HTTPException(
            status_code=400,
            detail="La partida todavía no está activa",
        )

    if usuario is None:
        return UnirseResponse(modo="invitado")

    rol = "creador" if _es_creador(partida, usuario) else "jugador"
    participacion = _get_o_crear_participacion(db, partida, usuario, rol=rol)
    if participacion.iniciado_en is None:
        participacion.iniciado_en = datetime.now(timezone.utc)
    db.commit()
    db.refresh(participacion)

    return UnirseResponse(modo="registrado", iniciado_en=participacion.iniciado_en)


@router.put("/partidas/{codigo}/palabras/{palabra_id}/encontrada", response_model=EncontradaResponse)
def marcar_encontrada(
    codigo: str,
    palabra_id: uuid.UUID,
    req: EncontradaRequest,
    db: Session = Depends(get_db),
    usuario: Optional[Usuario] = Depends(get_usuario_opcional),
):
    """
    Valida la selección del jugador (celda inicial y final) contra la posición real
    de la palabra y, si coincide (en cualquiera de los dos sentidos), la registra
    como encontrada PARA ESA PARTICIPACIÓN.

    - Jugador logueado: se le crea su participación (si no existe) y se registra un
      hallazgo propio, avanzando SÓLO en su progreso. El creador juega igual.
    - Invitado: se valida la jugada y se devuelve encontrada=True, pero NO se persiste
      nada (no hay usuario que asociar) -- el front guarda su progreso en localStorage.
    """
    partida = _get_partida_o_404(db, codigo)
    palabra = _get_palabra_o_404(db, partida, palabra_id)

    if partida.estado != "activo":
        raise HTTPException(status_code=400, detail="La partida todavía no está activa")
    if not palabra.posicion:
        raise HTTPException(status_code=400, detail="La palabra no tiene posición asignada")

    fila_real = palabra.posicion["fila"]
    col_real = palabra.posicion["columna"]
    orientacion = palabra.posicion["orientacion"]
    fila_fin_real, col_fin_real = calcular_celda_final(
        fila_real, col_real, orientacion, len(palabra.palabra)
    )

    seleccion_directa = (
        req.fila_inicio == fila_real
        and req.columna_inicio == col_real
        and req.fila_fin == fila_fin_real
        and req.columna_fin == col_fin_real
    )
    seleccion_invertida = (
        req.fila_inicio == fila_fin_real
        and req.columna_inicio == col_fin_real
        and req.fila_fin == fila_real
        and req.columna_fin == col_real
    )

    if not (seleccion_directa or seleccion_invertida):
        raise HTTPException(status_code=400, detail="Selección incorrecta")

    # Invitado: validamos y devolvemos, pero no persistimos nada.
    if usuario is None:
        return EncontradaResponse(encontrada=True, posicion=palabra.posicion)

    # Jugador logueado: progreso PROPIO por participación (incluido el creador).
    ahora = datetime.now(timezone.utc)
    rol = "creador" if _es_creador(partida, usuario) else "jugador"
    participacion = _get_o_crear_participacion(db, partida, usuario, rol=rol)

    _registrar_hallazgo(db, participacion, partida, palabra, ahora)
    db.commit()
    db.refresh(palabra)

    return EncontradaResponse(encontrada=True, posicion=palabra.posicion)


@router.put(
    "/partidas/{codigo}/palabras/{palabra_id}/respuesta",
    response_model=EncontradaResponse,
)
def responder_palabra(
    codigo: str,
    palabra_id: uuid.UUID,
    req: RespuestaRequest,
    db: Session = Depends(get_db),
    usuario: Optional[Usuario] = Depends(get_usuario_opcional),
):
    """
    Validación de respuesta por palabra para CRUCIGRAMAS (C-10, D1):

    - Solo partidas de tipo 'crucigrama' (la sopa sigue usando la selección
      de celdas del endpoint /encontrada).
    - Solo en estado 'activo'. La palabra debe estar posicionada.
    - Las letras ingresadas se normalizan con `limpiar_para_grilla`
      (mayúsculas, sin acentos/espacios/símbolos) y se comparan contra la
      palabra real del crucigrama: si NO coinciden -> 400 (jugada válida,
      letras incorrectas). El front limpia SOLO esa palabra y deja reintentar.
    - Si coinciden: comportamiento idéntico a /encontrada — hallazgo PARA ESA
      PARTICIPACIÓN (progreso propio, idempotente) y finalización automática
      al completar todas. Invitado: se valida y responde sin persistir nada.
    """
    partida = _get_partida_o_404(db, codigo)
    palabra = _get_palabra_o_404(db, partida, palabra_id)

    if partida.tipo != "crucigrama":
        raise HTTPException(
            status_code=400,
            detail=(
                "El ingreso de letras es solo para crucigramas: usá el endpoint "
                "de palabra encontrada (/encontrada) para sopa"
            ),
        )
    if partida.estado != "activo":
        raise HTTPException(status_code=400, detail="La partida todavía no está activa")
    if not palabra.posicion:
        raise HTTPException(status_code=400, detail="La palabra no tiene posición asignada")

    # Normalización del input del jugador (misma regla que el editor: solo
    # importan las letras reales, en la grilla no hay espacios ni símbolos).
    letras_limpias = limpiar_para_grilla(req.letras)
    if letras_limpias != palabra.palabra:
        raise HTTPException(
            status_code=400,
            detail="Las letras no coinciden con la palabra del crucigrama",
        )

    # Invitado: validamos y devolvemos, pero no persistimos nada.
    if usuario is None:
        return EncontradaResponse(encontrada=True, posicion=palabra.posicion)

    # Jugador logueado: progreso PROPIO por participación (incluido el creador).
    ahora = datetime.now(timezone.utc)
    rol = "creador" if _es_creador(partida, usuario) else "jugador"
    participacion = _get_o_crear_participacion(db, partida, usuario, rol=rol)

    _registrar_hallazgo(db, participacion, partida, palabra, ahora)
    db.commit()
    db.refresh(palabra)

    return EncontradaResponse(encontrada=True, posicion=palabra.posicion)