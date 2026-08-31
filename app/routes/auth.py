from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
import uuid

from app.database import get_db
from app.models.usuario import Usuario
from app.schemas.usuario import UsuarioCreate, LoginRequest, UsuarioResponse
from app.auth import (
    hash_password,
    verificar_password,
    setear_cookie_sesion,
    borrar_cookie_sesion,
    get_usuario_actual,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/registro", response_model=UsuarioResponse, status_code=201)
def registro(req: UsuarioCreate, response: Response, db: Session = Depends(get_db)):
    existente = db.query(Usuario).filter(Usuario.username == req.username).first()
    if existente:
        raise HTTPException(status_code=409, detail="Ese username ya está en uso")

    usuario = Usuario(
        id=uuid.uuid4(),
        username=req.username,
        password_hash=hash_password(req.password),
    )
    db.add(usuario)
    db.commit()
    db.refresh(usuario)

    setear_cookie_sesion(response, usuario.id)
    return usuario


@router.post("/login", response_model=UsuarioResponse)
def login(req: LoginRequest, response: Response, db: Session = Depends(get_db)):
    usuario = db.query(Usuario).filter(Usuario.username == req.username).first()
    # Mensaje genérico a propósito: no revelar si el error fue el username o el password
    if not usuario or not verificar_password(req.password, usuario.password_hash):
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos")

    setear_cookie_sesion(response, usuario.id)
    return usuario


@router.post("/logout", status_code=204)
def logout(response: Response):
    borrar_cookie_sesion(response)


@router.get("/me", response_model=UsuarioResponse)
def me(usuario: Usuario = Depends(get_usuario_actual)):
    return usuario
