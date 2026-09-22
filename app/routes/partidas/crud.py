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
from app.models.emparejamiento import Emparejamiento, EmparejamientoEstado
from app.routes.emparejamientos import _reciclar_esperas_vencidas
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
    while True:
        codigo = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        existing = db.query(Partida).filter(Partida.codigo == codigo).first()
        if not existing:
            return codigo


def _en_duelo_de(db: Session, partida_id: uuid.UUID) -> bool:
   
    return (
        db.query(Emparejamiento.id)
        .filter(
            Emparejamiento.partida_id == partida_id,
            Emparejamiento.estado == EmparejamientoEstado.EMPAREJADO.value,
        )
        .first()
        is not None
    )


def _en_espera_de(db: Session, partida_id: uuid.UUID) -> bool:
    
    return (
        db.query(Emparejamiento.id)
        .filter(
            Emparejamiento.partida_id == partida_id,
            Emparejamiento.estado == EmparejamientoEstado.ESPERANDO.value,
        )
        .first()
        is not None
    )


def _resumen_partida(
    partida: Partida, en_duelo: bool = False, en_espera: bool = False
) -> ResumenPartidaResponse:
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
        en_espera=en_espera,
    )


@router.get("/partidas", response_model=list[ResumenPartidaResponse])
def listar_mis_partidas(
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
   
    participaciones = (
        db.query(Participacion)
        .filter(
            Participacion.usuario_id == usuario.id,
            Participacion.rol == "creador",
        )
        .order_by(Participacion.unido_en.desc())
        .all()
    )

    _reciclar_esperas_vencidas(db)

    resultado = []
    for participacion in participaciones:
        partida = participacion.partida
        resultado.append(
            _resumen_partida(
                partida,
                _en_duelo_de(db, partida.id),
                _en_espera_de(db, partida.id),
            )
        )
    return resultado


@router.post("/partidas", response_model=CrearPartidaResponse, status_code=201)
def crear_partida(
    req: CrearPartidaRequest,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    codigo = generar_codigo(db)

    partida = Partida(
        id=uuid.uuid4(),
        codigo=codigo,
        tipo=req.tipo,
        config=req.config or {},
        estado="creando",
        creador_id=usuario.id,
        nombre=req.nombre,  
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
        nombre=partida.nombre, 
    )


@router.get("/partidas/{codigo}", response_model=PartidaPublicaResponse)
def obtener_partida(
    codigo: str,
    db: Session = Depends(get_db),
    usuario: Optional[Usuario] = Depends(get_usuario_opcional),
):
    
    partida = _get_partida_o_404(db, codigo)
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
        nombre=partida.nombre,  
    )


@router.post("/partidas/{codigo}/palabras", response_model=list[PalabraResponse], status_code=201)
def agregar_palabras(
    codigo: str,
    req: AgregarPalabrasRequest,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    
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
   
    partida = _get_partida_o_404(db, codigo)
    _requerir_creador(partida, usuario)

    partida.nombre = req.nombre
    db.commit()
    db.refresh(partida)

    return _resumen_partida(partida, _en_duelo_de(db, partida.id), _en_espera_de(db, partida.id))