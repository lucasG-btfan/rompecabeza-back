from typing import Optional
import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.partida import Partida
from app.models.palabra import Palabra
from app.models.usuario import Usuario
from app.schemas.partida import (
    EstadoPalabraResponse,
    EstadoPartidaResponse,
)
from app.services.texto import limpiar_para_grilla, texto_a_mostrar
from app.services.crucigrama_generator import DELTAS


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


def _es_creador(partida: Partida, usuario: Usuario) -> bool:
    """Devuelve True si el usuario es el creador de la partida."""
    return partida.creador_id == usuario.id


def _requerir_creador(partida: Partida, usuario: Usuario) -> None:
    if partida.creador_id != usuario.id:
        raise HTTPException(
            status_code=403,
            detail="Solo el usuario que creó la partida puede hacer esto",
        )


def _validar_y_normalizar(
    db: Session,
    partida: Partida,
    texto: str,
    excluir_id: Optional[uuid.UUID] = None,
) -> tuple[str, str]:
    """Limpia el texto para la grilla, arma la versión presentable y rechaza
    duplicados (misma versión limpia dentro de la misma partida)."""
    limpia = limpiar_para_grilla(texto)
    if not limpia:
        raise HTTPException(
            status_code=422,
            detail="La palabra debe contener al menos una letra",
        )
    q = db.query(Palabra).filter(
        Palabra.partida_id == partida.id,
        Palabra.palabra == limpia,
    )
    if excluir_id is not None:
        q = q.filter(Palabra.id != excluir_id)
    duplicada = q.first()
    if duplicada:
        raise HTTPException(
            status_code=400,
            detail=f"Ya existe '{duplicada.texto_mostrar or duplicada.palabra}' en esta partida",
        )
    return limpia, texto_a_mostrar(texto)


def _estado_response(db: Session, partida: Partida) -> EstadoPartidaResponse:
    es_crucigrama = partida.tipo == "crucigrama"
    palabras_estado = []
    for p in partida.palabras:
        numero = None
        if es_crucigrama and p.posicion:
            numero = p.posicion.get("numero")  
        palabras_estado.append(
            EstadoPalabraResponse(
                id=p.id,
                palabra=None if es_crucigrama else p.palabra,
                texto_mostrar=None if es_crucigrama else p.texto_mostrar,
                numero=numero,
                encontrada=False,
                posicion=None,
            )
        )

    grilla = partida.grilla
    if es_crucigrama and grilla:
        grilla = _sanitizar_grilla_crucigrama(grilla, {})

    return EstadoPartidaResponse(
        codigo=partida.codigo,
        tipo=partida.tipo,
        estado=partida.estado,
        grilla=grilla,
        palabras=palabras_estado,
    )


def _sanitizar_grilla_crucigrama(
    grilla: dict,
    posiciones_por_palabra: dict[uuid.UUID, dict],
) -> dict:
    
    encontradas_por_inicio = {
        (pos["fila"], pos["columna"], pos["orientacion"]): pos.get("numero")
        for pos in posiciones_por_palabra.values()
        if pos
    }

    palabras_grilla = grilla["palabras"]
    filas = max(
        w["posicion"]["fila"] + (w["longitud"] if w["orientacion"] == "V" else 1)
        for w in palabras_grilla
    )
    columnas = max(
        w["posicion"]["columna"] + (w["longitud"] if w["orientacion"] == "H" else 1)
        for w in palabras_grilla
    )

    reveladas: set[int] = set()
    for w in palabras_grilla:
        clave = (w["posicion"]["fila"], w["posicion"]["columna"], w["orientacion"])
        if clave not in encontradas_por_inicio:
            continue
        dr, dc = DELTAS[w["orientacion"]]
        for i in range(w["longitud"]):
            f = w["posicion"]["fila"] + dr * i
            c = w["posicion"]["columna"] + dc * i
            reveladas.add(f * columnas + c)

    celdas = [
        {**celda, "letra": celda["letra"] if indice in reveladas else None}
        for indice, celda in enumerate(grilla["celdas"])
    ]
    palabras = [dict(w, texto=None) for w in palabras_grilla]

    return {"celdas": celdas, "palabras": palabras}