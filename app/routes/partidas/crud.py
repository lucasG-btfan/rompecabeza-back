from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import uuid
import random
import string
from typing import Optional

from app.database import get_db
from app.auth import get_usuario_actual, get_usuario_opcional
from app.models.usuario import Usuario
from app.models.participacion import Participacion
from app.models.partida import Partida
from app.models.palabra import Palabra
from app.models.emparejamiento import Emparejamiento
from app.routes.emparejamientos import ESTADOS_ACTIVOS, _reciclar_esperas_vencidas
from app.schemas.partida import (
    CrearPartidaRequest,
    CrearPartidaResponse,
    PartidaPublicaResponse,
    AgregarPalabrasRequest,
    PalabraPublicaResponse,
    PalabraResponse,
    PalabraCreate,
    ResumenPartidaResponse,
    ActualizarNombrePartidaRequest,
)
from app.routes.partidas.deps import (
    _get_partida_o_404,
    _requerir_creador,
    _es_creador,
    _validar_y_normalizar,
)

router = APIRouter(tags=["partidas"])


def generar_codigo(db: Session) -> str:
    """Genera un codigo unico de 6 caracteres."""
    while True:
        codigo = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        existing = db.query(Partida).filter(Partida.codigo == codigo).first()
        if not existing:
            return codigo


def _en_duelo_de(db: Session, partida_id: uuid.UUID) -> bool:
    """C-17 (D10): la partida tiene un emparejamiento 1v1 activo."""
    return (
        db.query(Emparejamiento.id)
        .filter(
            Emparejamiento.partida_id == partida_id,
            Emparejamiento.estado.in_(ESTADOS_ACTIVOS),
        )
        .first()
        is not None
    )


def _resumen_partida(partida: Partida, en_duelo: bool = False) -> ResumenPartidaResponse:
    """Resumen de una partida para 'Mis partidas' y el PATCH de nombre (C-15/C-16)."""
    palabras = partida.palabras
    return ResumenPartidaResponse(
        id=partida.id,
        codigo=partida.codigo,
        tipo=partida.tipo,
        estado=partida.estado,
        creado_en=partida.creado_en,
        palabras_total=len(palabras),
        palabras_encontradas=sum(1 for p in palabras if p.encontrada),
        nombre=partida.nombre,
        en_duelo=en_duelo,
    )


@router.get("/partidas", response_model=list[ResumenPartidaResponse])
def listar_mis_partidas(
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    """
    Lista las partidas que el usuario logueado creó (participación con rol='creador'),
    ordenadas de más reciente a más antigua. Útil para la pantalla 'Mis partidas'.
    """
    participaciones = (
        db.query(Participacion)
        .filter(
            Participacion.usuario_id == usuario.id,
            Participacion.rol == "creador",
        )
        .order_by(Participacion.unido_en.desc())
        .all()
    )

    # D4: punto de lectura del duelo → reciclar esperas vencidas antes de
    # computar `en_duelo` (paridad con el lobby).
    _reciclar_esperas_vencidas(db)

    resultado = []
    for participacion in participaciones:
        partida = participacion.partida
        resultado.append(_resumen_partida(partida, _en_duelo_de(db, partida.id)))
    return resultado


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
        nombre=req.nombre,  # C-16 (D10): ya normalizado por el validador (None o trim)
    )
    db.add(partida)
    db.flush()  

    for p in req.palabras:
        limpia, mostrar = _validar_y_normalizar(db, partida, p.palabra)
        palabra = Palabra(
            id=uuid.uuid4(),
            partida_id=partida.id,
            palabra=limpia,
            texto_mostrar=mostrar,
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
        nombre=partida.nombre,  # C-16 (D11): nombre persistido en la creación
    )


@router.get("/partidas/{codigo}", response_model=PartidaPublicaResponse)
def obtener_partida(
    codigo: str,
    db: Session = Depends(get_db),
    usuario: Optional[Usuario] = Depends(get_usuario_opcional),
):
    """
    Vista pública de la partida. NO expone `posicion` de palabras todavía no
    encontradas (ver hallazgo de seguridad: antes este endpoint sí las filtraba).
    Accesible sin login: cualquiera con el código puede ver/jugar (soporta invitados).

    C-12 (D1): en un crucigrama la solución (`palabra`/`texto_mostrar`) solo se
    expone al creador autenticado — el editor (C-09) la necesita para mostrarla.
    Cualquier otro rol (jugador registrado distinto o invitado anónimo) la recibe
    `None`: la pista (`explicacion`) sigue siendo pública y la `posicion` de
    palabras encontradas respeta la regla previa. La sopa no filtra nada.

    C-12 (D1 REVISADO): `es_creador` viaja en la respuesta para que el front
    gatee la pantalla del editor sin necesidad de otro endpoint.
    """
    partida = _get_partida_o_404(db, codigo)
    # `_es_creador` asume un usuario autenticado (accede a `usuario.id`);
    # sin sesión no puede ser creador, nunca crashea.
    es_creador = usuario is not None and _es_creador(partida, usuario)
    ocultar_solucion = partida.tipo == "crucigrama" and not es_creador

    palabras = [
        PalabraPublicaResponse(
            id=p.id,
            palabra=None if ocultar_solucion else p.palabra,
            texto_mostrar=None if ocultar_solucion else p.texto_mostrar,
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
        es_creador=es_creador,
        nombre=partida.nombre,  # C-16: el GET público refleja el nombre persistido
    )


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
        limpia, mostrar = _validar_y_normalizar(db, partida, p.palabra)
        palabra = Palabra(
            id=uuid.uuid4(),
            partida_id=partida.id,
            palabra=limpia,
            texto_mostrar=mostrar,
            explicacion=p.explicacion,
        )
        db.add(palabra)
        nuevas.append(palabra)

    db.commit()
    for p in nuevas:
        db.refresh(p)
    return nuevas


@router.delete("/partidas/{codigo}", status_code=204)
def eliminar_partida(
    codigo: str,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    """Elimina una partida permanentemente. Solo el creador puede hacerlo."""
    partida = _get_partida_o_404(db, codigo)
    _requerir_creador(partida, usuario)

    db.delete(partida)
    db.commit()


@router.patch("/partidas/{codigo}/nombre", response_model=ResumenPartidaResponse)
def actualizar_nombre_partida(
    codigo: str,
    req: ActualizarNombrePartidaRequest,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    """Asigna/modifica/limpia el nombre de una partida (C-15). Solo el creador.

    El body llega normalizado por `ActualizarNombrePartidaRequest` (trim +
    vacío/whitespace → None, max 50, `extra='forbid'`). La respuesta es el
    resumen completo, paridad con 'Mis partidas'.
    """
    partida = _get_partida_o_404(db, codigo)
    _requerir_creador(partida, usuario)

    partida.nombre = req.nombre
    db.commit()
    db.refresh(partida)

    return _resumen_partida(partida, _en_duelo_de(db, partida.id))