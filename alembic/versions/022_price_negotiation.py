"""add route price negotiation system and update default commission to 5%

Revision ID: 022_price_negotiation
Revises: 021_batch_number
Create Date: 2026-08-05

Implementa el sistema de negociación de precios por ruta:
  - route_price_offers: historial de ofertas y contraofertas
  - route_headers: negotiation_status + suggested_price
  - Comisión Muevo estándar cambia de 12% a 5%
"""

from alembic import op
import sqlalchemy as sa

revision      = '022_price_negotiation'
down_revision = '021_batch_number'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── Enums ──────────────────────────────────────────────────────────────────
    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE negotiationstatus AS ENUM ('none', 'suggested', 'countered', 'accepted');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """))
    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE offersource AS ENUM ('warehouse', 'transport');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """))
    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE offerstatus AS ENUM ('pending', 'accepted', 'rejected', 'superseded');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """))

    # ── route_price_offers ────────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS route_price_offers (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            route_header_id    UUID NOT NULL REFERENCES route_headers(id) ON DELETE CASCADE,
            offered_by         offersource NOT NULL,
            offered_by_user_id UUID REFERENCES users(id),
            amount             NUMERIC(10,2) NOT NULL,
            note               TEXT,
            status             offerstatus NOT NULL DEFAULT 'pending',
            created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_rpo_route_header_id ON route_price_offers(route_header_id)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_rpo_status ON route_price_offers(status)"
    ))

    # ── route_headers: nuevos campos ──────────────────────────────────────────
    conn.execute(sa.text("""
        ALTER TABLE route_headers
            ADD COLUMN IF NOT EXISTS negotiation_status negotiationstatus NOT NULL DEFAULT 'none',
            ADD COLUMN IF NOT EXISTS suggested_price NUMERIC(10,2)
    """))

    # ── Comisión Muevo estándar: 12% → 5% ─────────────────────────────────────
    conn.execute(sa.text(
        "ALTER TABLE service_types ALTER COLUMN porcentaje_muevo SET DEFAULT 5.00"
    ))
    # Actualizar los tipos de servicio existentes que todavía tienen el 12% por defecto
    conn.execute(sa.text(
        "UPDATE service_types SET porcentaje_muevo = 5.00 WHERE porcentaje_muevo = 12.00"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE route_headers DROP COLUMN IF EXISTS negotiation_status"))
    conn.execute(sa.text("ALTER TABLE route_headers DROP COLUMN IF EXISTS suggested_price"))
    conn.execute(sa.text("DROP TABLE IF EXISTS route_price_offers CASCADE"))
    conn.execute(sa.text("ALTER TABLE service_types ALTER COLUMN porcentaje_muevo SET DEFAULT 12.00"))
