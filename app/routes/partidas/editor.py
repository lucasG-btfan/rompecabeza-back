from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import uuid

from app.database import get_db
from app.auth import get_usuario_actual
from app.models.partida import Partida
from app.models.usuario import Usuario
from app.schemas.partida import (
    PosicionUpdate,
    PalabraResponse,
    EditorPartidaResponse,
    FinalizarRequest,
    FinalizarResponse,
    EdicionRequest,
    EstadoPartidaResponse,
    PalabraCreate,
)
from app.services.sopa_generator import (
    generar_sopa,
    SopaGeneratorError,
    DIRECCIONES,
)
from app.services.crucigrama_generator import (
    ORIENTACIONES_CRUCE,
    generar_crucigrama,
    CrucigramaGeneratorError,
)
from app.services.crucigrama_editor import (
    EditorCrucigramaError,
    construir_layout,
    validar_posicion,
)
from app.routes.partidas.deps import (
    _get_partida_o_404,
    _get_palabra_o_404,
    _requerir_creador,
    _validar_y_normalizar,
    _estado_response,
)

router = APIRouter(tags=["partidas"])


def _requerir_estado_creando(partida: Partida) -> None:
    if partida.estado != "creando":
        raise HTTPException(
            status_code=400,
            detail="Solo se pueden modificar palabras mientras la partida está en estado 'creando'",
        )


def _dimensiones_layout(posiciones: dict[str, dict]) -> tuple[int, int]:
    """Ancho y alto del bbox que ocupan las palabras del crucigrama."""
    filas = max(
        pos["fila"] + (len(palabra) if pos["orientacion"] == "V" else 1)
        for palabra, pos in posiciones.items()
    )
    columnas = max(
        pos["columna"] + (len(palabra) if pos["orientacion"] == "H" else 1)
        for palabra, pos in posiciones.items()
    )
    return filas, columnas


@router.put("/partidas/{codigo}/palabras/{palabra_id}/posicion", response_model=PalabraResponse)
def posicionar_palabra(
    codigo: str,
    palabra_id: uuid.UUID,
    req: PosicionUpdate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    """Posiciona manualmente una palabra en la grilla (override del creador). Solo el creador.

    - `sopa`: valida contra las 8 direcciones y rechaza coordenadas negativas
      (comportamiento histórico intacto; la relajación de `ge=0` es SOLO
      crucigrama).
    - `crucigrama` (C-09): exige estado 'creando' y orientación H/V, y corre la
      díada de validación del editor (cruce con letra coincidente anti-paralela,
      anti-fantasma; SIN conectividad obligatoria — opción C, REVISIÓN 9.x).
      Acepta coordenadas negativas (D1 REVISADO: la palabra se extiende hacia
      arriba/izquierda; `construir_grilla` traslada al bbox mínimo en finalizar).
      Persiste `{fila, columna, orientacion}` SIN `numero` (la numeración se
      asigna en `finalizar`, cuando el layout está completo).
    """
    partida = _get_partida_o_404(db, codigo)
    _requerir_creador(partida, usuario)
    palabra = _get_palabra_o_404(db, partida, palabra_id)

    if partida.tipo == "crucigrama":
        if partida.estado != "creando":
            raise HTTPException(
                status_code=400,
                detail="Solo se pueden posicionar palabras durante la creación",
            )
        if req.orientacion not in ORIENTACIONES_CRUCE:
            raise HTTPException(
                status_code=400,
                detail="Para crucigrama la orientación debe ser H o V",
            )

        colocadas = [
            {
                "palabra": p.palabra,
                "posicion": {
                    "fila": p.posicion["fila"],
                    "columna": p.posicion["columna"],
                },
                "orientacion": p.posicion["orientacion"],
            }
            for p in partida.palabras
            if p.id != palabra.id and p.posicion
        ]
        try:
            validar_posicion(
                colocadas,
                palabra.palabra,
                req.fila,
                req.columna,
                req.orientacion,
            )
        except EditorCrucigramaError as e:
            raise HTTPException(status_code=400, detail=str(e))
    elif req.fila < 0 or req.columna < 0:
        # REVISIÓN 9.x (D1): la relajación de `ge=0` aplica SOLO a crucigrama.
        # La sopa no tiene otra cota de límites: validación explícita acá para
        # preservar el comportamiento previo (antes lo hacía el schema con 422).
        raise HTTPException(
            status_code=400,
            detail="las coordenadas deben ser no negativas",
        )
    elif req.orientacion not in DIRECCIONES:
        raise HTTPException(
            status_code=400,
            detail=f"Orientación inválida. Usar una de: {list(DIRECCIONES.keys())}",
        )

    palabra.posicion = {
        "fila": req.fila,
        "columna": req.columna,
        "orientacion": req.orientacion,
    }
    db.commit()
    db.refresh(palabra)
    return palabra


@router.delete("/partidas/{codigo}/palabras/{palabra_id}/posicion", response_model=PalabraResponse)
def quitar_posicion_palabra(
    codigo: str,
    palabra_id: uuid.UUID,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    """Quita la posición manual de una palabra del editor (C-11, BUG 2 QA):
    vuelve a `posicion: null` para reposicionarla o dejar que `finalizar` la
    genere automáticamente. Mismas guardas que el PUT posicion: solo el
    creador y, para crucigrama, solo en estado 'creando'."""
    partida = _get_partida_o_404(db, codigo)
    _requerir_creador(partida, usuario)
    palabra = _get_palabra_o_404(db, partida, palabra_id)

    if partida.tipo == "crucigrama" and partida.estado != "creando":
        raise HTTPException(
            status_code=400,
            detail="Solo se pueden quitar posiciones durante la creación",
        )

    palabra.posicion = None
    db.commit()
    db.refresh(palabra)
    return palabra


@router.get("/partidas/{codigo}/editor", response_model=EditorPartidaResponse)
def obtener_editor_partida(
    codigo: str,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    """Estado del editor manual del crucigrama (C-09), SOLO para el creador.

    A diferencia del GET público y del /estado (que ocultan `posicion` por
    anti-cheat), aquí la posición es SIEMPRE visible: el creador está armando
    el layout y necesita ver dónde quedó cada palabra (null si todavía no la
    posicionó). No expone `grilla` (no existe pre-finalizar; el editor la
    deriva de las posiciones).
    """
    partida = _get_partida_o_404(db, codigo)
    _requerir_creador(partida, usuario)

    palabras = [
        PalabraResponse(
            id=p.id,
            palabra=p.palabra,
            texto_mostrar=p.texto_mostrar,
            explicacion=p.explicacion,
            posicion=p.posicion,
            encontrada=p.encontrada,
        )
        for p in partida.palabras
    ]
    return EditorPartidaResponse(
        codigo=partida.codigo,
        tipo=partida.tipo,
        estado=partida.estado,
        palabras=palabras,
    )


@router.post("/partidas/{codigo}/finalizar", response_model=FinalizarResponse)
def finalizar_partida(
    codigo: str,
    req: Optional[FinalizarRequest] = None,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    """Genera automáticamente la sopa de letras o el crucigrama y pasa la
    partida a estado 'activo'. Solo el creador."""
    partida = _get_partida_o_404(db, codigo)
    _requerir_creador(partida, usuario)

    if partida.estado != "creando":
        raise HTTPException(status_code=400, detail="La partida ya fue finalizada")
    if not partida.palabras:
        raise HTTPException(status_code=400, detail="La partida no tiene palabras cargadas")

    if partida.tipo == "sopa":
        palabras_texto = [p.palabra for p in partida.palabras]
        config = partida.config or {}
        filas = config.get("filas")
        columnas = config.get("columnas")

        try:
            grid, posiciones = generar_sopa(palabras_texto, filas=filas, columnas=columnas)
        except SopaGeneratorError as e:
            raise HTTPException(status_code=400, detail=str(e))

        for p in partida.palabras:
            p.posicion = posiciones[p.palabra]

        partida.grilla = grid
        partida.estado = "activo"

        db.commit()
        db.refresh(partida)

        return FinalizarResponse(
            codigo=partida.codigo,
            estado=partida.estado,
            filas=len(grid),
            columnas=len(grid[0]),
        )

    if partida.tipo == "crucigrama":
        if len(partida.palabras) < 2:
            raise HTTPException(
                status_code=400,
                detail="Un crucigrama requiere al menos dos palabras que compartan letras",
            )

        faltan_pistas = [
            p.texto_mostrar or p.palabra
            for p in partida.palabras
            if not (p.explicacion and p.explicacion.strip())
        ]
        if faltan_pistas:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Las palabras deben tener una pista (explicación) para generar un crucigrama. "
                    f"Faltan: {', '.join(faltan_pistas)}"
                ),
            )

        # C-09: layout manual vs generación automática (todas o ninguna).
        posicionadas = [p for p in partida.palabras if p.posicion]
        if posicionadas and len(posicionadas) != len(partida.palabras):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Todas o ninguna palabra debe estar posicionada: no se mezcla "
                    "el layout manual con la generación automática"
                ),
            )

        if posicionadas:
            colocadas = [
                {
                    "palabra": p.palabra,
                    "posicion": {
                        "fila": p.posicion["fila"],
                        "columna": p.posicion["columna"],
                    },
                    "orientacion": p.posicion["orientacion"],
                }
                for p in partida.palabras
            ]
            try:
                grilla_data, posiciones = construir_layout(colocadas)
            except EditorCrucigramaError as e:
                raise HTTPException(status_code=400, detail=str(e))

            for p in partida.palabras:
                # Re-escribe la posición normalizada (traslación a 0,0) con numero.
                p.posicion = posiciones[p.palabra]

            partida.grilla = grilla_data
            partida.estado = "activo"

            filas, columnas = _dimensiones_layout(posiciones)

            db.commit()
            db.refresh(partida)

            return FinalizarResponse(
                codigo=partida.codigo,
                estado=partida.estado,
                filas=filas,
                columnas=columnas,
                grilla=grilla_data,
            )

        palabras_texto = [p.palabra for p in partida.palabras]
        try:
            grilla_data, posiciones = generar_crucigrama(palabras_texto)
        except CrucigramaGeneratorError as e:
            raise HTTPException(status_code=400, detail=str(e))

        for p in partida.palabras:
            p.posicion = posiciones[p.palabra]

        partida.grilla = grilla_data
        partida.estado = "activo"

        filas, columnas = _dimensiones_layout(posiciones)

        db.commit()
        db.refresh(partida)

        return FinalizarResponse(
            codigo=partida.codigo,
            estado=partida.estado,
            filas=filas,
            columnas=columnas,
            grilla=grilla_data,
        )

    raise HTTPException(
        status_code=400,
        detail=f"Tipo de partida no soportado: {partida.tipo}",
    )


@router.put("/partidas/{codigo}/ediciones", response_model=EstadoPartidaResponse)
def editar_letra(
    codigo: str,
    req: EdicionRequest,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    """
    Permite al creador editar manualmente una letra de la grilla ya generada.
    Solo el creador, y SOLO si todavía nadie encontró ninguna palabra: una vez
    que el puntaje de algún jugador empezó a depender de la grilla, se congela
    para no invalidar resultados ya generados.
    """
    partida = _get_partida_o_404(db, codigo)
    _requerir_creador(partida, usuario)

    if partida.tipo == "crucigrama":
        raise HTTPException(
            status_code=400,
            detail="La edición de celdas solo está disponible para sopa de letras",
        )

    if not partida.grilla:
        raise HTTPException(
            status_code=400,
            detail="La partida todavía no tiene grilla generada. Llamá primero a /finalizar",
        )

    if any(p.encontrada for p in partida.palabras):
        raise HTTPException(
            status_code=400,
            detail="Ya hay palabras encontradas en esta partida: la grilla queda congelada para no alterar puntajes.",
        )

    filas = len(partida.grilla)
    columnas = len(partida.grilla[0])
    if not (0 <= req.fila < filas and 0 <= req.columna < columnas):
        raise HTTPException(status_code=400, detail="Celda fuera de la grilla")

    # Copiamos la grilla para que SQLAlchemy detecte el cambio en la columna JSON
    nueva_grilla = [fila[:] for fila in partida.grilla]
    nueva_grilla[req.fila][req.columna] = req.letra.upper()
    partida.grilla = nueva_grilla

    db.commit()
    db.refresh(partida)

    return _estado_response(db, partida)


@router.put(
    "/partidas/{codigo}/palabras/{palabra_id}",
    response_model=PalabraResponse,
)
def editar_palabra(
    codigo: str,
    palabra_id: uuid.UUID,
    req: PalabraCreate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    """Cambia el texto (y/o explicación) de una palabra. Solo el creador, y solo
    mientras la partida está en 'creando' (una vez finalizada, la grilla ya se generó
    con esas palabras y no se pueden tocar)."""
    partida = _get_partida_o_404(db, codigo)
    _requerir_creador(partida, usuario)
    _requerir_estado_creando(partida)
    palabra = _get_palabra_o_404(db, partida, palabra_id)

    limpia, mostrar = _validar_y_normalizar(db, partida, req.palabra, excluir_id=palabra.id)
    palabra.palabra = limpia
    palabra.texto_mostrar = mostrar
    palabra.explicacion = req.explicacion
    db.commit()
    db.refresh(palabra)
    return palabra


@router.delete("/partidas/{codigo}/palabras/{palabra_id}", status_code=204)
def eliminar_palabra(
    codigo: str,
    palabra_id: uuid.UUID,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    """Elimina una palabra de la partida. Solo el creador, y solo en 'creando'."""
    partida = _get_partida_o_404(db, codigo)
    _requerir_creador(partida, usuario)
    _requerir_estado_creando(partida)
    palabra = _get_palabra_o_404(db, partida, palabra_id)

    db.delete(palabra)
    db.commit()