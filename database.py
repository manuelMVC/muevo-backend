"""
database.py — Muevo B2B
========================
Conexión a PostgreSQL con SQLAlchemy async.
Gestión de sesiones y utilidades de base de datos.

Configuración en .env:
  DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/muevodb
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
import os

# ─── Configuración ────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:password@localhost:5432/muevodb"
)

# Motor async con pool de conexiones
engine = create_async_engine(
    DATABASE_URL,
    echo=False,           # True en desarrollo para ver queries
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,   # verifica conexión antes de usar
    pool_recycle=3600,    # recicla conexiones cada hora
)

# Factory de sesiones async
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# ─── Dependency para FastAPI ──────────────────────────────────────────────────

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency de FastAPI para inyectar la sesión de base de datos.

    Uso:
        @router.get("/routes")
        async def list_routes(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

# ─── Context manager para scripts ────────────────────────────────────────────

@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession, None]:
    """
    Context manager para usar en scripts y tareas Celery.

    Uso:
        async with get_db_context() as db:
            result = await db.execute(select(Route))
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

# ─── Crear / borrar tablas (para testing) ────────────────────────────────────

async def create_tables():
    """Crea todas las tablas. Usar Alembic en producción."""
    from models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def drop_tables():
    """Borra todas las tablas. Solo para testing."""
    from models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

async def check_connection() -> bool:
    """Verifica que la conexión a la base de datos funciona."""
    try:
        async with engine.connect() as conn:
            from sqlalchemy import text
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        print(f"DB connection error: {e}")
        return False
