"""
Fixtures de backend con PostgreSQL REAL (regla dura del proyecto: sin mocks).

Crea una base `rompecabezas_test` idempotente en el servidor local, crea las
tablas una vez por sesión y limpia (TRUNCATE) entre cada test. Las consultas
de los tests nunca tocan la base de producción `rompecabezas`: el engine del
override vive en la base de test.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.database import Base, get_db
from app.main import app
from app.models.emparejamiento import _migrar_emparejamientos

TEST_DB_NAME = "rompecabezas_test"

# Orden para el TRUNCATE: hijos antes que padres, CASCADE como red de seguridad.
TABLAS_A_LIMPIAR = [
    "hallazgos",
    "participaciones",
    "emparejamientos",  # C-17: referencia partidas y usuarios
    "palabras",
    "partidas",
    "usuarios",
]


def _url_con_base(url: str, nombre: str):
    return make_url(url).set(database=nombre)


@pytest.fixture(scope="session")
def db_engine():
    settings = get_settings()
    url_base = _url_con_base(settings.database_url, "postgres")
    url_test = _url_con_base(settings.database_url, TEST_DB_NAME)

    admin = create_engine(url_base, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        existe = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :nombre"),
            {"nombre": TEST_DB_NAME},
        ).scalar()
        if not existe:
            conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    admin.dispose()

    engine = create_engine(url_test, pool_pre_ping=True)
    Base.metadata.create_all(bind=engine)
    # C-19: create_all no altera tablas existentes; la migración agrega las
    # columnas de conteo del duelo cuando la base ya existía del cambio C-17.
    _migrar_emparejamientos(engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture()
def db_sesion(db_engine):
    SesionLocal = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    db = SesionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def client(db_engine):
    SesionLocal = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)

    def override_get_db():
        db = SesionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    client = TestClient(app)
    yield client
    client.close()

    app.dependency_overrides.clear()

    with SesionLocal() as db:
        db.execute(
            text(
                "TRUNCATE TABLE "
                + ", ".join(TABLAS_A_LIMPIAR)
                + " RESTART IDENTITY CASCADE"
            )
        )
        db.commit()