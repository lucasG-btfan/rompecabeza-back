import argparse
import uuid

from sqlalchemy import inspect

from app.database import Base, engine, SessionLocal
from app.models import Partida, Palabra, Usuario, Participacion  # noqa: F401  (registra los modelos en Base)

TABLAS_ESPERADAS = {"partidas", "palabras", "usuarios", "participaciones"}
COLUMNAS_ESPERADAS = {
    "partidas": {
        "id", "codigo", "tipo", "creador_id", "creado_en", "estado", "config", "grilla",
    },
    "palabras": {
        "id", "partida_id", "palabra", "explicacion", "posicion", "encontrada",
        "encontrada_por", "encontrada_en",
    },
    "usuarios": {
        "id", "username", "password_hash", "creado_en",
    },
    "participaciones": {
        "id", "partida_id", "usuario_id", "rol", "palabras_encontradas",
        "unido_en", "iniciado_en", "finalizado_en",
    },
}


def verificar_tablas() -> bool:
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    tablas_existentes = set(inspector.get_table_names())
    print(f"Tablas en la base: {sorted(tablas_existentes)}")

    todo_ok = True

    faltantes = TABLAS_ESPERADAS - tablas_existentes
    if faltantes:
        print(f"⚠️  Faltan tablas: {faltantes}")
        todo_ok = False
    else:
        print("✅ Todas las tablas esperadas existen.")

    for tabla, columnas_esperadas in COLUMNAS_ESPERADAS.items():
        if tabla not in tablas_existentes:
            continue
        columnas_reales = {c["name"] for c in inspector.get_columns(tabla)}
        faltantes_cols = columnas_esperadas - columnas_reales
        if faltantes_cols:
            print(f"⚠️  A '{tabla}' le faltan columnas: {faltantes_cols}")
            if tabla == "partidas" and "grilla" in faltantes_cols:
                print("    Corré esto en psql/pgAdmin4:")
                print("    ALTER TABLE partidas ADD COLUMN IF NOT EXISTS grilla JSON;")
            todo_ok = False
        else:
            print(f"✅ '{tabla}' tiene todas las columnas esperadas.")

    return todo_ok


def crear_partida_de_prueba():
    from app.auth import hash_password

    db = SessionLocal()
    try:
        # Usuario demo (creador de la partida de prueba)
        usuario = db.query(Usuario).filter(Usuario.username == "demo").first()
        if not usuario:
            usuario = Usuario(
                id=uuid.uuid4(),
                username="demo",
                password_hash=hash_password("demo12345"),
            )
            db.add(usuario)
            db.flush()
            print("✅ Usuario demo creado (username='demo', password='demo12345').")
        else:
            print("El usuario 'demo' ya existe, no se duplica.")

        codigo = "SEED01"
        existente = db.query(Partida).filter(Partida.codigo == codigo).first()
        if existente:
            print(f"La partida de prueba '{codigo}' ya existe, no se duplica.")
            return

        partida = Partida(
            id=uuid.uuid4(),
            codigo=codigo,
            tipo="sopa",
            config={},
            estado="creando",
            creador_id=usuario.id,
        )
        db.add(partida)
        db.flush()

        for texto in ["PYTHON", "REACT", "FLASK", "VUE", "SASS"]:
            db.add(Palabra(id=uuid.uuid4(), partida_id=partida.id, palabra=texto))

        db.add(Participacion(id=uuid.uuid4(), partida_id=partida.id, usuario_id=usuario.id, rol="creador"))

        db.commit()
        print(f"✅ Partida de prueba creada con código '{codigo}' (5 palabras, estado 'creando').")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verifica y opcionalmente siembra la base de datos.")
    parser.add_argument(
        "--con-datos", action="store_true",
        help="Crea una partida de sopa de ejemplo (código SEED01) para probar los endpoints",
    )
    args = parser.parse_args()

    ok = verificar_tablas()

    if args.con_datos:
        crear_partida_de_prueba()

    if not ok:
        raise SystemExit(1)
