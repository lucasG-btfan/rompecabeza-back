from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import uuid
import random
import string

from app.database import get_db
from app.auth import get_usuario_actual, get_usuario_opcional
from app.models.partida import Partida
from app.models.palabra import Palabra
from app.models.usuario import Usuario
from app.models.participacion import Participacion
from app.schemas.partida import (
    CrearPartidaRequest,
    CrearPartidaResponse,
    PartidaResponse,
    AgregarPalabrasRequest,
    PosicionUpdate,
    EdicionRequest,
    FinalizarResponse,
    EstadoPalabraResponse,
    EstadoPartidaResponse,
    EncontradaRequest,
    EncontradaResponse,
    PalabraResponse,
    PartidaPublicaResponse,
    PalabraPublicaResponse,
)
from app.schemas.usuario import RankingEntry, UnirseResponse
from app.services.sopa_generator import (
    generar_sopa,
    calcular_celda_final,
    SopaGeneratorError,
    DIRECCIONES,
)

router = APIRouter(tags=["partidas"])

# Helpers

def _get_partida_o_404(db: Session, codigo: str) -> Partida:
    partida = db.query(Partida).filter(Partida.codigo == codigo).first()
    if not partida:
        raise HTTPException(status_code=404, detail="Partida no encontrada")
    return partida


def _get_palabra_o_404(db: Session, partida: Partida, palabra_id: uuid.UUID) -> Palabra:
    palabra = (
        db.query(Palabra)
        .filter(Palabra.id == palabra_id, Palabra.partida_id == partida.id)
        .first()
    )
    if not palabra:
        raise HTTPException(status_code=404, detail="Palabra no encontrada en esta partida")
    return palabra


def _requerir_creador(partida: Partida, usuario: Usuario) -> None:
    if partida.creador_id != usuario.id:
        raise HTTPException(
            status_code=403,
            detail="Solo el usuario que creó la partida puede hacer esto",
        )


def _estado_response(partida: Partida) -> EstadoPartidaResponse:
    palabras_estado = [
        EstadoPalabraResponse(
            id=p.id,
            palabra=p.palabra,
            encontrada=p.encontrada,
            posicion=p.posicion if p.encontrada else None,
        )
        for p in partida.palabras
    ]
    return EstadoPartidaResponse(
        codigo=partida.codigo,
        tipo=partida.tipo,
        estado=partida.estado,
        grilla=partida.grilla,
        palabras=palabras_estado,
    )


def _get_o_crear_participacion(
    db: Session, partida: Partida, usuario: Usuario, rol: str = "jugador"
) -> Participacion:
    """Auto-join perezoso: la primera vez que un usuario logueado interactúa
    con la partida (unirse o marcar una palabra), se le crea su fila."""
    participacion = (
        db.query(Participacion)
        .filter(Participacion.partida_id == partida.id, Participacion.usuario_id == usuario.id)
        .first()
    )
    if participacion:
        return participacion

    participacion = Participacion(
        id=uuid.uuid4(),
        partida_id=partida.id,
        usuario_id=usuario.id,
        rol=rol,
        iniciado_en=datetime.now(timezone.utc),
    )
    db.add(participacion)
    db.flush()
    return participacion


def _calcular_puntaje(palabras_encontradas: int, tiempo_segundos: Optional[int]) -> int:
    """
    Fórmula simple y ajustable: 100 puntos por palabra, menos 1 punto por
    segundo tardado, con un piso de 10 puntos por palabra (para que tardar
    mucho nunca deje el puntaje en 0 o negativo si de hecho encontró algo).
    """
    base = palabras_encontradas * 100
    piso = palabras_encontradas * 10
    if tiempo_segundos is None:
        return base
    return max(base - tiempo_segundos, piso)


def generar_codigo(db: Session) -> str:
    """Genera un codigo unico de 6 caracteres."""
    while True:
        codigo = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        existing = db.query(Partida).filter(Partida.codigo == codigo).first()
        if not existing:
            return codigo


# Endpoints existentes

@router.post("/partidas", response_model=CrearPartidaResponse, status_code=201)
def crear_partida(
    req: CrearPartidaRequest,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    """Crear una partida requiere estar logueado (necesitamos creador_id para ownership)."""
    codigo = generar_codigo(db)

    partida = Partida(
        id=uuid.uuid4(),
        codigo=codigo,
        tipo=req.tipo,
        config=req.config or {},
        estado="creando",
        creador_id=usuario.id,
    )
    db.add(partida)
    db.flush()  

    for p in req.palabras:
        palabra = Palabra(
            id=uuid.uuid4(),
            partida_id=partida.id,
            palabra=p.palabra.upper(),
            explicacion=p.explicacion,
        )
        db.add(palabra)

    # El creador también queda como participación (rol='creador'), útil si
    # después quiere jugar su propia partida o si querés listar "mis partidas".
    participacion = Participacion(
        id=uuid.uuid4(),
        partida_id=partida.id,
        usuario_id=usuario.id,
        rol="creador",
    )
    db.add(participacion)

    db.commit()
    db.refresh(partida)

    return CrearPartidaResponse(
        id=partida.id,
        codigo=partida.codigo,
        tipo=partida.tipo,
        estado=partida.estado,
    )


@router.get("/partidas/{codigo}", response_model=PartidaPublicaResponse)
def obtener_partida(codigo: str, db: Session = Depends(get_db)):
    """
    Vista pública de la partida. NO expone `posicion` de palabras todavía no
    encontradas (ver hallazgo de seguridad: antes este endpoint sí las filtraba).
    Accesible sin login: cualquiera con el código puede ver/jugar (soporta invitados).
    """
    partida = _get_partida_o_404(db, codigo)

    palabras = [
        PalabraPublicaResponse(
            id=p.id,
            palabra=p.palabra,
            explicacion=p.explicacion,
            posicion=p.posicion if p.encontrada else None,
            encontrada=p.encontrada,
        )
        for p in partida.palabras
    ]

    return PartidaPublicaResponse(
        id=partida.id,
        codigo=partida.codigo,
        tipo=partida.tipo,
        estado=partida.estado,
        palabras=palabras,
        config=partida.config,
        creado_en=partida.creado_en,
    )


# Endpoints de creador (requieren ownership)

@router.post("/partidas/{codigo}/palabras", response_model=list[PalabraResponse], status_code=201)
def agregar_palabras(
    codigo: str,
    req: AgregarPalabrasRequest,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    """Agrega más palabras a una partida que todavía está en estado 'creando'. Solo el creador."""
    partida = _get_partida_o_404(db, codigo)
    _requerir_creador(partida, usuario)

    if partida.estado != "creando":
        raise HTTPException(
            status_code=400,
            detail="Solo se pueden agregar palabras mientras la partida está en estado 'creando'",
        )

    nuevas = []
    for p in req.palabras:
        palabra = Palabra(
            id=uuid.uuid4(),
            partida_id=partida.id,
            palabra=p.palabra.upper(),
            explicacion=p.explicacion,
        )
        db.add(palabra)
        nuevas.append(palabra)

    db.commit()
    for p in nuevas:
        db.refresh(p)
    return nuevas


@router.put("/partidas/{codigo}/palabras/{palabra_id}/posicion", response_model=PalabraResponse)
def posicionar_palabra(
    codigo: str,
    palabra_id: uuid.UUID,
    req: PosicionUpdate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    """Posiciona manualmente una palabra en la grilla (override del creador). Solo el creador."""
    partida = _get_partida_o_404(db, codigo)
    _requerir_creador(partida, usuario)
    palabra = _get_palabra_o_404(db, partida, palabra_id)

    if req.orientacion not in DIRECCIONES:
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


@router.post("/partidas/{codigo}/finalizar", response_model=FinalizarResponse)
def finalizar_partida(
    codigo: str,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    """Genera automáticamente la sopa de letras y pasa la partida a estado 'activo'. Solo el creador."""
    partida = _get_partida_o_404(db, codigo)
    _requerir_creador(partida, usuario)

    if partida.tipo != "sopa":
        raise HTTPException(
            status_code=400,
            detail="Este endpoint solo genera Sopa de Letras. Para crucigrama todavía no está implementado.",
        )
    if partida.estado != "creando":
        raise HTTPException(status_code=400, detail="La partida ya fue finalizada")
    if not partida.palabras:
        raise HTTPException(status_code=400, detail="La partida no tiene palabras cargadas")

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

    return _estado_response(partida)


# Endpoints de juego (abiertos a invitados)

@router.get("/partidas/{codigo}/estado", response_model=EstadoPartidaResponse)
def obtener_estado(codigo: str, db: Session = Depends(get_db)):
    """Devuelve la grilla actual y el estado de cada palabra (sin revelar posiciones no encontradas)."""
    partida = _get_partida_o_404(db, codigo)
    return _estado_response(partida)


@router.post("/partidas/{codigo}/unirse", response_model=UnirseResponse)
def unirse_partida(
    codigo: str,
    db: Session = Depends(get_db),
    usuario: Optional[Usuario] = Depends(get_usuario_opcional),
):
    """
    Llamar al entrar a jugar: arranca el cronómetro de la participación (para
    el puntaje por tiempo). Funciona para invitados también, pero para ellos
    no se persiste nada -- el modo 'invitado' es solo informativo.
    """
    partida = _get_partida_o_404(db, codigo)

    if usuario is None:
        return UnirseResponse(modo="invitado")

    participacion = _get_o_crear_participacion(db, partida, usuario, rol="jugador")
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
    de la palabra y, si coincide (en cualquiera de los dos sentidos), la marca como
    encontrada. Si hay un usuario logueado, se le atribuye el hallazgo y se actualiza
    su puntaje; si es invitado, se marca igual pero sin atribución ni puntaje.
    """
    partida = _get_partida_o_404(db, codigo)
    palabra = _get_palabra_o_404(db, partida, palabra_id)

    if partida.estado != "activo":
        raise HTTPException(status_code=400, detail="La partida todavía no está activa")
    if palabra.encontrada:
        return EncontradaResponse(encontrada=True, posicion=palabra.posicion)
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

    ahora = datetime.now(timezone.utc)
    palabra.encontrada = True
    palabra.encontrada_en = ahora

    if usuario is not None:
        palabra.encontrada_por = usuario.id

        participacion = _get_o_crear_participacion(db, partida, usuario, rol="jugador")
        participacion.palabras_encontradas += 1

        total_palabras = len(partida.palabras)
        if participacion.palabras_encontradas >= total_palabras and participacion.finalizado_en is None:
            participacion.finalizado_en = ahora

    db.commit()
    db.refresh(palabra)

    return EncontradaResponse(encontrada=True, posicion=palabra.posicion)


@router.get("/partidas/{codigo}/ranking", response_model=list[RankingEntry])
def obtener_ranking(codigo: str, db: Session = Depends(get_db)):
    """Tabla de puntajes de la partida, ordenada de mayor a menor. Solo incluye jugadores registrados."""
    partida = _get_partida_o_404(db, codigo)

    entradas = []
    for participacion in partida.participaciones:
        tiempo_segundos = None
        if participacion.iniciado_en and participacion.finalizado_en:
            delta = participacion.finalizado_en - participacion.iniciado_en
            tiempo_segundos = int(delta.total_seconds())

        entradas.append(
            RankingEntry(
                username=participacion.usuario.username,
                rol=participacion.rol,
                palabras_encontradas=participacion.palabras_encontradas,
                tiempo_segundos=tiempo_segundos,
                puntaje=_calcular_puntaje(participacion.palabras_encontradas, tiempo_segundos),
            )
        )

    entradas.sort(key=lambda e: e.puntaje, reverse=True)
    return entradas
