from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import engine, Base
from app.routes import partidas, auth

from app.models import Partida, Palabra, Usuario, Participacion  

settings = get_settings()

app = FastAPI(
    title="Rompecabezas API",
    description="Backend for Word Search & Crossword games",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(partidas.router, prefix="/api")


@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)


@app.get("/")
def root():
    return {"message": "Rompecabezas API - Running"}


@app.get("/health")
def health():
    return {"status": "ok"}
