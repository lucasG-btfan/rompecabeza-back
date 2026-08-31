"""
Autenticación por cookie de sesión firmada.

No usamos JWT en localStorage a propósito: la cookie es httponly, así que
JavaScript en el navegador ni siquiera puede leerla (mitiga XSS robando la
sesión). itsdangerous firma el contenido con SECRET_KEY, así que el cliente
no puede fabricar ni alterar el valor sin conocer la clave del servidor.

Invitados: si no hay cookie, get_usuario_opcional devuelve None y las rutas
que lo permiten (jugar, marcar palabra encontrada) simplemente no atribuyen
puntaje a nadie. No hay "modo invitado" en el backend más que eso.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models.usuario import Usuario

settings = get_settings()

COOKIE_NAME = "rompecabezas_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 7  # 7 días

_serializer = URLSafeTimedSerializer(settings.secret_key, salt="rompecabezas-sesion")

# Passwords

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verificar_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


# Cookie de sesión

def crear_token_sesion(usuario_id: uuid.UUID) -> str:
    return _serializer.dumps(str(usuario_id))


def leer_token_sesion(token: str) -> Optional[uuid.UUID]:
    try:
        raw = _serializer.loads(token, max_age=COOKIE_MAX_AGE)
        return uuid.UUID(raw)
    except (BadSignature, SignatureExpired, ValueError):
        return None


def setear_cookie_sesion(response: Response, usuario_id: uuid.UUID) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=crear_token_sesion(usuario_id),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )


def borrar_cookie_sesion(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME)


# Dependencias FastAPI


def get_usuario_opcional(request: Request, db: Session = Depends(get_db)) -> Optional[Usuario]:
    """None si no hay cookie válida. Usar en rutas que permiten invitados."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None

    usuario_id = leer_token_sesion(token)
    if not usuario_id:
        return None

    return db.query(Usuario).filter(Usuario.id == usuario_id).first()


def get_usuario_actual(usuario: Optional[Usuario] = Depends(get_usuario_opcional)) -> Usuario:
    """401 si no hay sesión. Usar en rutas que requieren estar logueado."""
    if usuario is None:
        raise HTTPException(status_code=401, detail="No autenticado. Iniciá sesión primero.")
    return usuario
