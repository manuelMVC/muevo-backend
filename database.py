"""
database.py — Muevo B2B
========================
Conexión a PostgreSQL con SQLAlchemy (modo síncrono).
Gestión de sesiones y utilidades de base de datos.

Configuración en .env (opcional):
  DATABASE_URL=postgresql+psycopg2://postgres:password@localhost:5432/muevodb
"""

import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

# ─── Configuración ──────────────────────────────────────────────────────────

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:Semeolvido.01@localhost:5432/muevodb"
)

# Motor de conexión con pool
engine = create_engine(
    DATABASE_URL,
    echo=False,           # True en desarrollo para ver queries SQL
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,    # verifica conexión antes de usar
    pool_recycle=3600,     # recicla conexiones cada hora
)

# Factory de sesiones
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)

# ─── Dependency para FastAPI ─────────────────────────────────────────────────

def get_db() -> Generator[Session, None, None]:
    """
    Dependency de FastAPI para inyectar la sesión de base de datos.

    Uso:
        @app.get("/routes")
        async def list_routes(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

# ─── Context manager para scripts ────────────────────────────────────────────

@contextmanager
def get_db_context() -> Generator[Session, None, None]:
    """
    Context manager para usar en scripts independientes (seed, migraciones, etc.)

    Uso:
        with get_db_context() as db:
            result = db.execute(select(Route))
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

# ─── Crear / borrar tablas (solo para testing — usar Alembic en producción) ──

def create_tables():
    """Crea todas las tablas definidas en models.py. Usar Alembic en producción."""
    from models import Base
    Base.metadata.create_all(bind=engine)

def drop_tables():
    """Borra todas las tablas. Solo para testing."""
    from models import Base
    Base.metadata.drop_all(bind=engine)

def check_connection() -> bool:
    """Verifica que la conexión a la base de datos funciona."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        print(f"DB connection error: {e}")
        return False
