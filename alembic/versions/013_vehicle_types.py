"""add vehicle_types table

Revision ID: 013_vehicle_types
Revises: 012_service_types
Create Date: 2026-07-01

Crea la tabla vehicle_types con campos de capacidad y configuración,
agrega FK vehicle_type_code en vehicles, y siembra los 4 tipos base.
"""

from alembic import op
import sqlalchemy as sa

revision      = '013_vehicle_types'
down_revision = '012_service_types'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Crear tabla vehicle_types ─────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS vehicle_types (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code            VARCHAR(60)   NOT NULL UNIQUE,
            name            VARCHAR(120)  NOT NULL,
            description     TEXT,
            peso_max_lbs    NUMERIC(10,2) NOT NULL DEFAULT 0,
            volumen_max_ft3 NUMERIC(10,2) NOT NULL DEFAULT 0,
            habilitado      BOOLEAN       NOT NULL DEFAULT TRUE,
            maneja_unidades BOOLEAN       NOT NULL DEFAULT FALSE,
            unidades_max    INTEGER,
            created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_vehicle_types_code      ON vehicle_types(code)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_vehicle_types_habilitado ON vehicle_types(habilitado)"
    ))

    # ── 2. Sembrar los 4 tipos base ──────────────────────────────────────────
    conn.execute(sa.text("""
        INSERT INTO vehicle_types
            (code, name, description, peso_max_lbs, volumen_max_ft3,
             habilitado, maneja_unidades, unidades_max)
        VALUES
            ('moto',      'Moto',
             'Motocicleta para entregas rápidas de documentos y paquetes pequeños.',
             80, 10, TRUE, TRUE, 5),
            ('sedan',     'Sedán',
             'Automóvil para mensajería ejecutiva y transporte de personas.',
             600, 60, TRUE, TRUE, 4),
            ('furgoneta', 'Furgoneta',
             'Furgoneta de carga media, ideal para mensajería y logística urbana.',
             1800, 200, TRUE, TRUE, 20),
            ('furgon',    'Furgón de carga',
             'Furgón grande para logística pesada y distribución de almacén.',
             4400, 500, TRUE, TRUE, 50)
        ON CONFLICT (code) DO NOTHING
    """))

    # ── 3. Agregar FK vehicle_type_code en vehicles ──────────────────────────
    conn.execute(sa.text("""
        ALTER TABLE vehicles
            ADD COLUMN IF NOT EXISTS vehicle_type_code VARCHAR(60)
            REFERENCES vehicle_types(code)
    """))

    # ── 4. Poblar vehicle_type_code desde vehicle_type enum existente ─────────
    conn.execute(sa.text("""
        UPDATE vehicles
        SET vehicle_type_code = vehicle_type::text
        WHERE vehicle_type_code IS NULL
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "ALTER TABLE vehicles DROP COLUMN IF EXISTS vehicle_type_code"
    ))
    conn.execute(sa.text("DROP TABLE IF EXISTS vehicle_types CASCADE"))
