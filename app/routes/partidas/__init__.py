from fastapi import APIRouter

from app.routes.partidas import crud, editor, jugador

router = APIRouter()

router.include_router(crud.router)
router.include_router(editor.router)
router.include_router(jugador.router)